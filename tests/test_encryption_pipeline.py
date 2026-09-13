from __future__ import annotations

from dualforge.constants import PAK_MAGIC
from dualforge.encryption.brute import (
    _blocks_with_tail_marker,
    brute_force_aes,
    probe_pak_blocks,
    validate_key,
)
from dualforge.encryption.pipeline import build_pipeline
from dualforge.encryption.registry import Context, KeyMaterial

_MAGIC = b"\x5D\xF5\x86\x5E"
_ENCRYPTED_MAGIC = b"\xF5\x5D\x86\x5E"


def _key_hex() -> str:
    return "00" * 16 + "11" * 16


def _encrypted_block() -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    plain = b"\x00" * 12 + _MAGIC
    cipher = Cipher(algorithms.AES(bytes.fromhex(_key_hex())), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(plain) + enc.finalize()


def test_build_pipeline_unknown_scheme_is_empty():
    pipe = build_pipeline("nope", KeyMaterial(key_str="ab", scheme="nope"), Context())
    assert not pipe


def test_build_pipeline_multistage():
    km = KeyMaterial(key_str="ab", scheme="aes-256+xor8")
    pipe = build_pipeline("aes-256+xor8", km, Context())
    assert [s.name for s in pipe.stages] == ["aes-256", "xor8"]


def test_validate_key_rejects_bad_inputs():
    assert validate_key(b"", "aes-256", "", "") is False
    assert validate_key(b"A" * 16, "aes-256", "") is False
    assert validate_key(b"A" * 16, "aes-256", "AB") is False


def test_validate_key_hits_on_known_key():
    assert validate_key(_encrypted_block(), "aes-256", _key_hex(), "x.pak") is True


def test_validate_key_misses_on_wrong_key():
    assert validate_key(_encrypted_block(), "aes-256", "FF" * 32, "x.pak") is False


def test_brute_force_aes_returns_first_hit():
    block = _encrypted_block()
    assert brute_force_aes(block, ["FF" * 32, _key_hex()], "x.pak") == _key_hex()
    assert brute_force_aes(block, ["FF" * 32], "x.pak") is None


def test_blocks_with_tail_marker_picks_aligned_blocks():
    region = b"\x00" * 12 + _ENCRYPTED_MAGIC
    blocks = _blocks_with_tail_marker(region * 4, count=3)
    assert len(blocks) == 3
    assert all(b[12:16] == _ENCRYPTED_MAGIC for b in blocks)


def test_probe_pak_blocks_finds_footer_blocks():
    footer = PAK_MAGIC.to_bytes(4, "little")
    region = b"\x00" * 12 + _ENCRYPTED_MAGIC
    raw = region * 4 + footer
    blocks = probe_pak_blocks(raw, count=3)
    assert len(blocks) == 3
    assert all(b[12:16] == _ENCRYPTED_MAGIC for b in blocks)


def test_probe_pak_blocks_tail_scan_fallback():
    region = b"\x00" * 12 + _ENCRYPTED_MAGIC
    raw = region * 3 + b"garbage-no-footer"
    blocks = probe_pak_blocks(raw, count=2)
    assert len(blocks) == 2
    assert all(b[12:16] == _ENCRYPTED_MAGIC for b in blocks)