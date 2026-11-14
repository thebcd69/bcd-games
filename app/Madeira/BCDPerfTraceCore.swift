// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The performance trace (round 604): one line a second, kept small, that goes with Send Log when the player asked for it. A log says
// what happened; the trace says how the game was doing meanwhile (frame rate, the worst frame of each second, the CPU, the memory iOS
// still allowed, the heat), so "it slowed down after twenty minutes" and "it was fine until the memory ran out" can be told apart
// without anyone having watched a HUD. Plain values and rules only: nothing here touches the engine or UIKit, and
// tests/host/check-bcd-perftrace.py compiles this file alone. The sampler is BCDPerfTraceLive.swift.
//
// The file (one per session, named after the session log's inode, in Documents/logs/traces) is CSV:
//
//     #bcd-trace v1 date=2026-10-25 start=21:05:03.120 columns=t,fps,ms,worst,cpu,top,mem,free,swap,jit,th,lp,batt jit_total=896
//     0,0.0,,,12.5,9.1,812,5100,0,25,0,0,71
//     1,58.1,17.2,33,41.0,22.3,1024,4890,0,40,0,0,71
//
// and what Send Log appends to the log (after its last line, so no line number of the log moves) is the same text with "[trace] "
// in front of every line, between "[trace] begin" and "[trace] end" lines. Empty cells are readings the device could not give.

// MARK: - Rules

enum BCDTraceRules {
    /// The sampler wakes once a second.
    static let intervalSeconds = 1.0
    /// An hour of seconds. A longer session keeps its first `keepHead` lines (the loading, how the game started) and the newest ones.
    static let maxLines = 3600
    static let keepHead = 120
    /// The file may grow this far past `maxLines` before it is cut back, so the cut is a rare rewrite and not one per second.
    static let slack = 300
    /// What the sent block may hold: the longest file the app writes, plus the begin and end lines. A trace over this is cut the same way.
    static let maxSentLines = maxLines + 4
    /// The newest traces kept on the device; older ones go when a new one starts.
    static let keptFiles = 20
    static let folder = "traces"
    static let fileSuffix = ".trace.csv"
    static let tag = "[trace] "
    static let columns = ["t", "fps", "ms", "worst", "cpu", "top", "mem", "free", "swap", "jit", "th", "lp", "batt"]

    /// The trace of the session whose log has this inode.
    static func fileName(inode: UInt64) -> String { "trace-\(inode)\(fileSuffix)" }

    static func inode(ofFileName name: String) -> UInt64? {
        guard name.hasPrefix("trace-"), name.hasSuffix(fileSuffix) else { return nil }
        return UInt64(name.dropFirst("trace-".count).dropLast(fileSuffix.count))
    }

    /// Whether the trace is on for a session: the player's switch (Settings › Logs) or a guided debug run for it.
    static func records(switchOn: Bool, guidedRun: Bool) -> Bool { switchOn || guidedRun }

    /// The rows kept when there are too many: the first `keepHead` and the newest of the rest. Row order is kept. `dropped` says how many
    /// went.
    static func cut(_ rows: [String], limit: Int = maxLines) -> (rows: [String], dropped: Int) {
        guard rows.count > limit else { return (rows, 0) }
        let head = min(keepHead, limit / 2)
        return (Array(rows.prefix(head)) + Array(rows.suffix(limit - head)), rows.count - limit)
    }
}

// MARK: - One second

struct BCDTraceSample: Equatable {
    /// Seconds since the trace began.
    var t = 0
    /// Frames presented per second over this interval, and their mean and worst gap (ms). nil before the first frame ever.
    var fps: Double?
    var frameMs: Double?
    var worstMs: Double?
    /// Percent of all cores, and of the busiest thread's own core, over this interval; nil on the first reading.
    var cpu: Double?
    var top: Double?
    /// What iOS counts against the app (MB), and what it still allows (MB; nil when iOS gives no limit).
    var memoryMB: Int?
    var freeMB: Int?
    var swapMB: Int?
    /// The JIT pool in use (MB).
    var jitMB: Int?
    /// 0 nominal, 1 fair, 2 serious, 3 critical.
    var thermal = 0
    var lowPower = false
    var batteryPercent: Int?

    /// One CSV row; the cells are in `BCDTraceRules.columns` order.
    func row() -> String {
        func one(_ v: Double?) -> String { v.map { String(format: "%.1f", $0) } ?? "" }
        func whole(_ v: Double?) -> String { v.map { String(format: "%.0f", $0) } ?? "" }
        func int(_ v: Int?) -> String { v.map(String.init) ?? "" }
        return [String(max(0, t)), one(fps), one(frameMs), whole(worstMs), one(cpu), one(top), int(memoryMB), int(freeMB), int(swapMB), int(jitMB),
                String(min(max(thermal, 0), 3)), lowPower ? "1" : "0", int(batteryPercent)].joined(separator: ",")
    }

    /// A row read back; nil for anything that is not 13 cells with a whole-number time.
    static func parse(_ row: String) -> BCDTraceSample? {
        let cells = row.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
        guard cells.count == BCDTraceRules.columns.count, let t = Int(cells[0]), t >= 0 else { return nil }
        func d(_ i: Int) -> Double? { cells[i].isEmpty ? nil : Double(cells[i]) }
        func n(_ i: Int) -> Int? { cells[i].isEmpty ? nil : Int(cells[i]) }
        var s = BCDTraceSample()
        s.t = t
        s.fps = d(1); s.frameMs = d(2); s.worstMs = d(3); s.cpu = d(4); s.top = d(5)
        s.memoryMB = n(6); s.freeMB = n(7); s.swapMB = n(8); s.jitMB = n(9)
        s.thermal = min(max(n(10) ?? 0, 0), 3)
        s.lowPower = n(11) == 1
        s.batteryPercent = n(12)
        return s
    }
}

// MARK: - The frames of an interval

enum BCDTraceFrames {
    /// The frames presented between two readings. `times` are the presents' timestamps (seconds, oldest first) that arrived since the last
    /// reading, `previous` is the last timestamp before them (nil before the first frame ever) and `elapsed` the time between the readings.
    /// With no frame at all in an interval that follows frames, the worst gap is the silence so far: a stall shows as one long number.
    static func summarize(times: [Double], previous: Double?, now: Double, elapsed: Double) -> (fps: Double?, frameMs: Double?, worstMs: Double?) {
        guard previous != nil || !times.isEmpty else { return (nil, nil, nil) }
        guard elapsed > 0 else { return (nil, nil, nil) }
        guard !times.isEmpty else {
            let silence = previous.map { max(0, now - $0) * 1000 }
            return (0, nil, silence)
        }
        var worst = 0.0
        var last = previous
        for t in times {
            if let p = last, t >= p { worst = max(worst, (t - p) * 1000) }
            last = t
        }
        var mean: Double?
        if let first = previous ?? times.first, let end = times.last, end > first {
            let gaps = previous == nil ? times.count - 1 : times.count
            if gaps > 0 { mean = (end - first) * 1000 / Double(gaps) }
        }
        return (Double(times.count) / elapsed, mean, worst)
    }
}

// MARK: - The file

/// The traces on disk. `folder` is Documents/logs/traces in the app and a temporary folder in the host check.
struct BCDTraceStore {
    var folder: URL

    func url(inode: UInt64) -> URL { folder.appendingPathComponent(BCDTraceRules.fileName(inode: inode)) }

    /// The header line of a new trace.
    static func header(date: String, start: String, jitTotalMB: Int?) -> String {
        "#bcd-trace v1 date=\(date) start=\(start) columns=\(BCDTraceRules.columns.joined(separator: ","))"
            + (jitTotalMB.map { " jit_total=\($0)" } ?? "")
    }

    /// The header and rows of a trace file's text; rows that do not parse are left out (a line cut by a crash).
    static func read(_ text: String) -> (header: String?, rows: [String]) {
        var header: String?
        var rows: [String] = []
        for line in text.split(whereSeparator: \.isNewline) {
            if line.hasPrefix("#bcd-trace ") { header = header ?? String(line); continue }
            if BCDTraceSample.parse(String(line)) != nil { rows.append(String(line)) }
        }
        return (header, rows)
    }

    /// The lines Send Log appends to the log for the log whose inode this is; empty when there is no trace (the player did not ask for one)
    /// or it holds no row. At most `maxSentLines`.
    func linesToSend(inode: UInt64) -> [String] {
        guard inode != 0, let text = try? String(contentsOf: url(inode: inode), encoding: .utf8) else { return [] }
        let (header, rows) = Self.read(text)
        guard !rows.isEmpty else { return [] }
        let cut = BCDTraceRules.cut(rows)
        var out = [BCDTraceRules.tag + "begin " + (header.map { String($0.dropFirst("#bcd-trace ".count)) } ?? "v1")
                   + " rows=\(cut.rows.count)" + (cut.dropped > 0 ? " dropped=\(cut.dropped)" : "")]
        out += cut.rows.map { BCDTraceRules.tag + $0 }
        out.append(BCDTraceRules.tag + "end")
        return out
    }

    /// What the comment box says about it, or nil: "A performance trace of 12 minutes goes with it."
    func note(inode: UInt64) -> String? {
        guard inode != 0, let text = try? String(contentsOf: url(inode: inode), encoding: .utf8) else { return nil }
        let rows = Self.read(text).rows
        guard rows.count >= 2 else { return nil }
        let seconds = BCDTraceSample.parse(rows[rows.count - 1])?.t ?? rows.count
        let span = seconds >= 120 ? "\(Int((Double(seconds) / 60).rounded())) minutes" : "\(seconds) seconds"
        return "A performance trace of \(span) (one line a second: frame rate, CPU, memory, heat) goes with it."
    }

    /// Removes all but the newest `keep` traces (by modification time). Returns how many went.
    @discardableResult
    func prune(keep: Int = BCDTraceRules.keptFiles) -> Int {
        let fm = FileManager.default
        let names = (try? fm.contentsOfDirectory(atPath: folder.path)) ?? []
        let files = names.filter { BCDTraceRules.inode(ofFileName: $0) != nil }.map { folder.appendingPathComponent($0) }
        func modified(_ u: URL) -> Date { (try? u.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast }
        let old = files.sorted { modified($0) > modified($1) }.dropFirst(max(0, keep))
        for u in old { try? fm.removeItem(at: u) }
        return old.count
    }
}

// MARK: - The [benchmark] line (Porthole port, item 11)

/// One line at the end of a session for comparing builds and switches (bcd.cfg or a game's own line `benchmark-line = 1`; off by default):
///
///     [benchmark] build=658 seconds=612 fps-avg=41.3 fps-1pct=18.0 fps-5pct=27.0 fps-min=12.0 worst-ms=402 peak-mb=4210 least-free-mb=820
///                 swap-peak-mb=0 jit-peak-mb=311 heat=fair switches=MADEIRA_PRESENT_FX=1,MADEIRA_FRAMEGEN=1 game=Name
///
/// The frame rates are over the seconds in which the game showed a frame: the average, the slowest 1 % and 5 % of those seconds, and the
/// slowest one. The [session] line beside it (BCDSessionCore.swift) has the per-frame numbers; this one adds the build, the per-second view,
/// the least memory left, the swap and JIT peaks and the switches in force. The idea and the fields follow Porthole's `[benchmark]` line
/// (app/Sources/Core/Telemetry.swift, knowledge only); the code is BCD's.
enum BCDBenchmark {
    static let key = "benchmark-line"
    static func on(_ value: String?) -> Bool { value?.trimmingCharacters(in: .whitespaces) == "1" }
    /// The switches the line names when they are set for the session (their values as the session got them).
    static let switchNames = ["MADEIRA_PRESENT_FX", "MADEIRA_FRAMEGEN", "MADEIRA_PFX_UPSCALE", "MADEIRA_LSFG_CTL", "MADEIRA_VT_INTERP",
                              "DXMT_METALFX_SPATIAL_SWAPCHAIN", "MADEIRA_D3D11_SRC", "MADEIRA_FEX_CALLRET_LEAN"]

    /// The line, or nil when the session showed no frame.
    static func line(game: String, build: String, samples: [BCDTraceSample], switches: [String]) -> String? {
        let rates = samples.compactMap(\.fps).filter { $0 > 0 }.sorted()
        guard !rates.isEmpty else { return nil }
        func low(_ share: Double) -> Double { rates[min(rates.count - 1, Int(Double(rates.count) * share))] }
        let d = BCDTraceDigest.of(samples)
        var parts = ["build=\(build)", "seconds=\(d.seconds)",
                     String(format: "fps-avg=%.1f", rates.reduce(0, +) / Double(rates.count)),
                     String(format: "fps-1pct=%.1f", low(0.01)), String(format: "fps-5pct=%.1f", low(0.05)), String(format: "fps-min=%.1f", rates[0]),
                     String(format: "worst-ms=%.0f", d.worstMs), "peak-mb=\(d.peakMemoryMB)"]
        if let least = d.leastFreeMB { parts.append("least-free-mb=\(least)") }
        parts += ["swap-peak-mb=\(d.swapPeakMB)", "jit-peak-mb=\(d.jitPeakMB)", "heat=" + BCDSessionHeat.words[min(max(d.thermalPeak, 0), 3)],
                  "switches=" + (switches.isEmpty ? "none" : switches.joined(separator: ",")),
                  "game=" + game.replacingOccurrences(of: "\n", with: " ")]
        return "[benchmark] " + parts.joined(separator: " ")
    }
}

/// The heat levels in the words the [session] line uses.
enum BCDSessionHeat {
    static let words = ["nominal", "fair", "serious", "critical"]
}

// MARK: - What it says (for the readers)

/// A trace read back and summarised, for the card tools/log-facts.py makes and for the host check to hold the two to the same words.
struct BCDTraceDigest: Equatable {
    var seconds = 0
    var averageFPS: Double?
    var lowestFPS: Double?
    var lowestFPSAt = 0
    var worstMs = 0.0
    var worstMsAt = 0
    var peakMemoryMB = 0
    var leastFreeMB: Int?
    var leastFreeAt = 0
    var thermalPeak = 0
    var thermalFirstSeriousAt: Int?
    var swapPeakMB = 0
    var jitPeakMB = 0

    static func of(_ samples: [BCDTraceSample]) -> BCDTraceDigest {
        var d = BCDTraceDigest()
        d.seconds = samples.last?.t ?? 0
        let rates = samples.compactMap { s in s.fps.map { ($0, s.t) } }.filter { $0.0 > 0 }
        if !rates.isEmpty { d.averageFPS = rates.map(\.0).reduce(0, +) / Double(rates.count) }
        if let low = rates.min(by: { $0.0 < $1.0 }) { d.lowestFPS = low.0; d.lowestFPSAt = low.1 }
        for s in samples {
            if let w = s.worstMs, w > d.worstMs { d.worstMs = w; d.worstMsAt = s.t }
            if let m = s.memoryMB { d.peakMemoryMB = max(d.peakMemoryMB, m) }
            if let f = s.freeMB, f > 0, f < (d.leastFreeMB ?? Int.max) { d.leastFreeMB = f; d.leastFreeAt = s.t }
            d.thermalPeak = max(d.thermalPeak, s.thermal)
            if s.thermal >= 2, d.thermalFirstSeriousAt == nil { d.thermalFirstSeriousAt = s.t }
            d.swapPeakMB = max(d.swapPeakMB, s.swapMB ?? 0)
            d.jitPeakMB = max(d.jitPeakMB, s.jitMB ?? 0)
        }
        return d
    }

    static func clock(_ seconds: Int) -> String { String(format: "%d:%02d", seconds / 60, seconds % 60) }
}
