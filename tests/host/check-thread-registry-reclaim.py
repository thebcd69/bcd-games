#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Mach thread registry gives a full table the slots of threads that ended (round 630).

The registry (build/ntdll-unix/signal_arm64_ios.c) maps a Mach thread name to its TEB for the exception thread. It has 512
slots and never gave one back: a registered name is pinned (+4 send refs, ml401), so an exited thread's name stays a dead
name and is never recycled, and every thread after the 512th of a session went unregistered (its Mach events resolved to
slot 0, another process's TEB). NARUTO SHIPPUDEN: Ultimate Ninja STORM 3 (XB8E37) and Fallout: New Vegas (H1TRKP) ended on
such a thread.

Compiles the production ios_thread_registry_reclaim and the registration block of ios_setup_mach_exception_handler on the
host, with thread_info / mach_port_type / mach_port_mod_refs answered by a model, and checks:
  - below 512 threads nothing changes: slots are handed out in order, no reclaim runs;
  - full and every thread alive: the new thread is NOT registered and the FULL line is printed as before;
  - full with ended threads: the new thread gets the slot of one whose name is a dead name or whose thread_info answers
    KERN_TERMINATED; a slot that answers anything else (not sure), whose name is no dead name, whose name is 0, or slot 0
    (the lookup's fallback) is never taken; the slot's old TEB is not reported as a REPLACE;
  - the search goes on after the slot it last took;
  - a thread that registers again under its name still replaces its own entry (ml384), full or not;
  - eight threads registering at once against 200 ended slots: each slot is taken once, every new name lands in exactly
    one slot, no live entry is overwritten.
Needs python3 and a C compiler (AddressSanitizer/UBSan when available); on a host without <mach/mach.h> it says so and passes.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
fails = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        fails.append(what)


def between(first, last, src=source):
    start = src.index(first)
    return src[start:src.index(last, start) + len(last)]


def function(signature):
    start = source.index(signature)
    end = source.index('\n}\n', start) + 3
    return source[start:end]


table = between('#define IOS_MAX_WINE_THREADS 512', 'static volatile int32_t ios_thread_count = 0;')
reclaim = function('static int ios_thread_registry_reclaim( thread_t pe_thread )')
reg_start = source.index('    /* Register this thread in the registry.')
reg_end = source.index('    /* Set exception port for this thread (shared port).', reg_start)
register = source[reg_start:reg_end]

# Source checks: where the reclaim runs and what it never takes.
check('the reclaim runs only when the registry is full, before the FULL line',
      re.search(r'if \(idx >= IOS_MAX_WINE_THREADS\)\s*\{\s*/\*[^*]*\*/\s*idx = ios_thread_registry_reclaim\( pe_thread \);\s*'
                r'if \(idx < 0\)\s*ERR\("\[thread-registry\] FULL \(%d slots\)', register) is not None)
check('slot 0 (the lookup\'s fallback) is never searched', 'i = 1 + (start - 1 + n) % (IOS_MAX_WINE_THREADS - 1);' in reclaim)
check('a slot is claimed with a compare-and-swap of its name to 0',
      '__sync_bool_compare_and_swap( &ios_thread_registry[i].mach_thread, port, 0 )' in reclaim)
check('only a dead name or KERN_TERMINATED counts as ended',
      'if (kr == KERN_SUCCESS) continue;' in reclaim and 'kr != KERN_TERMINATED &&' in reclaim
      and '(type & MACH_PORT_TYPE_DEAD_NAME)' in reclaim)
check('the dead name keeps its pinned refs (no deallocate in the reclaim)', 'mach_port_deallocate' not in reclaim
      and 'mod_refs' not in reclaim)
check('the lookup is unchanged: first match wins, slot-0 fallback',
      'static int ios_lookup_thread(thread_t mach_thread, uintptr_t *teb_out, void **tramp_out)' in source
      and source.index('static int ios_thread_registry_reclaim') < source.index('static int ios_lookup_thread('))
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-thread-registry-reclaim.py' in wf)

code = r'''
#include <mach/mach.h>
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ERR(...) fprintf(stderr, __VA_ARGS__)

/* The model of the Mach answers, by name. */
enum { ALIVE, DEAD, TERMINATED, UNSURE, NONAME };
static int st[1 << 16];
static kern_return_t fake_thread_info( thread_inspect_t t, thread_flavor_t f, thread_info_t info, mach_msg_type_number_t *n )
{
    (void)f; (void)info; (void)n;
    switch (st[t])
    {
    case ALIVE: return KERN_SUCCESS;
    case TERMINATED: return KERN_TERMINATED;
    case UNSURE: return KERN_FAILURE;
    default: return MACH_SEND_INVALID_DEST;   /* a dead name, or a name we hold no right to */
    }
}
static kern_return_t fake_mach_port_type( ipc_space_t task, mach_port_name_t name, mach_port_type_t *type )
{
    (void)task;
    if (st[name] == NONAME) return KERN_INVALID_NAME;
    *type = st[name] == DEAD ? MACH_PORT_TYPE_DEAD_NAME : MACH_PORT_TYPE_SEND;
    return KERN_SUCCESS;
}
static int modrefs;
static kern_return_t fake_mod_refs( ipc_space_t task, mach_port_name_t name, mach_port_right_t right, mach_port_delta_t delta )
{
    (void)task; (void)name; (void)right; (void)delta;
    __sync_add_and_fetch( &modrefs, 1 );
    return KERN_SUCCESS;
}
#define thread_info fake_thread_info
#define mach_port_type fake_mach_port_type
#define mach_port_mod_refs fake_mod_refs
/* The trampoline page (virtual_ios.c): slot k's trampoline is base + 8 + 16 k; released slots are recorded. */
static int released[1024], nreleased;
int ios_jit_trampoline_slot_of( const void *trampoline )
{
    uintptr_t t = (uintptr_t)trampoline;
    if (t < 0x148000008u || (t - 0x148000008u) % 16 || (t - 0x148000008u) / 16 >= 1024) return -1;
    return (int)((t - 0x148000008u) / 16);
}
void ios_jit_release_trampoline_slot( int slot ) { if (slot > 0) released[nreleased++] = slot; }
#define TRAMP(slot) ((void *)(uintptr_t)(0x148000008u + 16u * (slot)))
'''
code += table + '\n\n' + reclaim + '\n'
code += r'''
static void reg( thread_t pe_thread, uintptr_t teb, void *trampoline )
{
''' + register + r'''
}

static int slot_of( thread_t port )
{
    for (int i = 0; i < IOS_MAX_WINE_THREADS; i++) if (ios_thread_registry[i].mach_thread == port) return i;
    return -1;
}
static uintptr_t teb_for( thread_t port ) { return 0x7000000000ull + ((uintptr_t)port << 16); }

static void reset( void )
{
    memset( ios_thread_registry, 0, sizeof(ios_thread_registry) );
    ios_thread_count = 0;
    memset( st, 0, sizeof(st) );
}

#define N 8
#define PER 40
static void *racer( void *arg )
{
    int k = (int)(intptr_t)arg;
    for (int j = 0; j < PER; j++)
    {
        thread_t p = 30000 + k * 1000 + j;
        st[p] = ALIVE;
        reg( p, teb_for( p ), NULL );
    }
    return NULL;
}

int main( void )
{
    /* 1. below 512: in order, nothing reclaimed */
    reset();
    for (thread_t p = 1; p <= 512; p++) { st[p] = ALIVE; reg( p, teb_for( p ), TRAMP( p - 1 ) ); }
    for (int i = 0; i < 512; i++) assert( ios_thread_registry[i].mach_thread == (thread_t)(i + 1) && ios_thread_registry[i].teb == teb_for( i + 1 ) );
    assert( ios_thread_count == 512 );
    puts( "ok: 512 threads take the 512 slots in order" );

    /* 2. full, every thread alive: not registered, FULL as before */
    st[600] = ALIVE;
    reg( 600, teb_for( 600 ), NULL );
    assert( slot_of( 600 ) < 0 );
    for (int i = 0; i < 512; i++) assert( ios_thread_registry[i].mach_thread == (thread_t)(i + 1) );
    puts( "ok: full and every thread alive: the new thread is not registered" );

    /* 3. ended threads: dead names and KERN_TERMINATED are taken; unsure, no-name, slot 0 never */
    st[1] = DEAD;          /* slot 0: never */
    st[3] = UNSURE;        /* slot 2: kept */
    st[4] = NONAME;        /* slot 3: no right at all, not a dead name: kept */
    st[8] = DEAD;          /* slot 7 */
    st[9] = TERMINATED;    /* slot 8 */
    ios_thread_registry[20].mach_thread = 0; ios_thread_registry[20].teb = 0;   /* slot 20: purged, skipped */
    st[601] = ALIVE; reg( 601, teb_for( 601 ), TRAMP( 900 ) );
    assert( slot_of( 601 ) == 7 && ios_thread_registry[7].teb == teb_for( 601 ) && ios_thread_registry[7].trampoline == TRAMP( 900 ) );
    assert( nreleased == 1 && released[0] == 7 );   /* the ended thread's trampoline slot (7) is released */
    st[602] = ALIVE; reg( 602, teb_for( 602 ), NULL );
    assert( slot_of( 602 ) == 8 && nreleased == 2 && released[1] == 8 );
    st[603] = ALIVE; reg( 603, teb_for( 603 ), NULL );
    assert( slot_of( 603 ) < 0 );
    assert( ios_thread_registry[0].mach_thread == 1 && ios_thread_registry[2].mach_thread == 3 && ios_thread_registry[3].mach_thread == 4 );
    assert( ios_thread_registry[20].mach_thread == 0 );
    puts( "ok: a dead name or KERN_TERMINATED frees a slot; unsure, no right, a purged slot and slot 0 are kept" );

    /* 4. the search goes on after the slot it last took */
    st[5] = DEAD;          /* slot 4: before the cursor (9) */
    st[400] = DEAD;        /* slot 399 */
    st[604] = ALIVE; reg( 604, teb_for( 604 ), NULL );
    assert( slot_of( 604 ) == 399 );
    st[605] = ALIVE; reg( 605, teb_for( 605 ), NULL );
    assert( slot_of( 605 ) == 4 );
    puts( "ok: the search starts after the last slot it took and wraps past slot 0" );

    /* 5. a name registering again replaces its own entry, full or not */
    reg( 601, teb_for( 601 ) + 0x1000, NULL );
    assert( slot_of( 601 ) == 7 && ios_thread_registry[7].teb == teb_for( 601 ) + 0x1000 );
    puts( "ok: a thread that registers again still replaces its own entry" );

    /* 6. eight registrations at once against 200 ended slots */
    reset();
    for (thread_t p = 1; p <= 512; p++) { st[p] = ALIVE; reg( p, teb_for( p ), NULL ); }
    st[1] = DEAD; ios_thread_registry[0].trampoline = TRAMP( 0 );
    st[2] = DEAD; ios_thread_registry[1].trampoline = TRAMP( 0 );   /* a thread that shared slot 0: slot 0 is never released */
    nreleased = 0;
    st[700] = ALIVE; reg( 700, teb_for( 700 ), NULL );
    assert( slot_of( 700 ) == 1 && nreleased == 0 );
    st[2] = ALIVE; ios_thread_registry[1].mach_thread = 2;   /* back to the race's starting state */
    ios_thread_registry[1].teb = teb_for( 2 ); st[700] = 0;
    srand( 630 );
    int dead = 0;
    static char was_dead[513];
    while (dead < 200)
    {
        int i = 1 + rand() % 511;
        if (was_dead[i]) continue;
        was_dead[i] = 1; st[ios_thread_registry[i].mach_thread] = DEAD; dead++;
    }
    pthread_t th[N];
    for (int k = 0; k < N; k++) pthread_create( &th[k], NULL, racer, (void *)(intptr_t)k );
    for (int k = 0; k < N; k++) pthread_join( th[k], NULL );
    int placed = 0;
    for (int k = 0; k < N; k++)
        for (int j = 0; j < PER; j++)
        {
            thread_t p = 30000 + k * 1000 + j;
            int hits = 0;
            for (int i = 0; i < IOS_MAX_WINE_THREADS; i++) if (ios_thread_registry[i].mach_thread == p) { hits++; assert( was_dead[i] && ios_thread_registry[i].teb == teb_for( p ) ); }
            assert( hits <= 1 );
            placed += hits;
        }
    assert( placed == 200 );
    for (int i = 0; i < IOS_MAX_WINE_THREADS; i++) if (!was_dead[i]) assert( ios_thread_registry[i].mach_thread == (thread_t)(i + 1) );
    puts( "ok: 320 registrations racing for 200 ended slots: 200 placed once each, no live entry overwritten" );
    return 0;
}
'''

# The trampoline page (build/ntdll-unix/virtual_ios.c): released slots serve new threads once the page is used up.
virt = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
s0 = virt.index('#define IOS_JIT_TRAMPOLINE_SIZE 16')
s1 = virt.index('void *ios_jit_get_trampoline(int slot)', s0)
slots = virt[s0:virt.index('\n}\n', s1) + 3]
check('the trampoline allocator takes a released slot only once the page is used up, never slot 0',
      'if (slot >= IOS_JIT_MAX_SLOTS)\n    {' in slots and 'if (slot <= 0 || slot >= IOS_JIT_MAX_SLOTS) return;' in slots
      and slots.index('__sync_fetch_and_add(&ios_jit_next_slot, 1)') < slots.index('ios_jit_free_slots[--ios_jit_free_slot_count]'))
check('the registry reclaim releases the ended thread\'s slot before clearing the entry',
      reclaim.index('ios_jit_release_trampoline_slot( ios_jit_trampoline_slot_of( ios_thread_registry[i].trampoline ) );')
      < reclaim.index('ios_thread_registry[i].trampoline = NULL;'))
slot_code = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
static char page_rx[0x4000], page_rw[0x4000];
void *ios_jit_rx_base_global = page_rx, *ios_jit_rw_base_global = page_rw;
static void sys_icache_invalidate( void *p, size_t n ) { (void)p; (void)n; }
''' + slots + r'''
int main( void )
{
    for (int k = 0; k < 1024; k++) assert( ios_jit_alloc_trampoline_slot() == k );
    assert( ios_jit_alloc_trampoline_slot() == 0 );                  /* used up, nothing released: slot 0, as before */
    ios_jit_release_trampoline_slot( 0 ); ios_jit_release_trampoline_slot( -1 ); ios_jit_release_trampoline_slot( 1024 );
    assert( ios_jit_alloc_trampoline_slot() == 0 );                  /* slot 0 and out-of-range slots are never released */
    ios_jit_release_trampoline_slot( 5 ); ios_jit_release_trampoline_slot( 700 );
    memset( page_rw + 700 * 16 + 8, 0, 8 );
    assert( ios_jit_alloc_trampoline_slot() == 700 );
    assert( *(uint32_t *)(page_rw + 700 * 16 + 8) == 0x58FFFFD2 && *(uint32_t *)(page_rw + 700 * 16 + 12) == 0xD61F0220 );
    assert( ios_jit_alloc_trampoline_slot() == 5 );
    assert( ios_jit_alloc_trampoline_slot() == 0 );
    for (int k = 0; k < 1024; k++) assert( ios_jit_trampoline_slot_of( ios_jit_get_trampoline( k ) ) == k );
    assert( ios_jit_trampoline_slot_of( page_rx ) == -1 && ios_jit_trampoline_slot_of( page_rx + 9 ) == -1
            && ios_jit_trampoline_slot_of( page_rx + 8 + 1024 * 16 ) == -1 && ios_jit_trampoline_slot_of( NULL ) == -1 );
    puts( "ok: 1,024 slots in order; then released slots (their code rewritten), slot 0 when none; slot 0 never released" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='madeira-tramp-slots-') as directory:
    src = Path(directory) / 'slots.c'
    src.write_text(slot_code)
    exe = Path(directory) / 'slots'
    cc = os.environ.get('CC', 'cc')
    r = subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-g', '-pthread', str(src), '-o', str(exe)],
                       capture_output=True, text=True)
    check('the slot harness builds: ' + r.stderr[-1200:], r.returncode == 0)
    if r.returncode == 0:
        res = subprocess.run([str(exe)], capture_output=True, text=True)
        print(res.stdout, end='')
        check('trampoline slots: ' + ('all passed' if res.returncode == 0 else (res.stdout + res.stderr)[-800:]), res.returncode == 0)
        check('the slots log what they did', '[thread-tables] no trampoline slot of an ended thread is free' in res.stderr
              and '[thread-tables] trampoline slot 700 of an ended thread reused rev=r630' in res.stderr)

if not Path('/usr/include/mach/mach.h').exists() and subprocess.run(['xcrun', '--show-sdk-path'], capture_output=True).returncode != 0:
    print('skip: no <mach/mach.h> on this host (the Mac and CI run this check)')
    if fails:
        sys.exit(1)
    print('PASS: thread registry reclaim (runtime part skipped here)')
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix='madeira-thread-registry-') as directory:
    folder = Path(directory)
    src = folder / 'check.c'
    src.write_text(code)
    exe = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-g', '-pthread', str(src), '-o', str(exe)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode == 0:
        print('built with AddressSanitizer/UBSan')
    else:
        r = subprocess.run(flags, capture_output=True, text=True)
        check('the harness builds: ' + r.stderr[-1500:], r.returncode == 0)
    if exe.exists():
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        print(result.stdout, end='')
        check('runtime checks: ' + (result.stderr[-1500:] if result.returncode else 'all passed'), result.returncode == 0)
        err = result.stderr
        check('full and all alive: the FULL line is printed as before',
              '[thread-registry] FULL (512 slots) — thread 0x258' in err)
        check('a reused slot is logged with the ended thread\'s name and the new one',
              '[thread-registry] full: slot 7 of an exited thread (port 0x8) reused for thread 0x259 (#1) rev=r630' in err)
        check('a reused slot is not reported as a REPLACE (the one REPLACE of slot 7 is the thread registering again)',
              err.count('REPLACE idx=7 port=0x259') == 1 and err.count('REPLACE idx=8 ') == 0 and err.count('REPLACE idx=399 ') == 0)

if fails:
    print('FAILED: thread registry reclaim')
    sys.exit(1)
print('PASS: a full thread registry takes the slots of threads that ended, never a live one, slot 0 or a slot twice')
