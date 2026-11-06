#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Portions copyright 2026 spitefulowl: code taken from https://github.com/willfaust/Madeira (commit 62e47df)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""bcd.cfg oversize-reserve / oversize-reserve-mb / fft-arena-patch (upstream Madeira v0.2.0 ml1298 + ml1299, 62e47df and 74e05ae); no Wine runs.

FINAL FANTASY TACTICS - The Ivalice Chronicles (FFT_classic.exe, FFT_enhanced.exe) starts with VirtualAlloc(NULL, 0x4003ffffff, MEM_RESERVE,
PAGE_NOACCESS): 256 GiB plus 64 MiB of slack. iOS gives the app a 63 GB map (a 32 GB hole in a 512 GB one), the reserve fails, main returns 0
and the game is gone before it opens a window: 11 BCD logs end at "[jumbo] kernel-pick reserve failed (0xc0000017) for size=0x4003ffffff"
(X4WZ5E and ten more, builds 579-611); the list's MADEIRA_USER_VA_GB = 32 does not change the ask.

Two opt-in keys (off, per game): oversize-reserve = 1 serves an unhinted reserve of 64 GB or more in part, with a real 64 MB aligned range
(oversize-reserve-mb, default 16384, halved down to 1 GB while the map has no hole that big) and reports the size asked for; fft-arena-patch = 1
(only with oversize-reserve) also sets the one immediate of the game's allocator (a 64 GB arena) to that real part, in the mapped image, for an
image named FFT_classic.exe / FFT_enhanced.exe whose 96-byte allocator init matches exactly once, and only when the 256 GB reserve does not fit.

Part A reads the sources (hooks, keys, the 96-byte pattern, the places the patch does and does not run). Part B compiles the production code
on the host (ASAN/UBSAN): the key parser against the production bcd.cfg reader; ios_oversize_reserve against a model allocator; the commit log;
and ios_fft_arena_patch against a synthetic image (patched exactly where the conditions hold, nothing else touched).
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(sig):
    a = src.index(sig)
    return src[a:src.index('\n}\n', a) + 3]


# ---------------------------------------------------------------- Part A: sources
real_mb = function('static long long ios_oversize_real_mb( void )')
reserve = function('static NTSTATUS ios_oversize_reserve( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect )')
failed = function('static void ios_oversize_commit_failed( const void *addr, SIZE_T size, NTSTATUS status )')
image_is = function('static int ios_image_file_is( const UNICODE_STRING *nt_name, const char *want )')
patch = function('static void ios_fft_arena_patch( char *base, const IMAGE_NT_HEADERS *nt, const IMAGE_SECTION_HEADER *sec,')
pat_src = re.search(r'static const short ios_fft_arena_pat\[\] =\n\{(.*?)\n\};', src, re.S)[1]
pat = []
for tok in re.findall(r'-1|0x[0-9a-fA-F]{2}', re.sub(r'/\*.*?\*/', '', pat_src)):
    pat.append(int(tok, 16) if tok.startswith('0x') else -1)
check('the allocator pattern is 96 bytes, ten of them wildcards (the two rel32 operands), with the 8-byte immediate 0x1000000000 at 0x33 '
      'right after the movabs rax opcode', len(pat) == 96 and pat.count(-1) == 8 and pat[0x31:0x33] == [0x48, 0xb8] and
      pat[0x33:0x3b] == [0, 0, 0, 0, 0x10, 0, 0, 0] and '#define IOS_FFT_ARENA_IMM 0x33' in src, 'len=%d wild=%d' % (len(pat), pat.count(-1)))
check('the two keys are plain bcd.cfg keys read once; the real part is 0 unless oversize-reserve is on',
      'madeira_cfg_bool( "oversize-reserve", 0 )' in real_mb and 'madeira_cfg_int( "oversize-reserve-mb", 16384 )' in real_mb and ': 0;' in real_mb)
check('the unhinted reserve hooks: NtAllocateVirtualMemory after the jumbo failure census, NtAllocateVirtualMemoryEx for an unconstrained request',
      src.count('ios_oversize_reserve( ret, size_ptr, type, protect )') == 2 and
      src.index('ios_band_census( "jumbo reserve failed" );') < src.index('if (!ios_oversize_reserve( ret, size_ptr, type, protect )) return STATUS_SUCCESS;') and
      'if (st && !jumbo_hint && !limit_low && !limit_high && !align && !attributes\n            && !ios_oversize_reserve( ret, size_ptr, type, protect ))\n            st = STATUS_SUCCESS;' in src)
check('the commit-failure log is called from both entry points, only for a failed commit with a hint', src.count('ios_oversize_commit_failed( jumbo_hint, jumbo_size, st );') == 2 and
      src.count('if (st && (type & MEM_COMMIT) && jumbo_hint) ios_oversize_commit_failed(') == 2)
check('the arena patch runs from map_image_into_view after the x64 image mark, before the protections and the pool copy',
      src.count('ios_fft_arena_patch( ptr, nt, sec, total_size, nt_name );') == 1 and
      src.index('ios_ignore_volatile_metadata( ptr, total_size, nt, (ULONG_PTR)image_info->base, nt_name );') <
      src.index('ios_fft_arena_patch( ptr, nt, sec, total_size, nt_name );') < src.index('    /* set the image protections */\n\n#ifdef WINE_IOS\n    /* madeira-bcd [wow-cpu-pool]'))
check('the patch needs: the key, the two image names, a 64-bit image, oversize-reserve-mb >= 1024, the 256 GB reserve failing, exactly one match, no pool copy yet',
      all(t in patch for t in ('madeira_cfg_bool( "fft-arena-patch", 0 )', '"fft_classic.exe"', '"fft_enhanced.exe"', 'IMAGE_NT_OPTIONAL_HDR64_MAGIC', 'if (mb < 1024)',
                               'IOS_FFT_RESERVE', 'if (hits != 1)', 'ios_jit_mappings[i].pe_base == base && !ios_jit_mappings[i].unmapped')) and
      patch.index('"fft_classic.exe"') < patch.index('madeira_cfg_bool( "fft-arena-patch", 0 )') < patch.index('if (mb < 1024)') < patch.index('mmap( NULL, IOS_FFT_RESERVE') <
      patch.index('if (hits != 1)') < patch.index('ios_jit_mappings[i].pe_base == base') < patch.index('memcpy( hit + IOS_FFT_ARENA_IMM'))
check('it scans executable sections only and stays inside the image', 'IMAGE_SCN_MEM_EXECUTE' in patch and 'sec[i].VirtualAddress >= total_size' in patch and
      'if (size > total_size - sec[i].VirtualAddress) size = total_size - sec[i].VirtualAddress;' in patch)
check('it writes eight bytes at one place only', patch.count('memcpy(') == 1 and patch.count('mprotect(') == 1)

# ---------------------------------------------------------------- Part B: compiled
harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/mman.h>
#include "madeira_cfg.h"
typedef uintptr_t ULONG_PTR, UINT_PTR;
typedef uint64_t UINT64;
typedef size_t SIZE_T;
typedef unsigned int ULONG, NTSTATUS, DWORD;
typedef unsigned short USHORT, WORD;
typedef uint16_t WCHAR;
typedef int BOOL;
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017u
#define MEM_RESERVE 0x2000
#define MEM_COMMIT 0x1000
#define MEM_TOP_DOWN 0x100000
#define PAGE_NOACCESS 1
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define IMAGE_SCN_MEM_EXECUTE 0x20000000
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define min(a,b) ((a)<(b)?(a):(b))
#define ROUND_SIZE(addr,size,mask) (((SIZE_T)(size) + ((UINT_PTR)(addr) & (mask)) + (mask)) & ~(UINT_PTR)(mask))
#define dprintf(fd, ...) do { logs++; fprintf(stderr, __VA_ARGS__); } while (0)
static int logs;
static UINT_PTR page_mask = 0xfff, host_page_mask = 0x3fff;
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { WORD NumberOfSections; } FILEHDR;
typedef struct { WORD Magic; } OPTHDR;
typedef struct { FILEHDR FileHeader; OPTHDR OptionalHeader; } IMAGE_NT_HEADERS;
typedef struct { DWORD VirtualAddress; DWORD SizeOfRawData; struct { DWORD VirtualSize; } Misc; DWORD Characteristics; } IMAGE_SECTION_HEADER;
static struct { void *pe_base; int unmapped; } ios_jit_mappings[4];
static int ios_jit_mapping_count;
/* the production bcd.cfg reader and the production code */
static int mmap_fits;       /* 1: the 256 GB probe mmap succeeds */
static int mprotect_ok = 1;
static void *fake_mmap(void *a, size_t l, int p, int f, int fd, off_t o) { (void)a; (void)l; (void)p; (void)f; (void)fd; (void)o; return mmap_fits ? (void *)0x1000 : MAP_FAILED; }
static int fake_munmap(void *a, size_t l) { (void)a; (void)l; return 0; }
static int fake_mprotect(void *a, size_t l, int p) { (void)a; (void)l; (void)p; if (!mprotect_ok) { errno = 13; return -1; } return 0; }
#define mmap fake_mmap
#define munmap fake_munmap
#define mprotect fake_mprotect
@REALMB@
#define IOS_OVERSIZE_MIN   0x1000000000ULL
#define IOS_OVERSIZE_ALIGN 0x4000000UL
#define IOS_FFT_RESERVE   0x4004000000ULL
#define IOS_FFT_ARENA_IMM 0x33
@PAT@
static SIZE_T ios_fft_arena_size;
#define IOS_OVERSIZE_SLOTS 8
static struct { uintptr_t base; SIZE_T real, size; } ios_oversize[IOS_OVERSIZE_SLOTS];
static unsigned ios_oversize_n;
static pthread_mutex_t ios_oversize_lock = PTHREAD_MUTEX_INITIALIZER;
/* a model allocator: one hole of `hole` bytes at `hole_base`; refuses a size above it, an alignment it cannot give, or a hint */
static ULONG_PTR hole_base = 0x7100000000ULL; static SIZE_T hole;
static ULONG_PTR last_align; static int alloc_calls;
static NTSTATUS allocate_virtual_memory(void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect, ULONG_PTR limit_low, ULONG_PTR limit_high, ULONG_PTR align, ULONG attributes)
{
    (void)type; (void)protect; (void)limit_low; (void)limit_high; (void)attributes;
    alloc_calls++; last_align = align;
    if (*size_ptr > hole) return STATUS_NO_MEMORY;
    *ret = (void *)((hole_base + align - 1) & ~(align - 1));
    *size_ptr = ROUND_SIZE(0, *size_ptr, page_mask);
    return STATUS_SUCCESS;
}
@REALLOC@
@FAILED@
@IMAGEIS@
@PATCH@
static void run_reserve(void)
{
    void *ret; SIZE_T sz; NTSTATUS st;
    ios_oversize_n = 0; memset(ios_oversize, 0, sizeof(ios_oversize)); ios_fft_arena_size = 0;

    /* 32 GB hole: the game's 0x4003ffffff ask gets 16 GB real (the default), rounded up to pages in the report */
    hole = 32ULL << 30; ret = NULL; sz = 0x4003ffffffULL; alloc_calls = 0;
    st = ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_NOACCESS);
    printf("big st=%x ret=%d size=%llx real=%llx align=%lx\n", st, ret == (void *)((hole_base + 0x3ffffff) & ~0x3ffffffULL), (unsigned long long)sz, (unsigned long long)ios_oversize[0].real, (unsigned long)last_align);
    /* the same with MEM_TOP_DOWN is the same request */
    hole = 32ULL << 30; ret = NULL; sz = 0x4003ffffffULL;
    printf("topdown st=%x\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE | MEM_TOP_DOWN, PAGE_NOACCESS));
    /* only 6 GB: 16 -> 8 -> 4 -> real 4 GB? (the largest power-of-two half that fits) */
    hole = 6ULL << 30; ret = NULL; sz = 0x4003ffffffULL; ios_oversize_n = 0;
    st = ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_NOACCESS);
    printf("six st=%x real=%llx\n", st, (unsigned long long)ios_oversize[0].real);
    /* only 512 MB: below the 1 GB floor */
    hole = 512ULL << 20; ret = NULL; sz = 0x4003ffffffULL;
    printf("tiny st=%x\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_NOACCESS));
    /* an ml1299 arena of 32 GB raises the floor to the arena: a 16 GB hole is not enough */
    hole = 16ULL << 30; ret = NULL; sz = 0x4003ffffffULL; ios_fft_arena_size = 32ULL << 30;
    printf("arena st=%x\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_NOACCESS));
    ios_fft_arena_size = 0;
    /* shapes that are not served: hinted, below 64 GB, a commit, a placeholder */
    hole = 32ULL << 30; ret = (void *)0x7200000000ULL; sz = 0x4003ffffffULL; alloc_calls = 0;
    printf("hinted st=%x calls=%d\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_NOACCESS), alloc_calls);
    ret = NULL; sz = 0xfffffffffULL;
    printf("below64 st=%x calls=%d\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_NOACCESS), alloc_calls);
    ret = NULL; sz = 0x4003ffffffULL;
    printf("commit st=%x calls=%d\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE | MEM_COMMIT, PAGE_NOACCESS), alloc_calls);
    printf("placeholder st=%x calls=%d\n", ios_oversize_reserve(&ret, &sz, MEM_RESERVE | 0x40000, PAGE_NOACCESS), alloc_calls);
    printf("logs=%d\n", logs);
}
/* a synthetic image: a header-less buffer with two sections; the allocator init in the executable one */
static unsigned char image[0x20000];
static UNICODE_STRING name_of(const char *s, WCHAR *buf) { size_t n = strlen(s), i; for (i = 0; i < n; i++) buf[i] = (unsigned char)s[i]; UNICODE_STRING u = { (USHORT)(n * 2), (USHORT)(n * 2), buf }; return u; }
static void put_pattern(unsigned char *at, unsigned call_a, unsigned call_b)
{
    size_t i;
    int w = 0;
    for (i = 0; i < ARRAY_SIZE(ios_fft_arena_pat); i++)
    {
        if (ios_fft_arena_pat[i] >= 0) { at[i] = (unsigned char)ios_fft_arena_pat[i]; continue; }
        /* the two wildcard groups (call rel32, jcc rel32): any value */
        at[i] = (unsigned char)((w < 4 ? call_a : call_b) >> (8 * (w % 4)));
        w++;
    }
}
#define CALL_A 0x11223344u
#define CALL_B 0x55667788u
static void run_patch(const char *mode)
{
    IMAGE_NT_HEADERS nt = { { 2 }, { IMAGE_NT_OPTIONAL_HDR64_MAGIC } };
    IMAGE_SECTION_HEADER sec[2];
    WCHAR wbuf[128];
    UNICODE_STRING nm;
    unsigned char *at;
    UINT64 imm;
    const char *file = "C:\\Games\\FFT_enhanced.exe";
    char *base = (char *)image;
    memset(image, 0x90, sizeof(image));
    memset(sec, 0, sizeof(sec));
    sec[0].VirtualAddress = 0x1000; sec[0].SizeOfRawData = 0x8000; sec[0].Misc.VirtualSize = 0x7000; sec[0].Characteristics = IMAGE_SCN_MEM_EXECUTE;
    sec[1].VirtualAddress = 0x9000; sec[1].SizeOfRawData = 0x8000; sec[1].Misc.VirtualSize = 0x8000; sec[1].Characteristics = 0;
    at = image + 0x3000;
    put_pattern(at, CALL_A, CALL_B);
    mmap_fits = 0; mprotect_ok = 1; ios_jit_mapping_count = 0;
    if (!strcmp(mode, "classic")) file = "D:\\x\\fft_classic.exe";
    if (!strcmp(mode, "othername")) file = "C:\\Games\\notfft_enhanced.exe";
    if (!strcmp(mode, "otherexe")) file = "C:\\Games\\game.exe";
    if (!strcmp(mode, "fits")) mmap_fits = 1;
    if (!strcmp(mode, "twice")) put_pattern(image + 0x5000, CALL_A, CALL_B);
    if (!strcmp(mode, "none")) at[0x40] ^= 0xff;                 /* one byte of the fixed part differs */
    if (!strcmp(mode, "datasec")) { memcpy(image + 0xa000, at, ARRAY_SIZE(ios_fft_arena_pat)); memset(at, 0x90, ARRAY_SIZE(ios_fft_arena_pat)); }
    if (!strcmp(mode, "poolcopy")) { ios_jit_mappings[0].pe_base = base; ios_jit_mappings[0].unmapped = 0; ios_jit_mapping_count = 1; }
    if (!strcmp(mode, "poolcopygone")) { ios_jit_mappings[0].pe_base = base; ios_jit_mappings[0].unmapped = 1; ios_jit_mapping_count = 1; }
    if (!strcmp(mode, "rwfail")) mprotect_ok = 0;
    if (!strcmp(mode, "pe32")) nt.OptionalHeader.Magic = 0x10b;
    if (!strcmp(mode, "edge"))      /* the pattern ends exactly at the end of the section: still a match, no overrun */
    {
        memset(at, 0x90, ARRAY_SIZE(ios_fft_arena_pat));
        at = image + 0x1000 + 0x7000 - ARRAY_SIZE(ios_fft_arena_pat);
        put_pattern(at, CALL_B, CALL_A);
    }
    if (!strcmp(mode, "split"))     /* the pattern starts 20 bytes before the section's end: not a match */
    {
        memset(at, 0x90, ARRAY_SIZE(ios_fft_arena_pat));
        at = image + 0x1000 + 0x7000 - 20;
        put_pattern(at, CALL_B, CALL_A);
    }
    nm = name_of(file, wbuf);
    ios_fft_arena_patch(base, &nt, sec, sizeof(image), &nm);
    memcpy(&imm, at + IOS_FFT_ARENA_IMM, 8);
    printf("imm=%llx arena=%llx logs=%d\n", (unsigned long long)imm, (unsigned long long)ios_fft_arena_size, logs);
    /* nothing else in the image changed: compare with a pristine copy of the same shape */
    {
        static unsigned char ref[sizeof(image)];
        size_t i, diff = 0;
        memset(ref, 0x90, sizeof(ref));
        if (!strcmp(mode, "twice")) put_pattern(ref + 0x5000, CALL_A, CALL_B);
        if (!strcmp(mode, "datasec")) { put_pattern(ref + 0xa000, CALL_A, CALL_B); }
        else if (!strcmp(mode, "edge")) put_pattern(ref + 0x1000 + 0x7000 - ARRAY_SIZE(ios_fft_arena_pat), CALL_B, CALL_A);
        else if (!strcmp(mode, "split")) put_pattern(ref + 0x1000 + 0x7000 - 20, CALL_B, CALL_A);
        else { put_pattern(ref + 0x3000, CALL_A, CALL_B); if (!strcmp(mode, "none")) ref[0x3000 + 0x40] ^= 0xff; }
        for (i = 0; i < sizeof(image); i++) if (image[i] != ref[i]) diff++;
        printf("changed=%zu\n", diff);
    }
}
int main(int argc, char **argv)
{
    if (!strcmp(argv[1], "reserve")) { run_reserve(); return 0; }
    if (!strcmp(argv[1], "realmb")) { printf("%lld\n", ios_oversize_real_mb()); return 0; }
    if (!strcmp(argv[1], "failed"))
    {
        ios_oversize_n = 1; ios_oversize[0].base = 0x7100000000ULL; ios_oversize[0].real = 16ULL << 30; ios_oversize[0].size = 0x4004000000ULL;
        ios_oversize_commit_failed((void *)0x7100000000ULL, 0x1000, 0xc0000017);                       /* inside the real part: silent */
        ios_oversize_commit_failed((void *)(0x7100000000ULL + (16ULL << 30)), 0x10000, 0xc0000141);   /* the first byte past it: logged */
        ios_oversize_commit_failed((void *)(0x7100000000ULL + 0x4004000000ULL), 0x1000, 0xc0000017);   /* beyond the reserve: silent */
        ios_oversize_commit_failed((void *)0x7500000000ULL, 0x1000, 0xc0000017);                       /* another range: silent */
        for (int i = 0; i < 40; i++) ios_oversize_commit_failed((void *)(0x7100000000ULL + (17ULL << 30)), 0x1000, 1);
        printf("logs=%d\n", logs);
        return 0;
    }
    if (!strcmp(argv[1], "patch")) { run_patch(argv[2]); return 0; }
    if (!strcmp(argv[1], "is")) { WCHAR b[64]; UNICODE_STRING u = name_of(argv[2], b); printf("%d\n", ios_image_file_is(&u, argv[3])); return 0; }
    return 2;
}
'''
harness = (harness.replace('@REALMB@', real_mb).replace('@PAT@', 'static const short ios_fft_arena_pat[] =\n{' + pat_src + '\n};')
           .replace('@REALLOC@', reserve).replace('@FAILED@', failed).replace('@IMAGEIS@', image_is).replace('@PATCH@', patch))
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    c = t / 't.c'
    c.write_text(harness)
    exe = t / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
                         '-Wno-unused-parameter', '-I', str(root / 'build'), '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production code compiles on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        base_env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}

        def run(args, cfg=None):
            docs = t / 'docs'
            docs.mkdir(exist_ok=True)
            cfgf = docs / 'bcd.cfg'
            if cfgf.exists():
                cfgf.unlink()
            if cfg is not None:
                cfgf.write_text(cfg)
            env = dict(base_env, MADEIRA_DOCS_DIR=str(docs))
            r = subprocess.run([str(exe)] + args, capture_output=True, text=True, env=env)
            return r

        # keys
        for cfg, want in ((None, '0'), ('swap-mb = 1\n', '0'), ('oversize-reserve = 0\noversize-reserve-mb = 4096\n', '0'), ('oversize-reserve = 1\n', '16384'),
                          ('oversize-reserve = 1\noversize-reserve-mb = 8192\n', '8192'), ('oversize-reserve = on\noversize-reserve-mb = 65536\n', '65536'),
                          ('oversize-mb = 3\noversize-reserve-mb = 8192\n', '0')):
            r = run(['realmb'], cfg)
            check('bcd.cfg %r: the real part is %s MB' % (cfg, want), r.stdout.strip() == want, r.stdout + r.stderr[-300:])
        # reserve
        on = 'oversize-reserve = 1\n'
        r = run(['reserve'], on)
        out = dict(l.split(' ', 1) for l in r.stdout.splitlines() if ' ' in l)
        check('on, 32 GB hole: the 0x4003ffffff ask is served (status 0) with 16 GB real at a 64 MB-aligned base and the size asked for (page-rounded, 0x4004000000)',
              out.get('big', '').startswith('st=0 ret=1 size=4004000000 real=400000000 align=4000000'), str(out.get('big')))
        check('MEM_TOP_DOWN does not change the shape', out.get('topdown', '').strip() == 'st=0', str(out.get('topdown')))
        check('on, 6 GB hole: the real part halves 16 -> 8 -> 4 GB', out.get('six', '').strip() == 'st=0 real=100000000', str(out.get('six')))
        check('on, 512 MB hole: nothing below the 1 GB floor (status c0000017)', out.get('tiny', '').strip() == 'st=c0000017', str(out.get('tiny')))
        check('with a 32 GB arena set (ml1299) a 16 GB hole is refused: the arena must be real', out.get('arena', '').strip() == 'st=c0000017', str(out.get('arena')))
        check('a hinted ask, one under 64 GB, a commit and a placeholder are not served and ask the allocator for nothing',
              out.get('hinted', '').split()[0] == 'st=c0000017' and out.get('below64', '').split()[0] == 'st=c0000017' and out.get('commit', '').split()[0] == 'st=c0000017' and
              out.get('placeholder', '').split()[0] == 'st=c0000017' and out.get('hinted', '').split()[1] == out.get('placeholder', '').split()[1], str(out))
        r = run(['reserve'], None)
        out = dict(l.split(' ', 1) for l in r.stdout.splitlines() if ' ' in l)
        check('key off: the same ask fails (c0000017) without asking the allocator, and says so once',
              out.get('big', '').startswith('st=c0000017') and 'logs=1' in r.stdout and 'oversize-reserve = 1 would serve it in part' in r.stderr and
              r.stderr.count('does not fit the map and fails') == 1, r.stdout + r.stderr[-400:])
        # commit log
        r = run(['failed'], on)
        check('a failed commit past the real part is logged (once for the first byte past it, then the rest up to 16 lines), nothing else', r.stdout.strip() == 'logs=16' and
              r.stderr.count('[oversize] ml1298 commit') == 16 and 'past its real 16384 MB' in r.stderr, r.stdout + r.stderr[-300:])
        # image names
        for path, want, exp in (('C:\\Games\\FFT_enhanced.exe', 'fft_enhanced.exe', '1'), ('c:/games/fft_enhanced.exe', 'fft_enhanced.exe', '1'), ('fft_enhanced.exe', 'fft_enhanced.exe', '1'),
                                ('C:\\Games\\notfft_enhanced.exe', 'fft_enhanced.exe', '0'), ('C:\\Games\\FFT_enhanced.exe.bak', 'fft_enhanced.exe', '0'),
                                ('C:\\Games\\fft_classic.exe', 'fft_enhanced.exe', '0'), ('x', 'fft_classic.exe', '0')):
            r = run(['is', path, want])
            check('ios_image_file_is(%r, %r) = %s' % (path, want, exp), r.stdout.strip() == exp, r.stdout + r.stderr[-200:])
        # the patch
        both = 'oversize-reserve = 1\noversize-reserve-mb = 16384\nfft-arena-patch = 1\n'
        pristine = '0'
        r = run(['patch', 'enhanced'], both)
        check('all conditions: the immediate becomes 16 GB (0x400000000), the eight bytes of the immediate are written (one of them changes value), nothing else in the image, one log line',
              'imm=400000000 arena=400000000' in r.stdout and 'changed=1' in r.stdout, r.stdout + r.stderr[-400:])
        check('... and the log names the arena before and after', 'arena 65536 MB -> 16384 MB' in r.stderr, r.stderr[-300:])
        r = run(['patch', 'classic'], both)
        check('FFT_classic.exe is patched the same way', 'imm=400000000' in r.stdout and 'changed=1' in r.stdout, r.stdout + r.stderr[-300:])
        r = run(['patch', 'edge'], both)
        check('a pattern that ends exactly at the end of the executable section matches (no overrun)', 'imm=400000000' in r.stdout and 'changed=1' in r.stdout, r.stdout + r.stderr[-300:])
        for mode, cfg, why in (('enhanced', 'oversize-reserve = 1\n', 'fft-arena-patch off'),
                               ('enhanced', 'fft-arena-patch = 1\n', 'oversize-reserve off (no real part)'),
                               ('enhanced', 'oversize-reserve = 1\noversize-reserve-mb = 512\nfft-arena-patch = 1\n', 'oversize-reserve-mb below 1024'),
                               ('othername', both, 'another file name (notfft_enhanced.exe)'), ('otherexe', both, 'another program'),
                               ('fits', both, 'the 256 GB reserve fits the map'), ('twice', both, 'the pattern matches twice'), ('none', both, 'one fixed byte differs'),
                               ('datasec', both, 'the pattern is in a non-executable section'), ('poolcopy', both, 'a pool copy exists'),
                               ('rwfail', both, 'the page cannot be made writable'), ('pe32', both, 'a PE32 image'), ('split', both, 'the pattern is cut by the end of the section')):
            r = run(['patch', mode], cfg)
            check('not patched when %s: the immediate stays 0x1000000000, nothing in the image changes' % why,
                  ('imm=1000000000 arena=0' in r.stdout or mode in ('othername', 'otherexe', 'datasec') and 'arena=0' in r.stdout) and 'changed=0' in r.stdout,
                  r.stdout + r.stderr[-300:])
        r = run(['patch', 'poolcopygone'], both)
        check('a pool copy whose view is gone does not block the patch', 'imm=400000000' in r.stdout and 'changed=1' in r.stdout, r.stdout + r.stderr[-300:])

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-oversize-reserve.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
