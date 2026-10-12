"""Generate a tiny but valid PE32+ (x64) test binary for the key hunter.

The .rdata section embeds a 64-char (32-byte) AES key hex string directly
followed by the AES S-box — the exact layout the entropy pass and the
signature scan are designed to find. Nothing runs; the entry point is a
single RET.

Usage: python scripts/ghidra/make_test_pe.py [output.exe]
"""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path


def aes_sbox() -> bytes:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "dualforge_ghidra_key_finder",
        Path(__file__).with_name("ghidra_key_finder.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return bytes(module.AES_SBOX)


def build_pe(key_hex: str, sbox: bytes, extra: bytes = b"") -> bytes:
    text_code = b"\xc3"  # ret
    text_vsize = 0x100
    text_raw_size = 0x200
    rdata_raw_size = 0x200

    blob = key_hex.encode("ascii") + b"\x00" * 8 + sbox + extra
    assert len(blob) <= rdata_raw_size

    dos = b"MZ" + b"\x00" * 58 + struct.pack("<I", 0x40)
    coff_offset = 0x40
    opt_offset = coff_offset + 4 + 20  # noqa: F841 - kept for readability of the layout

    machine = 0x8664
    n_sections = 2
    characteristics = 0x0022
    coff = struct.pack(
        "<HHIIIHH",
        machine,
        n_sections,
        0,
        0,
        0,
        0xF0,
        characteristics,
    )
    opt = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B,  # PE32+
        0, 0,  # linker version
        text_raw_size,  # SizeOfCode
        rdata_raw_size,  # SizeOfInitializedData
        0,  # SizeOfUninitializedData
        0x1000,  # AddressOfEntryPoint
        0x1000,  # BaseOfCode
        0x140000000,  # ImageBase
        0x1000,  # SectionAlignment
        0x200,  # FileAlignment
        6, 0,  # OS version
        0, 0,  # Image version
        6, 0,  # Subsystem version
        0,  # Win32VersionValue
        0x3000,  # SizeOfImage
        0x200,  # SizeOfHeaders
        0,  # CheckSum
        3,  # Subsystem: console
        0,  # DllCharacteristics
        0x100000, 0x1000,  # stack reserve/commit
        0x100000, 0x1000,  # heap reserve/commit
        0,  # LoaderFlags
        16,  # NumberOfRvaAndSizes
    )
    opt += b"\x00" * (16 * 8)  # empty data directories

    sec = b""
    sec += b".text\x00\x00\x00" + struct.pack(
        "<IIIIIIHHI",
        text_vsize,
        0x1000,
        text_raw_size,
        0x200,
        0,
        0,
        0,
        0,
        0x60000020,
    )
    sec += b".rdata\x00\x00" + struct.pack(
        "<IIIIIIHHI",
        len(blob),
        0x2000,
        rdata_raw_size,
        0x400,
        0,
        0,
        0,
        0,
        0x40000040,
    )

    header = dos + b"PE\x00\x00" + coff + opt + sec
    assert len(header) <= 0x200
    header = header.ljust(0x200, b"\x00")
    raw_text = text_code.ljust(text_raw_size, b"\x00")
    raw_rdata = blob.ljust(rdata_raw_size, b"\x00")
    return header + raw_text + raw_rdata


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test_key_hunt.exe")
    key_hex = hashlib.sha256(b"dualforge-e2e-key").hexdigest()
    pe = build_pe(key_hex, aes_sbox())
    out.write_bytes(pe)
    print(f"wrote {out} ({len(pe)} bytes)")
    print(f"embedded key: {key_hex}")
    return 0


if __name__ == "__main__":
    sys.exit(main())