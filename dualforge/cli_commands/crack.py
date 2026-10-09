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


def _print_verified(verified_keys, label: str = "") -> None:
    """Print verified keys with their detected scheme (when not plain AES)."""
    for entry in verified_keys:
        key = entry.key if hasattr(entry, "key") else entry
        scheme = getattr(entry, "scheme", "aes-256")
        tag = f" [{scheme}]" if scheme != "aes-256" else ""
        print(f"{label}{key}{tag}")


def _cmd_crack_offline(args: argparse.Namespace) -> int:
    """Hunt a key from a running game's memory (runtime / offline crack)."""
    if args.list_processes:
        from dualforge.unreal.process import list_processes

        for pid, exe in list_processes():
            print(f"{pid}\t{exe}")
        return 0
    if args.pid is None and not args.process:
        print("error: pass --process <game.exe> or --pid <id>", file=sys.stderr)
        return 2

    from dualforge.crack_process import crack_offline

    def _log(msg: str) -> None:
        print(f"  {msg}", file=sys.stderr)

    try:
        result = crack_offline(
            process=args.process,
            pid=args.pid,
            pak=args.pak,
            scheme=args.scheme,
            save_keys=not args.no_save,
            title=args.title,
            block_count=args.block_count,
            max_candidates=args.max_candidates,
            raw_windows=args.raw_windows,
            log=_log,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"process        : {result['process']} (pid {result['pid']})")
    print(f"bytes scanned  : {result['bytes_scanned']:,}")
    print(f"candidate keys : {len(result['candidates'])}")
    for match in result["matches"][:8]:
        print(f"  {match['signature']}: {match['candidate_count']} candidate(s) at {match['address']}")
    remaining = len(result["matches"]) - 8
    if remaining > 0:
        print(f"  ... {remaining} more signature site(s)")
    if result.get("pak"):
        print(f"validation pak : {result['pak']}")
    print(f"verified keys  : {len(result['verified'])}")
    vks = result.get("verified_keys") or []
    _print_verified(vks)
    if result["status"] == "ok":
        _print_verified(vks, label="cracked key    : ")
        if result["saved"]:
            print("saved to key store:", ", ".join(result["saved"]))
        else:
            print("(keys not saved; re-run without --no-save to persist)")
        return 0
    if result["status"] == "no_key_found":
        print("no candidate validated against the pak; try:")
        print("  - use --raw-windows <n> to widen the raw-memory sample")
        print("  - attach to a freshly started game so the key material is resident")
        print("  - pass --scheme <name> to restrict probing to one scheme")
        return 1
    # status == "scan_only": no pak given, report the best candidates
    for key in result["candidates"][:8]:
        print(f"candidate key  : {key}")
    print("(no --pak given; pass one to validate and save the key)")
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
        vks = result.get("verified_keys") or verified
        _print_verified(vks)
        if result["status"] == "ok":
            _print_verified(vks, label="cracked key    : ")
            if result["saved"]:
                print("saved to key store:", ", ".join(result["saved"]))
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
    vks = result.get("verified_keys") or verified
    _print_verified(vks)
    if result["status"] == "ok":
        _print_verified(vks, label="cracked key    : ")
        if result["saved"]:
            print("saved to key store:", ", ".join(result["saved"]))
        else:
            print("(keys not saved; re-run without --no-save to persist)")
        return 0
    # status == "no_valid_key"
    print("no candidate validated against the pak; the archive format may be")
    print("proprietary/obfuscated or the key is runtime/session-bound.")
    print("(see 'dualforge keys test' for details)")
    return 1