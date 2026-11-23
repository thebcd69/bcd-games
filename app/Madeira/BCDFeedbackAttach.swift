// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDFeedbackAttach.swift
//  Madeira
//
//  Settings › Feedback: whether the log of the last game goes with a message, and the words that say so (round 707, a player's
//  bug reports that could not be matched to a session). Foundation only: tests/host/check-feedback-attach.py compiles it with
//  tests/host/bcd_feedback_attach_checks.swift.
//
//  A bug report carries the newest game log unless the person turns that off, and the choice is kept: it is the option, on
//  until it is turned off. The log is the one Settings › Logs would send, sent first through the existing upload (LogUpload.send,
//  scrubbed on the device) and its code goes into the message (FeedbackUpload.send, the `log` field the intake already takes).
//  An idea or another message carries a log only when the person turns the switch on for that message, as before: nothing
//  about those kinds changed. The row says which log goes (the game, the day, the size), so the person always sees what is attached.

import Foundation

enum BCDFeedbackAttach {
    /// UserDefaults: the bug reports' choice. Absent means on; turning it back on removes the key, so an install that never
    /// touched it holds nothing.
    static let key = "bcd.feedback.attachLogToBugs"
    static let title = "Attach my last game's log"

    static func bugChoice(_ defaults: UserDefaults = .standard) -> Bool {
        defaults.object(forKey: key) == nil ? true : defaults.bool(forKey: key)
    }

    static func setBugChoice(_ on: Bool, _ defaults: UserDefaults = .standard) {
        if on { defaults.removeObject(forKey: key) } else { defaults.set(false, forKey: key) }
    }

    /// Whether a message of this kind sends the log: a bug report by its stored choice, any other message by the switch the person
    /// turned for it; never when there is no log.
    static func attaches(isBug: Bool, bugChoice: Bool, otherChoice: Bool, hasLog: Bool) -> Bool {
        guard hasLog else { return false }
        return isBug ? bugChoice : otherChoice
    }

    // MARK: What the row shows

    /// The log the row is about, as the person reads it.
    struct Shown: Equatable {
        /// The log's file name: "hollow_knight.exe-2026-11-21_20-14-05.txt", "bcd-log.txt".
        var name: String
        /// "21 Nov, 20:14".
        var when: String
        /// "3.2 MB".
        var size: String
        /// false: BCD's own start-up log, no game ran in it (BCDFeedbackLog.hasGame).
        var hasGame: Bool
    }

    /// The program a session log is named after ("hollow_knight.exe" from "hollow_knight.exe-2026-11-21_20-14-05.txt"): LogUpload.game(of:).
    static func game(of name: String) -> String {
        var base = name
        if base.hasSuffix(".txt") { base.removeLast(4) }
        if let range = base.range(of: #"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"#, options: .regularExpression) {
            return String(base[..<range.lowerBound])
        }
        return base
    }

    static func isAppLog(_ name: String) -> Bool { name == "bcd-log.txt" || name == "bcd-log.prev.txt" }

    /// "Game: hollow_knight.exe · 21 Nov, 20:14 · 3.2 MB", or BCD's own log, with "no game ran in it" when that is so.
    static func detail(_ log: Shown) -> String {
        let what = isAppLog(log.name) ? "BCD's own log" : "Game: \(game(of: log.name))"
        return [what, log.when, log.size].joined(separator: " · ") + (log.hasGame ? "" : " · no game ran in it")
    }

    /// The words under the switch.
    static func note(isBug: Bool, on: Bool, log: Shown?) -> String {
        guard let log else { return "No log yet. One appears once a game has run." }
        let detail = detail(log)
        switch (isBug, on) {
        case (true, true):
            return "\(detail). It goes with every bug report, cleaned on this device first as in Logs (a performance trace, if you recorded one, goes along). Turn this off to send the words alone."
        case (true, false):
            return "\(detail). Bug reports go without it. Turn this on to send it with them."
        case (false, true):
            return "\(detail). It goes with this message, cleaned on this device first as in Logs (a performance trace, if you recorded one, goes along)."
        case (false, false):
            return "\(detail). Turn this on to send it with this message. Bug reports carry it unless you turn that off."
        }
    }

    /// The failure when the log could not go: the words stay, and the switch that sends them alone is named.
    static func logFailed(_ why: String) -> String {
        "Your log was not sent: \(why) Your message is still here. Turn off \u{201C}\(title)\u{201D} to send it alone."
    }

    /// The line under the privacy line while a log goes along.
    static let privacyWithLog = "The log goes too, with user names, e-mail addresses, IP addresses and account IDs taken out on this device."
}
