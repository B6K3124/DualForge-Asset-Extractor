from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass, field
from collections.abc import Callable

from dualforge.log import get_logger

logger = get_logger(__name__)

# Windows-only: dumps the global FNamePool of a running UE5 game process and
# produces a CUE4Parse usmap whose name table matches the game's name pool.
# Format reference: UnrealEngine FNamePool (UE4.25+), FNameEntry = ushort
# header (length incl. NUL, wide flag) followed by UTF-8 / UTF-16LE chars.
# The header bit layout differs between engine versions and is auto-detected.

FNAME_BLOCK_SIZE = 0x10000        # 64 KB per block
FNAME_CHUNK_TABLE_SIZE = 0x4000   # 16 KB chunk table = 4096 x u32
FNAME_ANCHOR = b"None\x00ByteProperty\x00IntProperty\x00BoolProperty\x00"

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_READABLE = (
    0x02 | 0x04 | 0x20 | 0x40 | 0x08  # READONLY, READWRITE, EXECUTE_READ, EXECUTE_READWRITE, WRITECOPY
)

_READ_CHUNK = 4 * 1024 * 1024

_LAYOUT_MSB = "msb"    # wide flag = bit 15, length = bits 0-14 (UE4.25-UE5.0)
_LAYOUT_LSB = "lsb"    # wide flag = bit 0, length = bits 1-15 (UE5.1)
_LAYOUT_PACKED = "packed"  # length = bits 6-15, index = bits 0-5 (UE5.2+; aligned 2)

# Packed-layout FNamePool starts straight with the header bytes + canonical
# names (no NUL separators).
FNAME_ANCHOR_PACKED = b"\x1e\x01None\x10\x03ByteProperty\xc0\x02IntProperty"


class UsmapDumpError(Exception):
    pass


@dataclass
class FNamePool:
    names: list[str] = field(default_factory=list)
    pool_base: int = 0
    block0_base: int = 0
    block_count: int = 0


def list_game_processes() -> list[tuple[int, str]]:
    """Return [(pid, exe)] for running processes (Windows only)."""
    _check_windows()
    from ctypes import wintypes

    class ProcessEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_ulonglong),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    handle = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if handle == wintypes.HANDLE(-1).value:
        raise UsmapDumpError("CreateToolhelp32Snapshot failed")
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(ProcessEntry32)
        if not ctypes.windll.kernel32.Process32FirstW(handle, ctypes.byref(entry)):
            raise UsmapDumpError("Process32FirstW failed")
        result: list[tuple[int, str]] = []
        while True:
            if entry.th32ProcessID > 0:
                result.append((entry.th32ProcessID, entry.szExeFile))
            if not ctypes.windll.kernel32.Process32NextW(handle, ctypes.byref(entry)):
                break
        return result
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def find_process(name: str) -> tuple[int, str]:
    """Find a process by executable name (case-insensitive, .exe optional)."""
    wanted = name.lower()
    if not wanted.endswith(".exe"):
        wanted += ".exe"
    for pid, exe in list_game_processes():
        if exe.lower() == wanted:
            return pid, exe
    raise UsmapDumpError(f"no running process named {wanted!r}")


def _check_windows() -> None:
    import sys

    if sys.platform != "win32":
        raise UsmapDumpError("usmap dump requires Windows (process memory reading)")


class _ProcessReader:
    def __init__(self, pid: int):
        from ctypes import wintypes

        class MemoryBasicInformation(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", wintypes.LPVOID),
                ("AllocationBase", wintypes.LPVOID),
                ("AllocationProtect", wintypes.DWORD),
                ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
            ]

        self._memory_basic_info = MemoryBasicInformation
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.ReadProcessMemory.argtypes = (
            wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
            ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
        )
        self._kernel32.VirtualQueryEx.argtypes = (
            wintypes.HANDLE, wintypes.LPCVOID,
            ctypes.POINTER(MemoryBasicInformation), ctypes.c_size_t,
        )
        self.handle = self._kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not self.handle:
            raise UsmapDumpError(
                f"OpenProcess failed for pid {pid} (run as admin for protected games)"
            )
        self.pid = pid

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        if not self._kernel32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        ):
            raise UsmapDumpError(f"ReadProcessMemory failed at 0x{address:X}")
        return buf.raw[:read.value]

    def readable_regions(self):
        info = self._memory_basic_info()
        address = 0
        max_address = 1 << (ctypes.sizeof(ctypes.c_void_p) * 8)
        while address < max_address:
            if self._kernel32.VirtualQueryEx(
                self.handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
            ) == 0:
                break
            if (
                info.State == MEM_COMMIT
                and (info.Protect & PAGE_READABLE)
                and info.RegionSize > 0
            ):
                yield int(info.BaseAddress or 0), int(info.RegionSize)
            address = int(info.BaseAddress or 0) + int(info.RegionSize)

    def region(self, address: int) -> tuple[int, int]:
        """Return (base, size) of the mapped region containing address."""
        info = self._memory_basic_info()
        if self._kernel32.VirtualQueryEx(
            self.handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
        ) == 0:
            raise UsmapDumpError(f"VirtualQueryEx failed at 0x{address:X}")
        return int(info.BaseAddress or 0), int(info.RegionSize)

    def scan(self, pattern: bytes) -> list[int]:
        """Find all occurrences of pattern in readable memory."""
        hits: list[int] = []
        for base, size in self.readable_regions():
            if size < len(pattern):
                continue
            offset = 0
            while offset < size:
                try:
                    chunk = self.read(base + offset, min(_READ_CHUNK, size - offset))
                except UsmapDumpError:
                    break
                if not chunk:
                    break
                start = 0
                while True:
                    found = chunk.find(pattern, start)
                    if found < 0:
                        break
                    hits.append(base + offset + found)
                    start = found + 1
                offset += len(chunk)
        return hits


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
    _check_windows()
    reader = _ProcessReader(pid)
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


def _scan_packed_pool(reader: _ProcessReader, block0_base: int) -> FNamePool:
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
    "FNamePool",
    "UsmapDumpError",
    "dump_usmap",
    "find_process",
    "list_game_processes",
    "scan_fname_pool",
    "usmap_from_names",
]
