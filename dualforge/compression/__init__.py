from __future__ import annotations

import bz2
import gzip
import io
import lzma
import zlib
import zipfile

from dualforge.compression.oodle import (
    MAX_DECOMPRESSED_SIZE,
    Oodle,
    OodleDecompressError,
    OodleUnavailableError,
)
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
        from dualforge.compression.oodle import find_oodle_dll

        return find_oodle_dll() is not None
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


def _bomb(method: str) -> CompressionError:
    return CompressionError(
        f"{method} output exceeds the {MAX_DECOMPRESSED_SIZE}-byte safety "
        f"limit (possible decompression bomb)"
    )


def _check_expected_size(output_size: int | None) -> None:
    """Reject caller-supplied sizes above the cap before allocating anything."""
    if output_size is None:
        return
    if output_size < 0:
        raise CompressionError(
            f"negative output_size ({output_size}) is not valid"
        )
    if output_size > MAX_DECOMPRESSED_SIZE:
        raise CompressionError(
            f"output_size {output_size} exceeds the "
            f"{MAX_DECOMPRESSED_SIZE}-byte safety limit"
        )


def _bounded_zlib(data: bytes) -> bytes:
    """zlib.decompress bounded: asks for one byte over the cap to detect bombs."""
    out = zlib.decompressobj().decompress(data, MAX_DECOMPRESSED_SIZE + 1)
    if len(out) > MAX_DECOMPRESSED_SIZE:
        raise _bomb("zlib")
    return out


def _bounded_gzip(data: bytes) -> bytes:
    """gzip.decompress bounded via streaming read of cap+1 bytes."""
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        out = f.read(MAX_DECOMPRESSED_SIZE + 1)
    if len(out) > MAX_DECOMPRESSED_SIZE:
        raise _bomb("gzip")
    return out


def _bounded_bz2(data: bytes) -> bytes:
    out = bz2.BZ2Decompressor().decompress(data, MAX_DECOMPRESSED_SIZE + 1)
    if len(out) > MAX_DECOMPRESSED_SIZE:
        raise _bomb("bz2")
    return out


def _bounded_lzma(data: bytes) -> bytes:
    out = lzma.LZMADecompressor().decompress(data, MAX_DECOMPRESSED_SIZE + 1)
    if len(out) > MAX_DECOMPRESSED_SIZE:
        raise _bomb("lzma")
    return out


def decompress(
    data: bytes,
    method: str,
    output_size: int | None = None,
    archive_member: str | None = None,
    oodle_dll: str | None = None,
) -> bytes:
    method = (method or "none").lower()
    _check_expected_size(output_size)
    if method == "none":
        return data
    if method == "zlib":
        return _bounded_zlib(data)
    if method == "gzip":
        return _bounded_gzip(data)
    if method == "bz2":
        return _bounded_bz2(data)
    if method == "lzma":
        return _bounded_lzma(data)
    if method in {"lz4", "lz4hc"}:
        if data.startswith(b"\x04\x22\x4d\x18"):
            frame = _lz4_frame().LZ4FrameDecompressor()
            out = frame.decompress(data, MAX_DECOMPRESSED_SIZE + 1)
            if len(out) > MAX_DECOMPRESSED_SIZE:
                raise _bomb("lz4")
            return out
        block = _lz4_block()
        try:
            return block.decompress(data)
        except Exception:
            if output_size is None:
                raise CompressionError("lz4/lz4hc block decompression requires output_size") from None
            logger.debug("lz4 block retry with explicit output_size")
            return block.decompress(data, uncompressed_size=output_size)
    if method == "zstd":
        max_size = min(output_size or MAX_DECOMPRESSED_SIZE, MAX_DECOMPRESSED_SIZE)
        return _zstandard().ZstdDecompressor().decompress(data, max_output_size=max_size)
    if method == "brotli":
        d = _brotli().Decompressor()
        out = bytearray()
        for i in range(0, len(data), 1 << 20):
            out += d.process(data[i : i + (1 << 20)])
            if len(out) > MAX_DECOMPRESSED_SIZE:
                raise _bomb("brotli")
        return bytes(out)
    if method == "snappy":
        n = _snappy().uncompressed_length(data)
        if n > MAX_DECOMPRESSED_SIZE:
            raise _bomb("snappy")
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
        if member is not None:
            info = zf.getinfo(member)
            if info.file_size > MAX_DECOMPRESSED_SIZE:
                raise _bomb("zip")
            return zf.read(member)
        for info in zf.infolist():
            if not info.filename.endswith("/"):
                if info.file_size > MAX_DECOMPRESSED_SIZE:
                    raise _bomb("zip")
                return zf.read(info.filename)
    raise CompressionError("zip archive contains no files")


def _check_py7zr_members(infos) -> None:
    for info in infos:
        size = getattr(info, "uncompressed", None)
        if size is not None and size > MAX_DECOMPRESSED_SIZE:
            raise _bomb("7z")


def _extract_7z(data: bytes, member: str | None) -> bytes:
    py7zr = _py7zr()
    with py7zr.SevenZipFile(io.BytesIO(data), mode="r") as archive:
        _check_py7zr_members(archive.list())
        if member is not None:
            with archive.read([member]) as extracted:
                for out in extracted.values():
                    return out.read()
        for _name, out in archive.readall().items():
            return out.read()
    raise CompressionError("7z archive contains no files")


def compress(data: bytes, method: str, level: int | None = None) -> bytes:
    """Compress with the given method.

    Supports none/zlib/gzip/lz4/zstd/brotli. Used by the vendored pak writer to
    produce compressed-pak fixtures, so the read path has something real to
    round-trip against.
    """
    method = (method or "none").lower()
    if method == "none":
        return data
    if method == "zlib":
        return zlib.compress(data, level if level is not None else 6)
    if method == "gzip":
        return gzip.compress(data, compresslevel=level if level is not None else 6)
    if method == "lz4":
        # Unreal stores a raw LZ4 block with no size prefix; the uncompressed
        # size lives in the pak block table, so store_size must be False.
        return _lz4_block().compress(
            data,
            mode="high_compression",
            compression=level if level is not None else 9,
            store_size=False,
        )
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
