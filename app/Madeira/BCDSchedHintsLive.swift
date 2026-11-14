// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
// app/Sources/Core/SchedHints.swift and CPUClampMonitor.swift) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// Scheduler hints while a game runs, and the watch on the fast cores' clock (BCDSchedHints.swift has the rule and the words,
// BCDSchedHints.m the kernel calls). Only a session whose bcd.cfg or game line has `sched-hints` gets any of it: without the key
// `begin` returns at its first line, and nothing is asked of iOS, no timer runs and no line is logged. Engine-bound: the preview
// build leaves this file out.

@MainActor enum BCDSchedHintsLive {
    private static var mode = BCDSchedHints.Mode.off
    private static var activity: NSObjectProtocol?
    /// The tiers the process had before the hints, to put back when the session ends; nil when they could not be read.
    private static var before: (latency: UInt32, throughput: UInt32)?
    private static var timer: Timer?
    private static var clamp = BCDCPUClamp()
    private static var last: (fastMs: Double, cycles: Double, wallMs: Double)?
    private static var started = Date()

    /// A session's start (ContentView's launch code runs on a worker; the work itself is done on the main thread).
    nonisolated static func launch() {
        DispatchQueue.main.async { MainActor.assumeIsolated { begin() } }
    }

    static func begin() {
        let wanted = BCDSchedHints.mode(MadeiraConfig.gameValue(BCDSchedHints.key) ?? MadeiraConfig.get(BCDSchedHints.key))
        guard wanted != .off, mode == .off else { return }
        mode = wanted
        var latencyAnswer: Int32 = 0, throughputAnswer: Int32 = 0
        var tiers: (latency: UInt32, throughput: UInt32)?
        if wanted.gives {
            var l: UInt32 = 0, t: UInt32 = 0
            before = madeira_sched_task_tiers(&l, &t) == 0 ? (l, t) : nil
            activity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated, .latencyCritical, .idleSystemSleepDisabled], reason: "BCD is running a game")
            madeira_sched_task_qos(madeira_sched_first_latency_tier(), madeira_sched_first_throughput_tier(), &latencyAnswer, &throughputAnswer)
            if madeira_sched_task_tiers(&l, &t) == 0 { tiers = (l, t) }
        }
        LogStore.shared.log(BCDSchedHints.startLine(wanted, latency: latencyAnswer, throughput: throughputAnswer, tiers: tiers))
        clamp = BCDCPUClamp()
        last = nil
        started = Date()
        let t = Timer(timeInterval: 1, repeats: true) { _ in MainActor.assumeIsolated { tick() } }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private static func tick() {
        var fastMs = 0.0, cycles = 0.0, wallMs = 0.0
        guard madeira_cpu_clock_sample(&fastMs, &cycles, &wallMs) == 0 else { return }
        defer { last = (fastMs, cycles, wallMs) }
        guard let p = last else { return }
        if let event = clamp.step(dtMs: wallMs - p.wallMs, fastMs: fastMs - p.fastMs, cycles: cycles - p.cycles) {
            LogStore.shared.log(BCDCPUClamp.line(event, after: Date().timeIntervalSince(started), thermal: thermal))
        }
    }

    private static var thermal: String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: return "cool"
        case .fair: return "warm"
        case .serious: return "hot"
        case .critical: return "very hot"
        @unknown default: return "?"
        }
    }

    /// The session ended: the watch stops, and the process is what it was before the hints.
    static func end() {
        guard mode != .off else { return }
        timer?.invalidate()
        timer = nil
        if let line = clamp.endLine() { LogStore.shared.log(line) }
        if mode.gives {
            if let activity { ProcessInfo.processInfo.endActivity(activity) }
            activity = nil
            if let before {
                var l: Int32 = 0, t: Int32 = 0
                madeira_sched_task_qos(before.latency, before.throughput, &l, &t)
                LogStore.shared.log("[sched] session end: the hints are withdrawn (latency tier \(l == 0 ? "put back" : "refused (\(l))"), throughput tier \(t == 0 ? "put back" : "refused (\(t))"))")
            }
            before = nil
        }
        mode = .off
    }
}
