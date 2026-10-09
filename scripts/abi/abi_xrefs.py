import pefile, capstone

PATH = r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Binaries\Win64\GameLoader.exe"
pe = pefile.PE(PATH, fast_load=True)
ib = pe.OPTIONAL_HEADER.ImageBase
print(f"ImageBase 0x{ib:X}")
text = None
for s in pe.sections:
    name = s.Name.rstrip(b"\x00").decode(errors="replace")
    va = ib + s.VirtualAddress
    print(f"  {name:<8} VA=0x{va:X} vsize=0x{s.Misc_VirtualSize:X} rawsize=0x{s.SizeOfRawData:X} raw=0x{s.PointerToRawData:X}")
    if name == ".text":
        text = s

TARGETS = {
    "ini_key": 0x30B710,
    "sbox": 0x30B720,
    "tableA": 0x30B820,
    "cand16_0x30B870": 0x30B870,
    "cand_0x30B700": 0x30B700,
}

text_va = ib + text.VirtualAddress
text_data = pe.get_data(text.VirtualAddress, text.SizeOfRawData)

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = False

xrefs = {k: [] for k in TARGETS}
for ins in md.disasm(text_data, text_va):
    op = ins.op_str
    for name, va in TARGETS.items():
        if hex(va) in op or f"0x{va:x}" in op:
            xrefs[name].append(ins.address)
for name, addrs in xrefs.items():
    print(f"\n== xrefs to {name} (0x{TARGETS[name]:X}): {len(addrs)}")
    for a in addrs[:12]:
        print(f"   code VA 0x{a:X} (file 0x{a - ib + text.PointerToRawData:X})")
