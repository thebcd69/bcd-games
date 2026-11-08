#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The SRW-lock hang census for 32-bit processes (ml2250, upstream Madeira #517 eb0da2e), diagnostic only.

A 32-bit thread that waits without a timeout on an address 2 mod 4 whose owners half is non-zero is waiting on a Wine SRW lock that
somebody holds (Steam's 32-bit web helper in upstream's logs 116-119; in BCD 8 non-Rockstar 32-bit logs show such a wait and the
`[waiters]` line cannot say who holds the lock). Every census cycle (ios_pump_sample), after env.MADEIRA_SRW_HANG_DIAG_S (30; 0 = off)
seconds of such a wait, every 32-bit thread of that process is logged once (at most 8 processes): the x86 registers from the WoW64 CPU
area, the EBP chain, the module-resolved words above ESP, the module list from PEB32->Ldr. Read-only (Mach reads). The process is never
ended: upstream's MADEIRA_SRW_HANG_KILL_S and the wineserver hook behind it are not part of this port.

The production census functions are cut from build/ntdll-unix/signal_arm64_ios.c and run against a fake 32-bit process in real memory
(a TEB with a WoW64 CPU area, a PEB32 with a loader list of two modules, a stack): the waiting thread and a second thread of the same
process, a thread of another process, and a 64-bit thread. Checks:
  - the wait is reported after the threshold only, once per process, with the lock address and the owners count;
  - both 32-bit threads of the process are dumped with their EIP resolved to module+RVA, the EBP chain and the stack words named;
    the other process's thread and the 64-bit thread are not;
  - MADEIRA_SRW_HANG_DIAG_S=0 logs nothing; a wait with a timeout, a lock word with no owners, an address that is not 2 mod 4 and a
    wait younger than the threshold log nothing;
  - no ending of a process exists in the source (no ios_hang_kill_pid, no MADEIRA_SRW_HANG_KILL_S, nothing in the wineserver).
Needs python3 and a C compiler.
"""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
sig = (root / "build/ntdll-unix/signal_arm64_ios.c").read_text()
srv = (root / "build/wineserver/fd_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[:2500]) if (detail and not cond) else ""))
    ok = ok and bool(cond)


start = sig.index("/* ml2250: SRW-LOCK HANGS IN 32-BIT PROCESSES")
end = sig.index("#define IOS_PUMP_MAX 4", start)
block = sig[start:end]
pump = sig[sig.index("void ios_pump_sample(void)\n{"):]
pump = pump[:pump.index("\n}\n")]

check("the census runs every pump sample, after the waiter dump", "ios_srw_hang_census( count );" in pump
      and pump.index("ios_alert_waiter_dump();") < pump.index("ios_srw_hang_census( count );"))
census = block[block.index("static void ios_srw_hang_census( int count )"):]
check("nothing in the source can end a process for it (no kill switch read, no pid hand-off, nothing in the wineserver)",
      "KILL" not in census and "getenv( \"MADEIRA_SRW_HANG_KILL_S\"" not in sig and "ios_hang_kill_pid" not in sig
      and "ios_hang_kill_pid" not in srv and "SRW_HANG_KILL" not in srv)
check("the diagnostic defaults to 30 seconds", 'ios_srw_hang_env( "MADEIRA_SRW_HANG_DIAG_S", 30 )' in block)

cc = shutil.which("cc") or shutil.which("clang")
if not cc:
    print("note: no host C compiler; the census was not run")
else:
    code = r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
#define KERN_SUCCESS 0
#define mach_task_self() 0

#define WB   0x7000000000ull                      /* the 32-bit process's window */
#define WOB  0x7200000000ull                      /* another 32-bit process's window */
#define LOW_SIZE  0x400000ull                    /* guest memory at the window's base */
#define TEB_SIZE  0x4000ull
#define TEB_A   (WB + 0xfffa0000ull)              /* waiting thread */
#define TEB_B   (WB + 0xfff80000ull)              /* a second thread of the same process */
#define TEB_O   (WOB + 0xfffa0000ull)             /* a thread of another 32-bit process */
#define TEB_X   (0x7400000000ull)                /* a 64-bit thread */
#define CPU_A   (WB + 0x300000ull)
#define CPU_B   (WB + 0x301000ull)
#define CPU_X   (WB + 0x302000ull)
#define CPU_O   (WOB + 0x300000ull)
#define LOCK    (WB + 0x100002ull)                /* the SRW lock: 2 mod 4 */

static int mach_vm_read_overwrite( int task, mach_vm_address_t a, mach_vm_size_t n, mach_vm_address_t to, mach_vm_size_t *got )
{
    static const struct { uint64_t lo, hi; } ok[] = {
        { WB, WB + LOW_SIZE }, { TEB_A, TEB_A + TEB_SIZE }, { TEB_B, TEB_B + TEB_SIZE }, { WOB, WOB + LOW_SIZE },
        { TEB_O, TEB_O + TEB_SIZE }, { TEB_X, TEB_X + TEB_SIZE } };
    unsigned i;
    (void)task;
    for (i = 0; i < sizeof(ok) / sizeof(ok[0]); i++)
        if (a >= ok[i].lo && a + n <= ok[i].hi) { memcpy( (void *)(uintptr_t)to, (void *)(uintptr_t)a, n ); *got = n; return KERN_SUCCESS; }
    return 1;
}

struct ios_thread_entry_fake { uint64_t teb; };
static struct ios_thread_entry_fake ios_thread_registry[8];

static int wait_age = 31, wait_inf = 1;
static uint64_t wait_addr = LOCK;
int ios_alert_waiter_lookup( unsigned int tid, const void **addr, int *age_s, int *inf )
{
    if (tid != 0x34) return 0;
    *addr = (const void *)(uintptr_t)wait_addr; *age_s = wait_age; *inf = wait_inf;
    return 1;
}

''' + block + r'''

static void put32( uint64_t a, uint32_t v ) { memcpy( (void *)(uintptr_t)a, &v, 4 ); }
static void put16( uint64_t a, uint16_t v ) { memcpy( (void *)(uintptr_t)a, &v, 2 ); }
static void put64( uint64_t a, uint64_t v ) { memcpy( (void *)(uintptr_t)a, &v, 8 ); }

static void thread( uint64_t teb, uint64_t cpu, uint32_t pid, uint32_t tid, uint16_t machine, uint32_t eip, uint32_t esp, uint32_t ebp )
{
    put64( teb + 0x40, pid ); put64( teb + 0x48, tid ); put64( teb + 0x1488, cpu );
    put16( cpu + 2, machine );
    put32( cpu + 4 + 0xb4, ebp ); put32( cpu + 4 + 0xb8, eip ); put32( cpu + 4 + 0xc4, esp );
}

static void ldr_entry( uint64_t base_addr, uint32_t self, uint32_t next, uint32_t dll, uint32_t size, const char *name, uint32_t name_at )
{
    uint16_t w[16];
    size_t i, n = strlen( name );
    put32( base_addr + self, next );
    put32( base_addr + self + 0x18, dll ); put32( base_addr + self + 0x20, size );
    put32( base_addr + self + 0x2c, (uint32_t)(n * 2) );
    put32( base_addr + self + 0x30, name_at );
    for (i = 0; i < n; i++) w[i] = (unsigned char)name[i];
    memcpy( (void *)(uintptr_t)(base_addr + name_at), w, n * 2 );
}

static void build( void )
{
    void *p;
    p = mmap( (void *)WB, LOW_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 ); if (p != (void *)WB) { puts( "mmap" ); exit( 2 ); }
    p = mmap( (void *)WOB, LOW_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 ); if (p != (void *)WOB) { puts( "mmap" ); exit( 2 ); }
    p = mmap( (void *)TEB_A, TEB_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 ); if (p != (void *)TEB_A) { puts( "mmap" ); exit( 2 ); }
    p = mmap( (void *)TEB_B, TEB_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 ); if (p != (void *)TEB_B) { puts( "mmap" ); exit( 2 ); }
    p = mmap( (void *)TEB_O, TEB_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 ); if (p != (void *)TEB_O) { puts( "mmap" ); exit( 2 ); }
    p = mmap( (void *)TEB_X, TEB_SIZE, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 ); if (p != (void *)TEB_X) { puts( "mmap" ); exit( 2 ); }

    /* PEB32 at guest 0x200000, Ldr at 0x200100, the load-order list head at Ldr+0x0c; two modules */
    put32( TEB_A + 0x2000 + 0x30, 0x200000 );
    put32( WB + 0x200000 + 0x0c, 0x200100 );
    put32( WB + 0x200100 + 0x0c, 0x200200 );                       /* head.Flink -> first entry */
    ldr_entry( WB, 0x200200, 0x200300, 0x400000, 0x80000, "game.exe", 0x200500 );
    ldr_entry( WB, 0x200300, 0x20010c, 0x10000000, 0x200000, "libcef.dll", 0x200540 );   /* last entry points back to the head */

    /* the waiting thread: eip in libcef, the ebp chain returns into game.exe, the stack names libcef and game.exe */
    thread( TEB_A, CPU_A, 0x20, 0x34, 0x14c, 0x10001234, 0x100000, 0x100100 );
    put32( WB + 0x100100, 0x100200 ); put32( WB + 0x100104, 0x400a10 );
    put32( WB + 0x100200, 0x100300 ); put32( WB + 0x100204, 0x10002000 );
    put32( WB + 0x100300, 0 );        put32( WB + 0x100304, 0x400b20 );
    put32( WB + 0x100000 + 8, 0x400c30 ); put32( WB + 0x100000 + 16, 0x10003000 );
    /* the SRW lock: owners half (offset 2) = 2 shared owners */
    put16( WB + 0x100002, 2 );
    /* the same process's second thread */
    thread( TEB_B, CPU_B, 0x20, 0x35, 0x14c, 0x400777, 0x180000, 0x180100 );
    put32( WB + 0x180100, 0 ); put32( WB + 0x180104, 0x400d40 );
    /* another process's thread, and a 64-bit thread */
    thread( TEB_O, CPU_O, 0x21, 0x40, 0x14c, 0x400888, 0x1a0000, 0 );   /* TEB in another window: filtered by the window test */
    thread( TEB_X, CPU_X, 0x22, 0x41, 0x8664, 0x1400, 0x1b0000, 0 );

    ios_thread_registry[0].teb = TEB_A; ios_thread_registry[1].teb = TEB_B; ios_thread_registry[2].teb = TEB_O; ios_thread_registry[3].teb = TEB_X;
}

int main( int argc, char **argv )
{
    int mode = argc > 1 ? atoi( argv[1] ) : 0;
    build();
    switch (mode)
    {
    case 0: ios_srw_hang_census( 4 ); ios_srw_hang_census( 4 ); break;                   /* dumps once */
    case 1: wait_age = 29; ios_srw_hang_census( 4 ); break;                              /* younger than the threshold */
    case 2: wait_inf = 0; ios_srw_hang_census( 4 ); break;                               /* a wait with a timeout */
    case 3: put16( WB + 0x100002, 0 ); ios_srw_hang_census( 4 ); break;                   /* no owners */
    case 4: wait_addr = LOCK + 2; ios_srw_hang_census( 4 ); break;                       /* not 2 mod 4 */
    case 5: ios_srw_hang_census( 4 ); break;                                             /* run with MADEIRA_SRW_HANG_DIAG_S=0 */
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as d:
        c = Path(d) / "t.c"
        exe = Path(d) / "t"
        c.write_text(code)
        r = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the production census compiles on the host", r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            def run(mode, env=None):
                import os
                e = dict(os.environ)
                e.pop("MADEIRA_SRW_HANG_DIAG_S", None)
                e.update(env or {})
                return subprocess.run([str(exe), str(mode)], capture_output=True, text=True, env=e, timeout=60)

            r0 = run(0)
            out = r0.stderr
            check("the census runs", r0.returncode == 0, r0.stderr + r0.stdout)
            check("one report: tid, pid, age, the lock address and the owners count",
                  out.count("[srw-hang] ml2250 tid=0034 of pid 0020 waits 31s for an SRW lock at 0x7000100002 held by 2 shared owner(s)") == 1, out)
            check("the process is listed with its window and its two modules",
                  "[srw-hang] ml2250 pid=0020 window=0x7000000000: every 32-bit thread of the process, 2 modules" in out, out)
            check("the waiting thread: eip in libcef.dll, esp/ebp, the wait and the lock word",
                  "[srw-hang] tid=0034 eip=10001234(libcef.dll+0x1234) esp=00100000 ebp=00100100 wait=0x7000100002 31s INF w=00020000" in out, out)
            check("its EBP chain returns into game.exe and libcef.dll, resolved",
                  "[srw-hang]   ebp: game.exe+0xa10 libcef.dll+0x2000 game.exe+0xb20" in out, out)
            check("its stack words are named by module", "[srw-hang]   stack: +8:game.exe+0xc30 +10:libcef.dll+0x3000" in out
                  or "+8:game.exe+0xc30" in out and "+10:libcef.dll+0x3000" in out, out)
            check("the second thread of the same process is dumped",
                  "[srw-hang] tid=0035 eip=00400777(game.exe+0x777)" in out, out)
            check("a thread of another process and a 64-bit thread are not",
                  "tid=0040" not in out and "tid=0041" not in out and "pid=0021" not in out, out)
            check("a second cycle does not dump the process again", out.count("[srw-hang] ml2250 pid=0020") == 1, out)
            for mode, what in ((1, "a wait younger than the threshold"), (2, "a wait with a timeout"), (3, "a lock word with no owners"),
                               (4, "an address that is not 2 mod 4")):
                rr = run(mode)
                check("%s logs nothing" % what, rr.returncode == 0 and "[srw-hang]" not in rr.stderr, rr.stderr)
            rr = run(5, {"MADEIRA_SRW_HANG_DIAG_S": "0"})
            check("MADEIRA_SRW_HANG_DIAG_S=0 logs nothing", rr.returncode == 0 and "[srw-hang]" not in rr.stderr, rr.stderr)
            rr = run(0, {"MADEIRA_SRW_HANG_DIAG_S": "40"})
            check("a threshold of 40 seconds keeps a 31 second wait quiet", rr.returncode == 0 and "[srw-hang]" not in rr.stderr, rr.stderr)

wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-srw-census.py" in wf)
print("PASS: SRW hang census" if ok else "FAILED: SRW hang census")
sys.exit(0 if ok else 1)
