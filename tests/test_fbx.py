from __future__ import annotations

import struct
from pathlib import Path

from dualforge.export.fbx import write_fbx_animation, write_fbx_mesh

_MAGIC = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"
_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOT_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"


def _count_balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _read_u32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<I", data, pos)[0]


_SCALAR_SIZE = {"Y": 2, "I": 4, "F": 4, "D": 8, "L": 8, "B": 1, "C": 1}
_ITEM_SIZE = {"f": 4, "d": 8, "i": 4, "l": 8, "q": 8, "b": 1, "c": 1}
_SCALAR_BYTE = {ord(k): v for k, v in _SCALAR_SIZE.items()}
_ITEM_SIZE_BYTE = {ord(k): v for k, v in _ITEM_SIZE.items()}


def _binary_names(path: Path) -> list[str]:
    """Walk a binary FBX element tree and return every element name."""
    data = path.read_bytes()
    assert data[:23] == _MAGIC
    assert struct.unpack_from("<I", data, 23)[0] == 7400
    assert data[-16:] == _FOOT_MAGIC
    names: list[str] = []
    end = data.rfind(_FOOT_ID)

    def walk(start: int, stop: int) -> None:
        pos = start
        while pos < stop:
            elem_end = _read_u32(data, pos)
            if elem_end == 0:
                return
            nprops = _read_u32(data, pos + 4)
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
                    n = _read_u32(data, q)
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


def test_write_fbx_ascii_mesh_plain(tmp_path: Path):
    path = tmp_path / "quad.fbx"
    write_fbx_mesh(
        str(path),
        "quad",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        normals=[(0, 0, 1)] * 4,
        uvs=[(0, 0), (1, 0), (1, 1), (0, 1)],
        binary=False,
    )
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "FBXHeaderExtension" in text
    assert "Vertices: *12" in text
    assert "PolygonVertexIndex: *6" in text
    assert "GeometryVersion: 124" in text
    assert _count_balanced(text)


def test_write_fbx_ascii_mesh_skinned(tmp_path: Path):
    path = tmp_path / "skinned.fbx"
    binds = [
        [1.0 if r == c else 0.0 for r in range(4) for c in range(4)],
        [1.0 if r == c else 0.0 for r in range(4) for c in range(4)],
    ]
    binds[1][7] = 2.0
    write_fbx_mesh(
        str(path),
        "skinned",
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)] * 2,
        [(0, 1, 2), (0, 2, 3)] * 2,
        bone_names=["Root", "Child"],
        bone_parents=[-1, 0],
        bind_matrices=binds,
        joints=[[0, 1, 0, 0] * 2 for _ in range(8)],
        weights=[[0.6, 0.4, 0.0, 0.0]] * 8,
        blendshapes=None,
        binary=False,
    )
    text = path.read_text(encoding="utf-8", errors="replace")
    assert text.count('"Cluster"') == 2
    assert 'Deformer::skinned_Skin' in text
    assert "BindPose" in text
    assert "PoseNode" in text
    assert "Weights: *8" in text
    assert "Indexes: *8" in text
    assert _count_balanced(text)


def test_write_fbx_ascii_animation(tmp_path: Path):
    path = tmp_path / "clip.fbx"
    write_fbx_animation(
        str(path),
        name="run",
        tracks={
            "Root": {
                "translation": [(0.0, [0, 0, 0]), (1.0, [1, 0, 0])],
                "rotation": [(0.0, [0, 0, 0, 1]), (1.0, [0, 0, 0.7071, 0.7071])],
            },
            "Arm": {
                "scale": [(0.0, [1, 1, 1])],
            },
        },
        binary=False,
    )
    text = path.read_text(encoding="utf-8", errors="replace")
    assert "AnimationStack" in text
    assert "AnimationLayer" in text
    assert 'CurveNode' in text
    assert "Curve" in text
    assert "KeyTime" in text
    assert _count_balanced(text)


def test_write_fbx_binary_mesh(tmp_path: Path):
    path = tmp_path / "quad.fbx"
    write_fbx_mesh(
        str(path),
        "quad",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        normals=[(0, 0, 1)] * 4,
        uvs=[(0, 0), (1, 0), (1, 1), (0, 1)],
    )
    names = _binary_names(path)
    for expected in ("FBXHeaderExtension", "Definitions", "Objects", "Connections", "Geometry", "LayerElementMaterial"):
        assert expected in names, f"{expected} missing"


def test_write_fbx_binary_skinned(tmp_path: Path):
    path = tmp_path / "skinned.fbx"
    binds = [
        [1.0 if r == c else 0.0 for r in range(4) for c in range(4)],
        [1.0 if r == c else 0.0 for r in range(4) for c in range(4)],
    ]
    binds[1][7] = 2.0
    write_fbx_mesh(
        str(path),
        "skinned",
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)] * 2,
        [(0, 1, 2), (0, 2, 3)] * 2,
        bone_names=["Root", "Child"],
        bone_parents=[-1, 0],
        bind_matrices=binds,
        joints=[[0, 1, 0, 0] * 2 for _ in range(8)],
        weights=[[0.6, 0.4, 0.0, 0.0]] * 8,
        blendshapes=None,
    )
    names = _binary_names(path)
    assert names.count("Deformer") == 3  # skin + 2 clusters
    for expected in ("Pose", "PoseNode", "Link_DeformAcuracy", "TransformLink", "Weights", "Indexes"):
        assert expected in names, f"{expected} missing"


def test_write_fbx_binary_animation(tmp_path: Path):
    path = tmp_path / "clip.fbx"
    write_fbx_animation(
        str(path),
        name="run",
        tracks={
            "Root": {
                "translation": [(0.0, [0, 0, 0]), (1.0, [1, 0, 0])],
                "rotation": [(0.0, [0, 0, 0, 1]), (1.0, [0, 0, 0.7071, 0.7071])],
            },
            "Arm": {
                "scale": [(0.0, [1, 1, 1])],
            },
        },
    )
    names = _binary_names(path)
    for expected in ("AnimationStack", "AnimationLayer", "AnimationCurve", "AnimationCurveNode", "KeyTime"):
        assert expected in names, f"{expected} missing"