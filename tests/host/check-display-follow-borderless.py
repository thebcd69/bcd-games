#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_DISPLAY_FOLLOW_BORDERLESS: the virtual monitor follows a borderless window smaller than it (night round 2026-11-11, T2 S-DL1; opt-in).

Dying Light (T92QFA): after a change of the in-game resolution the game resized its window to a borderless 800x600 (style 14000000, at 0,0) inside the
1408x648 virtual monitor and never called ChangeDisplaySettings; BCD's display modes scale the whole monitor, so the picture stayed in a corner.
Checks: the switch is read once and only the exact value 1 turns it on (default off); the two callers in driver_ios.c (the window-position callback and
a pDestroyWindow wrapper installed only with the key) do nothing without it; the extern names match; CI runs this check; the real functions, cut from
build/win32u-unix/sysparams_ios.c and run on the host with stubs for Wine's window calls: a visible caption-less top-level window at 0,0 of 320x200 or
more and smaller than the session default (and not larger in either direction) sets the monitor to its size, once, without WM_DISPLAYCHANGE; a captioned
window, a child, a tool window, a splash below 320x200, a window off the origin or from another thread is ignored; the session default comes back when
the followed window is hidden, destroyed, moved, captioned or grown to the default; a second candidate takes over; a window that was never followed
changes nothing. With the iOS SDK (the Mac) both files pass `clang -fsyntax-only` with the win32u-unix build's flags.
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


sysp = (root / "build/win32u-unix/sysparams_ios.c").read_text()
drv = (root / "build/win32u-unix/driver_ios.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-display-follow-borderless.py" in wf)
start = sysp.index("static HWND ios_follow_hwnd;")
code = sysp[start:sysp.index("/* MADEIRA_VIRTUAL_MODE_SET=0: accept a listed mode without programming it", start)]
check("the switch is read once and only the exact value 1 turns it on (default off)",
      code.count('getenv( "MADEIRA_DISPLAY_FOLLOW_BORDERLESS" )') == 1 and "on = e && e[0] == '1' && !e[1];" in code and "static int on = -1;" in code)
check("the window-position callback and the destroy wrapper do nothing without the key",
      "if (ios_display_follow_enabled()) ios_display_follow_window( hwnd, swp_flags, &new_rects->visible );" in drv
      and "winios_user_driver.pDestroyWindow       = ios_display_follow_enabled() ? winios_drv_destroy_window" in drv
      and ": winios_pDestroyWindow;" in drv)
check("the three functions driver_ios.c declares are the ones sysparams_ios.c defines",
      all(("extern " + sig) in drv and sig.replace("(void);", "(void)").replace(";", "") .split("(")[0] for sig in
          ("int ios_display_follow_enabled(void);", "void ios_display_follow_window( HWND hwnd, UINT swp_flags, const RECT *visible );",
           "void ios_display_follow_window_gone( HWND hwnd );"))
      and "int ios_display_follow_enabled(void)\n{" in code and "void ios_display_follow_window( HWND hwnd, UINT swp_flags, const RECT *visible )\n{" in code
      and "void ios_display_follow_window_gone( HWND hwnd )\n{" in code)
sys.path.insert(0, str(root / "build/tools"))
import importlib.util
spec = importlib.util.spec_from_file_location("catalog", root / "build/tools/gen-config-catalog.py")
cat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cat)
check("the settings catalog's scanner finds the key in sysparams_ios.c", any(m.group(2) == "MADEIRA_DISPLAY_FOLLOW_BORDERLESS" for m in cat.ENV.finditer(sysp)))

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef void *HWND; typedef unsigned int UINT, DWORD; typedef int BOOL;
typedef struct { int left, top, right, bottom; } RECT;
#define TRUE 1
#define FALSE 0
#define STDERR_FILENO 2
#define GA_PARENT 1
#define GWL_STYLE (-16)
#define GWL_EXSTYLE (-20)
#define WS_CHILD 0x40000000u
#define WS_MINIMIZE 0x20000000u
#define WS_VISIBLE 0x10000000u
#define WS_CAPTION 0x00c00000u
#define WS_EX_TOOLWINDOW 0x00000080u
#define WS_EX_NOACTIVATE 0x08000000u
#define SWP_HIDEWINDOW 0x0080u
#define dprintf(fd, ...) printf(__VA_ARGS__)
/* the monitor, as sysparams_ios.c keeps it */
static int ios_screen_cur_w = 1408, ios_screen_cur_h = 648, ios_screen_def_w = 1408, ios_screen_def_h = 648;
static int publishes;
static void ios_screen_size(int *w, int *h) { *w = ios_screen_cur_w; *h = ios_screen_cur_h; }
static void ios_publish_screen_size(BOOL broadcast) { publishes++; if (broadcast) { printf("BROADCAST\n"); } }
/* the window calls */
static struct { HWND parent; unsigned thread; DWORD style, ex; } win;
static HWND desktop = (HWND)0x10;
static HWND NtUserGetAncestor(HWND h, int what) { return win.parent; }
static HWND get_desktop_window(void) { return desktop; }
static unsigned get_window_thread(HWND h, DWORD *pid) { return win.thread; }
static unsigned GetCurrentThreadId(void) { return 7; }
static DWORD get_window_long(HWND h, int off) { return off == GWL_STYLE ? win.style : win.ex; }
@CODE@
static void step(const char *name, HWND h, DWORD style, DWORD ex, UINT flags, int l, int t, int r, int b, unsigned thread, HWND parent)
{
    RECT v = { l, t, r, b };
    int before = publishes;
    win.parent = parent; win.thread = thread; win.style = style; win.ex = ex;
    ios_display_follow_window(h, flags, &v);
    printf("%s: %dx%d publishes=%d\n", name, ios_screen_cur_w, ios_screen_cur_h, publishes - before);
}
int main(int argc, char **argv)
{
    HWND A = (HWND)0xa, B = (HWND)0xb;
    const DWORD BORDERLESS = 0x14000000u, CAPTION = 0x14c80000u;
    if (!ios_display_follow_enabled()) { printf("off\n"); return 0; }
    step("captioned 806x632", A, CAPTION, 0, 0, 0, 0, 806, 632, 7, desktop);
    step("borderless 800x600", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("borderless 800x600 again", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("borderless 1024x600", A, BORDERLESS, 0, 0, 0, 0, 1024, 600, 7, desktop);
    step("grown to 1408x648", A, BORDERLESS, 0, 0, 0, 0, 1408, 648, 7, desktop);
    step("borderless 800x600 (2)", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("hidden", A, BORDERLESS, 0, SWP_HIDEWINDOW, 0, 0, 800, 600, 7, desktop);
    step("borderless 800x600 (3)", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("moved off the origin", A, BORDERLESS, 0, 0, 10, 10, 810, 610, 7, desktop);
    step("borderless 800x600 (4)", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("given a caption", A, CAPTION, 0, 0, 0, 0, 806, 632, 7, desktop);
    step("borderless 800x600 (5)", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    ios_display_follow_window_gone(B);
    printf("gone(B, never followed): %dx%d\n", ios_screen_cur_w, ios_screen_cur_h);
    ios_display_follow_window_gone(A);
    printf("gone(A): %dx%d\n", ios_screen_cur_w, ios_screen_cur_h);
    step("splash 200x100", A, BORDERLESS, 0, 0, 0, 0, 200, 100, 7, desktop);
    step("child window", A, BORDERLESS | WS_CHILD, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("tool window", A, BORDERLESS, WS_EX_TOOLWINDOW, 0, 0, 0, 800, 600, 7, desktop);
    step("other thread", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 8, desktop);
    step("not on the desktop", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, (HWND)0x99);
    step("minimised", A, BORDERLESS | WS_MINIMIZE, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("larger than the default", A, BORDERLESS, 0, 0, 0, 0, 1600, 700, 7, desktop);
    step("A 800x600", A, BORDERLESS, 0, 0, 0, 0, 800, 600, 7, desktop);
    step("B 640x480 takes over", B, BORDERLESS, 0, 0, 0, 0, 640, 480, 7, desktop);
    step("A changes (not followed now)", A, BORDERLESS, 0, 0, 0, 0, 1408, 648, 7, desktop);
    step("B grows", B, BORDERLESS, 0, 0, 0, 0, 1408, 648, 7, desktop);
    printf("publishes=%d\n", publishes);
    return 0;
}
'''.replace("@CODE@", code)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "h.c").write_text(harness)
    b = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-format", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the real functions compile on the host with stubs", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(key):
            env = dict(os.environ)
            env.pop("MADEIRA_DISPLAY_FOLLOW_BORDERLESS", None)
            if key is not None:
                env["MADEIRA_DISPLAY_FOLLOW_BORDERLESS"] = key
            p = subprocess.run([str(t / "h")], capture_output=True, text=True, env=env)
            return [l for l in p.stdout.splitlines() if not l.startswith("[display]")], [l for l in p.stdout.splitlines() if l.startswith("[display]")]
        for key in (None, "0", "11", "true"):
            check("key %r: the switch is off (only the exact value 1 turns it on)" % key, run(key)[0] == ["off"], run(key))
        out, logs = run("1")
        want = [
            "captioned 806x632: 1408x648 publishes=0",
            "borderless 800x600: 800x600 publishes=1",
            "borderless 800x600 again: 800x600 publishes=0",
            "borderless 1024x600: 1024x600 publishes=1",
            "grown to 1408x648: 1408x648 publishes=1",
            "borderless 800x600 (2): 800x600 publishes=1",
            "hidden: 1408x648 publishes=1",
            "borderless 800x600 (3): 800x600 publishes=1",
            "moved off the origin: 1408x648 publishes=1",
            "borderless 800x600 (4): 800x600 publishes=1",
            "given a caption: 1408x648 publishes=1",
            "borderless 800x600 (5): 800x600 publishes=1",
            "gone(B, never followed): 800x600",
            "gone(A): 1408x648",
            "splash 200x100: 1408x648 publishes=0",
            "child window: 1408x648 publishes=0",
            "tool window: 1408x648 publishes=0",
            "other thread: 1408x648 publishes=0",
            "not on the desktop: 1408x648 publishes=0",
            "minimised: 1408x648 publishes=0",
            "larger than the default: 1408x648 publishes=0",
            "A 800x600: 800x600 publishes=1",
            "B 640x480 takes over: 640x480 publishes=1",
            "A changes (not followed now): 640x480 publishes=0",
            "B grows: 1408x648 publishes=1",
            "publishes=14",
        ]
        check("key 1: the monitor follows, restores and ignores exactly as described, without a broadcast", out == want, [(a, b) for a, b in zip(out, want) if a != b] or out)
        check("each change logs one line with the sizes and the reason; no WM_DISPLAYCHANGE broadcast", len(logs) == 14 and not any("BROADCAST" in l for l in out)
              and "virtual monitor 1408x648 -> 800x600 (a borderless window smaller than the monitor," in logs[0] and "(that window changed," in logs[2], logs[:2])

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
        for name, known in (("sysparams_ios.c", []), ("driver_ios.c", ["warning: unused function 'nodrv_CreateWindow' [-Wunused-function]"])):
            cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                   "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
                   "-include", str(b_ / "config_ios.h"), "-include", str(sh_ / "wine_ios_exit.h"), "-I" + str(b_), "-I" + str(wb / "include"), "-I" + str(sh_),
                   "-I" + str(wb / "dlls/win32u"), "-I" + str(wine / "dlls/win32u"), "-I" + str(wine / "include"), "-D__WINESRC__", "-D_WIN32U_", "-D_ACRTIMP=",
                   "-DWINBASEAPI=", '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-D__wine_unix_lib_init=win32u_unix_lib_init",
                   "-USONAME_LIBFREETYPE", "-USONAME_LIBFONTCONFIG", "-USONAME_LIBEGL", "-USONAME_LIBVULKAN", "-USONAME_LIBGNUTLS", "-UHAVE_FT2BUILD_H", "-Wall",
                   str(b_ / name)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            warn = sorted(re.sub(r".*" + re.escape(name) + r":\d+:\d+: ", "", l) for l in r.stderr.splitlines() if "warning:" in l)
            check("%s passes clang -fsyntax-only with the win32u-unix build's flags: no error, only the file's known warnings" % name,
                  r.returncode == 0 and "error:" not in r.stderr and warn == known, (r.stderr[-800:], warn))

print("PASS: display follows a borderless window" if ok else "FAIL: display follows a borderless window")
sys.exit(0 if ok else 1)
