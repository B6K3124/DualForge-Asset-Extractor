from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from dualforge.export.usd import write_usd_mesh, write_usd_world


def _cube_mesh():
    return {
        "name": "MyCube",
        "vertices": [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
        "triangles": [(0, 1, 2), (0, 2, 3)],
        "uvs": [(0, 0), (1, 0), (1, 1), (0, 1)],
    }


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_write_usd_mesh_single(tmp_path: Path):
    target = tmp_path / "out.usd"
    written = write_usd_mesh(str(target), "Cube", [(0, 0, 0), (1, 0, 0)], [(0, 1, 2)])
    assert Path(written).exists()
    text = target.read_text()
    assert text.startswith("#usda 1.0")
    assert 'upAxis = "Y"' in text
    assert 'def Xform "World"' in text
    assert 'def Mesh "Cube"' in text
    assert "float3[] points" in text
    assert "faceVertexIndices" in text
    # braces balance
    assert text.count("{") == text.count("}")


def test_write_usd_world_two_meshes(tmp_path: Path):
    other = {**{"name": "Other", "vertices": [(0, 0, 0)], "triangles": [(0, 0, 0)]}}
    other["triangles"] = [(0, 0, 0)]
    path = write_usd_world(
        str(tmp_path / "world.usda"),
        [_cube_mesh(), other],
    )
    text = Path(path).read_text()
    assert text.count('def Mesh "') == 2
    assert 'def Xform "Asset_1"' in text
    assert 'def Xform "Asset_2"' in text


def test_write_usd_texture(tmp_path: Path):
    path = write_usd_world(
        str(tmp_path / "ambient.usd"),
        [_cube_mesh()],
        textures=[{"name": "MyCube", "pixels": _png_bytes()}],
    )
    text = Path(path).read_text()
    assert "/World/Asset_1/Material_MyCube" in text
    assert "@textures/MyCube.png@" in text
    assert (tmp_path / "textures" / "MyCube.png").read_bytes()[:4] == b"\x89PNG"
    assert "UsdPreviewSurface" in text
    assert "UsdUVTexture" in text


def test_write_usd_explicit_texture_file(tmp_path: Path):
    path = write_usd_world(
        str(tmp_path / "custom.usd"),
        [{**_cube_mesh(), "texture_file": "skins/cube.png"}],
    )
    text = Path(path).read_text()
    assert "@skins/cube.png@" in text


def test_write_usd_no_meshes_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        write_usd_world(str(tmp_path / "empty.usda"), [])


def test_write_usd_connection_paths_rooted(tmp_path: Path):
    path = write_usd_world(
        str(tmp_path / "rooted.usd"),
        [_cube_mesh()],
        textures=[{"name": "MyCube", "pixels": _png_bytes()}],
    )
    text = Path(path).read_text()
    assert "</World/Asset_1/Material_MyCube/surfaceShader.outputs:surface>" in text
    assert "</World/Asset_1/Material_MyCube/diffuseTexture.outputs:rgb>" in text
    assert "</World/Asset_1/MyCube.primvars:st>" in text
    # no unrooted references
    assert "</Asset_1/" not in text