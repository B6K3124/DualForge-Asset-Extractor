"""Engine-agnostic scene model and exporters for skinned meshes and clips.

The :class:`SceneModel` is the neutral intermediate every engine parser
(Bethesda NIF, Unity, CDPR, Unreal GLB) produces; :func:`save_scene` routes it
into the existing glTF / FBX writers.  Writers keep their established
contracts (row-major inverse-bind matrices, Y-up pass-through, ``1.0 - v`` UV
flip) and the scene adapter converts each engine's native conventions to those
contracts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


class SceneError(Exception):
    pass


SCENE_FORMATS = ("gltf", "fbx")


@dataclass
class Bone:
    """One skeleton bone.

    ``bind_matrix`` is the row-major 4x4 *inverse* bind pose, exactly what the
    glTF / FBX writers consume.  ``parent`` is the index into the owning
    mesh's ``bones`` list (-1 = root).
    """

    name: str
    parent: int = -1
    bind_matrix: list[float] | None = None


@dataclass
class MeshPrimitive:
    vertices: list[list[float]]
    triangles: list[list[int]]
    normals: list[list[float]] | None = None
    uvs: list[list[float]] | None = None
    joints: list[list[int]] | None = None
    weights: list[list[float]] | None = None
    bones: list[Bone] = field(default_factory=list)
    blendshapes: list[dict[str, Any]] | None = None
    texture_name: str | None = None


@dataclass
class AnimationClip:
    name: str
    tracks: dict[str, dict[str, list[tuple[float, Sequence[float]]]]]
    fps: float = 60.0


@dataclass
class SceneModel:
    name: str
    meshes: list[MeshPrimitive] = field(default_factory=list)
    clips: list[AnimationClip] | None = None
    up_axis: str = "Y"  # engine-native world-up axis ("Y" is the writer default)
    uv_v_flip: bool = False  # engine stores UVs with V=0 at the image top


# Rotation (X -> X, Y -> Z, Z -> -Y): Z-up -> Y-up, det +1 (right-handed).
_UP_MAT3 = np.array(
    [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]], dtype=np.float64
)
_UP_MAT4 = np.eye(4, dtype=np.float64)
_UP_MAT4[:3, :3] = _UP_MAT3
_QUAT_X_NEG90 = (math.sqrt(0.5), -math.sqrt(0.5), 0.0, 0.0)


def _rotate_vectors(rows: np.ndarray) -> np.ndarray:
    return rows @ _UP_MAT3.T


def _rotate_bind(mat: Sequence[float]) -> list[float]:
    m = np.asarray(mat, dtype=np.float64).reshape(4, 4)
    out = _UP_MAT4 @ m @ _UP_MAT4.T
    return [float(v) for v in out.ravel()]


def _qmul(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _rotate_quat(q: Sequence[float]) -> tuple[float, float, float, float]:
    w, x, y, z = q
    # Conjugate by the world-up frame rotation (inverse of _QUAT_X_NEG90) so
    # rotation axes follow the same mapping as positions and bind matrices.
    conj = (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)
    return _qmul(_qmul(_QUAT_X_NEG90, (w, x, y, z)), conj)


def _adapt_points(mesh: MeshPrimitive, model: SceneModel) -> list[list[float]]:
    pts = np.asarray(mesh.vertices, dtype=np.float64)
    if model.up_axis == "Z":
        pts = _rotate_vectors(pts)
    return [[float(v) for v in row] for row in pts]


def _adapt_normals(mesh: MeshPrimitive, model: SceneModel) -> list[list[float]] | None:
    if mesh.normals is None:
        return None
    rows = np.asarray(mesh.normals, dtype=np.float64)
    if model.up_axis == "Z":
        rows = _rotate_vectors(rows)
    return [[float(v) for v in row] for row in rows]


def _adapt_uvs(mesh: MeshPrimitive, model: SceneModel) -> list[list[float]] | None:
    if mesh.uvs is None:
        return None
    if not model.uv_v_flip:
        return list(mesh.uvs)
    return [[float(u), 1.0 - float(v)] for u, v in mesh.uvs]


def _adapt_bone(mesh: MeshPrimitive, model: SceneModel) -> tuple[list[str], list[int], list[list[float]]]:
    names: list[str] = []
    parents: list[int] = []
    binds: list[list[float]] = []
    for bone in mesh.bones:
        names.append(bone.name)
        parents.append(bone.parent)
        bind = bone.bind_matrix or [
            [1.0 if r == c else 0.0 for c in range(4)] for r in range(4)
        ]
        if model.up_axis == "Z":
            bind = _rotate_bind(bind)
        binds.append([float(v) for v in bind])
    return names, parents, binds


def _adapt_tracks(clip: AnimationClip, model: SceneModel) -> dict[str, dict[str, list]]:
    if model.up_axis != "Z":
        return clip.tracks
    out: dict[str, dict[str, list]] = {}
    for node, channels in clip.tracks.items():
        adapted: list[tuple] = []
        channels_out: dict[str, list] = {}
        for channel in ("translation", "rotation", "scale"):
            curve = channels.get(channel)
            if not curve:
                continue
            if channel == "translation":
                adapted = [(t, [float(v) for v in np.asarray(vals) @ _UP_MAT3.T]) for t, vals in curve]
            elif channel == "rotation":
                adapted = [(t, list(_rotate_quat(vals))) for t, vals in curve]
            else:
                adapted = [(t, [float(v) for v in vals]) for t, vals in curve]
            channels_out[channel] = adapted
        out[node] = channels_out
    return out


def _save_mesh(path: str, model: SceneModel, mesh: MeshPrimitive, fmt: str) -> None:
    vertices = _adapt_points(mesh, model)
    triangles = [[int(i) for i in tri] for tri in mesh.triangles]
    normals = _adapt_normals(mesh, model)
    uvs = _adapt_uvs(mesh, model)
    joints = mesh.joints
    weights = mesh.weights
    bone_names: list[str] | None = None
    bone_parents: list[int] | None = None
    bind_matrices: list[list[float]] | None = None
    if mesh.bones:
        bone_names, bone_parents, bind_matrices = _adapt_bone(mesh, model)
    if fmt == "gltf":
        from dualforge.export.gltf import write_gltf, write_gltf_skinned

        if not mesh.bones:
            write_gltf(path, vertices, triangles, normals=normals, uvs=uvs, name=model.name)
        else:
            write_gltf_skinned(
                path,
                vertices=vertices,
                triangles=triangles,
                normals=normals,
                uvs=uvs,
                joints=joints,
                weights=weights,
                bind_matrices=bind_matrices,
                bone_names=bone_names,
                bone_parents=bone_parents,
                blendshapes=mesh.blendshapes,
                name=model.name,
            )
    else:
        from dualforge.export.fbx import write_fbx_mesh

        write_fbx_mesh(
            path,
            model.name,
            vertices,
            triangles,
            normals=normals,
            uvs=uvs,
            bone_names=bone_names,
            bone_parents=bone_parents,
            bind_matrices=bind_matrices,
            joints=joints,
            weights=weights,
            blendshapes=mesh.blendshapes,
        )


def save_scene(path_stem: Path, model: SceneModel, fmt: str) -> list[str]:
    """Export a :class:`SceneModel` to glTF or FBX; returns written paths.

    Each mesh becomes its own ``<stem>.gltf``/``.fbx`` (``<stem>.<i>.`` when a
    model carries several meshes); every clip is written to
    ``<stem>.<clip>.``.  Skinned and static meshes, plus clips, all use the
    proven DualForge writers.
    """
    fmt = fmt.lower().lstrip(".")
    if fmt not in SCENE_FORMATS:
        raise SceneError(f"unsupported scene format {fmt!r}")
    written: list[str] = []
    for i, mesh in enumerate(model.meshes):
        stem = path_stem if len(model.meshes) == 1 else path_stem.with_name(f"{path_stem.stem}.{i}")
        target = stem.with_suffix(f".{fmt}")
        _save_mesh(str(target), model, mesh, fmt)
        written.append(str(target))
    for clip in model.clips or []:
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in clip.name).strip("._")
        target = path_stem.with_name(f"{path_stem.stem}.{safe_name or 'clip'}.{fmt}")
        tracks = _adapt_tracks(clip, model)
        if fmt == "gltf":
            from dualforge.export.gltf import write_gltf_animation

            bone_names = None
            bone_parents = None
            if model.meshes and model.meshes[0].bones:
                bone_names, bone_parents, _ = _adapt_bone(model.meshes[0], model)
            write_gltf_animation(
                str(target),
                name=clip.name,
                bone_names=bone_names,
                bone_parents=bone_parents,
                tracks=tracks,
            )
        else:
            from dualforge.export.fbx import write_fbx_animation

            write_fbx_animation(str(target), clip.name, tracks, fps=clip.fps)
        written.append(str(target))
    if not written:
        raise SceneError(f"scene {model.name!r} has no meshes or clips to export")
    return written


__all__ = [
    "AnimationClip",
    "Bone",
    "MeshPrimitive",
    "SCENE_FORMATS",
    "SceneError",
    "SceneModel",
    "save_scene",
]