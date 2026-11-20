#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The image sync of NtProtectVirtualMemory skips the lookup of a word that lies outside the span of the mapped images, and rewrites,
keeps and counts exactly the words it did before (virtual_ios.c: ios_jit_pe_span, MADEIRA_IAT_SYNC_SPAN, default on); no Wine runs.

Why: the sync hands every non-zero 8-byte word of the region to ios_jit_translate_addr_for_owner(), a pass over the whole mapping
table. E5WNFW (build 665, iPhone15,3, about 130 entries): 2.2 s for the 134 MB .text of a relocated game image, 0.5 s for each sync
of its 38 MB .rdata; GXBV16 (build 659): about half a second for 57 MB; [thread-sample] finds a thread inside the call in 42 logs.

Part A reads the sources. Part B compiles, on the host, the production lookup, ios_jit_pe_span, the switch and the production loop
(cut out of NtProtectVirtualMemory) next to the SAME loop with the span test taken out, and runs both on the same tables and regions
in separate processes:
  - random tables (live entries in three address bands, copies of one range for several owners, cleared entries, entries being
    cleared or written) and random regions (random words, small numbers, pointers into the entries and one byte beside them, with and
    without the keep bitmap, in executable and other sections, for every owner): the region bytes, the five counters and the two
    "first value" fields are identical; so are the lookup's own [xlate-any] lines;
  - with the switch off (MADEIRA_IAT_SYNC_SPAN=0) the production loop makes exactly the lookups of the loop without the test; with
    it on (unset, 1, or anything that is not exactly 0) it makes fewer, and none at all for an empty table or for random data;
  - the edges: a value one byte below the lowest image and at the end of the highest one is not looked up, the first and the last
    byte of an image are; an entry being written (size set, pe_base still NULL) matches from address 0 in both loops;
  - an image registered while a sync runs (a page of the region faults at its first read and the handler adds the entry): at a 4 KB
    boundary of the region both loops rewrite from the same word on; inside a 4 KB stretch the production loop starts at the next
    boundary, less than 512 words later, and never earlier.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
wf = (root / '.github/workflows/build-ipa.yml').read_text()
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def cut(signature):
    a = src.index(signature)
    return src[a:src.index('\n}', a) + 2] + '\n'


# ---------------------------------------------------------------- Part A: sources
span_fn = cut('static void ios_jit_pe_span(')
switch_fn = cut('static int ios_iat_sync_span_enabled(')
lookup_fn = cut('void *ios_jit_translate_addr_for_owner(')
a = src.index('#define IOS_JIT_MAX_MAPPINGS')
mapping = src[a:src.index('\n};', a) + 3]
a = src.index('/* madeira-bcd: a word outside the span of the mapped images is in none of them (ios_jit_pe_span) */')
loop = src[a:src.index('static int ml1017_said;', a)]
DECL = ('                        const int span_on = ios_iat_sync_span_enabled();\n'
        '                        uintptr_t span_lo = 0, span_hi = 0;\n')
TEST = ('                            if (span_on)\n'
        '                            {\n'
        '                                if (!(kw & 511)) ios_jit_pe_span( &span_lo, &span_hi );   /* again for every 4 KB */\n'
        '                                if (val < span_lo || val >= span_hi) { p++; continue; }   /* the lookup would hand it back unchanged */\n'
        '                            }\n')
check('the loop declares the switch and the span once, before its first word', loop.count(DECL) == 1 and loop.index(DECL) < loop.index('while (p < end_p)'))
check('the span test is the first thing done with a word, before both lookups',
      loop.count(TEST) == 1 and loop.count('ios_jit_translate_addr_for_owner(') == 2 and
      loop.index('size_t kw = ') < loop.index(TEST) < loop.index('ios_jit_translate_addr_for_owner('))
check('nothing else in NtProtectVirtualMemory reads the span', src.count('ios_jit_pe_span( &span_lo, &span_hi )') == 1 and src.count('span_lo') == 3)
check('the span skips an entry of size 0, takes the count once and gives lo > hi for an empty table',
      'if (!size) continue;' in span_fn and 'n = ios_jit_mapping_count;' in span_fn and span_fn.count('ios_jit_mapping_count') == 1 and
      'uintptr_t l = ~(uintptr_t)0, h = 0;' in span_fn)
check('the switch is read once, is on unless it is exactly 0, and says so in one line',
      src.count('getenv( "MADEIRA_IAT_SYNC_SPAN" )') == 1 and "int on = !(e && e[0] == '0' && !e[1]);" in switch_fn and
      '__sync_bool_compare_and_swap( &said, 0, 1 )' in switch_fn and switch_fn.count('[iat-span]') == 1)
check('the settings catalog lists env.MADEIRA_IAT_SYNC_SPAN (default on)',
      '"env.MADEIRA_IAT_SYNC_SPAN": {"category": "Memory & JIT pool"' in gen and
      re.search(r'key: "env\.MADEIRA_IAT_SYNC_SPAN", title: "[^"]+", kind: \.bool, defaultValue: "1"', cat) is not None)
check('CI runs this check', 'python3 tests/host/check-iat-sync-span.py' in wf)
ref_loop = loop.replace(DECL, '').replace(TEST, '')
check('the loop without the span test is the loop the reference runs',
      'span_on' not in ref_loop and 'span_lo' not in ref_loop and 'ios_jit_pe_span(' not in ref_loop and
      ref_loop.count('ios_jit_translate_addr_for_owner(') == 2 and len(loop) - len(ref_loop) == len(DECL) + len(TEST))

# ---------------------------------------------------------------- Part B: compiled
PREFIX = r'''
#define _GNU_SOURCE
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
''' + mapping + r'''
static struct ios_jit_mapping ios_jit_mappings[IOS_JIT_MAX_MAPPINGS];
static int ios_jit_mapping_count;
static int capture( int fd, const char *format, ... )
{
    char line[1024];
    va_list args;
    (void)fd;
    va_start( args, format );
    vsnprintf( line, sizeof(line), format, args );
    va_end( args );
    printf( "D %s", line );
    return 0;
}
#define dprintf capture
''' + lookup_fn + span_fn + switch_fn + r'''
static unsigned long lookups;
static void *counted( void *addr, void *owner )
{
    lookups++;
    return ios_jit_translate_addr_for_owner( addr, owner );
}
static uint64_t x86_lo, x86_hi;
static int ios_va_is_x86_code( uint64_t va ) { return va >= x86_lo && va < x86_hi; }
struct result { int fixup_count, x86skip, execskip; unsigned long keep_hits; uint64_t x86_first, exec_first; };
#define ios_jit_translate_addr_for_owner counted
static struct result sync_prod( uint64_t *slots, size_t count, void *sync_owner, int keep_ok, const unsigned char *ios_sync_keep,
                                int region_is_exec )
{
    char *jit_rw_dest = (char *)slots;
    uint64_t *p = slots, *end_p = slots + count;
    int x86skip = 0, execskip = 0, fixup_count = 0;
    unsigned long keep_hits = 0;
    uint64_t x86_first = 0, exec_first = 0;
''' + loop + r'''
    return (struct result){ fixup_count, x86skip, execskip, keep_hits, x86_first, exec_first };
}
static struct result sync_ref( uint64_t *slots, size_t count, void *sync_owner, int keep_ok, const unsigned char *ios_sync_keep,
                               int region_is_exec )
{
    char *jit_rw_dest = (char *)slots;
    uint64_t *p = slots, *end_p = slots + count;
    int x86skip = 0, execskip = 0, fixup_count = 0;
    unsigned long keep_hits = 0;
    uint64_t x86_first = 0, exec_first = 0;
''' + ref_loop + r'''
    return (struct result){ fixup_count, x86skip, execskip, keep_hits, x86_first, exec_first };
}
#undef ios_jit_translate_addr_for_owner
'''

DRIVER = r'''
static int use_ref;
static struct result run( uint64_t *slots, size_t count, void *owner, int keep_ok, const unsigned char *keep, int is_exec )
{
    return use_ref ? sync_ref( slots, count, owner, keep_ok, keep, is_exec ) : sync_prod( slots, count, owner, keep_ok, keep, is_exec );
}
static uint64_t seed;
static uint64_t rnd( void )
{
    seed ^= seed >> 12; seed ^= seed << 25; seed ^= seed >> 27;
    return seed * 0x2545F4914F6CDD1Dull;
}
static uint64_t below( uint64_t n ) { return n ? rnd() % n : 0; }
static uint64_t digest( const uint64_t *w, size_t n )
{
    uint64_t h = 0xcbf29ce484222325ull; size_t i;
    for (i = 0; i < n; i++) { h ^= w[i]; h *= 0x100000001b3ull; h ^= h >> 29; }
    return h;
}
static void *const pebs[4] = { NULL, (void *)0x71ffff0000, (void *)0x116a14000, (void *)0x10900c000 };
static void put( int i, uint64_t pe, uint64_t size, void *owner )
{
    memset( &ios_jit_mappings[i], 0, sizeof(ios_jit_mappings[i]) );
    ios_jit_mappings[i].pe_base = (void *)(uintptr_t)pe;
    ios_jit_mappings[i].size = size;
    ios_jit_mappings[i].jit_base = (void *)(uintptr_t)(0x300000000ull + (uint64_t)i * 0x4000000ull);
    ios_jit_mappings[i].owner_peb = owner;
    if (i >= ios_jit_mapping_count) ios_jit_mapping_count = i + 1;
}
static void clear_table( void ) { ios_jit_mapping_count = 0; memset( ios_jit_mappings, 0, sizeof(ios_jit_mappings) ); }
static uint64_t band_base( void )
{
    switch (below( 3 ))
    {
    case 0: return 0x110000000ull + (below( 0x5000 ) << 16);      /* the app's low range: 64-bit programs and the WoW64 host's libraries */
    case 1: return 0x400000000ull + (below( 0xfff0 ) << 16);      /* a 32-bit program's 4 GB window */
    default: return 0x7000000000ull + (below( 0x20000 ) << 16);   /* the guest band */
    }
}
static void random_table( void )
{
    int n = (int)below( 41 ), i;
    clear_table();
    for (i = 0; i < n; i++)
    {
        unsigned kind = (unsigned)below( 100 );
        uint64_t size = (1 + below( 0x400 )) << 16;
        if (kind < 10) put( i, 0, 0, NULL );                                             /* cleared */
        else if (kind < 13) put( i, band_base(), 0, pebs[below( 4 )] );                  /* being cleared: size gone, pe_base still there */
        else if (kind < 14) put( i, 0, size, pebs[below( 4 )] );                         /* being written: size there, pe_base not yet */
        else if (kind < 30 && i) put( i, (uint64_t)(uintptr_t)ios_jit_mappings[below( i )].pe_base,
                                      ios_jit_mappings[below( i )].size, pebs[1 + below( 3 )] );   /* a copy for another owner (or a stale overlap) */
        else put( i, band_base(), size, pebs[below( 4 )] );
    }
}
static uint64_t random_word( uintptr_t lo, uintptr_t hi )
{
    unsigned kind = (unsigned)below( 100 );
    if (kind < 30) return rnd();
    if (kind < 40) return 0;
    if (kind < 50) return below( 0x10000 );
    if (kind < 60) return rnd() & 0xffffffffull;
    if (kind < 70) return (below( 0x80 ) << 32) | (rnd() & 0xffffffffull);
    if (kind < 90 && ios_jit_mapping_count)
    {
        const struct ios_jit_mapping *m = &ios_jit_mappings[below( ios_jit_mapping_count )];
        uint64_t b = (uint64_t)(uintptr_t)m->pe_base, s = m->size;
        switch (below( 6 ))
        {
        case 0: return b - 1;
        case 1: return b;
        case 2: return b + s - 1;
        case 3: return b + s;
        default: return b + below( s );
        }
    }
    if (kind < 95 && lo < hi) return lo + below( hi - lo );
    switch (below( 7 ))
    {
    case 0: return ~0ull;
    case 1: return 1;
    case 2: return lo - 1;
    case 3: return hi;
    case 4: return hi + 1;
    case 5: return 0x8000000000ull;
    default: return lo;
    }
}
static void report( const char *name, int k, const uint64_t *slots, size_t count, struct result r, unsigned long looked )
{
    printf( "R %s %d: bytes %016llx fixups %d x86 %d exec %d keep %lu first %llx/%llx\n", name, k,
            (unsigned long long)digest( slots, count ), r.fixup_count, r.x86skip, r.execskip, r.keep_hits,
            (unsigned long long)r.x86_first, (unsigned long long)r.exec_first );
    printf( "L %s %d: %lu lookups for %zu words\n", name, k, looked, count );
}

/* an image registered while the sync runs: the page of the region that is read first faults, and the handler adds the entry */
static char *late_page;
static size_t late_len;
static uint64_t late_pe, late_size;
static void late_handler( int sig, siginfo_t *info, void *context )
{
    (void)sig; (void)context;
    if (!late_page || (char *)info->si_addr < late_page || (char *)info->si_addr >= late_page + late_len) _exit( 97 );
    mprotect( late_page, late_len, PROT_READ | PROT_WRITE );
    late_page = NULL;
    put( ios_jit_mapping_count, late_pe, late_size, NULL );   /* size and the rest first, the count last, as ios_jit_add_mapping does */
}
static void late_case( const char *name, size_t first_word )
{
    const size_t page = (size_t)sysconf( _SC_PAGESIZE ), pw = page / 8, pages = (4096 / pw + 1) * 3 + 2;
    char *map = mmap( NULL, pages * page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0 );
    uint64_t *slots = (uint64_t *)map + first_word;
    size_t count = pages * pw - first_word, k, fault_page = pages / 2, changed = 0, first_changed = count;
    struct sigaction sa;
    struct result r;
    unsigned long l0;

    clear_table();
    put( 0, 0x120000000ull, 0x400000, NULL );
    put( 1, 0x7000000000ull, 0x400000, NULL );
    late_pe = 0x7100000000ull; late_size = (uint64_t)count * 8;       /* above every entry so far: outside the span until it is added */
    for (k = 0; k < count; k++) slots[k] = late_pe + k * 8;
    memset( &sa, 0, sizeof(sa) );
    sa.sa_sigaction = late_handler; sa.sa_flags = SA_SIGINFO;
    sigaction( SIGSEGV, &sa, NULL ); sigaction( SIGBUS, &sa, NULL );
    late_page = map + fault_page * page; late_len = page;
    mprotect( late_page, late_len, PROT_NONE );
    x86_lo = x86_hi = 0;
    l0 = lookups;
    r = run( slots, count, NULL, 0, NULL, 0 );
    for (k = 0; k < count; k++)
        if (slots[k] != late_pe + k * 8)
        {
            if (first_changed == count) first_changed = k;
            changed++;
            if (slots[k] != 0x300000000ull + 2 * 0x4000000ull + k * 8) { printf( "R %s: word %zu holds a wrong address\n", name, k ); return; }
        }
    printf( "T %s: registered at word %zu, first rewritten word %zu, %zu rewritten of %zu (fixups %d), all from the first on: %d\n", name,
            fault_page * pw - first_word, first_changed, changed, count, r.fixup_count, changed == count - first_changed );
    printf( "L %s 0: %lu lookups for %zu words\n", name, lookups - l0, count );
    munmap( map, pages * page );
}

int main( int argc, char **argv )
{
    static uint64_t slots[4096], big[1 << 19];
    static unsigned char keep[4096 / 8 + 1];
    struct result r;
    unsigned long l0;
    uintptr_t lo, hi;
    size_t count, k;
    int round;

    if (argc != 2) return 2;
    use_ref = !strcmp( argv[1], "ref" );
    setvbuf( stdout, NULL, _IOFBF, 1 << 16 );

    /* the span itself */
    clear_table();
    ios_jit_pe_span( &lo, &hi );
    if (!(lo > hi)) { puts( "R span: an empty table must give lo > hi" ); return 1; }
    put( 0, 0x7000100000ull, 0x20000, NULL );
    put( 1, 0x130000000ull, 0x40000, pebs[1] );
    put( 2, 0x110000000ull, 0, NULL );                 /* size 0: matches nothing, must not widen the span */
    put( 3, 0, 0, NULL );
    ios_jit_pe_span( &lo, &hi );
    if (lo != 0x130000000ull || hi != 0x7000120000ull) { printf( "R span: %llx..%llx\n", (unsigned long long)lo, (unsigned long long)hi ); return 1; }
    put( 4, 0, 0x30000, NULL );                        /* being written: the lookup matches it from address 0 */
    ios_jit_pe_span( &lo, &hi );
    if (lo != 0 || hi != 0x7000120000ull) { printf( "R span (entry being written): %llx..%llx\n", (unsigned long long)lo, (unsigned long long)hi ); return 1; }
    puts( "R span: empty, live, cleared and half-written entries give the expected ends" );

    /* random tables and regions */
    seed = 0x9E3779B97F4A7C15ull;
    for (round = 0; round < 600; round++)
    {
        int keep_ok = (int)below( 2 ), is_exec = below( 4 ) == 0;
        unsigned density = (unsigned)below( 3 );
        void *owner = pebs[below( 4 )];
        random_table();
        ios_jit_pe_span( &lo, &hi );
        count = 1 + below( 4096 );
        for (k = 0; k < count; k++) slots[k] = random_word( lo, hi );
        for (k = 0; k < sizeof(keep); k++) keep[k] = density == 0 ? 0 : density == 1 ? (unsigned char)(rnd() & rnd()) : 0xff;
        x86_lo = x86_hi = 0;
        if (ios_jit_mapping_count && below( 2 ))
        {
            const struct ios_jit_mapping *m = &ios_jit_mappings[below( ios_jit_mapping_count )];
            x86_lo = (uint64_t)(uintptr_t)m->pe_base + below( m->size + 1 ) / 2;
            x86_hi = x86_lo + below( m->size + 1 ) / 2;
        }
        l0 = lookups;
        r = run( slots, count, owner, keep_ok, keep, is_exec );
        report( "random", round, slots, count, r, lookups - l0 );
    }

    /* an empty table: nothing is in any image */
    x86_lo = x86_hi = 0;
    clear_table();
    for (k = 0; k < 4096; k++) slots[k] = random_word( 0x110000000ull, 0x7200000000ull );
    l0 = lookups;
    r = run( slots, 4096, NULL, 0, keep, 0 );
    report( "empty-table", 0, slots, 4096, r, lookups - l0 );

    /* the edges of the span, one image and two */
    clear_table();
    put( 0, 0x140000000ull, 0x100000, NULL );
    slots[0] = 0x140000000ull - 1; slots[1] = 0x140000000ull; slots[2] = 0x1400fffffull; slots[3] = 0x140100000ull; slots[4] = 0;
    slots[5] = ~0ull; slots[6] = 0x13fffffff8ull; slots[7] = 0x140100008ull;
    l0 = lookups;
    r = run( slots, 8, NULL, 0, keep, 0 );
    report( "edges", 0, slots, 8, r, lookups - l0 );
    if (slots[0] != 0x13fffffffull || slots[1] != 0x300000000ull || slots[2] != 0x3000fffffull || slots[3] != 0x140100000ull)
    { puts( "R edges: the first and the last byte of the image are rewritten, the bytes beside it are not" ); return 1; }
    put( 1, 0x7000000000ull, 0x10000, NULL );
    slots[0] = 0x140000000ull - 8; slots[1] = 0x150000000ull; slots[2] = 0x700000fff8ull; slots[3] = 0x7000010000ull;
    l0 = lookups;
    r = run( slots, 4, NULL, 0, keep, 0 );
    report( "edges", 1, slots, 4, r, lookups - l0 );

    /* an entry being written matches from address 0 */
    clear_table();
    put( 0, 0x140000000ull, 0x100000, NULL );
    put( 1, 0, 0x20000, NULL );
    slots[0] = 0x1000; slots[1] = 0x1ffff; slots[2] = 0x20000; slots[3] = 0x140000010ull;
    l0 = lookups;
    r = run( slots, 4, NULL, 0, keep, 0 );
    report( "being-written", 0, slots, 4, r, lookups - l0 );

    /* a cleared entry's old place is in no image */
    clear_table();
    put( 0, 0x140000000ull, 0x100000, NULL );
    put( 1, 0x110000000ull, 0, NULL );
    for (k = 0; k < 64; k++) slots[k] = 0x110000000ull + k * 8;
    l0 = lookups;
    r = run( slots, 64, NULL, 0, keep, 0 );
    report( "cleared-entry", 0, slots, 64, r, lookups - l0 );

    /* random data against a session-sized table: 168 entries, 4 MB of words */
    clear_table();
    seed = 0xD1B54A32D192ED03ull;
    for (k = 0; k < 168; k++) put( (int)k, band_base(), (1 + below( 0x400 )) << 16, k % 7 ? NULL : pebs[1] );
    for (k = 0; k < sizeof(big) / 8; k++) big[k] = rnd();
    l0 = lookups;
    r = run( big, sizeof(big) / 8, NULL, 0, NULL, 0 );
    report( "random-data", 0, big, sizeof(big) / 8, r, lookups - l0 );

    /* registered during the sync: at a 4 KB boundary of the region, and inside a 4 KB stretch */
    late_case( "late-aligned", 0 );
    late_case( "late-inside", 200 );
    fflush( stdout );
    return 0;
}
'''


def run(exe, mode, value):
    env = dict(os.environ)
    env.pop('MADEIRA_IAT_SYNC_SPAN', None)
    if value is not None:
        env['MADEIRA_IAT_SYNC_SPAN'] = value
    r = subprocess.run([exe, mode], capture_output=True, text=True, env=env, timeout=600)
    return r.returncode, r.stdout.split('\n'), r.stderr


def part(lines, tag):
    return [l for l in lines if l.startswith(tag + ' ')]


def lookups(lines):
    return {l.split(':')[0]: int(l.split(': ')[1].split(' ')[0]) for l in part(lines, 'L')}


def late(lines, name):
    m = re.search(r'T %s: registered at word (\d+), first rewritten word (\d+), (\d+) rewritten of (\d+) \(fixups (\d+)\), all from the first on: (\d)' % name,
                  '\n'.join(lines))
    return tuple(int(x) for x in m.groups()) if m else None


with tempfile.TemporaryDirectory(prefix='iat-sync-span-') as tmp:
    c = os.path.join(tmp, 'check.c')
    exe = os.path.join(tmp, 'check')
    with open(c, 'w') as f:
        f.write(PREFIX + DRIVER)
    cc = subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', c, '-o', exe],
                        capture_output=True, text=True)
    check('the production lookup, span, switch and loop build on the host', cc.returncode == 0, cc.stderr[:2000])
    if cc.returncode == 0:
        rc_ref, ref, err_ref = run(exe, 'ref', None)
        rc_on, on, err_on = run(exe, 'prod', None)
        rc_off, off, err_off = run(exe, 'prod', '0')
        rc_one, one, _ = run(exe, 'prod', '1')
        rc_odd, odd, _ = run(exe, 'prod', '00')
        check('every run ends cleanly', (rc_ref, rc_on, rc_off, rc_one, rc_odd) == (0, 0, 0, 0, 0),
              'ref %d on %d off %d\n%s%s%s' % (rc_ref, rc_on, rc_off, err_ref[-500:], err_on[-500:], err_off[-500:]))
        n = len(part(ref, 'R'))
        check('the reference loop ran every case (%d result lines)' % n, n == 607)
        check('on: the region bytes, the counters and the first values are those of the loop without the test, in every case',
              part(on, 'R') == part(ref, 'R'))
        check('off: the same', part(off, 'R') == part(ref, 'R'))
        some = sum(1 for l in part(ref, 'R') if ' fixups 0 x86 0 exec 0 keep 0 ' not in l)
        check('the cases do rewrite, keep and decline words (%d of %d with a non-zero counter)' % (some, n), some >= 400)
        xl = [l for l in part(ref, 'D') if '[xlate-any]' in l]
        check("the lookup's own [xlate-any] lines are the same (%d of them)" % len(xl),
              len(xl) > 0 and [l for l in part(on, 'D') if '[xlate-any]' in l] == xl and [l for l in part(off, 'D') if '[xlate-any]' in l] == xl)
        lr, lo_, lf = lookups(ref), lookups(on), lookups(off)
        check('off (MADEIRA_IAT_SYNC_SPAN=0): exactly the lookups of the loop without the test, case by case', lf == lr and len(lr) == 608)
        fewer = sum(1 for k in lr if lo_[k] < lr[k])
        check('on: never more lookups, fewer in %d of %d cases (%d against %d in all)' % (fewer, len(lr), sum(lo_.values()), sum(lr.values())),
              all(lo_[k] <= lr[k] for k in lr) and fewer >= 500 and sum(lo_.values()) * 3 < sum(lr.values()))
        check('on: no lookup at all for an empty table (%d without the test)' % lr['L empty-table 0'], lo_['L empty-table 0'] == 0 and lr['L empty-table 0'] > 3000)
        check('on: random data against 168 entries: %d lookups for 524,288 words (%d without the test)' % (lo_['L random-data 0'], lr['L random-data 0']),
              lo_['L random-data 0'] <= 64 and lr['L random-data 0'] == 524288)
        check('on: one image: the bytes beside it are not looked up, its first and last byte are (2 lookups; 7 without the test)',
              lo_['L edges 0'] == 2 and lr['L edges 0'] == 7)
        check('on: two images: what lies between them is looked up, what lies outside is not (2 lookups; 4 without)',
              lo_['L edges 1'] == 2 and lr['L edges 1'] == 4)
        check('on: an entry being written keeps its lookups from address 0 (4 and 4)', lo_['L being-written 0'] == 4 and lr['L being-written 0'] == 4)
        check("on: a cleared entry's old place is not looked up (0; 64 without the test)", lo_['L cleared-entry 0'] == 0 and lr['L cleared-entry 0'] == 64)
        check('the switch: unset, 1 and 00 are on (the same lookups), only 0 is off',
              lookups(one) == lo_ and lookups(odd) == lo_ and part(one, 'R') == part(ref, 'R') and part(odd, 'R') == part(ref, 'R'))
        said = lambda lines: [l for l in part(lines, 'D') if '[iat-span]' in l]
        check('one [iat-span] line a session, saying on or off',
              len(said(on)) == 1 and '[iat-span] on:' in said(on)[0] and len(said(off)) == 1 and '[iat-span] off:' in said(off)[0] and not said(ref))
        ra, oa, fa = late(ref, 'late-aligned'), late(on, 'late-aligned'), late(off, 'late-aligned')
        check('an image registered at a 4 KB boundary of the region: both loops rewrite from that word on (%s)' % (ra,),
              ra is not None and ra == oa == fa and ra[0] == ra[1] and ra[0] % 512 == 0 and ra[2] == ra[3] - ra[0] == ra[4] and ra[5] == 1)
        ri, oi, fi = late(ref, 'late-inside'), late(on, 'late-inside'), late(off, 'late-inside')
        check('registered inside a 4 KB stretch: the loop without the test and the switched-off loop rewrite from that word on',
              ri is not None and ri == fi and ri[0] == ri[1] and ri[0] % 512 != 0 and ri[5] == 1)
        if ri and oi:
            nxt = (ri[0] + 511) // 512 * 512
            check('on: from the next 4 KB boundary on, %d words later (never earlier, less than 512 later), and every word after it'
                  % (oi[1] - ri[0]), oi[0] == ri[0] and oi[1] == nxt and 0 < oi[1] - ri[0] < 512 and oi[2] == oi[3] - oi[1] == oi[4] and oi[5] == 1)
        else:
            check('on: the late image is seen', False, str(oi))

print('PASS: iat-sync span' if ok else 'FAILED: iat-sync span')
sys.exit(0 if ok else 1)
