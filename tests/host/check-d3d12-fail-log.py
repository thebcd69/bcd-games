#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[madeira-d3d12] FAILED: the D3D12 methods that refuse a call say so (round 624, agent B S15; log only).

The KINGDOM HEARTS HD 1.5+2.5 launcher (8X1X2V, 7EHABT, S5EPGT) reads a NULL command allocator and stops; no line said which method had
refused the call that should have made it. madeira_d3d12.c's silent refusals (a reset while recording, a double Close, a NULL allocator, a
command list type the layer does not implement such as BUNDLE, fence and list flags) now go through MAD_FAIL: the first 4 refusals of each
site, then every 256th, print "[madeira-d3d12] FAILED <what> -> 0x<hr> (a, b) [call #n here, #m in all]", and the same value is returned.

Checks: the logger and the macro are cut out of the source and run on the host (the per-site limit, the 256th, the shared slot when 96 sites
are used, the value returned); every site names itself and returns what it returned before; nothing else in the file calls the logger.
Needs python3 and a C compiler.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / 'madeira-d3d12/src/pe/madeira_d3d12.c').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


f0 = src.index('static void mad_fail_log(const char *what, HRESULT hr, unsigned a, unsigned b) {')
f1 = src.index('while (0)\n', f0) + len('while (0)\n')
fn = src[f0:f1]

SITES = {
    'alloc_Reset while recording': ('alloc_Reset(', 'E_FAIL'),
    'list_Close twice': ('list_Close(', 'E_FAIL'),
    'list_Reset while recording': ('list_Reset(', 'E_FAIL'),
    'list_Reset null allocator': ('list_Reset(', 'E_INVALIDARG'),
    'CreateCommandAllocator null out': ('device_CreateCommandAllocator(', 'E_INVALIDARG'),
    'CreateCommandAllocator type': ('device_CreateCommandAllocator(', 'E_NOTIMPL'),
    'CreateCommandList null': ('device_CreateCommandList(ID3D12Device *This', 'E_INVALIDARG'),
    'CreateCommandList type': ('device_CreateCommandList(ID3D12Device *This', 'E_NOTIMPL'),
    'CreateCommandList allocator type': ('device_CreateCommandList(ID3D12Device *This', 'E_INVALIDARG'),
    'CreateFence flags': ('device_CreateFence(', 'E_NOTIMPL'),
    'CreateCommandList1 flags': ('device_CreateCommandList1(', 'E_INVALIDARG'),
    'CreateCommandList1 type': ('device_CreateCommandList1(', 'E_NOTIMPL'),
}
uses = re.findall(r'MAD_FAIL\("([^"]+)", (E_[A-Z]+)', src)
check('the twelve silent refusals go through MAD_FAIL, each named once', sorted(u[0] for u in uses) == sorted(SITES), str(uses))
for what, (func, hr) in SITES.items():
    body = src[src.index('static HRESULT STDMETHODCALLTYPE ' + func):]
    body = body[:body.index('\n}\n')]
    check(f'"{what}" is in {func.rstrip("(")} and returns {hr} as before', f'MAD_FAIL("{what}", {hr},' in body)
check('nothing else calls the logger', src.count('mad_fail_log(') == 2, str(src.count('mad_fail_log(')))

harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
typedef long HRESULT; typedef long LONG;
#define E_FAIL ((HRESULT)0x80004005L)
#define E_NOTIMPL ((HRESULT)0x80004001L)
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
static int lines;
static void d3d12_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap); lines++; }
''' + fn + r'''
static HRESULT refuse(int n) { if (n >= 0) MAD_FAIL("CreateCommandAllocator type", E_NOTIMPL, 1, 0); return 0; }
static HRESULT other(void) { MAD_FAIL("list_Close twice", E_FAIL, 0, 0); }
static const char names[100][8] = { "s0" };
int main(void)
{
    int i, ret_ok = 1, before;
    for (i = 0; i < 300; i++) ret_ok &= refuse(i) == E_NOTIMPL;
    printf("--- after 300: %d lines\n", lines);
    other();
    printf("--- other: %d lines\n", lines);
    before = lines;
    for (i = 0; i < 100; i++) mad_fail_log(names[i], E_FAIL, (unsigned)i, 0);
    printf("--- 100 sites: %d new lines\n", lines - before);
    printf("--- returned %s\n", ret_ok ? "the same value" : "something else");
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the logger compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
        first = [l for l in out.splitlines() if 'CreateCommandAllocator type' in l]
        check('a BUNDLE allocator (type 1) is named with its HRESULT and arguments',
              first and first[0] == '[madeira-d3d12] FAILED CreateCommandAllocator type -> 0x80004001 (1, 0) [call #1 here, #1 in all]', out[:400])
        check('the first 4 calls of a site, then the 256th: 5 lines for 300 calls', '--- after 300: 5 lines' in out
              and '[call #256 here, #256 in all]' in out and '[call #5 here' not in out, out[-600:])
        check('another site has its own count', '--- other: 6 lines' in out and 'FAILED list_Close twice -> 0x80004005 (0, 0) [call #1 here, #301 in all]' in out, out[-400:])
        # 2 sites are taken; 94 more get their own slot; the last 6 share slot 95 with the 94th (its count goes on: #2, #3, #4 print)
        check('past 96 sites the rest share the last slot and its limit (94 + 3 lines for 100 new sites)', '--- 100 sites: 97 new lines' in out,
              out[-300:])
        check('MAD_FAIL returns the value it was given', '--- returned the same value' in out)

print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
