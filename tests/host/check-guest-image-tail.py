#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""ios_guest_image_is_host_data: a range that runs past a 32-bit image view only within its last host page is still that image's.

Ported from upstream Madeira v0.2.0 (ml2020). A request is host-page (16 KB) rounded while an image view ends on a 4 KB boundary. A
launcher's protector DLL made its last section plus the rest of that section's final host page executable and writable, unpacked code
there and jumped to it; the range was not "the image" by this test, took the anonymous RWX pool-copy path, and the code it wrote afterwards
was not what the emulator ran ("Invalid instruction in entry block"). The same rounding is already in mprotect_exec's ml348 range check;
this is the other call site. No Wine runs.

A compiled run of the production function against a model view table (host page 16 KB, one image view of 0x5000 bytes at a host-page
aligned base inside a window, one anonymous view, one second image right behind the first):
  - the exact range, as before: the image's answer (unchanged paths);
  - the range host-page rounded: now the image's;
  - one host page further: not the image's (as before); a range in the next view: not the image's (as before);
  - base in no view, base outside the window, an anonymous view with a rounded range, the switch MADEIRA_GUEST_IMAGE_WRITE=0:
    not the image's (as before).
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


a = src.index('static int ios_guest_image_is_host_data( const void *base, size_t size )')
fn = src[a:src.index('\n}\n', a) + 3]
round_size = re.search(r'#define ROUND_SIZE\(addr,size,mask\) [^\n]*', src)[0]
check('the rounded retry sits after the exact lookup failed, and only widens the end of the range by the view\'s last host page',
      'if (!(view = find_view( base, 0 ))) return 0;' in fn and
      '(char *)base + size > (char *)view->base + ROUND_SIZE( view->base, view->size, host_page_mask )) return 0;' in fn and
      fn.index('find_view( base, size )') < fn.index('find_view( base, 0 )') < fn.index('return (view->protect & SEC_IMAGE) != 0;'))
check('the window and switch gates are before it, unchanged',
      fn.index('ios_guest_image_write_enabled()') < fn.index('ios_wow_in_window( base )') < fn.index('find_view( base, size )'))
check('mprotect_exec\'s ml348 check keeps the same rounding (16 KB) for the other call site',
      '(((uintptr_t)image_base + image_size + 0x3fffULL) & ~0x3fffULL)' in src)

harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WINE_IOS 1
typedef uintptr_t UINT_PTR;
typedef uintptr_t ULONG_PTR;
typedef size_t SIZE_T;
#define SEC_IMAGE 0x01000000
#define SEC_COMMIT 0x08000000
@ROUND@
static UINT_PTR host_page_mask = 0x3fff;
struct file_view { char *base; size_t size; unsigned int protect; };
static struct file_view views[3];
static int write_enabled = 1;
static ULONG_PTR window_base = 0x7200000000ULL;
static int ios_guest_image_write_enabled(void) { return write_enabled; }
static int ios_wow_in_window(const void *a) { return (ULONG_PTR)a >= window_base && (ULONG_PTR)a < window_base + (1ULL << 32); }
static struct file_view *find_view(const void *addr, size_t size)
{
    if ((const char *)addr + size < (const char *)addr) return NULL;
    for (int i = 0; i < 3; i++)
    {
        struct file_view *v = &views[i];
        if (!v->base) continue;
        if (v->base > (const char *)addr) continue;
        if (v->base + v->size <= (const char *)addr) continue;
        if (v->base + v->size < (const char *)addr + size) return NULL;   /* size too large: wine breaks out of the walk */
        return v;
    }
    return NULL;
}
@FN@
#define CASE(name, expect, ...) do { int r = ios_guest_image_is_host_data(__VA_ARGS__); printf("%s %s: %d (want %d)\n", r == (expect) ? "ok  " : "FAIL", name, r, expect); fails += r != (expect); } while (0)
int main(void)
{
    int fails = 0;
    char *img = (char *)(window_base + 0x400000);          /* host-page aligned, 0x5000 long: ends 0x1000 into a 16 KB host page */
    views[0] = (struct file_view){ img, 0x5000, SEC_IMAGE | 0x4, };
    views[1] = (struct file_view){ img + 0x8000, 0x4000, SEC_IMAGE | 0x4 };   /* a second image view behind it, another host page */
    views[2] = (struct file_view){ img + 0x20000, 0x5000, SEC_COMMIT | 0x4 };   /* an anonymous view of the same shape */

    CASE("exact range inside the view (the old answer)", 1, img + 0x1000, 0x2000);
    CASE("exact range to the view's end", 1, img + 0x4000, 0x1000);
    CASE("host-page rounded range of the last page", 1, img + 0x4000, 0x4000);
    CASE("the whole view rounded up (0x8000)", 1, img, 0x8000);
    CASE("one host page further than the rounded end", 0, img + 0x4000, 0x8000);
    CASE("a range that runs into the next view", 0, img + 0x4000, 0x6000);
    CASE("a range starting in the second image view", 1, img + 0x8000, 0x4000);
    CASE("base in no view", 0, img + 0x10000, 0x4000);
    CASE("an anonymous view, rounded range of its last page", 0, img + 0x20000 + 0x4000, 0x4000);
    CASE("an anonymous view, exact range", 0, img + 0x20000, 0x1000);
    CASE("base outside the window", 0, (char *)0x600000000ULL, 0x1000);
    write_enabled = 0;
    CASE("MADEIRA_GUEST_IMAGE_WRITE=0", 0, img + 0x4000, 0x4000);
    return fails != 0;
}
'''
harness = harness.replace('@ROUND@', round_size).replace('@FN@', fn)
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-g', '-fsanitize=address,undefined', '-Wall', '-Wno-unused-function',
                         '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the production function compiles on the host (ASAN/UBSAN)', cc.returncode == 0, cc.stderr[-2000:])
    if cc.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        for line in r.stdout.splitlines():
            check(line[5:], line.startswith('ok'))
        check('every case passes and the run is clean', r.returncode == 0, r.stderr[-800:])

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the workflow runs this check', 'python3 tests/host/check-guest-image-tail.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
