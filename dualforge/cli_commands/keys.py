"""Handlers for ``dualforge keys``: manage the decryption key database."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dualforge.unreal import KeyStore
from dualforge.unreal.keys import DEFAULT_ENDPOINTS


def _cmd_keys_list(args: argparse.Namespace) -> int:
    store = KeyStore()
    entries = store.list()
    if not entries:
        print("key store is empty")
        return 0
    for entry in entries:
        print(f"{entry.title}\t{entry.engine}\t{entry.aes_key[:16]}...")
    return 0


def _cmd_keys_schemes(args: argparse.Namespace) -> int:
    from dualforge.encryption import registry
    from dualforge.encryption.presets import PRESETS

    print("registered schemes:")
    for name in registry.list_schemes():
        print(f"  {name}")
    print("\ngame presets:")
    for preset in PRESETS:
        print(f"  {preset.name:<20} {preset.label}")
    return 0


def _cmd_keys_add(args: argparse.Namespace) -> int:
    scheme = args.scheme or "aes-256"
    from dualforge.encryption.registry import list_schemes

    known = set(list_schemes())
    from dualforge.encryption.presets import PRESETS

    known |= {p.name for p in PRESETS}
    if scheme not in known:
        print(
            f"warning: unknown scheme '{scheme}'. Known: {', '.join(sorted(known))}",
            file=sys.stderr,
        )
    parameters = {}
    for item in args.param:
        if "=" in item:
            k, v = item.split("=", 1)
            parameters[k.strip()] = v.strip()
    KeyStore().add(
        args.title,
        args.aes_key,
        engine=args.engine,
        scheme=scheme,
        guid=args.guid,
        parameters=parameters,
    )
    print(f"added key for {args.title}")
    return 0


def _cmd_keys_test(args: argparse.Namespace) -> int:
    from dualforge.encryption.brute import validate_key, probe_pak_blocks

    store = KeyStore()
    if args.title:
        entry = store.get_entry(args.title)
        if entry is None:
            print(f"no stored key for {args.title}", file=sys.stderr)
            return 1
        aes_key = entry.aes_key
        scheme = entry.scheme or "aes-256"
        parameters = dict(entry.parameters)
        guid = entry.guid
    else:
        if not args.aes:
            print("provide --title or --aes (and --scheme if non-standard)", file=sys.stderr)
            return 1
        aes_key = args.aes
        scheme = args.scheme or "aes-256"
        parameters = {}
        guid = ""

    raw = Path(args.pak).read_bytes()
    blocks = probe_pak_blocks(raw)
    if not blocks:
        print("could not locate an encrypted index block in the pak footer", file=sys.stderr)
        return 1
    hits = 0
    for block in blocks:
        if validate_key(block, scheme, aes_key, Path(args.pak).name, guid, parameters):
            hits += 1
    if hits:
        print(f"key OK (decrypted {hits}/{len(blocks)} index blocks with scheme '{scheme}')")
        return 0
    print(
        f"key did NOT decrypt the index (scheme '{scheme}'). Check the scheme and "
        f"--param values, or list stored keys with 'dualforge keys list'.",
        file=sys.stderr,
    )
    return 1


def _cmd_keys_remove(args: argparse.Namespace) -> int:
    if KeyStore().remove(args.title):
        print(f"removed key for {args.title}")
        return 0
    print(f"no key found for {args.title}", file=sys.stderr)
    return 1


def _cmd_keys_sync(args: argparse.Namespace) -> int:
    endpoints = args.endpoint or DEFAULT_ENDPOINTS
    try:
        synced = KeyStore().sync(endpoints)
    except Exception as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    for endpoint, count in synced.items():
        print(f"{endpoint}: {count} new keys")
    return 0


def _cmd_keys_import(args: argparse.Namespace) -> int:
    try:
        count = KeyStore().import_fmodel_json(args.path)
    except Exception as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported {count} keys from {args.path}")
    return 0