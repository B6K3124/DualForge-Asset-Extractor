"""Parser for Unreal Engine .locres localization files.

The .locres format packs namespaced key/value string pairs. It is a
versioned, binary, length-prefixed format that ships with UE games and is
read natively here (no CUE4Parse bridge required) so localization tables
can be previewed and exported to JSON/CSV.

Format notes (compatible with what CUE4Parse/FModel read):

    Header:
        u32  Magic      = 0x324F4352  ("ROCO2")
        u8   Version    (1: compact, 2: legacy, 3: optimized)

    Legacy / Optimized body:
        u32  table_count
        for each table:
            FString namespace
            u32    entry_count
            for each entry:
                FString key
                FString value

    FString:  int32 length (negative = UTF-16LE, positive = ANSI/UTF-8,
              includes a terminating NUL that is skipped during decoding).
              length == 0 is the empty string.
"""

from __future__ import annotations

import json
import struct
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = 0x324F4352

VERSION_NAMES = {
    1: "compact",
    2: "legacy",
    3: "optimized",
}


@dataclass
class LocresEntry:
    namespace: str
    key: str
    source: str = ""
    value: str = ""

    @property
    def qualified_key(self) -> str:
        if self.namespace:
            return f"{self.namespace}.{self.key}"
        return self.key


@dataclass
class LocresFile:
    version: int | None = None
    magic_ok: bool = False
    entries: list[LocresEntry] = field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        return {entry.qualified_key: entry.value for entry in self.entries}

    def to_json(self, indent: int = 2) -> str:
        obj = {
            "version": VERSION_NAMES.get(self.version, self.version),
            "entries": [
                {
                    "namespace": entry.namespace,
                    "key": entry.key,
                    "source": entry.source,
                    "value": entry.value,
                }
                for entry in self.entries
            ],
        }
        return json.dumps(obj, indent=indent, ensure_ascii=False)

    def to_csv(self) -> str:
        lines = ["namespace,key,source,value"]
        for entry in self.entries:
            lines.append(
                ",".join(_csv_field(field_value) for field_value in (entry.namespace, entry.key, entry.source, entry.value))
            )
        return "\n".join(lines)


def parse_locres(data: bytes) -> LocresFile:
    if not data:
        raise ValueError("empty locres data")
    result = LocresFile()
    pos = 0
    if len(data) >= 5:
        magic = _u32(data, 0)
        if magic == MAGIC:
            result.magic_ok = True
            result.version = data[4]
            pos = 5
        else:
            result.version = _u32(data, 0)
            pos = 4
    tables = list(_read_string_tables(data, pos))
    for namespace, entries in tables:
        for key, value in entries:
            result.entries.append(LocresEntry(namespace=namespace, key=key, value=value))
    if not result.entries:
        if not result.magic_ok:
            raise ValueError("no locres entries found (not a .locres file?)")
        raise ValueError(f"locres version {result.version} not yet supported")
    return result


def _read_string_tables(data: bytes, pos: int):
    table_count, pos = _u32_value(data, pos)
    for _ in range(table_count):
        namespace, pos = _read_fstring(data, pos)
        entry_count, pos = _u32_value(data, pos)
        entries: list[tuple[str, str]] = []
        for _ in range(entry_count):
            key, pos = _read_fstring(data, pos)
            value, pos = _read_fstring(data, pos)
            entries.append((key, value))
        yield namespace, entries


def _read_fstring(data: bytes, pos: int) -> tuple[str, int]:
    length, pos = _i32_value(data, pos)
    if length == 0:
        return "", pos
    if length < 0:
        byte_count = -length * 2
        raw = data[pos : pos + byte_count]
        pos += byte_count
        if raw.endswith(b"\x00\x00"):
            raw = raw[:-2]
        text = raw.decode("utf-16-le", "replace")
        return text, pos
    raw = data[pos : pos + length]
    pos += length
    if raw.endswith(b"\x00"):
        raw = raw[:-1]
    try:
        return raw.decode("utf-8"), pos
    except UnicodeDecodeError:
        return raw.decode("latin-1"), pos


def _u32(data: bytes, pos: int) -> int:
    return int.from_bytes(data[pos : pos + 4], "little", signed=False)


def _u32_value(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise ValueError("locres data truncated")
    return _u32(data, pos), pos + 4


def _i32_value(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise ValueError("locres data truncated")
    return int.from_bytes(data[pos : pos + 4], "little", signed=True), pos + 4


def _csv_field(value: str) -> str:
    value = value.replace('"', '""')
    if "," in value or '"' in value or "\n" in value:
        return f'"{value}"'
    return value


def parse_locres_file(path: str) -> LocresFile:
    return parse_locres(Path(path).read_bytes())


def apply_replacements(
    entries: list[LocresEntry],
    replacements: dict[str, str],
) -> list[LocresEntry]:
    """Return new entries with ``qualified_key`` -> new value applied."""
    out = [LocresEntry(namespace=e.namespace, key=e.key, source=e.source, value=e.value) for e in entries]
    for entry in out:
        new_value = replacements.get(entry.qualified_key)
        if new_value is not None:
            entry.value = new_value
    return out


def encode_locres(entries: list[LocresEntry], version: int = 3) -> bytes:
    """Serialize entries back to the .locres binary layout.

    ``version`` selects which magic header to write: 2 = legacy, 3 = optimized
    (both share the same body). Version 1 (compact) packs a key->value map
    without re-emitting the source strings and is *not* produced here to avoid
    guessing ownership, so callers writing files they can re-read use 2 or 3.

    Strings are encoded as UTF-16LE FStrings (negative length) exactly like the
    Unreal engine writes them, so the file stays readable by CUE4Parse/FModel.
    """
    if version not in (2, 3):
        raise ValueError("encode_locres supports only versions 2 (legacy) and 3 (optimized)")

    def fstring(text: str) -> bytes:
        if not text:
            return struct.pack("<i", 0)
        encoded = text.encode("utf-16-le") + b"\x00\x00"  # includes NUL terminator
        length = -(len(text) + 1)
        return struct.pack("<i", length) + encoded

    tables: OrderedDict[str, list[LocresEntry]] = OrderedDict()
    for entry in entries:
        tables.setdefault(entry.namespace, []).append(entry)

    body = struct.pack("<I", len(tables))
    for namespace, table_entries in tables.items():
        body += fstring(namespace)
        body += struct.pack("<I", len(table_entries))
        for entry in table_entries:
            body += fstring(entry.key)
            body += fstring(entry.value)

    header = struct.pack("<I", MAGIC) + bytes([version])
    return header + body


def save_locres(path: str, entries: list[LocresEntry], version: int = 3) -> int:
    """Write entries to ``path`` as a .locres file; returns entry count."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(encode_locres(entries, version=version))
    return len(entries)


__all__ = [
    "LocresEntry",
    "LocresFile",
    "MAGIC",
    "VERSION_NAMES",
    "apply_replacements",
    "encode_locres",
    "parse_locres",
    "parse_locres_file",
    "save_locres",
]