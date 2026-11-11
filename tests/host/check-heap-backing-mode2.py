#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""heap-backing = 2 (build 652; madeira-d3d12/src/pe/madeira_d3d12.c device_CreateHeap).

The Last of Us Part I ran with heap-backing = 0 (list v8, build 643: with 1 its 1.9 GB of texture-only heaps were committed at creation
and the main menu stood at 7.7 GB). With 0 every placed resource stands alone: the game reads through one placed buffer what it wrote
through another at the same heap offset, so it read zeros (the owner's start of 2026-11-10 10:42, build 651: faces black, walls missing;
the light-probe tree that walked itself on 2026-11-09), and its transient render targets never shared memory (killed at 8187 MB).
2 backs a DEFAULT heap only when it denies plain textures (D3D12_HEAP_FLAG_DENY_NON_RT_DS_TEXTURES): buffer heaps and render-target /
depth heaps alias as in 1; a texture heap stays a description, as in 0. Checks:
  - the key is read once (default 1, unchanged), the line names the mode;
  - the backing condition: DEFAULT heap with a size, and either a mode other than 2 or the DENY_NON_RT_DS_TEXTURES flag;
  - placed buffers and textures use the heap's Metal heap only when it has one (a texture heap of mode 2 has none: they stand alone);
  - a model of the decision over the heap flags The Last of Us creates;
  - CI runs this check.
"""
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1200])


src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ch = src[src.index("static HRESULT STDMETHODCALLTYPE device_CreateHeap("):]
ch = ch[:ch.index("\n}\n")]
check("the key is read once, default 1",
      'backing = (int)mad_cfg_int_pe("heap-backing", 1);' in ch and "static int backing = -1;" in ch and "if (backing < 0) {" in ch)
check("...the line names mode 2",
      'backing == 2 ? "that deny plain textures are Metal placement heaps; texture heaps are descriptions" :' in ch)
check("a DEFAULT heap is backed in mode 1 always, in mode 2 only when it denies plain textures",
      "if (backing && desc->Properties.Type == D3D12_HEAP_TYPE_DEFAULT && desc->SizeInBytes &&\n"
      "            (backing != 2 || (desc->Flags & D3D12_HEAP_FLAG_DENY_NON_RT_DS_TEXTURES))) {" in ch)
check("placed textures and buffers go into the heap only when it has a Metal heap",
      "if (ph && ph->mtl) {   /* ml1145: inside the application's heap, at its offset */" in src
      and "if (ph && ph->mtl && heap_type == D3D12_HEAP_TYPE_DEFAULT) {   /* ml1145: inside the application's heap */" in src)


def backed(mode, heap_type, flags, size=1):
    # the condition above, for D3D12_HEAP_TYPE_DEFAULT = 1 and DENY_NON_RT_DS_TEXTURES = 0x80
    return bool(mode and heap_type == 1 and size and (mode != 2 or (flags & 0x80)))


# the heaps of the 10:42 log: (type, flags) -> backed in mode 2
want = {(1, 0x44): False, (1, 0x844): False, (1, 0x84): True, (1, 0x884): True, (1, 0xc0): True, (1, 0x8c0): True,
        (1, 0x0): False, (2, 0xc0): False, (3, 0xc0): False}
got = {k: backed(2, *k) for k in want}
check("model: mode 2 backs buffer and RT/DS heaps, not texture heaps, not UPLOAD or READBACK", got == want, got)
check("model: mode 1 backs every DEFAULT heap, mode 0 none",
      all(backed(1, 1, f) for f in (0x44, 0x84, 0xc0, 0)) and not any(backed(0, 1, f) for f in (0x44, 0x84, 0xc0, 0)))

ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-heap-backing-mode2.py" in ci)

print("PASS: heap-backing-mode2" if ok else "FAILED: heap-backing-mode2")
sys.exit(0 if ok else 1)
