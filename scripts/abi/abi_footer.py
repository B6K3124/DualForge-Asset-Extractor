import struct, sys, hashlib, pathlib

p = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks\pakchunk91-WindowsNoEditor.pak")
data = p.read_bytes()
n = len(data)
print("file", p.name, "size", n)

region = data[n - 221:]          # 160 methods + 61 header
print("methods table:", region[61:61+96].rstrip(b"\x00"))

# layout A: CUE4Parse ABI branch  guid(16) magic(4) enc(1) size(8) off(8) hash(20) ver(4)
guidA   = region[0:16]
magicA, = struct.unpack("<I", region[16:20])
encA    = region[20]
sizeA, offA = struct.unpack("<qq", region[21:37])
hashA   = region[37:57]
verA,   = struct.unpack("<i", region[57:61])
print("A: magic=%08x enc=%d size=%d off=%d ver=%d" % (magicA, encA, sizeA, offA, verA))

# layout B: guid(16) ver(4) magic(4) enc(1) size(8) off(8) hash(20)
magicB, = struct.unpack("<I", region[20:24])
encB    = region[24]
sizeB, offB = struct.unpack("<qq", region[25:41])
hashB   = region[41:61]
print("B: magic=%08x enc=%d size=%d off=%d" % (magicB, encB, sizeB, offB))

for label, off, size, h in (("A", offA, sizeA, hashA), ("B", offB, sizeB, hashB)):
    ok_off = 0 <= off < n
    end = off + size if ok_off else -1
    print(f"{label}: off={off} size={size} end={end} (footer starts at {n-221}) delta={n-221-end}")
    if ok_off and 0 < size < n:
        blob = data[off:off+size]
        print("   sha1 =", hashlib.sha1(blob).hexdigest(), " indexhash =", h.hex())
        print("   first 32 bytes:", blob[:32].hex())
