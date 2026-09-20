"""Shared magic values and format constants for DualForge.

Centralising the byte magics used by the detector, pak reader and key
bruteforcer so a format's signature is defined in exactly one place.
"""

# Unreal Engine pak footer/index magic ("paK").
PAK_MAGIC = 0x5A6F12E1

# Unreal Engine IoStore table-of-contents magic.
UTOC_MAGIC = b"-==--==--==--==-"

# Bethesda Gamebryo archives.
BSA_MAGIC = b"BSA\x00"
BA2_MAGIC = b"BTD\x00"

# CD Projekt RED REDengine archives.
RDAR_MAGIC = b"RDAR"

# Unreal Engine localization.
LOCRES_MAGIC = 0x324F4352

# Unity IL2CPP metadata.
IL2CPP_METADATA_MAGIC = 0xFAB11BAF

# Texture containers.
DDS_MAGIC = b"DDS "
KTX_MAGIC = b"\xAB" + b"KTX"
KTX2_MAGIC = b"\xABKTX 20\xBB\r\n\x1A\n"
KTX1_MAGIC = b"\xABKTX 11\xBB\r\n\x1A\n"