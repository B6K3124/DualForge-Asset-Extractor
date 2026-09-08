"""Generate the hero Unity bundle used by the docs screenshot.

Writes a self-contained UnityFS ``.unity3d`` bundle containing a real
license-free cube ``Mesh`` plus an ``AssetBundle`` object that maps a
container path (``assets/meshes/hero_cube.mesh``) to it, so the app's
tree shows a realistic asset entry and its 3D preview renders geometry.

Usage:
    python scripts/make_hero_bundle.py tests/fixtures/unity3d/hero_mesh.unity3d
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

VERSION = "2022.3.41f1"
MESH_CLASS_ID = 43
ASSETBUNDLE_CLASS_ID = 142
CONTAINER_PATH = "assets/meshes/hero_cube.mesh"
MESH_NAME = "HeroCube"


def _make_serialized_file():
    from UnityPy.enums import BuildTarget, ClassIDType
    from UnityPy.files import ObjectReader, SerializedFile
    from UnityPy.files.ObjectReader import get_typetree_node
    from UnityPy.files.SerializedFile import SerializedFileHeader, SerializedType
    from UnityPy.helpers import TypeTreeHelper
    from UnityPy.helpers.UnityVersion import UnityVersion
    from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

    sf = SerializedFile.__new__(SerializedFile)
    header = SerializedFileHeader(EndianBinaryReader(b"\x00" * 16, endian="<"))
    header.version = 22
    header.endian = "<"
    header.reserved = b"\x00\x00\x00"
    sf.header = header
    sf.reader = EndianBinaryReader(b"", endian="<")
    sf.set_version(VERSION)
    sf._m_target_platform = BuildTarget.StandaloneWindows
    sf.target_platform = BuildTarget.StandaloneWindows
    sf._enable_type_tree = True
    sf.types = []
    sf.script_types = []
    sf.externals = []
    sf.objects = {}
    sf.ref_types = []
    sf.userInformation = ""
    sf.unknown = 0
    sf.big_id_enabled = 0

    uv = UnityVersion.from_str(VERSION)

    def make_type(class_id: int):
        st = SerializedType.__new__(SerializedType)
        st.class_id = class_id
        st.is_stripped_type = False
        st.script_type_index = -1
        st.script_id = b"\x00" * 16
        st.old_type_hash = b"\x00" * 16
        st.node = get_typetree_node(class_id, uv)

        def fix(node):
            for attr in ("m_TypeFlags", "m_RefTypeHash"):
                if getattr(node, attr, None) is None:
                    setattr(node, attr, 0)
            for child in node.m_Children:
                fix(child)

        fix(st.node)
        st.type_dependencies = []
        st.m_ClassName = None
        st.m_NameSpace = None
        st.m_AssemblyName = None
        sf.types.append(st)
        return st

    def make_object(st, path_id: int, type_id: int, value: dict) -> bytes:
        writer = EndianBinaryWriter(endian="<")
        TypeTreeHelper.write_typetree(value, st.node, writer, sf)
        payload = writer.bytes
        obj = ObjectReader(
            assets_file=sf,
            reader=EndianBinaryReader(payload, endian="<"),
            path_id=path_id,
            type_id=type_id,
            serialized_type=st,
            class_id=st.class_id,
            type=ClassIDType.Mesh if st.class_id == MESH_CLASS_ID else ClassIDType.AssetBundle,
            byte_start=0,
            byte_size=len(payload),
            data=None,
            is_destroyed=0,
            is_stripped=0,
        )
        sf.objects[path_id] = obj
        return payload

    def vec3(x, y, z):
        return {"x": x, "y": y, "z": z}

    def packed(m_NumItems=0, m_Range=0.0, m_Start=0.0, m_Data=None, m_BitSize=0, has_range=True):
        d = {"m_NumItems": m_NumItems, "m_Data": m_Data or [], "m_BitSize": m_BitSize}
        if has_range:
            d["m_Range"] = m_Range
            d["m_Start"] = m_Start
        return d

    vertices = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    vertex_count = len(vertices)
    indices = [
        0, 1, 2, 0, 2, 3,
        4, 5, 6, 4, 6, 7,
        1, 5, 6, 1, 6, 2,
        4, 0, 3, 4, 3, 7,
        3, 2, 6, 3, 6, 7,
        4, 5, 1, 4, 1, 0,
    ]
    index_count = len(indices)

    vertex_buffer = bytearray()
    normal = (0.577350, 0.577350, 0.577350)
    for x, y, z in vertices:
        vertex_buffer += struct.pack("<fff", x, y, z)
        vertex_buffer += struct.pack("<fff", *normal)
        vertex_buffer += struct.pack("<ff", 0.0, 0.0)
    index_buffer = struct.pack(f"<{index_count}I", *indices)

    mesh_tree = {
        "m_Name": MESH_NAME,
        "m_SubMeshes": [
            {
                "firstByte": 0,
                "indexCount": index_count,
                "topology": 0,
                "baseVertex": 0,
                "firstVertex": 0,
                "vertexCount": vertex_count,
                "localAABB": {"m_Center": vec3(0, 0, 0), "m_Extent": vec3(1, 1, 1)},
            }
        ],
        "m_Shapes": {"vertices": [], "shapes": [], "channels": [], "fullWeights": []},
        "m_BindPose": [],
        "m_BoneNameHashes": [],
        "m_RootBoneNameHash": 0,
        "m_BonesAABB": [],
        "m_VariableBoneCountWeights": {"m_Data": []},
        "m_MeshCompression": 0,
        "m_IsReadable": True,
        "m_KeepVertices": True,
        "m_KeepIndices": True,
        "m_IndexFormat": 1,
        "m_IndexBuffer": index_buffer,
        "m_VertexData": {
            "m_VertexCount": vertex_count,
            "m_Channels": [
                {"stream": 0, "offset": 0, "format": 0, "dimension": 3},
                {"stream": 0, "offset": 12, "format": 0, "dimension": 3},
                {"stream": 0, "offset": 24, "format": 0, "dimension": 2},
            ],
            "m_DataSize": bytes(vertex_buffer),
        },
        "m_CompressedMesh": {
            "m_Vertices": packed(),
            "m_UV": packed(),
            "m_Normals": packed(),
            "m_Tangents": packed(),
            "m_Weights": packed(has_range=False),
            "m_NormalSigns": packed(has_range=False),
            "m_TangentSigns": packed(has_range=False),
            "m_FloatColors": packed(),
            "m_BoneIndices": packed(has_range=False),
            "m_Triangles": packed(has_range=False),
            "m_UVInfo": 0,
        },
        "m_LocalAABB": {"m_Center": vec3(0, 0, 0), "m_Extent": vec3(1, 1, 1)},
        "m_MeshUsageFlags": 0,
        "m_CookingOptions": 0,
        "m_BakedConvexCollisionMesh": [],
        "m_BakedTriangleCollisionMesh": [],
        "m_MeshMetrics[0]": float(vertex_count),
        "m_MeshMetrics[1]": float(index_count),
        "m_StreamData": {"offset": 0, "size": 0, "path": ""},
    }

    (mesh_type, assetbundle_type) = (
        make_type(MESH_CLASS_ID),
        make_type(ASSETBUNDLE_CLASS_ID),
    )
    make_object(mesh_type, 1, 0, mesh_tree)

    pptr = {"m_FileID": 0, "m_PathID": 1}
    asset_info = {"preloadIndex": 0, "preloadSize": 0, "asset": pptr}
    assetbundle_tree = {
        "m_Name": "hero_mesh",
        "m_PreloadTable": [pptr],
        "m_Container": [(CONTAINER_PATH, asset_info)],
        "m_MainAsset": asset_info,
        "m_RuntimeCompatibility": 0,
        "m_AssetBundleName": "hero_mesh",
        "m_Dependencies": [],
        "m_IsStreamedSceneAssetBundle": False,
        "m_ExplicitDataLayout": 0,
        "m_PathFlags": 0,
        "m_SceneHashes": [],
    }
    make_object(assetbundle_type, 2, 1, assetbundle_tree)
    return sf


def make_bundle_bytes() -> bytes:
    from UnityPy.enums import ArchiveFlags
    from UnityPy.files import BundleFile

    sf = _make_serialized_file()
    sf.flags = 4
    bundle = BundleFile.__new__(BundleFile)
    bundle.signature = "UnityFS"
    bundle.version = 7
    bundle.version_player = "6.0.0"
    bundle.version_engine = VERSION
    bundle.dataflags = ArchiveFlags.BlocksAndDirectoryInfoCombined
    bundle._block_info_flags = 64
    bundle._uses_block_alignment = True
    bundle.files = {"archive:/CAB-hero/hero_cube.assets": sf}
    return bundle.save()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = Path(argv[0]) if argv else Path("tests/fixtures/unity3d/hero_mesh.unity3d")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = make_bundle_bytes()
    out.write_bytes(data)
    print(f"wrote {len(data)} bytes to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())