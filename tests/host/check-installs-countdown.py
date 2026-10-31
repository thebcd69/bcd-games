#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A session of one-time installs that succeeded counts down to its restart (round 624, agent R S25/S26; 1EXE6P; text and log only).

1EXE6P: Social Club and the Rockstar Launcher installed fine ([dock-installers] end succeeded=2 failed=0); the starting screen then said
only "This session is closing now. Wait until BCD asks you to restart it." while the session idled with its services, and the player left
35 s later thinking BCD had crashed (the restart is offered 45 s after the batch, MadeiraDockModel). Now the screen counts down to that
offer (DockInstallers.closingCountdown, one number restartOfferDelay for the offer and the text), and the wait for child processes
names them at 10 s and 30 s as well as every 60 s (WineProcessBridge.m).

Checks: the countdown compiled on the host with the values it reads (3 s after the batch: about 42 seconds; 44 s: 1 second; 45 s and later
and no batch end: the closing words); the offer and the text use one number; the starting screen shows the countdown; the 10 s/30 s lines.
"""
from pathlib import Path
import re, shutil, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


inst = (app / 'DockInstallers.swift').read_text()
fn = inst[inst.index('    static func closingCountdown(now: Date = Date()) -> String {'):]
fn = fn[:fn.index('\n    }\n') + 7]
closing = re.search(r'nonisolated static let closingWords = "([^"]*)"', inst).group(1)
check('one number for the offer and the countdown', 'nonisolated static let restartOfferDelay = 45' in inst
      and 'Date().timeIntervalSince(finished) >= Double(DockInstallers.restartOfferDelay) {' in (app / 'MadeiraDockModel.swift').read_text()
      and '>= 45' not in (app / 'MadeiraDockModel.swift').read_text())
check('the starting screen of an installs-only session shows the countdown',
      'restartHint: DockInstallers.installsOnly ? DockInstallers.closingCountdown() : nil' in (app / 'BCDSessionLive.swift').read_text())
bridge = (app / 'WineProcessBridge.m').read_text()
check('the wait for child processes names them at 10 s and 30 s, then every 60 s (200 ms ticks)',
      'if (++ticks == 50 || ticks == 150 || (ticks % 300) == 0)' in bridge and '(++ticks % 300)' not in bridge)

swiftc = shutil.which('swiftc')
if swiftc:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / 'main.swift').write_text('import Foundation\nenum DockInstallers {\n    static var finishedAt: Date?\n'
                                      '    static let closingWords = "' + closing + '"\n    static let restartOfferDelay = 45\n' + fn + '}\n'
                                      'let d = Date(timeIntervalSince1970: 1_800_000_000)\n'
                                      'print(DockInstallers.closingCountdown(now: d))\n'
                                      'DockInstallers.finishedAt = d\n'
                                      'for s in [0.0, 3.0, 44.0, 44.9, 45.0, 120.0] { print(DockInstallers.closingCountdown(now: d.addingTimeInterval(s))) }\n')
        r = subprocess.run([swiftc, '-o', str(t / 'c'), str(t / 'main.swift')], capture_output=True, text=True)
        check('the countdown compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            out = subprocess.run([str(t / 'c')], capture_output=True, text=True).stdout.splitlines()
            want = [closing,
                    'The installs are finished. BCD is saving the setup and asks you to restart in about 45 seconds. Do not close BCD yet.',
                    'The installs are finished. BCD is saving the setup and asks you to restart in about 42 seconds. Do not close BCD yet.',
                    'The installs are finished. BCD is saving the setup and asks you to restart in about 1 second. Do not close BCD yet.',
                    'The installs are finished. BCD is saving the setup and asks you to restart in about 1 second. Do not close BCD yet.',
                    closing, closing]
            check('no batch end: the closing words; then 45, 42, 1, 1 seconds; at 45 s and after: the closing words', out == want, out)
else:
    print('skip the compiled part: no swiftc')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-installs-countdown.py' in wf)
print('PASS: installs countdown' if ok else 'FAIL: installs countdown')
raise SystemExit(0 if ok else 1)
