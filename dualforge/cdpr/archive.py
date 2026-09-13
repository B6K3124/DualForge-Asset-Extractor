"""REDengine 4 archive reader (.archive) for Cyberpunk 2077.

Reads the RDAR container format used by CD Projekt RED's REDengine 4.
Files are addressed by 64-bit FNV-1a hashes; original depot paths are not
stored in the archive and require an external hash database for recovery.

Layout
------
Header (40 bytes):
    char[4]   magic            "RDAR"
    uint32    version          currently 12
    uint64    indexPosition    offset of the file-list index
    uint32    indexSize        byte length of the index
    uint64    debugPosition    (unused, 0)
    uint32    debugSize        (unused, 0)
    uint64    fileSize         total size excluding this 40-byte header

Index (at indexPosition):
    Header (28 bytes):
        uint32    fileTableOffset       always 8
        uint32    fileTableSize
        uint64    crc
        uint32    fileEntryCount
        uint32    fileSegmentCount
        uint32    resourceDependencyCount
    FileEntry (56 bytes each):
        uint64    nameHash64            FNV-1a hash of the depot path
        int64     timestamp             Windows FILETIME
        uint32    numInlineBufferSegments
        uint32    segmentsStart         index into the segment table
        uint32    segmentsEnd           exclusive
        uint32    resourceDependenciesStart
        uint32    resourceDependenciesEnd
        uint8[20] sha1Hash
    FileSegment (16 bytes each):
        uint64    offset                absolute byte offset in the archive
        uint32    zSize                 compressed size (0 if uncompressed)
        uint32    size                  uncompressed size

Compression
-----------
Each segment is either stored raw (zSize == size) or wrapped in a KARK
block: 4-byte magic "KARK" + 4-byte LE uncompressed size, followed by a
raw Kraken stream.  Decompression uses the Oodle DLL via ctypes
(``dualforge.compression.oodle``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator

RDAR_MAGIC = b"RDAR"

_KARK_MAGIC = b"KARK"

_HEADER_SIZE = 40
_INDEX_HEADER_SIZE = 28
_FILE_ENTRY_SIZE = 56
_SEGMENT_SIZE = 16


class RedError(Exception):
    """Raised when a REDengine archive cannot be parsed or extracted."""


@dataclass
class _Segment:
    offset: int
    z_size: int
    size: int


@dataclass
class _FileEntry:
    name_hash: int
    timestamp: int
    num_inline: int
    segments_start: int
    segments_end: int
    res_dep_start: int
    res_dep_end: int
    sha1: bytes


class RedArchive:
    """Read-only container for a REDengine 4 .archive file."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self._content = fh.read()

        if len(self._content) < _HEADER_SIZE:
            raise RedError("file too small for a REDengine archive header")

        magic = self._content[:4]
        if magic != RDAR_MAGIC:
            raise RedError(f"not a REDengine archive (magic: {magic!r})")

        self.version = struct.unpack_from("<I", self._content, 4)[0]
        index_pos = struct.unpack_from("<Q", self._content, 8)[0]
        index_size = struct.unpack_from("<I", self._content, 16)[0]
        self._file_size = struct.unpack_from("<Q", self._content, 32)[0]

        if index_pos + index_size > len(self._content):
            raise RedError(
                f"index out of bounds (pos={index_pos}, size={index_size}, "
                f"file={len(self._content)})"
            )

        index_data = self._content[index_pos : index_pos + index_size]
        self._parse_index(index_data)

    # ── index parsing ───────────────────────────────────────────────
    def _parse_index(self, data: bytes) -> None:
        if len(data) < _INDEX_HEADER_SIZE:
            raise RedError("index header truncated")

        self._file_table_offset = struct.unpack_from("<I", data, 0)[0]
        self._file_table_size = struct.unpack_from("<I", data, 4)[0]
        self._crc = struct.unpack_from("<Q", data, 8)[0]
        entry_count = struct.unpack_from("<I", data, 16)[0]
        seg_count = struct.unpack_from("<I", data, 20)[0]
        self._res_dep_count = struct.unpack_from("<I", data, 24)[0]

        entry_off = _INDEX_HEADER_SIZE
        entries_end = entry_off + entry_count * _FILE_ENTRY_SIZE
        if entries_end > len(data):
            raise RedError("file entries extend beyond index")

        self._entries: list[_FileEntry] = []
        for i in range(entry_count):
            off = entry_off + i * _FILE_ENTRY_SIZE
            e = data[off : off + _FILE_ENTRY_SIZE]
            self._entries.append(
                _FileEntry(
                    name_hash=struct.unpack_from("<Q", e, 0)[0],
                    timestamp=struct.unpack_from("<q", e, 8)[0],
                    num_inline=struct.unpack_from("<I", e, 16)[0],
                    segments_start=struct.unpack_from("<I", e, 20)[0],
                    segments_end=struct.unpack_from("<I", e, 24)[0],
                    res_dep_start=struct.unpack_from("<I", e, 28)[0],
                    res_dep_end=struct.unpack_from("<I", e, 32)[0],
                    sha1=e[36:56],
                )
            )

        seg_off = entries_end
        segs_end = seg_off + seg_count * _SEGMENT_SIZE
        if segs_end > len(data):
            raise RedError("segments extend beyond index")

        self._segments: list[_Segment] = []
        for i in range(seg_count):
            off = seg_off + i * _SEGMENT_SIZE
            s = data[off : off + _SEGMENT_SIZE]
            self._segments.append(
                _Segment(
                    offset=struct.unpack_from("<Q", s, 0)[0],
                    z_size=struct.unpack_from("<I", s, 8)[0],
                    size=struct.unpack_from("<I", s, 12)[0],
                )
            )

    # ── public API ──────────────────────────────────────────────────
    @property
    def file_count(self) -> int:
        return len(self._entries)

    def list_files(self) -> Iterator[str]:
        """Yield hex-hash pseudo-paths for every entry (``<hash>.bin``)."""
        for entry in self._entries:
            yield self._hash_to_name(entry.name_hash)

    def list_file_hashes(self) -> list[int]:
        """Return the raw 64-bit FNV-1a hashes of every entry."""
        return [e.name_hash for e in self._entries]

    def get_entry(self, name: str) -> _FileEntry | None:
        """Look up an entry by its hex-hash pseudo-path."""
        try:
            hash_int = int(Path(name).stem, 16)
        except (ValueError, AttributeError):
            return None
        for entry in self._entries:
            if entry.name_hash == hash_int:
                return entry
        return None

    def open_file(self, name: str) -> bytes:
        """Return the raw (decompressed) bytes of a single entry."""
        entry = self.get_entry(name)
        if entry is None:
            raise RedError(f"no such entry: {name}")
        return self._read_entry(entry)

    def extract_file(self, name: str, out_dir: str) -> str:
        """Extract a single entry to *out_dir*, returning the written path."""
        from dualforge.export.exporter import write_entry

        return write_entry(out_dir, name, self.open_file(name))

    def extract_all(
        self, out_dir: str, progress: object | None = None
    ) -> list[str]:
        """Extract every entry. Returns written file paths."""
        from dualforge.export import Exporter

        exporter = Exporter(out_dir)
        written: list[str] = []
        total = len(self._entries)
        for idx, entry in enumerate(self._entries):
            name = self._hash_to_name(entry.name_hash)
            if progress:
                progress(idx, total, name)
            try:
                data = self._read_entry(entry)
            except RedError:
                continue
            written.append(exporter.write(name, data))
        return written

    # ── internal ────────────────────────────────────────────────────
    def _read_entry(self, entry: _FileEntry) -> bytes:
        if entry.segments_start >= entry.segments_end:
            raise RedError(
                f"entry has no segments (hash=0x{entry.name_hash:016x})"
            )

        parts: list[bytes] = []
        for i in range(entry.segments_start, entry.segments_end):
            if i >= len(self._segments):
                raise RedError(f"segment index {i} out of range")
            seg = self._segments[i]
            parts.append(self._read_segment(seg))
        return b"".join(parts)

    def _read_segment(self, seg: _Segment) -> bytes:
        if seg.offset + seg.z_size > len(self._content):
            raise RedError(
                f"segment out of bounds (offset=0x{seg.offset:x}, "
                f"zsize={seg.z_size}, file={len(self._content)})"
            )

        raw = self._content[seg.offset : seg.offset + seg.z_size]

        if seg.z_size == seg.size or seg.z_size == 0:
            return raw

        if len(raw) >= 8 and raw[:4] == _KARK_MAGIC:
            return self._decompress_kark(raw, seg.size)

        return raw

    @staticmethod
    def _decompress_kark(block: bytes, expected_size: int) -> bytes:
        """Decompress a KARK (Kraken) block.

        Layout: ``[4: KARK][4: decompressed_size LE][compressed payload...]``
        The compressed payload is a raw Oodle Kraken stream.
        """
        if len(block) < 8:
            raise RedError("KARK block too small")
        payload = block[8:]
        if not payload:
            raise RedError("KARK block has no compressed payload")
        try:
            from dualforge.compression.oodle import Oodle

            return Oodle().decompress(payload, expected_size)
        except Exception as exc:
            raise RedError(f"KARK decompression failed: {exc}") from exc

    @staticmethod
    def _hash_to_name(hash_int: int) -> str:
        return f"{hash_int:016x}.bin"

    def __repr__(self) -> str:
        return (
            f"RedArchive({self.path!r}, version={self.version}, "
            f"files={self.file_count})"
        )
