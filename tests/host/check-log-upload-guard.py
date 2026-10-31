#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Send Log and Feedback offer the game's log when the newest log holds no game (round 624, agent N S6).

Four of the round's 227 Send Logs (RZTPBY, WMDK8B, H0TBAZ, 2FJV7P) were 1-3 KB logs of an app start with no game in them: after a restart
Settings > Logs lists BCD's new log first and Feedback attached it. LogUpload.hasGame reads the first 64 KB for "[bcd] library launch" or
"[dock-launch]"; when the newest log has neither, the send box names the newest game log of the last day and sends that one first (this log
can still be sent), and Feedback attaches the game log.

Checks: hasGame compiled on the host and run on temporary files; the call sites (the Logs list fills hasGame, the send box offers the game
log only when the newest has none, the plain box stays otherwise, Feedback attaches the game log); the settings driver covers the pure rule.
"""
from pathlib import Path
import os, shutil, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


up = (app / 'LogUpload.swift').read_text()
fn = up[up.index('    static func hasGame(_ url: URL) -> Bool {'):]
fn = fn[:fn.index('\n    }\n') + 7]
sections = (app / 'BCDSettingsSections.swift').read_text()
live = (app / 'BCDSettingsLive.swift').read_text()
feedback = (app / 'BCDSettingsFeedback.swift').read_text()
confirm = sections[sections.index('    private func confirmSend(_ log: BCDLogFile) {'):]
confirm = confirm[:confirm.index('\n    }\n') + 7]
check('the Logs list fills hasGame for every listed log', 'hasGame: LogUpload.hasGame($0)) }' in live and 'var hasGame = true' in sections)
check('the send box offers the game log only when the newest log (this one) has no game and a game log of the last day exists',
      'if !log.hasGame, items.first?.name == log.name, let i = BCDLogChoice.gameLogToOffer(items, now: Date()) {' in confirm
      and '.init(title: "Send this log anyway")' in confirm and confirm.index('return\n') < confirm.index('.init(title: "Send") {'))
check('otherwise the box is the one it was (Send, cleaned on this device, the memory-rights advice)',
      '.init(title: "Send") { BCDLogComment.ask(for: URL(fileURLWithPath: log.id)) { backend.send(log, comment: $0) } }' in confirm
      and '"\\(BCDFormat.bytes(log.bytes)), cleaned on this device before it leaves."' in confirm)
check('Feedback attaches the game log when the newest has none, and says so when there is none',
      'if !LogUpload.hasGame(url) {' in live and 'if let i = BCDLogChoice.gameLogToOffer(items, now: Date()) { url = urls[i] }' in live
      and '($0.hasGame ? "" : ": no game ran in it")' in feedback)

swiftc = shutil.which('swiftc')
if swiftc:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / 'game.txt').write_text('[10:00:00.000] [INFO] [build] v0.1.624 (624)\n' + 'x\n' * 40 + '[10:00:05.000] [INFO] [bcd] library launch Game.exe: avx=0\n')
        (t / 'dock.txt').write_text('[10:00:00.000] [INFO] [dock-launch] steam app 1234\n')
        (t / 'app.txt').write_text('[10:00:00.000] [INFO] [build] v0.1.624 (624)\n' + 'x\n' * 29)
        (t / 'late.txt').write_text('y' * 70000 + '\n[bcd] library launch Late.exe\n')
        (t / 'main.swift').write_text('import Foundation\nenum LogUpload {\n' + fn + '}\n'
                                      'for n in ["game.txt", "dock.txt", "app.txt", "late.txt", "missing.txt"] {\n'
                                      '    print(n, LogUpload.hasGame(URL(fileURLWithPath: CommandLine.arguments[1] + "/" + n)))\n}\n')
        r = subprocess.run([swiftc, '-o', str(t / 'g'), str(t / 'main.swift')], capture_output=True, text=True)
        check('hasGame compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            out = subprocess.run([str(t / 'g'), str(t)], capture_output=True, text=True).stdout.split('\n')
            check('a game log and a Dock log hold a game; an app-only log, a marker beyond 64 KB and a missing file do not',
                  out[:5] == ['game.txt true', 'dock.txt true', 'app.txt false', 'late.txt false', 'missing.txt false'], out)
else:
    print('skip the compiled part: no swiftc')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-log-upload-guard.py' in wf)
print('PASS: log upload guard' if ok else 'FAIL: log upload guard')
raise SystemExit(0 if ok else 1)
