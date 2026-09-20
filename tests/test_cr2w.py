"""Tests for the REDengine 4 CR2W container parser and chunk decoder."""

from __future__ import annotations

import struct

import pytest

from dualforge.cdpr.cr2w import (
    Cr2wError,
    decode_variables,
    interpret_value,
    load_cr2w,
    parse_cr2w,
)
from util_cr2w import build_cr2w, pack_class_stream

NAMES = [
    "None",
    "Float",
    "Uint32",
    "CName",
    "String",
    "Bool",
    "Uint16",
    "Uint8",
    "Int32",
    "Double",
    "MyClass",
    "SomeName",
    "myFloat",
    "myInt",
    "myName",
    "myString",
    "flag",
]
NORD = {name: i for i, name in enumerate(NAMES)}


def _stream(*fields) -> bytes:
    return pack_class_stream(fields, NORD, NORD, terminator_type=NORD["CName"])


def _basic() -> bytes:
    fields = [
        ("myFloat", "Float", struct.pack("<f", 1.5)),
        ("myInt", "Uint32", struct.pack("<I", 7)),
        ("myName", "CName", struct.pack("<H", NORD["SomeName"])),
        ("myString", "String", b"\x04" + "hi".encode("utf-16-le")),
        ("flag", "Bool", b"\x01"),
    ]
    return build_cr2w(NAMES, [("MyClass", _stream(*fields))])


def test_parse_basic() -> None:
    cr2w = parse_cr2w(_basic())
    assert cr2w.version == 195
    assert cr2w.names == NAMES
    assert cr2w.path == ""
    root = cr2w.root
    assert root is not None
    assert root.index == 0
    assert root.type_name == "MyClass"
    assert cr2w.buffers == []
    assert cr2w.imports == []


def test_decode_variables_roundtrip() -> None:
    cr2w = parse_cr2w(_basic())
    root = cr2w.root
    assert root is not None
    variables = decode_variables(root.data, cr2w.names)
    assert [v.name for v in variables] == [
        "myFloat",
        "myInt",
        "myName",
        "myString",
        "flag",
    ]
    assert [v.type_name for v in variables[:3]] == ["Float", "Uint32", "CName"]
    assert interpret_value("Float", variables[0].value) == pytest.approx(1.5)
    assert interpret_value("Uint32", variables[1].value) == 7
    assert interpret_value("CName", variables[2].value) == NORD["SomeName"]
    assert interpret_value("String", variables[3].value) == "hi"
    assert interpret_value("Bool", variables[4].value) is True
    assert variables[-1].size == 1


def test_empty_chunk_has_no_variables() -> None:
    data = build_cr2w(NAMES, [("MyClass", _stream())])
    cr2w = parse_cr2w(data)
    assert decode_variables(cr2w.chunks[0].data, cr2w.names) == []


def test_interpret_primitives() -> None:
    assert interpret_value("Int8", b"\xff") == -1
    assert interpret_value("Uint8", b"\xff") == 255
    assert interpret_value("Int16", struct.pack("<h", -321)) == -321
    assert interpret_value("Uint16", struct.pack("<H", 65000)) == 65000
    assert interpret_value("Int32", struct.pack("<i", -1234)) == -1234
    assert interpret_value("Uint32", struct.pack("<I", 2**32 - 1)) == 2**32 - 1
    assert interpret_value("Int64", struct.pack("<q", -(2**40))) == -(2**40)
    assert interpret_value("Uint64", struct.pack("<Q", 2**40)) == 2**40
    assert interpret_value("Float", struct.pack("<f", -0.25)) == pytest.approx(-0.25)
    assert interpret_value("Double", struct.pack("<d", 2.5)) == 2.5
    assert interpret_value("CName", struct.pack("<H", 3)) == 3
    assert interpret_value("Enum", struct.pack("<I", 9)) == 9
    assert interpret_value("CHandle:rendRenderTextureBlobPC", struct.pack("<I", 4)) == 3
    assert interpret_value("CHandle:x", struct.pack("<I", 0)) == -1


def test_interpret_strings() -> None:
    assert interpret_value("String", b"\x04" + "hi".encode("utf-16-le")) == "hi"
    assert interpret_value("String", b"\x82hi") == "hi"
    text = "x" * 100
    payload = text.encode("utf-16-le")
    low6 = len(payload) & 0x3F
    head = bytes([low6 | 0x40, len(payload) >> 6])
    assert interpret_value("String", head + payload) == text
    assert interpret_value("String", b"\x00") == ""


def test_interpret_opaque_and_refs() -> None:
    assert interpret_value("raRef:IRenderResourceBlob", (123).to_bytes(8, "little")) == 123
    assert interpret_value("NodeRef", b"\x07\x00\x00\x00\x00\x00\x00\x00") == 7
    assert interpret_value("Bool", b"\x00") is False
    buf = b"\x01\x02"
    assert interpret_value("serializationDeferredDataBuffer", buf) == buf
    assert interpret_value("NotARealType", b"\x05") == b"\x05"


def test_imports() -> None:
    data = build_cr2w(
        NAMES, [("MyClass", _stream())], imports=[("base\\foo.bin", "MyClass")]
    )
    cr2w = parse_cr2w(data)
    assert len(cr2w.imports) == 1
    assert cr2w.imports[0].depot_path == "base\\foo.bin"
    assert cr2w.imports[0].class_name == "MyClass"


def test_multiple_chunks_and_buffers() -> None:
    raw = bytes(range(256))
    data = build_cr2w(
        NAMES,
        [("MyClass", _stream()), ("MyClass", _stream())],
        buffers=[raw],
    )
    cr2w = parse_cr2w(data)
    assert [c.index for c in cr2w.chunks] == [0, 1]
    assert len(cr2w.buffers) == 1
    assert cr2w.buffers[0].data == raw
    root = cr2w.root
    assert root is not None
    assert root.type_name == "MyClass"
    assert cr2w.chunks[1].type_name == "MyClass"
    assert cr2w.chunks[0].data == cr2w.chunks[1].data


def test_bad_magic() -> None:
    with pytest.raises(Cr2wError):
        parse_cr2w(b"XXXX" + b"\x00" * 100)


def test_truncated_header() -> None:
    with pytest.raises(Cr2wError):
        parse_cr2w(b"CR2W" + b"\x00" * 8)


def test_string_region_crc_mismatch() -> None:
    data = bytearray(_basic())
    data[4 + 36 + 10 * 12] ^= 0x01
    with pytest.raises(Cr2wError):
        parse_cr2w(bytes(data))


def test_kark_buffer_raises_without_oodle() -> None:
    raw = b"KARK" + struct.pack("<I", 10) + b"\x00\x01\x02"
    data = build_cr2w(NAMES, [("MyClass", _stream())], buffers=[raw])
    with pytest.raises(Cr2wError):
        parse_cr2w(data)


def test_load_cr2w(tmp_path) -> None:
    path = tmp_path / "x.cr2w"
    path.write_bytes(_basic())
    cr2w = load_cr2w(path)
    assert cr2w.path == str(path)
    assert cr2w.root is not None