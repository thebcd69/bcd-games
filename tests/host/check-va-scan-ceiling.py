#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Portions copyright 2026 Vugar: the host harness is that of tests/host/check-va-scan-floor.py, taken from https://github.com/willfaust/Madeira
# (pull request #622, commit 3adceb56) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""env.MADEIRA_VA_SCAN_CEILING: the free-area scan does not try what lies at or above the address map's ceiling.

host_addr_space_limit is a power-of-two walk that is only ever raised ("[va-limit] ml749 walk=0x8000000000 kernel_max=0x7180000000 ->
keeping walk"), so on a map that does not end on a power of two the scan windows reach above the map: 454 GB maps (iOS 27: installs without
the increased memory limit, 301 sessions, and 3-6 GB devices with it, 24) and 17 GB maps (iOS 26 and 18: 52 and 7). A fixed mapping that ends above
the ceiling is refused, Mach calls the range free, so nothing was jumped over: a top-down placement crawled from the top of its window down
to the ceiling, a refused mach_vm_map and a mach_vm_region query per 64 KB, under virtual_mutex ("[va-scan] SLOW window=0x7038000000..
0x73ffff0000 size=0x200000 ... top-down tries=163839 skips=0 ... errno=12 ... no-region-above", KZWJYG, build 659, iPhone12,8: about
0.45 s each; "tries=239616" on a 17 GB map, QZ39XV, build 653), and a bottom-up scan that found nothing crawled the rest of its window
before it failed (253,952 tries for every try of a steer slot that lies wholly above a 454 GB map).

Checks:
  - try_map_free_area, ios_skip_occupied, ios_va_map_floor, ios_va_map_ceiling and ios_va_no_region_in, cut out of virtual_ios.c and run
    on the host against fake address maps (a floor, a ceiling, regions between, on some maps the kernel's own region above the ceiling):
    - the measured cases cost what the logs say with the switch off and a few dozen attempts with it on;
    - bottom-up and top-down scans, windows below, across and above the ceiling, granules of 16 KB to 1 MB, sizes of 4 KB to 512 MB:
      the address returned, the tries and region jumps counted, the first refusal, the [alloc-fail] record and errno are the same with the
      switch on, with it off, and when the kernel does not report a ceiling;
    - on, no mapping is attempted at or above the ceiling on a map with no region there, and every attempt saved is counted as
      above-ceiling;
    - a scan that meets a region above the ceiling takes the old path (the 63 GB map: one attempt, one jump, nothing counted);
    - a ceiling the kernel raised is seen (the scan asks again before it skips), with the same placements as the switch off;
    - a fixed-address attempt (step 0) above the ceiling is still made;
    - a bottom-up scan that reaches the ceiling ends there (it never turns back into its window);
    - the ceiling is asked for once when no scan reaches it, and never with the switch off;
  - in the source: the switch is read once, the jump sits before the mapping attempt, and the [va-scan] line reports above-ceiling;
  - the settings catalog lists the switch (when the catalog exists).
The host run is skipped when no C compiler is found.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
src = (R / "build/ntdll-unix/virtual_ios.c").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


def function(signature):
    start = src.index(signature)
    return src[start:src.index("\n}\n", start) + 3]


floor_fn = function("static uintptr_t ios_va_map_floor(void)")
ceiling_fn = function("static uintptr_t ios_va_map_ceiling( int refresh )")
region_fn = function("static int ios_va_no_region_in( uintptr_t lo, uintptr_t hi )")
skip_fn = function("static int ios_skip_occupied( void *addr, size_t size, size_t align_mask,")
scan_fn = function("static void* try_map_free_area( void *base, void *end, ptrdiff_t step,")

check("the switch is read once", src.count('getenv( "MADEIRA_VA_SCAN_CEILING" )') == 1 and
      'getenv( "MADEIRA_VA_SCAN_CEILING" )' in ceiling_fn)
check("the ceiling is the kernel's TASK_VM_INFO.max_address", "hi = (uintptr_t)vmi.max_address;" in ceiling_fn)
check("the jump comes before the mapping attempt, after the floor's, and leaves a fixed-address attempt alone",
      "if (step && !ceiling_declined)\n        {\n            uintptr_t a = (uintptr_t)start, ceiling = ios_va_map_ceiling( 0 );" in scan_fn and
      scan_fn.index("ios_va_map_floor()") < scan_fn.index("ios_va_map_ceiling( 0 )") <
      scan_fn.index("anon_mmap_tryfixed( start, size, unix_prot, 0 )"))
check("only an address wholly above the ceiling is skipped, after the kernel was asked again",
      "if (ceiling && a >= ceiling && (ceiling = ios_va_map_ceiling( 1 )) && a >= ceiling)" in scan_fn)
check("a skipped stretch is one Mach has no region in", "if (ios_va_no_region_in( step < 0 ? a - (n - 1) * g : a, step < 0 ? a : a + (n - 1) * g ))" in scan_fn)
check("the skipped refusals leave the [alloc-fail] record of a refused mach_vm_map",
      "ios_af_set( 6, KERN_INVALID_ADDRESS, start, size );" in scan_fn)
check("the [va-scan] line reports above-ceiling",
      '" above-ceiling=%u"' in src and "ios_va_scan_above_ceiling - above0," in src and
      'tries=%u skips=%u below-floor=%u"' in src)

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <errno.h>
#define WINE_IOS 1
typedef uintptr_t UINT_PTR;
#define ROUND_ADDR(addr,mask) ((void *)((UINT_PTR)(addr) & ~(UINT_PTR)(mask)))
#define MAP_FAILED ((void *)-1)
#define TRACE(...) ((void)0)
#define ERR(...) ((void)0)
static int lines;
#define dprintf(fd, ...) (lines++)
typedef int kern_return_t; typedef unsigned int mach_msg_type_number_t; typedef int *task_info_t;
typedef uint64_t mach_vm_address_t, mach_vm_size_t; typedef unsigned int mach_port_t; typedef int *vm_region_info_t;
typedef struct { int protection; } vm_region_basic_info_data_64_t;
typedef struct { uint64_t min_address, max_address; } task_vm_info_data_t;
#define KERN_SUCCESS 0
#define KERN_INVALID_ADDRESS 1
#define KERN_NO_SPACE 3
#define TASK_VM_INFO 22
#define TASK_VM_INFO_COUNT 1
#define VM_REGION_BASIC_INFO_64 9
#define VM_REGION_BASIC_INFO_COUNT_64 1
#define MACH_PORT_NULL 0
#define mach_task_self() 0

/* the fake address map: nothing below kern_floor, nothing that ends above kern_max, regions between, and (some maps) a region of the
 * kernel's own above kern_max, which mach_vm_region reports and nothing can map over */
#define NREG 160
static uint64_t kern_floor, kern_max, reg_lo[NREG], reg_hi[NREG];
static unsigned nreg, info_asks, region_asks, attempts, attempts_above;
static int max_known = 1;
static kern_return_t task_info( int task, int flavor, task_info_t out, mach_msg_type_number_t *cnt )
{
    task_vm_info_data_t *vmi = (task_vm_info_data_t *)out;
    (void)task; (void)flavor; (void)cnt;
    info_asks++;
    if (!max_known) return 5;
    vmi->min_address = kern_floor;
    vmi->max_address = kern_max;
    return KERN_SUCCESS;
}
/* the table is kept in address order (add_region): the first region whose end lies above `a`, or nreg */
static unsigned first_region_ending_above( uint64_t a )
{
    unsigned lo = 0, hi = nreg;
    while (lo < hi) { unsigned mid = (lo + hi) / 2; if (reg_hi[mid] > a) hi = mid; else lo = mid + 1; }
    return lo;
}
static kern_return_t mach_vm_region( int task, mach_vm_address_t *addr, mach_vm_size_t *size, int flavor,
                                     vm_region_info_t info, mach_msg_type_number_t *cnt, mach_port_t *obj )
{
    unsigned i;
    (void)task; (void)flavor; (void)info; (void)cnt; (void)obj;
    region_asks++;
    i = first_region_ending_above( *addr );      /* the region that holds *addr or the first one above it */
    if (i == nreg) return 1;
    *addr = reg_lo[i]; *size = reg_hi[i] - reg_lo[i];
    return KERN_SUCCESS;
}
struct ios_af_rec { int stage; int err; unsigned long long addr, size; };
static struct ios_af_rec ios_af;
static void ios_af_set( int stage, int err, const void *addr, size_t size )
{
    if (ios_af.stage) return;
    ios_af.stage = stage; ios_af.err = err;
    ios_af.addr = (unsigned long long)(uintptr_t)addr; ios_af.size = (unsigned long long)size;
}
static void *anon_mmap_tryfixed( void *start, size_t size, int prot, int flags )
{
    uint64_t a = (uintptr_t)start;
    unsigned i;
    (void)prot; (void)flags;
    attempts++;
    if (a >= kern_max) attempts_above++;
    /* the kernel checks the bounds first (vm_map_enter: KERN_INVALID_ADDRESS), then the overlap (KERN_NO_SPACE) */
    if (a < kern_floor || a + size > kern_max) { ios_af_set( 6, KERN_INVALID_ADDRESS, start, size ); errno = ENOMEM; return MAP_FAILED; }
    i = first_region_ending_above( a );
    if (i < nreg && a + size > reg_lo[i]) { ios_af_set( 6, KERN_NO_SPACE, start, size ); errno = EEXIST; return MAP_FAILED; }
    return start;
}
void *ios_jit_rx_base_global, *ios_jit_rw_base_global;
size_t ios_jit_pool_size_global, ios_jit_low_size_global;
uintptr_t ios_jit_low_rx_global, ios_jit_low_rw_global;
unsigned long long ios_layerkit_lo, ios_layerkit_hi;
static unsigned long ios_layerkit_skips;
static int ios_in_layerkit( unsigned long long a, size_t size ) { (void)a; (void)size; return 0; }
static unsigned int ios_va_scan_tries, ios_va_scan_skips, ios_va_scan_below_floor, ios_va_scan_above_ceiling;
static void *ios_scan_fail_addr;
static int ios_scan_fail_errno;
''' + skip_fn + floor_fn + ceiling_fn + region_fn + scan_fn + r'''
static unsigned long long rng = 11;
static uint64_t rnd( uint64_t n ) { rng = rng * 6364136223846793005ull + 1442695040888963407ull; return (rng >> 24) % n; }

static void scan( uint64_t base, uint64_t end, uint64_t g, uint64_t size, int dir )
{
    uint64_t start = dir > 0 ? (base + g - 1) & ~(g - 1) : dir < 0 ? (end - size) & ~(g - 1) : base;
    void *r;
    int e;
    if (end < size || !start || start < base || start + size > end) return;
    ios_va_scan_tries = ios_va_scan_skips = ios_va_scan_below_floor = ios_va_scan_above_ceiling = 0;
    ios_scan_fail_addr = NULL; ios_scan_fail_errno = 0;
    memset( &ios_af, 0, sizeof(ios_af) );
    attempts = attempts_above = region_asks = 0;
    errno = 0;
    r = try_map_free_area( (void *)(uintptr_t)base, (void *)(uintptr_t)end, dir > 0 ? (ptrdiff_t)g : dir < 0 ? -(ptrdiff_t)g : 0,
                           (void *)(uintptr_t)start, size, 0 );
    e = r ? 0 : errno;     /* errno says something only after a failure */
    /* what every mode must agree on, then what differs */
    printf( "%d %llx %llx %llx %llx -> %p tries=%u skips=%u fail=%p/%d af=%d/%d/%llx/%llx errno=%d | attempts=%u above=%u counted=%u regions=%u\n",
            dir, (unsigned long long)base, (unsigned long long)end, (unsigned long long)g, (unsigned long long)size,
            r, ios_va_scan_tries, ios_va_scan_skips, ios_scan_fail_addr, ios_scan_fail_errno,
            ios_af.stage, ios_af.err, ios_af.addr, ios_af.size, e,
            attempts, attempts_above, ios_va_scan_above_ceiling, region_asks );
}

static void add_region( uint64_t lo, uint64_t hi )
{
    unsigned i = nreg++;
    for (; i && reg_lo[i - 1] > lo; i--) { reg_lo[i] = reg_lo[i - 1]; reg_hi[i] = reg_hi[i - 1]; }   /* kept in address order */
    reg_lo[i] = lo; reg_hi[i] = hi;
}

int main( int argc, char **argv )
{
    static const uint64_t grans[] = { 0x4000, 0x10000, 0x100000 };
    static const uint64_t sizes[] = { 0x1000, 0x4000, 0x10000, 0x11000, 0x60000, 0x110000, 0x200000, 0x800000, 0x2000000, 0x1ffff000 };
    const char *mode = argc > 4 ? argv[4] : "";
    uint64_t at, top;
    unsigned i;

    kern_floor = strtoull( argv[1], NULL, 16 );
    kern_max = strtoull( argv[2], NULL, 16 );
    top = strtoull( argv[3], NULL, 16 );                                     /* where the engine believes the map ends (the walk) */
    if (!strcmp( mode, "unknown" )) max_known = 0;
    add_region( kern_floor, kern_floor + 0x1000000 );                        /* the app image */
    for (at = reg_hi[0]; nreg < 96; )
    {
        uint64_t lo = at + (rnd( 6 ) ? 0 : (1 + rnd( 64 )) * 0x4000);       /* mostly packed, some gaps */
        add_region( lo, lo + (1 + rnd( 512 )) * 0x4000 );
        at = lo + (reg_hi[nreg - 1] - reg_lo[nreg - 1]);
    }
    /* the kernel's own region above the ceiling (17 GB and 63 GB maps: 0xfc0000000..0x1000000000) */
    if (kern_max <= 0xfc0000000ull) add_region( 0xfc0000000ull, 0x1000000000ull );

    /* the measured cases first, on a map whose top is still free (see the docstring) */
    scan( 0x7038000000ull, 0x73ffff0000ull, 0x10000, 0x200000, -1 );
    scan( 0x100000000ull, 0x73ffff0000ull, 0x10000, 0x110000, -1 );
    scan( 0x100010000ull, 0x800000000ull, 0x10000, 0x200000, -1 );
    scan( 0x7c00000000ull, 0x8000000000ull, 0x10000, 0x1ffff000, 1 );
    scan( 0x7400000000ull, 0x7800000001ull, 0x10000, 0x1ffff000, 1 );
    scan( 0x100000000ull, 0x1000000000ull, 0x10000, 0x200000, -1 );
    if (kern_max < top) scan( kern_max + 0x100000, top, 0x10000, 0x10000, 0 );   /* a fixed address above the ceiling */
    /* bottom-up, a window that starts 16 MB below the ceiling and ends 40 MB above it, 32 MB asked: every address straddles the ceiling
     * or lies above it, and above it fewer granules are left than the scan has come: the scan must end there, not turn back */
    if (kern_max + 0x2800000 <= top)
    {
        scan( kern_max - 0x1000000, kern_max + 0x2800000, 0x10000, 0x2000000, 1 );
        scan( kern_max - 0x1000000, kern_max + 0x2800000, 0x4000, 0x2000000, 1 );
        scan( kern_max - 0x1000000, kern_max + 0x2800000, 0x100000, 0x2000000, 1 );
    }

    /* then regions just below the ceiling (the thread blocks live there), with gaps between them */
    {
        uint64_t lo = kern_max - 0x30000000ull;
        while (nreg < NREG - 2 && lo + 0x400000 < kern_max)
        {
            uint64_t len = (1 + rnd( 31 )) * 0x10000;
            lo += (1 + rnd( 96 )) * 0x10000;
            if (lo + 0x200000 >= kern_max) break;
            add_region( lo, lo + len );
            lo += len;
        }
        if (!strcmp( mode, "tight" )) add_region( kern_max - 0x10000, kern_max );   /* one that ends at the ceiling */
    }
    if (top > kern_max + 0x40000000ull) top = kern_max + 0x40000000ull;      /* the random windows: at most 1 GB above the ceiling */
    for (i = 0; i < 700; i++)
    {
        uint64_t g = grans[rnd( 3 )], size = sizes[rnd( 10 )], base, end;
        switch (rnd( 6 ))
        {
        case 0: base = 0x100000000ull; break;
        case 1: base = kern_floor + rnd( 0x4000 ) * 0x4000; break;
        case 2: base = kern_max - (1 + rnd( 0x8000 )) * 0x4000; break;       /* just below the ceiling */
        case 3: base = kern_max; break;
        case 4: base = kern_max + rnd( 0x4000 ) * 0x4000; break;             /* a window wholly above the ceiling */
        default: base = at + rnd( 0x4000 ) * 0x4000; break;
        }
        switch (rnd( 5 ))
        {
        case 0: end = top; break;
        case 1: end = top - 0x10000; break;
        case 2: end = kern_max + rnd( 0x4000 ) * 0x4000; break;
        case 3: end = kern_max - rnd( 0x800 ) * 0x4000; break;               /* a window that ends below the ceiling */
        default: end = base + (1 + rnd( 0x20000 )) * 0x10000; break;
        }
        if (end > top) end = top;                                            /* no window reaches above host_addr_space_limit */
        if (!strcmp( mode, "grows" ) && i == 350 && kern_max + 0x20000000ull <= top)
            kern_max += 0x20000000ull;                                       /* the kernel enlarges the map half way */
        scan( base, end, g, size, rnd( 2 ) ? 1 : -1 );
    }
    printf( "ceiling=%llx asks=%u\n", (unsigned long long)ios_va_map_ceiling( 0 ), info_asks );
    return 0;
}
'''

cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP host run: no C compiler")
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = os.path.join(tmp, "t.c")
        exe = os.path.join(tmp, "t")
        open(c, "w").write(harness)
        b = subprocess.run([cc, "-std=gnu99", "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                            "-Wno-unused-value", "-o", exe, c], capture_output=True, text=True)
        check("the scan builds on the host", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            def run(floor, kmax, top, switch, mode=None, floor_switch="0"):
                env = dict(os.environ)
                env.pop("MADEIRA_VA_SCAN_CEILING", None)
                env.pop("MADEIRA_VA_SCAN_FLOOR", None)
                env.pop("MADEIRA_MAP_RETRY_ANY", None)
                if switch is not None:
                    env["MADEIRA_VA_SCAN_CEILING"] = switch
                if floor_switch is not None:
                    env["MADEIRA_VA_SCAN_FLOOR"] = floor_switch      # "0": only the ceiling asks the kernel, so asks= is its count
                r = subprocess.run([exe, floor, kmax, top] + ([mode] if mode else []), capture_output=True, text=True, env=env)
                assert r.returncode == 0, r.stderr
                rows = r.stdout.strip().split("\n")
                scans = []
                for row in rows[:-1]:
                    same, differs = row.split(" | ")
                    scans.append((same, dict((k, int(v)) for k, v in (f.split("=") for f in differs.split()))))
                return scans, rows[-1]

            # floor, the kernel's ceiling, the walk's answer (get_host_addr_space_limit), as the archive's four kinds of device have them
            maps = (("104b38000", "7180000000", "8000000000"),     # 454 GB (KZWJYG)
                    ("10242c000", "458000000", "800000000"),       # 17 GB (QZ39XV)
                    ("104e60000", "fc0000000", "1000000000"),      # 63 GB
                    ("10437c000", "8000000000", "8000000000"))     # 512 GB
            same = counted = clean = asked = grows = both = True
            saved = total_scans = 0
            for floor, kmax, top in maps:
                on, on_end = run(floor, kmax, top, None)
                off, off_end = run(floor, kmax, top, "0")
                unknown, unknown_end = run(floor, kmax, top, None, "unknown")
                assert len(on) == len(off) == len(unknown) > 400, (len(on), len(off), len(unknown))
                total_scans += len(on)
                same &= [s for s, _ in on] == [s for s, _ in off] == [s for s, _ in unknown]
                if [s for s, _ in on] != [s for s, _ in off]:
                    for (a, _), (b, _) in zip(on, off):
                        if a != b:
                            print("     first difference on map %s:\n       on  %s\n       off %s" % (kmax, a, b))
                            break
                counted &= all(a["attempts"] + a["counted"] == b["attempts"] and b["counted"] == 0
                               for (_, a), (_, b) in zip(on, off))
                counted &= [d for _, d in unknown] == [d for _, d in off]
                saved += sum(b["attempts"] - a["attempts"] for (_, a), (_, b) in zip(on, off))
                asked &= off_end == "ceiling=0 asks=0" and unknown_end.startswith("ceiling=0 asks=")
                step0 = [d for s, d in on if s.startswith("0 ")]
                clean &= [d["attempts"] for d in step0] == [d["above"] for d in step0] == ([1] if int(kmax, 16) < int(top, 16) else [])
                if kmax in ("7180000000", "8000000000"):
                    # no region above this ceiling: nothing is attempted there, and at most one region query per scan pays for it
                    clean &= all(d["above"] == 0 for s, d in on if not s.startswith("0 "))
                    clean &= all(a["regions"] <= b["regions"] + 1 for (_, a), (_, b) in zip(on, off))
                if kmax == "8000000000":
                    asked &= on_end == "ceiling=8000000000 asks=1"
                    clean &= all(d["counted"] == 0 for _, d in on)
                if kmax == "7180000000":
                    a, b = on[0][1], off[0][1]
                    print("     KZWJYG's first placement (2 MB, top-down): %d attempts with the switch off, %d on" % (b["attempts"], a["attempts"]))
                    check("KZWJYG's placement: 163,839 refused attempts off (the log's tries=163839), under 40 on, the same address",
                          b["attempts"] == 163840 and "tries=163839 " in off[0][0] and a["attempts"] < 40 and on[0][0] == off[0][0])
                    a, b = on[4][1], off[4][1]
                    check("a steer slot wholly above the map (RZA7QN: 511 MB, bottom-up): 253,953 attempts off (the log's tries=253953), none on",
                          b["attempts"] == 253953 and "tries=253953 " in off[4][0] and a["attempts"] == 0 and on[4][0] == off[4][0])
                if kmax == "458000000":
                    a, b = on[2][1], off[2][1]
                    check("QZ39XV's placement (17 GB map): 239,616 refused attempts off (the log's tries=239616), under 40 on",
                          b["attempts"] == 239617 and "tries=239616 " in off[2][0] and a["attempts"] < 40 and on[2][0] == off[2][0])
                if kmax == "fc0000000":
                    a, b = on[5][1], off[5][1]
                    check("the 63 GB map, top-down from the walk's 64 GB: the kernel's region above the ceiling is jumped as before "
                          "(one attempt there, one jump, nothing counted)",
                          on[5][0] == off[5][0] and " skips=1 " in on[5][0] and a["counted"] == 0 and a["above"] == 1 and a["attempts"] == b["attempts"])
                # the kernel enlarges the map half way: the same placements as the switch off
                g_on, _ = run(floor, kmax, top, None, "grows")
                g_off, _ = run(floor, kmax, top, "0", "grows")
                grows &= [s for s, _ in g_on] == [s for s, _ in g_off]
                # a region that ends exactly at the ceiling
                t_on, _ = run(floor, kmax, top, None, "tight")
                t_off, _ = run(floor, kmax, top, "0", "tight")
                same &= [s for s, _ in t_on] == [s for s, _ in t_off]
                # with the floor's switch on too (the app's default): the same as with the ceiling's switch off beside it
                b_on, _ = run(floor, kmax, top, None, None, None)
                b_off, _ = run(floor, kmax, top, "0", None, None)
                both &= [s for s, _ in b_on] == [s for s, _ in b_off]
            check("on, off and with no ceiling reported: the same address, tries, jumps, first refusal, [alloc-fail] record and errno for "
                  "every scan (%d scans on four maps)" % total_scans, same)
            check("on: nothing is attempted at or above the ceiling of a map with no region there, except a fixed-address attempt (once), "
                  "for one region query a scan at most", clean)
            check("every attempt saved is counted as above-ceiling; off and unknown attempt them all (%d attempts saved)" % saved,
                  counted and saved > 10000000)
            check("the ceiling is asked for once when no scan reaches it, never with the switch off, and an unusable answer is ignored", asked)
            check("a map the kernel enlarged is seen: the same placements as the switch off", grows)
            check("with the floor's switch on as well (the app's default): the same as with only the ceiling's switch off", both)

catalog = R / "app/Madeira/ConfigCatalog.generated.swift"
if catalog.exists():
    check("the settings catalog lists env.MADEIRA_VA_SCAN_CEILING",
          'ConfigOption(key: "env.MADEIRA_VA_SCAN_CEILING"' in catalog.read_text())

print("PASS: va-scan ceiling" if ok else "FAILED: va-scan ceiling")
sys.exit(0 if ok else 1)
