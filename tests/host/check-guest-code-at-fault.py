#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[guest-code-at-fault]: the guest code around a call through NULL is printed (round 630, agent G1 S3).

Call of Duty: Black Ops III (NEXN61, build 626, and nine older logs) calls through NULL right after its start-up code walked the
system DLLs' export tables; the routine is in no log. segv_handler's cpu_area block (a PC below 4 GB, a plausible cpu_area in x17)
now also prints the 512 bytes before and the 64 after the guest RIP in cpu_area [0x00], read through the kernel.

Compiles ios_guest_code_line and ios_guest_code_at_fault, cut from build/ntdll-unix/signal_arm64_ios.c, with the host's
mach_vm_read_overwrite and checks: 36 lines for a 16-byte-aligned RIP, offsets -512..+48, the bytes as they are in memory, "??" for a
page that cannot be read (no fault), nothing for a RIP below 0x10000 or above 512 GB, two dumps per process at most; and that the
handler calls it right after the cpu_area lines. CI runs this check.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:1500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


start = src.index("static void ios_guest_code_line( char *buf, size_t len, unsigned long long a, long rel, const unsigned char *b, int ok )")
end = src.index("\n}\n", src.index("static void ios_guest_code_at_fault( unsigned long long rip )")) + 3
helpers = src[start:end]
block = src[src.index('ERR("  cpu_area@%p: [0x00]=%llx'):]
block = block[:block.index("\n                }\n")]
check("segv_handler prints the code right after the cpu_area lines, from cpu_area [0x00]",
      block.rstrip().endswith("ios_guest_code_at_fault( (unsigned long long)ca[0] );   /* round 630 (G1 S3): the guest code around it */"))
check("the helpers are iOS-only and sit before segv_handler",
      src.rindex("#ifdef WINE_IOS", 0, start) > src.rindex("#endif", 0, start)
      and src.index("static void segv_handler( int signal, siginfo_t *siginfo, void *sigcontext )") > end)
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-guest-code-at-fault.py" in wf)

harness = r'''
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#define ERR(...) printf(__VA_ARGS__)
''' + helpers + r'''
int main( void )
{
    unsigned char *p = mmap( (void *)(uintptr_t)0x300000000ull, 0x8000, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0 );
    if (p == MAP_FAILED || (uintptr_t)p >= 0x8000000000ull) { puts( "FAIL: no low pages" ); return 1; }
    for (int i = 0; i < 0x8000; i++) p[i] = (unsigned char)(i & 0xff);
    if (mprotect( p, 0x4000, PROT_NONE )) { puts( "FAIL: mprotect" ); return 1; }
    puts( "-- a" );
    ios_guest_code_at_fault( (unsigned long long)(uintptr_t)(p + 0x6000) );   /* readable around it */
    puts( "-- b" );
    ios_guest_code_at_fault( (unsigned long long)(uintptr_t)(p + 0x4100) );   /* 256 bytes of the 512 before lie in the PROT_NONE page */
    puts( "-- c" );
    ios_guest_code_at_fault( 0x8000 );                                        /* too low: nothing */
    ios_guest_code_at_fault( 0x9000000000ull );                               /* too high: nothing */
    ios_guest_code_at_fault( (unsigned long long)(uintptr_t)(p + 0x6000) );   /* a third dump: nothing */
    puts( "-- end" );
    return 0;
}
'''
cc = os.environ.get("CC", "cc")
if not shutil.which(cc):
    print("skip: no C compiler")
else:
    with tempfile.TemporaryDirectory(prefix="madeira-guest-code-") as tmp:
        c = Path(tmp) / "t.c"
        exe = Path(tmp) / "t"
        c.write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Werror", str(c), "-o", str(exe)], capture_output=True, text=True)
        check("the helpers build: " + b.stderr[-600:], b.returncode == 0)
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            out = r.stdout
            parts, cur = {}, None
            for l in out.splitlines():
                if l.startswith("-- "):
                    cur = l[3:]
                    parts[cur] = []
                elif cur:
                    parts[cur].append(l)
            a = parts["a"]
            check("36 lines for an aligned RIP, offsets -512 to +48", len(a) == 36 and "(-512):" in a[0] and "(+48):" in a[-1], a[:2] + a[-2:])
            check("the bytes are the memory's (offset 0 holds 00 01 02 ...)",
                  any("(+0): 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f" in l for l in a), [l for l in a if "(+0)" in l])
            bb = parts["b"]
            check("an unreadable page prints ?? and does not fault",
                  r.returncode == 0 and sum(1 for l in bb if l.endswith(" ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ?? ??")) == 16
                  and sum(1 for l in bb if "??" not in l) == 20, bb[:3])
            check("nothing for a RIP below 0x10000 or above 512 GB, and nothing after two dumps", parts["c"] == [], parts["c"])

print("PASS: the guest code around a call through NULL is printed" if ok else "FAILED: guest code at fault")
sys.exit(0 if ok else 1)
