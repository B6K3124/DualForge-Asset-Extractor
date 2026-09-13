from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dualforge.unreal.keys import KeyStore, _extract_keys
from dualforge.unreal.pak import (
    _find_game_oodle,
    _probe_key_list,
    pak_footer_version,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pyuepak") is None,
    reason="pyuepak not installed",
)


class FakeStore:
    def __init__(self, entries):
        self._entries = entries

    def list(self):
        return self._entries


def _fake_entry(title: str, key: str, scheme: str = "aes-256"):
    return SimpleNamespace(title=title, aes_key=key, scheme=scheme)


@pytest.fixture()
def store_path(tmp_path: Path) -> str:
    return str(tmp_path / "keys.json")


# ---------------------------------------------------------------- key probing


def test_probe_key_list_no_key_first(store_path: str, monkeypatch):
    monkeypatch.setattr("dualforge.unreal.KeyStore", lambda: FakeStore([]))
    probes = _probe_key_list(None, try_all_keys=True)
    assert probes[0] == (None, None, True)
    assert len(probes) == 1


def test_probe_key_list_store_then_default(store_path: str, monkeypatch):
    monkeypatch.setattr(
        "dualforge.unreal.KeyStore",
        lambda: FakeStore(
            [
                _fake_entry("Fortnite", "A" * 64),
                _fake_entry("Other", "B" * 64),
                _fake_entry("Dup", "B" * 64),
            ]
        ),
    )
    probes = _probe_key_list("C" * 64, try_all_keys=True)
    titles = [title for title, _, _ in probes]
    assert titles == [None, "Fortnite", "Other", "default"]
    keys = [key for _, key, _ in probes if key]
    assert keys == ["A" * 64, "B" * 64, "C" * 64]
    assert all(can for _, _, can in probes)


def test_probe_key_list_skips_when_disabled(store_path: str, monkeypatch):
    monkeypatch.setattr(
        "dualforge.unreal.KeyStore",
        lambda: FakeStore([_fake_entry("Fortnite", "A" * 64)]),
    )
    probes = _probe_key_list(None, try_all_keys=False)
    assert probes == [(None, None, True)]


def test_probe_key_list_ignores_store_errors(store_path: str, monkeypatch):
    monkeypatch.setattr(
        "dualforge.unreal.KeyStore", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    probes = _probe_key_list("D" * 64, try_all_keys=True)
    assert probes == [(None, None, True), ("default", "D" * 64, True)]


def test_probe_key_list_skips_non_aes_schemes(store_path: str, monkeypatch):
    """Non-AES scheme keys and non-hex keys are not handed to pyuepak."""
    monkeypatch.setattr(
        "dualforge.unreal.KeyStore",
        lambda: FakeStore(
            [
                _fake_entry("Delta Force", "E" * 64, scheme="delta-force"),
                _fake_entry("Unity CN", "XxecodrPeGaka2e6", scheme="unity-cn"),
            ]
        ),
    )
    probes = _probe_key_list(None, try_all_keys=True)
    assert probes == [(None, None, True)]


class FakePakFile:
    """Stand-in for pyuepak's PakFile: index read fails with a wrong key."""

    right_key = "B" * 64
    calls: list = []

    def __init__(self):
        self.key = None
        self.count = 0
        self._index = None
        self._footer = None

    def set_key(self, key):
        self.key = key

    def read(self, path):
        if self.key == self.right_key:
            self.count = 1

    def list_files(self):
        return ["Game/Content/a.bin"] if self.count else []


def test_pak_archive_probes_store_keys(monkeypatch, tmp_path: Path):
    from dualforge.unreal.pak import PakArchive

    target = str(tmp_path / "encrypted.pak")
    Path(target).write_bytes(b"fake pak bytes")

    monkeypatch.setattr(
        "dualforge.unreal.KeyStore",
        lambda: FakeStore([_fake_entry("Wrong", "A" * 64), _fake_entry("Right", "B" * 64)]),
    )

    archive = PakArchive.__new__(PakArchive)
    archive.path = target
    archive._lock = __import__("threading").Lock()
    archive._pak = archive._open(FakePakFile, None, try_all_keys=True)
    assert archive.key_title == "Right"
    assert archive.key_source == "key store"
    assert archive.list_files() == ["Game/Content/a.bin"]


def test_pak_archive_default_key_source(monkeypatch, tmp_path: Path):
    from dualforge.unreal.pak import PakArchive

    target = str(tmp_path / "default_key.pak")
    Path(target).write_bytes(b"fake pak bytes")

    monkeypatch.setattr("dualforge.unreal.KeyStore", lambda: FakeStore([]))
    archive = PakArchive.__new__(PakArchive)
    archive.path = target
    archive._lock = __import__("threading").Lock()
    archive._pak = archive._open(FakePakFile, "B" * 64, try_all_keys=True)
    assert archive.key_title == "default"
    assert archive.key_source == "default key"


def test_pak_archive_all_keys_fail(monkeypatch, tmp_path: Path):
    from dualforge.unreal.pak import PakArchive, PakError

    target = str(tmp_path / "locked.pak")
    Path(target).write_bytes(b"fake pak bytes")

    monkeypatch.setattr("dualforge.unreal.KeyStore", lambda: FakeStore([]))
    archive = PakArchive.__new__(PakArchive)
    archive.path = target
    with pytest.raises(PakError, match="key"):
        archive._open(FakePakFile, None, try_all_keys=True)


# ------------------------------------------------------- game-folder Oodle


def test_find_game_oodle_next_to_archive(tmp_path: Path, monkeypatch):
    game = tmp_path / "game"
    binary = game / "Binaries" / "Win64"
    binary.mkdir(parents=True)
    dll = binary / "oo2core_9_win64.dll"
    dll.write_bytes(b"\x00dll")
    archive = game / "Content" / "Paks" / "game.pak"

    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    found = _find_game_oodle(str(archive))
    assert found is not None
    assert found == dll


def test_find_game_oodle_absent(tmp_path: Path, monkeypatch):
    archive = tmp_path / "game" / "Content" / "Paks" / "game.pak"
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert _find_game_oodle(str(archive)) is None


# ------------------------------------------------------------- footer peek


@pytest.fixture()
def PakFile():
    from dualforge.unreal.pak import _import_pyuepak

    return _import_pyuepak()


def test_pak_footer_version_reads_v12(tmp_path: Path, PakFile):
    import pyuepak.version as version

    target = str(tmp_path / "v12.pak")
    pak = PakFile()
    pak.set_version(version.PakVersion.V12)
    pak.add_file("Game/Content/x.bin", b"payload")
    pak.write(target)
    assert pak_footer_version(target) == 13


def test_pak_footer_version_garbage(tmp_path: Path):
    target = tmp_path / "garbage.pak"
    target.write_bytes(b"not a pak at all, definitely not")
    assert pak_footer_version(str(target)) is None


def test_chunk_key_hint_for_v12(tmp_path: Path, PakFile):
    import pyuepak.version as version

    from dualforge.extract import _chunk_key_hint

    target = str(tmp_path / "v12.pak")
    pak = PakFile()
    pak.set_version(version.PakVersion.V12)
    pak.add_file("Game/Content/x.bin", b"payload")
    pak.write(target)
    assert "chunk" in _chunk_key_hint(str(target))


# ------------------------------------------------------------------- key store


def test_fmodel_json_import(tmp_path: Path, store_path: str):
    fmodel = tmp_path / "Global.AESKeys.json"
    fmodel.write_text(
        json.dumps(
            {
                "Fortnite": {
                    "mainKey": "0x" + "A" * 62,
                    "dynamicKeys": {"guid1": "0x" + "B" * 62},
                },
                "Palworld": {"mainKey": "0x" + "C" * 62},
                "NoKey": {},
            }
        ),
        encoding="utf-8",
    )
    store = KeyStore(store_path)
    count = store.import_fmodel_json(str(fmodel))
    assert count == 2
    entry = store.get_entry("Fortnite")
    assert entry is not None
    assert entry.aes_key == "0x" + "A" * 62
    assert entry.dynamic_keys == {"guid1": "0x" + "B" * 62}
    assert store.get_entry("Palworld").aes_key == "0x" + "C" * 62
    assert store.get_entry("NoKey") is None


def test_fmodel_json_import_updates(store_path: str):
    store = KeyStore(store_path)
    store.add("Fortnite", "A" * 64)
    fmodel = Path(store_path).parent / "keys2.json"
    fmodel.write_text(
        json.dumps(
            {"Fortnite": {"mainKey": "0x" + "D" * 62, "dynamicKeys": {"g": "0x" + "E" * 62}}}
        ),
        encoding="utf-8",
    )
    count = store.import_fmodel_json(str(fmodel))
    assert count == 1
    entry = store.get_entry("Fortnite")
    assert entry.aes_key == "0x" + "D" * 62
    assert entry.dynamic_keys == {"g": "0x" + "E" * 62}


def test_extract_keys_endpoint_shapes(store_path: str):
    fortnite_central = {"mainKey": "0x" + "A" * 62, "dynamicKeys": {"g1": "0x" + "B" * 62}}
    mapping, dynamic = _extract_keys(fortnite_central)
    assert mapping == {"Fortnite": "0x" + "A" * 62}
    assert dynamic == {"Fortnite": {"g1": "0x" + "B" * 62}}

    ue4server = {"games": {"Palworld": "0x" + "C" * 62, "Aion2": {"mainKey": "0x" + "D" * 62}}}
    mapping, dynamic = _extract_keys(ue4server)
    assert mapping == {"Palworld": "0x" + "C" * 62, "Aion2": "0x" + "D" * 62}
    assert dynamic == {}

    plain = {"Game1": "0x" + "E" * 62}
    mapping, dynamic = _extract_keys(plain)
    assert mapping == {"Game1": "0x" + "E" * 62}


def test_import_mapping_merges_dynamic_keys(store_path: str):
    store = KeyStore(store_path)
    store.add("Game", "A" * 64)
    count = store.import_mapping({"Game": "B" * 64}, dynamic={"Game": {"g9": "0x" + "F" * 62}})
    assert count == 1
    entry = store.get_entry("Game")
    assert entry.aes_key == "B" * 64
    assert entry.dynamic_keys == {"g9": "0x" + "F" * 62}
    assert store.import_mapping({"Game": "B" * 64}, dynamic={"Game": {"g9": "0x" + "F" * 62}}) == 0


# -------------------------------------------------------- unity sibling streams


def test_load_sibling_streams_registers_resS(tmp_path: Path, monkeypatch):
    from dualforge.unity.archive import UnityArchive

    (tmp_path / "CAB-1a2b.resS").write_bytes(b"\x00stream")
    (tmp_path / "main.assets").write_bytes(b"fake")

    loaded = []

    class FakeEnv:
        files = {"main.assets": SimpleNamespace(externals=[SimpleNamespace(path="archive:/CAB-1a2b.resS")])}

        def load_file(self, path, is_dependency=False):
            loaded.append((path, is_dependency))

    archive = UnityArchive.__new__(UnityArchive)
    archive.path = str(tmp_path / "main.assets")
    archive.env = FakeEnv()

    count = archive.load_sibling_streams()
    assert count >= 1
    assert any("CAB-1a2b.resS" in path for path, _ in loaded)


def test_load_sibling_streams_no_crash_when_missing(tmp_path: Path):
    from dualforge.unity.archive import UnityArchive

    class FakeEnv:
        files = {}

        def load_file(self, path, is_dependency=False):
            raise FileNotFoundError(path)

    archive = UnityArchive.__new__(UnityArchive)
    archive.path = str(tmp_path / "missing.assets")
    archive.env = FakeEnv()
    assert archive.load_sibling_streams() == 0


# ---------------------------------------------------------------- settings


def test_settings_roundtrip_unlock_fields(tmp_path: Path):
    from dualforge.ui.settings import Settings

    path = str(tmp_path / "settings.json")
    settings = Settings()
    settings._path = path
    settings.try_all_keys = False
    settings.sync_endpoints = ["https://a.example/", "https://b.example/"]
    settings.save()

    loaded = Settings.load(path)
    assert loaded.try_all_keys is False
    assert loaded.sync_endpoints == ["https://a.example/", "https://b.example/"]


def test_settings_defaults():
    from dualforge.ui.settings import Settings

    assert Settings().try_all_keys is True
    assert Settings().sync_endpoints == []


# ---------------------------------------------------------------- key schemes


def test_keyentry_scheme_roundtrip(store_path: str):
    from dualforge.unreal.keys import KeyEntry

    entry = KeyEntry(
        title="Delta Force",
        aes_key="0x" + "A" * 64,
        scheme="delta-force",
        guid="abc",
        parameters={"xor_key": "1122334455667788"},
        dynamic_keys={"g1": "0x" + "B" * 64},
    )
    restored = KeyEntry.from_dict(entry.as_dict())
    assert restored.scheme == "delta-force"
    assert restored.guid == "abc"
    assert restored.parameters == {"xor_key": "1122334455667788"}
    assert restored.dynamic_keys == {"g1": "0x" + "B" * 64}


def test_keyentry_to_material(store_path: str):
    from dualforge.unreal.keys import KeyEntry

    entry = KeyEntry(title="G", aes_key="AB" * 32, scheme="xor8", parameters={"start": "0"})
    material = entry.to_material()
    assert material.scheme == "xor8"
    assert material.parameters == {"start": "0"}


def test_keystore_persists_scheme_fields(store_path: str):
    store = KeyStore(store_path)
    store.add(
        "Snowbreak",
        "CD" * 32,
        scheme="derived-aes-md5",
        guid="g9",
        parameters={"derived_name": "pakchunk0-Windows"},
    )
    reloaded = KeyStore(store_path)
    entry = reloaded.get_entry("Snowbreak")
    assert entry.scheme == "derived-aes-md5"
    assert entry.guid == "g9"
    assert entry.parameters == {"derived_name": "pakchunk0-Windows"}


def test_find_for_archive_matches_game_folder(store_path: str):
    store = KeyStore(store_path)
    store.add("Delta Force", "AB" * 32)
    store.add("Other", "CD" * 32)
    hit = store.find_for_archive(r"C:\Games\DeltaForce\Content\Paks\pak.pak")
    assert hit is not None and hit.title == "Delta Force"
    miss = store.find_for_archive(r"C:\Games\Unrelated\Content\Paks\pak.pak")
    assert miss is None