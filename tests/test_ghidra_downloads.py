from __future__ import annotations

import json

import pytest

from dualforge.ghidra import manager as m

_ZIP_NAME = "ghidra_11.3.2_PUBLIC_20250601.zip"
_ZIP_URL = "https://go/z.zip"


def _resp(data: bytes, headers=None):
    class R:
        def __init__(self):
            self.headers = headers or {}
            self._data = data
            self._pos = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            chunk = self._data[self._pos:] if size < 0 else self._data[self._pos : self._pos + size]
            self._pos += len(chunk)
            return chunk

    return R()


def _json_resp(payload):
    return _resp(json.dumps(payload).encode())


def test_latest_ghidra_asset_uses_digest(monkeypatch):
    asset = {
        "name": _ZIP_NAME,
        "browser_download_url": _ZIP_URL,
        "digest": "sha256:" + "ab" * 32,
    }

    def fake_urlopen(req, timeout=None):
        return _json_resp({"assets": [asset]})

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    name, url, sha = m._latest_ghidra_asset(lambda msg: None)
    assert (name, url, sha) == (_ZIP_NAME, _ZIP_URL, "ab" * 32)


def test_latest_ghidra_asset_falls_back_to_sha256_sibling(monkeypatch):
    zip_asset = {"name": _ZIP_NAME, "browser_download_url": _ZIP_URL}
    sha_asset = {"name": _ZIP_NAME + ".sha256", "browser_download_url": _ZIP_URL + ".sha256"}
    calls = []

    def fake_urlopen(req, timeout=None):
        url = str(getattr(req, "full_url", req))
        calls.append(url)
        if url.endswith(".sha256"):
            return _resp((("cd" * 32) + "  z.zip").encode())
        return _json_resp({"assets": [zip_asset, sha_asset]})

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    name, url, sha = m._latest_ghidra_asset(lambda msg: None)
    assert name == _ZIP_NAME
    assert url == _ZIP_URL
    assert sha == "cd" * 32
    assert calls == [m.GHIDRA_API, _ZIP_URL + ".sha256"]


def test_latest_ghidra_asset_raises_when_no_zip(monkeypatch):
    def fake_urlopen(req, timeout=None):
        return _json_resp({"assets": [{"name": "other.txt"}]})

    monkeypatch.setattr(m.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(m.GhidraError):
        m._latest_ghidra_asset(lambda msg: None)


def test_sha256_hex_matches_file_content(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello world")
    assert m._sha256_hex(f) == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_verify_sha256_accepts_matching_hash(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello world")
    logs = []
    m._verify_sha256(f, m._sha256_hex(f), "a.bin", logs.append)
    assert f.exists()
    assert any("hash OK" in line for line in logs)


def test_verify_sha256_mismatch_deletes_and_raises(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello world")
    with pytest.raises(m.GhidraError, match="SHA-256 mismatch"):
        m._verify_sha256(f, "11" * 32, "a.bin", lambda msg: None)
    assert not f.exists()
    assert not f.with_name(f.name + ".part").exists()