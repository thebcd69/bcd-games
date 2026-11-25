// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// One game's round 604 input choices as plain values (BCDInputPrefs.swift keeps them, BCDInputLive.swift reads and
// applies them), and the routing of a setting of the game's page or the in-game menu to the line that keeps it.
// Foundation only.

/// The raw lines of one game, by feature, and what each feature makes of them.
struct BCDInputProfile: Equatable {
    /// feature -> line -> value.
    var lines: [String: [String: String]] = [:]

    func get(_ feature: String, _ line: String) -> String? { lines[feature]?[line] }

    var turbo: BCDTurboSettings { .parse { get(BCDInputLines.turbo, $0) } }
    var macros: BCDMacroSettings { .parse { get(BCDInputLines.turbo, $0) } }
    var touchLook: BCDTouchLook { .parse { get(BCDInputLines.touchLook, $0) } }
    var players: BCDPlayerSettings { .parse { get(BCDInputLines.players, $0) } }
    var radial: BCDRadialSettings { .parse { get(BCDInputLines.radial, $0) } }
    var aim: BCDAimSettings { .parse { get(BCDInputLines.aim, $0) } }
    var gestures: BCDGestureSettings { .parse { get(BCDInputLines.gestures, $0) } }
    var pencil: BCDPencilSettings { .parse { get(BCDInputLines.pencil, $0) } }

    /// Whether any feature is on for this game (the session hooks have work).
    var anyOn: Bool { turbo.isOn || macros.isOn || radial.isOn || aim.isOn || gestures.isOn || pencil.isOn }
}

enum BCDInputLines {
    static let turbo = "turbo"
    static let touchLook = "touchlook"
    static let players = "players"
    static let radial = "radial"
    static let aim = "aim"
    static let gestures = "gestures"
    static let pencil = "pencil"

    /// Every feature that keeps lines, in the order the profile loads them.
    static let features = [turbo, touchLook, players, radial, aim, gestures, pencil]

    /// The features whose choices are the same for every game (kept under BCDInputPrefs.everyone).
    static let global: Set<String> = [touchLook, players]

    /// The feature that keeps a line, nil for a line of any other setting.
    static func feature(of line: String) -> String? {
        if BCDTurboSettings.lineNames.contains(line) || BCDMacroSettings.lineNames.contains(line) { return turbo }
        if BCDTouchLook.lineNames.contains(line) { return touchLook }
        if BCDPlayerSettings.lineNames.contains(line) { return players }
        if BCDRadialSettings.lineNames.contains(line) { return radial }
        if BCDAimSettings.lineNames.contains(line) { return aim }
        if BCDGestureSettings.lineNames.contains(line) { return gestures }
        if BCDPencilSettings.lineNames.contains(line) { return pencil }
        return nil
    }

    /// The prefix of a game page setting that switches one name of the `turbo` line ("turbo:a", "turbo:mouse-left").
    static let turboToggle = "turbo:"

    /// The lines that are a switch: off is no line.
    static let toggles: Set<String> = ["radial", "aim-invert", "aim-tap", "aim-accel", "pencil"]

    /// Whether a setting of the game's page is one of this round's (checked before anything is looked up).
    static func handles(_ setting: String) -> Bool { setting.hasPrefix(turboToggle) || feature(of: setting) != nil }

    /// What a setting of the game's page (or a row of the in-game menu) writes: the feature, the line and its new value, given the
    /// game's lines as they are. Nil when the setting is none of this round's.
    static func write(setting: String, value: String, current: (_ feature: String, _ line: String) -> String?) -> (feature: String, line: String, value: String)? {
        if setting.hasPrefix(turboToggle) {
            let name = String(setting.dropFirst(turboToggle.count))
            let line = BCDTurboSettings.toggled(current(turbo, "turbo") ?? "", name: name, on: value == "1")
            return (turbo, "turbo", line)
        }
        if let feature = feature(of: setting) {
            // A choice that is what happens with no line is not kept: full rumble, the touch controls as Player 1.
            let plain = (setting.hasSuffix("-rumble") && value == "100") || (setting == "touch-player" && value == "1")
                || (toggles.contains(setting) && value == "0")
            return (feature, setting, plain ? "" : value)
        }
        return nil
    }
}
