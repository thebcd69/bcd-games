#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Gyro aim (round 603): turning the phone aims the game, as the mouse or as the controller's right stick.

Per game, off until its player turns it on (the game page's Gyro aim, the in-game menu's Controls › Gyro aim), with a
sensitivity, a dead zone, inversion, a choice of which vertical the turning is measured around, and "only while this
controller button is held". CoreMotion's device motion drives it; the mouse gets whole relative counts through the
controller right-stick mouse's own exit (HardwareInput.postRelative), the stick is merged into player 1 in GamepadInput.

Source checks (every host):
  1. BCD's licence header on the new files, the harness and this check.
  2. Layers: BCDGyroAim.swift is Foundation only (no sensor, no engine); only BCDGyroLive.swift imports CoreMotion; it is out
     of the preview build; the Xcode project builds both.
  3. Off means nothing exists: the sensor is made only when a game that turned the gyro on is being played with no menu over
     it, and stopped when any of that stops; the hooks in GamepadInput are static flags that are false until then; the
     session view's hook only reads the game's six stored strings; no timer, display link or animation anywhere.
  4. The choices live with the game's path in the app's own preferences, not in its config file (no change to the
     fingerprint auto-tune compares, nothing for a recommended list to overwrite).
  5. The game page has the Gyro aim section, the in-game menu's Controls tab too; CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure): tests/host/bcd_gyro_checks.swift: the lines a game's
settings make, which way a turn of the phone aims in each of the four ways of holding it (and tilted back), the dead zone, the mouse
counts without rounding losses, the stick with the floor past a game's dead zone, the hold gate and the merge with a physical stick.
Build (xcrun; --require-swift makes a missing Xcode a failure): the preview build compiles with the plain file.

  check-bcd-gyro.py [--require-swift] [--skip-preview]
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
ap.add_argument("--skip-preview", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)[-2500:]) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    out = []
    for line in text.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


def func(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
aim_src = (APP / "BCDGyroAim.swift").read_text()
live_src = (APP / "BCDGyroLive.swift").read_text()
harness = (R / "tests/host/bcd_gyro_checks.swift").read_text()
gamepad = (APP / "GamepadInput.swift").read_text()
session = (APP / "BCDSessionLive.swift").read_text()
page = (APP / "BCDGamePageLive.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()

# ---- 1. licence ---------------------------------------------------------------------------------
for name, text in (("BCDGyroAim.swift", aim_src), ("BCDGyroLive.swift", live_src), ("bcd_gyro_checks.swift", harness)):
    check(text.startswith(HEADER), f"{name}: BCD's licence header")
check(Path(__file__).read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)\n"),
      "this check: BCD's licence header")

# ---- 2. layers ----------------------------------------------------------------------------------
aim = code(aim_src)
live = code(live_src)
check(re.findall(r"^import (\w+)", aim, re.M) == ["Foundation"], "BCDGyroAim.swift imports Foundation only")
named = sorted({w for w in ("CMMotionManager", "CoreMotion", "UIKit", "SwiftUI", "GamepadInput", "HardwareInput", "LibraryModel", "MadeiraConfig",
                            "GameProfile", "UserDefaults", "winios_", "LogStore") if w in aim})
check(not named, "BCDGyroAim.swift names no sensor, engine or storage type", ", ".join(named))
importers = [p.name for p in APP.glob("*.swift") if re.search(r"^import CoreMotion", p.read_text(), re.M)]
check(importers == ["BCDGyroLive.swift"], "only BCDGyroLive.swift imports CoreMotion", importers)
check(re.search(r'ENGINE_BOUND\s*\|=\s*\{[^}]*"BCDGyroLive\.swift"', tool) is not None, "BCDGyroLive.swift is in ui-preview.py's ENGINE_BOUND")
check(not re.search(r'ENGINE_BOUND[^\n]*"BCDGyroAim\.swift"', tool), "BCDGyroAim.swift stays in the preview build")
for name in ("BCDGyroAim.swift", "BCDGyroLive.swift"):
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1, f"{name} is built by the Xcode project")

# ---- 3. off means nothing ------------------------------------------------------------------------
check(live.count("CMMotionManager()") == 1, "the sensor is made in one place")
start = func(live, "private func startIfNeeded(")
check("CMMotionManager()" in start and "guard motion == nil else { return }" in start, "... which is the start, once")
refresh = func(live, "@MainActor func refresh()")
check("let on = wanted.isOn && playing && entry != nil" in refresh, "it starts only for a game whose gyro is on, while it is played")
check("let playing = library.current != nil && !library.desktopHome && !library.launching && !library.menu && appActive" in refresh
      and "applicationState == .active" in refresh and "willResignActiveNotification" in live and "didBecomeActiveNotification" in live,
      "... with the game running, no menu over it and the app in front (its own notifications keep that)")
check(re.search(r"if on \{\s*startIfNeeded\(wanted, title: entry\?\.title \?\? \"\"\)\s*\} else \{\s*stopIfNeeded\(\)\s*\}", refresh) is not None,
      "anything else stops it")
check("stopDeviceMotionUpdates()" in func(live, "private func stopIfNeeded()") and "motion = nil" in func(live, "private func stopIfNeeded()"),
      "stopping ends the sensor and drops it")
check("static var stickActive = false" in live and "static var padNeeded = false" in live, "the two flags GamepadInput reads start false")
check("Self.stickActive = on && wanted.target == .stick" in refresh and "Self.padNeeded = on && (wanted.target == .stick || !wanted.hold.isEmpty)" in refresh,
      "... and are true only while the gyro is on")
check("let gyroConnected = i == 0 && BCDGyroLive.stickActive" in gamepad and "guard pad != nil || touchConnected || gyroConnected || aimConnected else {" in gamepad,
      "GamepadInput connects player 1 for the gyro stick only while it drives it")
check("if i == 0, BCDGyroLive.padNeeded, active, keyboardMouse == nil, !LibraryController.shared.ownsInput {\n                BCDGyroLive.shared.padSample(&state)\n            }" in gamepad,
      "... and looks at the controller only then (not in keyboard-and-mouse mode, nor under the library's menu)")
check(code(gamepad).count("BCDGyro") == 3, "GamepadInput names the gyro in exactly its three places (two flags and the call)", code(gamepad).count("BCDGyro"))
hook = func(live_src, "struct BCDGyroSessionHook: ViewModifier")
check("BCDGyroLive.shared.refresh()" in hook and "CMMotionManager" not in hook, "the session view's hook only asks the gyro to look again")
check(session.count(".modifier(BCDGyroSessionHook())") == 1, "... from BCDSessionLiveLayer, once")
for word in ("Timer", "CADisplayLink", "withAnimation", ".animation(", "TimelineView", "repeatForever"):
    check(word not in live and word not in aim, f"nothing runs on its own: no {word}")
check("deviceMotionUpdateInterval = 1.0 / 100.0" in live and "startDeviceMotionUpdates(to: queue)" in live, "device motion at 100 Hz on a queue of its own")
mouse = live[live.index("case .mouse:"):live.index("case .stick:")]
check("guard open, HardwareInput.shared.baseFocused else" in mouse and "HardwareInput.shared.postRelative(counts.dx, counts.dy)" in mouse,
      "the mouse moves only while the game view has the pointer, through the controller right-stick mouse's own exit")

# ---- 4. where the choices live ----------------------------------------------------------------------
check('private static let key = "bcd.gyro"' in live and "UserDefaults.standard" in live, "the choices are in the app's preferences, by the game's path")
check("GameProfile" not in live and "MadeiraConfig" not in live, "not in the game's config file or bcd.cfg")

# ---- 5. where a player finds it -----------------------------------------------------------------------
check("BCDGameSettingGroup(id: \"gyro\", title: \"Gyro aim\", symbol: \"gyroscope\"" in page and "controls(entry, profile), gyro(entry)]" in page,
      "the game page has a Gyro aim section after Controls")
check('case "gyro-aim", "gyro-sens", "gyro-hold", "gyro-invert", "gyro-deadzone", "gyro-turn":' in page
      and "BCDGyroStore.set(entry.windowsPath, setting, value)" in page, "... which writes the game's choices")
check("if let gyro = BCDGyroMenu.section(for: model.activeEntry) { out.append(gyro) }" in session, "the in-game menu's Controls tab has the section")
check("        default:\n            if BCDGyroMenu.handles(row) { BCDGyroMenu.choose(row, value, entry: model.activeEntry); return }" in session,
      "... and takes its choices (in the default case, so no other choice is looked at first)")
default_page = re.search(r'id: "gyro-aim".*?defaultValue: ""', page, re.S)
check(default_page is not None, "gyro aim defaults to Off on the page")
ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("python3 tests/host/check-bcd-gyro.py" in ci.read_text(), "CI runs this check")

# ---- behaviour -------------------------------------------------------------------------------------------
SWIFTC = shutil.which("swiftc")
if not SWIFTC:
    check(not args.require_swift, "swiftc for the behaviour checks", "not found")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-gyro-") as tmp:
        main = Path(tmp) / "main.swift"
        main.write_text(harness)
        exe = Path(tmp) / "check"
        r = subprocess.run([SWIFTC, "-O", str(APP / "BCDGyroAim.swift"), str(main), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the gyro's behaviour checks compile on the host", r.stderr or r.stdout)
        if r.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL") or line.startswith("ALL"):
                    print("  " + line)
            check(r.returncode == 0, "the gyro aims as described", r.stdout + r.stderr)

# ---- build -----------------------------------------------------------------------------------------------
if args.skip_preview:
    print("SKIP: the preview build (--skip-preview)")
elif not shutil.which("xcrun"):
    check(not args.require_swift, "xcrun for the preview build", "not found")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-gyro-preview-") as tmp:
        r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp], capture_output=True, text=True)
        check(r.returncode == 0, "the preview build compiles (the plain gyro file without the engine)", r.stderr or r.stdout)

if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("PASS: gyro aim")
