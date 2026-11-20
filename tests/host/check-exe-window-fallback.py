#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 669 (S9; Hogwarts Legacy 6EW662, EAQKAS, build 665): when exe-window-mb's one fixed allocation is refused because a small
mapping lies inside the wanted range ("could not widen the executable window to 512MB (kr=3)"; a 1 MB malloc region at 0x15dd00000 /
0x159d00000), StikJITHelper takes the free part below that mapping, if it still holds a large program. Keyless: the path failed with
certainty before (the 371.5 MiB program then stopped with c0000018).

The pure functions are cut out of app/Madeira/StikJITHelper.swift and run with swiftc; the call site is checked as text."""
import shutil, subprocess, sys, tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
src = (R / "app/Madeira/StikJITHelper.swift").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + ("" if cond or not detail else "\n     " + str(detail)[-1200:]))
    ok = ok and bool(cond)


a = src.index("    static func exeWindowFallbackMB(")
b = src.index("    static func fixedBaseWindowMB(in folder: URL)")
funcs = src[a:b]
blk = src[src.index("// madeira-bcd exe-window-mb"):src.index("if reservePlan == nil {   // madeira-bcd pool-reserve: no census")]
fail = blk[blk.index("exeWindowWidenFailedMB = mb"):]
check("only after the whole request failed: the fallback sits in the old failure branch, after what was got is given back",
      blk.index("if kr == KERN_SUCCESS { releasePoolMapping(extra, vm_size_t(want - exeWinSize)) }") < blk.index("exeWindowFallbackMB(")
      and "exeWindowFallbackMB(" not in blk[:blk.index("} else {\n                if kr == KERN_SUCCESS { releasePoolMapping(extra")])
check("the first mapping above the window is read now (vm_region_64 from the window's end)",
      "var firstMapped: vm_address_t = exeWinBase + exeWinSize" in fail and "vm_region_64(mach_task_self_, &firstMapped" in fail)
check("fixed allocations from the fitted size down in 16 MB steps, never below the floor; a moved one is given back",
      "while fit >= floorMB {" in fail and "fit -= 16" in fail and "if kr2 == KERN_SUCCESS { releasePoolMapping(again, vm_size_t(size)) }" in fail
      and "kr2 == KERN_SUCCESS && again == exeWinBase + exeWinSize" in fail)
check("a fallback window reaches ntdll, keeps the phrase ExitWords reads and clears the 'could not widen' exit text",
      'exeWindowWidenFailedMB = nil' in fail and 'setenv("WINE_IOS_EXE_WINDOW"' in fail
      and '"[exe-window] executable window widened to [0x140000000,+%dMB) for this game' in fail)
check("without a fallback the old line and text stay", "[exe-window] could not widen the executable window to \\(mb)MB (kr=\\(kr))" in fail)

swiftc = shutil.which("swiftc")
if not swiftc:
    print("SKIP the compiled checks: no swiftc")
else:
    prog = "enum StikJITHelper {\n" + funcs + "}\n" + r'''
func show(_ v: Int?) -> String { v.map(String.init) ?? "nil" }
let w: UInt = 0x140000000
print("eaqkas", show(StikJITHelper.exeWindowFallbackMB(wantMB: 512, haveMB: 128, windowBase: w, firstMappedBase: 0x159d00000)))
print("6ew662", show(StikJITHelper.exeWindowFallbackMB(wantMB: 512, haveMB: 128, windowBase: w, firstMappedBase: 0x15dd00000)))
print("beyond", show(StikJITHelper.exeWindowFallbackMB(wantMB: 512, haveMB: 128, windowBase: w, firstMappedBase: 0x170000000)))
print("small", show(StikJITHelper.exeWindowFallbackMB(wantMB: 512, haveMB: 128, windowBase: w, firstMappedBase: 0x14a000000)))
print("inside", show(StikJITHelper.exeWindowFallbackMB(wantMB: 512, haveMB: 128, windowBase: w, firstMappedBase: 0x146000000)))
print("mgsv", show(StikJITHelper.exeWindowFallbackMB(wantMB: 176, haveMB: 128, windowBase: w, firstMappedBase: 0x14a000000)))
print("swgr", show(StikJITHelper.exeWindowFallbackMB(wantMB: 496, haveMB: 128, windowBase: w, firstMappedBase: 0x15a000000)))
print("floor", StikJITHelper.exeWindowFallbackFloorMB(wantMB: 512, haveMB: 128))
'''
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "m.swift").write_text(prog)
        c = subprocess.run([swiftc, str(t / "m.swift"), "-o", str(t / "m")], capture_output=True, text=True)
        check("the functions compile on the host", c.returncode == 0, c.stderr)
        if c.returncode == 0:
            out = dict(l.split() for l in subprocess.run([str(t / "m")], capture_output=True, text=True).stdout.split("\n") if l)
            check("EAQKAS: mapping at 0x159d00000 -> 400 MB (413 MB free; holds the 371.5 MiB program)", out["eaqkas"] == "400", out)
            check("6EW662: mapping at 0x15dd00000 -> 464 MB", out["6ew662"] == "464", out)
            check("a mapping beyond the wanted range is not this failure: nil", out["beyond"] == "nil", out)
            check("room under three quarters of the ask (160 MB of 512): nil, the old message", out["small"] == "nil", out)
            check("a mapping inside the window it already had: nil", out["inside"] == "nil", out)
            check("an ask under 256 MB (MGS V's 176): nil", out["mgsv"] == "nil", out)
            check("SWGR's 496 MB ask, room 416 MB (>= 372): 416", out["swgr"] == "416", out)
            check("the floor for 512 MB is 384 MB", out["floor"] == "384", out)

wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "run: python3 tests/host/check-exe-window-fallback.py" in wf)
print("PASS: exe-window fallback" if ok else "FAILED: exe-window fallback")
sys.exit(0 if ok else 1)
