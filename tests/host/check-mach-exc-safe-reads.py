#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 626 (agent P S8; E9C6ME and 14 archive logs): the Mach exception thread's [nls-probe] and caller_insn diagnostics read through
ios_exc_read64 (mach_vm_read_overwrite), never by a plain load: a wild register (x16 = 0xffffffffffff) faulted inside the thread that
serves every Mach exception and the app hung. Source pins, and the helper compiled and run on this Mac against mapped and unmapped
addresses (it must answer, never fault)."""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


src = (R / "build/ntdll-unix/signal_arm64_ios.c").read_text()
m = re.search(r"static int ios_exc_read64\( uint64_t addr, uint64_t \*out \)\n\{\n(.*?)\n\}\n", src, re.S)
check("ios_exc_read64 exists (mach_vm_read_overwrite, 8 bytes)", m and "mach_vm_read_overwrite" in m.group(1) and "got == 8" in m.group(1))
check("it stands before ios_mach_exception_thread", m and src.find("static int ios_exc_read64(") < src.find("static void *ios_mach_exception_thread( void *arg )"))
a = src.find('"[mach_exc] pc pool-copy')
b = src.find("[nls-probe] upcase ptr")
block = src[src.rfind("/* Read instruction at LR-4", 0, a):b + 300]
check("caller_insn reads lr-4 through the helper", "ios_exc_read64( (uint64_t)state.__lr - 4, &insn )" in block and "*lr_p" not in block)
check("[nls-probe] reads x16+0x4e0 and pe+0xc04e0 through the helper",
      "ios_exc_read64( x16v + 0x4e0, &pool_v )" in block and "ios_exc_read64( (uint64_t)(uintptr_t)copy_pe + 0xc04e0, &pe_v )" in block
      and "*(volatile uint64_t *)" not in block)
# Round 626 (agent P S8, the rest): the [findblock-probe], [bucketscan-probe] and [nullfp-probe] reads go through the helper too (their
# range tests do not prove an address is mapped); nothing between the first probe and the fp-chain backtrace loads a register-derived
# address directly.
pa = src.find("/* [bucketscan-probe] Thumper dies in FEXCore GuestToHostMap::BlockList")
pb = src.find("/* fp-chain backtrace of the faulting thread", pa)
probes = src[pa:pb]
check("the findblock, bucketscan and nullfp probes read only through the helper",
      pa > 0 and pb > pa and "*(volatile uint64_t *)" not in probes and "*(volatile uint8_t  *)" not in probes
      and "cp[-" not in probes and probes.count("ios_exc_read64(") == 13)   # findblock 2, bucketscan 1 + 6, nullfp 4

cc = shutil.which("clang") or shutil.which("cc")
if not cc or sys.platform != "darwin":
    print("note: no macOS host compiler; the runtime part is skipped")
else:
    prog = "#include <mach/mach.h>\n#include <mach/mach_vm.h>\n#include <stdint.h>\n#include <stdio.h>\n" + m.group(0) + r'''
static uint64_t mapped = 0x1122334455667788ULL;
int main(void)
{
    uint64_t v = 0;
    int a = ios_exc_read64( (uint64_t)(uintptr_t)&mapped, &v );
    int b = ios_exc_read64( 0x1000000004dfULL, &v );          /* E9C6ME's address: not mapped */
    int c = ios_exc_read64( 0xffffffffffffULL + 0x4e0, &v );   /* the wild x16 itself */
    printf("%d %d %d %d\n", a, a ? (v == 0x1122334455667788ULL) : 0, b, c);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"; c.write_text(prog); exe = Path(d) / "t"
        r = subprocess.run([cc, "-O1", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the helper compiles on the host", r.returncode == 0)
        if r.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True)
            check("a mapped address reads its value; the two unmapped ones answer 0, with no signal",
                  out.returncode == 0 and out.stdout.split() == ["1", "1", "0", "0"])
print("PASS: Mach exception thread reads are checked" if ok else "FAILED")
sys.exit(0 if ok else 1)
