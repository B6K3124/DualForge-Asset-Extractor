"""Pure-Python texture container decoding (DDS / KTX1 / KTX2).

Decodes the GPU block formats most common in shipped games with no C-side
dependency: BC1 (DXT1), BC2 (DXT3), BC3 (DXT5), BC4 (RGTC1), BC5 (RGTC2),
plus uncompressed 8/16/24/32-bit RGB/RGBA/grayscale layouts. Output is
always an RGBA PIL image in top-down order.

Supported containers:

* DDS  - v1 + DX10 (BC1-BC5, RGBA8/BGRA8, classic uncompressed masks)
* KTX1 - BC1/2/3/4/5 + uncompressed GL formats (8/16-bit)
* KTX2 - non-supercompressed files (BC1-BC5, R8G8B8A8, B8G8R8A8)

BC6H/BC7/ASTC/ETC and KTX2 supercompression are intentionally left out and
raise a descriptive error rather than producing garbage.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

DDS_MAGIC = b"DDS "
KTX1_MAGIC = b"\xABKTX 11\xBB\r\n\x1A\n"
KTX2_MAGIC = b"\xABKTX 20\xBB\r\n\x1A\n"

_DDPF_ALPHAPIXELS = 0x1
_DDPF_FOURCC = 0x4
_DDPF_RGB = 0x40
_DDPF_RGBA = 0x41
_DDPF_LUMINANCE = 0x20000

_FOURCC_FORMATS: Dict[bytes, str] = {
    b"DXT1": "bc1",
    b"DXT2": "bc2",
    b"DXT3": "bc2",
    b"DXT4": "bc3",
    b"DXT5": "bc3",
    b"ATI1": "bc4",
    b"BC4U": "bc4",
    b"ATI2": "bc5",
    b"BC5U": "bc5",
}

_DXGI_FORMATS: Dict[int, str] = {
    71: "bc1",  # BC1_UNORM
    74: "bc2",  # BC2_UNORM
    77: "bc3",  # BC3_UNORM
    80: "bc4",  # BC4_UNORM
    83: "bc5",  # BC5_UNORM
    28: "rgba8",  # R8G8B8A8_UNORM
    87: "bgra8",  # B8G8R8A8_UNORM
}

_KTX_COMPRESSED: Dict[int, str] = {
    0x83F1: "bc1",  # GL_COMPRESSED_RGBA_S3TC_DXT1_EXT
    0x83F4: "bc2",  # GL_COMPRESSED_RGBA_S3TC_DXT3_EXT
    0x83F5: "bc3",  # GL_COMPRESSED_RGBA_S3TC_DXT5_EXT
    0x8DBB: "bc4",  # GL_COMPRESSED_RED_RGTC1
    0x8DBD: "bc5",  # GL_COMPRESSED_RG_RGTC2
}

_KTX_UNCOMPRESSED: Dict[int, str] = {
    0x1903: "red",  # GL_RED
    0x1907: "rgb",
    0x1908: "rgba",
    0x1909: "luminance",
    0x190A: "luminance_alpha",
    0x8227: "rg",
    0x8051: "rgb8",
    0x8058: "rgba8",
    0x8C41: "srgb8",
    0x8C43: "srgb8_alpha8",
    0x8D96: "rgb8",
    0x8D98: "rgba8",
    0x8D94: "red8",
    0x8D95: "rg8",
    0x822B: "rgb8",
    0x822C: "rgba8",
}

_VK_FORMATS: Dict[int, str] = {
    131: "bc1",  # VK_FORMAT_BC1_RGBA_UNORM_BLOCK
    132: "bc2",  # VK_FORMAT_BC2_UNORM_BLOCK
    133: "bc3",  # VK_FORMAT_BC3_UNORM_BLOCK
    134: "bc4",  # VK_FORMAT_BC4_UNORM_BLOCK
    135: "bc5",  # VK_FORMAT_BC5_UNORM_BLOCK
    37: "rgba8",  # VK_FORMAT_R8G8B8A8_UNORM
    44: "bgra8",  # VK_FORMAT_B8G8R8A8_UNORM
}

_BLOCK_FORMATS = frozenset({"bc1", "bc2", "bc3", "bc4", "bc5"})
_MAX_DIMENSION = 16384


class TextureDecodeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Public entry point / sniffing.


def decode_texture_data(data: bytes):
    """Decode a DDS/KTX1/KTX2 buffer to an RGBA PIL image.

    Returns None when the bytes are not a recognized container (callers fall
    through to generic sniffing); raises :class:`TextureDecodeError` when the
    container is recognized but uses an unsupported payload format.
    """
    if not data:
        return None
    if data[:4] == DDS_MAGIC:
        return decode_dds(data)
    if data[:12] == KTX1_MAGIC:
        return decode_ktx(data)
    if data[:12] == KTX2_MAGIC:
        return decode_ktx2(data)
    return None


# ---------------------------------------------------------------------------
# 565 / 5551 / 4444 unpackers used across containers.


def _expand565(v) -> np.ndarray:
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return np.stack(
        [((r << 3) | (r >> 2)), ((g << 2) | (g >> 4)), ((b << 3) | (b >> 2))],
        axis=-1,
    ).astype(np.uint8)


def _expand5551(v) -> Tuple[np.ndarray, np.ndarray]:
    r = (v >> 11) & 0x1F
    g = (v >> 6) & 0x1F
    b = (v >> 1) & 0x1F
    a = v & 1
    rgb = np.stack(
        [((r << 3) | (r >> 2)), ((g << 3) | (g >> 2)), ((b << 3) | (b >> 2))],
        axis=-1,
    ).astype(np.uint8)
    return rgb, (a * 255).astype(np.uint8)


def _expand4444(v) -> Tuple[np.ndarray, np.ndarray]:
    rgb = np.stack(
        [((v >> 12) & 0xF) * 17, ((v >> 8) & 0xF) * 17, ((v >> 4) & 0xF) * 17],
        axis=-1,
    ).astype(np.uint8)
    a = ((v & 0xF) * 17).astype(np.uint8)
    return rgb, a


# ---------------------------------------------------------------------------
# Block decoders. All operate on ``(B, block_bytes)`` uint8 and return
# ``(B, 4, 4, 4)`` uint8 RGBA (top-down within each block).


def _words(blocks: np.ndarray, first: int, width: int, dtype) -> np.ndarray:
    """Read ``width``-byte sub-fields packed LSB-first into an integer array."""
    chunk = np.ascontiguousarray(blocks[:, first:first + width])
    return np.frombuffer(chunk.tobytes(), dtype=dtype).reshape(blocks.shape[0])


def _bc1_palette(c0_words: np.ndarray, c1_words: np.ndarray) -> np.ndarray:
    """Return the 4-entry color+alpha palette ``(B, 4, 4)`` for BC1 data."""
    col0 = _expand565(c0_words).astype(np.int32)
    col1 = _expand565(c1_words).astype(np.int32)
    opaque = c0_words > c1_words  # (B,)
    use = opaque[..., None]
    col2 = np.where(use, (2 * col0 + col1 + 1) // 3, (col0 + col1 + 1) // 2)
    col3 = np.where(use, (col0 + 2 * col1 + 1) // 3, 0)
    palette = np.stack([col0, col1, col2, col3], axis=1)  # (B,4,3)
    alpha = np.where(
        opaque[..., None],
        np.full((1, 4), 255, np.int32),
        np.array([[255, 255, 255, 0]], dtype=np.int32),
    )
    return np.concatenate([palette, alpha[..., None]], axis=-1)  # (B,4,4)


def _bc_color_indices(blocks: np.ndarray, first: int) -> np.ndarray:
    """2-bit color indices from 4 bytes, LSB-first, column-major 4x4."""
    words = _words(blocks, first, 4, np.uint32)  # (B,)
    shifts = (np.arange(16) % 4) * 2 + (np.arange(16) // 4) * 8
    return (words[:, None] >> shifts[None, :]) & 3


def _bc_alpha_indices(blocks: np.ndarray, byte_off: int) -> np.ndarray:
    """3-bit alpha indices from 6 bytes at ``byte_off`` (2 endpoint bytes
    precede them), read as one uint64 so masking stays cheap."""
    words = _words(blocks, byte_off, 8, np.uint64)
    shifts = (16 + np.arange(16) * 3).astype(np.uint64)
    return (words[:, None] >> shifts[None, :]) & 7


def _bc_alpha_palette(a0: np.ndarray, a1: np.ndarray) -> np.ndarray:
    """(B, 8) alpha palette for BC3/4/5 style endpoints.

    Index 0/1 are always the endpoints. When a0 > a1 the 8 entries form a
    single linear ramp; otherwise the middle six are interpolated and
    indices 6/7 are fixed to 0/255 (BC3 spec).
    """
    steps = np.arange(8, dtype=np.float64)[None, :]  # (1, 8)
    lo = a0.astype(np.float64)[:, None]  # (B, 1)
    hi = a1.astype(np.float64)[:, None]
    high = (a0 > a1)[:, None]
    ramp8 = (lo * (7 - steps) + hi * steps) / 7.0
    ramp6 = (lo * (5 - (steps - 1)) + hi * (steps - 1)) / 5.0
    low = np.where(
        steps <= 5,
        ramp6,
        np.where(steps == 7, 255.0, 0.0),
    )
    low = np.where(steps == 0, lo, np.where(steps == 1, hi, low))
    return np.rint(np.where(high, ramp8, low)).astype(np.uint8)


def _decode_bc(blocks: np.ndarray, fmt: str) -> np.ndarray:
    """Decode an ``(B, block_bytes)`` array into ``(B, 4, 4, 4)`` RGBA."""
    b = blocks.shape[0]
    if fmt == "bc1":
        palette = _bc1_palette(_words(blocks, 0, 2, np.uint16), _words(blocks, 2, 2, np.uint16))
        idx = _bc_color_indices(blocks, 4)
        return palette[np.arange(b)[:, None], idx].reshape(b, 4, 4, 4)
    if fmt == "bc2":
        byte_sel = blocks[:, np.arange(16) // 2]  # (B,16)
        nib_shift = (np.arange(16) % 2) * 4
        alpha_nibbles = (byte_sel >> nib_shift[None, :]) & 0xF
        palette = _bc1_palette(_words(blocks, 8, 2, np.uint16), _words(blocks, 10, 2, np.uint16))
        idx = _bc_color_indices(blocks, 12)
        color = palette[np.arange(b)[:, None], idx]
        color[..., 3] = (alpha_nibbles * 17).astype(np.uint8)
        return color.reshape(b, 4, 4, 4)
    if fmt == "bc3":
        alphas = _bc_alpha_palette(blocks[:, 0], blocks[:, 1])
        a_idx = _bc_alpha_indices(blocks, 0)
        alpha = alphas[np.arange(b)[:, None], a_idx]  # (B,16)
        palette = _bc1_palette(_words(blocks, 8, 2, np.uint16), _words(blocks, 10, 2, np.uint16))
        idx = _bc_color_indices(blocks, 12)
        color = palette[np.arange(b)[:, None], idx]
        color = color.copy()
        color[..., 3] = alpha
        return color.reshape(b, 4, 4, 4)
    if fmt == "bc4":
        alpha = _bc_alpha_palette(blocks[:, 0], blocks[:, 1])
        a = alpha[np.arange(b)[:, None], _bc_alpha_indices(blocks, 0)]  # (B,16)
        out = np.zeros((b, 16, 4), np.uint8)
        out[..., 0] = a
        out[..., 3] = 255
        return out.reshape(b, 4, 4, 4)
    if fmt == "bc5":
        r = _bc_alpha_palette(blocks[:, 0], blocks[:, 1])
        g = _bc_alpha_palette(blocks[:, 8], blocks[:, 9])
        out = np.zeros((b, 16, 4), np.uint8)
        out[..., 0] = r[np.arange(b)[:, None], _bc_alpha_indices(blocks, 0)]
        out[..., 1] = g[np.arange(b)[:, None], _bc_alpha_indices(blocks, 8)]
        out[..., 3] = 255
        return out.reshape(b, 4, 4, 4)
    raise TextureDecodeError(f"unsupported block format: {fmt}")


def _decode_blocks(fmt: str, data: bytes, width: int, height: int) -> np.ndarray:
    """Decode a full buffer of blocks into an ``(H, W, 4)`` uint8 image."""
    block_bytes = 8 if fmt in ("bc1", "bc4") else 16
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    needed = bw * bh * block_bytes
    if len(data) < needed:
        raise TextureDecodeError(
            f"{fmt} payload too small: have {len(data)} bytes, need {needed}"
        )
    blocks = np.frombuffer(data, dtype=np.uint8, count=needed).reshape(bh, bw, block_bytes)
    out = _decode_bc(blocks.reshape(-1, block_bytes), fmt)  # (N, 4, 4, 4)
    out = out.reshape(bh, bw, 4, 4, 4)
    image = out.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    # bc1/bc2/bc3 palettes arrive as int32; PIL only accepts uint8 here.
    return np.ascontiguousarray(image[:height, :width], dtype=np.uint8)


# ---------------------------------------------------------------------------
# Uncompressed decoding (mask-driven for DDS, layout-driven for KTX).


def _channel_from_mask(words: np.ndarray, mask: int) -> Optional[np.ndarray]:
    if mask == 0:
        return None
    shift = (mask & -mask).bit_length() - 1
    nbits = bin(mask).count("1")
    v = (words & mask) >> shift
    if nbits < 8:
        shift_left = 8 - nbits
        fill = 2 * nbits - 8
        if fill > 0:
            v = (v << shift_left) | (v >> fill)
        else:
            v = v << shift_left
    elif nbits > 8:
        v = v >> (nbits - 8)
    return v.astype(np.uint8)


def _decode_uncompressed(
    data: bytes,
    width: int,
    height: int,
    bitcount: int,
    rmask: int,
    gmask: int,
    bmask: int,
    amask: int,
    luminance: bool = False,
) -> np.ndarray:
    bpp = (bitcount + 7) // 8
    needed = width * height * bpp
    if len(data) < needed:
        raise TextureDecodeError("pixel payload too small for image dimensions")
    px = data[:needed]
    out = np.zeros((height, width, 4), dtype=np.uint8)
    if bpp == 3:
        raw = np.frombuffer(px, dtype=np.uint8).reshape(height, width, 3)
        channels = [mask_channel_index(m) for m in (rmask, gmask, bmask)]
        for dst, ch in zip((0, 1, 2), channels):
            out[..., dst] = raw[..., ch] if ch is not None else 0
        out[..., 3] = 255
        return out
    if bpp == 1:
        raw = np.frombuffer(px, dtype=np.uint8).reshape(height, width).astype(np.uint32)
    elif bpp == 2:
        raw = np.frombuffer(px, dtype=np.uint16).reshape(height, width).astype(np.uint32)
    else:
        raw = np.frombuffer(px, dtype=np.uint32).reshape(height, width)
    if luminance or (rmask == 0 and gmask == 0 and bmask == 0):
        gray = raw.astype(np.uint8) if bpp == 1 else (raw & 0xFF).astype(np.uint8)
        out[..., 0] = gray
        out[..., 1] = gray
        out[..., 2] = gray
        if amask and bpp >= 2:
            out[..., 3] = ((raw >> 8) & 0xFF).astype(np.uint8)
        else:
            out[..., 3] = 255
        return out
    r = _channel_from_mask(raw, rmask)
    g = _channel_from_mask(raw, gmask)
    b = _channel_from_mask(raw, bmask)
    a = _channel_from_mask(raw, amask)
    if r is not None:
        out[..., 0] = r
    if g is not None:
        out[..., 1] = g
    if b is not None:
        out[..., 2] = b
    out[..., 3] = a if a is not None else 255
    return out


def mask_channel_index(mask: int) -> Optional[int]:
    """Map a 24-bit channel mask to the byte index it occupies (BGR style)."""
    if mask == 0:
        return None
    shift = (mask & -mask).bit_length() - 1
    return 2 - shift // 8


_KTX_ROW_BPP: Dict[str, int] = {
    "rgba": 4,
    "rgba8": 4,
    "srgb8_alpha8": 4,
    "rgb": 3,
    "rgb8": 3,
    "srgb8": 3,
    "red": 1,
    "red8": 1,
    "rg": 2,
    "rg8": 2,
    "luminance": 1,
    "luminance_alpha": 2,
}


def _decode_ktx_pixels(payload: bytes, width: int, height: int, fmt: str, gl_type: int, endian: str) -> np.ndarray:
    """Decode an uncompressed KTX mip payload (rows aligned to 4 bytes)."""
    bpp = _KTX_ROW_BPP.get(fmt)
    if bpp is None:
        raise TextureDecodeError(f"unsupported KTX uncompressed format: {fmt}")
    row_pitch = (width * bpp + 3) & ~3
    if gl_type == 0x1401:  # UNSIGNED_BYTE
        rows = b"".join(
            payload[i * row_pitch: i * row_pitch + width * bpp] for i in range(height)
        )
    else:
        raw_rows = b"".join(
            payload[i * row_pitch: i * row_pitch + width * bpp] for i in range(height)
        )
        rows = raw_rows
    if gl_type == 0x8363:  # UNSIGNED_SHORT_5_6_5
        return _kit_rgb16(rows, width, height, endian, "565")
    if gl_type == 0x8362:  # UNSIGNED_SHORT_5_5_5_1
        return _kit_rgb16(rows, width, height, endian, "5551")
    if gl_type == 0x8033:  # UNSIGNED_SHORT_4_4_4_4
        return _kit_rgb16(rows, width, height, endian, "4444")
    arr = np.frombuffer(rows, dtype=np.uint8).reshape(height, width, bpp)
    out = np.zeros((height, width, 4), dtype=np.uint8)
    if fmt in ("rgba", "rgba8", "srgb8_alpha8"):
        out[..., :4] = arr
    elif fmt in ("rgb", "rgb8", "srgb8"):
        out[..., :3] = arr
        out[..., 3] = 255
    elif fmt in ("red", "red8"):
        out[..., 0] = arr[..., 0]
        out[..., 3] = 255
    elif fmt in ("rg", "rg8"):
        out[..., :2] = arr
        out[..., 3] = 255
    elif fmt == "luminance":
        out[..., 0] = arr[..., 0]
        out[..., 1] = arr[..., 0]
        out[..., 2] = arr[..., 0]
        out[..., 3] = 255
    elif fmt == "luminance_alpha":
        out[..., 0] = arr[..., 0]
        out[..., 1] = arr[..., 0]
        out[..., 2] = arr[..., 0]
        out[..., 3] = arr[..., 1]
    return out


def _kit_rgb16(rows: bytes, width: int, height: int, endian: str, kind: str) -> np.ndarray:
    words = np.frombuffer(rows[: width * height * 2], dtype=f"{endian}u2").astype(np.uint32)
    out = np.zeros((height, width, 4), dtype=np.uint8)
    if kind == "565":
        out[..., :3] = _expand565(words)
        out[..., 3] = 255
    elif kind == "5551":
        rgb, a = _expand5551(words)
        out[..., :3] = rgb
        out[..., 3] = a
    elif kind == "4444":
        rgb, a = _expand4444(words)
        out[..., :3] = rgb
        out[..., 3] = a
    return out


# ---------------------------------------------------------------------------
# Container parsers.


def decode_dds(data: bytes):
    from PIL import Image

    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise TextureDecodeError("not a DDS file")
    height = _u32(data, 12)
    width = _u32(data, 16)
    if not _valid_dimensions(width, height):
        raise TextureDecodeError("invalid DDS dimensions")
    pf_flags = _u32(data, 80)
    fourcc = data[84:88]
    bitcount = _u32(data, 88)
    rmask = _u32(data, 92)
    gmask = _u32(data, 96)
    bmask = _u32(data, 100)
    amask = _u32(data, 104)
    offset = 128
    fmt: Optional[str] = None
    luminance = bool(pf_flags & _DDPF_LUMINANCE)
    if fourcc == b"DX10":
        if len(data) < 148:
            raise TextureDecodeError("truncated DX10 DDS header")
        dxgi = _u32(data, 128)
        if dxgi not in _DXGI_FORMATS:
            raise TextureDecodeError(f"unsupported DXGI format {dxgi}")
        fmt = _DXGI_FORMATS[dxgi]
        offset = 148
    elif fourcc in _FOURCC_FORMATS:
        fmt = _FOURCC_FORMATS[fourcc]
    elif pf_flags & (_DDPF_RGB | _DDPF_RGBA | _DDPF_ALPHAPIXELS):
        fmt = "uncompressed"

    payload = data[offset:]
    if fmt in _BLOCK_FORMATS:
        image = _decode_blocks(fmt, payload, width, height)
    elif fmt in ("rgba8", "bgra8"):
        if fmt == "rgba8":
            image = _decode_uncompressed(payload, width, height, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
        else:
            image = _decode_uncompressed(payload, width, height, 32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
    elif fmt == "uncompressed":
        image = _decode_uncompressed(payload, width, height, bitcount, rmask, gmask, bmask, amask, luminance=luminance)
    else:
        raise TextureDecodeError(f"unsupported DDS pixel format (flags 0x{pf_flags:X})")
    image = np.ascontiguousarray(image[::-1])  # DDS is bottom-up
    return Image.fromarray(image, "RGBA")


def decode_ktx(data: bytes):
    from PIL import Image

    if len(data) < 64 or data[:12] != KTX1_MAGIC:
        raise TextureDecodeError("not a KTX file")
    endian = ">" if _u32(data, 12) == 0x01020304 else "<"
    gl_type = _u32(data, 16, endian)
    gl_format = _u32(data, 24, endian)
    internal = _u32(data, 28, endian)
    width = _u32(data, 36, endian)
    height = _u32(data, 40, endian)
    mips = _u32(data, 56, endian)
    kv_size = _u32(data, 60, endian)
    if not _valid_dimensions(width, height):
        raise TextureDecodeError("invalid KTX dimensions")
    pos = (64 + kv_size + 3) & ~3
    if internal in _KTX_COMPRESSED:
        fmt = _KTX_COMPRESSED[internal]
    elif internal in _KTX_UNCOMPRESSED:
        fmt = _KTX_UNCOMPRESSED[internal]
    else:
        # Fall back on the base internal format (glFormat) for ambiguous codes.
        if gl_format in _KTX_UNCOMPRESSED:
            fmt = _KTX_UNCOMPRESSED[gl_format]
        else:
            raise TextureDecodeError(f"unsupported KTX internal format 0x{internal:X}")

    image = None
    for level in range(mips or 1):
        image_size = _u32(data, pos, endian)
        pos += 4
        payload = data[pos:pos + image_size]
        pos += image_size
        pos = (pos + 3) & ~3
        if level > 0:
            continue
        if fmt in _BLOCK_FORMATS:
            image = _decode_blocks(fmt, payload, width, height)
        else:
            image = _decode_ktx_pixels(payload, width, height, fmt, gl_type, endian)
    if image is None:
        raise TextureDecodeError("KTX file has no mip level 0")
    return Image.fromarray(np.ascontiguousarray(image), "RGBA")


def decode_ktx2(data: bytes):
    from PIL import Image

    if len(data) < 80 or data[:12] != KTX2_MAGIC:
        raise TextureDecodeError("not a KTX2 file")
    vk_format = _u32(data, 12)
    width = _u32(data, 20)
    height = _u32(data, 24)
    supercomp = _u32(data, 44)
    if supercomp != 0:
        raise TextureDecodeError("KTX2 supercompression is not supported (zstd/zlc)")
    if vk_format not in _VK_FORMATS:
        raise TextureDecodeError(f"unsupported KTX2 vkFormat {vk_format}")
    fmt = _VK_FORMATS[vk_format]
    if not _valid_dimensions(width, height):
        raise TextureDecodeError("invalid KTX2 dimensions")
    # The Level Index immediately follows the 80-byte header: two u32 per mip
    # (levelByteOffset, levelByteLength); level 0 is the first entry.
    if len(data) < 88:
        raise TextureDecodeError("truncated KTX2 level index")
    level_off = _u32(data, 80)
    level_len = _u32(data, 84)
    payload = data[level_off:level_off + level_len] if level_len else b""
    if not payload:
        raise TextureDecodeError("KTX2 level 0 payload missing")
    if fmt in _BLOCK_FORMATS:
        image = _decode_blocks(fmt, payload, width, height)
    else:
        bpp = 4
        row_pitch = (width * bpp + 3) & ~3
        if len(payload) < row_pitch * height:
            raise TextureDecodeError("KTX2 level 0 payload too small")
        image = _decode_ktx_pixels(payload, width, height, fmt, 0x1401, "<")
    return Image.fromarray(np.ascontiguousarray(image), "RGBA")


def _valid_dimensions(width: int, height: int) -> bool:
    return 0 < width <= _MAX_DIMENSION and 0 < height <= _MAX_DIMENSION


def _u32(data: bytes, offset: int, endian: str = "<") -> int:
    byteorder = "little" if endian == "<" else "big"
    return int.from_bytes(data[offset:offset + 4], byteorder, signed=False)


__all__ = [
    "DDS_MAGIC",
    "KTX1_MAGIC",
    "KTX2_MAGIC",
    "TextureDecodeError",
    "decode_dds",
    "decode_ktx",
    "decode_ktx2",
    "decode_texture_data",
]