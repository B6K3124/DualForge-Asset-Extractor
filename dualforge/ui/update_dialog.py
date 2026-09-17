"""Update dialog: check for a newer DualForge and update the source checkout.

Runs the network check and the ``git pull`` / ``pip install`` steps on worker
threads so the UI never blocks (same pattern as :mod:`dualforge.ui.ghidra_dialog`).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from dualforge.log import get_logger
from dualforge.update import (
    LATEST_RELEASE_URL,
    UpdateState,
    check_update_cached,
    perform_update,
)
from dualforge.version import __version__

logger = get_logger(__name__)


class UpdateCheckSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class UpdateCheckWorker(QThread):
    def __init__(self, force: bool = True, url: str | None = None, parent=None):
        super().__init__(parent)
        self._force = force
        self._url = url or LATEST_RELEASE_URL
        self._signals = UpdateCheckSignals()
        self.done = self._signals.done
        self.failed = self._signals.failed

    def run(self) -> None:
        try:
            state = check_update_cached(__version__, url=self._url, force=self._force)
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            self.failed.emit(str(exc))
            return
        self.done.emit(state)


class UpdateInstallSignals(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class UpdateInstallWorker(QThread):
    def __init__(self, pull: bool = True, parent=None):
        super().__init__(parent)
        self._pull = pull
        self._signals = UpdateInstallSignals()
        self.log = self._signals.log
        self.done = self._signals.done
        self.failed = self._signals.failed

    def run(self) -> None:
        try:
            root = perform_update(pull=self._pull, log=self.log.emit)
        except Exception as exc:  # noqa: BLE001 - surfaced to the dialog
            self.failed.emit(str(exc))
            return
        self.done.emit(root)


class UpdateDialog(QDialog):
    def __init__(self, parent=None, start_check: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Update DualForge")
        self.resize(600, 420)
        self._check_worker: UpdateCheckWorker | None = None
        self._install_worker: UpdateInstallWorker | None = None
        self._state: UpdateState | None = None

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.title = QLabel("DualForge Updates")
        self.title.setStyleSheet("font-size: 17px; font-weight: 700;")
        row.addWidget(self.title)
        row.addStretch(1)
        layout.addLayout(row)

        self.current_label = QLabel(f"Installed: v{__version__}")
        self.latest_label = QLabel("Latest: checking...")
        self.latest_label.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.current_label)
        layout.addWidget(self.latest_label)

        self.status_label = QLabel("Checking the GitHub release feed...")
        self.status_label.setStyleSheet("color: #8b90a3;")
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view, 1)

        buttons = QHBoxLayout()
        self.recheck_btn = QPushButton("Check Again")
        self.recheck_btn.clicked.connect(lambda: self._start_check(force=True))
        buttons.addWidget(self.recheck_btn)
        self.update_btn = QPushButton("Update Now")
        self.update_btn.setProperty("role", "primary")
        self.update_btn.setEnabled(False)
        self.update_btn.clicked.connect(self._start_install)
        buttons.addWidget(self.update_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        if start_check:
            self._start_check(force=False)

    def _start_check(self, force: bool) -> None:
        self._set_busy(True, "Checking the GitHub release feed...")
        self.update_btn.setEnabled(False)
        worker = UpdateCheckWorker(force=force)
        self._check_worker = worker
        worker.done.connect(self._on_check_done)
        worker.failed.connect(self._on_check_failed)
        worker.start()

    def _on_check_done(self, state: UpdateState) -> None:
        self._state = state
        self._set_busy(False, "")
        if state.error:
            self.status_label.setText(f"Could not reach the update feed: {state.error}")
            self.latest_label.setText("Latest: unknown")
            return
        latest = state.latest or "unknown"
        self.latest_label.setText(f"Latest: {latest}")
        if state.is_outdated:
            self.status_label.setText(f"A newer version ({latest}) is available.")
            self.update_btn.setEnabled(True)
        else:
            self.status_label.setText("DualForge is up to date.")
            self.update_btn.setEnabled(False)

    def _on_check_failed(self, message: str) -> None:
        self._set_busy(False, "")
        self.status_label.setText(f"Update check failed: {message}")
        self.latest_label.setText("Latest: unknown")

    def _start_install(self) -> None:
        self._set_busy(True, "Updating...")
        self.log_view.clear()
        self.log_view.setVisible(True)
        self.update_btn.setEnabled(False)
        worker = UpdateInstallWorker(pull=True)
        self._install_worker = worker
        worker.log.connect(self._append_log)
        worker.done.connect(self._on_install_done)
        worker.failed.connect(self._on_install_failed)
        worker.start()

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_install_done(self, root: Path) -> None:
        self._set_busy(False, "")
        self.status_label.setText("DualForge updated.")
        QMessageBox.information(
            self,
            "Update Complete",
            "DualForge has been updated.\n\nRestart the application to use the "
            "new version.",
        )
        self.log_view.setVisible(True)

    def _on_install_failed(self, message: str) -> None:
        self._set_busy(False, "")
        self.status_label.setText("Update failed.")
        self.recheck_btn.setEnabled(True)
        self.log_view.setVisible(True)
        self._append_log(message)
        QMessageBox.warning(self, "Update Failed", f"Could not update DualForge:\n{message}")

    def _set_busy(self, busy: bool, status: str) -> None:
        self.progress.setVisible(busy)
        self.recheck_btn.setEnabled(not busy)
        self.update_btn.setEnabled(False if busy else bool(
            self._state is not None and self._state.is_outdated and not self._state.error
        ))
        if status:
            self.status_label.setText(status)


__all__ = ["UpdateCheckWorker", "UpdateInstallWorker", "UpdateDialog"]