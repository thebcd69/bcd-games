#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Anonymous RWX blocks that are adjacent in the guest are adjacent in the pool: env.MADEIRA_ANON_POOL_ADJACENT and the [seam] line (MADEIRA_SEAM_TRACE); no Wine runs.

Night round 2026-11-11, report G9 S1 (Slay the Spire 2, .NET stubs; 13 of 13 logs with the seam signature): the guest-adjacent blocks 0x7192030000 and 0x7192034000 got the pool
offsets 0xaebc000 and 0xaec4000 because the block 0x7192130000 was allocated between them (6VA3V9). A store or a stub that crosses the alias end lands in the next POOL slot,
which is another block's. Two switches, both off by default: MADEIRA_SEAM_TRACE = 1 prints one [seam] line per block that starts where a live alias ends (adjacent=0|1);
MADEIRA_ANON_POOL_ADJACENT = 1 gives an anonymous RWX block of 64 KB or less that does not continue a held run a pool run of up to eight times its size and holds the bytes
after it for the blocks that follow it in the guest, matched by process and guest address and dropped with the process.

Compiled on the host: the production key readers, the hold table, ios_anon_pool_offset and ios_seam_trace_note against a model bump allocator and alias table, run on the
sequence of the log (and a longer interleaved one).
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


sigs = ['static int ios_anon_adjacent_on(void)', 'static int ios_seam_trace_on(void)',
        'static void ios_anon_follow_add( void *peb, uintptr_t next_va, size_t off, size_t left )',
        'static size_t ios_anon_follow_take( void *peb, uintptr_t base, size_t size )', 'static void ios_anon_follow_drop( void *peb )',
        'static size_t ios_anon_pool_offset( void *peb, uintptr_t base, size_t alloc_size, size_t pool_limit )',
        'static void ios_seam_trace_note( uintptr_t base, size_t alloc_size, size_t off )']
pieces = [function(s) for s in sigs]
data_start = native.index('#define IOS_ANON_FOLLOW_MAX 256')
data = native[data_start:native.index('\n', native.index('static pthread_mutex_t ios_anon_follow_lock'))]

# wiring
mp = native[native.index('static inline int mprotect_exec( void *base, size_t size, int unix_prot )'):]
mp = mp[:mp.index('/* madeira-bcd: a resource-only view (ios_map_resource_view)')]
check('the anonymous RWX path takes its pool offset from ios_anon_pool_offset (one ios_pool_alloc_range call without the key) and reports [seam] after the alias is registered',
      'size_t offset = ios_anon_pool_offset( ios_jit_current_peb(), (uintptr_t)base, alloc_size, jit_pool_size - ios_jit_tail_reserved );' in mp and
      mp.index('ios_jit_anon_alias_add(base, alloc_size') < mp.index('ios_seam_trace_note( (uintptr_t)base, alloc_size, offset );') and mp.count('ios_pool_alloc_range(') == 0)
rc = native[native.index('void ios_jit_reclaim_process( void *peb )'):]
check('the holds of an ended process are dropped before its pool ranges are freed', rc.index('ios_anon_follow_drop( peb );') < rc.index('pthread_mutex_lock( &ios_pool_lock );'))
check('CI runs this check', 'python3 tests/host/check-anon-adjacent.py' in workflow)

program = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t ULONG_PTR;
static int model_lines;
static char logbuf[65536];
#define dprintf(fd, ...) ((void)(fd), model_lines++, snprintf( logbuf + strlen( logbuf ), sizeof(logbuf) - strlen( logbuf ), __VA_ARGS__ ))
struct ios_jit_anon_alias { uintptr_t user_va, user_va_end, jit_rw_alias, jit_rx_alias; };
static struct ios_jit_anon_alias ios_jit_anon_aliases[1024];
static volatile int ios_jit_anon_alias_count;
static char fake_rw[1];
void *ios_jit_rw_base_global = fake_rw;
static size_t bump, pool_fail_above = (size_t)-1;
static int alloc_calls;
static size_t ios_pool_alloc_range( size_t alloc_size, size_t pool_limit )
{
    size_t off = bump;
    (void)pool_limit;
    alloc_calls++;
    if (alloc_size > pool_fail_above || off + alloc_size > pool_limit) return (size_t)-1;
    bump += alloc_size;
    return off;
}
@DATA@
@PIECES@
#define POOL 0x27000000ULL
static void add_alias( uintptr_t va, size_t size, size_t off )
{
    ios_jit_anon_aliases[ios_jit_anon_alias_count].user_va = va;
    ios_jit_anon_aliases[ios_jit_anon_alias_count].user_va_end = va + size;
    ios_jit_anon_aliases[ios_jit_anon_alias_count].jit_rw_alias = (uintptr_t)fake_rw + off;
    ios_jit_anon_alias_count++;
}
/* one NEW: the offset, then the alias and the [seam] line as the production path does */
static size_t new_block( void *peb, uintptr_t va, size_t size )
{
    size_t off = ios_anon_pool_offset( peb, va, size, POOL );
    if (off == (size_t)-1) return off;
    add_alias( va, size, off );
    ios_seam_trace_note( va, size, off );
    return off;
}
int main( int argc, char **argv )
{
    int what = atoi( argv[1] ), i;
    void *P = (void *)0x118464000, *Q = (void *)0x118500000;
    size_t offs[16];

    if (what == 0) { printf( "adj=%d seam=%d\n", ios_anon_adjacent_on(), ios_seam_trace_on() ); return 0; }
    if (what == 1)   /* the sequence of 6VA3V9: A, S (elsewhere), B follows A, C follows B, T elsewhere, D follows C */
    {
        uintptr_t va[] = { 0x7192030000ULL, 0x7192130000ULL, 0x7192034000ULL, 0x7192038000ULL, 0x7192230000ULL, 0x719203c000ULL };
        for (i = 0; i < 6; i++) offs[i] = new_block( P, va[i], 0x4000 );
        printf( "calls=%d A=%zx S=%zx B=%zx C=%zx T=%zx D=%zx\n", alloc_calls, offs[0], offs[1], offs[2], offs[3], offs[4], offs[5] );
        printf( "pairs AB=%d BC=%d CD=%d\n", offs[2] == offs[0] + 0x4000, offs[3] == offs[2] + 0x4000, offs[5] == offs[3] + 0x4000 );
        printf( "%s", logbuf );
        return 0;
    }
    if (what == 2)   /* another process at the same guest address does not take P's hold; the dropped hold is gone; a larger block than what is left does not take it */
    {
        size_t a = new_block( P, 0x7192030000ULL, 0x4000 );
        size_t other = new_block( Q, 0x7192034000ULL, 0x4000 );
        size_t big;
        printf( "other_adjacent=%d\n", other == a + 0x4000 );
        ios_anon_follow_drop( P );
        size_t after_drop = new_block( P, 0x7192034000ULL, 0x4000 );
        printf( "after_drop_adjacent=%d\n", after_drop == a + 0x4000 );
        size_t a2 = new_block( P, 0x7193000000ULL, 0x4000 );
        big = new_block( P, 0x7193004000ULL, 0x8000 );   /* 32 KB of the 112 KB held: it fits and takes it */
        printf( "big_adjacent=%d a2=%zx big=%zx\n", big == a2 + 0x4000, a2, big );
        {   /* a hold of 16 KB only (the pool had no room for more): a 32 KB successor does not take it */
            size_t save = pool_fail_above, c, d;
            pool_fail_above = 0x4000;
            ios_anon_follow_add( P, 0x7194000000ULL, 0x100000, 0x4000 );
            c = new_block( P, 0x7194000000ULL, 0x8000 );
            d = c;
            pool_fail_above = save;
            printf( "toolarge_untouched=%d\n", d != 0x100000 );
        }
        return 0;
    }
    if (what == 3)   /* a block of more than 64 KB is not doubled; one of 64 KB is; the pool has no room for 2x: the plain slot */
    {
        size_t b0 = bump, o1, o2;
        o1 = new_block( P, 0x7200000000ULL, 0x20000 );
        printf( "big=%zx used=%zx\n", o1 - b0, bump - b0 );
        b0 = bump;
        o2 = new_block( P, 0x7201000000ULL, 0x10000 );
        printf( "64k=%zx used=%zx\n", o2 - b0, bump - b0 );
        b0 = bump;
        o2 = new_block( P, 0x7201800000ULL, 0x4000 );
        printf( "16k=%zx used=%zx\n", o2 - b0, bump - b0 );
        pool_fail_above = 0x4000;
        b0 = bump;
        o2 = new_block( P, 0x7202000000ULL, 0x4000 );
        printf( "nofit_used=%zx off=%zx\n", bump - b0, o2 - b0 );
        return 0;
    }
    if (what == 4)   /* the table is a ring of 256: old holds are overwritten, never a crash */
    {
        for (i = 0; i < 600; i++) new_block( P, 0x7300000000ULL + (uint64_t)i * 0x100000ULL, 0x4000 );
        printf( "ok calls=%d\n", alloc_calls );
        return 0;
    }
    return 2;
}
'''
program = program.replace('@DATA@', data).replace('@PIECES@', '\n'.join(pieces))
tmp = Path(tempfile.mkdtemp(prefix='madeira-anon-adjacent-'))
(tmp / 'm.c').write_text(program)
cc = os.environ.get('CC', 'cc')
b = subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable', str(tmp / 'm.c'),
                    '-o', str(tmp / 'm'), '-lpthread'], capture_output=True, text=True)
check('the production code compiles on the host', b.returncode == 0, b.stdout + b.stderr)
if b.returncode == 0:
    def run(what, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        e.update(env)
        return subprocess.run([str(tmp / 'm'), str(what)], capture_output=True, text=True, env=e)

    for k, name in (('MADEIRA_ANON_POOL_ADJACENT', 'adj'), ('MADEIRA_SEAM_TRACE', 'seam')):
        for val, want in ((None, 0), ('', 0), ('0', 0), ('1', 1), ('11', 0), ('2', 0)):
            r = run(0, **({} if val is None else {k: val}))
            check('%s=%r: %s' % (k, val, 'on' if want else 'off'), ('%s=%d' % (name, want)) in r.stdout, r.stdout + r.stderr)
    r = run(1)
    check('default: the log sequence is the old bump (one ios_pool_alloc_range call per block): the guest-adjacent pairs A-B and C-D are NOT adjacent in the pool (a block of '
          'elsewhere came between), B-C is by luck, no [seam] line',
          'calls=6 ' in r.stdout and 'pairs AB=0 BC=1 CD=0' in r.stdout and '[seam]' not in r.stdout, r.stdout + r.stderr)
    r = run(1, MADEIRA_SEAM_TRACE='1')
    lines = [l for l in r.stdout.splitlines() if l.startswith('[seam]')]
    check('MADEIRA_SEAM_TRACE=1: the allocation is unchanged and one [seam] line per block that follows a live alias says adjacent=0, 1, 0 for A-B, B-C and C-D',
          'calls=6 ' in r.stdout and 'pairs AB=0 BC=1 CD=0' in r.stdout and len(lines) == 3 and [('adjacent=1' in l) for l in lines] == [False, True, False] and
          'B=0x7192034000+0x4000 follows A=0x7192030000+0x4000' in lines[0], r.stdout + r.stderr)
    r = run(1, MADEIRA_ANON_POOL_ADJACENT='1', MADEIRA_SEAM_TRACE='1')
    lines = [l for l in r.stdout.splitlines() if l.startswith('[seam]')]
    check('MADEIRA_ANON_POOL_ADJACENT=1: every guest successor takes the next held bytes, so A-B, B-C and C-D are adjacent in the pool however many blocks were made elsewhere between '
          'them; the three blocks with no predecessor start a run (three pool allocations); [seam] says adjacent=1 three times',
          'pairs AB=1 BC=1 CD=1' in r.stdout and len(lines) == 3 and all('adjacent=1' in l for l in lines) and 'calls=3 ' in r.stdout, r.stdout + r.stderr)
    r = run(2, MADEIRA_ANON_POOL_ADJACENT='1')
    check('a hold is matched by process and guest address (another process at the same address does not take it), is gone once its process is dropped, a successor larger than the block before takes what it needs of the run, and a successor larger than what is left does not take it',
          'other_adjacent=0' in r.stdout and 'after_drop_adjacent=0' in r.stdout and 'big_adjacent=1' in r.stdout and 'toolarge_untouched=1' in r.stdout, r.stdout + r.stderr)
    r = run(3, MADEIRA_ANON_POOL_ADJACENT='1')
    check('a block above 64 KB gets its own size, one of 64 KB a run of four (256 KB), a 16 KB one a run of eight (128 KB), and when the pool has no room for a run the plain slot is used',
          'big=0 used=20000' in r.stdout and '64k=0 used=40000' in r.stdout and 'nofit_used=4000 off=0' in r.stdout and '16k=0 used=20000' in r.stdout, r.stdout + r.stderr)
    r = run(4, MADEIRA_ANON_POOL_ADJACENT='1')
    check('the hold table is a ring of 256', r.returncode == 0 and r.stdout.startswith('ok'), r.stdout + r.stderr)
    r = run(4, MADEIRA_SEAM_TRACE='1')
    check('the [seam] line stops at 200 lines', r.returncode == 0, r.stdout + r.stderr)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
