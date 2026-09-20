from __future__ import annotations

from dualforge import cli


def _run_handler(monkeypatch, capsys, result, argv, crack_impl=None):
    namespace = cli.build_parser().parse_args(["crack"] + argv)
    namespace.no_download = True
    namespace.path = "test"
    namespace.ghidra_home = None
    namespace.startup_timeout = 5
    namespace.title = None
    namespace.no_save = False

    def _fake_crack(*args, **kwargs):
        if crack_impl is not None:
            return crack_impl(result)
        return result

    monkeypatch.setattr("dualforge.crack.crack", _fake_crack)
    rc = cli._cmd_crack_run(namespace)
    return rc, capsys.readouterr().out


def test_crack_run_ok_prints_key_and_saved(monkeypatch, capsys):
    result = {
        "status": "ok",
        "exe": "Game.exe",
        "pak": "game.pak",
        "candidates": ["ab" * 32],
        "verified": ["ab" * 32],
        "saved": ["Game [cracked-1]"],
        "returncode": 0,
        "detail": "",
    }
    rc, out = _run_handler(monkeypatch, capsys, result, ["run", "test"])
    assert rc == 0
    assert "cracked key    : " + "ab" * 32 in out
    assert "saved to key store: Game [cracked-1]" in out


def test_crack_run_no_valid_key_returns_nonzero(monkeypatch, capsys):
    result = {
        "status": "no_valid_key",
        "exe": "Game.exe",
        "pak": "game.pak",
        "candidates": ["ab" * 32],
        "verified": [],
        "saved": [],
        "returncode": 0,
        "detail": "",
    }
    rc, out = _run_handler(monkeypatch, capsys, result, ["run", "test"])
    assert rc == 1
    assert "proprietary/obfuscated" in out


def test_crack_run_hunt_failed_returns_nonzero(monkeypatch, capsys):
    result = {
        "status": "hunt_failed",
        "exe": "Game.exe",
        "pak": "game.pak",
        "returncode": 3,
        "detail": "boom",
    }
    rc, out = _run_handler(monkeypatch, capsys, result, ["run", "test"])
    assert rc == 1
    assert "hunt failed (exit 3)" in out


def test_locres_edit_roundtrip(tmp_path, capsys):
    import struct

    from dualforge.unreal.locres import MAGIC, parse_locres

    def fstr(text: str) -> bytes:
        data = text.encode("utf-8") + b"\x00"
        return struct.pack("<i", len(data)) + data

    src = tmp_path / "en.locres"
    payload = bytearray(struct.pack("<I", MAGIC) + bytes([2]))
    payload += struct.pack("<I", 1) + fstr("Menu") + struct.pack("<I", 2)
    payload += fstr("START") + fstr("Start Game")
    payload += fstr("QUIT") + fstr("Quit")
    src.write_bytes(bytes(payload))

    out = tmp_path / "en_new.locres"
    args = cli.build_parser().parse_args(
        ["locres", "edit", str(src), "Menu.START=Begin", "-o", str(out)]
    )
    rc = cli._cmd_locres_edit(args)
    assert rc == 0
    assert "wrote 2 entries" in capsys.readouterr().out
    edited = parse_locres(out.read_bytes())
    assert edited.as_dict() == {"Menu.START": "Begin", "Menu.QUIT": "Quit"}
    assert edited.version == 2


def test_locres_edit_refuses_source_overwrite(tmp_path, capsys):
    import struct

    from dualforge.unreal.locres import MAGIC, parse_locres

    def fstr(text: str) -> bytes:
        data = text.encode("utf-8") + b"\x00"
        return struct.pack("<i", len(data)) + data

    src = tmp_path / "en.locres"
    payload = bytearray(struct.pack("<I", MAGIC) + bytes([3]))
    payload += struct.pack("<I", 1) + fstr("Menu") + struct.pack("<I", 1)
    payload += fstr("START") + fstr("Start Game")
    src.write_bytes(bytes(payload))
    assert parse_locres(src.read_bytes()).as_dict() == {"Menu.START": "Start Game"}

    args = cli.build_parser().parse_args(
        ["locres", "edit", str(src), "Menu.START=Begin", "-o", str(src)]
    )
    rc = cli._cmd_locres_edit(args)
    assert rc == 1
    assert "refusing to overwrite the source" in capsys.readouterr().err


def test_export_mesh_skinned_gltf_and_static_fbx(tmp_path, capsys):
    from tests.test_bethesda import _build_sse_nif, _build_sse_skinned_nif

    skinned = tmp_path / "skinned.nif"
    skinned.write_bytes(_build_sse_skinned_nif())
    static = tmp_path / "static.nif"
    static.write_bytes(_build_sse_nif())

    args = cli.build_parser().parse_args(
        ["export-mesh", str(skinned), "gltf", "-o", str(tmp_path / "skin")]
    )
    assert cli._cmd_export_mesh(args) == 0
    gltf_text = (tmp_path / "skin.gltf").read_text()
    assert "skins" in gltf_text
    assert "JOINTS_0" in gltf_text

    args = cli.build_parser().parse_args(
        ["export-mesh", str(static), "fbx", "-o", str(tmp_path / "static")]
    )
    assert cli._cmd_export_mesh(args) == 0
    fbx_bytes = (tmp_path / "static.fbx").read_bytes().decode("utf-8", "replace")
    assert "Geometry" in fbx_bytes

    args = cli.build_parser().parse_args(["export-mesh", str(tmp_path / "missing.nif")])
    assert cli._cmd_export_mesh(args) == 1
    assert "no such file" in capsys.readouterr().err


def test_export_mesh_glb_skin_passthrough(tmp_path, capsys):
    from dualforge.export.gltf_reader import parse_glb_scene
    from tests.test_gltf_reader import _skinned_glb

    glb = tmp_path / "skinned.glb"
    glb.write_bytes(_skinned_glb())

    args = cli.build_parser().parse_args(
        ["export-mesh", str(glb), "gltf", "-o", str(tmp_path / "out")]
    )
    assert cli._cmd_export_mesh(args) == 0
    gltf_text = (tmp_path / "out.gltf").read_text()
    assert "skins" in gltf_text
    assert "JOINTS_0" in gltf_text
    assert "WEIGHTS_0" in gltf_text

    args = cli.build_parser().parse_args(
        ["export-mesh", str(glb), "fbx", "-o", str(tmp_path / "outfbx")]
    )
    assert cli._cmd_export_mesh(args) == 0
    fbx_bytes = (tmp_path / "outfbx.fbx").read_bytes().decode("utf-8", "replace")
    assert "Deformer" in fbx_bytes
    assert parse_glb_scene(glb.read_bytes()) is not None


def test_export_mesh_cdpr_mesh_and_rig(tmp_path, capsys):
    from tests.test_cdpr_mesh import _mesh_bytes

    mesh = tmp_path / "hero.mesh"
    mesh.write_bytes(_mesh_bytes())
    args = cli.build_parser().parse_args(
        ["export-mesh", str(mesh), "gltf", "-o", str(tmp_path / "hero")]
    )
    assert cli._cmd_export_mesh(args) == 0
    gltf_text = (tmp_path / "hero.gltf").read_text()
    assert "skins" in gltf_text
    assert "JOINTS_0" in gltf_text
    assert "WEIGHTS_0" in gltf_text


def test_export_mesh_cdpr_rig(tmp_path, capsys):
    from tests.test_cdpr_rig import _rig_bytes

    rig = tmp_path / "hero.rig"
    rig.write_bytes(_rig_bytes())
    args = cli.build_parser().parse_args(
        ["export-mesh", str(rig), "fbx", "-o", str(tmp_path / "hero_rig")]
    )
    assert cli._cmd_export_mesh(args) == 0
    fbx_bytes = (tmp_path / "hero_rig.fbx").read_bytes().decode("utf-8", "replace")
    assert "Deformer" in fbx_bytes
