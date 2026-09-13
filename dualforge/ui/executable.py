"""Auto-detect a game's main executable inside an opened folder.

Helps the Tools menu (Ghidra Key Hunt etc.) start with the right binary and
lets DualForge identify which game a folder belongs to so the matching driver
can be applied automatically.
"""

from __future__ import annotations

from pathlib import Path

from dualforge.drivers import registry


def find_game_executable(folder: str) -> str | None:
    """Return the most likely game executable path in ``folder`` (or None)."""
    from dualforge.unreal.autodetect import find_game_executable as _find

    best, _ranked = _find(folder)
    return best


def identify_game(exe_path: str, folder: str = "") -> object | None:
    """Match an executable (or folder) against the driver registry.

    Returns the best-scoring ``GameDriver`` or None.
    """
    candidates = [
        p
        for p in (exe_path, folder)
        if p
    ]
    text = " ".join(candidates).lower()
    best = None
    best_score = 0.0
    for driver in registry.list():
        for frag in driver.game_fragments:
            if frag.lower() in text:
                score = len(frag)
                if score > best_score:
                    best_score = score
                    best = driver
                break
    return best


def find_usmap_output(exe_path: str) -> str | None:
    """Suggest a .usmap output path for the given game executable."""
    if not exe_path:
        return None
    base = Path(exe_path).stem
    return str(Path.home() / ".dualforge" / f"{base}.usmap")


__all__ = ["find_game_executable", "identify_game", "find_usmap_output"]
