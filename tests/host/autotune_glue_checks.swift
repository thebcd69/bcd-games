// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: auto-tune's app side on the host (docs/AUTO-TUNE.md): app/Madeira/AutoTune.swift
// with AutoTuneCore.swift, the rule table and tests/host/autotune_glue_stubs.swift.
//
//   glue off   run with "-madeira.autotune NO" (UserDefaults' argument domain): every entry point is called with a
//              tuned game, a leftover marker and a state file on disk, and nothing may
//              change: files, MADEIRA_CFG_GAME, log lines, the offer.
//   glue default  no argument for the key: auto-tune is on for the app and for every game.
//   glue on    run with "-madeira.autotune YES" (UserDefaults' argument domain): the launch
//              file, the marker, the analysis after a session, a killed run recovered at
//              the next start, and the question when its log says nothing.
// AUTOTUNE_TEST_CONFIGS, AUTOTUNE_TEST_DOCS and AUTOTUNE_TEST_LOG name temporary paths;
// AUTOTUNE_TEST_CRASH and AUTOTUNE_TEST_SILENT name fixtures (a crash, a silent end); AUTOTUNE_TEST_INSTALLS and
// AUTOTUNE_TEST_INSTALLS_FAILED name sessions that only ran the one-time installs (they succeeded; one failed).

import Foundation

@main
struct AutoTuneGlueChecks {
    static var failures = 0
    static func expect(_ cond: Bool, _ what: String, line: UInt = #line) {
        if cond { print("ok   \(what)") } else { print("FAIL \(what) (line \(line))"); failures += 1 }
    }
    static let env = ProcessInfo.processInfo.environment
    static let configs = URL(fileURLWithPath: env["AUTOTUNE_TEST_CONFIGS"]!, isDirectory: true)
    static let docs = URL(fileURLWithPath: env["AUTOTUNE_TEST_DOCS"]!, isDirectory: true)
    static let log = URL(fileURLWithPath: env["AUTOTUNE_TEST_LOG"]!)
    static let game = LibraryEntry(windowsPath: "C:\\Games\\Test Game\\Game.exe", title: "Test Game", desktop: nil, gameConfigText: nil)

    /// Lets queued work run: the analysis runs on a utility queue and publishes on main.
    static func settle(until: () -> Bool, seconds: Double = 10) -> Bool {
        let end = Date().addingTimeInterval(seconds)
        while Date() < end {
            if until() { return true }
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        return until()
    }

    static func snapshot(_ dirs: [URL]) -> [String: Date] {
        var out: [String: Date] = [:]
        for d in dirs {
            guard let e = FileManager.default.enumerator(at: d, includingPropertiesForKeys: [.contentModificationDateKey]) else { continue }
            for case let u as URL in e {
                out[u.path] = (try? u.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            }
        }
        return out
    }

    /// What WineProcessBridge prints at launch for the lines this session runs with.
    static func echoCandidate() {
        let lines = AutoTune.state(game.windowsPath).candidate ?? []
        guard !lines.isEmpty, let h = try? FileHandle(forWritingTo: log) else { return }
        h.seekToEndOfFile()
        h.write(Data(lines.map { AutoTune.echo($0) + "\n" }.joined().utf8))
        try? h.close()
    }

    static func copy(_ from: String, to: URL) {
        try? FileManager.default.removeItem(at: to)
        try? FileManager.default.copyItem(at: URL(fileURLWithPath: from), to: to)
    }

    static func main() {
        let mode = CommandLine.arguments.dropFirst().first ?? ""
        // The persistent domain may hold an earlier run's values (cfprefsd does not follow
        // CFFIXED_USER_HOME): start clean, before anything reads AutoTune.enabled. "on"
        // comes from the argument domain only; both keys are removed again at the end.
        UserDefaults.standard.removeObject(forKey: AutoTune.globalKey)
        UserDefaults.standard.removeObject(forKey: AutoTune.gamesKey)
        defer {
            UserDefaults.standard.removeObject(forKey: AutoTune.globalKey)
            UserDefaults.standard.removeObject(forKey: AutoTune.gamesKey)
        }
        let profile = GameProfile(windowsPath: game.windowsPath)
        profile.text = "# the user's own lines\nvram-mb = 3072\nenv.MADEIRA_X = 2\n"
        copy(env["AUTOTUNE_TEST_CRASH"]!, to: log)
        if mode == "off" { off(profile) } else if mode == "default" { byDefault() } else { on(profile) }
        UserDefaults.standard.removeObject(forKey: AutoTune.globalKey)
        UserDefaults.standard.removeObject(forKey: AutoTune.gamesKey)
        print(failures == 0 ? "PASS: auto-tune app side (\(mode))" : "FAILED: auto-tune app side (\(mode), \(failures))")
        exit(failures == 0 ? 0 : 1)
    }

    static func off(_ profile: GameProfile) {
        expect(!AutoTune.enabled, "with -madeira.autotune NO auto-tune is off")
        // Everything a tuned game could have on disk, as if auto-tune had been on before.
        UserDefaults.standard.set([game.windowsPath.lowercased(): true], forKey: AutoTune.gamesKey)
        var st = AutoTuneState()
        st.status = "trying"; st.candidate = ["env.MADEIRA_CAGE_RELEASE = 1"]; st.candidateRule = "cage-release-band-exhausted"
        AutoTune.save(st, game.windowsPath)
        try? Data("{}".utf8).write(to: AutoTune.markerURL!)
        setenv("MADEIRA_CFG_GAME", "/original/path.cfg", 1)
        Thread.sleep(forTimeInterval: 1.1)   // so a rewrite would show in the modification times
        let before = snapshot([configs, docs])

        AutoTune.exportLayer(for: game)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, crashStatus: 0xC0000005, seconds: 20))
        AutoTune.recoverUnfinishedSession()
        AutoTune.answer("froze")
        _ = settle(until: { false }, seconds: 1.5)

        expect(snapshot([configs, docs]) == before, "off: no file is written, moved or removed (\(before.count) files)")
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == "/original/path.cfg", "off: MADEIRA_CFG_GAME is untouched")
        expect(LogStore.shared.lines.isEmpty, "off: no log line (\(LogStore.shared.lines.count))")
        expect(AutoTuneOffers.shared.offer == nil, "off: no offer")
        expect(!FileManager.default.fileExists(atPath: configs.appendingPathComponent("autotune-launch.cfg").path),
               "off: no launch file")
    }

    /// No argument for the key at all, as on a device that never touched the setting: auto-tune is on,
    /// for the app and for every game; a stored off is kept, and Settings' off applies from the next start.
    static func byDefault() {
        expect(UserDefaults.standard.object(forKey: AutoTune.globalKey) == nil, "the setting is absent")
        expect(AutoTune.enabled && AutoTune.preferred, "with the setting absent auto-tune is on")
        expect(AutoTune.isOn(game.windowsPath), "and so is every game")
        AutoTune.preferred = false
        expect(!AutoTune.preferred && AutoTune.enabled, "turned off in Settings: stored now, this run keeps its value")
        AutoTune.preferred = true
        expect(AutoTune.preferred, "and on again")
        AutoTune.setOn(game.windowsPath, false)
        expect(!AutoTune.isOn(game.windowsPath) && AutoTune.isOn("c:\\other\\game.exe"), "a game's own off is kept for that game only")
        AutoTune.setOn(game.windowsPath, true)
        expect(AutoTune.isOn(game.windowsPath), "and its on")
    }

    static func on(_ profile: GameProfile) {
        expect(AutoTune.enabled, "with -madeira.autotune YES auto-tune is on")
        let launch = configs.appendingPathComponent("autotune-launch.cfg")

        // Every game starts with its switch on; the owner of the device can turn it off, and that is kept.
        expect(AutoTune.isOn(game.windowsPath), "a game's own switch is on until it is turned off")
        AutoTune.setOn(game.windowsPath, false)
        expect(!AutoTune.isOn(game.windowsPath) && !AutoTune.isOn(game.windowsPath.uppercased()), "a game turned off stays off, any case")
        expect(AutoTune.isOn(game.windowsPath + "-other"), "turning one game off leaves the others on")
        setenv("MADEIRA_CFG_GAME", profile.url!.path, 1)
        AutoTune.exportLayer(for: game)
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == profile.url!.path && !FileManager.default.fileExists(atPath: launch.path),
               "a game whose own switch is off runs as before")
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "and gets no marker")

        AutoTune.setOn(game.windowsPath, true)
        expect(AutoTune.isOn(game.windowsPath) && AutoTune.isOn(game.windowsPath.uppercased()), "the game's switch is per game, any case")

        // A first session: nothing to add yet, the game runs exactly as configured.
        AutoTune.exportLayer(for: game)
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == profile.url!.path, "the baseline session runs the game's own file")

        // The baseline crashed (the Rift Apart 23:02 fixture): marker, analysis, offer.
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        expect(FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "a tuned session writes the marker")
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 25))
        expect(settle(until: { AutoTuneOffers.shared.offer != nil }), "the session's analysis publishes an offer")
        var s = AutoTune.state(game.windowsPath)
        expect(s.attempts.count == 1 && s.attempts[0].outcome == "crashed", "the baseline is recorded as crashed")
        expect(s.candidate == [] && s.candidateRule == "retry", "first: the same settings once more")
        // The analysis publishes the offer on the main queue and removes the marker when it
        // returns on its own queue: wait for that too (a loaded Mac lost this race, 2026-10-18).
        expect(settle(until: { !FileManager.default.fileExists(atPath: AutoTune.markerURL!.path) }),
               "the marker is gone after the analysis")
        expect(AutoTuneOffers.shared.offer?.state.candidate == [], "the offer says what the next start runs")
        if var mono = AutoTuneOffers.shared.offer {
            // Wine's "Wine Mono is not installed" (Panzer Paladin): the alert says what to do first.
            mono.report.monoMissing = true
            expect(mono.message.contains("This game needs Wine Mono (.NET). Download it in Settings \u{203A} .NET Framework"),
                   "a session that died for want of Wine Mono: the alert says to download it")
            mono.report.monoMissing = nil
            expect(!mono.message.contains("Wine Mono"), "any other session: no Wine Mono sentence")
        } else {
            expect(false, "an offer to check the Wine Mono sentence on")
        }
        // Night round 2026-11-11 (T3 S3, T1 S2): the memory limit in the memory-kill advice is the device's own, and a 6 GB phone is named.
        expect(AutoTuneOffer.limitWords(peakMB: 2244, memoryPct: 95) == "2.3 GB" && AutoTuneOffer.limitWords(peakMB: 2122, memoryPct: 90) == "2.3 GB",
               "WE9D10, 2P7QQY: a 4 GB phone (limit 2347 MB) reads 2.3 GB, not 4 GB")
        expect(AutoTuneOffer.limitWords(peakMB: 4000, memoryPct: 98) == "4 GB" && AutoTuneOffer.limitWords(peakMB: 4096, memoryPct: 100) == "4 GB",
               "a limit of 4 GB still reads 4 GB")
        expect(AutoTuneOffer.limitWords(peakMB: 4000, memoryPct: nil) == "4 GB" && AutoTuneOffer.limitWords(peakMB: 4000, memoryPct: 20) == "4 GB"
               && AutoTuneOffer.limitWords(peakMB: 0, memoryPct: 99) == "4 GB", "no usable percentage: the words it had")
        expect(AutoTuneOffer.sixGBClassWords(title: "Spider-Man", peakMB: 6062, memoryPct: 98, ramMB: 7500)
                == "This device lets apps use about 6 GB, and Spider-Man needed more (its memory reached 6062 MB). A game this size needs a phone that gives apps more than 6 GB: an iPad or a 12 GB iPhone has the room.",
               "C0S8PX: a 6 GB-limit phone is named, with the way out")
        expect(AutoTuneOffer.sixGBClassWords(title: "G", peakMB: 8097, memoryPct: 99, ramMB: 11500) == nil
               && AutoTuneOffer.sixGBClassWords(title: "G", peakMB: 2244, memoryPct: 95, ramMB: 3800) == nil
               && AutoTuneOffer.sixGBClassWords(title: "G", peakMB: 6062, memoryPct: nil, ramMB: 7500) == nil
               && AutoTuneOffer.sixGBClassWords(title: "G", peakMB: 6062, memoryPct: 98, ramMB: nil) == nil,
               "a 12 GB phone (limit 8191), a 4 GB phone, an unknown percentage or an unknown RAM: no 6 GB sentence")
        AutoTuneOffers.shared.offer = nil

        // The retry: the same crash -> the first rule.
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 25))
        expect(settle(until: { AutoTuneOffers.shared.offer != nil }), "the retry is analysed")
        s = AutoTune.state(game.windowsPath)
        expect(s.candidate == ["env.MADEIRA_CAGE_RELEASE = 1"], "then the cage line (\(s.candidate ?? []))")
        AutoTuneOffers.shared.offer = nil

        // The launch file: the candidate under the game's own lines, a key the user sets is left out.
        var forced = s
        forced.kept = ["vram-mb = 4096"]   // as if kept before the user set vram-mb
        AutoTune.save(forced, game.windowsPath)
        AutoTune.exportLayer(for: game)
        let text = (try? String(contentsOf: launch, encoding: .utf8)) ?? ""
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == launch.path, "MADEIRA_CFG_GAME names the launch file")
        expect(text.hasPrefix("# BCD auto-tune, attempt"), "the launch file says what it is")
        expect(!text.split(separator: "\n").contains { $0.hasPrefix("#") && $0.range(of: "madeira", options: .caseInsensitive) != nil },
               "its comment lines say nothing of Madeira")
        expect(text.contains("env.MADEIRA_CAGE_RELEASE = 1\n"), "it carries the candidate")
        expect(!text.contains("vram-mb = 4096"), "a key the game's own file sets is never added")
        if let c = text.range(of: "env.MADEIRA_CAGE_RELEASE = 1"), let own = text.range(of: "vram-mb = 3072") {
            expect(c.lowerBound < own.lowerBound, "the game's own lines come after it, so they win")
        } else { expect(false, "the game's own lines are in the launch file") }
        expect(LogStore.shared.lines.contains { $0.hasPrefix("[autotune] Test Game: this session adds") }, "the launch says what it adds")
        AutoTune.save(s, game.windowsPath)

        // Not eligible: the Desktop.
        setenv("MADEIRA_CFG_GAME", "/desktop.cfg", 1)
        AutoTune.exportLayer(for: LibraryEntry(windowsPath: game.windowsPath, title: "Desktop", desktop: true, gameConfigText: nil))
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == "/desktop.cfg", "the Desktop is never tuned")
        // A Dock session without a library entry is never armed.
        AutoTune.sessionBegan(game, remember: false, dockWithoutEntry: true)
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "a Dock start without an entry gets no marker")

        // Round 591: a program that is no game is never tuned (PJE6FT's .NET installer was filed under a game, 4W9GX4's Steam helper under "Steam").
        func entry(_ path: String) -> LibraryEntry { LibraryEntry(windowsPath: path, title: "x", desktop: nil, gameConfigText: nil) }
        expect(AutoTune.isNoGame(#"C:\Program Files (x86)\Steam\steamapps\shadercache\fossilize-replay64.exe"#)
               && AutoTune.isNoGame(#"C:\Program Files (x86)\Steam\bin\cef\cef.win64\steamwebhelper.exe"#)
               && AutoTune.isNoGame(#"C:\LEGO_MARVEL_Super_Heroes\LEGO MARVEL Super Heroes\~Common Redist\NDP48-x86-x64-AllOS-ENU.exe"#)
               && AutoTune.isNoGame(#"C:\Games\Foo\_CommonRedist\vcredist\2015\vc_redist.x64.exe"#)
               && AutoTune.isNoGame(#"C:\Games\Foo\DirectX\dxsetup.exe"#)
               && AutoTune.isNoGame("C:/Games/Foo/redist/anything.exe"),
               "a Steam helper and the redistributable installers are no game")
        expect(!AutoTune.isNoGame(#"C:\Program Files (x86)\Steam\steamapps\common\Hades\Hades.exe"#)
               && !AutoTune.isNoGame(#"C:\Games\Redistricting Simulator\game.exe"#)
               && !AutoTune.isNoGame(#"C:\Program Files (x86)\Steam\steamapps\common\Foo"#)
               && !AutoTune.isNoGame(#"C:\Games\Foo\Setup.exe"#),
               "a game, a folder that only starts with the same letters, a folder entry and an unknown setup are tuned as before")
        setenv("MADEIRA_CFG_GAME", "/helper.cfg", 1)
        AutoTune.exportLayer(for: entry(#"C:\Program Files (x86)\Steam\steamapps\shadercache\fossilize-replay64.exe"#))
        AutoTune.sessionBegan(entry(#"C:\Program Files (x86)\Steam\steamapps\shadercache\fossilize-replay64.exe"#), remember: true, dockWithoutEntry: false)
        expect(String(cString: getenv("MADEIRA_CFG_GAME")) == "/helper.cfg" && !FileManager.default.fileExists(atPath: AutoTune.markerURL!.path),
               "a Steam helper gets no launch file and no marker")

        // Round 591: the recommended list's lines in the game's file are not the user's while the file is what BCD wrote from the list; a
        // line auto-tune kept that raises one of them wins over it in the launch file (the game's file itself is not touched).
        let listGame = LibraryEntry(windowsPath: "C:\\Games\\Listed\\Listed.exe", title: "Listed", desktop: nil, gameConfigText: nil)
        let listFile = GameProfile(windowsPath: listGame.windowsPath)
        listFile.text = "# BCD's recommended settings for Listed.\nvram-mb = 4096\nswap-mb = 3072\npool-split = 1\n"
        expect(AutoTune.listValues(listGame).isEmpty && AutoTune.userKeys(listGame) == ["vram-mb", "swap-mb", "pool-split"],
               "no record of a list for the game: all its keys are the user's, as before")
        setenv("AUTOTUNE_TEST_FOLLOWS_LIST", "1", 1)
        expect(AutoTune.listValues(listGame) == ["vram-mb": "4096", "swap-mb": "3072", "pool-split": "1"] && AutoTune.userKeys(listGame).isEmpty,
               "a file that is exactly the list's: its lines are the list's")
        let withOwn = LibraryEntry(windowsPath: listGame.windowsPath, title: "Listed", desktop: nil, gameConfigText: "pool-split = 0\n")
        expect(AutoTune.userKeys(withOwn) == ["pool-split"] && AutoTune.listValues(withOwn)["pool-split"] == nil && AutoTune.listValues(withOwn)["swap-mb"] == "3072",
               "a key the entry's own lines also set is the user's")
        var kept = AutoTuneState()
        kept.status = "found"; kept.kept = ["swap-mb = 6144", "swap-mode = 2", "swap-min-mb = 1"]
        AutoTune.save(kept, listGame.windowsPath)
        AutoTune.exportLayer(for: listGame)
        var layered = (try? String(contentsOf: launch, encoding: .utf8)) ?? ""
        expect(layered.contains("swap-mb = 6144\n") && !layered.contains("swap-mb = 3072") && layered.contains("vram-mb = 4096\n") && layered.contains("pool-split = 1\n")
               && layered.contains("swap-mode = 2\n") && layered.contains("# BCD's recommended settings for Listed."),
               "the launch file carries 6144 in place of the list's 3072 and the rest of the list's file")
        expect(listFile.text.contains("swap-mb = 3072"), "the game's file itself still says 3072")
        kept.kept = ["swap-mb = 2048"]
        AutoTune.save(kept, listGame.windowsPath)
        AutoTune.exportLayer(for: listGame)
        layered = (try? String(contentsOf: launch, encoding: .utf8)) ?? ""
        if let low = layered.range(of: "swap-mb = 2048"), let list = layered.range(of: "swap-mb = 3072") {
            expect(low.lowerBound < list.lowerBound, "a lower number never replaces the list's: the file's line comes later and wins")
        } else { expect(false, "both swap-mb lines are in the launch file") }
        setenv("AUTOTUNE_TEST_FOLLOWS_LIST", "0", 1)
        expect(AutoTune.listValues(listGame).isEmpty && AutoTune.userKeys(listGame) == ["vram-mb", "swap-mb", "pool-split"],
               "a file the player changed: every key is the user's again")
        unsetenv("AUTOTUNE_TEST_FOLLOWS_LIST")
        AutoTune.reset(listGame.windowsPath)
        listFile.text = ""

        // A session whose log never shows the candidate (it did not reach the game) counts for nothing.
        let notApplied = AutoTune.state(game.windowsPath)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 25))
        expect(settle(until: { AutoTune.state(game.windowsPath).attempts.count == notApplied.attempts.count + 1 }),
               "a session without the candidate in its log is analysed")
        let na = AutoTune.state(game.windowsPath)
        expect(na.attempts.last?.outcome == "inconclusive" && na.candidate == notApplied.candidate && na.kept == notApplied.kept,
               "and counts for nothing: the same candidate runs next time")
        expect(LogStore.shared.lines.contains { $0.contains("the session ran without env.MADEIRA_CAGE_RELEASE = 1") },
               "the log says the line did not reach the session")
        AutoTuneOffers.shared.offer = nil

        // A killed run: the marker is left behind, the next start finds the log by its inode.
        let logs = docs.appendingPathComponent("logs", isDirectory: true)
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        // The session ran with the candidate: WineProcessBridge echoes it at launch.
        if let h = try? FileHandle(forWritingTo: log) {
            h.seekToEndOfFile(); h.write(Data("[madeira-env] game MADEIRA_CAGE_RELEASE=1\n".utf8)); try? h.close()
        }
        let hardLink = logs.appendingPathComponent("Game.exe-2026-10-16_10-00-00.txt")
        try? FileManager.default.linkItem(at: log, to: hardLink)   // what LogStore.startSessionLog does
        try? FileManager.default.removeItem(at: log)                 // the next start rotated bcd-log.txt away
        let attemptsBefore = AutoTune.state(game.windowsPath).attempts.count
        AutoTune.recoverUnfinishedSession()
        expect(settle(until: { AutoTune.state(game.windowsPath).attempts.count == attemptsBefore + 1 }),
               "a killed run is analysed at the next start from its logs/ hard link")
        expect(AutoTune.state(game.windowsPath).attempts.last?.outcome == "crashed", "its crash is recognised from the log alone")
        // The recovery removes its pending copy on the utility queue AFTER analyse() records the attempt (AutoTune.recoverUnfinishedSession),
        // so on a busy Mac the copy can still be there when the attempt count above already moved (2026-11-08: two of five full runs).
        expect(settle(until: { !FileManager.default.fileExists(atPath: AutoTune.markerURL!.path)
                               && !FileManager.default.fileExists(atPath: configs.appendingPathComponent("autotune-session.pending.json").path) }),
               "the marker and its pending copy are gone")
        AutoTuneOffers.shared.offer = nil

        // A killed run whose log says nothing: the user is asked, and the answer decides.
        copy(env["AUTOTUNE_TEST_SILENT"]!, to: log)
        echoCandidate()
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        let before = AutoTune.state(game.windowsPath).attempts.count
        // The next start: LogStore rotates bcd-log.txt to bcd-log.prev.txt (same inode).
        let prev = docs.appendingPathComponent("bcd-log.prev.txt")
        try? FileManager.default.removeItem(at: prev)
        try? FileManager.default.moveItem(at: log, to: prev)
        AutoTune.recoverUnfinishedSession()
        expect(settle(until: { AutoTuneOffers.shared.offer?.ask == true }), "a silent end asks what happened")
        expect(AutoTune.state(game.windowsPath).attempts.count == before, "nothing is decided before the answer")
        AutoTuneOffers.shared.offer = nil
        AutoTune.answer("froze")
        expect(settle(until: { AutoTune.state(game.windowsPath).attempts.count == before + 1 }), "the answer is analysed")
        expect(AutoTune.state(game.windowsPath).attempts.last?.outcome == "hung", "\"It froze\" makes it a hang")
        AutoTune.answer("froze")
        _ = settle(until: { false }, seconds: 0.5)
        expect(AutoTune.state(game.windowsPath).attempts.count == before + 1, "a second answer does nothing")

        // Night round 2026-11-11 (agent T3 S7; 2FR906, 9W3TW7, BZQMTZ, XVABPA, P1433Z): a launch of the same game that starts while the verdict
        // about its last session is still being written does NOT wait for it (the lead: exportLayer runs on the main thread; a wait of up to
        // 20 s there would freeze the interface): it starts at once and says so, and the verdict still lands for the start after it.
        do {
            AutoTuneOffers.shared.offer = nil
            copy(env["AUTOTUNE_TEST_CRASH"]!, to: log)
            echoCandidate()
            AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
            try? FileManager.default.removeItem(at: prev)
            try? FileManager.default.moveItem(at: log, to: prev)
            let attemptsNow = AutoTune.state(game.windowsPath).attempts.count
            AutoTune.recoverUnfinishedSession()
            let began = Date()
            AutoTune.exportLayer(for: game)   // the next start, at once: no settle() before it
            expect(Date().timeIntervalSince(began) < 1.0, "a launch right after the recovery does not wait for the verdict (main thread)")
            _ = settle(until: { !FileManager.default.fileExists(atPath: AutoTune.markerURL!.path)
                                && !FileManager.default.fileExists(atPath: configs.appendingPathComponent("autotune-session.pending.json").path) })
            expect(AutoTune.state(game.windowsPath).attempts.count == attemptsNow + 1, "the verdict about the session before it still lands")
            // The analysis posts its result on the main queue after it saved: let that happen here, not inside the next case.
            _ = settle(until: { AutoTuneOffers.shared.offer != nil }, seconds: 3)
            AutoTuneOffers.shared.offer = nil
        }

        // Night round 2026-11-11 (agent G10 S4): a known stop's own words lead the headline and the advice ("Choose another OpenGL backend").
        do {
            let rep = AutoTuneReport(outcome: "crashed", stage: 3, seconds: 30, presents: 0, peakMB: nil, dock: false, d3d12: false, ramMB: 11695,
                                     matched: ["opengl-es-for-desktop-game"], evidence: [])
            var st = AutoTuneState(); st.status = "known"; st.known = "opengl-es-for-desktop-game"; st.knownReason = "Choose Desktop OpenGL."; st.knownNoFix = true
            expect(AutoTuneOffer(title: "Isaac", windowsPath: "C:\\x.exe", report: rep, state: st, reason: nil).headline == "Isaac: no setting changes this failure",
                   "a known stop without its own words keeps the old headline")
            st.knownLeadText = "Choose another OpenGL backend"
            let offer = AutoTuneOffer(title: "Isaac", windowsPath: "C:\\x.exe", report: rep, state: st, reason: nil)
            expect(offer.headline == "Isaac: choose another OpenGL backend" && offer.message.hasSuffix("Choose another OpenGL backend: Choose Desktop OpenGL."),
                   "with its own words the headline and the advice use them")
        }

        // A session that only ran the one-time installs and whose batch ended is no session of the game (round 579): BCD closes
        // itself for a restart, the leftover marker would read as a kill and the user would be asked about it (NM88NQ).
        AutoTuneOffers.shared.offer = nil
        let beforeInstalls = AutoTune.state(game.windowsPath)
        copy(env["AUTOTUNE_TEST_INSTALLS"]!, to: log)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        try? FileManager.default.removeItem(at: prev)
        try? FileManager.default.moveItem(at: log, to: prev)
        AutoTune.recoverUnfinishedSession()
        _ = settle(until: { LogStore.shared.lines.contains { $0.contains("this session ran only the one-time installs") } }, seconds: 3)
        _ = settle(until: { false }, seconds: 0.5)
        expect(AutoTuneOffers.shared.offer == nil, "an installs-only session that BCD closed itself is not asked about")
        expect(AutoTune.state(game.windowsPath) == beforeInstalls, "and changes nothing: no attempt, no candidate, no kept line")
        expect(LogStore.shared.lines.contains { $0.hasPrefix("[autotune] Test Game: this session ran only the one-time installs") },
               "the log says it was not scored")
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path)
               && !FileManager.default.fileExists(atPath: configs.appendingPathComponent("autotune-session.pending.json").path),
               "its marker is gone too")
        // The same when the session ended on its own (an installer failed, the batch ended with it).
        copy(env["AUTOTUNE_TEST_INSTALLS_FAILED"]!, to: log)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        AutoTune.sessionEnded(AutoTuneSessionEnd(sawProcess: true, seconds: 12))
        expect(settle(until: { !FileManager.default.fileExists(atPath: AutoTune.markerURL!.path) }),
               "an installs-only session that ended on its own is analysed")
        _ = settle(until: { false }, seconds: 0.5)
        expect(AutoTuneOffers.shared.offer == nil && AutoTune.state(game.windowsPath) == beforeInstalls,
               "and it also offers nothing and changes nothing, whatever its installer's helper logged")
        AutoTuneOffers.shared.offer = nil

        // A game that already plays is not asked about after a silent end.
        AutoTuneOffers.shared.offer = nil
        var playing = AutoTune.state(game.windowsPath)
        playing.status = "ok"; playing.candidate = nil; playing.candidateRule = nil
        AutoTune.save(playing, game.windowsPath)
        copy(env["AUTOTUNE_TEST_SILENT"]!, to: log)
        AutoTune.sessionBegan(game, remember: true, dockWithoutEntry: false)
        try? FileManager.default.removeItem(at: prev)
        try? FileManager.default.moveItem(at: log, to: prev)
        AutoTune.recoverUnfinishedSession()
        _ = settle(until: { false }, seconds: 1.5)
        expect(AutoTuneOffers.shared.offer == nil && AutoTune.state(game.windowsPath) == playing,
               "a game that plays is not asked about a silent end, and nothing changes")

        expect(AutoTune.echo("env.MADEIRA_CAGE_RELEASE = 1") == "[madeira-env] game MADEIRA_CAGE_RELEASE=1"
               && AutoTune.echo("swap-mb = 6144") == "[game-cfg]   swap-mb = 6144",
               "the echo matches WineProcessBridge.m's two formats")

        // Undo all.
        AutoTune.reset(game.windowsPath)
        expect(AutoTune.state(game.windowsPath) == AutoTuneState(), "Undo all removes the lines and the history")
        // Turning it off: this run keeps its value (the argument domain also keeps "preferred"
        // on here), and a marker on disk is removed so the next start finds nothing to recover.
        try? Data("{}".utf8).write(to: AutoTune.markerURL!)
        AutoTune.preferred = false
        expect(AutoTune.enabled, "turning it off applies from the next start")
        expect(!FileManager.default.fileExists(atPath: AutoTune.markerURL!.path), "turning it off removes a session marker")
    }
}
