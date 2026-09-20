"""REDengine 4 ``.rig`` decoder (animRig).

A cooked ``.rig`` is a CR2W container whose root chunk is an ``animRig``.
Its *RTTI variables* hold bone/track names, reference poses and a-pose
arrays; ``animRig`` is an ``IRedAppendix`` class, so the per-bone parent
indexes and local transforms live in the *appendix* following the variable
terminator (as raw int16s then ``QsTransform`` float blocks).  This module
walks both regions and exposes the skeleton as engine-agnostic
:class:`dualforge.export.scene` models (RED-native Z-up coordinates, with the
standard engine conversion applied on export via ``up_axis="Z"``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from dualforge.cdpr.cr2w import parse_cr2w, split_class_stream
from dualforge.cdpr.red4 import (
    Red4Decoder,
    Red4Error,
    mat4_from_trs,
    mat4_identity,
    mat4_inverse_row_major,
    mat4_multiply,
)

RigError = Red4Error

_VECTOR4 = (("X", "Float"), ("Y", "Float"), ("Z", "Float"), ("W", "Float"))


SCHEMAS: dict[str, tuple[tuple[str, str], ...]] = {
    "animRig": (
        ("boneNames", "array:CName"),
        ("trackNames", "array:CName"),
        ("rigExtraTracks", "array:animFloatTrackInfo"),
        ("levelOfDetailStartIndices", "array:Int16"),
        ("distanceCategoryToLodMap", "array:Int16"),
        ("turnOffLOD", "Int32"),
        ("turningOffUpdateAndSample", "Bool"),
        ("referenceTracks", "array:Float"),
        ("referencePoseMS", "array:QsTransform"),
        ("aPoseLS", "array:QsTransform"),
        ("aPoseMS", "array:QsTransform"),
        ("tags", "redTagList"),
        ("parts", "array:animRigPart"),
        ("retargets", "array:animRigRetarget"),
        ("ikSetups", "array:animSBehavior"),
        ("ragdollDesc", "physicsRagdollBodyInfo"),
        ("ragdollNames", "array:physicsRagdollBodyNames"),
    ),
    "animFloatTrackInfo": (("name", "CName"), ("referenceValue", "Float")),
    "QsTransform": (
        ("Translation", "Vector4"),
        ("Rotation", "Quaternion"),
        ("Scale", "Vector4"),
    ),
    "Quaternion": (("I", "Float"), ("J", "Float"), ("K", "Float"), ("R", "Float")),
    "Vector4": _VECTOR4,
}


@dataclass
class QsTransformInfo:
    translation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    rotation: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    scale: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])

    @classmethod
    def from_floats(cls, raw: list[float]) -> QsTransformInfo:
        return cls(
            translation=[float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])],
            rotation=[float(raw[4]), float(raw[5]), float(raw[6]), float(raw[7])],
            scale=[float(raw[8]), float(raw[9]), float(raw[10]), float(raw[11])],
        )


@dataclass
class RigInfo:
    name: str
    bone_names: list[str] = field(default_factory=list)
    bone_parents: list[int] = field(default_factory=list)
    bone_transforms: list[QsTransformInfo] = field(default_factory=list)
    track_names: list[str] = field(default_factory=list)
    reference_tracks: list[float] = field(default_factory=list)
    a_pose_ls: list[QsTransformInfo] = field(default_factory=list)
    a_pose_ms: list[QsTransformInfo] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)


def _vec4(values) -> list[float]:
    out = []
    for name in ("X", "Y", "Z", "W"):
        raw = values.get(name, 0.0) if isinstance(values, dict) else 0.0
        out.append(float(raw))
    return out


def _quat(values) -> list[float]:
    out = []
    for name in ("I", "J", "K", "R"):
        raw = values.get(name, 0.0) if isinstance(values, dict) else 0.0
        out.append(float(raw))
    return out


def _qs_transform(values) -> QsTransformInfo:
    if not isinstance(values, dict):
        return QsTransformInfo()
    translation = _vec4(values.get("Translation") or {})
    rotation = _quat(values.get("Rotation") or {})
    scale = _vec4(values.get("Scale") or {})
    return QsTransformInfo(translation, rotation, scale)


def decode_rig(data: bytes, name: str = "") -> RigInfo:
    """Decode a cooked ``.rig`` buffer into a :class:`RigInfo`."""
    cr2w = parse_cr2w(data)
    root = cr2w.root
    if root is None:
        raise RigError("CR2W container has no root chunk")
    if not root.type_name.endswith("animRig"):
        raise RigError(f"root chunk is {root.type_name!r}, expected animRig")
    decoder = Red4Decoder(cr2w, SCHEMAS)
    body = decoder.decode_chunk("animRig", root)

    bone_names = [str(b) for b in (body.get("boneNames") or [])]
    _, appendix = split_class_stream(root.data, cr2w.names)

    bone_parents: list[int] = []
    bone_transforms: list[QsTransformInfo] = []
    count = len(bone_names)
    if count:
        needed = count * 2 + count * 12 * 4
        if len(appendix) < needed:
            raise RigError(f"animRig appendix too small ({len(appendix)} < {needed} bytes for {count} bones)")
        parent_offset = 0
        bone_parents = list(struct.unpack_from(f"<{count}h", appendix, parent_offset))
        transform_offset = count * 2
        for i in range(count):
            base = transform_offset + i * 48
            raw = struct.unpack_from("<12f", appendix, base)
            bone_transforms.append(QsTransformInfo.from_floats(raw))

    def _list_of(field_name: str):
        out = body.get(field_name) or []
        return [_qs_transform(v) for v in out if isinstance(v, dict)]

    meta = {
        "Engine": "REDengine",
        "Kind": "rig",
        "Bones": str(count),
        "Tracks": str(len(body.get("trackNames") or [])),
        "Reference tracks": str(len(body.get("referenceTracks") or [])),
        "A-pose LS": str(len(body.get("aPoseLS") or [])),
        "A-pose MS": str(len(body.get("aPoseMS") or [])),
        "Appendix": f"{len(appendix)} bytes",
    }
    return RigInfo(
        name=name or "rig",
        bone_names=bone_names,
        bone_parents=bone_parents,
        bone_transforms=bone_transforms,
        track_names=[str(t) for t in (body.get("trackNames") or [])],
        reference_tracks=[float(t) for t in (body.get("referenceTracks") or [])],
        a_pose_ls=_list_of("aPoseLS"),
        a_pose_ms=_list_of("aPoseMS"),
        meta=meta,
    )


def rig_world_transforms(info: RigInfo) -> tuple[list[list[float]], list[list[float]]]:
    """Compute world matrices and inverse-bind matrices for every bone.

    Local transforms come from the appendix ``boneTransforms``; the hierarchy
    is composed row-major (``world = parent_world @ local``).  Returns
    ``(world_matrices, bind_matrices)`` in REDengine-native space.
    """
    local = [mat4_identity()] * len(info.bone_names)
    for i, transform in enumerate(info.bone_transforms):
        local[i] = mat4_from_trs(
            translation=tuple(transform.translation[:3]),
            quaternion=tuple(transform.rotation),
            scale=tuple(transform.scale[:3]),
        )
    world = [mat4_identity()] * len(local)
    for i in range(len(local)):
        parent = info.bone_parents[i] if i < len(info.bone_parents) else -1
        if parent >= 0 and parent < i:
            world[i] = mat4_multiply(world[parent], local[i])
        else:
            world[i] = local[i]
    binds: list[list[float]] = []
    for w in world:
        try:
            binds.append(mat4_inverse_row_major(w))
        except ValueError:
            binds.append(mat4_identity())
    return world, binds


def rig_to_scene(info: RigInfo):
    """Convert a :class:`RigInfo` into a skinned :class:`SceneModel` (Z-up).

    A lightweight stick scaffold (one quad per bone segment) carries the
    skeleton so the established glTF/FBX writers can serialize the armature;
    every scaffold vertex is pinned to a single joint.
    """
    from dualforge.export.scene import Bone, MeshPrimitive, SceneModel

    world, binds = rig_world_transforms(info)

    bones = [
        Bone(name=name, parent=parent, bind_matrix=bind_matrix)
        for name, parent, bind_matrix in zip(info.bone_names, info.bone_parents, binds, strict=True)
    ]

    origins = [[w[12], w[13], w[14]] for w in world]
    vertices, triangles, joints, weights = _scaffold(info.bone_names, info.bone_parents, origins)
    primitive = MeshPrimitive(
        vertices=vertices,
        triangles=triangles,
        joints=joints,
        weights=weights,
        bones=bones,
    )
    return SceneModel(info.name or "rig", meshes=[primitive], up_axis="Z", uv_v_flip=True)


def _scaffold(
    bone_names: list[str],
    bone_parents: list[int],
    origins: list[list[float]],
    stub: float = 0.25,
    thickness: float = 0.02,
) -> tuple[list[list[float]], list[list[int]], list[list[int]], list[list[float]]]:
    """Build a stick scaffold for the skeleton.

    Returns ``(vertices, triangles, joints, weights)`` where every quad is a
    bone segment pinned to its child bone with a single unit weight (the
    scaffold exists to carry the armature through the skinned writers).
    """
    parents = list(bone_parents) + [-1] * max(0, len(bone_names) - len(bone_parents))
    vertices: list[list[float]] = []
    triangles: list[list[int]] = []
    joints: list[list[int]] = []
    weights: list[list[float]] = []

    def _push(start: list[float], end: list[float], bone_index: int) -> None:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dz = end[2] - start[2]
        length = (dx * dx + dy * dy + dz * dz) ** 0.5
        if length < 1e-9:
            return
        # perpendicular offset (Z-up aware fallback)
        ref = (0.0, 0.0, 1.0) if abs(dz / length) < 0.9 else (1.0, 0.0, 0.0)
        ox = dy * ref[2] - dz * ref[1]
        oy = dz * ref[0] - dx * ref[2]
        oz = dx * ref[1] - dy * ref[0]
        onorm = (ox * ox + oy * oy + oz * oz) ** 0.5
        if onorm < 1e-9:
            return
        half = thickness / 2.0
        ox, oy, oz = ox / onorm * half, oy / onorm * half, oz / onorm * half

        base = len(vertices)
        vertices.append([start[0] - ox, start[1] - oy, start[2] - oz])
        vertices.append([start[0] + ox, start[1] + oy, start[2] + oz])
        vertices.append([end[0] - ox, end[1] - oy, end[2] - oz])
        vertices.append([end[0] + ox, end[1] + oy, end[2] + oz])
        triangles.append([base, base + 2, base + 1])
        triangles.append([base + 1, base + 2, base + 3])
        for _ in range(4):
            joints.append([bone_index])
            weights.append([1.0])

    for i in range(len(bone_names)):
        parent = parents[i]
        if parent >= 0:
            _push(origins[parent], origins[i], i)
        else:
            _push(origins[i], [origins[i][0], origins[i][1], origins[i][2] - stub], i)

    for i in range(len(bone_names)):
        children = [j for j in range(len(bone_names)) if parents[j] == i]
        if children:
            continue
        parent = parents[i]
        if parent >= 0:
            dx = origins[i][0] - origins[parent][0]
            dy = origins[i][1] - origins[parent][1]
            dz = origins[i][2] - origins[parent][2]
            length = (dx * dx + dy * dy + dz * dz) ** 0.5
            if length > 1e-9:
                end = [
                    origins[i][0] + dx / length * stub,
                    origins[i][1] + dy / length * stub,
                    origins[i][2] + dz / length * stub,
                ]
            else:
                end = [origins[i][0], origins[i][1], origins[i][2] - stub]
        else:
            end = [origins[i][0], origins[i][1], origins[i][2] - stub]
        _push(origins[i], end, i)

    return vertices, triangles, joints, weights


__all__ = [
    "QsTransformInfo",
    "RigInfo",
    "SCHEMAS",
    "decode_rig",
    "rig_to_scene",
    "rig_world_transforms",
]
