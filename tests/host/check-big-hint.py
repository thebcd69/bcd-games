#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Last of Us Part I's sixteen 512 GB reservations: the log-only code dump and the exact-hint experiment (round 634).

tlou-i.exe (Send Logs 24TC8Y and W5M7YN, builds 579 and 580; S0M36P and 8GRXB9, build 630; four starts of build 633) asks for a
512 GB MEM_RESERVE at 0x1000000000 + n * 0x8000000000, n = 0..15 ("[jumbo#1]".."[jumbo#16]"), takes none of the answers and stops itself
("HALT: Coudln't find any available virtual memory start addresses after 16 tries", Memory::FindAvailableVirtualMemoryStartAddress, an int3,
exit 0x80000003). Two keys, both default off:
  - env.MADEIRA_JUMBO_CODE_DUMP = 1: [jumbo-code] lines, log only (the code around the caller, the saved registers and stack);
  - env.MADEIRA_BIG_HINT_EXACT = 1: a reserve of 256 GB or more at a named address that ends above the address space is answered with that
    address, backed by nothing ([big-hint] lines); a commit inside is refused, a query says reserved, a release forgets it.

Checks (Patch B, the experiment, is optional: with build/ntdll-unix/ios_big_hint.h absent, only the log-only dump is checked):
  - build/ntdll-unix/ios_big_hint.h compiled on the host: the sixteen requests of the logs are all wanted, every request that fits or is small
    or unnamed is not, the table keeps the sixteen ranges apart, finds, and forgets only an exact base;
  - virtual_ios.c: where each hook sits and that nothing runs with the keys off;
  - the keys are in the settings catalog and the list policy;
  - virtual_ios.c still compiles for iOS (syntax only, pinned Wine headers) when the Wine tree is there.
The auto-tune rule and its fixtures are held by tests/host/check-autotune.py.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


header = root / "build/ntdll-unix/ios_big_hint.h"
src = root / "build/ntdll-unix/virtual_ios.c"
has_b = header.exists()
print("note: the exact-hint experiment (ios_big_hint.h) is %s" % ("in this tree" if has_b else "not in this tree: only the log-only dump is checked"))
text = src.read_text(errors="replace")

# ---------------------------------------------------------------- the header, compiled on the host
harness = r'''
#include <stdio.h>
#include <stdint.h>
#include "ios_big_hint.h"
static int fails;
#define EXPECT(c, what) do { if (!(c)) { printf("FAIL %s\n", what); fails++; } } while (0)
int main(void)
{
    const uint64_t limit = 0x8000000000ULL;   /* user space of an iOS 27 map: [jumbo#1] user_space_limit 0x8000000000 */
    const uint64_t real = 0x7000000000ULL;    /* ios_big_hint_real_lo: the JIT pool's RW alias starts there; BCD's memory is above it */
    const uint64_t g512 = 0x8000000000ULL;
    const uintptr_t me = 0x10a91c000, other = 0x108dd8000;
    struct ios_big_hint t[IOS_BIG_HINT_MAX];
    unsigned n = 0, i;
    uint64_t sz = 0;

    /* the sixteen requests of the logs: hint 0x1000000000 + i * 0x8000000000, 512 GB, every one ends above the map */
    for (i = 0; i < 16; i++)
    {
        uint64_t hint = 0x1000000000ULL + i * 0x8000000000ULL;
        char what[80];
        snprintf(what, sizeof what, "request %u (hint 0x%llx) is wanted", i + 1, (unsigned long long)hint);
        EXPECT(ios_big_hint_wanted(hint, g512, limit, real), what);
    }
    /* the first request's range is [0x1000000000, 0x9000000000): it crosses the map's end */
    EXPECT(0x1000000000ULL + g512 > limit, "the first range crosses the end of the map");
    /* not wanted: no hint, below 64 GB, off the 64 KB granularity, a size under 256 GB, a size off the page, a range that wraps */
    EXPECT(!ios_big_hint_wanted(0, g512, limit, real), "no hint: the placement code picks");
    EXPECT(!ios_big_hint_wanted(0x140000000ULL, g512, limit, real), "a hint below 64 GB lies in what BCD maps there");
    EXPECT(!ios_big_hint_wanted(0xfffff0000ULL, g512, limit, real), "...just under 64 GB too");
    EXPECT(!ios_big_hint_wanted(0x1000001000ULL, g512, limit, real), "a hint off the 64 KB granularity is not one a program names");
    EXPECT(!ios_big_hint_wanted(0x1000000000ULL, 0x2000000000ULL, limit, real), "128 GB is not a block this experiment answers");
    EXPECT(!ios_big_hint_wanted(0x1000000000ULL, 0x3ffffff000ULL, limit, real), "just under 256 GB is not either");
    EXPECT(!ios_big_hint_wanted(0x1000000000ULL, g512 + 0x800, limit, real), "a size off the page");
    EXPECT(!ios_big_hint_wanted(0xffffffff00000000ULL, g512, limit, real), "a range that wraps");
    /* a request that fits the map is left to the placement code */
    EXPECT(!ios_big_hint_wanted(0x1000000000ULL, 0x4000000000ULL, limit, real), "256 GB at 64 GB ends at 320 GB: it fits, not wanted");
    EXPECT(ios_big_hint_wanted(0x1000000000ULL, 0x4000000000ULL, 0x4000000000ULL, real), "...but a map of 256 GB cannot hold it");
    /* a hint in the part of the map BCD works in is real memory, even when the range runs past the map */
    EXPECT(!ios_big_hint_wanted(0x7800000000ULL, 0x4000000000ULL, limit, real), "256 GB at 0x7800000000: the base is real memory");
    EXPECT(!ios_big_hint_wanted(real, g512, limit, real), "...from the floor of the real part up");
    EXPECT(ios_big_hint_wanted(real - 0x10000, g512, limit, real), "...and just under it the base is virtual");
    /* the 63 GB maps of iOS 26 and older: the same sixteen */
    EXPECT(ios_big_hint_wanted(0x1000000000ULL, g512, 0xfc0000000ULL, 0xfc0000000ULL), "a 63 GB map: wanted");

    /* the virtual part of a range: below the real part, or beyond the map */
    EXPECT(ios_big_hint_virtual(0x1000000000ULL, limit, real), "64 GB is virtual");
    EXPECT(ios_big_hint_virtual(0x6ffffff000ULL, limit, real), "just under the pool's alias is virtual");
    EXPECT(!ios_big_hint_virtual(0x7047ff0000ULL, limit, real), "the pool's alias itself is real (it ends at the usable floor)");
    EXPECT(!ios_big_hint_virtual(real, limit, real), "the real part starts at the floor");
    EXPECT(!ios_big_hint_virtual(0x7c00000000ULL, limit, real), "the FEX arena is real");
    EXPECT(!ios_big_hint_virtual(0x7ffffff000ULL, limit, real), "the last page of the map is real");
    EXPECT(ios_big_hint_virtual(limit, limit, real), "beyond the map is virtual");

    /* the table */
    for (i = 0; i < 8; i++)
        EXPECT(ios_big_hint_add(t, &n, 0x1000000000ULL + i * g512, g512, 4, me) == 0, "eight adjacent ranges fit");
    EXPECT(n == 8, "eight ranges");
    EXPECT(ios_big_hint_add(t, &n, 0x1000000000ULL + 8 * g512, g512, 4, me) == -1, "the ninth: the table is full");
    EXPECT(ios_big_hint_find(t, n, 0x1000000000ULL, me, limit, real) == 0, "the first base is in range 0");
    EXPECT(ios_big_hint_find(t, n, 0x6ffffff000ULL, me, limit, real) == 0, "...and so is the last virtual page below the real part");
    EXPECT(ios_big_hint_find(t, n, 0x7000000000ULL, me, limit, real) == -1, "the JIT pool's alias inside range 0 is not found");
    EXPECT(ios_big_hint_find(t, n, 0x7048000000ULL, me, limit, real) == -1, "BCD's own memory inside range 0 is not found");
    EXPECT(ios_big_hint_find(t, n, 0x7c00000000ULL, me, limit, real) == -1, "...the FEX arena neither");
    EXPECT(ios_big_hint_find(t, n, 0x8000000000ULL, me, limit, real) == 0, "beyond the map, range 0 goes on");
    EXPECT(ios_big_hint_find(t, n, 0x9000000000ULL - 1, me, limit, real) == 0, "the last byte of range 0");
    EXPECT(ios_big_hint_find(t, n, 0x9000000000ULL, me, limit, real) == 1, "the first byte of range 1");
    EXPECT(ios_big_hint_find(t, n, 0x9000000000ULL, other, limit, real) == -1, "another process does not see it");
    EXPECT(ios_big_hint_find(t, n, 0xfffffffffULL, me, limit, real) == -1, "just below the first range");
    EXPECT(ios_big_hint_find(t, n, 0x1000000000ULL + 8 * g512, me, limit, real) == -1, "just above the last range");
    EXPECT(!ios_big_hint_forget(t, &n, 0x9000000000ULL, other, &sz), "another process cannot release it");
    EXPECT(!ios_big_hint_forget(t, &n, 0x9000010000ULL, me, &sz), "an address inside a range is not its base");
    EXPECT(n == 8, "...and nothing was forgotten");
    EXPECT(ios_big_hint_forget(t, &n, 0x9000000000ULL, me, &sz) == 1 && sz == g512 && n == 7, "the base of range 1 is forgotten with its size");
    EXPECT(ios_big_hint_find(t, n, 0x9000000000ULL, me, limit, real) == -1, "...and no longer found");
    EXPECT(ios_big_hint_find(t, n, 0x11000000000ULL, me, limit, real) >= 0, "...while the next range is");
    EXPECT(ios_big_hint_add(t, &n, 0x9000000000ULL, g512, 4, me) == 0, "the freed place can be asked for again");
    n = 0;
    EXPECT(ios_big_hint_add(t, &n, 0x1000000000ULL, g512, 4, me) == 0, "one range");
    EXPECT(ios_big_hint_add(t, &n, 0x5000000000ULL, g512, 4, other) == -1, "an overlapping range is refused, whoever asks");
    EXPECT(ios_big_hint_add(t, &n, 0xfffffffffff00000ULL, 0x200000, 4, me) == -1, "a range that wraps is refused");
    EXPECT(ios_big_hint_add(t, &n, 0x1000000000ULL, 0, 4, me) == -1, "an empty range is refused");
    /* build 635: the reserve loop of tlou-i-l.exe from [jumbo-code] of the owner's build-634 log (return address 0x1233b3d89; the
     * global at 0x126649c40, as objdump resolves `add rbx, [rip + 0x3295ec6]` at 0x1233b3d73) */
    {
        static const unsigned char loop[IOS_BIG_HINT_TLOU_LEN] = {
            0x48, 0xba, 0x00, 0x00, 0x00, 0x00, 0x80, 0x00, 0x00, 0x00, 0x48, 0xc1, 0xe3, 0x27, 0x41, 0xb9, 0x04, 0x00, 0x00, 0x00,
            0x48, 0x03, 0x1d, 0xc6, 0x5e, 0x29, 0x03, 0x41, 0xb8, 0x00, 0x20, 0x00, 0x00, 0x48, 0x8b, 0xcb, 0xff, 0x15, 0xef, 0x6d,
            0x56, 0x02, 0x48, 0x3b, 0xc3, 0x75 };
        unsigned char bad[IOS_BIG_HINT_TLOU_LEN];
        uint64_t g = 0;
        unsigned k;
        EXPECT(ios_big_hint_tlou_loop(loop, 0x1233b3d89ULL, &g) && g == 0x126649c40ULL, "the loop of the log is recognised and its global found");
        for (k = 0; k < IOS_BIG_HINT_TLOU_LEN; k++)
        {
            char what[80];
            if (k >= 23 && k <= 26) continue;   /* the global's displacement */
            if (k >= 38 && k <= 41) continue;   /* the call's displacement */
            for (i = 0; i < IOS_BIG_HINT_TLOU_LEN; i++) bad[i] = loop[i];
            bad[k] ^= 0x01;
            snprintf(what, sizeof what, "a loop with byte %u changed is not taken", k);
            EXPECT(!ios_big_hint_tlou_loop(bad, 0x1233b3d89ULL, &g), what);
        }
        EXPECT(ios_big_hint_tlou_seed(0x7400000000ULL) + 0x8000000000ULL == 0x7400000000ULL,
               "the seed makes try 1 (n = 1) ask for the moved base, wrapping below zero");
        EXPECT(ios_big_hint_tlou_seed(0x7400000000ULL) + 0 * 0x8000000000ULL != 0x1000000000ULL, "...and try 0 can never ask for 64 GB again");
    }
    /* build 644: the game's 1 GB slots (tlou-i-l.exe 0x1403b58b8), from the owner's build-643 log: base 0x7800000000, slot 16 asked for at
     * 0x7c00000000 (the emulator's arena), the 5 GB CPU area at slot 0, the process heap's growing commits at slot 11 */
    {
        uint64_t seen[IOS_BIG_HINT_TLOU_SLOTS / 64] = { 0 };
        const uint64_t b = 0x7800000000ULL;
        EXPECT(ios_big_hint_tlou_slot(b, b, 30) == 0, "the base is slot 0");
        EXPECT(ios_big_hint_tlou_slot(b, 0x7980000000ULL, 30) == 6, "0x7980000000 (the 1 GB CPU area after the 5 GB one) is slot 6");
        EXPECT(ios_big_hint_tlou_slot(b, 0x7ac0000000ULL, 30) == 11, "0x7ac0000000 is slot 11");
        EXPECT(ios_big_hint_tlou_slot(b, 0x7bc0000000ULL, 30) == 15, "0x7bc0000000 (the PSO pre-loading heap) is slot 15");
        EXPECT(ios_big_hint_tlou_slot(b, 0x7c00000000ULL, 30) == 16, "0x7c00000000, where build 643 hung, is slot 16");
        EXPECT(ios_big_hint_tlou_slot(b, 0x7bffffffffULL, 30) == 15, "the last byte before it is still slot 15");
        EXPECT(ios_big_hint_tlou_slot(b, b + g512 - 1, 30) == 511, "the last byte of the area is slot 511");
        EXPECT(ios_big_hint_tlou_slot(b, b + g512, 30) == -1, "the first byte after the area is outside it");
        EXPECT(ios_big_hint_tlou_slot(b, b - 1, 30) == -1, "the byte before the base is outside it");
        EXPECT(ios_big_hint_slot_first(seen, 11) == 1, "slot 11's first commit is new");
        EXPECT(ios_big_hint_slot_first(seen, 11) == 0, "...its second (the heap growing) is not");
        EXPECT(ios_big_hint_slot_first(seen, 63) == 1 && ios_big_hint_slot_first(seen, 64) == 1, "slots 63 and 64 sit in different words");
        EXPECT(ios_big_hint_slot_first(seen, 511) == 1 && ios_big_hint_slot_first(seen, 511) == 0, "the last slot");
        EXPECT(ios_big_hint_slot_first(seen, 512) == -1 && ios_big_hint_slot_first(seen, -1) == -1, "slots outside the area are refused");
        EXPECT(ios_big_hint_tlou_slot(0x7400000000ULL, 0x7c00000000ULL, 30) == 32, "a 32 GB block at 0x7400000000: the arena starts at slot 32");
        /* build 646, mode 3: 64 MB slots; the area the game sees is 32 GB of slots, its 512 GB reservation unchanged */
        EXPECT(IOS_BIG_HINT_TLOU_SMALL_SHIFT == 26, "mode 3's slots are 64 MB");
        EXPECT(ios_big_hint_tlou_slot(b, 0x7c00000000ULL, 26) == 256, "64 MB slots: 0x7c00000000 is slot 256");
        EXPECT(ios_big_hint_tlou_slot(b, b + 0x4000000ULL * 511 + 0x3ffffff, 26) == 511, "...the last byte of slot 511");
        EXPECT(ios_big_hint_tlou_slot(b, b + 0x4000000ULL * 512, 26) == -1, "...and past 32 GB there is no slot");
        EXPECT(ios_big_hint_tlou_slot(0x7400000000ULL, 0x7bffffffffULL, IOS_BIG_HINT_TLOU_SMALL_SHIFT) == 511,
               "a 32 GB block at 0x7400000000 holds all 512 slots of 64 MB: the last byte before the arena is slot 511");
        EXPECT(ios_big_hint_tlou_slot(0x7400000000ULL, 0x7c00000000ULL, IOS_BIG_HINT_TLOU_SMALL_SHIFT) == -1,
               "...and the arena's first byte is in no slot (the game's own 512-slot limit comes first)");
        EXPECT((((0x142400000ULL - 1) >> 26) + 1) == 81 && (((0x142400000ULL - 1) >> 28) + 1) == 21,
               "the 5 GB CPU area of the owner's starts takes 81 slots of 64 MB (21 of 256 MB): a run across words");
    }
    /* build 646: the six sites, each changing exactly one byte from the 1 GB value to the 64 MB one */
    {
        unsigned k;
        for (k = 0; k < 6; k++)
        {
            const struct ios_big_hint_site *t = &ios_big_hint_tlou_sites[k];
            uint8_t bad[7];
            unsigned j;
            EXPECT(t->at < t->len && t->len <= 7, "a site's changed byte lies inside its instruction");
            EXPECT(ios_big_hint_site_matches(k, t->old), "a site matches its own bytes");
            for (j = 0; j < t->len; j++) bad[j] = t->old[j];
            bad[t->at] = t->new_byte;
            EXPECT(!ios_big_hint_site_matches(k, bad), "a site already patched does not match again (never patched twice)");
            EXPECT(t->old[t->at] == (k == 2 ? 0x3f : 0x1e) && t->new_byte == (k == 2 ? 0x03 : 0x1a),
                   "shifts go 30 -> 26, the alignment mask 0x3fffffff -> 0x03ffffff");
        }
        EXPECT(!ios_big_hint_site_matches(6, ios_big_hint_tlou_sites[0].old), "there is no seventh site");
    }
    if (!fails) printf("header-ok\n");
    return fails != 0;
}
'''
cc = shutil.which("cc") or shutil.which("clang")
if has_b and cc:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "t.c"
        c.write_text(harness)
        exe = Path(tmp) / "t"
        b = subprocess.run([cc, "-Wall", "-Werror", "-I", str(header.parent), "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the header compiles on the host (-Wall -Werror)", b.returncode == 0, b.stderr)
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            check("the sixteen requests are wanted, what fits or is unnamed is not, the table keeps ranges apart",
                  r.returncode == 0 and "header-ok" in r.stdout, r.stdout)
elif has_b:
    print("skip the host compile: no C compiler")

# ---------------------------------------------------------------- [jumbo-code]: log only, keyed
fn = text[text.find("static int ios_jumbo_code_dump_enabled(void)"):]
fn = fn[:fn.find("\n}\n") + 3]
check("MADEIRA_JUMBO_CODE_DUMP is read once and is off unless it is 1",
      'getenv( "MADEIRA_JUMBO_CODE_DUMP" )' in fn and "cached = (s && *s == '1') ? 1 : 0;" in fn, fn)
dump = text[text.find("static void ios_jumbo_code_dump( unsigned seq, size_t size )"):]
dump = dump[:dump.find("\nstatic void ios_jumbo_census")]
window = text[text.find("static void ios_jumbo_code_window( unsigned seq"):]
window = window[:window.find("\nstatic void ios_jumbo_code_dump")]
check("the dump returns at once with the key off, for a reservation under 64 GB and after the second call of a session",
      "if (!ios_jumbo_code_dump_enabled() || size < 0x1000000000ULL || !teb || calls >= 2) return;" in dump, dump[:700])
check("it prints the registers, 32 stack words and two code places: the saved RIP and the word on top of the stack",
      'ios_jumbo_code_window( seq, "rip", rip, done, &ndone );' in dump and 'ios_jumbo_code_window( seq, "ret", ret, done, &ndone );' in dump
      and "ios_safe_read64( rsp, &ret )" in dump and "a < rsp + 32 * 8" in dump, dump[-700:])
check("a code place within 0x180 bytes of one already printed, or a fifth, is skipped",
      "center - done[i] < 0x180 || done[i] - center < 0x180" in window and "if (*ndone >= 4) return;" in window)
check("the code window is 0x280 before and 0x180 after the place", "lo = (center - 0x280) & ~0xfULL;" in window and "hi = (center + 0x180) & ~0xfULL;" in window)
check("every read goes through the fault-safe readers", "ios_safe_read( (uintptr_t)a, b, 16 )" in window and "ios_safe_read64( a + 8 * j, &w[j] )" in dump)
check("the dump writes nothing but log lines",
      not re.search(r"\b(mprotect|mmap|munmap|write|fwrite)\s*\(", dump + window) and "memset" not in dump + window, (dump + window)[:300])
census = text[text.find("static void ios_jumbo_census( void *hint"):]
census = census[:census.find("/* ml130 IS THE GUEST'S 12GB ACTUALLY USED?")]
check("ios_jumbo_census calls the dump after the [jumbo#] line, only where it logs",
      census.find('dprintf(2, "[jumbo#%u]') < census.find("if (log_this) ios_jumbo_code_dump( seq, size );") < census.find("[jumbo-tls]"), census[:100])
vi = text[text.find("void virtual_init(void)"):]
vi = vi[:vi.find("ios_log_running_image_identity();")]
check("virtual_init reads the key(s) once, so no reservation calls getenv for them",
      "(void)ios_jumbo_code_dump_enabled();" in vi and (not has_b or "(void)ios_big_hint_enabled();" in vi))

# ---------------------------------------------------------------- [big-hint]: where the hooks sit, nothing with the key off
if has_b:
    en = text[text.find("static int ios_big_hint_enabled(void)"):]
    en = en[:en.find("\n}\n") + 3]
    check("MADEIRA_BIG_HINT_EXACT is read once and is off unless it is exactly 1, 2 or 3 (the mode)",
          'getenv( "MADEIRA_BIG_HINT_EXACT" )' in en and "cached = (s && (*s == '1' || *s == '2' || *s == '3') && !s[1]) ? *s - '0' : 0;" in en, en)
    check("ios_big_hint.h is included by virtual_ios.c", '#include "ios_big_hint.h"' in text)
    grant = text[text.find("static int ios_big_hint_grant( void *hint"):]
    grant = grant[:grant.find("\n}\n") + 3]
    check("the grant answers nothing with the key off (before anything else) or for a request this map could hold",
          grant.find("if (!(mode = ios_big_hint_enabled())) return 0;") < grant.find("real = IOS_BIG_HINT_REAL;")
          < grant.find("if (!ios_big_hint_wanted( h, size, IOS_BIG_HINT_LIMIT, real )) return 0;") and grant.count("ios_big_hint_enabled()") == 1,
          grant[:600])
    check("...nor over memory Wine holds inside the map below the real part",
          "if (h < real && ios_big_hint_first_view( h, real ) < real)" in grant, grant[:900])
    real_lo = text[text.find("static uint64_t ios_big_hint_real_lo(void)"):]
    real_lo = real_lo[:real_lo.find("\n}\n") + 3]
    check("the real part starts at the JIT pool's alias (0x7000000000, lower with a lower RW base), never past the map, not at the usable floor",
          "uint64_t lo = 0x7000000000ULL" in real_lo and "if (rw >= IOS_BIG_HINT_MIN_HINT && rw < lo) lo = rw;" in real_lo
          and "return lo < lim ? lo : lim;" in real_lo and "ios_usable_va_floor" not in real_lo.split("{", 1)[1]
          and "#define IOS_BIG_HINT_REAL  (ios_big_hint_real_lo())" in text, real_lo)
    fv = text[text.find("static uint64_t ios_big_hint_first_view( uint64_t addr, uint64_t end )"):]
    fv = fv[:fv.find("\n}\n") + 3]
    check("the view lookup stops at the end of the map and holds virtual_mutex around the view tree",
          "if (addr >= hi) return end;" in fv and "server_enter_uninterrupted_section( &virtual_mutex, &sigset );" in fv
          and "ios_lowest_view_in(" in fv and "server_leave_uninterrupted_section( &virtual_mutex, &sigset );" in fv, fv)
    cm = text[text.find("static int ios_big_hint_commit("):]
    cm = cm[:cm.find("\n}\n") + 3]
    qu = text[text.find("static int ios_big_hint_query("):]
    qu = qu[:qu.find("\n}\n") + 3]
    check("a commit or query where Wine holds a view goes the old way, and the view is looked up outside the table's lock",
          "if (i < 0 || ios_big_hint_first_view( a, a + 1 ) == a) return 0;" in cm
          and cm.find("pthread_mutex_unlock( &ios_big_hint_lock );") < cm.find("ios_big_hint_first_view(")
          and "if (re == a) return 0;" in qu and qu.find("pthread_mutex_unlock( &ios_big_hint_lock );") < qu.find("ios_big_hint_first_view("),
          cm + qu)
    check("a query answer below the real part ends where the real part (or Wine's first view) starts",
          "if (a < real && re > real) re = real;" in qu and "re = ios_big_hint_first_view( a, re );" in qu
          and "info->RegionSize        = (SIZE_T)(re - a);" in qu, qu)
    check("the grant probes one 16 KB page at the hint with the same call as [va-profile], and gives it back",
          "mach_vm_allocate( mach_task_self(), &pa, 0x4000, VM_FLAGS_FIXED )" in grant and "mach_vm_deallocate( mach_task_self(), pa, 0x4000 )" in grant)
    hook = text.find("int bh = ios_big_hint_grant( *ret, *size_ptr, protect );")
    first = text.rfind("st = allocate_virtual_memory( ret, size_ptr, type, protect, 0, limit, 0, 0 );", 0, hook)
    plan_c = text.find("/* task#29 CEF plan C: a HINTED jumbo reserve that fails placement")
    check("the grant sits right after the first placement attempt (at the named address) and before the old jumbo answers",
          0 < first < hook < plan_c and hook - first < 5000 and plan_c - hook < 1000, (first, hook, plan_c))
    check("...and only turns a failure into success, or refuses it outright (no soft grant after a refusal)",
          "if (st && *ret && (type & MEM_RESERVE) && !(type & MEM_COMMIT))\n            {\n                int bh = ios_big_hint_grant( *ret, *size_ptr, protect );\n"
          "                if (bh > 0) st = STATUS_SUCCESS;\n                else if (bh < 0) big_hint_refused = 1;" in text
          and "if (st && *ret && *size_ptr >= 0x40000000 && (type & MEM_RESERVE) && !big_hint_refused)" in text)
    # build 635, mode 2: The Last of Us Part I's area base moved
    mv = text[text.find("static int ios_big_hint_tlou_move( uint64_t h, SIZE_T size, int mode )"):]
    mv = mv[:mv.find("\n}\n") + 3]
    check("mode 2 moves only try 0 of the recognised loop (hint 64 GB, 512 GB, rdi 0, the global holding the hint), once per process",
          "if (size != 0x8000000000ULL || h != 0x1000000000ULL) return 0;" in mv
          and "if (ios_big_hint_reloc_base && ios_big_hint_reloc_owner == IOS_BIG_HINT_OWNER) return 0;" in mv
          and "if (!ios_big_hint_guest( &ret, &rdi ) || rdi != 0) return 0;" in mv
          and "if (!ios_big_hint_tlou_loop( code, ret, &global )) return 0;" in mv
          and "if (ios_safe_read64( global, &cur ) || cur != h) return 0;" in mv, mv[:900])
    check("...reserves a real 4 GB-aligned block in BCD's part of the map first (build 644: 32 GB down to 6), top down below the emulator's arena",
          "{ 32ULL << 30, 28ULL << 30, 24ULL << 30, 20ULL << 30, 16ULL << 30, 12ULL << 30, 8ULL << 30, 6ULL << 30 }" in mv
          and "ULONG_PTR hi = ios_fex_arena_base_unix ? ios_fex_arena_base_unix : (ULONG_PTR)host_addr_space_limit;" in mv
          and "allocate_virtual_memory( &b, &len, MEM_RESERVE | MEM_TOP_DOWN, PAGE_READWRITE, (ULONG_PTR)IOS_BIG_HINT_REAL,\n"
              "                                               hi, 0x100000000ULL, 0 );" in mv, mv)
    check("...writes the seed with mach_vm_write and gives the block back when that fails",
          "seed = ios_big_hint_tlou_seed( (uint64_t)(uintptr_t)b );" in mv and "mach_vm_write( mach_task_self(), (mach_vm_address_t)global" in mv
          and "NtFreeVirtualMemory( NtCurrentProcess(), &fb, &z, MEM_RELEASE );" in mv, mv)
    check("the grant refuses try 0 (-1) and answers try 1 at the moved base, the real part recorded (modes 2 and 3)",
          "if (mode >= 2 && ios_big_hint_tlou_move( h, size, mode )) return -1;" in grant
          and "ios_big_hints[ios_big_hint_n - 1].real = ios_big_hint_reloc_len;" in grant
          and grant.find("ios_big_hint_tlou_move(") < grant.find("if (!ios_big_hint_wanted("), grant[:1500])
    check("a commit of the game past the moved block's real part, inside the map, lands only in its own views",
          "if (!ios_big_hint_foreign( a, size, owner )) return 0;" in cm and "foreign = !view || (uintptr_t)view->ios_owner != owner;" in text, cm)
    fo = text[text.find("static int ios_big_hint_foreign( uint64_t addr, uint64_t size, uintptr_t owner )"):]
    fo = fo[:fo.find("\n}\n") + 3]
    eo = text[text.find("static int ios_big_hint_emulator_own( uint64_t addr, uint64_t size )"):]
    eo = eo[:eo.find("\n}\n") + 3]
    check("build 645: a commit in the emulator's arena is the emulator's own only when one view holds all of it (find_view with the size)",
          "if (!ios_fex_arena_base_unix || addr < ios_fex_arena_base_unix || addr >= ios_fex_arena_end_unix) return 0;" in eo
          and "own = find_view( (const void *)(uintptr_t)addr, size ? size : 1 ) != NULL;" in eo
          and "server_enter_uninterrupted_section( &virtual_mutex, &sigset );" in eo, eo)
    check("build 644/645: in the emulator's arena a commit is foreign unless it is the emulator's own, decided before the view tree",
          "return !ios_big_hint_emulator_own( addr, size );" in fo
          and fo.find("ios_big_hint_emulator_own(") < fo.find("server_enter_uninterrupted_section(")
          and "if (!ios_big_hint_foreign( a, size, owner )) return 0;" in cm, fo)
    check("build 645: the emulator's own commits get no slot (no log line, no end), checked outside the table's lock",
          "if (slot >= 0 && ios_big_hint_emulator_own( a, size )) slot = -1;" in cm
          and cm.find("pthread_mutex_unlock( &ios_big_hint_lock );") < cm.find("ios_big_hint_emulator_own( a, size )")
          < cm.find("first = ios_big_hint_slot_first(") < cm.find("[big-hint] r644 slot"), cm[:2500])
    check("build 644: the first commit in each 1 GB slot of the moved area is logged once (capped at 64 lines, its own counter)",
          "first = ios_big_hint_slot_first( ios_big_hint_slot_seen, slot );" in cm
          and "__atomic_fetch_add( &ios_big_hint_slot_said, 1, __ATOMIC_RELAXED ) < 64" in cm
          and "[big-hint] r644 slot %d (%p) first commit" in cm and "ios_big_hint_reloc_owner == owner" in cm, cm[:1500])
    oor = cm[cm.find("if (!ios_big_hint_foreign( a, size, owner )) return 0;"):]
    check("build 644: a commit of the moved area that would land in foreign memory ends the game with STATUS_NO_MEMORY, after the log line",
          oor.find("[big-hint] r635 COMMIT") < oor.find("[big-hint] r644 OUT OF ROOM") < oor.find("NtTerminateProcess( NtCurrentProcess(), STATUS_NO_MEMORY );")
          < oor.find("return 1;") and "if (slot >= 0 && rb == ios_big_hint_reloc_base && !(type & MEM_RESERVE))" in oor, oor[:1800])
    check("...and nothing else in the file ends a process for the big hint", text.count("[big-hint] r644 OUT OF ROOM") == 1)
    ss = text[text.find("static int ios_big_hint_tlou_small_slots( uint64_t ret )"):]
    ss = ss[:ss.find("\n}\n") + 3]
    pk = text[text.find("static int ios_big_hint_poke( uint64_t addr, uint8_t v )"):]
    pk = pk[:pk.find("\n}\n") + 3]
    check("build 646, mode 3: the slots shrink only in mode 3, once the move succeeded, and the shift follows only a full patch",
          "if (mode == 3 && ios_big_hint_tlou_small_slots( ret )) ios_big_hint_slot_shift = IOS_BIG_HINT_TLOU_SMALL_SHIFT;" in mv
          and mv.find("ios_big_hint_reloc_owner = IOS_BIG_HINT_OWNER;") < mv.find("ios_big_hint_tlou_small_slots( ret )")
          and "static unsigned ios_big_hint_slot_shift = IOS_BIG_HINT_TLOU_SLOT_SHIFT;" in text, mv)
    check("...only in the exe the sites were read from: the loop's return at its rva inside the main image, every site's bytes checked first",
          "uint64_t image = ret - IOS_BIG_HINT_TLOU_LOOP_RET;" in ss and "(uint64_t)(uintptr_t)peb->ImageBaseAddress != image" in ss
          and ss.find("ios_big_hint_site_matches( i, got )") < ss.find("ios_big_hint_poke(")
          and "ios_safe_read( (uintptr_t)(image + t[i].rva), got, t[i].len )" in ss, ss)
    check("...all six or none: a byte that does not take puts back the ones before it",
          "while (done--) ios_big_hint_poke( image + t[done].rva + t[done].at, t[done].old[t[done].at] );" in ss
          and "if (done < 6)" in ss and ss.count("return 0;") == 3 and ss.rstrip().endswith("return 1;\n}"), ss)
    check("...a byte is written with mach_vm_write, through Wine's NtProtectVirtualMemory only when the page is read-only, its "
          "protection given back, and read back",
          pk.find("mach_vm_write(") < pk.find("NtProtectVirtualMemory( NtCurrentProcess(), &base, &len, PAGE_EXECUTE_READWRITE, &old )")
          < pk.find("NtProtectVirtualMemory( NtCurrentProcess(), &base, &len, old, &tmp );")
          and "return ios_safe_read( (uintptr_t)addr, &back, 1 ) && back == v;" in pk, pk)
    check("...the slot log and the out-of-room line follow the slot size",
          "slot = ios_big_hint_tlou_slot( ios_big_hint_reloc_base, a, ios_big_hint_slot_shift );" in cm
          and "(ios_big_hint_reloc_len >> ios_big_hint_slot_shift) - 1, (1u << ios_big_hint_slot_shift) >> 20" in cm
          and "(1u << ios_big_hint_slot_shift) >> 20 );" in cm[cm.find("r644 OUT OF ROOM"):], cm[-2500:])
    # the six sites against the exe itself, when the owner's copy is on this Mac (never in the repository)
    exe = Path.home() / "madeira-local/intake/analysis/tlou1-2026-11-07/exe/tlou-i-l.exe"
    if exe.exists():
        import struct
        d = exe.read_bytes()
        e = struct.unpack_from("<I", d, 0x3c)[0]
        nsec = struct.unpack_from("<H", d, e + 6)[0]
        osz = struct.unpack_from("<H", d, e + 20)[0]
        secs = []
        o = e + 24 + osz
        for _ in range(nsec):
            vs, va, rs, ro = struct.unpack_from("<IIII", d, o + 8)
            secs.append((va, rs, ro))
            o += 40
        def at(rva, n):
            for va, rs, ro in secs:
                if va <= rva < va + rs:
                    return d[ro + rva - va: ro + rva - va + n]
            return b""
        hdr = header.read_text()
        sites = re.findall(r"\{ 0x([0-9a-f]+), (\d), \{ ([^}]*) \}, (\d), 0x([0-9a-f]+) \}", hdr)
        ok_sites = len(sites) == 6 and all(at(int(r, 16), int(n)) == bytes(int(x, 16) for x in b.split(", ")) for r, n, b, _, _ in sites)
        check("the six sites hold those bytes in the owner's tlou-i-l.exe 1.1.5", ok_sites, sites)
        loop = at(0x3b3d89 - 0x2a, 0x2e)
        check("...and its reserve loop returns at IOS_BIG_HINT_TLOU_LOOP_RET (the loop the move recognises ends there)",
              "#define IOS_BIG_HINT_TLOU_LOOP_RET    0x3b3d89u" in hdr and loop[:2] == b"\x48\xba" and loop[-4:-1] == b"\x48\x3b\xc3", loop.hex())
    else:
        print("skip the exe check: the owner's tlou-i-l.exe is not on this Mac")
    rl = text[text.find("static int ios_big_hint_release("):]
    rl = rl[:rl.find("\n}\n") + 3]
    check("releasing the moved block forgets it and lets Wine release its real view (2)",
          "if (r && moved) return 2;" in rl and "if (ios_big_hint_release( base, &bsz ) == 1)" in text, rl)
    commit = text.find("if ((type & MEM_COMMIT) && ios_big_hint_n && *ret && ios_big_hint_commit( *ret, *size_ptr, type, protect ))")
    soft = text.find("if ((type & MEM_COMMIT) && ios_soft_n && *ret)")
    check("a commit inside a granted range is answered before the soft pool looks at it, with STATUS_NO_MEMORY",
          0 < commit < soft and "return STATUS_NO_MEMORY;" in text[commit:commit + 200], (commit, soft))
    nt_free = text[text.find("NTSTATUS WINAPI NtFreeVirtualMemory( HANDLE process"):]
    nt_free = nt_free[:nt_free.find("\nNTSTATUS WINAPI ", 100)]
    rel = nt_free.find("if ((type & MEM_RELEASE) && !size && base == (char *)addr && ios_big_hint_n)")
    ml433 = nt_free.find("/* ml433 (#72): keep the jumbo ledger honest")
    check("a release of a granted range's exact base is handled before the soft pool and the view code", 0 < rel < ml433, (rel, ml433))
    bm = text[text.find("static unsigned int get_basic_memory_info( HANDLE process"):]
    bm = bm[:bm.find("\nstatic unsigned int get_memory_region_info")]
    q = bm.find("if (ios_big_hint_n && ios_big_hint_query( addr, info ))")
    fill = bm.find("if ((status = fill_basic_memory_info( addr, info ))) return status;")
    check("a query inside a granted range is answered before fill_basic_memory_info (which refuses an address above the map)", 0 < q < fill, (q, fill))
    for name in ("ios_big_hint_commit", "ios_big_hint_release", "ios_big_hint_query"):
        body = text[text.find("static int %s(" % name):]
        body = body[:body.find("\n}\n") + 3]
        check("%s does nothing while the table is empty (the key off)" % name, "if (!ios_big_hint_n) return 0;" in body, body[:300])
    check("every hook line says [big-hint] r634 or r635 and the per-access lines are capped", text.count("[big-hint] r634") >= 5
          and text.count("[big-hint] r635") >= 5
          and text.count("__atomic_fetch_add( &ios_big_hint_said, 1, __ATOMIC_RELAXED ) < 64") == 4 and "ios_big_hint_said++" not in text)
    check("the table is guarded by its own mutex", "static pthread_mutex_t ios_big_hint_lock = PTHREAD_MUTEX_INITIALIZER;" in text)


# ---------------------------------------------------------------- the keys are known to the settings and to downloaded lists
cat = (root / "build/tools/gen-config-catalog.py").read_text()
gen = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
for key in ("MADEIRA_JUMBO_CODE_DUMP",) + (("MADEIRA_BIG_HINT_EXACT",) if has_b else ()):
    check("settings catalog entry env.%s (generator and generated file)" % key,
          '"env.%s"' % key in cat and 'ConfigOption(key: "env.%s"' % key in gen)
    values = "0, 1, 2 or 3" if key == "MADEIRA_BIG_HINT_EXACT" else "0 or 1"   # build 635: mode 2; build 646: mode 3
    check("a downloaded list may set %s (allowedEnvNames), %s" % (key, values),
          ('"%s"' % key) in pol.split("static let allowedEnvNames")[1].split("]\n")[0] and ("%s is %s" % (key, values)) in pol)
if not has_b:
    check("...and the experiment's key is not there", "MADEIRA_BIG_HINT_EXACT" not in cat + gen + pol + text)

# ---------------------------------------------------------------- the real file still compiles for iOS (when the wine headers exist)
wine = Path(os.environ.get("WINE_SRC", root / "wine"))
config = wine / "build-macos/include/config.h"
if shutil.which("xcrun") and config.exists():
    try:
        sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sdk = ""
    if sdk:
        build = root / "build/ntdll-unix"
        flags = ["-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-O2", "-fPIC",
                 "-fno-strict-aliasing", "-Wno-implicit-function-declaration", "-Wno-int-conversion",
                 "-include", str(config), "-include", str(build / "shims/wine_ios_exit.h"), f"-I{build / 'shims'}",
                 f"-I{wine / 'build-macos/dlls/ntdll'}", f"-I{wine / 'dlls/ntdll'}", f"-I{wine / 'dlls/ntdll/unix'}",
                 f"-I{wine / 'build-macos/include'}", f"-I{wine / 'include'}", "-D__WINESRC__", "-DLTC_NO_PROTOTYPES",
                 "-DLTC_SOURCE", "-D_NTSYSTEM_", "-D_ACRTIMP=", "-DWINBASEAPI=", '-DBINDIR="/usr/local/bin"',
                 '-DLIBDIR="/usr/local/lib"', '-DDATADIR="/usr/local/share"', '-DSYSTEMDLLPATH=""', "-DWINE_UNIX_LIB",
                 "-DWINE_IOS=1", "-Wall"]
        r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", *flags, str(src)], capture_output=True, text=True)
        check("virtual_ios.c compiles for iOS (syntax only, pinned wine headers)", r.returncode == 0, r.stderr[-2000:])
        check("...and the new code adds no warning",
              not re.search(r"big_hint|big-hint|jumbo_code|jumbo-code", r.stderr), r.stderr[-2000:])
    else:
        print("skip the iOS syntax check: no iphoneos SDK")
else:
    print("skip the iOS syntax check: no wine headers (set WINE_SRC)")

print("PASS: big hint" if ok else "FAILED: big hint")
sys.exit(0 if ok else 1)
