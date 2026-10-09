import math, pathlib, collections

p = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks\pakchunk92-WindowsNoEditor.pak")
d = p.read_bytes()
n = len(d)
print("size", n)

def ent(b):
    if not b: return 0.0
    c = collections.Counter(b)
    tot = len(b)
    return -sum((v/tot)*math.log2(v/tot) for v in c.values())

BS = 1024
print("\nblock entropy:")
for i in range(0, n, BS):
    blk = d[i:i+BS]
    print(f"  {i:8d} ent={ent(blk):.2f} zeros={blk.count(0):4d} {blk[:32].hex()}")

print("\n--- first 512 bytes ---")
for i in range(0, 512, 32):
    print(f"{i:04x}  {d[i:i+32].hex(' ')}")
