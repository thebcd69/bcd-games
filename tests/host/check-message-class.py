#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The built-in Message window class in every pseudo-process, with env.MADEIRA_MESSAGE_CLASS = 1 (round 630).

Upstream's init_user registers the Message class (register_desktop_class) in every process. In BCD every pseudo-process of a
session shares one win32u, so init_user runs once, for the first one, and register_builtin_classes registers only the
controls per pseudo-process (build/win32u-unix/class_ios.c). In every later program CreateWindow(L"Message", ...,
HWND_MESSAGE) fails with ERROR_INVALID_HANDLE: winmm's devices thread exits at once and waveOutOpen answers
MMSYSERR_NODRIVER (FINAL FANTASY VII's launcher 03ZW5E: 486 such threads; Messiah J0R6MM: 2,967).

Compiles the production ios_message_class_on and register_builtin_classes with recording stubs and checks, in a fresh process
for each value of the key:
  - unset, 0 or anything else: each (pid, PEB) gets the controls once and no Message class (as before);
  - 1, on, true, yes: each (pid, PEB) gets the controls and the Message class once, the session's first process too (the
    server's CLASS_ALREADY_EXISTS for it is tolerated by register_builtin), and the log line says so;
plus source checks of init_user, register_desktop_class, the descriptor, the catalog, the list policy and CI.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/win32u-unix/class_ios.c').read_text()
fails = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        fails.append(what)


def function(signature):
    start = source.index(signature)
    end = source.index('\n}\n', start) + 3
    return source[start:end]


descr = source[source.index('static const struct builtin_class_descr message_builtin_class ='):]
descr = descr[:descr.index('};') + 2]
on = function('static int ios_message_class_on(void)')
per_process = function('void register_builtin_classes(void)')
desktop = function('void register_desktop_class(void)')
init_user = function('static void init_user(void)')

check('the descriptor is the built-in Message class', '.name = "Message"' in descr and '.proc = NTUSER_WNDPROC_MESSAGE' in descr)
check('init_user (once per session) still registers the desktop and Message classes', 'register_desktop_class();' in init_user
      and 'register_builtin( &desktop_builtin_class );' in desktop and 'register_builtin( &message_builtin_class );' in desktop)
i_builtins = per_process.index('register_builtins();')
i_message = per_process.index('if (ios_message_class_on()) register_builtin( &message_builtin_class );')
i_done = per_process.index('if (done_count < ARRAYSIZE(done))')
check('the Message class is registered inside the per-(pid, PEB) block, after the controls, before the bookkeeping',
      i_builtins < i_message < i_done and per_process.index('pthread_mutex_lock( &builtin_lock );') < i_builtins)
check('the key is read once, with madeira_cfg_bool\'s words for on',
      'static int on = -1;' in on and 'getenv( "MADEIRA_MESSAGE_CLASS" )' in on
      and '!strcmp( v, "1" ) || !strcmp( v, "on" ) || !strcmp( v, "true" ) || !strcmp( v, "yes" )' in on)
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the key (bool, default 0)',
      re.search(r'"env\.MADEIRA_MESSAGE_CLASS": \{[^}]*?"kind": "bool", "default": "0"', cat, re.S) is not None)
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
allowed = policy.split('static let allowedEnvNames')[1].split('\n    ]\n')[0]   # the set ends with its own line (comments hold brackets)
check('a downloaded list may set it, 0 or 1 only', '"MADEIRA_MESSAGE_CLASS"' in allowed
      and 'case "MADEIRA_MESSAGE_CLASS":' in policy and 'MADEIRA_MESSAGE_CLASS is 0 or 1' in policy)
rules = (root / 'build/tools/autotune_rules.py').read_text()
check('auto-tune offers the line on the devices-thread churn only (50 failed exits), not once the class is registered',
      '"lines": ["env.MADEIRA_MESSAGE_CLASS = 1"]' in rules
      and 's("status=00000001 caller=", "[thr-exit] ml806 RtlExitUserThread", count=50)' in rules
      and 's("+ Message (MADEIRA_MESSAGE_CLASS)")' in rules)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-message-class.py' in wf)

code = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

typedef unsigned int DWORD;
struct builtin_class_descr { const char *name; unsigned int style; int proc; int extra; const void *cursor; void *brush; };
enum { NTUSER_WNDPROC_MESSAGE = 7 };
struct fake_teb { struct { void *UniqueProcess; } ClientId; void *Peb; };
static struct fake_teb cur;
static struct fake_teb *NtCurrentTeb( void ) { return &cur; }
#define HandleToULong(h) ((DWORD)(uintptr_t)(h))
#define ARRAYSIZE(a) (sizeof(a) / sizeof((a)[0]))

static int controls[0x200], message[0x200];
static void register_builtins( void ) { controls[HandleToULong( cur.ClientId.UniqueProcess )]++; }
static void register_builtin( const struct builtin_class_descr *d )
{
    assert( !strcmp( d->name, "Message" ) && d->proc == NTUSER_WNDPROC_MESSAGE );
    message[HandleToULong( cur.ClientId.UniqueProcess )]++;
}
'''
code += descr + '\n' + on + '\n' + per_process + r'''
static void as( DWORD pid, uintptr_t peb ) { cur.ClientId.UniqueProcess = (void *)(uintptr_t)pid; cur.Peb = (void *)peb; }

int main( int argc, char **argv )
{
    int want = argc > 1 && !strcmp( argv[1], "on" );
    as( 0x20, 0x71ffff0000 ); register_builtin_classes(); register_builtin_classes();   /* the session's first process */
    as( 0xb4, 0x7900310000 ); register_builtin_classes(); register_builtin_classes();   /* FF7's launcher, started later */
    as( 0xe0, 0x7900330000 ); register_builtin_classes();
    assert( controls[0x20] == 1 && controls[0xb4] == 1 && controls[0xe0] == 1 );
    assert( message[0x20] == want && message[0xb4] == want && message[0xe0] == want );
    printf( "ok: controls once per process; Message class %s\n", want ? "once per process" : "never (as before)" );
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-message-class-') as directory:
    folder = Path(directory)
    src = folder / 'check.c'
    src.write_text(code)
    exe = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-g', '-pthread', str(src), '-o', str(exe)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode == 0:
        print('built with AddressSanitizer/UBSan')
    else:
        r = subprocess.run(flags, capture_output=True, text=True)
        check('the harness builds: ' + r.stderr[-1500:], r.returncode == 0)
    if exe.exists():
        for value, want in ((None, 'off'), ('0', 'off'), ('2', 'off'), ('1', 'on'), ('on', 'on'), ('true', 'on'), ('yes', 'on')):
            env = dict(os.environ)
            env.pop('MADEIRA_MESSAGE_CLASS', None)
            if value is not None:
                env['MADEIRA_MESSAGE_CLASS'] = value
            r = subprocess.run([str(exe), want], capture_output=True, text=True, env=env)
            ok = r.returncode == 0
            if want == 'on':
                ok = ok and r.stderr.count('+ Message (MADEIRA_MESSAGE_CLASS) rev=r630') == 3
            else:
                ok = ok and 'Message' not in r.stderr and r.stderr.count('[builtin-classes] registered builtin classes for pid=') == 3
            check('MADEIRA_MESSAGE_CLASS=%s: %s' % (value if value is not None else '(unset)',
                  'the Message class in every process, logged' if want == 'on' else 'controls only, as before') +
                  ('' if ok else ' :: ' + (r.stdout + r.stderr)[-800:]), ok)

if fails:
    print('FAILED: Message window class per program')
    sys.exit(1)
print('PASS: env.MADEIRA_MESSAGE_CLASS = 1 registers the Message class in every program; off, nothing changes')
