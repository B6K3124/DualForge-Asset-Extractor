from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "ghidra" / "ghidra_key_finder.py"

_spec = importlib.util.spec_from_file_location("dualforge_ghidra_key_finder", _SCRIPT)
assert _spec and _spec.loader
gkf = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = gkf
_spec.loader.exec_module(gkf)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyuepak") is None,
    reason="pyuepak not installed",
)


class FakeStore:
    def __init__(self, entries=None):
        self._entries = entries or []

    def list(self):
        return self._entries

    def add(self, title, key, engine="", notes="", dynamic_keys=None):
        for entry in self._entries:
            if entry["title"] == title:
                entry["aes_key"] = key
                return
        self._entries.append({"title": title, "aes_key": key, "engine": engine})


# ---------------------------------------------------------------- pure logic


def test_shannon_entropy_uniform_zero():
    assert gkf.shannon_entropy(b"A" * 64) == 0.0
    assert gkf.shannon_entropy(b"") == 0.0


def test_shannon_entropy_max():
    data = bytes(range(256)) * 4
    assert gkf.shannon_entropy(data) == pytest.approx(8.0, abs=0.01)


def test_is_hex_key():
    assert gkf.is_hex_key(b"0123456789abcdef" * 4, 32)
    assert not gkf.is_hex_key(b"zz123456789abcdef0" * 4, 32)
    assert not gkf.is_hex_key(b"0123456789abcdef", 32)
    assert gkf.is_hex_key(b"0123456789abcdef" * 2, 16)


def test_parse_hex_key():
    assert gkf.parse_hex_key("0x" + "A" * 64) == "a" * 64
    assert gkf.parse_hex_key("B" * 64) == "b" * 64
    assert gkf.parse_hex_key("C" * 32) is None


def test_hex_string_to_bytes():
    assert gkf.hex_string_to_bytes("DE AD BE EF") == b"\xde\xad\xbe\xef"
    with pytest.raises(ValueError):
        gkf.hex_string_to_bytes("DEADBEE")
    with pytest.raises(ValueError):
        gkf.hex_string_to_bytes("zz")


def test_collect_candidates_entropy_ranking_and_cap():
    import hashlib

    filler = b"\x00" * 2000
    key32 = hashlib.sha256(b"dualforge-test").hexdigest().encode()
    context = filler + key32 + filler
    candidates = gkf.collect_candidates(context, 0, min_length=32, max_length=32, max_per_match=5)
    assert len(candidates) <= 5
    assert all(c.length == 32 and len(c.hex_value) == 64 for c in candidates)
    assert candidates[0].offset == 2000
    assert candidates[0].hex_value == key32.decode()


def test_collect_candidates_ignores_low_entropy():
    context = (b"AB" * 1024) + (b"00" * 64) + (b"CD" * 1024)
    assert gkf.collect_candidates(context, 0) == []


def test_scan_for_signatures_multiple_hits():
    haystack = b"\x00" * 10 + bytes(gkf.AES_SBOX) + b"\x00" * 10 + bytes(gkf.AES_SBOX)
    hits = gkf.scan_for_signatures(haystack, {"sbox": bytes(gkf.AES_SBOX)}, 100)
    assert hits == [("sbox", 110), ("sbox", 10 + 256 + 10 + 100)]


# ------------------------------------------------------------ scan_memory


class FakeBlock(gkf.MemoryBlock):
    pass


def _memory_fetch(blocks_by_name, block_name, offset, size):
    data = blocks_by_name[block_name]
    return data[offset : offset + size]


def test_scan_memory_signature_across_chunk_boundary():
    signature = b"\xde\xad\xbe\xef"
    block = FakeBlock(name=".data", size=10 * 1024 * 1024)
    data = bytearray(block.size)
    data[4 * 1024 * 1024 - 2 : 4 * 1024 * 1024 + 2] = signature
    data[block.size - 4 :] = signature
    matches = gkf.scan_memory(
        [block],
        lambda name, off, size: _memory_fetch({".data": bytes(data)}, name, off, size),
        {"sig": signature},
        chunk_size=4 * 1024 * 1024,
        entropy_enabled=False,
        context_size=0,
        min_length=16,
        max_length=32,
        threshold=3.5,
        max_per_match=20,
    )
    assert sorted(m.offset for m in matches) == [4 * 1024 * 1024 - 2, block.size - 4]
    assert all(m.signature == "sig" for m in matches)


def test_scan_memory_sbox_hit_yields_key_candidate():
    key = "1a2b3c4d5e6f708192a3b4c5d6e7f809" * 2
    block = FakeBlock(name=".rdata", size=8 * 1024 * 1024)
    data = bytearray(block.size)
    data[300:300 + len(gkf.AES_SBOX)] = gkf.AES_SBOX
    data[700 : 700 + 64] = key.encode()
    matches = gkf.scan_memory(
        [block],
        lambda name, off, size: _memory_fetch({".rdata": bytes(data)}, name, off, size),
        {"preset:aes_sbox": bytes(gkf.AES_SBOX)},
        chunk_size=4 * 1024 * 1024,
        entropy_enabled=True,
        context_size=512,
        min_length=16,
        max_length=32,
        threshold=3.5,
        max_per_match=20,
    )
    assert len(matches) == 1
    assert matches[0].offset == 300
    assert any(c.hex_value == key for c in matches[0].candidates)


def test_entropy_only_scan_skips_uninitialized():
    import hashlib

    blocks = [
        FakeBlock(name=".init", size=1024, initialized=False),
        FakeBlock(name=".text", size=65536),
    ]
    data = bytearray(65536)
    key = hashlib.sha256(b"entropy-scan-test").hexdigest().encode()
    data[100:164] = key
    matches = gkf.entropy_only_scan(
        blocks,
        lambda name, off, size: _memory_fetch({".text": bytes(data)}, name, off, size),
        chunk_size=4096,
        min_length=16,
        max_length=32,
        threshold=3.5,
        max_per_match=20,
    )
    assert matches and all(m.block == ".text" for m in matches)
    assert any(c.offset == 100 and c.hex_value == key.decode() for m in matches for c in m.candidates)


def test_scan_memory_handles_empty_and_uninitialized():
    blocks = [FakeBlock(name=".empty", size=0), FakeBlock(name=".nope", size=10, initialized=False)]
    assert (
        gkf.scan_memory(
            blocks,
            lambda *args: b"",
            {},
            chunk_size=4096,
            entropy_enabled=False,
            context_size=0,
            min_length=16,
            max_length=32,
            threshold=3.5,
            max_per_match=20,
        )
        == []
    )


# ------------------------------------------------------------ key delivery


def _candidate(hex_value, entropy=7.5, length=32):
    return gkf.Candidate(hex_value=hex_value, entropy=entropy, length=length, offset=0)


def _match(candidates, signature="preset:aes_sbox"):
    return gkf.Match(signature=signature, block=".rdata", offset=0, candidates=candidates)


def test_add_to_keystore_skips_16_byte_keys(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr("dualforge.unreal.keys.KeyStore", lambda: store)
    short_key = "a1" * 16
    long_key = "b2" * 32
    added = gkf.add_to_keystore("SomeGame.exe", [_match([_candidate(short_key, 8.0, 16), _candidate(long_key, 7.0, 32)])], 5)
    assert added == ["SomeGame [ghidra-1]"]
    assert store._entries == [{"title": "SomeGame [ghidra-1]", "aes_key": long_key, "engine": "unreal"}]


def test_add_to_keystore_ranks_and_caps(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr("dualforge.unreal.keys.KeyStore", lambda: store)
    keys = [(f"{i:02x}" * 32, 6.0 + i / 10.0) for i in range(5)]
    added = gkf.add_to_keystore("Game.exe", [_match([_candidate(k, e) for k, e in keys])], 2)
    assert added == ["Game [ghidra-1]", "Game [ghidra-2]"]
    stored = [e["aes_key"] for e in store._entries]
    assert stored == [max(keys, key=lambda item: item[1])[0], sorted(keys, key=lambda item: item[1], reverse=True)[1][0]]


def test_add_to_keystore_none_when_no_32_byte(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr("dualforge.unreal.keys.KeyStore", lambda: store)
    added = gkf.add_to_keystore("Game.exe", [_match([_candidate("a1" * 16, 9.0, 16)])], 5)
    assert added == []
    assert store._entries == []


# ------------------------------------------------------------------- CLI


def test_find_analyze_headless_from_managed_cache(monkeypatch, tmp_path):
    support = tmp_path / "ghidra_11.3.2_PUBLIC" / "support"
    support.mkdir(parents=True)
    headless = support / "analyzeHeadless.bat"
    headless.touch()
    monkeypatch.setattr(gkf, "CACHE_DIR", tmp_path)
    monkeypatch.delenv("GHIDRA_HOME", raising=False)
    assert gkf.find_analyze_headless() == headless


def test_find_java_from_managed_cache(monkeypatch, tmp_path):
    bin_dir = tmp_path / "jre21" / "bin"
    bin_dir.mkdir(parents=True)
    java = bin_dir / "java.exe"
    java.touch()
    monkeypatch.setattr(gkf, "CACHE_DIR", tmp_path)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    assert gkf.find_java() == str(java)


def test_build_parser_defaults():
    parser = gkf.build_parser()
    args = parser.parse_args(["game.exe"])
    assert args.preset is None
    assert args.entropy_threshold == 3.5
    assert args.min_length == 32
    assert args.max_length == 32
    assert args.keystore_count == 5
    assert not args.no_add_keystore


def test_cmd_hunt_missing_binary_is_usage_error(capsys):
    assert gkf.cmd_hunt(gkf.build_parser().parse_args([])) == 2
    assert "binary" in capsys.readouterr().err


def test_cmd_hunt_nonexistent_binary_is_usage_error(capsys, tmp_path: Path):
    missing = tmp_path / "nope.exe"
    args = gkf.build_parser().parse_args([str(missing)])
    assert gkf.cmd_hunt(args) == 2


def test_cmd_hunt_rejects_bad_custom_signature(monkeypatch, tmp_path: Path):
    binary = tmp_path / "game.exe"
    binary.write_bytes(b"MZ")
    headless = tmp_path / "analyzeHeadless.bat"
    headless.touch()
    monkeypatch.setattr(gkf, "find_analyze_headless", lambda: headless)
    monkeypatch.setattr(gkf, "ensure_ghidra_bridge", lambda allow, log: None)
    monkeypatch.setattr(gkf, "find_ghidra_scripts_dir", lambda: tmp_path)
    parser = gkf.build_parser()
    args = parser.parse_args([str(binary), "--signature", "deadbee"])
    assert gkf.cmd_hunt(args) == 2


def test_cmd_check_without_ghidra(monkeypatch, capsys):
    monkeypatch.setattr(gkf, "find_analyze_headless", lambda: None)
    monkeypatch.setattr(gkf, "find_java", lambda: None)
    assert gkf.cmd_check(gkf.build_parser().parse_args([])) == gkf.EXIT_NO_GHIDRA
    assert "ghidra/releases" in capsys.readouterr().out


def test_cmd_check_ghidra_without_java(monkeypatch, capsys, tmp_path: Path):
    headless = tmp_path / "ghidra" / "support" / "analyzeHeadless.bat"
    headless.parent.mkdir(parents=True)
    headless.touch()
    monkeypatch.setattr(gkf, "find_analyze_headless", lambda: headless)
    monkeypatch.setattr(gkf, "find_java", lambda: None)
    assert gkf.cmd_check(gkf.build_parser().parse_args([])) == gkf.EXIT_NO_JAVA
    assert "temurin" in capsys.readouterr().out


# ---------------------------------------------------------------- PE builder


def test_make_test_pe_section_table_layout():
    import struct

    spec = importlib.util.spec_from_file_location(
        "dualforge_make_test_pe",
        _REPO_ROOT / "scripts" / "ghidra" / "make_test_pe.py",
    )
    assert spec and spec.loader
    maker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = maker
    spec.loader.exec_module(maker)

    key_hex = "7ae44748e21f003d2c5a999b04861a2dc3cfe1931fbb210bad96e8a71f04b9d1"
    blob = key_hex.encode("ascii") + b"\x00" * 8 + bytes(maker.aes_sbox())
    pe = maker.build_pe(key_hex, bytes(maker.aes_sbox()))

    fmt = "<8sIIIIIIHHI"
    text_off = pe.find(b".text")
    assert text_off >= 0
    rdata_off = text_off + 40
    text_name, tvsize, tva, trawsize, trawptr, _, _, _, _, tchars = struct.unpack_from(
        fmt, pe, text_off
    )
    rdata_name, rvsize, rva, rrawsize, rrawptr, _, _, _, _, rchars = struct.unpack_from(
        fmt, pe, rdata_off
    )
    assert text_name == b".text\x00\x00\x00"
    assert (tvsize, tva, trawsize, trawptr, tchars) == (
        0x100, 0x1000, 0x200, 0x200, 0x60000020,
    )
    assert rdata_name == b".rdata\x00\x00"
    assert (rvsize, rva, rrawsize, rrawptr, rchars) == (
        len(blob), 0x2000, 0x200, 0x400, 0x40000040,
    )
    assert len(pe) == 0x600
    raw = pe[rrawptr : rrawptr + rrawsize]
    assert raw[:64].decode("ascii") == key_hex
    assert raw[72:88] == bytes(maker.aes_sbox())[:16]