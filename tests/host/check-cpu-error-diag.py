#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-cpu-error-diag.py: [msgbox-origin] / [pf-xmmi64] log which CPU check Burnout Paradise fails.

On the pinned Wine source (when checked out): applies once (idempotent); the user32 hook runs in i386 builds only
(the 64-bit user32.dll BCD ships is a committed binary) and caps at 4 boxes per process; the wow64 hook logs once and only when feature 10 answers FALSE; the return
value of wow64_NtWow64IsProcessorFeaturePresent is unchanged. The workflow runs it.
"""
from pathlib import Path
import shutil, subprocess, sys, tempfile
root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok &= bool(cond)


mb = root / "wine/dlls/user32/msgbox.c"
sc = root / "wine/dlls/wow64/syscall.c"
if mb.is_file() and sc.is_file():
    with tempfile.TemporaryDirectory() as t:
        w = Path(t) / "wine"
        (w / "dlls/user32").mkdir(parents=True)
        (w / "dlls/wow64").mkdir(parents=True)
        # Start from the file as committed: a build tree's wine already carries the patch (the patch is applied in place),
        # and "applies once" is about the committed file.  Without git (an exported tree) the working file is used.
        def committed(rel, fallback):
            r = subprocess.run(["git", "-C", str(root / "wine"), "show", "HEAD:" + rel], capture_output=True, text=True)
            return r.stdout if r.returncode == 0 and r.stdout else fallback.read_text()
        (w / "dlls/user32/msgbox.c").write_text(committed("dlls/user32/msgbox.c", mb))
        (w / "dlls/wow64/syscall.c").write_text(committed("dlls/wow64/syscall.c", sc))
        p = root / "tools/patch-wine-cpu-error-diag.py"
        r1 = subprocess.run([sys.executable, str(p), str(w)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(p), str(w)], capture_output=True, text=True)
        check("applies once", r1.returncode == 0 and r1.stdout.count("applied") == 2 and "already" not in r1.stdout
              and r2.returncode == 0 and r2.stdout.count("already applied") == 2)
        m = (w / "dlls/user32/msgbox.c").read_text()
        check("user32: the i386 scan, 4 boxes at most, page faults caught, called once from MessageBoxIndirectW",
              "#ifdef __i386__\n/* madeira-bcd: [msgbox-origin]" in m and "> 4) return" in m and "__EXCEPT_PAGE_FAULT" in m
              and m.count("madeira_msgbox_origin( msgbox );") == 1 and m.count("static void madeira_msgbox_origin") == 2)
        # round 624: the 64-bit user32.dll is a committed binary nothing builds from this source, so the #else stays an empty function and
        # says why (a scan there would be dead code)
        check("user32: outside i386 an empty function that says why (the shipped 64-bit user32.dll is a committed binary)",
              "a committed binary that\n * no step builds from this source" in m and "static void madeira_msgbox_origin( const MSGBOXPARAMSW *mb ) { }" in m)
        s = (w / "dlls/wow64/syscall.c").read_text()
        check("wow64: logs once, only for PF_XMMI64 FALSE, returns the same value",
              "if (!ret && feature == PF_XMMI64_INSTRUCTIONS_AVAILABLE)" in s and "InterlockedExchange( &told, 1 )" in s
              and "    return ret;\n}" in s
              and "BOOLEAN ret = pBTCpuIsProcessorFeaturePresent && pBTCpuIsProcessorFeaturePresent( feature );" in s)
else:
    print("SKIP: no wine sources (check out the submodule)")
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs it", "python3 tools/patch-wine-cpu-error-diag.py wine" in wf)
print("PASS: cpu error diag" if ok else "FAILED: cpu error diag")
sys.exit(0 if ok else 1)
