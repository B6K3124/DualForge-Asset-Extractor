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


# --------------------------------------------------------- decompression limits


def test_output_size_above_cap_is_rejected():
    from dualforge.compression import MAX_DECOMPRESSED_SIZE

    with pytest.raises(CompressionError, match="exceeds"):
        decompress(PAYLOAD, "zlib", output_size=MAX_DECOMPRESSED_SIZE + 1)


def test_negative_output_size_is_rejected():
    with pytest.raises(CompressionError, match="negative"):
        decompress(PAYLOAD, "zlib", output_size=-1)


@pytest.mark.parametrize(
    "method",
    ["zlib", "gzip", "bz2", "lzma"],
)
def test_bomb_is_stopped_for_stream_codecs(method, monkeypatch):
    """A tiny payload that expands past the cap must be refused, not allocated.

    The cap is patched down so the test does not need a real multi-gigabyte
    payload; the code path and arithmetic are identical.
    """
    import dualforge.compression as comp

    monkeypatch.setattr(comp, "MAX_DECOMPRESSED_SIZE", 1024)
    expanding = PAYLOAD * 200  # decompresses well past 1024 bytes
    data = {
        "zlib": zlib.compress,
        "gzip": gzip.compress,
        "bz2": __import__("bz2").compress,
        "lzma": lzma.compress,
    }[method](expanding)

    with pytest.raises(CompressionError, match="limit"):
        comp.decompress(data, method)


def test_bomb_is_stopped_for_zip(monkeypatch):
    import io
    import zipfile

    import dualforge.compression as comp

    monkeypatch.setattr(comp, "MAX_DECOMPRESSED_SIZE", 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.txt", PAYLOAD * 200)

    with pytest.raises(CompressionError, match="limit"):
        comp.decompress(buffer.getvalue(), "zip", archive_member="bomb.txt")


def test_size_below_cap_still_roundtrips(monkeypatch):
    import dualforge.compression as comp

    monkeypatch.setattr(comp, "MAX_DECOMPRESSED_SIZE", len(PAYLOAD) + 1)
    assert comp.decompress(zlib.compress(PAYLOAD), "zlib") == PAYLOAD


def test_exact_cap_boundary_is_allowed(monkeypatch):
    """The limit is inclusive: exactly cap bytes must not be treated as a bomb."""
    import dualforge.compression as comp

    monkeypatch.setattr(comp, "MAX_DECOMPRESSED_SIZE", len(PAYLOAD))
    assert comp.decompress(gzip.compress(PAYLOAD), "gzip") == PAYLOAD
