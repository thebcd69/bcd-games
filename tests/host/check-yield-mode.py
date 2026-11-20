#!/usr/bin/env python3
"""bcd.cfg yield-mode (tools/patch-wine-yield-mode.py): the yield primitive
behind NtYieldExecution (SwitchToThread, Sleep(0), every non-alertable Sleep).

Checks:
  1. the patch adds the madeira_yield helper before NtYieldExecution and
     replaces only its sched_yield() call (iOS only); undoing both edits gives
     back the source; a second run changes nothing; a moved anchor fails;
  2. NtYieldExecution compiled on the host from the stock and the patched text
     (the Mach call and the config read are stand-ins):
       - unset (and any unknown value): sched_yield() on every yield, exactly as
         the stock function does, and the adaptive sleep after yield-streak
         back-to-back yields is unchanged;
       - switch: thread_switch(MACH_PORT_NULL, SWITCH_OPTION_NONE, 0);
       - depress: thread_switch(MACH_PORT_NULL, SWITCH_OPTION_DEPRESS, ms) with
         yield-depress-ms clamped to 1..10 (default 1);
  3. CI applies the patch before ntdll-unix is built and runs this check; the
     catalog documents both keys; no game list sets them.
Needs the wine submodule checked out and `cc` on PATH. In CI this runs after
the patch step, so a patched sync.c is first restored to the stock text.
"""
from pathlib import Path
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
script = root / 'tools/patch-wine-yield-mode.py'
spec = importlib.util.spec_from_file_location('yieldmode', script)
ym = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ym)
src_path = root / 'wine/dlls/ntdll/unix/sync.c'
if not src_path.is_file():
    print('FAIL: wine/dlls/ntdll/unix/sync.c missing (check out the wine submodule)')
    sys.exit(1)
cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
failures = []


def check(cond, what):
    print(('PASS: ' if cond else 'FAIL: ') + what)
    if not cond:
        failures.append(what)


# build 666 (pull request #55): CI applies tools/patch-wine-overlapped-system-apc.py to the same file after this patch (its helper before
# NtYieldExecution's comment block and one call at the top of its body); the stock text is the file with both taken out.
_ovl_spec = importlib.util.spec_from_file_location('overlapped', root / 'tools/patch-wine-overlapped-system-apc.py')
_ovl = importlib.util.module_from_spec(_ovl_spec)
_ovl_spec.loader.exec_module(_ovl)
_OVL_CALL = 'NTSTATUS WINAPI NtYieldExecution(void)\n{\n#ifdef WINE_IOS\n    ios_yield_system_apcs();\n#endif'


def unpatch_overlapped(text):
    return text.replace(_ovl.NATIVE, '').replace(_OVL_CALL, 'NTSTATUS WINAPI NtYieldExecution(void)\n{')


def unpatch(text):
    return text.replace(ym.HELPER + ym.HEAD, ym.HEAD).replace(ym.YIELD_NEW, ym.YIELD_OLD)


current = unpatch_overlapped(src_path.read_text())
stock = unpatch(current) if ym.MARKER in current else current
patched = ym.patch(stock)
check(patched != stock and patched.count(ym.MARKER) >= 1, 'the patch applies to the submodule\'s unix sync.c')
check(unpatch(patched) == stock, 'undoing the helper and the call site gives back the source byte for byte')
check(ym.patch(patched) == patched, 'a second run changes nothing')
try:
    ym.patch(stock.replace('    sched_yield();\n#ifdef RUSAGE_THREAD', '    sched_yield( );\n#ifdef RUSAGE_THREAD'))
    moved = False
except ValueError as e:
    moved = 'yield-mode anchor' in str(e)
check(moved, 'a moved anchor stops the patch with an error')
with tempfile.TemporaryDirectory(prefix='madeira-yield-mode-') as d:
    p = Path(d) / 'sync.c'
    p.write_text(stock)
    r1 = subprocess.run([sys.executable, str(script), str(p)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(script), str(p)], capture_output=True, text=True)
    check(r1.returncode == 0 and 'patched' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout
          and p.read_text() == patched, 'the command line patches once, then reports "already patched"')

fn = patched[patched.index('NTSTATUS WINAPI NtYieldExecution(void)'):]
fn = fn[:fn.index('\n}\n') + 3]
check('#ifdef WINE_IOS\n    madeira_yield();' in fn and '#else\n    sched_yield();\n#endif' in fn,
      'only the iOS build changes: its sched_yield() call becomes madeira_yield()')
helper = ym.HELPER
check('case 1: thread_switch( MACH_PORT_NULL, SWITCH_OPTION_NONE, 0 ); break;' in helper and
      'case 2: thread_switch( MACH_PORT_NULL, SWITCH_OPTION_DEPRESS, madeira_yield_depress_ms ); break;' in helper and
      'default: sched_yield(); break;' in helper, 'the three primitives')

HARNESS = r'''
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
typedef int NTSTATUS;
typedef unsigned int mach_msg_timeout_t;
#define WINAPI
#define STATUS_SUCCESS 0
#define STATUS_NO_YIELD_PERFORMED 0x40000024
#define MACH_PORT_NULL 0
#define SWITCH_OPTION_NONE 0
#define SWITCH_OPTION_DEPRESS 1
#define WINE_IOS 1
#define HAVE_SCHED_YIELD 1
#undef RUSAGE_THREAD
#define IOS_ECO_POLL() do { } while (0)
static long n_sched, n_switch_none, n_switch_depress, n_sleep, last_ms, last_usleep;
static int stub_sched_yield(void) { n_sched++; return 0; }
static int thread_switch( unsigned name, int option, mach_msg_timeout_t ms )
{
    if (name) abort();
    if (option == SWITCH_OPTION_NONE && !ms) n_switch_none++;
    else if (option == SWITCH_OPTION_DEPRESS) { n_switch_depress++; last_ms = ms; }
    else abort();
    return 0;
}
static int stub_usleep( useconds_t us ) { n_sleep++; last_usleep = us; return 0; }
#define sched_yield stub_sched_yield
#define usleep stub_usleep
static int madeira_cfg_get( const char *key, char *out, size_t cap )
{
    char name[64]; const char *v;
    snprintf( name, sizeof(name), "CFG_%s", key );
    for (char *c = name; *c; c++) if (*c == '-') *c = '_';
    if (!(v = getenv( name ))) return 0;
    snprintf( out, cap, "%s", v );
    return 1;
}
static long long madeira_cfg_int( const char *key, long long dflt )
{
    char v[32];
    return madeira_cfg_get( key, v, sizeof(v) ) ? atoll( v ) : dflt;
}
static volatile long long ios_xp_yield, ios_xp_yield_slept;
#include "fn.c"
int main( int argc, char **argv )
{
    int i, n = argc > 1 ? atoi( argv[1] ) : 100, gap_us = argc > 2 ? atoi( argv[2] ) : 0;
    for (i = 0; i < n; i++)
    {
        if (gap_us) { struct timespec t = { 0, gap_us * 1000L }; nanosleep( &t, NULL ); }
        NtYieldExecution();
    }
    printf( "sched=%ld none=%ld depress=%ld ms=%ld sleep=%ld us=%ld yields=%lld slept=%lld\n",
            n_sched, n_switch_none, n_switch_depress, last_ms, n_sleep, last_usleep, ios_xp_yield, ios_xp_yield_slept );
    return 0;
}
'''


def build(folder, kind, text):
    body = text[text.index('NTSTATUS WINAPI NtYieldExecution(void)'):]
    body = body[:body.index('\n}\n') + 3]
    if kind == 'patched':
        h = ym.HELPER.replace('#include <mach/mach.h>\n', '').replace('#include <mach/thread_switch.h>\n', '')
        body = h + body
    sub = folder / kind
    sub.mkdir()
    (sub / 'fn.c').write_text(body)
    (sub / 'h.c').write_text(HARNESS)
    exe = sub / 'run'
    r = subprocess.run([cc, '-std=gnu11', '-O1', '-w', '-I', str(sub), str(sub / 'h.c'), '-o', str(exe)],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:])
    return exe if r.returncode == 0 else None


def run(exe, env, n, gap):
    e = {k: v for k, v in os.environ.items() if not k.startswith('CFG_')}
    e.update(env)
    r = subprocess.run([str(exe), str(n), str(gap)], capture_output=True, text=True, env=e, timeout=120)
    m = re.search(r'sched=(\d+) none=(\d+) depress=(\d+) ms=(\d+) sleep=(\d+) us=(\d+) yields=(\d+) slept=(\d+)', r.stdout)
    return tuple(int(x) for x in m.groups()) if m else None


with tempfile.TemporaryDirectory(prefix='madeira-yield-mode-run-') as d:
    folder = Path(d)
    se, pa = build(folder, 'stock', stock), build(folder, 'patched', patched)
    check(se is not None and pa is not None, 'the stock and the patched NtYieldExecution compile on the host')
    if se and pa:
        # 200 yields 3 ms apart: no streak, every one reaches the primitive
        s, p = run(se, {}, 200, 3000), run(pa, {}, 200, 3000)
        check(s == p == (200, 0, 0, 0, 0, 0, 200, 0), 'unset: sched_yield() on every yield, as the stock code (%s, %s)' % (s, p))
        p = run(pa, {'CFG_yield_mode': 'bogus'}, 50, 3000)
        check(p == (50, 0, 0, 0, 0, 0, 50, 0), 'an unknown yield-mode keeps sched_yield() (%s)' % (p,))
        p = run(pa, {'CFG_yield_mode': 'pri'}, 50, 3000)
        check(p == (50, 0, 0, 0, 0, 0, 50, 0), 'yield-mode = pri is sched_yield() (%s)' % (p,))
        p = run(pa, {'CFG_yield_mode': 'switch'}, 200, 3000)
        check(p == (0, 200, 0, 0, 0, 0, 200, 0), 'switch: thread_switch(SWITCH_OPTION_NONE) on every yield (%s)' % (p,))
        p = run(pa, {'CFG_yield_mode': 'depress'}, 200, 3000)
        check(p == (0, 0, 200, 1, 0, 0, 200, 0), 'depress: thread_switch(SWITCH_OPTION_DEPRESS, 1 ms) by default (%s)' % (p,))
        p = run(pa, {'CFG_yield_mode': 'depress', 'CFG_yield_depress_ms': '4'}, 20, 3000)
        check(p and p[2] == 20 and p[3] == 4, 'yield-depress-ms = 4 bounds the drop at 4 ms (%s)' % (p,))
        p1 = run(pa, {'CFG_yield_mode': 'depress', 'CFG_yield_depress_ms': '50'}, 5, 3000)
        p2 = run(pa, {'CFG_yield_mode': 'depress', 'CFG_yield_depress_ms': '0'}, 5, 3000)
        check(p1 and p1[3] == 10 and p2 and p2[3] == 1, 'yield-depress-ms is clamped to 1..10 (%s, %s)' % (p1, p2))
        # 400 back-to-back yields with yield-streak 16: the adaptive sleep, unchanged, in every mode
        s = run(se, {'CFG_yield_streak': '16', 'CFG_yield_sleep_us': '70'}, 400, 0)
        p = run(pa, {'CFG_yield_streak': '16', 'CFG_yield_sleep_us': '70'}, 400, 0)
        q = run(pa, {'CFG_yield_streak': '16', 'CFG_yield_sleep_us': '70', 'CFG_yield_mode': 'switch'}, 400, 0)
        check(s and s == p and s[0] == 17 and s[4] == 383 and s[5] == 70,
              'the adaptive sleep after yield-streak back-to-back yields is unchanged (%s, %s)' % (s, p))
        check(q and q[1] == 17 and q[4] == 383, 'and comes first in the other modes too (%s)' % (q,))

ci = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-yield-mode.py wine/dlls/ntdll/unix/sync.c'
check(step in ci and ci.index('python3 tools/patch-wine-lock-orphan.py') < ci.index(step) < ci.index('- name: Build ntdll-unix'),
      'CI patches unix sync.c before ntdll-unix is built')
check('- name: Check yield-mode\n        run: python3 tests/host/check-yield-mode.py' in ci and
      ci.index('- name: Check quiet-log') < ci.index('- name: Check yield-mode'), 'CI runs this check after quiet-log')
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
gen = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('"yield-mode"' in cat and '"yield-depress-ms"' in cat and 'key: "yield-mode"' in gen and
      'key: "yield-depress-ms"' in gen, 'the catalog documents yield-mode and yield-depress-ms')
rec = (root / 'recommended/recommendations.json').read_text()   # the game lists (2026-10-18)
check('yield-mode' not in rec, 'no game list sets it')

if failures:
    print('FAILED: %d check(s)' % len(failures))
    sys.exit(1)
print('all yield-mode checks passed')
