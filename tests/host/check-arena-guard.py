#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_ARENA_GUARD: a fixed or hinted guest reservation never lands in the FEX arena (virtual_ios.c).

Every Windows process of a session shares FEX's arena (ml799: a FEX_ONLY reserved area). A request with no base address is
kept out of it (fex_arena_covers), but a request that names a base goes to map_fixed_area, which maps over any reserved
area: SocialClubHelper.exe's V8 reserves 4 GB at random hints and one hint inside the arena was granted there, FEX's 16 MB
claims then failed (Red Dead Redemption 2 crashed at libarm64ecfex.dll+0xfe770). With the guard map_view refuses such a
request with STATUS_CONFLICTING_ADDRESSES, which callers already handle (V8 retries without a hint), and the steering valve
skips its top slot when the arena lies in it.

It changes where a game's own reservations may go, so in BCD it is OFF unless env.MADEIRA_ARENA_GUARD = 1: the games that
run today have never been through it. The functions are cut out of virtual_ios.c and run on the host:
  1. off (unset, empty, 0, anything but 1): nothing is refused, the steering top slot is usable, nothing is logged;
  2. on: a fixed range overlapping the FEX_ONLY area is refused (a start inside, an end inside, one covering it) and logged
     (the first 32 refusals, then every 256th); a range outside it, or one that overlaps only a generic reserved area, passes;
     a request whose limits lie inside the arena (FEX's own, MEM_ADDRESS_REQUIREMENTS) passes; without an arena nothing is
     refused;
  3. on: the steering top slot [0x7c00000000, host limit) is skipped when the arena lies in it, logged once, and used
     when there is no arena, or the arena is below the slot;
  4. the source: map_view asks the guard right before map_fixed_area, the valve asks for the slot through its bound
     (ios_steer_floor's caller) and nowhere else, and the switch is read once.
Skipped when no C compiler is found.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


a = src.index("static int ios_arena_guard_enabled(void)")
b = src.index("/* madeira-bcd: [stack-spill] a 4 GB floor constrains nothing on iOS.")
guard = src[a:b]
f0 = src.index("static int fex_arena_covers( const void *limit_low, const void *limit_high )")
covers = src[f0:src.index("\n}\n", f0) + 3]

# ---- the source
check("round 630 (A2 S3): map_view retries exactly the WOW64 window with the overlap flag set, after the cage retry",
      "if (!ptr && (char *)start == (char *)0xc00000000ULL && (char *)end == (char *)0xf00000000ULL)" in src
      and "ios_fex_overlap_retry = 1;\n                ptr = map_reserved_area( start, end, host_size, top_down, unix_prot, align_mask );\n                ios_fex_overlap_retry = 0;" in src
      and src.index("if (!ptr && ios_cage_release_on_exhaustion( start, end, view_size ))") < src.index("ios_fex_overlap_retry = 1;"))
check("the switch is read once and only '1' turns the guard on",
      src.count('getenv( "MADEIRA_ARENA_GUARD" )') == 1 and "v = (e && e[0] == '1');" in guard)
check("map_view asks the guard right before map_fixed_area",
      "if (ios_arena_guard_refuses( base, size, limit_low, limit_high )) return STATUS_CONFLICTING_ADDRESSES;\n"
      "        if ((status = map_fixed_area( base, size, unix_prot )))\n" in src and src.count("ios_arena_guard_refuses(") == 2)
# (round 626, agent G2 S10: the refusal of a big fixed view names the kernel's regions ([map-block]) before it returns the status)
valve = src[src.index("ULONG_PTR steer_lo = ios_steer_floor();"):src.index("if (!sst && ios_steer_n < IOS_STEER_MAX)")]
check("the steering valve skips its top slot through the bound its retry also uses",
      "if (!ios_steer_top_slot_usable()) steer_lo = (ULONG_PTR)host_addr_space_limit;" in valve and src.count("ios_steer_top_slot_usable()") == 1
      and valve.count("steer_lo,") == 2)
check("the settings catalog lists it",
      "env.MADEIRA_ARENA_GUARD" in (R / "app/Madeira/ConfigCatalog.generated.swift").read_text())
check("it is in no recommended list and no auto-tune rule line (opt-in, nobody's default)",
      "MADEIRA_ARENA_GUARD" not in (R / "recommended/recommendations.json").read_text()
      and "MADEIRA_ARENA_GUARD" not in (R / "build/tools/autotune_rules.py").read_text())

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stddef.h>
typedef uintptr_t ULONG_PTR;
typedef unsigned int NTSTATUS;
struct list { struct list *next, *prev; };
#define LIST_ENTRY(elem, type, field) ((type *)((char *)(elem) - offsetof(type, field)))
#define LIST_FOR_EACH_ENTRY(elem, list, type, field) \
    for ((elem) = LIST_ENTRY((list)->next, type, field); &(elem)->field != (list); (elem) = LIST_ENTRY((elem)->field.next, type, field))
struct reserved_area { struct list entry; void *base; size_t size; unsigned int fex_only; };
static struct list reserved_areas;
static size_t host_page_mask = 0x3fff;
#define ROUND_SIZE(addr, size, mask) (((size) + ((ULONG_PTR)(addr) & (mask)) + (mask)) & ~(size_t)(mask))
static int lines;
#define dprintf(fd, ...) (lines++)
static const ULONG_PTR ios_steer_slot = 0x7C00000000;
static void *host_addr_space_limit = (void *)0x8000000000ull;
static ULONG_PTR ios_fex_arena_base_unix, ios_fex_arena_end_unix;
struct teb_stub { struct { void *UniqueThread; } ClientId; };
static struct teb_stub the_teb __attribute__((unused));
#define NtCurrentTeb() (&the_teb)
static int ios_fex_overlap_retry;   /* round 630 (A2 S3): virtual_ios.c declares it right above fex_arena_covers */
''' + covers + guard + r'''
static int fails;
static void expect( const char *what, int got, int want, int want_lines )
{
    if (got != want || lines != want_lines)
    {
        printf( "FAIL %s: got %d (want %d), %d log lines (want %d)\n", what, got, want, lines, want_lines );
        fails++;
    }
}
static struct reserved_area generic, fex;
static void areas( ULONG_PTR arena_base, ULONG_PTR arena_end )
{
    reserved_areas.next = reserved_areas.prev = &reserved_areas;
    generic.base = (void *)0x7000000000ull; generic.size = 0x400000000ull; generic.fex_only = 0;
    fex.base = (void *)arena_base; fex.size = arena_end - arena_base; fex.fex_only = 1;
    /* the list is kept in address order, as the real one is */
    generic.entry.next = &fex.entry; generic.entry.prev = &reserved_areas;
    fex.entry.next = &reserved_areas; fex.entry.prev = &generic.entry;
    reserved_areas.next = &generic.entry; reserved_areas.prev = &fex.entry;
    ios_fex_arena_base_unix = arena_base; ios_fex_arena_end_unix = arena_end;
}
int main( int argc, char **argv )
{
    const char *mode = argc > 1 ? argv[1] : "";
    int i;
    areas( 0x7c40000000ull, 0x8000000000ull );
    if (!strcmp( mode, "off" ))
    {
        expect( "off: a range inside the arena", ios_arena_guard_refuses( (void *)0x7d00000000ull, 0x1000000000ull, 0, 0 ), 0, 0 );
        expect( "off: the steering slot", ios_steer_top_slot_usable(), 1, 0 );
    }
    else if (!strcmp( mode, "on" ))
    {
        expect( "a start inside the arena", ios_arena_guard_refuses( (void *)0x7d00000000ull, 0x10000, 0, 0 ), 1, 1 );
        expect( "an end inside the arena", ios_arena_guard_refuses( (void *)0x7b00000000ull, 0x1000000000ull, 0, 0 ), 1, 2 );
        expect( "a range covering the arena", ios_arena_guard_refuses( (void *)0x7000000000ull, 0x1000000000ull, 0, 0 ), 1, 3 );
        expect( "a range below the arena", ios_arena_guard_refuses( (void *)0x7000000000ull, 0x100000000ull, 0, 0 ), 0, 3 );
        expect( "a range just below the arena", ios_arena_guard_refuses( (void *)0x7c30000000ull, 0x10000000, 0, 0 ), 0, 3 );
        expect( "a range in the generic area only", ios_arena_guard_refuses( (void *)0x7100000000ull, 0x10000, 0, 0 ), 0, 3 );
        expect( "FEX's own limits (inside the arena)",
                ios_arena_guard_refuses( (void *)0x7d00000000ull, 0x10000, 0x7c40000000ull, 0x7fffffffffull ), 0, 3 );
        expect( "limits that are not inside the arena",
                ios_arena_guard_refuses( (void *)0x7d00000000ull, 0x10000, 0x7000000000ull, 0x7fffffffffull ), 1, 4 );
        for (i = 0; i < 100; i++) ios_arena_guard_refuses( (void *)0x7d00000000ull, 0x10000, 0, 0 );
        expect( "refusals are logged 32 times, then every 256th", lines, 4 + 28, lines );
        ios_fex_arena_base_unix = ios_fex_arena_end_unix = 0;
        reserved_areas.next = reserved_areas.prev = &reserved_areas;
        expect( "no arena at all", ios_arena_guard_refuses( (void *)0x7d00000000ull, 0x10000, 0, 0 ), 0, lines );
    }
    else if (!strcmp( mode, "steer" ))
    {
        int before = lines;
        expect( "the arena lies in the top slot", ios_steer_top_slot_usable(), 0, before + 1 );
        before = lines;
        expect( "once", ios_steer_top_slot_usable(), 0, before );
        ios_fex_arena_base_unix = 0; ios_fex_arena_end_unix = 0;
        expect( "no arena yet", ios_steer_top_slot_usable(), 1, before );
        ios_fex_arena_base_unix = 0x7000000000ull; ios_fex_arena_end_unix = 0x7400000000ull;
        expect( "an arena below the slot", ios_steer_top_slot_usable(), 1, before );
    }
    else if (!strcmp( mode, "overlap" ))
    {
        /* round 630 (agent A2 S3): the WOW64 emulator's window on a 63 GB map, [0xc00000000, 0xf00000000), overlaps the arena */
        areas( 0xb30000000ull, 0xd30000000ull );
        expect( "outside the retry: not served", fex_arena_covers( (void *)0xc00000000ull, (void *)0xeffffffffull ), 0, lines );
        ios_fex_overlap_retry = 1;
        expect( "in the retry: the window (inclusive end) is served", fex_arena_covers( (void *)0xc00000000ull, (void *)0xeffffffffull ), 1, lines );
        expect( "in the retry: the window (exclusive end) is served", fex_arena_covers( (void *)0xc00000000ull, (void *)0xf00000000ull ), 1, lines );
        expect( "in the retry: another window is not", fex_arena_covers( (void *)0xc00000000ull, (void *)0xe00000000ull ), 0, lines );
        expect( "in the retry: a generic request is not", fex_arena_covers( (void *)0, (void *)0x7fffffffffull ), 0, lines );
        areas( 0x7c40000000ull, 0x8000000000ull );
        expect( "in the retry: an arena that does not overlap the window", fex_arena_covers( (void *)0xc00000000ull, (void *)0xeffffffffull ), 0, lines );
        ios_fex_overlap_retry = 0;
    }
    else if (!strcmp( mode, "overlap-off" ))
    {
        areas( 0xb30000000ull, 0xd30000000ull );
        ios_fex_overlap_retry = 1;
        expect( "MADEIRA_WOW_FEX_BAND=0: not served", fex_arena_covers( (void *)0xc00000000ull, (void *)0xeffffffffull ), 0, lines );
    }
    if (!fails) printf( "harness ok\n" );
    return fails != 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cc, "-std=gnu99", "-Wall", "-Werror", "-Wno-unused-function", "-o", exe, c], capture_output=True, text=True)
        check("the functions build on the host", b.returncode == 0, b.stderr[-2500:])
        if b.returncode == 0:
            def run(mode, value):
                env = dict(os.environ)
                env.pop("MADEIRA_ARENA_GUARD", None)
                env.pop("MADEIRA_WOW_FEX_BAND", None)
                if value is not None:
                    env["MADEIRA_ARENA_GUARD"] = value
                return subprocess.run([exe, mode], capture_output=True, text=True, env=env)
            for value in (None, "", "0", "off", "2", "true"):
                r = run("off", value)
                check("MADEIRA_ARENA_GUARD=%r: off, nothing refused, nothing logged" % value, r.returncode == 0, r.stdout)
            r = run("on", "1")
            check("MADEIRA_ARENA_GUARD=1: the overlaps are refused, the rest passes, and the log is capped", r.returncode == 0, r.stdout)
            r = run("steer", "1")
            check("MADEIRA_ARENA_GUARD=1: the steering top slot is skipped while it holds the arena", r.returncode == 0, r.stdout)
            r = run("overlap", None)
            check("round 630 (A2 S3): the WOW64 window on a 63 GB map uses the arena it overlaps, in map_view's retry only", r.returncode == 0, r.stdout)
            env = dict(os.environ, MADEIRA_WOW_FEX_BAND="0")
            r = subprocess.run([exe, "overlap-off"], capture_output=True, text=True, env=env)
            check("round 630: MADEIRA_WOW_FEX_BAND=0 turns the overlap off with the [wow-band] case", r.returncode == 0, r.stdout)

print("PASS: arena guard" if ok else "FAIL: arena guard")
sys.exit(0 if ok else 1)
