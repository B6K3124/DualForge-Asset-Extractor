import pathlib

d = pathlib.Path(r"E:\SteamLibrary\steamapps\common\ABInfinite\ABInfinite\Content\Paks")
for name in ("pakchunk91-WindowsNoEditor.pak", "pakchunk7-WindowsNoEditor.pak", "pakchunk92-WindowsNoEditor.pak"):
    p = d / name
    n = p.stat().st_size
    print(f"\n=== {name} size={n}")
    with p.open("rb") as f:
        for off in (0, 0x6000, 0xC000, 0x12000, 0x18000, 0x1E000, 0x6000 * 10, 0x6000 * 100):
            if off + 128 > n: break
            f.seek(off); b = f.read(96)
            print(f"  @{off:#x}: {b[:48].hex()}  |{b[48:96].hex()}")
