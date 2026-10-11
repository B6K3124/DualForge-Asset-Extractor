"""Container unwrapping limits.

A file may be a container that unpacks to another container. DualForge unwraps
those iteratively, and while each individual decompression is size-bounded, the
number of layers was not - a crafted "gzip of gzips" could keep the loop busy.
"""

from __future__ import annotations

import gzip

from dualforge.extract import _MAX_CONTAINER_DEPTH, ExtractOptions, extract_file


def test_shallow_nesting_still_unwraps(tmp_path):
    data = gzip.compress(b"hello")
    data = gzip.compress(data)
    path = tmp_path / "two.gz"
    path.write_bytes(data)

    result = extract_file(str(path), ExtractOptions(out_dir=str(tmp_path / "out")))

    assert result.errors == []
    assert result.ok == 1
    assert (tmp_path / "out" / "container.bin").read_bytes() == b"hello"


def test_deeply_nested_container_is_capped(tmp_path):
    data = b"innermost"
    for _ in range(_MAX_CONTAINER_DEPTH + 8):
        data = gzip.compress(data)
    path = tmp_path / "nested.gz"
    path.write_bytes(data)

    result = extract_file(str(path), ExtractOptions(out_dir=str(tmp_path / "out")))

    assert result.ok == 0
    assert any("nesting" in err for err in result.errors), result.errors


def test_exactly_at_the_limit_still_unwraps(tmp_path):
    """The cap is a ceiling, not an off-by-one rejection."""
    data = b"bottom"
    for _ in range(_MAX_CONTAINER_DEPTH):
        data = gzip.compress(data)
    path = tmp_path / "atlimit.gz"
    path.write_bytes(data)

    result = extract_file(str(path), ExtractOptions(out_dir=str(tmp_path / "out")))

    assert result.errors == []
    assert (tmp_path / "out" / "container.bin").read_bytes() == b"bottom"
