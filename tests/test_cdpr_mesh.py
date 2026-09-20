"""Tests for the REDengine 4 ``.mesh`` (CMesh / rendRenderMeshBlob) decoder."""

from __future__ import annotations

import struct

import pytest

from dualforge.cdpr.mesh import MeshError, decode_mesh, mesh_to_scene
from util_cr2w import (
    build_cr2w,
    pack_array,
    pack_class_stream,
    pack_data_buffer,
)

NAMES = [
    "None",
    "Uint32",
    "Uint16",
    "Uint8",
    "Float",
    "Bool",
    "CName",
    "DataBuffer",
    "array:Uint32",
    "array:Uint8",
    "array:CName",
    "array:Vector4",
    "array:rendChunk",
    "array:CMatrix",
    "array:rendTopologyData",
    "array:Chandle",
    "array:CHandle:meshMeshAppearance",
    "array:CHandle:meshMeshParameter",
    "array:GpuWrapApiVertexPackingPackingElement",
    "CHandle:IRenderResourceBlob",
    "CHandle:meshMeshParameter",
    "CHandle:meshMeshAppearance",
    "raRef:IMaterial",
    "Enum.GpuWrapApiVertexPackingePackingType",
    "Enum.GpuWrapApiVertexPackingePackingUsage",
    "Enum.GpuWrapApiVertexPackingEStreamType",
    "Enum.GpuWrapApieIndexBufferChunkType",
    "Enum.ERenderObjectType",
    "CMesh",
    "Box",
    "Vector3",
    "Vector4",
    "CMatrix",
    "meshMeshAppearance",
    "meshMeshMaterialBuffer",
    "rendRenderMeshBlob",
    "rendRenderMeshBlobHeader",
    "rendChunk",
    "rendVertexBufferChunk",
    "GpuWrapApiVertexLayoutDesc",
    "GpuWrapApiVertexPackingPackingElement",
    "rendIndexBufferChunk",
    "rendTopologyData",
    # enum members
    "PT_Float3",
    "PS_Position",
    "PS_SkinIndices",
    "PS_Normal",
    "PS_Tangent",
    "PS_TexCoord",
    "ST_PerVertex",
    "IBCT_IndexUShort",
    "ERenderObjectType_Default",
    # CName values
    "root_bone",
    "tip_bone",
    "ml_hero_body",
    "green_hero",
    # field names (CMesh)
    "parameters",
    "boundingBox",
    "appearances",
    "renderResourceBlob",
    "boneNames",
    "boneRigMatrices",
    "materialEntries",
    # field names (box / vectors / cmatrix)
    "Min",
    "Max",
    "X",
    "Y",
    "Z",
    "W",
    "mx",
    "my",
    "mz",
    "mw",
    # field names (blob / header)
    "header",
    "renderBuffer",
    "version",
    "dataProcessing",
    "bonePositions",
    "quantizationScale",
    "quantizationOffset",
    "vertexBufferSize",
    "indexBufferSize",
    "indexBufferOffset",
    "renderChunkInfos",
    # field names (rendChunk + layout)
    "chunkVertices",
    "chunkIndices",
    "numVertices",
    "numIndices",
    "materialId",
    "vertexFactory",
    "lodMask",
    "vertexLayout",
    "byteOffsets",
    "elements",
    "slotStrides",
    "slotMask",
    "hash",
    "type",
    "usage",
    "usageIndex",
    "streamIndex",
    "streamType",
    "teOffset",
    "pe",
    # meshMeshAppearance
    "name",
    "chunkMaterials",
    "tags",
]
NORD = {name: i for i, name in enumerate(NAMES)}

POSITIONS = [
    (0.0, 0.0, 0.0),
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
]
UVS = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.25, 0.75)]
SKIN_INDICES = [
    [0, 1, 0, 0],
    [1, 0, 0, 0],
    [0, 0, 0, 0],
    [1, 1, 1, 0],
]
SKIN_WEIGHTS = [
    [255, 0, 0, 0],
    [200, 55, 0, 0],
    [0, 0, 0, 0],
    [100, 100, 55, 0],
]


def _signed10(v: float) -> int:
    """Bias a -1..1 component into its 10-bit reading (0 -> 512)."""
    return max(0, min(1023, int(round((v + 1.0) * 0.5 * 1023))))


def _normal10(x: float, y: float, z: float, w_bits: int = 0) -> int:
    return _signed10(x) | (_signed10(y) << 10) | (_signed10(z) << 20) | (w_bits << 30)


NORMAL = _normal10(1.0, 0.0, 0.0)  # +X, w=1.0
TANGENT = _normal10(0.0, 1.0, 0.0)  # +Y, w=1.0
TRIANGLES = [0, 1, 2, 0, 2, 3]


def _stream(*fields) -> bytes:
    return pack_class_stream(fields, NORD, NORD)


def _enum(member: str) -> bytes:
    return struct.pack("<I", NORD[member])


def _name(member: str) -> bytes:
    return struct.pack("<H", NORD[member])


def _vec4(x: float, y: float, z: float, w: float) -> bytes:
    return _stream(
        ("X", "Float", struct.pack("<f", x)),
        ("Y", "Float", struct.pack("<f", y)),
        ("Z", "Float", struct.pack("<f", z)),
        ("W", "Float", struct.pack("<f", w)),
    )


def _packing_element(usage: str, stream_index: int, ptype: str = "PT_Float3") -> bytes:
    return _stream(
        ("type", "Enum.GpuWrapApiVertexPackingePackingType", _enum(ptype)),
        ("usage", "Enum.GpuWrapApiVertexPackingePackingUsage", _enum(usage)),
        ("usageIndex", "Uint8", b"\x00"),
        ("streamIndex", "Uint8", bytes([stream_index])),
        ("streamType", "Enum.GpuWrapApiVertexPackingEStreamType", _enum("ST_PerVertex")),
    )


def _layout() -> bytes:
    return _stream(
        (
            "elements",
            "array:GpuWrapApiVertexPackingPackingElement",
            pack_array(
                _packing_element("PS_Position", 0),
                _packing_element("PS_SkinIndices", 0),
                _packing_element("PS_Tangent", 1),
                _packing_element("PS_Normal", 1),
                _packing_element("PS_TexCoord", 2),
            ),
        ),
        ("slotStrides", "array:Uint8", pack_array(b"\x10", b"\x08", b"\x04")),
        ("slotMask", "Uint32", struct.pack("<I", 0)),
        ("hash", "Uint32", struct.pack("<I", 0)),
    )


def _chunk_vertices() -> bytes:
    return _stream(
        ("vertexLayout", "GpuWrapApiVertexLayoutDesc", _layout()),
        (
            "byteOffsets",
            "array:Uint32",
            pack_array(
                struct.pack("<I", 0),
                struct.pack("<I", 64),
                struct.pack("<I", 96),
                struct.pack("<I", 0),
                struct.pack("<I", 0),
            ),
        ),
    )


def _chunk_indices() -> bytes:
    return _stream(
        ("pe", "Enum.GpuWrapApieIndexBufferChunkType", _enum("IBCT_IndexUShort")),
        ("teOffset", "Uint32", struct.pack("<I", 0)),
    )


def _rend_chunk() -> bytes:
    return _stream(
        ("chunkVertices", "rendVertexBufferChunk", _chunk_vertices()),
        ("chunkIndices", "rendIndexBufferChunk", _chunk_indices()),
        ("numVertices", "Uint16", struct.pack("<H", 4)),
        ("numIndices", "Uint32", struct.pack("<I", 6)),
        ("vertexFactory", "Uint8", b"\x00"),
        ("lodMask", "Uint8", b"\x01"),
    )


def _header() -> bytes:
    header_fields = [
        ("version", "Uint32", struct.pack("<I", 48)),
        ("dataProcessing", "Uint32", struct.pack("<I", 0)),
        (
            "bonePositions",
            "array:Vector4",
            pack_array(
                _vec4(0.0, 0.0, 0.0, 1.0),
                _vec4(0.0, 1.5, 0.0, 1.0),
            ),
        ),
        (
            "renderChunkInfos",
            "array:rendChunk",
            pack_array(_rend_chunk()),
        ),
        ("quantizationScale", "Vector4", _vec4(1.0, 1.0, 1.0, 1.0)),
        ("quantizationOffset", "Vector4", _vec4(0.0, 0.0, 0.0, 0.0)),
        ("vertexBufferSize", "Uint32", struct.pack("<I", 112)),
        ("indexBufferSize", "Uint32", struct.pack("<I", 12)),
        ("indexBufferOffset", "Uint32", struct.pack("<I", 112)),
    ]
    return _stream(*header_fields)


def _blob() -> bytes:
    return _stream(
        ("header", "rendRenderMeshBlobHeader", _header()),
        ("renderBuffer", "DataBuffer", pack_data_buffer(0)),
    )


def _cmatrix(m: list[float]) -> bytes:
    return _stream(
        ("mx", "Vector4", _vec4(m[0], m[1], m[2], m[3])),
        ("my", "Vector4", _vec4(m[4], m[5], m[6], m[7])),
        ("mz", "Vector4", _vec4(m[8], m[9], m[10], m[11])),
        ("mw", "Vector4", _vec4(m[12], m[13], m[14], m[15])),
    )


def _appearance() -> bytes:
    return _stream(
        ("name", "CName", _name("green_hero")),
        ("chunkMaterials", "array:CName", pack_array(_name("ml_hero_body"))),
        ("tags", "array:CName", pack_array()),
    )


def _root() -> bytes:
    identity = [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ]
    tip = [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        1.5,
        0.0,
        1.0,
    ]
    return _stream(
        ("parameters", "array:CHandle:meshMeshParameter", pack_array()),
        (
            "boundingBox",
            "Box",
            _stream(("Min", "Vector4", _vec4(-1, -1, -1, 1)), ("Max", "Vector4", _vec4(1, 1, 1, 1))),
        ),
        (
            "appearances",
            "array:CHandle:meshMeshAppearance",
            pack_array(struct.pack("<I", 3)),
        ),
        ("renderResourceBlob", "CHandle:IRenderResourceBlob", struct.pack("<I", 2)),
        (
            "boneNames",
            "array:CName",
            pack_array(_name("root_bone"), _name("tip_bone")),
        ),
        ("boneRigMatrices", "array:CMatrix", pack_array(_cmatrix(identity), _cmatrix(tip))),
    )


def _vertex_stream() -> bytes:
    out = bytearray()
    for i in range(4):
        xi, yi, zi = (int(round(v * 32767)) for v in POSITIONS[i])
        out += struct.pack("<hhh", xi, yi, zi)
        out += b"\x00\x00"
        out += bytes(SKIN_INDICES[i])
        out += bytes(SKIN_WEIGHTS[i])
    for _i in range(4):
        out += struct.pack("<I", NORMAL)
        out += struct.pack("<I", TANGENT)
    for u, v in UVS:
        out += struct.pack("<ee", u, v)
    for index in TRIANGLES:
        out += struct.pack("<H", index)
    return bytes(out)


def _mesh_bytes() -> bytes:
    return build_cr2w(
        NAMES,
        [
            ("CMesh", _root()),
            ("rendRenderMeshBlob", _blob()),
            ("meshMeshAppearance", _appearance()),
        ],
        buffers=[_vertex_stream()],
    )


def test_decode_mesh_geometry() -> None:
    info = decode_mesh(_mesh_bytes(), name="hero")
    assert info.name == "hero"
    assert info.meta["Submeshes"] == "1"
    assert info.meta["Bones"] == "2"

    assert info.bone_names == ["root_bone", "tip_bone"]
    assert len(info.bind_matrices) == 2
    assert info.bind_matrices[0] == pytest.approx(
        [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]
    )
    assert info.bind_matrices[1] == pytest.approx(
        [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            -1.5,
            0.0,
            1.0,
        ]
    )

    submesh = info.submeshes[0]
    assert submesh.lod == 1
    assert submesh.material_name == "ml_hero_body"
    assert submesh.positions == [list(p) for p in POSITIONS]
    assert submesh.triangles == [[0, 1, 2], [0, 2, 3]]
    for nrow in submesh.normals:
        assert nrow[1:3] == pytest.approx([0.0, 0.0], abs=0.01)
    assert (submesh.normals[0][0] - 1.0) < 0.01
    for trow in submesh.tangents:
        assert trow[:3] == pytest.approx([0.0, 1.0, 0.0], abs=0.01)
        assert trow[3] == 1.0
    assert submesh.uvs0 == [list(u) for u in UVS]
    assert submesh.joints == SKIN_INDICES
    expected_weights = [
        [1.0, 0.0, 0.0, 0.0],
        [200 / 255.0, 55 / 255.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [100 / 255.0, 100 / 255.0, 55 / 255.0, 0.0],
    ]
    assert submesh.weights == expected_weights


def test_mesh_to_scene() -> None:
    info = decode_mesh(_mesh_bytes(), name="hero")
    scene = mesh_to_scene(info)
    assert scene.up_axis == "Z"
    assert len(scene.meshes) == 1
    mesh = scene.meshes[0]
    assert mesh.texture_name == "ml_hero_body"
    assert len(mesh.bones) == 2
    assert mesh.bones[0].name == "root_bone"
    assert mesh.bones[0].parent == -1
    assert mesh.joints == SKIN_INDICES
    assert len(mesh.vertices) == 4


def test_decode_mesh_wrong_root() -> None:
    data = build_cr2w(NAMES, [("rendRenderMeshBlob", _blob())], buffers=[b""])
    with pytest.raises(MeshError):
        decode_mesh(data)


def test_decode_mesh_bad_blob_handle() -> None:
    root = _stream(("renderResourceBlob", "CHandle:IRenderResourceBlob", struct.pack("<I", 7)))
    data = build_cr2w(NAMES, [("CMesh", root)], buffers=[b""])
    with pytest.raises(MeshError):
        decode_mesh(data)
