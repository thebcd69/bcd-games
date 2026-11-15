#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""tools/patch-wine-guest-log-ring.py: the [guest-log] mirror remembers its last 16 lines, keeps a JVM fatal-error report and reads
console.txt (round 660, M032; log only).

Checks: (a) the workflow checks it and applies it right after the sign-in-value script and before the file trace; (b) on a copy of the
Wine fork's file.c, the mirror scripts in the workflow's order (base, tag, service, Ren'Py, trace, account names, sign-in values) and then
this one apply, this one once ("already patched" the second time); it is refused before the sign-in-value script and when a line it
rewrites moved, writing nothing; undoing its four edits gives the file before back; every later file.c script still applies; (c) the
whole patched mirror is compiled on the host (-Wall, AddressSanitizer) and driven with real files: an exception and its stack line written
three times alternately come out once each; the lines after a JVM fatal-error header are kept in the keyword mode though they hold none of
the words; console.txt is mirrored and settings.txt is not.
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
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


spec = importlib.util.spec_from_file_location('ring_patch', root / 'tools/patch-wine-guest-log-ring.py')
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)
ring = root / 'tools/patch-wine-guest-log-ring.py'

wf = (root / '.github/workflows/build-ipa.yml').read_text()
step = 'python3 tools/patch-wine-guest-log-ring.py wine/dlls/ntdll/unix/file.c'
check('the workflow checks it and applies it right after the sign-in-value script, before the file trace',
      'python3 tests/host/check-guest-log-ring.py\n          ' + step in wf
      and wf.index('python3 tools/patch-wine-guest-log-secrets.py wine/dlls/ntdll/unix/file.c') < wf.index(step)
      < wf.index('python3 tools/patch-wine-file-trace.py wine/dlls/ntdll/unix/file.c'))
order = [m.group(1) for m in re.finditer(r'python3 tools/(patch-wine-[a-z-]+)\.py wine/dlls/ntdll/unix/file\.c', wf)]
check('patch-wine-guest-log.py no longer rewrites the mirror (the round 660 changes are this script\'s)',
      'UPGRADE_660' not in (root / 'tools/patch-wine-guest-log.py').read_text())

real = root / 'wine/dlls/ntdll/unix/file.c'
if not real.is_file():
    print('SKIP: no wine sources (check out the submodule)')
    print('PASS' if ok else 'FAIL')
    raise SystemExit(0 if ok else 1)

with tempfile.TemporaryDirectory(prefix='bcd-guest-log-ring-') as td:
    t = Path(td)
    f = t / 'file.c'
    start = real.read_text()
    # In the build the step's own check runs on the wine tree the earlier mirror steps patched (and a re-run may find this script's
    # edits too): undo this script's edits so the run below starts from what the chain before it leaves.
    for _what, old_, new_ in rp.EDITS:
        start = start.replace(new_, old_)
    f.write_text(start)
    before_secrets = None
    for s_ in order[:order.index('patch-wine-guest-log-ring')]:
        if s_ == 'patch-wine-guest-log-secrets':
            before_secrets = f.read_text()
        r = subprocess.run([sys.executable, str(root / 'tools' / (s_ + '.py')), str(f)], capture_output=True, text=True)
        check(f'{s_} applies to the fork\'s file.c', r.returncode == 0, r.stdout + r.stderr)
    # A file without the sign-in-value script's marker (in the build the tree has it already, so it is taken out of a copy).
    early = t / 'early.c'
    early_text = (before_secrets or '').replace('madeira-bcd: [guest-log] secrets redacted', 'madeira-bcd: [guest-log] not yet')
    early.write_text(early_text)
    r = subprocess.run([sys.executable, str(ring), str(early)], capture_output=True, text=True)
    check('refused before the sign-in-value script, nothing written',
          r.returncode != 0 and 'secrets' in (r.stdout + r.stderr) and early.read_text() == early_text, r.stdout + r.stderr)
    before = f.read_text()
    for what, old_, new_ in rp.EDITS:
        moved = t / 'moved.c'
        moved.write_text(before.replace(old_, old_[:-1] + ' /* moved */\n'))   # every anchor ends its line
        r = subprocess.run([sys.executable, str(ring), str(moved)], capture_output=True, text=True)
        check(f'a moved line ({what}) is refused by name and nothing is written',
              r.returncode != 0 and what in (r.stdout + r.stderr) and rp.MARKER not in moved.read_text(), r.stdout + r.stderr)
    r1 = subprocess.run([sys.executable, str(ring), str(f)], capture_output=True, text=True)
    r2 = subprocess.run([sys.executable, str(ring), str(f)], capture_output=True, text=True)
    after = f.read_text()
    check('applies once, then says it is already patched',
          r1.returncode == 0 and 'applied' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
    undone = after
    for what, old_, new_ in rp.EDITS:
        undone = undone.replace(new_, old_)
    check('only its four edits change the file (undoing them gives the file before back)', undone == before and after != before)
    later = t / 'later.c'
    later.write_text(after)
    bad = []
    for s_ in order[order.index('patch-wine-guest-log-ring') + 1:]:
        r = subprocess.run([sys.executable, str(root / 'tools' / (s_ + '.py')), str(later)], capture_output=True, text=True)
        if r.returncode:
            bad.append(s_ + ': ' + (r.stdout + r.stderr).strip()[-200:])
    check('every later file.c script of the workflow still applies', not bad, bad)

    # (c) the whole patched mirror, compiled and driven with real files
    region = after[after.index('/* guest-log-test:begin'):after.index('/* guest-log-test:end */')]
    harness = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName; } RTL_USER_PROCESS_PARAMETERS;
typedef struct { RTL_USER_PROCESS_PARAMETERS *ProcessParameters; } PEB;
typedef struct { PEB *Peb; } TEB;
static RTL_USER_PROCESS_PARAMETERS pp_;
static PEB peb_ = { &pp_ };
static TEB teb_ = { &peb_ };
#define NtCurrentTeb() (&teb_)
static unsigned int GetCurrentThreadId(void) { return 0x24; }
''' + region + r'''
static void put( int fd, const char *s ) { ios_guest_log_mirror( fd, s, (unsigned)strlen( s ) ); }
static int open_file( const char *dir, const char *name )
{
    char p[512];
    snprintf( p, sizeof(p), "%s/%s", dir, name );
    return open( p, O_CREAT | O_TRUNC | O_WRONLY, 0644 );
}
int main( int argc, char **argv )
{
    int log = open_file( argv[1], "Player.log" ), con = open_file( argv[1], "console.txt" ), hs = open_file( argv[1], "hs_err_pid42.log" );
    int txt = open_file( argv[1], "settings.txt" ), k;
    (void)argc;
    for (k = 0; k < 3; k++) put( log, "NullReferenceException: Object reference not set\n  at Interaction_Follower.Update ()\n" );
    put( hs, "# A fatal error has been detected by the Java Runtime Environment:\n#\n#  EXCEPTION_ACCESS_VIOLATION (0xc0000005) at pc=0x780ba14ba8\n"
             "# Problematic frame:\n# j  zombie.core.Core.something()\n" );
    put( con, "LOG  : General, 1731000000000> error: something broke\n" );
    put( txt, "error in a text file that is not a log\n" );
    return 0;
}
'''
    hc = t / 'm.c'
    hc.write_text(harness)
    mexe = t / 'm'
    cc = subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(mexe), str(hc)],
                        capture_output=True, text=True)
    check('the whole patched mirror compiles on the host (-Wall, AddressSanitizer, no warning)', cc.returncode == 0 and 'warning' not in cc.stderr, cc.stderr[-2000:])
    if cc.returncode == 0:
        def run(mode):
            d = tempfile.mkdtemp(dir=t)
            env = {k: v for k, v in os.environ.items() if not k.startswith('MADEIRA_GUEST_LOG')}
            if mode:
                env['MADEIRA_GUEST_LOG'] = mode
            r = subprocess.run([str(mexe), d], capture_output=True, text=True, env=env)
            check(f'the run ends cleanly (mode {mode or "default"}, AddressSanitizer)', r.returncode == 0, r.stderr[-1500:])
            return r.stderr.splitlines()
        lines = run('')
        body = [re.sub(r'^\[guest-log\] #\d+ tid=0024 ', '', l) for l in lines if re.match(r'\[guest-log\] #\d+', l)]
        check('keyword mode: the exception written three times between its stack lines comes out once',
              body.count('NullReferenceException: Object reference not set') == 1, body)
        check('keyword mode: the lines after a JVM fatal-error header are kept, though they hold none of the words',
              '# Problematic frame:' in body and '# j  zombie.core.Core.something()' in body, body)
        check('console.txt is mirrored (and named once), settings.txt is not',
              'LOG  : General, 1731000000000> error: something broke' in body and not any('not a log' in l for l in body)
              and any(l.startswith('[guest-log] file ') and l.endswith('/console.txt') for l in lines), lines)
        lines = run('all')
        body = [re.sub(r'^\[guest-log\] #\d+ tid=0024 ', '', l) for l in lines if re.match(r'\[guest-log\] #\d+', l)]
        check('every-line mode: the alternating pair comes out once each (the one-line memory printed it three times)',
              body.count('NullReferenceException: Object reference not set') == 1 and body.count('  at Interaction_Follower.Update ()') == 1, body)

print('PASS' if ok else 'FAILED: guest-log ring')
sys.exit(0 if ok else 1)
