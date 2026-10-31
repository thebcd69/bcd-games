#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""pool-window-cut: the JIT pool census keeps clear of an executable window that was not held (round 624, agent R).

GTA V Legacy 71GQSA (and 12 archive logs): ml1034 could not hold [0x140000000, +128MB); the census still offered the hole that
contains it, the pool was sized to it and placed at its base over 0x140000000 ("BAD POOL placement ... swallows the 0x140000000
executable window"), and the re-roll never came back. With `pool-window-cut = 1` the holes are cut around the window and the uncut
hole first-fit would still pick is plugged for the request.

Checks: StikJITHelper.cutHolesAroundWindow compiled alone (swiftc) on 71GQSA's census and edge cases; the wiring is keyed
(`pool-window-cut`, off) and only for a window that was not held; the plug only after a cut; the catalog entry.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
src = (root / "app/Madeira/StikJITHelper.swift").read_text()
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


m = re.search(r"\n    static func cutHolesAroundWindow\(.*?\n    \}\n", src, re.S)
check("cutHolesAroundWindow is in StikJITHelper.swift", m is not None)
fn = m.group(0) if m else ""
check("the cut is keyed: pool-window-cut, and only when the window was not held",
      'MadeiraConfig.gameValue("pool-window-cut")' in src and "if windowCutOn && !windowHeld {" in src)
check("the uncut hole is plugged only after a cut and only for a single pool", "if holesCut && pairA == nil, let target" in src)
check("catalog entry pool-window-cut (default 0)", '"pool-window-cut": {' in (root / "build/tools/gen-config-catalog.py").read_text())

swiftc = shutil.which("swiftc")
if swiftc and fn:
    prog = "import Foundation\ntypealias vm_address_t = UInt64\nenum StikJITHelper {\n" + fn + "}\n" + r'''
let MB: UInt64 = 1 << 20
let W: UInt64 = 0x140000000, WS: UInt64 = 0x8000000
var fails = 0
func expect(_ c: Bool, _ what: String) { if !c { print("FAIL " + what); fails += 1 } }
// 71GQSA: 0x124894000+564MB (ends at 0x147c94000, inside the window), 0x14f400000+445MB, 0x16b744000+328MB
let census: [(base: vm_address_t, size: vm_address_t)] = [(0x124894000, 564 * MB), (0x14f400000, 445 * MB), (0x16b744000, 328 * MB)]
let cut = StikJITHelper.cutHolesAroundWindow(census, windowBase: W, windowSize: WS)
expect(cut.count == 3, "three holes after the cut")
expect(cut[0].base == 0x124894000 && cut[0].size == W - 0x124894000, "the hole is cut at the window's start (439MB)")
expect(cut[1].base == 0x14f400000 && cut[1].size == 445 * MB && cut[2].base == 0x16b744000, "holes above the window unchanged")
expect(!cut.contains { $0.base < W + WS && $0.base + $0.size > W }, "no hole touches the window")
// a hole that spans the window on both sides: two pieces
let span = StikJITHelper.cutHolesAroundWindow([(0x120000000, 0x60000000)], windowBase: W, windowSize: WS)
expect(span.count == 2 && span[0].size == W - 0x120000000 && span[1].base == W + WS, "a spanning hole becomes two pieces")
// pieces shorter than 64MB are dropped
let small = StikJITHelper.cutHolesAroundWindow([(W - 16 * MB, 100 * MB)], windowBase: W, windowSize: WS)
expect(small.isEmpty, "a 16MB piece below and a <64MB piece above are dropped")
// holes that do not touch the window are returned unchanged
let away: [(base: vm_address_t, size: vm_address_t)] = [(0x100000000, 256 * MB), (0x160000000, 300 * MB)]
let same = StikJITHelper.cutHolesAroundWindow(away, windowBase: W, windowSize: WS)
expect(same.count == 2 && same[0].base == away[0].base && same[0].size == away[0].size && same[1].base == away[1].base, "untouched holes unchanged")
if fails == 0 { print("cut-ok") }
exit(fails == 0 ? 0 : 1)
'''
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / "main.swift"
        f.write_text(prog)
        exe = Path(t) / "cut"
        b = subprocess.run([swiftc, "-o", str(exe), str(f)], capture_output=True, text=True)
        check("the helper compiles alone", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check("71GQSA's census and the edge cases", r.returncode == 0 and "cut-ok" in r.stdout, r.stdout + r.stderr)
else:
    print("skip the compiled run: no swiftc")
print("PASS: pool window cut" if ok else "FAILED: pool window cut")
sys.exit(0 if ok else 1)
