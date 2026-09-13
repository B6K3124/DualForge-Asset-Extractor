from __future__ import annotations


import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

DARK_BG = QColor("#15161b")
BAR_COLOR = QColor("#e88b3a")
BAR_PLAYED = QColor("#8bc34a")
GRID_COLOR = QColor("#2a2d3a")
TEXT_DIM = QColor("#8b90a3")
PLAYHEAD = QColor("#f0f1f5")


class WaveformWidget(QWidget):
    """Min/max peak waveform with playhead and time ruler."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self._peaks: np.ndarray | None = None
        self._duration: float = 0.0
        self._position: float = 0.0
        self._font = QFont("Consolas, Cascadia Mono, monospace")
        self._font.setPointSize(8)
        self._metric = QFontMetricsF(self._font)

    def set_audio(self, peaks: np.ndarray, duration: float) -> None:
        self._peaks = peaks
        self._duration = duration
        self._position = 0.0
        self.update()

    def clear(self) -> None:
        self._peaks = None
        self._duration = 0.0
        self._position = 0.0
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = max(0.0, min(seconds, self._duration))
        self.update()

    def has_audio(self) -> bool:
        return self._peaks is not None

    def _format_time(self, seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), DARK_BG)

        width = self.width()
        height = self.height()
        ruler = self._metric.height() + 8
        chart_bottom = height - ruler

        painter.setPen(QPen(GRID_COLOR, 1))
        painter.drawLine(0, chart_bottom, width, chart_bottom)

        if self._peaks is None or len(self._peaks) == 0:
            painter.setPen(QPen(TEXT_DIM, 1))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No waveform available")
            return

        played_ratio = self._position / self._duration if self._duration else 0.0
        played_x = int(width * played_ratio)

        peaks = self._peaks
        column_width = width / len(peaks)
        bar_scale = (chart_bottom - 4) / 2.0
        mid = chart_bottom / 2.0

        for index in range(len(peaks)):
            pmin, pmax = float(peaks[index, 0]), float(peaks[index, 1])
            x = index * column_width
            y0 = mid - pmax * bar_scale
            y1 = mid - pmin * bar_scale
            color = BAR_PLAYED if x <= played_x else BAR_COLOR
            painter.setPen(QPen(color, max(1.0, column_width - 1.0)))
            painter.drawLine(QPointF(x + column_width / 2, y0), QPointF(x + column_width / 2, y1))

        if played_x > 0:
            painter.setPen(QPen(PLAYHEAD, 1))
            painter.drawLine(played_x, 0, played_x, chart_bottom)

        painter.setPen(QPen(TEXT_DIM, 1))
        painter.setFont(self._font)
        painter.drawText(QRectF(6, chart_bottom + 2, 120, ruler - 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "0:00")
        painter.drawText(QRectF(width - 126, chart_bottom + 2, 120, ruler - 2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._format_time(self._duration))
        painter.drawText(QRectF(width / 2 - 60, chart_bottom + 2, 120, ruler - 2), Qt.AlignmentFlag.AlignCenter, self._format_time(self._position))
