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
    check("MADEIRA_BIG_HINT_EXACT is read once and is off unless it is 1",
          'getenv( "MADEIRA_BIG_HINT_EXACT" )' in en and "cached = (s && *s == '1') ? 1 : 0;" in en, en)
    check("ios_big_hint.h is included by virtual_ios.c", '#include "ios_big_hint.h"' in text)
    grant = text[text.find("static int ios_big_hint_grant( void *hint"):]
    grant = grant[:grant.find("\n}\n") + 3]
    check("the grant answers nothing with the key off (before anything else) or for a request this map could hold",
          grant.find("if (!ios_big_hint_enabled()) return 0;") < grant.find("real = IOS_BIG_HINT_REAL;")
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
    hook = text.find("if (st && *ret && (type & MEM_RESERVE) && !(type & MEM_COMMIT) && ios_big_hint_grant( *ret, *size_ptr, protect ))")
    first = text.rfind("st = allocate_virtual_memory( ret, size_ptr, type, protect, 0, limit, 0, 0 );", 0, hook)
    plan_c = text.find("/* task#29 CEF plan C: a HINTED jumbo reserve that fails placement")
    check("the grant sits right after the first placement attempt (at the named address) and before the old jumbo answers",
          0 < first < hook < plan_c and hook - first < 5000 and plan_c - hook < 1000, (first, hook, plan_c))
    check("...and only turns a failure into success", "ios_big_hint_grant( *ret, *size_ptr, protect ))\n                st = STATUS_SUCCESS;" in text)
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
    check("every hook line says [big-hint] r634 and the log is capped", text.count("[big-hint] r634") >= 5
          and text.count("__atomic_fetch_add( &ios_big_hint_said, 1, __ATOMIC_RELAXED ) < 64") == 3 and "ios_big_hint_said++" not in text)
    check("the table is guarded by its own mutex", "static pthread_mutex_t ios_big_hint_lock = PTHREAD_MUTEX_INITIALIZER;" in text)


# ---------------------------------------------------------------- the keys are known to the settings and to downloaded lists
cat = (root / "build/tools/gen-config-catalog.py").read_text()
gen = (root / "app/Madeira/ConfigCatalog.generated.swift").read_text()
pol = (root / "app/Madeira/GameRecommendationsList.swift").read_text()
for key in ("MADEIRA_JUMBO_CODE_DUMP",) + (("MADEIRA_BIG_HINT_EXACT",) if has_b else ()):
    check("settings catalog entry env.%s (generator and generated file)" % key,
          '"env.%s"' % key in cat and 'ConfigOption(key: "env.%s"' % key in gen)
    check("a downloaded list may set %s (allowedEnvNames), 0 or 1" % key,
          ('"%s"' % key) in pol.split("static let allowedEnvNames")[1].split("]\n")[0] and ("%s is 0 or 1" % key) in pol)
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
