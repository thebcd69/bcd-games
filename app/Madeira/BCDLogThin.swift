// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. The thinning rule in this file (which lines a kind keeps, how a line's kind is found, the
// numbers and the note) was taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Core/LogThinner.swift)
// under GPL-3.0-or-later with the Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: BCD's names, BCD's tags kept
// whole, the decision made one line at a time over two passes of the file (a log of any size costs the same small memory, as Send
// Log's packing does) instead of on the whole log in memory, and the switch. See THIRD-PARTY-NOTICES.md.

import Foundation

// Send Log's "Make big logs smaller" (Settings › Logs, `bcd.logs.thin`; off by default). A big game's log is mostly one diagnostic
// repeated (thread stacks, draw dumps, placement and allocation traces, per-second counters), and the first lines of each kind say what
// the rest do. With the switch on, a log of 1.5 MB or more is sent with the first 150 lines of each kind and one in 40 after that; the
// app's own timestamped lines, BCD's measurement and verdict lines, the first 300 and the last 400 lines of the log, and every 5th line
// that names a failure are kept whole, and a note at the top says what was left out. The log on the device is not changed. Off, or
// for a smaller log, Send Log sends the log as it always did. Foundation only: tests/host/check-log-thin.py compiles this file alone.

struct BCDLogThinner {
    static let firstPerKind = 150
    static let thenEvery = 40
    static let keepFirstLines = 300
    static let keepLastLines = 400
    /// A log under this many bytes is sent whole.
    static let minimumBytes = 1_500_000
    /// Settings › Logs › Make big logs smaller before sending.
    static let switchKey = "bcd.logs.thin"

    /// Kinds that are always kept whole: what BCD's verdicts and measurements are read from, and the game's own words.
    private static let alwaysTags: Set<UInt64> = Set([
        // Porthole's list, where BCD writes the same tag.
        "xp", "guest-log", "lsfg", "telemetry", "mem-pressure", "gpu-perf", "footprint", "swap", "pad", "settings", "game-cfg", "engine",
        "crash", "jit", "dock", "display", "power", "audio", "prefix", "phase", "thread-sample", "app", "hwinput", "mem-alert", "bg-run",
        "cloud", "dock-installers", "dock-offline", "dock-launch", "exe-window",
        // BCD's own.
        "autotune", "madeira-env", "trace", "benchmark", "user-comment", "framegen", "pfx", "vti", "clamp", "sched-hints", "pad-auto",
        "device", "jit-pool", "WineProc", "fg", "feel", "touch", "session", "va-scan", "fex-arena", "recs", "dock-report", "prev-tail",
    ].map { fnv(Array($0.utf8)) })

    /// Words that make a line worth keeping even in a storm of its kind.
    private static let important: [[UInt8]] = ["Assertion", "abort_process", "Game exited", "FATAL", "Timed out", "failed to", "ERR_",
                                               "nhandled exception", "Game End", "crash"].map { Array($0.utf8) }

    private static func fnv(_ bytes: [UInt8]) -> UInt64 {
        var h: UInt64 = 0xcbf29ce484222325
        for b in bytes { h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return h
    }

    @inline(__always) private static func isHexOrX(_ b: UInt8) -> Bool { (b >= 48 && b <= 57) || (b >= 97 && b <= 102) || b == 120 }
    @inline(__always) private static func isTagByte(_ b: UInt8) -> Bool { (b >= 48 && b <= 57) || (b >= 65 && b <= 90) || (b >= 97 && b <= 122) || b == 95 || b == 45 || b == 46 }

    /// "[18:41:14.123] [INFO] ..." (the app's own line).
    private static func isAppLine(_ p: UnsafeRawBufferPointer) -> Bool {
        let e = p.count
        guard e > 20, p[0] == 91, p[3] == 58, p[6] == 58, p[9] == 46, p[13] == 93, p[14] == 32, p[15] == 91 else { return false }
        return p[16] >= 65 && p[16] <= 90
    }

    /// The kind of a line: the first `[tag]` in its first 60 bytes; a Wine `0198:err:module:function` prefix; else its first 24 bytes with
    /// digits and hex letters made equal. Returns the kind's hash and whether it was a `[tag]`.
    private static func kind(_ p: UnsafeRawBufferPointer) -> (hash: UInt64, isTag: Bool) {
        let e = p.count
        let limit = min(e, 60)
        var i = 0
        while i < limit {
            if p[i] == 91 {   // [
                var j = i + 1
                while j < e, j - i <= 25, isTagByte(p[j]) { j += 1 }
                if j < e, p[j] == 93, j - i >= 3 {
                    var h: UInt64 = 0xcbf29ce484222325
                    for k in (i + 1)..<j { h = (h ^ UInt64(p[k])) &* 0x100000001b3 }
                    return (h, true)
                }
            }
            i += 1
        }
        // 0198:err:seh:function ...
        if e > 12, p[4] == 58, isHexOrX(p[0]), isHexOrX(p[1]), isHexOrX(p[2]), isHexOrX(p[3]) {
            var colons = 0, j = 5
            while j < e, colons < 2 { if p[j] == 58 { colons += 1 }; j += 1 }
            while j < e, p[j] != 32, p[j] != 58 { j += 1 }
            var h: UInt64 = 0x84222325cbf29ce4
            for k in 5..<j { h = (h ^ UInt64(p[k])) &* 0x100000001b3 }
            return (h, false)
        }
        var h: UInt64 = 0x1234567
        for k in 0..<min(e, 24) { let b = isHexOrX(p[k]) ? UInt8(35) : p[k]; h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return (h, false)
    }

    private static func contains(_ p: UnsafeRawBufferPointer, _ needle: [UInt8]) -> Bool {
        let n = needle.count
        guard p.count >= n, let base = p.baseAddress else { return false }
        return needle.withUnsafeBufferPointer { nb in memmem(base, p.count, nb.baseAddress!, n) != nil }
    }

    // MARK: One pass

    /// The lines of the log, counted by the first pass.
    let total: Int
    private var index = 0
    private var counts: [UInt64: Int] = [:]
    private var names: [UInt64: (name: String, dropped: Int)] = [:]
    private(set) var dropped = 0

    init(total: Int) { self.total = total }

    /// Whether the next line of the log is sent. Called once for every line, in order (without its newline).
    mutating func keep(_ p: UnsafeRawBufferPointer) -> Bool {
        defer { index += 1 }
        if index < Self.keepFirstLines || index >= total - Self.keepLastLines || Self.isAppLine(p) { return true }
        let (k, isTag) = Self.kind(p)
        if isTag, Self.alwaysTags.contains(k) { return true }
        let n = (counts[k] ?? 0) + 1
        counts[k] = n
        if n <= Self.firstPerKind || n % Self.thenEvery == 0 { return true }
        if n % 5 == 0, Self.important.contains(where: { Self.contains(p, $0) }) { return true }
        var entry = names[k] ?? (name: "", dropped: 0)
        if entry.name.isEmpty { entry.name = String(decoding: p.prefix(28), as: UTF8.self) }
        entry.dropped += 1
        names[k] = entry
        dropped += 1
        return false
    }

    /// The note that goes at the top of a thinned log.
    var note: String {
        let top = names.values.sorted { $0.dropped > $1.dropped }.prefix(8)
            .map { "\($0.name.trimmingCharacters(in: .whitespaces)) x\($0.dropped)" }.joined(separator: "; ")
        return "[log-thin] \(dropped) of \(total) lines of repeated diagnostics were left out of this copy to keep it small (the first "
            + "\(Self.firstPerKind) of every kind and one in \(Self.thenEvery) after that stay; BCD's own lines, the measurements and the "
            + "end of the log are whole; the log on the device is complete). Most left out: \(top)"
    }

    // MARK: The file

    /// Calls `line` for every line of the file at `url`, in order, without its newline (a last line without one too). 1 MB at a time.
    static func lines(of url: URL, _ line: (UnsafeRawBufferPointer) -> Void) -> Bool {
        guard let input = try? FileHandle(forReadingFrom: url) else { return false }
        defer { try? input.close() }
        var carry = Data()
        while true {
            let chunk = (try? input.read(upToCount: 1 << 20)) ?? nil
            guard let chunk, !chunk.isEmpty else { break }
            chunk.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
                var start = 0
                while start < raw.count {
                    guard let nl = memchr(raw.baseAddress! + start, 0x0A, raw.count - start) else {
                        carry.append(contentsOf: UnsafeRawBufferPointer(rebasing: raw[start...]))
                        break
                    }
                    let end = UnsafeRawPointer(nl) - raw.baseAddress!
                    if carry.isEmpty {
                        line(UnsafeRawBufferPointer(rebasing: raw[start..<end]))
                    } else {
                        carry.append(contentsOf: UnsafeRawBufferPointer(rebasing: raw[start..<end]))
                        carry.withUnsafeBytes { line($0) }
                        carry.removeAll(keepingCapacity: true)
                    }
                    start = end + 1
                }
            }
        }
        if !carry.isEmpty { carry.withUnsafeBytes { line($0) } }
        return true
    }

    /// The first pass over a log: nil when it is sent whole (under `minimumBytes`, unreadable, or nothing to leave out); otherwise a fresh
    /// thinner for the second pass, which makes the same decisions, and the note for the top of the copy.
    static func plan(_ url: URL) -> (thinner: BCDLogThinner, note: String)? {
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.intValue ?? 0
        guard size >= minimumBytes else { return nil }
        var total = 0
        guard lines(of: url, { _ in total += 1 }) else { return nil }
        var first = BCDLogThinner(total: total)
        guard lines(of: url, { _ = first.keep($0) }), first.dropped > 0 else { return nil }
        return (BCDLogThinner(total: total), first.note)
    }
}
