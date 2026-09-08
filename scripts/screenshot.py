"""Render the DualForge GUI offscreen and save a screenshot.

Generates ``docs/screenshots/hero.png`` (or the given output path) without
opening a window on screen, so you can re-shoot the hero image at any time.

When an archive is loaded, the first mesh (or any non-folder) asset is selected
in the tree and the async preview worker is allowed to finish before grabbing.

Examples:
    python scripts/screenshot.py                          # empty app state
    python scripts/screenshot.py my_game.assets           # with an archive loaded
    python scripts/screenshot.py my_game.pak -o hero.png  # custom output
"""

from __future__ import annotations

import argparse
import os
import sys
import time

MESH_PAGE_INDEX = 3


def _find_preview_item(tree):
    """Return the first non-folder tree item that has previewable data."""
    from dualforge.ui.tree_builder import USER_ROLE

    for i in range(tree.topLevelItemCount()):
        stack = [tree.topLevelItem(i)]
        while stack:
            item = stack.pop()
            data = item.data(0, USER_ROLE) or {}
            if data.get("folder"):
                stack.extend(item.child(i) for i in range(item.childCount()))
                continue
            if data.get("kind") == "Mesh" or data.get("engine") == "unity":
                return item
            stack.extend(item.child(i) for i in range(item.childCount()))
    return None


def _wait_for_preview(window, timeout: float = 10.0) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if window.preview_panel.currentIndex() == MESH_PAGE_INDEX:
            return
        time.sleep(0.02)
    print(
        "warning: preview did not reach the mesh page before timeout",
        file=sys.stderr,
    )


_FONT_FILES = [
    ("Segoe UI", r"C:\Windows\Fonts\segoeui.ttf"),
    ("Segoe UI", r"C:\Windows\Fonts\segoeuib.ttf"),
    ("Segoe UI", r"C:\Windows\Fonts\segoeuii.ttf"),
    ("Segoe UI Semibold", r"C:\Windows\Fonts\seguisb.ttf"),
    ("Segoe UI Light", r"C:\Windows\Fonts\segoeuil.ttf"),
    ("Segoe UI Symbol", r"C:\Windows\Fonts\seguisym.ttf"),
    ("Cascadia Mono", r"C:\Windows\Fonts\cascadiamono.ttf"),
    ("Cascadia Code", r"C:\Windows\Fonts\cascadia.ttf"),
    ("Consolas", r"C:\Windows\Fonts\consola.ttf"),
]


def _register_fonts() -> None:
    """Register system font files with the offscreen Qt platform.

    ``QT_QPA_PLATFORM=offscreen`` reports no font families at all, so text
    would otherwise render as empty boxes. Loading the font files directly
    brings glyphs back for the grab.
    """
    from PySide6.QtGui import QFontDatabase

    for _family, path in _FONT_FILES:
        if os.path.isfile(path):
            QFontDatabase.addApplicationFont(path)


def main(argv=None) -> int:
    if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="?", help="game archive to load, if any")
    parser.add_argument(
        "-o",
        "--out",
        default=os.path.join("docs", "screenshots", "hero.png"),
        help="output PNG path (default: docs/screenshots/hero.png)",
    )
    args = parser.parse_args(argv)

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from dualforge.ui.main_window import MainWindow
    from dualforge.ui.settings import Settings
    from dualforge.ui.theme import apply_theme

    app = QApplication([])
    app.setApplicationName("DualForge")
    _register_fonts()
    app.setFont(QFont("Segoe UI", 10))

    settings = Settings.load()
    apply_theme(app, settings.theme)

    window = MainWindow(settings)
    window.resize(1280, 760)
    window.show()
    app.processEvents()

    if args.archive:
        window._load(args.archive)
        app.processEvents()
        item = _find_preview_item(window.tree)
        if item is not None:
            window.tree.expandAll()
            window.tree.setCurrentItem(item)
            app.processEvents()
            _wait_for_preview(window)

    out = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    ok = window.grab().save(out, "PNG")
    if not ok:
        print(f"error: could not write {out}", file=sys.stderr)
        return 1
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())