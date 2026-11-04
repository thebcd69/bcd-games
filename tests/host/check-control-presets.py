#!/usr/bin/env python3
"""Exercise the production touch-control layout store without an iOS SDK.

Part A compiles the Foundation-only blocks (ControlAction/TouchControl from
ContentView.swift, the pure touch state from TouchGamepad.swift and the pure
preset state from TouchControlPresets.swift) on the host: the built-in
controller layout on phone and tablet screens, built-ins read-only, "Custom
Layout N" naming, encode/decode and what loading a layout puts on screen.

The preset is checked as data: its name, id and its 18 bindings, each an XInput
mapping that produces a non-neutral pad sample; and the key sticks' glyphs. The
built-in PC keyboard layout (a WASD stick, the mouse buttons and the usual keys)
is checked the same way, and the kind/plan the log line `[touch] layout=...` states
and the built-in a game gets when its touch controls are on with nothing on screen.

Part B checks the UI wiring in the same files: kill switches, the write-back
on the end of an edit, the default for new users only, and the session slot.
Set SWIFTC if Swift is not on PATH.
"""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
content = (app / 'ContentView.swift').read_text()
touch = (app / 'TouchGamepad.swift').read_text()
presets = (app / 'TouchControlPresets.swift').read_text()

# ---------------------------------------------------------------- Part A
actions = content[content.index('enum ControlAction: Codable'):content.index('final class TouchControlsModel')]
pure_touch = touch.split('// MARK: - Pure touch state', 1)[1].split('// MARK: - UIKit touch lifetime', 1)[0]
pure_touch = pure_touch[pure_touch.index('struct GamepadSample'):]
pure = presets.split('// MARK: - Pure preset state', 1)[1].split('// MARK: - Persistence', 1)[0]
pure = pure[pure.index('struct ControlPreset'):]
for block in (actions, pure):
    assert 'SwiftUI' not in block and 'UIKit' not in block, 'the layout core stays Foundation-only'

tests = r'''
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}

let screens: [(String, ControlPresetScreen)] = [
    ("6.3in phone", ControlPresetScreen(width: 874, height: 402, left: 59, right: 59, top: 0, bottom: 21)),
    ("6.1in phone", ControlPresetScreen(width: 844, height: 390, left: 47, right: 47, top: 0, bottom: 21)),
    ("6.9in phone", ControlPresetScreen(width: 956, height: 440, left: 62, right: 62, top: 0, bottom: 21)),
    ("4.7in phone", ControlPresetScreen(width: 667, height: 375)),
    ("insets unreported", ControlPresetScreen(width: 874, height: 402)),
    ("11in tablet", ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20)),
    ("13in tablet", ControlPresetScreen(width: 1366, height: 1024, top: 24, bottom: 20)),
    ("mini tablet", ControlPresetScreen(width: 1133, height: 744, top: 24, bottom: 20)),
    ("phone, portrait", ControlPresetScreen(width: 402, height: 874, left: 0, right: 0, top: 62, bottom: 34)),
]
let expected = ["LS", "RS", "D↑", "D↓", "D←", "D→", "A", "B", "X", "Y",
                "LB", "RB", "LT", "RT", "View", "Menu", "L3", "R3"]
func find(_ cs: [TouchControl], _ name: String) -> TouchControl { cs.first { $0.action == .pad(name) }! }

// --- Built-in controller layout: complete, mapped, on screen, non-overlapping.
for (label, screen) in screens {
    let s = screen.landscape
    let cs = ControlPresetLayout.xbox(for: screen)
    let names = cs.compactMap { $0.action.padName }
    require(cs.count == expected.count && expected.allSatisfy { n in names.filter { $0 == n }.count == 1 },
            "\(label): sticks, D-pad, ABXY, bumpers, triggers, View/Menu, L3/R3 once each")
    require(names.allSatisfy(TouchPadAction.supported), "\(label): every control is a supported XInput mapping")
    require(cs.allSatisfy { $0.scale >= 0.5 && $0.scale <= 3.0 }, "\(label): scales inside the pinch clamp")
    require(cs.allSatisfy { $0.nx >= 0.03 && $0.nx <= 0.97 && $0.ny >= 0.03 && $0.ny <= 0.97 },
            "\(label): positions inside the editor's drag clamp")
    let m = ControlPresetLayout.margins(s)
    let safe = CGRect(x: m.left, y: m.top, width: s.width - m.left - m.right, height: s.height - m.top - m.bottom)
    let boxes = cs.map { ControlPresetLayout.box($0, screen: s) }
    require(boxes.allSatisfy { safe.contains($0) }, "\(label): every control inside the safe area")
    var overlaps: [String] = []
    for i in cs.indices { for j in cs.indices where j > i {
        let a = boxes[i], b = boxes[j]
        // Round controls (a square box) are compared as circles, so the face
        // diamond's diagonal neighbours may share box corners; anything else
        // (wide shoulders, Start/Select pills) as its drawn box. 2 pt gap.
        let dx = a.midX - b.midX, dy = a.midY - b.midY
        let round = abs(a.width - a.height) < 1e-9 && abs(b.width - b.height) < 1e-9
        if round ? (dx * dx + dy * dy).squareRoot() < a.width / 2 + b.width / 2 + 2
                 : a.insetBy(dx: -1, dy: -1).intersects(b) {
            overlaps.append("\(cs[i].action.label)/\(cs[j].action.label)")
        }
    } }
    require(overlaps.isEmpty, "\(label): no two controls overlap \(overlaps)")
    let bar = ControlPresetLayout.topBarRect(s)
    require(!boxes.contains { $0.intersects(bar) }, "\(label): the overlay's top buttons stay free")
    let l = find(cs, "LS"), r = find(cs, "RS"), up = find(cs, "D↑"), down = find(cs, "D↓")
    let left = find(cs, "D←"), right = find(cs, "D→")
    let a = find(cs, "A"), b = find(cs, "B"), x = find(cs, "X"), y = find(cs, "Y")
    let lt = find(cs, "LT"), lb = find(cs, "LB"), rt = find(cs, "RT"), rb = find(cs, "RB")
    let view = find(cs, "View"), menu = find(cs, "Menu")
    require(l.nx < 0.3 && l.ny > 0.6 && r.nx > 0.7 && r.ny > 0.6, "\(label): sticks in the bottom corners")
    require(down.ny < l.ny && up.ny < down.ny && left.nx < up.nx && up.nx < right.nx && abs(up.nx - down.nx) < 1e-9,
            "\(label): D-pad cross above the left stick")
    require(y.ny < x.ny && x.ny == b.ny && b.ny < a.ny && x.nx < y.nx && y.nx < b.nx && abs(y.nx - a.nx) < 1e-9
            && a.ny < r.ny && a.nx > 0.5, "\(label): Y top, X left, B right, A bottom, above the right stick")
    require(lt.ny < 0.2 && abs(lt.nx - lb.nx) < 1e-9 && lt.ny < lb.ny && lb.nx < 0.5
            && abs(rt.nx - rb.nx) < 1e-9 && rt.ny < rb.ny && rb.nx > 0.5,
            "\(label): shoulder columns in the top corners, triggers above bumpers")
    require(view.nx < 0.5 && menu.nx > 0.5 && abs(view.ny - menu.ny) < 1e-9, "\(label): View left of Menu, centre")
}
let ids = Set(ControlPresetLayout.xbox(for: .referencePhone).map { $0.id })
require(ids.count == expected.count, "built-in controls have distinct ids")

// --- The Xbox controller preset as data: what a user is offered and what each button does.
let builtIn = ControlPresetStore.builtIns
require(builtIn.count == 2 && builtIn[0].id == ControlPresetLayout.xboxID && builtIn[0].name == "Xbox controller"
        && builtIn[1].id == ControlPresetLayout.keyboardID && builtIn[1].name == "PC keyboard",
        "two built-ins: \"Xbox controller\" first, then \"PC keyboard\"")
require(builtIn[0].controls.count == 18 && Set(builtIn[0].controls.compactMap { $0.action.padName }) == Set(expected),
        "the built-in binds the 18 controller inputs, none twice")
require(builtIn[0].controls.allSatisfy { c in
            guard let n = c.action.padName else { return false }
            return TouchPadAction.sample(n, x: 1, y: 1) != GamepadSample()
        }, "every built-in binding produces a non-neutral XInput sample when pressed or deflected")
require(ControlPresetStore().preset(ControlPresetLayout.xboxID)?.name == "Xbox controller"
        && ControlPresetStore(user: []).all.first?.id == ControlPresetLayout.xboxID,
        "a store with no user layouts still offers the built-in first")
let one = ControlPresetStore(user: [ControlPreset(id: "u", name: "Mine", controls: [])])
require(one.resolvedID(ControlPresetLayout.xboxID) == ControlPresetLayout.xboxID && one.resolvedID("u") == "u",
        "a game's remembered built-in or custom layout resolves")
require(one.resolvedID("gone") == nil && one.resolvedID(nil) == nil, "a deleted or absent layout resolves to none")

// --- The built-in PC keyboard layout: complete, on screen, non-overlapping, and nothing but keys and the mouse.
let keyboardExpected: [ControlAction] = [.joystickWASD, .mouseLeft, .mouseRight, .key(0x20), .key(0x45), .key(0x52), .key(0x46),
                                         .key(0x10), .key(0x11), .key(0x51), .key(0x1B), .key(0x09), .key(0x0D), .keyboardToggle]
for (label, screen) in screens {
    let s = screen.landscape
    let cs = ControlPresetLayout.keyboard(for: screen)
    require(cs.count == keyboardExpected.count && keyboardExpected.allSatisfy { a in cs.filter { $0.action == a }.count == 1 },
            "\(label): PC keyboard: WASD, both mouse buttons, Space, E, R, F, Shift, Ctrl, Q, Esc, Tab, Enter and the keyboard once each")
    require(cs.allSatisfy { !$0.action.isPad }, "\(label): PC keyboard: no controller input in it")
    require(cs.allSatisfy { $0.scale >= 0.5 && $0.scale <= 3.0 }, "\(label): PC keyboard: scales inside the pinch clamp")
    require(cs.allSatisfy { $0.nx >= 0.03 && $0.nx <= 0.97 && $0.ny >= 0.03 && $0.ny <= 0.97 },
            "\(label): PC keyboard: positions inside the editor's drag clamp")
    let m = ControlPresetLayout.margins(s)
    let safe = CGRect(x: m.left, y: m.top, width: s.width - m.left - m.right, height: s.height - m.top - m.bottom)
    let boxes = cs.map { ControlPresetLayout.box($0, screen: s) }
    require(boxes.allSatisfy { safe.contains($0) }, "\(label): PC keyboard: every control inside the safe area")
    var overlaps: [String] = []
    for i in cs.indices { for j in cs.indices where j > i {
        let dx = boxes[i].midX - boxes[j].midX, dy = boxes[i].midY - boxes[j].midY
        if (dx * dx + dy * dy).squareRoot() < boxes[i].width / 2 + boxes[j].width / 2 + 2 {
            overlaps.append("\(cs[i].action.label)/\(cs[j].action.label)")
        }
    } }
    require(overlaps.isEmpty, "\(label): PC keyboard: no two controls overlap \(overlaps)")
    let bar = ControlPresetLayout.topBarRect(s)
    require(!boxes.contains { $0.intersects(bar) }, "\(label): PC keyboard: the overlay's top buttons stay free")
    let wasd = cs.first { $0.action == .joystickWASD }!, mouseLeft = cs.first { $0.action == .mouseLeft }!
    let esc = cs.first { $0.action == .key(0x1B) }!, enter = cs.first { $0.action == .key(0x0D) }!
    require(wasd.nx < 0.3 && wasd.ny > 0.6 && mouseLeft.nx > 0.7 && mouseLeft.ny > 0.7 && esc.nx < 0.2 && esc.ny < 0.2
            && enter.nx > 0.8 && enter.ny < 0.2, "\(label): PC keyboard: the stick bottom left, the mouse button bottom right, Esc and Enter in the top corners")
}
require(Set(ControlPresetLayout.keyboard(for: .referencePhone).map { $0.id }).count == keyboardExpected.count, "keyboard controls have distinct ids")
require(builtIn[1].controls.count == 14 && TouchLayoutKind.of(builtIn[1].controls) == .keys,
        "the built-in keyboard layout is stored for the reference phone, 14 controls, all keys and mouse")
var readOnlyStore = ControlPresetStore()
require(ControlPresetStore.isBuiltIn(ControlPresetLayout.keyboardID) && !readOnlyStore.delete(id: ControlPresetLayout.keyboardID)
        && !readOnlyStore.overwrite(id: ControlPresetLayout.keyboardID, controls: []),
        "the keyboard built-in is read-only too")
require(readOnlyStore.save(name: "pc KEYBOARD ", controls: [], newID: { "x" }) == .refusedBuiltIn, "a built-in's name is refused: PC keyboard")
let fitKeyboard = ControlPresetStore.layout(of: ControlPresetStore.builtIns[1], screen: ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20))
require(fitKeyboard.map { $0.action } == ControlPresetLayout.keyboard(for: ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20)).map { $0.action }
        && fitKeyboard.map { $0.scale } == ControlPresetLayout.keyboard(for: ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20)).map { $0.scale },
        "the keyboard built-in is laid out again for the screen it is loaded on")
let keyboardJSON = String(decoding: try! JSONEncoder().encode(builtIn[1].controls), as: UTF8.self)
require(keyboardJSON.contains("joystickWASD") && keyboardJSON.contains("mouseLeft") && !keyboardJSON.contains("\"pad\""),
        "the keyboard built-in stores keys, never a controller input")

// --- What the touch layer does with a layout (the log's kind=, keys-path=, pad=) and which built-in a game gets.
let padLayout = ControlPresetLayout.xbox(for: .referencePhone), keyLayout = ControlPresetLayout.keyboard(for: .referencePhone)
require(TouchLayoutKind.of(padLayout) == .pad && TouchLayoutKind.of(keyLayout) == .keys
        && TouchLayoutKind.of(padLayout + keyLayout) == .mixed && TouchLayoutKind.of([]) == .none,
        "kind: the controller layout is pad, the keyboard layout keys, both mixed, none none")
var noAction = TouchControl(); noAction.action = .none
var oddPad = TouchControl(); oddPad.action = .pad("Nope")
require(TouchLayoutKind.of([noAction, oddPad]) == .none, "kind: a control that does nothing, or an unsupported controller input, counts for neither")
var toggle = TouchControl(); toggle.action = .keyboardToggle
require(TouchLayoutKind.of([toggle]) == .keys, "kind: the keyboard button is keys")
let onKeys = TouchLayoutPlan.make(layout: "PC keyboard", controls: keyLayout, shown: true, accepting: true, padAvailable: true)
require(onKeys.line == "[touch] layout=\"PC keyboard\" kind=keys keys-path=on pad=n/a controls=14",
        "log line for a keyboard layout: keys path on, no pad: \(onKeys.line)")
let onPad = TouchLayoutPlan.make(layout: "Xbox controller", controls: padLayout, shown: true, accepting: true, padAvailable: true)
require(onPad.line == "[touch] layout=\"Xbox controller\" kind=pad keys-path=n/a pad=connected controls=18", "log line for the controller layout: pad connected: \(onPad.line)")
require(TouchLayoutPlan.make(layout: "x", controls: padLayout, shown: true, accepting: false, padAvailable: true).pad == "waiting",
        "a menu, the starting screen or the editor has the pad: waiting")
require(TouchLayoutPlan.make(layout: "x", controls: padLayout, shown: true, accepting: true, padAvailable: false).pad == "off",
        "touch XInput off: the controller buttons are inert, and the line says pad=off")
require(TouchLayoutPlan.make(layout: "x", controls: padLayout, shown: false, accepting: true, padAvailable: true).pad == "hidden"
        && TouchLayoutPlan.make(layout: "x", controls: keyLayout, shown: false, accepting: true, padAvailable: true).keysPath == "off",
        "hidden controls: pad=hidden, keys-path=off")
let mixedPlan = TouchLayoutPlan.make(layout: "unsaved", controls: padLayout + keyLayout, shown: true, accepting: true, padAvailable: true)
require(mixedPlan.kind == .mixed && mixedPlan.keysPath == "on" && mixedPlan.pad == "connected", "a mixed layout: both paths on")
require(TouchLayoutPlan.make(layout: "none", controls: [], shown: true, accepting: true, padAvailable: true).line
        == "[touch] layout=\"none\" kind=none keys-path=n/a pad=n/a controls=0", "no controls: kind=none")
require(ControlPresetLayout.defaultID(steam: true, touchPad: true) == ControlPresetLayout.xboxID
        && ControlPresetLayout.defaultID(steam: false, touchPad: true) == ControlPresetLayout.keyboardID
        && ControlPresetLayout.defaultID(steam: true, touchPad: false) == ControlPresetLayout.keyboardID
        && ControlPresetLayout.defaultID(steam: false, touchPad: false) == ControlPresetLayout.keyboardID,
        "default built-in: the controller for a Steam game, the keyboard for any other, and for every game without touch XInput")

// --- The key sticks carry a glyph that tells them apart; nothing else does.
require(ControlAction.joystickWASD.stickGlyph == "keyboard", "the WASD stick's glyph is the keyboard")
require(ControlAction.joystickArrows.stickGlyph == "arrow.up.and.down.and.arrow.left.and.right",
        "the arrow-key stick's glyph is the arrows")
require(ControlAction.joystickWASD.stickGlyph != ControlAction.joystickArrows.stickGlyph, "WASD and Arrows differ")
let plain: [ControlAction] = [.none, .mouseLeft, .mouseRight, .keyboardToggle, .key(0x20), .pad("LS"), .pad("RS"), .pad("A")]
require(plain.allSatisfy { $0.stickGlyph == nil }, "no other control (nor the controller sticks) has a stick glyph")
require(ControlAction.joystickWASD.stickKeys == [0x57, 0x44, 0x53, 0x41]
        && ControlAction.joystickArrows.stickKeys == [0x26, 0x27, 0x28, 0x25], "the glyph does not change the keys a stick posts")

// --- Encode / decode round trip.
var store = ControlPresetStore()
var custom = TouchControl()
custom.nx = 0.2; custom.ny = 0.7; custom.scale = 1.4; custom.action = .key(0x20)
var stick = TouchControl(); stick.action = .pad("LS")
let layout = [custom, stick] + ControlPresetLayout.xbox(for: .referencePhone)
var n = 0
let fixedID: () -> String = { n += 1; return "user-\(n)" }
require(store.save(name: "  Racing  ", controls: layout, newID: fixedID) == .created("user-1"), "save creates")
require(store.save(name: "Shooter", controls: [custom], newID: fixedID) == .created("user-2"), "second layout")
require(store.preset("user-1")?.name == "Racing", "names are trimmed")
let data = try! store.encoded()
let back = try! ControlPresetStore.decoded(data)
require(back == store && back.user.count == 2, "user layouts round-trip through JSON")
require(back.preset("user-1")?.controls == layout, "keyboard and controller actions survive")
let text = String(decoding: data, as: UTF8.self)
require(!text.contains(ControlPresetLayout.xboxID), "built-ins are never written to the file")
let future = #"{"version":2,"presets":[]}"#
require((try? ControlPresetStore.decoded(Data(future.utf8))) == nil, "a newer file version is refused, not misread")
let smuggled = try! ControlPresetStore.decoded(Data(#"{"version":1,"presets":[{"id":"builtin.xbox","name":"X","controls":[]}]}"#.utf8))
require(smuggled.user.isEmpty && smuggled.preset(ControlPresetLayout.xboxID)?.controls.count == expected.count,
        "a file entry cannot shadow a built-in")
let saved = #"{"version":1,"presets":[{"id":"p","name":"Old","controls":[{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.5,"scale":1,"action":{"pad":{"_0":"A"}}}]}]}"#
require((try? ControlPresetStore.decoded(Data(saved.utf8)))?.preset("p")?.controls.first?.action == .pad("A"),
        "a stored controller mapping decodes by name")

// --- Built-ins are read-only; naming.
let xbox = ControlPresetLayout.xboxID
require(ControlPresetStore.isBuiltIn(xbox) && store.all.first?.id == xbox, "the controller layout is built in and listed first")
require(!store.delete(id: xbox) && !store.overwrite(id: xbox, controls: []), "built-in not deletable or writable")
require(store.save(name: "xbox CONTROLLER ", controls: [], newID: fixedID) == .refusedBuiltIn, "a built-in's name is refused")
require(store.save(name: "   ", controls: [], newID: fixedID) == .refusedEmpty, "an empty name is refused")
require(store.save(name: "racing", controls: [custom], newID: fixedID) == .replaced("user-1")
        && store.preset("user-1")?.controls == [custom], "saving under a user layout's name replaces it")
require(store.overwrite(id: "user-2", controls: layout) && store.preset("user-2")?.controls == layout, "overwrite a user layout")
require(store.nextCustomName() == "Custom Layout 1", "first custom name")
_ = store.save(name: "Custom Layout 1", controls: [], newID: fixedID)
_ = store.save(name: "custom layout 3", controls: [], newID: fixedID)
require(store.nextCustomName() == "Custom Layout 2", "the lowest free number, case-insensitive")
_ = store.save(name: "Custom Layout 2", controls: [], newID: fixedID)
require(store.nextCustomName() == "Custom Layout 4", "numbers already taken are skipped")
require(store.delete(id: "user-2") && store.preset("user-2") == nil && !store.delete(id: "user-2"), "delete once")
require(ControlPresetStore.clean(String(repeating: "x", count: 99)).count == ControlPresetStore.maxNameLength, "names are capped")

// --- What loading puts on screen.
var wild = TouchControl(); wild.nx = -1; wild.ny = 5; wild.scale = 9; wild.action = .pad("A")
let loaded = ControlPresetStore.layout(of: ControlPreset(id: "w", name: "W", controls: [wild, wild]), screen: nil)
require(loaded.count == 2 && loaded[0].id != loaded[1].id && loaded[0].id != wild.id, "fresh ids on every load")
require(loaded[0].nx == 0.03 && loaded[0].ny == 0.97 && loaded[0].scale == 3.0, "positions and scale clamped")
let tablet = ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20)
let fitted = ControlPresetStore.layout(of: ControlPresetStore.builtIns[0], screen: tablet)
require(fitted.map { $0.action } == ControlPresetLayout.xbox(for: tablet).map { $0.action }
        && fitted.map { $0.scale } == ControlPresetLayout.xbox(for: tablet).map { $0.scale },
        "a built-in is laid out again for the screen it is loaded on")

if failures > 0 { print("\(failures) FAILED"); exit(1) }
print("PASS: built-in controller layout, layout store, naming and loading")
'''

with tempfile.TemporaryDirectory(prefix='madeira-presets-') as tmp:
    src, exe = Path(tmp) / 'main.swift', Path(tmp) / 'check'
    # CoreGraphics where it exists: on macOS, Foundation alone no longer gives CGRect
    # its members (midX, width, ...); Linux's Foundation still does.
    header = 'import Foundation\n#if canImport(CoreGraphics)\nimport CoreGraphics\n#endif\n'
    src.write_text(header + actions + pure_touch + pure + tests)
    subprocess.run([os.environ.get('SWIFTC', 'swiftc'), str(src), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)

# ---------------------------------------------------------------- Part B
def function(source, start):
    i = source.index(start); b = source.index('{', i); depth = 1; k = b + 1
    while depth:
        depth += (source[k] == '{') - (source[k] == '}'); k += 1
    return source[i:k]

gamepad = (app / 'GamepadInput.swift').read_text()
# (Wave 3, 2026-10-19: MADEIRA_CONTROLS_EDITOR_DONE, the classic bar's kill switch, is deleted with that bar.)
for flag, where in (('MADEIRA_CONTROL_PRESETS', presets),):
    assert f'.flag("{flag}")' in where, flag + ' is a kill switch'
# Defaults an existing user would notice are opt-in: the built-in is never applied
# automatically and player 1 is not reserved unless the switch is set to 1.
for flag, where in (('MADEIRA_CONTROLS_XBOX_DEFAULT', presets), ('MADEIRA_PAD_EARLY_SLOT', gamepad)):
    assert f'.optIn("{flag}")' in where and f'.flag("{flag}")' not in where, flag + ' is opt-in'
assert '!= "0"' in function(gamepad, 'static func flag('), 'only "0" disables'
assert '== "1"' in function(gamepad, 'static func optIn('), 'only "1" enables'
assert 'Self.xboxDefault' in function(presets, 'var defaultPending: Bool {'), 'the automatic built-in needs the opt-in'

model = content[content.index('final class TouchControlsModel'):content.index('/// Click-through EXCEPT')]
assert 'if oldValue && !editing { ControlPresetsModel.shared.editingEnded(baseline: editBaseline) }' in model
assert 'needsDefaultLayout = !FileManager.default.fileExists(atPath: Self.url.path)' in model, 'default only without a file'
assert 'var layout: String?' in model, 'older controls files still decode'
ended = function(presets, 'func editingEnded(baseline: [TouchControl]) {')
assert ended.index('guard Self.enabled else { return }') < ended.index('s.overwrite(id: id'), 'write-back is switchable'
default = function(presets, 'func applyDefaultIfNeeded(screen: ControlPresetScreen) {')
assert 'm.controls.isEmpty' in default and 'm.needsDefaultLayout = false' in default, 'applied once, never over controls'
assert 'Self.touchPad' in function(presets, 'var defaultPending: Bool {'), 'no controller default without touch XInput'
overlay = function(content, 'struct TouchControlsOverlay: View {')
assert 'ControlPresetsModel.enabled && m.visible && !m.editing' in overlay, 'menu only while touch controls are shown'
# (Wave 3, 2026-10-19: the classic bar's Done button and MADEIRA_CONTROLS_EDITOR_DONE are deleted; BCD's editor has its own Done.)
assert 'TouchControlsOverlay.showsLayoutMenu(self) ? 4 : 3) + 1' in model, 'the menu button is hit-testable'
window = function(content, 'final class ControlsWindow: UIWindow {')
assert window.index('if m.editing || m.sessionPanel {') < window.index('!hit.isDescendant(of: root)') \
    < window.index('guard m.hitsInteractive('), 'menu and dialog presentations take their touches in play mode'
reserve = function(gamepad, '@MainActor func reserveSessionSlot(touchControls: Bool) {')
assert reserve.index('guard Self.enabled, gameSet || Self.optIn("MADEIRA_PAD_EARLY_SLOT")') < reserve.index('touchState.reserved = true')
# Round 630 (agent T1 S1, Dokimon Quest): the game's own file can set it (a list row), exactly "1"; a set switch that reserves nothing says so.
assert 'let gameSet = MadeiraConfig.gameValue("env.MADEIRA_PAD_EARLY_SLOT") == "1"' in reserve and 'slot=0 not reserved' in reserve
run = function(content, 'private func runWineFullSequence(profile: LibraryEntry? = nil) {')
assert run.index('GamepadInput.shared.reserveSessionSlot(') < run.index('DispatchQueue.global'), 'reserved before Wine starts'

# The built-in reaches a library session through BCD's in-game menu (wave 3, 2026-10-19: LibraryHUD, its layout row and the
# classic ControlLayoutMenu are deleted; check-bcd-touch.py pins BCD's touch section and layout menu), and a game remembers
# the layout it was loaded from.
lib = (app / 'Library.swift').read_text()
assert 'if session && !m.editing { BCDSessionLiveLayer() } else { topBar }' in overlay, 'a session shows BCD\'s layer instead of the top bar'
assert 'ControlLayoutMenu' not in lib + presets and 'struct LibraryHUD' not in lib, 'the classic layout menu and HUD are gone'
entry = function(lib, 'struct LibraryEntry: Codable, Identifiable {')
assert 'var controlLayout: String?' in entry, 'optional, so older library files decode'
model_lib = function(lib, 'final class LibraryModel: ObservableObject {')
begin = function(model_lib, 'func begin(_ entry: LibraryEntry')
assert 'store.resolvedID(entry.controlLayout)' in begin and 'savedLayout = controls.layoutID' in begin
assert begin.index('if let profile = entry.controls') < begin.index('resolvedID'), "the layout follows the game's own controls only"
assert 'entry.controlLayout = controls.layoutID' in function(model_lib, 'func saveCurrentProfile()')
fin = function(model_lib, 'private func finish()')
assert fin.index('saveCurrentProfile()') < fin.index('controls.layoutID = savedLayout'), 'the session restores the shared layout after saving'

# 578 (tester: "keyboard layout in custom games doesn't work ... need manually to add pc keyboard"): the built-in PC keyboard
# layout is listed whether or not touch XInput is on, and a game whose touch controls are on with nothing on screen gets the
# built-in that suits it. Only an empty overlay changes: controls or a layout the player has are never replaced.
assert '.flag("MADEIRA_TOUCH_GAME_DEFAULT")' in presets and '.optIn("MADEIRA_TOUCH_GAME_DEFAULT")' not in presets, \
    'the game default is on, with a kill switch at 0'
available = function(presets, 'var available: [ControlPreset] {')
assert '$0.id != ControlPresetLayout.xboxID || Self.touchPad' in available, 'only the controller built-in needs touch XInput'
apply = function(presets, '@discardableResult func applyGameDefault(steam: Bool, reason: String) -> Bool {')
for needle in ('Self.enabled', 'm.visible', '!m.editing', 'm.controls.isEmpty', 'm.layoutID == nil'):
    assert needle in apply.split('guard Self.gameDefault')[0], 'the game default only fills an empty overlay: ' + needle
assert apply.index('guard Self.gameDefault') < apply.index('load(id,'), 'the kill switch comes before the layout is put on screen'
assert 'ControlPresetLayout.defaultID(steam: steam, touchPad: Self.touchPad)' in apply
assert '[touch] default layout=' in apply and 'MADEIRA_TOUCH_GAME_DEFAULT=0' in apply, 'every decision is logged'
# 578 (lead): no default at session start (an empty overlay with touch controls on may be the player's touch-as-mouse
# setup); only turning touch controls on in the in-game menu fills an empty overlay.
assert 'applyGameDefault(' not in begin and 'ControlPresetsModel.shared.sessionBegan()' in begin, \
    'a session never puts a default layout on screen by itself'
live = (app / 'BCDTouchLive.swift').read_text()
set_visible = function(live, 'func setVisible(_ on: Bool) {')
assert 'presets.applyGameDefault(' in set_visible and 'if on, let entry = library.activeEntry, inLibrarySession' in set_visible \
    and set_visible.index('model.visible = on') < set_visible.index('applyGameDefault('), 'turning touch controls on in a library session fills an empty overlay'
configure = function(overlay, 'private func configureGamepad(landscape: Bool) {')
assert 'ControlPresetsModel.shared.logPlan(shown: landscape && m.visible, accepting: accepting)' in configure \
    and configure.index('GamepadInput.shared.configureTouch(') < configure.index('logPlan('), 'the [touch] line follows what configureTouch was given'
assert 'ControlPresetLayout.keyboardID' in function(presets, 'static func layout(of p: ControlPreset, screen: ControlPresetScreen?)'), \
    'a keyboard built-in is laid out again for the screen it is loaded on'

# The WASD stick's centre glyph: fed by the action (the classic stick face that drew it is deleted; BCD's controls draw their own).
assert 'var stickGlyph: String?' in content, 'the action still names its stick glyph'
print('PASS: switches (opt-in built-in default and session slot), edit write-back, the session\'s layout and stick glyph wiring')
