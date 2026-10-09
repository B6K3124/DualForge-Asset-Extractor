import pathlib

d = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks")
files = sorted(d.glob("*.pak"), key=lambda p: p.stat().st_size)
sel = files[:6] + files[len(files)//2:len(files)//2+2] + files[-3:]
tails = []
for p in sel:
    n = p.stat().st_size
    with p.open("rb") as f:
        head = f.read(128)
        f.seek(n - 4096); tail = f.read()
    i = tail.rfind(b"\x86\x75\x64\x53")
    blob = tail[i+1:i+1+36]
    tails.append(blob)
    print(f"{p.name:45s} size={n}")
    print("   head:", head[:64].hex())
    print("   t36 :", blob.hex())

print("\nconstant positions across samples:")
cols = list(zip(*tails))
for i, c in enumerate(cols):
    uniq = set(c)
    print(f"  [{i:2d}] {'CONST %02x' % c[0] if len(uniq)==1 else 'vary   ' + ' '.join(f'{b:02x}' for b in c)}")
