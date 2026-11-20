#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""exe-window-auto and pool-above-window: the JIT pool around a large fixed-address program (StikJITHelper.swift, ContentView.swift).

A game whose main program has its relocations stripped can only run at 0x140000000, in the executable window. The window is
128 MB; a larger program (The Adventures of Elliot's Elliot-Win64-Shipping.exe, 473 MB; MapleStory.exe, 183 MB) overlapped the
JIT pool and failed to load with c0000018. BCD already widens the window from `exe-window-mb` in the game's own file (and
auto-tune adds that line after the failure). Two more things were ported:
  - exe-window-auto: every launch (a BCD Dock launch, and from build 680 a library launch) reads the PE headers of the programs
    in the game's folder (StikJITHelper.fixedBaseWindowMB) and widens the window to the largest fixed-address one, in 16 MB
    steps; an exe-window-mb of the game's own wins. On by default from build 680 (the owner, 2026-11-19, round 677 S1-S2: every
    such program failed its first start until a row or auto-tune's second start gave it a window); 0 in the game's file or
    bcd.cfg turns it off. It only acts when a fixed-base program larger than the window is in the folder, which cannot load
    today;
  - pool-above-window = 1: a `pool` line in the game's own file counts, and with pool-split the two runs above the window are
    paired also when the single pool would land above it (in the higher of two runs), with runs of 128 MB or more.

Checks:
  1. fixedBaseWindowMB, cut out of StikJITHelper.swift and run on synthetic folders of PE headers: the largest fixed-address
     64-bit program based in [0x140000000, 0x180000000) that ends above the 128 MB window sets the size (rounded up to 16 MB);
     relocatable programs, 32-bit programs, programs based elsewhere, programs that fit the window, other files, hidden folders
     and folders without a program give nothing; subfolders are read; only 4 KB of each file is read;
  2. the source: ContentView measures the folder only when the switch is on (and clears the value otherwise);
     allocatePool takes the value once and clears it, uses it only when the game's file names no exe-window-mb, and the size
     is capped at 960 MB; the pair above the window and the 128 MB run are gated by pool-above-window, the old test
     (a single run below the window, runs of 256 MB or more) stays for every other game; the game's pool line is read only with
     the switch.
Needs python3 and swiftc (--require-swift makes its absence a failure).
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


stik = (APP / "StikJITHelper.swift").read_text()
cv = (APP / "ContentView.swift").read_text()

# ---- 1. the measurement
a = stik.index("    static func fixedBaseWindowMB(in folder: URL) -> (mb: Int, exe: String)? {")
fn = stik[a:stik.index("\n    }\n", a) + 7]
swiftc = shutil.which("xcrun") and ["xcrun", "swiftc"] or (shutil.which("swiftc") and ["swiftc"])
if not swiftc:
    check("swiftc is here for the Swift part", not args.require_swift)
else:
    def pe(base=0x140000000, size=473 << 20, stripped=True, reloc_dir=False, magic=0x20B, machine=0x8664, dll=False):
        b = bytearray(1024)
        b[0:2] = b"MZ"
        struct.pack_into("<I", b, 0x3C, 0x80)
        b[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", b, 0x84, machine)
        characteristics = 0x0002 | (0x0001 if stripped else 0) | (0x2000 if dll else 0)
        struct.pack_into("<H", b, 0x80 + 22, characteristics)
        opt = 0x80 + 24
        struct.pack_into("<H", b, opt, magic)
        struct.pack_into("<Q", b, opt + 24, base)
        struct.pack_into("<I", b, opt + 56, size)
        struct.pack_into("<II", b, opt + 112 + 5 * 8, 0x1000 if reloc_dir else 0, 0x200 if reloc_dir else 0)
        return bytes(b)

    scenarios = {
        "elliot": ({"Binaries/Win64/Elliot-Win64-Shipping.exe": pe(), "Launcher.exe": pe(size=2 << 20), "readme.txt": b"x"}, "480:Elliot-Win64-Shipping.exe"),
        "rounding": ({"a.exe": pe(size=(128 << 20) + 1)}, "144:a.exe"),
        "largest": ({"small.exe": pe(size=200 << 20), "big.exe": pe(size=300 << 20), "mid.exe": pe(size=250 << 20)}, "304:big.exe"),
        "fits": ({"a.exe": pe(size=128 << 20), "b.exe": pe(size=100 << 20)}, "none"),
        "relocatable": ({"a.exe": pe(stripped=False, reloc_dir=True, size=600 << 20)}, "none"),
        "no-reloc-dir": ({"a.exe": pe(stripped=False, reloc_dir=False, size=300 << 20)}, "304:a.exe"),
        "32bit": ({"a.exe": pe(magic=0x10B, size=300 << 20)}, "none"),
        "based-elsewhere": ({"low.exe": pe(base=0x400000, size=300 << 20), "high.exe": pe(base=0x180000000, size=300 << 20)}, "none"),
        "not-an-exe": ({"a.dll": pe(size=300 << 20), "b.bin": pe(size=300 << 20), "c.exe": b"not a PE at all", "d.exe": b""}, "none"),
        "hidden": ({".hidden/a.exe": pe(size=300 << 20)}, "none"),
        "empty": ({}, "none"),
    }
    w0 = stik.index("    static func exeWindowAutoWanted(_ value: String?) -> Bool {")
    wanted_fn = stik[w0:stik.index("\n    }\n", w0) + 7]
    harness = '''import Foundation
enum StikJITHelper {
''' + fn + wanted_fn + '''
}
if CommandLine.arguments[1] == "--wanted" {
    let values: [String?] = [nil, "", " ", "1", "on", "yes", "true", "0", "off", "OFF", "no", "false", " 0 "]
    print(values.map { StikJITHelper.exeWindowAutoWanted($0) ? "1" : "0" }.joined())
    exit(0)
}
let folder = URL(fileURLWithPath: CommandLine.arguments[1])
if let r = StikJITHelper.fixedBaseWindowMB(in: folder) { print("\\(r.mb):\\(r.exe)") } else { print("none") }
'''
    with tempfile.TemporaryDirectory(prefix="exe-window-auto-") as tmp:
        t = Path(tmp)
        (t / "main.swift").write_text(harness)
        b = subprocess.run(swiftc + ["-O", str(t / "main.swift"), "-o", str(t / "m")], capture_output=True, text=True)
        check("fixedBaseWindowMB builds on the host", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            for name, (files, want) in scenarios.items():
                d = t / name
                d.mkdir()
                for rel, data in files.items():
                    f = d / rel
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(data)
                r = subprocess.run([str(t / "m"), str(d)], capture_output=True, text=True)
                check("%s: %s" % (name, want), r.stdout.strip() == want, r.stdout + r.stderr)
            r = subprocess.run([str(t / "m"), str(t / "no-such-folder")], capture_output=True, text=True)
            check("a folder that does not exist gives nothing", r.stdout.strip() == "none", r.stdout + r.stderr)
            r = subprocess.run([str(t / "m"), "--wanted"], capture_output=True, text=True)
            check("exe-window-auto is on unless the value says off: unset, empty, 1/on/yes/true on; 0/off/no/false (any case, spaces) off",
                  r.stdout.strip() == "1111111000000", r.stdout + r.stderr)

# ---- 2. the source
check("only 4 KB of each program is read, no more than 256 programs and 60,000 folder entries",
      "fh.read(upToCount: 4096)" in fn and "if seen > 256 { break }" in fn and "if walked > 60_000 { break }" in fn)
lib = (APP / "LibraryBCD.swift").read_text()
check("ContentView (Dock) and BCDLaunch.applyLibrary (library) measure when exe-window-auto is wanted, and clear the value first",
      "StikJITHelper.exeWindowAutoMB = 0\n            let autoWindow = " in cv
      and "if StikJITHelper.exeWindowAutoWanted(autoWindow), let need = StikJITHelper.fixedBaseWindowMB(in: installFolder) {" in cv
      and cv.count("StikJITHelper.fixedBaseWindowMB(") == 1
      and "StikJITHelper.exeWindowAutoMB = 0\n        if StikJITHelper.exeWindowAutoWanted(profile.get(\"exe-window-auto\") ?? MadeiraConfig.get(\"exe-window-auto\"))," in lib
      and "let need = StikJITHelper.fixedBaseWindowMB(in: folder) {" in lib and "LibraryEntry.hostDrive.standardizedFileURL.path" in lib)
check("allocatePool takes the value once and clears it",
      "let exeWindowAuto = exeWindowAutoMB" in stik and "exeWindowAutoMB = 0\n" in stik[stik.index("let exeWindowAuto = exeWindowAutoMB"):][:200])
widen = stik[stik.index("let explicitWindowMB = "):stik.index("let want = vm_address_t(mb) << 20")]
check("the measured size is used only when the game's file names no exe-window-mb, capped at 960 MB",
      "let windowFromAuto = explicitWindowMB == nil && exeWindowAuto > 128" in widen and "min(exeWindowAuto, 960)" in widen
      and "mb > Int(exeWinSize >> 20), mb <= 1024" in widen)
check("pool-above-window is read from the game's file, then bcd.cfg, and only '1' (on, true, yes) turns it on",
      stik.count('MadeiraConfig.gameValue("pool-above-window")') == 1 and 'let pairHigh = ["1", "on", "true", "yes"].contains(' in stik)
check("the old pair test stays for every other game: a single run below the window, runs of 256 MB or more",
      "if let s = singleAt, pairHigh || s.base + s.size <= exeWinBase {" in stik and "aFit >= vm_address_t(pairHigh ? 128 : 256) << 20" in stik)
check("the log line for a pair below the window is word for word what it was",
      '"below the window, where the pool cannot split"' in stik and "pool-pair = 0 turns this off)" in stik)
check("the game's own pool line counts only with pool-above-window",
      'let gamePool = poolAbove ? MadeiraConfig.gameValue("pool") : nil' in cv and "if let txt = gamePool ?? MadeiraConfig.get(\"pool\")," in cv)
import json
recs = json.loads((R / "recommended/recommendations.json").read_text())["rules"]
rules_py = (R / "build/tools/autotune_rules.py").read_text()
check("no recommended list and no auto-tune rule sets exe-window-auto", "exe-window-auto" not in json.dumps(recs) + rules_py)
check("pool-above-window is set by The Adventures of Elliot's list (build 603), Persona 5 Royal's (round 677, G2 S6: a 416 MB window cut its pool to 304 MB) "
      "and by no other list and no auto-tune rule",
      sorted(r["id"] for r in recs if "pool-above-window" in json.dumps(r["config"])) == ["persona-5-royal", "the-adventures-of-elliot"] and "pool-above-window" not in rules_py)

print("PASS: exe window auto" if ok else "FAIL: exe window auto")
sys.exit(0 if ok else 1)
