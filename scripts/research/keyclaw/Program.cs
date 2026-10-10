using System;
using System.Buffers.Binary;
using System.IO;
using System.Threading.Tasks;

class Program
{
    static readonly byte[] TableA =
    [
        0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,
        0x18,0x19,0x1A,0x1B,0x1C,0x1D,0x1E,0x1F,
        0x20,0x21,0x22,0x23,0x24,0x25,0x26,0x27,
        0x18,0x19,0x1A,0x1B,0x1C,0x1D,0x1E,0x1F,
        0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,
        0x48,0x49,0x4A,0x4B,0x4C,0x4D,0x4E,0x4F,
        0x20,0x21,0x32,0x43,0x54,0x65,0x16,0x17,
        0x48,0x49,0x6A,0x4B,0x4C,0x4D,0x4E,0x4F,
    ];

    static readonly byte[] Sbox =
    [
        0xd6,0x90,0xe9,0xfe,0xcc,0xe1,0x3d,0xb7,0x16,0xb6,0x14,0xc2,0x28,0xfb,0x2c,0x05,
        0x2b,0x67,0x9a,0x76,0x2a,0xbe,0x04,0xc3,0xaa,0x44,0x13,0x26,0x49,0x86,0x06,0x99,
        0x9c,0x42,0x50,0xf4,0x91,0xef,0x98,0x7a,0x33,0x54,0x0b,0x43,0xed,0xcf,0xac,0x62,
        0xe4,0xb3,0x1c,0xa9,0xc9,0x08,0xe8,0x95,0x80,0xdf,0x94,0xfa,0x75,0x8f,0x3f,0xa6,
        0x47,0x07,0xa7,0xfc,0xf3,0x73,0x17,0xba,0x83,0x59,0x3c,0x19,0xe6,0x85,0x4f,0xa8,
        0x68,0x6b,0x81,0xb2,0x71,0x64,0xda,0x8b,0xf8,0xeb,0x0f,0x4b,0x70,0x56,0x9d,0x35,
        0x1e,0x24,0x0e,0x5e,0x63,0x58,0xd1,0xa2,0x25,0x22,0x7c,0x3b,0x01,0x21,0x78,0x87,
        0xd4,0x00,0x46,0x57,0x9f,0xd3,0x27,0x52,0x4c,0x36,0x02,0xe7,0xa0,0xc4,0xc8,0x9e,
        0xea,0xbf,0x8a,0xd2,0x40,0xc7,0x38,0xb5,0xa3,0xf7,0xf2,0xce,0xf9,0x61,0x15,0xa1,
        0xe0,0xae,0x5d,0xa4,0x9b,0x34,0x1a,0x55,0xad,0x93,0x32,0x30,0xf5,0x8c,0xb1,0xe3,
        0x1d,0xf6,0xe2,0x2e,0x82,0x66,0xca,0x60,0xc0,0x29,0x23,0xab,0x0d,0x53,0x4e,0x6f,
        0xd5,0xdb,0x37,0x45,0xde,0xfd,0x8e,0x2f,0x03,0xff,0x6a,0x72,0x6d,0x6c,0x5b,0x51,
        0x8d,0x1b,0xaf,0x92,0xbb,0xdd,0xbc,0x7f,0x11,0xd9,0x5c,0x41,0x1f,0x10,0x5a,0xd8,
        0x0a,0xc1,0x31,0x88,0xa5,0xcd,0x7b,0xbd,0x2d,0x74,0xd0,0x12,0xb8,0xe5,0xb4,0xb0,
        0x89,0x69,0x97,0x4a,0x0c,0x96,0x77,0x7e,0x65,0xb9,0xf1,0x09,0xc5,0x6e,0xc6,0x84,
        0x18,0xf0,0x7d,0xec,0x3a,0xdc,0x4d,0x20,0x79,0xee,0x5f,0x3e,0xd7,0xcb,0x39,0x48,
    ];

    static readonly uint[] CK =
    [
        0x00070e15,0x1c232a31,0x383f464d,0x545b6269,0x70777e85,0x8c939aa1,0xa8afb6bd,0xc4cbd2d9,
        0xe0e7eef5,0xfc030a11,0x181f262d,0x343b4249,0x50575e65,0x6c737a81,0x888f969d,0xa4abb2b9,
        0xc0c7ced5,0xdce3eaf1,0xf8ff060d,0x141b2229,0x30373e45,0x4c535a61,0x686f767d,0x848b9299,
        0xa0a7aeb5,0xbcc3cad1,0xd8dfe6ed,0xf4fb0209,0x10171e25,0x2c333a41,0x484f565d,0x646b7279,
    ];
    static readonly uint[] FK = [0xa3b1bac6,0x56aa3350,0x677d9197,0xb27022dc];

    static uint Rol(uint x, int n) => (x << n) | (x >> (32 - n));
    static uint Tau(uint a) => (uint)((Sbox[a >> 24] << 24) | (Sbox[(a >> 16) & 0xff] << 16) | (Sbox[(a >> 8) & 0xff] << 8) | Sbox[a & 0xff]);
    static uint Tap(uint z) { uint b = Tau(z); return b ^ Rol(b, 13) ^ Rol(b, 23); }
    static uint Tf(uint z) { uint b = Tau(z); return b ^ Rol(b, 2) ^ Rol(b, 10) ^ Rol(b, 18) ^ Rol(b, 24); }

    static void Expand(ReadOnlySpan<byte> key16, Span<uint> rk)
    {
        uint k0 = BinaryPrimitives.ReadUInt32BigEndian(key16) ^ FK[0];
        uint k1 = BinaryPrimitives.ReadUInt32BigEndian(key16[4..]) ^ FK[1];
        uint k2 = BinaryPrimitives.ReadUInt32BigEndian(key16[8..]) ^ FK[2];
        uint k3 = BinaryPrimitives.ReadUInt32BigEndian(key16[12..]) ^ FK[3];
        rk[31] = k0 ^ Tap(k1 ^ k2 ^ k3 ^ CK[0]);
        rk[30] = k1 ^ Tap(k2 ^ k3 ^ rk[31] ^ CK[1]);
        rk[29] = k2 ^ Tap(k3 ^ rk[31] ^ rk[30] ^ CK[2]);
        rk[28] = k3 ^ Tap(rk[31] ^ rk[30] ^ rk[29] ^ CK[3]);
        for (int i = 27; i >= 0; --i)
            rk[i] = rk[i + 4] ^ Tap(rk[i + 3] ^ rk[i + 2] ^ rk[i + 1] ^ CK[31 - i]);
    }

    static void Block(ReadOnlySpan<uint> rk, ReadOnlySpan<byte> input, Span<byte> output)
    {
        uint x0 = BinaryPrimitives.ReadUInt32BigEndian(input);
        uint x1 = BinaryPrimitives.ReadUInt32BigEndian(input[4..]);
        uint x2 = BinaryPrimitives.ReadUInt32BigEndian(input[8..]);
        uint x3 = BinaryPrimitives.ReadUInt32BigEndian(input[12..]);
        for (int i = 0; i < 32; i += 4)
        {
            x0 ^= Tf(x1 ^ x2 ^ x3 ^ rk[i]);
            x1 ^= Tf(x2 ^ x3 ^ x0 ^ rk[i + 1]);
            x2 ^= Tf(x3 ^ x0 ^ x1 ^ rk[i + 2]);
            x3 ^= Tf(x0 ^ x1 ^ x2 ^ rk[i + 3]);
        }
        BinaryPrimitives.WriteUInt32BigEndian(output, x3);
        BinaryPrimitives.WriteUInt32BigEndian(output[4..], x2);
        BinaryPrimitives.WriteUInt32BigEndian(output[8..], x1);
        BinaryPrimitives.WriteUInt32BigEndian(output[12..], x0);
    }

    static bool Plausible(ReadOnlySpan<byte> p)
    {
        int len = BinaryPrimitives.ReadInt32LittleEndian(p);
        if (len < 1 || len > 300) return false;
        for (int i = 4; i < 16; i++)
        {
            byte b = p[i];
            bool ok = (b >= '0' && b <= '9') || (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') || b == '.' || b == '/' || b == '_' || b == '-';
            if (!ok) return false;
        }
        byte p0 = p[4];
        return p0 == '.' || p0 == '/' || p0 == 'A' || p0 == 'a';
    }

    static bool TestKey(ReadOnlySpan<byte> raw16, ReadOnlySpan<byte> cipher, bool modeA, Span<uint> rk, Span<byte> keybuf, Span<byte> outbuf)
    {
        if (modeA)
        {
            for (int i = 0; i < 16; i++) keybuf[i] = TableA[raw16[i] & 0x3F];
        }
        else
        {
            raw16[..16].CopyTo(keybuf);
        }
        Expand(keybuf, rk);
        Block(rk, cipher, outbuf);
        return Plausible(outbuf);
    }

    static void Scan(byte[] data, string path, byte[] cipher, bool modeA, bool alignedOnly)
    {
        int step = alignedOnly ? 4 : 1;
        object gate = new();
        Parallel.For(0, (data.Length - 16) / step + 1, () => (rk: new uint[32], keybuf: new byte[16], outbuf: new byte[16], found: 0), (i, state, st) =>
        {
            int off = i * step;
            if (off + 16 > data.Length) return st;
            if (TestKey(data.AsSpan(off, 16), cipher, modeA, st.rk, st.keybuf, st.outbuf))
            {
                byte[] key = data.AsSpan(off, 16).ToArray();
                lock (gate) Console.WriteLine($"HIT {(modeA ? "A" : "N")} {path} @0x{off:X} key={Convert.ToHexString(key)} pt={Convert.ToHexString(st.outbuf)}");
                st.found++;
            }
            return st;
        }, st => { });
    }

    static int Main(string[] args)
    {
        // args: cipherHex file...
        if (args.Length < 2) { Console.WriteLine("usage: keyclaw <cipherHex> <file...> [--aligned]"); return 2; }
        bool aligned = false;
        var files = new System.Collections.Generic.List<string>();
        foreach (var a in args[1..])
        {
            if (a == "--aligned") { aligned = true; continue; }
            files.Add(a);
        }
        byte[] cipher = Convert.FromHexString(args[0]);
        Console.WriteLine($"cipher={args[0]} aligned={aligned} files={files.Count}");
        foreach (var f in files)
        {
            Console.WriteLine($"scan {f}");
            var data = File.ReadAllBytes(f);
            Scan(data, f, cipher, modeA: true, alignedOnly: aligned);
            Scan(data, f, cipher, modeA: false, alignedOnly: aligned);
            Console.WriteLine($"done {f}");
        }
        return 0;
    }
}
