from __future__ import annotations

import glob
import logging
import os
import sys
import threading
import types
from pathlib import Path

from dualforge.constants import PAK_MAGIC
from dualforge.log import get_logger

logger = get_logger(__name__)


class PakError(Exception):
    pass


class _OodleContext(threading.local):
    archive_path: str | None = None


_oodle_ctx = _OodleContext()


class _GameOodle:
    """Lazy Oodle decompressor backed by a locally-provided oo2core DLL.

    pyuepak's ``oodle`` submodule fetches ``oo2core_9_win64.dll`` from the
    internet at import time. DualForge's policy is that the Oodle DLL is never
    bundled and never downloaded - it always comes from the game itself. We
    pre-register a stub module in ``sys.modules`` before the pyuepak package
    is imported, so pyuepak uses this lazy resolver instead. The resolver
    searches the open archive's directory chain first (the DLL usually sits
    next to the game), then the usual locations.
    """

    def decompress(self, data: bytes, output_size: int) -> bytes:
        path = _find_game_oodle(_oodle_ctx.archive_path)
        if path is None:
            raise PakError(
                "This pak uses Oodle compression and requires the game's "
                "oo2core_*.dll. Searched: the pak's own folder chain, the "
                "working directory, ~/.dualforge and PATH. Copy the DLL from "
                "the game's Binaries/Win64 folder into ~/.dualforge if needed."
            )
        from dualforge.compression.oodle import Oodle

        return Oodle(dll_path=str(path)).decompress(data, output_size)


class _OodleInitializationFailed(Exception):
    pass


def _make_oodle_stub(module_name: str = "pyuepak.oodle") -> types.ModuleType:
    """Build a stand-in for pyuepak's ``oodle`` module.

    Works in source and frozen (PyInstaller) environments alike - no files are
    read from disk, so it cannot trigger the DLL download.
    """
    module = types.ModuleType(module_name)

    def fetch_oodle() -> Path:
        raise _OodleInitializationFailed(
            "no local oo2core_*.dll found; supply the game's Oodle DLL"
        )

    module.fetch_oodle = fetch_oodle
    module.InitializationFailed = _OodleInitializationFailed
    module.oodle = lambda: _GameOodle()
    return module


def _preload_oodle_patch() -> None:
    if "pyuepak.oodle" not in sys.modules:
        sys.modules["pyuepak.oodle"] = _make_oodle_stub()


_OODLE_PATTERNS = ("oo2core_*_win64.dll", "oo2core_*_linux64.so", "oo2core_*_mac64.dylib")


def _find_game_oodle(archive_path: str | None = None) -> Path | None:
    """Locate a game-shipped Oodle DLL.

    Search order: the open archive's folder chain (up to 3 parent levels plus
    common Binaries subpaths - the DLL ships next to the game executable),
    then the working directory, ~/.dualforge, and PATH.
    """
    seen: set = set()
    dirs: list[str] = []
    if archive_path:
        current = Path(archive_path).resolve().parent
        for _ in range(3):
            dirs.append(str(current))
            for sub in ("Binaries/Win64", "Binaries/Win32", "Engine/Binaries/Win64", "Binaries/ThirdParty/Oodle/Win64"):
                dirs.append(str(current / sub))
            if current.parent == current:
                break
            current = current.parent
    dirs += [os.getcwd(), os.path.expanduser("~/.dualforge")]
    dirs += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for d in dirs:
        for pattern in _OODLE_PATTERNS:
            for path in glob.glob(os.path.join(d, pattern)):
                key = os.path.abspath(path)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if Path(path).is_file():
                        return Path(path)
                except OSError:
                    continue
    return None


def _import_pyuepak():
    _preload_oodle_patch()
    try:
        from pyuepak import PakFile
    except ImportError as exc:
        raise PakError(
            "pyuepak is required for native .pak support (pip install pyuepak)"
        ) from exc
    logging.getLogger("pyuepak").disabled = True
    return PakFile


_preload_oodle_patch()


def _probe_key_list(aes_key: str | None, try_all_keys: bool) -> list[tuple]:
    """Build the ordered (title, key) probe list: no-key first, then the
    stored key store, then the explicitly provided/default key.

    Keys whose scheme isn't plain ``aes-256`` (xor/derived/custom/unity-cn)
    are returned with a ``(entry, False)`` style marker so the caller can skip
    blindly handing them to pyuepak's single-key ``set_key`` - those archives
    need the CUE4Parse bridge. Returns list of ``(title, key, can_set_key)``.
    """
    probes: list[tuple] = [(None, None, True)]
    seen_keys = {None}
    if try_all_keys:
        try:
            from dualforge.unreal import KeyStore

            for entry in KeyStore().list():
                key = (entry.aes_key or "").strip()
                scheme = getattr(entry, "scheme", None)
                can_set = _is_aes_keyable(scheme, key)
                if key and can_set and key not in seen_keys:
                    probes.append((entry.title, key, True))
                    seen_keys.add(key)
        except Exception:
            logger.debug("key-store probing unavailable", exc_info=True)
    if aes_key:
        key = aes_key.strip()
        if key and key not in seen_keys:
            probes.append(("default", key, True))
    return probes


def _is_aes_keyable(scheme: str | None, key: str) -> bool:
    """True when a key should be handed to pyuepak's single-key ``set_key``.

    Only plain AES-256 (or absent scheme) with a plausible note is keyable;
    xor/derived/custom/unity schemes and non-hex keys go through the bridge.
    """
    s = (scheme or "aes-256").lower()
    if s not in ("aes-256", "aes-256+dynamic", "fortnite", "huwei"):
        return False
    cleaned = key.lower().replace("0x", "", 1).replace(" ", "")
    if not cleaned or len(cleaned) < 32:
        return False
    return all(c in "0123456789abcdef" for c in cleaned)


def pak_footer_version(path: str) -> int | None:
    """Read the pak footer version without opening the archive (best effort).

    Returns the PakVersion enum value (e.g. 13 for a V12/UE 5.4+ archive),
    or None when the file is not a readable pak.
    """
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, 2)
            for back in (44, 172, 204, 205):
                if size < back + 8:
                    continue
                fh.seek(size - back)
                magic = int.from_bytes(fh.read(4), "little")
                if magic != PAK_MAGIC:
                    continue
                stored = int.from_bytes(fh.read(4), "little")
                if back in (44, 172):
                    return stored
                return stored + 1
    except OSError:
        return None
    return None


class PakArchive:
    """Read-only access to an Unreal .pak archive via pyuepak.

    Opening tries the key store automatically (every stored key), then the
    explicitly provided/default key, and records which key unlocked the
    archive in ``key_title`` / ``key_source``.
    """

    def __init__(
        self,
        path: str,
        aes_key: str | None = None,
        try_all_keys: bool = True,
    ):
        PakFile = _import_pyuepak()
        self.path = str(path)
        self.version = 0
        self.is_encrypted = False
        self.key_title: str | None = None
        self.key_source: str | None = None
        self._lock = threading.Lock()
        self._pak = self._open(PakFile, aes_key, try_all_keys)
        self._entries: dict[str, int] = self._read_sizes()

    def _open(self, PakFile, aes_key: str | None, try_all_keys: bool):
        probes = _probe_key_list(aes_key, try_all_keys)
        attempts = []
        _oodle_ctx.archive_path = self.path
        try:
            for title, key, can_set in probes:
                attempts.append(title or "no key")
                pak = PakFile()
                if key and can_set:
                    try:
                        pak.set_key(key)
                    except ValueError as exc:
                        raise PakError(f"invalid AES key: {exc}") from exc
                try:
                    pak.read(self.path)
                except Exception:
                    logger.debug("key %r did not open %s", title, self.path, exc_info=True)
                    continue
                if pak.count == 0:
                    continue
                self._pak_footer = getattr(pak, "_footer", None)
                if self._pak_footer is not None:
                    self.version = getattr(self._pak_footer, "version", 0)
                    self.is_encrypted = bool(getattr(self._pak_footer, "is_encrypted", False))
                if title:
                    self.key_title = title
                    self.key_source = "key store" if title != "default" else "default key"
                return pak
        finally:
            _oodle_ctx.archive_path = None
        tried = ", ".join(attempts)
        chunk_hint = ""
        footer_hint = pak_footer_version(self.path)
        if footer_hint and footer_hint >= 13:
            chunk_hint = (
                "\n\nThis pak looks like a UE 5.4+ archive. Newer games may use "
                "per-chunk encryption keys, which require a CUE4Parse-based CLI "
                "for full support (DualForge falls back to it automatically)."
            )
        raise PakError(
            f"failed to read the pak index with {len(probes) - 1} key(s) tried "
            f"({tried}). The archive may be encrypted - add its key via "
            f"File > Manage Keys or paste it into Settings.{chunk_hint}"
        )

    def _read_sizes(self) -> dict[str, int]:
        index = getattr(self._pak, "_index", None)
        entrys = getattr(index, "entrys", None) if index is not None else None
        if not isinstance(entrys, dict):
            return {}
        sizes: dict[str, int] = {}
        for path, entry in entrys.items():
            size = getattr(entry, "size", 0) or 0
            if size:
                sizes[path] = int(size)
        return sizes

    def list_files(self) -> list[str]:
        with self._lock:
            return list(self._pak.list_files())

    def size_of(self, path: str) -> int:
        return self._entries.get(path, 0)

    def read_file(self, path: str) -> bytes:
        with self._lock:
            candidates = [path, path.lstrip("/")]
            _oodle_ctx.archive_path = self.path
            try:
                for candidate in candidates:
                    try:
                        return self._pak.read_file(candidate)
                    except KeyError:
                        continue
                    except Exception as exc:
                        raise PakError(f"failed to read '{path}' from pak: {exc}") from exc
                raise PakError(f"file not found in pak: {path}")
            finally:
                _oodle_ctx.archive_path = None

    def extract_file(self, path: str, out_dir: str) -> str:
        from dualforge.export.exporter import write_entry

        return write_entry(out_dir, _rel_path(path), self.read_file(path))

    def close(self) -> None:
        pass


def _rel_path(path: str) -> str:
    cleaned = Path(path.replace("\\", "/")).as_posix().lstrip("/")
    return cleaned or "unnamed.bin"


__all__ = ["PakArchive", "PakError", "pak_footer_version"]