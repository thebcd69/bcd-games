// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// Settings > Memory & performance > In the background > CPU limit (BCDBackgroundCPU.swift), compiled as main.swift with it by
// tests/host/check-background-cpu.py.

import Foundation

var failures = 0
var passed = 0
func check(_ ok: Bool, _ what: String, _ detail: @autoclosure () -> String = "", line: Int = #line) {
    if ok { passed += 1; print("ok   " + what) } else { failures += 1; print("FAIL " + what + " (line \(line))" + (detail().isEmpty ? "" : "\n     " + detail())) }
}

typealias C = BCDBackgroundCPU

// What the engine does with bcd.cfg's value (BackgroundRun.cpuPercent), as the engine writes it. The check script holds that line of BackgroundRun.swift
// to this very text, so the two cannot drift apart.
func engine(_ raw: String?) -> Int32 {
    guard let raw, let v = Int32(raw) else { return 50 }
    return max(0, v)
}

check(C.key == "background-cpu" && C.standardPercent == 50, "the key is background-cpu and its standard is 50% of one core")
let samples: [String?] = [nil, "", " ", "50", "050", "65", "80", "100", "0", "-5", "150", "abc", "6 5", "65 ", "2147483647", "99999999999", "1.5", "+65"]
check(samples.allSatisfy { C.effective($0) == Int(engine($0)) }, "what the row reads is what the engine takes, for every kind of value (junk is 50, a negative is no cap)")
check(C.rowValue(nil) == "" && C.rowValue("") == "" && C.rowValue("50") == "" && C.rowValue("abc") == "" && C.rowValue("050") == "",
      "no line, junk and a hand-typed 50 all show as the standard (the engine does the same for all of them)")
check(C.rowValue("65") == "65" && C.rowValue("0") == "0" && C.rowValue("100") == "100" && C.rowValue("150") == "150" && C.rowValue("065") == "65",
      "the other values show as plain numbers")
check(C.stored("") == nil && C.stored("50") == nil && C.stored("65") == "65" && C.stored("0") == "0" && C.stored("100") == "100",
      "the standard writes no line at all; the others write their number")
check(C.stored("") == nil && C.effective(C.stored("")) == 50, "the default is exactly today's behaviour: no line, and the engine holds BCD to 50%")
let offered = C.choices(current: "")
check(offered.map(\.value) == ["", "65", "80", "100", "0"] && offered.map(\.label) == ["50% (standard)", "65%", "80%", "100%", "No limit"],
      "the row offers the standard first, then 65%, 80%, 100% and No limit", "\(offered)")
check(C.choices(current: "40").map(\.value) == ["", "65", "80", "100", "0", "40"] && C.choices(current: "40").last?.label == "40%",
      "a value set by hand stays reachable")
check(C.choices(current: "80").count == 5, "a value the row offers is not offered twice")
check(offered.allSatisfy { choice in C.effective(C.stored(choice.value)) == (choice.value.isEmpty ? 50 : Int(choice.value)!) },
      "every choice writes what it says: the engine then holds BCD to that percent (0 is no cap)")
check(offered.allSatisfy { C.rowValue(C.stored($0.value)) == $0.value }, "every choice reads back as itself")
check(C.title == "CPU limit in the background" && C.note.contains("50%") && C.note.contains("80%") && C.note.contains("next time you leave BCD")
      && C.note.contains("Keep a game running") && C.note.contains("Keep downloads running"),
      "the words say what it does, the iOS limit, when it applies and when it is used")
check(!(C.note + C.title).localizedCaseInsensitiveContains("madeira") && !C.note.contains("@"), "the words name no old name and no person")

print("background cpu: \(passed) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
