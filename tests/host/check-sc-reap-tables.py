#!/usr/bin/env python3
"""A Social Club helper that ends out of memory gives its V8 reservations back (round 631, agent R4 S1 and S3).

GTA V Enhanced 30WE7B = JYYM0T and EX7S2C (build 626), RDR2 DFRKWK and RS64VG (592), 1HB5J4, 9NNP2Q, 3A27ZN,
S59TH0: every V8 isolate of SocialClubHelper.exe (the page, each ServiceWorker and DedicatedWorker) reserves
3 x 512 MB placeholders; six live isolates fill the band, the 24th ask fails (c0000017), Chromium exits with
0xE0000008, and the dead helper's tables (7.2 GB in 30WE7B) stay mapped: a pseudo-process's views are not unmapped
when it exits. Every restarted helper died within 3 s and the Launcher said "The CEF browser ... could not restart".

S1: the helper's reservations of 128 MB up to 1 GB below the FEX arena are listed; when THAT helper ends with
0xE0000008, its own views in those ranges (create_view tags each view with its maker's PEB) are released before the
next helper starts, after a grace of 3 s. Any other end keeps today's behaviour. env.MADEIRA_SC_REAP_DEAD = 0: off.
S3: with env.MADEIRA_SC_ALIGN1G = 1, a table the band refuses gets a 512 MB slot of the idle 3 GB of that key's hold.

Checks, without Wine:
1. The wiring, textually: the owner tag in struct file_view, set by create_view and kept by remove_pages_from_view
   (the only two alloc_view callers); the ledger noted at both reservation paths; forgotten only on a plain
   MEM_RELEASE (a placeholder split frees nothing) under virtual_mutex; the death hook before the grant ledger's
   early return; the OOM note in NtTerminateProcess's 0xE0000008 branch; the spawn hook before the helper's command
   line; both jumbo-ask reapers; the zone call only after STATUS_NO_MEMORY for the helper's placeholder table ask with
   the key's hold; the zone's re-hold after a release there; the CI step.
2. The production ledger, reaper and zone, compiled against a model view tree, Mach map and clock: notes only the
   helper's 128 MB..1 GB reserves below the arena; a normal exit drops the entries and releases nothing; an
   out-of-memory exit releases exactly the dead helper's views inside its ranges (a view of another owner, one that
   reaches in from outside, an image view and a quarantined stack stay), after the grace (the spawn waits for it;
   the jumbo-ask path leaves young entries); a PEB that runs again is skipped; forget keeps an entry while a piece is
   left; the key's "0" turns it all off; the zone gives the lowest free slot (256 MB: the lower half only), refuses
   when all six are in use, puts a blocked slot's hold back, and holds a slot again once it is empty (a release, or
   the reap of a dead helper's table there).
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
process = (root / 'build/ntdll-unix/process_ios.c').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()


def function(signature, text=native):
    start = text.index(signature + '\n{')
    return text[start:text.index('\n}\n', start) + 3]


# 1. Wiring.
struct = native[native.index('struct file_view\n{'):]
struct = struct[:struct.index('};')]
assert 'void         *ios_owner;' in struct, 'struct file_view needs the owner tag'
cv = function('static NTSTATUS create_view( struct file_view **view_ret, void *base, size_t size, unsigned int vprot )')
assert '    view->ios_flags = 0;\n    view->ios_owner = ios_jit_current_peb();' in cv
rp = function('static NTSTATUS remove_pages_from_view( struct file_view *view, char *base, size_t size )')
assert 'new_view->ios_owner = view->ios_owner;' in rp
assert len(re.findall(r'\balloc_view\(\)', native)) == 2 and native.count('alloc_view(void)') == 1, \
    'alloc_view: the two callers above only, so every view gets its owner'
died = function('static void ios_sc_grants_owner_died( void *peb )')
assert died.index('ios_sc_tbl_owner_died( peb );') < died.index('if (!peb || !ios_sc_grant_n) return;')
note_site = ('        if (!st && *size_ptr >= 0x10000000 && *size_ptr < 0x40000000 && (type & MEM_RESERVE))\n'
             '            ios_bigres_note( *ret, *size_ptr );\n'
             '        if (!st) ios_sc_tbl_note( *ret, *size_ptr, type );')
assert native.count(note_site) == 2, 'both reservation paths note the helper\'s ranges'
free = function('NTSTATUS WINAPI NtFreeVirtualMemory( HANDLE process, PVOID *addr_ptr, SIZE_T *size_ptr, ULONG type )')
fg = free.index('if (type == MEM_RELEASE && ios_sc_tbl_n) ios_sc_tbl_forget( base );')
assert free.rindex('if (status == STATUS_SUCCESS)', 0, fg) > free.index('server_enter_uninterrupted_section( &virtual_mutex, &sigset );')
assert fg < free.rindex('server_leave_uninterrupted_section( &virtual_mutex, &sigset );'), 'forget runs under virtual_mutex'
assert free.count('ios_sc_tbl_forget') == 1
rh = free.index('ios_sc2_tbl_rehold_empty();')
assert rh > free.rindex('server_leave_uninterrupted_section( &virtual_mutex, &sigset );'), 'the zone re-hold runs after the mutex'
assert 'if (status == STATUS_SUCCESS && (type & MEM_RELEASE) && ios_sc2_tbl_given_n' in free
for site in ('            if (sc2 && (ios_sc_grant_dead_n || ios_sc_tbl_dead_n))\n            {\n                ios_sc_reap_dead();\n'
             '                ios_sc_tbl_reap( 0 );',
             '        if (is_jumbo && (ios_sc_grant_dead_n || ios_sc_tbl_dead_n) && ios_sc_cef_enabled() && ios_sc_current_is_helper())\n'
             '        {\n            ios_sc_reap_dead();\n            ios_sc_tbl_reap( 0 );'):
    assert native.count(site) == 1, site
zone_call = native.index('if (ios_sc2_table_zone( ret, size_ptr, type, protect ))')
gate = native[native.rindex('st = allocate_virtual_memory( ret, size_ptr, type, protect,', 0, zone_call):zone_call]
for need in ('st == STATUS_NO_MEMORY', '!ask_at', 'ios_sc2_a1g_on', 'ios_sc_layout_mode == 2',
             'type == (MEM_RESERVE | MEM_RESERVE_PLACEHOLDER)', 'protect == PAGE_NOACCESS',
             '(ask_sz == 0x20000000 || ask_sz == 0x10000000)', 'ios_sc_current_is_helper()'):
    assert need in gate, need
term = function('NTSTATUS WINAPI NtTerminateProcess( HANDLE handle, LONG exit_code )', process)
oom = term[term.index('if ((unsigned int)exit_code == 0xE0000008u && handle == NtCurrentProcess())'):]
oom = oom[:oom.index('    }\n')]
assert 'ios_term_oom_record( NtCurrentTeb() );' in oom and 'ios_sc_note_oom_exit();' in oom
spawn = process[process.index('        if (kind == SC_BROWSER)\n'):]
assert spawn.index('ios_sc_reap_before_spawn();') < spawn.index('o = sc_browser_cmdline(')
assert 'run: python3 tests/host/check-sc-reap-tables.py' in workflow

# 2. The production code against a model.
sigs = ('static int ios_sc_reap_key( void )',
        'static void ios_sc_tbl_note( void *ret, SIZE_T size, ULONG type )',
        'static struct file_view *ios_lowest_view_in( char *lo, char *hi )',
        'static void ios_sc_tbl_forget( void *base )',
        'void ios_sc_note_oom_exit( void )',
        'static void ios_sc_tbl_owner_died( void *peb )',
        'static void ios_sc2_tbl_rehold_empty( void )',
        'static NTSTATUS ios_sc2_table_zone( void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect )',
        'static void ios_sc_tbl_reap( int may_wait )',
        'void ios_sc_reap_before_spawn( void )',
        'static inline int is_view_valloc( const struct file_view *view )')
pieces = {s: function(s) for s in sigs}
data = []
for pat in (r'#define (?:IOS_SC_TBL_\w+|IOS_SC_OOM_MAX|IOS_SC2_TBL_\w+|IOS_SC2_A1G_BASE|IOS_SC2_A1G_SIZE)\b.*',
            r'static struct \{ uint64_t base, size; void \*peb; unsigned long long died_ms; int dead; \} ios_sc_tbl\[.*',
            r'static int ios_sc_tbl_n, ios_sc_tbl_dead_n;', r'static void \*ios_sc_oom_peb\[.*',
            r'static pthread_mutex_t ios_sc_tbl_lock.*', r'static uint64_t ios_sc2_tbl_given\[.*',
            r'static int ios_sc2_tbl_given_n;', r'static pthread_mutex_t ios_sc2_tbl_lock.*'):
    found = re.findall(r'^' + pat + r'$', native, re.M)
    assert found, pat
    data += found
order = [sigs[10], sigs[0], sigs[1], sigs[2], sigs[3], sigs[4], sigs[5], sigs[6], sigs[7], sigs[8], sigs[9]]
program = r'''
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define nanosleep model_nanosleep
typedef unsigned int NTSTATUS, ULONG;
typedef size_t SIZE_T;
typedef uintptr_t ULONG_PTR;
typedef uint64_t mach_vm_address_t, mach_vm_size_t;
typedef int kern_return_t;
#define STATUS_SUCCESS 0
#define STATUS_NO_MEMORY 0xc0000017u
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018u
#define KERN_SUCCESS 0
#define KERN_NO_SPACE 3
#define VM_FLAGS_FIXED 0
#define MEMORY_OBJECT_NULL 0
#define PROT_NONE 0
#define VM_PROT_ALL 7
#define VM_INHERIT_COPY 1
#define MEM_COMMIT 0x1000
#define MEM_RESERVE 0x2000
#define MEM_REPLACE_PLACEHOLDER 0x4000
#define MEM_RESERVE_PLACEHOLDER 0x40000
#define MEM_RELEASE 0x8000
#define MEM_PRESERVE_PLACEHOLDER 0x2
#define PAGE_NOACCESS 1
#define SEC_FILE 0x800000
#define SEC_IMAGE 0x1000000
#define SEC_RESERVE 0x4000000
#define SEC_COMMIT 0x8000000
#define VPROT_SYSTEM 0x0200
#define VPROT_PLACEHOLDER 0x0400
#define IOS_VIEW_QUARANTINED 0x4
#define NtCurrentProcess() ((void *)-1)
static int lines;
#define dprintf(fd, ...) ((void)(fd), lines++, getenv( "MODEL_LOG" ) ? fprintf( stderr, __VA_ARGS__ ) : 0)
/* the model view tree: a sorted array; an rb_entry is the view itself */
struct rb_entry { int unused; };
struct file_view { struct rb_entry entry; void *base; size_t size; unsigned int protect, ios_flags; void *ios_owner; };
#define RB_ENTRY_VALUE( e, type, field ) ((type *)(e))
static struct file_view views[256];
static int nviews;
static struct rb_entry *rb_next( struct rb_entry *e ) { struct file_view *v = (struct file_view *)e; return v + 1 < views + nviews ? &v[1].entry : NULL; }
static struct rb_entry *rb_prev( struct rb_entry *e ) { struct file_view *v = (struct file_view *)e; return v > views ? &v[-1].entry : NULL; }
static struct file_view *find_view_range( const void *addr, size_t size )
{
    int i, hit = -1;   /* the LAST overlapping view, so the walk back to the lowest one is exercised */
    for (i = 0; i < nviews; i++)
        if ((char *)views[i].base < (const char *)addr + size && (const char *)addr < (char *)views[i].base + views[i].size) hit = i;
    return hit < 0 ? NULL : &views[hit];
}
static void *current_peb;
static void *ios_jit_current_peb( void ) { return current_peb; }
static void add_view( uint64_t base, uint64_t size, unsigned protect, void *owner )
{
    int i = nviews;
    assert( !find_view_range( (void *)(ULONG_PTR)base, size ) && nviews < 256 );
    while (i > 0 && (uint64_t)(ULONG_PTR)views[i - 1].base > base) { views[i] = views[i - 1]; i--; }
    views[i].base = (void *)(ULONG_PTR)base; views[i].size = size; views[i].protect = protect;
    views[i].ios_flags = 0; views[i].ios_owner = owner;
    nviews++;
}
static struct file_view *view_at( uint64_t base )
{
    for (int i = 0; i < nviews; i++) if ((uint64_t)(ULONG_PTR)views[i].base == base) return &views[i];
    return NULL;
}
static int has_view( uint64_t base ) { return view_at( base ) != NULL; }
static void remove_view( uint64_t base )
{
    int i;
    for (i = 0; i < nviews && (uint64_t)(ULONG_PTR)views[i].base != base; i++) ;
    assert( i < nviews );
    for (; i + 1 < nviews; i++) views[i] = views[i + 1];
    nviews--;
}
static int virtual_mutex, mutex_depth;
static void server_enter_uninterrupted_section( int *m, sigset_t *s ) { (void)m; (void)s; mutex_depth++; }
static void server_leave_uninterrupted_section( int *m, sigset_t *s ) { (void)m; (void)s; mutex_depth--; }
/* native map: holds only (Wine views count as mapped too) */
struct range { uint64_t lo, hi; };
static struct range holds[64];
static int nholds;
static int native_busy( uint64_t lo, uint64_t hi )
{
    for (int i = 0; i < nholds; i++) if (lo < holds[i].hi && holds[i].lo < hi) return 1;
    return find_view_range( (void *)(ULONG_PTR)lo, hi - lo ) != NULL;
}
static int held( uint64_t lo, uint64_t hi )
{
    for (uint64_t a = lo; a < hi; a += 0x10000000ULL)
    {
        int hit = 0;
        for (int i = 0; i < nholds; i++) if (holds[i].lo <= a && a < holds[i].hi) hit = 1;
        if (!hit) return 0;
    }
    return 1;
}
static kern_return_t mach_vm_map( int task, mach_vm_address_t *a, mach_vm_size_t s, int mask, int flags, int obj,
                                  int off, int copy, int cur, int max, int inh )
{
    (void)task; (void)mask; (void)flags; (void)obj; (void)off; (void)copy; (void)cur; (void)max; (void)inh;
    if (native_busy( *a, *a + s )) return KERN_NO_SPACE;
    holds[nholds].lo = *a; holds[nholds].hi = *a + s; nholds++;
    return KERN_SUCCESS;
}
static kern_return_t mach_vm_deallocate( int task, mach_vm_address_t lo, mach_vm_size_t s )
{
    uint64_t hi = lo + s;
    (void)task;
    for (int i = 0; i < nholds; i++)
    {
        struct range r = holds[i];
        if (!(lo < r.hi && r.lo < hi)) continue;
        holds[i--] = holds[--nholds];
        if (r.lo < lo) { holds[nholds].lo = r.lo; holds[nholds].hi = lo; nholds++; }
        if (hi < r.hi) { holds[nholds].lo = hi; holds[nholds].hi = r.hi; nholds++; }
    }
    return KERN_SUCCESS;
}
static int mach_task_self( void ) { return 0; }
static void ios_va_release_note( void ) {}
static int blocked;   /* something else took a zone slot between the release of its hold and the reserve */
static uint64_t blocked_at;   /* the same for ONE slot (night round 2026-11-11, R1 S2) */
static NTSTATUS allocate_virtual_memory( void **ret, SIZE_T *size, ULONG type, ULONG protect, int a, int b, int c, int d )
{
    uint64_t lo = (uint64_t)(ULONG_PTR)*ret;
    (void)protect; (void)a; (void)b; (void)c; (void)d;
    if (blocked || (blocked_at && lo == blocked_at)) add_view( lo, 0x10000, 0, (void *)0xb10c );
    if (native_busy( lo, lo + *size )) return STATUS_CONFLICTING_ADDRESSES;
    add_view( lo, *size, (type & MEM_RESERVE_PLACEHOLDER) ? VPROT_PLACEHOLDER : 0, current_peb );
    return STATUS_SUCCESS;
}
static unsigned long long clock_ms = 100000, slept_ms;
static unsigned long long ios_now_ms( void ) { return clock_ms; }
static int nanosleep( const struct timespec *ts, struct timespec *rem )
{
    (void)rem;
    slept_ms += ts->tv_sec * 1000ULL + ts->tv_nsec / 1000000;
    clock_ms += ts->tv_sec * 1000ULL + ts->tv_nsec / 1000000;
    return 0;
}
static uint64_t ios_sc2_arena_lo = 0x7d00000000ULL;
static int helper = 1, live_child_peb_set;
static void *live_child;
static int ios_sc_cef_enabled( void ) { return 1; }
static int ios_sc_current_is_helper( void ) { return helper; }
int ios_peb_is_live_child( const void *peb ) { return live_child_peb_set && peb == live_child; }
static int jumbo_reaps;
static void ios_sc_reap_dead( void ) { jumbo_reaps++; }
static void ios_sc_tbl_reap( int may_wait );
static void ios_sc2_tbl_rehold_empty( void );
static int frees, forget_calls;
@DATA@
static void ios_sc_tbl_forget( void *base );
#define IOS_SC2_TBL_END (IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOTS * IOS_SC2_TBL_SLOT)
static NTSTATUS NtFreeVirtualMemory( void *proc, void **addr, SIZE_T *size, ULONG type )
{
    int i;
    char *b = *addr;
    (void)proc;
    assert( !mutex_depth );
    for (i = 0; i < nviews && views[i].base != b; i++) ;
    if (i == nviews) return 0xc000009fu;
    *size = views[i].size;
    if (type == (MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER)) return STATUS_SUCCESS;   /* a split: the views stay */
    for (; i + 1 < nviews; i++) views[i] = views[i + 1];
    nviews--;
    frees++;
    /* as the production: forget under the mutex, the zone's re-hold after it */
    mutex_depth++;
    if (type == MEM_RELEASE && ios_sc_tbl_n) { forget_calls++; ios_sc_tbl_forget( b ); }
    mutex_depth--;
    if ((type & MEM_RELEASE) && ios_sc2_tbl_given_n && (uint64_t)(ULONG_PTR)b >= IOS_SC2_TBL_BASE
        && (uint64_t)(ULONG_PTR)b < IOS_SC2_TBL_END)
        ios_sc2_tbl_rehold_empty();
    return STATUS_SUCCESS;
}
@PIECES@
#define MB (1ULL << 20)
static void reserve( uint64_t base, uint64_t size, ULONG type )
{
    if (type & MEM_RESERVE_PLACEHOLDER) add_view( base, size, VPROT_PLACEHOLDER, current_peb );
    else add_view( base, size, 0, current_peb );
    ios_sc_tbl_note( (void *)(ULONG_PTR)base, size, type );
}
static void release( uint64_t base )
{
    void *b = (void *)(ULONG_PTR)base;
    SIZE_T s = 0;
    assert( NtFreeVirtualMemory( NtCurrentProcess(), &b, &s, MEM_RELEASE ) == STATUS_SUCCESS );
}
/* V8 splits the head off a table and commits it: two views of the same owner */
static void split_head( uint64_t base, uint64_t size, uint64_t head )
{
    int i;
    for (i = 0; i < nviews && (uint64_t)(ULONG_PTR)views[i].base != base; i++) ;
    assert( i < nviews && views[i].size == size );
    views[i].size = head;
    add_view( base + head, size - head, VPROT_PLACEHOLDER, views[i].ios_owner );
}
int main( int argc, char **argv )
{
    void *A = (void *)0x10c3c4000, *B = (void *)0x10d000000, *C = (void *)0x10e000000, *D = (void *)0x10f000000;
    void *L = (void *)0x10a000000;   /* the Launcher */
    const ULONG PH = MEM_RESERVE | MEM_RESERVE_PLACEHOLDER;
    int i;

    if (argc > 1) { printf( "%d\n", ios_sc_reap_key() ); return 0; }

    /* 1. what is noted */
    current_peb = A;
    reserve( 0x7100000000ULL, 512 * MB, PH );                   /* a table */
    reserve( 0x7120000000ULL, 256 * MB, PH );                   /* the 256 MB one */
    reserve( 0x7130000000ULL, 512 * MB, MEM_RESERVE );          /* the code range */
    reserve( 0x7150000000ULL, 64 * MB, PH );                    /* too small */
    reserve( 0x7160000000ULL, 1024 * MB, MEM_RESERVE );         /* a jumbo grant: ios_sc_reap_dead's */
    reserve( 0x7d10000000ULL, 512 * MB, MEM_RESERVE );          /* in the FEX arena */
    ios_sc_tbl_note( (void *)(ULONG_PTR)0x7100000000ULL, 512 * MB, MEM_RESERVE | MEM_COMMIT | MEM_REPLACE_PLACEHOLDER );
    helper = 0;
    current_peb = L;
    reserve( 0x71a0000000ULL, 512 * MB, MEM_RESERVE );          /* not the helper */
    helper = 1;
    current_peb = A;
    assert( ios_sc_tbl_n == 3 && !ios_sc_tbl_dead_n );

    /* 2. pieces: V8 splits the table's head; another owner's view in the 256 MB range (a hole it took); a view of
     * the helper that starts in the 256 MB range and reaches into the code range; an image view and a quarantined
     * stack of the helper inside the code range */
    split_head( 0x7100000000ULL, 512 * MB, 0x20000 );
    split_head( 0x7120000000ULL, 256 * MB, 0x20000 );
    view_at( 0x7120020000ULL )->size = 0x100000;                    /* the helper's remainder shrank ... */
    current_peb = B;
    add_view( 0x7120120000ULL, 0x100000, 0, B );                    /* ... and B took the hole */
    current_peb = A;
    add_view( 0x7120220000ULL, 256 * MB - 0x230000, VPROT_PLACEHOLDER, A );
    view_at( 0x7130000000ULL )->size = 0x1000000 - 0x10000;         /* the code range's first 64 KB went back ... */
    view_at( 0x7130000000ULL )->base = (void *)(ULONG_PTR)0x7130010000ULL;
    add_view( 0x712fff0000ULL, 0x20000, 0, A );                     /* ... and this one reaches in from below */
    add_view( 0x7131000000ULL, 0x100000, SEC_IMAGE | SEC_FILE, A );
    add_view( 0x7131100000ULL, 0x100000, 0, A );
    view_at( 0x7131100000ULL )->ios_flags = IOS_VIEW_QUARANTINED;
    add_view( 0x7131200000ULL, 512 * MB - 0x1200000, 0, A );

    /* 3. a normal exit: entries dropped, nothing released, no wait at the next start */
    {
        int before = nviews;
        int n = ios_sc_tbl_n;
        ios_sc_tbl_owner_died( A );
        assert( !ios_sc_tbl_n && !ios_sc_tbl_dead_n );
        current_peb = L;
        ios_sc_reap_before_spawn();
        assert( nviews == before && !slept_ms && !jumbo_reaps && n == 3 );
        current_peb = A;
        ios_sc_tbl_note( (void *)(ULONG_PTR)0x7100000000ULL, 512 * MB, PH );
        ios_sc_tbl_note( (void *)(ULONG_PTR)0x7120000000ULL, 256 * MB, PH );
        ios_sc_tbl_note( (void *)(ULONG_PTR)0x7130000000ULL, 512 * MB, MEM_RESERVE );
        assert( ios_sc_tbl_n == 3 );
    }

    /* 4. forget: a live helper D releases a table piece by piece; the entry goes with its last piece only */
    current_peb = D;
    reserve( 0x7300000000ULL, 512 * MB, PH );
    split_head( 0x7300000000ULL, 512 * MB, 0x20000 );
    assert( ios_sc_tbl_n == 4 );
    {   /* a split frees nothing and forgets nothing */
        void *b = (void *)(ULONG_PTR)0x7300000000ULL;
        SIZE_T s = 0x10000;
        int fc = forget_calls;
        assert( !NtFreeVirtualMemory( NtCurrentProcess(), &b, &s, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER ) && forget_calls == fc );
    }
    release( 0x7300000000ULL );
    assert( ios_sc_tbl_n == 4 );                                   /* the remainder is still there */
    release( 0x7300020000ULL );
    assert( ios_sc_tbl_n == 3 );
    /* another process's release of the same base forgets nothing */
    reserve( 0x7300000000ULL, 512 * MB, PH );
    current_peb = L;
    release( 0x7300000000ULL );
    assert( ios_sc_tbl_n == 4 );
    current_peb = D;
    ios_sc_tbl_owner_died( D );                                    /* D ends normally */
    assert( ios_sc_tbl_n == 3 );

    /* 5. A ends out of memory; the Launcher starts the next helper 1 s later: it waits 2 s, then A's views go */
    current_peb = A;
    ios_sc_note_oom_exit();
    ios_sc_note_oom_exit();                                        /* twice: one record */
    ios_sc_tbl_owner_died( A );
    assert( ios_sc_tbl_dead_n == 3 );
    clock_ms += 1000;
    current_peb = L;
    ios_sc_tbl_reap( 0 );                                          /* the jumbo-ask path: too young, nothing */
    assert( ios_sc_tbl_dead_n == 3 && !slept_ms );
    {
        int frees0 = frees;
        ios_sc_reap_before_spawn();
        assert( slept_ms == 2000 && jumbo_reaps == 1 && !ios_sc_tbl_dead_n && !ios_sc_tbl_n );
        /* released: the table's two pieces, the 256 MB range's three pieces of A, the code range's two plain views */
        assert( frees - frees0 == 7 );
    }
    assert( !has_view( 0x7100000000ULL ) && !has_view( 0x7100020000ULL ) );
    assert( !has_view( 0x7120000000ULL ) && !has_view( 0x7120020000ULL ) && !has_view( 0x7120220000ULL ) );
    assert( has_view( 0x7120120000ULL ) );                          /* B's */
    assert( !has_view( 0x7130010000ULL ) && !has_view( 0x7131200000ULL ) );
    assert( has_view( 0x7131000000ULL ) && has_view( 0x7131100000ULL ) );   /* the image view, the quarantined stack */
    assert( has_view( 0x712fff0000ULL ) );                         /* it crosses the ranges' border */
    assert( has_view( 0x7160000000ULL ) );                          /* the jumbo grant is ios_sc_reap_dead's */

    /* 6. a PEB that runs again: skipped */
    current_peb = C;
    reserve( 0x7200000000ULL, 512 * MB, PH );
    ios_sc_note_oom_exit();
    ios_sc_tbl_owner_died( C );
    live_child = C;
    live_child_peb_set = 1;
    clock_ms += 5000;
    current_peb = L;
    ios_sc_tbl_reap( 0 );
    assert( has_view( 0x7200000000ULL ) && !ios_sc_tbl_dead_n && !ios_sc_tbl_n );
    live_child_peb_set = 0;

    /* 7. S3: the zone of the key's hold */
    {
        mach_vm_address_t h = IOS_SC2_A1G_BASE;
        void *p;
        SIZE_T s;
        assert( mach_vm_map( 0, &h, 0x100000000ULL, 0, 0, 0, 0, 0, 0, 7, 1 ) == KERN_SUCCESS );
        mach_vm_deallocate( 0, IOS_SC2_A1G_BASE, IOS_SC2_A1G_SIZE );   /* the first GB went to the 1 GB ask */
        current_peb = A;
        p = NULL; s = 512 * MB;
        assert( !ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) && (uint64_t)(ULONG_PTR)p == IOS_SC2_TBL_BASE );
        ios_sc_tbl_note( p, s, PH );
        p = NULL; s = 256 * MB;
        assert( !ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) && (uint64_t)(ULONG_PTR)p == IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT );
        assert( held( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT + 256 * MB, IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT ) );
        for (i = 2; i < IOS_SC2_TBL_SLOTS; i++)
        {
            p = NULL; s = 512 * MB;
            assert( !ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) );
        }
        p = NULL; s = 512 * MB;
        assert( ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) == STATUS_NO_MEMORY && ios_sc2_tbl_given_n == IOS_SC2_TBL_SLOTS );
        /* the 256 MB table is released: its slot is held again and serves the next ask */
        release( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT );
        assert( ios_sc2_tbl_given_n == IOS_SC2_TBL_SLOTS - 1 && held( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT, IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT ) );
        /* a blocked slot: the ask fails cleanly; its hold cannot come back while the other view sits there */
        blocked = 1;
        p = NULL; s = 512 * MB;
        assert( ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) == STATUS_NO_MEMORY && !p );
        blocked = 0;
        /* night round 2026-11-11 (R1 S2; GTA IV R5A0JP and KCY3C5: the first held slot was refused because a view stood in it and the function
         * ended the helper with five slots left): a slot that is refused is passed over, its hold is not put back while the other view stands
         * there, and the next free slot serves the ask; three lines (the old one, the busy one, the served one) */
        {
            int l0 = lines;
            remove_view( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT );          /* the view the blocked ask left in slot 1 goes */
            ios_sc2_tbl_rehold_empty();
            assert( !ios_sc2_tbl_given[1] && held( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT, IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT ) );
            release( IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT );          /* slot 2's table is released: held again */
            assert( !ios_sc2_tbl_given[2] && ios_sc2_tbl_given[0] );
            l0 = lines;
            blocked_at = IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT;           /* a view appears in slot 1 when its hold is let go */
            p = NULL; s = 512 * MB;
            assert( !ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) && (uint64_t)(ULONG_PTR)p == IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT );
            assert( lines - l0 == 3 && ios_sc2_tbl_given[1] && has_view( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT ) && ios_sc2_tbl_given[2] );
            assert( !held( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT, IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT ) );   /* not put back under the view */
            blocked_at = 0;
            /* all the free slots refused: the ask fails as before */
            remove_view( IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT );
            ios_sc2_tbl_rehold_empty();
            blocked_at = IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT;
            p = NULL; s = 512 * MB;
            assert( ios_sc2_table_zone( &p, &s, PH, PAGE_NOACCESS ) == STATUS_NO_MEMORY && !p );
            blocked_at = 0;
            remove_view( IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT );
            remove_view( IOS_SC2_TBL_BASE + 2 * IOS_SC2_TBL_SLOT );
            ios_sc2_tbl_rehold_empty();
            assert( !ios_sc2_tbl_given[1] && !ios_sc2_tbl_given[2] );
        }
        /* the helper with the first slot's table ends out of memory: the reap empties the slot and holds it again */
        split_head( IOS_SC2_TBL_BASE, 512 * MB, 0x20000 );
        ios_sc_note_oom_exit();
        ios_sc_tbl_owner_died( A );
        clock_ms += 4000;
        current_peb = L;
        ios_sc_reap_before_spawn();
        assert( !has_view( IOS_SC2_TBL_BASE ) && held( IOS_SC2_TBL_BASE, IOS_SC2_TBL_BASE + IOS_SC2_TBL_SLOT ) );
        assert( !ios_sc2_tbl_given[0] );
    }
    puts( "model ok" );
    return 0;
}
'''
program = program.replace('@DATA@', '\n'.join(data)).replace('@PIECES@', '\n'.join(pieces[s] for s in order))
with tempfile.TemporaryDirectory(prefix='sc-reap-tables-') as tmp:
    src, exe = Path(tmp) / 'model.c', Path(tmp) / 'model'
    src.write_text(program)
    r = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Werror',
                        '-fsanitize=address,undefined', '-o', str(exe), str(src)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-4000:]
    out = subprocess.run([str(exe)], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == 'model ok', out.stdout + out.stderr[-3000:]
    env0 = {k: v for k, v in os.environ.items() if k != 'MADEIRA_SC_REAP_DEAD'}
    for value, expect in (('0', '0'), ('1', '1'), ('', '1'), (None, '1'), ('00', '0')):
        env = dict(env0)
        if value is not None:
            env['MADEIRA_SC_REAP_DEAD'] = value
        got = subprocess.run([str(exe), 'key'], env=env, capture_output=True, text=True).stdout.strip()
        assert got == expect, (value, got)
    # the key off: nothing is noted, so nothing can be released
    off = program.replace('    if (argc > 1) { printf( "%d\\n", ios_sc_reap_key() ); return 0; }',
                          '    if (argc > 1) { setenv( "MADEIRA_SC_REAP_DEAD", "0", 1 ); current_peb = A; '
                          'reserve( 0x7100000000ULL, 512 * MB, PH ); ios_sc_note_oom_exit(); ios_sc_tbl_owner_died( A ); '
                          'printf( "%d %d\\n", ios_sc_tbl_n, ios_sc_tbl_dead_n ); return 0; }')
    src.write_text(off)
    r = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Werror',
                        '-o', str(exe), str(src)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-4000:]
    got = subprocess.run([str(exe), 'off'], env=env0, capture_output=True, text=True).stdout.strip()
    assert got == '0 0', got

print('SocialClubHelper.exe out of memory: its V8 reservations (128 MB..1 GB below the arena, owner-tagged views) are '
      'listed, released before the next helper starts after a 3 s grace, only its own views (another owner, an image, '
      'a quarantined stack and a view from outside stay), a normal exit and a running PEB release nothing, a split '
      'forgets nothing; MADEIRA_SC_REAP_DEAD=0 is off; the ALIGN1G zone gives the lowest free slot (256 MB: half), '
      'refuses at six, survives a blocked slot and holds emptied slots again')
