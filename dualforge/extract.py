from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from dualforge.compression import CompressionError, decompress
from dualforge.detector import Detection, detect, detect_header
from dualforge.export import Exporter
from dualforge.unity import UnityArchive, UnityError
from dualforge.unreal import PakError, UnrealBridge, UnrealError

Progress = Callable[[int, int, str], None]
Cancel = Callable[[], bool]


class ExtractCancelled(Exception):
    pass


@dataclass
class ExtractResult:
    detected: Optional[Detection] = None
    extracted: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def ok(self) -> int:
        return len(self.extracted)


@dataclass
class ExtractOptions:
    out_dir: str
    aes_key: Optional[str] = None
    engine: Optional[str] = None
    type_filter: Optional[Tuple[str, ...]] = None
    files: Optional[List[str]] = None
    formats: Optional[dict] = None
    usmap: Optional[str] = None
    driver: Optional[object] = None
    scheme: Optional[str] = None
    scheme_params: Optional[dict] = None
    egame: Optional[str] = None
    progress: Optional[Progress] = None
    is_cancelled: Optional[Cancel] = None


def extract_file(path: str, options: ExtractOptions) -> ExtractResult:
    result = ExtractResult()
    from dualforge.drivers.driver import GameDriver

    driver = options.driver
    if driver is None:
        from dualforge.drivers import registry

        driver = registry.match(path)
    elif driver is not None and isinstance(driver, str):
        from dualforge.drivers import registry

        driver = registry.get(driver)
    driver = driver if isinstance(driver, GameDriver) else None
    if driver is not None:
        _apply_driver(options, driver, path)
    detection = detect(path)
    result.detected = detection
    if detection is None:
        raise ValueError(f"unable to identify archive format: {path}")
    if options.engine and options.engine != "auto" and detection.engine != options.engine:
        raise ValueError(
            f"file detected as {detection.engine}, but engine {options.engine} requested"
        )
    if detection.engine == "unity":
        _extract_unity(path, detection, options, result)
    elif detection.engine == "unreal":
        _extract_unreal(path, detection, options, result)
    elif detection.engine == "bethesda":
        _extract_bethesda(path, detection, options, result)
    elif detection.engine == "cdpr":
        _extract_cdpr(path, detection, options, result)
    elif detection.engine == "container":
        _extract_container(path, detection, options, result)
    else:
        raise ValueError(f"unsupported engine: {detection.engine}")
    return result


def _apply_driver(options: ExtractOptions, driver, path: str) -> None:
    """Apply a game driver's config onto extract options.

    Only fills in values the caller did not already provide, so explicit
    CLI/GUI options always win over driver defaults.
    """
    # script/asset filters
    if driver.asset_filter and not options.type_filter:
        options.type_filter = tuple(driver.asset_filter)
    # export format defaults
    if driver.export_formats and not options.formats:
        options.formats = dict(driver.export_formats)
    # engine constraint
    if driver.engine != "auto" and not options.engine:
        options.engine = driver.engine
    # encryption scheme + params
    if not options.scheme and driver.encryption_scheme:
        options.scheme = driver.encryption_scheme
    if not options.scheme_params and driver.encryption_params:
        options.scheme_params = dict(driver.encryption_params)
    # exact CUE4Parse EGame for this title
    if not options.egame and getattr(driver, "egame", None):
        options.egame = driver.egame
    # usmap hint: search the archive's Paks folder, not the export target
    if driver.usmap_required and not options.usmap:
        from pathlib import Path

        paks_dir = str(Path(path).parent)
        options.usmap = _find_usmap_hint(paks_dir)


def _extract_unity(path: str, detection: Detection, options: ExtractOptions, result: ExtractResult) -> None:
    archive = UnityArchive(path)
    key = options.aes_key
    scheme = options.scheme or "aes-256"
    if not key:
        try:
            from dualforge.unreal import KeyStore

            entry = KeyStore().find_for_archive(path)
            if entry is not None:
                key = entry.aes_key
                scheme = options.scheme or entry.scheme or "aes-256"
        except Exception:
            pass
    if key:
        archive.set_decrypt_key(key, scheme=scheme)
    assets = [a for a in archive.assets()]
    if options.type_filter:
        assets = [a for a in assets if a.type_name in options.type_filter]
    if options.files:
        wanted = {f.lstrip("/") for f in options.files}
        assets = [a for a in assets if a.path.lstrip("/") in wanted]
    total = len(assets)
    for index, asset in enumerate(assets):
        _report(options, index, total, f"{asset.type_name}: {asset.path}")
        try:
            written = archive.extract_asset(asset, options.out_dir, formats=options.formats)
            result.extracted.extend(written)
        except UnityError as exc:
            result.errors.append(f"{asset.path}: {exc}")
        except Exception as exc:
            result.errors.append(f"{asset.path}: {type(exc).__name__}: {exc}")


def _find_usmap_hint(path: str) -> str:
    """Best-effort locate a usmap file when a driver requires one."""
    try:
        from dualforge.unreal.uex_adapter import find_usmap

        usmap = find_usmap(path)
        return usmap or ""
    except Exception:
        return ""


def _extract_unreal(path: str, detection: Detection, options: ExtractOptions, result: ExtractResult) -> None:
    from pathlib import Path

    if Path(path).suffix.lower() == ".pak":
        try:
            _extract_unreal_native(path, options, result)
            return
        except (PakError, ImportError) as exc:
            result.errors.append(f"native pak read failed: {exc}")
    _extract_unreal_bridge(path, options, result)


def _chunk_key_hint(path: str) -> str:
    try:
        from dualforge.unreal.pak import pak_footer_version

        version = pak_footer_version(path)
    except Exception:
        return ""
    if version is not None and version >= 13:
        return (
            "\n\nThis looks like a UE 5.4+ archive. Newer games may encrypt "
            "per-chunk with dynamic keys, which the native reader does not "
            "support; FModel can export such paks when given the dynamic keys."
        )
    return ""


def _extract_unreal_native(path: str, options: ExtractOptions, result: ExtractResult) -> None:
    from dualforge.unreal import PakArchive

    archive = PakArchive(path, aes_key=options.aes_key)
    entries = archive.list_files()
    if options.files:
        wanted = {f.lstrip("/") for f in options.files}
        entries = [e for e in entries if e.lstrip("/") in wanted]
    total = len(entries)
    for index, entry in enumerate(entries):
        _report(options, index, total, entry)
        try:
            written = archive.extract_file(entry, options.out_dir)
            result.extracted.append(written)
        except Exception as exc:
            result.errors.append(f"{entry}: {exc}")


def _extract_unreal_bridge(path: str, options: ExtractOptions, result: ExtractResult) -> None:
    from pathlib import Path

    bridge = UnrealBridge()
    usmap = options.usmap
    if not usmap:
        from dualforge.unreal.uex_adapter import find_usmap

        usmap = find_usmap(str(Path(path).parent))
    dynamic_keys = None
    scheme = None
    if options.scheme:
        scheme = options.scheme if options.scheme not in ("", "aes-256") else None
    if not options.aes_key:
        try:
            from dualforge.unreal import KeyStore

            entry = KeyStore().find_for_archive(path)
            if entry is not None and not options.aes_key:
                dynamic_keys = entry.dynamic_keys or None
                scheme = options.scheme or (entry.scheme if entry.scheme not in ("", "aes-256") else None)
        except Exception:
            pass
    entries = bridge.list_files(
        path, aes_key=options.aes_key, usmap=usmap,
        dynamic_keys=dynamic_keys, scheme=scheme, egame=options.egame,
    )
    if options.files:
        entries = [e for e in entries if e.get("path") in set(options.files)]
    total = len(entries)
    for index, entry in enumerate(entries):
        _report(options, index, total, str(entry.get("path", entry)))
    if entries:
        try:
            count = bridge.extract(
                path,
                options.out_dir,
                aes_key=options.aes_key,
                files=[str(entry.get("path", "")) for entry in entries],
                usmap=usmap,
                dynamic_keys=dynamic_keys,
                scheme=scheme,
                egame=options.egame,
            )
        except UnrealError as exc:
            result.errors.append(f"{exc}{_chunk_key_hint(path)}")
            return
        result.extracted.extend(str(entry.get("path", "")) for entry in entries)
        result.skipped = max(0, len(entries) - count)


def _extract_bethesda(path: str, detection: Detection, options: ExtractOptions, result: ExtractResult) -> None:
    from dualforge.bethesda import BethesdaArchive, BethesdaError

    try:
        archive = BethesdaArchive(path)
    except BethesdaError as exc:
        result.errors.append(str(exc))
        return
    entries = list(archive.list_files())
    if options.files:
        wanted = {f.replace("\\", "/").lstrip("/") for f in options.files}
        entries = [e for e in entries if e in wanted]
    total = len(entries)
    for index, entry in enumerate(entries):
        _report(options, index, total, entry)
        try:
            written = archive.extract_file(entry, options.out_dir)
            result.extracted.append(written)
        except Exception as exc:
            result.errors.append(f"{entry}: {exc}")


def _extract_cdpr(path: str, detection: Detection, options: ExtractOptions, result: ExtractResult) -> None:
    from dualforge.cdpr import RedArchive, RedError

    try:
        archive = RedArchive(path)
    except RedError as exc:
        result.errors.append(str(exc))
        return
    entries = list(archive.list_files())
    if options.files:
        wanted = {f.replace("\\", "/").lstrip("/") for f in options.files}
        entries = [e for e in entries if e in wanted]
    total = len(entries)
    for index, entry in enumerate(entries):
        _report(options, index, total, entry)
        try:
            written = archive.extract_file(entry, options.out_dir)
            result.extracted.append(written)
        except Exception as exc:
            result.errors.append(f"{entry}: {exc}")


def _extract_container(path: str, detection: Detection, options: ExtractOptions, result: ExtractResult) -> None:
    from pathlib import Path

    raw = Path(path).read_bytes()
    current = detection
    data = raw
    while True:
        _report(options, 0, 1, f"decompressing {current.kind}")
        try:
            data = decompress(data, current.kind)
        except CompressionError as exc:
            result.errors.append(f"container decompression failed: {exc}")
            return
        nested = detect_header(data, Path(path).name)
        if nested is None:
            exporter = Exporter(options.out_dir)
            result.extracted.append(exporter.write("container.bin", data))
            return
        if nested.engine == "container":
            current = nested
            continue
        if nested.engine in {"unity", "unreal"}:
            temp = _write_temp(data, nested.kind)
            try:
                sub = extract_file(temp, options)
                result.extracted.extend(sub.extracted)
                result.errors.extend(sub.errors)
            finally:
                _remove_temp(temp)
            return
        result.errors.append(f"nested container type not supported: {nested.kind}")
        return


def _write_temp(data: bytes, kind: str) -> str:
    import tempfile

    suffix = ".pak" if kind == "pak" else ".bundle"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix="dualforge_")
    with open(fd, "wb") as fh:
        fh.write(data)
    return path


def _remove_temp(path: str) -> None:
    try:
        import os

        os.remove(path)
    except OSError:
        pass


def _report(options: ExtractOptions, index: int, total: int, message: str) -> None:
    if options.is_cancelled and options.is_cancelled():
        raise ExtractCancelled()
    if options.progress:
        options.progress(index, total, message)


__all__ = ["extract_file", "ExtractOptions", "ExtractResult"]
