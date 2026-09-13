from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dualforge.encryption.registry import KeyMaterial
from dualforge.log import get_logger
import builtins

logger = get_logger(__name__)

DEFAULT_ENDPOINTS = [
    "https://fortnitecentral.ga/api/v1/aes",
    "https://aes.ue4server.com/",
]


DEFAULT_SCHEME = "aes-256"


def _fold(text: str) -> str:
    """Normalize a name for loose comparison: lowercase, drop non-alnum/spaces."""
    return "".join(ch.lower() for ch in text if ch.isalnum())


_MAIN_KEY_FIELDS = ("mainKey", "main_key", "aes_key", "key")
_DYNAMIC_KEY_FIELDS = ("dynamicKeys", "dynamic_keys")


def _main_key(value: dict) -> str:
    """Return the first non-empty main-key field found on ``value`` ("" if none)."""
    for key_name in _MAIN_KEY_FIELDS:
        candidate = value.get(key_name)
        if candidate:
            return str(candidate)
    return ""


def _dynamic_keys(value: dict) -> dict[str, str]:
    """Return the dynamic-key GUID map on ``value`` ({} if absent)."""
    raw = None
    for key_name in _DYNAMIC_KEY_FIELDS:
        if value.get(key_name):
            raw = value[key_name]
            break
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


@dataclass
class KeyEntry:
    title: str
    aes_key: str
    engine: str = "unreal"
    notes: str = ""
    updated: str = ""
    dynamic_keys: dict[str, str] = field(default_factory=dict)
    scheme: str = DEFAULT_SCHEME
    guid: str = ""
    parameters: dict[str, str] = field(default_factory=dict)

    def to_material(self) -> KeyMaterial:
        """Build a registry ``KeyMaterial`` for this entry for use in pipelines."""
        return KeyMaterial(
            key_str=self.aes_key,
            scheme=self.scheme,
            guid=self.guid,
            parameters=dict(self.parameters),
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> KeyEntry:
        title = str(data.get("title", ""))
        key = str(data.get("aes_key", data.get("key", "")) or "")
        scheme = str(data.get("scheme", DEFAULT_SCHEME) or DEFAULT_SCHEME)
        params = data.get("parameters")
        params = {str(k): str(v) for k, v in params.items()} if isinstance(params, dict) else {}
        dynamic_raw = data.get("dynamic_keys", data.get("dynamicKeys", {})) or {}
        dynamic = {str(k): str(v) for k, v in dynamic_raw.items()} if isinstance(dynamic_raw, dict) else {}
        return cls(
            title=title,
            aes_key=key,
            engine=str(data.get("engine", "unreal") or "unreal"),
            notes=str(data.get("notes", "") or ""),
            updated=str(data.get("updated", "") or ""),
            dynamic_keys=dynamic,
            scheme=scheme,
            guid=str(data.get("guid", "") or ""),
            parameters=params,
        )


class KeyStore:
    def __init__(self, path: str | None = None):
        self.path = path or str(Path.home() / ".dualforge" / "keys.json")
        self._entries: dict[str, KeyEntry] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning("could not read key store %s: %s", self.path, exc)
            return
        except json.JSONDecodeError:
            corrupted = Path(self.path)
            backup = corrupted.with_name(
                f"keys.json.corrupt-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            )
            try:
                os.replace(self.path, backup)
                logger.warning(
                    "key store %s was corrupt; backed it up to %s and started fresh",
                    self.path,
                    backup,
                )
            except OSError:
                logger.warning("key store %s was corrupt and could not be backed up", self.path)
            return
        for key, value in raw.items():
            if isinstance(value, str):
                self._entries[key] = KeyEntry(title=key, aes_key=value)
            elif isinstance(value, dict):
                self._entries[key] = KeyEntry.from_dict({"title": key, **value})

    def _save(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({k: v.as_dict() for k, v in self._entries.items()}, fh, indent=2)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            logger.debug("could not restrict key store permissions on %s", self.path, exc_info=True)

    def list(self) -> builtins.list[KeyEntry]:
        return list(self._entries.values())

    def get(self, title: str) -> str | None:
        entry = self._entries.get(title)
        return entry.aes_key if entry else None

    def get_entry(self, title: str) -> KeyEntry | None:
        return self._entries.get(title)

    def find_for_archive(self, archive_path: str) -> KeyEntry | None:
        """Best-effort match of a stored entry to an archive path.

        Every path component (folder names and the file name) is matched
        case-insensitively against stored entry titles, with the deepest
        match scoring highest. This finds the game even when the pak lives in
        ``Content/Paks``. Returns the best-scoring entry or None.
        """
        from pathlib import Path

        path_lower = archive_path.replace("\\", "/").lower()
        parts = [p for p in Path(path_lower).parts if p]
        best: KeyEntry | None = None
        best_score = -1
        for entry in self._entries.values():
            title = entry.title.lower()
            title_key = _fold(title)
            score = -1
            for depth, part in enumerate(parts):
                part_key = _fold(part)
                if part_key and part_key == title_key:
                    score = max(score, 100 - depth)
                elif part_key and len(part_key) >= 3 and (
                    part_key in title_key or title_key in part_key
                ):
                    score = max(score, 50 - depth)
            if score > best_score:
                best_score = score
                best = entry
        return best if best_score >= 0 else None

    def add(
        self,
        title: str,
        aes_key: str,
        engine: str = "unreal",
        notes: str = "",
        dynamic_keys: dict[str, str] | None = None,
        scheme: str = DEFAULT_SCHEME,
        guid: str = "",
        parameters: dict[str, str] | None = None,
    ) -> None:
        if not title or not aes_key:
            raise ValueError("title and aes_key are required")
        self._entries[title] = KeyEntry(
            title=title,
            aes_key=aes_key,
            engine=engine,
            notes=notes,
            updated=datetime.now(timezone.utc).isoformat(),
            dynamic_keys={str(k): str(v) for k, v in (dynamic_keys or {}).items()},
            scheme=scheme or DEFAULT_SCHEME,
            guid=guid or "",
            parameters={str(k): str(v) for k, v in (parameters or {}).items()},
        )
        self._save()

    def remove(self, title: str) -> bool:
        if title in self._entries:
            del self._entries[title]
            self._save()
            return True
        return False

    def import_mapping(
        self,
        mapping: dict[str, str],
        engine: str = "unreal",
        dynamic: dict[str, dict[str, str]] | None = None,
    ) -> int:
        count = 0
        for title, key in mapping.items():
            if not title or not key:
                continue
            existing = self._entries.get(title)
            dyn = (dynamic or {}).get(title, {})
            if existing is None:
                self.add(
                    title,
                    key,
                    engine=engine,
                    notes="imported",
                    dynamic_keys=dyn,
                )
                count += 1
            else:
                merged = {**existing.dynamic_keys, **dyn}
                changed = existing.aes_key != key or set(merged) != set(existing.dynamic_keys)
                if changed:
                    self.add(
                        existing.title,
                        key,
                        engine=existing.engine,
                        notes=existing.notes,
                        dynamic_keys=merged,
                    )
                    count += 1
        return count

    def import_fmodel_json(self, path: str) -> int:
        """Import an FModel Global.AESKeys.json key file.

        Format: {"GameName": {"mainKey": "0x...", "dynamicKeys": {"guid": "0x..."}}}
        """
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            raise ValueError("FModel key file must contain a JSON object")
        count = 0
        for title, value in payload.items():
            if not isinstance(value, dict):
                continue
            main_key = _main_key(value)
            dynamic = _dynamic_keys(value)
            if not main_key:
                continue
            existing = self._entries.get(title)
            if existing is None:
                self.add(
                    title,
                    main_key,
                    engine="unreal",
                    notes="imported from FModel",
                    dynamic_keys=dynamic,
                )
                count += 1
            elif main_key != existing.aes_key or set(dynamic) != set(existing.dynamic_keys):
                self.add(
                    existing.title,
                    main_key,
                    engine=existing.engine,
                    notes=existing.notes,
                    dynamic_keys=dynamic,
                )
                count += 1
        return count

    def sync(self, endpoints: builtins.list[str]) -> dict[str, int]:
        import requests

        synced: dict[str, int] = {}
        for endpoint in endpoints:
            try:
                response = requests.get(endpoint, timeout=30)
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                logger.warning("key sync failed for %s: %s", endpoint, exc)
                synced[endpoint] = 0
                continue
            mapping, dynamic = _extract_keys(payload)
            synced[endpoint] = self.import_mapping(mapping, dynamic=dynamic)
        return synced


def _extract_keys(payload) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract main + dynamic keys from a community endpoint payload.

    Supports FModel-style {"games": {...}}, FortniteCentral's
    {"mainKey": ..., "dynamicKeys": {...}}, and the plain
    {"Game": "0x..."} / {"keys": {...}} shapes.
    Returns (main_keys, dynamic) where dynamic maps title ->
    {"keys": {...}, "new": bool}.
    """
    mapping: dict[str, str] = {}
    dynamic: dict[str, dict[str, str]] = {}
    if not isinstance(payload, dict):
        return mapping, dynamic
    source = payload
    if "games" in payload and isinstance(payload["games"], dict):
        source = payload["games"]
    elif "keys" in payload and isinstance(payload["keys"], dict):
        source = payload["keys"]
    for title, value in source.items():
        if title in (
            "mainKey", "main_key", "aes_key", "key",
            "dynamicKeys", "dynamic_keys", "version", "build", "updated",
        ):
            continue
        if isinstance(value, str):
            mapping[title] = value
        elif isinstance(value, dict):
            main_key = _main_key(value)
            if main_key:
                mapping[title] = main_key
            dynamic_keys = _dynamic_keys(value)
            if dynamic_keys:
                dynamic[title] = dynamic_keys
    for key_name in _MAIN_KEY_FIELDS:
        if source.get(key_name):
            mapping["Fortnite"] = str(source[key_name])
            dynamic_keys = _dynamic_keys(source)
            if dynamic_keys:
                dynamic["Fortnite"] = dynamic_keys
            break
    return mapping, dynamic


__all__ = ["KeyStore", "KeyEntry", "DEFAULT_ENDPOINTS"]