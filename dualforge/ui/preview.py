from __future__ import annotations

import json
import tempfile
import xml.dom.minidom
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dualforge.ui import preview_helpers as helpers
from dualforge.ui.widgets import HexView, ImageView, LoadingOverlay, MeshView, SoftwareMeshView, WaveformWidget, gl_context_available

IMAGE_TYPES = {"Texture2D", "Sprite"}
AUDIO_TYPES = {"AudioClip"}
MESH_TYPES = {"Mesh"}
TEXT_TYPES = {"TextAsset"}
OBJECT_TYPES = {"MonoBehaviour", "Material"}
SHADER_TYPES = {"Shader"}
FONT_TYPES = {"Font"}
ANIMATION_TYPES = {"AnimationClip"}

_PAGE_HERO = 0
_PAGE_IMAGE = 1
_PAGE_AUDIO = 2
_PAGE_MESH = 3
_PAGE_TEXT = 4
_PAGE_HEX = 5
_PAGE_META = 6
_PAGE_ERROR = 7


@dataclass
class PreviewItem:
    title: str
    engine: str
    kind: str
    size: int
    asset: object = None
    entry: Optional[str] = None
    aes_key: Optional[str] = None
    archive_path: str = ""
    native_archive: object = None
    meta: Dict[str, str] = field(default_factory=dict)

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
            return None
        try:
            import json as _json

            text = _json.dumps(tree, indent=2, default=str)
        except Exception:
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
        locres_result = _try_locres(path.name, data)
        if locres_result is not None:
            text, locres_meta = locres_result
            payload["text"] = text
            payload["meta"].update(locres_meta)
            return payload
        image = helpers.sniff_image(data)
        if image is not None:
            payload["image"] = image
            payload["meta"].update(
                {
                    "Width": str(image.width()),
                    "Height": str(image.height()),
                    "Decoded": "image",
                    "Format": path.suffix.lstrip(".").upper() or "BIN",
                }
            )
            return payload
        key = helpers.cache_key(str(path), len(data))
        audio = helpers.sniff_audio(data, path.name, self.cache_dir, key)
        if audio is not None:
            payload["audio_path"] = audio["audio_path"]
            payload["peaks"] = audio["peaks"]
            payload["duration"] = audio["duration"]
            payload["sample_rate"] = audio["sample_rate"]
            payload["channels"] = audio["channels"]
            payload["meta"].update(
                {
                    "Decoded": "audio",
                    "Format": path.suffix.lstrip(".").upper() or "BIN",
                }
            )
            return payload
        if helpers.guess_text(data):
            payload["text"] = _pretty_text(data)
            payload["meta"]["Decoded"] = "yes (utf-8)"
        else:
            payload["meta"]["Decoded"] = "no"
        return payload

    def _preview_unreal(self) -> dict:
        payload = self._base_payload()
        payload["kind"] = "file"
        key = helpers.cache_key(str(self.item.entry or ""), self.item.size)
        filename = Path(self.item.entry or self.item.title).name or "file.bin"
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
                        bridge.extract(
                            self.item.archive_path,
                            temp_dir,
                            aes_key=self.item.aes_key,
                            files=[self.item.entry or self.item.title],
                        )
                    except Exception as exc:
                        raise ValueError(f"preview extract failed: {exc}") from exc
                    found = list(Path(temp_dir).rglob("*"))
                    candidate = next((p for p in found if p.is_file()), None)
                    if candidate is None:
                        raise ValueError("no file was extracted for preview")
                    cached = candidate.read_bytes()
                    helpers.write_cached(self.cache_dir, key, filename, cached)
        mesh_result = self._try_unreal_mesh()
        if mesh_result is not None:
            geometry, glb_bytes = mesh_result
            payload["mesh"] = geometry
            payload["glb"] = glb_bytes
            payload["glb_name"] = f"{Path(filename).stem}.glb"
            return payload
        payload["raw"] = cached
        locres_result = _try_locres(filename, cached)
        if locres_result is not None:
            text, locres_meta = locres_result
            payload["text"] = text
            payload["meta"].update(locres_meta)
            return payload
        image = helpers.sniff_image(cached)
        if image is not None:
            payload["image"] = image
            payload["meta"].update(
                {
                    "Width": str(image.width()),
                    "Height": str(image.height()),
                    "Decoded": "image",
                }
            )
            return payload
        audio = helpers.sniff_audio(cached, filename, self.cache_dir, key)
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
            return payload
        if helpers.guess_text(cached):
            payload["text"] = _pretty_text(cached)
            payload["meta"]["Decoded"] = "yes (utf-8)"
        else:
            payload["meta"]["Decoded"] = "no"
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
            return None
        usmap = None
        try:
            from dualforge.unreal.uex_adapter import find_usmap

            usmap = find_usmap(str(Path(self.item.archive_path).parent))
        except Exception:
            usmap = None
        try:
            result = bridge.preview_mesh(
                self.item.archive_path,
                entry,
                aes_key=self.item.aes_key,
                usmap=usmap,
            )
        except Exception:
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
        locres_result = _try_locres(filename, cached)
        if locres_result is not None:
            text, locres_meta = locres_result
            payload["text"] = text
            payload["meta"].update(locres_meta)
            return payload
        image = helpers.sniff_image(cached)
        if image is not None:
            payload["image"] = image
            payload["meta"].update(
                {
                    "Width": str(image.width()),
                    "Height": str(image.height()),
                    "Decoded": "image",
                }
            )
            return payload
        audio = helpers.sniff_audio(cached, filename, self.cache_dir, key)
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
            return payload
        if helpers.guess_text(cached):
            payload["text"] = _pretty_text(cached)
            payload["meta"]["Decoded"] = "yes (utf-8)"
        else:
            payload["meta"]["Decoded"] = "no"
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
        return None


def _typetree(payload: dict, asset) -> Optional[Dict[str, object]]:
    """Attach the full structure of a Unity object to a preview payload as
    JSON-able data (drives the GUI property inspector / JSON exports)."""
    from dualforge.export.unity_assets import typetree_dict

    try:
        tree = typetree_dict(asset)
    except Exception:
        tree = None
    if tree is not None:
        payload["typetree"] = tree
    return tree


def _typetree_text(tree: Optional[Dict[str, object]]) -> str:
    if tree is None:
        return "No readable type tree for this object."
    try:
        return json.dumps(tree, indent=2, default=str)
    except (TypeError, ValueError):
        return str(tree)


def _count_fields(tree: Optional[Dict[str, object]]) -> str:
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


def _try_locres(filename: str, data: bytes) -> Optional[tuple]:
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
        return text


class AudioPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self.waveform = WaveformWidget()
        layout.addWidget(self.waveform, 1)

        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setProperty("role", "primary")
        self.play_button.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop)
        controls.addWidget(self.stop_button)

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setEnabled(False)
        self.position.sliderMoved.connect(self._seek)
        controls.addWidget(self.position, 1)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color: #8b90a3;")
        controls.addWidget(self.time_label)

        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setMaximum(100)
        volume.setValue(80)
        volume.setFixedWidth(90)
        volume.valueChanged.connect(lambda v: self.audio_output.setVolume(v / 100.0))
        controls.addWidget(volume)
        layout.addLayout(controls)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player.setAudioOutput(self.audio_output)
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.mediaStatusChanged.connect(self._on_status)

    def set_audio(self, path: str, peaks, duration: float, rate: int, channels: int = 1) -> None:
        self.waveform.set_audio(peaks, duration)
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.position.setEnabled(True)
        self.time_label.setText(f"0:00 / {self._fmt(duration)}")

    def clear(self) -> None:
        self.player.stop()
        self.waveform.clear()
        self.position.setEnabled(False)
        self.time_label.setText("0:00 / 0:00")

    def _fmt(self, seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"

    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _stop(self) -> None:
        self.player.stop()

    def _seek(self, position: int) -> None:
        self.player.setPosition(position)

    def _on_duration(self, duration: int) -> None:
        self.position.setRange(0, max(duration, 1))

    def _on_position(self, position: int) -> None:
        self.position.setValue(position)
        self.waveform.set_position(position / 1000.0)
        self.time_label.setText(f"{self._fmt(position / 1000.0)} / {self._fmt(self.player.duration() / 1000.0)}")

    def _on_status(self, status) -> None:
        self.play_button.setText(
            "Pause" if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState else "Play"
        )


class MeshPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        self.wireframe_check = QCheckBox("Wireframe")
        self.wireframe_check.toggled.connect(self._on_wireframe)
        toolbar.addWidget(self.wireframe_check)
        reset_btn = QPushButton("Reset view")
        reset_btn.clicked.connect(self._reset)
        toolbar.addWidget(reset_btn)
        self.export_btn = QPushButton("Export GLB...")
        self.export_btn.clicked.connect(self._on_export_glb)
        self.export_btn.setVisible(False)
        toolbar.addWidget(self.export_btn)
        toolbar.addStretch(1)
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("color: #8b90a3;")
        toolbar.addWidget(self.stats_label)
        layout.addLayout(toolbar)

        if gl_context_available():
            self.view = MeshView()
        else:
            self.view = SoftwareMeshView()
        layout.addWidget(self.view, 1)
        self._glb: Optional[bytes] = None
        self._glb_name = "mesh.glb"

    def set_mesh(self, mesh, bones=None, glb=None, name=None) -> None:
        if self.view is None:
            return
        verts, normals, tris, edges = mesh
        uv = getattr(mesh, "uv", None)
        texture = getattr(mesh, "texture", None)
        self.view.set_mesh(verts, normals, tris, edges, uv=uv, texture=texture)
        self.view.set_bones(bones)
        label = f"{len(verts):,} vertices - {len(tris):,} triangles"
        if bones:
            label += f" - {len(bones):,} bones"
        if texture:
            texture_name = getattr(mesh, "texture_name", None) or ""
            label += f" - textured ({texture_name})" if texture_name else " - textured"
        self.stats_label.setText(label)
        self._glb = glb
        self._glb_name = name or "mesh.glb"
        self.export_btn.setVisible(glb is not None)

    def _on_export_glb(self) -> None:
        if not self._glb:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export GLB", self._glb_name, "GLB (*.glb)"
        )
        if not path:
            return
        try:
            Path(path).write_bytes(self._glb)
        except OSError as exc:
            QMessageBox.warning(self, "Export GLB", f"Could not write GLB:\n{exc}")
            return
        QMessageBox.information(self, "Export GLB", "GLB written:\n" + path)

    def _on_wireframe(self, enabled: bool) -> None:
        if self.view is not None:
            self.view.set_wireframe(enabled)

    def _reset(self) -> None:
        if self.view is not None:
            self.view.reset_view()


class TextPage(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setProperty("role", "text-page")
        font = self.font()
        font.setFamily("Consolas, Cascadia Mono, monospace")
        font.setPointSize(10)
        self.setFont(font)


class ImagePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(self._fit)
        toolbar.addWidget(fit_btn)
        actual_btn = QPushButton("1:1")
        actual_btn.clicked.connect(self._actual)
        toolbar.addWidget(actual_btn)
        in_btn = QPushButton("+")
        in_btn.clicked.connect(self._zoom_in)
        toolbar.addWidget(in_btn)
        out_btn = QPushButton("-")
        out_btn.clicked.connect(self._zoom_out)
        toolbar.addWidget(out_btn)
        toolbar.addStretch(1)
        self.dims_label = QLabel("")
        self.dims_label.setStyleSheet("color: #8b90a3;")
        toolbar.addWidget(self.dims_label)
        layout.addLayout(toolbar)

        self.view = ImageView()
        layout.addWidget(self.view, 1)

    def set_image(self, image: QImage) -> None:
        self.view.set_image(image)
        self.dims_label.setText(f"{image.width()} x {image.height()} px")

    def _fit(self) -> None:
        self.view.fit_in_view()

    def _actual(self) -> None:
        self.view.zoom_actual()

    def _zoom_in(self) -> None:
        self.view.zoom_in()

    def _zoom_out(self) -> None:
        self.view.zoom_out()


class HexPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.view = HexView()
        layout.addWidget(self.view, 1)
        self.note = QLabel("")
        self.note.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.note)

    def set_bytes(self, data: bytes) -> None:
        self.view.set_data(data[: helpers.MAX_PREVIEW_BYTES])
        omitted = max(0, len(data) - helpers.MAX_PREVIEW_BYTES)
        note = f"{len(data):,} bytes"
        if omitted:
            note += f" (showing first {helpers.MAX_PREVIEW_BYTES:,}; {omitted:,} omitted)"
        self.note.setText(note)


class MetaPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("")
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.details = QLabel("")
        self.details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.details.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.details)


class HeroPage(QWidget):
    open_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon)
        self.title = QLabel("No asset selected")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setProperty("role", "hero-title")
        layout.addWidget(self.title)
        self.subtitle = QLabel("Unity & Unreal asset extractor")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setProperty("role", "hero-subtitle")
        layout.addWidget(self.subtitle)
        self.hint = QLabel("Open an archive to browse, preview, and extract game assets.")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setProperty("role", "hero-subtitle")
        layout.addWidget(self.hint)
        self.open_button = QPushButton("Open Archive...")
        self.open_button.setProperty("role", "primary")
        self.open_button.clicked.connect(self.open_requested)
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_hero(self, title: str, hint: str, pixmap: Optional[QPixmap] = None) -> None:
        self.title.setText(title)
        self.hint.setText(hint)
        if pixmap is not None:
            self.icon.setPixmap(pixmap)


class PreviewPanel(QStackedWidget):
    typetree_loaded = Signal(dict)

    def __init__(self, cache_dir: str, parent=None):
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.current_item: Optional[PreviewItem] = None
        self._worker: Optional[PreviewWorker] = None

        self.hero_page = HeroPage()
        self.image_page = ImagePage()
        self.audio_page = AudioPage()
        self.mesh_page = MeshPage()
        self.text_page = TextPage()
        self.hex_page = HexPage()
        self.meta_page = MetaPage()
        self.error_page = MetaPage()

        self.addWidget(self.hero_page)
        self.addWidget(self.image_page)
        self.addWidget(self.audio_page)
        self.addWidget(self.mesh_page)
        self.addWidget(self.text_page)
        self.addWidget(self.hex_page)
        self.addWidget(self.meta_page)
        self.addWidget(self.error_page)

        self.overlay = LoadingOverlay(self)
        self.overlay.cancel_button.clicked.connect(self._cancel_preview)

    def show_hero(self, title: str = "No asset selected", hint: str = "Select an asset in the list to preview it.", pixmap: Optional[QPixmap] = None) -> None:
        self._cancel_preview()
        self.hero_page.set_hero(title, hint, pixmap)
        self.setCurrentIndex(_PAGE_HERO)

    def request_preview(self, item: PreviewItem) -> None:
        if self.current_item is not None and self.current_item.identity() == item.identity():
            return
        self._cancel_preview()
        self.current_item = item
        self.overlay.show_overlay(f"Loading {item.title}...")
        self._worker = PreviewWorker(item, self.cache_dir, self)
        self._worker.loaded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

    def _cancel_preview(self) -> None:
        if self._worker is not None:
            if self._worker.isRunning():
                self._worker.cancel()
                self._worker.wait(2000)
            if self._worker.isRunning():
                self._worker.terminate()
            self._worker = None
        self.overlay.hide_overlay()

    def _on_worker_done(self) -> None:
        if self._worker is not None and not self._worker.isRunning():
            self._worker = None
        self.overlay.hide_overlay()

    def _on_loaded(self, payload: dict) -> None:
        self.overlay.hide_overlay()
        meta_rows = "\n".join(f"{k}: {v}" for k, v in payload.get("meta", {}).items())
        title = payload.get("title", "")
        if "typetree" in payload:
            self.typetree_loaded.emit(payload["typetree"])
        if "image" in payload and payload["image"] is not None:
            self.image_page.set_image(payload["image"])
            self.setCurrentIndex(_PAGE_IMAGE)
            self.meta_page.title.setText(title)
            self.meta_page.details.setText(meta_rows)
        elif "audio_path" in payload:
            self.audio_page.set_audio(
                payload["audio_path"],
                payload.get("peaks"),
                payload.get("duration", 0.0),
                payload.get("sample_rate", 0),
                payload.get("channels", 1),
            )
            self.setCurrentIndex(_PAGE_AUDIO)
            self.meta_page.title.setText(title)
            self.meta_page.details.setText(meta_rows)
        elif "mesh" in payload:
            self.mesh_page.set_mesh(
                payload["mesh"],
                payload.get("bones"),
                glb=payload.get("glb"),
                name=payload.get("glb_name"),
            )
            self.setCurrentIndex(_PAGE_MESH)
            self.meta_page.title.setText(title)
            self.meta_page.details.setText(meta_rows)
        elif "text" in payload:
            self.text_page.setPlainText(payload["text"])
            self.setCurrentIndex(_PAGE_TEXT)
            self.meta_page.title.setText(title)
            self.meta_page.details.setText(meta_rows)
        elif "raw" in payload:
            self.hex_page.set_bytes(payload["raw"])
            self.setCurrentIndex(_PAGE_HEX)
            self.meta_page.title.setText(title)
            self.meta_page.details.setText(meta_rows)
        else:
            self.meta_page.title.setText(title)
            self.meta_page.details.setText(meta_rows or "No previewable content.")
            self.setCurrentIndex(_PAGE_META)

    def _on_failed(self, title: str, message: str) -> None:
        self.overlay.hide_overlay()
        self.error_page.title.setText(title)
        self.error_page.details.setText(f"No preview available.\n\n{message}")
        self.setCurrentIndex(_PAGE_ERROR)
