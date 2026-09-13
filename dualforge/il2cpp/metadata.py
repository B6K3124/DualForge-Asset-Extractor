"""IL2CPP `global-metadata.dat` inspector and string dump.

A dependency-free take on the "il2cppdumper" metadata reverse-engineering:
parse the metadata header (magic + version + section pointer table) and
enumerate the string-literal pool exactly the way il2cppdumper's ``-nns``
preset does for Unity 2019+ (global-metadata format versions 22-33).

The header begins with a u32 magic (0xFAB11BAF) and an i32 version. The
remainder is ``Il2CppGlobalMetadataHeader`` — a table of ``(offset: u32,
size: i32)`` pairs, one per section, emitted in a fixed order. Some fields
are version-gated and skipped for versions outside their band (e.g.
``metadataUsage*`` drop out at v27+, ``windowsRuntimeStrings`` appears at
v27+). The modern combined string-literal pool is split into:

* ``stringLiteral``     - an array of ``StringLiteral { u32 length; int dataIndex }``
* ``stringLiteralData`` - the packed UTF-8 bytes that ``dataIndex`` points into

Unknown/unsupported versions still report their basic header and do not
crash; only known layouts produce string output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterator

MAGIC = 0xFAB11BAF
MIN_SUPPORTED = 22
MAX_SUPPORTED = 33

# Field order of the serialized ``Il2CppGlobalMetadataHeader`` section table,
# exactly mirroring the official layout. Each entry is ``(name, min_ver,
# max_ver)``; ``None`` means unbounded. The header always begins with the
# 8-byte ``(u32 sanity, i32 version)``, after which every field's (offset,
# size) pair is emitted in declaration order, skipping fields whose
# version band excludes the file's metadata version. Several later fields are
# gated (e.g. ``metadataUsage*`` vanish from v27+, ``windowsRuntimeStrings``
# is introduced in v27).
HEADER_ORDER = (
    ("stringLiteral", None, None),
    ("stringLiteralData", None, None),
    ("string", None, None),
    ("events", None, None),
    ("properties", None, None),
    ("methods", None, None),
    ("parameterDefaultValues", None, None),
    ("fieldDefaultValues", None, None),
    ("fieldAndParameterDefaultValueData", None, None),
    ("fieldMarshaledSizes", None, None),
    ("parameters", None, None),
    ("fields", None, None),
    ("genericParameters", None, None),
    ("genericParameterConstraints", None, None),
    ("genericContainers", None, None),
    ("nestedTypes", None, None),
    ("interfaces", None, None),
    ("vtableMethods", None, None),
    ("interfaceOffsets", None, None),
    ("typeDefinitions", None, None),
    ("rgctxEntries", None, 24),
    ("images", None, None),
    ("assemblies", None, None),
    ("metadataUsageLists", 19, 24),
    ("metadataUsagePairs", 19, 24),
    ("fieldRefs", 19, None),
    ("referencedAssemblies", 20, None),
    ("attributesInfo", 21, 27),
    ("attributeTypes", 21, 27),
    ("attributeData", 29, None),
    ("attributeDataRange", 29, None),
    ("unresolvedVirtualCallParameterTypes", 22, None),
    ("unresolvedVirtualCallParameterRanges", 22, None),
    ("windowsRuntimeTypeNames", 23, None),
    ("windowsRuntimeStrings", 27, None),
    ("exportedTypeDefinitions", 24, None),
)


class MetadataError(Exception):
    pass


# Each ``StringLiteral`` is 8 bytes: a u32 byte ``length`` followed by a u32
# ``dataIndex`` (offset into the string blob).
_LITERAL_ENTRY = 8


@dataclass
class MetadataInfo:
    version: int
    sections: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def string_literal_count(self) -> int:
        """Number of managed string literals.

        The section table stores a *byte size* for the ``stringLiteral``
        array; the count is that size divided by the per-entry width (8).
        """
        return self.sections.get("stringLiteral", (0, 0))[1] // _LITERAL_ENTRY

    @property
    def string_count(self) -> int:
        """Byte size of the ``string`` (packed names) section."""
        return self.sections.get("string", (0, 0))[1]

    @property
    def type_definition_count(self) -> int:
        """Byte size of the ``typeDefinitions`` section table.

        This is a byte size in the pointer table (each entry is a fixed-size
        struct), not a raw count; expose it as such rather than pretend it is
        a precise element count.
        """
        return self.sections.get("typeDefinitions", (0, 0))[1]


def parse_metadata(data: bytes) -> MetadataInfo:
    """Parse the global-metadata header and section pointer table."""
    if len(data) < 8:
        raise MetadataError("file is too small to be IL2CPP metadata")
    magic, version = _u32(data, 0), _i32(data, 4)
    if magic != MAGIC:
        raise MetadataError(
            f"not an IL2CPP metadata file (magic 0x{magic:08X}, expected 0x{MAGIC:08X})"
        )
    sections: dict[str, tuple[int, int]] = {}
    cursor = 8
    for name, lo, hi in HEADER_ORDER:
        if lo is not None and version < lo:
            continue
        if hi is not None and version > hi:
            continue
        if cursor + 8 > len(data):
            break
        offset, size = _u32(data, cursor), _i32(data, cursor + 4)
        sections[name] = (offset, size)
        cursor += 8
    return MetadataInfo(version=version, sections=sections)


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little", signed=False)


def _i32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little", signed=True)


def iter_string_literals(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield ``(index, raw_bytes)`` for every string literal in the pool.

    Works on the modern combined layout (v22+): a ``StringLiteral`` array
    plus a dense byte blob. Each 8-byte entry is ``{ u32 length; int
    dataIndex }`` (length first); ``dataIndex`` is relative to the start of
    the ``stringLiteralData`` blob. Literals are read as UTF-8 with the
    stored length; invalid regions are skipped defensively.
    """
    info = parse_metadata(data)
    if info.version < MIN_SUPPORTED or info.version > MAX_SUPPORTED:
        raise MetadataError(
            f"metadata version {info.version} is outside the supported 22-33 range"
        )
    literal_off, literal_size = info.sections.get("stringLiteral", (0, 0))
    data_off, data_size = info.sections.get("stringLiteralData", (0, 0))
    literal_count = literal_size // _LITERAL_ENTRY
    if literal_count <= 0 or data_off <= 0 or data_size <= 0:
        raise MetadataError("metadata carries no readable string-literal pool")
    if literal_off + literal_size > len(data):
        raise MetadataError("string-literal table runs past the end of the file")
    blob_end = min(data_off + data_size, len(data))
    for index in range(literal_count):
        entry = literal_off + index * 8
        length = _u32(data, entry)
        data_index = _i32(data, entry + 4)
        if data_index < 0:
            continue
        start = data_off + data_index
        end = min(start + length, blob_end)
        if start >= blob_end:
            break
        yield index, data[start:end]


def string_text(raw: bytes) -> str:
    """Decode a string-literal payload, replacing anything non-textual."""
    return raw.decode("utf-8", "replace")


def dump_strings(data: bytes, out_path: str | None = None, prefix: str = "0x") -> tuple[int, str | None]:
    """Dump every string literal to stdout or a file (il2cppdumper -nns style).

    Returns ``(count, path_or_None)``.
    """
    lines: list[str] = []
    for index, raw in iter_string_literals(data):
        label = f"{prefix}{index:08X}"
        lines.append(f"{label} {string_text(raw)}")
    if out_path:
        Path(out_path).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return len(lines), out_path
    print("\n".join(lines))
    return len(lines), None


__all__ = [
    "MAGIC",
    "MAX_SUPPORTED",
    "MIN_SUPPORTED",
    "MetadataError",
    "MetadataInfo",
    "dump_strings",
    "iter_string_literals",
    "parse_metadata",
    "string_text",
]