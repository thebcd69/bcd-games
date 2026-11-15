#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright the Wine authors (LGPL-2.1-or-later): this test compiles RtlWaitOnAddress, RtlWakeAddressAll, RtlWakeAddressSingle
# and their helpers cut from Wine's dlls/ntdll/sync.c at run time; none of their text is in this file. BCD's terms above do not apply to
# those lines.
"""A waker takes a waiter's entry out of the wait queue before it marks the entry as woken
(tools/patch-wine-woa-wake-order.py: Wine commit 81e52628b25, written into the 32-bit ntdll at build time; build 660).

The entry of a thread waiting on an address lives on that thread's stack. When its park returns, the waiter looks at
entry.addr without the queue's lock; NULL means a waker took it out, and it returns: the stack entry is gone. Wine 11.4's
wakers clear entry->addr first and then still unlink the entry (both wakers) and read the thread to alert from it
(RtlWakeAddressAll). The patch makes them read the thread id, unlink, and clear last.

Checks (the Wine source is wine/ of this checkout, $BCD_WINE_SRC or ~/madeira-bcd/wine; skipped when there is none):
  1. the script: it changes the text, a second run changes nothing, undoing gives the text back, a moved anchor fails; with
     tools/patch-wine-woa-recheck.py (the other script on these functions) both apply in either order to the same text, and
     tools/patch-wine-ntdll-sync.py still applies on top;
  2. what each build compiles: with MADEIRA_WOA_WAKE_ORDER not defined (every build but i386) the file is, line for line,
     the file before the patch; with it defined the three functions have Wine's new statements in Wine's order; the macro
     is defined under "#ifdef __i386__" only, and no changed place says "#ifdef __i386__" itself (tests/host/
     check-woa-recheck.py turns that exact line on when it compiles RtlWaitOnAddress on the host);
  3. the real functions, old and new side by side in one program (-Wall -Wextra -Werror, ASan + UBSan), single thread, every
     step where another thread could run is a hook:
       a. the real waiter, old and new: a park that returns with entry.addr NULL makes it return at once, without taking
          the lock and without touching the queue (so its stack entry is gone from that moment); a park that returns with
          entry.addr still set makes it take the lock first, and while a waker holds the lock it only spins;
       b. the real wakers with a waiter that leaves at the first step where its entry reads NULL (its entry is then
          overwritten, as a reused stack frame would be), over every queue of one to five entries with every non-empty subset
          waiting on the woken address, and 300 woken entries in one call (more than one batch of alerts):
          new: the waker never touches an entry after that step: the queue is intact, exactly the right threads are
          alerted, in order, nothing is read from or written to an overwritten entry;
          old: in every one of these cases the waker unlinks an entry whose owner has left, the queue still points at
          that entry afterwards, and RtlWakeAddressAll alerts a thread id read from the overwritten entry (the defect,
          shown, not assumed);
       c. without such a waiter (nobody leaves early) old and new do the same: the same alerts in the same batches, the
          same queue, the same entries marked, over the same queues and 2,000 random ones; the waiter returns the same
          status and leaves the same queue over every scripted park (woken, left-over alert, time-out, wrong size,
          value already changed), with and without a time-out;
  4. the patched file compiles for i686 with llvm-mingw and generated headers (the full tree; the CI runs this);
  5. the build applies the script before the i386 farm is built and CI runs this check.
NOT covered: real threads on a device (the window is a few instructions wide; the hooks stand for the scheduler); that
emulated 32-bit code keeps x86's store order (the emulator's business, the same for all 32-bit code); the 64-bit ntdll.dll,
ntdll-sync.dll and ntdll-xsec.dll, which keep the old order.
"""
from pathlib import Path
import importlib.util
import itertools
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), root / 'tools' / (name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wo = load('patch-wine-woa-wake-order')
wine = None
for candidate in (os.environ.get('BCD_WINE_SRC'), root / 'wine', Path.home() / 'madeira-bcd/wine'):
    if candidate and (Path(candidate) / 'dlls/ntdll/sync.c').is_file():
        wine = Path(candidate)
        break
if not wine:
    print('SKIP no Wine source (wine/ in this checkout, BCD_WINE_SRC or ~/madeira-bcd/wine): the Wine parts are not run')


def cut(text, start, end='\n}\n'):
    at = text.index(start)
    return text[at:text.index(end, at) + len(end)]


def resolve(text, defined):
    """The text a compiler sees for the patch's own #ifdef MADEIRA_WOA_WAKE_ORDER / #else / #endif blocks (they do not nest)."""
    out, state = [], None
    for line in text.split('\n'):
        if line.startswith('#ifdef MADEIRA_WOA_WAKE_ORDER'):
            state = 'if'
        elif state and line == '#else':
            state = 'else'
        elif state and line == '#endif':
            state = None
        elif state is None or (state == 'if') == defined:
            out.append(line)
    return '\n'.join(out)


HARNESS_HEAD = r"""
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef int32_t NTSTATUS, LONG; typedef uint32_t DWORD, ULONG; typedef unsigned char UCHAR; typedef unsigned short USHORT;
typedef uint64_t ULONG64; typedef int64_t LONGLONG, LONG64; typedef size_t SIZE_T; typedef uintptr_t ULONG_PTR, DWORD_PTR;
typedef int BOOL; typedef void *HANDLE;
typedef union { LONGLONG QuadPart; } LARGE_INTEGER;
#define WINAPI
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define STATUS_SUCCESS ((NTSTATUS)0)
#define STATUS_ALERTED ((NTSTATUS)0x101)
#define STATUS_TIMEOUT ((NTSTATUS)0x102)
#define STATUS_INVALID_PARAMETER ((NTSTATUS)0xc000000d)
#define TRACE(...) do { } while (0)
#define ERR(...) do { } while (0)
#define debugstr_timeout(t) ""

/* every place where another thread could run is a call of step() */
enum { AT_UNLINK, AT_CLEARED, AT_ALERT, AT_SPIN };
static void step( int where, void *what );

struct list { struct list *next, *prev; };
static void list_init( struct list *l ) { l->next = l->prev = l; }
static void list_add_tail( struct list *l, struct list *e ) { e->next = l; e->prev = l->prev; l->prev->next = e; l->prev = e; }
static void list_remove( struct list *e ) { step( AT_UNLINK, e ); e->next->prev = e->prev; e->prev->next = e->next; }
#define LIST_ENTRY(elem, type, field) ((type *)((char *)(elem) - offsetof(type, field)))
#define LIST_FOR_EACH_ENTRY(elem, list, type, field) \
    for ((elem) = LIST_ENTRY((list)->next, type, field); &(elem)->field != (list); (elem) = LIST_ENTRY((elem)->field.next, type, field))
#define LIST_FOR_EACH_ENTRY_SAFE(cursor, cursor2, list, type, field) \
    for ((cursor) = LIST_ENTRY((list)->next, type, field), (cursor2) = LIST_ENTRY((cursor)->field.next, type, field); \
         &(cursor)->field != (list); (cursor) = (cursor2), (cursor2) = LIST_ENTRY((cursor)->field.next, type, field))

static int n_lock_tries;
static LONG cas( LONG *p, LONG x, LONG c ) { LONG old = *p; n_lock_tries++; if (old == c) *p = x; return old; }
#define InterlockedCompareExchange(p, x, c) cas( (p), (x), (c) )
#define InterlockedExchange(p, x) (*(p) = (x))
#define InterlockedIncrement(p) (++*(p))
#define InterlockedIncrement64(p) (++*(p))
#define YieldProcessor() step( AT_SPIN, NULL )
static void *ReadPointerAcquire( void *const volatile *src ) { return *src; }
static void WritePointerRelease( void *volatile *dest, void *value ) { *dest = value; step( AT_CLEARED, (void *)dest ); }

static DWORD cur_tid = 0x58;
#define GetCurrentThreadId() cur_tid
static struct { void *Instrumentation[16]; struct { void *UniqueThread; } ClientId; } teb;
#define NtCurrentTeb() (&teb)
static struct { LONG64 woa_waits, woa_wake_all, woa_wake_single; } ios_xp_nt;
static const void *ios_pump_wait_addr, *ios_pump_wait_queue;
static void ios_srw_dump_park( const void *a ) { (void)a; }
static void ios_futex_note_wake( const char *how, const void *addr, const void *queue, int found )
{ (void)how; (void)addr; (void)queue; (void)found; }

/* the alerts a waker sends, with the batches */
static DWORD alerts[1024]; static int n_alerts; static char batches[256];
static void note_batch( unsigned int n ) { size_t l = strlen( batches ); if (l < sizeof(batches) - 12) snprintf( batches + l, sizeof(batches) - l, "%u,", n ); }
static NTSTATUS NtAlertThreadByThreadId( HANDLE tid )
{ step( AT_ALERT, NULL ); if (n_alerts < 1024) alerts[n_alerts++] = (DWORD)(ULONG_PTR)tid; note_batch( 1 ); return STATUS_SUCCESS; }
static NTSTATUS NtAlertMultipleThreadByThreadId( HANDLE *tids, ULONG count, void *a, void *b )
{
    ULONG i;
    (void)a; (void)b;
    step( AT_ALERT, NULL );
    for (i = 0; i < count; i++) if (n_alerts < 1024) alerts[n_alerts++] = (DWORD)(ULONG_PTR)tids[i];
    note_batch( count );
    return STATUS_SUCCESS;
}
static NTSTATUS NtWaitForAlertByThreadId( const void *addr, const LARGE_INTEGER *timeout );
"""

HARNESS_QUEUE = r"""
static struct futex_queue the_queue, futex_queues[1];
static struct futex_queue *get_futex_queue( const void *addr ) { (void)addr; return &the_queue; }
"""

HARNESS_TAIL = r"""
/* ---- part b and c: the wakers. Entries are the test's own; an entry whose "owner has left" is overwritten the way a reused
 * stack frame would be: links into two guard nodes (so a later unlink is memory-safe and visible), a thread id of 0xdead.... */
#define MAX_E 320
static struct futex_entry ents[MAX_E];
static struct list guard_a[MAX_E], guard_b[MAX_E];
static int n_ents, gone[MAX_E], racing;
enum { UNLINKED_GONE = 1, QUEUE_HOLDS_GONE = 2, ALERTED_GARBAGE = 4, GUARD_WRITTEN = 8, QUEUE_BROKEN = 16 };
static int flags;
static int64_t word_a, word_b;

/* ---- part a: the waiter under a script */
enum { P_NONE, P_WOKEN, P_LEFTOVER, P_TIMEOUT, P_CLEARED_ONLY, P_HELD };
static int park_script, n_parks, spins, tries_at_park, waker_holds;
static struct futex_entry *parked;

static void finish_wake( void )   /* what a waker does, in the patched order, for the entry that is parked */
{
    if (!parked) return;
    list_remove( &parked->entry ); parked->addr = NULL;
}

static void step( int where, void *what )
{
    int i;
    if (where == AT_SPIN)
    {
        /* a waiter is spinning on the queue's lock: the waker that holds it finishes and lets go */
        spins++;
        if (waker_holds) { racing = 0; finish_wake(); the_queue.lock = 0; waker_holds = 0; }
        else if (spins > 1000) { puts( "a spin that never ends" ); exit( 3 ); }
        return;
    }
    for (i = 0; racing && i < n_ents; i++)
    {
        if (gone[i] || ents[i].addr) continue;
        /* this entry reads NULL: its waiter, whose park has just returned, leaves; its stack frame is reused */
        gone[i] = 1;
        ents[i].entry.next = &guard_a[i]; ents[i].entry.prev = &guard_b[i];
        ents[i].tid = 0xdead0000u + i;
    }
    if (where == AT_UNLINK)   /* the waker is about to unlink this entry: has its owner left? */
        for (i = 0; i < n_ents; i++) if (&ents[i].entry == what && gone[i]) flags |= UNLINKED_GONE;
}

static NTSTATUS NtWaitForAlertByThreadId( const void *addr, const LARGE_INTEGER *timeout )
{
    (void)addr; (void)timeout;
    n_parks++;
    parked = the_queue.queue.next != &the_queue.queue ? LIST_ENTRY( the_queue.queue.next, struct futex_entry, entry ) : NULL;
    tries_at_park = n_lock_tries;
    switch (park_script)
    {
    case P_WOKEN: finish_wake(); return STATUS_ALERTED;
    case P_LEFTOVER: return STATUS_ALERTED;
    case P_TIMEOUT: return STATUS_TIMEOUT;
    case P_CLEARED_ONLY: if (parked) parked->addr = NULL; return STATUS_ALERTED;   /* an old waker between its two steps */
    case P_HELD: the_queue.lock = -1; waker_holds = 1; return STATUS_ALERTED;      /* a waker holds the lock, entry untouched */
    }
    return STATUS_TIMEOUT;
}

static void reset_queue( void )
{
    memset( &the_queue, 0, sizeof(the_queue) ); list_init( &the_queue.queue );
    memset( ents, 0, sizeof(ents) ); memset( gone, 0, sizeof(gone) ); memset( guard_a, 0, sizeof(guard_a) ); memset( guard_b, 0, sizeof(guard_b) );
    n_ents = flags = n_alerts = racing = n_parks = spins = waker_holds = n_lock_tries = 0; batches[0] = 0; parked = NULL;
}

static void waiter( int new, int script, int size, int cmpval, int with_timeout )
{
    LARGE_INTEGER t; int64_t cmp = cmpval; NTSTATUS ret; int linked, tries_after;
    reset_queue(); word_a = 7; park_script = script; t.QuadPart = -10000;
    ret = new ? RtlWaitOnAddress_new( &word_a, &cmp, size, with_timeout ? &t : NULL )
              : RtlWaitOnAddress_old( &word_a, &cmp, size, with_timeout ? &t : NULL );
    tries_after = n_lock_tries - tries_at_park;
    linked = the_queue.queue.next != &the_queue.queue;
    /* status, parks, lock attempts after the park returned, spins, is its (dead) entry still linked, is the lock free */
    printf( "W %x %d %d %d %d %d\n", (unsigned int)ret, n_parks, n_parks ? tries_after : -1, spins, linked, the_queue.lock == 0 );
}

static void waker( int new, int all, int race, int n, unsigned int mask, int big )
{
    int i, expect = 0; struct list *e; char order[MAX_E + 1]; int n_order = 0; DWORD want[MAX_E]; int n_want = 0, alerts_ok;
    reset_queue();
    n_ents = n;
    for (i = 0; i < n; i++)
    {
        int hit = big ? 1 : (mask >> i) & 1;
        ents[i].addr = hit ? (void *)&word_a : (void *)&word_b; ents[i].tid = 1000 + i;
        list_add_tail( &the_queue.queue, &ents[i].entry );
        if (hit && (all || !expect)) { want[n_want++] = 1000 + i; expect++; }
    }
    racing = race;
    if (all) { if (new) RtlWakeAddressAll_new( &word_a ); else RtlWakeAddressAll_old( &word_a ); }
    else { if (new) RtlWakeAddressSingle_new( &word_a ); else RtlWakeAddressSingle_old( &word_a ); }
    racing = 0;
    for (i = 0; i < n; i++) if (guard_a[i].next || guard_a[i].prev || guard_b[i].next || guard_b[i].prev) flags |= GUARD_WRITTEN;
    for (e = the_queue.queue.next; e != &the_queue.queue && n_order < MAX_E; e = e->next)
    {
        for (i = 0; i < n; i++) if (&ents[i].entry == e) break;
        if (i == n) { flags |= QUEUE_BROKEN; break; }
        if (gone[i]) { flags |= QUEUE_HOLDS_GONE; break; }
        order[n_order++] = big ? 'x' : '0' + i;
    }
    if (!(flags & (QUEUE_BROKEN | QUEUE_HOLDS_GONE)))
        for (e = the_queue.queue.next; ; e = e->next) { if (e->next->prev != e || e->prev->next != e) flags |= QUEUE_BROKEN; if (e == &the_queue.queue) break; }
    order[n_order] = 0;
    alerts_ok = n_alerts == n_want;
    for (i = 0; i < n_alerts; i++) { if (alerts[i] >= 0xdead0000u) flags |= ALERTED_GARBAGE; if (i < n_want && alerts[i] != want[i]) alerts_ok = 0; }
    /* flags, alerts right, batches, the queue afterwards, lock free, which entries read NULL */
    printf( "K %d %d %s %s %d ", flags, alerts_ok, batches[0] ? batches : "-", n_order ? order : "-", the_queue.lock == 0 );
    for (i = 0; i < (big ? 1 : n); i++) putchar( ents[i].addr ? '1' : '0' );
    putchar( '\n' );
}

int main( void )
{
    char line[128];
    while (fgets( line, sizeof(line), stdin ))
    {
        int a, b, c, d, e; unsigned int m;
        if (sscanf( line, "W %d %d %d %d %d", &a, &b, &c, &d, &e ) == 5) waiter( a, b, c, d, e );
        else if (sscanf( line, "K %d %d %d %d %u %d", &a, &b, &c, &d, &m, &e ) == 6) waker( a, b, c, d, m, e );
    }
    return 0;
}
"""

P_WOKEN, P_LEFTOVER, P_TIMEOUT, P_CLEARED_ONLY, P_HELD = 1, 2, 3, 4, 5

if wine:
    text = (wine / 'dlls/ntdll/sync.c').read_text()
    stock = wo.unpatch_text(text)
    patched = wo.patch_text(stock)

    # ---------------------------------------------------------------- 1. the script
    check('the script changes sync.c and adds its marker', patched != stock and wo.MARKER in patched and wo.MARKER not in stock)
    check('a second run changes nothing', wo.patch_text(patched) == patched)
    check('undoing the edits gives back the text before the patch', wo.unpatch_text(patched) == stock)
    for name, anchor in (('waiter', wo.WAIT_OLD), ('wake-all', wo.ALL_OLD), ('wake-single', wo.SINGLE_OLD)):
        try:
            wo.patch_text(stock.replace(anchor, anchor.replace('entry', 'entry ', 1)))
            check('a moved %s anchor fails' % name, False)
        except SystemExit:
            check('a moved %s anchor fails' % name, True)
    head = (root / 'tools/patch-wine-woa-wake-order.py').read_text().split('"""')[0]
    check('the header names Wine\'s commit and author', 'Paul Gofman' in head and '81e52628b25' in head)

    recheck = load('patch-wine-woa-recheck')
    base = recheck.unpatch_pe(stock) if recheck.MARKER in stock else stock
    one = wo.patch_text(recheck.patch_pe(base))
    two = recheck.patch_pe(wo.patch_text(base))
    check('with tools/patch-wine-woa-recheck.py: both apply in either order to the same text, and a second run of each changes nothing',
          one == two and wo.MARKER in one and recheck.MARKER in one and wo.patch_text(one) == one and recheck.patch_pe(one) == one)
    check('undoing woa-recheck on the result leaves this patch, and the other way round',
          recheck.unpatch_pe(one) == wo.patch_text(base) and wo.unpatch_text(one) == recheck.patch_pe(base))
    tmp = Path(tempfile.mkdtemp(prefix='woa-wake-order-sync-'))
    try:
        (tmp / 'dlls/ntdll').mkdir(parents=True)
        for name in ('ntdll.spec', 'Makefile.in'):
            shutil.copy(wine / 'dlls/ntdll' / name, tmp / 'dlls/ntdll' / name)
        (tmp / 'dlls/ntdll/sync.c').write_text(one)
        r = subprocess.run([sys.executable, str(root / 'tools/patch-wine-ntdll-sync.py'), str(tmp)], capture_output=True, text=True)
        after = (tmp / 'dlls/ntdll/sync.c').read_text()
        check('tools/patch-wine-ntdll-sync.py (the opt-in 64-bit ntdll-sync.dll) still applies on top and keeps this patch\'s lines',
              r.returncode == 0 and after != one and all(new in after for _h, _o, new in wo.EDITS) and wo.DEFINE in after,
              (r.stdout + r.stderr)[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 2. what each build compiles
    body = patched.replace(wo.DEFINE, '')
    check('every build but i386 compiles the file as it was: with the macro not defined the patched file is the file before the patch, line for line',
          resolve(body, False) == stock and patched.count(wo.DEFINE) == 1)
    check('the macro is defined for __i386__ alone', '#ifdef __i386__\n#define MADEIRA_WOA_WAKE_ORDER 1\n#endif\n' in wo.DEFINE
          and patched.count('#define MADEIRA_WOA_WAKE_ORDER') == 1 and patched.index(wo.DEFINE) < patched.index(wo.WAIT_HEAD))
    check('no changed place says "#ifdef __i386__" itself (check-woa-recheck.py turns that line on for the host)',
          all('__i386__' not in new for _h, _o, new in wo.EDITS))
    on = resolve(body, True)
    f_wait, f_all, f_single = cut(on, wo.WAIT_HEAD), cut(on, wo.ALL_HEAD), cut(on, wo.SINGLE_HEAD)
    clear = 'WritePointerRelease( (void * volatile *)&entry->addr, NULL );'
    check('with the macro: the waiter\'s unlocked look is Wine\'s ReadPointerAcquire, the locked one is unchanged',
          f_wait.count('if (ReadPointerAcquire( (void * volatile *)&entry.addr ))') == 1 and f_wait.count('if (entry.addr)') == 1
          and f_wait.index('ReadPointerAcquire') < f_wait.index('spin_lock( &queue->lock );', f_wait.index('ReadPointerAcquire')) < f_wait.index('if (entry.addr)'))
    check('with the macro: RtlWakeAddressAll reads the thread id, unlinks, clears, and alerts the id it read',
          'entry->addr = NULL' not in f_all and f_all.count(clear) == 1
          and f_all.index('tid = entry->tid;') < f_all.index('list_remove( &entry->entry );') < f_all.index(clear)
          < f_all.index('tids[count++] = (HANDLE)(ULONG_PTR)tid;') and 'tids[count++] = (HANDLE)(ULONG_PTR)entry->tid;' not in f_all)
    check('with the macro: RtlWakeAddressSingle reads the thread id, unlinks, clears',
          'entry->addr = NULL' not in f_single and f_single.count(clear) == 1
          and f_single.index('tid = entry->tid;') < f_single.index('list_remove( &entry->entry );') < f_single.index(clear) < f_single.index('break;'))
    before = set(stock.split('\n'))
    added = sorted(set(l.strip() for l in on.split('\n') if l not in before))
    check('with the macro the new lines are these four and nothing else (Wine\'s commit; the thread id is declared where it is read)',
          added == sorted(['if (ReadPointerAcquire( (void * volatile *)&entry.addr ))', 'DWORD tid = entry->tid;', clear,
                           'tids[count++] = (HANDLE)(ULONG_PTR)tid;']), added)

    # ---------------------------------------------------------------- 3. the real functions
    cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
    structs = cut(stock, 'struct futex_entry\n{', '\n};\n') + cut(stock, 'struct futex_queue\n{', '\n};\n')
    helpers = (cut(stock, 'static void spin_lock( LONG *lock )') + cut(stock, 'static void spin_unlock( LONG *lock )')
               + cut(stock, 'static BOOL compare_addr( const void *addr, const void *cmp, SIZE_T size )'))

    def three(src, suffix):
        out = ''
        for h in (wo.WAIT_HEAD, wo.ALL_HEAD, wo.SINGLE_HEAD):
            out += cut(src, h)
        for name in ('RtlWaitOnAddress', 'RtlWakeAddressAll', 'RtlWakeAddressSingle'):
            out = re.sub(r'\b%s\(' % name, name + suffix + '(', out)
        return out

    src = (HARNESS_HEAD + structs + HARNESS_QUEUE + helpers + three(stock, '_old')
           + '#define MADEIRA_WOA_WAKE_ORDER 1\n' + three(body, '_new') + '#undef MADEIRA_WOA_WAKE_ORDER\n' + HARNESS_TAIL)
    tmp = Path(tempfile.mkdtemp(prefix='woa-wake-order-'))
    try:
        (tmp / 't.c').write_text(src)
        r = subprocess.run([cc, '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-unused-variable',
                            '-Wno-unused-but-set-variable', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                            '-o', str(tmp / 't'), str(tmp / 't.c')], capture_output=True, text=True)
        check('the real functions, old and new, compile with -Wall -Wextra -Werror under ASan + UBSan', r.returncode == 0, r.stderr)
        if r.returncode == 0:
            def run(lines):
                p = subprocess.run([str(tmp / 't')], input=''.join(l + '\n' for l in lines), capture_output=True, text=True, timeout=300)
                out = p.stdout.split('\n')[:-1]
                if p.returncode or len(out) != len(lines):
                    check('the harness ran %d lines' % len(lines), False, (p.stdout[-300:] + p.stderr[-800:]))
                    return None
                return [o.split() for o in out]

            # a. the waiter
            res = run(['W %d %d 4 7 %d' % (new, script, t) for script in (P_CLEARED_ONLY, P_LEFTOVER, P_HELD) for new in (0, 1) for t in (0, 1)])
            if res:
                cleared, leftover, held = res[0:4], res[4:8], res[8:12]
                check('the real waiter, old and new, with and without a time-out: a park that returns with entry.addr NULL makes it return success '
                      'at once: not one attempt on the lock, and its entry is still in the queue (so the entry is gone while a waker may still hold it)',
                      all(r == ['W', '0', '1', '0', '0', '1', '1'] for r in cleared), cleared)
                check('the real waiter: a park that returns with entry.addr still set takes the lock and removes its own entry',
                      all(r == ['W', '0', '1', '1', '0', '0', '1'] for r in leftover), leftover)
                check('the real waiter: with entry.addr still set and a waker holding the lock it only spins; when the waker has finished '
                      '(unlinked, then cleared) it does not unlink again',
                      all(r[1] == '0' and r[2] == '1' and int(r[3]) >= 2 and int(r[4]) >= 1 and r[5] == '0' and r[6] == '1' for r in held), held)

            # b. and c. the wakers
            cases = []
            for n in range(1, 6):
                for mask in range(1, 1 << n):
                    cases.append((n, mask, 0))
            cases.append((300, 0, 1))
            rng = random.Random(660)
            randoms = [(rng.randint(1, 5), 0, 0) for _ in range(2000)]
            randoms = [(n, rng.randrange(0, 1 << n), 0) for n, _m, _b in randoms]
            lines = []
            for race in (1, 0):
                for n, mask, big in (cases if race else cases + randoms):
                    for allw in (1, 0):
                        for new in (0, 1):
                            lines.append('K %d %d %d %d %u %d' % (new, allw, race, n, mask, big))
            res = run(lines)
            if res:
                n_race = len(cases) * 4
                race_rows, calm_rows = res[:n_race], res[n_race:]
                new_bad, old_all_bad, old_single_bad, shown_all, shown_single = [], [], [], 0, 0
                for i in range(0, n_race, 4):
                    all_old, all_new, single_old, single_new = race_rows[i:i + 4]
                    for row in (all_new, single_new):
                        if row[1] != '0' or row[2] != '1' or row[5] != '1':
                            new_bad.append((lines[i], row))
                    if int(all_old[1]) & 7 == 7:
                        shown_all += 1
                    else:
                        old_all_bad.append((lines[i], all_old))
                    if int(single_old[1]) & 3 == 3 and not int(single_old[1]) & 4:
                        shown_single += 1
                    else:
                        old_single_bad.append((lines[i + 2], single_old))
                check('new wakers with a waiter that leaves at the first step where its entry reads NULL, %d queues (every subset of one to five '
                      'entries, and 300 woken at once): no entry is touched after that step, the queue is intact, exactly the right threads are '
                      'alerted in order' % len(cases), not new_bad, new_bad[:3])
                check('old RtlWakeAddressAll, the same %d queues: in every one it unlinks an entry whose owner has left, leaves the queue '
                      'pointing at it, and alerts a thread id read from the overwritten entry' % len(cases),
                      shown_all == len(cases), old_all_bad[:3])
                check('old RtlWakeAddressSingle, the same %d queues: in every one it unlinks an entry whose owner has left and leaves the queue '
                      'pointing at it (the thread id it read before is right)' % len(cases), shown_single == len(cases), old_single_bad[:3])
                diff = [(lines[n_race + i], calm_rows[i], calm_rows[i + 1]) for i in range(0, len(calm_rows), 2) if calm_rows[i] != calm_rows[i + 1]]
                clean = all(r[1] == '0' and r[2] == '1' and r[5] == '1' for r in calm_rows)
                big = [r for l, r in zip(lines[n_race:], calm_rows) if l.endswith(' 1') and l.split()[2] == '1']
                check('without such a waiter old and new do the same in %d calls (%d queues, both wakers): the same alerts in the same batches, '
                      'the same queue left, the same entries marked; 300 woken at once go out as 256 and 44'
                      % (len(calm_rows) // 2, len(cases) + len(randoms)),
                      not diff and clean and big and all(r[3] == '256,44,' for r in big), (diff[:2], big[:1]))

            # c. the waiter over every scripted park
            lines = ['W %d %d %d %d %d' % (new, script, size, cmpv, t) for script in (P_WOKEN, P_LEFTOVER, P_TIMEOUT, P_CLEARED_ONLY, P_HELD)
                     for size in (1, 2, 3, 4, 8) for cmpv in (7, 8) for t in (0, 1) for new in (0, 1)]
            res = run(lines)
            if res:
                diff = [(lines[i], res[i], res[i + 1]) for i in range(0, len(res), 2) if res[i] != res[i + 1]]
                statuses = set(r[1] for r in res)
                check('the waiter, old and new, over %d scripted parks (woken, left-over alert, time-out, cleared only, lock held; sizes 1, 2, 3, 4, 8; '
                      'value equal or already changed; with and without a time-out): the same status, the same lock use, the same queue'
                      % (len(lines) // 2), not diff and statuses == {'0', '102', 'c000000d'}, (diff[:3], statuses))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------------------------------------------------------- 4. the whole file for i686
    tc = root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
    gen = next((g for g in (wine / 'build-i386/include', wine / 'build-macos/include') if g.is_dir()), None)
    if (tc / 'i686-w64-mingw32-clang').exists() and gen:
        tmp = Path(tempfile.mkdtemp(prefix='woa-wake-order-pe-'))
        try:
            (tmp / 'sync.c').write_text(one)
            r = subprocess.run([str(tc / 'i686-w64-mingw32-clang'), '-fsyntax-only', '-O2', '-Wall', '-Wdeclaration-after-statement', '-D__WINESRC__',
                                '-D_NTSYSTEM_', '-D_ACRTIMP=', '-DWINBASEAPI=', '-D__WINE_PE_BUILD', '-D_MSVCR_VER=0',
                                '-I' + str(wine / 'dlls/ntdll'), '-I' + str(gen), '-I' + str(wine / 'include'), '-I' + str(wine / 'include/msvcrt'),
                                str(tmp / 'sync.c')], capture_output=True, text=True)
            check('the patched ntdll/sync.c (with woa-recheck) compiles for i686', r.returncode == 0 and ' error' not in r.stderr, r.stderr[-1500:])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        print('note: no llvm-mingw or generated headers in this tree; the compile runs in the full tree')

# ---------------------------------------------------------------- 5. the build
ci = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-woa-wake-order.py wine/dlls/ntdll/sync.c'
farm = ci.find('- name: Build i386 farm (WoW64)')
check('the build applies the script before the i386 farm is built', step in ci and 0 < ci.index(step) < farm)
check('CI runs this check', 'python3 tests/host/check-woa-wake-order.py' in ci)
check('the script stays in the tree for the 64-bit ntdll builds (not put back): tools/build-wine-extra-dlls.sh restores sync.c only after '
      'its own patched build', 'git -C "$R/wine" checkout -- dlls/ntdll/sync.c dlls/ntdll/ntdll.spec dlls/ntdll/Makefile.in'
      in (root / 'tools/build-wine-extra-dlls.sh').read_text())
check('the notices name the script and Wine\'s commit', 'patch-wine-woa-wake-order.py' in (root / 'THIRD-PARTY-NOTICES.md').read_text()
      and '81e52628b25' in (root / 'THIRD-PARTY-NOTICES.md').read_text())

print('PASS: wait-on-address wake order' if ok else 'FAILED: wait-on-address wake order')
sys.exit(0 if ok else 1)
