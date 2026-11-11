#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A committed page that reads back unreadable: [commit-noaccess] names the four guest-page bytes and the last guard event, and env.MADEIRA_COMMIT_GUARD_CLEAR; no Wine runs.

Night round 2026-11-11, report G5 S9 (Half-Life 2 with CEF, FKB2GS and SXSYQN, iPad15,7, build 634): a MEM_COMMIT of 0xf5000 bytes at base+0xa000 of a just-released 2 MB block returned
success and the host page at its start was PROT_NONE (host_page_vprot=0x37: READ|WRITE|EXEC|GUARD|COMMITTED); the first store raised STATUS_GUARD_PAGE_VIOLATION and the game
died 9 s after the start, every time. A host page is 16 KB and a guest page 4 KB; get_unix_prot takes the OR of the four guest pages' bytes, so a guard bit on any of them
turns the whole host page PROT_NONE. The origin of the bit is unknown (no PAGE_GUARD request and no thread stack in the log).

Always (log only): the two writers of the page tables (set_page_vprot, set_page_vprot_bits) note every time they set a guard bit (32-entry ring: range, writer, thread, guest RIP);
the [commit-noaccess] line prints vprot4=aa,bb,cc,dd and guard-hist=B#seq@tidXXXX pc=0x..., and the line is followed by the view history (ios_vh_dump).
env.MADEIRA_COMMIT_GUARD_CLEAR = 1 (default off, per game): before a MEM_COMMIT without PAGE_GUARD, a guard bit on a guest page of the same host page that is OUTSIDE the commit and
NOT committed is cleared (PAGE_GUARD means nothing for an uncommitted page); a committed page's bit stays.

The production functions are compiled on the host against a model two-level page table.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
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


sigs = ['static BYTE get_page_vprot( const void *addr )', 'static inline __attribute__((always_inline)) void ios_guard_note( const void *addr, size_t size, char tag )',
        'static void set_page_vprot( const void *addr, size_t size, BYTE vprot )',
        'static void set_page_vprot_bits( const void *addr, size_t size, BYTE set, BYTE clear )',
        'static void ios_describe_host_vprot( const void *addr, char *out, size_t out_size )',
        'static int ios_commit_guard_clear_on(void)', 'static void ios_commit_guard_clear( const void *base, size_t size, ULONG protect )']
pieces = {s: function(s) for s in sigs}
ring = native[native.index('#define IOS_GUARD_HIST_N 32'):native.index('static inline __attribute__((always_inline)) void ios_guard_note( const void *addr, size_t size, char tag )')]

# wiring
na = native[native.index('        char vp4[96];\n'):]
na = na[:na.index('return;')]
check('[commit-noaccess] prints vprot4= from ios_describe_host_vprot and is followed by the view history', 'ios_describe_host_vprot( base, vp4, sizeof(vp4) );' in na and 'recommit=%lu rev=ml981 vprot4=%s' in na and 'ios_vh_dump( base );' in na)
commit = native[native.index('        else if (ios_commit_guard_clear( base, size, protect ), !(status = set_protection( view, base, size, protect )))'):]
check('the experiment runs in the commit branch right before set_protection (the key is tested inside)', commit.index('ios_commit_guard_clear( base, size, protect )') < commit.index('set_protection( view, base, size, protect )'))
check('both writers of the page tables note a guard bit', "if (vprot & VPROT_GUARD) ios_guard_note( addr, size, 'S' );" in pieces[sigs[2]] and
      "if (set & VPROT_GUARD) ios_guard_note( addr, size, 'B' );" in pieces[sigs[3]])
check('CI runs this check', 'python3 tests/host/check-guard-hist.py' in workflow)

program = r'''
#define _WIN64 1
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char BYTE;
typedef unsigned int ULONG;
typedef uint64_t ULONGLONG;
#define VPROT_READ 0x01
#define VPROT_WRITE 0x02
#define VPROT_EXEC 0x04
#define VPROT_GUARD 0x10
#define VPROT_COMMITTED 0x20
#define PAGE_GUARD 0x100
#define PAGE_READWRITE 0x04
#define ROUND_ADDR(addr,mask) ((void *)((ULONG_PTR)(addr) & ~(ULONG_PTR)(mask)))
typedef uintptr_t ULONG_PTR;
static const size_t page_shift = 12, page_size = 0x1000, page_mask = 0xfff, host_page_size = 0x4000, host_page_mask = 0x3fff;
static const size_t pages_vprot_shift = 20, pages_vprot_mask = (1 << 20) - 1;
static size_t pages_vprot_size = 4;
static BYTE *pages_vprot[4];
typedef struct { struct { void *UniqueProcess, *UniqueThread; } ClientId; } TEB;
static TEB the_teb = { { 0, (void *)0xa8 } };
static TEB *NtCurrentTeb( void ) { return &the_teb; }
#define HandleToULong(h) ((unsigned long)(uintptr_t)(h))
static int model_lines;
static char logbuf[8192];
#define dprintf(fd, ...) ((void)(fd), model_lines++, snprintf( logbuf + strlen( logbuf ), sizeof(logbuf) - strlen( logbuf ), __VA_ARGS__ ))
@RING@
@PIECES@
/* addresses 0x42540000...: page index 0x42540 >> 20 = 0 (the first block of the table) */
#define BASE 0x42540000ULL
int main( int argc, char **argv )
{
    int what = atoi( argv[1] );
    char out[160];
    pages_vprot[0] = calloc( 1, (size_t)1 << 20 );
    if (what == 0) { printf( "on=%d\n", ios_commit_guard_clear_on() ); return 0; }
    /* the host page at BASE + 0x8000: guest pages 0x8000 (committed guard? no), 0x9000, 0xa000, 0xb000. HL2: the commit is base+0xa000 size 0xf5000 */
    set_page_vprot( (void *)(BASE + 0x8000), 0x1000, VPROT_READ | VPROT_WRITE | VPROT_GUARD );              /* a stale guard bit, not committed */
    set_page_vprot( (void *)(BASE + 0x9000), 0x1000, 0 );
    if (what == 1)   /* the ring and the description */
    {
        set_page_vprot_bits( (void *)(BASE + 0x2000), 0x1000, VPROT_GUARD, 0 );                              /* an older guard event elsewhere */
        set_page_vprot( (void *)(BASE + 0x8000), 0x2000, VPROT_READ | VPROT_GUARD );                        /* the one that matters */
        set_page_vprot_bits( (void *)(BASE + 0x2000), 0x1000, 0, VPROT_GUARD );                              /* clearing is not an event */
        set_page_vprot( (void *)(BASE + 0xa000), 0x2000, VPROT_READ | VPROT_WRITE | VPROT_COMMITTED );
        ios_describe_host_vprot( (void *)(BASE + 0xa000), out, sizeof(out) );
        printf( "%s\n", out );
        ios_describe_host_vprot( (void *)(BASE + 0x40000), out, sizeof(out) );   /* a host page nobody set: 00,00,00,00 and no event */
        printf( "%s\n", out );
        ios_describe_host_vprot( (void *)(BASE + 0x2000), out, sizeof(out) );    /* the older event's page */
        printf( "%s\n", out );
        return 0;
    }
    if (what == 2)   /* the experiment */
    {
        /* HL2: the commit starts at +0xa000 (inside host page +0x8000..+0xbfff); +0x8000 carries a stale guard bit and is not committed */
        ios_commit_guard_clear( (void *)(BASE + 0xa000), 0xf5000, PAGE_READWRITE );
        printf( "after: p8=%02x p9=%02x lines=%d\n", get_page_vprot( (void *)(BASE + 0x8000) ), get_page_vprot( (void *)(BASE + 0x9000) ), model_lines );
        /* a committed guard page beside a commit stays; a PAGE_GUARD commit changes nothing; a commit that is aligned to the host page has no neighbours */
        set_page_vprot( (void *)(BASE + 0x100000), 0x1000, VPROT_READ | VPROT_COMMITTED | VPROT_GUARD );
        ios_commit_guard_clear( (void *)(BASE + 0x101000), 0x1000, PAGE_READWRITE );
        printf( "committed_guard=%02x\n", get_page_vprot( (void *)(BASE + 0x100000) ) );
        set_page_vprot( (void *)(BASE + 0x200000), 0x1000, VPROT_READ | VPROT_GUARD );
        ios_commit_guard_clear( (void *)(BASE + 0x201000), 0x1000, PAGE_READWRITE | PAGE_GUARD );
        printf( "guard_request=%02x\n", get_page_vprot( (void *)(BASE + 0x200000) ) );
        set_page_vprot( (void *)(BASE + 0x300000), 0x1000, VPROT_READ | VPROT_GUARD );
        ios_commit_guard_clear( (void *)(BASE + 0x304000), 0x4000, PAGE_READWRITE );
        printf( "other_host_page=%02x\n", get_page_vprot( (void *)(BASE + 0x300000) ) );
        ios_commit_guard_clear( (void *)(BASE + 0x400000), 0, PAGE_READWRITE );
        return 0;
    }
    return 2;
}
'''
program = program.replace('@RING@', ring).replace('@PIECES@', '\n'.join(pieces[s] for s in sigs))
tmp = Path(tempfile.mkdtemp(prefix='madeira-guard-hist-'))
(tmp / 'm.c').write_text(program)
cc = os.environ.get('CC', 'cc')
b = subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable', '-Wno-pointer-arith', str(tmp / 'm.c'), '-o', str(tmp / 'm')],
                   capture_output=True, text=True)
check('the production functions compile on the host', b.returncode == 0, b.stdout + b.stderr)
if b.returncode == 0:
    def run(what, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        e.update(env)
        return subprocess.run([str(tmp / 'm'), str(what)], capture_output=True, text=True, env=e)

    for val, want in ((None, 0), ('', 0), ('0', 0), ('1', 1), ('11', 0), ('2', 0)):
        r = run(0, **({} if val is None else {'MADEIRA_COMMIT_GUARD_CLEAR': val}))
        check('MADEIRA_COMMIT_GUARD_CLEAR=%r: %s' % (val, 'on' if want else 'off'), r.stdout.strip() == 'on=%d' % want, r.stdout + r.stderr)
    r = run(1)
    lines = r.stdout.splitlines()
    check('the description gives the four guest-page bytes of the host page and the last guard event that touched it (writer B or S, sequence, thread, the host pc of the site that asked)',
          len(lines) == 3 and re.fullmatch(r'11,11,23,23 guard-hist=S#3@tid00a8 pc=0x[0-9a-f]+', lines[0]), r.stdout + r.stderr)
    check('a host page nobody touched has zero bytes and no event; an older event is found on its own page (its bit was cleared again: clearing is not an event)',
          len(lines) == 3 and lines[1] == '00,00,00,00 guard-hist=none' and re.fullmatch(r'00,00,00,00 guard-hist=B#2@tid00a8 pc=0x[0-9a-f]+', lines[2]), r.stdout + r.stderr)
    r = run(2)
    check('without the key nothing is cleared', 'after: p8=13 p9=00 lines=0' in r.stdout, r.stdout + r.stderr)
    r = run(2, MADEIRA_COMMIT_GUARD_CLEAR='1')
    out = r.stdout.splitlines()
    check('HL2 shape with the key: the stale guard bit of the uncommitted neighbour is cleared (one line), the other neighbour is untouched',
          out and out[0] == 'after: p8=03 p9=00 lines=1', r.stdout + r.stderr)
    check('a committed guard page beside a commit stays, a PAGE_GUARD commit changes nothing, a page of another host page is not looked at, a zero-size commit does nothing',
          'committed_guard=31' in r.stdout and 'guard_request=11' in r.stdout and 'other_host_page=11' in r.stdout, r.stdout + r.stderr)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
