from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)
from PySide6.QtCore import Qt

from dualforge.drivers import registry
from dualforge.drivers.driver import DRIVER_FILE_SUFFIX


class DriversDialog(QDialog):
    """View, import, export and manage game drivers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Game Drivers")
        self.resize(760, 460)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Game drivers bundle engine type, encryption scheme, export format "
            "defaults and detection patterns for a specific game. Export them to "
            "share or back up, and import to add custom drivers."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        self.list_widget = QListWidget()
        self._rebuild_list()
        splitter.addWidget(self.list_widget)

        self.detail = QLabel()
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(
            "background: rgba(127,127,127,0.08); padding: 8px; border-radius: 6px;"
        )
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.list_widget.currentRowChanged.connect(self._show_detail)

        buttons = QHBoxLayout()
        import_btn = QPushButton("Import...")
        import_btn.setProperty("role", "primary")
        import_btn.clicked.connect(self._import_file)
        create_btn = QPushButton("Create from Archive...")
        create_btn.setToolTip(
            "Auto-build a driver from an archive file (engine, scheme, formats)"
        )
        create_btn.setProperty("role", "primary")
        create_btn.clicked.connect(self._create_from_archive)
        export_btn = QPushButton("Export Selected")
        export_btn.clicked.connect(self._export_selected)
        export_all_btn = QPushButton("Export All")
        export_all_btn.clicked.connect(self._export_all)
        export_builtin_btn = QPushButton("Export Built-in")
        export_builtin_btn.clicked.connect(self._export_builtin)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(import_btn)
        buttons.addWidget(create_btn)
        buttons.addWidget(export_btn)
        buttons.addWidget(export_all_btn)
        buttons.addWidget(export_builtin_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self._select_first()
        self._show_detail()

    # ── list / detail ────────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        self.list_widget.clear()
        for driver in sorted(registry.list(), key=lambda d: d.name):
            row = (
                f"{driver.name}\n"
                f"  {driver.label}  ·  {driver.engine}/{driver.encryption_scheme}"
            )
            item = QListWidgetItem(row)
            item.setData(Qt.ItemDataRole.UserRole, driver.name)
            self.list_widget.addItem(item)

    def _select_first(self) -> None:
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _current_driver(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return registry.get(name)

    def _show_detail(self, *args) -> None:
        driver = self._current_driver()
        if driver is None:
            self.detail.setText("Select a driver to see its details.")
            return
        lines = [f"<b>{driver.label}</b>  <code>({driver.name})</code>"]
        lines.append(f"<br>Engine: <b>{driver.engine}</b>")
        lines.append(f"Encryption scheme: <b>{driver.encryption_scheme}</b>")
        if driver.encryption_params:
            lines.append(
                "Scheme params: "
                + ", ".join(f"{k}={v}" for k, v in driver.encryption_params.items())
            )
        if driver.egame:
            lines.append(f"CUE4Parse EGame: <code>{driver.egame}</code>")
        if driver.usmap_required:
            lines.append("USMap required: yes")
        if driver.unity_cn:
            lines.append("Unity CN Pro: yes")
        if driver.export_formats:
            formats = ", ".join(f"{k}={v}" for k, v in driver.export_formats.items())
            lines.append(f"Export formats: {formats}")
        if driver.asset_filter:
            lines.append(f"Asset filter: {', '.join(driver.asset_filter)}")
        if driver.archive_patterns:
            lines.append(f"Archive patterns: {', '.join(driver.archive_patterns)}")
        if driver.game_fragments:
            lines.append(f"Game fragments: {', '.join(driver.game_fragments)}")
        if driver.notes:
            lines.append(f"<br><i>{driver.notes}</i>")
        if driver.tags:
            lines.append(f"<br>Tags: {', '.join(driver.tags)}")
        self.detail.setText("<br>".join(lines))

    # ── actions ──────────────────────────────────────────────────────

    def _import_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Import game driver",
            "",
            f"DualForge driver (*{DRIVER_FILE_SUFFIX});;All files (*)",
        )
        if not chosen:
            return
        try:
            driver = registry.load_file(chosen)
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        self._rebuild_list()
        self.detail.setText(f"Imported driver <b>{driver.name}</b> ({driver.label}).")

    def _export_selected(self) -> None:
        driver = self._current_driver()
        if driver is None:
            QMessageBox.information(self, "Export", "Select a driver to export.")
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Export game driver",
            f"{driver.name}{DRIVER_FILE_SUFFIX}",
            f"DualForge driver (*{DRIVER_FILE_SUFFIX});;All files (*)",
        )
        if not chosen:
            return
        try:
            written = registry.save(driver, chosen)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        self.detail.setText(f"Exported driver to <code>{written}</code>.")

    def _create_from_archive(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Create driver from archive",
            "",
            "Game archives (*.pak *.utoc *.ucas *.unity3d *.bundle *.assets *.assetbundle);;All files (*)",
        )
        if not chosen:
            return
        from dualforge.drivers import build_driver_from_archive

        try:
            driver = build_driver_from_archive(chosen)
        except Exception as exc:
            QMessageBox.warning(self, "Create failed", str(exc))
            return
        if registry.get(driver.name) is not None:
            driver.name = f"{driver.name}-auto"
        registry.register(driver)
        self._rebuild_list()
        self._select_named(driver.name)
        self.detail.setText(
            f"<b>Auto-created driver</b> from archive.<br><br>"
            f"Engine: <b>{driver.engine}</b><br>"
            f"Scheme: <b>{driver.encryption_scheme}</b><br>"
            f"Review and export it to save as a file."
        )

    def _select_named(self, name: str) -> None:
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == name:
                self.list_widget.setCurrentRow(i)
                return

    def _export_all(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose export folder", Path.home().as_posix()
        )
        if not folder:
            return
        count = registry.export_all(folder)
        self.detail.setText(f"<b>Exported {count} driver(s)</b> to <code>{folder}</code>.")

    def _export_builtin(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose export folder", Path.home().as_posix()
        )
        if not folder:
            return
        count = registry.export_builtin(folder)
        self.detail.setText(f"<b>Exported {count} built-in driver(s)</b> to <code>{folder}</code>.")


__all__ = ["DriversDialog"]
