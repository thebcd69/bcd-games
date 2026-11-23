// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDBackgroundCPU.swift
//  Madeira
//
//  Settings › Memory & performance › In the background › "CPU limit in the background": the rules of that row, Foundation only
//  (tests/host/check-background-cpu.py compiles this file with tests/host/bcd_background_cpu_checks.swift).
//
//  While BCD keeps a game or a download running in the background (BackgroundRun.swift), it holds its own CPU use under
//  bcd.cfg's `background-cpu` percent of one core, 50 when the key is absent (Winios/BackgroundCPUCap.c pauses its threads
//  for 100 ms at a time). A big download hashes, decrypts and decompresses on several streams, so 50% slows it a great deal
//  (a player's report, round 707); iOS, on the other side, ends an app that averages more than about 80% of one core for a
//  minute. The row lets the player choose; its standard is no line at all, which is exactly what the engine did before the row.

import Foundation

enum BCDBackgroundCPU {
    /// bcd.cfg's key. It is also a game's own key (a game's file overrides the bcd.cfg value for that game).
    static let key = "background-cpu"
    /// What the engine uses when the key is absent or holds something that is not a whole number (BackgroundRun.cpuPercent).
    static let standardPercent = 50
    /// The values after the standard, in the order the row offers them; "0" is no limit.
    static let steps = ["65", "80", "100", "0"]

    /// The percent the engine takes from what bcd.cfg holds: BackgroundRun.cpuPercent's own rule (a whole number, lowered to 0 at least;
    /// anything else is the standard). 0 means no cap.
    static func effective(_ raw: String?) -> Int {
        guard let raw, let value = Int32(raw) else { return standardPercent }
        return Int(max(0, value))
    }

    /// The row's value for what bcd.cfg holds: "" for the standard (no line, junk, or a 50 typed by hand: the engine does the same
    /// for all three), else the percent as a plain number.
    static func rowValue(_ raw: String?) -> String {
        let percent = effective(raw)
        return percent == standardPercent ? "" : String(percent)
    }

    /// What to write for a chosen value: nil, no line, for the standard.
    static func stored(_ value: String) -> String? {
        let row = rowValue(value)
        return row.isEmpty ? nil : row
    }

    /// The row's values and words: the standard first; a value set by hand that the steps do not have stays reachable.
    static func choices(current: String) -> [(value: String, label: String)] {
        var values = [""] + steps
        if !current.isEmpty, !steps.contains(current) { values.append(current) }
        return values.map { ($0, label($0)) }
    }

    static func label(_ value: String) -> String {
        if value.isEmpty { return "\(standardPercent)% (standard)" }
        if value == "0" { return "No limit" }
        return "\(value)%"
    }

    /// The row's title and the words under it.
    static let title = "CPU limit in the background"
    static let note = "While BCD keeps a game or a download going in the background, it uses at most this much of one core. "
        + "At \(standardPercent)% a big download is slow; more is faster. iOS closes an app that averages above about 80% for a minute, "
        + "so 100% and No limit can get BCD closed. Used only while Keep a game running or Keep downloads running is on, "
        + "and it applies the next time you leave BCD."
}
