from __future__ import annotations

import threading
from html import escape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QByteArray, QThread, QObject, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dualforge.detector import detect
from dualforge.extract import ExtractCancelled, ExtractOptions, extract_file
from dualforge.ui.branding import make_app_icon, make_folder_icon, make_toolbar_icon
from dualforge.ui.keys_dialog import KeyDialog
from dualforge.ui.preview import PreviewItem, PreviewPanel
from dualforge.ui.profiles_dialog import ProfilesDialog
from dualforge.ui.settings import Settings, SettingsDialog
from dualforge.ui.stats_dialog import StatsDialog
from dualforge.ui.theme import DARK, LIGHT, apply_theme, available_themes
from dualforge.ui.tree_builder import (
    USER_ROLE,
    AssetTreeBuilder,
    apply_filter,
    checked_leaves,
    iter_leaves,
    set_all_checkstates,
)
from dualforge.unity import UnityArchive
from dualforge.unreal import KeyStore, PakArchive, PakError, UnrealBridge

ARCHIVE_SUFFIXES = (".pak", ".utoc", ".ucas", ".unity3d", ".unityweb", ".bundle", ".assets", ".assetbundle", ".archive", ".bsa", ".ba2")

_BETHESDA_KINDS = {
    ".dds": "texture",
    ".png": "texture",
    ".jpg": "texture",
    ".jpeg": "texture",
    ".tga": "texture",
    ".bmp": "texture",
    ".nif": "mesh",
    ".wav": "audio",
    ".fuz": "audio",
    ".xwm": "audio",
    ".mp3": "audio",
    ".ogg": "audio",
    ".txt": "text",
    ".json": "text",
    ".xml": "text",
    ".csv": "text",
    ".hlsl": "text",
    ".fx": "text",
    ".hkx": "anim",
    ".kf": "anim",
    ".bto": "terrain",
    ".btr": "terrain",
}


def _bethesda_kind(name: str) -> str:
    return _BETHESDA_KINDS.get(Path(name).suffix.lower(), "file")


class WorkerSignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(int, list, list)
    cancelled = Signal()
    failed = Signal(str)


class ExtractWorker(QThread):
    def __init__(
        self,
        paths: List[str],
        out_dir: str,
        aes_key: Optional[str],
        types: Optional[List[str]],
        files_by_archive: Dict[str, Optional[List[str]]],
        formats: Optional[dict],
        usmap: Optional[str] = None,
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
        extracted: List[str] = []
        errors: List[str] = []
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

    def _write_manifest(self, extracted: List[str], errors: List[str]) -> None:
        from datetime import datetime

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


def _type_icon(kind: str) -> QIcon:
    colors = {
        "Texture2D": "#4fae6d",
        "Sprite": "#4fae6d",
        "AudioClip": "#4f8fd0",
        "Mesh": "#b06fd0",
        "TextAsset": "#e0a53c",
        "Shader": "#e05252",
        "Material": "#e05252",
        "MonoBehaviour": "#4fd0c4",
    }
    color = colors.get(kind, "#8b90a3" if kind != "file" else "#5a5f73")
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, 12, 12, 3, 3)
    painter.end()
    return QIcon(pixmap)


_REPLACE_TEXTURE = "Texture2D"
_REPLACE_TEXT = "TextAsset"
_REPLACE_FONT = "Font"


class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.setWindowTitle("DualForge - Unity & Unreal Asset Extractor")
        self.resize(1280, 820)
        self.setMinimumSize(960, 640)
        self.setWindowIcon(make_app_icon())

        self.current_path: Optional[str] = None
        self.current_executable: Optional[str] = None
        self.current_engine: Optional[str] = None
        self.current_driver = None
        self._usmap_prompt_done = False
        self.unity_archive: Optional[UnityArchive] = None
        self._unity_archives: Dict[str, UnityArchive] = {}
        self.unity_assets: Dict[Tuple[str, str], object] = {}
        self._unity_engine_versions: Dict[str, Tuple[str, int]] = {}
        self.pak_archives: Dict[str, PakArchive] = {}
        self._cdpr_archives: Dict[str, object] = {}
        self._bethesda_archives: Dict[str, object] = {}
        self.unreal_entries: List[str] = []
        self._tree_builder: Optional[AssetTreeBuilder] = None
        self._folder_mode = False
        self._open_archives: List[str] = []
        self.worker: Optional[ExtractWorker] = None
        self._last_out_dir: Optional[str] = None
        self._recent_menu: Optional[QMenu] = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._show_preview)
        self._progress_bar: Optional[QProgressBar] = None
        self._cancel_button: Optional[QPushButton] = None
        self._toolbar_actions: Dict[str, QAction] = {}
        self._donate_button: Optional[QPushButton] = None

        self.setAcceptDrops(True)
        self._build_ui()
        self._build_menus()
        self._build_toolbar()
        self._build_statusbar()
        self._rebuild_recent()
        self._restore_window_state()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview_panel = PreviewPanel(self.settings.cache_dir())
        layout.addWidget(self.preview_panel)
        self.preview_panel.show_hero(pixmap=self._hero_pixmap())
        self.preview_panel.hero_page.open_requested.connect(self.open_archive)

        self.assets_dock = QDockWidget("Assets", self)
        self.assets_dock.setObjectName("assets_dock")
        assets_widget = QWidget()
        assets_layout = QVBoxLayout(assets_widget)
        assets_layout.setContentsMargins(6, 6, 6, 6)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search assets...  (Ctrl+F)")
        self.search_edit.textChanged.connect(self._apply_filter)
        assets_layout.addWidget(self.search_edit)

        filter_row = QHBoxLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem("All types", None)
        self.type_combo.currentIndexChanged.connect(self._apply_filter)
        self.regex_check = QCheckBox("Regex")
        self.regex_check.toggled.connect(self._apply_filter)
        check_all_btn = QPushButton("Check All")
        check_all_btn.setToolTip("Check every visible asset for extraction")
        check_all_btn.clicked.connect(lambda: self._check_all(True))
        check_none_btn = QPushButton("None")
        check_none_btn.setToolTip("Uncheck every asset")
        check_none_btn.clicked.connect(lambda: self._check_all(False))
        filter_row.addWidget(self.type_combo)
        filter_row.addWidget(self.regex_check)
        filter_row.addStretch(1)
        filter_row.addWidget(check_all_btn)
        filter_row.addWidget(check_none_btn)
        assets_layout.addLayout(filter_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Path", "Type", "Size"])
        self.tree.setColumnWidth(0, 460)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._schedule_preview)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        assets_layout.addWidget(self.tree, 1)
        self.assets_dock.setWidget(assets_widget)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.assets_dock)

        self.properties_dock = QDockWidget("Properties", self)
        self.properties_dock.setObjectName("properties_dock")
        props_widget = QWidget()
        props_layout = QVBoxLayout(props_widget)
        props_layout.setContentsMargins(6, 6, 6, 6)
        from PySide6.QtWidgets import QSplitter

        self.props_splitter = QSplitter(Qt.Orientation.Vertical, props_widget)
        self.properties_table = QTableWidget(0, 2)
        self.properties_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.properties_table.horizontalHeader().setStretchLastSection(True)
        self.properties_table.setColumnWidth(0, 130)
        self.properties_table.verticalHeader().setVisible(False)
        from dualforge.ui.widgets import InspectorTree

        self.inspector_tree = InspectorTree()
        self.inspector_tree.setObjectName("inspector_tree")
        self.props_splitter.addWidget(self.properties_table)
        self.props_splitter.addWidget(self.inspector_tree)
        self.props_splitter.setStretchFactor(0, 0)
        self.props_splitter.setStretchFactor(1, 1)
        self.props_splitter.setSizes([120, 240])
        props_layout.addWidget(self.props_splitter)
        self.properties_dock.setWidget(props_widget)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.properties_dock)
        self.preview_panel.typetree_loaded.connect(self._on_typetree_loaded)

        self.log_dock = QDockWidget("Log", self)
        self.log_dock.setObjectName("log_dock")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log_dock.setWidget(self.log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks(
            [self.properties_dock, self.log_dock],
            [180, 120],
            Qt.Orientation.Vertical,
        )

    def _hero_pixmap(self) -> QPixmap:
        from dualforge.ui.branding import make_app_icon

        return make_app_icon(96).pixmap(96, 96)

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        open_action = QAction("Open Archive...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_archive)
        file_menu.addAction(open_action)
        folder_action = QAction("Open Folder...", self)
        folder_action.setShortcut("Ctrl+Shift+O")
        folder_action.triggered.connect(self.open_folder)
        file_menu.addAction(folder_action)
        self._recent_menu = file_menu.addMenu("Open &Recent")
        file_menu.addSeparator()
        export_action = QAction("Export Selected...", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.extract_selected)
        file_menu.addAction(export_action)
        extract_action = QAction("Extract All...", self)
        extract_action.setShortcut("Ctrl+Shift+E")
        extract_action.triggered.connect(self.extract_all)
        file_menu.addAction(extract_action)
        file_menu.addSeparator()
        keys_action = QAction("Manage Keys...", self)
        keys_action.triggered.connect(self.manage_keys)
        file_menu.addAction(keys_action)
        profiles_action = QAction("Game Profiles...", self)
        profiles_action.triggered.connect(self.open_profiles)
        file_menu.addAction(profiles_action)
        settings_action = QAction("Settings...", self)
        settings_action.triggered.connect(self.open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("&View")
        stats_action = QAction("Asset Statistics...", self)
        stats_action.triggered.connect(self.show_stats)
        view_menu.addAction(stats_action)
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("&Theme")
        theme_group = QActionGroup(self)
        theme_group.setExclusive(True)
        for key, label in available_themes().items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self.settings.theme == key)
            action.setData(key)
            action.triggered.connect(self._change_theme)
            theme_group.addAction(action)
            theme_menu.addAction(action)
        view_menu.addSeparator()
        for dock in (self.assets_dock, self.properties_dock, self.log_dock):
            action = dock.toggleViewAction()
            view_menu.addAction(action)

        tools_menu = menu_bar.addMenu("&Tools")
        ghidra_action = QAction("Ghidra Key Hunt...", self)
        ghidra_action.setToolTip(
            "Launch headless Ghidra to find hardcoded AES keys in a game binary"
        )
        ghidra_action.triggered.connect(self.open_ghidra_hunt)
        tools_menu.addAction(ghidra_action)
        usmap_action = QAction("Generate USMAP from Running Game...", self)
        usmap_action.setToolTip(
            "Dump the FNamePool of a running UE5 game into a local .usmap file"
        )
        usmap_action.triggered.connect(self.open_usmap_dump)
        tools_menu.addAction(usmap_action)
        tools_menu.addSeparator()
        drivers_action = QAction("Game Drivers...", self)
        drivers_action.setToolTip(
            "View, import and export game driver configs (engine, scheme, formats)"
        )
        drivers_action.triggered.connect(self.open_drivers)
        tools_menu.addAction(drivers_action)

        help_menu = menu_bar.addMenu("&Help")
        compat_action = QAction("Video Game Compatibility...", self)
        compat_action.setToolTip(
            "Compatibility guide for supported engines, formats and game drivers"
        )
        compat_action.triggered.connect(self.open_compatibility)
        help_menu.addAction(compat_action)
        about_action = QAction("About DualForge", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        self._extract_action = extract_action
        self._export_action = export_action
        self._keys_action = keys_action

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("main_toolbar")
        toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        color = self._toolbar_icon_color()
        open_btn = toolbar.addAction(make_toolbar_icon("open", color), "Open")
        open_btn.triggered.connect(self.open_archive)
        folder_btn = toolbar.addAction(make_toolbar_icon("folder", color), "Folder")
        folder_btn.triggered.connect(self.open_folder)
        toolbar.addSeparator()
        extract_btn = toolbar.addAction(make_toolbar_icon("extract", color), "Extract")
        extract_btn.triggered.connect(self.extract_all)
        export_btn = toolbar.addAction(make_toolbar_icon("extract", color), "Export Selected")
        export_btn.triggered.connect(self.extract_selected)
        toolbar.addSeparator()
        keys_btn = toolbar.addAction(make_toolbar_icon("keys", color), "Keys")
        keys_btn.triggered.connect(self.manage_keys)
        self._toolbar_extract = extract_btn
        self._toolbar_actions = {
            "open": open_btn,
            "folder": folder_btn,
            "extract": extract_btn,
            "extract2": export_btn,
            "keys": keys_btn,
        }

        toolbar.addSeparator()
        donate_btn = QPushButton("Donate")
        donate_btn.setIcon(make_toolbar_icon("donate", self._donate_icon_color()))
        donate_btn.setProperty("role", "primary")
        donate_btn.setToolTip("Support DualForge - open the donation page")
        donate_btn.clicked.connect(self.donate)
        toolbar.addWidget(donate_btn)
        self._donate_button = donate_btn

    def _toolbar_icon_color(self) -> str:
        return (DARK if self.settings.theme == "dark" else LIGHT).text

    def _donate_icon_color(self) -> str:
        return (DARK if self.settings.theme == "dark" else LIGHT).accent_text

    def _refresh_toolbar_icons(self) -> None:
        color = self._toolbar_icon_color()
        for name, action in self._toolbar_actions.items():
            action.setIcon(make_toolbar_icon(name, color))
        if self._donate_button is not None:
            self._donate_button.setIcon(make_toolbar_icon("donate", self._donate_icon_color()))

    def _build_statusbar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        self.engine_badge = QLabel()
        self.engine_badge.setProperty("role", "badge")
        self.driver_badge = QLabel()
        self.driver_badge.setProperty("role", "badge")
        self.driver_badge.setTextFormat(Qt.TextFormat.RichText)
        self.driver_badge.setOpenExternalLinks(False)
        self.driver_badge.linkActivated.connect(self.open_drivers)
        self.driver_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.driver_badge.setToolTip("Click to manage game drivers")
        self.item_count = QLabel()
        self.item_count.setProperty("role", "badge")
        self.preview_note = QLabel("Ready")
        self.format_chips = QLabel("")
        self.format_chips.setProperty("role", "badge")
        status.addWidget(self.engine_badge)
        status.addWidget(self.driver_badge)
        status.addWidget(self.item_count)
        status.addWidget(self.format_chips)
        status.addPermanentWidget(self.preview_note)
        self._set_engine(None)
        self._set_driver(None)

    def _set_driver(self, driver) -> None:
        self.current_driver = driver
        if driver is None:
            self.driver_badge.setText("")
            self.driver_badge.setStyleSheet("")
            self.driver_badge.setToolTip("Click to manage game drivers")
            return
        name = escape(driver.name)
        parts = [p for p in (getattr(driver, "label", None), getattr(driver, "engine", None), getattr(driver, "encryption_scheme", None)) if p]
        detail = " · ".join(parts)
        text = f'driver: <a href="drivers" style="color:#e0a53c;">{name}</a>'
        if detail:
            text += f' <span style="color:#c8cbd8;">· {escape(detail)}</span>'
        self.driver_badge.setText(text)
        self.driver_badge.setStyleSheet("")
        self.driver_badge.setToolTip(
            getattr(driver, "notes", None)
            or "Click to manage game drivers"
        )

    def _set_engine(self, engine: Optional[str], detail: str = "") -> None:
        colors = {"unity": "#4fae6d", "unreal": "#4f8fd0", "container": "#e0a53c"}
        if engine is None:
            self.engine_badge.setText("No archive")
            self.engine_badge.setStyleSheet("color: #8b90a3;")
            self.item_count.setText("")
            self._refresh_format_chips()
            return
        label = {"unity": "Unity", "unreal": "Unreal", "container": "Container"}.get(engine, engine.title())
        if detail:
            label += f" · {detail}"
        self.engine_badge.setText(f"● {label}")
        self.engine_badge.setStyleSheet(f"color: {colors.get(engine, '#e88b3a')};")

    def _refresh_format_chips(self) -> None:
        formats = getattr(self.settings, "export_formats", None) or {}
        chips = " ".join(f"{k}:{v}" for k, v in sorted(formats.items()))
        self.format_chips.setText(f"formats ▸ {chips}" if chips else "formats ▸ defaults")

    # ---- loading ----

    def open_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open game archive",
            self.settings.default_out_dir or "",
            "Game archives (*.pak *.utoc *.ucas *.unity3d *.bundle *.assets *.assetbundle *.bsa *.ba2);;All files (*)",
        )
        if not path:
            return
        self._load(path)

    def load(self, path: str) -> None:
        """Open an archive file or a folder of archives (used by --open)."""
        if Path(path).is_dir():
            self.open_folder(path)
        else:
            self._load(path)

    def _reset_session(self, folder_mode: bool = False) -> None:
        self._folder_mode = folder_mode
        self._open_archives = []
        if not folder_mode:
            self.current_executable = None
        self.unity_archive = None
        self._unity_archives.clear()
        self.unity_assets.clear()
        self._unity_engine_versions.clear()
        self.pak_archives.clear()
        self._cdpr_archives.clear()
        self._bethesda_archives.clear()
        self.unreal_entries = []
        self.tree.clear()
        self._tree_builder = AssetTreeBuilder(self.tree)
        self.search_edit.clear()
        self.properties_table.setRowCount(0)
        self.inspector_tree.clear_object()

    def _load(self, path: str) -> None:
        self.current_path = path
        self.settings.add_recent(path)
        self.settings.save()
        self._rebuild_recent()
        self._reset_session()
        self.log.clear()
        self.preview_panel.show_hero(
            title="Loading archive...",
            hint=Path(path).name,
            pixmap=self._hero_pixmap(),
        )
        detection = detect(path)
        if detection is None:
            self._set_engine(None)
            self.preview_panel.show_hero(
                title="Unable to identify format",
                hint=f"{Path(path).name} does not match a known game archive format.",
            )
            self.log.appendPlainText(f"unable to identify: {path}")
            return
        self.current_engine = detection.engine
        self._set_engine(detection.engine, detection.kind)
        self._set_driver(None)
        try:
            from dualforge.drivers import registry as _driver_registry

            driver = _driver_registry.match(path, engine=detection.engine)
            self._set_driver(driver)
        except Exception:
            pass
        self.log.appendPlainText(detection.summary())
        try:
            if detection.engine == "unity":
                self._load_unity(path)
            elif detection.engine == "unreal":
                self._load_unreal(path)
            elif detection.engine == "cdpr":
                self._load_cdpr(path)
            elif detection.engine == "bethesda":
                self._load_bethesda(path)
            elif detection.engine == "container":
                self._load_container(path)
            else:
                self.preview_panel.show_hero(
                    title="Unsupported engine",
                    hint=f"Engine '{detection.engine}' is not supported yet.",
                )
        except Exception as exc:
            QMessageBox.warning(self, "DualForge", f"Failed to read archive:\n{exc}")
            self.log.appendPlainText(f"error: {exc}")
        self._apply_filter()

    def _add_file(self, archive_path: str, path: str, kind: str, size: int, data: dict, root: Optional[QTreeWidgetItem] = None) -> None:
        builder = self._tree_builder
        if builder is None:
            builder = AssetTreeBuilder(self.tree)
            self._tree_builder = builder
        if root is not None and builder.root is not root:
            builder.reset(root)
        builder.add_file(path, kind, size, data, _type_icon(kind))

    def _load_unity(self, path: str, root: Optional[QTreeWidgetItem] = None) -> int:
        archive = UnityArchive(path)
        self.unity_archive = archive
        self._unity_archives[path] = archive
        assets = list(archive.assets())
        self._unity_engine_versions[path] = (
            archive.engine_version(),
            archive.serialized_version(),
        )
        if root is not None:
            builder = AssetTreeBuilder(self.tree, root=root)
            self._tree_builder = builder
        for asset in assets:
            key = (path, asset.path)
            self.unity_assets[key] = asset
            self._add_file(
                path,
                asset.path,
                asset.type_name,
                asset.byte_size,
                {"engine": "unity", "path": asset.path, "kind": asset.type_name, "archive": path, "size": asset.byte_size},
                root,
            )
        self._collect_types({a.type_name for a in assets})
        self.log.appendPlainText(f"loaded {len(assets)} Unity assets from {Path(path).name}")
        self.item_count.setText(f"{len(assets)} assets")
        return len(assets)

    def _load_unreal(self, path: str, root: Optional[QTreeWidgetItem] = None) -> int:
        if Path(path).suffix.lower() == ".pak":
            try:
                return self._load_unreal_native(path, root)
            except PakError as exc:
                self.log.appendPlainText(f"native pak read failed ({exc}); falling back to CLI...")
            except ImportError:
                self.log.appendPlainText("pyuepak unavailable; falling back to CLI...")
        return self._load_unreal_bridge(path, root)

    def _load_unreal_native(self, path: str, root: Optional[QTreeWidgetItem] = None) -> int:
        archive = PakArchive(
            path,
            aes_key=self.settings.default_aes_key or None,
            try_all_keys=self.settings.try_all_keys,
        )
        self.pak_archives[path] = archive
        entries = archive.list_files()
        if root is not None:
            builder = AssetTreeBuilder(self.tree, root=root)
            self._tree_builder = builder
        self.unreal_entries.extend(entries)
        for entry in entries:
            size = archive.size_of(entry)
            self._add_file(
                path,
                entry,
                "file",
                size,
                {"engine": "unreal", "path": entry, "kind": "file", "archive": path, "size": size},
                root,
            )
        self._set_engine("unreal", "native")
        unlock = ""
        if archive.key_title:
            unlock = f' (unlocked with key "{archive.key_title}")'
        self.log.appendPlainText(
            f"loaded {len(entries)} Unreal files (native) from {Path(path).name}{unlock}"
        )
        self.item_count.setText(f"{len(entries)} files")
        return len(entries)

    def _load_unreal_bridge(self, path: str, root: Optional[QTreeWidgetItem] = None) -> int:
        bridge = UnrealBridge()
        if not bridge.available():
            if not self.pak_archives and not self._folder_mode:
                self.preview_panel.show_hero(
                    title="CUE4Parse CLI not found",
                    hint="Unreal extraction requires a CUE4Parse-based CLI.\n"
                    "Set its path in Settings or DUALFORGE_CUE4PARSE - see the README "
                    "(the 'uex' CLI is the maintained option).",
                )
            return 0
        driver = getattr(self, "current_driver", None)
        usmap = self.settings.usmap_path or None
        if driver is not None and getattr(driver, "usmap_required", False) and not self._usmap_prompt_done:
            from dualforge.unreal.uex_adapter import find_usmap

            paks_dir = str(Path(path).parent)
            found = usmap or find_usmap(paks_dir)
            if found:
                usmap = found
            else:
                choice = self._prompt_for_usmap(driver, path)
                if choice == "generate":
                    self.open_usmap_dump()
                    usmap = self.settings.usmap_path or find_usmap(paks_dir)
                elif choice == "pick":
                    usmap = self._pick_usmap_file()
                if usmap:
                    self.settings.usmap_path = usmap
                self._usmap_prompt_done = True
                if not usmap:
                    self.log.appendPlainText(
                        f"note: the {driver.name} driver requires a .usmap mappings "
                        "file for unversioned packages - open Settings > Usmap or place "
                        "one in ~/.dualforge (Tools > Generate USMAP can create it)."
                    )
        entries = bridge.list_files(
            path,
            aes_key=self.settings.default_aes_key or None,
            usmap=usmap,
            egame=getattr(self.current_driver, "egame", None) or None,
        )
        paths = [str(e.get("path", e)) for e in entries]
        if root is not None:
            builder = AssetTreeBuilder(self.tree, root=root)
            self._tree_builder = builder
        self.unreal_entries.extend(paths)
        for entry in paths:
            self._add_file(
                path,
                entry,
                "file",
                0,
                {"engine": "unreal", "path": entry, "kind": "file", "archive": path, "size": 0},
                root,
            )
        self.log.appendPlainText(f"loaded {len(paths)} Unreal files from {Path(path).name}")
        self.item_count.setText(f"{len(paths)} files")
        return len(paths)

    def _load_cdpr(self, path: str, root: Optional[QTreeWidgetItem] = None) -> int:
        from dualforge.cdpr import RedArchive, RedError

        try:
            archive = RedArchive(path)
        except RedError as exc:
            self.log.appendPlainText(f"failed to open REDengine archive: {exc}")
            return 0
        self._cdpr_archives[path] = archive
        if root is not None:
            builder = AssetTreeBuilder(self.tree, root=root)
            self._tree_builder = builder
        entries = list(archive.list_files())
        for entry in entries:
            hash_int = int(Path(entry).stem, 16)
            entry_data = archive.get_entry(entry)
            size = 0
            if entry_data is not None:
                for si in range(entry_data.segments_start, entry_data.segments_end):
                    if si < len(archive._segments):
                        size += archive._segments[si].size
            self._add_file(
                path,
                entry,
                "file",
                size,
                {"engine": "cdpr", "path": entry, "kind": "file", "archive": path, "size": size, "hash": f"0x{hash_int:016x}"},
                root,
            )
        self.log.appendPlainText(f"loaded {len(entries)} REDengine files from {Path(path).name}")
        self.item_count.setText(f"{len(entries)} files")
        return len(entries)

    def _load_bethesda(self, path: str, root: Optional[QTreeWidgetItem] = None) -> int:
        from dualforge.bethesda import BethesdaArchive, BethesdaError

        try:
            archive = BethesdaArchive(path)
        except BethesdaError as exc:
            self.log.appendPlainText(f"failed to open Bethesda archive: {exc}")
            return 0
        self._bethesda_archives[path] = archive
        if root is not None:
            builder = AssetTreeBuilder(self.tree, root=root)
            self._tree_builder = builder
        entries = list(archive.list_files())
        for entry in entries:
            size = 0
            try:
                size = archive.size_of(entry)
            except Exception:
                pass
            kind = _bethesda_kind(entry)
            self._add_file(
                path,
                entry,
                kind,
                size,
                {"engine": "bethesda", "path": entry, "kind": kind, "archive": path, "size": size},
                root,
            )
        self.log.appendPlainText(
            f"loaded {len(entries)} {archive.format} files from {Path(path).name}"
        )
        self.item_count.setText(f"{len(entries)} files")
        return len(entries)

    def _load_container(self, path: str) -> None:
        container_dir = Path(self.settings.cache_dir()) / Path(path).stem
        container_dir.mkdir(parents=True, exist_ok=True)
        options = ExtractOptions(out_dir=str(container_dir), progress=None)
        result = extract_file(path, options)
        for written in result.extracted:
            self._add_file(path, Path(written).name, "extracted", 0, {"engine": "container", "path": written, "kind": "extracted", "archive": path, "size": 0})
        for error in result.errors:
            self.log.appendPlainText(f"error: {error}")
        self.preview_panel.show_hero(
            title="Container decompressed",
            hint=f"{len(result.extracted)} file(s) written to the preview cache.",
        )
        self.item_count.setText(f"{len(result.extracted)} files")

    def _collect_types(self, type_names: set) -> None:
        if self.type_combo.count() == 1:
            for type_name in sorted(type_names):
                self.type_combo.addItem(type_name, type_name)
        else:
            known = {self.type_combo.itemData(i) for i in range(self.type_combo.count())}
            for type_name in sorted(type_names):
                if type_name not in known:
                    self.type_combo.addItem(type_name, type_name)

    def open_folder(self, folder: Optional[str] = None) -> None:
        if folder is None or not isinstance(folder, str):
            folder = QFileDialog.getExistingDirectory(
                self, "Choose a game folder", self.settings.default_out_dir or ""
            )
        if not folder:
            return
        self._reset_session(folder_mode=True)
        self.current_path = folder
        self._set_engine(None)
        archives = self._scan_archives(folder)
        if not archives:
            self.preview_panel.show_hero(
                title="No archives found",
                hint=f"No supported archives found in {folder}.",
            )
            return
        self.log.clear()
        self.log.appendPlainText(f"scanning {Path(folder).name}: found {len(archives)} archive(s)")
        total = 0
        for path in archives:
            root = QTreeWidgetItem([Path(path).name])
            root.setFlags(
                root.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            root.setCheckState(0, Qt.CheckState.Unchecked)
            root.setIcon(0, make_folder_icon())
            root.setData(0, USER_ROLE, {"folder": True, "archive": path, "path": Path(path).name})
            self.tree.addTopLevelItem(root)
            try:
                detection = detect(path)
                if detection is None:
                    self.log.appendPlainText(f"unable to identify: {Path(path).name}")
                    continue
                if detection.engine == "unity":
                    count = self._load_unity(path, root)
                elif detection.engine == "unreal":
                    count = self._load_unreal(path, root)
                elif detection.engine == "cdpr":
                    count = self._load_cdpr(path, root)
                elif detection.engine == "bethesda":
                    count = self._load_bethesda(path, root)
                else:
                    continue
                total += count
                self._open_archives.append(path)
            except Exception as exc:
                self.log.appendPlainText(f"error loading {Path(path).name}: {exc}")
        self._collect_types(self._all_type_names())
        self.item_count.setText(f"{total} assets in {len(archives)} archive(s)")
        self._detect_game_in_folder(folder)
        if not self._open_archives:
            self.preview_panel.show_hero(
                title="Could not read archives",
                hint="None of the found archives could be parsed. Check the log.",
            )
        else:
            hint = f"{total} assets in {Path(folder).name}"
            if self.current_executable:
                hint += f"\nGame executable: {Path(self.current_executable).name}\nTools are ready (Ghidra Key Hunt, Generate USMAP)."
            self.preview_panel.show_hero(
                title=f"Loaded {len(self._open_archives)} archive(s)",
                hint=hint,
                pixmap=self._hero_pixmap(),
            )
        self._apply_filter()

    def _scan_archives(self, folder: str, depth: int = 4) -> List[str]:
        found: List[str] = []
        root = Path(folder)
        if depth <= 0:
            return found
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return found
        for entry in entries:
            try:
                if entry.is_dir():
                    if entry.name.lower() in {"steamapps", "common", "node_modules", ".git"}:
                        continue
                    found.extend(self._scan_archives(str(entry), depth - 1))
                elif entry.suffix.lower() in ARCHIVE_SUFFIXES:
                    found.append(str(entry))
            except OSError:
                continue
        return found

    def _all_type_names(self) -> set:
        names = set()
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            if top.data(0, USER_ROLE) and top.data(0, USER_ROLE).get("archive"):
                for leaf in iter_leaves(top):
                    data = leaf.data(0, USER_ROLE) or {}
                    if not data.get("folder") and data.get("kind"):
                        names.add(data["kind"])
        return names

    def _detect_game_in_folder(self, folder: Optional[str]) -> None:
        from dualforge.ui.executable import find_game_executable, identify_game

        exe = find_game_executable(folder) if folder else None
        self.current_executable = exe
        driver = None
        if exe:
            driver = identify_game(exe, folder)
            self.log.appendPlainText(f"detected executable: {exe}")
        if driver is None and folder:
            from dualforge.drivers import registry as _driver_registry

            try:
                driver = _driver_registry.match(folder, engine=self.current_engine)
            except Exception:
                driver = None
        if driver is not None:
            self._set_driver(driver)
            self.log.appendPlainText(f"matched game driver: {driver.name}")
            profile = self._profile_for_driver(driver)
            if profile and profile.get("aes_key"):
                self.settings.default_aes_key = profile["aes_key"]
                self.settings.save()
                self.log.appendPlainText(f"applied AES key from profile: {driver.name}")
        elif driver is None and exe:
            self._set_driver(None)

    def _profile_for_driver(self, driver) -> Optional[dict]:
        for profile in self.settings.profiles:
            name = (profile.get("name") or "").lower()
            folder = (profile.get("folder") or "").lower()
            if name == driver.name or (folder and driver.name in folder):
                return profile
        return None

    # ---- filtering ----

    def _apply_filter(self, *args) -> None:
        text = self.search_edit.text().strip()
        type_filter = self.type_combo.currentData()
        visible = apply_filter(self.tree, text, type_filter, self.regex_check.isChecked())
        if text or type_filter:
            self.preview_note.setText(f"{visible} shown")
        else:
            self.preview_note.setText("Ready")

    def _check_all(self, state: bool) -> None:
        check_state = Qt.CheckState.Checked if state else Qt.CheckState.Unchecked
        for index in range(self.tree.topLevelItemCount()):
            set_all_checkstates(self.tree.topLevelItem(index), check_state)

    # ---- preview ----

    def _schedule_preview(self) -> None:
        self._preview_timer.start()

    def _current_item_data(self) -> Optional[dict]:
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, USER_ROLE)

    def _show_preview(self) -> None:
        data = self._current_item_data()
        if data is None or data.get("folder"):
            return
        self._fill_properties(data)
        engine = data.get("engine")
        path = data.get("path", "")
        archive = data.get("archive") or self.current_path or ""
        if engine == "unity":
            asset = self.unity_assets.get((archive, path))
            if asset is None:
                return
            meta = {"Type": asset.type_name, "Engine": "Unity", "Source": Path(archive).name}
            engine_version, serialized = self._unity_engine_versions.get(archive, ("", -1))
            if engine_version:
                meta["Unity version"] = engine_version
            if serialized > 0:
                meta["Serialized format"] = str(serialized)
            self.preview_panel.request_preview(
                PreviewItem(
                    title=path,
                    engine="unity",
                    kind=data.get("kind", ""),
                    size=asset.byte_size,
                    asset=asset,
                    archive_path=archive,
                    meta=meta,
                )
            )
        elif engine == "unreal":
            self.preview_panel.request_preview(
                PreviewItem(
                    title=path,
                    engine="unreal",
                    kind="file",
                    size=data.get("size") or 0,
                    entry=path,
                    aes_key=self.settings.default_aes_key or None,
                    archive_path=archive,
                    native_archive=self.pak_archives.get(archive),
                    usmap=self.settings.usmap_path or None,
                    scheme=getattr(self.current_driver, "scheme", None) or getattr(self.current_driver, "encryption_scheme", None) or None,
                    egame=getattr(self.current_driver, "egame", None) or None,
                    meta={"Engine": "Unreal", "Source": Path(archive).name},
                )
            )
        elif engine == "cdpr":
            self.preview_panel.request_preview(
                PreviewItem(
                    title=path,
                    engine="cdpr",
                    kind="file",
                    size=data.get("size") or 0,
                    entry=path,
                    archive_path=archive,
                    native_archive=self._cdpr_archives.get(archive),
                    meta={"Engine": "REDengine", "Hash": data.get("hash", ""), "Source": Path(archive).name},
                )
            )
        elif engine == "bethesda":
            self.preview_panel.request_preview(
                PreviewItem(
                    title=path,
                    engine="bethesda",
                    kind=data.get("kind", "file"),
                    size=data.get("size") or 0,
                    entry=path,
                    archive_path=archive,
                    native_archive=self._bethesda_archives.get(archive),
                    meta={"Engine": "Bethesda", "Type": data.get("kind", "file"), "Source": Path(archive).name},
                )
            )
        elif engine == "file" or engine == "container":
            written = data.get("path", "")
            self.preview_panel.request_preview(
                PreviewItem(
                    title=Path(written).name or path,
                    engine="file",
                    kind="file",
                    size=data.get("size") or 0,
                    entry=written,
                    meta={"Engine": "File", "Source": Path(written).parent.name or ""},
                )
            )
        else:
            self.preview_panel.show_hero(
                title=Path(path).name,
                hint="Extracted container member.",
            )

    def _fill_properties(self, data: dict) -> None:
        engine = data.get("engine")
        path = data.get("path", "")
        archive = data.get("archive") or self.current_path or ""
        rows = [("Path", path)]
        if engine == "unity":
            asset = self.unity_assets.get((archive, path))
            if asset is not None:
                rows += [
                    ("Type", asset.type_name),
                    ("Size", f"{asset.byte_size:,} bytes"),
                    ("Engine", "Unity"),
                    ("Source", Path(archive).name),
                ]
                engine_version, serialized = self._unity_engine_versions.get(archive, ("", -1))
                if engine_version:
                    rows.append(("Unity version", engine_version))
                if serialized > 0:
                    rows.append(("Serialized format", str(serialized)))
        elif engine == "unreal":
            rows += [
                ("Type", "file"),
                ("Engine", "Unreal"),
                ("Source", Path(archive).name),
            ]
        elif engine == "cdpr":
            rows += [
                ("Type", "file"),
                ("Engine", "REDengine"),
                ("Hash", data.get("hash", "")),
                ("Source", Path(archive).name),
            ]
        elif engine == "bethesda":
            rows += [
                ("Type", data.get("kind", "file")),
                ("Size", f"{data.get('size') or 0:,} bytes"),
                ("Engine", "Bethesda"),
                ("Source", Path(archive).name),
            ]
        self.properties_table.setRowCount(len(rows))
        for index, (key, value) in enumerate(rows):
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item = QTableWidgetItem(value)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.properties_table.setItem(index, 0, key_item)
            self.properties_table.setItem(index, 1, value_item)
        self.inspector_tree.clear_object()

    def _on_typetree_loaded(self, tree: dict) -> None:
        data = self._current_item_data() or {}
        title = data.get("path") or "Object"
        self.inspector_tree.set_object(title, tree)

    # ---- tree context menu ----

    def _tree_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        data = item.data(0, USER_ROLE) or {}
        engine = data.get("engine")
        asset_type = ""
        if engine == "unity":
            path = data.get("path", "")
            archive = data.get("archive") or self.current_path or ""
            asset = self.unity_assets.get((archive, path))
            if asset is not None:
                asset_type = asset.type_name
        menu = QMenu(self)
        preview_action = menu.addAction("Preview")
        export_action = menu.addAction("Export Selected...")
        replace_action = None
        if engine == "unity" and asset_type in {_REPLACE_TEXTURE, _REPLACE_TEXT, _REPLACE_FONT}:
            replace_action = menu.addAction("Replace with File...")
        open_action = None
        if self._last_out_dir:
            open_action = menu.addAction("Open Last Output Folder")
        reveal_action = None
        if not data.get("folder"):
            extract_dir = self._extract_dir_for(data)
            if extract_dir:
                reveal_action = menu.addAction("Open Containing Folder")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
        if chosen is preview_action:
            self._show_preview()
        elif chosen is export_action:
            self.extract_selected()
        elif chosen is not None and replace_action is not None and chosen is replace_action:
            self._replace_selected()
        elif chosen is not None and open_action is not None and chosen is open_action:
            self._open_folder(self._last_out_dir)
        elif chosen is not None and reveal_action is not None and chosen is reveal_action:
            self._open_folder(extract_dir)

    def _replace_selected(self) -> None:
        if self.current_path is None:
            return
        data = self._current_item_data() or {}
        archive = data.get("archive") or self.current_path
        path = data.get("path", "")
        asset = self.unity_assets.get((archive, path))
        if asset is None:
            return
        archive_handle = self._unity_archives.get(archive) or self.unity_archive
        if archive_handle is None:
            return
        from dualforge.unity.repack import replace_font, replace_text_asset, replace_texture, save_archive

        caption = f"Replace {asset.type_name} - choose source file"
        if asset.type_name == _REPLACE_TEXTURE:
            source, _ = QFileDialog.getOpenFileName(
                self, caption, "", "Images (*.png *.jpg *.jpeg *.bmp *.webp *.tga *.dds *.tex)"
            )
            if not source:
                return
            replace_texture(archive_handle, asset, source)
        elif asset.type_name == _REPLACE_TEXT:
            source, _ = QFileDialog.getOpenFileName(self, caption, "", "Text (*.txt *.json *.xml *.csv *.log *.lua *.cs);;All files (*)")
            if not source:
                return
            with open(source, "rb") as fh:
                data_in = fh.read()
            replace_text_asset(archive_handle, asset, data_in)
        elif asset.type_name == _REPLACE_FONT:
            source, _ = QFileDialog.getOpenFileName(self, caption, "", "Fonts (*.ttf *.otf)")
            if not source:
                return
            replace_font(archive_handle, asset, source)
        else:
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "Save repacked archive to (new folder - never the source)",
            self.settings.profiles_dir or str(Path.home()),
        )
        if not out_dir:
            return
        if Path(out_dir).resolve() == Path(archive).resolve():
            QMessageBox.warning(
                self, "DualForge", "Refusing to overwrite the source archive. Choose a different output folder.",
            )
            return
        try:
            save_archive(archive_handle, out_dir)
        except Exception as exc:
            QMessageBox.critical(self, "DualForge", f"Repack failed:\n{exc}")
            return
        self.statusBar().showMessage(f"Repacked '{Path(path).name}' -> {out_dir}")
        self._last_out_dir = out_dir

    def _extract_dir_for(self, data: dict) -> Optional[str]:
        ext_path = data.get("path", "") or ""
        if data.get("kind") == "extracted":
            dir_path = Path(ext_path).parent if ext_path else None
            return str(dir_path) if dir_path and dir_path.is_dir() else None
        if self._last_out_dir:
            return self._last_out_dir
        return None

    # ---- extraction ----

    def extract_all(self) -> None:
        if not self.current_path:
            QMessageBox.information(self, "DualForge", "Open an archive or folder first.")
            return
        if self.worker is not None and self.worker.isRunning():
            return
        groups = self._all_groups()
        if not groups and self._folder_mode:
            groups = {self.current_path: None}
        self._start_extract(groups, types=None)

    def extract_selected(self) -> None:
        if not self.current_path:
            QMessageBox.information(self, "DualForge", "Open an archive or folder first.")
            return
        if self.worker is not None and self.worker.isRunning():
            return
        groups = self._checked_groups()
        if not groups:
            if self._folder_mode:
                QMessageBox.information(
                    self, "DualForge", "Check the assets to export, then press Export Selected."
                )
                return
            groups = {self.current_path: None}
        self._start_extract(groups, types=None)

    def _all_groups(self) -> Dict[str, Optional[List[str]]]:
        """Build an (archive -> files) map covering every file in the tree."""
        groups: Dict[str, Optional[List[str]]] = {}
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for leaf in iter_leaves(top):
                data = leaf.data(0, USER_ROLE) or {}
                if data.get("folder"):
                    continue
                archive = data.get("archive")
                if not archive:
                    continue
                if groups.get(archive) is None:
                    groups[archive] = [data.get("path", "")]
                else:
                    groups[archive].append(data.get("path", ""))
        return groups

    def _checked_groups(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for index in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(index)
            for leaf in checked_leaves(top):
                data = leaf.data(0, USER_ROLE) or {}
                archive = data.get("archive")
                if not archive:
                    continue
                groups.setdefault(archive, []).append(data.get("path", ""))
        return groups

    def _start_extract(self, groups: Dict[str, Optional[List[str]]], types: Optional[List[str]]) -> None:
        if not groups:
            return
        if any(archive.lower().endswith((".utoc", ".ucas")) for archive in groups):
            if not UnrealBridge().available():
                QMessageBox.warning(
                    self,
                    "DualForge",
                    "CUE4Parse CLI is required for this Unreal format (IoStore).",
                )
                return
        out_dir = QFileDialog.getExistingDirectory(
            self,
            "Choose output directory",
            self._last_out_dir or self.settings.default_out_dir or "",
        )
        if not out_dir:
            return
        self._last_out_dir = out_dir
        self.log.clear()
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self._progress_bar)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self._cancel_extract)
        self.statusBar().addPermanentWidget(self._cancel_button)
        self.worker = ExtractWorker(
            list(groups),
            out_dir,
            self.settings.default_aes_key or None,
            types,
            groups,
            self.settings.export_formats,
            self.settings.usmap_path or None,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.failed.connect(self._on_failed)
        self.worker.finished.connect(self._cleanup_extract_ui)
        self.worker.cancelled.connect(self._cleanup_extract_ui)
        self.worker.failed.connect(self._cleanup_extract_ui)
        self.worker.start()

    def _cancel_extract(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self._cancel_button.setEnabled(False)

    def _cleanup_extract_ui(self) -> None:
        self.statusBar().removeWidget(self._progress_bar)
        self.statusBar().removeWidget(self._cancel_button)
        self._progress_bar.deleteLater()
        self._cancel_button.deleteLater()
        self.worker = None

    def _on_progress(self, index: int, total: int, message: str) -> None:
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(index + 1)
        self.preview_note.setText(message)

    def _on_finished(self, ok: int, extracted: list, errors: list) -> None:
        self.log.appendPlainText(f"extracted {ok} assets")
        for error in errors:
            self.log.appendPlainText(f"warning: {error}")
        if self._last_out_dir:
            self.settings.default_out_dir = self._last_out_dir
            self.settings.save()
        self._refresh_format_chips()
        box = QMessageBox(self)
        box.setWindowTitle("DualForge")
        box.setIcon(QMessageBox.Icon.Information)
        text = f"Extraction finished.\n\n{ok} assets written to:\n{self._last_out_dir or ''}"
        if errors:
            text += f"\n\n{len(errors)} warning(s) - see log."
        box.setText(text)
        open_btn = box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is open_btn and self._last_out_dir:
            self._open_folder(self._last_out_dir)

    def _on_cancelled(self) -> None:
        self.log.appendPlainText("extraction cancelled by user")
        self.preview_note.setText("Cancelled")

    def _on_failed(self, message: str) -> None:
        self.log.appendPlainText(f"error: {message}")
        QMessageBox.warning(self, "DualForge", f"Extraction failed:\n{message}")

    def _open_folder(self, path: str) -> None:
        if not path or not Path(path).is_dir():
            QMessageBox.warning(self, "DualForge", f"Folder not found:\n{path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, "DualForge", f"Could not open folder:\n{path}")

    def show_stats(self) -> None:
        if not self.current_path:
            QMessageBox.information(self, "DualForge", "Open an archive or folder first.")
            return
        StatsDialog(self.tree, Path(self.current_path).name, self).exec()

    # ---- keys / profiles / settings / about ----

    def manage_keys(self) -> None:
        store = KeyStore()
        dialog = QDialog(self)
        dialog.setWindowTitle("Manage Keys")
        dialog.resize(420, 340)
        layout = QVBoxLayout(dialog)
        self._key_list = QListWidget()
        entries = store.list()
        for entry in entries:
            scheme = getattr(entry, "scheme", "aes-256")
            self._key_list.addItem(
                f"{entry.title}  ({entry.engine}/{scheme})  -  "
                f"{entry.aes_key[:8]}...{entry.aes_key[-8:] if len(entry.aes_key) > 8 else ''}"
            )
        layout.addWidget(self._key_list, 1)
        buttons = QDialogButtonBox()
        add_btn = buttons.addButton("Add...", QDialogButtonBox.ButtonRole.ActionRole)
        add_btn.setProperty("role", "primary")
        remove_btn = buttons.addButton("Remove", QDialogButtonBox.ButtonRole.ActionRole)
        remove_btn.setProperty("role", "danger")
        buttons.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def add_key() -> None:
            key_dialog = KeyDialog(dialog)
            if key_dialog.exec() == QDialog.DialogCode.Accepted:
                title, key, engine, scheme, guid, params_text = key_dialog.values()
                if not title or not key:
                    QMessageBox.warning(dialog, "DualForge", "Title and key are required.")
                    return
                parameters = {}
                if params_text:
                    for item in params_text.split(","):
                        if "=" in item:
                            k, v = item.split("=", 1)
                            parameters[k.strip()] = v.strip()
                store.add(
                    title, key,
                    engine=engine,
                    scheme=scheme or "aes-256",
                    guid=guid,
                    parameters=parameters,
                )
                self._key_list.addItem(
                    f"{title}  ({engine}/{scheme})  -  {key[:8]}...{key[-8:] if len(key) > 8 else ''}"
                )
                self.log.appendPlainText(f"stored {scheme} key for {title}")

        def remove_key() -> None:
            row = self._key_list.currentRow()
            if row < 0:
                return
            title = store.list()[row].title
            store.remove(title)
            self._key_list.takeItem(row)

        add_btn.clicked.connect(add_key)
        remove_btn.clicked.connect(remove_key)
        dialog.exec()

    def open_profiles(self) -> None:
        dialog = ProfilesDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        profile = getattr(dialog, "_profile_to_load", None)
        if profile and profile.get("folder"):
            self.open_folder(profile["folder"])

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.values()
            self.settings.save()
            apply_theme(QApplication.instance(), self.settings.theme)
            self._refresh_toolbar_icons()
            self._refresh_format_chips()
            self.log.appendPlainText("settings saved")

    def _change_theme(self) -> None:
        action = self.sender()
        if action is None:
            return
        self.settings.theme = action.data()
        self.settings.save()
        apply_theme(QApplication.instance(), self.settings.theme)
        self._refresh_toolbar_icons()

    DEFAULT_DONATION_URL = "https://ko-fi.com/b6000"

    def donate(self) -> None:
        from dualforge import __version__

        url = (self.settings.donation_url or "").strip()
        if not url:
            url = self.DEFAULT_DONATION_URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if QDesktopServices.openUrl(QUrl(url)):
            self.log.appendPlainText(
                f"opened donation page ({url}) - thank you for supporting DualForge {__version__}"
            )
        else:
            QMessageBox.warning(
                self,
                "Donate",
                f"Could not open the donation page.\n\n{url}\n"
                "Copy the link into your browser, or set a custom URL under "
                "File \u2192 Settings \u2192 Donation URL.",
            )

    def _show_about(self) -> None:
        from dualforge.ui.about import AboutDialog

        AboutDialog(self).exec()

    def open_ghidra_hunt(self) -> None:
        from dualforge.ui.ghidra_dialog import GhidraDialog

        binary = self.current_executable or ""
        if binary and not Path(binary).is_file():
            binary = ""
        GhidraDialog(self, default_binary=binary).exec()

    def open_usmap_dump(self) -> None:
        from dualforge.ui.usmap_dialog import UsmapDumpDialog

        UsmapDumpDialog(self, suggested_exe=self.current_executable).exec()

    def _prompt_for_usmap(self, driver, path: str) -> str:
        """Ask the user how to obtain the .usmap a driver requires.

        Returns ``"generate"``, ``"pick"`` or ``"skip"``."""
        title = Path(path).name
        box = QMessageBox(self)
        box.setWindowTitle("USMAP required")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f"<b>{driver.label}</b> uses unversioned UE5 packages, which need a "
            "CUE4Parse mappings file (<code>.usmap</code>). No mappings file was "
            "found for <b>{0}</b>.".format(escape(title))
        )
        generate = box.addButton("Generate from running game", QMessageBox.ButtonRole.AcceptRole)
        pick = box.addButton("Choose existing .usmap...", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is generate:
            return "generate"
        if clicked is pick:
            return "pick"
        return "skip"

    def _pick_usmap_file(self) -> Optional[str]:
        from PySide6.QtWidgets import QFileDialog

        start = str(Path.home() / ".dualforge")
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Choose a .usmap mappings file",
            start,
            "USMAP mappings (*.usmap)",
        )
        return picked or None

    def open_drivers(self) -> None:
        from dualforge.ui.drivers_dialog import DriversDialog

        DriversDialog(self).exec()

    def open_compatibility(self) -> None:
        from dualforge.ui.compat_dialog import CompatDialog

        CompatDialog(self).exec()

    # ---- recent files ----

    def _rebuild_recent(self) -> None:
        if self._recent_menu is None:
            return
        self._recent_menu.clear()
        files = [Path(p) for p in self.settings.recent_files]
        names = [p.name for p in files]
        from collections import Counter

        dupes = {name for name, count in Counter(names).items() if count > 1}
        for path in files:
            label = path.name
            if path.name in dupes and path.parent.name:
                label = f"{path.name}  ({path.parent.name})"
            action = self._recent_menu.addAction(label)
            action.setToolTip(str(path))
            action.triggered.connect(lambda checked=False, p=str(path): self._load(p))
        if not files:
            empty = self._recent_menu.addAction("(no recent archives)")
            empty.setEnabled(False)

    # ---- window state ----

    def _restore_window_state(self) -> None:
        for attr, restore in (
            ("window_geometry", self.restoreGeometry),
            ("window_state", self.restoreState),
        ):
            data = getattr(self.settings, attr)
            if not data:
                continue
            try:
                restore(QByteArray.fromBase64(data.encode("ascii")))
            except (ValueError, UnicodeEncodeError):
                continue

    def closeEvent(self, event) -> None:
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.settings.window_state = bytes(self.saveState().toBase64()).decode("ascii")
        self.settings.save()
        super().closeEvent(event)

    # ---- drag & drop ----

    def dragEnterEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            return
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and Path(path).suffix.lower() in ARCHIVE_SUFFIXES:
                event.acceptProposedAction()
                return

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        paths = [
            url.toLocalFile()
            for url in urls
            if url.toLocalFile() and Path(url.toLocalFile()).suffix.lower() in ARCHIVE_SUFFIXES
        ]
        if not paths:
            self.log.appendPlainText("dropped non-archive file(s); only game archives are accepted")
            return
        if len(paths) == 1:
            self._load(paths[0])
        else:
            self.open_folder_from_paths(paths)

    def open_folder_from_paths(self, paths: List[str]) -> None:
        folder = str(Path(paths[0]).parent)
        self._reset_session(folder_mode=True)
        self.current_path = folder
        self._set_engine(None)
        self.log.clear()
        self.log.appendPlainText(
            f"dropped {len(paths)} archive(s) from {Path(folder).name}"
        )
        archives = sorted(set(paths), key=str.lower)
        total = 0
        for path in archives:
            root = QTreeWidgetItem([Path(path).name])
            root.setFlags(
                root.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            root.setCheckState(0, Qt.CheckState.Unchecked)
            root.setIcon(0, make_folder_icon())
            root.setData(0, USER_ROLE, {"folder": True, "archive": path, "path": Path(path).name})
            self.tree.addTopLevelItem(root)
            try:
                detection = detect(path)
                if detection is None:
                    self.log.appendPlainText(f"unable to identify: {Path(path).name}")
                    continue
                if detection.engine == "unity":
                    count = self._load_unity(path, root)
                elif detection.engine == "unreal":
                    count = self._load_unreal(path, root)
                elif detection.engine == "cdpr":
                    count = self._load_cdpr(path, root)
                elif detection.engine == "bethesda":
                    count = self._load_bethesda(path, root)
                else:
                    self.log.appendPlainText(
                        f"skipping non-extractable archive: {Path(path).name}"
                    )
                    continue
                total += count
                self._open_archives.append(path)
            except Exception as exc:
                self.log.appendPlainText(f"error loading {Path(path).name}: {exc}")
        self._collect_types(self._all_type_names())
        self.item_count.setText(f"{total} assets in {len(archives)} archive(s)")
        self._detect_game_in_folder(folder)
        if not self._open_archives:
            self.preview_panel.show_hero(
                title="Could not read archives",
                hint="None of the dropped archives could be parsed. Check the log.",
            )
        else:
            hint = f"{total} assets in {Path(folder).name}"
            if self.current_executable:
                hint += f"\nGame executable: {Path(self.current_executable).name}\nTools are ready (Ghidra Key Hunt, Generate USMAP)."
            self.preview_panel.show_hero(
                title=f"Loaded {len(self._open_archives)} archive(s)",
                hint=hint,
                pixmap=self._hero_pixmap(),
            )
        self._apply_filter()
