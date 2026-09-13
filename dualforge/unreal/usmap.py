from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

from dualforge.compression import CompressionError, decompress

# CUE4Parse usmap format (magic 0x30C4, EUsmapVersion.ExplicitEnumValues).
# Spec source: CUE4Parse/MappingsProvider/Usmap/*.cs (Apache-2.0).

USMAP_MAGIC = 0x30C4


class UsmapVersion(IntEnum):
    Initial = 0
    PackageVersioning = 1
    LongFName = 2
    LargeEnums = 3
    ExplicitEnumValues = 4

    Latest = ExplicitEnumValues


class UsmapCompression(IntEnum):
    None_ = 0
    Oodle = 1
    Brotli = 2
    ZStandard = 3


@dataclass
class UsmapCustomVersion:
    file_version: int
    licensee_version: int
    guid: bytes
    friendly_name: int


@dataclass
class UsmapPackageVersioning:
    package_file_version: int
    package_licensee_version: int
    custom_versions: list[UsmapCustomVersion] = field(default_factory=list)
    net_cl: int = 0


@dataclass
class UsmapPropertyType:
    kind: str
    struct_type: str | None = None
    inner: UsmapPropertyType | None = None
    value: UsmapPropertyType | None = None
    enum_name: str | None = None


@dataclass
class UsmapProperty:
    index: int
    array_dim: int
    name: str
    type: UsmapPropertyType


@dataclass
class UsmapStruct:
    name: str
    super_type: str | None = None
    property_count: int = 0
    properties: list[UsmapProperty] = field(default_factory=list)


@dataclass
class UsmapMappings:
    names: list[str] = field(default_factory=list)
    enums: dict[str, dict[int, str]] = field(default_factory=dict)
    structs: dict[str, UsmapStruct] = field(default_factory=dict)
    versioning: UsmapPackageVersioning | None = None
    version: UsmapVersion = UsmapVersion.Latest


class UsmapError(Exception):
    pass


# --------------------------------------------------------------------- reading

def parse_usmap(data: bytes) -> UsmapMappings:
    """Parse a CUE4Parse usmap file (magic 0x30C4) into a UsmapMappings."""
    if len(data) < 2:
        raise UsmapError("usmap is empty")
    magic, = struct.unpack_from("<H", data, 0)
    if magic != USMAP_MAGIC:
        raise UsmapError(f"invalid usmap magic 0x{magic:04X} (expected 0x{USMAP_MAGIC:04X})")
    try:
        version = UsmapVersion(data[2])
    except ValueError as exc:
        raise UsmapError(f"unsupported usmap version {data[2]}") from exc
    if version > UsmapVersion.Latest:
        raise UsmapError(f"unsupported usmap version {version}")
    offset = 3
    versioning = None
    if version >= UsmapVersion.PackageVersioning:
        # CUE4Parse reads this flag with ReadBoolean() = Read<int>() (4 bytes).
        has_versioning, = struct.unpack_from("<i", data, offset)
        if has_versioning not in (0, 1):
            raise UsmapError(f"invalid usmap versioning flag {has_versioning}")
        offset += 4
        if has_versioning:
            file_version, licensee_version = struct.unpack_from("<ii", data, offset)
            offset += 8
            count, = struct.unpack_from("<i", data, offset)
            offset += 4
            custom = []
            for _ in range(count):
                cv_file, cv_licensee = struct.unpack_from("<ii", data, offset)
                offset += 8
                guid = data[offset:offset + 16]
                offset += 16
                friendly, = struct.unpack_from("<i", data, offset)
                offset += 4
                custom.append(UsmapCustomVersion(cv_file, cv_licensee, guid, friendly))
            net_cl, = struct.unpack_from("<I", data, offset)
            offset += 4
            versioning = UsmapPackageVersioning(file_version, licensee_version, custom, net_cl)
    try:
        compression = UsmapCompression(data[offset])
    except ValueError as exc:
        raise UsmapError(f"unsupported usmap compression method 0x{data[offset]:02X}") from exc
    offset += 1
    comp_size, decomp_size = struct.unpack_from("<II", data, offset)
    offset += 8
    if offset + comp_size > len(data):
        raise UsmapError("usmap truncated: compressed payload exceeds file size")
    if compression == UsmapCompression.None_:
        if comp_size != decomp_size:
            raise UsmapError("no compression: compressed size must equal decompressed size")
        payload = data[offset:offset + comp_size]
    else:
        method = _compression_kind(compression)
        try:
            payload = decompress(data[offset:offset + comp_size], method, output_size=decomp_size)
        except Exception as exc:
            raise UsmapError(f"usmap decompression failed ({method}): {exc}") from exc
        if len(payload) != decomp_size:
            raise UsmapError(
                f"usmap decompressed to {len(payload)} bytes, expected {decomp_size}"
            )
    return _parse_payload(payload, version, versioning)


def _compression_kind(compression: UsmapCompression) -> str:
    if compression == UsmapCompression.ZStandard:
        return "zstd"
    if compression == UsmapCompression.Brotli:
        return "brotli"
    if compression == UsmapCompression.Oodle:
        return "oodle"
    raise UsmapError(f"unsupported usmap compression method {compression}")


class _Reader:
    def __init__(self, data: bytes, version: UsmapVersion):
        self.data = data
        self.offset = 0
        self.version = version

    def u8(self) -> int:
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u16(self) -> int:
        value, = struct.unpack_from("<H", self.data, self.offset)
        self.offset += 2
        return value

    def u32(self) -> int:
        value, = struct.unpack_from("<I", self.data, self.offset)
        self.offset += 4
        return value

    def i32(self) -> int:
        value, = struct.unpack_from("<i", self.data, self.offset)
        self.offset += 4
        return value

    def u64(self) -> int:
        value, = struct.unpack_from("<Q", self.data, self.offset)
        self.offset += 8
        return value

    def name(self, lut: list[str]) -> str | None:
        index = self.i32()
        return lut[index] if index != -1 else None

    def string(self) -> str:
        length = self.u16() if self.version >= UsmapVersion.LongFName else self.u8()
        value = self.data[self.offset:self.offset + length].decode("utf-8", errors="replace")
        self.offset += length
        return value


def _parse_payload(
    payload: bytes, version: UsmapVersion, versioning: UsmapPackageVersioning | None
) -> UsmapMappings:
    reader = _Reader(payload, version)
    names = [reader.string() for _ in range(reader.u32())]

    enums: dict[str, dict[int, str]] = {}
    for _ in range(reader.u32()):
        enum_name = reader.name(names)
        if enum_name is None:
            continue
        count = reader.u16() if version >= UsmapVersion.LargeEnums else reader.u8()
        values: dict[int, str] = {}
        if version >= UsmapVersion.ExplicitEnumValues:
            for _ in range(count):
                value = reader.u64()
                entry = reader.name(names)
                if entry is not None:
                    values[value] = entry
        else:
            for index in range(count):
                entry = reader.name(names)
                if entry is not None:
                    values[index] = entry
        enums.setdefault(enum_name, values)

    structs: dict[str, UsmapStruct] = {}
    for _ in range(reader.u32()):
        usmap_struct = _parse_struct(reader, names)
        structs[usmap_struct.name] = usmap_struct
    return UsmapMappings(
        names=names, enums=enums, structs=structs,
        versioning=versioning, version=version,
    )


def _parse_struct(reader: _Reader, names: list[str]) -> UsmapStruct:
    name = reader.name(names)
    if name is None:
        raise UsmapError("struct with null name")
    super_type = reader.name(names)
    property_count = reader.u16()
    serializable_count = reader.u16()
    properties: list[UsmapProperty] = []
    for _ in range(serializable_count):
        properties.append(_parse_property(reader, names))
    return UsmapStruct(
        name=name, super_type=super_type,
        property_count=property_count, properties=properties,
    )


def _parse_property(reader: _Reader, names: list[str]) -> UsmapProperty:
    index = reader.u16()
    array_dim = reader.u8()
    prop_name = reader.name(names)
    if prop_name is None:
        raise UsmapError("property with null name")
    prop_type = _parse_property_type(reader, names)
    return UsmapProperty(index=index, array_dim=array_dim, name=prop_name, type=prop_type)


_PROPERTY_KINDS = (
    "ByteProperty", "BoolProperty", "IntProperty", "FloatProperty", "ObjectProperty",
    "NameProperty", "DelegateProperty", "DoubleProperty", "ArrayProperty",
    "StructProperty", "StrProperty", "TextProperty", "InterfaceProperty",
    "MulticastDelegateProperty", "WeakObjectProperty", "LazyObjectProperty",
    "AssetObjectProperty", "SoftObjectProperty", "UInt64Property", "UInt32Property",
    "UInt16Property", "Int64Property", "Int16Property", "Int8Property", "MapProperty",
    "SetProperty", "EnumProperty", "FieldPathProperty", "OptionalProperty",
    "Utf8StrProperty", "AnsiStrProperty", "ClassProperty",
    "MulticastInlineDelegateProperty", "SoftClassProperty", "VerseStringProperty",
    "VerseDynamicProperty", "VerseFunctionProperty",
)


def _parse_property_type(reader: _Reader, names: list[str]) -> UsmapPropertyType:
    type_byte = reader.u8()
    if type_byte >= len(_PROPERTY_KINDS):
        raise UsmapError(f"unknown property type byte 0x{type_byte:02X}")
    kind = _PROPERTY_KINDS[type_byte]
    struct_type = inner = value = enum_name = None
    if kind == "EnumProperty":
        inner = _parse_property_type(reader, names)
        enum_name = reader.name(names)
    elif kind == "StructProperty":
        struct_type = reader.name(names)
    elif kind in ("SetProperty", "ArrayProperty", "OptionalProperty"):
        inner = _parse_property_type(reader, names)
    elif kind == "MapProperty":
        inner = _parse_property_type(reader, names)
        value = _parse_property_type(reader, names)
    return UsmapPropertyType(
        kind=kind, struct_type=struct_type, inner=inner, value=value, enum_name=enum_name
    )


# --------------------------------------------------------------------- writing

def build_usmap(
    mappings: UsmapMappings,
    version: UsmapVersion = UsmapVersion.Latest,
    compression: UsmapCompression = UsmapCompression.ZStandard,
) -> bytes:
    """Serialize UsmapMappings into CUE4Parse usmap bytes.

    The name LUT is rebuilt from every referenced name (CUE4Parse maps game
    names to LUT indices itself when loading).
    """
    lut, index_of = _build_name_lut(mappings)
    writer = _Writer(version)
    writer.u32(len(lut))
    for name in lut:
        raw = name.encode("utf-8")
        if version >= UsmapVersion.LongFName:
            writer.u16(len(raw))
        else:
            writer.u8(len(raw))
        writer.bytes(raw)

    writer.u32(len(mappings.enums))
    for enum_name, values in mappings.enums.items():
        writer.i32(index_of[enum_name])
        if version >= UsmapVersion.LargeEnums:
            writer.u16(len(values))
        else:
            writer.u8(len(values))
        if version >= UsmapVersion.ExplicitEnumValues:
            for value, name in values.items():
                writer.u64(value)
                writer.i32(index_of[name])
        else:
            for name in values.values():
                writer.i32(index_of[name])

    writer.u32(len(mappings.structs))
    for usmap_struct in mappings.structs.values():
        _write_struct(writer, usmap_struct, index_of)

    payload = writer.data()
    header = bytearray()
    header += struct.pack("<H", USMAP_MAGIC)
    header += bytes([version])
    if version >= UsmapVersion.PackageVersioning:
        if mappings.versioning is None:
            header += struct.pack("<i", 0)
        else:
            header += struct.pack("<i", 1)
            header += struct.pack("<ii", mappings.versioning.package_file_version,
                                  mappings.versioning.package_licensee_version)
            header += struct.pack("<i", len(mappings.versioning.custom_versions))
            for custom in mappings.versioning.custom_versions:
                header += struct.pack("<ii", custom.file_version, custom.licensee_version)
                header += custom.guid
                header += struct.pack("<i", custom.friendly_name)
            header += struct.pack("<I", mappings.versioning.net_cl)
    if compression == UsmapCompression.None_:
        header += bytes([compression])
        header += struct.pack("<II", len(payload), len(payload))
        return bytes(header) + payload
    method = _compression_kind(compression)
    try:
        from dualforge.compression import compress
    except ImportError as exc:
        raise UsmapError(f"compression backend unavailable: {exc}") from exc
    try:
        compressed = compress(payload, method)
    except CompressionError as exc:
        raise UsmapError(f"usmap compression failed ({method}): {exc}") from exc
    header += bytes([compression])
    header += struct.pack("<II", len(compressed), len(payload))
    return bytes(header) + compressed


def _build_name_lut(mappings: UsmapMappings):
    names: list[str] = list(mappings.names)
    index_of: dict[str, int] = {name: i for i, name in enumerate(names)}

    def add(name: str | None) -> None:
        if name is None:
            return
        if name not in index_of:
            index_of[name] = len(names)
            names.append(name)

    for enum_name, values in mappings.enums.items():
        add(enum_name)
        for name in values.values():
            add(name)
    for usmap_struct in mappings.structs.values():
        add(usmap_struct.name)
        add(usmap_struct.super_type)
        for prop in usmap_struct.properties:
            add(prop.name)
            _collect_type_names(prop.type, add)
    return names, index_of


def _collect_type_names(prop_type: UsmapPropertyType, add) -> None:
    add(prop_type.struct_type)
    add(prop_type.enum_name)
    if prop_type.inner is not None:
        _collect_type_names(prop_type.inner, add)
    if prop_type.value is not None:
        _collect_type_names(prop_type.value, add)


class _Writer:
    def __init__(self, version: UsmapVersion):
        self.version = version
        self.buffer = bytearray()

    def u8(self, value: int) -> None:
        if not 0 <= value <= 0xFF:
            raise UsmapError(f"u8 value out of range: {value}")
        self.buffer.append(value)

    def u16(self, value: int) -> None:
        self.buffer += struct.pack("<H", value)

    def u32(self, value: int) -> None:
        self.buffer += struct.pack("<I", value)

    def i32(self, value: int) -> None:
        self.buffer += struct.pack("<i", value)

    def u64(self, value: int) -> None:
        self.buffer += struct.pack("<Q", value)

    def bytes(self, raw: bytes) -> None:
        self.buffer += raw

    def data(self) -> bytes:
        return bytes(self.buffer)


def _write_struct(writer: _Writer, struct: UsmapStruct, index_of: dict[str, int]) -> None:
    writer.i32(index_of.get(struct.name, -1))
    writer.i32(index_of.get(struct.super_type, -1) if struct.super_type else -1)
    writer.u16(struct.property_count)
    writer.u16(len(struct.properties))
    for prop in struct.properties:
        writer.u16(prop.index)
        writer.u8(prop.array_dim)
        writer.i32(index_of.get(prop.name, -1))
        _write_property_type(writer, prop.type, index_of)


def _write_property_type(
    writer: _Writer, prop_type: UsmapPropertyType, index_of: dict[str, int]
) -> None:
    writer.u8(_PROPERTY_KINDS.index(prop_type.kind))
    if prop_type.kind == "EnumProperty":
        _write_property_type(writer, prop_type.inner, index_of)
        writer.i32(index_of.get(prop_type.enum_name, -1))
    elif prop_type.kind == "StructProperty":
        writer.i32(index_of.get(prop_type.struct_type, -1))
    elif prop_type.kind in ("SetProperty", "ArrayProperty", "OptionalProperty"):
        _write_property_type(writer, prop_type.inner, index_of)
    elif prop_type.kind == "MapProperty":
        _write_property_type(writer, prop_type.inner, index_of)
        _write_property_type(writer, prop_type.value, index_of)


__all__ = [
    "UsmapCompression",
    "UsmapCustomVersion",
    "UsmapError",
    "UsmapMappings",
    "UsmapPackageVersioning",
    "UsmapProperty",
    "UsmapPropertyType",
    "UsmapStruct",
    "UsmapVersion",
    "build_usmap",
    "parse_usmap",
]