"""Auto-detect a game's main executable from an archive / install folder.

Given the path to a pak/archive or an install directory, this module locates
the game's primary ``.exe``. It is used to feed the automated AES key hunt
(Ghidra static analysis) so the key can be recovered from the binary.

Search order:

1. If the input is a file (e.g. a .pak), climb to the install root and search
   that. Otherwise treat the input as the install root.
2. Collect every ``*.exe`` under the root up to a sane depth, score them using
   several heuristics (folder-name match, ``Binaries`` adjacency, UE naming
   conventions, GameLoader/Shipping patterns, size) and return the winner.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

# Well-known UE / Tencent game executable name fragments, in rough priority.
_EXE_HINTS = (
    "shipping",
    "game",
    "client",
    "win64",
    "binaries",
)

# Names that are clearly NOT the main game binary (launchers/loaders/utils).
_IGNORED_FRAGMENTS = (
    "uninstall",
    "crash",
    "report",
    "patch",
    "updater",
    "install",
    "uninst",
    "diagnostic",
    "repair",
    "render",
    "launcher",
    "webview",
    "crossplatform",
    "antivirus",
    "service",
    "helper",
    "proxy",
    "protect",
    "anticheat",
    "sguard",
    "setup",
    "redist",
    "prereq",
    "vcredist",
    "dotnet",
    "ndp",
)

_IS_EXE = ("*.exe",)
_MAX_DEPTH = 8


def _fold(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def find_install_root(archive_or_folder: str) -> Path:
    """Return the game install root for a pak/folder path."""
    p = Path(archive_or_folder).resolve()
    if not p.is_file():
        return p
    # For a file, the install root is the first ancestor directory that
    # directly contains a "Content" folder (the pak lives under Content/Paks).
    cur = p.parent
    for _ in range(6):
        if cur is None or cur == cur.parent:
            break
        content = cur / "Content"
        if content.is_dir():
            # pak should be under this Content; the root is the folder
            # that owns Content.
            return cur
        cur = cur.parent
    return p.parent


def _candidate_exes(root: Path, max_depth: int = _MAX_DEPTH) -> List[Path]:
    """All .exe files under ``root`` (skipping ignored dirs like Windows/System32)."""
    results: List[Path] = []
    for pattern in _IS_EXE:
        for exe in root.rglob(pattern):
            depth = len(exe.relative_to(root).parts)
            if depth > max_depth:
                continue
            low = exe.as_posix().lower()
            if any(seg in low for seg in ("\\windows\\", "/windows/", "\\system32\\")):
                continue
            results.append(exe)
    return results


def _score_exe(exe: Path, root: Path) -> float:
    """Heuristic score for how likely ``exe`` is the game's main binary."""
    name = exe.stem
    name_low = name.lower()
    folded = _fold(name)
    rel = exe.relative_to(root).as_posix().lower()

    score = 0.0

    # Ignore obvious non-game binaries.
    for ignored in _IGNORED_FRAGMENTS:
        if ignored in name_low:
            return -1.0

    # UE Shipping builds are strong signals.
    if "shipping" in name_low:
        score += 120.0
    if re.search(r"win64|win32|uwp", name_low):
        score += 20.0

    # Lives under Binaries/Win64 -> strong.
    if "binaries" in rel:
        score += 60.0
        if "win64" in rel or "win32" in rel:
            score += 30.0

    # Exe name matches / contains the install folder name.
    root_name = _fold(root.name)
    if root_name and root_name in folded and len(folded) >= 4:
        score += 80.0

    # Any of the generic "game" family hints.
    for hint in _EXE_HINTS:
        if hint in name_low:
            score += 10.0

    # Bigger binaries are more likely the full game (avoid tiny helpers).
    try:
        size = exe.stat().st_size
    except OSError:
        size = 0
    if size > 50 * 1024 * 1024:
        score += 40.0
    elif size > 5 * 1024 * 1024:
        score += 15.0

    # Prefer top-level/looser subfolder over deep third-party paths.
    if rel.count("/") <= 2:
        score += 10.0

    return score


def find_game_executable(archive_or_folder: str) -> Tuple[Optional[str], List[Tuple[str, float]]]:
    """Locate the game's main executable under an install root.

    Returns ``(best_path, ranked_candidates)`` where candidates are
    ``(path, score)`` sorted descending. ``best_path`` is None when nothing
    plausible was found.
    """
    root = find_install_root(archive_or_folder)
    if not root.is_dir():
        return None, []
    ranked: List[Tuple[str, float]] = []
    for exe in _candidate_exes(root):
        score = _score_exe(exe, root)
        if score < 0:
            continue
        ranked.append((str(exe), score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    best = ranked[0][0] if ranked else None
    return best, ranked
