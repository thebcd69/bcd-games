#!/usr/bin/env python3
# Copyright 2026 NevesHenry. This file was taken from https://github.com/willfaust/Madeira (pull request #579)
# under GPL-3.0-or-later with the Madeira Converter Exception, and modified for BCD in November 2026 (BCD's bridge also keeps the
# message, which BCD's launch shows). See THIRD-PARTY-NOTICES.md.
"""A wineserver that dies at start-up is no longer reported as running (app/Madeira/WineServerBridge.m); no Wine runs.

wineserver runs as a thread of the app. Its fatal_error() is replaced in WineServerBridge.m: it logs "[Wine] FATAL: ..." and
ends the thread with pthread_exit(), so the end of wineserver_thread_func, which clears g_wineserver_running, never runs.
BCD clears the flag and keeps the message inside fatal_error() since round 624 (RM4C0R, AQZ3E2, 3DX90K: "[Wine] FATAL: bind:
Operation not permitted" in LiveContainer); check-wineserver-bind-fallback.py checks the text of that and of the launch.

This check runs it: it compiles the production fatal_error(), wineserver_is_running(), wineserver_fatal_message() and their
two variables with a stub logger, calls fatal_error() from a server thread during start-up, and checks that
  - the thread ends inside fatal_error() (pthread_exit), the FATAL line is logged;
  - wineserver_is_running() is 0 afterwards and the message is kept without its line break;
  - the launch's wait for the server (ContentView.swift: until ready, until it stops running, or 2 s) stops at once.
Needs python3 and a C compiler (AddressSanitizer/UBSan when available).
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
bridge = (root / 'app/Madeira/WineServerBridge.m').read_text()
content = (root / 'app/Madeira/ContentView.swift').read_text()
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


fatal = function(bridge, 'void fatal_error( const char *err, ... ) {')
running = function(bridge, 'int wineserver_is_running(void) {')
message = function(bridge, 'const char *wineserver_fatal_message(void) {')
flag = re.findall(r'^static volatile int g_wineserver_running(?: = 0)?;$', bridge, re.M)
kept = re.findall(r'^static char g_wineserver_fatal\[256\];$', bridge, re.M)
require(len(flag) == 1 and len(kept) == 1, 'WineServerBridge.m defines g_wineserver_running and g_wineserver_fatal once')
require('pthread_exit(NULL);' in fatal and not re.search(r'^\s*exit\(', fatal, re.M),
        'fatal_error() ends only the server thread (pthread_exit), not the app')
launch = content[content.index('self.startWineserver()'):content.index('self.startWineProcess()')]
require('while wineserver_is_ready() == 0, wineserver_is_running() != 0, CFAbsoluteTimeGetCurrent() - waitStart < 2.0 {' in launch,
        'the launch waits for the server until it is ready, stops running, or 2 s pass (the loop the harness runs)')

harness = r'''
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static char logged[1200];
static void wine_log_msg_impl( const char *fmt, ... )
{
    va_list args;
    va_start( args, fmt );
    vsnprintf( logged, sizeof(logged), fmt, args );
    va_end( args );
}
void fatal_error( const char *err, ... );
int wineserver_is_running(void);
const char *wineserver_fatal_message(void);
''' + ''.join(line + '\n' for line in flag + kept)

harness_main = r'''
static int bad;
static void check( int ok, const char *label ) { printf( "%s: %s\n", ok ? "PASS" : "FAIL", label ); bad |= !ok; }

static double now( void )
{
    struct timespec ts;
    clock_gettime( CLOCK_MONOTONIC, &ts );
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

/* wineserver_thread_func -> wineserver_main -> acquire_lock() (request_ios.c) */
static void *server_thread( void *arg )
{
    (void)arg;
    fatal_error( "bind: %s\n", strerror( EPERM ) );
    return (void *)1;   /* not reached */
}

int main( void )
{
    pthread_t thread;
    void *result = NULL;
    double start, waited;
    int ready = 0;
    char want[300];

    g_wineserver_fatal[0] = 0;   /* wineserver_start(), before pthread_create */
    g_wineserver_running = 1;
    pthread_create( &thread, NULL, server_thread, NULL );

    /* ContentView.swift, MADEIRA_FAST_SERVER_START: wait for ready, a stopped server or 2 s */
    start = now();
    while (!ready && wineserver_is_running() != 0 && now() - start < 2.0)
    {
        struct timespec ms10 = { 0, 10 * 1000 * 1000 };
        nanosleep( &ms10, NULL );
    }
    waited = now() - start;
    pthread_join( thread, &result );

    snprintf( want, sizeof(want), "bind: %s", strerror( EPERM ) );
    check( result == NULL, "the server thread ended inside fatal_error() (pthread_exit)" );
    check( strstr( logged, "[Wine] FATAL: bind: " ) != NULL, "the FATAL line is logged" );
    check( wineserver_is_running() == 0, "after fatal_error(), wineserver_is_running() is 0" );
    check( !strcmp( wineserver_fatal_message(), want ), "the message is kept for the launch error, without its line break" );
    printf( "launch wait ended after %.0f ms\n", waited * 1000 );
    check( waited < 1.0, "the launch's wait for the server stops when fatal_error() runs, not after 2 s" );
    return bad;
}
'''

with tempfile.TemporaryDirectory(prefix='bcd-wineserver-fatal-') as directory:
    folder = Path(directory)
    source = folder / 'check.c'
    source.write_text(harness + message + fatal + running + harness_main)
    executable = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-g', '-pthread',
             str(source), '-o', str(executable)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    build = subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True, text=True)
    if build.returncode == 0:
        print('built with AddressSanitizer/UBSan')
    else:
        build = subprocess.run(flags, capture_output=True, text=True)
        print('built without sanitizers' if build.returncode == 0 else build.stderr[-4000:])
    require(build.returncode == 0, 'harness compiles')
    if build.returncode == 0:
        run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
        print(run.stdout, end='')
        require(run.returncode == 0, 'every runtime check passed, no sanitizer report')
        if run.returncode:
            print(run.stderr[-4000:])

wf = (root / '.github/workflows/build-ipa.yml').read_text()
require('python3 tests/host/check-wineserver-fatal.py' in wf, 'CI runs this check')
print('PASS' if not failures else f'FAILED ({failures})')
sys.exit(1 if failures else 0)
