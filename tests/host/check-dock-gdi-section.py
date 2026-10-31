#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
"""A Dock or Desktop session's GDI handle table is section-backed (app/Madeira/ContentView.swift).

A Dock session starts 64-bit (explorer, then the host) and later starts 32-bit
programs: one-time installers and 32-bit games. win32u decides once, when the first
program initialises it, whether the GDI handle table is a section a 32-bit program can
map inside its guest window (wine dlls/win32u/gdiobj.c gdi_shared_use_section). The
Dock start now asks for the section; bcd.cfg can still turn it off.

The library's Desktop (owner, 2026-10-21) asks for it too: programs installed and
started there are often 32-bit (Battle.net-Setup.exe, build 579, died in gdi32 on the
truncated table address). A game started "In a Wine desktop" keeps the default.

Checks:
  * startDock sets MADEIRA_GDI_SHARED_SECTION=1 once, before the launch request;
  * DesktopGDISection.apply sets it only for the library's Desktop (entry.desktop), and
    clears it only when this app run's Desktop set it; startLibraryEntry calls it before
    the session starts; nothing else in the app sets or clears the variable;
  * build/ntdll-unix/virtual_ios.c logs "[gdi-shared] 32-bit program in a session whose
    GDI table is private" when the session is armed only after win32u made a private
    table, and only then (compiled verbatim on the host: four cases); it changes nothing;
  * the bridge exports bcd.cfg env.* entries with setenv(..., 1) when the session
    starts, i.e. after startDock, so env.MADEIRA_GDI_SHARED_SECTION = 0 still wins;
  * when the wine submodule is checked out: gdi_shared_use_section(), compiled
    verbatim, returns TRUE for "1", FALSE for "0", and falls back to the session
    being armed when the variable is unset or empty.
Source checks and one small host compile: no Wine, iOS or device.
"""
from pathlib import Path
import os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app' / 'Madeira'
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


content = (app / 'ContentView.swift').read_text(encoding='utf-8')
start = content.index('private func startDock(')
body = content[start:content.index('\n    }\n', start)]
line = 'setenv("MADEIRA_GDI_SHARED_SECTION", "1", 1)'
require(body.count(line) == 1, 'startDock asks for the section-backed GDI table once')
require(body.index(line) < body.index('MadeiraDock.requestLaunch('), 'before the launch request')

# The library's Desktop (DesktopGDISection, at the end of ContentView.swift).
enum_at = content.index('enum DesktopGDISection {')
desk = content[enum_at:content.index('\n}\n', enum_at)]
set_line, unset_line = 'setenv("MADEIRA_GDI_SHARED_SECTION", "1", 1)', 'unsetenv("MADEIRA_GDI_SHARED_SECTION")'
gate = 'if entry.desktop == true || (entry.runsInDesktop && entry.bits == 32) {'
require(desk.count(set_line) == 1 and desk.count(gate) == 1 and desk.index(gate) < desk.index(set_line),
        'DesktopGDISection: the section for the library\'s Desktop entry and a 32-bit program started "In a Wine desktop" only (round 624)')
require('entry.desktop == true ? "Desktop" : "Wine-desktop start of a 32-bit program"' in desk,
        'DesktopGDISection: the log line says which of the two set it')
require(desk.count(unset_line) == 1 and desk.index('if setByDesktop {') < desk.index(unset_line),
        'DesktopGDISection: cleared only when this run\'s Desktop set it')
lib_at = content.index('private func startLibraryEntry(')
lib = content[lib_at:content.index('\n    }\n', lib_at)]
call = 'DesktopGDISection.apply(entry)'
require(lib.count(call) == 1 and lib.index('exportBCDDesktopProfile(entry)') < lib.index(call)
        < lib.index('runWineFullSequence(profile: entry)'), 'startLibraryEntry applies it before the session starts')
require(content.count(call) == 1, 'DesktopGDISection.apply is called from startLibraryEntry only')

for path in sorted(app.rglob('*')):
    if path.suffix not in ('.swift', '.m', '.mm', '.c', '.h') or path.name == 'ConfigCatalog.generated.swift':
        continue
    text = path.read_text(encoding='utf-8', errors='replace')
    count = text.count('"MADEIRA_GDI_SHARED_SECTION"')
    expected = 3 if path.name == 'ContentView.swift' else 0
    if count != expected:
        require(False, f'{path.name}: MADEIRA_GDI_SHARED_SECTION appears {count} time(s), expected {expected}')
require(content.count('"MADEIRA_GDI_SHARED_SECTION"') == 3,
        'nothing else in ContentView touches it (startDock, the Desktop\'s set and clear; games unchanged)')

# The diagnostic in ios_wow_session_arm (a log line; nothing else).
virt = (root / 'build' / 'ntdll-unix' / 'virtual_ios.c').read_text(encoding='utf-8', errors='replace')
d_begin = virt.index('extern int win32u_wow_session __attribute__((weak));')
d_end = virt.index('\n}\n', virt.index('void ios_wow_session_arm(void)', d_begin)) + 3
diag = virt[d_begin:d_end]
require('[gdi-shared] 32-bit program in a session whose GDI table is private host memory' in diag,
        'virtual_ios.c: ios_wow_session_arm says when a 32-bit program meets a private GDI table')
require('setenv' not in diag and 'win32u_wow_session =' not in diag, 'virtual_ios.c: the diagnostic changes nothing')
CC = os.environ.get('CC') or shutil.which('cc') or 'cc'
with tempfile.TemporaryDirectory() as tmp:
    c = Path(tmp) / 'arm.c'
    c.write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <unistd.h>\ntypedef int BOOL;\n'
                 'static int ios_wow_armed;\n' + diag +
                 'int win32u_wow_session;\nvoid *ios_win32u_session_peb;\n'
                 'int main(int c, char **v) { win32u_wow_session = atoi(v[1]);'
                 ' ios_win32u_session_peb = atoi(v[2]) ? (void *)&c : NULL;'
                 ' ios_wow_session_arm(); ios_wow_session_arm(); return 0; }\n')
    exe = Path(tmp) / 'arm'
    subprocess.run([CC, '-O1', '-w', '-o', str(exe), str(c)], check=True)

    def arm(section, ready, value=None):
        env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_GDI_SHARED_SECTION'}
        if value is not None:
            env['MADEIRA_GDI_SHARED_SECTION'] = value
        return subprocess.run([str(exe), str(section), str(ready)], env=env, capture_output=True, text=True).stderr

    out = arm(0, 1)
    require(out.count('[wow-window] session armed') == 1 and out.count('[gdi-shared] 32-bit program') == 1
            and 'MADEIRA_GDI_SHARED_SECTION=unset' in out, 'diagnostic: private table already made -> one line (and once)')
    require('MADEIRA_GDI_SHARED_SECTION=0' in arm(0, 1, '0'), 'diagnostic: names the variable\'s value')
    require('[gdi-shared]' not in arm(1, 1), 'diagnostic: section-backed table -> no line')
    require('[gdi-shared]' not in arm(0, 0), 'diagnostic: win32u not initialised yet (it will see the armed session) -> no line')

bridge = (app / 'WineProcessBridge.m').read_text(encoding='utf-8')
export = bridge.index('if (![line hasPrefix:@"env."] || eq.location == NSNotFound) continue;')
# ml1184 grew the export loop (a game's own keys win), so look to the end of it.
require('setenv(k.UTF8String, v.UTF8String, 1);' in bridge[export:export + 6000],
        'bcd.cfg env.* entries are exported with overwrite when the session starts')

gdiobj = root / 'wine' / 'dlls' / 'win32u' / 'gdiobj.c'
if gdiobj.exists():
    src = gdiobj.read_text(encoding='utf-8', errors='replace')
    begin = src.index('static BOOL gdi_shared_use_section(void)')
    func = src[begin:src.index('\n}\n', begin) + 3]
    CC = os.environ.get('CC') or shutil.which('cc') or 'cc'
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / 't.c'
        c.write_text('#include <stdio.h>\n#include <stdlib.h>\ntypedef int BOOL;\n'
                     'static int armed;\nstatic int ios_wow_session_armed(void) { return armed; }\n'
                     + func.replace('ios_wow_session_armed && ', '') +
                     'int main(int c, char **v) { armed = atoi(v[1]); printf("%d\\n", gdi_shared_use_section()); return 0; }\n')
        exe = Path(tmp) / 't'
        subprocess.run([CC, '-O1', '-o', str(exe), str(c)], check=True)

        def run(value, armed):
            env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_GDI_SHARED_SECTION'}
            if value is not None:
                env['MADEIRA_GDI_SHARED_SECTION'] = value
            return subprocess.run([str(exe), str(armed)], env=env, capture_output=True, text=True).stdout.strip()

        require(run('1', 0) == '1', 'wine: "1" section-backs the table in an unarmed (64-bit start) session')
        require(run('0', 1) == '0', 'wine: "0" keeps the private table even in an armed session')
        require(run(None, 0) == '0' and run(None, 1) == '1', 'wine: unset follows the session being armed')
        require(run('', 1) == '1', 'wine: empty follows the session being armed')
else:
    print('SKIP: wine submodule not checked out; gdi_shared_use_section not compiled')

print('FAILURES:', failures)
sys.exit(1 if failures else 0)
