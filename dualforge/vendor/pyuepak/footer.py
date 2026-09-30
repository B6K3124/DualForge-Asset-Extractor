from .file_io import Reader, Writer
from .version import PakVersion
from .utils import COMPRESSION, from_name, UnsupportedPakVersion

from io import SEEK_END

import logging

logger = logging.getLogger("pyuepak.footer")

PAK_MAGIC = 0x5A6F12E1


#: Distance from end-of-file to the footer magic, oldest layout first. Probed in
#: order; the first hit wins. UE 6.0 grew the footer by appending fields *after*
#: the compression-name table, which moves the magic further from the end.
#: Mirrors CUE4Parse's OffsetsToTry (FPakInfo.cs).
MAGIC_OFFSETS_FROM_END: tuple[tuple[int, bool], ...] = (
    (44, False),  # versions 1-7: version stored raw
    (172, True),  # V8A: implicit
    (204, True),  # V8B, 10, 11, 12: version stored as value - 1
    (205, True),  # V9: implicit
    (245, True),  # UE 6.0
)

#: Raw footer versions we understand. 15 == UE 6.0.
_MAX_RAW_VERSION = 15


def locate_footer(reader: Reader) -> tuple[int, int, bool]:
    """Return ``(magic_pos, raw_version, stored_minus_one)`` for the pak footer.

    Probing by magic rather than by a hardcoded seek keeps this working for
    footers whose size changed between engine versions - the reason UE 6.0
    paks were previously rejected outright.
    """
    size = reader.get_size()
    for offset, minus_one in MAGIC_OFFSETS_FROM_END:
        if offset > size:
            continue
        pos = size - offset
        reader.set_pos(pos)
        if reader.uint32() != PAK_MAGIC:
            continue
        if minus_one:
            return pos, reader.uint32(), True
        return pos, reader.uint32(), False
    raise UnsupportedPakVersion(
        "no pak footer magic found at any known offset - this is not a "
        "recognised Unreal .pak file (it may be truncated, or an IoStore "
        "container such as .utoc instead of a .pak)."
    )


def check_pak_version(reader: Reader) -> PakVersion:
    """Check the version of the pak file."""

    _, raw, minus_one = locate_footer(reader)

    if minus_one:
        value = raw + 1
        # A V8A footer carries no version field; its raw value is 0.
        if raw == 0:
            return PakVersion.V8A
    else:
        value = raw
    if raw == 0 and minus_one:
        value = PakVersion.V8A

    try:
        return PakVersion(value)
    except ValueError as exc:
        if raw > _MAX_RAW_VERSION:
            raise UnsupportedPakVersion(
                f"pak declares footer version {raw} (v{value}), which is newer "
                f"than the newest version this reader supports "
                f"(v{int(PakVersion.V13) - 1} = UE 6.0). "
                f"Use the CUE4Parse/uex bridge for this archive."
            ) from exc
        raise UnsupportedPakVersion(
            f"pak declares footer version {raw}, which is not a recognised pak "
            f"layout. Use the CUE4Parse/uex bridge for this archive."
        ) from exc


class Footer:
    def __init__(self):
        self.encryption_key = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        self.is_encrypted = False
        self.version = None
        self.index_offset = 0
        self.index_size = 0
        self.hash = None
        self.is_frozen = False
        self.compresion = None
        self.compression_names: list[str] = []

    def read(self, reader: Reader):
        """Read the footer of the pak file."""

        magic_pos, _, _ = locate_footer(reader)
        self.version = check_pak_version(reader)

        # Read forward from the located magic rather than seeking to a
        # hardcoded offset from EOF: the encryption-key GUID and the magic have
        # both moved between engine versions.
        reader.set_pos(magic_pos - 17)
        self.encryption_key = reader.read(16)
        self.is_encrypted = reader.uint8() == 1

        magic = reader.uint32()
        if magic != PAK_MAGIC:  # pragma: no cover - locate_footer just checked
            logger.error("Invalid pak file magic: %X", magic)
            raise UnsupportedPakVersion(
                f"pak footer magic is invalid (0x{magic:08X}, expected "
                f"0x{PAK_MAGIC:08X}) - the archive is corrupt or truncated."
            )

        reader.uint32()  # version, already resolved above

        self.index_offset = reader.uint64()
        self.index_size = reader.uint64()

        self.hash = reader.sha1()

        if self.version == PakVersion.V9:
            self.is_frozen = reader.uint8()

        char_count = 0
        if self.version == PakVersion.V8A:
            char_count = 4
        elif self.version >= PakVersion.V8B:
            char_count = 5

        # Build the compression-name table. This mirrors CUE4Parse's
        # FPakInfo.ReadPakInfo exactly (see external/uex/external/CUE4Parse/
        # CUE4Parse/UE4/Pak/Objects/FPakInfo.cs:514-529):
        #
        #   * slot 0 is an implicit CompressionMethod.None - the table on disk
        #     lists only the *additional* methods, and an entry's 6-bit index
        #     refers to this compacted list, not to the physical slot.
        #   * empty slots are skipped rather than reserving a position, which is
        #     why a footer with an all-zero name table still decodes (every
        #     entry resolves to None).
        #
        # Do not "fix" this into a positional lookup: real UE paks rely on the
        # compaction, and doing so breaks every pak with an empty table.
        #
        # An unrecognised name maps to COMPRESSION.UNSUPPORTED instead of
        # raising, so a pak that merely *lists* an exotic method still lists
        # (and lets us extract) the entries that don't use it. The error is
        # raised where an entry actually selects it.
        self.compresion = [COMPRESSION.NONE]
        self.compression_names = ["None"]
        raw = reader.read(32 * char_count)
        for i in range(char_count):
            name_bytes = raw[i * 32 : (i + 1) * 32]
            name = name_bytes.split(b"\x00")[0].decode(errors="replace").strip()
            if not name:
                continue
            method = from_name(name)
            if method is COMPRESSION.UNSUPPORTED:
                logger.warning(
                    "pak footer declares unknown compression method %r "
                    "(table index %d) - entries using it cannot be decoded natively",
                    name,
                    len(self.compresion),
                )
            self.compresion.append(method)
            self.compression_names.append(name)

        logger.debug(
            "Footer:"
            f"\n  Version: {self.version.name}"
            f"\n  Index Offset: {self.index_offset}"
            f"\n  Index Size: {self.index_size}"
            f"\n  Hash: {self.hash.hex()}"
            f"\n  Is Frozen: {self.is_frozen}"
            f"\n  Is Encrypted: {self.is_encrypted}"
            f"\n  Compression: {self.compresion}"
        )

    def write(
        self,
        writer: Writer,
        version: PakVersion,
        offset,
        size,
        hash,
        compression_names: list[str] | None = None,
    ):
        if version >= PakVersion.V7:
            writer.uint128(0)
        if version >= PakVersion.V4:
            writer.uint8(0)  # is encrypted TODO

        writer.uint32(PAK_MAGIC)

        if version >= 9:
            writer.uint32(version - 1)
        else:
            writer.uint32(version)

        writer.uint64(offset)
        writer.uint64(size)
        writer.write(hash)

        if version == PakVersion.V9:
            writer.uint8(0)  # is frozen

        algo_size = 0
        if version == PakVersion.V8A:
            algo_size = 4
        elif version > PakVersion.V8A:
            algo_size = 5

        names = list(compression_names or [])
        if len(names) > algo_size:
            raise ValueError(
                f"pak footer can declare at most {algo_size} compression "
                f"methods, got {len(names)}: {names!r}"
            )

        # Only the *additional* methods are stored: slot 0 is an implicit
        # None, matching the reader's compaction (see above).
        table = bytearray()
        for name in names:
            raw = name.encode()
            if len(raw) > 32:
                raise ValueError(f"compression method name too long: {name!r}")
            table += raw.ljust(32, b"\x00")
        table += b"\x00" * (32 * algo_size - len(table))
        writer.write(bytes(table))
