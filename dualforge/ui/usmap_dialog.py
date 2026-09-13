from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from dualforge.unreal.usmap_dump import UsmapDumpError, dump_usmap, list_game_processes


class UsmapDumpSignals(QObject):
    log = Signal(str)
    done = Signal(int)
    failed = Signal(str)


class UsmapDumpWorker(QThread):
    """Dump the FNamePool of a running UE5 game on a background thread."""

    def __init__(self, pid: int, out_path: str, parent=None):
        super().__init__(parent)
        self.pid = pid
        self.out_path = out_path
        self._signals = UsmapDumpSignals()
        self.log = self._signals.log
        self.done = self._signals.done
        self.failed = self._signals.failed

    def run(self) -> None:
        try:
            pool = dump_usmap(self.pid, self.out_path)
        except (UsmapDumpError, OSError, Exception) as exc:
            self.failed.emit(str(exc))
            return
        self.log.emit(
            f"dumped {len(pool.names)} names from {pool.block_count} blocks -> {self.out_path}"
        )
        self.done.emit(0)


class UsmapDumpDialog(QDialog):
    """Tools > Generate USMAP: dump names from a running UE5 game process."""

    def __init__(self, parent=None, suggested_exe: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Generate USMAP from Running Game")
        self.resize(680, 460)
        self._worker: UsmapDumpWorker | None = None
        self._processes: list[tuple[int, str]] = []
        self._suggested_exe = suggested_exe

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Generate a .usmap mappings file for UNREAL ENGINE (UE) games.\n"
            "The game must be a UE5 title with unversioned packages (e.g. TEKKEN 8, "
            "Dragon Ball: Sparking! ZERO).\n"
            "Start the game first, pick its process below, then generate - no internet needed."
        ))

        process_row = QHBoxLayout()
        self.process_combo = QComboBox()
        self.process_combo.setMinimumWidth(380)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh_processes)
        process_row.addWidget(QLabel("Game process:"))
        process_row.addWidget(self.process_combo, 1)
        process_row.addWidget(self.refresh_button)
        layout.addLayout(process_row)

        output_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("output usmap path, e.g. ~/.dualforge/MyGame.usmap")
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_output)
        output_row.addWidget(QLabel("Output file:"))
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse_button)
        layout.addLayout(output_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.dump_button = QPushButton("Generate USMAP")
        self.dump_button.clicked.connect(self._start_dump)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self.dump_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._refresh_processes()

    def _refresh_processes(self) -> None:
        try:
            self._processes = sorted(list_game_processes(), key=lambda item: item[1].lower())
        except (UsmapDumpError, Exception) as exc:
            QMessageBox.warning(self, "Generate USMAP", f"Could not list processes:\n{exc}")
            self._processes = []
        self.process_combo.clear()
        for pid, exe in self._processes:
            self.process_combo.addItem(f"{exe}  (pid {pid})", pid)
        if self._processes:
            match_index = -1
            if self._suggested_exe:
                wanted = Path(self._suggested_exe).name.lower()
                for index, (_pid, exe) in enumerate(self._processes):
                    if exe.lower() == wanted:
                        match_index = index
                        break
            self.process_combo.setCurrentIndex(match_index if match_index >= 0 else 0)
            self._suggest_output()
        self.process_combo.currentIndexChanged.connect(self._on_process_changed)

    def _on_process_changed(self) -> None:
        if self.output_edit.text().strip():
            return
        self._suggest_output()

    def _suggest_output(self) -> None:
        index = self.process_combo.currentIndex()
        if index < 0 or index >= len(self._processes):
            return
        exe = self._processes[index][1]
        base = Path(exe).stem
        self.output_edit.setText(str(Path.home() / ".dualforge" / f"{base}.usmap"))
        self.log_view.appendPlainText(
            "Output suggested in ~/.dualforge so DualForge finds it automatically."
        )

    def _browse_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save USMAP", str(Path.home() / "game.usmap"), "USMAP files (*.usmap)"
        )
        if path:
            self.output_edit.setText(path)

    def _start_dump(self) -> None:
        if self._worker is not None:
            return
        index = self.process_combo.currentIndex()
        if index < 0 or index >= len(self._processes):
            QMessageBox.warning(self, "Generate USMAP", "Pick a running game process first.")
            return
        out_path = self.output_edit.text().strip()
        if not out_path:
            QMessageBox.warning(self, "Generate USMAP", "Pick an output file first.")
            return
        pid = self._processes[index][0]
        self.dump_button.setEnabled(False)
        self.progress.show()
        self._worker = UsmapDumpWorker(pid, out_path, self)
        self._worker.log.connect(self.log_view.appendPlainText)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_failed(self, message: str) -> None:
        self.log_view.appendPlainText(f"error: {message}")
        self.dump_button.setEnabled(True)
        self.progress.hide()
        self._worker = None

    def _on_done(self, code: int) -> None:
        self.dump_button.setEnabled(True)
        self.progress.hide()
        out_path = self.output_edit.text().strip()
        if code == 0 and out_path:
            answer = QMessageBox.question(
                self,
                "Generate USMAP",
                f"USMAP written to {out_path}.\n\nUse it as the mappings file for this game now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes and self.parent() is not None:
                self.parent().settings.usmap_path = out_path
                self.parent().settings.save()
                self.log_view.appendPlainText("usmap_path set in Settings.")
        self._worker = None


__all__ = ["UsmapDumpDialog"]