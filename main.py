from __future__ import annotations

import os
import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    open_path = None
    for flag in ("--open", "-o"):
        if flag in argv:
            index = argv.index(flag)
            if index + 1 < len(argv):
                open_path = argv[index + 1]
            del argv[index : index + 2]
            break
    if "--gui" in argv or "-g" in argv:
        argv = [a for a in argv if a not in {"--gui", "-g"}]
        return _run_gui(open_path=open_path)
    if argv and argv[0] in {
        "detect", "extract", "keys", "codecs", "usmap", "drivers", "crack",
        "world", "il2cpp", "locres", "repack",
    }:
        from dualforge.cli import main as cli_main

        return cli_main(argv)
    return _run_gui(open_path=open_path)


def _run_gui(open_path: str | None = None) -> int:
    if os.environ.get("QT_QPA_PLATFORM", "") == "" and os.name == "nt":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QSplashScreen

    from dualforge.ui.branding import make_app_icon, make_splash_pixmap
    from dualforge.ui.settings import Settings
    from dualforge.ui.theme import apply_theme

    app = QApplication([])
    app.setApplicationName("DualForge")
    app.setApplicationDisplayName("DualForge")
    app.setOrganizationName("DualForge")
    app.setWindowIcon(make_app_icon())
    app.setFont(QFont("Segoe UI", 10))

    settings = Settings.load()
    apply_theme(app, settings.theme)

    splash = QSplashScreen(make_splash_pixmap())
    splash.show()
    app.processEvents()

    from dualforge.ui.main_window import MainWindow

    window = MainWindow(settings)
    splash.finish(window)
    window.show()
    if open_path:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: window.load(open_path))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
