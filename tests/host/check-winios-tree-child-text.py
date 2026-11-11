#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[winios-tree] names the text of a Static or Button child (night round 2026-11-11, G6 S9; Surviving Mars 34BEY6, a 64-bit "Error" box).

A 64-bit program's message box left no text in the log: [win-name] reads a window's text when the window is positioned (a dialog sets its controls'
text afterwards, so the Static's line is empty) and [msgbox-origin] exists only for 32-bit programs (the 64-bit user32.dll is a committed binary). The
window-tree dump (every few seconds while a window is up) printed class, style and rect of a child but not its text. Checks: the dump line carries the
text between the class and the style, like the top-level lines do; the real helper (cut from build/win32u-unix/driver_ios.c) compiled on the host
with a stub for NtUserInternalGetWindowText: a Static and a Button give their text, any other class (an Edit, a list, a game's own control) is
not asked at all, non-printable and non-ASCII characters show as '?', the text is cut at 99 characters, an empty or failed read gives ""; CI runs this
check; with the iOS SDK (the Mac) driver_ios.c passes `clang -fsyntax-only` with the win32u-unix build's flags and has no warning the original lacked.
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
check("CI runs this check", "python3 tests/host/check-winios-tree-child-text.py" in wf)
dump = drv[drv.index("void winios_dump_window_tree(void)"):]
dump = dump[:dump.index("\n}\n")]
check("the child line carries the text between the class and the style; the text is read once per child, before the line",
      "winios_tree_child_text( kids[k], kcls, ktxt, sizeof(ktxt) );" in dump and "char kcls[32], ktxt[100];" in dump
      and "\"[winios-tree]    +child %p '%s' \\\"%s\\\" style=%08x vis=%d win={%d,%d,%d,%d}\\n\"" in dump
      and dump.index("winios_tree_child_text( kids[k]") < dump.index("+child %p"))
check("the top-level line and [win-name] keep their words (rules and tools read them)",
      '"[winios-tree] %p \'%s\' \\"%s\\" style=%08x ex=%08x vis=%d tid=%04x "' in dump
      and '"[win-name] #%u hwnd=%p class=\'%s\' text=\\"%s\\" rev=ml853\\n"' in drv)
helper = drv[drv.index("static void winios_tree_child_text("):]
helper = helper[:helper.index("\n}\n") + 3]
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    harness = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef unsigned short WCHAR;
typedef void *HWND;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static int asked;
static const WCHAR *stub_text; static int stub_len;
static int NtUserInternalGetWindowText( HWND hwnd, WCHAR *buf, int count )
{
    asked++;
    if (stub_len <= 0) return stub_len;
    if (stub_len > count) stub_len = count;
    memcpy(buf, stub_text, stub_len * sizeof(WCHAR));
    return stub_len;
}
@HELPER@
int main(int argc, char **argv)
{
    char out[100];
    WCHAR w[300];
    int mode = atoi(argv[2]), i;
    memset(out, 'x', sizeof(out));
    for (i = 0; i < 300; i++) w[i] = 'A' + (i % 26);
    switch (mode)
    {
    case 0: { const char *s = "Could not initialise."; for (i = 0; s[i]; i++) w[i] = (unsigned char)s[i]; stub_text = w; stub_len = i; break; }
    case 1: w[0] = 'a'; w[1] = 0x20ac; w[2] = 7; w[3] = 'z'; stub_text = w; stub_len = 4; break;
    case 2: stub_text = w; stub_len = 250; break;       /* longer than the buffer: the stub returns the buffer's count */
    case 3: stub_text = w; stub_len = 0; break;
    case 4: stub_text = w; stub_len = -1; break;
    }
    winios_tree_child_text((HWND)1, argv[1], out, sizeof(out));
    printf("asked=%d len=%d text=%s\n", asked, (int)strlen(out), out);
    return 0;
}
'''.replace("@HELPER@", helper)
    (t / "h.c").write_text(harness)
    b = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the real helper compiles on the host with a stub for NtUserInternalGetWindowText", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(cls, mode):
            return subprocess.run([str(t / "h"), cls, str(mode)], capture_output=True, text=True).stdout.strip()
        check("a Static gives its text", run("Static", 0) == "asked=1 len=21 text=Could not initialise.", run("Static", 0))
        check("a Button gives its text too", run("Button", 0) == "asked=1 len=21 text=Could not initialise.", run("Button", 0))
        for cls in ("Edit", "ComboBox", "SysListView32", "Chrome_RenderWidgetHostHWND", "", "Static2"):
            check("class '%s' is not asked at all (nothing a player types, nothing of a game's own control)" % cls, run(cls, 0) == "asked=0 len=0 text=", run(cls, 0))
        check("non-printable and non-ASCII characters show as '?'", run("Static", 1) == "asked=1 len=4 text=a??z", run("Static", 1))
        o = run("Static", 2)
        check("a long text is cut at 99 characters (the buffer is 100)", o.startswith("asked=1 len=99 text=ABCDEFGHIJKLMNOPQRSTUVWXYZ") , o)
        check("an empty text and a failed read give an empty string", run("Static", 3) == "asked=1 len=0 text=" and run("Static", 4) == "asked=1 len=0 text=",
              (run("Static", 3), run("Static", 4)))

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

        def syntax(path):
            cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                   "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
                   "-include", str(b_ / "config_ios.h"), "-include", str(sh_ / "wine_ios_exit.h"), "-I" + str(b_), "-I" + str(wb / "include"), "-I" + str(sh_),
                   "-I" + str(wb / "dlls/win32u"), "-I" + str(wine / "dlls/win32u"), "-I" + str(wine / "include"), "-D__WINESRC__", "-D_WIN32U_", "-D_ACRTIMP=",
                   "-DWINBASEAPI=", '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-D__wine_unix_lib_init=win32u_unix_lib_init",
                   "-USONAME_LIBFREETYPE", "-USONAME_LIBFONTCONFIG", "-USONAME_LIBEGL", "-USONAME_LIBVULKAN", "-USONAME_LIBGNUTLS", "-UHAVE_FT2BUILD_H", "-Wall", str(path)]
            return subprocess.run(cmd, capture_output=True, text=True)

        r = syntax(root / "build/win32u-unix/driver_ios.c")
        warn = sorted(re.sub(r".*driver_ios\.c:\d+:\d+: ", "", l) for l in r.stderr.splitlines() if "warning:" in l)
        check("driver_ios.c passes clang -fsyntax-only with the win32u-unix build's flags: no error, only the file's known warning",
              r.returncode == 0 and "error:" not in r.stderr and warn == ["warning: unused function 'nodrv_CreateWindow' [-Wunused-function]"], (r.stderr[-800:], warn))

print("PASS: winios-tree child text" if ok else "FAIL: winios-tree child text")
sys.exit(0 if ok else 1)
