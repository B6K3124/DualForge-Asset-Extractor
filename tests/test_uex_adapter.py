from __future__ import annotations

from pathlib import Path

from dualforge.unreal.uex_adapter import (
    UexAdapter,
    egame_candidates,
    find_usmap,
    normalize_aes_key,
    parse_export_summary,
    parse_mesh_summary,
    parse_search_output,
)


def test_normalize_aes_key():
    assert normalize_aes_key(None) is None
    assert normalize_aes_key("0xabc") == "0xabc"
    assert normalize_aes_key("abc") == "0xabc"
    assert normalize_aes_key("  abc  ") == "0xabc"


def test_egame_candidates_footer_13():
    candidates = egame_candidates("E:/Games/Tekken 8", 13)
    assert candidates[0] == "GAME_UE5_2"  # "tekken 8" folder hint first
    assert "GAME_TEKKEN7" in candidates
    assert candidates.index("GAME_UE5_2") < candidates.index("GAME_TEKKEN7")
    assert "GAME_UE5_4" in candidates
    assert "GAME_UE5_LATEST" in candidates
    assert "GAME_UE6_0" in candidates  # next-generation engines ride on v12 paks


def test_egame_candidates_tekken7_falls_back_to_tekken7():
    candidates = egame_candidates("E:/Games/Tekken 7", 13)
    assert candidates[0] == "GAME_TEKKEN7"  # "tekken 8" should not match
    assert "GAME_UE5_2" not in candidates


def test_egame_candidates_unknown_footer():
    candidates = egame_candidates("E:/Games/Whatever", None)
    assert candidates == ["GAME_UE5_LATEST", "GAME_UE6_LATEST", "GAME_UE4_LATEST"]


def test_egame_candidates_old_pak_bands():
    assert egame_candidates("E:/Games/OldUE3", 3) == [
        "GAME_UE3_0",
        "GAME_UE4_LATEST",  # old-band fallback prefers UE4 over UE5
        "GAME_UE5_LATEST",
        "GAME_UE6_LATEST",
    ]
    candidates_8 = egame_candidates("E:/Games/OldUE4", 8)
    assert candidates_8[0] == "GAME_UE4_10"
    assert "GAME_UE4_16" in candidates_8
    assert candidates_8.index("GAME_UE4_LATEST") < candidates_8.index("GAME_UE5_LATEST")
    # early-UE4 (pak v4) paks get UE4.0-4.3 candidates before the fallbacks
    candidates_4 = egame_candidates("E:/Games/OldUE4", 4)
    assert candidates_4[:4] == [
        "GAME_UE4_0", "GAME_UE4_1", "GAME_UE4_2", "GAME_UE4_3",
    ]
    assert candidates_4[4] == "GAME_UE4_LATEST"


def test_egame_candidates_modern_band_fallback_is_ue5_first():
    candidates = egame_candidates("E:/Games/NewGame", 12)
    assert candidates[-3:] == ["GAME_UE5_LATEST", "GAME_UE6_LATEST", "GAME_UE4_LATEST"]
    assert candidates.index("GAME_UE5_LATEST") < candidates.index("GAME_UE4_LATEST")


def test_game_for_honors_dualforge_egame_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DUALFORGE_EGAME", " GAME_Placeholder ")
    plan = {"doctor": (1, "", "boom")}  # probing must be skipped entirely
    adapter = _adapter(plan)
    game = adapter._game_for(str(tmp_path), None)
    assert game == "GAME_Placeholder"
    assert not adapter._run.calls


def test_parse_search_output():
    assert parse_search_output("Game/A.uasset\nGame/B.wem\n") == ["Game/A.uasset", "Game/B.wem"]
    assert parse_search_output("") == []


def test_parse_export_summary():
    out = "exported: 12 packages, 3 textures, 0 decoded data, 57 raw files -> C:/out"
    assert parse_export_summary(out) == 72
    assert parse_export_summary("nothing here") == 0


class FakeRunner:
    """Scripted uex responses: (exit_code, stdout, stderr) per command name."""

    def __init__(self, plan):
        self.plan = plan
        self.calls = []

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        command = args[0]
        code, out, err = self.plan[command]
        return out, err, code


def _adapter(plan) -> UexAdapter:
    adapter = UexAdapter("fake-uex")
    adapter._run = FakeRunner(plan)
    return adapter


def test_list_files_uses_search_and_probes_game(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "pakchunk0-Windows.pak"
    pak.write_bytes(b"\xe1\x12\x6f\x5a" + b"\x00" * 32)
    plan = {
        "doctor": (1, "profile: dualforge (GAME_UE4_22)\nmounted: 1 archives, 5 files\n", "no packages"),
        "search": (0, "Game/Content/A.uasset\nGame/Content/B.wem\n", ""),
    }
    adapter = _adapter(plan)
    entries = adapter.list_files(str(pak), aes_key="0123")
    assert [e["path"] for e in entries] == ["Game/Content/A.uasset", "Game/Content/B.wem"]
    doctor_call = next(c for c in adapter._run.calls if c[0] == "doctor")
    search_call = next(c for c in adapter._run.calls if c[0] == "search")
    assert "--config" in doctor_call and "--config" in search_call
    assert search_call[search_call.index("--limit") + 1] == "1000000"


def test_probing_tries_next_candidate_on_failure(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    attempts = []

    def run(args, timeout):
        attempts.append(args)
        if args[0] == "doctor":
            import json as _json

            config_path = args[args.index("--config") + 1]
            with open(config_path, encoding="utf-8") as fh:
                game = _json.load(fh)["profiles"]["dualforge"]["game"]
            if len(attempts) == 1:
                return "", f"error: unknown game '{game}'", 1
            return "mounted: 2 archives, 10 files\n", "", 0
        return "Game/x.wem\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    entries = adapter.list_files(str(pak))
    assert entries == [{"path": "Game/x.wem"}]
    doctor_games = [a[a.index("--config") + 1] for a in attempts if a[0] == "doctor"]
    assert len(doctor_games) >= 2
    assert adapter._games[str(paks)]  # cached after success


def test_extract_passes_files_as_only(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    plan = {
        "doctor": (0, "mounted: 1 archives, 3 files\nparse ok: Game/a.uasset\n", ""),
        "export": (0, "exported: 1 packages, 0 textures, 0 decoded data, 2 raw files -> C:/out\n", ""),
    }
    adapter = _adapter(plan)
    count = adapter.extract(str(pak), str(tmp_path / "out"), files=["Game/a.uasset", "Game/b.wem"])
    assert count == 3
    export_call = next(c for c in adapter._run.calls if c[0] == "export")
    assert "Game/a.uasset" in export_call and "Game/b.wem" in export_call


def test_extract_without_files_derives_roots(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    plan = {
        "doctor": (0, "mounted: 1 archives, 3 files\n", ""),
        "search": (0, "Game/a.wem\nEngine/b.ini\n", ""),
        "export": (0, "exported: 0 packages, 0 textures, 0 decoded data, 2 raw files -> C:/out\n", ""),
    }
    adapter = _adapter(plan)
    count = adapter.extract(str(pak), str(tmp_path / "out"))
    assert count == 2
    export_call = next(c for c in adapter._run.calls if c[0] == "export")
    assert "Game" in export_call and "Engine" in export_call


def test_extract_passes_usmap_into_config(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    usmap = tmp_path / "mappings.usmap"
    usmap.write_bytes(b"UM")
    seen = {}

    def run(args, timeout):
        import json as _json

        if args[0] == "doctor":
            config_path = args[args.index("--config") + 1]
            with open(config_path, encoding="utf-8") as fh:
                seen["doctor"] = _json.load(fh)
            return "mounted: 1 archives, 3 files\n", "", 0
        config_path = args[args.index("--config") + 1]
        with open(config_path, encoding="utf-8") as fh:
            seen["export"] = _json.load(fh)
        return "exported: 0 packages, 0 textures, 0 decoded data, 1 raw files -> X\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    adapter.extract(str(pak), str(tmp_path / "out"), files=["Game/a.uasset"], usmap=str(usmap))
    assert seen["doctor"]["profiles"]["dualforge"]["usmap"] == str(usmap)
    assert seen["export"]["profiles"]["dualforge"]["usmap"] == str(usmap)


def test_find_usmap_checks_env_then_home_then_paks(tmp_path: Path, monkeypatch):
    paks = tmp_path / "Paks"
    paks.mkdir()
    monkeypatch.setenv("DUALFORGE_USMAP", "")
    monkeypatch.delenv("DUALFORGE_USMAP", raising=False)
    monkeypatch.setattr("dualforge.unreal.uex_adapter.Path.home", lambda: tmp_path / "home")
    (tmp_path / "home" / ".dualforge").mkdir(parents=True)
    home_map = tmp_path / "home" / ".dualforge" / "game.usmap"
    home_map.write_bytes(b"UM")
    assert find_usmap(str(paks)) == str(home_map)
    paks_map = paks / "local.usmap"
    paks_map.write_bytes(b"UM")
    assert find_usmap(str(paks)) == str(home_map)  # home wins over game folder
    home_map.unlink()
    assert find_usmap(str(paks)) == str(paks_map)
    monkeypatch.setenv("DUALFORGE_USMAP", str(paks_map))
    assert find_usmap(str(paks)) == str(paks_map)


def test_list_files_truncation_raises(tmp_path: Path):
    import pytest

    from dualforge.unreal.bridge import UnrealError

    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    plan = {
        "doctor": (0, "mounted: 1 archives, 3 files\n", ""),
        "search": (0, "a\nb\n", "(2 of 5 matches shown - raise --limit)"),
    }
    adapter = _adapter(plan)
    with pytest.raises(UnrealError, match="cap"):
        adapter.list_files(str(pak))


def test_parse_mesh_summary():
    assert parse_mesh_summary("meshexport: staticmesh -> C:/out.glb\n") == "staticmesh"
    assert parse_mesh_summary("meshexport: SkeletalMesh -> C:/out.glb") == "skeletalmesh"
    assert parse_mesh_summary("meshexport: none") is None
    assert parse_mesh_summary("exported: 0 packages") is None


def test_preview_mesh_returns_glb_and_kind(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    glb = b"glTF" + b"x" * 12

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        out_flag = args[args.index("--out") + 1]
        Path(out_flag).write_bytes(glb)
        assert "--materials" in args
        return f"meshexport: staticmesh -> {out_flag}\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    result = adapter.preview_mesh(str(pak), "Game/a.uasset", aes_key="0123")
    assert result is not None and result[0] == glb and result[1] == "staticmesh"


def test_preview_mesh_skips_materials_when_disabled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DUALFORGE_MESH_TEXTURES", "0")
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    glb = b"glTF" + b"x" * 12

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        out_flag = args[args.index("--out") + 1]
        Path(out_flag).write_bytes(glb)
        assert "--materials" not in args
        return f"meshexport: staticmesh -> {out_flag}\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    result = adapter.preview_mesh(str(pak), "Game/a.uasset", aes_key="0123")
    assert result is not None and result[0] == glb


def test_preview_mesh_none_when_no_mesh(tmp_path: Path):
    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        return "meshexport: none\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    assert adapter.preview_mesh(str(pak), "Game/no-mesh.uasset") is None


def test_preview_mesh_resolves_usmap_when_missing(tmp_path: Path, monkeypatch):
    import json

    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    usmap_file = tmp_path / "game.usmap"
    usmap_file.write_bytes(b"M")

    seen = {}

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        cfg = args[args.index("--config") + 1]
        with open(cfg, encoding="utf-8") as fh:
            seen["cfg"] = json.load(fh)
        out_flag = args[args.index("--out") + 1]
        Path(out_flag).write_bytes(b"glTFx")
        return f"meshexport: staticmesh -> {out_flag}\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    monkeypatch.setattr(
        "dualforge.unreal.uex_adapter.find_usmap", lambda p: str(usmap_file)
    )
    result = adapter.preview_mesh(str(pak), "Game/a.uasset", aes_key="0123")
    assert result is not None and result[1] == "staticmesh"
    assert seen["cfg"]["profiles"]["dualforge"]["usmap"] == str(usmap_file)


def test_preview_mesh_raises_on_exit_code(tmp_path: Path):
    import pytest

    from dualforge.unreal.bridge import UnrealError

    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        return "", "boom", 1

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    with pytest.raises(UnrealError, match="boom"):
        adapter.preview_mesh(str(pak), "Game/a.uasset")


def test_export_mesh_writes_glb(tmp_path: Path):
    from dualforge.unreal.uex_adapter import UexAdapter

    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    glb = b"glTF" + b"x" * 12
    out = tmp_path / "sub" / "model.glb"

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        assert "--materials" in args
        out_flag = args[args.index("--out") + 1]
        Path(out_flag).write_bytes(glb)
        return f"meshexport: skeletalmesh -> {out_flag}\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    result = adapter.export_mesh(str(pak), "Game/a.uasset", str(out), aes_key="0123")
    assert result == "skeletalmesh"
    assert out.read_bytes() == glb


def test_export_mesh_none_when_no_mesh(tmp_path: Path):
    from dualforge.unreal.uex_adapter import UexAdapter

    paks = tmp_path / "Paks"
    paks.mkdir()
    pak = paks / "p.pak"
    pak.write_bytes(b"x" * 8)
    out = tmp_path / "model.glb"

    def run(args, timeout):
        if args[0] == "doctor":
            return "mounted: 1 archives, 3 files\n", "", 0
        return "meshexport: none\n", "", 0

    adapter = UexAdapter("fake-uex")
    adapter._run = run
    assert adapter.export_mesh(str(pak), "Game/no-mesh.uasset", str(out)) is None
    assert not out.exists()