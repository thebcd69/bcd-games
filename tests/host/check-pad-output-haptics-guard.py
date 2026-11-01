#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Every CoreHaptics call of BCD's controller rumble is guarded (round 626, Sable PVTSYM; no device, no Xcode project).

A call on the player or engine of a haptic engine iOS has already stopped raises NSException 'com.apple.CoreHaptics'
("checkEngineRunning: self.running") instead of returning an error; uncaught, it ended the app ("Terminating app due to
uncaught exception"). app/Madeira/PadOutput.m sends every such call through MadeiraHapticTry (@try/@catch) and drops that
handle's engine when it raised, so the next rumble builds a fresh one. This check reads the source:
  - MadeiraHapticTry catches NSException and says so once in a while ([hidpad-out] ml2108);
  - every line that calls stopAtTime:, startAtTime:, sendParameters:, stopWithCompletionHandler:, startAndReturnError: or
    createPlayerWithPattern: does it inside MadeiraHapticTry(...);
  - a failed stop, start or level send drops the engine (dropEngine:), and dropEngine: forgets the engine and player.
"""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
src = (root / "app/Madeira/PadOutput.m").read_text()
ok = True


def check(what, cond):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    ok = ok and bool(cond)


fn = src[src.index("static BOOL MadeiraHapticTry("):]
fn = fn[:fn.index("\n}\n") + 3]
check("MadeiraHapticTry runs the call inside @try and catches NSException", "@try {" in fn and "@catch (NSException *e)" in fn
      and "return YES;" in fn and "return NO;" in fn)
check("it logs what was raised, at most 8 times ([hidpad-out] ml2108)", "[hidpad-out] ml2108 haptic call raised" in fn and "said++ < 8" in fn)
impl = src[src.index("@implementation MadeiraRumble"):]
impl = impl[:impl.index("@end")]
calls = ("stopAtTime:", "startAtTime:", "sendParameters:", "stopWithCompletionHandler:", "startAndReturnError:", "createPlayerWithPattern:")
lines = [l for l in impl.splitlines() if any(c in l for c in calls) and not l.strip().startswith(("//", "/*", "*"))]
bare = [l.strip() for l in lines if "MadeiraHapticTry(" not in l]
check("every CoreHaptics call of MadeiraRumble (%d lines) is inside MadeiraHapticTry" % len(lines), lines and not bare)
for l in bare:
    print("     unguarded: " + l)
drop = impl[impl.index("- (void)dropEngine:(int)i"):]
drop = drop[:drop.index("\n}\n")]
check("dropEngine: forgets the engine and the player and stops the engine through the guard",
      "_engine[i] = nil;" in drop and "_player[i] = nil;" in drop and "_playing[i] = NO;" in drop and "MadeiraHapticTry(" in drop)
push = impl[impl.index("- (void)push"):]
push = push[:push.index("\n}\n")]
check("push drops the engine when a stop or a level send raised", push.count("[self dropEngine:i];") >= 4)
stop = impl[impl.index("- (void)stop\n"):]
stop = stop[:stop.index("\n}\n")]
check("stop drops the engine when its stop raised", "[self dropEngine:i]" in stop)
check("sendLevel:to: answers whether the call went through", "- (BOOL)sendLevel:(float)level to:" in impl)
rules = (root / "build/tools/autotune_rules.py").read_text()
rule = rules[rules.index('"id": "corehaptics-uncaught-exception"'):]
rule = rule[:rule.index("\n    },")]
check("auto-tune knows the old crash (fixedIn 626)", '"fixedIn": 626' in rule and
      "Terminating app due to uncaught exception 'com.apple.CoreHaptics'" in rule)
check("the fixture proves the rule", (root / "tests/fixtures/autotune/sable-corehaptics-uncaught-2026-10-30.expect").exists())
print("PASS: rumble haptics guard" if ok else "FAILED: rumble haptics guard")
sys.exit(0 if ok else 1)
