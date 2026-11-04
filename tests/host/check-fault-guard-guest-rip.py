#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 le0-VV (the compiled-guard harnesses: thebcd69/bcd-games#16, submitted with a Signed-off-by under CONTRIBUTING.md)
# Copyright 2026 thebcd69 (BCD) (the changes below)
# Modified by BCD (thebcd69), 2026-10-24: ported into the tree behind the public repository (host check, build step, opt-in switch).
# Not BCD Material: LICENSE-BCD.md section 1 keeps the other author's header and licence on a file that mixes in their text.
"""env.MADEIRA_FAULT_GUARD_GUEST_RIP = 1: FEX's shared SIGILL thunk is judged by the guest instruction; no Wine runs.

Based on thebcd69/bcd-games#16 by le0-VV (Minecraft Dungeons II). FEX raises a guest #UD / invalid instruction through ONE host
thunk (`hlt #0` in the JIT pool): every such Mach exception has the same host pc and the same fault address (the opcode), whatever
guest instruction caused it. A program that recovers from a long run of them (it steps over each one in its own handler) looked
like one stationary fault to
  - the redelivery guard (2000 identical (thread, pc, address) deliveries end the process), and
  - the page-fault loop breaker ([fault-stuck], 50 faults at one page and pc divert the thread to abort_thread).
With the switch the redelivery guard also keys a thunk delivery on the guest RIP FEX saved in its CpuStateFrame (fault-safe reads;
any pc that is not the thunk, an unreadable frame or a non-SIGILL fault keeps the key it always had), and the page-fault breaker
counts EXC_BAD_ACCESS only. A stationary guest fault, a native fault and a real memory loop end exactly as before. Off (default):
the same code as before, shown below by running the production text both ways.

The helpers, the redelivery guard and the page-fault breaker are cut out of build/ntdll-unix/signal_arm64_ios.c and compiled on
the host (AddressSanitizer + UBSan) with fakes for the readers and the Mach calls. Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
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


# ---------------------------------------------------------------- source shape
check('the switch is read once, by name, and is on only for a value that is not empty or 0',
      src.count('getenv( "MADEIRA_FAULT_GUARD_GUEST_RIP" )') == 1 and "v = (e && e[0] && e[0] != '0') ? 1 : 0;" in src[src.index('ios_fault_guard_guest_on( void )'):])
helper_start = src.index('static uint64_t ios_fault_guest_rip(')
helper = src[helper_start:src.index('\n}\n', helper_start) + 3]
check('the guest RIP is read only for generated code, a plausible frame, `hlt #0` and a readable RIP (frame + 0x18)',
      'ios_ajan2_pc_is_jit( pc )' in helper and 'frame < 0x100000 || frame >= 0x800000000000ULL' in helper and 'insn != 0xd4400000' in helper
      and 'frame + 0x18' in helper and helper.count('ios_ajan2_read(') == 2)
check('the helper does not write memory or end anything',
      not [f for f in ('task_terminate', '_exit', 'abort', 'mprotect', 'mach_vm_write', 'thread_suspend', 'thread_set_state') if f in helper])
capture_call = ('if (rec.ExceptionCode == EXCEPTION_ILLEGAL_INSTRUCTION && ios_fault_guard_guest_on())\n'
                '        redeliv_guest_rip = ios_fault_guest_rip( state, pc );')
check('the guest RIP is captured for a SIGILL delivery with the switch on, before state becomes the dispatcher context',
      src.count(capture_call) == 1 and src.index('REGn_sig(18, &uc) = thread_teb;') < src.index(capture_call) < src.index('    *state = mc.__ss;', src.index(capture_call))
      and src.count('uint64_t redeliv_guest_rip = 0;') == 1)
guard_start = src.index('static struct { uint64_t thread, key; uint32_t n; uint64_t t0, p0; } redeliv[16];')
guard_end = src.index('ios_redeliv_terminating = 1;', guard_start) + len('ios_redeliv_terminating = 1;')
guard = src[guard_start:guard_end]
check('the delivery run restarts when the guest RIP changes, in the same condition as thread and key',
      'redeliv[rslot].thread != (uint64_t)thread || redeliv[rslot].key != rkey || redeliv_rip[rslot] != redeliv_guest_rip' in guard
      and 'redeliv_rip[rslot] = redeliv_guest_rip;' in guard)
check('the 2000 mark, the 256 line and the kill are the old text (the rate switch and the rules that read the log still match)',
      'else if (redeliv[rslot].n >= 2000 && !ios_redeliv_terminating &&\n                 !(ios_fault_guard_rate_on() &&\n' in guard
      and 'if (!ios_fault_guard_rate_on() || ios_fgr_say_256())\n                dprintf( 2, "[redeliv] 256 identical redeliveries pc=0x%llx addr=0x%llx — storm forming rev=ml461\\n"' in guard
      and 'dprintf( 2, "[redeliv] 2000 identical redeliveries pc=0x%llx addr=0x%llx — unrecoverable host fault misdelivered to guest rev=ml461\\n",' in src
      and 'task_terminate( mach_task_self() );\n            _exit( 76 );' in src)
page_condition = 'if (req->exception == EXC_BAD_ACCESS || !ios_fault_guard_guest_on())\n                    {\n                        static volatile uint64_t stuck_page;'
check('the page-fault breaker skips instruction traps only with the switch on', src.count(page_condition) == 1)
check('build-ipa.yml runs this check', 'python3 tests/host/check-fault-guard-guest-rip.py' in ci)
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the switch is in the settings catalog (generator and generated file) and on the list policy',
      'env.MADEIRA_FAULT_GUARD_GUEST_RIP' in catalog and 'env.MADEIRA_FAULT_GUARD_GUEST_RIP' in gen
      and '"MADEIRA_FAULT_GUARD_GUEST_RIP"' in policy and 'case "MADEIRA_FAULT_GUARD_GUEST_RIP":' in policy)

# ---------------------------------------------------------------- compiled runs: the redelivery guard
switch_start = src.index('static int ios_fault_guard_guest = -1;')
switch_code = src[switch_start:src.index('\n}\n', src.index('static int ios_fault_guard_guest_on( void )')) + 3]
capture_start = src.index(capture_call)
capture = src[capture_start:src.index('\n    *state = mc.__ss;', capture_start)]

redeliv_harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint64_t __x[29]; } arm_thread_state64_t;
#define EXCEPTION_ILLEGAL_INSTRUCTION 0xc000001d
static const uint64_t jit_pc = 0x160000100ULL, frame_base = 0x7000000000ULL;
static uint32_t instruction = 0xd4400000;
static uint64_t saved_rip;
static int read_pc_ok = 1, read_rip_ok = 1, reads, fatal_hit;
static int ios_ajan2_pc_is_jit(uint64_t pc) { return pc >= 0x160000000ULL && pc < 0x170000000ULL; }
static int ios_ajan2_read(uint64_t address, void *buffer, size_t length)
{
    reads++;
    if (address == jit_pc && length == 4 && read_pc_ok) { memcpy(buffer, &instruction, 4); return 1; }
    if (address == frame_base + 0x18 && length == 8 && read_rip_ok) { memcpy(buffer, &saved_rip, 8); return 1; }
    return 0;
}
@SWITCH@
@HELPER@
static int ios_fault_guard_rate_on(void) { return 0; }
static void ios_fgr_run_begin(uint64_t *t0, uint64_t *p0) { *t0 = *p0 = 0; }
static int ios_fgr_say_256(void) { return 1; }
static int ios_fgr_run_alive(uint32_t *n, uint64_t *t0, uint64_t *p0, uint64_t pc, uint64_t addr)
{ (void)n; (void)t0; (void)p0; (void)pc; (void)addr; return 0; }
static int smc_diag, lines;
static void ios_smc_loop_diag(uint64_t thread, uint64_t pc, uint64_t addr, uint32_t code)
{ (void)thread; (void)pc; (void)addr; (void)code; smc_diag++; }
#define dprintf(...) (lines++)
static uint32_t exception_code = EXCEPTION_ILLEGAL_INSTRUCTION;
static void delivery(arm_thread_state64_t *state, uint64_t thread, uint64_t pc, uint64_t fault_addr)
{
    struct { uint32_t ExceptionCode; } rec = { exception_code };
    uint64_t redeliv_guest_rip = 0;
    @CAPTURE@
@GUARD@
    fatal_hit++;
    }
}
static void probe_checks(void)
{
    arm_thread_state64_t state = {{0}}; state.__x[28] = frame_base;
    saved_rip = 0x140001000;
    assert(ios_fault_guest_rip(&state, jit_pc) == saved_rip);                       /* the thunk: the saved RIP */
    reads = 0;
    assert(!ios_fault_guest_rip(&state, 0x100001000) && !reads);                    /* a native pc: no read at all */
    instruction = 0xd503201f;
    assert(!ios_fault_guest_rip(&state, jit_pc)); instruction = 0xd4400000;        /* generated code, but not hlt #0 */
    read_pc_ok = 0; assert(!ios_fault_guest_rip(&state, jit_pc)); read_pc_ok = 1;  /* the instruction cannot be read */
    read_rip_ok = 0; assert(!ios_fault_guest_rip(&state, jit_pc)); read_rip_ok = 1;/* the frame cannot be read */
    state.__x[28] = 0x1000; reads = 0;
    assert(!ios_fault_guest_rip(&state, jit_pc) && !reads);                         /* x28 is no frame */
    state.__x[28] = 0x800000000000ULL;
    assert(!ios_fault_guest_rip(&state, jit_pc) && !reads);
    state.__x[28] = frame_base; saved_rip = 0;
    assert(!ios_fault_guest_rip(&state, jit_pc));                                   /* a saved RIP of 0 is "not known" */
}
int main(int argc, char **argv)
{
    assert(argc == 3);
    probe_checks();
    int switch_on = !strcmp(argv[1], "on");
    if (switch_on) setenv("MADEIRA_FAULT_GUARD_GUEST_RIP", "1", 1); else unsetenv("MADEIRA_FAULT_GUARD_GUEST_RIP");
    arm_thread_state64_t state = {{0}}; state.__x[28] = frame_base;
    uint64_t pc = jit_pc;
    const char *mode = argv[2];
    int progress = !strcmp(mode, "progress"), native = !strcmp(mode, "native"), unreadable = !strcmp(mode, "unreadable");
    int reset = !strcmp(mode, "reset"), access = !strcmp(mode, "access"), other_insn = !strcmp(mode, "other-insn");
    if (native) pc = 0x100001000;
    if (unreadable) read_rip_ok = 0;
    if (access) exception_code = 0xc0000005;
    if (other_insn) instruction = 0xd503201f;
    int delivered = 0;
    while (delivered < 5000 && !fatal_hit)
    {
        saved_rip = 0x140001000 + ((progress || native || access || other_insn) ? (delivered % 6) * 7 : 0);
        if (reset && delivered >= 1999) saved_rip += 7;
        delivery(&state, 0x1234, pc, 0xd4400000);
        delivered++;
    }
    if (switch_on && (progress)) assert(!fatal_hit && delivered == 5000);
    else if (switch_on && reset) assert(fatal_hit == 1 && delivered == 3999);
    else assert(fatal_hit == 1 && delivered == 2000);
    printf("%s %s: %d deliveries, %d terminal decisions\n", argv[1], mode, delivered, fatal_hit);
    return 0;
}
'''

# ---------------------------------------------------------------- compiled runs: the page-fault breaker
page_start = src.index(page_condition)
page_end = src.index('\n\n                    if (cnt <= 50', page_start)
page_guard = src[page_start:page_end]
page_harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define EXC_BAD_ACCESS 1
#define EXC_BAD_INSTRUCTION 2
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
static unsigned killed;
static int guest_switch;
static int ios_fault_guard_guest_on(void) { return guest_switch; }
/* thebcd69/bcd-games#4 (MADEIRA_FAULT_LOOP_GUEST_PROGRESS) adds a hook to the same breaker block; it is off here (check-fault-loop-guest-progress.py runs it) */
struct ios_fault_progress { uintptr_t thread; uint64_t frame, rip; };
static struct ios_fault_progress ios_fault_progress_last;
static int ios_fault_loop_guest_progress_on(void) { return 0; }
static int ios_fault_loop_guest_progress(struct ios_fault_progress *last, uintptr_t thread, unsigned exception, uintptr_t frame, uintptr_t x28,
                                         uint64_t guest_rip, uint64_t *previous)
{ (void)last; (void)thread; (void)exception; (void)frame; (void)x28; (void)guest_rip; *previous = 0; return 0; }
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
#define dprintf(...) ((void)0)
static void fault(int exception, uint64_t address)
{
    struct { int exception; } request = { exception }, *req = &request;
    uint64_t fault_addr = address, fault_pc_check = 0x16cff44e8;
    arm_thread_state64_t state = { .pc = 0, .sp = 0x7000000101 };
    int thread = 1, handled = 0; unsigned count = 0;
    uintptr_t thread_teb = 0; void *fex_state_q = 0; uint64_t state_rip_q = 0; (void)thread_teb; (void)fex_state_q; (void)state_rip_q;
@GUARD@
    (void)handled; (void)req;
}
int main(int argc, char **argv)
{
    assert(argc == 3); unsigned n = 0;
    guest_switch = !strcmp(argv[1], "on");
    int instruction = !strcmp(argv[2], "instruction"), advancing = !strcmp(argv[2], "advancing"), reset = !strcmp(argv[2], "reset");
    while (n < 25000 && !killed)
    {
        uint64_t addr = instruction ? 0xd4400000 : 0x7000000000;
        if (advancing) addr += (n % 128) * 8;
        if (reset && n >= 49) addr += 0x4000;
        fault(instruction ? EXC_BAD_INSTRUCTION : EXC_BAD_ACCESS, addr); ++n;
    }
    if (instruction && guest_switch) assert(!killed && n == 25000);
    else if (instruction) assert(killed == 1 && n == 51);
    else if (advancing) assert(killed == 1 && n == 20001);
    else if (reset) assert(killed == 1 && n == 100);
    else assert(killed == 1 && n == 51);
    printf("%s %s: %u faults, %u aborts\n", argv[1], argv[2], n, killed);
    return 0;
}
'''

# ---------------------------------------------------------------- the switch's own reading
switch_harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
@SWITCH@
int main(void)
{
    unsetenv("MADEIRA_FAULT_GUARD_GUEST_RIP"); assert(ios_fault_guard_guest_on() == 0);
    ios_fault_guard_guest = -1; setenv("MADEIRA_FAULT_GUARD_GUEST_RIP", "", 1); assert(ios_fault_guard_guest_on() == 0);
    ios_fault_guard_guest = -1; setenv("MADEIRA_FAULT_GUARD_GUEST_RIP", "0", 1); assert(ios_fault_guard_guest_on() == 0);
    ios_fault_guard_guest = -1; setenv("MADEIRA_FAULT_GUARD_GUEST_RIP", "1", 1); assert(ios_fault_guard_guest_on() == 1);
    setenv("MADEIRA_FAULT_GUARD_GUEST_RIP", "0", 1); assert(ios_fault_guard_guest_on() == 1);   /* read once */
    return 0;
}
'''

cc = os.environ.get('CC', 'cc')
flags = ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-variable', '-Wno-unused-function',
         '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')


def build(tmp, name, text):
    path = Path(tmp) / (name + '.c')
    path.write_text(text)
    exe = Path(tmp) / name
    r = subprocess.run([cc] + flags + [str(path), '-o', str(exe)], capture_output=True, text=True)
    check('the production %s compiles on the host' % name, r.returncode == 0, r.stderr[-3000:])
    return exe if r.returncode == 0 else None


def run(exe, *args):
    r = subprocess.run([str(exe)] + list(args), capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-600:]


with tempfile.TemporaryDirectory() as tmp:
    exe = build(tmp, 'switch', switch_harness.replace('@SWITCH@', switch_code))
    if exe:
        good, out = run(exe)
        check('the switch: unset, empty and 0 are off; 1 is on; read once', good, out)

    delivery_text = (redeliv_harness.replace('@SWITCH@', switch_code).replace('@HELPER@', helper)
                     .replace('@CAPTURE@', capture.replace('\n    ', '\n    ')).replace('@GUARD@', guard))
    exe = build(tmp, 'redeliv', delivery_text)
    if exe:
        for state in ('off', 'on'):
            for mode, what in (('progress', 'a run of different guest instructions at the thunk'),
                               ('stuck', 'one guest instruction faulting again and again'),
                               ('native', 'a fault at a pc that is not the thunk (host RIP varies)'),
                               ('unreadable', 'a thunk whose saved RIP cannot be read'),
                               ('reset', 'a new guest instruction after 1999 deliveries'),
                               ('access', 'an access violation delivered at the same pc (guest RIP varies)'),
                               ('other-insn', 'generated code that is not hlt #0 (guest RIP varies)')):
                good, out = run(exe, state, mode)
                expect = ('goes on for 5000 deliveries' if (state == 'on' and mode == 'progress')
                          else 'restarts and ends at 3999' if (state == 'on' and mode == 'reset') else 'ends at the 2000 mark')
                check('switch %s: %s %s' % (state, what, expect), good, out)

    exe = build(tmp, 'pagefault', page_harness.replace('@GUARD@', page_guard))
    if exe:
        for state in ('off', 'on'):
            for mode, what in (('instruction', 'instruction traps at one pc and opcode'), ('memory', 'a real memory loop on one address'),
                               ('advancing', 'faults whose address advances inside one page'), ('reset', 'a loop that moves to another page')):
                good, out = run(exe, state, mode)
                expect = ('are not counted (25000 traps, no abort)' if (state == 'on' and mode == 'instruction')
                          else 'is aborted at the 51st fault' if mode in ('instruction', 'memory') else 'is aborted as before')
                check('switch %s: %s %s' % (state, what, expect), good, out)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
