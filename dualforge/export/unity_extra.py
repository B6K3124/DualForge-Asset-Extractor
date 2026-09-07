"""Export helpers for Unity asset types UnityPy does not expose directly:

* ``Cubemap``        - decode each face to a PIL image (best effort; falls
                       back to face 0 for unusual texture formats).
* ``VideoClip``      - resolve the streamed resource bytes (external ``.resS``
                       or a ``StreamedResource`` inside another serialized file).
* ``SpriteAtlas``    - resolve ``m_PackedSprites`` to individual sprite images.
* ``AnimatorController`` / ``Avatar`` / ``LightmapData`` - human-readable JSON
  summaries so the whole object graph can be browsed/exported.

Everything here is defensive: a malformed or unreadable object degrades to a
clear UnityError instead of aborting an extraction run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:
    from UnityPy.enums import TextureFormat as _TF
except ImportError:  # pragma: no cover - UnityPy is a hard runtime dep
    _TF = None

# bytes per pixel for uncompressed texture formats
_BYTES_PER_PIXEL = {
    "Alpha8": 1, "ARGB4444": 2, "RGB24": 3, "RGBA32": 4, "ARGB32": 4,
    "ARGBFloat": 16, "RGB565": 2, "BGR24": 3, "R16": 2, "RGBA4444": 2,
    "BGRA32": 4, "RHalf": 2, "RGHalf": 4, "RGBAHalf": 8, "RFloat": 4,
    "RGFloat": 8, "RGBAFloat": 16, "RGB9e5Float": 4, "RGBFloat": 12,
    "RG16": 4, "R8": 1, "RG32": 8, "RGB48": 6, "RGBA64": 8,
    "R8_SIGNED": 1, "RG16_SIGNED": 4, "RGB24_SIGNED": 3, "RGBA32_SIGNED": 4,
    "R16_SIGNED": 2, "RG32_SIGNED": 8, "RGB48_SIGNED": 6, "RGBA64_SIGNED": 8,
}

# bytes per 4x4 block for block-compressed formats
_BYTES_PER_BLOCK = {
    "DXT1": 8, "DXT3": 16, "DXT5": 16, "DXT1Crunched": 8, "DXT5Crunched": 16,
    "BC4": 8, "BC5": 16, "BC6H": 16, "BC7": 16,
    "ETC_RGB4": 8, "ETC_RGB4_3DS": 8, "ETC2_RGB": 8, "ETC2_RGBA1": 8,
    "ETC2_RGBA8": 16, "ETC_RGB4Crunched": 8, "ETC2_RGBA8Crunched": 16,
    "ATC_RGB4": 8, "ATC_RGBA8": 16,
    "EAC_R": 8, "EAC_R_SIGNED": 8, "EAC_RG": 16, "EAC_RG_SIGNED": 16,
}


def _ceil(value: int, block: int) -> int:
    return (value + block - 1) // block


def _face_byte_size(width: int, height: int, texture_format: Any) -> Optional[int]:
    """Return the byte length of one Cubemap face's mip 0 surface, or None."""
    fmt_name = getattr(texture_format, "name", None) or str(texture_format)
    if fmt_name.startswith("PVR"):
        # PVRTC requires power-of-two square textures; sizes are awkward on
        # purpose so punt to the per-face-via-parse fallback when unscaled.
        return None
    if fmt_name.startswith("ASTC"):
        block = int(fmt_name.rsplit("_", 1)[1].split("x")[1]) if "_" in fmt_name else 4
        return _ceil(width, block) * _ceil(height, block) * 16
    if fmt_name in _BYTES_PER_PIXEL:
        return width * height * _BYTES_PER_PIXEL[fmt_name]
    if fmt_name in _BYTES_PER_BLOCK:
        return _ceil(width, 4) * _ceil(height, 4) * _BYTES_PER_BLOCK[fmt_name]
    return None


def cubemap_faces(cubemap: Any) -> List[Any]:
    """Decode every face of a Unity ``Cubemap`` to a PIL image.

    Faces are returned in the order Unity stores them (north, south, east,
    west, top, bottom for 6-face cubemaps).  The first face alone is returned
    for texture formats whose per-face size we cannot compute, keeping the
    export out of a hard failure.
    """
    try:
        from UnityPy.export.Texture2DConverter import parse_image_data
    except ImportError:
        raise ImportError("UnityPy is required for Cubemap export")

    data = bytes(getattr(cubemap, "get_image_data")() or b"")
    width = int(getattr(cubemap, "m_Width", 0) or 0)
    height = int(getattr(cubemap, "m_Height", 0) or 0)
    fmt = getattr(cubemap, "m_TextureFormat", None)
    count = int(getattr(cubemap, "m_ImageCount", 0) or 0)
    if width <= 0 or height <= 0 or not data:
        raise ValueError("cubemap has no readable dimensions or data")

    reader = getattr(cubemap, "object_reader", None)
    version = tuple(getattr(reader, "version", (0, 0, 0, 0)) or (0, 0, 0, 0))
    platform = getattr(reader, "platform", 0)
    blob = getattr(cubemap, "m_PlatformBlob", None)

    def decode(face_data: bytes) -> Any:
        return parse_image_data(
            face_data, width, height, fmt, version, platform, blob, True
        )

    face_size = _face_byte_size(width, height, fmt)
    if face_size is None or face_size <= 0 or face_size * max(count or 1, 1) < len(data) * 0.5:
        # Unknown layout (PVRTC etc.) -> decode the leading bytes as one image.
        return [decode(data)]
    faces: List[Any] = []
    for index in range(max(count, 1)):
        start = index * face_size
        if start + face_size > len(data):
            break
        try:
            faces.append(decode(data[start:start + face_size]))
        except Exception:
            continue
    return faces or [decode(data[:face_size])]


def _resource_path_bits(path: str) -> List[str]:
    """Normalise a Unity resource reference into candidate file names."""
    base = Path(str(path or "")).name
    bits: List[str] = []
    if base:
        bits.append(base)
        if ":" in base:
            bits.append(base.split(":")[-1])
    return [b for b in dict.fromkeys(bits) if b]


def video_clip_data(
    clip: Any,
    archive_path: str,
    environment: Any = None,
) -> Tuple[bytes, str]:
    """Resolve a ``VideoClip``'s raw video bytes + a suggested extension.

    Unity stores video as a ``StreamedResource`` (path + offset + size)
    either inside another serialized file in the same environment or in a
    sibling raw stream file next to the archive.  ``m_OriginalPath`` keeps the
    deck-link file name (e.g. ``clip.mp4``) so the extension survives export.
    """
    resource = getattr(clip, "m_ExternalResources", None)
    offset = int(getattr(resource, "m_Offset", 0) or 0)
    size = int(getattr(resource, "m_Size", 0) or 0)
    resource_path = str(getattr(resource, "m_Path", "") or "")
    original = str(getattr(clip, "m_OriginalPath", "") or "")
    suffix = Path(original).suffix.lower()
    if suffix not in (".mp4", ".mov", ".avi", ".webm", ".mkv", ".wmv"):
        suffix = Path(resource_path).suffix.lower() or ".mp4"

    candidates = _resource_path_bits(resource_path) + _resource_path_bits(original)

    def read_bytes(source: Any) -> bytes:
        if source is None:
            return b""
        raw = source.read() if hasattr(source, "read") and callable(source.read) else None
        if raw is None:
            try:
                raw = source.get_raw_data()
            except Exception:
                raw = None
        return bytes(raw or b"")

    # 1. Raw stream files inside the UnityPy environment (cab/container).
    if environment is not None:
        for key, file_obj in getattr(environment, "files", {}).items():
            name = str(getattr(file_obj, "name", "") or key)
            if not any(cand in name for cand in candidates):
                continue
            raw = read_bytes(file_obj)
            if raw and offset < len(raw):
                chunk = raw[offset:offset + size] if size else raw[offset:]
                if chunk:
                    return chunk, suffix
            if raw and offset == 0 and size == 0:
                return raw, suffix

    # 2. Sibling raw files on disk next to the archive.
    folder = Path(archive_path).parent
    for candidate in candidates:
        if not candidate:
            continue
        disk = folder / candidate
        if not disk.is_file():
            continue
        try:
            raw = disk.read_bytes()
        except OSError:
            continue
        chunk = raw[offset:offset + size] if size else raw[offset:]
        if chunk:
            return chunk, suffix

    # 3. Directly embedded payload.
    embedded = getattr(clip, "m_VideoData", None) or getattr(clip, "m_MovieData", None)
    if embedded:
        return bytes(embedded), suffix

    raise ValueError("video clip has no resolvable stream (missing .resS / resource file)")


def _iter_readers(scope: Any) -> Iterator[Any]:
    try:
        yield from scope.get_objects()
        return
    except (AttributeError, TypeError):
        pass
    try:
        yield from scope.objects
        return
    except (AttributeError, TypeError):
        pass
    return


def resolve_pptr(ptr: Any, assets_file: Any) -> Optional[Any]:
    """Return the readable object a PPtr points at (same assets file)."""
    if ptr is None or getattr(ptr, "path_id", None) is None:
        return None
    try:
        reader = assets_file.objects.get(ptr.path_id)
    except Exception:
        return None
    if reader is None:
        return None
    return reader


def sprite_atlas_entries(atlas: Any, assets_file: Any) -> List[Tuple[str, Any]]:
    """Resolve a ``SpriteAtlas`` to ``(name, PIL image)`` pairs."""
    packed = getattr(atlas, "m_PackedSprites", None) or []
    names = list(getattr(atlas, "m_PackedSpriteNamesToIndex", None) or [])
    entries: List[Tuple[str, Any]] = []
    for index, ptr in enumerate(packed):
        reader = resolve_pptr(ptr, assets_file)
        if reader is None:
            continue
        try:
            sprite = reader.read()
        except Exception:
            continue
        image = getattr(sprite, "image", None)
        if image is None:
            try:
                from UnityPy.export import SpriteHelper

                image = SpriteHelper.get_image(sprite)
            except Exception:
                image = None
        if image is None:
            continue
        name = ""
        if index < len(names) and names[index]:
            name = str(names[index])
        else:
            name = str(getattr(sprite, "m_Name", "") or "")
        entries.append((name or f"Sprite_{index:04d}", image))
    return entries


def sprite_name(ptr: Any, assets_file: Any) -> str:
    reader = resolve_pptr(ptr, assets_file)
    if reader is None:
        return ""
    try:
        return str(getattr(reader.read(), "m_Name", "") or "")
    except Exception:
        return ""


def animator_summary(controller: Any, assets_file: Any) -> Dict[str, Any]:
    """Dump an ``AnimatorController`` (clips, params, bone map) as JSON-able data."""
    clips: List[Dict[str, str]] = []
    for ptr in getattr(controller, "m_AnimationClips", None) or []:
        name = sprite_name(ptr, assets_file)
        clips.append({
            "name": name or f"path_id_{getattr(ptr, 'path_id', None)}",
            "path_id": getattr(ptr, "path_id", None),
            "file_id": getattr(ptr, "file_id", None),
        })

    tos = getattr(controller, "m_TOS", None) or []
    state_names: List[str] = []
    try:
        machine = getattr(getattr(controller, "m_Controller", None), "m_StateMachine", None)
        states = getattr(machine, "m_States", None) or []
        for child in states:
            state = getattr(child, "m_State", None)
            if state is not None:
                name = getattr(state, "m_Name", "") or ""
                if name:
                    state_names.append(str(name))
    except Exception:
        pass

    params: List[str] = []
    try:
        controller_const = getattr(controller, "m_Controller", None)
        for param in getattr(controller_const, "m_AnimatorParameters", None) or []:
            name = getattr(param, "m_Name", "") or ""
            param_type = getattr(param, "m_Type", None)
            params.append(str(name) + (f":{param_type}" if param_type is not None else ""))
    except Exception:
        pass

    return {
        "name": str(getattr(controller, "m_Name", "") or ""),
        "animation_clips": clips,
        "bone_tree": [str(name) for _, name in (tos or [])],
        "parameters": params,
        "states": state_names,
    }


def avatar_summary(avatar: Any) -> Dict[str, Any]:
    """Dump an ``Avatar`` (human config + skeleton names) as JSON-able data."""
    description = getattr(avatar, "m_HumanDescription", None) or getattr(avatar, "m_AvatarDescription", None)
    human_bones: List[str] = []
    if description is not None:
        for bone in getattr(description, "m_Human", None) or []:
            name = getattr(bone, "m_BoneName", "") or ""
            limit = getattr(bone, "m_HumanName", "") or ""
            if name:
                human_bones.append(str(name) + (f" ({limit})" if limit else ""))
    skeleton: List[str] = []
    for bone in getattr(getattr(avatar, "m_AvatarSkeleton", None), "m_Node", None) or []:
        name = getattr(bone, "m_Name", "") or ""
        if name:
            skeleton.append(str(name))
    root_bone = str(getattr(avatar, "m_RootMotionBoneName", "") or "")
    return {
        "name": str(getattr(avatar, "m_Name", "") or ""),
        "root_motion_bone": root_bone,
        "human_bones": human_bones,
        "skeleton": skeleton,
    }


def lightmap_summary(lightmap: Any, assets_file: Any) -> Dict[str, Any]:
    """Dump a ``LightmapData`` (referenced light/dir/shadow-mask textures)."""
    out: Dict[str, Any] = {
        "name": str(getattr(lightmap, "m_Name", "") or ""),
    }
    for key, attr in (
        ("light", "m_Light"),
        ("dir", "m_Dir"),
        ("shadow_mask", "m_ShadowMask"),
        ("light_color", "m_LightColor"),
        ("light_dir", "m_LightDir"),
    ):
        ptr = getattr(lightmap, attr, None)
        if ptr is None:
            continue
        name = sprite_name(ptr, assets_file)
        out[key] = {
            "name": name or f"path_id_{getattr(ptr, 'path_id', None)}",
            "path_id": getattr(ptr, "path_id", None),
            "file_id": getattr(ptr, "file_id", None),
        }
    return out


def referenced_texture_images(obj: Any, assets_file: Any) -> Iterator[Tuple[str, Any]]:
    """Yield ``(name, PIL image)`` for every direct texture PPtr on ``obj``."""
    seen = set()
    for attr in dir(obj):
        if not attr.startswith("m_"):
            continue
        try:
            value = getattr(obj, attr)
        except Exception:
            continue
        if value is None or not hasattr(value, "path_id"):
            continue
        reader = resolve_pptr(value, assets_file)
        if reader is None:
            continue
        path_id = getattr(value, "path_id", None)
        if path_id in seen:
            continue
        try:
            target = reader.read()
        except Exception:
            continue
        if getattr(target, "type", None) is not None and target.type.name not in {"Texture2D", "Cubemap"}:
            continue
        image = getattr(target, "image", None)
        if image is None:
            continue
        seen.add(path_id)
        name = str(getattr(target, "m_Name", "") or f"Texture_{path_id}")
        yield name, image


__all__ = [
    "animator_summary",
    "avatar_summary",
    "cubemap_faces",
    "lightmap_summary",
    "referenced_texture_images",
    "resolve_pptr",
    "sprite_atlas_entries",
    "video_clip_data",
]