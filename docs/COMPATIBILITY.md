# DualForge — Compatibility & Integration Plan

## Architecture

```
[ PySide6 GUI / CLI ]                 main.py, dualforge/ui, dualforge/cli
        |
[ Engine Router / Detector ]          dualforge/detector  (magic bytes)
        |
   +----+----+
   |         |
[ Unity ] [ Unreal ]                  dualforge/unity, dualforge/unreal
   |         |
[ Decompression Core ]                dualforge/compression (zlib/gzip/bz2/lzma/
                                       lz4/zstd/brotli/oodle-ctypes/7z)
[ Export / Audio ]                    dualforge/export, dualforge/audio
[ Key Store + Sync ]                  dualforge/unreal/keys.py
```

## Compatibility matrix

| Target | Format | Backend | Verified |
| --- | --- | --- | --- |
| Unreal UE4.17–4.21 | `.pak` v8B | pyuepak native (no tools) | ✔ tests/test_pak.py |
| Unreal UE4.22–4.25 | `.pak` v9 | pyuepak native | ✔ tests/test_pak.py |
| Unreal UE4.26–4.27 | `.pak` v10 | pyuepak native | ✔ tests/test_pak.py |
| Unreal UE5.0–5.3 | `.pak` v11 | pyuepak native | ✔ tests/test_pak.py |
| Unreal UE5.4–5.8 | `.pak` v12 | pyuepak native | ✔ tests/test_pak.py; real game (TEKKEN 8, 20,778 files listed natively; Oodle entries require the game-shipped `oo2core_*.dll`) |
| Unreal UE3–UE5 | `.utoc` / `.ucas` (IoStore) + pak fallback | uex adapter (`dualforge/unreal/uex_adapter.py`) — auto-EGame probing via `doctor`, engine-band mapped from the pak footer (`VERSION_GAMES`: UE3/early-UE4 paks → `GAME_UE3_0`/`GAME_UE4_x`, footer 9–13 → UE4.17→UE6.0 candidates; generic fallback is engine-band-aware so old paks never mis-serialize as UE5; `DUALFORGE_EGAME` forces an exact engine) | ✔ verified on TEKKEN 8: 279,410 files across 100 archives; raw files (.wem/.ini) extract byte-perfect (RIFF-validated); unversioned packages (`.uasset`) require a CUE4Parse mappings file — auto-found via `UexAdapter.find_usmap` (`DUALFORGE_USMAP` → `~/.dualforge/*.usmap` → pak folder → cwd); mesh preview verified live (skeletal `SK_CH_bal_dokuro` + static `SM_CraneRig_Arm`, GLB re-parsed in the 3D viewport, **texture-mapped** — uex `preview-mesh --materials` bakes material base-color textures into the GLB; disable with `DUALFORGE_MESH_TEXTURES=0`) |
| Unreal | `.usmap` mappings (UE only — Unity never needs one) | `dualforge/unreal/usmap.py` (native parser/rebuilder: header, versioning block, 37 property kinds, zstd/brotli) + `usmap_dump.py` (Windows FNamePool dumper, `usmap dump` CLI / *Tools ▸ Generate USMAP* GUI, output auto-placed in `~/.dualforge`) | ✔ parser/writer round-trip vs CUE4Parse fixtures (40,567 names / 1,833 enums / 8,991 structs; rebuilt usmaps produce byte-identical CUE4Parse exports); 34 unit tests |
| Unreal | AES-256 encrypted | auto-probe: no-key → key store (`keys.json`) → default key; FModel `Global.AESKeys.json` import + opt-in sync (FortniteCentral, aes.ue4server.com); per-entry `scheme`/`guid`/`parameters` persisted | ✔ tests/test_unlock.py |
| Unreal | Oodle-compressed | game-shipped `oo2core_*.dll` (ctypes, never bundled/downloaded); auto-discovered in the pak's folder chain + `Binaries/`/`Engine/Binaries` subpaths | ✔ verified on TEKKEN 8 with the UE 5.7 engine-shipped `oo2core_9_win64.dll` (via `~/.dualforge`); texture/ini/`.wem` reads OK |
| Unreal | per-chunk / dynamic-key / scheme-based encryption (CUE4Parse GameTypes) | `dualforge/encryption` registry + presets (AES+XOR, derived AES, partial, custom round keys, Unity CN XOR); plain-AES keys open natively (pyuepak), non-AES schemes route through the CUE4Parse bridge (`UexAdapter` passes `scheme`→`game` and `dynamicKeys`); `keys test <pak>` verifies a scheme+key offline; `keys schemes` lists registered schemes/presets | ✔ tests/test_encryption.py, tests/test_unlock.py |
| Unreal | hardcoded AES keys in game binaries | automated headless-Ghidra key hunt (`scripts/ghidra/ghidra_key_finder.py`): AES S-box signature scan + high-entropy hex-key harvest, top 32-byte candidates auto-added to the key store; GUI "Scan ALL detected binaries" and `crack run --all-binaries` hunt every auto-detected game executable (not just the top-scored one) and deduplicate the combined candidates | ✔ tests/test_ghidra.py, tests/test_crack.py |
| Unity 2019–2021 | `.unity3d` / `.bundle` / `.assets` | UnityPy | ✔ verified on real games (Raft 2021.3, CarX, Tabletop Simulator) |
| Unity 2022.3 LTS | UnityFS v3, LZ4/LZ4HC blocks | UnityPy | — |
| Unity 6 (6000.x, 6.3 LTS) | UnityFS, newer serialized formats | UnityPy + typetree fallback for undecodable objects | — |
| Unity | encrypt/decrypt keys (incl. CN Pro) | `UnityPy.set_assetbundle_decrypt_key`; `0x`-hex and 16-char keys normalized; `unity-cn` scheme can supply a literal XOR mask | — |
| Unity | `.resS` / `.resource` / `.split*` sibling streams | eager pre-load into the UnityPy environment (`UnityArchive.load_sibling_streams`) | ✔ tests/test_unlock.py |
| Audio (any) | WEM/FSB/OGG/XMA/ADPCM/... | vgmstream (subprocess), `.wem`-style preview sniffing | — (raw `.wem` extraction from real paks verified on TEKKEN 8; conversion needs vgmstream, not installed) |
| Containers | zip / 7z / gzip / zstd / lz4 / lzma | dualforge/compression | — |

Engine versions are surfaced in the GUI (properties + preview meta: "Unity version", "Serialized format"; native pak version on the badge). Assets whose serialized format is too new for UnityPy to decode fall back to a type-tree JSON read instead of failing the preview, and per-asset extraction errors are isolated so one bad asset never aborts the run.

## External dependencies

| Dependency     | Purpose                      | License              |
| -------------- | ---------------------------- | -------------------- |
| PySide6        | GUI                          | LGPL-3.0 (free for commercial use) |
| UnityPy 1.25+  | Unity parsing/export         | MIT                  |
| numpy / Pillow | UnityPy texture pipeline     | BSD / HPND          |
| lz4 / zstandard / brotli / py7zr | codecs      | BSD-3 / BSD / MIT / LGPL-2.1 |
| CUE4Parse-based CLI | Unreal parsing (e.g. `uex`, .NET 10) | MIT / Apache-2.0 |

> **Unreal backend note (2026-08):** the original `CUE4ParseCLI` project is archived and the PyPI
> package `cue4parse` is broken (import fails out of the box). DualForge's `UnrealBridge` is a
> generic subprocess wrapper — it works with any CUE4Parse-based CLI exposing `list`/`extract`
> (set `DUALFORGE_CUE4PARSE` or configure in Settings). The maintained successor
> [**uex**](https://github.com/arkive-games/uex) (Apache-2.0) is the recommended binary; it is
> auto-discovered in `~/.dualforge` (e.g. an extracted `uex` release folder) and handled by a
> dedicated adapter (`dualforge/unreal/uex_adapter.py`) that generates a throwaway
> `profiles.json` per invocation, auto-probes the CUE4Parse `EGame` via `uex doctor` (folder
> hints first — TEKKEN 7/Fortnite/Palworld/Tarkov/Valorant — then pak footer version, then
> LATEST fallbacks), and converts FModel-style export trees into DualForge's raw layout.
> On first mount uex downloads its Oodle/zlib natives into `.uex-cache` next to the exe
> (requires network; the user's own `oo2core_*.dll` in `~/.dualforge` is picked up by the
> native pak path instead).

| vgmstream      | audio conversion             | (verify before distribution) |
| oo2core_*.dll  | Oodle decompression          | RAD proprietary (never bundled) |

## Monetization & licensing decisions

1. **PySide6 (LGPL)** instead of PyQt6 (GPL) — selling a closed-source build is
   free and legal with PySide6.
2. **No GPL Python Oodle bindings** — would contaminate the closed-source build.
   Oodle via ctypes on game-shipped DLL, or inside CUE4Parse.
3. **Key sync is opt-in** — memory-dump key scanning (README idea) is out of scope;
   manual entry + community endpoints only. The automated Ghidra key hunt is opt-in,
   local, and runs entirely on the user's own machine; it only ever writes candidate
   keys into the user's local `~/.dualforge/keys.json`.
4. `.NET 8` runtime is required for the Unreal path; the Unity path and
   decompression core work without it.

## Build phases

1. Scaffold + core codecs + detector         (done)
2. Unity module + export                     (done, v0.1)
3. Unreal bridge + key store                 (done, v0.1)
4. GUI + CLI parity                          (done, v0.1)
5. Native pak (pyuepak), tree/checkbox UI, formats, folder mode, profiles, stats, packaging (done)
6. Engine-era verification matrix UE4.27–UE5.8 / Unity 2022.3 LTS + Unity 6 (done)
7. Max-asset unlock: key auto-probe, game-folder Oodle discovery, FModel key import + multi-endpoint sync, UE5.4+ chunk-key detection/routing, Unity .resS streams, profile key binding (done)
8. Automated Ghidra key hunt: headless `analyzeHeadless` lifecycle + ghidra_bridge, AES S-box signature scan, entropy key harvest, keystore auto-add; multi-binary scan-all (`crack run --all-binaries` / GUI "Scan ALL detected binaries", ranked-exe auto-detection + candidate dedup + pak validation) (done)
9. Multi-key-type unlock: typed `KeyEntry` (scheme/guid/parameters), `dualforge/encryption` registry + pipeline + presets (AES, AES+XOR, derived AES, partial, custom round keys, Unity CN), `keys schemes`/`keys test` CLI, UI scheme selector, dynamic-key + scheme routing through the CUE4Parse bridge (done)
10. Licensing audit + EULA (next)
