"""Test-fixture writer for BSA / BA2 archives.

These helpers produce minimal but valid BSA (v103/104/105) and BA2 (GNRL/DX10)
archives used to validate the reader. They intentionally use the same byte
layouts the reader consumes so the tests are self-consistent round-trips.
"""

from __future__ import annotations

import struct
import zlib
from typing import Dict, List, Sequence, Tuple

from dualforge.bethesda.archive import (
    BA2_MAGIC,
    BSA_MAGIC,
    BA2_COMPRESSED,
    FILE_COMPRESSED_MASK,
    FILE_SIZE_MASK,
    FLAG_DIRECTORIES_NAMED,
    FLAG_FILES_NAMED,
    FLAG_FILES_COMPRESSED,
)


def _uv(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _uvarint_prefixed(name: str) -> bytes:
    raw = name.encode("utf-8")
    return _uv(len(raw)) + raw


def build_bsa(
    *,
    files: Sequence[Tuple[str, str, bytes]],
    version: int = 104,
    compress: bool = False,
    streamed: bool = False,
) -> bytes:
    """Build a BSA archive.

    ``files`` is a sequence of (relative_path, folder_name, data).
    When ``compress`` is True the default-archive compressed flag is set and
    every file payload is stored zlib (v<105) / LZ4-frame (v105) compressed.

    For v104/105 the archive is written in the real offset-based layout used by
    Fallout 4 / Skyrim Special Edition (folder records pointing at content
    blocks, folder names as ``u8-length + bytes + NUL``, and a separate
    NUL-terminated file-name block); v103 keeps the classic linear layout.

    ``streamed`` (v104/105 only) mirrors the Skyrim SE texture-archive layout:
    ``file_flags`` gains ``FLAG_FILES_NAMED`` and each compressed block in the
    data region is prefixed with an embedded name label,
    ``[u8 name length][name][u32 uncompressed size][payload]``.
    """
    if version == 103:
        version_int = 0x67
    elif version == 104:
        version_int = 0x68
    elif version == 105:
        version_int = 0x69
    else:
        raise ValueError(f"unsupported bsa version {version}")

    # Group files per folder, preserving order.
    ordered: List[str] = []
    by_folder: Dict[str, List[Tuple[str, bytes]]] = {}
    for rel, folder, data in files:
        if folder not in by_folder:
            by_folder[folder] = []
            ordered.append(folder)
        by_folder[folder].append((rel, data))

    folders = [(folder, by_folder[folder]) for folder in ordered]
    flattened = [
        (folder, rel, data) for folder, entries in folders for rel, data in entries
    ]

    archive_flags = FLAG_DIRECTORIES_NAMED | FLAG_FILES_NAMED
    if compress:
        archive_flags |= FLAG_FILES_COMPRESSED

    if version != 103:
        return _build_bsa_v104_105(
            folders, flattened, version, version_int, archive_flags, compress,
            streamed,
        )

    # ── v103 classic linear layout ───────────────────────────────────
    # assemble name + file-record region (folder blocks)
    folder_blocks = bytearray()
    folder_records = bytearray()
    all_file_count = 0
    for folder, entries in folders:
        if archive_flags & FLAG_DIRECTORIES_NAMED:
            folder_blocks += _uvarint_prefixed(folder)
        for rel, data in entries:
            size_flags = len(data) & FILE_SIZE_MASK
            if compress:
                size_flags |= FILE_COMPRESSED_MASK
            folder_blocks += struct.pack("<QII", _hash(rel), size_flags, 0)
        cnt = len(entries)
        all_file_count += cnt
        folder_records += struct.pack("<QII", _hash(folder), cnt, 0)

    # file names (CString-style, uvarint-prefixed without NUL)
    file_names = bytearray()
    for rel, folder, _data in files:
        file_names += _uvarint_prefixed(rel.split("/")[-1])

    header_size = 32
    folder_name_bytes = sum(
        len(_uvarint_prefixed(folder)) for folder, _ in folders
        if archive_flags & FLAG_DIRECTORIES_NAMED
    )

    # data offset = header + folder_records + folder_blocks + file_names
    data_offset = header_size + len(folder_records) + len(folder_blocks) + len(file_names)

    # Assemble header with real offsets.
    header = bytearray()
    header += BSA_MAGIC
    header += struct.pack("<I", version_int)
    header += struct.pack("<I", header_size)
    header += struct.pack("<I", archive_flags)
    header += struct.pack("<I", len(folders))
    header += struct.pack("<I", all_file_count)
    header += struct.pack("<I", folder_name_bytes)
    header += struct.pack("<I", sum(len(_uvarint_prefixed(f.split("/")[-1])) for f, _f, _d in files))

    # build file data
    data = bytearray()
    file_offsets = []
    blob_sizes = []
    for rel, folder_name, content in files:
        blob, = _encode_blob(content, version, compress)
        file_offsets.append(data_offset + len(data))
        blob_sizes.append(len(blob))
        data += blob

    # rewrite folder blocks with real file data offsets
    folder_blocks2 = bytearray()
    fi = 0
    for folder, entries in folders:
        if archive_flags & FLAG_DIRECTORIES_NAMED:
            folder_blocks2 += _uvarint_prefixed(folder)
        for rel, content in entries:
            size_flags = blob_sizes[fi] & FILE_SIZE_MASK
            if compress:
                size_flags |= FILE_COMPRESSED_MASK
            folder_blocks2 += struct.pack("<QII", _hash(rel), size_flags, file_offsets[fi])
            fi += 1

    out = bytes(header) + bytes(folder_records) + bytes(folder_blocks2) + bytes(file_names) + bytes(data)
    return out


def _build_bsa_v104_105(
    folders, flattened, version, version_int, archive_flags, compress, streamed,
) -> bytes:
    """Write the real Fallout 4 / Skyrim SE (v104 / v105) byte layout."""
    dir_size = 24 if version >= 105 else 16
    header_size = 36

    # File-name block: one NUL-terminated base name per file, folder-major.
    file_name_blob = bytearray()
    for _folder, entries in folders:
        for rel, _data in entries:
            file_name_blob += rel.split("/")[-1].encode("utf-8") + b"\x00"

    # Content blocks: [folder name][16-byte file records], one per folder, laid
    # out right after the folder-record table (folder names are stored inline
    # at the head of each folder's block, not in a separate region).
    content_region = header_size + len(folders) * dir_size
    content_length = sum(
        1 + len(folder.encode("utf-8")) + 16 * len(entries)
        for folder, entries in folders
    )
    data_offset = content_region + content_length + len(file_name_blob)

    # File data blobs (offsets relative to data_offset).
    data = bytearray()
    blob_sizes = []
    for _dir, rel, content in flattened:
        blob, = _encode_blob(content, version, compress)
        if streamed and compress:
            base = rel.split("/")[-1].encode("utf-8")
            blob = struct.pack("<B", len(base)) + base + blob
        blob_sizes.append(len(blob))
        data += blob

    # Content blocks with real per-file offsets.
    content_blocks = bytearray()
    stored_dir_offsets = []  # content pos + total_file_name_length
    fi = 0
    content_pos = content_region
    for folder, entries in folders:
        stored_dir_offsets.append(content_pos + len(file_name_blob))
        raw = folder.encode("utf-8")
        content_blocks += struct.pack("<B", len(raw)) + raw
        for _rel, _data in entries:
            # In a compressed-by-default archive the FileRecord size bit is
            # CLEAR for compressed files (set = stored raw); matches the reader.
            size_flags = blob_sizes[fi] & FILE_SIZE_MASK
            content_blocks += struct.pack(
                "<QII", _hash(_rel), size_flags,
                data_offset + sum(blob_sizes[:fi]),
            )
            fi += 1
        content_pos += 1 + len(raw) + 16 * len(entries)

    # Folder records in folder order.
    folder_records = bytearray()
    for i, (folder, entries) in enumerate(folders):
        stored_offset = stored_dir_offsets[i]
        if version >= 105:
            folder_records += struct.pack("<QIIII", _hash(folder), len(entries), 0, stored_offset, 0)
        else:
            folder_records += struct.pack("<QII", _hash(folder), len(entries), stored_offset)

    header = bytearray()
    header += BSA_MAGIC
    header += struct.pack("<I", version_int)
    header += struct.pack("<I", header_size)
    header += struct.pack("<I", archive_flags)
    header += struct.pack("<I", len(folders))
    header += struct.pack("<I", len(flattened))
    header += struct.pack(
        "<I", sum(len(folder.encode("utf-8")) for folder, _ in folders)
    )
    header += struct.pack("<I", len(file_name_blob))
    header += struct.pack("<HH", FLAG_FILES_NAMED if streamed else 0, 0)  # file_flags + padding

    return (
        bytes(header)
        + bytes(folder_records)
        + bytes(content_blocks)
        + bytes(file_name_blob)
        + bytes(data)
    )


def _encode_blob(content: bytes, version: int, compress: bool):
    """Return the on-disk blob for one file (``(u32 size + stream)`` when
    compressed, the raw bytes otherwise)."""
    if not compress:
        return (content,)
    if version >= 105:
        import lz4.frame

        return (struct.pack("<I", len(content)) + lz4.frame.compress(content),)
    return (struct.pack("<I", len(content)) + zlib.compress(content),)


def _hash(name: str) -> int:
    """Simple deterministic 32-bit hash (fixture-only; not Bethesda's)."""
    return zlib.crc32(name.encode("utf-8"))


def build_ba2_general(*, files: Sequence[Tuple[str, bytes]], compress: bool = False) -> bytes:
    """Build a BA2 GNRL archive. ``files`` = (relative_path, data)."""
    header_size = 28
    entry_count = len(files)
    entries = bytearray()
    name_blob = bytearray()
    data_blob = bytearray()
    for rel, _content in files:
        name_blob += _uvarint_prefixed(rel)

    # Data lives after the header, the entry table, and the name table.
    data_base = header_size + entry_count * 36 + len(name_blob)

    offsets = []
    for rel, content in files:
        payload = zlib.compress(content) if compress else content
        packed = len(payload) if compress else 0
        offsets.append(data_base + len(data_blob))
        data_blob += payload

    for i, (rel, content) in enumerate(files):
        base = rel.split("/")[-1]
        ext = _ext4(base)
        name_hash = _hash(base) & 0xFFFFFFFF
        dir_hash = _hash("/".join(rel.split("/")[:-1])) & 0xFFFFFFFF
        packed = len(zlib.compress(content)) if compress else 0
        unpacked = len(content)
        entries += struct.pack(
            "<I4sII", name_hash, ext, dir_hash, BA2_COMPRESSED if compress else 0
        )
        entries += struct.pack("<QII", offsets[i], packed, unpacked)
        entries += struct.pack("<I", 0xBAADF00D)

    # ── resource header ──────────────────────────────────────────────
    name_off = header_size + len(entries)
    header = BA2_MAGIC
    header += struct.pack("<I4sIQ", 1, b"GNRL", entry_count, name_off)
    header += struct.pack("<I", 0)  # name table size (GNRL)
    return header + bytes(entries) + bytes(name_blob) + bytes(data_blob)


def _ext4(name: str) -> bytes:
    if "." in name:
        ext = name.rsplit(".", 1)[1]
    else:
        ext = ""
    return ext.ljust(4, "\x00")[:4].encode("ascii")


def build_ba2_dx10(
    *, files: Sequence[Tuple[str, int, int, int, int, bytes]], compress: bool = False
) -> bytes:
    """Build a BA2 DX10 (texture) archive.

    Each entry is ``(rel_path, width, height, mips, dxgi_format, pixel_bytes)``.
    The reader reconstructs a DDS header from the width/height/mips/format and
    appends the (decompressed) chunk data, so the round-trip yields a valid DDS.
    """
    header_size = 24
    entry_count = len(files)
    entries = bytearray()
    name_blob = bytearray()
    data_blob = bytearray()

    for rel, _w, _h, _m, _f, _px in files:
        raw = rel.encode("utf-8")
        name_blob += struct.pack("<H", len(raw)) + raw  # Int16ul-prefixed names

    # Entry table region: each entry = 24-byte header + (1 chunk * 24 bytes).
    pos = header_size
    for _ in files:
        pos += 24 + 24

    data_base = pos + len(name_blob)
    data_offsets = []
    for rel, _w, _h, _m, _f, pixel in files:
        payload = zlib.compress(pixel) if compress else pixel
        data_offsets.append(data_base + len(data_blob))
        data_blob += payload

    for i, (rel, w, h, mips, fmt, pixel) in enumerate(files):
        base = rel.split("/")[-1]
        ext = base.rsplit(".", 1)[1][:4].ljust(4, "\x00").encode("ascii")
        name_hash = _hash(base) & 0xFFFFFFFF
        dir_hash = _hash(rel.split("/")[0] if "/" in rel else "") & 0xFFFFFFFF
        head = struct.pack("<I4sIBBH", name_hash, ext, dir_hash, 0, 1, 24)
        head += struct.pack("<HHBBH", h, w, mips, fmt, 0)
        chunk = struct.pack(
            "<QIIHHI",
            data_offsets[i],
            len(zlib.compress(pixel)) if compress else 0,
            len(pixel),
            0,
            mips - 1,
            0xBAADF00D,
        )
        entries += head
        entries += chunk

    name_off = header_size + len(entries)
    header = BA2_MAGIC
    header += struct.pack("<I4sIQ", 1, b"DX10", entry_count, name_off)
    return header + bytes(entries) + bytes(name_blob) + bytes(data_blob)


__all__ = ["build_bsa", "build_ba2_general", "build_ba2_dx10"]
