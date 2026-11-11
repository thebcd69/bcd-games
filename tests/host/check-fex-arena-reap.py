#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The emulator's arena memory of a process that has ended: counted by default, released with env.MADEIRA_FEX_ARENA_REAP = 1; no Wine runs.

GTA IV (Steam 12210, BCD Dock), R5A0JP (build 634) and KCY3C5 (631): Rockstar Launcher's sign-in page restarts its Social Club helper (about 70
emulator threads each) and the FEX arena of 12 GB went from 49 % to 94 % in four restarts with only 92 live threads; the free notices of the
emulator's tracker showed 0-2 frees after each helper death. Everything the arena holds for a pseudo-process is a view its own threads made
(create_view tags it with the PEB, view->ios_owner); a view is unmapped only when the emulator frees it, and a process whose threads do not
all leave through the emulator's own thread exit leaves all of it mapped. FEXAlloc then fails (`[alloc-fail] ml798`) and the Launcher itself dies
(`[fex-fault] ... fault-addr=0x800010`).

virtual_ios.c now queues every pseudo-process that ends (only in a session that has an arena); at the first request the arena serves IOS_FEXREAP_GRACE_MS
later (3 s, the JIT pool's grace) it counts the views the process made inside the arena and prints one `[fex-arena] r652` line. Default: nothing
is released. env.MADEIRA_FEX_ARENA_REAP = 1 releases them through NtFreeVirtualMemory(MEM_RELEASE) as the emulator's own free would; 2 only says
"would be released".

Part A reads the wiring (where the process is queued, the hook, the key's three values, the lock-free fast path). Part B compiles the production
functions against a model view tree, clock and lock counter: a view of another process, one of the same process that is an image, quarantined or
that reaches in from outside the arena, a view with no owner, and a PEB that runs again are never released; the grace is kept; the queue is capped
and deduplicated; nothing is queued without an arena; and the release runs without virtual_mutex held.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
native = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(signature, text=native):
    start = text.index(signature + '\n{')
    return text[start:text.index('\n}\n', start) + 3]


# ---------------------------------------------------------------- Part A: wiring
reclaim = function('void ios_jit_reclaim_process( void *peb )')
check('every ended process is queued before the early return of ios_jit_reclaim_process',
      reclaim.index('ios_fexreap_note_dead( peb );') < reclaim.index('if (!peb || !rx_base) return;'))
alloc = function('static inline __attribute__((always_inline)) NTSTATUS ios_nt_impl_NtAllocateVirtualMemoryEx( HANDLE process, PVOID *ret, SIZE_T *size_ptr, ULONG type,\n'
                 '                                           ULONG protect, MEM_EXTENDED_PARAMETER *parameters,\n'
                 '                                           ULONG count )')
hook = 'if (__atomic_load_n( &ios_fexreap_n, __ATOMIC_ACQUIRE ) && limit_high && fex_arena_covers( (void *)limit_low, (void *)limit_high ))\n        ios_fex_arena_reap_run();'
check('the hook is one atomic load in a request the arena serves, after the parameter checks and before any lock, and calls the runner',
      hook in alloc and alloc.index('if (!*size_ptr) return STATUS_INVALID_PARAMETER;') < alloc.index(hook) and
      alloc.index('get_extended_params( parameters, count') < alloc.index(hook) and 'server_enter_uninterrupted_section' not in alloc[:alloc.index(hook)])
mode = function('static int ios_fexreap_mode( void )')
check('the key has three values: unset or 0 = never release, 1 = release, 2 = count only; read once',
      'getenv( "MADEIRA_FEX_ARENA_REAP" )' in mode and "e[0] == '1' && !e[1] ? 1" in mode and "e[0] == '2' && !e[1] ? 2 : 0" in mode and 'static int mode = -1;' in mode)
one = function('static void ios_fex_arena_reap_one( void *peb, unsigned long long died_ms, unsigned long long now )')
check('the release only runs with mode 1, one view at a time, each time the lookup under virtual_mutex and the free without it',
      'if (mode == 1)\n    {\n        cur = lo;' in one and one.count('NtFreeVirtualMemory(') == 1 and
      one.index('server_leave_uninterrupted_section( &virtual_mutex, &sigset );', one.index('while (cur < hi')) < one.index('NtFreeVirtualMemory('))
check('a view is released only when its owner is the dead process and it is a plain allocation inside the arena (not an image, a system view or a quarantined stack)',
      one.count('v->ios_owner == peb && is_view_valloc( v ) && !(v->protect & (SEC_IMAGE | VPROT_SYSTEM)) &&') == 2 and
      one.count('own = v->ios_owner == peb && is_view_valloc( v ) && !(v->protect & (SEC_IMAGE | VPROT_SYSTEM)) &&') == 1 and
      one.count('!(v->ios_flags & IOS_VIEW_QUARANTINED) && (char *)v->base + v->size <= hi') == 2)
check('a PEB that is not dead (a new process took its address) is skipped', '!ios_peb_is_dead( peb )' in one)
check('the count of threads still listed walks teb_list for the process', 'LIST_FOR_EACH_ENTRY( thread_data, &teb_list' in function('static unsigned ios_fexreap_listed_threads( void *peb )'))
check('CI runs this check', 'python3 tests/host/check-fex-arena-reap.py' in workflow)

# ---------------------------------------------------------------- Part B: compiled against a model
sigs = ['static int ios_fexreap_mode( void )', 'static void ios_fexreap_note_dead( void *peb )',
        'static struct file_view *ios_lowest_view_in( char *lo, char *hi )',
        'static void ios_fex_arena_reap_one( void *peb, unsigned long long died_ms, unsigned long long now )',
        'static void ios_fex_arena_reap_run( void )']
pieces = {s: function(s) for s in sigs}
data = []
for pat in (r'#define IOS_FEXREAP_(?:MAX|GRACE_MS)\b.*', r'static struct \{ void \*peb; unsigned long long died_ms; \} ios_fexreap_q\[.*',
            r'static int ios_fexreap_n;.*', r'static pthread_mutex_t ios_fexreap_lock.*', r'static pthread_mutex_t ios_fexreap_busy.*'):
    found = re.findall(r'^' + pat + r'$', native, re.M)
    assert found, pat
    data += found
program = r'''
#include <assert.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int NTSTATUS, ULONG;
typedef size_t SIZE_T;
typedef uintptr_t ULONG_PTR;
#define STATUS_SUCCESS 0
#define MEM_RELEASE 0x8000
#define SEC_FILE 0x800000
#define SEC_IMAGE 0x1000000
#define VPROT_SYSTEM 0x0200
#define IOS_VIEW_QUARANTINED 0x4
#define NtCurrentProcess() ((void *)-1)
static int model_log_lines;
static char logbuf[4096];
#define dprintf(fd, ...) ((void)(fd), model_log_lines++, snprintf( logbuf + strlen( logbuf ), sizeof(logbuf) - strlen( logbuf ), __VA_ARGS__ ), \
                          getenv( "MODEL_LOG" ) ? fprintf( stderr, __VA_ARGS__ ) : 0)
struct rb_entry { int unused; };
struct file_view { struct rb_entry entry; void *base; size_t size; unsigned int protect, ios_flags; void *ios_owner; };
#define RB_ENTRY_VALUE( e, type, field ) ((type *)(e))
static struct file_view views[512];
static int nviews;
static struct rb_entry *rb_next( struct rb_entry *e ) { struct file_view *v = (struct file_view *)e; return v + 1 < views + nviews ? &v[1].entry : NULL; }
static struct rb_entry *rb_prev( struct rb_entry *e ) { struct file_view *v = (struct file_view *)e; return v > views ? &v[-1].entry : NULL; }
static struct file_view *find_view_range( const void *addr, size_t size )
{
    int i, hit = -1;
    for (i = 0; i < nviews; i++)
        if ((char *)views[i].base < (const char *)addr + size && (const char *)addr < (char *)views[i].base + views[i].size) hit = i;
    return hit < 0 ? NULL : &views[hit];
}
static void add_view( uint64_t base, uint64_t size, unsigned protect, void *owner )
{
    int i = nviews;
    assert( !find_view_range( (void *)(ULONG_PTR)base, size ) && nviews < 512 );
    while (i > 0 && (uint64_t)(ULONG_PTR)views[i - 1].base > base) { views[i] = views[i - 1]; i--; }
    views[i].base = (void *)(ULONG_PTR)base; views[i].size = size; views[i].protect = protect; views[i].ios_flags = 0; views[i].ios_owner = owner;
    nviews++;
}
static int has_view( uint64_t base ) { for (int i = 0; i < nviews; i++) if ((uint64_t)(ULONG_PTR)views[i].base == base) return 1; return 0; }
static int virtual_mutex, mutex_depth;
static void server_enter_uninterrupted_section( int *m, sigset_t *s ) { (void)m; (void)s; mutex_depth++; }
static void server_leave_uninterrupted_section( int *m, sigset_t *s ) { (void)m; (void)s; mutex_depth--; }
static inline int is_view_valloc( const struct file_view *view ) { return !(view->protect & (SEC_FILE | SEC_IMAGE)); }
static unsigned long long clock_ms = 100000;
static unsigned long long ios_now_ms( void ) { return clock_ms; }
static ULONG_PTR ios_fex_arena_base_unix, ios_fex_arena_end_unix;
static void *dead_pebs[8]; static int ndead;
int ios_peb_is_dead( const void *peb ) { for (int i = 0; i < ndead; i++) if (dead_pebs[i] == peb) return 1; return 0; }
static void *listed_peb; static unsigned listed_n;
static unsigned ios_fexreap_listed_threads( void *peb ) { return peb == listed_peb ? listed_n : 0; }
static int frees, refuse_next;
static NTSTATUS NtFreeVirtualMemory( void *proc, void **addr, SIZE_T *size, ULONG type )
{
    int i;
    char *b = *addr;
    (void)proc;
    assert( !mutex_depth && type == MEM_RELEASE && *size == 0 );
    for (i = 0; i < nviews && views[i].base != b; i++) ;
    if (i == nviews) return 0xc000009fu;
    if (refuse_next) { refuse_next--; return 0xc0000022u; }
    for (; i + 1 < nviews; i++) views[i] = views[i + 1];
    nviews--;
    frees++;
    return STATUS_SUCCESS;
}
@DATA@
@PIECES@
#define MB (1ULL << 20)
#define ARENA_LO 0x7d00000000ULL
#define ARENA_HI 0x8000000000ULL
int main( int argc, char **argv )
{
    void *A = (void *)0x10c3c4000, *B = (void *)0x10d000000, *C = (void *)0x10e000000;
    int scenario = atoi( argv[1] );
    int i;

    if (scenario == 0) { printf( "mode=%d\n", ios_fexreap_mode() ); return 0; }
    ios_fex_arena_base_unix = ARENA_LO; ios_fex_arena_end_unix = ARENA_HI;
    /* the arena: A (the ended helper) made 40 plain views, an image view, a quarantined stack, one that reaches in from below the arena and one
     * past its end; B (live) made 20; one view has no owner (boot) */
    for (i = 0; i < 40; i++) add_view( ARENA_LO + 0x10000000ULL + (uint64_t)i * 0x2000000ULL, 24 * MB, 0, A );
    add_view( ARENA_LO + 0x4000000ULL, 0x1000000, SEC_IMAGE | SEC_FILE, A );
    add_view( ARENA_LO + 0x9100000ULL, 0x100000, 0, A ); views[1].ios_flags = 0;
    for (i = 0; i < nviews; i++) if ((uint64_t)(ULONG_PTR)views[i].base == ARENA_LO + 0x9100000ULL) views[i].ios_flags = IOS_VIEW_QUARANTINED;
    add_view( ARENA_LO - 0x100000ULL, 0x200000, 0, A );                      /* reaches in from below the arena: not inside it */
    add_view( ARENA_HI - 0x1000000ULL, 0x2000000, 0, A );                    /* starts in the arena, ends past it */
    for (i = 0; i < 20; i++) add_view( ARENA_LO + 0x60000000ULL + (uint64_t)i * 0x2000000ULL, 24 * MB, 0, B );
    add_view( ARENA_LO + 0x8000000ULL, 0x100000, 0, NULL );
    int total = nviews;
    dead_pebs[ndead++] = A;
    listed_peb = A; listed_n = 3;

    if (scenario == 1)   /* the grace: a request 1 s after the death looks at nothing; 3 s after it, the count */
    {
        ios_fexreap_note_dead( A );
        ios_fexreap_note_dead( A );                                           /* deduplicated */
        assert( ios_fexreap_n == 1 );
        clock_ms += 1000;
        ios_fex_arena_reap_run();
        assert( ios_fexreap_n == 1 && !model_log_lines && frees == 0 );
        clock_ms += 2000;
        ios_fex_arena_reap_run();
        if (!( ios_fexreap_n == 0 && model_log_lines == 1 && frees == 0 && nviews == total && !mutex_depth ))
            fprintf( stderr, "n=%d lines=%d frees=%d nviews=%d total=%d depth=%d\n", ios_fexreap_n, model_log_lines, frees, nviews, total, mutex_depth );
        assert( ios_fexreap_n == 0 && model_log_lines == 1 && frees == 0 && nviews == total && !mutex_depth );
        printf( "%s", logbuf );
        return 0;
    }
    if (scenario == 2)   /* the release */
    {
        ios_fexreap_note_dead( A );
        clock_ms += 3000;
        ios_fex_arena_reap_run();
        assert( !mutex_depth && ios_fexreap_n == 0 );
        printf( "frees=%d total=%d left=%d image=%d quarantined=%d below=%d past=%d boot=%d b0=%d\n", frees, total, nviews,
                has_view( ARENA_LO + 0x4000000ULL ), has_view( ARENA_LO + 0x9100000ULL ), has_view( ARENA_LO - 0x100000ULL ),
                has_view( ARENA_HI - 0x1000000ULL ), has_view( ARENA_LO + 0x8000000ULL ), has_view( ARENA_LO + 0x60000000ULL ) );
        printf( "%s", logbuf );
        return 0;
    }
    if (scenario == 3)   /* the PEB runs again: nothing to release */
    {
        ndead = 0;
        ios_fexreap_note_dead( A );
        clock_ms += 3000;
        ios_fex_arena_reap_run();
        assert( frees == 0 && nviews == total && !model_log_lines && ios_fexreap_n == 0 );
        printf( "ok\n" );
        return 0;
    }
    if (scenario == 4)   /* a live process B is queued by mistake: not dead, nothing happens */
    {
        ios_fexreap_note_dead( B );
        clock_ms += 3000;
        ios_fex_arena_reap_run();
        assert( frees == 0 && nviews == total && !model_log_lines );
        printf( "ok\n" );
        return 0;
    }
    if (scenario == 5)   /* no arena: nothing is queued */
    {
        ios_fex_arena_base_unix = 0;
        ios_fexreap_note_dead( A );
        assert( ios_fexreap_n == 0 );
        printf( "ok\n" );
        return 0;
    }
    if (scenario == 6)   /* the queue is capped at 16, the oldest is dropped; two processes die at different times */
    {
        for (i = 0; i < 20; i++) { clock_ms += 10; ios_fexreap_note_dead( (void *)(0x20000000ULL + i * 0x1000) ); }
        assert( ios_fexreap_n == 16 && ios_fexreap_q[0].peb == (void *)(0x20000000ULL + 4 * 0x1000) );
        printf( "ok\n" );
        return 0;
    }
    if (scenario == 7)   /* a process with nothing in the arena prints nothing and is dropped from the queue */
    {
        dead_pebs[ndead++] = C;
        ios_fexreap_note_dead( C );
        clock_ms += 3000;
        ios_fex_arena_reap_run();
        assert( ios_fexreap_n == 0 && !model_log_lines && frees == 0 );
        printf( "ok\n" );
        return 0;
    }
    if (scenario == 8)   /* a release the engine refuses is counted */
    {
        ios_fexreap_note_dead( A );
        clock_ms += 3000;
        refuse_next = 2;
        ios_fex_arena_reap_run();
        printf( "frees=%d\n%s", frees, logbuf );
        return 0;
    }
    if (scenario == 9)   /* the runner is busy in another thread: this one skips */
    {
        pthread_mutex_lock( &ios_fexreap_busy );
        ios_fexreap_note_dead( A );
        clock_ms += 3000;
        ios_fex_arena_reap_run();
        assert( ios_fexreap_n == 1 && frees == 0 && !model_log_lines );
        printf( "ok\n" );
        return 0;
    }
    return 2;
}
'''
program = program.replace('@DATA@', '\n'.join(data)).replace('@PIECES@', '\n'.join(pieces[s] for s in sigs))
tmp = Path(tempfile.mkdtemp(prefix='madeira-fex-arena-reap-'))
(tmp / 'm.c').write_text(program)
cc = os.environ.get('CC', 'cc')
build = subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unused-but-set-variable',
                        str(tmp / 'm.c'), '-o', str(tmp / 'm'), '-lpthread'], capture_output=True, text=True)
check('the production functions compile on the host', build.returncode == 0, build.stdout + build.stderr)
if build.returncode == 0:
    def run(scenario, key=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        if key is not None:
            env['MADEIRA_FEX_ARENA_REAP'] = key
        return subprocess.run([str(tmp / 'm'), str(scenario)], capture_output=True, text=True, env=env)

    for key, want in ((None, 0), ('', 0), ('0', 0), ('1', 1), ('2', 2), ('3', 0), ('11', 0), ('on', 0)):
        r = run(0, key)
        check('MADEIRA_FEX_ARENA_REAP=%r gives mode %d' % (key, want), r.stdout.strip() == 'mode=%d' % want, r.stdout + r.stderr[-300:])
    r = run(1)
    check('default (key unset): 1 s after the death nothing is looked at, 3 s after it the views are counted and one line is printed, nothing is released',
          r.returncode == 0 and 'holding 40 view(s), 960 MB' in r.stdout and 'kept (MADEIRA_FEX_ARENA_REAP is not set)' in r.stdout and
          '3 thread(s) of it still listed' in r.stdout, r.stdout + r.stderr[-400:])
    r = run(1, '2')
    check('mode 2: the same count, "would be released", nothing released', r.returncode == 0 and 'would be released (MADEIRA_FEX_ARENA_REAP = 1)' in r.stdout, r.stdout + r.stderr[-300:])
    r = run(2, '1')
    out = r.stdout
    check('mode 1: exactly the 40 plain views of the dead process are released; the image, the quarantined stack, the view that reaches in from below, '
          'the one that ends past the arena, the boot view and the live process\'s views stay',
          r.returncode == 0 and 'frees=40 total=' in out and 'left=%d' % (int(re.search(r'total=(\d+)', out)[1]) - 40) in out and 'image=1 quarantined=1 below=1 past=1 boot=1 b0=1' in out,
          out + r.stderr[-300:])
    check('... and says so in one line (the count it found, what it released)', 'holding 40 view(s)' in out and 'released 40 view(s), 960 MB' in out, out)
    r = run(2)
    check('default: the same death releases nothing (the arena is untouched without the key)', r.returncode == 0 and 'frees=0 ' in r.stdout, r.stdout + r.stderr[-300:])
    r = run(3, '1')
    check('a PEB that runs again (not dead) releases nothing and prints nothing even with the key', r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-300:])
    r = run(4, '1')
    check('a live process queued by mistake releases nothing', r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-300:])
    r = run(5, '1')
    check('without an arena nothing is queued', r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-300:])
    r = run(6, '1')
    check('the queue is capped at 16 and drops the oldest', r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-300:])
    r = run(7, '1')
    check('a dead process with nothing in the arena is dropped quietly', r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-300:])
    r = run(8, '1')
    check('releases the engine refuses are counted', r.returncode == 0 and 'frees=38' in r.stdout and 'released 38 view(s)' in r.stdout and '(others refused)' in r.stdout, r.stdout + r.stderr[-300:])
    r = run(9, '1')
    check('a second thread skips while the runner is busy (trylock)', r.returncode == 0 and r.stdout.strip() == 'ok', r.stdout + r.stderr[-300:])

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
