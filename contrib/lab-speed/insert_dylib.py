"""Append an LC_LOAD_DYLIB command to a thin 64-bit Mach-O (needs free space between the load commands and the first section)."""
import struct
def insert(data, path):
    data = bytearray(data)
    magic, cpu, sub, ftype, ncmds, sizeofcmds, flags, res = struct.unpack_from("<IiiIIIII", data, 0)
    assert magic == 0xfeedfacf, "thin arm64 Mach-O expected"
    off, first = 32, len(data)
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x19:  # LC_SEGMENT_64: find the lowest section file offset
            nsects = struct.unpack_from("<I", data, off + 64)[0]
            for i in range(nsects):
                so = struct.unpack_from("<I", data, off + 72 + i * 80 + 48)[0]
                if so: first = min(first, so)
        if cmd in (0xc, 0x80000018) and path.encode() in bytes(data[off:off + size]):
            return bytes(data)  # already there
        off += size
    name = path.encode() + b"\0"; size = (24 + len(name) + 7) & ~7
    end = 32 + sizeofcmds
    assert end + size <= first, f"no room for the load command ({first - end} bytes free)"
    cmdb = struct.pack("<IIIIII", 0xc, size, 24, 2, 0x10000, 0x10000) + name
    data[end:end + size] = cmdb.ljust(size, b"\0")
    struct.pack_into("<II", data, 16, ncmds + 1, sizeofcmds + size)
    return bytes(data)
