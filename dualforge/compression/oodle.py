"""The single Oodle loader for DualForge.

Policy: the Oodle DLL is never downloaded and never bundled. It always comes
from the game the user already has, or from a location the user pointed us at.

The search deliberately **excludes the current working directory and bare
``PATH`` entries**. DualForge's normal workflow is "open the archive someone
sent me", and both of those are attacker-writable in that situation - dropping
an ``oo2core_9_win64.dll`` next to the downloaded pak, or anywhere on ``PATH``,
would otherwise be code execution inside this process.

``dualforge.vendor.pyuepak.oodle`` used to carry a second, stricter loader and
delegate here; that split let the permissive path below stay reachable. There is
now exactly one implementation, and it is the strict one.
"""

from __future__ import annotations

import ctypes
import glob
import os
import threading
from pathlib import Path

__all__ = [
    "Oodle",
    "OodleDecompressError",
    "OodleUnavailableError",
    "find_oodle_dll",
    "set_search_root",
]


class OodleUnavailableError(Exception):
    pass


class OodleDecompressError(Exception):
    pass


OODLE_PATTERNS = (
    "oo2core_*_win64.dll",
    "oo2core_*_linux64.so",
    "oo2core_*_mac64.dylib",
)

#: Sub-paths under a game folder where the DLL commonly lives.
_GAME_SUBDIRS = (
    "Binaries/Win64",
    "Binaries/Win32",
    "Engine/Binaries/Win64",
    "Binaries/ThirdParty/Oodle/Win64",
    "Engine/Binaries/ThirdParty/Oodle/Win64",
)

#: How far up from an archive to walk when looking for a game folder.
_MAX_PARENT_WALK = 3

#: Ceiling on a single Oodle output buffer. ``output_size`` comes from the
#: archive, so an unvalidated value would let a 12-byte crafted entry ask for a
#: 4 GiB allocation. Real cooked assets are far below this.
_MAX_OUTPUT_SIZE = 2 * 1024 * 1024 * 1024


class _SearchRoot(threading.local):
    """Per-thread record of the archive currently being read.

    A thread-local starts with no attributes, so ``path`` is declared here to
    give fresh threads the same ``None`` default the main thread has.
    """

    path: str | None = None


_SEARCH_ROOT = _SearchRoot()

_NOT_FOUND = (
    "Oodle library (oo2core_*) not found. DualForge never downloads it - copy it "
    "from the game's Binaries/Win64 folder into ~/.dualforge, or set "
    "DUALFORGE_OODLE to its full path."
)


def set_search_root(archive_path: str | None) -> None:
    """Record the archive being read, so its game folder chain can be searched.

    The DLL normally sits beside the game rather than beside the pak, so knowing
    which archive is open is what makes the restricted search sufficient. This is
    a plain thread-local, matching how the pak reader uses it.
    """
    _SEARCH_ROOT.path = archive_path


def _configured_dll() -> Path | None:
    """Honour ``DUALFORGE_OODLE`` when it names a real file.

    The user is told in the error message to "point DUALFORGE_OODLE at its full
    path", and games (or mod managers) sometimes rename the DLL, so an explicit
    file must win outright rather than be filtered out by the ``oo2core_*``
    glob patterns. Falls back to treating the value as a directory to search.
    """
    configured = os.environ.get("DUALFORGE_OODLE", "").strip()
    if not configured:
        return None
    path = Path(configured)
    try:
        if path.is_file():
            return path
    except OSError:
        return None
    return None


def _candidate_dirs() -> list[str]:
    """Directories to search, most specific first.

    CWD and ``PATH`` are intentionally absent - see the module docstring.
    """
    dirs: list[str] = []

    configured = os.environ.get("DUALFORGE_OODLE", "").strip()
    if configured and not Path(configured).is_file():
        dirs.append(configured)

    archive_path = _SEARCH_ROOT.path
    if archive_path:
        current = Path(archive_path).resolve().parent
        for _ in range(_MAX_PARENT_WALK):
            dirs.append(str(current))
            for sub in _GAME_SUBDIRS:
                dirs.append(str(current / sub))
            if current.parent == current:
                break
            current = current.parent

    dirs.append(str(Path(__file__).resolve().parent))
    # Common game installs (Windows)
    home = Path.home()
    dirs.extend(
        [
            str(home / ".dualforge"),
            str(Path("C:/Program Files (x86)/Steam/steamapps/common")),
            str(Path("C:/Program Files/Steam/steamapps/common")),
            str(Path("C:/SteamLibrary/steamapps/common")),
            str(Path("D:/SteamLibrary/steamapps/common")),
            str(Path("E:/SteamLibrary/steamapps/common")),
            str(Path("F:/SteamLibrary/steamapps/common")),
            str(Path("C:/Program Files/Epic Games")),
            str(Path("C:/Program Files (x86)/Epic Games")),
            str(Path("C:/Program Files/GOG Galaxy/Games")),
            str(Path("C:/Program Files (x86)/GOG Galaxy/Games")),
            str(Path("C:/GOG Games")),
        ]
    )

    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def find_oodle_dll() -> Path | None:
    """Locate a usable Oodle DLL, or return None.

    Deliberately not memoised globally: the search is derived from the archive
    currently being read, so a cache would hand game A's DLL to game B. The
    per-instance lazy load in :class:`Oodle` is where the reuse lives instead.
    """
    configured = _configured_dll()
    if configured is not None:
        return configured
    for directory in _candidate_dirs():
        for pattern in OODLE_PATTERNS:
            for path in glob.glob(os.path.join(directory, pattern)):
                try:
                    candidate = Path(path)
                    if candidate.is_file():
                        return candidate
                except OSError:
                    continue
    return None


class Oodle:
    _SIGNATURES = (
        [
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
        ],
        [
            ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32,
        ],
    )

    def __init__(self, dll_path: str | None = None, search_paths: list[str] | None = None):
        self.path: str | None = None
        self._lib: ctypes.CDLL | None = None
        self._load(dll_path, search_paths)

    def _candidates(self, extra_dirs: list[str] | None) -> list[str]:
        """Absolute paths to try, in priority order.

        An explicit ``dll_path``/``search_paths`` is the caller telling us where
        the DLL is; honouring it does not widen anything else, it just replaces
        the derived list.
        """
        dirs = list(extra_dirs) if extra_dirs else _candidate_dirs()

        seen: set[str] = set()
        out: list[str] = []
        for d in dirs:
            for pattern in OODLE_PATTERNS:
                for path in glob.glob(os.path.join(d, pattern)):
                    key = os.path.abspath(path)
                    if key not in seen:
                        seen.add(key)
                        out.append(key)
        return out

    def _load(self, dll_path: str | None, search_paths: list[str] | None) -> None:
        if dll_path:
            if not os.path.isfile(dll_path):
                raise OodleUnavailableError(
                    f"the configured Oodle DLL does not exist: {dll_path}"
                )
            candidates = [dll_path]
        else:
            candidates = self._candidates(search_paths)

        for path in candidates:
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            if not hasattr(lib, "OodleLZ_Decompress"):
                continue
            self.path = path
            self._lib = lib
            return
        raise OodleUnavailableError(_NOT_FOUND)

    def decompress(self, data: bytes, output_size: int) -> bytes:
        if not data:
            return b""
        if output_size < 0:
            raise OodleDecompressError(f"invalid Oodle output size: {output_size}")
        if output_size > _MAX_OUTPUT_SIZE:
            raise OodleDecompressError(
                f"refusing to allocate {output_size} bytes for an Oodle entry: the "
                f"declared size exceeds the {_MAX_OUTPUT_SIZE} byte limit. The "
                f"archive is corrupt or hostile."
            )
        assert self._lib is not None  # guaranteed by _load or OodleUnavailableError
        raw_out = ctypes.create_string_buffer(output_size)
        comp_buf = ctypes.c_char_p(data)
        last_error: Exception | None = None
        for sig in self._SIGNATURES:
            func = self._lib.OodleLZ_Decompress
            func.restype = ctypes.c_int64
            func.argtypes = sig
            try:
                result = func(
                    comp_buf, len(data),
                    ctypes.cast(raw_out, ctypes.c_void_p), output_size,
                    0, 0, 0,
                    None, 0,
                    None, None,
                    None, 0, 0,
                )
            except (ctypes.ArgumentError, OSError) as exc:
                # Wrong signature variant for this DLL - try the next one.
                last_error = exc
                continue
            if result and result > 0:
                return raw_out.raw[:result]
            last_error = OodleDecompressError(
                f"OodleLZ_Decompress returned {result} (output_size={output_size})"
            )
        raise OodleDecompressError(
            f"OodleLZ_Decompress failed for {self.path or 'unknown dll'} "
            f"(output_size={output_size}); the DLL may be incompatible with this "
            f"archive. Last error: {last_error}"
        )
