from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dualforge.ui.offline_crack_dialog import parse_process_text, scheme_choices


def test_parse_process_text_empty():
    assert parse_process_text("") == (None, None)
    assert parse_process_text("   ") == (None, None)


def test_parse_process_text_picker_item():
    assert parse_process_text("Game.exe  (pid 1234)") == ("game.exe", 1234)
    assert parse_process_text("game.exe  (PID 4321)") == ("game.exe", 4321)


def test_parse_process_text_raw_pid():
    assert parse_process_text("4321") == (None, 4321)


def test_parse_process_text_exe_name():
    assert parse_process_text("Game.exe") == ("Game.exe", None)
    assert parse_process_text("ArenaBreakoutInfinite.exe") == ("ArenaBreakoutInfinite.exe", None)


def test_scheme_choices_include_abi_and_auto():
    choices = scheme_choices()
    assert choices[0] == ("", "Auto (pick the scheme from game + pak)")
    assert ("sm4-abi", "SM4 key-table A  (Arena Breakout: Infinite)") in choices
    assert ("aes-256", "Standard AES-256") in choices


def test_scheme_choices_deterministic_and_registered():
    from dualforge.encryption.registry import list_schemes

    first = scheme_choices()
    assert scheme_choices() == first
    registered = {name for name in list_schemes()}
    for value, _label in first:
        if value:
            assert value in registered