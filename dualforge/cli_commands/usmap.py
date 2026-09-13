"""Handlers for ``dualforge usmap``: inspect, validate and rebuild .usmap files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_usmap_validate(args: argparse.Namespace) -> int:
    from dualforge.unreal.usmap import parse_usmap

    try:
        mappings = parse_usmap(Path(args.path).read_bytes())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"names:   {len(mappings.names)}")
    print(f"enums:   {len(mappings.enums)}")
    print(f"structs: {len(mappings.structs)}")
    print(f"version: {mappings.version}")
    print(f"versioning: {'yes' if mappings.versioning else 'no'}")
    return 0


def _cmd_usmap_repack(args: argparse.Namespace) -> int:
    from dualforge.unreal.usmap import UsmapCompression, UsmapVersion, build_usmap, parse_usmap

    try:
        mappings = parse_usmap(Path(args.path).read_bytes())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    compression = {
        "zstd": UsmapCompression.ZStandard,
        "brotli": UsmapCompression.Brotli,
        "none": UsmapCompression.None_,
    }[args.compression]
    try:
        version = UsmapVersion(args.version) if args.version is not None else mappings.version
        data = build_usmap(mappings, version=version, compression=compression)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = args.out or f"{args.path}.rebuilt.usmap"
    Path(out).write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes)")
    return 0


def _cmd_usmap_names(args: argparse.Namespace) -> int:
    from dualforge.unreal.usmap import parse_usmap

    try:
        mappings = parse_usmap(Path(args.path).read_bytes())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.count:
        print(len(mappings.names))
        return 0
    for name in mappings.names:
        if not args.filter or args.filter in name:
            print(name)
    return 0


def _cmd_usmap_dump(args: argparse.Namespace) -> int:
    from dualforge.unreal.usmap_dump import (
        UsmapDumpError,
        dump_usmap,
        find_process,
        list_game_processes,
    )

    if args.list_processes:
        for pid, exe in list_game_processes():
            print(f"{pid}\t{exe}")
        return 0
    if not args.out:
        print("error: pass -o/--out <usmap file>", file=sys.stderr)
        return 1
    if args.pid is None and not args.process:
        print("error: pass --process <game.exe> or --pid <id>", file=sys.stderr)
        return 1
    try:
        if args.pid is not None:
            pid = args.pid
        else:
            pid, exe = find_process(args.process)
            print(f"attaching to {exe} (pid {pid})")
        pool = dump_usmap(pid, args.out)
    except (UsmapDumpError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"dumped {len(pool.names)} names from {pool.block_count} blocks -> {args.out}")
    return 0