import pathlib, struct

MASK = (1 << 64) - 1

def ror(x, n):
    n &= 63
    return ((x >> n) | (x << (64 - n))) & MASK

d = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks")
rows = []
for p in sorted(d.glob("pakchunk*.pak")):
    n = p.stat().st_size
    with p.open("rb") as f:
        f.seek(n - 221)
        h = f.read(61)
    ver = struct.unpack_from("<I", h, 16)[0]
    magic = struct.unpack_from("<I", h, 20)[0]
    enc = h[24]
    off = ror(struct.unpack_from("<Q", h, 45)[0] ^ 0xD72CAC4E59907DA0, 23) ^ 0xD3A512
    sz = ror(struct.unpack_from("<Q", h, 53)[0] ^ 0xD72CAC4E59907DA0, 23) ^ 0xB640093C
    rows.append((p.name, n, ver, magic, enc, off, sz))

print(f"{len(rows)} paks")
print("enc=0:", sum(1 for r in rows if r[4] == 0), " enc!=0:", sum(1 for r in rows if r[4] != 0))
print("magic ok:", sum(1 for r in rows if r[3] == 0x53647586))
print("ver values:", sorted({r[2] for r in rows}))
print("gap = (n-221) - (off+sz):")
gaps = [(r[0], (r[1] - 221) - (r[5] + r[6])) for r in rows]
print(" min", min(g for _, g in gaps), "max", max(g for _, g in gaps))
print(" negative gaps:", [g for _, g in gaps if g < 0][:5])
for r in rows[:5]:
    print(" ", r[0], "size", r[1], "ver", r[2], "enc", r[4], "off", r[5], "sz", r[6])
