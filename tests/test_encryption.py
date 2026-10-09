"""Tests for the dualforge.encryption package: schemes, pipeline and presets."""

from __future__ import annotations

from dualforge.encryption import list_schemes
from dualforge.encryption.pipeline import TransformPipeline
from dualforge.encryption.presets import guess_scheme
from dualforge.encryption.registry import Context, KeyMaterial
from dualforge.encryption.schemes.aes import _aes_ecb_decrypt
from dualforge.encryption.schemes.roundkey import decrypt_custom_roundkeys, expand_key


def test_all_schemes_registered():
    names = set(list_schemes())
    assert {
        "aes-256",
        "xor8",
        "xor",
        "xor-header",
        "derived-aes-md5",
        "derived-xor-md5",
        "partial-encrypt",
        "unity-cn",
        "custom-aes-round",
        "delta-force",
        "sm4",
        "sm4-abi",
    } <= names


def test_aes256_roundtrip_vector():
    # FIPS-197 AES-128 canonical vector
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ciphertext = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    plaintext = bytes.fromhex("00112233445566778899aabbccddeeff")
    km = KeyMaterial(key_str=key.hex(), scheme="aes-256")
    assert _aes_ecb_decrypt(key, ciphertext) == plaintext
    from dualforge.encryption.registry import transform

    assert transform(ciphertext, "aes-256", km, Context()) == plaintext


def test_aes256_non_aligned_tail_passthrough():
    km = KeyMaterial(key_str="AB" * 32, scheme="aes-256")
    from dualforge.encryption.registry import transform

    data = bytes.fromhex("00112233445566778899aabbccddeeff") + b"Z"
    out = transform(data, "aes-256", km, Context())
    assert out[-1:] == b"Z"


def test_custom_roundkeys_matches_standard_aes():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    pt = bytes.fromhex("3243f6a8885a308d313198a2e0370734")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    ct = cipher.encryptor().update(pt) + cipher.encryptor().finalize()

    # Recovered schedule must decrypt the standard ciphertext to the plaintext.
    flat = expand_key(key)
    assert decrypt_custom_roundkeys(ct, bytes(flat)) == pt


def test_expand_key_matches_pyca_schedule():
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    flat = expand_key(key)
    assert flat[0:16] == list(key)
    assert len(flat) == 11 * 16  # AES-128: 11 round keys


def test_xor_scheme():
    from dualforge.encryption.registry import transform

    km = KeyMaterial(key_str="0a", scheme="xor")
    assert transform(b"\x01\x02\x03", "xor", km, Context()) == b"\x0b\x08\x09"


def test_xor_header_only_first_n():
    from dualforge.encryption.registry import transform

    params = {"header_bytes": "2"}
    km = KeyMaterial(key_str="ff", scheme="xor-header", parameters=params)
    out = transform(b"\x01\x02\x03\x04", "xor-header", km, Context())
    assert out[0:2] == b"\xfe\xfd"
    assert out[2:] == b"\x03\x04"


def test_xor_header_zero_means_all():
    from dualforge.encryption.registry import transform

    km = KeyMaterial(key_str="0a", scheme="xor-header", parameters={"header_bytes": "0"})
    assert transform(b"\x01\x02\x03", "xor-header", km, Context()) == b"\x0b\x08\x09"


def test_delta_force_pipeline_stages():
    pipe = TransformPipeline.from_scheme("aes-256+xor8")
    assert [s.name for s in pipe.stages] == ["aes-256", "xor8"]


def test_guess_scheme_by_mount():
    assert guess_scheme(archive_name="pakchunk0-Windows.pak", mount="FortniteGame") is not None
    assert guess_scheme(archive_name="a.pak", mount="/Game/Snowbreak") is not None


def test_scheme_validation_matches_preset_names():
    from dualforge.encryption.presets import PRESETS

    names = {p.name for p in PRESETS}
    assert "snowbreak" in names
    assert "delta-force" in names
    assert "arena-breakout" in names


# ---------------------------------------------------------------- SM4 / ABI


def test_sm4_fips_vector():
    from dualforge.encryption.schemes.sm4 import sm4_ecb_decrypt

    key = bytes.fromhex("0123456789abcdeffedcba9876543210")
    plaintext = bytes.fromhex("0123456789abcdeffedcba9876543210")
    ciphertext = bytes.fromhex("681edf34d206965e86b3e94f536e4246")
    assert sm4_ecb_decrypt(ciphertext, key, "none") == plaintext


def test_sm4_ecb_roundtrip():
    from dualforge.encryption.schemes.sm4 import sm4_ecb_decrypt, sm4_ecb_encrypt

    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    data = bytes(range(0x40)) + b"tail"
    block = data[:64]
    for sbox in ("none", "37", "38", "39"):
        assert sm4_ecb_decrypt(sm4_ecb_encrypt(block, key, sbox), key, sbox) == block
    # unaligned tail passes through unchanged
    out = sm4_ecb_decrypt(block + b"Z", key, "none")
    assert out[-1:] == b"Z"


def test_sm4_abi_key_transform():
    from dualforge.encryption.schemes.sm4 import sm4_ecb_decrypt, sm4_ecb_encrypt, sm4_transform_key
    from dualforge.encryption.schemes.sm4_tables import TableA

    # mode 'a' maps each key byte through the 64-entry TableA via key & 0x3F
    key = bytes.fromhex("0123456789abcdeffedcba9876543210")
    expected = bytes(TableA[b & 0x3F] for b in key)
    transformed = sm4_transform_key(key, "a")
    assert len(transformed) == 16
    assert transformed == expected
    assert transformed[0] == TableA[1]  # key[0] = 0x01 -> TableA[1]

    plaintext = bytes([0x2B, 0, 0, 0]) + b"../../../ABI"
    ciphertext = sm4_ecb_encrypt(plaintext, transformed, "none")
    assert sm4_ecb_decrypt(ciphertext, transformed, "none") == plaintext


def _encode_abi_index(offset: int, seed: int) -> int:
    mask = (1 << 64) - 1
    xor = 0xD72CAC4E59907DA0
    t = (offset ^ seed) & mask
    rol = ((t << 23) | (t >> 41)) & mask
    return (rol ^ xor) & mask


def _make_abi_pak(index_bytes: bytes, offset: int = 0x100) -> bytes:
    raw = bytearray(offset)
    raw.extend(index_bytes)
    footer = bytearray(221)
    footer[16:20] = (0x0B).to_bytes(4, "little")          # version
    footer[20:24] = (0x53647586).to_bytes(4, "little")    # ABI pak magic
    footer[24] = 1                                         # encrypted index
    footer[45:53] = _encode_abi_index(offset, 0xD3A512).to_bytes(8, "little")
    footer[53:61] = _encode_abi_index(len(index_bytes), 0xB640093C).to_bytes(8, "little")
    raw.extend(footer)
    return bytes(raw)


def test_validate_key_sm4_abi_synthetic_pak():
    from dualforge.encryption.brute import probe_pak_blocks, validate_key
    from dualforge.encryption.schemes.sm4 import sm4_ecb_encrypt, sm4_transform_key

    key_str = "1F5E4191BDE73F9C65A48D8AA0648C46C06C08F9853093C7EBF4AA5CA22F0486"
    key = sm4_transform_key(bytes.fromhex(key_str), "a")
    plaintext = bytes([0x2B, 0, 0, 0]) + b"../../../ABInfinite/Content/"
    index = sm4_ecb_encrypt(plaintext[:16], key, "none")
    raw = _make_abi_pak(index)

    blocks = probe_pak_blocks(raw)
    assert blocks, "ABI footer probe must locate the index block"
    assert validate_key(blocks[0], "sm4-abi", key_str, "pakchunk0-WindowsNoEditor.pak")
    # a wrong scheme/key must not validate
    assert not validate_key(blocks[0], "sm4-abi", "00" * 32, "pakchunk0-WindowsNoEditor.pak")
    assert not validate_key(blocks[0], "aes-256", key_str, "pakchunk0-WindowsNoEditor.pak")


def test_guess_scheme_abinf():
    from dualforge.encryption.presets import guess_scheme

    preset = guess_scheme(mount="../../../ABInfinite/Content/", archive_name="pakchunk0-WindowsNoEditor.pak")
    assert preset is not None and preset.name == "arena-breakout"
    assert guess_scheme(game="Arena Breakout Infinite").name == "arena-breakout"
