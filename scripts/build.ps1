param(
    [string]$OutDir = ".\dist\DualForge"
)

$ErrorActionPreference = "Stop"

# Builds a standalone Windows build of DualForge with PyInstaller.
# Usage:  .\scripts\build.ps1  [-OutDir .\dist]

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "Installing PyInstaller..."
    python -m pip install pyinstaller
}

python -m pip install -e .

# Policy guard: the Oodle DLL must never be bundled or downloaded.
# The reader (dualforge/compression/oodle.py) performs no network access and
# loads lazily, so no module-graph stubbing is needed any more - the old
# pyuepak oodle.py swap existed only because upstream downloaded the DLL at
# import time. We assert the property rather than assume it.
Write-Host "Building DualForge..."
python -m PyInstaller dualforge.spec --noconfirm --clean

# Policy guard: verify the build output contains no Oodle DLL.
$bundled = Get-ChildItem -Path "dist\DualForge" -Recurse -Filter "oo2core*" -ErrorAction SilentlyContinue
if ($bundled) {
    Write-Host "ERROR: Oodle DLL was bundled into the build - aborting." -ForegroundColor Red
    exit 1
}

# The canonical PyInstaller onedir output lives at dist\DualForge\ and contains
# DualForge.exe + the _internal runtime beside it. This is the runnable artifact.
$built = Join-Path $root "dist\DualForge"
if (-not (Test-Path (Join-Path $built "DualForge.exe")) -or -not (Test-Path (Join-Path $built "_internal"))) {
    Write-Host "ERROR: build output is incomplete (expected DualForge.exe + _internal in dist\DualForge)." -ForegroundColor Red
    exit 1
}

# Remove any leftover flattened copies at the dist root so there is exactly one
# authoritative layout (the onedir). A bare DualForge.exe without _internal is
# non-runnable and confuses users - drop it.
$bare = Join-Path $root "dist\DualForge.exe"
if (Test-Path $bare) { Remove-Item $bare -Force }
$flatInternal = Join-Path $root "dist\_internal"
if (Test-Path $flatInternal) { Remove-Item $flatInternal -Recurse -Force }

# -OutDir lets you relocate the runnable app folder. It is the directory that
# should contain DualForge.exe + _internal directly. By default it is the
# canonical onedir (dist\DualForge), which is left in place.
if (-not [System.IO.Path]::IsPathRooted($OutDir)) {
    $OutDir = Join-Path $root $OutDir
}
$canonical = (Resolve-Path $built).Path
$target = (Resolve-Path $OutDir -ErrorAction SilentlyContinue).Path
if (-not $target -or $target -ne $canonical) {
    if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    Copy-Item -Path "$built\*" -Destination $OutDir -Recurse -Force
    $built = $OutDir
}

Write-Host ""
Write-Host "Build complete. Run the onedir app:"
Write-Host "  $built\DualForge.exe"
Write-Host "Keep DualForge.exe and the '_internal' folder together - both live in $built."
Write-Host "To install, run: .\scripts\install.ps1  (installs from $built)"
Write-Host ""
Write-Host "Oodle DLLs and CUE4Parse/vgmstream CLIs are never bundled -"
Write-Host "place oo2core_*.dll next to the exe (or in ~/.dualforge) as needed."