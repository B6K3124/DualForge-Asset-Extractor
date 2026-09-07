"""Game driver: a portable, JSON-serializable config for handling a specific game.

A driver bundles engine type, encryption pipeline, export format defaults,
detection patterns, and CLI hints into a single object that can be saved to
disk, shared, and auto-applied during extraction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

DRIVER_VERSION = "1.0"
DRIVER_FILE_SUFFIX = ".dualforge-driver.json"
DRIVER_MAGIC = "dualforge_driver"


@dataclass
class GameDriver:
    """Full game configuration plugin."""

    # ── identity ──────────────────────────────────────────────────────
    name: str
    label: str
    version: str = DRIVER_VERSION

    # ── engine ────────────────────────────────────────────────────────
    engine: str = "auto"  # "unity" | "unreal" | "bethesda" | "cdpr" | "auto"

    # ── detection ─────────────────────────────────────────────────────
    game_fragments: List[str] = field(default_factory=list)
    archive_patterns: List[str] = field(default_factory=list)

    # ── encryption ────────────────────────────────────────────────────
    encryption_scheme: str = "aes-256"
    encryption_params: Dict[str, str] = field(default_factory=dict)

    # ── unreal-specific ───────────────────────────────────────────────
    egame: str = ""
    usmap_required: bool = False

    # ── unity-specific ────────────────────────────────────────────────
    unity_cn: bool = False

    # ── export defaults ───────────────────────────────────────────────
    export_formats: Dict[str, str] = field(default_factory=dict)
    asset_filter: List[str] = field(default_factory=list)

    # ── CLI hints ─────────────────────────────────────────────────────
    cli_args: Dict[str, str] = field(default_factory=dict)

    # ── metadata ──────────────────────────────────────────────────────
    author: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)

    # ── serialization ─────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data[DRIVER_MAGIC] = DRIVER_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> GameDriver:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {}
        for key, value in data.items():
            if key == DRIVER_MAGIC or key not in known:
                continue
            kwargs[key] = value
        return cls(**kwargs)

    def to_json(self, indent: int = 2) -> str:
        import json

        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> GameDriver:
        import json

        return cls.from_dict(json.loads(text))

    def save(self, path: Optional[str] = None) -> str:
        """Write this driver to a JSON file. Returns the written path."""
        if path is None:
            from dualforge.drivers.registry import default_drivers_dir

            path = str(default_drivers_dir() / f"{self.name}{DRIVER_FILE_SUFFIX}")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return str(target)

    @classmethod
    def load(cls, path: str) -> GameDriver:
        """Read a driver from a JSON file."""
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_json(text)

    def matches(self, archive_path: str, mount: str = "") -> float:
        """Score how well this driver matches a given archive.

        Returns a score >= 0 (higher is better) or 0.0 if no match.
        """
        text = " ".join(filter(None, [archive_path, mount])).lower()
        score = 0.0
        for frag in self.game_fragments:
            if frag.lower() in text:
                score += 100.0
        if self.archive_patterns:
            from fnmatch import fnmatch

            basename = Path(archive_path).name
            for pattern in self.archive_patterns:
                if fnmatch(basename, pattern):
                    score += 50.0
                    break
        if self.engine != "auto":
            if self.engine == "unreal" and any(
                text.endswith(ext) for ext in (".pak", ".utoc", ".ucas")
            ):
                score += 10.0
            elif self.engine == "unity" and any(
                text.endswith(ext) for ext in (".unity3d", ".bundle", ".assetbundle", ".assets")
            ):
                score += 10.0
            elif self.engine == "bethesda" and any(
                text.endswith(ext) for ext in (".bsa", ".ba2")
            ):
                score += 10.0
            elif self.engine == "cdpr" and text.endswith(".archive"):
                score += 10.0
        return score


__all__ = ["GameDriver", "DRIVER_VERSION", "DRIVER_FILE_SUFFIX"]
