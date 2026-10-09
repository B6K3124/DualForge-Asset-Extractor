import pathlib

p = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks\pakchunk92-WindowsNoEditor.pak")
d = p.read_bytes()
n = len(d)

def dump(a, b, label):
    print(f"\n=== {label}  [{a}:{b}] ===")
    for i in range(a, b, 32):
        chunk = d[i:i+32]
        asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)
        print(f"{i:06x}  {chunk.hex(' '):<96} {asc}")

dump(0x60, 0x140, "start after header")
dump(0x5b00, 0x5f00, "low-entropy region")
dump(0x5f00, 0x6100, "second header-like")
dump(47800, n, "pre-footer + footer")
