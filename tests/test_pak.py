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
        ("V1", "UE 4.0"),
        ("V2", "UE 4.1"),
        ("V3", "UE 4.2"),
        ("V4", "UE 4.3"),
        ("V5", "UE 4.4"),
        ("V6", "UE 4.5"),
        ("V7", "UE 4.6"),
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


@pytest.mark.parametrize("version_name", ["V1", "V2", "V3"])
def test_pre_v4_footer_is_not_marked_encrypted(
    tmp_path: Path, PakFile, version_name: str
):
    """V1-V3 footers have no key GUID or encryption byte.

    Regression: the reader always read 17 bytes before the magic, so these
    versions interpreted pak data as a key GUID/flag and could randomly mark an
    unencrypted archive as encrypted (sending it down the AES path).
    """
    from dualforge.vendor.pyuepak import version as ver

    pak = PakFile()
    pak.set_version(getattr(ver.PakVersion, version_name))
    pak.add_file("Game/x.bin", b"legacy payload")
    path = str(tmp_path / f"{version_name}.pak")
    pak.write(path)

    archive = PakArchive(path)
    assert archive._pak._footer.is_encrypted is False
    assert archive.read_file("Game/x.bin") == b"legacy payload"


#: Compressible, non-trivial payload: a single repeated block compresses well
#: and is large enough that a "compressed" flag without real compression is
#: obvious when read back.
_COMPRESSIBLE = (b"dualforge-compression-roundtrip-" * 40) + bytes(range(256))


@pytest.mark.parametrize("method", ["Zlib", "Gzip", "Lz4", "Zstd", "Brotli"])
def test_compressed_add_file_roundtrips(tmp_path: Path, PakFile, method: str):
    """``add_file(..., compression=...)`` must actually compress.

    Regression: ``_compress_payload`` used to gate on ``compressio_name``, which
    ``PakFile.write`` only assigns after the entry was serialised, so the writer
    emitted raw bytes under a compressed flag and the read-back failed.
    """
    from dualforge.compression import is_available
    from dualforge.vendor.pyuepak import version

    if not is_available(method.lower()):
        pytest.skip(f"{method} codec not installed")

    pak = PakFile()
    pak.set_version(version.PakVersion.V11)
    pak.add_file("Game/Content/Data/blob.bin", _COMPRESSIBLE, compression=method)
    path = str(tmp_path / f"{method.lower()}.pak")
    pak.write(path)

    archive = PakArchive(path)
    assert archive.read_file("Game/Content/Data/blob.bin") == _COMPRESSIBLE


def test_compressed_entry_is_smaller_on_disk(tmp_path: Path, PakFile):
    """The compressed payload must be smaller than the source for compressible
    data, proving the bytes were really run through a codec."""
    from dualforge.compression import is_available
    from dualforge.vendor.pyuepak import version

    if not is_available("zstd"):
        pytest.skip("zstandard not installed")

    plain = str(tmp_path / "plain.pak")
    pak = PakFile()
    pak.set_version(version.PakVersion.V11)
    pak.add_file("blob.bin", _COMPRESSIBLE)
    pak.write(plain)

    packed = str(tmp_path / "packed.pak")
    pak = PakFile()
    pak.set_version(version.PakVersion.V11)
    pak.add_file("blob.bin", _COMPRESSIBLE, compression="Zstd")
    pak.write(packed)

    assert Path(packed).stat().st_size < Path(plain).stat().st_size


def test_flat_index_rejects_huge_counts():
    """UE 6.0 flat-index counts are attacker-controlled and must be bounded
    against the remaining buffer before they size an allocation."""
    import struct

    from dualforge.vendor.pyuepak.file_io import Reader
    from dualforge.vendor.pyuepak.index import (
        FLAT_DIRECTORY_INDEX_MAGIC,
        read_flat_directory_index,
    )

    header = struct.pack(
        "<iiiiii",
        0,          # num_dirs
        1 << 30,    # num_files: absurd for a 4-byte buffer
        1,          # restart_interval
        0,          # dir_blob_bytes
        0,          # file_blob_bytes
        0,          # num_path_hashes
    )
    reader = Reader(header + b"\x00\x00\x00\x00")
    with pytest.raises(ValueError, match="remaining"):
        read_flat_directory_index(reader, FLAT_DIRECTORY_INDEX_MAGIC)


def test_decrypt_ctr_counter_layout():
    """UE AES-CTR must use a 12-byte IV prefix + big-endian block counter.

    Cross-checked against an explicit AES-ECB keystream, so a change to the
    counter layout (little-endian, 16-byte IV, etc.) fails here even without
    the CUE4Parse fixtures present.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from dualforge.vendor.pyuepak.entry import decrypt_ctr

    key = bytes(range(32))
    iv = bytes(range(12))
    data = bytes(range(64))

    encryptor = Cipher(
        algorithms.AES(key), modes.ECB(), backend=default_backend()
    ).encryptor()
    keystream = bytearray()
    for block_index in range(len(data) // 16):
        keystream += encryptor.update(iv + block_index.to_bytes(4, "big"))
    expected = bytes(a ^ b for a, b in zip(data, keystream, strict=True))

    assert decrypt_ctr(key, data, iv) == expected
    # Starting at block 1 must line up with the same keystream offset.
    assert decrypt_ctr(key, data[16:48], iv, block_index=1) == expected[16:48]


def test_decrypt_ctr_rejects_bad_iv():
    from dualforge.vendor.pyuepak.entry import decrypt_ctr

    with pytest.raises(ValueError, match="12 bytes"):
        decrypt_ctr(bytes(range(32)), b"x" * 16, b"short")