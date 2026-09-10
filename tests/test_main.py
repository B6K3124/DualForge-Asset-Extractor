from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent / "main.py"
_spec = importlib.util.spec_from_file_location("dualforge_main", _ROOT)
assert _spec and _spec.loader
_main = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _main
_spec.loader.exec_module(_main)


def test_main_known_cli_command_routes_to_cli(monkeypatch):
    monkeypatch.setattr(_main, "_run_gui", lambda: "GUI")
    from dualforge import cli as _cli

    captured = []
    orig = _cli.main
    try:
        _cli.main = lambda argv: captured.append(argv) or "CLI"
        assert _main.main(["crack", "status"]) == "CLI"
        assert _main.main(["drivers", "list"]) == "CLI"
    finally:
        _cli.main = orig
    assert captured and captured[0] == ["crack", "status"]


def test_main_unknown_arg_falls_back_to_gui(monkeypatch):
    called = []

    def fake_gui(open_path=None):
        called.append(open_path)
        return 7

    monkeypatch.setattr(_main, "_run_gui", fake_gui)
    assert _main.main(["something-random"]) == 7
    assert called == [None]


def test_main_gui_flags_force_gui(monkeypatch):
    monkeypatch.setattr(_main, "_run_gui", lambda open_path=None: 5)
    assert _main.main(["--gui"]) == 5
    assert _main.main(["-g"]) == 5


def test_main_open_flag_forwards_path(monkeypatch):
    captured = []

    def fake_gui(open_path=None):
        captured.append(open_path)
        return 5

    monkeypatch.setattr(_main, "_run_gui", fake_gui)
    assert _main.main(["--open", r"C:\Game\Data\Skyrim - Misc.bsa"]) == 5
    assert captured == [r"C:\Game\Data\Skyrim - Misc.bsa"]
    assert _main.main(["-o", r"C:\Game\Data\Meshes.bsa"]) == 5
    assert captured == [r"C:\Game\Data\Skyrim - Misc.bsa", r"C:\Game\Data\Meshes.bsa"]
