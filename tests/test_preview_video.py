"""Tests for the video preview path (sniffing + tree kind tagging)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dualforge.ui import preview_helpers as helpers
from dualforge.ui.archive_loaders import bethesda_kind, entry_kind
from dualforge.ui.preview_readers import _sniff_resource, _sniff_video


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_sniff_video_writes_mp4(tmp_path):
    payload = b"\x00\x00\x00\x18ftypmp42" + b"x" * 16
    path = _sniff_video("cutscene.mp4", payload, str(tmp_path), "key1")
    assert path is not None
    assert Path(path).read_bytes() == payload
    assert path.endswith("cutscene.mp4")


def test_sniff_video_ignores_other_suffixes(tmp_path):
    assert _sniff_video("notes.txt", b"hello", str(tmp_path), "key1") is None
    assert _sniff_video("clip.mpg", b"", str(tmp_path), "key1") is None


def test_sniff_resource_attaches_video_payload(tmp_path):
    payload = b"\x00\x00\x00\x18ftypmp42" + b"v" * 16
    data: dict = {"meta": {}}
    _sniff_resource(data, "intro.mp4", payload, str(tmp_path), "key2")
    assert data["video_path"].endswith("intro.mp4")
    assert data["meta"]["Decoded"] == "video"
    assert data["meta"]["Format"] == "MP4"
    assert "raw" not in data


def test_sniff_resource_skips_video_for_text(tmp_path):
    data: dict = {"meta": {}}
    _sniff_resource(data, "readme.txt", b"hello world", str(tmp_path), "key3")
    assert "video_path" not in data
    assert data["text"] == "hello world"


def test_sniff_video_uses_helpers_cache_key(tmp_path):
    key = helpers.cache_key("a.mp4", 42)
    assert key
    path = _sniff_video("a.mp4", b"data" * 4, str(tmp_path), key)
    assert Path(path).exists()


def test_entry_kind_video_suffixes():
    assert entry_kind("movies/cut_intro.mp4") == "video"
    assert entry_kind("clips.s.webm") == "video"
    assert entry_kind("audio/theme.wav") == "audio"
    assert entry_kind("todo.txt") == "text"
    assert entry_kind("data.xyz") == "file"
    assert entry_kind("data.xyz", default="misc") == "misc"


def test_bethesda_kind_delegates_video():
    assert bethesda_kind("video/seg1.mp4") == "video"
    assert bethesda_kind("fx.hkx") == "anim"
    assert bethesda_kind("raw") == "file"


def test_video_page_constructs(qapp):
    from dualforge.ui.preview import VideoPage

    page = VideoPage()
    assert page.video is not None
    assert page.play_button.text() == "Play"
    page.set_video(str(Path(__file__).parent / "nonexistent.mp4"))
    assert page.position.isEnabled()
    page.clear()
    assert not page.position.isEnabled()