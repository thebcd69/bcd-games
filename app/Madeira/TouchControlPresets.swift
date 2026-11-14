// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
// app/Sources/Core/VirtualPadModel.swift) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
import Foundation
import SwiftUI
import UIKit

// ============================================================================
// ml1530/ml1970 — NAMED TOUCH-CONTROL LAYOUTS.
//
// A layout is the landscape editor's `TouchControlsModel.controls`. The user's
// own layouts ("Custom Layout 1", "Custom Layout 2", ...) are kept app-wide in
// Documents/bcd-control-presets.json, beside bcd-controls.json, so a
// device backup carries them. Built-ins ship in code and can be loaded but
// never changed: a full XInput layout named "Xbox controller" (shown as "Full controller") and a
// keyboard-and-mouse layout named "PC keyboard" (a WASD stick, mouse buttons and the usual keys).
//
// bcd-controls.json stays the working copy the overlay draws; it records
// which layout it was loaded from (`TouchControlsModel.layoutID`) so edits made
// in the editor are written back to that custom layout when editing ends.
//
// BCD's interface offers the layouts in the touch controls' top bar and in the in-game menu
// (BCDTouchLive.swift). A library game remembers its layout (`LibraryEntry.controlLayout`);
// other games and the overlay outside the library keep the one shared working copy.
//
// Switches (Documents/bcd.cfg `env.NAME = value`, or the environment):
//   MADEIRA_CONTROL_PRESETS=0        no layout menu, no write-back (default on)
//   MADEIRA_CONTROLS_XBOX_DEFAULT=1  a user with no controls file gets the
//                                    built-in once (default OFF: otherwise the
//                                    built-in is only applied from the menu)
//   MADEIRA_TOUCH_GAME_DEFAULT=0     a library game whose touch controls are on with nothing on
//                                    screen is not given a built-in layout (default on; see
//                                    ControlPresetsModel.applyGameDefault)
// ============================================================================

// MARK: - Pure preset state (also compiled by the host regression test)

struct ControlPreset: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var controls: [TouchControl]
}

/// The screen a built-in layout is placed on, in points, with its safe-area
/// insets. Built-ins are laid out in points from the edges and then normalised
/// to THIS screen, so one layout reads right on a phone and on a tablet.
struct ControlPresetScreen: Equatable {
    var width: Double
    var height: Double
    var left = 0.0, right = 0.0, top = 0.0, bottom = 0.0

    /// The overlay is landscape-only, so a portrait screen is laid out as the
    /// landscape one it rotates to: the top/bottom insets move to the sides.
    var landscape: ControlPresetScreen {
        guard height > width else { return self }
        let side = max(top, bottom)
        return ControlPresetScreen(width: height, height: width, left: side, right: side,
                                   top: 0, bottom: min(bottom, 21))
    }

    /// A 6.3-inch phone in landscape: what the stored copy of a built-in is
    /// laid out for. Loading lays it out again for the real screen.
    static let referencePhone = ControlPresetScreen(width: 874, height: 402,
                                                    left: 59, right: 59, top: 0, bottom: 21)
}

enum ControlPresetLayout {
    static let xboxID = "builtin.xbox"
    static let xboxName = "Xbox controller"
    static let keyboardID = "builtin.keyboard"
    static let keyboardName = "PC keyboard"
    /// Mirrors `TouchControlsModel.baseDiameter`.
    static let baseDiameter = 64.0

    /// The margins a built-in keeps from each edge, whatever the reported insets.
    static func margins(_ s: ControlPresetScreen) -> (left: Double, right: Double, top: Double, bottom: Double) {
        (max(s.left, 16), max(s.right, 16), max(s.top, 8), max(s.bottom, 8))
    }

    /// The overlay's own buttons, centred at the top (TouchControlsModel.
    /// hitsInteractive claims this band). A built-in leaves it free.
    static func topBarRect(_ s: ControlPresetScreen) -> CGRect {
        CGRect(x: s.width / 2 - 100, y: 0, width: 200, height: 76)
    }

    /// The drawn bounding box, in points: `baseDiameter` × the control's scale
    /// for a round button, the pad button's own shape otherwise (a wide
    /// shoulder, a Start/Select pill), centred on its normalised position.
    static func box(_ c: TouchControl, screen s: ControlPresetScreen) -> CGRect {
        let z = c.action.controlSize(diameter: CGFloat(baseDiameter * c.scale))
        return CGRect(x: c.nx * s.width - Double(z.width) / 2, y: c.ny * s.height - Double(z.height) / 2,
                      width: Double(z.width), height: Double(z.height))
    }

    /// The built-in controller layout, using the editor's controller mappings:
    ///
    ///   LT LB  (top left)                              (top right)  RB RT
    ///   D-pad  above the left stick        A/B/X/Y diamond above the right stick
    ///   LS (bottom left)  L3    View  Menu     R3  RS (bottom right)
    ///
    /// Placed in points from the safe-area edges, then normalised. `k` scales
    /// the whole thing up on a tall (tablet) screen, where a phone-sized stick
    /// would be lost; every scale stays inside the editor's 0.5–3.0 pinch clamp.
    static func xbox(for screen: ControlPresetScreen) -> [TouchControl] {
        let s = screen.landscape
        let W = s.width, H = s.height
        guard W > 0, H > 0 else { return [] }
        let k = min(max(H / 400, 1.0), 1.3)
        let (L, R, T, B) = margins(s)
        var out: [TouchControl] = []
        func add(_ name: String, _ scale: Double, _ x: Double, _ y: Double) {
            var c = TouchControl()
            c.action = .pad(name)
            c.scale = scale
            c.nx = x / W
            c.ny = y / H
            out.append(c)
        }
        func d(_ scale: Double) -> Double { baseDiameter * scale }
        let stickScale = 1.45 * k, faceScale = 0.78 * k, dpadScale = 0.7 * k
        let shoulderScale = 0.88 * k, systemScale = 0.75 * k, clickScale = 0.62 * k

        // Sticks: bottom corners, clear of the home indicator.
        let stick = d(stickScale)
        let stickY = H - B - 12 * k - stick / 2
        let lStickX = L + 76 * k, rStickX = W - R - 76 * k
        add("LS", stickScale, lStickX, stickY)
        add("RS", stickScale, rStickX, stickY)
        let stickTop = stickY - stick / 2

        // Shoulders: a column in each top corner, trigger above bumper as on
        // the controller. Drawn 1.6× as wide as they are tall and 0.74 d high
        // (ControlAction.controlSize), so the column is two of those plus a gap.
        let sh = d(shoulderScale), shW = sh * 1.6, shH = sh * 0.74
        let shoulderX = shW / 2 + 6 * k
        let triggerY = T + 6 * k + shH / 2
        let bumperY = triggerY + shH + 8 * k
        add("LT", shoulderScale, L + shoulderX, triggerY)
        add("LB", shoulderScale, L + shoulderX, bumperY)
        add("RB", shoulderScale, W - R - shoulderX, bumperY)
        add("RT", shoulderScale, W - R - shoulderX, triggerY)
        // The columns' bottom and inner edge, for placing the D-pad cross and
        // the face diamond below them (or, on a short screen, inboard of them).
        let rowBottom = bumperY + shH / 2
        let columnInner = shoulderX + shW / 2 + 10 * k

        // D-pad: a tight cross at the left edge, between the shoulder column
        // and the stick, so the middle of the screen stays clear. When the
        // screen is too short for that stack (a phone with a tall column), the
        // cross moves inboard of the column instead and rises beside it.
        let dp = d(dpadScale), arm = dp + 6 * k
        let stacked = rowBottom + 8 * k + 2 * arm + dp <= stickTop - 8 * k
        let dpadX = stacked ? L + 6 * k + arm + dp / 2 : L + columnInner + arm + dp / 2
        let dpadY = stacked ? rowBottom + 8 * k + arm + dp / 2
                            : max(stickTop - 22 * k - arm - dp / 2, T + 6 * k + arm + dp / 2)
        add("D↑", dpadScale, dpadX, dpadY - arm)
        add("D←", dpadScale, dpadX - arm, dpadY)
        add("D→", dpadScale, dpadX + arm, dpadY)
        add("D↓", dpadScale, dpadX, dpadY + arm)

        // A/B/X/Y: a tight diamond at the right edge, mirroring the D-pad.
        // Diagonal neighbours sit o·√2 apart, which clears one face button's
        // diameter.
        let face = d(faceScale)
        let o = face / 2 + 22 * k
        let cx = stacked ? W - R - 6 * k - o - face / 2 : W - R - columnInner - o - face / 2
        let cy = stacked ? rowBottom + 8 * k + o + face / 2
                         : max(stickTop - 22 * k - o - face / 2, T + 6 * k + o + face / 2)
        add("Y", faceScale, cx, cy - o)
        add("X", faceScale, cx - o, cy)
        add("B", faceScale, cx + o, cy)
        add("A", faceScale, cx, cy + o)

        // View / Menu: bottom centre, between the sticks.
        let sys = d(systemScale)
        let sysY = H - B - 20 * k - sys / 2
        add("View", systemScale, W / 2 - 46 * k, sysY)
        add("Menu", systemScale, W / 2 + 46 * k, sysY)

        // L3 / R3: small buttons on the inner side of each stick, level with
        // its bottom edge.
        let click = d(clickScale)
        let clickY = stickY + stick / 2 - click / 2
        add("L3", clickScale, lStickX + stick / 2 + 20 * k + click / 2, clickY)
        add("R3", clickScale, rStickX - stick / 2 - 20 * k - click / 2, clickY)
        return out
    }

    /// The built-in keyboard-and-mouse layout, for games that read no controller:
    ///
    ///   Esc  Tab  (top left)                                    (top right)  keyboard  Enter
    ///   WASD stick (bottom left)             Q  F  E / Ctrl  R  Shift / mouse R  mouse L  (bottom right)
    ///
    /// A key stick for W A S D, and a 3 x 3 block of round buttons in the bottom-right corner (the mouse
    /// buttons and Space on the bottom row, then E, R and F, then Shift, Ctrl and Q), Esc and Tab in the
    /// top-left corner, Enter and the iOS keyboard in the top-right one. Placed like the controller layout:
    /// in points from the safe-area edges, then normalised, clear of the overlay's own top buttons.
    static func keyboard(for screen: ControlPresetScreen) -> [TouchControl] {
        let s = screen.landscape
        let W = s.width, H = s.height
        guard W > 0, H > 0 else { return [] }
        let k = min(max(H / 400, 1.0), 1.3)
        let (L, R, T, B) = margins(s)
        var out: [TouchControl] = []
        func add(_ action: ControlAction, _ scale: Double, _ x: Double, _ y: Double) {
            var c = TouchControl()
            c.action = action
            c.scale = scale
            c.nx = x / W
            c.ny = y / H
            out.append(c)
        }
        func d(_ scale: Double) -> Double { baseDiameter * scale }

        // The WASD stick: bottom-left, where the controller layout's left stick is.
        let stickScale = 1.45 * k
        let stick = d(stickScale)
        add(.joystickWASD, stickScale, L + 76 * k, H - B - 12 * k - stick / 2)

        // The 3 x 3 block, from the bottom-right corner: column 0 is the rightmost, row 0 the bottom one.
        let keyScale = 0.8 * k
        let key = d(keyScale), pitch = key + 10 * k
        func x(_ column: Int) -> Double { W - R - 6 * k - key / 2 - Double(column) * pitch }
        func y(_ row: Int) -> Double { H - B - 12 * k - key / 2 - Double(row) * pitch }
        add(.mouseLeft, keyScale, x(0), y(0))
        add(.mouseRight, keyScale, x(1), y(0))
        add(.key(0x20), keyScale, x(2), y(0))      // Space
        add(.key(0x45), keyScale, x(0), y(1))      // E
        add(.key(0x52), keyScale, x(1), y(1))      // R
        add(.key(0x46), keyScale, x(2), y(1))      // F
        add(.key(0x10), keyScale, x(0), y(2))      // Shift
        add(.key(0x11), keyScale, x(1), y(2))      // Ctrl
        add(.key(0x51), keyScale, x(2), y(2))      // Q

        // The corners at the top, away from the overlay's own buttons in the middle.
        let smallScale = 0.7 * k
        let small = d(smallScale), step = small + 8 * k
        let topY = T + 6 * k + small / 2
        add(.key(0x1B), smallScale, L + 6 * k + small / 2, topY)                 // Esc
        add(.key(0x09), smallScale, L + 6 * k + small / 2 + step, topY)          // Tab
        add(.key(0x0D), smallScale, W - R - 6 * k - small / 2, topY)             // Enter
        add(.keyboardToggle, smallScale, W - R - 6 * k - small / 2 - step, topY) // the iOS keyboard
        return out
    }
}

// MARK: - More built-in layouts (Porthole port, item 9)

/// madeira-bcd (Porthole port, item 9): six more built-in layouts to start from, each a choice in the layout list like the two above
/// (none is put on screen by itself; a game's default stays the controller or the PC keyboard). The left-handed one is BCD's own
/// controller layout turned round. The other five are Porthole's arrangements (app/Sources/Core/VirtualPadModel.swift,
/// `VirtualPadLayout.place` and `frames`: the proportions of each part, the room kept between them and the shrinking until nothing
/// touches), made of BCD's controls: a stick is a stick of the same size, Porthole's diamond of face buttons is four round buttons
/// where Porthole draws them, its direction pad four round buttons that fill the cross (BCD has no cross of one piece), a shoulder
/// is BCD's wide shoulder as wide as Porthole's, a racing pedal is a trigger in an upright rectangle. Two changes for BCD's screen:
/// View and Menu sit either side of the overlay's top buttons instead of between them, and the right edge keeps BCD's margin
/// (Porthole keeps 44 points there for its menu's swipe).
extension ControlPresetLayout {
    enum Arrangement: String, CaseIterable {
        case leftHanded, middleSticks, handheld, retro, racing, small

        var id: String { "builtin." + rawValue }
        var name: String {
            switch self {
            case .leftHanded: return "Left-handed controller"
            case .middleSticks: return "Sticks in the middle"
            case .handheld: return "Handheld controller"
            case .retro: return "Retro pad"
            case .racing: return "Racing pad"
            case .small: return "Small controller"
            }
        }
    }

    /// The built-ins that press the touch controller (all but the PC keyboard): without touch XInput their buttons are inert.
    static func needsTouchPad(_ id: String) -> Bool { id == xboxID || Arrangement.allCases.contains { $0.id == id } }

    static func arranged(_ a: Arrangement, for screen: ControlPresetScreen) -> [TouchControl] {
        let s = screen.landscape
        guard s.width > 0, s.height > 0 else { return [] }
        if a == .leftHanded {
            // The controller layout the other way round, across the middle of the room between the margins.
            let (L, R, _, _) = margins(s)
            return xbox(for: screen).map { c in
                var c = c
                c.nx = (L + s.width - R) / s.width - c.nx
                return c
            }
        }
        var scale = 1.0
        while true {
            let parts = porthole(a, s, scale: scale)
            let out = controls(parts, s)
            if scale <= 0.5 || fits(out, s) { return out }
            scale = max(0.5, scale * 0.95)
        }
    }

    /// The parts of an arrangement as Porthole's VirtualPadLayout places them: frames in points, from the top-left corner.
    private enum Part: Hashable { case leftStick, rightStick, dpad, face, leftBumper, rightBumper, leftTrigger, rightTrigger, back, start }

    private static func porthole(_ a: Arrangement, _ s: ControlPresetScreen, scale: Double) -> [Part: CGRect] {
        let (L, R, T, B) = margins(s)
        let W = s.width, H = s.height
        let u = min(min(W, H), 470) * scale                    // the sizes belong to a screen up to 470 points: a tablet gets a big phone's
        let m = 0.035 * u + 8                                   // the room from the edges and between parts
        let left = L + m, right = W - R - m * 0.6
        let bottom = H - B - m, top = T + m * 0.8
        let bw = 0.20 * u, bh = 0.08 * u                        // a bumper or a trigger
        let sw = 0.14 * u, sh = 0.075 * u                       // View and Menu
        var out: [Part: CGRect] = [:]
        func bumpers() {
            out[.leftBumper] = CGRect(x: left, y: top + (out[.leftTrigger] != nil ? bh + m * 0.4 : 0), width: bw, height: bh)
            out[.rightBumper] = CGRect(x: right - bw, y: top + (out[.rightTrigger] != nil ? bh + m * 0.4 : 0), width: bw, height: bh)
        }
        func centre() {
            // BCD: either side of the overlay's top buttons (topBarRect), level with the top row.
            let bar = topBarRect(s)
            out[.back] = CGRect(x: bar.minX - m * 0.4 - sw, y: top, width: sw, height: sh)
            out[.start] = CGRect(x: bar.maxX + m * 0.4, y: top, width: sw, height: sh)
        }
        switch a {
        case .leftHanded:
            break
        case .middleSticks:
            let stick = 0.22 * u, face = 0.33 * u, dpad = 0.22 * u
            out[.dpad] = CGRect(x: left, y: bottom - dpad, width: dpad, height: dpad)
            out[.face] = CGRect(x: right - face, y: bottom - face, width: face, height: face)
            // The sticks side by side in the middle of the room between the direction pad and the face buttons.
            let room = (right - face - m) - (left + dpad + m)
            let start = left + dpad + m + max(0, (room - (2 * stick + m)) / 2)
            out[.leftStick] = CGRect(x: start, y: bottom - stick, width: stick, height: stick)
            out[.rightStick] = CGRect(x: start + stick + m, y: bottom - stick, width: stick, height: stick)
            out[.leftTrigger] = CGRect(x: left, y: top, width: bw, height: bh)
            out[.rightTrigger] = CGRect(x: right - bw, y: top, width: bw, height: bh)
            bumpers(); centre()
        case .handheld:
            // Two columns: a stick over the direction pad on the left, the face buttons over a stick on the right.
            let stick = 0.22 * u, face = 0.33 * u, dpad = 0.22 * u
            out[.dpad] = CGRect(x: left, y: bottom - dpad, width: dpad, height: dpad)
            out[.leftStick] = CGRect(x: left, y: bottom - dpad - m - stick, width: stick, height: stick)
            out[.rightStick] = CGRect(x: right - face / 2 - stick / 2, y: bottom - stick, width: stick, height: stick)
            out[.face] = CGRect(x: right - face, y: bottom - stick - m - face, width: face, height: face)
            out[.leftTrigger] = CGRect(x: left, y: top, width: bw, height: bh)
            out[.rightTrigger] = CGRect(x: right - bw, y: top, width: bw, height: bh)
            bumpers(); centre()
        case .retro:
            let dpad = 0.30 * u, face = 0.40 * u
            out[.dpad] = CGRect(x: left, y: bottom - dpad, width: dpad, height: dpad)
            out[.face] = CGRect(x: right - face, y: bottom - face, width: face, height: face)
            bumpers(); centre()
        case .racing:
            let stick = 0.27 * u, face = 0.30 * u
            let pw = 0.115 * u, ph = 0.32 * u                   // the pedals: the right trigger (gas) at the edge, the left (brake) beside it
            out[.leftStick] = CGRect(x: left, y: bottom - stick, width: stick, height: stick)
            out[.rightTrigger] = CGRect(x: right - pw, y: bottom - ph, width: pw, height: ph)
            out[.leftTrigger] = CGRect(x: right - 2 * pw - m * 0.6, y: bottom - ph, width: pw, height: ph)
            out[.face] = CGRect(x: right - 2 * pw - m * 0.6 - m - face, y: bottom - face, width: face, height: face)
            bumpers(); centre()
        case .small:
            let stick = 0.26 * u, face = 0.38 * u
            out[.leftStick] = CGRect(x: left, y: bottom - stick, width: stick, height: stick)
            out[.face] = CGRect(x: right - face, y: bottom - face, width: face, height: face)
            bumpers(); centre()
        }
        return out
    }

    /// Porthole's parts as BCD's controls, normalised to the screen.
    private static func controls(_ parts: [Part: CGRect], _ s: ControlPresetScreen) -> [TouchControl] {
        var out: [TouchControl] = []
        func add(_ name: String, _ diameter: Double, _ x: Double, _ y: Double, aspect: Double? = nil) {
            var c = TouchControl()
            c.action = .pad(name)
            c.scale = diameter / baseDiameter
            c.nx = x / s.width
            c.ny = y / s.height
            if let aspect { c.face = TouchControl.Face(shape: "rectangle", aspect: aspect, text: nil) }
            out.append(c)
        }
        /// Four buttons of a cluster: `across` of its width across, `out` of it from its middle, never under BCD's smallest size.
        func cluster(_ f: CGRect, _ names: (up: String, left: String, right: String, down: String), across: Double, out: Double) {
            let d = max(f.width * across, baseDiameter * 0.5), o = f.width * out
            add(names.up, d, f.midX, f.midY - o)
            add(names.left, d, f.midX - o, f.midY)
            add(names.right, d, f.midX + o, f.midY)
            add(names.down, d, f.midX, f.midY + o)
        }
        if let f = parts[.leftStick] { add("LS", f.width, f.midX, f.midY) }
        if let f = parts[.rightStick] { add("RS", f.width, f.midX, f.midY) }
        // The direction pad: BCD's four buttons fill Porthole's cross (39 % across, 30 % out); the face buttons sit where Porthole
        // draws them (36 % across, 32 % out).
        if let f = parts[.dpad] { cluster(f, ("D↑", "D←", "D→", "D↓"), across: 0.39, out: 0.30) }
        if let f = parts[.face] { cluster(f, ("Y", "X", "B", "A"), across: 0.36, out: 0.32) }
        for (part, name) in [(Part.leftTrigger, "LT"), (.leftBumper, "LB"), (.rightBumper, "RB"), (.rightTrigger, "RT")] {
            guard let f = parts[part] else { continue }
            if f.height > f.width {
                // A pedal: an upright rectangle of the pedal's own proportions (a rectangle keeps a square's area).
                add(name, (f.width * f.height).squareRoot(), f.midX, f.midY, aspect: f.width / f.height)
            } else {
                add(name, f.width / 1.6, f.midX, f.midY)        // BCD's wide shoulder is 1.6 diameters across
            }
        }
        if let f = parts[.back] { add("View", f.width / 1.2, f.midX, f.midY) }   // BCD's View and Menu are 1.2 diameters across
        if let f = parts[.start] { add("Menu", f.width / 1.2, f.midX, f.midY) }
        return out
    }

    /// Porthole's test for a size: every control on the screen and none touching another (here: BCD's drawn boxes inside the
    /// margins and clear of the overlay's top buttons; two round controls as circles 2 points apart, as the layouts are checked).
    static func fits(_ cs: [TouchControl], _ s: ControlPresetScreen) -> Bool {
        let (L, R, T, B) = margins(s)
        let safe = CGRect(x: L, y: T, width: s.width - L - R, height: s.height - T - B).insetBy(dx: -0.5, dy: -0.5)
        let boxes = cs.map { arrangedBox($0, s) }
        let bar = topBarRect(s)
        for b in boxes where !safe.contains(b) || b.intersects(bar) { return false }
        for i in boxes.indices {
            for j in boxes.indices where j > i {
                let a = boxes[i], b = boxes[j]
                let round = abs(a.width - a.height) < 1e-9 && abs(b.width - b.height) < 1e-9
                let dx = a.midX - b.midX, dy = a.midY - b.midY
                if round ? (dx * dx + dy * dy).squareRoot() < a.width / 2 + b.width / 2 + 2 : a.insetBy(dx: -1, dy: -1).intersects(b) {
                    return false
                }
            }
        }
        return true
    }

    /// A control's drawn box, its own shape counted (a pedal's rectangle).
    static func arrangedBox(_ c: TouchControl, _ s: ControlPresetScreen) -> CGRect {
        guard let aspect = c.face?.aspect, c.face?.shape == "rectangle" else { return box(c, screen: s) }
        let d = baseDiameter * c.scale, k = aspect.squareRoot()
        return CGRect(x: c.nx * s.width - d * k / 2, y: c.ny * s.height - d / k / 2, width: d * k, height: d / k)
    }
}

/// What a layout's controls do for the game (the log's `kind=`): `pad` presses the virtual XInput pad,
/// `keys` posts keyboard keys and mouse buttons (winios_post_key / winios_pointer, nothing of the controller
/// hub's "keys and mouse" mapping, which turns a physical pad into keys), `mixed` has both.
enum TouchLayoutKind: String {
    case none, pad, keys, mixed

    static func of(_ controls: [TouchControl]) -> TouchLayoutKind {
        var pad = false, keys = false
        for c in controls {
            switch c.action {
            case .pad(let name): if TouchPadAction.supported(name) { pad = true }
            case .key, .mouseLeft, .mouseRight, .joystickWASD, .joystickArrows, .keyboardToggle: keys = true
            case .none: break
            }
        }
        return pad && keys ? .mixed : pad ? .pad : keys ? .keys : .none
    }
}

/// What the touch layer does with the layout on screen, as the log line `[touch] ...` says it.
struct TouchLayoutPlan: Equatable {
    var layout: String
    var kind: TouchLayoutKind
    /// Keyboard keys and mouse buttons reach the game: on while the controls are shown; off while hidden.
    var keysPath: String
    /// The virtual pad: connected (shown, touch XInput on, taking input), waiting (shown, but a menu, the
    /// starting screen or the editor has it), off (shown, but touch XInput is off: the buttons are inert),
    /// hidden (the controls are not shown).
    var pad: String
    var controls: Int

    var line: String {
        "[touch] layout=\"\(layout)\" kind=\(kind.rawValue) keys-path=\(keysPath) pad=\(pad) controls=\(controls)"
    }

    static func make(layout: String, controls: [TouchControl], shown: Bool, accepting: Bool, padAvailable: Bool) -> TouchLayoutPlan {
        let kind = TouchLayoutKind.of(controls)
        let hasKeys = kind == .keys || kind == .mixed, hasPad = kind == .pad || kind == .mixed
        return TouchLayoutPlan(layout: layout, kind: kind,
                               keysPath: hasKeys ? (shown ? "on" : "off") : "n/a",
                               pad: hasPad ? (!shown ? "hidden" : !padAvailable ? "off" : accepting ? "connected" : "waiting") : "n/a",
                               controls: controls.count)
    }
}

extension ControlPresetLayout {
    /// The built-in a game gets when its touch controls are on and nothing is on screen: the controller for a
    /// Steam game (nearly all read XInput), the keyboard and mouse for any other (a program added by hand,
    /// a GOG game) and for every game when touch XInput is off (a pad layout would be inert).
    static func defaultID(steam: Bool, touchPad: Bool) -> String {
        steam && touchPad ? xboxID : keyboardID
    }
}

/// The layout list: built-ins (read-only, in code), then the user's own
/// (persisted). Pure value type; `ControlPresetsModel` owns the file.
struct ControlPresetStore: Equatable {
    static let builtIns: [ControlPreset] = [
        ControlPreset(id: ControlPresetLayout.xboxID, name: ControlPresetLayout.xboxName,
                      controls: ControlPresetLayout.xbox(for: .referencePhone)),
        ControlPreset(id: ControlPresetLayout.keyboardID, name: ControlPresetLayout.keyboardName,
                      controls: ControlPresetLayout.keyboard(for: .referencePhone)),
    ] + ControlPresetLayout.Arrangement.allCases.map {   // madeira-bcd (Porthole port, item 9): six more to start from, after the two
        ControlPreset(id: $0.id, name: $0.name, controls: ControlPresetLayout.arranged($0, for: .referencePhone))
    }
    static let maxNameLength = 40

    private(set) var user: [ControlPreset] = []

    init(user: [ControlPreset] = []) { self.user = user.filter { !Self.isBuiltIn($0.id) } }

    var all: [ControlPreset] { Self.builtIns + user }

    static func isBuiltIn(_ id: String) -> Bool { builtIns.contains { $0.id == id } }

    func preset(_ id: String) -> ControlPreset? { all.first { $0.id == id } }

    /// The id if a layout still has it, else nil: a game's remembered layout may
    /// have been deleted since, and controls that no layout holds are unsaved.
    func resolvedID(_ id: String?) -> String? { id.flatMap { preset($0)?.id } }

    static func clean(_ name: String) -> String {
        String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxNameLength))
    }

    /// Case-insensitive, whitespace-trimmed: "xbox controller " IS the built-in.
    func named(_ name: String) -> ControlPreset? {
        let n = Self.clean(name).lowercased()
        guard !n.isEmpty else { return nil }
        return all.first { $0.name.lowercased() == n }
    }

    /// "Custom Layout N", the lowest N not taken.
    func nextCustomName() -> String {
        var n = 1
        while named("Custom Layout \(n)") != nil { n += 1 }
        return "Custom Layout \(n)"
    }

    enum SaveResult: Equatable {
        case created(String)      // new user layout, its id
        case replaced(String)     // an existing user layout of that name, its id
        case refusedBuiltIn       // the name is a built-in's: pick another
        case refusedEmpty
    }

    /// Save a layout under a name. A built-in's name is refused (built-ins are
    /// never overwritten); a user layout's name replaces that layout in place.
    mutating func save(name: String, controls: [TouchControl],
                       newID: () -> String = { UUID().uuidString }) -> SaveResult {
        let n = Self.clean(name)
        guard !n.isEmpty else { return .refusedEmpty }
        if let existing = named(n) {
            if Self.isBuiltIn(existing.id) { return .refusedBuiltIn }
            guard let i = user.firstIndex(where: { $0.id == existing.id }) else { return .refusedEmpty }
            user[i].controls = controls
            return .replaced(existing.id)
        }
        let id = newID()
        user.append(ControlPreset(id: id, name: n, controls: controls))
        return .created(id)
    }

    /// Overwrite a user layout's controls. false for a built-in or a missing id.
    mutating func overwrite(id: String, controls: [TouchControl]) -> Bool {
        guard let i = user.firstIndex(where: { $0.id == id }) else { return false }
        user[i].controls = controls
        return true
    }

    /// false for a built-in or a missing id.
    mutating func delete(id: String) -> Bool {
        guard let i = user.firstIndex(where: { $0.id == id }) else { return false }
        user.remove(at: i)
        return true
    }

    private struct File: Codable { var version: Int; var presets: [ControlPreset] }

    /// Only the user's layouts are written; built-ins live in code.
    func encoded() throws -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try e.encode(File(version: 1, presets: user))
    }

    static func decoded(_ data: Data) throws -> ControlPresetStore {
        let f = try JSONDecoder().decode(File.self, from: data)
        guard f.version == 1 else {
            throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "preset file version \(f.version)"))
        }
        return ControlPresetStore(user: f.presets)
    }

    /// What loading a layout puts on screen: its controls with FRESH ids (a
    /// control's id keys its touch state, and two loads of one layout must not
    /// share them), positions inside the editor's drag clamp, and scales inside
    /// the pinch clamp. A built-in is laid out again for the screen it is
    /// loaded on.
    static func layout(of p: ControlPreset, screen: ControlPresetScreen?) -> [TouchControl] {
        var controls = p.controls
        if let screen {
            let fitted = p.id == ControlPresetLayout.xboxID ? ControlPresetLayout.xbox(for: screen)
                : p.id == ControlPresetLayout.keyboardID ? ControlPresetLayout.keyboard(for: screen)
                : ControlPresetLayout.Arrangement.allCases.first(where: { $0.id == p.id }).map { ControlPresetLayout.arranged($0, for: screen) } ?? []
            if !fitted.isEmpty { controls = fitted }
        }
        return controls.map { c in
            var c = c
            c.id = UUID()
            c.nx = min(max(c.nx, 0.03), 0.97)
            c.ny = min(max(c.ny, 0.03), 0.97)
            c.scale = min(max(c.scale, 0.5), 3.0)
            return c
        }
    }
}

// MARK: - Persistence and overlay actions

/// Owns bcd-control-presets.json and applies layouts to TouchControlsModel.
/// Main thread only, like the overlay that drives it.
final class ControlPresetsModel: ObservableObject {
    static let shared = ControlPresetsModel()
    static let enabled = GamepadInput.flag("MADEIRA_CONTROL_PRESETS")
    /// Opt-in. "No bcd-controls.json" is not a fresh install: the file is
    /// only written once the controls or their visibility change, so an
    /// existing user who never touched them has none either and would get an
    /// 18-button overlay they did not ask for. Nothing on disk tells the two
    /// apart reliably, so the built-in is applied automatically only on request.
    static let xboxDefault = GamepadInput.optIn("MADEIRA_CONTROLS_XBOX_DEFAULT")
    /// A library game whose touch controls are on with nothing on screen gets a built-in layout for its kind
    /// (applyGameDefault). Only "0" turns it off.
    static let gameDefault = GamepadInput.flag("MADEIRA_TOUCH_GAME_DEFAULT")

    @Published private(set) var store = ControlPresetStore()
    /// A file that exists and cannot be read is left untouched: saving is
    /// refused instead of overwriting it.
    private(set) var readOnly = false
    private var logged = 0

    private static var url: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("bcd-control-presets.json")
    }

    private init() {
        guard let d = try? Data(contentsOf: Self.url) else { return }
        do {
            store = try ControlPresetStore.decoded(d)
        } catch {
            readOnly = true
            log("unreadable file kept, saving disabled: \(error.localizedDescription)")
        }
    }

    /// The controller built-in needs touch XInput; without it its buttons are inert.
    private static var touchPad: Bool { MainActor.assumeIsolated { GamepadInput.touchEnabled } }

    /// What the layout menu lists. The controller built-in needs touch XInput; the keyboard one does not.
    var available: [ControlPreset] {
        ControlPresetStore.builtIns.filter { ControlPresetLayout.needsTouchPad($0.id) == false || Self.touchPad } + store.user
    }

    var active: ControlPreset? { TouchControlsModel.shared.layoutID.flatMap { store.preset($0) } }

    /// On-screen controls that no layout holds: switching away would lose them.
    var currentIsUnsaved: Bool {
        let m = TouchControlsModel.shared
        return m.layoutID == nil && !m.controls.isEmpty
    }

    /// With MADEIRA_CONTROLS_XBOX_DEFAULT=1, a user with no bcd-controls.json gets the controller layout the
    /// first time the landscape overlay appears.
    var defaultPending: Bool {
        TouchControlsModel.shared.needsDefaultLayout && Self.enabled && Self.xboxDefault && Self.touchPad
    }

    private func log(_ line: String) {
        guard logged < 64 else { return }
        logged += 1
        fputs("[controls-layout] ml1970 \(line)\n", stderr)
    }

    @discardableResult private func persist() -> Bool {
        guard !readOnly else { return false }
        do {
            try store.encoded().write(to: Self.url, options: .atomic)
            return true
        } catch {
            log("write failed: \(error.localizedDescription)")
            return false
        }
    }

    /// The landscape screen the overlay is laid out on.
    static func currentScreen() -> ControlPresetScreen {
        let window = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows).first { $0.isKeyWindow }
        guard let window, window.bounds.width > 0 else { return .referencePhone }
        let i = window.safeAreaInsets
        let s = ControlPresetScreen(width: Double(window.bounds.width), height: Double(window.bounds.height),
                                    left: Double(i.left), right: Double(i.right),
                                    top: Double(i.top), bottom: Double(i.bottom))
        return s.landscape
    }

    /// Never over a layout the user already has: only when no controls file existed.
    func applyDefaultIfNeeded(screen: ControlPresetScreen) {
        let m = TouchControlsModel.shared
        guard m.needsDefaultLayout else { return }
        let apply = defaultPending && m.controls.isEmpty
        m.needsDefaultLayout = false
        guard apply else { return }
        load(ControlPresetLayout.xboxID, screen: screen, reason: "new-user-default")
    }

    /// Replace the on-screen layout with this one.
    func load(_ id: String, screen: ControlPresetScreen, reason: String = "chosen") {
        guard let p = store.preset(id) else { return }
        let m = TouchControlsModel.shared
        let controls = ControlPresetStore.layout(of: p, screen: screen)
        m.selected = nil
        m.controls = controls
        m.layoutID = p.id
        let kind = p.id == ControlPresetLayout.xboxID ? "xbox" : p.id == ControlPresetLayout.keyboardID ? "keyboard" : "custom"
        log("loaded layout=\(kind) controls=\(controls.count) reason=\(reason)")
    }

    /// A library game's touch controls are on and nothing is on screen (no controls, no layout): put a built-in
    /// layout on screen that suits the game, as if the player had picked it, so they do not have to build a
    /// controller or PC keyboard layout by hand. `steam`: a Steam game (the controller layout), else the keyboard
    /// one. Never over controls or a layout the player has (an empty custom layout is a layout), and nothing
    /// while the editor is open. MADEIRA_TOUCH_GAME_DEFAULT=0 and MADEIRA_CONTROL_PRESETS=0 turn it off.
    @discardableResult func applyGameDefault(steam: Bool, reason: String) -> Bool {
        let m = TouchControlsModel.shared
        guard Self.enabled, m.visible, !m.editing, m.controls.isEmpty, m.layoutID == nil else { return false }
        guard Self.gameDefault else {
            if !loggedGameDefaultOff {
                loggedGameDefaultOff = true
                LogStore.shared.log("[touch] default layout not applied: env.MADEIRA_TOUCH_GAME_DEFAULT=0 (reason=\(reason))")
            }
            return false
        }
        let id = ControlPresetLayout.defaultID(steam: steam, touchPad: Self.touchPad)
        guard let preset = store.preset(id) else { return false }
        load(id, screen: Self.currentScreen(), reason: "game-default-" + reason)
        LogStore.shared.log("[touch] default layout=\"\(BCDTouchNames.layoutName(id: preset.id, stored: preset.name))\" for=\(steam ? "steam" : "local") reason=\(reason) controls=\(m.controls.count)")
        return true
    }
    private var loggedGameDefaultOff = false

    /// The one `[touch] layout="..." kind=... keys-path=... pad=... controls=...` line for what the touch layer does with
    /// the layout on screen: logged when it changes (the overlay calls this on every change of controls, visibility,
    /// editing or menus), at most 64 times.
    func logPlan(shown: Bool, accepting: Bool) {
        let m = TouchControlsModel.shared
        let name = active.map { BCDTouchNames.layoutName(id: $0.id, stored: $0.name) } ?? (m.controls.isEmpty ? "none" : "unsaved")
        let plan = TouchLayoutPlan.make(layout: name, controls: m.controls, shown: shown, accepting: accepting,
                                        padAvailable: Self.touchPad)
        guard plan != lastPlan, plannedLines < 64 else { return }
        lastPlan = plan
        plannedLines += 1
        LogStore.shared.log(plan.line)
    }
    private var lastPlan: TouchLayoutPlan?
    /// A game session begins: its first plan is logged even if it equals the last session's.
    func sessionBegan() { lastPlan = nil }
    private var plannedLines = 0

    /// Keep the unsaved on-screen controls as the next "Custom Layout N".
    @discardableResult func keepCurrent() -> Bool {
        guard !readOnly else { return false }
        let m = TouchControlsModel.shared
        var s = store
        guard case .created(let id) = s.save(name: s.nextCustomName(), controls: m.controls) else { return false }
        store = s
        m.layoutID = id
        persist()
        log("kept on-screen controls as a custom layout controls=\(m.controls.count)")
        return true
    }

    /// A layout from a shared file (BCDControlShareLive.swift, round 604): added as a new custom layout under a name no other layout has, never over
    /// one; the on-screen controls are not touched. nil when saving is refused (an unreadable file is kept) or the name is taken after all.
    @discardableResult func importLayout(name: String, controls: [TouchControl]) -> String? {
        guard !readOnly, store.named(name) == nil else { return nil }
        var s = store
        guard case .created(let id) = s.save(name: name, controls: controls) else { return nil }
        store = s
        persist()
        log("added a shared layout controls=\(controls.count)")
        return id
    }

    /// "New layout": an empty custom layout, made active, opened in the editor.
    @discardableResult func createLayout() -> Bool {
        guard !readOnly else { return false }
        var s = store
        guard case .created(let id) = s.save(name: s.nextCustomName(), controls: []) else { return false }
        store = s
        persist()
        let m = TouchControlsModel.shared
        m.selected = nil
        m.controls = []
        m.layoutID = id
        m.visible = true
        m.editing = true
        log("created custom layout")
        return true
    }

    /// Delete a custom layout. The on-screen copy of a deleted active layout
    /// becomes the controller layout, or stays as unsaved controls without it.
    @discardableResult func delete(_ id: String) -> Bool {
        guard !readOnly else { return false }
        var s = store
        guard s.delete(id: id) else { return false }
        store = s
        persist()
        log("deleted custom layout")
        let m = TouchControlsModel.shared
        if m.layoutID == id {
            if Self.touchPad { load(ControlPresetLayout.xboxID, screen: Self.currentScreen(), reason: "deleted") }
            else { m.layoutID = nil }
        }
        return true
    }

    /// An edit session ended. Edits to a custom layout are written back to it;
    /// a built-in stays as shipped, so an edited copy of one becomes unsaved
    /// controls that the menu offers to keep before switching away.
    func editingEnded(baseline: [TouchControl]) {
        guard Self.enabled else { return }
        let m = TouchControlsModel.shared
        guard let id = m.layoutID else { return }
        if ControlPresetStore.isBuiltIn(id) {
            if baseline != m.controls {
                m.layoutID = nil
                log("edited built-in kept as unsaved controls=\(m.controls.count)")
            }
            return
        }
        var s = store
        guard !readOnly, s.overwrite(id: id, controls: m.controls) else { return }
        store = s
        persist()
        log("saved custom layout controls=\(m.controls.count)")
    }
}
