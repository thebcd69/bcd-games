#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 mckenna654 (the cases: thebcd69/bcd-games#4, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 le0-VV (the way the breaker is cut out of the source and run: thebcd69/bcd-games#16, tests/host/check-fault-guard-guest-rip.py)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (opt-in switch, the real breaker block, separate-process runs).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""env.MADEIRA_FAULT_LOOP_GUEST_PROGRESS = 1: a run of breakpoints at different guest addresses is not a stuck thread.

Based on thebcd69/bcd-games#4 by mckenna654. FEX raises many distinct guest breakpoints (int3) through ONE host trap stub, so the page-fault
loop breaker of build/ntdll-unix/signal_arm64_ios.c ("[fault-stuck] BREAKING LOOP": 50 faults at one page and pc divert the thread to
abort_thread) took Diablo II: Resurrected's loader, which steps through 96+ breakpoints of its own, for one stationary fault and ended its main
thread. With the switch, a Mach breakpoint trap from a VERIFIED registered FEX frame (x28 is the thread's registered frame, the saved guest
RIP is non-zero) belongs to a run keyed by (thread, frame, guest RIP); a different key restarts the breaker's run. Everything else counts as before.

This cuts the REAL breaker block and the helper out of the source and runs them on the host (AddressSanitizer + UBSan) against fakes for the
Mach calls, one process per switch state:
  - switch off: the helper is never evaluated; a run of breakpoints at different guest RIPs is diverted at the 51st trap (today's behaviour,
    shown), and so is every other run below;
  - switch on: 25,000 breakpoints at different guest RIPs are never diverted; the same guest RIP again is diverted at the 51st trap; an unverified
    frame (none, a frame that is not the registered one, a zero RIP), another exception type, a native access violation, the NULL page and a loop
    over advancing addresses end exactly as with the switch off (51, 51, 51, 51, 51, 51, 20001); a run that changes its guest RIP every 30 traps is
    never diverted; two threads that alternate restart the run each time (the contributor's key includes the thread).
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ci = (root / '.github/workflows/build-ipa.yml').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


helper_start = src.index('struct ios_fault_progress\n{')
helper_end = src.index('static struct ios_fault_progress ios_fault_progress_last;') + len('static struct ios_fault_progress ios_fault_progress_last;')
helper = src[helper_start:helper_end]
block_start = src.index('{\n                        static volatile uint64_t stuck_page;')
block_end = src.index('\n\n                    if (cnt <= 50', block_start)
block = src[block_start:block_end]

check('the switch is read once, by name, and is on only for the value 1',
      src.count('getenv( "MADEIRA_FAULT_LOOP_GUEST_PROGRESS" )') == 1 and "cached = (s && s[0] == '1' && !s[1]) ? 1 : 0;" in helper
      and 'static int cached = -1;' in helper)
call = ('if (ios_fault_loop_guest_progress_on())\n'
        '                        {\n'
        '                            uint64_t previous_rip = 0;\n'
        '                            if (ios_fault_loop_guest_progress( &ios_fault_progress_last, thread_teb, req->exception,\n'
        '                                                               (uintptr_t)fex_state_q, state.__x[28], state_rip_q, &previous_rip ))')
check('the helper is evaluated only behind the switch, with the thread\'s TEB, the exception type, the registered FEX frame, x28 and the saved guest RIP',
      block.count(call) == 1 and src.count('ios_fault_loop_guest_progress(') == 2)
check('the restart is stuck_page = 0, after the NULL-page rule and before the two tests, so the old "new run" branch runs and nothing else changes',
      block.index('if (!fpage) fpage = 1;') < block.index(call) < block.index('stuck_page = 0;')
      < block.index('if (fpage == stuck_page && fault_pc_check == stuck_pc &&')
      # round 630 (agent O2 S2): the second restart is the __TRY rule's (a fault the system call answers), between the hook and the tests
      and block.count('stuck_page = 0;') == 2 and block.index(call) < block.index('if (ios_mach_syscall_try_active(')
      < block.index('if (fpage == stuck_page && fault_pc_check == stuck_pc &&'))
check('the old text of the breaker is all still there (the 50 mark, the abort_thread diversion, the 20000 page ceiling, the region map)',
      'if (n >= 50)' in block and 'abort_thread' in block and 'if (pn >= 20000) stuck_n = 49;' in block and '[fault-stuck] BREAKING LOOP: page' in block
      and '[fault-stuck] ml876 page' in block and 'region map around it' in block)
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('a later game of the same app run does not inherit the switch', 'unsetenv("MADEIRA_FAULT_LOOP_GUEST_PROGRESS")' in bridge)

harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define EXC_BAD_ACCESS 1
#define EXC_BAD_INSTRUCTION 2
#define EXC_BREAKPOINT 6
#define KERN_SUCCESS 0
#define ARM_THREAD_STATE64 6
#define VM_REGION_BASIC_INFO_COUNT_64 10
#define VM_REGION_BASIC_INFO_64 9
#define MACH_PORT_NULL 0
#define STDERR_FILENO 2
typedef uint64_t mach_vm_address_t;
typedef uint64_t mach_vm_size_t;
typedef unsigned mach_msg_type_number_t;
typedef unsigned mach_port_t;
typedef void *thread_state_t;
typedef void *vm_region_info_t;
typedef struct { int protection, max_protection; } vm_region_basic_info_data_64_t;
typedef struct { uint64_t pc, sp, __x[29]; } arm_thread_state64_t;
static unsigned killed, progress_lines;
/* round 630 (agent O2 S2): a fault inside a system call's __TRY is no livelock; off here (check-syscall-fault-loop.py runs it) */
static int ios_mach_syscall_try_active(uintptr_t teb, uint64_t sp) { (void)teb; (void)sp; return 0; }
static int mach_task_self(void) { return 0; }
static int mach_vm_region(int task, mach_vm_address_t *q, mach_vm_size_t *rs,
                         int f, vm_region_info_t r, mach_msg_type_number_t *rc, mach_port_t *ro)
{ (void)task; (void)q; (void)rs; (void)f; (void)r; (void)rc; (void)ro; return 1; }
static uint64_t __darwin_arm_thread_state64_get_sp(arm_thread_state64_t s) { return s.sp; }
#define __darwin_arm_thread_state64_set_sp(s, v) ((s).sp = (v))
#define __darwin_arm_thread_state64_set_pc_fptr(s, v) ((s).pc = (uintptr_t)(v))
void abort_thread(int status) { (void)status; }
static int thread_set_state(int t, int f, thread_state_t p, unsigned n)
{ (void)t; (void)f; (void)n; assert(((arm_thread_state64_t *)p)->pc == (uintptr_t)abort_thread); ++killed; return 0; }
#define dprintf(fd, ...) ((void)(fd), (void)(strstr(#__VA_ARGS__, "fault-progress") ? ++progress_lines : 0))
@HELPER@
static void fault(uintptr_t thread_teb, int exception, uint64_t address, uint64_t frame, uint64_t x28, uint64_t guest_rip)
{
    struct { int exception; } request = { exception }, *req = &request;
    uint64_t fault_addr = address, fault_pc_check = 0x169ff852c, state_rip_q = guest_rip;
    void *fex_state_q = (void *)(uintptr_t)frame;
    arm_thread_state64_t state = { .pc = 0, .sp = 0x7000000101 };
    int thread = 1, handled = 0; unsigned count = 0;
    state.__x[28] = x28;
    (void)thread_teb; (void)fex_state_q; (void)state_rip_q;
@BLOCK@
    (void)handled; (void)req; (void)thread; (void)count;
}
int main(int argc, char **argv)
{
    assert(argc == 3);
    int on = !strcmp(argv[1], "on");
    if (on) setenv("MADEIRA_FAULT_LOOP_GUEST_PROGRESS", "1", 1); else unsetenv("MADEIRA_FAULT_LOOP_GUEST_PROGRESS");
    const char *mode = argv[2];
    unsigned n = 0, limit = 25000;
    const uint64_t F = 0x7c44001140ULL;
    while (n < limit && !killed)
    {
        uint64_t rip = 0x71001000 + n;
        if (!strcmp(mode, "progress"))          fault(1, EXC_BREAKPOINT, 0x169ff852c, F, F, 0x71001000 + n);
        else if (!strcmp(mode, "same-rip"))     fault(1, EXC_BREAKPOINT, 0x169ff852c, F, F, 0x71001000);
        else if (!strcmp(mode, "no-frame"))     fault(1, EXC_BREAKPOINT, 0x169ff852c, 0, 0, rip);
        else if (!strcmp(mode, "other-frame"))  fault(1, EXC_BREAKPOINT, 0x169ff852c, F, F + 0x1000, rip);
        else if (!strcmp(mode, "zero-rip"))     fault(1, EXC_BREAKPOINT, 0x169ff852c, F, F, 0);
        else if (!strcmp(mode, "access"))       fault(1, EXC_BAD_ACCESS, 0x169ff852c, F, F, rip);
        else if (!strcmp(mode, "instruction"))  fault(1, EXC_BAD_INSTRUCTION, 0xd4400000, F, F, rip);
        else if (!strcmp(mode, "null-page"))    fault(1, EXC_BAD_ACCESS, 0, F, F, rip);
        else if (!strcmp(mode, "advancing"))    fault(1, EXC_BAD_ACCESS, 0x7000000000ULL + (n % 128) * 8, 0, 0, 0);
        else if (!strcmp(mode, "every-30"))     fault(1, EXC_BREAKPOINT, 0x169ff852c, F, F, 0x71001000 + n / 30);
        else if (!strcmp(mode, "two-threads"))  fault(1 + (n & 1), EXC_BREAKPOINT, 0x169ff852c, F, F, 0x71001000);
        else if (!strcmp(mode, "then-stuck"))   fault(1, EXC_BREAKPOINT, 0x169ff852c, F, F, n < 100 ? 0x71001000 + n : 0x71009000);
        else { fprintf(stderr, "mode?\n"); return 2; }
        n++;
    }
    /* the numbers the cases above must give: n traps when the first diversion happens (0 = none in 25000) */
    unsigned want;
    if (!strcmp(mode, "progress") || !strcmp(mode, "every-30") || !strcmp(mode, "two-threads")) want = on ? 0 : 51;
    else if (!strcmp(mode, "then-stuck")) want = on ? 151 : 51;
    else if (!strcmp(mode, "advancing")) want = 20001;
    else want = 51;
    if (want == 0) assert(!killed && n == limit);
    else assert(killed == 1 && n == want);
    if (on && !strcmp(mode, "progress")) assert(progress_lines == 16);        /* logged 16 times at most */
    else assert(progress_lines == 0 || !strcmp(mode, "then-stuck") || !strcmp(mode, "every-30"));
    printf("%s %s: %u traps, %u diversions, %u [fault-progress] lines\n", argv[1], mode, n, killed, progress_lines);
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
flags = ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-function',
         '-Wno-unused-but-set-variable', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
with tempfile.TemporaryDirectory() as tmp:
    c, exe = Path(tmp) / 'loop.c', Path(tmp) / 'loop'
    c.write_text(harness.replace('@HELPER@', helper).replace('@BLOCK@', block))
    r = subprocess.run([cc] + flags + [str(c), '-o', str(exe)], capture_output=True, text=True)
    check('the production helper and breaker block compile on the host with AddressSanitizer and UBSan', r.returncode == 0, r.stderr[-3000:])
    if r.returncode == 0:
        cases = (('progress', 'breakpoints at 25,000 different guest RIPs from a verified frame'),
                 ('same-rip', 'one guest breakpoint again and again'),
                 ('no-frame', 'breakpoints with no registered frame (the guest RIP varies)'),
                 ('other-frame', 'breakpoints whose x28 is not the registered frame (the guest RIP varies)'),
                 ('zero-rip', 'breakpoints with a saved guest RIP of 0'),
                 ('access', 'access violations at one pc (the guest RIP varies)'),
                 ('instruction', 'invalid-instruction traps at the thunk (the guest RIP varies)'),
                 ('null-page', 'NULL-page accesses (the guest RIP varies)'),
                 ('advancing', 'memory faults on advancing addresses inside one page'),
                 ('every-30', 'a verified run that moves to a new guest RIP every 30 traps'),
                 ('two-threads', 'two threads alternating at one guest RIP'),
                 ('then-stuck', 'a hundred different guest RIPs, then one that never moves'))
        for state in ('off', 'on'):
            for mode, what in cases:
                r = subprocess.run([str(exe), state, mode], capture_output=True, text=True, env=env)
                good = r.returncode == 0
                if good:
                    out = r.stdout.strip()
                else:
                    out = (r.stdout + r.stderr).strip()[-600:]
                check('switch %s: %s -> %s' % (state, what, out.split(': ', 1)[-1] if good else 'FAILED'), good, out)

lst = (root / 'recommended/recommendations.json').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('only the D2R list carries the line, from build 598', lst.count('"env.MADEIRA_FAULT_LOOP_GUEST_PROGRESS = 1"') == 1
      and '{"line": "env.MADEIRA_FAULT_LOOP_GUEST_PROGRESS = 1", "minBuild": 598}' in lst)
check('the list policy allows the name; the settings catalog has it', '"MADEIRA_FAULT_LOOP_GUEST_PROGRESS"' in policy and 'env.MADEIRA_FAULT_LOOP_GUEST_PROGRESS' in catalog)
check('build-ipa.yml runs this check', 'python3 tests/host/check-fault-loop-guest-progress.py' in ci)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
