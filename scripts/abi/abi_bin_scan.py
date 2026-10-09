import pathlib, sys

PATTERNS = {
    "abi_ini_key": bytes([0x97, 0x67, 0x87, 0xDE, 0xEA, 0x18, 0x47, 0x0D]),
    "abi_uasset_key37": bytes([0x43, 0x23, 0x07, 0x67, 0x19, 0xAB, 0xAC, 0xEF]),
    "abi_uasset_key38": bytes([0x3C, 0x17, 0x08, 0xD5, 0xBD, 0x80, 0xD8, 0x15]),
    "abi_mobile_index_key": bytes([0xF3, 0x7F, 0x02, 0xC1, 0x8B, 0x29, 0x5E, 0x5B]),
    "sm4_sbox_head": bytes([0xD6, 0x90, 0xE9, 0xFE, 0xCC, 0xE1, 0x3D, 0xB7, 0x16, 0xB6, 0x14, 0xC2, 0x28, 0xFB, 0x2C, 0x05]),
    "abi_tableA_head": bytes(range(0x10, 0x28)) + bytes([0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F]),
    "abi_magic_ascii": b"ArenaBreakout",
    "abi_xor_const": bytes([0x93, 0x93, 0x93]),  # placeholder, unused below
}
del PATTERNS["abi_xor_const"]

roots = [
    pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Binaries\Win64"),
    pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\Engine\Binaries\Win64"),
    pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite"),
]

files = []
seen = set()
for r in roots:
    if not r.exists():
        print(f"(missing {r})")
        continue
    for p in r.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".exe", ".dll") and p.stat().st_size > 0:
            if p.resolve() in seen:
                continue
            seen.add(p.resolve())
            files.append(p)

print(f"scanning {len(files)} binaries")
hits = {k: [] for k in PATTERNS}
for p in files:
    try:
        data = p.read_bytes()
    except OSError as e:
        print(f"skip {p}: {e}")
        continue
    for name, pat in PATTERNS.items():
        start = 0
        while True:
            i = data.find(pat, start)
            if i < 0:
                break
            hits[name].append((str(p), i))
            start = i + 1

for name, locs in hits.items():
    print(f"\n== {name}: {len(locs)} hit(s)")
    for path, off in locs[:20]:
        print(f"   {path} +0x{off:X}")
