import struct, pathlib
from Crypto.Cipher import AES

PakDir = pathlib.Path(r'E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks')
def idxblock(p):
    size=p.stat().st_size
    with open(p,'rb') as fh:
        fh.seek(size-221); f=fh.read(221)
        eo,es=struct.unpack_from('<Q',f,45)[0],struct.unpack_from('<Q',f,53)[0]
        def dec(e,x): return ((((e^0xD72CAC4E59907DA0)>>23)|((e^0xD72CAC4E59907DA0)<<41))&0xFFFFFFFFFFFFFFFF)^x
        off=dec(eo,0xD3A512); sz=dec(es,0xB640093C)
        fh.seek(off); return fh.read(64), sz

def plausible(pt):
    if len(pt)<5: return None
    L=struct.unpack_from('<i',pt,0)[0]
    if not (1<=L<=512): return None
    body=pt[4:]
    if L<=len(body):
        s=body[:L]
        if all(32<=c<127 for c in s) and (b'/' in s or b'.' in s): return f"utf8 {s!r}"
    return None

cands = {
 'ABI_global': bytes.fromhex('1C0265C3AFF5DA59A610A67CACC10F6775C6889C88DCA6BF3BA0C6B1383131BA'),
 'ABI_chinese': bytes.fromhex('4BBF15A1C48012FE8DEAF35A2955A4442E30ED1D1ED5E7326C1C65A3E8C6E93F'),
}
for pak in ['pakchunk92-WindowsNoEditor.pak','pakchunk0-WindowsNoEditor.pak','pakchunk12-WindowsNoEditor.pak']:
    C,sz=idxblock(PakDir/pak)
    print(f'--- {pak} indexsize={sz} ct={C[:16].hex()}')
    for name,k in cands.items():
        for bits,kk in (('AES128',k[:16]),('AES256',k)):
            try:
                pt=AES.new(kk,AES.MODE_ECB).decrypt(C[:64])
            except Exception as e:
                print('   err',e); continue
            print(f'   {name:12} {bits}: {pt[:40].hex()} | {plausible(pt)}')
