"""Shared REDengine 4 schema-walk helpers for resource decoders.

:mod:`dualforge.cdpr.xbm` hand-codes its ``CBitmapTexture`` walk; mesh and
rig resources share a generic decoder that knows how to serialize every
red-class value a cooked CP77 asset stores: primitives, enum ordinals,
``CName`` string ordinals, handles, resource references, ``DataBuffer`` /
``serializationDeferredDataBuffer`` container pointers, fixed-size arrays of
primitives and class-stream arrays of structs.  Decoders supply ``SCHEMAS``
(dict of ``type_name -> (field_name, red_type)`` pairs, mirroring the XBM
module's ``_STRUCT_SCHEMAS``).
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO

from dualforge.cdpr.cr2w import (
    Cr2wError,
    Cr2wFile,
    split_class_stream,
)
from dualforge.cdpr.xbm import enum_member

SCHEMA_TYPE = tuple[tuple[str, str], ...]

_PRIMITIVES = {
    "Bool",
    "Boolean",
    "Int8",
    "Uint8",
    "Int16",
    "Uint16",
    "Int32",
    "Uint32",
    "Int64",
    "Uint64",
    "Float",
    "Double",
    "CName",
    "String",
}

# Fixed in-memory size of one array element for the scalar red types; structs
# and unknown composites serialize as 0x00-prefixed class streams instead.
_ARRAY_ELEMENT_SIZE = {
    "Bool": 1,
    "Boolean": 1,
    "Int8": 1,
    "Uint8": 1,
    "Int16": 2,
    "Uint16": 2,
    "CName": 2,
    "Int32": 4,
    "Uint32": 4,
    "Int64": 8,
    "Uint64": 8,
    "Float": 4,
    "Double": 8,
    "Enum": 4,
    "CHandle": 4,
}

# DataBuffer container pointer: 0x80000000 marks an empty buffer, larger
# values encode a container-buffer index as (value ^ 0x80000000) - 1.
DATABUFFER_EMPTY = 0x80000000


class Red4Error(Cr2wError):
    """Raised when a REDengine 4 resource cannot be decoded."""


@dataclass
class RedDataBuffer:
    """A ``DataBuffer`` variable: points at a container buffer, or is empty."""

    buffer_index: int
    raw: bytes = b""

    @property
    def is_empty(self) -> bool:
        return self.buffer_index < 0


@dataclass
class RedDeferredBuffer:
    """A ``serializationDeferredDataBuffer`` (flag + container-buffer index)."""

    flags: int
    buffer_index: int


class Red4Decoder:
    """Walk a red-class schema over decoded chunk variables.

    ``schemas`` maps red type names to ``(field_name, red_type)`` tuples;
    unknown types are passed through as the raw payload bytes.
    """

    def __init__(self, cr2w: Cr2wFile, schemas: dict[str, SCHEMA_TYPE]):
        self.cr2w = cr2w
        self.schemas = schemas

    @property
    def names(self) -> list[str]:
        return self.cr2w.names

    def decode_struct(self, type_name: str, data: bytes) -> dict[str, object]:
        schema = self.schemas.get(type_name)
        if schema is None:
            raise Red4Error(f"no schema for struct {type_name!r}")
        variables, _ = split_class_stream(data, self.names)
        by_name = {v.name: v for v in variables}
        result: dict[str, object] = {}
        for field_name, field_type in schema:
            variable = by_name.get(field_name)
            if variable is None:
                continue
            result[field_name] = self.decode_value(field_type, variable.value)
        return result

    def decode_chunk(self, chunk_type: str, chunk) -> dict[str, object]:
        """Decode a CR2W chunk by its declared schema."""
        return self.decode_struct(chunk_type, chunk.data)

    def decode_value(self, type_name: str, value: bytes) -> object:
        if type_name in _PRIMITIVES:
            return self._decode_primitive(type_name, value)
        if type_name.startswith("Enum"):
            return self.enum_member(self._ordinal(value))
        if type_name.startswith("CHandle"):
            return self._handle(value)
        if type_name == "DataBuffer":
            return self.data_buffer(value)
        if type_name == "serializationDeferredDataBuffer":
            return self.deferred_buffer(value)
        if type_name.startswith("raRef") or type_name.startswith("NodeRef"):
            return int.from_bytes(value, "little")
        if type_name.startswith(("array:", "static:")):
            return self.decode_array(type_name.split(":", 1)[1], value)
        if type_name in self.schemas:
            return self.decode_struct(type_name, value)
        return value

    def _decode_primitive(self, type_name: str, value: bytes) -> object:
        if type_name == "CName":
            ordinal = struct.unpack("<H", value)[0] if len(value) >= 2 else 0
            return self.name_member(ordinal)
        if type_name in ("String",):
            from dualforge.cdpr.cr2w import _decode_red_string

            return _decode_red_string(value)
        if type_name in ("Bool", "Boolean"):
            return bool(value and value[0])
        if type_name in ("Int8", "Uint8"):
            return struct.unpack("b" if type_name.startswith("I") else "B", value)[0]
        if type_name in ("Int16", "Uint16"):
            return struct.unpack("<h" if type_name.startswith("I") else "<H", value)[0]
        if type_name in ("Int32", "Uint32"):
            return struct.unpack("<i" if type_name.startswith("I") else "<I", value)[0]
        if type_name in ("Int64", "Uint64"):
            return struct.unpack("<q" if type_name.startswith("I") else "<Q", value)[0]
        if type_name == "Float":
            return struct.unpack("<f", value)[0] if len(value) >= 4 else 0.0
        if type_name == "Double":
            return struct.unpack("<d", value)[0] if len(value) >= 8 else 0.0
        return value

    def _ordinal(self, value: bytes) -> int:
        return struct.unpack("<I", value)[0] if len(value) >= 4 else 0

    def _handle(self, value: bytes) -> int:
        """Decode a ``CHandle`` chunk pointer (0 = null, else index + 1)."""
        raw = struct.unpack("<I", value)[0] if len(value) >= 4 else 0
        return raw - 1 if raw > 0 else -1

    def name_member(self, ordinal: int) -> str:
        if 0 <= ordinal < len(self.names):
            return self.names[ordinal]
        return f"CName#{ordinal}"

    def enum_member(self, ordinal: int) -> str:
        if 0 <= ordinal < len(self.names):
            return enum_member(self.names[ordinal])
        return f"Enum#{ordinal}"

    def data_buffer(self, value: bytes) -> RedDataBuffer:
        """Decode a ``DataBuffer``: empty or a container-buffer index."""
        raw = struct.unpack("<I", value[:4])[0] if len(value) >= 4 else DATABUFFER_EMPTY
        if raw == DATABUFFER_EMPTY:
            return RedDataBuffer(-1)
        if raw > DATABUFFER_EMPTY:
            index = (raw ^ DATABUFFER_EMPTY) - 1
            return RedDataBuffer(index)
        return RedDataBuffer(-1)

    def deferred_buffer(self, value: bytes) -> RedDeferredBuffer:
        if len(value) >= 2:
            return RedDeferredBuffer(flags=value[0], buffer_index=value[1])
        return RedDeferredBuffer(flags=0, buffer_index=0)

    def decode_array(self, element_type: str, value: bytes) -> list[object]:
        if len(value) < 4:
            return []
        count = struct.unpack("<I", value[:4])[0]
        stream = BytesIO(value[4:])
        items: list[object] = []
        for _ in range(count):
            size = self._element_size(element_type)
            if size is not None:
                raw = stream.read(size)
                if len(raw) < size:
                    break
                items.append(self.decode_value(element_type, raw))
            else:
                entry = self._read_one_class(stream)
                if entry is None:
                    break
                items.append(self.decode_value(element_type, entry))
        return items

    def _element_size(self, element_type: str) -> int | None:
        """Fixed element size for scalar array elements, else ``None``."""
        if element_type in _ARRAY_ELEMENT_SIZE:
            return _ARRAY_ELEMENT_SIZE[element_type]
        if element_type.startswith("Enum"):
            return _ARRAY_ELEMENT_SIZE["Enum"]
        if element_type.startswith("CHandle"):
            return _ARRAY_ELEMENT_SIZE["CHandle"]
        if element_type.startswith("array:") or element_type.startswith("static:"):
            return 4
        return None

    def _read_one_class(self, stream: BytesIO) -> bytes | None:
        """Read a single 0x00-prefixed class stream from *stream*."""
        marker = stream.read(1)
        if marker != b"\x00":
            return None
        slice_ = bytearray(b"\x00")
        while True:
            head = stream.read(8)
            if len(head) < 8:
                return None
            slice_ += head
            name_ordinal, _type_ordinal, size = struct.unpack("<HHI", head)
            payload_len = size - 4 if size >= 4 else 0
            payload = stream.read(payload_len)
            if len(payload) < payload_len:
                return None
            slice_ += payload
            if name_ordinal < len(self.names) and self.names[name_ordinal] == "None":
                break
        return bytes(slice_)


def to_red_data_buffer_bytes(buffer_index: int) -> bytes:
    """Encode a container-buffer index as a ``DataBuffer`` payload."""
    return struct.pack("<I", (buffer_index | DATABUFFER_EMPTY) + 1)


def container_bytes(decoder: Red4Decoder, cr2w: Cr2wFile, db, minimum: int) -> bytes:
    """Return the raw bytes behind a ``DataBuffer``/deferred pointer."""
    if isinstance(db, (RedDataBuffer, RedDeferredBuffer)):
        index = db.buffer_index
    else:
        raise Red4Error("expected a buffer pointer variable")
    if index < 0 or index >= len(cr2w.buffers):
        raise Red4Error(f"buffer index {index} out of range ({len(cr2w.buffers)})")
    data = cr2w.buffers[index].data
    if len(data) < minimum:
        raise Red4Error(f"buffer {index} too small ({len(data)} < {minimum} bytes)")
    return data


def hfconvert(bits: int) -> float:
    """Decode an IEEE-754 binary16 stored in a ushort (WolvenKit parity)."""
    return struct.unpack("<e", struct.pack("<H", bits & 0xFFFF))[0]


def ten_bit_shifted(bits: int) -> tuple[float, float, float, float]:
    """Decode a 32-bit packed 10/10/10/2 normal/tangent (WolvenKit parity).

    Returns ``(x, y, z, w)``; ``w`` collapses from the top two bits with the
    convention 0 -> 1 and 3 -> -1 (anything else -> 0).
    """
    dequant = 2.0 / 1023.0
    x = ((bits & 0x3FF) * dequant) - 1.0
    y = (((bits >> 10) & 0x3FF) * dequant) - 1.0
    z = (((bits >> 20) & 0x3FF) * dequant) - 1.0
    w = (bits >> 30) & 0x3
    w = {0: 1.0, 3: -1.0}.get(w, 0.0)
    return x, y, z, w


def mat4_inverse_row_major(rows: list[float]) -> list[float]:
    """Invert a row-major 4x4 matrix (no scale projection, like WolvenKit)."""
    m = [[rows[i * 4 + j] for j in range(4)] for i in range(4)]
    inv = _invert4(m)
    return [float(v) for row in inv for v in row]


def _invert4(m: list[list[float]]) -> list[list[float]]:
    a = [row[:] + [1.0 if c == r else 0.0 for c in range(4)] for r, row in enumerate(m)]
    for col in range(4):
        pivot = next((r for r in range(col, 4) if abs(a[r][col]) > 1e-12), None)
        if pivot is None:
            raise ValueError("matrix is singular")
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        a[col] = [v / scale for v in a[col]]
        for r in range(4):
            if r == col:
                continue
            factor = a[r][col]
            if factor:
                a[r] = [a[r][c] - factor * a[col][c] for c in range(8)]
    return [row[4:] for row in a]


def mat4_identity() -> list[float]:
    return [1.0 if r == c else 0.0 for c in range(4) for r in range(4)]


def mat4_translate(x: float, y: float, z: float) -> list[float]:
    tx, ty, tz = x, y, z
    rows = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [tx, ty, tz, 1.0],
    ]
    return [float(v) for row in rows for v in row]


def mat4_multiply(a: Sequence[float], b: Sequence[float]) -> list[float]:
    """Row-major 4x4 multiplication ``a @ b``."""
    A = [[a[i * 4 + j] for j in range(4)] for i in range(4)]
    B = [[b[i * 4 + j] for j in range(4)] for i in range(4)]
    out = [[sum(A[r][k] * B[k][c] for k in range(4)) for c in range(4)] for r in range(4)]
    return [float(v) for row in out for v in row]


def mat4_from_trs(
    translation: tuple[float, float, float],
    quaternion: tuple[float, float, float, float],
    scale: tuple[float, float, float],
) -> list[float]:
    """Row-major TRS matrix from a red ``QsTransform`` (quat is w-last)."""
    qi, qj, qk, qr = quaternion
    n = qr * qr + qi * qi + qj * qj + qk * qk
    if n > 0.0:
        qr, qi, qj, qk = qr / n, qi / n, qj / n, qk / n
    sx, sy, sz = scale
    row0 = [(1 - 2 * (qj * qj + qk * qk)) * sx, 2 * (qi * qj - qk * qr) * sy, 2 * (qi * qk + qj * qr) * sz, 0.0]
    row1 = [2 * (qi * qj + qk * qr) * sx, (1 - 2 * (qi * qi + qk * qk)) * sy, 2 * (qj * qk - qi * qr) * sz, 0.0]
    row2 = [2 * (qi * qk - qj * qr) * sx, 2 * (qj * qk + qi * qr) * sy, (1 - 2 * (qi * qi + qj * qj)) * sz, 0.0]
    row3 = [translation[0], translation[1], translation[2], 1.0]
    return [float(v) for row in (row0, row1, row2, row3) for v in row]


__all__ = [
    "DATABUFFER_EMPTY",
    "Red4Decoder",
    "Red4Error",
    "RedDataBuffer",
    "RedDeferredBuffer",
    "SCHEMA_TYPE",
    "container_bytes",
    "hfconvert",
    "mat4_from_trs",
    "mat4_identity",
    "mat4_inverse_row_major",
    "mat4_multiply",
    "mat4_translate",
    "ten_bit_shifted",
    "to_red_data_buffer_bytes",
]
