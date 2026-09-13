from __future__ import annotations

import ctypes
import glob
import os


class OodleUnavailableError(Exception):
    pass


class OodleDecompressError(Exception):
    pass


class Oodle:
    _SIGNATURES = (
        [
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32,
        ],
        [
            ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
            ctypes.c_void_p, ctypes.c_int64,
            ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int64, ctypes.c_int32,
        ],
    )

    def __init__(self, dll_path: str | None = None, search_paths: list[str] | None = None):
        self.path: str | None = None
        self._libs: list[ctypes.CDLL] = []
        self._load(dll_path, search_paths)

    def _candidate_dirs(self, search_paths: list[str] | None) -> list[str]:
        dirs = []
        if search_paths:
            dirs += list(search_paths)
        dirs += [
            os.path.dirname(os.path.abspath(__file__)),
            os.getcwd(),
            os.path.expanduser("~/.dualforge"),
        ]
        dirs += [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        return dirs

    def _candidates(self, dirs: list[str]) -> list[str]:
        patterns = ("oo2core_*_win64.dll", "oo2core_*_linux64.so", "oo2core_*_mac64.dylib")
        seen = set()
        out = []
        for d in dirs:
            for pattern in patterns:
                for path in glob.glob(os.path.join(d, pattern)):
                    key = os.path.abspath(path)
                    if key not in seen:
                        seen.add(key)
                        out.append(key)
        return out

    def _load(self, dll_path: str | None, search_paths: list[str] | None) -> None:
        candidates = [dll_path] if dll_path else []
        candidates += self._candidates(self._candidate_dirs(search_paths))
        for path in candidates:
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            if not hasattr(lib, "OodleLZ_Decompress"):
                continue
            self.path = path
            self._libs.append(lib)
            return
        raise OodleUnavailableError(
            "Oodle library (oo2core_*.dll) not found. Point it at your game's "
            "binary directory or download the official RAD Oodle SDK."
        )

    def decompress(self, data: bytes, output_size: int) -> bytes:
        if not data:
            return b""
        raw_out = ctypes.create_string_buffer(output_size)
        comp_buf = ctypes.c_char_p(data)
        for lib, sig in zip(self._libs, self._SIGNATURES, strict=True):
            try:
                func = lib.OodleLZ_Decompress
                func.restype = ctypes.c_int64
                func.argtypes = sig
            except AttributeError:
                continue
            try:
                result = func(
                    comp_buf, len(data),
                    ctypes.cast(raw_out, ctypes.c_void_p), output_size,
                    0, 0, 0,
                    None, 0,
                    None, None,
                    None, 0, 0,
                )
            except (ctypes.ArgumentError, OSError):
                continue
            if result and result > 0:
                return raw_out.raw[: result]
        raise OodleDecompressError(
            f"OodleLZ_Decompress failed for {self.path or 'unknown dll'} "
            f"(output_size={output_size}). The DLL may be incompatible."
        )
