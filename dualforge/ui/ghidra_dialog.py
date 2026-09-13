from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import types
from contextlib import redirect_stderr, redirect_stdout, suppress
from pathlib import Path
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

EXIT_OK = 0
EXIT_NO_GHIDRA = 3
EXIT_NO_JAVA = 4
EXIT_SETUP = 6
EXIT_ANALYSIS = 7


def ghidra_script_path() -> Path | None:
    """Locate ghidra_key_finder.py in source and frozen (PyInstaller) builds."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        candidate = base / "dualforge" / "ghidra" / "ghidra_key_finder.py"
        if candidate.is_file():
            return candidate
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent / "scripts" / "ghidra" / "ghidra_key_finder.py"
    if candidate.is_file():
        return candidate
    return None


def load_key_finder() -> types.ModuleType | None:
    """Import the key-finder script by path (never imported by the app)."""
    path = ghidra_script_path()
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("dualforge_ghidra_key_finder", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _LogStream(io.TextIOBase):
    def __init__(self, emit: Callable[[str], None]):
        super().__init__()
        self._emit = emit
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        pass


class GhidraSignals(QObject):
    log = Signal(str)
    done = Signal(int)
    failed = Signal(str)


class GhidraWorker(QThread):
    def __init__(self, argv: list[str], tasks: list[list[str]] | None = None):
        super().__init__()
        self.argv = argv
        self.tasks = tasks
        self._signals = GhidraSignals()
        self.log = self._signals.log
        self.done = self._signals.done
        self.failed = self._signals.failed

    def run(self) -> None:
        module = load_key_finder()
        if module is None:
            self.failed.emit("The Ghidra key-finder script was not found.")
            return
        if self.tasks:
            codes: list[int] = []
            for task in self.tasks:
                self.log.emit(f"=== Scanning {Path(task[0]).name} ===")
                try:
                    args = module.build_parser().parse_args(task)
                except SystemExit:
                    self.log.emit("  invalid scan arguments")
                    codes.append(EXIT_SETUP)
                    continue
                stream = _LogStream(self.log.emit)
                try:
                    with redirect_stdout(stream), redirect_stderr(stream):
                        code = module.cmd_check(args) if args.check else module.cmd_hunt(args)
                except Exception as exc:
                    self.log.emit(f"  error: {exc}")
                    code = EXIT_ANALYSIS
                codes.append(code)
            self.done.emit(
                EXIT_OK if any(c == EXIT_OK for c in codes) else (codes[0] if codes else EXIT_SETUP)
            )
            return
        try:
            args = module.build_parser().parse_args(self.argv)
            stream = _LogStream(self.log.emit)
            with redirect_stdout(stream), redirect_stderr(stream):
                code = module.cmd_check(args) if args.check else module.cmd_hunt(args)
            self.done.emit(code)
        except Exception as exc:
            self.failed.emit(str(exc))


class GhidraDialog(QDialog):
    def __init__(self, parent=None, default_binary: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Ghidra Key Hunt")
        self.resize(760, 560)
        self._worker: GhidraWorker | None = None
        self._result_json: str | None = None
        self._result_owned = False
        self._close_when_done = False

        layout = QVBoxLayout(self)

        grid = QGridLayout()
        grid.addWidget(QLabel("Binary:"), 0, 0)
        self.binary_edit = QLineEdit(default_binary or "")
        self.binary_edit.setPlaceholderText("The game executable or DLL to analyze, e.g. Game.exe")
        grid.addWidget(self.binary_edit, 0, 1)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_binary)
        grid.addWidget(browse_btn, 0, 2)

        grid.addWidget(QLabel("Entropy threshold:"), 1, 0)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(1.0, 8.0)
        self.threshold_spin.setSingleStep(0.1)
        self.threshold_spin.setValue(3.5)
        self.threshold_spin.setToolTip("Minimum Shannon entropy per byte for a candidate key (default 3.5)")
        grid.addWidget(self.threshold_spin, 1, 1)
        grid.addWidget(QLabel("Keys to store:"), 1, 2)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 50)
        self.count_spin.setValue(5)
        self.count_spin.setToolTip("How many top 32-byte candidates to write into the key store")
        grid.addWidget(self.count_spin, 1, 3)
        self.add_store_check = QCheckBox("Add candidate keys to the key store")
        self.add_store_check.setChecked(True)
        grid.addWidget(self.add_store_check, 2, 0, 1, 4)
        self.scan_all_check = QCheckBox(
            "Scan ALL detected binaries in the install folder (auto-find the key)"
        )
        self.scan_all_check.setChecked(False)
        self.scan_all_check.setToolTip(
            "Instead of analyzing a single binary, auto-detect every game "
            "executable under the folder and scan them all, collecting the "
            "combined candidate keys."
        )
        self.scan_all_check.toggled.connect(self._toggle_scan_all)
        grid.addWidget(self.scan_all_check, 3, 0, 1, 4)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        self.check_btn = QPushButton("Check Setup")
        self.check_btn.setToolTip("Diagnose the Ghidra / Java / ghidra-bridge setup")
        self.check_btn.clicked.connect(self._check_setup)
        buttons.addWidget(self.check_btn)
        self.hunt_btn = QPushButton("Start Key Hunt")
        self.hunt_btn.setProperty("role", "primary")
        self.hunt_btn.clicked.connect(self._start_hunt)
        buttons.addWidget(self.hunt_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.status_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        font = self.log_view.font()
        font.setFamily("Consolas, Cascadia Mono, monospace")
        font.setPointSize(9)
        self.log_view.setFont(font)
        layout.addWidget(self.log_view, 1)

        self.results_label = QLabel("Results")
        self.results_label.setVisible(False)
        layout.addWidget(self.results_label)
        self.results_table = QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(["Signature", "Block", "Offset", "Candidates"])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.setVisible(False)
        layout.addWidget(self.results_table)

        self.note_label = QLabel(
            "Requires a local Ghidra 11.x install and Java 21. The hunt launches "
            "headless Ghidra and can take several minutes per binary - the log "
            "shows progress. Checking 'Scan ALL detected binaries' runs the hunt "
            "over every game executable found under the selected folder."
        )
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.note_label)

    # ---- helpers ----

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _toggle_scan_all(self, checked: bool) -> None:
        if checked:
            self.binary_edit.setPlaceholderText(
                "The game install folder to scan all executables in, e.g. C:\\Game"
            )
        else:
            self.binary_edit.setPlaceholderText(
                "The game executable or DLL to analyze, e.g. Game.exe"
            )

    def _browse_binary(self) -> None:
        if self.scan_all_check.isChecked():
            path = QFileDialog.getExistingDirectory(
                self, "Choose the game install folder", ""
            )
            if path:
                self.binary_edit.setText(path)
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a binary to analyze",
            "",
            "Executables and libraries (*.exe *.dll *.bin);;All files (*)",
        )
        if path:
            self.binary_edit.setText(path)

    def _set_running(self, running: bool) -> None:
        self.check_btn.setEnabled(not running)
        self.hunt_btn.setEnabled(not running)
        self.binary_edit.setEnabled(not running)
        self.threshold_spin.setEnabled(not running)
        self.count_spin.setEnabled(not running)
        self.add_store_check.setEnabled(not running)
        self.scan_all_check.setEnabled(not running)
        self.progress.setVisible(running)

    def _finish(self) -> None:
        self._set_running(False)
        if self._worker is not None:
            self._worker = None
        if self._close_when_done:
            self.accept()

    # ---- actions ----

    def _check_setup(self) -> None:
        self._run_worker(["--check"])

    def _start_hunt(self) -> None:
        if self.scan_all_check.isChecked():
            self._start_all_hunt()
            return
        binary = self.binary_edit.text().strip()
        if not binary:
            QMessageBox.information(self, "Ghidra Key Hunt", "Choose a binary to analyze first.")
            return
        if not Path(binary).is_file():
            QMessageBox.warning(self, "Ghidra Key Hunt", f"Binary not found:\n{binary}")
            return
        argv = self._hunt_argv(binary)
        fd, json_path = tempfile.mkstemp(prefix="dualforge_ghidra_", suffix=".keys.json")
        import os

        os.close(fd)
        self._result_json = json_path
        self._result_owned = True
        argv += ["--json", json_path]
        self._run_worker(argv)

    def _start_all_hunt(self) -> None:
        from dualforge.unreal.autodetect import find_game_executable

        folder = self.binary_edit.text().strip()
        if not folder:
            QMessageBox.information(
                self, "Ghidra Key Hunt", "Choose the game install folder to scan first."
            )
            return
        if not Path(folder).is_dir():
            QMessageBox.warning(self, "Ghidra Key Hunt", f"Folder not found:\n{folder}")
            return

        _best, ranked = find_game_executable(folder)
        if not ranked:
            QMessageBox.warning(
                self, "Ghidra Key Hunt",
                f"No game executables detected under:\n{folder}",
            )
            return

        tasks: list[list[str]] = []
        json_paths: list[str] = []
        import os

        for binary, _score in ranked:
            fd, json_path = tempfile.mkstemp(prefix="dualforge_ghidra_", suffix=".keys.json")
            os.close(fd)
            json_paths.append(json_path)
            tasks.append(self._hunt_argv(binary) + ["--json", json_path])
        self._log(f"Scanning {len(tasks)} detected executable(s) under {folder}")
        self._result_json = json_paths
        self._result_owned = True
        self._run_worker([], tasks=tasks)

    def _hunt_argv(self, binary: str) -> list[str]:
        argv = [
            binary,
            "--entropy-threshold",
            f"{self.threshold_spin.value():.1f}",
            "--keystore-count",
            str(self.count_spin.value()),
            "--startup-timeout",
            "600",
        ]
        if not self.add_store_check.isChecked():
            argv.append("--no-add-keystore")
        if getattr(sys, "frozen", False):
            argv.append("--no-auto-install")
        return argv

    def _run_worker(self, argv: list[str], tasks: list[list[str]] | None = None) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.results_table.setVisible(False)
        self.results_label.setVisible(False)
        self.log_view.clear()
        self._set_running(True)
        self.status_label.setText("Working...")
        worker = GhidraWorker(argv, tasks=tasks)
        worker.log.connect(self._log)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._finish)
        self._worker = worker
        worker.start()

    def _on_done(self, code: int) -> None:
        labels = {
            EXIT_OK: "Done.",
            EXIT_NO_GHIDRA: "Ghidra was not found (see log).",
            EXIT_NO_JAVA: "Java was not found (see log).",
            EXIT_SETUP: "Setup or bridge failure (see log).",
            EXIT_ANALYSIS: "Analysis failed or timed out (see log).",
        }
        self.status_label.setText(labels.get(code, f"Finished with exit code {code}."))
        if code == EXIT_OK:
            self._show_results(self._result_json)

    def _on_failed(self, message: str) -> None:
        self.status_label.setText("Failed.")
        self._log(f"error: {message}")

    def _show_results(self, json_path) -> None:
        paths = json_path if isinstance(json_path, list) else ([json_path] if json_path else [])
        matches: list[dict] = []
        total_bytes = 0
        duration = 0.0
        added: list[str] = []
        for path in paths:
            try:
                result = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._log(f"could not read the result JSON: {path}")
                continue
            matches.extend(result.get("matches", []))
            total_bytes += result.get("bytes_scanned", 0)
            duration += result.get("duration_s", 0)
            added.extend(result.get("keystore_added", []))
        if not paths:
            self.status_label.setText("Done.")
            return
        self.results_table.setRowCount(len(matches))
        for row, match in enumerate(matches):
            values = [
                match.get("signature", ""),
                match.get("block", ""),
                f"{match.get('offset', 0):#x}",
                str(len(match.get("candidates", []))),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.results_table.setItem(row, column, item)
        if added:
            self._log(f"added to key store: {', '.join(sorted(set(added)))}")
        summary = (
            f"Results: {len(matches)} match(es), "
            f"{total_bytes:,} bytes scanned in "
            f"{duration:.1f}s"
        )
        if added:
            summary += f" - {len(added)} key(s) added to the store"
        self.status_label.setText(summary)
        self.results_label.setText(summary)
        self.results_label.setVisible(True)
        self.results_table.setVisible(True)

    def _cleanup_result(self) -> None:
        if self._result_owned and self._result_json:
            import os

            paths = (
                self._result_json
                if isinstance(self._result_json, list)
                else [self._result_json]
            )
            for path in paths:
                with suppress(OSError):
                    os.unlink(path)
            self._result_json = None
            self._result_owned = False

    def _close(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._close_when_done = True
            self.setVisible(False)
            return
        self._cleanup_result()
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._close_when_done = True
            self.setVisible(False)
            event.ignore()
            return
        self._cleanup_result()
        super().closeEvent(event)
