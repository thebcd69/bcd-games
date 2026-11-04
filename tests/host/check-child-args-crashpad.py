#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_CHILD_ARGS never reaches a Chromium crash handler (round 630).

env.MADEIRA_CHILD_ARGS = "<exe> <arguments>" appends the arguments to every process started from <exe>
(build/ntdll-unix/process_ios.c, child_extra_args). A Chromium program (NW.js, CEF) starts its crash handler as itself with
--type=crashpad-handler, and the handler takes only its own switches: with the NW.js row's
"--disable-accelerated-video-decode --disable-accelerated-video-encode" it answered "invalid option -- `-d'" and exited 1
(The Genesis Order, S8QC9X). Compiles the production ios_image_name_is, sc_switch_end, child_extra_args and the call site
with a model of the process parameters, and checks: a crash handler's command line is left as it is and the log says so;
the browser, its GPU, renderer and utility processes still get the arguments; another program is never touched; a switch
that only starts like the handler's (--type=crashpad-handlerX) or sits inside another argument does not count.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/ntdll-unix/process_ios.c').read_text()
fails = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        fails.append(what)


def function(signature):
    start = source.index(signature)
    end = source.index('\n}\n', start) + 3
    return source[start:end]


name_is = function('static int ios_image_name_is( const WCHAR *image, int image_len, const char *name )')
switch_end = function('static int sc_switch_end( const WCHAR *cl, int cl_len, const char *sw )')
extra_args = function('static const char *child_extra_args( const char *spec, const WCHAR *image, int image_len )')
site_start = source.index('    /* madeira-bcd: env.MADEIRA_CHILD_ARGS -- see child_extra_args. */')
site_end = source.index('    /* conhost.exe is refused in an ARM64EC session', site_start)
site = source[site_start:site_end]
check('the guard sits between child_extra_args and the append',
      site.index('child_extra_args( getenv( "MADEIRA_CHILD_ARGS" )') < site.index('"--type=crashpad-handler" ) >= 0)')
      < site.index('memcpy( nbuf, params->CommandLine.Buffer'))
check('sc_switch_end is defined before the call site', source.index('static int sc_switch_end(') < site_start)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-child-args-crashpad.py' in wf)

code = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define dprintf(fd, ...) fprintf(stderr, __VA_ARGS__)
typedef uint16_t WCHAR;
typedef struct { unsigned short Length, MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
typedef struct { UNICODE_STRING ImagePathName, CommandLine; } RTL_USER_PROCESS_PARAMETERS;
''' + name_is + '\n' + switch_end + '\n' + extra_args + r'''
static void widen( UNICODE_STRING *u, const char *s )
{
    size_t n = strlen( s );
    u->Buffer = calloc( n + 1, sizeof(WCHAR) );
    for (size_t i = 0; i < n; i++) u->Buffer[i] = (unsigned char)s[i];
    u->Length = (unsigned short)(n * sizeof(WCHAR));
    u->MaximumLength = u->Length + sizeof(WCHAR);
}
static char *narrow( const UNICODE_STRING *u )
{
    size_t n = u->Length / sizeof(WCHAR);
    char *s = calloc( n + 1, 1 );
    for (size_t i = 0; i < n; i++) s[i] = (char)u->Buffer[i];
    return s;
}
static const char *start( const char *image, const char *cmdline )
{
    RTL_USER_PROCESS_PARAMETERS store, *params = &store;
    widen( &params->ImagePathName, image );
    widen( &params->CommandLine, cmdline );
''' + site + r'''
    return narrow( &params->CommandLine );
}
#define SW "--disable-accelerated-video-decode --disable-accelerated-video-encode"
int main( void )
{
    const char *game = "C:\\The Genesis Order\\Game.exe";
    const char *r;
    setenv( "MADEIRA_CHILD_ARGS", "Game.exe " SW, 1 );

    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --type=crashpad-handler \"--user-data-dir=C:\\x\" /prefetch:7 --monitor-self" );
    assert( !strstr( r, "--disable-accelerated" ) );
    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --monitor-self-annotation=ptype=crashpad-handler --type=crashpad-handler" );
    assert( !strstr( r, "--disable-accelerated" ) );
    puts( "ok: a crash handler's command line is left as it is" );

    r = start( game, "\"C:\\The Genesis Order\\Game.exe\"" );
    assert( strstr( r, " " SW ) );
    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --type=gpu-process --field-trial-handle=1" );
    assert( strstr( r, " " SW ) );
    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --type=renderer --lang=en-US" );
    assert( strstr( r, " " SW ) );
    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --type=utility --utility-sub-type=network.mojom.NetworkService" );
    assert( strstr( r, " " SW ) );
    puts( "ok: the browser, GPU, renderer and utility processes still get the arguments" );

    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --type=crashpad-handlerx" );
    assert( strstr( r, " " SW ) );
    r = start( game, "\"C:\\The Genesis Order\\Game.exe\" --note=--type=crashpad-handler" );
    assert( strstr( r, " " SW ) );
    puts( "ok: only the whole --type=crashpad-handler argument counts" );

    r = start( "C:\\Program Files\\Other\\Other.exe", "Other.exe --type=crashpad-handler" );
    assert( !strcmp( r, "Other.exe --type=crashpad-handler" ) );
    setenv( "MADEIRA_CHILD_ARGS", "GTA5_Enhanced.exe -scDebugLogging", 1 );
    r = start( "C:\\Games\\GTAV\\GTA5_Enhanced.exe", "\"C:\\Games\\GTAV\\GTA5_Enhanced.exe\" -fromRGL" );
    assert( strstr( r, " -scDebugLogging" ) );
    puts( "ok: other programs as before (GTA V's line still appends)" );
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-child-args-') as directory:
    folder = Path(directory)
    src = folder / 'check.c'
    src.write_text(code)
    exe = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-Wno-sign-compare', '-g', str(src), '-o', str(exe)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')   # the call site leaks its buffer once per start, as in BCD
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode == 0:
        print('built with AddressSanitizer/UBSan')
    else:
        r = subprocess.run(flags, capture_output=True, text=True)
        check('the harness builds: ' + r.stderr[-1500:], r.returncode == 0)
    if exe.exists():
        result = subprocess.run([str(exe)], capture_output=True, text=True, env=env)
        print(result.stdout, end='')
        check('runtime checks: ' + (result.stderr[-1500:] if result.returncode else 'all passed'), result.returncode == 0)
        err = result.stderr
        check('the log names the skipped crash handler',
              err.count('[child-args] env.MADEIRA_CHILD_ARGS: not appended to a crash handler ("' + '--disable-accelerated-video-decode') == 2)
        check('the appended lines are logged as before', err.count('[child-args] env.MADEIRA_CHILD_ARGS: appended "') == 7)

if fails:
    print('FAILED: MADEIRA_CHILD_ARGS and crash handlers')
    sys.exit(1)
print('PASS: env.MADEIRA_CHILD_ARGS never reaches a --type=crashpad-handler process; every other child as before')
