from __future__ import annotations

from pathlib import Path

from dualforge.ui.executable import find_game_executable, find_usmap_output, identify_game


def test_ui_executable_delegates_to_autodetect(tmp_path):
    root = tmp_path / "TheGame"
    (root / "Binaries" / "Win64").mkdir(parents=True)
    exe = root / "Binaries" / "Win64" / "TheGame-Win64-Shipping.exe"
    exe.write_bytes(b"\x00" * (60 * 1024 * 1024))
    (root / "Binaries" / "Win64" / "TheGameLauncher.exe").write_bytes(b"\x00" * 1024)
    best = find_game_executable(str(root))
    assert best == str(exe)


def test_ui_executable_returns_none_for_empty_or_missing(tmp_path):
    assert find_game_executable(str(tmp_path)) is None
    assert find_game_executable(str(tmp_path / "nope")) is None


def test_identify_game_matches_driver_fragment():
    driver = identify_game("C:/Games/Delta Force/DeltaForce.exe", folder="")
    assert driver is not None
    assert driver.name == "delta-force"
    assert driver.encryption_scheme == "aes-256+xor8"


def test_identify_game_returns_none_for_unknown():
    assert identify_game("C:/Somewhere/Unknown.exe", "C:/Somewhere") is None


def test_find_usmap_output(tmp_path):
    assert find_usmap_output("C:/Games/X/Game.exe") == str(
        Path.home() / ".dualforge" / "Game.usmap"
    )
    assert find_usmap_output("") is None