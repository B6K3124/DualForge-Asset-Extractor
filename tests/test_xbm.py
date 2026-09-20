"""Tests for the REDengine 4 ``.xbm`` (CBitmapTexture) decoder."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from dualforge.cdpr.xbm import XbmError, decode_xbm, enum_member
from util_bc7 import flat_bc7_block
from util_cr2w import build_cr2w, pack_class_stream

NAMES = [
    "None",
    "Uint32",
    "Float",
    "Uint8",
    "Bool",
    "Enum.ECookingPlatform",
    "Enum.ETextureDepth",
    "Enum.GpuWrapApieTextureGroup",
    "Enum.ETextureRawFormat",
    "Enum.ETextureCompression",
    "Enum.GpuWrapApieTextureType",
    "CHandle:rendRenderTextureBlobPC",
    "serializationDeferredDataBuffer",
    "raRef:IRenderResourceBlob",
    "array:rendRenderTextureBlobMipMapInfo",
    "array:rendRenderTextureBlobHistogramData",
    "CBitmapTexture",
    "rendRenderTextureBlobPC",
    "STextureGroupSetup",
    "Vector3",
    "rendRenderTextureResource",
    "rendRenderTextureBlobHeader",
    "rendRenderTextureBlobSizeInfo",
    "rendRenderTextureBlobTextureInfo",
    "rendRenderTextureBlobMipMapInfo",
    "rendRenderTextureBlobHistogramData",
    "cookingPlatform",
    "width",
    "height",
    "depth",
    "setup",
    "histBiasMulCoef",
    "histBiasAddCoef",
    "renderResourceBlob",
    "renderTextureResource",
    "render_resource_blob_pc",
    "group",
    "rawFormat",
    "compression",
    "isStreamable",
    "hasMipchain",
    "isGamma",
    "platformMipBiasPC",
    "platformMipBiasConsole",
    "allowTextureDowngrade",
    "x",
    "y",
    "z",
    "header",
    "texture_data",
    "version",
    "size_info",
    "texture_info",
    "flags",
    "mipMapInfo",
    "histogramData",
    "textureDataSize",
    "sliceSize",
    "dataAlignment",
    "sliceCount",
    "mipCount",
    "type",
    "PC",
    "2D",
    "TEXG_Generic_Color",
    "TRF_R8G8B8A8",
    "TCM_DXTNoAlpha",
    "TCM_QualityColor",
    "TCM_None",
    "TEX_TYPE_2D",
]
NORD = {name: i for i, name in enumerate(NAMES)}


def _stream(*fields) -> bytes:
    return pack_class_stream(fields, NORD, NORD)


def _enum(member: str) -> bytes:
    return struct.pack("<I", NORD[member])


def _vec3(x: float, y: float, z: float) -> bytes:
    return _stream(
        ("x", "Float", struct.pack("<f", x)),
        ("y", "Float", struct.pack("<f", y)),
        ("z", "Float", struct.pack("<f", z)),
    )


def _setup(compression: str) -> bytes:
    return _stream(
        ("group", "Enum.GpuWrapApieTextureGroup", _enum("TEXG_Generic_Color")),
        ("rawFormat", "Enum.ETextureRawFormat", _enum("TRF_R8G8B8A8")),
        ("compression", "Enum.ETextureCompression", _enum(compression)),
        ("isStreamable", "Bool", b"\x00"),
        ("hasMipchain", "Bool", b"\x00"),
        ("isGamma", "Bool", b"\x00"),
        ("platformMipBiasPC", "Uint8", b"\x00"),
        ("platformMipBiasConsole", "Uint8", b"\x00"),
        ("allowTextureDowngrade", "Bool", b"\x00"),
    )


def _root(compression: str, blob_handle: int) -> bytes:
    return _stream(
        ("cookingPlatform", "Enum.ECookingPlatform", _enum("PC")),
        ("width", "Uint32", struct.pack("<I", 8)),
        ("height", "Uint32", struct.pack("<I", 8)),
        ("depth", "Enum.ETextureDepth", _enum("2D")),
        ("setup", "STextureGroupSetup", _setup(compression)),
        ("histBiasMulCoef", "Vector3", _vec3(0.5, 1.0, 2.0)),
        ("histBiasAddCoef", "Vector3", _vec3(0.0, 0.1, 0.2)),
        ("renderResourceBlob", "raRef:IRenderResourceBlob", (123).to_bytes(8, "little")),
        (
            "renderTextureResource",
            "rendRenderTextureResource",
            _stream(
                (
                    "render_resource_blob_pc",
                    "CHandle:rendRenderTextureBlobPC",
                    struct.pack("<I", blob_handle),
                )
            ),
        ),
    )


def _blob(texture_size: int) -> bytes:
    return pack_class_stream(
        [
            (
                "header",
                "rendRenderTextureBlobHeader",
                pack_class_stream(
                    [
                        ("version", "Uint32", struct.pack("<I", 48)),
                        (
                            "size_info",
                            "rendRenderTextureBlobSizeInfo",
                            pack_class_stream(
                                [
                                    ("width", "Uint32", struct.pack("<I", 8)),
                                    ("height", "Uint32", struct.pack("<I", 8)),
                                    ("depth", "Uint32", struct.pack("<I", 1)),
                                ],
                                NORD,
                                NORD,
                            ),
                        ),
                        (
                            "texture_info",
                            "rendRenderTextureBlobTextureInfo",
                            pack_class_stream(
                                [
                                    ("textureDataSize", "Uint32", struct.pack("<I", texture_size)),
                                    ("sliceSize", "Uint32", struct.pack("<I", texture_size)),
                                    ("dataAlignment", "Uint32", struct.pack("<I", 4)),
                                    ("sliceCount", "Uint32", struct.pack("<I", 1)),
                                    ("mipCount", "Uint32", struct.pack("<I", 1)),
                                    ("type", "Enum.GpuWrapApieTextureType", _enum("TEX_TYPE_2D")),
                                ],
                                NORD,
                                NORD,
                            ),
                        ),
                        ("flags", "Uint32", struct.pack("<I", 0)),
                        (
                            "mipMapInfo",
                            "array:rendRenderTextureBlobMipMapInfo",
                            b"\x00\x00\x00\x00",
                        ),
                        (
                            "histogramData",
                            "array:rendRenderTextureBlobHistogramData",
                            b"\x00\x00\x00\x00",
                        ),
                    ],
                    NORD,
                    NORD,
                ),
            ),
            ("texture_data", "serializationDeferredDataBuffer", b"\x01\x00"),
        ],
        NORD,
        NORD,
    )


def _bc1_8x8() -> bytes:
    block = struct.pack("<HH", 0xF800, 0x07E0) + b"\x00\x00\x00\x00"
    return block * 4


def _xbm(compression: str, texture_size: int, buffer_payload: bytes) -> bytes:
    return build_cr2w(
        NAMES,
        [
            ("CBitmapTexture", _root(compression, 2)),
            ("rendRenderTextureBlobPC", _blob(texture_size)),
        ],
        buffers=[buffer_payload],
    )


def test_decode_bc1_xbm() -> None:
    data = _xbm("TCM_DXTNoAlpha", 32, _bc1_8x8())
    xbm = decode_xbm(data)
    assert xbm.width == 8
    assert xbm.height == 8
    assert xbm.depth == 1
    assert xbm.compression == "TCM_DXTNoAlpha"
    assert xbm.block_format == "bc1"
    assert xbm.raw_format == "TRF_R8G8B8A8"
    assert xbm.mip_count == 1
    assert xbm.slice_size == 32
    assert xbm.meta["Decoded"] == "yes"
    assert xbm.meta["Buffer"] == "0"
    assert xbm.meta["Width"] == "8"

    image = xbm.rgba_image()
    assert image is not None
    assert image.shape == (8, 8, 4)
    assert image.dtype == np.uint8
    assert (image[..., 0] == 255).all()
    assert (image[..., 1] == 0).all()
    assert (image[..., 3] == 255).all()


def test_decode_bc7_quality_color() -> None:
    # TCM_QualityColor is BC7-backed; the rgba_image() path must decode it.
    payload = flat_bc7_block(0, 9, 5, 3) * 4  # 8x8, mode 0, all-index-0 block
    data = _xbm("TCM_QualityColor", len(payload), payload)
    xbm = decode_xbm(data)
    assert xbm.compression == "TCM_QualityColor"
    assert xbm.block_format == "bc7"
    assert xbm.meta["Decoded"] == "yes"
    assert xbm.meta["Block format"] == "BC7"

    image = xbm.rgba_image()
    assert image is not None
    assert image.shape == (8, 8, 4)
    assert image.dtype == np.uint8
    assert (image == [148, 82, 49, 255]).all()


def test_decode_raw_rgba8() -> None:
    raw = bytes(range(256))
    data = _xbm("TCM_None", 256, raw)
    xbm = decode_xbm(data)
    assert xbm.block_format is None
    assert xbm.raw_bpp == 4
    image = xbm.rgba_image()
    assert image is not None
    assert image.shape == (8, 8, 4)
    assert bytes(np.ascontiguousarray(image).reshape(-1)) == raw


def test_decode_bad_blob_handle() -> None:
    data = build_cr2w(NAMES, [("CBitmapTexture", _root("TCM_None", 5))])
    with pytest.raises(XbmError):
        decode_xbm(data)


def test_decode_wrong_root_type() -> None:
    data = build_cr2w(
        NAMES,
        [("rendRenderTextureBlobPC", _blob(0))],
        buffers=[b""],
    )
    with pytest.raises(XbmError):
        decode_xbm(data)


def test_enum_member() -> None:
    assert enum_member("Enum.ECookingPlatform.PC") == "PC"
    assert enum_member("TCM_None") == "TCM_None"
    assert enum_member("Enum.X::Y") == "Y"
    assert enum_member("ETextureCompression.TCM_DXTNoAlpha") == "TCM_DXTNoAlpha"