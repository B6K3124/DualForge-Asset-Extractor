from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView


def _checker_brush(
    size: int = 12,
    light: QColor | None = None,
    dark: QColor | None = None,
) -> QBrush:
    light = QColor("#ffffff") if light is None else light
    dark = QColor("#d4d7de") if dark is None else dark
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(dark)
    painter = QPainter(pixmap)
    painter.fillRect(QRectF(0, 0, size, size), light)
    painter.fillRect(QRectF(size, size, size, size), light)
    painter.end()
    return QBrush(pixmap)


class ImageView(QGraphicsView):
    """Zoomable / pannable image viewer with checkered alpha background."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(_checker_brush())
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None
        self._fit_pending = False

    def set_image(self, image) -> None:
        self._scene.clear()
        self._item = self._scene.addPixmap(QPixmap.fromImage(image))
        self._fit_pending = True
        self._fit_soon()

    def has_image(self) -> bool:
        return self._item is not None

    def _fit_soon(self) -> None:
        if self._fit_pending and self.isVisible():
            self.fit_in_view()
            self._fit_pending = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._fit_pending:
            self.fit_in_view()
            self._fit_pending = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_pending and self.isVisible():
            self.fit_in_view()
            self._fit_pending = False

    def fit_in_view(self) -> None:
        if self._item is None:
            return
        rect = self._item.boundingRect()
        if rect.width() == 0 or rect.height() == 0:
            return
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(rect.center())

    def zoom_in(self) -> None:
        self.scale(1.25, 1.25)

    def zoom_out(self) -> None:
        self.scale(0.8, 0.8)

    def zoom_actual(self) -> None:
        if self._item is None:
            return
        self.resetTransform()
        self.centerOn(self._item.boundingRect().center())

    def wheelEvent(self, event) -> None:
        if self._item is None:
            super().wheelEvent(event)
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#15161b"))
        painter.fillRect(rect, _checker_brush())
