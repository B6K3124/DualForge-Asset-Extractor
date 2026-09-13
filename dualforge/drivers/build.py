"""Auto-build a GameDriver from an archive, "from scratch".

Given an archive path, this module analyzes the file to infer:
  - engine type (unity / unreal / container)
  - suggested encryption scheme (from the scheme registry / presets)
  - export format defaults (from the convert module)
  - detection fragments derived from folder/file names

The result is a usable ``GameDriver`` the user can review, tweak, and save.
"""

from __future__ import annotations

from pathlib import Path

from dualforge.detector import detect
from dualforge.drivers.driver import GameDriver
from dualforge.export.convert import DEFAULT_FORMATS
from dualforge.log import get_logger

logger = get_logger(__name__)


def build_driver_from_archive(
    archive_path: str,
    name: str | None = None,
    label: str | None = None,
) -> GameDriver:
    """Infer a game driver from an archive file (best-effort).

    ``name``/``label`` default to a slug of the archive's parent folder (the
    most likely game name). The engine, scheme, formats and fragments are
    derived automatically where possible.
    """
    path = Path(archive_path)
    detection = detect(archive_path)
    engine = "auto"
    if detection is not None:
        engine = detection.engine

    game_name = name or _slug(path.parent.name) or path.stem
    game_label = label or path.parent.name or path.stem if engine in ("unity", "unreal") else label or "Generic Game"

    driver = GameDriver(
        name=game_name,
        label=game_label,
        engine=engine,
        game_fragments=_fragments(path),
        archive_patterns=_patterns(path),
        encryption_scheme=_suggest_scheme(path, engine),
        export_formats=_format_defaults(engine),
        egame=_suggest_egame(detection),
        author="DualForge (auto-detected)",
        notes=_notes(detection),
    )
    return driver


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "archived-game"


def _fragments(path: Path) -> list:
    frags = [path.stem, path.parent.name]
    # Strip common suffixes that aren't the game name
    cleaned = []
    for frag in frags:
        for suffix in ("windows", "win64", "shipping", "game", "paks", "content"):
            if frag.lower() == suffix:
                frag = ""
                break
        if frag:
            cleaned.append(frag)
    return list(dict.fromkeys(cleaned))


def _patterns(path: Path) -> list:
    return [path.name]


def _suggest_scheme(path: Path, engine: str) -> str:
    try:
        from dualforge.encryption.presets import guess_scheme

        preset = guess_scheme(archive_name=path.name, mount=path.parent.name)
        if preset is not None:
            return preset.name
    except Exception:
        logger.debug("scheme suggestion failed for %s", path, exc_info=True)
        pass
    if engine == "unity":
        return "aes-256"
    return "aes-256"


def _suggest_egame(detection) -> str:
    if detection is None or detection.engine != "unreal":
        return ""
    try:
        from dualforge.unreal.uex_adapter import FOLDER_GAMES, VERSION_GAMES

        lowered = str(detection.path).lower()
        for needle, game in FOLDER_GAMES:
            if needle in lowered:
                return game
        version = detection.details.get("pak_version")
        if version is not None:
            candidates = VERSION_GAMES.get(version, [])
            if candidates:
                return candidates[0]
    except Exception:
        logger.debug("egame suggestion failed", exc_info=True)
        pass
    return ""


def _format_defaults(engine: str) -> dict:
    if engine == "unity":
        return dict(DEFAULT_FORMATS)
    return {}


def _notes(detection) -> str:
    if detection is None:
        return "Auto-generated driver (unrecognized archive)."
    return (
        f"Auto-detected: {detection.engine}/{detection.kind}. "
        "Review the encryption scheme and export formats before use."
    )


__all__ = ["build_driver_from_archive"]
