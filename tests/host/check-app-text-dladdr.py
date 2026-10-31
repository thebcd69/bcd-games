#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The app's own __TEXT is the image that holds signal_arm64_ios.c's code, not dyld's image 0 (round 624, LiveContainer).

In LiveContainer dyld's image 0 is LiveContainer's executable, so ios_pc_in_app_text() said the app's own pc was not app code and
ios_native_unaligned_emulate refused the unaligned release store of _invoke_system_apc; FEX made it a fatal c0000005 (GTA V BTHD1F).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text(errors="replace")
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


fn = src[src.find("static int ios_pc_in_app_text( uint64_t pc )\n{"):]
fn = fn[:fn.find("\n}\n") + 3]
check("ios_pc_in_app_text asks dladdr for the image that holds it", "dladdr( (const void *)ios_pc_in_app_text, &self )" in fn, fn)
check("...and falls back to dyld's image 0 only when dladdr gives nothing",
      fn.find("dladdr(") < fn.find("if (!mh) mh = (const struct mach_header_64 *)_dyld_get_image_header( 0 );"), fn)
check("the range is still resolved once at start, not on the exception thread", "ios_pc_in_app_text( 0 );" in src)
check("a failed lookup is not retried (ios_app_text_tried)", "ios_app_text_tried = 1;" in fn and "!ios_app_text_tried" in fn, fn)
check("env.MADEIRA_APP_TEXT_DLADDR = 0 restores the old lookup", 'getenv( "MADEIRA_APP_TEXT_DLADDR" )' in fn and "!(dl && dl[0] == '0')" in fn, fn)
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
config = wine / "build-macos/include/config.h"
if shutil.which("xcrun") and config.exists():
    sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    b = root / "build/ntdll-unix"
    flags = ["-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC", "-fno-strict-aliasing",
             "-Wno-implicit-function-declaration", "-Wno-int-conversion", "-include", str(config), "-include", str(b / "shims/wine_ios_exit.h"),
             f"-I{b / 'shims'}", f"-I{wine / 'build-macos/dlls/ntdll'}", f"-I{wine / 'dlls/ntdll'}", f"-I{wine / 'dlls/ntdll/unix'}",
             f"-I{wine / 'build-macos/include'}", f"-I{wine / 'include'}", "-D__WINESRC__", "-DLTC_NO_PROTOTYPES", "-DLTC_SOURCE",
             "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"', '-DLIBDIR="/usr/local/lib"',
             '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB", "-DWINE_IOS=1",
             "-Dget_thread_context=ntdll_get_thread_context", "-Dset_thread_context=ntdll_set_thread_context"]
    r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *flags, str(b / "signal_arm64_ios.c")], capture_output=True, text=True)
    check("signal_arm64_ios.c compiles for iOS (syntax only, pinned wine headers)", r.returncode == 0, r.stderr[-1500:])
else:
    print("skip the iOS syntax check: no wine headers (set WINE_SRC)")
print("PASS: app text by dladdr" if ok else "FAILED: app text by dladdr")
sys.exit(0 if ok else 1)
