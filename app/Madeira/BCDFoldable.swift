// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import CoreGraphics

// Experimental support for the foldable iPhone (iPhone Duo, 2026-10-25): the gate, the display shape BCD
// assumes for its inner screen and the pure part of the scene-size follower (docs/HANDOFF.md, 2026-10-25).
//
// EVERYTHING NEW HERE RUNS ONLY WHEN BOTH ARE TRUE:
//   1. this device's model identifier is the foldable iPhone's (`BCDFoldableDevice.identifiers`), and
//   2. the experimental switch is on (Settings › Graphics › "Foldable iPhone support (experimental)", shown on
//      that model only; off unless a person turns it on).
// `BCDFoldable.active` is that gate. It is read once per app run (a change of the switch applies from the next
// start), and every hook elsewhere in the app is one line that tests it first. On any other iPhone or iPad the
// gate is false whatever the switch holds: no new layout, no new call, no new log line, and Settings has no new
// row. tests/host/check-bcd-foldable.py runs the gate over the identifiers of other devices and lists the hooks.
//
// Why the switch is off by default (also on the foldable model): nothing here could be run on the device. This
// Mac has Xcode 26.3, whose simulators have no foldable iPhone, and the model identifier comes from secondary
// sources (below). A person with the device turns it on and sends a log.

/// Which devices are the foldable iPhone.
enum BCDFoldableDevice {
    /// The foldable iPhone's model identifiers. iPhone Duo is `iPhone19,4` (board V68AP) in AppleDB and The Apple
    /// Wiki, and an Xcode 27.1 beta maps it to "iPhone Duo" (a GitHub gist's comment); one other comment there says
    /// 19,6, which no database repeats. Apple's own pages give only model numbers (A3447, A3719, A3720, A3721), no
    /// identifier. If the switch does not show on a real device, the log's `[device] model=` line has the true one.
    /// Round 626 (players' Send Logs of build 625, iOS 27.2): `[device] model=iPhone19,4 ... ram=11570MB cores=6` exists. A direct
    /// install (BCD is built with the iOS 26 SDK) runs in the compatibility window on the inner display: `screen=1125x2001@3.0x`,
    /// `window: scene=375x667 pt` (47EDCB, B8RAJG, 1FJAWW; 667x375 in landscape, A5G54C). Inside LiveContainer the scene is the whole
    /// inner display: `scene=951x669 pt` at 3x (QC1TBD), the same 1.42:1 shape as innerPoints below.
    static let identifiers: [String] = ["iPhone19,4"]

    static func isFoldable(machine: String) -> Bool { identifiers.contains(machine) }

    /// The model identifier `uname` reports ("iPhone19,4"; "arm64" in the simulator).
    static let machine: String = {
        var info = utsname()
        uname(&info)
        return withUnsafePointer(to: &info.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
    }()

    static var isThisDevice: Bool { isFoldable(machine: machine) }
}

/// The experimental switch: one string in UserDefaults, "1" on and "0" off.
enum BCDFoldableSwitch {
    static let key = "bcd.foldable.support"
    /// What no stored answer means. Off: see the header.
    static let defaultOn = false

    static func resolve(_ stored: String?) -> Bool {
        switch stored?.trimmingCharacters(in: .whitespaces) {
        case "1": return true
        case "0": return false
        default: return defaultOn
        }
    }

    /// The gate: the foldable model AND the switch.
    static func gate(machine: String, stored: String?) -> Bool {
        BCDFoldableDevice.isFoldable(machine: machine) && resolve(stored)
    }

    static var stored: String? { UserDefaults.standard.string(forKey: key) }
    static func store(_ on: Bool) { UserDefaults.standard.set(on ? "1" : "0", forKey: key) }
}

/// The one gate, and what the rest of the app asks it.
enum BCDFoldable {
    /// Read once per app run.
    static let active: Bool = BCDFoldableSwitch.gate(machine: BCDFoldableDevice.machine, stored: BCDFoldableSwitch.stored)

    /// The inner display, in landscape. Apple's specification: 1878 x 2670 pixels at 430 ppi (the outer display:
    /// 1398 x 2034 at 460 ppi, 1.455:1 against the inner 1.422:1). The points are those pixels at 3x; only the shape
    /// is used (a new library entry's default Resolution), so a scale of 2.8 (the App Store Connect screenshot size
    /// of the inner display, 2007 x 2853, would make it 951 x 669 pt) gives the same shape.
    static let innerPixels = CGSize(width: 2670, height: 1878)
    static let innerPoints = CGSize(width: 890, height: 626)

    /// The screen shape a new entry's default Resolution is made from (ResolutionChoices.screen): the inner display's,
    /// whichever screen BCD started on (UIScreen.main is ambiguous on a two-display phone, Apple says); nil when the gate
    /// is shut, which leaves today's reading of the screen.
    static func screen(active: Bool) -> (points: CGSize, pixels: CGSize)? {
        active ? (innerPoints, innerPixels) : nil
    }
    static var innerScreen: (points: CGSize, pixels: CGSize)? { screen(active: active) }
}

/// When the iPad layout does not fit. BCD's library draws its iPad layout (a top bar of tabs and chips, a game page with a 320 pt side
/// panel, 36 pt gutters) for a regular x regular window. Apple says the inner display is regular x regular in every pose, but its portrait
/// pose is 626 pt wide (or 669): there the top bar runs out of both edges and the game page's panel off the right (previewed with
/// tools/ui-preview.py --stage 626x890:regular,regular, 2026-10-25; at 890 x 626 and at an iPad mini's 744 it fits). Below this width the
/// scene's horizontal size class is read as compact, which gives the layout of an iPhone held upright, as on the outer display.
enum BCDFoldableLayout {
    static let padMinimumWidth = 700.0

    /// Whether a window this wide (points) should read as compact. A width of zero (no window yet) never does.
    static func compact(forWidth width: Double) -> Bool { width > 0 && width < padMinimumWidth }
}

/// What the follower sees of the scene on one layout pass.
struct BCDFoldableSnapshot: Equatable {
    /// The game window's size, in points.
    var width: Double
    var height: Double
    /// Its safe area (the four sides separately: they are not equal on this device).
    var top: Double
    var left: Double
    var bottom: Double
    var right: Double

    var aspect: Double { min(width, height) > 0 ? max(width, height) / min(width, height) : 0 }
    var text: String { String(format: "%.0fx%.0f pt safe t%.0f l%.0f b%.0f r%.0f", width, height, top, left, bottom, right) }
}

/// Notices a change of the scene's size or safe area between two layout passes. The size of a scene changes with a
/// fold, an unfold, a rotation, Split View or a window resize, and nothing says so but the layout itself.
struct BCDFoldableTracker {
    enum Verdict: Equatable {
        /// The first pass of a session.
        case first
        /// Neither size nor insets moved by more than `tolerance`.
        case unchanged
        /// The window's size moved.
        case resized
        /// Only the safe area moved.
        case insets
    }

    /// Points. Layout passes repeat with sub-point noise while a window animates.
    static let tolerance = 0.5

    private(set) var last: BCDFoldableSnapshot?
    private(set) var previous: BCDFoldableSnapshot?
    private(set) var changes = 0

    mutating func reset() { last = nil; previous = nil; changes = 0 }

    mutating func note(_ now: BCDFoldableSnapshot) -> Verdict {
        guard let was = last else { last = now; return .first }
        let t = Self.tolerance
        let size = abs(now.width - was.width) > t || abs(now.height - was.height) > t
        let safe = abs(now.top - was.top) > t || abs(now.left - was.left) > t
            || abs(now.bottom - was.bottom) > t || abs(now.right - was.right) > t
        guard size || safe else { return .unchanged }
        previous = was
        last = now
        changes += 1
        return size ? .resized : .insets
    }

    /// The `[foldable]` log line for a verdict; `extra` is what only UIKit knows (screen, scale, size classes).
    static func line(_ verdict: Verdict, now: BCDFoldableSnapshot, was: BCDFoldableSnapshot?, changes: Int, extra: String) -> String {
        let what: String
        switch verdict {
        case .first: what = "session start"
        case .unchanged: what = "unchanged"
        case .resized: what = "resized from \(was?.text ?? "?")"
        case .insets: what = "safe area moved from \(was?.text ?? "?")"
        }
        return "[foldable] \(what): now \(now.text) (aspect \(String(format: "%.2f", now.aspect))) change=\(changes)\(extra.isEmpty ? "" : " " + extra)"
    }
}
