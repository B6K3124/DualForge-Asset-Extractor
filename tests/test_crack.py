from __future__ import annotations

import json
from pathlib import Path

from dualforge.crack import (
    VerifiedKey,
    crack_all,
    extract_candidate_keys,
    find_validation_bundle,
    find_validation_pak,
    unity_probe_schemes,
    validate_keys_against_pak,
    validate_keys_against_unity,
)


def test_find_validation_pak_picks_largest(tmp_path):
    small = tmp_path / "pakchunk0-WindowsNoEditor.pak"
    big = tmp_path / "pakchunk5-WindowsNoEditor.pak"
    small.write_bytes(b"\x00" * 100)
    big.write_bytes(b"\x00" * 500)
    assert find_validation_pak(str(tmp_path)) == str(big)


def test_find_validation_pak_accepts_file_directly(tmp_path):
    pak = tmp_path / "direct.pak"
    pak.write_bytes(b"\x00" * 32)
    assert find_validation_pak(str(pak)) == str(pak)


def test_find_validation_pak_raises_without_pak(tmp_path):
    from pytest import raises

    with raises(RuntimeError):
        find_validation_pak(str(tmp_path))


def test_find_validation_pak_prefers_largest_in_tree(tmp_path):
    root = tmp_path / "Game"
    nested = root / "Content" / "Paks"
    nested.mkdir(parents=True)
    for name, size in (("patch.pak", 100), ("main.pak", 900), ("tiny.pak", 20)):
        (nested / name).write_bytes(b"\x00" * size)
    assert find_validation_pak(str(root)) == str(nested / "main.pak")


def test_find_validation_pak_skips_media_paks(tmp_path):
    root = tmp_path / "Game"
    nested = root / "Content" / "Paks"
    nested.mkdir(parents=True)
    videos = nested / "pakchunk2-videos1080-WindowsClient.pak"
    videos.write_bytes(b"\x00" * 4400)
    content = nested / "pakchunk1-WindowsClient.pak"
    content.write_bytes(b"\x00" * 1200)
    assert find_validation_pak(str(root)) == str(content)


def test_find_validation_pak_falls_back_to_media_only(tmp_path):
    nested = tmp_path / "Content" / "Paks"
    nested.mkdir(parents=True)
    big_video = nested / "pakchunk-movies-WindowsNoEditor.pak"
    big_video.write_bytes(b"\x00" * 900)
    small_video = nested / "pakchunk-trailer.pak"
    small_video.write_bytes(b"\x00" * 100)
    assert find_validation_pak(str(tmp_path)) == str(big_video)


def test_cleanup_hunt_json_ignores_foreign_dirs(tmp_path):
    from dualforge.crack import _cleanup_hunt_json

    foreign = tmp_path / "other"
    foreign.mkdir()
    marker = foreign / "keep.json"
    marker.write_text("{}", encoding="utf-8")
    _cleanup_hunt_json(str(marker))
    assert marker.exists()


def test_run_ghidra_hunt_invokes_script(monkeypatch):
    import dualforge.crack as crack_module

    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = "out"
        stderr = ""

    def fake_run(cmd, capture_output=None, text=None, timeout=None, creationflags=None):
        calls["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(crack_module.subprocess, "run", fake_run)
    rc, output, json_path = crack_module.run_ghidra_hunt("game.exe", "C:/ghidra", 5)
    assert rc == 0
    assert json_path
    assert "game.exe" in (" ".join(calls["cmd"]))
    assert "--no-add-keystore" in calls["cmd"]
    crack_module._cleanup_hunt_json(json_path)


def test_extract_candidate_keys_filters_64_hex(tmp_path):
    out = tmp_path / "candidates.json"
    out.write_text(
        json.dumps(
            {
                "matches": [
                    {
                        "candidates": [
                            {"hex": "aabb" * 16},
                            {"hex": "0x" + "cc" * 32},
                            {"hex": "deadbeef"},
                        ]
                    }
                ]
            }
        )
    )
    keys = extract_candidate_keys(str(out))
    assert keys == ["aabb" * 16, "cc" * 32]


def test_extract_candidate_keys_missing_or_invalid(tmp_path):
    assert extract_candidate_keys(None) == []
    assert extract_candidate_keys(str(tmp_path / "none.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert extract_candidate_keys(str(bad)) == []


def test_validate_keys_against_pak_dedupes_and_uses_validate(monkeypatch, tmp_path):
    pak = tmp_path / "game.pak"
    pak.write_bytes(b"\x00" * 2048)
    calls: dict = {"probe": None, "keys": []}

    def fake_probe(raw, count=16):
        calls["probe"] = raw
        return [b"A" * 16, b"B" * 16]

    def fake_validate(block, scheme, key, archive_name="", guid="", parameters=None):
        calls.setdefault("keys", []).append((scheme, key))
        return scheme == "aes-256" and key == "11" * 32

    monkeypatch.setattr("dualforge.crack.probe_pak_blocks", fake_probe)
    monkeypatch.setattr("dualforge.crack.validate_key", fake_validate)

    verified = validate_keys_against_pak(
        str(pak), ["11" * 32, "22" * 32, "11" * 32]
    )
    assert [v.key for v in verified] == ["11" * 32]
    assert verified[0].scheme == "aes-256"
    # Dedup: the winning key matches plain AES on its very first block; the
    # losing key is tried on both blocks before other schemes are probed.
    assert [k for s, k in calls["keys"] if s == "aes-256"] == [
        "11" * 32,
        "22" * 32,
        "22" * 32,
    ]
    # The scheme-aware probe went beyond plain AES (per-game presets included).
    schemes = sorted({s for s, _ in calls["keys"]})
    assert len(schemes) > 1
    assert "derived-aes-md5" in schemes


def test_validate_keys_against_pak_restricts_scheme(monkeypatch, tmp_path):
    pak = tmp_path / "game.pak"
    pak.write_bytes(b"\x00" * 2048)
    schemes: list[str] = []

    def fake_probe(raw, count=16):
        return [b"A" * 16]

    def fake_validate(block, scheme, key, archive_name="", guid="", parameters=None):
        schemes.append(scheme)
        return False

    monkeypatch.setattr("dualforge.crack.probe_pak_blocks", fake_probe)
    monkeypatch.setattr("dualforge.crack.validate_key", fake_validate)

    assert validate_keys_against_pak(str(pak), ["11" * 32], scheme="aes-256") == []
    assert set(schemes) == {"aes-256"}


def test_validate_keys_against_pak_no_blocks(monkeypatch, tmp_path):
    pak = tmp_path / "game.pak"
    pak.write_bytes(b"\x00" * 100)

    def fake_probe(raw, count=16):
        return []

    monkeypatch.setattr("dualforge.crack.probe_pak_blocks", fake_probe)
    assert validate_keys_against_pak(str(pak), ["11" * 32]) == []


def test_pak_probe_schemes_always_includes_aes_and_more():
    from dualforge.crack import pak_probe_schemes

    probes = pak_probe_schemes(pak_name="pakchunk0-WindowsNoEditor.pak")
    names = [name for name, _scheme, _params in probes]
    assert names[0] == "aes-256"
    assert len(names) > 1
    assert "delta-force" in names or "marvel-rivals" in names
    assert "wuthering-waves" not in names


def test_pak_probe_schemes_guessed_game_first():
    from dualforge.crack import pak_probe_schemes

    probes = pak_probe_schemes(game="DeltaForce")
    name, scheme_string, params = probes[0]
    assert name == "delta-force"
    assert scheme_string == "aes-256+xor8"
    assert params == {}
    # aes+xor8 appears exactly once despite Delta Force / Marvel Rivals sharing it
    names = [n for n, _s, _p in probes]
    signature = names.count("delta-force") + names.count("marvel-rivals")
    assert signature == 1


class _FakeJsonResult:
    def __init__(self, keys):
        self._keys = keys

    def write_text(self, *args, **kwargs):
        pass


def test_crack_all_scans_every_binary_and_dedupes(monkeypatch, tmp_path):
    class FakeHeadless:
        parents = ("", "")
        name = "analyzeHeadless.bat"

    monkeypatch.setattr(
        "dualforge.crack.find_game_executable",
        lambda folder: (
            "game.exe",
            [("game.exe", 200.0), ("GameClient.exe", 150.0), ("launcher.exe", -1.0)],
        ),
    )
    monkeypatch.setattr(
        "dualforge.crack.find_validation_pak",
        lambda folder: str(tmp_path / "g.pak"),
    )

    import dualforge.crack as crack_module

    monkeypatch.setattr(
        "dualforge.ghidra.manager.ensure_ghidra",
        lambda download=True, ghidra_home=None: FakeHeadless(),
    )
    monkeypatch.setattr("dualforge.ghidra.manager.ensure_java", lambda download=True: None)

    hunts = {}

    def fake_hunt(binary, ghidra_home, startup_timeout=300):
        hunts[binary] = True
        return 0, "ok", str(tmp_path / f"{Path(binary).stem}.json")

    monkeypatch.setattr(crack_module, "run_ghidra_hunt", fake_hunt)

    def fake_extract(json_path):
        if "game" in json_path:
            return ["11" * 32, "22" * 32]
        return ["22" * 32, "33" * 32]

    monkeypatch.setattr(crack_module, "extract_candidate_keys", fake_extract)
    monkeypatch.setattr(
        crack_module,
        "validate_keys_against_pak",
        lambda pak, keys, block_count=16, scheme=None, game="": [
            VerifiedKey(key="22" * 32, scheme="aes-256", game="")
        ],
    )

    class FakeStore:
        def __init__(self):
            self.entries = []

        def add(self, title, key, engine="", notes="", scheme="aes-256", parameters=None):
            self.entries.append(title)

    monkeypatch.setattr(crack_module, "KeyStore", FakeStore)

    result = crack_all(str(tmp_path))
    # Only the two positively-scored binaries were scanned (launcher filtered out).
    assert hunts == {"game.exe": True, "GameClient.exe": True}
    assert result["candidates"] == ["11" * 32, "22" * 32, "33" * 32]
    assert result["verified"] == ["22" * 32]
    assert result["saved"] == [f"{Path(str(tmp_path)).stem} [cracked-1]"]
    assert result["status"] == "ok"


def test_crack_all_skips_failed_hunts_and_reports_no_key(monkeypatch, tmp_path):
    class FakeHeadless:
        parents = ("", "")
        name = "analyzeHeadless.bat"

    monkeypatch.setattr(
        "dualforge.crack.find_game_executable",
        lambda folder: ("game.exe", [("game.exe", 200.0)]),
    )
    monkeypatch.setattr(
        "dualforge.crack.find_validation_pak",
        lambda folder: str(tmp_path / "g.pak"),
    )

    import dualforge.crack as crack_module

    monkeypatch.setattr(
        "dualforge.ghidra.manager.ensure_ghidra",
        lambda download=True, ghidra_home=None: FakeHeadless(),
    )
    monkeypatch.setattr("dualforge.ghidra.manager.ensure_java", lambda download=True: None)
    monkeypatch.setattr(
        crack_module,
        "run_ghidra_hunt",
        lambda binary, ghidra_home, startup_timeout=300: (20, "boom", ""),
    )
    monkeypatch.setattr(crack_module, "extract_candidate_keys", lambda path: [])
    monkeypatch.setattr(
        crack_module,
        "validate_keys_against_pak",
        lambda pak, keys, block_count=16: [],
    )

    result = crack_all(str(tmp_path), save_keys=False)
    assert result["status"] == "no_valid_key"
    assert result["hunt_results"][0]["status"] == "hunt_failed"
    assert result["candidates"] == []


def _xor_mask(data: bytes, mask: str) -> bytes:
    key = mask.encode("ascii")
    return bytes(byte ^ key[i % len(key)] for i, byte in enumerate(data))


def test_unity_probe_schemes_only_cn_mask():
    assert unity_probe_schemes() == [("unity-cn", "unity-cn", {"header_bytes": "0"})]


def test_find_validation_bundle_accepts_file(tmp_path):
    bundle = tmp_path / "data.unity3d"
    bundle.write_bytes(b"\x00" * 32)
    assert find_validation_bundle(str(bundle)) == str(bundle.resolve())


def test_find_validation_bundle_picks_largest(tmp_path):
    small = tmp_path / "a.bundle"
    big = tmp_path / "b.bundle"
    small.write_bytes(b"\x00" * 16)
    big.write_bytes(b"\x00" * 64)
    assert find_validation_bundle(str(tmp_path)) == str(big.resolve())


def test_extract_candidate_keys_ascii_preserves_case(tmp_path):
    out = tmp_path / "candidates.json"
    out.write_text(
        json.dumps(
            {
                "matches": [
                    {
                        "candidates": [
                            {"hex": "gHjKlMnOpQrStUvW", "source": "ascii"},
                            {"hex": "AABB" * 16, "source": "entropy"},
                        ]
                    }
                ]
            }
        )
    )
    assert extract_candidate_keys(str(out)) == ["aabb" * 16]
    assert extract_candidate_keys(str(out), allow_ascii=True) == [
        "gHjKlMnOpQrStUvW",
        "aabb" * 16,
    ]


def test_validate_keys_against_unity_recovers_mask(monkeypatch, tmp_path):
    mask = "gHjKlMnOpQrStUvW"
    enc = _xor_mask(b"UnityFS" + b"\x00" * 9, mask)
    monkeypatch.setattr(
        "dualforge.crack.probe_unity_blocks_file", lambda path, count=4: [enc]
    )
    verified = validate_keys_against_unity(
        str(tmp_path / "game.unity3d"), [mask, "ZqWmXpLrNvTbKjYh"]
    )
    assert [v.key for v in verified] == [mask]
    assert verified[0].scheme == "unity-cn"


def test_validate_keys_against_unity_no_blocks(monkeypatch, tmp_path):
    monkeypatch.setattr("dualforge.crack.probe_unity_blocks_file", lambda path, count=4: [])
    assert validate_keys_against_unity(str(tmp_path / "game.unity3d"), ["gHjKlMnOpQrStUvW"]) == []


def test_crack_unity_validates_and_saves(monkeypatch, tmp_path):
    import dualforge.crack as crack_module
    from dualforge.crack import crack_unity

    mask = "gHjKlMnOpQrStUvW"
    enc = _xor_mask(b"UnityFS" + b"\x00" * 9, mask)
    bundle = tmp_path / "game.unity3d"
    bundle.write_bytes(enc + b"\x00" * 48)
    exe = tmp_path / "Game.exe"
    exe.write_bytes(b"MZ")

    class FakeHeadless:
        parents = ("", "")
        name = "analyzeHeadless.bat"

    saved: list[dict] = []

    class FakeStore:
        def __init__(self, path=None):
            pass

        def add(self, title, key, engine="", notes="", scheme="aes-256", parameters=None):
            saved.append(dict(title=title, key=key, engine=engine, scheme=scheme))

    monkeypatch.setattr(
        "dualforge.ghidra.manager.ensure_ghidra",
        lambda download=True, ghidra_home=None: FakeHeadless(),
    )
    monkeypatch.setattr("dualforge.ghidra.manager.ensure_java", lambda download=True: None)
    monkeypatch.setattr(crack_module, "find_game_executable", lambda folder: (str(exe), []))
    monkeypatch.setattr(crack_module, "find_validation_bundle", lambda folder: str(bundle))
    monkeypatch.setattr(crack_module, "probe_unity_blocks_file", lambda path, count=4: [enc])
    monkeypatch.setattr(
        crack_module,
        "run_ghidra_hunt",
        lambda binary, ghidra_home, startup_timeout=300, ascii_keys=False: (
            0,
            "ok",
            str(tmp_path / "hunt.json"),
        ),
    )
    monkeypatch.setattr(
        crack_module,
        "extract_candidate_keys",
        lambda path, limit=64, allow_ascii=False: [mask],
    )
    monkeypatch.setattr(crack_module, "KeyStore", FakeStore)

    result = crack_unity(str(tmp_path), title="Game")
    assert result["status"] == "ok"
    assert result["engine"] == "unity"
    assert result["verified"] == [mask]
    assert result["saved"] == ["Game [cracked-1]"]
    assert saved == [
        {"title": "Game [cracked-1]", "key": mask, "engine": "unity", "scheme": "unity-cn"}
    ]
