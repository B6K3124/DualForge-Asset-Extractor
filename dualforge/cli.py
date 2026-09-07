from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dualforge import __version__
from dualforge.compression import METHODS, is_available
from dualforge.detector import detect
from dualforge.extract import ExtractOptions, extract_file
from dualforge.unreal import KeyStore
from dualforge.unreal.keys import DEFAULT_ENDPOINTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualforge",
        description="Universal Unity + Unreal game asset extraction toolkit.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    detect_parser = sub.add_parser("detect", help="identify an archive format")
    detect_parser.add_argument("path", help="file to inspect")
    detect_parser.set_defaults(handler=_cmd_detect)

    extract_parser = sub.add_parser("extract", help="extract assets from an archive")
    extract_parser.add_argument("path", help="archive to extract")
    extract_parser.add_argument("-o", "--out", required=True, help="output directory")
    extract_parser.add_argument("--engine", choices=["auto", "unity", "unreal", "bethesda", "cdpr"], default="auto")
    extract_parser.add_argument("--aes", help="AES-256 key (hex) for encrypted Unreal archives")
    extract_parser.add_argument(
        "--usmap",
        help="CUE4Parse mappings file (.usmap) for unversioned UE5 packages; "
        "auto-detected from DUALFORGE_USMAP, ~/.dualforge or the game folder",
    )
    extract_parser.add_argument(
        "--types", nargs="*", help="Unity object types to export, e.g. Texture2D AudioClip"
    )
    extract_parser.add_argument("--files", nargs="*", help="Unreal paths to extract")
    extract_parser.add_argument(
        "--format",
        help="export format applied to all types that support it, e.g. png, wav, obj, gltf",
    )
    extract_parser.add_argument(
        "--driver",
        help="game driver name to apply (overrides aes/scheme/format defaults); "
        "'auto' matches the archive path automatically",
    )
    extract_parser.set_defaults(handler=_cmd_extract)

    world_parser = sub.add_parser(
        "world",
        help="combine every mesh in a Unity archive into a single USD world layer",
    )
    world_parser.add_argument("path", help="Unity archive (bundle, assets, level, ...)")
    world_parser.add_argument(
        "-o", "--out", required=True,
        help="output .usd/.usda file (defaults to ASCII regardless of extension)",
    )
    world_parser.add_argument(
        "--scene", default="World", help="root prim name (default: World)",
    )
    world_parser.add_argument(
        "--up-axis", choices=("Y", "Z"), default="Y",
        help="stage up axis in the USD metadata (default: Y)",
    )
    world_parser.set_defaults(handler=_cmd_world)

    il2cpp_parser = sub.add_parser(
        "il2cpp",
        help="inspect and dump a Unity IL2CPP global-metadata.dat",
    )
    il2cpp_sub = il2cpp_parser.add_subparsers(dest="il2cpp_command", required=True)
    il2cpp_inspect = il2cpp_sub.add_parser("inspect", help="print the metadata header summary")
    il2cpp_inspect.add_argument("path", help="path to global-metadata.dat")
    il2cpp_inspect.set_defaults(il2cpp_handler=_cmd_il2cpp_inspect)
    il2cpp_strings = il2cpp_sub.add_parser(
        "strings", help="dump the string-literal pool (il2cppdumper -nns style)",
    )
    il2cpp_strings.add_argument("path", help="path to global-metadata.dat")
    il2cpp_strings.add_argument(
        "-o", "--out", help="output text file; if omitted, prints to stdout",
    )
    il2cpp_strings.set_defaults(il2cpp_handler=_cmd_il2cpp_strings)

    keys_parser = sub.add_parser("keys", help="manage the decryption key database")
    keys_sub = keys_parser.add_subparsers(dest="key_command", required=True)
    keys_list = keys_sub.add_parser("list")
    keys_list.set_defaults(key_handler=_cmd_keys_list)
    keys_add = keys_sub.add_parser("add")
    keys_add.add_argument("title")
    keys_add.add_argument("aes_key")
    keys_add.add_argument("--engine", default="unreal")
    keys_add.add_argument(
        "--scheme",
        default=None,
        help="encryption scheme/preset (default: aes-256). See 'dualforge keys schemes'.",
    )
    keys_add.add_argument("--guid", default="", help="encryption key GUID (dynamic-key games)")
    keys_add.add_argument(
        "--param", action="append", default=[],
        metavar="KEY=VALUE",
        help="scheme parameter, e.g. xor_key=1122334455667788 (repeatable)",
    )
    keys_add.set_defaults(key_handler=_cmd_keys_add)
    keys_list = keys_sub.add_parser("schemes")
    keys_list.set_defaults(key_handler=_cmd_keys_schemes)
    keys_test = keys_sub.add_parser(
        "test", help="test a key/scheme against an Unreal pak file",
    )
    keys_test.add_argument("pak", help="path to an encrypted .pak file")
    keys_test.add_argument("--title", help="use this stored entry's scheme/key/params")
    keys_test.add_argument("--aes", help="key to test (hex)")
    keys_test.add_argument(
        "--scheme", default="aes-256",
        help="scheme to test with (only meaningful with --aes)",
    )
    keys_test.set_defaults(key_handler=_cmd_keys_test)
    keys_remove = keys_sub.add_parser("remove")
    keys_remove.add_argument("title")
    keys_remove.set_defaults(key_handler=_cmd_keys_remove)
    keys_sync = keys_sub.add_parser("sync")
    keys_sync.add_argument("--endpoint", action="append", help="community key endpoint URL")
    keys_sync.set_defaults(key_handler=_cmd_keys_sync)
    keys_import = keys_sub.add_parser("import", help="import an FModel Global.AESKeys.json file")
    keys_import.add_argument("path", help="path to Global.AESKeys.json")
    keys_import.set_defaults(key_handler=_cmd_keys_import)

    codec_parser = sub.add_parser("codecs", help="list supported compression codecs")
    codec_parser.set_defaults(handler=_cmd_codecs)

    usmap_parser = sub.add_parser("usmap", help="inspect, validate and rebuild .usmap files")
    usmap_sub = usmap_parser.add_subparsers(dest="usmap_command", required=True)
    usmap_validate = usmap_sub.add_parser("validate", help="parse a usmap and report its contents")
    usmap_validate.add_argument("path", help="usmap file to parse")
    usmap_validate.set_defaults(usmap_handler=_cmd_usmap_validate)
    usmap_repack = usmap_sub.add_parser("repack", help="rebuild a usmap (optionally recompress)")
    usmap_repack.add_argument("path", help="usmap file to rebuild")
    usmap_repack.add_argument("-o", "--out", help="output file (default: <path>.rebuilt.usmap)")
    usmap_repack.add_argument(
        "--compression", choices=["zstd", "brotli", "none"], default="zstd",
        help="output compression (default: zstd)",
    )
    usmap_repack.add_argument(
        "--version", type=int, choices=list(range(5)), default=None,
        help="usmap format version 0-4 (default: keep input version)",
    )
    usmap_repack.set_defaults(usmap_handler=_cmd_usmap_repack)
    usmap_names = usmap_sub.add_parser("names", help="list the usmap name table")
    usmap_names.add_argument("path", help="usmap file to inspect")
    usmap_names.add_argument("--filter", help="only print names containing this substring")
    usmap_names.add_argument("--count", action="store_true", help="only print the name count")
    usmap_names.set_defaults(usmap_handler=_cmd_usmap_names)
    usmap_dump = usmap_sub.add_parser(
        "dump", help="dump the FNamePool of a running UE5 game into a usmap (Windows)",
    )
    usmap_dump.add_argument("-o", "--out", help="output usmap file")
    usmap_dump.add_argument(
        "--process", help="game executable name, e.g. POLARIS-Win64-Shipping",
    )
    usmap_dump.add_argument("--pid", type=int, help="game process id (alternative to --process)")
    usmap_dump.add_argument(
        "--list-processes", action="store_true", help="list running processes and exit",
    )
    usmap_dump.set_defaults(usmap_handler=_cmd_usmap_dump)

    driver_parser = sub.add_parser(
        "drivers", help="manage game drivers (import/export/match)"
    )
    driver_sub = driver_parser.add_subparsers(dest="driver_command", required=True)
    driver_list = driver_sub.add_parser("list", help="list all registered game drivers")
    driver_list.set_defaults(driver_handler=_cmd_drivers_list)
    driver_show = driver_sub.add_parser("show", help="show a driver's details as JSON")
    driver_show.add_argument("name", help="driver name")
    driver_show.set_defaults(driver_handler=_cmd_drivers_show)
    driver_export = driver_sub.add_parser(
        "export", help="export a driver to a JSON file"
    )
    driver_export.add_argument("name", help="driver name")
    driver_export.add_argument("-o", "--out", help="output file (default: <name>.<name>.dualforge-driver.json)")
    driver_export.add_argument(
        "--dir",
        help="export a directory of driver files",
    )
    driver_export.add_argument(
        "--all", action="store_true", help="export all registered drivers",
    )
    driver_export.add_argument(
        "--builtin", action="store_true", help="export only built-in drivers",
    )
    driver_export.set_defaults(driver_handler=_cmd_drivers_export)
    driver_import = driver_sub.add_parser(
        "import", help="import a driver from a JSON file"
    )
    driver_import.add_argument("path", help="path to a .dualforge-driver.json file")
    driver_import.add_argument(
        "--dir", help="import all driver files from a directory",
    )
    driver_import.set_defaults(driver_handler=_cmd_drivers_import)
    driver_match = driver_sub.add_parser(
        "match", help="find the best driver for an archive"
    )
    driver_match.add_argument("archive", help="path to an archive file")
    driver_match.add_argument("--mount", default="", help="pak mount point hint")
    driver_match.add_argument(
        "--engine", choices=["unity", "unreal", "bethesda", "cdpr"], help="filter by engine",
    )
    driver_match.set_defaults(driver_handler=_cmd_drivers_match)
    driver_create = driver_sub.add_parser(
        "create",
        help="auto-build a game driver from an archive, from scratch",
    )
    driver_create.add_argument("archive", help="path to an archive file")
    driver_create.add_argument("--name", help="driver name (default: derived from folder)")
    driver_create.add_argument("--label", help="human-friendly label")
    driver_create.add_argument(
        "-o", "--out", help="output file; if set, saves the driver and registers it",
    )
    driver_create.set_defaults(driver_handler=_cmd_drivers_create)

    crack_parser = sub.add_parser(
        "crack",
        help="auto-detect the game exe and crack its AES key via Ghidra",
    )
    crack_sub = crack_parser.add_subparsers(dest="crack_command")
    crack_hunt = crack_sub.add_parser("run", help="run the auto-crack pipeline")
    crack_hunt.add_argument("path", help="a game .pak or the game install folder")
    crack_hunt.add_argument(
        "--no-download", action="store_true",
        help="do not download Ghidra/JRE; fail if not already installed",
    )
    crack_hunt.add_argument(
        "--ghidra-home", help="explicit Ghidra install root (overrides auto-detection)",
    )
    crack_hunt.add_argument(
        "--startup-timeout", type=int, default=300,
        help="seconds to wait for Ghidra to start (default: 300)",
    )
    crack_hunt.add_argument("--title", help="title to store the cracked key under")
    crack_hunt.add_argument(
        "--no-save", action="store_true", help="validate candidates but do not save keys",
    )
    crack_hunt.add_argument(
        "--all-binaries", action="store_true",
        help="scan every detected executable instead of just the top-scored one",
    )
    crack_hunt.set_defaults(crack_handler=_cmd_crack_run)
    crack_status = crack_sub.add_parser(
        "status", help="report the Ghidra/JRE toolchain status",
    )
    crack_status.set_defaults(crack_handler=_cmd_crack_status)

    locres_parser = sub.add_parser(
        "locres", help="inspect and dump Unreal Engine .locres localization files",
    )
    locres_sub = locres_parser.add_subparsers(dest="locres_command")
    locres_dump = locres_sub.add_parser("dump", help="dump a .locres file to JSON or CSV")
    locres_dump.add_argument("path", help="path to a .locres file")
    locres_dump.add_argument(
        "-f", "--format", choices=("json", "csv", "text"), default="json",
        help="output format (default: json)",
    )
    locres_dump.add_argument(
        "-o", "--out", help="output file; if omitted, prints to stdout",
    )
    locres_dump.set_defaults(locres_handler=_cmd_locres_dump)
    locres_edit = locres_sub.add_parser("edit", help="patch one or more entries and write a new .locres file")
    locres_edit.add_argument("path", help="path to a .locres file")
    locres_edit.add_argument(
        "set", nargs="+", metavar="KEY=VALUE",
        help="replacement as qualified-key=value (e.g. NS.Key=New text); repeatable",
    )
    locres_edit.add_argument(
        "-o", "--out", required=True, help="output .locres file (never the source)",
    )
    locres_edit.add_argument(
        "--version", type=int, choices=(2, 3), default=None,
        help="output format version (default: keep the input version)",
    )
    locres_edit.set_defaults(locres_handler=_cmd_locres_edit)

    repack_parser = sub.add_parser(
        "repack", help="replace an asset inside a Unity archive and save the result",
    )
    repack_sub = repack_parser.add_subparsers(dest="repack_command", required=True)
    repack_tex = repack_sub.add_parser("texture", help="replace a Texture2D's pixels")
    repack_tex.add_argument("archive", help="path to a Unity archive (bundle, assets, resS)")
    repack_tex.add_argument("asset", help="asset path inside the archive, e.g. textures/icon_0")
    repack_tex.add_argument("image", help="replacement image file (png/jpg/bmp/webp/tga/dds)")
    repack_tex.add_argument("-o", "--out", required=True, help="output directory (never the source)")
    repack_tex.set_defaults(repack_handler=_cmd_repack_texture)
    repack_txt = repack_sub.add_parser("text", help="replace a TextAsset's payload")
    repack_txt.add_argument("archive", help="path to a Unity archive")
    repack_txt.add_argument("asset", help="asset path inside the archive")
    repack_txt.add_argument("file", help="replacement text file")
    repack_txt.add_argument("-o", "--out", required=True, help="output directory (never the source)")
    repack_txt.set_defaults(repack_handler=_cmd_repack_text)
    repack_font = repack_sub.add_parser("font", help="replace a Font's embedded TTF/OTF bytes")
    repack_font.add_argument("archive", help="path to a Unity archive")
    repack_font.add_argument("asset", help="asset path inside the archive")
    repack_font.add_argument("font", help="replacement .ttf or .otf file")
    repack_font.add_argument("-o", "--out", required=True, help="output directory (never the source)")
    repack_font.set_defaults(repack_handler=_cmd_repack_font)
    return parser


def _cmd_detect(args: argparse.Namespace) -> int:
    detection = detect(args.path)
    if detection is None:
        print(f"unable to identify: {args.path}")
        return 1
    print(detection.summary())
    return 0


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

        if args.driver == "auto":
            driver = registry.match(args.path)
        else:
            driver = registry.get(args.driver)
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
    except (ValueError, Exception) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"detected: {result.detected.engine}/{result.detected.kind}")
    if driver is not None:
        print(f"driver:  {driver.name} ({driver.label})")
    print(f"extracted {result.ok} assets to {args.out}")
    for error in result.errors:
        print(f"warning: {error}", file=sys.stderr)
    return 0 if not result.errors else 2


def _cmd_world(args: argparse.Namespace) -> int:

    from dualforge.export.usd import write_usd_world
    from dualforge.unity import UnityArchive

    try:
        archive = UnityArchive(args.path)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    meshes = list(archive.world_meshes())
    if not meshes:
        print(f"no readable meshes in {args.path}", file=sys.stderr)
        return 1
    textures = list(archive.world_textures())
    try:
        write_usd_world(
            args.out,
            meshes,
            textures=textures,
            scene_name=args.scene,
            up_axis=args.up_axis,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    printed = len(textures)
    print(
        f"wrote {args.out}: {len(meshes)} mesh prims (skinned exporters ignored), "
        f"{printed} textures placed in textures/"
    )
    return 0


def _cmd_il2cpp_inspect(args: argparse.Namespace) -> int:
    from pathlib import Path

    from dualforge.il2cpp import MAX_SUPPORTED, MIN_SUPPORTED, MetadataError, parse_metadata

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
    from pathlib import Path

    from dualforge.il2cpp import MetadataError, dump_strings

    try:
        count, out = dump_strings(Path(args.path).read_bytes(), args.out)
    except (OSError, MetadataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if out:
        print(f"wrote {count} string literals to {out}")
    return 0


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


def _cmd_codecs(args: argparse.Namespace) -> int:
    for method in METHODS:
        status = "available" if is_available(method) else "missing"
        print(f"{method:10s} {status}")
    return 0


def _cmd_usmap_validate(args: argparse.Namespace) -> int:
    from dualforge.unreal.usmap import parse_usmap

    try:
        mappings = parse_usmap(open(args.path, "rb").read())
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
        mappings = parse_usmap(open(args.path, "rb").read())
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
    open(out, "wb").write(data)
    print(f"wrote {out} ({len(data)} bytes)")
    return 0


def _cmd_usmap_names(args: argparse.Namespace) -> int:
    from dualforge.unreal.usmap import parse_usmap

    try:
        mappings = parse_usmap(open(args.path, "rb").read())
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
        from pathlib import Path

        target = Path(args.dir)
        target.mkdir(parents=True, exist_ok=True)
        count = registry.export_all(str(target))
        print(f"exported {count} driver(s) to {target}")
        return 0
    if args.all:
        target = args.out or "drivers"
        from pathlib import Path

        Path(target).mkdir(parents=True, exist_ok=True)
        count = registry.export_all(target)
        print(f"exported {count} driver(s) to {target}")
        return 0
    if args.builtin:
        target = args.out or "drivers"
        from pathlib import Path

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
    from pathlib import Path

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


def _cmd_locres_dump(args: argparse.Namespace) -> int:
    from pathlib import Path

    from dualforge.unreal.locres import parse_locres_file

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

    from dualforge.unreal.locres import apply_replacements, parse_locres_file, save_locres

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
    except (OSError, LookupError, Exception) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"replaced Texture2D '{args.asset}' -> {args.out}")
    return 0


def _cmd_repack_text(args: argparse.Namespace) -> int:
    from pathlib import Path

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


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler:
        return handler(args)
    key_handler = getattr(args, "key_handler", None)
    if key_handler:
        return key_handler(args)
    usmap_handler = getattr(args, "usmap_handler", None)
    if usmap_handler:
        return usmap_handler(args)
    driver_handler = getattr(args, "driver_handler", None)
    if driver_handler:
        return driver_handler(args)
    crack_handler = getattr(args, "crack_handler", None)
    if crack_handler:
        return crack_handler(args)
    locres_handler = getattr(args, "locres_handler", None)
    if locres_handler:
        return locres_handler(args)
    il2cpp_handler = getattr(args, "il2cpp_handler", None)
    if il2cpp_handler:
        return il2cpp_handler(args)
    repack_handler = getattr(args, "repack_handler", None)
    if repack_handler:
        return repack_handler(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
