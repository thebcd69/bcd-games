// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  AutoTuneAsk.swift
//  Madeira
//
//  madeira-bcd (Porthole port, item 10): auto-tune's "Ask before trying a fix" (Settings › Logs › After a crash; off by default).
//  The idea is Porthole's (app/Sources/Core/AutoFix.swift and AutoFixStore.swift: a suggested change waits for Apply, can be taken
//  back, and the next run judges it), taken as knowledge only; this is BCD's own code on top of BCD's search (docs/AUTO-TUNE.md).
//
//  Off, nothing here is read beyond the one switch: every start gets auto-tune's lines as always and every result reads as always.
//  On, auto-tune still reads each failed session and still picks the fix it would try, but a fix the game does not have yet waits for
//  the player's yes:
//    - the result says what it would try and asks: Try it next start, Not now, or Stop for this game;
//    - a start before the yes runs with what auto-tune kept so far and without the waiting lines (the session's log then lacks them, so
//      auto-tune counts it for nothing and the search stays where it was: AutoTune's "this attempt is not counted");
//    - after Not now the same question is not asked again after every start; the game's page keeps the suggestion (Auto-tune › Try
//      the suggested fix) until the search moves on;
//    - once lines were kept, the result offers Undo, which is the page's Undo all (the lines and the history go).
//  A retry with nothing changed ("the same settings once more") needs no yes: it changes nothing.
//

import Foundation

enum AutoTuneAsk {
    /// Settings › Logs › After a crash › Ask before trying a fix. Absent: off.
    static let key = "madeira.autotune.ask"
    static var on: Bool { UserDefaults.standard.bool(forKey: key) }

    /// The candidate a game's player said yes to, and the one they put off, by the game's Windows path (lower case).
    private static let approvedKey = "madeira.autotune.ask.approved"
    private static let declinedKey = "madeira.autotune.ask.declined"

    private static func stored(_ key: String, _ path: String) -> [String]? {
        (UserDefaults.standard.dictionary(forKey: key) as? [String: [String]])?[path.lowercased()]
    }
    private static func store(_ key: String, _ path: String, _ lines: [String]?) {
        var all = (UserDefaults.standard.dictionary(forKey: key) as? [String: [String]]) ?? [:]
        if lines == nil && all[path.lowercased()] == nil { return }   // nothing to forget: nothing is written
        all[path.lowercased()] = lines
        UserDefaults.standard.set(all, forKey: key)
    }

    /// The lines of the fix that waits for the player's yes: the next candidate's lines the game does not have yet, while the switch is on
    /// and the yes was not given to exactly that candidate. Empty: nothing waits (the switch is off, the next start tries nothing new, or
    /// the player said yes).
    static func waiting(_ state: AutoTuneState, _ windowsPath: String) -> [String] {
        guard on, let next = state.candidate, !next.isEmpty else { return [] }
        let new = next.filter { !state.kept.contains($0) }
        guard !new.isEmpty, stored(approvedKey, windowsPath) != next else { return [] }
        return new
    }

    /// The lines a start gets from auto-tune: its layer as always, or, while a fix waits, only what it kept.
    static func layer(_ state: AutoTuneState, _ windowsPath: String) -> [String] {
        waiting(state, windowsPath).isEmpty ? state.layerLines : AutoTuneState.merge(state.kept, [])
    }

    /// Try it: the next start gets the waiting fix.
    static func approve(_ state: AutoTuneState, _ windowsPath: String) {
        store(approvedKey, windowsPath, state.candidate)
        store(declinedKey, windowsPath, nil)
        LogStore.shared.log("[autotune] the player said yes to \((state.candidate ?? []).joined(separator: "; ")) (Ask before trying a fix)")
    }

    /// Not now: the next starts run without it, and the result does not ask again for the same fix.
    static func decline(_ state: AutoTuneState, _ windowsPath: String) {
        store(declinedKey, windowsPath, state.candidate)
        LogStore.shared.log("[autotune] the player put off \((state.candidate ?? []).joined(separator: "; ")) (Ask before trying a fix)")
    }

    /// Undo, or Undo all on the game's page: the answers go with the lines.
    static func forget(_ windowsPath: String) {
        store(approvedKey, windowsPath, nil)
        store(declinedKey, windowsPath, nil)
    }

    /// The result asks Try it / Not now: a fix waits for this game.
    static func asks(_ offer: AutoTuneOffer) -> Bool { !offer.ask && !waiting(offer.state, offer.windowsPath).isEmpty }

    /// A result with nothing new to say: the player put this very fix off, and it still waits. It is not shown (the page has it).
    static func quiet(_ offer: AutoTuneOffer) -> Bool {
        asks(offer) && stored(declinedKey, offer.windowsPath) == offer.state.candidate
    }

    /// The result offers Undo: the switch is on and the game plays with lines auto-tune kept.
    static func offersUndo(_ offer: AutoTuneOffer) -> Bool { on && !offer.ask && offer.state.status == "found" && !offer.state.kept.isEmpty }

    /// What the result says while a fix waits.
    static func message(_ offer: AutoTuneOffer) -> String {
        offer.message.replacingOccurrences(of: "The next start tries: ", with: "Auto-tune would try: ")
            .replacingOccurrences(of: " Restart BCD and play again.", with: "")
            + "\n\nNothing changes until you say so. Try it next start adds it to the next start (restart BCD first); Not now keeps the game as it is, and its page keeps the suggestion."
    }

    /// The game page's words for a waiting fix.
    static func pageStatus(_ lines: [String]) -> String {
        "Auto-tune suggests trying \(lines.joined(separator: "; ")) at the next start. Nothing changes until you choose Try the suggested fix."
    }
}
