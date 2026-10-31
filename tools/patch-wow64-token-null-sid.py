#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wow64.dll: a NULL SID from NtQueryInformationToken is answered, not dereferenced (round 624, Garry's Mod).

    tools/patch-wow64-token-null-sid.py [--verify] app/Madeira/aarch64-windows/wow64.dll

app/Madeira/aarch64-windows/wow64.dll is a COMMITTED binary (no step builds it), so Wine's source fix cannot reach the
phone through a source patch. This patches the DLL in place, once, and --verify checks the committed file is the patched form.

Wine dlls/wow64/security.c, wow64_NtQueryInformationToken, case TokenOwner / TokenPrimaryGroup / TokenAppContainerSid:
    sid = owner->Owner;
    sid_len = offsetof( SID, SubAuthority[sid->SubAuthorityCount] );   <- reads byte 1 of a NULL SID
ntdll answers TokenAppContainerSid with success and a NULL SID, as Windows does for a token that is not an AppContainer's,
and the thunk faulted at address 1 (wow64.dll+0x16744): Garry's Mod's CEF launcher thread, 8 of 8 logs on 6 devices
(7S6YT7 = NHJSK8 build 622; T14SYR, 3VB49Y, 9Z35CW, DHBA30, 9RRBCX, MD7Z5X builds 574-614).
The patch (49 bytes): the `ldr x1, [sp]` at RVA 0x16740 branches to a cave in the 0xcc padding at the end of .text (RVA
0x21f90), which re-executes it and the next instruction (`ldrb w8, [x1, #1]`) and returns to 0x16748 when the SID is not NULL;
for a NULL SID it stores owner32->Owner = 0 and returns STATUS_SUCCESS with *retlen = 4 (STATUS_BUFFER_TOO_SMALL when len < 4),
through the function's own tails (0x169b4, 0x167a4). .text VirtualSize 0x11f8c -> 0x11fc0 so the cave is in the section's
declared code range. Register roles from the unchanged prologue: x20 = info (or 0), x21 = len, x19 = retlen (or 0), x22 the
value the tails store to *retlen. Every call that gets a real SID runs the same instructions plus two branches.
"""
import hashlib
import struct
import sys

SRC_SHA256 = "291f7827ac9862e1cea520b9a17d4441865f256d66d3d135e91decdfc2a75492"   # the committed DLL of builds 619-623
PATCHED_SHA256 = "904427947dad8556f1f29b6e5305e6af962220d8a023e57e94862e24f3927f8e"

SITE = 0x16740          # RVA = file offset (.text raw pointer == virtual address 0x10000)
ORIG = bytes.fromhex("e10340f9")   # ldr x1, [sp]
CAVE = 0x21f90
BACK = 0x16748          # lsl x8, x8, #2
TAIL_OK = 0x169b4       # mov w0, wzr ; cbz x19, +8 ; str w22, [x25] ; epilogue
TAIL_SMALL = 0x167a4    # mov w0, STATUS_BUFFER_TOO_SMALL ; ... *retlen = w22


def b(src, dst):
    off = (dst - src) // 4
    assert (dst - src) % 4 == 0 and -(1 << 25) <= off < (1 << 25)
    return struct.pack("<I", 0x14000000 | (off & 0x3ffffff))


def cbz(rt, src, dst):
    off = (dst - src) // 4
    assert -(1 << 18) <= off < (1 << 18)
    return struct.pack("<I", 0xb4000000 | ((off & 0x7ffff) << 5) | rt)


def bcond(cond, src, dst):
    off = (dst - src) // 4
    assert -(1 << 18) <= off < (1 << 18)
    return struct.pack("<I", 0x54000000 | ((off & 0x7ffff) << 5) | cond)


def cave_bytes():
    c = bytearray()
    pc = CAVE

    def emit(x):
        nonlocal pc
        c.extend(x)
        pc += 4
    nullsid, small = CAVE + 0x10, CAVE + 0x28
    emit(bytes.fromhex("e10340f9"))   # ldr  x1, [sp]        (the displaced instruction)
    emit(cbz(1, pc, nullsid))          # cbz  x1, nullsid
    emit(bytes.fromhex("28044039"))   # ldrb w8, [x1, #1]    (the original next instruction)
    emit(b(pc, BACK))                  # b    0x16748
    assert pc == nullsid
    emit(bytes.fromhex("96008052"))   # mov  w22, #4         (sizeof(TOKEN_OWNER32), stored to *retlen by the tail)
    emit(bytes.fromhex("bf1200f1"))   # cmp  x21, #4
    emit(bcond(3, pc, small))          # b.lo small
    emit(cbz(20, pc, small))           # cbz  x20, small      (no output buffer)
    emit(bytes.fromhex("9f0200b9"))   # str  wzr, [x20]      (owner32->Owner = 0)
    emit(b(pc, TAIL_OK))               # b    0x169b4         (STATUS_SUCCESS, *retlen = 4)
    assert pc == small
    emit(b(pc, TAIL_SMALL))            # b    0x167a4         (STATUS_BUFFER_TOO_SMALL, *retlen = 4)
    return bytes(c)


def patch(data):
    d = bytearray(data)
    assert d[SITE:SITE + 4] == ORIG, "site bytes differ"
    cave = cave_bytes()
    assert d[CAVE:CAVE + len(cave)] == b"\xcc" * len(cave), "the cave area is not the 0xcc padding of .text"
    d[CAVE:CAVE + len(cave)] = cave
    d[SITE:SITE + 4] = b(SITE, CAVE)
    pe = struct.unpack_from("<I", d, 0x3c)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    sec = pe + 24 + optsz
    for i in range(nsec):
        o = sec + 40 * i
        if bytes(d[o:o + 8]).rstrip(b"\0") == b".text":
            assert struct.unpack_from("<I", d, o + 8)[0] == 0x11f8c
            struct.pack_into("<I", d, o + 8, 0x11fc0)
            break
    else:
        raise SystemExit("no .text section")
    return bytes(d)


def main():
    args = sys.argv[1:]
    verify = "--verify" in args
    paths = [a for a in args if a != "--verify"]
    if len(paths) != 1:
        raise SystemExit(__doc__)
    path = paths[0]
    data = open(path, "rb").read()
    have = hashlib.sha256(data).hexdigest()
    if verify:
        if have != PATCHED_SHA256:
            raise SystemExit(f"patch-wow64-token-null-sid: {path} is not the patched wow64.dll (sha256 {have[:16]})")
        print(f"patch-wow64-token-null-sid: {path} is the patched form")
        return
    if have == PATCHED_SHA256:
        print(f"patch-wow64-token-null-sid: {path} is already patched")
        return
    if have != SRC_SHA256:
        raise SystemExit(f"patch-wow64-token-null-sid: {path} is neither the known wow64.dll nor its patched form "
                         f"(sha256 {have[:16]}): re-derive the offsets")
    out = patch(data)
    assert hashlib.sha256(out).hexdigest() == PATCHED_SHA256, "the result is not the expected bytes"
    open(path, "wb").write(out)
    print(f"patch-wow64-token-null-sid: patched {path} ({sum(1 for a, c in zip(data, out) if a != c)} bytes)")


if __name__ == "__main__":
    main()
