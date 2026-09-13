"""Key-verification / brute-force helpers.

``validate_key`` decrypts the first block of a pak with a candidate key and
checks for a plausible Unreal Magic value. This powers the ``keys test`` CLI
command and the "test key" dialog so a user can confirm a scheme+key before
committing it.
"""

from __future__ import annotations


from dualforge.constants import PAK_MAGIC
from dualforge.encryption.pipeline import build_pipeline
from dualforge.encryption.registry import Context, KeyMaterial
from dualforge.log import get_logger

logger = get_logger(__name__)

# Unreal pak magic at the start of (most) read blocks; after AES these appear
# as the footer. We detect the encrypted/decrypted markers that reveal a hit.
_ENCRYPTED_MAGIC = b"\xF5\x5D\x86\x5E"   # 0x5E865DF5 (seen at front when keyed)
_MAGIC = b"\x5D\xF5\x86\x5E"             # 0x5E86F55D (after successful AES)


def _looks_decrypted_chunk(data: bytes) -> bool:
    if len(data) < 4:
        return False
    # Unreal usually stores a known 4-byte value at the *end* of each 16-byte
    # block when keyed. A very strong signal is that the last 4 bytes equal the
    # magic after decrypt.
    tail = data[-4:]
    head = data[:4]
    return head == _MAGIC or tail in (_MAGIC, _ENCRYPTED_MAGIC)


def validate_key(
    block: bytes,
    scheme: str,
    key_str: str,
    archive_name: str = "",
    guid: str = "",
    parameters: dict | None = None,
) -> bool:
    """Decrypt ``block`` with the given scheme/key and return True if the magic
    matches, i.e. the key/scheme is very likely correct for this archive."""
    if not block or not key_str:
        return False
    parameters = parameters or {}
    km = KeyMaterial(
        key_str=key_str,
        scheme=scheme,
        guid=guid,
        parameters=parameters,
    )
    ctx = Context(archive_name=archive_name, guid=guid)
    pipe = build_pipeline(scheme, km, ctx)
    if not pipe:
        return False
    try:
        decrypted = pipe.apply(block, km, ctx)
    except Exception:
        logger.debug("validate_key pipeline failed for scheme %r", scheme, exc_info=True)
        return False
    return _looks_decrypted_chunk(decrypted)


def brute_force_aes(
    block: bytes,
    candidates: list[str],
    archive_name: str = "",
    guid: str = "",
) -> str | None:
    """Try each candidate AES hex key; return the first that validates."""
    for key in candidates:
        if validate_key(block, "aes-256", key, archive_name, guid):
            return key
    return None


def _blocks_with_tail_marker(region: bytes, count: int) -> list[bytes]:
    """Gather aligned 16-byte blocks whose tail equals the AES-encrypted magic.

    Encrypted Unreal index blocks carry the magic ``0x5E865DF5`` (bytes
    ``F5 5D 86 5E``) at offset 12 whether or not the pak footer is standard.
    """
    blocks: list[bytes] = []
    for off in range(0, len(region) - 16, 16):
        blk = region[off : off + 16]
        if blk[12:16] == b"\xF5\x5D\x86\x5E":
            blocks.append(blk)
            if len(blocks) >= count:
                return blocks
    return blocks


def probe_pak_blocks(raw: bytes, count: int = 16) -> list[bytes]:
    """Extract up to ``count`` 16-byte-aligned encrypted index candidate blocks.

    Walks backward from the pak footer magic, collecting aligned blocks whose
    last 4 bytes look like an AES-encrypted Unreal magic marker. These are the
    blocks ``validate_key`` checks to confirm a key/scheme.

    For archives whose footer is obfuscated/custom (no ``paK`` magic - e.g.
    Tencent/NetEase engines), it falls back to scanning the whole tail region
    for the encrypted-magic marker, so a key found via static/runtime analysis
    can still be validated.
    """
    size = len(raw)
    blocks: list[bytes] = []
    pos = size
    while pos >= 0 and len(blocks) < count:
        end = raw.rfind(PAK_MAGIC.to_bytes(4, "little"), 0, pos)
        if end < 0:
            break
        pos = end
        # index area starts just before the footer; scan the 64KB before it
        start = max(0, end - 0x10000)
        region = raw[start:end]
        blocks = _blocks_with_tail_marker(region, count)
        if len(blocks) >= count:
            return blocks
        pos = end - 1
    if blocks:
        return blocks

    # Fallback: no standard footer magic found. Scan the tail (up to a few MB)
    # for 16-byte-aligned blocks carrying the encrypted-magic tail marker.
    TAIL_WINDOW = 8 * 1024 * 1024
    start = max(0, size - TAIL_WINDOW)
    tail = raw[start:]
    blocks = _blocks_with_tail_marker(tail, count)
    return blocks[:count]
