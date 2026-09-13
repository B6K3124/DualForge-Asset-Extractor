"""Dependency-free ASCII USD (``.usda``/``.usd``) world export.

The generated layer is plain UTF-8 text in the Pixar Universal Scene
Description ASCII dialect. ``.usd`` supports both ASCII and "crate"
(binary) content, and the USD Asset Resolver autodetects the encoding, so
a single text layer written to a ``.usd`` file is valid, as is an explicit
``.usda``.

A "world" is a root Xform containing one nested Xform per source asset.
Each asset optionally carries a ``Mesh`` with per-vertex UVs bound to a
UsdPreviewSurface material; texture pixels are written out as PNG files
next to the layer and referenced with an ``inputs:file`` asset path.
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence

_DEFAULT_SCENE = "World"


def _fmt_vec(values: Sequence, digits: int = 4) -> str:
    if not values:
        return "()"
    return "(" + ", ".join(f"{float(v):.{digits}f}" for v in values) + ")"


def _point_list(vertices: Sequence[Sequence[float]]) -> str:
    return "[" + ", ".join(_fmt_vec(v) for v in vertices) + "]"


def _prim_name(name: object) -> str:
    clean = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(name))
    if not clean:
        clean = "Mesh"
    if clean[0].isdigit():
        clean = "M_" + clean
    return clean


def write_usd_world(
    path: str,
    meshes: Sequence[dict[str, object]],
    textures: Sequence[dict[str, object]] | None = None,
    scene_name: str = _DEFAULT_SCENE,
    up_axis: str = "Y",
    meters_per_unit: float = 1.0,
) -> str:
    """Write a combined ASCII USD stage made of the given meshes.

    ``meshes`` is a sequence of dicts::

        {"name": str,
         "vertices": [(x, y, z), ...],
         "triangles": [(i, j, k), ...],
         "uvs": [(u, v), ...],         # optional
         "normals": [(x, y, z), ...],  # optional
         "texture_file": str}          # optional asset path to bind

    ``textures`` is a sequence of ``{"name": str, "pixels": bytes}`` PNG
    payloads written to ``textures/`` beside the layer. Any mesh whose
    ``name`` matches a texture's ``name`` gets the matching material authored
    (via ``textures/<name>.png``); a mesh with an explicit ``texture_file``
    always binds that path instead.

    Returns the path written.
    """
    meshes = [m for m in meshes if m.get("vertices") and m.get("triangles")]
    if not meshes:
        raise ValueError("no decodable meshes to export to USD")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    texture_files: dict[str, str] = {}
    if textures:
        texture_dir = out.parent / "textures"
        texture_dir.mkdir(parents=True, exist_ok=True)
        for texture in textures:
            blob = texture.get("pixels")
            if not blob:
                continue
            file_name = f"{_prim_name(texture.get('name', 'tex'))}.png"
            with open(texture_dir / file_name, "wb") as fh:
                fh.write(blob)
            texture_files[_prim_name(texture.get("name", "tex"))] = f"textures/{file_name}"

    axis = "Y" if str(up_axis).upper() == "Y" else "Z"
    lines: list[str] = ["#usda 1.0", "(", f"    metersPerUnit = {meters_per_unit:g}", f'    upAxis = "{axis}"', ")", ""]
    scene = _prim_name(scene_name)
    lines.append(f'def Xform "{scene}" (')
    lines.append('    kind = "assembly"')
    lines.append(")")
    lines.append("{")

    used_assets = set()

    def _asset_xform() -> str:
        idx = 1
        while True:
            name = f"Asset_{idx}"
            if name not in used_assets:
                used_assets.add(name)
                return name
            idx += 1

    for mesh in meshes:
        xform = _asset_xform()
        mesh_name = _prim_name(mesh.get("name"))
        texture_file = _texture_file_for(mesh, texture_files)
        lines.append(f'    def Xform "{xform}" (')
        lines.append('        kind = "component"')
        lines.append("    )")
        lines.append("    {")
        lines.append(f'        def Mesh "{mesh_name}"')
        lines.append("        {")
        _write_mesh_body(lines, mesh)
        if texture_file:
            lines.append(f'            rel material:binding = </{scene}/{xform}/Material_{mesh_name}>')
        lines.append("        }")
        if texture_file:
            _write_material_template(lines, scene, xform, mesh_name, texture_file)
        lines.append("    }")
        lines.append("")

    lines.append("}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(out)


def _texture_file_for(mesh: dict[str, object], texture_files: dict[str, str]) -> str | None:
    explicit = mesh.get("texture_file")
    if explicit:
        return str(explicit)
    key = _prim_name(mesh.get("name"))
    if key in texture_files:
        return texture_files[key]
    return None


def _write_mesh_body(lines: list[str], mesh: dict[str, object]) -> None:
    normals: list = list(mesh.get("normals") or [])
    uvs: list = list(mesh.get("uvs") or [])
    triangles: list = list(mesh["triangles"])
    vertices: list = list(mesh["vertices"])
    if normals:
        lines.append(f"            float3[] normals = {_point_list(normals)} (")
        lines.append('                interpolation = "vertex"')
        lines.append("            )")
    lines.append(f"            float3[] points = {_point_list(vertices)}")
    lines.append("            int[] faceVertexCounts = [" + ", ".join("3" for _ in triangles) + "]")
    indices = ", ".join(str(int(idx)) for tri in triangles for idx in tri)
    lines.append(f"            int[] faceVertexIndices = [{indices}]")
    if uvs:
        lines.append(f"            float2[] primvars:st = {_point_list(uvs)} (")
        lines.append('                interpolation = "vertex"')
        lines.append("            )")


def _write_material_template(lines: list[str], scene: str, xform: str, mesh_name: str, texture_file: str) -> None:
    material = f"{scene}/{xform}/Material_{mesh_name}"
    surface = f"{material}/surfaceShader.outputs:surface"
    diffuse = f"{material}/diffuseTexture.outputs:rgb"
    st_target = f"{scene}/{xform}/{mesh_name}"
    lines.append(f'        def Material "Material_{mesh_name}"')
    lines.append("        {")
    lines.append(f"            token outputs:surface.connect = </{surface}>")
    lines.append('            def Shader "surfaceShader"')
    lines.append("            {")
    lines.append('                uniform token info:id = "UsdPreviewSurface"')
    lines.append(f"                color3f inputs:diffuseColor.connect = </{diffuse}>")
    lines.append("                float inputs:metallic = 0")
    lines.append("                float inputs:roughness = 1")
    lines.append("            }")
    lines.append('            def Shader "diffuseTexture"')
    lines.append("            {")
    lines.append('                uniform token info:id = "UsdUVTexture"')
    lines.append(f"                asset inputs:file = @{texture_file}@")
    lines.append(f"                float2 inputs:st.connect = </{st_target}.primvars:st>")
    lines.append("                token outputs:rgb")
    lines.append("            }")
    lines.append("        }")


def write_usd_mesh(
    path: str,
    name: str,
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    uvs: Sequence[Sequence[float]] | None = None,
) -> str:
    return write_usd_world(
        path,
        [{"name": name, "vertices": vertices, "triangles": triangles, "uvs": uvs or []}],
    )


__all__ = [
    "write_usd_mesh",
    "write_usd_world",
]