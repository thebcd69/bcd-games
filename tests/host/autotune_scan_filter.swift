// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: the log scanner's pair filter against the plain search (tests/host/check-autotune-scan-filter.py compiles this with
// app/Madeira/AutoTuneCore.swift and AutoTuneRules.generated.swift).
//
//   scanfilter [--time] LOG...
//
// Every log is scanned twice, with the filter and without it, whole and in 4096-byte and 7-byte pieces; counts, first lines,
// evidence and the line count must be equal. Then made-up lines: every needle of every signal at the start, in the middle and at
// the end of a line, alone, cut by one byte, across the 16384-byte line limit. Prints one summary line; exit 1 on any difference.

import Foundation

@main
struct ScanFilter {
    static func scan(_ signals: [AutoTuneSignal], _ data: Data, filter: Bool, chunk: Int) -> AutoTuneLogScanner {
        let s = AutoTuneLogScanner(signals: signals, pairFilter: filter)
        if chunk <= 0 { s.feed(data) } else {
            var at = 0
            while at < data.count { let n = min(chunk, data.count - at); s.feed(data.subdata(in: at..<(at + n))); at += n }
        }
        s.finish()
        return s
    }
    static func same(_ a: AutoTuneLogScanner, _ b: AutoTuneLogScanner) -> Bool {
        a.counts == b.counts && a.firstLine == b.firstLine && a.evidence == b.evidence && a.lineCount == b.lineCount
    }
    static func main() {
        var args = Array(CommandLine.arguments.dropFirst())
        var timing = false
        if args.first == "--time" { timing = true; args.removeFirst() }
        let rules = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: nil)
        let signals = AutoTuneEngine.signals(rules)
        var bad = 0, files = 0, lines = 0, hits = 0
        var tOn = 0.0, tOff = 0.0
        for path in args {
            guard let data = FileManager.default.contents(atPath: path) else { print("cannot read \(path)"); bad += 1; continue }
            files += 1
            let t0 = Date()
            let on = scan(signals, data, filter: true, chunk: 0)
            let t1 = Date()
            let off = scan(signals, data, filter: false, chunk: 0)
            let t2 = Date()
            tOn += t1.timeIntervalSince(t0); tOff += t2.timeIntervalSince(t1)
            lines += on.lineCount; hits += on.counts.reduce(0, +)
            if !same(on, off) { print("DIFF whole \(path)"); bad += 1 }
            if !timing {
                for c in [4096, 7] where data.count <= 4_000_000 || c == 4096 {
                    if !same(scan(signals, data, filter: true, chunk: c), off) { print("DIFF chunk \(c) \(path)"); bad += 1 }
                }
            }
        }
        // Made-up lines from the rules' own needles.
        var made = Data(), madeLines = 0
        func add(_ bytes: [UInt8]) { made.append(contentsOf: bytes); made.append(0x0A); madeLines += 1 }
        let pad = Array("[madeira-d3d12] 0x1f 12:00:00.000 ".utf8)
        for (i, sig) in signals.enumerated() {
            let ns = sig.has.map { Array($0.utf8) }
            let all = ns.reduce([UInt8]()) { $0 + $1 + [0x20] }
            add(all)                                    // every needle, the first at the line's start
            add(pad + all + pad)                        // in the middle
            add(pad + Array(all.dropLast()))            // the last needle at the line's end
            if let first = ns.first, first.count > 1 {
                add(pad + Array(first.dropLast()) + [0x7e] + pad)   // cut by one byte: no match unless another line gives it
                add(Array(first.reversed()))                         // the same pairs backwards
            }
            if i % 97 == 0 {                            // across the line limit: only the first 16384 bytes count
                add([UInt8](repeating: 0x78, count: AutoTuneLogScanner.maxLine - 3) + all)
                add([UInt8](repeating: 0x78, count: AutoTuneLogScanner.maxLine - all.count) + all)
            }
        }
        add([]); add([0x41]); add([0x41, 0x42]); add([UInt8](repeating: 0xff, count: 300))
        let mOn = scan(signals, made, filter: true, chunk: 0), mOff = scan(signals, made, filter: false, chunk: 0)
        if !same(mOn, mOff) { print("DIFF made-up lines"); bad += 1 }
        if !same(scan(signals, made, filter: true, chunk: 1000), mOff) { print("DIFF made-up lines, 1000-byte pieces"); bad += 1 }
        let matchedSignals = mOff.counts.filter { $0 > 0 }.count
        print("scanfilter: \(signals.count) signals, \(files) logs (\(lines) lines, \(hits) signal hits), \(madeLines) made-up lines " +
              "(\(matchedSignals) signals matched in them); filter \(String(format: "%.2f", tOn)) s, plain \(String(format: "%.2f", tOff)) s; " +
              "\(bad == 0 ? "all equal" : "\(bad) DIFFERENT")")
        exit(bad == 0 ? 0 : 1)
    }
}
