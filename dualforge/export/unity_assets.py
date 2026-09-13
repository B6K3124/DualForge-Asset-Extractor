"""Helpers for extracting structured JSON and specialized exports from Unity
objects via UnityPy's type tree. Used by previews, exports and the CLI.

These work on the same objects the rest of DualForge handles (Texture2D,
MonoBehaviour, Shader, Font, ...) and fall back gracefully when a type tree
is unavailable (e.g. stripped or obfuscated bundles).
"""

from __future__ import annotations

import json
from typing import Any

from UnityPy.files import ObjectReader

from dualforge.log import get_logger

logger = get_logger(__name__)


def json_default(value: Any) -> Any:
    if hasattr(value, "read"):
        return str(getattr(value, "read", lambda: value)())
    return str(value)


def typetree_dict(asset) -> dict[str, Any] | None:
    """Return the full typed object tree of a Unity asset as a JSON-able dict.

    Returns None when the object does not carry a readable type tree
    (some shaders or fully stripped objects).
    """
    reader = getattr(asset, "_reader", None)
    if reader is None:
        return None
    try:
        return reader.read_typetree()
    except Exception:
        logger.debug("type tree unavailable for %s", getattr(asset, "path", "object"), exc_info=True)
        return None


def typetree_json(asset, indent: int = 2) -> str | None:
    tree = typetree_dict(asset)
    if tree is None:
        return None
    try:
        return json.dumps(tree, indent=indent, default=json_default)
    except (TypeError, ValueError):
        return str(tree)


def monobehaviour_json(asset, indent: int = 2) -> str | None:
    """Serialize a MonoBehaviour to JSON using its type tree."""
    return typetree_json(asset, indent=indent)


def shader_to_text(asset) -> str:
    """Best-effort readable shader source.

    UnityShader uses nested containers internally; surface the shader byte
    blocks as readable text where the type tree carries them, otherwise fall
    back to a JSON dump of the shader properties.
    """
    reader = getattr(asset, "_reader", None)
    if reader is None:
        return ""
    try:
        obj = reader.read()
    except Exception:
        logger.debug("shader object could not be read; using type tree", exc_info=True)
        obj = None
    name = ""
    if obj is not None:
        name = str(getattr(obj, "m_ParsedForm", "") or "") or str(getattr(obj, "m_Name", "") or "")
    source = _shader_source_text(obj)
    if source:
        return source
    tree = typetree_json(asset)
    if tree is not None:
        header = f"// {name}".strip() if name else ""
        return f"{header}\n{tree}".strip()
    return ""


def _shader_source_text(obj) -> str:
    if obj is None:
        return ""
    parsed_form = getattr(obj, "m_ParsedForm", None)
    if parsed_form is not None:
        # Unity 2019+ parsed shader: collect sub-shader/pass/stage source blocks.
        sections = []
        for attribute in ("m_SubShaders", "m_ShaderKeyword", "m_Fallback", "m_PropInfo"):
            value = getattr(parsed_form, attribute, None)
            if value is not None:
                sections.append(str(value))
        if sections:
            return "\n".join(sections)
    m_Binding = getattr(obj, "m_Binding", None)
    if m_Binding is not None:
        return str(m_Binding)
    return ""


def font_data(asset) -> bytes | None:
    """Extract the raw font bytes (TTF/OTF) from a Unity Font asset."""
    try:
        obj = asset._reader.read()
    except Exception:
        logger.debug("font asset could not be read", exc_info=True)
        return None
    data = getattr(obj, "m_FontData", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    data = getattr(obj, "m_FontData2", None)
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return None


def object_reader(asset) -> ObjectReader | None:
    return getattr(asset, "_reader", None)


__all__ = [
    "font_data",
    "json_default",
    "monobehaviour_json",
    "object_reader",
    "shader_to_text",
    "typetree_dict",
    "typetree_json",
]