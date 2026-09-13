from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from dualforge.version import __version__

ACCENT = QColor("#e88b3a")
EMBER = QColor("#f09d55")
BASE = QColor("#1b1c22")
BASE_LIGHT = QColor("#22242e")


def make_splash_pixmap(width: int = 480, height: int = 260) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0, 0, width, height)
    gradient.setColorAt(0.0, QColor("#20212a"))
    gradient.setColorAt(1.0, QColor("#14151a"))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, width, height), 16, 16)

    glow = QRadialGradient(width * 0.78, height * 0.22, width * 0.45)
    glow.setColorAt(0.0, QColor(232, 139, 58, 70))
    glow.setColorAt(1.0, QColor(232, 139, 58, 0))
    painter.setBrush(glow)
    painter.drawRoundedRect(QRectF(0, 0, width, height), 16, 16)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#23242e"))
    painter.drawEllipse(width * 0.5 - 34, height * 0.5 - 34, 68, 68)
    painter.setBrush(ACCENT)
    painter.drawRoundedRect(QRectF(width * 0.5 - 30, height * 0.5 - 30, 60, 60), 12, 12)
    font = QFont("Segoe UI", 30, QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor("#16171c"))
    painter.drawText(QRectF(width * 0.5 - 30, height * 0.5 - 30, 60, 60), Qt.AlignmentFlag.AlignCenter, "DF")

    title_font = QFont("Segoe UI", 26, QFont.Weight.Bold)
    painter.setFont(title_font)
    painter.setPen(QColor("#e8eaf1"))
    painter.drawText(QRectF(0, 52, width, 40), Qt.AlignmentFlag.AlignCenter, "DualForge")

    tag_font = QFont("Segoe UI", 11)
    painter.setFont(tag_font)
    painter.setPen(QColor("#8b90a3"))
    painter.drawText(QRectF(0, 96, width, 26), Qt.AlignmentFlag.AlignCenter, "Unity & Unreal asset extractor")

    version_font = QFont("Segoe UI", 9)
    painter.setFont(version_font)
    painter.setPen(QColor("#6a7087"))
    painter.drawText(QRectF(0, 210, width, 22), Qt.AlignmentFlag.AlignCenter, f"v{__version__}")

    painter.end()
    return pixmap


def make_app_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ACCENT)
    painter.drawRoundedRect(QRectF(2, 2, size - 4, size - 4), size * 0.2, size * 0.2)
    painter.setPen(QColor("#16171c"))
    painter.setFont(QFont("Segoe UI", int(size * 0.42), QFont.Weight.Bold))
    painter.drawText(
        QRectF(2, 2, size - 4, size - 4),
        Qt.AlignmentFlag.AlignCenter,
        "DF",
    )
    painter.end()
    return QIcon(pixmap)


_IconPainter = Callable[[QPainter, float, str], None]


def _icon_pen(color: str, s: float, width: float = 0.07) -> QPen:
    pen = QPen(QColor(color), max(2.0, s * width))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _draw_open(painter: QPainter, s: float, color: str) -> None:
    painter.setPen(_icon_pen(color, s, 0.06))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath()
    path.moveTo(0.15 * s, 0.34 * s)
    path.lineTo(0.36 * s, 0.34 * s)
    path.lineTo(0.44 * s, 0.42 * s)
    path.lineTo(0.85 * s, 0.42 * s)
    path.lineTo(0.85 * s, 0.78 * s)
    path.lineTo(0.15 * s, 0.78 * s)
    path.closeSubpath()
    painter.drawPath(path)


def _draw_extract(painter: QPainter, s: float, color: str) -> None:
    painter.setPen(_icon_pen(color, s, 0.06))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(0.5 * s, 0.14 * s, 0.5 * s, 0.54 * s)
    painter.drawLine(0.5 * s, 0.54 * s, 0.40 * s, 0.44 * s)
    painter.drawLine(0.5 * s, 0.54 * s, 0.60 * s, 0.44 * s)
    path = QPainterPath()
    path.moveTo(0.16 * s, 0.40 * s)
    path.lineTo(0.16 * s, 0.80 * s)
    path.lineTo(0.84 * s, 0.80 * s)
    path.lineTo(0.84 * s, 0.40 * s)
    painter.drawPath(path)


def _draw_keys(painter: QPainter, s: float, color: str) -> None:
    painter.setPen(_icon_pen(color, s, 0.08))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(0.08 * s, 0.10 * s, 0.40 * s, 0.40 * s))
    painter.drawLine(0.44 * s, 0.30 * s, 0.86 * s, 0.30 * s)
    painter.drawLine(0.72 * s, 0.30 * s, 0.72 * s, 0.44 * s)
    painter.drawLine(0.82 * s, 0.30 * s, 0.82 * s, 0.54 * s)


def _draw_donate(painter: QPainter, s: float, color: str) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(0.50 * s, 0.88 * s)
    path.cubicTo(0.06 * s, 0.58 * s, 0.15 * s, 0.20 * s, 0.37 * s, 0.22 * s)
    path.cubicTo(0.45 * s, 0.23 * s, 0.50 * s, 0.30 * s, 0.50 * s, 0.38 * s)
    path.cubicTo(0.50 * s, 0.30 * s, 0.55 * s, 0.23 * s, 0.63 * s, 0.22 * s)
    path.cubicTo(0.85 * s, 0.20 * s, 0.94 * s, 0.58 * s, 0.50 * s, 0.88 * s)
    path.closeSubpath()
    painter.drawPath(path)


_draw_folder = _draw_open


_ICON_PAINTERS: dict[str, _IconPainter] = {
    "open": _draw_open,
    "folder": _draw_folder,
    "extract": _draw_extract,
    "keys": _draw_keys,
    "donate": _draw_donate,
}


def make_toolbar_icon(name: str, color: str = "#d7dae3", size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw = _ICON_PAINTERS.get(name)
    if draw is not None:
        draw(painter, float(size), color)
    painter.end()
    return QIcon(pixmap)


def make_folder_icon(color: str = "#e0a53c", size: int = 16) -> QIcon:
    return make_toolbar_icon("open", color, size)


__all__ = ["make_app_icon", "make_folder_icon", "make_splash_pixmap", "make_toolbar_icon"]
