from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from dualforge.drivers import registry

_DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "COMPATIBILITY.md"


def render_compat_markdown(text: str) -> str:
    """Render docs/COMPATIBILITY.md into a readable plain-text overview."""
    out: list[str] = []
    in_code = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        stripped = line.strip()
        if in_code:
            out.append(f"    {stripped}")
            continue
        if stripped.startswith("###"):
            out.append(f"   {stripped.lstrip('#').strip()}")
        elif stripped.startswith("##"):
            out += ["", f"── {stripped.lstrip('#').strip()} ──", ""]
        elif stripped.startswith("#"):
            out += ["", f"█ {stripped.lstrip('#').strip()}", ""]
        elif stripped.startswith(">"):
            out.append(f"▸ {stripped.lstrip('>').strip()}")
        elif line.startswith("|") and set(stripped.replace("|", "").replace("-", "").replace(" ", "").replace(":", "")) == set():
            continue
        elif line.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            out.append("   ".join(cells))
        elif stripped == "---":
            continue
        elif stripped.startswith("- "):
            out.append(f"   • {stripped[2:].strip()}")
        elif stripped[:2].rstrip(". ") == "" and len(stripped) > 1:
            out.append(f"   • {stripped}")
        elif stripped:
            out.append(stripped)
        else:
            out.append("")
    return "\n".join(out).strip("\n")


class CompatDialog(QDialog):
    """Video game compatibility guide, readable inside the app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Video Game Compatibility")
        self.resize(880, 580)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "<b>DualForge</b> auto-detects engine, archive format and encryption for the "
            "games below. The matched <i>game driver</i> (engine, scheme, export defaults) "
            "is shown in the status bar — click it or use <b>Tools ▸ Game Drivers</b> to "
            "manage custom drivers."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        driver_count = len(registry.list())
        if driver_count:
            names = ", ".join(d.name for d in sorted(registry.list(), key=lambda d: d.name))
            hint = QLabel(f"Installed drivers ({driver_count}): {names}")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #8b90a3;")
            layout.addWidget(hint)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Consolas", 9))
        self.view.setPlainText(self._render())
        layout.addWidget(self.view, 1)

        buttons = QHBoxLayout()
        open_doc = QPushButton("Open docs/COMPATIBILITY.md...")
        open_doc.setToolTip("Open the full compatibility guide in your browser/editor")
        open_doc.clicked.connect(self._open_doc)
        close_btn = QPushButton("Close")
        close_btn.setProperty("role", "primary")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(open_doc)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _render(self) -> str:
        if _DOC.exists():
            return render_compat_markdown(_DOC.read_text(encoding="utf-8"))
        return "Compatibility guide not found. See docs/COMPATIBILITY.md."

    def _open_doc(self) -> None:
        if _DOC.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(_DOC)))


__all__ = ["CompatDialog", "render_compat_markdown"]