// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// "What does this mean?" under a message BCD shows (Porthole port, item 15). Porthole links its messages to its website's documentation
// (app/Sources/Core/DocsLink.swift: a list of message pieces and the entry each one has); the idea is taken as knowledge only. BCD keeps
// the explanations in the app, in these words: nothing is opened, fetched or sent, and there is no website. A message with no entry here
// gets no button, so every message that existed before reads exactly as it did.
//
// Plain values only: tests/host/check-bcd-explain.py compiles this file alone. The library's alert (BCDLiveFrontEnd.swift) shows the button.

struct BCDExplanation: Equatable {
    /// Short, stable: the check names entries by it.
    var id: String
    var title: String
    var text: String
}

enum BCDExplain {
    /// (a piece of the message, lower case; the explanation). The first entry whose piece is in the message wins.
    static let entries: [(match: String, explanation: BCDExplanation)] = [
        ("jit has to be enabled", BCDExplanation(
            id: "jit-needed", title: "Why JIT has to be on",
            text: "Windows games are made for another kind of processor. BCD translates their code while they run, and iOS lets an app make new code only while JIT is on for it. It is turned on by BCD's built-in helper or by StikDebug, whichever you set up in Settings › JIT, once each time BCD starts. Turn it on, then tap Play again.")),
        ("jit is now enabled, but the game didn't start", BCDExplanation(
            id: "jit-then-play", title: "JIT is on, the game did not start",
            text: "Turning JIT on and starting the game are two steps, and the start was missed while JIT was being turned on. Nothing is wrong: tap Play once more.")),
        ("another session is still running", BCDExplanation(
            id: "one-session", title: "One game at a time",
            text: "BCD runs one Windows session at a time, and the last one has not finished closing. Wait a moment and try again; if it keeps saying this, close BCD from the app switcher and open it again.")),
        ("windows is still running", BCDExplanation(
            id: "desktop-running", title: "The Windows desktop is still open",
            text: "The Windows desktop is a session of its own, and only one session runs at a time. Go back to it from Home with Show Windows and close it, or use Stop, before you start a game.")),
        ("increased memory limit", BCDExplanation(
            id: "memory-limit", title: "The increased memory limit",
            text: "iOS gives every app a memory limit. An app signed with the increased memory limit may use much more, and most Windows games need it. A free Apple account does not add it by itself: GetMoreRam can add it with SideStore or LiveContainer, and a paid developer account adds it when you sign BCD. Without it iOS closes larger games early, whatever their settings.")),
        ("external drive is not connected", BCDExplanation(
            id: "drive-missing", title: "The game is on an external drive",
            text: "This game was installed on an external drive, and its files are only there. Plug the drive in, wait until Files shows it, and try again.")),
        ("steam no longer accepts your sign-in", BCDExplanation(
            id: "steam-signin", title: "Steam's sign-in ran out",
            text: "Steam's saved sign-in on this device is no longer valid: Steam ends saved sign-ins after a while, after a password change, or when you sign out everywhere. Sign out of Steam in Settings and sign in again; your installed games stay.")),
        ("not enough free space", BCDExplanation(
            id: "free-space", title: "Not enough room on the device",
            text: "A download needs room for the game's files and a little more while they are checked. Delete something you do not need (a game you no longer play, photos or videos), then try again. Files already downloaded are kept.")),
        ("longer than bcd allows", BCDExplanation(
            id: "command-line", title: "The start line is too long",
            text: "Windows limits how long a program's path and its launch arguments may be together. Shorten the arguments in Game details, or move the game to a folder with a shorter path.")),
    ]

    /// The explanation for a message, or nil when it has none.
    static func explanation(for message: String?) -> BCDExplanation? {
        guard let message, !message.isEmpty else { return nil }
        let lower = message.lowercased()
        return entries.first { lower.contains($0.match) }?.explanation
    }

    static let buttonTitle = "What does this mean?"
}
