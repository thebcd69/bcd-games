#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.BCD_EPIC_HOST_START: an Epic game's signed-in start keeps BCD Host as its first program (opt-in); no device.

BCDEpic.applyLaunch (BCDEpicStart.swift, right after the first LibraryEntry.configureLaunch on the main thread) sets MADEIRA_EXE and MADEIRA_ARGS to
BCD Host (bcdhost.exe --epic). LibraryEntry.applyEnvironment() then runs configureLaunch() AGAIN on the launch worker, which sets both back to the
game's own program. So in builds 590 to 597 no Epic game ever started through the host: all twelve Epic sessions of the logs of 2026-10-23/24 print
"[bcd-host] no report", and their argv[1] is the game's exe with Epic's public arguments only ("-epicapp=... -epicenv=Prod -EpicPortal -epiclocale=en";
no -AUTH_* arguments). BioShock Infinite from the Epic store (linked to EOSSDK-Win32-Shipping.dll) ended itself with ExitProcess(1) 1.5-3 s after the
start in eight logs; the sign-in is the first suspect. With env.BCD_EPIC_HOST_START = 1 in the game's own config, startThroughHost holds the host's two
values and applyEnvironment sets them again right after its configureLaunch (once). Off (the default, no game but BioShock Infinite's list sets it) nothing
is held and the start is exactly what builds 590 to 597 did.

  * static checks: the flag reads the game's own lines, then bcd.cfg, with the default OFF; the hold happens only inside that flag, with the same program
    and arguments as the setenv lines beside it; applyLaunch clears any hold before it looks at the entry; applyEnvironment restores right after its
    configureLaunch; the policy allows exactly 0 and 1 for the name; the catalog lists the key off by default; the BioShock Infinite (Epic) list is the only
    list that sets it; check-epic's invariants still hold (five setenv lines in BCDEpicStart.swift, only BCD_HOST_REQUEST unset there); CI runs this;
  * the hold's code (between "epic-host-hold:begin" and ":end" in Library.swift) is compiled on the host with swiftc and run: no hold leaves
    MADEIRA_EXE and MADEIRA_ARGS as they are; a hold for the entry sets both once and is spent; a hold for another entry sets nothing and is spent; a clear
    spends it; a later hold replaces an earlier one.
Needs python3; the run needs swiftc.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def code(text):
    """Swift without comments, for matching."""
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return '\n'.join(re.sub(r'(?<!:)//.*$', '', line) for line in text.splitlines())


library = (R / 'app/Madeira/Library.swift').read_text()
start = (R / 'app/Madeira/BCDEpic/BCDEpicStart.swift').read_text()
facade = (R / 'app/Madeira/BCDEpic/BCDEpic.swift').read_text()
policy = (R / 'app/Madeira/GameRecommendationsList.swift').read_text()
sc, lc = code(start), code(library)

# ------------------------------------------------------------------ static
check('the flag defaults to OFF: hostStartDefault reads BCD_EPIC_HOST_START with fallback false',
      'static var hostStartDefault: Bool { MadeiraConfig.flag("BCD_EPIC_HOST_START", fallback: false) }' in code(facade))
check('BCDEpic.flag(_:entry:) keeps "on" as its default for every other switch and takes the fallback only from a caller that names one',
      'static func flag(_ name: String, entry: LibraryEntry, fallback: Bool = true) -> Bool {' in sc
      and 'return MadeiraConfig.flag(name, fallback: fallback)' in sc
      and 'Self.flag("BCD_EPIC_SIGNIN", entry: entry)' in sc and 'Self.flag("BCD_EPIC_HOST", entry: entry)' in sc)
host_block = re.search(r'if Self\.flag\("BCD_EPIC_HOST_START", entry: entry, fallback: Self\.hostStartDefault\) \{(.*?)\n        \}', sc, re.S)
check('the hold happens only inside that flag, once', host_block is not None and sc.count('LibraryEntry.holdEpicHostStart(') == 1
      and host_block.start() > sc.index('func startThroughHost') and sc.index('LibraryEntry.holdEpicHostStart(') > host_block.start())
if host_block:
    hb = host_block.group(1)
    check('...with the program and arguments of the setenv lines beside it (the host, or explorer.exe with the /desktop=shell host line)',
          'exe: desktop ? "explorer.exe" : Self.hostProgram' in hb and 'args: desktop ? "/desktop=shell,\\(entry.resolution) \\"\\(Self.hostProgram)\\" --epic" : "--epic"' in hb
          and 'setenv("MADEIRA_EXE", "explorer.exe", 1)' in sc and 'setenv("MADEIRA_ARGS", "/desktop=shell,\\(entry.resolution) \\"\\(Self.hostProgram)\\" --epic", 1)' in sc
          and 'setenv("MADEIRA_EXE", Self.hostProgram, 1)' in sc and 'setenv("MADEIRA_ARGS", "--epic", 1)' in sc)
    check('...and says so in the log, with no value', 'BCDEpicLog.write("[epic-launch] host start held (env.BCD_EPIC_HOST_START = 1)")' in hb)
apply_launch = sc[sc.index('func applyLaunch(_ entry: LibraryEntry) {'):]
check('applyLaunch clears any hold right after the request path, before it looks at the entry (a hold of an earlier start never reaches this one)',
      apply_launch.index('unsetenv("BCD_HOST_REQUEST")') < apply_launch.index('LibraryEntry.clearEpicHostStart()')
      < apply_launch.index('guard let record = record(forEntry: entry.id) else { return }'))
env = [l.strip() for l in lc[lc.index('func applyEnvironment() {'):].splitlines() if l.strip()]
check('LibraryEntry.applyEnvironment restores right after its configureLaunch, for its own entry (its first two statements)',
      env[1:3] == ['configureLaunch()', 'Self.restoreEpicHostStart(for: id)'], str(env[:4]))
check('configureLaunch itself is not changed (it still sets the game\'s program; only applyEnvironment puts the host back)',
      'let command = desktop == true ? (exe: "explorer.exe", args: launchArguments) : launchCommand\n        setenv("MADEIRA_EXE", command.exe, 1)\n        setenv("MADEIRA_ARGS", command.args, 1)' in lc)
# check-epic's invariants for this file
check('BCDEpicStart.swift still sets the environment in five places only and unsets only BCD_HOST_REQUEST',
      len([l for l in sc.splitlines() if re.search(r'(?<![un])\bsetenv\(', l)]) == 5
      and sorted(set(re.findall(r'unsetenv\("(\w+)"\)', sc))) == ['BCD_HOST_REQUEST'])
check('the policy allows BCD_EPIC_HOST_START with 0 or 1 only', '"BCD_EPIC_HOST_START",' in policy
      and re.search(r'case "MADEIRA_WINTYPES", "MADEIRA_X64_MACHINE", "MADEIRA_TINY_WINDOW_FIX", "BCD_EPIC_HOST_START"(, "[A-Z_0-9]+")*:[^\n]*\n(\s*//[^\n]*\n)*\s*guard value == "0" \|\| value == "1"', policy) is not None)
catalog = (R / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the generated catalog lists env.BCD_EPIC_HOST_START, off by default (regenerate it in the full tree; the line is the generator\'s)',
      re.search(r'ConfigOption\(key: "env\.BCD_EPIC_HOST_START", [^\n]*kind: \.bool, defaultValue: "0"', catalog) is not None)
lists = json.loads((R / 'recommended/recommendations.json').read_text())
carriers = sorted(r['id'] for r in lists['rules'] if 'BCD_EPIC_HOST_START' in json.dumps(r['config']))
# Round 620: Farming Simulator 22 from the Epic store (5C4SAB) showed the same "host=1" start with "[bcd-host] no report" and Epic's
# sign-in box; its list carries the line too, matched only outside a Steam library.
# Round 626 (agent G1 S6; VV1JGH = 7ED7X7): BioShock Remastered from Epic, the same start through BCD Host.
# Round 630 (agent R S11): GTA V Enhanced and Legacy outside Steam (an Epic copy lost BCD Host's start the same way); the key is read only
# by the Epic start (BCDEpicStart), so the rows' other copies are untouched.
check('only the BioShock Infinite, BioShock Remastered, Farming Simulator 22 (Epic) and (round 630) GTA V outside Steam lists set it, from build 598',
      carriers == ['bioshock-infinite-epic', 'bioshock-remastered-epic', 'farming-simulator-22-epic', 'gta5e-other', 'gta5l-other'] and all({'line': 'env.BCD_EPIC_HOST_START = 1', 'minBuild': 598} in
      next(r for r in lists['rules'] if r['id'] == i)['config'] for i in carriers))
rules = (R / 'build/tools/autotune_rules.py').read_text()
check('auto-tune: the rule epic-host-start-lost adds the line after an Epic game with the EOS SDK left with exit code 1 although the start line chose the host and the host left no report, never twice',
      '"id": "epic-host-start-lost"' in rules and '"lines": ["env.BCD_EPIC_HOST_START = 1"]' in rules
      and 's("Load module EOSSDK-Win32-Shipping.dll")' in rules and 's("[epic-launch] start", "host=1")' in rules and 's("[bcd-host] no report")' in rules
      and '"none": [s("[epic-launch] host start held")]' in rules and '"outcomes": ["exited-early"]' in rules)
exp = R / 'tests/fixtures/autotune/bioshock-infinite-epic-host-lost-2026-10-24.expect'
check('the fixture is there and expects the line', exp.is_file() and json.loads(exp.read_text())['next'] == ['env.BCD_EPIC_HOST_START = 1']
      and json.loads(exp.read_text())['matched'] == ['epic-host-start-lost'])
workflow = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-epic-host-hold.py' in workflow)

# ------------------------------------------------------------------ the hold's code, compiled
swiftc = shutil.which('swiftc')
b = library.index('// epic-host-hold:begin')
e = library.index('// epic-host-hold:end')
snippet = library[b:e]
HARNESS = '''import Foundation
struct LibraryEntry {
''' + snippet + '''
}
func env(_ n: String) -> String? { getenv(n).map { String(cString: $0) } }
var failed = 0
func T(_ c: Bool, _ what: String, line: Int = #line) { if !c { print("FAIL line \\(line): \\(what)"); failed += 1 } }
let a = UUID(), b = UUID()
setenv("MADEIRA_EXE", "C:\\\\game\\\\g.exe", 1); setenv("MADEIRA_ARGS", "-epicapp=x", 1)
// nothing held: untouched
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "C:\\\\game\\\\g.exe" && env("MADEIRA_ARGS") == "-epicapp=x", "no hold leaves both as they are")
// a hold for this entry: set once, spent
LibraryEntry.holdEpicHostStart(entry: a, exe: "C:\\\\windows\\\\system32\\\\bcdhost.exe", args: "--epic")
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "C:\\\\windows\\\\system32\\\\bcdhost.exe" && env("MADEIRA_ARGS") == "--epic", "the held values are set")
setenv("MADEIRA_EXE", "C:\\\\game\\\\g.exe", 1); setenv("MADEIRA_ARGS", "-epicapp=x", 1)
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "C:\\\\game\\\\g.exe" && env("MADEIRA_ARGS") == "-epicapp=x", "a hold is spent by its restore: the second does nothing")
// a hold for another entry: nothing set, spent
LibraryEntry.holdEpicHostStart(entry: b, exe: "h.exe", args: "--epic")
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "C:\\\\game\\\\g.exe" && env("MADEIRA_ARGS") == "-epicapp=x", "another entry's hold sets nothing")
LibraryEntry.restoreEpicHostStart(for: b)
T(env("MADEIRA_EXE") == "C:\\\\game\\\\g.exe", "...and it was spent by that restore")
// clear spends a hold
LibraryEntry.holdEpicHostStart(entry: a, exe: "h.exe", args: "--epic")
LibraryEntry.clearEpicHostStart()
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "C:\\\\game\\\\g.exe" && env("MADEIRA_ARGS") == "-epicapp=x", "a cleared hold sets nothing")
// a later hold replaces an earlier one
LibraryEntry.holdEpicHostStart(entry: a, exe: "first.exe", args: "1")
LibraryEntry.holdEpicHostStart(entry: a, exe: "second.exe", args: "2")
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "second.exe" && env("MADEIRA_ARGS") == "2", "the later hold wins")
// desktop form
LibraryEntry.holdEpicHostStart(entry: a, exe: "explorer.exe", args: "/desktop=shell,1280x720 \\"C:\\\\windows\\\\system32\\\\bcdhost.exe\\" --epic")
LibraryEntry.restoreEpicHostStart(for: a)
T(env("MADEIRA_EXE") == "explorer.exe" && env("MADEIRA_ARGS")!.hasSuffix("bcdhost.exe\\" --epic"), "the desktop form is set whole")
if failed == 0 { print("hold: ok") }
exit(failed == 0 ? 0 : 1)
'''
if swiftc:
    with tempfile.TemporaryDirectory(prefix='epic-hold-') as tmp:
        main = Path(tmp) / 'main.swift'
        main.write_text(HARNESS)
        exe = Path(tmp) / 'hold'
        r = subprocess.run([swiftc, '-O', '-o', str(exe), str(main)], capture_output=True, text=True)
        check('the hold\'s code compiles on the host', r.returncode == 0, r.stderr[-2500:])
        if r.returncode == 0:
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            print(run.stdout.rstrip())
            check('no hold changes nothing; a hold for the entry sets both once; another entry\'s, a cleared and a replaced hold behave', run.returncode == 0)
else:
    print('note: no swiftc; the hold\'s code was not run')
sys.exit(0 if ok else 1)
