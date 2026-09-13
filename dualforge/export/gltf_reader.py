from __future__ import annotations

import base64
import json
import struct
import urllib.parse
from pathlib import Path
from typing import Any
from collections.abc import Sequence

import numpy as np


class GltfReaderError(Exception):
    pass


class MeshGeometry:
    """Mesh preview geometry that unpacks exactly like the historical 4-tuple.

    ``verts, normals, tris, edges = geometry`` keeps working, while
    ``.uv`` / ``.texture`` (optional) carry the data needed to *texture-map*
    the model: per-vertex UVs (float32 ``(N,2)``) and the raw image bytes of
    the first material's base-color texture.
    """

    __slots__ = ("verts", "normals", "tris", "edges", "uv", "texture", "texture_name")

    def __init__(
        self,
        verts,
        normals,
        tris,
        edges,
        uv=None,
        texture=None,
        texture_name=None,
    ) -> None:
        self.verts = verts
        self.normals = normals
        self.tris = tris
        self.edges = edges
        self.uv = uv
        self.texture = texture
        self.texture_name = texture_name

    def __iter__(self):
        yield from (self.verts, self.normals, self.tris, self.edges)

    def __len__(self) -> int:
        return 4

    def __getitem__(self, index):
        return (self.verts, self.normals, self.tris, self.edges)[index]


_COMPONENT_TYPES = {
    5120: ("b", 1),  # BYTE
    5121: ("B", 1),  # UNSIGNED_BYTE
    5122: ("h", 2),  # SHORT
    5123: ("H", 2),  # UNSIGNED_SHORT
    5125: ("I", 4),  # UNSIGNED_INT
    5126: ("f", 4),  # FLOAT
}
_COMPONENT_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_ARRAY_TYPE_TO_DTYPE = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_TARGET_ELEMENT_BYTE_OFFSET = 34963


def parse_glb(data: bytes):
    """Parse a glTF 2.0 asset (binary GLB or JSON) into mesh preview geometry.

    Returns a :class:`MeshGeometry` (unpacks like ``(verts, normals, tris,
    edges)`` — the shape the Unity OBJ path produces) with optional ``.uv``
    (``TEXCOORD_0``, float32 ``(N,2)``) and ``.texture`` (raw image bytes of
    the primitive's base-color material).  Returns ``None`` when the document
    holds no mesh.
    """
    if data[:4] == b"glTF":
        doc, buffers = _read_glb(data)
    else:
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GltfReaderError(f"not a readable glTF asset: {exc}") from exc
        buffers = _load_external_buffers(doc)

    meshes = doc.get("meshes")
    if not meshes:
        return None
    positions: Sequence[Sequence[float]] | None = None
    normals: Sequence[Sequence[float]] | None = None
    indices: Sequence[int] | None = None
    uv_list: Sequence[Sequence[float]] | None = None
    has_normals_field = False
    for mesh in meshes:
        for primitive in mesh.get("primitives", []):
            attributes = primitive.get("attributes", {}) or {}
            if "POSITION" not in attributes:
                continue
            pos = _read_accessor(doc, buffers, attributes["POSITION"])
            if pos is None:
                continue
            positions = pos
            norm_accessor = attributes.get("NORMAL")
            if norm_accessor is not None:
                read_normals = _read_accessor(doc, buffers, norm_accessor)
                if read_normals is not None:
                    normals = read_normals
                    has_normals_field = True
            uv_accessor = attributes.get("TEXCOORD_0")
            if uv_accessor is not None:
                read_uv = _read_accessor(doc, buffers, uv_accessor)
                if read_uv is not None:
                    uv_list = read_uv
            index_accessor = primitive.get("indices")
            if index_accessor is not None:
                indices = _read_accessor(doc, buffers, index_accessor, allow_unsigned=True)
            break
        if positions is not None:
            break

    if positions is None or len(positions) < 3:
        return None
    if indices is None or len(indices) < 3:
        return None

    verts = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
    have_normals = has_normals_field and normals is not None and len(normals) >= len(verts)
    if have_normals:
        n = np.asarray(normals, dtype=np.float32).reshape(-1, 3)
        lengths = np.linalg.norm(n, axis=1, keepdims=True)
        lengths[lengths == 0] = 1.0
        n = n / lengths
    else:
        n = np.zeros((len(verts), 3), dtype=np.float32)

    idx = np.asarray(indices, dtype=np.uint32).reshape(-1)
    face_count, remainder = divmod(idx.size, 3)
    if remainder or face_count == 0:
        return None
    t = idx.reshape(face_count, 3)
    edge_set = set()
    for a, b, _c in t:
        edge_set.add((int(min(a, b)), int(max(a, b))))
        edge_set.add((int(min(b, _c)), int(max(b, _c))))
        edge_set.add((int(min(_c, a)), int(max(_c, a))))
    e = np.asarray(sorted(edge_set), dtype=np.uint32).reshape(-1, 2)
    if not have_normals:
        n = _smooth_normals(verts, t)

    uv = None
    if uv_list is not None and len(uv_list):
        uv = np.asarray(uv_list, dtype=np.float32).reshape(-1, 2)
        if len(uv) < len(verts):
            padded = np.zeros((len(verts), 2), dtype=np.float32)
            padded[: len(uv)] = uv
            uv = padded
        elif len(uv) > len(verts):
            uv = uv[: len(verts)]
    texture, texture_name = None, None
    if primitive_material := _resolve_material_texture(doc, buffers, primitive):
        texture, texture_name = primitive_material
    return MeshGeometry(verts, n, t, e, uv=uv, texture=texture, texture_name=texture_name)


def _read_glb(data: bytes) -> tuple[dict[str, Any], list[bytes]]:
    if len(data) < 20:
        raise GltfReaderError("GLB file is too small")
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise GltfReaderError("invalid GLB magic")
    offset = 12
    json_bytes: bytes | None = None
    buff_bytes: list[bytes] = []
    while offset < len(data):
        if offset + 8 > len(data):
            break
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset : offset + chunk_len]
        offset += chunk_len
        if chunk_type == 0x4E4F534A:  # JSON
            json_bytes = chunk
        elif chunk_type == 0x004E4942:  # BIN
            buff_bytes.append(chunk)
    if json_bytes is None:
        raise GltfReaderError("GLB has no JSON chunk")
    try:
        doc = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GltfReaderError(f"GLB JSON chunk is invalid: {exc}") from exc
    return doc, buff_bytes


def _load_external_buffers(doc: dict[str, Any]) -> list[bytes]:
    buffers: list[bytes] = []
    for buffer_spec in doc.get("buffers", []) or []:
        uri = buffer_spec.get("uri", "")
        if uri.startswith("data:"):
            buffers.append(_decode_data_uri(uri))
        else:
            raise GltfReaderError(
                f"glTF references an external buffer ('{uri}') that cannot be embedded"
            )
    return buffers


def _decode_data_uri(uri: str) -> bytes:
    comma = uri.find(",")
    if comma == -1:
        raise GltfReaderError("malformed data URI")
    header, payload = uri[:comma], uri[comma + 1 :]
    mimetype = header.split(";")
    if "base64" in mimetype or "BASE64" in mimetype:
        try:
            return base64.b64decode(payload)
        except Exception as exc:
            raise GltfReaderError(f"invalid base64 data URI: {exc}") from exc
    return urllib.parse.unquote_to_bytes(payload)


def _resolve_material_texture(
    doc: dict[str, Any], buffers: list[bytes], primitive: dict[str, Any]
) -> tuple[bytes, str] | None:
    """Best-effort base-color texture for a glTF primitive.

    Follows ``primitive.material -> pbrMetallicRoughness.baseColorTexture``
    (falling back to a plain ``baseColorTexture`` or ``emissiveTexture``) to
    ``textures[].source -> images[]`` and returns the decoded ``(image_bytes,
    name)``. Images may come from a data URI or the BIN bufferView. Returns
    ``None`` when the material/geometry carries no embeddable texture.
    """
    material_index = primitive.get("material")
    if material_index is None:
        return None
    materials = doc.get("materials") or []
    if not (0 <= material_index < len(materials)):
        return None
    material = materials[material_index]
    texture_slots = [
        (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture"),
        material.get("baseColorTexture"),
        material.get("emissiveTexture"),
    ]
    for slot in texture_slots:
        texture_index = (slot or {}).get("index")
        if texture_index is None:
            continue
        textures = doc.get("textures") or []
        if not (0 <= texture_index < len(textures)):
            continue
        image_index = textures[texture_index].get("source")
        if image_index is None:
            continue
        images = doc.get("images") or []
        if not (0 <= image_index < len(images)):
            continue
        image = images[image_index]
        uri = image.get("uri")
        if uri:
            if uri.startswith("data:"):
                try:
                    return _decode_data_uri(uri), image.get("name") or "texture"
                except GltfReaderError:
                    return None, None
            return None, None
        view_index = image.get("bufferView")
        if view_index is None:
            continue
        views = doc.get("bufferViews") or []
        if not (0 <= view_index < len(views)):
            continue
        view = views[view_index]
        buffer_index = view.get("buffer", 0)
        if not (0 <= buffer_index < len(buffers)):
            continue
        buffer = buffers[buffer_index]
        start = int(view.get("byteOffset", 0))
        end = start + int(view.get("byteLength", 0))
        if 0 <= start <= end <= len(buffer):
            return buffer[start:end], image.get("name") or f"texture{view_index}"
    return None


def _read_accessor(
    doc: dict[str, Any],
    buffers: list[bytes],
    accessor_index: int,
    allow_unsigned: bool = False,
) -> list[Any] | None:
    accessors = doc.get("accessors", []) or []
    if not (0 <= accessor_index < len(accessors)):
        return None
    accessor = accessors[accessor_index]
    view_index = accessor.get("bufferView")
    if view_index is None:
        return None
    views = doc.get("bufferViews", []) or []
    if not (0 <= view_index < len(views)):
        return None
    view = views[view_index]
    buffer_index = view.get("buffer", 0)
    if not (0 <= buffer_index < len(buffers)):
        return None
    buffer = buffers[buffer_index]

    component_type = accessor.get("componentType", 0)
    if component_type not in _COMPONENT_TYPES:
        return None
    component_size = _COMPONENT_TYPES[component_type][1]
    type_name = accessor.get("type", "SCALAR")
    component = _COMPONENT_COUNT.get(type_name)
    if component is None:
        return None

    byte_offset = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    stride = int(view.get("byteStride", 0)) or component * component_size
    needed = byte_offset + stride * (accessor.get("count", 0) - 1) + component_size
    if needed > len(buffer):
        return None

    values: list[Any] = []
    for i in range(int(accessor.get("count", 0))):
        offset = byte_offset + i * stride
        elements = struct.unpack_from(
            f"<{component}{_COMPONENT_TYPES[component_type][0]}", buffer, offset
        )
        if component == 1:
            values.append(elements[0])
        else:
            values.append(list(elements))
    return values


def _smooth_normals(
    vertices: np.ndarray, triangles: np.ndarray
) -> np.ndarray:
    import math

    n = np.zeros_like(vertices, dtype=np.float32)
    for tri in triangles:
        a, b, c = (int(tri[0]), int(tri[1]), int(tri[2]))
        va, vb, vc = vertices[a], vertices[b], vertices[c]
        u = vb - va
        v = vc - va
        cross = np.cross(u, v)
        length = math.sqrt(float(np.dot(cross, cross)))
        if length == 0:
            continue
        cross = cross / length
        n[a] += cross
        n[b] += cross
        n[c] += cross
    lengths = np.linalg.norm(n, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    n = n / lengths
    return n


def read_glb(path: str):
    """Read a GLB/glTF file on disk into preview geometry (same tuple as parse_glb)."""
    data = Path(path).read_bytes()
    return parse_glb(data)


__all__ = [
    "GltfReaderError",
    "MeshGeometry",
    "parse_glb",
    "read_glb",
]