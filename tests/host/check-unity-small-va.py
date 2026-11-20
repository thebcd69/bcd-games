#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_UNITY_SMALL_VA: windows for a Unity player's 511 MB pieces inside the emulator's 4 GB arena
(build/ntdll-unix/ios_unity_va.h, the glue in virtual_ios.c; round 2026-11-17, K2/N112).

A Unity player reserves 12 to 34 pieces of 0x1ffff000 bytes (MEM_RESERVE, PAGE_NOACCESS, no address; 2 x 256 MB - 4 KB: it carves one
256 MB-aligned block out of each) and commits a few MB of each. On an install whose task map leaves BCD 4 to 5 GB of free address space
the first piece fits and the second does not. With the key, a 3 to 4.5 GB arena gets a small head at every 256 MB-aligned address when it
is made, and each piece is answered with one of them (highest first); a commit past the head, inside its window, gets a view at that
address when the address space is free and STATUS_NO_MEMORY when it is not. Default off; no effect in an arena of any other size.

  1. ios_unity_va.h compiled on the host (it needs no Wine header): the key (only "1"), the head size (16..128, multiples of 16, default
     64), the arena gate (3 to 4.5 GB), the aligned slots (a 4 GB arena at 0x320000000 has 16, one at 0x7027000000 has 15, a window that
     would end past the arena is no slot), the slot a piece gets (the highest held one, 17th: none), the window an address belongs to
     (used windows only, the boundaries), the cover classification of a commit range from the views in it (none, ours whole, ours part,
     foreign whole, foreign part, more views than collected) and the decision for every cover (pass, grow, straddle, conflict, refuse at
     the growth point, ambiguous, outside the window);
  2. a small model of a window's life with a list of views: heads pass, the step after the head grows when free and is then ours, a
     foreign view right after the head makes the next contiguous step refuse, a request wholly inside a foreign view elsewhere passes as
     ambiguous, a request over the head's end straddles, a request that leaves the window is not the window's;
  3. the source: the key is read once and only "1" turns it on; every hook of virtual_ios.c is skipped while ios_usva_live_n is 0, and
     ios_usva_live_n is raised by ios_usva_claim_heads alone, which starts with the key and the arena gate and is called from
     ios_reserve_fex_arena only; the reserve hook answers exactly Unity's request (NULL base, MEM_RESERVE, PAGE_NOACCESS, 0x1ffff000
     bytes); the commit hook sees commits only (MEM_COMMIT without MEM_RESERVE); the release hook follows a successful release; the arena
     guard lets the window's own views through; nothing in a hook leaves the program a window with no head;
  4. the key is in the settings catalog and the list policy, in no recommended list, in one auto-tune rule (with the FEX call-return
     line), and that rule has a fixture whose replay proposes exactly its two lines.
Needs python3 and a C compiler (the host run is skipped without one).
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
hdr_path = R / "build/ntdll-unix/ios_unity_va.h"
hdr = hdr_path.read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("  " + str(detail)) if detail and not cond else ""))
    ok = ok and bool(cond)


cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ios_unity_va.h"

#define MB (1ULL << 20)
#define GB (1ULL << 30)

static void iv(struct ios_usva_iv *v, unsigned *n, uint64_t b, uint64_t e, int ours) { v[*n].base = b; v[*n].end = e; v[*n].ours = ours; (*n)++; }

int main(void)
{
    struct ios_usva_iv v[IOS_USVA_IV_MAX];
    struct ios_usva_slot slots[IOS_USVA_MAX];
    uint64_t bases[IOS_USVA_MAX];
    unsigned n, i;
    int k;

    /* key */
    printf("key %d %d %d %d %d %d %d\n", ios_usva_key_on(NULL), ios_usva_key_on(""), ios_usva_key_on("0"), ios_usva_key_on("2"),
           ios_usva_key_on("11"), ios_usva_key_on("on"), ios_usva_key_on("1"));
    /* head size */
    printf("head %u %u %u %u %u %u %u %u %u %u %u %u\n", ios_usva_head_mb(NULL), ios_usva_head_mb(""), ios_usva_head_mb("abc"), ios_usva_head_mb("8"),
           ios_usva_head_mb("16"), ios_usva_head_mb("47"), ios_usva_head_mb("64"), ios_usva_head_mb("100"), ios_usva_head_mb("128"),
           ios_usva_head_mb("129"), ios_usva_head_mb("99999999"), ios_usva_head_mb("-5"));
    /* arena gate */
    printf("arena %d %d %d %d %d %d %d\n", ios_usva_arena_ok(0x370000000ULL, 0x3f0000000ULL), ios_usva_arena_ok(0x320000000ULL, 0x420000000ULL),
           ios_usva_arena_ok(0x7027000000ULL, 0x7127000000ULL), ios_usva_arena_ok(0, 3 * GB), ios_usva_arena_ok(0, 4 * GB + 512 * MB),
           ios_usva_arena_ok(0x7c00000000ULL, 0x8000000000ULL), ios_usva_arena_ok(0x1000, 0x1000));
    /* slots */
    n = ios_usva_aligned_slots(0x320000000ULL, 0x420000000ULL, bases, IOS_USVA_MAX);
    printf("slots17 %u %llx %llx\n", n, (unsigned long long)bases[0], (unsigned long long)bases[n - 1]);
    n = ios_usva_aligned_slots(0x7027000000ULL, 0x7127000000ULL, bases, IOS_USVA_MAX);
    printf("slots454 %u %llx %llx\n", n, (unsigned long long)bases[0], (unsigned long long)bases[n - 1]);
    n = ios_usva_aligned_slots(0x320000000ULL, 0x4207fffffULL, bases, IOS_USVA_MAX);   /* an arena that ends 128 MB past a boundary: the last window does not fit */
    printf("slotsodd %u %llx\n", n, (unsigned long long)bases[n - 1]);
    n = ios_usva_aligned_slots(0x320000000ULL, 0x420000000ULL, bases, 4);
    printf("slotsmax %u\n", n);
    n = ios_usva_aligned_slots(0xfffffffff0000000ULL, 0xfffffffffffff000ULL, bases, IOS_USVA_MAX);   /* the top of the address space: no wrap-around */
    printf("slotstop %u\n", n);
    /* pick and find */
    memset(slots, 0, sizeof(slots));
    n = ios_usva_aligned_slots(0x320000000ULL, 0x420000000ULL, bases, IOS_USVA_MAX);
    for (i = 0; i < n; i++) { slots[i].base = bases[i]; slots[i].state = IOS_USVA_HELD; }
    printf("pick");
    for (k = 0; k < 18; k++)
    {
        int p = ios_usva_pick(slots, n);
        printf(" %d", p);
        if (p >= 0) slots[p].state = IOS_USVA_USED;
    }
    printf("\n");
    slots[3].state = IOS_USVA_GONE;
    slots[4].state = IOS_USVA_HELD;
    printf("pick2 %d\n", ios_usva_pick(slots, n));
    printf("find %d %d %d %d %d %d\n", ios_usva_find(slots, n, 0x410000000ULL), ios_usva_find(slots, n, 0x41fffffffULL), ios_usva_find(slots, n, 0x420000000ULL),
           ios_usva_find(slots, n, 0x40fffffffULL), ios_usva_find(slots, n, slots[4].base + 8), ios_usva_find(slots, n, slots[3].base + 8));
    /* cover */
    {
        uint64_t lo = 0x410010000ULL, hi = 0x410020000ULL;
        unsigned m = 0;

        printf("cover none %d\n", ios_usva_cover_of(lo, hi, v, 0, 0));
        m = 0; iv(v, &m, 0x410000000ULL, 0x414000000ULL, 1);
        printf("cover ours %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410000000ULL, 0x410015000ULL, 1);
        printf("cover oursPart %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410000000ULL, 0x410010000ULL, 1); iv(v, &m, 0x410010000ULL, 0x410030000ULL, 1);
        printf("cover oursTwoViewsWhole %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410000000ULL, 0x410018000ULL, 1); iv(v, &m, 0x410018000ULL, 0x410020000ULL, 1);
        printf("cover oursTwo %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x40f000000ULL, 0x412000000ULL, 0);
        printf("cover foreign %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410018000ULL, 0x412000000ULL, 0);
        printf("cover foreignPart %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410000000ULL, 0x410018000ULL, 1); iv(v, &m, 0x410018000ULL, 0x412000000ULL, 0);
        printf("cover mixed %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410030000ULL, 0x412000000ULL, 0); iv(v, &m, 0x400000000ULL, 0x410010000ULL, 1);   /* neighbours that only touch the range */
        printf("cover touching %d\n", ios_usva_cover_of(lo, hi, v, m, 0));
        m = 0; iv(v, &m, 0x410000000ULL, 0x414000000ULL, 1);
        printf("cover overflow %d\n", ios_usva_cover_of(lo, hi, v, m, 1));
    }
    /* decisions: x_off, len, hw, cover */
    printf("class");
    printf(" %d", ios_usva_commit_class(0, 16 * MB, 0, IOS_USVA_COVER_OURS));
    printf(" %d", ios_usva_commit_class(64 * MB, 16 * MB, 64 * MB, IOS_USVA_COVER_NONE));
    printf(" %d", ios_usva_commit_class(60 * MB, 16 * MB, 60 * MB, IOS_USVA_COVER_OURS_PART));
    printf(" %d", ios_usva_commit_class(64 * MB, 16 * MB, 64 * MB, IOS_USVA_COVER_FOREIGN_PART));
    printf(" %d", ios_usva_commit_class(64 * MB, 16 * MB, 64 * MB, IOS_USVA_COVER_FOREIGN));
    printf(" %d", ios_usva_commit_class(96 * MB, 16 * MB, 64 * MB, IOS_USVA_COVER_FOREIGN));
    printf(" %d", ios_usva_commit_class(250 * MB, 16 * MB, 64 * MB, IOS_USVA_COVER_NONE));
    printf(" %d", ios_usva_commit_class(0, 0, 0, IOS_USVA_COVER_NONE));
    printf(" %d", ios_usva_commit_class(0xfffffffffffff000ULL, 0x2000, 0, IOS_USVA_COVER_NONE));
    printf(" %d", ios_usva_commit_class(255 * MB, 1 * MB, 0, IOS_USVA_COVER_NONE));
    printf(" %d", ios_usva_commit_class(255 * MB + 4096, 1 * MB, 0, IOS_USVA_COVER_NONE));
    printf(" %d", ios_usva_commit_class(0, 4096, 0, 99));
    printf("\n");
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cc, "-std=gnu99", "-Wall", "-Wextra", "-Werror", "-I", str(hdr_path.parent), "-o", exe, c], capture_output=True, text=True)
        check("ios_unity_va.h builds on the host (-Wall -Wextra -Werror)", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = subprocess.run([exe], capture_output=True, text=True).stdout.splitlines()
            got = {l.split(" ", 1)[0] + (" " + l.split(" ")[1] if l.startswith("cover") else ""): l for l in out}
            line = lambda key: next((l for l in out if l.startswith(key)), "")
            check("the key: only exactly 1", line("key ") == "key 0 0 0 0 0 0 1", line("key "))
            check("the head: default 64, clamped to 16..128, rounded down to a multiple of 16, junk and overflow give 64",
                  line("head ") == "head 64 64 64 16 16 32 64 96 128 128 64 64", line("head "))
            check("the arena gate: 3 to 4.5 GB only (a 2 GB arena, an 8 or 16 GB arena and an empty one are left alone)",
                  line("arena ") == "arena 0 1 1 1 1 0 0", line("arena "))
            check("a 4 GB arena at 0x320000000 has 16 aligned windows, 0x320000000 to 0x410000000",
                  line("slots17 ") == "slots17 16 320000000 410000000", line("slots17 "))
            check("a 4 GB arena at 0x7027000000 (454 GB map) has 15, the first at 0x7030000000, the last at 0x7110000000",
                  line("slots454 ") == "slots454 15 7030000000 7110000000", line("slots454 "))
            check("a window that would end past the arena is no slot", line("slotsodd ") == "slotsodd 16 410000000", line("slotsodd "))
            check("the slot table is bounded and does not wrap at the top of the address space",
                  line("slotsmax ") == "slotsmax 4" and line("slotstop ") == "slotstop 0", line("slotstop "))
            check("a piece gets the highest held head; the 17th request finds none",
                  line("pick ") == "pick 15 14 13 12 11 10 9 8 7 6 5 4 3 2 1 0 -1 -1", line("pick "))
            check("a gone slot is never given out again, a held one below is", line("pick2 ") == "pick2 4", line("pick2 "))
            check("a window belongs to its address only while in use, from its base to 256 MB below the next one",
                  line("find ") == "find 15 15 -1 14 -1 -1", line("find "))
            check("cover: none / ours whole / ours part / ours whole in the second of two views / ours in two views / foreign whole / foreign part / mixed / touching only / more views than collected",
                  [line("cover none"), line("cover ours "), line("cover oursPart"), line("cover oursTwoViewsWhole"), line("cover oursTwo "), line("cover foreign "),
                   line("cover foreignPart"), line("cover mixed"), line("cover touching"), line("cover overflow")] ==
                  ["cover none 0", "cover ours 1", "cover oursPart 2", "cover oursTwoViewsWhole 1", "cover oursTwo 2", "cover foreign 3", "cover foreignPart 4",
                   "cover mixed 4", "cover touching 0", "cover overflow 4"], [l for l in out if l.startswith("cover")])
            check("decisions: pass, grow, straddle, conflict, refuse at the growth point, ambiguous elsewhere, outside, empty, wrap, the window's last page(s), a bad cover",
                  line("class ") == "class 0 1 2 3 4 5 6 6 6 0 6 6" or line("class ") == "class 0 1 2 3 4 5 6 6 6 1 6 6", line("class "))

# ---- 2. a window's life, with a list of views (the decisions of the header on a model of the engine's view list)
model = r'''
#include <stdio.h>
#include <string.h>
#include "ios_unity_va.h"
#define MB (1ULL << 20)
struct mv { uint64_t base, end; int ours; };
static struct mv views[64];
static unsigned nviews;
static struct ios_usva_slot slot;

static int cover(uint64_t lo, uint64_t hi)
{
    struct ios_usva_iv v[IOS_USVA_IV_MAX];
    unsigned i, n = 0;
    for (i = 0; i < nviews; i++)
        if (views[i].end > lo && views[i].base < hi) { v[n].base = views[i].base; v[n].end = views[i].end; v[n++].ours = views[i].ours; }
    return ios_usva_cover_of(lo, hi, v, n, 0);
}

/* the commit hook of virtual_ios.c on a list of views; returns the class, and grows when told */
static int commit(uint64_t x, uint64_t len)
{
    uint64_t off = x - slot.base;
    int cls = ios_usva_commit_class(off, len, slot.hw, cover(x, x + len));
    if (cls == IOS_USVA_GROW)
    {
        uint64_t gb = x & ~0xffffULL, ge = (x + len + 0xffff) & ~0xffffULL;
        if (cover(gb, ge) != IOS_USVA_COVER_NONE) cls = IOS_USVA_REFUSE_CONFLICT;
        else { views[nviews].base = gb; views[nviews].end = ge; views[nviews++].ours = 1; }
    }
    if (cls == IOS_USVA_PASS || cls == IOS_USVA_GROW) { if (off + len > slot.hw) slot.hw = off + len; }
    return cls;
}

int main(void)
{
    uint64_t a = 0x410000000ULL;
    slot.base = a; slot.state = IOS_USVA_USED; slot.hw = 0;
    views[nviews].base = a; views[nviews].end = a + 64 * MB; views[nviews++].ours = 1;   /* the head */
    printf("head");
    printf(" %d", commit(a, 16 * MB)); printf(" %d", commit(a + 16 * MB, 16 * MB)); printf(" %d", commit(a + 32 * MB, 32 * MB));   /* inside the head */
    printf(" hw %llu\n", (unsigned long long)(slot.hw / MB));
    printf("grow %d", commit(a + 64 * MB, 16 * MB));      /* the next step: free address space */
    printf(" %d", commit(a + 64 * MB, 16 * MB));            /* again: ours now */
    printf(" %d", commit(a + 80 * MB, 16 * MB));            /* the step after: free again */
    printf(" hw %llu views %u\n", (unsigned long long)(slot.hw / MB), nviews);
    /* a foreign view appears right after the grown part, at the growth point */
    views[nviews].base = a + 96 * MB; views[nviews].end = a + 112 * MB; views[nviews++].ours = 0;
    printf("conflict %d", commit(a + 96 * MB, 16 * MB));                  /* wholly inside it, at the growth point */
    printf(" %d", commit(a + 100 * MB, 1 * MB));                          /* wholly inside it, elsewhere: its owner's own commit */
    printf(" %d", commit(a + 104 * MB, 16 * MB));                         /* over its end: part of it */
    printf(" %d", commit(a + 90 * MB, 16 * MB));                          /* over its start: ours part and foreign part */
    printf(" hw %llu\n", (unsigned long long)(slot.hw / MB));
    /* a step over the head's end with nothing after it, in a second window with no growth yet */
    nviews = 0; slot.hw = 0;
    views[nviews].base = a; views[nviews].end = a + 64 * MB; views[nviews++].ours = 1;
    printf("straddle %d", commit(a + 56 * MB, 16 * MB));
    printf(" outside %d", commit(a + 250 * MB, 16 * MB));
    printf(" 64KB-rounding %d\n", commit(a + 64 * MB + 0x1000, 0x1000));        /* the view made for it is rounded to 64 KB and is free: grows */
    /* a grown 64 KB view of ours: a commit inside it passes, a commit over its end (into free space) straddles */
    nviews = 0; slot.hw = 0;
    views[nviews].base = a; views[nviews].end = a + 64 * MB; views[nviews++].ours = 1;
    views[nviews].base = a + 64 * MB; views[nviews].end = a + 64 * MB + 0x10000; views[nviews++].ours = 1;
    printf("round2 %d", commit(a + 64 * MB + 0x8000, 0x1000));
    printf(" %d\n", commit(a + 64 * MB + 0xf000, 0x2000));
    return 0;
}
'''
if cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "m.c")
        exe = os.path.join(tmp, "m")
        open(c, "w").write(model)
        b = subprocess.run([cc, "-std=gnu99", "-Wall", "-Wextra", "-Werror", "-I", str(hdr_path.parent), "-o", exe, c], capture_output=True, text=True)
        check("the model of a window's life builds on the host", b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = subprocess.run([exe], capture_output=True, text=True).stdout.splitlines()
            line = lambda key: next((l for l in out if l.startswith(key)), "")
            check("the head's own commits pass and the window's highest commit follows (0 = pass)", line("head") == "head 0 0 0 hw 64", line("head"))
            check("the step after the head grows when the address space is free (1), is ours afterwards (0), the next one grows again",
                  line("grow") == "grow 1 0 1 hw 96 views 3", line("grow"))
            check("a foreign view at the growth point refuses the contiguous step (4), its owner's own commit inside it passes (5), "
                  "a request over its end or its start is refused (3)", line("conflict") == "conflict 4 5 3 3 hw 96", line("conflict"))
            check("a request over the head's end straddles (2), one that leaves the 256 MB window is not the window's (6), a free small one beyond the head grows (1)",
                  line("straddle") == "straddle 2 outside 6 64KB-rounding 1", line("straddle"))
            check("a grown 64 KB view of ours: a commit inside it passes (0), one over its end straddles (2)", line("round2") == "round2 0 2", line("round2"))

# ---- 3. the source
H = hdr
check("the header carries the evidence and the limit (16 pieces) and says default off",
      "default off" in H and "ABSOLUTE LIMIT" in H and "at most 16 pieces" in H)
check("the key is read once, with the header's ios_usva_key_on, and nowhere else",
      src.count('getenv( "MADEIRA_UNITY_SMALL_VA" )') == 1 and 'ios_usva_key_on( getenv( "MADEIRA_UNITY_SMALL_VA" ) )' in src
      and src.count('getenv( "MADEIRA_UNITY_SMALL_VA_HEAD_MB" )') == 1 and "static inline int ios_usva_key_on( const char *e )" in H
      and "return e && e[0] == '1' && !e[1];" in H)
claim = src[src.index("static void ios_usva_claim_heads( ULONG_PTR lo, ULONG_PTR hi )"):]
claim = claim[:claim.index("\n}\n")]
check("ios_usva_claim_heads starts with the key and the arena gate, and nothing but it raises ios_usva_live_n above 0",
      claim.index("if (!ios_usva_enabled()) return;") < claim.index("if (!ios_usva_arena_ok( lo, hi ))") < claim.index("ios_usva_live_n = held;")
      and src.count("ios_usva_live_n = held;") == 1 and src.count("ios_usva_live_n++") == 0)
check("it is called from ios_reserve_fex_arena only, after the arena is published, with the arena's own range",
      src.count("ios_usva_claim_heads( (ULONG_PTR)base, (ULONG_PTR)base + size );") == 1
      and src.index("ios_usva_claim_heads( (ULONG_PTR)base, (ULONG_PTR)base + size );") > src.index('"[fex-arena] ml793 PUBLISHED')
      and src.index("ios_usva_claim_heads( (ULONG_PTR)base, (ULONG_PTR)base + size );") > src.index("void ios_reserve_fex_arena(void)")
      and src.count("ios_usva_claim_heads(") == 2)   # the definition and the one call
check("the reserve hook answers exactly Unity's request: no address (the caller's branch), MEM_RESERVE only, PAGE_NOACCESS, 0x1ffff000 bytes, and only with a live window",
      "if (!*ret && type == MEM_RESERVE && !limit_low && !limit_high && !align && !attributes)" in src
      and "if (ios_usva_live_n && ios_usva_reserve( ret, size_ptr, protect, &thin_status )) return thin_status;" in src
      and "if (!ios_usva_live_n || protect != PAGE_NOACCESS || ROUND_SIZE( 0, *size_ptr, page_mask ) != IOS_USVA_PIECE) return FALSE;" in src
      and "#define IOS_USVA_PIECE        0x1ffff000ULL" in H and src.count("ios_usva_reserve( ret, size_ptr, protect, &thin_status )") == 1)
check("the commit hook sees commits only (MEM_COMMIT, no MEM_RESERVE), at an explicit address, with a live window",
      "if (*ret && (type & MEM_COMMIT) && !(type & MEM_RESERVE) && ios_usva_live_n)" in src and src.count("ios_usva_commit( *ret, *size_ptr, &usva_status )") == 1)
check("the release hook follows a successful MEM_RELEASE and a live window",
      "if ((type & MEM_RELEASE) && ios_usva_live_n) ios_usva_released( base );" in src and src.count("ios_usva_released( base )") == 1)
check("the arena guard lets the window's own head and growth views through, and only while one is being made under the mutex",
      "if (ios_usva_placing) return 0;" in src and src.count("ios_usva_placing = 1;") == 1 and src.count("ios_usva_placing = 0;") == 1
      and src.index("ios_usva_placing = 1;") < src.index("allocate_virtual_memory( &b, &s, MEM_RESERVE, PAGE_NOACCESS, 0, 0, 0, 0 );") < src.index("ios_usva_placing = 0;"))
check("a piece is never answered with a window whose head is gone (the head view is looked up and tagged before the answer)",
      "if (!v || !(v->ios_flags & IOS_VIEW_USVA))" in src and src.index("if (!v || !(v->ios_flags & IOS_VIEW_USVA))") < src.index("ios_usva_slots[i].state = IOS_USVA_USED;"))
check("a refused commit answers STATUS_NO_MEMORY and the program's range is never given to a view of anybody else",
      "*status = STATUS_NO_MEMORY;" in src[src.index("static int ios_usva_commit("):src.index("static void ios_usva_released(")]
      and "else if (ios_usva_make_view( gb, ge - gb )) cls = IOS_USVA_REFUSE_CONFLICT;" in src)
check("the log-only lines exist: [unity-va] r669 on / piece / grow / REFUSED / AMBIGUOUS / state, [bigres-hw] and [fex-arena] r669 fill",
      all(t in src for t in ('"[unity-va] r669 on:', '"[unity-va] r669 piece #%u', '"[unity-va] r669 grow #%u', '"[unity-va] r669 REFUSED #%u', '"[unity-va] r669 AMBIGUOUS #%u',
                              '"[unity-va] r669 state (%s)', '"[bigres-hw] r669', '"[fex-arena] r669 fill (%s)')))
check("the periodic census lines come with the bigres report only, never from a hook of the program's allocation path",
      src.count("ios_fex_arena_fill_line( why );") == 1 and src.count("ios_usva_report( why );") == 1
      and 'if (!strcmp( why, "periodic" ))' in src)

# ---- 4. catalog, policy, lists, auto-tune
cat = (R / "app/Madeira/ConfigCatalog.generated.swift").read_text()
check("the settings catalog lists both keys, default off and 64",
      'key: "env.MADEIRA_UNITY_SMALL_VA", title: "Unity\'s address-space pieces as windows in the emulator\'s address space", kind: .bool, defaultValue: "0"' in cat
      and 'key: "env.MADEIRA_UNITY_SMALL_VA_HEAD_MB"' in cat and 'kind: .int, defaultValue: "64"' in cat)
pol = (R / "app/Madeira/GameRecommendationsList.swift").read_text()
check("the list policy knows both names and checks them (0 or 1; 16 to 128)",
      '"MADEIRA_UNITY_SMALL_VA", "MADEIRA_UNITY_SMALL_VA_HEAD_MB",' in pol and 'case "MADEIRA_UNITY_SMALL_VA":' in pol
      and "MADEIRA_UNITY_SMALL_VA_HEAD_MB is a whole number of MB from 16 to 128" in pol)
recs = (R / "recommended/recommendations.json").read_text()
rules = (R / "build/tools/autotune_rules.py").read_text()
check("no recommended list sets it (nobody's default); exactly one auto-tune rule does, with the FEX call-return line",
      "MADEIRA_UNITY_SMALL_VA" not in recs and rules.count('"env.MADEIRA_UNITY_SMALL_VA = 1"') == 1
      and '"lines": ["env.MADEIRA_UNITY_SMALL_VA = 1", "env.MADEIRA_FEX_CALLRET_RESERVE_KB = 64"],' in rules)
rule = rules[rules.index('"id": "unity-reserve-loop-4gb-arena-windows"'):]
rule = rule[:rule.index('\n    },\n')]
check("the rule needs the memory limit, a 4 GB arena, the 511 MB refusal and 200 piece requests, steps aside once the key ran, and ranks ahead of the Dock host's shutdown crash (12)",
      all(t in rule for t in ('s("[device] address-map=", "increased-memory=1")', 's("[fex-arena] ml793 PUBLISHED", "size=0x100000000")',
                               's("[mmap-place] ml1027 NO GAP FITS size=0x20010000")', 's("[bigres] tid=", "size=0x1ffff000", count=200)',
                               '"none": [s("[unity-va] r669 on:"), s("[unity-va] r669 MADEIRA_UNITY_SMALL_VA = 1")]', '"priority": 10,', '"noRetry": True', '"maxStage": 3')))
fx = R / "tests/fixtures/autotune/unity-reserve-loop-4gb-arena-dressmaker-2026-11-16"
if fx.with_suffix(".expect").is_file():
    exp = json.loads(fx.with_suffix(".expect").read_text())
    check("the fixture (Dressmaker's real log) proposes exactly the rule's two lines, first of the matched rules",
          exp["matched"][0] == "unity-reserve-loop-4gb-arena-windows" and exp["next"] == ["env.MADEIRA_UNITY_SMALL_VA = 1", "env.MADEIRA_FEX_CALLRET_RESERVE_KB = 64"]
          and exp["status"] == "trying", exp)
else:
    check("the fixture exists", False)
old = (R / "tests/fixtures/autotune/unity-reserve-loop-small-map-2026-10-29.expect")
check("the 2 GB-arena fixture (iPhone SE 2) still ends in the noFix verdict: the windows rule does not match it",
      json.loads(old.read_text())["matched"] == ["unity-reserve-loop-small-map"] and json.loads(old.read_text())["status"] == "known")

print("PASS: Unity windows" if ok else "FAIL: Unity windows")
sys.exit(0 if ok else 1)
