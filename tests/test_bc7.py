"""Tests for the vectorised BC7 decoder in ``dualforge.export.texture_decode``.

The reference decoder in :mod:`tests.util_bc7` is uyjulian's pure-Python
DDS decoder (MIT), verified bit-for-bit against binomialllc/bc7decomp.c.
The vectorised implementation must produce identical output for identical
input blocks, including for the mode-8 degenerate (all-zero) block.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from dualforge.export.texture_decode import TextureDecodeError, _decode_blocks, decode_dds
from util_bc7 import decode_bc7_block, flat_bc7_block, mode_byte

RNG_SEED = 0xBC7


def _oracle_image(payload: bytes, width: int, height: int) -> np.ndarray:
    """Decode a BC7 payload with the reference decoder to an ``(H, W, 4)`` array."""
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    blocks = np.zeros((bh, bw, 4, 4, 4), np.uint8)
    for n in range(bh * bw):
        block = decode_bc7_block(payload[n * 16:(n + 1) * 16])
        blocks[n // bw, n % bw] = np.frombuffer(block, np.uint8).reshape(4, 4, 4)
    image = blocks.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(image[:height, :width])


def test_bc7_random_vs_reference():
    rng = np.random.default_rng(RNG_SEED)
    payload = rng.integers(0, 256, size=9 * 16, dtype=np.uint8)
    # Force each of the 8 modes plus the degenerate all-zero block.
    for i in range(9):
        payload[i * 16] = mode_byte(i % 9)
    decoded = _decode_blocks("bc7", bytes(payload), 4, 36)
    expect = _oracle_image(bytes(payload), 4, 36)
    assert np.array_equal(decoded, expect)


def test_bc7_any_byte0_mode_selection():
    rng = np.random.default_rng(RNG_SEED)
    payload = bytes(rng.integers(0, 256, size=32, dtype=np.uint8))
    # Low bit of byte 0 chooses the mode (LSB-first); full byte 0 is legal.
    decoded = _decode_blocks("bc7", payload, 4, 4)
    expect = _oracle_image(payload, 4, 4)
    assert np.array_equal(decoded, expect)


def test_bc7_degenerate_block_is_opaque_black():
    decoded = _decode_blocks("bc7", b"\x00" * 16, 4, 4)
    assert decoded.shape == (4, 4, 4)
    assert (decoded == [0, 0, 0, 255]).all()


def test_bc7_flat_known_values():
    cases = [
        (0, 9, 5, 3, 0xFF, 0, (148, 82, 49, 255)),
        (1, 25, 15, 7, 0xFF, 0, (100, 60, 28, 255)),
        (2, 9, 5, 3, 0xFF, 0, (74, 41, 24, 255)),
        (3, 40, 20, 10, 0xFF, 0, (80, 40, 20, 255)),
        (4, 9, 5, 3, 20, 0, (74, 41, 24, 81)),
        (5, 40, 20, 10, 100, 0, (80, 40, 20, 100)),
        (6, 40, 20, 10, 100, 0, (80, 40, 20, 200)),
        (7, 9, 5, 3, 20, 0, (73, 40, 24, 162)),
    ]
    payload = b"".join(flat_bc7_block(m, r, g, b, a, pbit) for m, r, g, b, a, pbit, _ in cases)
    decoded = _decode_blocks("bc7", payload, 4, 32)
    for n, (_, _, _, _, _, _, expect) in enumerate(cases):
        block = decoded[n * 4:(n + 1) * 4]
        assert block.shape == (4, 4, 4)
        assert (block == expect).all(), f"mode {n} expected all {expect}"
        reference = np.frombuffer(decode_bc7_block(payload[n * 16:(n + 1) * 16]), np.uint8)
        assert (reference == list(expect) * 16).all()


def test_bc7_flat_pbit_variants():
    block0 = flat_bc7_block(6, 40, 20, 10, 100, pbit=0)
    block1 = flat_bc7_block(6, 40, 20, 10, 100, pbit=1)
    decoded = _decode_blocks("bc7", block0 + block1, 4, 8)
    assert (decoded[:4] == [80, 40, 20, 200]).all()
    assert (decoded[4:] == [81, 41, 21, 201]).all()
    reference = _oracle_image(block0 + block1, 4, 8)
    assert np.array_equal(decoded, reference)


def test_bc7_too_small_payload_raises():
    with pytest.raises(TextureDecodeError):
        _decode_blocks("bc7", b"\x00" * 15, 4, 4)


def _dx10_dds(width: int, height: int, dxgi: int, payload: bytes) -> bytes:
    header = bytearray(128)
    header[0:4] = b"DDS "
    header[4:8] = (124).to_bytes(4, "little")
    header[12:16] = (height).to_bytes(4, "little")
    header[16:20] = (width).to_bytes(4, "little")
    header[76:80] = (124).to_bytes(4, "little")  # pixel format size
    header[80:84] = (0x4).to_bytes(4, "little")  # DDPF_FOURCC
    header[84:88] = b"DX10"
    ext = struct.pack("<IIIII", dxgi, 3, 0, 1, 0)  # 2D, array slices = 1
    return bytes(header) + ext + payload


def test_bc7_dds_dx10_roundtrip():
    payload = flat_bc7_block(0, 9, 5, 3) * 4
    expected = [148, 82, 49, 255]
    for dxgi in (97, 98, 99):
        image = decode_dds(_dx10_dds(8, 8, dxgi, payload))
        arr = np.asarray(image.convert("RGBA"))
        assert arr.shape == (8, 8, 4)
        assert (arr == expected).all(), f"dxgi {dxgi}"


def test_bc7_dds_odd_dimensions():
    # 2x2 blocks cover 6x6; the cropped output must match the reference.
    payload = flat_bc7_block(2, 9, 5, 3) * 4
    decoded = _decode_blocks("bc7", payload, 6, 6)
    assert decoded.shape == (6, 6, 4)
    assert (decoded == [74, 41, 24, 255]).all()
    assert np.array_equal(decoded, _oracle_image(payload, 6, 6))