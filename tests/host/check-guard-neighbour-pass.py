#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_GUARD_NEIGHBOUR_PASS = 1: a store to a committed guest page without a guard bit passes when the guard is its neighbour's; no Wine runs.

Round 707 (agent M4 S7; Half-Life 2 J17QEB, JSJ8EP, C6Z5YZ = round 660's M074): a host page is 16 KB and a guest page 4 KB; get_host_page_vprot ORs the four
guest bytes, so a guard bit on ONE committed guest page (HL2: vprot 0x23,0x31,0x27,0x27) made the whole host page PROT_NONE and a store to page 0, which has no
guard, raised STATUS_GUARD_PAGE_VIOLATION (0x80000001) at tier0, 7-9 s in, every start. With the key (default off, per game) both fault paths (the signal path
virtual_handle_fault and the Mach path ios_virtual_handle_fault_for_thread) ask ios_guard_neighbour_pass first: the guest page touched is committed and has no
guard of its own -> the host page's guard bit is dropped, its protections applied, STATUS_SUCCESS (the access is retried). Its own guard, or an uncommitted
page, keeps the old path. A thread stack's guard is handled before (stack growth).

The production helper and the page-table functions are compiled on the host against a model two-level table. Needs python3 and a C compiler.
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


sigs = ['static BYTE get_page_vprot( const void *addr )',
        'static inline __attribute__((always_inline)) void ios_guard_note( const void *addr, size_t size, char tag )',
        'static void set_page_vprot( const void *addr, size_t size, BYTE vprot )',
        'static void set_page_vprot_bits( const void *addr, size_t size, BYTE set, BYTE clear )',
        'static int ios_guard_neighbour_on( void )', 'static int ios_guard_neighbour_pass( void *addr, char *host_page )']
pieces = {s: function(s) for s in sigs}
ring = native[native.index('#define IOS_GUARD_HIST_N 32'):native.index(sigs[1])]

# wiring: both fault paths ask the helper before raising the violation, outside a thread stack only
for name, head, stack in (('the signal path', 'NTSTATUS virtual_handle_fault( EXCEPTION_RECORD *rec, void *stack )', 'is_inside_thread_stack( page, &stack_info )'),
                          ('the Mach path', 'NTSTATUS ios_virtual_handle_fault_for_thread( EXCEPTION_RECORD *rec, TEB *teb )', 'is_inside_thread_stack_teb( page, &stack_info, teb )')):
    body = function(head)
    g = body[body.index('if (!' + stack + ')'):]
    g = g[:g.index('else ret = grow_thread_stack')]
    check(name + ' asks ios_guard_neighbour_pass first and keeps the violation otherwise, outside a thread stack only',
          g.index('if (ios_guard_neighbour_pass( addr, page )) ret = STATUS_SUCCESS;') < g.index('ret = STATUS_GUARD_PAGE_VIOLATION;'), g)
check('the key is read once, 1 only', 'getenv( "MADEIRA_GUARD_NEIGHBOUR_PASS" )' in pieces[sigs[4]] and "on = e && e[0] == '1' && !e[1];" in pieces[sigs[4]])
check('CI runs this check', 'python3 tests/host/check-guard-neighbour-pass.py' in workflow)

program = r'''
#define _WIN64 1
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char BYTE;
typedef unsigned int ULONG;
typedef uintptr_t ULONG_PTR;
#define VPROT_READ 0x01
#define VPROT_WRITE 0x02
#define VPROT_EXEC 0x04
#define VPROT_GUARD 0x10
#define VPROT_COMMITTED 0x20
static const size_t page_shift = 12, page_size = 0x1000, page_mask = 0xfff, host_page_size = 0x4000, host_page_mask = 0x3fff;
static const size_t pages_vprot_shift = 20, pages_vprot_mask = (1 << 20) - 1;
static size_t pages_vprot_size = 4;
static BYTE *pages_vprot[4];
typedef struct { struct { void *UniqueProcess, *UniqueThread; } ClientId; } TEB;
static TEB the_teb = { { 0, (void *)0xa8 } };
static TEB *NtCurrentTeb( void ) { return &the_teb; }
#define HandleToULong(h) ((unsigned long)(uintptr_t)(h))
static int model_lines, mprotects;
#define dprintf(fd, ...) ((void)(fd), model_lines++)
static int mprotect_range( void *base, size_t size, BYTE set, BYTE clear ) { (void)base; (void)size; (void)set; (void)clear; mprotects++; return 0; }
@RING@
@PIECES@
#define BASE 0x42540000ULL
int main( int argc, char **argv )
{
    int what = atoi( argv[1] );
    char *hp = (char *)(BASE + 0x8000);
    pages_vprot[0] = calloc( 1, (size_t)1 << 20 );
    /* HL2's host page: 0x23 (RW committed), 0x31 (READ|GUARD|COMMITTED), 0x27, 0x27 */
    set_page_vprot( hp, 0x1000, VPROT_READ | VPROT_WRITE | VPROT_COMMITTED );
    set_page_vprot( hp + 0x1000, 0x1000, VPROT_READ | VPROT_GUARD | VPROT_COMMITTED );
    set_page_vprot( hp + 0x2000, 0x2000, VPROT_READ | VPROT_WRITE | VPROT_EXEC | VPROT_COMMITTED );
    void *at = what == 1 ? hp + 0x10 : what == 2 ? hp + 0x1010 : hp + 0x2010;
    if (what == 3) set_page_vprot( hp + 0x2000, 0x1000, VPROT_READ | VPROT_WRITE );   /* touched page not committed */
    int r = ios_guard_neighbour_pass( at, hp );
    printf( "pass=%d v=%02x,%02x,%02x,%02x mprotect=%d lines=%d\n", r, get_page_vprot( hp ), get_page_vprot( hp + 0x1000 ), get_page_vprot( hp + 0x2000 ),
            get_page_vprot( hp + 0x3000 ), mprotects, model_lines );
    return 0;
}
'''
program = program.replace('@RING@', ring).replace('@PIECES@', '\n'.join(pieces[s] for s in sigs))
tmp = Path(tempfile.mkdtemp(prefix='madeira-guard-neighbour-'))
(tmp / 'm.c').write_text(program)
cc = os.environ.get('CC', 'cc')
b = subprocess.run([cc, '-std=gnu11', '-O1', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable', str(tmp / 'm.c'),
                    '-o', str(tmp / 'm')], capture_output=True, text=True)
check('the production helper compiles on the host', b.returncode == 0, b.stdout + b.stderr)
if b.returncode == 0:
    def run(what, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        e.update(env)
        return subprocess.run([str(tmp / 'm'), str(what)], capture_output=True, text=True, env=e).stdout.strip()
    for val in (None, '0', '11', '2'):
        r = run(1, **({} if val is None else {'MADEIRA_GUARD_NEIGHBOUR_PASS': val}))
        check('MADEIRA_GUARD_NEIGHBOUR_PASS=%r: off, nothing changes (the old violation follows)' % (val,), r == 'pass=0 v=23,31,27,27 mprotect=0 lines=0', r)
    r = run(1, MADEIRA_GUARD_NEIGHBOUR_PASS='1')
    check('HL2 with the key: a store to the committed page beside the guard passes, the guard bit leaves the host page, its protections are applied, one line',
          r == 'pass=1 v=23,21,27,27 mprotect=1 lines=1', r)
    r = run(2, MADEIRA_GUARD_NEIGHBOUR_PASS='1')
    check('the guard page itself: the normal path (its one-shot violation)', r == 'pass=0 v=23,31,27,27 mprotect=0 lines=0', r)
    r = run(3, MADEIRA_GUARD_NEIGHBOUR_PASS='1')
    check('a page that is not committed: the normal path', r.startswith('pass=0 ') and 'mprotect=0' in r, r)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
