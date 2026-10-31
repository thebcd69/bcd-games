// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// BCD Dock: the game died and the session stayed open (round 579). A Dock session's initial program is Wine's desktop
// (explorer), and on iOS that desktop never closes by itself (the server's own close timer is switched off), so when
// the game's process ended with an error (Dying Light 32FEAT and The Binding of Isaac VV5NHZ: c0000005; the host
// reported the game over and finished) the session went on for good: the dead game's last frame or an empty desktop
// stayed on the screen, and the user took it for a hang. The library's watch (LibraryModel.watchDockGameEnd) reads the
// engine's process table (madeira_child_report) and asks the rules below. They are Foundation only and pure
// (tests/host/check-dock-game-end.py): what the engine and the host's report say goes in, a verdict comes out.
//
// The game is not found by name. A program of the session is the game's (or its own launcher's or helper's) when it
// lives anywhere but under C:\windows (Wine's own programs and the Dock host), Valve's client folder outside its
// steamapps library (SteamLaunchScene.owner, the classification the starting screen already uses) and Valve's shared
// folder beside it (the service program), and is not a crash reporter or helper by the table's file-name rule.
// The session is over only when ALL of these hold:
//   - the host reported the game running and over, and finished (Valve's client counted the game as ended, 3 s and
//     any keep-alive wait included, so a launcher that starts the game again is still running or counted);
//   - no program of the game runs, and the engine could track every one (a spawn past the table never ends a session);
//   - the program of the game that ended LAST ended with an error status (an exception code such as c0000005 /
//     c0000409 / c000001d / c0000374, a breakpoint, or a negative exit code), and that is `grace` seconds ago
//     (a launcher's relaunch came within 0.4 s in the logs of 331 Dock sessions; the next start after any crash of
//     a program was 27 s later or never: Rockstar's service retries).
// A game that ended with exit code 0, or any other status, is left as it is today, with one exception (round 625, N-S18):
// a program that closed by itself with exit code 0 within `shortExitSeconds` of its start, before any frame reached the
// screen (Brotato, Until Then, Darksiders II, Shadow of Mordor ... showed their own error box and left: QQ0BEX and about
// 40 Dock logs), ends the session under all the other conditions above. `rendered` says whether frames were drawn;
// env.MADEIRA_DOCK_SHORT_EXIT_END = 0 turns the exception off (the library then passes rendered = true).
// A launch the host reported as failed, with no game process, is the second rule below (round 580).

/// One process of the session, as the engine's process table reports it.
struct DockProcess: Equatable {
    /// The image path as the process was spawned (a Windows path); "" when it could not be read.
    var path: String
    /// A crash reporter or helper by the engine's file-name rule (process_ios.c): never the game.
    var helper: Bool
    var running: Bool
    /// The exit status, for a process that ended.
    var status: UInt32 = 0
    /// Seconds since it ended / how long it lived, for a process that ended.
    var endedAgo: Double = 0
    var lived: Double = 0

    /// The file name, lower case: what the user's log line says (never the folder).
    var fileName: String {
        let name = path.split(whereSeparator: { $0 == "\\" || $0 == "/" }).last.map(String.init) ?? path
        return name.isEmpty ? "?" : name.lowercased()
    }
}

/// The engine's snapshot (WineProcessBridge.h, madeira_child_report).
struct DockProcessTable: Equatable {
    var processes: [DockProcess] = []
    /// Spawns the table could not track: unknown programs, which never allow an end.
    var untracked = 0
    /// False when the text was cut or had a line this reader does not know: nothing may be concluded from it.
    var complete = true
}

/// What the host's report says (MadeiraDock.Report), as three questions.
struct DockHostState: Equatable {
    /// `launch-game-running=1`: Valve's client saw the game run.
    var gameRan: Bool
    /// `launch-game-ended=1`: Valve's client counted the game as ended (after its own 3 s and any keep-alive wait).
    var gameOver: Bool
    /// `probe-result` is in the report: the host finished (it logged off Steam and exited).
    var finished: Bool
}

enum DockGameEndRules {
    /// Seconds the game's last program must have been gone.
    static let defaultGrace = 10.0
    /// Round 625 (N-S18): a program that left with exit code 0 within this many seconds of its start, before any frame was drawn.
    static let shortExitSeconds = 30.0
    static let graceRange = 3.0...300.0

    /// `env.MADEIRA_DOCK_GAME_END_GRACE` as text: a number of seconds inside the range, else the default.
    static func grace(_ text: String?) -> Double {
        guard let text, let value = Double(text.trimmingCharacters(in: .whitespaces)), value.isFinite else { return defaultGrace }
        return min(max(value, graceRange.lowerBound), graceRange.upperBound)
    }

    /// A status that says the program crashed or failed hard: an NTSTATUS error or an exception code (0xC.......,
    /// 0xE.......: an access violation, a stack overrun, a C++ exception), the breakpoint exit (0x80000003), or a
    /// negative exit code (-1). Exit code 0 and plain small codes are not: a game may end with 1 after a quit.
    static func isCrash(_ status: UInt32) -> Bool {
        status & 0xC000_0000 == 0xC000_0000 || status == 0x8000_0003
    }

    enum Verdict: Equatable {
        /// No program of the game ended with an error status last: nothing to say.
        case none
        /// One did, but the session is not over yet: `why` is a short fixed word for the log.
        case wait(status: UInt32, file: String, why: String)
        /// The game is over and ended with this status.
        case end(status: UInt32, file: String, lived: Double)
    }

    /// Valve's shared folder beside the client's own ("C:\Program Files (x86)\Common Files\Steam\", where its service
    /// program lives, from the CEG preparation): Valve's, never the game's. Derived from the client folder.
    static func valveShared(_ places: SteamLaunchScene.Places) -> String? {
        let parts = places.client.split(separator: "\\", omittingEmptySubsequences: true)
        guard parts.count >= 2 else { return nil }
        return parts.dropLast().joined(separator: "\\") + "\\common files\\" + parts[parts.count - 1] + "\\"
    }

    /// `places`: where programs live on drive C (SteamLaunchScene.Places.standard). `host` is asked only when
    /// everything the table can say is already in favour of ending, so the report file is read for the rare case.
    /// `anyStatus` (round 624, Cuphead PDKQP7 "Close game bug"): the player has asked to close, so a game that ended with ANY
    /// status is over (Cuphead quit itself with 0 and the session sat on the empty desktop: 212 of 433 archive Dock sessions whose
    /// host reported the game over); every other condition below stays.
    /// `rendered` (round 625): frames of the game reached the screen in this session; false lets a short clean exit end it too.
    static func verdict(_ table: DockProcessTable, places: SteamLaunchScene.Places, grace: Double,
                        anyStatus: Bool = false, rendered: Bool = true, host: () -> DockHostState) -> Verdict {
        var running = 0
        var last: DockProcess?
        let shared = valveShared(places)
        for p in table.processes where !p.helper {
            switch SteamLaunchScene.owner(p.path, places: places) {
            case .other:
                if let shared, SteamLaunchScene.path(p.path).hasPrefix(shared) { break }
                if p.running { running += 1 }
                else if last.map({ p.endedAgo < $0.endedAgo }) ?? true { last = p }
            case .unknown:
                if p.running { running += 1 }   // a program whose folder is unknown may be the game: never end under it
            case .helper, .client:
                break
            }
        }
        guard let last else { return .none }
        // Round 625 (N-S18): it closed by itself with 0, soon, having drawn nothing (its own error box, then exit).
        let shortCleanExit = !rendered && last.status == 0 && last.lived < shortExitSeconds
        guard anyStatus || isCrash(last.status) || shortCleanExit else { return .none }
        func wait(_ why: String) -> Verdict { .wait(status: last.status, file: last.fileName, why: why) }
        if !table.complete { return wait("table-incomplete") }
        if table.untracked > 0 { return wait("untracked-process") }
        if running > 0 { return wait("game-still-runs") }
        if last.endedAgo < grace { return wait("grace") }
        let state = host()
        if !state.gameRan { return wait("host-never-saw-the-game-run") }
        if !state.gameOver { return wait("host-still-counts-the-game") }
        if !state.finished { return wait("host-not-finished") }
        return .end(status: last.status, file: last.fileName, lived: last.lived)
    }

    // MARK: A launch that failed (round 580)
    //
    // The other way a Dock session stays open for good: the host reports that the LAUNCH failed and no game process ever
    // ran. Spec Ops: The Line (2FRXXM, NEXHHM) and Risen 2 (9DBDZ6) are games whose executable Valve builds for each
    // account (CEG); Valve's client could not (RPC error 1726, `ceg-result=55`), the host finished with `probe-result=49`
    // a few seconds after it asked Valve's client, the starting screen showed the reason, and the session sat on its desktop: 271 s in the
    // longest log until the user closed BCD. The host's report is final once it holds `probe-result` (the host has shut
    // Valve's client down by then, so nothing is still being prepared in the background), and the game never ran
    // (`launch-game-running` is absent): the same wait as above for a program that is about to start (`failedLaunchGrace`),
    // then the session ends, and the library says why. A game that ran, or a program of the game that runs now, is never
    // touched: the first is the crash rule's, the second is a session that is going on.

    /// What the host's report says about the launch (MadeiraDock.Report), as two facts.
    struct DockHostResult: Equatable {
        /// `probe-result`: the number the host finished with; nil while it has not finished.
        var result: Int?
        /// `launch-game-running=1`: Valve's client saw the game run.
        var gameRan: Bool
    }

    /// A launch the host reported as failed, with nothing of the game running. `blocked`: why the session must not be
    /// ended on this reading (a short fixed word for the log); nil when it may be, once `failedLaunchGrace` has passed.
    struct FailedLaunch: Equatable {
        var result: Int
        var blocked: String?
    }

    /// Seconds the failure must have stood, with nothing of the game running, before the session ends. The host finished
    /// before this started counting; a spawn the table has not caught up with yet shows within it.
    static let failedLaunchGrace = 20.0

    /// nil: nothing to say (the host has not finished, it finished well, the game ran, or a program of the game runs).
    /// `host` is asked only when nothing of the game runs, so the report file is not read while a game plays.
    static func failedLaunch(_ table: DockProcessTable, places: SteamLaunchScene.Places,
                             host: () -> DockHostResult) -> FailedLaunch? {
        let shared = valveShared(places)
        for p in table.processes where !p.helper && p.running {
            switch SteamLaunchScene.owner(p.path, places: places) {
            case .other:
                if let shared, SteamLaunchScene.path(p.path).hasPrefix(shared) { break }
                return nil
            case .unknown:
                return nil   // a program whose folder is unknown may be the game: never end under it
            case .helper, .client:
                break
            }
        }
        let state = host()
        guard let result = state.result, result != 0, !state.gameRan else { return nil }
        if !table.complete { return FailedLaunch(result: result, blocked: "table-incomplete") }
        if table.untracked > 0 { return FailedLaunch(result: result, blocked: "untracked-process") }
        return FailedLaunch(result: result, blocked: nil)
    }

    /// The engine's text (process_ios.c, madeira_child_report): one line per process, tab separated, path last.
    /// A line this reader does not know, or a cut text, makes the table incomplete.
    static func parse(_ text: String) -> DockProcessTable {
        var table = DockProcessTable()
        for line in text.split(separator: "\n", omittingEmptySubsequences: true) {
            let f = line.split(separator: "\t", maxSplits: 5, omittingEmptySubsequences: false).map(String.init)
            switch f.first ?? "" {
            case "R" where f.count == 4:
                guard let helper = Int(f[1]), Double(f[2]) != nil else { table.complete = false; continue }
                table.processes.append(DockProcess(path: f[3], helper: helper != 0, running: true))
            case "E" where f.count == 6:
                guard let helper = Int(f[1]), let status = UInt32(f[2], radix: 16),
                      let ago = Double(f[3]), let lived = Double(f[4]) else { table.complete = false; continue }
                table.processes.append(DockProcess(path: f[5], helper: helper != 0, running: false, status: status,
                                                   endedAgo: ago, lived: lived))
            case "U" where f.count == 2:
                table.untracked = Int(f[1]) ?? 1
            default:
                table.complete = false   // "T" (cut) and anything unknown
            }
        }
        return table
    }
}
