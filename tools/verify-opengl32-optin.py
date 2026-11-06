#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Check opengl32-optin.dll before it ships (OpenGL, gl-backend = zink or gles).

The shipped arm64ec-windows/opengl32.dll is a prebuilt file based at 0x7a800000: ARM64EC code loaded at a sub-4 GB base is run from an
emulated "sub-floor" window, where the thunks of Wine's opengl32 lose the TEB (NULL teb in every wgl call). The Wine fork builds
opengl32 with the default image base for everything but x86 (dlls/opengl32/Makefile.in), and tools/build-wine-extra-dlls.sh (block OGL)
builds it from the tree as opengl32-optin.dll. A game whose gl-backend key is on gets it as opengl32.dll for its session
(WineProcessBridge.m: the key adds opengl32 to MADEIRA_OPTIN_DLLS); every other game keeps the shipped file byte for byte.

  verify-opengl32-optin.py BUILT SHIPPED
      BUILT is for the shipped file's machine, is named opengl32.dll in its export directory, exports exactly the shipped DLL's names (a
      program sees the same entry points), and is NOT based at the shipped 0x7a800000. Exit 1 with the reasons otherwise.
"""
import importlib.util
import struct
import sys
from pathlib import Path

LOW_BASE = 0x7A800000

_spec = importlib.util.spec_from_file_location("kb_fibertrace", Path(__file__).resolve().parent / "patch-wine-kernelbase-fibertrace.py")
_kb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_kb)


def image_base(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x20B:
        return struct.unpack_from("<Q", data, opt + 24)[0]
    return struct.unpack_from("<I", data, opt + 28)[0]


def export_name(data):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    optsz = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    secs = []
    for i in range(nsec):
        s = opt + optsz + 40 * i
        vsz, va, rsz, raw = struct.unpack_from("<IIII", data, s + 8)
        secs.append((va, max(vsz, rsz), raw))

    def off(rva):
        for va, size, raw in secs:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError("rva outside the sections")

    e = off(struct.unpack_from("<I", data, opt + 112)[0])
    n = off(struct.unpack_from("<I", data, e + 12)[0])
    return data[n:data.index(b"\0", n)].decode("latin-1")


def problems(built, shipped):
    d, machine, names = _kb.pe_exports(built)
    sd, smachine, snames = _kb.pe_exports(shipped)
    bad = []
    if machine != smachine:
        bad.append("machine %#x, the shipped opengl32.dll's is %#x" % (machine, smachine))
    if export_name(d).lower() != "opengl32.dll":
        bad.append("the export directory names %r, not opengl32.dll" % export_name(d))
    if names != snames:
        bad.append("exports differ from the shipped opengl32.dll: only here %s, only there %s"
                   % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    base = image_base(d)
    if base == LOW_BASE:
        bad.append("based at %#x, the shipped file's sub-floor base: not built from the tree with the Wine fork's opengl32 change" % base)
    return bad, len(names), base


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    bad, n, base = problems(sys.argv[1], sys.argv[2])
    if bad:
        sys.exit("verify %s:\n  %s" % (sys.argv[1], "\n  ".join(bad)))
    print("verify %s: based at %#x, the %d exports of %s" % (sys.argv[1], base, n, sys.argv[2]))
