"""Driver registry: load, save, match, import/export game drivers.

The registry maintains an in-memory collection of ``GameDriver`` instances.
Built-in drivers are loaded at startup; user drivers are loaded from
``~/.dualforge/drivers/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from dualforge.drivers.driver import DRIVER_FILE_SUFFIX, GameDriver

DEFAULT_DRIVERS_DIR = Path.home() / ".dualforge" / "drivers"


def default_drivers_dir() -> Path:
    return DEFAULT_DRIVERS_DIR


class DriverRegistry:
    """Central registry for game drivers."""

    def __init__(self) -> None:
        self._drivers: Dict[str, GameDriver] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load_defaults()
            self._load_user_dir()

    # ── built-in loading ──────────────────────────────────────────────

    def _load_defaults(self) -> None:
        from dualforge.drivers.defaults import BUILTIN_DRIVERS

        for driver in BUILTIN_DRIVERS:
            self._drivers[driver.name] = driver

    def _load_user_dir(self) -> None:
        d = default_drivers_dir()
        if not d.is_dir():
            return
        for path in sorted(d.glob(f"*{DRIVER_FILE_SUFFIX}")):
            try:
                driver = GameDriver.load(str(path))
                if driver.name not in self._drivers:
                    self._drivers[driver.name] = driver
            except Exception:
                continue

    # ── public API ────────────────────────────────────────────────────

    def register(self, driver: GameDriver) -> None:
        """Register a driver (overwrites if name already exists)."""
        self._ensure_loaded()
        self._drivers[driver.name] = driver

    def get(self, name: str) -> Optional[GameDriver]:
        self._ensure_loaded()
        return self._drivers.get(name)

    def list(self) -> List[GameDriver]:
        self._ensure_loaded()
        return list(self._drivers.values())

    def names(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self._drivers)

    def remove(self, name: str) -> bool:
        self._ensure_loaded()
        if name in self._drivers:
            del self._drivers[name]
            path = default_drivers_dir() / f"{name}{DRIVER_FILE_SUFFIX}"
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return True
        return False

    def match(
        self,
        archive_path: str,
        mount: str = "",
        engine: Optional[str] = None,
    ) -> Optional[GameDriver]:
        """Find the best-scoring driver for an archive.

        Optionally filters by engine type. Returns the highest-scoring
        driver or None if nothing scores above zero.
        """
        self._ensure_loaded()
        best: Optional[GameDriver] = None
        best_score = 0.0
        best_is_generic = False
        best_hits = (0, 0)
        text = archive_path.lower()
        for driver in self._drivers.values():
            if engine and driver.engine not in (engine, "auto"):
                continue
            score = driver.matches(archive_path, mount)
            if score <= 0:
                continue
            # A "generic" driver (no game fragments and no archive patterns) is
            # only ever scored on the weak engine-baseline, so it represents
            # "unknown game" rather than a specific title.  When a specific
            # driver also only reaches that baseline (i.e. no fragment or
            # pattern actually matched), it is an ambiguous tie — prefer the
            # generic driver so we never mislabel an unknown game as a known one.
            is_generic = (
                not driver.game_fragments
                and not driver.archive_patterns
                and driver.engine not in ("auto", "")
            )
            # For specific drivers that tie (e.g. "new vegas" vs "fallout" on a
            # Fallout: New Vegas archive), prefer the one whose fragments hit
            # the path more and with the longest distinguishing match, so a
            # sub-title driver beats its umbrella driver.
            matched = [fragment for fragment in driver.game_fragments if fragment.lower() in text]
            hits = (len(matched), max((len(fragment) for fragment in matched), default=0))
            if (
                best is None
                or score > best_score
                or (
                    score == best_score
                    and not is_generic
                    and not best_is_generic
                    and hits > best_hits
                )
                or (score == best_score and is_generic and not best_is_generic)
            ):
                best_score = score
                best = driver
                best_is_generic = is_generic
                best_hits = hits
        return best if best_score > 0 else None

    # ── file I/O ──────────────────────────────────────────────────────

    def save(self, driver: GameDriver, path: Optional[str] = None) -> str:
        """Export a driver to disk. Returns the written path."""
        self._ensure_loaded()
        self._drivers[driver.name] = driver
        return driver.save(path)

    def load_file(self, path: str) -> GameDriver:
        """Read a driver from a JSON file and register it."""
        self._ensure_loaded()
        driver = GameDriver.load(path)
        self._drivers[driver.name] = driver
        return driver

    def load_dir(self, dir_path: str) -> int:
        """Bulk-load all driver files from a directory. Returns count."""
        self._ensure_loaded()
        count = 0
        d = Path(dir_path)
        if not d.is_dir():
            return count
        for path in sorted(d.glob(f"*{DRIVER_FILE_SUFFIX}")):
            try:
                driver = GameDriver.load(str(path))
                self._drivers[driver.name] = driver
                count += 1
            except Exception:
                continue
        return count

    def export_all(self, out_dir: str) -> int:
        """Export all registered drivers to a directory. Returns count."""
        self._ensure_loaded()
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        count = 0
        for driver in self._drivers.values():
            path = target / f"{driver.name}{DRIVER_FILE_SUFFIX}"
            try:
                path.write_text(driver.to_json(), encoding="utf-8")
                count += 1
            except OSError:
                continue
        return count

    def export_builtin(self, out_dir: str) -> int:
        """Export only built-in drivers to a directory. Returns count."""
        self._ensure_loaded()
        from dualforge.drivers.defaults import BUILTIN_DRIVERS

        builtin_names = {d.name for d in BUILTIN_DRIVERS}
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        count = 0
        for driver in self._drivers.values():
            if driver.name in builtin_names:
                path = target / f"{driver.name}{DRIVER_FILE_SUFFIX}"
                try:
                    path.write_text(driver.to_json(), encoding="utf-8")
                    count += 1
                except OSError:
                    continue
        return count

    def reload(self) -> int:
        """Clear and reload all drivers from disk. Returns count."""
        self._drivers.clear()
        self._loaded = True
        self._load_defaults()
        self._load_user_dir()
        return len(self._drivers)


# Module-level singleton
registry = DriverRegistry()

__all__ = ["DriverRegistry", "registry", "default_drivers_dir"]
