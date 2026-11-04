#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A desktop window made later never takes the touch input from the desktop that has it (round 630).

BCD's touch input goes to the visible winstation's input desktop (send_hardware_message( NULL, ... ), queue_ios.c). The
iOS port marks a winstation visible and makes a desktop its input desktop whenever that desktop gets its desktop window
(build/wineserver/window_ios.c: the first top-level CreateWindowEx on it, and get_desktop_window with force). In a Windows
desktop session a service on the Default desktop (the Google updater's crash handler, through ios_child_desktop_fixup) got
its desktop window after explorer's virtual desktop had the input, took it, and every tap landed on its detached window and
was dropped (416C0V, 0MYT4P, XYWN2M). Compiles the production ios_desktop_window_made against a model of winstations and
desktops and checks:
  - the session's first desktop window: its winstation becomes visible and the desktop the input desktop, as before;
  - a desktop window made on ANOTHER desktop while the visible winstation's input desktop has its window: nothing changes
    (no input move, no second visible winstation), and the log says so once;
  - an input desktop without its window yet, or the same desktop again: the desktop takes the input, as before;
  - MADEIRA_INPUT_DESKTOP_STEAL=1: every desktop window takes the input, as before round 630;
plus source checks: both creation paths call it and nothing else in window_ios.c moves the input desktop; explorer's virtual
desktop still takes the input in winstation.c (full tree).
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'build/wineserver/window_ios.c').read_text()
fails = []


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        fails.append(what)


start = source.index('static void ios_desktop_window_made( struct desktop *desktop )')
helper = source[start:source.index('\n}\n', start) + 3]
check('both creation paths call it (CreateWindowEx auto-create, get_desktop_window force)',
      source.count('ios_desktop_window_made( desktop );') == 2
      and source.index('ios_desktop_window_made( desktop );') < source.index('DECL_HANDLER(get_desktop_window)\n{')
      < source.rindex('ios_desktop_window_made( desktop );'))
check('nothing else in window_ios.c moves the input desktop',
      source.count('->input_desktop = desktop;') == 1 and 'desktop->winstation->input_desktop = desktop;' in helper)
winstation = Path.home() / 'madeira-bcd/wine/server/winstation.c'
if winstation.exists():
    w = winstation.read_text()
    check("explorer's virtual desktop still takes the input in winstation.c (set_input_desktop, forced)",
          'set_input_desktop( winstation, desktop );\n                fprintf( stderr, "[srv-desktop] virtual desktop created' in w)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-input-desktop.py' in wf)

code = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define WINE_IOS 1
struct window { unsigned int handle; };
struct winstation;
struct desktop { struct winstation *winstation; struct window *top_window; };
struct winstation { unsigned int flags; struct desktop *input_desktop; };
static struct winstation ws[2];
static struct winstation *get_visible_winstation( void )
{
    for (int i = 0; i < 2; i++) if (ws[i].flags & 1) return &ws[i];
    return NULL;
}
''' + helper + r'''
static struct window w_virtual = { 0x10020 }, w_default = { 0x1004c }, w_service = { 0x20040 };
int main( int argc, char **argv )
{
    int steal = argc > 1 && !strcmp( argv[1], "steal" );
    struct desktop virtual_desk = { &ws[0], NULL }, default_desk = { &ws[0], NULL }, service_desk = { &ws[1], NULL };

    /* 1. the session's first desktop window */
    memset( ws, 0, sizeof(ws) );
    virtual_desk.top_window = &w_virtual;
    ios_desktop_window_made( &virtual_desk );
    assert( (ws[0].flags & 1) && ws[0].input_desktop == &virtual_desk );
    /* 2. the Default desktop of the same winstation gets its window later (416C0V) */
    default_desk.top_window = &w_default;
    ios_desktop_window_made( &default_desk );
    assert( ws[0].input_desktop == (steal ? &default_desk : &virtual_desk) );
    /* 3. a desktop of another winstation (a service winstation) gets its window */
    ws[0].input_desktop = &virtual_desk;
    service_desk.top_window = &w_service;
    ios_desktop_window_made( &service_desk );
    if (steal) assert( (ws[1].flags & 1) && ws[1].input_desktop == &service_desk );
    else assert( !(ws[1].flags & 1) && ws[1].input_desktop == NULL );
    /* 4. the input desktop has no window yet: the new one takes it, as before */
    memset( ws, 0, sizeof(ws) );
    ws[0].flags = 1; ws[0].input_desktop = &virtual_desk; virtual_desk.top_window = NULL;
    ios_desktop_window_made( &default_desk );
    assert( ws[0].input_desktop == &default_desk );
    /* 5. the same desktop again */
    virtual_desk.top_window = &w_virtual; ws[0].input_desktop = &virtual_desk;
    ios_desktop_window_made( &virtual_desk );
    assert( ws[0].input_desktop == &virtual_desk && (ws[0].flags & 1) );
    puts( steal ? "ok: MADEIRA_INPUT_DESKTOP_STEAL=1 gives every desktop window the input, as before"
                : "ok: first window, no-window input desktop and same desktop as before; a later desktop never takes the input" );
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-input-desktop-') as directory:
    folder = Path(directory)
    src = folder / 'check.c'
    src.write_text(code)
    exe = folder / 'check'
    cc = os.environ.get('CC', 'cc')
    flags = [cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-g', str(src), '-o', str(exe)]
    sanitize = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all']
    if subprocess.run(flags[:1] + sanitize + flags[1:], capture_output=True).returncode == 0:
        print('built with AddressSanitizer/UBSan')
    else:
        r = subprocess.run(flags, capture_output=True, text=True)
        check('the harness builds: ' + r.stderr[-1500:], r.returncode == 0)
    if exe.exists():
        for mode, env_value in (('keep', None), ('steal', '1')):
            env = dict(os.environ)
            env.pop('MADEIRA_INPUT_DESKTOP_STEAL', None)
            if env_value:
                env['MADEIRA_INPUT_DESKTOP_STEAL'] = env_value
            r = subprocess.run([str(exe), mode], capture_output=True, text=True, env=env)
            print(r.stdout, end='')
            ok = r.returncode == 0
            if mode == 'keep':
                ok = ok and r.stderr.count('the input stays on the desktop that has it (window 00010020) rev=r630') == 2 \
                    and 'window 0001004c' in r.stderr
            else:
                ok = ok and 'rev=r630' not in r.stderr
            check('runtime (%s): %s' % (mode, 'all passed' if ok else (r.stdout + r.stderr)[-800:]), ok)

if fails:
    print('FAILED: input desktop')
    sys.exit(1)
print('PASS: a desktop window made later keeps away from the input of the desktop that has it')
