// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
// app/Sources/Core/SchedHints.swift and CPUClampMonitor.swift) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// Scheduler hints for a running game, and a watch on the fast cores' clock (an experiment), as plain values: the key, the rule that
// says "iOS holds the fast cores back", and the words. BCDSchedHintsLive.swift asks iOS and reads the counters; nothing here does, so
// the preview build and tests/host/bcd_sched_hints_checks.swift use this file as it is.
//
// From Porthole: which hints are given (an activity of the user-initiated, latency-critical kind, and the kernel's first latency and
// throughput tiers for the process), and the clamp rule's numbers (half a core of load, 1.3 GHz, 1.8 GHz, three seconds, two seconds).

enum BCDSchedHints {
    /// bcd.cfg, or a game's own line: "1" gives the hints and logs the fast cores' clock; "watch" only logs (the other half of a
    /// comparison); no line, the default, does neither.
    static let key = "sched-hints"

    enum Mode: String, Equatable {
        case off = "", watch = "watch", hints = "1"

        /// The hints are given to iOS.
        var gives: Bool { self == .hints }
        /// The fast cores' clock is logged.
        var watches: Bool { self != .off }
    }

    static func mode(_ value: String?) -> Mode {
        switch value?.trimmingCharacters(in: .whitespaces).lowercased() {
        case "1", "on", "true", "yes": return .hints
        case "watch", "log": return .watch
        default: return .off
        }
    }

    /// Settings > Memory & performance > Speed and power.
    static let title = "Tell iOS a game is running (experimental)"
    static let note = "On: while a game runs, BCD asks iOS to treat it as what you are doing right now (an activity that must respond quickly, and the highest scheduling tiers for the app). It may keep a busy game on the fast cores for longer; it is a request, and iOS may ignore it. Log only gives no hint and writes the fast cores' clock to the log, [clamp] lines, to compare with. A game's own line sched-hints comes first."
    static let choices = [("", "Off"), ("watch", "Log only"), ("1", "On")]

    /// The line a session's start writes to the log. latency / throughput: what the kernel answered for the two tiers (0 = taken).
    static func startLine(_ mode: Mode, latency: Int32, throughput: Int32, tiers: (latency: UInt32, throughput: UInt32)?) -> String {
        guard mode.gives else { return "[sched] sched-hints = watch: no hint given; the fast cores' clock is watched ([clamp] lines)" }
        var out = "[sched] hints given: activity (user-initiated, latency-critical), latency tier \(latency == 0 ? "taken" : "refused (\(latency))"), throughput tier \(throughput == 0 ? "taken" : "refused (\(throughput))")"
        if let tiers { out += String(format: "; the process now has 0x%x / 0x%x", tiers.latency, tiers.throughput) }
        return out + "; the fast cores' clock is watched ([clamp] lines)"
    }
}

/// Does iOS hold the fast (performance) cores back? Their clock is their cycles over the time the process ran on them. A low clock
/// says nothing without load (idle cores clock down), so a sample counts only while the process keeps the fast cores busy: half a
/// core or more. Three seconds of such samples at 1.3 GHz or below with none at 1.8 GHz or above between them: held back. Two
/// seconds at 1.8 GHz or above with none at 1.3 or below between them: free again.
struct BCDCPUClamp: Equatable {
    static let lowGHz = 1.3, freeGHz = 1.8, busyShare = 0.5, lowMs = 3000.0, freeMs = 2000.0

    enum Event: Equatable { case held(ghz: Double), free(ghz: Double, heldMs: Double) }

    private(set) var held = false
    /// The clock of the last sample under load, in GHz; 0 before one.
    private(set) var ghz = 0.0
    /// How often the cores were held back, and for how long in all (ms), this session.
    private(set) var count = 0
    private(set) var totalMs = 0.0
    private var low = 0.0, high = 0.0, since = 0.0

    /// One sample: `dtMs` of time, `fastMs` of it the process ran on the fast cores (summed over cores), with `cycles` of them.
    mutating func step(dtMs: Double, fastMs: Double, cycles: Double) -> Event? {
        guard dtMs > 0, dtMs.isFinite else { return nil }
        if held { since += dtMs; totalMs += dtMs }
        guard fastMs.isFinite, cycles.isFinite, fastMs > 0, cycles >= 0, fastMs >= Self.busyShare * dtMs else { return nil }
        let g = cycles / (fastMs * 1e6)
        ghz = g
        if g <= Self.lowGHz {
            low += dtMs; high = 0
            if !held, low >= Self.lowMs { held = true; count += 1; since = 0; return .held(ghz: g) }
        } else if g >= Self.freeGHz {
            high += dtMs; low = 0
            if held, high >= Self.freeMs { held = false; return .free(ghz: g, heldMs: since) }
        }
        return nil
    }

    /// The log's line for an event. `after`: seconds since the session started. `thermal`: iOS's thermal state in a word.
    static func line(_ event: Event, after: Double, thermal: String) -> String {
        switch event {
        case .held(let ghz):
            return String(format: "[clamp] the fast cores run at %.2f GHz under load, %.0f s after the start (heat: %@): iOS is holding them back", ghz, after, thermal)
        case .free(let ghz, let heldMs):
            return String(format: "[clamp] the fast cores run free again at %.2f GHz after %.0f s held back (heat: %@)", ghz, heldMs / 1000, thermal)
        }
    }

    /// The line at the end of a session; nil when the cores were never held back.
    func endLine() -> String? {
        count > 0 ? String(format: "[clamp] session end: the fast cores were held back %d time%@, %.0f s in all", count, count == 1 ? "" : "s", totalMs / 1000) : nil
    }
}
