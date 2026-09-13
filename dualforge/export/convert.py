from __future__ import annotations

import json
from pathlib import Path

from dualforge.export.gltf import write_gltf
import contextlib

TEXTURE_FORMATS = ("png", "jpg", "bmp", "webp", "tga", "dds", "ktx")
AUDIO_FORMATS = ("wav", "ogg", "flac", "raw")
MESH_FORMATS = ("obj", "gltf", "fbx", "usd", "usda")
TEXT_FORMATS = ("txt",)
RAW_FORMATS = ("bin",)
OBJECT_FORMATS = ("json",)
SHADER_FORMATS = ("shader", "txt")
FONT_FORMATS = ("ttf", "otf", "bin")
CUBEMAP_FORMATS = ("png", "jpg", "bmp", "webp", "tga", "dds", "ktx")
VIDEO_FORMATS = ("source", "mp4", "mov", "webm", "avi", "bin", "json")

ANIMATION_FORMATS = ("gltf", "fbx", "json")

DEFAULT_FORMATS = {
    "Texture2D": "png",
    "Sprite": "png",
    "AudioClip": "wav",
    "Mesh": "fbx",
    "TextAsset": "txt",
    "MonoBehaviour": "json",
    "Material": "json",
    "Shader": "shader",
    "Font": "ttf",
    "AnimationClip": "fbx",
    "Cubemap": "png",
    "VideoClip": "source",
    "MovieTexture": "source",
    "SpriteAtlas": "png",
    "AnimatorController": "json",
    "Avatar": "json",
    "LightmapData": "json",
}


def normalize_format(fmt: str | None) -> str:
    return (fmt or "png").lower().lstrip(".")


def format_choices(type_name: str) -> tuple[str, ...]:
    if type_name in {"Texture2D", "Sprite"}:
        return TEXTURE_FORMATS
    if type_name == "AudioClip":
        return AUDIO_FORMATS
    if type_name == "Mesh":
        return MESH_FORMATS
    if type_name == "TextAsset":
        return TEXT_FORMATS
    if type_name in {"MonoBehaviour", "Material"}:
        return OBJECT_FORMATS
    if type_name == "Shader":
        return SHADER_FORMATS
    if type_name == "Font":
        return FONT_FORMATS
    if type_name == "AnimationClip":
        return ANIMATION_FORMATS
    if type_name == "Cubemap":
        return CUBEMAP_FORMATS
    if type_name in {"VideoClip", "MovieTexture"}:
        return VIDEO_FORMATS
    if type_name == "SpriteAtlas":
        return TEXTURE_FORMATS
    if type_name in {"AnimatorController", "Avatar", "LightmapData"}:
        return OBJECT_FORMATS
    return RAW_FORMATS


def save_texture(image, path_stem: Path, fmt: str) -> str:
    fmt = normalize_format(fmt)
    if fmt == "jpg":
        target = path_stem.with_suffix(".jpg")
        image.convert("RGB").save(target, quality=95)
        return str(target)
    if fmt in {"dds", "ktx"}:
        from dualforge.export.texture import image_to_dds, image_to_ktx

        target = path_stem.with_suffix(f".{fmt}")
        data = image_to_dds(image) if fmt == "dds" else image_to_ktx(image)
        with open(target, "wb") as fh:
            fh.write(data)
        return str(target)
    target = path_stem.with_suffix(f".{fmt}")
    image.save(target)
    return str(target)


def save_mesh(name: str, obj_data: bytes, path_stem: Path, fmt: str) -> str:
    fmt = normalize_format(fmt)
    if fmt in {"usd", "usda"}:
        from dualforge.export.usd import write_usd_world

        verts, tris, uvs = _parse_obj_with_uv(obj_data)
        if not verts or not tris:
            raise ValueError("mesh has no decodable geometry for USD export")
        target = path_stem.with_suffix(f".{fmt}")
        write_usd_world(
            str(target),
            [{"name": name or path_stem.stem, "vertices": verts, "triangles": tris, "uvs": uvs}],
        )
        return str(target)
    if fmt == "gltf":
        verts, tris, uvs = _parse_obj_with_uv(obj_data)
        if not verts or not tris:
            raise ValueError("mesh has no decodable geometry for glTF export")
        target = path_stem.with_suffix(".gltf")
        write_gltf(str(target), verts, tris, uvs=uvs, name=name or path_stem.stem)
        return str(target)
    target = path_stem.with_suffix(".obj")
    with open(target, "wb") as fh:
        fh.write(obj_data)
    return str(target)


def save_text(data: bytes, path_stem: Path, fmt: str = "txt") -> str:
    fmt = normalize_format(fmt)
    target = path_stem.with_suffix(f".{fmt}")
    text = data.decode("utf-8", "replace")
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return str(target)


def save_json(obj, path_stem: Path, fmt: str = "json") -> str:
    from dualforge.export.unity_assets import json_default

    fmt = normalize_format(fmt)
    target = path_stem.with_suffix(f".{fmt}")
    text = json.dumps(obj, indent=2, default=json_default)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return str(target)


def save_shader(asset, path_stem: Path, fmt: str = "shader") -> str:
    from dualforge.export.unity_assets import shader_to_text

    fmt = normalize_format(fmt)
    target = path_stem.with_suffix(f".{fmt}")
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(shader_to_text(asset))
    return str(target)


def save_font(asset, path_stem: Path, fmt: str = "ttf") -> str:
    from dualforge.export.unity_assets import font_data

    fmt = normalize_format(fmt)
    data = font_data(asset)
    if not data:
        raise ValueError("font has no extractable TTF/OTF data")
    target = path_stem.with_suffix(f".{fmt}")
    with open(target, "wb") as fh:
        fh.write(data)
    return str(target)


def _parse_obj_with_uv(data: bytes):
    positions: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces_raw: list[list[str]] = []
    for line in data.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            try:
                positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif parts[0] == "vt" and len(parts) >= 3:
            try:
                uvs.append((float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        elif parts[0] == "f" and len(parts) >= 4:
            faces_raw.append(parts[1:])
    if not positions or not faces_raw:
        return [], [], []
    merge_map: dict = {}
    merged: list[tuple[int, int]] = []
    tris: list[tuple[int, int, int]] = []
    for face in faces_raw:
        corners: list[int] = []
        for part in face:
            segments = part.split("/")
            try:
                pos_index = int(segments[0]) - 1
            except ValueError:
                continue
            uv_index = -1
            if len(segments) > 1 and segments[1]:
                with contextlib.suppress(ValueError):
                    uv_index = int(segments[1]) - 1
            key = (pos_index, uv_index)
            index = merge_map.get(key)
            if index is None:
                index = len(merged)
                merge_map[key] = index
                merged.append(key)
            corners.append(index)
        for i in range(1, len(corners) - 1):
            tris.append((corners[0], corners[i], corners[i + 1]))
    if not tris:
        return [], [], []
    verts = [positions[pos_index] for pos_index, _ in merged]
    uv_out = [uvs[uv_index] if uv_index >= 0 and uv_index < len(uvs) else (0.0, 0.0) for _, uv_index in merged]
    return verts, tris, uv_out


__all__ = [
    "AUDIO_FORMATS",
    "ANIMATION_FORMATS",
    "CUBEMAP_FORMATS",
    "DEFAULT_FORMATS",
    "FONT_FORMATS",
    "MESH_FORMATS",
    "OBJECT_FORMATS",
    "RAW_FORMATS",
    "SHADER_FORMATS",
    "TEXT_FORMATS",
    "TEXTURE_FORMATS",
    "VIDEO_FORMATS",
    "format_choices",
    "normalize_format",
    "save_font",
    "save_json",
    "save_mesh",
    "save_shader",
    "save_text",
    "save_texture",
]