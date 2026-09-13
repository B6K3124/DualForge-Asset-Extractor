"""Handlers for ``dualforge locres``: inspect and patch .locres files."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dualforge.unreal.locres import (
    apply_replacements,
    parse_locres_file,
    save_locres,
)


def _cmd_locres_dump(args: argparse.Namespace) -> int:
    try:
        locres = parse_locres_file(args.path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        content = locres.to_json()
    elif args.format == "csv":
        content = locres.to_csv()
    else:
        lines = [f"{e.qualified_key} = {e.value}" for e in locres.entries]
        content = "\n".join(lines) + ("\n" if lines else "")

    if args.out:
        Path(args.out).write_text(content, encoding="utf-8")
        print(f"wrote {len(locres.entries)} entries to {args.out}")
    else:
        print(content)
    return 0


def _cmd_locres_edit(args: argparse.Namespace) -> int:
    try:
        locres = parse_locres_file(args.path)
        replacements = {}
        for item in args.set:
            key, _, value = item.partition("=")
            if not key or not _:
                print(f"error: expected KEY=VALUE, got {item!r}", file=sys.stderr)
                return 1
            replacements[key] = value
        missing = [k for k in replacements if k not in locres.as_dict()]
        if missing:
            print(
                f"warning: {len(missing)} key(s) not found in the file; they will be written verbatim: "
                + ", ".join(sorted(missing)[:5]),
                file=sys.stderr,
            )
        entries = apply_replacements(locres.entries, replacements)
        out_path = args.out
        if os.path.normcase(os.path.abspath(out_path)) == os.path.normcase(os.path.abspath(args.path)):
            print("error: refusing to overwrite the source file; pick a different --out", file=sys.stderr)
            return 1
        version = args.version if args.version is not None else (locres.version if locres.version in (2, 3) else 3)
        count = save_locres(out_path, entries, version=version)
        print(f"wrote {count} entries to {out_path} (version {version})")
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1