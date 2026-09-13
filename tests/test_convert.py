from __future__ import annotations

import json
from pathlib import Path

import pytest

from dualforge.export.convert import (
    DEFAULT_FORMATS,
    format_choices,
    normalize_format,
    save_mesh,
    save_text,
    save_texture,
)


def test_normalize_format():
    assert normalize_format(None) == "png"
    assert normalize_format(".JPG") == "jpg"
    assert normalize_format("WAV") == "wav"


def test_format_choices():
    assert "png" in format_choices("Texture2D")
    assert "tga" in format_choices("Sprite")
    assert format_choices("AudioClip") == ("wav", "ogg", "flac", "raw")
    assert "gltf" in format_choices("Mesh")
    assert format_choices("MonoBehaviour") == ("json",)
    assert "json" in format_choices("Material")
    assert "shader" in format_choices("Shader")
    assert "ttf" in format_choices("Font")


def test_default_formats():
    assert DEFAULT_FORMATS["Texture2D"] == "png"
    assert DEFAULT_FORMATS["AudioClip"] == "wav"
    assert DEFAULT_FORMATS["Mesh"] == "fbx"
    assert DEFAULT_FORMATS["AnimationClip"] == "fbx"
    assert DEFAULT_FORMATS["MonoBehaviour"] == "json"
    assert DEFAULT_FORMATS["Shader"] == "shader"
    assert DEFAULT_FORMATS["Font"] == "ttf"
    assert DEFAULT_FORMATS["Cubemap"] == "png"
    assert DEFAULT_FORMATS["VideoClip"] == "source"
    assert DEFAULT_FORMATS["AnimatorController"] == "json"
    assert DEFAULT_FORMATS["Avatar"] == "json"


def test_save_texture_png_jpg(tmp_path: Path):
    from PIL import Image

    image = Image.new("RGBA", (8, 8), (200, 30, 40, 255))
    png = save_texture(image, tmp_path / "tex", "png")
    assert Path(png).suffix == ".png"
    assert Image.open(png).size == (8, 8)
    jpg = save_texture(image, tmp_path / "tex", "jpg")
    assert Path(jpg).suffix == ".jpg"
    assert Image.open(jpg).mode == "RGB"


def test_save_texture_dds_ktx(tmp_path: Path):
    from PIL import Image

    image = Image.new("RGBA", (8, 8), (200, 30, 40, 255))
    dds = save_texture(image, tmp_path / "tex", "dds")
    assert Path(dds).read_bytes()[:4] == b"DDS "
    ktx = save_texture(image, tmp_path / "tex", "ktx")
    assert Path(ktx).read_bytes()[1:4] == b"KTX"


def test_save_mesh_obj_and_gltf(tmp_path: Path):
    obj = b"v 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nf 1/1 2/2 3/3\n"
    obj_target = save_mesh("quad", obj, tmp_path / "mesh", "obj")
    assert Path(obj_target).suffix == ".obj"
    assert Path(obj_target).read_bytes() == obj

    gltf_target = save_mesh("quad", obj, tmp_path / "mesh", "gltf")
    assert Path(gltf_target).suffix == ".gltf"
    doc = json.loads(Path(gltf_target).read_text(encoding="utf-8"))
    assert doc["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]


def test_save_mesh_gltf_invalid_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        save_mesh("bad", b"not an obj", tmp_path / "mesh", "gltf")


def test_save_mesh_usd(tmp_path: Path):
    obj = b"v 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nf 1/1 2/2 3/3\n"
    usda_target = save_mesh("quad", obj, tmp_path / "mesh", "usda")
    assert Path(usda_target).suffix == ".usda"
    text = Path(usda_target).read_text(encoding="utf-8")
    assert text.startswith("#usda 1.0")
    assert 'def Mesh "quad"' in text

    usd_target = save_mesh("quad", obj, tmp_path / "mesh2", "usd")
    assert Path(usd_target).suffix == ".usd"
    assert Path(usd_target).read_text(encoding="utf-8").startswith("#usda 1.0")


def test_save_text(tmp_path: Path):
    target = save_text(b"hello\n", tmp_path / "note", "txt")
    assert Path(target).read_text(encoding="utf-8") == "hello\n"


def test_mesh_to_obj_with_uvs():
    from dualforge.unity.archive import _mesh_to_obj

    obj = _mesh_to_obj(
        "quad",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
        [[(0, 1, 2)]],
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
    ).decode("utf-8")
    assert obj.startswith("o quad")
    assert "v 0 0 0" in obj
    assert "vt 1.0 0.0" in obj
    assert "f 1/1 2/2 3/3" in obj


def test_mesh_to_obj_without_uvs():
    from dualforge.unity.archive import _mesh_to_obj

    obj = _mesh_to_obj("tri", [(0, 0, 0), (1, 0, 0), (0, 1, 0)], [[(0, 1, 2)]], []).decode("utf-8")
    assert "vt" not in obj
    assert "f 1 2 3" in obj


def test_mesh_to_obj_multi_component_uvs():
    from dualforge.unity.archive import _mesh_to_obj

    obj = _mesh_to_obj(
        "m",
        [(0, 0, 0), (1, 0, 0), (1, 1, 0)],
        [[(0, 1, 2)]],
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)],
    ).decode("utf-8")
    assert "vt 0.0 0.0" in obj
    assert "vt 1.0 1.0" in obj