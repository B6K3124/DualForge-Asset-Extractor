import pefile, capstone

PATH = r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Binaries\Win64\GameLoader.exe"
pe = pefile.PE(PATH, fast_load=True)
ib = pe.OPTIONAL_HEADER.ImageBase

# VA of the ABI table cluster (section2: VA 0x1402B1000, raw 0x2A4C00)
def file_to_va(off):
    for s in pe.sections:
        if s.PointerToRawData <= off < s.PointerToRawData + s.SizeOfRawData:
            return ib + s.VirtualAddress + (off - s.PointerToRawData)
    return None

ini_key_va = file_to_va(0x30B710)
sbox_va = file_to_va(0x30B720)
tableA_va = file_to_va(0x30B820)
cand_va = file_to_va(0x30B870)
print(f"ini_key VA=0x{ini_key_va:X} sbox VA=0x{sbox_va:X} tableA VA=0x{tableA_va:X} cand VA=0x{cand_va:X}")

TARGETS = {"ini_key": ini_key_va, "sbox": sbox_va, "tableA": tableA_va, "cand": cand_va}

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
md.detail = True

xrefs = {k: [] for k in TARGETS}
for s in pe.sections:
    if not (s.Characteristics & 0x20000000):  # IMAGE_SCN_MEM_EXECUTE
        continue
    va = ib + s.VirtualAddress
    try:
        data = pe.get_data(s.VirtualAddress, s.SizeOfRawData)
    except Exception:
        continue
    for ins in md.disasm(data, va):
        # only consider instructions with a memory operand whose displacement matches target
        for op in ins.operands:
            if op.type == capstone.x86.X86_OP_MEM:
                disp = op.mem.disp
                # RIP-relative: effective = ins.address + ins.size + disp
                if op.mem.base == capstone.x86.X86_REG_RIP:
                    eff = ins.address + ins.size + disp
                else:
                    eff = disp
                for name, tva in TARGETS.items():
                    if tva is not None and eff == tva:
                        xrefs[name].append((ins.address, ins.mnemonic, ins.op_str))

for name, locs in xrefs.items():
    print(f"\n== {name} (0x{TARGETS[name]:X}): {len(locs)} xref(s)")
    for addr, mnem, ops in locs[:15]:
        print(f"   0x{addr:X}: {mnem} {ops}")
