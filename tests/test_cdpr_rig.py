"""Tests for the REDengine 4 ``.rig`` (animRig + appendix) decoder."""

from __future__ import annotations

import struct

import pytest

from dualforge.cdpr.red4 import mat4_translate
from dualforge.cdpr.rig import RigError, decode_rig, rig_to_scene, rig_world_transforms
from util_cr2w import build_cr2w, pack_class_stream

NAMES = [
    "None",
    "Float",
    "CName",
    "array:CName",
    "array:Float",
    "animRig",
    # field names (animRig)
    "boneNames",
    "trackNames",
    "referenceTracks",
    # CName values
    "Root",
    "Hips",
    "LeftArm",
    "track_move",
]
NORD = {name: i for i, name in enumerate(NAMES)}


def _name(member: str) -> bytes:
    return struct.pack("<H", NORD[member])


def _qs(translation, rotation, scale) -> list[float]:
    return [float(v) for v in (*translation, *rotation, *scale)]


def _appendix() -> bytes:
    parents = struct.pack("<hhh", -1, 0, 1)
    transforms = b"".join(
        struct.pack("<12f", *bone)
        for bone in [
            _qs((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0, 1.0)),
            _qs((0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0, 1.0)),
            _qs((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0, 1.0)),
        ]
    )
    return parents + transforms


def _stream(trailing: bytes = _appendix()) -> bytes:
    return pack_class_stream(
        [
            (
                "boneNames",
                "array:CName",
                struct.pack("<I", 3) + _name("Root") + _name("Hips") + _name("LeftArm"),
            ),
            (
                "trackNames",
                "array:CName",
                struct.pack("<I", 1) + _name("track_move"),
            ),
            (
                "referenceTracks",
                "array:Float",
                struct.pack("<I", 1) + struct.pack("<f", 1.0),
            ),
        ],
        NORD,
        NORD,
        trailing=trailing,
    )


def _rig_bytes(trailing: bytes | None = None) -> bytes:
    return build_cr2w(NAMES, [("animRig", _stream(trailing if trailing is not None else _appendix()))])


def test_decode_rig_names_and_appendix() -> None:
    info = decode_rig(_rig_bytes(), name="hero_rig")
    assert info.name == "hero_rig"
    assert info.bone_names == ["Root", "Hips", "LeftArm"]
    assert info.bone_parents == [-1, 0, 1]
    assert info.track_names == ["track_move"]
    assert info.reference_tracks == pytest.approx([1.0])
    assert len(info.bone_transforms) == 3
    assert info.bone_transforms[1].translation == pytest.approx([0.0, 1.0, 0.0, 1.0])
    assert info.bone_transforms[2].scale == pytest.approx([1.0, 1.0, 1.0, 1.0])
    assert info.meta["Bones"] == "3"


def test_rig_world_transforms() -> None:
    info = decode_rig(_rig_bytes())
    world, binds = rig_world_transforms(info)
    origins = [[w[12], w[13], w[14]] for w in world]
    assert origins == [list(p) for p in [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 1.0, 1.0)]]
    assert binds[0] == pytest.approx(mat4_translate(0.0, 0.0, 0.0))
    assert binds[1] == pytest.approx(mat4_translate(0.0, -1.0, 0.0))
    assert binds[2] == pytest.approx(mat4_translate(0.0, -1.0, -1.0))


def test_rig_to_scene() -> None:
    info = decode_rig(_rig_bytes())
    scene = rig_to_scene(info)
    assert scene.up_axis == "Z"
    assert len(scene.meshes) == 1
    mesh = scene.meshes[0]
    assert [b.name for b in mesh.bones] == ["Root", "Hips", "LeftArm"]
    assert [b.parent for b in mesh.bones] == [-1, 0, 1]
    assert len(mesh.vertices) > 0
    assert mesh.joints and len(mesh.joints) == len(mesh.vertices)


def test_decode_rig_appendix_too_small() -> None:
    data = _rig_bytes(trailing=struct.pack("<hh", -1, 0))
    with pytest.raises(RigError):
        decode_rig(data)


def test_decode_rig_wrong_root() -> None:
    names = list(NAMES)
    names.append("someOtherClass")
    nord = {name: i for i, name in enumerate(names)}
    stream = pack_class_stream([], nord, nord)
    data = build_cr2w(names, [("someOtherClass", stream)])
    with pytest.raises(RigError):
        decode_rig(data)
