from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMatrix4x4, QPainter, QPen, QPolygonF, QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFunctions_3_3_Core,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

try:
    QSurfaceFormat()
    _GL_AVAILABLE = True
except Exception:  # pragma: no cover
    _GL_AVAILABLE = False


def gl_available() -> bool:
    return _GL_AVAILABLE


def gl_context_available() -> bool:
    """True if a real GL 3.3 context can actually be created right now.

    ``gl_available()`` only checks whether Qt can build a surface format, which
    also succeeds in headless/offscreen environments where no GL context can be
    made current.  This probes for a workable context so the app can fall back
    to the software renderer.
    """
    if not _GL_AVAILABLE:
        return False
    try:
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        surface = QOffscreenSurface()
        surface.setFormat(fmt)
        surface.create()
        if not surface.isValid():
            return False
        context = QOpenGLContext()
        context.setFormat(surface.format())
        if not context.create():
            return False
        ok = context.makeCurrent(surface)
        if ok:
            context.doneCurrent()
        return ok
    except Exception:
        return False


_VERTEX_SRC = """
#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;
uniform mat4 uMvp;
uniform mat4 uModelView;
out vec3 vNormal;
void main() {
    vNormal = mat3(uModelView) * aNormal;
    gl_Position = uMvp * vec4(aPos, 1.0);
}
"""

_FRAGMENT_SRC = """
#version 330 core
in vec3 vNormal;
uniform vec4 uColor;
uniform float uWireframe;
out vec4 fragColor;
void main() {
    vec3 lightDir = normalize(vec3(0.35, 0.6, 0.7));
    vec3 n = normalize(vNormal);
    float diffuse = max(dot(n, lightDir), 0.15);
    vec4 color = vec4(uColor.rgb * diffuse, uColor.a);
    if (uWireframe > 0.5) {
        color = uColor;
    }
    fragColor = color;
}
"""

_BACKGROUND = QColor("#15161b")


class MeshViewBase(QWidget):
    """Shared orbit-camera scene logic for the GL and software renderers.

    Holds the parsed mesh, bone joints, and camera state, and implements the
    orbit interactions (drag to rotate, wheel to zoom, double-click to reset).
    Subclasses render the scene in ``paintScene``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._verts: Optional[np.ndarray] = None
        self._normals: Optional[np.ndarray] = None
        self._tris: Optional[np.ndarray] = None
        self._edges: Optional[np.ndarray] = None
        self._center = np.zeros(3, dtype=np.float32)
        self._radius = 1.0
        self._yaw = -0.6
        self._pitch = 0.4
        self._distance = 3.0
        self._target_distance = 3.0
        self._dragging = False
        self._last_pos = (0, 0)
        self._wireframe = False
        self._solid_color = QColor("#e88b3a")
        self._edge_color = QColor("#17181d")
        self._bone_color = QColor("#4fae6d")
        self._joint_data: Optional[np.ndarray] = None
        self._bone_edges: Optional[np.ndarray] = None
        self._joint_dots: Optional[np.ndarray] = None

    # ---- mesh data ----

    def set_bones(self, bones) -> None:
        self._joint_data = None
        self._bone_edges = None
        self._joint_dots = None
        if not bones:
            self.update()
            return
        points = np.array(
            [[bone["x"], bone["y"], bone["z"]] for bone in bones],
            dtype=np.float32,
        )
        self._joint_data = points
        parent_indices = [bone["parent"] for bone in bones]
        lines = []
        for index, parent in enumerate(parent_indices):
            if parent >= 0 and parent < len(bones):
                lines.append([index, parent])
        if lines:
            self._bone_edges = np.asarray(lines, dtype=np.uint32).ravel()
        self.update()

    def set_mesh(self, verts: np.ndarray, normals: np.ndarray, tris: np.ndarray, edges: np.ndarray) -> None:
        self._verts = np.ascontiguousarray(verts, dtype=np.float32)
        self._normals = np.ascontiguousarray(normals, dtype=np.float32)
        self._tris = np.ascontiguousarray(tris, dtype=np.uint32)
        self._edges = np.ascontiguousarray(edges, dtype=np.uint32)
        center = (self._verts.min(axis=0) + self._verts.max(axis=0)) / 2.0
        radius = float(np.linalg.norm(self._verts - center, axis=1).max())
        self._center = center.astype(np.float32)
        self._radius = max(radius, 1e-6)
        self._target_distance = self._radius * 2.6
        self.reset_view()

    def set_wireframe(self, enabled: bool) -> None:
        self._wireframe = enabled
        self.update()

    def reset_view(self) -> None:
        self._yaw = -0.6
        self._pitch = 0.4
        self._distance = self._target_distance
        self.update()

    # ---- camera ----

    def _eye_matrix(self) -> QMatrix4x4:
        eye = QMatrix4x4()
        eye.translate(0, 0, -self._distance)
        eye.rotate(self._pitch * 180.0 / math.pi, 1, 0, 0)
        eye.rotate(self._yaw * 180.0 / math.pi, 0, 1, 0)
        eye.translate(-self._center[0], -self._center[1], -self._center[2])
        return eye

    def _projection_matrix(self, aspect: float) -> QMatrix4x4:
        proj = QMatrix4x4()
        proj.perspective(45.0, aspect, 0.01, 1000.0)
        return proj

    # ---- interactions ----

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = (event.position().x(), event.position().y())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        x, y = event.position().x(), event.position().y()
        dx, dy = x - self._last_pos[0], y - self._last_pos[1]
        self._last_pos = (x, y)
        self._yaw -= dx * 0.01
        self._pitch += dy * 0.01
        self._pitch = max(-1.55, min(1.55, self._pitch))
        self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._distance = max(self._radius * 0.3, min(self._radius * 20.0, self._distance / factor))
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()


class MeshView(QOpenGLWidget):
    """OpenGL solid / wireframe renderer for parsed OBJ meshes.

    Same scene camera and interaction logic as :class:`MeshViewBase`; used when
    a working GL context exists.  Falls back to :class:`SoftwareMeshView`
    otherwise.
    """

    def __init__(self, parent=None):
        if not _GL_AVAILABLE:
            raise RuntimeError("OpenGL is not available on this system")
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSamples(4)
        fmt.setDepthBufferSize(24)
        self.setFormat(fmt)
        self._gl: Optional[QOpenGLFunctions_3_3_Core] = None
        self._program: Optional[QOpenGLShaderProgram] = None
        self._vao: Optional[QOpenGLVertexArrayObject] = None
        self._dirty = True
        self._verts: Optional[np.ndarray] = None
        self._normals: Optional[np.ndarray] = None
        self._tris: Optional[np.ndarray] = None
        self._edges: Optional[np.ndarray] = None
        self._center = np.zeros(3, dtype=np.float32)
        self._radius = 1.0
        self._yaw = -0.6
        self._pitch = 0.4
        self._distance = 3.0
        self._target_distance = 3.0
        self._dragging = False
        self._last_pos = (0, 0)
        self._wireframe = False
        self._solid_color = QColor("#e88b3a")
        self._edge_color = QColor("#17181d")
        self._bone_color = QColor("#4fae6d")
        self._joint_data: Optional[np.ndarray] = None
        self._bone_edges: Optional[np.ndarray] = None
        self._joint_dots: Optional[np.ndarray] = None
        self._bone_line_count = 0
        self._vbo: Optional[QOpenGLBuffer] = None
        self._vbo_edges: Optional[QOpenGLBuffer] = None
        self._vbo_bones: Optional[QOpenGLBuffer] = None
        self._ebo_tris: Optional[QOpenGLBuffer] = None

    def set_bones(self, bones) -> None:
        self._joint_data = None
        self._bone_edges = None
        self._joint_dots = None
        if not bones:
            self._build_bones()
            return
        points = np.array(
            [[bone["x"], bone["y"], bone["z"]] for bone in bones],
            dtype=np.float32,
        )
        self._joint_data = points
        parent_indices = [bone["parent"] for bone in bones]
        lines = []
        for index, parent in enumerate(parent_indices):
            if parent >= 0 and parent < len(bones):
                lines.append([index, parent])
        if lines:
            self._bone_edges = np.asarray(lines, dtype=np.uint32).ravel()
        self._build_bones()

    def set_mesh(self, verts: np.ndarray, normals: np.ndarray, tris: np.ndarray, edges: np.ndarray) -> None:
        self._verts = np.ascontiguousarray(verts, dtype=np.float32)
        self._normals = np.ascontiguousarray(normals, dtype=np.float32)
        self._tris = np.ascontiguousarray(tris, dtype=np.uint32)
        self._edges = np.ascontiguousarray(edges, dtype=np.uint32)
        center = (self._verts.min(axis=0) + self._verts.max(axis=0)) / 2.0
        radius = float(np.linalg.norm(self._verts - center, axis=1).max())
        self._center = center.astype(np.float32)
        self._radius = max(radius, 1e-6)
        self._target_distance = self._radius * 2.6
        self._dirty = True
        self.reset_view()

    def set_wireframe(self, enabled: bool) -> None:
        self._wireframe = enabled
        self.update()

    def reset_view(self) -> None:
        self._yaw = -0.6
        self._pitch = 0.4
        self._distance = self._target_distance
        self.update()

    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions_3_3_Core(self)
        self._gl.initializeOpenGLFunctions()
        self._gl.glEnable(self._gl.GL_DEPTH_TEST)
        self._gl.glEnable(self._gl.GL_MULTISAMPLE)
        self._program = QOpenGLShaderProgram(self)
        self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VERTEX_SRC)
        self._program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FRAGMENT_SRC)
        self._program.link()
        self._build_buffers()

    def _build_buffers(self) -> None:
        if self._verts is None or self._program is None:
            return
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vao.bind()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        stride = 6 * 4
        interleaved = np.hstack([self._verts, self._normals]).astype(np.float32)
        self._vbo.allocate(interleaved.tobytes(), interleaved.nbytes)
        pos_loc = self._program.attributeLocation("aPos")
        normal_loc = self._program.attributeLocation("aNormal")
        self._gl.glEnableVertexAttribArray(pos_loc)
        self._gl.glVertexAttribPointer(pos_loc, 3, self._gl.GL_FLOAT, False, stride, 0)
        self._gl.glEnableVertexAttribArray(normal_loc)
        self._gl.glVertexAttribPointer(normal_loc, 3, self._gl.GL_FLOAT, False, stride, 3 * 4)

        self._ebo_tris = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self._ebo_tris.create()
        self._ebo_tris.bind()
        self._ebo_tris.allocate(self._tris.tobytes(), self._tris.nbytes)

        if len(self._edges):
            self._vbo_edges = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._vbo_edges.create()
            self._vbo_edges.bind()
            edge_verts = np.ascontiguousarray(self._verts[self._edges.ravel()], dtype=np.float32)
            self._vbo_edges.allocate(edge_verts.tobytes(), edge_verts.nbytes)

        self._vbo_bones = None
        self._joint_dots = None
        self._bone_line_count = 0
        self._vao.release()
        self._build_bones()

    def _build_bones(self) -> None:
        if self._verts is None or self._program is None:
            return
        if self._vbo_bones is None:
            self._vbo_bones = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._vbo_bones.create()
        self._vbo_bones.bind()
        if self._joint_data is not None:
            if self._bone_edges is not None and len(self._bone_edges):
                line_verts = np.ascontiguousarray(self._joint_data[self._bone_edges], dtype=np.float32)
            else:
                line_verts = np.empty((0, 3), dtype=np.float32)
            self._vbo_bones.allocate(line_verts.tobytes(), line_verts.nbytes)
            self._bone_line_count = len(line_verts)
            self._joint_dots = np.ascontiguousarray(self._joint_data, dtype=np.float32)
        else:
            self._vbo_bones.allocate(b"", 0)
            self._joint_dots = None
            self._bone_line_count = 0
        self._vbo_bones.release()

    def paintGL(self) -> None:
        if self._gl is None or self._program is None or self._verts is None:
            return
        self._gl.glClearColor(0.082, 0.086, 0.106, 1.0)
        self._gl.glClear(self._gl.GL_COLOR_BUFFER_BIT | self._gl.GL_DEPTH_BUFFER_BIT)
        if self._dirty:
            self._build_buffers()
            self._dirty = False
        self._vao.bind()
        aspect = self.width() / max(self.height(), 1)
        proj = QMatrix4x4()
        proj.perspective(45.0, aspect, 0.01, 1000.0)
        eye = QMatrix4x4()
        eye.translate(0, 0, -self._distance)
        eye.rotate(self._pitch * 180.0 / math.pi, 1, 0, 0)
        eye.rotate(self._yaw * 180.0 / math.pi, 0, 1, 0)
        eye.translate(-self._center[0], -self._center[1], -self._center[2])
        mvp = proj * eye
        self._program.bind()
        self._program.setUniformValue("uMvp", mvp)
        self._program.setUniformValue("uModelView", eye)
        self._program.setUniformValue("uColor", self._solid_color)
        self._program.setUniformValue("uWireframe", 0.0)

        self._ebo_tris.bind()
        self._gl.glDrawElements(self._gl.GL_TRIANGLES, self._tris.size, self._gl.GL_UNSIGNED_INT, 0)
        self._ebo_tris.release()

        if self._vbo_edges is not None and self._vbo_edges.isCreated():
            self._vbo_edges.bind()
            self._gl.glEnableVertexAttribArray(0)
            self._gl.glVertexAttribPointer(0, 3, self._gl.GL_FLOAT, False, 3 * 4, 0)
            self._program.setUniformValue("uColor", self._edge_color)
            self._program.setUniformValue("uWireframe", 1.0)
            self._gl.glDrawArrays(self._gl.GL_LINES, 0, self._edges.size)
            self._vbo_edges.release()

        if self._vbo_bones is not None and self._vbo_bones.isCreated() and self._bone_line_count:
            self._vbo_bones.bind()
            self._gl.glEnableVertexAttribArray(0)
            self._gl.glVertexAttribPointer(0, 3, self._gl.GL_FLOAT, False, 3 * 4, 0)
            self._program.setUniformValue("uColor", self._bone_color)
            self._program.setUniformValue("uWireframe", 1.0)
            self._gl.glDrawArrays(self._gl.GL_LINES, 0, self._bone_line_count)
            self._vbo_bones.release()

        if self._joint_dots is not None and self._vbo_bones is not None:
            self._vbo_bones.bind()
            self._gl.glEnableVertexAttribArray(0)
            self._gl.glVertexAttribPointer(0, 3, self._gl.GL_FLOAT, False, 3 * 4, 0)
            self._program.setUniformValue("uColor", self._bone_color)
            self._program.setUniformValue("uWireframe", 1.0)
            self._gl.glPointSize(7.0)
            self._gl.glDrawArrays(self._gl.GL_POINTS, 0, len(self._joint_dots))
            self._vbo_bones.release()
        self._program.release()
        self._vao.release()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_pos = (event.position().x(), event.position().y())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event) -> None:
        if not self._dragging:
            return
        x, y = event.position().x(), event.position().y()
        dx, dy = x - self._last_pos[0], y - self._last_pos[1]
        self._last_pos = (x, y)
        self._yaw -= dx * 0.01
        self._pitch += dy * 0.01
        self._pitch = max(-1.55, min(1.55, self._pitch))
        self.update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._distance = max(self._radius * 0.3, min(self._radius * 20.0, self._distance / factor))
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        self.reset_view()


class SoftwareMeshView(MeshViewBase):
    """Software-rendered mesh preview used when no GL context is available.

    Projects the parsed mesh with an orbit camera and paints it with QPainter
    (painter's-algorithm depth sort, flat shading, edge + bone overlay), so the
    preview keeps working under offscreen / headless environments.
    """

    def paintScene(self, painter: QPainter) -> None:
        if self._verts is None or len(self._tris) == 0:
            painter.fillRect(self.rect(), QColor("#15161b"))
            return
        painter.fillRect(self.rect(), QColor("#15161b"))
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        eye = self._eye_matrix()
        proj = self._projection_matrix(w / h)
        mvp = proj * eye

        view = np.empty((len(self._verts), 4), dtype=np.float64)
        for i, v in enumerate(self._verts):
            ndc = mvp.map(QVector3D(float(v[0]), float(v[1]), float(v[2])))
            view[i] = (ndc.x(), ndc.y(), ndc.z(), 1.0)

        rows = []
        for triangle in self._tris.reshape(-1, 3):
            i0, i1, i2 = int(triangle[0]), int(triangle[1]), int(triangle[2])
            a, b, c = view[i0], view[i1], view[i2]
            if a[2] > 1.0 or b[2] > 1.0 or c[2] > 1.0:
                continue
            z = float(a[2] + b[2] + c[2]) / 3.0
            normal = self._face_normal(i0, i1, i2)
            rows.append((z, (i0, i1, i2), (a, b, c), normal))

        rows.sort(key=lambda r: r[0], reverse=True)

        light = np.array([0.35, 0.6, 0.7])
        light /= np.linalg.norm(light)
        base = self._solid_color

        for _z, (i0, i1, i2), (a, b, c), normal in rows:
            diffuse = float(max(np.dot(normal, light), 0.15))
            color = QColor(
                min(255, round(base.red() * diffuse)),
                min(255, round(base.green() * diffuse)),
                min(255, round(base.blue() * diffuse)),
            )
            poly = QPolygonF()
            for p in (a, b, c):
                poly.append(
                    QPointF(
                        (0.5 + p[0] / 2.0) * w,
                        (0.5 - p[1] / 2.0) * h,
                    )
                )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(poly)

        if self._edges.size:
            painter.setPen(QPen(self._edge_color, 1))
            for edge in self._edges.reshape(-1, 2):
                i0, i1 = int(edge[0]), int(edge[1])
                painter.drawLine(
                    (0.5 + view[i0][0] / 2.0) * w,
                    (0.5 - view[i0][1] / 2.0) * h,
                    (0.5 + view[i1][0] / 2.0) * w,
                    (0.5 - view[i1][1] / 2.0) * h,
                )

        if self._joint_data is not None and len(self._joint_data):
            joints = np.empty((len(self._joint_data), 2), dtype=np.float64)
            for i, p in enumerate(self._joint_data):
                ndc = mvp.map(QVector3D(float(p[0]), float(p[1]), float(p[2])))
                joints[i] = ((0.5 + ndc.x() / 2.0) * w, (0.5 - ndc.y() / 2.0) * h)
            painter.setPen(QPen(self._bone_color, 2))
            if self._bone_edges is not None and len(self._bone_edges):
                for edge in self._bone_edges.reshape(-1, 2):
                    i0, i1 = int(edge[0]), int(edge[1])
                    painter.drawLine(joints[i0][0], joints[i0][1], joints[i1][0], joints[i1][1])
            painter.setBrush(self._bone_color)
            painter.setPen(Qt.PenStyle.NoPen)
            for x, y in joints:
                painter.drawEllipse(x - 3, y - 3, 7, 7)

    def _face_normal(self, i0: int, i1: int, i2: int) -> np.ndarray:
        if self._normals is not None and len(self._normals) == len(self._verts):
            n = (self._normals[i0] + self._normals[i1] + self._normals[i2]) / 3.0
            norm = np.linalg.norm(n)
            if norm > 0:
                return n / norm
        a = self._verts[i1] - self._verts[i0]
        b = self._verts[i2] - self._verts[i0]
        n = np.cross(a, b)
        norm = np.linalg.norm(n)
        if norm == 0:
            return np.array([0.0, 1.0, 0.0])
        return n / norm

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paintScene(painter)
        painter.end()


__all__ = [
    "MeshView",
    "SoftwareMeshView",
    "gl_available",
    "gl_context_available",
]