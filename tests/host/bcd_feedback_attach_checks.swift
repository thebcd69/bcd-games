// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// Settings > Feedback > "Attach my last game's log" (BCDFeedbackAttach.swift), compiled as main.swift with it by
// tests/host/check-feedback-attach.py.

import Foundation

var failures = 0
var passed = 0
func check(_ ok: Bool, _ what: String, _ detail: @autoclosure () -> String = "", line: Int = #line) {
    if ok { passed += 1; print("ok   " + what) } else { failures += 1; print("FAIL " + what + " (line \(line))" + (detail().isEmpty ? "" : "\n     " + detail())) }
}

typealias A = BCDFeedbackAttach

// The stored choice, in a defaults database of its own.
let suite = "bcd-feedback-attach-test-\(UUID().uuidString)"
let defaults = UserDefaults(suiteName: suite)!
defer { defaults.removePersistentDomain(forName: suite) }
check(A.bugChoice(defaults) == true && defaults.object(forKey: A.key) == nil, "an install that never touched it: bug reports attach the log, and nothing is stored")
A.setBugChoice(false, defaults)
check(A.bugChoice(defaults) == false && defaults.object(forKey: A.key) as? Bool == false, "turned off: bug reports go without it, and that is kept")
A.setBugChoice(true, defaults)
check(A.bugChoice(defaults) == true && defaults.object(forKey: A.key) == nil, "turned back on: the key goes again (the default holds nothing)")
A.setBugChoice(false, defaults)
let again = UserDefaults(suiteName: suite)!
check(A.bugChoice(again) == false, "the choice survives a new reader of the same defaults (the next launch)")
check(A.key == "bcd.feedback.attachLogToBugs", "the key")

// Which messages carry the log.
var table = true
for isBug in [true, false] {
    for bugChoice in [true, false] {
        for otherChoice in [true, false] {
            for hasLog in [true, false] {
                let expected = hasLog && (isBug ? bugChoice : otherChoice)
                if A.attaches(isBug: isBug, bugChoice: bugChoice, otherChoice: otherChoice, hasLog: hasLog) != expected { table = false }
            }
        }
    }
}
check(table, "a bug report follows its stored choice, any other message the switch the person turned for it, and nothing goes without a log")
check(A.attaches(isBug: true, bugChoice: true, otherChoice: false, hasLog: true), "by default a bug report carries the log")
check(!A.attaches(isBug: false, bugChoice: true, otherChoice: false, hasLog: true), "by default an idea or another message does not: nothing about those kinds changed")
check(!A.attaches(isBug: true, bugChoice: true, otherChoice: true, hasLog: false), "no log, no attachment, whatever the choices")

// The words.
check(A.game(of: "hollow_knight.exe-2026-11-21_20-14-05.txt") == "hollow_knight.exe" && A.game(of: "war3.exe-2026-10-19_09-12-40.txt") == "war3.exe"
      && A.game(of: "bcd-log.txt") == "bcd-log" && A.game(of: "weird") == "weird" && A.game(of: "a-b.exe-2026-01-02_03-04-05.txt") == "a-b.exe",
      "the game a log is named after")
check(A.isAppLog("bcd-log.txt") && A.isAppLog("bcd-log.prev.txt") && !A.isAppLog("war3.exe-2026-10-19_09-12-40.txt"), "BCD's own two logs are told apart")
let game = A.Shown(name: "hollow_knight.exe-2026-11-21_20-14-05.txt", when: "21 Nov, 20:14", size: "3.2 MB", hasGame: true)
let own = A.Shown(name: "bcd-log.txt", when: "22 Nov, 09:00", size: "120 KB", hasGame: false)
check(A.detail(game) == "Game: hollow_knight.exe · 21 Nov, 20:14 · 3.2 MB", "the row names the game, the day and the size", A.detail(game))
check(A.detail(own) == "BCD's own log · 22 Nov, 09:00 · 120 KB · no game ran in it", "BCD's own log says no game ran in it", A.detail(own))
let notes = [A.note(isBug: true, on: true, log: game), A.note(isBug: true, on: false, log: game), A.note(isBug: false, on: true, log: game),
             A.note(isBug: false, on: false, log: game)]
check(Set(notes).count == 4 && notes.allSatisfy { $0.hasPrefix("Game: hollow_knight.exe · 21 Nov, 20:14 · 3.2 MB.") }, "four states, four notes, each names what would be attached", "\(notes)")
check(notes[0].contains("every bug report") && notes[0].contains("Turn this off") && notes[0].contains("cleaned on this device"), "a bug report with the switch on says it goes with every one, how to turn it off, and that it is cleaned")
check(notes[1].contains("Bug reports go without it") && notes[2].contains("goes with this message") && notes[3].contains("Bug reports carry it unless you turn that off"),
      "off for bugs, on for another message, off for another message: each says what happens")
check(A.note(isBug: true, on: true, log: nil) == "No log yet. One appears once a game has run." && A.note(isBug: false, on: false, log: nil) == A.note(isBug: true, on: false, log: nil),
      "no log: one plain sentence")
check(A.title == "Attach my last game's log", "the row's title")
check(A.logFailed("Offline.") == "Your log was not sent: Offline. Your message is still here. Turn off \u{201C}Attach my last game's log\u{201D} to send it alone.",
      "when the log cannot go, the words stay and the row to turn off is named by its own title")
check(A.privacyWithLog.contains("user names") && A.privacyWithLog.contains("e-mail addresses") && A.privacyWithLog.contains("IP addresses") && A.privacyWithLog.contains("on this device"),
      "the privacy line for the log says what is taken out and where")
let all = notes + [A.title, A.privacyWithLog, A.detail(own), A.logFailed("x")]
check(!all.contains { $0.localizedCaseInsensitiveContains("madeira") || $0.contains("@") || $0.contains("http") }, "no old name, no address, no person in any of the words")

print("feedback attach: \(passed) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
