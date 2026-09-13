from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from dualforge.export.texture import image_to_dds, image_to_ktx, load_image
from dualforge.unity import UnityError
from dualforge.unity.repack import replace_font, replace_text_asset, replace_texture, save_archive


class _FakeEnv:
    def __init__(self):
        self.saved = None

    def save(self, **kwargs):
        self.saved = kwargs


class _FakeArchive:
    def __init__(self, env=None):
        self.env = env or _FakeEnv()


def _rgba_image(size=(4, 4)):
    image = Image.new("RGBA", size, (200, 30, 40, 255))
    image.putpixel((0, 0), (10, 20, 30, 255))
    return image


def test_dds_magic_and_header():
    dds = image_to_dds(_rgba_image())
    assert dds[:4] == b"DDS "
    assert struct.unpack("<I", dds[4:8])[0] == 124
    width, height = struct.unpack("<II", dds[12:20])
    assert (width, height) == (4, 4)


def test_ktx_magic():
    ktx = image_to_ktx(_rgba_image())
    assert ktx[1:4] == b"KTX"
    # endianness marker
    assert struct.unpack("<I", ktx[12:16])[0] == 0x04030201


def test_dds_ktx_mips_larger():
    base = len(image_to_ktx(_rgba_image()))
    with_mips = len(image_to_ktx(_rgba_image(), mips=2))
    assert with_mips > base


def test_load_image_rgba(tmp_path: Path):
    source = tmp_path / "tex.png"
    _rgba_image().save(source)
    loaded = load_image(str(source))
    assert loaded.mode == "RGBA"
    assert loaded.size == (4, 4)


class _FakeType:
    name = "Texture2D"


class _FakeObj:
    type = _FakeType()
    m_Width = 4
    m_Height = 4
    m_FontData = None
    m_Script = ""

    def set_image(self, image, target_format=None, mipmap_count=1):
        self.image_was_set = (image.size, target_format, mipmap_count)


class _FakeReader:
    def __init__(self, obj):
        self._obj = obj
        self.assets_file = _FakeAssetsFile()

    def read(self):
        return self._obj


class _FakeAssetsFile:
    def __init__(self):
        self.changed = False

    def mark_changed(self):
        self.changed = True


class _FakeAsset:
    def __init__(self, obj):
        self._reader = _FakeReader(obj)


def test_save_archive_delegates(tmp_path: Path):
    env = _FakeEnv()
    save_archive(_FakeArchive(env), str(tmp_path / "out"), "none")
    assert env.saved == {"pack": "none", "out_path": str(tmp_path / "out")}
    assert (tmp_path / "out").name == "out"


def test_save_archive_rejects_unknown_pack():
    with pytest.raises(UnityError):
        save_archive(_FakeArchive(), "out", pack="bogus")


def test_save_archive_refuses_source_directory(tmp_path: Path):
    source = tmp_path / "game.bundle"
    source.write_bytes(b"fake")
    env = _FakeEnv()
    archive = NS(env=env, path=str(source))
    with pytest.raises(UnityError):
        save_archive(archive, str(tmp_path), "none")  # same folder as source


def test_save_archive_verifies_written_output(tmp_path: Path):
    source = tmp_path / "game.bundle"
    source.write_bytes(b"fake")
    out = tmp_path / "repacked"

    class WritingEnv:
        saved = False

        def save(self, **kwargs):
            self.saved = True
            out.mkdir(parents=True, exist_ok=True)
            (out / source.name).write_bytes(b"repacked data")

    env = WritingEnv()
    archive = NS(env=env, path=str(source))
    save_archive(archive, str(out), "none")
    assert env.saved
    assert (out / source.name).read_bytes() == b"repacked data"


def test_save_archive_raises_when_nothing_written(tmp_path: Path):
    source = tmp_path / "game.bundle"
    source.write_bytes(b"fake")
    archive = NS(env=_FakeEnv(), path=str(source))
    with pytest.raises(UnityError):
        save_archive(archive, str(tmp_path / "empty_out"), "none")


def test_load_image_from_dds_and_ktx(tmp_path: Path):
    source_dds = tmp_path / "tex.dds"
    source_ktx = tmp_path / "tex.ktx"
    image = _rgba_image()
    source_dds.write_bytes(image_to_dds(image))
    source_ktx.write_bytes(image_to_ktx(image))

    dds_loaded = load_image(str(source_dds))
    assert dds_loaded.size == (4, 4)
    ktx_loaded = load_image(str(source_ktx))
    assert ktx_loaded.size == (4, 4)
    assert dds_loaded.tobytes() == ktx_loaded.tobytes()


def test_replace_texture(tmp_path: Path):
    obj = _FakeObj()
    asset = _FakeAsset(obj)
    source = tmp_path / "new.png"
    _rgba_image().save(source)
    size = replace_texture(_FakeArchive(), asset, str(source))
    assert size == "4x4"
    assert obj.image_was_set[0] == (4, 4)
    assert asset._reader.assets_file.changed


def test_replace_texture_wrong_type_raises():
    obj = _FakeObj()
    obj.type.name = "Mesh"
    with pytest.raises(UnityError):
        replace_texture(_FakeArchive(), _FakeAsset(obj), "x.png")


def test_replace_text_asset():
    obj = _FakeObj()
    obj.type.name = "TextAsset"
    asset = _FakeAsset(obj)
    replace_text_asset(_FakeArchive(), asset, b"hello world")
    assert obj.m_Script == "hello world"
    assert asset._reader.assets_file.changed


def test_replace_font(tmp_path: Path):
    obj = _FakeObj()
    obj.type.name = "Font"
    asset = _FakeAsset(obj)
    font = tmp_path / "font.ttf"
    font.write_bytes(b"\x00\x01\x00\x00" + b"fakedata")
    replace_font(_FakeArchive(), asset, str(font))
    assert bytes(obj.m_FontData)[:4] == b"\x00\x01\x00\x00"


def test_replace_font_empty_raises(tmp_path: Path):
    obj = _FakeObj()
    obj.type.name = "Font"
    font = tmp_path / "empty.ttf"
    font.write_bytes(b"")
    with pytest.raises(UnityError):
        replace_font(_FakeArchive(), _FakeAsset(obj), str(font))