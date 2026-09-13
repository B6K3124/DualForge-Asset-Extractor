from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class VgmstreamError(Exception):
    pass


class Vgmstream:
    def __init__(self, exe_path: str | None = None, search_paths: list[str] | None = None):
        self.exe_path = exe_path or _find_exe(search_paths)

    def available(self) -> bool:
        return self.exe_path is not None

    def convert(self, input_path: str, output_path: str, fmt: str = "wav") -> str:
        if not self.available():
            raise VgmstreamError(
                "vgmstream not found. Download it from the vgmstream releases page "
                "and set DUALFORGE_VGMSTREAM or add it to PATH."
            )
        fmt = fmt.lower()
        if fmt not in {"wav", "ogg", "flac", "m4a"}:
            raise VgmstreamError(f"unsupported output format: {fmt}")
        out = Path(output_path)
        if out.suffix.lower() != f".{fmt}":
            out = out.with_suffix(f".{fmt}")
        out.parent.mkdir(parents=True, exist_ok=True)
        args = [self.exe_path, "-o", str(out), "-l", "0.0", str(input_path)]
        try:
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                args, capture_output=True, text=True, timeout=600, creationflags=flags
            )
        except FileNotFoundError as exc:
            raise VgmstreamError(f"could not run vgmstream: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise VgmstreamError("vgmstream timed out") from exc
        if completed.returncode != 0:
            raise VgmstreamError(
                f"vgmstream failed (exit {completed.returncode}): "
                f"{(completed.stderr or '').strip()[:500]}"
            )
        return str(out)


def _find_exe(search_paths: list[str] | None) -> str | None:
    env = os.environ.get("DUALFORGE_VGMSTREAM")
    if env and Path(env).is_file():
        return env
    for name in ("vgmstream-cli", "vgmstream-cli.exe", "vgmstream", "vgmstream.exe"):
        found = shutil.which(name)
        if found:
            return found
    dirs = list(search_paths or []) + [str(Path.home() / ".dualforge")]
    for d in dirs:
        for pattern in ("vgmstream-cli.exe", "vgmstream-cli", "vgmstream.exe", "vgmstream"):
            for candidate in Path(d).glob(pattern):
                if candidate.is_file():
                    return str(candidate)
    return None


__all__ = ["Vgmstream", "VgmstreamError"]
