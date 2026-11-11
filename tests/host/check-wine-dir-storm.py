#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-dir-storm.py: [dir-slow] / [dir-storm] name where a process spends its time listing directories (night round 2026-11-11, A2 S4;
Spec Ops: The Line 0TF6NN, 82Y3VH: the Steam Client Service's thread sits at 95 % CPU in NtQueryDirectoryFile and no log says which directory).

Checks: CI runs this check and then the patch (after the other file.c patches, before the ntdll unix library is built); the patch applies to a copy
of the CI-patched dlls/ntdll/unix/file.c once, then says "already patched"; an anchor that moved fails by name and leaves the file alone; the hooks
are WINE_IOS only, the timing starts after dir_mutex is taken and the note runs before it is released, and nothing else changed; the real helper,
cut from the patched file and compiled on the host with a controllable clock and a real directory below ".../dosdevices/c:/", prints exactly the
documented lines: a [dir-storm] line for a ten-second window with 2 s or more in listings (24 at most), a [dir-slow] line for one listing of 500 ms
or more (12 at most), nothing for a quiet window, and the directory as the Windows drive sees it; MADEIRA_DIR_STORM=0 switches it off; with the iOS
SDK (the Mac) the patched file passes `clang -fsyntax-only` with the ntdll unix build's own flags. SKIP of the file part (exit 0) without a wine tree.
"""
from pathlib import Path
import importlib.util
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


spec = importlib.util.spec_from_file_location("dirstorm", root / "tools/patch-wine-dir-storm.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
step = wf.index("      - name: Patch wine with the directory-listing census\n")
check("CI runs this check and then the patch on file.c, after the other file.c patches and before the ntdll unix library is built",
      "python3 tests/host/check-wine-dir-storm.py\n          python3 tools/patch-wine-dir-storm.py wine/dlls/ntdll/unix/file.c\n" in wf[step:step + 500]
      and wf.index("python3 tools/patch-wine-hide-host-version-plist.py wine/dlls/ntdll/unix/file.c") < step < wf.index("      - name: Build ntdll-unix\n"))
cat = (root / "build/tools/gen-config-catalog.py").read_text()
i = cat.find('"env.MADEIRA_DIR_STORM"')
check("the switch is in the settings catalog (default on)", i > 0 and '"default": "1"' in cat[i:i + 700])

wine = None
for cand in (root / "wine", Path(os.environ.get("WINE_SRC", "/nonexistent")), Path.home() / "madeira-bcd/wine"):
    if (cand / P.FILE).is_file():
        wine = cand
        break
if not wine:
    print("SKIP: no wine source (check out the submodule or set WINE_SRC)")
    print("PASS: directory storm census (static part)" if ok else "FAIL: directory storm census")
    sys.exit(0 if ok else 1)

pristine = (wine / P.FILE).read_text()
if P.MARKER in pristine:
    print("SKIP: the wine tree is already patched; the checks need the unpatched file")
    print("PASS: directory storm census (static part)" if ok else "FAIL: directory storm census")
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    f = t / "w" / P.FILE
    f.parent.mkdir(parents=True)
    f.write_text(pristine)
    script = str(root / "tools/patch-wine-dir-storm.py")
    p1 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    patched = f.read_text()
    p2 = subprocess.run([sys.executable, script, str(f)], capture_output=True, text=True)
    check("applies once (the Wine directory or the file as argument), then says already patched",
          p1.returncode == 0 and "patched" in p1.stdout and p2.returncode == 0 and "already patched" in p2.stdout and f.read_text() == patched,
          p1.stdout + p1.stderr + p2.stdout + p2.stderr)
    fn = patched[patched.index("NTSTATUS WINAPI NtQueryDirectoryFile("):]
    fn = fn[:fn.index("\n}\n")]
    check("the timing starts after dir_mutex is taken, the note runs before it is released (data still valid), all of it WINE_IOS only",
          fn.index("mutex_lock( &dir_mutex );") < fn.index("madeira_ds_t0 = mach_absolute_time()") < fn.index("madeira_ds_note(")
          < fn.index("ios_dir_read_trace( status, io, data );") < fn.index("mutex_unlock( &dir_mutex );")
          and fn.count("#ifdef WINE_IOS") == fn.count("#endif") and fn.count("madeira_ds") == 6, fn.count("madeira_ds"))
    strip = patched
    strip = strip.replace(P.HELPER, "").replace(P.LOCALS_NEW, P.LOCALS_OLD).replace(P.START_NEW, P.START_OLD).replace(P.END_NEW, P.END_OLD)
    check("nothing else changed", strip == pristine)
    f.write_text(pristine.replace("    BOOL by_path = FALSE;\n\n    TRACE(\"(%p", "    BOOL by_path = FALSE;\n\n    TRACE( \"(%p", 1))
    moved = f.read_text()
    p3 = subprocess.run([sys.executable, script, str(t / "w")], capture_output=True, text=True)
    check("a moved anchor fails by name and leaves the file alone",
          p3.returncode != 0 and "local declarations" in p3.stderr and f.read_text() == moved, p3.stdout + p3.stderr)

    cc = shutil.which("cc")
    helper = patched[patched.index("#ifdef WINE_IOS\n/* " + P.MARKER):]
    helper = helper[:helper.index("#endif\n\n") + len("#endif\n")]
    harness = r'''
#define WINE_IOS 1
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <limits.h>
#include <unistd.h>
#include <mach/mach_time.h>
typedef unsigned short WCHAR;
typedef unsigned int ULONG;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { unsigned int Status; unsigned long Information; } IO_STATUS_BLOCK;
struct dir_data { unsigned int count, pos; };
static unsigned long long fake_now;
#define mach_absolute_time() fake_now
@HELPER@
static unsigned long long ticks(unsigned long long ms)
{
    mach_timebase_info_data_t tb;
    mach_timebase_info(&tb);
    return ms * 1000000ull * tb.denom / tb.numer;
}
int main(int argc, char **argv)
{
    int mode = atoi(argv[1]), i, fd = open(argv[2], O_RDONLY);
    IO_STATUS_BLOCK io = { 0, 8160 };
    struct dir_data data = { 31422, 1180 };
    WCHAR star = '*';
    UNICODE_STRING mask = { 2, 2, &star };
    unsigned long long t0;
    if (mode == 9) { printf("%d\n", madeira_ds_on()); return 0; }
    fake_now = ticks(1000);
    madeira_ds_note(fake_now, fd, 0, &io, &data, &mask);          /* the first call (no time passes) opens the window at t=1 s */
    switch (mode)
    {
    case 0: /* storm: ten calls of 300 ms, 7 s of quiet, then a 1 ms call that crosses the ten seconds */
        for (i = 0; i < 10; i++) { t0 = fake_now; fake_now += ticks(300); madeira_ds_note(t0, fd, 0, &io, &data, &mask); }
        fake_now += ticks(7000);
        t0 = fake_now; fake_now += ticks(1); madeira_ds_note(t0, fd, 0, &io, &data, &mask);
        break;
    case 1: /* one slow listing */
        t0 = fake_now; fake_now += ticks(812); madeira_ds_note(t0, fd, 0, &io, &data, &mask);
        break;
    case 2: /* quiet window: 100 calls of 5 ms, a window end */
        for (i = 0; i < 100; i++) { t0 = fake_now; fake_now += ticks(5); madeira_ds_note(t0, fd, 0, &io, &data, &mask); }
        fake_now += ticks(10000); t0 = fake_now; fake_now += ticks(1); madeira_ds_note(t0, fd, 0, &io, &data, &mask);
        break;
    case 3: /* 30 storm windows, 20 slow listings */
        for (i = 0; i < 30; i++)
        {
            t0 = fake_now; fake_now += ticks(3000); madeira_ds_note(t0, fd, 0, &io, &data, &mask);
            fake_now += ticks(10000); t0 = fake_now; fake_now += ticks(1); madeira_ds_note(t0, fd, 0, &io, &data, &mask);
        }
        for (i = 0; i < 20; i++) { t0 = fake_now; fake_now += ticks(600); madeira_ds_note(t0, fd, 0, &io, &data, &mask); }
        break;
    }
    return 0;
}
'''
    (t / "h.c").write_text(harness.replace("@HELPER@", helper))
    b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the inserted helper compiles on the host", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        d = t / "dosdevices" / "c:" / "windows" / "system32"
        d.mkdir(parents=True)

        def run(mode, env=None):
            e = dict(os.environ)
            e.pop("MADEIRA_DIR_STORM", None)
            e.update(env or {})
            p = subprocess.run([str(t / "h"), str(mode), str(d)], capture_output=True, text=True, env=e)
            return p.stdout.strip(), p.stderr.splitlines()
        o, l = run(0)
        storm = [x for x in l if x.startswith("[dir-storm]")]
        check("a window with ten 300 ms listings: one [dir-storm] line with the calls, the time, the bytes, the slowest call, the size and the path",
              len(storm) == 1 and not [x for x in l if x.startswith("[dir-slow]")]
              and re.fullmatch(r"\[dir-storm\] r652 10s: calls=12 ms-in-calls=300\d \(30% of the window\) bytes=97920 slowest=300 ms names=31422 "
                               r"dir=c:/windows/system32", storm[0]) is not None, l)
        o, l = run(1)
        check("one 812 ms listing: one [dir-slow] line with the status, bytes, names, position, mask and path",
              l == ["[dir-slow] r652 812 ms status=0x00000000 bytes=8160 names=31422 pos=1180 mask=* dir=c:/windows/system32"], l)
        o, l = run(2)
        check("a quiet window (100 calls of 5 ms) prints nothing", l == [], l)
        o, l = run(3)
        check("24 [dir-storm] lines and 12 [dir-slow] lines at most",
              len([x for x in l if x.startswith("[dir-storm]")]) == 24 and len([x for x in l if x.startswith("[dir-slow]")]) == 12, len(l))
        o, _ = run(9)
        o0, _ = run(9, {"MADEIRA_DIR_STORM": "0"})
        o1, _ = run(9, {"MADEIRA_DIR_STORM": "1"})
        check("MADEIRA_DIR_STORM=0 switches it off, unset and 1 leave it on", (o, o0, o1) == ("1", "0", "1"), (o, o0, o1))

    xcrun = shutil.which("xcrun")
    if not xcrun or not (wine / "build-macos/include/config.h").is_file():
        print("SKIP: the iOS-SDK syntax check needs Xcode and the full tree's wine/build-macos/include (the Mac)")
    else:
        src = t / "file.c"
        src.write_text(patched)
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        nb = root / "build/ntdll-unix"
        wb = wine / "build-macos"
        cmd = ["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
               "-fvisibility=hidden", "-fno-stack-protector", "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
               "-include", str(wb / "include/config.h"), "-include", str(nb / "shims/wine_ios_exit.h"), "-I" + str(nb / "shims"),
               "-I" + str(root / "build/madsync"), "-DHAVE_LINUX_NTSYNC_H=1", "-I" + str(wb / "dlls/ntdll"), "-I" + str(wine / "dlls/ntdll"),
               "-I" + str(wine / "dlls/ntdll/unix"), "-I" + str(wb / "include"), "-I" + str(wine / "include"), "-D__WINESRC__", "-DLTC_NO_PROTOTYPES",
               "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"', '-DLIBDIR="/usr/local/lib"',
               '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1", "-Dget_thread_context=ntdll_get_thread_context",
               "-Dset_thread_context=ntdll_set_thread_context", "-Wall", str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        mine = [l for l in r.stderr.splitlines() if ("warning:" in l or "error:" in l) and "madeira_ds" in r.stderr]
        base = subprocess.run(cmd[:-1] + [str(t / "base.c")], capture_output=True, text=True) if (t / "base.c").write_text(pristine) else None
        n_new = len([l for l in r.stderr.splitlines() if "warning:" in l or "error:" in l])
        n_old = len([l for l in base.stderr.splitlines() if "warning:" in l or "error:" in l])
        check("the patched file passes clang -fsyntax-only with the ntdll unix build's flags: no error and no warning the unpatched file lacks",
              r.returncode == 0 and "error:" not in r.stderr and n_new == n_old, (n_new, n_old, r.stderr[-800:]))

print("PASS: directory storm census" if ok else "FAIL: directory storm census")
sys.exit(0 if ok else 1)
