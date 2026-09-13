"""Handler for ``dualforge detect``: identify an archive format."""

from __future__ import annotations

import argparse

from dualforge.detector import detect


def _cmd_detect(args: argparse.Namespace) -> int:
    detection = detect(args.path)
    if detection is None:
        print(f"unable to identify: {args.path}")
        return 1
    print(detection.summary())
    return 0