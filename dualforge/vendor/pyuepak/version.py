from enum import IntEnum


class PakVersion(IntEnum):
    """Pak version, stored in the footer as ``value - 1``.

    The offset mirrors CUE4Parse's ``EPakFileVersion`` (see
    external/uex/external/CUE4Parse/CUE4Parse/UE4/Pak/Objects/FPakInfo.cs), which
    is the reference implementation these values are kept in step with.
    """

    V1 = 1
    V2 = 2
    V3 = 3
    V4 = 4
    V5 = 5
    V6 = 6
    V7 = 7
    V8A = 8
    V8B = 9
    V9 = 10
    V10 = 11
    V11 = 12
    V12 = 13
    # UE 6.0 appends extra fields after the compression-name table
    # (PakchunkIndex, encryption method). The fields *before* the table are
    # unchanged, so the index/entry layout is still the V12 one.
    V13 = 16
