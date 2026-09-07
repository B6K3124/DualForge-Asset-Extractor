"""FBX 7.4 exporter for DualForge: binary (default) and ASCII.

Writes the variant of FBX produced by Blender/the Unity FBX exporters so the
files import cleanly into Blender, 3ds Max, Maya, Unity and Godot:

* ``write_fbx_mesh``   - skinned meshes (cluster deformer skinning), a bone
  skeleton laid out from Unity ``m_BindPose`` inverse matrices, a BindPose,
  and morph targets (BlendShape / BlendShapeChannel / deformer Geometry).
* ``write_fbx_animation`` - an AnimationStack/Take with per-node TRS channels
  driven by AnimationCurve keyframes (intended for Unity AnimationClips).

The default output is FBX 7.4 **binary**, byte-layout compatible with
Blender's own binary encoder (``io_scene_fbx.encode_bin``), which is what
Blender 3.2+ actually supports - Blender refuses the ASCII variant entirely.
Set the environment variable ``DUALFORGE_FBX_ASCII=1`` (or pass
``binary=False``) to write plain-text FBX for tools that predate Blender's
binary-only policy.  Coordinates are passed through in Unity's own Y-up,
Z-forward space (the same convention the glTF exporter uses), so both
exporters stay consistent.
"""

from __future__ import annotations

import array as _array
import math
import os
import struct
import zlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

# FBX time resolution: 1 second == 46186158000 internal KTime ticks.
_FBX_TICKS = 46186158000
_FBX_VERSION = 7400

# -- binary FBX constants (must match Blender's io_scene_fbx.encode_bin) ------
_HEAD_MAGIC = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"
# FBX has very strict CRC rules based on the file timestamp; Autodesk's FBX
# SDK validates FileId + CreationTime. Blender works around this by writing
# fixed values, and those same values pass the SDK checks.
_FILE_ID = b"\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1"
_TIME_ID = b"1970-01-01 10:00:00:000"
_FOOT_ID = b"\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e"
_FOOT_MAGIC = b"\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b"
# Some element classes need a block sentinel even when they have children-less
# props (Angled braces edge cases in the FBX SDK).
_ALWAYS_SENTINEL = {b"AnimationStack", b"AnimationLayer"}


class FbxError(Exception):
    pass


def _fmt(value: float) -> str:
    if isinstance(value, int):
        return str(value)
    text = f"{float(value):.9G}"
    return text


def _fmt_v(values: Sequence[float]) -> str:
    return ",".join(_fmt(v) for v in values)


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in " _.-" else "_" for ch in name).strip() or "unnamed"


# -------------------------------------------------------------- typed props --
class _Prop:
    """A single FBX property: binary type code, encoded bytes, ASCII text."""

    __slots__ = ("code", "payload", "display")

    def __init__(self, code: str, payload: bytes, display: str) -> None:
        self.code = code  # 'I','L','D','S','R','B' or array 'i','l','q','d','f','b'
        self.payload = payload
        self.display = display

    def size(self) -> int:
        return 1 + len(self.payload)


def _I(value: int) -> _Prop:
    return _Prop("I", struct.pack("<i", int(value)), str(int(value)))


def _L(value: int) -> _Prop:
    return _Prop("L", struct.pack("<q", int(value)), str(int(value)))


def _D(value: float) -> _Prop:
    return _Prop("D", struct.pack("<d", float(value)), _fmt(float(value)))


def _B(value: bool) -> _Prop:
    return _Prop("B", b"\x01" if value else b"\x00", "true" if value else "false")


def _S(value: str) -> _Prop:
    data = str(value).encode("utf-8")
    return _Prop("S", struct.pack("<I", len(data)) + data, f'"{value}"')


def _R(data: bytes) -> _Prop:
    return _Prop("R", struct.pack("<I", len(data)) + data, "")


def _array_prop(code: str, typecode: str, values: Sequence[Any], display: str) -> _Prop:
    arr = _array.array(typecode, values)
    raw = arr.tobytes()
    if len(raw) <= 128:
        encoding, payload = 0, raw
    else:
        encoding, payload = 1, zlib.compress(raw, 1)
    data = struct.pack("<3I", len(arr), encoding, len(payload)) + payload
    return _Prop(code, data, display)


def _F64A(values: Sequence[float]) -> _Prop:
    return _array_prop("d", "d", [float(v) for v in values], f"*{len(values)} {{ a: {_fmt_v(values)} }}")


def _I32A(values: Sequence[int]) -> _Prop:
    vals = [int(v) for v in values]
    return _array_prop("i", "i", vals, f"*{len(vals)} {{ a: {','.join(str(int(v)) for v in vals)} }}")


def _I64A(values: Sequence[int]) -> _Prop:
    vals = [int(v) for v in values]
    return _array_prop("l", "q", vals, f"*{len(vals)} {{ a: {','.join(str(int(v)) for v in vals)} }}")


class _Elem:
    __slots__ = ("name", "props", "children", "obj_id", "_plen", "_end")

    def __init__(self, name: str) -> None:
        self.name = name
        self.props: List[_Prop] = []
        self.children: List[_Elem] = []
        self.obj_id: Optional[int] = None  # set for objects added to the graph
        self._plen = 0
        self._end = 0


def _leaf(name: str, prop: _Prop) -> _Elem:
    elem = _Elem(name)
    elem.props.append(prop)
    return elem


def _props70(entries: Sequence[_Elem]) -> _Elem:
    """Wrap one or more ``P`` elements in a Properties70 block."""
    props = _Elem("Properties70")
    props.children.extend(entries)
    return props


def _p_entry(name: str, fbx_type: str, label: str, flags: str, values: Sequence[_Prop]) -> _Elem:
    """Build a ``P`` element mirroring the ASCII ``P: "name", "type", ...`` row."""
    entry = _Elem("P")
    entry.props.append(_S(name))
    entry.props.append(_S(fbx_type))
    entry.props.append(_S(label))
    entry.props.append(_S(flags))
    entry.props.extend(values)
    return entry


def _p_leaf(name: str, fbx_type: str, label: str, flags: str, value: _Prop) -> _Elem:
    return _p_entry(name, fbx_type, label, flags, (value,))


def _use_binary(binary: bool) -> bool:
    if os.environ.get("DUALFORGE_FBX_ASCII") == "1":
        return False
    return binary


# ---------------------------------------------------------------- matrices --
def _mat4_mul(a: Sequence[float], b: Sequence[float]) -> List[float]:
    out = [0.0] * 16
    for i in range(4):
        for j in range(4):
            out[i * 4 + j] = sum(a[i * 4 + k] * b[k * 4 + j] for k in range(4))
    return out


def _mat4_invert(m: Sequence[float]) -> Optional[List[float]]:
    """Invert a row-major 4x4 (Unity convention, translation in last column)."""
    inv: List[float] = [0.0] * 16
    inv[0] = m[5] * m[10] * m[15] - m[5] * m[11] * m[14] - m[9] * m[6] * m[15] + m[9] * m[7] * m[14] + m[13] * m[6] * m[11] - m[13] * m[7] * m[10]
    inv[4] = -m[4] * m[10] * m[15] + m[4] * m[11] * m[14] + m[8] * m[6] * m[15] - m[8] * m[7] * m[14] - m[12] * m[6] * m[11] + m[12] * m[7] * m[10]
    inv[8] = m[4] * m[9] * m[15] - m[4] * m[11] * m[13] - m[8] * m[5] * m[15] + m[8] * m[7] * m[13] + m[12] * m[5] * m[11] - m[12] * m[7] * m[9]
    inv[12] = -m[4] * m[9] * m[14] + m[4] * m[10] * m[13] + m[8] * m[5] * m[14] - m[8] * m[6] * m[13] - m[12] * m[5] * m[10] + m[12] * m[6] * m[9]

    inv[1] = -m[1] * m[10] * m[15] + m[1] * m[11] * m[14] + m[9] * m[2] * m[15] - m[9] * m[3] * m[14] - m[13] * m[2] * m[11] + m[13] * m[3] * m[10]
    inv[5] = m[0] * m[10] * m[15] - m[0] * m[11] * m[14] - m[8] * m[2] * m[15] + m[8] * m[3] * m[14] + m[12] * m[2] * m[11] - m[12] * m[3] * m[10]
    inv[9] = -m[0] * m[9] * m[15] + m[0] * m[11] * m[13] + m[8] * m[1] * m[15] - m[8] * m[3] * m[13] - m[12] * m[1] * m[11] + m[12] * m[3] * m[9]
    inv[13] = m[0] * m[9] * m[14] - m[0] * m[10] * m[13] - m[8] * m[1] * m[14] + m[8] * m[2] * m[13] + m[12] * m[1] * m[10] - m[12] * m[2] * m[9]

    inv[2] = m[1] * m[6] * m[15] - m[1] * m[7] * m[14] - m[5] * m[2] * m[15] + m[5] * m[3] * m[14] + m[13] * m[2] * m[7] - m[13] * m[3] * m[6]
    inv[6] = -m[0] * m[6] * m[15] + m[0] * m[7] * m[14] + m[4] * m[2] * m[15] - m[4] * m[3] * m[14] - m[12] * m[2] * m[7] + m[12] * m[3] * m[6]
    inv[10] = m[0] * m[5] * m[15] - m[0] * m[7] * m[13] - m[4] * m[1] * m[15] + m[4] * m[3] * m[13] + m[12] * m[1] * m[7] - m[12] * m[3] * m[5]
    inv[14] = -m[0] * m[5] * m[14] + m[0] * m[6] * m[13] + m[4] * m[1] * m[14] - m[4] * m[2] * m[13] - m[12] * m[1] * m[6] + m[12] * m[2] * m[5]

    inv[3] = -m[1] * m[6] * m[11] + m[1] * m[7] * m[10] + m[5] * m[2] * m[11] - m[5] * m[3] * m[10] - m[9] * m[2] * m[7] + m[9] * m[3] * m[6]
    inv[7] = m[0] * m[6] * m[11] - m[0] * m[7] * m[10] - m[4] * m[2] * m[11] + m[4] * m[3] * m[10] + m[8] * m[2] * m[7] - m[8] * m[3] * m[6]
    inv[11] = -m[0] * m[5] * m[11] + m[0] * m[7] * m[9] + m[4] * m[1] * m[11] - m[4] * m[3] * m[9] - m[8] * m[1] * m[7] + m[8] * m[3] * m[5]
    inv[15] = m[0] * m[5] * m[10] - m[0] * m[6] * m[9] - m[4] * m[1] * m[10] + m[4] * m[2] * m[9] + m[8] * m[1] * m[6] - m[8] * m[2] * m[5]

    det = m[0] * inv[0] + m[1] * inv[4] + m[2] * inv[8] + m[3] * inv[12]
    if abs(det) < 1e-12:
        return None
    scale = 1.0 / det
    return [v * scale for v in inv]


def _mat3_to_quat(row3: Sequence[float]) -> List[float]:
    """"Row-major 3x3 (9 floats) to unit quaternion [x, y, z, w]."""
    m = [float(v) for v in row3]
    trace = m[0] + m[4] + m[8]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m[7] - m[5]) / s
        y = (m[2] - m[6]) / s
        z = (m[3] - m[1]) / s
    elif m[0] > m[4] and m[0] > m[8]:
        s = math.sqrt(1.0 + m[0] - m[4] - m[8]) * 2.0
        w = (m[7] - m[5]) / s
        x = 0.25 * s
        y = (m[1] + m[3]) / s
        z = (m[2] + m[6]) / s
    elif m[4] > m[8]:
        s = math.sqrt(1.0 + m[4] - m[0] - m[8]) * 2.0
        w = (m[2] - m[6]) / s
        x = (m[1] + m[3]) / s
        y = 0.25 * s
        z = (m[5] + m[7]) / s
    else:
        s = math.sqrt(1.0 + m[8] - m[0] - m[4]) * 2.0
        w = (m[3] - m[1]) / s
        x = (m[2] + m[6]) / s
        y = (m[5] + m[7]) / s
        z = 0.25 * s
    length = math.sqrt(w * w + x * x + y * y + z * z)
    if length == 0.0:
        return [0.0, 0.0, 0.0, 1.0]
    return [x / length, y / length, z / length, w / length]


def _quat_to_euler(q: Sequence[float]) -> List[float]:
    """Quaternion [x, y, z, w] to Euler XYZ degrees (FBX Lcl Rotation order)."""
    x, y, z, w = (float(v) for v in q)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2.0 * (w * y - z * x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return [math.degrees(roll), math.degrees(pitch), math.degrees(yaw)]


def decompose_trs(row_major: Sequence[float]) -> Tuple[List[float], List[float], List[float]]:
    """Split a row-major 4x4 (Unity layout) into (translation, euler_xyz, scale)."""
    m = [float(v) for v in row_major]
    translation = [m[3], m[7], m[11]]
    columns = (
        [m[0], m[4], m[8]],
        [m[1], m[5], m[9]],
        [m[2], m[6], m[10]],
    )
    scales = [math.sqrt(sum(v * v for v in col)) for col in columns]
    safe = [s if s > 1e-12 else 1.0 for s in scales]
    rot = [v / safe[c] for c in range(3) for v in columns[c]]
    quat = _mat3_to_quat(rot)
    euler = _quat_to_euler(quat)
    return translation, euler, [1.0 if s < 1e-12 else s for s in scales]


def _to_fbx_mat4(row_major: Sequence[float]) -> List[float]:
    """FBX matrices are stored column-major; transpose the Unity rows."""
    m = [float(v) for v in row_major]
    return [m[col * 4 + row] for col in range(4) for row in range(4)]


# ------------------------------------------------------------------ graph --
class _Graph:
    """Collects FBX objects/connections and renders ASCII or binary FBX."""

    _DEF_ORDER = (
        "GlobalSettings", "Model", "Geometry", "Deformer", "BlendShape",
        "BlendShapeChannel", "Pose", "Material", "Video", "Texture",
        "AnimationStack", "AnimationLayer", "AnimationCurveNode", "AnimationCurve",
    )

    def __init__(self) -> None:
        self.objects: List[_Elem] = []
        self.connections: List[_Elem] = []
        self._next_id = 1000

    def new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def add_object(self, obj_type: str, name: str, subtype: str = "", *, name_class: str = "") -> _Elem:
        elem = _Elem(obj_type)
        elem.obj_id = self.new_id()
        elem.props.append(_L(elem.obj_id))
        # FBX object names are "<name>\x00\x01<class>" (NUL+SOH class
        # separator); Blender's importer splits on that.
        cls = name_class or obj_type
        elem.props.append(_S(f"{name}\x00\x01{cls}" if obj_type else name))
        elem.props.append(_S(subtype))  # every FBX object carries a class subtype
        self.objects.append(elem)
        return elem

    def connect(self, child: int, parent: int, prop: Optional[str] = None) -> None:
        conn = _Elem("C")
        conn.props.append(_S("OP" if prop else "OO"))
        conn.props.append(_L(child))
        conn.props.append(_L(parent))
        if prop:
            conn.props.append(_S(prop))
        self.connections.append(conn)

    connect_prop = connect

    # ---------------------------------------------------- ASCII rendering --
    def _element_lines(self, elem: _Elem, depth: int) -> List[str]:
        ind = "    " * depth
        if not elem.children:
            if elem.name == "P":
                strings = [p.display for p in elem.props[:4]]
                tail = [p.display for p in elem.props[4:]]
                sep = ", " if tail and elem.props[4].code == "S" else ","
                return [f"{ind}P: " + ", ".join(strings) + sep + ",".join(tail)]
            if elem.name == "C":
                return [f"{ind}C: " + ",".join(p.display for p in elem.props)]
            segs = [p.display for p in elem.props]
            if len(segs) == 1:
                return [f"{ind}{elem.name}: {segs[0]}"]
            return [f"{ind}{elem.name}: " + ",".join(segs)]
        if elem.props:
            head = f"{ind}{elem.name}: {elem.props[0].display} {{"
        else:
            head = f"{ind}{elem.name}:  {{"
        lines = [head]
        for child in elem.children:
            lines.extend(self._element_lines(child, depth + 1))
        lines.append(f"{ind}}}")
        return lines

    def _objects_body(self) -> str:
        lines: List[str] = []
        for elem in self.objects:
            head = f"{elem.name} {elem.props[0].display}"
            # FBX names embed the class as "<name>\x00\x01<class>"; render that
            # as the legacy "<class>::<name>" ASCII style.
            raw_name = elem.props[1].display.strip('"')
            if "\x00\x01" in raw_name:
                nm, cls = raw_name.split("\x00\x01", 1)
                name = f'"{cls}::{nm}"'
            else:
                name = f'"{raw_name}"'
            rest_extra = ", ".join(p.display for p in elem.props[2:])
            head += ", " + name + ((", " + rest_extra) if rest_extra else "")
            if elem.children:
                body = self._element_lines_each(elem.children, 2)
                lines.append(f"    {head} {{\n{body}\n    }}")
            else:
                lines.append(f"    {head}")
        return "\n".join(lines)

    def _element_lines_each(self, elems: Sequence[_Elem], depth: int) -> str:
        lines: List[str] = []
        for elem in elems:
            lines.extend(self._element_lines(elem, depth))
        return "\n".join(lines)

    def _definitions(self) -> str:
        counts: Dict[str, int] = {}
        for elem in self.objects:
            counts[elem.name] = counts.get(elem.name, 0) + 1
        lines: List[str] = []
        for obj_type in self._DEF_ORDER:
            count = counts.get(obj_type, 0)
            if count or obj_type == "GlobalSettings":
                lines.append(f"    ObjectType: \"{obj_type}\" {{\n        Count: {count}\n    }}")
        return "\n".join(lines)

    def _connections_body(self) -> str:
        lines = []
        for conn in self.connections:
            lines.append("    C: " + ",".join(p.display for p in conn.props))
        return "\n".join(lines) or "   "

    def render_ascii(self) -> str:
        return f"""; FBX 7.4.0 project file
; ----------------------------------------------------

FBXHeaderExtension:  {{
    FBXHeaderVersion: 1003
    FBXVersion: 7400
    Creator: "DualForge"
}}

GlobalSettings:  {{
    Version: 1000
    Properties70:  {{
        P: "UpAxis", "int", "Integer", "",1
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",2
        P: "FrontAxisSign", "int", "Integer", "",1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "OriginalUpAxis", "int", "Integer", "",-1
        P: "OriginalUpAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",1
        P: "OriginalUnitScaleFactor", "double", "Number", "",1
        P: "AmbientColor", "ColorRGB", "", "A",0,0,0
        P: "DefaultCamera", "KString", "", "", "Producer Perspective"
        P: "TimeMode", "enum", "", "",11
    }}
}}

Definitions:  {{
    Version: 100
    Count: {len(self.objects)}
    {self._definitions()}
}}

Objects:  {{
{self._objects_body()}
}}

Connections:  {{
{self._connections_body()}
}}
"""

    # ---------------------------------------------------- binary rendering --
    @staticmethod
    def _global_settings() -> _Elem:
        gs = _Elem("GlobalSettings")
        gs.children.append(_leaf("Version", _I(1000)))
        props = _Elem("Properties70")
        rows: List[Tuple[str, str, str, str, Sequence[_Prop]]] = [
            ("UpAxis", "int", "Integer", "", (_I(1),)),
            ("UpAxisSign", "int", "Integer", "", (_I(1),)),
            ("FrontAxis", "int", "Integer", "", (_I(2),)),
            ("FrontAxisSign", "int", "Integer", "", (_I(1),)),
            ("CoordAxis", "int", "Integer", "", (_I(0),)),
            ("CoordAxisSign", "int", "Integer", "", (_I(1),)),
            ("OriginalUpAxis", "int", "Integer", "", (_I(-1),)),
            ("OriginalUpAxisSign", "int", "Integer", "", (_I(1),)),
            ("UnitScaleFactor", "double", "Number", "", (_D(1),)),
            ("OriginalUnitScaleFactor", "double", "Number", "", (_D(1),)),
            ("AmbientColor", "ColorRGB", "", "A", (_D(0), _D(0), _D(0))),
            ("DefaultCamera", "KString", "", "", (_S("Producer Perspective"),)),
            ("TimeMode", "enum", "", "", (_I(11),)),
        ]
        for name, fbx_type, label, flags, values in rows:
            props.children.append(_p_entry(name, fbx_type, label, flags, values))
        gs.children.append(props)
        return gs

    def _definitions_bin(self) -> _Elem:
        defs = _Elem("Definitions")
        defs.children.append(_leaf("Version", _I(100)))
        defs.children.append(_leaf("Count", _I(len(self.objects))))
        counts: Dict[str, int] = {}
        for elem in self.objects:
            counts[elem.name] = counts.get(elem.name, 0) + 1
        for obj_type in self._DEF_ORDER:
            count = counts.get(obj_type, 0)
            if count or obj_type == "GlobalSettings":
                ot = _Elem("ObjectType")
                ot.props.append(_S(obj_type))
                ot.children.append(_leaf("Count", _I(count)))
                defs.children.append(ot)
        return defs

    def binary_root(self) -> _Elem:
        root = _Elem("")
        header = _Elem("FBXHeaderExtension")
        header.children.append(_leaf("FBXHeaderVersion", _I(1003)))
        header.children.append(_leaf("FBXVersion", _I(_FBX_VERSION)))
        header.children.append(_leaf("Creator", _S("DualForge")))
        file_id = _Elem("FileId")
        file_id.props.append(_R(_FILE_ID))
        creation = _Elem("CreationTime")
        creation.props.append(_S("1970-01-01 10:00:00:000"))
        creator = _Elem("Creator")
        creator.props.append(_S("DualForge"))
        objects = _Elem("Objects")
        objects.children.extend(self.objects)
        connections = _Elem("Connections")
        connections.children.extend(self.connections)
        root.children.extend(
            (header, file_id, creation, creator, self._global_settings(), self._definitions_bin(), objects, connections)
        )
        return root


def _write_binary(path: str, graph: _Graph) -> str:
    root = graph.binary_root()
    top = root.children

    def calc(elem: _Elem, offset: int, is_last: bool) -> int:
        offset += 12 + 1 + len(elem.name)  # uint[3] + name length byte + id
        elem._plen = sum(p.size() for p in elem.props)
        offset += elem._plen
        if elem.children:
            last = elem.children[-1]
            for child in elem.children:
                offset = calc(child, offset, child is last)
            offset += 13  # block sentinel
        elif (not elem.props and not is_last) or elem.name.encode() in _ALWAYS_SENTINEL:
            offset += 13
        elem._end = offset
        return offset

    size = len(_HEAD_MAGIC) + 4
    for idx, elem in enumerate(top):
        size = calc(elem, size, elem is top[-1])

    out = bytearray()
    out += _HEAD_MAGIC
    out += struct.pack("<I", _FBX_VERSION)

    def write_elem(elem: _Elem, is_last: bool) -> None:
        out.extend(struct.pack("<III", elem._end, len(elem.props), elem._plen))
        name = elem.name.encode()
        out.extend(bytes((len(name),)) + name)
        for prop in elem.props:
            out.extend(prop.code.encode())
            out.extend(prop.payload)
        if elem.children:
            last = elem.children[-1]
            for child in elem.children:
                write_elem(child, child is last)
            out.extend(b"\x00" * 13)
        elif (not elem.props and not is_last) or elem.name.encode() in _ALWAYS_SENTINEL:
            out.extend(b"\x00" * 13)

    for idx, elem in enumerate(top):
        write_elem(elem, elem is top[-1])

    # trailing block sentinel: ends the top-level stream so the FBX SDK /
    # Blender parse loop stops (read_elem returns None on end_offset == 0).
    out.extend(b"\x00" * 13)

    # footer (identical layout to Blender's encode_bin._write_footer)
    out += _FOOT_ID
    out += b"\x00" * 4
    pad = ((len(out) + 15) & ~15) - len(out)
    if pad == 0:
        pad = 16
    out += b"\x00" * pad
    out += struct.pack("<I", _FBX_VERSION)
    out += b"\x00" * 120
    out += _FOOT_MAGIC

    with open(path, "wb") as fh:
        fh.write(out)
    return path


# ------------------------------------------------------------- mesh export --
def write_fbx_mesh(
    path: str,
    name: str,
    vertices: Sequence[Sequence[float]],
    triangles: Sequence[Sequence[int]],
    normals: Optional[Sequence[Sequence[float]]] = None,
    uvs: Optional[Sequence[Sequence[float]]] = None,
    *,
    bone_names: Optional[Sequence[str]] = None,
    bone_parents: Optional[Sequence[int]] = None,
    bind_matrices: Optional[Sequence[Sequence[float]]] = None,
    joints: Optional[Sequence[Sequence[int]]] = None,
    weights: Optional[Sequence[Sequence[float]]] = None,
    blendshapes: Optional[Sequence[Dict[str, Any]]] = None,
    binary: bool = True,
) -> str:
    """Write a (optionally skinned) mesh to FBX 7.4 (binary by default).

    ``bind_matrices`` follow Unity conventions: row-major 4x4 *inverse* bind
    poses (``Mesh.bindposes``), one per bone.  Every cluster gets
    ``TransformLink`` = inverse of each bind matrix (i.e. the bone's rest
    world transform) and an identity ``Transform``, which is exactly the
    layout Blender expects. ``blendshapes`` are ``{"name", "positions",
    "normals"}`` dicts with per-vertex deltas.  Pass ``binary=False`` for the
    legacy ASCII text variant.
    """
    if not vertices or not triangles:
        raise FbxError("mesh has no geometry to export")

    graph = _Graph()
    node_name = _sanitize(name) or "Mesh"

    root = graph.add_object("Model", "RootNull", "Null")
    graph.connect(root.obj_id, 0)
    root.children.append(_props70((_p_entry("Lcl Translation", "Lcl Translation", "", "A", _d3(0)),)))

    skinned = bool(bone_names and bone_parents and bind_matrices and joints and weights)

    # ---- bone nodes -------------------------------------------------------
    bone_ids: List[_Elem] = []
    if skinned:
        world: List[List[float]] = []
        for bind in bind_matrices:
            rest_world = _mat4_invert([float(v) for v in bind])
            world.append(rest_world or [float(v) for v in bind])
        local: List[List[float]] = []
        for idx in range(len(world)):
            parent = bone_parents[idx]
            if 0 <= parent < len(world):
                parent_world_inv = _mat4_invert(world[parent])
                local.append(_mat4_mul(parent_world_inv or _identity(), world[idx]))
            else:
                local.append(world[idx])
        for idx in range(len(bone_names)):
            translation, euler, scale = decompose_trs(local[idx])
            props = (
                _p_entry("Lcl Translation", "Lcl Translation", "", "A", _num3(translation)),
                _p_entry("Lcl Rotation", "Lcl Rotation", "", "A", _num3(euler)),
                _p_entry("Lcl Scaling", "Lcl Scaling", "", "A", _num3(scale)),
            )
            bone = graph.add_object("Model", _sanitize(str(bone_names[idx])) or f"Bone_{idx}", "LimbNode")
            bone.children.append(_props70(props))
            bone_ids.append(bone)

    # ---- mesh geometry ----------------------------------------------------
    pos_flat = [float(v) for vert in vertices for v in vert[:3]]
    normal_flat: List[float] = []
    if normals:
        for n in normals:
            normal_flat.extend((float(n[0]), float(n[1]), float(n[2])))
    uv_flat: List[float] = []
    if uvs:
        for uv in uvs:
            uv_flat.extend((float(uv[0]), 1.0 - float(uv[1])))  # FBX V is up

    flat_index: List[int] = []
    for tri in triangles:
        a, b, c = int(tri[0]), int(tri[1]), int(tri[2])
        flat_index.extend((c, b, -(a + 1)))  # FBX polygons are CCW + negated end

    geometry = graph.add_object("Geometry", node_name, "Mesh")
    geom_kids: List[_Elem] = [_leaf("Vertices", _F64A(pos_flat))]
    geom_kids.append(_leaf("PolygonVertexIndex", _I32A(flat_index)))
    geom_kids.append(_leaf("GeometryVersion", _I(124)))
    if normal_flat:
        layer = _Elem("LayerElementNormal")
        layer.props.append(_I(0))
        layer.children.extend(
            (
                _leaf("Version", _I(101)),
                _leaf("Name", _S("")),
                _leaf("MappingInformationType", _S("ByVertice")),
                _leaf("ReferenceInformationType", _S("Direct")),
                _leaf("Normals", _F64A(normal_flat)),
            )
        )
        geom_kids.append(layer)
    if uv_flat:
        layer = _Elem("LayerElementUV")
        layer.props.append(_I(0))
        layer.children.extend(
            (
                _leaf("Version", _I(101)),
                _leaf("Name", _S("UVMap")),
                _leaf("MappingInformationType", _S("ByVertice")),
                _leaf("ReferenceInformationType", _S("Direct")),
                _leaf("UV", _F64A(uv_flat)),
            )
        )
        geom_kids.append(layer)
    mat_layer = _Elem("LayerElementMaterial")
    mat_layer.props.append(_I(0))
    mat_layer.children.extend(
        (
            _leaf("Version", _I(101)),
            _leaf("Name", _S("")),
            _leaf("MappingInformationType", _S("AllSame")),
            _leaf("ReferenceInformationType", _S("IndexToDirect")),
            _leaf("Materials", _I32A((0,))),
        )
    )
    geom_kids.append(mat_layer)
    geometry.children = geom_kids

    # ---- mesh node --------------------------------------------------------
    mesh_model = graph.add_object("Model", node_name, "Mesh")
    mesh_model.children.append(
        _props70(
            (
                _p_entry("Lcl Translation", "Lcl Translation", "", "A", _d3(0)),
                _p_entry("GeometricTranslation", "GeometricTranslation", "", "A", _d3(0)),
                _p_entry("GeometricRotation", "GeometricRotation", "", "A", _d3(0)),
                _p_entry("GeometricScaling", "GeometricScaling", "", "A", (_D(1), _D(1), _D(1))),
            )
        )
    )
    graph.connect(mesh_model.obj_id, root.obj_id)

    # ---- skin deformer ----------------------------------------------------
    skin_id: Optional[int] = None
    cluster_ids: List[int] = []
    if skinned:
        skin = graph.add_object("Deformer", f"{node_name}_Skin", "Skin")
        skin.children.extend(
            (_leaf("Version", _I(101)), _leaf("Link_DeformAcuracy", _I(50)), _leaf("SkinType", _S("Rigid")))
        )
        skin_id = skin.obj_id
        graph.connect_prop(skin_id, geometry.obj_id, "Deformers")

        bone_vertex_weights: Dict[int, List[Tuple[int, float]]] = {}
        for vert_index, (vert_joints, vert_weights) in enumerate(zip(joints, weights)):
            pair = list(zip(vert_joints, vert_weights))
            pair = sorted(pair, key=lambda item: -float(item[1]))[:4]
            total = sum(float(w) for _, w in pair) or 1.0
            for bone_index, wt in pair:
                bone_index = int(bone_index)
                if not (0 <= bone_index < len(bone_ids)):
                    continue
                bone_vertex_weights.setdefault(bone_index, []).append(
                    (vert_index, float(wt) / total)
                )

        for bone_index, influences in sorted(bone_vertex_weights.items()):
            cluster_vertices = [vertex for vertex, _ in influences]
            cluster_weights = [weight for _, weight in influences]
            rest_world = world[bone_index]
            cluster = graph.add_object("Deformer", f"{node_name}.{_sanitize(str(bone_names[bone_index]))}.Weight", "Cluster")
            cluster.children.extend(
                (
                    _leaf("Version", _I(100)),
                    _leaf("UserData", _S("")),
                    _leaf("Indexes", _I32A(cluster_vertices)),
                    _leaf("Weights", _F64A(cluster_weights)),
                    _leaf("Transform", _F64A(_identity())),
                    _leaf("TransformLink", _F64A(_to_fbx_mat4(rest_world))),
                    _leaf("TransformAssociateModel", _F64A(_to_fbx_mat4(rest_world))),
                )
            )
            cluster_ids.append(cluster.obj_id)
            graph.connect_prop(cluster.obj_id, skin_id, "SubDeformer")
            graph.connect_prop(bone_ids[bone_index].obj_id, cluster.obj_id, "Model")

        # ---- bind pose ----------------------------------------------------
        pose = graph.add_object("Pose", "Pose", "BindPose")
        pose_kids: List[_Elem] = [
            _leaf("Type", _S("BindPose")),
            _leaf("NbPoseNodes", _I(len(bone_ids))),
        ]
        for idx in range(len(bone_ids)):
            node = _Elem("PoseNode")
            node.children.extend(
                (_leaf("Node", _L(bone_ids[idx].obj_id)), _leaf("Matrix", _F64A(_to_fbx_mat4(world[idx]))))
            )
            pose_kids.append(node)
        pose.children = pose_kids
        graph.connect_prop(pose.obj_id, mesh_model.obj_id, "Pose")
        if bone_ids:
            graph.connect_prop(mesh_model.obj_id, bone_ids[0].obj_id, "Skeleton")

    # ---- morph targets ----------------------------------------------------
    for shape_index, shape in enumerate(blendshapes or []):
        shape_name = _sanitize(str(shape.get("name", "Shape")))
        deltas = shape.get("positions") or []
        normal_deltas = shape.get("normals") or []
        affected: List[int] = []
        delta_flat: List[float] = []
        normal_flat_delta: List[float] = []
        for idx, delta in enumerate(deltas):
            d = [float(v) for v in delta[:3]]
            if math.sqrt(sum(v * v for v in d)) > 1e-6:
                affected.append(idx)
                delta_flat.extend(d)
                if idx < len(normal_deltas):
                    nd = normal_deltas[idx]
                    normal_flat_delta.extend((float(nd[0]), float(nd[1]), float(nd[2])))
        if not affected:
            continue
        shape_geo = graph.add_object("Geometry", f"{node_name}.{shape_name}", "Mesh")
        shape_geo_kids = [_leaf("Indexes", _I32A(affected)), _leaf("Vertices", _F64A(delta_flat))]
        if len(normal_flat_delta) == len(delta_flat):
            shape_geo_kids.append(_leaf("Normals", _F64A(normal_flat_delta)))
        shape_geo.children = shape_geo_kids
        channel = graph.add_object("BlendShapeChannel", shape_name)
        channel.children.extend((_leaf("Version", _I(100)), _leaf("DeformPercent", _I(100))))
        blend_shape = graph.add_object("BlendShape", f"{node_name}Morph")
        blend_shape.children.append(_leaf("Version", _I(100)))
        graph.connect(channel.obj_id, blend_shape.obj_id)
        graph.connect(blend_shape.obj_id, geometry.obj_id)
        graph.connect_prop(channel.obj_id, blend_shape.obj_id, "SubDeformer")
        graph.connect_prop(blend_shape.obj_id, geometry.obj_id, "Deformers")

    # ---- hierarchy connections ------------------------------------------
    if skinned:
        for idx, parent in enumerate(bone_parents):
            if 0 <= parent < len(bone_ids):
                graph.connect(bone_ids[idx].obj_id, bone_ids[parent].obj_id)
            else:
                graph.connect(bone_ids[idx].obj_id, root.obj_id)
    graph.connect(geometry.obj_id, mesh_model.obj_id)

    if not _use_binary(binary):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(graph.render_ascii())
        return path
    return _write_binary(path, graph)


def _d3(value: float) -> Tuple[_Prop, _Prop, _Prop]:
    return (_D(value), _D(value), _D(value))


def _num3(values: Sequence[float]) -> Tuple[_Prop, _Prop, _Prop]:
    return (_D(values[0]), _D(values[1]), _D(values[2]))


def _identity() -> List[float]:
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


# ---------------------------------------------------------- animation export
def write_fbx_animation(
    path: str,
    name: str,
    tracks: Dict[str, Dict[str, List[Tuple[float, Sequence[float]]]]],
    fps: float = 60.0,
    binary: bool = True,
) -> str:
    """Write an AnimationClip as an FBX take (AnimationStack + channels).

    ``tracks`` maps node names to ``{"translation"|"rotation"|"scale":
    [(time, values), ...]}`` — exactly the structure produced by
    ``dualforge.export.unity_skin.animation_tracks``.  Times are seconds; FBX
    KTime resolution is applied per keyframe.  Pass ``binary=False`` for the
    legacy ASCII text variant.
    """
    tracks = tracks or {}
    if not tracks:
        raise FbxError("animation has no keyframes to export")

    graph = _Graph()
    clip_name = _sanitize(name) or "clip"
    root = graph.add_object("Model", "RootNull", "Null")
    graph.connect(root.obj_id, 0)
    root.children.append(_props70((_p_entry("Lcl Translation", "Lcl Translation", "", "A", _d3(0)),)))

    # one model node per animated path
    node_ids: Dict[str, int] = {}
    for node_name in tracks:
        node_name = _sanitize(str(node_name)) or "Node"
        node = graph.add_object("Model", node_name, "Null")
        node.children.append(_props70((_p_entry("Lcl Translation", "Lcl Translation", "", "A", _d3(0)),)))
        node_ids[node_name] = node.obj_id
        graph.connect(node.obj_id, root.obj_id)

    channels = ("translation", "rotation", "scale")
    target_builder = {"translation": "Lcl Translation", "rotation": "Lcl Rotation", "scale": "Lcl Scaling"}
    axis_channel = ("d|X", "d|Y", "d|Z")

    def make_curve(prefix: str, times: List[int], values: List[float]) -> int:
        curve = graph.add_object(
            "AnimationCurve", f"{prefix}_T_{times[0] if times else 0}_{len(values)}", "", name_class="AnimCurve"
        )
        curve.children.extend(
            (
                _leaf("Default", _I(0)),
                _leaf("Keyed", _B(True)),
                _leaf("KeyVer", _I(0)),
                _leaf("KeyTime", _I64A(times)),
                _leaf("KeyValueFloat", _F64A(values)),
                _leaf("KeyAttrFlags", _I32A([8] * len(times))),
                _leaf("KeyAttrDataFloat", _I32A([0] * len(times))),
                _leaf("KeyAttrRefCount", _I32A([0] * len(times))),
            )
        )
        return curve.obj_id

    all_times = [t for td in tracks.values() for ch in td.values() for t, _ in ch]
    end_time = round(max(all_times) * _FBX_TICKS) if all_times else 0
    stack = graph.add_object("AnimationStack", f"AnimStack::{clip_name}", "", name_class="AnimStack")
    stack.children.append(
        _props70(
            (
                _p_entry("LocalTimeSpan", "KTime", "Time", "", (_L(0), _L(end_time))),
                _p_entry("Take", "KString", "Take", "", (_S(clip_name),)),
            )
        )
    )
    graph.connect(stack.obj_id, root.obj_id)
    layer = graph.add_object("AnimationLayer", f"AnimLayer::{clip_name}", "", name_class="AnimLayer")
    layer.children.append(_leaf("Version", _I(100)))
    graph.connect(layer.obj_id, stack.obj_id)
    graph.connect_prop(layer.obj_id, stack.obj_id, "Takes")

    for node_name, target_data in tracks.items():
        safe_name = _sanitize(str(node_name)) or "Node"
        model_id = node_ids.get(safe_name)
        if model_id is None:
            continue
        for target in channels:
            keyframes = target_data.get(target)
            if not keyframes:
                continue
            times = [round(float(t) * _FBX_TICKS) for t, _ in keyframes]
            builder = target_builder[target]
            curve_node = graph.add_object(
                "AnimationCurveNode", f"AnimCurveNode::{builder}", "", name_class="AnimCurveNode"
            )
            curve_node.children.append(
                _props70(
                    (
                        _p_leaf("d", "Compound", "", "A", _I(1)),
                        _p_leaf("x", "Number", "", "A", _I(1)),
                        _p_leaf("y", "Number", "", "A", _I(1)),
                        _p_leaf("z", "Number", "", "A", _I(1)),
                    )
                )
            )
            graph.connect_prop(curve_node.obj_id, model_id, builder)
            graph.connect(curve_node.obj_id, layer.obj_id)
            for axis in range(3):
                values = [float(v[axis]) for _, v in keyframes]
                curve_id = make_curve(f"{safe_name}_{builder}", times, values)
                graph.connect_prop(curve_id, curve_node.obj_id, axis_channel[axis])

    if not _use_binary(binary):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(graph.render_ascii())
        return path
    return _write_binary(path, graph)


__all__ = ["FbxError", "decompose_trs", "write_fbx_animation", "write_fbx_mesh"]