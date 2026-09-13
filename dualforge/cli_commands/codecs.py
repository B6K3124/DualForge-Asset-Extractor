"""Handler for ``dualforge codecs``: list supported compression codecs."""

from __future__ import annotations

import argparse

from dualforge.compression import METHODS, is_available


def _cmd_codecs(args: argparse.Namespace) -> int:
    for method in METHODS:
        status = "available" if is_available(method) else "missing"
        print(f"{method:10s} {status}")
    return 0