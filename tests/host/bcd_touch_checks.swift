// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The behaviour half of tests/host/check-bcd-touch.py. That script compiles this file together
// with the stored layout types (ControlAction, TouchControl from ContentView.swift), the pure
// layout store (TouchControlPresets.swift), the classic control's own input functions (snap,
// stickKeys, applyStick and press, frozen in tests/fixtures/classic-touch-control.swift.txt from the
// classic touch control deleted in wave 3, wrapped in ClassicButton with stubs
// that record what they send), BCDTouchCore.swift and BCDTouchLive.swift's conversion, and
// runs it: BCD's controls must send exactly what the classic ones sent, be the same size, take
// touches in the same places, and convert layouts without changing a byte.

var failures = 0
func require(_ ok: @autoclosure () -> Bool, _ what: String) {
    if ok() { print("PASS: " + what) } else { print("FAIL: " + what); failures += 1 }
}

/// A fixed sequence, so every run checks the same cases.
struct Dice {
    var state: UInt64
    mutating func next() -> UInt64 { state = state &* 6364136223846793005 &+ 1442695040888963407; return state >> 33 }
    mutating func unit() -> Double { Double(next() % 1_000_001) / 1_000_000 }
    mutating func range(_ a: Double, _ b: Double) -> Double { a + (b - a) * unit() }
}

func record(_ e: BCDTouchEvent) -> String {
    switch e {
    case .key(let vk, let down): return "key \(vk) \(down ? 1 : 0)"
    case .mouse(let flags): return "mouse \(flags)"
    case .toggleKeyboard: return "keyboard"
    }
}

/// One step's events as a set of releases, then a set of presses, with the order inside each
/// set left out (the classic stick went through a Set, whose order changes from run to run).
func normalised(_ steps: [[String]]) -> [[String]] {
    steps.map { step in
        step.filter { $0.hasSuffix(" 0") && $0.hasPrefix("key") }.sorted()
            + step.filter { !($0.hasSuffix(" 0") && $0.hasPrefix("key")) }.sorted()
    }
}

/// Releases never come after a press within a step.
func releasesFirst(_ steps: [[String]]) -> Bool {
    steps.allSatisfy { step in
        let firstPress = step.firstIndex { $0.hasPrefix("key") && $0.hasSuffix(" 1") } ?? step.count
        return !step[firstPress...].contains { $0.hasPrefix("key") && $0.hasSuffix(" 0") }
    }
}

/// The classic control under one finger: SwiftUI's DragGesture(minimumDistance: 0) called
/// onChanged at touch-down (no travel) and at every move, onEnded at the lift.
func classicRun(_ action: ControlAction, diameter: CGFloat, path: [CGSize]) -> (steps: [[String]], taps: Int) {
    classicLog = []
    classicTaps = 0
    var c = TouchControl()
    c.action = action
    let button = ClassicButton(control: c, diameter: diameter)
    var isDown = false
    var steps: [[String]] = []
    for t in path {
        classicLog = []
        if let q = action.stickKeys {
            isDown = true
            button.applyStick(button.snap(t), q)
        } else if !isDown {
            isDown = true
            button.press(true)
        }
        steps.append(classicLog)
    }
    classicLog = []
    if let q = action.stickKeys {
        button.applyStick(-1, q)
        isDown = false
    } else if isDown {
        isDown = false
        button.press(false)
    }
    steps.append(classicLog)
    return (steps, classicTaps)
}

/// BCD's control under the same finger: BCDTouchKeyView begins at touch-down, moves with the
/// finger's travel and ends at the lift.
func bcdRun(_ action: BCDTouchAction, diameter: Double, path: [CGSize]) -> (steps: [[String]], taps: Int) {
    var log: [String] = []
    var taps = 0
    let press = BCDTouchPress(action: action, diameter: diameter, send: { log.append(record($0)) }, tap: { taps += 1 })
    var steps: [[String]] = []
    for (i, t) in path.enumerated() {
        log = []
        if i == 0 { press.begin() }
        press.move(dx: Double(t.width), dy: Double(t.height))
        steps.append(log)
    }
    log = []
    press.end()
    steps.append(log)
    return (steps, taps)
}

let actions: [ControlAction] = [.key(0x41), .key(0x20), .key(0x0D), .key(0x70), .mouseLeft, .mouseRight,
                                .keyboardToggle, .none, .joystickWASD, .joystickArrows, .pad("A"), .pad("LS")]
var dice = Dice(state: 0xBCD_7006)

// ---- every control sends what it always sent ----------------------------------------------
for action in actions {
    let mine = BCDTouchAction(stored: action)
    var same = true, ordered = true, tapsSame = true
    var detail = ""
    for scale in [0.5, 1.0, 1.45, 3.0] {
        for layout in [0.5, 1.0, 2.0] {
            let classicD = 64 * CGFloat(scale) * CGFloat(layout)
            let d = BCDTouchGeometry.diameter(scale: scale, layoutScale: layout)
            if Double(classicD) != d { same = false; detail = "diameter \(classicD) vs \(d)" }
            for _ in 0..<60 {
                // A finger: touch-down, then a walk that crosses the dead zone and the sectors' edges.
                var path = [CGSize.zero]
                var x = 0.0, y = 0.0
                for _ in 0..<Int(dice.next() % 24) {
                    if dice.next() % 5 == 0 {
                        // Straight onto a sector's edge or the dead zone's rim.
                        let angle = Double(dice.next() % 16) * .pi / 8
                        let r = d * (dice.next() % 2 == 0 ? 0.22 : dice.range(0.05, 1.5))
                        x = r * sin(angle); y = -r * cos(angle)
                    } else {
                        x += dice.range(-0.5, 0.5) * d; y += dice.range(-0.5, 0.5) * d
                    }
                    path.append(CGSize(width: x, height: y))
                }
                let a = classicRun(action, diameter: classicD, path: path)
                let b = bcdRun(mine, diameter: d, path: path)
                if normalised(a.steps) != normalised(b.steps) {
                    same = false
                    detail = "\(path): classic \(a.steps) BCD \(b.steps)"
                }
                if !releasesFirst(a.steps) || !releasesFirst(b.steps) { ordered = false }
                if a.taps != b.taps { tapsSame = false }
            }
        }
    }
    require(same, "\(action): BCD sends the same events, step by step, as the classic control \(same ? "" : detail)")
    require(ordered, "\(action): keys let go before new ones are pressed")
    require(tapsSame, "\(action): the same light ticks (down only; a stick when it leaves the centre)")
}
require(BCDTouchRules.press(.pad("A"), down: true).isEmpty && BCDTouchRules.press(.pad("LS"), down: false).isEmpty,
        "controller controls send nothing here: the touch gamepad's surface does")

// An interrupted touch (cancelled, the app inactive, the control gone) lets go of what it holds.
do {
    var log: [String] = []
    let press = BCDTouchPress(action: .keyStick(.wasd), diameter: 64, send: { log.append(record($0)) })
    press.begin(); press.move(dx: 40, dy: -40)
    press.end(); press.end()
    require(log == ["key 87 1", "key 68 1", "key 87 0", "key 68 0"] || log == ["key 87 1", "key 68 1", "key 68 0", "key 87 0"],
            "a held diagonal is let go once when the touch ends, however often it is ended \(log)")
    log = []
    let key = BCDTouchPress(action: .key(0x20), diameter: 64, send: { log.append(record($0)) })
    key.begin(); key.rebind(.key(0x41)); key.end()
    require(log == ["key 32 1", "key 32 0"], "a control rebound while held lets go of its old key first \(log)")
}

// ---- the same size and the same touch area -------------------------------------------------
let names = ["A", "B", "X", "Y", "D↑", "D↓", "D←", "D→", "LB", "RB", "LT", "RT", "LS", "RS", "L3", "R3",
             "Menu", "View", "Guide", "Unknown"]
let shapes: [ControlAction] = names.map { .pad($0) } + [.key(0x41), .mouseLeft, .mouseRight, .keyboardToggle, .none,
                                                         .joystickWASD, .joystickArrows]
var sizesSame = true, areasSame = true, stickSame = true
for action in shapes {
    let mine = BCDTouchAction(stored: action)
    if mine.isStick != (action.stickKeys != nil || action.isPadStick) { stickSame = false }
    for d in stride(from: 16.0, through: 400.0, by: 7.5) {
        let classic = action.controlSize(diameter: CGFloat(d))
        let bcd = BCDTouchGeometry.size(mine.shape, diameter: d)
        if classic != bcd { sizesSame = false }
        // Classic: the window took a touch within half a diameter of the centre; a controller
        // control's UIKit surface then took all of its frame, any other control its round face.
        for _ in 0..<40 {
            let p = CGPoint(x: dice.range(-1.2, 1.2) * d, y: dice.range(-1.2, 1.2) * d)
            let inWindow = hypot(Double(p.x), Double(p.y)) <= d / 2
            let inView = action.isPad ? (abs(p.x) <= classic.width / 2 && abs(p.y) <= classic.height / 2)
                                      : hypot(Double(p.x), Double(p.y)) <= d / 2
            if (inWindow && inView) != BCDTouchGeometry.takes(p, action: mine, diameter: d) { areasSame = false }
        }
    }
}
require(sizesSame, "every control is drawn the same size as before, at every diameter")
require(areasSame, "every control takes touches in the same area as before")
require(stickSame, "the same controls are sticks")
require(BCDTouchGeometry.addPosition(count: 4) == (0.56, 0.5) && BCDTouchGeometry.addPosition(count: 5) == (0.62, 0.56),
        "an added control goes where it went before")

// ---- layouts convert both ways without changing a byte ------------------------------------
let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
var controls: [TouchControl] = []
for action in shapes + [.key(0x7B), .key(0xDE), .key(0x6F), .key(0x5B), .key(0x08)] {
    for link in [nil, "RT", "LS", "D↑"] as [String?] {
        var c = TouchControl()
        c.nx = dice.range(0.03, 0.97); c.ny = dice.range(0.03, 0.97); c.scale = dice.range(0.5, 3)
        c.action = action
        c.padBinding = link
        controls.append(c)
    }
}
let before = try! encoder.encode(controls)
let back = controls.map(BCDTouchItem.init(stored:)).map(\.stored)
require(try! encoder.encode(back) == before && back == controls,
        "every control (all \(shapes.count + 5) actions, with and without a controller link) converts back unchanged")
let written = #"{"controls":[{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.25,"ny":0.75,"scale":1.45,"action":{"pad":{"_0":"LS"}}},{"id":"7A1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.5,"scale":1,"action":{"joystickWASD":{}},"padBinding":"LS"}]}"#
struct Stored: Codable, Equatable { var controls: [TouchControl] }
let old = try! JSONDecoder().decode(Stored.self, from: Data(written.utf8))
let again = Stored(controls: old.controls.map(BCDTouchItem.init(stored:)).map(\.stored))
require(try! encoder.encode(again) == (try! encoder.encode(old)), "a controls file the old editor wrote comes back byte for byte")

// ---- locking and layer order (upstream port 2026-10-25) -------------------------------------
// The stored control gains one optional key, written only when a control is locked: a layout with
// nothing locked is the file it always was (above), and a locked one survives the conversion.
var plain = TouchControl()
plain.nx = 0.3; plain.ny = 0.4; plain.action = .key(0x57)
let plainJSON = String(data: try! encoder.encode(plain), encoding: .utf8)!
require(!plainJSON.contains("locked"), "a control that is not locked is written without the key")
var pinned = plain
pinned.locked = true
require(String(data: try! encoder.encode(pinned), encoding: .utf8)!.contains("\"locked\":true"), "a locked control is written with it")
let pinnedItem = BCDTouchItem(stored: pinned)
require(pinnedItem.locked && !BCDTouchItem(stored: plain).locked, "the view value says whether the control is locked")
require(pinnedItem.stored == pinned && (try! JSONDecoder().decode(TouchControl.self, from: try! encoder.encode(pinned))) == pinned,
        "a locked control comes back unchanged, through the view value and through its own file")
var released = pinnedItem
released.locked = false
require(released.stored == plain && released.stored.locked == nil, "unlocking it writes no key again: the file is as if it was never locked")
let oldFile = #"{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.25,"ny":0.75,"scale":1,"action":{"mouseLeft":{}}}"#
let oldControl = try! JSONDecoder().decode(TouchControl.self, from: Data(oldFile.utf8))
require(oldControl.locked == nil && !BCDTouchItem(stored: oldControl).locked, "a control saved before locking existed is not locked")

let layers = (0..<4).map { i in BCDTouchItem(id: UUID(), x: 0.2 * Double(i + 1), y: 0.5, scale: 1, action: .mouseLeft) }
let ids = layers.map(\.id)
require(BCDTouchLayering.moved(layers, ids[1], toFront: true).map(\.id) == [ids[0], ids[2], ids[3], ids[1]], "to front: last in the list")
require(BCDTouchLayering.moved(layers, ids[2], toFront: false).map(\.id) == [ids[2], ids[0], ids[1], ids[3]], "to back: first in the list")
require(BCDTouchLayering.moved(layers, ids[3], toFront: true) == layers && BCDTouchLayering.moved(layers, ids[0], toFront: false) == layers,
        "already in front / at the back: the list is untouched")
require(BCDTouchLayering.moved(layers, UUID(), toFront: true) == layers, "a control that is not there changes nothing")
require(BCDTouchLayering.isFront(layers, ids[3]) && !BCDTouchLayering.isFront(layers, ids[0])
        && BCDTouchLayering.isBack(layers, ids[0]) && !BCDTouchLayering.isBack(layers, ids[3]) && !BCDTouchLayering.isFront([BCDTouchItem](), UUID()),
        "which control is in front and which at the back")
let reordered = BCDTouchLayering.moved(layers, ids[0], toFront: true)
require(Set(reordered.map(\.id)) == Set(ids) && reordered.count == 4 && reordered.first { $0.id == ids[0] } == layers[0],
        "reordering loses nothing and changes no control")
let storedReordered = BCDTouchLayering.moved(controls, controls[5].id, toFront: true)
require(storedReordered.count == controls.count && storedReordered.last == controls[5] && Set(storedReordered.map(\.id)) == Set(controls.map(\.id)),
        "the stored list reorders the same way, every control as it was")

var store = ControlPresetStore()
var n = 0
_ = store.save(name: "Racing", controls: controls, newID: { n += 1; return "user-\(n)" })
_ = store.save(name: "Keys", controls: Array(controls.prefix(9)), newID: { n += 1; return "user-\(n)" })
let file = try! store.encoded()
var edited = try! ControlPresetStore.decoded(file)
for p in edited.user {
    _ = edited.overwrite(id: p.id, controls: p.controls.map(BCDTouchItem.init(stored:)).map(\.stored))
}
require(try! edited.encoded() == file, "bcd-control-presets.json written after a pass through BCD's values is the same file")
require(ControlPresetStore.builtIns.first.map { BCDTouchNames.layoutName(id: $0.id, stored: $0.name) } == "Full controller",
        "the built-in is shown as \"Full controller\" (its stored name and id stay)")

// ---- a look of the player's own: a shape and words (2026-11-03, Discord #ideas) -------------
// The stored control gains one optional key, `face`, written only when set; it is read leniently
// (a value of the wrong kind, or a shape a later build adds, never fails a layout); it travels in
// every format a control is kept in; a control without one is drawn, sized and touched exactly as
// before; one with a shape takes touches in that shape; words are as large as fit.

func json(_ c: TouchControl) -> String { String(data: try! encoder.encode(c), encoding: .utf8)! }

// Nothing changes without one: every control above (every action, with and without a link).
var lookless = true, touchedAsBefore = true
for c in controls {
    let item = BCDTouchItem(stored: c)
    if c.face != nil || json(c).contains("face") || c.bcdOwnShape != nil || item.customShape != nil || item.customText != nil
        || item.ownShape != nil || item.words != nil || item.shape != item.action.shape { lookless = false }
    for d in [20.0, 64.0, 190.0] {
        for _ in 0..<25 {
            let p = CGPoint(x: dice.range(-1.3, 1.3) * d, y: dice.range(-1.3, 1.3) * d)
            if BCDTouchGeometry.takes(p, action: item.action, own: item.ownShape, diameter: d) != BCDTouchGeometry.takes(p, action: item.action, diameter: d)
                || BCDTouchGeometry.windowTakes(p, own: item.ownShape, diameter: d) != (hypot(Double(p.x), Double(p.y)) <= d / 2) {
                touchedAsBefore = false
            }
        }
    }
}
require(lookless, "a control without a look of its own has none in BCD's values and is written without the key")
require(touchedAsBefore, "... and the window and its surface take touches where they always did")
require(Stored(controls: old.controls).controls.allSatisfy { $0.face == nil && BCDTouchItem(stored: $0).customShape == nil },
        "a controls file written before looks existed has none")

// The format, both ways: every shape with every kind of words, on every kind of control.
let lookShapes: [BCDTouchCustomShape?] = [nil, .circle, .square, .rectangle(aspect: 0.25), .rectangle(aspect: 0.5), .rectangle(aspect: 1),
                                          .rectangle(aspect: 2), .rectangle(aspect: 3.37), .rectangle(aspect: 4)]
let lookWords: [String?] = [nil, "INV", "QUICK SAVE", "🎒", "Ünïcödé", "👨‍👩‍👧 2"]
let lookActions: [ControlAction] = [.key(0x49), .mouseLeft, .mouseRight, .pad("A"), .pad("LB"), .pad("Menu"), .pad("L3"), .none, .keyboardToggle]
var looked: [TouchControl] = []
var lookItems: [BCDTouchItem] = []
for action in lookActions {
    for shape in lookShapes {
        for words in lookWords {
            let item = BCDTouchItem(id: UUID(), x: dice.range(0.03, 0.97), y: dice.range(0.03, 0.97), scale: dice.range(0.5, 3),
                                    action: BCDTouchAction(stored: action), padLink: action.isPad ? nil : "RT", customShape: shape, customText: words)
            lookItems.append(item)
            looked.append(item.stored)
        }
    }
}
let lookedFile = try! encoder.encode(looked)
let lookedBack = try! JSONDecoder().decode([TouchControl].self, from: lookedFile)
require(lookedBack == looked && (try! encoder.encode(lookedBack)) == lookedFile,
        "every look (\(looked.count) controls) comes back from its own file byte for byte")
require(lookedBack.map(BCDTouchItem.init(stored:)) == lookItems && (try! encoder.encode(lookedBack.map(BCDTouchItem.init(stored:)).map(\.stored))) == lookedFile,
        "... and through BCD's values: the same shape, aspect and words")
var inv = TouchControl()
inv.id = UUID(uuidString: "6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F")!
inv.nx = 0.5; inv.ny = 0.25; inv.action = .key(0x49)
inv.face = TouchControl.Face.from(shape: .rectangle(aspect: 2), text: "INV")
require(json(inv) == #"{"action":{"key":{"_0":73}},"face":{"aspect":2,"shape":"rectangle","text":"INV"},"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.25,"scale":1}"#,
        "the stored look: \(json(inv))")
inv.face = TouchControl.Face.from(shape: nil, text: " INV ")
let wordsOnly = json(inv)
inv.face = TouchControl.Face.from(shape: .square, text: nil)
let shapeOnly = json(inv)
require(wordsOnly.contains(#""face":{"text":"INV"}"#) && shapeOnly.contains(#""face":{"shape":"square"}"#)
        && TouchControl.Face.from(shape: nil, text: "  ") == nil && TouchControl.Face.from(shape: nil, text: nil) == nil,
        "only what is set is written; a look with neither a shape nor words is no key at all")
var layoutStore = ControlPresetStore()
var lookIDs = 0
_ = layoutStore.save(name: "Looks", controls: looked, newID: { lookIDs += 1; return "look-\(lookIDs)" })
let layoutsFile = try! layoutStore.encoded()
var layoutsBack = try! ControlPresetStore.decoded(layoutsFile)
for p in layoutsBack.user { _ = layoutsBack.overwrite(id: p.id, controls: p.controls.map(BCDTouchItem.init(stored:)).map(\.stored)) }
require(try! layoutsBack.encoded() == layoutsFile && layoutsBack.preset("look-1")?.controls == looked,
        "bcd-control-presets.json keeps every look, byte for byte after a pass through BCD's values")
let loadedLooks = ControlPresetStore.layout(of: layoutsBack.preset("look-1")!, screen: nil)
require(loadedLooks.map(\.face) == looked.map(\.face), "a layout put on screen keeps its controls' looks")

// Read leniently: a look this build cannot make sense of never fails a control, a controls file or a layouts file.
func control(face: String) -> TouchControl? {
    try? JSONDecoder().decode(TouchControl.self, from: Data((#"{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.5,"scale":1,"action":{"key":{"_0":73}},"face":"# + face + "}").utf8))
}
let odd = ["5", #""square""#, "[1,2]", "true", "{}", "null", #"{"shape":5,"aspect":"wide","text":7}"#, #"{"shape":{"a":1},"text":["x"]}"#]
require(odd.allSatisfy { control(face: $0).map { BCDTouchItem(stored: $0).customShape == nil && BCDTouchItem(stored: $0).customText == nil && $0.bcdOwnShape == nil } == true },
        "a look of the wrong kind is left out and the control loads: \(odd.filter { control(face: $0) == nil })")
let later = control(face: #"{"shape":"hexagon","aspect":3,"text":"HEX","glow":true,"more":{"x":[1,2]}}"#)
require(later?.face?.shape == "hexagon" && later.map { BCDTouchItem(stored: $0).customShape == nil && BCDTouchItem(stored: $0).shape == .round
                                                             && BCDTouchItem(stored: $0).customText == "HEX" && $0.bcdOwnShape == nil } == true,
        "a shape a later build adds draws the standard shape, keeps its name in the file, and its words still show")
require(control(face: #"{"shape":"rectangle","aspect":1e9}"#).map { BCDTouchItem(stored: $0).customShape } == .rectangle(aspect: 4)
        && control(face: #"{"shape":"rectangle","aspect":0.01}"#).map { BCDTouchItem(stored: $0).customShape } == .rectangle(aspect: 0.25)
        && control(face: #"{"shape":"rectangle","aspect":-3}"#).map { BCDTouchItem(stored: $0).customShape } == .rectangle(aspect: 2)
        && control(face: #"{"shape":"rectangle","aspect":"2"}"#).map { BCDTouchItem(stored: $0).customShape } == .rectangle(aspect: 2)
        && control(face: #"{"shape":"rectangle"}"#).map { BCDTouchItem(stored: $0).customShape } == .rectangle(aspect: 2)
        && control(face: #"{"shape":" SQUARE "}"#).map { BCDTouchItem(stored: $0).customShape } == .square,
        "a rectangle's aspect is kept within 1 : 4 and 4 : 1, an unreadable one is 2 : 1; a shape's name is read whatever its case")
require(control(face: #"{"text":"a\nb\tc   d‮e\u0000"}"#).map { BCDTouchItem(stored: $0).customText } == "a b c de"
        && control(face: #"{"text":"   "}"#).map { BCDTouchItem(stored: $0).customText == nil } == true
        && control(face: "{\"text\":\"" + String(repeating: "W", count: 500) + "\"}").map { $0.face?.text?.unicodeScalars.count == 64
                                                                                        && BCDTouchItem(stored: $0).customText?.count == 20 } == true,
        "words are one clean line, at most 20 characters on the control (64 kept in the file, as a shared file allows)")
let mixedFile = #"{"controls":[{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.25,"ny":0.75,"scale":1.45,"action":{"pad":{"_0":"LS"}},"face":"oops"},{"id":"7A1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.5,"scale":1,"action":{"key":{"_0":77}},"face":{"shape":"square","text":"MAP"}},{"id":"8A1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.7,"ny":0.5,"scale":1,"action":{"mouseLeft":{}},"face":{"shape":"star","aspect":null,"text":null}}]}"#
let mixed = try? JSONDecoder().decode(Stored.self, from: Data(mixedFile.utf8))
require(mixed?.controls.count == 3 && mixed.map { BCDTouchItem(stored: $0.controls[1]).customShape == .square && BCDTouchItem(stored: $0.controls[1]).customText == "MAP" } == true,
        "a controls file with looks of every kind loads every control")
let oddLayouts = #"{"version":1,"presets":[{"id":"p","name":"Odd","controls":[{"id":"6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F","nx":0.5,"ny":0.5,"scale":1,"action":{"pad":{"_0":"A"}},"face":{"shape":7,"aspect":"x","text":{"no":1}}}]}]}"#
require((try? ControlPresetStore.decoded(Data(oddLayouts.utf8)))?.preset("p")?.controls.count == 1,
        "a layouts file with an unreadable look still loads (it is not taken for an unreadable file and frozen)")

// A stick keeps its ring and its labels: the look stays stored, for when the control is a button again.
var lookStick = TouchControl()
lookStick.action = .joystickWASD
lookStick.face = TouchControl.Face(shape: "square", text: "MOVE")
var padStick = lookStick
padStick.action = .pad("RS")
require([lookStick, padStick].allSatisfy { c in
            let item = BCDTouchItem(stored: c)
            return item.shape == .round && item.ownShape == nil && item.words == nil && c.bcdOwnShape == nil
                && item.customShape == .square && item.customText == "MOVE" && item.stored == c
        }, "a stick is drawn and touched as always, whatever look it keeps")

// Sizes: a circle and a square are as wide as a round control; a rectangle has the square's area, stretched.
var lookSizes = true
for d in stride(from: 16.0, through: 400.0, by: 13.0) {
    if BCDTouchGeometry.size(BCDTouchCustomShape.circle.drawn, diameter: d) != CGSize(width: d, height: d)
        || BCDTouchGeometry.size(BCDTouchCustomShape.square.drawn, diameter: d) != CGSize(width: d, height: d) { lookSizes = false }
    for a in [0.25, 0.5, 1, 1.6, 2, 4] {
        let s = BCDTouchGeometry.size(BCDTouchCustomShape.rectangle(aspect: a).drawn, diameter: d)
        if abs(Double(s.width * s.height) - d * d) > 1e-6 * d * d || abs(Double(s.width / s.height) - a) > 1e-9 { lookSizes = false }
    }
}
require(lookSizes, "a circle and a square are a round control's size; a rectangle is the square's area at its aspect")
require(BCDTouchGeometry.size(.rectangle(aspect: 40), diameter: 64) == BCDTouchGeometry.size(.rectangle(aspect: 4), diameter: 64)
        && BCDTouchGeometry.size(.rectangle(aspect: 0.001), diameter: 64) == BCDTouchGeometry.size(.rectangle(aspect: 0.25), diameter: 64)
        && BCDTouchGeometry.size(.rectangle(aspect: .nan), diameter: 64) == BCDTouchGeometry.size(.rectangle(aspect: 2), diameter: 64),
        "a rectangle stretches at most four times either way; an unreadable aspect is 2 : 1")
let shoulder = BCDTouchItem(id: UUID(), x: 0.5, y: 0.5, scale: 1, action: .pad("LB"))
var roundShoulder = shoulder
roundShoulder.customShape = .circle
require(shoulder.shape == .wide && roundShoulder.shape == .round && roundShoulder.ownShape == .round,
        "a shoulder is wide as always, and round when the player makes it a circle")
require(BCDTouchGeometry.diameter(fitting: .rectangle(aspect: 4), in: CGSize(width: 44, height: 40), atMost: 34) == 22
        && BCDTouchGeometry.diameter(fitting: .round, in: CGSize(width: 44, height: 40), atMost: 34) == 34,
        "a picture of a control fits its room")
require(BCDTouchCustomShape.aspect(slider: 0) == 1 && BCDTouchCustomShape.aspect(slider: 1) == 2 && BCDTouchCustomShape.aspect(slider: -2) == 0.25
        && BCDTouchCustomShape.aspect(slider: 9) == 4 && BCDTouchCustomShape.aspect(slider: BCDTouchCustomShape.slider(aspect: 2.5)) == 2.5
        && BCDTouchCustomShape.proportion(2) == "2.0 : 1" && BCDTouchCustomShape.proportion(0.5) == "1 : 2.0",
        "the editor's slider runs from 1 : 4 through a square to 4 : 1 and says which")

// Touches: a control with a shape of its own takes them exactly in that shape, in the window and on its surface.
var lookAreas = true
for custom in [BCDTouchCustomShape.circle, .square, .rectangle(aspect: 0.25), .rectangle(aspect: 0.5), .rectangle(aspect: 2), .rectangle(aspect: 4)] {
    for action in [BCDTouchAction.key(0x41), .mouseLeft, .keyboard, .none, .pad("A"), .pad("LB"), .pad("Menu")] {
        let item = BCDTouchItem(id: UUID(), x: 0.5, y: 0.5, scale: 1, action: action, customShape: custom)
        guard let own = item.ownShape else { lookAreas = false; continue }
        for d in [20.0, 64.0, 190.0] {
            let s = BCDTouchGeometry.size(item.shape, diameter: d)
            for _ in 0..<150 {
                let p = CGPoint(x: dice.range(-1.6, 1.6) * d, y: dice.range(-1.6, 1.6) * d)
                let inFrame = abs(Double(p.x)) <= Double(s.width) / 2 && abs(Double(p.y)) <= Double(s.height) / 2
                let drawn = custom == .circle ? hypot(Double(p.x), Double(p.y)) <= d / 2 : inFrame
                // The window first; then a controller input's surface fills its frame, any other control's is its shape.
                let window = BCDTouchGeometry.windowTakes(p, own: own, diameter: d)
                let surface = action.isPad ? inFrame : BCDTouchGeometry.inside(p, own, diameter: d)
                if (window && surface) != drawn || BCDTouchGeometry.takes(p, action: action, own: own, diameter: d) != drawn { lookAreas = false }
            }
        }
    }
}
require(lookAreas, "a control with a shape of its own takes touches exactly where it is drawn")
let square64 = BCDTouchCustomShape.square.drawn, tall64 = BCDTouchCustomShape.rectangle(aspect: 0.5).drawn, wide64 = BCDTouchCustomShape.rectangle(aspect: 4).drawn
require(BCDTouchGeometry.windowTakes(CGPoint(x: 29, y: 29), own: square64, diameter: 64) && !BCDTouchGeometry.windowTakes(CGPoint(x: 29, y: 29), own: nil, diameter: 64),
        "a square's corners take touches, beyond the circle a round control has")
require(!BCDTouchGeometry.windowTakes(CGPoint(x: 29, y: 0), own: tall64, diameter: 64)
        && !BCDTouchGeometry.takes(CGPoint(x: 29, y: 0), action: .key(0x41), own: tall64, diameter: 64)
        && BCDTouchGeometry.takes(CGPoint(x: 0, y: 44), action: .key(0x41), own: tall64, diameter: 64),
        "beside a tall rectangle a touch is the game's (no dead zone), above its middle it is the control's")
require(BCDTouchGeometry.takes(CGPoint(x: 60, y: 0), action: .pad("A"), own: wide64, diameter: 64)
        && !BCDTouchGeometry.takes(CGPoint(x: 0, y: 20), action: .pad("A"), own: wide64, diameter: 64),
        "a wide rectangle takes touches out to its ends, not above and below it")

// Words: one clean line, cut to the limits.
require(BCDTouchText.clean("  INV  ") == "INV" && BCDTouchText.clean("a\nb\r\nc\td") == "a b c d" && BCDTouchText.clean("x\u{0}y\u{7}z") == "xyz"
        && BCDTouchText.clean("\u{202E}evil\u{202C}") == "evil" && BCDTouchText.clean("a\u{00A0}\u{3000} b") == "a b"
        && BCDTouchText.clean("   ") == nil && BCDTouchText.clean("") == nil && BCDTouchText.clean(nil) == nil && BCDTouchText.clean("\u{200D}\u{200D} ") == nil,
        "words are one line: control and direction characters out, every run of spaces one space; nothing left is no words")
require(BCDTouchText.clean("👨‍👩‍👧") == "👨‍👩‍👧" && BCDTouchText.clean(String(repeating: "x", count: 30)) == String(repeating: "x", count: 20)
        && BCDTouchText.clean(String(repeating: "👨‍👩‍👧", count: 12))?.count == 9,
        "an emoji stays whole; at most 20 characters and 48 Unicode scalars (a shared file allows 64)")
let cleanSamples = ["  QUICK   SAVE ", "a\u{202E}b\n", "👨‍👩‍👧 " + String(repeating: "z", count: 40), "\u{200D}x\u{200D}", "Ünïcödé\t!"]
require(cleanSamples.allSatisfy { BCDTouchText.clean(BCDTouchText.clean($0)) == BCDTouchText.clean($0) }, "cleaning clean words changes nothing")

// The type: as large as fits inside the outline, for each shape; two lines only when clearly larger; never below the minimum.
let mono: (String) -> CGSize = { CGSize(width: 0.6 * Double($0.count), height: 1.2) }
let fitShapes: [(String, BCDTouchShape)] = [("circle", .round), ("square", .square), ("2:1 rectangle", .rectangle(aspect: 2)),
                                            ("1:2 rectangle", .rectangle(aspect: 0.5)), ("4:1 rectangle", .rectangle(aspect: 4)),
                                            ("shoulder", .wide), ("pill", .pill), ("stick click", .small)]
var inside = true, largestFit = true, shorterLarger = true, growsWithControl = true, boxesRight = true, floorHeld = true
var fitTrouble = ""
for (name, shape) in fitShapes {
    var sizes: [String: [Double]] = [:]
    for d in [32.0, 64.0, 128.0] {
        let frame = BCDTouchGeometry.size(shape, diameter: d)
        let inset = BCDTouchTextFit.padding(frame)
        var lastSingle = Double.infinity
        for text in ["I", "INV", "MAP 2", "QUICK SAVE", "INVENTORY", "OPEN THE MAP", "ABCDEFGHIJKLMNOPQRST"] {
            let f = BCDTouchTextFit.fit(text, in: shape, frame: frame, measure: mono)
            sizes[text, default: []].append(f.size)
            if f.whole {
                if !BCDTouchTextFit.contains(f.block, in: shape, frame: frame, inset: inset) { inside = false; fitTrouble = "\(name) \(d) \(text)" }
                if BCDTouchTextFit.contains(CGSize(width: f.block.width * 1.02, height: f.block.height * 1.02), in: shape, frame: frame, inset: inset) {
                    largestFit = false; fitTrouble = "\(name) \(d) \(text) could be larger"
                }
            } else if f.size != BCDTouchTextFit.minimumSize {
                floorHeld = false
            }
            if f.size < BCDTouchTextFit.minimumSize { floorHeld = false }
            let box = BCDTouchTextFit.box(f, in: shape, frame: frame)
            if box.width <= 0 || box.width > frame.width + 1e-9 || box.height < f.block.height || (f.whole && box.width < f.block.width - 1e-9) {
                boxesRight = false; fitTrouble = "\(name) \(d) \(text) box \(box) block \(f.block)"
            }
            if !text.contains(" ") {
                if f.size > lastSingle + 1e-9 { shorterLarger = false }
                lastSingle = f.size
            }
        }
    }
    for (_, list) in sizes where !(list[0] <= list[1] + 1e-9 && list[1] <= list[2] + 1e-9) { growsWithControl = false; fitTrouble = "\(name) \(list)" }
}
require(inside, "the words lie inside every outline, clear of its rim \(fitTrouble)")
require(largestFit, "... at the largest type that does \(fitTrouble)")
require(shorterLarger, "shorter words are drawn larger than longer ones")
require(growsWithControl, "a larger control draws its words larger \(fitTrouble)")
require(floorHeld, "the type is never below \(BCDTouchTextFit.minimumSize) points: words too long for the control are cut short there")
require(boxesRight, "the words' frame is inside the control and holds them \(fitTrouble)")
let circle64 = BCDTouchGeometry.size(.round, diameter: 64), wide4 = BCDTouchGeometry.size(.rectangle(aspect: 4), diameter: 64)
require(BCDTouchTextFit.fit("INVENTORY", in: .rectangle(aspect: 4), frame: wide4, measure: mono).size
        > 1.8 * BCDTouchTextFit.fit("INVENTORY", in: .round, frame: circle64, measure: mono).size,
        "a long word is drawn far larger in a wide rectangle than in a circle of the same size")
require(BCDTouchTextFit.fit("QUICK SAVE", in: .round, frame: circle64, measure: mono).lines == ["QUICK", "SAVE"]
        && BCDTouchTextFit.fit("QUICK SAVE", in: .rectangle(aspect: 4), frame: wide4, measure: mono).lines == ["QUICK SAVE"]
        && BCDTouchTextFit.fit("OPEN THE MAP", in: .round, frame: circle64, measure: mono).lines == ["OPEN", "THE MAP"],
        "two words go on two lines where that is clearly larger (a circle), broken where the lines are most even; one line in a wide rectangle")
require(BCDTouchTextFit.fit("MAP 2", in: .round, frame: circle64, measure: mono).lines == ["MAP 2"]
        && BCDTouchTextFit.fit("INVENTORY", in: .round, frame: circle64, measure: mono).lines == ["INVENTORY"],
        "one line when two would be only a little larger, and always for a single word")
let tiny = BCDTouchGeometry.size(.rectangle(aspect: 4), diameter: 16)
let cut = BCDTouchTextFit.fit("ABCDEFGHIJKLMNOPQRST", in: .rectangle(aspect: 4), frame: tiny, measure: mono)
require(!cut.whole && cut.size == BCDTouchTextFit.minimumSize && BCDTouchTextFit.box(cut, in: .rectangle(aspect: 4), frame: tiny).width > 0,
        "words too long for a tiny control are drawn at the minimum and cut short")

// A shared layout file carries the looks: what the app exports, the file's own checks pass and the app's decoder reads back.
let sharing = Array(looked.prefix(BCDControlShare.maxControls))
let sharedFile = try! BCDControlShare.export(name: "Looks", game: nil, build: "630", controlsJSON: try! JSONEncoder().encode(sharing))
var sharedLooks = false
if case .success(let parsed) = BCDControlShare.parse(sharedFile), let back = try? JSONDecoder().decode([TouchControl].self, from: parsed.controlsJSON) {
    sharedLooks = back.count == sharing.count && zip(back, sharing).allSatisfy { $0.face == $1.face && $0.action == $1.action && $0.id != $1.id }
}
require(sharedLooks, "a shared layout file carries every control's look and reads back the same (with fresh ids)")
let oldShare = #"{"bcdControls":1,"name":"Old","app":"604","controls":[{"id":"x","nx":0.5,"ny":0.5,"scale":1,"action":{"mouseLeft":{}}}]}"#
let oddShare = #"{"bcdControls":1,"name":"Odd","controls":[{"id":"x","nx":0.5,"ny":0.5,"scale":1,"action":{"mouseLeft":{}},"face":{"shape":"hexagon","aspect":"x","text":5}}]}"#
var sharesRead = true
for file in [oldShare, oddShare] {
    guard case .success(let parsed) = BCDControlShare.parse(Data(file.utf8)),
          let back = try? JSONDecoder().decode([TouchControl].self, from: parsed.controlsJSON), back.count == 1,
          BCDTouchItem(stored: back[0]).customShape == nil, BCDTouchItem(stored: back[0]).customText == nil else { sharesRead = false; continue }
}
require(sharesRead, "a shared file from before looks, or with a look this build cannot read, is added as it always was")

// ---- the editor's panel never covers the selected control on common screens ---------------
var covered: [String] = []
for (w, h) in [(844.0, 390.0), (667.0, 375.0), (956.0, 440.0), (1180.0, 820.0), (390.0, 844.0), (820.0, 1180.0)] {
    for _ in 0..<300 {
        let d = 64 * dice.range(0.5, 1.8)
        let control = CGRect(x: dice.range(0.03, 0.97) * w - d / 2, y: dice.range(0.03, 0.97) * h - d / 2, width: d, height: d)
        let panel = BCDTouchPanelPlacement.dock(screen: CGSize(width: w, height: h), left: w > h ? 47 : 0, right: w > h ? 47 : 0,
                                                top: w > h ? 0 : 47, bottom: w > h ? 21 : 34, bar: 60, avoid: control)
        if panel.intersects(control) { covered.append("\(Int(w))x\(Int(h)) \(control)") }
        if panel.minX < 0 || panel.maxX > w || panel.minY < 0 || panel.maxY > h { covered.append("off screen \(panel)") }
    }
}
require(covered.isEmpty, "the selected control's panel stays on screen and off the control \(covered.prefix(3))")

if failures > 0 { print("\(failures) FAILED"); exit(1) }
print("PASS: BCD's touch controls send the same events, take the same touches and keep layouts byte for byte")
