"""Handlers for ``dualforge update``: check the installed version against the
latest GitHub release, and update a source checkout into the active environment.

Exit codes (check):
* 0 - the installed version is the same as or newer than the latest release
* 1 - the check failed (network/HTTP/payload; nothing is reported as current)
* 2 - a newer release is available
"""

from __future__ import annotations

import argparse

from dualforge import __version__
from dualforge.log import get_logger

logger = get_logger(__name__)


def _cmd_update_check(args: argparse.Namespace) -> int:
    from dualforge.update import LATEST_RELEASE_URL, NoReleasesError, UpdateError, check_update

    try:
        latest, is_outdated = check_update(__version__, url=args.url or LATEST_RELEASE_URL)
    except NoReleasesError as exc:
        print(str(exc))
        return 0
    except UpdateError as exc:
        print(str(exc))
        return 1
    if is_outdated:
        print(f"dualforge {__version__} - update available: {latest}")
        return 2
    print(f"dualforge {__version__} - up to date (latest: {latest})")
    return 0


def _cmd_update_install(args: argparse.Namespace) -> int:
    from dualforge.update import UpdateError, check_update_cached, perform_update

    current = __version__
    latest = None
    try:
        latest = check_update_cached(current).latest
    except Exception:  # noqa: BLE001 - the cached check is best-effort only
        logger.debug("could not refresh update state ahead of install", exc_info=True)
    print(f"dualforge {current} - updating from the source checkout...")
    try:
        root = perform_update(pull=not args.no_pull, log=print)
    except UpdateError as exc:
        print(str(exc))
        return 1
    print(f"updated from {root}")
    print(f"dualforge {current} installed")
    if latest:
        print(f"latest release is {latest} - restart DualForge to verify (dualforge --version)")
    else:
        print("restart DualForge to verify (dualforge --version)")
    return 0