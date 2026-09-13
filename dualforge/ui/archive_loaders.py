"""Archive discovery helpers shared by the UI folder/drop browsers.

Uncoupled archive-suffix / kind / scan logic so the main window stays thin:
``scan_archives`` walks a game folder for supported archives, ``bethesda_kind``
maps Bethesda entry suffixes to a generic asset kind, and ``is_supported_archive``
guards drag-and-drop targets.
"""

from __future__ import annotations

from pathlib import Path

ARCHIVE_SUFFIXES = (
    ".pak",
    ".utoc",
    ".ucas",
    ".unity3d",
    ".unityweb",
    ".bundle",
    ".assets",
    ".assetbundle",
    ".archive",
    ".bsa",
    ".ba2",
)

_BETHESDA_KINDS = {
    ".dds": "texture",
    ".png": "texture",
    ".jpg": "texture",
    ".jpeg": "texture",
    ".tga": "texture",
    ".bmp": "texture",
    ".nif": "mesh",
    ".wav": "audio",
    ".fuz": "audio",
    ".xwm": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".txt": "text",
    ".json": "text",
    ".xml": "text",
    ".csv": "text",
    ".hlsl": "text",
    ".fx": "text",
    ".hkx": "anim",
    ".kf": "anim",
    ".bto": "terrain",
    ".btr": "terrain",
}


def bethesda_kind(name: str) -> str:
    return _BETHESDA_KINDS.get(Path(name).suffix.lower(), "file")


def is_supported_archive(path: str) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_SUFFIXES


def scan_archives(folder: str, depth: int = 4) -> list[str]:
    """Walk ``folder`` (recursively, depth-bounded) for supported archives.

    Skips well-known bulk/uninteresting directories (steamapps, common,
    node_modules, .git) to keep the scan fast and useful. Missing/
    unreadable lookup errors are silently absorbed and never raise.
    """
    found: list[str] = []
    root = Path(folder)
    if depth <= 0:
        return found
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.is_dir():
                if entry.name.lower() in {"steamapps", "common", "node_modules", ".git"}:
                    continue
                found.extend(scan_archives(str(entry), depth - 1))
            elif entry.suffix.lower() in ARCHIVE_SUFFIXES:
                found.append(str(entry))
        except OSError:
            continue
    return found


__all__ = ["ARCHIVE_SUFFIXES", "bethesda_kind", "is_supported_archive", "scan_archives"]