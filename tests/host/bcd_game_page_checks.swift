// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// BCD's game page: its rules, compiled on the host by tests/host/check-bcd-game-page.py with
// app/Madeira/BCDStore.swift and the rules part of app/Madeira/BCDGamePage.swift, then run.

import Foundation
import SwiftUI

@MainActor final class Recorder: BCDActions {
    var calls: [String] = []
    func play(_ game: BCDGame) { calls.append("play \(game.id)") }
    func install(_ game: BCDGame, place: BCDInstallPlace) { calls.append("install \(game.id) \(place)") }
    func update(_ game: BCDGame) { calls.append("update \(game.id)") }
    func uninstall(_ game: BCDGame) { calls.append("uninstall \(game.id)") }
    func verify(_ game: BCDGame) { calls.append("verify \(game.id)") }
    func pauseDownload(_ id: String) { calls.append("pause \(id)") }
    func resumeDownload(_ id: String) { calls.append("resume \(id)") }
    func cancelDownload(_ id: String) { calls.append("cancel \(id)") }
    func enableJIT() { calls.append("jit") }
    func signIn(_ store: BCDStoreFront) { calls.append("signin \(store)") }
    func signOut(_ store: BCDStoreFront) { calls.append("signout") }
    func addGameFromFiles() { calls.append("add") }
    func refreshLibrary() { calls.append("refresh") }
}

@MainActor final class Backend: BCDGameDetailBackend {
    var onChange: (() -> Void)?
    var onNotice: (() -> Void)?
    var calls: [String] = []
    var shown = BCDGameDetails()
    var log: BCDGameLog?
    var refresh = BCDRecommendedRefresh.upToDate
    var askedSettings: [Bool] = []
    func details(for game: BCDGame, settings: Bool) -> BCDGameDetails {
        askedSettings.append(settings)
        var d = shown
        if !settings { d.groups = [] }
        return d
    }
    func opened(_ game: BCDGame) { calls.append("opened \(game.id)") }
    func set(_ setting: String, to value: String, for game: BCDGame) { calls.append("set \(setting)=\(value)") }
    func syncCloud(_ game: BCDGame) { calls.append("sync") }
    func keepCloudSaves(_ game: BCDGame, fromCloud: Bool) { calls.append("keep \(fromCloud)") }
    func resetToRecommended(_ game: BCDGame) { calls.append("reset") }
    func refreshRecommendedSettings(for game: BCDGame) async -> BCDRecommendedRefresh { calls.append("latest"); return refresh }
    func applyRecommendedSettings(_ game: BCDGame) { calls.append("apply") }
    func setTuning(_ on: Bool, for game: BCDGame) { calls.append("tuning \(on)") }
    func undoTuning(_ game: BCDGame) { calls.append("untune") }
    func writeTuningLines(_ game: BCDGame) { calls.append("write") }
    func tuningReport(for game: BCDGame) -> String? { "report" }
    func shortcutLink(for game: BCDGame) -> String? { "bcd://play" }
    func backUpSaves() async throws -> BCDSavesBackup {
        BCDSavesBackup(file: URL(fileURLWithPath: "/tmp/x.zip"), files: 2, bytes: 2048, leftOut: 0)
    }
    func restoreSaves(from file: URL) async throws -> Int { calls.append("restore"); return 3 }
    func lastLog(of game: BCDGame) async -> BCDGameLog? { log }
    func logComment() async -> String? { "" }
    func send(_ log: BCDGameLog, of game: BCDGame, comment: String, progress: @escaping (String) -> Void) async throws -> String {
        calls.append("send \(log.name)"); progress("Sending…"); return "K7Q2XM"
    }
    var config = "fence-chain = 6\n"
    var coverFails = false
    func configText(for game: BCDGame) -> String? { config }
    func saveConfig(_ text: String, for game: BCDGame) { calls.append("config"); config = text }
    func searchStore(_ text: String) async throws -> [BCDStoreMatch] { calls.append("search \(text)"); return [BCDStoreMatch(id: 7, name: "Seven")] }
    func useStoreMatch(_ match: BCDStoreMatch, for game: BCDGame) { calls.append("match \(match.id)") }
    func setCover(from file: URL, for game: BCDGame) async throws {
        calls.append("cover \(file.lastPathComponent)")
        if coverFails { throw BCDGameError("Choose a picture under 20 MB.") }
    }
}

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: Any = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok ? "" : "\n     \(detail)"))
    if !ok { failures += 1 }
}

/// Dates are made from numbers (BCDFormat, the "dates" part of BCDStore.swift): the same text Foundation gives in the
/// device's time zone, for 4000 moments. The host test may use DateFormatter; the app may not. The zone is the process's
/// (check-bcd-game-page.py runs this with TZ set to several zones, "-datesOnly" for the dates alone).
func dateChecks() {
    let zone = TimeZone.current
    if let wanted = ProcessInfo.processInfo.environment["TZ"] {
        check("dates: the process runs in the zone the test asked for (\(wanted))", zone.identifier == wanted, zone.identifier)
    }
    // BCD modification (2026-10-26): keep every sampled moment and zone, but
    // configure each reference formatter once per dateChecks() invocation.
    // BCD_VERIFY_DATE_REFERENCES=1 cross-checks every result against a fresh
    // formatter, for validation when this test or Foundation changes.
    var referenceFormatters: [String: DateFormatter] = [:]
    let verifyFresh = ProcessInfo.processInfo.environment["BCD_VERIFY_DATE_REFERENCES"] == "1"
    var referenceComparisons = 0
    var referenceMismatches: [String] = []
    func makeReference(_ pattern: String, utc: Bool) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = utc ? TimeZone(identifier: "UTC")! : zone
        f.dateFormat = pattern
        return f
    }
    func foundation(_ pattern: String, _ date: Date, utc: Bool = false) -> String {
        let key = (utc ? "UTC:" : "local:") + pattern
        let f: DateFormatter
        if let existing = referenceFormatters[key] {
            f = existing
        } else {
            f = makeReference(pattern, utc: utc)
            referenceFormatters[key] = f
        }
        let result = f.string(from: date)
        if verifyFresh {
            referenceComparisons += 1
            let fresh = makeReference(pattern, utc: utc).string(from: date)
            if result != fresh { referenceMismatches.append("\(pattern) \(date): \(result) != \(fresh)") }
        }
        return result
    }
    var mismatches: [String] = []
    var seed: UInt64 = 0x9E37_79B9_7F4A_7C15
    let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for i in 0..<4000 {
        seed = seed &* 6364136223846793005 &+ 1442695040888963407
        // From 1970 to 2100; every seventh moment is a whole second plus a fixed fraction.
        let seconds = i % 7 == 0 ? (Double(seed >> 33) / Double(1 << 31) * 4_100_000_000).rounded(.down) + 0.987
                                 : Double(seed >> 33) / Double(1 << 31) * 4_100_000_000
        let date = Date(timeIntervalSince1970: seconds)
        let monthName = months[Int(foundation("M", date))! - 1]
        let day = "\(Int(foundation("d", date))!) \(monthName) \(foundation("yyyy", date))"
        let wants: [(String, String, String)] = [
            ("fullDate", BCDFormat.fullDate(date), day),
            ("dateTime", BCDFormat.dateTime(date), day + " at " + foundation("HH:mm", date)),
            ("reportTime", BCDFormat.reportTime(date), foundation("yyyy-MM-dd HH:mm", date)),
            ("fileStamp", BCDFormat.fileStamp(date), foundation("yyyy-MM-dd_HH-mm-ss", date)),
            ("fileStamp minutes", BCDFormat.fileStamp(date, seconds: false), foundation("yyyy-MM-dd_HH-mm", date)),
            ("compactStamp", BCDFormat.compactStamp(date), foundation("yyyyMMdd-HHmmss", date)),
            ("utcFolderStamp", BCDFormat.utcFolderStamp(date), foundation("yyyy-MM-dd'T'HH-mm-ss'Z'", date, utc: true)),
            ("logClock", BCDFormat.logClock(date), foundation("HH:mm:ss.SSS", date)),
        ]
        for (name, got, want) in wants where got != want { mismatches.append("\(name) \(seconds): \(got) != \(want)") }
        if mismatches.count > 5 { break }
    }
    check("dates in \(zone.identifier): the helpers give Foundation's text for 4000 moments", mismatches.isEmpty, mismatches.prefix(5))
    // The clock changes of the zone: each hour around the two changes of 2026, and the days at the year's edges.
    var edge: [String] = []
    var moment = 1_767_225_600.0 - 40 * 86_400   // 2025-12-31 minus 40 days
    while moment < 1_767_225_600.0 + 400 * 86_400 {
        let date = Date(timeIntervalSince1970: moment)
        if BCDFormat.fileStamp(date) != foundation("yyyy-MM-dd_HH-mm-ss", date) { edge.append("\(moment): \(BCDFormat.fileStamp(date))") }
        moment += 1_800
    }
    check("dates in \(zone.identifier): every half hour of 15 months, including the clock changes", edge.isEmpty, edge.prefix(5))
    let nowDate = Date(timeIntervalSince1970: 1_790_000_000)
    check("dates in \(zone.identifier): shortDate drops this year only",
          BCDFormat.shortDate(nowDate, now: nowDate).split(separator: " ").count == 2
          && BCDFormat.shortDate(Date(timeIntervalSince1970: 1_600_000_000), now: nowDate).hasSuffix(" 2020"))
    // "2026-10-18" is that day (at noon) in the zone; shapes that are not a real day are nil.
    check("dates in \(zone.identifier): day(\"2026-10-18\") is that day at noon", BCDFormat.day("2026-10-18").map { foundation("yyyy-MM-dd HH:mm", $0) } == "2026-10-18 12:00")
    check("dates in \(zone.identifier): day() takes leap days and nothing else",
          BCDFormat.day("2028-02-29") != nil && BCDFormat.day("2026-02-29") == nil && BCDFormat.day("2100-02-29") == nil
          && BCDFormat.day("2000-02-29") != nil && BCDFormat.day("2026-13-01") == nil && BCDFormat.day("2026-00-10") == nil
          && BCDFormat.day("2026-04-31") == nil && BCDFormat.day("") == nil && BCDFormat.day("2026-10-18T10:00") == nil
          && BCDFormat.day("26-10-2018") == nil && BCDFormat.day("2026/10/18") == nil && BCDFormat.day("2026-1-018") == nil
          && BCDFormat.day("0000-01-01") == nil)
    // "Last played": today, yesterday, a date.
    check("dates in \(zone.identifier): dayWord says Today, Yesterday and the date",
          BCDFormat.dayWord(nowDate, now: nowDate) == "Today" && BCDFormat.dayWord(nowDate.addingTimeInterval(-86_400), now: nowDate) == "Yesterday"
          && BCDFormat.dayWord(nowDate.addingTimeInterval(-3 * 86_400), now: nowDate) == BCDFormat.fullDate(nowDate.addingTimeInterval(-3 * 86_400)))
    check("dates: dates that are no moment (infinite, NaN, distant) do not trap",
          BCDFormat.fullDate(Date(timeIntervalSinceReferenceDate: .infinity)) == "1 Jan 1970"
          && BCDFormat.logClock(Date(timeIntervalSinceReferenceDate: .nan)) == "00:00:00.000"
          && !BCDFormat.dateTime(.distantPast).isEmpty && !BCDFormat.dateTime(.distantFuture).isEmpty)
    if verifyFresh {
        check("dates in \(zone.identifier): reused references equal fresh formatters for \(referenceComparisons) calls",
              referenceComparisons == 61_121 && referenceMismatches.isEmpty, referenceMismatches.prefix(5))
    }
}

@MainActor func settle() async { for _ in 0..<20 { try? await Task.sleep(nanoseconds: 50_000_000) } }

@main struct Main {
    @MainActor static func main() async {
        if CommandLine.arguments.contains("-datesOnly") {
            dateChecks()
            print(failures == 0 ? "dates: all passed" : "dates: \(failures) failed")
            exit(failures == 0 ? 0 : 1)
        }
        var system = BCDSystemStatus()
        system.jit = .on
        system.memoryLimitBytes = 6_000_000_000
        system.increasedMemoryLimit = true
        system.controllerName = "DualSense"
        system.freeDeviceBytes = 50_000_000_000
        let none = BCDGameDetails()
        func plan(_ game: BCDGame?, _ download: BCDDownload? = nil, _ sys: BCDSystemStatus? = nil, accounts: [BCDAccount] = [],
                  others: Bool = false, _ details: BCDGameDetails = BCDGameDetails()) -> BCDGamePagePlan {
            BCDGamePagePlan(game: game, download: download, system: sys ?? system, accounts: accounts, otherDownloads: others,
                            details: details)
        }
        let ready = BCDGame(id: "steam.620", title: "Portal 2", store: .steam, state: .ready, size: 12_800_000_000)

        // The main action for each state.
        var p = plan(ready)
        check("ready: Play, nothing in the way", p.main?.action == .play && p.others.isEmpty && p.notes.isEmpty && p.progress == nil, p)
        p = plan(BCDGame(id: "u", title: "U", store: .steam, state: .updateAvailable(bytes: 120_000_000)))
        check("an update: Play, and Update with its size beside it",
              p.main?.action == .play && p.others.first?.action == .update && p.others.first?.title.contains("MB") == true, p)
        p = plan(BCDGame(id: "u", title: "U", store: .steam, state: .updateAvailable(bytes: 0)))
        check("an update of unknown size: Update alone", p.others.first?.title == "Update", p)
        var repairable = none
        repairable.canRepair = true
        p = plan(BCDGame(id: "b", title: "B", store: .steam, state: .broken(reason: "Files are missing.")), nil, nil, repairable)
        check("broken, repairable: Repair, and why", p.main?.action == .repair && p.notes.first?.text == "Files are missing.", p)
        p = plan(BCDGame(id: "b", title: "B", store: .local, state: .broken(reason: "Gone.")))
        check("broken, nothing to repair: Play anyway", p.main?.action == .play, p)
        let big = BCDGame(id: "h", title: "H", store: .steam, state: .notInstalled, size: 98_000_000_000)
        p = plan(big)
        check("not installed: Install on this device, its size and the room left",
              p.main?.action == .install(.device)
              && p.installLine == "\(BCDFormat.bytes(98_000_000_000)) on this device, \(BCDFormat.bytes(50_000_000_000)) free"
              && p.notes.contains { $0.text.contains("It needs") && $0.warning }, p)
        var external = system
        external.freeExternalBytes = 400_000_000_000
        p = plan(big, nil, external)
        check("not installed, an external drive: the drive beside Install", p.others.contains { $0.action == .install(.externalDrive) }, p)
        p = plan(big, accounts: [BCDAccount(store: .steam, name: nil, available: true)])
        check("not installed, signed out: the line says so first and offers the sign-in",
              p.notes.first?.fix?.action == .signIn(.steam), p)
        let downloading = BCDGame(id: "d", title: "D", store: .steam, state: .downloading(progress: 0.34), size: 2_400_000_000)
        let running = BCDDownload(id: "d", title: "D", store: .steam, doneBytes: 816_000_000, totalBytes: 2_400_000_000,
                                  bytesPerSecond: 23_600_000)
        p = plan(downloading, running)
        check("downloading: Pause, Stop beside it, the progress with its numbers",
              p.main?.action == .pause && p.others.contains { $0.action == .cancelDownload }
              && p.progress?.title == "Downloading · 34%"
              && p.progress?.detail.contains("\(BCDFormat.bytes(816_000_000)) of \(BCDFormat.bytes(2_400_000_000))") == true, p)
        var paused = running
        paused.paused = true
        p = plan(downloading, paused)
        check("paused: Resume", p.main?.action == .resume && p.main?.title == "Resume" && p.progress?.title.hasPrefix("Paused") == true, p)
        var failed = running
        failed.failed = "The connection dropped."
        p = plan(downloading, failed)
        check("a failed download: Try again, and why", p.main?.title == "Try again" && p.progress?.failed == "The connection dropped.", p)
        p = plan(BCDGame(id: "q", title: "Q", store: .steam, state: .queued))
        check("queued: Pause, waiting", p.main?.action == .pause && p.progress?.title == "Waiting to download", p)
        p = plan(nil)
        check("a game no longer in the library: no action", p.main == nil && p.others.isEmpty && p.notes.isEmpty, p)

        // What stands in the way: the engine's reasons first, at most two lines, only for a game that can play.
        var off = system
        off.jit = .off
        p = plan(ready, nil, off)
        check("JIT off: the line offers to turn it on", p.notes.first?.fix?.action == .enableJIT, p)
        p = plan(big, nil, off)
        check("JIT off does not concern a game that is not installed", !p.notes.contains { $0.fix?.action == .enableJIT }, p)
        var worst = off
        worst.increasedMemoryLimit = false
        worst.controllerName = nil
        var blocked = none
        blocked.blockers = ["You're not signed in to Steam."]
        p = plan(ready, nil, worst, others: true, blocked)
        check("at most two lines, the engine's reason first", p.notes.count == 2 && p.notes[0].text == "You're not signed in to Steam.", p)
        var noMemory = system
        noMemory.increasedMemoryLimit = false
        p = plan(ready, nil, noMemory)
        check("no increased memory limit: the memory note, first", p.notes.first?.text.contains("increased memory limit") == true
              && p.notes.first?.text.contains("extended virtual addressing") == false, p)
        noMemory.extendedAddressing = false
        p = plan(ready, nil, noMemory)
        // Owner, 2026-10-22: only the increased memory limit is named; extended virtual addressing did not change the
        // address map on any iOS version in the logs, so a missing VA+ gets no note of its own and no mention.
        check("neither limit nor extended addressing: the memory note only, GetMoreRam named",
              p.notes.first?.text.contains("lacks the increased memory limit, so") == true && p.notes.first?.text.contains("GetMoreRam") == true
              && !p.notes.contains { $0.text.contains("virtual addressing") }, p)
        // Round 625 (owner's item 19): a 6 GB iPhone with the increased limit lets apps use 4095 MB; a game of 8 GiB or more gets a note before Play.
        var fourGB = system
        fourGB.memoryLimitBytes = 4095 << 20
        let sekiro = BCDGame(id: "steam.814380", title: "Sekiro", store: .steam, state: .ready, size: 15_000_000_000)
        p = plan(sekiro, nil, fourGB)
        check("a 4 GB-limit device and a large game: the note before Play (not a warning)",
              p.notes.count == 1 && p.notes[0].text.hasPrefix("iOS lets apps use about ") && p.notes[0].text.contains("BCD tries a swap file next")
              && !p.notes[0].warning, p)
        let small = BCDGame(id: "steam.105600", title: "Terraria", store: .steam, state: .ready, size: 500_000_000)
        p = plan(small, nil, fourGB)
        check("a small game on that device: no note", p.notes.isEmpty, p)
        p = plan(sekiro, nil, system)
        check("the large game on a device with more room (6 GB limit): no note", p.notes.isEmpty, p)
        var fourGBNoLimit = fourGB
        fourGBNoLimit.increasedMemoryLimit = false
        p = plan(sekiro, nil, fourGBNoLimit)
        check("without the increased limit: that install's own note instead, never both",
              p.notes.count == 1 && p.notes[0].text.contains("lacks the increased memory limit"), p)
        var noVA = system
        noVA.extendedAddressing = false
        p = plan(ready, nil, noVA)
        check("no extended addressing alone: no note about it", !p.notes.contains { $0.text.contains("virtual addressing") }, p)
        p = plan(big, nil, noVA)
        check("the missing entitlement does not concern a game that is not installed", p.notes.isEmpty || !p.notes.contains { $0.text.contains("virtual addressing") }, p)
        // Wine Mono: the last start of this game died for want of it (Panzer Paladin, 2026-10-20), and the device still has none.
        var needsMono = none
        needsMono.needsWineMono = true
        let monoText = "This game needs Wine Mono (.NET). Download it in Settings › .NET Framework."
        p = plan(ready, nil, nil, needsMono)
        check("Wine Mono missing after a start that needed it: the page says where to download it, nothing else",
              p.notes.count == 1 && p.notes[0].text == monoText && p.notes[0].warning && p.notes[0].fix == nil
              && p.main?.action == .play && p.others.isEmpty, p)
        check("without that, no note about Wine Mono", !plan(ready).notes.contains { $0.text.contains("Mono") }, plan(ready))
        p = plan(ready, nil, off, needsMono)
        check("JIT off comes first, then Wine Mono (two lines at most)",
              p.notes.count == 2 && p.notes[0].fix?.action == .enableJIT && p.notes[1].text == monoText, p)
        p = plan(ready, nil, noMemory, others: true, needsMono)
        check("Wine Mono before the memory note and the rest, two lines at most",
              p.notes.count == 2 && p.notes[0].text == monoText && p.notes[1].text.contains("increased memory limit"), p)
        p = plan(ready, nil, worst, others: true, blocked)
        check("the engine's reason still comes before everything", p.notes.first?.text == "You're not signed in to Steam.", p)
        var blockedMono = blocked
        blockedMono.needsWineMono = true
        p = plan(ready, nil, nil, others: true, blockedMono)
        check("a Steam blocker comes before Wine Mono", p.notes.count == 2 && p.notes[0].text == "You're not signed in to Steam." && p.notes[1].text == monoText, p)
        p = plan(big, nil, nil, needsMono)
        check("nothing about Wine Mono for a game that is not installed", !p.notes.contains { $0.text.contains("Mono") }, p)
        p = plan(downloading, running, nil, needsMono)
        check("nothing about Wine Mono while the game downloads", p.notes.isEmpty, p)
        p = plan(ready, nil, system, others: true)
        check("a running download will pause", p.notes.first?.text.contains("Downloads pause") == true, p)
        p = plan(downloading, running, worst, others: true, blocked)
        check("nothing about Play while the game downloads", p.notes.isEmpty, p)

        // Settings read "Default · …" until changed; a section says what changed.
        let fps = BCDGameSetting(id: "fps", title: "Frame-rate limit",
                                 kind: .choice([BCDGameChoice(value: "1", label: "60 FPS"), BCDGameChoice(value: "3", label: "30 FPS")]),
                                 value: "1", defaultValue: "1")
        var thirty = fps
        thirty.value = "3"
        let sync = BCDGameSetting(id: "fence-chain", title: "GPU sync", kind: .choice([BCDGameChoice(value: "", label: "Every pass (default)")]))
        let avx = BCDGameSetting(id: "avx", title: "AVX", kind: .toggle, value: "1", defaultValue: "0")
        check("a setting at its default says so once", fps.summary == "Default · 60 FPS" && sync.summary == "Every pass (default)")
        check("a changed setting shows its choice, and is marked", thirty.summary == "30 FPS" && thirty.changed && !fps.changed)
        check("a switch reads On or Off", avx.summary == "On" && avx.changed)
        let display = BCDGameSettingGroup(id: "display", title: "Display", symbol: "display", settings: [fps, avx])
        check("a section's line: its changed settings, else BCD's own", display.summary == "AVX on"
              && BCDGameSettingGroup(id: "x", title: "X", symbol: "x", settings: [fps]).summary == "As BCD sets it", display.summary)
        check("Steam Cloud in a word or two", BCDCloudSaves.differ(2).summary == "2 differ" && BCDCloudSaves.synced(matching: 0).summary == "No saves yet")

        // The facts.
        var facts = BCDGameFact.list(ready, none).map { "\($0.label): \($0.value)" }
        check("facts of an installed game", facts.contains("Last played: Not yet") && facts.contains("Installed on: This device")
              && facts.contains("Store: Steam") && facts.contains("Size: \(BCDFormat.bytes(12_800_000_000))"), facts)
        var detailed = none
        detailed.program = "32-bit · Direct3D 9"
        detailed.settings = "Recommended"
        facts = BCDGameFact.list(big, detailed).map { "\($0.label): \($0.value)" }
        check("facts of a game to download", facts.contains("Download: \(BCDFormat.bytes(98_000_000_000))") && !facts.contains { $0.hasPrefix("Installed on") }
              && facts.contains("Program: 32-bit · Direct3D 9") && facts.contains("Settings: Recommended"), facts)

        // A Steam game before its install: Steam's own figures (about this much), when the game's size is not known yet.
        let unsized = BCDGame(id: "steam.730", title: "Q", store: .steam, state: .notInstalled)
        var sized = none
        sized.downloadBytes = 20_000_000_000
        sized.installBytes = 30_000_000_000
        facts = BCDGameFact.list(unsized, sized).map { "\($0.label): \($0.value)" }
        check("facts of a Steam game before its install: the download and the size on this device, both 'About'",
              facts.contains("Download size: About \(BCDFormat.bytes(20_000_000_000))")
              && facts.contains("Size on this device: About \(BCDFormat.bytes(30_000_000_000))")
              && !facts.contains { $0.hasPrefix("Download:") }, facts)
        check("  and with neither figure, no size line at all", !BCDGameFact.list(unsized, none).contains { $0.label.contains("size") || $0.label == "Download" })
        p = plan(unsized, nil, nil, sized)
        check("the install line says 'About' for Steam's figure, and the room left",
              p.installLine == "About \(BCDFormat.bytes(30_000_000_000)) on this device, \(BCDFormat.bytes(50_000_000_000)) free", p)
        var roomy = sized
        roomy.installBytes = 80_000_000_000
        p = plan(unsized, nil, nil, roomy)
        check("  and warns when the device has less room: 'It needs about'",
              p.notes.contains { $0.warning && $0.text == "It needs about \(BCDFormat.bytes(80_000_000_000)); this device has \(BCDFormat.bytes(50_000_000_000)) free." }, p)
        p = plan(unsized, nil, nil, none)
        check("  and without a figure the line is as before", p.installLine == "On this device, \(BCDFormat.bytes(50_000_000_000)) free", p)
        p = plan(big, nil, nil, sized)
        check("  a size the store gave wins over Steam's estimate (no 'About')",
              p.installLine == "\(BCDFormat.bytes(98_000_000_000)) on this device, \(BCDFormat.bytes(50_000_000_000)) free", p)

        // The questions.
        let log = BCDGameLog(file: URL(fileURLWithPath: "/tmp/war3.exe-2026-10-18_21-04-11.txt"), name: "war3.exe-2026-10-18_21-04-11.txt",
                             date: Date(), bytes: 1000)
        let reset = BCDGameQuestion(title: "Take out the installs?", message: "…", confirm: "At the next start", destructive: true)
        let request = BCDGameSetting(id: "installrequest", title: "At the next start", kind: .choice([
            BCDGameChoice(value: "", label: "Nothing more"), BCDGameChoice(value: "reset", label: "Take them out", question: reset)]))
        let art = BCDGameSetting(id: "storeart", title: "Use Steam's art again", kind: .action,
                                 question: BCDGameQuestion(title: "Use it?", message: "", confirm: "Use", destructive: false))
        var removable = none
        removable.removal = .uninstall(frees: 9_100_000_000)
        removable.tuning = BCDGameTuning(on: true, status: nil, canUndo: true, found: ["dxmt = x"])
        removable.groups = [BCDGameSettingGroup(id: "steam", title: "Steam", symbol: "s", settings: [request, art, fps,
                            BCDGameSetting(id: "fastsync", title: "Fast sync", kind: .toggle, value: "1", defaultValue: "1", locked: "Fastsync only")])]
        let asks: [BCDGameAction] = [.uninstall, .removeFromLibrary, .cancelDownload, .resetToRecommended, .applyRecommended(changes: 3),
                                     .keepCloud(fromCloud: true), .keepCloud(fromCloud: false), .undoTuning, .writeTuningLines,
                                     .restoreSaves(URL(fileURLWithPath: "/tmp/s.zip")), .sendLog(log), .set("installrequest", "reset"),
                                     .set("storeart", "run")]
        let gentle: [BCDGameAction] = [.sendLog(log), .writeTuningLines, .set("storeart", "run")]
        for action in asks {
            let q = action.question(for: ready, details: removable)
            check("asks before \(action)", q != nil && (q?.destructive == true) == !gentle.contains(action), q as Any)
        }
        check("Uninstall says what it frees",
              BCDGameAction.uninstall.question(for: ready, details: removable)?.message.contains(BCDFormat.bytes(9_100_000_000)) == true)
        var update = none
        update.updating = true
        check("Stop says what it deletes: a first download's files, never an installed game's",
              BCDGameAction.cancelDownload.question(for: downloading, details: none)?.message.contains("deleted") == true
              && BCDGameAction.cancelDownload.question(for: downloading, details: update)?.message.contains("stays as it is") == true)
        let plain: [BCDGameAction] = [.play, .install(.device), .update, .pause, .resume, .repair, .enableJIT, .syncCloud, .setTuning(true),
                                      .signIn(.steam), .set("installrequest", ""), .set("fps", "3")]
        check("asks nothing before the rest", plain.allSatisfy { $0.question(for: ready, details: removable) == nil })

        // The model: what reaches BCDActions and the backend, and in which order.
        let recorder = Recorder()
        let store = BCDStore(actions: recorder)
        store.games = [ready, downloading]
        store.downloads = [running]
        store.system = system
        let backend = Backend()
        backend.shown = removable
        let model = BCDGamePageModel(gameID: "steam.620", backend: backend)
        model.close = { recorder.calls.append("close") }
        model.open(store)
        check("opening tells the backend and reads the details, without the settings",
              backend.calls == ["opened steam.620"] && model.details.removal == removable.removal && model.details.groups.isEmpty
              && backend.askedSettings.allSatisfy { !$0 }, backend.calls)
        model.showSettings(true)
        check("Options open: the settings are read", model.details.groups.count == 1 && backend.askedSettings.last == true)
        model.request(.uninstall)
        check("Uninstall waits for the answer", recorder.calls.isEmpty && model.pending?.action == .uninstall, recorder.calls)
        model.answer(false)
        check("refused, nothing happens", recorder.calls.isEmpty && model.pending == nil, recorder.calls)
        model.request(.uninstall)
        model.answer(true)
        check("confirmed, the page closes, then Uninstall reaches BCDActions", recorder.calls == ["close", "uninstall steam.620"], recorder.calls)
        recorder.calls = []
        model.request(.play)
        model.request(.install(.externalDrive))
        model.request(.repair)
        model.request(.enableJIT)
        model.request(.signIn(.steam))
        check("Play, Install, Repair, JIT and sign-in close the page first (BCD's live store shows them on the main screen)",
              recorder.calls == ["close", "play steam.620", "close", "install steam.620 externalDrive", "close", "verify steam.620",
                                 "close", "jit", "close", "signin steam"], recorder.calls)
        recorder.calls = []
        model.request(.update)
        model.request(.pause)
        model.request(.resume)
        model.request(.cancelDownload)
        model.answer(true)
        check("Update, Pause, Resume and Stop keep the page, which shows the download",
              recorder.calls == ["update steam.620", "pause steam.620", "resume steam.620", "cancel steam.620"], recorder.calls)
        recorder.calls = []
        model.request(.removeFromLibrary)
        model.answer(true)
        check("Remove from library asks, closes the page, and goes through the store's uninstall (its files stay)",
              recorder.calls == ["close", "uninstall steam.620"], recorder.calls)
        backend.calls = []
        model.request(.syncCloud)
        model.request(.keepCloud(fromCloud: true))
        model.answer(true)
        model.request(.resetToRecommended)
        model.answer(true)
        model.request(.setTuning(true))
        model.request(.undoTuning)
        model.answer(true)
        model.request(.writeTuningLines)
        model.answer(true)
        model.request(.set("fps", "3"))
        model.request(.set("fps", "1"))
        model.request(.set("fastsync", "0"))
        model.request(.set("nothing", "1"))
        check("cloud, reset, auto-tune and settings go to the backend; an unchanged value, a locked or unknown setting do nothing",
              backend.calls == ["sync", "keep true", "reset", "tuning true", "untune", "write", "set fps=3"], backend.calls)
        backend.calls = []
        model.request(.set("installrequest", "reset"))
        check("a choice that removes something asks first", backend.calls.isEmpty && model.pending?.question == reset)
        model.answer(true)
        model.request(.set("storeart", "run"))
        model.answer(true)
        check("... and runs once confirmed; a section's action too", backend.calls == ["set installrequest=reset", "set storeart=run"], backend.calls)
        backend.calls = []
        model.refreshRecommended()
        await settle()
        check("Get the latest recommended settings: up to date", backend.calls == ["latest"] && model.messages["recommended"] == "Up to date",
              model.messages)
        backend.refresh = .offline(builtIn: true, nothingOnline: false)
        model.refreshRecommended()
        await settle()
        check("... offline: the built-in list", model.messages["recommended"] == "Offline: using the built-in list", model.messages)
        backend.refresh = .available(changes: 3)
        model.refreshRecommended()
        await settle()
        check("... newer settings: offered with what they change", model.pending?.action == .applyRecommended(changes: 3), model.pending as Any)
        model.answer(false)
        check("... left: nothing applied, and the line says so", !backend.calls.contains("apply")
              && model.messages["recommended"] == "Not applied: 3 changes waiting", model.messages)
        model.refreshRecommended()
        await settle()
        model.answer(true)
        check("... applied: Updated, with the count", backend.calls.last == "apply" && model.messages["recommended"] == "Updated: 3 changes",
              model.messages)
        model.request(.restoreSaves(URL(fileURLWithPath: "/tmp/s.zip")))
        model.answer(true)
        await settle()
        check("Restore saves asks, restores and says how many", backend.calls.last == "restore" && model.messages["restore"] == "Put back 3 files.",
              model.messages)
        model.report()
        await settle()
        check("Report a problem without a log says so", model.pending == nil && model.messages["report"]?.contains("no log") == true, model.messages)
        backend.log = log
        model.report()
        await settle()
        check("Report a problem asks before sending the game's log", model.pending?.action == .sendLog(log), model.pending as Any)
        model.answer(true)
        await settle()
        check("sent, the code to quote", backend.calls.last == "send \(log.name)" && model.messages["report"] == "Sent. Quote this code: K7Q2XM",
              model.messages)
        let file = await model.backUpSaves()
        check("Back up saves hands over the zip and says what is in it",
              file?.lastPathComponent == "x.zip" && model.messages["saves"] == "2 files, \(BCDFormat.bytes(2048)).", model.messages)
        // GOG: no Update and no file check yet (a later version of BCD), and its note about runtimes.
        var gogGame = BCDGame(id: "gog.1207658924", title: "The Witcher", store: .gog, state: .updateAvailable(bytes: 0), size: 1_000)
        var gogDetails = BCDGameDetails()
        gogDetails.storeNote = "May need: Visual C++ 2017, DirectX (June 2010)."
        gogDetails.canRepair = true
        let gogPlan = BCDGamePagePlan(game: gogGame, download: nil, system: system, otherDownloads: false, details: gogDetails)
        check("GOG: no Update, and the store's note under Play", gogPlan.main?.action == .play && gogPlan.others.isEmpty
              && gogPlan.notes.contains { $0.text.hasPrefix("May need") && !$0.warning }, gogPlan)
        gogGame.state = .broken(reason: "No program to start.")
        let gogBroken = BCDGamePagePlan(game: gogGame, download: nil, system: system, otherDownloads: false, details: gogDetails)
        check("GOG: no Repair either", gogBroken.main?.action == .play, gogBroken.main as Any)
        check("GOG: Options offer no Check and repair files", !gogDetails.offersRepair(gogGame)
              && gogDetails.offersRepair(BCDGame(id: "steam.1", title: "S", store: .steam, state: .ready, size: nil)))

        // Epic (milestone 1): a game BCD cannot install shows the reason and no Install; one that may not start says so beside
        // Install; with neither, the page is what it was.
        let epicGame = BCDGame(id: "epic.Fixture", title: "Fixture", store: .epic, state: .notInstalled, size: 4_000_000_000)
        var epicBlocked = BCDGameDetails()
        epicBlocked.installBlocker = "This game comes with Easy Anti-Cheat anti-cheat, which can't run in BCD."
        let blockedPlan = BCDGamePagePlan(game: epicGame, download: nil, system: system, otherDownloads: false, details: epicBlocked)
        check("Epic: a game BCD cannot install has no Install, no size line and its reason as a warning",
              blockedPlan.main == nil && blockedPlan.others.isEmpty && blockedPlan.installLine == nil && blockedPlan.progress == nil
              && blockedPlan.notes.first?.text == epicBlocked.installBlocker && blockedPlan.notes.first?.warning == true, blockedPlan)
        var warned = BCDGameDetails()
        warned.installWarning = "Epic says this game must be online to start."
        let warnedPlan = BCDGamePagePlan(game: epicGame, download: nil, system: system, otherDownloads: false, details: warned)
        check("Epic: a game that may not start keeps Install, and says so as a plain note",
              warnedPlan.main?.action == .install(.device) && warnedPlan.notes.contains { $0.text == warned.installWarning && !$0.warning }, warnedPlan)
        let plainPlan = BCDGamePagePlan(game: epicGame, download: nil, system: system, otherDownloads: false, details: BCDGameDetails())
        let steamLikePlan = BCDGamePagePlan(game: BCDGame(id: "gog.1", title: "G", store: .gog, state: .notInstalled, size: 4_000_000_000), download: nil,
                                            system: system, otherDownloads: false, details: BCDGameDetails())
        check("Epic: with no blocker and no warning the page is the other stores' (Install, the size and the room left)",
              plainPlan.main?.action == .install(.device) && plainPlan.notes.isEmpty && plainPlan.installLine == steamLikePlan.installLine
              && plainPlan.notes == steamLikePlan.notes, plainPlan)
        var blockedElsewhere = BCDGameDetails()
        blockedElsewhere.installBlocker = "x"
        check("a blocker concerns only a game that is not installed",
              BCDGamePagePlan(game: BCDGame(id: "epic.A", title: "A", store: .epic, state: .ready, size: 1), download: nil, system: system, otherDownloads: false,
                              details: blockedElsewhere).main?.action == .play)

        // The online list's other answers, word for word.
        check("refresh lines: updated, downloaded list offline, nothing online yet, a list refused",
              BCDRecommendedRefresh.updated(changes: 1).line == "Updated: 1 change"
                  && BCDRecommendedRefresh.offline(builtIn: false, nothingOnline: false).line == "Offline: using the downloaded list"
                  && BCDRecommendedRefresh.offline(builtIn: true, nothingOnline: true).line == "No list online yet: using the built-in list"
                  && BCDRecommendedRefresh.rejected.line?.contains("the one in use stays") == true
                  && BCDRecommendedRefresh.available(changes: 2).line == nil)
        backend.refresh = .updated(changes: 2)
        model.refreshRecommended()
        await settle()
        check("... a newer list the game took: Updated, without a question", model.pending == nil && model.messages["recommended"] == "Updated: 2 changes",
              model.messages)
        check("the list's source line", BCDGameRecommendation(title: nil, version: 3).source == "List 3, built into this version of BCD."
              && BCDGameRecommendation(title: nil, version: 4, downloaded: true).source == "List 4, downloaded.")

        // Game config: saved as typed, and the count of its settings.
        check("a config's settings: one per key, notes and blank lines left out",
              BCDGameText.configCount("a = 1\n# b = 2\n\n c=3\na = 4\nnot a setting\n") == 2 && BCDGameText.configCount("") == 0)
        backend.calls = []
        model.request(.saveConfig("fence-chain = 6\nmetalfx-upscale = 2\n"))
        check("Save writes the game's config and says how many settings it holds",
              backend.calls == ["config"] && backend.config == "fence-chain = 6\nmetalfx-upscale = 2\n" && model.messages["config"] == "Saved: 2 settings",
              (backend.calls, model.messages))
        model.request(.saveConfig("# nothing\n"))
        check("... an empty config: no settings", model.messages["config"] == "Saved: no settings", model.messages)
        check("BCDGameText.settings", BCDGameText.settings(0) == "None" && BCDGameText.settings(1) == "1 setting" && BCDGameText.settings(5) == "5 settings")

        // Find on Steam and the cover picture.
        backend.calls = []
        model.request(.useStoreMatch(BCDStoreMatch(id: 2800, name: "Warcraft III: Reign of Chaos")))
        check("a store match is used at once, and the row says what changed",
              backend.calls == ["match 2800"] && model.pending == nil && model.messages["findstore"]?.contains("Warcraft III: Reign of Chaos") == true,
              (backend.calls, model.messages))
        model.request(.setCover(URL(fileURLWithPath: "/tmp/cover.png")))
        await settle()
        check("a cover picture is set without a question, and the row says so",
              backend.calls.last == "cover cover.png" && model.messages["cover"] == "Your picture is the cover now.", model.messages)
        backend.coverFails = true
        model.request(.setCover(URL(fileURLWithPath: "/tmp/huge.png")))
        await settle()
        check("... a picture refused: the reason in BCD's words", model.messages["cover"] == "Choose a picture under 20 MB.", model.messages)
        check("store search, config, match and cover never close the page",
              !BCDGameAction.saveConfig("").closesPage && !BCDGameAction.useStoreMatch(BCDStoreMatch(id: 1, name: "x")).closesPage
                  && !BCDGameAction.setCover(URL(fileURLWithPath: "/x")).closesPage)

        // A row that opens a screen.
        let binds = BCDGameSetting(id: "binds", title: "Controller binds", kind: .opens(.section("binds")), value: "2 changed", defaultValue: "BCD's layout")
        check("a row that opens a screen says what it holds, and is marked when it is not the default",
              binds.summary == "2 changed" && binds.changed
                  && !BCDGameSetting(id: "c", title: "c", kind: .opens(.coverPicture)).changed)

        // Launch arguments' flags.
        check("flags: found whole and in any case", BCDLaunchFlags.has("-DX11 -windowed", "-dx11") && !BCDLaunchFlags.has("-dx110", "-dx11"))
        check("flags: one Direct3D flag at a time", BCDLaunchFlags.toggled("-dx11 -windowed", "-dx12") == "-windowed -dx12",
              BCDLaunchFlags.toggled("-dx11 -windowed", "-dx12"))
        check("flags: -windowed and -fullscreen rule each other out", BCDLaunchFlags.toggled("-windowed -nosplash", "-fullscreen") == "-nosplash -fullscreen")
        check("flags: a second press takes the flag out", BCDLaunchFlags.toggled("-nosplash -dx9", "-dx9") == "-nosplash")
        check("flags: words in quotes stay whole",
              BCDLaunchFlags.words("\"C:\\My Games\\x\" -dx9") == ["\"C:\\My Games\\x\"", "-dx9"]
                  && BCDLaunchFlags.toggled("+set \"name with space\"", "-nosplash") == "+set \"name with space\" -nosplash",
              BCDLaunchFlags.words("\"C:\\My Games\\x\" -dx9"))

        // Controller binds.
        check("binds: inputs by their names", BCDPadBinds.inputName("Menu") == "Start" && BCDPadBinds.inputName("View") == "Select"
              && BCDPadBinds.inputName("D↑") == "D-pad up" && BCDPadBinds.inputName("LT") == "Left trigger (LT)"
              && BCDPadBinds.inputName("A") == "A button"
              && BCDPadBinds.sections.flatMap(\.inputs).count == 18)
        check("binds: what each value does", BCDPadBinds.label("key.32", input: "A") == "Space" && BCDPadBinds.label("none", input: "RS") == "Mouse"
              && BCDPadBinds.label("none", input: "LS") == "Nothing" && BCDPadBinds.label("key.112", input: "A") == "F1"
              && BCDPadBinds.label("key.96", input: "A") == "Numpad 0" && BCDPadBinds.keyName(0x99) == "Key 99"
              && BCDPadBinds.label("pad.Jump", input: "A") == "Jump" && BCDPadBinds.label("keyboard", input: "A") == "Show the keyboard")
        let buttonChoices = BCDPadBinds.choices(for: "A", current: "", standard: "Space")
        check("binds: a button's choices: the default, the mouse, 93 keys, the keyboard and nothing",
              buttonChoices.count == 98 && buttonChoices.first?.label == "Default (Space)" && buttonChoices.first?.value == ""
                  && Set(buttonChoices.map(\.value)).count == 98 && buttonChoices.last?.value == "none",
              buttonChoices.count)
        let stick = BCDPadBinds.choices(for: "RS", current: "stick.arrows", standard: "Mouse")
        check("binds: a stick's choices", stick.map(\.value) == ["", "none", "stick.wasd", "stick.arrows"] && stick[1].label == "Mouse")
        check("binds: a value saved elsewhere stays listed", BCDPadBinds.choices(for: "B", current: "pad.Jump", standard: "Ctrl").last?.label == "Jump")
        check("binds: numpad keys carry short caps", buttonChoices.first { $0.value == "key.106" }?.cap == "*")
        let speeds = BCDPadBinds.speeds(current: 1)
        check("binds: up and down speed from 25% to 150% in fives", speeds.count == 26 && speeds.first?.label == "25%" && speeds.last?.label == "150%"
              && speeds.contains { $0.value == "1.00" } && BCDPadBinds.speeds(current: 0.33).count == 27)

        // Steam Cloud's differing saves.
        let day = Date(timeIntervalSince1970: 1_790_000_000)
        let newer = BCDCloudConflict(name: "save.sav", cloudDate: day, cloudBytes: 2048, deviceDate: day - 3600, deviceBytes: 1024)
        check("a differing save: each side's copy, the newer one marked",
              newer.lines.cloud.hasPrefix("Steam Cloud: ") && newer.lines.cloud.hasSuffix(" · newer") && !newer.lines.device.hasSuffix("newer")
                  && newer.lines.device.hasPrefix("This device: "), newer.lines)
        let missing = BCDCloudConflict(name: "p.dat", cloudDate: day, cloudBytes: 10, deviceDate: nil)
        check("... missing on this device", missing.lines.device == "This device: missing, though it was synced here before"
              && !missing.lines.cloud.hasSuffix("newer"), missing.lines)

        model.showSettings(false)
        check("Options closed: the settings are let go", model.details.groups.isEmpty)
        recorder.calls = []
        backend.onNotice?()
        check("the engine's notice closes the page", recorder.calls == ["close"])
        model.leave()
        check("leaving lets the backend go and keeps nothing", backend.onChange == nil && backend.onNotice == nil && model.details == BCDGameDetails())
        let gone = BCDGamePageModel(gameID: "nothing", backend: backend)
        gone.open(store)
        gone.request(.play)
        check("a game no longer in the library: nothing runs", gone.game == nil && !recorder.calls.contains("play nothing"))
        // Steam DLC (BCDGameDLC.swift): the question before a first install, and Options' DLC section.
        func spaced(_ text: String?) -> String? { text?.replacingOccurrences(of: "\u{00A0}", with: " ") }
        do {
            recorder.calls = []
            let dlcBackend = Backend()
            dlcBackend.shown.dlcOffer = BCDDLCOffer(items: [BCDDLCOfferItem(id: 11, name: "Alpha", bytes: 1_500_000_000),
                                                           BCDDLCOfferItem(id: 12, name: "Beta", bytes: 2_000_000_000),
                                                           BCDDLCOfferItem(id: 13, name: "Gamma", bytes: 1_000_000_000)])
            let dlcModel = BCDGamePageModel(gameID: "steam.620", backend: dlcBackend)
            dlcModel.close = { recorder.calls.append("close") }
            dlcModel.open(store)
            dlcBackend.calls = []
            dlcModel.request(.install(.device))
            let asked = dlcModel.pending
            check("a game with owned DLC asks before its first install: Choose DLC…, Game only, Cancel, and the sizes",
                  recorder.calls.isEmpty && asked?.question.alternative == "Game only" && asked?.question.confirm == "Choose DLC…"
                  && asked?.question.message.contains("3 DLC") == true && asked?.question.message.contains(BCDFormat.bytes(4_500_000_000)) == true
                  && asked?.question.message.contains("\u{201C}Alpha\u{201D}, \u{201C}Beta\u{201D} and \u{201C}Gamma\u{201D}") == true
                  && asked?.question.destructive == false && asked?.action == .chooseDLC(.device)
                  && asked?.alternative == .installChoosingDLC(.device, chosen: []), asked as Any)
            dlcModel.answer(true)
            check("Choose DLC… opens the chooser: every owned DLC, all on; nothing installs and nothing is stored yet",
                  dlcModel.pending == nil && dlcModel.chooser?.rows.map(\.id) == [11, 12, 13] && dlcModel.chooser?.selected == [11, 12, 13]
                  && dlcModel.chooser?.place == .device && dlcBackend.calls.isEmpty && recorder.calls.isEmpty, dlcModel.chooser as Any)
            check("the chooser says what the download comes to: the game and the DLC chosen",
                  dlcModel.chooser?.chosenBytes == 4_500_000_000 && dlcModel.chooser?.totalBytes == 12_800_000_000 + 4_500_000_000
                  && spaced(dlcModel.chooser?.summary) == "Download: the game \(BCDFormat.bytes(12_800_000_000)) + 3 of 3 DLC \(BCDFormat.bytes(4_500_000_000)) = \(BCDFormat.bytes(17_300_000_000))"
                  && dlcModel.chooser?.installTitle == "Install", dlcModel.chooser?.summary as Any)
            check("a size and its unit never split across two lines (no-break spaces)",
                  dlcModel.chooser?.summary.contains("12.8\u{00A0}GB") == true && dlcModel.chooser?.summary.contains("=\u{00A0}17.3\u{00A0}GB") == true
                  && dlcModel.chooser?.summary.contains("4.5\u{00A0}GB") == true, dlcModel.chooser?.summary as Any)
            dlcModel.chooserToggle(12)
            check("a switch turns one DLC off; the total follows",
                  dlcModel.chooser?.selected == [11, 13] && dlcModel.chooser?.chosenBytes == 2_500_000_000
                  && dlcModel.chooser?.summary.contains("2 of 3 DLC") == true && dlcModel.chooser?.allOn == false)
            dlcModel.chooserToggle(99)
            check("a DLC that is not in the list can not be switched", dlcModel.chooser?.selected == [11, 13])
            dlcModel.chooserSelectNone()
            check("Select none: no DLC; Install then says it is the game only",
                  dlcModel.chooser?.selected == [] && dlcModel.chooser?.noneOn == true && dlcModel.chooser?.installTitle == "Install the game only"
                  && spaced(dlcModel.chooser?.summary) == "Download: the game \(BCDFormat.bytes(12_800_000_000)), no DLC")
            dlcModel.chooserSelectAll()
            check("Select all: every DLC again", dlcModel.chooser?.selected == [11, 12, 13] && dlcModel.chooser?.allOn == true)
            dlcModel.chooserBack()
            check("Cancel in the chooser goes back to the question and stores nothing",
                  dlcModel.chooser == nil && dlcModel.pending?.action == .chooseDLC(.device) && dlcBackend.calls.isEmpty && recorder.calls.isEmpty)
            dlcModel.answer(true)
            check("the chooser opens again with every switch on (nothing was kept)", dlcModel.chooser?.selected == [11, 12, 13])
            dlcModel.chooserToggle(11)
            dlcModel.chooserInstall()
            check("Install stores exactly the DLC chosen, then the page closes and Install runs",
                  dlcBackend.calls == ["set dlc.choice=12,13"] && recorder.calls == ["close", "install steam.620 device"] && dlcModel.chooser == nil,
                  [dlcBackend.calls, recorder.calls])
            recorder.calls = []; dlcBackend.calls = []
            dlcModel.request(.install(.device))
            dlcModel.answer(true)
            dlcModel.chooserInstall()
            check("everything left on stores every DLC's App ID (the all-on case)", dlcBackend.calls == ["set dlc.choice=11,12,13"], dlcBackend.calls)
            recorder.calls = []; dlcBackend.calls = []
            dlcModel.request(.install(.device))
            dlcModel.answer(true)
            dlcModel.chooserSelectNone()
            dlcModel.chooserInstall()
            check("none chosen is the game only: \"none\" is stored and the game installs",
                  dlcBackend.calls == ["set dlc.choice=none"] && recorder.calls == ["close", "install steam.620 device"], [dlcBackend.calls, recorder.calls])
            check("the choice reads back in the backend's words", BCDGameDLC.chosenSet("12,13") == [12, 13] && BCDGameDLC.chosenSet("none") == []
                  && BCDGameDLC.chosenSet("all") == nil && BCDGameDLC.chosenSet("5, x, 7") == [5, 7]
                  && BCDGameDLC.chooseValue([13, 11]) == "11,13" && BCDGameDLC.chooseValue([]) == "none")
            recorder.calls = []; dlcBackend.calls = []
            dlcModel.request(.install(.externalDrive))
            dlcModel.answerAlternative()
            check("Game only: the game alone is remembered, then Install runs",
                  dlcBackend.calls == ["set dlc.choice=none"] && recorder.calls == ["close", "install steam.620 externalDrive"], [dlcBackend.calls, recorder.calls])
            recorder.calls = []; dlcBackend.calls = []
            dlcModel.request(.install(.device))
            dlcModel.answer(false)
            check("Cancel: nothing is chosen and nothing installs", dlcBackend.calls.isEmpty && recorder.calls.isEmpty && dlcModel.pending == nil)
            dlcBackend.shown.dlcOffer = nil
            dlcModel.reload()
            dlcModel.request(.install(.device))
            check("no offer (no DLC, asked before, DLC support off): Install runs at once, as before",
                  recorder.calls == ["close", "install steam.620 device"] && dlcModel.pending == nil && dlcBackend.calls.isEmpty, recorder.calls)
            check("the DLC install actions close the page like Install; opening the chooser does not", BCDGameAction.installChoosingDLC(.device, chosen: []).closesPage
                  && BCDGameAction.installChoosingDLC(.device, chosen: []).question(for: ready, details: BCDGameDetails()) == nil
                  && !BCDGameAction.chooseDLC(.device).closesPage)

            let rows = [BCDDLCRow(id: 11, name: "Alpha", bytes: 200_000_000, installed: true, wanted: true),
                        BCDDLCRow(id: 12, name: "Beta", bytes: 1_000_000_000, installed: false, wanted: false),
                        BCDDLCRow(id: 13, name: "Gamma", bytes: 0, installed: true, wanted: false)]
            let section = BCDGameDLC.group(BCDDLCState(rows: rows, withoutFiles: 2, notOwned: 4))
            check("no owned DLC with files: no DLC section", BCDGameDLC.group(BCDDLCState(rows: [], withoutFiles: 2, notOwned: 4)) == nil)
            check("the section: a switch per owned DLC at its installed state, its size and state in the note",
                  section?.settings.prefix(3).map(\.id) == ["dlc.11", "dlc.12", "dlc.13"]
                  && section?.settings[0].value == "1" && section?.settings[1].value == "0" && section?.settings[2].value == "0"
                  && section?.settings[2].defaultValue == "1" && section?.settings[2].changed == true && section?.settings[0].changed == false
                  && section?.settings[0].note == "\(BCDFormat.bytes(200_000_000)) · Installed"
                  && section?.settings[1].note == "\(BCDFormat.bytes(1_000_000_000)) · Not installed"
                  && section?.settings[2].note == "Will be removed", section as Any)
            let tail = section?.settings.dropFirst(3).map(\.id) ?? []
            check("then Install all (the DLC not installed), Apply, and the DLC not owned as a number", tail == ["dlc.all", "dlc.apply", "dlc.notowned", "dlc.nofiles"], tail)
            check("Apply says what it does and asks, destructively when files go",
                  section?.settings.first { $0.id == "dlc.apply" }?.title == "Remove 1 DLC"
                  && section?.settings.first { $0.id == "dlc.apply" }?.question?.destructive == true
                  && section?.settings.first { $0.id == "dlc.all" }?.title == "Install all your DLC · \(BCDFormat.bytes(1_000_000_000))"
                  && section?.settings.first { $0.id == "dlc.all" }?.question?.destructive == false)
            let both = BCDGameDLC.group(BCDDLCState(rows: [BCDDLCRow(id: 1, name: "A", bytes: 5_000, installed: false, wanted: true),
                                                           BCDDLCRow(id: 2, name: "B", bytes: 0, installed: true, wanted: false)]))
            check("a download and a removal at once: one Apply row names both", both?.settings.first { $0.id == "dlc.apply" }?.title
                  == "Download 1 DLC · \(BCDFormat.bytes(5_000)), Remove 1 DLC", both?.settings.map(\.title) as Any)
            check("the section's row says how many are installed and that changes wait",
                  section?.summary == "2 of 3 installed · changes waiting"
                  && BCDGameDLC.group(BCDDLCState(rows: [BCDDLCRow(id: 1, name: "A", bytes: 0, installed: false, wanted: false)]))?.summary == "None installed")
            check("nothing to apply, everything installed: only the switches", BCDGameDLC.group(BCDDLCState(rows: [BCDDLCRow(id: 1, name: "A", bytes: 0, installed: true, wanted: true)]))?.settings.map(\.id) == ["dlc.1"])
            let busy = BCDGameDLC.group(BCDDLCState(rows: rows, busy: true))
            check("while the game downloads the switches and buttons wait for it", busy?.settings.filter { $0.id.hasPrefix("dlc.") }.allSatisfy { $0.locked != nil } == true)
            check("the section's text offers no purchase", !(section?.settings.flatMap { [$0.title, $0.note ?? "", $0.value] } ?? []).joined(separator: " ").lowercased().contains("buy")
                  && !(section?.settings.flatMap { [$0.title, $0.note ?? "", $0.value] } ?? []).joined(separator: " ").lowercased().contains("purchase"))
            check("setting ids go to the engine and back", BCDGameDLC.dlcID("dlc.123") == 123 && BCDGameDLC.dlcID("dlc.all") == nil && BCDGameDLC.dlcID("display") == nil)
            // Through the model: a switch is a setting of the section; Apply asks first.
            let sectionBackend = Backend()
            sectionBackend.shown.groups = section.map { [$0] } ?? []
            let sectionModel = BCDGamePageModel(gameID: "steam.620", backend: sectionBackend)
            sectionModel.close = {}
            sectionModel.open(store)
            sectionModel.showSettings(true)
            sectionBackend.calls = []
            sectionModel.request(.set("dlc.12", "1"))
            sectionModel.request(.set("dlc.11", "1"))
            check("a switch moves the engine's choice at once; an unchanged one does nothing", sectionBackend.calls == ["set dlc.12=1"], sectionBackend.calls)
            sectionModel.request(.set("dlc.apply", "run"))
            check("Apply asks before it removes files", sectionModel.pending?.question.destructive == true && sectionBackend.calls == ["set dlc.12=1"])
            sectionModel.answer(true)
            check("... and runs once confirmed", sectionBackend.calls == ["set dlc.12=1", "set dlc.apply=run"], sectionBackend.calls)
        }

        // Install waits for the DLC lookup (BCDGameDLC.swift, "Checking your DLC…"): the question must not be missed by a tap that
        // comes before Steam has answered which DLC the player owns.
        do {
            func waiting(_ configure: (Backend) -> Void = { _ in }) -> (BCDGamePageModel, Backend) {
                let b = Backend()
                b.shown.dlcCheck = .checking
                configure(b)
                let m = BCDGamePageModel(gameID: "steam.620", backend: b)
                m.checkSeconds = 0.4
                m.close = { recorder.calls.append("close") }
                m.open(store)
                recorder.calls = []; b.calls = []
                return (m, b)
            }
            let owned = BCDDLCOffer(items: [BCDDLCOfferItem(id: 11, name: "Alpha", bytes: 1_000_000_000), BCDDLCOfferItem(id: 12, name: "Beta", bytes: 2_000_000_000)])
            var (m, b) = waiting()
            m.request(.install(.device))
            check("Install while Steam is still asked about the DLC waits: a card says Checking your DLC…, nothing installs",
                  recorder.calls.isEmpty && m.pending?.checking == true && m.pending?.question.title == "Checking your DLC…"
                  && m.pending?.question.confirm == "Game only" && m.pending?.question.alternative == nil && b.calls.isEmpty, m.pending as Any)
            let card = m.pending
            m.request(.install(.device))
            check("a second tap on Install changes nothing", m.pending == card && recorder.calls.isEmpty)
            b.shown.dlcCheck = .none
            b.shown.dlcOffer = owned
            m.reload()
            check("the lookup is done with owned DLC: the card turns into the DLC question, still waiting for the answer",
                  m.pending?.checking == false && m.pending?.question.title == "Install your DLC too?"
                  && m.pending?.alternative == .installChoosingDLC(.device, chosen: []) && m.pending?.action == .chooseDLC(.device) && recorder.calls.isEmpty, m.pending as Any)
            m.answer(true)
            m.chooserInstall()
            check("Choose DLC… then Install (all left on) installs with the choice remembered first",
                  b.calls == ["set dlc.choice=11,12"] && recorder.calls == ["close", "install steam.620 device"], [b.calls, recorder.calls])
            await settle()
            check("... and the wait's timer does not install it a second time", recorder.calls == ["close", "install steam.620 device"], recorder.calls)

            (m, b) = waiting()
            m.request(.install(.device))
            b.shown.dlcCheck = .none
            m.reload()
            check("the lookup is done, no owned DLC with files: the game installs alone at once, nothing logged as skipped",
                  recorder.calls == ["close", "install steam.620 device"] && m.pending == nil && b.calls.isEmpty, [b.calls, recorder.calls])

            (m, b) = waiting()
            m.request(.install(.device))
            b.shown.dlcCheck = .failed
            m.reload()
            check("the lookup failed: the game installs alone and the backend logs why",
                  recorder.calls == ["close", "install steam.620 device"] && b.calls == ["set dlc.skipped=lookup-failed"] && m.pending == nil,
                  [b.calls, recorder.calls])

            (m, b) = waiting()
            m.request(.install(.device))
            await settle()
            check("the wait ran out (the test shortens it): the game installs alone and the backend logs the timeout",
                  recorder.calls == ["close", "install steam.620 device"] && b.calls == ["set dlc.skipped=timeout"] && m.pending == nil,
                  [b.calls, recorder.calls])

            (m, b) = waiting()
            m.request(.install(.device))
            m.answer(false)
            await settle()
            check("Cancel on the card: nothing installs, and the timer does not install it later",
                  recorder.calls.isEmpty && b.calls.isEmpty && m.pending == nil, [b.calls, recorder.calls])

            (m, b) = waiting()
            m.request(.install(.device))
            m.answer(true)
            await settle()
            check("Game only on the card: installs at once without a remembered choice, and only once",
                  recorder.calls == ["close", "install steam.620 device"] && b.calls.isEmpty, [b.calls, recorder.calls])

            (m, b) = waiting { $0.shown.dlcCheck = .failed }
            m.request(.install(.device))
            check("a lookup that already failed: no wait, the game installs alone and the backend logs why",
                  recorder.calls == ["close", "install steam.620 device"] && b.calls == ["set dlc.skipped=lookup-failed"], [b.calls, recorder.calls])

            (m, b) = waiting()
            m.request(.install(.device))
            m.leave()
            await settle()
            check("leaving the page while it waits stops the timer", recorder.calls.isEmpty && b.calls.isEmpty, [b.calls, recorder.calls])
            check("the checking card draws no spinner or timer of its own (a plain card)",
                  BCDGameDLC.checkingQuestion(title: "Portal 2").message.contains("Portal 2") && BCDGameDLC.checkSeconds == 15)
        }
        dateChecks()
        print(failures == 0 ? "page rules: all passed" : "page rules: \(failures) failed")
        exit(failures == 0 ? 0 : 1)
    }
}
