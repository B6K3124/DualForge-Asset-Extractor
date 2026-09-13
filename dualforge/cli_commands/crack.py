"""Handlers for ``dualforge crack``: auto-crack AES keys via Ghidra."""

from __future__ import annotations

import argparse
import sys


def _cmd_crack_status(args: argparse.Namespace) -> int:
    from dualforge.ghidra.manager import toolchain_status

    status = toolchain_status()
    print(f"ghidra        : {status['ghidra'] or 'not found'}")
    print(f"java          : {status['java'] or 'not found'}")
    if status["java"]:
        print(f"java major    : {status['java_major']}")
    print(f"cache root    : {status['cache_root']}")
    print(f"ready to hunt : {'yes' if status['ready'] else 'no'}")
    if not status["ready"]:
        print("run 'dualforge crack run <pak-or-folder>' to auto-download the toolchain")
    return 0


def _cmd_crack_run(args: argparse.Namespace) -> int:
    if not args.path:
        print("usage: dualforge crack run <pak-or-folder>", file=sys.stderr)
        return 2
    if getattr(args, "all_binaries", False):
        from dualforge.crack import crack_all

        def _log(msg: str) -> None:
            print(f"  {msg}", file=sys.stderr)

        download = not args.no_download
        try:
            result = crack_all(
                args.path,
                download=download,
                startup_timeout=args.startup_timeout,
                ghidra_home=args.ghidra_home,
                save_keys=not args.no_save,
                title=args.title,
                log=_log,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        print(f"validation pak : {result['pak']}")
        scanned = result.get("scanned", [])
        print(f"binaries scanned: {len(scanned)}")
        for exe in scanned:
            print(f"  {exe}")
        print(f"candidate keys : {len(result['candidates'])}")
        verified = result["verified"]
        print(f"verified keys  : {len(verified)}")
        for key in verified:
            print(f"  {key}")
        if result["status"] == "ok":
            for key in verified:
                print(f"cracked key    : {key}")
            if result["saved"]:
                print("saved to key store:", ", ".join(result['saved']))
            else:
                print("(keys not saved; re-run without --no-save to persist)")
            return 0
        # status == "no_valid_key"
        print("no candidate validated against the pak; the archive format may be")
        print("proprietary/obfuscated or the key is runtime/session-bound.")
        print("(see 'dualforge keys test' for details)")
        return 1

    from dualforge.crack import crack

    download = not args.no_download
    try:
        result = crack(
            args.path,
            download=download,
            startup_timeout=args.startup_timeout,
            ghidra_home=args.ghidra_home,
            save_keys=not args.no_save,
            title=args.title,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"exe            : {result['exe']}")
    print(f"validation pak : {result['pak']}")
    if result["status"] == "hunt_failed":
        print(f"Ghidra hunt failed (exit {result['returncode']})")
        print(result["detail"][-1200:])
        return 1
    print(f"candidate keys : {len(result['candidates'])}")
    verified = result["verified"]
    print(f"verified keys  : {len(verified)}")
    for key in verified:
        print(f"  {key}")
    if result["status"] == "ok":
        for key in verified:
            print(f"cracked key    : {key}")
        if result["saved"]:
            print("saved to key store:", ", ".join(result['saved']))
        else:
            print("(keys not saved; re-run without --no-save to persist)")
        return 0
    # status == "no_valid_key"
    print("no candidate validated against the pak; the archive format may be")
    print("proprietary/obfuscated or the key is runtime/session-bound.")
    print("(see 'dualforge keys test' for details)")
    return 1