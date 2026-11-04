// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import CoreGraphics

// BCD's on-screen touch controls: the rules, without views (docs/BCD-UI-ARCHITECTURE.md).
// Foundation and CoreGraphics only: tests/host/check-bcd-touch.py compiles this file on the
// host, next to the stored layout types, to prove that every control sends the game the same
// events and takes touches in the same places as before, and that layouts convert both ways
// without changing a byte. A control the player gave a shape or words of its own (BCD's editor,
// Look: 2026-11-03) is drawn, and takes touches, in that shape, with the words as large as fit;
// every other control is drawn and touched exactly as before.
//
// The views (BCDTouchControls.swift, BCDTouchEditor.swift) work on these values. BCDTouchLive
// converts them to and from the stored controls and carries the events to the game.

// MARK: - A control

/// What a control does.
enum BCDTouchAction: Hashable {
    /// Nothing (a placeholder the player has not bound yet).
    case none
    /// A key, as its Windows virtual-key code.
    case key(Int32)
    case mouseLeft
    case mouseRight
    /// A stick that holds keys: W A S D, or the arrow keys.
    case keyStick(BCDKeyStick)
    /// Shows or hides the iOS keyboard.
    case keyboard
    /// An input of player 1's controller: "A", "B", "X", "Y", "D↑", "D↓", "D←", "D→", "LB", "RB",
    /// "LT", "RT", "LS", "RS", "L3", "R3", "Menu", "View", "Guide" (kept as stored).
    case pad(String)

    var padName: String? { if case .pad(let name) = self { return name }; return nil }
    var isPad: Bool { padName != nil }
    var keyStick: BCDKeyStick? { if case .keyStick(let s) = self { return s }; return nil }
    var isControllerStick: Bool { padName == "LS" || padName == "RS" }
    /// Drawn as a stick: a ring with a knob that follows the thumb.
    var isStick: Bool { keyStick != nil || isControllerStick }

    /// The control's outline. A shoulder is wide, Menu and View are small pills, a stick click
    /// is a small circle; everything else is round.
    var shape: BCDTouchShape {
        switch padName {
        case "LB", "RB", "LT", "RT": return .wide
        case "Menu", "View": return .pill
        case "L3", "R3": return .small
        default: return .round
        }
    }

    /// The outline of a control with this action and the player's own shape (BCD's editor, Look): the
    /// player's, except on a stick, which stays the ring its thumb moves in.
    func shape(custom: BCDTouchCustomShape?) -> BCDTouchShape {
        guard !isStick, let custom else { return shape }
        return custom.drawn
    }
}

enum BCDKeyStick: Hashable {
    case wasd, arrows
    /// The keys for up, right, down and left.
    var keys: [Int32] {
        switch self {
        case .wasd: return [0x57, 0x44, 0x53, 0x41]
        case .arrows: return [0x26, 0x27, 0x28, 0x25]
        }
    }
}

/// A control's outline: the four its action draws (`BCDTouchAction.shape`), and the two the player can
/// choose besides a circle (`BCDTouchCustomShape`).
enum BCDTouchShape: Hashable {
    case round, wide, pill, small
    /// As wide and as tall as a round control.
    case square
    /// `aspect` times as wide as it is tall, with a square's area.
    case rectangle(aspect: Double)
}

/// A shape the player gives a control in BCD's editor (Look), in place of the one its action draws. The
/// stored control keeps it as words (TouchControl.Face: "circle", "square" or "rectangle" and a
/// rectangle's aspect), written only when set, so a layout without one is the file it always was; a
/// name this build does not know draws the control's standard shape.
enum BCDTouchCustomShape: Hashable {
    case circle
    case square
    /// `aspect` times as wide as it is tall (below 1, taller than wide), within `aspects`.
    case rectangle(aspect: Double)

    /// How far a rectangle stretches either way: four times as wide as it is tall, or as tall as wide.
    static let aspects = 0.25...4.0
    /// A new rectangle is twice as wide as it is tall.
    static let defaultAspect = 2.0

    /// A rectangle's aspect, inside `aspects`; one that cannot be read is the default.
    static func clampAspect(_ value: Double?) -> Double {
        guard let value, value.isFinite, value > 0 else { return defaultAspect }
        return min(max(value, aspects.lowerBound), aspects.upperBound)
    }

    /// From a stored control: nil for no shape of its own, or for one this build does not know.
    init?(name: String?, aspect: Double?) {
        switch name?.trimmingCharacters(in: .whitespaces).lowercased() {
        case "circle": self = .circle
        case "square": self = .square
        case "rectangle": self = .rectangle(aspect: Self.clampAspect(aspect))
        default: return nil
        }
    }

    /// Its stored name.
    var name: String {
        switch self {
        case .circle: return "circle"
        case .square: return "square"
        case .rectangle: return "rectangle"
        }
    }

    /// A rectangle's width over its height, as stored; nil for the others.
    var aspect: Double? {
        if case .rectangle(let value) = self { return Self.clampAspect(value) }
        return nil
    }

    /// How it is drawn.
    var drawn: BCDTouchShape {
        switch self {
        case .circle: return .round
        case .square: return .square
        case .rectangle(let value): return .rectangle(aspect: Self.clampAspect(value))
        }
    }

    /// The editor's slider for a rectangle runs from four times as tall (-2) through a square (0) to four
    /// times as wide (2); the aspect it sets is kept to two decimals.
    static func aspect(slider value: Double) -> Double {
        (pow(2, min(max(value.isFinite ? value : 0, -2), 2)) * 100).rounded() / 100
    }

    /// Where the slider stands for an aspect.
    static func slider(aspect: Double) -> Double { log2(clampAspect(aspect)) }

    /// "2.0 : 1" for a wide rectangle, "1 : 2.0" for a tall one.
    static func proportion(_ aspect: Double) -> String {
        let a = clampAspect(aspect)
        return a >= 1 ? String(format: "%.1f : 1", a) : String(format: "1 : %.1f", 1 / a)
    }
}

/// One control on screen, as the views see it.
struct BCDTouchItem: Identifiable, Hashable {
    var id: UUID
    /// Centre, as a fraction of the screen's width and height (a layout survives rotation and
    /// other screen sizes).
    var x: Double
    var y: Double
    /// The control's own size: 1 is a 64-point button.
    var scale: Double
    var action: BCDTouchAction
    /// In keyboard-and-mouse mode, the controller input that presses this control too.
    var padLink: String?
    /// The editor cannot move, resize or remove it.
    var locked = false
    /// The player's own shape for it (BCD's editor, Look); nil: the one its action draws.
    var customShape: BCDTouchCustomShape? = nil
    /// The player's own words on it in place of its label (BCDTouchText.clean); nil: its label.
    var customText: String? = nil
}

extension BCDTouchItem {
    /// The outline it is drawn in.
    var shape: BCDTouchShape { action.shape(custom: customShape) }
    /// The player's shape as drawn, where it counts (never on a stick); nil: the action's own, drawn
    /// and touched as always.
    var ownShape: BCDTouchShape? { action.isStick ? nil : customShape?.drawn }
    /// The words drawn in place of its label; nil: its label (a stick keeps its own).
    var words: String? { action.isStick ? nil : customText }
}

// MARK: - Size and where touches count

enum BCDTouchGeometry {
    /// A size-1 button's diameter, in points.
    static let base = 64.0
    /// The editor's limits for a control's own size (the pinch and the slider).
    static let scaleRange = 0.5...3.0
    /// Where a control's centre may be dragged to, as a fraction of the screen.
    static let placeRange = 0.03...0.97

    /// A control's diameter: its own size times the whole layout's (the game's Control size).
    static func diameter(scale: Double, layoutScale: Double) -> Double { base * scale * layoutScale }

    /// The diameter at which a control of `shape` fits in `room`, at most `largest`: a picture of it in a panel.
    static func diameter(fitting shape: BCDTouchShape, in room: CGSize, atMost largest: Double) -> Double {
        let unit = size(shape, diameter: 1)
        return min(largest, Double(room.width) / Double(unit.width), Double(room.height) / Double(unit.height))
    }

    /// The drawn frame. A round control is `diameter` across.
    static func size(_ shape: BCDTouchShape, diameter d: Double) -> CGSize {
        switch shape {
        case .wide: return CGSize(width: d * 1.6, height: d * 0.74)
        case .pill: return CGSize(width: d * 1.2, height: d * 0.5)
        case .small: return CGSize(width: d * 0.8, height: d * 0.8)
        case .round: return CGSize(width: d, height: d)
        case .square: return CGSize(width: d, height: d)
        case .rectangle(let aspect):
            // The square's area, stretched: √aspect wider and as much lower.
            let k = BCDTouchCustomShape.clampAspect(aspect).squareRoot()
            return CGSize(width: d * k, height: d / k)
        }
    }

    /// How round an outline's corners are, for a control drawn `size`: every outline is a rounded
    /// rectangle (a circle and a pill round all the way; a square and a rectangle by a fifth of their
    /// shorter side, drawn continuous as the shoulders are).
    static func cornerRadius(_ shape: BCDTouchShape, size: CGSize) -> Double {
        let w = Double(size.width), h = Double(size.height)
        switch shape {
        case .round, .small, .pill: return min(w, h) / 2
        case .wide: return h * 0.32
        case .square, .rectangle: return min(w, h) * 0.2
        }
    }

    /// The overlay's window takes a touch only within half a diameter of a control's centre;
    /// there a controller control takes it anywhere in its frame, any other control in its
    /// circle. `p` is measured from the control's centre.
    static func takes(_ p: CGPoint, action: BCDTouchAction, diameter d: Double) -> Bool {
        let r = (Double(p.x) * Double(p.x) + Double(p.y) * Double(p.y)).squareRoot()
        guard r <= d / 2 else { return false }
        guard action.isPad else { return true }
        let s = size(action.shape, diameter: d)
        return abs(Double(p.x)) <= Double(s.width) / 2 && abs(Double(p.y)) <= Double(s.height) / 2
    }

    /// Whether `p`, from the centre, is inside an outline of `diameter`: a circle within its radius,
    /// any other outline within its frame (its rounded corners count: a few points of slop cost nothing).
    static func inside(_ p: CGPoint, _ shape: BCDTouchShape, diameter d: Double) -> Bool {
        let s = size(shape, diameter: d)
        switch shape {
        case .round, .small:
            return (Double(p.x) * Double(p.x) + Double(p.y) * Double(p.y)).squareRoot() <= Double(s.width) / 2
        case .wide, .pill, .square, .rectangle:
            return abs(Double(p.x)) <= Double(s.width) / 2 && abs(Double(p.y)) <= Double(s.height) / 2
        }
    }

    /// Where a control takes a touch, the overlay's window and the control's own surface together:
    /// with a shape of the player's own (`own`, BCDTouchItem.ownShape), anywhere in that shape; without
    /// one, as it always did (`takes(_:action:diameter:)`).
    static func takes(_ p: CGPoint, action: BCDTouchAction, own: BCDTouchShape?, diameter d: Double) -> Bool {
        guard let own else { return takes(p, action: action, diameter: d) }
        return inside(p, own, diameter: d)
    }

    /// The overlay window's part of it (TouchControlsModel.hitsInteractive): within half a diameter of
    /// the centre, as always, or the player's own shape.
    static func windowTakes(_ p: CGPoint, own: BCDTouchShape?, diameter d: Double) -> Bool {
        guard let own else { return (Double(p.x) * Double(p.x) + Double(p.y) * Double(p.y)).squareRoot() <= d / 2 }
        return inside(p, own, diameter: d)
    }

    /// Where an added control goes: a little apart from the last one, so repeated adds do not
    /// stack out of sight.
    static func addPosition(count: Int) -> (x: Double, y: Double) {
        (0.5 + Double(count % 3) * 0.06, 0.5 + Double(count % 2) * 0.06)
    }

    static func clampPlace(_ v: Double) -> Double { min(max(v, placeRange.lowerBound), placeRange.upperBound) }
    static func clampScale(_ v: Double) -> Double { min(max(v, scaleRange.lowerBound), scaleRange.upperBound) }
}

// MARK: - Which control is in front

/// The controls are drawn, and take touches where they overlap, in list order: the last one is in
/// front. Bringing one forward or back only reorders the list the layout already stores.
enum BCDTouchLayering {
    /// `list` with the element `id` moved to the front (last) or to the back (first); the same list
    /// when it is not there or already there.
    static func moved<T: Identifiable>(_ list: [T], _ id: T.ID, toFront: Bool) -> [T] {
        guard let i = list.firstIndex(where: { $0.id == id }) else { return list }
        if toFront ? i == list.count - 1 : i == 0 { return list }
        var out = list
        let item = out.remove(at: i)
        if toFront { out.append(item) } else { out.insert(item, at: 0) }
        return out
    }

    static func isFront<T: Identifiable>(_ list: [T], _ id: T.ID) -> Bool { list.last?.id == id }
    static func isBack<T: Identifiable>(_ list: [T], _ id: T.ID) -> Bool { list.first?.id == id }
}

// MARK: - What reaches the game

/// One input event for the game.
enum BCDTouchEvent: Hashable {
    /// A key down or up (winios_post_key).
    case key(Int32, down: Bool)
    /// A mouse button at the pointer, as SendInput's flags (winios_pointer at 0, 0).
    case mouse(UInt32)
    /// The iOS keyboard shown or hidden.
    case toggleKeyboard

    static let leftDown: UInt32 = 0x0002, leftUp: UInt32 = 0x0004
    static let rightDown: UInt32 = 0x0008, rightUp: UInt32 = 0x0010
}

enum BCDTouchRules {
    /// What pressing (`down`) or lifting a button-like control sends. Sticks and controller
    /// inputs send nothing here: a key stick follows the thumb (`stickChange`), and controller
    /// inputs go through the touch gamepad.
    static func press(_ action: BCDTouchAction, down: Bool) -> [BCDTouchEvent] {
        switch action {
        case .key(let vk): return [.key(vk, down: down)]
        case .mouseLeft: return [.mouse(down ? BCDTouchEvent.leftDown : BCDTouchEvent.leftUp)]
        case .mouseRight: return [.mouse(down ? BCDTouchEvent.rightDown : BCDTouchEvent.rightUp)]
        case .keyboard: return down ? [.toggleKeyboard] : []
        case .none, .keyStick, .pad: return []
        }
    }

    /// A key stick's direction: -1 inside the dead zone (22 % of the control's diameter), else
    /// 0 for up and on clockwise in eighths. `dx`, `dy`: the thumb's travel in points, y down.
    static func direction(dx: Double, dy: Double, diameter: Double) -> Int {
        if (dx * dx + dy * dy).squareRoot() < diameter * 0.22 { return -1 }
        var angle = atan2(dx, -dy) * 180 / .pi
        if angle < 0 { angle += 360 }
        return Int((angle + 22.5) / 45.0) % 8
    }

    /// The keys a key stick holds in a direction (diagonals hold two).
    static func held(_ direction: Int, _ keys: [Int32]) -> [Int32] {
        guard keys.count == 4 else { return [] }
        let up = keys[0], right = keys[1], down = keys[2], left = keys[3]
        switch direction {
        case 0: return [up]
        case 1: return [up, right]
        case 2: return [right]
        case 3: return [down, right]
        case 4: return [down]
        case 5: return [down, left]
        case 6: return [left]
        case 7: return [up, left]
        default: return []
        }
    }

    /// Moving a key stick from one direction to another: only what changed is sent, the keys
    /// let go first, so a held direction never stutters while the thumb wanders inside it.
    static func stickChange(from old: Int, to new: Int, keys: [Int32]) -> [BCDTouchEvent] {
        guard old != new else { return [] }
        let before = held(old, keys), after = held(new, keys)
        return before.filter { !after.contains($0) }.map { .key($0, down: false) }
            + after.filter { !before.contains($0) }.map { .key($0, down: true) }
    }
}

/// One finger on one key, mouse, keyboard or key-stick control, from touch-down to lift: what
/// it sends. The UIKit surface (BCDTouchKeySurface) feeds it; a lift, a cancelled touch, the
/// app leaving the foreground and the control leaving the screen all end it, so nothing stays
/// held.
final class BCDTouchPress {
    private(set) var action: BCDTouchAction
    var diameter: Double
    private(set) var down = false
    private(set) var direction = -1
    private let send: (BCDTouchEvent) -> Void
    private let tap: () -> Void
    /// The drawn state changed: pressed, and a key stick's direction.
    var changed: ((Bool, Int) -> Void)?

    init(action: BCDTouchAction, diameter: Double, send: @escaping (BCDTouchEvent) -> Void, tap: @escaping () -> Void = {}) {
        self.action = action
        self.diameter = diameter
        self.send = send
        self.tap = tap
    }

    func begin() {
        guard !down else { return }
        down = true
        if action.keyStick == nil {
            tap()   // a light tick on the way down only: a held key does not buzz
            BCDTouchRules.press(action, down: true).forEach(send)
        }
        changed?(down, direction)
    }

    func move(dx: Double, dy: Double) {
        guard down, let stick = action.keyStick else { return }
        let next = BCDTouchRules.direction(dx: dx, dy: dy, diameter: diameter)
        guard next != direction else { return }
        BCDTouchRules.stickChange(from: direction, to: next, keys: stick.keys).forEach(send)
        if direction == -1 && next != -1 { tap() }
        direction = next
        changed?(down, direction)
    }

    func end() {
        guard down else { return }
        if let stick = action.keyStick {
            BCDTouchRules.stickChange(from: direction, to: -1, keys: stick.keys).forEach(send)
            direction = -1
        } else {
            BCDTouchRules.press(action, down: false).forEach(send)
        }
        down = false
        changed?(down, direction)
    }

    /// A different action ends the old one's press first.
    func rebind(_ action: BCDTouchAction) {
        guard action != self.action else { return }
        end()
        self.action = action
    }
}

// MARK: - Names

/// What the controls and the editor call things, in BCD's words.
enum BCDTouchNames {
    /// The label drawn on a key.
    static func keyLabel(_ vk: Int32) -> String {
        switch vk {
        case 0x08: return "⌫"
        case 0x09: return "Tab"
        case 0x0D: return "Enter"
        case 0x10: return "Shift"
        case 0x11: return "Ctrl"
        case 0x12: return "Alt"
        case 0x13: return "Pause"
        case 0x14: return "Caps"
        case 0x1B: return "Esc"
        case 0x20: return "Space"
        case 0x21: return "PgUp"
        case 0x22: return "PgDn"
        case 0x23: return "End"
        case 0x24: return "Home"
        case 0x25: return "←"
        case 0x26: return "↑"
        case 0x27: return "→"
        case 0x28: return "↓"
        case 0x2C: return "PrtSc"
        case 0x2D: return "Ins"
        case 0x2E: return "Del"
        case 0x5B, 0x5C: return "Win"
        case 0x6A: return "Num*"
        case 0x6B: return "Num+"
        case 0x6D: return "Num−"
        case 0x6E: return "Num."
        case 0x6F: return "Num/"
        case 0x90: return "NumLk"
        case 0x91: return "ScrLk"
        case 0xA0, 0xA1: return "Shift"
        case 0xA2, 0xA3: return "Ctrl"
        case 0xA4, 0xA5: return "Alt"
        case 0xBA: return ";"
        case 0xBB: return "="
        case 0xBC: return ","
        case 0xBD: return "-"
        case 0xBE: return "."
        case 0xBF: return "/"
        case 0xC0: return "`"
        case 0xDB: return "["
        case 0xDC: return "\\"
        case 0xDD: return "]"
        case 0xDE: return "'"
        default:
            if (0x30...0x39).contains(vk) || (0x41...0x5A).contains(vk), let u = UnicodeScalar(UInt32(vk)) {
                return String(Character(u))
            }
            if (0x60...0x69).contains(vk) { return "Num\(vk - 0x60)" }
            if (0x70...0x87).contains(vk) { return "F\(vk - 0x6F)" }
            return String(format: "0x%02X", vk)
        }
    }

    /// A key's full name, for the editor.
    static func keyName(_ vk: Int32) -> String {
        switch vk {
        case 0x08: return "Backspace"
        case 0x0D: return "Enter"
        case 0x14: return "Caps Lock"
        case 0x1B: return "Escape"
        case 0x20: return "Space bar"
        case 0x21: return "Page Up"
        case 0x22: return "Page Down"
        case 0x25: return "Left arrow"
        case 0x26: return "Up arrow"
        case 0x27: return "Right arrow"
        case 0x28: return "Down arrow"
        case 0x2C: return "Print Screen"
        case 0x2D: return "Insert"
        case 0x2E: return "Delete"
        case 0x5B, 0x5C: return "Windows key"
        default:
            if (0x60...0x69).contains(vk) { return "Numpad \(vk - 0x60)" }
            if (0x6A...0x6F).contains(vk) { return "Numpad " + String(keyLabel(vk).dropFirst(3)) }
            let label = keyLabel(vk)
            return label.count == 1 ? "Key " + label : label
        }
    }

    /// A controller input's short name, as drawn on its control and its chip.
    static func padLabel(_ name: String) -> String {
        switch name {
        case "D↑": return "↑"
        case "D↓": return "↓"
        case "D←": return "←"
        case "D→": return "→"
        case "LS": return "L"
        case "RS": return "R"
        default: return name
        }
    }

    /// A controller input's full name.
    static func padName(_ name: String) -> String {
        switch name {
        case "A", "B", "X", "Y": return "Button " + name
        case "D↑": return "D-pad up"
        case "D↓": return "D-pad down"
        case "D←": return "D-pad left"
        case "D→": return "D-pad right"
        case "LB": return "Left bumper"
        case "RB": return "Right bumper"
        case "LT": return "Left trigger"
        case "RT": return "Right trigger"
        case "LS": return "Left stick"
        case "RS": return "Right stick"
        case "L3": return "Left stick click"
        case "R3": return "Right stick click"
        case "Menu": return "Menu (Start)"
        case "View": return "View (Back)"
        case "Guide": return "Guide"
        default: return name
        }
    }

    /// What a control does, for the editor's title.
    static func title(_ action: BCDTouchAction) -> String {
        switch action {
        case .none: return "Nothing yet"
        case .key(let vk): return keyName(vk)
        case .mouseLeft: return "Left click"
        case .mouseRight: return "Right click"
        case .keyStick(.wasd): return "WASD stick"
        case .keyStick(.arrows): return "Arrow-key stick"
        case .keyboard: return "Show keyboard"
        case .pad(let name): return "Controller · " + padName(name)
        }
    }

    /// The built-in layouts' names, in BCD's words (the stored names stay as they are).
    static func layoutName(id: String, stored: String) -> String {
        id == "builtin.xbox" ? "Full controller" : stored
    }
}

// MARK: - The player's words on a control

/// Words the player puts on a control in place of its label (BCD's editor, Look): "INV" on an
/// inventory key.
enum BCDTouchText {
    /// The most characters kept, and Unicode scalars (an emoji can take several; a shared layout file
    /// allows 64 to a text: BCDControlShare.maxStringLength).
    static let maxCharacters = 20
    static let maxScalars = 48

    /// One line of words: control and format characters out (but the joiners emoji and some scripts
    /// are written with), line breaks and tabs a space, every run of spaces one space, trimmed, cut to
    /// the limits; nil when nothing is left to show.
    static func clean(_ text: String?) -> String? {
        guard let text else { return nil }
        var scalars = String.UnicodeScalarView()
        for s in text.unicodeScalars {
            if s == "\u{200C}" || s == "\u{200D}" {
                scalars.append(s)
            } else if s.properties.isWhitespace {
                scalars.append(" ")
            } else if s.properties.generalCategory != .control && s.properties.generalCategory != .format {
                scalars.append(s)
            }
        }
        let words = String(scalars).split(separator: " ", omittingEmptySubsequences: true).joined(separator: " ")
        var out = "", characters = 0, count = 0
        for c in words {
            let n = c.unicodeScalars.count
            guard characters < maxCharacters, count + n <= maxScalars else { break }
            out.append(c)
            characters += 1
            count += n
        }
        out = out.trimmingCharacters(in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: "\u{200C}\u{200D}")))
        return out.isEmpty ? nil : out
    }
}

/// How large the player's words are drawn: the largest type at which they fit inside the control's
/// outline, `padding` in from its rim, on one line; on two (broken at a space) only when that makes
/// the type clearly larger; never below `minimumSize`, where words too long for the control are cut
/// short. The face measures its own type (BCDTouchTextMeasure) and hands the measure in, so the
/// rules are checked on the host with a measure of their own.
enum BCDTouchTextFit {
    /// The smallest type the words are drawn in, in points.
    static let minimumSize = 8.0
    /// Two lines only when they make the type at least this much larger than one line does.
    static let twoLineGain = 1.15
    /// Continuous corners reach a little further in than a circle's arc of the same radius.
    static let continuousCorner = 1.15

    struct Fit: Equatable {
        /// The words as drawn: one line, or two.
        var lines: [String]
        /// The type size, in points.
        var size: Double
        /// All of the words show at `size`; false: even at `minimumSize` they are too long for the
        /// control and are cut short.
        var whole: Bool
        /// The lines' width and height at `size`.
        var block: CGSize
    }

    /// The room the words keep from the rim, for a control drawn `frame`.
    static func padding(_ frame: CGSize) -> Double { max(2, 0.08 * Double(min(frame.width, frame.height))) }

    /// Half the width inside an outline drawn `frame`, `inset` in from its rim, at `y` above or below
    /// its middle; nil beyond its top or bottom (BCDTouchGeometry.cornerRadius: every outline is a
    /// rounded rectangle).
    static func halfWidth(_ shape: BCDTouchShape, frame: CGSize, inset: Double, at y: Double) -> Double? {
        let w = Double(frame.width) - 2 * inset, h = Double(frame.height) - 2 * inset
        guard w > 0, h > 0, abs(y) <= h / 2 else { return nil }
        var r = BCDTouchGeometry.cornerRadius(shape, size: frame)
        switch shape {
        case .wide, .square, .rectangle: r *= continuousCorner
        case .round, .small, .pill: break
        }
        r = min(max(r - inset, 0), min(w, h) / 2)
        let over = abs(y) - (h / 2 - r)
        guard over > 0 else { return w / 2 }
        return w / 2 - r + max(r * r - over * over, 0).squareRoot()
    }

    /// Whether a block, centred on the control, lies inside its outline, `inset` in from the rim.
    static func contains(_ block: CGSize, in shape: BCDTouchShape, frame: CGSize, inset: Double) -> Bool {
        guard let half = halfWidth(shape, frame: frame, inset: inset, at: Double(block.height) / 2) else { return false }
        return Double(block.width) / 2 <= half
    }

    /// The largest type size at which lines measuring `unit` at 1 point fit inside the outline.
    static func largest(_ unit: CGSize, in shape: BCDTouchShape, frame: CGSize) -> Double {
        let inset = padding(frame)
        let a = Double(unit.width), b = Double(unit.height)
        let w = Double(frame.width) - 2 * inset, h = Double(frame.height) - 2 * inset
        guard a > 0, b > 0, w > 0, h > 0, a.isFinite, b.isFinite else { return 0 }
        func fits(_ s: Double) -> Bool { contains(CGSize(width: a * s, height: b * s), in: shape, frame: frame, inset: inset) }
        var hi = min(w / a, h / b)
        if fits(hi) { return hi }
        var lo = 0.0
        for _ in 0..<48 {
            let mid = (lo + hi) / 2
            if fits(mid) { lo = mid } else { hi = mid }
        }
        return lo
    }

    /// The words' lines and type for a control drawn `frame` in `shape`. `measure` gives a line's width
    /// and height at a type size of 1 point. `text` is one clean line (BCDTouchText.clean).
    static func fit(_ text: String, in shape: BCDTouchShape, frame: CGSize, measure: (String) -> CGSize) -> Fit {
        let one = measure(text)
        var best = (lines: [text], unit: one, size: largest(one, in: shape, frame: frame))
        let words = text.split(separator: " ")
        if words.count > 1 {
            // Two lines, broken at the space that leaves them most even.
            var even: (lines: [String], unit: CGSize)?
            for i in 1..<words.count {
                let top = words[..<i].joined(separator: " "), bottom = words[i...].joined(separator: " ")
                let a = measure(top), b = measure(bottom)
                let unit = CGSize(width: max(a.width, b.width), height: a.height + b.height)
                if let known = even, known.unit.width <= unit.width { continue }
                even = ([top, bottom], unit)
            }
            if let even {
                let size = largest(even.unit, in: shape, frame: frame)
                if size >= best.size * twoLineGain { best = (even.lines, even.unit, size) }
            }
        }
        let size = max(best.size, minimumSize)
        return Fit(lines: best.lines, size: size, whole: best.size >= minimumSize,
                   block: CGSize(width: Double(best.unit.width) * size, height: Double(best.unit.height) * size))
    }

    /// The frame the face lays the words out in: their block's height and a little more (so rounding
    /// never clips a line), as wide as the outline is where the block's top and bottom edges are. Words
    /// that fit have room to spare in it; words cut short end at the rim's padding (at its widest
    /// row when even one line is taller than the control).
    static func box(_ fit: Fit, in shape: BCDTouchShape, frame: CGSize) -> CGSize {
        let inset = padding(frame)
        let inner = max(Double(frame.height) - 2 * inset, 0)
        let edge = Double(fit.block.height) <= inner ? Double(fit.block.height) / 2 : 0
        let half = halfWidth(shape, frame: frame, inset: inset, at: edge) ?? 0
        return CGSize(width: max(2 * half, fit.whole ? Double(fit.block.width) : 0), height: Double(fit.block.height) + 4)
    }
}

// MARK: - Where the editor's panels go

enum BCDTouchPanelPlacement {
    /// A panel docked to the side of the screen that leaves `avoid` (the selected control)
    /// uncovered: left or right on a screen wider than tall or wide enough for a side panel (an
    /// upright iPad), else (an upright phone) top or bottom. `bar` is what the editor's bar takes
    /// at the top. Safe-area insets are kept clear.
    /// `wide`: the panel's widest (an iPad's roomier panels).
    static func dock(screen: CGSize, left: Double, right: Double, top: Double, bottom: Double,
                     bar: Double, avoid: CGRect?, wide: Double = 340) -> CGRect {
        let w = Double(screen.width), h = Double(screen.height)
        let gap = 12.0, edge = 8.0
        if w >= h || w >= 700 {
            let minX = max(left, edge), maxX = w - max(right, edge)
            let y0 = max(top, edge) + bar, y1 = h - max(bottom, edge)
            let roomLeft = (avoid.map { Double($0.minX) } ?? w / 2) - gap - minX
            let roomRight = maxX - (avoid.map { Double($0.maxX) } ?? w / 2) - gap
            let width = min(max(max(roomLeft, roomRight), 196), wide, maxX - minX)
            let x = roomRight >= roomLeft ? maxX - width : minX
            return CGRect(x: x, y: y0, width: width, height: max(y1 - y0, 120))
        }
        let minY = max(top, edge) + bar, maxY = h - max(bottom, edge)
        let x0 = max(left, edge), x1 = w - max(right, edge)
        let roomAbove = (avoid.map { Double($0.minY) } ?? h / 2) - gap - minY
        let roomBelow = maxY - (avoid.map { Double($0.maxY) } ?? h / 2) - gap
        let height = min(max(max(roomAbove, roomBelow), 220), 420, maxY - minY)
        let y = roomBelow >= roomAbove ? maxY - height : minY
        return CGRect(x: x0, y: y, width: max(x1 - x0, 120), height: height)
    }
}
