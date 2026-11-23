#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Players (round 604): controller assignment, the touch controls as a player, rumble for each player.

Games see up to four XInput pads. Until now a controller took the first free slot as it connected and the touch controls were always
Player 1. Now a person can press a button on a controller to make it Player 1, 2, 3 or 4 (Settings › Controls › Controllers, the Players
card; the in-game menu's Controls › Players), say how hard each player's controller rumbles (Full, 75, 50, 25 %, Off), and have the touch
controls play as another player. What they choose is remembered by the controller's name, kind and its number among the same kind. With
nothing chosen every rule gives what it gave before.

Source checks (every host):
  1. BCD's licence header on the new files, the harness and this check.
  2. Layers: BCDPlayers.swift is Foundation only (rules, no engine); BCDPlayersViews.swift is SwiftUI only (no engine type); only
     BCDPlayersLive.swift names GameController and the engine and stays out of the preview build; the Xcode project builds all three.
  3. With nothing chosen nothing changes: the slot a connecting controller takes is the first free slot when no choice is kept; the
     choosing flag is -1 and the touch slot 0 until a person changes them; PadOutput's per-slot factor starts at 1; nothing runs on a timer
     except the 20 seconds a choice waits.
  4. The hooks in GamepadInput and PadOutput are the few lines described (each marked), and a pad changes places only inside the queue
     that owns the slots.
  5. The choices are global and kept in the app's preferences; the card is on the controller screen; the in-game menu has the page; CI.
Behaviour (swiftc on the host; --require-swift makes its absence a failure): tests/host/bcd_players_checks.swift.
Build (xcrun; --require-swift makes a missing Xcode a failure): the preview build compiles with the card and the page.

  check-bcd-players.py [--require-swift] [--skip-preview]
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


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
NEW = ["BCDPlayers.swift", "BCDPlayersViews.swift", "BCDPlayersLive.swift"]
src = {n: (APP / n).read_text() for n in NEW}
harness = (R / "tests/host/bcd_players_checks.swift").read_text()
gamepad = (APP / "GamepadInput.swift").read_text()
padout = (APP / "PadOutput.m").read_text()
padout_h = (APP / "PadOutput.h").read_text()
screen = (APP / "BCDControllerScreen.swift").read_text()
session = (APP / "BCDSessionLive.swift").read_text()
menu = (APP / "BCDInputMenu.swift").read_text()
input_live = (APP / "BCDInputLive.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()

# ---- 1. licence ---------------------------------------------------------------------------------
for name, text in list(src.items()) + [("bcd_players_checks.swift", harness)]:
    check(text.startswith(HEADER), f"{name}: BCD's licence header")
check(Path(__file__).read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)\n"),
      "this check: BCD's licence header")

# ---- 2. layers ----------------------------------------------------------------------------------
rules, views, live = code(src["BCDPlayers.swift"]), code(src["BCDPlayersViews.swift"]), code(src["BCDPlayersLive.swift"])
check(re.findall(r"^import (\w+)", rules, re.M) == ["Foundation"], "BCDPlayers.swift imports Foundation only")
check(not [w for w in ("GameController", "GCController", "GamepadInput", "UIKit", "SwiftUI", "winios_", "LogStore", "UserDefaults") if w in rules],
      "... and names no controller, engine or storage type")
check(re.findall(r"^import (\w+)", views, re.M) == ["SwiftUI"], "BCDPlayersViews.swift imports SwiftUI only")
check(not [w for w in ("GameController", "GCController", "GamepadInput", "winios_", "LogStore", "LibraryModel") if w in views], "... and names no engine type")
check("@preconcurrency import GameController" in live and "madeira_pad_output_set_scale" in live, "BCDPlayersLive.swift is the engine side")
check(re.search(r'ENGINE_BOUND\s*\|=\s*\{[^}]*"BCDPlayersLive\.swift"', tool) is not None, "... and is in ui-preview.py's ENGINE_BOUND")
check(not re.search(r'ENGINE_BOUND[^\n]*"BCDPlayers(Views)?\.swift"', tool), "the rules and the card are built into the preview app")
for name in NEW:
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1, f"{name} is built by the Xcode project")

# ---- 3. nothing chosen, nothing changes ---------------------------------------------------------------
check("nonisolated(unsafe) static var listening = -1" in live, "nobody is choosing: the flag starts at -1")
check("static var touchSlot = BCDPlayersLive.stored().touchSlot" in live and 'if let text = get("touch-player")' in rules and "var touchSlot = 0" in rules,
      "the touch slot is the stored choice, 0 when there is none")
slot = live[live.index("nonisolated static func slot(forKey"):live.index("private var timeout")]
check("guard let key, !s.assigned.isEmpty else { return occupied.firstIndex(of: false) }" in slot, "with no remembered controller a connecting one takes the first free slot")
check("static float g_scale[PAD_SLOTS] = { 1.0f, 1.0f, 1.0f, 1.0f };" in padout, "PadOutput's per-slot factor starts at 1")
check("low *= g_scale[i];" in padout and "high *= g_scale[i];" in padout, "... and multiplies the motors' levels")
check("void madeira_pad_output_set_scale(int slot, float scale);" in padout_h and "if (g_scale[slot] == s) return;" in padout, "a factor is set per slot, clamped to 0...1, and does nothing when it did not change")
check(live.count("DispatchWorkItem {") == 1 and "asyncAfter(deadline: .now() + 20" in live, "the one timer is the 20 seconds a choice waits")
for word in ("Timer.scheduled", "CADisplayLink", "withAnimation", "makeTimerSource", "repeatForever"):
    check(word not in live and word not in views, f"nothing runs on its own: no {word}")

# ---- 4. the hooks ------------------------------------------------------------------------------------------
check("let i = BCDPlayersLive.slot(forKey: playerKeys[ObjectIdentifier(controller)], occupied: controllers.map { $0 != nil })" in gamepad, "refreshControllers asks where a controller goes")
check("let playerKeys = BCDPlayersLive.keys(for: live.map { $0.0 })" in gamepad, "... with the keys made on the main thread")
check("if BCDPlayersLive.listening >= 0, let hit = BCDPlayersLive.pressed(in: profiles) { assignListened(hit) }" in gamepad, "sample() looks at the buttons only while a choice waits")
check("let touchConnected = i == BCDPlayersLive.touchSlot && touchState.connected" in gamepad, "the touch controls connect as the chosen player (slot 0 by default)")
check(code(gamepad).count("BCDPlayersLive") == 6, "GamepadInput names the players in exactly six places", code(gamepad).count("BCDPlayersLive"))
assign = live[live.index("func assignListened"):live.index("@MainActor enum BCDPlayersMenuLive")]
check("controllers.swapAt(i, j)" in assign and "profiles.swapAt(i, j)" in assign and "winios_gamepad_set_vibration(Int32(slot), 0, 0)" in assign
      and "madeira_pad_output_set_slot(Int32(slot), controllers[slot])" in assign and "updateTimer()" in assign,
      "two controllers change places with their slots' rumble and sampling")
check("guard controllers.indices.contains(i), controllers.indices.contains(j), let moved = controllers[i] else { return }" in assign, "... only for slots that exist and a controller that is there")
check("static func pressed(in pads" in live and "waitingForRelease" in live, "a button already down when choosing began is not a choice")

# ---- 5. where the choices live and where they are offered ------------------------------------------------------
check("BCDInputPrefs.shared.lines(BCDInputLines.players, game: BCDInputPrefs.everyone)" in live, "the choices are kept for everyone, in the app's preferences")
check("playersCard" in screen and screen.count("playersCard") >= 5, "the controller screen has the Players card (all its arrangements)")
check("playersPage" in menu and "case playersPage: return playersPageContent(profile, players)" in menu, "the in-game menu has the players' page")
check("if BCDInputMenu.handles(row), BCDInputMenuLive.act(row) { menu?.changed(); return }" in session, "... and its choosing rows act")
check("BCDPlayersLive.shared.reload()" in input_live, "a change reloads the players")
ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("python3 tests/host/check-bcd-players.py" in ci.read_text(), "CI runs this check")

# ---- PadOutput.m compiles for the phone ----------------------------------------------------------------------------------
# (the whole-app typecheck reads Swift only: a block that assigns to a captured parameter was only seen by a compile of the file)
if shutil.which("xcrun"):
    sdk = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["xcrun", "-sdk", "iphoneos", "clang", "-fsyntax-only", "-arch", "arm64", "-isysroot", sdk, "-miphoneos-version-min=17.0", "-fobjc-arc",
                        f"-I{APP}", f"-I{APP}/Winios", "-Wall", str(APP / "PadOutput.m")], capture_output=True, text=True)
    errors = [line for line in r.stderr.splitlines() if "error:" in line]
    check(not errors, "PadOutput.m compiles for the iPhone with the per-player rumble factor", "\n".join(errors[:5]))
else:
    check(not args.require_swift, "xcrun for the PadOutput.m compile", "not found")

# ---- behaviour -------------------------------------------------------------------------------------------
SWIFTC = shutil.which("swiftc")
if not SWIFTC:
    check(not args.require_swift, "swiftc for the behaviour checks", "not found")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-players-") as tmp:
        main = Path(tmp) / "main.swift"
        main.write_text(harness)
        exe = Path(tmp) / "check"
        files = ["BCDKeyNames.swift", "BCDExtraKeys.swift", "BCDInputPrefs.swift", "BCDTurbo.swift", "BCDTouchLook.swift", "BCDTouchAcceleration.swift", "BCDPlayers.swift", "BCDInputActions.swift",
                 "BCDRadial.swift", "BCDAimZone.swift", "BCDGestureMap.swift", "BCDPencil.swift", "BCDInputProfile.swift"]   # the profile routes every feature
        r = subprocess.run([SWIFTC, "-O"] + [str(APP / n) for n in files] + [str(main), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the behaviour checks compile on the host", r.stderr or r.stdout)
        if r.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL") or line.startswith("ALL"):
                    print("  " + line)
            check(r.returncode == 0, "the players behave as described", r.stdout + r.stderr)

# ---- build -----------------------------------------------------------------------------------------------
if args.skip_preview:
    print("SKIP: the preview build (--skip-preview)")
elif not shutil.which("xcrun"):
    check(not args.require_swift, "xcrun for the preview build", "not found")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-players-preview-") as tmp:
        r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp], capture_output=True, text=True)
        check(r.returncode == 0, "the preview build compiles with the card and the page", r.stderr or r.stdout)

if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("PASS: players")
