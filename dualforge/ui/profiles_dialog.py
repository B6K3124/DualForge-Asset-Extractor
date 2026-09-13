from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from dualforge.ui.settings import Settings


class ProfilesDialog(QDialog):
    """Manage per-game profiles (folder + AES key + output dir)."""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game Profiles")
        self.resize(480, 360)
        self.settings = settings

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Profiles remember a game folder, its AES key and output folder, "
            "so re-opening a game is one click."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self._rebuild()
        layout.addWidget(self.list_widget, 1)

        buttons = QHBoxLayout()
        add_btn = QPushButton("Add Profile...")
        add_btn.setProperty("role", "primary")
        add_btn.clicked.connect(self._add)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove)
        load_btn = QPushButton("Open Game Folder")
        load_btn.setProperty("role", "primary")
        load_btn.clicked.connect(self._load)
        buttons.addWidget(add_btn)
        buttons.addWidget(remove_btn)
        buttons.addWidget(load_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self._profile_to_load: dict | None = None

    def _rebuild(self) -> None:
        self.list_widget.clear()
        for profile in self.settings.profiles:
            row = f"{profile.get('name', '?')}  -  {profile.get('folder', '')}"
            key = profile.get("aes_key", "")
            if key:
                row += f"  [AES: {key[:8]}...]"
            self.list_widget.addItem(row)

    def _selected(self) -> dict | None:
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self.settings.profiles):
            return None
        return self.settings.profiles[row]

    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        if not ok or not name.strip():
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose the game folder")
        if not folder:
            return
        profile = {
            "name": name.strip(),
            "folder": folder,
            "aes_key": self.settings.default_aes_key,
            "out_dir": self.settings.default_out_dir,
        }
        self.settings.profiles.append(profile)
        self.settings.save()
        self._rebuild()

    def _remove(self) -> None:
        profile = self._selected()
        if profile is None:
            return
        self.settings.profiles.remove(profile)
        self.settings.save()
        self._rebuild()

    def _load(self) -> None:
        profile = self._selected()
        if profile is None:
            QMessageBox.information(self, "Profiles", "Select a profile first.")
            return
        folder = profile.get("folder", "")
        if not Path(folder).is_dir():
            QMessageBox.warning(self, "Profiles", "The profile folder no longer exists.")
            return
        self.settings.default_aes_key = profile.get("aes_key", "")
        if profile.get("out_dir"):
            self.settings.default_out_dir = profile["out_dir"]
        self.settings.save()
        self._profile_to_load = profile
        self.accept()


__all__ = ["ProfilesDialog"]