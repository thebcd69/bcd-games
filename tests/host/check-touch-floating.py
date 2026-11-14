#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Floating sticks and six more built-in touch layouts (Porthole port, item 9). No iPhone.

1. The geometry, compiled and run on the host: a floating stick's zone and where its ring is drawn (TouchGamepad.swift,
   BCDFloatingStick), and every new built-in layout on nine screens: complete, every control a supported XInput input, inside the
   editor's clamps and the safe area, none touching another, the overlay's top buttons free; the left-handed one is the controller
   layout turned round.
2. Off path, on the source: the switch is a value of the in-game menu's touch settings, nil (off) by default and in every older file;
   off, the touch layer draws the controls exactly as before, the stick's surface measures from its own frame as before, and the
   overlay's window takes no touch it did not take.
3. The list: the two built-ins stay first and unchanged, the six follow; a game's default is still one of the two; the pad layouts need
   touch XInput like the controller one.
4. Credit, catalog-free wiring, workflow.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
failures = []


def check(what, cond, detail=""):
    print(("PASS: " if cond else "FAIL: ") + what + (f"\n      {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(what)
    return cond


def block(text, start):
    i = text.index(start)
    depth = 0
    for j in range(text.index("{", i), len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise ValueError(start)


content = (APP / "ContentView.swift").read_text()
touch = (APP / "TouchGamepad.swift").read_text()
presets = (APP / "TouchControlPresets.swift").read_text()
controls = (APP / "BCDTouchControls.swift").read_text()
live = (APP / "BCDTouchLive.swift").read_text()
feel = (APP / "BCDSessionFeel.swift").read_text()
feel_live = (APP / "BCDSessionFeelLive.swift").read_text()
menu = (APP / "BCDMenuMore.swift").read_text()

# ---- 1. behaviour
actions = content[content.index("enum ControlAction: Codable"):content.index("final class TouchControlsModel")]
pure_touch = touch.split("// MARK: - Pure touch state", 1)[1].split("// MARK: - UIKit touch lifetime", 1)[0]
pure_touch = pure_touch[pure_touch.index("struct GamepadSample"):]
pure = presets.split("// MARK: - Pure preset state", 1)[1].split("// MARK: - Persistence", 1)[0]
pure = pure[pure.index("struct ControlPreset"):]
swiftc = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not swiftc:
    check("swiftc for the behaviour checks", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-touch-floating-") as tmp:
        main = Path(tmp) / "main.swift"
        floating = block((APP / "BCDFloatingStick.swift").read_text(), "enum BCDFloatingStick {")
        main.write_text("import Foundation\n#if canImport(CoreGraphics)\nimport CoreGraphics\n#endif\n" + actions + pure_touch + pure + floating + "\n"
                        + (R / "tests/host/bcd_touch_floating_checks.swift").read_text())
        exe = Path(tmp) / "run"
        r = subprocess.run([swiftc, "-O", str(main), "-o", str(exe)], capture_output=True, text=True)
        if check("the geometry and the layouts compile on the host", r.returncode == 0, (r.stderr or r.stdout)[-3000:]):
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print("  " + line)
            n = sum(1 for l in r.stdout.splitlines() if l.startswith("PASS"))
            check(f"the Swift harness: {n} checks, none failing", r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:])

# ---- 2. off path
check("the switch is the in-game menu's touch setting, nil (off) by default, dropped when false, said in the saved set's words",
      "var floatingSticks: Bool?" in feel and "if out.floatingSticks == false { out.floatingSticks = nil }" in feel
      and 'if floatingSticks == true { out.append("floating sticks") }' in feel
      and "nonisolated(unsafe) static var floatingSticks = false" in feel and "floatingSticks = false" in block(feel, "static func reset() {"))
apply = block(feel_live, "private func applyAll() {")
check("the session applies it and draws the controls again only when it changes",
      "if BCDTouchFeel.floatingSticks != (current.floatingSticks == true) {" in apply and "BCDTouchFeelModel.shared.colorChanged()" in apply)
layer = block(controls, "struct BCDTouchLayer: View {")
old_each = re.sub(r"\s+", " ", "ForEach(items) { item in BCDTouchControl(item: item, screen: screen, layoutScale: layoutScale, opacity: opacity, "
                  "editing: editing, selected: editing && selected == item.id, input: input, arranging: arranging, look: look) .equatable() }")
check("off (or editing), the layer draws every control as before; on, the sticks' zones go under every other control",
      "var floatingSticks = false" in layer and "if floatingSticks && !editing {" in layer and old_each in re.sub(r"\s+", " ", layer)
      and layer.index("BCDTouchFloatingStick(") < layer.index("items.filter { !$0.action.isControllerStick }"))
view = block(touch, "final class TouchPadView: UIView {")
check("the stick's surface measures from its own frame as before unless it floats (one thumb then, the ring where it lands)",
      "radius = max(1, (floatDiameter ?? bounds.width) * 0.35)" in view and "if let d = floatDiameter {" in view
      and "BCDFloatingStick.centre(touch:" in view and "guard fingers.isEmpty else { continue }" in view)
hits = block(content, "func hitsInteractive(")
check("the overlay's window takes a touch in a stick's zone only while the sticks float (the old test comes first, unchanged)",
      "if BCDTouchFeel.floatingSticks, !editing {" in hits and "BCDFloatingStick.zone(of:" in hits
      and hits.index("if hypot(p.x - cx, p.y - cy) <= r { return true }") < hits.index("if BCDTouchFeel.floatingSticks, !editing {"))
check("the in-game menu has the switch while the controls are shown and the layout has a controller stick",
      'id: "mm.float"' in menu and 'case "mm.float": feel.update { $0.floatingSticks = on ? true : nil }' in menu)
check("BCD's touch layer passes the switch", "floatingSticks: BCDTouchFeel.floatingSticks" in live)

# ---- 3. the list
store = block(presets, "struct ControlPresetStore: Equatable {")
built = store[store.index("static let builtIns"):store.index("static let maxNameLength")]
check("the two built-ins stay first and as they were, the six follow",
      built.index("ControlPresetLayout.xboxID") < built.index("ControlPresetLayout.keyboardID") < built.index("Arrangement.allCases"))
check("a game's default layout is still the controller or the PC keyboard",
      "steam && touchPad ? xboxID : keyboardID" in presets)
check("loading a new built-in lays it out again for the real screen",
      "ControlPresetLayout.Arrangement.allCases.first(where: { $0.id == p.id }).map { ControlPresetLayout.arranged($0, for: screen) }" in presets)
check("the pad layouts need touch XInput, like the controller one",
      "ControlPresetLayout.needsTouchPad($0.id) == false || Self.touchPad" in presets)

# ---- 4. credit and CI
floating_src = (APP / "BCDFloatingStick.swift").read_text()
check("BCDFloatingStick.swift keeps Porthole's licence and copyright; TouchGamepad.swift and TouchControlPresets.swift name Porthole's files",
      floating_src.startswith("// SPDX-License-Identifier: GPL-3.0-or-later\n// Copyright (C) 2026 The Porthole authors.")
      and "BCD Material" not in floating_src and "app/Sources/Core/FloatingStick.swift" in floating_src[:600]
      and "app/Sources/UI/HUD.swift" in touch[:700] and "app/Sources/Core/VirtualPadModel.swift" in presets[:700])
notices = (R / "THIRD-PARTY-NOTICES.md").read_text()
check("their rows in the notices", all(f"| `app/Madeira/{n}` |" in notices for n in ("BCDFloatingStick.swift", "TouchGamepad.swift", "TouchControlPresets.swift")))
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the new file is in the project", "BCDFloatingStick.swift in Sources" in project)
check("the workflow runs this check", "run: python3 tests/host/check-touch-floating.py" in (R / ".github/workflows/build-ipa.yml").read_text())

print("FAILED: floating sticks" if failures else "PASS: floating sticks and more layouts")
sys.exit(1 if failures else 0)
