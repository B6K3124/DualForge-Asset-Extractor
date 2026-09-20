"""Tests for the REDengine path-hash database (cdpr.hashes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dualforge.cdpr.hashes import (
    HashDatabase,
    HashDatabaseError,
    default_hash_csv,
    fnv1a64,
    load_hash_database,
)


def test_fnv1a64_known_vector():
    assert fnv1a64("") == 0xCBF29CE484222325
    assert fnv1a64("a") == 0xAF63DC4C8601EC8C
    assert fnv1a64("test_string") == 0xA74A9DF432A5AE3F


def test_from_csv_basic(tmp_path):
    csv = tmp_path / "hashes.csv"
    csv.write_text(
        "hash,path\n"
        "0x0f7b1b2e3c4d5e6f,base/gameplay/game/video/clip.mp4\n"
        "1125899906842624,audio/sounds/fx.mp3\n",
        encoding="utf-8",
    )
    db = HashDatabase.from_csv(str(csv))
    assert db.count == 2
    assert db.resolve(0x0F7B1B2E3C4D5E6F) == "base/gameplay/game/video/clip.mp4"
    assert db.resolve(1125899906842624) == "audio/sounds/fx.mp3"
    assert db.resolve(1) is None


def test_from_csv_path_with_comma(tmp_path):
    csv = tmp_path / "hashes.csv"
    csv.write_text("42,rooms/villa, 2/atlas.bberconverter\n", encoding="utf-8")
    db = HashDatabase.from_csv(str(csv))
    assert db.resolve(42) == "rooms/villa, 2/atlas.bberconverter"


def test_from_hashlist_txt_style(tmp_path):
    txt = tmp_path / "hashlist.txt"
    txt.write_text(
        "0x0f7b1b2e3c4d5e6f  base/gameplay/game/video/clip.mp4\n"
        "987654321023459876\taudio/sounds/fx.mp3\n",
        encoding="utf-8",
    )
    db = HashDatabase.from_csv(str(txt))
    assert db.count == 2
    assert db.resolve(0x0F7B1B2E3C4D5E6F) == "base/gameplay/game/video/clip.mp4"
    assert db.resolve(987654321023459876) == "audio/sounds/fx.mp3"


def test_from_csv_skips_blanks_and_comments(tmp_path):
    csv = tmp_path / "hashes.csv"
    csv.write_text(
        "# comment\n"
        "hash,path\n"
        "\n"
        "; another comment\n"
        "7,audio/x.wem\n",
        encoding="utf-8",
    )
    db = HashDatabase.from_csv(str(csv))
    assert db.count == 1
    assert db.resolve(7) == "audio/x.wem"


def test_from_csv_missing_file_raises(tmp_path):
    with pytest.raises(HashDatabaseError):
        HashDatabase.from_csv(str(tmp_path / "nope.csv"))


@pytest.mark.parametrize(
    "line",
    ["bad row", "zzz,path", "0xZZ,path", "42,"],
)
def test_from_csv_malformed_line_raises(tmp_path, line):
    csv = tmp_path / "hashes.csv"
    csv.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(HashDatabaseError):
        HashDatabase.from_csv(str(csv))


def test_load_default_missing_is_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("DUALFORGE_CDPR_HASHES", raising=False)
    monkeypatch.setattr(
        "dualforge.cdpr.hashes.DEFAULT_HASH_CSV", tmp_path / "missing.csv"
    )
    db = load_hash_database()
    assert db.is_empty
    assert db.count == 0


def test_load_env_override(monkeypatch, tmp_path):
    csv = tmp_path / "env.csv"
    csv.write_text("0x1,data/a.bin\n", encoding="utf-8")
    monkeypatch.setenv("DUALFORGE_CDPR_HASHES", str(csv))
    assert default_hash_csv() == str(csv)
    db = load_hash_database()
    assert db.resolve(1) == "data/a.bin"


def test_load_explicit_path(monkeypatch, tmp_path):
    csv = tmp_path / "explicit.csv"
    csv.write_text("123,video/x.mp4\n", encoding="utf-8")
    db = load_hash_database(str(csv))
    assert db.resolve(123) == "video/x.mp4"


def test_default_hash_csv_falls_back(monkeypatch, tmp_path):
    missing = tmp_path / "missing.csv"
    monkeypatch.delenv("DUALFORGE_CDPR_HASHES", raising=False)
    monkeypatch.setattr("dualforge.cdpr.hashes.DEFAULT_HASH_CSV", missing)
    assert default_hash_csv() == str(missing)


def test_resolve_masks_to_uint64():
    db = HashDatabase(by_hash={0xFFFFFFFFFFFFFFFF: "edge"})
    assert db.resolve(-1) == "edge"
    assert Path(db.path or "x").name == "x"