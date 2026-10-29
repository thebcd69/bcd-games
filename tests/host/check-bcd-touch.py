#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's on-screen touch controls and their editor (app/Madeira/BCDTouch*.swift;
docs/BCD-UI-ARCHITECTURE.md).

Source checks (every host):
  - BCD's licence header, exactly, on every BCD touch file and the preview sample;
  - only BCDTouchLive.swift touches the engine: tools/ui-preview.py leaves it out of the preview
    build, and no other BCD touch file names an engine type or calls the engine;
  - the Xcode project builds the five files;
  - every place that shows touch controls shows BCD's: TouchControlsOverlay's controls and top
    bar are BCD's (its gating, the touch gamepad's set-up and the pinch stay as they were), the
    classic mapping panel is no longer shown, and the classic in-game menu's layout row is BCD's;
  - what reaches the game goes through the same calls: keys through winios_post_key, mouse
    buttons through winios_pointer at the pointer, the keyboard through MetalBackedView, and
    controller controls through the touch gamepad's own surface (TouchPadSurface), filling the
    control's frame; a key control's surface is its circle;
  - the bar over a game outside the library has the buttons the overlay window's hit band was
    measured for (44-point circles 10 apart, 10 down, four or five);
  - nothing is stored anywhere new: the controls and layouts go through TouchControlsModel and
    ControlPresetsModel (bcd-control-presets.json), a library game's through its profile;
  - drawn over a running game: no animation, blur, material or shadow in the touch views, and
    no "Madeira" or "Xbox" in what a person reads;
  - the in-game menu's section exists as ui-session uses it, and CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure):
  - tests/host/bcd_touch_checks.swift, compiled with the stored layout types, the layout store,
    the classic control's own input functions, BCDTouchCore.swift and BCDTouchLive.swift's
    conversion: every control sends the same events step by step as the classic one, is the same
    size and takes the same touches; layouts and bcd-control-presets.json convert both ways
    byte for byte; the editor's panel stays off the selected control.
Build (xcrun; --require-swift makes its absence a failure):
  - the preview build compiles (tools/ui-preview.py --build-only).

  check-bcd-touch.py [--require-swift] [--skip-preview]
"""
import argparse
import importlib.util
import os
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
ap.add_argument("--skip-preview", action="store_true", help="leave out the preview build")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


def code(text):
    """The text without whole-line comments."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("//") and not l.strip().startswith("///"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n"
          "// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
VIEWS = ["BCDTouchCore.swift", "BCDTouchControls.swift", "BCDTouchLayouts.swift", "BCDTouchEditor.swift"]
LIVE = "BCDTouchLive.swift"
src = {n: (APP / n).read_text() for n in VIEWS + [LIVE]}
sample = (R / "app/BCDPreview/BCDTouchPreview.swift").read_text()
content = (APP / "ContentView.swift").read_text()
lib = (APP / "Library.swift").read_text()
presets = (APP / "TouchControlPresets.swift").read_text()
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()

# ---- files, licence, preview, project ------------------------------------------------------
for name, text in list(src.items()) + [("BCDTouchPreview.swift", sample),
                                       ("bcd_touch_checks.swift", (R / "tests/host/bcd_touch_checks.swift").read_text())]:
    check(text.startswith(HEADER), f"{name}: BCD's licence header, exactly")

spec = importlib.util.spec_from_file_location("ui_preview", R / "tools/ui-preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
check(LIVE in preview.ENGINE_BOUND and not set(VIEWS) & preview.ENGINE_BOUND,
      "tools/ui-preview.py: only BCDTouchLive.swift stays out of the preview build", sorted(preview.ENGINE_BOUND))
ENGINE = ["TouchControlsModel", "TouchControl", "ControlAction", "ControlPresetsModel", "ControlPresetStore", "ControlPreset",
          "TouchControlsOverlay", "TouchPadSurface", "TouchPadView", "TouchPadAction", "GamepadInput", "GamepadSample",
          "PadBindings", "LibraryModel", "LibraryEntry", "LibraryHUD", "MetalBackedView", "MadeiraConfig", "LogStore",
          "ContentView", "GlassShape", "MappingPanel", "ControlLayoutMenu", "JoystickFace", "SessionPanelView"]
named = {}
for name in VIEWS + ["../BCDPreview/BCDTouchPreview.swift"]:
    text = code((APP / name).read_text())
    found = sorted({t for t in ENGINE if re.search(r"\b" + t + r"\b", text)})
    found += sorted(set(re.findall(r"\b(winios_\w+|madeira_\w+|wine_\w+)\(", text)))
    if found:
        named[name] = found
check(not named, "no BCD touch view names an engine type or calls the engine", named)
unbuilt = [n for n in VIEWS + [LIVE] if pbx.count(f"/* {n} in Sources */") != 2 or pbx.count(f"path = {n};") != 1]
check(not unbuilt, "the Xcode project builds the five BCD touch files", unbuilt)
ids = set(re.findall(r"\b(A[12]000BD[0-9A-F])\b", pbx))
check(ids == {f"A{k}000BD{i}" for k in (1, 2) for i in range(5)},
      "they use IDs A1000BD0-A1000BD4 / A2000BD0-A2000BD4 (BCD touch's range)", sorted(ids))
for i in range(5):
    entry = re.findall(rf"A1000BD{i} /\* (\S+) in Sources \*/ = {{isa = PBXBuildFile; fileRef = A2000BD{i} /\* (\S+) \*/; }};", pbx)
    check(len(entry) == 1 and entry[0][0] == entry[0][1], f"A1000BD{i} builds the file A2000BD{i} names", entry)

# ---- where the controls show -------------------------------------------------------------
overlay = block(content, "struct TouchControlsOverlay: View {")
body = block(overlay, "var body: some View {")
controls_fn = block(overlay, "private func controls(_ screen: CGSize, session: Bool) -> some View {")
check("BCDTouchControlsLive(screen: screen, session: session)" in controls_fn,
      "TouchControlsOverlay draws BCD's controls")
check("if let area = fold.bottom, !m.editing {" in controls_fn
      and "BCDTouchControlsLive(screen: area.size, session: session)" in controls_fn,
      "in iPhone Duo's tabletop pose the controls are laid out below the fold, and the editor keeps the whole screen")
check("private var topBar: some View { BCDTouchChromeLive() }" in overlay,
      "TouchControlsOverlay's top bar is BCD's bar and editor (over a game outside the library, and while editing)")
check("MappingPanel(" not in body and "classicTopBar" not in body and "classicControls" not in body,
      "the classic mapping panel, bar and controls are no longer shown")
for kept in ("controls(geo.size, session: session)", ".opacity(m.editing ? 1.0 : m.opacity)",
             "if (m.visible || m.editing) && !library.blocksGameplayTouch {",
             "if session && !m.editing { BCDSessionLiveLayer() } else { topBar }",   # ui-session: BCD's in-game layer in LibraryHUD's place
             ".gesture(scalePinch, including: m.editing ? .all : .subviews)",
             ".onChange(of: m.controls) { _, _ in configureGamepad(landscape: landscape) }",
             ".onDisappear { GamepadInput.shared.configureTouch(controls: []); TouchMouseGate.padOverlay = false }"):
    check(kept in body, f"the overlay still does what it did: {kept}")
config = block(overlay, "private func configureGamepad(landscape: Bool) {")
check("let accepting = !m.editing && !library.blocksGameplayTouch" in config
      and "GamepadInput.shared.configureTouch(controls: Set(ids), acceptingInput: accepting)" in config,
      "the touch gamepad is set up as before (controller controls on screen, no input while editing or in a menu)")
# Wave 3 (2026-10-19): the classic in-game menu (LibraryHUD) and its layout row are deleted; the layouts are in
# BCD's touch section of the in-game menu (BCDTouchMenuSectionLive) and the editor's bar.
check("struct LibraryHUD" not in lib and "ControlLayoutMenu" not in lib, "the classic in-game menu and its layout row are gone")
def live_code(f):
    return code(f.read_text())


users = {f.name for f in APP.glob("*.swift") if f.name != "TouchControlPresets.swift"
         and re.search(r"\bControlLayoutMenu\(", live_code(f))}
check(not users and "ControlLayoutMenu" not in code(presets), "nothing shows the classic layout menu any more: it is deleted", users)
check(not re.search(r"\bclassic(TopBar|Controls)\b", code(overlay)) and "TouchControlButton" not in code(content),
      "the classic bar and controls are deleted")

# ---- what reaches the game -----------------------------------------------------------------
live = src[LIVE]
send = block(live, "func send(_ event: BCDTouchEvent) {")
check("case .key(let vk, let down): winios_post_key(vk, down ? 1 : 0)" in send
      and "case .mouse(let flags): winios_pointer(0, 0, flags, 0)" in send
      and "case .toggleKeyboard: MetalBackedView.toggleKeyboard()" in send,
      "keys, mouse buttons and the keyboard go through the calls the classic controls made")
# The classic control's input functions, frozen (tests/fixtures/classic-touch-control.swift.txt: verbatim from
# TouchControlButton in ContentView.swift at 0b287c31, deleted in wave 3, 2026-10-19).
CLASSIC_FIXTURE = (R / "tests/fixtures/classic-touch-control.swift.txt").read_text()
classic_press = block(CLASSIC_FIXTURE, "private func press(_ down: Bool) {")
check("winios_post_key(vk, down ? 1 : 0)" in classic_press and "winios_pointer(0, 0, down ? 0x0002 : 0x0004, 0)" in classic_press
      and "winios_pointer(0, 0, down ? 0x0008 : 0x0010, 0)" in classic_press,
      "(the classic control's calls, for comparison)")
check("AnyView(TouchPadSurface(control: control, action: name, changed: changed))" in live,
      "controller controls go through the touch gamepad's own surface, as before")
control = block(src["BCDTouchControls.swift"], "struct BCDTouchControl: View, Equatable {")
check(".frame(width: size.width, height: size.height)\n            .overlay { if !editing { surface } }" in control
      and "input.padSurface(control: item.id, name: name)" in control,
      "a controller control's surface fills its drawn frame (the classic surface's area)")
inside = block(src["BCDTouchControls.swift"], "override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {")
check("<= diameter / 2" in inside, "a key control's surface is its circle (the classic face's area)")
check('.gesture(editDrag, including: editing ? .all : .subviews)' in control,
      "in play only the surfaces take touches; editing, a drag moves the control")
key_view = block(src["BCDTouchControls.swift"], "final class BCDTouchKeyView: UIView {")
for what, needle in (("a cancelled touch", "override func touchesCancelled"), ("the app going inactive", "willResignActiveNotification"),
                     ("the control leaving the screen", "if window == nil { finish() }"),
                     ("SwiftUI removing it", "static func dismantleUIView(_ view: BCDTouchKeyView, coordinator: ()) { view.finish() }")):
    check(needle in key_view or needle in src["BCDTouchControls.swift"], f"a key control lets go on {what}")

# ---- locking and layer order (upstream port 2026-10-25) --------------------------------------
check("    var locked: Bool?\n}" in block(content, "struct TouchControl: Codable, Identifiable, Equatable {"),
      "the stored control gains one optional key (so a layout written before it, or with nothing locked, is the same file)")
check("guard m.editing, let i = m.index(of: m.selected), m.controls[i].locked != true else { return }" in content,
      "the pinch does not resize a locked control")
check("update(id) { if $0.locked != true { $0.nx = x; $0.ny = y } }" in live
      and "update(id) { if $0.locked != true { $0.scale = BCDTouchGeometry.clampScale(scale) } }" in live
      and "if let i = model.index(of: id), model.controls[i].locked == true { return }" in block(live, "func remove(_ id: UUID) {"),
      "a locked control is not moved, resized or removed by the editor's calls either")
check("if item.locked { return }" in block(control, "private var editDrag: some Gesture {"),
      "dragging a locked control selects it and moves nothing")
check("c.locked = locked ? true : nil" in live, "the view value writes the key only when it is set")
check("let moved = BCDTouchLayering.moved(model.controls, id, toFront: toFront)" in live
      and "func bringToFront(_ id: UUID) { reorder(id, toFront: true) }" in live and "func sendToBack(_ id: UUID) { reorder(id, toFront: false) }" in live,
      "the layer order is the stored list's order: nothing new is stored")
inspector = block(src["BCDTouchEditor.swift"], "struct BCDTouchInspector: View {")
check("actions.setLocked(item.id, !item.locked)" in inspector and "actions.bringToFront(item.id)" in inspector
      and "actions.sendToBack(item.id)" in inspector and ".disabled(item.locked)" in inspector,
      "the panel has Lock, To front and To back, and a locked control's size and Remove are off")

# ---- the bar over a game outside the library -----------------------------------------------
bar = block(src["BCDTouchControls.swift"], "struct BCDTouchPlayBar: View {")
model_hit = block(content, "func hitsInteractive(_ p: CGPoint, in bounds: CGRect, topBar: Bool = true, controlsArea: CGRect? = nil) -> Bool {")
check("HStack(spacing: 10)" in bar and ".frame(width: 44, height: 44)" in bar and ".padding(.top, 10)" in bar
      and len(re.findall(r"(?<!func )\bround\(", code(bar))) == 5 and "if showsLayouts { round(" in bar,
      "the bar: 44-point circles 10 apart, 10 down, five with Layouts and four without")
check("(TouchControlsOverlay.showsLayoutMenu(self) ? 4 : 3) + 1" in model_hit
      and "showsLayouts: TouchControlsOverlay.showsLayoutMenu(model)" in live,
      "the overlay window's hit band counts the same buttons")

# ---- storage -------------------------------------------------------------------------------
writes = {n: w for n in VIEWS + [LIVE] for w in ("write(to:", "FileManager", "JSONEncoder", "UserDefaults", ".json")
          if w in code(src[n])}
check(not writes, "BCD's touch files store nothing themselves", writes)
check('.appendingPathComponent("bcd-control-presets.json")' in presets and '.appendingPathComponent("bcd-controls.json")' in content,
      "layouts stay in bcd-control-presets.json, the controls on screen in bcd-controls.json")
for fn, needles in (("func chooseLayout(_ id: String, keepCurrentFirst: Bool) {",
                     ["presets.keepCurrent()", "presets.load(id, screen: ControlPresetsModel.currentScreen())", "profileChanged()"]),
                    ("func newLayout(keepCurrentFirst: Bool) {", ["presets.createLayout()"]),
                    ("func deleteLayout(_ id: String) {", ["presets.delete(id)"]),
                    ("private func profileChanged() {", ["library.saveCurrentProfile()"])):
    body_fn = block(live, fn)
    check(all(n in body_fn for n in needles), f"BCDTouchLive.{fn.split('(')[0][5:]} goes through the layout store", needles)
choose = block(live, "func chooseLayout(_ id: String, keepCurrentFirst: Bool) {")
check(choose.index("presets.keepCurrent()") < choose.index("pauseEdit()") < choose.index("presets.load("),
      "switching mid-edit keeps what is on screen first, then ends the edit, then loads (a built-in is never written)")
save_as = block(live, "func saveAsNewLayout() {")
check(save_as.index("presets.keepCurrent()") < save_as.index("pauseEdit()"),
      "Save as new: kept under the new name before the edit ends, so the layout it came from keeps what it had")

# ---- drawn over a game ---------------------------------------------------------------------
heavy = {}
for n in ["BCDTouchControls.swift", "BCDTouchLayouts.swift", "BCDTouchEditor.swift", LIVE]:
    found = [w for w in ("withAnimation", ".animation(", ".transition(", ".blur(", "Material", "glassEffect", ".shadow(",
                         "repeatForever", "TimelineView", "drawingGroup")
             if w in code(src[n])]
    if found:
        heavy[n] = found
check(not heavy, "no animation, blur, material or shadow in the touch views", heavy)
strings = {n: s for n in VIEWS + [LIVE] for s in re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', code(src[n]))
           if re.search(r"madeira|xbox", s, re.I) and s != "builtin.xbox"}
check(not strings, "no \"Madeira\" or \"Xbox\" in what a person reads", strings)

# ---- the in-game menu's section ------------------------------------------------------------
layouts_src = src["BCDTouchLayouts.swift"]
check("struct BCDTouchMenuSection: View {" in layouts_src
      and "init(state: BCDTouchMenuState, actions: BCDTouchMenuActions, opened: @escaping () -> Void, layoutsOpen: Bool = false,\n"
          "         focusPrefix: String? = \"menu.touch\")" in layouts_src
      and "struct BCDTouchMenuSectionLive: View {\n    let closeMenu: () -> Void" in live
      and "final class BCDTouchPreviewModel: ObservableObject, BCDTouchInput, BCDTouchEditorActions, BCDTouchMenuActions" in sample,
      "an in-game menu gets BCDTouchMenuSection (live: BCDTouchMenuSectionLive(closeMenu:), preview: BCDTouchPreviewModel)")

# A controller reaches every row of the section in a menu (BCD's in-game menu drives a BCDFocus):
# each is .bcdFocusable("menu.touch.<thing>"), and left and right step the sliders.
section = block(layouts_src, "struct BCDTouchMenuSection: View {")
for thing in ('"visible"', '"opacity", value: state.opacity', '"size", value: size', '"edit"'):
    check(f"(focusPrefix, {thing}" in section, f"the menu section's {thing.split(',')[0]} row is reachable by a controller")
check("focusPrefix: focusPrefix)" in section and '.bcdTouchFocus(focusPrefix, "layout", radius: 12) { open.toggle() }' in layouts_src
      and '.bcdTouchFocus(focusPrefix, "layout." + entry.id, radius: 12)' in layouts_src
      and all(f'"confirm.{a}"' in layouts_src for a in ("keep", "replace", "cancel", "delete"))
      and '"layout.new"' in layouts_src and '"layout.delete"' in layouts_src,
      "the layout row, every layout, New, Delete and the questions' answers are reachable too")
check("self.bcdFocusable(prefix + \".\" + name, cornerRadius: radius, action: action).id(prefix + \".\" + name)" in layouts_src,
      "each focus id is also the row's scroll id (the menu scrolls to the ring)")
focus_src = (APP / "BCDFocus.swift").read_text()
move = block(focus_src, "func move(_ direction: BCDMove) {")
check("if BCDFocusSteppers.step(focused, direction) { return }" in move
      and move.index("if !wasActive { return }") < move.index("BCDFocusSteppers.step(") < move.index("nearest(from: current"),
      "left and right step a slider under the ring (after the first press shows the ring, before the ring moves)")
check("BCDTouchLayoutRow(layouts: BCDTouchLive.shared.layouts, actions: BCDTouchLive.shared, open: $open) {" in live
      and "focusPrefix" not in block(live, "struct BCDTouchLayoutRowLive: View {"),
      "the classic menu's row (no BCDFocus there) has nothing focusable")

ci = R / ".github/workflows/build-ipa.yml"
if ci.exists():
    check("run: python3 tests/host/check-bcd-touch.py" in ci.read_text(), "CI runs this check")

# ---- behaviour -----------------------------------------------------------------------------
SWIFTC = os.environ.get("SWIFTC") or shutil.which("swiftc")
if not SWIFTC:
    check(not args.require_swift, "swiftc for the behaviour checks", "not found")
else:
    actions = content[content.index("enum ControlAction: Codable"):content.index("final class TouchControlsModel")]
    pure_touch = (APP / "TouchGamepad.swift").read_text().split("// MARK: - Pure touch state", 1)[1].split("// MARK: - UIKit touch lifetime", 1)[0]
    pure_touch = pure_touch[pure_touch.index("struct GamepadSample"):]
    pure_presets = presets.split("// MARK: - Pure preset state", 1)[1].split("// MARK: - Persistence", 1)[0]
    pure_presets = pure_presets[pure_presets.index("struct ControlPreset"):]
    button = CLASSIC_FIXTURE
    classic_fns = "\n".join(block(button, f).replace("private func", "func")
                            for f in ("private func snap(", "private func stickKeys(", "private func applyStick(", "private func press("))
    classic = r'''
var classicLog: [String] = []
var classicTaps = 0
func winios_post_key(_ vk: Int32, _ down: Int32) { classicLog.append("key \(vk) \(down)") }
func winios_pointer(_ x: Int32, _ y: Int32, _ flags: UInt32, _ data: UInt32) { classicLog.append("mouse \(flags)") }
enum MetalBackedView { static func toggleKeyboard() { classicLog.append("keyboard") } }
struct UIImpactFeedbackGenerator {
    enum FeedbackStyle { case light }
    init(style: FeedbackStyle) {}
    func impactOccurred() { classicTaps += 1 }
}
/// TouchControlButton's own input functions, verbatim, on a class instead of SwiftUI state.
final class ClassicButton {
    let control: TouchControl
    let diameter: CGFloat
    var stickDir = -1
    init(control: TouchControl, diameter: CGFloat) { self.control = control; self.diameter = diameter }
''' + classic_fns + "\n}\n"
    core = src["BCDTouchCore.swift"]
    conversion = live.split("// MARK: - Stored controls <-> BCD's values", 1)[1].split("// MARK: - Input and edits", 1)[0]
    checks = (R / "tests/host/bcd_touch_checks.swift").read_text()
    with tempfile.TemporaryDirectory(prefix="bcd-touch-") as tmp:
        main = Path(tmp) / "main.swift"
        header = "import Foundation\n#if canImport(CoreGraphics)\nimport CoreGraphics\n#endif\n"
        main.write_text(header + actions + pure_touch + pure_presets + classic
                        + core.replace("import Foundation\nimport CoreGraphics\n", "") + conversion + checks)
        exe = Path(tmp) / "check"
        r = subprocess.run([SWIFTC, "-O", str(main), "-o", str(exe)], capture_output=True, text=True)
        check(r.returncode == 0, "the behaviour checks compile on the host", (r.stderr or r.stdout)[-3000:])
        if r.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=600)
            for line in r.stdout.splitlines():
                print("  " + line)
            check(r.returncode == 0, "BCD's controls behave as the classic ones did", (r.stdout + r.stderr)[-2000:])

# ---- build ---------------------------------------------------------------------------------
if args.skip_preview:
    print("SKIP: the preview build (--skip-preview)")
elif not shutil.which("xcrun"):
    check(not args.require_swift, "xcrun for the preview build", "not found")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-touch-preview-") as tmp:
        r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp],
                           capture_output=True, text=True)
        check(r.returncode == 0, "the preview build compiles (BCD's touch views without the engine)", (r.stderr or r.stdout)[-3000:])

if failures:
    print(f"{failures} FAILED")
    sys.exit(1)
print("PASS: BCD's touch controls and editor")
