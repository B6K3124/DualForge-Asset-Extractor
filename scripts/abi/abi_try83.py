import struct, pathlib
from Crypto.Cipher import AES
import abi_trykeys as T   # reuse SM4 impl + plausible + idxblock
T.PakDir = pathlib.Path(r'E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks')

def idxblock(p):
    size=p.stat().st_size
    with open(p,'rb') as fh:
        fh.seek(size-221); f=fh.read(221)
        eo,es=struct.unpack_from('<Q',f,45)[0],struct.unpack_from('<Q',f,53)[0]
        def dec(e,x): return ((((e^0xD72CAC4E59907DA0)>>23)|((e^0xD72CAC4E59907DA0)<<41))&0xFFFFFFFFFFFFFFFF)^x
        off=dec(eo,0xD3A512); sz=dec(es,0xB640093C)
        fh.seek(off); return fh.read(64), sz

keys = {
 'page83': bytes.fromhex('1F5E4191BDE73F9C65A48D8AA0648C46C06C08F9853093C7EBF4AA5CA22F0486'),
}
for pak in ['pakchunk0-WindowsNoEditor.pak','pakchunk92-WindowsNoEditor.pak','pakchunk12-WindowsNoEditor.pak']:
    C,sz=idxblock(T.PakDir/pak)
    print(f'--- {pak} sz={sz}')
    for name,k in keys.items():
        for lbl,pt in [
            ('SM4_A16', T.decrypt(C,T.keyA(k[:16]))),
            ('SM4_none16', T.decrypt(C,k[:16])),
            ('AES256', AES.new(k,AES.MODE_ECB).decrypt(C[:64])),
            ('AES128', AES.new(k[:16],AES.MODE_ECB).decrypt(C[:64])),
        ]:
            print(f'   {name} {lbl:10}: {pt[:40].hex()} | {T.plausible(pt)}')
