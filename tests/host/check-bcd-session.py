#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""BCD's in-game layer (app/Madeira/BCDSession.swift, BCDInGameMenu.swift, BCDStartingScreen.swift,
BCDPerformanceOverlay.swift; engine side BCDSessionLive.swift; docs/BCD-UI-ARCHITECTURE.md): the
starting screen, the menu button, the performance overlay, the session line, the live log, the
in-game menu and the keyboard's key bar, in BCD's own design, doing what the classic layer did.

Source checks (every host):
  1. Licence header on every file of the layer, the preview file and this check.
  2. The views name no engine type (they are drawn from plain values); the engine-bound file is
     in tools/ui-preview.py's ENGINE_BOUND; the files are in the Xcode project.
  3. Every place that showed the classic layer shows BCD's: the session's layer in
     TouchControlsOverlay, the menu of the touch-controls window (wave 3: the old home's upright sheet, its starting
     screen launch and the developer overlay's placements are deleted with those interfaces), the
     hide of the starting screen on a refused launch, the key bar over the keyboard. Nothing in ContentView
     names LibraryHUD, SessionPanelView, LaunchSplashHost or FPSOverlay any more.
  4. The menu's functions call the engine as the classic menu did (LibraryHUD's menu, the
     classic starting screen, SteamCloudQuitRow, SessionPanelView, FPSOverlay, LaunchSplashView):
     Resume, Quit (asked first, then LibraryModel.requestQuit), Upload saves and close BCD (and
     replacing the cloud's copies, asked first), Keyboard, the frame-rate limit (30 and 40 only
     where DXMT has them), Aspect & scaling (MADEIRA_SESSION_TOOLS), the overlay and its fields,
     Eco, the controller's mode, binds and API, the pointer, the diagnostics
     (MADEIRA_SESSION_DIAGNOSTICS), the live log, sharing the logs; the touch controls only through
     BCD's touch section (BCDTouchMenuSectionLive, ui-touch), with no rows of the menu's own; the starting screen's Show desktop, Show the game, Close session and
     live log; Back + Start, and the opt-in Home hold, open and close it; B and Menu close it.
  5. Nothing is alive while it is closed: the menu exists only while open, the starting screen's
     and the overlay's timers only while they are shown, the overlay samples at most twice a
     second; no blur, no material, no spinner and no animation that runs on its own.
  6. CI runs this check.
Behaviour (swiftc on the host; --require-swift makes its absence a failure):
  - BCDDockStart.read against the classic DockStartStatus.text on the same Dock reports: the same
    order of precedence, as a step in a word and notes;
  - BCDFrameRate: steady readings at 60, 19 (no rounding to steps of 5) and nothing; lines packed
    to the width;
  - LibraryController: the Home hold sends "home" once per half-second hold, only when opted in.
  - BCDSessionLinePlace (round 624): the session line ends 8 pt short of the menu button on an upright phone (a long line ran
    under it), goes below a button dragged near the middle, and keeps its old place on a phone held sideways, on an iPad and
    with no button.
Preview build (tools/ui-preview.py --build-only; --require-preview makes a missing Xcode a
failure): the views build without the engine.

  check-bcd-session.py [--require-swift] [--require-preview]
"""
import argparse
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
ap.add_argument("--require-preview", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + detail) if detail and not cond else ""))
    if not cond:
        failures += 1


def block(text, header):
    """The text from `header` to the brace that closes its first opening brace."""
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


def code(text):
    """The text without comments (whole-line and trailing //, so a word in a comment counts for nothing)."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("///"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


VIEWS = ["BCDSession.swift", "BCDInGameMenu.swift", "BCDStartingScreen.swift", "BCDPerformanceOverlay.swift"]
LIVE = "BCDSessionLive.swift"
HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
src = {name: (APP / name).read_text() for name in VIEWS + [LIVE]}
preview = (R / "app/BCDPreview/BCDPreviewSession.swift").read_text()
content = (APP / "ContentView.swift").read_text()
library = (APP / "Library.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
tool = (R / "tools/ui-preview.py").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
live = code(src[LIVE])

# ---- 1. licence ---------------------------------------------------------------------------------
for name, text in list(src.items()) + [("BCDPreviewSession.swift", preview)]:
    check(text.startswith(HEADER), f"{name}: BCD's licence header")
check(Path(__file__).read_text().startswith("#!/usr/bin/env python3\n# SPDX-License-Identifier: GPL-3.0-only\n# Copyright 2026 thebcd69 (BCD)\n"),
      "this check: BCD's licence header")

# ---- 2. no engine type in the views ---------------------------------------------------------------
ENGINE = ["LibraryModel", "LibraryEntry", "LibraryController", "LibraryKeyboard", "LibraryHUD", "TouchControlsModel",
          "ControlPresetsModel", "ControlAction", "PadBindings", "InputSettings", "GameSession", "GameProfile", "DockStartScreen",
          "DockStartStatus", "MadeiraDock", "DockInstallers", "SteamOwnedLibrary", "SteamGames", "LogStore", "MadeiraConfig",
          "GamepadInput", "FPSOverlay", "ProMotionIntent", "FrameLimit", "DisplayFit", "DisplayMode", "MetalBackedView",
          "JITCoordinator", "BCDLiveStore", "CoverStore", "CPUMeter", "madeira_", "winios_", "wine_", "wineserver_"]
for name in VIEWS:
    body = code(src[name])
    named = sorted({w for w in ENGINE if re.search(r"\b" + re.escape(w) if not w.endswith("_") else re.escape(w), body)})
    check(not named, f"{name} names no engine type", ", ".join(named))
check(re.search(r'ENGINE_BOUND\s*\|=\s*\{[^}]*"BCDSessionLive\.swift"', tool) is not None,
      "BCDSessionLive.swift is in ui-preview.py's ENGINE_BOUND")
check(all(s in tool for s in ('"ingame-menu"', '"starting"', '"overlay"')), "the preview tool photographs the layer's screens")
for name in VIEWS + [LIVE]:
    check(project.count(f"/* {name} in Sources */") == 2 and project.count(f"path = {name};") == 1,
          f"{name} is built by the Xcode project")
check("BCDPreviewSessionScreens(store: store, focus: focus)" in (R / "app/BCDPreview/BCDPreviewApp.swift").read_text(),
      "the preview app shows -BCDTab ingame-menu, starting and overlay")

# ---- 3. every place shows BCD's layer -------------------------------------------------------------
overlay = block(content, "struct TouchControlsOverlay: View {")
check("if session && !m.editing { BCDSessionLiveLayer() }" in overlay, "a library session draws BCD's layer over the game")
check("BCDDeskSessionMenu(onClose: { m.sessionPanel = false })" in overlay, "the developer session's menu in landscape is BCD's")
# Wave 3 (2026-10-19): the old home screen and the developer interface are deleted, with their upright session
# menu (a sheet), their starting screen launch and their overlay placements; ContentView keeps only a library
# session's body and the touch-controls window's menu above.
check("sessionPanelOpen" not in content and "BCDStartingHost.show(title: launch.title)" not in content
      and "pendingLaunch" not in content, "ContentView has no old-home session: no upright session menu, no home launch")
run = block(content, "private func runWineFullSequence(profile: LibraryEntry? = nil) {")
refuse = run[run.index("DeviceCapability.launchRefusal("):run.index("if Self.wineSequenceStarted {")]
check("BCDStartingHost.hide()" in refuse, "a refused launch hides BCD's starting screen")
check("BCDDeskOverlay(" not in code(content) and "FPSOverlay" not in code(content),
      "the developer interface's overlay placements are gone from ContentView")
gone = [w for w in ("LibraryHUD(", "SessionPanelView(", "LaunchSplashHost.", "FPSOverlay(") if w in code(content)]
check(not gone, "ContentView shows no classic in-game view", ", ".join(gone))
key_input = block(library, "final class LibraryKeyInput: UIView, UIKeyInput {")
check("BCDKeyboardAccessory.view(for: self)" in key_input and "override var inputAccessoryView: UIView? { keyBar }" in key_input
      and "UIStackView" not in key_input, "the keyboard's key bar is BCD's")
bar = block(src[LIVE], "@MainActor enum BCDKeyboardAccessory {")
check(all(f'"{k}": 0x{v}' in bar for k, v in (("esc", "1B"), ("ctrl", "11"), ("shift", "10"), ("alt", "12"), ("tab", "09"),
                                              ("enter", "0D"), ("left", "25"), ("up", "26"), ("down", "28"), ("right", "27")))
      and "winios_post_key(vk, 0)" in bar and "winios_post_key(vk, 1)" in bar and "input.press(vk)" in bar
      and "LibraryKeyboard.hide()" in bar, "the key bar posts the classic keys, holds the modifiers and Done hides it")

# ---- 4. the same engine calls --------------------------------------------------------------------
lib_menu = block(live, "@MainActor final class BCDLibraryMenuSource: BCDSessionMenuSource {")
answer = block(lib_menu, "func answer(_ row: String, _ answer: String) {")
act = block(lib_menu, "func act(_ row: String) {")
choose = block(lib_menu, "func choose(_ row: String, _ value: String) {")
toggle = block(lib_menu, "func toggle(_ row: String, _ on: Bool) {")
slide = block(lib_menu, "func slide(_ row: String, _ value: Double) {")
quit_row = lib_menu[lib_menu.index('BCDMenuRow(id: "quit"'):]
check('case ("quit", "quit"): model.requestQuit()' in answer and "confirm: BCDConfirm(" in quit_row[:quit_row.index("]))")],
      "Quit game asks first, then asks the game to close (LibraryModel.requestQuit)")
check('case "resume": close()' in act and "func close() { model.menu = false }" in lib_menu, "Resume closes the menu")
check('model.menu = false\n            LibraryKeyboard.show()' in act, "Keyboard: the menu closes and the keyboard opens")
upload = block(lib_menu, "private func uploadAndClose(replace: Bool) {")
cloud = block(lib_menu, "private var cloudSection: BCDMenuSection? {")
check("await steam.uploadForQuit(appID, replaceCloud: replace)" in upload and "guard case .done(let count) = steam.cloudQuit" in upload
      and "exit(0)" in upload and "SteamOwnedLibrary.cloudQuitEnabled, steam.signedIn" in cloud
      and '"Upload saves and close BCD"' in cloud, "Upload saves and close BCD: SteamCloudQuitRow's upload, then BCD closes")
check('case "cloud.upload": uploadAndClose(replace: false)' in act and 'case ("cloud.replace", "replace"): uploadAndClose(replace: true)' in answer
      and 'id: "cloud.replace"' in cloud and "confirm: BCDConfirm(" in cloud[cloud.index('id: "cloud.replace"'):],
      "replacing the cloud's saves is asked first")
check("model.setFPS(mode)" in choose and "ProMotionIntent.has30Cap || mode == 3" in lib_menu and "ProMotionIntent.has40Cap || mode == 4" in lib_menu,
      "the frame-rate limit: LibraryModel.setFPS, 30 and 40 only where DXMT has them")
check('MadeiraConfig.flag("MADEIRA_SESSION_TOOLS")' in lib_menu and "if sessionTools {" in lib_menu
      and "model.displayMode = mode" in choose, "Aspect & scaling: the session's display mode, behind MADEIRA_SESSION_TOOLS")
hud_core = (APP / "BCDHUDCore.swift").read_text()
check("if BCDHUDMenu.owns(row) { return applyHUD(BCDHUDMenu.toggle(row, on, BCDHUDState.shared.setup)) }" in toggle
      and "if BCDHUDMenu.owns(row) { return applyHUD(BCDHUDMenu.choose(" in choose
      and "out += BCDHUDMenu.sections(BCDHUDState.shared.setup, screen: hudScreen)" in lib_menu
      and all(f'case "{f}": return .' in hud_core for f in ("FPS", "Frame time", "CPU", "GPU", "RAM", "Battery", "Thermal")),
      "the performance HUD (Off, 1 to 4, Custom: tests/host/check-bcd-hud.py) and every field the classic overlay had, each an item of it")
check("madeira_set_eco(on ? 1 : 0)" in toggle and "private var eco = madeira_get_eco() != 0" in lib_menu, "Eco mode: the engine's switch")
check("model.controllerMode = value.isEmpty ? nil : value\n            model.saveCurrentProfile()" in choose
      and "GamepadInput.keyboardMouseAvailable" in lib_menu, "the controller's mode, saved to the game")
check("model.controllerBinds = table\n                model.saveCurrentProfile()" in choose and "model.controllerBinds = [:]" in act
      and "model.padMouseVertical = abs(value - 1) < 0.01 ? 1 : value" in slide and "PadBindings.build(controls: controls)" in live,
      "controller binds: the game's table, the right stick's vertical speed, reset")
pad = block(live, "@MainActor enum BCDLivePadMode {")
check("GameProfile(windowsPath: entry.windowsPath).set(GamepadInput.padModeKey, explicit ? value : nil)" in pad
      and "GamepadInput.sessionHIDKind" in pad and "BCDLivePadMode.set(value, for: entry)" in choose
      and "entry.desktop != true" in lib_menu, "the controller API: the game's file, live or after a restart")
check('input.touchMode = value == "touch"' in choose and 'input.relative = value == "relative"' in choose
      and "input.sensRel = value } else { input.sensAbs = value" in slide, "the pointer: mode and speed (InputSettings)")
check('MadeiraConfig.flag("MADEIRA_SESSION_DIAGNOSTICS", fallback: false)' in lib_menu and "if sessionTools && sessionDiagnostics {" in lib_menu
      and "madeira_capture_request(1)" in act and "madeira_set_fence_mode(Int32(mode == 0 ? 7 : mode))" in choose
      and "FPSOverlayFenceMode.current = mode" in choose, "the diagnostics: frame capture and GPU sync, opt-in")
check("LogStore.shared.exportableLogs" in lib_menu and '.page("log"' in lib_menu and "LogStore.shared.setDisplayActive(true)" in lib_menu,
      "the live log and Share logs")
check('menu.custom["touch"] = { AnyView(BCDTouchMenuSectionLive(closeMenu: { LibraryModel.shared.menu = false })) }' in live
      and 'model.custom["touch"] = { AnyView(BCDTouchMenuSectionLive(closeMenu: onClose)) }' in live
      and live.count('kind: .custom("touch")') == 2,
      "the touch controls (on, layout, opacity, size, editor): BCD's touch section in both menus, closing the menu for the editor")
own = [w for w in ("controls.visible = ", "controls.editing = ", "presets.load(", "presets.createLayout(", "controls.opacity = ",
                   "model.opacity = ", "controls.sizeScale = ", "ControlLayoutMenu") if w in live]
check(not own, "no touch-control rows of the menu's own beside BCD's touch section", ", ".join(own))
session = block(live, "@MainActor final class BCDLiveSession: ObservableObject, BCDSessionHUDActions {")
check("LibraryController.shared.configure(enabled: true, ownsInput: true)" in session
      and "LibraryController.shared.configure(enabled: true, ownsInput: false)" in session
      and "if save { model.saveCurrentProfile() }" in session and "model.saveCurrentProfile()" in block(session, "func appeared() {"),
      "the menu takes the controller while open and saves the game's choices when it closes")
command = block(session, "func command(_ name: String) {")
check('if name == "menu" || name == "home" {' in command and "model.showMenu()" in command and '"back": .back' in command
      and "LibraryController.shared.commands" in live, "Back + Start (and the Home hold) open and close the menu; B goes back")
menu_view = code(src["BCDInGameMenu.swift"])
check("case .back: focus.press(.b)" in menu_view and "case .menu: focus.press(.menu)" in menu_view
      and '(BCDHint(button: .menu, title: "Close"), { source.close() })' in menu_view, "B goes back and Menu closes the menu")
starting = block(session, "func starting(_ action: BCDStartingAction) {")
check("case .toggleLog: model.toggleLaunchLogs()" in starting and "case .showDesktop: dock.showDesktop(model)" in starting
      and 'case .showGame: model.showGameView(reason: "button")' in starting and "case .close: model.requestQuit()" in starting,
      "the starting screen: live log, Show desktop, Show the game, Close session")
info = block(session, "private func startingInfo() -> BCDStartingInfo {")
check("MadeiraDock.pollReport().fields" in info and "DockInstallers.poll(drive: MadeiraDock.drive)" in info
      and "DockInstallers.installsOnly ? DockInstallers.closingCountdown() : nil" in info and "DockInstallers.note" in info
      and "dock.failure" in info and "info.canClose = true" in info and "info.canShowDesktop = dock.holding" in info
      and "info.canShowGame = model.launchSlow && !dock.holding" in info and "model.launchSlow" in info,
      "the starting screen reads the Dock's report, installs, failure and the slow start as the classic one did")
# round 624 (agent D2 S1 / Discord W1: BioShock 2 sat 62 minutes at "Loading" on a dead Wine server): ten minutes without a frame
check('if model.launchVerySlow {' in info and 'BCDStartNote(id: "very-slow", text: "Nothing has been drawn for 10 minutes.' in info
      and info.index("if model.launchVerySlow {") < info.index("} else if model.launchSlow {")
      and "launchVerySlow = true" in library and "Date().timeIntervalSince(launchStarted) > 600" in library
      and "launchSlow = false; launchVerySlow = false;" in library,
      "the starting screen says to close BCD and start again after 10 minutes without a frame (round 624)")
check("model.menuButtonRect = frame" in session and "model.performanceRect = frame" in session,
      "the menu button and the overlay take their touches (ControlsWindow)")
check('static let homeHold = MadeiraConfig.flag("MADEIRA_MENU_HOME_HOLD", fallback: false)' in library
      and 'if homeHeld { command = "home" }' in library, "the Home hold is opt-in")
desk = block(live, "@MainActor final class BCDDeskMenuSource: BCDSessionMenuSource {")
check(all(s in desk for s in ("FrameLimit.apply(next)", "DisplayFit.current = next", "session.showPerformance = on",
                              "madeira_set_eco(on ? 1 : 0)",
                              "input.sensAbs = value", "input.sensRel = value", "MetalBackedView.toggleKeyboard()",
                              "madeira_capture_request(1)",
                              "madeira_set_fence_mode(Int32(mode == 0 ? 7 : mode))", "LogStore.shared.exportableLogs")),
      "the developer session's menu: SessionPanelView's functions")
sampler = block(live, "@MainActor final class BCDOverlaySampler {")
check("ProMotionIntent.apply(mode: madeira_get_vsync_locked())" in sampler and '"[present] count=' in sampler
      and "madeira_set_vsync_locked(next)" in sampler and "ProMotionIntent.apply(mode: next)" in sampler
      and "os_proc_available_memory()" in sampler and "libraryReadings" not in sampler,
      "the developer overlay holds ProMotion, logs [present] and keeps FPSOverlay's switches (a library session's HUD is BCDHUDLive.swift)")
host = block(live, "@MainActor enum BCDStartingHost {")
watch = block(live, "@MainActor final class BCDDeskStartingWatch: ObservableObject {")
check("w.windowLevel = .normal + 102" in host and "if framesSeen >= 2 { BCDStartingHost.hide(); stop() }" in watch
      and '"[win-pos]"' in watch and "ticks - seen >= 32" in watch, "the old home's starting screen closes on LaunchSplashView's rules")

# ---- 5. nothing alive while closed --------------------------------------------------------------
layer = block(src["BCDSession.swift"], "struct BCDSessionLayer: View {")
check("let place = BCDSessionLinePlace.of(viewport: geo.size, top: insets.top + 12," in layer
      and "button: menuButton.viewport == geo.size ? menuButton.frame : .zero)" in layer
      and ".frame(maxWidth: place.width)" in layer and ".padding(.top, place.top)" in layer
      and "report: { menuButton = ($0, geo.size); actions.menuButtonFrame($0) }" in layer,
      "the session line keeps clear of the menu button, whose frame the layer keeps with the screen size it had (round 624)")
check("if let menu = hud.menu {" in layer and "if let starting = hud.starting {" in layer
      and "if hud.starting == nil, let performance = hud.performance {" in layer, "the menu, the starting screen and the performance HUD exist only while shown")
check("hud.menu = nil" in block(session, "private func closeMenuModel(save: Bool) {")
      and "startingTimer?.invalidate()" in block(session, "private func stopStarting() {")
      and "if running && model.launching { startStarting() } else { stopStarting() }" in session,
      "closing drops the menu; the starting screen's timer runs only while it is up")
check("Timer(timeInterval: 0.5, repeats: true)" in sampler and "timer?.invalidate()" in block(sampler, "func stop() {"),
      "the overlay samples twice a second, only while shown")
quiet = []
for name in VIEWS:
    body = code(src[name])
    for bad in (".blur(", "Material", "repeatForever", "TimelineView", "ProgressView(", "Timer.", "Timer(", ".glassEffect"):
        if bad in body:
            quiet.append(f"{name}: {bad}")
check(not quiet, "no blur, material, spinner, timer or animation that runs on its own in the views", ", ".join(quiet))
check("BCDSessionStyle.dim" in src["BCDInGameMenu.swift"] and "static let dim = Color.black.opacity(0.55)" in src["BCDSession.swift"],
      "the menu dims the game; it never blurs it")
check(not re.search(r'"[^"\n]*\bMadeira\b[^"\n]*"', "\n".join(code(t) for t in src.values()) + code(preview)),
      "no Madeira in what the layer shows")

# ---- 6. CI ---------------------------------------------------------------------------------------
check("python3 tests/host/check-bcd-session.py" in workflow, "CI runs this check")

# ---- behaviour ------------------------------------------------------------------------------------
swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    starting_src = src["BCDStartingScreen.swift"]
    overlay_src = src["BCDPerformanceOverlay.swift"]
    dock = (APP / "DockStartScreen.swift").read_text()
    rules = dock[dock.index("// MARK: - Rules"):dock.index("// MARK: - Model")]
    harness = "import Foundation\n"
    harness += "enum BCDSessionTone: Equatable { case normal, soft, good, warm, hot }\n"
    harness += block(starting_src, "enum BCDStartStep: String, CaseIterable {") + "\n"
    harness += block(starting_src, "struct BCDStartNote: Identifiable, Equatable {") + "\n"
    harness += block(starting_src, "enum BCDDockStart {") + "\n"
    harness += block(overlay_src, "struct BCDReading: Identifiable, Equatable {") + "\n"
    harness += block(overlay_src, "enum BCDFrameRate {") + "\n"
    harness += "#if canImport(CoreGraphics)\nimport CoreGraphics\n#endif\n" + block(src["BCDSession.swift"], "enum BCDSessionLinePlace {") + "\n"
    harness += rules + "\n"
    lc = block(library, "final class LibraryController: ObservableObject, @unchecked Sendable {")
    # Round 604: the controller's chord (hold Start / hold Home) types LibraryController.sample reads.
    feel = (APP / "BCDSessionFeel.swift").read_text()
    for header in ("struct BCDFeelChoice: Equatable {", "enum BCDMenuChord: String, CaseIterable {", "struct BCDChordTracker {", "enum BCDChordSetting {"):
        harness += block(feel, header) + "\n"
    harness += """
#if canImport(Combine)
import Combine
#endif
enum MadeiraConfig {
    static var hold = false
    static func flag(_ name: String, fallback: Bool = true) -> Bool { name == "MADEIRA_MENU_HOME_HOLD" ? hold : fallback }
}
""" + lc.replace("static let homeHold", "static var homeHold") + "\n"
    harness += r'''
var failed = 0
func expect(_ c: Bool, _ what: String) { print((c ? "PASS: " : "FAIL: ") + what); if !c { failed += 1 } }

// The classic text for a report, and the step BCD shows for it: the same order of precedence.
let cases: [([String: String], Bool, String?, Bool, Double, String?, BCDStartStep, String)] = [
    (["launch-option-invalid": "1", "launch-client-error": "0"], false, nil, false, 0, nil, .launching, "launch option"),
    (["launch-update-wait": "1", "probe-start-bits": "1"], false, nil, false, 0, nil, .waiting, "update"),
    (["launch-update-wait": "1", "launch-update-ready": "1", "launch-client-error": "0"], false, nil, false, 0, nil, .launching, "update ready"),
    (["launch-session-wait": "1", "launch-client-error": "35"], false, nil, false, 0, nil, .waiting, "session elsewhere"),
    (["launch-config-wait": "1", "launch-client-error": "22"], false, nil, false, 0, nil, .waiting, "configuration"),
    (["launch-client-error": "0", "probe-start-bits": "1", "session-authenticated-online": "1"], false, nil, false, 0, nil, .launching, "launch accepted"),
    (["ceg-request": "1", "session-requested-app-listed": "1"], false, nil, false, 0, nil, .preparing, "executable"),
    (["ceg-request": "1", "ceg-result": "0", "session-requested-app-listed": "1"], false, nil, false, 0, nil, .launching, "executable ready"),
    (["session-offline-listed": "1"], false, nil, false, 0, nil, .launching, "offline licence"),
    (["session-offline-logon-result": "1"], false, nil, false, 0, nil, .checking, "offline sign-in"),
    (["session-offline-requested": "1"], false, nil, false, 0, nil, .signingIn, "offline asked"),
    (["session-requested-app-listed": "1", "session-authenticated-online": "1"], false, nil, false, 0, nil, .launching, "licence confirmed"),
    (["session-authenticated-online": "1", "probe-start-bits": "1"], false, nil, false, 0, nil, .checking, "signed in"),
    (["session-native-token-submitted": "1", "probe-start-bits": "1"], false, nil, false, 0, nil, .signingIn, "signing in"),
    (["probe-start-bits": "1"], true, nil, false, 0, nil, .connecting, "host started"),
    ([:], false, nil, false, 0, nil, .starting, "host starting"),
    ([:], false, nil, false, 31, nil, .starting, "host slow"),
    ([:], true, "Installing 1 of 2", false, 0, nil, .installing, "installs"),
    ([:], true, nil, true, 0, nil, .starting, "installs done"),
    ([:], true, "Done", true, 0, "closing", .installing, "installs only"),
]
for (fields, installers, progress, finished, waited, hint, step, what) in cases {
    let classic = DockStartStatus.text(fields, installers: installers, installerProgress: progress, installsFinished: finished,
                                       waited: waited, restartHint: hint)
    let read = BCDDockStart.read(fields, installers: installers, installerProgress: progress, installsFinished: finished,
                                 waited: waited, restartHint: hint)
    expect(read.step == step, "Dock start, \(what): \(read.step.word) (classic: \(classic.replacingOccurrences(of: "\n", with: " ")))")
}
let slow = BCDDockStart.read([:], installers: false, installerProgress: nil, installsFinished: false, waited: 31)
expect(slow.notes.contains { $0.id == "slow" } && BCDDockStart.read([:], installers: false, installerProgress: nil,
       installsFinished: false, waited: 5).notes.isEmpty, "a slow Dock start says so after 30 s, not before")
let progress = BCDDockStart.read([:], installers: true, installerProgress: "Installing 1 of 2", installsFinished: false, waited: 0)
expect(progress.notes.first?.text == "Installing 1 of 2", "the installs' own progress is a note")
let hint = BCDDockStart.read([:], installers: true, installerProgress: nil, installsFinished: true, waited: 0, restartHint: "closing")
expect(hint.notes.contains { $0.text == "closing" }, "a start that only runs the installs says what comes next")
expect(Set(BCDStartStep.allCases.map(\.word)).count == BCDStartStep.allCases.count, "every step has its own word")

func steady(_ fps: Double, seconds: Double) -> [(t: Double, count: UInt64)] {
    stride(from: 0.0, through: seconds, by: 0.5).map { (t: $0, count: UInt64(($0 * fps).rounded(.down))) }
}
expect(abs(BCDFrameRate.fps(steady(60, seconds: 3)) - 60) < 1.5, "60 FPS reads as 60")
let nineteen = BCDFrameRate.fps(steady(19, seconds: 3))
expect(abs(nineteen - 19) < 1.2, "19 FPS reads as 19, not a step of 5 (\(nineteen))")
expect(BCDFrameRate.fps([(0, 5), (0.5, 5), (1.0, 5)]) == 0 && BCDFrameRate.fps([(0, 5)]) == 0, "no frames: 0")
expect(BCDFrameRate.tone(60) == .good && BCDFrameRate.tone(35) == .warm && BCDFrameRate.tone(5) == .hot && BCDFrameRate.tone(0) == .soft,
       "the reading's colour follows the rate")
let parts = ["58 FPS", "17.2 ms", "CPU 41% (top 96%)", "GPU 63% (10.8 ms)", "2310 MB", "82%", "Warm"].enumerated()
    .map { BCDReading(id: String($0.offset), text: $0.element) }
let lines = BCDFrameRate.lines(parts, characters: 40)
expect(lines.flatMap { $0 }.count == parts.count && lines.allSatisfy { l in l.map(\.text.count).reduce(0, +) + 3 * (l.count - 1) <= 40 || l.count == 1 },
       "readings are packed into lines that fit")

// The Home hold: opt-in, once per hold of half a second.
var got: [String] = []
func pump() { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
MadeiraConfig.hold = false
LibraryController.homeHold = false
let off = LibraryController()
#if canImport(Combine)
var subs: [AnyCancellable] = []
subs.append(off.commands.sink { got.append($0) })
#endif
off.configure(enabled: true, ownsInput: false)
off.sample(buttons: 0x0400, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.6); off.sample(buttons: 0x0400, lx: 0, ly: 0); pump()
expect(!got.contains("home"), "off: holding Home does nothing")
LibraryController.homeHold = true
let on = LibraryController()
got = []
#if canImport(Combine)
subs.append(on.commands.sink { got.append($0) })
#endif
on.configure(enabled: true, ownsInput: false)
on.sample(buttons: 0x0400, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.2); on.sample(buttons: 0x0400, lx: 0, ly: 0); pump()
expect(!got.contains("home"), "on: a short press is not a hold")
Thread.sleep(forTimeInterval: 0.4); on.sample(buttons: 0x0400, lx: 0, ly: 0); on.sample(buttons: 0x0400, lx: 0, ly: 0); pump()
expect(got.filter { $0 == "home" }.count == 1, "on: half a second held sends home once")
on.sample(buttons: 0, lx: 0, ly: 0); on.sample(buttons: 0x0400, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.55)
on.sample(buttons: 0x0400, lx: 0, ly: 0); pump()
expect(got.filter { $0 == "home" }.count == 2, "on: a new hold sends it again")
on.sample(buttons: 0x30, lx: 0, ly: 0); pump()
expect(got.last == "menu", "Back + Start still opens the menu")

// Round 604, the in-game menu's "Open this menu from a controller with": Back + Start is the default; hold Start and hold Home are chosen.
BCDChordSetting.mode = .backStart
LibraryController.homeHold = false
let plain = LibraryController()
got = []
#if canImport(Combine)
subs.append(plain.commands.sink { got.append($0) })
#endif
plain.configure(enabled: true, ownsInput: false)
plain.sample(buttons: 0x0010, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.95); plain.sample(buttons: 0x0010, lx: 0, ly: 0); pump()
plain.sample(buttons: 0x0400, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.6); plain.sample(buttons: 0x0400, lx: 0, ly: 0); pump()
expect(got.isEmpty, "default: holding Start or Home opens nothing")
BCDChordSetting.mode = .holdStart
let startHold = LibraryController()
got = []
#if canImport(Combine)
subs.append(startHold.commands.sink { got.append($0) })
#endif
startHold.configure(enabled: true, ownsInput: false)
startHold.sample(buttons: 0x0010, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.3); startHold.sample(buttons: 0x0010, lx: 0, ly: 0); pump()
expect(got.isEmpty, "hold Start: a short press reaches the game and opens nothing")
Thread.sleep(forTimeInterval: 0.6); startHold.sample(buttons: 0x0010, lx: 0, ly: 0); startHold.sample(buttons: 0x0010, lx: 0, ly: 0); pump()
expect(got == ["menu"], "hold Start: 0.8 s held opens the menu, once")
startHold.configure(enabled: true, ownsInput: true)
startHold.sample(buttons: 0, lx: 0, ly: 0)
startHold.sample(buttons: 0x0010, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.95); startHold.sample(buttons: 0x0010, lx: 0, ly: 0); pump()
expect(got.filter { $0 == "menu" }.count == 2, "hold Start: while the menu is open Start is the menu's own button (one press, no hold)")
BCDChordSetting.mode = .holdGuide
let guide = LibraryController()
got = []
#if canImport(Combine)
subs.append(guide.commands.sink { got.append($0) })
#endif
guide.configure(enabled: true, ownsInput: false)
guide.sample(buttons: 0x0400, lx: 0, ly: 0); Thread.sleep(forTimeInterval: 0.6); guide.sample(buttons: 0x0400, lx: 0, ly: 0); pump()
expect(got == ["home"], "hold Home (chosen in the menu): half a second held sends home once")
BCDChordSetting.mode = .backStart

// The session line and the menu button (the button's frame as BCDDraggable reports it: 44 pt, 8 pt from the screen's edge).
func line(_ w: CGFloat, _ h: CGFloat, top: CGFloat, button: CGRect) -> (CGFloat, CGFloat) {
    let p = BCDSessionLinePlace.of(viewport: CGSize(width: w, height: h), top: top, button: button)
    return (p.width, p.top)
}
expect(line(375, 667, top: 32, button: CGRect(x: 323, y: 28, width: 44, height: 44)) == (255, 32),
       "an upright iPhone SE: the line ends 8 pt short of the menu button in the corner")
expect(line(375, 667, top: 32, button: CGRect(x: 8, y: 28, width: 44, height: 44)) == (255, 32),
       "the button moved to the other corner: the same, mirrored")
expect(line(375, 667, top: 32, button: CGRect(x: 165, y: 28, width: 44, height: 44)) == (335, 80),
       "the button dragged to the middle: the line goes 8 pt below it")
expect(line(375, 667, top: 32, button: CGRect(x: 323, y: 300, width: 44, height: 44)) == (335, 32),
       "the button dragged down the side: the old place")
expect(line(375, 667, top: 32, button: .zero) == (335, 32), "no button (the starting screen, a menu): the old place")
expect(line(667, 375, top: 12, button: CGRect(x: 615, y: 8, width: 44, height: 44)) == (520, 12),
       "an iPhone SE held sideways: the old place")
expect(line(874, 402, top: 12, button: CGRect(x: 822, y: 44, width: 44, height: 44)) == (520, 12),
       "an iPhone 17 Pro held sideways: the old place")
expect(line(744, 1133, top: 36, button: CGRect(x: 692, y: 32, width: 44, height: 44)) == (520, 36),
       "an upright iPad mini: the old place")
exit(failed == 0 ? 0 : 1)
'''
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session.swift"
        path.write_text(harness)
        exe = Path(tmp) / "session"
        r = subprocess.run([swiftc, "-O", "-o", str(exe), str(path)], capture_output=True, text=True)
        if r.returncode:
            print(r.stderr[-4000:])
            check(False, "behaviour harness builds")
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            sys.stdout.write(r.stdout)
            if r.returncode:
                sys.stdout.write(r.stderr[-2000:])
                check(False, "behaviour harness")

# ---- preview build ---------------------------------------------------------------------------------
if shutil.which("xcrun") and sys.platform == "darwin":
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([sys.executable, str(R / "tools/ui-preview.py"), "--build-only", "--out", tmp], capture_output=True, text=True)
        check(r.returncode == 0, "the preview app builds without the engine", (r.stdout + r.stderr)[-3000:])
else:
    check(not args.require_preview, "Xcode on the host (preview build)")

print("check-bcd-session:", "FAIL" if failures else "PASS")
sys.exit(1 if failures else 0)
