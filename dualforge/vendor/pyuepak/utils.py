from enum import Enum


class UnsupportedCompressionMethod(Exception):
    """Raised when a pak uses a compression method DualForge cannot decode.

    Carries the offending name so the error can name it instead of the
    misleading "the archive may be encrypted" that the upstream reader used
    to produce for any footer read failure.
    """

    def __init__(self, name: str, known: tuple[str, ...] = ()):
        self.name = name
        self.known = known
        detail = f" (supported: {', '.join(known)})" if known else ""
        super().__init__(
            f"pak uses the '{name}' compression method, which DualForge cannot "
            f"decode natively{detail}. Open the archive with the CUE4Parse/uex "
            f"bridge instead (it will be used automatically if available)."
        )


class UnsupportedPakVersion(Exception):
    """Raised when the pak footer declares a version this reader cannot parse.

    Distinct from a decryption failure so callers can tell "too new for the
    native reader" apart from "wrong key" and "unsupported compression".
    """

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class COMPRESSION(Enum):
    """Unreal's EPakCompressionMethod.

    Values are the UE enum value plus one, which preserves the upstream
    ``COMPRESSION(raw + 1)`` convention used for legacy (pre-v8B) pak entries
    where the compression is stored as a raw numeric flag.

    ``UNSUPPORTED`` is a DualForge sentinel, not a real UE method: it marks a
    footer-declared compression name we do not recognise. It is only an error
    if an entry actually selects it, so paks that merely *list* an exotic method
    in the footer still list and extract the entries that don't use it.
    """

    NONE = 1  # UE 0
    ZLIB = 2  # UE 1
    GZIP = 3  # UE 2
    CUSTOM = 4  # UE 3
    OODLE = 5  # UE 4
    LZ4 = 6  # UE 5
    LZO = 7  # UE 6
    ZSTD = 8  # UE 7
    XB1ZLIB = 9  # UE 8
    XBOXONEGDKZLIB = 10  # UE 9
    BROTLI = 11  # UE 10
    PWC = 12  # UE 11
    UNSUPPORTED = 13


#: Footer compression-name (uppercased) -> enum member.
COMPRESSION_BY_NAME: dict[str, COMPRESSION] = {
    "NONE": COMPRESSION.NONE,
    "ZLIB": COMPRESSION.ZLIB,
    "GZIP": COMPRESSION.GZIP,
    "CUSTOM": COMPRESSION.CUSTOM,
    "OODLE": COMPRESSION.OODLE,
    "LZ4": COMPRESSION.LZ4,
    "LZO": COMPRESSION.LZO,
    "ZSTD": COMPRESSION.ZSTD,
    "XB1ZLIB": COMPRESSION.XB1ZLIB,
    "XBOXONEGDKZLIB": COMPRESSION.XBOXONEGDKZLIB,
    "BROTLI": COMPRESSION.BROTLI,
    "PWC": COMPRESSION.PWC,
}

#: The sentinel member itself, exported for tests and callers that want to
#: assert a footer table resolved cleanly.
UNSUPPORTED_MARKER = COMPRESSION.UNSUPPORTED

#: Methods DualForge can actually decode, delegated to dualforge.compression.
NATIVE_METHODS: dict[COMPRESSION, str] = {
    COMPRESSION.ZSTD: "zstd",
    COMPRESSION.LZ4: "lz4",
    COMPRESSION.BROTLI: "brotli",
}

#: Methods that are recognised but have no decoder. These need the uex bridge.
BRIDGE_ONLY_METHODS = (
    COMPRESSION.CUSTOM,
    COMPRESSION.LZO,
    COMPRESSION.PWC,
    COMPRESSION.XB1ZLIB,
    COMPRESSION.XBOXONEGDKZLIB,
)


def from_name(name: str) -> COMPRESSION:
    """Map a footer compression name to an enum member.

    Unknown names become ``COMPRESSION.UNSUPPORTED`` rather than raising, so a
    footer listing a method we don't know does not prevent listing files.
    """
    return COMPRESSION_BY_NAME.get((name or "").strip().upper(), COMPRESSION.UNSUPPORTED)


def from_ue_value(raw: int) -> COMPRESSION:
    """Map a raw legacy numeric compression flag to an enum member."""
    try:
        return COMPRESSION(raw + 1)
    except ValueError as exc:
        raise UnsupportedCompressionMethod(f"unknown method id {raw}") from exc


class hybrid_method:
    def __init__(self, func):
        self.func = func

    def __get__(self, obj, cls=None):
        def wrapper(*args, **kwargs):
            instance = obj if obj is not None else cls()
            return self.func(instance, *args, **kwargs)

        return wrapper


def fnv64(data_bytes: bytes, offset: int) -> int:
    OFFSET = 0xCBF29CE484222325
    PRIME = 0x00000100000001B3
    hash_ = (OFFSET + offset) & 0xFFFFFFFFFFFFFFFF

    for b in data_bytes:
        hash_ ^= b
        hash_ = (hash_ * PRIME) & 0xFFFFFFFFFFFFFFFF  # simulate u64 wrapping

    return hash_


def fnv64_path(path: str, offset: int) -> int:
    lower = path.lower()
    utf16le = lower.encode("utf-16le")  # match encode_utf16 + to_le_bytes
    return fnv64(utf16le, offset)


def split_path_child(path: str) -> tuple[str, str] | None:
    if path == "/" or not path:
        return None

    # Remove trailing slash if present
    path = path.rstrip("/")

    idx = path.rfind("/")
    if idx != -1:
        return (path[: idx + 1], path[idx + 1 :])
    else:
        return ("/", path)
