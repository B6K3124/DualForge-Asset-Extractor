"""DualForge Ghidra key hunter - fully automated headless key extraction.

One command, zero questions::

    python scripts/ghidra/ghidra_key_finder.py "C:\\Game\\Game.exe"

The orchestrator:

1. Discovers a local Ghidra install (``GHIDRA_HOME``, common paths, PATH)
   and the ``ghidra_bridge`` pip package (auto-installs it if missing,
   unless ``--no-auto-install``).
2. Launches ``analyzeHeadless`` importing the target binary with
   ``ghidra_key_finder_server.py`` as a ``-postScript`` and ``-deleteProject``
   for guaranteed cleanup.
3. Connects the bridge, walks ``currentProgram.getMemory().getBlocks()``,
   streams each block in chunks, and scans for cryptographic signatures
   (AES S-box preset and/or custom hex) plus signature-free high-entropy
   16/32-byte key candidates.
4. Auto-adds the strongest 32-byte hex candidates to DualForge's key store
   (``~/.dualforge/keys.json``), where the pak key auto-probe picks them up.

Exit codes: 0 ok, 3 Ghidra not found, 4 Java missing, 6 setup/bridge
failure, 7 analysis failed or timed out.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable, Sequence
import contextlib

CHUNK_SIZE = 4 * 1024 * 1024
CONNECT_PORT = 4768  # ghidra_bridge package default server port
STARTUP_TIMEOUT = 300
RESPONSE_TIMEOUT = 120

EXIT_OK = 0
EXIT_NO_GHIDRA = 3
EXIT_NO_JAVA = 4
EXIT_SETUP = 6
EXIT_ANALYSIS = 7

AES_SBOX = bytes(
    [
        0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B, 0xFE, 0xD7, 0xAB, 0x76,
        0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0, 0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0,
        0xB7, 0xFD, 0x93, 0x26, 0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
        0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2, 0xEB, 0x27, 0xB2, 0x75,
        0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0, 0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84,
        0x53, 0xD1, 0x00, 0xED, 0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
        0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F, 0x50, 0x3C, 0x9F, 0xA8,
        0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5, 0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2,
        0xCD, 0x0C, 0x13, 0xEC, 0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
        0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14, 0xDE, 0x5E, 0x0B, 0xDB,
        0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C, 0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79,
        0xE7, 0xC8, 0x37, 0x6D, 0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
        0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F, 0x4B, 0xBD, 0x8B, 0x8A,
        0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E, 0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E,
        0xE1, 0xF8, 0x98, 0x11, 0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
        0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F, 0xB0, 0x54, 0xBB, 0x16,
    ]
)

AES_SBOX_INV = bytes(
    [
        0x52, 0x09, 0x6A, 0xD5, 0x30, 0x36, 0xA5, 0x38, 0xBF, 0x40, 0xA3, 0x9E, 0x81, 0xF3, 0xD7, 0xFB,
        0x7C, 0xE3, 0x39, 0x82, 0x9B, 0x2F, 0xFF, 0x87, 0x34, 0x8E, 0x43, 0x44, 0xC4, 0xDE, 0xE9, 0xCB,
        0x54, 0x7B, 0x94, 0x32, 0xA6, 0xC2, 0x23, 0x3D, 0xEE, 0x4C, 0x95, 0x0B, 0x42, 0xFA, 0xC3, 0x4E,
        0x08, 0x2E, 0xA1, 0x66, 0x28, 0xD9, 0x24, 0xB2, 0x76, 0x5B, 0xA2, 0x49, 0x6D, 0x8B, 0xD1, 0x25,
        0x72, 0xF8, 0xF6, 0x64, 0x86, 0x68, 0x98, 0x16, 0xD4, 0xA4, 0x5C, 0xCC, 0x5D, 0x65, 0xB6, 0x92,
        0x6C, 0x70, 0x48, 0x50, 0xFD, 0xED, 0xB9, 0xDA, 0x5E, 0x15, 0x46, 0x57, 0xA7, 0x8D, 0x9D, 0x84,
        0x90, 0xD8, 0xAB, 0x00, 0x8C, 0xBC, 0xD3, 0x0A, 0xF7, 0xE4, 0x58, 0x05, 0xB8, 0xB3, 0x45, 0x06,
        0xD0, 0x2C, 0x1E, 0x8F, 0xCA, 0x3F, 0x0F, 0x02, 0xC1, 0xAF, 0xBD, 0x03, 0x01, 0x13, 0x8A, 0x6B,
        0x3A, 0x91, 0x11, 0x41, 0x4F, 0x67, 0xDC, 0xEA, 0x97, 0xF2, 0xCF, 0xCE, 0xF0, 0xB4, 0xE6, 0x73,
        0x96, 0xAC, 0x74, 0x22, 0xE7, 0xAD, 0x35, 0x85, 0xE2, 0xF9, 0x37, 0xE8, 0x1C, 0x75, 0xDF, 0x6E,
        0x47, 0xF1, 0x1A, 0x71, 0x1D, 0x29, 0xC5, 0x89, 0x6F, 0xB7, 0x62, 0x0E, 0xAA, 0x18, 0xBE, 0x1B,
        0xFC, 0x56, 0x3E, 0x4B, 0xC6, 0xD2, 0x79, 0x20, 0x9A, 0xDB, 0xC0, 0xFE, 0x78, 0xCD, 0x5A, 0xF4,
        0x1F, 0xDD, 0xA8, 0x33, 0x88, 0x07, 0xC7, 0x31, 0xB1, 0x12, 0x10, 0x59, 0x27, 0x80, 0xEC, 0x5F,
        0x60, 0x51, 0x7F, 0xA9, 0x19, 0xB5, 0x4A, 0x0D, 0x2D, 0xE5, 0x7A, 0x9F, 0x93, 0xC9, 0x9C, 0xEF,
        0xA0, 0xE0, 0x3B, 0x4D, 0xAE, 0x2A, 0xF5, 0xB0, 0xC8, 0xEB, 0xBB, 0x3C, 0x83, 0x53, 0x99, 0x61,
        0x17, 0x2B, 0x04, 0x7E, 0xBA, 0x77, 0xD6, 0x26, 0xE1, 0x69, 0x14, 0x63, 0x55, 0x21, 0x0C, 0x7D,
    ]
)

PRESETS: dict[str, bytes] = {
    "aes_sbox": AES_SBOX,
    "aes_sbox_inv": AES_SBOX_INV,
}


# ---------------------------------------------------------------- pure logic


@dataclass
class MemoryBlock:
    name: str
    size: int
    initialized: bool = True
    base: int = 0


@dataclass
class Candidate:
    hex_value: str
    entropy: float
    length: int
    offset: int
    source: str = "entropy"


@dataclass
class Match:
    signature: str
    block: str
    offset: int
    address: str = ""
    candidates: list[Candidate] = field(default_factory=list)


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    total = len(data)
    entropy = 0.0
    for count in counts:
        if count:
            p = count / total
            entropy -= p * math.log2(p)
    return entropy


def is_hex_key(value: bytes, key_length: int) -> bool:
    """A plausible hardcoded key: printable hex string for ``key_length`` bytes."""
    if len(value) != key_length * 2:
        return False
    if not value.isalnum():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def collect_candidates(
    context: bytes,
    context_offset: int,
    min_length: int = 16,
    max_length: int = 32,
    threshold: float = 3.5,
    max_per_match: int = 20,
) -> list[Candidate]:
    """Pull high-entropy hex key-sized windows from a context buffer.

    ``context_offset`` is the context's offset within the containing block.
    Windows are sized as ``2 * key_length`` hex characters.
    """
    found: dict[tuple[int, int], Candidate] = {}
    for key_length in range(min_length, max_length + 1):
        window_size = key_length * 2
        for i in range(0, len(context) - window_size + 1):
            window = context[i : i + window_size]
            entropy = shannon_entropy(window)
            if entropy < threshold:
                continue
            if not is_hex_key(window, key_length):
                continue
            found[(key_length, i)] = Candidate(
                hex_value=window.decode("ascii").lower(),
                entropy=round(entropy, 4),
                length=key_length,
                offset=context_offset + i,
                source="entropy",
            )
    ranked = sorted(found.values(), key=lambda c: (-c.length, c.entropy, c.offset))
    return ranked[:max_per_match]


def scan_for_signatures(
    data: bytes,
    signatures: dict[str, bytes],
    base_offset: int = 0,
) -> list[tuple[str, int]]:
    hits: list[tuple[str, int]] = []
    for name, signature in signatures.items():
        start = 0
        while True:
            index = data.find(signature, start)
            if index < 0:
                break
            hits.append((name, base_offset + index))
            start = index + 1
    return hits


def scan_memory(
    blocks: Sequence[MemoryBlock],
    fetch: Callable[[str, int, int], bytes],
    signatures: dict[str, bytes],
    chunk_size: int,
    entropy_enabled: bool,
    context_size: int,
    min_length: int,
    max_length: int,
    threshold: float,
    max_per_match: int,
) -> list[Match]:
    """Walk every block in overlapped chunks and collect signature hits."""
    matches: list[Match] = []
    max_sig = max((len(s) for s in signatures.values()), default=0)
    overlap = max(max_sig, 2 * max_length) - 1
    for block in blocks:
        if block.size <= 0 or not block.initialized:
            continue
        cursor = 0
        while cursor < block.size:
            size = min(chunk_size, block.size - cursor)
            data = fetch(block.name, cursor, size)
            if not data:
                break
            for name, offset in scan_for_signatures(data, signatures, cursor):
                context_start = max(0, offset - context_size)
                context_end = min(block.size, offset + context_size + max(max_sig, 2 * max_length))
                context = fetch(block.name, context_start, context_end - context_start)
                candidates: list[Candidate] = []
                if entropy_enabled:
                    candidates = collect_candidates(
                        context,
                        context_start,
                        min_length=min_length,
                        max_length=max_length,
                        threshold=threshold,
                        max_per_match=max_per_match,
                    )
                matches.append(
                    Match(
                        signature=name,
                        block=block.name,
                        offset=offset,
                        address=f"{block.name}+0x{offset:x}",
                        candidates=candidates,
                    )
                )
            cursor += size - (overlap if cursor + size < block.size else 0)
    unique: dict[tuple[str, str, int], Match] = {}
    for match in matches:
        unique.setdefault((match.block, match.signature, match.offset), match)
    return list(unique.values())


def entropy_only_scan(
    blocks: Sequence[MemoryBlock],
    fetch: Callable[[str, int, int], bytes],
    chunk_size: int,
    min_length: int,
    max_length: int,
    threshold: float,
    max_per_match: int,
) -> list[Match]:
    """Signature-free pass: harvest high-entropy windows anywhere."""
    matches: list[Match] = []
    overlap = 2 * max_length - 1
    for block in blocks:
        if block.size <= 0 or not block.initialized:
            continue
        cursor = 0
        while cursor < block.size:
            size = min(chunk_size, block.size - cursor)
            data = fetch(block.name, cursor, size)
            if not data:
                break
            candidates = collect_candidates(
                data,
                cursor,
                min_length=min_length,
                max_length=max_length,
                threshold=threshold,
                max_per_match=max_per_match,
            )
            if candidates:
                matches.append(
                    Match(
                        signature="entropy",
                        block=block.name,
                        offset=cursor,
                        address=f"{block.name}+0x{cursor:x}",
                        candidates=candidates,
                    )
                )
            cursor += size - (overlap if cursor + size < block.size else 0)
    unique: dict[tuple[str, int], Match] = {}
    for match in matches:
        unique.setdefault((match.block, match.offset), match)
    return list(unique.values())


def hex_string_to_bytes(value: str) -> bytes:
    cleaned = value.strip().replace("0x", "").replace(" ", "").replace(",", "")
    if not cleaned or len(cleaned) % 2:
        raise ValueError(f"invalid hex signature: {value}")
    return bytes.fromhex(cleaned)


def parse_hex_key(value: str) -> str | None:
    cleaned = value.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) == 64:
        return cleaned
    return None


# ------------------------------------------------------------- environment


def _is_windows() -> bool:
    return os.name == "nt"


def find_analyze_headless() -> Path | None:
    candidates: list[Path] = []
    home = os.environ.get("GHIDRA_HOME")
    if home:
        root = Path(home)
        candidates.append(root / "support" / ("analyzeHeadless.bat" if _is_windows() else "analyzeHeadless"))
    search_roots = [
        Path.cwd(),
        Path.home(),
        Path.home() / "tools",
        Path.home() / "Desktop",
        Path("C:\\") if _is_windows() else Path("/opt"),
        Path("D:\\") if _is_windows() else Path("/usr/local"),
    ]
    for base in search_roots:
        if not base.exists():
            continue
        base_candidates = [base]
        ghidra_sub = base / "ghidra"
        if ghidra_sub.is_dir():
            base_candidates.append(ghidra_sub)
        for root in base_candidates:
            for match in root.glob("ghidra_*/support/analyzeHeadless*"):
                if match.is_file():
                    candidates.append(match)
    for name in ("analyzeHeadless", "analyzeHeadless.bat"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def find_java() -> str | None:
    if os.environ.get("JAVA_HOME"):
        candidate = Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if _is_windows() else "java")
        if candidate.is_file():
            return str(candidate)
    java = shutil.which("java")
    if java:
        return java
    return None


def _java_major(path: str) -> int | None:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=30, creationflags=flags
        )
    except Exception:
        return None
    output = (completed.stderr or completed.stdout)
    import re

    match = re.search(r'version "(\d+)(?:\.(\d+))?', output)
    if not match:
        return None
    major = int(match.group(1))
    if major == 1:  # Java 8 style "1.8.0"
        return int(match.group(2) or 0)
    return major


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def find_ghidra_scripts_dir() -> Path | None:
    """Locate the pip package's bridge server script directory.

    ghidra-bridge <1.1 ships it under ``ghidra_scripts/``; 1.0.0 moved it
    under ``server/``.
    """
    try:
        import ghidra_bridge
    except ImportError:
        return None
    package_dir = Path(ghidra_bridge.__file__).resolve().parent
    for sub in ("ghidra_scripts", "server"):
        candidate = package_dir / sub
        if (candidate / "ghidra_bridge_server.py").is_file():
            return candidate
    return None


def prepare_server_scripts(work_dir: Path) -> str | None:
    """Stage the bridge server + jfx_bridge for Jython in ``work_dir``.

    ghidra-bridge 1.0.0's ``ghidra_bridge_server.py`` imports
    ``jfx_bridge``, which must be reachable from Ghidra's Jython script
    path. The package ships an installer for this; we do the same thing
    into the per-run temp dir (already on ``-scriptPath``), so nothing
    permanent is installed on the user's Ghidra.

    Returns an error message on failure, else None.
    """
    try:
        import jfx_bridge
        import ghidra_bridge  # noqa: F401 - import availability probe
    except ImportError:
        return "ghidra_bridge/jfx_bridge not installed (pip install ghidra-bridge)"
    jfx_dir = Path(jfx_bridge.__file__).resolve().parent
    if not (jfx_dir / "bridge.py").is_file():
        return f"jfx_bridge is incomplete at {jfx_dir}"
    target = work_dir / "jfx_bridge"
    target.mkdir(parents=True, exist_ok=True)
    for source in jfx_dir.glob("*.py"):
        if source.name.startswith("test_"):
            continue
        shutil.copy2(source, target / source.name)

    server_dir = find_ghidra_scripts_dir()
    if server_dir is None:
        return "ghidra_bridge server scripts not found in the package"
    for source in server_dir.glob("ghidra_bridge_*.py"):
        shutil.copy2(source, work_dir / source.name)
    return None


def ensure_ghidra_bridge(allow_install: bool, log: Callable[[str], None]) -> None:
    try:
        import ghidra_bridge  # noqa: F401
    except ImportError:
        if not allow_install:
            raise RuntimeError(
                "ghidra_bridge is not installed. Install it with "
                "'pip install ghidra-bridge' or re-run with auto-install enabled."
            ) from None
        log("ghidra_bridge not found - installing (pip install ghidra-bridge)...")
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        completed = subprocess.run(
            [sys.executable, "-m", "pip", "install", "ghidra-bridge>=1.0"],
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=flags,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"auto-install of ghidra_bridge failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            ) from None
        log("ghidra_bridge installed.")


# -------------------------------------------------------------- bridge host


class GhidraSession:
    """Owns the analyzeHeadless subprocess and the bridge connection."""

    def __init__(self, analyze_headless: Path, binary: str, log: Callable[[str], None]):
        self.analyze_headless = analyze_headless
        self.binary = binary
        self.log = log
        self.port = _free_port()
        self.work_dir = Path(tempfile.mkdtemp(prefix="dualforge_ghidra_"))
        self.project_dir = self.work_dir / "project"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.stop_file = self.work_dir / "stop.flag"
        self.process: subprocess.Popen | None = None
        self.bridge = None
        self._log_stack = contextlib.ExitStack()

    def launch(self, ghidra_scripts: Path | None = None) -> None:
        server_script = self.work_dir / "ghidra_key_finder_server.py"
        shutil.copy2(Path(__file__).with_name("ghidra_key_finder_server.py"), server_script)
        env = dict(os.environ)
        env["GHIDRA_BRIDGE_PORT"] = str(self.port)
        env["DF_STOP_FILE"] = str(self.stop_file)
        command = [
            str(self.analyze_headless),
            str(self.project_dir),
            "dfproj",
            "-import",
            str(self.binary),
            "-scriptPath",
            str(self.work_dir),
        ]
        if ghidra_scripts is not None:
            command += ["-scriptPath", str(ghidra_scripts)]
        command += ["-postScript", "ghidra_key_finder_server.py", "-deleteProject"]
        self.log(f"launching analyzeHeadless (project under {self.project_dir})")
        # Handles stay open for the child process lifetime; owned by _log_stack.
        out = self._log_stack.enter_context(open(self.work_dir / "analyzeheadless.out.txt", "w", encoding="utf-8", errors="replace"))  # noqa: SIM115
        err = self._log_stack.enter_context(open(self.work_dir / "analyzeheadless.err.txt", "w", encoding="utf-8", errors="replace"))  # noqa: SIM115
        self.log_files = (out, err)
        self.process = subprocess.Popen(
            command,
            env=env,
            stdout=out,
            stderr=err,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def connect(self, timeout: int) -> None:
        import ghidra_bridge

        deadline = time.time() + timeout
        attempts = [self.port]
        if self.port != CONNECT_PORT:
            attempts.append(CONNECT_PORT)
        last_error: Exception | None = None
        while time.time() < deadline:
            if self.process and self.process.poll() is not None:
                _, tail = self._collect_tail()
                raise RuntimeError(
                    "analyzeHeadless exited before the bridge connected "
                    f"(exit {self.process.returncode}):\n{tail}"
                )
            for port in attempts:
                if not self._port_accepting(port):
                    continue
                try:
                    bridge = ghidra_bridge.GhidraBridge(
                        connect_to_host="127.0.0.1",
                        connect_to_port=port,
                        response_timeout=RESPONSE_TIMEOUT,
                    )
                    # BridgeClient connects lazily - prove the RPC layer works.
                    bridge.remote_eval("1 + 1")
                    self.bridge = bridge
                    self.log(f"bridge connected on port {port}.")
                    return
                except Exception as exc:  # noqa: BLE001 - keep retrying
                    last_error = exc
                    time.sleep(2.0)
            time.sleep(2.0)
        raise TimeoutError(
            f"timed out waiting for the Ghidra bridge ({timeout}s); last error: {last_error}"
        )

    @staticmethod
    def _port_accepting(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                return True
        except OSError:
            return False

    def _collect_tail(self) -> tuple[str, str]:
        if not self.log_files:
            return "", ""
        out_path = self.work_dir / "analyzeheadless.out.txt"
        err_path = self.work_dir / "analyzeheadless.err.txt"
        stdout = ""
        stderr = ""
        for path, is_err in ((out_path, False), (err_path, True)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if is_err:
                stderr = text
            else:
                stdout = text
        tail_lines = stdout.splitlines()[-12:] + stderr.splitlines()[-12:]
        return stdout, "\n".join(tail_lines)

    def close_logs(self) -> None:
        if self.log_files:
            self._log_stack.close()
            self.log_files = None

    def shutdown(self) -> None:
        with contextlib.suppress(OSError):
            self.stop_file.touch(exist_ok=True)
        try:
            if self.bridge is not None:
                self.bridge.remote_shutdown()
        except Exception:
            pass
        if self.process is not None:
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.log("analyzeHeadless did not exit cleanly; terminating...")
                self.process.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=15)
        self.close_logs()
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None


# ---------------------------------------------------------------- analysis


def remote_fetch_function() -> str:
    """Jython source for a chunk-fetch helper, defined once in the remote
    ``__main__`` namespace via ``remote_exec``.

    Ghidra's ``MemoryBlock.getBytes(Address, byte[])`` fills the buffer
    in-place (Java semantics), so a helper is needed to return the payload.
    The buffer must be a real Java ``byte[]`` (``jarray.zeros``): a Jython
    ``bytearray`` is copied when passed to Java, so it never sees the filled
    data and the fetch returns zeros. Subsequent chunk fetches are a single
    ``remote_eval`` call of ``__dualforge_fetch__(name, base, offset, size)``
    returning ascii base64.
    """
    return (
        "def __dualforge_fetch__(block_name, base, offset, size):\n"
        "    import base64\n"
        "    from jarray import zeros\n"
        "    buf = zeros(size, 'b')\n"
        "    currentProgram.getMemory().getBlock(block_name).getBytes(\n"
        "        currentProgram.getAddressFactory().getDefaultAddressSpace()\n"
        "        .getAddress(base + offset), buf)\n"
        "    return base64.b64encode(''.join(chr(x & 0xFF) for x in buf)).decode('ascii')"
    )


def run_analysis(
    session: GhidraSession,
    signatures: dict[str, bytes],
    entropy_enabled: bool,
    chunk_size: int,
    context_size: int,
    min_length: int,
    max_length: int,
    threshold: float,
    max_per_match: int,
    log: Callable[[str], None],
) -> tuple[list[Match], list[MemoryBlock], int]:
    bridge = session.bridge
    if bridge is None:
        raise RuntimeError("bridge not connected")
    try:
        blocks_raw = bridge.remote_eval(
            "[(blk.getName(), blk.getSize(), blk.isInitialized(), blk.getStart().getOffset()) "
            "for blk in currentProgram.getMemory().getBlocks()]"
        )
    except Exception:
        # Ghidra <11: getBlocks(forward) boolean variant.
        blocks_raw = bridge.remote_eval(
            "[(blk.getName(), blk.getSize(), blk.isInitialized(), blk.getStart().getOffset()) "
            "for blk in currentProgram.getMemory().getBlocks(True)]"
        )
    blocks = [
        MemoryBlock(name=str(name), size=int(size), initialized=bool(initialized), base=int(base))
        for name, size, initialized, base in blocks_raw
    ]
    log(f"memory: {len(blocks)} blocks, {sum(b.size for b in blocks):,} bytes total")
    bridge.remote_exec(remote_fetch_function())

    def fetch(block_name: str, offset: int, size: int) -> bytes:
        if size <= 0:
            return b""
        block = next((b for b in blocks if b.name == block_name), None)
        if block is None:
            return b""
        try:
            raw = bridge.remote_eval(
                f"__dualforge_fetch__({block_name!r}, {block.base!r}, {int(offset)!r}, {int(size)!r})"
            )
        except Exception as exc:  # noqa: BLE001
            log(f"chunk fetch failed ({block_name}@{offset:#x}): {exc}")
            return b""
        try:
            return base64.b64decode(raw)
        except Exception:
            return b""

    if entropy_enabled and not signatures:
        matches = entropy_only_scan(
            blocks,
            fetch,
            chunk_size,
            min_length,
            max_length,
            threshold,
            max_per_match,
        )
    else:
        matches = scan_memory(
            blocks,
            fetch,
            signatures,
            chunk_size,
            entropy_enabled,
            context_size,
            min_length,
            max_length,
            threshold,
            max_per_match,
        )
    total_scanned = sum(block.size for block in blocks)
    return matches, blocks, total_scanned


# ------------------------------------------------------------ key delivery


def add_to_keystore(binary_path: str, matches: list[Match], count: int) -> list[str]:
    """Write the strongest 32-byte hex candidates into DualForge's key store."""
    from dualforge.unreal.keys import KeyStore

    candidates: dict[str, float] = {}
    for match in matches:
        for candidate in match.candidates:
            if candidate.length != 32:
                continue
            key = parse_hex_key(candidate.hex_value)
            if key:
                candidates[key] = max(candidates.get(key, 0.0), candidate.entropy)
    ranked = sorted(candidates.items(), key=lambda item: item[1], reverse=True)[:count]
    if not ranked:
        return []
    stem = Path(binary_path).stem
    store = KeyStore()
    added: list[str] = []
    for index, (key, _entropy) in enumerate(ranked, start=1):
        title = f"{stem} [ghidra-{index}]"
        try:
            store.add(title, key, engine="unreal", notes="ghidra key hunt")
            added.append(title)
        except ValueError:
            continue
    return added


# ------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghidra_key_finder",
        description=(
            "Automated headless Ghidra key hunt: finds hardcoded AES keys and "
            "crypto constants in a binary and feeds them into DualForge's key "
            "store. Requires a local Ghidra install (11.x) with Java 21."
        ),
    )
    parser.add_argument("binary", nargs="?", help="the target executable / DLL to analyze")
    parser.add_argument("--check", action="store_true", help="diagnose the Ghidra + bridge setup and exit")
    parser.add_argument("--ghidra-home", help="path to the Ghidra installation root (overrides discovery)")
    parser.add_argument(
        "--preset",
        action="append",
        choices=sorted(PRESETS),
        help="crypto-constant signature preset (repeatable); default: aes_sbox",
    )
    parser.add_argument("--signature", action="append", help="custom hex byte signature, e.g. DEADBEEF (repeatable)")
    parser.add_argument("--no-signatures", action="store_true", help="skip signature scanning (entropy pass only)")
    parser.add_argument("--entropy-threshold", type=float, default=3.5, help="minimum Shannon entropy per byte (default 3.5)")
    parser.add_argument(
        "--min-length",
        type=int,
        default=32,
        help="smallest candidate key length in bytes; default 32 (16-byte keys would break pak probing)",
    )
    parser.add_argument(
        "--max-length",
        type=int,
        default=32,
        help="largest candidate key length in bytes (default 32)",
    )
    parser.add_argument("--context", type=int, default=256, help="context window around signature hits (default 256)")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE, help="memory chunk size in bytes (default 4 MiB)")
    parser.add_argument("--max-per-match", type=int, default=20, help="candidate cap per match (default 20)")
    parser.add_argument("--startup-timeout", type=int, default=STARTUP_TIMEOUT, help="bridge connect timeout (default 300s)")
    parser.add_argument("--json", help="write the result JSON to this path (default <binary>.keys.json)")
    parser.add_argument("--no-add-keystore", action="store_true", help="do not write candidates into DualForge's key store")
    parser.add_argument("--keystore-count", type=int, default=5, help="how many top candidates to store (default 5)")
    parser.add_argument("--no-auto-install", action="store_true", help="refuse to pip-install ghidra_bridge automatically")
    parser.add_argument("--quiet", action="store_true", help="print only the result JSON")
    return parser


def _log(args: argparse.Namespace, message: str) -> None:
    if not args.quiet:
        print(f"[ghidra-key-hunt] {message}", file=sys.stderr)


def cmd_check(args: argparse.Namespace) -> int:
    def report(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'OK ' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))

    headless = find_analyze_headless()
    report("Ghidra (analyzeHeadless)", headless is not None, str(headless) if headless else "")
    java = find_java()
    report("Java (JAVA_HOME preferred, then PATH)", java is not None, java or "")
    if java:
        major = _java_major(java)
        detail = f"Java {major}" if major else java
        report("Java version (Ghidra 11.x needs 21+)", major is not None and major >= 21, detail)
    try:
        import ghidra_bridge  # noqa: F401

        report("ghidra_bridge", True, str(Path(ghidra_bridge.__file__).resolve().parent))
    except ImportError:
        report("ghidra_bridge", False, "pip install ghidra-bridge")
    try:
        import jfx_bridge  # noqa: F401

        report("jfx_bridge (Jython-side dep)", True, str(Path(jfx_bridge.__file__).resolve().parent))
    except ImportError:
        report("jfx_bridge (Jython-side dep)", False, "pip install ghidra-bridge (pulls jfx-bridge)")
    with tempfile.TemporaryDirectory(prefix="dualforge_ghidra_check_") as check_dir:
        staging_error = prepare_server_scripts(Path(check_dir))
        report("bridge server staging", staging_error is None, staging_error or "copied into per-run temp dir")
    if headless is None:
        print("\nGhidra was not found. Download Ghidra 11.x from")
        print("  https://github.com/NationalSecurityAgency/ghidra/releases")
        print("extract it, then either add it to PATH, set GHIDRA_HOME, or use --ghidra-home.")
        return EXIT_NO_GHIDRA
    if java is None:
        print("\nJava was not found. Ghidra 11.x needs Java 21 (JDK):")
        print("  https://adoptium.net/temurin/releases/?version=21")
        return EXIT_NO_JAVA
    return EXIT_OK


def cmd_hunt(args: argparse.Namespace) -> int:
    if not args.binary:
        print("error: a binary path is required (or use --check)", file=sys.stderr)
        return 2
    binary = Path(args.binary)
    if not binary.is_file():
        print(f"error: binary not found: {binary}", file=sys.stderr)
        return 2

    signatures: dict[str, bytes] = {}
    presets = args.preset or ["aes_sbox"]
    for preset in presets:
        signatures[f"preset:{preset}"] = PRESETS[preset]
    if args.signature:
        for index, value in enumerate(args.signature, start=1):
            try:
                signatures[f"custom:{index}"] = hex_string_to_bytes(value)
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    if args.no_signatures:
        signatures = {}

    if args.ghidra_home:
        root = Path(args.ghidra_home)
        headless = root / "support" / ("analyzeHeadless.bat" if _is_windows() else "analyzeHeadless")
        if not headless.is_file():
            print(f"error: no analyzeHeadless under {root}", file=sys.stderr)
            return EXIT_NO_GHIDRA
    else:
        headless = find_analyze_headless()
        if headless is None:
            print(
                "error: Ghidra was not found. Download it from "
                "https://github.com/NationalSecurityAgency/ghidra/releases, then "
                "set GHIDRA_HOME, add it to PATH, or pass --ghidra-home.",
                file=sys.stderr,
            )
            return EXIT_NO_GHIDRA

    started = time.time()
    log = lambda message: _log(args, message)  # noqa: E731

    try:
        ensure_ghidra_bridge(not args.no_auto_install, log)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SETUP
    try:
        import jfx_bridge  # noqa: F401
    except ImportError:
        print(
            "error: jfx_bridge is missing (ghidra-bridge's Jython-side dependency); "
            "reinstall it with 'pip install --force-reinstall ghidra-bridge'",
            file=sys.stderr,
        )
        return EXIT_SETUP

    entropy_enabled = True
    max_length = max(args.min_length, args.max_length)
    min_length = min(args.min_length, args.max_length)

    session = GhidraSession(headless, str(binary), log)
    try:
        staging_error = prepare_server_scripts(session.work_dir)
        if staging_error:
            print(f"error: could not stage the bridge server: {staging_error}", file=sys.stderr)
            session.shutdown()
            return EXIT_SETUP
        session.launch()
        session.connect(args.startup_timeout)
        matches, blocks, total_scanned = run_analysis(
            session,
            signatures,
            entropy_enabled,
            max(1024, args.chunk_size),
            max(0, args.context),
            min_length,
            max_length,
            args.entropy_threshold,
            args.max_per_match,
            log,
        )
    except (TimeoutError, RuntimeError) as exc:
        print(f"error: analysis failed: {exc}", file=sys.stderr)
        session.shutdown()
        return EXIT_ANALYSIS
    except Exception as exc:  # noqa: BLE001
        print(f"error: unexpected failure: {exc}", file=sys.stderr)
        session.shutdown()
        return EXIT_SETUP
    finally:
        session.shutdown()

    duration = round(time.time() - started, 2)
    added_titles: list[str] = []
    if not args.no_add_keystore:
        try:
            added_titles = add_to_keystore(str(binary), matches, args.keystore_count)
            if added_titles:
                log(f"added {len(added_titles)} candidate key(s) to the DualForge key store")
        except Exception as exc:  # noqa: BLE001
            log(f"could not write the key store: {exc}")

    result = {
        "status": "ok",
        "binary": str(binary),
        "ghidra": str(headless),
        "duration_s": duration,
        "blocks": [block.name for block in blocks],
        "bytes_scanned": total_scanned,
        "matches": [
            {
                "signature": match.signature,
                "block": match.block,
                "offset": match.offset,
                "address": match.address,
                "candidates": [
                    {
                        "hex": candidate.hex_value,
                        "entropy": candidate.entropy,
                        "length": candidate.length,
                        "offset": candidate.offset,
                        "source": candidate.source,
                    }
                    for candidate in match.candidates
                ],
            }
            for match in matches
        ],
        "keystore_added": added_titles,
    }
    json_path = Path(args.json or f"{binary.name}.keys.json")
    try:
        json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as exc:
        log(f"could not write result JSON: {exc}")
    if not args.quiet:
        total_candidates = sum(len(m.candidates) for m in matches)
        print(
            f"\n{len(matches)} match(es), {total_candidates} candidate key(s) in "
            f"{duration}s. Results: {json_path}"
        )
        if added_titles:
            print(f"Added to key store: {', '.join(added_titles)}")
    else:
        print(json.dumps(result, indent=2))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check:
        return cmd_check(args)
    return cmd_hunt(args)


if __name__ == "__main__":
    sys.exit(main())