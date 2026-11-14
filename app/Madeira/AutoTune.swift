// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  AutoTune.swift
//  Madeira
//
//  madeira-bcd: auto-tune's app side (docs/AUTO-TUNE.md): the global switch,
//  the per-game files, the session marker and the hooks LibraryModel and BCDLaunch call.
//
//  On is the default, for the app (Settings › Auto-tune) and for every game (Game details
//  › Auto-tune); a stored "off" is kept. Off, the app is the app without auto-tune: every
//  hook is skipped at its call site behind `AutoTune.enabled`, and every entry point
//  below returns at once as well. Nothing is read, written or logged.
//
//  On, nothing runs while a game runs either. Before a session one small file is
//  written (the marker) and, for a game being tuned, the launch file; after it the
//  session log is read as a stream on a utility queue, once the game process and
//  the wineserver are gone.
//

import Foundation
import Darwin
import os

enum AutoTune {
    static let globalKey = "madeira.autotune"
    static let gamesKey = "madeira.autotune.games"
    /// Settings › Auto-tune, read once per app run: a session that started without
    /// auto-tune ends without it. Absent key: on (only a stored "off" turns it off).
    static let enabled: Bool = storedOn(globalKey)
    private static func storedOn(_ key: String) -> Bool { UserDefaults.standard.object(forKey: key) == nil || UserDefaults.standard.bool(forKey: key) }
    /// What Settings shows and stores; applies from the next start.
    static var preferred: Bool {
        get { storedOn(globalKey) }
        set {
            UserDefaults.standard.set(newValue, forKey: globalKey)
            if !newValue, let m = markerURL { try? FileManager.default.removeItem(at: m) }
        }
    }

    // MARK: Per game

    /// The game's own switch (Game details › Auto-tune); on unless the owner of the device turned it off.
    static func isOn(_ windowsPath: String) -> Bool {
        (UserDefaults.standard.dictionary(forKey: gamesKey) as? [String: Bool])?[windowsPath.lowercased()] ?? true
    }
    static func setOn(_ windowsPath: String, _ on: Bool) {
        var d = (UserDefaults.standard.dictionary(forKey: gamesKey) as? [String: Bool]) ?? [:]
        d[windowsPath.lowercased()] = on
        UserDefaults.standard.set(d, forKey: gamesKey)
    }
    /// The Desktop is no game; everything else can be tuned (Steam games through Madeira Dock too), except a program that is plainly no
    /// game: a Steam helper or a redistributable installer (nothing to learn from it, and the verdict was filed under a real game or under
    /// "Steam": PJE6FT, 4W9GX4, QGEK8K, round 591).
    static func eligible(_ entry: LibraryEntry) -> Bool { entry.desktop != true && !isNoGame(entry.windowsPath) }

    /// A program that is no game, by its name and the folder it sits in: Steam's own helpers (the shader-cache replayer, the web helper,
    /// the error reporter, the driver queries) and the installers of a game's redistributables (the "~Common Redist" folders, the Microsoft
    /// .NET, Visual C++, DirectX and OpenAL setups). Nothing else is guessed: an installer under another name is tuned as before.
    static func isNoGame(_ windowsPath: String) -> Bool {
        let path = windowsPath.lowercased().replacingOccurrences(of: "/", with: "\\")
        let parts = path.split(separator: "\\").map(String.init)
        guard let name = parts.last, name.hasSuffix(".exe") else { return false }
        let helpers = ["fossilize-replay", "steamwebhelper", "steamerrorreporter", "gldriverquery", "vulkandriverquery"]
        if helpers.contains(where: { name.hasPrefix($0) }) { return true }
        let setups = ["ndp3", "ndp4", "vc_redist", "vcredist", "dotnetfx", "dxsetup", "dxwebsetup", "oalinst"]
        if setups.contains(where: { name.hasPrefix($0) }) { return true }
        let folders: Set<String> = ["~common redist", "_commonredist", "commonredist", "redist", "redistributable", "redistributables", "vcredist", "dotnetfx"]
        return parts.dropLast().contains { folders.contains($0) }
    }

    // MARK: Files

    /// GameConfigs/<hash>: the same hash the game's own file uses.
    private static func base(_ windowsPath: String) -> URL? {
        GameProfile(windowsPath: windowsPath).url?.deletingPathExtension()
    }
    static func stateURL(_ windowsPath: String) -> URL? { base(windowsPath)?.appendingPathExtension("autotune.json") }
    /// The file a tuned launch exports as MADEIRA_CFG_GAME: the layer, then the game's own file.
    static var launchURL: URL? { GameProfile.directory?.appendingPathComponent("autotune-launch.cfg") }
    /// Written when a tuned session starts and removed once it was analysed; left
    /// behind, it says the app did not come back (killed, or closed by the user).
    static var markerURL: URL? { GameProfile.directory?.appendingPathComponent("autotune-session.json") }
    static var overrideURL: URL? { MadeiraConfig.documents?.appendingPathComponent("autotune-rules.json") }

    static func state(_ windowsPath: String) -> AutoTuneState {
        guard let u = stateURL(windowsPath), let d = try? Data(contentsOf: u),
              let s = try? JSONDecoder().decode(AutoTuneState.self, from: d) else { return AutoTuneState() }
        return s
    }
    static func save(_ state: AutoTuneState, _ windowsPath: String) {
        guard let u = stateURL(windowsPath) else { return }
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let d = try? enc.encode(state) { try? d.write(to: u, options: .atomic) }
    }
    /// "Undo all": the lines and the history go.
    static func reset(_ windowsPath: String) {
        if let u = stateURL(windowsPath) { try? FileManager.default.removeItem(at: u) }
    }

    static func rules() -> [AutoTuneRule] {
        AutoTuneRules.load(builtIn: AutoTuneBuiltInRules.json, override: overrideURL)
    }

    /// Keys this game already gets from the user: its own file, the entry's lines, bcd.cfg. Not the lines of the recommended list while
    /// the file is exactly what BCD wrote from it (listValues).
    static func userKeys(_ entry: LibraryEntry) -> Set<String> {
        var keys = Set(GameProfile(windowsPath: entry.windowsPath).values.keys)
        keys.subtract(listValues(entry).keys)
        keys.formUnion(MadeiraConfig.parse(entry.gameConfigText ?? "").keys)
        keys.formUnion(MadeiraConfig.all().keys)
        return keys
    }

    /// The recommended list's own lines in the game's file: while the file is exactly what BCD wrote from the list (nobody changed it;
    /// GameRecommendations.followsList), its values are the list's, not the user's. The engine lets a rule that is marked as an upgrade
    /// replace one of them after a memory kill, with a larger number (GTA V Enhanced's list swap-mb = 3072 under the 12 GB tier's
    /// swap-mb = 6144, A24ES3). A key the entry's own lines or bcd.cfg also set is the user's.
    static func listValues(_ entry: LibraryEntry) -> [String: String] {
        guard GameRecommendations.followsList(windowsPath: entry.windowsPath) == true else { return [:] }
        var values = GameProfile(windowsPath: entry.windowsPath).values
        for key in MadeiraConfig.parse(entry.gameConfigText ?? "").keys { values[key] = nil }
        for key in MadeiraConfig.all().keys { values[key] = nil }
        return values
    }

    /// The game's settings as the user made them; a change starts a new baseline.
    static func fingerprint(_ entry: LibraryEntry) -> String {
        let p = entry.windowsPath
        let text = GameProfile(windowsPath: p).text + "\u{1}" + (entry.gameConfigText ?? "")
            + "\u{1}\(LibraryPrefs.avx(p))\(LibraryPrefs.wineVCRT(p))\(LibraryPrefs.nvidia(p))"
        return String(format: "%016llx", LibraryPrefs.stableHash(text))
    }

    // MARK: Analyses in flight

    /// Night round 2026-11-11 (agent T3 S7; 2FR906, 9W3TW7, BZQMTZ, XVABPA, P1433Z): the verdict about a session lands on a utility queue, 2 to
    /// 65 s after the app restarted, and a player who starts the same game inside that window launched with the state as it was before: the
    /// line the verdict prepared (the swap tier after a kill) missed that start and cost one more kill. One group per game (its Windows
    /// path); a launch of that game waits for it, at most `waitCapSeconds`, and no other game waits for anything.
    private static let pendingLock = NSLock()
    private static var pendingAnalyses: [String: DispatchGroup] = [:]
    static let waitCapSeconds = 20

    private static func pendingEnter(_ windowsPath: String) {
        pendingLock.lock(); defer { pendingLock.unlock() }
        let key = windowsPath.lowercased()
        let group = pendingAnalyses[key] ?? DispatchGroup()
        pendingAnalyses[key] = group
        group.enter()
    }
    private static func pendingLeave(_ windowsPath: String) {
        pendingLock.lock(); defer { pendingLock.unlock() }
        pendingAnalyses[windowsPath.lowercased()]?.leave()
    }
    /// How long this launch waited for the game's own earlier analysis, in milliseconds (0: none was running). Not called on the launch path
    /// (see exportLayer); kept for an asynchronous launch that may wait off the main thread.
    private static func waitForAnalysis(_ windowsPath: String) -> Int {
        pendingLock.lock()
        let group = pendingAnalyses[windowsPath.lowercased()]
        pendingLock.unlock()
        guard let group else { return 0 }
        let began = DispatchTime.now()
        _ = group.wait(timeout: .now() + .seconds(waitCapSeconds))
        return Int((DispatchTime.now().uptimeNanoseconds - began.uptimeNanoseconds) / 1_000_000)
    }
    /// Whether the game's earlier session is still being analysed (no wait).
    static func analysisPending(_ windowsPath: String) -> Bool {
        pendingLock.lock()
        let group = pendingAnalyses[windowsPath.lowercased()]
        pendingLock.unlock()
        guard let group else { return false }
        return group.wait(timeout: .now()) == .timedOut
    }

    // MARK: Hooks

    /// H1, BCDLaunch.applyLibrary after applyExtras: a tuned game's lines go under its own file.
    static func exportLayer(for entry: LibraryEntry) {
        guard enabled, eligible(entry), isOn(entry.windowsPath) else { return }
        let path = entry.windowsPath
        // Night round 2026-11-11 (agent T3 S7): the verdict about the last session of this game may still be being written. The lead did
        // not take the agent's wait (up to 20 s on the main thread, where this runs: the interface would freeze and iOS may end the app);
        // the start says so instead, and the line that verdict prepares reaches the next start. An asynchronous wait is an owner's decision.
        if analysisPending(path) {
            LogStore.shared.log("[autotune] \(entry.title): the earlier session's verdict is still being written; this start uses the game's state as it was")
        }
        let s = state(path)
        let blocked = userKeys(entry)
        // madeira-bcd (Porthole port, item 10): with "Ask before trying a fix" on, a fix the player has not said yes to waits
        // (AutoTuneAsk.swift); off, AutoTuneAsk.layer is the layer as always.
        let waiting = AutoTuneAsk.waiting(s, path)
        if !waiting.isEmpty {
            LogStore.shared.log("[autotune] \(entry.title): \(waiting.joined(separator: "; ")) waits for the player's yes (Ask before trying a fix); this start runs without it")
        }
        let lines = AutoTuneAsk.layer(s, path).filter { !blocked.contains(AutoTuneRules.key($0)) }
        guard !lines.isEmpty, let launch = launchURL else { return }
        let profile = GameProfile(windowsPath: path)
        var own = profile.text
        // The game's own file comes after the layer and wins. A list line a layer line raises (swap-mb = 3072 -> 6144) must not win over it:
        // that line leaves the launch file's copy of the game's file (the file itself is not touched).
        let listed = listValues(entry)
        let raised = Set(lines.filter { line in
            guard let had = listed[AutoTuneRules.key(line)], let old = Int(had), let new = Int(AutoTuneRules.value(line)) else { return false }
            return new > old
        }.map(AutoTuneRules.key))
        if !raised.isEmpty {
            own = own.split(separator: "\n", omittingEmptySubsequences: false).filter { raw in
                let t = raw.trimmingCharacters(in: .whitespaces)
                return t.hasPrefix("#") || !raised.contains(AutoTuneRules.key(t))
            }.joined(separator: "\n")
        }
        var text = "# BCD auto-tune, attempt \(s.attempts.count) for \(entry.title)\n"
        text += lines.joined(separator: "\n") + "\n"
        if !own.isEmpty {
            text += "# the game's own file (\(profile.url?.lastPathComponent ?? "")), after the lines above so it wins\n" + own
        }
        do {
            try (text.hasSuffix("\n") ? text : text + "\n").write(to: launch, atomically: true, encoding: .utf8)
            setenv("MADEIRA_CFG_GAME", launch.path, 1)
            LogStore.shared.log("[autotune] \(entry.title): this session adds \(lines.joined(separator: "; "))"
                                + (s.candidateRule.map { " (trying \($0))" } ?? ""))
        } catch {
            LogStore.shared.log("[autotune] \(entry.title): the launch file could not be written, the game runs without its lines", level: .error)
        }
    }

    private struct Marker: Codable {
        var windowsPath: String
        var title: String
        var startedAt: Date
        var build: String
        var logInode: UInt64
        var limitMB: Int?
        var fingerprint: String
        var userKeys: [String]
        /// The recommended list's lines in the game's file (listValues); optional: a marker written before it existed still decodes.
        var listValues: [String: String]?
        /// Set once the session ended normally, before the analysis; a marker with it
        /// survived only a kill during the analysis.
        var end: AutoTuneSessionEnd?
        /// Round 630 (agent U S15b): the lines auto-tune's layer gave this session (exportLayer's), optional: an older marker still decodes.
        var layer: [String]?
    }

    /// The session H2 armed in this app run.
    private static var armed: Marker?
    /// A session that never came back and whose log does not say why: it waits for the
    /// user's answer (main thread only).
    private static var pending: (marker: Marker, log: URL)?

    /// H2, LibraryModel.begin.
    static func sessionBegan(_ entry: LibraryEntry, remember: Bool, dockWithoutEntry: Bool) {
        guard enabled, eligible(entry), !dockWithoutEntry, isOn(entry.windowsPath), let url = markerURL else { return }
        let log = LogStore.shared.logFileURL
        let attrs = try? FileManager.default.attributesOfItem(atPath: log.path)
        let inode = (attrs?[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
        var m = Marker(windowsPath: entry.windowsPath, title: entry.title, startedAt: Date(),
                       build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
                       logInode: inode, limitMB: memoryLimitMB(),
                       fingerprint: fingerprint(entry), userKeys: Array(userKeys(entry)).sorted(),
                       listValues: listValues(entry), end: nil,
                       layer: state(entry.windowsPath).layerLines.filter { !userKeys(entry).contains(AutoTuneRules.key($0)) })
        // madeira-bcd (Porthole port, item 10): the lines this start really got while a fix waits for the player's yes.
        if AutoTuneAsk.on { m.layer = AutoTuneAsk.layer(state(entry.windowsPath), entry.windowsPath).filter { !userKeys(entry).contains(AutoTuneRules.key($0)) } }
        if let d = try? JSONEncoder().encode(m) { try? d.write(to: url, options: .atomic) }
        armed = m
    }

    /// H3, first line of LibraryModel.finish: the game process and the wineserver are gone.
    static func sessionEnded(_ end: AutoTuneSessionEnd) {
        guard enabled, var m = armed, let url = markerURL else { return }
        armed = nil
        m.end = end
        if let d = try? JSONEncoder().encode(m) { try? d.write(to: url, options: .atomic) }
        let log = LogStore.shared.logFileURL
        // Night round 2026-11-11 (T3 S7): a game that ended on its own or crashed may have a line waiting in its verdict; a game the player
        // closed from the library (almost always a played session) does not hold the next start back.
        let holdsNextStart = !end.quitRequested || end.crashStatus != nil
        if holdsNextStart { pendingEnter(m.windowsPath) }
        DispatchQueue.global(qos: .utility).async {
            analyse(m, end: end, log: log)
            try? FileManager.default.removeItem(at: url)
            if holdsNextStart { pendingLeave(m.windowsPath) }
        }
    }

    /// H4, LibraryModel.init: a marker from an earlier run means that session never
    /// came back to the library.
    static func recoverUnfinishedSession() {
        guard enabled, let url = markerURL, let d = try? Data(contentsOf: url),
              let m = try? JSONDecoder().decode(Marker.self, from: d) else { return }
        let pending = url.deletingLastPathComponent().appendingPathComponent("autotune-session.pending.json")
        try? FileManager.default.removeItem(at: pending)
        try? FileManager.default.moveItem(at: url, to: pending)
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        guard m.build == build, Date().timeIntervalSince(m.startedAt) < 7 * 86400, let log = findLog(inode: m.logInode) else {
            try? FileManager.default.removeItem(at: pending)
            return
        }
        pendingEnter(m.windowsPath)   // night round 2026-11-11 (T3 S7)
        DispatchQueue.global(qos: .utility).async {
            let end = m.end ?? AutoTuneSessionEnd(sawProcess: true, seconds: 0, killed: true, limitMB: m.limitMB)
            analyse(m, end: end, log: log)
            try? FileManager.default.removeItem(at: pending)
            pendingLeave(m.windowsPath)
        }
    }

    /// The session's log after a restart: the rotated bcd-log.prev.txt or one of the
    /// logs/ hard links (the same file, so the same inode).
    private static func findLog(inode: UInt64) -> URL? {
        guard inode != 0, let docs = MadeiraConfig.documents else { return nil }
        var candidates = [docs.appendingPathComponent("bcd-log.prev.txt")]
        let dir = docs.appendingPathComponent("logs")
        if let names = try? FileManager.default.contentsOfDirectory(atPath: dir.path) {
            candidates += names.filter { $0.hasSuffix(".txt") }.map { dir.appendingPathComponent($0) }
        }
        return candidates.first { url in
            ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.systemFileNumber] as? NSNumber)?.uint64Value == inode
        }
    }

    private static func memoryLimitMB() -> Int? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        #if os(iOS)
        let avail = os_proc_available_memory()
        #else
        let avail = 0
        #endif
        guard kr == KERN_SUCCESS, avail > 0 else { return nil }
        return Int(info.phys_footprint / (1024 * 1024)) + Int(avail / (1024 * 1024))
    }

    /// The user's answer to "what happened?" after a session that never came back:
    /// "froze", "closed-by-itself" or "i-closed" (which leaves everything as it was).
    static func answer(_ said: String) {
        guard enabled, let p = pending else { return }
        pending = nil
        var end = p.marker.end ?? AutoTuneSessionEnd(sawProcess: true, seconds: 0, killed: true, limitMB: p.marker.limitMB)
        end.userSaid = said
        pendingEnter(p.marker.windowsPath)   // night round 2026-11-11 (T3 S7)
        DispatchQueue.global(qos: .utility).async {
            analyse(p.marker, end: end, log: p.log)
            pendingLeave(p.marker.windowsPath)
        }
    }

    // MARK: Analysis (utility queue, no game running)

    /// How WineProcessBridge.m echoes a line of the game's file at launch: env.NAME lines as
    /// "[madeira-env] game NAME=value", the others as "[game-cfg]   key = value".
    static func echo(_ line: String) -> String {
        let key = AutoTuneRules.key(line)
        let value = line.split(separator: "=", maxSplits: 1).dropFirst().first.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
        return key.hasPrefix("env.") ? "[madeira-env] game \(key.dropFirst(4))=\(value)" : "[game-cfg]   \(key) = \(value)"
    }

    private static func analyse(_ m: Marker, end: AutoTuneSessionEnd, log: URL) {
        let rules = rules()
        let before = state(m.windowsPath)
        // The lines this session was to try must show up in its log, or the attempt says nothing.
        let tried = (before.candidate ?? []).map { AutoTuneSignal(has: [echo($0)]) }
        let ruleSignals = AutoTuneEngine.signals(rules)
        guard let scanner = AutoTuneLogScanner.scan(log, signals: ruleSignals + tried) else { return }
        // madeira-bcd (round 579): a Dock session that ran only the one-time installs, and whose batch ended, is no session of the
        // game (BCD quits itself for a restart, which a recovered marker reads as a kill and the next start asks about, and an
        // installer's helper may fail): it is not scored, not asked about and changes nothing, so the game's search goes on from
        // where it was. A batch that never ended (an installer that hangs) is scored as before.
        if scanner.facts.installsOver {
            LogStore.shared.log("[autotune] \(m.title): this session ran only the one-time installs; it is not a session of the game and is not scored")
            return
        }
        // Night round 2026-11-11 (agent G2 S8; BlueField SM3JHE and VW6TV2): BCD asked the debugger for the JIT pool, none answered, and the
        // start ended before Wine began: not a session of the game, so nothing is scored and no line is kept or dropped for it.
        if scanner.facts.wineNeverStarted {
            LogStore.shared.log("[autotune] \(m.title): Wine never started (no debugger answered BCD's request for the JIT pool); this is not a session of the game and is not scored")
            return
        }
        var report = AutoTuneEngine.report(end, scanner, rules: rules, signals: ruleSignals + tried)
        let missing = zip(before.candidate ?? [], scanner.counts.suffix(tried.count)).filter { $0.1 == 0 }.map(\.0)
        if !missing.isEmpty && report.outcome != "inconclusive" {
            LogStore.shared.log("[autotune] \(m.title): the session ran without \(missing.joined(separator: "; ")); this attempt is not counted")
            report.outcome = "inconclusive"
        }
        // The app never came back and the log does not say why: ask before deciding anything,
        // unless the game already plays (found, ok): then a silent end is no news.
        if report.outcome == "inconclusive", end.killed, end.userSaid == nil {
            let state = state(m.windowsPath)
            if state.status == "found" || state.status == "ok" { return }
            let offer = AutoTuneOffer(title: m.title, windowsPath: m.windowsPath, report: report, state: state,
                                      reason: nil, ask: true)
            DispatchQueue.main.async {
                pending = (m, log)
                AutoTuneOffers.shared.offer = offer
            }
            return
        }
        let after = AutoTuneEngine.advance(before, report: report, rules: rules, userKeys: Set(m.userKeys),
                                           fingerprint: m.fingerprint, log: log.lastPathComponent, listValues: m.listValues ?? [:])
        save(after, m.windowsPath)
        let next = after.candidate.map { $0.isEmpty ? "the same settings once more" : $0.joined(separator: "; ") } ?? "nothing"
        // What became of the line this session tried, and which lines stay (text only): a kept line is never taken back
        // by itself, and the logs of build 576 could not say why env.MADEIRA_FEX_AVX = 1 had been kept on a game
        // whose every session after it ended earlier (Rockstar Launcher: GBY65K, Y5MEB6, A50T0Q).
        var verdict = ""
        if report.outcome != "inconclusive", let last = after.attempts.last, let rule = last.rule {
            verdict = "; tried \(rule): " + (last.kept == true ? "kept" : "reverted")
        }
        let keptNow = after.kept.isEmpty ? "" : "; kept \(after.kept.joined(separator: ", "))"
        // Night round 2026-11-11 (agent T1 S1): why a trial was kept or reverted; (agent T2 S-DL3): which file was read and where the matched
        // rules' signals first appeared in it, so that a verdict no log reproduces (Dying Light WF4EHP) can be checked.
        let trial = after.trialNote.map { "; " + $0 } ?? ""
        let firstSeen = AutoTuneEngine.matchedLines(report.matched, rules: rules, signals: ruleSignals + tried, scanner: scanner)
        let readFrom = "; read \(log.lastPathComponent) (\(scanner.lineCount) lines" + (firstSeen.isEmpty ? ")" : "; " + firstSeen.joined(separator: ", ") + ")")
        LogStore.shared.log("[autotune] \(m.title): \(report.outcome) at stage \(report.stage) after \(Int(report.seconds)) s; "
                            + "matched \(report.matched.joined(separator: ",")); status \(after.status); next \(next)"
                            + verdict + trial + keptNow + (after.skippedNote.map { "; skipped " + $0 } ?? "") + readFrom)   // round 624 (E1 S5b)
        let offer = AutoTuneOffer(title: m.title, windowsPath: m.windowsPath, report: report, state: after,
                                  reason: after.candidateRule.flatMap { id in rules.first { $0.id == id }?.reason })
        DispatchQueue.main.async {
            // Round 630 (agent U S15b; G3SF3A): a recovered session's analysis ends while the next session of the same game runs. When that
            // session already carries every line the result asks the next start to try, "The next start tries ..." is wrong: no result now;
            // the running session's own comes when it ends. (armed is read on the main thread, where the session hooks set it.)
            if let running = armed, running.windowsPath == m.windowsPath, let lines = after.candidate, !lines.isEmpty,
               Set(lines).isSubset(of: Set(running.layer ?? [])) {
                LogStore.shared.log("[autotune] \(m.title): result not shown, the running session already carries \(lines.joined(separator: "; "))")
                return
            }
            // madeira-bcd (Porthole port, item 10): the player put this very fix off and it still waits: nothing new to say (the game's page has it).
            if AutoTuneAsk.quiet(offer) {
                LogStore.shared.log("[autotune] \(m.title): result not shown, the fix it would try waits for the player's yes")
                return
            }
            AutoTuneOffers.shared.offer = offer
        }
    }
}

/// What the app tells the user after a tuned session (ContentView's alert).
struct AutoTuneOffer: Equatable {
    var title: String
    var windowsPath: String
    var report: AutoTuneReport
    var state: AutoTuneState
    var reason: String?
    /// The session never came back to the library and its log does not say why.
    var ask = false

    var headline: String {
        if ask { return "What happened to \(title)?" }
        switch state.status {
        case "found": return "\(title) played with the lines auto-tune found"
        case "ok": return "\(title) played"
        case "stuck": return "\(title): no known fix fits this failure (tried: \(state.triedSummary))"
        case "gave-up": return "\(title): auto-tune stopped after \(AutoTuneEngine.maxAttempts) attempts"
        case "known":
            // Night round 2026-11-11 (agent G10 S4): a rule's own lead ("Choose another OpenGL backend") says what to do instead.
            if let lead = state.knownLeadText, let first = lead.first { return "\(title): \(first.lowercased())\(lead.dropFirst())" }
            return state.knownNoFix == true ? "\(title): no setting changes this failure" : "\(title): a crash in BCD, not a setting"
        default: return "\(title) \(AutoTuneOffer.outcomeText(report.outcome))"
        }
    }
    var message: String {
        if ask {
            return "The last session of \(title) did not come back to the library and its log does not say why. "
                + "Auto-tune uses your answer to decide what to try next."
        }
        var text = ""
        // The game's own error box says more than any rule (Darksiders II: an unsupported resolution).
        if let said = report.dialogs.last(where: { $0.count > 20 }) ?? report.dialogs.last {
            text += "The game said: \u{201C}\(said.replacingOccurrences(of: "?", with: " "))\u{201D}\n\n"
        }
        // Wine's own "Wine Mono is not installed" (a .NET program, Panzer Paladin): no setting helps
        // until Mono is downloaded, so say that first (the game page shows the same note).
        if report.monoMissing == true && !WineMonoModel.available {
            text += "This game needs Wine Mono (.NET). Download it in Settings \u{203A} .NET Framework, then play again.\n\n"
        }
        switch state.status {
        case "found":
            text += "Kept: " + state.kept.joined(separator: "; ") + ". Write them to the game's config on the game's page, under Auto-tune."
        case "ok":
            text += "Nothing was added."
        case "stuck", "gave-up":
            if MemoryRights.lacking { text += MemoryRights.failureNote + "\n\n" }   // round 624 (agent N S1): the install is the usual reason
            // madeira-bcd (round 624, Ghost of Tsushima JKJA9Q/VQ9SBA): a game iOS closed for memory with nothing left to try: the game's own
            // graphics settings are what is left.
            if report.outcome == "killed" {
                text += "iOS closed the game at its memory limit. Set the game's own graphics to the lowest preset (and any resolution or "
                    + "texture setting to Low) and play again.\n\n"
                // Night round 2026-11-11 (T1 S2; Spider-Man Remastered and Miles Morales, DOA6, Batman: Arkham Knight on phones that give an app 6 GB: 9 logs
                // closed at 98 to 100 % of the limit in the first minute): the phone class, so the player knows why the lowest preset may not be enough.
                if let words = Self.sixGBClassWords(title: title, peakMB: report.peakMB, memoryPct: report.memoryPct, ramMB: report.ramMB) {
                    text += words + "\n\n"
                }
            }
            if let note = state.lowDiskNote { text += note + "\n\n" }   // round 624 (agent D3 S5-3): no swap file was tried for lack of storage
            text += "Export the auto-tune report on the game's page, under Auto-tune, to report it."
        case "known":
            text += state.knownAdvice
        default:
            if let c = state.candidate {
                // madeira-bcd (round 624, agent D3 S5 (d); Sekiro XJ8YM3/ZJYGS9, Cloudpunk F88RB4): a memory kill on a device that lets apps use about
                // 4 GB, with a swap file next: say why (the peak and the device class), once, before the lines.
                if report.outcome == "killed", c.contains(where: { $0.hasPrefix("swap-mb") }), let peak = report.peakMB, peak <= 4600,
                   (report.ramMB ?? Int.max) <= 8000 {
                    // Night round 2026-11-11 (T3 S3; Vampire: The Masquerade WE9D10, The Sims 2 2P7QQY and 6B98A9): the number is the device's own limit
                    // (a 4 GB phone's limit is 2347 MB, not 4 GB), read from how full the memory was when iOS closed the game.
                    let limit = Self.limitWords(peakMB: peak, memoryPct: report.memoryPct)
                    text += "This iPhone lets apps use about \(limit), and \(title) needed more (its memory reached \(peak) MB). A swap file moves part "
                        + "of it to storage; an iPad or a newer iPhone has more room.\n\n"
                }
                text += c.isEmpty ? "The next start runs the same settings once more: a first failure after an update often clears on its own."
                                 : "The next start tries: " + c.joined(separator: "; ") + "."
                if let reason { text += " " + reason }
                text += " Restart BCD and play again."
            }
        }
        return text
    }
    /// Night round 2026-11-11 (T3 S3): the device's memory limit in words, "4 GB" or "2.3 GB", from the peak and how many percent of the limit the last
    /// footprint line was (AutoTuneReport.memoryPct; a kill is at 90 to 100 %). Without a usable percentage the old "4 GB" stays.
    static func limitWords(peakMB: Int, memoryPct: Int?) -> String {
        guard let pct = memoryPct, (50...110).contains(pct), peakMB > 0 else { return "4 GB" }
        let gb = (Double(peakMB) * 100 / Double(pct) / 1024 * 10).rounded() / 10
        return gb == gb.rounded() ? "\(Int(gb)) GB" : String(format: "%.1f GB", gb)
    }

    /// Night round 2026-11-11 (T1 S2): for a memory kill on a phone whose limit is about 6 GB (8 GB phones: 6143 MB), the class in words; nil for any
    /// other device. The limit is estimated as in limitWords; a phone with more than 8 GB of memory, or no usable percentage, gets nothing.
    static func sixGBClassWords(title: String, peakMB: Int?, memoryPct: Int?, ramMB: Int?) -> String? {
        guard let peak = peakMB, let pct = memoryPct, (50...110).contains(pct), peak > 0, (ramMB ?? Int.max) <= 8000 else { return nil }
        let gb = Double(peak) * 100 / Double(pct) / 1024
        guard gb >= 5.5, gb < 6.8 else { return nil }
        return "This device lets apps use about 6 GB, and \(title) needed more (its memory reached \(peak) MB). A game this size needs a phone that "
            + "gives apps more than 6 GB: an iPad or a 12 GB iPhone has the room."
    }

    static func outcomeText(_ outcome: String) -> String {
        switch outcome {
        case "crashed": return "crashed"
        case "killed": return "was closed by iOS (memory)"
        case "hung": return "froze"
        case "exited-early": return "closed before it got going"
        default: return "ended"
        }
    }
}

final class AutoTuneOffers: ObservableObject {
    static let shared = AutoTuneOffers()
    @Published var offer: AutoTuneOffer?
}
