"""Generic, engine-agnostic decryption core.

DualForge supports more key types than plain AES-256. The ``dualforge.encryption``
package models any game/scheme encryption as an ordered pipeline of transform
stages (AES -> custom XOR -> derived-key -> partial-entry override) and provides
a registry so a new game is a small, isolated module.

Key-type vocabulary (also mirrored in ``unreal.keys`` ``KeyEntry.scheme``):

- ``aes-256``             standard single-key AES-256 (ECB), the common case
- ``aes-256+dynamic``     per-archive AES keys selected by GUID
- ``aes-256+xor8``        AES followed by an 8-byte repeating XOR (Delta Force)
- ``xor``                 repeating-key XOR (Unity bundles, many CN games)
- ``derived``             key material derived at runtime (Snowbreak, Star Savior)
- ``unity-cn``            Unity CN Pro 16-char bundle key
- ``sm4`` / ``sm4-abi``   SM4 with ABI/Tencent key tables (Arena Breakout)
- ``partial``             partially-encrypted paks (Wuthering Waves / NetEase)
- per-game pseudo-AES     custom round-key / S-box implementations
"""

from dualforge.encryption.registry import (
    Transformer,
    get_scheme,
    list_schemes,
    register,
    transform,
)

from dualforge.encryption import schemes as _schemes  # noqa: F401   (self-register)

__all__ = [
    "Transformer",
    "get_scheme",
    "list_schemes",
    "register",
    "transform",
]
