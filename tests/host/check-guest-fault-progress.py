#!/usr/bin/env python3
"""Compile the real Mach fault guard and exercise shared FEX SIGILL deliveries.

Changing guest RIPs at one host thunk must not reach the repeat limit.
A stationary guest fault and an ordinary host fault must still reach it.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
start = source.index('static uint64_t ios_fault_guest_rip(')
helper = source[start:source.index('\n}\n', start) + 2]
start = source.index('static struct { uint64_t thread, key, guest_rip;')
end = source.index('ios_redeliv_terminating = 1;', start) + len('ios_redeliv_terminating = 1;')
guard = source[start:end] + '\n            fatal_hit++;\n        }\n'
# Also run the old identity against the same changing-RIP fixture.
old_guard = guard.replace('thread, key, guest_rip;', 'thread, key;')
old_guard = old_guard.replace(' ||\n            redeliv[rslot].guest_rip != redeliv_guest_rip', '')
old_guard = old_guard.replace('            redeliv[rslot].guest_rip = redeliv_guest_rip;\n', '')
assert old_guard != guard
call = 'redeliv_guest_rip = ios_fault_guest_rip( state, pc );'
assert source.count(call) == 1
capture = source.index(call)
assert source.rfind('if (rec.ExceptionCode == EXCEPTION_ILLEGAL_INSTRUCTION)', 0, capture) > capture - 100
assert source.index('*state = mc.__ss;', capture) > capture
assert 'python3 tests/host/check-guest-fault-progress.py' in (ROOT / '.github/workflows/build-ipa.yml').read_text()

harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct { uint64_t __x[29]; } arm_thread_state64_t;
static const uint64_t jit_pc = 0x160000100ULL, frame_base = 0x7000000000ULL;
static uint32_t instruction = 0xd4400000;
static uint64_t saved_rip;
static int read_pc_ok = 1, read_rip_ok = 1, reads, fatal_hit, warnings;
static int ios_ajan2_pc_is_jit(uint64_t pc) { return pc >= 0x160000000ULL && pc < 0x170000000ULL; }
static int ios_ajan2_read(uint64_t address, void *buffer, size_t length)
{
    reads++;
    if (address == jit_pc && length == 4 && read_pc_ok) { memcpy(buffer, &instruction, 4); return 1; }
    if (address == frame_base + 0x18 && length == 8 && read_rip_ok) { memcpy(buffer, &saved_rip, 8); return 1; }
    return 0;
}
@HELPER@
static int ios_fault_guard_rate_on(void) { return 0; }
static void ios_fgr_run_begin(uint64_t *t0, uint64_t *p0) { *t0 = *p0 = 0; }
static int ios_fgr_say_256(void) { return 1; }
static int ios_fgr_run_alive(uint32_t *n, uint64_t *t0, uint64_t *p0, uint64_t pc, uint64_t addr)
{ (void)n; (void)t0; (void)p0; (void)pc; (void)addr; return 0; }
static void ios_smc_loop_diag(uint64_t thread, uint64_t pc, uint64_t addr, uint32_t code)
{ (void)thread; (void)pc; (void)addr; (void)code; }
#define dprintf(...) ((void)0)
static void delivery(uint64_t thread, uint64_t pc, uint64_t fault_addr, uint64_t redeliv_guest_rip)
{
    struct { uint32_t ExceptionCode; } rec = {0xc000001d};
@GUARD@
}
static void probe_checks(void)
{
    arm_thread_state64_t state = {0}; state.__x[28] = frame_base;
    saved_rip = 0x140001000;
    assert(ios_fault_guest_rip(&state, jit_pc) == saved_rip);
    reads = 0;
    assert(!ios_fault_guest_rip(&state, 0x100001000) && !reads);
    instruction = 0xd503201f;
    assert(!ios_fault_guest_rip(&state, jit_pc)); instruction = 0xd4400000;
    read_pc_ok = 0; assert(!ios_fault_guest_rip(&state, jit_pc)); read_pc_ok = 1;
    read_rip_ok = 0; assert(!ios_fault_guest_rip(&state, jit_pc)); read_rip_ok = 1;
    state.__x[28] = 0x1000; reads = 0;
    assert(!ios_fault_guest_rip(&state, jit_pc) && !reads);
    state.__x[28] = 0x800000000000ULL;
    assert(!ios_fault_guest_rip(&state, jit_pc) && !reads);
}
int main(int argc, char **argv)
{
    assert(argc == 2);
    probe_checks();
    arm_thread_state64_t state = {0}; state.__x[28] = frame_base;
    uint64_t pc = jit_pc;
    int progress = !strcmp(argv[1], "progress");
    int native = !strcmp(argv[1], "native");
    int unreadable = !strcmp(argv[1], "unreadable");
    int reset = !strcmp(argv[1], "reset");
    if (native) pc = 0x100001000;
    if (unreadable) read_rip_ok = 0;
    int delivered = 0;
    while (delivered < 5000 && !fatal_hit)
    {
        saved_rip = 0x140001000 + ((progress || native) ? (delivered % 6) * 7 : 0);
        if (reset && delivered >= 1999) saved_rip += 7;
        uint64_t rip = ios_fault_guest_rip(&state, pc);
        delivery(0x1234, pc, 0xd4400000, rip);
        delivered++;
    }
    if (progress) assert(!fatal_hit && delivered == 5000);
    else if (reset) assert(fatal_hit == 1 && delivered == 3999);
    else assert(fatal_hit == 1 && delivered == 2000);
    printf("PASS %s: %d deliveries, %d terminal decisions\n", argv[1], delivered, fatal_hit);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='bcd-guest-progress-') as temp:
    temp = Path(temp)
    for label, block in (('old', old_guard), ('fixed', guard)):
        unit = temp / (label + '.c')
        unit.write_text(harness.replace('@HELPER@', helper).replace('@GUARD@', block))
        binary = temp / label
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O1', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-Wno-unused-variable', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', str(unit), '-o', str(binary)], check=True)
        for mode in (('progress',) if label == 'old' else ('progress', 'stuck', 'native', 'unreadable', 'reset')):
            result = subprocess.run([str(binary), mode], text=True, capture_output=True,
                                    env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            if label == 'old':
                assert result.returncode != 0 and 'fatal_hit' in result.stderr
                print('PASS: the old guard terminates the changing-guest-RIP fixture')
            else:
                assert result.returncode == 0, result.stdout + result.stderr
                print(result.stdout.strip())
print('PASS: fault-safe probe gates and capture before dispatcher-state replacement')
