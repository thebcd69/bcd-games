#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-lock-orphan-default.py: the orphan-lock reaper's stamped mode is the default (round 660, M030/M141; owner 2026-11-15).

Checks: (a) the workflow checks and applies it after the opt-in lock-orphan script and the fault-safe waiter reads; (b) on the submodule's
sync.c with those two applied (as the build has it; a tree that already carries this script's edit is undone first) it applies once
("already patched" the second time), refuses a file without the lock-orphan script and a moved anchor, writing nothing, and changes only
the mode selection and its log line; (c) the patched ios_orphan_check, compiled against stubs and run as the monitor would: unset gives
stamped (a lock nobody stamped is never released; a stamped one whose holder vanished is released on the 3rd cycle) and says so once;
"legacy" is the reaper as it was (released after ONE call with 3 waiters); "stamped" and any other value are stamped; "0" releases nothing.
"""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), root / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wf = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-lock-orphan-default.py wine/dlls/ntdll/unix/sync.c'
check('the workflow checks and applies it after the opt-in lock-orphan script and the waiter reads',
      'python3 tests/host/check-lock-orphan-default.py\n          ' + step in wf
      and wf.index('python3 tools/patch-wine-lock-orphan.py wine/dlls/ntdll/unix/sync.c') < wf.index(step)
      and wf.index('python3 tools/patch-wine-waiter-read.py wine/dlls/ntdll/unix/sync.c') < wf.index(step))

sync = root / 'wine/dlls/ntdll/unix/sync.c'
if not sync.is_file():
    print('SKIP: no wine sources (check out the submodule)')
    print('PASS' if ok else 'FAILED: lock-orphan default')
    sys.exit(0 if ok else 1)

d = load('patch-wine-lock-orphan-default')
orphan = load('patch-wine-lock-orphan')
waiter = load('patch-wine-waiter-read')
start = sync.read_text().replace(d.NEW, d.OLD)          # a re-run's tree: this script's edit undone
base = orphan.patch(waiter.patch(start))                 # both idempotent: the build's tree already has them
patched = d.patch(base)
check('applies once and is idempotent', patched != base and d.patch(patched) == patched and patched.count(d.MARKER) == 1)
check('only the mode selection and its log line change (undoing them gives the file before)', patched.replace(d.NEW, d.OLD) == base)
try:
    d.patch(start.replace(d.NEEDS, 'madeira-bcd not yet') if d.NEEDS in start else waiter.patch(start))
    refused = False
except ValueError as e:
    refused = 'patch-wine-lock-orphan.py' in str(e)
check('refused without the lock-orphan script', refused)
try:
    d.patch(base.replace(d.OLD, d.OLD.replace('? 2 : 0;', '? 2 : 0; /* moved */')))
    refused = False
except ValueError as e:
    refused = 'moved' in str(e)
check('refused when the anchor moved', refused)
with tempfile.TemporaryDirectory(prefix='bcd-lock-orphan-default-') as t:
    f = Path(t) / 'sync.c'
    f.write_text(base)
    r1 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-lock-orphan-default.py'), str(f)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-lock-orphan-default.py'), str(f)], capture_output=True, text=True)
    check('the script applies, then says it is already patched',
          r1.returncode == 0 and 'applied' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout and f.read_text() == patched,
          r1.stdout + r1.stderr + r2.stdout)
    moved = Path(t) / 'moved.c'
    moved.write_text(base.replace('orphan_mode = !e ? 0', 'orphan_mode = !e ?  0'))
    r = subprocess.run([sys.executable, str(root / 'tools/patch-wine-lock-orphan-default.py'), str(moved)], capture_output=True, text=True)
    check('a moved anchor is refused and nothing is written', r.returncode != 0 and d.MARKER not in moved.read_text(), r.stdout + r.stderr)


def cut(text, start_, end):
    i = text.index(start_)
    return text[i:text.index(end, i) + len(end)]


waiters = cut(patched, '#define IOS_ALERT_WAITER_MAX 512', '} ios_alert_waiters[IOS_ALERT_WAITER_MAX];')
body = cut(patched, 'void ios_orphan_check( const unsigned long long *live_stamps, int nstamps )', '\n}\n')
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef uintptr_t ULONG_PTR; typedef unsigned long long ULONGLONG;
#define MS_ASYNC 1
static int msync( void *p, size_t n, int f ) { (void)p; (void)n; (void)f; return 0; }
#define dprintf(fd, ...) printf(__VA_ARGS__)
static unsigned long long reaped[16]; static int nreaped;
static void ios_srw_reap_exclusive( unsigned long long lock, unsigned long long teb )
{
    (void)teb; reaped[nreaped++] = lock;
    *(volatile unsigned int *)(uintptr_t)lock = 0x00000008;   /* released, waiters left */
}
''' + waiters + '\n' + body + r'''
#define FAIL(...) do { fprintf(stderr, __VA_ARGS__); exit(1); } while (0)
#include <sys/mman.h>
static unsigned int *word;
static unsigned long long park( int nwait )
{
    unsigned long long lock = (unsigned long long)(uintptr_t)&word[64];
    int i;
    word[64] = 0x00010001u | (unsigned)(nwait << 1);
    for (i = 0; i < IOS_ALERT_WAITER_MAX; i++) ios_alert_waiters[i].addr = NULL;
    for (i = 0; i < nwait; i++) ios_alert_waiters[i].addr = (void *)(uintptr_t)(lock + 2);
    return lock;
}
int main( int argc, char **argv )
{
    const char *scenario = argv[1];
    unsigned long long none = 0, lock;
    int call;
    (void)argc;
    word = mmap( (void *)(uintptr_t)0x200000000ull, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0 );
    if (word == MAP_FAILED || (uintptr_t)word >= 0x8000000000ull) FAIL( "no low page for the lock\n" );
    if (!strcmp( scenario, "old" ))
    {
        lock = park( 3 );
        ios_orphan_check( &none, 0 );
        if (nreaped != 1) FAIL( "legacy: %d releases after one call\n", nreaped );
    }
    else if (!strcmp( scenario, "unstamped" ))
    {
        lock = park( 4 );
        for (call = 0; call < 20; call++) ios_orphan_check( &none, 0 );
        if (nreaped) FAIL( "stamped released a lock nobody stamped\n" );
    }
    else if (!strcmp( scenario, "orphan" ))
    {
        lock = park( 3 );
        ios_orphan_check( &lock, 1 );
        for (call = 1; call <= 3; call++)
        {
            ios_orphan_check( &none, 0 );
            if (call < 3 && nreaped) FAIL( "released after %d cycles\n", call );
        }
        if (nreaped != 1 || reaped[0] != lock) FAIL( "not released on the 3rd cycle\n" );
    }
    else if (!strcmp( scenario, "off" ))
    {
        lock = park( 5 );
        ios_orphan_check( &lock, 1 );
        for (call = 0; call < 10; call++) ios_orphan_check( &none, 0 );
        if (nreaped) FAIL( "0 released a lock\n" );
    }
    printf( "done %s\n", scenario );
    return 0;
}
'''
cc = os.environ.get('CC', 'cc')
if not shutil.which(cc):
    print('SKIP: no C compiler')
    sys.exit(0 if ok else 1)
with tempfile.TemporaryDirectory(prefix='bcd-orphan-default-') as t:
    c = Path(t) / 'o.c'
    exe = Path(t) / 'o'
    c.write_text(harness)
    b = subprocess.run([cc, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', str(c), '-o', str(exe)], capture_output=True, text=True)
    check('the patched reaper compiles (-Wall -Werror)', b.returncode == 0, b.stderr[-2000:])
    if b.returncode == 0:
        def run(scenario, value):
            env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_LOCK_ORPHAN'}
            if value is not None:
                env['MADEIRA_LOCK_ORPHAN'] = value
            r = subprocess.run([str(exe), scenario], capture_output=True, text=True, env=env)
            return r.returncode == 0, r.stdout + r.stderr
        for what, scenario, value, said in (
                ('unset: stamped, a lock nobody stamped is never released', 'unstamped', None, 'stamped by default'),
                ('unset: stamped, a stamped lock whose holder vanished goes on the 3rd cycle', 'orphan', None, 'stamped by default'),
                ('legacy: the reaper as before build 663 (one call, 3 waiters)', 'old', 'legacy', 'the reaper as before build 663'),
                ('stamped: as before', 'unstamped', 'stamped', 'MADEIRA_LOCK_ORPHAN=stamped: only locks a live thread has stamped'),
                ('any other value is stamped', 'unstamped', '1', 'MADEIRA_LOCK_ORPHAN=1: only locks a live thread has stamped'),
                ('0: nothing is released', 'off', '0', 'no orphan-lock releases')):
            passed, out = run(scenario, value)
            check(what + ', and the start says which mode runs, once', passed and out.count('[lock-orphan] madeira-bcd') == 1 and said in out, out)

print('PASS' if ok else 'FAILED: lock-orphan default')
sys.exit(0 if ok else 1)
