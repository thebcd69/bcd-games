// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Round 604, "Help with this game": the debug run's rules, the game feedback message, the section's rows and the launch check, compiled on the
// host by tests/host/check-bcd-help.py with BCDHelpCore.swift, BCDGameHelp.swift, BCDReadinessCore.swift, BCDGraphicsScan.swift, BCDStore.swift
// and the game page's rules. `scan <folder> [vc] [mono] [openal] [physx] [xna] [dx]` prints the finding ids for a folder.

import Foundation

var failures = 0
var checks = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    checks += 1
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

func module(_ file: String, _ is64: Bool, _ imports: [String]) -> BCDReadinessModule {
    BCDReadinessModule(file: file, is64: is64, imports: Set(imports))
}

func debugRuns() {
    let longSteps = String(repeating: "word ", count: 400)
    check("steps lose control characters and line breaks, keep single spaces, and stop at 600 characters",
          BCDDebugRules.clean("  open\tthe\nshop \u{7}now ") == "open the shop now" && BCDDebugRules.clean(longSteps).unicodeScalars.count <= 600
          && BCDDebugRules.maxSteps == 600)
    var run = BCDDebugRun(entryID: "e1", title: "Hollow Knight", program: "hollow_knight.exe", problem: "crashes", steps: "open the map", armedAt: 1000)
    check("the comment names the problem and the steps, on one line",
          BCDDebugRules.comment(for: run) == "Guided debug run: It crashes or closes. What I did: open the map")
    run.steps = ""
    check("... and has no steps part without steps", BCDDebugRules.comment(for: run) == "Guided debug run: It crashes or closes.")
    run.problem = "nonsense"
    check("an unknown problem reads as Something else", BCDDebugRules.problem(run) == .other)
    run.problem = ""
    check("a run whose problem was not said has none, and its words leave it out",
          BCDDebugRules.problem(run) == nil && BCDDebugRules.comment(for: run) == "Guided debug run." && BCDDebugRules.problemWords(run) == "not said"
          && !BCDDebugRules.offerMessage(run, closed: false).contains("(") )
    run.steps = "open the map"
    check("... with steps: just the steps", BCDDebugRules.comment(for: run) == "Guided debug run. What I did: open the map")
    run.steps = ""
    run.problem = "slow"
    check("a run is its entry's session; a Steam game's run is also recognised by its App ID (its entry may not be saved yet)",
          run.matches(entryID: "e1", steamAppID: nil) && !run.matches(entryID: "e2", steamAppID: 5) && !run.matches(entryID: "e2", steamAppID: nil)
          && { var r = run; r.steamAppID = 5; return r.matches(entryID: "other", steamAppID: 5) && !r.matches(entryID: "other", steamAppID: 6) && !r.matches(entryID: "other", steamAppID: nil) }())

    check("an armed run is stale after ten minutes, not before", !BCDDebugRules.stale(run, now: 1000 + 599) && BCDDebugRules.stale(run, now: 1000 + 601))
    run.phase = .running
    check("a running run is kept for three days (the app may be closed during it)", !BCDDebugRules.stale(run, now: 1000 + 86400) && BCDDebugRules.stale(run, now: 1000 + 4 * 86400))
    run.phase = .ended; run.endedAt = 5000
    check("a finished run is offered for three days", !BCDDebugRules.stale(run, now: 5000 + 86400 * 2) && BCDDebugRules.stale(run, now: 5000 + 86400 * 4))
    run.phase = .armed
    check("an armed run is never offered", !BCDDebugRules.offerable(run, appJustStarted: true, now: 1100))
    run.phase = .running
    check("a run found 'running' is offered only when the app has just started (it was closed during the run)",
          BCDDebugRules.offerable(run, appJustStarted: true, now: 2000) && !BCDDebugRules.offerable(run, appJustStarted: false, now: 2000))
    run.phase = .ended; run.endedAt = 1500
    check("a finished run is offered", BCDDebugRules.offerable(run, appJustStarted: false, now: 2000))
    check("the question says what is sent and that nothing is sent without Send",
          BCDDebugRules.offerMessage(run, closed: false).contains("Nothing is sent unless you choose Send")
          && BCDDebugRules.offerMessage(run, closed: true).contains("closed during the debug run") && BCDDebugRules.offerTitle(run, closed: true) == "The debug run closed BCD")

    let suite = UserDefaults(suiteName: "bcd-help-check-\(UUID().uuidString)")!
    BCDDebugRules.save(run, suite)
    check("a run is kept and read back", BCDDebugRules.load(suite) == run)
    BCDDebugRules.save(nil, suite)
    check("... and removed", BCDDebugRules.load(suite) == nil)
}

func feedback() {
    var draft = BCDGameFeedbackDraft()
    check("nothing is composed before a result is chosen", BCDGameFeedbackCore.text(game: "X", draft: draft, numbers: nil) == nil)
    draft.result = .fine
    draft.issues = [.sound, .slow]
    draft.words = "stutters in the\ncity\u{7} but fine elsewhere"
    let numbers = ["play": "1180s", "avg": "58.7", "low1": "41.2", "api": "D3D11/64", "game": "Hollow Knight", "length": "1234s", "evil": "x"]
    let text = BCDGameFeedbackCore.text(game: "Hollow Knight", draft: draft, numbers: numbers)!
    print("note: " + text.replacingOccurrences(of: "\n", with: " ¶ "))
    let lines = text.split(separator: "\n").map(String.init)
    check("the first line is machine-readable: result, the problems in a fixed order, the game last",
          lines[0] == "[game-feedback] result=fine issues=slow,sound game=Hollow Knight", lines[0])
    check("the numbers are the summary's, only the keys the message may repeat (no game name, no unknown key)",
          lines[1] == "numbers: play=1180s avg=58.7 low1=41.2 api=D3D11/64", lines[1])
    check("the player's words follow, in one line, without control characters", lines[2] == "stutters in the city but fine elsewhere" && lines.count == 3)
    draft.includeNumbers = false
    check("without the numbers there is no numbers line", !BCDGameFeedbackCore.text(game: "G", draft: draft, numbers: numbers)!.contains("numbers:"))
    check("with no numbers to include there is no numbers line", !BCDGameFeedbackCore.text(game: "G", draft: BCDGameFeedbackDraft(result: .great), numbers: nil)!.contains("numbers:"))
    draft = BCDGameFeedbackDraft(result: .broken)
    let bare = BCDGameFeedbackCore.text(game: "Game\nWith = odd name", draft: draft, numbers: nil)!
    check("no problem ticked is 'none'; the game's name is one line and cannot start another", bare == "[game-feedback] result=broken issues=none game=Game With = odd name", bare)
    check("... and reads back", BCDGameFeedbackCore.parse(bare) == BCDGameFeedbackCore.Parsed(result: "broken", issues: [], game: "Game With = odd name"))
    check("the message reads back with its problems", BCDGameFeedbackCore.parse(text) == BCDGameFeedbackCore.Parsed(result: "fine", issues: ["slow", "sound"], game: "Hollow Knight"))
    check("something that is not a game feedback is not parsed", BCDGameFeedbackCore.parse("hello") == nil && BCDGameFeedbackCore.parse("[game-feedback] result=fine") == nil)
    draft.words = String(repeating: "é", count: 5000)
    let long = BCDGameFeedbackCore.text(game: String(repeating: "g", count: 300), draft: draft, numbers: numbers)!
    check("the message stays inside the intake's 4000 characters (3000 of words, 80 of the game's name)",
          long.unicodeScalars.count <= 4000 && BCDDebugRules.maxSteps == 600 && BCDGameFeedbackCore.maxWords == 3000, "\(long.unicodeScalars.count)")
    check("a value with spaces or an equals sign cannot split a field", BCDGameFeedbackCore.token("a b=c\n") == "abc")
    check("the question shows the whole message and what goes with it",
          BCDGameFeedbackCore.confirmation(message: text, attachLog: false).contains(text) && BCDGameFeedbackCore.confirmation(message: text, attachLog: false).contains("No name or account")
          && BCDGameFeedbackCore.confirmation(message: text, attachLog: true).contains("last log goes first") && BCDGameFeedbackCore.confirmation(message: text, attachLog: true).contains("performance trace"))
}

func section() {
    var s = BCDHelpState()
    let g0 = BCDGameHelp.group(s)
    let ids = g0.settings.map(\.id)
    check("the section is Help with this game, a row of Options' own list", g0.id == "help" && g0.title == "Help with this game" && g0.parent == nil)
    check("rows: launch check, the debug run (problem, steps, start), the feedback (result, six problems, words, numbers, log, send)",
          ids == ["help.ready", "help.problem", "help.steps", "help.start", "help.fb.result"]
              + BCDFeedbackIssue.allCases.map { "help.fb.issue." + $0.rawValue } + ["help.fb.words", "help.fb.numbers", "help.fb.log", "help.fb.send"], "\(ids)")
    let send0 = g0.settings.first { $0.id == "help.fb.send" }!
    check("Send feedback waits for a result", send0.locked == "Choose how it ran first." && send0.question == nil)
    check("Start a debug run asks first and says nothing is sent unless the player chooses Send",
          g0.settings.first { $0.id == "help.start" }!.question?.message.contains("nothing is sent unless you choose Send") == true)
    check("the log toggle is locked without a log", g0.settings.first { $0.id == "help.fb.log" }!.locked != nil)
    check("the first row of each block starts a heading", g0.settings[0].heading == "Before you play" && g0.settings[1].heading == "Debug run" && g0.settings[4].heading == "Tell BCD how it ran")
    check("the problem choice starts at 'Not said' and lists the seven problems after it",
          g0.settings[1].choices.count == 8 && g0.settings[1].choices.first?.label == "Not said" && g0.settings[1].value == "" && g0.settings[1].isDefault)
    check("the section has no warning-coloured note of its own (the rows say it)", g0.note == nil)
    check("the result choice starts with 'Not chosen'", g0.settings[4].choices.first?.label == "Not chosen" && g0.settings[4].choices.count == 5)
    check("the headline: debug run and feedback", g0.headline == "Debug run, feedback")
    check("the launch check row says it is looking until it is done", g0.settings[0].value == "Looking…")

    s.feedback.result = .hard
    s.feedback.issues = [.slow]
    s.hasLog = true
    s.feedback.attachLog = true
    s.message = BCDGameFeedbackCore.text(game: "Hollow Knight", draft: s.feedback, numbers: nil)
    s.readinessChecked = true
    let g1 = BCDGameHelp.group(s)
    let send1 = g1.settings.first { $0.id == "help.fb.send" }!
    check("with a result, Send feedback is open and its question holds the whole message",
          send1.locked == nil && send1.question?.message.contains("[game-feedback] result=hard issues=slow game=Hollow Knight") == true
          && send1.question?.message.contains("last log goes first") == true && send1.question?.confirm == "Send")
    check("the log toggle is open with a log", g1.settings.first { $0.id == "help.fb.log" }!.locked == nil)
    check("a ticked problem shows as on", g1.settings.first { $0.id == "help.fb.issue.slow" }!.isOn)
    check("a clean launch check says nothing is missing", g1.settings[0].value == "Nothing missing")
    s.readiness = "x.exe wants mfc140u.dll."
    check("a launch check with a finding says see below, and the headline warns", BCDGameHelp.group(s).settings[0].value == "See below" && BCDGameHelp.group(s).headline == "Something is missing"
          && BCDGameHelp.group(s).settings[0].note == "x.exe wants mfc140u.dll.")
    s.sending = true
    check("while sending, the button is locked", BCDGameHelp.group(s).settings.first { $0.id == "help.fb.send" }!.locked == "Sending…")
    s.sending = false
    s.status = "Sent. Your code: K7Q2MX"
    let g2 = BCDGameHelp.group(s)
    check("a status shows as the last row", g2.settings.last?.id == "help.fb.status" && g2.settings.last?.note == "Sent. Your code: K7Q2MX")
    check("issue ids read back", BCDGameHelp.issue(ofID: "help.fb.issue.sound") == .sound && BCDGameHelp.issue(ofID: "help.fb.issue.nope") == nil && BCDGameHelp.issue(ofID: "help.start") == nil)
}

func readiness() {
    let none = BCDReadinessProvided()
    func ids(_ m: [BCDReadinessModule], files: [String] = [], _ p: BCDReadinessProvided = none) -> [String] {
        BCDReadiness.evaluate(modules: m, files: Set(files), provided: p).map(\.id)
    }
    check("a game that imports only what Windows and BCD have has nothing missing",
          ids([module("g.exe", true, ["kernel32.dll", "d3d11.dll", "vcruntime140.dll", "msvcp140.dll", "xinput1_3.dll"])]).isEmpty)
    check("a 64-bit MFC game without Microsoft's Visual C++ runtime is told to download it",
          ids([module("a.dll", true, ["mfc140u.dll"])]) == ["mfc64"]
          && BCDReadiness.evaluate(modules: [module("a.dll", true, ["mfc140u.dll"])], files: [], provided: none)[0].text.contains("Settings"))
    var vc = none; vc.vcRuntimeX64 = true
    check("... and with it, nothing is missing", ids([module("a.dll", true, ["mfc140u.dll", "mfc140.dll"])], vc).isEmpty)
    check("... except an MFC file Microsoft's x64 copy does not hold (the debug one), which BCD cannot supply",
          BCDReadiness.evaluate(modules: [module("a.dll", true, ["mfc140ud.dll"])], files: [], provided: vc).map { ($0.id, $0.fixable) }.first! == ("mfc64", false))
    // Round 625 (the owner's item 26): the 32-bit MFC is downloadable (Settings › Visual C++ › MFC, 32-bit); a game takes it with env.MADEIRA_MFC32 = 1.
    let mfc32 = BCDReadiness.evaluate(modules: [module("muv-luv_pf.exe", false, ["mfc140u.dll"])], files: [], provided: vc)
    check("a 32-bit MFC game without the 32-bit MFC is told to download it (the 64-bit runtime does not count)",
          mfc32.map { ($0.id, $0.fixable) }.first! == ("mfc32", true) && mfc32[0].text.contains("Download the 32-bit MFC in Settings › Visual C++ first"))
    var vc32 = vc; vc32.vcRuntimeX86 = true
    check("... and with it, nothing is missing (the four DLLs of Microsoft's 32-bit package)",
          ids([module("muv-luv_pf.exe", false, ["mfc140u.dll", "mfc140.dll", "mfcm140.dll", "mfcm140u.dll"])], vc32).isEmpty)
    check("... except an MFC file the package does not hold (the debug one), which BCD cannot supply",
          BCDReadiness.evaluate(modules: [module("a.exe", false, ["mfc140ud.dll"])], files: [], provided: vc32).map { ($0.id, $0.fixable) }.first! == ("mfc32", false)
          && BCDReadiness.evaluate(modules: [module("a.exe", false, ["mfc140ud.dll"])], files: [], provided: none).map { ($0.id, $0.fixable) }.first! == ("mfc32", false))
    // Round 624 (agent N S12/S12b/S13): a Unity game on an install without the limit or on a small map, a 32-bit game without the limit and extended VA
    var noRights = BCDReadinessProvided(); noRights.increasedMemory = false; noRights.extendedVA = false
    var smallMap = BCDReadinessProvided(); smallMap.mapGB = 17
    var bigMap = BCDReadinessProvided(); bigMap.mapGB = 512
    let unityFiles: Set<String> = ["unityplayer.dll", "game.exe"]
    let noRightsUnity = BCDReadiness.evaluate(modules: [], files: unityFiles, provided: noRights)
    check("a Unity game on an install without the limit gets a note with no fix; on a 17 GB map another; on 512 GB or by default none",
          noRightsUnity.map(\.id) == ["unity-no-rights"] && noRightsUnity.first?.fixable == false
          && BCDReadiness.evaluate(modules: [], files: unityFiles, provided: smallMap).map(\.id) == ["unity-small-map"]
          && BCDReadiness.evaluate(modules: [], files: unityFiles, provided: bigMap).isEmpty
          && BCDReadiness.evaluate(modules: [], files: unityFiles, provided: BCDReadinessProvided()).isEmpty
          && BCDReadiness.evaluate(modules: [], files: ["game.exe"], provided: noRights).isEmpty)
    let wow32 = BCDReadiness.evaluate(modules: [module("Game.exe", false, [])], files: [], provided: noRights, mainFile: "game.exe")
    check("a 32-bit launched program on an install without the limit and extended VA gets the window note; a 64-bit one, a helper, or rights do not",
          wow32.map(\.id) == ["wow-window"] && wow32.first?.fixable == false
          && BCDReadiness.evaluate(modules: [module("Game.exe", true, [])], files: [], provided: noRights, mainFile: "game.exe").isEmpty
          && BCDReadiness.evaluate(modules: [module("Helper.exe", false, []), module("Game.exe", true, [])], files: [], provided: noRights,
                                   mainFile: "game.exe").isEmpty
          && BCDReadiness.evaluate(modules: [module("Game.exe", false, [])], files: [], provided: BCDReadinessProvided(), mainFile: "game.exe").isEmpty
          && BCDReadiness.evaluate(modules: [module("Game.exe", false, [])], files: [], provided: noRights).isEmpty)
    check("the log line names the Unity note as one with no fix",
          BCDReadiness.logLine(game: "U", modules: 0, findings: noRightsUnity).hasSuffix("unity-no-rights(no fix)"))
    check("a game that carries its own MFC file is fine", ids([module("a.exe", false, ["mfc140u.dll"])], files: ["mfc140u.dll"]).isEmpty)
    // Round 624 (agent E1 S1, Aliens: Colonial Marines VMJ4WV): Valve's steam_api missing from the game's own copy (c0000135 before any window).
    let noSteamAPI = BCDReadiness.evaluate(modules: [module("ACM.exe", false, ["kernel32.dll", "steam_api.dll"])], files: [], provided: none)
    check("a program that imports steam_api.dll from a folder without it: the copy is incomplete, no fix BCD offers",
          noSteamAPI.map { ($0.id, $0.fixable) }.first! == ("steamapi", false) && noSteamAPI[0].text.contains("ACM.exe needs steam_api.dll")
          && noSteamAPI[0].text.contains("install the game again"))
    check("... steam_api64.dll the same; the file anywhere in the folder tree: nothing",
          ids([module("g.exe", true, ["steam_api64.dll"])]) == ["steamapi"]
          && ids([module("g.exe", true, ["steam_api64.dll"])], files: ["steam_api64.dll"]).isEmpty
          && ids([module("g.exe", false, ["steam_api.dll"])], files: ["steam_api.dll"]).isEmpty)
    check("... and a scan that stopped at its limit calls nothing missing (the file may not have been reached)",
          BCDReadiness.evaluate(modules: [module("g.exe", true, ["steam_api64.dll"])], files: [], provided: none, complete: false).isEmpty)
    check(".NET without Wine Mono says to download it; with it, nothing", ids([module("g.exe", false, ["mscoree.dll"])]) == ["mono"]
          && ids([module("g.exe", false, ["mscoree.dll"])], { var p = none; p.wineMono = true; return p }()).isEmpty)
    check("OpenAL, PhysX and the 2010 DirectX files each point at their Start menu runtime",
          ids([module("g.exe", false, ["openal32.dll"])]) == ["openal"] && ids([module("g.exe", false, ["physxloader.dll"])]) == ["physx"]
          && ids([module("g.exe", false, ["xactengine2_1.dll"])]) == ["dx2010"] && ids([module("g.exe", true, ["xactengine3_7.dll"])]) == ["dx2010"])
    check("... a DLL the game carries itself is not missing", ids([module("g.exe", false, ["openal32.dll", "physxloader.dll"])], files: ["openal32.dll", "physxloader.dll"]).isEmpty)
    check("... installed, they are not missing", { var p = none; p.openAL = true; p.physX = true; p.directX2010 = true; return ids([module("g.exe", false, ["openal32.dll", "physxloader.dll", "xactengine2_1.dll"])], p).isEmpty }())
    check("a 32-bit DirectX file BCD already has (xactengine2_0, d3dx9_43) is not flagged", ids([module("g.exe", false, ["xactengine2_0.dll", "d3dx9_43.dll", "xinput1_3.dll"])]).isEmpty)
    check("XNA: the game's folder names the framework and the runtime is missing", ids([], files: ["microsoft.xna.framework.dll"]) == ["xna"]
          && ids([], files: ["microsoft.xna.framework.dll"], { var p = none; p.xna = true; return p }()).isEmpty)
    let findings = BCDReadiness.evaluate(modules: [module("a.exe", false, ["mfc140ud.dll", "openal32.dll", "physxloader.dll"])], files: [], provided: none)
    check("what can be fixed comes first; the note shows two and counts the rest", findings.first?.fixable == true && findings.last?.fixable == false
          && BCDReadiness.note(findings)!.hasSuffix("(1 more in the log.)") && BCDReadiness.note([]) == nil, BCDReadiness.note(findings) ?? "nil")
    check("a finding is reported once even when ten programs import the same file",
          ids((0..<10).map { module("m\($0).dll", true, ["mfc140u.dll"]) }) == ["mfc64"])
    check("the log line names the findings, and 'no fix' where there is none",
          BCDReadiness.logLine(game: "G", modules: 3, findings: findings) == "[readiness] G: 3 program files read; openal, physx, mfc32(no fix)"
          && BCDReadiness.logLine(game: "G", modules: 1, findings: []) == "[readiness] G: 1 program file read; nothing missing", BCDReadiness.logLine(game: "G", modules: 3, findings: findings))
}

@main enum Run {
    static func main() {
        let a = CommandLine.arguments
        if a.count > 2, a[1] == "scan" {
            let root = URL(fileURLWithPath: a[2])
            var p = BCDReadinessProvided()
            for flag in a.dropFirst(3) {
                switch flag {
                case "vc": p.vcRuntimeX64 = true
                case "mono": p.wineMono = true
                case "openal": p.openAL = true
                case "physx": p.physX = true
                case "xna": p.xna = true
                case "dx": p.directX2010 = true
                default: break
                }
            }
            let r = BCDReadiness.scan(root)
            let findings = BCDReadiness.evaluate(modules: r.modules, files: r.files, provided: p)
            print("modules=\(r.modules.count) files=\(r.files.count) complete=\(r.complete)")
            print("findings=" + findings.map(\.id).joined(separator: ","))
            exit(0)
        }
        debugRuns()
        feedback()
        section()
        readiness()
        print(failures == 0 ? "bcd_help_checks: PASS (\(checks) checks)" : "bcd_help_checks: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
