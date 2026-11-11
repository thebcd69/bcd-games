// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CoreGraphics
import Foundation

// Aim mode (round 604): a part of the screen where a finger is the mouse of a first- or third-person game. Drag in the zone and the camera
// turns; the finger never moves a pointer there. The touch controls keep their places (a finger on a control never reaches the zone);
// only the touches that miss them can aim. Per game, off until its player turns it on (the game page's Aim mode, the in-game menu's
// Controls). This file is plain values and arithmetic, Foundation and CoreGraphics only: the settings, whether a point is in the zone,
// how a finger's travel becomes whole mouse counts, and the one finger that aims. BCDAimLive.swift reads the touches.

struct BCDAimSettings: Equatable {
    /// Which part of the screen aims.
    enum Zone: String, CaseIterable {
        case off = "", right, left, all
        var title: String {
            switch self {
            case .off: return "Off"
            case .right: return "The right half"
            case .left: return "The left half"
            case .all: return "Anywhere there is no control"
            }
        }
    }

    /// Night round 2026-11-11 (A5 S13; NieR: Automata 83B2RX: "the swipe to aim doesn't work with controller config since it's trying to use the mouse cursor"):
    /// what the aiming finger drives. The mouse is what it always did; the right stick is for a game that plays with a controller layout and ignores the mouse
    /// for the camera (the way gyro aim can drive the stick).
    enum Output: String, CaseIterable {
        case mouse = "", stick
        var title: String { self == .mouse ? "The mouse" : "The right stick" }
    }

    var zone = Zone.off
    var output = Output.mouse
    /// A multiple: 1 is two mouse counts for every point the finger travels (the speed the pointer settings' mouse-look starts at).
    var sensitivity = 1.0
    /// How fast the camera turns up and down against left and right, in percent.
    var vertical = 100
    var invertY = false
    /// A short tap in the zone is a left click.
    var tapClicks = false

    var isOn: Bool { zone != .off }

    static let sensitivityChoices: [Double] = [0.5, 0.75, 1, 1.5, 2, 3, 4]
    static let sensitivityRange = 0.1...8.0
    static let verticalChoices = [50, 75, 100, 125, 150]
    static let verticalRange = 25...200
    /// The lines of a game this feature keeps.
    static let lineNames = ["aim-zone", "aim-sens", "aim-vertical", "aim-invert", "aim-tap", "aim-as"]

    static func parse(_ get: (String) -> String?) -> BCDAimSettings {
        func word(_ line: String) -> String { (get(line) ?? "").trimmingCharacters(in: .whitespaces).lowercased() }
        var s = BCDAimSettings()
        s.zone = Zone(rawValue: word("aim-zone")) ?? .off
        s.output = Output(rawValue: word("aim-as")) ?? .mouse
        if let v = Double(word("aim-sens")), v.isFinite { s.sensitivity = min(max(v, sensitivityRange.lowerBound), sensitivityRange.upperBound) }
        if let v = Int(word("aim-vertical")) { s.vertical = min(max(v, verticalRange.lowerBound), verticalRange.upperBound) }
        s.invertY = word("aim-invert") == "1"
        s.tapClicks = word("aim-tap") == "1"
        return s
    }
}

enum BCDAimMath {
    /// Mouse counts for a point of the finger's travel at sensitivity 1.
    static let countsPerPoint = 2.0
    /// A touch that lasts less than this and moves less than `tapTravel` is a tap.
    static let tapSeconds = 0.25, tapTravel = 12.0

    /// Fractions of a count that were not sent yet (so a slow drag still turns the camera).
    struct Carry: Equatable {
        var x = 0.0, y = 0.0
        mutating func reset() { x = 0; y = 0 }
    }

    /// Whether a touch that began at `point` on a screen of `bounds` is in the zone.
    static func contains(_ point: CGPoint, bounds: CGSize, zone: BCDAimSettings.Zone) -> Bool {
        switch zone {
        case .off: return false
        case .all: return true
        case .right: return point.x >= bounds.width / 2
        case .left: return point.x < bounds.width / 2
        }
    }

    /// The whole counts for a finger's travel of (`dx`, `dy`) points, y growing downwards; the rest stays in `carry`. Dragging right
    /// turns right and dragging down looks down, as a mouse does.
    static func counts(dx: Double, dy: Double, _ s: BCDAimSettings, carry: inout Carry) -> (dx: Int32, dy: Int32) {
        guard dx.isFinite, dy.isFinite else { return (0, 0) }
        let gain = countsPerPoint * s.sensitivity
        carry.x += dx * gain
        carry.y += dy * gain * Double(s.vertical) / 100 * (s.invertY ? -1 : 1)
        let ix = Int32(max(-30000, min(30000, carry.x.rounded(.towardZero))))
        let iy = Int32(max(-30000, min(30000, carry.y.rounded(.towardZero))))
        carry.x -= Double(ix)
        carry.y -= Double(iy)
        return (ix, iy)
    }
}

extension BCDAimMath {
    /// Night round 2026-11-11 (A5 S13): the stick for a finger's travel in one touch event: a drag of this many points in one event is a full push of the
    /// stick (1 point an event, the pace of a slow drag at 60 events a second, is 40 %).
    static let pointsPerFullPush = 2.5
    /// A stick that gets no new move for this long goes back to rest: a finger that stops stops the camera (a stick, unlike a mouse, is a rate).
    static let stickHoldSeconds = 0.05

    /// The right stick (x right, y up, each -1...1, the vector at most 1) for a travel of (`dx`, `dy`) points, y growing downwards: dragging right turns right
    /// and dragging down looks down (stick down is negative). `sensitivity`, `vertical` and `invertY` keep their meaning.
    static func stickPush(dx: Double, dy: Double, _ s: BCDAimSettings) -> (x: Double, y: Double) {
        guard dx.isFinite, dy.isFinite else { return (0, 0) }
        let x = dx * s.sensitivity / pointsPerFullPush
        let y = -dy * s.sensitivity / pointsPerFullPush * Double(s.vertical) / 100 * (s.invertY ? -1 : 1)
        let length = (x * x + y * y).squareRoot()
        return length > 1 ? (x / length, y / length) : (x, y)
    }
}

/// The one finger that aims, from touch-down to lift.
struct BCDAimTouch {
    private(set) var finger: Int?
    private var last = CGPoint.zero
    private var start = CGPoint.zero
    private var startTime = 0.0
    private var travelled = 0.0
    private(set) var carry = BCDAimMath.Carry()

    var isAiming: Bool { finger != nil }

    /// A finger went down at `point`: it aims when none does yet and it is in the zone. True when this finger aims now.
    mutating func began(id: Int, at point: CGPoint, time: Double, bounds: CGSize, _ s: BCDAimSettings) -> Bool {
        guard finger == nil, BCDAimMath.contains(point, bounds: bounds, zone: s.zone) else { return false }
        finger = id
        last = point
        start = point
        startTime = time
        travelled = 0
        carry.reset()
        return true
    }

    /// The aiming finger moved: the counts to send; nil for any other finger.
    mutating func moved(id: Int, to point: CGPoint, _ s: BCDAimSettings) -> (dx: Int32, dy: Int32)? {
        guard finger == id else { return nil }
        let dx = Double(point.x - last.x), dy = Double(point.y - last.y)
        last = point
        travelled = max(travelled, hypot(Double(point.x - start.x), Double(point.y - start.y)))
        return BCDAimMath.counts(dx: dx, dy: dy, s, carry: &carry)
    }

    /// Night round 2026-11-11 (A5 S13): the aiming finger moved: its travel since the last event, in points, for the stick; nil for any other finger.
    mutating func movedPoints(id: Int, to point: CGPoint) -> (dx: Double, dy: Double)? {
        guard finger == id else { return nil }
        let dx = Double(point.x - last.x), dy = Double(point.y - last.y)
        last = point
        travelled = max(travelled, hypot(Double(point.x - start.x), Double(point.y - start.y)))
        return (dx, dy)
    }

    /// The aiming finger lifted: true when it was a tap; nil for any other finger.
    mutating func ended(id: Int, time: Double) -> Bool? {
        guard finger == id else { return nil }
        let tap = time - startTime < BCDAimMath.tapSeconds && travelled < BCDAimMath.tapTravel
        finger = nil
        carry.reset()
        return tap
    }

    /// The touch was taken away; nothing is a tap.
    mutating func cancelled(id: Int) -> Bool {
        guard finger == id else { return false }
        finger = nil
        carry.reset()
        return true
    }

    mutating func reset() { finger = nil; carry.reset() }
}
