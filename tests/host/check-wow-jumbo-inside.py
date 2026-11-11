#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 32-bit program's 1 GB reservation outside its window: the [jumbo-wow] line and env.MADEIRA_WOW_JUMBO_INSIDE; no Wine runs.

Night round 2026-11-11, report G6 S7 (The Binding of Isaac: Rebirth, 32-bit, 4QSSTH and 7CQ9J4, build 634): the program reserves 1 GB "for Lua"; the unix side placed it at
0x703ac00000 / 0x370000000, outside the program's 4 GB window (0x7900000000 / 0x400000000), and the program was handed the low 32 bits of that host address. 4QSSTH faulted a
second later at 0x7ac00000 = low32(0x703ac00000) + 1 GB; 7CQ9J4 at a hole of the window. Always (log only): every jumbo request of a 32-bit process prints [jumbo-wow] with the
host address, the window and inside=0|1. With env.MADEIRA_WOW_JUMBO_INSIDE = 1 (default off, per game) an unhinted reserve of 1 GB or more by a 32-bit process, placed outside its
window, is released and answered STATUS_NO_MEMORY; only the program's own NtAllocateVirtualMemory (never the emulator's requests, which come through the Ex entry point).

The two production helpers are compiled on the host; the wiring is read from the source.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(signature):
    start = native.index(signature + '\n{')
    return native[start:native.index('\n}\n', start) + 3]


on_fn = function('static int ios_wow_jumbo_inside_on( void )')
out_fn = function('static int ios_wow_jumbo_outside( ULONG_PTR wb, ULONG_PTR base, SIZE_T size )')

# wiring: the refusal is in the plain entry point only, after the census, unhinted, without address limits
census = function('static void ios_jumbo_census( void *hint, size_t size, void *result, unsigned st )')
check('the census prints [jumbo-wow] for a 32-bit caller after a successful placement, 16 lines at most, with the host address, the window and inside=',
      'if (log_this && result && !st && ios_wow_base())' in census and 'wow_said++ < 16' in census and 'window=[0x%lx,0x%lx) inside=%d guest-visible=0x%08x' in census)
site = 'if (is_jumbo && !st && !jumbo_hint && !limit && ios_wow_jumbo_inside_on() && ios_wow_base() &&'
check('the refusal sits once, right after the census of NtAllocateVirtualMemory, only for an unhinted request without limits, and never in the Ex entry point (the emulator\'s)',
      native.count(site) == 1 and native.count('ios_wow_jumbo_inside_on()') == 1 and
      native.index('if (is_jumbo) ios_jumbo_census( jumbo_hint, jumbo_size, st ? NULL : *ret, (unsigned)st );') < native.index(site) < native.index('/* madeira-bcd: remember a helper\'s large reservation for ios_sc_reap_dead */'))
refuse = native[native.index(site):]
refuse = refuse[:refuse.index('st = STATUS_NO_MEMORY;')]
check('it releases the range, restores the request and answers STATUS_NO_MEMORY', 'NtFreeVirtualMemory( NtCurrentProcess(), &outside, &fs, MEM_RELEASE );' in refuse and
      '*ret = jumbo_hint;' in refuse and '*size_ptr = jumbo_size;' in refuse)
check('CI runs this check', 'python3 tests/host/check-wow-jumbo-inside.py' in workflow)

program = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t ULONG_PTR;
typedef size_t SIZE_T;
#define IOS_WOW_WINDOW_SIZE ((ULONG_PTR)1 << 32)
@FUNCS@
int main( int argc, char **argv )
{
    const ULONG_PTR B = 0x7900000000ULL, B2 = 0x400000000ULL;
    if (argc > 1) { printf( "%d\n", ios_wow_jumbo_inside_on() ); return 0; }
    printf( "%d%d%d%d%d%d%d%d%d\n",
            ios_wow_jumbo_outside( B, 0x703ac00000ULL, 1ULL << 30 ),          /* 4QSSTH: below the window */
            ios_wow_jumbo_outside( B2, 0x370000000ULL, 1ULL << 30 ),          /* 7CQ9J4: below the window */
            ios_wow_jumbo_outside( B, B + 0x10000000ULL, 1ULL << 30 ),        /* inside */
            ios_wow_jumbo_outside( B, B + (1ULL << 32) - (1ULL << 30), 1ULL << 30 ),   /* the last GB: inside */
            ios_wow_jumbo_outside( B, B + (1ULL << 32) - (1ULL << 30) + 0x10000, 1ULL << 30 ),   /* runs past the end */
            ios_wow_jumbo_outside( B, B + (1ULL << 32), 0x10000 ),            /* starts at the end */
            ios_wow_jumbo_outside( B, B - 0x10000, 0x20000 ),                 /* starts just below */
            ios_wow_jumbo_outside( 0, 0x1000, 0x1000 ),                       /* no window (not a 32-bit process): always outside */
            ios_wow_jumbo_outside( B, B, 1ULL << 32 ) );                      /* the whole window */
    return 0;
}
'''
tmp = Path(tempfile.mkdtemp(prefix='madeira-wow-jumbo-'))
(tmp / 'm.c').write_text(program.replace('@FUNCS@', on_fn + '\n' + out_fn))
cc = os.environ.get('CC', 'cc')
b = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', str(tmp / 'm.c'), '-o', str(tmp / 'm')], capture_output=True, text=True)
check('the production helpers compile on the host', b.returncode == 0, b.stdout + b.stderr)
if b.returncode == 0:
    def run(*args, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        e.update(env)
        return subprocess.run([str(tmp / 'm')] + list(args), capture_output=True, text=True, env=e).stdout.strip()

    for val, want in ((None, '0'), ('', '0'), ('0', '0'), ('1', '1'), ('11', '0'), ('2', '0')):
        check('MADEIRA_WOW_JUMBO_INSIDE=%r: %s' % (val, 'on' if want == '1' else 'off'), run('k', **({} if val is None else {'MADEIRA_WOW_JUMBO_INSIDE': val})) == want)
    check('outside: both Isaac placements, a request that runs past the end, one that starts at the end or just below, no window; inside: the first and last GB and the whole window',
          run() == '110011110', run())

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
