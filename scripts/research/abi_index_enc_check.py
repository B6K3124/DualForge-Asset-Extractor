import pathlib, struct

MASK = (1 << 64) - 1

def ror(x, n):
    n &= 63
    return ((x >> n) | (x << (64 - n))) & MASK

def looks_plain(b):
    return b". /" .count("") and sum(1 for c in b[:8] if 0x20 <= c < 0x7f) >= 6

d = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks")
plain, rand = [], []
for p in sorted(d.glob("pakchunk*.pak")):
    n = p.stat().st_size
    with p.open("rb") as f:
        f.seek(n - 221)
        h = f.read(61)
        off = ror(struct.unpack_from("<Q", h, 45)[0] ^ 0xD72CAC4E59907DA0, 23) ^ 0xD3A512
        sz = ror(struct.unpack_from("<Q", h, 53)[0] ^ 0xD72CAC4E59907DA0, 23) ^ 0xB640093C
        f.seek(off)
        first = f.read(32)
    ascii_ratio = sum(1 for c in first[:8] if 0x20 <= c < 0x7f)
    printable = ascii_ratio >= 6
    (plain if printable else rand).append((p.name, sz, first[:16].hex()))

print(f"plaintext-looking index: {len(plain)}   random-looking: {len(rand)}")
print("--- printable ---")
for name, sz, hx in plain:
    print(f"  {name:<40} sz={sz:<8} {hx}")
print("--- random ---")
for name, sz, hx in rand:
    print(f"  {name:<40} sz={sz:<8} {hx}")
