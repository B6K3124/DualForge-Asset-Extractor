"""Key-verification / brute-force helpers.

``validate_key`` decrypts the first block of a pak with a candidate key and
checks for a plausible Unreal Magic value. This powers the ``keys test`` CLI
command and the "test key" dialog so a user can confirm a scheme+key before
committing it.
"""

from __future__ import annotations

import os

from dualforge.constants import PAK_MAGIC
from dualforge.encryption.pipeline import build_pipeline
from dualforge.encryption.registry import Context, KeyMaterial
from dualforge.log import get_logger

logger = get_logger(__name__)

# Unreal pak magic at the start of (most) read blocks; after AES these appear
# as the footer. We detect the encrypted/decrypted markers that reveal a hit.
_ENCRYPTED_MAGIC = b"\xF5\x5D\x86\x5E"   # 0x5E865DF5 (seen at front when keyed)
_MAGIC = b"\x5D\xF5\x86\x5E"             # 0x5E86F55D (after successful AES)

# ABI (Arena Breakout: Infinite and siblings) pak footer: 221 bytes, magic at
# EOF-201. The index lives at a decoded absolute offset and starts with a UE
# FString mount point (int32 length + path), which is what scheme-aware
# validation checks for. See CUE4Parse ABIDecryption / FPakInfo.
_ABI_PAK_MAGIC = 0x53647586
_ABI_FOOTER_SIZE = 221
_ABI_INDEX_OFFSET_POS = 45        # GUID(16) ver(4) magic(4) enc(1) hash(20)
_ABI_INDEX_SIZE_POS = 53
_ABI_INDEX_XOR = 0xD72CAC4E59907DA0
_ABI_INDEX_ROR = 23
_ABI_OFFSET_SEED = 0xD3A512
_ABI_SIZE_SEED = 0xB640093C
_ABI_MASK64 = (1 << 64) - 1

# Characters that appear in a UE pak mount point / index path.
_MOUNT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/\\\\._- "
)


def _looks_like_mount_fstring(data: bytes) -> bool:
    """True if ``data`` starts with a plausible UE mount-point FString.

    UE pak indexes begin with a length-prefixed mount path such as
    ``../../../Game/Content/...``. This is a scheme-independent signal: it works
    for AES, SM4 and custom key derivations alike, so a decrypted index can be
    recognised even when the archive carries no Unreal magic.
    """
    if len(data) < 12:
        return False
    length = int.from_bytes(data[0:4], "little")
    if not 4 <= length <= 4096:
        return False
    window = data[4:16]
    if b"/" not in window and b"\\" not in window:
        return False
    return all(chr(b) in _MOUNT_CHARS for b in window)


def _looks_decrypted_chunk(data: bytes) -> bool:
    if len(data) < 4:
        return False
    # Unreal usually stores a known 4-byte value at the *end* of each 16-byte
    # block when keyed. A very strong signal is that the last 4 bytes equal the
    # magic after decrypt.
    tail = data[-4:]
    head = data[:4]
    if head == _MAGIC or tail in (_MAGIC, _ENCRYPTED_MAGIC):
        return True
    # Fallback for archives without a Unreal footer (e.g. ABI/SM4): the
    # decrypted index starts with a mount-point FString.
    return _looks_like_mount_fstring(data)


def _decode_abi_index_info(encoded: int, seed: int) -> int:
    """Invert the ABI index info obfuscation: ROR(e ^ C, 23) ^ seed."""
    value = (encoded ^ _ABI_INDEX_XOR) & _ABI_MASK64
    rotated = ((value >> _ABI_INDEX_ROR) | (value << (64 - _ABI_INDEX_ROR))) & _ABI_MASK64
    return (rotated ^ seed) & _ABI_MASK64


def _abi_index_info(raw: bytes, expected_size: int) -> tuple[int, int] | None:
    """Decode ``(offset, size)`` of the ABI index from a raw tail/footer region.

    If ``raw`` is the whole file the footer starts at ``len(raw) - 221``; when
    ``raw`` is just the 221-byte footer the footer starts at 0. ``expected_size``
    is the archive size used to sanity-check the decoded absolute offset.
    """
    if len(raw) < _ABI_FOOTER_SIZE:
        return None
    footer = len(raw) - _ABI_FOOTER_SIZE
    magic = int.from_bytes(raw[footer + 20:footer + 24], "little")
    if magic != _ABI_PAK_MAGIC:
        return None
    enc_off = int.from_bytes(raw[footer + _ABI_INDEX_OFFSET_POS:footer + _ABI_INDEX_OFFSET_POS + 8], "little")
    enc_size = int.from_bytes(raw[footer + _ABI_INDEX_SIZE_POS:footer + _ABI_INDEX_SIZE_POS + 8], "little")
    offset = _decode_abi_index_info(enc_off, _ABI_OFFSET_SEED)
    size = _decode_abi_index_info(enc_size, _ABI_SIZE_SEED)
    if offset <= 0 or offset >= expected_size or size <= 0:
        return None
    return offset, size


def _abi_index_blocks(raw: bytes, count: int) -> list[bytes]:
    """Return 16-byte-aligned candidate blocks from an ABI-format pak index.

    Parses the fixed 221-byte ABI footer (magic ``0x53647586``), decodes the
    encrypted index offset/size and reads the first blocks of the index region.
    """
    info = _abi_index_info(raw, len(raw))
    if info is None:
        return []
    offset, size = info
    end = min(len(raw), offset + min(size, 16 * count))
    blocks: list[bytes] = []
    pos = offset
    while pos + 16 <= end and len(blocks) < count:
        blocks.append(raw[pos:pos + 16])
        pos += 16
    return blocks


def probe_pak_blocks_file(path: str, count: int = 16) -> list[bytes]:
    """Like :func:`probe_pak_blocks` but seeks only the needed parts of a pak.

    Reads just the footer (221 bytes for ABI archives) or the trailing 64 KB
    region (standard paks) instead of loading a multi-GB file into memory. The
    ABI index is seek-read from its decoded absolute offset, which can sit far
    from the file tail (beyond any in-memory tail window).
    """
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size >= _ABI_FOOTER_SIZE:
            fh.seek(size - _ABI_FOOTER_SIZE)
            footer = fh.read(_ABI_FOOTER_SIZE)
            info = _abi_index_info(footer, size)
            if info is not None:
                offset, index_size = info
                fh.seek(offset)
                region = fh.read(min(index_size, 16 * count))
                return _0padded_blocks(region, count)

        trailing_start = max(0, size - 0x10000 - 16)
        fh.seek(trailing_start)
        region = fh.read(size - trailing_start)
        blocks = _blocks_with_tail_marker(region, count)
        return blocks[:count]


def _0padded_blocks(region: bytes, count: int) -> list[bytes]:
    """Slice the first ``count`` aligned 16-byte blocks (zero-pad short reads)."""
    region = region.ljust(16 * count, b"\x00")
    return [region[i:i + 16] for i in range(0, 16 * count, 16)]


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

    ABI-format paks (magic ``0x53647586``) are handled first: their footer
    carries the encrypted index offset/size directly.
    """
    abi_blocks = _abi_index_blocks(raw, count)
    if abi_blocks:
        return abi_blocks

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
