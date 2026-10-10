import struct, pathlib

d = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks")
magic = b"\x86\x75\x64\x53"
files = sorted(d.glob("*.pak"), key=lambda p: p.stat().st_size)
sel = files[:8] + files[len(files)//2:len(files)//2+2] + files[-2:]
for p in sel:
    n = p.stat().st_size
    with p.open("rb") as f:
        f.seek(max(0, n - 4096)); tail = f.read()
    base = n - len(tail)
    i = tail.rfind(magic)
    if i < 0:
        print(f"{p.name:45s} no magic in last 4KB"); continue
    hdr = tail[i-20:i+41]
    print(f"{p.name:45s} size={n:12d} magic_at={base+i} dist_eof={n-(base+i)}")
    print("   hdr61:", hdr.hex())
    rest = hdr[25:]
    for name, lens in (("8,8,20", (8, 8, 20)), ("8,20,8", (8, 20, 8)), ("20,8,8", (20, 8, 8))):
        if lens == (8, 8, 20):
            size, off = struct.unpack("<qq", rest[:16]); h = rest[16:]
        elif lens == (8, 20, 8):
            size, = struct.unpack("<q", rest[:8]); h = rest[8:28]; off, = struct.unpack("<q", rest[28:])
        else:
            h = rest[:20]; size, off = struct.unpack("<qq", rest[20:])
        print(f"     {name}: off={off} size={size} sane={0 <= off < n and 0 < size < n} delta={n-221-(off+size) if 0<=off<n and 0<size<n else '-'}")
    print()
