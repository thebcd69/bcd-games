// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: auto-tune's engine checks (docs/AUTO-TUNE.md), compiled on the host by
// tests/host/check-autotune.py with app/Madeira/AutoTuneCore.swift and
// AutoTuneRules.generated.swift. No log files: reports are built by hand, and the
// scanner is fed bytes directly.

import Foundation

@main
struct AutoTuneEngineChecks {
    static var failures = 0
    static func expect(_ cond: Bool, _ what: String, file: StaticString = #fileID, line: UInt = #line) {
        if cond { print("ok   \(what)") } else { print("FAIL \(what) (line \(line))"); failures += 1 }
    }

    static func report(_ outcome: String, stage: Int, seconds: Double = 30, matched: [String] = [],
                       dock: Bool = false) -> AutoTuneReport {
        AutoTuneReport(outcome: outcome, stage: stage, seconds: seconds, presents: 0, peakMB: nil, dock: dock,
                       d3d12: false, ramMB: 11695, matched: matched, evidence: [])
    }

    static func main() {
        let rules = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: nil)
        expect(rules.count >= 10, "the built-in table decodes (\(rules.count) rules)")
        expect(Set(rules.map(\.id)).count == rules.count, "rule ids are unique")
        let cage = "cage-release-band-exhausted", jumbo = "jumbo-half-probe", spill = "thread-stack-spill"

        // 1. Baseline fails -> one unchanged retry -> the best-matching rule.
        var s = AutoTuneState()
        s = AutoTuneEngine.advance(s, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "baseline.txt")
        expect(s.status == "trying" && s.candidate == [] && s.candidateRule == "retry" && s.retried,
               "a first failure runs the same settings once more")
        expect(s.attempts.count == 1 && s.attempts[0].kept == nil && s.attempts[0].rule == nil, "the baseline is recorded")
        s = AutoTuneEngine.advance(s, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "retry.txt")
        expect(s.attempts.last?.kept == false && s.attempts.last?.rule == "retry", "an unchanged retry that did no better is not kept")
        expect(s.candidate == ["env.MADEIRA_CAGE_RELEASE = 1"] && s.candidateRule == cage,
               "then the first matching rule by priority (cage before stack spill)")
        expect(s.layerLines == ["env.MADEIRA_CAGE_RELEASE = 1"], "the layer carries the candidate")

        // 2. The candidate got further -> kept; the next failure brings the next rule.
        s = AutoTuneEngine.advance(s, report: report("crashed", stage: 4, seconds: 90, matched: [jumbo]), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "a1.txt")
        expect(s.kept == ["env.MADEIRA_CAGE_RELEASE = 1"] && s.attempts.last?.kept == true, "a later stage keeps the line")
        expect(s.candidate == ["env.MADEIRA_JUMBO_HALF = 1"] && s.layerLines.count == 2, "and the next rule is tried on top")

        // 3. It plays -> found.
        s = AutoTuneEngine.advance(s, report: report("quit", stage: 5, seconds: 400), rules: rules,
                                   userKeys: [], fingerprint: "a", log: "a2.txt")
        expect(s.status == "found" && s.kept == ["env.MADEIRA_CAGE_RELEASE = 1", "env.MADEIRA_JUMBO_HALF = 1"] && s.candidate == nil,
               "a session that played ends the search with both lines")

        // 4. No progress -> reverted, the next candidate comes from the new report.
        var r = AutoTuneState()
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, seconds: 31, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        expect(r.kept.isEmpty && r.attempts.last?.kept == false, "no progress reverts the line")
        expect(r.candidateRule == spill, "an untried rule of the same failure comes next")
        r = AutoTuneEngine.advance(r, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: [], fingerprint: "b", log: nil)
        expect(r.status == "stuck" && r.candidate == nil, "every matching rule tried: stuck")

        // 5. Same stage but half again as long and 10 s more counts as progress.
        expect(AutoTuneEngine.better(report("crashed", stage: 3, seconds: 45), than: report("crashed", stage: 3, seconds: 30)),
               "1.5x and +10 s at the same stage is better")
        expect(!AutoTuneEngine.better(report("crashed", stage: 3, seconds: 35), than: report("crashed", stage: 3, seconds: 30)),
               "a few seconds more is not")
        expect(AutoTuneEngine.better(report("quit", stage: 3), than: report("crashed", stage: 4)), "playing beats any failure")
        expect(!AutoTuneEngine.better(report("crashed", stage: 2), than: report("crashed", stage: 3)), "an earlier stage is worse")
        expect(AutoTuneEngine.better(report("crashed", stage: 3, matched: [spill]), than: report("crashed", stage: 3, matched: [cage]), tried: cage),
               "the tried rule's own failure gone at the same stage is better")
        expect(!AutoTuneEngine.better(report("crashed", stage: 3, matched: [cage, spill]), than: report("crashed", stage: 3, matched: [cage]), tried: cage),
               "its failure still there plus another is not")
        expect(!AutoTuneEngine.better(report("crashed", stage: 3, matched: [spill]), than: report("crashed", stage: 3, matched: [cage])),
               "a different failure alone, with nothing tried, is not")

        // 6. Inconclusive sessions change nothing and do not count.
        var q = AutoTuneState()
        q = AutoTuneEngine.advance(q, report: report("crashed", stage: 3, matched: [cage]), rules: rules, userKeys: [], fingerprint: "c", log: nil)
        let before = q
        q = AutoTuneEngine.advance(q, report: report("inconclusive", stage: 2), rules: rules, userKeys: [], fingerprint: "c", log: nil)
        expect(q.candidate == before.candidate && q.kept == before.kept && q.decided == before.decided && q.attempts.count == before.attempts.count + 1,
               "an inconclusive session keeps the same candidate for the next start")

        // 7. The user's own keys are never proposed.
        var u = AutoTuneState(retried: true)
        u = AutoTuneEngine.advance(u, report: report("crashed", stage: 3, matched: [cage, spill]), rules: rules,
                                   userKeys: ["env.MADEIRA_CAGE_RELEASE"], fingerprint: "d", log: nil)
        expect(u.candidateRule == spill, "a key the game's own file sets is skipped")

        // 8. A changed fingerprint starts a new baseline and drops the pending candidate.
        var f = AutoTuneState()
        f = AutoTuneEngine.advance(f, report: report("crashed", stage: 3, matched: [cage]), rules: rules, userKeys: [], fingerprint: "e1", log: nil)
        f = AutoTuneEngine.advance(f, report: report("crashed", stage: 3, matched: [cage]), rules: rules, userKeys: [], fingerprint: "e1", log: nil)
        expect(f.candidateRule == cage, "a candidate is pending")
        f = AutoTuneEngine.advance(f, report: report("crashed", stage: 2, matched: [spill]), rules: rules, userKeys: [], fingerprint: "e2", log: nil)
        expect(f.fingerprint == "e2" && f.best?.stage == 2 && f.attempts.last?.kept == nil,
               "after the user changed the settings the session is a new baseline, nothing kept or reverted")
        expect(f.candidateRule == spill, "and the search goes on from it")

        // 9. At most maxAttempts decided attempts (rules with lines: a fixedIn rule stops at once, see 16).
        var m = AutoTuneState(retried: true)
        var n = 0
        let ids = rules.filter { $0.fixedIn == nil }.map(\.id)
        while m.status != "gave-up" && m.status != "stuck" && n < 40 {
            m = AutoTuneEngine.advance(m, report: report("crashed", stage: 3, matched: ids), rules: rules,
                                       userKeys: [], fingerprint: "g", log: nil)
            n += 1
        }
        expect(m.status == "gave-up" && m.decided == AutoTuneEngine.maxAttempts,
               "it stops after \(AutoTuneEngine.maxAttempts) attempts (\(m.decided) decided, status \(m.status))")

        // 10. A game that played at once needs nothing.
        let ok = AutoTuneEngine.advance(AutoTuneState(), report: report("ran", stage: 5, seconds: 600), rules: rules,
                                        userKeys: [], fingerprint: "h", log: nil)
        expect(ok.status == "ok" && ok.layerLines.isEmpty, "a first session that played adds nothing")

        // 11. The override file replaces, adds and disables rules.
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("autotune-override-\(getpid()).json")
        let override = """
        [{"id": "cage-release-band-exhausted", "lines": [], "priority": 1, "all": [{"has": ["x"]}], "reason": "off"},
         {"id": "my-rule", "lines": ["env.MADEIRA_TEST = 1"], "priority": 5, "all": [{"has": ["[my-signal]"]}], "reason": "test"},
         {"id": "jumbo-half-probe", "lines": ["env.MADEIRA_JUMBO_HALF = 1"], "priority": 3, "all": [{"has": ["[jumbo] other"]}], "reason": "replaced"}]
        """
        try? override.write(to: tmp, atomically: true, encoding: .utf8)
        let merged = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: tmp)
        expect(!merged.contains { $0.id == cage }, "an override with no lines disables a built-in rule")
        expect(merged.contains { $0.id == "my-rule" }, "an override adds a rule")
        expect(merged.first { $0.id == jumbo }?.all.first?.has == ["[jumbo] other"], "an override replaces a rule of the same id")
        expect(merged.count == rules.count, "one removed, one added, one replaced")
        let broken = FileManager.default.temporaryDirectory.appendingPathComponent("autotune-broken-\(getpid()).json")
        try? "not json".write(to: broken, atomically: true, encoding: .utf8)
        expect(AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: broken).count == rules.count,
               "a broken override file leaves the built-in table alone")
        try? FileManager.default.removeItem(at: tmp)
        try? FileManager.default.removeItem(at: broken)

        // 12. The scanner: lines cut across chunks, a line without its newline, an overlong line, numbers.
        let signals = [AutoTuneSignal(has: ["[sig] alpha", "beta"]), AutoTuneSignal(has: ["[sig] gamma"])]
        let text = "[build] v0.1.552 (552)\n[device] model=iPhone18,2 ram=11695MB x\n[sig] alpha and beta\n"
            + String(repeating: "x", count: 40000) + " [sig] alpha beta (too far in)\n"
            + "[footprint] rev=ml358 phys=6100 MB (peak 6200) internal\n[freeze] alive t+305s beats\n"
            + "[exit-report] status=0xC0000005\n[sig] gamma at the end"
        for chunk in [1, 3, 7, 64, 4096, 1 << 20] {
            let sc = AutoTuneLogScanner(signals: signals)
            let bytes = Data(text.utf8)
            var i = 0
            while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
            sc.finish()
            expect(sc.counts == [1, 1] && sc.facts.build == 552 && sc.facts.ramMB == 11695
                   && sc.facts.lastFootprintMB == 6100 && sc.facts.peakFootprintMB == 6200 && sc.facts.aliveSeconds == 305
                   && sc.facts.stage == 5 && sc.facts.exitStatus == 0xC0000005,
                   "scanner with \(chunk)-byte chunks: counts \(sc.counts), facts \(sc.facts.build ?? -1)/\(sc.facts.ramMB ?? -1)/\(sc.facts.aliveSeconds)")
        }

        // 12b. The quit line freezes the crash evidence seen so far.
        let qsig: [AutoTuneSignal] = []
        let crashFirst = AutoTuneLogScanner(signals: qsig)
        crashFirst.feed(Data("x Unhandled exception code c0000005\n[frontend] graceful close requested\n".utf8)); crashFirst.finish()
        let quitFirst = AutoTuneLogScanner(signals: qsig)
        quitFirst.feed(Data("[frontend] graceful close requested\nx Unhandled exception code c0000005\n".utf8)); quitFirst.finish()
        expect(crashFirst.facts.crashEvidenceAtQuit == 1 && quitFirst.facts.crashEvidenceAtQuit == 0,
               "crash evidence is counted up to the quit request only")

        // 13. Outcomes from the session-end facts.
        var fx = AutoTuneLogFacts()
        fx.stage = 4
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 100), fx) == "quit", "quit after frames: played")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, crashStatus: 0xC0000005, seconds: 100), fx) == "crashed", "a Windows error exit: crashed")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: false, seconds: 2), AutoTuneLogFacts()) == "inconclusive", "never started: inconclusive")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 400), fx) == "ran", "a long session that ended on its own: ran")
        var early = AutoTuneLogFacts(); early.stage = 2
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 8), early) == "exited-early", "ended before a frame: exited early")
        var mem = AutoTuneLogFacts(); mem.stage = 4; mem.lastFootprintMB = 6250; mem.availableMB = 6100
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), mem) == "killed", "a kill near the memory limit: killed")
        var high = AutoTuneLogFacts(); high.stage = 5; high.lastFootprintMB = 7932; high.availableMB = 8061
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), high) == "inconclusive",
               "a session that ended 400 MB under the estimated limit is not called a memory kill")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, limitMB: 8050), high) == "killed",
               "with the marker's measured limit 118 MB above it, it is")
        var gpu = AutoTuneLogFacts(); gpu.stage = 5; gpu.gpuErrors = 3
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), gpu) == "hung", "a kill after GPU errors: hung")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), fx) == "inconclusive", "a kill with no evidence: ask the user")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "froze"), fx) == "hung", "the user said it froze")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "closed-by-itself"), fx) == "killed", "the user said it closed by itself")
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), gpu) == "inconclusive", "the user closed it: nothing learned")
        var cq = AutoTuneLogFacts(); cq.stage = 4; cq.unhandled = 1; cq.crashEvidenceAtQuit = 1; cq.quitRequested = true
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 100), cq) == "crashed",
               "a game that crashed before the user closed the session crashed (Madeira Dock)")
        var dq = AutoTuneLogFacts(); dq.stage = 4; dq.unhandled = 2
        expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 100), dq) == "quit",
               "a quit game that logged an exception on the way out still played")

        // 14. Memory: the device's swap step, then the upgrade replaces auto-tune's own swap-mb, never the user's.
        var w = AutoTuneState(retried: true)
        let kill = report("killed", stage: 4, seconds: 120, matched: ["swap-12gb-memory-kill"])
        w = AutoTuneEngine.advance(w, report: kill, rules: rules, userKeys: [], fingerprint: "m", log: nil)
        expect(w.candidate == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"], "a 12 GB memory kill: the 6144 MB step")
        w = AutoTuneEngine.advance(w, report: report("killed", stage: 4, seconds: 300, matched: ["swap-12gb-memory-kill", "swap-12gb-more"]),
                                   rules: rules, userKeys: [], fingerprint: "m", log: nil)
        expect(w.kept == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"], "longer before the kill: the step is kept")
        expect(w.candidate == ["swap-mb = 8192"] && w.layerLines == ["swap-mode = 2", "swap-min-mb = 1", "swap-mb = 8192"],
               "killed again with the swap file in use: 8192 replaces 6144 in the layer (\(w.layerLines))")
        w = AutoTuneEngine.advance(w, report: report("quit", stage: 5, seconds: 900), rules: rules, userKeys: [], fingerprint: "m", log: nil)
        expect(w.status == "found" && w.kept == ["swap-mode = 2", "swap-min-mb = 1", "swap-mb = 8192"], "kept: one swap-mb, the larger")
        var v = AutoTuneState(retried: true)
        v = AutoTuneEngine.advance(v, report: report("killed", stage: 4, matched: ["swap-12gb-memory-kill", "swap-12gb-more"]),
                                   rules: rules, userKeys: ["swap-mb"], fingerprint: "n", log: nil)
        expect(v.status == "stuck" && v.candidate == nil, "the user's own swap-mb is neither added to nor raised")
        var x = AutoTuneState(retried: true)
        x = AutoTuneEngine.advance(x, report: report("killed", stage: 4, matched: ["swap-12gb-more"]), rules: rules,
                                   userKeys: [], fingerprint: "o", log: nil)
        expect(x.status == "stuck", "an upgrade needs a line auto-tune kept: it never adds a first swap-mb on its own")
        // Round 625 (the owner's decision 38 b; Horizon Zero Dawn NFFPKV, its next log C7X0G9: "killed ... status stuck"): with the
        // player's own swap file the swap tier is skipped, and helper memory whose guard closed at BCD's limit warning is next.
        let pool = "helper-memory-guard-closed-at-limit"
        expect(rules.first { $0.id == pool }.map { $0.lines == ["env.MADEIRA_POOL_MORE = 1"] && $0.priority == 52 && $0.noRetry == true
                                                    && $0.outcomes == ["killed"] && $0.memoryMinPct == 85 } == true,
               "the helper-memory rule: the key, after the swap tier's first lines, killed at 85 % or more, no retry")
        var hm = AutoTuneState(retried: true)
        hm = AutoTuneEngine.advance(hm, report: report("killed", stage: 5, seconds: 90,
                                                       matched: ["swap-12gb-memory-kill-sure", "swap-12gb-memory-kill", pool, "swap-12gb-more"]),
                                    rules: rules, userKeys: ["swap-mb", "swap-mode", "swap-min-mb"], fingerprint: "hm", log: nil)
        expect(hm.status == "trying" && hm.candidateRule == pool && hm.candidate == ["env.MADEIRA_POOL_MORE = 1"],
               "the player's own swap file: helper memory takes more next, not stuck (\(hm.status), \(hm.candidateRule ?? "-"))")
        var hm2 = AutoTuneState(retried: true)
        hm2 = AutoTuneEngine.advance(hm2, report: report("killed", stage: 5, seconds: 90, matched: ["swap-12gb-memory-kill", pool]),
                                     rules: rules, userKeys: [], fingerprint: "hm2", log: nil)
        expect(hm2.candidate == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"],
               "without a swap file of the player's the swap step still comes first")

        // 15b. A failure that comes back the same every start (a noRetry rule, the JIT pool filling up):
        // no unchanged retry; nocopy first, then the split pool (Counter-Strike 2, 2026-10-20).
        let nocopy = "x64-image-nocopy", split = "pool-split-exhausted"
        expect(rules.first { $0.id == split }?.noRetry == true && rules.first { $0.id == nocopy }?.noRetry == true,
               "the JIT pool rules skip the retry")
        var p = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 2, matched: [nocopy, split]),
                                       rules: rules, userKeys: [], fingerprint: "n", log: nil)
        expect(!p.retried && p.candidateRule == nocopy && p.candidate == ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"],
               "a full JIT pool gets its first line at once (\(p.candidateRule ?? "-"))")
        p = AutoTuneEngine.advance(p, report: report("crashed", stage: 2, matched: [nocopy, split]), rules: rules,
                                   userKeys: [], fingerprint: "n", log: nil)
        expect(p.candidateRule == split && p.candidate == ["pool-split = 1"], "then the split pool")
        let plain = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                           userKeys: [], fingerprint: "n2", log: nil)
        expect(plain.candidateRule == "retry", "other failures still get the unchanged retry first")

        // 16. A crash only a change in Madeira fixes (a fixedIn rule): no attempt, not even the retry.
        let layer = "metal-layer-format"
        expect(rules.first { $0.id == layer }?.fixedIn == 560 && rules.first { $0.id == layer }?.lines == [],
               "Warcraft III's layer-format crash is a rule with no lines, fixed in 560")
        var k = AutoTuneState()
        var old = report("crashed", stage: 2, matched: [layer, cage])
        old.build = 559
        k = AutoTuneEngine.advance(k, report: old, rules: rules, userKeys: [], fingerprint: "p", log: nil)
        expect(k.status == "known" && k.candidate == nil && k.candidateRule == nil && !k.retried && k.tried.isEmpty
               && k.known == layer && k.knownFixedIn == 560 && k.knownBuild == 559 && k.layerLines.isEmpty,
               "the best match is a known crash: auto-tune stops, no retry, no line (\(k.status))")
        expect(k.knownAdvice.contains("Fixed in build 560: update BCD."), "on an older build it says to update")
        var newer = old
        newer.build = 561
        let k2 = AutoTuneEngine.advance(AutoTuneState(), report: newer, rules: rules, userKeys: [], fingerprint: "p", log: nil)
        expect(k2.status == "known" && k2.knownAdvice.contains("export the report"),
               "on a build that has the fix it asks for the report instead")
        var later = AutoTuneState(retried: true)
        later = AutoTuneEngine.advance(later, report: report("crashed", stage: 3, matched: [cage, layer]), rules: rules,
                                       userKeys: [], fingerprint: "q", log: nil)
        expect(later.status == "trying" && later.candidateRule == cage,
               "a line rule ranked above the known crash is still tried first")
        k = AutoTuneEngine.advance(k, report: report("quit", stage: 5, seconds: 900), rules: rules, userKeys: [],
                                   fingerprint: "p", log: nil)
        expect(k.status == "ok", "after the update the game plays: nothing was added")
        let tmp2 = FileManager.default.temporaryDirectory.appendingPathComponent("autotune-known-\(getpid()).json")
        try? Data("""
        [{"id": "x-known", "lines": [], "fixedIn": 999, "priority": 2, "all": [{"has": ["x"]}], "reason": "r"},
         {"id": "metal-layer-format", "lines": [], "priority": 1, "all": [{"has": ["x"]}], "reason": "off"}]
        """.utf8).write(to: tmp2)
        let withKnown = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: tmp2)
        try? FileManager.default.removeItem(at: tmp2)
        expect(withKnown.contains { $0.id == "x-known" && $0.fixedIn == 999 } && !withKnown.contains { $0.id == layer },
               "an override may add a fixedIn rule; lines [] without fixedIn still disables one")
        let objc = AutoTuneLogScanner(signals: [])
        objc.feed(Data("*** Terminating app due to uncaught exception 'CAMetalLayerInvalid', reason: 'invalid pixel format 40'\n".utf8))
        objc.feed(Data("[redeliv] terminating process rev=ml465\n".utf8))
        objc.finish()
        expect(objc.facts.errorExits == 2, "the app's own end (an Objective-C exception, the redelivery guard) is crash evidence")

        // 17. Wine Mono missing (Panzer Paladin, 2026-10-20): the game page says where to download it. Only the err line
        // of mscoree counts; the "[wine-mono] ml1275 neither ..." line is written at every start of a device without
        // Mono, for games that do not need .NET too.
        let monoErr = "00a4:err:mscoree:CLRRuntimeInfo_GetRuntimeHost Wine Mono is not installed\n"
        let monoLine = "[wine-mono] ml1275 neither this build nor a download has Wine Mono\n"
        let monoLineNew = "[wine-mono] ml1275 neither this build nor a download has Wine Mono -- a .NET Framework game then exits 0xffffffff with \"Wine Mono is not installed\"; Settings > .NET Framework downloads it\n"
        let otherMscoree = "00a4:err:mscoree:CLRRuntimeInfo_GetRuntimeHost could not load the runtime\n"
        func scanned(_ text: String, chunk: Int = 1 << 20) -> AutoTuneLogScanner {
            let sc = AutoTuneLogScanner(signals: [])
            let bytes = Data(text.utf8)
            var i = 0
            while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
            sc.finish()
            return sc
        }
        for chunk in [1, 5, 64, 1 << 20] {
            expect(scanned("[phase] wine-start t+ 0.7s\n" + monoLine + monoErr + "[exit-report] status=0xFFFFFFFF\n", chunk: chunk).facts.monoMissing,
                   "the scanner sees the mscoree err line (\(chunk)-byte chunks)")
        }
        expect(!scanned(monoLine + monoLineNew).facts.monoMissing,
               "the [wine-mono] ml1275 line alone (old or new wording) is no evidence: every start of a device without Mono writes it")
        expect(!scanned(otherMscoree + "00a4:fixme:mscoree:parse_supported_runtime sku=L\".NETFramework,Version=v4.5.2\" not implemented\n").facts.monoMissing,
               "other mscoree lines are not it")
        let failedEnd = AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 40)
        let monoScan = scanned("[phase] wine-start t+ 0.7s\n" + monoErr + "[exit-report] status=0xFFFFFFFF\n")
        let monoReport = AutoTuneEngine.report(failedEnd, monoScan, rules: rules, signals: [])
        expect(monoReport.failed && monoReport.monoMissing == true, "a failed session that showed it: the report says Wine Mono is missing (\(monoReport.outcome))")
        let playedScan = scanned("[phase] wine-start t+ 0.7s\n" + monoErr + "[freeze] alive t+305s beats\n")
        let playedReport = AutoTuneEngine.report(AutoTuneSessionEnd(sawProcess: true, seconds: 400), playedScan, rules: rules, signals: [])
        expect(playedReport.played && playedReport.monoMissing == nil,
               "a session that played shows nothing, whatever a side process logged (\(playedReport.outcome))")
        let plainReport = AutoTuneEngine.report(failedEnd, scanned("[phase] wine-start t+ 0.7s\n" + monoLine + "[exit-report] status=0xC0000005\n"),
                                                rules: rules, signals: [])
        expect(plainReport.failed && plainReport.monoMissing == nil, "a failure without the err line shows nothing, the ml1275 line or not")
        var mono = AutoTuneEngine.advance(AutoTuneState(), report: monoReport, rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == true, "the state keeps it for the game page")
        mono = AutoTuneEngine.advance(mono, report: report("inconclusive", stage: 1), rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == true, "an inconclusive session changes nothing")
        mono = AutoTuneEngine.advance(mono, report: monoReport, rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == true && mono.status == "stuck", "it stays while the failure comes back; auto-tune itself behaves as before (\(mono.status))")
        if let d = try? JSONEncoder().encode(mono), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
            expect(back == mono && back.monoMissing == true, "the flag round-trips through JSON")
        } else { expect(false, "the Mono state encodes") }
        mono = AutoTuneEngine.advance(mono, report: report("quit", stage: 5, seconds: 400), rules: rules, userKeys: [], fingerprint: "mono", log: nil)
        expect(mono.monoMissing == nil, "a session that played clears it")
        let neverMono = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 3, matched: [cage]), rules: rules,
                                               userKeys: [], fingerprint: "n3", log: nil)
        expect(neverMono.monoMissing == nil, "a game that never showed it has none")
        if let d = try? JSONEncoder().encode(neverMono) {
            expect(!String(decoding: d, as: UTF8.self).contains("monoMissing"), "and its saved file is the same as before: no new key")
        } else { expect(false, "the plain state encodes") }
        let savedBefore = Data(#"{"version":1,"status":"found","kept":["x = 1"],"tried":[],"retried":false,"attempts":[],"best":{"outcome":"crashed","stage":3,"seconds":30,"presents":0,"dock":false,"d3d12":false,"matched":[],"evidence":[],"dialogs":[]}}"#.utf8)
        let loaded = try? JSONDecoder().decode(AutoTuneState.self, from: savedBefore)
        expect(loaded?.kept == ["x = 1"] && loaded?.monoMissing == nil && loaded?.best?.monoMissing == nil,
               "a state saved before the flag existed still loads, with its kept lines")

        // 18. Round 579, the false positives the logs of 2026-10-21 showed. Each guard only narrows when a rule fires or how
        // a session that is no game's is scored; the first half of every pair is the case it must still catch.
        do {
            let allSignals = AutoTuneEngine.signals(rules)
            func scan(_ text: String) -> AutoTuneLogScanner {
                let sc = AutoTuneLogScanner(signals: allSignals)
                sc.feed(Data(text.utf8)); sc.finish()
                return sc
            }
            func session(_ text: String, end: AutoTuneSessionEnd = AutoTuneSessionEnd(sawProcess: true, seconds: 30)) -> AutoTuneReport {
                AutoTuneEngine.report(end, scan(text), rules: rules, signals: allSignals)
            }
            let head = "[build] v0.1.578 (578)\n[phase] wine-start             t+  0.639s rev=ml526\n"
                + "D 24 Load module Game.exe (game.exe-0123456789abcdef): 140000000\n"
            func exitLine(_ code: String) -> String {
                "0024:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=\(code)) syscalls=0 unix_calls=4158\n"
            }

            // free-delay-off needs a held release of its own (Minecraft Dungeons CYDN1E: 458,752 refused probes, no hold at all).
            let refused = (1...210).map {
                "[alloc-fail] ml814 #\($0) status=c0000018 hint=0x71f5632000 size=0x1000 type=0x3000 protect=0x40 tid=00a8 | STAGE=fixed-map err=3 at 0x73ffe40000+0x1ac000\n"
            }.joined()
            let held = "[free-delay] #1 release of 0x7300000000+0x200000 held for 2000 ms (1 in flight)\n"
            expect(session(head + refused + exitLine("0xc0000005")).matched.contains("free-delay-off") == false,
                   "200+ refused c0000018 requests without a [free-delay] line: not the free-delay trap")
            expect(session(head + held + refused + exitLine("0xc0000005")).matched.contains("free-delay-off"),
                   "the same with a held release in the log: it is")
            expect(session(head + "[free-delay] #3 reservation at 0x7300000000+0x200000: 1 held release(s) from 0x7300000000 (0x3ff000 bytes) freed at once\n"
                           + refused + exitLine("0xc0000005")).matched.contains("free-delay-off"),
                   "and a reservation line of the hold's reuse path counts as well")

            // exe-window-fixed-base: a one-time installer's helper in %TEMP% is not the game's program.
            let gameMain = "wine: failed to create main module for L\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Game\\\\Game.exe\", status c0000018\n"
            let helperMain = "wine: failed to create main module for L\"C:\\\\users\\\\<user>\\\\AppData\\\\Local\\\\Temp\\\\DXa9e1.tmp\\\\infinst.exe\", status c0000018\n"
            expect(session(head + gameMain + exitLine("0xc0000018")).matched.contains("exe-window-fixed-base"),
                   "the game's own main module refused (c0000018): the window rule still fires")
            expect(!session(head + helperMain + exitLine("0xc0000018")).matched.contains("exe-window-fixed-base"),
                   "the DirectX setup's infinst.exe in %TEMP% failing the same way: it does not")
            expect(!session(head + helperMain.replacingOccurrences(of: "Temp\\\\DXa9e1.tmp\\\\", with: "") + exitLine("0xc0000018")).matched.contains("exe-window-fixed-base"),
                   "the helper's file name alone is enough")

            // thread-stack-spill: not for a 32-bit program's stack, which the line cannot move (Shade 6NE7KH).
            let kernelStack = "[thr-create] FAILED at kernel stack: status=0xc0000017 pthread=0(-) reserve=0x0 commit=0x8000 creator pid=0020 tid=0024 peb=0x71ffff0000; task threads 101\n"
            let wowStack = "[thr-create] FAILED at WoW64 32-bit stack: status=0xc0000017 pthread=0(-) reserve=0x100000 commit=0x0 creator pid=0020 tid=0028 peb=0x47ffd0000; task threads 28\n"
            expect(session(head + kernelStack + exitLine("0xc0000005")).matched.contains("thread-stack-spill"),
                   "a thread's kernel stack refused: the spill rule still fires")
            expect(!session(head + wowStack + exitLine("0x3")).matched.contains("thread-stack-spill"),
                   "a WoW64 32-bit stack refused inside its window: it does not")
            expect(!session(head + kernelStack + wowStack + exitLine("0x3")).matched.contains("thread-stack-spill"),
                   "nor when that is one of the session's refusals")

            // A session that only ran the one-time installs, and whose batch ended, is no session of the game.
            let installsOnly = "[madeira-dock] app 271590: this session runs only the one-time installs; the host starts after a restart\n"
            let installsEnd = "[dock-installers] end succeeded=2 failed=0 of=2; the session ends, the game starts after a restart\n"
            let byItself = AutoTuneSessionEnd(sawProcess: true, seconds: 60, killed: true, userSaid: "closed-by-itself")
            let footprint = "[footprint] rev=ml358 phys=2116 MB (peak 2272) internal=1572 MB compressed=541 MB external=316 MB reusable=31 MB (cycle=21)\n"
            let dock = "[dock-launch] app 271590\n"
            expect(session(head + dock + footprint + installsOnly, end: byItself).outcome == "killed",
                   "an installs-only session whose batch never ended and whose user said it closed by itself: scored as before")
            expect(scan(head + dock + installsOnly).facts.installsOnly && !scan(head + dock + installsOnly).facts.installsOver,
                   "the scanner sees the installs-only line, and the batch is not over without its end line")
            for chunk in [1, 5, 64] {
                let sc = AutoTuneLogScanner(signals: allSignals)
                let bytes = Data((head + installsOnly + installsEnd).utf8)
                var i = 0
                while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
                sc.finish()
                expect(sc.facts.installsOver, "installs-only and its end line make the session over (\(chunk)-byte chunks)")
            }
            expect(session(head + dock + footprint + installsOnly + installsEnd, end: byItself).outcome == "inconclusive",
                   "the install batch ended and BCD closed itself for a restart: inconclusive, not a memory kill (NM88NQ)")
            let overReport = session(head + dock + footprint + installsOnly + installsEnd + helperMain + exitLine("0xc0000018"))
            expect(overReport.outcome == "inconclusive" && overReport.matched.isEmpty,
                   "an installer's helper failing in such a session: inconclusive, no rule (T3P8HK)")
            let overState = AutoTuneEngine.advance(AutoTuneState(), report: overReport, rules: rules, userKeys: [], fingerprint: "i", log: nil)
            expect(overState.candidate == nil && overState.kept.isEmpty && overState.best == nil && overState.status == "baseline",
                   "and the search has nothing to start from")
            expect(session(head + dock + footprint + installsEnd + exitLine("0xc0000005")).outcome == "crashed",
                   "the end line without the installs-only line (the host follows the installs in the same session): scored normally")
            expect(session(head + dock + footprint + installsOnly, end: AutoTuneSessionEnd(sawProcess: true, seconds: 68, killed: true, userSaid: "froze")).outcome == "hung",
                   "an installer that never ends and a user who says it froze: still a hang (Arkham City's .NET setup)")

            // A window title that only reads like an error box is not one (Hotline Miami's GameMaker form).
            expect(scan(head + "[win-name] #56 hwnd=0x100ae class='' text=\"Error Messages\" rev=ml853\n").facts.dialogs.isEmpty,
                   "GameMaker's hidden \"Error Messages\" form is no error box")
            expect(scan(head + "[win-name] #56 hwnd=0x100ae class='' text=\"Error Messages\" rev=ml853\n"
                        + "[win-name] #57 hwnd=0x100b0 class='' text=\"Critical error\" rev=ml853\n").facts.dialogs == ["Critical error"],
                   "a real caption beside it still is")
            let exitsAlone = session(head + "[launch-view] dismissed reason=present logs=0\n"
                                     + "[win-name] #56 hwnd=0x100ae class='' text=\"Error Messages\" rev=ml853\n" + exitLine("0x0"))
            expect(exitsAlone.outcome == "exited-early" && exitsAlone.dialogs.isEmpty,
                   "a game that ends itself with exit 0 and shows nothing but that form: exited early, not crashed (\(exitsAlone.outcome))")
        }
        // 19. One huge image that got no JIT-pool copy (Stellar Blade Demo's 314 MiB exe, YBX2WT, and FINAL FANTASY RESONANCE
        // DEMO's 474 MiB exe, EJDV7Z, build 576): no region of a split pool takes it, so pool-split-exhausted (imageMaxMB 300)
        // does not match; x64-image-nocopy does. A small image keeps both rules, as does an anonymous allocation that failed.
        expect(rules.first { $0.id == split }?.imageMaxMB == 300 && rules.first { $0.id == nocopy }?.imageMaxMB == nil,
               "only the split pool's rule carries the image size bar")
        let allSignals = AutoTuneEngine.signals(rules)
        func poolFail(_ line: String) -> AutoTuneReport {
            let sc = AutoTuneLogScanner(signals: allSignals)
            sc.feed(Data(("[phase] wine-start t+ 0.7s\n" + line + "\n"
                          + "00ac:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0xc0000017) syscalls=0 unix_calls=8210\n").utf8))
            sc.finish()
            return AutoTuneEngine.report(AutoTuneSessionEnd(sawProcess: true, seconds: 5), sc, rules: rules, signals: allSignals)
        }
        let tail = " want=0x13a48000 bump=0xbb44000/0x2bd30000 tail_resv=0x2008000 freelist=0 \u{2014} FAILING the load (was: silent BUS loop)"
        let huge = poolFail("[jit-pool] EXHAUSTED (image 0x140000000+0x13a43000):" + tail)
        expect(huge.jitImageFailMB == 314 && huge.matched.contains(nocopy) && !huge.matched.contains(split),
               "a 314 MiB image: x64-image-nocopy only (\(huge.matched), \(huge.jitImageFailMB ?? -1) MiB)")
        let small = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x4a36000):" + tail)
        expect(small.jitImageFailMB == 74 && small.matched.contains(nocopy) && small.matched.contains(split),
               "a 74 MiB DLL keeps both rules (\(small.matched))")
        let edge = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x12c00000):" + tail)
        let edgePlus = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x12d00000):" + tail)
        expect(edge.matched.contains(split) && !edgePlus.matched.contains(split),
               "300 MiB passes the bar, 301 MiB does not (\(edge.jitImageFailMB ?? -1), \(edgePlus.jitImageFailMB ?? -1))")
        let anon = poolFail("[jit-pool] EXHAUSTED (anon RWX): want=0x40000000 bump=0x10000000/0x25000000 tail_resv=0x0 freelist=0 \u{2014} FAILING allocation")
        expect(anon.jitImageFailMB == nil && anon.matched.contains(split) && !anon.matched.contains(nocopy),
               "an anonymous allocation that failed has no image size: pool-split still matches (\(anon.matched))")
        let both = poolFail("[jit-pool] EXHAUSTED (image 0x71e5950000+0x4a36000):" + tail + "\n[jit-pool] EXHAUSTED (image 0x140000000+0x1da1b000):" + tail)
        expect(both.jitImageFailMB == 474 && !both.matched.contains(split),
               "the largest image counts, whatever order the lines come in (\(both.jitImageFailMB ?? -1) MiB)")
        for chunk in [1, 7, 4096] {
            expect(scanned("[jit-pool] EXHAUSTED (image 0x140000000+0x13a43000):" + tail + "\n", chunk: chunk).facts.jitImageFailMB == 314,
                   "the scanner reads the image size across \(chunk)-byte chunks")
        }
        var hugeState = AutoTuneEngine.advance(AutoTuneState(), report: huge, rules: rules, userKeys: [], fingerprint: "huge", log: nil)
        expect(!hugeState.retried && hugeState.candidateRule == nocopy, "a huge image gets x64-image-nocopy at once (\(hugeState.candidateRule ?? "-"))")
        hugeState = AutoTuneEngine.advance(hugeState, report: huge, rules: rules, userKeys: [], fingerprint: "huge", log: nil)
        expect(hugeState.status == "stuck" && hugeState.candidate == nil,
               "and when that did not help there is no futile pool-split attempt after it (\(hugeState.status))")
        let listed = AutoTuneEngine.advance(AutoTuneState(), report: huge, rules: rules,
                                            userKeys: ["env.MADEIRA_X64_IMAGE_NOCOPY"], fingerprint: "huge2", log: nil)
        expect(listed.status == "stuck" && listed.candidate == nil,
               "with the line already in the game's list, a huge image has no attempt left (\(listed.status))")
        if let d = try? JSONEncoder().encode(huge), let back = try? JSONDecoder().decode(AutoTuneReport.self, from: d) {
            expect(back == huge && back.jitImageFailMB == 314, "the image size round-trips through JSON")
        } else { expect(false, "the report encodes") }

        // 20. A .NET 7+ game whose garbage collector cannot reserve its 256 GB range (Unrailed! 2: Back on Track, CR8H6H, build 576):
        // DOTNET_GCRegionRange = 400000000 (16 GB); not again when the session already ran with it.
        let gcRule = "dotnet-gc-region-range"
        func crashed(_ lines: String) -> AutoTuneReport {
            let sc = AutoTuneLogScanner(signals: allSignals)
            sc.feed(Data(("[phase] wine-start t+ 0.7s\n" + lines
                          + "00ac:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0xe0434352) syscalls=0 unix_calls=8210\n").utf8))
            sc.finish()
            return AutoTuneEngine.report(AutoTuneSessionEnd(sawProcess: true, seconds: 5), sc, rules: rules, signals: allSignals)
        }
        let gcFail = "00ac:err:eventlog:ReportEventW L\"Message: GC heap initialization failed with error 0x8007000E\\n\"\n"
        let gcOne = crashed(gcFail)
        expect(gcOne.matched == [gcRule] && rules.first { $0.id == gcRule }?.lines == ["env.DOTNET_GCRegionRange = 400000000"],
               "the GC's out-of-memory at initialisation gets the 16 GB range (\(gcOne.matched))")
        var gcState = AutoTuneEngine.advance(AutoTuneState(), report: gcOne, rules: rules, userKeys: [], fingerprint: "gc", log: nil)
        expect(!gcState.retried && gcState.candidateRule == gcRule, "at once, no unchanged retry (\(gcState.candidateRule ?? "-"))")
        expect(crashed("[madeira-env] game DOTNET_GCRegionRange=400000000\n" + gcFail).matched.isEmpty,
               "a session that already ran with the line does not match again")
        expect(crashed("00ac:err:eventlog:ReportEventW L\"Message: GC heap initialization failed with error 0x80070057\\n\"\n").matched.isEmpty
               && crashed("00ac:err:eventlog:ReportEventW L\"Message: something else\\n\"\n").matched.isEmpty,
               "another GC initialisation error, or another event, is not it")
        gcState = AutoTuneEngine.advance(gcState, report: gcOne, rules: rules, userKeys: [], fingerprint: "gc", log: nil)
        expect(gcState.status == "stuck", "and when the line did not help, nothing else is tried for it (\(gcState.status))")

        // 21. Round 580, the tur580 logs. Each guard only narrows when a rule fires, how a session that is no game's is scored, or
        // which attempts are spent again; the first half of every pair is the case it must still catch.
        do {
            let sigs = AutoTuneEngine.signals(rules)
            func scan(_ text: String, chunk: Int = 1 << 20) -> AutoTuneLogScanner {
                let sc = AutoTuneLogScanner(signals: sigs)
                let bytes = Data(text.utf8)
                var i = 0
                while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
                sc.finish()
                return sc
            }
            func session(_ text: String, end: AutoTuneSessionEnd = AutoTuneSessionEnd(sawProcess: true, seconds: 30)) -> AutoTuneReport {
                AutoTuneEngine.report(end, scan(text), rules: rules, signals: sigs)
            }
            let head = "[build] v0.1.579 (579)\n[phase] wine-start             t+  0.639s rev=ml526\n"
                + "D 24 Load module Game.exe (game.exe-0123456789abcdef): 140000000\n"
            func exitLine(_ code: String) -> String {
                "0024:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=\(code)) syscalls=0 unix_calls=4158\n"
            }
            let nocopyId = "x64-image-nocopy", smc = "x64-image-smc-nocopy", exeRule = "exe-window-fixed-base"

            // exe-window-fixed-base: a program of 512 MiB or more is more than the 512 MB window of its line (Persona 4 Golden, 888 MiB).
            func mapImage(_ size: String, base: String = "0x140000000", charact: String = "0x23") -> String {
                "ml985: map_image_view size=\(size) base=\(base) info_base=\(base) map_addr=0 flags=0 charact=\(charact) limits=0x10000..0x73fffeffff\n"
            }
            let mainFail = "wine: failed to create main module for L\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Game\\\\Game.exe\", status c0000018\n"
            expect(rules.first { $0.id == exeRule }?.exeMaxMB == 511, "the window rule carries the 511 MiB bar")
            let big = session(head + mapImage("0x378b2000") + mainFail + exitLine("0xc0000018"))
            expect(big.fixedExeMB == 888 && !big.matched.contains(exeRule), "an 888 MiB program: no window to try (\(big.matched), \(big.fixedExeMB ?? -1) MiB)")
            expect(session(head + mapImage("0x18c91000") + mainFail + exitLine("0xc0000018")).matched.contains(exeRule),
                   "a 397 MiB program (Yakuza: Like a Dragon) keeps the rule")
            expect(session(head + mapImage("0x1fffffff") + mainFail + exitLine("0xc0000018")).matched.contains(exeRule)
                   && !session(head + mapImage("0x20000000") + mainFail + exitLine("0xc0000018")).matched.contains(exeRule),
                   "511 MiB (rounded down) passes the bar, 512 MiB does not")
            expect(session(head + mapImage("0x378b2000", base: "0x7133520000") + mainFail + exitLine("0xc0000018")).matched.contains(exeRule)
                   && session(head + mapImage("0x378b2000", charact: "0x22") + mainFail + exitLine("0xc0000018")).matched.contains(exeRule),
                   "a big image that is not the fixed-base main program (another base, relocations kept) does not count")
            for chunk in [1, 7, 4096] {
                expect(scan(mapImage("0x378b2000") + mapImage("0x18c91000"), chunk: chunk).facts.fixedExeMB == 888,
                       "the scanner reads the program size across \(chunk)-byte chunks, the largest wins")
            }

            // dotnet-gc-region-range: not on the 454 GB map of an install without the memory entitlements, which has no gap for any range.
            let gcFail = "00ac:err:eventlog:ReportEventW L\"Message: GC heap initialization failed with error 0x8007000E\\n\"\n"
            let map512 = "[device] address-map=[0x102aa0000,0x8000000000) 512GB increased-memory=1 extended-va=1\n"
            let map454 = "[device] address-map=[0x100b5c000,0x7180000000) 454GB increased-memory=0 extended-va=0\n"
            expect(session(head + map512 + gcFail + exitLine("0xe0434352")).matched == ["dotnet-gc-region-range"],
                   "a 512 GB map keeps the GC rule")
            expect(session(head + map454 + gcFail + exitLine("0xe0434352")).matched.isEmpty,
                   "the 454 GB map of an install without memory rights: no try (largest gap 513 MB, Fortune Mill)")

            // x64-image-smc-nocopy: the Mach handler's 200th unhandled fault is enough where the redelivery guard's line never came.
            let storeNoalias = "[store-noalias] #1 rev=ml348 addr=0x140010122 insn=0x19000028 pc=0x16baa957c NO pool/anon alias | region 0x140010000+0x4000 prot=1 max=3 (prot without W = exec-downgraded page that never got dual-mapped)\n"
            let stripDeclined = "ml998: [wr-strip-declined]   view 0x140000000+0x56f4000 protect=0x180002d { SEC_IMAGE WRITECOPY  } page_off=0x10000\n"
            let unhandled5 = "[mach_exc] UNHANDLED #5 pc=0x16baa957c addr=0x140010122 x18=0x71fff00000 type=1 lr=0x16bff8354 sp=0x71f4d40000 x16=0x14185997d x17=0x14185997d\n"
            let unhandled200 = "[mach_exc] UNHANDLED #200 pc=0x16baa957c addr=0x140010122 x18=0x71fff00000 type=1 lr=0x16bff8354 sp=0x71f4d40000 x16=0x14185997d x17=0x14185997d\n"
            expect(!session(head + storeNoalias + stripDeclined + unhandled5 + exitLine("0xc0000005")).matched.contains(smc),
                   "an exec-downgraded image page and a handful of unhandled faults: recovered noise, no match")
            expect(session(head + storeNoalias + stripDeclined + unhandled5 + unhandled200 + exitLine("0xc0000005")).matched.contains(smc),
                   "the 200th unhandled fault (DARK SOULS III) matches")
            expect(session(head + storeNoalias + stripDeclined + "[redeliv] 256 identical redeliveries of the same fault\n" + exitLine("0xc0000005")).matched.contains(smc),
                   "and so does the redelivery guard's line, as before")
            expect(!session(head + storeNoalias + unhandled200 + exitLine("0xc0000005")).matched.contains(smc)
                   && !session(head + stripDeclined + unhandled200 + exitLine("0xc0000005")).matched.contains(smc),
                   "the fault and the declined strip are both needed")
            expect(!session(head + "[x64-image] L\"Game.exe\" 0x140000000+0x56f4000 mapped without a pool copy (pure x64)\n" + storeNoalias + stripDeclined + unhandled200 + exitLine("0xc0000005")).matched.contains(smc),
                   "an image that already runs without a copy does not match")

            // fex-avx-on: after a store the emulator could not do, or on a UDF data word, c000001d is no AVX instruction; FEX's HLT is.
            let avxHead = head + "[bcd] library launch Game: avx=0 wine-vcrt=0 nvidia=0 game-config=1 metalfx=off\n"
            let c1d = "0024:err:seh:call_seh_handlers [exc] ml811 code=c000001d addr=0000000152190110 flags=0 nparams=0 tid=0024 hit=1\n"
            func insnStream(_ word: String) -> String { "[mach_exc] insn_stream PC-12..PC+8: f946a108 d63f0100 00000000 [\(word)] 7100027f 9a9f0100 f9400ffe\n" }
            expect(session(avxHead + insnStream("d4400000") + c1d + exitLine("0xc000001d")).matched.contains("fex-avx-on"),
                   "FEX's HLT before the c000001d (The Last of Us Part I): AVX on, as before")
            expect(!session(avxHead + insnStream("00000036") + c1d + exitLine("0xc000001d")).matched.contains("fex-avx-on"),
                   "a UDF data word (Portal): not AVX")
            expect(!session(avxHead + "[store-undecoded] #1 rev=ml350 insn=0xc8e8ff60 pc=0x16d2d21d4 addr=0x7193ab4510 rw_addr=0x700cb6c510 \u{2014} alias OK, ADD THIS ENCODING to the case-4 emulator\n"
                            + c1d + exitLine("0xc000001d")).matched.contains("fex-avx-on"),
                   "a store the emulator could not do (Jetboard Joust): not AVX")

            // Scanner and verdicts. A Wine process that ended with code 0 ends the crash evidence; faults after it are teardown (Shade).
            let presenting = "Game is presenting (#1\nEarly detach: game presenting and settled\n"
            let cleanExit = "[WineProc] Wine exited with code 0 (caught by longjmp)\n"
            let lateFault = "E 34 NoExec instruction in entry block: 157B3AD\nwine: Unhandled exception code c0000005\n"
                + exitLine("0xc0000005") + "[exit-report] status=0xc0000005\n"
            let shade = scan(head + presenting + cleanExit + lateFault)
            expect(shade.facts.cleanMainExit && shade.facts.unhandled == 0 && shade.facts.errorExits == 0 && shade.facts.exitStatus == nil,
                   "faults, error exits and the exit report after a clean exit of the Wine process count for nothing")
            let shadeEnd = AutoTuneSessionEnd(sawProcess: true, crashStatus: 0xC0000005, seconds: 78)
            expect(session(head + presenting + cleanExit + lateFault, end: shadeEnd).outcome == "ran",
                   "and neither does the app's crash status of such a run: it ran (Shade, 75 s of play)")
            expect(session(head + presenting + "wine: Unhandled exception code c0000005\n" + cleanExit, end: shadeEnd).outcome == "crashed",
                   "a fault BEFORE the clean exit is still a crash")
            expect(!scan(head + "[WineProc] Wine exited with code 1 (caught by longjmp)\n" + lateFault).facts.cleanMainExit
                   && !scan(head + "[WineProc] Wine exited with code 01 (caught by longjmp)\n").facts.cleanMainExit
                   && !scan(head + "[WineProc] Wine exited with code -1073741819 (caught by longjmp)\n").facts.cleanMainExit,
                   "only code 0 is a clean exit")
            for chunk in [1, 7, 4096] {
                expect(scan(head + cleanExit + lateFault, chunk: chunk).facts.cleanMainExit, "the clean exit is read across \(chunk)-byte chunks")
            }

            // The user left: the last focus line says the keyboard went elsewhere because the app went inactive or the pointer left.
            let away = "[hwinput] keyboard focus: elsewhere (app inactive)\n"
            let back = "[hwinput] keyboard focus: program (app active)\n"
            let pointer = "[hwinput] keyboard focus: elsewhere (pointer left the game view)\n"
            expect(scan(away).facts.focusLeft && scan(away + back).facts.focusLeft == false && scan(away + back + pointer).facts.focusLeft
                   && !scan("[hwinput] keyboard focus: elsewhere (poll)\n").facts.focusLeft,
                   "the scanner follows the last keyboard focus line; only 'app inactive' and 'pointer left the game view' are the user leaving")
            expect(session(head + away).outcome == "inconclusive",
                   "an early end with no crash, no quit and no rule, the user away: inconclusive, not a failure (Final Fantasy Tactics, 19 s)")
            expect(session(head + away, end: AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 20)).outcome == "exited-early",
                   "a user who quit before the game was up keeps the old verdict")
            expect(session(head + away + "[dock-launch] app 12210\n[svc] Unable to open SC manager! (1722)\n").outcome == "exited-early",
                   "a session that shows a known failure keeps its verdict whoever left")
            expect(session(head + "wine: Unhandled exception code c0000005\n" + away).outcome == "crashed", "and a crash before the user left is a crash")

            // A Dock session whose frame counter stayed at 0 and in which nothing printed a frame: neither the clock nor the launch cover
            // coming down to the desktop is a played game (Call of Duty HQ, 331 s). The stage keeps rising with them (rules and fixtures
            // read it); the verdict does not.
            let waiting = "detach-wait: presents=0 running=1 elapsed=330s\n[freeze] alive t+331s beats\n"
            let desktop = "[launch-view] dismissed reason=show-desktop logs=0\n"
            let noFrames = scan(head + waiting + desktop)
            expect(noFrames.facts.noFrames && noFrames.facts.stage == 5 && !noFrames.facts.frameSeen,
                   "331 s of presents=0 and the cover to the desktop: stage \(noFrames.facts.stage), but no frame was seen")
            // Round 580 (lead): the outcome does NOT use noFrames (a game drawing without the Dock's counter, OpenGL or GDI,
            // would read as not run and get lines); only the round 577 rule stands: no frame AND under stage 3.
            expect(session(head + waiting + desktop).outcome == "ran", "stage 5 by the clock still counts as run (\(session(head + waiting + desktop).outcome))")
            expect(session(head + "detach-wait: presents=620 running=1 elapsed=30s\n" + desktop).outcome == "ran"
                   && scan(head + "detach-wait: presents=620 running=1 elapsed=30s\n").facts.frameSeen,
                   "with frames counted, nothing changes")

            // better(): the pool failure gone and the modules loaded beats a start that could not load, whatever stage and clock say.
            var baseline = report("crashed", stage: 3, seconds: 60, matched: [nocopyId])
            baseline.jitImageFailMB = 384
            let loaded = report("crashed", stage: 2, seconds: 21, matched: [])
            expect(AutoTuneEngine.better(loaded, than: baseline, tried: nocopyId), "RAIDOU Remastered: the start that loaded counts as further")
            expect(!AutoTuneEngine.better(loaded, than: baseline), "the line must be named (the unchanged retry does not get this)")
            expect(!AutoTuneEngine.better(report("crashed", stage: 2, seconds: 21, matched: [nocopyId]), than: baseline, tried: nocopyId),
                   "the rule's own failure still in the log: no")
            var stillFailing = report("crashed", stage: 2, seconds: 21, matched: [])
            stillFailing.jitImageFailMB = 120
            expect(!AutoTuneEngine.better(stillFailing, than: baseline, tried: nocopyId), "another image refused: no")
            expect(!AutoTuneEngine.better(report("crashed", stage: 1, seconds: 21, matched: []), than: baseline, tried: nocopyId),
                   "a start whose modules never loaded: no")
            var noPool = baseline
            noPool.jitImageFailMB = nil
            expect(!AutoTuneEngine.better(loaded, than: noPool, tried: nocopyId), "a baseline that was not a pool failure: no")
            var raidou = AutoTuneEngine.advance(AutoTuneState(), report: baseline, rules: rules, userKeys: [], fingerprint: "r", log: nil)
            expect(raidou.candidateRule == nocopyId, "RAIDOU: the baseline's pool failure brings the NOCOPY line (\(raidou.candidateRule ?? "-"))")
            raidou = AutoTuneEngine.advance(raidou, report: loaded, rules: rules, userKeys: [], fingerprint: "r", log: nil)
            expect(raidou.kept == ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"] && raidou.attempts.last?.kept == true,
                   "and the line is kept after the run that loaded, not reverted (\(raidou.kept))")

            // A rule that adds the very lines of a rule already tried is not a new try (NOCOPY belongs to three rules).
            var tried = AutoTuneEngine.advance(AutoTuneState(), report: baseline, rules: rules, userKeys: [], fingerprint: "t", log: nil)
            tried = AutoTuneEngine.advance(tried, report: report("crashed", stage: 3, seconds: 31, matched: [nocopyId, smc]), rules: rules, userKeys: [], fingerprint: "t", log: nil)
            expect(tried.attempts.last?.kept == false && tried.candidate == nil && tried.status == "stuck",
                   "NOCOPY tried and reverted: the rule with the same line is not tried after it (\(tried.status), \(tried.candidateRule ?? "-"))")
            var smcFirst = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 3, matched: [smc]), rules: rules, userKeys: [], fingerprint: "t2", log: nil)
            smcFirst = AutoTuneEngine.advance(smcFirst, report: report("crashed", stage: 3, matched: [smc]), rules: rules, userKeys: [], fingerprint: "t2", log: nil)
            expect(smcFirst.candidateRule == smc && smcFirst.candidate == ["env.MADEIRA_X64_IMAGE_NOCOPY = 1"], "but on its own, after the unchanged retry, it is tried")
        }

        // 22. Round 591, the tur591 logs. Each change only narrows when a rule fires, widens a rule to the second case of a failure it already
        // answers, or changes how a session that was playing is scored; the first half of every pair is the case that must stay as it was.
        do {
            let sigs = AutoTuneEngine.signals(rules)
            func scan(_ text: String, chunk: Int = 1 << 20) -> AutoTuneLogScanner {
                let sc = AutoTuneLogScanner(signals: sigs)
                let bytes = Data(text.utf8)
                var i = 0
                while i < bytes.count { sc.feed(bytes.subdata(in: i..<min(i + chunk, bytes.count))); i += chunk }
                sc.finish()
                return sc
            }
            func session(_ text: String, end: AutoTuneSessionEnd = AutoTuneSessionEnd(sawProcess: true, seconds: 30)) -> AutoTuneReport {
                AutoTuneEngine.report(end, scan(text), rules: rules, signals: sigs)
            }
            let head = "[build] v0.1.580 (580)\n[phase] wine-start             t+  0.639s rev=ml526\n"
                + "D 24 Load module Game.exe (game.exe-0123456789abcdef): 140000000\n"
            func exitLine(_ code: String) -> String {
                "0024:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=\(code)) syscalls=0 unix_calls=4158\n"
            }

            // (a) A game that was playing, closed by the user, then the app killed before it came back: it played.
            var qk = AutoTuneLogFacts(); qk.stage = 5; qk.quitRequested = true; qk.presentsCounted = true; qk.presentsLogged = 8296
            let killedEnd = AutoTuneSessionEnd(killed: true)
            expect(AutoTuneEngine.outcome(killedEnd, qk) == "quit", "a quit after 8,296 frames, then the kill: quit (Cult of the Lamb T0K6FH)")
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "froze"), qk) == "hung", "...unless the user said it froze")
            var qf = qk; qf.freezeGaps = 2
            var qg = qk; qg.gpuErrors = 1
            var q3 = qk; q3.stage = 3
            var qn = qk; qn.presentsCounted = nil
            var qz = qk; qz.presentsLogged = 0
            var qc = qk; qc.crashEvidenceAtQuit = 1
            expect([qf, qg, q3, qn, qz, qc].allSatisfy { AutoTuneEngine.outcome(killedEnd, $0) == "hung" },
                   "a freeze, a GPU error, a stage under 4, no frame counter, no frame, or a crash before the quit: hung, as before")
            var qr = qk; qr.returnedToLibrary = true
            expect(AutoTuneEngine.outcome(killedEnd, qr) == "inconclusive", "the app that came back to the library is not the case: as before")
            var nq = qk; nq.quitRequested = false
            expect(AutoTuneEngine.outcome(killedEnd, nq) == "inconclusive", "no quit line: as before")

            // (b) The recommended list's lines in the game's file: a rule marked as an upgrade may replace them after a memory kill, with a larger number.
            let kill12 = report("killed", stage: 4, seconds: 120, matched: ["swap-12gb-memory-kill"])
            var l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: kill12, rules: rules, userKeys: [], fingerprint: "l1", log: nil,
                                           listValues: ["swap-mb": "3072", "vram-mb": "4096"])
            expect(l.candidate == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"] && l.candidateRule == "swap-12gb-memory-kill",
                   "GTA V Enhanced: the list's swap-mb = 3072 is raised to 6144 after a memory kill (\(l.candidate ?? []))")
            expect(rules.first { $0.id == "swap-12gb-memory-kill" }?.listUpgrade == true && rules.first { $0.id == "swap-12gb-memory-dialog" }?.listUpgrade != true,
                   "only the memory-kill rules carry the mark")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: kill12, rules: rules, userKeys: [], fingerprint: "l2", log: nil,
                                       listValues: ["swap-mb": "8192", "swap-mode": "2", "swap-min-mb": "1"])
            expect(l.status == "stuck" && l.candidate == nil, "a list with more swap than the tier's step (RDR2's 8192) is never lowered")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: kill12, rules: rules, userKeys: [], fingerprint: "l3", log: nil,
                                       listValues: ["swap-mb": "6144", "swap-mode": "2", "swap-min-mb": "1"])
            expect(l.status == "stuck" && l.candidate == nil, "a list that already has every line: nothing new to try")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: kill12, rules: rules, userKeys: [], fingerprint: "l4", log: nil,
                                       listValues: ["swap-mb": "6144"])
            expect(l.candidate == ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"], "the same number in the list: the other two lines are new")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: kill12, rules: rules, userKeys: ["swap-mb"], fingerprint: "l5", log: nil,
                                       listValues: ["swap-mb": "3072"])
            expect(l.status == "stuck", "the user's own swap-mb still keeps the rule out")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: report("crashed", stage: 4, matched: ["swap-12gb-memory-dialog"]), rules: rules,
                                       userKeys: [], fingerprint: "l6", log: nil, listValues: ["swap-mb": "3072"])
            expect(l.status == "stuck", "a crash with the memory dialog (not a kill) leaves the list's swap-mb alone")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: report("crashed", stage: 2, matched: ["x64-image-nocopy"]), rules: rules,
                                       userKeys: [], fingerprint: "l7", log: nil, listValues: ["env.MADEIRA_X64_IMAGE_NOCOPY": "1"])
            expect(l.status == "stuck", "any other rule still treats the list's keys as the user's")

            // Round 625 (owner's item 21, agent E1 S13-c; Hogwarts Legacy GBQRKH): the two window rules may raise the list's exe-window-mb after their
            // own failure (a crash or an early exit, not only a memory kill), only to a larger number, never a user's key.
            let win = report("crashed", stage: 2, matched: ["exe-window-fixed-base"])
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: win, rules: rules, userKeys: [], fingerprint: "w1", log: nil,
                                       listValues: ["exe-window-mb": "448"])
            expect(l.candidate == ["exe-window-mb = 512"] && l.candidateRule == "exe-window-fixed-base",
                   "the list's 448 MB window is raised to 512 after the crash it causes (\(l.candidate ?? []))")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: report("exited-early", stage: 2, matched: ["exe-window-fixed-base"]), rules: rules,
                                       userKeys: [], fingerprint: "w2", log: nil, listValues: ["exe-window-mb": "448"])
            expect(l.candidate == ["exe-window-mb = 512"], "an early exit with the same failure: raised too")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: win, rules: rules, userKeys: [], fingerprint: "w3", log: nil,
                                       listValues: ["exe-window-mb": "512"])
            expect(l.status == "stuck" && l.candidate == nil, "a list that already has 512: nothing new")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: win, rules: rules, userKeys: [], fingerprint: "w4", log: nil,
                                       listValues: ["exe-window-mb": "592"])
            expect(l.status == "stuck", "a list with a larger window is never lowered")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: win, rules: rules, userKeys: ["exe-window-mb"], fingerprint: "w5", log: nil,
                                       listValues: ["exe-window-mb": "448"])
            expect(l.status == "stuck", "the user's own exe-window-mb keeps the rule out")
            var bigWin = report("crashed", stage: 2, matched: ["exe-window-fixed-base-large"])
            bigWin.fixedExeMB = 575
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: bigWin, rules: rules, userKeys: [], fingerprint: "w6", log: nil,
                                       listValues: ["exe-window-mb": "512"])
            expect(l.candidate == ["exe-window-mb = 592"], "the large rule compares the window it would write (575 MiB: 592) with the list's 512")
            l = AutoTuneEngine.advance(AutoTuneState(retried: true), report: report("crashed", stage: 2, matched: ["x64-image-nocopy"]), rules: rules,
                                       userKeys: [], fingerprint: "w7", log: nil, listValues: ["env.MADEIRA_X64_IMAGE_NOCOPY": "1"])
            expect(l.status == "stuck", "a rule without the mark still treats the list's keys as the user's")
            expect(Set(rules.filter { $0.listUpgradeFailure == true }.map(\.id)) == ["exe-window-fixed-base", "exe-window-fixed-base-large"]
                   && rules.filter { $0.listUpgradeFailure == true }.allSatisfy { $0.listUpgrade == true },
                   "only the two window rules carry the failure mark, both with listUpgrade")

            // (c) A failure no setting changes: the retry is not spent, and the advice does not promise a build.
            let ceg = "dock-ceg-result-55", mina = "metal-texture-double-release"
            var c = AutoTuneEngine.advance(AutoTuneState(), report: report("exited-early", stage: 2, matched: [ceg], dock: true), rules: rules,
                                           userKeys: [], fingerprint: "c1", log: nil)
            expect(c.status == "known" && c.known == ceg && c.knownNoFix == true && c.knownFixedIn == nil && c.candidate == nil && !c.retried && c.tried.isEmpty,
                   "CEG result 55: known at once, no retry")
            expect(c.knownLead == "No setting changes this" && c.knownAdvice.hasPrefix("No setting changes this: ")
                   && !c.knownAdvice.contains("Fixed in build") && !c.knownAdvice.contains("update BCD") && !c.knownAdvice.contains("A crash in BCD"),
                   "its advice names no build and no crash of BCD's (\(c.knownAdvice))")
            c = AutoTuneEngine.advance(AutoTuneState(), report: report("killed", stage: 3, matched: [mina, "swap-8gb-memory-kill"]), rules: rules,
                                       userKeys: [], fingerprint: "c2", log: nil)
            expect(c.status == "known" && c.known == mina && c.candidate == nil, "Mina the Hollower: the open release bug wins over the swap rule")
            expect(rules.first { $0.id == ceg }?.session == "dock" && rules.first { $0.id == ceg }?.lines == [] && rules.first { $0.id == mina }?.priority == 2,
                   "the CEG rule is a Dock rule; the release rule runs before every swap rule")
            let oldKnown = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 2, matched: ["metal-layer-format"]), rules: rules,
                                                  userKeys: [], fingerprint: "c3", log: nil)
            expect(oldKnown.knownNoFix == nil && oldKnown.knownLead == "A crash in BCD itself, not a setting" && oldKnown.knownAdvice.contains("Fixed in build 560"),
                   "a fixedIn rule keeps its words")
            if let d = try? JSONEncoder().encode(c), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
                expect(back == c && back.knownNoFix == true, "a noFix state round-trips through JSON")
            } else { expect(false, "the noFix state encodes") }
            let cegLines = "[dock-launch] app 42700\n[dock-report] ceg-job-result=55\n[dock-report] ceg-result=55\n[dock-report] launch-host-result=49\n[dock-report] probe-result=49\n"
            expect(!session(head + cegLines + exitLine("0xc0000135")).matched.contains(ceg),
                   "a build 579 log of the same failure (the session never ended itself) keeps the fixedIn rule's words")
            expect(session(head + cegLines + "[dock-game-end] the host reported the launch failed (code 49) and no game process runs\n" + exitLine("0xc0000135")).matched.contains(ceg),
                   "from build 580, which ends the session, the CEG rule matches")
            expect(!session(head + cegLines.replacingOccurrences(of: "ceg-result=55", with: "ceg-result=0") + "[dock-game-end] x\n" + exitLine("0xc0000135")).matched.contains(ceg),
                   "another CEG result is not this rule")
            let overrideOff = AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: nil)
            expect(overrideOff.contains { $0.id == ceg } && overrideOff.contains { $0.id == mina }, "noFix rules with no lines stay in the table")

            // (d) A fixed-base program of 512 to 703 MiB: the window is its size plus 17 MiB, up to a multiple of 16.
            var withExe = report("crashed", stage: 2)
            for (mb, window) in [(575, 592), (379, 400), (396, 416), (512, 544), (703, 720), (640, 672)] {
                withExe.fixedExeMB = mb
                expect(AutoTuneEngine.resolve(["exe-window-mb = {exeWindowMB}"], report: withExe) == ["exe-window-mb = \(window)"],
                       "\(mb) MiB gets a \(window) MB window, the image and 16 MiB or more")
            }
            withExe.fixedExeMB = nil
            expect(AutoTuneEngine.resolve(["exe-window-mb = {exeWindowMB}"], report: withExe) == ["exe-window-mb = {exeWindowMB}"], "without a size the line stays as it is")
            expect(AutoTuneEngine.resolve(["swap-mb = 3072"], report: withExe) == ["swap-mb = 3072"], "a plain line is not touched")
            func mapImage(_ size: String) -> String {
                "ml985: map_image_view size=\(size) base=0x140000000 info_base=0x140000000 map_addr=0 flags=0 charact=0x23 limits=0x10000..0x73fffeffff\n"
            }
            let mainFail = "wine: failed to create main module for L\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Game\\\\Game.exe\", status c0000018\n"
            let big = "exe-window-fixed-base-large", small = "exe-window-fixed-base"
            func exeSession(_ size: String) -> AutoTuneReport { session(head + mapImage(size) + mainFail + exitLine("0xc0000018")) }
            let re9 = exeSession("0x23f12000")
            expect(re9.matched.contains(big) && !re9.matched.contains(small), "Resident Evil Requiem (575 MiB): the large rule, not the 512 MB one (\(re9.matched))")
            expect(exeSession("0x1fffffff").matched.contains(small) && !exeSession("0x1fffffff").matched.contains(big), "511 MiB: the 512 MB rule as before")
            expect(exeSession("0x20000000").matched.contains(big) && !exeSession("0x20000000").matched.contains(small), "512 MiB: the large rule from the first MiB")
            expect(exeSession("0x2bfffff0").matched.contains(big) && !exeSession("0x2c000000").matched.contains(big), "703 MiB passes, 704 MiB does not")
            expect(!exeSession("0x378b2000").matched.contains(big), "Persona 4 Golden's 888 MiB: no window to try")
            let t = AutoTuneEngine.advance(AutoTuneState(), report: re9, rules: rules, userKeys: [], fingerprint: "e1", log: nil)
            expect(t.candidate == ["exe-window-mb = 592"] && !t.retried && t.candidateRule == big, "the first try is exe-window-mb = 592, at once (\(t.candidate ?? []))")
            let noSize = session(head + mainFail + exitLine("0xc0000018")).matched
            expect(noSize.contains(small) && !noSize.contains(big), "a log with no program size: the 512 MB rule as before, never the large one (\(noSize))")

            // (e) A kill far below the memory limit is not a memory kill; one at the limit still is.
            let limitLine = "ml992: os_proc_available_memory=7082 MB + phys_footprint=1109 MB = 8192 MB\n"
            func memorySession(_ footprint: Int, _ said: String? = "closed-by-itself") -> AutoTuneReport {
                session(head + "[device] model=iPhone18,1 idiom=phone os=iOS ram=11695MB available=8155MB cores=6\n" + limitLine
                        + "[swap] ml1077 backed 512 MB\n[footprint] rev=ml358 phys=\(footprint) MB (peak \(footprint)) internal=1 MB\n",
                        end: AutoTuneSessionEnd(sawProcess: true, seconds: 30, killed: true, userSaid: said))
            }
            let low = memorySession(2018)
            expect(low.outcome == "killed" && low.memoryPct == 24 && !low.matched.contains("swap-12gb-memory-kill") && !low.matched.contains("swap-12gb-more"),
                   "a closed-by-itself kill at 2 GB of 8 GB: no swap rule (\(low.matched), \(low.memoryPct ?? -1) %)")
            let atLimit = memorySession(8100)
            expect(atLimit.outcome == "killed" && atLimit.matched.contains("swap-12gb-memory-kill") && atLimit.matched.contains("swap-12gb-more"),
                   "a kill at 8,100 of 8,192 MB: both swap rules, as before (\(atLimit.matched))")
            expect(memorySession(4096).matched.contains("swap-12gb-memory-kill"), "half of the limit is the bar: still a memory kill")
            expect(!memorySession(4000).matched.contains("swap-12gb-memory-kill"), "under half of it is not")
            expect(rules.filter { $0.id.hasPrefix("swap-") && $0.id.hasSuffix("-kill") || $0.id.hasSuffix("-more") }.allSatisfy { $0.memoryMinPct == 50 },
                   "the kill and more rules of all three tiers carry the bar")
            expect(session(head + "[footprint] rev=ml358 phys=300 MB (peak 300) internal=1 MB\n[swap] ml1077 backed 512 MB\n",
                           end: AutoTuneSessionEnd(sawProcess: true, seconds: 30, killed: true, userSaid: "closed-by-itself")).memoryPct == nil,
                   "a footprint line with no limit anywhere: no percentage, the rule passes")

            // (f) wow-rwx-plain-smc without the [store-noalias] line.
            let rwx = "wow-rwx-plain-smc"
            let unsetLine = "[wow-rwx] ml1279 MADEIRA_WOW_RWX_PLAIN=(unset) -> 32-bit guest RWX memory is plain read/write in a window once Wine Mono is mapped there\n"
            let handled = "D 24 Handled self-modifying code: pc: 16B038E5C fault: 710455C01C\n"
            let redeliv = "[redeliv] 256 identical redeliveries pc=0x16b038e5c addr=0x710455c01c — storm forming rev=ml461\n"
            let storeLine = "[store-noalias] #1 rev=ml348 addr=0x140010122 insn=0x19000028 pc=0x16baa957c NO pool/anon alias | region 0x140010000+0x4000 prot=1 max=3 (prot without W = exec-downgraded page that never got dual-mapped)\n"
            expect(session(head + unsetLine + String(repeating: handled, count: 256) + redeliv + exitLine("0xc0000005")).matched.contains(rwx),
                   "256 handled SMC faults and the guard's line: the plain-RWX line (Pro Evolution Soccer 2013)")
            expect(!session(head + unsetLine + String(repeating: handled, count: 255) + redeliv + exitLine("0xc0000005")).matched.contains(rwx),
                   "255 are not enough")
            expect(!session(head + unsetLine + String(repeating: handled, count: 300) + exitLine("0xc0000005")).matched.contains(rwx),
                   "without the redelivery guard's line it is not this loop")
            expect(!session(head + String(repeating: handled, count: 300) + redeliv + exitLine("0xc0000005")).matched.contains(rwx),
                   "with the switch already set (no unset line) it is not offered again")
            expect(session(head + unsetLine + storeLine + redeliv + exitLine("0xc0000005")).matched.contains(rwx),
                   "and the old signature (the store line and the guard) still matches")

            // (g) The Dock host's result 47 (the nested Steam bootstrapper): the died-game and orphan-lock rules stay out.
            let died = "[dock-report] launch-game-ended=1\n[Wine ntdll/server] process_exit_wrapper(-1073741819): closing child fd_socket=248\n"
            let orphan = "[lock-reap] SRW 0x7c770e0098 dead_teb=0x7200300000 released exclusive (word 00010001 -> 00000000) rev=ml446\n"
                + "0140:err:seh:NtRaiseException Unhandled exception code c0000005 flags 0 addr 0x71f3dfa824\n"
            let dockHead = head + "[dock-launch] app 12110\n"
            let plainDied = session(dockHead + died + orphan + exitLine("0xc0000005"))
            expect(plainDied.matched.contains("dock-game-died-session-open") && plainDied.matched.contains("lock-orphan-stamped"),
                   "a Dock game that died with an orphan lock: both rules, as before (\(plainDied.matched))")
            let v47 = session(dockHead + died + orphan + "[dock-report] launch-host-result=47\n" + exitLine("0xc0000005"))
            expect(!v47.matched.contains("dock-game-died-session-open") && !v47.matched.contains("lock-orphan-stamped"),
                   "the same with the host's result 47 (GTA: Vice City): neither (\(v47.matched))")

            // (h) The nested rule order: a Rockstar Launcher without Social Club's pools before the pool rules.
            let scLine = "[sc-cef] SocialClubHelper.exe asks for PartitionAlloc's glued 32 GB pools at a 32 GB boundary; without env.MADEIRA_SC_PA_POOLS=1 there is none to give (FEX's arena covers 0x7c00000000) -- its chrome_elf.dll will fail as before\n"
            let map512 = "[device] address-map=[0x1045cc000,0x8000000000) 512GB increased-memory=1 extended-va=0\n"
            let map454 = "[device] address-map=[0x100530000,0x7180000000) 454GB increased-memory=0 extended-va=0\n"
            let pools = session(head + map512 + scLine + exitLine("0xc0000005")).matched
            expect(pools.contains("sc-pa-pools-missing"), "a 512 GB map: Social Club's pools (\(pools))")
            expect(!session(head + map454 + scLine + exitLine("0xc0000005")).matched.contains("sc-pa-pools-missing")
                   && !session(head + map512 + scLine + "[madeira-env] game MADEIRA_SC_PA_POOLS=2\n" + exitLine("0xc0000005")).matched.contains("sc-pa-pools-missing"),
                   "not on a 454 GB map, and not with the switch already in force")
            let sc = AutoTuneEngine.advance(AutoTuneState(), report: report("crashed", stage: 5, matched: ["sc-pa-pools-missing", "x64-image-nocopy", "pool-split-exhausted"]),
                                            rules: rules, userKeys: [], fingerprint: "s1", log: nil)
            expect(sc.candidateRule == "sc-pa-pools-missing" && sc.candidate == ["env.MADEIRA_SC_PA_POOLS = 2"], "...tried before the pool rules")
        }

        // 14b. Round 624 verdicts (agents B, D2, E1), with the facts set by hand.
        do {
            // (a) a launcher's child crashed before the game presented (Control FGTGM8: Control.exe exited 0, control_dx11.exe died)
            var lc = AutoTuneLogFacts(); lc.stage = 2; lc.aliveSeconds = 11; lc.cleanMainExit = true; lc.childrenPending = true; lc.childUnhandled = 1
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 11), lc) == "crashed",
                   "a launcher's child that crashed before a frame: crashed (Control FGTGM8)")
            var lp = lc; lp.stage = 4; lp.aliveSeconds = 120
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 120), lp) == "ran",
                   "...not once the game presented (Fallout 4 C5Y6PW: early helper errors, then play)")
            var ln = lc; ln.childrenPending = false
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 11), ln) == "exited-early",
                   "...nor without the launcher's \"main process exited but\" line (the old teardown guard)")
            var lk = lc; lk.childUnhandled = 0
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 11), lk) == "exited-early",
                   "...nor when the child left no crash evidence")
            // (b) "I closed it" after a freeze in BCD's own lock with zero frames (Cult of the Lamb VHRFNE)
            var fz = AutoTuneLogFacts(); fz.stage = 3; fz.fexlockStuck = 2; fz.presentsCounted = true; fz.presentsLogged = 0
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), fz) == "hung",
                   "\"I closed it\" after a freeze in BCD's own lock with no frame: hung (VHRFNE)")
            var fzp = fz; fzp.presentsLogged = 50
            var fzn = fz; fzn.presentsCounted = nil
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), fzp) == "inconclusive"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), fzn) == "inconclusive",
                   "...with frames, or without the Dock's frame counter: nothing learned, as before")
            // (c) a start-up burst kill (Metro 2033 Redux Y1H618: 1960 MB slow, 3689 MB fast, limit 4096)
            var bk = AutoTuneLogFacts(); bk.stage = 3; bk.limitMB = 4096; bk.lastSlowFootprintMB = 1960; bk.lastFootprintMB = 3689; bk.lastFootprintFast = true
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "froze"), bk) == "killed"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), bk) == "killed",
                   "a start-up burst at 90 % of the limit, 1.9 x the slow line: killed, whatever the answer (Y1H618)")
            var bs = bk; bs.lastFootprintFast = false
            var bl = bk; bl.lastFootprintMB = 2500
            var bp = bk; bp.lastSlowFootprintMB = 3000
            var b4 = bk; b4.stage = 4
            var bf = bk; bf.freezeGaps = 1
            expect([bs, bl, bp, b4, bf].allSatisfy { AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "froze"), $0) == "hung" },
                   "...not from a slow line, below 80 %, a plateau, after a frame or with a freeze the app saw: \"it froze\" stays hung")
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), bk) == "inconclusive",
                   "...and \"I closed it\" is still the player's")
            // (d) alive two minutes after a few frames, 10 s or less of play (WRC 9 JDJ8KE: 122 s, play 4 s, 32 frames)
            var wr = AutoTuneLogFacts(); wr.stage = 3; wr.aliveSeconds = 122; wr.sessionPlaySeconds = 4; wr.presentsCounted = true; wr.presentsLogged = 32
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 122), wr) == "hung", "a loading screen that stayed two minutes: hung (WRC 9)")
            var wp = wr; wp.sessionPlaySeconds = 60
            var w4 = wr; w4.stage = 4
            var wy = wr; wy.aliveSeconds = 90
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 122), wp) == "ran"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 122), w4) == "ran"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 90), wy) == "exited-early",
                   "...not with a minute of play, at stage 4, or before two minutes")
            // (e) a Dock game whose steam_api found no Steam and that showed an error box: crashed at any stage (Portal 2 HZE5E9)
            var st = AutoTuneLogFacts(); st.stage = 4; st.aliveSeconds = 30; st.dock = true; st.steamNotFound = true; st.dialogs = ["Engine Error"]
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 30), st) == "crashed", "a Dock game with no Steam and an error box: crashed (Portal 2)")
            var sn = st; sn.dock = false
            var sd = st; sd.dialogs = []
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 30), sn) == "ran"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 30), sd) == "ran",
                   "...not outside the Dock (the Rockstar launcher's own probe), nor without a box")
            // (f) Unreal Engine 4's own crash window is crash evidence (Gal*Gun 2 B2B7BR: the window was open, no exit status, scored "ran")
            let ueSigs = AutoTuneEngine.signals(rules)
            let ueScan = AutoTuneLogScanner(signals: ueSigs)
            ueScan.feed(Data("[win-name] #100 hwnd=0x1008a class='' text=\"The UE4-GalGun2 Game has crashed and will close\" rev=ml853\n".utf8))
            ueScan.finish()
            var ue = ueScan.facts; ue.stage = 4; ue.aliveSeconds = 139
            expect(ueScan.facts.runtimeErrorDialog && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 139), ue) == "crashed",
                   "UE4's \"... has crashed and will close\" window counts as the program's own crash box: crashed, not ran (B2B7BR)")
            let titleScan = AutoTuneLogScanner(signals: ueSigs)
            titleScan.feed(Data("[win-name] #5 hwnd=0x10052 class='' text=\"Gal*Gun 2 \" rev=ml853\n".utf8))
            titleScan.finish()
            expect(!titleScan.facts.runtimeErrorDialog, "...and the game's own window title is not")
            // (g) the Rockstar Launcher's own error line with no game frame is a failure (agent R S29: AX6XNN "ran, ok" with 113)
            var lf = AutoTuneLogFacts(); lf.stage = 3; lf.aliveSeconds = 120; lf.dock = true; lf.presentsCounted = true; lf.presentsLogged = 0
            lf.launcherErrors = 1
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 120), lf) == "crashed",
                   "a Launcher error (LAUNCHER_ERR_) and no game frame: crashed, not ran (AX6XNN)")
            var lfq = lf; lfq.quitRequested = true; lfq.launcherErrorBeforeQuit = true; lfq.crashEvidenceAtQuit = 0
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 30), lfq) == "crashed",
                   "...also when the player closed it after the error (5T9WRM)")
            var lfp = lf; lfp.stage = 4; lfp.presentsLogged = 900
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 120), lfp) == "ran",
                   "...not once the game drew frames")
            var lfc = lf; lfc.stage = 5; lfc.aliveSeconds = 663
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 663), lfc) == "crashed",
                   "...also when the clock raised the stage but the Dock's counter stayed at 0 (RXTG8X, 663 s)")
            let leScan = AutoTuneLogScanner(signals: ueSigs)
            leScan.feed(Data("[guest-log] #5 tid=00d4 [2026-10-28 18:26:56.525] [ ERROR ] [Main ] [error] * Code:      113 (LAUNCHER_ERR_STEAM_FAILED)\n".utf8))
            leScan.finish()
            expect(leScan.facts.launcherErrors == 1, "the scanner counts the Launcher's error line")
            // (h) frames, then the Dock's counter flat for three readings with a FEX lock held: frozen (agent C S7, Kerbal Space Program 2G3Q3V:
            // 3474 frames, then the same number for 5 minutes; scored "ran"/"quit" and the cause rule was never read)
            var fk = AutoTuneLogFacts(); fk.stage = 5; fk.aliveSeconds = 421; fk.dock = true; fk.fexlockStuck = 2; fk.presentsCounted = true
            fk.presentsLogged = 3474; fk.presentsLast = 3474; fk.presentsFlat = 3; fk.frameSeen = true
            expect(fk.lockFreeze && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 421), fk) == "hung",
                   "frames, then a counter flat for 90 s with a FEX lock held: hung, not ran (2G3Q3V)")
            var fkq = fk; fkq.quitRequested = true; fkq.lockFreezeAtQuit = true; fkq.crashEvidenceAtQuit = 0
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 421), fkq) == "hung"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true), fkq) == "hung",
                   "...also when the player closed the frozen game (the quit came after the freeze), killed or not")
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(killed: true, userSaid: "i-closed"), fk) == "hung",
                   "...and \"I closed it\" after such a freeze is a hang too")
            var fk2 = fk; fk2.presentsFlat = 2
            var fkn = fk; fkn.fexlockStuck = 0
            var fkl = fkq; fkl.lockFreezeAtQuit = false
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 421), fk2) == "ran"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 421), fkn) == "ran"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 421), fkl) == "quit",
                   "...not with 60 s of a flat counter, without the stuck lock, or when the freeze came only after the player's quit")
            let fkScan = AutoTuneLogScanner(signals: ueSigs)
            var fkLines = "[fexlock] STUCK write-wait on mutex 0x7c304e0108 word=c0010000 | owner teb=71fff00000 tid=00a8\n"
            for n in [0, 1740, 3474, 3474, 3474, 3474] { fkLines += "[INFO] detach-wait: presents=\(n) running=1 elapsed=30s\n" }
            fkScan.feed(Data(fkLines.utf8))
            let flatBefore = fkScan.facts.lockFreeze
            fkScan.feed(Data("[INFO] detach-wait: presents=3600 running=1 elapsed=210s\n".utf8))
            fkScan.finish()
            expect(flatBefore && !fkScan.facts.lockFreeze && fkScan.facts.presentsFlat == 0,
                   "the scanner sees the flat run (three repeats) and forgets it when the counter moves again")
            // (i) round 624, RETRO-1 R4 (WRC 9 JDJ8KE/WV1P7T): a [spin] report and a counter flat for 60 s after frames is a freeze like (h); the
            // player's Close 75-80 s into it is a hang, not a quit
            var sp = AutoTuneLogFacts(); sp.stage = 3; sp.aliveSeconds = 200; sp.dock = true; sp.spinReports = 1; sp.presentsCounted = true
            sp.presentsLogged = 34; sp.presentsLast = 34; sp.presentsFlat = 2; sp.frameSeen = true
            var spq = sp; spq.quitRequested = true; spq.spinFreezeAtQuit = true; spq.crashEvidenceAtQuit = 0
            expect(sp.spinFreeze && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 200), spq) == "hung",
                   "a [spin] report and a frame counter flat for 60 s, then Close: hung, not quit (WRC 9 JDJ8KE)")
            var spr = spq; spr.spinFreezeAtQuit = false
            var spn = spq; spn.spinReports = 0; spn.spinFreezeAtQuit = false
            var sp1 = sp; sp1.presentsFlat = 1
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 200), spr) == "quit"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, quitRequested: true, seconds: 200), spn) == "quit"
                   && !sp1.spinFreeze,
                   "...not when the counter still moved at the quit, without a spin report (CS:S W69RYK), or after one flat reading")
            let spScan = AutoTuneLogScanner(signals: ueSigs)
            var spLines = "[spin] ml1140 23:33:20.967 look 1: x64->EC 12486246/s, 100% of the sampled calls for 4 s go to d3d11.dll+0x6be54\n"
            for n in [0, 34, 34, 34] { spLines += "[INFO] detach-wait: presents=\(n) running=1 elapsed=30s\n" }
            spLines += "[INFO] [frontend] graceful close requested\n[INFO] detach-wait: presents=40 running=1 elapsed=150s\n"
            spScan.feed(Data(spLines.utf8)); spScan.finish()
            expect(spScan.facts.spinReports == 1 && spScan.facts.spinFreezeAtQuit && !spScan.facts.spinFreeze,
                   "the scanner counts the spin look and keeps the freeze as it stood at the quit line")
            // (j) round 624, RETRO-2 RQ3 (Ghost of Tsushima JKJA9Q): a helper's error exit before the first frame, then the game drew and reached
            // stage 4: not the game's crash; an error exit after a frame still is
            var he = AutoTuneLogFacts(); he.stage = 5; he.aliveSeconds = 600; he.errorExits = 1; he.frameSeen = true; he.presentsCounted = true
            he.presentsLogged = 30000; he.lastErrorExitAfterFrame = false
            var hf = he; hf.lastErrorExitAfterFrame = true
            var h3 = he; h3.stage = 3; h3.presentsLogged = 100
            var hu = he; hu.unhandled = 1
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 600), he) == "ran",
                   "a helper's error exit before the first frame, then 10 minutes of play: ran, not crashed (JKJA9Q)")
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 600), hf) == "crashed"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 600), h3) == "crashed"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 600), hu) == "crashed",
                   "...an error exit after a frame, a session below stage 4, or an unhandled exception still is a crash")
            let heScan = AutoTuneLogScanner(signals: ueSigs)
            heScan.feed(Data(("0030:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0xc0000135) syscalls=0\n"
                              + "[INFO] detach-wait: presents=900 running=1 elapsed=30s\n").utf8))
            let beforeFrame = heScan.facts.errorExits == 1 && !heScan.facts.lastErrorExitAfterFrame
            heScan.feed(Data("0024:err:process:NtTerminateProcess NtTerminateProcess(handle=0xffffffffffffffff, exit_code=0xc0000005) syscalls=0\n".utf8))
            heScan.finish()
            expect(beforeFrame && heScan.facts.errorExits == 2 && heScan.facts.lastErrorExitAfterFrame,
                   "the scanner notes whether the last error exit came after a frame")
            // (k) round 624, Farming Simulator 22 on Discord: an unhandled host fault with no TEB, no frame for 695 s: hung, not ran by the clock
            var nt = AutoTuneLogFacts(); nt.stage = 5; nt.aliveSeconds = 695; nt.dock = true; nt.machFaultNoTeb = 1; nt.presentsCounted = true
            nt.presentsLogged = 0
            var nts = nt; nts.aliveSeconds = 73; nts.stage = 2
            var ntf = nt; ntf.presentsLogged = 500; ntf.frameSeen = true
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 695), nt) == "hung",
                   "an unhandled no-TEB fault and no frame for 695 s: hung (FS22)")
            expect(AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 73), nts) == "exited-early"
                   && AutoTuneEngine.outcome(AutoTuneSessionEnd(sawProcess: true, seconds: 695), ntf) == "ran",
                   "...not a session that ended within two minutes (MGS PFDA0D, 73 s), nor one that drew frames")
            let ntScan = AutoTuneLogScanner(signals: ueSigs)
            ntScan.feed(Data(("[mach_exc] UNHANDLED #12400 pc=0x13e994024 addr=0x71f75f7c80 x18=0x0 type=1 lr=0x13f2e0354\n"
                              + "[mach_exc] UNHANDLED #1 pc=0x13e346788 addr=0x71f3b68000 x18=0x71fffc0000 type=1 lr=0x13f2e0354\n").utf8))
            let notYet = ntScan.facts.machFaultNoTeb == 0
            ntScan.feed(Data("[mach_exc] UNHANDLED #1 pc=0x14d2241d8 addr=0x57e x18=0x0 type=1 lr=0x14d209864 sp=0x717ae5e3f0\n".utf8))
            ntScan.finish()
            expect(notYet && ntScan.facts.machFaultNoTeb == 1,
                   "the scanner counts only the first unhandled fault with x18 0 (not #12400, not one with a TEB in x18: Watch Dogs 2)")
            // (l) round 624, RETRO-2 RQ1: a noFix rule marked evaluateWhenRan speaks for a session scored ran; without the flag, or with kept
            // lines, the session stays "ok"/"found"
            let ran = AutoTuneRule(id: "test-ran-nofix", lines: [], priority: 3, all: [AutoTuneSignal(has: ["only in the test"])],
                                   reason: "the test's reason", noFix: true, evaluateWhenRan: true)
            var plain = ran; plain.id = "test-ran-plain"; plain.evaluateWhenRan = nil
            let tr = rules + [ran, plain]
            let tsig = AutoTuneEngine.signals(tr)
            let rr = AutoTuneReport(outcome: "ran", stage: 5, seconds: 314, presents: 0, peakMB: nil, dock: true, d3d12: false, ramMB: 11695,
                                    matched: [], evidence: [], build: 624)
            let counts = tsig.map { sig in sig.has == ["only in the test"] ? 1 : 0 }
            expect(AutoTuneEngine.matches(ran, report: rr, counts: counts, signals: tsig) && !AutoTuneEngine.matches(plain, report: rr, counts: counts, signals: tsig),
                   "a played session matches a flagged noFix rule, not an unflagged one")
            var kr = rr; kr.matched = [ran.id]
            let ks = AutoTuneEngine.advance(AutoTuneState(), report: kr, rules: tr, userKeys: [], fingerprint: "r", log: nil)
            expect(ks.status == "known" && ks.known == ran.id && ks.knownNoFix == true && ks.knownFixedIn == nil && ks.candidate == nil,
                   "...and the verdict says what it was (known), nothing is tried")
            var pr = rr; pr.matched = [plain.id]
            let ps = AutoTuneEngine.advance(AutoTuneState(), report: pr, rules: tr, userKeys: [], fingerprint: "r", log: nil)
            var keptState = AutoTuneState(); keptState.kept = ["env.MADEIRA_CAGE_RELEASE = 1"]; keptState.fingerprint = "r"
            let kept = AutoTuneEngine.advance(keptState, report: kr, rules: tr, userKeys: [], fingerprint: "r", log: nil)
            expect(ps.status == "ok" && kept.status == "found", "...not for an unflagged rule (ok), nor when auto-tune kept lines (found)")
            // (n) round 624 (Fallout 4 voices, WRSNC8): a ranOnly rule speaks for a played session only; a failed session with the same
            // lines keeps its own verdict
            var only = ran; only.id = "test-ran-only"; only.ranOnly = true
            let failedReport = AutoTuneReport(outcome: "crashed", stage: 4, seconds: 90, presents: 300, peakMB: nil, dock: true, d3d12: false,
                                              ramMB: 15389, matched: [], evidence: [], build: 624)
            expect(AutoTuneEngine.matches(only, report: rr, counts: counts, signals: tsig)
                   && !AutoTuneEngine.matches(only, report: failedReport, counts: counts, signals: tsig)
                   && AutoTuneEngine.matches(ran, report: failedReport, counts: counts, signals: tsig),
                   "a ranOnly rule matches a played session and not a failed one (an evaluateWhenRan rule without it matches both)")
            // (m) round 624, agent D3 S5-3 (Sekiro XJ8YM3, Cloudpunk F88RB4 with 2 GB free; Sekiro ZJYGS9 with 4 GB): a swap file is sized to the
            // free storage; with too little no swap attempt is made and the state says why
            expect(AutoTuneEngine.swapRoomMB(freeGB: 0) == 0 && AutoTuneEngine.swapRoomMB(freeGB: 2) == 0 && AutoTuneEngine.swapRoomMB(freeGB: 3) == 1536
                   && AutoTuneEngine.swapRoomMB(freeGB: 4) == 2560 && AutoTuneEngine.swapRoomMB(freeGB: 8) == 6656,
                   "the swap room: all but 1.5 GB of the free storage, nothing under 1 GB")
            var sr = AutoTuneReport(outcome: "killed", stage: 3, seconds: 60, presents: 0, peakMB: 4052, dock: false, d3d12: false, ramMB: 5665,
                                    matched: [], evidence: [], build: 624)
            sr.freeDiskGB = 3
            expect(AutoTuneEngine.resolve(["swap-mb = 3072", "swap-mode = 2"], report: sr) == ["swap-mb = 1536", "swap-mode = 2"],
                   "a swap line is clamped to the room, the other lines are left as they are")
            var srn = sr; srn.freeDiskGB = nil
            expect(AutoTuneEngine.resolve(["swap-mb = 3072", "swap-mode = 2"], report: srn) == ["swap-mb = 3072", "swap-mode = 2"],
                   "...and left alone when the free storage is not known")
            let swapRule = AutoTuneRule(id: "test-swap", lines: ["swap-mb = 3072", "swap-mode = 2", "swap-min-mb = 1"], priority: 40,
                                        all: [AutoTuneSignal(has: ["only in the swap test"])], reason: "test")
            let swapUp = AutoTuneRule(id: "test-swap-more", lines: ["swap-mb = 6144"], priority: 41, all: [AutoTuneSignal(has: ["only in the swap test"])],
                                      upgrade: true, noRetry: true, reason: "test")
            var r2 = sr; r2.freeDiskGB = 2; r2.matched = [swapRule.id]
            var st0 = AutoTuneState(); st0.retried = true; st0.fingerprint = "s"
            let s2 = AutoTuneEngine.advance(st0, report: r2, rules: rules + [swapRule, swapUp], userKeys: [], fingerprint: "s", log: nil)
            expect(s2.status == "stuck" && s2.lowDiskGB == 2 && s2.candidate == nil && (s2.lowDiskNote ?? "").contains("this device has 2 GB free"),
                   "2 GB free: no swap attempt, stuck, and the note says why")
            var r4 = sr; r4.freeDiskGB = 4; r4.matched = [swapRule.id]
            let s4 = AutoTuneEngine.advance(st0, report: r4, rules: rules + [swapRule, swapUp], userKeys: [], fingerprint: "s", log: nil)
            expect(s4.status == "trying" && s4.lowDiskGB == nil && s4.candidate == ["swap-mb = 2560", "swap-mode = 2", "swap-min-mb = 1"],
                   "4 GB free: the swap file of 2560 MB")
            var stk = st0; stk.kept = ["swap-mb = 2560"]
            var ru = sr; ru.freeDiskGB = 4; ru.matched = [swapUp.id]
            let su = AutoTuneEngine.advance(stk, report: ru, rules: rules + [swapRule, swapUp], userKeys: [], fingerprint: "s", log: nil)
            expect(su.status == "stuck" && su.lowDiskGB == 4, "a larger swap file the room cannot hold over the kept one is no attempt either")
        }

        // 14b. Round 624 (agent E1 S5b): a "stuck" verdict says why each matched rule was passed over; the choice itself is the same,
        // and the note belongs to that session only.
        do {
            var st = AutoTuneState(); st.retried = true; st.fingerprint = "w"; st.tried = [cage]
            var rp = report("crashed", stage: 3, matched: [cage, spill])
            rp.freeDiskGB = nil
            let stuck = AutoTuneEngine.advance(st, report: rp, rules: rules, userKeys: ["env.MADEIRA_THREAD_STACK_SPILL"], fingerprint: "w", log: nil)
            expect(stuck.status == "stuck" && stuck.candidate == nil, "the tried rule and a rule whose key the user set: stuck, as before")
            expect(stuck.skippedNote == "\(cage) (tried), \(spill) (a key you set)", "...and the note names both and why (\(stuck.skippedNote ?? "nil"))")
            let listed = AutoTuneEngine.advance(AutoTuneState(retried: true, fingerprint: "w"), report: report("crashed", stage: 3, matched: [spill]),
                                                rules: rules, userKeys: [], fingerprint: "w", log: nil,
                                                listValues: ["env.MADEIRA_THREAD_STACK_SPILL": "1"])
            expect(listed.status == "stuck" && listed.skippedNote == "\(spill) (a key of the recommended list)",
                   "a key of the recommended list is named as such (\(listed.skippedNote ?? "nil"))")
            let trying = AutoTuneEngine.advance(stuck, report: report("crashed", stage: 3, matched: [jumbo]), rules: rules, userKeys: [],
                                                fingerprint: "w", log: nil)
            expect(trying.skippedNote == nil, "the next session's verdict does not carry the note of the one before")
        }

        // 15. State survives JSON (the app saves it between sessions).
        if let d = try? JSONEncoder().encode(s), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
            expect(back == s, "the state round-trips through JSON")
        } else { expect(false, "the state encodes") }
        if let d = try? JSONEncoder().encode(k2), let back = try? JSONDecoder().decode(AutoTuneState.self, from: d) {
            expect(back == k2 && back.knownFixedIn == 560, "a known crash's state round-trips through JSON")
        } else { expect(false, "the known state encodes") }
        let saved = Data(#"{"version":1,"status":"stuck","kept":[],"tried":[],"retried":true,"attempts":[]}"#.utf8)
        expect((try? JSONDecoder().decode(AutoTuneState.self, from: saved))?.known == nil,
               "a state saved before known crashes existed still loads")

        print(failures == 0 ? "PASS: auto-tune engine" : "FAILED: auto-tune engine (\(failures))")
        exit(failures == 0 ? 0 : 1)
    }
}
