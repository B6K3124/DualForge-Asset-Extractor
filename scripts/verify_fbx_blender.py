"""Headless Blender verification of DualForge FBX output.

Imports every .fbx in the given folder and reports how Blender interpreted it
(meshes, vertices, vertex groups, bones, actions). Run inside Blender with:

    blender --background --factory-startup --python scripts/verify_fbx_blender.py -- <fbx_folder>
"""

from __future__ import annotations

import os
import sys

import bpy


def _clear_scene() -> None:
    for o in bpy.data.objects:
        bpy.data.objects.remove(o, do_unlink=True)
    for a in bpy.data.actions:
        bpy.data.actions.remove(a)


def _import_one(path: str) -> str:
    before_objs = set(bpy.data.objects)
    before_meshes = set(bpy.data.meshes)
    before_arms = set(bpy.data.armatures)
    before_actions = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=path)
    new_objs = set(bpy.data.objects) - before_objs
    meshes = [o for o in new_objs if o.type == "MESH"]
    armatures = [o for o in new_objs if o.type == "ARMATURE"]
    verts = sum(len(o.data.vertices) for o in meshes)
    armature_bones = sum(len(arm.data.bones) for arm in armatures)
    actions = [a.name for a in set(bpy.data.actions) - before_actions if a.users]
    new_meshes = len(set(bpy.data.meshes) - before_meshes)
    new_arms = len(set(bpy.data.armatures) - before_arms)
    summary = f"{len(meshes)} obj-meshes, {verts} verts, {len(armatures)} armatures ({armature_bones} bones), {len(actions)} actions; raw new: {new_meshes} meshes, {new_arms} armatures"
    if any(o.type == "MESH" and getattr(o, "vertex_groups", None) for o in new_objs):
        summary += " (skin weights on objects)"
    elif any(getattr(m, "vertex_groups", None) for m in (set(bpy.data.meshes) - before_meshes)):
        summary += " (skin weights on meshes)"
    kinds = sorted({o.type for o in new_objs})
    summary += f"  new object kinds: {kinds}"
    return summary


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    folder = argv[0] if argv else "."
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".fbx"))
    if not files:
        print(f"no .fbx files in {folder}")
        return 1
    failed = 0
    for name in files:
        _clear_scene()
        try:
            result = _import_one(os.path.join(folder, name))
        except Exception as exc:
            print(f"[{name}] ERROR: {exc}")
            failed += 1
            continue
        print(f"[{name}] {result}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())