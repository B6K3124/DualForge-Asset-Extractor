"""Minimal Skyrim Special Edition NIF reader (v20.2.0.7, user version 12).

Deliberately small: it understands just enough of the Bethesda-Extended
header and the *geometry* blocks a *preview* needs (``BSTriShape`` skin/bound
vertex data, ``NiTriShapeData`` / ``NiTriStripsData`` legacy strips/shapes)
plus the ``BSLightingShaderProperty`` -> ``BSShaderTextureSet`` path of a
diffuse texture so a mesh can be drawn textured.
"""

from __future__ import annotations

import math
import struct

import numpy as np

from dualforge.bethesda import BethesdaError
from dualforge.export.gltf_reader import MeshGeometry
from dualforge.export.scene import AnimationClip, Bone, MeshPrimitive, SceneModel
from dualforge.log import get_logger

logger = get_logger(__name__)

NIF_VERSION_SSE = 0x14020007

_KEY_LINEAR = 1
_KEY_QUADRATIC = 2
_KEY_TBC = 3
_KEY_XYZ = 4
_KEY_CONSTANT = 5


class R:
    """Little-endian binary reader over a byte slice with bounds checks."""

    __slots__ = ("data", "pos", "end")

    def __init__(self, data: bytes | bytearray, pos: int = 0, end: int | None = None):
        self.data = data
        self.pos = pos
        if end is None:
            end = len(data)
        self.end = end

    def _need(self, n: int) -> None:
        if self.pos + n > self.end:
            raise BethesdaError(
                f"NIF read past end of block at 0x{self.pos:x} (+{n}) "
                f"(block spans 0x..0x{self.end:x})"
            )

    def u8(self) -> int:
        self._need(1)
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        self._need(2)
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        self._need(4)
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        self._need(4)
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self) -> int:
        self._need(8)
        v = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def f32(self) -> float:
        self._need(4)
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def float3(self) -> tuple[float, float, float]:
        return (self.f32(), self.f32(), self.f32())

    def half(self) -> float:
        self._need(2)
        v = struct.unpack_from("<e", self.data, self.pos)[0]
        self.pos += 2
        return v

    def half3(self) -> tuple[float, float, float]:
        return (self.half(), self.half(), self.half())

    def half2(self) -> tuple[float, float]:
        return (self.half(), self.half())

    def bytes(self, n: int) -> bytes:
        self._need(n)
        b = self.data[self.pos : self.pos + n]
        self.pos += n
        return b

    def skip(self, n: int) -> None:
        self._need(n)
        self.pos += n


def _sized_string(reader: R) -> str:
    ln = reader.u32()
    raw = reader.bytes(ln)
    return raw.decode("latin-1", "replace")


def parse_nif(data: bytes) -> NifFile:
    nif = NifFile(data)
    nif.read_header()
    return nif


class NifBlock:
    __slots__ = ("index", "type_name", "offset", "size", "string_table")

    def __init__(self, index: int, type_name: str, offset: int, size: int, string_table: list[str]):
        self.index = index
        self.type_name = type_name
        self.offset = offset
        self.size = size
        self.string_table = string_table

    def reader(self, data: bytes) -> R:
        return R(data, self.offset, self.offset + self.size)

    def name(self, reader: R) -> str:
        idx = reader.u32()
        if 0 <= idx < len(self.string_table):
            return self.string_table[idx]
        return ""


class NifFile:
    def __init__(self, data: bytes):
        self.data = data
        self.blocks: list[NifBlock] = []
        self.strings: list[str] = []
        self.roots: list[int] = []

    def read_header(self) -> None:
        data = self.data
        try:
            he = data.index(b"\x0a") + 1
            version = struct.unpack_from("<I", data, he)[0]
            if version != NIF_VERSION_SSE:
                raise BethesdaError(
                    f"unsupported NIF version 0x{version:08x} (only SSE 0x{NIF_VERSION_SSE:08x})"
                )
            if data[he + 4] != 1:
                raise BethesdaError("unsupported NIF endian type")
            user_version = struct.unpack_from("<I", data, he + 5)[0]
            if user_version != 12:
                raise BethesdaError(f"unsupported NIF user version {user_version}")
            num_blocks = struct.unpack_from("<I", data, he + 9)[0]
            p = he + 13
            bs_version, = struct.unpack_from("<I", data, p)
            if bs_version != 100:
                raise BethesdaError(f"unsupported BS version {bs_version}")
            p += 4

            def export_string() -> str:
                nonlocal p
                ln = data[p]
                p += 1
                raw = data[p : p + ln]
                p += ln
                return raw.decode("latin-1", "replace")

            export_string()  # author
            export_string()  # process script (BS < 131)
            export_string()  # export script
            num_types, = struct.unpack_from("<H", data, p)
            p += 2
            types = []
            table_end = p
            for _ in range(num_types):
                ln, = struct.unpack_from("<I", data, table_end)
                types.append(data[table_end + 4 : table_end + 4 + ln].decode("latin-1", "replace"))
                table_end += 4 + ln
            p = table_end
            type_indices = struct.unpack_from(f"<{num_blocks}H", data, p)
            p += 2 * num_blocks
            sizes = struct.unpack_from(f"<{num_blocks}I", data, p)
            p += 4 * num_blocks
            num_strings, _max_string_len = struct.unpack_from("<II", data, p)
            p += 8
            for _ in range(num_strings):
                ln, = struct.unpack_from("<I", data, p)
                self.strings.append(data[p + 4 : p + 4 + ln].decode("latin-1", "replace"))
                p += 4 + ln
            num_groups, = struct.unpack_from("<I", data, p)
            p += 4 + 4 * num_groups
            num_roots, = struct.unpack_from("<I", data, p)
            p += 4
            self.roots = list(struct.unpack_from(f"<{num_roots}I", data, p))
            p += 4 * num_roots
            off = p
            for i in range(num_blocks):
                if off + sizes[i] > len(data):
                    raise BethesdaError(f"NIF block {i} runs past end of file")
                self.blocks.append(
                    NifBlock(i, types[type_indices[i]], off, sizes[i], self.strings)
                )
                off += sizes[i]
        except struct.error as exc:
            raise BethesdaError(f"could not read NIF header: {exc}") from exc
        if not self.blocks:
            raise BethesdaError("NIF has no blocks")


def _normbyte(b: int) -> float:
    return (b - 128.0) / 127.0 if b != 128 else 0.0


def _strips_to_triangles(strips: list[int], lengths: list[int]) -> list[tuple[int, int, int]]:
    tris = []
    pos = 0
    for ln in lengths:
        strip = strips[pos : pos + ln]
        pos += ln
        for i in range(ln - 2):
            a, b, c = strip[i], strip[i + 1], strip[i + 2]
            if a in (b, c) or b == c:
                continue
            if i % 2 == 0:
                tris.append((a, b, c))
            else:
                tris.append((c, b, a))
    return tris


def _shape_prefix(nif: NifFile, block: NifBlock) -> tuple[R, int, int]:
    """Skips the shared NiObjectNET / NiAVObject / BSTriShape prefix and
    returns the reader plus the shader and skin refs."""
    r = block.reader(nif.data)
    block.name(r)
    num_extra = r.u32()
    if num_extra == 0xFFFFFFFF:
        num_extra = 0
    r.skip(4 * num_extra)
    r.u32()  # flags
    r.skip(12)  # translation
    r.skip(36)  # rotation matrix
    r.f32()  # scale
    r.u32()  # collision object
    r.skip(16)  # NiBound (center + radius)
    skin_ref = r.u32()  # skin ref
    shader_ref = r.u32()  # shader property ref
    r.u32()  # alpha property ref
    return r, shader_ref, skin_ref


def read_dynamic_tri_shape(nif: NifFile, block: NifBlock) -> np.ndarray | None:
    """World-space vertex positions from a BSDynamicTriShape buffer.

    Skyrim SE stores the animated (dynamic) vertex buffer for models like
    character heads here as ``Vector4[...]`` rows; the UV/triangle data
    lives in the paired NiSkinPartition.
    """
    r, _shader_ref, _skin_ref = _shape_prefix(nif, block)
    r.u64()  # vertex desc (only the zero-stride dynamic variant is present)
    r.u16()  # num triangles (0 for dynamic shapes)
    num_vertices = r.u16()
    r.u32()  # static data size (0)
    r.u32()  # dynamic data size
    if num_vertices <= 0 or num_vertices > 1 << 20:
        return None
    rows = np.empty((num_vertices, 4), dtype=np.float32)
    for i in range(num_vertices):
        rows[i] = (r.f32(), r.f32(), r.f32(), r.f32())
    # Some SSE heads store positions in the trailing three components
    # (first column all-zero).
    if np.all(np.abs(rows[:, 0]) < 1e-6):
        return rows[:, 1:4].copy()
    return rows[:, 0:3].copy()


def _vertex_row(reader: R, arg: int) -> tuple:
    """One BSVertexDataSSE row: (pos, normal, uv, bytes_read, weights, bones).

    Positions are full float3 in SSE either way; normal is a byte vector3
    and UV a half2 when the matching arg bits are set.  With the ``0x40``
    vertex-descriptor bit set the row also holds 4x(weight: half, bone: byte),
    returned as ``weights``/``bones`` (both None when unskinned).
    """
    start = reader.pos
    pos = reader.float3() if arg & 0x1 else (0.0, 0.0, 0.0)
    if (arg & 0x11) == 0x11:
        reader.f32()  # bitangent X
    elif arg & 0x1:
        reader.skip(2)  # unused W
    uv = reader.half2() if arg & 0x2 else None
    normal = None
    if arg & 0x8:
        normal = (_normbyte(reader.u8()), _normbyte(reader.u8()), _normbyte(reader.u8()))
        reader.u8()  # bitangent Y
    if (arg & 0x18) == 0x18:
        reader.bytes(3)  # tangent
        reader.u8()  # bitangent Z
    if arg & 0x20:
        reader.skip(4)  # vertex colors
    weights = None
    bones = None
    if arg & 0x40:
        weights = [reader.half() for _ in range(4)]
        bones = [reader.u8() for _ in range(4)]
    if arg & 0x100:
        reader.f32()  # eye data
    return pos, normal, uv, reader.pos - start, weights, bones


def read_skin_partition(
    nif: NifFile, block: NifBlock, dynamic_vertices: np.ndarray | None = None
) -> dict | None:
    """Mesh from a SSE NiSkinPartition: shared interleaved vertex buffer
    plus per-partition triangle copies.

    Layout (v20.2.0.7, user version 12, BS version 100 -- validated against
    real Skyrim SE character files):
      uint data size, uint vertex size, u64 vertex desc, vertex data[...],
      then SkinPartition structs: num verts/tris/bones/strips/weights-per-
      vert (u16 each), bone list (u16 -> global skin-bone index), vertex
      map (local -> global vertex), per-vertex weights (float), faces /
      strips, per-vertex bone indices (byte -> partition bone list entry),
      LOD byte, Global VB byte, u64 partition vertex desc, and the SSE-only
      triangle copy (global vertices).

    The per-partition skin tables are merged back into per-global-vertex
    ``join``/``weight`` arrays (0..4 influences each) so a skinned mesh can
    be exported directly.
    """
    data = nif.data
    offset = block.offset
    data_size, = struct.unpack_from("<I", data, offset)
    vertex_size, = struct.unpack_from("<I", data, offset + 4)
    descriptor, = struct.unpack_from("<Q", data, offset + 8)
    arg = (descriptor >> 44) & 0xFFF
    stride = (descriptor & 0xF) * 4
    if vertex_size != stride or stride <= 0 or data_size % stride != 0:
        return None
    num_vertices = data_size // stride
    if dynamic_vertices is not None and dynamic_vertices.shape[0] != num_vertices:
        return None

    vertices = np.zeros((num_vertices, 3), dtype=np.float32)
    normals = np.zeros((num_vertices, 3), dtype=np.float32)
    normals[:, 2] = 1.0
    uv = np.zeros((num_vertices, 2), dtype=np.float32)
    has_uv = False
    for i in range(num_vertices):
        r = R(data, offset + 16 + i * stride, offset + 16 + (i + 1) * stride)
        pos, normal, uvrow, _consumed, _weights, _bones = _vertex_row(r, arg)
        vertices[i] = pos
        if normal is not None:
            normals[i] = normal
        if uvrow is not None:
            uv[i] = uvrow
            has_uv = True
    if dynamic_vertices is not None:
        vertices = dynamic_vertices

    joints = np.zeros((num_vertices, 4), dtype=np.int32)
    weights = np.zeros((num_vertices, 4), dtype=np.float32)
    has_skin = False

    tris = []
    total_tris = 0
    p = offset + 16 + data_size
    end = offset + block.size
    while end - p >= 12:
        nv, nt, nb, ns, nwp = struct.unpack_from("<5H", data, p)
        p += 10
        partition_bones = struct.unpack_from(f"<{nb}H", data, p) if nb else ()
        p += 2 * nb  # bones (partition bone list -> global skin-bone index)
        has_vm = data[p]
        p += 1
        vertex_map = struct.unpack_from(f"<{nv}H", data, p) if has_vm else None
        if has_vm:
            p += 2 * nv  # vertex map (local -> global vertex)
        has_w = data[p]
        p += 1
        weight_table = struct.unpack_from(f"<{nv * nwp}f", data, p) if has_w else ()
        if has_w:
            p += 4 * nv * nwp  # vertex weights (full floats)
        strip_lengths = struct.unpack_from(f"<{ns}H", data, p) if ns else ()
        p += 2 * ns  # strip lengths
        has_faces = data[p]
        p += 1
        if has_faces and ns == 0:
            p += 6 * nt  # triangles (u16 triplets)
        elif has_faces:
            p += 2 * sum(strip_lengths)  # strips
        has_bi = data[p]
        p += 1
        bone_indices = struct.unpack_from(f"<{nv * nwp}B", data, p) if has_bi else ()
        if has_bi:
            p += nv * nwp  # bone indices (into the partition bone list)
        p += 2  # LOD level, Global VB
        p += 8  # partition vertex desc
        if nt > 0:
            tri = struct.unpack_from(f"<{nt * 3}H", data, p)
            for k in range(0, len(tri), 3):
                a, b, c = tri[k : k + 3]
                if a < num_vertices and b < num_vertices and c < num_vertices:
                    tris.append((a, b, c))
                    total_tris += 1
        p += 6 * nt  # SSE triangle copy (global vertex indices)
        if has_w and has_bi and partition_bones:
            has_skin = True
            for local in range(nv):
                global_vertex = int(vertex_map[local]) if vertex_map else local
                if not (0 <= global_vertex < num_vertices):
                    continue
                for k in range(min(nwp, 4)):
                    slot = local * nwp + k
                    bone = partition_bones[int(bone_indices[slot])]
                    joints[global_vertex][k] = bone
                    weights[global_vertex][k] = weight_table[slot]
    if total_tris <= 0 or not tris:
        return None
    return {
        "verts": vertices,
        "normals": normals,
        "tris": np.asarray(tris, dtype=np.uint32),
        "uv": uv if has_uv else None,
        "joints": joints if has_skin else None,
        "weights": weights if has_skin else None,
        "bone_names": None,
    }


def read_bstri_shape(nif: NifFile, block: NifBlock) -> dict:
    """Extract preview geometry from a Skyrim SE BSTriShape block.

    Layout (v20.2.0.7, user version 12, BS version 100 -- validated against
    real Skyrim SE files):
      NiObjectNET   : name(u32 string idx), num extra data (u32, -1 = none)
      NiAVObject    : flags(u32), translation, rotation(9f), scale, collision
      BSTriShape    : NiBound(16B), skin, shader prop, alpha prop refs,
                      vertex desc(u64), num triangles(u16), num vertices(u16),
                      data size(u32), vertex data, triangles, trailing 8B.
    """
    r = block.reader(nif.data)
    block.name(r)
    num_extra = r.u32()
    if num_extra == 0xFFFFFFFF:
        num_extra = 0
    r.skip(4 * num_extra)
    r.u32()  # flags
    r.skip(12)  # translation
    r.skip(36)  # rotation matrix
    r.f32()  # scale
    r.u32()  # collision object
    r.skip(16)  # NiBound (center + radius)
    skin_ref = r.u32()  # skin ref
    shader_ref = r.u32()  # shader property ref
    r.u32()  # alpha property ref
    descriptor = r.u64()  # BSVertexDesc
    num_triangles = r.u16()
    num_vertices = r.u16()
    r.u32()  # data size (stored)
    arg = (descriptor >> 44) & 0xFFF
    stride = (descriptor & 0xF) * 4

    vertices = np.zeros((num_vertices, 3), dtype=np.float32)
    normals = np.zeros((num_vertices, 3), dtype=np.float32)
    normals[:, 2] = 1.0
    uv = np.zeros((num_vertices, 2), dtype=np.float32)
    has_uv = False
    skin_weights = None
    skin_bones = None
    if arg & 0x40:
        skin_bones = np.zeros((num_vertices, 4), dtype=np.int32)
        skin_weights = np.zeros((num_vertices, 4), dtype=np.float32)
    for i in range(num_vertices):
        row_start = r.pos
        if arg & 0x1:
            vertices[i] = r.float3()
        if (arg & 0x11) == 0x11:
            r.f32()  # bitangent X
        elif (arg & 0x1) == 0x1:
            r.skip(2)  # unused W
        if arg & 0x2:
            uv[i] = r.half2()
            has_uv = True
        if arg & 0x8:
            normals[i] = (
                _normbyte(r.u8()),
                _normbyte(r.u8()),
                _normbyte(r.u8()),
            )
            r.u8()  # bitangent Y
        if (arg & 0x18) == 0x18:
            r.bytes(3)  # tangent
            r.u8()  # bitangent Z
        if arg & 0x20:
            r.bytes(4)  # vertex colors
        if arg & 0x40:
            weights = [r.half() for _ in range(4)]
            bones = [r.u8() for _ in range(4)]
            skin_weights[i] = weights
            skin_bones[i] = bones
        if arg & 0x100:
            r.f32()  # eye data
        consumed = r.pos - row_start
        if consumed > stride:
            raise BethesdaError(
                f"BSTriShape vertex {i} needs {consumed} > stride {stride}"
            )
        if consumed < stride:
            r.skip(stride - consumed)

    triangles = np.empty((num_triangles, 3), dtype=np.uint32)
    for i in range(num_triangles):
        triangles[i] = (r.u16(), r.u16(), r.u16())

    consumed = r.pos - block.offset
    if consumed > block.size:
        raise BethesdaError(
            f"BSTriShape block {block.index} parsed {consumed} of {block.size} bytes"
        )
    if consumed < block.size:
        r.skip(block.size - consumed)
    use_uv = uv if has_uv else None
    return {
        "verts": vertices,
        "normals": normals,
        "tris": triangles,
        "uv": use_uv,
        "joints": skin_bones,
        "weights": skin_weights,
        "skin_ref": skin_ref,
        "texture_name": _shape_texture(nif, shader_ref),
    }


def _shape_texture(nif: NifFile, shader_index: int) -> str | None:
    if not (0 <= shader_index < len(nif.blocks)):
        return None
    shader = nif.blocks[shader_index]
    if shader.type_name != "BSLightingShaderProperty":
        return None
    try:
        s = shader.reader(nif.data)
        s.u32()  # name
        num_extra = s.u32()
        if num_extra == 0xFFFFFFFF:
            num_extra = 0
        s.skip(4 * num_extra)
        s.u32()  # controller
        s.u32()  # shader flags 1
        s.u32()  # shader flags 2
        s.skip(8)  # uv offset
        s.skip(8)  # uv scale
        texture_set_ref = s.u32()
    except Exception:
        logger.debug("shader property %d could not be parsed", shader_index, exc_info=True)
        return None
    if not (0 <= texture_set_ref < len(nif.blocks)):
        return None
    texset = nif.blocks[texture_set_ref]
    if texset.type_name != "BSShaderTextureSet":
        return None
    try:
        end = texset.offset + texset.size
        offset = texset.offset
        first = ""
        while offset < end:
            ln = struct.unpack_from("<I", nif.data, offset)[0]
            if ln <= 0 or ln > end - (offset + 4):
                break
            raw = nif.data[offset + 4 : offset + 4 + ln]
            offset += 4 + ln
            if not first:
                first = raw.decode("latin-1", "replace")
    except Exception:
        logger.debug("texture set %d could not be parsed", texture_set_ref, exc_info=True)
        return None
    if not first:
        return None
    return first.replace("\\", "/").rstrip("\x00").removeprefix("textures/")


def read_geometry(nif: NifFile) -> MeshGeometry | None:
    """MeshGeometry for the first render-able geometry block in the NIF.

    Static props decode from their BSTriShape directly; skinned models
    (character heads etc.) are declared as BSDynamicTriShape and carry the
    live vertex positions while UVs/triangles live in the NiSkinPartition.
    """
    candidates = []
    dynamic_shapes = []
    for block in nif.blocks:
        try:
            if block.type_name == "BSTriShape":
                m = read_bstri_shape(nif, block)
                if m.get("tris") is not None and len(m["tris"]):
                    candidates.append(m)
            elif block.type_name in ("NiTriShapeData", "NiTriStripsData"):
                m = read_legacy_geometry(nif, block)
                if m.get("tris") is not None and len(m["tris"]):
                    candidates.append(m)
            elif block.type_name == "BSDynamicTriShape":
                verts = read_dynamic_tri_shape(nif, block)
                if verts is not None and len(verts):
                    _, shader_ref, skin_ref = _shape_prefix(nif, block)
                    dynamic_shapes.append((verts, shader_ref, skin_ref))
        except BethesdaError:
            continue
        except Exception:
            logger.debug("geometry block %s could not be decoded", block.type_name, exc_info=True)
            continue
    if not candidates and dynamic_shapes:
        verts, shader_ref, _skin_ref = dynamic_shapes[0]
        for block in nif.blocks:
            if block.type_name != "NiSkinPartition":
                continue
            try:
                m = read_skin_partition(nif, block, verts)
            except Exception:
                logger.debug("skin partition %s could not be decoded", block.type_name, exc_info=True)
                continue
            if m is not None:
                m["texture_name"] = _shape_texture(nif, shader_ref)
                candidates.append(m)
    if not candidates:
        return None
    m = max(candidates, key=lambda x: len(x["tris"]))
    return MeshGeometry(
        m["verts"].copy(),
        m["normals"].copy(),
        np.asarray(m["tris"], dtype=np.uint32).reshape(-1),
        np.zeros((0, 2), dtype=np.uint32),
        uv=m["uv"],
        texture=None,
        texture_name=m.get("texture_name"),
    )


def _rest_matrix(translation: tuple[float, float, float], rotation: tuple[float, ...], scale: float) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    for i in range(3):
        for j in range(3):
            m[i][j] = rotation[i * 3 + j] * scale
    m[0][3], m[1][3], m[2][3] = translation
    return m


def _read_ni_hierarchy(nif: NifFile) -> dict[int, tuple[str, np.ndarray, list[int]]]:
    """NiNode / BSFadeNode blocks: name, local rest matrix, children refs.

    Layout (v20.2.0.7, user version 12): NiObjectNET name + extra data, then
    NiAVObject flags / translation / rotation (Matrix33) / scale / collision,
    then NiNode num children refs and (for SSE) an effects ref count."
    """
    out: dict[int, tuple[str, np.ndarray, list[int]]] = {}
    for block in nif.blocks:
        if block.type_name not in ("NiNode", "BSFadeNode"):
            continue
        try:
            r = block.reader(nif.data)
            name = block.name(r)
            num_extra = r.u32()
            if num_extra == 0xFFFFFFFF:
                num_extra = 0
            r.skip(4 * num_extra)
            r.u32()  # NiAVObject flags
            translation = r.float3()
            rotation = tuple(r.f32() for _ in range(9))
            scale = r.f32()
            r.u32()  # collision object
            num_children = r.u32()
            children = [r.u32() for _ in range(num_children)]
            if r.pos - block.offset < block.size:
                num_effects = r.u32()  # effects ref list (SSE)
                r.skip(4 * num_effects)
            out[block.index] = (name, _rest_matrix(translation, rotation, scale), children)
        except Exception:
            logger.debug("node block %s could not be parsed", block.type_name, exc_info=True)
            continue
    return out


def _skinned_hierarchy(nif: NifFile) -> tuple[list[str], list[int], list[np.ndarray]]:
    """Root-first bone list from the NiNode tree: names, parent indices, and
    row-major inverse-bind matrices (skin space)."""
    nodes = _read_ni_hierarchy(nif)
    parent_of: dict[int, int] = {}
    for idx, (_name, _m, children) in nodes.items():
        for child in children:
            if child in nodes:
                parent_of[child] = idx
    roots = [i for i, (_n, _m, _c) in nodes.items() if i not in parent_of]
    order: list[int] = []
    visited: set[int] = set()

    def visit(i: int) -> None:
        if i in visited:
            return
        visited.add(i)
        order.append(i)
        for child in nodes[i][2]:
            if child in nodes:
                visit(child)

    for root in roots:
        visit(root)
    for i in nodes:
        visit(i)

    world: dict[int, np.ndarray] = {}

    def world_of(i: int) -> np.ndarray:
        if i in world:
            return world[i]
        m = nodes[i][1].copy()
        p = parent_of.get(i, -1)
        if p != -1:
            m = world_of(p) @ m
        world[i] = m
        return m

    names: list[str] = []
    parents: list[int] = []
    binds: list[np.ndarray] = []
    positions = {i: k for k, i in enumerate(order)}
    for i in order:
        names.append(nodes[i][0])
        p = parent_of.get(i, -1)
        parents.append(positions[p] if p != -1 else -1)
        binds.append(np.linalg.inv(world_of(i)))
    return names, parents, binds


def _read_skinning_data(nif: NifFile) -> tuple[list[str], list[np.ndarray]] | None:
    """Bone names and rest transforms from a NiSkinningData block.

    Layout (v20.2.0.7, user version 12): skin transform (NiTransform), num
    bones, then per-bone {name SizedString, NiTransform}.  Returns ``None``
    when no block parses with sane counts.
    """
    for block in nif.blocks:
        if block.type_name != "NiSkinningData":
            continue
        try:
            r = block.reader(nif.data)
            r.skip(52)  # skin transform NiTransform
            num_bones = r.u32()
            if num_bones <= 0 or num_bones > 4096:
                continue
            names: list[str] = []
            matrices: list[np.ndarray] = []
            ok = True
            for _ in range(num_bones):
                name = _sized_string(r)
                translation = r.float3()
                rotation = tuple(r.f32() for _ in range(9))
                scale = r.f32()
                if len(name) > 0 and set(name.split(".")[0].replace(" ", "")) - set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") :
                    ok = False
                    break
                names.append(name)
                matrices.append(_rest_matrix(translation, rotation, scale))
            if ok and len(names) == num_bones:
                return names, matrices
        except Exception:
            logger.debug("NiSkinningData block %s could not be parsed", block.index, exc_info=True)
            continue
    return None


def read_skinned_geometry(nif: NifFile) -> SceneModel | None:
    """Build a :class:`SceneModel` from the first skinned geometry block.

    Prefers a BSTriShape carrying per-vertex weights (``0x40`` descriptor
    bit); falls back to a BSDynamicTriShape + NiSkinPartition pair like
    :func:`read_geometry`.  Bones come from the NiNode hierarchy (or the
    NiSkinningData bone list when present); inverse-bind matrices are the
    inverse of each bone's world-space rest transform.  NIF stores V-down
    UVs, so the model is stamped ``uv_v_flip=True``.
    """
    skinned: list[dict] = []
    for block in nif.blocks:
        if block.type_name == "BSTriShape":
            try:
                m = read_bstri_shape(nif, block)
            except Exception:
                logger.debug("BSTriShape %d could not be decoded", block.index, exc_info=True)
                continue
            if m.get("joints") is not None and m.get("weights") is not None:
                skinned.append(m)
        elif block.type_name == "BSDynamicTriShape":
            try:
                verts = read_dynamic_tri_shape(nif, block)
            except Exception:
                continue
            if verts is None or not len(verts):
                continue
            _, shader_ref, _skin_ref = _shape_prefix(nif, block)
            for target in nif.blocks:
                if target.type_name != "NiSkinPartition":
                    continue
                try:
                    part = read_skin_partition(nif, target, verts)
                except Exception:
                    continue
                if part is not None and part.get("joints") is not None:
                    part["texture_name"] = _shape_texture(nif, shader_ref)
                    skinned.append(part)
    if not skinned:
        return None
    best = max(skinned, key=lambda x: len(x["tris"]))
    if best["joints"] is None or best["joints"].size == 0 or best["weights"] is None:
        return None
    if len(best["joints"]) != len(best["weights"]):
        return None

    skinning = _read_skinning_data(nif)
    bone_names, bone_parents, bind_matrices = _skinned_hierarchy(nif)
    if skinning is not None:
        snames, smats = skinning
        if len(snames) == len(bone_names):
            bone_names = snames
        elif len(snames) >= best["joints"].max() + 1:
            bone_names = snames
            bone_parents = [-1] * len(snames)
            bind_matrices = [np.linalg.inv(m) for m in smats]

    bound = best["joints"].max()
    if len(bone_names) <= bound:
        for i in range(len(bone_names), int(bound) + 1):
            bone_names.append(f"Bone_{i}")
            bone_parents.append(-1)
            bind_matrices.append(np.eye(4, dtype=np.float64))

    joints = best["joints"].astype(np.int32).tolist()
    weights = best["weights"]
    sums = weights.sum(axis=1, keepdims=True)
    sums[sums == 0.0] = 1.0
    weights = (weights / sums).astype(np.float32).tolist()
    prim = MeshPrimitive(
        vertices=best["verts"].astype(np.float64).tolist(),
        triangles=best["tris"].astype(np.int64).tolist(),
        normals=best["normals"].astype(np.float64).tolist(),
        uvs=(best["uv"].astype(np.float64).tolist() if best.get("uv") is not None else None),
        joints=joints,
        weights=weights,
        bones=[
            Bone(name, parent, [float(v) for v in m.ravel()])
            for name, parent, m in zip(bone_names, bone_parents, bind_matrices, strict=True)
        ],
        texture_name=best.get("texture_name"),
    )
    return SceneModel(
        "skinned",
        meshes=[prim],
        up_axis="Z",
        uv_v_flip=True,
    )


def read_legacy_geometry(nif: NifFile, block: NifBlock) -> dict:
    r = block.reader(nif.data)
    r.i32()  # group id
    num_vertices = r.u16()
    r.u8()  # keep flags
    r.u8()  # compress flags
    has_vertices = r.u8() != 0
    verts = []
    if has_vertices:
        for _ in range(num_vertices):
            verts.append(r.float3())
    df = r.u16()  # BS data flags
    r.u32()  # material CRC
    has_normals = r.u8() != 0
    normals = []
    if has_normals:
        for _ in range(num_vertices):
            normals.append(r.float3())
    r.skip(16)  # NiBound
    has_vertex_colors = r.u8() != 0
    if has_vertex_colors:
        r.skip(16 * num_vertices)
    if not has_vertices:
        raise BethesdaError("NiGeometryData without vertices")
    uv_sets = (df & 63) | (df & 1)
    if uv_sets:
        r.skip(8 * num_vertices * uv_sets)
    r.u16()  # consistency flags
    r.u32()  # additional data
    r.u16()  # num triangles
    num_strips = r.u16()
    strip_lengths = [r.u16() for _ in range(num_strips)]
    has_points = r.u8() != 0
    points = []
    total = sum(strip_lengths)
    if has_points:
        for _ in range(total):
            points.append(r.u16())
    consumed = r.pos - block.offset
    if consumed != block.size:
        raise BethesdaError(
            f"{block.type_name} block {block.index} parsed {consumed} of {block.size} bytes"
        )
    tris = _strips_to_triangles(points, strip_lengths)
    return {
        "verts": np.array(verts, dtype=np.float32),
        "normals": np.array(normals, dtype=np.float32) if normals else np.zeros((len(verts), 3), dtype=np.float32),
        "tris": tris,
        "uv": None,
        "texture_name": None,
    }


def _read_key_group(reader: R, vec: bool) -> list[tuple[float, list[float]]]:
    """One SSE KeyGroup: num keys, interpolation, then ``(time, value)`` keys.

    Scalar (rotation/scale) and Vector3 (translation) groups both keep their
    forward/backward tangents in quadratic mode; those are discarded.
    """
    num_keyframes = reader.u32()
    if num_keyframes == 0:
        return []
    interpolation = reader.u32()
    if interpolation not in (_KEY_LINEAR, _KEY_QUADRATIC):
        raise BethesdaError(f"unsupported SSE key interpolation type {interpolation}")
    keyframes: list[tuple[float, list[float]]] = []
    for _ in range(num_keyframes):
        t = reader.f32()
        if vec:
            value = [reader.f32(), reader.f32(), reader.f32()]
            if interpolation == _KEY_QUADRATIC:
                reader.skip(24)  # forward/backward tangent vectors
        else:
            value = [reader.f32()]
            if interpolation == _KEY_QUADRATIC:
                reader.skip(8)  # forward/backward tangent scalars
        keyframes.append((t, value))
    return keyframes


def _sample_scalar(keyframes: list[tuple[float, list[float]]], t: float) -> float:
    """Linear sample of a scalar key curve at ``t`` (first/last key pinned)."""
    if not keyframes:
        return 0.0
    if t <= keyframes[0][0]:
        return keyframes[0][1][0]
    for (t0, v0), (t1, v1) in zip(keyframes, keyframes[1:], strict=False):
        if t0 <= t <= t1:
            if t1 == t0:
                return v0[0]
            return v0[0] + (v1[0] - v0[0]) * (t - t0) / (t1 - t0)
    return keyframes[-1][1][0]


def _euler_xyz_quat(x: float, y: float, z: float) -> list[float]:
    """Rotation ``Rx * Ry * Rz`` (applied X then Y then Z) as (w, x, y, z)."""
    cx, cy, cz = math.cos(x / 2.0), math.cos(y / 2.0), math.cos(z / 2.0)
    sx, sy, sz = math.sin(x / 2.0), math.sin(y / 2.0), math.sin(z / 2.0)
    return [
        cx * cy * cz + sx * sy * sz,
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
    ]


def _transform_interpolator(nif: NifFile, interp: NifBlock) -> dict[str, list[tuple[float, list[float]]]]:
    """Animated TRS channels from a NiTransformInterpolator's NiTransformData.

    Layout (v20.2.0.7): interpolator translation (12B), rotation (16B), then the
    NiTransformData ref at offset 28.  The data block holds a ``XYZ_ROTATION``
    rot-type (u32), three scalar Euler KeyGroups, a Vector3 translation group
    and a scalar scale group; the trailing 4 bytes are a sentinel to ignore.
    """
    r = interp.reader(nif.data)
    r.skip(12)  # interpolator translation
    r.skip(16)  # interpolator rotation
    data_ref = r.u32()
    if not (0 <= data_ref < len(nif.blocks)):
        return {}
    data = nif.blocks[data_ref]
    if data.type_name != "NiTransformData":
        return {}
    dr = data.reader(nif.data)
    rotation_type = dr.u32()
    if rotation_type != _KEY_XYZ:
        logger.debug(
            "NiTransformData %d uses unsupported rotation type %d", data.index, rotation_type
        )
        return {}
    x_keys = _read_key_group(dr, False)
    y_keys = _read_key_group(dr, False)
    z_keys = _read_key_group(dr, False)
    trans_keys = _read_key_group(dr, True)
    scale_keys = _read_key_group(dr, False)

    channels: dict[str, list[tuple[float, list[float]]]] = {}
    times = sorted({t for group in (x_keys, y_keys, z_keys) for t, _ in group})
    if times:
        rotation = [
            (t, _euler_xyz_quat(_sample_scalar(x_keys, t), _sample_scalar(y_keys, t), _sample_scalar(z_keys, t)))
            for t in times
        ]
        channels["rotation"] = rotation
    if trans_keys:
        channels["translation"] = [(t, list(v)) for t, v in trans_keys]
    if scale_keys:
        channels["scale"] = [(t, [v[0], v[0], v[0]]) for t, v in scale_keys]
    return channels


def _read_sequence_clip(nif: NifFile, block: NifBlock) -> AnimationClip | None:
    """Decode a NiControllerSequence into its transform clips.

    Layout (v20.2.0.7, BS version 100, validated against real Skyrim SE files):
      u32 num controlled blocks, u32 name (string index),
      then 29-byte controlled blocks (interp ref, ctrl ref, priority byte,
      node name / prop type / ctrl type / ctrl id / interp id string indices),
      then a 38-byte footer (weight, text-key ref, cycle, frequency, start,
      stop, manager ref, accum-root name, u16 note-array count, 4B sentinel).
      There is no array-grow-by and no phase field in SSE files.
    Float/blend and bool controllers (alpha, particle emitters) stay unkeyed
    as the AnimationClip TRS channels cannot carry them.
    """
    r = block.reader(nif.data)
    num_cb = r.u32()
    clip_name = block.name(r)
    tracks: dict[str, dict[str, list]] = {}
    for _ in range(num_cb):
        interp_ref = r.u32()
        r.u32()  # ctrl ref
        r.u8()  # priority
        node_idx = r.u32()
        r.u32()  # prop type
        r.u32()  # ctrl type
        r.u32()  # ctrl id
        r.u32()  # interp id
        if not (0 <= node_idx < len(nif.strings)):
            continue
        if not (0 <= interp_ref < len(nif.blocks)):
            continue
        node_name = nif.strings[node_idx]
        if not node_name:
            continue
        interp = nif.blocks[interp_ref]
        channels = {}
        if interp.type_name == "NiTransformInterpolator":
            channels = _transform_interpolator(nif, interp)
        if not channels:
            continue
        bag = tracks.setdefault(node_name, {})
        for channel, curves in channels.items():
            bag.setdefault(channel, []).extend(curves)
    if not tracks:
        return None
    for _node, channels in tracks.items():
        for channel in channels:
            channels[channel] = sorted(channels[channel], key=lambda kv: kv[0])
    return AnimationClip(clip_name or f"Seq_{block.index}", tracks)


def read_animations(nif: NifFile) -> list[AnimationClip]:
    """Decode every ``NiControllerSequence`` into :class:`AnimationClip` TRS tracks."""
    clips: list[AnimationClip] = []
    seen: dict[str, int] = {}
    for block in nif.blocks:
        if block.type_name != "NiControllerSequence":
            continue
        try:
            clip = _read_sequence_clip(nif, block)
        except Exception:
            logger.debug("NiControllerSequence %d could not be decoded", block.index, exc_info=True)
            continue
        if clip is not None:
            if clip.name in seen:
                seen[clip.name] += 1
                clip.name = f"{clip.name}_{seen[clip.name]}"
            else:
                seen[clip.name] = 0
            clips.append(clip)
    return clips