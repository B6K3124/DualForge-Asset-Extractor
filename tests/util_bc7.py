"""Reference BC7 decoder and minimal-block builders for tests.

``decode_bc7_block`` is transcribed verbatim from uyjulian's
``pure_python_dds.py`` (MIT, https://gist.github.com/uyjulian), which was
verified bit-for-bit against BinomialLLC's ``bc7decomp.c``.  It decodes one
16-byte block to the 64 RGBA bytes of its 4x4 pixels (row 0, then row 1,
...).  ``flat_bc7_block`` builds a valid block whose endpoints are constant,
whose partition/rotation/index-selection are zero and whose indices are all
zero, so every pixel decodes to the ``endpoint 0`` colour: it checks the
vectorised decoder against hand-computed expectations.
"""

from __future__ import annotations

# Mode layout: subsets, partition bits, rotation bits, index-selection bits,
# colour bits, alpha bits, per-endpoint P-bits, shared P-bits, primary index
# bits, secondary index bits.
BC7_MODES = [
    (3, 4, 0, 0, 4, 0, 1, 0, 3, 0),
    (2, 6, 0, 0, 6, 0, 0, 1, 3, 0),
    (3, 6, 0, 0, 5, 0, 0, 0, 2, 0),
    (2, 6, 0, 0, 7, 0, 1, 0, 2, 0),
    (1, 0, 2, 1, 5, 6, 0, 0, 2, 3),
    (1, 0, 2, 0, 7, 8, 0, 0, 2, 2),
    (1, 0, 0, 0, 7, 7, 1, 0, 4, 0),
    (2, 6, 0, 0, 5, 5, 1, 0, 2, 0),
]

BC7_SI2 = [
    0xCCCC, 0x8888, 0xEEEE, 0xECC8, 0xC880, 0xFEEC, 0xFEC8, 0xEC80,
    0xC800, 0xFFEC, 0xFE80, 0xE800, 0xFFE8, 0xFF00, 0xFFF0, 0xF000,
    0xF710, 0x008E, 0x7100, 0x08CE, 0x008C, 0x7310, 0x3100, 0x8CCE,
    0x088C, 0x3110, 0x6666, 0x366C, 0x17E8, 0x0FF0, 0x718E, 0x399C,
    0xAAAA, 0xF0F0, 0x5A5A, 0x33CC, 0x3C3C, 0x55AA, 0x9696, 0xA55A,
    0x73CE, 0x13C8, 0x324C, 0x3BDC, 0x6996, 0xC33C, 0x9966, 0x0660,
    0x0272, 0x04E4, 0x4E40, 0x2720, 0xC936, 0x936C, 0x39C6, 0x639C,
    0x9336, 0x9CC6, 0x817E, 0xE718, 0xCCF0, 0x0FCC, 0x7744, 0xEE22,
]

BC7_SI3 = [
    0xAA685050, 0x6A5A5040, 0x5A5A4200, 0x5450A0A8, 0xA5A50000, 0xA0A05050,
    0x5555A0A0, 0x5A5A5050, 0xAA550000, 0xAA555500, 0xAAAA5500, 0x90909090,
    0x94949494, 0xA4A4A4A4, 0xA9A59450, 0x2A0A4250, 0xA5945040, 0x0A425054,
    0xA5A5A500, 0x55A0A0A0, 0xA8A85454, 0x6A6A4040, 0xA4A45000, 0x1A1A0500,
    0x0050A4A4, 0xAAA59090, 0x14696914, 0x69691400, 0xA08585A0, 0xAA821414,
    0x50A4A450, 0x6A5A0200, 0xA9A58000, 0x5090A0A8, 0xA8A09050, 0x24242424,
    0x00AA5500, 0x24924924, 0x24499224, 0x50A50A50, 0x500AA550, 0xAAAA4444,
    0x66660000, 0xA5A0A5A0, 0x50A050A0, 0x69286928, 0x44AAAA44, 0x66666600,
    0xAA444444, 0x54A854A8, 0x95809580, 0x96969600, 0xA85454A8, 0x80959580,
    0xAA141414, 0x96960000, 0xAAAA1414, 0xA05050A0, 0xA0A5A5A0, 0x96000000,
    0x40804080, 0xA9A8A9A8, 0xAAAAAA44, 0x2A4A5254,
]

BC7_AI0 = [
    15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15,
    15, 2, 8, 2, 2, 8, 8, 15, 2, 8, 2, 2, 8, 8, 2, 2,
    15, 15, 6, 8, 2, 8, 15, 15, 2, 8, 2, 2, 2, 15, 15, 6,
    6, 2, 6, 8, 15, 15, 2, 2, 15, 15, 15, 15, 15, 2, 2, 15,
]
BC7_AI1 = [
    3, 3, 15, 15, 8, 3, 15, 15, 8, 8, 6, 6, 6, 5, 3, 3,
    3, 3, 8, 15, 3, 3, 6, 10, 5, 8, 8, 6, 8, 5, 15, 15,
    8, 15, 3, 5, 6, 10, 8, 15, 15, 3, 15, 5, 15, 15, 15, 15,
    3, 15, 5, 5, 5, 8, 5, 10, 5, 10, 8, 13, 15, 12, 3, 3,
]
BC7_AI2 = [
    15, 8, 8, 3, 15, 15, 3, 8, 15, 15, 15, 15, 15, 15, 15, 8,
    15, 8, 15, 3, 15, 8, 15, 8, 3, 15, 6, 10, 15, 15, 10, 8,
    15, 3, 15, 10, 10, 8, 9, 10, 6, 15, 8, 15, 3, 6, 6, 8,
    15, 3, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 3, 15, 15, 8,
]

BC7_WEIGHTS2 = [0, 21, 43, 64]
BC7_WEIGHTS3 = [0, 9, 18, 27, 37, 46, 55, 64]
BC7_WEIGHTS4 = [0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64]

BC7_MODE_INFO = [
    {"ns": m[0], "pb": m[1], "rb": m[2], "isb": m[3], "cb": m[4], "ab": m[5],
     "epb": m[6], "spb": m[7], "ib": m[8], "ib2": m[9]}
    for m in BC7_MODES
]


def _get_bit(src: bytes, bit: int) -> int:
    return (src[bit >> 3] >> (bit & 7)) & 1


def _get_bits(src: bytes, bit: int, count: int) -> int:
    if count == 0:
        return 0
    if (bit & 7) + count <= 8:
        return (src[bit >> 3] >> (bit & 7)) & ((1 << count) - 1)
    value = src[bit >> 3] | (src[(bit >> 3) + 1] << 8)
    return ((value >> (bit & 7)) & ((1 << count) - 1)) & 0xFF


def _get_weights(n: int) -> list[int]:
    return (BC7_WEIGHTS2, BC7_WEIGHTS3, BC7_WEIGHTS4)[n - 2]


def _get_subset(ns: int, partition: int, n: int) -> int:
    if ns == 2:
        return 1 & (BC7_SI2[partition] >> n)
    if ns == 3:
        return 3 & (BC7_SI3[partition] >> (2 * n))
    return 0


def _expand_quantized(v: int, bits: int) -> int:
    v = v << (8 - bits)
    return (v | (v >> bits)) & 0xFF


def _lerp(col: bytearray, dst: int, e: bytearray, eo: int, s0: int, s1: int) -> None:
    t0 = 64 - s0
    t1 = 64 - s1
    r0 = eo * 4
    r1 = (eo + 1) * 4
    d = dst * 4
    col[d + 0] = ((t0 * e[r0 + 0] + s0 * e[r1 + 0] + 32) >> 6) & 0xFF
    col[d + 1] = ((t0 * e[r0 + 1] + s0 * e[r1 + 1] + 32) >> 6) & 0xFF
    col[d + 2] = ((t0 * e[r0 + 2] + s0 * e[r1 + 2] + 32) >> 6) & 0xFF
    col[d + 3] = ((t1 * e[r0 + 3] + s1 * e[r1 + 3] + 32) >> 6) & 0xFF


def decode_bc7_block(src: bytes) -> bytearray:
    """Decode a 16-byte BC7 block to 64 RGBA bytes (4x4 pixels, row-major)."""
    col = bytearray(4 * 4 * 4)
    endpoints = bytearray(6 * 4)

    bit = 0
    mode = src[0]
    if mode == 0:
        for i in range(16):
            col[i * 4 + 0] = 0
            col[i * 4 + 1] = 0
            col[i * 4 + 2] = 0
            col[i * 4 + 3] = 0xFF
        return col
    while True:
        condition = (mode & (1 << bit)) != 0
        bit += 1
        if condition:
            break
    mode = bit - 1
    info = BC7_MODE_INFO[mode]
    cb = info["cb"]
    ab = info["ab"]
    cw = _get_weights(info["ib"])
    aw = _get_weights(info["ib2"] if (ab != 0 and info["ib2"] != 0) else info["ib"])

    partition = _get_bits(src, bit, info["pb"])
    bit += info["pb"]
    rotation = _get_bits(src, bit, info["rb"])
    bit += info["rb"]
    index_sel = _get_bits(src, bit, info["isb"])
    bit += info["isb"]
    numep = info["ns"] << 1

    for i in range(numep):
        endpoints[i * 4 + 0] = _get_bits(src, bit, cb)
        bit += cb
    for i in range(numep):
        endpoints[i * 4 + 1] = _get_bits(src, bit, cb)
        bit += cb
    for i in range(numep):
        endpoints[i * 4 + 2] = _get_bits(src, bit, cb)
        bit += cb
    for i in range(numep):
        if ab != 0:
            endpoints[i * 4 + 3] = _get_bits(src, bit, ab)
            bit += ab
        else:
            endpoints[i * 4 + 3] = 0xFF

    if info["epb"] != 0:
        cb += 1
        if ab != 0:
            ab += 1
        for i in range(numep):
            o = i * 4
            pbit = _get_bits(src, bit, 1)
            bit += 1
            endpoints[o + 0] = (endpoints[o + 0] << 1) | pbit
            endpoints[o + 1] = (endpoints[o + 1] << 1) | pbit
            endpoints[o + 2] = (endpoints[o + 2] << 1) | pbit
            if ab != 0:
                endpoints[o + 3] = (endpoints[o + 3] << 1) | pbit
    if info["spb"] != 0:
        cb += 1
        if ab != 0:
            ab += 1
        for i in range(0, numep, 2):
            pbit = _get_bits(src, bit, 1)
            bit += 1
            for j in range(2):
                o = (i + j) * 4
                endpoints[o + 0] = (endpoints[o + 0] << 1) | pbit
                endpoints[o + 1] = (endpoints[o + 1] << 1) | pbit
                endpoints[o + 2] = (endpoints[o + 2] << 1) | pbit
                if ab != 0:
                    endpoints[o + 3] = (endpoints[o + 3] << 1) | pbit

    for i in range(numep):
        o = i * 4
        endpoints[o + 0] = _expand_quantized(endpoints[o + 0], cb)
        endpoints[o + 1] = _expand_quantized(endpoints[o + 1], cb)
        endpoints[o + 2] = _expand_quantized(endpoints[o + 2], cb)
        if ab != 0:
            endpoints[o + 3] = _expand_quantized(endpoints[o + 3], ab)

    cibit = bit
    aibit = cibit + 16 * info["ib"] - info["ns"]
    for i in range(16):
        pair = _get_subset(info["ns"], partition, i) << 1
        ib = info["ib"]
        if i == 0:
            ib -= 1
        elif info["ns"] == 2:
            if i == BC7_AI0[partition]:
                ib -= 1
        elif info["ns"] == 3 and (i == BC7_AI1[partition] or i == BC7_AI2[partition]):
            ib -= 1
        i0 = _get_bits(src, cibit, ib)
        cibit += ib
        if ab != 0 and info["ib2"] != 0:
            ib2 = info["ib2"]
            if i == 0:
                ib2 -= 1
            i1 = _get_bits(src, aibit, ib2)
            aibit += ib2
            if index_sel != 0:
                _lerp(col, i, endpoints, pair, aw[i1], cw[i0])
            else:
                _lerp(col, i, endpoints, pair, cw[i0], aw[i1])
        else:
            _lerp(col, i, endpoints, pair, cw[i0], cw[i0])
        if rotation == 1:
            val = col[i * 4 + 0]
            col[i * 4 + 0] = col[i * 4 + 3]
            col[i * 4 + 3] = val
        elif rotation == 2:
            val = col[i * 4 + 1]
            col[i * 4 + 1] = col[i * 4 + 3]
            col[i * 4 + 3] = val
        elif rotation == 3:
            val = col[i * 4 + 2]
            col[i * 4 + 2] = col[i * 4 + 3]
            col[i * 4 + 3] = val
    return col


def mode_byte(mode: int) -> int:
    """Byte 0 that selects ``mode`` (LSB-first; 8 = degenerate all-zero byte)."""
    if mode == 8:
        return 0
    return 1 << mode


class _BitWriter:
    def __init__(self) -> None:
        self._buf = bytearray(16)
        self._bit = 0

    def put(self, value: int, count: int) -> None:
        while count:
            byte = self._bit >> 3
            shift = self._bit & 7
            take = min(count, 8 - shift)
            self._buf[byte] |= ((value & ((1 << take) - 1)) << shift)
            value >>= take
            self._bit += take
            count -= take

    def build(self) -> bytes:
        return bytes(self._buf)


def flat_bc7_block(mode: int, r: int, g: int, b: int, a: int = 0xFF, pbit: int = 0) -> bytes:
    """Build a zero-index, constant-endpoint BC7 block for ``mode``.

    Partition, rotation and index-selection fields are zero; every index is
    zero, so the whole block decodes to the expanded endpoint-0 colour.
    """
    ns, pb, rb, isb, cb, ab, epb, spb, ib, ib2 = BC7_MODES[mode]
    w = _BitWriter()
    w.put(1 << mode, mode + 1)  # LSB-first unary mode prefix (byte 0 = 1 << mode)
    w.put(0, pb)
    w.put(0, rb)
    w.put(0, isb)
    for _ in range(ns * 2):
        w.put(r, cb)
    for _ in range(ns * 2):
        w.put(g, cb)
    for _ in range(ns * 2):
        w.put(b, cb)
    for _ in range(ns * 2):
        w.put(a if ab else 0xFF, ab)
    if epb:
        for _ in range(ns * 2):
            w.put(pbit, 1)
    if spb:
        for _ in range(ns):
            w.put(pbit, 1)
    for i in range(16):
        width = ib
        if i == 0:
            width -= 1
        elif ns == 2:
            if i == BC7_AI0[0]:
                width -= 1
        elif ns == 3 and (i == BC7_AI1[0] or i == BC7_AI2[0]):
            width -= 1
        w.put(0, width)
    if ab and ib2:
        for i in range(16):
            w.put(0, ib2 - 1 if i == 0 else ib2)
    return w.build()