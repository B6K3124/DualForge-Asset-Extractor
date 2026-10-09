from __future__ import annotations

import random

import pytest

import dualforge.crack_process as cp
from dualforge.crack import VerifiedKey
from dualforge.encryption.schemes.sm4_tables import MODE_TABLES
from dualforge.unreal.process import ProcessError


class FakeReader:
    """In-memory stand-in for ``ProcessReader``."""

    def __init__(self, regions: list[tuple[int, bytes]]):
        total = max((base + len(data) for base, data in regions), default=0)
        self.mem = bytearray(total)
        for base, data in regions:
            self.mem[base : base + len(data)] = data
        self.regions = [(base, len(data)) for base, data in regions]
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.mem[address : address + size])

    def readable_regions(self):
        return iter(self.regions)

    def region(self, address: int) -> tuple[int, int]:
        for base, size in self.regions:
            if base <= address < base + size:
                return base, size
        raise ProcessError(f"no region at 0x{address:X}")

    def scan(self, pattern: bytes) -> list[int]:
        hits: list[int] = []
        for base, size in self.regions:
            data = bytes(self.mem[base : base + size])
            start = 0
            while True:
                index = data.find(pattern, start)
                if index < 0:
                    break
                hits.append(base + index)
                start = index + 1
        return hits

    def read_chunks(self, base: int, size: int):
        data = bytes(self.mem[base : base + size])
        step = max(len(data), 1)
        for i in range(0, len(data), step):
            yield base + i, data[i : i + step]


def _random_key(length: int) -> bytes:
    return bytes(random.getrandbits(8) for _ in range(length))


# 16 random-looking bytes with entropy ~3.875 (>= the 3.75 raw threshold).
_KEY16 = bytes.fromhex("d862c2e36b0a42f7827c67ebc8d44df7")
_KEY32 = __import__("hashlib").sha256(b"dualforge offline key hunt").digest()
_HEXSTR64 = "deadbeef" + "1234567890abcdef" * 3 + "cafe1234"


def test_default_signatures_include_crypto_tables():
    sigs = cp.default_signatures()
    assert len(sigs["aes_sbox"]) == 256
    assert len(sigs["aes_sbox_inv"]) == 256
    assert len(sigs["sm4_sbox"]) == 256
    assert sigs["abi_table_a"] == MODE_TABLES["a"]
    assert sigs["AesKey"] == b"AesKey"


def test_collect_candidates_finds_both_hex_text_and_raw():
    key = _KEY16
    hexstr = _HEXSTR64
    context = b"\x00" * 64 + key + b"\x00" * 64 + hexstr.encode()
    candidates = cp.collect_candidates(context, 1000, max_per_match=200)
    values = [c.hex_value for c in candidates]
    assert key.hex() in values
    assert hexstr in values


def test_collect_candidates_rejects_low_entropy():
    assert cp.collect_candidates(b"A" * 512, 0) == []


def test_rank_candidates_dedupes_and_sorts():
    high = cp.Candidate(hex_value="ff", entropy=4.0, length=32, offset=0)
    low = cp.Candidate(hex_value="ff", entropy=3.0, length=32, offset=1)
    other = cp.Candidate(hex_value="aa", entropy=3.9, length=16, offset=2)
    result = cp._rank_candidates([low, high, other], limit=10)
    assert [c.hex_value for c in result] == ["ff", "aa"]
    assert result[0].entropy == 4.0


def test_scan_signature_matches_harvests_context_near_table():
    key = _KEY32
    table = MODE_TABLES["a"]
    data = b"\x00" * 128 + table + key + b"\x00" * 128
    reader = FakeReader([(0x400000, data)])
    matches = cp.scan_signature_matches(
        reader, signatures={"abi_table_a": table}, context_size=512
    )
    assert len(matches) == 1
    assert matches[0].signature == "abi_table_a"
    assert any(c.hex_value == key.hex() for c in matches[0].candidates)


def test_scan_signature_matches_caps_hits():
    data = b"AAAABBBB" + b"AABBAABB" + MODE_TABLES["a"] * 3
    reader = FakeReader([(0x1000, data)])
    matches = cp.scan_signature_matches(
        reader, signatures={"abi_table_a": MODE_TABLES["a"]}, max_sig_hits=2
    )
    assert len(matches) == 2


def test_harvest_raw_windows_finds_shrapnel():
    key = _KEY16
    blob = key + b"\x00" * 4096
    reader = FakeReader([(0x800000, blob)])
    found = cp.harvest_raw_windows(reader, max_windows=500, threshold=3.75)
    hit = next((c for c in found if c.hex_value == key.hex()), None)
    assert hit is not None
    assert hit.offset == 0x800000
    assert hit.source == "raw-window"


def test_crack_offline_scan_only_without_pak(monkeypatch):
    reader = FakeReader([(0x400000, _KEY16 + b"\x00" * 512)])
    monkeypatch.setattr(cp, "ProcessReader", lambda pid: reader)
    monkeypatch.setattr(cp, "resolve_process", lambda process=None, pid=None: (4242, "Game.exe"))

    result = cp.crack_offline(process="Game.exe", raw_windows=50)
    assert result["status"] == "scan_only"
    assert result["pid"] == 4242
    assert _KEY16.hex() in result["candidates"]
    assert not result["verified"]
    assert reader.closed


def test_crack_offline_validates_and_saves(monkeypatch, tmp_path):
    key = "11" * 32
    reader = FakeReader([(0x400000, b"\x00" * 128 + bytes.fromhex(key) + b"\x00" * 128)])
    monkeypatch.setattr(cp, "ProcessReader", lambda pid: reader)
    monkeypatch.setattr(cp, "resolve_process", lambda process=None, pid=None: (7, "Game.exe"))
    monkeypatch.setattr(cp, "find_validation_pak", lambda pak: str(pak))

    saved: list[dict] = []

    class FakeStore:
        def __init__(self, path=None):
            pass

        def add(self, title, keystr, engine="", notes="", scheme="aes-256", parameters=None):
            saved.append(dict(title=title, key=keystr, scheme=scheme))

    monkeypatch.setattr(cp, "KeyStore", FakeStore)
    monkeypatch.setattr(
        cp,
        "validate_keys_against_pak",
        lambda pak, keys, block_count=16, scheme=None, game="": [
            VerifiedKey(key=key, scheme="sm4-abi", parameters={}, game="Game")
        ],
    )

    pak = tmp_path / "game.pak"
    pak.write_bytes(b"\x00" * 512)
    result = cp.crack_offline(
        process="Game.exe", pak=str(pak), title="Game", max_candidates=100
    )
    assert result["status"] == "ok"
    assert result["verified"] == [key]
    assert len(saved) == 1
    assert saved[0]["title"] == "Game [offline-1]"
    assert saved[0]["scheme"] == "sm4-abi"


def test_crack_offline_no_pak_does_not_save(monkeypatch):
    reader = FakeReader([(0x400000, b"\x00" * 256 + b"112233445566778899aabbccddeeff00" * 2)])
    monkeypatch.setattr(cp, "ProcessReader", lambda pid: reader)
    monkeypatch.setattr(cp, "resolve_process", lambda process=None, pid=None: (9, "Game.exe"))
    monkeypatch.setattr(cp, "KeyStore", lambda path=None: pytest.fail("KeyStore must not be used when no pak"))

    result = cp.crack_offline(process="Game.exe", save_keys=True)
    assert result["status"] == "scan_only"
    assert result["saved"] == []