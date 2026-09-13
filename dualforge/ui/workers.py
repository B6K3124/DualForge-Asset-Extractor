"""Background extraction worker (QThread) for the main window.

Computing-free binding between the UI thread and :func:`dualforge.extract.extract_file`:
the GUI kicks off an :class:`ExtractWorker`, feeds it (archives x files) plus output
settings, and receives progress / finished / cancelled / failed signals.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from dualforge.extract import ExtractCancelled, ExtractOptions, extract_file
from dualforge.log import get_logger

logger = get_logger(__name__)


class WorkerSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(int, list, list)
    cancelled = Signal()
    failed = Signal(str)


class ExtractWorker(QThread):
    def __init__(
        self,
        paths: list[str],
        out_dir: str,
        aes_key: str | None,
        types: list[str] | None,
        files_by_archive: dict[str, list[str] | None],
        formats: dict | None,
        usmap: str | None = None,
    ):
        super().__init__()
        self.paths = paths
        self.out_dir = out_dir
        self.aes_key = aes_key
        self.types = types
        self.files_by_archive = files_by_archive
        self.formats = formats
        self.usmap = usmap
        self._signals = WorkerSignals()
        self.progress = self._signals.progress
        self.finished = self._signals.finished
        self.cancelled = self._signals.cancelled
        self.failed = self._signals.failed
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        extracted: list[str] = []
        errors: list[str] = []
        total = sum(len(files) for files in self.files_by_archive.values() if files)
        done = 0
        cancelled = False
        for path in self.paths:
            files = self.files_by_archive.get(path)
            options = ExtractOptions(
                out_dir=self.out_dir,
                aes_key=self.aes_key,
                type_filter=tuple(self.types) if self.types else None,
                files=files,
                formats=self.formats,
                usmap=self.usmap,
                progress=lambda i, t, m, _d=done: self.progress.emit(_d + i, max(total, 1), m),
                is_cancelled=self._cancel_event.is_set,
            )
            try:
                result = extract_file(path, options)
            except ExtractCancelled:
                cancelled = True
                break
            except Exception as exc:
                self.failed.emit(f"{Path(path).name}: {exc}")
                return
            else:
                extracted.extend(result.extracted)
                errors.extend(result.errors)
            done += len(files) if files else 1
        if cancelled:
            self.cancelled.emit()
            return
        self._write_manifest(extracted, errors)
        self.finished.emit(len(extracted), extracted, errors)

    def _write_manifest(self, extracted: list[str], errors: list[str]) -> None:
        manifest = {
            "tool": "DualForge",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "out_dir": self.out_dir,
            "archives": self.paths,
            "files": extracted,
            "errors": errors,
        }
        try:
            import json

            target = Path(self.out_dir) / "_dualforge_manifest.json"
            target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except OSError:
            pass


__all__ = ["ExtractWorker", "WorkerSignals"]