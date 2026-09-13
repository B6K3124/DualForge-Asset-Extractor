# DualForge User Guide

**DualForge** is a desktop tool for browsing, previewing, and extracting game assets
from **Unity**, **Unreal**, and **Bethesda Gamebryo** archives. This guide covers
installing and using the current build, step by step.

> The latest build always supersedes older instructions. When in doubt, follow the
> "Install the build" section below — especially the part about **which `.exe` is the
> real program** and what must stay next to it.

---

## 1. Install the current build (Windows)

The build output is a **portable folder** — there is no installer. It consists of two
things that must **stay together**:

| Item | What it is | Required? |
|---|---|---|
| `DualForge.exe` | The launcher / program | Yes |
| `_internal` | The runtime: Python, PySide6, all modules, plugins | Yes — never delete, never move alone |

### Step-by-step

1. Copy the **whole `dist` folder** (or at least `DualForge.exe` **and** the
   `_internal` folder next to it) to wherever you keep programs,
   e.g. `C:\Programs\DualForge\`.
2. Double-click `DualForge.exe`.
3. If Windows SmartScreen shows a warning: click **More info → Run anyway**
   (the build is unsigned; this is expected for a locally built program).

**Prefer a one-command install?** From the repo, run:

```powershell
.\scripts\install.ps1                # installs to %LOCALAPPDATA%\Programs\DualForge
.\scripts\install.ps1 -DesktopShortcut   # ...and adds a desktop shortcut
.\scripts\install.ps1 -InstallDir "D:\Tools"   # custom location
```

No admin rights are needed. **Uninstall** = delete the install folder (and the shortcut).

> **Common pitfall — "Failed to load Python DLL".** This error means you ran an exe
> that has no runtime next to it. The real program is `DualForge.exe` **with `_internal`
> in the same folder**. Never run:
> - `build\dualforge\DualForge.exe` (an internal PyInstaller intermediate — it is
>   deleted after each build on purpose), or
> - a bare `DualForge.exe` that was copied without its `_internal` folder.

### Optional: pin it to the taskbar / start menu

Right-click `DualForge.exe` → **Pin to taskbar** / **Pin to Start**. You can also
right-click → **Create shortcut** and put the shortcut anywhere.

---

## 2. Quick start (first 60 seconds)

1. Launch `DualForge.exe`.
2. Click **Open Archive...** (or press `Ctrl+O`) and pick a game archive:
   `.pak` (Unreal), `.utoc`/`.ucas` (Unreal IoStore), Bethesda Gamebryo
   `.bsa` / `.ba2`, or Unity `.unity3d` / `.bundle` / `.assets` / `.assetbundle`.
   You can also **drag & drop** a file onto the window, or use **File ▸ Open Folder**
   to scan an entire game directory (recursive, up to 4 levels).
3. Browse the asset tree on the left. Use the **search box** (`Ctrl+F`) and the
   **type filter** to narrow down. Click any asset — a preview appears on the right
   (textures, sprites, audio with waveform playback, meshes in 3D, text/JSON/XML,
   hex for everything else).
4. **Extract**:
   - **File ▸ Extract All** (`Ctrl+Shift+E`) — extract everything in the archive.
   - **File ▸ Export Selected** (`Ctrl+E`) — first tick the checkboxes next to the
     assets you want, then export only those.
   - Both ask for an output folder. A progress bar appears in the status bar, with a
     **Cancel** button.
5. Done. A `_dualforge_manifest.json` is written into the output folder listing every
   extracted file, plus any warnings.

> **UE5 games with unversioned packages** (TEKKEN 8, Sparking! ZERO, ...): packages
> (`.uasset`) only extract if a mappings file exists — either place the game's
> `.usmap` in `~/.dualforge`, or generate one from the running game with
> **Tools ▸ Generate USMAP from Running Game...** (see §6). Raw files (`.wem`, `.ini`,
> ...) extract without it.

> **Tip:** check the **Log** dock at the bottom — it reports format detection,
> the AES key that unlocked an archive, and extraction warnings.

---

## 3. The interface

### Menu bar

| Menu | Item | What it does |
|---|---|---|
| File | Open Archive... (`Ctrl+O`) | Open a single archive |
| File | Open Folder... (`Ctrl+Shift+O`) | Scan a whole game folder (multi-archive mode) |
| File | Open Recent | Re-open previously opened archives |
| File | Export Selected / Extract All | Extract checked assets / everything |
| File | Manage Keys... | Add, remove, or sync decryption keys (choose a scheme for non-standard protection) |
| File | Game Profiles... | Save a game (folder + key + output) and reopen it in one click |
| File | Settings... | Theme, paths, export formats, default key (see §7) |
| View | Asset Statistics... | Per-type file counts and sizes |
| View | Theme | Switch dark / light |
| Tools | **Ghidra Key Hunt...** | Find hardcoded AES keys in a game binary via headless Ghidra (see §5.4) |
| Tools | **Generate USMAP from Running Game...** | Generate a mappings file (`.usmap`) from a running **Unreal Engine 5** game (see §6) |
| Help | About DualForge | Version and info |

### Toolbar

Open, Folder, Extract (all), Export Selected, Keys, and a **Donate** button that opens
the Ko-fi page (<https://ko-fi.com/b6000>) — the URL is editable in Settings.

### Docks

- **Assets** (left) — search + type filter + checkbox tree. Checkboxes drive
  *Export Selected*; "Check All / None" buttons affect only visible rows.
- **Properties** (bottom) — a vertical splitter: the top pane shows metadata of
  the selected asset (type, size, engine version…), the bottom pane is the
  **Inspector** — a searchable Property/Value tree of the asset's full type tree
  (MonoBehaviour, Material, SerializedObject, Font, …). Right-click any row to
  copy its value.
- **Log** (bottom) — detection results, key used, warnings.

All docks can be toggled under **View** and re-docked by dragging their title bars.

---

## 4. Previews

Selecting an asset loads a preview automatically (in a background thread):

| Asset type | Preview |
|---|---|
| Texture2D / Sprite | Image viewer — zoom (`+`/`-`), **Fit**, **1:1**, pan with the mouse |
| AudioClip (Unity) | Waveform + inline **Play/Stop** with a seek bar |
| `.wav` / `.ogg` / `.flac` and exotic Unreal audio (`.wem`, `.fsb`, …) | Same audio page — exotic formats need **vgmstream** (§7) |
| Mesh (Unity) | 3D viewer — **Wireframe** toggle, **Reset view**, drag to orbit; skinned meshes also render the **skeleton** (bones + joints) |
| Mesh (Unreal) | Same 3D viewer; geometry carries UVs, and **base-color textures are baked into the preview GLB** and mapped onto the surface (`DUALFORGE_MESH_TEXTURES=0` disables the texture bake) |
| Mesh (Bethesda NIF) | Gamebryo/NetImmerse `.nif` (Skyrim SE v20.2.0.7) rendered in the 3D viewer, **diffuse-texture mapped** (BSTriShape + legacy `NiTriShapeData`/`NiTriStripsData`) |
| AnimationClip (Unity) | Track summary (position/rotation/scale keyframes per node) |
| TextAsset | Pretty-printed JSON/XML/text viewer |
| Anything else | Hex inspector (first 256 KB shown) |

Previews are cached under `~/.dualforge/preview_cache` so revisiting an asset is fast.
Audio previews that fail to decode fall back gracefully with an error page.

---

## 4.1 Exporting, replacing & repacking

**Export formats** are set in *File ▸ Settings* (or per-type via the `extract --format`
CLI flag). The status bar shows the active `Texture2D:png AudioClip:wav Mesh:fbx
AnimationClip:fbx …` chips.

| Asset type | Export formats |
|---|---|
| Texture2D / Sprite | PNG, JPG, BMP, WebP, TGA, DDS, KTX |
| Cubemap | PNG, JPG, BMP, WebP, TGA, DDS, KTX — one file per face (default PNG) |
| AudioClip | WAV, OGG, FLAC, raw |
| Mesh | OBJ, glTF, **FBX**, USD, USDA |
| AnimationClip | glTF, **FBX**, JSON |
| VideoClip / MovieTexture | source (original container), MP4, MOV, WebM, AVI, raw, JSON |
| SpriteAtlas | texture formats — one image per packed sprite |
| AnimatorController | JSON |
| Avatar | JSON |
| LightmapData | JSON |
| Font | TTF, OTF, raw |

**Skeleton & animation.** A skinned Mesh exports with its skeleton, inverse-bind
matrices and morph targets (BlendShapes) as **FBX 7.4 binary** by default (ASCII can be
forced with `DUALFORGE_FBX_ASCII=1`), or as skinned glTF. `AnimationClip` assets export
their position/rotation/scale tracks as an FBX animation (default) or a glTF animation —
both import cleanly into Blender/Unreal/Unity/Maya (FBX verified against Blender 5.2).

**Cubemaps, videos, atlases & metadata.** Selecting "Cubemap" writes each face as a
separate image (`<name>_face0.png … _face5.png`). `VideoClip`/`MovieTexture` assets
re-assemble the streamed file and keep its original container (the extension is picked
up from the clip; `source` is the auto-detected default). `SpriteAtlas` exports every
packed sprite as its own image. `AnimatorController`, `Avatar` and `LightmapData` export
readable JSON (referenced clips, bone tree, parameters/states for controllers; human
bones + skeleton for avatars; referenced light/dir/shadow-mask textures for lightmaps).

**USD world export.** `Mesh` → USD produces an ASCII `.usda`/`.usd` layer (no
external USD library needed). The `world` CLI command aggregates every readable
mesh in an archive into one USD stage — ideal for bringing a whole scene into
Blender/Blender-USD/Houdini/Omniverse:

```powershell
python main.py world "game_Data\sharedassets0.assets" -o out\world.usd
```

**Replacing & repacking (write-back).** You can swap the *data* of a Unity asset and
save the edited archive to a **new** folder (DualForge never overwrites your source
archive). Supported: `Texture2D` (PNG/JPG/BMP/WebP/TGA/DDS), `TextAsset` (any
text/script file), and `Font` (`.ttf`/`.otf`).

- **GUI:** right-click a supported asset in the tree → **Replace with File...**, pick
  the replacement, then choose a fresh output folder. The log confirms the result.
- **CLI:** the `repack` subcommand does the same headlessly:

```powershell
python main.py repack texture "sharedassets0.assets" "textures/hero_0" "hero.png" -o repacked
python main.py repack font   "sharedassets0.assets" "fonts/title"     "title.ttf" -o repacked
```

**IL2CPP metadata.** If a game ships `global-metadata.dat`, the `il2cpp` CLI can
inspect its header and dump the entire string-literal pool (il2cppdumper-`-nns`
style) — handy for string references and obfuscation triage:

```powershell
python main.py il2cpp inspect "global-metadata.dat"      # version + section counts
python main.py il2cpp strings "global-metadata.dat" -o strings.txt
```

**Unreal `.locres`.** `locres dump` converts UE localization files to JSON/CSV/text
from the CLI, and `.locres` files are detected by magic bytes in the browser.

---

## 5. Unlocking encrypted archives (decryption keys)

DualForge **never ships keys**. It decrypts with keys you provide — manually, from an
FModel file, from community endpoints (opt-in), or by finding them yourself with
Ghidra. Keys are stored locally in plain text at `~/.dualforge/keys.json`.

**Schemes.** Beyond plain AES-256, keys can carry a *scheme* (e.g. `delta-force`
AES+XOR, `snowbreak` derived AES, `unity-cn` XOR, custom round-key AES, partial
encryption) plus an optional GUID and scheme parameters, so games with non-standard
protection work too. Run `python main.py keys schemes` (or *File ▸ Manage Keys ▸ Add...*
Scheme dropdown) to see what's supported.

### 5.1 Add a key manually

**File ▸ Manage Keys → Add...** — enter a title and a key, pick a **Scheme** (default
AES-256), and optionally a GUID and comma-separated parameters (e.g.
`xor_key=1122334455667788`). Or paste your default key under
**File ▸ Settings ▸ Default AES key**.

Prefer to verify a key before committing? Use the CLI:
`python main.py keys test "game.pak" --title "<name>"` (or `--aes <key> --scheme <s>`)
confirms the scheme+key against the pak's encrypted index before you save it.

### 5.2 Import from FModel (`Global.AESKeys.json`)

**File ▸ Settings → Import AES Keys JSON...** — picks up main keys **and** per-chunk
dynamic keys (important for modern games).

### 5.3 Sync from community endpoints (opt-in, network)

**File ▸ Manage Keys → Sync from endpoints** pulls the latest keys from community
repositories (defaults: FortniteCentral, aes.ue4server.com — editable in Settings).

### 5.4 Automated Ghidra key hunt (find keys yourself)

If a key is not in any list and you own the game binary, DualForge can scan it for
AES S-box signatures and high-entropy 32-byte hex keys. Missing Ghidra/Java is no
longer a blocker: **Check Setup** (and the hunt itself) offers to download a
portable Ghidra 11.x + Java 21 into `~/.dualforge` automatically (about 500 MB).

**From the GUI (recommended):** **Tools ▸ Ghidra Key Hunt...**

**Scan one binary:**

1. Click **Browse...** and pick the game `.exe` / `.dll`.
2. (Optional) tweak the entropy threshold and how many candidate keys to store.
3. Click **Check Setup** first. If Ghidra or Java is missing, confirm the
   auto-download when asked (or choose **No** and point it at your own install
   with `GHIDRA_HOME` / `JAVA_HOME`). It then verifies Ghidra, Java, and the bridge.
4. Click **Start Key Hunt** and watch the live log. This can take several minutes.
5. The best candidates are added to the key store automatically as
   `"<game> [ghidra-N]"` and are tried automatically the next time you open the pak.

**Scan ALL binaries in the install folder (recommended):**

1. Tick **"Scan ALL detected binaries in the install folder"**.
2. **Browse...** now picks a folder instead of a file — choose the game's install
   directory.
3. **Check Setup**, then **Start Key Hunt**. DualForge auto-detects every game
   executable under that folder (scoring heuristics skip launchers/helpers) and
   hunts each one in turn; the log shows per-binary progress.
4. Candidate keys from **all** binaries are collected, deduplicated, and written
   to the key store as `"<binary> [ghidra-N]"`, so the correct `.exe` never has
   to be located manually. Note this takes a few minutes per binary.

**From the command line:**

```powershell
python scripts\ghidra\ghidra_key_finder.py "C:\Game\Game.exe"
python scripts\ghidra\ghidra_key_finder.py --check   # diagnostics
```

**End-to-end auto-crack (CLI only):** `crack run` wires the whole pipeline
together — auto-detect the game exe, provision Ghidra/JRE (downloads on demand),
hunt, validate candidates against a real pak, and save only the verified keys:

```powershell
python main.py crack run "C:\Game"                  # scan the top-scored executable
python main.py crack run "C:\Game" --all-binaries   # scan every detected executable
python main.py crack status                         # toolchain readiness
```

**Prerequisites:** none to get started — the GUI auto-downloads a portable Ghidra
11.x + Java 21 into `~/.dualforge/ghidra` on request. To use your own install
instead, set `GHIDRA_HOME` (or add Ghidra to `PATH`) and Java 21 to `PATH`/`JAVA_HOME`.
`ghidra-bridge` is installed automatically on first run (source installs only — for
the frozen build, install it into the same Python environment used to build).

### 5.5 How unlocking works

Every pak is opened by auto-probing: **no key → every stored key → default key**.
The winning key is reported in the log. Toggle this off under
**Settings ▸ "Try every key from the key store before failing"** if you want only the
default key tried.

---

## 6. Generating a USMAP file (Unreal Engine games only)

Some UE5 games (e.g. TEKKEN 8, Dragon Ball: Sparking! ZERO) ship **unversioned
packages**: their `.uasset` files omit property names, so extraction needs a
*mappings file* (`.usmap`) describing the game's enums and structs — the same file
FModel uses. If the game does not ship one (TEKKEN 8's is not redistributable),
DualForge can **generate it locally from a running instance of the game** — no
internet needed.

> **This applies only to Unreal Engine games.** Unity titles never need a mappings
> file — skip this section entirely if you are extracting Unity assets.

### 6.1 From the GUI (recommended)

1. **Start the game** and leave it running (it must be at least at the main menu;
   the name table is already loaded by then).
2. In DualForge, open **Tools ▸ Generate USMAP from Running Game...**.
3. Pick the game process from the list (sorted by name — look for the game's
   executable, e.g. `POLARIS-Win64-Shipping.exe`).
4. The output path is **pre-filled** to `~/.dualforge\<game>.usmap` — that folder is
   searched automatically, so no further configuration is needed. Change it if you
   prefer a different location.
5. Click **Generate USMAP** and watch the log. The dump scans the game's memory for
   the global name table and can take a minute.
6. When asked, click **Yes** to use the new file as the mappings file for this game
   (this just sets *Settings ▸ USMap* for you).

From then on, opening the game's `.utoc`/`.ucas` archives uses the mappings file
automatically.

### 6.2 From the command line

```powershell
# find the game's executable name
python main.py usmap dump --list-processes

# dump the name table into ~/.dualforge (auto-discovered by find_usmap)
python main.py usmap dump --process "POLARIS-Win64-Shipping.exe" -o "%USERPROFILE%\.dualforge\TEKKEN 8.usmap"

# or attach by process id instead
python main.py usmap dump --pid 12345 -o game.usmap

# verify a usmap file (any usmap, not just generated ones)
python main.py usmap validate "game.usmap"

# rebuild / recompress a usmap (zstd is the smallest; brotli is a valid alternative)
python main.py usmap repack "game.usmap" -o small.usmap --compression zstd
```

`usmap validate` reports the name/enum/struct counts and format version;
`usmap repack` lets you downgrade the format version or switch compression.
The extracted package names must resolve against the same game version the dump was
taken from — re-dump after a game update.

### 6.3 Requirements and limits

| Requirement / limit | Details |
|---|---|
| **UE5 game with unversioned packages** | UE4 games and versioned packages don't need a usmap; Unity never needs one |
| Game must be **running** | The name table only exists in the game's memory — start the game first |
| **Windows only** | The dump reads the game's memory via the Windows API |
| **Administrator rights** | Needed if the game blocks memory access (most do). Run DualForge (or the terminal) **as administrator**; the error message says so explicitly |
| Unreal Engine 5.x | Uses the UE5 global `FNamePool`; engines before UE5 store names differently |
| Game updates | Re-dump after every game update — package names change |

### 6.4 Troubleshooting the dump

| Error message | What it means / what to do |
|---|---|
| `no running process named ...` | The game isn't running, or the executable name is wrong — use `usmap dump --list-processes` to check |
| `FNamePool anchor not found (is this a UE5 game?)` | The process is running but is not an Unreal Engine 5 game (or you picked the wrong process) |
| `OpenProcess failed ... (run as admin ...)` | The game blocks access — start DualForge as administrator |
| `ReadProcessMemory failed ...` | The game's memory changed while scanning (rare) — just run the dump again |

---

## 7. Settings reference (**File ▸ Settings**)

| Setting | Purpose |
|---|---|
| Theme | Dark / light |
| Default output folder | Pre-filled destination for extraction dialogs |
| CUE4Parse CLI (uex) | Path to the **uex** CLI — required for Unreal IoStore (`.utoc`/`.ucas`) and pak fallback. Download from [github.com/arkive-games/uex](https://github.com/arkive-games/uex) |
| USMap (UE5 packages) | Path to a `.usmap` mappings file for unversioned UE5 packages (required for games like TEKKEN 8). Also auto-discovered in `~/.dualforge` — generate it with **Tools ▸ Generate USMAP** (§6) |
| vgmstream | Path to `vgmstream-cli` for exotic audio (`.wem`, `.fsb`, `.vag`, …) and FLAC export. Optional |
| Preview cache folder | Where previews are cached (default `~/.dualforge/preview_cache`) |
| Default AES key | Key tried for every archive (besides the key store) |
| Key probing | Try every stored key before failing |
| Key file (FModel) | Import `Global.AESKeys.json` |
| Sync endpoints | Comma-separated community key URLs |
| Donation URL | What the toolbar Donate button opens |
| Export formats per asset type | Texture, Sprite, Audio, Mesh (OBJ/glTF/**FBX**/USD), Animation (glTF/**FBX**/JSON), Cubemap, Video, Sprite atlas, Animator, Avatar, Lightmap (JSON) |

### External tools at a glance

| Tool | Needed for | Where to configure |
|---|---|---|
| **uex** (CUE4Parse CLI) | `.utoc`/`.ucas` IoStore, pak fallback | Settings → CUE4Parse CLI, or env var `DUALFORGE_CUE4PARSE` |
| **USMap** (`.usmap`) | Unversioned UE5 packages (`.uasset`) — **Unreal only** | Settings → USMap, or drop the file in `~/.dualforge`; generate with **Tools ▸ Generate USMAP from Running Game...** (§6) |
| **Engine version (EGame)** | Pick the exact CUE4Parse engine when auto-probing guesses wrongly (rare — see §9) | Env var `DUALFORGE_EGAME`, e.g. `GAME_UE5_2` |
| **vgmstream** | Exotic audio decode + FLAC export | Settings → vgmstream, or env var `DUALFORGE_VGMSTREAM` |
| **Oodle DLL** (`oo2core_*.dll`) | Oodle-compressed paks | Found automatically in the pak's folder chain, `Binaries/`, `~/.dualforge`, `PATH` — **never bundled or downloaded** |

---

## 8. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| "Failed to load Python DLL" | You ran the wrong exe or split it from its runtime. Run `DualForge.exe` **with `_internal` in the same folder** (from `dist`), never `build\dualforge\DualForge.exe` |
| App closes instantly, nothing happens | Same as above, or a missing `_internal` folder |
| "CUE4Parse CLI not found" | IoStore/fallback needs **uex** — set its path in Settings |
| "Oodle DLL not found" | Oodle-compressed pak. Copy `oo2core_*.dll` from the game's `Binaries\Win64\` into the pak's folder, the working directory, or `~/.dualforge` |
| Archive won't open / encrypted | Add the decryption key (File ▸ Manage Keys, pick the right scheme), import an FModel JSON, verify it with `keys test`, or run the Ghidra key hunt (Tools ▸ Ghidra Key Hunt) |
| Exotic audio has no sound | Install **vgmstream** and set it in Settings; without it only WAV/OGG/FLAC preview |
| 3D mesh viewer empty | OpenGL unavailable — the mesh page shows a message; mesh **export** still works |
| Ghidra hunt fails | Run **Check Setup** in the dialog; install Ghidra 11.x + Java 21 and set `GHIDRA_HOME` |
| `FNamePool anchor not found (is this a UE5 game?)` | The process is not a UE5 game, or you picked the wrong one — see §6.4 |
| `OpenProcess failed (run as admin)` | The game blocks memory access — start DualForge as administrator |
| USMAP dump found no game / wrong game | Start the game first, then use **Tools ▸ Generate USMAP**, or `usmap dump --list-processes` to find the exact executable name |
| Packages still fail to export after dumping | The usmap must match the game version — re-dump after a game update |
| Where did my files go? | The output folder you chose + `_dualforge_manifest.json` listing every file |
| Where are settings/keys stored? | `~/.dualforge/` — `settings.json`, `keys.json`, `preview_cache/` |

---

## 9. Build your own copy (from source)

Requires **Python 3.10+** and **Git**.

```powershell
git clone <this-repo> DualForge
cd DualForge
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .        # core
pip install -e ".[keys]"    # optional: community key sync
pip install -e ".[snappy]"  # optional: snappy codec
python main.py          # run from source
```

Run the test suite any time:

```powershell
python -m pytest tests -q
```

### Build the Windows exe

```powershell
.\.venv\Scripts\activate
.\scripts\build.ps1
```

The build lands in `dist\`. **Run `dist\DualForge.exe`** (keep `_internal` beside it).
The script deletes the confusing intermediate `build\dualforge\DualForge.exe` and
prints the exact file to run. Oodle DLLs and CLI helpers are never bundled — drop
`oo2core_*.dll` next to the exe (or into `~/.dualforge`) if a game needs it.

---

## 10. Command-line quick reference

```powershell
python main.py detect "game\Content\Paks\pakchunk0-Windows.pak"   # identify a file
python main.py extract "game_Data\sharedassets0.assets" -o out     # extract everything
python main.py extract "game_Data\sharedassets0.assets" -o out --format jpg
python main.py extract "game_Data\sharedassets0.assets" -o out --types Mesh AnimationClip --format fbx
python main.py extract "game_Data\sharedassets0.assets" -o out --types Cubemap VideoClip SpriteAtlas
python main.py extract "game_Data\sharedassets0.assets" -o out --types AnimatorController Avatar LightmapData
python main.py extract "game\Content\Paks\pakchunk0-Windows.utoc" -o out --usmap "C:\mappings\game.usmap"
python main.py keys add "My Game" 0123...64hex...abc
python main.py keys add "My Game" 0x... --scheme delta-force --guid abc --param xor_key=1122334455667788
python main.py keys list
python main.py keys schemes                    # list supported schemes / game presets
python main.py keys test "game.pak" --title "My Game"        # verify a stored key+scheme
python main.py keys test "game.pak" --aes 0x... --scheme aes-256
python main.py keys import "C:\FModel\Output\Global.AESKeys.json"
python main.py keys sync
python main.py codecs

# USMAP tools (Unreal Engine games only)
python main.py usmap dump --list-processes                 # list running processes
python main.py usmap dump --process "Game-Win64-Shipping.exe" -o "%USERPROFILE%\.dualforge\Game.usmap"
python main.py usmap validate "game.usmap"                 # inspect a usmap file
python main.py usmap repack "game.usmap" -o small.usmap --compression zstd

# USD world export (Unity mesh aggregate)
python main.py world "game_Data\sharedassets0.assets" -o out\world.usd

# IL2CPP metadata
python main.py il2cpp inspect "game_Data\il2cpp_data\Metadata\global-metadata.dat"
python main.py il2cpp strings "global-metadata.dat" -o strings.txt

# Write-back / repack an edited asset into a new archive
python main.py repack texture "sharedassets0.assets" "textures/hero_0" "hero.png" -o repacked
python main.py repack text   "sharedassets0.assets" "assets/script"    "script.cs"  -o repacked
python main.py repack font   "sharedassets0.assets" "fonts/title"      "title.ttf"   -o repacked

# Unreal .locres localization dump
python main.py locres dump "game\Content\Localization\Game\Game.locres" -o game.json
```
