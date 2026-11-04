#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_ANON_CAS_FLUSH = 1: a CAS on an anonymous RWX alias goes to FEX, which drops its translation of the page (build 591); no Wine runs.

Build 580 emulates CAS/CASAL on an anonymous alias in the Mach handler (Jetboard Joust, MonoGame on CoreCLR). Four sessions on a 7.5 GB
iPad (GHVYNG, T9CQD7, RTSYP6, CTR7BV) then show exactly ONE emulated CAS, and ~190 log lines later the CLR main thread calls one routine
11-12 million times a second and never presents (zero frames at 30 s and 60 s). On build 579 the same store reached FEX: "Handled
self-modifying code" (HandleRWXAccessViolation drops the blocks of the page), "[atomic-anon] ml656 anon RX->RW", "[smc-atomic] ml657
handled" (FEX performs the CASAL through the alias), 16 times in 5QQ3XX, and the runtime went on to load clrjit.dll. The Mach thread cannot
call into the PE-side emulator, and FEX has no entry that a native thread could ask for an invalidation, so the only way FEX learns of the
write is to see the fault. With the switch the branch does not emulate: the fault is delivered to the guest as before build 580, for the
same stores (an anonymous alias, natural alignment, the whole word in one alias run) and no others.

The branch is cut out of signal_arm64_ios.c and compiled on the host with the production CAS emulation and fakes:
  - switch off: the CAS is emulated as in build 580 (memory takes the new value, the register gets the old one, emulated = 1);
  - switch on: nothing is written, emulated stays 0, the hand-off flag is set, no [store-undecoded] line would print;
  - unaligned or an alias run that ends inside the word: neither happens, with or without the switch (the old fall-through);
  - the JIT pool's own CAS case is not in this branch.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
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
check('the switch is read once, by name', src.count('getenv( "MADEIRA_ANON_CAS_FLUSH" )') == 1)
marker = 'if (!emulated && !in_jit &&\n                        (insn & 0xbfa07c00u) == 0x88a07c00u)'
b = src.index(marker)
undecoded = src.index('[store-undecoded] #%d rev=ml350', b)
# round 630: the LD<OP>/CAS case (Madeira b7640ef, ported last) sits between this branch and the report; it has its own check
# (tests/host/check-jit-alias-atomics.py), so this one reads up to it
_lse = src.find('/* madeira-bcd (2026-11-03, round 630): LD<OP> and CAS, the other read-modify-write', b)
body = src[b:_lse if 0 <= _lse < undecoded else undecoded]
cut = body[:body.index('/* ml350 DISCRIMINATOR')] if '/* ml350 DISCRIMINATOR' in body else body
check('the hand-off is inside the anonymous-alias CAS branch (not the pool case), with the emulation\'s own conditions',
      'if (ios_anon_cas_flush_on() && !((uintptr_t)fault_addr & (cas_width - 1)) &&\n'
      '                            ios_jit_anon_alias_lookup( fault_addr + cas_width - 1 ) == rw_addr + cas_width - 1)' in cut)
check('the emulation runs only when the hand-off did not', 'if (!anon_cas_handoff && !((uintptr_t)fault_addr & (cas_width - 1)) &&' in cut
      and 'ios_mach_emulate_cas(insn, rw_addr, state.__x)' in cut)
h0 = cut.index('anon_cas_handoff = 1;')
h1 = cut.index('if (!anon_cas_handoff && !((uintptr_t)fault_addr')
hand = cut[h0:h1]
check('the hand-off writes nothing and emulates nothing', 'ios_mach_emulate_cas' not in hand and 'emulated = 1' not in hand and 'memcpy' not in hand
      and '__atomic' not in hand)
check('the hand-off logs a capped [anon-cas] build591 line', 'cas_flush_logs++ < 16' in hand and '[anon-cas] build591 MADEIRA_ANON_CAS_FLUSH' in hand)
check('[store-undecoded] is not printed for a hand-off', 'if (!emulated && !anon_cas_handoff)\n                    {\n                        static int undecoded_n;' in src)
pool = src.index('if (!emulated && in_jit &&\n                        (insn & 0xbfa07c00u) == 0x88a07c00u)')
check('the pool CAS case does not look at the switch', 'ios_anon_cas_flush_on' not in src[pool:b])

# ---------------------------------------------------------------- compiled branch
cas = src[src.index('static int ios_mach_emulate_cas(uint32_t insn, uintptr_t rw_addr, uint64_t gpr[29])'):]
cas = cas[:cas.index('\n}\n') + 3]
branch = cut

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define STDERR_FILENO 2
#define dprintf(fd, ...) do { logged++; } while (0)
static int logged, flush_on, note_handed, note_emulated;
static int ios_anon_cas_flush_on(void) { return flush_on; }
static void ios_anon_cas_note(unsigned long long a, int handed) { (void)a; if (handed) note_handed++; else note_emulated++; }
static uintptr_t alias_end_mismatch;   /* make the last-byte lookup disagree */
static uint64_t mem[4] __attribute__((aligned(16)));
static uintptr_t ios_jit_anon_alias_lookup(uintptr_t a) { return alias_end_mismatch ? 0 : (uintptr_t)mem + (a - 0x7193a44510ull); }
''' + cas + r'''
struct { uint64_t __x[29]; } state;
static void run(uint32_t insn, uintptr_t fault_addr, int *emulated_out, int *handoff_out)
{
    int emulated = 0, in_jit = 0, anon_cas_handoff = 0;
    uintptr_t rw_addr = (uintptr_t)mem + (fault_addr - 0x7193a44510ull);
    uintptr_t fault_pc = 0x13d89a1d4;
    @BRANCH@
    *emulated_out = emulated; *handoff_out = anon_cas_handoff;
}

int main(void)
{
    int e, h, fail = 0;
    uint32_t casal = 0xc8e8ff60u;   /* casal x8, x0, [x27] */
#define CHECK(c, m) do { if (c) printf("ok   %s\n", m); else { printf("FAIL %s\n", m); fail++; } } while (0)

    /* switch off: emulated, as in build 580 */
    flush_on = 0; logged = 0; note_emulated = note_handed = 0; alias_end_mismatch = 0;
    mem[0] = 0x1122334455667788ull; memset(&state, 0, sizeof state); state.__x[8] = mem[0]; state.__x[0] = 0xdeadbeefcafef00dull;
    run(casal, 0x7193a44510ull, &e, &h);   /* 16-aligned guest address: rw_addr = mem */
    CHECK(e == 1 && h == 0 && mem[0] == 0xdeadbeefcafef00dull && state.__x[8] == 0x1122334455667788ull, "off: the CAS is emulated, memory takes the new value, x8 the old one");
    CHECK(note_emulated == 1 && note_handed == 0 && logged == 1, "off: counted as emulated, one [anon-cas] build580 line");

    /* switch on: handed to FEX, nothing written */
    flush_on = 1; logged = 0; note_emulated = note_handed = 0;
    mem[0] = 0x1122334455667788ull; memset(&state, 0, sizeof state); state.__x[8] = mem[0]; state.__x[0] = 0xdeadbeefcafef00dull;
    run(casal, 0x7193a44510ull, &e, &h);
    CHECK(e == 0 && h == 1 && mem[0] == 0x1122334455667788ull && state.__x[8] == 0x1122334455667788ull && state.__x[0] == 0xdeadbeefcafef00dull,
          "on: the CAS is not emulated (emulated 0, hand-off 1), memory and registers untouched");
    CHECK(note_handed == 1 && note_emulated == 0 && logged == 1, "on: counted as handed, one [anon-cas] build591 line");

    /* 32-bit form: cas w1, w2, [x3] = 0x88a17c62 */
    flush_on = 1; mem[1] = 5;
    run(0x88a17c62u, 0x7193a44518ull, &e, &h);
    CHECK(e == 0 && h == 1, "on: the 32-bit CAS is handed over too");
    flush_on = 0; memset(&state, 0, sizeof state); state.__x[1] = 5; state.__x[2] = 9; mem[1] = 5;
    run(0x88a17c62u, 0x7193a44518ull, &e, &h);
    CHECK(e == 1 && h == 0 && (uint32_t)mem[1] == 9, "off: the 32-bit CAS is emulated");

    /* unaligned: neither, with or without the switch */
    for (flush_on = 0; flush_on <= 1; flush_on++)
    {
        memset(&state, 0, sizeof state); mem[0] = 1; logged = 0;
        run(casal, 0x7193a44514ull, &e, &h);
        CHECK(e == 0 && h == 0 && logged == 0, flush_on ? "on: an unaligned CAS is neither emulated nor handed over (the old fall-through)" : "off: an unaligned CAS is not emulated (the old fall-through)");
    }

    /* the alias ends inside the word: neither */
    alias_end_mismatch = 1;
    for (flush_on = 0; flush_on <= 1; flush_on++)
    {
        run(casal, 0x7193a44510ull, &e, &h);
        CHECK(e == 0 && h == 0, flush_on ? "on: an alias run that ends inside the word is not handed over" : "off: an alias run that ends inside the word is not emulated");
    }
    alias_end_mismatch = 0;

    /* an instruction that is not a CAS never reaches the branch's body */
    for (flush_on = 0; flush_on <= 1; flush_on++)
    {
        run(0xf9000000u, 0x7193a44510ull, &e, &h);
        CHECK(e == 0 && h == 0, "a store that is not a CAS is left alone");
    }
    return fail != 0;
}
'''.replace('@BRANCH@', branch)

with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wno-implicit-function-declaration', '-Wno-unused-variable', '-o', str(exe), str(c), '-lpthread'],
                        capture_output=True, text=True)
    check('the production branch compiles on the host', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            check(line[5:], line.startswith('ok'))
        check('every compiled scenario passes', r.returncode == 0, r.stdout[-800:])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
