#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Aim mode, touch gestures and the Apple Pencil as a mouse (round 604): what a finger or a Pencil on the game does beyond the pointer setting.

  - Aim mode: a part of the screen where a finger turns the camera as a mouse does (the right half, the left half, or anywhere no
    touch control is); speed, up-and-down share, invert, and "a tap is a click". Whole mouse counts through the controller
    right-stick mouse's exit, fractions carried.
  - Touch gestures: a tap with two, three or four fingers, a swipe with two or three, a pinch in or out, each bound to one thing
    BCD can do (a key, Alt+Tab, a click, the wheel, BCD's menu, the key bar, a press of a controller button).
  - The Apple Pencil: its tip puts the pointer exactly where it touches, hovering moves it without a click, a squeeze or a double tap
    clicks.
All three are per game, off until the player turns them on. MetalBackedView hands each batch of touches to BCDTouchExtras.filter
first; with all three off that is one static flag.

Source checks (every host):
  1. BCD's licence header on the new files, the harnesses and this check.
  2. Layers: BCDAimZone.swift, BCDGestureMap.swift and BCDPencil.swift are plain values; only BCDTouchExtrasLive.swift touches the engine and
     UIKit's touches, and it stays out of the preview build; the Xcode project builds all four.
  3. Off means nothing: MetalBackedView's four hooks and the two gesture-replacing checks are single lines on one static flag;
     nothing polls or animates; the Pencil's recognisers are added when the feature turns on and taken off when it turns off.
  4. A finger in the aim zone is taken from the older handling and no other; the Pencil's touches are taken only while the Pencil is on;
     gestures take nothing.
  5. The game page and the in-game menu offer them; CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure): tests/host/bcd_aim_checks.swift, bcd_gestures_checks.swift, bcd_pencil_checks.swift.
Build (xcrun; --require-swift makes a missing Xcode a failure): the preview build compiles with the plain files.

  check-bcd-touch-extras.py [--require-swift] [--skip-preview]
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

PLAIN = ["BCDAimZone.swift", "BCDTouchAcceleration.swift", "BCDGestureMap.swift", "BCDPencil.swift"]
src = {n: (APP / n).read_text() for n in PLAIN + ["BCDTouchExtrasLive.swift"]}
harnesses = {n: (R / f"tests/host/bcd_{n}_checks.swift").read_text() for n in ("aim", "gestures", "pencil")}
content = (APP / "ContentView.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()
input_live = (APP / "BCDInputLive.swift").read_text()
page = (APP / "BCDInputPage.swift").read_text()
menu = (APP / "BCDInputMenu.swift").read_text()
profile = (APP / "BCDInputProfile.swift").read_text()

# ---- 1. licence ---------------------------------------------------------------------------------
k.licence(list(src.items()) + [(f"bcd_{n}_checks.swift", t) for n, t in harnesses.items()], __file__)

# ---- 2. layers ----------------------------------------------------------------------------------
check(sorted(imports(src["BCDAimZone.swift"])) == ["CoreGraphics", "Foundation"] and sorted(imports(src["BCDGestureMap.swift"])) == ["CoreGraphics", "Foundation"],
      "BCDAimZone.swift and BCDGestureMap.swift import Foundation and CoreGraphics only")
check(imports(src["BCDPencil.swift"]) == ["Foundation"], "BCDPencil.swift imports Foundation only")
check(imports(src["BCDTouchAcceleration.swift"]) == ["Foundation"], "touch acceleration math needs Foundation only")
for name in PLAIN:
    named = sorted(w for w in ("UIKit", "SwiftUI", "UITouch", "GamepadInput", "HardwareInput", "LibraryModel", "MadeiraConfig", "winios_", "LogStore", "UserDefaults") if w in code(src[name]))
    check(not named, f"{name} names no engine, UI or storage type", ", ".join(named))
check(re.search(r'ENGINE_BOUND\s*\|=\s*\{[^}]*"BCDTouchExtrasLive\.swift"', tool) is not None, "BCDTouchExtrasLive.swift is in ui-preview.py's ENGINE_BOUND")
for name in PLAIN:
    check(not re.search(r'ENGINE_BOUND[^\n]*"%s"' % re.escape(name), tool), f"{name} stays in the preview build")
for name in PLAIN + ["BCDTouchExtrasLive.swift"]:
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1, f"{name} is built by the Xcode project")

# ---- 3. off means nothing ----------------------------------------------------------------------------------
live = code(src["BCDTouchExtrasLive.swift"])
check("nonisolated(unsafe) static var active = false" in live, "the flag MetalBackedView reads starts false")
check("active = aim.isOn || gestures.isOn || pencil.isOn" in func(live, "static func configure("), "... and is true only while one of the three is on")
check("guard active else { return touches }" in func(live, "static func filter("), "with it false the touches go on untouched")
for stage in ("began", "moved", "ended", "cancelled"):
    check(f"guard let touches = BCDTouchExtras.filter(self, touches, .{stage}) else {{ return }}" in content, f"MetalBackedView's touches{stage.capitalize()} asks first (one guard line)")
check(code(content).count("BCDTouchExtras.filter") == 4, "... and nowhere else", code(content).count("BCDTouchExtras.filter"))
check("case 2: if !BCDTouchExtras.replaces(.tap2) { tmAbsoluteClick(" in content and "case 3: if !BCDTouchExtras.replaces(.tap3) { tmAbsoluteClick(" in content
      and "&& !BCDTouchExtras.replaces(.tap2)" in content, "a bound two- or three-finger tap replaces the older right and middle click (three one-line checks)")
check("nonisolated static func replaces(_ gesture: BCDGesture) -> Bool { bound.contains(gesture) }" in live, "... a set lookup")
check("    func mapPoint(_ p: CGPoint) -> (Int32, Int32) {" in content, "mapPoint is the only older line made less private")
for word in ("Timer", "CADisplayLink", "withAnimation", ".animation(", "TimelineView", "repeatForever", "makeTimerSource"):
    check(word not in live, f"nothing runs on its own: no {word}")
pencil = func(live, "@MainActor final class BCDPencilLive")
check("if new.isOn { install() } else { uninstall() }" in pencil and "view.addGestureRecognizer(hover)" in pencil and "view.addInteraction(interaction)" in pencil
      and "view.removeGestureRecognizer(hover)" in pencil and "view.removeInteraction(interaction)" in pencil, "the Pencil's hover and squeeze recognisers come with the feature and go with it")
check("if was && !new.isOn { post(pointer.reset()) }" in pencil, "turning the Pencil off lets a held button go")

# ---- 4. what is taken --------------------------------------------------------------------------------------
aim = func(live, "@MainActor final class BCDAimLive")
check("for t in touches where t.type == .direct" in aim and "touches.subtracting(taken)" in aim, "aim mode takes only the aiming finger's touches, and only fingers")
check("HardwareInput.shared.postRelative(counts.dx, counts.dy)" in aim and "HardwareInput.shared.baseFocused" in aim, "its counts go through the controller right-stick mouse's exit, only while the game view has the pointer")
gesture = func(live, "@MainActor final class BCDGestureLive")
check("func observe(" in gesture and "subtracting" not in gesture and "taken" not in gesture, "gestures watch the fingers and take nothing")
check("for t in touches where t.type == .pencil" in pencil and "rest.remove(t)" in pencil and "preciseLocation(in: view)" in pencil, "the Pencil's touches are taken (at their precise location), a finger's are not")
check("recognizer.zOffset > 0" in pencil, "a hover counts as the Pencil's only when it reports a height (a mouse pointer reports none)")
check("didReceiveSqueeze" in pencil and "squeeze.phase == .ended" in pencil and "didReceiveTap" in pencil, "a squeeze (on its release) and a double tap click")
check(func(live, "static func filter(").index("pencil.handle") < func(live, "static func filter(").index("gestures.observe") < func(live, "static func filter(").index("aim.handle"),
      "the Pencil is looked at first, then gestures watch, then the aim finger is taken")

# ---- 5. where a player finds them ----------------------------------------------------------------------------
check("BCDTouchExtras.configure(aim: profile.aim, gestures: profile.gestures, pencil: profile.pencil, title: title)" in input_live, "the session's hook tells them what the game chose")
for gid, title in (("aim", "Aim mode"), ("gestures", "Touch gestures"), ("pencil", "Apple Pencil")):
    check(f'BCDGameSettingGroup(id: "{gid}", title: "{title}"' in page, f"the game page has {title}")
check('id: "aim-accel"' in page and 'id: "in.aim-accel"' in menu and '"aim-accel"' in profile,
      "Aim's per-game mouse-only acceleration has both settings surfaces and a routed opt-in line")
check('id: "in.aim-zone"' in menu and 'id: "in.pencil"' in menu and 'id: "in.gestures.note"' in menu, "the in-game menu has them (the gestures' bindings are set on the page)")
check("BCDAimSettings.lineNames.contains(line) { return aim }" in profile and "BCDGestureSettings.lineNames.contains(line) { return gestures }" in profile
      and "BCDPencilSettings.lineNames.contains(line) { return pencil }" in profile, "their lines are routed")
check("BCDTouchAcceleration.multiplier" in content and "input.accelTrackpad" in content and "input.accelLook" in content,
      "trackpad and mouse-look share the optional swipe-speed gain")
check('BCDTabletopTouchpad.owns(' in content and 'let pad = padGesture' in content,
      "Duo lower touchpad shares trackpad gesture handling")
ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("python3 tests/host/check-bcd-touch-extras.py" in ci.read_text(), "CI runs this check")

# ---- behaviour and build ------------------------------------------------------------------------------------------
base = ["BCDKeyNames.swift", "BCDExtraKeys.swift", "BCDTurbo.swift", "BCDInputPrefs.swift"]
k.harness("aim mode", ["BCDTouchAcceleration.swift", "BCDAimZone.swift"], "bcd_aim_checks.swift")
k.harness("the touch gestures", base + ["BCDInputActions.swift", "BCDGestureMap.swift"], "bcd_gestures_checks.swift")
k.harness("the Apple Pencil", ["BCDPencil.swift"], "bcd_pencil_checks.swift")
k.preview("with the plain files")
k.finish("aim mode, touch gestures and the Apple Pencil")
