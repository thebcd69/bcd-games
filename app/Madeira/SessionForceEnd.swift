// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  SessionForceEnd.swift
//  Madeira
//
//  A game that ignores Close (round 707, G1-S15: Hollow Knight, DEQCHZ; 31 of 330 archive logs with a close request show no return
//  to the library a minute later): the player pressed Quit game, BCD posted Alt+F4, and the game never ended, so the only way out
//  was to kill BCD. The rules below say when BCD offers "End the game now" and what it says. Foundation only and pure
//  (tests/host/check-session-force-end.py): what the library knows goes in, a phase comes out. LibraryModel (Library.swift) keeps the clocks,
//  logs, and ends the session the way the app already ends one that nothing of the game is left in (wine_session_dock_game_quit:
//  the player's quit, not a crash); the in-game menu (BCDSessionLive.swift) shows the row.
//
//  Only a game's session that is showing, after a Close that did not end it in `offerAfter` seconds. A game that closes by itself, a
//  session that has not asked to close, the Windows desktop (whose Close ends it its own way) and a start that is still under way
//  are never touched.

import Foundation

enum SessionForceEnd {
    /// Seconds after the player's Close before "End the game now" is offered.
    static let offerAfter: TimeInterval = 45
    /// Seconds after the player chose to end it before the session still being up is said, with the way out that is left.
    static let recoverAfter: TimeInterval = 10

    enum Phase: Equatable {
        /// Nothing to offer: no Close yet, not 45 s yet, or the session is over.
        case none
        /// The game did not close: the menu offers "End the game now".
        case offered
        /// The player chose it; the engine is ending the session.
        case ending
        /// The player chose it and the session is still up after `recoverAfter` seconds: only closing BCD is left.
        case stuck
    }

    /// - sinceClose: seconds since the player's first Close of this session; nil when the player has not asked to close.
    /// - sinceEnd: seconds since the player chose "End the game now"; nil before.
    /// - sessionUp: the engine's session is still running.
    static func phase(sinceClose: TimeInterval?, sinceEnd: TimeInterval?, sessionUp: Bool) -> Phase {
        guard sessionUp, let sinceClose, sinceClose >= offerAfter else { return .none }
        guard let sinceEnd else { return .offered }
        return sinceEnd >= recoverAfter ? .stuck : .ending
    }

    /// Whether a session may ever be offered this: a game that is showing (not the Windows desktop, not a start under way).
    static func applies(desktop: Bool, launching: Bool) -> Bool { !desktop && !launching }

    // MARK: Words

    /// The session line once it is offered (over the game, at the top).
    static let offerWords = "The game did not close. Open the menu and choose End the game now. Unsaved progress is lost."
    static let endingWords = "Ending the game…"
    /// The wording the Windows desktop's close error already uses (Library.swift desktopCloseError).
    static let stuckWords = "The game could not be ended. Restart BCD to recover."

    /// The menu's row, in place of Quit game while `phase` is `.offered`.
    static let menuTitle = "End the game now"
    static let menuDetail = "The game did not close when asked. Progress you haven't saved is lost."
    static let confirmTitle = "End the game now?"
    static let confirmMessage = "BCD stops the game without waiting for it. What you haven't saved is lost, and the game gets no chance to ask you anything."
    static let answerTitle = "End the game now"

    /// The line the log gets when the player ends it: how long after Close, and what of the game was still running.
    static func logLine(secondsAfterClose: Int, running: Int, names: String) -> String {
        "[force-end] player ended the game \(secondsAfterClose) s after Close: "
            + (running > 0 ? "\(running) process(es) still run (\(names))" : "no process of its own was left")
    }
}
