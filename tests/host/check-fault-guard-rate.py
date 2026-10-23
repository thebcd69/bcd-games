#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_FAULT_GUARD_RATE = 1: the fault-storm guards count a real storm only (build 591); no Wine runs.

Four tester logs (SpiderHeck ZHVF9Y, ROUNDS A1CX12, PlateUp 21DKZW, Red Dead Redemption 2 2SR5VJ) show a live game ended by one of two
guards in signal_arm64_ios.c:
  - ml461, Mach path: 2000 identical (thread, pc, address) deliveries in a row end the process. SpiderHeck reached it at 44 a second
    while drawing 92 fps, ROUNDS at 460 a second at 56 fps, RDR2 at 27 a second at 7 fps: Mono's null-check traps are meant to fault
    and each one is a managed exception.
  - SEGV LOOP FATAL, BSD path: 5 repeats of one pc, counted over ALL threads, skip the instruction and set x0 = 0xDEAD0001 (PlateUp
    then dereferenced 0xdead0011).
With the switch a Mach run is a storm when 2000 deliveries lie within 0.5 s, or when the presenter's frame counter does not move
(3 frames) during the run; a BSD run is counted per thread and is a storm when its 5 repeats lie within 5 ms of the first. A real
storm (tens of thousands a second) ends exactly as before. Unset, the guards are the old code.

The helpers and the BSD block are cut out of the source and compiled on the host with a fake clock, a fake thread id and a fake
frame counter. Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- source shape
check('the switch is read once, by name, and is on only for a value that is not empty or 0',
      src.count('getenv( "MADEIRA_FAULT_GUARD_RATE" )') == 1 and "v = (e && e[0] && e[0] != '0') ? 1 : 0;" in src)
mach_start = src.index('static struct { uint64_t thread, key, guest_rip; uint32_t n; uint64_t t0, p0; } redeliv[16];')
mach_end = src.index('ios_redeliv_terminating = 1;', mach_start)
mach = src[mach_start:mach_end]
check('Mach path: a run records its start only with the switch', 'if (ios_fault_guard_rate_on()) ios_fgr_run_begin( &redeliv[rslot].t0, &redeliv[rslot].p0 );' in mach)
check('Mach path: the 2000 mark is skipped only when the switch is on AND the run is alive; the old condition is otherwise intact',
      'else if (redeliv[rslot].n >= 2000 && !ios_redeliv_terminating &&\n                 !(ios_fault_guard_rate_on() &&\n'
      '                   ios_fgr_run_alive( &redeliv[rslot].n, &redeliv[rslot].t0, &redeliv[rslot].p0, pc, fault_addr )))' in mach)
check('Mach path: the 256 line prints as before without the switch',
      'if (!ios_fault_guard_rate_on() || ios_fgr_say_256())\n                dprintf( 2, "[redeliv] 256 identical redeliveries' in mach)
tail = src[mach_end:mach_end + 400]
check('the kill (task_terminate, _exit(76)) is still the end of that branch',
      'task_terminate( mach_task_self() );\n            _exit( 76 );' in src[mach_end:mach_end + 4500] and 'terminating process rev=ml465' in src[mach_end:mach_end + 4500])

bsd_start = src.index('        static uintptr_t last_fault_pc = 0;')
bsd_end = src.index('\n    }\n#endif', bsd_start)
bsd = src[bsd_start:bsd_end]
check('BSD path: without the switch the pointers are the two statics', 'uintptr_t *lastp = &last_fault_pc;' in bsd and 'int *cntp = &fault_repeat_count;' in bsd)
check('BSD path: the fatal branch is unchanged (skip the instruction, x0 = 0xDEAD0001, x18 fixup, reset, return)',
      'PC_sig(context) = PC_sig(context) + 4;\n                REGn_sig(0, context) = 0xDEAD0001;\n                ios_fixup_x18_for_return( context );' in bsd
      and 'if (*cntp >= 5)' in bsd)
check('BSD path: the per-thread slot and the window apply only inside the switch',
      bsd.index('if (ios_fault_guard_rate_on())') < bsd.index('fgr_slot = ios_fgr_bsd_slot_get();') < bsd.index('fgr_now - fgr_slot->t0 > IOS_FGR_BSD_RUN_NS'))
check('no process-ending or memory-writing call was added to the helpers',
      not [f for f in ('task_terminate', '_exit', 'abort(', 'mprotect', 'mach_vm_write', 'thread_suspend')
           if f in src[src.index('#define IOS_FGR_DENSE_NS'):src.index('struct ios_fgr_bsd_slot *ios_fgr_bsd_slot_get')]])

# ---------------------------------------------------------------- compiled runs
gate0 = src.index('static int ios_sig_storm_gate( unsigned long *n )')
gate = src[gate0:src.index('\n}\n', gate0) + 3]
h0 = src.index('#define IOS_FGR_DENSE_NS')
h1 = src.index('\n}\n', src.index('static struct ios_fgr_bsd_slot *ios_fgr_bsd_slot_get')) + 3
helpers = src[h0:h1]
block = bsd.replace('return;', '{ fatal_hit++; return; }')

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include <stdarg.h>

static uint64_t fake_now;                      /* nanoseconds */
static uintptr_t fake_thread = 0x1000;
static uint64_t fake_presents;
static int have_counter = 1;
#define clock_gettime fake_clock_gettime
static int fake_clock_gettime(int id, struct timespec *ts) { (void)id; ts->tv_sec = fake_now / 1000000000ull; ts->tv_nsec = fake_now % 1000000000ull; return 0; }
#define pthread_self fake_pthread_self
static pthread_t fake_pthread_self(void) { return (pthread_t)fake_thread; }
#ifndef NO_COUNTER
uint64_t madeira_get_present_count(void) { return fake_presents; }
#endif
#define dprintf(fd, ...) do { if (getenv("QUIET_LOG") == NULL) fprintf(stderr, __VA_ARGS__); } while (0)
'''
harness += gate + helpers + r'''

/* ---- the BSD block: the production text, with fakes for the Wine macros */
struct fake_ctx { uintptr_t pc; uint64_t x0; };
struct fake_si { void *si_addr; };
#define PC_sig(c) ((c)->pc)
#define REGn_sig(n, c) ((c)->x0)
#define ERR(...) do { if (getenv("QUIET_LOG") == NULL) fprintf(stderr, __VA_ARGS__); } while (0)
static uintptr_t ios_teb_for_signals;
static void ios_fixup_x18_for_return(struct fake_ctx *c) { (void)c; }
static int fatal_hit;
static void bsd_fault(struct fake_ctx *context, struct fake_si *siginfo)
{
    {
@BLOCK@
    }
}

static int failures;
#define EXPECT(c, msg) do { if (!(c)) { printf("FAIL %s\n", msg); failures++; } else printf("ok   %s\n", msg); } while (0)

/* Mach path driver: n deliveries, dt_ns apart, with `frames` presents spread over the run; returns alive at the 2000 mark */
static int run_mach(uint64_t gap_ns, uint64_t frames_total, int counter)
{
    uint32_t n = 1; uint64_t t0 = 0, p0 = 0;
    fake_presents = 1000; have_counter = counter;
    ios_fgr_run_begin(&t0, &p0);
    for (; n < 2000;)
    {
        n++;
        fake_now += gap_ns;
        if (frames_total) fake_presents = 1000 + (frames_total * n) / 2000;
    }
    return ios_fgr_run_alive(&n, &t0, &p0, 0x16b038e5c, 0x710455c01c) ? (int)n : 0;   /* the new n (1) when alive, 0 when the guard kills */
}

int main(void)
{
    int i;
#ifdef NO_COUNTER
    setenv("MADEIRA_FAULT_GUARD_RATE", "1", 1);
    EXPECT(madeira_get_present_count == NULL, "no winemetal in the build: the weak symbol is NULL");
    EXPECT(run_mach(22000000ull, 0, 0) == 1, "no frame counter: a sparse run (44 a second) is alive, the rate decides");
    EXPECT(run_mach(20000, 0, 0) == 0, "no frame counter: a dense run is a storm");
    printf(failures ? "FAILED %d\n" : "all ok\n", failures);
    return failures != 0;
#endif

    unsetenv("MADEIRA_FAULT_GUARD_RATE");
    EXPECT(ios_fault_guard_rate_on() == 0, "unset: off");
    ios_fault_guard_rate = -1; setenv("MADEIRA_FAULT_GUARD_RATE", "", 1);
    EXPECT(ios_fault_guard_rate_on() == 0, "empty: off");
    ios_fault_guard_rate = -1; setenv("MADEIRA_FAULT_GUARD_RATE", "0", 1);
    EXPECT(ios_fault_guard_rate_on() == 0, "0: off");
    ios_fault_guard_rate = -1; setenv("MADEIRA_FAULT_GUARD_RATE", "1", 1);
    EXPECT(ios_fault_guard_rate_on() == 1, "1: on");

    /* ---- Mach path */
    EXPECT(run_mach(20000, 0, 1) == 0, "a real storm (2000 deliveries 20 us apart, 40 ms) ends the process");
    EXPECT(run_mach(20000, 5000, 1) == 0, "... even when frames are presented by another thread meanwhile (a dense run is a storm)");
    EXPECT(run_mach(120000, 0, 1) == 0, "8000 a second, no frames: dense, ends the process");
    EXPECT(run_mach(249000, 0, 1) == 0, "just inside 0.5 s with no frames: ends the process");
    EXPECT(run_mach(22000000ull, 4000, 1) == 1, "SpiderHeck: 44 a second for 45 s with 4000 frames: alive, the run starts over");
    EXPECT(run_mach(2170000ull, 260, 1) == 1, "ROUNDS: 460 a second for 4.3 s with 260 frames: alive");
    EXPECT(run_mach(37000000ull, 700, 1) == 1, "RDR2: 27 a second for 74 s at 10 frames a second: alive");
    EXPECT(run_mach(22000000ull, 0, 1) == 0, "44 a second and the presenter does not move: ends the process as before");
    EXPECT(run_mach(22000000ull, 2, 1) == 0, "44 a second and only 2 frames: ends the process (3 are needed)");
    EXPECT(run_mach(22000000ull, 3, 1) == 1, "44 a second and 3 frames: alive");
    EXPECT(run_mach(260000ull, 100, 1) == 1, "2000 deliveries in 520 ms with frames: sparse, alive");
    EXPECT(run_mach(249000ull, 100, 1) == 0, "2000 deliveries in 498 ms: dense, ends the process even with frames");
    /* ---- BSD path */
    {
        struct fake_ctx c = { 0x1000, 0 }; struct fake_si si = { (void *)0x28 };
        /* a real storm on one thread: 100 us apart */
        fake_thread = 0x2000; fake_now = 1000000000ull; fatal_hit = 0; c.x0 = 0;
        for (i = 0; i < 4; i++) { c.pc = 0x1000; bsd_fault(&c, &si); fake_now += 100000; }
        EXPECT(fatal_hit == 0, "a storm: four repeats do not end it");
        c.pc = 0x1000; bsd_fault(&c, &si);
        EXPECT(fatal_hit == 1 && c.x0 == 0xDEAD0001 && c.pc == 0x1004, "a storm 100 us apart: the fifth repeat skips the instruction and sets x0 as before");

        /* Mono: one thread, the same pc every 2.2 ms (ROUNDS), 40 faults */
        fatal_hit = 0; c.x0 = 0; fake_now += 1000000000ull;
        for (i = 0; i < 40; i++) { c.pc = 0x2000; bsd_fault(&c, &si); fake_now += 2170000; }
        EXPECT(fatal_hit == 0 && c.x0 == 0, "460 a second on one thread: never fatal, x0 untouched");

        /* PlateUp: five threads take turns at the same pc */
        fatal_hit = 0; c.x0 = 0;
        for (i = 0; i < 40; i++) { fake_thread = 0x3000 + 0x100 * (i % 5); c.pc = 0x3000; bsd_fault(&c, &si); fake_now += 300000; }
        EXPECT(fatal_hit == 0 && c.x0 == 0, "five threads alternating at one pc (PlateUp): never fatal");

        /* a storm on one thread while another thread faults elsewhere in between */
        fatal_hit = 0; c.x0 = 0;
        for (i = 0; i < 5; i++)
        {
            fake_thread = 0x4000; c.pc = 0x4000; bsd_fault(&c, &si); fake_now += 50000;
            fake_thread = 0x4100; c.pc = 0x4444 + 4 * i; bsd_fault(&c, &si); fake_now += 50000;
        }
        EXPECT(fatal_hit == 1, "a storm on one thread is still ended while another thread faults in between");

        /* a different pc resets the run, as before */
        fatal_hit = 0; c.x0 = 0; fake_thread = 0x5000;
        for (i = 0; i < 30; i++) { c.pc = (i % 2) ? 0x5000 : 0x5004; bsd_fault(&c, &si); fake_now += 20000; }
        EXPECT(fatal_hit == 0, "two pcs alternating: never fatal");

        /* a slow first four and a quick fifth do not make a storm; the window is from the first repeat */
        fatal_hit = 0; c.x0 = 0; fake_thread = 0x6000;
        c.pc = 0x6000; bsd_fault(&c, &si); fake_now += 1500000;
        c.pc = 0x6000; bsd_fault(&c, &si); fake_now += 1500000;
        c.pc = 0x6000; bsd_fault(&c, &si); fake_now += 1500000;
        c.pc = 0x6000; bsd_fault(&c, &si); fake_now += 1500000;    /* 6 ms since the first */
        c.pc = 0x6000; bsd_fault(&c, &si);
        EXPECT(fatal_hit == 0, "five repeats spread over 6 ms: not a storm (the run starts over)");
        for (i = 0; i < 4; i++) { fake_now += 10000; c.pc = 0x6000; bsd_fault(&c, &si); }
        EXPECT(fatal_hit == 1, "... and a real storm after it is ended");
    }

    /* ---- the switch off: the old global logic (statics) */
    {
        struct fake_ctx c = { 0x1000, 0 }; struct fake_si si = { (void *)0x28 };
        ios_fault_guard_rate = -1; unsetenv("MADEIRA_FAULT_GUARD_RATE");
        fatal_hit = 0; c.x0 = 0;
        for (i = 0; i < 40; i++) { fake_thread = 0x3000 + 0x100 * (i % 5); c.pc = 0x3000; bsd_fault(&c, &si); fake_now += 300000; }
        EXPECT(fatal_hit >= 1, "switch off: five threads at one pc ARE ended, as today (the old global counter)");
        fatal_hit = 0; c.x0 = 0; fake_thread = 0x7000;
        for (i = 0; i < 40; i++) { c.pc = 0x7000; bsd_fault(&c, &si); fake_now += 2170000; }
        EXPECT(fatal_hit >= 1, "switch off: 460 a second on one thread is ended, as today");
    }

    printf(failures ? "FAILED %d\n" : "all ok\n", failures);
    return failures != 0;
}
'''.replace('@BLOCK@', block)

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-o', str(exe), str(c)],
                        capture_output=True, text=True)
    check('the production helpers and the BSD block compile on the host', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        env = dict(os.environ, QUIET_LOG='1')
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
        for line in r.stdout.splitlines():
            if line.startswith('ok   ') or line.startswith('FAIL'):
                check(line[5:] if line.startswith('ok   ') else line[5:], line.startswith('ok   '))
        check('every compiled scenario passes', r.returncode == 0 and 'all ok' in r.stdout, r.stdout[-1500:] + r.stderr[-500:])

    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-DNO_COUNTER', '-Wl,-U,_madeira_get_present_count',
                         '-o', str(Path(tmp) / 't2'), str(c)], capture_output=True, text=True)
    check('the same code without the weak symbol compiles and links', cc.returncode == 0, cc.stderr[-2000:])
    if cc.returncode == 0:
        r = subprocess.run([str(Path(tmp) / 't2')], capture_output=True, text=True, env=dict(os.environ, QUIET_LOG='1'))
        for line in r.stdout.splitlines():
            if line.startswith('ok   ') or line.startswith('FAIL'):
                check(line[5:], line.startswith('ok   '))
        check('every no-counter scenario passes', r.returncode == 0 and 'all ok' in r.stdout, r.stdout[-800:] + r.stderr[-500:])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
