#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-shell32-navpane-null.py: closing an Explorer folder window whose navigation pane never opened does not call through NULL (night round
2026-11-11, A4 S5; Send Logs BWGQE7, N1455G: `shell32.dll+0x2c9f0`). Checks: the patch applies once to a copy of dlls/shell32/ebrowser.c (the Wine directory or
the file as argument), a second run changes nothing, a moved function refuses it by name; it adds only the guard; the real function, cut from the patched file
and run on the host with a stub tree control: with a NULL pane it returns TRUE and calls nothing, with a pane it unadvises, releases and clears it as before; with
llvm-mingw and the full tree's build-i386 (the Mac) the patched file compiles for the i386 PE build with its own flags, no error and no warning.
WHERE THIS REACHES: no CI step applies the patch (the crashing 64-bit shell32.dll is a committed binary); see the night-round note W-WINE.md (A4-S5).
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location("navpane", root / "tools/patch-wine-shell32-navpane-null.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
wine = next((c for c in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine") if (c / P.FILE).is_file()), None)
if not wine:
    print("SKIP: no wine source (check out the submodule or set WINE_SRC)")
    print("PASS: navpane null guard (static part)" if ok else "FAIL: navpane null guard")
    sys.exit(0 if ok else 1)
pristine = (wine / P.FILE).read_text()
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    f = t / "w" / P.FILE
    f.parent.mkdir(parents=True)
    f.write_text(pristine)
    script = str(root / "tools/patch-wine-shell32-navpane-null.py")
    p1 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    patched = f.read_text()
    p2 = subprocess.run([sys.executable, script, str(f)], capture_output=True, text=True)
    check("applies once (the Wine directory or the file as argument), then says already patched",
          p1.returncode == 0 and "patched" in p1.stdout and p2.returncode == 0 and "already patched" in p2.stdout and f.read_text() == patched, p1.stdout + p1.stderr + p2.stdout)
    check("nothing but the guard changed", patched.replace(P.NEW, P.OLD) == pristine)
    f.write_text(pristine.replace("INameSpaceTreeControl2_TreeUnadvise(This->navpane.pnstc2, This->navpane.nstc_cookie);", "INameSpaceTreeControl2_TreeUnadvise( This->navpane.pnstc2, This->navpane.nstc_cookie );", 1))
    moved = f.read_text()
    p3 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    check("a moved function fails by name and leaves the file alone", p3.returncode != 0 and "navpane_on_wm_destroy of shell32/ebrowser.c" in p3.stderr and f.read_text() == moved, p3.stderr)

    fn = patched[patched.index("static LRESULT navpane_on_wm_destroy(ExplorerBrowserImpl *This)"):]
    fn = fn[:fn.index("\n}\n") + 3]
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
typedef long LRESULT; typedef unsigned long DWORD;
#define TRUE 1
typedef struct INameSpaceTreeControl2 INameSpaceTreeControl2;
typedef struct { struct { INameSpaceTreeControl2 *pnstc2; DWORD nstc_cookie; } navpane; } ExplorerBrowserImpl;
static int unadvised, released;
static void INameSpaceTreeControl2_TreeUnadvise(INameSpaceTreeControl2 *p, DWORD cookie) { if (!p) { printf("NULL CALL\n"); exit(7); } unadvised++; }
static void INameSpaceTreeControl2_Release(INameSpaceTreeControl2 *p) { if (!p) { printf("NULL CALL\n"); exit(7); } released++; }
@FN@
int main(int argc, char **argv)
{
    ExplorerBrowserImpl e;
    LRESULT r;
    e.navpane.pnstc2 = argc > 1 ? (INameSpaceTreeControl2 *)0x10 : NULL;
    e.navpane.nstc_cookie = 5;
    r = navpane_on_wm_destroy(&e);
    printf("ret=%ld unadvised=%d released=%d pane=%s\n", r, unadvised, released, e.navpane.pnstc2 ? "set" : "null");
    return 0;
}
'''.replace("@FN@", fn)
    (t / "h.c").write_text(harness)
    b = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the real function compiles on the host with a stub tree control", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        r0 = subprocess.run([str(t / "h")], capture_output=True, text=True)
        r1 = subprocess.run([str(t / "h"), "pane"], capture_output=True, text=True)
        check("a NULL pane: returns TRUE, nothing is called", r0.returncode == 0 and r0.stdout.strip() == "ret=1 unadvised=0 released=0 pane=null", (r0.stdout, r0.returncode))
        check("a pane: unadvised, released and cleared as before", r1.returncode == 0 and r1.stdout.strip() == "ret=1 unadvised=1 released=1 pane=null", r1.stdout)
        unpatched = harness.replace(fn, pristine[pristine.index("static LRESULT navpane_on_wm_destroy(ExplorerBrowserImpl *This)"):][:pristine[pristine.index("static LRESULT navpane_on_wm_destroy(ExplorerBrowserImpl *This)"):].index("\n}\n") + 3])
        (t / "u.c").write_text(unpatched)
        if subprocess.run([shutil.which("cc"), "-std=gnu11", "-o", str(t / "u"), str(t / "u.c")], capture_output=True).returncode == 0:
            ru = subprocess.run([str(t / "u")], capture_output=True, text=True)
            check("the unpatched function calls through the NULL pane (this check fails without the patch)", "NULL CALL" in ru.stdout and ru.returncode == 7, ru.stdout)

    mingw = Path.home() / "madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin"
    bd = Path.home() / "madeira-bcd/wine/build-i386"
    if not (mingw / "i686-w64-mingw32-gcc").is_file() or not (bd / "include").is_dir():
        print("SKIP: the PE compile needs llvm-mingw and the full tree's wine/build-i386 (the Mac)")
    else:
        env = dict(os.environ, PATH=str(mingw) + os.pathsep + os.environ["PATH"])

        def pe(text, name):
            src = t / (name + ".c")
            src.write_text(text)
            cmd = ["i686-w64-mingw32-gcc", "-D__STDC__", "-c", "-o", str(t / (name + ".o")), str(src), "-I" + str(bd / "dlls/shell32"), "-I" + str(wine / "dlls/shell32"),
                   "-I" + str(bd / "include"), "-I" + str(wine / "include"), "-I" + str(wine / "include/msvcrt"), "-D_UCRT", "-D__WINESRC__", "-D_SHELL32_",
                   "-D__WINE_PE_BUILD", "-Wall", "-target", "i686-windows", "-fuse-ld=lld", "--no-default-config", "-fno-strict-aliasing", "-Wdeclaration-after-statement",
                   "-Wempty-body", "-Wignored-qualifiers", "-Winit-self", "-Wno-microsoft-enum-forward-reference", "-Wstrict-prototypes", "-Wtype-limits",
                   "-Wunused-but-set-parameter", "-Wvla", "-Wwrite-strings", "-Wpointer-arith", "-Wabsolute-value", "-ffunction-sections", "-fasync-exceptions",
                   "-ffp-exception-behavior=maytrap", "-fms-hotpatch", "-fno-omit-frame-pointer", "-mlong-double-64", "-O2"]
            return subprocess.run(cmd, capture_output=True, text=True, env=env)

        r = pe(patched, "ebrowser")
        check("the patched ebrowser.c compiles for the i386 PE build with the build's flags (no error, no warning)", r.returncode == 0 and not r.stderr.strip(), r.stderr[-800:])

print("PASS: navpane null guard" if ok else "FAIL: navpane null guard")
sys.exit(0 if ok else 1)
