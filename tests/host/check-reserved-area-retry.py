#!/usr/bin/env python3
"""A MAP_FIXED over a guest window that the kernel refuses is retried at the next free range (small address maps only).

Shade: Wrath of Angels (39Z345, C534F0, Z5F6MG; build 576, iPhone13,4, iOS 18.6.2, 63 GB address map, 32-bit program):
a 0x11000-byte section map inside the guest window [0x400000000,0x500000000) ended in STATUS_NO_MEMORY with a 2 GB gap
free ("[laa] ml1070 SPILL", "[va-scan] FAILED window=0x400110000..0x500000000 ... errno=17"), and ucrtbase's init
failed (c0000142). The errno=17 is only map_free_area's no-clobber probe finding our own PROT_NONE reservation;
the placement itself is map_reserved_area's MAP_FIXED over that reservation, which returned NULL in silence, either
because its search found no range or because the MAP_FIXED failed; the log cannot tell which. The same logs show the
window refusing the program's image at its preferred base 0x410000000 (c0000017: the MAP_FIXED's ENOMEM or
create_view's allocation) and taking the relocated map at 0x401310000.

virtual_ios.c now retries the next free range after an ENOMEM from that MAP_FIXED (ios_reserved_retry), only when
ios_wow_small_va_slots is set (a small address map), and says why on the failing path: [reserved-area] (the refusal),
[va-scan] window room (what the free-range table holds), and STAGE= on the [map-sec] and ml985 refusals, so the next
log tells the refusal from the search from create_view. The retry is a recovery for the first reading, not a proof.

Round 579 (Shade 6NE7KH, build 578): on 578 the same refusal ended an 8 MB request (the game's Direct3D 9 buffer, then a
WoW64 stack): all 32 retries are 64 KB apart, so they cover 2 MB of range, and the [reserved-area] budget of eight lines
had been spent by the small requests at load time. Now: a request of 1 MB or more has its own budget, every refused candidate
and the kernel's regions across the refused range are logged (and what the first candidate after a refusal held before it
was mapped), and, only when the placement ended in NULL after a refusal on a small address map, the first refused
candidate is unmapped and mapped again without overwriting (ios_reserved_remap; env.MADEIRA_RESERVED_REMAP = 0 turns it off).
The retry loop itself is unchanged. The whole of map_reserved_area_inner is compiled and run against fakes: nothing but a
refused, unplaced chain reaches the new code, and a healthy placement reads no region.

Round 624 (Hotline Miami 2 4RHXR8, build 623; agent A S2): the skip placed wined3d.dll 22 times and then a 0x30000-byte
d3dcompiler_43.dll got no place, and no line said why (the small requests' eight lines were spent). Now the skip records why it
ended, the request's last attempt that ends without a placement prints one "[reserved-area] last attempt FAILED" line with a
budget of its own (64, then every 64th) and a census of the window's dead kernel regions, the small requests' [reserved-area]
lines and the window-room census have 64 lines each, and, after a skip that ended without a placement (S2.2), the ordinary
placement runs once more inside each stretch the kernel reports free of dead regions and live mappings (ios_reserved_kernel_hole;
Wine's free-range table still picks the address). Only the request's last attempt on a small address map reaches it, with an
ENOMEM refusal and env.MADEIRA_RESERVED_SKIP not 0; a healthy or merely retried placement reads no region.

Source checks (every host) hold the shape; the retry loop is then compiled and run against fakes.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/virtual_ios.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def function(signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


retry_fn = function('static void *ios_reserved_retry(')
typedefs = source[source.index('typedef void *(*ios_reserved_once_fn)'):source.index('static void *ios_reserved_retry(')]
inner_fn = function('static void *map_reserved_area_inner( void *limit_low, void *limit_high, size_t size, int top_down,\n'
                    '                                      int unix_prot, size_t align_mask )\n{\n    struct ios_reserved_ctx')
once_fn = function('static void *map_reserved_area_once( void *limit_low, void *limit_high, void *ctx_, void **refused, int *err )\n{')
room_fn = function('static void ios_log_window_room(')

check('the retry loop only calls the placement again; it maps, protects and unmaps nothing itself',
      all(m not in retry_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'anon_mmap', 'memset(', 'memcpy(')))
check('a retry happens only after the kernel said ENOMEM, only up to the given number of tries',
      'while (!ptr && refused && err == ENOMEM && n < max_tries)' in retry_fn)
check('each retry lies strictly above (bottom-up) or below (top-down) the refused address',
      'if (top_down) high = (char *)refused + size - 1;' in retry_fn
      and 'else low = (char *)refused + align_mask + 1;' in retry_fn)
check('the retry is on only on a small address map (ios_wow_small_va_slots), zero tries elsewhere',
      'ios_wow_small_va_slots && !ios_reserved_skip_now ? IOS_RESERVED_RETRY_MAX : 0' in inner_fn and '#define IOS_RESERVED_RETRY_MAX 32' in source)
check('the placement is still one MAP_FIXED over the reservation, and a failure of it is reported, not hidden',
      once_fn.count('anon_mmap_fixed(') == 1 and '*refused = ptr;' in once_fn and '*err = errno;' in once_fn)
check('a search that finds no range still returns NULL with no refusal recorded (nothing is retried)',
      once_fn.count('return NULL;') == 2 and 'refused' not in once_fn[:once_fn.index('if (top_down)')].replace('void **refused', ''))
check('the refusal is logged once-capped, with what Mach says about the address',
      '++ra_n <= 64' in inner_fn and 'ios_va_describe( first_refused' in inner_fn)
check('the free-range census reads only, and runs only on the failure path of a request that cannot relax',
      all(m not in room_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'anon_mmap')) and 'room_n++ >= 64' in room_fn
      and 'if (!ptr && !ceiling_relaxable) ios_log_window_room( start, end, host_size, align_mask );' in source)
check('map_fixed_area (a fixed address) is untouched', source.count('static NTSTATUS map_fixed_area(') == 1
      and 'ios_reserved_retry' not in function('static NTSTATUS map_fixed_area('))

remap_fn = function('static void *ios_reserved_remap(')
check('the retry loop is as it was (the refused candidates are recorded by the placement, not by the loop)',
      retry_fn.count('once(') == 2 and 'ctx' in retry_fn and 'refused_list' not in retry_fn and 'ios_va_regions' not in retry_fn)
check('the last resort runs only when the placement is still NULL after a refusal, ENOMEM, a small address map, key on',
      'if (!ptr && first_err == ENOMEM && ios_wow_small_va_slots && ios_reserved_remap_enabled())' in inner_fn
      and inner_fn.index('if (first_refused)') < inner_fn.index('ios_reserved_remap( first_refused'))
check('the last resort touches only a candidate Wine calls free: no view, wholly inside one reserved area, not the JIT pool',
      'find_view_range( addr, size )' in remap_fn and 'mmap_is_in_reserved_area( addr, size ) != 1' in remap_fn
      and 'ios_jit_pool_intersects( addr, size )' in remap_fn
      and remap_fn.index('find_view_range') < remap_fn.index('munmap(') and remap_fn.index('ios_jit_pool_intersects') < remap_fn.index('munmap('))
check('the last resort puts the PROT_NONE reservation back when the no-overwrite map fails',
      'anon_mmap_fixed( addr, size, PROT_NONE, MAP_NORESERVE )' in remap_fn and remap_fn.index('anon_mmap_tryfixed(') < remap_fn.index('anon_mmap_fixed('))
check('the new kernel reads (regions, the candidate after a refusal) happen only after a refusal',
      once_fn.count('ios_va_regions(') == 1 and 'if (ptr && ctx->nrefused)' in once_fn
      and inner_fn.count('ios_va_regions(') == 1 and inner_fn.index('ios_va_regions(') > inner_fn.index('if (first_refused)'))
check('requests of 1 MB or more have a budget of their own (64, then every 64th); the small ones 64 then every 256th (round 624: was eight)',
      'int first_err, show = 0, big = size >= 0x100000;' in inner_fn
      and 'show = big ? (++ra_big_n <= 64 || !(ra_big_n % 64)) : (++ra_n <= 64 || !(ra_n % 256));' in inner_fn)
check('the first [reserved-area] line keeps its words (tools and earlier logs read them)',
      '"[reserved-area] #%lu MAP_FIXED over our own reservation refused: %p+0x%lx errno=%d (%s) | %s | "' in inner_fn
      and '"%s (%u refused in all; small address map retry %s) rev=ajan5-motor\\n"' in inner_fn)
check('the catalog lists env.MADEIRA_RESERVED_REMAP, on', '"env.MADEIRA_RESERVED_REMAP"' in (root / 'build/tools/gen-config-catalog.py').read_text())

# ---- round 623: the skip past the kernel's fixed regions (Hotline Miami 2 3NB0B0, Metro: Last Light QJANDC) ----
skip_fn = function('static void *ios_reserved_skip(')
scan_fn = function('static int ios_va_blocker_scan(')
blocker_fn = function('static int ios_va_blocker( const void *addr, size_t size, uintptr_t *blo, uintptr_t *bhi )\n{')
check('the skip and the scan only call the placement again and read the kernel through a callback; they map, protect and unmap nothing',
      all(m not in skip_fn + scan_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'anon_mmap', 'memset(', 'memcpy(', 'mach_vm')))
check('the kernel is read by mach_vm_region only, and only the blocker query reaches it',
      'mach_vm_region(' in function('static int ios_va_region_next(') and 'ios_va_blocker_scan( (uintptr_t)addr, size, ios_va_region_next' in blocker_fn
      and source.count('ios_va_blocker(') == 4)   # forward declaration, definition, the skip, the last resort
check('a blocker is a region of MAXIMUM protection none (vm_region p/m 0/0), the one kind the log shows in every refusal',
      'unsigned n = 0, prot = 0;' in scan_fn.replace('\n', ' ') or 'unsigned n = 0, prot = 0;' in scan_fn)
check('the skip runs only in the request\'s last attempt, on a small address map, after an ENOMEM refusal, with its key on',
      'if (!ptr && ios_reserved_skip_now && ctx.last_err == ENOMEM && ios_wow_small_va_slots && ios_reserved_skip_enabled())' in inner_fn
      and inner_fn.index('ios_reserved_retry(') < inner_fn.index('ios_reserved_skip(') < inner_fn.index('ios_reserved_remap( first_refused')
      and 'ios_wow_small_va_slots && !ios_reserved_skip_now ? IOS_RESERVED_RETRY_MAX : 0' in inner_fn)
last_fn = function('static void *map_reserved_area_last(')
check('the last attempt is map_reserved_area_last: the flag set round one map_reserved_area call and cleared again, nowhere else',
      source.count('ios_reserved_skip_now = 1;') == 1 and last_fn.index('ios_reserved_skip_now = 1;') < last_fn.index('map_reserved_area(')
      < last_fn.index('ios_reserved_skip_now = 0;') and source.count('ios_reserved_skip_now = 0;') == 1)
call_at = source.index('map_reserved_area_last( start, end, host_size, top_down, unix_prot, align_mask )')
check('map_reserved_area_last is called once, on a small address map, only where map_view is about to return STATUS_NO_MEMORY for a request that may not relax',
      source.count('map_reserved_area_last(') == 2 and 'ios_wow_small_va_slots &&' in source[call_at - 200:call_at]
      and source.rindex('if (!ceiling_relaxable)', 0, call_at) > call_at - 900
      and 'return STATUS_NO_MEMORY;' in source[call_at:call_at + 700])
check('each skip step moves strictly past the last refusal and the steps are capped',
      '#define IOS_RESERVED_SKIP_MAX 64' in source and 'while (*jumps < IOS_RESERVED_SKIP_MAX)' in skip_fn
      and 'if (next_low <= (uintptr_t)refused || next_low >= (uintptr_t)high)\n            { ios_reserved_skip_ended = "no room above the last kernel region"; break; }' in skip_fn
      and 'if (blo <= size || blo <= (uintptr_t)low) { ios_reserved_skip_ended = "no room below the last kernel region"; break; }' in skip_fn)
check('the skip ends at a placement, a refusal other than ENOMEM, a search that finds nothing, or a refusal no blocker explains',
      'if (ptr) break;' in skip_fn
      and 'if (!refused) { ios_reserved_skip_ended = "the search found no free range of that size past it"; break; }' in skip_fn
      and 'if (err != ENOMEM) { ios_reserved_skip_ended = "a refusal other than ENOMEM"; break; }' in skip_fn
      and 'if (!ios_va_blocker( refused, size, &blo, &bhi )) { ios_reserved_skip_ended = "a refusal no kernel region explains"; break; }' in skip_fn)
check('round 624: every way the skip ends without a placement is named (five breaks and the step cap), and it starts with none',
      skip_fn.count('ios_reserved_skip_ended = "') == 6 and 'ios_reserved_skip_ended = NULL;' in skip_fn
      and 'if (!ptr && *jumps >= IOS_RESERVED_SKIP_MAX) ios_reserved_skip_ended = "the step cap";' in skip_fn)
check('the last resort asks for a blocker before it unmaps (a region no munmap removes would only leave a hole)',
      remap_fn.index('ios_va_blocker( addr, size') < remap_fn.index('munmap(') and 'remap not tried: the range holds a kernel region' in remap_fn)
check('the placement records its newest refusal for the skip', 'ctx->last_refused = ptr; ctx->last_err = *err;' in once_fn)
check('the first [reserved-area] line keeps its words; the skip has a line of its own',
      '"[reserved-area] #%lu skip: %u step(s) past kernel regions' in inner_fn and 'rev=ajan58-skip' in inner_fn)
check('the catalog lists env.MADEIRA_RESERVED_SKIP, on', '"env.MADEIRA_RESERVED_SKIP"' in (root / 'build/tools/gen-config-catalog.py').read_text())

# ---- round 624 (Hotline Miami 2 4RHXR8, agent A S2): the failure-path line and the kernel's free stretches ----
hole_fn = function('static void *ios_reserved_kernel_hole(')
keep_fn = function('static void ios_kernel_hole_keep(')
census_fn = function('static int ios_va_dead_census(')
prot_fn = function('static int ios_va_region_prot(')
check('S2.2 maps, protects and unmaps nothing itself: it only reads regions through a callback and calls the ordinary placement again',
      all(m not in hole_fn + keep_fn + census_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'anon_mmap', 'mach_vm', 'memset(', 'memcpy('))
      and 'ptr = once( (void *)run_lo[k], (void *)run_hi[k], ctx, &refused, &err );' in hole_fn and 'info( a, &rlo, &rhi, &cur, &max )' in hole_fn)
check('a stretch ends at a dead region (maximum protection none) or a live mapping; reservation and unmapped holes belong to it',
      'if (!max || cur)' in hole_fn and 'if (rlo > from && rlo - from >= size) ios_kernel_hole_keep(' in hole_fn and 'from = rhi;' in hole_fn)
check('at most 8192 regions are read (then only what was read counts) and IOS_KERNEL_HOLE_MAX stretches kept, the highest ones top-down',
      'if (n++ >= 8192) { end = a; break; }' in hole_fn and '#define IOS_KERNEL_HOLE_MAX 64' in source
      and 'if (!keep_top) return;' in keep_fn and 'ios_kernel_hole_keep( run_lo, run_hi, &nruns, from, rlo, top_down );' in hole_fn
      and 'unsigned k = top_down ? nruns - 1 - i : i;' in hole_fn)
check('S2.2 runs only inside the skip\'s own block (last attempt, small map, ENOMEM, key on), after a skip that placed nothing, still ENOMEM',
      'skip_ran = 1;' in inner_fn and 'if (!ptr && ctx.last_err == ENOMEM)\n                hole_placed = (ptr = ios_reserved_kernel_hole( limit_low, limit_high, size, top_down, map_reserved_area_once, &ctx,' in inner_fn
      and inner_fn.index('ios_reserved_skip(') < inner_fn.index('ios_reserved_kernel_hole(') < inner_fn.index('ios_reserved_remap( first_refused')
      and inner_fn.count('ios_reserved_kernel_hole(') == 1 and source.count('ios_reserved_kernel_hole(') == 2)
check('the real kernel is read by mach_vm_region with both protections, read-only',
      'mach_vm_region(' in prot_fn and '*cur = (unsigned)info.protection; *max = (unsigned)info.max_protection;' in prot_fn
      and all(m not in prot_fn for m in ('mmap(', 'munmap(', 'mprotect(', 'mach_vm_allocate', 'mach_vm_deallocate', 'mach_vm_protect')))
check('the census of dead regions reads at most 8192 regions and says when it stopped there',
      'if (n++ >= 8192) return 1;' in census_fn and '(8192 regions read: the walk stopped there)' in inner_fn)
check('one "last attempt FAILED" line when the last attempt ends without a placement, with a budget of its own (64, then every 64th)',
      'if (!ptr && ios_reserved_skip_now)' in inner_fn and 'if (++fail_n <= 64 || !(fail_n % 64))' in inner_fn
      and '"[reserved-area] last attempt FAILED #%lu: 0x%lx bytes %s; first refused %p errno=%d; %u skip step(s) of %d, ended: %s; "' in inner_fn
      and '"last kernel region 0x%lx+0x%lx; kernel\'s free stretches tried: %u; window %p-%p: %u run(s) of kernel regions of "' in inner_fn)
check('the line says why the skip did not run, or why it ended',
      '!skip_ran ? "not run (the refusal was not ENOMEM)"' in inner_fn and ': ios_reserved_skip_ended ? ios_reserved_skip_ended : "unknown";' in inner_fn)
check('a placement by S2.2 is named in the first line and in the skip line',
      'hole_placed ? "the last attempt placed it in a free stretch the kernel reported"' in inner_fn
      and 'hole_placed ? "the skip did not place it; the kernel\'s free stretch did"' in inner_fn)

harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
''' + typedefs + retry_fn + r'''
/* a window of candidate 64 KB-aligned addresses; `bad` ones are refused with `bad_err` */
#define NCAND 8
static uintptr_t cand[NCAND];
static int bad[NCAND], bad_err = ENOMEM;
static unsigned calls;

static void *once_fake( void *low, void *high, void *ctx, void **refused, int *err )
{
    size_t size = *(size_t *)ctx;
    int i;
    (void)size; calls++;
    /* bottom-up: the lowest candidate in [low, high - size]; top-down is tested through `topdown` below */
    for (i = 0; i < NCAND; i++)
    {
        uintptr_t a = cand[i];
        if (a < (uintptr_t)low || a + size > (uintptr_t)high) continue;
        if (bad[i]) { *refused = (void *)a; *err = bad_err; return NULL; }
        return (void *)a;
    }
    return NULL;
}
static void *once_fake_td( void *low, void *high, void *ctx, void **refused, int *err )
{
    size_t size = *(size_t *)ctx;
    int i;
    calls++;
    for (i = NCAND - 1; i >= 0; i--)
    {
        uintptr_t a = cand[i];
        if (a < (uintptr_t)low || a + size > (uintptr_t)high) continue;
        if (bad[i]) { *refused = (void *)a; *err = bad_err; return NULL; }
        return (void *)a;
    }
    return NULL;
}

int main(void)
{
    size_t size = 0x14000, align_mask = 0xffff;
    unsigned n; void *first; int err, i;
    void *p;
    for (i = 0; i < NCAND; i++) { cand[i] = 0x400110000UL + (uintptr_t)i * 0x100000; bad[i] = 0; }
    void *lo = (void *)0x400110000UL, *hi = (void *)0x500000000UL;

    /* nothing refused: placed at the first range, no retry, nothing recorded */
    calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == (void *)cand[0] && n == 0 && first == NULL && calls == 1 );

    /* two refusals bottom-up: the third range is taken; the first refusal is the one reported */
    bad[0] = bad[1] = 1; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == (void *)cand[2] && n == 2 && first == (void *)cand[0] && err == ENOMEM && calls == 3 );

    /* the same on a 512 GB map (zero tries): the refusal is reported and nothing is retried, as before */
    calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 0, once_fake, &size, &n, &first, &err );
    assert( p == NULL && n == 1 && first == (void *)cand[0] && calls == 1 );

    /* top-down: the highest candidates are refused, the next lower is taken */
    memset( bad, 0, sizeof bad ); bad[NCAND - 1] = bad[NCAND - 2] = 1; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 1, align_mask, 32, once_fake_td, &size, &n, &first, &err );
    assert( p == (void *)cand[NCAND - 3] && n == 2 && first == (void *)cand[NCAND - 1] && calls == 3 );

    /* only ENOMEM is retried: EEXIST (someone else's mapping) is not */
    memset( bad, 0, sizeof bad ); bad[0] = 1; bad_err = EEXIST; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == NULL && n == 1 && err == EEXIST && calls == 1 );
    bad_err = ENOMEM;

    /* the cap holds */
    for (i = 0; i < NCAND; i++) bad[i] = 1; calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 3, once_fake, &size, &n, &first, &err );
    assert( p == NULL && calls == 4 && n == 4 );

    /* every candidate refused and nothing left: the loop ends with NULL */
    calls = 0;
    p = ios_reserved_retry( lo, hi, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == NULL && calls == NCAND + 1 && n == NCAND );

    /* a search that finds no range at all is not a refusal */
    memset( bad, 0, sizeof bad ); calls = 0;
    p = ios_reserved_retry( (void *)0x600000000UL, (void *)0x700000000UL, size, 0, align_mask, 32, once_fake, &size, &n, &first, &err );
    assert( p == NULL && n == 0 && first == NULL && calls == 1 );

    puts( "PASS: retry after ENOMEM (bottom-up, top-down), none on a 512 GB map, none for other errors, capped, none without a refusal" );
    return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='reserved-area-retry-') as directory:
    temp = Path(directory)
    unit = temp / 'retry.c'
    unit.write_text(harness)
    exe = temp / 'retry'
    subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-D_DARWIN_C_SOURCE', '-D_GNU_SOURCE', '-Wall', '-Wextra',
                    '-Werror', str(unit), '-o', str(exe)], check=True)
    r = subprocess.run([str(exe)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    check('the retry loop behaves against fake placements', r.returncode == 0, r.stderr)

# ---- the whole placement (map_reserved_area_inner) against fakes: the last resort, the budgets, the healthy path ----
ctx_struct = source[source.index('struct ios_reserved_ctx\n{'):]
ctx_struct = ctx_struct[:ctx_struct.index('};') + 3]
remap_region = source[source.index('static int ios_reserved_remap_enabled(void)'):source.index('/* remap-test:end */')]
skip_region = source[source.index('#define IOS_RESERVED_SKIP_MAX 64'):source.index('/* skip-test:end */')]
harness2 = r"""
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#define IOS_RESERVED_RETRY_MAX 32
struct file_view { int x; };
typedef unsigned long SIZE_T;
/* ---- the kernel and Wine's tables, faked ---- */
static int view_overlaps, in_area = 1, pool_hit, unmap_rc, unmap_errno = EACCES, tryfixed_ok = 1, restore_ok = 1;
static unsigned n_unmap, n_try, n_restore, n_regions, n_describe;
static void *last_unmap, *last_try, *last_restore;
static int last_try_prot = -1, last_restore_prot = -1;
static struct file_view dummy_view;
static struct file_view *find_view_range( const void *a, size_t s ) { (void)a; (void)s; return view_overlaps ? &dummy_view : NULL; }
static int mmap_is_in_reserved_area( void *a, SIZE_T s ) { (void)a; (void)s; return in_area; }
static int ios_jit_pool_intersects( const void *a, size_t s ) { (void)a; (void)s; return pool_hit; }
static void ios_pool_va_warn( const char *w, const void *a, size_t s ) { (void)w; (void)a; (void)s; }
static int fake_munmap( void *a, size_t s ) { (void)s; n_unmap++; last_unmap = a; if (unmap_rc) errno = unmap_errno; return unmap_rc; }
static void *anon_mmap_tryfixed( void *a, size_t s, int prot, int flags ) { (void)s; (void)flags; n_try++; last_try = a; last_try_prot = prot; if (tryfixed_ok) return a; errno = ENOMEM; return MAP_FAILED; }
void *anon_mmap_fixed( void *a, size_t s, int prot, int flags ) { (void)s; (void)flags; n_restore++; last_restore = a; last_restore_prot = prot; if (restore_ok) return a; errno = ENOMEM; return MAP_FAILED; }
#define munmap fake_munmap
static unsigned ios_wow_small_va_slots = 12;
static char logbuf[200000]; static unsigned nlines_first, nlines_detail, nlines_last, nlines_failed;
static int dprintf_fake( int fd, const char *fmt, ... )
{
    char line[1500]; va_list ap; (void)fd;
    va_start( ap, fmt ); vsnprintf( line, sizeof(line), fmt, ap ); va_end( ap );
    if (strstr( line, "last attempt FAILED" )) nlines_failed++;
    else if (strstr( line, " detail: " )) nlines_detail++;
    else if (strstr( line, "last resort at" ) && !strstr( line, "MAP_FIXED over" )) nlines_last++;
    else if (strstr( line, "MAP_FIXED over our own reservation refused" )) nlines_first++;
    if (strlen( logbuf ) + strlen( line ) < sizeof(logbuf) - 2) strcat( logbuf, line );
    return 0;
}
#define dprintf dprintf_fake
static void ios_va_describe( void *a, char *b, size_t n ) { (void)a; n_describe++; snprintf( b, n, "OCCUPIED fake prot=0/0" ); }
static void ios_va_regions( void *a, size_t s, char *b, size_t n ) { (void)a; (void)s; n_regions++; snprintf( b, n, "0xA+0x10000 0/0" ); }
/* the kernel's regions of maximum protection none ("bands", ascending): a MAP_FIXED over a range that touches one is refused (ENOMEM) */
#define NBANDS 4
static uintptr_t band_lo[NBANDS], band_hi[NBANDS];
static unsigned nbands, n_blocker;
static int comb;   /* bands every comb_period from comb_start, comb_width wide, comb_count of them (0: to the window's end);
                      the default: every 32 MB, 1 MB wide, from 0x40f800000 on: no 32 MB range is free of them */
static uintptr_t comb_start = 0x40f800000UL, comb_period = 0x2000000UL, comb_width = 0x100000; static unsigned comb_count;
static int comb_overlap( uintptr_t x, uintptr_t y, uintptr_t *lo, uintptr_t *hi )
{
    uintptr_t k = x <= comb_start ? 0 : (x - comb_start) / comb_period, l, h;
    for (;; k++)
    {
        if (comb_count && k >= comb_count) return 0;
        l = comb_start + k * comb_period; h = l + comb_width;
        if (l >= y) return 0;
        if (h > x) { *lo = l; *hi = h; return 1; }
    }
}
static int ios_va_blocker( const void *a, size_t s, uintptr_t *lo, uintptr_t *hi )
{
    uintptr_t x = (uintptr_t)a, y = x + s; unsigned i;
    n_blocker++;
    if (comb) return comb_overlap( x, y, lo, hi );
    for (i = 0; i < nbands; i++) if (band_lo[i] < y && band_hi[i] > x) { *lo = band_lo[i]; *hi = band_hi[i]; return 1; }
    return 0;
}
""" + ctx_struct + typedefs + retry_fn + remap_region + skip_region + r"""
/* ---- round 624: the kernel's regions with both protections (S2.2) and the census, faked over the same bands ---- */
static uintptr_t live_lo, live_hi;   /* one live mapping (protection read/write), none when live_hi is 0 */
static int tiny;                      /* every region 16 KB of our own reservation: the 8192-region cap */
static unsigned n_regread;
static int next_band( uintptr_t a, uintptr_t *lo, uintptr_t *hi )    /* the lowest band that ends above a */
{
    uintptr_t l = 0, h = 0, cl, ch; unsigned i; int got = 0;
    for (i = 0; i < nbands; i++) if (band_hi[i] > a && (!got || band_lo[i] < l)) { l = band_lo[i]; h = band_hi[i]; got = 1; }
    if (comb && comb_overlap( a, 0x500000000UL, &cl, &ch ) && (!got || cl < l)) { l = cl; h = ch; got = 1; }
    if (got) { *lo = l; *hi = h; }
    return got;
}
static int ios_va_region_prot( uintptr_t a, uintptr_t *lo, uintptr_t *hi, unsigned *cur, unsigned *max )
{
    uintptr_t bl, bh, e = 0x500000000UL;
    n_regread++;
    if (a >= 0x500000000UL) return 0;
    if (tiny) { *lo = a & ~(uintptr_t)0x3fff; *hi = *lo + 0x4000; *cur = 0; *max = 7; return 1; }
    if (live_hi && live_lo <= a && live_hi > a) { *lo = live_lo; *hi = live_hi; *cur = 3; *max = 7; return 1; }
    if (next_band( a, &bl, &bh ))
    {
        if (bl <= a) { *lo = bl; *hi = bh; *cur = 0; *max = 0; return 1; }
        e = bl;
    }
    if (live_hi && live_lo > a && live_lo < e) e = live_lo;
    *lo = a & ~(uintptr_t)0x3fff; *hi = e; *cur = 0; *max = 7;   /* our PROT_NONE reservation */
    return 1;
}
static int census_next( uintptr_t a, uintptr_t *lo, uintptr_t *hi, unsigned *max )
{
    unsigned cur;
    return ios_va_region_prot( a, lo, hi, &cur, max );
}
static int ios_va_dead_census_now( uintptr_t lo, uintptr_t hi, unsigned *runs, uintptr_t *dead, uintptr_t *gap, uintptr_t *gap_at )
{
    return ios_va_dead_census( lo, hi, census_next, runs, dead, gap, gap_at );
}
/* the scripted placement: the first `refuse` candidates (64 KB apart from 0x40f8c0000) are refused with `ref_err`, then one is taken (or never) */
static unsigned refuse, ref_err = ENOMEM, called; static int never_place;
static uintptr_t last_once_lo, last_once_hi;
static void *map_reserved_area_once( void *limit_low, void *limit_high, void *ctx_, void **refused, int *err );
""" + inner_fn + r"""
static void *map_reserved_area_once( void *limit_low, void *limit_high, void *ctx_, void **refused, int *err )
{
    struct ios_reserved_ctx *ctx = ctx_;
    uintptr_t lo = (uintptr_t)limit_low, hi = (uintptr_t)limit_high, a;
    void *ptr;
    unsigned i;
    int touches = 0;
    called++; last_once_lo = lo; last_once_hi = hi;
    if (ctx->top_down)
    {
        if (hi < ctx->size) return NULL;
        a = (hi - ctx->size) & ~(uintptr_t)ctx->align_mask;
        if (a < lo) return NULL;                            /* the search finds no range: not a refusal */
    }
    else
    {
        a = lo > 0x40f8c0000UL ? lo : 0x40f8c0000UL;
        a = (a + ctx->align_mask) & ~(uintptr_t)ctx->align_mask;
        if (a + ctx->size > hi) return NULL;
    }
    ptr = (void *)a;
    for (i = 0; i < nbands; i++) if (band_lo[i] < a + ctx->size && band_hi[i] > a) touches = 1;
    if (comb) { uintptr_t cl, ch; touches |= comb_overlap( a, a + ctx->size, &cl, &ch ); }
    if (ptr && ctx->nrefused) { ctx->next_pre_addr = ptr; ios_va_regions( ptr, ctx->size, ctx->next_pre, sizeof(ctx->next_pre) ); }
    if (never_place || touches || ctx->nrefused < refuse)
    {
        *refused = ptr; *err = (int)ref_err;
        if (ctx->nrefused < 40) ctx->refused_list[ctx->nrefused] = ptr;
        ctx->nrefused++;
        ctx->last_refused = ptr; ctx->last_err = (int)ref_err;
        return NULL;
    }
    return ptr;
}
static void *place( size_t size, int unix_prot )
{
    called = 0;
    return map_reserved_area_inner( (void *)0x400110000UL, (void *)0x500000000UL, size, 0, unix_prot, 0xffff );
}
static void *place_td( size_t size, int unix_prot )
{
    called = 0;
    return map_reserved_area_inner( (void *)0x400110000UL, (void *)0x500000000UL, size, 1, unix_prot, 0xffff );
}
/* the request's last attempt (map_reserved_area_last sets the flag round the same call) */
static void *place_last( size_t size, int unix_prot )
{
    void *r;
    ios_reserved_skip_now = 1; r = place( size, unix_prot ); ios_reserved_skip_now = 0;
    return r;
}
static void *place_td_last( size_t size, int unix_prot )
{
    void *r;
    ios_reserved_skip_now = 1; r = place_td( size, unix_prot ); ios_reserved_skip_now = 0;
    return r;
}
static void reset( void )
{
    n_unmap = n_try = n_restore = n_regions = n_describe = 0; last_unmap = last_try = last_restore = NULL;
    view_overlaps = pool_hit = unmap_rc = 0; in_area = 1; tryfixed_ok = restore_ok = 1; ref_err = ENOMEM; never_place = 0;
    ios_wow_small_va_slots = 12; refuse = 0; logbuf[0] = 0; nlines_first = nlines_detail = nlines_last = 0;
    nbands = n_blocker = 0; comb = 0; ios_reserved_skip_now = 0;
    comb_start = 0x40f800000UL; comb_period = 0x2000000UL; comb_width = 0x100000; comb_count = 0;
    live_lo = live_hi = 0; tiny = 0; n_regread = 0; nlines_failed = 0; last_once_lo = last_once_hi = 0;
}
static struct { uintptr_t lo, hi; unsigned prot; } fmap[16];
static unsigned nfmap, freads; static int many;
static void add_region( uintptr_t lo, uintptr_t hi, unsigned prot ) { fmap[nfmap].lo = lo; fmap[nfmap].hi = hi; fmap[nfmap].prot = prot; nfmap++; }
static int fake_next( uintptr_t a, uintptr_t *lo, uintptr_t *hi, unsigned *m )
{
    unsigned i;
    freads++;
    if (many) { *lo = a & ~0xfffUL; *hi = *lo + 0x1000; *m = 7; return 1; }
    for (i = 0; i < nfmap; i++) if (fmap[i].hi > a) { *lo = fmap[i].lo; *hi = fmap[i].hi; *m = fmap[i].prot; return 1; }
    return 0;
}
int main( int argc, char **argv )
{
    void *p;
    int i;
    if (argc > 1 && !strcmp( argv[1], "budget" ))
    {
    /* log budgets: small requests 64 lines then every 256th (round 624: was eight); 1 MB and more 64 then every 64th */
    reset(); never_place = 1; ios_wow_small_va_slots = 0;       /* no last resort, one refusal per chain: only the logging */
    for (i = 0; i < 300; i++) place( 0x14000, 3 );
    {
        assert( nlines_first == 64 + 1 );    /* 64, then the 256th */
    }
    reset(); never_place = 1; ios_wow_small_va_slots = 0;
    for (i = 0; i < 200; i++) place( 0x810000, 3 );
    assert( nlines_first == 64 + 2 );        /* 64, then the 128th and the 192nd */
    assert( nlines_failed == 0 );            /* never the request's last attempt: no "last attempt FAILED" line */
    /* the "last attempt FAILED" line: 64, then every 64th, its own budget */
    reset(); never_place = 1; ios_wow_small_va_slots = 0;
    for (i = 0; i < 200; i++) place_last( 0x14000, 3 );
    assert( nlines_failed == 64 + 2 && strstr( logbuf, "ended: not run (a large address map)" ) );

        puts( "PASS: log budgets" );
        return 0;
    }
    if (argc > 1 && !strcmp( argv[1], "off" ))
    {
        /* env.MADEIRA_RESERVED_REMAP = 0 (read once per process): the retry and the logging as before, no last resort */
        reset(); never_place = 1; p = place( 0x810000, 3 );
        assert( p == NULL && n_unmap == 0 && n_try == 0 && n_restore == 0 && called == 33 && nlines_first == 1 && nlines_detail == 1 );
        assert( !strstr( logbuf, "last resort" ) );
        puts( "PASS: MADEIRA_RESERVED_REMAP=0 keeps the retry and the log, and does no last resort" );
        return 0;
    }

    if (argc > 1 && !strcmp( argv[1], "skipoff" ))
    {
        /* env.MADEIRA_RESERVED_SKIP = 0 (read once per process): the plain retries only, as before round 623 */
        reset(); nbands = 1; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL;
        p = place_last( 0x810000, 3 );
        assert( p == NULL && called == 1 && n_blocker == 1 /* only the remap guard asks */ && n_unmap == 0 && n_try == 0 );
        assert( !strstr( logbuf, "skip:" ) && strstr( logbuf, "NOT placed" ) && strstr( logbuf, "remap not tried: the range holds a kernel region" ) );
        assert( strstr( logbuf, "ended: not run (env.MADEIRA_RESERVED_SKIP = 0)" ) && strstr( logbuf, "kernel's free stretches tried: 0;" ) );
        puts( "PASS: MADEIRA_RESERVED_SKIP=0 keeps the plain retries and does no skip" );
        return 0;
    }
    if (argc > 1 && !strcmp( argv[1], "scan" ))
    {
        /* the scan of the kernel's regions, against a fake map */
        uintptr_t lo, hi;
        nfmap = freads = 0; many = 0;
        assert( !ios_va_blocker_scan( 0x410000000UL, 0x1000000, fake_next, &lo, &hi ) );                       /* no region at all */
        add_region( 0x410000000UL, 0x410004000UL, 7 ); add_region( 0x410004000UL, 0x410044000UL, 7 );
        assert( !ios_va_blocker_scan( 0x410000000UL, 0x1000000, fake_next, &lo, &hi ) );                       /* regions that MAP_FIXED replaces */
        nfmap = 0;
        add_region( 0x410000000UL, 0x410004000UL, 7 ); add_region( 0x410004000UL, 0x410044000UL, 0 ); add_region( 0x41bb60000UL, 0x420000000UL, 7 );
        assert( ios_va_blocker_scan( 0x404960000UL, 0x17010000, fake_next, &lo, &hi ) && lo == 0x410004000UL && hi == 0x410044000UL );   /* Metro */
        nfmap = 0;
        add_region( 0x401000000UL, 0x401030000UL, 0 );
        assert( ios_va_blocker_scan( 0x401010000UL, 0x20000, fake_next, &lo, &hi ) && lo == 0x401000000UL && hi == 0x401030000UL );     /* begins below the range */
        nfmap = 0;
        add_region( 0x402e00000UL, 0x402e10000UL, 0 ); add_region( 0x402e10000UL, 0x402e20000UL, 0 ); add_region( 0x402e20000UL, 0x402e24000UL, 0 );
        add_region( 0x402e24000UL, 0x402e30000UL, 7 ); add_region( 0x402e40000UL, 0x402e50000UL, 0 );
        assert( ios_va_blocker_scan( 0x402e00000UL, 0x100000, fake_next, &lo, &hi ) && lo == 0x402e00000UL && hi == 0x402e24000UL );    /* adjacent ones merge, the run ends at a region that is not one */
        nfmap = 0;
        add_region( 0x402e00000UL, 0x402e10000UL, 0 ); add_region( 0x402e30000UL, 0x402e40000UL, 0 );
        assert( ios_va_blocker_scan( 0x402e00000UL, 0x100000, fake_next, &lo, &hi ) && lo == 0x402e00000UL && hi == 0x402e10000UL );    /* a gap ends the run: the first one only */
        nfmap = 0;
        add_region( 0x410000000UL, 0x410010000UL, 0 );
        assert( !ios_va_blocker_scan( 0x404960000UL, 0x1000000, fake_next, &lo, &hi ) );                       /* a blocker beyond the range is none */
        many = 1; freads = 0;
        assert( !ios_va_blocker_scan( 0x400000000UL, 0x100000000UL, fake_next, &lo, &hi ) && freads <= 8192 );                          /* capped */
        puts( "PASS: scan" );
        return 0;
    }

    /* healthy: placed first time: nothing read from the kernel, nothing logged, no remap */
    reset(); p = place( 0x810000, 3 );
    assert( p == (void *)0x40f8c0000UL && n_regions == 0 && n_describe == 0 && n_unmap == 0 && logbuf[0] == 0 );

    /* the 578 small case: nine refusals, the retry places it: logged, but no last resort */
    reset(); refuse = 9; p = place( 0x14000, 3 );
    assert( p == (void *)(0x40f8c0000UL + 9 * 0x10000UL) && n_unmap == 0 && n_try == 0 && nlines_first == 1 && nlines_detail == 1 );
    assert( strstr( logbuf, "it took the mapping" ) && strstr( logbuf, "9 candidate(s) refused" ) && !strstr( logbuf, "last resort" ) );

    /* the 578 Shade 8 MB case: every retry refused (33 candidates): the last resort maps the FIRST refused candidate */
    reset(); never_place = 1; p = place( 0x810000, 3 );
    assert( p == (void *)0x40f8c0000UL && n_unmap == 1 && last_unmap == (void *)0x40f8c0000UL && n_try == 1 && last_try == last_unmap
            && last_try_prot == 3 && n_restore == 0 );
    assert( strstr( logbuf, "munmap then a no-overwrite map placed it" ) && strstr( logbuf, "(it was refused too)" ) && strstr( logbuf, "[reserved-area] #1 detail" ) );
    assert( strstr( logbuf, "the last resort placed it" ) );
    assert( strstr( logbuf, "33 candidate(s) refused" ) && strstr( logbuf, "(>= 1 MB, own budget)" ) );

    /* the no-overwrite map fails too: the reservation goes back as PROT_NONE and the request fails as before */
    reset(); never_place = 1; tryfixed_ok = 0; p = place( 0x810000, 3 );
    assert( p == NULL && n_unmap == 1 && n_try == 1 && n_restore == 1 && last_restore == (void *)0x40f8c0000UL && last_restore_prot == PROT_NONE );
    assert( strstr( logbuf, "reservation put back" ) );
    reset(); never_place = 1; tryfixed_ok = 0; restore_ok = 0; p = place( 0x810000, 3 );
    assert( p == NULL && strstr( logbuf, "reservation NOT put back" ) );

    /* munmap refused: nothing changed, nothing else called */
    reset(); never_place = 1; unmap_rc = -1; p = place( 0x810000, 3 );
    assert( p == NULL && n_unmap == 1 && n_try == 0 && n_restore == 0 && strstr( logbuf, "munmap failed errno=" ) );

    /* a view, a partial area or the JIT pool in the way: never touched */
    reset(); never_place = 1; view_overlaps = 1; p = place( 0x810000, 3 );
    assert( p == NULL && n_unmap == 0 && n_try == 0 && n_restore == 0 && strstr( logbuf, "a view overlaps it" ) );
    reset(); never_place = 1; in_area = -1; p = place( 0x810000, 3 );
    assert( p == NULL && n_unmap == 0 && strstr( logbuf, "not wholly inside one reserved area" ) );
    reset(); never_place = 1; in_area = 0; p = place( 0x810000, 3 );
    assert( p == NULL && n_unmap == 0 );
    reset(); never_place = 1; pool_hit = 1; p = place( 0x810000, 3 );
    assert( p == NULL && n_unmap == 0 && strstr( logbuf, "touches the JIT pool" ) );

    /* a 512 GB map (no small-map slots): one refusal reported, nothing retried, nothing remapped, as before */
    reset(); never_place = 1; ios_wow_small_va_slots = 0; p = place( 0x810000, 3 );
    assert( p == NULL && called == 1 && n_unmap == 0 && n_try == 0 && nlines_first == 1 );

    /* another error than ENOMEM (someone else's mapping): neither retried nor remapped */
    reset(); never_place = 1; ref_err = EEXIST; p = place( 0x810000, 3 );
    assert( p == NULL && called == 1 && n_unmap == 0 );

    /* ---- round 623: the skip past the kernel's fixed regions (ios_reserved_skip), the request's LAST attempt only ---- */
    /* any other placement (the LAA pre-attempt, the biased first try ...) never skips, whatever the kernel holds: plain 33 candidates, then NULL;
     * the last resort does not unmap a range that holds a blocker */
    reset(); nbands = 1; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL;
    p = place( 0x810000, 3 );
    assert( p == NULL && called == 33 && n_blocker == 1 && n_unmap == 0 && n_try == 0 && n_restore == 0 && !strstr( logbuf, "skip:" ) );
    assert( strstr( logbuf, "NOT placed" ) && strstr( logbuf, "remap not tried: the range holds a kernel region no munmap removes (0x40f900000+0x300000" ) );

    /* the last attempt, no blocker known: the first candidate is refused (nothing explains it), nothing more from the skip; round 624: the
     * kernel's one free stretch (the whole window) is tried once and refused too; the last resort as before */
    reset(); never_place = 1; p = place_last( 0x810000, 3 );
    assert( p == (void *)0x40f8c0000UL && called == 2 && n_blocker >= 1 && !strstr( logbuf, "skip:" ) && n_unmap == 1 );
    assert( last_once_lo == 0x400110000UL && last_once_hi == 0x500000000UL && nlines_failed == 0 );

    /* Hotline Miami 2 / Metro: the last attempt jumps the band at once (the plain steps were refused already) and places beyond it */
    reset(); nbands = 1; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL;
    p = place_last( 0x810000, 3 );
    assert( p == (void *)0x40fc00000UL && called == 2 && n_unmap == 0 && n_try == 0 && n_restore == 0 );
    assert( nlines_first == 1 && nlines_detail == 1 && strstr( logbuf, "skip: 1 step(s) past kernel regions" ) );
    assert( strstr( logbuf, "the last 0x40f900000+0x300000" ) && strstr( logbuf, "the retry placed it past the kernel's fixed regions" ) );
    assert( strstr( logbuf, "(1 refused in all" ) && strstr( logbuf, "(it took the mapping)" ) && !strstr( logbuf, "last resort" ) );
    assert( strstr( logbuf, "the placement took a range beyond it" ) && strstr( logbuf, "rev=ajan58-skip" ) );

    /* two bands: the second is met after the first jump and skipped as well */
    reset(); nbands = 2; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL; band_lo[1] = 0x40fd00000UL; band_hi[1] = 0x40fe00000UL;
    p = place_last( 0x810000, 3 );
    assert( p == (void *)0x40fe00000UL && called == 3 && n_unmap == 0 && strstr( logbuf, "skip: 2 step(s)" ) );

    /* top-down: an 8 MB band below the top; the skip ends the next range at its start */
    reset(); nbands = 1; band_lo[0] = 0x4ff000000UL; band_hi[0] = 0x4ff800000UL;
    p = place_td_last( 0x810000, 3 );
    assert( p == (void *)0x4fe7f0000UL && called == 2 && n_unmap == 0 && strstr( logbuf, "skip: 1 step(s)" ) );

    /* a band that fills the window to its top: nothing to place, nothing unmapped, the last resort not tried (it would only leave a hole);
     * round 624: the stretch below the band is tried once (Wine's table has nothing there) and the failure is named */
    reset(); nbands = 1; band_lo[0] = 0x40f800000UL; band_hi[0] = 0x500000000UL;
    p = place_last( 0x810000, 3 );
    assert( p == NULL && called == 2 && n_unmap == 0 && n_try == 0 && n_restore == 0 );
    assert( last_once_lo == 0x400110000UL && last_once_hi == 0x40f800000UL );
    assert( nlines_failed == 1 && strstr( logbuf, "ended: no room above the last kernel region" ) && strstr( logbuf, "kernel's free stretches tried: 1;" )
            && strstr( logbuf, "0 skip step(s) of 64" ) && strstr( logbuf, "1 run(s) of kernel regions of maximum protection none, 3848 MB in all, "
                                                                       "the largest stretch without one 246 MB at 0x400110000" ) );
    assert( strstr( logbuf, "NOT placed" ) && strstr( logbuf, "remap not tried: the range holds a kernel region no munmap removes (0x40f800000+0xf" ) );
    assert( !strstr( logbuf, "skip:" ) );   /* the one step that could be taken was past the window: not taken */

    /* bands everywhere: the skip is capped at IOS_RESERVED_SKIP_MAX steps and then fails as before; round 624: the only stretch of 32 MB
     * without a band (below the first) is tried once, Wine's table has nothing there; the line names the cap and the census */
    reset(); comb = 1;
    p = place_last( 0x2000000, 3 );
    assert( p == NULL && called == 1 + 64 + 1 && n_unmap == 0 && strstr( logbuf, "skip: 64 step(s)" ) && strstr( logbuf, "still NOT placed" ) );
    assert( nlines_failed == 1 && strstr( logbuf, "[reserved-area] last attempt FAILED #" ) && strstr( logbuf, ": 0x2000000 bytes bottom-up; first refused 0x40f8c0000 errno=12;" )
            && strstr( logbuf, "64 skip step(s) of 64, ended: the step cap;" ) && strstr( logbuf, "kernel's free stretches tried: 1;" )
            && strstr( logbuf, "window 0x400110000-0x500000000: 121 run(s) of kernel regions of maximum protection none, 121 MB in all, "
                               "the largest stretch without one 246 MB at 0x400110000 rev=r624" ) );

    /* ---- round 624 (Hotline Miami 2 4RHXR8, S2.2): the kernel's free stretches after a skip that placed nothing ---- */
    /* 100 dead runs 4 MB apart: the 64-step skip ends at its cap; the stretch beyond the last run takes it (the one below the first
     * is tried first, bottom-up, and Wine's table has nothing there) */
    reset(); comb = 1; comb_period = 0x400000; comb_width = 0x10000; comb_count = 100;
    p = place_last( 0x810000, 3 );
    assert( p == (void *)0x428410000UL && called == 1 + 64 + 2 && n_unmap == 0 && n_try == 0 && n_restore == 0 && nlines_failed == 0 );
    assert( strstr( logbuf, "the last attempt placed it in a free stretch the kernel reported" ) && strstr( logbuf, "skip: 64 step(s)" )
            && strstr( logbuf, "the skip did not place it; the kernel's free stretch did" ) && !strstr( logbuf, "last resort" ) );
    assert( n_regread > 200 && n_regread < 260 );
    /* a live mapping inside that stretch ends it: the 4 MB below it is too small, the rest above it takes the request */
    reset(); comb = 1; comb_period = 0x400000; comb_width = 0x10000; comb_count = 100; live_lo = 0x428810000UL; live_hi = 0x428820000UL;
    p = place_last( 0x810000, 3 );
    assert( p == (void *)0x428820000UL && n_unmap == 0 );
    /* top-down: 140 dead runs 4 MB apart at the window's top; the skip passes two per step and stops at its cap; of the two stretches below,
     * the higher one is tried first and takes it at its top */
    reset(); comb = 1; comb_start = 0x4dd000000UL; comb_period = 0x400000; comb_width = 0x10000; comb_count = 140;
    nbands = 1; band_lo[0] = 0x480000000UL; band_hi[0] = 0x480010000UL;
    p = place_td_last( 0x810000, 3 );
    assert( p == (void *)0x4dc7f0000UL && last_once_lo == 0x480010000UL && last_once_hi == 0x4dd000000UL && n_unmap == 0 && nlines_failed == 0 );
    assert( strstr( logbuf, "skip: 64 step(s)" ) && strstr( logbuf, "the skip did not place it; the kernel's free stretch did" ) );
    /* a refusal no kernel region explains (here: only the first MAP_FIXED fails): the skip stops at once, the kernel's stretch takes it */
    reset(); refuse = 1;
    p = place_last( 0x810000, 3 );
    assert( p == (void *)0x40f8c0000UL && called == 2 && n_unmap == 0 && !strstr( logbuf, "skip:" )
            && strstr( logbuf, "the last attempt placed it in a free stretch the kernel reported" ) );
    /* the 8192-region cap: only the stretch that was read is offered */
    reset(); tiny = 1; never_place = 1;
    p = place_last( 0x810000, 3 );
    assert( last_once_lo == 0x400110000UL && last_once_hi == 0x400110000UL + 8192UL * 0x4000 && n_unmap == 1 );
    /* no stretch of the size anywhere (bands every 4 MB over the whole window): nothing tried, still NOT placed, named */
    reset(); comb = 1; comb_start = 0x400100000UL; comb_period = 0x400000; comb_width = 0x10000;
    p = place_last( 0x810000, 3 );
    assert( p == NULL && n_unmap == 0 && nlines_failed == 1 && strstr( logbuf, "kernel's free stretches tried: 0;" ) );

    /* a refusal other than ENOMEM is neither retried nor skipped; a 512 GB map neither; neither reads the kernel's stretches */
    reset(); nbands = 1; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL; ref_err = EEXIST;
    p = place_last( 0x810000, 3 );
    assert( p == NULL && called == 1 && !strstr( logbuf, "skip:" ) && strstr( logbuf, "ended: not run (the refusal was not ENOMEM)" ) );
    reset(); nbands = 1; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL; ios_wow_small_va_slots = 0;
    p = place_last( 0x810000, 3 );
    assert( p == NULL && called == 1 && !strstr( logbuf, "skip:" ) && n_unmap == 0 && strstr( logbuf, "ended: not run (a large address map)" ) );

    /* healthy and merely retried placements read no blocker: nothing changes for them */
    reset(); p = place( 0x810000, 3 );
    assert( p == (void *)0x40f8c0000UL && n_blocker == 0 && n_regread == 0 );
    reset(); p = place_last( 0x810000, 3 );
    assert( p == (void *)0x40f8c0000UL && n_blocker == 0 && n_regread == 0 && logbuf[0] == 0 );
    reset(); refuse = 9; p = place( 0x14000, 3 );
    assert( p == (void *)(0x40f8c0000UL + 9 * 0x10000UL) && n_blocker == 0 && n_regread == 0 && !strstr( logbuf, "skip:" ) );
    /* a request placed by the skip reads no stretch either */
    reset(); nbands = 1; band_lo[0] = 0x40f900000UL; band_hi[0] = 0x40fc00000UL;
    p = place_last( 0x810000, 3 );
    assert( p == (void *)0x40fc00000UL && n_regread == 0 );

    puts( "PASS: nothing but a refused, unplaced chain reaches the new code; the last resort places, restores or does nothing" );
    return 0;
}
"""
with tempfile.TemporaryDirectory(prefix='reserved-area-remap-') as directory:
    temp = Path(directory)
    unit = temp / 'remap.c'
    unit.write_text(harness2)
    exe = temp / 'remap'
    r = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-D_DARWIN_C_SOURCE', '-Wall', '-Wno-unused-function', '-Wno-unused-variable',
                        '-fsanitize=address,undefined', str(unit), '-o', str(exe)], capture_output=True, text=True)
    check('the placement, the last resort and the logging compile on the host', r.returncode == 0, r.stderr[-2500:])
    if r.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0'})
        sys.stdout.write(r.stdout)
        check('map_reserved_area_inner against fake kernel and tables', r.returncode == 0, (r.stdout + r.stderr)[-2500:])
        r = subprocess.run([str(exe), 'off'], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0', 'MADEIRA_RESERVED_REMAP': '0'})
        sys.stdout.write(r.stdout)
        check('env.MADEIRA_RESERVED_REMAP = 0 turns the last resort off', r.returncode == 0, (r.stdout + r.stderr)[-1500:])
        r = subprocess.run([str(exe), 'skipoff'], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0', 'MADEIRA_RESERVED_SKIP': '0'})
        sys.stdout.write(r.stdout)
        check('env.MADEIRA_RESERVED_SKIP = 0 turns the skip off', r.returncode == 0, (r.stdout + r.stderr)[-1500:])
        r = subprocess.run([str(exe), 'scan'], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0'})
        sys.stdout.write(r.stdout)
        check('the scan for a kernel region of maximum protection none, against a fake map', r.returncode == 0, (r.stdout + r.stderr)[-1500:])
        for value in ('1', '', '00', 'no'):
            r = subprocess.run([str(exe)], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0', 'MADEIRA_RESERVED_SKIP': value})
            check('MADEIRA_RESERVED_SKIP=%r leaves the skip on (only "0" turns it off)' % value, r.returncode == 0, (r.stdout + r.stderr)[-800:])
        r = subprocess.run([str(exe), 'budget'], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0'})
        sys.stdout.write(r.stdout)
        check('the log budgets: small requests 64 lines then every 256th, 1 MB and more 64 then every 64th, the FAILED line 64 then every 64th',
              r.returncode == 0, (r.stdout + r.stderr)[-1500:])
        for value in ('1', '', '00', 'no'):
            r = subprocess.run([str(exe)], capture_output=True, text=True, env={**os.environ, 'ASAN_OPTIONS': 'symbolize=0', 'MADEIRA_RESERVED_REMAP': value})
            check('MADEIRA_RESERVED_REMAP=%r leaves it on (only "0" turns it off)' % value, r.returncode == 0, (r.stdout + r.stderr)[-800:])
sys.exit(0 if ok else 1)
