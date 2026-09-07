from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from dualforge.export.unity_extra import (
    _face_byte_size,
    animator_summary,
    avatar_summary,
    cubemap_faces,
    lightmap_summary,
    sprite_atlas_entries,
    video_clip_data,
)

try:
    from UnityPy.enums import TextureFormat as TextureFormat
except ImportError:  # pragma: no cover
    TextureFormat = None


def test_face_byte_size_uncompressed_and_blocks():
    assert _face_byte_size(4, 4, TextureFormat.RGBA32) == 64
    assert _face_byte_size(4, 4, TextureFormat.RGB24) == 48
    assert _face_byte_size(4, 4, TextureFormat.DXT5) == 16
    assert _face_byte_size(8, 8, TextureFormat.BC7) == 16 * 4
    assert _face_byte_size(8, 8, TextureFormat.ASTC_RGBA_4x4) == 2 * 2 * 16
    assert _face_byte_size(8, 8, TextureFormat.PVRTC_RGBA4) is None


def test_cubemap_faces_six_rgba32():
    from UnityPy.enums import TextureFormat as UPY_TF

    w, h = 4, 4
    face_size = w * h * 4
    colors = [(v,) * 4 for v in (10, 60, 110, 160, 210, 255)]
    payload = b"".join(bytes(color) * (face_size // 4) for color in colors)
    cubemap = NS(
        m_Width=w,
        m_Height=h,
        m_TextureFormat=UPY_TF.RGBA32,
        m_ImageCount=6,
        object_reader=NS(version=(2022, 3, 0, 0), platform=4),
        m_PlatformBlob=None,
    )
    cubemap.get_image_data = lambda: payload

    faces = cubemap_faces(cubemap)
    assert len(faces) == 6
    assert all(face.size == (w, h) for face in faces)
    pixels = {face.getpixel((0, 0))[0] for face in faces}
    assert len(pixels) == 6


def test_cubemap_faces_fallback_on_unknown_format():
    from UnityPy.enums import TextureFormat as UPY_TF

    w, h = 2, 2
    payload = bytes([9, 9, 9, 9]) * (w * h)
    cubemap = NS(
        m_Width=w,
        m_Height=h,
        m_TextureFormat=UPY_TF.RGBA32,
        m_ImageCount=1,
        object_reader=NS(version=(2022, 3, 0, 0), platform=4),
        m_PlatformBlob=None,
    )
    cubemap.get_image_data = lambda: payload
    faces = cubemap_faces(cubemap)
    assert len(faces) >= 1
    assert faces[0].size == (w, h)


def test_video_clip_data_from_sibling_file(tmp_path: Path):
    blob = b"\x00\x00\x00\x1cmp4-mock-data"
    movie = tmp_path / "runner.mp4"
    movie.write_bytes(blob)
    clip = NS(m_ExternalResources=NS(m_Path="", m_Offset=0, m_Size=0), m_OriginalPath="Runner/runner.mp4")
    data, suffix = video_clip_data(clip, str(tmp_path / "unused.assets"))
    assert data == blob
    assert suffix == ".mp4"


def test_video_clip_data_offset_and_size(tmp_path: Path):
    raw = b"HEADER" + b".resS" * 8
    res = tmp_path / "movie.resS"
    res.write_bytes(raw)
    clip = NS(m_ExternalResources=NS(m_Path="movie.resS", m_Offset=6, m_Size=5), m_OriginalPath="movie.mp4")
    data, suffix = video_clip_data(clip, str(tmp_path / "unused.assets"))
    assert data == b".resS"
    assert suffix == ".mp4"


def test_video_clip_data_unresolvable_raises(tmp_path: Path):
    clip = NS(m_ExternalResources=NS(m_Path="missing", m_Offset=0, m_Size=0), m_OriginalPath="x.avi")
    with pytest.raises(ValueError):
        video_clip_data(clip, str(tmp_path / "unused.assets"))


def test_sprite_atlas_entries(tmp_path: Path):
    from PIL import Image

    def reader(name, color):
        r = NS(
            path_id=hash(name),
            type=NS(name="Sprite"),
        )
        image = Image.new("RGBA", (2, 2), color)
        r.read = lambda: NS(m_Name=name, image=image)
        return r

    atlas = NS(
        m_PackedSprites=[NS(path_id=7, file_id=0), NS(path_id=9, file_id=0)],
        m_PackedSpriteNamesToIndex=["face.png", "armor.png"],
    )
    assets_file = NS(objects={7: reader("face", (1, 2, 3, 255)), 9: reader("armor", (4, 5, 6, 255))})
    entries = sprite_atlas_entries(atlas, assets_file)
    assert [name for name, _ in entries] == ["face.png", "armor.png"]
    assert entries[0][1].size == (2, 2)


def test_animator_summary_clips_and_states():
    controller = NS(
        m_Name="Human",
        m_AnimationClips=[NS(path_id=5, file_id=0)],
        m_TOS=[(1, "Hips"), (2, "Spine")],
        m_Controller=NS(
            m_StateMachine=NS(m_States=[NS(m_State=NS(m_Name="Idle"))]),
            m_AnimatorParameters=[NS(m_Name="Speed", m_Type=1)],
        ),
    )
    assets_file = NS(objects={5: NS(path_id=5, type=NS(name="AnimationClip"), read=lambda: NS(m_Name="run"))})
    summary = animator_summary(controller, assets_file)
    assert summary["animation_clips"][0]["name"] == "run"
    assert "Idle" in summary["states"]
    assert "Speed:1" in summary["parameters"]
    assert "Hips" in summary["bone_tree"]


def test_avatar_and_lightmap_summary():
    avatar = NS(
        m_Name="hero",
        m_RootMotionBoneName="Hips",
        m_HumanDescription=NS(m_Human=[NS(m_BoneName="Spine1", m_HumanName="Chest")]),
        m_AvatarSkeleton=NS(m_Node=[NS(m_Name="Hips"), NS(m_Name="Spine")]),
    )
    summary = avatar_summary(avatar)
    assert summary["root_motion_bone"] == "Hips"
    assert summary["human_bones"][0].startswith("Spine1")

    lightmap = NS(m_Name="lm0", m_Light=NS(path_id=3, file_id=0), m_Dir=NS(path_id=4, file_id=0))
    assets_file = NS(
        objects={
            3: NS(path_id=3, type=NS(name="Texture2D"), read=lambda: NS(m_Name="baked_light")),
            4: NS(path_id=4, type=NS(name="Texture2D"), read=lambda: NS(m_Name="baked_dir")),
        }
    )
    summary = lightmap_summary(lightmap, assets_file)
    assert summary["light"]["name"] == "baked_light"
    assert summary["dir"]["path_id"] == 4