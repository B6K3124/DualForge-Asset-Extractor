"""Render the DualForge GUI offscreen and save a screenshot.

Generates ``docs/screenshots/hero.png`` (or the given output path) without
opening a window on screen, so you can re-shoot the hero image at any time.

Examples:
    python scripts/screenshot.py                          # empty app state
    python scripts/screenshot.py my_game.assets           # with an archive loaded
    python scripts/screenshot.py my_game.pak -o hero.png  # custom output
"""

from __future__ import annotations

import argparse
import os
import sys


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