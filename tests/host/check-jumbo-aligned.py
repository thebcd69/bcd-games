#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The jumbo aligned walk (upstream Madeira v0.2.0 ml2020, BCD's form); no Wine runs.

A hinted reservation of 1-4 GB at a 4 GB boundary is a request for a 4 GB-aligned base (V8's trusted range). Social Club's browser helper
asked for 1 GB at such a hint; in the GTA IV log FM66KY (build 630) the aligned gigabyte 0x7300000000 was free, BCD's offset-preserving
walk tried only 0x7100/0x7200/0x7500/0x7600/0x7900/0x7a00, the kernel's unhinted pick handed out an unaligned gigabyte, Chromium freed it
three times, over-reserved 5119 MB, found no 5 GB hole and closed the helper (0xe0000008): the Launcher's "CEF browser crashed".

The walk runs after every other attempt (the offset walk, layout 2's route, the boot holdback, the cage, MADEIRA_SC_ALIGN1G) found
nothing and before the unhinted fall-back; it tries the 4 GB-aligned bases of the furniture band [ios_usable_va_floor, ios_furniture_ceiling),
top down. Unlike upstream's (0x7b00000000 down to 0x7000000000, which leans on window-keep and arena-guard defaults BCD does not have) it
stays below the ceiling, where the unhinted pick would have put the same gigabyte, and map_fixed_area refuses, under the lock, a base that
touches a reserved area (a window or placeholder, the FEX arena, a holdback) or the free 4 GB slot an armed session keeps for its next
32-bit program.

Part A reads the sources (wiring, order, gates, the switch MADEIRA_JUMBO_ALIGNED=1 (BCD: off by default), map_fixed_area's refusal). Part B compiles the
production walk block, the production refusal and the production switch parser against a model address space (kernel regions plus a
reserved-area list) and runs FM66KY's geometry and its neighbours.
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


# ---------------------------------------------------------------- Part A: the sources
retry = src[src.index('/* task#29 CEF plan C: a HINTED jumbo reserve that fails placement'):]
retry = retry[:retry.index('            sc_decided:')]
b0 = retry.index('if (st2 && !((ULONG_PTR)hint & 0xffffffffULL) && *size_ptr <= 0x100000000ULL &&')
b1 = retry.index('                if (st2)\n                {\n                    pick = NULL;', b0)
block = retry[b0:b1]
check('the walk sits right before the unhinted fall-back and after every other attempt',
      all(retry.index(e) < b0 for e in ('if (sc2 && ios_sc2_route(', 'if (st2 && off)', 'ios_jumbo_holdback_take( *size_ptr )',
                                         'st2 = ios_cage_grant(', 'cage4_slots', 'st2 = ios_sc2_align1g( type, protect, &pick, &sz );')) and
      retry[b1:].lstrip().startswith('if (st2)'))
check('it needs a failed ask, a hint on a 4 GB boundary, at most 4 GB, a ceiling above the size and the switch',
      block.startswith('if (st2 && !((ULONG_PTR)hint & 0xffffffffULL) && *size_ptr <= 0x100000000ULL &&\n'
                       '                    ios_furniture_ceiling > *size_ptr && ios_jumbo_aligned_enabled())'))
check('every try is a plain allocate_virtual_memory at the base with the walk flag set around it',
      block.count('allocate_virtual_memory(') == 1 and 'ios_jumbo_walk_active = 1;' in block and 'ios_jumbo_walk_active = 0;' in block and
      block.index('ios_jumbo_walk_active = 1;') < block.index('allocate_virtual_memory(') < block.index('ios_jumbo_walk_active = 0;'))
check('the bases run from the highest that fits below the ceiling down to the usable floor, 4 GB apart',
      'a = (ios_furniture_ceiling - *size_ptr) & ~(ULONG_PTR)0xffffffffULL; a >= lo && st2; a -= 0x100000000ULL' in block and
      'ULONG_PTR lo = (ios_usable_va_floor + 0xffffffffULL) & ~(ULONG_PTR)0xffffffffULL' in block)
mf = function('static NTSTATUS map_fixed_area( void *base, size_t size, int unix_prot )')
check('map_fixed_area applies the refusal right after the view-conflict test, for the walk only',
      mf.index('if (find_view_range( base, size )) return STATUS_CONFLICTING_ADDRESSES;') <
      mf.index('if (ios_jumbo_walk_active && ios_jumbo_walk_refused( start, end )) return STATUS_CONFLICTING_ADDRESSES;') <
      mf.index('LIST_FOR_EACH_ENTRY( area, &reserved_areas'))
check('the walk flag is a thread-local and nothing else sets it', 'static __thread int ios_jumbo_walk_active;' in src and
      src.count('ios_jumbo_walk_active = 1;') == 1)
sw = function('static int ios_jumbo_aligned_enabled(void)')
check('the switch: MADEIRA_JUMBO_ALIGNED, "1" alone turns it on (default off), read once', 'getenv( "MADEIRA_JUMBO_ALIGNED" )' in sw and
      "v = (e && e[0] == '1' && !e[1]);" in sw and src.count('MADEIRA_JUMBO_ALIGNED" )') == 1)

# ---------------------------------------------------------------- Part B: compiled
refused = function('static int ios_jumbo_walk_refused( const char *start, const char *end )')
harness = r'''
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t ULONG_PTR;
typedef size_t SIZE_T;
typedef unsigned int ULONG, NTSTATUS;
#define STATUS_SUCCESS 0
#define STATUS_CONFLICTING_ADDRESSES 0xc0000018u
#define STATUS_NO_MEMORY 0xc0000017u
#define IOS_WOW_WINDOW_SIZE ((ULONG_PTR)1 << 32)
#define dprintf(fd, ...) do { if (!getenv("QUIET")) fprintf(stderr, __VA_ARGS__); } while (0)
struct list { struct list *next, *prev; };
#define LIST_ENTRY(elem, type, field) ((type *)((char *)(elem) - offsetof(type, field)))
#define LIST_FOR_EACH_ENTRY(elem, list, type, field) \
    for ((elem) = LIST_ENTRY((list)->next, type, field); &(elem)->field != (list); (elem) = LIST_ENTRY((elem)->field.next, type, field))
struct reserved_area { struct list entry; void *base; size_t size; unsigned int fex_only; unsigned int thin_only; };
static struct list reserved_areas = { &reserved_areas, &reserved_areas };
static void add_area(ULONG_PTR base, size_t size)
{
    struct reserved_area *a = calloc(1, sizeof(*a));
    struct list *tail = reserved_areas.prev;
    a->base = (void *)base; a->size = size;
    a->entry.prev = tail; a->entry.next = &reserved_areas; tail->next = &a->entry; reserved_areas.prev = &a->entry;
}
/* the session: armed or not, and the candidate slot ios_wow_candidate_slot would give */
static int armed;
static ULONG_PTR candidate;
static int ios_wow_session_armed(void) { return armed; }
static ULONG_PTR ios_wow_candidate_slot(void) { return candidate; }
/* a model address space: kernel regions that make a fixed map fail */
struct reg { ULONG_PTR base, size; };
static struct reg regs[32];
static int nregs;
static const ULONG_PTR ios_furniture_ceiling = 0x73ffff0000ULL;
static ULONG_PTR floor_va = 0x7038000000ULL;
#define ios_usable_va_floor (floor_va)
@SW@
@REFUSED@
static int find_view_range_hits;
static __thread int dummy;
static NTSTATUS allocate_virtual_memory(void **ret, SIZE_T *size_ptr, ULONG type, ULONG protect, ULONG_PTR limit_low, ULONG_PTR limit_high, ULONG_PTR align, ULONG attributes)
{
    ULONG_PTR a = (ULONG_PTR)*ret, e = a + *size_ptr;
    if (ios_jumbo_walk_active && ios_jumbo_walk_refused((const char *)a, (const char *)e)) return STATUS_CONFLICTING_ADDRESSES;   /* map_fixed_area's hook */
    for (int i = 0; i < nregs; i++) if (a < regs[i].base + regs[i].size && regs[i].base < e) return STATUS_NO_MEMORY;   /* the kernel region is taken */
    regs[nregs].base = a; regs[nregs].size = *size_ptr; nregs++;
    return STATUS_SUCCESS;
}
static int ran;
/* the production block, in a function with the locals it uses */
static NTSTATUS walk(void *hint, SIZE_T size, NTSTATUS st2_in, void **pick_out)
{
    NTSTATUS st2 = st2_in;
    void *pick = NULL;
    SIZE_T sz = 0, *size_ptr = &size;
    ULONG type = 0x2000, protect = 4;
    ran = 0;
    @BLOCK@
    if (!st2) *pick_out = pick;
    return st2;
}
static void reset(void) { nregs = 0; armed = 0; candidate = 0; setenv("QUIET", "1", 1); while (reserved_areas.next != &reserved_areas) { struct list *n = reserved_areas.next; reserved_areas.next = n->next; n->next->prev = &reserved_areas; free(LIST_ENTRY(n, struct reserved_area, entry)); } }
static void occupy(ULONG_PTR base, ULONG_PTR size) { regs[nregs].base = base; regs[nregs].size = size; nregs++; }
#define G 0x40000000ULL
#define T(name, cond) do { printf("%s %s\n", (cond) ? "ok  " : "FAIL", name); fails += !(cond); } while (0)
int main(int argc, char **argv)
{
    int fails = 0;
    void *pick = NULL;
    NTSTATUS st;

    if (argc > 1 && !strcmp(argv[1], "switch")) { st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick); printf("%u %p\n", st, pick); return 0; }

    /* FM66KY: everything below 0x72fde90000 and above 0x73d4290000 is taken; the aligned GB at 0x7300000000 is free */
    reset();
    occupy(0x7038000000ULL, 0x72fde90000ULL - 0x7038000000ULL);
    occupy(0x73d4290000ULL, 0x8000000000ULL - 0x73d4290000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("FM66KY geometry: the aligned gigabyte 0x7300000000 is found first", st == 0 && pick == (void *)0x7300000000ULL);

    /* the same ask is made again (a second isolate): the next free aligned base is below, or none */
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("the second ask finds nothing (0x7200 and 0x7100 are taken) and leaves the fall-back to the caller", st != 0);

    /* an earlier attempt already succeeded: the walk does not run */
    reset();
    st = walk((void *)0x7100000000ULL, G, STATUS_SUCCESS, &pick);
    T("a ask that already has its placement is not touched", st == 0 && nregs == 0);

    /* hints that are not on a 4 GB boundary: not run */
    reset();
    st = walk((void *)0x7100010000ULL, G, STATUS_NO_MEMORY, &pick);
    T("a hint off a 4 GB boundary (PartitionAlloc's guard-style hint) does not walk", st != 0 && nregs == 0);
    st = walk((void *)0x7100000000ULL, 0x100000001ULL, STATUS_NO_MEMORY, &pick);
    T("an ask above 4 GB does not walk", st != 0 && nregs == 0);
    st = walk((void *)0x7100000000ULL, 0x100000000ULL, STATUS_NO_MEMORY, &pick);
    T("a 4 GB ask walks with bases at most 0x72ffff0000 & ~4G: first 0x7200000000", st == 0 && pick == (void *)0x7200000000ULL);

    /* a window / placeholder reserved area is never taken, even when the kernel would let a fixed map replace it */
    reset();
    add_area(0x7200000000ULL, 0x100000000ULL);        /* a window of exactly 4 GB (a borrowed guard, or MADEIRA_WOW_GUARD_INSIDE) */
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("with 0x7300000000 free and a 4 GB window at 0x7200000000: 0x7300000000", st == 0 && pick == (void *)0x7300000000ULL);
    reset(); add_area(0x7200000000ULL, 0x100004000ULL); add_area(0x7300000000ULL, 0x100000000ULL);   /* 0x7300 is a reserved area (placeholder) too */
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("windows at 0x7200 and 0x7300: the walk reaches 0x7100000000 (nothing else is in the way)", st == 0 && pick == (void *)0x7100000000ULL);
    reset(); add_area(0x7100000000ULL, 0x300000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("one reserved area over the whole aligned band: nothing is taken, the caller falls back", st != 0 && nregs == 0);
    /* the guard page of a window alone makes a base unusable: the 4 GB + guard window at 0x7200 touches 0x7300000000's first page */
    reset(); add_area(0x7200000000ULL, 0x100004000ULL); occupy(0x7100000000ULL, 0x100000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("the guard page of the window below makes 0x7300000000 unusable (the 9NNP2Q shape: the SC1b piece lifts it)", st != 0 && nregs == 1);

    /* the free 4 GB slot of an armed session is kept */
    reset(); armed = 1; candidate = 0x7100000000ULL; occupy(0x7200000000ULL, 0x200000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("armed session, only the candidate slot 0x7100000000 free: kept for the 32-bit program, nothing taken", st != 0 && nregs == 1);
    reset(); armed = 1; candidate = 0x7100000000ULL; occupy(0x7300000000ULL, 0x100000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("armed session: 0x7200000000 is outside the candidate slot and is taken", st == 0 && pick == (void *)0x7200000000ULL);
    reset(); armed = 0; candidate = 0x7100000000ULL; occupy(0x7200000000ULL, 0x200000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("session not armed: the candidate slot is just a free base and is taken", st == 0 && pick == (void *)0x7100000000ULL);

    /* the floor: layout 2's floor is 0x7100000000, the usual one lies inside the first slot: bases below it are never tried */
    reset(); floor_va = 0x7100000000ULL; occupy(0x7200000000ULL, 0x200000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("floor 0x7100000000: the base at the floor is allowed", st == 0 && pick == (void *)0x7100000000ULL);
    floor_va = 0x7200000000ULL; reset(); floor_va = 0x7200000000ULL; occupy(0x7200000000ULL, 0x200000000ULL);
    st = walk((void *)0x7100000000ULL, G, STATUS_NO_MEMORY, &pick);
    T("floor 0x7200000000 with 0x7200 and 0x7300 taken: nothing below the floor is tried", st != 0 && nregs == 1);
    floor_va = 0x7038000000ULL;

    /* the refusal function alone */
    reset(); add_area(0x7200000000ULL, 0x100000000ULL);
    T("refused: overlap with a reserved area", ios_jumbo_walk_refused((const char *)0x71c0000000ULL, (const char *)0x7240000000ULL) == 1);
    T("not refused: ends exactly where the area starts", ios_jumbo_walk_refused((const char *)0x71c0000000ULL, (const char *)0x7200000000ULL) == 0);
    T("not refused: starts exactly where the area ends", ios_jumbo_walk_refused((const char *)0x7300000000ULL, (const char *)0x7340000000ULL) == 0);
    return fails != 0;
}
'''
harness = harness.replace('@SW@', sw).replace('@REFUSED@', 'static __thread int ios_jumbo_walk_active;\n' + refused).replace('@BLOCK@', block)
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
                         '-Wno-unused-but-set-variable', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production block, refusal and switch compile on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-3000:])
    if cc.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=dict(os.environ, MADEIRA_JUMBO_ALIGNED='1'))   # the cases run with the walk on
        for line in r.stdout.splitlines():
            check(line[5:], line.startswith('ok'))
        check('every compiled case passes and the run is clean', r.returncode == 0, r.stdout[-600:] + r.stderr[-600:])
        env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_')}
        base = subprocess.run([str(exe), 'switch'], capture_output=True, text=True, env=env).stdout.split()
        check('switch unset: the walk is skipped (BCD default off; status c0000017 = still failing)', base[0] != '0', str(base))
        for value, expect_runs in (('0', False), ('1', True), ('', False), ('00', False), ('off', False), ('11', False), ('on', False)):
            out = subprocess.run([str(exe), 'switch'], capture_output=True, text=True, env=dict(env, MADEIRA_JUMBO_ALIGNED=value)).stdout.split()
            check('MADEIRA_JUMBO_ALIGNED=%r: the walk %s' % (value, 'runs' if expect_runs else 'is skipped (status c0000017 = still failing)'),
                  (out[0] == '0') == expect_runs, str(out))

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-jumbo-aligned.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
