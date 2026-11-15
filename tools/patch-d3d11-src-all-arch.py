#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""d3d11-src.dll gets the Direct3D 10/11 fixes the pin compiles for 32-bit builds only.

BCD ran tools/patch-dxmt-cb-short.py, patch-dxmt-idx-align.py and the [vb-align] census of patch-dxmt-vb-align.py in every 32-bit
Direct3D 10/11 program from 2026-09-30; upstream Madeira took them into DXMT as 1b4f3dc (willfaust/dxmt#33, in BCD's pin since
2dc8d16) "in the form it shipped them": every hunk inside `#if defined(__i386__)  /* madeira-bcd: 32-bit (i386) builds only, as the
fork shipped it */ ... #else <the original> #endif`, because the 64-bit d3d11.dll was upstream's committed binary. So no 64-bit
game has ever had them, d3d11-src.dll included:
  * short constant buffers (keyless): a constant buffer bound shorter than the shader's dcl_constantbuffer reads 0 past its end, as
    Direct3D defines, instead of whatever memory follows it ([cb-short]);
  * 16-bit index realignment (on; env.MADEIRA_IDX_REALIGN = 0 turns it off): a 16-bit indexed draw whose offset is 2 mod 4 draws
    from an aligned copy of its range ([idx-align]);
  * the [vb-align] census of vertex buffer bindings that are not 4-byte aligned (log only; the byte-aligned fetch itself is airconv's,
    which runs natively for every game already).

This turns those 25 gates into `#if 1` in d3d11-src.dll's COPY of dxmt/src (tools/build-d3d11-dll.sh, BCD_PATCHES), so the 64-bit
DLL compiles the same code the 32-bit one runs. The submodule is never touched: the native Direct3D 9 frontend and the i386 farm
build from it as before, and the name stays outside the i386 farm's tools/patch-dxmt-*.py cache key. The other `__i386__` tests of
the tree (the WoW64 memory placement, `&& !defined(DXMT_MADEIRA)`) are not these gates and stay.

Exits 1, leaving every file as it was, when a file holds another number of gates than the pin (a moved pin: review this script).
A tree whose sources carry the fixes without a gate (a later pin) is left alone. Idempotent.

Usage: patch-d3d11-src-all-arch.py [<copy of dxmt/src>]   (default: dxmt/src under the current directory, as BCD_PATCHES runs it)
"""
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dxmt/src")
GATE = "#if defined(__i386__)  /* madeira-bcd: 32-bit (i386) builds only, as the fork shipped it */"
DONE = "#if 1  /* madeira-bcd: 32-bit (i386) builds only, as the fork shipped it; d3d11-src.dll: every architecture (tools/patch-d3d11-src-all-arch.py) */"
# The pin's gates (dxmt 1b4f3dc): short constant buffers, 16-bit index realignment, the [vb-align] census.
EXPECT = {
    "d3d11/d3d11_context_impl.cpp": 7,
    "d3d11/d3d11_pipeline_cache.cpp": 4,
    "d3d11/d3d11_shader.hpp": 1,
    "dxmt/dxmt_command_queue.hpp": 1,
    "dxmt/dxmt_context.cpp": 6,
    "dxmt/dxmt_context.hpp": 6,
}
UNGATED = ("d3d11/d3d11_context_impl.cpp", "madeira-bcd: short constant buffers")


def main():
    texts = {}
    for rel in EXPECT:
        p = ROOT / rel
        if not p.is_file():
            print(f"patch-d3d11-src-all-arch: {p} is missing")
            return 1
        texts[rel] = p.read_text()
    gates = {rel: s.count(GATE) for rel, s in texts.items()}
    done = {rel: s.count(DONE) for rel, s in texts.items()}
    if not any(gates.values()):
        if done == EXPECT:
            print("d3d11-src all-arch: the 25 gates are already open")
            return 0
        if not any(done.values()) and UNGATED[1] in texts[UNGATED[0]]:
            print("d3d11-src all-arch: the pin carries the fixes without the i386 gate; nothing to do")
            return 0
    bad = {rel: (gates[rel], done[rel], n) for rel, n in EXPECT.items() if gates[rel] + done[rel] != n}
    if bad:
        for rel, (g, d, n) in bad.items():
            print(f"patch-d3d11-src-all-arch: {rel} has {g} gate(s) and {d} opened, the pin had {n}: the pin moved, review this script")
        return 1
    for rel, s in texts.items():
        if gates[rel]:
            (ROOT / rel).write_text(s.replace(GATE, DONE))
    left = sum((ROOT / rel).read_text().count(GATE) for rel in EXPECT)
    if left:
        print(f"patch-d3d11-src-all-arch: {left} gate(s) left after the edit")
        return 1
    print(f"d3d11-src all-arch: {sum(gates.values())} gates opened (short constant buffers, 16-bit index realignment, [vb-align] census)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
