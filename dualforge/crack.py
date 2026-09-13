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

from dualforge.encryption.brute import probe_pak_blocks, validate_key
from dualforge.encryption.presets import PRESETS, GameScheme, guess_scheme
from dualforge.encryption.registry import list_schemes
from dualforge.unreal.autodetect import find_game_executable, find_install_root
from dualforge.unreal.keys import KeyStore

_HUNT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ghidra" / "ghidra_key_finder.py"


def find_validation_pak(pak_or_folder: str) -> str:
    """Return a pak to validate candidate keys against."""
    candidate = Path(pak_or_folder).resolve()
    if candidate.is_file():
        return str(candidate)
    root = find_install_root(pak_or_folder)
    best: tuple[str, int] | None = None
    for pak in root.rglob("*.pak"):
        # skip tiny patch paks; prefer a full archive but any is fine
        if best is None:
            best = (str(pak), 0)
        try:
            size = pak.stat().st_size
        except OSError:
            size = 0
        if size > (best[1] if best else 0):
            best = (str(pak), size)
    if best is None:
        raise RuntimeError(f"no .pak found under {root}")
    return best[0]


def run_ghidra_hunt(
    binary: str,
    ghidra_home: Path,
    startup_timeout: int = 300,
) -> tuple[int | None, str, str]:
    """Run the static Ghidra hunt, returning (returncode, stdout, json_path)."""
    tmpdir = Path(tempfile.mkdtemp(prefix="dualforge_hunt_"))
    json_path = tmpdir / "candidates.json"
    cmd = [
        sys.executable,
        str(_HUNT_SCRIPT),
        binary,
        "--ghidra-home",
        str(ghidra_home),
        "--json",
        str(json_path),
        "--no-add-keystore",
        "--startup-timeout",
        str(startup_timeout),
    ]
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


def extract_candidate_keys(json_path: str | None, limit: int = 64) -> list[str]:
    """Flatten the 32-byte hex candidates from a hunt JSON result."""
    keys: list[str] = []
    if not json_path or not Path(json_path).is_file():
        return keys
    try:
        payload = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return keys
    for match in payload.get("matches", []):
        for candidate in match.get("candidates", []):
            hexval = (candidate.get("hex") or "").strip().lower().replace("0x", "")
            if len(hexval) == 64 and hexval not in keys:
                keys.append(hexval)
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
