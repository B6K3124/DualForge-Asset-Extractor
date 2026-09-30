from __future__ import annotations

import logging
import threading
from pathlib import Path

from dualforge.constants import PAK_MAGIC
from dualforge.log import get_logger

logger = get_logger(__name__)


class PakError(Exception):
    pass


def _import_pyuepak():
    """Return the vendored ``PakFile`` class.

    DualForge vendors a patched copy of pyuepak (see ``dualforge/vendor``) so
    that Zstd / LZ4 / Brotli paks decode natively and so that a footer read
    failure reports its real cause instead of being reported as an encryption
    failure. The vendored Oodle binding is lazy and network-free, so importing
    it never downloads a DLL.
    """
    try:
        from dualforge.vendor.pyuepak import PakFile
    except ImportError as exc:  # pragma: no cover - broken install
        raise PakError(
            "the vendored pak reader (dualforge.vendor.pyuepak) could not be "
            "imported; the DualForge installation looks incomplete"
        ) from exc
    logging.getLogger("pyuepak").disabled = True
    return PakFile


def _find_game_oodle(archive_path: str | None = None) -> Path | None:
    """Locate a game-shipped Oodle DLL.

    Thin wrapper over the one loader in :mod:`dualforge.compression.oodle` so
    both the pak reader and the rest of DualForge agree on where the DLL is
    looked for. Returns None when nothing is found; the caller decides how to
    report that.
    """
    from dualforge.compression.oodle import find_oodle_dll, set_search_root

    set_search_root(archive_path)
    try:
        return find_oodle_dll()
    finally:
        set_search_root(None)


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
        from dualforge.compression.oodle import set_search_root
        from dualforge.vendor.pyuepak.utils import (
            UnsupportedCompressionMethod,
            UnsupportedPakVersion,
        )

        probes = _probe_key_list(aes_key, try_all_keys)
        attempts: list[str] = []

        # Structural failures (unsupported pak version, unreadable footer,
        # unsupported compression) are independent of which key we try, so the
        # first one aborts the probe loop instead of being retried once per
        # stored key and then mislabelled as an encryption problem.
        last_error: Exception | None = None
        encryption_failures = 0

        set_search_root(self.path)
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
                except (UnsupportedPakVersion, UnsupportedCompressionMethod) as exc:
                    # Structural: the archive is not one this reader can parse.
                    # Surface the real reason instead of the misleading
                    # "may be encrypted", but keep it a PakError so callers have
                    # one exception type to catch.
                    raise PakError(str(exc)) from exc
                except Exception as exc:
                    last_error = exc
                    encryption_failures += 1
                    logger.debug(
                        "key %r did not open %s", title, self.path, exc_info=True
                    )
                    continue
                if pak.count == 0:
                    last_error = None
                    continue
                self._pak_footer = getattr(pak, "_footer", None)
                if self._pak_footer is not None:
                    self.version = getattr(self._pak_footer, "version", 0)
                    self.is_encrypted = bool(
                        getattr(self._pak_footer, "is_encrypted", False)
                    )
                if title:
                    self.key_title = title
                    self.key_source = (
                        "key store" if title != "default" else "default key"
                    )
                return pak
        finally:
            set_search_root(None)

        tried = ", ".join(attempts)

        # The archive parsed structurally but no key unlocked it, or it failed
        # in a way that only keys could explain. Say so, and keep the real
        # exception for context.
        detail = f" (last error: {last_error})" if last_error else ""
        raise PakError(
            f"failed to read the pak index with {len(probes) - 1} key(s) tried "
            f"({tried}). The archive may be encrypted - add its key via "
            f"File > Manage Keys or paste it into Settings.{detail}"
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
        from dualforge.compression.oodle import set_search_root
        from dualforge.vendor.pyuepak.utils import UnsupportedCompressionMethod

        with self._lock:
            candidates = [path, path.lstrip("/")]
            set_search_root(self.path)
            try:
                for candidate in candidates:
                    try:
                        return self._pak.read_file(candidate)
                    except KeyError:
                        continue
                    except UnsupportedCompressionMethod as exc:
                        raise PakError(str(exc)) from exc
                    except Exception as exc:
                        raise PakError(f"failed to read '{path}' from pak: {exc}") from exc
                raise PakError(f"file not found in pak: {path}")
            finally:
                set_search_root(None)

    def extract_file(self, path: str, out_dir: str) -> str:
        from dualforge.export.exporter import write_entry

        return write_entry(out_dir, _rel_path(path), self.read_file(path))

    def close(self) -> None:
        pass


def _rel_path(path: str) -> str:
    cleaned = Path(path.replace("\\", "/")).as_posix().lstrip("/")
    return cleaned or "unnamed.bin"


__all__ = ["PakArchive", "PakError", "pak_footer_version"]