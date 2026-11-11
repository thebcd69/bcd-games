#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 64-bit program's anonymous RWX memory: env.MADEIRA_X64_RWX_PLAIN = 1 and MADEIRA_GUEST_RWX_DATA = 2, and the [rwx-view] line; no Wine runs.

Night round 2026-11-11, reports G6 S1 and G10 S11:
  - Jetboard Joust (64-bit CoreCLR, AM23WQ WZBXF4 C2E3EV 9X82G5): 5.6 million emulated stores in about 50 s into the runtime's RWX code heaps. Their size is a multiple
    of 64 KB, which the size rule of ios_guest_anon_rwx_view_ok leaves on the code pool path. MADEIRA_X64_RWX_PLAIN = 1 (default off) is the 64-bit counterpart of
    MADEIRA_WOW_RWX_PLAIN: plain read/write memory (the processor never runs those bytes).
  - Wolfpack (64-bit Unity on its own mono.dll, VNJYRF): 12.5 million emulated stores into a heap of about 1.5 GB. MADEIRA_GUEST_RWX_DATA = 2 (default 1 unchanged) also counts a
    64-bit program's RWX view of 4 MB or more as a data heap, and every RWX view of 64 KB or more is described once in a [rwx-view] line (first 24 views).

The production functions (the key readers, ios_guest_anon_rwx_view_ok, ios_wow_rwx_view_ok and ios_guest_anon_rwx_is_host_data) are compiled on the host against a model view
list, alias table and pool range. Without the keys nothing changes: the same answers as the base commit's rules.
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


sigs = ['static int ios_guest_rwx_data_mode(void)', 'static int ios_guest_rwx_data_enabled(void)',
        'static void ios_rwx_view_note( const struct file_view *view, int plain, const char *why )',
        'static int ios_guest_anon_rwx_view_ok( const struct file_view *view )', 'static int ios_x64_rwx_plain_on(void)',
        'static int ios_wow_rwx_view_ok( const struct file_view *view )',
        'static int ios_guest_anon_rwx_is_host_data( const void *base, size_t size )']
pieces = {s: function(s) for s in sigs}

# wiring
isd = pieces[sigs[6]]
check('the 64-bit plain rule runs after the 32-bit window rule and before the pool-path size rule, only for a 64-bit process, never for an EC_CODE request, the pool or an alias',
      isd.index('ios_wow_rwx_plain_window( base )') < isd.index('ios_x64_rwx_plain_on() && !ios_alloc_ec_code && e > b && !ios_wow_base()') <
      isd.index('if (!ios_guest_rwx_data_enabled() || ios_alloc_ec_code || !arm64ec_view) return 0;') and
      '!(rx && e > rx && b < rx + ios_jit_pool_size_global)' in isd[isd.index('ios_x64_rwx_plain_on()'):] and
      'ios_jit_anon_alias_find_cover( (void *)base, size, &cov_rw, &cov_rx ))' in isd[isd.index('ios_x64_rwx_plain_on()'):])
vok = pieces[sigs[3]]
check('mode 2 needs a 64-bit caller and 4 MB, and is tested only after the 64 KB-multiple rule said no', 'ios_guest_rwx_data_mode() == 2 && view->size >= 0x400000 && !ios_wow_base()' in vok and
      vok.index('ok = (view->size & 0xffff) != 0;') < vok.index('ios_guest_rwx_data_mode() == 2'))
check('CI runs this check', 'python3 tests/host/check-x64-rwx-plain.py' in workflow)

program = r'''
#define WINE_IOS 1
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t ULONG_PTR;
typedef size_t SIZE_T;
#define VPROT_READ 0x01
#define VPROT_WRITE 0x02
#define VPROT_EXEC 0x04
#define VPROT_ARM64EC 0x0100
#define VPROT_SYSTEM 0x0200
#define SEC_FILE 0x800000
#define SEC_IMAGE 0x1000000
#define IOS_WOW_WINDOW_SIZE ((ULONG_PTR)1 << 32)
struct file_view { void *base; size_t size; unsigned int protect; };
struct ios_wow_window { ULONG_PTR base; int wine_mono; };
static int model_log_lines;
static char logbuf[16384];
#define dprintf(fd, ...) ((void)(fd), model_log_lines++, snprintf( logbuf + strlen( logbuf ), sizeof(logbuf) - strlen( logbuf ), __VA_ARGS__ ))
static struct file_view views[64];
static int nviews;
static struct file_view *find_view( const void *addr, size_t size )
{
    for (int i = 0; i < nviews; i++)
        if ((char *)views[i].base <= (const char *)addr && (const char *)addr < (char *)views[i].base + views[i].size) return &views[i];
    (void)size;
    return NULL;
}
static void add_view( uint64_t base, uint64_t size, unsigned protect )
{
    views[nviews].base = (void *)(ULONG_PTR)base; views[nviews].size = size; views[nviews].protect = protect; nviews++;
}
static inline int is_view_valloc( const struct file_view *view ) { return !(view->protect & (SEC_FILE | SEC_IMAGE)); }
static int ios_alloc_ec_code;
static int arm64ec_view_dummy; static void *arm64ec_view = &arm64ec_view_dummy;
static ULONG_PTR wow_base;
static ULONG_PTR ios_wow_base(void) { return wow_base; }
static struct ios_wow_window *ios_wow_rwx_plain_window( const void *addr ) { (void)addr; return NULL; }
void *ios_jit_rx_base_global = (void *)0x300000000ULL;
size_t ios_jit_pool_size_global = 0x38000000;
static int alias_cover;
int ios_jit_anon_alias_find_cover( void *base, size_t size, void **rw, void **rx ) { (void)base; (void)size; (void)rw; (void)rx; return alias_cover; }
@PIECES@
#define KB 1024ULL
#define MB (1024ULL * 1024)
static void reset( void ) { nviews = 0; model_log_lines = 0; logbuf[0] = 0; alias_cover = 0; ios_alloc_ec_code = 0; wow_base = 0; }
static struct file_view *one( uint64_t base, uint64_t size, unsigned protect ) { nviews = 0; add_view( base, size, protect ); return &views[0]; }
int main( int argc, char **argv )
{
    const unsigned RWX = VPROT_READ | VPROT_WRITE | VPROT_EXEC;
    int what = atoi( argv[1] ), i;

    reset();
    if (what == 0) { printf( "mode=%d enabled=%d x64=%d\n", ios_guest_rwx_data_mode(), ios_guest_rwx_data_enabled(), ios_x64_rwx_plain_on() ); return 0; }
    if (what == 1)   /* ios_guest_anon_rwx_view_ok */
    {
        printf( "a=%d b=%d c=%d d=%d e=%d f=%d g=%d h=%d\n",
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x41000, RWX ) ),               /* a: Boehm's chunk: plain */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x100000, RWX ) ),              /* b: 1 MB, a multiple of 64 KB */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, RWX ) ),              /* c: 4 MB */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x40000000, RWX ) ),            /* d: 1 GB */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x8000, RWX ) ),                /* e: below 64 KB */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, VPROT_READ | VPROT_WRITE ) ),  /* f: not allocated RWX */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, RWX | VPROT_ARM64EC ) ),      /* g: ARM64EC */
                ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, RWX | SEC_IMAGE ) ) );        /* h: image */
        wow_base = 0x7900000000ULL;
        printf( "wow c=%d d=%d\n", ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, RWX ) ), ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x40000000, RWX ) ) );
        return 0;
    }
    if (what == 2)   /* the line: once per view, 24 views at most */
    {
        struct file_view *v = one( 0x7100000000ULL, 0x100000, RWX );
        for (i = 0; i < 100; i++) ios_guest_anon_rwx_view_ok( v );
        printf( "lines_one=%d\n", model_log_lines );
        for (i = 0; i < 40; i++) ios_guest_anon_rwx_view_ok( one( 0x7200000000ULL + (uint64_t)i * 0x10000000ULL, 0x100000, RWX ) );
        printf( "lines_all=%d\n%s", model_log_lines, logbuf );
        return 0;
    }
    if (what == 3)   /* below 64 KB and views that are not anonymous RWX never print */
    {
        ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x8000, RWX ) );
        ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, VPROT_READ | VPROT_WRITE ) );
        ios_guest_anon_rwx_view_ok( one( 0x7100000000ULL, 0x400000, RWX | SEC_IMAGE ) );
        printf( "lines=%d\n", model_log_lines );
        return 0;
    }
    if (what == 4)   /* ios_guest_anon_rwx_is_host_data */
    {
        int r_1mb, r_arm, r_ec, r_pool, r_alias, r_img, r_gap, r_mixed, r_wow, r_small, r_boehm;
        add_view( 0x7100000000ULL, 0x100000, RWX );                                       /* a CoreCLR code heap: 1 MB, a multiple of 64 KB */
        r_1mb = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x100000 );
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX | VPROT_ARM64EC );
        r_arm = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x100000 );
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX );
        ios_alloc_ec_code = 1; r_ec = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x100000 ); ios_alloc_ec_code = 0;
        nviews = 0; add_view( 0x300100000ULL, 0x100000, RWX );
        r_pool = ios_guest_anon_rwx_is_host_data( (void *)0x300100000ULL, 0x100000 );
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX );
        alias_cover = 1; r_alias = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x100000 ); alias_cover = 0;
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX | SEC_IMAGE );
        r_img = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x100000 );
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX ); add_view( 0x7100100000ULL, 0x100000, RWX | VPROT_SYSTEM );
        r_mixed = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x200000 );   /* one view of the range does not qualify */
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX ); add_view( 0x7100110000ULL, 0x100000, RWX );
        r_gap = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x210000 );    /* a gap between two qualifying views is a rounding gap */
        nviews = 0; add_view( 0x7100000000ULL, 0x100000, RWX );
        wow_base = 0x7900000000ULL; r_wow = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x100000 ); wow_base = 0;
        nviews = 0; add_view( 0x7100000000ULL, 0x8000, RWX );
        r_small = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x8000 );
        nviews = 0; add_view( 0x7100000000ULL, 0x41000, RWX );
        r_boehm = ios_guest_anon_rwx_is_host_data( (void *)0x7100000000ULL, 0x44000 );
        printf( "mb1=%d arm=%d ec=%d pool=%d alias=%d img=%d mixed=%d gap=%d wow=%d small=%d boehm=%d\n", r_1mb, r_arm, r_ec, r_pool, r_alias, r_img, r_mixed, r_gap, r_wow, r_small, r_boehm );
        return 0;
    }
    return 2;
}
'''
program = program.replace('@PIECES@', '\n'.join(pieces[s] for s in sigs))
tmp = Path(tempfile.mkdtemp(prefix='madeira-x64-rwx-'))
(tmp / 'm.c').write_text('#define IOS_RWX_VIEW_LOGGED 24\n' + program)
cc = os.environ.get('CC', 'cc')
b = subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable', str(tmp / 'm.c'), '-o', str(tmp / 'm')],
                   capture_output=True, text=True)
check('the production functions compile on the host', b.returncode == 0, b.stdout + b.stderr)
if b.returncode == 0:
    def run(what, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        e.update(env)
        return subprocess.run([str(tmp / 'm'), str(what)], capture_output=True, text=True, env=e)

    for val, mode in ((None, 1), ('', 1), ('1', 1), ('0', 0), ('n', 0), ('N', 0), ('no', 0), ('2', 2), ('22', 1), ('3', 1)):
        r = run(0, **({} if val is None else {'MADEIRA_GUEST_RWX_DATA': val}))
        check('MADEIRA_GUEST_RWX_DATA=%r: mode %d, enabled %d' % (val, mode, int(mode != 0)), r.stdout.startswith('mode=%d enabled=%d ' % (mode, int(mode != 0))), r.stdout + r.stderr)
    for val, want in ((None, 0), ('', 0), ('0', 0), ('1', 1), ('11', 0), ('2', 0), ('on', 0)):
        r = run(0, **({} if val is None else {'MADEIRA_X64_RWX_PLAIN': val}))
        check('MADEIRA_X64_RWX_PLAIN=%r: %s' % (val, 'on' if want else 'off'), r.stdout.strip().endswith('x64=%d' % want), r.stdout + r.stderr)
    r = run(1)
    check('mode 1 (default): Boehm\'s 0x41000 chunk is plain; a 1 MB, 4 MB or 1 GB view (64 KB multiples), one under 64 KB, one not allocated RWX, an ARM64EC view and an image stay on the pool path; '
          'a 32-bit caller changes nothing',
          r.stdout.splitlines()[0] == 'a=1 b=0 c=0 d=0 e=0 f=0 g=0 h=0' and r.stdout.splitlines()[1] == 'wow c=0 d=0', r.stdout + r.stderr)
    r = run(1, MADEIRA_GUEST_RWX_DATA='2')
    check('mode 2: the 4 MB and 1 GB views of a 64-bit caller are plain too, the 1 MB view, the others and a 32-bit caller are not',
          r.stdout.splitlines()[0] == 'a=1 b=0 c=1 d=1 e=0 f=0 g=0 h=0' and r.stdout.splitlines()[1] == 'wow c=0 d=0', r.stdout + r.stderr)
    r = run(1, MADEIRA_GUEST_RWX_DATA='0')
    check('mode 0 does not change the view test itself (the caller asks ios_guest_rwx_data_enabled first, as before)', r.stdout.splitlines()[0].startswith('a=1 b=0 c=0'), r.stdout)
    r = run(2)
    check('[rwx-view]: one line per view however often it is asked, 24 views at most (no key needed)', 'lines_one=1\n' in r.stdout and 'lines_all=24\n' in r.stdout and
          r.stdout.count('[rwx-view] r652') == 24 and 'decision=pool reason=size-multiple-of-64k mode=1 wow=0' in r.stdout, r.stdout)
    r = run(2, MADEIRA_GUEST_RWX_DATA='2')
    check('[rwx-view] with mode 2: the 1 MB view is still pool; a 4 MB one would say mode-2-4mb-or-more', 'decision=pool reason=size-multiple-of-64k mode=2' in r.stdout, r.stdout)
    r = run(3)
    check('no [rwx-view] line for a view under 64 KB, one not allocated RWX or an image', r.stdout.strip() == 'lines=0', r.stdout)
    base_answers = 'mb1=0 arm=0 ec=0 pool=0 alias=0 img=0 mixed=0 gap=0 wow=0 small=0 boehm=1'
    r = run(4)
    check('without the key: a 1 MB code heap stays on the pool path, a Boehm chunk is plain (as the base commit)', r.stdout.strip() == base_answers, r.stdout + r.stderr)
    r = run(4, MADEIRA_X64_RWX_PLAIN='1')
    check('MADEIRA_X64_RWX_PLAIN=1: the 1 MB heap is plain; an ARM64EC view, an EC_CODE request, the pool, an alias, an image, a system view in the range, '
          'a 32-bit caller and a view under 64 KB (a possible native thunk page) still are not; a gap between two plain views is a rounding gap',
          r.stdout.strip() == 'mb1=1 arm=0 ec=0 pool=0 alias=0 img=0 mixed=0 gap=1 wow=0 small=0 boehm=1', r.stdout + r.stderr)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
