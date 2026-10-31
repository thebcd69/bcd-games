#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
"""swap-mb caps the disk in broad mode (swap-mode = 2), for commits into reservations as well (madeira-bcd, build 622).

The owner's Spider-Man 2 logs (build 620): with swap-mb = 256 and swap-mode = 2 the swap file held 2,445 MB on the disk. broad
maps whole reservations from the sparse file when they are made, and the cap ("the disk the file occupies now", ml1257) was asked
only then, when the file was still empty; later commits into them grew it without a bound. Build 621 capped the committed data
instead, and that was wrong: Spider-Man 2 commits ~9.3 GB into the file for 2.4 GB on the disk, so swap-mb 2048-4096 filled at
load, the rest stayed in memory and iOS closed the game at 8 GB (owner's logs 2026-10-28 16:00-16:02); RDR2 commits 10-13.5 GB
for 4.2-4.5 GB on the disk against its 8192. Build 622: the cap is the disk again, and it is also asked for every fresh commit
into a reservation that is in the file; past it the commit gets fresh anonymous memory.

This compiles the production swap-tier core (virtual_ios.c, between the "swap-tier core" markers) on macOS against small stubs
for Wine's view and protection helpers and runs it on real memory with a real sparse file (Darwin's own F_PUNCHHOLE): committed
data past swap-mb stays in the file while the disk is under it; once the written-back disk reaches it the next commit stays in
memory with zeroed, writable pages and leaves the reservation's extent, and the data beside it stays intact; executable commits
are never moved; a decommit takes its pages off the disk and the next commit goes to the file again; a committed reservation
twice the cap is mapped while the disk is empty and a new one is refused once the disk is past it; the disk figure is cached for
20 ms; the HUD shows the disk; the start, "reached", census and stats lines name the figures. Source checks cover the call sites.
Off Darwin the runtime part is skipped (CI is macOS).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
failures = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        failures.append(what)


def body_of(src, signature):
    start = src.find(signature)
    while start >= 0:
        semi, brace = src.find(';', start), src.find('{', start)
        if brace != -1 and (semi == -1 or brace < semi):
            break
        start = src.find(signature, start + 1)
    if start < 0:
        raise ValueError(signature)
    brace = src.index('{', start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return src[brace:i + 1]
    raise AssertionError('unterminated body: ' + signature)


core = virt[virt.index('/* swap-tier core begin'):virt.index('/* swap-tier core end */')]

prelude = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <errno.h>
#include <unistd.h>
#include <time.h>
#include <fcntl.h>
#include <assert.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <mach/mach.h>
typedef unsigned long ULONG_PTR;
#define VPROT_READ       0x01
#define VPROT_WRITE      0x02
#define VPROT_EXEC       0x04
#define VPROT_WRITECOPY  0x08
#define VPROT_GUARD      0x10
#define VPROT_COMMITTED  0x20
#define VPROT_WRITEWATCH 0x40
#define VPROT_ARM64EC          0x0100
#define VPROT_SYSTEM           0x0200
#define VPROT_PLACEHOLDER      0x0400
#define VPROT_FREE_PLACEHOLDER 0x0800
#define SEC_FILE    0x00800000
#define SEC_IMAGE   0x01000000
#define SEC_RESERVE 0x04000000
#define SEC_COMMIT  0x08000000
struct file_view { void *base; size_t size; unsigned int protect; };
static inline int is_view_valloc( const struct file_view *view )
{ return !(view->protect & (SEC_FILE | SEC_RESERVE | SEC_COMMIT)); }
static uintptr_t host_page_mask = 0x3fff;
ULONG_PTR ios_fex_arena_base_unix = 0x7c00000000ULL, ios_fex_arena_end_unix = 0x8000000000ULL;
void *ios_jit_rw_base_global = (void *)0x7000000000ULL;
void *ios_jit_rx_base_global = (void *)0x119400000ULL;
size_t ios_jit_pool_size_global = 0x20000000;
uintptr_t ios_jit_low_rx_global, ios_jit_low_rw_global;
size_t ios_jit_low_size_global;
static size_t jit_pool_offset, ios_jit_tail_reserved;
static int ios_jit_low_overlaps( uintptr_t a, size_t size )
''' + body_of(virt, 'static int ios_jit_low_overlaps(') + r'''
static int get_unix_prot( unsigned char v )
{
    int p = 0;
    if ((v & VPROT_COMMITTED) && !(v & VPROT_GUARD))
    {
        if (v & VPROT_READ) p |= PROT_READ;
        if (v & VPROT_WRITE) p |= PROT_READ | PROT_WRITE;
        if (v & VPROT_EXEC) p |= PROT_READ;
    }
    return p;
}
/* every page committed read-write, as set_protection leaves a fresh commit */
static unsigned char get_host_page_vprot( const void *addr ) { (void)addr; return VPROT_READ | VPROT_WRITE | VPROT_COMMITTED; }
static int mprotect_range( void *base, size_t size, unsigned char set, unsigned char clear )
{
    char *a = (char *)((uintptr_t)base & ~host_page_mask);
    char *e = (char *)(((uintptr_t)base + size + host_page_mask) & ~host_page_mask);
    for (; a < e; a += host_page_mask + 1)
        if (mprotect( a, host_page_mask + 1, get_unix_prot( (get_host_page_vprot( a ) & ~clear) | set ) )) return -1;
    return 0;
}
static void *anon_mmap_fixed( void *a, size_t l, int prot, int flags )
{
    (void)flags;
    assert( !((uintptr_t)a & host_page_mask) );
    assert( !(l & host_page_mask) );
    return mmap( a, l, prot, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 );
}
'''

harness = r'''
static unsigned long long ios_swap_footprint_mb( void ) { return 4321; }
static void ios_swap_cfg( int *mode, int *min_mb ) { *mode = 2; *min_mb = 0; }
static unsigned long long ios_swap_disk_used( void )
{
    struct stat st;
    if (ios_swap_fd < 0 || fstat( ios_swap_fd, &st )) return 0;
    return (unsigned long long)st.st_blocks * 512;
}
static int bad;
#define CHECK(c, what) do { if (!(c)) { printf("FAIL: %s (line %d)\n", what, __LINE__); bad++; } else printf("ok: %s\n", what); } while (0)
#define MB (1ull << 20)

/* every extent's count within its length, and the total their sum */
static int consistent( void )
{
    unsigned long long sum = 0;
    unsigned i;
    for (i = 0; i < ios_swap_n; i++)
    {
        if (ios_swap_ext[i].live > ios_swap_ext[i].len) return 0;
        sum += ios_swap_ext[i].live;
    }
    return sum == ios_swap_live;
}
static char *region( uintptr_t at, size_t len )
{
    void *p = mmap( (void *)at, len, PROT_NONE, MAP_PRIVATE | MAP_ANON | MAP_FIXED, -1, 0 );
    if (p == MAP_FAILED || p != (void *)at) { printf( "FAIL: cannot map a test region at %p (errno %d)\n", (void *)at, errno ); exit( 2 ); }
    return p;
}
/* what set_protection does for a fresh commit inside a file-mapped reservation: an mprotect of those pages */
static void commit( char *p, size_t len, struct file_view *v )
{
    mprotect( p, len, PROT_READ | PROT_WRITE );
    ios_swap_commit( p, len, VPROT_READ | VPROT_WRITE, v );
}
static int all( const char *p, size_t len, char c ) { size_t i; for (i = 0; i < len; i += 4096) if (p[i] != c) return 0; return 1; }

int main( void )
{
    char path[] = "/tmp/madeira-swapcap-XXXXXX";
    struct file_view v = { 0, 0, 0 }, v2 = { 0, 0, 0 }, v3 = { 0, 0, 0 };
    char *r, *r2, *r3;
    unsigned long long disk;
    int fd = mkstemp( path );
    if (fd < 0) return 2;
    close( fd );
    setenv( "MADEIRA_SWAP_FILE", path, 1 );
    setenv( "MADEIRA_SWAP_MB", "64", 1 );
    setenv( "MADEIRA_SWAP_COVERAGE", "broad", 1 );
    ios_swap_init();
    if (ios_swap_fd < 0 || !ios_swap_broad) { printf( "FAIL: the tier did not start in broad\n" ); return 1; }
    unlink( path );
    CHECK( ios_swap_cap == 64 * MB, "the cap is swap-mb (64 MB)" );

    /* a 256 MB writable reservation: mapped from the file whole, nothing committed, nothing on the disk */
    r = region( 0x7050000000ULL, 256 * MB );
    v.base = r; v.size = 256 * MB;
    CHECK( ios_swap_whole_resv( &v, VPROT_READ | VPROT_WRITE ) == 1 && ios_swap_n == 1 && ios_swap_ext[0].resv, "a reservation is mapped while the disk is under the cap" );
    CHECK( ios_swap_live == 0 && madeira_hud_swap_bytes() == 0, "a reservation alone is no data and no disk" );

    /* 32 MB committed and written, then 48 MB more: 80 MB committed is past swap-mb, but the disk is not (build 621's
     * mistake was to cap the committed data: Spider-Man 2 commits ~4x what it writes) -- both commits stay in the file */
    commit( r, 32 * MB, &v );
    memset( r, 0x41, 32 * MB );
    commit( r + 64 * MB, 48 * MB, &v );
    memset( r + 64 * MB, 0x42, 48 * MB );
    CHECK( ios_swap_live == 80 * MB && ios_swap_overlaps( r, 32 * MB ) && ios_swap_overlaps( r + 64 * MB, 48 * MB ), "committed data past swap-mb stays in the file while the disk is under it" );
    CHECK( ios_swap_cap_anon == 0 && ios_swap_cap_anon_n == 0, "... and nothing is kept in memory" );
    CHECK( consistent(), "counts consistent after the two commits" );

    /* written back: the disk holds 80 MB, past the cap */
    msync( r, 112 * MB, MS_SYNC );
    disk = ios_swap_disk_used();
    printf( "disk after write-back: %llu KB\n", disk >> 10 );
    CHECK( disk >= 79 * MB && disk <= 81 * MB, "the disk holds what was written (80 MB)" );
    CHECK( madeira_hud_swap_bytes() == disk, "the HUD and the Task Manager show the disk" );
    ios_swap_disk_seen_ns = 0;   /* as if 20 ms had passed: the figure is read again */
    CHECK( ios_swap_disk_full() && ios_swap_disk_seen == disk, "the disk has reached the cap" );
    {
        unsigned long long seen = ios_swap_disk_seen;
        ios_swap_disk_seen = 0;   /* within 20 ms the cached figure is used, no fstat */
        CHECK( !ios_swap_disk_full() && ios_swap_disk_seen == 0, "within 20 ms the figure is not read again" );
        ios_swap_disk_seen = seen;
    }

    /* the next fresh commit stays in memory, zeroed and writable, and leaves the extent; the data beside it is intact */
    commit( r + 160 * MB, 16 * MB, &v );
    CHECK( !ios_swap_overlaps( r + 160 * MB, 16 * MB ), "the commit after the cap left the reservation's extent" );
    CHECK( ios_swap_cap_anon == 16 * MB && ios_swap_cap_anon_n == 1, "... and is reported as kept in memory" );
    CHECK( ios_swap_live == 80 * MB, "... and is not counted as file data" );
    CHECK( all( r + 160 * MB, 16 * MB, 0 ), "the memory it got reads zero" );
    memset( r + 160 * MB, 0x43, 16 * MB );
    CHECK( all( r + 160 * MB, 16 * MB, 0x43 ), "... and is writable" );
    CHECK( all( r, 32 * MB, 0x41 ) && all( r + 64 * MB, 48 * MB, 0x42 ), "the file data beside it is intact" );
    CHECK( ios_swap_overlaps( r + 112 * MB, 48 * MB ) && ios_swap_overlaps( r + 176 * MB, 80 * MB ), "the rest of the reservation is still mapped from the file" );
    CHECK( consistent(), "counts consistent after the split" );
    CHECK( ios_swap_disk_used() >= 79 * MB && ios_swap_disk_used() <= 81 * MB, "the disk did not grow" );

    /* an executable commit past the cap is never moved (counted only) */
    mprotect( r + 228 * MB, 8 * MB, PROT_READ | PROT_WRITE );
    ios_swap_commit( r + 228 * MB, 8 * MB, VPROT_READ | VPROT_WRITE | VPROT_EXEC, &v );
    CHECK( ios_swap_cap_anon == 16 * MB && ios_swap_overlaps( r + 228 * MB, 8 * MB ) && ios_swap_live == 88 * MB, "an executable commit is not moved" );

    /* a decommit of the 48 MB: punched, out of the count and off the disk; the next commit goes to the file again */
    CHECK( ios_swap_punch_resv( r + 64 * MB, 48 * MB ) == 1, "decommit punches in place" );
    CHECK( ios_swap_live == 40 * MB, "the decommitted data leaves the count" );
    disk = ios_swap_disk_used();
    CHECK( disk >= 31 * MB && disk <= 33 * MB, "... and the disk (32 MB left)" );
    ios_swap_disk_seen_ns = 0;
    commit( r + 192 * MB, 16 * MB, &v );
    CHECK( ios_swap_overlaps( r + 192 * MB, 16 * MB ) && ios_swap_live == 56 * MB && ios_swap_cap_anon == 16 * MB, "under the cap again: the commit is file data" );
    CHECK( consistent(), "counts consistent after it" );

    /* MEM_RELEASE of the whole reservation: nothing left in the table, the count or on the disk */
    ios_swap_release_range( r, 256 * MB, 0 );
    CHECK( ios_swap_n == 0 && ios_swap_live == 0 && madeira_hud_swap_bytes() <= 1 * MB, "release: count and disk are zero again" );

    /* a reservation made committed (MEM_RESERVE|MEM_COMMIT) of 128 MB, twice the cap, with an empty disk: mapped (RDR2 commits
     * 10-13.5 GB for 4.2-4.5 GB on the disk against its 8192) */
    r2 = region( 0x7060000000ULL, 128 * MB );
    v2.base = r2; v2.size = 128 * MB;
    anon_mmap_fixed( r2, 128 * MB, PROT_READ | PROT_WRITE, 0 );
    CHECK( ios_swap_whole_resv( &v2, VPROT_READ | VPROT_WRITE | VPROT_COMMITTED ) == 1 && ios_swap_n == 1 && ios_swap_live == 128 * MB, "a committed reservation past swap-mb is mapped while the disk is under it" );
    memset( r2, 0x44, 128 * MB );
    msync( r2, 128 * MB, MS_SYNC );
    CHECK( ios_swap_disk_used() >= 127 * MB, "written back: 128 MB on the disk" );
    /* with the disk past the cap a new committed reservation is not mapped (the ml1257 rule, asked when it is made) */
    r3 = region( 0x7068000000ULL, 32 * MB );
    v3.base = r3; v3.size = 32 * MB;
    anon_mmap_fixed( r3, 32 * MB, PROT_READ | PROT_WRITE, 0 );
    {
        unsigned long long before = ios_swap_disk_refused;
        CHECK( ios_swap_whole_resv( &v3, VPROT_READ | VPROT_WRITE | VPROT_COMMITTED ) == 1 && ios_swap_n == 1 && ios_swap_disk_refused == before + 1, "with the disk past the cap a new reservation is not mapped" );
    }
    ios_swap_release_range( r2, 128 * MB, 0 );
    CHECK( ios_swap_n == 0 && ios_swap_live == 0 && consistent(), "released: zero" );

    ios_swap_stats_line();
    printf( "%d failures\n", bad );
    return bad != 0;
}
'''

if sys.platform != 'darwin':
    print('note: the runtime part needs Darwin (mach, F_PUNCHHOLE); source checks only')
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 'swapcap.c'
        exe = Path(tmp) / 'swapcap'
        c.write_text(prelude + core + harness)
        b = subprocess.run(['cc', '-O1', '-Wall', '-Wno-unused-function', '-Wno-deprecated-declarations', '-o', str(exe), str(c)],
                           capture_output=True, text=True)
        check(b.returncode == 0, 'the swap-tier core compiles with the harness' + ('' if b.returncode == 0 else ':\n' + b.stderr[-4000:]))
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            fails = [l for l in r.stdout.splitlines() if l.startswith('FAIL')]
            print('\n'.join(l for l in r.stdout.splitlines() if l.startswith('disk after')))
            check(r.returncode == 0 and not fails, 'runtime: %d checks' % r.stdout.count('ok: ') + ('' if not fails else ':\n' + '\n'.join(fails)))
            err = r.stderr
            check('swap-mb caps the disk the file occupies (past it, new commits stay in memory)' in err, 'the start line says what swap-mb caps')
            reached = [l for l in err.splitlines() if l.startswith('[swap] swap-mb reached:')]
            check(len(reached) == 1 and re.fullmatch(r'\[swap\] swap-mb reached: the swap file holds (79|80|81) MB on the disk \(cap 64 MB\); '
                                                    r'later commits stay in memory', reached[0]) is not None,
                  'the first commit kept in memory is announced once, with the disk and the cap')
            census = [l for l in err.splitlines() if l.startswith('[swap] census file-backed now=')]
            check(bool(census) and re.search(r' disk=\d+MB.* committed=\d+MB kept-in-memory=\d+MB', census[-1]) is not None,
                  'the census carries the disk, the committed data and what swap-mb kept in memory')
            check(re.search(r'\| coverage broad: disk \d+ MB of 64 cap, .* disk-full refusals 1 .*'
                            r'\| committed data in the file 0 MB \(peak 128\), 16 MB kept in memory by swap-mb$', err, re.M) is not None,
                  'the stats line carries the disk, the refusal, the committed data (peak 128) and what was kept in memory')

# --------------------------------------------------------------- source checks
avm = body_of(virt, 'static NTSTATUS allocate_virtual_memory(')
check('if (any_committed) ios_swap_recommit( base, size, fresh_bytes );' in avm,
      'a commit that also covered committed pages counts its fresh pages')
commit = body_of(virt, 'static void ios_swap_commit( void *base, size_t size, unsigned int vprot, struct file_view *view )\n{')
check('ios_swap_present( base, size, vprot );' in commit, 'a commit inside a mapped reservation goes through ios_swap_present')
smap = body_of(virt, 'static int ios_swap_map( void *base, size_t size, int unix_prot, int committed )\n{')
# Round 625 (the owner's decision 38 b): the rule is asked before helper memory as in build 624, or after it with
# env.MADEIRA_POOL_MORE = 1 (check-helper-memory.py); a reservation for the file meets it either way.
check('if (!ios_pool_more && ios_swap_broad && ios_swap_disk_used() >= ios_swap_cap) { ios_swap_disk_refused++; ios_swap_refused++; return IOS_SW_REFUSED; }' in smap
      and 'if (ios_pool_more && ios_swap_broad && ios_swap_disk_used() >= ios_swap_cap) { ios_swap_disk_refused++; ios_swap_refused++; return IOS_SW_REFUSED; }' in smap,
      'a reservation is not mapped once the disk has reached swap-mb (the ml1257 rule; before helper memory, or after it with MADEIRA_POOL_MORE)')
present = body_of(virt, 'static void ios_swap_present( void *base, size_t size, unsigned int vprot )\n{')
check('full = ios_swap_broad && plain && ios_swap_disk_full();' in present and 'if (full && !ios_swap_ext[i].img && ncut < 16)' in present,
      'a fresh commit into a reservation in the file stays in memory once the disk has reached swap-mb')
check('ios_swap_live + len > ios_swap_cap' not in virt and 'ios_swap_live + (uint64_t)(ob - oa) > ios_swap_cap' not in virt,
      'nothing caps the committed data (build 621: Spider-Man 2 commits ~4x, RDR2 ~3x what they write)')
full = body_of(virt, 'static int ios_swap_disk_full( void )\n{')
check('ios_swap_disk_used()' in full and '20000000ULL' in full, 'the disk is read with one fstat at most every 20 ms')
hud = body_of(virt, 'unsigned long long madeira_hud_swap_bytes( void )\n{')
check('ios_swap_disk_used()' in hud.split('*/')[-1] and 'ios_swap_live' not in hud.split('*/')[-1],
      'the HUD and the Task Manager show the disk, the figure swap-mb caps')
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('python3 tests/host/check-swap-cap.py' in wf, 'CI runs this check')

if failures:
    print('FAILED: swap-mb cap')
    sys.exit(1)
print('PASS: swap-mb caps the disk in broad mode, for commits into reservations as well')
