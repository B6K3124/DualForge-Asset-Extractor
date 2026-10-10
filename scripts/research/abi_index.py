import pathlib, re, struct

p = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks\pakchunk91-WindowsNoEditor.pak")
n = p.stat().st_size
with p.open("rb") as f:
    tail = f.read()   # last 4096 already
    f.seek(0); head = f.read(4096)

def strs(b, k=5):
    return [m.group().decode('latin1') for m in re.finditer(rb'[\x20-\x7e]{%d,}' % k, b)]

def xor93(b):
    return bytes(x ^ 0x93 if x not in (0, 0x93) else x for x in b)

print("== tail region (last 4KB) raw strings:", strs(tail)[:20])
print("== tail region xor93 strings:", strs(xor93(tail))[:30])
print("== head raw strings:", strs(head)[:30])
print("== head xor93 strings:", strs(xor93(head))[:30])

# index likely ends at n-221 ; scan backwards in 8KB windows
foot = 221
for back in (8192, 65536, 1048576, 8388608, 33554432):
    start = max(0, n - foot - back)
    with p.open("rb") as f:
        f.seek(start); blk = f.read(min(back, 65536))
    for name, dec in (("raw", lambda x: x), ("x93", xor93)):
        s = strs(dec(blk), 8)
        if s:
            print(f"back={back:9d} {name}: {s[:6]}")
            break
