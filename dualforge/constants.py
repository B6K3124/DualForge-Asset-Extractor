"""Shared magic values and format constants for DualForge.

Centralising the byte magics used by the detector, pak reader and key
bruteforcer so a format's signature is defined in exactly one place.
"""

# Unreal Engine pak footer/index magic ("paK").
PAK_MAGIC = 0x5A6F12E1