"""REDengine 4 ``.xbm`` texture decoder (CBitmapTexture).

A cooked ``.xbm`` is a CR2W container whose root chunk is a
``CBitmapTexture``.  Its ``renderTextureResource`` references a
``rendRenderTextureBlobPC`` chunk by handle; that blob's ``texture_data``
(``serializationDeferredDataBuffer``) names a container buffer holding the
packed pixels.  This module walks those red-class schemas and produces
metadata plus, for decodable block formats, a full RGBA image.

Compression is named by the file's own CName table (member names such as
``TCM_QualityColor``).  Supported payloads reuse the pure-Python BCn
decoders in :mod:`dualforge.export.texture_decode` (BC1/BC3/BC4/BC5/BC7)
and a straight-through RGBA8 path for ``TCM_None``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from io import BytesIO

import numpy as np

from dualforge.cdpr.cr2w import (
    Cr2wError,
    Cr2wFile,
    decode_variables,
    interpret_value,
    parse_cr2w,
)

XBM_COMPRESSION_BLOCKS: dict[str, str] = {
    "TCM_DXTNoAlpha": "bc1",
    "TCM_DXTAlpha": "bc3",
    "TCM_QualityR": "bc4",
    "TCM_Normals": "bc5",
    "TCM_NormalsHigh": "bc5",
    "TCM_NormalsGloss": "bc5",
    "TCM_QualityRG": "bc5",
    "TCM_QualityColor": "bc7",
    "TCM_QualityNormals": "bc7",
}

_RAW_COMPRESSIONS = {"TCM_None"}


class XbmError(Cr2wError):
    """Raised when an ``.xbm`` container is structurally unexpected."""


@dataclass
class _DeferredBuffer:
    flags: int
    buffer_index: int


@dataclass
class XbmTexture:
    width: int
    height: int
    depth: int
    compression: str
    raw_format: str
    block_format: str | None
    raw_bpp: int | None
    payload: bytes
    mip_count: int
    slice_size: int
    meta: dict[str, str]

    def rgba_image(self) -> np.ndarray | None:
        """Decode the mip-0 pixel data to an ``(H, W, 4)`` uint8 array."""
        if self.width <= 0 or self.height <= 0:
            return None
        if self.block_format:
            try:
                from dualforge.export.texture_decode import _decode_blocks

                return _decode_blocks(
                    self.block_format, self.payload, self.width, self.height
                )
            except Exception:
                return None
        if self.raw_bpp == 4 and len(self.payload) >= self.width * self.height * 4:
            count = self.width * self.height * 4
            pixels = np.frombuffer(self.payload, dtype=np.uint8, count=count)
            return pixels.reshape(self.height, self.width, 4)
        return None


_STRUCT_SCHEMAS: dict[str, tuple[tuple[str, str], ...]] = {
    "CBitmapTexture": (
        ("cookingPlatform", "Enum.ECookingPlatform"),
        ("width", "Uint32"),
        ("height", "Uint32"),
        ("depth", "Enum.ETextureDepth"),
        ("setup", "STextureGroupSetup"),
        ("histBiasMulCoef", "Vector3"),
        ("histBiasAddCoef", "Vector3"),
        ("renderResourceBlob", "raRef:IRenderResourceBlob"),
        ("renderTextureResource", "rendRenderTextureResource"),
    ),
    "STextureGroupSetup": (
        ("group", "Enum.GpuWrapApieTextureGroup"),
        ("rawFormat", "Enum.ETextureRawFormat"),
        ("compression", "Enum.ETextureCompression"),
        ("isStreamable", "Bool"),
        ("hasMipchain", "Bool"),
        ("isGamma", "Bool"),
        ("platformMipBiasPC", "Uint8"),
        ("platformMipBiasConsole", "Uint8"),
        ("allowTextureDowngrade", "Bool"),
    ),
    "Vector3": (("x", "Float"), ("y", "Float"), ("z", "Float")),
    "rendRenderTextureResource": (
        ("render_resource_blob_pc", "CHandle:rendRenderTextureBlobPC"),
    ),
    "rendRenderTextureBlobPC": (
        ("header", "rendRenderTextureBlobHeader"),
        ("texture_data", "serializationDeferredDataBuffer"),
    ),
    "rendRenderTextureBlobHeader": (
        ("version", "Uint32"),
        ("size_info", "rendRenderTextureBlobSizeInfo"),
        ("texture_info", "rendRenderTextureBlobTextureInfo"),
        ("flags", "Uint32"),
        ("mipMapInfo", "array:rendRenderTextureBlobMipMapInfo"),
        ("histogramData", "array:rendRenderTextureBlobHistogramData"),
    ),
    "rendRenderTextureBlobSizeInfo": (
        ("width", "Uint32"),
        ("height", "Uint32"),
        ("depth", "Uint32"),
    ),
    "rendRenderTextureBlobTextureInfo": (
        ("textureDataSize", "Uint32"),
        ("sliceSize", "Uint32"),
        ("dataAlignment", "Uint32"),
        ("sliceCount", "Uint32"),
        ("mipCount", "Uint32"),
        ("type", "Enum.GpuWrapApieTextureType"),
    ),
    "rendRenderTextureBlobMipMapInfo": (
        ("layout", "rendRenderTextureBlobLayout"),
        ("placement", "rendRenderTextureBlobPlacement"),
    ),
    "rendRenderTextureBlobLayout": (("rowPitch", "Uint32"), ("slicePitch", "Uint32")),
    "rendRenderTextureBlobPlacement": (("size", "Uint32"), ("offset", "Uint32")),
    "rendRenderTextureBlobHistogramData": (("value", "Uint32"),),
}

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


def enum_member(name: str) -> str:
    """Normalize an enum CName (``Enum.X.Y``) to its bare member name."""
    if name.startswith("Enum.") or name.startswith("enum."):
        name = name.split(".", 1)[-1]
    if "::" in name:
        name = name.rsplit("::", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class _SchemaDecoder:
    """Walk a red-class schema over decoded chunk variables."""

    def __init__(self, cr2w: Cr2wFile):
        self.cr2w = cr2w
        self.names = cr2w.names

    def decode_struct(self, type_name: str, data: bytes) -> dict[str, object]:
        schema = _STRUCT_SCHEMAS.get(type_name)
        if schema is None:
            raise XbmError(f"no schema for struct {type_name!r}")
        by_name = {v.name: v for v in decode_variables(data, self.names)}
        result: dict[str, object] = {}
        for field_name, field_type in schema:
            variable = by_name.get(field_name)
            if variable is None:
                continue
            result[field_name] = self._decode_value(field_type, variable.value)
        return result

    def _decode_value(self, type_name: str, value: bytes) -> object:
        if type_name in _PRIMITIVES:
            return interpret_value(type_name, value)
        if type_name.startswith("Enum"):
            return self._enum_member(_as_int(interpret_value(type_name, value)))
        if type_name.startswith("CHandle"):
            return interpret_value(type_name, value)
        if type_name == "serializationDeferredDataBuffer":
            if len(value) >= 2:
                return _DeferredBuffer(flags=value[0], buffer_index=value[1])
            return _DeferredBuffer(flags=0, buffer_index=0)
        if type_name.startswith("raRef") or type_name.startswith("NodeRef"):
            return interpret_value(type_name, value)
        if type_name.startswith("array:"):
            element_type = type_name.split(":", 1)[1]
            return self._decode_array(element_type, value)
        if type_name in _STRUCT_SCHEMAS:
            return self.decode_struct(type_name, value)
        return value

    def _enum_member(self, ordinal: int) -> str:
        if 0 <= ordinal < len(self.names):
            return enum_member(self.names[ordinal])
        return f"Enum#{ordinal}"

    def _decode_array(self, element_type: str, value: bytes) -> list[object]:
        if len(value) < 4:
            return []
        count = _as_int(struct.unpack("<I", value[:4])[0])
        stream = BytesIO(value[4:])
        items: list[object] = []
        for _ in range(count):
            entry = self._read_one_class(stream)
            if entry is None:
                break
            items.append(self._decode_value(element_type, entry))
        return items

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
            _name, _type, size = struct.unpack("<HHI", head)
            payload_len = size - 4 if size >= 4 else 0
            payload = stream.read(payload_len)
            if len(payload) < payload_len:
                return None
            slice_ += payload
            if _name < len(self.names) and self.names[_name] == "None":
                break
        return bytes(slice_)


def decode_xbm(data: bytes) -> XbmTexture:
    """Decode a cooked ``.xbm`` buffer into an :class:`XbmTexture`."""
    cr2w = parse_cr2w(data)
    root = cr2w.root
    if root is None:
        raise XbmError("CR2W container has no root chunk")
    if not root.type_name.endswith("CBitmapTexture"):
        raise XbmError(
            f"root chunk is {root.type_name!r}, expected CBitmapTexture"
        )
    decoder = _SchemaDecoder(cr2w)
    body = decoder.decode_struct("CBitmapTexture", root.data)
    return _assemble(cr2w, body)


def _assemble(cr2w: Cr2wFile, body: dict[str, object]) -> XbmTexture:
    width = _as_int(body.get("width"))
    height = _as_int(body.get("height"))
    depth = _as_int(body.get("depth"))
    setup = body.get("setup") or {}
    compression = str(setup.get("compression", "TCM_Unknown"))
    raw_format = str(setup.get("rawFormat", "TRF_Unknown"))

    resource = body.get("renderTextureResource") or {}
    handle = resource.get("render_resource_blob_pc", -1)
    if not isinstance(handle, int) or handle < 0:
        raise XbmError("xbm has no rendRenderTextureBlobPC handle")
    if handle >= len(cr2w.chunks):
        raise XbmError(
            f"xbm blob handle {handle} out of range ({len(cr2w.chunks)} chunks)"
        )
    blob_chunk = cr2w.chunks[handle]
    if not blob_chunk.type_name.endswith("rendRenderTextureBlobPC"):
        raise XbmError(
            f"blob chunk is {blob_chunk.type_name!r}, expected rendRenderTextureBlobPC"
        )

    decoder = _SchemaDecoder(cr2w)
    blob = decoder.decode_struct("rendRenderTextureBlobPC", blob_chunk.data)
    header = blob.get("header") or {}
    size_info = header.get("size_info") or {}
    texture_info = header.get("texture_info") or {}

    if not width:
        width = _as_int(size_info.get("width"))
    if not height:
        height = _as_int(size_info.get("height"))

    tex_data = blob.get("texture_data")
    buffer_index = (
        tex_data.buffer_index if isinstance(tex_data, _DeferredBuffer) else 0
    )
    if buffer_index < 0 or buffer_index >= len(cr2w.buffers):
        raise XbmError("xbm texture_data references a missing buffer")
    payload = cr2w.buffers[buffer_index].data

    tex_size = _as_int(texture_info.get("textureDataSize"))
    if tex_size and 0 < tex_size <= len(payload):
        payload = payload[:tex_size]

    mip_count = _as_int(texture_info.get("mipCount")) or 1
    slice_size = _as_int(texture_info.get("sliceSize"))
    slice_count = _as_int(texture_info.get("sliceCount")) or 1

    block_format = XBM_COMPRESSION_BLOCKS.get(compression)
    raw_bpp = 4 if compression in _RAW_COMPRESSIONS else None
    supported = block_format is not None or raw_bpp is not None

    meta = {
        "Engine": "REDengine",
        "Width": str(width),
        "Height": str(height),
        "Depth": str(depth or 1),
        "Compression": compression,
        "Raw format": raw_format,
        "Block format": block_format.upper() if block_format else "raw",
        "Mips": str(mip_count),
        "Slices": str(slice_count),
        "Buffer": str(buffer_index),
        "Decoded": "yes" if supported else "no",
    }
    return XbmTexture(
        width=width,
        height=height,
        depth=depth or 1,
        compression=compression,
        raw_format=raw_format,
        block_format=block_format,
        raw_bpp=raw_bpp,
        payload=payload,
        mip_count=mip_count,
        slice_size=slice_size,
        meta=meta,
    )


__all__ = [
    "XBM_COMPRESSION_BLOCKS",
    "XbmError",
    "XbmTexture",
    "decode_xbm",
    "enum_member",
]