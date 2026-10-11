"""A small form dialog for reviewing/editing a game driver before saving it.

Used by the Drivers dialog for both "create from archive" (review + correct
the auto-detected facts) and editing an existing driver.
"""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from dualforge.drivers.driver import GameDriver


def _scheme_choices() -> list[str]:
    """Registered encryption schemes plus presets/stage names a user may type."""
    names: set[str] = {
        "aes-256",
        "aes-256+xor8",
        "derived-aes-md5",
        "partial-encrypt",
        "custom-aes-round",
        "derived-xor-md5",
        "unity-cn",
        "sm4",
        "sm4-abi",
    }
    try:
        from dualforge.encryption import list_schemes

        names.update(list_schemes())
    except Exception:
        pass
    try:
        from dualforge.encryption.presets import PRESETS

        names.update(p.name for p in PRESETS)
        names.update(name for p in PRESETS for name in p.stages)
    except Exception:
        pass
    return sorted(names)


def _egame_choices() -> list[str]:
    """Known CUE4Parse EGame names from the adapter mappings and built-ins."""
    games = {
        "GAME_UE4_27",
        "GAME_UE5_0",
        "GAME_UE5_1",
        "GAME_UE5_2",
        "GAME_UE5_3",
        "GAME_UE5_4",
        "GAME_UE5_5",
        "GAME_TEKKEN7",
        "GAME_ArenaBreakoutInfinite",
    }
    try:
        from dualforge.unreal.uex_adapter import FOLDER_GAMES, SCHEME_GAMES

        games.update(game for _, game in FOLDER_GAMES)
        games.update(SCHEME_GAMES.values())
    except Exception:
        pass
    try:
        from dualforge.drivers.defaults import BUILTIN_DRIVERS

        games.update(d.egame for d in BUILTIN_DRIVERS if getattr(d, "egame", ""))
    except Exception:
        pass
    return sorted(games)


class DriverEditDialog(QDialog):
    """Review/edit a :class:`GameDriver` before it is saved and registered."""

    def __init__(self, driver: GameDriver, mode: str = "edit", parent=None):
        super().__init__(parent)
        self._driver = driver
        self.setWindowTitle("Create Driver" if mode == "create" else "Edit Driver")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        if mode == "create":
            hint = QLabel(
                "Auto-detected from the archive. Review the encryption scheme and "
                "CUE4Parse EGame - for encrypted titles the scheme matters (e.g. "
                "Arena Breakout: Infinite uses <b>sm4-abi</b>)."
            )
        else:
            hint = QLabel("Edit the driver facts below and save. Everything is stored as JSON.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()

        self.name_edit = QLineEdit(driver.name)
        self.label_edit = QLineEdit(driver.label)

        self.engine_combo = QComboBox()
        for engine in ("auto", "unity", "unreal", "bethesda", "cdpr"):
            self.engine_combo.addItem(engine)
        index = self.engine_combo.findText(driver.engine)
        self.engine_combo.setCurrentIndex(index if index >= 0 else 0)

        self.scheme_combo = QComboBox()
        self.scheme_combo.setEditable(True)
        self.scheme_combo.addItems(_scheme_choices())
        if driver.encryption_scheme:
            self.scheme_combo.setCurrentText(driver.encryption_scheme)

        self.egame_combo = QComboBox()
        self.egame_combo.setEditable(True)
        self.egame_combo.addItem("")
        self.egame_combo.addItems(_egame_choices())
        if driver.egame:
            self.egame_combo.setCurrentText(driver.egame)

        self.patterns_edit = QLineEdit(", ".join(driver.archive_patterns))
        self.patterns_edit.setPlaceholderText("e.g. pakchunk*-WindowsNoEditor*.pak")
        self.fragments_edit = QLineEdit(", ".join(driver.game_fragments))
        self.fragments_edit.setPlaceholderText("e.g. ABInfinite, Arena Breakout Infinite")

        self.usmap_check = QCheckBox("Requires a .usmap mappings file")
        self.usmap_check.setChecked(driver.usmap_required)
        self.unity_cn_check = QCheckBox("Unity CN Pro bundle")
        self.unity_cn_check.setChecked(driver.unity_cn)

        self.notes_edit = QPlainTextEdit(driver.notes)
        self.notes_edit.setMaximumHeight(90)

        form.addRow("Name", self.name_edit)
        form.addRow("Label", self.label_edit)
        form.addRow("Engine", self.engine_combo)
        form.addRow("Encryption scheme", self.scheme_combo)
        form.addRow("CUE4Parse EGame", self.egame_combo)
        form.addRow("Archive patterns", self.patterns_edit)
        form.addRow("Game fragments", self.fragments_edit)
        form.addRow("", self.usmap_check)
        form.addRow("", self.unity_cn_check)
        form.addRow("Notes", self.notes_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save & Use")
        save_btn.setProperty("role", "primary")
        save_btn.clicked.connect(self._accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        self.resized = None

    def _split_list(self, text: str) -> list[str]:
        return [part.strip() for part in text.split(",") if part.strip()]

    def _accept(self) -> None:
        name = self.name_edit.text().strip()
        scheme = self.scheme_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "Driver", "A driver name is required.")
            return
        if not scheme:
            QMessageBox.warning(self, "Driver", "An encryption scheme is required (try aes-256).")
            return
        self.accept()

    @property
    def driver(self) -> GameDriver:
        """The edited driver (mirrors the source one plus these field changes)."""
        driver = deepcopy(self._driver)
        driver.name = self.name_edit.text().strip() or driver.name
        driver.label = self.label_edit.text().strip() or driver.label
        driver.engine = self.engine_combo.currentText()
        driver.encryption_scheme = self.scheme_combo.currentText().strip()
        driver.egame = self.egame_combo.currentText().strip()
        driver.archive_patterns = self._split_list(self.patterns_edit.text())
        driver.game_fragments = self._split_list(self.fragments_edit.text())
        driver.usmap_required = self.usmap_check.isChecked()
        driver.unity_cn = self.unity_cn_check.isChecked()
        driver.notes = self.notes_edit.toPlainText().strip()
        return driver


__all__ = ["DriverEditDialog"]