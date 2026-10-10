import pathlib, struct

MASK = (1 << 64) - 1
def ror(x, n):
    n &= 63
    return ((x >> n) | (x << (64 - n))) & MASK
def rol(x, n):
    n &= 63
    return ((x << n) | (x >> (64 - n))) & MASK

IDX_XOR = 0xD72CAC4E59907DA0

def decode_index_info(encoded, final_xor):
    return ror(encoded ^ IDX_XOR, 23) ^ final_xor

HASH_KEY0 = 0xC360A0B3AC0A1368
def decode_index_hash(h):
    key = HASH_KEY0
    out = bytearray(h)
    for i in range(len(out)):
        out[i] ^= (key >> ((i & 7) * 8)) & 0xFF
        key = rol(key, 7)
    return bytes(out)

paks = sorted(pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks").glob("*.pak"))
sample = paks[:14] + paks[-3:]
for p in sample:
    n = p.stat().st_size
    with p.open("rb") as f:
        f.seek(n - 221)
        hdr = f.read(61)
        guid, ver, magic, enc = struct.unpack_from("<16sIIB", hdr, 0)
        raw_hash = hdr[25:45]
        info = hdr[45:61]
        enc_off, enc_sz = struct.unpack_from("<QQ", info, 0)
        off = decode_index_info(enc_off, 0xD3A512)
        sz = decode_index_info(enc_sz, 0xB640093C)
        h = decode_index_hash(raw_hash)
        tail = b""
        if 0 <= off < n:
            f.seek(off)
            tail = f.read(16)
        print(f"{p.name:<45} n={n:<9} ver={ver:#x} magic={magic:#010x} enc={enc} off={off:<9} size={sz:<8} "
              f"sum_ok={off + sz == n - 221} hash={h.hex()[:24]} idxhead={tail.hex()}")
