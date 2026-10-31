// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import SwiftUI

/// State of the Madeira Dock sheet: sign-in, Valve's client components, the
/// installed games and the last Dock result.
@MainActor
final class MadeiraDockModel: ObservableObject {
    static let shared = MadeiraDockModel()

    @Published private(set) var games: [DockGame] = []
    @Published private(set) var clientInstalled = false
    @Published private(set) var preparing = false
    @Published private(set) var progress = ""
    @Published var error: String?
    /// The last Dock launch's outcome, from the host report.
    @Published var status: String?
    /// Opt-in per launch: a 512 MB JIT pool instead of the standard one.
    @Published var compactPool = SteamSignIn.flag("MADEIRA_DOCK_COMPACT_POOL", default: false)
    /// App ID -> number of one-time install programs in the game's Steam install scripts.
    @Published private(set) var installPrograms: [Int: Int] = [:]
    /// App ID -> the game's One-time installs choice (true: Run at next start).
    @Published private(set) var installRunNext: [Int: Bool] = [:]
    /// App ID -> the Record as done / Reset request its next Dock start applies.
    @Published private(set) var installRequest: [Int: DockInstallRequest] = [:]

    private var watch: Task<Void, Never>?

    func refresh() {
        clientInstalled = MadeiraDock.clientInstalled
        let drive = MadeiraDock.drive, prefix = MadeiraDock.prefix
        Task.detached(priority: .userInitiated) {
            let found = MadeiraDock.games(drive: drive)
            var programs: [Int: Int] = [:], runNext: [Int: Bool] = [:], requests: [Int: DockInstallRequest] = [:]
            if DockInstallers.choiceEnabled {
                let ledger = DockInstallLedger.load(prefix: prefix)
                for game in found where game.installed {
                    let count = DockInstallers.programCount(game, drive: drive)
                    if count > 0 { programs[game.id] = count; runNext[game.id] = ledger.runsNext(game.id) }
                    if count > 0, let request = ledger.request(game.id) { requests[game.id] = request }
                }
            }
            let counts = programs, choices = runNext, pendingRequests = requests
            await MainActor.run {
                self.games = found; self.installPrograms = counts; self.installRunNext = choices
                self.installRequest = pendingRequests
            }
        }
    }

    /// The game's One-time installs choice, saved next to the prefix's registry files.
    func setRunsInstallers(_ appID: Int, _ run: Bool) {
        DockInstallers.setRunsNext(appID, run, prefix: MadeiraDock.prefix)
        installRunNext[appID] = run
    }

    /// Saves (nil: cancels) the game's Record as done / Reset request; its next Dock start
    /// applies it while no session runs.
    func setInstallRequest(_ appID: Int, _ request: DockInstallRequest?) {
        DockInstallers.setRequest(appID, request, prefix: MadeiraDock.prefix)
        installRequest[appID] = request
    }

    /// Downloads and unpacks Valve's client components (no Wine session).
    func prepareClient() {
        guard !preparing else { return }
        preparing = true; error = nil; progress = "Starting…"
        Task { @MainActor in
            do {
                try await SteamRuntimeInstaller.shared.prepare(prefix: MadeiraDock.prefix) { text in
                    await MainActor.run { self.progress = text }
                }
                SteamLog.event("[dock-setup] client components ready")
            } catch {
                self.error = error.localizedDescription
                SteamLog.event("[dock-setup] client components failed: \(error.localizedDescription)"
                               + (SteamRuntimeFiles.lastConflict.map { " (at \($0))" } ?? ""))
            }
            preparing = false
            refresh()
        }
    }

    /// Follows the host's report until it records a result, or the session ends
    /// without one, then removes any unconsumed sign-in transfer.
    func watchReport() {
        watch?.cancel()
        let starting = DockInstallers.installsOnly ? "This game's one-time installs are running…"
                                                   : "Starting BCD Dock. Valve's client will sign in and check the license."
        let plan = DockInstallers.note
        status = plan.map { $0 + "\n" + starting } ?? starting
        if let plan { LogStore.shared.log("[dock-installers] " + plan) }
        watch = Task { @MainActor in
            var idle = 0, started = false, offered = false
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                let report = MadeiraDock.pollReport()
                DockOffline.observe(report.fields)
                // The game's one-time installs run before the host writes its first field.
                if DockInstallers.script != nil {
                    let progress = DockInstallers.poll(drive: MadeiraDock.drive)
                    if report.fields["probe-start-bits"] == nil, report.result == nil, let progress { status = progress }
                }
                if report.result != nil {
                    if let failure = report.failure {
                        status = failure
                        LogStore.shared.log("[madeira-dock] " + failure, level: .error)
                        // madeira-bcd (round 578): a failed CEG start (host result 49) leaves Valve's own account in the
                        // client's log files; the lines about the job, the service and DRM go to the log.
                        if report.result == 49, report.fields["ceg-result"] != nil { DockCEGService.logSteamLogs(drive: MadeiraDock.drive) }
                    } else {
                        status = "BCD Dock ended without errors."
                        LogStore.shared.log("[madeira-dock] host finished (result 0)")
                    }
                    break
                }
                // Steam still counts another session of this account as playing
                // (launch refusal 35); the host asks again for up to three minutes.
                if report.fields["launch-session-wait"] != nil, report.fields["launch-client-error"] == "35" {
                    status = "Steam still counts this account as playing in another session. Waiting up to 3 minutes for Steam to end it…"
                }
                // The session starts after the JIT pool is set up; count only once it ran.
                let running = wine_process_is_running() != 0 || wineserver_is_running() != 0
                if running { started = true; idle = 0 } else { idle += 1 }
                if !started && idle >= 150 {
                    status = "The Dock session never started. The log has the details."
                    break
                }
                // madeira-bcd: a program an installer started (a launcher, a helper) can keep a
                // session of one-time installs open after its batch ended. The wineserver writes
                // the registry to disk every 30 s, so 45 s after the batch a restart loses nothing.
                if DockInstallers.installsOnly, running, !offered, let finished = DockInstallers.finishedAt,
                   Date().timeIntervalSince(finished) >= Double(DockInstallers.restartOfferDelay) {
                    offered = true
                    let ended = DockInstallers.poll(drive: MadeiraDock.drive) ?? "Done with the one-time installs."
                    status = ended + "\n" + DockInstallers.restartWords
                    LogStore.shared.log("[dock-installers] the session still runs 45 s after the installs ended (a program they started); the restart is offered now")
                    LibraryModel.shared.restartNotice = DockInstallers.restartNotice(ended)
                }
                if started && idle >= 5 {
                    if DockInstallers.installsOnly {
                        // madeira-bcd: a session of one-time installs ends after its batch, and the
                        // game starts in the next app run (DockInstallers.installsOnly). The
                        // wineserver has stopped by now, so the registry is on disk.
                        let ended = DockInstallers.poll(drive: MadeiraDock.drive) ?? "The session for the one-time installs has ended."
                        status = ended + "\n" + DockInstallers.restartWords
                        LogStore.shared.log("[dock-installers] the installs-only session ended; the game starts after BCD is restarted")
                        LibraryModel.shared.restartNotice = DockInstallers.restartNotice(DockInstallers.poll(drive: MadeiraDock.drive))
                    } else {
                        status = "BCD Dock quit without reporting a result. Export the log to find out why."
                        LogStore.shared.log("[madeira-dock] session ended without a host result", level: .error)
                    }
                    break
                }
            }
            MadeiraDock.cleanup()
            // The host is done with the sign-in: the app's own Steam connection may come
            // back once no session runs (SteamOwnedLibrary).
            SteamOwnedLibrary.shared.dockEnded()
        }
    }
}
