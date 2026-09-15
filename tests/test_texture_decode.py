from __future__ import annotations

import struct

import numpy as np
import pytest
from PIL import Image

from dualforge.export.texture import image_to_dds, image_to_ktx
from dualforge.export.texture_decode import (
    TextureDecodeError,
    _decode_bc,
    _decode_etc_rgb,
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


# ---------------------------------------------------------------------------
# ETC2 / EAC golden vectors. Blocks are packed per the GLES3 spec C.1 bit
# layouts: byte 0 holds spec bits 63..56, so a big-endian word read makes
# (w >> n) & 1 equal spec bit n. All decode helpers take (B, 8|16) uint8.


def _etc_block(bits: dict[int, int]) -> np.ndarray:
    """Build a single 8-byte ETC/EAC block from spec-bit integer values.

    Keys are (first_bit_index, value); bits are OR-ed into a big-endian word.
    """
    w = 0
    for first, value in bits.items():
        w |= value << first
    return np.frombuffer(int(w).to_bytes(8, "big"), np.uint8).reshape(1, 8)


def _etc_block_from_word(w: int) -> np.ndarray:
    return np.frombuffer(int(w).to_bytes(8, "big"), np.uint8).reshape(1, 8)


def _eac_index_bits(indices: np.ndarray) -> int:
    """Pack per-pixel 3-bit EAC indices into spec bits 45-3n (MSB at 47-3n)."""
    w = 0
    for n in range(16):
        w |= (indices[n] & 7) << (45 - 3 * n)
    return w


def test_etc2_individual_mode():
    # R1=0xF, G1=0x3, B1=0x8 -> extended (255, 51, 136); R2=0x0, G2=0xC,
    # B2=0x5 -> (0, 204, 85). cw1=cw2=1 (mod rows (5,17,-5,-17)); all idx 0 so
    # each pixel is base + mod[0]. flip=1: sub0 = top 2 rows, sub1 = bottom 2.
    block = _etc_block({60:0xF, 56:0x0, 52:0x3, 48:0xC, 44:0x8, 40:0x5,
                       37:1, 34:1, 33:0, 32:1})
    out = _decode_etc_rgb(block, "etc2")[0]  # (4,4,4)
    assert out[0, 0, :3].tolist() == [255, 56, 141]  # 260 clipped to 255
    assert out[3, 3, :3].tolist() == [5, 209, 90]
    assert (out[..., 3] == 255).all()


def test_etc2_differential_mode():
    # R=5, G=6, B=7 (5-bit) with dR=dG=dB=0 -> sums in [0,31] -> differential.
    # Extended: 5->(5<<3)|(5>>2)=40+1=41; 6->48+1=49; 7->56+1=57 base1.
    # base2 same (deltas 0). cw1=cw2=0 (mod row (2,8,-2,-8)); all idx 0, so
    # each pixel is base + mod[0] = base + 2.
    block = _etc_block({59:5, 56:0, 51:6, 48:0, 43:7, 40:0, 37:0, 34:0, 33:1, 32:1})
    out = _decode_etc_rgb(block, "etc2")[0]
    assert out[0, 0, :3].tolist() == [43, 51, 59]


def test_etc2_t_mode_paint_colors():
    # T mode: R1a=3 (bits 61..60, we use 60..59 lens), R1b=2 -> R1 nibble 14
    # -> (238,51,136); R2=(4,12,9) -> (68,204,153). da=1, db=1 -> dist idx 3
    # -> d=16. So paints [c1, c2+16, c2, c2-16]. Bit63+62+61 push R (diff
    # lens) out of [0,31] so cr>31 selects T; those bits are ignored by the
    # T-mode paint fields.
    block = _etc_block({63:1, 62:1, 61:1, 60:1, 59:1, 57:1, 52:3, 48:8,
                       44:4, 40:12, 36:9, 34:1, 33:1, 32:1,
                       4:1, 24:1, 12:1, 28:1})
    out = _decode_etc_rgb(block, "etc2")[0]
    pixels = out.reshape(16, 4)
    assert pixels[0][:3].tolist() == [238, 51, 136]        # paint 0
    assert pixels[1][:3].tolist() == [68 + 16, 204 + 16, 153 + 16]  # paint 1
    assert pixels[2][:3].tolist() == [68, 204, 153]        # paint 2
    assert pixels[3][:3].tolist() == [52, 188, 137]        # paint 3


def test_etc2_h_mode_paint_colors():
    # H mode: base1 (9,10,3) -> (153,170,51); base2 (2,13,7) -> (34,221,119).
    # value1=153*65536+170*256+51 = 10070579; value2=2284919 -> cmp bit = 1.
    # da=1, db=0 -> index 5 -> d=dist[5]=32. Selection: cr = 9 + sign3(5) =
    # 6 (in range), cg = 0 + sign3(4) = -4 (out) -> mu = H. Bit 50 (the dG
    # MSB) is not part of any H-mode paint field.
    block = _etc_block({59:9, 56:5, 52:0, 51:0, 48:1, 47:1,
                       43:2, 40:6, 39:1, 35:7, 34:1, 33:1, 32:0,
                       50:1,
                       4:1, 24:1, 12:1, 28:1})
    out = _decode_etc_rgb(block, "etc2")[0]
    pixels = out.reshape(16, 4)
    assert pixels[0][:3].tolist() == [153 + 32, 170 + 32, 51 + 32]  # paint 0
    assert pixels[1][:3].tolist() == [153 - 32, 170 - 32, 51 - 32]  # paint 1
    assert pixels[2][:3].tolist() == [34 + 32, 221 + 32, 119 + 32]  # paint 2
    assert pixels[3][:3].tolist() == [34 - 32, 221 - 32, 119 - 32]  # paint 3


def test_etc2_planar_mode_golden():
    # GLES3 spec figure C.3g example: origin (12,64,62), horizontal (50,5,37),
    # vertical (40,112,45) -> (0,0)= (48,129,251), (3,3)=(250,112,124).
    # Unused discriminator bits 45..47 set so b = B+dB exits [0,31] (planar)
    # while r/g sums stay in range; bit 33 (D) must be 1.
    block = _etc_block({56:(64 >> 6) & 1, 57:12, 49:64 & 0x3F, 48:1,
                        43:(62 >> 3) & 3, 40:(62 >> 1) & 3, 39:62 & 1,
                        34:50 >> 1, 32:50 & 1, 25:5, 24:(37 >> 5) & 1,
                        19:37 & 0x1F, 16:(40 >> 3) & 7, 13:40 & 7,
                        8:(112 >> 2) & 0x1F, 6:112 & 3, 0:45,
                        33:1, 45:7})
    out = _decode_etc_rgb(block, "etc2")[0]
    assert out[0, 0, :3].tolist() == [48, 129, 251]
    assert out[3, 3, :3].tolist() == [250, 112, 124]


def test_etc2_a1_punchthrough_transparent():
    # Non-opaque (fmt a1, D=0). R=13, G=14, B=15 with dR=dG=dB=0 -> sums in
    # [0,31] so mu stays differential; cw1=cw2=1 -> non-opaque zero-modifier
    # table (0,17,0,-17), so idx 0/1/3 keep the extended base (107,115,123).
    # The pixel with index 2 (memory position 2 = col 2, row 0) is punched to
    # fully transparent black. flip=1: sub0 = top rows.
    block = _etc_block({59:13, 51:14, 43:15, 37:1, 34:1, 33:0, 32:1,
                       24:1})  # pixel 2 index nibble = 2
    out = _decode_etc_rgb(block, "etc2_a1")[0]
    pixels = out.reshape(16, 4)
    assert pixels[0][3] == 255 and pixels[0][:3].tolist() == [107, 115, 123]
    assert tuple(pixels[2]) == (0, 0, 0, 0)  # index 2 -> transparent
    assert pixels[3][:3].tolist() == [107, 115, 123]


def test_etc2_a1_opaque_normal_color():
    # D=1 -> opaque; index 2 is a paint color, not transparent.
    block = _etc_block({59:13, 51:14, 43:15, 37:1, 34:1, 33:1, 32:1})
    out = _decode_etc_rgb(block, "etc2_a1")[0]
    pixels = out.reshape(16, 4)
    assert (pixels[2][..., 3] == 255).all()


def test_eac_alpha_worked_example():
    # Spec p.311: table index 13 -> table [-1,-2,-3,-10,0,1,2,9].
    # base=103, mult=2. Pixel (0,0) has index 3 (value -10): 103 - 20 = 83.
    # Pixel (1,1) = linear position 5 has index 0 (modifier -1): 103-2 = 101.
    from dualforge.export.texture_decode import _decode_eac_channel_alpha

    indices = np.zeros(16, np.uint64)
    indices[0] = 3
    w = 103 << 56 | 13 << 48 | 2 << 52 | _eac_index_bits(indices)
    alpha = _decode_eac_channel_alpha(_etc_block_from_word(w))[0].reshape(4, 4)
    assert alpha[0, 0] == 83
    assert alpha[1, 1] == 101


def test_eac_r11_unsigned():
    # C.4: base*8 + 4 + mod*effect, effect = 1 when mult==0. base=103,
    # table 13, mult 0, idx 3 -> 103*8 + 4 - 10 = 818 -> 818>>3 = 102.
    from dualforge.export.texture_decode import _decode_eac

    indices = np.zeros(16, np.uint64)
    indices[0] = 3
    w = 103 << 56 | 13 << 48 | 0 << 52 | _eac_index_bits(indices)
    out = _decode_eac(_etc_block_from_word(w), "eac_r11")[0]
    assert out[0, 0, 0] == 102
    assert out[0, 0, 3] == 255


def test_eac_r11_signed_negative_wraps():
    # C.8: base*8 + mod*effect (no +4), clamp [-1023,1023], >>3 then
    # two's-complement wrap. base = -100, table 13, mult 2, idx 3:
    # -800 + (-10)*16 = -960 -> -120 -> uint8 136.
    from dualforge.export.texture_decode import _decode_eac

    indices = np.zeros(16, np.uint64)
    indices[0] = 3
    base = np.uint8(-100 & 0xFF)
    w = int(base) << 56 | 13 << 48 | 2 << 52 | _eac_index_bits(indices)
    out = _decode_eac(_etc_block_from_word(w), "eac_r11s")[0]
    assert out[0, 0, 0] == 136


def test_eac_rg11_channels():
    from dualforge.export.texture_decode import _decode_eac

    rindices = np.zeros(16, np.uint64)
    rindices[0] = 3
    rw = 103 << 56 | 13 << 48 | 0 << 52 | _eac_index_bits(rindices)
    gidx = np.zeros(16, np.uint64)
    gidx[0] = 0
    gw = 100 << 56 | 1 << 48 | 1 << 52 | _eac_index_bits(gidx)
    block = np.concatenate([_etc_block_from_word(rw), _etc_block_from_word(gw)], axis=1)
    out = _decode_eac(block, "eac_rg11")[0]
    assert out[0, 0, 0] == 102  # red: 103*8+4-10 = 818 -> 102
    assert out[0, 0, 1] == (100 * 8 + 4 + (-3) * 8) >> 3  # idx0 mod -3, effect 8


def test_etc2_a_rgba_combined():
    # 16-byte block: EAC alpha (base 255 -> all 255) + RGB ETC2 individual.
    from dualforge.export.texture_decode import _decode_etc2_rgba

    alpha_w = 255 << 56 | 0 << 48 | 0 << 52
    rgb = _etc_block({60:0xF, 56:0x0, 52:0x3, 48:0xC, 44:0x8, 40:0x5,
                     37:1, 34:1, 33:0, 32:1})
    block = np.concatenate([_etc_block_from_word(alpha_w), rgb], axis=1)
    out = _decode_etc2_rgba(block)[0]
    assert out[0, 0, :3].tolist() == [255, 56, 141]
    assert (out[..., 3] == 255).all()


def test_etc_format_id_mapping():
    from dualforge.export.texture_decode import (
        _BLOCK_FORMATS,
        _KTX_COMPRESSED,
        _VK_FORMATS,
    )

    assert {"etc2", "etc2_a1", "etc2_a", "eac_r11",
                              "eac_r11s", "eac_rg11", "eac_rg11s"} <= _BLOCK_FORMATS
    assert _KTX_COMPRESSED[0x9270] == "eac_r11"
    assert _KTX_COMPRESSED[0x9271] == "eac_r11s"
    assert _KTX_COMPRESSED[0x9278] == "etc2_a"
    assert _KTX_COMPRESSED[0x9276] == "etc2_a1"
    assert _KTX_COMPRESSED[0x8D64] == "etc2"  # ETC1 is a subset
    assert _VK_FORMATS[140] == "etc2"
    assert _VK_FORMATS[148] == "eac_rg11"
    assert _VK_FORMATS[149] == "eac_rg11s"


def test_etc2_decode_blocks_crops_edges():
    from dualforge.export.texture_decode import _decode_blocks

    block = _etc_block({60:0xF, 56:0x0, 52:0x3, 48:0xC, 44:0x8, 40:0x5,
                       37:1, 34:1, 33:0, 32:1}).tobytes()
    image = _decode_blocks("etc2", block * 2, 5, 3)
    assert image.shape == (3, 5, 4)
    assert tuple(image[0, 0]) == (255, 56, 141, 255)