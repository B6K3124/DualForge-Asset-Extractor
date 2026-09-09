from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PySide6.QtGui import QImage

from dualforge.export.gltf_reader import MeshGeometry

MAX_PREVIEW_BYTES = 256 * 1024
MAX_WAV_BYTES = 512 * 1024 * 1024


def format_bytes(size: int) -> str:
    size = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def pil_to_qimage(image) -> QImage:
    image = image.convert("RGBA")
    width, height = image.size
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, width, height, QImage.Format.Format_RGBA8888)
    return qimage.copy()


def guess_text(data: bytes) -> bool:
    if not data:
        return False
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not decoded.strip():
        return False
    control = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\t\r\n")
    return control / max(len(decoded), 1) < 0.02


def format_hex_lines(data: bytes, width: int = 16) -> List[str]:
    lines: List[str] = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:08x}  {hex_part}  {ascii_part}")
    return lines


def make_hex(data: bytes, width: int = 16, limit: int = MAX_PREVIEW_BYTES) -> str:
    shown = data[:limit]
    lines = format_hex_lines(shown, width)
    if len(data) > limit:
        lines.append(f"... {len(data) - limit} bytes omitted ...")
    return "\n".join(lines)


def wav_peaks(path: str, bins: int = 1200) -> Tuple[np.ndarray, float, int, int]:
    import wave

    peaks = np.zeros((bins, 2), dtype=np.float32)
    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.getnframes()
        duration = frames / rate if rate else 0.0
        if frames == 0 or rate == 0:
            return peaks, duration, rate, channels
        raw = wf.readframes(frames)
        if len(raw) > MAX_WAV_BYTES:
            raw = raw[:MAX_WAV_BYTES]
        if width == 1:
            dtype = np.uint8
        elif width == 2:
            dtype = np.int16
        else:
            dtype = np.int32
        data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
        if channels > 1:
            data = data[: (len(data) // channels) * channels].reshape(-1, channels)
            data = data.mean(axis=1)
        sample_count = len(data)
        if sample_count == 0:
            return peaks, duration, rate, channels
        peak_abs = float(np.abs(data).max())
        if peak_abs > 0:
            data = data / peak_abs
        boundaries = np.linspace(0, sample_count, bins + 1).astype(np.int64)
        for i in range(bins):
            segment = data[boundaries[i] : boundaries[i + 1]]
            if segment.size:
                peaks[i, 0] = segment.min()
                peaks[i, 1] = segment.max()
    return peaks, duration, rate, channels


def _compute_normals(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    normals = np.zeros((len(verts), 3), dtype=np.float32)
    if len(tris) == 0:
        return normals
    a = verts[tris[:, 0]]
    b = verts[tris[:, 1]]
    c = verts[tris[:, 2]]
    face_normals = np.cross(b - a, c - a)
    lengths = np.linalg.norm(face_normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    face_normals = face_normals / lengths
    np.add.at(normals, tris.ravel(), np.repeat(face_normals, 3, axis=0))
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return normals / lengths


def parse_obj(data: bytes):
    verts: List[Tuple[float, float, float]] = []
    uvs: List[Tuple[float, float]] = []
    faces: List[List[int]] = []
    for line in data.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            try:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif parts[0] == "vt" and len(parts) >= 3:
            try:
                uvs.append((float(parts[1]), float(parts[2])))
            except ValueError:
                continue
        elif parts[0] == "f" and len(parts) >= 4:
            indices: List[int] = []
            valid = True
            for part in parts[1:]:
                try:
                    indices.append(int(part.split("/")[0]) - 1)
                except ValueError:
                    valid = False
                    break
            if valid and len(indices) >= 3:
                faces.append(indices)
    if not verts:
        return None
    v = np.asarray(verts, dtype=np.float32)
    tri_list: List[Tuple[int, int, int]] = []
    edge_set = set()
    for face in faces:
        for i in range(1, len(face) - 1):
            tri_list.append((face[0], face[i], face[i + 1]))
        for i in range(len(face)):
            a, b = face[i], face[(i + 1) % len(face)]
            edge_set.add((min(a, b), max(a, b)))
    t = np.asarray(tri_list, dtype=np.uint32).reshape(-1, 3)
    e = np.asarray(sorted(edge_set), dtype=np.uint32).reshape(-1, 2)
    n = _compute_normals(v, t)
    uv = None
    if uvs:
        padded = np.zeros((len(v), 2), dtype=np.float32)
        for i, val in enumerate(uvs[: len(v)]):
            padded[i] = val
        uv = padded
    return MeshGeometry(v, n, t, e, uv=uv)


def cache_key(archive_path: str, size: int) -> str:
    digest = hashlib.sha256(f"{archive_path}:{size}".encode("utf-8")).hexdigest()[:16]
    return digest


def write_cached(cache_root: str, key: str, filename: str, data: bytes) -> str:
    directory = Path(cache_root) / key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(data)
    return str(path)


def read_cached(cache_root: str, key: str, filename: str) -> Optional[bytes]:
    path = Path(cache_root) / key / filename
    if path.is_file():
        try:
            return path.read_bytes()
        except OSError:
            return None
    return None


def wav_info(path: str) -> Tuple[int, int]:
    import wave

    with wave.open(path, "rb") as wf:
        return wf.getframerate(), wf.getnframes()


def sniff_image(data: bytes) -> Optional["QImage"]:
    """Decode a raw image payload by magic number (png/jpeg/gif/bmp/webp)."""
    from PySide6.QtGui import QImage

    magics = (
        (b"\x89PNG\r\n\x1a\n", QImage.Format.Format_RGBA8888),
        (b"\xff\xd8\xff", QImage.Format.Format_RGB32),
        (b"GIF87a", QImage.Format.Format_RGB32),
        (b"GIF89a", QImage.Format.Format_RGB32),
        (b"BM", QImage.Format.Format_RGB32),
    )
    for magic, _fmt in magics:
        if data.startswith(magic):
            image = QImage.fromData(data)
            if not image.isNull():
                return image
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        image = QImage.fromData(data)
        if not image.isNull():
            return image
    # GPU texture containers (DDS / KTX1 / KTX2) aren't readable by Qt/ImageMagick;
    # decode them in pure Python, then hand the result to Qt.
    if data[:4] == b"DDS " or data[:12] in (b"\xABKTX 11\xBB\r\n\x1A\n", b"\xABKTX 20\xBB\r\n\x1A\n"):
        try:
            from dualforge.export.texture_decode import decode_texture_data

            decoded = decode_texture_data(data)
            if decoded is not None:
                return pil_to_qimage(decoded)
        except Exception:
            return None
    return None


def sniff_audio(data: bytes, filename: str, cache_root: str, key: str):
    """Decode an audio payload by magic; returns a preview dict or None."""
    import numpy as np

    ext = Path(filename).suffix.lstrip(".").lower()
    is_wav = data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    is_ogg = data.startswith(b"OggS")
    is_flac = data.startswith(b"fLaC")
    VGMSTREAM_EXTS = {"wem", "fsb", "vag", "at9", "adx", "hca", "opus", "bnk", "mp3", "m4a", "aac"}
    if not (is_wav or is_ogg or is_flac or ext in VGMSTREAM_EXTS):
        return None
    stem = Path(filename).stem or "audio"
    if is_wav:
        wav_path = write_cached(cache_root, key, f"{stem}.wav", data)
        peaks, duration, rate, channels = wav_peaks(wav_path)
        return {
            "audio_path": wav_path,
            "peaks": peaks,
            "duration": duration,
            "sample_rate": rate,
            "channels": channels,
        }
    try:
        from dualforge.audio import Vgmstream
    except Exception:
        return None
    vg = Vgmstream()
    if vg.available():
        raw_path = write_cached(cache_root, key, f"{stem}.{ext or 'bin'}", data)
        try:
            wav_path = vg.convert(raw_path, str(Path(cache_root) / key / stem), "wav")
            peaks, duration, rate, channels = wav_peaks(wav_path)
            return {
                "audio_path": wav_path,
                "peaks": peaks,
                "duration": duration,
                "sample_rate": rate,
                "channels": channels,
            }
        except Exception:
            pass
    raw_path = write_cached(cache_root, key, f"{stem}.{ext or 'bin'}", data)
    return {
        "audio_path": raw_path,
        "peaks": np.zeros((2, 2)),
        "duration": 0.0,
        "sample_rate": 0,
        "channels": 0,
    }


__all__ = [
    "MAX_PREVIEW_BYTES",
    "cache_key",
    "format_bytes",
    "format_hex_lines",
    "guess_text",
    "make_hex",
    "parse_obj",
    "pil_to_qimage",
    "read_cached",
    "sniff_audio",
    "sniff_image",
    "wav_peaks",
    "write_cached",
]
