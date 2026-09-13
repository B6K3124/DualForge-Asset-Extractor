"""Handlers for ``dualforge repack``: replace assets inside a Unity archive."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _find_unity_asset(archive: str, asset_path: str):
    from dualforge.unity import UnityArchive

    handle = UnityArchive(archive)
    for asset in handle.assets():
        if asset.path == asset_path:
            return handle, asset
    raise LookupError(f"no asset '{asset_path}' in {archive}")


def _cmd_repack_texture(args: argparse.Namespace) -> int:
    from dualforge.unity.repack import replace_texture, save_archive

    try:
        handle, asset = _find_unity_asset(args.archive, args.asset)
        replace_texture(handle, asset, args.image)
        save_archive(handle, args.out)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"replaced Texture2D '{args.asset}' -> {args.out}")
    return 0


def _cmd_repack_text(args: argparse.Namespace) -> int:
    from dualforge.unity.repack import replace_text_asset, save_archive

    try:
        handle, asset = _find_unity_asset(args.archive, args.asset)
        replace_text_asset(handle, asset, Path(args.file).read_bytes())
        save_archive(handle, args.out)
    except (OSError, LookupError, Exception) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"replaced TextAsset '{args.asset}' -> {args.out}")
    return 0


def _cmd_repack_font(args: argparse.Namespace) -> int:
    from dualforge.unity.repack import replace_font, save_archive

    try:
        handle, asset = _find_unity_asset(args.archive, args.asset)
        replace_font(handle, asset, args.font)
        save_archive(handle, args.out)
    except (OSError, LookupError, Exception) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"replaced Font '{args.asset}' -> {args.out}")
    return 0