#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A controller whose extendedGamepad object was replaced is read again (app/Madeira/GamepadInput.swift; Madeira #517, 58c8990).

GameController can hand a controller a new extendedGamepad object while the old one goes on reporting buttons with its sticks
at rest (an upstream device log: buttons and triggers reached the game, the sticks never left zero). BCD's slot kept the
profile it saw when the pad joined. Now every 2 s (and on each refresh) a slot whose controller's live profile is another
object reads the live one. It acts only on a replaced object; MADEIRA_XINPUT_PROFILE_CHECK = 0 restores the old way.

  1. The decision, GamepadInput.staleProfileSlots, cut out of the production file and compiled with stand-in classes: a slot
     with the live object is left alone; a replaced one is named with the new object; a controller with no slot, an empty live
     list and a slot beyond the profiles are not; several replaced slots come back in the live order.
  2. The wiring, from the source: the switch is read like the file's other switches and defaults on; the 2 s timer runs on the main
     actor and only compares (the players' card, system gestures and rumble slots are not run again); the slots are touched on the
     queue only; the join and the replacement share attach(), which clears the old handler; the replacement log is capped; the
     refresh runs the check before it admits new controllers; the stick diagnostic is log-only (it writes no part of the state).
  3. The catalog lists the switch, on by default; CI runs this check.
Linux hosts without Swift run the source checks (CI runs the compile with --require-swift)."""
from pathlib import Path
import argparse
import os
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--require-swift', action='store_true')
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
src = (root / 'app/Madeira/GamepadInput.swift').read_text()
catalog = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()
failures = 0


def check(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def cut(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a)]


decision = cut(src, '    static func staleProfileSlots(', '    /// A controller that already has a slot but a different extendedGamepad')
start_fn = cut(src, '    @MainActor func start() {', '    /// Slot i reads `profile`')
recheck = cut(src, '    @MainActor private func recheckProfiles() {', '    /// The slots whose controller is live')
replace = cut(src, '    private func replaceStaleProfiles(', '    @MainActor private func refreshControllers() {')
refresh = cut(src, '    @MainActor private func refreshControllers() {', '    private func setActive(_ value: Bool) {')
attach = cut(src, '    private func attach(_ profile: GCExtendedGamepad, slot i: Int) {', '    /// madeira-bcd (Madeira #517): the 2 s look.')

check('static let profileCheck: Bool = flag("MADEIRA_XINPUT_PROFILE_CHECK")' in src
      and src.count('static func flag(_ name: String) -> Bool {\n        (MadeiraConfig.get("env.\\(name)") ?? ProcessInfo.processInfo.environment[name]) != "0"') == 1,
      'the switch is read like the file\'s others and is on unless it says 0')
check('if Self.profileCheck {\n            profileTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true)' in start_fn
      and 'MainActor.assumeIsolated { self?.recheckProfiles() }' in start_fn, 'a 2 s timer on the main actor, only with the switch')
check('GCController.controllers()' in recheck and 'queue.async { [self] in replaceStaleProfiles(live) }' in recheck
      and 'refreshControllers' not in recheck.replace('the rest of refreshControllers', '').replace('as refreshControllers does', ''),
      "the 2 s look captures the live profiles on main and hands them to the queue; it does not run the refresh again")
check(recheck.count('controllers') == 1 and 'self.controllers' not in recheck and 'profiles[' not in recheck,
      'the slots (controllers, profiles) are not read on the main thread')
check(replace.startswith('    private func replaceStaleProfiles(') and 'guard Self.profileCheck else { return }' in replace
      and 'attach(profile, slot: i)' in replace and 'profileLogsLeft[i] > 0' in replace and 'profileLogsLeft[i] -= 1' in replace,
      'the replacement is off with the switch, goes through attach(), and logs at most eight times a slot')
check('profiles[i]?.valueChangedHandler = nil' in attach and 'profiles[i] = profile' in attach and 'profile.valueChangedHandler = {' in attach
      and attach.index('profiles[i]?.valueChangedHandler = nil') < attach.index('profiles[i] = profile'),
      "attach() clears the old object's handler, then installs the new one")
join = cut(refresh, 'controllers[i] = controller', 'fputs("[xinput] ml1920 slot=\\(i) connected')
check('attach(profile, slot: i)' in join and 'profiles[i] = profile' not in join and 'profile.valueChangedHandler' not in join,
      'a joining pad uses attach() too (the same two lines as before)')
check(refresh.index('replaceStaleProfiles(live)') < refresh.index('for (controller, profile) in live {'),
      'the refresh checks the profiles of controllers that already have a slot before it admits new ones')
diag = cut(src, 'if stickDiagLeft[i] > 0 {', '// The library front end navigates')
check('state.' not in re.sub(r'abs\(Int\(state\.\w+\)\)', '', diag) and 'LogStore.shared.log("[xinput] ml2190 app slot=' in diag
      and 'stickDiagLeft[i] -= 1' in diag, 'the stick diagnostic reads the state and writes the log only, at most eight times a slot')
check(re.search(r'ConfigOption\(key: "env\.MADEIRA_XINPUT_PROFILE_CHECK", [^\n]*kind: \.bool, defaultValue: "1"', catalog) is not None,
      'the settings catalog lists env.MADEIRA_XINPUT_PROFILE_CHECK, on by default')
check('run: python3 tests/host/check-gamepad-profile.py' in workflow, 'CI runs this check')

main = r'''
import Foundation
final class GCController {}
final class GCExtendedGamepad {}
''' + 'enum GamepadInput {\n' + decision + '}\n' + r'''
var failed = 0
func expect(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failed += 1 } }
let c0 = GCController(), c1 = GCController(), c2 = GCController()
let p0 = GCExtendedGamepad(), p1 = GCExtendedGamepad(), p2 = GCExtendedGamepad(), q1 = GCExtendedGamepad(), q0 = GCExtendedGamepad()

var r = GamepadInput.staleProfileSlots(controllers: [c0, c1, nil, nil], profiles: [p0, p1, nil, nil], live: [(c0, p0), (c1, p1)])
expect(r.isEmpty, "slots that read the live objects: nothing to do")
r = GamepadInput.staleProfileSlots(controllers: [c0, c1, nil, nil], profiles: [p0, p1, nil, nil], live: [(c0, p0), (c1, q1)])
expect(r.count == 1 && r[0].slot == 1 && r[0].profile === q1, "a replaced profile: the slot is named with the live object")
r = GamepadInput.staleProfileSlots(controllers: [c0, c1, nil, nil], profiles: [p0, p1, nil, nil], live: [(c0, q0), (c1, q1)])
expect(r.map { $0.slot } == [0, 1] && r[0].profile === q0 && r[1].profile === q1, "two replaced: both, in the live order")
r = GamepadInput.staleProfileSlots(controllers: [c0, c1, nil, nil], profiles: [p0, p1, nil, nil], live: [(c1, q1), (c0, q0)])
expect(r.map { $0.slot } == [1, 0], "...in the live list's order, whatever the slots")
r = GamepadInput.staleProfileSlots(controllers: [c0, nil, nil, nil], profiles: [p0, nil, nil, nil], live: [(c2, p2)])
expect(r.isEmpty, "a controller with no slot is the join's business, not this check's")
r = GamepadInput.staleProfileSlots(controllers: [c0, c1, nil, nil], profiles: [p0, p1, nil, nil], live: [])
expect(r.isEmpty, "no live controller: nothing")
r = GamepadInput.staleProfileSlots(controllers: [nil, nil, nil, nil], profiles: [nil, nil, nil, nil], live: [(c0, p0)])
expect(r.isEmpty, "no slot taken: nothing")
r = GamepadInput.staleProfileSlots(controllers: [nil, c0, nil, nil], profiles: [nil, p0, nil, nil], live: [(c0, p0)])
expect(r.isEmpty, "a controller in slot 1 that reads its live profile: nothing")
r = GamepadInput.staleProfileSlots(controllers: [nil, c0, nil, nil], profiles: [nil, nil, nil, nil], live: [(c0, p0)])
expect(r.count == 1 && r[0].slot == 1 && r[0].profile === p0, "a slot with a controller and no profile reads the live one")
r = GamepadInput.staleProfileSlots(controllers: [c0, nil, nil, nil, c1], profiles: [p0, nil, nil, nil], live: [(c1, q1)])
expect(r.isEmpty, "a slot beyond the profiles is skipped, not a crash")
if failed > 0 { print("\(failed) failure(s)"); exit(1) }
'''

compiler = os.environ.get('SWIFTC') or shutil.which('swiftc')
if compiler:
    with tempfile.TemporaryDirectory(prefix='bcd-gamepad-profile-') as directory:
        path = Path(directory) / 'main.swift'
        path.write_text(main)
        binary = Path(directory) / 'profile'
        built = subprocess.run([compiler, str(path), '-o', str(binary)], capture_output=True, text=True)
        check(built.returncode == 0, 'the production decision compiles with stand-in controller classes')
        if built.returncode:
            print(built.stderr[-3000:])
        else:
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            print(result.stdout, end='')
            check(result.returncode == 0, 'the decision behaves')
elif args.require_swift:
    raise SystemExit('Swift compiler required for the profile decision')
else:
    print('SKIP: Swift compiler unavailable locally; macOS CI runs this check with --require-swift')

if failures:
    raise SystemExit(f'{failures} check(s) failed')
print('All gamepad-profile checks passed')
