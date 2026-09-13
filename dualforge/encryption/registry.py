"""Central registry of decryption schemes.

A scheme is a callable transformer:

    def transform(data: bytes, key: KeyMaterial, ctx: Context) -> bytes

where ``KeyMaterial`` is an object exposing ``key_str`` (raw key text/hex) and
``parameters`` (a dict of scheme-specific fields such as ``xor_byte``,
``derived_from``, ``guid``), and ``Context`` carries reader-level info such as
the archive file name and GUID (so derived/hash-keyed schemes can compute keys).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable


@dataclass
class KeyMaterial:
    """Typed view of a stored key entry handed to a transformer."""

    key_str: str = ""
    scheme: str = "aes-256"
    guid: str = ""
    parameters: dict[str, str] = field(default_factory=dict)

    def hex_bytes(self) -> bytes:
        """Because AES keys are stored as hex, decode to raw bytes.

        Falls back to the utf-8 bytes when the value is not hex (e.g. a
        16-char Unity CN key stored as plain text).
        """
        return unhex(self.key_str)


@dataclass
class Context:
    """Reader-level info a transformer may use (pak name, GUID, ...)."""

    archive_name: str = ""       # e.g. "pakchunk0-Windows.pak"
    archive_path: str = ""
    guid: str = ""               # encryption-key GUID from the footer
    index: bool = False          # True while decrypting the pak index
    offset: int = 0
    length: int = 0
    extra: dict[str, str] = field(default_factory=dict)


Transformer = Callable[[bytes, KeyMaterial, Context], bytes]

_REGISTRY: dict[str, Transformer] = {}


def register(name: str, fn: Transformer | None = None) -> Transformer:
    """Register a scheme transformer. Duplicate names override.

    Supports both ``register("x", fn)`` and ``@register("x")``.
    """

    def _decorator(func: Transformer) -> Transformer:
        _REGISTRY[name] = func
        return func

    if fn is None:
        return _decorator  # type: ignore[return-value]
    _REGISTRY[name] = fn
    return fn


def get_scheme(name: str) -> Transformer | None:
    return _REGISTRY.get(name)


def list_schemes() -> list[str]:
    return sorted(_REGISTRY)


def transform(data: bytes, name: str, key: KeyMaterial, ctx: Context) -> bytes:
    fn = get_scheme(name)
    if fn is None:
        return data
    return fn(data, key, ctx)


# --------------------------------------------------------------------------- utils


def unhex(value: str) -> bytes:
    """Decode a key string to bytes, tolerating ``0x`` and whitespace."""
    if not value:
        return b""
    text = str(value).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    text = text.replace(" ", "")
    try:
        if len(text) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in text):
            return bytes.fromhex(text)
    except ValueError:
        pass
    return value.encode("utf-8")


def resolve_guid(guid: str, context_guid: str, key: KeyMaterial) -> str:
    """Return the effective GUID used for dynamic-key lookup (fallback logic)."""
    return guid or context_guid or ""
