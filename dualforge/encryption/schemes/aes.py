"""AES-256 decryption schemes (the common case and the GUID-keyed variant)."""

from __future__ import annotations

from dualforge.encryption.registry import Context, KeyMaterial, register


def _aes_ecb_decrypt(key_bytes: bytes, data: bytes) -> bytes:
    # AES-256 ECB = 32-byte key. Some games use AES-128; fall back if needed.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    klen = len(key_bytes)
    if klen == 32 or klen == 16:
        alg = algorithms.AES(key_bytes)
    else:
        raise ValueError(f"unsupported AES key length: {klen}")
    cipher = Cipher(alg, modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    return decryptor.update(data) + decryptor.finalize()


@register("aes-256")
def aes256(data: bytes, key: KeyMaterial, ctx: Context) -> bytes:
    """Plain AES-256 ECB. Block-aligned only; non-aligned tails pass through."""
    kb = key.hex_bytes()
    if not kb:
        return data
    if len(data) % 16 != 0:
        whole = len(data) - (len(data) % 16)
        if whole <= 0:
            return data
        return _aes_ecb_decrypt(kb, data[:whole]) + data[whole:]
    return _aes_ecb_decrypt(kb, data)
