"""Handler for ``dualforge extract``: extract assets from an archive."""

from __future__ import annotations

import argparse
import sys

from dualforge.extract import ExtractOptions, extract_file


def _cmd_extract(args: argparse.Namespace) -> int:
    formats = None
    if args.format:
        from dualforge.export.convert import DEFAULT_FORMATS, format_choices

        formats = {}
        for type_name in DEFAULT_FORMATS:
            if args.format in format_choices(type_name):
                formats[type_name] = args.format
    driver = None
    if args.driver:
        from dualforge.drivers import registry

        driver = registry.match(args.path) if args.driver == "auto" else registry.get(args.driver)
        if driver is None:
            print(
                f"no driver matching '{args.driver}' for {args.path}",
                file=sys.stderr,
            )
            return 1
    options = ExtractOptions(
        out_dir=args.out,
        aes_key=args.aes,
        engine=None if args.engine == "auto" else args.engine,
        type_filter=tuple(args.types) if args.types else None,
        files=args.files,
        formats=formats,
        usmap=args.usmap,
        driver=driver,
        progress=lambda i, t, m: print(f"[{i + 1}/{t}] {m}", file=sys.stderr),
    )
    try:
        result = extract_file(args.path, options)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"detected: {result.detected.engine}/{result.detected.kind}")
    if driver is not None:
        print(f"driver:  {driver.name} ({driver.label})")
    print(f"extracted {result.ok} assets to {args.out}")
    for error in result.errors:
        print(f"warning: {error}", file=sys.stderr)
    return 0 if not result.errors else 2