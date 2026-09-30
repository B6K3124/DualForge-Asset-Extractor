"""Cross-check the vendored pak reader against CUE4Parse's own test fixtures.

The synthetic paks in test_pak.py are written by the same code that reads them,
so they cannot catch a shared misunderstanding of the pak format. The fixtures
under external/uex/external/CUE4Parse/CUE4Parse.Tests/Fixtures are real
archives produced by Epic's cooker, and CUE4Parse's C# source is vendored
alongside them - so this suite is a genuine external oracle.

The fixture AES key is the one CUE4Parse's own tests submit
(FixtureTestUtilities.cs:36, ASCII "CUE4ParseFixtureAESKey0123456789").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dualforge.unreal.pak import PakArchive, PakError

FIXTURES = Path("external/uex/external/CUE4Parse/CUE4Parse.Tests/Fixtures")
FIXTURE_AES_KEY = b"CUE4ParseFixtureAESKey0123456789"

pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir(),
    reason="CUE4Parse fixtures not present (external/uex submodule)",
)


def _paks(engine: str) -> list[Path]:
    return sorted((FIXTURES / engine).rglob("*.pak"))


def _rel(path: Path) -> str:
    return str(path.relative_to(FIXTURES)).replace("\\", "/")


@pytest.mark.parametrize("engine", ["UE5_8", "UE6_0"])
def test_unencrypted_paks_open_and_extract(engine: str) -> None:
    """Uncompressed/Zlib paks must list and return real bytes.

    Zlib entries are the important half: an entry that declared a compression
    method the reader ignored would still "succeed" while returning compressed
    bytes, so the payload is compared against the matching uncompressed fixture.
    """
    opened = 0
    for pak in _paks(engine):
        if "Encrypted" in _rel(pak):
            continue
        archive = PakArchive(str(pak))
        files = archive.list_files()
        assert files, f"{_rel(pak)} listed no files"
        for name in files:
            data = archive.read_file(name)
            assert isinstance(data, bytes)
        opened += 1
    assert opened >= 4, f"expected several {engine} fixtures, got {opened}"


@pytest.mark.parametrize("engine", ["UE5_8", "UE6_0"])
def test_zlib_matches_uncompressed_twin(engine: str) -> None:
    """The Zlib fixture must decode to byte-identical content as Uncompressed.

    CUE4Parse ships these as a matched pair, which is what makes this a real
    check on the compression dispatch rather than a smoke test.
    """
    pairs = [
        ("Pak", "CUE4ParseFixtures-Minimal-{kind}.pak"),
        ("LegacyPak/Unversioned", "CUE4ParseFixtures-Legacy-Unversioned-{kind}.pak"),
        ("LegacyPak/Tagged", "CUE4ParseFixtures-Legacy-Tagged-{kind}.pak"),
    ]
    compared = 0
    for subdir, template in pairs:
        zlib_pak = FIXTURES / engine / subdir / "Zlib" / template.format(kind="Zlib")
        plain_pak = FIXTURES / engine / subdir / "Uncompressed" / template.format(
            kind="Uncompressed"
        )
        if not (zlib_pak.is_file() and plain_pak.is_file()):
            continue
        z = PakArchive(str(zlib_pak))
        p = PakArchive(str(plain_pak))
        z_files, p_files = z.list_files(), p.list_files()
        assert z_files == p_files, f"{subdir}: file lists differ"
        for name in z_files:
            assert z.read_file(name) == p.read_file(name), f"{subdir}: {name} differs"
        compared += 1
    assert compared >= 1, f"no matched Zlib/Uncompressed pair found for {engine}"


@pytest.mark.parametrize("engine", ["UE5_8", "UE6_0"])
def test_oodle_matches_uncompressed_twin(engine: str) -> None:
    """Oodle-compressed entries must decode to the same bytes as uncompressed."""
    oodle_pak = FIXTURES / engine / "Pak" / "Oodle" / "CUE4ParseFixtures-Minimal-Oodle.pak"
    plain_pak = (
        FIXTURES / engine / "Pak" / "Uncompressed" / "CUE4ParseFixtures-Minimal-Uncompressed.pak"
    )
    if not (oodle_pak.is_file() and plain_pak.is_file()):
        pytest.skip(f"no Oodle/Uncompressed pair for {engine}")
    o = PakArchive(str(oodle_pak))
    p = PakArchive(str(plain_pak))
    assert o.list_files() == p.list_files()
    for name in o.list_files():
        assert o.read_file(name) == p.read_file(name), f"{name} differs"


def test_footer_declares_compression_names() -> None:
    """The footer table must resolve names to methods, not leave them unknown."""
    from dualforge.vendor.pyuepak import PakFile
    from dualforge.vendor.pyuepak.utils import COMPRESSION, UNSUPPORTED_MARKER

    oodle_pak = FIXTURES / "UE5_8" / "Pak" / "Oodle" / "CUE4ParseFixtures-Minimal-Oodle.pak"
    if not oodle_pak.is_file():
        pytest.skip("Oodle fixture not present")

    pak = PakFile()
    pak.read(str(oodle_pak))
    names = pak._footer.compression_names
    methods = pak._footer.compresion
    assert "Oodle" in names, names
    # Slot 0 is an implicit None; the table lists only additional methods.
    assert methods[0] is COMPRESSION.NONE
    assert COMPRESSION.OODLE in methods
    assert UNSUPPORTED_MARKER not in methods, f"unresolved name in {names}"


def test_encrypted_pak_reports_encryption_not_a_crash() -> None:
    """An encrypted pak without a key must say so, not fail obscurely."""
    paks = [p for p in FIXTURES.rglob("*.pak") if "Encrypted" in _rel(p)]
    if not paks:
        pytest.skip("no encrypted fixtures present")
    with pytest.raises(PakError) as excinfo:
        PakArchive(str(paks[0]))
    message = str(excinfo.value).lower()
    assert "encrypted" in message or "key" in message, message


def test_ue50_encrypted_pak_opens_with_fixture_key() -> None:
    """The UE 5.8 encrypted fixture must decrypt with CUE4Parse's test key."""
    paks = [
        p
        for p in _paks("UE5_8")
        if "Encrypted" in _rel(p) and "LegacyPak/Unversioned" in _rel(p)
    ]
    if not paks:
        pytest.skip("no UE 5.8 encrypted fixture present")

    from dualforge.vendor.pyuepak import PakFile

    pak = PakFile()
    pak.set_key(FIXTURE_AES_KEY)
    pak.read(str(paks[0]))
    assert pak.count > 0
    name = pak.list_files()[0]
    assert len(pak.read_file(name)) > 0


def test_not_a_pak_is_rejected() -> None:
    """A non-pak file must fail with a clear error, not a struct crash."""
    not_a_pak = FIXTURES / "UE5_8" / "IoStore" / "Tagged" / "global.ucas"
    if not not_a_pak.is_file():
        pytest.skip("ucas fixture not present")
    with pytest.raises(PakError) as excinfo:
        PakArchive(str(not_a_pak))
    assert "pak" in str(excinfo.value).lower()
