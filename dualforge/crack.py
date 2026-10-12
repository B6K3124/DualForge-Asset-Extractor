"""Automated AES-key crack for an Unreal game.

Wires together the existing key hunting toolchain:

1. Auto-detect the game's main executable from a pak/install folder.
2. Provision Ghidra + JRE (download on demand when allowed).
3. Run the static Ghidra key hunt against the binary.
4. Validate every candidate key against a real pak from the game, probing
   every known scheme pipeline (plain AES, then per-game presets) so wrapped
   schemes like ``aes-256 + xor8`` verify too.
5. Persist the winning key(s) to the DualForge key store.

The orchestration is dependency-light: it shells out to the existing
``scripts/ghidra/ghidra_key_finder.py`` hunt (reusing all of its Ghidra
scanning complexity) and re-validates candidates locally.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dualforge.encryption.brute import (
    probe_pak_blocks,
    probe_pak_blocks_file,
    probe_unity_blocks_file,
    validate_key,
)
from dualforge.encryption.presets import PRESETS, GameScheme, guess_scheme
from dualforge.encryption.registry import list_schemes
from dualforge.unreal.autodetect import find_game_executable, find_install_root
from dualforge.unreal.keys import KeyStore

# Pak names that are usually media, not index-encrypted content (accepted only
# as a last resort so auto-cracks validate against a real data archive).
_MEDIA_PAK_FRAGMENTS = ("video", "movie", "cinematic", "trailer")


def find_validation_pak(pak_or_folder: str) -> str:
    """Return a pak to validate candidate keys against."""
    candidate = Path(pak_or_folder).resolve()
    if candidate.is_file():
        return str(candidate)
    root = find_install_root(pak_or_folder)
    best: tuple[str, int] | None = None
    fallback: tuple[str, int] | None = None
    for pak in root.rglob("*.pak"):
        # skip tiny patch paks; prefer a full archive but any is fine
        try:
            size = pak.stat().st_size
        except OSError:
            size = 0
        is_media = any(f in pak.name.lower() for f in _MEDIA_PAK_FRAGMENTS)
        slot = fallback if is_media else best
        if slot is None or size > slot[1]:
            entry = (str(pak), size)
            if is_media:
                fallback = entry
            else:
                best = entry
    if best is None:
        best = fallback
    if best is None:
        raise RuntimeError(f"no .pak found under {root}")
    return best[0]


def run_ghidra_hunt(
    binary: str,
    ghidra_home: Path,
    startup_timeout: int = 300,
    ascii_keys: bool = False,
) -> tuple[int | None, str, str]:
    """Run the static Ghidra hunt, returning (returncode, stdout, json_path)."""
    from dualforge.ghidra.manager import hunter_script_path

    script = hunter_script_path()
    if script is None:
        raise RuntimeError("ghidra_key_finder.py was not found (source or packed build)")
    tmpdir = Path(tempfile.mkdtemp(prefix="dualforge_hunt_"))
    json_path = tmpdir / "candidates.json"
    cmd = [
        sys.executable,
        str(script),
        binary,
        "--ghidra-home",
        str(ghidra_home),
        "--json",
        str(json_path),
        "--no-add-keystore",
        "--startup-timeout",
        str(startup_timeout),
    ]
    if ascii_keys:
        cmd += ["--ascii-keys"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=startup_timeout + 1200, creationflags=flags)
    return completed.returncode, completed.stdout + completed.stderr, str(json_path)


def _cleanup_hunt_json(json_path: str | None) -> None:
    """Remove the hunt temp dir (parent of the JSON result) if it is ours."""
    if not json_path:
        return
    tmpdir = Path(json_path).parent
    if tmpdir.name.startswith("dualforge_hunt_"):
        shutil.rmtree(tmpdir, ignore_errors=True)


def _accepted_candidate_value(value: str, allow_ascii: bool) -> bool:
    """A candidate worth validating: 64-hex AES, or (optionally) 16-char ASCII."""
    if len(value) == 64 and all(c in "0123456789abcdef" for c in value):
        return True
    return bool(allow_ascii and len(value) == 16 and value.isprintable())


def extract_candidate_keys(
    json_path: str | None, limit: int = 64, allow_ascii: bool = False
) -> list[str]:
    """Flatten the key candidates from a hunt JSON result.

    Accepts 64-hex AES candidates and, when ``allow_ascii`` is set, 16-char
    printable-ASCII candidates (Unity CN masks, case preserved). Optimistic
    binary blob values are handled by the Unity runtime pass, not the static
    hunt.
    """
    keys: list[str] = []
    if not json_path or not Path(json_path).is_file():
        return keys
    try:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return keys
    for match in payload.get("matches", []):
        for candidate in match.get("candidates", []):
            raw = (candidate.get("hex") or "").strip()
            hexval = raw.lower().replace("0x", "")
            value = raw if candidate.get("source") == "ascii" else hexval
            if _accepted_candidate_value(value, allow_ascii) and value not in keys:
                keys.append(value)
        if len(keys) >= limit:
            break
    return keys


@dataclass
class VerifiedKey:
    """A candidate key that successfully decrypted a real pak block."""

    key: str = ""
    scheme: str = "aes-256"
    parameters: dict[str, str] = field(default_factory=dict)
    game: str = ""


# Stage names that cannot confirm a key on their own (they defer to the reader)
# and are therefore never probed during scheme-aware validation.
_NON_KEYED_STAGES = {"partial-encrypt"}


def pak_probe_schemes(pak_name: str = "", game: str = "") -> list[tuple[str, str, dict]]:
    """Ordered ``(preset_name, pipeline_scheme, default_params)`` probes.

    ``pipeline_scheme`` is the registry-resolvable stage string (e.g.
    ``aes-256+xor8``) passed to ``validate_key``; ``preset_name`` is the label
    stored on the key (matches the CUE4Parse game mapping). The per-game preset
    goes first (most specific diagnosis), then the rest of the preset table with
    duplicates collapsed by stage signature - Delta Force and Marvel Rivals
    (both ``aes-256 + xor8``) become a single probe. Any self-registered scheme
    names that no preset covers are appended afterwards.
    """
    guessed = guess_scheme(archive_name=pak_name, game=game)
    ordered: list[tuple[str, str, tuple[str, ...], dict]] = []
    seen: set[tuple[str, ...]] = set()

    def push(preset: GameScheme) -> None:
        stages = tuple(s for s in preset.stages if s not in _NON_KEYED_STAGES)
        if not stages or stages in seen:
            return
        seen.add(stages)
        ordered.append((preset.name, "+".join(stages), stages, dict(preset.default_params)))

    if guessed:
        push(guessed)
    for preset in PRESETS:
        push(preset)

    covered: set[str] = set().union(*seen)
    probes = [(name, pipeline, params) for name, pipeline, _stages, params in ordered]
    probe_names = {name for name, _, _ in probes}
    for name in list_schemes():
        if name not in probe_names and name not in covered and name not in _NON_KEYED_STAGES:
            probes.append((name, name, {}))
    return probes


def validate_keys_against_pak(
    pak_path: str,
    keys: list[str],
    block_count: int = 16,
    scheme: str | None = None,
    game: str = "",
) -> list[VerifiedKey]:
    """Return the subset of ``keys`` that decrypt a pak index block.

    Each unique candidate is tried against every known scheme pipeline (per-game
    preset first, then all others, then any self-registered schemes); the first
    pipeline that decrypts a probed block to the Unreal magic wins and is
    reported with the key. Pass an explicit ``scheme`` to restrict probing to a
    single pipeline (e.g. for the interactive ``keys test`` diagnostics).
    """
    verified: list[VerifiedKey] = []
    with open(pak_path, "rb") as fh:
        # read enough of the file (tail) to locate index blocks
        size = fh.seek(0, 2)
        tail_window = min(size, 16 * 1024 * 1024)
        fh.seek(size - tail_window)
        raw = fh.read(tail_window)
    blocks = probe_pak_blocks(raw, count=block_count)
    if not blocks:
        # The tail window can miss an ABI index that lives far from EOF; probe
        # the archive on disk by seeking to the decoded index offset directly.
        blocks = probe_pak_blocks_file(pak_path, count=block_count)
    if not blocks:
        return verified
    pak_name = Path(pak_path).name
    if scheme:
        probes: list[tuple[str, str, dict]] = [(scheme, scheme, {})]
    else:
        probes = pak_probe_schemes(pak_name=pak_name, game=game)
    for key in dict.fromkeys(keys):
        for probe_name, pipeline, params in probes:
            if any(
                validate_key(
                    block, pipeline, key, archive_name=pak_name, parameters=params
                )
                for block in blocks
            ):
                verified.append(
                    VerifiedKey(key=key, scheme=probe_name, parameters=params, game=game)
                )
                break
    return verified


# Unity asset files that can carry a CN-Pro XOR header (bundle containers +
# serialized files). Encrypted copies have no magic bytes, so match by name too.
_UNITY_VALIDATION_GLOBS = ("*.unity3d", "*.bundle", "*.u3d", "*.assetbundle", "*.ab", "*.assets")
_UNITY_SERIALIZED_NAMES = ("globalgamemanagers", "globalgamemanagers.assets", "level0", "maindata")


def find_validation_bundle(path: str) -> str:
    """Return a Unity bundle/serialized file to validate candidate keys against."""
    candidate = Path(path).resolve()
    if candidate.is_file():
        return str(candidate)
    root = find_install_root(path)
    best: tuple[str, int] | None = None
    for pattern in _UNITY_VALIDATION_GLOBS:
        try:
            matches = list(root.rglob(pattern))
        except OSError:
            continue
        for asset in matches:
            try:
                size = asset.stat().st_size
            except OSError:
                size = 0
            entry = (str(asset), size)
            if best is None or size > best[1]:
                best = entry
    for name in _UNITY_SERIALIZED_NAMES:
        probe = root / name
        if probe.is_file():
            try:
                size = probe.stat().st_size
            except OSError:
                size = 0
            entry = (str(probe), size)
            if best is None or size > best[1]:
                best = entry
    if best is None:
        raise RuntimeError(f"no Unity bundle/assets found under {root}")
    return best[0]


def unity_probe_schemes() -> list[tuple[str, str, dict]]:
    """Validation pipelines for Unity bundles: the CN-Pro XOR mask."""
    return [("unity-cn", "unity-cn", {"header_bytes": "0"})]


def validate_keys_against_unity(
    bundle_path: str,
    keys: list[str],
    scheme: str | None = None,
    count: int = 4,
) -> list[VerifiedKey]:
    """Return the subset of ``keys`` that decrypt a Unity bundle header.

    Unity CN Pro bundles XOR their header with a fixed 16-char ASCII mask.
    Each candidate is applied with the ``unity-cn`` pipeline and the decrypted
    header is checked for a ``Unity*`` signature or a serialized-file header.
    """
    blocks = probe_unity_blocks_file(bundle_path, count=count)
    if not blocks:
        return []
    if scheme:
        probes: list[tuple[str, str, dict]] = [(scheme, scheme, {})]
    else:
        probes = unity_probe_schemes()
    verified: list[VerifiedKey] = []
    for key in dict.fromkeys(keys):
        for probe_name, pipeline, params in probes:
            if any(
                validate_key(
                    block, pipeline, key, archive_name=Path(bundle_path).name, parameters=params
                )
                for block in blocks
            ):
                verified.append(
                    VerifiedKey(key=key, scheme=probe_name, parameters=params, game="")
                )
                break
    return verified


def crack_unity(
    bundle_or_folder: str,
    download: bool = True,
    startup_timeout: int = 300,
    ghidra_home: str | None = None,
    save_keys: bool = True,
    title: str | None = None,
) -> dict:
    """Run the full auto-crack pipeline for a Unity game and return a result dict.

    Autodetects the game executable, hunts it with Ghidra (harvesting 16-char
    ASCII masks as well as 64-hex AES candidates) and validates candidates
    against a Unity bundle with the ``unity-cn`` scheme; verified masks are
    saved to the key store, engine ``unity``.
    """
    from dualforge.ghidra.manager import ensure_ghidra, ensure_java

    exe, _ranked = find_game_executable(bundle_or_folder)
    if not exe:
        raise RuntimeError("could not auto-detect the game executable under the given path")
    bundle = find_validation_bundle(bundle_or_folder)

    headless = ensure_ghidra(download=download, ghidra_home=ghidra_home)
    ensure_java(download=download)
    ghidra_root = headless.parents[1]

    returncode, output, json_path = run_ghidra_hunt(exe, ghidra_root or headless, startup_timeout, ascii_keys=True)
    if returncode != 0:
        _cleanup_hunt_json(json_path)
        return {
            "status": "hunt_failed",
            "engine": "unity",
            "exe": exe,
            "bundle": bundle,
            "returncode": returncode,
            "detail": output[-2000:],
        }

    try:
        candidates = extract_candidate_keys(json_path, allow_ascii=True)
    finally:
        _cleanup_hunt_json(json_path)
    verified_keys = validate_keys_against_unity(bundle, candidates)
    verified = [vk.key for vk in verified_keys]

    saved: list[str] = []
    if save_keys and verified_keys:
        store = KeyStore()
        base_title = title or Path(exe).stem
        for index, vk in enumerate(verified_keys, start=1):
            entry_title = f"{base_title} [cracked-{index}]"
            try:
                store.add(
                    entry_title,
                    vk.key,
                    engine="unity",
                    notes="auto-cracked via Ghidra (Unity CN)",
                    scheme=vk.scheme,
                    parameters=vk.parameters or None,
                )
                saved.append(entry_title)
            except ValueError:
                continue

    return {
        "status": "ok" if verified else "no_valid_key",
        "engine": "unity",
        "exe": exe,
        "bundle": bundle,
        "candidates": candidates,
        "verified": verified,
        "verified_keys": verified_keys,
        "saved": saved,
    }


def crack(
    pak_or_folder: str,
    download: bool = True,
    startup_timeout: int = 300,
    ghidra_home: str | None = None,
    save_keys: bool = True,
    title: str | None = None,
    block_count: int = 16,
) -> dict:
    """Run the full auto-crack pipeline and return a result dict."""
    from dualforge.ghidra.manager import ensure_ghidra, ensure_java

    # 1. Auto-find the game executable.
    exe, _ranked = find_game_executable(pak_or_folder)
    if not exe:
        raise RuntimeError("could not auto-detect the game executable under the given path")

    # 2. Pick a pak to validate against.
    pak = find_validation_pak(pak_or_folder)

    # 3. Provision Ghidra + JRE. ensure_ghidra returns a usable analyzeHeadless
    #    or raises; ensure_java returns a java path or None when downloading is
    #    disabled (the hunt script then reports the missing-Java condition).
    headless = ensure_ghidra(download=download, ghidra_home=ghidra_home)
    ensure_java(download=download)

    ghidra_root = headless.parents[1]

    # 4. Run the static hunt.
    returncode, output, json_path = run_ghidra_hunt(exe, ghidra_root or headless, startup_timeout)
    if returncode != 0:
        _cleanup_hunt_json(json_path)
        return {
            "status": "hunt_failed",
            "exe": exe,
            "pak": pak,
            "returncode": returncode,
            "detail": output[-2000:],
        }

    # 5. Validate candidates against the real pak.
    try:
        candidates = extract_candidate_keys(json_path)
    finally:
        _cleanup_hunt_json(json_path)
    verified_keys = validate_keys_against_pak(
        pak, candidates, block_count=block_count, game=Path(exe).stem
    )
    verified = [vk.key for vk in verified_keys]

    saved: list[str] = []
    if save_keys and verified_keys:
        store = KeyStore()
        base_title = title or Path(exe).stem
        for index, vk in enumerate(verified_keys, start=1):
            entry_title = f"{base_title} [cracked-{index}]"
            try:
                store.add(
                    entry_title,
                    vk.key,
                    engine="unreal",
                    notes="auto-cracked via Ghidra",
                    scheme=vk.scheme,
                    parameters=vk.parameters or None,
                )
                saved.append(entry_title)
            except ValueError:
                continue

    return {
        "status": "ok" if verified else "no_valid_key",
        "exe": exe,
        "pak": pak,
        "candidates": candidates,
        "verified": verified,
        "verified_keys": verified_keys,
        "saved": saved,
    }


def crack_all(
    pak_or_folder: str,
    download: bool = True,
    startup_timeout: int = 300,
    ghidra_home: str | None = None,
    save_keys: bool = True,
    title: str | None = None,
    block_count: int = 16,
    log: Callable[[str], None] | None = None,
) -> dict:
    """Scan every detected binary under the install root and return verified keys.

    This runs the Ghidra key hunt against each ranked candidate executable,
    deduplicates all 32-byte hex candidates, then validates them against a
    real pak in one pass.  ``log`` is an optional callback for progress
    messages.
    """
    from dualforge.ghidra.manager import ensure_ghidra, ensure_java

    def _emit(msg: str) -> None:
        if log:
            log(msg)

    # 1. Discover all ranked executables.
    _best, ranked = find_game_executable(pak_or_folder)
    if not ranked:
        raise RuntimeError("could not auto-detect any game executables under the given path")
    ranked = [(path, score) for path, score in ranked if score >= 0]
    if not ranked:
        raise RuntimeError("could not auto-detect any game executables under the given path")
    _emit(f"found {len(ranked)} candidate executable(s)")

    # 2. Pick a pak to validate against.
    pak = find_validation_pak(pak_or_folder)
    _emit(f"validation pak: {Path(pak).name}")

    # 3. Provision Ghidra + JRE.
    headless = ensure_ghidra(download=download, ghidra_home=ghidra_home)
    ensure_java(download=download)
    ghidra_root = headless.parents[1]

    # 4. Scan every candidate, collecting and deduplicating candidates.
    all_candidates: list[str] = []
    hunt_results: list[dict] = []
    for rank_index, (exe_path, score) in enumerate(ranked, start=1):
        _emit(f"[{rank_index}/{len(ranked)}] scanning {Path(exe_path).name} (score {score:.0f})...")
        returncode, output, json_path = run_ghidra_hunt(
            exe_path, ghidra_root or headless, startup_timeout,
        )
        if returncode != 0:
            _emit(f"  hunt failed (exit {returncode}), skipping")
            _cleanup_hunt_json(json_path)
            hunt_results.append({
                "exe": exe_path,
                "status": "hunt_failed",
                "returncode": returncode,
            })
            continue
        try:
            candidates = extract_candidate_keys(json_path)
        finally:
            _cleanup_hunt_json(json_path)
        new = [k for k in candidates if k not in all_candidates]
        all_candidates.extend(new)
        _emit(f"  found {len(candidates)} candidate(s) ({len(new)} new)")
        hunt_results.append({
            "exe": exe_path,
            "status": "ok",
            "candidates": candidates,
        })

    if not all_candidates:
        return {
            "status": "no_valid_key",
            "pak": pak,
            "scanned": [r["exe"] for r in hunt_results],
            "candidates": [],
            "verified": [],
            "saved": [],
            "hunt_results": hunt_results,
        }

    # 5. Validate the combined candidate list against the real pak.
    _emit(f"validating {len(all_candidates)} unique candidate(s) against the pak...")
    verified_keys = validate_keys_against_pak(
        pak,
        all_candidates,
        block_count=block_count,
        game=Path(pak_or_folder).stem,
    )
    verified = [vk.key for vk in verified_keys]
    _emit(f"{len(verified)} key(s) verified")

    # 6. Save verified keys.
    saved: list[str] = []
    if save_keys and verified_keys:
        store = KeyStore()
        base_title = title or Path(pak_or_folder).stem
        for index, vk in enumerate(verified_keys, start=1):
            entry_title = f"{base_title} [cracked-{index}]"
            try:
                store.add(
                    entry_title,
                    vk.key,
                    engine="unreal",
                    notes="auto-cracked via Ghidra (multi-binary scan)",
                    scheme=vk.scheme,
                    parameters=vk.parameters or None,
                )
                saved.append(entry_title)
            except ValueError:
                continue

    return {
        "status": "ok" if verified else "no_valid_key",
        "pak": pak,
        "scanned": [r["exe"] for r in hunt_results],
        "candidates": all_candidates,
        "verified": verified,
        "verified_keys": verified_keys,
        "saved": saved,
        "hunt_results": hunt_results,
    }
