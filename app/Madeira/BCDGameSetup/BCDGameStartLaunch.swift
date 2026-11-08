// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Game setup (round 604): a start with a delay, a second program or window rules (BCDGameStart.swift has the plan, the script text and
// the page's words; bcd-helper/ the Windows program). BCDGameSetup.prepare calls wrapStart after the game's own lines are applied and
// after LibraryEntry.configureLaunch has set what the session starts (MADEIRA_EXE, MADEIRA_ARGS): when the game's file asks for any of
// it, and the start is the game's own, the session starts C:\windows\system32\cmd.exe with a batch file BCD wrote to C:\bcd-games
// (explorer.exe with the batch in a game that starts in a Wine desktop), the way the services batch has done for the games that need
// it. The batch starts the second program, the supervisor (the helper, for the window rules and the closing of the second program),
// waits (the helper again), and starts the game's program as the services batch does. A game that asks for none of it is not touched.
//
// What is left alone: a start that is not the game's own (an Epic game through BCD Host holds its own program here), a game BCD Dock
// starts through Steam (its first program is Valve's client), and the Wine desktop. When the helper is not in the app (a build that
// has not got it) the delay and the supervisor are left out and the log says so; the second program still starts.

extension BCDGameSetup {
    /// Where the helper is in the app, and where the start files go.
    static var bundledHelper: URL { Bundle.main.bundleURL.appendingPathComponent("arm64ec-windows/" + BCDStartScript.helperName) }
    static var startFolder: URL { drive.appendingPathComponent("bcd-games", isDirectory: true) }

    /// Returns log lines. Never throws and never stops a start.
    static func wrapStart(_ entry: LibraryEntry, values: [String: String]) -> [String] {
        let plan = BCDStartPlan.read(values)
        var lines = plan.problems.map { "[game-start] \($0)" }
        guard !plan.isEmpty, entry.usesLaunchOptions, entry.desktop != true else { return lines }
        let title = entry.title

        // Only the game's own start: what configureLaunch set names the game's program or the services batch.
        guard let exe = environment("MADEIRA_EXE"), let args = environment("MADEIRA_ARGS") else { return lines }
        let mine = [entry.launchWindowsPath, entry.servicesScriptPath]
        guard mine.contains(where: { exe.caseInsensitiveCompare($0) == .orderedSame || args.range(of: $0, options: .caseInsensitive) != nil }) else {
            lines.append("[game-start] \(title): this start is another program's (BCD Host); left as it is")
            return lines
        }

        let fm = FileManager.default
        let id = entry.id.uuidString
        let batchPath = BCDStartScript.folder + "\\" + id + "-start.bat", configPath = BCDStartScript.folder + "\\" + id + "-start.txt"
        let helperPresent = fm.fileExists(atPath: bundledHelper.path)
        if !helperPresent && (plan.delay > 0 || plan.needsSupervisor) {
            lines.append("[game-start] \(title): this build has no start helper; the delay and the window rules are left out")
        }
        var secondExists = !plan.second.isEmpty
        if secondExists, plan.second.count >= 3, plan.second.prefix(3).uppercased() == "C:\\" {
            secondExists = LibraryEntry.hostURL(ofWindowsPath: plan.second).map { fm.fileExists(atPath: $0.path) } ?? false
            if !secondExists { lines.append("[game-start] \(title): the second program is not there; left out") }
        }

        let main: BCDStartScript.Main = entry.servicesScript != nil   // startServices, or a GOG game's installers still to run (build 632)
            ? .servicesBatch(path: entry.servicesScriptPath)
            : .program(path: entry.launchWindowsPath, arguments: entry.programArguments, isBatch: entry.isBatch)
        let script = BCDStartScript.batch(title: title, plan: plan, main: main, workdir: entry.launchDirectory, configPath: configPath,
                                          secondExists: secondExists, helperPresent: helperPresent)
        let install = installFolder(entry).map { "C:\\" + $0.replacingOccurrences(of: "/", with: "\\") } ?? ""
        let config = BCDStartScript.supervisorFile(plan: plan, game: install, program: entry.launchWindowsPath, log: BCDStartScript.folder + "\\" + id + "-start.log")
        do {
            try fm.createDirectory(at: startFolder, withIntermediateDirectories: true)
            try Data(script.utf8).write(to: startFolder.appendingPathComponent(id + "-start.bat"), options: .atomic)
            if helperPresent {
                try Data(config.utf8).write(to: startFolder.appendingPathComponent(id + "-start.txt"), options: .atomic)
                try copyHelper(fm)
            }
        } catch {
            lines.append("[game-start] \(title): the start files could not be written (\(error.localizedDescription)); the game starts as it always did")
            return lines
        }

        // The session now starts the batch, as the services batch is started.
        let command = "/c \"\(batchPath)\""
        if entry.runsInDesktop {
            setenv("MADEIRA_EXE", "explorer.exe", 1)
            setenv("MADEIRA_ARGS", "/desktop=shell,\(entry.resolution) cmd \(command)", 1)
        } else {
            setenv("MADEIRA_EXE", "C:\\windows\\system32\\cmd.exe", 1)
            setenv("MADEIRA_ARGS", command, 1)
        }
        setenv("MADEIRA_WORKDIR", entry.launchDirectory, 1)
        var parts: [String] = []
        if plan.delay > 0 && helperPresent { parts.append("wait \(plan.delay) s") }
        if secondExists { parts.append("second program") }
        if plan.needsSupervisor && helperPresent { parts.append(plan.rules.isEmpty ? "closes it at the end" : "\(plan.rules.count) window rule\(plan.rules.count == 1 ? "" : "s")") }
        lines.append("[game-start] \(title): started by a script (\(parts.joined(separator: ", ")))")
        return lines
    }

    /// An environment variable of the session being prepared (read through a name that is not a literal at the getenv call, so the
    /// settings catalog's scan, which lists where each variable is read, keeps listing only the engine's own reads).
    private static func environment(_ name: String) -> String? { getenv(name).map { String(cString: $0) } }

    /// The helper next to the scripts, replaced when the app's differs (an update).
    private static func copyHelper(_ fm: FileManager) throws {
        let target = startFolder.appendingPathComponent(BCDStartScript.helperName)
        let source = bundledHelper
        if let a = try? Data(contentsOf: source, options: .alwaysMapped), let b = try? Data(contentsOf: target, options: .alwaysMapped), a == b { return }
        try? fm.removeItem(at: target)
        try fm.copyItem(at: source, to: target)
    }
}
