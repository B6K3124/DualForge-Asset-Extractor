from __future__ import annotations

import struct
from dataclasses import dataclass, field
from collections.abc import Callable

from dualforge.log import get_logger
from dualforge.unreal.process import (
    ProcessError as UsmapDumpError,
    ProcessReader,
    check_windows,
    find_process,
    list_processes as list_game_processes,
)

logger = get_logger(__name__)

# Windows-only: dumps the global FNamePool of a running UE5 game process and
# produces a CUE4Parse usmap whose name table matches the game's name pool.
# Format reference: UnrealEngine FNamePool (UE4.25+), FNameEntry = ushort
# header (length incl. NUL, wide flag) followed by UTF-8 / UTF-16LE chars.
# The header bit layout differs between engine versions and is auto-detected.

FNAME_BLOCK_SIZE = 0x10000        # 64 KB per block
FNAME_CHUNK_TABLE_SIZE = 0x4000   # 16 KB chunk table = 4096 x u32
FNAME_ANCHOR = b"None\x00ByteProperty\x00IntProperty\x00BoolProperty\x00"

_LAYOUT_MSB = "msb"    # wide flag = bit 15, length = bits 0-14 (UE4.25-UE5.0)
_LAYOUT_LSB = "lsb"    # wide flag = bit 0, length = bits 1-15 (UE5.1)
_LAYOUT_PACKED = "packed"  # length = bits 6-15, index = bits 0-5 (UE5.2+; aligned 2)

# Packed-layout FNamePool starts straight with the header bytes + canonical
# names (no NUL separators).
FNAME_ANCHOR_PACKED = b"\x1e\x01None\x10\x03ByteProperty\xc0\x02IntProperty"


@dataclass
class FNamePool:
    names: list[str] = field(default_factory=list)
    pool_base: int = 0
    block0_base: int = 0
    block_count: int = 0


def _parse_entry(block: bytes, offset: int, layout: str) -> tuple[str, int] | None:
    """Parse one FNameEntry at offset; returns (name, next_offset) or None."""
    if offset + 2 > len(block):
        return None
    header, = struct.unpack_from("<H", block, offset)
    if layout == _LAYOUT_MSB:
        wide = bool(header & 0x8000)
        length = header & 0x7FFF
    elif layout == _LAYOUT_LSB:
        wide = bool(header & 0x0001)
        length = header >> 1
    else:  # packed: length (10 bits) in bits 6-15, index in bits 0-5
        wide = False
        length = header >> 6
    if length == 0:
        return None
    offset += 2
    size = length * (2 if wide else 1)
    if offset + size > len(block):
        return None
    raw = block[offset:offset + size]
    name = raw.decode("utf-16-le", errors="replace") if wide else raw.decode("utf-8", errors="replace")
    if name.endswith("\x00"):
        name = name[:-1]
    next_offset = offset + size
    if layout == _LAYOUT_PACKED and (next_offset & 1):
        next_offset += 1  # packed entries are 2-byte aligned
    return name, next_offset


def _detect_layout(block: bytes) -> str | None:
    """Identify the header bit layout from the canonical first names."""
    expected = ("None", "ByteProperty", "IntProperty", "BoolProperty")
    for layout in (_LAYOUT_PACKED, _LAYOUT_MSB, _LAYOUT_LSB):
        offset = 0
        ok = True
        for want in expected:
            parsed = _parse_entry(block, offset, layout)
            if parsed is None or parsed[0] != want:
                ok = False
                break
            offset = parsed[1]
        if ok:
            return layout
    return None


def _walk_block(block: bytes, names: list[str], layout: str) -> int:
    """Walk FNameEntry records inside one 64 KB block; returns entry count."""
    offset = 0
    count = 0
    while offset + 2 <= len(block):
        parsed = _parse_entry(block, offset, layout)
        if parsed is None:
            break
        name, offset = parsed
        names.append(name)
        count += 1
    return count


def _walk_pool_table(
    table: bytes,
    pool_base: int,
    read_block: Callable[[int], bytes],
    layout: str,
    max_blocks: int = 4096,
) -> FNamePool:
    """Walk all blocks referenced by the chunk table (pure, testable)."""
    pool = FNamePool(pool_base=pool_base)
    entries = struct.unpack_from(f"<{min(len(table) // 4, max_blocks)}I", table)
    for offset in entries:
        if offset == 0:
            continue
        try:
            block = read_block(pool_base + offset)
        except Exception:
            logger.debug("fname pool block at offset %d unreadable", pool_base + offset, exc_info=True)
            continue
        count = _walk_block(block, pool.names, layout)
        if count == 0:
            break
        pool.block_count += 1
    return pool


def scan_fname_pool(pid: int, anchor: bytes = FNAME_ANCHOR) -> FNamePool:
    """Find the global FNamePool and walk every name in a running UE5 process."""
    check_windows()
    reader = ProcessReader(pid)
    try:
        hits = reader.scan(FNAME_ANCHOR_PACKED)
        if hits:
            return _scan_packed_pool(reader, hits[0])
        hits = reader.scan(anchor)
        if not hits:
            raise UsmapDumpError("FNamePool anchor not found (is this a UE5 game?)")
        block0_base = hits[0] - 2  # anchor starts at the 'None' entry header
        pool_base = block0_base - FNAME_CHUNK_TABLE_SIZE
        if pool_base < 0:
            raise UsmapDumpError("FNamePool chunk table out of range")
        block0 = reader.read(block0_base, FNAME_BLOCK_SIZE)
        layout = _detect_layout(block0)
        if layout is None:
            raise UsmapDumpError("could not detect FNameEntry header layout")
        table = reader.read(pool_base, FNAME_CHUNK_TABLE_SIZE)
        pool = _walk_pool_table(table, pool_base, reader.read, layout)
        pool.block0_base = block0_base
        return pool
    finally:
        reader.close()


def _scan_packed_pool(reader: ProcessReader, block0_base: int) -> FNamePool:
    """Walk the contiguous name arena of a packed (UE5.2+) FNamePool."""
    region_base, region_size = reader.region(block0_base)
    offset_in_region = block0_base - region_base
    to_read = min(region_size - offset_in_region, 1 << 24)
    if to_read <= 0:
        raise UsmapDumpError("FNamePool arena not readable")
    data = reader.read(block0_base, to_read)
    layout = _detect_layout(data[:FNAME_BLOCK_SIZE])
    if layout is None:
        raise UsmapDumpError("could not detect FNameEntry header layout")
    pool = FNamePool(pool_base=block0_base, block0_base=block0_base)
    names = pool.names
    offset = 0
    while offset + 2 <= len(data):
        parsed = _parse_entry(data, offset, layout)
        if parsed is None:
            break
        name, offset = parsed
        names.append(name)
    pool.block_count = max(1, to_read // FNAME_BLOCK_SIZE)
    return pool


def usmap_from_names(names: list[str]):
    """Build a CUE4Parse UsmapMappings whose name table is the dumped pool."""
    from dualforge.unreal.usmap import UsmapMappings

    return UsmapMappings(names=list(names))


def dump_usmap(pid: int, out_path: str, version=None, compression=None) -> FNamePool:
    """Dump a running UE5 game's name pool and write a usmap file."""
    from dualforge.unreal.usmap import UsmapCompression, UsmapVersion, build_usmap

    pool = scan_fname_pool(pid)
    if not pool.names:
        raise UsmapDumpError("no names found in FNamePool")
    mappings = usmap_from_names(pool.names)
    data = build_usmap(
        mappings,
        version=version or UsmapVersion.Latest,
        compression=compression or UsmapCompression.ZStandard,
    )
    with open(out_path, "wb") as handle:
        handle.write(data)
    return pool


def _build_test_pool():
    """Build a synthetic FNamePool (chunk table + 2 x 64 KB blocks) for tests."""
    def block_for(entries):
        block = bytearray()
        for name, wide in entries:
            raw = name.encode("utf-16-le") if wide else name.encode("utf-8")
            length = len(name) + 1  # includes NUL terminator
            block += struct.pack("<H", (length << 1) | (1 if wide else 0))  # LSB layout
            block += raw + b"\x00\x00" if wide else raw + b"\x00"
        block += b"\x00\x00"
        return bytes(block[:FNAME_BLOCK_SIZE]).ljust(FNAME_BLOCK_SIZE, b"\x00")

    block0 = block_for([
        ("None", False),
        ("ByteProperty", False),
        ("IntProperty", False),
        ("BoolProperty", False),
        ("日本語テスト", True),
        ("A" * 300, False),
    ])
    block1 = block_for([("SecondBlockName", False)])
    table = bytearray(FNAME_CHUNK_TABLE_SIZE)
    struct.pack_into("<I", table, 0, FNAME_CHUNK_TABLE_SIZE)  # block 0
    struct.pack_into("<I", table, 4, FNAME_CHUNK_TABLE_SIZE + FNAME_BLOCK_SIZE)  # block 1
    return bytes(table), {FNAME_CHUNK_TABLE_SIZE: block0, FNAME_CHUNK_TABLE_SIZE + FNAME_BLOCK_SIZE: block1}


__all__ = [
    "FNAME_ANCHOR",
    "FNAME_ANCHOR_PACKED",
    "FNamePool",
    "ProcessReader",
    "UsmapDumpError",
    "dump_usmap",
    "find_process",
    "list_game_processes",
    "scan_fname_pool",
    "usmap_from_names",
]
