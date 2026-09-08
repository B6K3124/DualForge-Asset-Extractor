from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from dualforge.export.gltf_reader import GltfReaderError, parse_glb, read_glb


def _glb(doc: dict, binary: bytes) -> bytes:
    json_chunk = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    # GLB chunks must be padded to 4 bytes.
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    bin_chunk = binary + b"\x00" * ((4 - len(binary) % 4) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(json_chunk), 0x4E4F534A)
    out += json_chunk
    out += struct.pack("<II", len(bin_chunk), 0x004E4942)
    out += bin_chunk
    return bytes(out)


def _simple_glb():
    verts = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    ]
    indices = [0, 1, 2, 0, 2, 3]
    pos_bytes = struct.pack("<12f", *[c for v in verts for c in v])
    idx_bytes = struct.pack("<6I", *indices)
    binary = pos_bytes + idx_bytes
    doc = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0},
                        "indices": 1,
                        "mode": 4,
                    }
                ]
            }
        ],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(idx_bytes), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3",
             "min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
            {"bufferView": 1, "componentType": 5125, "count": 6, "type": "SCALAR"},
        ],
    }
    return _glb(doc, binary)


def test_parse_glb_returns_geometry_tuple():
    glb = _simple_glb()
    result = parse_glb(glb)
    assert result is not None
    verts, normals, tris, edges = result
    assert verts.shape == (4, 3)
    assert verts.dtype == np.float32
    assert tris.dtype == np.uint32
    assert tris.tolist() == [[0, 1, 2], [0, 2, 3]]
    assert edges.shape[1] == 2
    # Smooth normals should be normalized unit vectors (length ~1).
    lengths = np.linalg.norm(normals, axis=1)
    assert np.allclose(lengths, 1.0, atol=1e-3)


def test_parse_glb_no_mesh_returns_none():
    doc = {"asset": {"version": "2.0"}, "meshes": []}
    assert parse_glb(_glb(doc, b"")) is None


def test_parse_glb_invalid_magic_raises():
    with pytest.raises(GltfReaderError):
        parse_glb(b"notaglb")


def test_read_glb_from_disk(tmp_path):
    target = tmp_path / "model.glb"
    target.write_bytes(_simple_glb())
    result = read_glb(str(target))
    assert result is not None and result[0].shape == (4, 3)


def test_parse_glb_accepts_plain_json_with_data_uri():
    import base64

    verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    idx = [0, 1, 2]
    binary = struct.pack("<9f", *[c for v in verts for c in v]) + struct.pack("<3I", *idx)
    uri = "data:application/octet-stream;base64," + base64.b64encode(binary).decode("ascii")
    doc = {
        "asset": {"version": "2.0"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary), "uri": uri}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36, "target": 34962},
            {"buffer": 0, "byteOffset": 36, "byteLength": 12, "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5125, "count": 3, "type": "SCALAR"},
        ],
    }
    result = parse_glb(json.dumps(doc).encode("utf-8"))
    assert result is not None and len(result[0]) == 3 and len(result[2]) == 1


def test_unreal_preview_payload_contains_mesh(monkeypatch, tmp_path):
    from dualforge.ui.preview import PreviewItem, PreviewWorker

    class FakeBridge:
        def available(self):
            return True

        def preview_mesh(self, *args, **kwargs):
            return _simple_glb(), "staticmesh"

        def extract(self, pak, out_dir, **kwargs):
            Path(out_dir, "a.bin").write_bytes(b"x")

    monkeypatch.setattr("dualforge.unreal.UnrealBridge", FakeBridge)

    item = PreviewItem(
        title="Game/a.uasset",
        engine="unreal",
        kind="file",
        size=1024,
        entry="/Game/a.uasset",
        archive_path=str(tmp_path / "p.pak"),
        aes_key="0123",
        meta={},
    )
    worker = PreviewWorker(item, str(tmp_path))
    payload = worker._preview_unreal()
    assert "mesh" in payload
    verts, normals, tris, edges = payload["mesh"]
    assert len(verts) == 4
    assert len(tris) == 2
    assert len(edges) == 5


def test_unreal_preview_falls_back_without_mesh(monkeypatch, tmp_path):
    from dualforge.ui.preview import PreviewItem, PreviewWorker

    class FakeBridge:
        def available(self):
            return True

        def preview_mesh(self, *args, **kwargs):
            return None

        def extract(self, pak, out_dir, **kwargs):
            Path(out_dir, "dummy.bin").write_bytes(b"not a mesh")
            return 1

    monkeypatch.setattr("dualforge.unreal.UnrealBridge", FakeBridge)

    item = PreviewItem(
        title="Game/a.uasset",
        engine="unreal",
        kind="file",
        size=1024,
        entry="/Game/a.wem",
        archive_path=str(tmp_path / "p.pak"),
        aes_key="0123",
        meta={},
    )
    worker = PreviewWorker(item, str(tmp_path))
    payload = worker._preview_unreal()
    assert "mesh" not in payload
