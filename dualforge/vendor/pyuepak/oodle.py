"""Oodle binding for the vendored pak reader.

Deliberately network-free. Upstream pyuepak downloads the proprietary
``oo2core_9_win64.dll`` from a third-party GitHub repository at *import* time.
DualForge's policy is that the Oodle DLL is never downloaded and never bundled -
it always comes from the game itself.

This module therefore:

* never performs any network I/O, and
* loads the DLL lazily, so importing ``dualforge.vendor.pyuepak`` never requires
  the DLL to be present. A missing DLL is only an error when an entry actually
  needs Oodle.

The actual ``ctypes`` work is delegated to
:mod:`dualforge.compression.oodle` so there is a single Oodle loader in the
project rather than two.
"""

import ctypes
import threading
from pathlib import Path

__all__ = [
    "CompressionFailed",
    "InitializationFailed",
    "OodleError",
    "OODLE_PATTERNS",
    "find_game_oodle",
    "oodle",
    "set_archive_context",
]

from dualforge.compression.oodle import (
    OODLE_PATTERNS,
    OodleUnavailableError,
    find_oodle_dll as find_game_oodle,
    set_search_root,
)

_NOT_FOUND = (
    "This pak uses Oodle compression and requires the game's oo2core_*.dll. "
    "DualForge never downloads it. Copy the DLL from the game's "
    "Binaries/Win64 folder into ~/.dualforge, or point DUALFORGE_OODLE at it."
)


class OodleError(Exception):
    pass


class InitializationFailed(OodleError):
    pass


class CompressionFailed(OodleError):
    pass


def set_archive_context(archive_path: str | None) -> None:
    """Record (or clear) the archive whose folder chain should be searched."""
    set_search_root(archive_path)


class Oodle:
    """Lazy Oodle decompressor backed by a locally-provided ``oo2core`` DLL.

    The search itself lives in :mod:`dualforge.compression.oodle`, which is the
    single loader in the project. This only adds laziness: importing the pak
    reader must never require the DLL to be present.
    """

    def __init__(self):
        self._impl = None
        self._path: Path | None = None

    def _load(self):
        if self._impl is not None:
            return self._impl

        path = find_game_oodle()
        if path is None:
            raise InitializationFailed(_NOT_FOUND)

        from dualforge.compression.oodle import Oodle as _Oodle

        try:
            self._impl = _Oodle(dll_path=str(path))
        except OodleUnavailableError as exc:
            raise InitializationFailed(str(exc)) from exc
        self._path = path
        return self._impl

    @property
    def path(self) -> Path | None:
        return self._path

    def decompress(self, data: bytes, output_size: int) -> bytes:
        return self._load().decompress(data, output_size)

    def compress(self, data: bytes, compressor: int, level: int) -> bytes:
        raise CompressionFailed(
            "Oodle compression is not supported - DualForge only decompresses."
        )


_singleton: Oodle | None = None
_singleton_lock = threading.Lock()


def oodle() -> Oodle:
    """Return the process-wide lazy Oodle decompressor."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = Oodle()
    return _singleton


# Kept so callers that only want to assert the DLL is absent can do so without
# constructing the decompressor.
def fetch_oodle() -> Path:
    path = find_game_oodle()
    if path is None:
        raise InitializationFailed(_NOT_FOUND)
    return path


# Surface ctypes' own import-time symbol errors under our name so a missing
# export is not mistaken for a decompression failure.
try:  # pragma: no cover - trivial
    _ = ctypes.sizeof(ctypes.c_void_p)
except Exception as _exc:  # pragma: no cover - defensive
    raise InitializationFailed(str(_exc)) from _exc
