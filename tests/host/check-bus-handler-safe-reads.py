#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Night round 2026-11-11 (agent G6 S8; Maneater JFS9DW = DQ5RJD and VV9BZF, builds 611 and 634; also 0224CG and KDAAW0): BCD's own SIGBUS
handler read memory through LR - 4 by a plain load. After three SIGBUS faults that FEX handled, the fourth had LR = 0xc062000000000001 (the
bits of a double, not a return address); the load at 0xc061fffffffffffd faulted INSIDE bus_handler, segv_handler found no way out
("segv_handler SEGV #1: pc=... bus_handler+0x180 ... (virtual_handle_fault failed)") and the game's thread ended with no message.

The diagnostics of the fault handlers that read through a register of the interrupted thread (LR - 4, PC, x11, x17) now read through
ios_read_u32_or_zero / ios_read_u64_or_zero (mach_vm_read_overwrite): an address that is not canonical, not mapped or too low answers 0 and
the handler goes on to the normal path (execute fault, store emulation, setup_exception). Held here:
  1. Source shape: bus_handler's BUS #n line, its "BUS EXEC in JIT .data" insn@LR-4 and the tiny-PC block's LR, x11 and x17 reads use the helper;
     none of them loads through the register directly any more.
  2. The helpers, extracted from signal_arm64_ios.c and compiled on this Mac against mapped and unmapped addresses (the wild LR itself,
     a non-canonical address, the zero page, a PROT_NONE page, a page that ends in a hole): they answer, they never fault. The ORIGINAL
     plain load of the same address is run in a child process and does die with a signal (SIGSEGV or SIGBUS): the test fails without the fix.
  3. Auto-tune: rule bus-handler-wild-lr (fixedIn 652, noFix text) fires on the fixtures; the CI step runs this check.
Needs python3 and (for the runtime part) a macOS C compiler."""
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

# 1. source shape -------------------------------------------------------------------------------------------------------------------------
m_rd = re.search(r"static int ios_wow_rd\( uint64_t a, void \*out, size_t n \)\n\{\n(.*?)\n\}\n", src, re.S)
m32 = re.search(r"static uint32_t ios_read_u32_or_zero\( uintptr_t a \)\n\{\n(.*?)\n\}\n", src, re.S)
m64 = re.search(r"static uint64_t ios_read_u64_or_zero\( uintptr_t a \)\n\{\n(.*?)\n\}\n", src, re.S)
check("ios_wow_rd reads through mach_vm_read_overwrite", m_rd and "mach_vm_read_overwrite" in m_rd.group(1))
check("ios_read_u32_or_zero exists: canonical test, then the checked read",
      m32 and "0xffff800000000000ull" in m32.group(1) and "ios_wow_rd(" in m32.group(1))
check("ios_read_u64_or_zero exists the same way",
      m64 and "0xffff800000000000ull" in m64.group(1) and "ios_wow_rd(" in m64.group(1))
check("the helpers stand before bus_handler and before the tiny-PC block of segv_handler",
      m32 and src.find("static uint32_t ios_read_u32_or_zero(") < src.find("static void bus_handler( int signal, siginfo_t *siginfo, void *sigcontext )")
      and src.find("static uint32_t ios_read_u32_or_zero(") < src.find("branch@(lr-4)="))

b0 = src.find("static void bus_handler( int signal, siginfo_t *siginfo, void *sigcontext )")
b1 = src.find("bus_fatal:", b0)
bus = src[b0:b1]
check("bus_handler: BUS #n reads PC and LR - 4 through the helper",
      "bus_pc_ok ? ios_read_u32_or_zero( bus_pc_val ) : 0" in bus and "bus_lr_ok ? ios_read_u32_or_zero( bus_lr_val - 4 ) : 0" in bus)
check("bus_handler: no plain load through bus_pc_val / bus_lr_val / lr_val", not re.search(r"\*\s*\(\s*uint32_t\s*\*\s*\)\s*\(?\s*(bus_pc_val|bus_lr_val|lr_val)", bus))
check("bus_handler: insn@LR-4 of the BUS EXEC report reads through the helper", "insn_lr_m4 = ios_read_u32_or_zero( lr_val - 4 )" in bus)

t0 = src.find("branch@(lr-4)=")
t1 = src.find("rec.NumberParameters = 2;", t0)
tiny = src[t0 - 800:t1]
check("tiny-PC block: LR-4, LR-8, LR-12, LR-16 through the helper",
      all(("ios_read_u32_or_zero( lr_val - %d )" % n) in tiny for n in (4, 8, 12, 16)))
check("tiny-PC block: no plain load through lr_val, slot_addr, parent_slot, x11 or x17 any more",
      not re.search(r"\*\s*\(\s*uint32_t\s*\*\s*\)\s*\(\s*lr_val", tiny) and "*(void**)slot_addr" not in tiny
      and "*(void**)parent_slot" not in tiny and "(uint32_t *)x11_at_seg" not in tiny and "(uint64_t *)cpu_area_p" not in tiny
      and "uint32_t *t = (uint32_t *)b_target" not in tiny.split("cc[0] is B")[0])
check("tiny-PC block: the x11 words, the trampoline words and the cpu_area words come from the helpers",
      "ios_read_u32_or_zero( x11_at_seg + 4 * pk )" in tiny and "ios_read_u32_or_zero( (uintptr_t)b_target + 4 * tk )" in tiny
      and "ios_read_u64_or_zero( cpu_area_p + 8 * ck )" in tiny)

# 2. the helpers, compiled and run --------------------------------------------------------------------------------------------------------
cc = shutil.which("clang") or shutil.which("cc")
if not cc or sys.platform != "darwin" or not (m_rd and m32 and m64):
    print("note: no macOS host compiler; the runtime part is skipped")
else:
    prog = ("#include <mach/mach.h>\n#include <mach/mach_vm.h>\n#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n"
            "#include <signal.h>\n#include <unistd.h>\n#include <sys/mman.h>\n#include <string.h>\n"
            + m_rd.group(0) + m32.group(0) + m64.group(0) + r'''
int main( int argc, char **argv )
{
    long pg = sysconf( _SC_PAGESIZE );
    char *a = mmap( NULL, 3 * pg, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0 );
    uint32_t *w = (uint32_t *)a;
    w[0] = 0xd63f0080; w[1] = 0x11223344;
    *(uint64_t *)(a + 16) = 0x1122334455667788ULL;
    munmap( a + 2 * pg, pg );                                  /* a hole after two mapped pages */
    mprotect( a + pg, pg, PROT_NONE );                         /* a PROT_NONE page */
    if (argc > 1)                                              /* the ORIGINAL code: a plain load through the wild LR - 4 */
    {
        volatile uint32_t v = *(volatile uint32_t *)(0xc062000000000001ULL - 4);
        printf( "survived %x\n", v );
        return 0;
    }
    uintptr_t lr = 0xc062000000000001ULL;                       /* Maneater's LR */
    printf( "%08x %08x %08x %08x\n", ios_read_u32_or_zero( (uintptr_t)a ), ios_read_u32_or_zero( lr - 4 ),
            ios_read_u32_or_zero( 0x100000003ULL ), ios_read_u32_or_zero( (uintptr_t)a + pg ) );          /* mapped, wild, odd, PROT_NONE */
    printf( "%016llx %016llx %016llx %08x %08x\n", (unsigned long long)ios_read_u64_or_zero( (uintptr_t)a + 16 ),
            (unsigned long long)ios_read_u64_or_zero( lr ), (unsigned long long)ios_read_u64_or_zero( (uintptr_t)a + pg - 4 ),
            ios_read_u32_or_zero( 0 ), ios_read_u32_or_zero( 0x8000 ) );      /* mapped, wild, straddles into PROT_NONE, zero page, low */
    return 0;
}
''')
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        c.write_text(prog)
        exe = Path(d) / "t"
        r = subprocess.run([cc, "-O1", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the extracted helpers compile on the host", r.returncode == 0)
        if r.returncode != 0:
            print(r.stderr[:2000])
        else:
            out = subprocess.run([str(exe)], capture_output=True, text=True)
            lines = out.stdout.split("\n")
            check("a mapped address reads its word; the wild LR - 4, an odd low address and a PROT_NONE page answer 0, with no signal",
                  out.returncode == 0 and lines[0].split() == ["d63f0080", "00000000", "00000000", "00000000"])
            check("64-bit: a mapped qword reads; the wild LR, a read that runs into PROT_NONE, the zero page and a low address answer 0",
                  out.returncode == 0 and lines[1].split() == ["1122334455667788", "0000000000000000", "0000000000000000", "00000000", "00000000"])
            orig = subprocess.run([str(exe), "orig"], capture_output=True, text=True)
            check("the ORIGINAL plain load through the same wild LR - 4 dies with a signal (so the test fails without the fix)",
                  orig.returncode < 0 and "survived" not in orig.stdout)

# 3. auto-tune + CI -----------------------------------------------------------------------------------------------------------------------
rules = (R / "build/tools/autotune_rules.py").read_text()
check("auto-tune rule bus-handler-wild-lr exists with fixedIn 652", '"id": "bus-handler-wild-lr"' in rules
      and re.search(r'"id": "bus-handler-wild-lr".{0,400}"fixedIn": 652', rules, re.S) is not None)
check("the maneater fixtures exist", all((R / "tests/fixtures/autotune" / ("%s.expect" % n)).exists() for n in
                                          ("maneater-bus-handler-wild-lr-2026-11-10", "maneater-bus-handler-wild-lr-2-2026-11-10")))
check("CI runs this check", "tests/host/check-bus-handler-safe-reads.py" in (R / ".github/workflows/build-ipa.yml").read_text())
print("PASS: the fault handlers' register-derived reads are checked" if ok else "FAILED")
sys.exit(0 if ok else 1)
