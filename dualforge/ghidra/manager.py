"""Ghidra + JRE auto-provisioning for the static AES key hunt.

DualForge's static key hunt drives Ghidra's headless analyzer
(``analyzeHeadless``) through ``ghidra_bridge``. To make this work with zero
manual setup, this module locates an already-installed Ghidra/JRE (env vars,
common paths, PATH, the managed cache) and, when allowed, downloads and
unpacks a portable Ghidra release (and, optionally, a JRE) into
``~/.dualforge/ghidra/``.

Nothing here imports Ghidra itself - it only manages the files on disk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from collections.abc import Callable

from dualforge.log import get_logger

logger = get_logger(__name__)

# Version we prefer to download when the user does not supply one.
DEFAULT_GHIDRA_RELEASE = "Ghidra_11.3.2_build"

# Where we keep the downloaded/unpacked toolchain.
CACHE_ROOT = Path.home() / ".dualforge" / "ghidra"

# GitHub release asset names come in the form:
#   ghidra_<X.Y.Z>_PUBLIC_<YYYYMMDD>.zip
# We query the API and pick the latest PUBLIC zip, but allow a pin.
GHIDRA_API = "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest"

# Portable JRE source (Temurin 21, the version Ghidra 11.x requires).
# Server-side JSON used only for the on-demand auto-download path.
JRE_DOWNLOAD_ROOT = "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jre/hotspot/normal/eclipse"


class GhidraError(RuntimeError):
    pass


def _log(message: str) -> None:
    print(message, file=sys.stderr)


# ------------------------------------------------------------------ discovery


def find_analyze_headless() -> Path | None:
    """Locate an existing ``analyzeHeadless`` (env, cache, common roots, PATH).

    Mirrors (and extends) ``scripts/ghidra/ghidra_key_finder.find_analyze_headless``
    so the hunt works from library code too.
    """
    home = os.environ.get("GHIDRA_HOME")
    if home:
        root = Path(home)
        bat = root / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
        if bat.is_file():
            return bat

    # Managed cache: any unpacked Ghidra under CACHE_ROOT.
    if CACHE_ROOT.is_dir():
        for match in CACHE_ROOT.glob("ghidra_*/support/analyzeHeadless*"):
            if match.is_file():
                return match

    search_roots = [Path.cwd(), Path.home(), Path.home() / "tools", Path.home() / "Desktop"]
    if os.name == "nt":
        for drive in ("C:\\", "D:\\", "E:\\", "F:\\"):
            search_roots.append(Path(drive))
    else:
        search_roots += [Path("/opt"), Path("/usr/local")]

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
                    return match

    for name in ("analyzeHeadless", "analyzeHeadless.bat"):
        found = shutil.which(name)
        if found:
            p = Path(found)
            if p.is_file():
                return p
    return None


def find_java() -> str | None:
    """Locate a usable JRE/JDK (21 preferred; any Java accepted by caller)."""
    if os.environ.get("JAVA_HOME"):
        candidate = Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return str(candidate)
    # portable JRE shipped into the managed cache
    if CACHE_ROOT.is_dir():
        for match in CACHE_ROOT.glob("*/bin/java*"):
            if match.is_file():
                return str(match)
    java = shutil.which("java")
    if java:
        return java
    return None


def _java_major(path: str) -> int | None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=30, creationflags=flags
        )
    except Exception:
        logger.debug("could not probe java %s", path, exc_info=True)
        return None
    import re

    match = re.search(r'version "(\d+)(?:\.(\d+))?', completed.stderr or completed.stdout or "")
    if not match:
        return None
    major = int(match.group(1))
    if major == 1:
        return int(match.group(2) or 0)
    return major


# --------------------------------------------------------------- provisioning


def _latest_ghidra_asset(log: Callable[[str], None]) -> tuple[str, str, str]:
    """Return (zip_name, zip_url, sha256_hex) for the latest stable Ghidra PUBLIC build.

    ``sha256_hex`` is "" when GitHub provides no digest for the asset.
    """
    import json as _json

    req = urllib.request.Request(GHIDRA_API, headers={"Accept": "application/vnd.github+json", "User-Agent": "DualForge"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = _json.load(resp)

    def _sha256_of(asset: dict) -> str:
        digest = asset.get("digest") or ""
        if isinstance(digest, str) and digest.startswith("sha256:"):
            return digest[len("sha256:"):].strip()
        sibling = f"{asset.get('name', '')}.sha256"
        for candidate in payload.get("assets", []):
            if candidate.get("name") == sibling:
                text = _fetch_sha256_file(candidate.get("browser_download_url", ""))
                hex_part = text.split()[0] if text else ""
                return hex_part if len(hex_part) == 64 else ""
        return ""

    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.startswith("ghidra_") and "PUBLIC" in name and name.endswith(".zip"):
            return name, asset.get("browser_download_url", ""), _sha256_of(asset)
    raise GhidraError(f"no PUBLIC zip found in latest Ghidra release ({payload.get('tag_name')})")


def _fetch_sha256_file(url: str) -> str:
    """Download a ``foo.zip.sha256`` companion file and return its text."""
    if not url:
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "DualForge"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace").strip()


def _sha256_hex(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_sha256(path: Path, expected: str, label: str, log: Callable[[str], None]) -> None:
    """Confirm a downloaded file matches its expected SHA-256 or self-heal."""
    log(f"verifying {label} (SHA-256)")
    actual = _sha256_hex(path)
    if actual.lower() == expected.lower():
        log(f"  {label}: hash OK")
        return
    path.unlink(missing_ok=True)
    raise GhidraError(
        f"SHA-256 mismatch for {label}: expected {expected}, downloaded {actual}. "
        "Removed the stale download; re-run to fetch it again."
    )


def _download(url: str, dest: Path, log: Callable[[str], None]) -> None:
    log(f"downloading {url.split('/')[-1]}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "DualForge"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        written = 0
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)
            if total:
                percent = written * 100 // total
                log(f"  {written // (1024 * 1024)}/{total // (1024 * 1024)} MB ({percent}%)")
    tmp.replace(dest)


def ensure_ghidra(
    download: bool = True,
    ghidra_home: str | None = None,
    log: Callable[[str], None] = _log,
) -> Path:
    """Return a path to ``analyzeHeadless``, downloading a portable build if needed.

    Raises ``GhidraError`` if no local install exists and downloading is disabled.
    """
    if ghidra_home:
        root = Path(ghidra_home)
        bat = root / "support" / ("analyzeHeadless.bat" if os.name == "nt" else "analyzeHeadless")
        if bat.is_file():
            return bat
        raise GhidraError(f"no analyzeHeadless under {root}")

    existing = find_analyze_headless()
    if existing is not None:
        return existing

    if not download:
        raise GhidraError(
            "Ghidra is not installed and auto-download is disabled. Install Ghidra 11.x "
            "(https://github.com/NationalSecurityAgency/ghidra/releases), set GHIDRA_HOME, "
            "or pass --download."
        )

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    name, url, sha256 = _latest_ghidra_asset(log)
    zip_dest = CACHE_ROOT / name
    if not zip_dest.is_file():
        _download(url, zip_dest, log)
    if sha256:
        _verify_sha256(zip_dest, sha256, name, log)

    def _find_unpacked_headless() -> Path | None:
        # The zip's top-level dir may differ from the zip stem (GitHub Ghidra
        # zips extract to a non-dated dir). Locate analyzeHeadless by scanning
        # the cache rather than guessing the directory name.
        for match in CACHE_ROOT.glob("*/support/analyzeHeadless*"):
            if match.is_file():
                return match
        return None

    headless = _find_unpacked_headless()
    if headless is None:
        log(f"unpacking {name}")
        with zipfile.ZipFile(zip_dest) as zf:
            zf.extractall(CACHE_ROOT)
        headless = _find_unpacked_headless()
    if headless is None:
        raise GhidraError(f"downloaded Ghidra is missing analyzeHeadless (from {name})")
    log(f"Ghidra ready: {headless.parents[1]}")
    return headless


def ensure_java(
    download: bool = True,
    java_home: str | None = None,
    log: Callable[[str], None] = _log,
) -> str | None:
    """Return a usable ``java`` path, provisioning a portable JRE if allowed.

    Returns None only if no Java is found and downloading is disabled.
    """
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return str(candidate)
        raise GhidraError(f"no java under {java_home}")

    existing = find_java()
    if existing is not None:
        return existing

    if not download:
        return None

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    jre_dir = CACHE_ROOT / "jre21"
    java = jre_dir / "bin" / ("java.exe" if os.name == "nt" else "java")
    if java.is_file():
        return str(java)
    from urllib.request import Request, urlopen

    req = Request(JRE_DOWNLOAD_ROOT, headers={"User-Agent": "DualForge"})
    # adoptium redirects to an actual zip; download and unpack
    import io

    with urlopen(req, timeout=120) as resp:
        raw = resp.read()
    log(f"unpacking portable JRE ({len(raw) // (1024 * 1024)} MB)")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        zf.extractall(CACHE_ROOT)
    # extracted into a toc-rooted subdir; find java under CACHE_ROOT
    for match in CACHE_ROOT.glob("*/bin/java*"):
        if match.is_file():
            return str(match)
    return None


def toolchain_status() -> dict:
    """Human/CLI-facing status of the provisioned toolchain."""
    headless = find_analyze_headless()
    java = find_java()
    java_major = _java_major(java) if java else None
    return {
        "ghidra": str(headless) if headless else None,
        "java": java,
        "java_major": java_major,
        "cache_root": str(CACHE_ROOT),
        "ready": headless is not None and java is not None,
    }
