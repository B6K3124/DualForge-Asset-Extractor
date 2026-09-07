from __future__ import annotations


import pytest

import dualforge.ghidra.manager as mgr


def test_find_analyze_headless_from_ghidra_home(monkeypatch, tmp_path):
    support = tmp_path / "support"
    support.mkdir()
    headless = support / "analyzeHeadless.bat"
    headless.touch()
    monkeypatch.setenv("GHIDRA_HOME", str(tmp_path))
    assert mgr.find_analyze_headless() == headless


def test_find_analyze_headless_from_cache(monkeypatch, tmp_path):
    support = tmp_path / "ghidra_11.3.2_PUBLIC" / "support"
    support.mkdir(parents=True)
    headless = support / "analyzeHeadless.bat"
    headless.touch()
    monkeypatch.setattr(mgr, "CACHE_ROOT", tmp_path)
    monkeypatch.delenv("GHIDRA_HOME", raising=False)
    assert mgr.find_analyze_headless() == headless


def test_find_java_from_java_home(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    java = bin_dir / "java.exe"
    java.touch()
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    monkeypatch.setattr(mgr, "CACHE_ROOT", tmp_path / "cache")
    assert mgr.find_java() == str(java)


def test_java_major_parsing():
    assert mgr._java_major(None) is None


def test_ensure_ghidra_raises_when_disabled(monkeypatch):
    monkeypatch.setattr(mgr, "find_analyze_headless", lambda: None)
    with pytest.raises(mgr.GhidraError):
        mgr.ensure_ghidra(download=False)


def test_ensure_ghidra_uses_existing(monkeypatch, tmp_path):
    support = tmp_path / "support"
    support.mkdir()
    headless = support / "analyzeHeadless.bat"
    headless.touch()
    monkeypatch.setattr(mgr, "find_analyze_headless", lambda: headless)
    assert mgr.ensure_ghidra(download=False) == headless


def test_toolchain_status_empty(monkeypatch):
    monkeypatch.setattr(mgr, "find_analyze_headless", lambda: None)
    monkeypatch.setattr(mgr, "find_java", lambda: None)
    status = mgr.toolchain_status()
    assert status["ghidra"] is None
    assert status["java"] is None
    assert status["ready"] is False


def test_ensure_ghidra_extracts_zip_with_nonmatching_dir_name(monkeypatch, tmp_path):
    """GitHub Ghidra zips extract to a non-dated dir; we must locate analyzeHeadless
    by scanning the cache rather than assuming zip stem == extracted dir name."""
    import zipfile

    monkeypatch.setattr(mgr, "find_analyze_headless", lambda: None)
    monkeypatch.setattr(mgr, "CACHE_ROOT", tmp_path)
    zip_name = "ghidra_11.3.2_PUBLIC_20250315.zip"
    monkeypatch.setattr(mgr, "_latest_ghidra_asset", lambda log: (zip_name, "http://example.test/ghidra.zip"))

    zip_dest = tmp_path / zip_name
    inner = "ghidra_11.3.2_PUBLIC/support/analyzeHeadless.bat"
    with zipfile.ZipFile(zip_dest, "w") as zf:
        zf.writestr(inner, "echo gh \r\n")

    headless = mgr.ensure_ghidra(download=True)
    assert headless == tmp_path / "ghidra_11.3.2_PUBLIC" / "support" / "analyzeHeadless.bat"
    assert headless.is_file()


def test_ensure_ghidra_raises_when_zip_has_no_headless(monkeypatch, tmp_path):
    import zipfile

    monkeypatch.setattr(mgr, "find_analyze_headless", lambda: None)
    monkeypatch.setattr(mgr, "CACHE_ROOT", tmp_path)
    zip_name = "ghidra_11.3.2_PUBLIC_20250315.zip"
    monkeypatch.setattr(mgr, "_latest_ghidra_asset", lambda log: (zip_name, "http://example.test/ghidra.zip"))
    zip_dest = tmp_path / zip_name
    with zipfile.ZipFile(zip_dest, "w") as zf:
        zf.writestr("ghidra_11.3.2_PUBLIC/README.txt", "hello")
    with pytest.raises(mgr.GhidraError):
        mgr.ensure_ghidra(download=True)
