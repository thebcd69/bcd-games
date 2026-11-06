# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A minimal PE image for the host checks that read PE headers (build/wineserver/luajit_compat.c, virtual_ios.c's ios_gl_real_unixlib,
tools/verify-opengl32-optin.py): one section holding an export directory, an optional blob, and the fields those readers look at."""
import struct


def build_pe(*, machine=0x8664, image_base=0x180000000, dll_name="x.dll", exports=(), blob=b"", pe32=False, mapped=False):
    """Returns the bytes of a PE32+ (PE32 with pe32=True) image with one readable section at RVA 0x1000. As a file (the default) the
    section is at file offset 0x200; mapped=True lays it out as Wine maps it (RVA == offset), which build/ntdll-unix reads headers from."""
    names = sorted(exports)
    rva0 = 0x1000
    dll = dll_name.encode() + b"\0"
    nn = len(names)
    # layout inside the section: export directory (40), functions (4*nn), names (4*nn), ordinals (2*nn), name strings, dll name, blob
    ed_off = 0
    fn_off = ed_off + 40
    nm_off = fn_off + 4 * nn
    od_off = nm_off + 4 * nn
    strs_off = (od_off + 2 * nn + 3) & ~3
    name_offs, cur = [], strs_off
    for n in names:
        name_offs.append(cur)
        cur += len(n) + 1
    dll_off = cur
    cur += len(dll)
    blob_off = (cur + 15) & ~15
    size = blob_off + len(blob)
    sec = bytearray(size)
    struct.pack_into("<IIHHIIIIIII", sec, ed_off, 0, 0, 0, 0, rva0 + dll_off, 1, nn, nn, rva0 + fn_off, rva0 + nm_off, rva0 + od_off)
    for i, n in enumerate(names):
        struct.pack_into("<I", sec, fn_off + 4 * i, rva0 + blob_off)
        struct.pack_into("<I", sec, nm_off + 4 * i, rva0 + name_offs[i])
        struct.pack_into("<H", sec, od_off + 2 * i, i)
        sec[name_offs[i]:name_offs[i] + len(n) + 1] = n.encode() + b"\0"
    sec[dll_off:dll_off + len(dll)] = dll
    sec[blob_off:blob_off + len(blob)] = blob

    opt_size = 224 if pe32 else 240
    headers = bytearray(0x200)
    headers[0:2] = b"MZ"
    struct.pack_into("<I", headers, 0x3C, 0x80)
    pe = 0x80
    headers[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", headers, pe + 4, machine, 1, 0, 0, 0, opt_size, 0x2022)
    opt = pe + 24
    struct.pack_into("<H", headers, opt, 0x10B if pe32 else 0x20B)
    size_of_image = (rva0 + size + 0xFFF) & ~0xFFF
    if pe32:
        struct.pack_into("<I", headers, opt + 28, image_base & 0xFFFFFFFF)
        struct.pack_into("<II", headers, opt + 32, 0x1000, 0x200)
        struct.pack_into("<II", headers, opt + 56, size_of_image, 0x200)
        struct.pack_into("<I", headers, opt + 92, 16)
        dd = opt + 96
    else:
        struct.pack_into("<Q", headers, opt + 24, image_base)
        struct.pack_into("<II", headers, opt + 32, 0x1000, 0x200)
        struct.pack_into("<II", headers, opt + 56, size_of_image, 0x200)
        struct.pack_into("<I", headers, opt + 108, 16)
        dd = opt + 112
    struct.pack_into("<II", headers, dd, rva0, 40 + 10 * nn)   # DataDirectory[0], the export directory
    sh = opt + opt_size
    headers[sh:sh + 8] = b".rdata\0\0"
    raw = (size + 0x1FF) & ~0x1FF
    struct.pack_into("<IIIIIIHHI", headers, sh + 8, size, rva0, raw, 0x200, 0, 0, 0, 0, 0x40000040)
    if mapped:
        return bytes(headers) + b"\0" * (rva0 - len(headers)) + bytes(sec)
    return bytes(headers) + bytes(sec) + b"\0" * (raw - size)
