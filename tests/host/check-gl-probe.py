#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A GL window's 120th presented frame is probed: how much is not black, and its mean colour (night round 2026-11-11, T3 S11; Akka Arrh 8BBTB3, Soldier
of Fortune JHANDP: the first Zink device logs, "No visuals" and "texture / graphical problems", and nothing in the log tells a black picture from a wrongly
drawn one). app/Madeira/Winios/WiniosGL.m logs, once per presenter, `[winios-gl] hwnd 0x..: probe at frame 120 (WxH, N samples): non-black P%, mean rgb
R,G,B, varied|flat`, from a read-only lock of the slot's IOSurface and a grid of at most 64x64 samples. Checks: CI runs this check; the probe runs once, in
present_slot after the frame was committed and counted, only at the 120th frame, and gives up when the slot is locked for the CPU or cannot be locked; the real
pixel function (plain C, cut from WiniosGL.m) compiled on the host over synthetic BGRA buffers: black is 0 %, a solid colour gives that colour (100 % and
flat), half black / half white gives about 50 % and varied, row padding is ignored, a surface smaller than its row or a NULL base gives no samples, a 2752x2064
surface is sampled on at most 64x64 points; with the iOS SDK (the Mac) WiniosGL.m passes `clang -fsyntax-only` (Objective-C, ARC) with no warning.
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


gl = (root / "app/Madeira/Winios/WiniosGL.m").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-gl-probe.py" in wf)
present = gl[gl.index("static BOOL present_slot("):]
present = present[:present.index("\n}\n")]
check("the probe runs once, at the 120th frame, after the commit and the count", present.index("[cb commit];") < present.index("gl_present_count") < present.index("++p->frames")
      < present.index("if (p->frames == 120) gl_probe_slot(p, s, hwnd);") and present.count("gl_probe_slot(") == 1)
probe = gl[gl.index("static void gl_probe_slot("):]
probe = probe[:probe.index("\n}\n")]
check("the probe gives up for a missing or CPU-locked surface and reads it with a read-only lock, then unlocks it",
      "if (!s->surface || s->locked) return;" in probe and "IOSurfaceLock(s->surface, kIOSurfaceLockReadOnly, NULL) != 0) return;" in probe
      and probe.index("IOSurfaceLock(") < probe.index("madeira_gl_probe_pixels(") < probe.index("IOSurfaceUnlock(s->surface, kIOSurfaceLockReadOnly, NULL);"))
fn = gl[gl.index("static int madeira_gl_probe_pixels("):]
fn = fn[:fn.index("\n}\n") + 3]
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
@FN@
int main(int argc, char **argv)
{
    int mode = atoi(argv[1]), w = atoi(argv[2]), h = atoi(argv[3]), pad = atoi(argv[4]);
    size_t stride = (size_t)w * 4 + pad;
    uint8_t *buf = calloc(stride * (h > 0 ? h : 1) + 16, 1);
    int x, y, a, r, g, b, v, n;
    for (y = 0; y < h; y++)
        for (x = 0; x < w; x++)
        {
            uint8_t *px = buf + y * stride + x * 4;
            if (mode == 1) { px[0] = 30; px[1] = 10; px[2] = 200; px[3] = 255; }                     /* BGRA: R=200 G=10 B=30 */
            else if (mode == 2) { int c = x < w / 2 ? 0 : 255; px[0] = px[1] = px[2] = c; px[3] = 255; }
            else if (mode == 3) { px[0] = px[1] = px[2] = (x + y) & 0xff; }
        }
    for (y = 0; y < h; y++) memset(buf + y * stride + (size_t)w * 4, 0xff, pad);                      /* padding is garbage */
    if (argc > 5) { n = madeira_gl_probe_pixels(NULL, stride, w, h, &a, &r, &g, &b, &v); }
    else n = madeira_gl_probe_pixels(buf, mode == 9 ? (size_t)w * 4 - 1 : stride, w, h, &a, &r, &g, &b, &v);
    printf("%d %d %d %d %d %d\n", n, a, r, g, b, v);
    return 0;
}
'''.replace("@FN@", fn)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / "h.c").write_text(harness)
    b = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Wall", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
    check("the real pixel function compiles on the host", b.returncode == 0, b.stderr[-1500:])
    if b.returncode == 0:
        def run(mode, w, h, pad=0, null=False):
            args = [str(t / "h"), str(mode), str(w), str(h), str(pad)] + (["null"] if null else [])
            return [int(x) for x in subprocess.run(args, capture_output=True, text=True).stdout.split()]
        check("all black: 0 % non-black, mean 0,0,0, flat", run(0, 100, 100) == [10000, 0, 0, 0, 0, 0], run(0, 100, 100))
        check("a solid colour (BGRA bytes B=30 G=10 R=200): 100 % non-black, that colour as R,G,B, flat", run(1, 100, 100) == [10000, 100, 200, 10, 30, 0], run(1, 100, 100))
        n, nb, r_, g_, b_, v = run(2, 100, 100)
        check("half black, half white: about 50 % non-black, mean near 127, varied", 49 <= nb <= 51 and 120 <= r_ <= 135 and r_ == g_ == b_ and v == 1, run(2, 100, 100))
        check("row padding (0xff bytes after each row) is ignored: a black surface stays 0 %", run(0, 100, 100, pad=64) == [10000, 0, 0, 0, 0, 0], run(0, 100, 100, pad=64))
        check("a 1x1 surface and a 3x2 one are sampled", run(1, 1, 1)[0] == 1 and run(1, 3, 2)[0] == 6, (run(1, 1, 1), run(1, 3, 2)))
        check("width or height 0, a NULL base, a surface smaller than its row: no samples", run(1, 0, 10)[0] == 0 and run(1, 10, 0)[0] == 0
              and run(1, 10, 10, null=True)[0] == 0 and run(9, 10, 10)[0] == 0, (run(1, 0, 10), run(1, 10, 0), run(1, 10, 10, null=True), run(9, 10, 10)))
        big = run(1, 2752, 2064)
        check("a 2752x2064 surface is sampled on at most 64x64 points and the colour is exact", big[0] <= 64 * 64 + 130 and big[0] > 1000 and big[1:] == [100, 200, 10, 30, 0], big)
        d = run(3, 640, 480)
        check("a diagonal gradient is varied", d[5] == 1 and 0 < d[1] <= 100, d)

    xcrun = shutil.which("xcrun")
    if not xcrun:
        print("SKIP: the iOS-SDK syntax check needs Xcode (the Mac)")
    else:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-x", "objective-c", "-fobjc-arc", "-arch", "arm64", "-isysroot", sdk,
                            "-miphoneos-version-min=17.0", "-Wall", str(root / "app/Madeira/Winios/WiniosGL.m")], capture_output=True, text=True)
        check("WiniosGL.m passes clang -fsyntax-only (Objective-C, ARC, iOS SDK) with no warning", r.returncode == 0 and "warning:" not in r.stderr and "error:" not in r.stderr, r.stderr[-800:])

print("PASS: GL probe" if ok else "FAIL: GL probe")
sys.exit(0 if ok else 1)
