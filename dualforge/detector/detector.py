from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from dualforge.compression import sniff
from dualforge.constants import PAK_MAGIC

UTOC_MAGIC = b"-==--==--==--==-"
BSA_MAGIC = b"BSA\x00"
BA2_MAGIC = b"BTD\x00"
RDAR_MAGIC = b"RDAR"
LOCRES_MAGIC = 0x324F4352
IL2CPP_METADATA_MAGIC = 0xFAB11BAF
DDS_MAGIC = b"DDS "
KTX_MAGIC = b"\xAB" + b"KTX"
KTX2_MAGIC = b"\xABKTX 20\xBB\r\n\x1A\n"
KTX1_MAGIC = b"\xABKTX 11\xBB\r\n\x1A\n"
BSA_VERSION_BY_INT = {0x67: 103, 0x68: 104, 0x69: 105}
UNITY_SIGNATURES = (b"UnityFS", b"UnityWeb", b"UnityRaw")
UNITY_SERIALIZED_VERSION_MIN = 13
UNITY_SERIALIZED_VERSION_MAX = 25
UNITY_SERIALIZED_NAMES = frozenset(
    {
        "globalgamemanagers",
        "globalgamemanagers.assets",
        "maindata",
        "data.unity3d",
        "resources.assets",
        "assets.assets",
        "sharedassets.assets",
        "assets.resS",
        "resources.resS",
    }
)


@dataclass
class Detection:
    engine: str
    kind: str
    path: str
    details: dict[str, object] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [f"Engine : {self.engine}", f"Kind   : {self.kind}"]
        for key, value in self.details.items():
            lines.append(f"{key:8s}: {value}")
        return "\n".join(lines)


def detect(path: str) -> Detection | None:
    header = _read_header(path)
    if header is None:
        return None
    return detect_header(header, Path(path).name, path)


def detect_header(header: bytes, filename: str, path: str = "") -> Detection | None:
    if len(header) >= 4 and struct.unpack_from("<I", header, 0)[0] == PAK_MAGIC:
        version = struct.unpack_from("<I", header, 4)[0] if len(header) >= 8 else None
        return Detection(
            engine="unreal",
            kind="pak",
            path=path,
            details={"pak_version": version, "encrypted_index": False},
        )
    if header.startswith(UTOC_MAGIC):
        return Detection(engine="unreal", kind="iostore-toc", path=path)
    if header.startswith(BSA_MAGIC):
        return _detect_bsa(header, path)
    if header.startswith(BA2_MAGIC):
        return _detect_ba2(header, path)
    if header.startswith(RDAR_MAGIC):
        return _detect_rdar(header, path)
    if len(header) >= 4 and struct.unpack_from("<I", header, 0)[0] == LOCRES_MAGIC:
        return Detection(engine="unreal", kind="locres", path=path)
    if len(header) >= 4 and struct.unpack_from("<I", header, 0)[0] == IL2CPP_METADATA_MAGIC:
        return _detect_il2cpp_metadata(header, path)
    if header.startswith(DDS_MAGIC):
        return _detect_dds(header, path)
    if header.startswith(KTX1_MAGIC):
        return Detection(engine="container", kind="ktx", path=path)
    if header.startswith(KTX2_MAGIC):
        return Detection(engine="container", kind="ktx2", path=path)
    lower = filename.lower()
    if lower.endswith(".ucas"):
        return Detection(engine="unreal", kind="iostore-payload", path=path)
    if lower.endswith(".utoc"):
        return Detection(engine="unreal", kind="iostore-toc", path=path)
    if lower.endswith(".pak"):
        return Detection(engine="unreal", kind="pak", path=path, details={"magic_checked": False})
    for sig in UNITY_SIGNATURES:
        if header.startswith(sig):
            return _detect_unity(header, sig, filename, path)
    if lower.endswith(".unity3d") or lower.endswith(".bundle") or lower.endswith(".assetbundle"):
        return Detection(engine="unity", kind="bundle", path=path)
    if _is_unity_serialized_header(header):
        return _detect_unity_serialized(header, path)
    if _is_unity_serialized_name(filename):
        return Detection(engine="unity", kind="serialized", path=path)
    compressed = sniff(header)
    if compressed:
        return Detection(engine="container", kind=compressed, path=path)
    return None


def _detect_bsa(header: bytes, path: str) -> Detection:
    details = {}
    if len(header) >= 8:
        version_int = struct.unpack_from("<I", header, 4)[0]
        version = BSA_VERSION_BY_INT.get(version_int)
        details["bsa_version"] = version if version is not None else version_int
    return Detection(engine="bethesda", kind="bsa", path=path, details=details)


def _detect_ba2(header: bytes, path: str) -> Detection:
    details = {}
    if len(header) >= 12:
        version = struct.unpack_from("<I", header, 4)[0]
        type_ = header[8:12].decode("ascii", "replace").strip("\x00")
        details["ba2_version"] = version
        details["ba2_type"] = type_ or "GNRL"
    return Detection(engine="bethesda", kind="ba2", path=path, details=details)


def _detect_rdar(header: bytes, path: str) -> Detection:
    details: dict[str, object] = {}
    if len(header) >= 8:
        details["rdar_version"] = struct.unpack_from("<I", header, 4)[0]
    return Detection(engine="cdpr", kind="rdar", path=path, details=details)


def _detect_il2cpp_metadata(header: bytes, path: str) -> Detection:
    details: dict[str, object] = {}
    if len(header) >= 8:
        details["metadata_version"] = struct.unpack_from("<i", header, 4)[0]
    return Detection(engine="unity", kind="il2cpp-metadata", path=path, details=details)


def _detect_dds(header: bytes, path: str) -> Detection:
    details: dict[str, object] = {}
    if len(header) >= 20:
        width, height = struct.unpack_from("<II", header, 12)
        details["width"] = width
        details["height"] = height
    return Detection(engine="container", kind="dds", path=path, details=details)


def _is_unity_serialized_header(header: bytes) -> bool:
    """Unity serialized files have no magic bytes.

    The header is: metadata_size u32, file_size u32, serialized version u32,
    data_offset u32. In practice the first two are zero and the version sits
    in a narrow range, which reliably separates them from random data.
    """
    if len(header) < 16:
        return False
    if header[:8] != b"\x00" * 8:
        return False
    version = struct.unpack_from("<I", header, 8)[0]
    return UNITY_SERIALIZED_VERSION_MIN <= version <= UNITY_SERIALIZED_VERSION_MAX


def _is_unity_serialized_name(filename: str) -> bool:
    base = Path(filename).name.lower()
    if base in UNITY_SERIALIZED_NAMES:
        return True
    if base.endswith(".assets"):
        return True
    if base.startswith("sharedassets") and base.endswith(".assets"):
        return base[12:-7].isdigit()
    if base.endswith(".ress"):
        return True
    return base.startswith("level") and base[5:].isdigit()


def _detect_unity_serialized(header: bytes, path: str) -> Detection:
    details = {}
    if len(header) >= 12:
        details["serialized_version"] = struct.unpack_from("<I", header, 8)[0]
    return Detection(engine="unity", kind="serialized", path=path, details=details)


def _detect_unity(header: bytes, sig: bytes, filename: str, path: str) -> Detection:
    version, unity_version = _parse_unityfs_header(header, len(sig))
    kind = "assetbundle" if sig == b"UnityFS" else "web-bundle"
    details = {
        "signature": sig.decode("ascii", "replace"),
        "bundle_version": version,
    }
    if unity_version:
        details["unity_version"] = unity_version
    return Detection(engine="unity", kind=kind, path=path, details=details)


def _parse_unityfs_header(header: bytes, sig_len: int) -> tuple:
    pos = sig_len
    if pos >= len(header):
        return (None, None)
    end = header.find(b"\x00", pos)
    if end == -1:
        return (None, None)
    pos = end + 1
    if pos + 4 > len(header):
        return (None, None)
    version = struct.unpack_from("<I", header, pos)[0]
    pos += 4
    end = header.find(b"\x00", pos)
    if end == -1:
        return (version, None)
    return (version, header[pos:end].decode("utf-8", "replace"))


def _read_header(path: str, size: int = 32) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(size)
    except OSError:
        return None


__all__ = [
    "Detection",
    "detect",
    "detect_header",
    "PAK_MAGIC",
    "UTOC_MAGIC",
    "BSA_MAGIC",
    "BA2_MAGIC",
    "DDS_MAGIC",
    "KTX_MAGIC",
    "LOCRES_MAGIC",
    "IL2CPP_METADATA_MAGIC",
]
