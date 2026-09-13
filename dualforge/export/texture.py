"""Texture import/repack helpers.

Provides lossless DDS / KTX containers for exporting PIL images as standard
GPU textures, plus convenience loading for write-back imports.
"""

from __future__ import annotations

import struct


def load_image(path: str):
    """Load an image file (png/jpg/tga/dds/ktx/...) as RGBA via Pillow.

    DDS / KTX1 / KTX2 files (which Pillow cannot open) are decoded in pure
    Python first, so repack write-backs accept GPU textures as replacement
    images.
    """
    from PIL import Image, ImageOps

    with open(path, "rb") as fh:
        head = fh.read(12)
    try:
        if head[:4] == b"DDS " or head[:12] in (
            b"\xABKTX 11\xBB\r\n\x1A\n",
            b"\xABKTX 20\xBB\r\n\x1A\n",
        ):
            from dualforge.export.texture_decode import decode_texture_data

            with open(path, "rb") as fh:
                image = decode_texture_data(fh.read())
            if image is None:
                raise ValueError(f"unrecognized texture container: {path}")
            return image
    except FileNotFoundError:
        raise
    image = Image.open(path)
    image.load()
    image = ImageOps.exif_transpose(image)
    return image.convert("RGBA") if image.mode != "RGBA" else image


def image_to_rgba_rows(image) -> list[bytes]:
    """Return per-row top-to-bottom RGBA byte rows (handles odd widths/stride)."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    raw = rgba.tobytes()
    row_len = width * 4
    return [raw[row * row_len:(row + 1) * row_len] for row in range(height)]


def _flip_rgba(image) -> bytes:
    """Bottom-up (DDS order) RGBA bytes, bytes-per-row aligned to 4."""
    width, height = image.size
    rows = image_to_rgba_rows(image)
    flipped = bytearray()
    for row in reversed(rows):
        flipped += row
        pad = (4 - (len(row) % 4)) % 4
        flipped += b"\x00" * pad
    return bytes(flipped)


def image_to_dds(image, mips: int = 0) -> bytes:
    """Encode a PIL image as an uncompressed (BGRA) DDS file.

    DDS is a lossless container readable by every GPU toolchain (Viewer,
    texconv, DirectXTex, GIMP ...).  ``mips`` = number of mip levels (0/1 =
    no mip chain); only the full-resolution level holds real data, lower levels
    are zero-filled placeholders.
    """
    width, height = image.size
    pixels = _flip_rgba(image)
    header = _dds_header(width, height, len(pixels), mips=mips)
    mip_data = b""
    if mips > 1:
        w, h = width // 2, height // 2
        while w > 0 and h > 0 and w * h > 0:
            size = w * h * 4
            mip_data += b"\x00" * size
            w //= 2
            h //= 2
    return header + pixels + mip_data


def _dds_header(width: int, height: int, pitch: int, mips: int) -> bytes:
    flags = 0x1  # DDSD_CAPS
    flags |= 0x2  # DDSD_HEIGHT
    flags |= 0x4  # DDSD_WIDTH
    flags |= 0x8  # DDSD_PITCH
    flags |= 0x1000  # DDSD_PIXELFORMAT
    if mips > 1:
        flags |= 0x20000  # DDSD_MIPMAPCOUNT
    # RGBA masks matching the stored byte order (R,G,B,A).
    r, g, b, a = 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000
    # DDSPIXELFORMAT: size, flags, fourcc, RGBBitCount, RBitMask, GBitMask,
    # BBitMask, ABitMask (exactly 32 bytes).
    pixelfmt = struct.pack("<8I", 32, 0x41, 0, 32, r, g, b, a)
    caps = 0x1000  # DDSCAPS_TEXTURE
    if mips > 1:
        caps |= 0x400008  # COMPLEX | MIPMAP
    return b"".join(
        [
            b"DDS ",
            struct.pack("<I", 124),
            struct.pack("<I", flags),
            struct.pack("<I", height),
            struct.pack("<I", width),
            struct.pack("<I", pitch),
            struct.pack("<I", 0),  # depth
            struct.pack("<I", mips if mips > 1 else 0),  # mipmap count
            b"\x00" * 44,  # reserved[11] -> 124-byte header at offset 128 total
            pixelfmt,
            struct.pack("<4I", caps, 0, 0, 0),  # caps[4]
            struct.pack("<I", 0),  # reserved2
        ]
    )


def image_to_ktx(image, mips: int = 0) -> bytes:
    """Encode a PIL image as an RGBA8 KTX1 file (single mip unless requested).

    KTX stores rows top-down (OpenGL convention), unlike DDS.
    """
    width, height = image.size
    pixels = b"".join(image_to_rgba_rows(image))
    header = struct.pack(
        "<12sIIIIIIIIIIIII",
        bytes([0xAB, 0x4B, 0x54, 0x58, 0x20, 0x31, 0x31, 0xBB, 0x0D, 0x0A, 0x1A, 0x0A]),
        0x04030201,  # endianness
        0x1401,  # glType = GL_UNSIGNED_BYTE
        1,  # glTypeSize
        0x1908,  # glFormat = GL_RGBA
        0x1908,  # glInternalFormat = GL_RGBA
        0x1908,  # glBaseInternalFormat
        width,  # pixelWidth
        height,  # pixelHeight
        0,  # pixelDepth
        0,  # numberOfArrayElements
        1,  # numberOfFaces (2D texture)
        mips if mips > 1 else 0,  # numberOfMipmapLevels
        0,  # bytesOfKeyValueData
    )
    mip_blobs = [pixels]
    if mips > 1:
        w, h = width // 2, height // 2
        while w > 0 and h > 0:
            mip_blobs.append(b"\x00" * (w * h * 4))
            w //= 2
            h //= 2
    payload = b""
    for blob in mip_blobs:
        payload += struct.pack("<I", len(blob)) + blob
    return header + payload


__all__ = ["image_to_dds", "image_to_ktx", "load_image"]