// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: auto-tune's replay tool (docs/AUTO-TUNE.md), compiled on the host with
// app/Madeira/AutoTuneCore.swift and AutoTuneRules.generated.swift by
// tests/host/check-autotune.py and tools/autotune-fixture.py.
//
//   replay [--chunk N] [--end JSON] [--rules FILE] [--user-keys a,b] LOG...
//
// For each log: one JSON line with the scan's facts, the report (outcome, stage,
// matched rules, the game's error boxes, whether a .NET program found no Wine Mono) and the
// line auto-tune would try next (after a baseline session and the one unchanged retry). --chunk feeds the scanner
// N bytes at a time (lines cut across chunks); --end overrides AutoTuneSessionEnd's
// fields (e.g. {"killed": true}); --rules adds an override file as the app would.

import Foundation

@main
struct AutoTuneReplay {
    static func main() {
        var args = Array(CommandLine.arguments.dropFirst())
        var chunk = 0
        var endJSON = "{}"
        var overrideFile: URL?
        var userKeys: Set<String> = []
        while let a = args.first, a.hasPrefix("--") {
            args.removeFirst()
            guard let v = args.first else { break }
            args.removeFirst()
            switch a {
            case "--chunk": chunk = Int(v) ?? 0
            case "--end": endJSON = v
            case "--rules": overrideFile = URL(fileURLWithPath: v)
            // Night round 2026-11-11 (agent T3 S1): keys the player set (the game's own file, the entry's lines, bcd.cfg): "swap-mb,pool".
            case "--user-keys": userKeys = Set(v.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
            default: FileHandle.standardError.write(Data("unknown option \(a)\n".utf8)); exit(2)
            }
        }
        let rules = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: overrideFile)
        let signals = AutoTuneEngine.signals(rules)
        var base = (try? JSONSerialization.jsonObject(with: Data("{\"sawProcess\": true, \"quitRequested\": false, \"seconds\": 0, \"killed\": false}".utf8))) as? [String: Any] ?? [:]
        if let extra = (try? JSONSerialization.jsonObject(with: Data(endJSON.utf8))) as? [String: Any] {
            base.merge(extra) { $1 }
        }
        guard let endData = try? JSONSerialization.data(withJSONObject: base),
              let end = try? JSONDecoder().decode(AutoTuneSessionEnd.self, from: endData) else {
            FileHandle.standardError.write(Data("bad --end\n".utf8)); exit(2)
        }
        var failed = false
        for path in args {
            let url = URL(fileURLWithPath: path)
            let scanner: AutoTuneLogScanner
            if chunk > 0 {
                guard let data = try? Data(contentsOf: url) else { failed = true; continue }
                scanner = AutoTuneLogScanner(signals: signals)
                var i = 0
                while i < data.count {
                    scanner.feed(data.subdata(in: i..<min(i + chunk, data.count)))
                    i += chunk
                }
                scanner.finish()
            } else {
                guard let s = AutoTuneLogScanner.scan(url, signals: signals) else { failed = true; continue }
                scanner = s
            }
            let report = AutoTuneEngine.report(end, scanner, rules: rules, signals: signals)
            var state = AutoTuneEngine.advance(AutoTuneState(), report: report, rules: rules, userKeys: userKeys,
                                               fingerprint: "replay", log: nil)
            // The second start: after the unchanged retry, which a noRetry rule skips.
            if state.candidateRule == "retry" {
                state = AutoTuneEngine.advance(state, report: report, rules: rules, userKeys: userKeys,
                                               fingerprint: "replay", log: nil)
            }
            let enc = JSONEncoder()
            enc.outputFormatting = [.sortedKeys]
            let facts = (try? JSONSerialization.jsonObject(with: enc.encode(scanner.facts))) ?? [:]
            let out: [String: Any] = [
                "file": url.lastPathComponent,
                "facts": facts,
                "outcome": report.outcome,
                "stage": report.stage,
                "matched": report.matched,
                "dialogs": report.dialogs,
                "monoMissing": state.monoMissing == true,
                "status": state.status,
                "next": state.candidate ?? [],
            ]
            if let d = try? JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]) {
                print(String(decoding: d, as: UTF8.self))
            }
        }
        exit(failed ? 1 : 0)
    }
}
