"""Tests for the dualforge.drivers package."""

from __future__ import annotations

import json
from pathlib import Path

from dualforge.drivers import GameDriver, registry
from dualforge.drivers.defaults import BUILTIN_DRIVERS
from dualforge.drivers.driver import DRIVER_MAGIC, DRIVER_VERSION


# ── GameDriver serialization ─────────────────────────────────────────


def test_driver_roundtrip_dict():
    driver = GameDriver(
        name="test-game",
        label="Test Game",
        engine="unreal",
        game_fragments=["TestGame"],
        encryption_scheme="aes-256+xor8",
        egame="GAME_Test",
        export_formats={"Texture2D": "png"},
        tags=["test"],
    )
    data = driver.to_dict()
    restored = GameDriver.from_dict(data)
    assert restored.name == "test-game"
    assert restored.label == "Test Game"
    assert restored.engine == "unreal"
    assert restored.game_fragments == ["TestGame"]
    assert restored.encryption_scheme == "aes-256+xor8"
    assert restored.egame == "GAME_Test"
    assert restored.export_formats == {"Texture2D": "png"}
    assert restored.tags == ["test"]
    assert data[DRIVER_MAGIC] == DRIVER_VERSION


def test_driver_roundtrip_json():
    driver = GameDriver(
        name="json-test",
        label="JSON Test",
        game_fragments=["JsonTest"],
        encryption_params={"xor_key": "deadbeef"},
    )
    text = driver.to_json()
    restored = GameDriver.from_json(text)
    assert restored.name == "json-test"
    assert restored.encryption_params == {"xor_key": "deadbeef"}


def test_driver_from_dict_ignores_unknown_keys():
    data = {
        "name": "extra",
        "label": "Extra",
        "unknown_field": "should be ignored",
        "another": 42,
    }
    driver = GameDriver.from_dict(data)
    assert driver.name == "extra"
    assert not hasattr(driver, "unknown_field")


def test_driver_defaults():
    driver = GameDriver(name="minimal", label="Minimal")
    assert driver.engine == "auto"
    assert driver.encryption_scheme == "aes-256"
    assert driver.game_fragments == []
    assert driver.archive_patterns == []
    assert driver.egame == ""
    assert driver.usmap_required is False
    assert driver.unity_cn is False
    assert driver.export_formats == {}
    assert driver.asset_filter == []
    assert driver.cli_args == {}


# ── matching ─────────────────────────────────────────────────────────


def test_match_by_game_fragment():
    driver = GameDriver(
        name="fortnite",
        label="Fortnite",
        game_fragments=["Fortnite", "FortniteGame"],
    )
    score = driver.matches("/games/FortniteGame/Content/Paks/pakchunk0-Windows.pak")
    assert score >= 100.0


def test_match_by_archive_pattern():
    driver = GameDriver(
        name="test",
        label="Test",
        game_fragments=[],
        archive_patterns=["pakchunk*-Windows.pak"],
    )
    score = driver.matches("/some/path/pakchunk0-Windows.pak")
    assert score >= 50.0


def test_no_match():
    driver = GameDriver(
        name="unrelated",
        label="Unrelated",
        game_fragments=["XYZZY"],
        archive_patterns=["*.xyz"],
    )
    score = driver.matches("/games/Fortnite/pakchunk0-Windows.pak")
    assert score == 0.0


def test_match_combined_score():
    driver = GameDriver(
        name="fortnite",
        label="Fortnite",
        game_fragments=["Fortnite"],
        archive_patterns=["pakchunk*-Windows.pak"],
    )
    score = driver.matches(
        "/games/Fortnite/Content/Paks/pakchunk0-Windows.pak",
        mount="FortniteGame",
    )
    assert score >= 150.0  # fragment (100) + mount fragment (100) + pattern (50)


def test_match_engine_hint():
    driver = GameDriver(
        name="unity-game",
        label="Unity Game",
        engine="unity",
        game_fragments=["MyUnityGame"],
    )
    score_unreal = driver.matches("/games/MyUnityGame/game.pak")
    score_unity = driver.matches("/games/MyUnityGame/game.unity3d")
    assert score_unity > score_unreal


# ── file I/O ─────────────────────────────────────────────────────────


def test_driver_save_and_load(tmp_path):
    driver = GameDriver(
        name="file-test",
        label="File Test",
        game_fragments=["FileTest"],
        notes="saved to disk",
    )
    path = tmp_path / "file-test.dualforge-driver.json"
    written = driver.save(str(path))
    assert Path(written).exists()

    loaded = GameDriver.load(str(path))
    assert loaded.name == "file-test"
    assert loaded.notes == "saved to disk"


def test_driver_save_default_path(tmp_path, monkeypatch):
    import importlib

    reg_module = importlib.import_module("dualforge.drivers.registry")
    monkeypatch.setattr(reg_module, "DEFAULT_DRIVERS_DIR", tmp_path)
    driver = GameDriver(name="auto-save", label="Auto Save")
    written = driver.save()
    assert Path(written).parent == tmp_path
    assert Path(written).exists()


# ── registry ─────────────────────────────────────────────────────────


def test_registry_has_builtin_drivers():
    names = registry.names()
    assert "fortnite" in names
    assert "delta-force" in names
    assert "snowbreak" in names
    assert "generic-unity" in names
    assert "generic-unreal" in names


def test_registry_has_popular_moddable_games():
    names = registry.names()
    expect = [
        # bethesda
        "oblivion", "fallout-new-vegas", "oblivion-remastered",
        # unity
        "valheim", "subnautica", "grounded", "cities-skylines",
        "kerbal-space-program", "sons-of-the-forest", "seven-days-to-die",
        # unreal
        "lethal-company", "deep-rock-galactic", "satisfactory",
        # documented-but-unsupported (huge communities)
        "cyberpunk-2077", "baldurs-gate-3",
    ]
    for name in expect:
        assert name in names, f"missing driver: {name}"


def test_popular_game_drivers_are_valid_json():
    names = {d.name: d for d in BUILTIN_DRIVERS}
    for _name, driver in names.items():
        text = driver.to_json()
        raw = json.loads(text)
        assert DRIVER_MAGIC in raw
        assert raw["name"] == driver.name


def test_unsupported_format_drivers_do_not_hijack():
    """The REDengine / Larian drivers must not match arbitrary .archive/.pak
    files just by extension, or they'd mis-route every such archive."""
    from dualforge.drivers import GameDriver

    cp = GameDriver(name="cyberpunk-2077", label="Cyberpunk 2077", engine="auto",
                    game_fragments=["cyberpunk"])
    bg3 = GameDriver(name="baldurs-gate-3", label="Baldur's Gate 3", engine="auto",
                     game_fragments=["baldurs gate 3", "BaldursGate3"])
    unrelated_archive = cp.matches("/games/SomeOtherGame/archive/engine4.archive")
    unrelated_pak = bg3.matches("/games/SomeOtherGame/Pak/foo.pak")
    assert unrelated_archive == 0.0
    assert unrelated_pak == 0.0
    # They still win when the path names the game.
    assert cp.matches("/games/Cyberpunk 2077/r6/archive.pak") > 0.0
    assert bg3.matches("/games/BaldursGate3/Data/Shared.pak") > 0.0


def test_registry_builtin_count():
    assert len(BUILTIN_DRIVERS) >= 14
    assert len(registry.list()) >= 14


def test_registry_register_and_get():
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    driver = GameDriver(name="custom", label="Custom")
    reg.register(driver)
    assert reg.get("custom") is not None
    assert reg.get("custom").label == "Custom"


def test_registry_remove():
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    reg.register(GameDriver(name="removeme", label="Remove Me"))
    assert reg.get("removeme") is not None
    assert reg.remove("removeme")
    assert reg.get("removeme") is None


def test_registry_remove_nonexistent():
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    assert not reg.remove("does-not-exist")


def test_registry_match():
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    reg.register(
        GameDriver(
            name="test-match",
            label="Test Match",
            game_fragments=["TestMatch"],
        )
    )
    result = reg.match("/games/TestMatch/file.pak")
    assert result is not None
    assert result.name == "test-match"


def test_registry_match_filters_by_engine():
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    reg.register(
        GameDriver(name="unity-only", label="Unity Only", engine="unity", game_fragments=["X"])
    )
    reg.register(
        GameDriver(name="unreal-only", label="Unreal Only", engine="unreal", game_fragments=["X"])
    )
    result = reg.match("/games/X/file.pak", engine="unreal")
    assert result is not None
    assert result.name == "unreal-only"


def test_registry_save(tmp_path):
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    driver = GameDriver(name="exported", label="Exported")
    reg.register(driver)
    path = tmp_path / "exported.dualforge-driver.json"
    reg.save(driver, str(path))
    assert path.exists()


def test_registry_load_file(tmp_path):
    from dualforge.drivers.registry import DriverRegistry

    path = tmp_path / "loaded.dualforge-driver.json"
    path.write_text(
        GameDriver(name="loaded", label="Loaded", game_fragments=["Loaded"]).to_json(),
        encoding="utf-8",
    )
    reg = DriverRegistry()
    reg._loaded = True
    loaded = reg.load_file(str(path))
    assert loaded.name == "loaded"


def test_registry_load_dir(tmp_path):
    from dualforge.drivers.registry import DriverRegistry

    for i in range(3):
        path = tmp_path / f"dir-test-{i}.dualforge-driver.json"
        path.write_text(
            GameDriver(name=f"dir-{i}", label=f"Dir {i}").to_json(),
            encoding="utf-8",
        )
    reg = DriverRegistry()
    reg._loaded = True
    count = reg.load_dir(str(tmp_path))
    assert count == 3
    assert reg.get("dir-0") is not None
    assert reg.get("dir-2") is not None


def test_registry_export_all(tmp_path):
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    reg.register(GameDriver(name="exp-a", label="A"))
    reg.register(GameDriver(name="exp-b", label="B"))
    out = tmp_path / "exported"
    count = reg.export_all(str(out))
    assert count == 2
    assert (out / "exp-a.dualforge-driver.json").exists()
    assert (out / "exp-b.dualforge-driver.json").exists()


def test_registry_reload():
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._loaded = True
    count_before = len(reg.list())
    reg.register(GameDriver(name="temp-driver", label="Temp"))
    assert len(reg.list()) == count_before + 1
    reg.reload()
    assert reg.get("temp-driver") is None


# ── JSON file format validation ──────────────────────────────────────


def test_json_file_has_magic_key(tmp_path):
    driver = GameDriver(name="magic-test", label="Magic Test")
    path = tmp_path / "magic-test.dualforge-driver.json"
    driver.save(str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert DRIVER_MAGIC in raw
    assert raw[DRIVER_MAGIC] == DRIVER_VERSION


def test_builtin_drivers_are_valid_json():
    for driver in BUILTIN_DRIVERS:
        text = driver.to_json()
        raw = json.loads(text)
        assert DRIVER_MAGIC in raw
        assert raw["name"] == driver.name
        assert raw["label"] == driver.label


# ── from-scratch driver building ─────────────────────────────────────


def test_build_driver_from_archive_engine(tmp_path):
    from dualforge.drivers import build_driver_from_archive

    archive = tmp_path / "MyGame"
    archive.mkdir()
    pak = archive / "pakchunk0-Windows.pak"
    pak.write_bytes(b"x" * 32)
    driver = build_driver_from_archive(str(pak), name="my-game", label="My Game")
    assert isinstance(driver, GameDriver)
    assert driver.name == "my-game"
    assert driver.label == "My Game"
    # .pak -> unreal
    assert driver.engine == "unreal"
    assert driver.encryption_scheme in ("aes-256", "fortnite")


def test_build_driver_derives_name_from_folder(tmp_path):
    from dualforge.drivers import build_driver_from_archive

    archive = tmp_path / "CoolGame"
    archive.mkdir()
    pak = archive / "game.pak"
    pak.write_bytes(b"x" * 32)
    driver = build_driver_from_archive(str(pak))
    assert driver.name == "coolgame"
    assert driver.archive_patterns == ["game.pak"]


def test_build_driver_export_formats_for_unity(tmp_path):
    from dualforge.drivers import build_driver_from_archive

    archive = tmp_path / "UnityGame"
    archive.mkdir()
    bundle = archive / "assets.unity3d"
    bundle.write_bytes(b"UnityFS\x00" + bytes(28))
    driver = build_driver_from_archive(str(bundle))
    assert driver.engine == "unity"
    assert driver.export_formats.get("Texture2D") == "png"
    assert driver.export_formats.get("AudioClip") == "wav"


def test_build_driver_built_driver_is_saveable(tmp_path):
    from dualforge.drivers import build_driver_from_archive

    archive = tmp_path / "Saveable"
    archive.mkdir()
    pak = archive / "data.pak"
    pak.write_bytes(b"x" * 32)
    driver = build_driver_from_archive(str(pak), name="saveable-test")
    path = tmp_path / "out.dualforge-driver.json"
    written = driver.save(str(path))
    assert Path(written).exists()

    loaded = GameDriver.load(str(written))
    assert loaded.name == "saveable-test"
    assert loaded.engine == "unreal"


def test_build_driver_from_unrecognized_archive(tmp_path):
    from dualforge.drivers import build_driver_from_archive

    archive = tmp_path / "Unknown"
    archive.mkdir()
    blob = archive / "data.bin"
    blob.write_bytes(b"\x00" * 32)
    driver = build_driver_from_archive(str(blob))
    # Unknown binary should fall back to a usable default
    assert isinstance(driver, GameDriver)
    assert driver.engine in ("auto", "unity", "unreal")
    assert driver.notes


def test_registry_match_prefers_generic_on_ambiguous_tie():
    """An unknown game (no fragment/pattern matched) must not be mislabeled as
    a specific title when every unreal driver only ties on the engine-baseline.
    Regression: unknown paks like 'ABInfinite' used to match 'fortnite' just
    because fortnite happened to be iterated first."""
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._ensure_loaded()
    # An unknown game path (no fragment/pattern matched) must fall back to
    # generic rather than to whichever specific driver is iterated first.
    result = reg.match(
        "/games/UnknownTitle/Content/Paks/pakchunk0-WindowsNoEditor.pak"
    )
    assert result is not None
    assert result.name == "generic-unreal"
    assert result.egame == ""

    # A driver that genuinely matches its game fragment still wins.
    ab = reg.match("/games/ABInfinite/ABInfinite/Content/Paks/pakchunk0-WindowsNoEditor.pak")
    assert ab is not None
    assert ab.name == "ab-infinite"

    # A driver that matches a fragment still wins.
    fortnite = reg.match("/games/FortniteGame/Content/Paks/pakchunk0-Windows.pak")
    assert fortnite is not None
    assert fortnite.name == "fortnite"

    # A driver that matches only via its archive pattern still wins.
    tekken = reg.match("/games/Anything/pakchunk0-Windows.pak")
    assert tekken is not None
    assert tekken.name in (d.name for d in reg._drivers.values() if d.archive_patterns)
    assert tekken.name != "generic-unreal"


def test_registry_match_tie_prefers_specific_subtitle():
    """A Fallout: New Vegas archive inside a 'Fallout New Vegas' folder ties
    between the umbrella 'fallout' driver and 'fallout-new-vegas'; the longer,
    game-specific fragment must win instead of whichever iterates first."""
    from dualforge.drivers.registry import DriverRegistry

    reg = DriverRegistry()
    reg._ensure_loaded()
    result = reg.match(
        "D:/SteamLibrary/steamapps/common/Fallout New Vegas/Data/FalloutNV - Meshes.bsa"
    )
    assert result is not None
    assert result.name == "fallout-new-vegas"

    # A bare Fallout 3 archive still matches the umbrella driver.
    fo3 = reg.match("D:/SteamLibrary/steamapps/common/Fallout 3/Data/Fallout - Meshes.bsa")
    assert fo3 is not None
    assert fo3.name == "fallout"

