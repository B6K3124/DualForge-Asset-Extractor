from __future__ import annotations

from pathlib import Path

import pytest

from dualforge.export.exporter import ExportError, Exporter, _sanitize, write_entry


def test_exporter_write_creates_nested(tmp_path):
    exporter = Exporter(str(tmp_path))
    out = exporter.write("dir/file.dat", b"hello")
    assert Path(out) == tmp_path / "dir" / "file.dat"
    assert Path(out).read_bytes() == b"hello"
    assert exporter.written == 1


def test_exporter_backslash_normalization(tmp_path):
    exporter = Exporter(str(tmp_path))
    out = exporter.write("a\\b\\c.bin", b"x")
    assert Path(out) == tmp_path / "a" / "b" / "c.bin"


def test_exporter_overwrite_true_replaces(tmp_path):
    exporter = Exporter(str(tmp_path), overwrite=True)
    exporter.write("a.dat", b"1")
    exporter.write("a.dat", b"2")
    assert (tmp_path / "a.dat").read_bytes() == b"2"


def test_exporter_overwrite_false_uniquifies(tmp_path):
    exporter = Exporter(str(tmp_path), overwrite=False)
    first = exporter.write("a.dat", b"1")
    second = exporter.write("a.dat", b"2")
    third = exporter.write("a.dat", b"3")
    assert first == str(tmp_path / "a.dat")
    assert second == str(tmp_path / "a_1.dat")
    assert third == str(tmp_path / "a_2.dat")


def test_write_entry_writes_single_file(tmp_path):
    out = write_entry(str(tmp_path), "sub/f.bin", b"\x00\x01")
    assert Path(out).parent == tmp_path / "sub"
    assert Path(out).read_bytes() == b"\x00\x01"


def test_write_entry_overwrite_passthrough(tmp_path):
    first = write_entry(str(tmp_path), "a.bin", b"1", overwrite=False)
    second = write_entry(str(tmp_path), "a.bin", b"2", overwrite=False)
    assert first == str(tmp_path / "a.bin")
    assert second == str(tmp_path / "a_1.bin")


def test_sanitize_rejects_empty_and_traversal():
    with pytest.raises(ExportError):
        _sanitize("")
    with pytest.raises(ExportError):
        _sanitize("/")
    with pytest.raises(ExportError):
        _sanitize("..")
    with pytest.raises(ExportError):
        _sanitize("////")


def test_sanitize_strips_dots_and_forbidden_chars():
    assert _sanitize("a/../b") == "a/b"
    assert _sanitize("a//b/") == "a/b"
    assert _sanitize("x<y>z?.txt") == "x_y_z_.txt"
    assert _sanitize('../evil') == "evil"