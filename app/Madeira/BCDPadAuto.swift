// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. The two rules in this file (BCDPadAuto and BCDLayoutHint) were taken from
// https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Core/PadAuto.swift and LayoutSuggestion.swift) under
// GPL-3.0-or-later with the Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: BCD's names, key and words; the
// hint is a line over the game instead of a card. See THIRD-PARTY-NOTICES.md.

import Foundation

// A game that never asks for a controller (an experiment; a game's own line `pad-auto = 1`, off by default). Plain values: the two
// rules and the words. BCDPadAutoLive.swift reads the engine's count of controller reads and switches the mode; nothing here does, so
// the preview build and tests/host/bcd_pad_auto_checks.swift use this file as it is.

/// Decides, while a game runs, whether the physical controller should reach it as an Xbox pad or as keyboard and mouse.
///
/// A game that reads XInput (or the HID pad) asks for the controller over and over, even with none connected: the engine counts those
/// reads. A game that never asks has no controller support, and an Xbox pad it never looks at does nothing, so after a grace period
/// the pad is turned into keys and mouse. If the game starts reading later (a long load, a menu that enables it) the pad goes back to
/// being an Xbox pad at once. Nothing here applies when the player chose a mode for the game.
struct BCDPadAuto: Equatable {
    enum Decision: Equatable { case none, useKeys, useXInput }

    /// How long a running game gets to start reading the controller before the keyboard-and-mouse mode takes over.
    var grace: TimeInterval = 25
    private(set) var usingKeys = false
    private var startedAt: TimeInterval = 0
    private var baseline: UInt64 = 0

    /// `preferKeys`: the last run of this game never read the controller, so start in keyboard-and-mouse mode and let the reads switch it back.
    mutating func start(now: TimeInterval, reads: UInt64, preferKeys: Bool) {
        startedAt = now; baseline = reads; usingKeys = preferKeys
    }

    mutating func update(now: TimeInterval, reads: UInt64, padConnected: Bool) -> Decision {
        if usingKeys {
            guard reads > baseline else { return .none }
            usingKeys = false; baseline = reads; startedAt = .infinity      // it reads the controller: it is not switched again this run
            return .useXInput
        }
        if reads > baseline { baseline = reads; startedAt = .infinity; return .none }      // the game reads the controller: it is fine as it is
        guard startedAt.isFinite, padConnected, now - startedAt >= grace else { return .none }
        usingKeys = true; baseline = reads
        return .useKeys
    }

    /// Whether the game has been seen reading the controller during this run.
    var gameReads: Bool { !startedAt.isFinite }

    // MARK: BCD's side

    /// A game's own line: 1 = on. No line, the default: the controller is what the player chose, as always.
    static let key = "pad-auto"
    static func on(_ value: String?) -> Bool { value?.trimmingCharacters(in: .whitespaces) == "1" }

    static let title = "Controller as keys when the game has no controller support (experimental)"
    static let note = "On: when this game has not asked for a controller 25 seconds after it started, your controller works as keyboard and mouse (the mode of the same name, with its bindings), and goes back to being a controller the moment the game asks for one. It only applies while Controller mode is left at its standard choice. With the on-screen controller showing and no controller connected, a line suggests the keyboard layout once."

    static let switchedToKeys = "This game is not asking for a controller: yours now works as keyboard and mouse."
    static let switchedBack = "The game is asking for a controller now: yours is a controller again."

    /// The games whose last run never read the controller (by their Windows path), newest last; at most 300 are kept.
    static let rememberedKey = "bcd.padAuto.noReads"
    static func remembered(_ path: String, in list: [String]) -> Bool { list.contains(path.lowercased()) }
    static func remember(_ path: String, noReads: Bool, in list: [String]) -> [String] {
        var out = list.filter { $0 != path.lowercased() }
        if noReads { out.append(path.lowercased()) }
        if out.count > 300 { out.removeFirst(out.count - 300) }
        return out
    }
}

/// A game that never asks for a controller while the on-screen game controller is on has no use for it: after a few seconds BCD says
/// so, once per game, in the line over the game. Nothing is changed: the keyboard layout is chosen in the in-game menu as always.
struct BCDLayoutHint: Equatable {
    /// How long a running game gets to read the controller before the line is shown.
    var grace: TimeInterval = 20
    private var startedAt: TimeInterval = 0
    private var baseline: UInt64 = 0
    private var finished = false

    /// A game's session began. `reads` is the engine's count of controller reads so far.
    mutating func start(now: TimeInterval, reads: UInt64) {
        startedAt = now; baseline = reads; finished = false
    }

    /// About once a second. `eligible`: the on-screen game controller is on, no real controller is connected, nothing (the menu, the
    /// editor) is in the way and the game was not told before. Returns true once, when the line should be shown. A game that reads the
    /// controller at any time ends the matter for this run.
    mutating func update(now: TimeInterval, reads: UInt64, eligible: Bool) -> Bool {
        guard !finished else { return false }
        if reads > baseline { finished = true; return false }          // it reads the controller: the pad is of use
        // The clock only runs while the line could be shown: turning the on-screen controller on, or closing the menu, starts the
        // grace period again, so the line never appears at once.
        guard eligible else { startedAt = now; return false }
        guard now - startedAt >= grace else { return false }
        finished = true
        return true
    }

    /// The games that were told (by their Windows path), newest last; at most 300 are kept.
    static let toldKey = "bcd.padAuto.layoutHint"
    static func mayTell(_ path: String, told: [String]) -> Bool { !told.contains(path.lowercased()) }
    static func remember(_ path: String, told: [String]) -> [String] {
        guard !told.contains(path.lowercased()) else { return told }
        var out = told + [path.lowercased()]
        if out.count > 300 { out.removeFirst(out.count - 300) }
        return out
    }

    static let text = "This game is not asking for a controller, so the on-screen controller does nothing here. The keyboard layout is in the menu, under Controls."
}
