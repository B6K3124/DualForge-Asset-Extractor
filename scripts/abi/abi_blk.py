import struct, pathlib
PakDir = pathlib.Path(r'E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks')
def idxhead(p, n=64):
    size=p.stat().st_size
    with open(p,'rb') as fh:
        fh.seek(size-221); f=fh.read(221)
        eo,es=struct.unpack_from('<Q',f,45)[0],struct.unpack_from('<Q',f,53)[0]
        def dec(e,x): return ((((e^0xD72CAC4E59907DA0)>>23)|((e^0xD72CAC4E59907DA0)<<41))&0xFFFFFFFFFFFFFFFF)^x
        off=dec(eo,0xD3A512)
        fh.seek(off); return fh.read(n)
paks=sorted(PakDir.glob('pakchunk*-WindowsNoEditor.pak'))
heads=[]
for p in paks:
    try: heads.append((p.name, idxhead(p)))
    except Exception as e: print(p.name,'ERR',e)
print(f'{len(heads)} base paks')
# constant-first-16 checks
first16={h[:16] for _,h in heads}
print('distinct first-16:',len(first16))
second16={h[16:32] for _,h in heads}
print('distinct 16-31:',len(second16))
for b in sorted(second16):
    print('  blk1',b.hex())
print('\nSample heads:')
for n,h in heads[:20]:
    print(f'{n:38} {h[:48].hex()}')
