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

    hint_name, hint_label = _game_hint(path, engine)
    game_name = name or hint_name
    game_label = label or hint_label
    scheme = _suggest_scheme(path, engine)

    driver = GameDriver(
        name=game_name,
        label=game_label,
        engine=engine,
        game_fragments=_fragments(path),
        archive_patterns=_patterns(path),
        encryption_scheme=scheme,
        export_formats=_format_defaults(engine),
        egame=_suggest_egame(detection, scheme),
        author="DualForge (auto-detected)",
        notes=_notes(detection),
    )
    return driver


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or "archived-game"


_GENERIC_DIRS = {
    "windows", "win64", "shipping", "game", "paks", "content",
    "steamapps", "steamlibrary", "common", "minor0", "minor",
    "data", "assets", "bin", "local", "android", "ios",
}


def _game_hint(path: Path, engine: str) -> tuple[str, str]:
    """Guess a driver name/label from the nearest non-generic ancestor folder."""
    fallback = _slug(path.parent.name) or path.stem
    label = path.parent.name or path.stem
    probe = path.parent
    for _ in range(5):
        if probe is None or probe.parent == probe:
            break
        if probe.name.lower() not in _GENERIC_DIRS and probe.name:
            label = probe.name
            fallback = _slug(probe.name)
            break
        probe = probe.parent
    if engine not in ("unity", "unreal"):
        return fallback, "Generic Game"
    return fallback, label


def _fragments(path: Path) -> list:
    frags = [path.stem, path.parent.name]
    probe = path.parent.parent
    for _ in range(4):
        if probe is None or probe.parent == probe:
            break
        frags.append(probe.name)
        probe = probe.parent
    # Strip generic dirs that aren't the game name, and trailing filename noise.
    cleaned = []
    for frag in frags:
        low = frag.lower()
        for suffix in ("windows", "win64", "shipping", "game", "paks", "content"):
            if low == suffix or low.endswith(suffix):
                frag = ""
                break
        if not frag or frag.lower() in _GENERIC_DIRS:
            continue
        if frag.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(frag)
    return list(dict.fromkeys(cleaned)) or [path.stem]


def _patterns(path: Path) -> list:
    import re

    name = path.name
    if name.lower().startswith("pakchunk") and name.lower().endswith(".pak"):
        pattern = re.sub(r"^pakchunk\d+", "pakchunk*", name)
        pattern = re.sub(r"_\d+(\.\d+)+.*", "*", pattern)
        if not pattern.endswith(".pak"):
            pattern += ".pak"
        return [pattern]
    return [name]


def _suggest_scheme(path: Path, engine: str) -> str:
    try:
        from dualforge.encryption.presets import guess_scheme

        preset = guess_scheme(mount=path.parent.as_posix(), archive_name=path.name)
        if preset is not None:
            return preset.stages[0] if preset.stages else preset.name
    except Exception:
        logger.debug("scheme suggestion failed for %s", path, exc_info=True)
        pass
    if engine == "unity":
        return "aes-256"
    return "aes-256"


def _suggest_egame(detection, scheme: str = "") -> str:
    if detection is None or detection.engine != "unreal":
        return ""
    try:
        from dualforge.unreal.uex_adapter import FOLDER_GAMES, SCHEME_GAMES, VERSION_GAMES

        if scheme in SCHEME_GAMES:
            return SCHEME_GAMES[scheme]
        if scheme == "sm4-abi":
            return "GAME_ArenaBreakoutInfinite"
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
