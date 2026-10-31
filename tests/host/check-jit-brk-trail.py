#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""[jit-brk-prev]: the next start names the debugger region request the last run ended in (round 624, agent R S2; log only).

GTA V Enhanced (6D6ZZS) and the archive's "window held" logs end inside a pool step: the whole process is stopped inside a BRK while the
debugger serves a region request (StikJITHelper.requestRegion, five call sites), and when it is killed there nothing the app writes
survives. BCDBrkTrail (BCDSessionRecorderLive.swift) writes one fsynced record before each debugger request (the caller's line, size,
the pool loop's attempt, uptime, wall clock, thermal state, Low Power Mode, available memory) and "returned ms=N" after it; the next
start logs the record when it has no "returned" line, and removes the file either way.

Checks: the type compiled on the host (os_proc_available_memory stubbed, the file pointed at a temporary path) and driven: a record
without "returned" comes back, a returned one does not, a second request replaces the first, the file is removed, a missing file is
nothing; source pins: only the debugger path records (never the debugger-less pool), "returned" in a defer, the caller's line by #line,
the pool loop passes its attempt, the next start reads the trail before the unclean-session question, Application Support.
"""
from pathlib import Path
import shutil, subprocess, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


rec = (app / 'BCDSessionRecorderLive.swift').read_text()
trail = rec[rec.index('enum BCDBrkTrail {'):]
trail = trail[:trail.index('\n}\n') + 3]
helper = (app / 'StikJITHelper.swift').read_text()
req = helper[helper.index('    private static func requestRegion(_ size: Int, attempt: Int? = nil, site: Int = #line) -> UnsafeMutableRawPointer? {'):]
req = req[:req.index('\n    }\n') + 7]
debugger_path = req[req.index('guard noDebugger else {'):req.index('        }\n')]
check('only the debugger path records (the debugger-less pool cannot stop the process), "returned" in a defer, the caller\'s line by #line',
      'BCDBrkTrail.begin(site: site, size: size, attempt: attempt)' in debugger_path and 'defer { BCDBrkTrail.end() }' in debugger_path
      and debugger_path.index('BCDBrkTrail.begin(') < debugger_path.index('return jit26_prepare_region(nil, size)')
      and req.count('BCDBrkTrail.') == 2 and helper.count('BCDBrkTrail.') == 2)
check('the pool loop passes its attempt; the other four call sites are unchanged',
      helper.count('requestRegion(poolSize, attempt: attempt)') == 1 and helper.count('requestRegion(') == 6)
facade = rec[rec.index('    static func appStarted() {'):]
facade = facade[:facade.index('\n    }\n')]
check('the next start reads the trail before the unclean-session question and logs [jit-brk-prev]',
      'if let last = BCDBrkTrail.takeUnfinished() {' in facade
      and facade.index('BCDBrkTrail.takeUnfinished()') < facade.index('BCDSessionRecorderLive.shared.recoverUnfinished()')
      and '"[jit-brk-prev] the previous run ended inside a debugger region request: \\(last)"' in facade)
check('the file is in Application Support (not the player\'s Documents) and is fsynced',
      '.applicationSupportDirectory' in trail and 'jit-brk-last.txt' in trail and 'documentDirectory' not in trail
      and 'try? handle.synchronize()' in trail)

swiftc = shutil.which('swiftc')
if swiftc:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        driver = r'''
import Foundation
func os_proc_available_memory() -> Int { 3 << 30 }
''' + trail + r'''
let dir = CommandLine.arguments[1]
BCDBrkTrail.url = URL(fileURLWithPath: dir + "/jit-brk-last.txt")
let path = BCDBrkTrail.url!.path
func say(_ s: String) { print(s) }
// 1: a request that did not come back
BCDBrkTrail.begin(site: 864, size: 268 << 20, attempt: 1)
let a = BCDBrkTrail.takeUnfinished()
say("1 " + (a ?? "nil"))
say("1gone " + String(!FileManager.default.fileExists(atPath: path)))
// 2: a request that came back
BCDBrkTrail.begin(site: 1552, size: 64 << 20, attempt: nil)
BCDBrkTrail.end()
let text = (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
say("2lines " + String(text.split(separator: "\n").count) + " " + String(text.split(separator: "\n").last.map { $0.hasPrefix("returned ms=") } ?? false))
say("2 " + (BCDBrkTrail.takeUnfinished() ?? "nil"))
say("2gone " + String(!FileManager.default.fileExists(atPath: path)))
// 3: a second request replaces the first
BCDBrkTrail.begin(site: 864, size: 268 << 20, attempt: 0)
BCDBrkTrail.end()
BCDBrkTrail.begin(site: 1690, size: 32 << 20, attempt: nil)
say("3 " + (BCDBrkTrail.takeUnfinished() ?? "nil"))
// 4: no file
say("4 " + (BCDBrkTrail.takeUnfinished() ?? "nil"))
'''
        (t / 'main.swift').write_text(driver)
        r = subprocess.run([swiftc, '-o', str(t / 'b'), str(t / 'main.swift')], capture_output=True, text=True)
        check('BCDBrkTrail compiles on the host', r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            out = subprocess.run([str(t / 'b'), str(t)], capture_output=True, text=True).stdout.splitlines()
            got = dict((l.split(' ', 1) + [''])[:2] for l in out)
            first = got.get('1', '')
            check('a request that did not come back is read back whole: caller line, size, attempt, uptime, wall clock, heat, Low Power Mode, memory',
                  first.startswith('begin site=L864 size=268MB attempt=1 uptime=') and ' wall=' in first and ' thermal=' in first
                  and ' lpm=' in first and first.endswith(' avail=3072MB'), out)
            check('... and the file goes', got.get('1gone') == 'true', out)
            check('a request that came back has its "returned ms=" line and reads as nothing; the file goes',
                  got.get('2lines') == '2 true' and got.get('2') == 'nil' and got.get('2gone') == 'true', out)
            check('the file holds the last request only: the open one after a returned one is the one read back',
                  got.get('3', '').startswith('begin site=L1690 size=32MB attempt=- '), out)
            check('no file, nothing', got.get('4') == 'nil', out)
else:
    print('skip the compiled part: no swiftc')

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-jit-brk-trail.py' in wf)
print('PASS: jit-brk trail' if ok else 'FAIL: jit-brk trail')
raise SystemExit(0 if ok else 1)
