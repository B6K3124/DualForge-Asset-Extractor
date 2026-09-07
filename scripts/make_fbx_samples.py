"""Generate sample FBX files with DualForge's exporter for DCC verification.

Writes plain, skinned, and animated FBX files into the given directory.
Used by the Blender headless verification (scripts/verify_fbx_blender.py).
"""

from __future__ import annotations

import os
import sys

from dualforge.export.fbx import write_fbx_animation, write_fbx_mesh

_IDENTITY = [[1.0 if r == c else 0.0 for r in range(4) for c in range(4)]]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = argv[0] if argv else "fbx_samples"
    os.makedirs(out_dir, exist_ok=True)

    write_fbx_mesh(
        os.path.join(out_dir, "quad.fbx"),
        "quad",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        [(0, 1, 2), (0, 2, 3)],
        normals=[(0, 0, 1)] * 4,
        uvs=[(0, 0), (1, 0), (1, 1), (0, 1)],
    )

    write_fbx_mesh(
        os.path.join(out_dir, "skinned.fbx"),
        "skinned",
        [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)] * 2,
        [(0, 1, 2), (0, 2, 3)] * 2,
        bone_names=["Root", "Child"],
        bone_parents=[-1, 0],
        bind_matrices=_IDENTITY * 2,
        joints=[[0, 1, 0, 0] * 2 for _ in range(8)],
        weights=[[0.6, 0.4, 0.0, 0.0]] * 8,
    )

    write_fbx_animation(
        os.path.join(out_dir, "run.fbx"),
        name="run",
        tracks={
            "Root": {
                "translation": [(0.0, [0, 0, 0]), (1.0, [1, 0, 0])],
                "rotation": [(0.0, [0, 0, 0, 1]), (1.0, [0, 0, 0.7071, 0.7071])],
            },
        },
    )
    print(f"wrote FBX samples to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())