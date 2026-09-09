from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dualforge.ui import preview_helpers as helpers
from dualforge.ui.preview_helpers import (
    cache_key,
    format_bytes,
    format_hex_lines,
    guess_text,
    make_hex,
    parse_obj,
    pil_to_qimage,
    read_cached,
    wav_peaks,
    write_cached,
)
from dualforge.ui.settings import Settings


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _sine_wav(path: str, seconds: float = 0.1, rate: int = 8000) -> int:
    frames = int(rate * seconds)
    t = np.linspace(0, seconds, frames, endpoint=False)
    samples = (np.sin(2 * np.pi * 440 * t) * 32767 * 0.5).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.tobytes())
    return frames


def test_pil_to_qimage():
    from PIL import Image

    image = Image.new("RGBA", (4, 3), (255, 0, 0, 128))
    qimage = pil_to_qimage(image)
    assert qimage.width() == 4
    assert qimage.height() == 3
    assert qimage.pixelColor(1, 1).red() == 255
    assert qimage.pixelColor(1, 1).alpha() == 128


def test_mesh_page_exports_glb(tmp_path, qapp, monkeypatch):
    import numpy as np

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from dualforge.ui.preview import MeshPage

    page = MeshPage()
    verts = np.zeros((3, 3), dtype=np.float32)
    tris = np.array([[0, 1, 2]], dtype=np.int32)
    glb = b"glTF" + b"x" * 12
    page.set_mesh((verts, np.zeros_like(verts), tris, []), glb=glb, name="hero.glb")
    assert not page.export_btn.isHidden()
    assert page._glb == glb
    target = tmp_path / "hero.glb"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "GLB (*.glb)")),
    )
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    page._on_export_glb()
    assert target.read_bytes() == glb


def test_sniff_image_gpu_textures():
    from PIL import Image

    from dualforge.export.texture import image_to_dds, image_to_ktx
    from dualforge.ui.preview_helpers import sniff_image

    image = Image.new("RGBA", (4, 4), (12, 34, 56, 255))
    for blob in (image_to_dds(image), image_to_ktx(image)):
        qimage = sniff_image(blob)
        assert qimage is not None
        assert (qimage.width(), qimage.height()) == (4, 4)
        assert ((qimage.pixel(0, 0) >> 16) & 0xFF) == 12  # red channel


def test_sniff_image_ktx2():
    import struct

    from dualforge.ui.preview_helpers import sniff_image

    header = bytearray(80)
    header[0:12] = b"\xABKTX 20\xBB\r\n\x1A\n"
    struct.pack_into("<I", header, 12, 37)  # R8G8B8A8_UNORM
    struct.pack_into("<I", header, 16, 1)
    struct.pack_into("<I", header, 20, 4)
    struct.pack_into("<I", header, 24, 4)
    struct.pack_into("<I", header, 36, 1)
    struct.pack_into("<I", header, 40, 1)
    struct.pack_into("<I", header, 44, 0)
    payload = bytes([200, 100, 50, 255]) * 16
    blob = bytes(header) + struct.pack("<II", 88, len(payload)) + payload
    qimage = sniff_image(blob)
    assert qimage is not None and qimage.width() == 4


def test_format_bytes():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(1048576) == "1.0 MB"
    assert format_bytes(1073741824) == "1.0 GB"


def test_guess_text():
    assert guess_text(b"hello world\nsecond line")
    assert guess_text(b"{\"a\": 1}")
    assert not guess_text(b"\x00\x01\x02\xff\xfe")
    assert not guess_text(b"")


def test_format_hex_lines():
    data = b"\x00\x01\x41hello"
    lines = format_hex_lines(data)
    assert lines[0].startswith("00000000")
    assert "00 01 41" in lines[0]
    assert "hello" in lines[0]
    assert "." in lines[0].split("  ")[-1]


def test_make_hex_truncates():
    data = b"A" * (helpers.MAX_PREVIEW_BYTES + 100)
    preview = make_hex(data)
    assert "omitted" in preview
    assert len(preview.splitlines()) <= (helpers.MAX_PREVIEW_BYTES // 16) + 2


def test_wav_peaks(tmp_path, qapp):
    wav_path = str(tmp_path / "tone.wav")
    _sine_wav(wav_path)
    peaks, duration, rate, channels = wav_peaks(wav_path, bins=64)
    assert peaks.shape == (64, 2)
    assert duration == pytest.approx(0.1, abs=0.001)
    assert rate == 8000
    assert channels == 1
    assert peaks[:, 0].min() <= 0.0
    assert peaks[:, 1].max() > 0.5


def test_parse_obj():
    obj = b"""
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
f 1 2 3 4
"""
    parsed = parse_obj(obj)
    assert parsed is not None
    verts, normals, tris, edges = parsed
    assert verts.shape == (4, 3)
    assert tris.shape == (2, 3)
    assert len(edges) == 4
    assert normals.shape == (4, 3)


def test_parse_obj_invalid():
    assert parse_obj(b"not an obj file at all") is None
    assert parse_obj(b"") is None


def test_cache_roundtrip(tmp_path):
    key = cache_key("C:/games/Fortnite.pak", 123456)
    path = write_cached(str(tmp_path), key, "file.bin", b"payload")
    assert Path(path).exists()
    assert read_cached(str(tmp_path), key, "file.bin") == b"payload"
    assert read_cached(str(tmp_path), key, "missing.bin") is None


def test_theme_applies(qapp):
    from dualforge.ui.theme import apply_theme, available_themes

    assert set(available_themes()) == {"dark", "light"}
    apply_theme(qapp, "dark")
    assert qapp.styleSheet()
    apply_theme(qapp, "light")
    assert qapp.styleSheet()


def test_settings_roundtrip(tmp_path):
    path = str(tmp_path / "settings.json")
    settings = Settings()
    settings.theme = "light"
    settings.default_out_dir = "C:/out"
    settings.add_recent("a.pak")
    settings.add_recent("b.pak")
    settings._path = path
    settings.save()
    loaded = Settings.load(path)
    assert loaded.theme == "light"
    assert loaded.default_out_dir == "C:/out"
    assert loaded.recent_files == ["b.pak", "a.pak"]
    assert loaded.cache_dir() == settings.cache_dir()
