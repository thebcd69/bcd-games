#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A fixed section view beyond the address limit is refused (round 626, agent G2 S10; Splatterhouse YWX9P5, build 623).

A ReXGlue program (an Xbox 360 recompilation runtime with Xenia's memory manager) maps a 1 GB view of one section at 1<<n for
n = 32, 33, ... until a base works. Wine's map_view answered a fixed base beyond the address limit with
STATUS_WORKING_SET_LIMIT_RANGE (0x40000002), a success-class status with no view behind it; kernelbase's MapViewOfFileEx took the
requested address as a mapped view, the program took 1<<47 and its first write faulted at 0x80008000001c. build/ntdll-unix/virtual_ios.c
now refuses such a base with STATUS_CONFLICTING_ADDRESSES (ERROR_INVALID_ADDRESS, what Windows gives for an address it cannot use),
like the three limit checks after it; the first 16 refusals of a fixed view of 256 MB or more name the kernel's regions in the range
("[map-block]", log only). The owner's decision of 2026-10-31: on by default (no other log of the archive reaches the line).

Checks: the fixed-base branch of map_view under WINE_IOS (the #else keeps Wine's line), the diagnostic (capped, size >= 256 MB, the
status returned unchanged), the two other uses of the status (size checks) untouched, auto-tune's message rule and its fixture, CI;
and, with the pinned wine headers (the full tree), virtual_ios.c compiles for iOS without a new warning.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys

root = Path(__file__).resolve().parents[2]
src = root / "build/ntdll-unix/virtual_ios.c"
text = src.read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


i = text.index("static NTSTATUS map_view( struct file_view **view_ret, void *base, size_t size,")
fn = text[i:text.index("\n}\n", i)]
br = fn[fn.index("    if (base)\n    {\n"):fn.index("    else\n    {\n        void *start = address_space_start;")]
ios, rest = br.split("#else\n", 1) if "#else\n" in br else (br, "")
check("map_view's fixed-base branch refuses a base beyond the address limit under WINE_IOS (no success-class answer)",
      "#ifdef WINE_IOS" in ios and "if (is_beyond_limit( base, size, address_space_limit )) return STATUS_CONFLICTING_ADDRESSES;" in ios
      and "STATUS_WORKING_SET_LIMIT_RANGE;" not in ios.split("/*")[0] + ios.split("*/")[-1])
check("...the #else keeps Wine's own line", rest.lstrip().startswith(
      "if (is_beyond_limit( base, size, address_space_limit )) return STATUS_WORKING_SET_LIMIT_RANGE;\n#endif"))
check("the checks after it are unchanged",
      "if (limit_low && base < (void *)limit_low) return STATUS_CONFLICTING_ADDRESSES;" in br
      and "if (is_beyond_limit( base, size, host_addr_space_limit )) return STATUS_CONFLICTING_ADDRESSES;" in br)
blk = br[br.index("if ((status = map_fixed_area( base, size, unix_prot )))"):]
blk = blk[:blk.index("            return status;\n        }") + len("            return status;\n        }")]
check("[map-block]: only views of 256 MB or more, the first 16, log only (the status is returned unchanged)",
      "if (size >= 0x10000000 && InterlockedIncrement( &block_told ) <= 16)" in blk and "[map-block] #%d fixed view %p +0x%lx refused" in blk
      and "ios_va_regions( base, size, regions, sizeof(regions) );" in blk and blk.rstrip().endswith("return status;\n        }")
      and blk.count("return") == 1)
check("the status's two other uses (size checks) are untouched",
      "if (is_beyond_limit( 0, size, working_set_limit )) return STATUS_WORKING_SET_LIMIT_RANGE;" in text
      and "if (size != *size_ptr) return STATUS_WORKING_SET_LIMIT_RANGE;" in text
      and text.count("return STATUS_WORKING_SET_LIMIT_RANGE;") == 3)
rules = (root / "build/tools/autotune_rules.py").read_text()
r = rules[rules.index('"id": "fixed-window-2n-unavailable"'):] if '"id": "fixed-window-2n-unavailable"' in rules else ""
r = r[:r.index("\n    },")] if r else ""
check("auto-tune says why the program cannot start (no line, known)", '"noFix": True' in r and '"lines": []' in r
      and 'size=0x40000000 -> 0xc0000018' in r, r[:400])
exp = root / "tests/fixtures/autotune/splatterhouse-rexglue-fixed-window-2026-10-31.expect"
check("the fixture is there and expects the message", exp.is_file() and "fixed-window-2n-unavailable" in json.loads(exp.read_text()).get("matched", [])
      and json.loads(exp.read_text()).get("next") == [])
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-map-beyond-limit.py" in wf)

# --- the real file still compiles for iOS (when the wine headers exist) --------------------------------
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
        res = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *flags, str(src)], capture_output=True, text=True)
        check("virtual_ios.c compiles for iOS (syntax only, pinned wine headers)", res.returncode == 0, res.stderr[-2000:])
        check("...and the new code adds no warning", "block_told" not in res.stderr and "map-block" not in res.stderr, res.stderr[-2000:])
    else:
        print("skip the iOS syntax check: no iphoneos SDK")
else:
    print("skip the iOS syntax check: no wine headers (set WINE_SRC)")

print("PASS: fixed view beyond the limit" if ok else "FAILED: fixed view beyond the limit")
sys.exit(0 if ok else 1)
