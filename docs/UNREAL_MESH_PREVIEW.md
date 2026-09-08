# Unreal Mesh Preview — Implementation Plan

Goal: let DualForge render Unreal `UStaticMesh` / `USkeletalMesh` assets from `.pak`
archives inside the existing 3D preview viewport (the same mesh page Unity uses),
reusing the `MeshView` / `SoftwareMeshView` pipeline.

Status: plan. The DualForge-side pieces (GLB reader, uex adapter `preview_mesh`,
bridge passthrough, `_preview_unreal` wiring) are implemented; the **uex CLI side
is not committed anywhere yet** and is required for end-to-end mesh preview.

## Background

- Unity meshes already flow: `_preview_unity_mesh` (dualforge/ui/preview.py)
  builds `payload["mesh"] = (verts, normals, tris, edges)`; `PreviewPanel`
  dispatches on `"mesh" in payload` -> `MeshPage.set_mesh` -> `MeshView`/
  `SoftwareMeshView`.
- Unreal has no mesh path today: `_preview_unreal` (dualforge/ui/preview.py)
  extracts raw bytes then only sniffs locres/image/audio/text.
- `UnrealBridge` (dualforge/unreal/bridge.py) exposes `list_files`/`extract`,
  routing to `UexAdapter` when the configured CLI is named `uex*`.
- `UexAdapter` (dualforge/unreal/uex_adapter.py) writes a throwaway
  `profiles.json`, probes the EGame via `doctor`, and runs uex as a subprocess.
- uex `export` writes packages as JSON / textures as PNG — no geometry.
- CUE4Parse supports mesh->GLB natively:
  `new MeshExporter(uStaticMesh, ELodFormat.FirstLod, exportMaterials:false,
  EMeshFormat.Gltf2)` emits `.glb` (skeletal meshes emit skinned GLB including
  the armature). A V2 API (`StaticMeshExporter`/`SkeletalMeshExporter` +
  `GltfMeshFormat`) also exists.

## uex CLI changes (external repo)

Add a `preview-mesh` command mirroring the existing `preview-texture`:

```
uex preview-mesh --profile dualforge --config cfg <vpath> --out out.glb
```

- `src/Uex/Core/AssetOps.cs`: load the package; find the first `UStaticMesh`/
  `USkeletalMesh`/`USkeleton` export; run `MeshExporter` with
  `ELodFormat.FirstLod`, `exportMaterials:false`, `EMeshFormat.Gltf2`; write the
  LOD's bytes to `--out`.
- If the package contains no mesh export, print `meshexport: none` and exit 0 so
  the app falls back to its normal sniff-based preview.
- On success print a one-line summary (e.g. `meshexport: staticmesh -> out.glb`)
  for the app to parse.
- Register the command in `Program.cs`, the `Serve/` JSON-lines protocol, and the
  `Mcp/` tool list (e.g. `preview_mesh`).
- Pure-logic unit tests in `Uex.Tests` (no paks required), mirroring existing tests.

## DualForge changes (implemented)

1. `dualforge/export/gltf_reader.py` — minimal GLB/glTF 2.0 reader:
   - parses the 12-byte GLB header, JSON chunk, and BIN chunk;
   - resolves buffer/`bufferView`/accessor binary slices (data-URI buffers
     supported too);
   - extracts POSITION / NORMAL / indices (SCALAR u16/u32) from every primitive
     of the first mesh, derives edges from triangles;
   - returns the same `(verts, normals, tris, edges)` tuple the Unity OBJ parser
     produces, so it feeds `MeshPage.set_mesh` unchanged.
2. `dualforge/unreal/uex_adapter.py` — `preview_mesh(pak, vpath, ...)`:
   - reuses `_game_for` + `_write_config`, runs `preview-mesh --out <tmp.glb>`,
     parses the summary line; returns `(bytes, kind)` on success, `None` when the
     CLI reports `none`.
3. `dualforge/unreal/bridge.py` — `preview_mesh(...)` passthrough (like
   `list_files`/`extract`); raises a clear `UnrealError` when the configured CLI
   is not a `uex*` binary (no mesh export there).
4. `dualforge/ui/preview.py` — `_preview_unreal` attempts `preview_mesh` before
   sniffing; on geometry, sets `payload["mesh"]` (kind stays a mesh payload);
   otherwise falls through to the existing image/audio/text sniffing. No UI
   change needed — `PreviewPanel` + `MeshPage` already handle `payload["mesh"]`.

## Tests

- `tests/test_gltf_reader.py`: build a minimal GLB in-memory (or via
  `write_gltf` -> GLB pack), parse it back, assert geometry round-trips.
- `tests/test_uex_adapter.py`: `preview_mesh` command construction + summary
  parsing with a stubbed `_run`.
- Preview-path test: faked bridge returns GLB bytes; `_preview_unreal` yields a
  `payload["mesh"]`.

End-to-end verification with a real mesh requires a game pak and a uex build that
carries `preview-mesh`.

## Risks / decisions

- Preview-only, best-effort scope; no `convert`/export-format wiring.
- uex must ship `preview-mesh`; until then DualForge degrades to current sniff
  preview.
- NaNite-only meshes (UE5.3+) may expose no normal LOD0 — the uex command should
  fall back to `ENaniteMeshFormat` nanite decode or report `none`.
- Skeletal meshes reference a `USkeleton` that may fail to load — tolerate and
  report `none`; `set_bones` overlay is a follow-up.
- `.umap` worlds can hold mesh exports too — out of initial scope; the single
  export-class finder naturally extends.