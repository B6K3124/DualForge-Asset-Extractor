"""Fabricate synthetic REDengine 4 CR2W containers for tests.

The builder reproduces the documented layout (header, ten table
descriptors, string dict, name/import/export/buffer info, then chunk and
buffer data).  Tests use it to build bytes that :mod:`dualforge.cdpr.cr2w`
parses back, matching the writer's own conventions (variable ``size``
fields count the 4-byte ``size`` field itself, chunk bodies start with
``0x00`` and end with a ``None`` variable).
"""

from __future__ import annotations

import struct
import zlib

CR2W_MAGIC = b"CR2W"


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def pack_variable(name_ordinal: int, type_ordinal: int, payload: bytes) -> bytes:
    """Serialize one (name, type, payload) variable pair."""
    return struct.pack("<HHI", name_ordinal, type_ordinal, len(payload) + 4) + payload


def pack_class_stream(
    fields,
    name_ord,
    type_ord,
    terminator_type: int = 0,
    trailing: bytes = b"",
) -> bytes:
    """Serialize a chunk/struct body: ``0x00`` + fields + ``None`` terminator.

    ``fields`` is an iterable of ``(field_name, field_type, payload)``
    tuples; payloads are pre-packed value bytes.  ``trailing`` bytes are
    appended after the ``None`` terminator (used for ``IRedAppendix``
    classes such as ``animRig`` whose extra data follows the variables).
    """
    body = b"\x00"
    for var_name, var_type, payload in fields:
        body += pack_variable(name_ord[var_name], type_ord[var_type], payload)
    body += struct.pack("<HHI", name_ord["None"], terminator_type, 4)
    body += trailing
    return body


def pack_array(*items: bytes) -> bytes:
    """Serialize a red array/static: u32 element count + packed elements."""
    return struct.pack("<I", len(items)) + b"".join(items)


def pack_data_buffer(buffer_index: int) -> bytes:
    """Encode a container-buffer index as a ``DataBuffer`` pointer payload."""
    return struct.pack("<I", (buffer_index | 0x80000000) + 1)


def build_cr2w(
    names: list[str],
    chunks,
    buffers=(),
    imports=(),
) -> bytes:
    """Assemble a full CR2W container.

    ``names``: order matters; every string referenced by the container
    (field names, type names, class names, enum member names, plus
    ``"None"``) must appear exactly once.

    ``chunks``: sequence of ``(class_name, chunk_stream_bytes)``.

    ``buffers``: sequence of raw buffer payloads.

    ``imports``: sequence of ``(depot_path, class_name)`` entries.
    """
    names = list(names)
    for depot, _class in imports:
        if depot not in names:
            names.append(depot)
    name_ord = {name: i for i, name in enumerate(names)}

    string_region = b"".join(name.encode("utf-8") + b"\x00" for name in names)
    string_offsets: dict[str, int] = {}
    pos = 0
    for name in names:
        string_offsets[name] = pos
        pos += len(name.encode("utf-8")) + 1

    name_info = b"".join(struct.pack("<II", string_offsets[name], _crc32(name.encode("utf-8"))) for name in names)

    import_info = b"".join(
        struct.pack("<IHH", string_offsets[depot], name_ord[class_name], 0) for depot, class_name in imports
    )

    export_parts: list[bytes] = []
    cursor_rel = 0
    for class_name, stream in chunks:
        export_parts.append(
            struct.pack(
                "<HHIIIII",
                name_ord[class_name],
                0,
                0xFFFFFFFF,
                len(stream),
                cursor_rel,
                0,
                _crc32(stream),
            )
        )
        cursor_rel += len(stream)
    export_info = b"".join(export_parts)

    buffer_region = b"".join(buffers)
    buffer_parts: list[bytes] = []
    cursor_rel = 0
    for i, raw in enumerate(buffers):
        buffer_parts.append(struct.pack("<IIIIII", 0, i, cursor_rel, len(raw), len(raw), _crc32(raw)))
        cursor_rel += len(raw)
    buffer_info = b"".join(buffer_parts)

    base = 4 + 36 + 10 * 12
    t0_off = base
    t1_off = t0_off + len(string_region)
    t2_off = t1_off + len(name_info)
    t4_off = t2_off + len(import_info)
    t5_off = t4_off + len(export_info)
    objects_end = t5_off + len(buffer_info)
    buffers_end = objects_end + len(b"".join(stream for _, stream in chunks))

    tables = [
        (t0_off, len(string_region), _crc32(string_region)),
        (t1_off, len(names), _crc32(name_info)),
        (t2_off, len(imports), _crc32(import_info)),
        (0, 0, 0),
        (t4_off, len(chunks), _crc32(export_info)),
        (t5_off, len(buffers), _crc32(buffer_info)),
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
        (0, 0, 0),
    ]
    table_blob = b"".join(struct.pack("<III", *t) for t in tables)
    header = struct.pack(
        "<IIQIIIII",
        195,
        0,
        0,
        0,
        objects_end,
        buffers_end,
        0,
        len(chunks),
    )
    return (
        CR2W_MAGIC
        + header
        + table_blob
        + string_region
        + name_info
        + import_info
        + export_info
        + buffer_info
        + b"".join(stream for _, stream in chunks)
        + buffer_region
    )
