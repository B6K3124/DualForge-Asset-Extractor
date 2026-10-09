"""Windows process-memory reading (shared by usmap dumping and offline key cracks).

Windows-only: attaches to a running process with query/read access and exposes
the committed, readable regions of its address space for scanning. Used by:

* ``dualforge.unreal.usmap_dump`` (global FNamePool dump of a running UE5 game)
* ``dualforge.crack_process`` (runtime key-hunt for offline/session-bound keys)
"""

from __future__ import annotations

import ctypes
import sys

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
PAGE_READABLE = (
    0x02 | 0x04 | 0x20 | 0x40 | 0x08  # READONLY, READWRITE, EXECUTE_READ, EXECUTE_READWRITE, WRITECOPY
)

READ_CHUNK = 4 * 1024 * 1024


class ProcessError(Exception):  # noqa: N818 - RuntimeError would mangle the message
    pass


def check_windows() -> None:
    if sys.platform != "win32":
        raise ProcessError("process memory reading requires Windows")


def list_processes() -> list[tuple[int, str]]:
    """Return ``[(pid, exe)]`` for every running process (Windows only)."""
    check_windows()
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
        raise ProcessError("CreateToolhelp32Snapshot failed")
    try:
        entry = ProcessEntry32()
        entry.dwSize = ctypes.sizeof(ProcessEntry32)
        if not ctypes.windll.kernel32.Process32FirstW(handle, ctypes.byref(entry)):
            raise ProcessError("Process32FirstW failed")
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
    """Find a process by executable name (case-insensitive, ``.exe`` optional)."""
    wanted = name.lower()
    if not wanted.endswith(".exe"):
        wanted += ".exe"
    for pid, exe in list_processes():
        if exe.lower() == wanted:
            return pid, exe
    raise ProcessError(f"no running process named {wanted!r}")


def resolve_process(process: str | None = None, pid: int | None = None) -> tuple[int, str]:
    """Resolve a process argument to ``(pid, exe)``."""
    if pid is not None:
        return int(pid), f"pid {pid}"
    if process:
        return find_process(process)
    raise ProcessError("pass --process <game.exe> or --pid <id>")


class ProcessReader:
    """Raw memory access into one running process (Windows)."""

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
            raise ProcessError(
                f"OpenProcess failed for pid {pid} (run as admin for protected games)"
            )
        self.pid = pid

    def close(self) -> None:
        if self.handle:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        if not self._kernel32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        ):
            raise ProcessError(f"ReadProcessMemory failed at 0x{address:X}")
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
                and not (info.Protect & PAGE_NOACCESS)
                and not (info.Protect & PAGE_GUARD)
                and info.RegionSize > 0
            ):
                yield int(info.BaseAddress or 0), int(info.RegionSize)
            address = int(info.BaseAddress or 0) + int(info.RegionSize)

    def region(self, address: int) -> tuple[int, int]:
        """Return ``(base, size)`` of the mapped region containing address."""
        info = self._memory_basic_info()
        if self._kernel32.VirtualQueryEx(
            self.handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)
        ) == 0:
            raise ProcessError(f"VirtualQueryEx failed at 0x{address:X}")
        return int(info.BaseAddress or 0), int(info.RegionSize)

    def scan(self, pattern: bytes) -> list[int]:
        """Find all occurrences of pattern in readable memory."""
        hits: list[int] = []
        for base, size in self.readable_regions():
            if size < len(pattern):
                continue
            hits.extend(self.scan_region(base, size, pattern))
        return hits

    def scan_region(self, base: int, size: int, pattern: bytes) -> list[int]:
        """Find pattern occurrences inside one ``(base, size)`` region."""
        hits: list[int] = []
        if size < len(pattern):
            return hits
        offset = 0
        while offset < size:
            try:
                chunk = self.read(base + offset, min(READ_CHUNK, size - offset))
            except ProcessError:
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

    def read_chunks(self, base: int, size: int):
        """Yield ``(absolute_address, bytes)`` chunk-by-chunk over a region."""
        offset = 0
        while offset < size:
            try:
                chunk = self.read(base + offset, min(READ_CHUNK, size - offset))
            except ProcessError:
                break
            if not chunk:
                break
            yield base + offset, chunk
            offset += len(chunk)


__all__ = [
    "ProcessError",
    "ProcessReader",
    "READ_CHUNK",
    "check_windows",
    "find_process",
    "list_processes",
    "resolve_process",
]