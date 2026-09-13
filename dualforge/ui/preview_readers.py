"""Background file-preview readers for the UI.

Decodes asset payloads (Unity objects, Unreal/disk files, REDengine and
Bethesda entries) off the GUI thread. Each :class:`PreviewWorker` emits
``loaded`` with a dict payload (image/audio/mesh/text/hex/meta) that the
:class:`~dualforge.ui.preview.PreviewPanel` renders, or ``failed``.
"""

from __future__ import annotations

import json
import tempfile
import xml.dom.minidom
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from dualforge.log import get_logger
from dualforge.ui import preview_helpers as helpers

logger = get_logger(__name__)

IMAGE_TYPES = {"Texture2D", "Sprite"}
AUDIO_TYPES = {"AudioClip"}
MESH_TYPES = {"Mesh"}
TEXT_TYPES = {"TextAsset"}
OBJECT_TYPES = {"MonoBehaviour", "Material"}
SHADER_TYPES = {"Shader"}
FONT_TYPES = {"Font"}
ANIMATION_TYPES = {"AnimationClip"}


@dataclass
class PreviewItem:
    title: str
    engine: str
    kind: str
    size: int
    asset: object = None
    entry: str | None = None
    aes_key: str | None = None
    archive_path: str = ""
    native_archive: object = None
    usmap: str | None = None
    scheme: str | None = None
    egame: str | None = None
    meta: dict[str, str] = field(default_factory=dict)

    def identity(self) -> tuple:
        return (self.engine, self.kind, self.title, self.size)


class PreviewSignals(QObject):
    loaded = Signal(dict)
    failed = Signal(str, str)


class PreviewWorker(QThread):
    def __init__(self, item: PreviewItem, cache_dir: str, parent=None):
        super().__init__(parent)
        self.item = item
        self.cache_dir = cache_dir
        self._signals = PreviewSignals()
        self.loaded = self._signals.loaded
        self.failed = self._signals.failed
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def run(self) -> None:
        if self._cancelled or self.isInterruptionRequested():
            return
        try:
            if self.item.engine == "unity":
                payload = self._preview_unity()
            elif self.item.engine == "file":
                payload = self._preview_file()
            elif self.item.engine == "cdpr":
                payload = self._preview_cdpr()
            elif self.item.engine == "bethesda":
                payload = self._preview_bethesda()
            else:
                payload = self._preview_unreal()
        except Exception as exc:
            if self._cancelled or self.isInterruptionRequested():
                return
            self.failed.emit(self.item.title, str(exc))
        else:
            if self._cancelled or self.isInterruptionRequested():
                return
            self.loaded.emit(payload)

    def _base_payload(self) -> dict:
        return {
            "title": self.item.title,
            "size": self.item.size,
            "meta": dict(self.item.meta),
        }

    def _preview_unity(self) -> dict:
        payload = self._base_payload()
        asset = self.item.asset
        try:
            obj = asset._reader.read()
        except Exception as exc:
            obj = self._read_typetree_fallback(asset)
            if obj is None:
                raise ValueError(f"could not decode this Unity {asset.type_name} object: {exc}") from exc
        type_name = getattr(obj, "type", None)
        if type_name is None:
            reader_type = getattr(asset._reader, "type", None)
            type_name = reader_type.name if reader_type is not None else asset.type_name
        type_name = getattr(type_name, "name", type_name)
        payload["kind"] = type_name
        tree = _typetree(payload, asset)
        if type_name in IMAGE_TYPES:
            image = getattr(obj, "image", None)
            if image is None:
                raise ValueError("texture has no decodable image data")
            payload["image"] = helpers.pil_to_qimage(image)
            payload["meta"].update(
                {
                    "Width": str(image.width),
                    "Height": str(image.height),
                    "Format": image.mode,
                }
            )
            payload["meta"].update(_extra_unity_meta(obj, type_name))
        elif type_name in AUDIO_TYPES:
            from UnityPy.helpers import AudioClipConverter

            wav = AudioClipConverter.export_wav(obj)
            key = helpers.cache_key(self.item.archive_path, asset.byte_size)
            wav_path = helpers.write_cached(self.cache_dir, key, f"{helpers.cache_key(asset.path, asset.byte_size)}.wav", wav)
            peaks, duration, rate, channels = helpers.wav_peaks(wav_path)
            payload["audio_path"] = wav_path
            payload["peaks"] = peaks
            payload["duration"] = duration
            payload["sample_rate"] = rate
            payload["meta"].update(
                {
                    "Sample rate": f"{rate} Hz",
                    "Duration": f"{duration:.2f} s",
                    "Channels": str(channels),
                }
            )
        elif type_name in MESH_TYPES:
            from UnityPy.export import MeshExporter

            obj_data = MeshExporter.export_mesh_obj(obj)
            name = str(getattr(obj, "m_Name", "") or "").strip()
            parsed = helpers.parse_obj(obj_data.encode("utf-8"))
            if parsed is None:
                raise ValueError("mesh has no decodable geometry")
            payload["mesh"] = parsed
            bones = _preview_bones(asset, obj)
            if bones:
                payload["bones"] = bones
            payload["meta"].update(
                {
                    "Vertices": str(len(parsed[0])),
                    "Triangles": str(len(parsed[2])),
                    "Object": name or asset.path,
                }
            )
            if bones:
                payload["meta"]["Bones"] = str(len(bones))
        elif type_name in ANIMATION_TYPES:
            from dualforge.export.unity_skin import animation_tracks, clip_summary

            summary = clip_summary(obj)
            tracks = animation_tracks(obj)
            text_lines = [f"// {summary['Position curves']} position / {summary['Rotation curves']} rotation / {summary['Scale curves']} scale curves"]
            text_lines.append(f"// {summary['Keyframes']} keyframes @ {summary['Sample rate']} Hz")
            for node, node_data in tracks.items():
                text_lines.append(f"{node}: {', '.join(sorted(node_data))}")
            payload["text"] = "\n".join(text_lines)
            payload["meta"].update(summary)
            payload["meta"]["Object"] = str(getattr(obj, "m_Name", "") or asset.path)
        elif type_name in TEXT_TYPES:
            data = obj.m_Script
            if isinstance(data, str):
                data = data.encode("utf-8")
            payload["text"] = _pretty_text(data)
            payload["meta"]["Decoded"] = "yes"
        elif type_name in OBJECT_TYPES:
            from dualforge.export.unity_assets import monobehaviour_json

            text = monobehaviour_json(asset) or _typetree_text(tree)
            payload["text"] = text
            payload["meta"].update(
                {
                    "Decoded": "yes (type tree)",
                    "Fields": _count_fields(tree),
                }
            )
        elif type_name in SHADER_TYPES:
            from dualforge.export.unity_assets import shader_to_text

            payload["text"] = shader_to_text(asset) or _typetree_text(tree)
            payload["meta"].update(
                {
                    "Decoded": "yes (shader source)",
                    "Fields": _count_fields(tree),
                }
            )
        elif type_name in FONT_TYPES:
            payload["meta"].update(_extra_unity_meta(obj, type_name))
            from dualforge.export.unity_assets import font_data

            try:
                font_bytes = font_data(asset)
            except Exception:
                logger.debug("font bytes unavailable for %s", asset.type_name, exc_info=True)
                font_bytes = None
            if font_bytes:
                is_ttf = font_bytes[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")
                payload["meta"]["Font bytes"] = f"{len(font_bytes):,}"
                payload["meta"]["Font format"] = "TTF/OTF" if is_ttf else "embedded"
                payload["font"] = font_bytes
            payload["meta"]["Decoded"] = "font"
        elif type_name in {"AnimationClip"}:
            payload["meta"].update(_extra_unity_meta(obj, type_name))
            payload["meta"]["Decoded"] = "clip summary"
        else:
            try:
                raw = obj.raw_data
            except AttributeError:
                raw = asset._reader.get_raw_data()
            payload["raw"] = raw
            if helpers.guess_text(raw):
                payload["text"] = _pretty_text(raw)
                payload["meta"]["Decoded"] = "yes (utf-8)"
            else:
                payload["meta"]["Decoded"] = "no"
        return payload

    def _read_typetree_fallback(self, asset):
        """For new engine formats UnityPy cannot fully decode, read the
        type tree and surface it as structured JSON text."""
        from types import SimpleNamespace

        try:
            tree = asset._reader.read_typetree()
        except Exception:
            logger.debug("type tree unavailable for %s", getattr(asset, "type_name", "object"), exc_info=True)
            return None
        try:
            import json as _json

            text = _json.dumps(tree, indent=2, default=str)
        except Exception:
            logger.debug("type tree could not be serialized as JSON", exc_info=True)
            text = str(tree)
        return SimpleNamespace(
            type=SimpleNamespace(name=asset.type_name),
            raw_data=text.encode("utf-8"),
        )

    def _preview_file(self) -> dict:
        """Preview driver for a file already extracted to disk."""
        payload = self._base_payload()
        payload["kind"] = "file"
        path = Path(self.item.entry or self.item.title)
        if not path.is_file():
            raise ValueError(f"file not found: {path}")
        data = path.read_bytes()
        payload["raw"] = data
        key = helpers.cache_key(str(path), len(data))
        _sniff_resource(payload, path.name, data, self.cache_dir, key)
        return payload

    def _preview_unreal(self) -> dict:
        payload = self._base_payload()
        payload["kind"] = "file"
        key = helpers.cache_key(str(self.item.entry or ""), self.item.size)
        filename = Path(self.item.entry or self.item.title).name or "file.bin"
        mesh_result = self._try_unreal_mesh()
        if mesh_result is not None:
            geometry, glb_bytes = mesh_result
            payload["mesh"] = geometry
            payload["glb"] = glb_bytes
            payload["glb_name"] = f"{Path(filename).stem}.glb"
            return payload
        cached = None
        if self.item.native_archive is not None:
            cached = helpers.read_cached(self.cache_dir, key, filename)
            if cached is None:
                try:
                    raw = self.item.native_archive.read_file(self.item.entry or self.item.title)
                except Exception as exc:
                    raise ValueError(f"pak read failed: {exc}") from exc
                helpers.write_cached(self.cache_dir, key, filename, raw)
                cached = raw
        if cached is None:
            from dualforge.unreal import UnrealBridge

            bridge = UnrealBridge()
            if not bridge.available():
                raise ValueError(
                    "no CUE4Parse-based CLI configured - set DUALFORGE_CUE4PARSE "
                    "to preview Unreal files"
                )
            cached = helpers.read_cached(self.cache_dir, key, filename)
            if cached is None:
                with tempfile.TemporaryDirectory(prefix="dualforge_preview_") as temp_dir:
                    try:
                        _usmap = self.item.usmap
                        if not _usmap or not Path(_usmap).is_file():
                            from dualforge.unreal.uex_adapter import find_usmap

                            paks_dir = str(Path(self.item.archive_path).parent)
                            _usmap = find_usmap(paks_dir)
                        bridge.extract(
                            self.item.archive_path,
                            temp_dir,
                            aes_key=self.item.aes_key,
                            files=[self.item.entry or self.item.title],
                            usmap=_usmap,
                            scheme=self.item.scheme,
                            egame=self.item.egame,
                        )
                    except Exception as exc:
                        raise ValueError(f"preview extract failed: {exc}") from exc
                    found = list(Path(temp_dir).rglob("*"))
                    candidate = next((p for p in found if p.is_file()), None)
                    if candidate is None:
                        raise ValueError("no file was extracted for preview")
                    cached = candidate.read_bytes()
                    helpers.write_cached(self.cache_dir, key, filename, cached)
        payload["raw"] = cached
        _sniff_resource(payload, filename, cached, self.cache_dir, key)
        return payload

    def _try_unreal_mesh(self):
        """Best-effort mesh preview for an Unreal package via the uex CLI.

        Returns ``(geometry, glb_bytes)`` when the package holds an
        exportable mesh (``glb_bytes`` lets the mesh page offer a direct GLB
        export to disk), or ``None`` when no CLI/geometry is available
        (callers fall back to generic sniffing). Only Unreal *package* entries
        (.uasset/.umap and friends) are attempted - audio, textures and other
        media are already handled by the sniffing path, so we avoid spawning a
        uex subprocess for them.
        """
        from dualforge.unreal import UnrealBridge

        entry = self.item.entry or self.item.title
        if not self.item.archive_path or not entry:
            return None
        if not _looks_like_unreal_package(entry):
            return None
        try:
            bridge = UnrealBridge()
            if not bridge.available():
                return None
        except Exception:
            logger.warning("Unreal bridge could not be initialised; mesh preview disabled", exc_info=True)
            return None
        usmap = None
        try:
            from dualforge.unreal.uex_adapter import find_usmap

            usmap = self.item.usmap
            if not usmap or not Path(usmap).is_file():
                usmap = find_usmap(str(Path(self.item.archive_path).parent))
        except Exception:
            logger.debug("usmap lookup failed; continuing without mappings", exc_info=True)
            usmap = None
        try:
            result = bridge.preview_mesh(
                self.item.archive_path,
                entry,
                aes_key=self.item.aes_key,
                usmap=usmap,
                scheme=self.item.scheme,
                egame=self.item.egame,
            )
        except Exception:
            logger.warning("Unreal mesh preview failed for %s", entry, exc_info=True)
            return None
        if not result:
            return None
        glb_bytes, _kind = result
        if not glb_bytes:
            return None
        try:
            from dualforge.export.gltf_reader import parse_glb

            geometry = parse_glb(glb_bytes)
            if geometry is None:
                return None
            return geometry, glb_bytes
        except Exception:
            logger.warning("glTF parse of Unreal preview failed for %s", entry, exc_info=True)
            return None

    def _preview_cdpr(self) -> dict:
        payload = self._base_payload()
        payload["kind"] = "file"
        archive = self.item.native_archive
        entry_name = self.item.entry or self.item.title
        if archive is None:
            raise ValueError("no REDengine archive loaded for preview")
        key = helpers.cache_key(str(entry_name), self.item.size)
        filename = Path(entry_name).name or "file.bin"
        cached = helpers.read_cached(self.cache_dir, key, filename)
        if cached is None:
            try:
                raw = archive.open_file(entry_name)
            except Exception as exc:
                raise ValueError(f"REDengine read failed: {exc}") from exc
            helpers.write_cached(self.cache_dir, key, filename, raw)
            cached = raw
        payload["raw"] = cached
        _sniff_resource(payload, filename, cached, self.cache_dir, key)
        return payload

    def _preview_bethesda(self) -> dict:
        payload = self._base_payload()
        payload["kind"] = "file"
        archive = self.item.native_archive
        entry_name = self.item.entry or self.item.title
        if archive is None:
            raise ValueError("no Bethesda archive loaded for preview")
        key = helpers.cache_key(str(entry_name), self.item.size)
        filename = Path(entry_name).name or "file.bin"
        cached = helpers.read_cached(self.cache_dir, key, filename)
        if cached is None:
            try:
                raw = archive.open_file(entry_name)
            except Exception as exc:
                raise ValueError(f"Bethesda archive read failed: {exc}") from exc
            helpers.write_cached(self.cache_dir, key, filename, raw)
            cached = raw
        payload["raw"] = cached
        if filename.lower().endswith(".nif"):
            try:
                from dualforge.bethesda.nif import parse_nif, read_geometry

                geometry = read_geometry(parse_nif(cached))
                if geometry is not None:
                    payload["mesh"] = geometry
            except Exception:
                logger.debug("NIF geometry decode failed for %s", filename, exc_info=True)
                pass
        _sniff_resource(payload, filename, cached, self.cache_dir, key)
        return payload


def _preview_bones(asset, obj):
    """Best-effort skeleton joints for the mesh preview (or None)."""
    try:
        from dualforge.export.unity_skin import (
            bind_poses,
            bone_hierarchy,
            find_skinned_mesh_renderer,
            joint_positions,
            skin_data,
        )

        if skin_data(obj) is None or bind_poses(obj) is None:
            return None
        assets_file = getattr(asset._reader, "assets_file", None)
        if assets_file is None:
            return None
        smr = find_skinned_mesh_renderer(assets_file.get_objects(), asset._reader)
        if smr is None:
            return None
        names, parents = bone_hierarchy(smr, assets_file)
        points = joint_positions(bind_poses(obj))
        if not names or len(names) != len(points):
            return None
        return [
            {
                "index": idx,
                "name": names[idx],
                "x": float(point[0]),
                "y": float(point[1]),
                "z": float(point[2]),
                "parent": parents[idx],
            }
            for idx, point in enumerate(points)
        ]
    except Exception:
        logger.debug("bone hierarchy unavailable for preview", exc_info=True)
        return None


def _typetree(payload: dict, asset) -> dict[str, object] | None:
    """Attach the full structure of a Unity object to a preview payload as
    JSON-able data (drives the GUI property inspector / JSON exports)."""
    from dualforge.export.unity_assets import typetree_dict

    try:
        tree = typetree_dict(asset)
    except Exception:
        logger.debug("type tree unavailable for preview", exc_info=True)
        tree = None
    if tree is not None:
        payload["typetree"] = tree
    return tree


def _typetree_text(tree: dict[str, object] | None) -> str:
    if tree is None:
        return "No readable type tree for this object."
    try:
        return json.dumps(tree, indent=2, default=str)
    except (TypeError, ValueError):
        return str(tree)


def _count_fields(tree: dict[str, object] | None) -> str:
    if not isinstance(tree, dict):
        return "0"
    seen = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, val in value.items():
                seen.add(key)
                walk(val)
        elif isinstance(value, (list, tuple)):
            for val in value:
                walk(val)

    walk(tree)
    return str(len(seen))


def _try_locres(filename: str, data: bytes) -> tuple | None:
    """Parse Unreal .locres localization data into a (text, meta) tuple.

    Returns None when the data does not parse as locres (fall back to the
    normal image/audio/text sniffing path).
    """
    if not filename.lower().endswith(".locres"):
        return None
    from dualforge.unreal.locres import parse_locres

    try:
        locres = parse_locres(data)
    except Exception:
        logger.debug("locres parse failed for %s", filename, exc_info=True)
        return None
    rows = []
    for entry in locres.entries[:200]:
        label = entry.key if not entry.namespace else f"{entry.namespace}.{entry.key}"
        rows.append(f"{label}\n  {entry.value}")
    text = "\n\n".join(rows) if rows else "(empty locres)"
    if len(locres.entries) > 200:
        text += f"\n\n... {len(locres.entries) - 200} more entries ..."
    meta = {
        "Decoded": "yes (locres)",
        "Entries": str(len(locres.entries)),
        "Version": str(locres.version or "detected"),
    }
    return text, meta


def _sniff_resource(
    payload: dict,
    filename: str,
    data: bytes,
    cache_dir: str,
    key: str,
) -> None:
    """Mutate ``payload`` with decoded image / audio / text preview data.

    Used by the file, Unreal, CDPR and Bethesda preview drivers so every
    extracted-on-disk payload sniffs identically (locres → image → audio →
    text).
    """
    locres_result = _try_locres(filename, data)
    if locres_result is not None:
        text, locres_meta = locres_result
        payload["text"] = text
        payload["meta"].update(locres_meta)
        return
    image = helpers.sniff_image(data)
    if image is not None:
        payload["image"] = image
        payload["meta"].update(
            {
                "Width": str(image.width()),
                "Height": str(image.height()),
                "Decoded": "image",
                "Format": Path(filename).suffix.lstrip(".").upper() or "BIN",
            }
        )
        return
    audio = helpers.sniff_audio(data, filename, cache_dir, key)
    if audio is not None:
        payload["audio_path"] = audio["audio_path"]
        payload["peaks"] = audio["peaks"]
        payload["duration"] = audio["duration"]
        payload["sample_rate"] = audio["sample_rate"]
        payload["channels"] = audio["channels"]
        payload["meta"].update(
            {
                "Decoded": "audio",
                "Format": Path(filename).suffix.lstrip(".").upper() or "BIN",
            }
        )
        return
    if helpers.guess_text(data):
        payload["text"] = _pretty_text(data)
        payload["meta"]["Decoded"] = "yes (utf-8)"
    else:
        payload["meta"]["Decoded"] = "no"


def _looks_like_unreal_package(entry: str) -> bool:
    """True when ``entry`` is a Unreal package file that ``preview_mesh``
    can reasonably handle (.uasset/.umap). Audio, texture and other
    media entries are already covered by the generic sniffing path, so
    skipping them avoids an unnecessary uex subprocess call."""
    name = entry.lower()
    return name.endswith((".uasset", ".umap"))


def _extra_unity_meta(obj, type_name: str) -> dict:
    """Best-effort asset metadata for previews (sprite rects, clip timing, fonts)."""
    meta: dict = {}
    if type_name == "Sprite":
        rect = getattr(obj, "m_Rect", None)
        if rect is not None:
            x, y = getattr(rect, "x", 0.0), getattr(rect, "y", 0.0)
            w, h = getattr(rect, "width", 0.0), getattr(rect, "height", 0.0)
            meta["Rect"] = f"{x:.0f},{y:.0f} {w:.0f}x{h:.0f}"
        packed = getattr(obj, "m_Packed", None)
        if packed is not None:
            meta["Packed"] = "yes" if packed else "no"
        tags = getattr(obj, "m_AtlasTags", None)
        if tags:
            meta["Atlas"] = ", ".join(str(t) for t in tags[:3])
    elif type_name == "Texture2D":
        filter_mode = getattr(obj, "m_FilterMode", None)
        if filter_mode is not None:
            meta["Filter mode"] = str(filter_mode)
        wrap_mode = getattr(obj, "m_WrapMode", None)
        if wrap_mode is not None:
            meta["Wrap mode"] = str(wrap_mode)
    elif type_name == "AnimationClip":
        settings = getattr(obj, "m_AnimationClipSettings", None)
        if settings is not None:
            stop = getattr(settings, "m_StopTime", 0.0)
            meta["Duration"] = f"{stop:.3f} s"
        rate = getattr(obj, "m_SampleRate", None)
        if rate:
            meta["Sample rate"] = f"{rate} fps"
        curves = getattr(obj, "m_EditorCurves", None)
        if curves is not None:
            meta["Curves"] = str(len(curves))
        events = getattr(obj, "m_Events", None)
        if events is not None:
            meta["Events"] = str(len(events))
    elif type_name == "Font":
        glyphs = getattr(obj, "m_Glyphs", None)
        if glyphs is not None:
            meta["Glyphs"] = str(len(glyphs))
        spacing = getattr(obj, "m_LineSpacing", None)
        if spacing is not None:
            meta["Line spacing"] = f"{spacing:.2f}"
        size = getattr(obj, "m_DefaultSize", None)
        if size is not None:
            meta["Default size"] = str(size)
    return meta


def _pretty_text(data: bytes) -> str:
    text = data.decode("utf-8", "replace")
    try:
        return json.dumps(json.loads(text), indent=2)
    except (ValueError, TypeError):
        pass
    try:
        return xml.dom.minidom.parseString(text).toprettyxml(indent="  ")
    except Exception:
        logger.debug("text is neither JSON nor  XML; showing raw", exc_info=True)
        return text


__all__ = [
    "PreviewItem",
    "PreviewSignals",
    "PreviewWorker",
]