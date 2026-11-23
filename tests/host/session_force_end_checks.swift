// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// "End the game now" (SessionForceEnd.swift), compiled as main.swift with it by tests/host/check-session-force-end.py.

import Foundation

var failures = 0
var passed = 0
func check(_ ok: Bool, _ what: String, _ detail: @autoclosure () -> String = "", line: Int = #line) {
    if ok { passed += 1; print("ok   " + what) } else { failures += 1; print("FAIL " + what + " (line \(line))" + (detail().isEmpty ? "" : "\n     " + detail())) }
}

typealias F = SessionForceEnd

check(F.offerAfter == 45 && F.recoverAfter == 10, "45 s after Close it is offered; 10 s after the player chose it, the session still up is said")
check(F.phase(sinceClose: nil, sinceEnd: nil, sessionUp: true) == .none, "no Close, nothing offered")
check(F.phase(sinceClose: 0, sinceEnd: nil, sessionUp: true) == .none && F.phase(sinceClose: 44.99, sinceEnd: nil, sessionUp: true) == .none,
      "less than 45 s after Close, nothing is offered (a game that is saving is left alone)")
check(F.phase(sinceClose: 45, sinceEnd: nil, sessionUp: true) == .offered && F.phase(sinceClose: 600, sinceEnd: nil, sessionUp: true) == .offered,
      "from 45 s on, End the game now is offered, and stays offered")
check(F.phase(sinceClose: 50, sinceEnd: 0, sessionUp: true) == .ending && F.phase(sinceClose: 59, sinceEnd: 9.99, sessionUp: true) == .ending,
      "the player chose it: the engine is ending the session")
check(F.phase(sinceClose: 60, sinceEnd: 10, sessionUp: true) == .stuck && F.phase(sinceClose: 900, sinceEnd: 800, sessionUp: true) == .stuck,
      "10 s after, with the session still up: only closing BCD is left")
var overOnce = true
for close in [nil, 0, 45, 1000] as [Double?] { for end in [nil, 0, 20] as [Double?] where F.phase(sinceClose: close, sinceEnd: end, sessionUp: false) != .none { overOnce = false } }
check(overOnce, "a session that is over offers and says nothing, in every state")
check(F.phase(sinceClose: nil, sinceEnd: 20, sessionUp: true) == .none, "choosing it without having asked to close is no state (nothing is offered)")
// A tick-by-tick run, as the library's poll walks it (every 0.5 s): the phases come once each and in order.
var seen: [F.Phase] = []
var chosenAt: Double?
var t = 0.0
while t <= 80 {
    let phase = F.phase(sinceClose: t, sinceEnd: chosenAt.map { t - $0 }, sessionUp: true)
    if phase != seen.last { seen.append(phase) }
    if phase == .offered, t >= 52, chosenAt == nil { chosenAt = t }   // the player takes a few seconds to choose
    t += 0.5
}
check(seen == [.none, .offered, .ending, .stuck], "a run of 80 s: nothing, offered at 45, ending when chosen, stuck 10 s later", "\(seen)")
check(F.applies(desktop: false, launching: false) && !F.applies(desktop: true, launching: false) && !F.applies(desktop: false, launching: true)
      && !F.applies(desktop: true, launching: true), "only a game that is showing: not the Windows desktop, not a start under way")

check(F.offerWords == "The game did not close. Open the menu and choose End the game now. Unsaved progress is lost.", "the session line says what to do", F.offerWords)
check(F.menuTitle == "End the game now" && F.answerTitle == F.menuTitle && F.confirmTitle == "End the game now?" && F.endingWords == "Ending the game…",
      "the menu's row, its question and its answer are called End the game now")
check(F.stuckWords == "The game could not be ended. Restart BCD to recover.", "the last words are the Windows desktop's own wording")
check(F.confirmMessage.contains("without waiting") && F.confirmMessage.contains("haven't saved") && F.menuDetail.contains("haven't saved"), "both say that what is not saved is lost")
check(F.logLine(secondsAfterClose: 47, running: 0, names: "") == "[force-end] player ended the game 47 s after Close: no process of its own was left"
      && F.logLine(secondsAfterClose: 61, running: 2, names: "hollow_knight.exe steam.exe") == "[force-end] player ended the game 61 s after Close: 2 process(es) still run (hollow_knight.exe steam.exe)",
      "the log line says how long after Close and what was still running")
let words = [F.offerWords, F.endingWords, F.stuckWords, F.menuTitle, F.menuDetail, F.confirmTitle, F.confirmMessage, F.answerTitle]
check(!words.contains { $0.localizedCaseInsensitiveContains("madeira") || $0.contains("@") || $0.contains("http") }, "no old name, no address, no person in any of the words")

print("session force end: \(passed) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
