from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dualforge.il2cpp.metadata import (
    MAGIC,
    MetadataError,
    dump_strings,
    iter_string_literals,
    parse_metadata,
    string_text,
)


def _build_metadata(version: int = 31, literals: list[bytes] | None = None) -> bytes:
    """Synthesize a minimal but well-formed global-metadata.dat.

    Layout: 8-byte header + StringLiteral table + packed literal blob.
    Section pointers are file offsets, matching the real format.
    """
    literals = literals or [b"Hello", b"world", b"unity.games", b"\x00\xff"]
    entries = b"".join(struct.pack("<II", len(lit), sum(len(x) for x in literals[:i])) for i, lit in enumerate(literals))
    prefix = 8 + 32  # header + 4 section-pair rows below
    literal_off = prefix
    data_off = literal_off + len(entries)
    body = struct.pack(
        "<8I",
        literal_off, len(entries),  # stringLiteral (byte size, not count)
        data_off, len(b"".join(literals)),  # stringLiteralData
        0, 0,  # string
        0, 0,  # events
    )
    return struct.pack("<Ii", MAGIC, version) + body + entries + b"".join(literals)


def test_parse_header():
    data = _build_metadata()
    info = parse_metadata(data)
    assert info.version == 31
    assert info.string_literal_count == 4
    assert MAGIC == 0xFAB11BAF


def test_parse_bad_magic_raises():
    with pytest.raises(MetadataError):
        parse_metadata(b"\x00" * 32)


def test_parse_too_small_raises():
    with pytest.raises(MetadataError):
        parse_metadata(b"\x00")

def test_iter_string_literals_content():
    data = _build_metadata(literals=[b"alpha", b"beta gamma"])
    found = [string_text(raw) for _, raw in iter_string_literals(data)]
    assert found == ["alpha", "beta gamma"]


def test_iter_string_literals_unsupported_version():
    with pytest.raises(MetadataError):
        list(iter_string_literals(_build_metadata(version=2)))


def test_dump_strings_to_file(tmp_path: Path):
    data = _build_metadata(literals=[b"first", b"second"])
    target = tmp_path / "strings.txt"
    count, out = dump_strings(data, str(target))
    assert out == str(target)
    assert count == 2
    text = target.read_text(encoding="utf-8")
    assert "0x00000000 first" in text
    assert "0x00000001 second" in text


def test_dump_strings_stdout(capsys):
    data = _build_metadata(literals=[b"hello"])
    count, out = dump_strings(data)
    assert out is None
    assert count == 1
    captured = capsys.readouterr()
    assert "hello" in captured.out