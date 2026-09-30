# Vendored third-party code

## pyuepak

- **Upstream:** https://github.com/stas96111/pyuepak
- **Version vendored:** 0.2.8
- **License:** MIT, Copyright (c) 2025 stas96111 — full text in
  `pyuepak/LICENSE`
- **Location:** `dualforge/vendor/pyuepak/`

`pyuepak` is a pure-Python Unreal `.pak` reader. It is vendored rather than
installed from PyPI because the released version cannot read a large class of
real archives: its footer only knew four compression methods, so any pak
declaring Zstd, LZ4, Brotli, Custom or PWC failed to open and was misreported
as an encryption problem. Files were copied from the installed 0.2.8
distribution with `cli.py`, `__pycache__` and `oo2core_9_win64.dll` excluded.

### Patches applied

All changes are confined to the files below.

- **`utils.py`** — Replaced the four-member `COMPRESSION` enum with the full
  Unreal `EPakCompressionMethod` set, valued at `UE value + 1` to preserve the
  upstream legacy-entry convention. Added `from_name()` / `from_ue_value()`
  lookup, the `UNSUPPORTED` sentinel, `NATIVE_METHODS` and `BRIDGE_ONLY_METHODS`,
  and the `UnsupportedCompressionMethod` / `UnsupportedPakVersion` exception
  types.
- **`footer.py`** — Resolve compression names through `from_name()` so an
  unknown name becomes `UNSUPPORTED` rather than crashing, and keep the resolved
  names alongside the enum members. Footer detection is now version-agnostic:
  `locate_footer()` probes for the magic at every known offset instead of
  seeking to a fixed distance from EOF, and UE 6.0's enlarged footer is
  supported (`version.py` gains `V13`). `write()` can emit a name table.
- **`entry.py`** — Bound-check the 6-bit footer compression index, map legacy
  numeric compression through `from_ue_value()`, and delegate Zstd / LZ4 /
  Brotli to `dualforge.compression` so they decode natively. Methods with no
  decoder (Custom, LZO, PWC, Xb1zlib, XboxOneGdkZlib) raise a clear error
  pointing at the CUE4Parse/uex bridge. `write_data()` / `write()` can now emit
  compressed entries, which the upstream writer could not.
- **`index.py`** — Pass compression names through to entries, and implement
  UE 6.0's flat, prefix-compressed `FullDirectoryIndex` (ported from
  CUE4Parse's `ReadFlatDirectoryIndex`). All counts read from that blob are
  validated against the buffer before they size an allocation.
- **`oodle.py`** — Replaced with a thin adapter over
  `dualforge.compression.oodle.Oodle`. Upstream's module downloads
  `oo2core_9_win64.dll` from a third-party GitHub URL at import time and bundles
  a copy; the adapter loads lazily, performs no network access, and searches
  only an explicit, restricted set of locations.
- **`pak.py`** — Build and write the footer's compression-name table from the
  methods actually used, and assign each entry's 6-bit index accordingly.
  `add_file()` accepts a compression method.
- **`version.py`** — Added `V13` (UE 6.0).

Files copied unmodified from upstream: `__init__.py`, `file_io.py`.

These files are excluded from Ruff via `per-file-ignores` in `pyproject.toml`
so they stay as close to upstream as possible and remain straightforward to
re-sync. Behaviour is covered by `tests/test_pak.py` and, against real
CUE4Parse-produced archives, by `tests/test_pak_cue4parse_fixtures.py`.

## Format reference

The pak layout implemented here is cross-checked against CUE4Parse, which is
vendored in this repository at `external/uex/external/CUE4Parse/`. Notably:

- `CUE4Parse/Compression/CompressionMethod.cs` — the compression method enum.
- `CUE4Parse/UE4/Pak/Objects/FPakInfo.cs` — footer parsing, including the
  compression-name table and its compaction behaviour.
- `CUE4Parse/UE4/Pak/Objects/FPakEntry.cs` — per-entry layout and flags.
- `CUE4Parse/UE4/Pak/PakFileReader.cs` — index parsing, including UE 6.0's
  flat directory index.

CUE4Parse is licensed under Apache-2.0. No CUE4Parse code is copied into
DualForge; it is used as the authoritative reference and as a test oracle.
