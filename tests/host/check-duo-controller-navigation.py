#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Run the actual live controller sampler and focus-layer core on the host."""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[2]
library = (root / "app/Madeira/Library.swift").read_text()
focus = (root / "app/Madeira/BCDFocus.swift").read_text()
sampler = library[library.index("final class LibraryController:"):library.index("/// One game in the library")]
# Keep actual registration, layer and directional navigation; exclude platform polling/views.
core = focus[focus.index("enum BCDMove"):focus.index("    // MARK: Controllers")]
a = core.index("    init(watchControllers:")
b = core.index("    // MARK: Registration")
core = core[:a] + "    init(watchControllers: Bool = false) {}\n" + core[b:]
core += "    private func updatePolling() {}\n    private func stopRepeat() {}\n}\n"

art = (root / "app/Madeira/BCDArt.swift").read_text()
glow_identity = art[art.index("struct BCDGlowRenderID:"):art.index("struct BCDGlowItem")]

settings = (root / "app/Madeira/BCDSettings.swift").read_text()
def method(signature):
    start = settings.index(signature)
    brace = settings.index("{", start)
    depth = 1
    end = brace + 1
    while depth:
        if settings[end] == "{": depth += 1
        if settings[end] == "}": depth -= 1
        end += 1
    return settings[start:end] + "\n"
settings_fixture = """
enum BCDSettingsSection { case accounts, graphics }
@MainActor final class SettingsEntryFixture: ObservableObject {
 enum Mode { case split, stacked }
 @Published var selected = BCDSettingsSection.accounts
 @Published var pushed = false
 var mode = Mode.split
 var retainsDetail = true
 var order: [String] = []
 var focusFirst = false
 var focus: BCDFocus?
 init(_ focus: BCDFocus) { self.focus = focus }
 func open() { enter(.graphics, first: nil) }
 func hover() { show(.graphics) }
 private func setFocus(_ id: String) { focus?.focused = id }
""" + method("func orderChanged(_ ids:") + method("private func show(_ section:") + method("private func enter(_ section:") + "\n}\n"

# Exercise actual native-page lifecycle callbacks against the actual focus-layer core.
page = (root / "app/Madeira/BCDGamePage.swift").read_text()
page = page[page.index("struct BCDGamePage: View {"):]
foldable = (root / "app/Madeira/BCDFoldable.swift").read_text()
def extract(text, signature):
    start = text.index(signature)
    brace = text.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]
owner = extract(foldable, "@MainActor enum BCDDuoPageOwner {")
disappear = extract(page, ".onDisappear {").split("{", 1)[1][:-1]
returned = extract(page, ".onChange(of: workshop)").split("{ old, new in", 1)[1][:-1]
guards = page[page.index("    private func installButtons() {"):]
guards = guards[guards.index("{") + 1:guards.index("        // The DLC chooser:")]
page_fixture = owner + r'''
@MainActor final class LifecycleModel {
 var pending: Bool?
 var close: () -> Void = {}
 var opens = 0, leaves = 0
 var plan: (main: String?, unused: Bool) = ("Play", false)
 func answer(_ value: Bool) { pending = nil }
 func leave() { leaves += 1 }
 func open(_ store: Int) { opens += 1 }
 func request(_ action: String) {}
}
@MainActor final class PageLifecycleFixture {
 enum Layout { case phonePortrait, other }
 static let layer = "game.", panelLayer = "game.panel."
 let embeddedInNavigation = true
 let owner = UUID(), model = LifecycleModel()
 let focus: BCDFocus
 let store = 0
 let close: () -> Void = {}
 var visible = false, phoneUpright = false
 let layout = Layout.other
 let preview: (panel: String?, ask: String?, after: Double) = (nil, nil, 0)
 var options: String?, workshop: String?
 var handlersInstalled = 0
 init(_ focus: BCDFocus) { self.focus = focus }
 func openOptions(_ option: String) { options = option; focus.openLayer(Self.panelLayer) }
 func closeOptions() { options = nil; focus.closeLayer(Self.panelLayer) }
''' + extract(page, "    private func deactivateEmbeddedPage()").replace("private func", "func") + "\n" + extract(page, "    private func appeared()").replace("private func", "func") + "\nfunc disappeared() {" + disappear + "}\nfunc returned(old: String?, new: String?) {" + returned + "}\nfunc installButtons() {" + guards + "handlersInstalled += 1\n}\n}\n"
page_fixture = page_fixture.replace("DispatchQueue.main.async {", "DispatchQueue.main.async { [self] in").replace("+ preview.after) {", "+ preview.after) { [self] in")
lifecycle_tests = r'''
  for closeFirst in [false, true] {
   let modalFocus = BCDFocus(), page = PageLifecycleFixture(BCDFocus())
   let covered = PageLifecycleFixture(modalFocus)
   covered.appeared(); covered.openOptions("main")
   modalFocus.focused = "game.panel.row.workshop"
   covered.workshop = "area"
   if closeFirst { modalFocus.openLayer("workshop."); covered.disappeared() }
   else { covered.disappeared(); modalFocus.openLayer("workshop.") }
   modalFocus.focused = "workshop.close"
   precondition(covered.visible && covered.options == "main" && covered.model.leaves == 0)
   precondition(modalFocus.ownsLayer("workshop."), "cover keeps its layer in either appearance order")
   if closeFirst { modalFocus.closeLayer("workshop.") }
   covered.workshop = nil; covered.returned(old: "area", new: nil)
   RunLoop.main.run(until: Date().addingTimeInterval(0.02))
   if !closeFirst { modalFocus.closeLayer("workshop.") }
   covered.appeared()
   precondition(covered.options == "main" && covered.model.opens == 1, "return must not reopen/reset the game page")
   precondition(modalFocus.ownsLayer("game.panel.") && modalFocus.focused == "game.panel.row.workshop")
   // A delayed dismissal from an outgoing game cannot focus a newly active page.
   covered.workshop = "area"
   page.appeared()
   covered.workshop = nil; covered.returned(old: "area", new: nil)
   RunLoop.main.run(until: Date().addingTimeInterval(0.02))
   precondition(!covered.visible && !BCDDuoPageOwner.owns(covered.owner))
   precondition(modalFocus.focused != "game.panel.row.workshop")
   BCDDuoPageOwner.end()
  }
  let rootFocus = BCDFocus()
  rootFocus.scope(to: "home."); precondition(rootFocus.ownsRoot("home."))
  rootFocus.openLayer("system."); precondition(!rootFocus.ownsRoot("home."))
  rootFocus.closeLayer("system."); rootFocus.scope(to: "library.")
  precondition(!rootFocus.ownsRoot("home."), "late Home state cannot take another tab's focus")
  print("PASS: Workshop cover/dismiss ordering, preserved Options, outgoing-page ownership and root focus")
'''

stubs = r"""
import Foundation
import Combine
import SwiftUI
enum BCDDuoFeatures { static let active = true }
enum MadeiraConfig { static func flag(_ name: String, fallback: Bool = true) -> Bool { fallback } }
enum BCDChordSetting { enum Mode { case holdGuide, holdStart, chord }; static let mode = Mode.chord }
struct BCDChordTracker { mutating func reset() {}; mutating func update(buttons: UInt16, now: TimeInterval, mode: BCDChordSetting.Mode) -> Bool { false } }
enum BCDFocusSteppers { static func step(_ id: String?, _ move: BCDMove) -> Bool { false } }
"""
tests = r"""
@main struct Checks {
 @MainActor static func main() {
""" + lifecycle_tests + r"""
  let entryFocus = BCDFocus()
  let direct = SettingsEntryFixture(entryFocus)
  let entryRows = ["settings.graphics.resolution", "settings.graphics.frame-cap"]
  let directRows = direct.$selected.sink { if $0 == .graphics { direct.orderChanged(entryRows) } }
  direct.open()
  RunLoop.main.run(until: Date().addingTimeInterval(0.02))
  precondition(direct.order == entryRows && entryFocus.focused == entryRows.first, "synchronous rows survive direct entry")
  let hoverFocus = BCDFocus()
  let hover = SettingsEntryFixture(hoverFocus)
  let hoverRows = hover.$selected.sink { if $0 == .graphics { hover.orderChanged(entryRows) } }
  hover.hover()
  precondition(hover.order == entryRows, "sidebar selection must not erase synchronously delivered rows")
  hover.open()
  RunLoop.main.run(until: Date().addingTimeInterval(0.02))
  precondition(hoverFocus.focused == entryRows.first, "A after sidebar selection enters its first row")
  let pushedFocus = BCDFocus()
  let pushed = SettingsEntryFixture(pushedFocus)
  let pushedRows = pushed.$pushed.sink { if $0 { pushed.orderChanged(entryRows) } }
  pushed.open()
  RunLoop.main.run(until: Date().addingTimeInterval(0.02))
  precondition(pushed.order == entryRows && pushedFocus.focused == entryRows.first, "navigation publication cannot consume entry intent before it is established")
  withExtendedLifetime((directRows, hoverRows, pushedRows)) {}
  let target = "library.game.steam.25600"
  precondition(AnyHashable(BCDGlowRenderID(source: target)) != AnyHashable(target), "decorations cannot become interactive scroll targets")
  let pad = LibraryController()
  var commands: [String] = []
  let token = pad.commands.sink { commands.append($0) }
  func sample(_ buttons: UInt16 = 0, x: Int16 = 0) {
   pad.sample(buttons: buttons, lx: x, ly: 0)
   RunLoop.main.run(until: Date().addingTimeInterval(0.01))
  }
  pad.configure(enabled: true, ownsInput: true)
  sample(x: 20000)
  precondition(commands == ["right"], "first tilt is delivered")
  sample(x: 20000)
  precondition(commands.count == 1, "no immediate duplicate")
  Thread.sleep(forTimeInterval: 0.36); sample(x: 20000)
  precondition(commands == ["right", "right"], "held stick repeats without face button")
  Thread.sleep(forTimeInterval: 0.13); sample(0x1000, x: 20000)
  precondition(commands.last == "accept", "new A wins over a due directional repeat")
  sample(); sample(0x1000)
  Thread.sleep(forTimeInterval: 0.36); sample(0x1000)
  precondition(commands.filter { $0 == "accept" }.count == 2, "A never repeats")
  sample(); commands = []
  pad.configure(enabled: true, ownsInput: false)
  sample(0x30)
  precondition(commands == ["menu"], "gameplay chord opens menu")
  pad.configure(enabled: true, ownsInput: true)
  sample(0x30)
  precondition(commands == ["menu"], "ownership transfer does not replay held Start")
  sample(); sample(0x10)
  precondition(commands == ["menu", "menu"], "released then repressed Start works")
  withExtendedLifetime(token) {}

  let focus = BCDFocus()
  var behind = 0, cancelled = 0
  focus.register("settings.row", frame: CGRect(x: 0, y: 0, width: 50, height: 50))
  focus.focused = "settings.row"
  focus.steer = { _ in behind += 1; return true }
  focus.setHandlers([(BCDHint(button: .b, title: "Back"), { behind += 1 })])
  focus.preview(focused: "settings.row")
  focus.openLayer("menu.")
  focus.register("menu.ask.no", frame: CGRect(x: 10, y: 10, width: 50, height: 50))
  focus.focused = "menu.ask.no"
  focus.setHandlers([(BCDHint(button: .b, title: "Cancel"), { cancelled += 1 })])
  focus.move(.right); focus.press(.b)
  precondition(behind == 0 && cancelled == 1, "modal exclusively owns steering and B")
  precondition(!focus.ownsLayerTree("game."))
  focus.closeLayer("menu.")
  focus.move(.right); focus.press(.b)
  precondition(behind == 2 && focus.focused == "settings.row", "closing restores prior screen")
  precondition(BCDGridNavigation.target(index: 99, columns: 4, count: 1154, direction: .down) == 103)
  precondition(BCDGridNavigation.target(index: 1153, columns: 4, count: 1154, direction: .down) == 1153)
  precondition(BCDGridNavigation.target(index: 1, columns: 4, count: 1154, direction: .up) == nil)
  precondition(BCDRailPosition.offset(index: 0, count: 11, width: 122, spacing: 18, viewport: 803) == 0)
  precondition(BCDRailPosition.offset(index: 1, count: 11, width: 122, spacing: 18, viewport: 803) == 0)
  precondition(BCDRailPosition.offset(index: 5, count: 11, width: 122, spacing: 18, viewport: 803) == 361.5)
  precondition(BCDRailPosition.offset(index: 10, count: 11, width: 122, spacing: 18, viewport: 803) == 723)
  precondition(BCDRailPosition.offset(index: 0, count: 0, width: 122, spacing: 18, viewport: 803) == 0)
  let recent = ["home.recent.godot", "home.recent.blender", "home.add"]
  func home(_ id: String, _ direction: BCDMove, _ desktop: [String], portrait: Bool = false) -> String? {
   BCDHomeNavigation.target(from: id, direction: direction, recent: recent, desktop: desktop,
      hero: "home.start", downloadsBelow: portrait, remembered: recent[1])
  }
  let runningDesktop = ["home.windows.open", "home.windows.stop"]
  precondition(home(recent[0], .right, runningDesktop) == recent[1])
  precondition(home(recent[1], .left, runningDesktop) == recent[0])
  precondition(home(recent[1], .down, runningDesktop) == "home.windows.open", "Down selects enabled Open when Windows runs")
  precondition(home("home.windows.open", .right, runningDesktop) == "home.windows.stop")
  precondition(home("home.windows.stop", .up, runningDesktop) == recent[1], "Up remembers the game row selection")
  precondition(home(recent[1], .down, ["home.windows", "home.windows.options"]) == "home.windows")
  precondition(home(recent[1], .down, ["home.windows.restart", "home.windows.options"]) == "home.windows.restart")
  precondition(home(recent[1], .down, [], portrait: true) == "home.downloads")
  precondition(home("home.downloads", .down, runningDesktop, portrait: true) == "home.windows.open")
  precondition(BCDHomeNavigation.target(from: "home.start", direction: .right, recent: recent,
    desktop: runningDesktop, hero: "home.start", downloadsBelow: false, downloadsVisible: false,
    remembered: nil) == "home.start", "compact landscape omits the empty Downloads card")
  let scoped = BCDFocus()
  scoped.register("library.game.hidden", frame: CGRect(x: 60, y: 0, width: 50, height: 50))
  scoped.register("home.recent.godot", frame: CGRect(x: 0, y: 0, width: 50, height: 50))
  scoped.register("home.recent.blender", frame: CGRect(x: 120, y: 0, width: 50, height: 50))
  scoped.scope(to: "home.")
  scoped.preview(focused: "home.recent.godot")
  scoped.move(.right)
  precondition(scoped.focused == "home.recent.blender", "retained hidden-tab frames cannot create a dead step")
  scoped.move(.left)
  precondition(scoped.focused == "home.recent.godot", "reverse movement stays in active tab")
  let grid = BCDFocus()
  let ids = (0..<1154).map { "library.game.\($0)" }
  grid.setNavigator("library.game.", owner: UUID()) { direction in
   guard let id = grid.focused, let index = ids.firstIndex(of: id),
         let next = BCDGridNavigation.target(index: index, columns: 4, count: ids.count, direction: direction) else { return false }
   grid.focused = ids[next]; return true
  }
  grid.preview(focused: ids[99]) // deliberately no frames: lazy cells not created yet
  grid.move(.down); grid.move(.down)
  precondition(grid.focused == ids[107], "ordered navigation progresses before lazy realization")
  focus.openLayer("game."); focus.openLayer("game.dlc.")
  precondition(focus.ownsLayerTree("game."), "nested game layers retain page handlers")
  print("PASS: live stick repeat, first tilt, nonrepeating actions, chord handoff, modal ownership and restoration")
 }
}
"""
with tempfile.TemporaryDirectory(prefix="bcd-duo-navigation-") as folder:
    source = Path(folder) / "Checks.swift"
    binary = Path(folder) / "checks"
    source.write_text(stubs + glow_identity + sampler + core + settings_fixture + page_fixture + tests)
    subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-parse-as-library", str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=20)
