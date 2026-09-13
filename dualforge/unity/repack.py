"""In-place write-back ("modding") support for extracted Unity assets.

Given a loaded ``UnityArchive`` (which wraps a UnityPy environment) and a
changed asset, these helpers mutate the corresponding Unity object via
UnityPy's `set_image` / field assignment and mark its serialized file changed
so ``archive.save(out_dir, pack)`` can write the modified bundle back.

IMPORTANT: Write-back rewrites the original archive. Always export to a fresh
output directory; never overwrite the source file in place.
"""

from __future__ import annotations

from pathlib import Path

from dualforge.export.texture import load_image
from dualforge.unity import UnityError

_BACKEND_PACKS = ("none", "lz4", "lz4hc")


def replace_texture(
    archive,
    asset,
    image_path: str,
    target_format: int | None = None,
    mipmap_count: int = 1,
) -> str:
    """Replace a Texture2D's pixels with the decoded ``image_path``."""
    if mipmap_count < 1:
        raise UnityError("mipmap_count must be >= 1")
    obj = asset._reader.read()
    if getattr(obj, "type", None) is None:
        raise UnityError("not a readable Unity object")
    if obj.type.name != "Texture2D":
        raise UnityError(f"expected a Texture2D, got {obj.type.name}")
    image = _as_rgba(load_image(image_path))
    try:
        obj.set_image(image, target_format=target_format, mipmap_count=mipmap_count)
    except AttributeError as exc:
        raise UnityError("this Unity object does not support in-place image replacement") from exc
    except Exception as exc:
        raise UnityError(f"texture replacement failed: {exc}") from exc
    _mark_changed(asset)
    size = f"{obj.m_Width}x{obj.m_Height}" if hasattr(obj, "m_Width") else "?"
    return size


def replace_text_asset(archive, asset, data: bytes) -> int:
    """Replace a TextAsset's script payload with ``data`` (UTF-8)."""
    obj = asset._reader.read()
    if getattr(obj, "type", None) is None or obj.type.name != "TextAsset":
        raise UnityError("asset is not a TextAsset")
    obj.m_Script = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    _mark_changed(asset)
    return len(data)


def replace_font(archive, asset, font_path: str) -> int:
    """Replace a Font's embedded TTF/OTF bytes from ``font_path``."""
    obj = asset._reader.read()
    if getattr(obj, "type", None) is None or obj.type.name != "Font":
        raise UnityError("asset is not a Font")
    with open(font_path, "rb") as fh:
        data = bytearray(fh.read())
    if not data:
        raise UnityError("empty font file")
    if hasattr(obj, "m_FontData"):
        obj.m_FontData = data
    elif hasattr(obj, "m_FontData2"):
        obj.m_FontData2 = data
    else:
        raise UnityError("font object exposes no writable font-data field")
    _mark_changed(asset)
    return len(data)


def save_archive(archive, out_dir: str, pack: str = "none") -> None:
    """Persist any changed objects to ``out_dir`` (never the source path)."""
    env = getattr(archive, "env", None)
    if env is None:
        raise UnityError("archive has no writable environment")
    pack = (pack or "none").lower()
    if pack not in _BACKEND_PACKS:
        raise UnityError(
            f"unknown pack backend {pack!r}; expected one of {', '.join(_BACKEND_PACKS)}"
        )
    out_dir = Path(out_dir)
    source = Path(getattr(archive, "path", "") or "")
    if source.name:
        try:
            if out_dir.resolve() == source.resolve().parent:
                raise UnityError(
                    f"refusing to write the repacked archive next to the source ({out_dir}); "
                    "pick a different output directory"
                )
        except OSError:
            pass  # unresolvable paths fall through to the backend error handling
    try:
        env.save(pack=pack, out_path=str(out_dir))
    except Exception as exc:
        raise UnityError(f"could not save modified archive: {exc}") from exc
    if not source.name:
        return  # synthetic/mock archives do not produce on-disk files
    written = [p for p in out_dir.rglob("*") if p.is_file() and p.stat().st_size > 0]
    if not written:
        name = source.name
        if (out_dir / name).is_file() and (out_dir / name).stat().st_size > 0:
            return
        raise UnityError(f"no non-empty output written by the backend to {out_dir}")


def _mark_changed(asset) -> None:
    reader = getattr(asset, "_reader", None)
    if reader is None:
        return
    assets_file = getattr(reader, "assets_file", None)
    if assets_file is not None and hasattr(assets_file, "mark_changed"):
        assets_file.mark_changed()


def _as_rgba(image):
    return image.convert("RGBA") if image.mode != "RGBA" else image


__all__ = [
    "replace_font",
    "replace_text_asset",
    "replace_texture",
    "save_archive",
]