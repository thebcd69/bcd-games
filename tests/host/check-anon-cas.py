#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""CAS/CASAL stores into an anonymous RWX alias: emulated atomically (build 580); no Wine runs.

Jetboard Joust (MonoGame on a self-contained CoreCLR; 5QQ3XX, 5WG4FG, N4F8JR, build 578) died on
    [store-undecoded] #1 rev=ml350 insn=0xc8e8ff60 ... alias OK, ADD THIS ENCODING to the case-4 emulator
0xc8e8ff60 is CASAL X8,X0,[X27]: FEX's translation of the x86 `lock cmpxchg` the CLR patches its own code
with, in an RWX allocation whose page is physically RX (the guest-visible write goes through the alias).
The Mach handler's genuinely atomic CAS (ios_mach_emulate_cas) was reached only for the JIT pool's own
views (`in_jit`); the anonymous-alias branch never called it, so the store was undecoded and the
unhandled fault ended the process (exit c000001d after the access violation).

This test compiles ios_mach_emulate_cas from the source and runs it on memory that stands for the alias
(encodings from the AArch64 assembler: casal x8,x0,[x27] = 0xc8e8ff60, cas w1,w2,[x3] = 0x88a17c62,
casa x1,x2,[x3] = 0xc8e17c62, casl w4,wzr,[x5] = 0x88a4fcbf):
  - success: the value at the address becomes Xt and Xs receives the old value; failure: memory is
    unchanged and Xs receives the value that was there (the 64-bit and 32-bit forms; a 32-bit form
    zero-extends Xs and compares only the low 32 bits; XZR as Xt writes 0; XZR as Xs compares with 0 and
    receives nothing);
  - the byte, halfword and pair forms (CASB/CASH/CASP) and every other instruction decline;
  - an unaligned address and x29/x30 decline (the Mach register array holds x0..x28);
  - four threads incrementing one word with CAS lose no update (the emulation is atomic, not read-then-write).
And textually, that the new branch in signal_arm64_ios.c is reached only after every earlier case left the
store undecoded (`!emulated`), only for an anonymous alias (`!in_jit`: the pool case is untouched), only for
a naturally aligned access whose last byte lies in the same alias run as its first, and sits before the
[store-undecoded] report.
Needs python3 and a C compiler.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


# ---------------------------------------------------------------- the emulation, compiled
start = src.index('static int ios_mach_emulate_cas(uint32_t insn, uintptr_t rw_addr, uint64_t gpr[29])')
end = src.index('\n}\n', start) + 3
code = src[start:end]

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <pthread.h>
''' + code + r'''
static int failures;
#define EXPECT(c, msg) do { if (!(c)) { printf("FAIL %s\n", msg); failures++; } } while (0)

static uint64_t g[29];
static uint64_t mem[4] __attribute__((aligned(16)));

typedef struct { uint64_t *p; int n; } job_t;
static void *incr(void *a)
{
    job_t *j = a; uint64_t gpr[29]; memset(gpr, 0, sizeof gpr);
    for (int i = 0; i < j->n; i++)
    {
        for (;;)
        {
            gpr[1] = *(volatile uint64_t *)j->p;          /* Xs = x1: expected */
            gpr[2] = gpr[1] + 1;                           /* Xt = x2: new value */
            uint64_t want = gpr[1];
            if (!ios_mach_emulate_cas(0xc8e17c62u /* casa x1, x2, [x3] */, (uintptr_t)j->p, gpr)) return (void *)1;
            if (gpr[1] == want) break;                     /* Xs still the expected value: it succeeded */
        }
    }
    return NULL;
}

int main(void)
{
    /* casal x8, x0, [x27] = 0xc8e8ff60: Xs = x8, Xt = x0 */
    memset(g, 0, sizeof g); mem[0] = 0x1122334455667788ull;
    g[8] = 0x1122334455667788ull; g[0] = 0xdeadbeefcafef00dull;
    EXPECT(ios_mach_emulate_cas(0xc8e8ff60u, (uintptr_t)&mem[0], g), "casal x8,x0,[x27] is handled");
    EXPECT(mem[0] == 0xdeadbeefcafef00dull, "casal success: memory takes Xt");
    EXPECT(g[8] == 0x1122334455667788ull, "casal success: Xs keeps the old value");
    EXPECT(g[0] == 0xdeadbeefcafef00dull, "casal: Xt is untouched");

    memset(g, 0, sizeof g); mem[0] = 0x55;
    g[8] = 0x66; g[0] = 0x77;
    EXPECT(ios_mach_emulate_cas(0xc8e8ff60u, (uintptr_t)&mem[0], g), "casal mismatch is handled");
    EXPECT(mem[0] == 0x55, "casal mismatch: memory unchanged");
    EXPECT(g[8] == 0x55, "casal mismatch: Xs receives the value that was there");

    /* cas w1, w2, [x3] = 0x88a17c62: 32-bit; only the low 32 bits compare, the rest of the word stays */
    memset(g, 0, sizeof g); mem[1] = 0xaaaaaaaa11111111ull;
    g[1] = 0xffffffff11111111ull; g[2] = 0x22222222ull;     /* high half of Xs is ignored */
    EXPECT(ios_mach_emulate_cas(0x88a17c62u, (uintptr_t)&mem[1], g), "cas w1,w2,[x3]");
    EXPECT(mem[1] == 0xaaaaaaaa22222222ull, "cas w: only the low word is written");
    EXPECT(g[1] == 0x11111111ull, "cas w: Xs is zero-extended old value");
    memset(g, 0, sizeof g); mem[1] = 0xaaaaaaaa11111111ull; g[1] = 0x12345678; g[2] = 0x99;
    EXPECT(ios_mach_emulate_cas(0x88a17c62u, (uintptr_t)&mem[1], g) && mem[1] == 0xaaaaaaaa11111111ull && g[1] == 0x11111111ull,
           "cas w mismatch leaves memory, returns the old word");

    /* casa x1, x2, [x3] = 0xc8e17c62 and casl w4, wzr, [x5] = 0x88a4fcbf (Xt = XZR writes 0) */
    memset(g, 0, sizeof g); mem[2] = 7; g[1] = 7; g[2] = 9;
    EXPECT(ios_mach_emulate_cas(0xc8e17c62u, (uintptr_t)&mem[2], g) && mem[2] == 9 && g[1] == 7, "casa x1,x2");
    memset(g, 0, sizeof g); mem[3] = 0x1234567800000005ull; g[4] = 5;
    EXPECT(ios_mach_emulate_cas(0x88a4fcbfu, (uintptr_t)&mem[3], g) && mem[3] == 0x1234567800000000ull && g[4] == 5,
           "casl w4,wzr: writes zero to the low word");
    /* Xs = XZR (cas wzr, w2, [x3] = 0x88bf7c62): compares with 0, receives nothing */
    memset(g, 0, sizeof g); mem[0] = 0; g[2] = 6;
    EXPECT(ios_mach_emulate_cas(0x88bf7c62u, (uintptr_t)&mem[0], g) && (uint32_t)mem[0] == 6, "cas wzr,w2: XZR compares as 0");
    memset(g, 0, sizeof g); mem[0] = 3; g[2] = 6;
    EXPECT(ios_mach_emulate_cas(0x88bf7c62u, (uintptr_t)&mem[0], g) && (uint32_t)mem[0] == 3, "cas wzr,w2 mismatch");

    /* declines */
    memset(g, 0, sizeof g);
    EXPECT(!ios_mach_emulate_cas(0x08e1fc62u, (uintptr_t)&mem[0], g), "casalb is declined");
    EXPECT(!ios_mach_emulate_cas(0x08a17c62u, (uintptr_t)&mem[0], g), "casb is declined");
    EXPECT(!ios_mach_emulate_cas(0x48e17c62u, (uintptr_t)&mem[0], g), "casah is declined");
    EXPECT(!ios_mach_emulate_cas(0x48207c82u, (uintptr_t)&mem[0], g), "casp x (pair, bit 23 clear) is declined");
    EXPECT(!ios_mach_emulate_cas(0x08207c82u, (uintptr_t)&mem[0], g), "casp w is declined");
    EXPECT(!ios_mach_emulate_cas(0xf8e08000u | (3u << 5), (uintptr_t)&mem[0], g), "swpal is declined");
    EXPECT(!ios_mach_emulate_cas(0xf9000000u, (uintptr_t)&mem[0], g), "str is declined");
    EXPECT(!ios_mach_emulate_cas(0xc8e8ff60u, (uintptr_t)&mem[0] + 4, g), "an unaligned 64-bit address is declined");
    EXPECT(!ios_mach_emulate_cas(0x88a17c62u, (uintptr_t)&mem[0] + 2, g), "an unaligned 32-bit address is declined");
    EXPECT(!ios_mach_emulate_cas(0xc8e8ff60u, 0, g), "a null alias is declined");
    /* casal x29, x0, [x1]: Xs = x29 is not in the Mach array */
    EXPECT(!ios_mach_emulate_cas(0xc8fdfc20u, (uintptr_t)&mem[0], g), "Xs = x29 is declined");
    EXPECT(!ios_mach_emulate_cas(0xc8e0fc3eu, (uintptr_t)&mem[0], g), "Xt = x30 is declined");

    /* four threads, one word, CAS increments: no lost update */
    {
        pthread_t t[4]; job_t j = { &mem[0], 20000 }; int i; void *r;
        mem[0] = 0;
        for (i = 0; i < 4; i++) pthread_create(&t[i], NULL, incr, &j);
        for (i = 0; i < 4; i++) { pthread_join(t[i], &r); EXPECT(r == NULL, "thread ran"); }
        EXPECT(mem[0] == 4ull * 20000, "four threads incrementing with CAS lose no update");
    }
    if (failures) { printf("%d failure(s)\n", failures); return 1; }
    printf("all checks passed\n");
    return 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'anon_cas.c'
    c.write_text(harness)
    exe = Path(tmp) / 'anon_cas'
    cc = subprocess.run(['cc', '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c), '-lpthread'],
                        capture_output=True, text=True)
    check('the emulation compiles on the host', cc.returncode == 0)
    if cc.returncode:
        print(cc.stderr)
    else:
        run = subprocess.run([str(exe)], capture_output=True, text=True)
        for line in run.stdout.splitlines():
            print('     ' + line)
        check('the compiled checks pass', run.returncode == 0)

# ---------------------------------------------------------------- the branch, textually
marker = 'if (!emulated && !in_jit &&\n                        (insn & 0xbfa07c00u) == 0x88a07c00u)'
check('one anonymous-alias CAS branch', src.count(marker) == 1)
b = src.index(marker)
undecoded = src.index('[store-undecoded] #%d rev=ml350', b)
# round 630: the LD<OP>/CAS case (Madeira b7640ef, ported last) sits between this branch and the report; it has its own check
# (tests/host/check-jit-alias-atomics.py), so this one reads up to it
_lse = src.find('/* madeira-bcd (2026-11-03, round 630): LD<OP> and CAS, the other read-modify-write', b)
pool = src.index('if (!emulated && in_jit &&\n                        (insn & 0xbfa07c00u) == 0x88a07c00u)')
check('it comes after the pool CAS case and before the [store-undecoded] report', pool < b < undecoded)
body = src[b:_lse if 0 <= _lse < undecoded else undecoded]
check('naturally aligned access only', '!((uintptr_t)fault_addr & (cas_width - 1))' in body)
check('the last byte must lie in the same alias run as the first',
      'ios_jit_anon_alias_lookup( fault_addr + cas_width - 1 ) == rw_addr + cas_width - 1' in body)
check('it calls the shared atomic emulation and nothing else writes memory',
      'ios_mach_emulate_cas(insn, rw_addr, state.__x)' in body and 'memcpy' not in body and '*(uint' not in body)
check('it logs a capped [anon-cas] line', 'cas_anon_logs++ < 16' in body and '[anon-cas] build580' in body)
check('the pool case is unchanged (still gated on in_jit, same bounds)',
      'cas_inside &&\n                            ios_mach_emulate_cas(insn, rw_addr, state.__x))' in src[pool:b])

print('PASS: anon-alias CAS' if ok else 'FAIL: anon-alias CAS')
raise SystemExit(0 if ok else 1)
