from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dualforge.unreal.bridge import UnrealError

# CUE4Parse EGame candidates per pak index version (see docs/COMPATIBILITY.md).
# The pak "footer version" (pak_footer_version) lags the pak version by one:
#   footer 9  -> pak v8B  (UE 4.17-4.21)
#   footer 10 -> pak v9   (UE 4.22-4.25)
#   footer 11 -> pak v10  (UE 4.26-4.27)
#   footer 12 -> pak v11  (UE 5.0-5.3)
#   footer 13 -> pak v12  (UE 5.4+)
VERSION_GAMES: Dict[int, List[str]] = {
    9: ["GAME_UE4_17", "GAME_UE4_18", "GAME_UE4_19", "GAME_UE4_20", "GAME_UE4_21"],
    10: ["GAME_UE4_22", "GAME_UE4_23", "GAME_UE4_24", "GAME_UE4_25"],
    11: ["GAME_UE4_26", "GAME_UE4_27", "GAME_UE4_28"],
    12: ["GAME_UE5_0", "GAME_UE5_1", "GAME_UE5_2", "GAME_UE5_3"],
    13: [
        "GAME_UE5_4",
        "GAME_UE5_5",
        "GAME_UE5_6",
        "GAME_UE5_7",
        "GAME_UE5_8",
        "GAME_UE5_9",
    ],
}

# Known games whose exact CUE4Parse EGame beats the generic engine version.
FOLDER_GAMES: List[Tuple[str, str]] = [
    ("tekken", "GAME_TEKKEN7"),
    ("fortnite", "GAME_Fortnite"),
    ("palworld", "GAME_Palworld"),
    ("tarkov", "GAME_EscapeFromTarkov"),
    ("valorant", "GAME_VALORANT"),
]

# Map DualForge scheme/preset names to CUE4Parse EGame values so scheme-based
# archives decrypt through the correct GameType profile.
SCHEME_GAMES: Dict[str, str] = {
    "delta-force": "GAME_DeltaForce",
    "marvel-rivals": "GAME_MarvelRivals",
    "snowbreak": "GAME_Snowbreak",
    "wuthering-waves": "GAME_WutheringWaves",
    "fortnite": "GAME_Fortnite",
    "monster-jam": "GAME_MonsterJamShowdown",
    "dragon-sword": "GAME_DragonSword3",
}

SEARCH_LIMIT = 1_000_000
DOCTOR_TIMEOUT = 900
SEARCH_TIMEOUT = 900
EXPORT_TIMEOUT = 7200

_MOUNTED_RE = re.compile(r"mounted:\s+\d+\s+archives,\s+(\d+)\s+files", re.IGNORECASE)
_EXPORTED_RE = re.compile(
    r"exported:\s+(\d+)\s+packages,\s+(\d+)\s+textures,\s+(\d+)\s+decoded data,\s+(\d+)\s+raw files",
    re.IGNORECASE,
)


def normalize_aes_key(key: Optional[str]) -> Optional[str]:
    """uex profiles expect 0x-prefixed hex; DualForge stores bare hex."""
    if not key:
        return None
    key = key.strip()
    if key.lower().startswith("0x"):
        return key
    return "0x" + key


def find_usmap(paks_dir: str) -> Optional[str]:
    """Locate a CUE4Parse mappings file for unversioned (UE5.3+) packages.

    Order: DUALFORGE_USMAP env var, ~/.dualforge/*.usmap, the archive's own
    folder, then the working directory.
    """
    from pathlib import Path

    candidates: List[Path] = []
    env = os.environ.get("DUALFORGE_USMAP")
    if env:
        candidates.append(Path(env))
    candidates.extend(Path.home().glob(".dualforge/*.usmap"))
    candidates.extend(Path(paks_dir).glob("*.usmap"))
    candidates.extend(Path.cwd().glob("*.usmap"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def egame_candidates(paks_dir: str, footer_version: Optional[int]) -> List[str]:
    """Ordered list of EGame names to try for a pak folder."""
    candidates: List[str] = []
    lowered = paks_dir.lower()
    for needle, game in FOLDER_GAMES:
        if needle in lowered and game not in candidates:
            candidates.append(game)
    if footer_version is not None:
        for game in VERSION_GAMES.get(footer_version, []):
            if game not in candidates:
                candidates.append(game)
    for game in ("GAME_UE5_LATEST", "GAME_UE4_LATEST"):
        if game not in candidates:
            candidates.append(game)
    return candidates


def parse_search_output(output: str) -> List[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def parse_export_summary(output: str) -> int:
    match = _EXPORTED_RE.search(output)
    if not match:
        return 0
    return sum(int(group) for group in match.groups())


_MESH_RE = re.compile(r"meshexport:\s*([a-z0-9_]+)(?:\s*->.*)?", re.IGNORECASE)


def parse_mesh_summary(output: str) -> Optional[str]:
    """Extract the exported mesh kind (``staticmesh``/``skeletalmesh``/...) from
    uex ``preview-mesh`` output, or ``None`` when the package held no readable
    mesh (``meshexport: none``) or nothing was mentioned."""
    match = _MESH_RE.search(output)
    if not match:
        return None
    kind = match.group(1).lower()
    if kind == "none":
        return None
    return kind


class UexAdapter:
    """Thin adapter around the 'uex' CUE4Parse CLI (https://github.com/arkive-games/uex).

    uex is profile-based and writes FModel-style trees, so DualForge generates a
    throwaway profiles.json (--config) per invocation: paksDir = the archive's
    folder, aesKey from the caller/key store, outputDir = the export target.
    The engine (EGame) is auto-probed with `doctor` against version candidates.
    """

    def __init__(self, cli_path: str):
        self.cli_path = cli_path
        self._games: Dict[str, str] = {}

    # ------------------------------------------------------------- public API

    def list_files(
        self,
        pak: str,
        aes_key: Optional[str] = None,
        usmap: Optional[str] = None,
        dynamic_keys: Optional[Dict[str, str]] = None,
        scheme: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        paks_dir = str(Path(pak).parent)
        game = self._game_for(paks_dir, aes_key, usmap, scheme)
        config = _write_config(
            paks_dir, aes_key, str(Path(pak).parent), [],
            game, usmap, dynamic_keys=dynamic_keys,
        )
        try:
            output, stderr, code = self._run(
                [
                    "search",
                    "--profile",
                    "dualforge",
                    "--config",
                    str(config),
                    ".*",
                    "--regex",
                    "--limit",
                    str(SEARCH_LIMIT),
                ],
                timeout=SEARCH_TIMEOUT,
            )
        finally:
            _remove(config)
        if code != 0:
            raise UnrealError(f"uex search failed (exit {code}): {stderr.strip() or output.strip()}")
        if "raise --limit" in stderr:
            raise UnrealError(
                f"uex hit its {SEARCH_LIMIT} result cap while listing {Path(pak).parent} - "
                "this game is exceptionally large; file an issue."
            )
        return [{"path": path} for path in parse_search_output(output)]

    def extract(
        self,
        pak: str,
        out_dir: str,
        aes_key: Optional[str] = None,
        files: Optional[List[str]] = None,
        usmap: Optional[str] = None,
        dynamic_keys: Optional[Dict[str, str]] = None,
        scheme: Optional[str] = None,
    ) -> int:
        paks_dir = str(Path(pak).parent)
        game = self._game_for(paks_dir, aes_key, usmap, scheme)
        roots = _normalize_vpaths(files or self._default_roots(pak, aes_key, usmap))
        config = _write_config(
            paks_dir, aes_key, out_dir, roots, game, usmap,
            dynamic_keys=dynamic_keys,
        )
        try:
            args = ["export", "--profile", "dualforge", "--config", str(config)]
            if roots:
                args += ["--only"] + roots
            output, stderr, code = self._run(args, timeout=EXPORT_TIMEOUT)
        finally:
            _remove(config)
        if code != 0:
            raise UnrealError(f"uex export failed (exit {code}): {stderr.strip() or output.strip()}")
        return parse_export_summary(output)

    def preview_mesh(
        self,
        pak: str,
        vpath: str,
        aes_key: Optional[str] = None,
        usmap: Optional[str] = None,
        dynamic_keys: Optional[Dict[str, str]] = None,
        scheme: Optional[str] = None,
    ) -> Optional[Tuple[bytes, str]]:
        """Export one package's mesh as GLB via uex ``preview-mesh``.

        Returns ``(glb_bytes, kind)`` where ``kind`` is the exported Unreal
        type (e.g. ``staticmesh`` / ``skeletalmesh``), or ``None`` when the
        package holds no readable mesh (uex reports ``meshexport: none``).
        """
        paks_dir = str(Path(pak).parent)
        game = self._game_for(paks_dir, aes_key, usmap, scheme)
        config = _write_config(
            paks_dir, aes_key, str(paks_dir), [vpath], game, usmap,
            dynamic_keys=dynamic_keys,
        )
        out_path = Path(tempfile.mkstemp(suffix=".glb", prefix="dualforge_mesh_")[1])
        try:
            args = [
                "preview-mesh",
                "--profile",
                "dualforge",
                "--config",
                str(config),
                vpath,
                "--out",
                str(out_path),
            ]
            output, stderr, code = self._run(args, timeout=EXPORT_TIMEOUT)
        finally:
            _remove(config)
        if code != 0:
            raise UnrealError(f"uex preview-mesh failed (exit {code}): {stderr.strip() or output.strip()}")
        combined = f"{output}\n{stderr}"
        if "meshexport: none" in combined:
            return None
        summary = parse_mesh_summary(combined)
        if not out_path.is_file():
            raise UnrealError("uex preview-mesh reported success but wrote no GLB")
        try:
            glb = out_path.read_bytes()
        finally:
            _remove(out_path)
        if not glb:
            return None
        return glb, summary

    # -------------------------------------------------------------- internals

    def _default_roots(self, pak: str, aes_key: Optional[str], usmap: Optional[str]) -> List[str]:
        """Top-level virtual folders of the game, used for whole-archive exports."""
        try:
            entries = self.list_files(pak, aes_key, usmap)
        except UnrealError:
            return []
        roots = {path.split("/", 1)[0] for path in (e["path"] for e in entries)}
        return sorted(roots)

    def _game_for(
        self,
        paks_dir: str,
        aes_key: Optional[str],
        usmap: Optional[str] = None,
        scheme: Optional[str] = None,
    ) -> str:
        cached = self._games.get(paks_dir)
        if cached:
            return cached
        # A known scheme maps to a definitive GameType; prefer it over probing.
        if scheme and scheme in SCHEME_GAMES:
            self._games[paks_dir] = SCHEME_GAMES[scheme]
            return SCHEME_GAMES[scheme]
        from dualforge.unreal.pak import pak_footer_version

        footer = None
        try:
            footer = pak_footer_version(str(Path(paks_dir) / _probe_pak(paks_dir)))
        except Exception:
            pass
        candidates = egame_candidates(paks_dir, footer)
        last_error = ""
        for game in candidates:
            config = _write_config(paks_dir, aes_key, str(Path(paks_dir)), [], game, usmap)
            try:
                output, stderr, code = self._run(
                    ["doctor", "--profile", "dualforge", "--config", str(config)],
                    timeout=DOCTOR_TIMEOUT,
                )
            finally:
                _remove(config)
            if code == 0 or _mounted_file_count(output) > 0:
                self._games[paks_dir] = game
                return game
            last_error = f"{game}: {stderr.strip() or output.strip()}"
        raise UnrealError(
            "no working UE version found for this archive. Tried: "
            f"{', '.join(candidates)}. Last attempt: {last_error}"
        )

    def _run(self, args: List[str], timeout: int = 600) -> Tuple[str, str, int]:
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                [self.cli_path] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=flags,
            )
        except FileNotFoundError as exc:
            raise UnrealError(f"could not run uex: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise UnrealError(f"uex timed out after {timeout}s") from exc
        return completed.stdout or "", completed.stderr or "", completed.returncode


def _probe_pak(paks_dir: str) -> str:
    """Pick the first real pak in a Paks folder to read its footer version from."""
    for candidate in sorted(Path(paks_dir).glob("*.pak")):
        return candidate.name
    return "probe.pak"


def _normalize_vpaths(paths: List[str]) -> List[str]:
    return sorted({path.replace("\\", "/").strip("/") for path in paths if path.strip()})


def _mounted_file_count(output: str) -> int:
    match = _MOUNTED_RE.search(output)
    return int(match.group(1)) if match else 0


def _write_config(
    paks_dir: str,
    aes_key: Optional[str],
    out_dir: str,
    roots: List[str],
    game: str,
    usmap: Optional[str] = None,
    dynamic_keys: Optional[Dict[str, str]] = None,
    custom_key: Optional[str] = None,
) -> Path:
    config = {
        "profiles": {
            "dualforge": {
                "game": game,
                "paksDir": str(paks_dir),
                "usmap": usmap or None,
                "aesKey": normalize_aes_key(aes_key),
                "outputDir": str(out_dir),
                "exportRoots": list(roots),
            }
        }
    }
    profile = config["profiles"]["dualforge"]
    if dynamic_keys:
        profile["dynamicKeys"] = {str(k): normalize_aes_key(v) for k, v in dynamic_keys.items() if v}
    if custom_key:
        profile["customKey"] = custom_key
    fd, path = tempfile.mkstemp(suffix=".json", prefix="dualforge_uex_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(config, fh)
    return Path(path)


def _remove(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


__all__ = [
    "UexAdapter",
    "egame_candidates",
    "find_usmap",
    "normalize_aes_key",
    "parse_export_summary",
    "parse_mesh_summary",
    "parse_search_output",
]
