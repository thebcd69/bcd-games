#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Round 624 (agent B S1b, Animal Well 4S7NA9 / RK9NPZ / 37F86Z): one "[touch] hint:" log line when a game has presented for 45 s with no
pad, keyboard, mouse or touch control. Log only: the line changes nothing on screen (an on-screen text is the owner's decision), once per
game start, after the session's other watchers. Round 625 (owner's item 18): the same on screen for 6 s when no finger has touched
the game either (env.MADEIRA_NO_INPUT_HINT = 0: log only).
"""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[2]
src = (root / 'app/Madeira/Library.swift').read_text()
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


fn = src[src.index('    private func watchNoInputHint() {'):]
fn = fn[:fn.index('\n    }\n') + 7]
check('called once a poll, right after the Dock watcher, while the Wine process runs',
      src.count('watchNoInputHint()') == 2 and '            watchDockGameEnd()\n            watchNoInputHint()\n' in src)
check('once per game start (noInputHintFor), after 45 s and 60 presents, not while launching',
      'guard !launching, let id = current, noInputHintFor != id, Date().timeIntervalSince(launchStarted) > 45,' in fn
      and 'madeira_get_present_count() > launchPresent + 60 else { return }' in fn and 'noInputHintFor = id' in fn)
check('only with no pad, no keyboard, no mouse and no touch control',
      'guard pads == 0, !keyboard, mice == 0, touch.controls.isEmpty else { return }' in fn
      and 'GCController.controllers().count' in fn and 'GCKeyboard.coalesced != nil' in fn and 'GCMouse.mice().count' in fn)
check('the "[touch] hint:" line first, whatever follows; no setting is changed',
      fn.count('LogStore.shared.log(') == 2 and '"[touch] hint: game presenting' in fn
      and fn.index('"[touch] hint: game presenting') < fn.index('sessionMessage = words')
      and not re.search(r'TouchControlsModel\.shared\.\w+\s*=|UserDefaults', fn))
# Round 625 (owner's item 18): the on-screen text, only when no finger has touched the game in this session, nothing else is on the
# session line and the key is not 0; gone after 6 s unless something else replaced it.
check('round 625: on screen only with no touch on the game, an empty session line and the key on (default 1)',
      'guard MetalBackedView.touchesSinceLaunch == 0, sessionMessage.isEmpty, MadeiraConfig.flag("MADEIRA_NO_INPUT_HINT") else { return }' in fn
      and 'sessionMessage = words' in fn and 'DispatchQueue.main.asyncAfter(deadline: .now() + 6)' in fn
      and 'if self?.sessionMessage == words { self?.sessionMessage = "" }' in fn
      and 'static let noInputWords = "No controller or touch controls are on. Open the menu and turn on Touch controls."' in src)
_cv = (root / 'app/Madeira/ContentView.swift').read_text()
_tb = _cv[_cv.index('    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {\n        Self.touchesSinceLaunch'):][:400]
check('round 625: every finger on the game view counts before any filter; a new session starts at 0',
      'static var touchesSinceLaunch = 0' in _cv and 'Self.touchesSinceLaunch += touches.count' in _tb
      and 'MetalBackedView.touchesSinceLaunch = 0' in src[src.index('MetalBackedView.presentCountAtLaunch = launchPresent'):][:300])
_cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('round 625: the key is in the settings catalog (bool, default 1)',
      re.search(r'"env\.MADEIRA_NO_INPUT_HINT": \{[^}]*?"kind": "bool", "default": "1"', _cat, re.S) is not None)
check('GameController is imported the way the controller hub imports it', '@preconcurrency import GameController' in src)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-no-input-hint.py' in wf)
print('PASS: no-input hint' if ok else 'FAIL: no-input hint')
raise SystemExit(0 if ok else 1)
