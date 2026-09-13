"""Logging helpers for DualForge.

``dualforge`` is library code imported by both the GUI and the CLI, so it
never configures the root logger itself. Submodules get child loggers that
silently no-op until an entry point calls :func:`setup_logging`.

Default level is WARNING so the normal CLI/GUI output stays clean; warnings
and above go to stderr. ``--verbose`` (CLI) or ``DUALFORGE_LOG=DEBUG`` turns
debug instrumentation on. Error handling elsewhere follows three rules:

* R1 best-effort fallbacks -> ``logger.debug(..., exc_info=True)`` then absorb
* R2 degrade-with-warning  -> ``logger.warning(...)`` then fall back
* R3 raise/wrap            -> catch specific types, preserve ``from exc``
"""

from __future__ import annotations

import logging
import os
import sys

LOGGER_NAME = "dualforge"

_HANDLER_ATTR = "_dualforge_handler"

logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """Return a package-scoped child logger (safe to call from any module)."""
    qualified = (
        name
        if name == LOGGER_NAME or name.startswith(LOGGER_NAME + ".")
        else f"{LOGGER_NAME}.{name}"
    )
    return logging.getLogger(qualified)


def setup_logging(verbose: bool = False, stream=None) -> logging.Logger:
    """Configure the package root logger for an entry point.

    Default level is WARNING; ``verbose`` or ``DUALFORGE_LOG=DEBUG`` enables
    debug output. Safe to call more than once - only one stream handler is
    attached, subsequent calls just adjust the level.
    """
    root = logging.getLogger(LOGGER_NAME)
    env_level = os.environ.get("DUALFORGE_LOG", "").upper()
    if verbose or env_level in {"1", "DEBUG", "TRUE", "YES"}:
        root.setLevel(logging.DEBUG)
    elif env_level in {"0", "OFF", "FALSE", "NO"} or not root.handlers:
        root.setLevel(logging.WARNING)

    handler = next((h for h in root.handlers if getattr(h, _HANDLER_ATTR, False)), None)
    if handler is None:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        setattr(handler, _HANDLER_ATTR, True)
        root.addHandler(handler)
    elif stream is not None:
        handler.stream = stream
    root.propagate = False
    return root


__all__ = ["LOGGER_NAME", "get_logger", "setup_logging"]