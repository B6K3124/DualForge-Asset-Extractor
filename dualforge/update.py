"""Update checks and self-update for DualForge.

The latest version is read from the GitHub Releases API
(``/repos/B6K3124/DualForge-Asset-Extractor/releases/latest``) using the same
HTTP/header convention as :mod:`dualforge.ghidra.manager` and the ``requests``
pattern from :mod:`dualforge.unreal.keys`.

Alongside the explicit check, :func:`check_update_cached` stores the last
result under ``~/.dualforge/update_check.json`` so the GUI and CLI can look for
a notice without hitting the network on every launch. :func:`perform_update`
updates a source checkout (``git pull`` + ``pip install .``) into the active
Python environment.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import dualforge
import requests

from dualforge.log import get_logger

logger = get_logger(__name__)

LATEST_RELEASE_URL = (
    "https://api.github.com/repos/B6K3124/DualForge-Asset-Extractor/releases/latest"
)

_HTTP_TIMEOUT = 15

#: How long a successful check is trusted before re-querying GitHub.
_CACHE_MAX_AGE_MINUTES = 24 * 60
#: How long a failed check is remembered before retrying (avoids network spam).
_CACHE_ERROR_MAX_AGE_MINUTES = 30


class UpdateError(RuntimeError):
    """The update check could not be completed (network/HTTP/payload)."""


class NoReleasesError(UpdateError):
    """The release endpoint responded but has no published releases."""


def _state_path() -> Path:
    return Path.home() / ".dualforge" / "update_check.json"


@dataclass
class UpdateState:
    """The result of an (optionally cached) update check."""

    latest: str | None = None
    is_outdated: bool = False
    checked_at: str | None = None
    error: str = ""

    def age_minutes(self, now: datetime | None = None) -> float:
        if not self.checked_at:
            return float("inf")
        try:
            checked = datetime.fromisoformat(self.checked_at)
        except ValueError:
            return float("inf")
        reference = now or datetime.now(timezone.utc)
        return (reference - checked).total_seconds() / 60.0

    @classmethod
    def from_payload(cls, payload: dict | None) -> UpdateState:
        if not isinstance(payload, dict):
            return cls()
        return cls(
            latest=payload.get("latest"),
            is_outdated=bool(payload.get("is_outdated")),
            checked_at=payload.get("checked_at"),
            error=str(payload.get("error") or ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _is_fresh(state: UpdateState, now: datetime | None = None) -> bool:
    if state.error:
        return state.age_minutes(now) < _CACHE_ERROR_MAX_AGE_MINUTES
    return state.age_minutes(now) < _CACHE_MAX_AGE_MINUTES


def _parse_version(text: str) -> tuple[int, ...]:
    """Parse ``v0.3.1`` / ``0.3.1`` into a comparable int tuple.

    A leading ``v`` is stripped; non-numeric trailing segments (e.g. ``-dev``,
    ``-rc1``, ``.post1``) are ignored. Raises ``UpdateError`` when no numeric
    ``major.minor[.patch]`` can be parsed.
    """
    value = text.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) < len(chunk) or len(parts) == 3:
            break
    if len(parts) < 2:
        raise UpdateError(f"unparseable version tag: {text!r}")
    return tuple(parts)


def compare_versions(latest: str, current: str) -> int:
    """Return <0 if ``current`` is older than ``latest``, 0 if equal, >0 if newer."""
    left = _parse_version(latest)
    right = _parse_version(current)
    return (right > left) - (right < left)


def fetch_latest_version(url: str = LATEST_RELEASE_URL) -> str:
    """Return the latest release tag (without the ``v`` prefix).

    Raises ``UpdateError`` on network errors, non-2xx responses, unreadable
    JSON, or a missing ``tag_name``; raises ``NoReleasesError`` (an
    ``UpdateError``) when the endpoint reports no releases at all.
    """
    try:
        response = requests.get(url, timeout=_HTTP_TIMEOUT)
        if response.status_code == 404:
            raise NoReleasesError("no published releases found, assuming up to date")
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise UpdateError(f"update check failed: {exc}") from exc
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    if not isinstance(tag, str) or not tag.strip():
        raise UpdateError("update check failed: release payload has no tag_name")
    _parse_version(tag)
    return tag.lstrip("vV")


def check_update(current: str, url: str = LATEST_RELEASE_URL) -> tuple[str, bool]:
    """Check whether an update is available.

    Returns ``(latest, is_outdated)`` where ``is_outdated`` is True only when a
    newer release exists. Raises ``UpdateError`` if the latest version could not
    be resolved.
    """
    latest = fetch_latest_version(url)
    return latest, compare_versions(latest, current) < 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_state(state: UpdateState) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state.to_dict()), encoding="utf-8")
    except OSError as exc:  # best-effort cache, never fatal
        logger.debug("could not write update cache: %s", exc)


def check_update_cached(
    current: str,
    url: str = LATEST_RELEASE_URL,
    force: bool = False,
) -> UpdateState:
    """Return the cached update state, refreshing it when stale or forced.

    A fresh cached result is returned untouched. Stale or missing cache hits the
    network once; the result (success or failure) is written back so repeated
    calls stay local. Never raises - failures surface as ``state.error``.
    """
    try:
        cached = UpdateState.from_payload(
            json.loads(_state_path().read_text(encoding="utf-8"))
        )
    except (OSError, ValueError):
        cached = UpdateState()

    if not force and _is_fresh(cached, now=datetime.now(timezone.utc)):
        return cached

    state = UpdateState(checked_at=_now_iso())
    try:
        latest, is_outdated = check_update(current, url)
        state.latest = latest
        state.is_outdated = is_outdated
    except NoReleasesError as exc:
        state.error = str(exc)
    except UpdateError as exc:
        state.error = str(exc)
        logger.warning("update check failed: %s", exc)
    _save_state(state)
    return state


def _find_source_root() -> Path:
    """Locate the DualForge source checkout (parent of the package) or raise."""
    if getattr(sys, "frozen", False):
        raise UpdateError(
            "this build cannot update itself in place; download the new "
            "release bundle and replace the application"
        )
    root = Path(dualforge.__file__).resolve().parents[1]
    project = root / "pyproject.toml"
    if not project.is_file():
        raise UpdateError(
            f"no source checkout found next to the installed package "
            f"({root}); update manually with 'git pull' + 'pip install .'"
        )
    return root


def _run(step: Callable[[str], None], label: str, argv: list[str], cwd: Path) -> None:
    step(f"$ {' '.join(argv)}")
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"{label} failed to start: {exc}") from exc
    if proc.stdout:
        for line in proc.stdout.splitlines():
            step(line)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        raise UpdateError(f"{label} failed ({proc.returncode}): {detail}")


def perform_update(
    pull: bool = True,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Update a source checkout into the active environment.

    Runs ``git pull --ff-only`` (unless ``pull`` is False) and
    ``pip install .`` from the DualForge source root, then returns that root.
    Raises ``UpdateError`` on failure; ``log`` receives step output lines.
    """
    step: Callable[[str], None] = log or (lambda line: None)
    root = _find_source_root()
    if pull and (root / ".git").exists():
        _run(step, "git pull", ["git", "-C", str(root), "pull", "--ff-only"], root)
    _run(step, "pip install", [sys.executable, "-m", "pip", "install", ".", "--no-input"], root)
    return root


__all__ = [
    "UpdateError",
    "NoReleasesError",
    "UpdateState",
    "LATEST_RELEASE_URL",
    "compare_versions",
    "fetch_latest_version",
    "check_update",
    "check_update_cached",
    "perform_update",
]