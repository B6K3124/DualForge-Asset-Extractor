"""Offline / runtime key crack for a running game (Windows).

Complements :mod:`dualforge.crack` (static Ghidra hunt on disk binaries) for
games whose key never reaches disk and is only present in the process once the
game is running. Attaches to the game process with read access, harvests
key-sized high-entropy candidates from memory, then validates them against a
real pak exactly like the static path.

Candidate sources, in order of precision:

* signature contexts - the 8 KB around any match of a known crypto primitive
  (AES / SM4 S-box, ABI TableA key-transform, ``AesKey``-style ASCII anchors).
  Static key material almost always sits next to one of these.
* a bounded raw-window pass over the whole readable address space for keys that
  live far from any signature.
* a bounded printable-ASCII pass for case-sensitive Unity CN 16-char mask keys.

All candidate hex keys are deduplicated, ranked by (length, entropy) and run
through scheme-aware validation against the pak (``aes-256``, per-game presets,
``sm4-abi``, ...) so offline keys verify exactly like static ones. The hunt is
adaptive: if too few unique candidates surface, thresholds are lowered and
memory is resampled (bounded by ``min_candidates``).
"""

from __future__ import annotations

import importlib.util
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dualforge.crack import VerifiedKey, find_validation_pak, validate_keys_against_pak
from dualforge.encryption.schemes.sm4_tables import MODE_TABLES
from dualforge.log import get_logger
from dualforge.unreal.keys import KeyStore
from dualforge.unreal.process import ProcessError, ProcessReader, resolve_process

logger = get_logger(__name__)

_CHUNK = 4 * 1024 * 1024

# ASCII anchors that commonly precede/follow key material in Unreal games.
_TEXT_ANCHORS: dict[str, bytes] = {
    "AesKey": b"AesKey",
    "FAesKey": b"FAesKey",
    "creditkey": b"creditkey",
}


def _load_ghidra_finder() -> object | None:
    """Load the vendored Ghidra hunt script for its signature tables."""
    from dualforge.ghidra.manager import hunter_script_path

    script = hunter_script_path()
    if script is None:
        return None
    name = "dualforge_offline_finder"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        logger.debug("could not load ghidra_key_finder tables", exc_info=True)
        return None
    return module


def default_signatures() -> dict[str, bytes]:
    """Crypto-primitive byte patterns to scan process memory for."""
    signatures: dict[str, bytes] = {"abi_table_a": bytes(MODE_TABLES["a"])}
    signatures.update(_TEXT_ANCHORS)
    finder = _load_ghidra_finder()
    if finder is not None:
        for name, table in {
            "aes_sbox": getattr(finder, "AES_SBOX", b""),
            "aes_sbox_inv": getattr(finder, "AES_SBOX_INV", b""),
            "sm4_sbox": getattr(finder, "SM4_SBOX", b""),
        }.items():
            if len(table) >= 64:
                signatures[name] = bytes(table)
    return signatures


@dataclass
class Candidate:
    hex_value: str
    entropy: float
    length: int
    offset: int
    source: str = "raw"

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["address"] = f"0x{self.offset:X}"
        return data


@dataclass
class Match:
    signature: str
    offset: int
    candidates: list[Candidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "signature": self.signature,
            "address": f"0x{self.offset:X}",
            "candidate_count": len(self.candidates),
            "candidates": [c.as_dict() for c in self.candidates[:8]],
        }


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


def collect_candidates(
    context: bytes,
    context_offset: int,
    min_length: int = 16,
    max_length: int = 32,
    threshold: float = 3.5,
    raw_threshold: float | None = None,
    max_per_match: int = 256,
    ascii_threshold: float = 3.0,
) -> list[Candidate]:
    """Pull key-sized candidates from a context buffer near a signature.

    Passes: printable 64/32-hex-character windows (ASCII-encoded keys), raw
    high-entropy 16/32-byte windows (binary keys), and printable-ASCII
    16/32-char masks (Unity CN keys, original case preserved). ``context_offset``
    is the context's absolute offset in the process address space.
    """
    if raw_threshold is None:
        raw_threshold = 3.75
    found: dict[tuple[int, int, int], Candidate] = {}
    seen_hex: set[str] = set()

    # Hex-text pass: one window per maximal printable-hex run. Sliding over a
    # long hex string would yield ~N^2 spurious tokens; a real key stored as
    # text is its own 32/64-char token delimited by non-hex bytes.
    MAX_HEX = frozenset(b"0123456789abcdefABCDEF")
    pos = 0
    n = len(context)
    while pos < n:
        if context[pos] not in MAX_HEX:
            pos += 1
            continue
        end = pos
        while end < n and context[end] in MAX_HEX:
            end += 1
        run_len = end - pos
        for key_length in range(max_length, min_length - 1, -1):
            if run_len >= key_length * 2:
                window = context[pos : pos + key_length * 2]
                entropy = shannon_entropy(window)
                if entropy >= threshold:
                    candidate = Candidate(
                        hex_value=window.decode("ascii").lower(),
                        entropy=round(entropy, 4),
                        length=key_length,
                        offset=context_offset + pos,
                        source="hex-text",
                    )
                    found[(key_length, pos, 0)] = candidate
                    seen_hex.add(candidate.hex_value)
                break
        pos = end

    for key_length in (16, 32):
        if key_length < min_length or key_length > max_length:
            continue
        for i in range(0, len(context) - key_length + 1):
            window = context[i : i + key_length]
            entropy = shannon_entropy(window)
            if entropy < raw_threshold:
                continue
            value = window.hex()
            if value in seen_hex:
                continue
            found[(key_length, i, 1)] = Candidate(
                hex_value=value,
                entropy=round(entropy, 4),
                length=key_length,
                offset=context_offset + i,
                source="raw",
            )

    # ASCII-mask pass: Unity CN keys are 16 printable chars whose original case
    # is meaningful, so slice printable runs directly (no hex decoding).
    ascii_lengths = tuple(kl for kl in (16, max_length) if min_length <= kl <= max_length)
    if ascii_lengths:
        n = len(context)
        pos = 0
        while pos < n:
            byte = context[pos]
            if not (32 <= byte < 127):
                pos += 1
                continue
            end = pos
            while end < n and 32 <= context[end] < 127:
                end += 1
            run = context[pos:end]
            if len(run) >= 16:
                for kl in ascii_lengths:
                    for start in range(0, len(run) - kl + 1):
                        window = run[start : start + kl]
                        entropy = shannon_entropy(window)
                        if entropy < ascii_threshold:
                            continue
                        value = window.decode("ascii")
                        if value in seen_hex:
                            continue
                        found[(kl, pos + start, 2)] = Candidate(
                            hex_value=value,
                            entropy=round(entropy, 4),
                            length=kl,
                            offset=context_offset + pos + start,
                            source="ascii",
                        )
            pos = end

    ranked = sorted(found.values(), key=lambda c: (-c.entropy, -c.length, c.offset))
    return ranked[:max_per_match]


def scan_signature_matches(
    reader: ProcessReader,
    signatures: dict[str, bytes] | None = None,
    context_size: int = 4096,
    min_length: int = 16,
    max_length: int = 32,
    threshold: float = 3.5,
    raw_threshold: float | None = None,
    max_per_match: int = 256,
    max_sig_hits: int = 64,
) -> list[Match]:
    """Find signature hits in the process and harvest candidates around them."""
    signatures = signatures if signatures is not None else default_signatures()
    matches: list[Match] = []
    for name, signature in signatures.items():
        if not 6 <= len(signature) <= 4096:
            continue
        hits = reader.scan(signature)[:max_sig_hits]
        for addr in hits:
            base, size = reader.region(addr)
            rel = addr - base
            cs = max(0, rel - context_size)
            ce = min(size, rel + len(signature) + context_size)
            try:
                context = reader.read(base + cs, ce - cs)
            except ProcessError:
                continue
            candidates = collect_candidates(
                context,
                base + cs,
                min_length=min_length,
                max_length=max_length,
                threshold=threshold,
                raw_threshold=raw_threshold,
                max_per_match=max_per_match,
            )
            matches.append(Match(signature=name, offset=addr, candidates=candidates))
    return matches


def harvest_raw_windows(
    reader: ProcessReader,
    max_windows: int = 250_000,
    key_sizes: tuple[int, ...] = (16, 32),
    threshold: float = 3.75,
    chunk_size: int = _CHUNK,
) -> list[Candidate]:
    """Signature-free pass: sample high-entropy windows across address space.

    ``max_windows`` bounds the total number of entropy evaluations so the walk
    stays fast even on multi-GB processes; each region contributes windows
    proportional to its readable size.
    """
    regions = list(reader.readable_regions())
    total = sum(size for _base, size in regions)
    if total <= 0:
        return []
    out: list[Candidate] = []
    for base, size in regions:
        if size < min(key_sizes):
            continue
        share = max(1, int(max_windows * size / total))
        for pos, chunk in reader.read_chunks(base, size):
            if len(chunk) < min(key_sizes):
                continue
            chunk_windows = max(1, share * len(chunk) // size)
            stride = max(1, len(chunk) // chunk_windows)
            for off in range(0, len(chunk) - min(key_sizes) + 1, stride):
                for key_length in key_sizes:
                    window = chunk[off : off + key_length]
                    if len(window) < key_length:
                        break
                    entropy = shannon_entropy(window)
                    if entropy >= threshold:
                        out.append(
                            Candidate(
                                hex_value=window.hex(),
                                entropy=round(entropy, 4),
                                length=key_length,
                                offset=pos + off,
                                source="raw-window",
                            )
                        )
                        break
    return out


def harvest_ascii_windows(
    reader: ProcessReader,
    max_windows: int = 60_000,
    key_sizes: tuple[int, ...] = (16, 32),
    threshold: float = 3.0,
    chunk_size: int = _CHUNK,
) -> list[Candidate]:
    """Signature-free pass for printable-ASCII mask keys (Unity CN).

    Mirrors :func:`harvest_raw_windows` but harvests the first key-sized window
    of each printable run it samples, keeping original case (Unity CN masks are
    case-sensitive). ``max_windows`` bounds run evaluations per region so the
    pass stays fast on multi-GB processes.
    """
    regions = list(reader.readable_regions())
    total = sum(size for _base, size in regions)
    if total <= 0:
        return []
    key_sizes = tuple(sorted(key_sizes, reverse=True))
    out: list[Candidate] = []
    for base, size in regions:
        if size < min(key_sizes):
            continue
        share = max(1, int(max_windows * size / total))
        for pos, chunk in reader.read_chunks(base, size):
            n = len(chunk)
            if n < min(key_sizes):
                continue
            chunk_windows = max(1, share * n // size)
            stride = max(1, n // chunk_windows)
            scan = 0
            sampled = 0
            while scan < n and sampled < chunk_windows:
                byte = chunk[scan]
                if not (32 <= byte < 127):
                    scan += 1
                    continue
                end = scan
                while end < n and 32 <= chunk[end] < 127:
                    end += 1
                run = chunk[scan:end]
                if len(run) >= min(key_sizes):
                    for kl in key_sizes:
                        if kl > len(run):
                            continue
                        window = run[:kl]
                        entropy = shannon_entropy(window)
                        if entropy >= threshold:
                            out.append(
                                Candidate(
                                    hex_value=window.decode("ascii"),
                                    entropy=round(entropy, 4),
                                    length=kl,
                                    offset=pos + scan,
                                    source="ascii-window",
                                )
                            )
                            break
                    sampled += 1
                scan = max(end, scan + stride)
    return out


def _rank_candidates(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Deduplicate by hex value and rank by (length, entropy)."""
    best: dict[str, Candidate] = {}
    for candidate in candidates:
        existing = best.get(candidate.hex_value)
        if existing is None or (
            candidate.entropy, candidate.length
        ) > (existing.entropy, existing.length):
            best[candidate.hex_value] = candidate
    ranked = sorted(best.values(), key=lambda c: (-c.entropy, -c.length))
    return ranked[:limit]


def detect_target_engine(target: str | None) -> str:
    """Best-effort engine of a validation target (file or folder).

    Returns ``"unreal"`` for pak/IoStore files or folders containing paks,
    ``"unity"`` for Unity bundles/serialized files or folders with Unity data.
    """
    if not target:
        return "unreal"
    p = Path(target).resolve()
    if p.is_file():
        ext = p.suffix.lower()
        if ext in (".pak", ".utoc", ".ucas"):
            return "unreal"
        return "unity"
    try:
        for pak in p.rglob("*.pak"):
            if pak.is_file():
                return "unreal"
    except OSError:
        return "unity"
    return "unity"


def crack_offline(
    process: str | None = None,
    pid: int | None = None,
    pak: str | None = None,
    scheme: str | None = None,
    save_keys: bool = True,
    title: str | None = None,
    block_count: int = 16,
    max_candidates: int = 512,
    context_size: int = 4096,
    entropy_threshold: float = 3.5,
    raw_threshold: float | None = None,
    raw_windows: int = 250_000,
    ascii_windows: int = 60_000,
    min_candidates: int = 8,
    engine: str = "auto",
    log: object | None = None,
) -> dict:
    """Run the offline runtime key hunt; return a :func:`crack`-like result.

    Candidate harvesting is adaptive: when the first pass yields too few
    candidates, the raw-window threshold is lowered and memory is resampled so
    low-entropy keys that the default thresholds would miss are still seen.
    """
    def _emit(msg: str) -> None:
        if log is not None:
            log(msg)
        logger.info(msg)

    resolved_pid, exe = resolve_process(process, pid)
    _emit(f"attaching to {exe} (pid {resolved_pid})")

    candidates: list[Candidate] = []
    bytes_scanned = 0
    with ProcessReader(resolved_pid) as reader:
        signatures = default_signatures()
        matches: list[Match] = []
        for attempt in range(3):
            active_raw = (
                raw_threshold
                if raw_threshold is not None
                else max(3.5, 3.75 - 0.15 * attempt)
            )
            _emit(f"scanning for {len(signatures)} signature(s) (raw threshold {active_raw:.2f})...")
            attempt_matches = scan_signature_matches(
                reader,
                signatures=signatures,
                context_size=context_size,
                threshold=entropy_threshold,
                raw_threshold=active_raw,
            )
            _emit("sampling high-entropy windows across memory...")
            harvested = harvest_raw_windows(reader, max_windows=raw_windows, threshold=active_raw)
            ascii_harvest = harvest_ascii_windows(reader, max_windows=ascii_windows)
            bytes_scanned = sum(size for _base, size in reader.readable_regions())
            matches.extend(attempt_matches)
            seen = {c.hex_value for c in candidates}
            for candidate in harvested + ascii_harvest:
                if candidate.hex_value not in seen:
                    candidates.append(candidate)
                    seen.add(candidate.hex_value)
            for match in attempt_matches:
                for candidate in match.candidates:
                    if candidate.hex_value not in seen:
                        candidates.append(candidate)
                        seen.add(candidate.hex_value)
            if raw_threshold is not None or len(seen) >= min_candidates or attempt == 2:
                break

    deduped: dict[tuple[str, int], Match] = {}
    for match in matches:
        deduped.setdefault((match.signature, match.offset), match)
    matches = list(deduped.values())

    ranked = _rank_candidates(candidates, max_candidates)
    hexes = [c.hex_value for c in ranked]

    target_engine = engine if engine and engine != "auto" else detect_target_engine(pak)
    verified_keys: list[VerifiedKey] = []
    pak_path: str | None = None
    if pak:
        if target_engine == "unity":
            from dualforge.crack import find_validation_bundle, validate_keys_against_unity

            pak_path = find_validation_bundle(pak)
            _emit(f"validation bundle: {Path(pak_path).name}")
            verified_keys = validate_keys_against_unity(pak_path, hexes, scheme=scheme)
            _emit(f"validated {len(verified_keys)} key(s) against the bundle")
        else:
            pak_path = find_validation_pak(pak)
            _emit(f"validation pak: {Path(pak_path).name}")
            verified_keys = validate_keys_against_pak(
                pak_path, hexes, block_count=block_count, scheme=scheme, game=Path(exe).stem
            )
            _emit(f"validated {len(verified_keys)} key(s) against the pak")
    verified = [vk.key for vk in verified_keys]

    saved: list[str] = []
    if save_keys and verified_keys:
        store = KeyStore()
        base_title = title or (Path(exe).stem if exe else "game")
        for index, vk in enumerate(verified_keys, start=1):
            entry_title = f"{base_title} [offline-{index}]"
            try:
                store.add(
                    entry_title,
                    vk.key,
                    engine=target_engine,
                    notes="cracked from running process memory",
                    scheme=vk.scheme,
                    parameters=vk.parameters or None,
                )
                saved.append(entry_title)
            except ValueError:
                continue

    if pak and verified:
        status = "ok"
    elif pak:
        status = "no_key_found"
    else:
        status = "scan_only"

    return {
        "status": status,
        "engine": target_engine,
        "process": exe,
        "pid": resolved_pid,
        "pak": pak_path,
        "bytes_scanned": bytes_scanned,
        "matches": [m.as_dict() for m in matches],
        "candidates": hexes,
        "verified": verified,
        "verified_keys": verified_keys,
        "saved": saved,
    }


def summarize(result: dict) -> str:
    """Human-readable report for a :func:`crack_offline` result dict."""
    lines = [
        f"process        : {result['process']} (pid {result['pid']})",
        f"bytes scanned  : {result['bytes_scanned']:,}",
        f"candidate keys : {len(result['candidates'])}",
    ]
    for match in result["matches"]:
        lines.append(f"  {match['signature']}: {match['candidate_count']} candidate(s) at {match['address']}")
    if result.get("pak"):
        lines.append(f"validation pak : {result['pak']}")
    lines.append(f"verified keys  : {len(result['verified'])}")
    return "\n".join(lines)