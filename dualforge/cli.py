"""Command-line interface for DualForge.

Argument parsing lives in :func:`build_parser`; dispatch lives in
:func:`main`. The ``_cmd_*`` handlers have been factored out into
:mod:`dualforge.cli_commands` (one module per sub-command) and are
re-exported here so ``dualforge.cli._cmd_*`` stays the public entry point.
"""

from __future__ import annotations

import argparse
import sys

from dualforge import __version__
from dualforge.cli_commands import (
    _cmd_codecs,
    _cmd_crack_run,
    _cmd_crack_status,
    _cmd_detect,
    _cmd_drivers_create,
    _cmd_drivers_export,
    _cmd_drivers_import,
    _cmd_drivers_list,
    _cmd_drivers_match,
    _cmd_drivers_show,
    _cmd_extract,
    _cmd_il2cpp_inspect,
    _cmd_il2cpp_strings,
    _cmd_keys_add,
    _cmd_keys_import,
    _cmd_keys_list,
    _cmd_keys_remove,
    _cmd_keys_schemes,
    _cmd_keys_sync,
    _cmd_keys_test,
    _cmd_locres_dump,
    _cmd_locres_edit,
    _cmd_repack_font,
    _cmd_repack_text,
    _cmd_repack_texture,
    _cmd_usmap_dump,
    _cmd_usmap_names,
    _cmd_usmap_repack,
    _cmd_usmap_validate,
    _cmd_world,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualforge",
        description="Universal Unity + Unreal game asset extraction toolkit.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose debug logging to stderr")
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


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from dualforge.log import setup_logging

    setup_logging(verbose=bool(getattr(args, "verbose", False)))
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