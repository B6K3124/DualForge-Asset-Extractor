"""Handler for ``dualforge export``: turn a raw mesh file into glTF/FBX."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dualforge.export.scene import SCENE_FORMATS, save_scene


def _cmd_export_mesh(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    fmt = args.format.lower().lstrip(".")
    if fmt not in SCENE_FORMATS:
        print(f"unsupported format {fmt!r} (choose from {', '.join(SCENE_FORMATS)})", file=sys.stderr)
        return 1
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return 1
    suffix = path.suffix.lower()
    from dualforge.export.scene import MeshPrimitive, SceneModel

    if suffix == ".glb":
        from dualforge.export.gltf_reader import parse_glb, parse_glb_scene

        try:
            model = parse_glb_scene(data)
            if model is None:
                static = parse_glb(data)
                if static is None:
                    print(f"{path}: no readable geometry", file=sys.stderr)
                    return 1
                model = SceneModel(
                    path.stem,
                    meshes=[
                        MeshPrimitive(
                            vertices=static.verts.tolist(),
                            triangles=static.tris.reshape(-1, 3).astype(int).tolist(),
                            normals=static.normals.tolist(),
                            uvs=(static.uv.tolist() if static.uv is not None else None),
                            texture_name=static.texture_name,
                        )
                    ],
                )
        except Exception as exc:
            print(f"could not parse GLB {path}: {exc}", file=sys.stderr)
            return 1
    elif suffix == ".nif":
        from dualforge.bethesda.nif import parse_nif, read_animations, read_geometry, read_skinned_geometry

        try:
            nif = parse_nif(data)
        except Exception as exc:
            print(f"could not parse NIF {path}: {exc}", file=sys.stderr)
            return 1
        model = read_skinned_geometry(nif)
        if model is None:
            static = read_geometry(nif)
            if static is None:
                print(f"{path}: no readable geometry", file=sys.stderr)
                return 1
            model = SceneModel(
                path.stem,
                meshes=[
                    MeshPrimitive(
                        vertices=static.verts.tolist(),
                        triangles=static.tris.reshape(-1, 3).astype(int).tolist(),
                        normals=static.normals.tolist(),
                        uvs=(static.uv.tolist() if static.uv is not None else None),
                        texture_name=static.texture_name,
                    )
                ],
                up_axis="Z",
                uv_v_flip=True,
            )
        clips = read_animations(nif)
        if clips:
            model.clips = clips
    elif suffix == ".mesh":
        from dualforge.cdpr.mesh import decode_mesh, mesh_to_scene

        try:
            info = decode_mesh(data, name=path.stem)
        except Exception as exc:
            print(f"could not parse mesh {path}: {exc}", file=sys.stderr)
            return 1
        model = mesh_to_scene(info)
    elif suffix == ".rig":
        from dualforge.cdpr.rig import decode_rig, rig_to_scene

        try:
            info = decode_rig(data, name=path.stem)
        except Exception as exc:
            print(f"could not parse rig {path}: {exc}", file=sys.stderr)
            return 1
        model = rig_to_scene(info)
    else:
        print(f"unsupported mesh type {suffix!r}", file=sys.stderr)
        return 1
    out_stem = Path(args.out) if args.out else path.with_suffix("")
    try:
        paths = save_scene(out_stem, model, fmt)
    except Exception as exc:
        print(f"failed to export {path}: {exc}", file=sys.stderr)
        return 1
    for written in paths:
        print(f"wrote {written}")
    return 0