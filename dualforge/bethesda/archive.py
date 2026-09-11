"""Bethesda archive reader: BSA (v103/104/105) and BA2 (GNRL/DX10).

This is a self-contained, pure-Python container reader for Bethesda Softworks
archive files used by Skyrim (classic + Special Edition), Oblivion, Fallout 3 /
New Vegas / 4 / 76, and Starfield. It supports listing and extracting every
entry; it does not do asset-type re-export conversion.

BSA versions
------------
* ``103`` (0x67) - Oblivion, Fallout 3, Fallout: New Vegas. zlib compression.
* ``104`` (0x68) - Skyrim (2011). zlib compression.
* ``105`` (0x69) - Skyrim Special Edition / Skyrim VR. LZ4 (frame) compression.

BA2 types
---------
* ``GNRL`` - general files (meshes, materials, audio, scripts). zlib per file.
* ``DX10`` - DirectDraw textures stored as mip chunks + a rebuilt DDS header.
* ``GNMF`` - PlayStation GNF textures (treated as a general container).

Layout notes
------------
The exact byte layouts follow the UESP Skyrim Archive File Format reference and
the battle-tested bethesda-structs / BAE readers. Compression is handled through
the shared ``dualforge.compression`` helpers so zlib/lz4 selection is centralised.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Tuple

from dualforge.compression import CompressionError, decompress

BSA_MAGIC = b"BSA\x00"
BA2_MAGIC = b"BTD\x00"

BSA_VER_104 = 0x68
BSA_VER_105 = 0x69

# General archive flags / per-file masks (BSA).
FLAG_DIRECTORIES_NAMED = 0x001
FLAG_FILES_NAMED = 0x002
FLAG_FILES_COMPRESSED = 0x004
FLAG_EMBEDDED_FILE_NAMES = 0x100

FILE_SIZE_MASK = 0x3FFFFFFF
FILE_COMPRESSED_MASK = 0x40000000

# BA2 general entry flag: file data is zlib compressed.
BA2_COMPRESSED = 0x01

VERSION_BY_INT = {
    0x67: 103,
    0x68: 104,
    0x69: 105,
}


class BethesdaError(Exception):
    """Raised when a Bethesda archive cannot be parsed/extracted."""


@dataclass
class _BSAFile:
    path: str
    offset: int
    size: int
    compressed: bool


@dataclass
class _BA2File:
    path: str
    offset: int
    packed_size: int
    unpacked_size: int
    compressed: bool


@dataclass
class _BA2Texture:
    path: str
    offset: int
    name_hash: int
    ext: str
    dir_hash: int
    height: int
    width: int
    mips: int
    format: int
    is_cubemap: bool
    tile: int
    chunks: List[Dict[str, int]] = field(default_factory=list)


def _read_uvarint(data: bytes, pos: int) -> tuple:
    """Decode an LE7-bit variable-length integer (as used for BSA/BA2 names)."""
    result = 0
    shift = 0
    length = len(data)
    while pos < length:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    else:
        raise BethesdaError("truncated variable-length integer")
    return result, pos


class BethesdaArchive:
    """Read-only container for a BSA or BA2 archive."""

    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self._content = fh.read()
        self._magic = self._content[:4]
        if self._magic == BSA_MAGIC:
            self.format = "BSA"
            self._parse_bsa()
        elif self._magic == BA2_MAGIC:
            self.format = "BA2"
            self._parse_ba2()
        else:
            raise BethesdaError(f"not a Bethesda archive (magic: {self._magic!r})")

    # ── metadata ──────────────────────────────────────────────────────
    @property
    def file_count(self) -> int:
        if self.format == "BSA":
            return len(self._bsa_files)
        return len(self._ba2_entries)

    # ── BSA parsing ───────────────────────────────────────────────────
    def _parse_bsa(self) -> None:
        data = self._content
        if len(data) < 32:
            raise BethesdaError("BSA header truncated")

        magic, version, folder_offset, flags, folder_count, file_count, \
            folder_names_len, file_names_len = struct.unpack_from(
                "<4sIIIIIII", data, 0
            )

        if version == 0x67:
            self._parse_bsa_v103(
                folder_offset, flags, folder_count, file_count,
                folder_names_len, file_names_len,
            )
            return

        # v104 (Fallout 4) / v105 (Skyrim SE): 36-byte header adds a
        # file-flags u16 + padding, and every folder record points at its own
        # content block (folder name followed by the folder's 16-byte file
        # records). All file names live in a dedicated NUL-terminated block
        # stored after the content blocks.
        if len(data) < 36:
            raise BethesdaError("BSA header truncated")
        file_flags, = struct.unpack_from("<H", data, 32)
        self.version = VERSION_BY_INT.get(version)
        if self.version is None:
            raise BethesdaError(f"unsupported BSA version 0x{version:x}")

        self.flags = flags
        self.file_flags = file_flags
        self.directory_names_length = folder_names_len
        self.file_names_length = file_names_len

        dirs_named = bool(flags & FLAG_DIRECTORIES_NAMED)
        files_named = bool(flags & FLAG_FILES_NAMED)
        arch_compressed = bool(flags & FLAG_FILES_COMPRESSED)

        # Folder records: v105 pads a u32 before and after the real u32 offset.
        dir_size = 24 if self.version >= 105 else 16
        records_end = folder_offset + folder_count * dir_size
        if records_end > len(data):
            raise BethesdaError("BSA folder records out of range")

        folders: List[Dict[str, int]] = []
        for i in range(folder_count):
            pos = folder_offset + i * dir_size
            if self.version >= 105:
                (hash_, cnt, _pre, offset, _post) = struct.unpack_from(
                    "<QIIII", data, pos
                )
            else:
                (hash_, cnt, offset) = struct.unpack_from("<QII", data, pos)
            folders.append({"hash": hash_, "file_count": cnt, "offset": offset})

        # File-name block: one NUL-terminated string per file, in
        # folder-major/file order, located right after the content blocks.
        # Header folder_names_len counts the name bytes (plus trailing NUL per
        # folder) but not the u8 length byte that prefixes every folder name, so
        # the folder-name region spans folder_names_len + one byte per folder.
        names_region = 0
        if dirs_named:
            names_region = folder_names_len + folder_count
        file_names_offset = records_end + names_region + file_count * 16
        file_names: List[str] = []
        if files_named:
            if file_names_offset > len(data):
                raise BethesdaError("BSA file name table out of range")
            pos = file_names_offset
            for _ in range(file_count):
                end = data.find(b"\x00", pos)
                if end < pos:
                    raise BethesdaError("BSA file name out of range")
                file_names.append(self._clean_bsa_name(data[pos:end]))
                pos = end + 1

        # Each folder's content block = [u8-len folder name + bytes + NUL]
        # followed by 16-byte file records (hash, size+flags, data offset). The
        # stored folder offset points past the file-name block, so the real
        # content offset subtracts total_file_name_length.
        files: List[_BSAFile] = []
        idx = 0
        for folder in folders:
            content_pos = folder["offset"] - file_names_len
            folder_name = ""
            if dirs_named or files_named:
                if content_pos < 0 or content_pos >= len(data):
                    raise BethesdaError("BSA folder name out of range")
                name_len = data[content_pos]
                content_pos += 1
                if content_pos + name_len > len(data):
                    raise BethesdaError("BSA folder name out of range")
                folder_name = self._clean_bsa_name(
                    data[content_pos : content_pos + name_len]
                )
                content_pos += name_len  # no trailing NUL in v104/105 names
            for _ in range(folder["file_count"]):
                if content_pos + 16 > len(data):
                    raise BethesdaError("BSA file record out of range")
                (hash_, size_flags, offset) = struct.unpack_from(
                    "<QII", data, content_pos
                )
                content_pos += 16
                size = size_flags & FILE_SIZE_MASK
                bit_compressed = bool(size_flags & FILE_COMPRESSED_MASK)
                if arch_compressed:
                    compressed = not bit_compressed
                else:
                    compressed = bit_compressed
                fname = file_names[idx] if idx < len(file_names) else ""
                idx += 1
                files.append(
                    _BSAFile(
                        path=self._join(folder_name, fname),
                        offset=offset,
                        size=size,
                        compressed=compressed,
                    )
                )

        self._bsa_files = files

    def _parse_bsa_v103(
        self, folder_offset: int, flags: int, folder_count: int,
        file_count: int, folder_names_len: int, file_names_len: int,
    ) -> None:
        """v103 (Oblivion / Fallout 3 / New Vegas / Skyrim 2011).

        The 32-byte header carries no file-flags, and folder blocks - each a
        folder name followed by its 16-byte file records - are laid out linearly
        right after the folder-record table, with embedded file names after
        them.
        """
        data = self._content
        self.version = 103
        self.flags = flags
        self.file_flags = 0
        self.directory_names_length = folder_names_len
        self.file_names_length = file_names_len

        dirs_named = bool(flags & FLAG_DIRECTORIES_NAMED)
        files_named = bool(flags & FLAG_FILES_NAMED)
        folder_rec_end = folder_offset + folder_count * 16
        if folder_rec_end > len(data):
            raise BethesdaError("BSA folder records out of range")

        # Parse folder records.
        folders = []
        pos = folder_offset
        for _ in range(folder_count):
            (hash_, cnt, name_off) = struct.unpack_from("<QII", data, pos)
            pos += 16
            folders.append(
                {"hash": hash_, "file_count": cnt, "name_offset": name_off}
            )

        # Folder blocks + file records are stored contiguously after the
        # folder-record table (each folder's file records follow its name).
        pos = folder_rec_end
        folder_blocks = []
        for folder in folders:
            name = ""
            if dirs_named:
                length, pos = _read_uvarint(data, pos)
                if pos + length > len(data):
                    raise BethesdaError("BSA folder name out of range")
                raw = data[pos : pos + length]
                pos += length
                name = self._clean_bsa_name(raw)
            records = []
            for _ in range(folder["file_count"]):
                if pos + 16 > len(data):
                    raise BethesdaError("BSA file record out of range")
                (hash_, size_flags, offset) = struct.unpack_from("<QII", data, pos)
                pos += 16
                size = size_flags & FILE_SIZE_MASK
                compressed = bool(size_flags & FILE_COMPRESSED_MASK)
                if files_named:
                    records.append((name, offset, size, compressed, None))
                else:
                    records.append((name, offset, size, compressed, hash_))
            folder_blocks.append(records)

        # Embedded file names are stored directly after folder blocks.
        file_names: List[str] = []
        if files_named:
            for _ in range(file_count):
                length, pos = _read_uvarint(data, pos)
                if pos + length > len(data):
                    raise BethesdaError("BSA file name out of range")
                raw = data[pos : pos + length]
                pos += length
                file_names.append(self._clean_bsa_name(raw))

        # Each file record carries its folder path; the individual filename
        # lives in the file_names table, paired by global file index. Rebuild
        # the full path as folder/filename.
        files: List[_BSAFile] = []
        idx = 0
        for records in folder_blocks:
            for (folder, offset, size, compressed, _h) in records:
                fname = file_names[idx] if idx < len(file_names) else ""
                files.append(
                    _BSAFile(path=self._join(folder, fname), offset=offset,
                             size=size, compressed=compressed)
                )
                idx += 1

        self._bsa_files = files

    @staticmethod
    def _clean_bsa_name(raw: bytes) -> str:
        # BSA names are typically prefixed with a drive/slash marker and may
        # carry embedded NULs. Keep the printable portion, forward-slash paths.
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:
            text = raw.decode("latin-1", "replace")
        text = text.strip("\x00\r\n")
        return text

    @staticmethod
    def _join(folder: str, name: str) -> str:
        raw = folder.replace("\\", "/") + "/" + name.replace("\\", "/")
        parts = [p for p in raw.split("/") if p]
        return "/".join(parts)

    # ── BA2 parsing ───────────────────────────────────────────────────
    def _parse_ba2(self) -> None:
        data = self._content
        if len(data) < 24:
            raise BethesdaError("BA2 header truncated")

        magic, version, type_, file_count, name_off = struct.unpack_from(
            "<4sI4sIQ", data, 0
        )
        type_ = type_.decode("ascii", "replace").strip("\x00")
        self.version = version
        self.type = type_ if type_ else "GNRL"
        self.flags = 0

        if self.type == "DX10":
            self._parse_ba2_dx10(data, file_count, name_off)
        else:
            self._parse_ba2_general(data, file_count, name_off)

    def _names_at(
        self, data: bytes, name_off: int, count: int, width: int
    ) -> List[str]:
        pos = name_off
        names = []
        for _ in range(count):
            length, pos = _read_uvarint(data, pos) if width == 0 else (int.from_bytes(
                data[pos : pos + width], "little"), pos + width)
            if pos + length > len(data):
                raise BethesdaError("BA2 name table out of range")
            raw = data[pos : pos + length]
            pos += length
            text = raw.decode("utf-8", "replace")
            names.append(text.strip("\x00\r\n"))
        return names

    def _parse_ba2_general(self, data: bytes, file_count: int, name_off: int) -> None:
        # After the fixed 24-byte header, GNRL archives carry a u32 name-table
        # size (all zero in FO4); entries begin at offset 28.
        entry_base = 28
        if len(data) < entry_base:
            raise BethesdaError("BA2 header truncated")
        entry_size = 36
        entry_end = entry_base + file_count * entry_size
        if entry_end > len(data):
            raise BethesdaError("BA2 file entries out of range")

        names = self._names_at(data, name_off, file_count, 0)
        entries: List[_BA2File] = []
        pos = entry_base
        for i in range(file_count):
            name_hash, ext, dir_hash, flags, offset = struct.unpack_from(
                "<I4sIIQ", data, pos
            )
            packed_size, unpacked_size = struct.unpack_from("<II", data, pos + 24)
            pos += entry_size
            name = names[i] if i < len(names) else ""
            compressed = bool(flags & BA2_COMPRESSED)
            entries.append(
                _BA2File(
                    path=self._join("", name),
                    offset=offset,
                    packed_size=packed_size,
                    unpacked_size=unpacked_size,
                    compressed=compressed,
                )
            )
        self._ba2_entries = entries
        self._ba2_dx10 = None
        self._dx10_names = []

    def _parse_ba2_dx10(self, data: bytes, file_count: int, name_off: int) -> None:
        entry_base = 24
        if len(data) < entry_base:
            raise BethesdaError("BA2 header truncated")
        names = self._names_at(data, name_off, file_count, 2)
        entries: List[_BA2Texture] = []
        pos = entry_base
        idx = 0
        for i in range(file_count):
            if pos + 24 > len(data):
                raise BethesdaError("BA2 texture entry out of range")
            name_hash, ext, dir_hash, unk, n_chunks, chunk_hdr_size, height, \
                width, mips, fmt, tail = struct.unpack_from("<I4sIBBH HH BB H", data, pos)
            # Re-package: parse manually for clarity.
            name_hash, ext, dir_hash, unk, n_chunks, chunk_hdr_size = struct.unpack_from(
                "<I4sIBBH", data, pos
            )
            height, width = struct.unpack_from("<HH", data, pos + 16)
            mips, fmt, tail = struct.unpack_from("<BBH", data, pos + 20)
            pos += 24

            chunks = []
            for _ in range(n_chunks):
                if pos + 24 > len(data):
                    raise BethesdaError("BA2 texture chunk out of range")
                (c_off, c_packed, c_unpacked, start_mip, end_mip, c_unk) = (
                    struct.unpack_from("<QIIHHI", data, pos)
                )
                pos += 24
                chunks.append(
                    {
                        "offset": c_off,
                        "packed_size": c_packed,
                        "unpacked_size": c_unpacked,
                        "start_mip": start_mip,
                        "end_mip": end_mip,
                    }
                )

            name = names[idx] if idx < len(names) else ""
            idx += 1
            entries.append(
                _BA2Texture(
                    path=self._join("", name),
                    offset=0,
                    name_hash=name_hash,
                    ext=ext.decode("ascii", "replace").strip("\x00"),
                    dir_hash=dir_hash,
                    height=height,
                    width=width,
                    mips=mips,
                    format=fmt,
                    is_cubemap=tail == 2049,
                    tile=(tail & 0x00FF),
                    chunks=chunks,
                )
            )
        self._ba2_entries = entries
        self._ba2_dx10 = True
        self._dx10_names = names

    # ── public API ────────────────────────────────────────────────────
    def size_of(self, name: str) -> int:
        """Return the stored (on-disk) size of a single entry, without reading it."""
        name = name.replace("\\", "/").lstrip("./")
        if self.format == "BSA":
            for f in self._bsa_files:
                if f.path == name:
                    return f.size
        else:
            for e in self._ba2_entries:
                if e.path == name:
                    if isinstance(e, _BA2Texture):
                        return sum(c["unpacked_size"] for c in e.chunks)
                    return e.packed_size if e.compressed else e.unpacked_size
        raise BethesdaError(f"no such entry: {name}")

    def list_files(self) -> Iterator[str]:
        """Yield the normalized (forward-slash) relative path of every entry."""
        if self.format == "BSA":
            for f in self._bsa_files:
                yield f.path
        else:
            for e in self._ba2_entries:
                yield e.path

    def open_file(self, name: str) -> bytes:
        """Return the raw bytes of a single entry (decompressing if needed)."""
        name = name.replace("\\", "/").lstrip("./")
        if self.format == "BSA":
            for f in self._bsa_files:
                if f.path == name:
                    return self._read_bsa_file(f)
            raise BethesdaError(f"no such entry: {name}")
        for e in self._ba2_entries:
            if e.path == name:
                if isinstance(e, _BA2Texture):
                    return self._read_ba2_texture(e)
                return self._read_ba2_general(e)
        raise BethesdaError(f"no such entry: {name}")

    def _read_bsa_file(self, f: _BSAFile) -> bytes:
        raw = self._slice(f.offset, f.size)
        if not f.compressed:
            return raw
        if len(raw) < 4:
            raise BethesdaError(f"truncated compressed data for {f.path}")
        method = "lz4" if self.version >= 105 else "zlib"
        original_size, payload = self._bsa_payload(f.path, raw)
        try:
            return decompress(payload, method, output_size=original_size)
        except CompressionError as exc:
            raise BethesdaError(f"cannot decompress {f.path}: {exc}") from exc

    def _bsa_payload(self, path: str, raw: bytes) -> Tuple[int, bytes]:
        """Split a compressed BSA block into (uncompressed size, payload).

        Streaming-layout texture archives (v104/105 with ``FLAG_FILES_NAMED``)
        prepend an embedded name label to every compressed block in the data
        region: ``[u8 name length][name][u32 uncompressed size][payload]``.
        Other archives store ``[u32 uncompressed size][payload]`` directly.
        """
        skip = 0
        if (
            self.version >= 104
            and self.file_flags & FLAG_FILES_NAMED
            and len(raw) > 1
        ):
            name_len = raw[0]
            if 1 + name_len + 4 <= len(raw):
                skip = 1 + name_len
        if skip:
            return struct.unpack_from("<I", raw, skip)[0], raw[skip + 4 :]
        return struct.unpack_from("<I", raw, 0)[0], raw[4:]

    def _read_ba2_general(self, f: _BA2File) -> bytes:
        size = f.packed_size if f.compressed else f.unpacked_size
        raw = self._slice(f.offset, size)
        if not f.compressed:
            return raw
        try:
            return decompress(raw, "zlib", output_size=f.unpacked_size)
        except CompressionError as exc:
            raise BethesdaError(f"cannot decompress {f.path}: {exc}") from exc
        except Exception as exc:
            raise BethesdaError(f"cannot decompress {f.path}: {exc}") from exc

    def _read_ba2_texture(self, t: _BA2Texture) -> bytes:
        header = build_dds(t.width, t.height, t.mips, t.format, t.is_cubemap)
        chunks = []
        for chunk in t.chunks:
            size = (
                chunk["packed_size"]
                if chunk["packed_size"]
                else chunk["unpacked_size"]
            )
            raw = self._slice(chunk["offset"], size)
            if chunk["packed_size"]:
                try:
                    raw = decompress(raw, "zlib", output_size=chunk["unpacked_size"])
                except Exception as exc:
                    raise BethesdaError(f"cannot decompress {t.path}: {exc}") from exc
            chunks.append(raw)
        return header + b"".join(chunks)

    def extract_file(self, name: str, out_dir: str) -> str:
        """Extract a single entry to ``out_dir`` preserving its relative path."""
        from dualforge.export import Exporter

        data = self.open_file(name)
        exporter = Exporter(out_dir)
        return exporter.write(name, data)

    def extract_all(self, out_dir: str, progress=None) -> List[str]:
        """Extract every entry into ``out_dir``. Returns written paths."""
        from dualforge.export import Exporter

        exporter = Exporter(out_dir)
        written = []
        total = self.file_count
        for index, name in enumerate(self.list_files()):
            if progress:
                progress(index, total, name)
            try:
                data = self.open_file(name)
            except BethesdaError as exc:
                raise BethesdaError(exc) from exc
            written.append(exporter.write(name, data))
        return written

    def _slice(self, offset: int, size: int) -> bytes:
        end = offset + size
        if offset < 0 or end > len(self._content):
            raise BethesdaError(f"data range {offset}..{end} out of file bounds")
        return self._content[offset:end]


def build_dds(
    width: int, height: int, mips: int, dxgi_format: int, is_cubemap: bool = False
) -> bytes:
    """Build a full DDS file (magic + 124-byte header + optional DX10 header).

    Mirrors the reconstruction used by BAE / bethesda-structs: BA2 texture
    pixel data is stored with the DDS header stripped, so we rebuild the header
    from the entry's width/height/mip count and DXGI format, then the caller
    appends the (decompressed) chunk data.
    """

    DDSD_CAPS = 0x1
    DDSD_HEIGHT = 0x2
    DDSD_WIDTH = 0x4
    DDSD_PIXELFORMAT = 0x1000
    DDSD_MIPMAPCOUNT = 0x20000
    DDSD_LINEARSIZE = 0x80000

    DDSCAPS_COMPLEX = 0x8
    DDSCAPS_TEXTURE = 0x1000
    DDSCAPS_MIPMAP = 0x400000

    DDSCAPS2_CUBEMAP = 0x200
    DDSCAPS2_CUBEMAP_POSITIVEX = 0x400
    DDSCAPS2_CUBEMAP_NEGATIVEX = 0x800
    DDSCAPS2_CUBEMAP_POSITIVEY = 0x1000
    DDSCAPS2_CUBEMAP_NEGATIVEY = 0x2000
    DDSCAPS2_CUBEMAP_POSITIVEZ = 0x4000
    DDSCAPS2_CUBEMAP_NEGATIVEZ = 0x8000

    fmt = dxgi_format
    ddspf_flags, ddspf_fourcc, ddspf_bitcount, ddspf_masks, pitch, dx10 = \
        _dxgi_to_ddspf(fmt, width, height)

    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT
    if mips > 1:
        flags |= DDSD_MIPMAPCOUNT
    if pitch:
        flags |= DDSD_LINEARSIZE

    caps = DDSCAPS_TEXTURE
    if mips > 1:
        caps |= DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

    caps2 = 0
    if is_cubemap:
        caps2 = (
            DDSCAPS2_CUBEMAP
            | DDSCAPS2_CUBEMAP_POSITIVEX
            | DDSCAPS2_CUBEMAP_NEGATIVEX
            | DDSCAPS2_CUBEMAP_POSITIVEY
            | DDSCAPS2_CUBEMAP_NEGATIVEY
            | DDSCAPS2_CUBEMAP_POSITIVEZ
            | DDSCAPS2_CUBEMAP_NEGATIVEZ
        )

    size = 124 + len(dx10)

    header = bytearray(124)
    struct.pack_into("<4s", header, 0, b"DDS ")
    struct.pack_into("<I", header, 4, size)
    struct.pack_into("<I", header, 8, flags)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, pitch)
    struct.pack_into("<I", header, 24, 0)  # depth
    struct.pack_into("<I", header, 28, mips if mips > 1 else 0)  # mip count
    # dwReserved1[11] at 32..75 stays zero.
    # ddspf at 76
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, ddspf_flags)
    struct.pack_into("<4s", header, 84, struct.pack("<I", ddspf_fourcc))
    struct.pack_into("<I", header, 88, ddspf_bitcount)
    for i, mask in enumerate(ddspf_masks):
        struct.pack_into("<I", header, 92 + i * 4, mask)
    struct.pack_into("<I", header, 108, caps)   # caps at 108
    struct.pack_into("<I", header, 112, caps2)  # caps2 at 112
    struct.pack_into("<I", header, 116, 0)      # caps3
    struct.pack_into("<I", header, 120, 0)      # caps4
    return bytes(header) + dx10


def _dxgi_to_ddspf(fmt: int, width: int, height: int) -> tuple:
    """Map a DXGI_FORMAT to (flags, fourcc, bitcount, masks, pitch, dx10ext)."""
    FOURCC = 0x4
    RGBA = 0x41

    if fmt == 71:  # BC1_UNORM -> DXT1
        return FOURCC, 0x31545844, 0, (0, 0, 0, 0), width * height // 2, b""
    if fmt == 74:  # BC2_UNORM -> DXT3
        return FOURCC, 0x33545844, 0, (0, 0, 0, 0), width * height, b""
    if fmt == 77:  # BC3_UNORM -> DXT5
        return FOURCC, 0x35545844, 0, (0, 0, 0, 0), width * height, b""
    if fmt == 83:  # BC5_UNORM -> ATI2
        return FOURCC, 0x32495441, 0, (0, 0, 0, 0), width * height, b""
    if fmt in (98, 99):  # BC7_UNORM / BC7_UNORM_SRGB -> DX10 header
        dx10 = struct.pack("<IIIII", fmt, 3, 0, 1, 0)  # TEXTURE2D, array 1
        return FOURCC, 0x30315844, 0, (0, 0, 0, 0), width * height, dx10
    if fmt in (28, 113):  # B8G8R8A8_UNORM / _SRGB
        return RGBA, 0, 32, (0xFF000000, 0x00FF0000, 0x0000FF00, 0x000000FF), \
            width * height * 4, b""
    if fmt == 61:  # R8_UNORM
        return RGBA, 0, 8, (0x000000FF, 0, 0, 0), width * height, b""
    raise BethesdaError(f"unsupported DXGI format {fmt} for DDS header")


__all__ = [
    "BethesdaArchive",
    "BethesdaError",
    "build_dds",
    "BSA_MAGIC",
    "BA2_MAGIC",
]
