"""REDengine path-hash database loading.

REDengine 4 archives (.archive) address files only by a 64-bit FNV-1a
hash of their depot path; the path itself is not stored.  To recover real
names DualForge reads a CSV hash table (one ``hash,path`` pair per line,
already-lowercased, forward-slash paths).  The table is a plain text file
so community dumps (WolvenKit BC7 project ``hashlist.txt``-style exports)
can be dropped in without conversion tools.

CSV format
----------
    ::

        hash,path
        0x0f7b1b2e3c4d5e6f,base/gameplay/game/video/combat_video_meta.clip
        1125899906842624,audio/sounds_v2/music/music_tracks/track_apb01.wem

    * ``hash``   unsigned 64-bit decimal or ``0x``-prefixed hexadecimal
    * ``path``   depot path, forward slashes (everything after the first
                 comma is kept verbatim, so paths may contain commas)
    * a header row whose first field is ``hash`` (case-insensitive) is
                 skipped; anything else is treated as data

    Rows may also be whitespace-separated (``hash  path``) for WolvenKit
    ``hashlist.txt`` dumps - depot paths never contain spaces, so the first
    whitespace run is unambiguous.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HASH_CSV = Path.home() / ".dualforge" / "cp77_hashes.csv"
ENV_HASH_CSV = "DUALFORGE_CDPR_HASHES"

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3


def fnv1a64(path: str) -> int:
    """Standard FNV-1a 64-bit hash used by REDengine 4 for depot paths.

    The game normalises depot names to lowercase and forward slashes; callers
    handle that normalisation before hashing when they need to match the game.
    """
    value = _FNV_OFFSET
    for byte in path.encode("utf-8"):
        value ^= byte
        value = (value * _FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return value


class HashDatabaseError(ValueError):
    """Raised when a hash table cannot be parsed."""


@dataclass
class HashDatabase:
    """Resolves CDPR 64-bit path hashes to depot path strings."""

    path: str = ""
    by_hash: dict[int, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.by_hash)

    def resolve(self, hash_int: int) -> str | None:
        return self.by_hash.get(hash_int & 0xFFFFFFFFFFFFFFFF)

    @property
    def is_empty(self) -> bool:
        return not self.by_hash

    @classmethod
    def from_csv(cls, path: str) -> HashDatabase:
        rows: dict[int, str] = {}
        try:
            stream = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise HashDatabaseError(f"cannot read hash table: {exc}") from exc
        for line_no, raw in enumerate(stream.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            comma = line.find(",")
            if comma < 0:
                hash_field, _, depot_path = line.partition("  ")
                if not hash_field or not depot_path:
                    rest = line.split(None)
                    if len(rest) < 2:
                        raise HashDatabaseError(
                            f"{path}:{line_no} expected 'hash,path' row, got {line!r}"
                        )
                    hash_field, depot_path = rest[0], " ".join(rest[1:])
            else:
                hash_field = line[:comma].strip()
                depot_path = line[comma + 1:].strip()
            if hash_field.lower() == "hash":
                continue
            try:
                hash_int = (
                    int(hash_field, 16)
                    if hash_field.lower().startswith("0x")
                    else int(hash_field)
                )
            except ValueError as exc:
                raise HashDatabaseError(
                    f"{path}:{line_no} bad hash {hash_field!r}"
                ) from exc
            if not depot_path:
                raise HashDatabaseError(f"{path}:{line_no} missing path for hash")
            rows[hash_int & 0xFFFFFFFFFFFFFFFF] = depot_path
        return cls(path=path, by_hash=rows)


def default_hash_csv() -> str:
    """Resolve the hash table the user intends: settings override env override default."""
    env = os.environ.get(ENV_HASH_CSV, "")
    if env and Path(env).is_file():
        return env
    if DEFAULT_HASH_CSV.is_file():
        return str(DEFAULT_HASH_CSV)
    return env or str(DEFAULT_HASH_CSV)


def load_hash_database(path: str | None = None) -> HashDatabase:
    """Load a hash table from ``path`` (or the resolved default when None/
    empty).  Missing default files yield an empty database instead of raising
    so the collector can run hash-only from day one."""
    target = path or default_hash_csv()
    if not target or not Path(target).is_file():
        return HashDatabase(path=target)
    return HashDatabase.from_csv(target)


__all__ = [
    "DEFAULT_HASH_CSV",
    "ENV_HASH_CSV",
    "HashDatabase",
    "HashDatabaseError",
    "default_hash_csv",
    "fnv1a64",
    "load_hash_database",
]