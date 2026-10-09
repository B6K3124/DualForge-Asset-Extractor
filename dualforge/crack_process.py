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

All candidate hex keys are deduplicated, ranked by (length, entropy) and run
through scheme-aware validation against the pak (``aes-256``, per-game presets,
``sm4-abi``, ...) so offline keys verify exactly like static ones.
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
    script = Path(__file__).resolve().parent.parent / "scripts" / "ghidra" / "ghidra_key_finder.py"
    if not script.is_file():
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
) -> list[Candidate]:
    """Pull key-sized candidates from a context buffer near a signature.

    Two passes: printable 64/32-hex-character windows (ASCII-encoded keys) and
    raw high-entropy 16/32-byte windows (binary keys). ``context_offset`` is
    the context's absolute offset in the process address space.
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
    log: object | None = None,
) -> dict:
    """Run the offline runtime key hunt; return a :func:`crack`-like result."""
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
        _emit(f"scanning for {len(signatures)} signature(s)...")
        matches = scan_signature_matches(
            reader,
            signatures=signatures,
            context_size=context_size,
            threshold=entropy_threshold,
            raw_threshold=raw_threshold,
        )
        for match in matches:
            candidates.extend(match.candidates)

        _emit("sampling high-entropy windows across memory...")
        candidates.extend(
            harvest_raw_windows(reader, max_windows=raw_windows, threshold=raw_threshold or 3.75)
        )
        bytes_scanned = sum(size for _base, size in reader.readable_regions())

    ranked = _rank_candidates(candidates, max_candidates)
    hexes = [c.hex_value for c in ranked]

    verified_keys: list[VerifiedKey] = []
    pak_path: str | None = None
    if pak:
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
                    engine="unreal",
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