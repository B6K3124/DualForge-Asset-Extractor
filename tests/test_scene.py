from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from dualforge.export.scene import (
    AnimationClip,
    Bone,
    MeshPrimitive,
    SceneError,
    SceneModel,
    save_scene,
    _rotate_quat,
)


def test_rotate_quat_conjugates_axes():
    # 90 deg about engine Z maps to 90 deg about glTF Y under the world-up
    # frame rotation; the previous buggy conjugation collapsed every quat.
    q = _rotate_quat((0.7071067811865475, 0.0, 0.0, 0.7071067811865475))
    assert q == pytest.approx((0.7071067811865475, 0.0, 0.7071067811865475, 0.0))
    identity = _rotate_quat((1.0, 0.0, 0.0, 0.0))
    assert identity == pytest.approx((1.0, 0.0, 0.0, 0.0))

_IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
_MAGIC = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"
_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOT_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"
_SCALAR_SIZE = {"Y": 2, "I": 4, "F": 4, "D": 8, "L": 8, "B": 1, "C": 1}
_ITEM_SIZE = {"f": 4, "d": 8, "i": 4, "l": 8, "q": 8, "b": 1, "c": 1}
_SCALAR_BYTE = {ord(k): v for k, v in _SCALAR_SIZE.items()}
_ITEM_SIZE_BYTE = {ord(k): v for k, v in _ITEM_SIZE.items()}


def _binary_names(data: bytes) -> list[str]:
    """Walk a binary FBX element tree and return every element name."""
    assert data[:23] == _MAGIC
    assert struct.unpack_from("<I", data, 23)[0] == 7400
    assert data[-16:] == _FOOT_MAGIC
    names: list[str] = []
    end = data.rfind(_FOOT_ID)

    def walk(start: int, stop: int) -> None:
        pos = start
        while pos < stop:
            elem_end = struct.unpack_from("<I", data, pos)[0]
            if elem_end == 0:
                return
            nprops = struct.unpack_from("<I", data, pos + 4)[0]
            name_len = data[pos + 12]
            name = data[pos + 13 : pos + 13 + name_len].decode("utf-8", errors="replace")
            names.append(name)
            q = pos + 13 + name_len
            for _ in range(nprops):
                typ = data[q]
                q += 1
                if typ in _SCALAR_BYTE:
                    q += _SCALAR_BYTE[typ]
                elif typ in (ord("S"), ord("R")):
                    n = struct.unpack_from("<I", data, q)[0]
                    q += 4 + n
                else:
                    count, encoding, clen = struct.unpack_from("<III", data, q)
                    q += 12
                    q += clen if encoding == 1 else count * _ITEM_SIZE_BYTE[typ]
            if q < elem_end - 13:
                walk(q, elem_end - 13)
            pos = elem_end

    walk(27, end)
    return names


def _skinned_model(name: str = "Character") -> SceneModel:
    bones = [
        Bone("Hips", -1, _IDENTITY),
        Bone("Spine", 0, _IDENTITY),
        Bone("Chest", 1, _IDENTITY),
        Bone("Head", 2, _IDENTITY),
    ]
    mesh = MeshPrimitive(
        vertices=[[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
        triangles=[[0, 1, 2], [0, 2, 3]],
        normals=[[0, 0, 1]] * 4,
        uvs=[[0, 0], [1, 0], [1, 1], [0, 1]],
        joints=[[0, 1, 2, 3]] * 4,
        weights=[[0.5, 0.3, 0.1, 0.1]] * 4,
        bones=bones,
    )
    return SceneModel(name, meshes=[mesh])


def test_save_scene_gltf_skinned(tmp_path: Path):
    paths = save_scene(tmp_path / "char", _skinned_model(), "gltf")
    assert len(paths) == 1
    doc = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    primitive = doc["meshes"][0]["primitives"][0]
    assert primitive["attributes"]["JOINTS_0"] is not None
    assert primitive["attributes"]["WEIGHTS_0"] is not None
    skin = doc["skins"][0]
    assert len(skin["joints"]) == 4
    names = [node["name"] for node in doc["nodes"]]
    assert names == ["Character", "Hips", "Spine", "Chest", "Head"]


def test_save_scene_fbx_skinned(tmp_path: Path):
    paths = save_scene(tmp_path / "char", _skinned_model(), "fbx")
    assert len(paths) == 1
    names = _binary_names(Path(paths[0]).read_bytes())
    assert "Deformer" in names
    assert "TransformLink" in names
    assert "PoseNode" in names


def test_save_scene_static_gltf(tmp_path: Path):
    mesh = MeshPrimitive(
        vertices=[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        triangles=[[0, 1, 2]],
    )
    paths = save_scene(tmp_path / "tri", SceneModel("tri", meshes=[mesh]), "gltf")
    doc = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    assert doc["meshes"][0]["name"] == "tri"
    assert "JOINTS_0" not in doc["meshes"][0]["primitives"][0]["attributes"]


def test_save_scene_animation(tmp_path: Path):
    model = _skinned_model()
    model.clips = [
        AnimationClip(
            "Idle",
            {
                "Hips": {"translation": [(0.0, [0, 0, 0]), (1.0, [0.5, 0, 0])]},
                "Head": {"rotation": [(0.0, [0, 0, 0, 1]), (1.0, [0, 0, 0.7071, 0.7071])]},
            },
        )
    ]
    paths = save_scene(tmp_path / "char", model, "gltf")
    assert len(paths) == 2
    anim_path = next(p for p in paths if ".Idle.gltf" in p)
    doc = json.loads(Path(anim_path).read_text(encoding="utf-8"))
    assert doc["animations"][0]["name"] == "Idle"
    assert len(doc["animations"][0]["channels"]) == 2


def test_save_scene_z_up_transform(tmp_path: Path):
    # row-major inverse-bind: translation t=(2,0,0) sits in the last column
    binds = [[1, 0, 0, 2, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]]
    mesh = MeshPrimitive(
        vertices=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        triangles=[[0, 1, 2]],
        joints=[[0, 0, 0, 0]] * 3,
        weights=[[1.0, 0.0, 0.0, 0.0]] * 3,
        bones=[Bone("Root", -1, binds[0])],
    )
    model = SceneModel("z", meshes=[mesh], up_axis="Z")
    paths = save_scene(tmp_path / "z", model, "gltf")
    doc = json.loads(Path(paths[0]).read_text(encoding="utf-8"))
    primitive = doc["meshes"][0]["primitives"][0]
    buffers = doc["buffers"][0]
    pos_acc = doc["accessors"][primitive["attributes"]["POSITION"]]
    view = doc["bufferViews"][pos_acc["bufferView"]]
    offset = view["byteOffset"]
    raw = _decode_b64(buffers["uri"])
    # positions rotated Z-up -> Y-up: (0,1,0) engine -> (0,0,-1) glTF
    positions = [list(struct.unpack_from("<3f", raw, offset + 12 * i)) for i in range(pos_acc["count"])]
    assert positions[-1] == pytest.approx([0.0, 0.0, -1.0])
    bind_acc = doc["accessors"][doc["skins"][0]["inverseBindMatrices"]]
    view = doc["bufferViews"][bind_acc["bufferView"]]
    offset = view["byteOffset"]
    mat = list(struct.unpack_from("<16f", raw, offset))
    # translation along engine +X is untouched by the up-axis rotation and
    # lands in the column-major buffer as the last column
    assert mat == pytest.approx([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 0, 0, 1])


def _decode_b64(uri: str) -> bytes:
    import base64

    return base64.b64decode(uri.split(",", 1)[1])


def test_save_scene_empty_raises(tmp_path: Path):
    with pytest.raises(SceneError):
        save_scene(tmp_path / "none", SceneModel("none"), "gltf")


def test_save_scene_bad_format(tmp_path: Path):
    with pytest.raises(SceneError, match="unsupported"):
        save_scene(tmp_path / "x", _skinned_model(), "obj")