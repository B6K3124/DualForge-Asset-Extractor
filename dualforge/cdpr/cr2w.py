"""REDengine 4 CR2W container and chunk-variable decoding (Cyberpunk 2077).

Cooked REDengine 4 assets (``.xbm``, ``.mesh``, ``.rig``, ``.w2anims``, ...)
are stored in the CR2W container format.  This module parses the binary
layout and decodes the serialized chunk streams; it does not interpret
domain data (texture pixels, mesh geometry, animation tracks), which lives
in :mod:`dualforge.cdpr.xbm` and friends.

Layout (REDengine 4, format 195)
--------------------------------
Header (40 bytes)::

    char[4]   magic               "CR2W"
    uint32    version             file format version (195 for Cyberpunk 2077)
    uint32    flags
    uint64    timeStamp           Windows FILETIME
    uint32    buildVersion        engine build id
    uint32    objectsEnd          chunk-data base offset
    uint32    buffersEnd          buffer-data base offset
    uint32    crc32               header checksum
    uint32    numChunks           total chunk count

Table descriptors (10 x 12 bytes), each ``(offset, itemCount, crc32)``::

    [0] stringDict   - a single blob of null-terminated strings
    [1] nameInfo     - (offset, hash); each offset indexes the string dict
    [2] importInfo   - (offset, className, flags); resolved DBR imports
    [3] propertyInfo - (type, parentType, size2, crc32, offset)
    [4] exportInfo   - (className, flags, parentId, dataSize, dataOffset,
                        template, crc32)
    [5] bufferInfo   - (flags, index, offset, diskSize, memSize, crc32)
    [6] embeddedInfo - (offset, pathIndex, importIndex)
    [7-9] unused

Chunk bodies sit at ``objectsEnd + export.dataOffset``.  RED4 chunk data is
a serialized class stream: a single ``0x00`` byte followed by variables
terminated by the ``None`` name.  Each variable is::

    uint16    nameOrdinal    index into the names table
    uint16    typeOrdinal    index into the names table
    uint32    size           payload length + 4
    payload   payload bytes  (length = size - 4)

Buffer data sits at ``buffersEnd + buffer.offset`` for ``diskSize`` bytes.
A ``KARK`` prefix marks a Kraken-compressed block whose decompressed size
is stored at offset 4; decompression uses the Oodle DLL, which is never
bundled (see the project policy in ``docs/LICENSES.md``).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

CR2W_MAGIC = b"CR2W"
KARK_MAGIC = b"KARK"

_HEADER = struct.Struct("<IIQIIIII")
_TABLE = struct.Struct("<III")
_NAME_INFO = struct.Struct("<II")
_IMPORT_INFO = struct.Struct("<IHH")
_PROPERTY_INFO = struct.Struct("<HHHHQ")
_EXPORT_INFO = struct.Struct("<HHIIIII")
_BUFFER_INFO = struct.Struct("<IIIIII")
_EMBEDDED_INFO = struct.Struct("<IIQ")

_HEADER_SIZE = 4 + _HEADER.size
_TABLE_COUNT = 10
_NONE = "None"


class Cr2wError(ValueError):
    """Raised when a CR2W container cannot be parsed."""


@dataclass
class Cr2wTable:
    offset: int
    count: int
    crc32: int


@dataclass
class Cr2wImport:
    depot_path: str
    class_name: str = ""
    flags: int = 0


@dataclass
class Cr2wChunk:
    index: int
    type_name: str
    class_name_index: int
    object_flags: int
    parent_id: int
    size: int
    offset: int
    template: int
    crc32: int
    data: bytes


@dataclass
class Cr2wBuffer:
    index: int
    flags: int
    offset: int
    disk_size: int
    mem_size: int
    crc32: int
    data: bytes


@dataclass
class Variable:
    name: str
    type_name: str
    size: int
    value: bytes


@dataclass
class Cr2wFile:
    path: str
    version: int
    flags: int
    time_stamp: int
    build_version: int
    objects_end: int
    buffers_end: int
    header_crc32: int
    names: list[str]
    imports: list[Cr2wImport]
    chunks: list[Cr2wChunk]
    buffers: list[Cr2wBuffer]

    @property
    def root(self) -> Cr2wChunk | None:
        """Conventional root chunk (the first export, usually the asset)."""
        return self.chunks[0] if self.chunks else None


def parse_cr2w(data: bytes, path: str = "") -> Cr2wFile:
    """Parse a CR2W container from raw bytes."""
    if len(data) < _HEADER_SIZE:
        raise Cr2wError(f"file too small for a CR2W header ({len(data)} bytes)")
    if data[:4] != CR2W_MAGIC:
        raise Cr2wError(f"not a REDengine 4 CR2W (magic: {data[:4]!r})")

    (
        version,
        flags,
        time_stamp,
        build_version,
        objects_end,
        buffers_end,
        header_crc,
        num_chunks,
    ) = _HEADER.unpack_from(data, 4)

    tables = [_read_table(data, i) for i in range(_TABLE_COUNT)]
    string_dict = _read_string_dict(data, tables[0])
    names = _read_names(data, tables[1], string_dict)
    imports = _read_imports(data, tables[2], string_dict, names)
    chunks = _read_exports(data, tables[4], names, objects_end)
    buffers = _read_buffers(data, tables[5], buffers_end)

    if num_chunks and len(chunks) != num_chunks:
        raise Cr2wError(f"chunk count mismatch: header says {num_chunks}, table has {len(chunks)}")

    return Cr2wFile(
        path=path,
        version=version,
        flags=flags,
        time_stamp=time_stamp,
        build_version=build_version,
        objects_end=objects_end,
        buffers_end=buffers_end,
        header_crc32=header_crc,
        names=names,
        imports=imports,
        chunks=chunks,
        buffers=buffers,
    )


def load_cr2w(path: str | Path) -> Cr2wFile:
    """Parse the CR2W container at *path*."""
    return parse_cr2w(Path(path).read_bytes(), str(path))


# ---------------------------------------------------------------------------
# table / string-dict / names / imports


def _table_bytes(data: bytes, table: Cr2wTable, entry_size: int) -> bytes:
    start = table.offset
    end = start + table.count * entry_size
    if start < 0 or end > len(data):
        raise Cr2wError(
            f"table out of bounds (offset={start}, count={table.count}, entry={entry_size}, file={len(data)})"
        )
    return data[start:end]


def _check_crc(table: Cr2wTable, entries: bytes) -> None:
    hashed = zlib.crc32(entries) & 0xFFFFFFFF
    if hashed != table.crc32:
        raise Cr2wError(
            f"table checksum mismatch (offset={table.offset}, expected=0x{table.crc32:08x}, got=0x{hashed:08x})"
        )


def _read_table(data: bytes, index: int) -> Cr2wTable:
    pos = _HEADER_SIZE + index * _TABLE.size
    if pos + _TABLE.size > len(data):
        raise Cr2wError(f"table header {index} out of bounds")
    offset, count, crc32 = _TABLE.unpack_from(data, pos)
    return Cr2wTable(offset=offset, count=count, crc32=crc32)


def _read_string_dict(data: bytes, table: Cr2wTable) -> dict[int, str]:
    """Decode the string blob into ``{byte offset: text}`` for non-empty text."""
    if table.count == 0:
        return {}
    region = _table_bytes(data, table, 1)[: table.count]
    _check_crc(table, region)
    strings: dict[int, str] = {}
    pos = 0
    for chunk in region.split(b"\x00"):
        if chunk:
            text = chunk.decode("utf-8", "replace")
            strings[pos] = text
            # duplicate text at 0 offsets is not expected; keep the first
        pos += len(chunk) + 1
    return strings


def _read_names(data: bytes, table: Cr2wTable, string_dict: dict[int, str]) -> list[str]:
    if table.count == 0:
        return []
    entries = _table_bytes(data, table, _NAME_INFO.size)
    _check_crc(table, entries)
    names: list[str] = []
    for i in range(table.count):
        offset, _hash = _NAME_INFO.unpack_from(entries, i * _NAME_INFO.size)
        if offset in string_dict:
            names.append(string_dict[offset])
        elif offset == 0:
            names.append(_NONE)
        else:
            names.append(f"#{offset}")
    return names


def _read_imports(
    data: bytes,
    table: Cr2wTable,
    string_dict: dict[int, str],
    names: list[str],
) -> list[Cr2wImport]:
    if table.count == 0:
        return []
    entries = _table_bytes(data, table, _IMPORT_INFO.size)
    _check_crc(table, entries)
    imports: list[Cr2wImport] = []
    for i in range(table.count):
        offset, class_idx, flags = _IMPORT_INFO.unpack_from(entries, i * _IMPORT_INFO.size)
        depot = string_dict.get(offset, f"#{offset}")
        class_name = names[class_idx] if class_idx < len(names) else ""
        imports.append(Cr2wImport(depot_path=depot, class_name=class_name, flags=flags))
    return imports


def _read_exports(
    data: bytes,
    table: Cr2wTable,
    names: list[str],
    objects_end: int,
) -> list[Cr2wChunk]:
    if table.count == 0:
        return []
    entries = _table_bytes(data, table, _EXPORT_INFO.size)
    _check_crc(table, entries)
    chunks: list[Cr2wChunk] = []
    for i in range(table.count):
        (
            class_idx,
            object_flags,
            parent_id,
            data_size,
            data_offset,
            template,
            crc32,
        ) = _EXPORT_INFO.unpack_from(entries, i * _EXPORT_INFO.size)
        start = objects_end + data_offset
        end = start + data_size
        if start < 0 or end > len(data):
            raise Cr2wError(f"chunk {i} data out of bounds (offset={start}, size={data_size}, file={len(data)})")
        type_name = names[class_idx] if class_idx < len(names) else f"#{class_idx}"
        chunks.append(
            Cr2wChunk(
                index=i,
                type_name=type_name,
                class_name_index=class_idx,
                object_flags=object_flags,
                parent_id=parent_id,
                size=data_size,
                offset=data_offset,
                template=template,
                crc32=crc32,
                data=data[start:end],
            )
        )
    return chunks


def _read_buffers(
    data: bytes,
    table: Cr2wTable,
    buffers_end: int,
) -> list[Cr2wBuffer]:
    if table.count == 0:
        return []
    entries = _table_bytes(data, table, _BUFFER_INFO.size)
    _check_crc(table, entries)
    buffers: list[Cr2wBuffer] = []
    for i in range(table.count):
        flags, index, offset, disk_size, mem_size, crc32 = _BUFFER_INFO.unpack_from(entries, i * _BUFFER_INFO.size)
        start = buffers_end + offset
        end = start + disk_size
        if start < 0 or end > len(data):
            raise Cr2wError(f"buffer {i} out of bounds (offset={start}, size={disk_size}, file={len(data)})")
        raw = data[start:end]
        buffers.append(
            Cr2wBuffer(
                index=i,
                flags=flags,
                offset=offset,
                disk_size=disk_size,
                mem_size=mem_size,
                crc32=crc32,
                data=_decompress_buffer(raw, mem_size),
            )
        )
    return buffers


def _decompress_buffer(raw: bytes, mem_size: int) -> bytes:
    """Decompress a ``KARK``-wrapped buffer, or return the raw bytes."""
    if raw[:4] != KARK_MAGIC:
        return raw
    if len(raw) < 8:
        raise Cr2wError("KARK buffer too small for a decompressed-size field")
    expected = struct.unpack_from("<I", raw, 4)[0]
    payload = raw[8:]
    if not payload:
        raise Cr2wError("KARK buffer has no compressed payload")
    try:
        from dualforge.compression.oodle import Oodle

        return Oodle().decompress(payload, expected or mem_size)
    except ImportError as exc:
        raise Cr2wError(
            "KRAK/Oodle decompression unavailable; set DUALFORGE_OODLE to oo2core_*.dll (never bundled with DualForge)"
        ) from exc
    except Exception as exc:
        raise Cr2wError(f"KARK decompression failed: {exc}") from exc


# ---------------------------------------------------------------------------
# chunk-variable decoding


def split_class_stream(data: bytes, names: list[str]):
    """Decode a RED4 class stream into ``(variables, trailing)``.

    The stream is a leading ``0x00`` followed by ``(name, type, size,
    payload)`` quadruples until a variable named ``None`` is found.  Both the
    leading marker and the trailing ``None`` terminator are consumed; any
    bytes after the terminator (the *appendix* used by ``IRedAppendix``
    classes such as ``animRig``) are returned untouched so callers can parse
    them with class-specific knowledge.
    """
    stream = BytesIO(data)
    if stream.read(1) != b"\x00":
        stream.seek(0)

    variables: list[Variable] = []
    while True:
        head = stream.read(8)
        if len(head) < 8:
            break
        name_ordinal, type_ordinal, size = struct.unpack("<HHI", head)
        payload_len = size - 4 if size >= 4 else 0
        payload = stream.read(payload_len)
        if len(payload) < payload_len:
            raise Cr2wError("truncated variable payload in chunk stream")
        name = names[name_ordinal] if name_ordinal < len(names) else f"#{name_ordinal}"
        if name == _NONE:
            break
        type_name = names[type_ordinal] if type_ordinal < len(names) else f"#{type_ordinal}"
        variables.append(Variable(name, type_name, payload_len, payload))
    return variables, stream.read()


def decode_variables(data: bytes, names: list[str]) -> list[Variable]:
    """Decode a RED4 serialized class stream into its variables.

    The stream is a leading ``0x00`` followed by ``(name, type, size,
    payload)`` quadruples until a variable named ``None`` is found.  The
    trailing ``None`` terminator is consumed but not returned.
    """
    variables, _ = split_class_stream(data, names)
    return variables


def interpret_value(red_type: str, value: bytes) -> object:
    """Interpret primitive/known variable payloads for *red_type*.

    Returns a Python scalar for primitives, the raw payload bytes for
    opaque types (buffers, resource references) and ``(flags, buffer)``
    styling for deferred data buffers so callers can follow buffer indexes.
    """
    if red_type in ("Bool", "Boolean"):
        return bool(value and value[0])
    if red_type in ("Int8", "Uint8"):
        return struct.unpack("b" if red_type.startswith("I") else "B", value)[0]
    if red_type in ("Int16", "Uint16"):
        return struct.unpack("<h" if red_type.startswith("I") else "<H", value)[0]
    if red_type in ("Int32", "Uint32"):
        return struct.unpack("<i" if red_type.startswith("I") else "<I", value)[0]
    if red_type in ("Int64", "Uint64"):
        return struct.unpack("<q" if red_type.startswith("I") else "<Q", value)[0]
    if red_type == "Float":
        return struct.unpack("<f", value)[0] if len(value) >= 4 else 0.0
    if red_type == "Double":
        return struct.unpack("<d", value)[0] if len(value) >= 8 else 0.0
    if red_type == "CName":
        return struct.unpack("<H", value)[0] if len(value) >= 2 else 0
    if red_type == "String":
        return _decode_red_string(value)
    if red_type.startswith("Enum"):
        return struct.unpack("<I", value)[0] if len(value) >= 4 else 0
    if red_type.startswith("CHandle"):
        index = struct.unpack("<I", value)[0] if len(value) >= 4 else -1
        return index - 1 if index > 0 else -1
    if red_type.startswith("raRef") or red_type.startswith("NodeRef") or red_type == "raRef":
        return int.from_bytes(value, "little")
    if red_type in ("serializationDeferredDataBuffer", "DataBuffer", "DeferredDataBuffer"):
        return value
    return value


def _decode_red_string(value: bytes) -> str:
    """Decode a REDengine sized string (VLQ length, UTF-8 or UTF-16)."""
    if not value:
        return ""
    stream = BytesIO(value)
    first = stream.read(1)
    if not first:
        return ""
    b0 = first[0]
    is_utf8 = bool(b0 & 0x80)
    length = b0 & 0x3F
    if b0 & 0x40:
        for shift in (6, 13, 20, 27):
            byte = stream.read(1)
            if not byte:
                break
            length |= (byte[0] & 0x7F) << shift
            if not (byte[0] & 0x80):
                break
    raw = stream.read(length)
    if is_utf8:
        return raw.decode("utf-8", "replace")
    return raw.decode("utf-16", "replace")


__all__ = [
    "CR2W_MAGIC",
    "KARK_MAGIC",
    "Cr2wBuffer",
    "Cr2wChunk",
    "Cr2wError",
    "Cr2wFile",
    "Cr2wImport",
    "Variable",
    "decode_variables",
    "interpret_value",
    "load_cr2w",
    "parse_cr2w",
    "split_class_stream",
]
