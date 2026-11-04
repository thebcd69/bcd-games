#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A pseudo-process's error status reaches the app whole (round 630, agent G3 S19; the Resident Evil Requiem demo RVW5FD).

get_unix_exit_code (build/ntdll-unix/thread_ios.c) turned every nonzero status whose low byte is zero into 1, so Wine's stub end
0x80000100 ("unimplemented function ... aborting") reached the app as a plain 1 and the Dock watch kept the session open on a dead
desktop. On iOS a status with the high bit set is now kept whole; the bridge keeps 0x80000100 for a library start's exit report.

Compiles the function cut from thread_ios.c with and without WINE_IOS: 0x80000100 -> 0x80000100 (iOS) / 1 (not), 0x100 -> 1,
0xC0000005 -> 0xC0000005, 0 -> 0, 0x4000 -> 1; and checks the bridge's filter and the Dock rule; CI runs this check.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/thread_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1200]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


fn = src[src.index("static inline int get_unix_exit_code( NTSTATUS status )"):]
fn = fn[:fn.index("\n}\n") + 3]
bridge = (root / "app/Madeira/WineProcessBridge.m").read_text()
check("the bridge keeps 0x80000100 for a library start", "if ((uint32_t)status >= 0xC0000000u || (uint32_t)status == 0x80000100u)" in bridge)
dock = (root / "app/Madeira/DockGameEnd.swift").read_text()
check("the Dock watch reads it as a crash", "static let wineStubStatus: UInt32 = 0x8000_0100" in dock and "|| status == wineStubStatus" in dock)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-exit-status-whole.py" in wf)
harness = "#include <stdio.h>\n#include <stdlib.h>\ntypedef int NTSTATUS;\n" + fn + r'''
int main( void )
{
#ifdef WINE_IOS
    if (get_unix_exit_code( (NTSTATUS)0x80000100 ) != (int)0x80000100) { puts( "FAIL stub" ); return 1; }
#else
    if (get_unix_exit_code( (NTSTATUS)0x80000100 ) != 1) { puts( "FAIL stub (not iOS)" ); return 1; }
#endif
    if (get_unix_exit_code( 0x100 ) != 1 || get_unix_exit_code( (NTSTATUS)0xC0000005 ) != (int)0xC0000005
        || get_unix_exit_code( 0 ) != 0 || get_unix_exit_code( 0x4000 ) != 1) { puts( "FAIL others" ); return 1; }
    puts( "ok" );
    return 0;
}
'''
cc = os.environ.get("CC", "cc")
if shutil.which(cc):
    with tempfile.TemporaryDirectory(prefix="madeira-exit-whole-") as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(harness)
        for ios in (True, False):
            exe = Path(tmp) / ("t-ios" if ios else "t")
            b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror"] + (["-DWINE_IOS=1"] if ios else []) + [str(c), "-o", str(exe)],
                               capture_output=True, text=True)
            r = subprocess.run([str(exe)], capture_output=True, text=True) if b.returncode == 0 else None
            check(("iOS" if ios else "other builds") + ": the statuses as they should be",
                  b.returncode == 0 and r is not None and r.returncode == 0, (b.stderr + (r.stdout if r else ""))[-600:])
print("PASS: an error status reaches the app whole" if ok else "FAILED: exit status")
sys.exit(0 if ok else 1)
