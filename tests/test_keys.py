from __future__ import annotations

import json

import pytest

from dualforge.encryption.registry import KeyMaterial
from dualforge.unreal.keys import (
    KeyEntry,
    KeyStore,
    _dynamic_keys,
    _extract_keys,
    _main_key,
)


def test_main_key_prefers_canonical_field():
    assert _main_key({"mainKey": "0xAB", "aes_key": "0xCD"}) == "0xAB"
    assert _main_key({"aes_key": "0xCD"}) == "0xCD"
    assert _main_key({"dynamicKeys": {}}) == ""


def test_dynamic_keys_normalizes_map():
    out = _dynamic_keys({"dynamicKeys": {"AAA": "0x01"}, "dynamic_keys": {"BBB": "0x02"}})
    assert out == {"AAA": "0x01"}
    assert _dynamic_keys({"dynamicKeys": "not-a-dict"}) == {}


def test_entry_roundtrip_dict_and_material():
    entry = KeyEntry(
        title="Game",
        aes_key="CC" * 32,
        dynamic_keys={"G": "0x1"},
        parameters={"header_bytes": "2"},
    )
    material = entry.to_material()
    assert isinstance(material, KeyMaterial)
    assert material.key_str == entry.aes_key
    assert material.parameters == {"header_bytes": "2"}
    restored = KeyEntry.from_dict(entry.as_dict())
    assert restored == entry


def test_entry_from_dict_legacy_shapes():
    assert KeyEntry.from_dict({"title": "G", "key": "AB"}).aes_key == "AB"
    assert KeyEntry.from_dict({"title": "G", "dynamicKeys": {"k": "v"}}).dynamic_keys == {"k": "v"}
    assert KeyEntry.from_dict({"title": "G"}).aes_key == ""


def test_store_add_get_remove_persist(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    store.add("GameX", "AA" * 32, notes="n", dynamic_keys={"G": "0x1"})
    assert store.get("GameX") == "AA" * 32
    assert store.get_entry("GameX").dynamic_keys == {"G": "0x1"}
    reloaded = KeyStore(str(tmp_path / "keys.json"))
    assert reloaded.get("GameX") == "AA" * 32
    assert store.remove("GameX") is True
    assert store.remove("GameX") is False
    assert store.get("GameX") is None


def test_store_add_requires_title_and_key(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    with pytest.raises(ValueError):
        store.add("", "AA" * 32)
    with pytest.raises(ValueError):
        store.add("Game", "")


def test_store_recovers_corrupt_file(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("{ nope", encoding="utf-8")
    store = KeyStore(str(path))
    assert store.list() == []
    assert list(tmp_path.glob("keys.json.corrupt-*"))


def test_store_find_for_archive_matches_path_components(tmp_path):
    store = KeyStore(str(tmp_path / "keys.json"))
    store.add("Fortnite", "11" * 32)
    assert store.find_for_archive("C:/Games/Fortnite/Content/Paks/pakchunk0.pak") is not None
    assert store.find_for_archive("C:/Other/Content/Paks/pak.pak") is None


def test_import_fmodel_json(tmp_path):
    fmodel = tmp_path / "keys.json"
    fmodel.write_text(
        json.dumps(
            {
                "Game A": {"mainKey": "0xAA" * 32},
                "Game B": {"dynamicKeys": {"g": "0x1"}},
            }
        ),
        encoding="utf-8",
    )
    store = KeyStore(str(tmp_path / "store.json"))
    assert store.import_fmodel_json(str(fmodel)) == 1
    assert store.get("Game A") == "0xAA" * 32
    assert store.get("Game B") is None


def test_extract_keys_supports_games_wrapper_shape():
    payload = {
        "games": {
            "Title": {"mainKey": "BB" * 32, "dynamicKeys": {"G": "0x1"}},
        },
    }
    mapping, dynamic = _extract_keys(payload)
    assert mapping["Title"] == "BB" * 32
    assert dynamic["Title"] == {"G": "0x1"}


def test_extract_keys_supports_fortnite_top_level_shape():
    payload = {"mainKey": "CC" * 32, "dynamicKeys": {"F": "0x2"}}
    mapping, dynamic = _extract_keys(payload)
    assert mapping["Fortnite"] == "CC" * 32
    assert dynamic["Fortnite"] == {"F": "0x2"}


def test_extract_keys_plain_string_and_keys_shape():
    mapping, dynamic = _extract_keys({"AHero": "0xDD" * 32})
    assert mapping["AHero"] == "0xDD" * 32
    assert not dynamic
    mapping2, _ = _extract_keys({"keys": {"AHero": "0xEE" * 32}})
    assert mapping2["AHero"] == "0xEE" * 32


def test_extract_keys_ignores_bad_payload():
    assert _extract_keys([]) == ({}, {})
    assert _extract_keys(None) == ({}, {})