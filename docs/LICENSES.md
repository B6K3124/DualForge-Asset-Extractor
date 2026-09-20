# DualForge — License Ledger

Third-party components used by DualForge and their license status.

| Component       | License        | Redistribution note                              |
| --------------- | -------------- | ------------------------------------------------ |
| Python 3        | PSF-2.0        | fine                                             |
| PySide6         | LGPL-3.0       | OK for closed-source commercial apps; dynamic linking required |
| UnityPy         | MIT            | keep attribution (About dialog)                  |
| numpy           | BSD-3          | fine                                             |
| Pillow          | HPND (MIT-like)| fine                                             |
| lz4             | BSD-3          | fine                                             |
| zstandard       | BSD            | fine                                             |
| brotli          | MIT            | fine                                             |
| py7zr           | LGPL-2.1       | fine (dynamic)                                   |
| python-snappy   | BSD            | optional                                         |
| CUE4Parse / CUE4ParseCLI | MIT | keep attribution (About dialog)              |
| pure_python_dds.py (uyjulian) | MIT | BC7/ETC/EAC decoder reference + test oracle; keep attribution |
| bc7decomp.c (BinomialLLC) | ISC | BC7 descriptor tables; not vendored (see note) |
| vgmstream       | custom / LGPL-ish | never bundled; invoked as an external CLI (see policy) |
| oo2core_*.dll   | RAD Game Tools proprietary | never bundle; load on demand from target game or official SDK |
| ooz / python_oodle / kraken-decompressor | GPLv3 | excluded by policy                       |

## Policy

- The GPLv3 Oodle wrappers are intentionally excluded to keep DualForge
  closed-source and commercially monetizable.
- Oodle DLLs must never be redistributed; DualForge locates them in the target
  game's binary directory (or via `DUALFORGE_OODLE` / search paths).
- vgmstream is never bundled either; DualForge locates an externally installed
  `vgmstream-cli`/`vgmstream` binary via `DUALFORGE_VGMSTREAM`, PATH, Settings
  or `~/.dualforge` and calls it as a subprocess for audio conversion.
- Third-party attribution must appear in the application About dialog and this
  document before first public release.
