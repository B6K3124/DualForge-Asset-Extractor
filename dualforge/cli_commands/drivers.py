"""Handlers for ``dualforge drivers``: manage game drivers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_drivers_list(args: argparse.Namespace) -> int:
    from dualforge.drivers import registry

    drivers = registry.list()
    if not drivers:
        print("no game drivers registered")
        return 0
    print(f"{len(drivers)} driver(s):")
    for driver in sorted(drivers, key=lambda d: d.name):
        scheme = driver.encryption_scheme
        egame = f" [{driver.egame}]" if driver.egame else ""
        print(
            f"  {driver.name:<22} {driver.label:<30} "
            f"({driver.engine}/{scheme}){egame}"
        )
    return 0


def _cmd_drivers_show(args: argparse.Namespace) -> int:
    from dualforge.drivers import registry

    driver = registry.get(args.name)
    if driver is None:
        print(f"no driver named '{args.name}'", file=sys.stderr)
        return 1
    print(driver.to_json())
    return 0


def _cmd_drivers_export(args: argparse.Namespace) -> int:
    from dualforge.drivers import registry

    if args.dir:
        target = Path(args.dir)
        target.mkdir(parents=True, exist_ok=True)
        count = registry.export_all(str(target))
        print(f"exported {count} driver(s) to {target}")
        return 0
    if args.all:
        target = args.out or "drivers"
        Path(target).mkdir(parents=True, exist_ok=True)
        count = registry.export_all(target)
        print(f"exported {count} driver(s) to {target}")
        return 0
    if args.builtin:
        target = args.out or "drivers"
        Path(target).mkdir(parents=True, exist_ok=True)
        count = registry.export_builtin(target)
        print(f"exported {count} built-in driver(s) to {target}")
        return 0
    driver = registry.get(args.name)
    if driver is None:
        print(f"no driver named '{args.name}'", file=sys.stderr)
        return 1
    out = args.out or f"{driver.name}.{driver.name}.dualforge-driver.json"
    written = registry.save(driver, out)
    print(f"exported driver to {written}")
    return 0


def _cmd_drivers_import(args: argparse.Namespace) -> int:
    from dualforge.drivers import registry

    if args.dir:
        count = registry.load_dir(args.dir)
        print(f"imported {count} driver(s) from {args.dir}")
        return 0
    try:
        driver = registry.load_file(args.path)
    except Exception as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported driver '{driver.name}' ({driver.label})")
    return 0


def _cmd_drivers_match(args: argparse.Namespace) -> int:
    from dualforge.drivers import registry

    driver = registry.match(args.archive, args.mount, engine=args.engine)
    if driver is None:
        print(f"no driver matches {args.archive}", file=sys.stderr)
        return 1
    print(f"matched: {driver.name} ({driver.label})")
    print(f"  engine             : {driver.engine}")
    print(f"  encryption scheme  : {driver.encryption_scheme}")
    if driver.encryption_params:
        print(f"  scheme params      : {driver.encryption_params}")
    if driver.egame:
        print(f"  CUE4Parse EGame    : {driver.egame}")
    if driver.usmap_required:
        print("  usmap required     : yes")
    if driver.export_formats:
        print(f"  export formats     : {driver.export_formats}")
    if driver.asset_filter:
        print(f"  asset filter       : {driver.asset_filter}")
    return 0


def _cmd_drivers_create(args: argparse.Namespace) -> int:
    from dualforge.drivers import build_driver_from_archive, registry

    # Double-check the archive is real/readable for a clearer error.
    if not Path(args.archive).is_file():
        print(f"archive not found: {args.archive}", file=sys.stderr)
        return 1

    if args.name and registry.get(args.name) is not None:
        print(
            f"a driver named '{args.name}' already exists; pick a different --name",
            file=sys.stderr,
        )
        return 1
    driver = build_driver_from_archive(args.archive, name=args.name, label=args.label)
    if args.out:
        written = registry.save(driver, args.out)
        print(
            f"created and saved driver '{driver.name}' ({driver.label}) -> {written}"
        )
    else:
        print(f"created driver '{driver.name}' ({driver.label})")
    print(driver.to_json())
    return 0