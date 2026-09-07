from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

DEFAULT_SETTINGS_PATH = Path.home() / ".dualforge" / "settings.json"
DEFAULT_CACHE_DIR = Path.home() / ".dualforge" / "preview_cache"


@dataclass
class Settings:
    theme: str = "dark"
    default_out_dir: str = ""
    cue4parse_path: str = ""
    usmap_path: str = ""
    vgmstream_path: str = ""
    preview_cache_dir: str = ""
    default_aes_key: str = ""
    try_all_keys: bool = True
    sync_endpoints: List[str] = field(default_factory=list)
    donation_url: str = "https://ko-fi.com/b6000"
    export_formats: Dict[str, str] = field(default_factory=dict)
    profiles: List[dict] = field(default_factory=list)
    recent_files: List[str] = field(default_factory=list)
    window_geometry: str = ""
    window_state: str = ""
    _path: str = field(default="", repr=False, compare=False)

    def cache_dir(self) -> str:
        if self.preview_cache_dir:
            return self.preview_cache_dir
        return str(DEFAULT_CACHE_DIR)

    def add_recent(self, path: str) -> None:
        if path in self.recent_files:
            self.recent_files.remove(path)
        self.recent_files.insert(0, path)
        del self.recent_files[10:]

    @classmethod
    def load(cls, path: str | None = None) -> "Settings":
        settings = cls()
        settings._path = path or str(DEFAULT_SETTINGS_PATH)
        try:
            raw = json.loads(Path(settings._path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        for key in asdict(settings):
            if key in raw:
                value = raw[key]
                if key == "donation_url" and not str(value or "").strip():
                    continue
                setattr(settings, key, value)
        return settings

    def save(self) -> None:
        path = Path(self._path or str(DEFAULT_SETTINGS_PATH))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = asdict(self)
            payload.pop("_path", None)
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DualForge Settings")
        self.resize(460, 320)
        self.settings = settings

        form = QFormLayout()
        form.setSpacing(10)

        self.theme_combo = QComboBox()
        from dualforge.ui.theme import available_themes

        for key, label in available_themes().items():
            self.theme_combo.addItem(label, key)
        index = self.theme_combo.findData(settings.theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)
        form.addRow("Theme", self.theme_combo)

        self.out_dir_edit = self._path_field(settings.default_out_dir, is_dir=True)
        form.addRow("Default output folder", self.out_dir_edit)

        self.cue4parse_edit = self._path_field(settings.cue4parse_path, is_dir=False)
        form.addRow("CUE4Parse CLI (uex)", self.cue4parse_edit)
        self.usmap_edit = self._path_field(settings.usmap_path, is_dir=False)
        form.addRow("USMap (UE5 packages)", self.usmap_edit)

        self.vgmstream_edit = self._path_field(settings.vgmstream_path, is_dir=False)
        form.addRow("vgmstream", self.vgmstream_edit)

        self.cache_edit = self._path_field(settings.cache_dir(), is_dir=True)
        form.addRow("Preview cache folder", self.cache_edit)

        self.aes_edit = QLineEdit(settings.default_aes_key)
        self.aes_edit.setPlaceholderText("64 hex characters (AES-256), optional")
        form.addRow("Default AES key", self.aes_edit)

        self.try_all_check = QCheckBox("Try every key from the key store before failing")
        self.try_all_check.setChecked(settings.try_all_keys)
        form.addRow("Key probing", self.try_all_check)

        self.import_keys_btn = QPushButton("Import AES Keys JSON...")
        self.import_keys_btn.setProperty("role", "primary")
        self.import_keys_btn.clicked.connect(self._import_keys_file)
        form.addRow("Key file (FModel)", self.import_keys_btn)

        from dualforge.unreal.keys import DEFAULT_ENDPOINTS

        self.endpoints_edit = QLineEdit(
            ", ".join(settings.sync_endpoints or DEFAULT_ENDPOINTS)
        )
        self.endpoints_edit.setPlaceholderText("comma-separated community key endpoints")
        form.addRow("Sync endpoints", self.endpoints_edit)

        self.donation_edit = QLineEdit(settings.donation_url)
        self.donation_edit.setPlaceholderText("https://ko-fi.com/... or https://paypal.me/...")
        form.addRow("Donation URL", self.donation_edit)

        from dualforge.export.convert import DEFAULT_FORMATS, format_choices

        self.format_combos: Dict[str, QComboBox] = {}
        for type_name, label in (
            ("Texture2D", "Texture format"),
            ("Sprite", "Sprite format"),
            ("AudioClip", "Audio format"),
            ("Mesh", "Mesh format"),
            ("AnimationClip", "Animation format"),
            ("Cubemap", "Cubemap format"),
            ("VideoClip", "Video format"),
            ("SpriteAtlas", "Sprite atlas format"),
            ("AnimatorController", "Animator format"),
            ("Avatar", "Avatar format"),
            ("LightmapData", "Lightmap format"),
        ):
            combo = QComboBox()
            for choice in format_choices(type_name):
                combo.addItem(choice, choice)
            default = settings.export_formats.get(type_name) or DEFAULT_FORMATS.get(type_name, "bin")
            index = combo.findData(default)
            combo.setCurrentIndex(index if index >= 0 else 0)
            self.format_combos[type_name] = combo
            form.addRow(label, combo)

        note = QLabel("Keys are stored locally in plain text under ~/.dualforge.")
        note.setStyleSheet("color: gray;")
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("role", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _import_keys_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Import AES keys", "", "JSON files (*.json);;All files (*)"
        )
        if not chosen:
            return
        from dualforge.unreal import KeyStore

        try:
            count = KeyStore().import_fmodel_json(chosen)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Import failed", str(exc))
            return
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(
            self, "Keys imported", f"Imported {count} keys from the file."
        )

    def _path_field(self, value: str, is_dir: bool):
        edit = QLineEdit(value)
        button = QPushButton("Browse...")
        button.setProperty("role", "primary")

        def browse() -> None:
            if is_dir:
                chosen = QFileDialog.getExistingDirectory(self, "Choose folder")
            else:
                chosen, _ = QFileDialog.getOpenFileName(self, "Choose executable")
            if chosen:
                edit.setText(chosen)

        button.clicked.connect(browse)
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(button)
        return row

    def values(self) -> Settings:

        self.settings.theme = self.theme_combo.currentData()
        self.settings.default_out_dir = self.out_dir_edit.itemAt(0).widget().text().strip()
        self.settings.cue4parse_path = self.cue4parse_edit.itemAt(0).widget().text().strip()
        self.settings.usmap_path = self.usmap_edit.itemAt(0).widget().text().strip()
        self.settings.vgmstream_path = self.vgmstream_edit.itemAt(0).widget().text().strip()
        self.settings.preview_cache_dir = self.cache_edit.itemAt(0).widget().text().strip()
        self.settings.default_aes_key = self.aes_edit.text().strip()
        self.settings.try_all_keys = self.try_all_check.isChecked()
        self.settings.sync_endpoints = [
            endpoint.strip()
            for endpoint in self.endpoints_edit.text().split(",")
            if endpoint.strip()
        ]
        self.settings.donation_url = self.donation_edit.text().strip()
        self.settings.export_formats = {
            type_name: combo.currentData()
            for type_name, combo in self.format_combos.items()
        }
        return self.settings


__all__ = ["DEFAULT_CACHE_DIR", "DEFAULT_SETTINGS_PATH", "Settings", "SettingsDialog"]
