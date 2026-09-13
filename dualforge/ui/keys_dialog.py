from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from dualforge.log import get_logger

logger = get_logger(__name__)


class KeyDialog(QDialog):
    def __init__(self, parent=None, scheme: str = "aes-256"):
        super().__init__(parent)
        self.setWindowTitle("Add Key")
        self.setMinimumWidth(420)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("e.g. Delta Force")
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("64 hex characters (AES-256) or scheme-specific key")
        self.engine_edit = QLineEdit("unreal")
        self.scheme_combo = QComboBox()
        self._populate_schemes(scheme)
        self.guid_edit = QLineEdit()
        self.guid_edit.setPlaceholderText("encryption key GUID (dynamic-key games) - optional")
        self.params_edit = QLineEdit()
        self.params_edit.setPlaceholderText("comma-separated, e.g. xor_key=1122334455667788")

        form = QFormLayout()
        form.addRow("Game title", self.title_edit)
        form.addRow("Key", self.key_edit)
        form.addRow("Engine", self.engine_edit)
        form.addRow("Scheme", self.scheme_combo)
        form.addRow("GUID", self.guid_edit)
        form.addRow("Parameters", self.params_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _populate_schemes(self, selected: str) -> None:
        try:
            from dualforge.encryption.registry import list_schemes
            from dualforge.encryption.presets import PRESETS

            names = list_schemes()
            extra = [p.name for p in PRESETS if p.name not in names]
            ordered = sorted(names + extra, key=lambda s: (s != "aes-256", s.lower()))
        except Exception:
            logger.debug("scheme list unavailable; falling back to aes-256", exc_info=True)
            ordered = ["aes-256"]
        self.scheme_combo.addItems(ordered or ["aes-256"])
        index = ordered.index(selected) if selected in ordered else 0
        self.scheme_combo.setCurrentIndex(index)

    def values(self):
        return (
            self.title_edit.text().strip(),
            self.key_edit.text().strip(),
            self.engine_edit.text().strip(),
            self.scheme_combo.currentText(),
            self.guid_edit.text().strip(),
            self.params_edit.text().strip(),
        )


__all__ = ["KeyDialog"]
