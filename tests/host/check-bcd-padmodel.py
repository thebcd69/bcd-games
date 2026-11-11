#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Russell Barton: code taken from https://github.com/thebcd69/bcd-games (pull request #46 by rbarton124)
# under GPL-3.0-or-later with the Madeira Converter Exception and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""The gamepad model picker (round 604): what a game sees as player 1's controller.

The game page's Controller API row and the in-game menu's offer five choices in plain words: the Xbox controller (XInput, the default
and what every game gets today; a 360 and an One are the same to a game), the Xbox 360 controller (the same XInput pad plus the device
Windows lists for a wired 360 pad, for games that look for it first: Far Cry 4), a DualSense, a generic gamepad (what DirectInput games
list) and "the controller itself" (a DualSense when the paired pad is one). They are the values of `env.MADEIRA_PAD_MODE` (none,
xbox360, dualsense, generic, hid): the picker only names them. A DualShock 4 is written down (BCDPadModel.dualShock) but not offered:
this build's HID controller has no DualShock 4 identity yet, and a check below keeps the two from drifting apart.

Source checks (every host):
  1. BCD's licence header on the new file, the harness and this check; BCDPadModel.swift is Foundation only and in the preview build.
  2. What reaches a game: GamepadInput accepts exactly the modes the picker writes (hid, dualsense, generic, xbox360; the Xbox 360
     identity keeps player 1 on XInput), the wineserver's identity table has each and no DualShock 4, and BCDPadModel offers none
     while it has none (dualShockServed is false exactly then).
  3. The game page and the in-game menu offer the model's choices; the lines they write are the old ones.
  4. CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure): tests/host/bcd_padmodel_checks.swift.
Build (xcrun; --require-swift makes a missing Xcode a failure): the preview build compiles.

  check-bcd-padmodel.py [--require-swift] [--skip-preview]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bcd_input_kit import APP, R, Checks, code, func, imports  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--skip-preview", action="store_true")
args = ap.parse_args()
k = Checks(args)
check = k.check

model = (APP / "BCDPadModel.swift").read_text()
harness = (R / "tests/host/bcd_padmodel_checks.swift").read_text()
gamepad = (APP / "GamepadInput.swift").read_text()
page = (APP / "BCDGamePageLive.swift").read_text()
session = (APP / "BCDSessionLive.swift").read_text()
ids = (R / "build/hidpad/hidpad_ids.h").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()

# ---- 1. licence and layers ---------------------------------------------------------------------
k.licence([("BCDPadModel.swift", model), ("bcd_padmodel_checks.swift", harness)], __file__)
check(imports(model) == ["Foundation"], "BCDPadModel.swift imports Foundation only")
check(not [w for w in ("GamepadInput", "UIKit", "SwiftUI", "MadeiraConfig", "GameProfile", "winios_", "LogStore") if w in code(model)], "... and names no engine type")
check(not re.search(r'ENGINE_BOUND[^\n]*"BCDPadModel\.swift"', tool), "it is built into the preview app")
check(project.count("/* BCDPadModel.swift in Sources */") == 2 and project.count("path = BCDPadModel.swift;") == 1, "the Xcode project builds it")

# ---- 2. what reaches a game ---------------------------------------------------------------------------------
begin = func(gamepad, "@MainActor func beginPadSession()")
check('["hid", "dualsense", "generic", "xbox360"].contains(mode.value)' in begin and 'case "dualsense", "generic", "xbox360": kind = mode.value' in begin,
      "GamepadInput accepts hid, dualsense, generic and xbox360 (and no ds4); the last three are the identity itself")
check('let keepXInput = kind == "xbox360" ||' in begin, "the Xbox 360 identity keeps player 1 on XInput (it is the device beside an XInput pad)")
check(all('"%s"' % kind in ids for kind in ("dualsense", "generic", "xbox360")), "the wineserver's identity table has each identity the app exports")
check('"ds4"' not in code(gamepad), "... and does not name a DualShock 4")
check('MadeiraConfig.gameValue("env.MADEIRA_HIDPAD_XINPUT") == "1" || Self.optIn("MADEIRA_HIDPAD_XINPUT")' in begin,
      "HID mode's keep-XInput switch is also read from the game's own file, as the mode and MADEIRA_PAD_EARLY_SLOT are (the "
      "game's env lines reach the environment only after the session's controller path is chosen)")
served = "static let dualShockServed = true" in model
check(served == ('"ds4"' in ids), "DualShock 4 is offered exactly when the wineserver's identity table has one (it has none: " + ("served" if served else "not served") + ")")
check("static let dualShockServed = false" in model, "this build serves no DualShock 4")
check("hidpad_identities" in ids and ids.count("HIDPAD_KIND_") >= 3, "(sanity: the identity table this was compared with is the real one)")

# ---- 3. where it is offered ---------------------------------------------------------------------------------
check("BCDPadModel.choices().map { ($0.line, $0.title) }, note: BCDPadModel.pageNote" in page and 'GamepadInput.padModeKey, "Controller API"' in page,
      "the game page's Controller API row offers the model's choices, writing the same line")
choices = func(session, "static func choices(_ current: String)")
check("BCDPadModel.choices().map" in choices and '$0 == .xbox ? "xinput" : $0.line' in choices and "modes.append((current, current))" in choices,
      "the in-game menu's offer is the model's, with a value typed into the file by hand still listed")
check('case "dualsense", "generic", "xbox360": return session == mode' in func(session, "static func isLive(_ mode: String)"),
      "what is live in the session is the identity the session made")
ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("python3 tests/host/check-bcd-padmodel.py" in ci.read_text(), "CI runs this check")

k.harness("the gamepad model picker", ["BCDPadModel.swift"], "bcd_padmodel_checks.swift")
k.preview("with the picker's file")
k.finish("gamepad model picker")
