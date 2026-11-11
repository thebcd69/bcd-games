#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[winios-tree] surface: whether anything was flushed into a recreated window surface (night round 2026-11-11, G5 S4b; Fear & Hunger 2 6Z8SV4).

After five clicks on Maximize the NW.js window got one new surface ("[surf-create] ... RECREATED") and nothing was presented for 40 s; the log could not say
whether Chromium stopped drawing or BCD stopped showing it. For every window whose surface was recreated (32 at most) the driver now keeps the number of
recreations, the flushes since the last one and when, and the window-tree dump prints `[winios-tree] surface hwnd=.. recreated=.. flushes-since=..
last-flush=never|N ms ago recreated-ago=N ms vis=..`. Checks: CI runs this check; the recreate note sits after the old surface is released and only for a
recreate (a window with a previous surface), the flush note is the first statement of the flush; the real table functions and the dump block, cut from
build/win32u-unix/driver_ios.c and run on the host with stubs: nothing is printed without a recreate (and the flush note returns at once); one window
recreated shows flushes-since=0 and last-flush=never, then the flushes it got, then restarts at 0 on the next recreate (recreated=2); a window that was never
recreated is ignored; a destroyed window is not printed; 40 windows fill the table to 32 without overflowing; with the iOS SDK (the Mac) driver_ios.c
passes `clang -fsyntax-only` with the win32u-unix build's flags and has only its known warning.
"""
from pathlib import Path
import os
import re
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


drv = (root / "build/win32u-unix/driver_ios.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-winios-surface-recreated.py" in wf)
check("the recreate note follows the release of the old surface and only runs for a recreate; the flush note is the first statement of the flush",
      "    if (previous) window_surface_release( previous );\n    if (previous) winios_note_recreated( hwnd );" in drv
      and "                                  const BITMAPINFO *shape_info, const void *shape_bits )\n{\n    if (surface) winios_note_flushed( surface->hwnd );" in drv)
a = drv.index("#define WINIOS_RECREATED_MAX 32")
table = drv[a:drv.index("/* madeira-bcd (night round 2026-11-11, G6 S9;", a)]
d = drv.index("    /* madeira-bcd (night round 2026-11-11, G5 S4b): the windows whose surface was recreated")
dump = drv[d:drv.index("\n}\n", d)]
harness = r'''
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
typedef void *HWND; typedef int BOOL;
#define dprintf(fd, ...) printf(__VA_ARGS__)
static int destroyed_hwnd;
static BOOL is_window(HWND h) { return (int)(long)h != destroyed_hwnd; }
static BOOL is_window_visible(HWND h) { return 1; }
@TABLE@
static void dump(void)
{
@DUMP@
}
int main(int argc, char **argv)
{
    int mode = atoi(argv[1]), i;
    switch (mode)
    {
    case 0: winios_note_flushed((HWND)1); dump(); printf("count=%u\n", winios_recreated_count); break;
    case 1:
        winios_note_recreated((HWND)1); dump();
        winios_note_flushed((HWND)1); winios_note_flushed((HWND)1); winios_note_flushed((HWND)1); winios_note_flushed((HWND)2); usleep(60000); dump();
        winios_note_recreated((HWND)1); dump();
        break;
    case 2: winios_note_recreated((HWND)1); winios_note_recreated((HWND)3); destroyed_hwnd = 3; dump(); break;
    case 3: for (i = 1; i <= 40; i++) winios_note_recreated((HWND)(long)i); printf("count=%u\n", winios_recreated_count); dump(); break;
    }
    return 0;
}
'''.replace("@TABLE@", table).replace("@DUMP@", dump)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "h.c").write_text(harness)
    b = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the real table functions and the dump block compile on the host with stubs", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(mode):
            return subprocess.run([str(t / "h"), str(mode)], capture_output=True, text=True).stdout.splitlines()
        o = run(0)
        check("without a recreate: the flush note returns at once, the dump prints nothing", o == ["count=0"], o)
        o = run(1)
        check("one window recreated: flushes-since=0, last-flush=never",
              o[0] == "[winios-tree] surface hwnd=0x1 recreated=1 flushes-since=0 last-flush=never recreated-ago=0 ms vis=1" or
              re.fullmatch(r"\[winios-tree\] surface hwnd=0x1 recreated=1 flushes-since=0 last-flush=never recreated-ago=\d ms vis=1", o[0]), o[:1])
        check("three flushes later: flushes-since=3 with a last-flush age; the flush of a window never recreated is ignored",
              re.fullmatch(r"\[winios-tree\] surface hwnd=0x1 recreated=1 flushes-since=3 last-flush=\d+ ms ago recreated-ago=\d+ ms vis=1", o[1]) and len(o) == 3, o)
        check("recreated again: recreated=2 and the flush count starts over",
              re.fullmatch(r"\[winios-tree\] surface hwnd=0x1 recreated=2 flushes-since=0 last-flush=never recreated-ago=\d ms vis=1", o[2]), o[2:])
        o = run(2)
        check("a destroyed window is not printed", len(o) == 1 and " hwnd=0x1 " in o[0], o)
        o = run(3)
        check("40 windows fill the table to 32 without overflowing; the last slot is reused (the dump shows 32 lines, the last is window 40)",
              o[0] == "count=32" and len(o) == 33 and " hwnd=0x28 " in o[-1] and " hwnd=0x20 " not in o[-1], (o[0], len(o), o[-1]))

    xcrun = shutil.which("xcrun")
    wine = next((c for c in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine")
                 if (c / "dlls/win32u/font.c").is_file() and (c / "build-macos/include/config.h").is_file()), None)
    if not xcrun or not wine:
        print("SKIP: the iOS-SDK syntax check needs Xcode and the full tree's wine/build-macos/include (the Mac)")
    else:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        b_ = root / "build/win32u-unix"
        sh_ = root / "build/ntdll-unix/shims"
        wb = wine / "build-macos"
        cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
               "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
               "-include", str(b_ / "config_ios.h"), "-include", str(sh_ / "wine_ios_exit.h"), "-I" + str(b_), "-I" + str(wb / "include"), "-I" + str(sh_),
               "-I" + str(wb / "dlls/win32u"), "-I" + str(wine / "dlls/win32u"), "-I" + str(wine / "include"), "-D__WINESRC__", "-D_WIN32U_", "-D_ACRTIMP=",
               "-DWINBASEAPI=", '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-D__wine_unix_lib_init=win32u_unix_lib_init",
               "-USONAME_LIBFREETYPE", "-USONAME_LIBFONTCONFIG", "-USONAME_LIBEGL", "-USONAME_LIBVULKAN", "-USONAME_LIBGNUTLS", "-UHAVE_FT2BUILD_H", "-Wall",
               str(b_ / "driver_ios.c")]
        r = subprocess.run(cmd, capture_output=True, text=True)
        warn = sorted(re.sub(r".*driver_ios\.c:\d+:\d+: ", "", l) for l in r.stderr.splitlines() if "warning:" in l)
        check("driver_ios.c passes clang -fsyntax-only with the win32u-unix build's flags: no error, only its known warning",
              r.returncode == 0 and "error:" not in r.stderr and warn == ["warning: unused function 'nodrv_CreateWindow' [-Wunused-function]"], (r.stderr[-800:], warn))

print("PASS: winios surface recreated census" if ok else "FAIL: winios surface recreated census")
sys.exit(0 if ok else 1)
