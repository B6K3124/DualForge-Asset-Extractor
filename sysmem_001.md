# DualForge — Session Summary

## Objective
Make DualForge's key-cracking pipeline reusable across games (online + offline), the way ABI was cracked. ABI base-pak decryption with the SM4 scheme is complete; the offline/runtime process-memory key hunt (`dualforge crack offline`) is implemented, tested, and green.

## Important Details
- Repo: `C:\Users\B6k\source\repos\DualForge`, Python 3.13. Vendored CUE4Parse has no `.git` (patch manually).
- ABI base key (verified): `1F5E4191BDE73F9C65A48D8AA0648C46C06C08F9853093C7EBF4AA5CA22F0486`; scheme `sm4-abi` (SM4 mode `a` = `TableA[i & 0x3F]`, `SboxMode.None`). ABI footer 221 bytes at EOF, magic `0x53647586` at footer+20, index offset at footer+45 / size at footer+53, decode `ROR(e ^ 0xD72CAC4E59907DA0, 23) ^ seed` (offset `0xD3A512`, size `0xB640093C`).
- `tests/test_usmap_dump.py` imports `usmap_dump` internals (`_build_test_pool`, `_walk_pool_table`, `_detect_layout`, `_LAYOUT_*`, `FNAME_*`) — satisfied by re-exporting from `dualforge/unreal/process.py` (public API intact).
- Loading `scripts/ghidra/ghidra_key_finder.py` via importlib REQUIRES registering the module in `sys.modules` before `exec_module`, else `@dataclass` fails with `AttributeError: 'NoneType' object has no attribute '__dict__'`; exact pattern = `tests/test_ghidra.py` lines 13–17. Script path = `Path(__file__).resolve().parent.parent / "scripts" / "ghidra" / "ghidra_key_finder.py"` (two parents, not three).
- Offline scan defaults: hex-text threshold 3.5, raw threshold 3.75, `max_sig_hits` 64, sig length filter `6 <= len <= 4096`, `context_size` 4096, `max_per_match` 256, `max_candidates` 512, `raw_windows` 250_000. Ranking entropy-first (`(-entropy, -length)`).
- `collect_candidates` dedupes on exact window bytes (content), NOT entropy-clusters: an entropy-based collapse wrongly discards the aligned key — a shift-by-1 window padded with a zero byte can score entry 4.0 while the true key scores lower. Hex-text pass emits ONE candidate per maximal printable-hex run (no `O(N^2)` substring flood); raw pass keeps every distinct 16/32-byte window.
- `harvest_raw_windows` samples on a stride grid (best-effort; misaligned keys can be missed — tests place keys at offset 0). Candidate offset = `pos + off` (absolute pos from `read_chunks`); a `base + pos + off` double-count was fixed.
- `validate_keys_against_pak` still calls module-global `probe_pak_blocks` first (tests monkeypatch `dualforge.crack.probe_pak_blocks`), then falls back to `probe_pak_blocks_file` for ABI indexes outside the 16 MB tail window.
- Test-fixture lessons: deterministic fixtures (`_KEY16`, `_KEY32` = sha256 digest for guaranteed entropy, `_HEXSTR64`); never `\xff` padding (looks high-entropy and floods caps); deterministic constants instead of `random`.
- `crack_offline` result dict keys: `status` ("ok" | "no_key_found" | "scan_only"), `process`, `pid`, `pak`, `bytes_scanned`, `matches`, `candidates`, `verified`, `verified_keys`, `saved`. Keys saved as `f"{base_title} [offline-{index}]"` with notes `"cracked from running process memory"`.

## Work State
### Completed
- `dualforge/unreal/process.py` (new): constants (`READ_CHUNK`, `PROCESS_*`, `MEM_COMMIT`, `PAGE_*`), `ProcessError`, `check_windows`, `list_processes`, `find_process`, `resolve_process`, `ProcessReader` (`read`, `readable_regions` — also skips `PAGE_NOACCESS`/`PAGE_GUARD`, `region`, `scan`, `scan_region`, `read_chunks`).
- `usmap_dump.py` refactor: imports from `process.py`; re-exports `UsmapDumpError = ProcessError`, `ProcessReader`, `check_windows`, `find_process`, `list_game_processes = list_processes`; uses `ProcessReader` in `scan_fname_pool`/`_scan_packed_pool`. Tests green.
- ABI file probe: `brute.py` gained `_abi_index_info(raw, expected_size)` (shared decode), refactored `_abi_index_blocks`, `probe_pak_blocks_file(path, count)` and `_0padded_blocks`; `import os` added. Fallback wired into `validate_keys_against_pak` in `crack.py`.
- `dualforge/crack_process.py` (new): `_load_ghidra_finder`, `default_signatures` (abi_table_a + AesKey/FAesKey/creditkey + aes_sbox/aes_sbox_inv/sm4_sbox), `Candidate`/`Match` (with `as_dict`), `shannon_entropy`, `collect_candidates` (hex-text runs + raw windows), `scan_signature_matches`, `harvest_raw_windows`, `_rank_candidates`, `crack_offline`, `summarize`.
- CLI: `crack offline` subcommand in `cli.py` (`--process`, `--pid`, `--list-processes`, `--pak`, `--scheme`, `--title`, `--no-save`, `--block-count`, `--max-candidates`, `--raw-windows`); `_cmd_crack_offline` handler in `cli_commands/crack.py`; exported from `cli_commands/__init__.py`. `--help` verified.
- Tests: `tests/test_crack_process.py` with `FakeReader` (10 tests; orchestration via monkeypatched `ProcessReader`/`resolve_process`/`validate_keys_against_pak`/`KeyStore`/`find_validation_pak`). `test_collect_candidates_finds_both_hex_text_and_raw` fixed (see Important Details).
- Full suite: **651 passed**; `ruff check` clean on all changed files.
- Carried-over completed work: SM4 engine + tables (`sm4.py`, `sm4_tables.py`), scheme-aware `validate_key`/`probe_pak_blocks`/ABI blocks, `arena-breakout` preset, `uex_adapter.py` GAME/FOLDER mapping, UEX/CUE4Parse SM4 ABI patches, `SM4_SBOX` + `sm4_sbox` preset in the ghidra finder, `tests/test_encryption.py` SM4/ABI tests. `scripts/research/` holds the RE research scripts.

### Active
- None.

### Blocked
- None.

## Next Move
1. Exercise `crack offline` against a live game process + pak to confirm end-to-end (signature scan → raw harvest → validation → KeyStore save).
2. Consider tuning `raw_threshold`/`raw_windows` based on real-world hit/miss rates.

## Relevant Files
- `dualforge/crack_process.py`: offline/runtime key hunt.
- `dualforge/unreal/process.py`: shared Windows process reader (extracted from usmap_dump).
- `dualforge/unreal/usmap_dump.py`: consumes process.py, re-exports its API.
- `dualforge/encryption/brute.py`: `probe_pak_blocks_file`, `_abi_index_info`, `_0padded_blocks`, scheme-aware validate/probe.
- `dualforge/crack.py`: `validate_keys_against_pak` (tail probe + file-probe fallback), `find_validation_pak`.
- `dualforge/cli.py`, `dualforge/cli_commands/crack.py`, `dualforge/cli_commands/__init__.py`: `crack offline` CLI.
- `dualforge/encryption/schemes/sm4.py`, `sm4_tables.py`, `dualforge/encryption/presets.py`, `dualforge/unreal/uex_adapter.py`: ABI/SM4 scheme support (complete).
- `scripts/ghidra/ghidra_key_finder.py`: AES/SM4 table constants loaded at runtime; `tests/ghidra` loads it the same `sys.modules` way.
- `external/uex/external/CUE4Parse/CUE4Parse/GameTypes/ABI/Encryption/SM4/ABIDecryption.cs`, `UE4/Pak/Objects/FPakInfo.cs`, `external/uex/src/Uex/Program.cs`: UEX/CUE4Parse ABI SM4 support.
- `scripts/research/`: RE research scripts.
- `tests/test_crack_process.py`, `tests/test_usmap_dump.py`, `tests/test_crack.py`, `tests/test_encryption.py`: regression coverage.
- `sysmem_001.md`: this summary.