#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[wow-census]: what fills a 32-bit program's guest window when a placement scan in it fails (log only); no Wine runs.

Ported from upstream Madeira v0.2.0 (ml2021; the function is upstream's, with BCD's window registry under it). The log says, once per
window, how many views the 4 GB held, how many MB by kind (image, file, placeholder, reserve, anonymous) and the ten largest, so a
"freeze" after NULL allocations in a 32-bit window can be read (upstream's log 81: 1260 views and no gap over 1.4 MB).

Source shape: the call sits in map_view's failed-scan block right after ios_furniture_census(), only when the scan returned nothing
(`!ptr`), and nothing else changes in that block. A compiled run of the production helper and census with a model registry and view
tree: no window covers the range -> nothing printed; a window with views -> one summary line (counts and MB by kind add up) and at most
ten view lines, the ten largest; a second call for the same window prints nothing, another window prints again; a placeholder counts as a
window (its guard page extends it only when it is ours); a range that touches no window prints nothing.
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


# ---- source shape
cov = function('static ULONG_PTR ios_wow_window_covering( const void *start, const void *end )')
cen = function('static void ios_wow_window_census( const void *start, const void *end )')
block = src[src.index('if (!censused) { censused = 1; ios_furniture_census(); }'):]
block = block[:block.index('/* ml118 discriminator')]
check('the census is called in the failed-scan block, after ios_furniture_census, only when the scan found nothing',
      'if (!ptr) ios_wow_window_census( start, end );' in block and
      block.index('ios_furniture_census()') < block.index('ios_wow_window_census( start, end )'))
check('it is called from one place only', src.count('ios_wow_window_census( start, end )') == 1)
check('the census is defined before map_view and the helper before the census',
      src.index(cov) < src.index(cen) < src.index('static NTSTATUS map_view( struct file_view **view_ret'))
check('the line carries the tag the log reader greps for', '"[wow-census] ml2021 window %p: %u views, %llu MB of 4096 mapped"' in cen)
check('once per window, bounded by the registry size', 'static ULONG_PTR done[IOS_WOW_MAX_WINDOWS];' in cen and 'if (!wb) return;' in cen)

# ---- compiled run
consts = {}
for name in ('VPROT_PLACEHOLDER', 'VPROT_FREE_PLACEHOLDER'):
    consts[name] = re.search(r'#define %s\s+(0x[0-9a-f]+)' % name, src)[1]
window_size = '((ULONG_PTR)1 << 32)'
harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uintptr_t ULONG_PTR;
#define SEC_FILE 0x00800000
#define SEC_IMAGE 0x01000000
#define SEC_RESERVE 0x04000000
#define VPROT_PLACEHOLDER @VP@
#define VPROT_FREE_PLACEHOLDER @VFP@
#define IOS_WOW_WINDOW_SIZE @WS@
#define IOS_WOW_MAX_WINDOWS 8
#define dprintf(fd, ...) fprintf(stderr, __VA_ARGS__)
struct file_view { void *base; size_t size; unsigned int protect; };
static struct file_view views_tree[64];
static int nviews;
#define WINE_RB_FOR_EACH_ENTRY(v, tree, type, member) for (int _i = 0; _i < nviews && ((v) = &views_tree[_i]); _i++)
struct ios_wow_window { ULONG_PTR base; unsigned guard_owned; };
struct ios_wow_placeholder { ULONG_PTR base; unsigned guard_owned; unsigned adopted; };
static struct ios_wow_window ios_wow_windows[IOS_WOW_MAX_WINDOWS];
static unsigned ios_wow_window_count;
static struct ios_wow_placeholder ios_wow_placeholders[IOS_WOW_MAX_WINDOWS];
static unsigned ios_wow_placeholder_count;
static ULONG_PTR ios_wow_guard_size(void) { return 0x4000; }
static ULONG_PTR ios_wow_slot_reservation( const struct ios_wow_window *slot )
{
    return IOS_WOW_WINDOW_SIZE + (slot->guard_owned ? ios_wow_guard_size() : 0);
}
@COV@
@CEN@
static void add(ULONG_PTR base, size_t size, unsigned prot) { views_tree[nviews].base = (void *)base; views_tree[nviews].size = size; views_tree[nviews].protect = prot; nviews++; }
int main(void)
{
    /* no window at all */
    add(0x7200000000ULL, 0x1000, 0);
    ios_wow_window_census((void *)0x7200000000ULL, (void *)0x7200100000ULL);
    fprintf(stderr, "--A\n");

    /* a window with all five kinds and more than ten views */
    ios_wow_windows[0].base = 0x7200000000ULL; ios_wow_windows[0].guard_owned = 1; ios_wow_window_count = 1;
    nviews = 0;
    add(0x7200010000ULL, 0x200000, SEC_IMAGE | 0x4);              /* image 2 MB */
    add(0x7200300000ULL, 0x100000, SEC_FILE | 0x1);               /* file 1 MB */
    add(0x7200400000ULL, 0x4000000, VPROT_PLACEHOLDER | 0x1);     /* placeholder 64 MB */
    add(0x7208000000ULL, 0x10000000, SEC_RESERVE | 0x1);          /* reserve 256 MB */
    for (int i = 0; i < 12; i++) add(0x7220000000ULL + (ULONG_PTR)i * 0x1000000, (size_t)(i + 1) * 0x100000, 0x4);   /* anonymous 1..12 MB */
    add(0x7400000000ULL, 0x40000000, 0x4);                        /* outside the window: not counted */
    ios_wow_window_census((void *)0x7200000000ULL, (void *)0x7300000000ULL);
    fprintf(stderr, "--B\n");
    ios_wow_window_census((void *)0x7200000000ULL, (void *)0x7300000000ULL);   /* same window again: silent */
    fprintf(stderr, "--C\n");

    /* a range that touches no window */
    ios_wow_window_census((void *)0x7500000000ULL, (void *)0x7600000000ULL);
    fprintf(stderr, "--D\n");

    /* a placeholder counts as a window, with its guard page only when it is ours; a range in the guard page alone is covered */
    ios_wow_placeholders[0].base = 0x7600000000ULL; ios_wow_placeholders[0].guard_owned = 1; ios_wow_placeholder_count = 1;
    nviews = 0; add(0x7600010000ULL, 0x1000000, 0x4);
    printf("cover_guard=%d\n", ios_wow_window_covering((void *)0x7700000000ULL, (void *)0x7700004000ULL) == 0x7600000000ULL);
    ios_wow_placeholders[0].guard_owned = 0;
    printf("cover_noguard=%d\n", ios_wow_window_covering((void *)0x7700000000ULL, (void *)0x7700004000ULL) == 0);
    printf("cover_inside=%d\n", ios_wow_window_covering((void *)0x7600010000ULL, (void *)0x7600020000ULL) == 0x7600000000ULL);
    printf("cover_below=%d\n", ios_wow_window_covering((void *)0x75ffff0000ULL, (void *)0x7600000000ULL) == 0);
    printf("cover_window_guard=%d\n", ios_wow_window_covering((void *)0x7300000000ULL, (void *)0x7300004000ULL) == 0x7200000000ULL);
    ios_wow_window_census((void *)0x7600000000ULL, (void *)0x7600100000ULL);
    fprintf(stderr, "--E\n");
    return 0;
}
'''
harness = (harness.replace('@VP@', consts['VPROT_PLACEHOLDER']).replace('@VFP@', consts['VPROT_FREE_PLACEHOLDER'])
           .replace('@WS@', window_size).replace('@COV@', cov).replace('@CEN@', cen))
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-Wno-unused-function',
                         '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production helper and census compile on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-2000:])
    if cc.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        check('the run is clean', r.returncode == 0, r.stderr[-1500:])
        parts = r.stderr.split('--')
        seg = {p[0]: p[1:] for p in parts if p and p[0] in 'ABCDE'}
        # segments hold the text printed BEFORE their marker: A = before "--A", B = before "--B" ...
        first = r.stderr.split('--A\n')[0]
        check('no window covers the range: nothing is printed', first == '', repr(first))
        mid = r.stderr.split('--A\n')[1].split('--B\n')[0].splitlines()
        check('one summary line and ten view lines for a window with 16 views', len(mid) == 11 and mid[0].startswith('[wow-census] ml2021 window '), str(mid[:3]))
        head = mid[0]
        check('16 views are counted (the one outside the window is not), 12+4 views and the sizes add up',
              ' 16 views, ' in head and '| image 1/2 MB | file 1/1 MB | placeholder 1/64 MB | reserve 1/256 MB | anonymous 12/78 MB' in head and
              '%llu MB' not in head and ', 401 MB of 4096 mapped' in head, head)
        sizes = sorted((int(re.search(r'\+0x([0-9a-f]+) ', l)[1], 16) for l in mid[1:]), reverse=True)
        want = sorted([0x200000, 0x100000, 0x4000000, 0x10000000] + [(i + 1) * 0x100000 for i in range(12)], reverse=True)[:10]
        check('the ten largest views of the window are listed', sizes == want, '%s vs %s' % (sizes, want))
        check('a view line is "[wow-census]   <base>+0x<size> protect=<8 hex digits>"', all(re.fullmatch(r'\[wow-census\]   0x[0-9a-f]+\+0x[0-9a-f]+ protect=[0-9a-f]{8}', l) for l in mid[1:]), str(mid[1:3]))
        check('the same window prints nothing the second time', r.stderr.split('--B\n')[1].split('--C\n')[0] == '')
        check('a range that touches no window prints nothing', r.stderr.split('--C\n')[1].split('--D\n')[0] == '')
        check('the covering helper: a placeholder with its own guard page covers the page above it, without one it does not; windows likewise',
              all(('%s=1' % k) in r.stdout for k in ('cover_guard', 'cover_noguard', 'cover_inside', 'cover_below', 'cover_window_guard')), r.stdout)
        last = r.stderr.split('--D\n')[1].split('--E\n')[0].splitlines()
        check('a placeholder is censused as a window', len(last) == 2 and ' 1 views, ' in last[0] and last[0].startswith('[wow-census] ml2021 window 0x7600000000'), str(last))

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-wow-census.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
