#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ARM64EC ntdll.dll: the `[rtcs] pre:` log line no longer reads the CPU area of a thread that has none (night round 2026-11-11, W-FEX: R1 S3b).

    tools/patch-ntdll-rtcs-null-area.py [--verify] app/Madeira/arm64ec-windows/ntdll.dll

app/Madeira/arm64ec-windows/ntdll.dll is upstream's TRACKED binary (the CI does not compile it), so the fork's source fix (dlls/ntdll/signal_arm64ec.c,
prepare_exception_arm64ec: `get_arm64ec_cpu_area() ? get_arm64ec_cpu_area()->InSimulation : 0`, see W-FEX-wine-ntdll.patch) reaches a device only through a
rebuilt ntdll.dll (build/wine-pe/build-ntdll.sh). This is the byte patch for the case that file is not rebuilt: it patches the DLL in place, once, and --verify checks
a file is the patched form.

The defect: a native ARM64 thread of an ARM64EC session (services.exe, cmd.exe, a 32-bit child) has TEB->ChpeV2CpuAreaInfo == NULL (build/ntdll-unix/thread_ios.c).
prepare_exception_arm64ec logs its first 40 exceptions (`ERR( "[rtcs] pre: ... insim=%u", ..., get_arm64ec_cpu_area()->InSimulation )`): the argument is evaluated
before any output filter (MADEIRA_RTCS_LOG only drops the printed line), so EVERY first exception of such a thread reads NULL+0 -- ntdll.dll+0x61378 -- and the
fault is redelivered into the same code: "[redeliv] 256 identical redeliveries ... addr=0x0" and the process is ended (KT9K9S, MRX800, tur626's 28 sessions).
The patch (4 bytes): the `ldrb w8, [x8]` at RVA 0x61378 (file offset 0x61378: .text raw pointer == virtual address 0x10000) becomes `mov w8, wzr`; the line prints
insim=0. Nothing else reads the area there (the instruction before, `ldr x8, [x18, #0x1788]`, is left as it is and only loads the pointer).

NOTE: ntdll-sync.dll and ntdll-xsec.dll are built from the fork's tree only when it first rebuilds the SHIPPED ntdll.dll byte for byte
(tools/build-wine-extra-dlls.sh). A byte-patched ntdll.dll no longer matches that rebuild: choose either this patch or the source fix with a rebuilt ntdll.dll
(which keeps the proof), not both.
"""
import hashlib
import struct
import sys

SRC_SHA256 = "596ba0e4853e56ed61e006a20651edfb86cf49162bb0ac9bedc91d5873d43ada"       # the committed DLL of build 651 (and later up to this round)
PATCHED_SHA256 = "8c391db8ec70e2736902f137de9526a66c3ebb0a000d2ea625b62948bd6b092a"
SITE = 0x61378                  # RVA = file offset
ORIG = bytes.fromhex("08014039")    # ldrb w8, [x8]
NEW = bytes.fromhex("e8031f2a")     # mov  w8, wzr


def sha(d):
    return hashlib.sha256(d).hexdigest()


def check_layout(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    n = struct.unpack_from("<H", d, pe + 6)[0]
    o = pe + 24 + struct.unpack_from("<H", d, pe + 20)[0]
    for _ in range(n):
        name = d[o:o + 8].rstrip(b"\0")
        va, rp = struct.unpack_from("<II", d, o + 12)[0], struct.unpack_from("<I", d, o + 20)[0]
        if name == b".text":
            return va == 0x10000 and rp == 0x10000
        o += 40
    return False


def main():
    args = sys.argv[1:]
    verify = "--verify" in args
    args = [a for a in args if a != "--verify"]
    if len(args) != 1:
        sys.exit(__doc__.strip().splitlines()[2])
    path = args[0]
    d = bytearray(open(path, "rb").read())
    if not check_layout(d):
        sys.exit("patch-ntdll-rtcs-null-area: .text is not at RVA = file offset 0x10000 (a different ntdll.dll)")
    if verify:
        if sha(d) == PATCHED_SHA256 and bytes(d[SITE:SITE + 4]) == NEW:
            print("ntdll.dll: the [rtcs] pre: log argument is not read through a NULL CPU area (patched form)")
            return
        sys.exit("patch-ntdll-rtcs-null-area: not the patched form (sha256 %s)" % sha(d))
    if sha(d) == PATCHED_SHA256:
        print("ntdll.dll: the [rtcs] pre: byte patch is already in; nothing to do")
        return
    if sha(d) != SRC_SHA256 or bytes(d[SITE:SITE + 4]) != ORIG:
        sys.exit("patch-ntdll-rtcs-null-area: this is not the ntdll.dll the patch was written for (sha256 %s); re-derive the site" % sha(d))
    d[SITE:SITE + 4] = NEW
    if sha(d) != PATCHED_SHA256:
        sys.exit("patch-ntdll-rtcs-null-area: the result's sha256 is not the expected one")
    open(path, "wb").write(d)
    print("ntdll.dll: ldrb w8,[x8] -> mov w8,wzr at 0x%x: the [rtcs] pre: line no longer reads a NULL CPU area" % SITE)


main()
