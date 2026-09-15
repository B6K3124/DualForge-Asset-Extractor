"""Pure-Python texture container decoding (DDS / KTX1 / KTX2).

Decodes the GPU block formats most common in shipped games with no C-side
dependency: BC1 (DXT1), BC2 (DXT3), BC3 (DXT5), BC4 (RGTC1), BC5 (RGTC2),
ETC1/ETC2 RGB, ETC2 punchthrough alpha and EAC (RGBA8 / R11 / RG11),
plus uncompressed 8/16/24/32-bit RGB/RGBA/grayscale layouts. Output is
always an RGBA PIL image in top-down order.

Supported containers:

* DDS  - v1 + DX10 (BC1-BC5 + ETC/EAC DXGI codes, RGBA8/BGRA8, classic
         uncompressed masks)
* KTX1 - BC1/2/3/4/5 + ETC/EAC internal formats + uncompressed GL formats
* KTX2 - non-supercompressed files (BC1-BC5, ETC/EAC, R8G8B8A8, B8G8R8A8)

BC6H/BC7/ASTC and KTX2 supercompression are intentionally left out and
raise a descriptive error rather than producing garbage.
"""

from __future__ import annotations


import numpy as np

DDS_MAGIC = b"DDS "
KTX1_MAGIC = b"\xABKTX 11\xBB\r\n\x1A\n"
KTX2_MAGIC = b"\xABKTX 20\xBB\r\n\x1A\n"

_DDPF_ALPHAPIXELS = 0x1
_DDPF_FOURCC = 0x4
_DDPF_RGB = 0x40
_DDPF_RGBA = 0x41
_DDPF_LUMINANCE = 0x20000

_FOURCC_FORMATS: dict[bytes, str] = {
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

_DXGI_FORMATS: dict[int, str] = {
    71: "bc1",  # BC1_UNORM
    74: "bc2",  # BC2_UNORM
    77: "bc3",  # BC3_UNORM
    80: "bc4",  # BC4_UNORM
    83: "bc5",  # BC5_UNORM
    96: "etc2",  # ETC2_UNORM
    97: "etc2_a1",  # ETC2_PUNCHTHROUGH_ALPHA1_UNORM
    98: "etc2_a",  # ETC2_ALPHA8_UNORM (EAC alpha + ETC2 RGB)
    99: "eac_r11",  # EAC_R11_UNORM
    100: "eac_rg11",  # EAC_RG11_UNORM
    28: "rgba8",  # R8G8B8A8_UNORM
    87: "bgra8",  # B8G8R8A8_UNORM
    145: "etc2",  # ETC2_SRGB
    146: "etc2_a1",  # ETC2_PUNCHTHROUGH_ALPHA1_SRGB
    147: "etc2_a",  # ETC2_ALPHA8_SRGB
    148: "eac_r11s",  # EAC_R11_SNORM
    149: "eac_rg11s",  # EAC_RG11_SNORM
}

_KTX_COMPRESSED: dict[int, str] = {
    0x83F1: "bc1",  # GL_COMPRESSED_RGBA_S3TC_DXT1_EXT
    0x83F4: "bc2",  # GL_COMPRESSED_RGBA_S3TC_DXT3_EXT
    0x83F5: "bc3",  # GL_COMPRESSED_RGBA_S3TC_DXT5_EXT
    0x8DBB: "bc4",  # GL_COMPRESSED_RED_RGTC1
    0x8DBD: "bc5",  # GL_COMPRESSED_RG_RGTC2
    0x8D64: "etc2",  # GL_ETC1_RGB8_OES (ETC2 individual/differential subset)
    0x9270: "eac_r11",  # GL_COMPRESSED_R11_EAC
    0x9271: "eac_r11s",  # GL_COMPRESSED_SIGNED_R11_EAC
    0x9272: "eac_rg11",  # GL_COMPRESSED_RG11_EAC
    0x9273: "eac_rg11s",  # GL_COMPRESSED_SIGNED_RG11_EAC
    0x9274: "etc2",  # GL_COMPRESSED_RGB8_ETC2
    0x9275: "etc2",  # GL_COMPRESSED_SRGB8_ETC2
    0x9276: "etc2_a1",  # GL_COMPRESSED_RGB8_PUNCHTHROUGH_ALPHA1_ETC2
    0x9277: "etc2_a1",  # GL_COMPRESSED_SRGB8_PUNCHTHROUGH_ALPHA1_ETC2
    0x9278: "etc2_a",  # GL_COMPRESSED_RGBA8_ETC2_EAC
    0x9279: "etc2_a",  # GL_COMPRESSED_SRGB8_ALPHA8_ETC2_EAC
}

_KTX_UNCOMPRESSED: dict[int, str] = {
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

_VK_FORMATS: dict[int, str] = {
    131: "bc1",  # VK_FORMAT_BC1_RGBA_UNORM_BLOCK
    132: "bc2",  # VK_FORMAT_BC2_UNORM_BLOCK
    133: "bc3",  # VK_FORMAT_BC3_UNORM_BLOCK
    134: "bc4",  # VK_FORMAT_BC4_UNORM_BLOCK
    135: "bc5",  # VK_FORMAT_BC5_UNORM_BLOCK
    37: "rgba8",  # VK_FORMAT_R8G8B8A8_UNORM
    44: "bgra8",  # VK_FORMAT_B8G8R8A8_UNORM
    140: "etc2",  # VK_FORMAT_ETC2_R8G8B8_UNORM_BLOCK
    141: "etc2",  # VK_FORMAT_ETC2_R8G8B8_SRGB_BLOCK
    142: "etc2_a1",  # VK_FORMAT_ETC2_R8G8B8A1_UNORM_BLOCK
    143: "etc2_a1",  # VK_FORMAT_ETC2_R8G8B8A1_SRGB_BLOCK
    144: "etc2_a",  # VK_FORMAT_ETC2_R8G8B8A8_UNORM_BLOCK
    145: "etc2_a",  # VK_FORMAT_ETC2_R8G8B8A8_SRGB_BLOCK
    146: "eac_r11",  # VK_FORMAT_EAC_R11_UNORM_BLOCK
    147: "eac_r11s",  # VK_FORMAT_EAC_R11_SNORM_BLOCK
    148: "eac_rg11",  # VK_FORMAT_EAC_R11G11_UNORM_BLOCK
    149: "eac_rg11s",  # VK_FORMAT_EAC_R11G11_SNORM_BLOCK
}

_BLOCK_FORMATS = frozenset(
    {"bc1", "bc2", "bc3", "bc4", "bc5", "etc2", "etc2_a1", "etc2_a", "eac_r11", "eac_r11s", "eac_rg11", "eac_rg11s"}
)
_MAX_DIMENSION = 16384

# ETC2 differential intensity modifier table (spec table C.6). Row indexed by
# codeword 0-7, entry indexed by per-pixel index (0:+a, 1:+b, 2:-a, 3:-b).
_ETC_INTENSITY: list[tuple[int, int, int, int]] = [
    (2, 8, -2, -8),
    (5, 17, -5, -17),
    (9, 29, -9, -29),
    (13, 42, -13, -42),
    (18, 60, -18, -60),
    (24, 80, -24, -80),
    (33, 106, -33, -106),
    (47, 183, -47, -183),
]
# Table C.12: intensity modifier for non-opaque punchthrough alpha.
_ETC_INTENSITY_NONOPAQUE: list[tuple[int, int, int, int]] = [
    (0, 8, 0, -8),
    (0, 17, 0, -17),
    (0, 29, 0, -29),
    (0, 42, 0, -42),
    (0, 60, 0, -60),
    (0, 80, 0, -80),
    (0, 106, 0, -106),
    (0, 183, 0, -183),
]
# Table C.8: distance for T/H modes.
_ETC_DISTANCE = (3, 6, 11, 16, 23, 32, 41, 64)
# EAC modifier tables (spec C.11 / table 3.17.2). 16 tables x 8 indices,
# index 0:+a 1:+b 2:-a 3:-b 4:+c 5:+d 6:-c 7:-d.
_EAC_MODIFIERS: list[tuple[int, int, int, int, int, int, int, int]] = [
    (-3, -6, -9, -15, 2, 5, 8, 14),
    (-3, -7, -10, -13, 2, 6, 9, 12),
    (-2, -5, -8, -13, 1, 4, 7, 12),
    (-2, -4, -6, -13, 1, 3, 5, 12),
    (-3, -6, -8, -12, 2, 5, 7, 11),
    (-3, -7, -9, -11, 2, 6, 8, 10),
    (-4, -7, -8, -11, 3, 6, 7, 10),
    (-3, -5, -8, -11, 2, 4, 7, 10),
    (-2, -6, -8, -10, 1, 5, 7, 9),
    (-2, -5, -8, -10, 1, 4, 7, 9),
    (-2, -4, -8, -10, 1, 3, 7, 9),
    (-2, -5, -7, -10, 1, 4, 6, 9),
    (-3, -4, -7, -10, 2, 3, 6, 9),
    (-1, -2, -3, -10, 0, 1, 2, 9),
    (-4, -6, -8, -9, 3, 5, 7, 8),
    (-3, -5, -7, -9, 2, 4, 6, 8),
]


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


def _expand5551(v) -> tuple[np.ndarray, np.ndarray]:
    r = (v >> 11) & 0x1F
    g = (v >> 6) & 0x1F
    b = (v >> 1) & 0x1F
    a = v & 1
    rgb = np.stack(
        [((r << 3) | (r >> 2)), ((g << 3) | (g >> 2)), ((b << 3) | (b >> 2))],
        axis=-1,
    ).astype(np.uint8)
    return rgb, (a * 255).astype(np.uint8)


def _expand4444(v) -> tuple[np.ndarray, np.ndarray]:
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


def _etc_words(blocks: np.ndarray, first: int = 0) -> np.ndarray:
    """Read an 8-byte ETC/EAC codeword as a big-endian uint64.

    The ETC2 spec stores the block so that byte 0 holds spec bits 63..56; a
    big-endian read makes ``(w >> n) & 1`` equal spec bit ``n`` directly:
    R1=bits63..60, G1=55..52, etc. Returns ``(B,)`` uint64.
    """
    words = np.frombuffer(blocks[:, first:first + 8].tobytes(), dtype=">u8")
    return words.reshape(blocks.shape[0])


def _etc_pixel_index(words: np.ndarray) -> np.ndarray:
    """2-bit per-pixel index for individual/differential/T/H modes (B,16).

    Pixel (col x, row y) uses spec bit ``n = 4*x + y`` for the least
    significant bit and ``n + 16`` for the most significant bit.
    """
    x = np.arange(16) % 4  # column
    y = np.arange(16) // 4  # row (blocks are handed out in row-major order)
    n = 4 * x + y
    # Words hold spec bit n at bit n (big-endian); LSB at n, MSB at n+16.
    idx = ((words[:, None] >> (n[None, :] + 16) & 1) << 1)
    idx |= (words[:, None] >> n[None, :]) & 1
    return idx


def _etc_eac_index(words: np.ndarray) -> np.ndarray:
    """3-bit EAC single-channel index per pixel (B,16).

    Pixel n occupies spec bits 47-3n (MSB) .. 45-3n (LSB), i.e. value bits
    ``45 - 3*n`` up. memory-order pixel p (col0,row3) -> handled by row-major n.
    """
    n = np.arange(16, dtype=np.int64)
    n = (n % 4) * 4 + (n // 4)
    return (words[:, None] >> (45 - 3 * n)[None, :]) & 7


def _extend4(v):
    return (v << 4) | v


def _extend5(v):
    return (v << 3) | (v >> 2)


def _extend6(v):
    return (v << 2) | (v >> 4)


def _extend7(v):
    return (v << 1) | (v >> 6)


def _sign3(v: np.ndarray) -> np.ndarray:
    """Two's-complement sign-extend a 3-bit value (values 0..7)."""
    v = v.astype(np.int64)
    return np.where(v & 4, v - 8, v)


def _clamp8(v: np.ndarray) -> np.ndarray:
    return np.clip(v, 0, 255).astype(np.uint8)


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


def _decode_etc_rgb(blocks: np.ndarray, fmt: str) -> np.ndarray:
    """Decode 8-byte ETC2 RGB / punchthrough-alpha blocks -> (B,4,4,4) RGBA.

    ``fmt`` may be ``"etc2"`` (ETC1/ETC2 individual + differential + T/H +
    planar) or ``"etc2_a1"`` (punchthrough alpha variant). Implements the
    GLES 3.0 spec appendix C layout (words read big-endian).
    """
    if fmt not in ("etc2", "etc2_a1"):
        raise TextureDecodeError(f"unsupported ETC format: {fmt}")
    w = _etc_words(blocks).astype(np.int64)
    b = blocks.shape[0]

    flipbit = (w >> 32) & 1
    diffbit = (w >> 33) & 1
    cw1 = (w >> 37) & 7
    cw2 = (w >> 34) & 7

    # Punchthrough alpha: opaque bit == diffbit; non-opaque blocks drop
    # pixels with index 2 to fully transparent and use the zero-at-2 tables.
    nonopaque = (fmt == "etc2_a1") & (diffbit == 0)

    # Differential-mode codewords (spec C.5) used both for the differential
    # decode and, read through that same lens, to select T/H/planar modes.
    R = (w >> 59) & 0x1F
    G = (w >> 51) & 0x1F
    B = (w >> 43) & 0x1F
    dR = _sign3((w >> 56) & 7)
    dG = _sign3((w >> 48) & 7)
    dB = _sign3((w >> 40) & 7)
    cr = R + dR
    cg = G + dG
    cb = B + dB

    # Mode: 0=individual 1=differential 2=T 3=H 4=planar
    # (SwiftShader decodeBlock: diffbit or punch throws into the summing
    # selector; planar is never chosen for punchthrough blocks there).
    r_out = (cr < 0) | (cr > 31)
    g_out = (cg < 0) | (cg > 31)
    b_out = (cb < 0) | (cb > 31)
    mu = np.full(b, 1, np.int8)
    mu = np.where(r_out, 2, mu)
    mu = np.where((~r_out) & g_out, 3, mu)
    mu = np.where((~r_out) & (~g_out) & b_out, 4, mu)
    mu = np.where((diffbit == 0) & ~np.asarray(nonopaque, bool), 0, mu)

    index = _etc_pixel_index(w)  # (B,16)

    # --- Individual / differential base colors (8-bit extended) -----------
    r1_i = _extend4((w >> 60) & 0xF)
    g1_i = _extend4((w >> 52) & 0xF)
    b1_i = _extend4((w >> 44) & 0xF)
    r2_i = _extend4((w >> 56) & 0xF)
    g2_i = _extend4((w >> 48) & 0xF)
    b2_i = _extend4((w >> 40) & 0xF)
    r1_d = _extend5(R)
    g1_d = _extend5(G)
    b1_d = _extend5(B)
    r2_d = _extend5(np.clip(cr, 0, 31))
    g2_d = _extend5(np.clip(cg, 0, 31))
    b2_d = _extend5(np.clip(cb, 0, 31))

    # Per-block intensity table selection (opaque vs non-opaque a1).
    inten_norm = np.asarray(_ETC_INTENSITY, np.int64)
    inten_nop = np.asarray(_ETC_INTENSITY_NONOPAQUE, np.int64)
    use_nop = nonopaque.astype(bool)
    mod1 = np.where(use_nop[:, None], inten_nop[cw1], inten_norm[cw1])
    mod2 = np.where(use_nop[:, None], inten_nop[cw2], inten_norm[cw2])

    # Subblock assignment: flipbit=1 -> two 4x2 subblocks on top of each
    # other (sub0 = top rows, base1); flipbit=0 -> two 2x4 subblocks side by
    # side (sub0 = left columns, base1) per Table C.4/C.5.
    row = np.arange(16) // 4
    col = np.arange(16) % 4
    sub = np.where(
        flipbit[:, None] == 1,
        (row[None, :] >= 2).astype(np.int64),
        (col[None, :] >= 2).astype(np.int64),
    )  # (B,16) subblock0=0, subblock1=1

    # Combined 8-colour palette [sub0 base1+mod | sub1 base2+mod].
    pal_off = sub * 4 + index  # (B,16)
    for (r1, g1, b1, r2, g2, b2), slot in (
        ((r1_i, g1_i, b1_i, r2_i, g2_i, b2_i), "i"),
        ((r1_d, g1_d, b1_d, r2_d, g2_d, b2_d), "d"),
    ):
        base1 = np.stack([r1, g1, b1], axis=-1)  # (B,3)
        base2 = np.stack([r2, g2, b2], axis=-1)
        slot_c1 = base1[:, None, :] + mod1[:, :, None]  # (B,4,3)
        slot_c2 = base2[:, None, :] + mod2[:, :, None]
        pal = np.concatenate([slot_c1, slot_c2], axis=1)  # (B,8,3)
        if slot == "i":
            pal_i = pal
            pix_i = pal_i[np.arange(b)[:, None], pal_off]
        else:
            pal_d = pal
            pix_d = pal_d[np.arange(b)[:, None], pal_off]

    # --- T mode (spec C.3d) ----------------------------------------------
    r1_t = _extend4((((w >> 59) & 3) << 2) | ((w >> 56) & 3))
    g1_t = _extend4((w >> 52) & 0xF)
    b1_t = _extend4((w >> 48) & 0xF)
    r2_t = _extend4((w >> 44) & 0xF)
    g2_t = _extend4((w >> 40) & 0xF)
    b2_t = _extend4((w >> 36) & 0xF)
    da_t = (w >> 34) & 3
    db_t = (w >> 32) & 1
    d_t = np.asarray(_ETC_DISTANCE, np.int64)[(2 * da_t + db_t)]
    c0 = np.stack([r1_t, g1_t, b1_t], axis=-1)
    c2 = np.stack([r2_t, g2_t, b2_t], axis=-1)
    pal_t = np.stack([c0, c2 + d_t[:, None], c2, c2 - d_t[:, None]], axis=1)
    pix_t = pal_t[np.arange(b)[:, None], index]

    # --- H mode (spec C.3e) ----------------------------------------------
    r1_h = _extend4((w >> 59) & 0xF)
    g1_h = _extend4((((w >> 56) & 7) << 1) | ((w >> 52) & 1))
    b1_h = _extend4((((w >> 51) & 1) << 3) | (((w >> 48) & 3) << 1) | ((w >> 47) & 1))
    r2_h = _extend4((w >> 43) & 0xF)
    g2_h = _extend4((((w >> 40) & 7) << 1) | ((w >> 39) & 1))
    b2_h = _extend4((w >> 35) & 0xF)
    da_h = (w >> 34) & 1
    db_h = (w >> 32) & 1
    greater = (
        (r1_h << 16) | (g1_h << 8) | b1_h
    ) >= ((r2_h << 16) | (g2_h << 8) | b2_h)
    idx_h = (da_h << 2) | (db_h << 1) | greater.astype(np.int64)
    d_h = np.asarray(_ETC_DISTANCE, np.int64)[idx_h]
    c0h = np.stack([r1_h, g1_h, b1_h], axis=-1)
    c2h = np.stack([r2_h, g2_h, b2_h], axis=-1)
    pal_h = np.stack(
        [c0h + d_h[:, None], c0h - d_h[:, None], c2h + d_h[:, None], c2h - d_h[:, None]],
        axis=1,
    )
    pix_h = pal_h[np.arange(b)[:, None], index]

    # --- Planar mode (spec C.3g) -----------------------------------------
    ro = _extend6((w >> 57) & 0x3F)
    go = _extend7((((w >> 56) & 1) << 6) | ((w >> 49) & 0x3F))
    bo = _extend6(
        (((w >> 48) & 1) << 5) | (((w >> 43) & 3) << 3)
        | (((w >> 40) & 3) << 1) | ((w >> 39) & 1)
    )
    rh = _extend6((((w >> 34) & 0x1F) << 1) | ((w >> 32) & 1))
    gh = _extend7((w >> 25) & 0x7F)
    bh = _extend6((((w >> 24) & 1) << 5) | ((w >> 19) & 0x1F))
    rv = _extend6((((w >> 16) & 7) << 3) | ((w >> 13) & 7))
    gv = _extend7((((w >> 8) & 0x1F) << 2) | ((w >> 6) & 3))
    bv = _extend6(w & 0x3F)
    xcol = col[None, :].astype(np.int64)
    yrow = row[None, :].astype(np.int64)
    pr = (((xcol * (rh - ro)[:, None] + yrow * (rv - ro)[:, None] + 2) >> 2) + ro[:, None])
    pg = (((xcol * (gh - go)[:, None] + yrow * (gv - go)[:, None] + 2) >> 2) + go[:, None])
    pb = (((xcol * (bh - bo)[:, None] + yrow * (bv - bo)[:, None] + 2) >> 2) + bo[:, None])
    pix_p = np.stack([pr, pg, pb], axis=-1)

    # Select per-pixel colour from the five mode decoders.
    out = np.select(
        [
            mu[:, None, None] == 0,
            mu[:, None, None] == 1,
            mu[:, None, None] == 2,
            mu[:, None, None] == 3,
            mu[:, None, None] == 4,
        ],
        [pix_i, pix_d, pix_t, pix_h, pix_p],
        0,
    )

    # Alpha: opaque everywhere, except a1 non-opaque blocks drop index-2
    # pixels (spec punchthrough; planar blocks are not in a1 mode anyway).
    alpha = np.full((b, 16), 255, np.uint8)
    transparent = nonopaque[:, None] & (index == 2) & (mu[:, None] != 4)
    if transparent.any():
        alpha = np.where(transparent, 0, 255).astype(np.uint8)
        out = np.where(transparent[..., None], 0, out)

    out = np.clip(out, 0, 255)
    rgba = np.concatenate([out, alpha[..., None]], axis=-1)
    return np.ascontiguousarray(rgba.reshape(b, 4, 4, 4).astype(np.uint8))


def _decode_eac(blocks: np.ndarray, fmt: str) -> np.ndarray:
    """Decode EAC single (R11) / dual (RG11) channel blocks -> (B,4,4,4) RGBA.

    The 11-bit values are clamped per spec (unsigned 0..2047, signed -1023..
    1023) then reduced to 8 bits by a shift of 3. Signed codewords use
    two's-complement arithmetic; -128 base decodes as -127 (spec C.8).
    """
    if fmt not in ("eac_r11", "eac_r11s", "eac_rg11", "eac_rg11s"):
        raise TextureDecodeError(f"unsupported EAC format: {fmt}")
    signed = fmt.endswith("s")
    b = blocks.shape[0]

    def channel(raw: np.ndarray) -> np.ndarray:
        w = _etc_words(raw).astype(np.int64)
        base = (w >> 56) & 0xFF
        table = (w >> 48) & 0xF
        mult = (w >> 52) & 0xF
        idx = _etc_eac_index(w).astype(np.int64)
        tables = np.asarray(_EAC_MODIFIERS, np.int64)
        mod = tables[table][np.arange(b)[:, None], idx]  # (B,16)
        if signed:
            base = np.where(base > 127, base - 256, base)
            base = np.where(base == -128, -127, base)
            effect = np.where(mult == 0, 1, mult * 8)
            v = base * 8 + mod * effect[:, None]
            raw11 = np.clip(v, -1023, 1023)
            return (raw11 // 8).astype(np.uint8)  # floor for negatives
        effect = np.where(mult == 0, 1, mult * 8)
        v = base * 8 + 4 + mod * effect[:, None]
        raw11 = np.clip(v, 0, 2047)
        return (raw11 >> 3).astype(np.uint8)

    if fmt in ("eac_r11", "eac_r11s"):
        r = channel(blocks)
        out = np.zeros((b, 16, 4), np.uint8)
        out[..., 0] = r
        out[..., 3] = 255
    else:  # rg11: two 8-byte words, red first then green
        r = channel(blocks[:, :8])
        g = channel(blocks[:, 8:16])
        out = np.zeros((b, 16, 4), np.uint8)
        out[..., 0] = r
        out[..., 1] = g
        out[..., 3] = 255
    return out.reshape(b, 4, 4, 4)


def _decode_etc2_rgba(blocks: np.ndarray) -> np.ndarray:
    """16-byte EAC alpha + ETC2 RGB block (etc2_a / etc2_a_srgb)."""
    alpha = _decode_eac_channel_alpha(blocks[:, :8])
    b = blocks.shape[0]
    rgba = _decode_etc_rgb(np.ascontiguousarray(blocks[:, 8:16]), "etc2")
    out = rgba.copy()
    out[..., 3] = alpha.reshape(b, 4, 4)
    return out


def _decode_eac_channel_alpha(raw: np.ndarray) -> np.ndarray:
    """EAC 8-bit alpha channel (spec: base + mod*mult, clamped 0..255)."""
    w = _etc_words(raw).astype(np.int64)
    base = (w >> 56) & 0xFF
    table = (w >> 48) & 0xF
    mult = (w >> 52) & 0xF
    idx = _etc_eac_index(w).astype(np.int64)
    tables = np.asarray(_EAC_MODIFIERS, np.int64)
    mod = tables[table][np.arange(raw.shape[0])[:, None], idx]  # (B,16)
    v = base[:, None] + mod * mult[:, None]
    return _clamp8(v)


def _decode_blocks(fmt: str, data: bytes, width: int, height: int) -> np.ndarray:
    """Decode a full buffer of blocks into an ``(H, W, 4)`` uint8 image."""
    block_bytes = {
        "bc1": 8,
        "bc4": 8,
        "bc2": 16,
        "bc3": 16,
        "bc5": 16,
        "etc2": 8,
        "etc2_a1": 8,
        "etc2_a": 16,
        "eac_r11": 8,
        "eac_r11s": 8,
        "eac_rg11": 16,
        "eac_rg11s": 16,
    }[fmt]
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    needed = bw * bh * block_bytes
    if len(data) < needed:
        raise TextureDecodeError(
            f"{fmt} payload too small: have {len(data)} bytes, need {needed}"
        )
    blocks = np.frombuffer(data, dtype=np.uint8, count=needed).reshape(bh, bw, block_bytes)
    flat = blocks.reshape(-1, block_bytes)
    if fmt in ("bc1", "bc2", "bc3", "bc4", "bc5"):
        out = _decode_bc(flat, fmt)  # (N, 4, 4, 4)
    elif fmt in ("etc2", "etc2_a1"):
        out = _decode_etc_rgb(flat, fmt)
    elif fmt in ("eac_r11", "eac_r11s", "eac_rg11", "eac_rg11s"):
        out = _decode_eac(flat, fmt)
    elif fmt == "etc2_a":
        out = _decode_etc2_rgba(flat)
    else:
        raise TextureDecodeError(f"unsupported block format: {fmt}")
    out = out.reshape(bh, bw, 4, 4, 4)
    image = out.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    # Block format palettes arrive as int32; PIL only accepts uint8 here.
    return np.ascontiguousarray(image[:height, :width], dtype=np.uint8)


# ---------------------------------------------------------------------------
# Uncompressed decoding (mask-driven for DDS, layout-driven for KTX).


def _channel_from_mask(words: np.ndarray, mask: int) -> np.ndarray | None:
    if mask == 0:
        return None
    shift = (mask & -mask).bit_length() - 1
    nbits = bin(mask).count("1")
    v = (words & mask) >> shift
    if nbits < 8:
        shift_left = 8 - nbits
        fill = 2 * nbits - 8
        v = v << shift_left | v >> fill if fill > 0 else v << shift_left
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
        for dst, ch in zip((0, 1, 2), channels, strict=True):
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


def mask_channel_index(mask: int) -> int | None:
    """Map a 24-bit channel mask to the byte index it occupies (BGR style)."""
    if mask == 0:
        return None
    shift = (mask & -mask).bit_length() - 1
    return 2 - shift // 8


_KTX_ROW_BPP: dict[str, int] = {
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
    fmt: str | None = None
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