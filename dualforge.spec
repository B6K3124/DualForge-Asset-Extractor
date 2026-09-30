# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for DualForge.

Build with:
    pyinstaller dualforge.spec --noconfirm --clean
or via scripts/build.ps1
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

# cryptography is imported lazily (inside the encryption scheme functions) so it
# is invisible to PyInstaller's static import graph. collect_all bundles its
# Python modules plus the platform C/DLL backends it needs at runtime.
_crypt_datas, _crypt_binaries, _crypt_hidden = collect_all("cryptography")

hiddenimports = [
    "PIL._tkinter_finder",
]
hiddenimports += ["cryptography"]
hiddenimports += _crypt_hidden
hiddenimports += collect_submodules("dualforge")
hiddenimports += collect_submodules("UnityPy")
hiddenimports += collect_submodules("dualforge.vendor.pyuepak")
hiddenimports += collect_submodules("py7zr")
hiddenimports += ["requests"]

# The Ghidra key-hunt script (distributed as a file, loaded by path at runtime)
# imports ghidra_bridge and jfx_bridge and reads their .py source off disk to
# stage the Jython bridge. That only works if they exist as real files whose
# __file__ resolves to the filesystem. We therefore ship them as DATA (under
# _internal, which is sys._MEIPASS and on sys.path in frozen mode) rather than
# freezing them into the PYZ archive. They must NOT be added to hiddenimports,
# or the PYZ copy would shadow the filesystem copy.
_datas_ghidra = collect_data_files("ghidra_bridge", include_py_files=True)
_datas_ghidra += collect_data_files("jfx_bridge", include_py_files=True)

# The Ghidra key-hunt scripts are loaded by path (never imported), so bundle
# them as data so the Tools > Ghidra Key Hunt UI works in frozen builds.
datas = collect_data_files("dualforge")
datas += _crypt_datas
datas += _datas_ghidra
datas += [
    ("scripts/ghidra/ghidra_key_finder.py", "dualforge/ghidra"),
    ("scripts/ghidra/ghidra_key_finder_server.py", "dualforge/ghidra"),
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=_crypt_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DualForge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DualForge",
)