#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Experimental support for the foldable iPhone, iPhone Duo (BCDFoldable.swift, BCDFoldableLive.swift, BCDFoldableSettings.swift).

Owner's rule (2026-10-25): nothing that works today may change. Every new behaviour runs only on the foldable model (its
model identifier) AND with an experimental switch on; the switch is off by default, also on that model, because nothing here
could be run on the device (Xcode 26.3 has no simulator of it) and the identifier comes from secondary sources. Checks:
  1. The gate, run on the host (BCDFoldable.swift, GuestDisplay.swift and tests/host/bcd_foldable_checks.swift): the model
     table; the switch (unset is off, only "1" turns it on); 56 other model identifiers (every iPhone 17 and 18 generation
     identifier, iPads, the simulator's, look-alikes with a space or another case) give a shut gate for every switch value;
     the inner display's shape and the default Resolution made from it; the scene-size tracker; the log line.
  2. The pure part type-checks against the iOS SDK.
  3. The hooks are the only places the rest of the app mentions any of it, each is one line that tests the gate (or the model)
     first, and the lines they sit beside are as they were: the game view's layout pass, the Resolution screen reading, the
     Settings page. Nothing else in app/ names a BCDFoldable type.
  4. The engine side only follows: it reads the window and the scene, re-frames the control window, refreshes the layer (the
     rotation path), logs. It asks UIKit for no orientation or geometry, reads no UIScreen.main, ends and pauses nothing.
  5. Info.plist and the device families are as they were; the Xcode project builds the three files; the preview build leaves
     the engine side out; no "Madeira" in what a person reads; the workflow runs this check.
tests/host/check-bcd-foldable.py [--require-swift]
"""
import argparse
import plistlib
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
args = ap.parse_args()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what + (("\n     " + str(detail)[-3000:]) if detail and not cond else ""))
    ok = ok and bool(cond)


pure = (APP / "BCDFoldable.swift").read_text()
live = (APP / "BCDFoldableLive.swift").read_text()
row = (APP / "BCDFoldableSettings.swift").read_text()
ui = (APP / "BCDFoldableUI.swift").read_text()
check("the four files and the host test have the BCD licence header",
      all("SPDX-License-Identifier: GPL-3.0-only" in t[:300] and "LICENSE-BCD.md" in t[:400]
          for t in (pure, live, row, ui, (R / "tests/host/bcd_foldable_checks.swift").read_text())))

# ---------------------------------------------------------------- 1 and 2. Swift
xcrun = shutil.which("xcrun")
if not xcrun:
    check("xcrun swiftc is here for the Swift parts", not args.require_swift)
else:
    with tempfile.TemporaryDirectory(prefix="bcd-foldable-") as t:
        t = Path(t)
        (t / "Main.swift").write_text((Path(__file__).resolve().parent / "bcd_foldable_checks.swift").read_text())
        r = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(t / "run"), str(t / "Main.swift"),
                            str(APP / "BCDFoldable.swift"), str(APP / "GuestDisplay.swift")], capture_output=True, text=True)
        check("the gate, the display shape and the tracker compile on the host", r.returncode == 0, r.stderr)
        if r.returncode == 0:
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            print(r.stdout, end="")
            check("the gate, the display shape and the tracker behave", r.returncode == 0, r.stdout + r.stderr)
        sdk = subprocess.run(["xcrun", "--sdk", "iphonesimulator", "--show-sdk-path"], capture_output=True, text=True).stdout.strip()
        arch = "arm64" if subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip() == "arm64" else "x86_64"
        r = subprocess.run(["xcrun", "swiftc", "-typecheck", "-swift-version", "5", "-target", arch + "-apple-ios18.0-simulator", "-sdk", sdk,
                            str(APP / "BCDFoldable.swift")], capture_output=True, text=True)
        check("the pure part type-checks against the iOS SDK", r.returncode == 0, r.stderr)

# ---------------------------------------------------------------- 3. the hooks
names = re.compile(r"\bBCDFoldable\w*")
where = {}
for path in sorted(APP.rglob("*")):
    if path.suffix not in (".swift", ".m", ".mm", ".c", ".h") or path.name in ("BCDFoldable.swift", "BCDFoldableLive.swift", "BCDFoldableSettings.swift", "BCDFoldableUI.swift"):
        continue
    for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if names.search(line):
            where.setdefault(path.name, []).append(line.strip())
check("only three files outside the new ones mention it: ContentView (two lines), GuestDisplay and BCDSettingsSections, one hook each",
      {k: len(v) for k, v in where.items()} == {"ContentView.swift": 2, "GuestDisplay.swift": 1, "BCDSettingsSections.swift": 2}, where)
content = (APP / "ContentView.swift").read_text()
check("the game view's hook tests the gate first and sits at the end of the layout pass's window branch",
      re.search(r"winios_set_compositor_frame\(full\.minX, full\.minY, full\.width, full\.height\)\n"
                r"            if BCDFoldable\.active \{ BCDFoldableLive\.layoutPass\(self, in: w\) \}", content) is not None
      and content.count("BCDFoldableLive.layoutPass") == 1)
check("... and the rest of that layout pass is as it was",
      "    override func layoutSubviews() {\n        super.layoutSubviews()\n        if let w = window {\n            applyDisplayMode(reason: \"layout\")\n"
      "            let full = convert(bounds, to: w)\n" in content)
check("the root hook is one modifier on the root Group, before the navigation title; the lines around it are as they were",
      re.search(r"startDock: \{ startDock\(\$0, compactPool: \$1\) \}\)\n                \}\n            \}\n            \.modifier\(BCDFoldableWatch\(\)\)[^\n]*\n            \.navigationTitle\(\"BCD\"\)", content) is not None
      and content.count("BCDFoldableWatch()") == 1)
display = (APP / "GuestDisplay.swift").read_text()
check("the Resolution screen's hook is the first line of its UIKit branch, nil off the gate, and the old reading follows it untouched",
      re.search(r"#if canImport\(UIKit\)\n        if let inner = BCDFoldable\.innerScreen \{ return Screen\(points: inner\.points, pixels: inner\.pixels\) \}[^\n]*\n"
                r"        let b = UIScreen\.main\.bounds, n = UIScreen\.main\.nativeBounds\n"
                r"        return Screen\(points: CGSize\(width: max\(b\.width, b\.height\), height: min\(b\.width, b\.height\)\),\n", display) is not None
      and "static func screen(active: Bool) -> (points: CGSize, pixels: CGSize)? {\n        active ? (innerPoints, innerPixels) : nil" in pure)
sections = (APP / "BCDSettingsSections.swift").read_text()
check("Settings › Graphics adds its group only on the foldable model, after the Display group and before the restart note",
      re.search(r"\n            if BCDFoldableDevice\.isThisDevice \{[^\n]*\n                BCDSettingsGroup\(title: \"Foldable iPhone\"\) \{ BCDFoldableSettingsRow\(\) \}\n            \}\n"
                r"            if backend\.restartNeeded \{ BCDSettingsNotice\(text: bcdSettingsRestartNote\) \}\n", sections) is not None
      and "var displaySwitchesShown: Bool { get }" in sections)
check("the gate is read once per run and holds the model and the switch", "static let active: Bool = BCDFoldableSwitch.gate(machine: BCDFoldableDevice.machine, stored: BCDFoldableSwitch.stored)" in pure)
check("the switch is off by default", "static let defaultOn = false" in pure)
check("the model table has the one identifier", 'static let identifiers: [String] = ["iPhone19,4"]' in pure)
check("the pure part needs no UIKit, no engine type and no logging", not re.search(r"import UIKit|import SwiftUI|LogStore|MadeiraConfig|LibraryModel|fputs|print\(", pure))
check("the switch row keeps its answer in UserDefaults, not in bcd.cfg (no catalog entry is needed)",
      "UserDefaults.standard" in pure and "MadeiraConfig" not in row and "MadeiraConfig" not in pure)

# ---------------------------------------------------------------- 4. the engine side only follows
check("the engine side has one entry point, layoutPass", len(re.findall(r"^\s+static func layoutPass", live, re.M)) == 1
      and len(re.findall(r"^\s+static func ", live, re.M)) == 1 and len(re.findall(r"private static func ", live)) == 4)
check("it follows by the paths rotation uses: the control window's attach and the layer's refresh",
      "TouchControlsHost.attach()" in live and 'MetalBackedView.refreshDisplayMode(reason: "foldable")' in live)
check("it asks UIKit for no orientation or geometry and reads no UIScreen.main",
      not re.search(r"requestGeometryUpdate|setNeedsUpdateOfSupportedInterfaceOrientations|UIDevice\.current|UIScreen\.main|BCDOrientation|sizeRestrictions", live))
check("it ends, pauses and restarts nothing", not re.search(r"\bexit\(|abort\(|suspend|terminate|wine_process|madeira_[a-z_]*stop|isHidden = true|removeFromSuperview|rootViewController =", live))
check("it logs with one [foldable] line per change, through LogStore", live.count("LogStore.shared.log(") == 1 and "BCDFoldableTracker.line(" in live)
check("a burst of layout passes logs at most one line per half second", "t - lastLogged >= 0.5" in live and "asyncAfter(deadline: .now() + 0.5" in live)

# The interface side: a narrow window reads as compact (BCDFoldableUI.swift).
check("the size-class follower sets only the horizontal size class of each window of the scene, to compact, and takes the override off at 700 pt and more",
      "window.traitOverrides.horizontalSizeClass = .compact" in ui and "window.traitOverrides.remove(UITraitHorizontalSizeClass.self)" in ui
      and "for window in scene.windows" in ui and "verticalSizeClass" not in ui and "userInterfaceIdiom" not in ui and "scene.traitOverrides" not in ui.replace("the UIWindowScene itself", ""))
check("it decides from the width alone, through the pure rule, and does nothing when a window is already right",
      "BCDFoldableLayout.compact(forWidth: width)" in ui and "guard want != window.traitOverrides.contains(UITraitHorizontalSizeClass.self) else { return false }" in ui)
check("a window that appears later gets what the others have, when it becomes visible",
      "UIWindow.didBecomeVisibleNotification" in ui and "apply(wanted, to: window)" in ui)
check("the root modifier adds the probe only when the gate is open and gives the content as it is otherwise",
      re.search(r"if BCDFoldable\.active \{\n            content\.background\(BCDFoldableProbe\(\)\)\n        \} else \{\n            content\n        \}", ui) is not None)
check("the probe is clear, untouchable and reads the window's width",
      "isUserInteractionEnabled = false" in ui and "backgroundColor = .clear" in ui and "Double(window.bounds.width)" in ui)
check("the follower reads no UIScreen.main and asks for no orientation or geometry",
      not re.search(r"UIScreen\.main|requestGeometryUpdate|UIDevice|BCDOrientation", ui))

# ---------------------------------------------------------------- 5. as before
plist = plistlib.loads((APP / "Info.plist").read_bytes())
check("Info.plist is as it was: three orientations, no UIRequiresFullScreen, no iPad orientation key, no scene manifest",
      plist["UISupportedInterfaceOrientations"] == ["UIInterfaceOrientationPortrait", "UIInterfaceOrientationLandscapeLeft", "UIInterfaceOrientationLandscapeRight"]
      and "UIRequiresFullScreen" not in plist and "UISupportedInterfaceOrientations~ipad" not in plist and "UIApplicationSceneManifest" not in plist)
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the Xcode project builds the four files", all(f"{n} in Sources" in pbx and pbx.count(n) >= 4
                                                    for n in ("BCDFoldable.swift", "BCDFoldableLive.swift", "BCDFoldableSettings.swift", "BCDFoldableUI.swift")))
check("the device families are as they were", pbx.count('TARGETED_DEVICE_FAMILY = "1,2";') >= 1)
prev = (R / "tools/ui-preview.py").read_text()
check("the preview build leaves the engine side out and keeps the gate and the Settings row",
      'ENGINE_BOUND |= {"BCDFoldableLive.swift"}' in prev and "BCDFoldable.swift" not in re.search(r"ENGINE_BOUND = \{[^}]*\}", prev).group(0))
strings = re.sub(r"//[^\n]*", "", pure + live + row + ui)
check("no Madeira in what a person reads", "Madeira" not in strings)
wf = (R / ".github/workflows/build-ipa.yml").read_text()
check("the workflow runs this check", "tests/host/check-bcd-foldable.py" in wf)
sys.exit(0 if ok else 1)
