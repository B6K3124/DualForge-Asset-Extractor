"""Curated registry of known game encryption schemes.

This is the "future-proofing" surface: adding a new game's protection is a
one-line preset (or a tiny per-game scheme in ``schemes/games.py``). The data
here mirrors the publicly documented behaviour of real titles (as implemented
by CUE4Parse) in a data-driven, clean-room form so the app can pick the right
pipeline without user involvement.

Each preset:

    name            unique id (used as ``KeyEntry.scheme``)
    label           human-friendly name
    games           set of game title fragments matched against the pak mount
                    point or a user-specified game
    detect          fn(archive_path, mount) -> bool  (optional predicate)
    stages          ordered pipeline stage names
    default_params  scheme-specific parameters (xor_key, header_bytes, ...)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable


@dataclass
class GameScheme:
    name: str
    label: str
    games: set[str] = field(default_factory=set)
    stages: list[str] = field(default_factory=list)
    default_params: dict[str, str] = field(default_factory=dict)
    detect: Callable[[str, str], bool] | None = None


# Stage names must exist in the registry; unknown names are ignored by
# ``build_pipeline``. Common mappings used below:
#   aes-256            standard single-key AES-256 ECB
#   xor8               Delta-Force-style 8-byte repeating XOR
#   derived-aes-md5    Snowbreak-style md5(pakname) derived key
#   partial-encrypt    Wuthering Waves / NetEase partial encryption
#   unity-cn           Unity CN Pro 16-char bundle XOR
#   derived-xor-md5    Star-Savior-style filename-md5 XOR mask

PRESETS: list[GameScheme] = [
    GameScheme(
        name="aes-256",
        label="Standard AES-256",
        stages=["aes-256"],
    ),
    GameScheme(
        name="fortnite",
        label="Fortnite (partitioned / OOTP / dynamic keys)",
        games={"Fortnite", "FortniteGame"},
        stages=["aes-256"],
    ),
    GameScheme(
        name="snowbreak",
        label="Snowbreak: Containment Zone (derived AES)",
        games={"Snowbreak"},
        stages=["derived-aes-md5"],
    ),
    GameScheme(
        name="delta-force",
        label="Delta Force (AES + 8-byte XOR)",
        games={"DeltaForce", "Delta Force"},
        stages=["aes-256", "xor8"],
    ),
    GameScheme(
        name="huwei",
        label="War Thunder / HoYoverse split (AES + dynamic)",
        stages=["aes-256"],
    ),
    GameScheme(
        name="wuthering-waves",
        label="Wuthering Waves (partial encryption)",
        games={"WutheringWaves", "Wuthering Waves"},
        stages=["partial-encrypt"],
    ),
    GameScheme(
        name="marvel-rivals",
        label="Marvel Rivals (AES + XOR)",
        stages=["aes-256", "xor8"],
    ),
    GameScheme(
        name="dragon-sword",
        label="Dragon Sword (custom round keys)",
        stages=["custom-aes-round"],
    ),
    GameScheme(
        name="monster-jam",
        label="Monster Jam Showdown (custom round keys)",
        games={"MonsterJam"},
        stages=["custom-aes-round"],
    ),
    GameScheme(
        name="unity-cn",
        label="Unity CN Pro bundle (16-char XOR)",
        games={"unity-cn"},
        stages=["unity-cn"],
        default_params={"header_bytes": "0"},
    ),
    GameScheme(
        name="star-savior",
        label="Star Savior (per-filename md5 XOR)",
        stages=["derived-xor-md5"],
    ),
]

_BY_NAME: dict[str, GameScheme] = {p.name: p for p in PRESETS}


def get_preset(name: str) -> GameScheme | None:
    return _BY_NAME.get(name)


def guess_scheme(mount: str = "", archive_name: str = "", game: str = "") -> GameScheme | None:
    """Pick a preset by matching the pak mount point / game title.

    Returns the first preset whose ``detect`` passes or whose ``games`` set
    contains a case-insensitive fragment of the provided hints. Falls back to
    the standard AES-256 preset (None) when nothing matches.
    """
    hints = " ".join(filter(None, [mount, archive_name, game])).lower()
    for preset in PRESETS:
        if preset.detect and preset.detect(mount, archive_name):
            return preset
        for g in preset.games:
            if g.lower() in hints:
                return preset
    return None
