"""Offline runtime key hunt: find a game's decryption key from a running
process (no Ghidra / Java needed).

Reuses the offline crack pipeline (``dualforge.crack_process.crack_offline``)
which supports scheme-aware validation against a pak (``aes-256``, ``sm4-abi``
for Arena Breakout: Infinite, ``unity-cn``, derived schemes, ...) so a cracked
key lands in the key store exactly like a static Ghidra-found one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

# Known decryption stage names surfaced as a user choice, with friendly labels.
# These map directly to registered schemes (see dualforge.encryption.registry)
# so "arena-breakout"-style presets are offered by their stage name (sm4-abi).
SCHEME_CHOICES: list[tuple[str, str]] = [
    ("", "Auto (pick the scheme from game + pak)"),
    ("aes-256", "Standard AES-256"),
    ("sm4-abi", "SM4 key-table A  (Arena Breakout: Infinite)"),
    ("unity-cn", "Unity CN Pro (16-char XOR)"),
    ("derived-aes-md5", "Derived AES (Snowbreak-style)"),
    ("derived-xor-md5", "Per-filename md5 XOR (Star Savior-style)"),
    ("xor8", "AES + 8-byte XOR (Delta Force / Marvel Rivals)"),
    ("custom-aes-round", "Custom round keys (Monster Jam / Dragon Sword)"),
    ("partial-encrypt", "Partial encryption (Wuthering Waves)"),
]

_EXTRA_SCHEME_LABELS = {
    "sm4": "Generic SM4 (mode/sbox params)",
    "aes": "Raw AES block (params-driven)",
}


def scheme_choices() -> list[tuple[str, str]]:
    """Return the stable scheme option list, appending any self-registered
    schemes that are not yet surfaced (kept deterministic for the UI/tests)."""
    from dualforge.encryption.registry import list_schemes

    known = {value for value, _label in SCHEME_CHOICES if value}
    extra = sorted(
        (name, _EXTRA_SCHEME_LABELS.get(name, name))
        for name in list_schemes()
        if name not in known
    )
    return SCHEME_CHOICES + extra


def parse_process_text(text: str) -> tuple[str | None, int | None]:
    """Turn the process combo's current text into ``(process, pid)``.

    Accepts an executable name (``Game.exe``), a raw PID, or the rendered list
    item ``"Game.exe  (pid 1234)"`` produced by the process picker.
    """
    clean = (text or "").strip()
    if not clean:
        return None, None
    lowered = clean.lower()
    if "  (pid " in lowered and lowered.endswith(")"):
        name, _, pid_part = lowered.rpartition("  (pid ")
        pid_text = pid_part[:-1].strip()
        if pid_text.isdigit():
            return name, int(pid_text)
    if clean.isdigit():
        return None, int(clean)
    return clean, None


class OfflineCrackSignals(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class OfflineCrackWorker(QThread):
    def __init__(self, options: dict):
        super().__init__()
        self.options = options
        self._signals = OfflineCrackSignals()
        self.log = self._signals.log
        self.done = self._signals.done
        self.failed = self._signals.failed

    def run(self) -> None:
        from dualforge.crack_process import crack_offline

        try:
            result = crack_offline(
                process=self.options.get("process"),
                pid=self.options.get("pid"),
                pak=self.options.get("pak") or None,
                scheme=self.options.get("scheme") or None,
                save_keys=self.options.get("save_keys", True),
                title=self.options.get("title") or None,
                log=self.log.emit,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            self.failed.emit(str(exc))
            return
        self.done.emit(result)


class OfflineKeyHuntDialog(QDialog):
    """Scan a running game's memory for its decryption key without Ghidra."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Offline Key Hunt (from running game)")
        self.resize(720, 620)
        self._worker: OfflineCrackWorker | None = None
        self._windows = sys.platform == "win32"

        layout = QVBoxLayout(self)

        process_row = QHBoxLayout()
        process_row.addWidget(QLabel("Process:"))
        self.process_combo = QComboBox()
        self.process_combo.setEditable(True)
        self.process_combo.setPlaceholderText("Running game process, e.g. Game.exe  (or a PID)")
        self.process_combo.setMinimumWidth(320)
        process_row.addWidget(self.process_combo, 1)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_processes)
        process_row.addWidget(self.refresh_btn)
        layout.addLayout(process_row)
        self._refresh_processes()

        pak_row = QHBoxLayout()
        pak_row.addWidget(QLabel("Validate with:"))
        self.pak_edit = QLineEdit()
        self.pak_edit.setPlaceholderText("Game .pak (or folder with paks) to verify the cracked key")
        pak_row.addWidget(self.pak_edit, 1)
        pak_browse_btn = QPushButton("Browse...")
        pak_browse_btn.clicked.connect(self._browse_pak)
        pak_row.addWidget(pak_browse_btn)
        layout.addLayout(pak_row)

        scheme_row = QHBoxLayout()
        scheme_row.addWidget(QLabel("Scheme:"))
        self.scheme_combo = QComboBox()
        for value, label in scheme_choices():
            self.scheme_combo.addItem(label, value)
        self.scheme_combo.setToolTip(
            "Restrict validation to one key pipeline. Auto guesses from the "
            "game name and pak, e.g. sm4-abi for Arena Breakout: Infinite."
        )
        scheme_row.addWidget(self.scheme_combo, 1)
        scheme_row.addStretch(1)
        layout.addLayout(scheme_row)

        store_row = QHBoxLayout()
        self.store_check = QCheckBox("Save verified keys to the key store")
        self.store_check.setChecked(True)
        store_row.addWidget(self.store_check)
        store_row.addWidget(QLabel("Title:"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Default: <game.exe stem> [offline-N]")
        store_row.addWidget(self.title_edit, 1)
        layout.addLayout(store_row)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start Hunt")
        self.start_btn.setProperty("role", "primary")
        self.start_btn.clicked.connect(self._start_hunt)
        buttons.addWidget(self.start_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        font = self.log_view.font()
        font.setFamily("Consolas, Cascadia Mono, monospace")
        font.setPointSize(9)
        self.log_view.setFont(font)
        layout.addWidget(self.log_view, 1)

        self.result_label = QLabel("Ready.")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.result_label)

        note = QLabel(
            "Samples the running process memory for the decryption key material "
            "and validates candidates against a real pak block. Windows only; "
            "attach to a freshly started game so the key is resident. Use the "
            "'SM4 key-table A (Arena Breakout: Infinite)' scheme for ABI."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8b90a3;")
        layout.addWidget(note)

        if not self._windows:
            self._log("Offline process scanning requires Windows; the hunt is disabled here.")

    # ---- helpers ----

    def _log(self, message: str) -> None:
        self.log_view.appendPlainText(message)

    def _refresh_processes(self) -> None:
        self.process_combo.clear()
        if not self._windows:
            return
        try:
            from dualforge.unreal.process import list_processes

            processes = list_processes()
        except Exception as exc:  # noqa: BLE001 - surfaced in the combo
            self._log(f"could not list processes: {exc}")
            return
        for pid, exe in processes:
            self.process_combo.addItem(f"{exe}  (pid {pid})", (pid, exe))

    def _browse_pak(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a game pak to validate against",
            "",
            "Unreal pak (*.pak);;All files (*)",
        )
        if chosen:
            self.pak_edit.setText(chosen)

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.refresh_btn.setEnabled(not running)
        self.process_combo.setEnabled(not running)
        self.pak_edit.setEnabled(not running)
        self.scheme_combo.setEnabled(not running)
        self.store_check.setEnabled(not running)
        self.title_edit.setEnabled(not running)
        self.progress.setVisible(running)

    def _finish(self) -> None:
        self._set_running(False)
        self._worker = None

    # ---- actions ----

    def _start_hunt(self) -> None:
        if not self._windows:
            QMessageBox.information(
                self, "Offline Key Hunt", "Process memory scanning requires Windows."
            )
            return
        if self._worker is not None and self._worker.isRunning():
            return
        process, pid = parse_process_text(self.process_combo.currentText())
        if process is None and pid is None:
            QMessageBox.information(
                self, "Offline Key Hunt", "Pick a running game process or type an executable name."
            )
            return
        pak = self.pak_edit.text().strip()
        if pak and not Path(pak).exists():
            QMessageBox.warning(self, "Offline Key Hunt", f"Validation pak not found:\n{pak}")
            return
        options = {
            "process": process,
            "pid": pid,
            "pak": pak or None,
            "scheme": self.scheme_combo.currentData(),
            "save_keys": self.store_check.isChecked(),
            "title": self.title_edit.text().strip() or None,
        }
        scheme = self.scheme_combo.currentData()
        label = self.scheme_combo.currentText()
        self._log(
            f"=== offline hunt: process={'pid ' + str(pid) if pid is not None else process} "
            f"pak={Path(pak).name if pak else '(none)'} "
            f"scheme={scheme or 'auto'} ({label}) ==="
        )
        self.result_label.setText("Scanning the game's memory...")
        self.log_view.clear()
        self._set_running(True)
        worker = OfflineCrackWorker(options)
        worker.log.connect(self._log)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._finish)
        self._worker = worker
        worker.start()

    def _on_done(self, result: dict) -> None:
        from dualforge.crack_process import summarize

        self._log(summarize(result))
        status = result.get("status")
        verified = result.get("verified_keys") or []
        if verified:
            lines = []
            for vk in verified:
                tag = f" [{vk.scheme}]" if vk.scheme != "aes-256" else ""
                lines.append(f"verified key  : {vk.key}{tag}")
            saved = result.get("saved") or []
            if saved:
                lines.append("saved to key store : " + ", ".join(saved))
            self.result_label.setText(
                "Key verified and saved. Open the game archive to extract with it."
                + ("  (no pak given; pass one next time to verify and save.)" if not result.get("pak") else "")
            )
            self._log("\n".join(lines))
            return
        if status == "no_key_found":
            self.result_label.setText(
                "No candidate validated. Start the game fresh so the key is resident, "
                "widen the scan, or check the scheme (ABI = sm4-abi)."
            )
        else:
            self._log("\n".join(f"candidate key  : {key}" for key in result.get("candidates", [])[:8]))
            self.result_label.setText(
                f"Scanned {result.get('bytes_scanned', 0):,} bytes, found "
                f"{len(result.get('candidates', []))} candidate(s). Pick a pak to verify and save."
            )

    def _on_failed(self, message: str) -> None:
        self.result_label.setText("Hunt failed.")
        self._log(f"error: {message}")


__all__ = ["OfflineKeyHuntDialog", "parse_process_text", "scheme_choices"]