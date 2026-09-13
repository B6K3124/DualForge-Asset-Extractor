from __future__ import annotations

from pathlib import Path


class ExportError(Exception):
    pass


class Exporter:
    def __init__(self, out_dir: str, overwrite: bool = True):
        self.out_dir = Path(out_dir)
        self.overwrite = overwrite
        self.written: int = 0

    def write(self, rel_path: str, data: bytes) -> str:
        target = self._resolve(rel_path)
        if target.exists() and not self.overwrite:
            target = self._unique(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
        self.written += 1
        return str(target)

    def _resolve(self, rel_path: str) -> Path:
        safe = _sanitize(rel_path)
        return self.out_dir / safe

    def _unique(self, target: Path) -> Path:
        index = 1
        while True:
            candidate = target.with_name(f"{target.stem}_{index}{target.suffix}")
            if not candidate.exists():
                return candidate
            index += 1


def _sanitize(name: str) -> str:
    parts = [part for part in name.replace("\\", "/").split("/") if part]
    if not parts:
        raise ExportError("empty output path")
    clean = []
    for part in parts:
        cleaned = "".join(ch if ch not in '<>:"|?*' else "_" for ch in part).strip()
        if cleaned in {".", ".."}:
            continue
        clean.append(cleaned or "_")
    if not clean:
        raise ExportError(f"unsafe output path: {name!r}")
    return "/".join(clean)


def write_entry(out_dir: str, rel_path: str, data: bytes, overwrite: bool = True) -> str:
    """Write one extracted entry into ``out_dir`` and return the written path.

    Thin wrapper over :class:`Exporter` so archive types (Bethesda, CDPR,
    Unreal pak) expose a single-entry extract without rebuilding the writer.
    """
    return Exporter(out_dir, overwrite=overwrite).write(rel_path, data)


__all__ = ["Exporter", "ExportError", "write_entry"]
