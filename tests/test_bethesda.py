"""Tests for the dualforge.bethesda engine (BSA / BA2 reader)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from dualforge.bethesda import BethesdaArchive, BethesdaError, build_dds
from dualforge.bethesda.writer import build_ba2_dx10, build_ba2_general, build_bsa
from dualforge.detector import BA2_MAGIC, BSA_MAGIC, detect_header
from dualforge.drivers.defaults import BUILTIN_DRIVERS
from dualforge.extract import ExtractOptions, extract_file

FILES = [
    ("meshes/actor/a.nif", "meshes/actor", b"AAA"),
    ("textures/t.dds", "textures", b"B" * 100),
    ("scripts/x.pex", "scripts", b"CCC"),
]
EXPECTED = ["meshes/actor/a.nif", "textures/t.dds", "scripts/x.pex"]


def _write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ── BSA round-trips ──────────────────────────────────────────────────


@pytest.mark.parametrize("version", [103, 104, 105])
@pytest.mark.parametrize("compress", [False, True])
def test_bsa_roundtrip(tmp_path, version, compress):
    path = _write(tmp_path, "game.bsa", build_bsa(files=FILES, version=version, compress=compress))
    archive = BethesdaArchive(path)
    assert archive.format == "BSA"
    assert archive.version == version
    assert list(archive.list_files()) == EXPECTED
    for rel, _folder, data in FILES:
        assert archive.open_file(rel) == data
    assert archive.file_count == len(FILES)


def test_bsa_size_of(tmp_path):
    path = _write(tmp_path, "game.bsa", build_bsa(files=FILES, version=105, compress=True))
    archive = BethesdaArchive(path)
    for rel, _folder, _data in FILES:
        assert archive.size_of(rel) > 0
    with pytest.raises(BethesdaError):
        archive.size_of("missing/file.bin")


def test_bsa_v105_streamed_textures(tmp_path):
    tex = [
        ("textures/actors/dog/dog.dds", "textures/actors/dog", b"DDSTEXT" * 40),
        ("textures/actors/dog/dog_n.dds", "textures/actors/dog", b"NORM" * 50),
    ]
    path = _write(
        tmp_path, "textures0.bsa",
        build_bsa(files=tex, version=105, compress=True, streamed=True),
    )
    archive = BethesdaArchive(path)
    assert archive.file_flags & 0x002
    assert list(archive.list_files()) == [rel for rel, _f, _d in tex]
    for rel, _folder, data in tex:
        assert archive.open_file(rel) == data


def test_bsa_extract_all(tmp_path):
    path = _write(tmp_path, "game.bsa", build_bsa(files=FILES, version=105, compress=True))
    archive = BethesdaArchive(path)
    written = archive.extract_all(str(tmp_path / "out"))
    assert len(written) == len(FILES)
    for rel, _folder, data in FILES:
        assert (tmp_path / "out" / rel).read_bytes() == data


# ── BA2 GNRL round-trips ─────────────────────────────────────────────


@pytest.mark.parametrize("compress", [False, True])
def test_ba2_gnrl_roundtrip(tmp_path, compress):
    gf = [("meshes/a.nif", b"AAA"), ("sound/s.wav", b"Z" * 200)]
    path = _write(tmp_path, "main.ba2", build_ba2_general(files=gf, compress=compress))
    archive = BethesdaArchive(path)
    assert archive.format == "BA2"
    assert archive.type == "GNRL"
    assert list(archive.list_files()) == ["meshes/a.nif", "sound/s.wav"]
    for rel, data in gf:
        assert archive.open_file(rel) == data


# ── BA2 DX10 round-trips + DDS reconstruction ────────────────────────


def test_ba2_dx10_roundtrip(tmp_path):
    pixel = b"\x00\x80\x00" * 64
    tex = [("textures/rock.dds", 8, 8, 2, 71, pixel)]
    path = _write(tmp_path, "textures.ba2", build_ba2_dx10(files=tex, compress=True))
    archive = BethesdaArchive(path)
    assert archive.format == "BA2"
    assert archive.type == "DX10"
    assert list(archive.list_files()) == ["textures/rock.dds"]
    out = archive.open_file("textures/rock.dds")
    assert out[:4] == b"DDS "
    assert len(out) == 124 + len(pixel)
    assert struct.unpack_from("<I", out, 12)[0] == 8   # height
    assert struct.unpack_from("<I", out, 16)[0] == 8   # width
    assert struct.unpack_from("<I", out, 28)[0] == 2   # mip count


def test_ba2_dx10_uncompressed(tmp_path):
    pixel = b"\xff" * 16
    tex = [("textures/metal.dds", 2, 2, 1, 98, pixel)]
    path = _write(tmp_path, "textures.ba2", build_ba2_dx10(files=tex, compress=False))
    archive = BethesdaArchive(path)
    out = archive.open_file("textures/metal.dds")
    # BC7 emits a 20-byte DX10 extended header on top of the 124-byte DDS header.
    assert out[:4] == b"DDS "
    assert len(out) == 124 + 20 + len(pixel)


# ── detection ────────────────────────────────────────────────────────


def test_detect_bsa():
    header = build_bsa(files=FILES, version=105, compress=False)[:32]
    det = detect_header(header, "Skyrim - Meshes.bsa")
    assert det.engine == "bethesda"
    assert det.kind == "bsa"
    assert det.details["bsa_version"] == 105


def test_detect_bsa_v103():
    header = build_bsa(files=FILES, version=103, compress=False)[:32]
    det = detect_header(header, "oblivion.bsa")
    assert det.engine == "bethesda"
    assert det.details["bsa_version"] == 103


def test_detect_ba2_gnrl():
    header = build_ba2_general(files=[("a.txt", b"x")])[:32]
    det = detect_header(header, "Main.ba2")
    assert det.engine == "bethesda"
    assert det.kind == "ba2"
    assert det.details.get("ba2_type") == "GNRL"


def test_detect_ba2_dx10():
    header = build_ba2_dx10(files=[("t.dds", 2, 2, 1, 71, b"\x00" * 8)])[:32]
    det = detect_header(header, "Textures.ba2")
    assert det.engine == "bethesda"
    assert det.kind == "ba2"
    assert det.details.get("ba2_type") == "DX10"


def test_detector_exports_magics():
    assert BSA_MAGIC == b"BSA\x00"
    assert BA2_MAGIC == b"BTD\x00"


# ── extraction integration ───────────────────────────────────────────


def test_extract_bethesda_dispatch(tmp_path):
    path = _write(tmp_path, "Skyrim - Meshes.bsa", build_bsa(files=FILES, version=104, compress=True))
    result = extract_file(path, ExtractOptions(out_dir=str(tmp_path / "out")))
    assert result.detected is not None
    assert result.detected.engine == "bethesda"
    assert result.ok == len(FILES)


def test_extract_bethesda_file_filter(tmp_path):
    path = _write(tmp_path, "mod.ba2", build_ba2_general(files=[("a.txt", b"x"), ("b.txt", b"y")]))
    result = extract_file(
        path,
        ExtractOptions(out_dir=str(tmp_path / "out"), files=["a.txt"]),
    )
    assert result.ok == 1
    assert (tmp_path / "out" / "a.txt").exists()
    assert not (tmp_path / "out" / "b.txt").exists()


def test_extract_rejects_wrong_engine(tmp_path):
    path = _write(tmp_path, "mod.bsa", build_bsa(files=FILES, version=104))
    with pytest.raises(ValueError):
        extract_file(path, ExtractOptions(out_dir=str(tmp_path / "out"), engine="unreal"))


# ── error handling ───────────────────────────────────────────────────


def test_not_a_bethesda_archive(tmp_path):
    bad = _write(tmp_path, "junk.bsa", b"PK\x03\x04" + b"\x00" * 32)
    with pytest.raises(BethesdaError):
        BethesdaArchive(bad)


def test_open_missing_entry(tmp_path):
    path = _write(tmp_path, "g.bsa", build_bsa(files=FILES, version=104))
    archive = BethesdaArchive(path)
    with pytest.raises(BethesdaError):
        archive.open_file("does/not/exist.txt")


def test_unsupported_dxgi_raises():
    with pytest.raises(BethesdaError):
        build_dds(4, 4, 1, dxgi_format=0, is_cubemap=False)


# ── driver entries ───────────────────────────────────────────────────


def test_builtin_bethesda_drivers_present():
    names = {d.name: d for d in BUILTIN_DRIVERS}
    for name in ("skyrim", "fallout", "starfield", "oblivion", "fallout-new-vegas"):
        assert name in names
        assert names[name].engine == "bethesda"


def test_driver_engine_hint_scores_ba2():
    from dualforge.drivers import GameDriver

    driver = GameDriver(name="skyrim", label="Skyrim", engine="bethesda")
    assert driver.matches("/Data/Skyrim - Meshes.ba2") >= 10.0


def test_oblivion_remastered_driver_present():
    names = {d.name: d for d in BUILTIN_DRIVERS}
    driver = names["oblivion-remastered"]
    assert driver.engine == "unreal"
    assert driver.egame == "GAME_UE5_3"
    assert driver.usmap_required is True
    assert "0xDFA62F3EE8304BBF7A6E153F2F88203F823C47BF0A690D3D4793FB3EFA624F3F" in driver.notes
    # Must match a real Oblivion Remastered pak (game path names the game),
    # while an unrelated pak only ever earns the generic unreal extension hint.
    assert driver.matches("/Games/OblivionRemastered/Content/Paks/OblivionRemastered-Windows.pak") > 0.0
    assert driver.matches("/Games/SomeOtherGame/Content/Paks/engine5.pak") <= 10.0


# ── NIF reader (Skyrim SE) ───────────────────────────────────────────


def _nif_sized_string(data: bytes) -> bytes:
    return struct.pack("<I", len(data)) + data


def _nif_export_string(data: bytes) -> bytes:
    return struct.pack("<B", len(data)) + data


def _half(value: float) -> bytes:
    return struct.pack("<H", np.float16(value).view(np.uint16))


def _build_sse_nif() -> bytes:
    """Minimal Skyrim SE NIF: BSTriShape -> BSLightingShaderProperty ->
    BSShaderTextureSet (SSE layout has no Num Textures count)."""
    desc = (0x3 << 44) | 8  # position + UV bits, 8-dword (32 B) stride
    tri_verts = b"".join(
        struct.pack("<3f", x, y, z) + b"\x00\x00" + _half(u) + _half(v) + b"\x00" * 14
        for (x, y, z), (u, v) in [
            ((0.0, 0.0, 0.0), (0.0, 0.0)),
            ((1.0, 0.0, 0.0), (1.0, 0.0)),
            ((0.0, 1.0, 0.0), (0.0, 1.0)),
        ]
    )
    block_shape = b"".join(
        [
            struct.pack("<I", 0),  # name (string idx 0)
            struct.pack("<I", 0xFFFFFFFF),  # num extra (-1 = none)
            struct.pack("<I", 0xE),  # flags
            b"\x00" * 12,  # translation
            b"\x00" * 36,  # rotation
            struct.pack("<f", 1.0),  # scale
            struct.pack("<I", 0xFFFFFFFF),  # collision
            b"\x00" * 16,  # NiBound
            struct.pack("<I", 0xFFFFFFFF),  # skin
            struct.pack("<I", 1),  # shader ref
            struct.pack("<I", 0xFFFFFFFF),  # alpha
            struct.pack("<Q", desc),
            struct.pack("<H", 1),  # num triangles
            struct.pack("<H", 3),  # num vertices
            struct.pack("<I", 96),  # data size
            tri_verts,
            struct.pack("<3H", 0, 1, 2),
        ]
    )
    block_shader = b"".join(
        [
            struct.pack("<i", -1),  # name (none)
            struct.pack("<I", 0),  # num extra
            struct.pack("<i", -1),  # controller
            struct.pack("<I", 0x82400301),  # shader flags 1
            struct.pack("<I", 0x8021),  # shader flags 2
            struct.pack("<2f", 0.0, 0.0),  # uv offset
            struct.pack("<2f", 1.0, 1.0),  # uv scale
            struct.pack("<I", 2),  # texture set ref
        ]
    )
    block_textures = _nif_sized_string(b"textures\\clutter\\WoodMetalStrip01.dds")
    sizes = [len(block_shape), len(block_shader), len(block_textures)]

    types = [b"BSTriShape", b"BSLightingShaderProperty", b"BSShaderTextureSet"]
    body = b"".join(
        [
            b"Gamebryo File Format, Version 20.2.0.7\x0a",
            struct.pack("<I", 0x14020007),  # version (SSE)
            b"\x01",  # endian
            struct.pack("<I", 12),  # user version
            struct.pack("<I", 3),  # num blocks
            struct.pack("<I", 100),  # BS version
            _nif_export_string(b"a"),  # author
            _nif_export_string(b"p"),  # process script
            _nif_export_string(b"e"),  # export script
            struct.pack("<H", len(types)),
            b"".join(_nif_sized_string(t) for t in types),
            struct.pack("<3H", 0, 1, 2),  # block type indices
            struct.pack("<3I", *sizes),
            struct.pack("<II", 1, 9),  # num strings, max len
            _nif_sized_string(b"TestShape"),
            struct.pack("<I", 0),  # num groups
            struct.pack("<I", 1),  # num roots
            struct.pack("<I", 0),
            block_shape,
            block_shader,
            block_textures,
        ]
    )
    return body


def test_nif_sse_header_and_geometry():
    from dualforge.bethesda.nif import parse_nif

    nif = parse_nif(_build_sse_nif())
    assert [b.type_name for b in nif.blocks] == [
        "BSTriShape",
        "BSLightingShaderProperty",
        "BSShaderTextureSet",
    ]
    assert nif.strings == ["TestShape"]
    assert nif.roots == [0]
    assert nif.blocks[2].size == 41


def test_nif_geometry_texture_and_uv():
    from dualforge.bethesda.nif import parse_nif, read_geometry

    geometry = read_geometry(parse_nif(_build_sse_nif()))
    assert geometry.verts.tolist() == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert geometry.normals[:, 2].tolist() == [1.0, 1.0, 1.0]
    assert geometry.tris.tolist() == [0, 1, 2]
    assert geometry.uv.tolist() == [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    assert geometry.texture_name == "clutter/WoodMetalStrip01.dds"


def test_nif_rejects_non_sse_versions():
    from dualforge.bethesda.nif import parse_nif

    blob = _build_sse_nif()
    with pytest.raises(BethesdaError, match="unsupported NIF version"):
        parse_nif(blob[:37] + struct.pack("<I", 0x14020006) + blob[41:])


def _ni_matrix(translation, scale=1.0) -> bytes:
    rot = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    return struct.pack("<3f", *translation) + struct.pack("<9f", *rot) + struct.pack("<f", scale)


def _ni_node(name_idx, children) -> bytes:
    return b"".join(
        [
            struct.pack("<I", name_idx),
            struct.pack("<I", 0xFFFFFFFF),  # num extra (-1 = none)
            struct.pack("<I", 0xE),  # flags
            struct.pack("<3f", 0.0, 0.0, 0.0),  # translation
            struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1),  # rotation
            struct.pack("<f", 1.0),  # scale
            struct.pack("<I", 0xFFFFFFFF),  # collision
            struct.pack("<I", len(children)),  # num children
            b"".join(struct.pack("<I", c) for c in children),
            struct.pack("<I", 0),  # num effects (SSE)
        ]
    )


def _build_sse_skinned_nif() -> bytes:
    """Minimal skinned SSE NIF: skinned BSTriShape + NiSkinningData + bones.

    Two bones (``Root`` -> ``Bone``); vertex rows carry position, UV and the
    ``0x40`` weight/bone payloads (4 half-floats + 4 bytes).
    """
    desc = (0x43 << 44) | 8  # pos + uv + skin bits, 8-dword stride
    rows = [
        # (pos, uv, weights, bones)
        ((0.0, 0.0, 0.0), (0.0, 0.0), (1, 0, 0, 0), (0, 0, 0, 0)),
        ((1.0, 0.0, 0.0), (1.0, 0.0), (0.5, 0.5, 0, 0), (0, 1, 0, 0)),
        ((0.0, 1.0, 0.0), (0.0, 1.0), (0, 1, 0, 0), (1, 0, 0, 0)),
    ]
    tri_verts = b"".join(
        struct.pack("<3f", *pos)
        + b"\x00\x00"  # unused W
        + _half(u)
        + _half(v)
        + b"".join(_half(w) for w in weights)
        + struct.pack("<4B", *bones)
        + b"\x00" * 2  # pad to the 8-dword stride
        for pos, (u, v), weights, bones in rows
    )
    block_shape = b"".join(
        [
            struct.pack("<I", 0),  # name (string idx 0)
            struct.pack("<I", 0xFFFFFFFF),  # num extra
            struct.pack("<I", 0xE),  # flags
            struct.pack("<3f", 0.0, 0.0, 0.0),  # translation
            struct.pack("<9f", 1, 0, 0, 0, 1, 0, 0, 0, 1),  # rotation
            struct.pack("<f", 1.0),  # scale
            struct.pack("<I", 0xFFFFFFFF),  # collision
            b"\x00" * 16,  # NiBound
            struct.pack("<I", 1),  # skin ref -> NiSkinningData
            struct.pack("<I", 0xFFFFFFFF),  # shader ref
            struct.pack("<I", 0xFFFFFFFF),  # alpha ref
            struct.pack("<Q", desc),
            struct.pack("<H", 1),  # num triangles
            struct.pack("<H", 3),  # num vertices
            struct.pack("<I", 96),  # data size
            tri_verts,
            struct.pack("<3H", 0, 1, 2),
        ]
    )
    block_skin = b"".join(
        [
            _ni_matrix((0.0, 0.0, 0.0)),
            struct.pack("<I", 2),  # num bones
            _nif_sized_string(b"Root") + _ni_matrix((0.0, 0.0, 0.0)),
            _nif_sized_string(b"Bone") + _ni_matrix((0.0, 0.0, 1.0)),
        ]
    )
    block_root = _ni_node(1, [3])  # Root -> Bone
    block_bone = _ni_node(2, [])
    sizes = [len(block_shape), len(block_skin), len(block_root), len(block_bone)]

    types = [b"BSTriShape", b"NiSkinningData", b"NiNode", b"NiNode"]
    body = b"".join(
        [
            b"Gamebryo File Format, Version 20.2.0.7\x0a",
            struct.pack("<I", 0x14020007),  # version (SSE)
            b"\x01",  # endian
            struct.pack("<I", 12),  # user version
            struct.pack("<I", 4),  # num blocks
            struct.pack("<I", 100),  # BS version
            _nif_export_string(b"a"),  # author
            _nif_export_string(b"p"),  # process script
            _nif_export_string(b"e"),  # export script
            struct.pack("<H", len(types)),
            b"".join(_nif_sized_string(t) for t in types),
            struct.pack("<4H", 0, 1, 2, 2),  # block type indices
            struct.pack("<4I", *sizes),
            struct.pack("<II", 3, 8),  # num strings, max len
            _nif_sized_string(b"SkinnedShape"),
            _nif_sized_string(b"Root"),
            _nif_sized_string(b"Bone"),
            struct.pack("<I", 0),  # num groups
            struct.pack("<I", 1),  # num roots
            struct.pack("<I", 0),
            block_shape,
            block_skin,
            block_root,
            block_bone,
        ]
    )
    return body


def test_nif_skinned_scene(tmp_path):
    from dualforge.bethesda.nif import parse_nif, read_skinned_geometry
    from dualforge.export.scene import save_scene

    model = read_skinned_geometry(parse_nif(_build_sse_skinned_nif()))
    assert model is not None
    assert model.up_axis == "Z"
    assert model.uv_v_flip is True
    prim = model.meshes[0]
    assert [b.name for b in prim.bones] == ["Root", "Bone"]
    assert [b.parent for b in prim.bones] == [-1, 0]
    assert prim.triangles[0] == [0, 1, 2]
    assert prim.joints[1] == [0, 1, 0, 0]
    assert prim.weights[1] == [0.5, 0.5, 0.0, 0.0]

    written = save_scene(tmp_path / "skin", model, "gltf")
    assert written == [str(tmp_path / "skin.gltf")]
    assert "skins" in (tmp_path / "skin.gltf").read_text()
    assert "JOINTS_0" in (tmp_path / "skin.gltf").read_text()
    written = save_scene(tmp_path / "skin", model, "fbx")
    fbx_text = (tmp_path / "skin.fbx").read_bytes().decode("utf-8", "replace")
    assert "Deformer" in fbx_text
    assert "Root" in fbx_text


def _key_group_scalar(times: list[float], values: list[float]) -> bytes:
    """SSE scalar KeyGroup with quadratic keys (time, value, fwd, bwd)."""
    out = bytearray(struct.pack("<I", len(times)) + struct.pack("<I", 2))
    for t, v in zip(times, values, strict=False):
        out += struct.pack("<4f", t, v, 0.0, 0.0)
    return bytes(out)


def _build_sse_animation_nif(seq_copies: int = 1) -> bytes:
    """Minimal Skyrim SE animated NIF: NiControllerSequence -> interpolator ->
    NiTransformData (XYZ rotation keys spinning around Z) -> node."""
    block_data = b"".join(
        [
            struct.pack("<I", 4),  # XYZ rotation type
            _key_group_scalar([0.0, 1.0], [0.0, 0.0]),  # X always 0
            _key_group_scalar([0.0, 1.0], [0.7853981633974483, 0.7853981633974483]),  # Y 45 deg
            _key_group_scalar([0.0, 1.0], [0.0, 1.5707963267948966]),  # Z 0 -> 90 deg
            struct.pack("<II", 1, 1),  # translation: 1 linear key
            struct.pack("<f", 0.0) + struct.pack("<3f", 1.0, 2.0, 3.0),
            struct.pack("<II", 1, 1),  # scale: 1 linear key = 1.0
            struct.pack("<ff", 0.0, 1.0),
            b"\xff\xff\x7f\xff",  # 4-byte sentinel tail
        ]
    )
    block_interp = b"".join(
        [
            struct.pack("<3f", 0.0, 0.0, 0.0),  # interpolator translation
            struct.pack("<4f", 1.0, 0.0, 0.0, 0.0),  # interpolator rotation quat
            struct.pack("<I", 0),  # NiTransformData ref
        ]
    )
    cb = b"".join(
        [
            struct.pack("<I", 1),  # interp ref -> NiTransformInterpolator
            struct.pack("<I", 0xFFFFFFFF),  # ctrl ref
            b"\x00",  # priority
            struct.pack("<I", 1),  # node name string idx ("Wheel")
            struct.pack("<I", 0xFFFFFFFF),  # prop type
            struct.pack("<I", 0xFFFFFFFF),  # ctrl type
            struct.pack("<I", 0xFFFFFFFF),  # ctrl id
            struct.pack("<I", 0xFFFFFFFF),  # interp id
        ]
    )
    footer = b"".join(
        [
            struct.pack("<f", 1.0),  # weight
            struct.pack("<I", 0xFFFFFFFF),  # text keys ref
            struct.pack("<I", 0),  # cycle type (loop)
            struct.pack("<f", 1.0),  # frequency
            struct.pack("<f", 0.0),  # start
            struct.pack("<f", 1.0),  # stop
            struct.pack("<I", 0xFFFFFFFF),  # manager ref
            struct.pack("<I", 0xFFFFFFFF),  # accum root name
            struct.pack("<H", 0),  # num anim note arrays
            b"\xff\xff\x7f\xff",
        ]
    )
    block_seq = b"".join(
        [
            struct.pack("<I", 1),  # num controlled blocks
            struct.pack("<I", 0),  # name string idx ("Spin")
            cb,
            footer,
        ]
    )
    assert len(block_data) == 168
    assert len(block_interp) == 32
    assert len(block_seq) == 75
    sizes = [len(block_data), len(block_interp), *(len(block_seq),) * seq_copies]
    types = [b"NiTransformData", b"NiTransformInterpolator", b"NiControllerSequence"]
    num_blocks = 2 + seq_copies
    return b"".join(
        [
            b"Gamebryo File Format, Version 20.2.0.7\x0a",
            struct.pack("<I", 0x14020007),  # version (SSE)
            b"\x01",  # endian
            struct.pack("<I", 12),  # user version
            struct.pack("<I", num_blocks),  # num blocks
            struct.pack("<I", 100),  # BS version
            _nif_export_string(b"a"),  # author
            _nif_export_string(b"p"),  # process script
            _nif_export_string(b"e"),  # export script
            struct.pack("<H", len(types)),
            b"".join(_nif_sized_string(t) for t in types),
            struct.pack(f"<{num_blocks}H", 0, 1, *(2,) * seq_copies),  # block type indices
            struct.pack(f"<{num_blocks}I", *sizes),
            struct.pack("<II", 2, 6),  # num strings, max len
            _nif_sized_string(b"Spin"),
            _nif_sized_string(b"Wheel"),
            struct.pack("<I", 0),  # num groups
            struct.pack("<I", 1),  # num roots
            struct.pack("<I", 0),
            block_data,
            block_interp,
            block_seq * seq_copies,
        ]
    )


def test_nif_animations(tmp_path):
    import json
    from pathlib import Path

    from dualforge.bethesda.nif import _euler_xyz_quat, parse_nif, read_animations
    from dualforge.export.scene import Bone, MeshPrimitive, SceneModel, save_scene

    clips = read_animations(parse_nif(_build_sse_animation_nif()))
    assert [c.name for c in clips] == ["Spin"]
    clip = clips[0]
    assert list(clip.tracks) == ["Wheel"]
    wheel = clip.tracks["Wheel"]
    assert list(wheel) == ["rotation", "translation", "scale"]
    assert wheel["rotation"][0][0] == 0.0
    assert wheel["rotation"][0][1] == pytest.approx(_euler_xyz_quat(0.0, 0.7853981633974483, 0.0))
    assert wheel["rotation"][1][1] == pytest.approx(
        _euler_xyz_quat(0.0, 0.7853981633974483, 1.5707963267948966)
    )
    assert wheel["translation"] == [(0.0, [1.0, 2.0, 3.0])]
    assert wheel["scale"] == [(0.0, [1.0, 1.0, 1.0])]

    model = SceneModel(
        "an",
        meshes=[
            MeshPrimitive(
                vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                triangles=[[0, 1, 2]],
                bones=[Bone("Wheel", -1, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])],
            )
        ],
    )
    model.clips = clips
    written = save_scene(tmp_path / "an", model, "gltf")
    gltf_paths = [p for p in written if p.endswith(".gltf")]
    assert any("Spin" in p for p in gltf_paths)
    anim_doc = next(
        json.loads(Path(p).read_text(encoding="utf-8")) for p in gltf_paths if "Spin" in p
    )
    assert anim_doc["animations"][0]["name"] == "Spin"
    assert {c["target"]["path"] for c in anim_doc["animations"][0]["channels"]} == {
        "rotation",
        "translation",
        "scale",
    }
    written = save_scene(tmp_path / "an", model, "fbx")
    assert any("Spin" in p and p.endswith(".fbx") for p in written)


def test_nif_animation_name_dedupe():
    from dualforge.bethesda.nif import parse_nif, read_animations

    clips = read_animations(parse_nif(_build_sse_animation_nif(seq_copies=2)))
    assert [c.name for c in clips] == ["Spin", "Spin_1"]
