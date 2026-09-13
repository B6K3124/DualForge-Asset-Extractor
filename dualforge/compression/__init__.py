from __future__ import annotations

import bz2
import gzip
import io
import lzma
import zlib
import zipfile

from dualforge.compression.oodle import Oodle, OodleDecompressError, OodleUnavailableError
from dualforge.log import get_logger

logger = get_logger(__name__)


class CompressionError(Exception):
    pass


METHODS = (
    "none",
    "zlib",
    "gzip",
    "bz2",
    "lzma",
    "lz4",
    "lz4hc",
    "zstd",
    "brotli",
    "oodle",
    "snappy",
    "zip",
    "7z",
)


def _lz4_block():
    try:
        import lz4.block
    except ImportError as exc:
        raise CompressionError("lz4 support requires the 'lz4' package (pip install lz4)") from exc
    return lz4.block


def _lz4_frame():
    try:
        import lz4.frame
    except ImportError as exc:
        raise CompressionError("lz4 support requires the 'lz4' package (pip install lz4)") from exc
    return lz4.frame


def _zstandard():
    try:
        import zstandard
    except ImportError as exc:
        raise CompressionError("zstd support requires the 'zstandard' package") from exc
    return zstandard


def _brotli():
    try:
        import brotli
    except ImportError as exc:
        raise CompressionError("brotli support requires the 'brotli' package") from exc
    return brotli


def _snappy():
    try:
        import snappy
    except ImportError as exc:
        raise CompressionError("snappy support requires the 'python-snappy' package") from exc
    return snappy


def _py7zr():
    try:
        import py7zr
    except ImportError as exc:
        raise CompressionError("7z support requires the 'py7zr' package (pip install py7zr)") from exc
    return py7zr


def is_available(method: str) -> bool:
    method = (method or "none").lower()
    if method in {"none", "zlib", "gzip", "bz2", "lzma"}:
        return True
    if method in {"lz4", "lz4hc"}:
        try:
            _lz4_block()
            return True
        except CompressionError:
            return False
    if method == "zstd":
        try:
            _zstandard()
            return True
        except CompressionError:
            return False
    if method == "brotli":
        try:
            _brotli()
            return True
        except CompressionError:
            return False
    if method == "oodle":
        try:
            Oodle()
            return True
        except OodleUnavailableError:
            return False
    if method == "snappy":
        try:
            _snappy()
            return True
        except CompressionError:
            return False
    if method == "zip":
        return True
    if method == "7z":
        try:
            _py7zr()
            return True
        except CompressionError:
            return False
    return False


def decompress(
    data: bytes,
    method: str,
    output_size: int | None = None,
    archive_member: str | None = None,
    oodle_dll: str | None = None,
) -> bytes:
    method = (method or "none").lower()
    if method == "none":
        return data
    if method == "zlib":
        return zlib.decompress(data)
    if method == "gzip":
        return gzip.decompress(data)
    if method == "bz2":
        return bz2.decompress(data)
    if method == "lzma":
        return lzma.decompress(data)
    if method in {"lz4", "lz4hc"}:
        if data.startswith(b"\x04\x22\x4d\x18"):
            return _lz4_frame().decompress(data)
        block = _lz4_block()
        try:
            return block.decompress(data)
        except Exception:
            if output_size is None:
                raise CompressionError("lz4/lz4hc block decompression requires output_size") from None
            logger.debug("lz4 block retry with explicit output_size")
            return block.decompress(data, uncompressed_size=output_size)
    if method == "zstd":
        max_size = output_size or 2 ** 31 - 1
        return _zstandard().ZstdDecompressor().decompress(data, max_output_size=max_size)
    if method == "brotli":
        return _brotli().decompress(data)
    if method == "snappy":
        return _snappy().uncompress(data)
    if method == "oodle":
        if output_size is None:
            raise CompressionError("oodle decompression requires output_size")
        return Oodle(dll_path=oodle_dll).decompress(data, output_size)
    if method == "zip":
        return _extract_zip(data, archive_member)
    if method == "7z":
        return _extract_7z(data, archive_member)
    raise CompressionError(f"unknown compression method: {method!r}")


def _extract_zip(data: bytes, member: str | None) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if member is not None:
            return zf.read(member)
        for name in names:
            if not name.endswith("/"):
                return zf.read(name)
    raise CompressionError("zip archive contains no files")


def _extract_7z(data: bytes, member: str | None) -> bytes:
    py7zr = _py7zr()
    with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as archive:
        if member is not None:
            with archive.read([member]) as extracted:
                for out in extracted.values():
                    return out.read()
        for _name, out in archive.readall().items():
            return out.read()
    raise CompressionError("7z archive contains no files")


def compress(data: bytes, method: str, level: int | None = None) -> bytes:
    """Compress with the given method (zstd/brotli only)."""
    method = (method or "none").lower()
    if method == "none":
        return data
    if method == "zstd":
        level = level or 19
        compressor = _zstandard().ZstdCompressor(level=level)
        return compressor.compress(data)
    if method == "brotli":
        level = level or 11
        return _brotli().compress(data, quality=level)
    raise CompressionError(f"cannot compress with method: {method!r}")


def sniff(data: bytes) -> str | None:
    if not data:
        return None
    if data.startswith(b"\x1f\x8b"):
        return "gzip"
    if data.startswith(b"BZh"):
        return "bz2"
    if data.startswith(b"\xfd7zXZ\x00"):
        return "lzma"
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        return "zstd"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        return "zip"
    if data.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if data.startswith(b"\x04\x22\x4d\x18"):
        return "lz4"
    return None


__all__ = [
    "METHODS",
    "CompressionError",
    "OodleUnavailableError",
    "OodleDecompressError",
    "is_available",
    "decompress",
    "compress",
    "sniff",
]
