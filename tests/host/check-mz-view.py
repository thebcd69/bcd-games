#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An executable request outside the Wine view of its nearest MZ page is not that image's tail (round 624).

R.E.P.O. (Send Logs AJ7RNE, 1JXGHN, X8X6QM, YRCHHS on build 623, 1CQBCV on 622) and other Unity games on Mono: Mono maps its
assemblies as plain files, so the view ends where the file ends while the PE header's SizeOfImage reaches further; Mono's next
64 KB RWX code chunk started in that slack and mprotect_exec took its first 16 KB page for the image's tail (no store alias,
"[store-noalias] #1 ... NO pool/anon alias", then FEX's CodeInvalidationMutex leaked and the game never drew a frame).

Checks:
  - build/ntdll-unix/ios_mz_view.h compiled on the host against the geometries of the logs and of a genuine image;
  - virtual_ios.c: the check sits after the backward MZ scan and before the ml348 range check, only for a request inside the
    header's SizeOfImage, looks the view up with find_view( image_base, 0 ), logs "[mz-view] r624" at most 24 times, and drops
    the image only with MADEIRA_MZ_VIEW_CHECK=1 (default off);
  - the key is in the settings catalog and in the list policy's allowedEnvNames;
  - virtual_ios.c still compiles for iOS (syntax only, pinned Wine headers) when the Wine tree is there.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


header = root / "build/ntdll-unix/ios_mz_view.h"
src = root / "build/ntdll-unix/virtual_ios.c"
check("ios_mz_view.h exists", header.exists())
text = src.read_text(errors="replace")

# ---------------------------------------------------------------- the header, compiled on the host
harness = r'''
#include <stdio.h>
#include <stdint.h>
#include "ios_mz_view.h"
static int fails;
#define EXPECT(c, what) do { if (!(c)) { printf("FAIL %s\n", what); fails++; } } while (0)
int main(void)
{
    /* AJ7RNE (623): assembly view 0x7059480000+0x20000, SizeOfImage 0x24000, Mono chunk at 0x70594a0000 */
    EXPECT(!ios_mz_view_reaches(0x7059480000ULL, 0x20000, 0x70594a0000ULL), "AJ7RNE: the chunk behind the file view");
    /* 1CQBCV (622): view 0x71b0f70000+0x110000, chunk at 0x71b1080000 */
    EXPECT(!ios_mz_view_reaches(0x71b0f70000ULL, 0x110000, 0x71b1080000ULL), "1CQBCV: the chunk behind the file view");
    /* a genuine image: a SEC_IMAGE view spans SizeOfImage */
    EXPECT(ios_mz_view_reaches(0x71f28c0000ULL, 0x2433000, 0x71f28c0000ULL), "image: its base");
    EXPECT(ios_mz_view_reaches(0x71f28c0000ULL, 0x2433000, 0x71f28c0000ULL + 0x2400000), "image: its last section");
    EXPECT(ios_mz_view_reaches(0x71f28c0000ULL, 0x2433000, 0x71f28c0000ULL + 0x2433000 - 0x3000), "image: 12 KB before the end");
    EXPECT(!ios_mz_view_reaches(0x71f28c0000ULL, 0x2433000, 0x71f28c0000ULL + 0x2433000), "the view's end is outside");
    EXPECT(!ios_mz_view_reaches(0x71f28c0000ULL, 0x2433000, 0x71f28c0000ULL - 0x4000), "below the view is outside");
    EXPECT(!ios_mz_view_reaches(0xfffffffffff00000ULL, 0x200000, 0x10000ULL), "no wrap-around at the top of the address space");
    if (!fails) printf("header-ok\n");
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if cc and header.exists():
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(harness)
        exe = Path(tmp) / "t"
        b = subprocess.run([cc, "-Wall", "-Werror", "-I", str(header.parent), "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the header compiles on the host (-Wall -Werror)", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check("view geometry: AJ7RNE and 1CQBCV chunks are outside, a genuine image's pages are inside", r.returncode == 0 and "header-ok" in r.stdout, r.stdout)
else:
    print("skip the host compile: no C compiler")

# ---------------------------------------------------------------- where the check sits in mprotect_exec
scan = text.find("/* Scan backward to find MZ header (PE image start).")
mzv = text.find("/* round 624 (ios_mz_view.h): the nearest MZ owns `base` only if the Wine view")
ml348 = text.find("/* ml348: the backward scan finds the NEAREST MZ below")
check("the check sits after the backward MZ scan and before the ml348 range check", 0 < scan < mzv < ml348, (scan, mzv, ml348))
block = text[mzv:ml348]
check("only for a request inside the header's SizeOfImage",
      "(uintptr_t)base >= (uintptr_t)image_base" in block and "(uintptr_t)base < (uintptr_t)image_base + image_size" in block)
check("the view of the MZ page is looked up with find_view( image_base, 0 )", "find_view( image_base, 0 )" in block)
check("decided by ios_mz_view_reaches on that view", "ios_mz_view_reaches( (uintptr_t)mzv->base, mzv->size, (uintptr_t)base )" in block)
check("the [mz-view] r624 line is printed at most 24 times", "[mz-view] r624" in block and "mzv_n++ < 24" in block)
check("the image is dropped only when the key is on",
      re.search(r"if \(on\)\s*\{\s*image_base = NULL;\s*image_size = 0;\s*\}", block) is not None)
fn = text[text.find("static int ios_mz_view_check_enabled(void)"):]
fn = fn[:fn.find("\n}\n") + 3]
check("MADEIRA_MZ_VIEW_CHECK is read once and is off unless it is 1",
      'getenv( "MADEIRA_MZ_VIEW_CHECK" )' in fn and "cached = (s && *s == '1') ? 1 : 0;" in fn, fn)
check("ios_mz_view.h is included by virtual_ios.c", '#include "ios_mz_view.h"' in text)
vi = text[text.find("void virtual_init(void)"):]
vi = vi[:vi.find("ios_log_running_image_identity();")]
check("virtual_init reads the key once, so the fault path never calls getenv for it", "(void)ios_mz_view_check_enabled();" in vi)

# ---------------------------------------------------------------- the key is known to the settings and to downloaded lists
cat = (root / "build/tools/gen-config-catalog.py").read_text()
check("settings catalog entry env.MADEIRA_MZ_VIEW_CHECK", '"env.MADEIRA_MZ_VIEW_CHECK"' in cat)
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
check("a downloaded list may set MADEIRA_MZ_VIEW_CHECK (allowedEnvNames)", '"MADEIRA_MZ_VIEW_CHECK"' in pol)

# ---------------------------------------------------------------- the real file still compiles for iOS (when the wine headers exist)
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
config = wine / "build-macos/include/config.h"
if shutil.which("xcrun") and config.exists():
    try:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ""
    if sdk:
        build = root / "build/ntdll-unix"
        flags = ["-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                 "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
                 "-include", str(config), "-include", str(build / "shims/wine_ios_exit.h"), f"-I{build / 'shims'}",
                 f"-I{wine / 'build-macos/dlls/ntdll'}", f"-I{wine / 'dlls/ntdll'}", f"-I{wine / 'dlls/ntdll/unix'}",
                 f"-I{wine / 'build-macos/include'}", f"-I{wine / 'include'}", "-D__WINESRC__", "-DLTC_NO_PROTOTYPES",
                 "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB",
                 "-DWINE_IOS=1"]
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *flags, str(src)], capture_output=True, text=True)
        check("virtual_ios.c compiles for iOS (syntax only, pinned wine headers)", r.returncode == 0, r.stderr[-2000:])
        check("...and the new code adds no warning", "mz_view" not in r.stderr and "mzv" not in r.stderr, r.stderr[-2000:])
    else:
        print("skip the iOS syntax check: no iphoneos SDK")
else:
    print("skip the iOS syntax check: no wine headers (set WINE_SRC)")

print("PASS: mz view check" if ok else "FAILED: mz view check")
sys.exit(0 if ok else 1)
