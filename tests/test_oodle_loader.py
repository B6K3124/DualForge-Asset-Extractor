"""Tests for the Oodle loader: one loader, and it must not search attacker-writable places.

DualForge's normal workflow is "open the archive someone sent me", so the Oodle
DLL search is a code-execution surface: anything that can write a file into a
searched directory could get loaded into this process. These tests pin the
exclusions and the allocation ceiling.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from dualforge.compression import oodle as oodle_mod
from dualforge.compression.oodle import (
    Oodle,
    OodleDecompressError,
    OodleUnavailableError,
    find_oodle_dll,
    set_search_root,
)
from dualforge.vendor.pyuepak import oodle as vendored_oodle


@pytest.fixture(autouse=True)
def _clear_context():
    set_search_root(None)
    yield
    set_search_root(None)


def _write_fake_dll(directory: Path, name: str = "oo2core_9_win64.dll") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    # A real PE is not needed: nothing here loads it, we only assert on search.
    path.write_bytes(b"MZ" + b"\x00" * 64)
    return path


class _FakeLib:
    """Stand-in for a loaded oo2core DLL, exposing only the symbol we probe."""

    OodleLZ_Decompress = object()


# --------------------------------------------------------------- single loader


def test_vendored_module_delegates_to_the_one_loader(monkeypatch):
    """The vendored reader must not carry its own loader.

    It previously had a stricter search and delegated to a more permissive
    implementation, which left the permissive path reachable.
    """
    assert vendored_oodle.find_game_oodle is oodle_mod.find_oodle_dll
    assert not hasattr(vendored_oodle, "_candidate_dirs"), (
        "vendored module still defines its own search path list"
    )
    assert not hasattr(vendored_oodle, "_GAME_SUBDIRS"), (
        "vendored module still defines its own sub-path list"
    )
    assert not hasattr(vendored_oodle, "_ctx"), (
        "vendored module still keeps its own search context"
    )


def test_vendored_resolver_agrees_with_canonical(monkeypatch, tmp_path):
    """Both entry points must resolve the same DLL for the same context."""
    game = tmp_path / "Game" / "Content" / "Paks"
    pak = game / "pakchunk0.pak"
    pak.parent.mkdir(parents=True)
    pak.write_bytes(b"")
    expected = _write_fake_dll(tmp_path / "Game" / "Binaries" / "Win64")

    set_search_root(str(pak))
    assert oodle_mod.find_oodle_dll() == expected
    assert vendored_oodle.find_game_oodle() == expected


def test_archive_context_is_shared_state(monkeypatch, tmp_path):
    """Setting the context through the vendored name must be visible to the
    canonical loader, since both entries are documented as one behaviour."""
    expected = _write_fake_dll(tmp_path / "Game" / "Binaries" / "Win64")
    pak = tmp_path / "Game" / "Content" / "Paks" / "pakchunk0.pak"
    pak.parent.mkdir(parents=True)
    pak.write_bytes(b"")
    monkeypatch.delenv("DUALFORGE_OODLE", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    vendored_oodle.set_archive_context(str(pak))
    assert oodle_mod.find_oodle_dll() == expected
    assert vendored_oodle.find_game_oodle() == expected
    vendored_oodle.set_archive_context(None)
    assert oodle_mod.find_oodle_dll() is None


# ------------------------------------------------------------------ exclusions


def test_does_not_search_cwd(tmp_path, monkeypatch):
    """The CWD is attacker-writable when opening a downloaded archive."""
    dll = _write_fake_dll(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DUALFORGE_OODLE", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    assert find_oodle_dll() is None, "Oodle DLL in CWD must not be picked up"
    assert dll.exists()


def test_does_not_search_path(tmp_path, monkeypatch):
    """A DLL dropped anywhere on PATH must not be loaded."""
    _write_fake_dll(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("DUALFORGE_OODLE", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    assert find_oodle_dll() is None, "Oodle DLL on PATH must not be picked up"


def test_finds_dll_in_home_config_dir(tmp_path, monkeypatch):
    """~/.dualforge remains a supported, explicit location."""
    expected = _write_fake_dll(tmp_path / ".dualforge")
    monkeypatch.delenv("DUALFORGE_OODLE", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert find_oodle_dll() == expected


def test_finds_dll_beside_game_binaries(tmp_path, monkeypatch):
    """The archive's own game folder chain is searched."""
    expected = _write_fake_dll(tmp_path / "Game" / "Binaries" / "Win64")
    pak = tmp_path / "Game" / "Content" / "Paks" / "pakchunk0.pak"
    pak.parent.mkdir(parents=True)
    pak.write_bytes(b"")
    monkeypatch.delenv("DUALFORGE_OODLE", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    set_search_root(str(pak))
    assert find_oodle_dll() == expected


def test_honours_dualforge_oodle_env_var(tmp_path, monkeypatch):
    dll = _write_fake_dll(tmp_path / "custom")
    monkeypatch.setenv("DUALFORGE_OODLE", str(dll))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    assert find_oodle_dll() == dll


def test_env_var_honours_renamed_dll(tmp_path, monkeypatch):
    """The error message tells users to point DUALFORGE_OODLE at a full path,
    and mod managers rename the DLL, so an explicit file must win even when it
    does not match the oo2core_* glob patterns."""
    renamed = _write_fake_dll(tmp_path / "mods", name="kraken-custom.dll")
    monkeypatch.setenv("DUALFORGE_OODLE", str(renamed))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    assert find_oodle_dll() == renamed


def test_env_var_pointing_at_a_directory_still_searches_it(tmp_path, monkeypatch):
    """A directory value keeps the glob behaviour, so both forms work."""
    expected = _write_fake_dll(tmp_path / "o" / "bin")
    monkeypatch.setenv("DUALFORGE_OODLE", str(tmp_path / "o" / "bin"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    assert find_oodle_dll() == expected


def test_explicit_dll_path_is_not_widened(tmp_path, monkeypatch):
    """An explicit path must not fall back to scanning other directories."""
    explicit = _write_fake_dll(tmp_path / "a")
    _write_fake_dll(tmp_path / "b")
    attempted: list[str] = []

    def fake_cdll(path):
        attempted.append(path)
        return _FakeLib()

    monkeypatch.setattr(oodle_mod.ctypes, "CDLL", fake_cdll)
    monkeypatch.setenv(
        "DUALFORGE_OODLE", str(tmp_path / "b" / "oo2core_9_win64.dll")
    )

    o = Oodle(dll_path=str(explicit))
    assert attempted == [str(explicit)], "search widened beyond the explicit path"
    assert o.path == str(explicit)


def test_missing_explicit_dll_path_raises(tmp_path, monkeypatch):
    """A wrong explicit path must fail loudly instead of searching."""
    _write_fake_dll(tmp_path / "sneaky")
    monkeypatch.setenv("DUALFORGE_OODLE", str(tmp_path / "sneaky" / "oo2core_9_win64.dll"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))

    with pytest.raises(OodleUnavailableError, match="does not exist"):
        Oodle(dll_path=str(tmp_path / "nope" / "oo2core_9_win64.dll"))


def test_search_root_is_thread_local(tmp_path):
    """Context is per-thread; a worker must not inherit another archive's root."""
    set_search_root(str(tmp_path / "a.pak"))
    import threading

    seen: list[str | None] = []

    def worker() -> None:
        seen.append(oodle_mod._SEARCH_ROOT.path)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen == [None]


# ----------------------------------------------------------------- allocation


def test_refuses_absurd_output_size(tmp_path, monkeypatch):
    """output_size comes from the archive and must be bounded before allocating."""
    o = object.__new__(Oodle)
    o.path = "fake"
    o._lib = ctypes.CDLL.__new__(ctypes.CDLL)  # never called; size check comes first

    with pytest.raises(OodleDecompressError, match="exceeds"):
        o.decompress(b"x" * 16, 4 * 1024 * 1024 * 1024)

    with pytest.raises(OodleDecompressError, match="invalid"):
        o.decompress(b"x" * 16, -1)


def test_max_output_size_is_documented_limit():
    assert oodle_mod._MAX_OUTPUT_SIZE == 2 * 1024 * 1024 * 1024


def test_empty_input_short_circuits(tmp_path):
    o = object.__new__(Oodle)
    o.path = None
    o._lib = None
    assert o.decompress(b"", 1024) == b""


def test_missing_dll_message_explains_policy(tmp_path, monkeypatch):
    monkeypatch.delenv("DUALFORGE_OODLE", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty-home"))
    monkeypatch.setattr(oodle_mod, "_candidate_dirs", lambda: [])

    with pytest.raises(OodleUnavailableError) as excinfo:
        Oodle()
    message = str(excinfo.value).lower()
    assert "never downloads" in message
    assert "dualforge_oodle" in message
