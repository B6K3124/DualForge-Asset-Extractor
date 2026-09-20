"""REDengine 4 ``.mesh`` decoder (CMesh + rendRenderMeshBlob).

A cooked ``.mesh`` is a CR2W container whose root chunk is a ``CMesh``.  Its
``renderResourceBlob`` handle points at a ``rendRenderMeshBlob`` whose
``header`` describes the vertex streams and whose ``renderBuffer`` buffer
holds the packed geometry.  This module walks the schemas, ports the WolvenKit
``GetMeshesinfo`` / ``ContainRawMesh`` decode math (quantized int16 positions,
10-10-10-2 normals/tangents, half-float UVs, byte skin weights and u16
triangles) and exposes the result as engine-agnostic
:class:`dualforge.export.scene` models.

Coordinates stay REDengine-native (Z-up, X-right, Y-back); exporters use
``up_axis="Z"`` so :func:`dualforge.export.scene.save_scene` applies the
standard ``(x, y, z) -> (x, z, -y)`` conversion, matching WolvenKit output.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from dualforge.cdpr.cr2w import (
    Cr2wChunk,
    Cr2wFile,
    parse_cr2w,
)
from dualforge.cdpr.red4 import (
    Red4Decoder,
    Red4Error,
    container_bytes,
    hfconvert,
    mat4_identity,
    mat4_inverse_row_major,
    mat4_translate,
    ten_bit_shifted,
)

MeshError = Red4Error

_PS_NORMAL = "PS_Normal"
_PS_TANGENT = "PS_Tangent"
_PS_COLOR = "PS_Color"
_PS_TEXCOORD = "PS_TexCoord"
_PS_SKIN_INDICES = "PS_SkinIndices"
_PS_EXTRA_DATA = "PS_ExtraData"

_VECTOR4 = (("X", "Float"), ("Y", "Float"), ("Z", "Float"), ("W", "Float"))
_VECTOR3 = (("x", "Float"), ("y", "Float"), ("z", "Float"))


SCHEMAS: dict[str, tuple[tuple[str, str], ...]] = {
    "CMesh": (
        ("parameters", "array:CHandle:meshMeshParameter"),
        ("boundingBox", "Box"),
        ("surfaceAreaPerAxis", "Vector3"),
        ("materialEntries", "array:CMeshMaterialEntry"),
        ("externalMaterials", "array:raRef:IMaterial"),
        ("localMaterialInstances", "array:CHandle:CMaterialInstance"),
        ("localMaterialBuffer", "meshMeshMaterialBuffer"),
        ("preloadExternalMaterials", "array:raRef:IMaterial"),
        ("preloadLocalMaterialInstances", "array:CHandle:CMaterialInstance"),
        ("inplaceResources", "array:raRef:CResource"),
        ("appearances", "array:CHandle:meshMeshAppearance"),
        ("objectType", "Enum.ERenderObjectType"),
        ("renderResourceBlob", "CHandle:IRenderResourceBlob"),
        ("lodLevelInfo", "array:Float"),
        ("floatTrackNames", "array:CName"),
        ("boneNames", "array:CName"),
        ("boneRigMatrices", "array:CMatrix"),
        ("boneVertexEpsilons", "array:Float"),
        ("lodBoneMask", "array:Uint8"),
    ),
    "Box": (("Min", "Vector4"), ("Max", "Vector4")),
    "Vector3": _VECTOR3,
    "Vector4": _VECTOR4,
    "CMatrix": (
        ("mx", "Vector4"),
        ("my", "Vector4"),
        ("mz", "Vector4"),
        ("mw", "Vector4"),
    ),
    "CMeshMaterialEntry": (
        ("name", "CName"),
        ("index", "Uint16"),
        ("isLocalInstance", "Bool"),
    ),
    "meshMeshAppearance": (
        ("name", "CName"),
        ("chunkMaterials", "array:CName"),
        ("tags", "array:CName"),
    ),
    "meshMeshMaterialBuffer": (
        ("rawData", "DataBuffer"),
        ("rawDataHeaders", "array:meshLocalMaterialHeader"),
    ),
    "meshLocalMaterialHeader": (("offset", "Uint32"), ("size", "Uint32")),
    "rendRenderMeshBlob": (
        ("header", "rendRenderMeshBlobHeader"),
        ("renderBuffer", "DataBuffer"),
    ),
    "rendRenderMeshBlobHeader": (
        ("version", "Uint32"),
        ("dataProcessing", "Uint32"),
        ("bonePositions", "array:Vector4"),
        ("renderLODs", "array:Float"),
        ("renderChunks", "array:Uint8"),
        ("renderChunkInfos", "array:rendChunk"),
        ("speedTreeWind", "array:Uint8"),
        ("opacityMicromaps", "array:Uint8"),
        ("customData", "array:Uint8"),
        ("customDataElemStride", "Uint32"),
        ("topologyData", "array:Uint8"),
        ("topologyDataStride", "Uint32"),
        ("topologyMetadata", "array:Uint8"),
        ("topologyMetadataStride", "Uint32"),
        ("topology", "array:rendTopologyData"),
        ("quantizationScale", "Vector4"),
        ("quantizationOffset", "Vector4"),
        ("vertexBufferSize", "Uint32"),
        ("indexBufferSize", "Uint32"),
        ("indexBufferOffset", "Uint32"),
    ),
    "rendChunk": (
        ("chunkVertices", "rendVertexBufferChunk"),
        ("chunkIndices", "rendIndexBufferChunk"),
        ("numVertices", "Uint16"),
        ("numIndices", "Uint32"),
        ("materialId", "array:CName"),
        ("vertexFactory", "Uint8"),
        ("baseRenderMask", "Uint16"),
        ("mergedRenderMask", "Uint16"),
        ("lodMask", "Uint8"),
    ),
    "rendVertexBufferChunk": (
        ("vertexLayout", "GpuWrapApiVertexLayoutDesc"),
        ("byteOffsets", "array:Uint32"),
    ),
    "rendIndexBufferChunk": (
        ("pe", "Enum.GpuWrapApieIndexBufferChunkType"),
        ("teOffset", "Uint32"),
    ),
    "GpuWrapApiVertexLayoutDesc": (
        ("elements", "array:GpuWrapApiVertexPackingPackingElement"),
        ("slotStrides", "array:Uint8"),
        ("slotMask", "Uint32"),
        ("hash", "Uint32"),
    ),
    "GpuWrapApiVertexPackingPackingElement": (
        ("type", "Enum.GpuWrapApiVertexPackingePackingType"),
        ("usage", "Enum.GpuWrapApiVertexPackingePackingUsage"),
        ("usageIndex", "Uint8"),
        ("streamIndex", "Uint8"),
        ("streamType", "Enum.GpuWrapApiVertexPackingEStreamType"),
    ),
    "rendTopologyData": (
        ("data", "array:Uint8"),
        ("metadata", "array:Uint8"),
        ("dataStride", "Uint32"),
        ("metadataStride", "Uint32"),
    ),
}


def _vec4(values) -> tuple[float, float, float, float]:
    out = []
    for name in ("X", "Y", "Z", "W"):
        raw = values.get(name, 0.0) if isinstance(values, dict) else 0.0
        out.append(float(raw))
    return tuple(out)  # type: ignore[return-value]


def _read_int16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<h", data, offset)[0]


def _read_u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _read_u8(data: bytes, offset: int) -> int:
    return data[offset]


@dataclass
class MeshSubmesh:
    name: str
    lod: int
    positions: list[list[float]] = field(default_factory=list)
    triangles: list[list[int]] = field(default_factory=list)
    normals: list[list[float]] | None = None
    tangents: list[list[float]] | None = None
    uvs0: list[list[float]] | None = None
    uvs1: list[list[float]] | None = None
    colors: list[list[float]] | None = None
    joints: list[list[int]] | None = None
    weights: list[list[float]] | None = None
    material_name: str = "default"


@dataclass
class MeshInfo:
    name: str
    submeshes: list[MeshSubmesh] = field(default_factory=list)
    bone_names: list[str] = field(default_factory=list)
    bind_matrices: list[list[float]] | None = None
    bone_positions: list[list[float]] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)


def decode_mesh(data: bytes, name: str = "") -> MeshInfo:
    """Decode a cooked ``.mesh`` buffer into a :class:`MeshInfo`."""
    cr2w = parse_cr2w(data)
    root = cr2w.root
    if root is None:
        raise MeshError("CR2W container has no root chunk")
    if not root.type_name.endswith("CMesh"):
        raise MeshError(f"root chunk is {root.type_name!r}, expected CMesh")
    decoder = Red4Decoder(cr2w, SCHEMAS)
    body = decoder.decode_chunk("CMesh", root)

    blob = _resolve_blob(cr2w, body.get("renderResourceBlob"))
    blob_body = decoder.decode_chunk("rendRenderMeshBlob", blob)
    header = blob_body.get("header") or {}
    buffer_data = container_bytes(decoder, cr2w, blob_body.get("renderBuffer"), 1)

    appearances = _resolve_appearances(cr2w, body.get("appearances"))

    bone_names = list(body.get("boneNames") or [])
    bone_matrices = body.get("boneRigMatrices") or []
    bone_positions_list = header.get("bonePositions") or []
    bind_matrices, bone_positions = _bind_bones(bone_names, bone_matrices, bone_positions_list)

    submeshes = _decode_submeshes(cr2w, header, buffer_data, appearances, name)

    chunk_count = len(submeshes)
    bone_count = len(bone_names)
    meta = {
        "Engine": "REDengine",
        "Kind": "mesh",
        "Submeshes": str(chunk_count),
        "Bones": str(bone_count),
        "Appearances": str(len(appearances)),
        "Geometry buffer": str(_buffer_index(cr2w, blob_body.get("renderBuffer"))),
        "Vertex buffer size": str(header.get("vertexBufferSize", 0)),
        "Index buffer offset": str(header.get("indexBufferOffset", 0)),
    }
    return MeshInfo(
        name=name or "mesh",
        submeshes=submeshes,
        bone_names=[str(b) for b in bone_names],
        bind_matrices=bind_matrices,
        bone_positions=bone_positions,
        meta=meta,
    )


def _buffer_index(cr2w: Cr2wFile, buffer_var) -> int:
    if buffer_var is None:
        return -1
    index = getattr(buffer_var, "buffer_index", -1)
    return index


def _resolve_blob(cr2w: Cr2wFile, handle) -> Cr2wChunk:
    if not isinstance(handle, int) or handle < 0:
        raise MeshError("mesh has no rendRenderMeshBlob handle")
    if handle >= len(cr2w.chunks):
        raise MeshError(f"mesh blob handle {handle} out of range ({len(cr2w.chunks)} chunks)")
    chunk = cr2w.chunks[handle]
    if not chunk.type_name.endswith("rendRenderMeshBlob"):
        raise MeshError(f"blob chunk is {chunk.type_name!r}, expected rendRenderMeshBlob")
    return chunk


def _resolve_appearances(cr2w, handles) -> list[dict[str, list[str]]]:
    from dualforge.cdpr.red4 import Red4Decoder

    decoder = Red4Decoder(cr2w, SCHEMAS)
    appearances: list[dict[str, list[str]]] = []
    for handle in handles or []:
        if not isinstance(handle, int) or handle < 0:
            continue
        if handle >= len(cr2w.chunks):
            continue
        chunk = cr2w.chunks[handle]
        if not chunk.type_name.endswith("meshMeshAppearance"):
            continue
        appearance = decoder.decode_chunk("meshMeshAppearance", chunk)
        appearances.append(
            {
                "name": str(appearance.get("name", "")),
                "materials": [str(m) for m in (appearance.get("chunkMaterials") or [])],
            }
        )
    return appearances


def _bind_bones(
    names: list[str],
    matrices: list[dict],
    positions: list[dict],
) -> tuple[list[list[float]] | None, list[list[float]]]:
    """Compute per-bone inverse-bind matrices (WolvenKit ``GetOrphanRig``).

    ``boneRigMatrices`` are the rest-pose model matrices, so the bind matrix
    is their inverse; the per-bone position is that inverse's translation.
    Without matrices, falls back to the blob header's ``bonePositions`` as a
    pure translation with identity rotation.
    """
    if not names:
        return None, []
    if not positions:
        positions = [{}] * len(names)
    binds: list[list[float]] = []
    bones_positions: list[list[float]] = []
    for i in range(len(names)):
        if i < len(matrices):
            matrix = _matrix_from_dict(matrices[i])
            try:
                inv = mat4_inverse_row_major(matrix)
            except ValueError:
                inv = mat4_identity()
            binds.append(inv)
            bones_positions.append([float(v) for v in inv[12:15]])
        else:
            raw = positions[i] if i < len(positions) else {}
            px, py, pz = (float(raw.get("X", 0.0)), float(raw.get("Y", 0.0)), float(raw.get("Z", 0.0)))
            binds.append(mat4_translate(-px, -py, -pz))
            bones_positions.append([px, py, pz])
    return binds, bones_positions


def _matrix_from_dict(values) -> list[float]:
    rows = []
    for key in ("mx", "my", "mz", "mw"):
        cols = _vec4(values.get(key, {}) if isinstance(values, dict) else {})
        rows.extend(cols)
    if not rows:
        return mat4_identity()
    return rows


def _decode_submeshes(
    cr2w: Cr2wFile,
    header: dict[str, object],
    buffer_data: bytes,
    appearances: list[dict[str, list[str]]],
    name: str,
) -> list[MeshSubmesh]:
    quant_scale = _vec4(header.get("quantizationScale") or {})
    quant_trans = _vec4(header.get("quantizationOffset") or {})
    index_buffer_offset = int(header.get("indexBufferOffset", 0) or 0)
    infos = header.get("renderChunkInfos") or []

    default_materials: list[str] = []
    if appearances:
        first = appearances[0]
        default_materials = [str(m) for m in first.get("materials", [])]

    submeshes: list[MeshSubmesh] = []
    for index, info in enumerate(infos):
        if not isinstance(info, dict):
            continue
        submesh = _decode_one_submesh(
            cr2w,
            index,
            info,
            buffer_data,
            quant_scale,
            quant_trans,
            index_buffer_offset,
            default_materials,
            name,
        )
        if submesh is not None:
            submeshes.append(submesh)
    return submeshes


def _decode_one_submesh(
    cr2w,
    index: int,
    info: dict,
    buffer_data: bytes,
    quant_scale: tuple[float, float, float, float],
    quant_trans: tuple[float, float, float, float],
    index_buffer_offset: int,
    default_materials: list[str],
    name: str,
) -> MeshSubmesh | None:
    chunk_vert = info.get("chunkVertices")
    chunk_ind = info.get("chunkIndices")
    if not isinstance(chunk_vert, dict) or not isinstance(chunk_ind, dict):
        return None
    layout = chunk_vert.get("vertexLayout")
    if not isinstance(layout, dict):
        return None
    elements = layout.get("elements") or []
    byte_offsets = chunk_vert.get("byteOffsets") or []
    strides = layout.get("slotStrides") or []

    normal_si: int | None = None
    tangent_si: int | None = None
    color_si: int | None = None
    tex_sis: list[int] = []
    skin_count = 0
    for element in elements:
        if not isinstance(element, dict):
            break
        usage = str(element.get("usage", ""))
        if usage == _PS_NORMAL:
            normal_si = int(element.get("streamIndex", 0) or 0)
        elif usage == _PS_TANGENT:
            tangent_si = int(element.get("streamIndex", 0) or 0)
        elif usage == _PS_COLOR:
            color_si = int(element.get("streamIndex", 0) or 0)
        elif usage == _PS_TEXCOORD:
            tex_sis.append(int(element.get("streamIndex", 0) or 0))
        elif usage == _PS_SKIN_INDICES:
            skin_count += 1

    def _offset(stream_index: int | None, default: int = 0) -> int:
        if stream_index is None or stream_index >= len(byte_offsets):
            return default
        value = byte_offsets[stream_index]
        return int(value or 0) if value is not None else default

    posn_offset = _offset(0)
    normal_offset = _offset(normal_si)
    tangent_offset = _offset(tangent_si)
    color_offset = _offset(color_si)
    tex0_offset = _offset(tex_sis[0] if len(tex_sis) > 0 else None)
    tex1_offset = _offset(tex_sis[1] if len(tex_sis) > 1 else None)

    vp_stride = int(strides[0]) if strides else 8
    te_offset = int(chunk_ind.get("teOffset", 0) or 0)
    indices_offset = index_buffer_offset + te_offset

    vert_count = int(info.get("numVertices", 0) or 0)
    ind_count = int(info.get("numIndices", 0) or 0)
    if vert_count <= 0:
        return None

    weight_count = skin_count * 4
    size = len(buffer_data)

    positions: list[list[float]] = []
    tcoords0: list[list[float]] | None = [] if tex0_offset else None
    tcoords1: list[list[float]] | None = [] if tex1_offset else None
    colors: list[list[float]] | None = [] if color_offset else None
    normals: list[list[float]] | None = [] if normal_offset else None
    tangents: list[list[float]] | None = [] if tangent_offset else None
    joints: list[list[int]] | None = [] if weight_count else None
    weights: list[list[float]] | None = [] if weight_count else None

    def _read(offset: int, steps: int, width: int) -> bytes | None:
        end = offset + steps * width
        if offset < 0 or end > size or (steps > 0 and width == 0):
            return None
        return buffer_data[offset:end]

    for i in range(vert_count):
        vp = posn_offset + i * vp_stride
        raw = _read(vp, 3, 2)
        if raw is None:
            positions.append([0.0, 0.0, 0.0])
        else:
            positions.append(
                [
                    (_read_int16(raw, 0) / 32767.0) * quant_scale[0] + quant_trans[0],
                    (_read_int16(raw, 2) / 32767.0) * quant_scale[1] + quant_trans[1],
                    (_read_int16(raw, 4) / 32767.0) * quant_scale[2] + quant_trans[2],
                ]
            )

        if tcoords0 is not None:
            raw = _read(tex0_offset + i * 4, 2, 2)
            tcoords0.append([hfconvert(_read_u16(raw, 0)), hfconvert(_read_u16(raw, 2))] if raw else [0.0, 0.0])

        if colors is not None:
            stride = 4
            if tex1_offset:
                stride = 8
            raw = _read(color_offset + i * stride, 4, 1)
            colors.append(
                [
                    _read_u8(raw, 0) / 255.0,
                    _read_u8(raw, 1) / 255.0,
                    _read_u8(raw, 2) / 255.0,
                    _read_u8(raw, 3) / 255.0,
                ]
                if raw
                else [0.0, 0.0, 0.0, 1.0]
            )

        if tcoords1 is not None:
            stride = 4
            off = 0
            if color_offset:
                stride = 8
                off = 4
            raw = _read(tex1_offset + i * stride + off, 2, 2)
            tcoords1.append([hfconvert(_read_u16(raw, 0)), hfconvert(_read_u16(raw, 2))] if raw else [0.0, 0.0])

        if normals is not None:
            stride = 4 + (4 if tangent_offset else 0)
            raw = _read(normal_offset + stride * i, 4, 1)
            nx, ny, nz, _w = ten_bit_shifted(_read_u32(raw, 0) if raw else 0)
            _len = (nx * nx + ny * ny + nz * nz) ** 0.5
            if _len > 0.0:
                nx, ny, nz = nx / _len, ny / _len, nz / _len
            normals.append([nx, ny, nz])

        if tangents is not None:
            stride = 4
            off = 0
            if normal_offset:
                off = 4
                stride += 4
            raw = _read(tangent_offset + stride * i + off, 4, 1)
            tx, ty, tz, tw = ten_bit_shifted(_read_u32(raw, 0) if raw else 0)
            _len = (tx * tx + ty * ty + tz * tz) ** 0.5
            if _len > 0.0:
                tx, ty, tz = tx / _len, ty / _len, tz / _len
            tangents.append([tx, ty, tz, tw])

        if joints is not None and weights is not None:
            base = vp + 8
            raw_idx = _read(base, weight_count, 1)
            raw_wt = _read(base + weight_count, weight_count, 1)
            row_idx = [raw_idx[k] for k in range(weight_count)] if raw_idx else []
            row_wt = [raw_wt[k] / 255.0 for k in range(weight_count)] if raw_wt else []
            total = sum(row_wt)
            if total == 0.0 and weight_count > 0:
                row_wt[0] = 1.0
                total = 1.0
            if total > 0.0:
                row_wt = [w / total for w in row_wt]
            joints.append(row_idx)
            weights.append(row_wt)

    triangles: list[list[int]] = []
    for k in range(ind_count // 3):
        base = indices_offset + k * 6
        raw = _read(base, 3, 2)
        if raw is None:
            break
        triangles.append([_read_u16(raw, 0), _read_u16(raw, 2), _read_u16(raw, 4)])

    material_name = "default"
    if index < len(default_materials):
        material_name = default_materials[index] or "default"
    elif isinstance(info.get("materialId"), list):
        material_ids = info.get("materialId")
        if index < len(material_ids):
            material_name = str(material_ids[index]) or "default"

    lod = int(info.get("lodMask", 1) or 0)
    return MeshSubmesh(
        name=f"{name}_submesh_{index:02d}_LOD_{lod}" if name else f"submesh_{index:02d}_LOD_{lod}",
        lod=lod,
        positions=positions,
        triangles=triangles,
        normals=normals or None,
        tangents=tangents or None,
        uvs0=tcoords0 or None,
        uvs1=tcoords1 or None,
        colors=colors or None,
        joints=joints,
        weights=weights,
        material_name=material_name,
    )


def mesh_to_scene(info: MeshInfo):
    """Convert a :class:`MeshInfo` into a :class:`SceneModel` (Z-up)."""
    from dualforge.export.scene import Bone, MeshPrimitive, SceneModel

    bones: list[Bone] = []
    if info.bone_names:
        binds = info.bind_matrices or []
        for i, bone_name in enumerate(info.bone_names):
            bind = binds[i] if i < len(binds) else None
            bones.append(Bone(name=bone_name, parent=-1, bind_matrix=bind))

    meshes = []
    for submesh in info.submeshes:
        skinned = bool(submesh.joints)
        meshes.append(
            MeshPrimitive(
                vertices=submesh.positions,
                triangles=submesh.triangles,
                normals=submesh.normals,
                uvs=submesh.uvs0,
                joints=submesh.joints if skinned else None,
                weights=submesh.weights if skinned else None,
                bones=bones if skinned else [],
                texture_name=submesh.material_name,
            )
        )
    return SceneModel(info.name or "mesh", meshes=meshes, up_axis="Z", uv_v_flip=True)


__all__ = [
    "MeshInfo",
    "MeshSubmesh",
    "SCHEMAS",
    "decode_mesh",
    "mesh_to_scene",
]
