"""Handler for ``dualforge world``: combine Unity meshes into a USD world layer."""

from __future__ import annotations

import argparse
import sys

from dualforge.export.usd import write_usd_world
from dualforge.unity import UnityArchive


def _cmd_world(args: argparse.Namespace) -> int:
    try:
        archive = UnityArchive(args.path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    meshes = list(archive.world_meshes())
    if not meshes:
        print(f"no readable meshes in {args.path}", file=sys.stderr)
        return 1
    textures = list(archive.world_textures())
    try:
        write_usd_world(
            args.out,
            meshes,
            textures=textures,
            scene_name=args.scene,
            up_axis=args.up_axis,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    printed = len(textures)
    print(
        f"wrote {args.out}: {len(meshes)} mesh prims (skinned exporters ignored), "
        f"{printed} textures placed in textures/"
    )
    return 0