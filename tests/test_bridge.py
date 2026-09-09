from __future__ import annotations


def test_find_cli_only_resolves_executables(tmp_path, monkeypatch):
    from dualforge.unreal.bridge import _find_cli

    home = tmp_path / "home"
    df = home / ".dualforge"
    df.mkdir(parents=True)
    (df / "uex.deps.json").write_text('{"runtimeTarget": {}}')
    (df / "uex.runtimeconfig.json").write_text("{}")
    (df / "uex.dll").write_bytes(b"PE")
    (df / "uex.exe").write_bytes(b"MZ")

    monkeypatch.delenv("DUALFORGE_CUE4PARSE", raising=False)
    monkeypatch.setattr("dualforge.unreal.bridge.Path.home", lambda: home)
    monkeypatch.chdir(tmp_path)
    assert _find_cli() == str(df / "uex.exe")


def test_find_cli_prefers_env_pointer(tmp_path, monkeypatch):
    from dualforge.unreal.bridge import _find_cli

    exe = tmp_path / "custom-uex.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("DUALFORGE_CUE4PARSE", str(exe))
    assert _find_cli() == str(exe)