#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
"""bcd.cfg oversize-reserve-virtual / oversize-scan and the keyless [oversize] OVERLAP / tail lines (round 660 M023 W3ALLOC, agent K7R,
build 663); no Wine runs.

The Witcher 3's DirectX 12 build (5EV18N, GW9R17, build 653, iPhone18,1, 512 GB map) reserves 16, 64 and 32 GB. oversize-reserve = 1 served
the 64 GB one in part (real 6 GB at 0x7040000000, the program told it owns [base, base + 64 GB)); the 32 GB request's 16 GB half then went
to 0x7800000000, inside that range, and the game's free (witcher3.exe+0xbcb703: the 16 GB pool's range, then the 64 GB arena's range, else
the third pool) gave a block of the third pool to the arena's TLSF free, which read a zero previous block (AV READ of 18). Three changes:
  - keyless, log only: what lies in the unmapped part when a reserve is served in part ([oversize] ml1298 tail) and every later
    allocation that lands there ([oversize] ml1298 OVERLAP); a released reserve is forgotten;
  - oversize-reserve-virtual = 1 (off): the real part ends where the emulator's arena begins, so the unmapped part lies only in that arena
    (the steering valve is kept out of it) and above the map's end;
  - oversize-scan = 1 (off, log only): the main program's movabs of the reserve size, for a later arena patch.

Part A reads the sources: the keys, where each hook is, that the log-only code allocates nothing and writes no image byte. Part B compiles
the production functions on the host (ASAN/UBSAN) against a model of the 512 GB map with the sizes and placements of the device logs,
with the keys off and on, and a model of the game's free dispatcher (from the disassembled bytes of 5EV18N).
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
min_fn = function('static unsigned long long ios_oversize_min( void )')
virt_on = function('static int ios_oversize_virtual_on( void )')
scan_bytes = function('static unsigned long long ios_oversize_scan_bytes( void )')
reserve = function('static NTSTATUS ios_oversize_reserve( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect )')
vres = function('static NTSTATUS ios_vres_place( void **ret, SIZE_T *size_ptr, ULONG protect, SIZE_T want, SIZE_T real, SIZE_T floor )')
census = function('static void ios_oversize_tail_census( ULONG_PTR b, SIZE_T real, SIZE_T full )\n{')
note = function('static void ios_oversize_note_alloc( const void *addr, SIZE_T size, ULONG type )')
nfree = function('static void ios_oversize_note_free( HANDLE process, const void *addr, ULONG type )')
failed = function('static void ios_oversize_commit_failed( const void *addr, SIZE_T size, NTSTATUS status )')
steer_top = function('static int ios_steer_top_slot_usable(void)')
hexfn = function('static void ios_oversize_hex( char *out, size_t cap, const unsigned char *p, size_t n )')
scan = function('static void ios_oversize_scan( const char *base, const IMAGE_NT_HEADERS *nt, const IMAGE_SECTION_HEADER *sec,')

check('the keys are plain bcd.cfg keys, off by default; oversize-reserve-virtual needs oversize-reserve; the scan size defaults to 64 GB',
      'ios_oversize_real_mb() > 0 && madeira_cfg_bool( "oversize-reserve-virtual", 0 )' in virt_on and
      'madeira_cfg_bool( "oversize-scan", 0 )' in scan_bytes and 'madeira_cfg_int( "oversize-scan-mb", 65536 )' in scan_bytes)
call = 'if (ios_oversize_virtual_on() && want >= IOS_OVERSIZE_MIN && !ios_vres_place( ret, size_ptr, protect, want, real, floor ))\n        return STATUS_SUCCESS;'
check('ios_vres_place is called once, from ios_oversize_reserve, only with the key and for 64 GB or more, before the old serving loop',
      src.count('ios_vres_place(') == 2 and call in reserve and reserve.index(call) < reserve.index('for (; real >= floor; real >>= 1)') and
      reserve.index('if (cap_mb <= 0)') < reserve.index(call))
check('the old serving loop is unchanged; it only adds the tail census after its own line',
      'if (allocate_virtual_memory( &base, &sz, type, protect, 0, 0, IOS_OVERSIZE_ALIGN, 0 )) continue;' in reserve and
      reserve.count('ios_oversize_tail_census(') == 1 and
      reserve.index('"is not mapped\\n"') < reserve.index('ios_oversize_tail_census( (ULONG_PTR)base, sz, full );') < reserve.index('*ret = base;'))
check('the steering valve: the anchor check comes first and only returns 0; the arena-guard part follows unchanged',
      steer_top.index('__atomic_load_n( &ios_vres.base, __ATOMIC_ACQUIRE )') < steer_top.index('if (!ios_arena_guard_enabled()) return 1;') and
      'if (!ios_fex_arena_base_unix || ios_fex_arena_end_unix <= ios_steer_slot ||' in steer_top and src.count('ios_steer_top_slot_usable()') == 1)
wrapper = function('NTSTATUS WINAPI NtAllocateVirtualMemory( HANDLE process, PVOID *ret, ULONG_PTR zero_bits,')
check('the hooks: the note at the end of NtAllocateVirtualMemory for every success of the current process (the early answers of the '
      'big-reserve path included), the release note at NtFreeVirtualMemory, the scan right after the FFT arena patch in map_image_into_view',
      src.count('ios_oversize_note_alloc( *ret, *size_ptr, type );') == 1 and
      'if (NT_ERROR( status ))\n        ios_ntfail_alloc( "NtAllocateVirtualMemory", status, process, hint, want, type, protect, zero_bits, NULL, 0 );\n'
      in wrapper and
      'else if (process == NtCurrentProcess() && ret && size_ptr) ios_oversize_note_alloc( *ret, *size_ptr, type );\n    return status;\n}' in wrapper and
      src.count('ios_oversize_note_free( process, addr, type );') == 1 and
      src.index('    ios_vm_note_free( addr, size, type );\n    ios_oversize_note_free( process, addr, type );') > 0 and
      src.count('ios_oversize_scan( ptr, nt, sec, total_size, nt_name );') == 1 and
      src.index('ios_fft_arena_patch( ptr, nt, sec, total_size, nt_name );') < src.index('ios_oversize_scan( ptr, nt, sec, total_size, nt_name );') <
      src.index('    /* set the image protections */\n\n#ifdef WINE_IOS\n    /* madeira-bcd [wow-cpu-pool]'))
alloc_calls = ('allocate_virtual_memory(', 'mmap(', 'munmap(', 'mprotect(', 'NtAllocateVirtualMemory', 'NtFreeVirtualMemory', 'map_view(', 'delete_view(')
check('the keyless lines are log only: the tail census, the OVERLAP note and the release note place, free or protect nothing',
      not any(t in fn for fn in (census, note, nfree) for t in alloc_calls))
first_if = note[note.index('    if ('):]
first_if = first_if[:first_if.index(';') + 1]
check('the note returns at its first test while no reserve was served in part (every game without oversize-reserve = 1)',
      first_if.startswith('    if (!__atomic_load_n( &ios_oversize_n, __ATOMIC_RELAXED ) ||') and first_if.endswith('return;'), first_if)
check('the scan writes no byte of the image and asks for no memory: one memcpy into its own 8 bytes, no mprotect, no allocation',
      scan.count('memcpy(') == 1 and 'memcpy( imm, &want, sizeof(imm) );' in scan and not any(t in scan for t in alloc_calls) and
      'if (!want ||' in scan and 'IMAGE_FILE_DLL' in scan and 'IMAGE_NT_OPTIONAL_HDR64_MAGIC' in scan)
check('ios_vres_place: anchored where the emulator\'s arena begins, only when the arena ends at the map\'s end, one anchor at a time, never '
      'over a steered reserve of this program; a result off the slot is released',
      'end != ceil' in vres and 'void *b = (void *)(top - r);' in vres and 'another reserve already holds the top of the band' in vres and
      'a steered reserve of this program already lies in the emulator\'s arena' in vres and
      'NtFreeVirtualMemory( NtCurrentProcess(), &b, &zero, MEM_RELEASE );' in vres and vres.count('allocate_virtual_memory(') == 1)
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog describes the three keys', all('"%s": {' % k in cat for k in ('oversize-reserve-virtual', 'oversize-scan', 'oversize-scan-mb')))

# ---------------------------------------------------------------- Part B: compiled
harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include <pthread.h>
#include <signal.h>
#include "madeira_cfg.h"
typedef uintptr_t ULONG_PTR, UINT_PTR;
typedef uint64_t UINT64;
typedef size_t SIZE_T;
typedef unsigned int ULONG, NTSTATUS, DWORD;
typedef unsigned short USHORT, WORD;
typedef uint16_t WCHAR;
typedef void *HANDLE;
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017u
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018u
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define MEM_RELEASE 0x8000
#define MEM_TOP_DOWN 0x100000
#define PAGE_NOACCESS 1
#define PAGE_READWRITE 4
#define IMAGE_NT_OPTIONAL_HDR64_MAGIC 0x20b
#define IMAGE_SCN_CNT_INITIALIZED_DATA 0x40
#define IMAGE_SCN_MEM_EXECUTE 0x20000000
#define IMAGE_FILE_DLL 0x2000
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define ROUND_SIZE(addr,size,mask) (((SIZE_T)(size) + ((UINT_PTR)(addr) & (mask)) + (mask)) & ~(UINT_PTR)(mask))
#define NtCurrentProcess() ((HANDLE)~(ULONG_PTR)0)
static int logs;
static char logbuf[1 << 16]; static size_t logpos;
#define dprintf(fd, ...) do { char _l[4096]; int _n = snprintf(_l, sizeof(_l), __VA_ARGS__); logs++; fputs(_l, stderr); \
    if (_n > 0 && logpos + (size_t)_n < sizeof(logbuf)) { memcpy(logbuf + logpos, _l, (size_t)_n); logpos += (size_t)_n; } } while (0)
static UINT_PTR page_mask = 0xfff;
static void *host_addr_space_limit;
ULONG_PTR ios_fex_arena_base_unix, ios_fex_arena_end_unix;
static int guard_on;
static int ios_arena_guard_enabled(void) { return guard_on; }
static const ULONG_PTR ios_steer_slot = 0x7C00000000;
typedef struct { int dummy; } sigset_t_unused;
static int mutex_depth;
static void server_enter_uninterrupted_section( pthread_mutex_t *m, sigset_t *s ) { (void)m; (void)s; mutex_depth++; }
static void server_leave_uninterrupted_section( pthread_mutex_t *m, sigset_t *s ) { (void)m; (void)s; mutex_depth--; }
static pthread_mutex_t virtual_mutex = PTHREAD_MUTEX_INITIALIZER;
/* the model address map: views (sorted), the FEX arena, the ceiling */
struct rb_entry { int idx; };
struct file_view { struct rb_entry entry; void *base; size_t size; void *ios_owner; };
#define RB_ENTRY_VALUE(e, type, field) ((type *)((char *)(e) - offsetof(type, field)))
#define MAXV 256
static struct file_view views[MAXV]; static int nviews;
static void *cur_peb = (void *)0x10a960000ULL;
static void *ios_jit_current_peb(void) { return cur_peb; }
static void sortv(void) { int i, j; for (i = 1; i < nviews; i++) for (j = i; j > 0 && views[j - 1].base > views[j].base; j--) { struct file_view t = views[j]; views[j] = views[j - 1]; views[j - 1] = t; } for (i = 0; i < nviews; i++) views[i].entry.idx = i; }
static void addv(ULONG_PTR b, SIZE_T s, void *owner) { views[nviews].base = (void *)b; views[nviews].size = s; views[nviews].ios_owner = owner; nviews++; sortv(); }
static int delv(ULONG_PTR b) { int i; for (i = 0; i < nviews; i++) if ((ULONG_PTR)views[i].base == b) { views[i] = views[--nviews]; sortv(); return 1; } return 0; }
static struct file_view *find_view( const void *addr, size_t size ) { int i; (void)size; for (i = 0; i < nviews; i++) if ((const char *)addr >= (char *)views[i].base && (const char *)addr < (char *)views[i].base + views[i].size) return &views[i]; return NULL; }
static struct file_view *ios_lowest_view_in( char *lo, char *hi ) { int i; for (i = 0; i < nviews; i++) if ((char *)views[i].base + views[i].size > lo && (char *)views[i].base < hi) return &views[i]; return NULL; }
static struct rb_entry *rb_next( struct rb_entry *e ) { return e->idx + 1 < nviews ? &views[e->idx + 1].entry : NULL; }
static ULONG_PTR map_floor = 0x7000000000ULL;   /* the 512 GB map's band; 0x300000000 on a 63 GB map */
static int range_free(ULONG_PTR b, SIZE_T s)
{
    int i;
    if (b + s > (ULONG_PTR)host_addr_space_limit || b < map_floor) return 0;
    if (ios_fex_arena_end_unix && b < ios_fex_arena_end_unix && b + s > ios_fex_arena_base_unix) return 0;   /* FEX_ONLY: generic never */
    for (i = 0; i < nviews; i++) if (b < (ULONG_PTR)views[i].base + views[i].size && b + s > (ULONG_PTR)views[i].base) return 0;
    return 1;
}
static int calls; static char calllog[8192]; static size_t callpos;
static NTSTATUS allocate_virtual_memory( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect, ULONG_PTR limit_low, ULONG_PTR limit_high, ULONG_PTR align, ULONG attributes )
{
    SIZE_T s = ROUND_SIZE(0, *size_ptr, page_mask);
    ULONG_PTR a, step = align ? align : 0x10000;
    (void)protect; (void)limit_low; (void)limit_high; (void)attributes;
    calls++;
    callpos += (size_t)snprintf(calllog + callpos, sizeof(calllog) - callpos, "[%p+%zx t%x a%lx]", *ret, s, type, (unsigned long)align);
    if (*ret) { a = (ULONG_PTR)*ret; if (!range_free(a, s)) return STATUS_CONFLICTING_ADDRESSES; }
    else
    {
        for (a = (map_floor + step - 1) & ~(step - 1); a + s <= (ULONG_PTR)host_addr_space_limit; a += step) if (range_free(a, s)) break;
        if (a + s > (ULONG_PTR)host_addr_space_limit) return STATUS_NO_MEMORY;
    }
    addv(a, s, cur_peb);
    *ret = (void *)a; *size_ptr = s;
    return STATUS_SUCCESS;
}
static NTSTATUS NtFreeVirtualMemory( HANDLE process, void **addr, SIZE_T *size, ULONG type ) { (void)process; (void)size; (void)type; return delv((ULONG_PTR)*addr) ? 0 : 0xc0000019u; }
static struct { uint64_t base; uint64_t size; unsigned tid; unsigned freed; unsigned inuse; } ios_steer[8];
static unsigned ios_steer_n;
static SIZE_T ios_fft_arena_size;
#define IOS_OVERSIZE_MIN   0x1000000000ULL
#define IOS_OVERSIZE_ALIGN 0x4000000UL
#define IOS_OVERSIZE_SLOTS 8
static struct { uintptr_t base; SIZE_T real, size; } ios_oversize[IOS_OVERSIZE_SLOTS];
static unsigned ios_oversize_n;
static pthread_mutex_t ios_oversize_lock = PTHREAD_MUTEX_INITIALIZER;
struct thread_env { struct { void *UniqueProcess, *UniqueThread; } ClientId; };
static struct thread_env teb = { { 0, (void *)0xb0 } };
#define NtCurrentTeb() (&teb)
static struct { ULONG_PTR base, real, size, top, end; void *peb; } ios_vres;
static void ios_oversize_tail_census( ULONG_PTR b, SIZE_T real, SIZE_T full );
static void ios_oversize_note_free( HANDLE process, const void *addr, ULONG type );
/* image model for the scan */
typedef struct { USHORT Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
static const char *debugstr_us( const UNICODE_STRING *u ) { (void)u; return "L\"witcher3.exe\""; }
typedef struct { WORD NumberOfSections; WORD Characteristics; } FILEHDR;
typedef struct { WORD Magic; } OPTHDR;
typedef struct { FILEHDR FileHeader; OPTHDR OptionalHeader; } IMAGE_NT_HEADERS;
typedef struct { DWORD VirtualAddress; DWORD SizeOfRawData; struct { DWORD VirtualSize; } Misc; DWORD Characteristics; } IMAGE_SECTION_HEADER;
@FUNCS@
/* ------------------------------------------------------------------ scenarios */
static ULONG_PTR A_base, B_base, C_base; static SIZE_T A_size = 16ULL << 30, B_size = 64ULL << 30, C_size = 32ULL << 30, C_real;
static void map_512(ULONG_PTR hole_at)
{
    nviews = 0; ios_oversize_n = 0; memset(ios_oversize, 0, sizeof(ios_oversize)); memset(&ios_vres, 0, sizeof(ios_vres)); ios_steer_n = 0; map_floor = 0x7000000000ULL;
    host_addr_space_limit = (void *)0x8000000000ULL; ios_fex_arena_base_unix = 0x7c00000000ULL; ios_fex_arena_end_unix = 0x8000000000ULL;
    addv(0x7000000000ULL, hole_at - 0x7000000000ULL, NULL);                    /* the JIT pool's RW alias and Wine's furniture */
    addv(0x71c0000000ULL, 0x40000000ULL, cur_peb);                             /* images, TEBs, stacks below the cage */
    addv(0x7200000000ULL, 0x1ffff0000ULL, NULL);                               /* the V8 cage holdback [0x7200000000, 0x73ffff0000) */
}
/* jumbo-half value 2 for the 32 GB request (virtual_ios.c's search order: half-aligned slots from 0x7038000000 below 0x7c00000000, then 1/4) */
static ULONG_PTR jumbo_half2(SIZE_T want, SIZE_T *got)
{
    SIZE_T f;
    for (f = want / 2; f >= 0x100000000ULL; f /= 2)
    {
        ULONG_PTR a;
        for (a = (0x7038000000ULL + f - 1) & ~(f - 1); a + f <= 0x7c00000000ULL; a += f)
        {
            void *p = (void *)a; SIZE_T s = f;
            if (!allocate_virtual_memory(&p, &s, MEM_RESERVE, PAGE_READWRITE, 0, 0, 0, 0)) { *got = s; return a; }
        }
    }
    *got = 0; return 0;
}
/* witcher3.exe+0xbcb703 (5EV18N L16305-L16307, disassembled): [mgr+0xc900, mgr+0xc908) first, then [mgr+0xcda8, mgr+0xcdb0), else the third */
static char route(ULONG_PTR p)
{
    if (p - A_base < A_size) return 'A';
    if (p - B_base < B_size) return 'B';
    return 'C';
}
static void witcher3(const char *cfgname, ULONG_PTR hole_at)
{
    void *ret; SIZE_T sz; NTSTATUS st; int i;
    map_512(hole_at);
    /* (1) 16 GB: kernel pick at 0x73ffff0000 (jumbo#1 in both logs) */
    addv(0x73ffff0000ULL, A_size, cur_peb); A_base = 0x73ffff0000ULL;
    /* (2) 64 GB: every real placement fails (largest gap 16 GB), then ios_oversize_reserve */
    ret = NULL; sz = B_size; calls = 0; callpos = 0;
    st = ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_READWRITE);
    B_base = (ULONG_PTR)ret;
    printf("%s B st=%x base=%lx size=%lx real=%lx calls=%d %s\n", cfgname, st, (unsigned long)B_base, (unsigned long)sz,
           (unsigned long)(ios_oversize_n ? ios_oversize[0].real : 0), calls, calllog);
    /* (3) 32 GB: kernel pick fails; jumbo-half value 2 */
    C_base = jumbo_half2(C_size, &C_real);
    if (C_base) ios_oversize_note_alloc((void *)C_base, C_real, MEM_RESERVE);
    printf("%s C base=%lx real=%lx\n", cfgname, (unsigned long)C_base, (unsigned long)C_real);
    /* (4) the 1, 1, 3 GB and 500 MB reserves (jumbo#2-#4, [bigres] #7), unhinted */
    {
        static const SIZE_T more[] = { 0x1f400000ULL, 0x40000000ULL, 0x40000000ULL, 0xc0000000ULL };
        for (i = 0; i < 4; i++)
        {
            void *p = NULL; SIZE_T s = more[i];
            if (!allocate_virtual_memory(&p, &s, MEM_RESERVE, PAGE_READWRITE, 0, 0, 0, 0)) ios_oversize_note_alloc(p, s, MEM_RESERVE);
        }
    }
    /* (5) the free of a block of the third pool at +0x5260000 into it (5EV18N's 0x7805260000) and of the arena's own first block */
    printf("%s route C-block=%c B-block=%c A-block=%c overlap-lines=%d steer-top=%d\n", cfgname,
           C_base ? route(C_base + 0x5260000ULL) : '-', route(B_base + 0x80ULL), route(A_base + 0x1000ULL),
           (int)(strstr(logbuf, "OVERLAP") != NULL), ios_steer_top_slot_usable());
}
static void iw(void)
{
    void *ret = NULL; SIZE_T sz = 0x1140000000ULL; NTSTATUS st; ULONG_PTR commit;
    map_512(0x7040000000ULL);
    addv(0x7040000000ULL, 0x1c0000000ULL - 0x40000000ULL, NULL);   /* XKT0AD: slot#0 dirty below the cage */
    calls = 0; callpos = 0;
    st = ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_READWRITE);
    commit = (ULONG_PTR)ret + (34304ULL << 20);   /* XKT0AD L15929: the game commits 34304 MB into the reserve */
    printf("iw st=%x base=%lx size=%lx real=%lx commit=%lx real-there=%d\n", st, (unsigned long)(ULONG_PTR)ret, (unsigned long)sz,
           (unsigned long)(ios_oversize_n ? ios_oversize[0].real : 0), (unsigned long)commit,
           (int)(commit >= (ULONG_PTR)ret && commit < (ULONG_PTR)ret + (ios_oversize_n ? ios_oversize[0].real : 0)));
}
static void plain(const char *name, unsigned long long want, int layout)
{
    void *ret = NULL; SIZE_T sz = want; NTSTATUS st;
    map_512(0x7040000000ULL);
    addv(0x7040000000ULL, 0x180000000ULL, NULL);
    if (layout == 63)        /* iOS 26: a 63 GB map, the arena in its middle, Wine's furniture at its top */
    {
        nviews = 0; host_addr_space_limit = (void *)0xfc0000000ULL; ios_fex_arena_base_unix = 0xb30000000ULL; ios_fex_arena_end_unix = 0xd30000000ULL;
        map_floor = 0x300000000ULL; addv(0xd30000000ULL, 0x290000000ULL, cur_peb);   /* VCBB6X: [0x300000000, 0xfc0000000), Wine's furniture at the top */
    }
    if (layout == 2)         /* Social Club layout 2: the arena from 0x7d00000000, its own hold below */
    {
        ios_fex_arena_base_unix = 0x7d00000000ULL; addv(0x7c00000000ULL, 0x100000000ULL, NULL);
    }
    if (layout == 3)         /* a steered reserve of this program already in the arena */
    {
        ios_steer[0].base = 0x7c05400000ULL; ios_steer[0].size = 0x2000000; ios_steer_n = 1; addv(0x7c05400000ULL, 0x2000000, cur_peb);
    }
    if (layout == 4)         /* a steered reserve of another program in the arena */
    {
        ios_steer[0].base = 0x7c05400000ULL; ios_steer[0].size = 0x2000000; ios_steer_n = 1; addv(0x7c05400000ULL, 0x2000000, (void *)0x1234000ULL);
    }
    calls = 0; callpos = 0;
    st = ios_oversize_reserve(&ret, &sz, MEM_RESERVE, PAGE_READWRITE);
    printf("%s st=%x base=%lx size=%lx real=%lx calls=%d n=%u anchor=%lx\n", name, st, (unsigned long)(ULONG_PTR)ret, (unsigned long)sz,
           (unsigned long)(ios_oversize_n ? ios_oversize[(ios_oversize_n - 1) % IOS_OVERSIZE_SLOTS].real : 0), calls, ios_oversize_n,
           (unsigned long)ios_vres.base);
}
static void twice(void)
{
    void *r1 = NULL, *r2 = NULL, *r3 = NULL; SIZE_T s1 = B_size, s2 = B_size, s3 = B_size, z = 0;
    map_512(0x7040000000ULL); addv(0x7040000000ULL, 0x180000000ULL, NULL);
    ios_oversize_reserve(&r1, &s1, MEM_RESERVE, PAGE_READWRITE);
    ios_oversize_reserve(&r2, &s2, MEM_RESERVE, PAGE_READWRITE);      /* the anchor lives: served as without the key */
    NtFreeVirtualMemory(NtCurrentProcess(), &r1, &z, MEM_RELEASE); ios_oversize_note_free(NtCurrentProcess(), r1, MEM_RELEASE);
    NtFreeVirtualMemory(NtCurrentProcess(), &r2, &z, MEM_RELEASE); ios_oversize_note_free(NtCurrentProcess(), r2, MEM_RELEASE);
    ios_oversize_reserve(&r3, &s3, MEM_RESERVE, PAGE_READWRITE);      /* released: the top is free again */
    printf("twice r1=%lx r2=%lx r3=%lx anchor=%lx\n", (unsigned long)(ULONG_PTR)r1, (unsigned long)(ULONG_PTR)r2, (unsigned long)(ULONG_PTR)r3,
           (unsigned long)ios_vres.base);
}
static void notes(void)
{
    int i;
    map_512(0x7040000000ULL);
    ios_oversize_n = 1; ios_oversize[0].base = 0x7040000000ULL; ios_oversize[0].real = 6ULL << 30; ios_oversize[0].size = 64ULL << 30;
    ios_oversize_note_alloc((void *)0x7040000000ULL, 64ULL << 30, MEM_RESERVE);               /* the reserve itself: silent */
    ios_oversize_note_alloc((void *)0x7040000000ULL, 0x40000000ULL, MEM_COMMIT);              /* inside the real part: silent */
    ios_oversize_note_alloc((void *)0x7c40000000ULL, 0x10000, MEM_COMMIT);                    /* the emulator's own pages: silent */
    ios_oversize_note_alloc((void *)0x8040000000ULL, 0x10000, MEM_RESERVE);                   /* past the reserve: silent */
    ios_oversize_note_alloc((void *)0x6f00000000ULL, 0x10000, MEM_RESERVE);                   /* below it: silent */
    printf("silent logs=%d\n", logs);
    ios_oversize_note_alloc((void *)0x7800000000ULL, 16ULL << 30, MEM_RESERVE);               /* the 32 GB request's half: one line */
    ios_oversize_note_alloc((void *)0x7c74400000ULL, 0x30000000ULL, MEM_RESERVE);             /* a steered 768 MB reserve in the arena: one line */
    for (i = 0; i < 40; i++) ios_oversize_note_alloc((void *)0x7240010000ULL, 0x40000000ULL, MEM_RESERVE);
    printf("lines logs=%d\n", logs);
    ios_oversize_note_free(NtCurrentProcess(), (void *)0x7040000000ULL, MEM_RELEASE);
    printf("freed base=%lx\n", (unsigned long)ios_oversize[0].base);
}
static void none(void)
{
    /* no reserve served in part: the note does nothing at all */
    ios_oversize_n = 0; logs = 0; logpos = 0;
    ios_oversize_note_alloc((void *)0x7800000000ULL, 16ULL << 30, MEM_RESERVE);
    ios_oversize_note_free(NtCurrentProcess(), (void *)0x7800000000ULL, MEM_RELEASE);
    printf("none logs=%d\n", logs);
}
static void steer(void)
{
    int g;
    host_addr_space_limit = (void *)0x8000000000ULL; ios_fex_arena_base_unix = 0x7c00000000ULL; ios_fex_arena_end_unix = 0x8000000000ULL;
    memset(&ios_vres, 0, sizeof(ios_vres));
    for (g = 0; g < 2; g++) { guard_on = g; printf("steer guard=%d anchor=0 -> %d\n", g, ios_steer_top_slot_usable()); }
    ios_vres.base = 0x7a80000000ULL;
    for (g = 0; g < 2; g++) { guard_on = g; printf("steer guard=%d anchor=1 -> %d\n", g, ios_steer_top_slot_usable()); }
    guard_on = 0; ios_fex_arena_base_unix = 0xb30000000ULL; ios_fex_arena_end_unix = 0xd30000000ULL; memset(&ios_vres, 0, sizeof(ios_vres));
    printf("steer 63GB anchor=0 -> %d\n", ios_steer_top_slot_usable());
}
static unsigned char img[0x40000];
static void scanimg(const char *mode)
{
    IMAGE_NT_HEADERS nt = { { 2, 0 }, { IMAGE_NT_OPTIONAL_HDR64_MAGIC } };
    IMAGE_SECTION_HEADER sec[2];
    UNICODE_STRING nm = { 0, 0, NULL };
    static const unsigned char mov[] = { 0x48, 0xb8, 0, 0, 0, 0, 0x10, 0, 0, 0 };      /* movabs rax, 0x1000000000 */
    static const unsigned char mov9[] = { 0x49, 0xb9, 0, 0, 0, 0, 0x10, 0, 0, 0 };     /* movabs r9, 0x1000000000 */
    static unsigned char ref[sizeof(img)];
    size_t i, diff = 0;
    memset(img, 0xcc, sizeof(img)); memset(sec, 0, sizeof(sec));
    sec[0].VirtualAddress = 0x1000; sec[0].SizeOfRawData = 0x10000; sec[0].Misc.VirtualSize = 0x10000; sec[0].Characteristics = IMAGE_SCN_MEM_EXECUTE;
    sec[1].VirtualAddress = 0x20000; sec[1].SizeOfRawData = 0x8000; sec[1].Misc.VirtualSize = 0x9000; sec[1].Characteristics = IMAGE_SCN_CNT_INITIALIZED_DATA;
    memcpy(img + 0x1000 + 0x123, mov, 10);
    memcpy(img + 0x1000 + 0x10000 - 10, mov9, 10);                 /* ends exactly at the section's end */
    memcpy(img + 0x1000 + 0x10000 - 8, mov, 8);                    /* cut by the section's end: not a hit (overwrites mov9's tail) */
    memcpy(img + 0x1000 + 0x10000 - 10, mov9, 10);
    memcpy(img + 0x20000 + 0x40, mov + 2, 8);                      /* raw 8-aligned copy in data */
    memcpy(img + 0x20000 + 0x45, mov + 2, 8);                      /* unaligned copy in data: not counted */
    memcpy(img + 0x30000, mov, 10);                                /* past every section: not read */
    if (!strcmp(mode, "dll")) nt.FileHeader.Characteristics = IMAGE_FILE_DLL;
    if (!strcmp(mode, "pe32")) nt.OptionalHeader.Magic = 0x10b;
    memcpy(ref, img, sizeof(img));
    logs = 0; logpos = 0; memset(logbuf, 0, sizeof(logbuf));
    ios_oversize_scan((const char *)img, &nt, sec, sizeof(img), &nm);
    for (i = 0; i < sizeof(img); i++) if (img[i] != ref[i]) diff++;
    printf("scan %s logs=%d changed=%zu code=%d data=%d\n", mode, logs, diff, (int)(strstr(logbuf, "2 movabs") != NULL), (int)(strstr(logbuf, "1 copies") != NULL));
}
int main(int argc, char **argv)
{
    if (!strcmp(argv[1], "w3")) { witcher3(argv[2], strtoull(argv[3], NULL, 16)); return 0; }
    if (!strcmp(argv[1], "iw")) { iw(); return 0; }
    if (!strcmp(argv[1], "plain")) { plain(argv[2], strtoull(argv[3], NULL, 16), atoi(argv[4])); return 0; }
    if (!strcmp(argv[1], "twice")) { twice(); return 0; }
    if (!strcmp(argv[1], "notes")) { notes(); return 0; }
    if (!strcmp(argv[1], "none")) { none(); return 0; }
    if (!strcmp(argv[1], "steer")) { steer(); return 0; }
    if (!strcmp(argv[1], "scan")) { scanimg(argv[2]); return 0; }
    if (!strcmp(argv[1], "keys")) { printf("virt=%d scan=%llu\n", ios_oversize_virtual_on(), (unsigned long long)ios_oversize_scan_bytes()); return 0; }
    return 2;
}
'''
funcs = '\n'.join((real_mb, min_fn, virt_on, scan_bytes, failed, steer_top, vres, reserve, census, note, nfree, hexfn, scan))
harness = harness.replace('@FUNCS@', funcs)
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / 't.c').write_text(harness)
    exe = t / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-Wall',
                         '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-parameter', '-Wno-unused-but-set-variable', '-I', str(root / 'build'),
                         '-o', str(exe), str(t / 't.c')], capture_output=True, text=True)
    check('the production code compiles on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        base_env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}

        def run(args, cfg=None):
            docs = t / 'docs'
            docs.mkdir(exist_ok=True)
            f = docs / 'bcd.cfg'
            if f.exists():
                f.unlink()
            if cfg is not None:
                f.write_text(cfg)
            r = subprocess.run([str(exe)] + args, capture_output=True, text=True, env=dict(base_env, MADEIRA_DOCS_DIR=str(docs)))
            if r.returncode:
                print(r.stderr[-2000:])
            return r

        old = 'oversize-reserve = 1\noversize-reserve-mb = 6144\n'
        new = old + 'oversize-reserve-virtual = 1\n'
        for cfg, want in ((None, 'virt=0 scan=0'), (old, 'virt=0 scan=0'), ('oversize-reserve-virtual = 1\n', 'virt=0 scan=0'), (new, 'virt=1 scan=0'),
                          ('oversize-scan = 1\n', 'virt=0 scan=68719476736'), ('oversize-scan = 1\noversize-scan-mb = 70656\n', 'virt=0 scan=74088185856'),
                          ('oversize-scan-mb = 70656\n', 'virt=0 scan=0')):
            r = run(['keys'], cfg)
            check('bcd.cfg %r: %s' % (cfg, want), r.stdout.strip() == want, r.stdout + r.stderr[-300:])
        # The Witcher 3 (5EV18N: 6 GB hole at 0x7040000000; GW9R17: at 0x703c000000)
        for log, hole in (('5EV18N', '7040000000'), ('GW9R17', '703c000000')):
            r = run(['w3', 'off', hole], old)
            out = r.stdout
            check('%s, the list\'s lines (key off): the 64 GB reserve is served exactly as the device log says (real 0x%s+6144 MB, 64 GB reported, one '
                  'allocator call at 64 MB alignment)' % (log, hole),
                  ('off B st=0 base=%s size=1000000000 real=180000000 calls=1 [0x0+180000000 t2000 a4000000]' % hole) in out, out + r.stderr[-500:])
            check('%s, key off: the 32 GB request\'s half goes to 0x7800000000 (16 GB), inside the claimed 64 GB, as in the log' % log,
                  'off C base=7800000000 real=400000000' in out, out)
            check('%s, key off: the game\'s free gives a block of the third pool to the 64 GB arena (the crash), and the OVERLAP lines name it' % log,
                  'off route C-block=B B-block=B A-block=A overlap-lines=1' in out and '[oversize] ml1298 tail r663' in r.stderr, out + r.stderr[-800:])
            r = run(['w3', 'on', hole], new)
            out = r.stdout
            check('%s, oversize-reserve-virtual = 1: the real 6 GB ends where the emulator\'s arena begins (0x7a80000000), 64 GB reported' % log,
                  'on B st=0 base=7a80000000 size=1000000000 real=180000000 calls=1 [0x7a80000000+180000000 t2000 a0]' in out, out + r.stderr[-500:])
            check('%s, key on: the unmapped part holds no view outside the arena when it is served' % log,
                  'holds 0 views (0 MB, 0 of this program) outside the emulator\'s arena' in r.stderr, r.stderr[-800:])
            check('%s, key on: the 32 GB request gets 8 GB at 0x7800000000 (jumbo-half value 2, below the anchor)' % log,
                  'on C base=7800000000 real=200000000' in out, out)
            check('%s, key on: every pool\'s block goes to its own allocator, no OVERLAP line, the steering valve keeps out of the arena' % log,
                  'on route C-block=C B-block=B A-block=A overlap-lines=0 steer-top=0' in out, out + r.stderr[-800:])
        # Call of Duty: Infinite Warfare (XKT0AD): neither serving puts real memory where the game commits
        r = run(['iw'], 'oversize-reserve = 1\n')
        check('IW, key off: real 16 GB at 0x7400000000 (the log\'s line), the commit 34304 MB in lies in the arena, not in the real part',
              'iw st=0 base=7400000000 size=1140000000 real=400000000 commit=7c60000000 real-there=0' in r.stdout, r.stdout + r.stderr[-400:])
        r = run(['iw'], 'oversize-reserve = 1\noversize-reserve-virtual = 1\n')
        check('IW, key on: real 16 GB at 0x7800000000; the commit 34304 MB in lies above the map\'s end: not solved by this key (documented)',
              'iw st=0 base=7800000000 size=1140000000 real=400000000 commit=8060000000 real-there=0' in r.stdout, r.stdout + r.stderr[-400:])
        # sizes from the logs, key off and on, other layouts
        on = 'oversize-reserve = 1\noversize-reserve-virtual = 1\n'
        cases = (
            ('hitman-off', '1000000000', 0, 'oversize-reserve = 1\n', 'st=0 base=7400000000 size=1000000000 real=400000000 calls=1 n=1 anchor=0'),
            ('hitman-on', '1000000000', 0, on, 'st=0 base=7800000000 size=1000000000 real=400000000 calls=1 n=1 anchor=7800000000'),
            ('rockstar-512g-off', '8000000000', 0, None, 'st=c0000017 base=0 size=8000000000 real=0 calls=0 n=0 anchor=0'),
            ('rockstar-512g-virtual-only', '8000000000', 0, 'oversize-reserve-virtual = 1\n', 'st=c0000017 base=0 size=8000000000 real=0 calls=0 n=0 anchor=0'),
            ('w3-16g', '400000000', 0, on, 'st=c0000017 base=0 size=400000000 real=0 calls=0 n=0 anchor=0'),
            ('w3-32g', '800000000', 0, on, 'st=c0000017 base=0 size=800000000 real=0 calls=0 n=0 anchor=0'),
            ('digimon-256g-off', '4000000000', 0, None, 'st=c0000017 base=0 size=4000000000 real=0 calls=0 n=0 anchor=0'),
            ('ios26-63g-off', '1000000000', 63, 'oversize-reserve = 1\n', 'st=0 base=300000000 size=1000000000 real=400000000 calls=1 n=1 anchor=0'),
            ('ios26-63g-on', '1000000000', 63, on, 'st=0 base=300000000 size=1000000000 real=400000000 calls=1 n=1 anchor=0'),
            ('sc-layout2-on', '1000000000', 2, on, 'st=0 base=7400000000 size=1000000000 real=400000000'),
            ('steered-own-on', '1000000000', 3, on, 'st=0 base=7400000000 size=1000000000 real=400000000 calls=1 n=1 anchor=0'),
            ('steered-other-on', '1000000000', 4, on, 'st=0 base=7800000000 size=1000000000 real=400000000 calls=1 n=1 anchor=7800000000'),
        )
        for name, size, layout, cfg, want in cases:
            r = run(['plain', name, size, str(layout)], cfg)
            check('%s (%s): %s' % (name, size, want), (name + ' ' + want) in r.stdout, r.stdout + r.stderr[-400:])
        r = run(['plain', 'ios26-63g-on', '1000000000', '63'], on)
        check('a 63 GB map: not placed (the arena does not end at the map\'s end), said once, then served as without the key',
              'the emulator\'s arena does not end at the end of the address map' in r.stderr and 'served as without oversize-reserve-virtual' in r.stderr)
        r63off = run(['plain', 'ios26-63g-off', '1000000000', '63'], 'oversize-reserve = 1\n').stdout.split(' ', 1)[1]
        r63on = run(['plain', 'ios26-63g-on', '1000000000', '63'], on).stdout.split(' ', 1)[1]
        check('... and the result is the one without the key', r63off.split(' calls=')[0] == r63on.split(' calls=')[0], r63off + ' / ' + r63on)
        rsc = run(['plain', 'sc-layout2-on', '1000000000', '2'], on)
        check('Social Club layout 2 (the arena from 0x7d00000000 above its own hold): no free slot ends there, served as without the key',
              'no free range of 1 GB or more ends where the emulator\'s arena begins' in rsc.stderr, rsc.stderr[-400:])
        rso = run(['plain', 'steered-own-on', '1000000000', '3'], on)
        check('a steered reserve of this program already in the arena: not anchored, served as without the key',
              'a steered reserve of this program already lies in the emulator\'s arena' in rso.stderr, rso.stderr[-400:])
        r = run(['twice'], on)
        check('one anchor at a time: a second 64 GB reserve is served as without the key; after the release of the first the top is free again',
              'twice r1=7800000000 r2=7400000000 r3=7800000000 anchor=7800000000' in r.stdout, r.stdout + r.stderr[-500:])
        r = run(['notes'], old)
        check('OVERLAP: silent for the reserve itself, its real part, the emulator\'s own commits and ranges outside it',
              'silent logs=0' in r.stdout, r.stdout + r.stderr[-400:])
        check('OVERLAP: the 32 GB half and a steered reserve in the arena are named, at most 16 lines in all; a release forgets the reserve',
              'lines logs=16' in r.stdout and r.stderr.count('[oversize] ml1298 OVERLAP r663: reserve 0x7800000000+0x400000000') == 1 and
              'freed base=0' in r.stdout, r.stdout + r.stderr[-600:])
        r = run(['none'], None)
        check('no reserve served in part: the note and the release note print nothing', 'none logs=0' in r.stdout, r.stdout + r.stderr[-300:])
        r = run(['steer'], None)
        check('the steering valve without an anchor: as before (guard off -> 1, guard on with the arena in the top slot -> 0, a 63 GB map -> 1)',
              'steer guard=0 anchor=0 -> 1' in r.stdout and 'steer guard=1 anchor=0 -> 0' in r.stdout and 'steer 63GB anchor=0 -> 1' in r.stdout, r.stdout)
        check('with an anchor: 0 (out of the arena), whatever the guard', 'steer guard=0 anchor=1 -> 0' in r.stdout and 'steer guard=1 anchor=1 -> 0' in r.stdout, r.stdout)
        for mode, cfg, want in (('exe', 'oversize-scan = 1\n', 'logs=4 changed=0 code=1 data=1'), ('exe', None, 'logs=0 changed=0 code=0 data=0'),
                                ('dll', 'oversize-scan = 1\n', 'logs=0 changed=0'), ('pe32', 'oversize-scan = 1\n', 'logs=0 changed=0')):
            r = run(['scan', mode], cfg)
            check('oversize-scan %s, bcd.cfg %r: %s (nothing in the image changes)' % (mode, cfg, want), ('scan %s %s' % (mode, want)) in r.stdout,
                  r.stdout + r.stderr[-600:])
        r = run(['scan', 'exe'], 'oversize-scan = 1\n')
        check('the scan names both movabs (rax at rva 0x1123, r9 at the section\'s end) and the aligned data copy at rva 0x20040',
              'movabs r0, 0x1000000000 at rva 0x1123' in r.stderr and 'movabs r9, 0x1000000000 at rva 0x10ff6' in r.stderr and
              'the value 0x1000000000 at rva 0x20040 (data)' in r.stderr, r.stderr[-1500:])

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-oversize-virtual.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
