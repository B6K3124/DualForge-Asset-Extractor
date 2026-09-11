from __future__ import annotations

import struct

import numpy as np
import pytest
from PIL import Image

from dualforge.export.texture import image_to_dds, image_to_ktx
from dualforge.export.texture_decode import (
    TextureDecodeError,
    _decode_bc,
    decode_dds,
    decode_ktx,
    decode_ktx2,
    decode_texture_data,
)


def _rgba(image) -> np.ndarray:
    return np.asarray(image.convert("RGBA"))


def _pattern_image(size=(16, 12)):
    width, height = size
    image = Image.fromarray(np.zeros((height, width, 4), np.uint8), "RGBA")
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), (x * 31, y * 17, (x ^ y) * 3, 255 - x))
    return image


def test_dds_roundtrip_uncompressed():
    image = _pattern_image()
    assert np.array_equal(_rgba(decode_dds(image_to_dds(image))), _rgba(image))


def test_dds_roundtrip_odd_dimensions():
    image = _pattern_image((5, 3))
    assert np.array_equal(_rgba(decode_dds(image_to_dds(image))), _rgba(image))


def test_ktx_roundtrip():
    image = _pattern_image()
    assert np.array_equal(_rgba(decode_ktx(image_to_ktx(image))), _rgba(image))


def test_ktx_roundtrip_odd_dimensions():
    image = _pattern_image((7, 5))
    assert np.array_equal(_rgba(decode_ktx(image_to_ktx(image))), _rgba(image))


def test_decode_texture_data_sniffs_containers():
    image = _pattern_image((8, 8))
    assert decode_texture_data(image_to_dds(image)) is not None
    assert decode_texture_data(image_to_ktx(image)) is not None
    assert decode_texture_data(b"plain png bytes here") is None
    assert decode_texture_data(b"") is None


def _block(fmt_len, c0, c1, indices, extra=b""):
    return extra + struct.pack("<HH", c0, c1) + indices


def test_bc1_opaque_red_blocks():
    # c0 = rgb565 red (0xF800), c1 = black, all indices 0 -> pure red.
    block = _block(8, 0xF800, 0x0000, b"\x00\x00\x00\x00")
    decoded = _decode_bc(np.frombuffer(block, np.uint8).reshape(1, 8), "bc1")[0]
    assert decoded[0, 0].tolist() == [255, 0, 0, 255]


def test_bc1_transparent_blocks():
    # c0(0x0000) < c1(0xF800): index 3 picks the transparent color.
    block = _block(8, 0x0000, 0xF800, b"\xff\xff\xff\xff")
    decoded = _decode_bc(np.frombuffer(block, np.uint8).reshape(1, 8), "bc1")[0]
    assert decoded[0, 0].tolist() == [0, 0, 0, 0]


def test_bc2_explicit_alpha():
    alpha = b"\xFF" * 8  # every nibble 0xF -> alpha 255
    block = _block(16, 0xF800, 0x0000, b"\x00" * 4, extra=alpha)
    decoded = _decode_bc(np.frombuffer(block, np.uint8).reshape(1, 16), "bc2")[0]
    assert decoded[0, 0].tolist() == [255, 0, 0, 255]


def test_bc3_alpha_gradient_endpoints():
    from dualforge.export.texture_decode import _bc_alpha_palette

    lo = _bc_alpha_palette(np.array([0], np.uint8), np.array([255], np.uint8))[0]
    assert lo[0] == 0 and lo[1] == 255 and lo[6] == 0 and lo[7] == 255
    hi = _bc_alpha_palette(np.array([255], np.uint8), np.array([0], np.uint8))[0]
    assert hi[0] == 255 and hi[7] == 0

    a0, a1 = 0x00, 0xFF
    block = bytes([a0, a1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])  # all indices 0
    block += struct.pack("<HH", 0xF800, 0x0000) + b"\x00" * 4
    decoded = _decode_bc(np.frombuffer(block, np.uint8).reshape(1, 16), "bc3")[0]
    assert (decoded[..., 3] == 0).all()  # a0 = 0 with index 0 -> transparent
    assert (decoded[..., 0] == 255).all()  # red colour endpoint preserved


def test_bc4_bc5_red_green_channels():
    rblock = bytes([0x00, 0xFF]) + b"\xff" * 6  # all indices 7 -> a1
    red = _decode_bc(np.frombuffer(rblock, np.uint8).reshape(1, 8), "bc4")[0]
    assert (red[..., 0] == 255).all()
    assert set(red[..., 1:3].ravel()) == {0}

    gblock = bytes([0x00, 0xFF]) + b"\xff" * 6  # R: indices 7 -> 255
    gblock += bytes([0x33, 0x66]) + b"\x00" * 6  # G: indices 0 -> a0 0x33
    green = _decode_bc(np.frombuffer(gblock, np.uint8).reshape(1, 16), "bc5")[0]
    assert (green[..., 1] == 0x33).all()
    assert (green[..., 0] == 0xFF).all()


def test_decode_blocks_partial_edge_blocks():
    # A 5x3 BC1 surface decodes without overflowing into neighbours.
    blocks = np.zeros((2 * 1, 8), np.uint8)
    decoded = _decode_bc(blocks, "bc1")
    assert decoded.shape == (2, 4, 4, 4)


def test_too_small_payload_raises():
    from dualforge.export.texture_decode import _decode_blocks

    with pytest.raises(TextureDecodeError):
        _decode_blocks("bc1", b"\x00" * 4, 64, 64)


def test_dxt5_dds_decodes_to_rgba_image():
    # bc3/DXT5 goes through the int32 palette path; decode_dds must hand PIL a
    # uint8 RGBA array (4x4 board -> one 16-byte block).
    header = bytearray(128)
    header[0:4] = b"DDS "
    header[4:8] = (124).to_bytes(4, "little")
    header[12:16] = (4).to_bytes(4, "little")  # height
    header[16:20] = (4).to_bytes(4, "little")  # width
    header[76:80] = (124).to_bytes(4, "little")  # pixel format size
    header[80:84] = (0x4).to_bytes(4, "little")  # DDPF_FOURCC
    header[84:88] = b"DXT5"
    payload = bytes([0x00, 0xFF]) + b"\x00" * 6  # alpha endpoints, all idx 0
    payload += struct.pack("<HH", 0xF800, 0x0000) + b"\x00" * 4
    image = decode_dds(bytes(header) + payload)
    arr = np.asarray(image.convert("RGBA"))
    assert image.mode == "RGBA"
    assert arr.shape == (4, 4, 4)
    assert arr[..., 0].max() == 255  # red colour endpoint
    assert arr[..., 3].min() == 0  # a0=0 with index 0 -> transparent


def test_decode_dds_rejects_garbage():
    with pytest.raises(TextureDecodeError):
        decode_dds(b"not a dds" + b"\x00" * 200)


def test_decode_ktx_rejects_garbage():
    with pytest.raises(TextureDecodeError):
        decode_ktx(b"\x00" * 80)


def _ktx2_rgba8(width=4, height=4, pixel=(10, 20, 30, 40)):
    """Build a minimal, valid non-supercompressed KTX2 RGBA8 file."""
    header = bytearray(80)
    header[0:12] = b"\xABKTX 20\xBB\r\n\x1A\n"
    struct.pack_into("<I", header, 12, 37)  # vkFormat = R8G8B8A8_UNORM
    struct.pack_into("<I", header, 16, 1)  # typeSize
    struct.pack_into("<I", header, 20, width)  # pixelWidth
    struct.pack_into("<I", header, 24, height)  # pixelHeight
    struct.pack_into("<I", header, 28, 0)  # pixelDepth
    struct.pack_into("<I", header, 32, 0)  # layerCount
    struct.pack_into("<I", header, 36, 1)  # faceCount
    struct.pack_into("<I", header, 40, 1)  # levelCount
    struct.pack_into("<I", header, 44, 0)  # supercompressionScheme = none
    payload = bytes(pixel) * (width * height)
    level_off = 80 + 8  # level index table (2 u32 per mip)
    index = struct.pack("<II", level_off, len(payload))
    return bytes(header) + index + payload


def test_ktx2_rgba8_roundtrip():
    decoded = decode_ktx2(_ktx2_rgba8())
    assert decoded.size == (4, 4)
    assert _rgba(decoded)[0, 0].tolist() == [10, 20, 30, 40]


def test_ktx2_supercompression_rejected():
    blob = bytearray(_ktx2_rgba8())
    struct.pack_into("<I", blob, 44, 1)  # zstd supercompression
    with pytest.raises(TextureDecodeError):
        decode_ktx2(bytes(blob))


def test_decode_texture_data_ktx2():
    from dualforge.export.texture_decode import KTX2_MAGIC

    blob = _ktx2_rgba8()
    assert blob[:12] == KTX2_MAGIC
    assert decode_texture_data(blob) is not None