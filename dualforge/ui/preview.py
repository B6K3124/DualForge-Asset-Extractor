"""Asset preview UI panel and page widgets.

Decoding/payload work lives in :mod:`dualforge.ui.preview_readers`; this
module keeps the QWidget pages (image / audio / mesh / text / hex / meta)
and the :class:`PreviewPanel` that dispatches worker payloads to them.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
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
from dualforge.ui.preview_readers import PreviewItem, PreviewWorker
from dualforge.ui.widgets import HexView, ImageView, LoadingOverlay, MeshView, SoftwareMeshView, WaveformWidget, gl_context_available

_PAGE_HERO = 0
_PAGE_IMAGE = 1
_PAGE_AUDIO = 2
_PAGE_MESH = 3
_PAGE_TEXT = 4
_PAGE_HEX = 5
_PAGE_META = 6
_PAGE_ERROR = 7


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
        self._glb: bytes | None = None
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

    def set_hero(self, title: str, hint: str, pixmap: QPixmap | None = None) -> None:
        self.title.setText(title)
        self.hint.setText(hint)
        if pixmap is not None:
            self.icon.setPixmap(pixmap)


class PreviewPanel(QStackedWidget):
    typetree_loaded = Signal(dict)

    def __init__(self, cache_dir: str, parent=None):
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.current_item: PreviewItem | None = None
        self._worker: PreviewWorker | None = None

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

    def show_hero(self, title: str = "No asset selected", hint: str = "Select an asset in the list to preview it.", pixmap: QPixmap | None = None) -> None:
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