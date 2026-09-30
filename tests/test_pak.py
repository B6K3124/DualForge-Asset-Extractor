from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from dualforge.unreal.pak import PakArchive, PakError, _import_pyuepak

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("dualforge.vendor.pyuepak") is None,
    reason="vendored pyuepak not importable",
)


@pytest.fixture(scope="module")
def PakFile():
    return _import_pyuepak()


@pytest.fixture()
def pak_path(tmp_path: Path, PakFile) -> str:
    """Write a small, unencrypted pak fixture using pyuepak itself."""
    from dualforge.vendor.pyuepak import version

    target = str(tmp_path / "test.pak")
    pak = PakFile()
    pak.set_version(version.PakVersion.V10)
    pak.add_file("Game/Content/Maps/TestMap.umap", b"umap payload")
    pak.add_file("Game/Content/Textures/Tex.png", b"\x89PNG fake texture bytes")
    pak.add_file("Game/Content/Audio/sound.wav", b"RIFF....WAVEfake")
    pak.write(target)
    return target


def test_import_does_not_download_oodle(PakFile):
    import sys

    module = sys.modules["dualforge.vendor.pyuepak.oodle"]
    stub = module.oodle()
    assert hasattr(stub, "decompress")


def test_list_files(pak_path: str):
    archive = PakArchive(pak_path)
    files = archive.list_files()
    assert "Game/Content/Maps/TestMap.umap" in files
    assert len(files) == 3


def test_read_file_normalizes_leading_slash(pak_path: str):
    archive = PakArchive(pak_path)
    assert archive.read_file("/Game/Content/Textures/Tex.png") == b"\x89PNG fake texture bytes"
    assert archive.read_file("Game/Content/Textures/Tex.png") == b"\x89PNG fake texture bytes"


def test_size_of(pak_path: str):
    archive = PakArchive(pak_path)
    assert archive.size_of("Game/Content/Audio/sound.wav") == len(b"RIFF....WAVEfake")
    assert archive.size_of("missing/file.bin") == 0


def test_read_missing_raises(pak_path: str):
    archive = PakArchive(pak_path)
    with pytest.raises(PakError):
        archive.read_file("nope/nothere.bin")


def test_extract_file(pak_path: str, tmp_path: Path):
    archive = PakArchive(pak_path)
    out = str(tmp_path / "out")
    written = archive.extract_file("Game/Content/Textures/Tex.png", out)
    assert Path(written).read_bytes() == b"\x89PNG fake texture bytes"
    assert "Game" in Path(written).as_posix()


def test_open_garbage_raises(tmp_path: Path):
    garbage = str(tmp_path / "garbage.pak")
    Path(garbage).write_bytes(b"this is not a pak archive")
    with pytest.raises(PakError):
        PakArchive(garbage)


@pytest.mark.parametrize(
    "version_name,label",
    [
        ("V8B", "UE 4.17-4.21"),
        ("V9", "UE 4.22-4.25"),
        ("V10", "UE 4.26-4.27"),
        ("V11", "UE 5.0-5.3"),
        ("V12", "UE 5.4-5.8"),
    ],
)
def test_pak_version_matrix(tmp_path: Path, PakFile, version_name: str, label: str):
    """Every engine-era pak version must write AND read back natively."""
    from dualforge.vendor.pyuepak import version

    pak = PakFile()
    pak.set_version(getattr(version.PakVersion, version_name))
    payload = f"payload-{label}".encode()
    pak.add_file(f"Game/Content/{version_name}/Asset.bin", payload)
    path = str(tmp_path / f"{version_name}.pak")
    pak.write(path)

    archive = PakArchive(path)
    assert archive.read_file(f"Game/Content/{version_name}/Asset.bin") == payload
    assert archive.version > 0