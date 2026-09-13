import gzip
import lzma
import zlib

import pytest

from dualforge.compression import CompressionError, decompress, is_available, sniff

PAYLOAD = (b"the quick brown fox jumps over the lazy dog. " * 50) + bytes(range(256))


def test_none_roundtrip():
    assert decompress(PAYLOAD, "none") == PAYLOAD


def test_zlib():
    data = zlib.compress(PAYLOAD)
    assert sniff(data) is None
    assert decompress(data, "zlib") == PAYLOAD


def test_gzip():
    data = gzip.compress(PAYLOAD)
    assert sniff(data) == "gzip"
    assert decompress(data, "gzip") == PAYLOAD


def test_bz2():
    data = __import__("bz2").compress(PAYLOAD)
    assert sniff(data) == "bz2"
    assert decompress(data, "bz2") == PAYLOAD


def test_lzma():
    data = lzma.compress(PAYLOAD)
    assert sniff(data) == "lzma"
    assert decompress(data, "lzma") == PAYLOAD


@pytest.mark.parametrize("method", ["zlib", "gzip", "bz2", "lzma"])
def test_available_stdlib(method):
    assert is_available(method)


def test_lz4_roundtrip():
    if not is_available("lz4"):
        pytest.skip("lz4 not installed")
    import lz4.block

    data = lz4.block.compress(PAYLOAD)
    assert decompress(data, "lz4", output_size=len(PAYLOAD)) == PAYLOAD


def test_lz4_frame_sniff():
    if not is_available("lz4"):
        pytest.skip("lz4 not installed")
    import lz4.frame

    data = lz4.frame.compress(PAYLOAD)
    assert sniff(data) == "lz4"
    assert decompress(data, "lz4") == PAYLOAD


def test_zstd_roundtrip():
    if not is_available("zstd"):
        pytest.skip("zstandard not installed")
    import zstandard

    data = zstandard.ZstdCompressor().compress(PAYLOAD)
    assert sniff(data) == "zstd"
    assert decompress(data, "zstd", output_size=len(PAYLOAD)) == PAYLOAD


def test_brotli_roundtrip():
    if not is_available("brotli"):
        pytest.skip("brotli not installed")
    import brotli

    data = brotli.compress(PAYLOAD)
    assert decompress(data, "brotli") == PAYLOAD


def test_zip_container():
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("inner.txt", PAYLOAD)
    data = buffer.getvalue()
    assert sniff(data) == "zip"
    assert decompress(data, "zip", archive_member="inner.txt") == PAYLOAD


def test_unknown_method_raises():
    with pytest.raises(CompressionError):
        decompress(PAYLOAD, "not-a-method")
