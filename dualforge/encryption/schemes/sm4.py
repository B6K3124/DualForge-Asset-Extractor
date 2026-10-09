"""SM4 decryption schemes (standard GB/T 32907 plus ABI/Tencent key tables).

Dependency-free pure-Python SM4 engine, ported clean-room from the ``ABSM4``
implementation several Chinese UE titles use (Arena Breakout: Infinite and
siblings). It covers:

* optional key-transform tables (``SM4Mode`` A-F and ``MobileD``) applied to the
  first 16 key bytes as ``table[key_byte & 0x3F]``;
* alternate S-boxes (``SboxMode`` None/37/38/39 and the mobile variants);
* standard SM4 block rounds (ECB, no padding; any non-aligned tail passes
  through unchanged, matching the pak-index use case).

Registered schemes:

    sm4       generic; params ``mode`` (none/a..f/mobile-d) and ``sbox``
              (none/37/38/39/38-mobile/39-mobile/3a-mobile)
    sm4-abi   Arena Breakout: Infinite pak index (mode ``a``, sbox ``none``)
"""

from __future__ import annotations

from dualforge.encryption.registry import Context, KeyMaterial, register
from dualforge.encryption.schemes.sm4_tables import MODE_TABLES, SBOXES

_MASK32 = 0xFFFFFFFF

# Standard SM4 system parameter (FK) and fixed parameter (CK) constants.
_FK = (0xA3B1BAC6, 0x56AA3350, 0x677D9197, 0xB27022DC)
_CK = (
    0x00070E15, 0x1C232A31, 0x383F464D, 0x545B6269,
    0x70777E85, 0x8C939AA1, 0xA8AFB6BD, 0xC4CBD2D9,
    0xE0E7EEF5, 0xFC030A11, 0x181F262D, 0x343B4249,
    0x50575E65, 0x6C737A81, 0x888F969D, 0xA4ABB2B9,
    0xC0C7CED5, 0xDCE3EAF1, 0xF8FF060D, 0x141B2229,
    0x30373E45, 0x4C535A61, 0x686F767D, 0x848B9299,
    0xA0A7AEB5, 0xBCC3CAD1, 0xD8DFE6ED, 0xF4FB0209,
    0x10171E25, 0x2C333A41, 0x484F565D, 0x646B7279,
)


def _rotl32(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (32 - shift))) & _MASK32


def _tau(a: int, sbox: bytes) -> int:
    """Non-linear substitution (tau): apply the S-box to each byte."""
    return (
        (sbox[(a >> 24) & 0xFF] << 24)
        | (sbox[(a >> 16) & 0xFF] << 16)
        | (sbox[(a >> 8) & 0xFF] << 8)
        | sbox[a & 0xFF]
    )


def _t_ap(z: int, sbox: bytes) -> int:
    """Key-schedule mixer: L'(tau(z)), L'(b) = b ^ ROL13 ^ ROL23."""
    b = _tau(z, sbox)
    return b ^ _rotl32(b, 13) ^ _rotl32(b, 23)


def _t(z: int, sbox: bytes) -> int:
    """Round mixer: L(tau(z)), L(b) = b ^ ROL2 ^ ROL10 ^ ROL18 ^ ROL24."""
    b = _tau(z, sbox)
    return b ^ _rotl32(b, 2) ^ _rotl32(b, 10) ^ _rotl32(b, 18) ^ _rotl32(b, 24)


def sm4_transform_key(key: bytes, mode: str) -> bytes:
    """Apply a key-transform table to the first 16 key bytes."""
    table = MODE_TABLES[mode]
    return bytes(table[b & 0x3F] for b in key[:16])


def _expand_key(key16: bytes, sbox: bytes) -> list[int]:
    k0 = int.from_bytes(key16[0:4], "big") ^ _FK[0]
    k1 = int.from_bytes(key16[4:8], "big") ^ _FK[1]
    k2 = int.from_bytes(key16[8:12], "big") ^ _FK[2]
    k3 = int.from_bytes(key16[12:16], "big") ^ _FK[3]

    rk = [0] * 32
    rk[31] = k0 ^ _t_ap(k1 ^ k2 ^ k3 ^ _CK[0], sbox)
    rk[30] = k1 ^ _t_ap(k2 ^ k3 ^ rk[31] ^ _CK[1], sbox)
    rk[29] = k2 ^ _t_ap(k3 ^ rk[31] ^ rk[30] ^ _CK[2], sbox)
    rk[28] = k3 ^ _t_ap(rk[31] ^ rk[30] ^ rk[29] ^ _CK[3], sbox)
    for i in range(27, -1, -1):
        rk[i] = rk[i + 4] ^ _t_ap(rk[i + 3] ^ rk[i + 2] ^ rk[i + 1] ^ _CK[31 - i], sbox)
    return rk


def _process_block(buf: bytearray, off: int, rk: list[int], sbox: bytes) -> None:
    x0 = int.from_bytes(buf[off:off + 4], "big")
    x1 = int.from_bytes(buf[off + 4:off + 8], "big")
    x2 = int.from_bytes(buf[off + 8:off + 12], "big")
    x3 = int.from_bytes(buf[off + 12:off + 16], "big")

    for i in range(0, 32, 4):
        x0 ^= _t(x1 ^ x2 ^ x3 ^ rk[i], sbox)
        x1 ^= _t(x2 ^ x3 ^ x0 ^ rk[i + 1], sbox)
        x2 ^= _t(x3 ^ x0 ^ x1 ^ rk[i + 2], sbox)
        x3 ^= _t(x0 ^ x1 ^ x2 ^ rk[i + 3], sbox)

    buf[off:off + 4] = x3.to_bytes(4, "big")
    buf[off + 4:off + 8] = x2.to_bytes(4, "big")
    buf[off + 8:off + 12] = x1.to_bytes(4, "big")
    buf[off + 12:off + 16] = x0.to_bytes(4, "big")


def sm4_ecb_decrypt(data: bytes, key16: bytes, sbox_name: str = "none") -> bytes:
    """Decrypt block-aligned ``data`` in place-style; unaligned tail passes through."""
    if len(data) < 16 or len(key16) < 16:
        return data
    sbox = SBOXES.get(sbox_name, SBOXES["none"])
    rk = _expand_key(key16[:16], sbox)
    whole = len(data) - (len(data) % 16)
    out = bytearray(data)
    for off in range(0, whole, 16):
        _process_block(out, off, rk, sbox)
    return bytes(out)


def sm4_ecb_encrypt(data: bytes, key16: bytes, sbox_name: str = "none") -> bytes:
    """Encrypt block-aligned ``data`` (inverse of :func:`sm4_ecb_decrypt`).

    SM4 decrypts with the reverse key schedule, so encryption simply runs the
    rounds with the schedule reversed. Mainly used to build test fixtures.
    """
    if len(data) < 16 or len(key16) < 16:
        return data
    sbox = SBOXES.get(sbox_name, SBOXES["none"])
    rk = list(reversed(_expand_key(key16[:16], sbox)))
    whole = len(data) - (len(data) % 16)
    out = bytearray(data)
    for off in range(0, whole, 16):
        _process_block(out, off, rk, sbox)
    return bytes(out)


def _sm4_decrypt(data: bytes, key: KeyMaterial, default_mode: str, default_sbox: str) -> bytes:
    kb = key.hex_bytes()
    if len(kb) < 16:
        return data
    mode = (key.parameters.get("mode") or default_mode or "none").strip().lower()
    sbox = (key.parameters.get("sbox") or default_sbox or "none").strip().lower()
    if mode and mode != "none":
        if mode not in MODE_TABLES:
            return data
        kb = sm4_transform_key(kb, mode)
    return sm4_ecb_decrypt(data, kb[:16], sbox)


@register("sm4")
def sm4(data: bytes, key: KeyMaterial, ctx: Context) -> bytes:
    """Generic SM4 ECB. ``mode``/``sbox`` are read from key parameters."""
    return _sm4_decrypt(data, key, "none", "none")


@register("sm4-abi")
def sm4_abi(data: bytes, key: KeyMaterial, ctx: Context) -> bytes:
    """Arena Breakout: Infinite pak index (SM4, key-table A, standard S-box)."""
    return _sm4_decrypt(data, key, "a", "none")
