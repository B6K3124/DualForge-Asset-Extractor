from __future__ import annotations


from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea

OFFSET_COLOR = QColor("#6a7087")
HEX_COLOR = QColor("#d7dae3")
BYTE_HIGHLIGHT = QColor("#4fae6d")
ASCII_COLOR = QColor("#9aa0b4")
DIM_CHAR = QColor("#5a5f73")
HEADER_COLOR = QColor("#8b90a3")


class HexView(QAbstractScrollArea):
    """Offset / hex / ASCII byte viewer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: bytes = b""
        self._width = 16
        self._line_height = 19.0
        self._font = QFont("Consolas, Cascadia Mono, monospace")
        self._font.setPointSize(9)
        self._metric = QFontMetricsF(self._font)
        self._hex_chars = self._metric.horizontalAdvance("00") + self._metric.horizontalAdvance(" ")
        self._ascii_chars = self._metric.horizontalAdvance(" ")
        self.setMouseTracking(True)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def set_data(self, data: bytes) -> None:
        self._data = data
        rows = (len(data) + self._width - 1) // self._width
        self.verticalScrollBar().setRange(0, max(0, rows - 1))
        self.verticalScrollBar().setValue(0)
        self.viewport().update()

    def clear(self) -> None:
        self.set_data(b"")

    def _content_width(self) -> float:
        offset = self._metric.horizontalAdvance("00000000")
        hex_part = self._width * self._hex_chars
        ascii_part = self._width * self._metric.horizontalAdvance(" ") + self._width * self._metric.horizontalAdvance("X")
        return offset + 2 * self._metric.horizontalAdvance(" ") * 2 + hex_part + self._metric.horizontalAdvance("  ") + ascii_part + 20

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), QColor("#15161b"))
        painter.setFont(self._font)
        top = self.verticalScrollBar().value()
        first_row = max(0, top - 1)
        visible_rows = int(self.viewport().height() / self._line_height) + 2
        left = 10.0
        offset_width = self._metric.horizontalAdvance("00000000")

        painter.setPen(QPen(HEADER_COLOR, 1))
        painter.drawText(QPointF(left, self._metric.height()), "Offset")
        painter.drawText(QPointF(left + offset_width + 28, self._metric.height()), "Bytes")
        painter.drawText(QPointF(left + offset_width + 28 + self._width * self._hex_chars + 16, self._metric.height()), "ASCII")
        painter.setPen(QPen(QColor("#2a2d3a"), 1))
        painter.drawLine(0, int(self._metric.height()) + 6, self.viewport().width(), int(self._metric.height()) + 6)

        header_gap = self._metric.height() + 10
        for row in range(first_row, first_row + visible_rows):
            offset = row * self._width
            if offset >= len(self._data):
                break
            y = header_gap + row * self._line_height + self._metric.ascent()
            chunk = self._data[offset : offset + self._width]
            painter.setPen(QPen(OFFSET_COLOR, 1))
            painter.drawText(QPointF(left, y), f"{offset:08x}")
            x_hex = left + offset_width + 28
            for _index, byte in enumerate(chunk):
                painter.setPen(QPen(HEX_COLOR if byte != 0 else DIM_CHAR, 1))
                painter.drawText(QPointF(x_hex, y), f"{byte:02x}")
                x_hex += self._hex_chars
            x_ascii = left + offset_width + 28 + self._width * self._hex_chars + 16
            for byte in chunk:
                painter.setPen(QPen(ASCII_COLOR if 32 <= byte < 127 else DIM_CHAR, 1))
                painter.drawText(QPointF(x_ascii, y), chr(byte) if 32 <= byte < 127 else ".")
                x_ascii += self._ascii_chars

    def mouseWheelEvent(self, event) -> None:
        delta = -event.angleDelta().y() / 120
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - int(delta) * 3)

    def wheelEvent(self, event) -> None:
        self.mouseWheelEvent(event)
