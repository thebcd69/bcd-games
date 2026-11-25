#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[stack-narrow] (round 714, agent M4 D7; Gal*Gun: Double Peace RZM5YP): the failure-path line that names a program's access to
the low 32 bits of its own stack. Log only.

  1. the production ios_stack_narrow_real is compiled on the host: the log's numbers (stack [0x703b7b0000, 0x703bfb0000), fault
     0x3bfaf290 -> 0x703bfaf290), the first and last byte, a span running off the end, one below the range, an address above
     4 GB, a stack already below 4 GB, a reservation crossing a 4 GB line and an empty one all behave;
  2. the source: the diagnostic reads only through ios_av_args_read, writes nothing, prints at most 4 lines, takes 64-bit
     programs only, and is called once, from the UNHANDLED report after [wow-null32].
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
hdr = (R / "build/ntdll-unix/ios_stack_narrow_diag.h").read_text()
sig = (R / "build/ntdll-unix/signal_arm64_ios.c").read_text()
CC = os.environ.get("CC") or shutil.which("cc") or "clang"
failures = 0


def require(cond, label):
    global failures
    print(("PASS: " if cond else "FAIL: ") + label)
    if not cond:
        failures += 1


fn = re.search(r"static uint64_t ios_stack_narrow_real\(.*?\n}\n", hdr, re.S)
diag = hdr[hdr.index("static void ios_stack_narrow_diag("):hdr.index("#endif /* IOS_STACK_NARROW_DIAG_H */")]
require(fn is not None, "the pure arithmetic is one function")
require("ios_av_args_read( thread_teb + IOS_WN32_TEB_WOWOFF, &woff, 4 ) || woff != 0) return;" in diag,
        "64-bit programs only (a 32-bit program's 64-bit stack belongs to wow64)")
require(diag.count("ios_av_args_read(") == 3 and "mach_vm_write" not in diag and "vm_protect" not in diag and "=" not in
        diag.split("dprintf")[1].split(";")[0].replace("==", ""), "it reads through the fault-safe reader only and writes nothing")
require(">= 4) return;" in diag and "> 4) return;" in diag, "at most 4 lines per process")
require("fault_addr >= 0x100000000ull" in diag and "exception != EXC_BAD_ACCESS" in diag, "bad accesses below 4 GB only")
require(sig.count("ios_stack_narrow_diag(") == 1 and sig.count('#include "ios_stack_narrow_diag.h"') == 1
        and sig.index("ios_wow_null_diag( &state, thread_teb, req->exception, fault_addr );") < sig.index("ios_stack_narrow_diag("),
        "called once, in the UNHANDLED report after [wow-null32]")

main = r'''
#include <stdint.h>
#include <stdio.h>
FN
static int failed;
static void check(int ok, const char *label) { printf("%s%s\n", ok ? "PASS: " : "FAIL: ", label); if (!ok) failed++; }
int main(void) {
    const uint64_t d = 0x703b7b0000ull, b = 0x703bfb0000ull;
    check(ios_stack_narrow_real(0x3bfaf290ull, 1, d, b) == 0x703bfaf290ull, "RZM5YP: 0x3bfaf290 -> 0x703bfaf290");
    check(ios_stack_narrow_real(0x3bfaf290ull, 8, d, b) == 0x703bfaf290ull, "  an 8-byte store there too");
    check(ios_stack_narrow_real(0x3b7b0000ull, 1, d, b) == d, "the first byte of the reservation");
    check(ios_stack_narrow_real(0x3bfaffffull, 1, d, b) == b - 1, "the last byte");
    check(ios_stack_narrow_real(0x3bfafff8ull, 16, d, b) == 0, "a span that runs off the end: 0");
    check(ios_stack_narrow_real(0x3b7affffull, 1, d, b) == 0, "one byte below the range: 0");
    check(ios_stack_narrow_real(0x7b3bfaf290ull, 1, d, b) == 0, "an address above 4 GB: 0");
    check(ios_stack_narrow_real(0x3bfaf290ull, 0, d, b) == 0, "an empty access: 0");
    check(ios_stack_narrow_real(0x10000ull, 1, 0x8000ull, 0x810000ull) == 0, "a stack already below 4 GB: 0");
    check(ios_stack_narrow_real(0xfff80000ull, 1, 0x70fff00000ull, 0x7100100000ull) == 0, "a reservation crossing a 4 GB line: 0");
    check(ios_stack_narrow_real(0x3bfaf290ull, 1, b, b) == 0, "an empty reservation: 0");
    return failed ? 1 : 0;
}
'''
if fn:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "t.c"
        src.write_text(main.replace("FN", fn.group(0)))
        out = Path(tmp) / "t"
        build = subprocess.run([CC, "-O1", "-Wall", "-Werror", "-Wno-unused-function", str(src), "-o", str(out)], capture_output=True, text=True)
        if build.returncode != 0:
            print(build.stderr[-2000:])
            require(False, "the production ios_stack_narrow_real compiles on the host")
        else:
            run = subprocess.run([str(out)], capture_output=True, text=True)
            print(run.stdout, end="")
            if run.returncode != 0:
                failures += 1
print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILURE(S)" % failures))
sys.exit(0 if failures == 0 else 1)
