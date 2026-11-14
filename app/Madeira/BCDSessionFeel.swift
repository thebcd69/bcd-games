// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The in-game menu's "feel" settings (round 604): how the touch controls tick, hide and look, what a touch that misses the
// controls does, whether the phone shakes with the game's rumble, how the picture is filtered and turned, how the pointer
// behaves, and the controller chord that opens the menu.
//
// This file is plain values and rules, no engine, UIKit or storage type (tests/host/check-bcd-feel.py compiles it alone):
//   - the choices a row offers and the words for them;
//   - BCDSessionFeel, the set of values a game can keep for itself ("Save these settings for this game", B15). Every field
//     is optional: nil is "as BCD has always done it", so a game that saved nothing, or an older file, changes nothing;
//   - the few flags the touch drawing reads (BCDTouchFeel), kept here because BCDTouchLayouts.swift is in the preview build;
//   - the file format and the key a game's values are kept under.
// BCDSessionFeelLive.swift applies the values to the engine, BCDMenuMore.swift draws the rows.

// MARK: - What a row offers

struct BCDFeelChoice: Equatable {
    var id: String
    var title: String
}

/// How long the touch controls stay when no finger is on them. 0 is never: the controls stay as they always did.
enum BCDHideAfter {
    static let seconds: [Int] = [0, 3, 5, 10, 20, 30]
    static let choices: [BCDFeelChoice] = seconds.map { BCDFeelChoice(id: String($0), title: $0 == 0 ? "Never" : "\($0) s") }

    /// A stored number, kept to what the menu offers (anything else is "never": a hand-edited file never hides the controls for good).
    static func clamp(_ value: Int?) -> Int? {
        guard let value, seconds.contains(value), value != 0 else { return nil }
        return value
    }
}

/// The colour the touch controls are drawn in (their rim and their labels). Violet is BCD's own and the default.
enum BCDControlColor: String, CaseIterable {
    case violet = "", white, blue, green, orange, red, pink

    var title: String {
        switch self {
        case .violet: return "Violet"
        case .white: return "White"
        case .blue: return "Blue"
        case .green: return "Green"
        case .orange: return "Orange"
        case .red: return "Red"
        case .pink: return "Pink"
        }
    }

    /// Red, green and blue, 0...1. nil for violet: the palette is left as it is.
    var rgb: (r: Double, g: Double, b: Double)? {
        switch self {
        case .violet: return nil
        case .white: return (0.96, 0.96, 0.98)
        case .blue: return (0.38, 0.65, 0.98)
        case .green: return (0.30, 0.85, 0.52)
        case .orange: return (1.00, 0.62, 0.20)
        case .red: return (0.97, 0.36, 0.34)
        case .pink: return (1.00, 0.50, 0.78)
        }
    }

    static var choices: [BCDFeelChoice] { allCases.map { BCDFeelChoice(id: $0.rawValue, title: $0.title) } }
}

/// What a touch that misses the controls does. It is the game's `env.MADEIRA_TOUCH_MOUSE` for the session (TouchMouseGate).
enum BCDTouchMouse: String, CaseIterable {
    /// The rule BCD always had: not while the on-screen controller is shown (unless touch works as a trackpad), yes otherwise.
    case automatic = ""
    /// A touch never moves the mouse: the controls are the only thing the fingers do.
    case never = "0"
    /// A touch always reaches the game as the mouse.
    case always = "1"

    var title: String {
        switch self {
        case .automatic: return "Automatic"
        case .never: return "Never"
        case .always: return "Always"
        }
    }

    static var choices: [BCDFeelChoice] { allCases.map { BCDFeelChoice(id: $0.rawValue, title: $0.title) } }
}

/// How the game's picture is scaled to the screen where it is not shown at its own size.
enum BCDPictureFilter: String, CaseIterable {
    /// Smooth (bilinear): what BCD has always shown.
    case smooth = ""
    /// Sharp (nearest): pixels stay pixels; best for old games drawn at a low resolution.
    case sharp

    var title: String { self == .smooth ? "Smooth" : "Sharp" }
    static var choices: [BCDFeelChoice] { allCases.map { BCDFeelChoice(id: $0.rawValue, title: $0.title) } }
}

/// How the controller opens the in-game menu. Back + Start always does, in every mode.
enum BCDMenuChord: String, CaseIterable {
    case backStart = ""
    case holdStart = "start"
    case holdGuide = "guide"

    var title: String {
        switch self {
        case .backStart: return "Back + Start"
        case .holdStart: return "Hold Start"
        case .holdGuide: return "Hold Home"
        }
    }

    static var choices: [BCDFeelChoice] { allCases.map { BCDFeelChoice(id: $0.rawValue, title: $0.title) } }
}

// MARK: - Flipping the screen

/// Which landscape the interface is in right now, by UIKit's name (the side the home edge is on).
enum BCDInterfaceSide { case landscapeLeft, landscapeRight, other }

enum BCDOrientationFlip {
    /// The screen locked to the other landscape: the interface in `.landscapeRight` has the camera on the left (BCDOrientationChoice.mask), so
    /// flipping it puts the camera on the right, and the other way round. From anything else the camera goes to the left.
    static func next(from side: BCDInterfaceSide) -> BCDOrientationChoice {
        switch side {
        case .landscapeRight: return .landscapeCameraRight
        case .landscapeLeft: return .landscapeCameraLeft
        case .other: return .landscapeCameraLeft
        }
    }
}

// MARK: - The chord that opens the menu

/// Watches player 1's buttons (XInput bits) for the long press that opens the menu in the mode the player chose. Back + Start is
/// handled where it always was (LibraryController.sample) and works in every mode. Called with the lock held, from the sampler.
struct BCDChordTracker {
    static let start: UInt16 = 0x0010
    static let back: UInt16 = 0x0020
    static let guide: UInt16 = 0x0400
    /// Start is held this long, alone, before the menu opens (a tap on Start still reaches the game untouched).
    static let startHold = 0.8

    private var since: Double?
    private var sent = false

    /// True once per hold of Start: the menu should open. Only for `holdStart`; the Guide button is timed by LibraryController itself.
    mutating func update(buttons: UInt16, now: Double, mode: BCDMenuChord) -> Bool {
        guard mode == .holdStart, buttons & Self.start != 0, buttons & ~Self.start == 0 else {
            since = nil
            sent = false
            return false
        }
        guard let began = since else { since = now; return false }
        guard !sent, now - began >= Self.startHold else { return false }
        sent = true
        return true
    }

    mutating func reset() { since = nil; sent = false }
}

/// The player's choice, read by the sampler. Main thread writes, the sampler reads a word: a torn read is impossible for one byte-sized enum.
enum BCDChordSetting {
    /// The preference's key (the app's own defaults: one choice for the device).
    static let key = "bcd.menuChord"
    nonisolated(unsafe) static var mode: BCDMenuChord = BCDMenuChord(rawValue: UserDefaults.standard.string(forKey: key) ?? "") ?? .backStart
}

// MARK: - The flags the touch drawing reads

/// Read by the touch controls' drawing and haptics (BCDTouchLayouts.swift, BCDTouchControls.swift) and set by the session. Main thread.
/// Every value is "as always" until the player changes it.
enum BCDTouchFeel {
    /// The light tick a control or a button of the touch chrome gives.
    nonisolated(unsafe) static var haptics = true
    /// The colour of the controls' rims and labels; nil is BCD's violet.
    nonisolated(unsafe) static var tint: (r: Double, g: Double, b: Double)?
    /// Bumped whenever the tint changes, so the drawn controls know to draw again.
    nonisolated(unsafe) static var tintRevision = 0
    /// The controls are hidden for want of a touch (BCDTouchFeelModel): the window's hit test gives every touch to the game then.
    nonisolated(unsafe) static var controlsHidden = false
    /// madeira-bcd (Porthole port, item 9): controller sticks float (BCDSessionFeel.floatingSticks): read by the touch layer and the
    /// controls' window. Off unless the session's value says so.
    nonisolated(unsafe) static var floatingSticks = false

    static func setTint(_ color: BCDControlColor) {
        let rgb = color.rgb
        if rgb?.r == tint?.r && rgb?.g == tint?.g && rgb?.b == tint?.b { return }
        tint = rgb
        tintRevision &+= 1
    }

    static func reset() {
        haptics = true
        controlsHidden = false
        floatingSticks = false
        setTint(.violet)
    }
}

// MARK: - A game's saved set

/// The values a game keeps for itself: the player's "Save these settings for this game". A field that is nil was never saved and the
/// session takes what it always took. Written as JSON beside the library; unknown keys are ignored and every field is optional, so an
/// older or a newer file reads.
struct BCDSessionFeel: Codable, Equatable {
    /// The light tick of the touch controls (nil: on).
    var touchHaptics: Bool?
    /// The phone shakes with the game's rumble (nil: off). Only where the game has no paired controller to rumble.
    var phoneVibration: Bool?
    /// Seconds without a finger after which the touch controls hide (nil: they stay).
    var hideAfter: Int?
    /// A BCDControlColor raw value (nil: violet).
    var controlColor: String?
    /// A BCDTouchMouse raw value (nil: automatic).
    var touchMouse: String?
    /// A BCDPictureFilter raw value (nil: smooth).
    var filter: String?
    /// A BCDOrientationChoice raw value, the screen's turn (nil: the game's own choice or Settings').
    var orientation: String?
    /// absolute, touch or relative (nil: what Settings has).
    var pointerMode: String?
    /// The touch speed of the pointer mode in force (nil: what Settings has).
    var touchSpeed: Double?
    /// Eco mode (nil: off).
    var eco: Bool?
    /// madeira-bcd (Porthole port, item 9): floating sticks (nil: off). A controller stick's ring appears where the thumb lands near it.
    var floatingSticks: Bool?

    var isEmpty: Bool { self == BCDSessionFeel() }

    static let pointerModes = ["absolute", "touch", "relative"]
    static let speedRange = 0.1...8.0

    /// The same values with everything out of range or unknown dropped (a hand-edited file, a newer build's word).
    func sanitized() -> BCDSessionFeel {
        var out = self
        out.hideAfter = BCDHideAfter.clamp(hideAfter)
        if let c = controlColor, BCDControlColor(rawValue: c) == nil || c.isEmpty { out.controlColor = nil }
        if let m = touchMouse, BCDTouchMouse(rawValue: m) == nil || m.isEmpty { out.touchMouse = nil }
        if let f = filter, BCDPictureFilter(rawValue: f) == nil || f.isEmpty { out.filter = nil }
        if let o = orientation, o.isEmpty || o == "off" { out.orientation = nil }
        if let p = pointerMode, !Self.pointerModes.contains(p) { out.pointerMode = nil }
        if let s = touchSpeed { out.touchSpeed = s.isFinite ? min(max(s, Self.speedRange.lowerBound), Self.speedRange.upperBound) : nil }
        if out.touchHaptics == true { out.touchHaptics = nil }
        if out.phoneVibration == false { out.phoneVibration = nil }
        if out.eco == false { out.eco = nil }
        if out.floatingSticks == false { out.floatingSticks = nil }
        return out
    }

    /// What the saved set holds, in the menu's words: the note under "Save these settings for this game".
    var words: [String] {
        var out: [String] = []
        if touchHaptics == false { out.append("controls without a tick") }
        if phoneVibration == true { out.append("phone vibration") }
        if let hideAfter { out.append("controls hide after \(hideAfter) s") }
        if let c = controlColor.flatMap(BCDControlColor.init(rawValue:)), c != .violet { out.append("\(c.title.lowercased()) controls") }
        if let m = touchMouse.flatMap(BCDTouchMouse.init(rawValue:)), m != .automatic { out.append("touches \(m == .never ? "never" : "always") move the mouse") }
        if filter == BCDPictureFilter.sharp.rawValue { out.append("sharp picture") }
        if let orientation, !orientation.isEmpty { out.append("screen turn") }
        if let pointerMode { out.append("pointer: \(pointerMode)") }
        if touchSpeed != nil { out.append("touch speed") }
        if eco == true { out.append("eco mode") }
        if floatingSticks == true { out.append("floating sticks") }
        return out
    }
}

/// The file the saved sets are kept in, and the key a game's set is kept under.
enum BCDSessionFeelFile {
    static let name = "bcd-session-feel.json"

    /// A game's key: the library entry's Windows path, lower case (the way GameProfile names its file), so that a game added again keeps its set.
    static func key(forWindowsPath path: String) -> String {
        path.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    static func decode(_ data: Data?) -> [String: BCDSessionFeel] {
        guard let data, let all = try? JSONDecoder().decode([String: BCDSessionFeel].self, from: data) else { return [:] }
        return all.mapValues { $0.sanitized() }.filter { !$0.value.isEmpty }
    }

    static func encode(_ all: [String: BCDSessionFeel]) -> Data? {
        let kept = all.mapValues { $0.sanitized() }.filter { !$0.value.isEmpty }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        return try? encoder.encode(kept)
    }
}
