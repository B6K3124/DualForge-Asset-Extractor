"""Handlers for ``dualforge il2cpp``: inspect and dump IL2CPP metadata."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dualforge.il2cpp import (
    MAX_SUPPORTED,
    MIN_SUPPORTED,
    MetadataError,
    dump_strings,
    parse_metadata,
)


def _cmd_il2cpp_inspect(args: argparse.Namespace) -> int:
    try:
        info = parse_metadata(Path(args.path).read_bytes())
    except (OSError, MetadataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("magic        : ok")
    print(f"version      : {info.version}")
    print(f"string literal : {info.string_literal_count}")
    print(f"strings (bytes): {info.string_count}")
    print(f"type defs(bytes): {info.type_definition_count}")
    supported = "" if MIN_SUPPORTED <= info.version <= MAX_SUPPORTED else " (unsupported for dumps)"
    print(f"support      : {supported or 'yes'}")
    return 0


def _cmd_il2cpp_strings(args: argparse.Namespace) -> int:
    try:
        count, out = dump_strings(Path(args.path).read_bytes(), args.out)
    except (OSError, MetadataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if out:
        print(f"wrote {count} string literals to {out}")
    return 0