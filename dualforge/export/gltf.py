from __future__ import annotations

import json
import math
import struct
from typing import Any
from collections.abc import Sequence


class GltfError(Exception):
    pass


def _floats(data: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(data)}f", *data)


def _uints(data: Sequence[int]) -> bytes:
    return struct.pack(f"<{len(data)}I", *data)


def _uints16(data: Sequence[int]) -> bytes:
    return struct.pack(f"<{len(data)}H", *data)


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def write_gltf(
    path: str,
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    normals: Sequence[Sequence[float]] | None = None,
    uvs: Sequence[Sequence[float]] | None = None,
    name: str = "mesh",
) -> str:
    """Write a minimal glTF 2.0 file (embedded buffers, no external deps)."""
    if not vertices or not triangles:
        raise GltfError("mesh has no geometry to export")

    positions = [float(v) for vert in vertices for v in vert[:3]]
    if normals is None:
        normals = _smooth_normals(vertices, triangles)
    normal_data = [float(n) for n_vec in normals for n in n_vec[:3]]
    uv_data: list[float] = []
    if uvs:
        for uv in uvs:
            uv_data.extend((float(uv[0]), 1.0 - float(uv[1])))
    index_data = [int(i) for tri in triangles for i in tri[:3]]

    pos_bytes = _floats(positions)
    normal_bytes = _floats(normal_data)
    uv_bytes = _floats(uv_data) if uv_data else b""
    index_bytes = _uints(index_data)
    data_bytes = pos_bytes + normal_bytes + uv_bytes + index_bytes
    uri = "data:application/octet-stream;base64," + _b64(data_bytes)

    # Accumulating byte offsets: fill views in streaming order.
    buffer_views = []
    offset = 0
    sections = [(pos_bytes, 34962), (normal_bytes, 34962)]
    if uv_bytes:
        sections.append((uv_bytes, 34962))
    sections.append((index_bytes, 34963))
    for blob, target_val in sections:
        buffer_views.append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(blob), "target": target_val}
        )
        offset += len(blob)

    def accessor(view_idx: int, count: int, comp: int, typ: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "bufferView": view_idx,
            "componentType": comp,
            "count": count,
            "type": typ,
        }
        if extra:
            entry.update(extra)
        return entry

    accessors = [
        accessor(
            0,
            len(vertices),
            5126,
            "VEC3",
            {"min": [min(positions[i::3]) for i in range(3)], "max": [max(positions[i::3]) for i in range(3)]},
        ),
        accessor(1, len(vertices), 5126, "VEC3"),
    ]
    if uv_bytes:
        accessors.append(accessor(2, len(vertices), 5126, "VEC2"))
    accessors.append(accessor(len(buffer_views) - 1, len(index_data), 5125, "SCALAR"))

    attributes: dict[str, Any] = {"POSITION": 0, "NORMAL": 1}
    if uv_bytes:
        attributes["TEXCOORD_0"] = 2
    primitives = [
        {
            "attributes": attributes,
            "indices": len(accessors) - 1,
            "mode": 4,
        }
    ]

    document = {
        "asset": {"version": "2.0", "generator": "DualForge"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": primitives}],
        "buffers": [{"byteLength": len(data_bytes), "uri": uri}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, separators=(",", ":"))
    return path


def write_gltf_skinned(
    path: str,
    *,
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    normals: Sequence[Sequence[float]] | None = None,
    uvs: Sequence[Sequence[float]] | None = None,
    joints: Sequence[Sequence[int]] | None = None,
    weights: Sequence[Sequence[float]] | None = None,
    bind_matrices: Sequence[Sequence[float]] | None = None,
    bone_names: Sequence[str] | None = None,
    bone_parents: Sequence[int] | None = None,
    blendshapes: Sequence[dict[str, Any]] | None = None,
    name: str = "skinned_mesh",
) -> str:
    """Write a skinned glTF 2.0 mesh with skeleton + optional morph targets.

    ``joints``/``weights`` are per-vertex lists of bone influences (up to 4
    each; longer lists are trimmed).  ``bind_matrices`` holds one Unity-style
    row-major 4x4 matrix per bone.  ``bone_names``/``bone_parents`` give the
    skeleton hierarchy (-1 = root bone).  Each entry of ``blendshapes`` is
    ``{"name", "positions", "normals"}`` with deltas parallel to ``vertices``.
    """
    if not vertices or not triangles:
        raise GltfError("mesh has no geometry to export")

    skinned = bool(joints and weights and bind_matrices and bone_names)
    has_morphs = bool(blendshapes)

    positions = [float(v) for vert in vertices for v in vert[:3]]
    if normals is None:
        normals = _smooth_normals(vertices, triangles)
    normal_data = [float(n) for n_vec in normals for n in n_vec[:3]]
    uv_data: list[float] = []
    if uvs:
        for uv in uvs:
            uv_data.extend((float(uv[0]), 1.0 - float(uv[1])))
    index_data = [int(i) for tri in triangles for i in tri[:3]]

    joint_data: list[int] = []
    weight_data: list[float] = []
    if skinned:
        for vert_joints, vert_weights in zip(joints, weights, strict=True):
            pair = [
                (vert_joints[k], vert_weights[k])
                for k in range(min(len(vert_joints), len(vert_weights)))
            ]
            pair.sort(key=lambda item: -float(item[1]))
            pair = pair[:4]
            total = sum(float(w) for _, w in pair) or 1.0
            for idx, wt in pair:
                joint_data.append(int(idx))
                weight_data.append(float(wt) / total)
            for _ in range(4 - len(pair)):
                joint_data.append(0)
                weight_data.append(0.0)

    bind_data: list[float] = []
    if skinned:
        for matrix in bind_matrices:
            bind_data.extend(_to_gltf_mat4([float(matrix[i]) for i in range(16)]))

    morph_pos: list[list[float]] = []
    morph_nrm: list[list[float]] = []
    for shape in blendshapes or []:
        pos = [float(v) for vertex in shape.get("positions", []) for v in vertex[:3]]
        nrm = [float(v) for vertex in (shape.get("normals") or []) for v in vertex[:3]]
        morph_pos.append(pos)
        morph_nrm.append(nrm)

    pos_bytes = _floats(positions)
    normal_bytes = _floats(normal_data)
    uv_bytes = _floats(uv_data) if uv_data else b""
    joint_bytes = _uints16(joint_data) if joint_data else b""
    weight_bytes = _floats(weight_data) if weight_data else b""
    index_bytes = _uints(index_data)
    bind_bytes = _floats(bind_data) if bind_data else b""
    morph_bytes = [
        _floats(pos) + _floats(nrm) for pos, nrm in zip(morph_pos, morph_nrm, strict=True)
    ]

    sections: list[tuple[Any, int]] = [
        (pos_bytes, 34962),
        (normal_bytes, 34962),
    ]
    if uv_bytes:
        sections.append((uv_bytes, 34962))
    if joint_bytes:
        sections.append((joint_bytes, 34962))
    if weight_bytes:
        sections.append((weight_bytes, 34962))
    sections.append((index_bytes, 34963))
    if bind_bytes:
        sections.append((bind_bytes, 34962))

    buffer_views = []
    offsets = []
    cursor = 0
    for blob, target_val in sections:
        buffer_views.append(
            {"buffer": 0, "byteOffset": cursor, "byteLength": len(blob), "target": target_val}
        )
        offsets.append(cursor)
        cursor += len(blob)

    morph_view_pairs: list[list[int]] = []
    for shape_index, blob in enumerate(morph_bytes):
        pair: list[int] = []
        first_len = len(_floats(morph_pos[shape_index]))
        first = blob[:first_len]
        second = blob[first_len:]
        if first:
            buffer_views.append(
                {"buffer": 0, "byteOffset": cursor, "byteLength": len(first), "target": 34962}
            )
            pair.append(len(buffer_views) - 1)
            cursor += len(first)
        if second:
            buffer_views.append(
                {"buffer": 0, "byteOffset": cursor, "byteLength": len(second), "target": 34962}
            )
            pair.append(len(buffer_views) - 1)
            cursor += len(second)
        morph_view_pairs.append(pair)

    data_bytes = b"".join(blob for blob, _ in sections) + b"".join(morph_bytes)
    uri = "data:application/octet-stream;base64," + _b64(data_bytes)

    accessors: list[dict[str, Any]] = []
    view_index = 0

    def add_accessor(
        count: int,
        component_type: int,
        type_name: str,
        extras: dict[str, Any] | None = None,
    ) -> int:
        entry: dict[str, Any] = {
            "bufferView": view_index,
            "componentType": component_type,
            "count": count,
            "type": type_name,
        }
        if extras:
            entry.update(extras)
        accessors.append(entry)
        return len(accessors) - 1

    pos_acc = add_accessor(
        len(vertices),
        5126,
        "VEC3",
        {
            "min": [min(positions[i::3]) for i in range(3)],
            "max": [max(positions[i::3]) for i in range(3)],
        },
    )
    view_index += 1
    normal_acc = add_accessor(len(vertices), 5126, "VEC3")
    view_index += 1
    uv_acc: int | None = None
    if uv_bytes:
        uv_acc = add_accessor(len(vertices), 5126, "VEC2")
        view_index += 1
    joint_acc: int | None = None
    weight_acc: int | None = None
    if joint_bytes:
        joint_acc = add_accessor(len(vertices), 5123, "VEC4")
        view_index += 1
        weight_acc = add_accessor(len(vertices), 5126, "VEC4")
        view_index += 1
    index_acc = add_accessor(len(index_data), 5125, "SCALAR")
    view_index += 1
    bind_acc: int | None = None
    if bind_bytes:
        bind_acc = add_accessor(len(bone_names or []), 5126, "MAT4")
        view_index += 1
    morph_target_accessors: list[list[int]] = []
    for pair in morph_view_pairs:
        entries: list[int] = []
        if pair:
            entries.append(add_accessor(len(vertices), 5126, "VEC3"))
            view_index += 1
        if len(pair) > 1:
            entries.append(add_accessor(len(vertices), 5126, "VEC3"))
            view_index += 1
        morph_target_accessors.append(entries)

    attributes: dict[str, Any] = {"POSITION": pos_acc, "NORMAL": normal_acc}
    if uv_acc is not None:
        attributes["TEXCOORD_0"] = uv_acc
    if joint_acc is not None and weight_acc is not None:
        attributes["JOINTS_0"] = joint_acc
        attributes["WEIGHTS_0"] = weight_acc

    primitive: dict[str, Any] = {
        "attributes": attributes,
        "indices": index_acc,
        "mode": 4,
    }
    targets: list[dict[str, Any]] = []
    for accessor_ids in morph_target_accessors:
        target: dict[str, Any] = {}
        if len(accessor_ids) >= 1:
            target["POSITION"] = accessor_ids[0]
        if len(accessor_ids) >= 2:
            target["NORMAL"] = accessor_ids[1]
        targets.append(target)
    if targets:
        primitive["targets"] = targets

    mesh_node: dict[str, Any] = {"name": name, "mesh": 0, "children": []}
    if has_morphs:
        mesh_node["weights"] = [0.0] * len(targets)

    nodes: list[dict[str, Any]] = [mesh_node]
    if skinned:
        for idx in range(len(bone_names)):
            nodes.append({"name": str(bone_names[idx])})
        bone_root = None
        for idx, parent in enumerate(bone_parents):
            child_index = 1 + idx
            if parent < 0:
                if bone_root is None:
                    bone_root = child_index
                mesh_node["children"].append(child_index)
            else:
                parent_node = 1 + parent if parent < len(bone_names) else bone_root
                if parent_node is None:
                    mesh_node["children"].append(child_index)
                else:
                    nodes[parent_node].setdefault("children", []).append(child_index)

    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "DualForge"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": [
            {
                "name": name,
                "primitives": [primitive],
                **({"weights": [0.0] * len(targets)} if targets else {}),
            }
        ],
        "accessors": accessors,
    }
    if skinned:
        document["skins"] = [
            {
                "joints": list(range(1, 1 + len(bone_names))),
                "inverseBindMatrices": bind_acc,
            }
        ]
    document["buffers"] = [{"byteLength": len(data_bytes), "uri": uri}]
    document["bufferViews"] = buffer_views

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, separators=(",", ":"))
    return path


def write_gltf_animation(
    path: str,
    *,
    name: str,
    bone_names: Sequence[str] | None = None,
    bone_parents: Sequence[int] | None = None,
    tracks: dict[str, dict[str, list[tuple[float, Sequence[float]]]]] | None = None,
) -> str:
    """Write a glTF 2.0 file holding one animation over node TRS channels.

    ``tracks`` maps node names to ``{"translation": [(t, [x, y, z]), ...],
    "rotation": [(t, [x, y, z, w]), ...], "scale": [(t, [x, y, z]), ...]}``.
    If ``bone_names``/``bone_parents`` are supplied an armature is laid out so
    the animation curves can be re-bound in any glTF viewer.
    """
    tracks = tracks or {}
    if not tracks:
        raise GltfError("animation has no keyframes to export")

    nodes: list[dict[str, Any]] = []
    name_to_node: dict[str, int] = {}
    if bone_names:
        for idx in range(len(bone_names)):
            nodes.append({"name": str(bone_names[idx])})
            name_to_node[str(bone_names[idx])] = idx
        for idx, parent in enumerate(bone_parents):
            if 0 <= parent < len(nodes):
                nodes[parent].setdefault("children", []).append(idx)
    else:
        for node_name in tracks:
            name_to_node[node_name] = len(nodes)
            nodes.append({"name": node_name})

    keyframes: list[tuple[int, str]] = []
    for node_name, target_data in tracks.items():
        node_index = name_to_node.get(node_name)
        if node_index is None:
            continue
        for target_name in ("translation", "rotation", "scale"):
            curve = target_data.get(target_name)
            if not curve:
                continue
            keyframes.append((node_index, target_name))

    if not keyframes:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "asset": {"version": "2.0", "generator": "DualForge"},
                    "scene": 0,
                    "scenes": [{"nodes": list(range(len(nodes))) or [0]}],
                    "nodes": nodes or [{"name": name}],
                },
                fh,
                separators=(",", ":"),
            )
        return path

    channel_data: list[dict[str, Any]] = []
    for node_index, target_name in keyframes:
        curve = tracks[nodes[node_index]["name"]][target_name]
        times = [float(t) for t, _ in curve]
        values: list[float] = []
        for _, value in curve:
            values.extend(float(v) for v in value)
        channel_data.append({"node": node_index, "channel": target_name, "times": times, "values": values})

    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    sampler_data: list[dict[str, int]] = []
    channels: list[dict[str, Any]] = []

    def accessor_for(view_idx: int, count: int, comp: int, typ: str) -> int:
        accessors.append(
            {
                "bufferView": view_idx,
                "componentType": comp,
                "count": count,
                "type": typ,
            }
        )
        return len(accessors) - 1

    buf_parts: list[bytes] = []
    for entry in channel_data:
        values_per_keyframe = 3 if entry["channel"] in ("translation", "scale") else 4
        time_blob = _floats(entry["times"])
        value_blob = _floats(entry["values"])
        buf_parts.append(time_blob)
        buf_parts.append(value_blob)
        time_view = len(buf_parts) - 2
        value_view = len(buf_parts) - 1
        time_acc = accessor_for(time_view, len(entry["times"]), 5126, "SCALAR")
        value_acc = accessor_for(
            value_view,
            len(entry["values"]) // values_per_keyframe,
            5126,
            "VEC3" if values_per_keyframe == 3 else "VEC4",
        )
        sampler_data.append({"input": time_acc, "output": value_acc})
        channels.append(
            {
                "sampler": len(sampler_data) - 1,
                "target": {"node": entry["node"], "path": entry["channel"]},
            }
        )

    data_bytes = b"".join(buf_parts)
    uri = "data:application/octet-stream;base64," + _b64(data_bytes)
    buffer_views = []
    cursor = 0
    for _idx, blob in enumerate(buf_parts):
        buffer_views.append(
            {"buffer": 0, "byteOffset": cursor, "byteLength": len(blob), "target": 34962}
        )
        cursor += len(blob)

    document: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "DualForge"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes))) or [0]}],
        "nodes": nodes,
        "animations": [
            {
                "name": name,
                "samplers": [
                    {"input": s["input"], "output": s["output"], "interpolation": "LINEAR"}
                    for s in sampler_data
                ],
                "channels": channels,
            }
        ],
        "buffers": [{"byteLength": len(data_bytes), "uri": uri}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(document, fh, separators=(",", ":"))
    return path


def _to_gltf_mat4(row_major: Sequence[float]) -> list[float]:
    """Convert a Unity-style row-major 4x4 into glTF column-major order."""
    out: list[float] = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[col * 4 + row] = row_major[row * 4 + col]
    return out


def _smooth_normals(
    vertices: Sequence[Sequence[float]], triangles: Sequence[Sequence[int]]
) -> list[tuple[float, float, float]]:
    normals: list[list[float]] = [[0.0, 0.0, 0.0] for _ in vertices]
    for tri in triangles:
        if len(tri) < 3:
            continue
        a, b, c = tri[:3]
        va = vertices[a]
        vb = vertices[b]
        vc = vertices[c]
        u = (vb[0] - va[0], vb[1] - va[1], vb[2] - va[2])
        v = (vc[0] - va[0], vc[1] - va[1], vc[2] - va[2])
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        length = math.sqrt(sum(c * c for c in cross))
        if length == 0:
            continue
        for i in tri[:3]:
            for axis in range(3):
                normals[i][axis] += cross[axis] / length
    out: list[tuple[float, float, float]] = []
    for n in normals:
        length = math.sqrt(sum(c * c for c in n))
        if length == 0:
            out.append((0.0, 1.0, 0.0))
        else:
            out.append((n[0] / length, n[1] / length, n[2] / length))
    return out


__all__ = ["GltfError", "write_gltf", "write_gltf_skinned", "write_gltf_animation"]