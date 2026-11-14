// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-pad-auto.py for app/Madeira/BCDPadAuto.swift (compiled with it; it names no other type).

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
}

// the key
check("the key: only 1 is on", BCDPadAuto.on("1") && BCDPadAuto.on(" 1 ") && !BCDPadAuto.on(nil) && !BCDPadAuto.on("") && !BCDPadAuto.on("0") && !BCDPadAuto.on("on"))

// the controller rule
var a = BCDPadAuto()
a.start(now: 100, reads: 0, preferKeys: false)
check("a game that never asks: nothing for 24 seconds", (101...124).allSatisfy { a.update(now: Double($0), reads: 0, padConnected: true) == .none } && !a.usingKeys)
check("at 25 seconds the controller becomes keys, once", a.update(now: 125, reads: 0, padConnected: true) == .useKeys && a.usingKeys
      && a.update(now: 126, reads: 0, padConnected: true) == .none && a.update(now: 500, reads: 0, padConnected: true) == .none)
check("the game asks later: a controller again at once, and never switched again this run",
      a.update(now: 501, reads: 3, padConnected: true) == .useXInput && !a.usingKeys && a.gameReads
      && (502...700).allSatisfy { a.update(now: Double($0), reads: 3, padConnected: true) == .none })
var b = BCDPadAuto()
b.start(now: 0, reads: 40, preferKeys: false)
check("reads from before the start do not count; a game that asks within the grace period is left alone for the whole run",
      b.update(now: 10, reads: 40, padConnected: true) == .none && b.update(now: 20, reads: 41, padConnected: true) == .none && b.gameReads
      && (21...200).allSatisfy { b.update(now: Double($0), reads: 41, padConnected: true) == .none } && !b.usingKeys)
var c = BCDPadAuto()
c.start(now: 0, reads: 0, preferKeys: false)
check("without a controller connected nothing is switched, however long; with one, the time already passed counts",
      (1...300).allSatisfy { c.update(now: Double($0), reads: 0, padConnected: false) == .none } && c.update(now: 301, reads: 0, padConnected: true) == .useKeys)
var d = BCDPadAuto()
d.start(now: 0, reads: 7, preferKeys: true)
check("a game whose last run never asked starts as keys, and goes back the moment it asks",
      d.usingKeys && d.update(now: 1, reads: 7, padConnected: true) == .none && d.update(now: 2, reads: 8, padConnected: true) == .useXInput && !d.usingKeys)
var e = BCDPadAuto()
e.grace = 5
e.start(now: 0, reads: 0, preferKeys: false)
check("the grace period is the rule's own number", e.update(now: 4.9, reads: 0, padConnected: true) == .none && e.update(now: 5, reads: 0, padConnected: true) == .useKeys && BCDPadAuto().grace == 25)

// what is kept between runs
var list: [String] = []
list = BCDPadAuto.remember("C:\\Games\\Old\\game.exe", noReads: true, in: list)
check("a run that never asked is remembered by the game's path, whatever its case", BCDPadAuto.remembered("c:\\games\\old\\GAME.EXE", in: list) && list.count == 1)
list = BCDPadAuto.remember("C:\\Games\\Old\\game.exe", noReads: true, in: list)
check("once", list.count == 1)
list = BCDPadAuto.remember("C:\\Games\\Old\\game.exe", noReads: false, in: list)
check("a run that asked takes it off the list", list.isEmpty && !BCDPadAuto.remembered("C:\\Games\\Old\\game.exe", in: list))
var many: [String] = []
for i in 0..<320 { many = BCDPadAuto.remember("c:\\g\\\(i).exe", noReads: true, in: many) }
check("at most 300 are kept, the newest", many.count == 300 && many.last == "c:\\g\\319.exe" && many.first == "c:\\g\\20.exe")

// the hint
var h = BCDLayoutHint()
h.start(now: 0, reads: 0)
check("the on-screen controller, no real one, no reads: the line after 20 seconds, once",
      (1...19).allSatisfy { !h.update(now: Double($0), reads: 0, eligible: true) } && h.update(now: 20, reads: 0, eligible: true)
      && !h.update(now: 21, reads: 0, eligible: true) && !h.update(now: 99, reads: 0, eligible: true))
var i = BCDLayoutHint()
i.start(now: 0, reads: 0)
check("the clock runs only while the line could be shown (the menu open, a controller connected, another layout start it again)",
      !i.update(now: 30, reads: 0, eligible: false) && !i.update(now: 49, reads: 0, eligible: true) && i.update(now: 50, reads: 0, eligible: true))
var j = BCDLayoutHint()
j.start(now: 0, reads: 5)
check("a game that reads the controller at any time ends the matter for the run", !j.update(now: 10, reads: 6, eligible: true) && !j.update(now: 100, reads: 6, eligible: true))
var told: [String] = []
check("a game is told once: its path is kept", BCDLayoutHint.mayTell("C:\\a.exe", told: told) && { told = BCDLayoutHint.remember("C:\\a.exe", told: told); return !BCDLayoutHint.mayTell("c:\\A.EXE", told: told) }()
      && BCDLayoutHint.remember("C:\\a.exe", told: told) == told)
check("the words say where the keyboard layout is, and that nothing changed by itself", BCDLayoutHint.text.contains("under Controls") && BCDPadAuto.switchedToKeys.contains("keyboard and mouse")
      && BCDPadAuto.switchedBack.contains("a controller again"))

print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)
