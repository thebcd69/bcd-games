#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A 64-bit child whose PEB copy carries no GDI table pointer gets the session's table at user32 attach (round 624; Farming Simulator 22).

Farming Simulator 22 (Epic, started through BCD Host; Discord, build 622): bcdhost.exe -> the game's launcher stub -> the game. Every
64-bit pseudo-process's PEB is a copy of the session's first PEB (loader_ios.c), bcdhost.exe never runs user32's init_user, and the stub
did; the game's copy therefore carried Peb->GdiSharedHandleTable == NULL and gdi32 faulted on its first handle (Handles[0x3a] read at
0x57e). win32u's NtUserInitializeClientPfnArrays (every user32 attach, before any game code) now publishes the session's table when the
field is NULL in a 64-bit process.

Checks: the hook runs after init_user, only in a 64-bit process (WowTebOffset 0) and only when the field is NULL; it calls the existing
publisher (whose 64-bit branch stores the session's table and returns at once before gdi_init); its line is capped; the decision is compiled
and run with fake TEBs/PEBs (NULL -> published once, set -> untouched, WoW64 -> untouched, no table yet -> left NULL).
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


src = (root / 'build/win32u-unix/class_ios.c').read_text()
fn = src[src.index('NTSTATUS WINAPI NtUserInitializeClientPfnArrays('):]
fn = fn[:fn.index('\n}\n')]
hook = fn[fn.index('#ifdef WINE_IOS\n    /* madeira-bcd (round 624, Farming Simulator 22'):fn.index('#endif', fn.index('round 624, Farming Simulator 22'))]
check('the hook runs right after init_user, before the function returns',
      fn.index('pthread_once( &init_once, init_user );') < fn.index('round 624, Farming Simulator 22') < fn.index('return STATUS_SUCCESS;'))
check('only in a 64-bit process whose PEB has no table pointer',
      'if (!NtCurrentTeb()->WowTebOffset && !NtCurrentTeb()->Peb->GdiSharedHandleTable)' in hook)
check('it calls the existing publisher and logs at most 8 lines, only when the table was published',
      'win32u_gdi_publish_shared();' in hook and 'if (NtCurrentTeb()->Peb->GdiSharedHandleTable && InterlockedIncrement( &told ) <= 8)' in hook
      and not re.search(r'GdiSharedHandleTable\s*=[^=]', hook))

harness = r'''
#include <stdio.h>
#include <stdint.h>
typedef long LONG;
typedef struct { void *GdiSharedHandleTable; } PEB;
typedef struct { LONG WowTebOffset; PEB *Peb; struct { void *UniqueProcess; } ClientId; } TEB;
static TEB *cur; static void *gdi_shared; static int published;
static TEB *NtCurrentTeb(void) { return cur; }
static void win32u_gdi_publish_shared(void)          /* the 64-bit branch of gdiobj.c's publisher */
{
    if (!gdi_shared) return;
    if (!cur->WowTebOffset) { cur->Peb->GdiSharedHandleTable = gdi_shared; published++; }
}
#define InterlockedIncrement(p) (++*(p))
#define HandleToULong(h) ((unsigned long)(uintptr_t)(h))
#define dprintf(fd, ...) (printf(__VA_ARGS__))
static void hook(void)
{
''' + hook[hook.index('    if (!NtCurrentTeb()->WowTebOffset'):] + r'''
}
int main(void)
{
    static char table[64];
    PEB p1 = { 0 }, p2 = { (void *)0x1234 }, p3 = { 0 }, p4 = { 0 };
    TEB t1 = { 0, &p1 }, t2 = { 0, &p2 }, t3 = { 0x2000, &p3 }, t4 = { 0, &p4 };
    gdi_shared = table;
    cur = &t1; hook(); hook();
    printf("null=%s\n", p1.GdiSharedHandleTable == table ? "published" : "left");
    cur = &t2; hook();
    printf("set=%s\n", p2.GdiSharedHandleTable == (void *)0x1234 ? "untouched" : "changed");
    cur = &t3; hook();
    printf("wow=%s\n", p3.GdiSharedHandleTable == 0 ? "untouched" : "changed");
    gdi_shared = 0; cur = &t4; hook();
    printf("before-init=%s published=%d\n", p4.GdiSharedHandleTable == 0 ? "left" : "set", published);
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 't.c'
    c.write_text(harness)
    exe = Path(tmp) / 't'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-O1', '-Wall', '-Wno-unused-function', '-o', str(exe), str(c)], capture_output=True, text=True)
    check('the decision compiles on the host (-Wall)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-1500:])
    if cc.returncode == 0:
        out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
        check('a 64-bit PEB with no pointer gets the table, once (a second attach finds it set)', 'null=published' in out and 'published=1' in out, out)
        check('a PEB that has the pointer, a WoW64 process, and a session whose table is not made yet are left alone',
              'set=untouched' in out and 'wow=untouched' in out and 'before-init=left' in out, out)
        check('the line names the process and the table', out.count('[gdi-shared] peb=') == 1 and 'published 0x' in out, out)

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-gdi-table-child.py' in wf)
print('PASS' if ok else 'FAIL')
raise SystemExit(0 if ok else 1)
