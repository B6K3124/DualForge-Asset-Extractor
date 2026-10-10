import struct, pathlib
PakDir = pathlib.Path(r'E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks')
def idxblock(p):
    size=p.stat().st_size
    with open(p,'rb') as fh:
        fh.seek(size-221); f=fh.read(221)
        magic=struct.unpack_from('<I',f,16)[0]
        enc=f[20]
        eo,es=struct.unpack_from('<Q',f,45)[0],struct.unpack_from('<Q',f,53)[0]
        def dec(e,x): return ((((e^0xD72CAC4E59907DA0)>>23)|((e^0xD72CAC4E59907DA0)<<41))&0xFFFFFFFFFFFFFFFF)^x
        off=dec(eo,0xD3A512); sz=dec(es,0xB640093C)
        fh.seek(off); return fh.read(80), sz, magic, enc, off
for p in list(PakDir.glob('patch/*.pak'))[:3] + list(PakDir.glob('pakchunk9[0-9]-*.pak'))[:2]:
    try:
        b,sz,magic,enc,off=idxblock(p)
    except Exception as e:
        print(p.name,'ERR',e); continue
    L=struct.unpack_from('<i',b,0)[0]
    print(f'{p.name}\n  magic=0x{magic:08X} enc={enc} off={off} sz={sz} len={L} head={b[:70]!r}')
