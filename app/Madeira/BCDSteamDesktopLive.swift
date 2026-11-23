// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import SwiftUI

/// Steam for Windows on the engine (experimental; branch steam-client, milestone M1; docs/STEAM_DESKTOP.md). Engine-bound: the preview
/// build leaves this file out (tools/ui-preview.py, ENGINE_BOUND). SteamDesktop.swift has the rules; this file is where they meet the
/// app:
///
/// - Settings › BCD Dock › Steam for Windows (BCDSteamDesktop.swift's card): the switch, the install from Valve's update host
///   (SteamDesktopInstaller, SteamDesktopCDN), Open.
/// - The library entry: BCD's own (SteamDesktop.entryID), saved once the client is installed, shown by the library only while the
///   switch is on (hidesEntry). It starts in a Wine desktop, explorer.exe first, so the session lives through steam.exe restarting
///   itself for an update (LibraryEntry.launchMode "desktop": explorer.exe /desktop=shell,<size> "<steam.exe>" <arguments>).
/// - A start (preLaunch, from ContentView.startLibraryEntry): BCD's own Steam connection logs off and its downloads stop first and
///   stay so until the session has ended (SteamOwnedLibrary.prepareDesktopClient, SteamConnectionGate); the registry values are
///   written while no session runs (SteamDesktopRegistry).
/// - The session: its settings (applySessionSettings, on the launch worker), no fixed Steam game identity (ContentView sets
///   SteamDesktop.sessionVariable, WineProcessBridge.m reads it), and the watch that writes the "[steam-desktop]" lines.
/// BCD Dock never runs in the same session: BCD runs one session each time it is opened, and a Dock start is one of its own.
///
/// Log tag: [steam-desktop] (file names, sizes, counts and short reasons; never an account, a path with a user name or an address).
@MainActor final class BCDSteamDesktopLive: ObservableObject, BCDSteamDesktopBackend {
    static let shared = BCDSteamDesktopLive()

    @Published private(set) var state = BCDSteamDesktopState()
    private var task: Task<Void, Never>?
    private var measuring = false
    /// The install's hold on running while BCD is not on screen (BackgroundRun: iOS's grace period, longer with `background-download`),
    /// and whether its end is what stopped the download.
    private var background: BackgroundRun.Token?
    private var pausedInBackground = false

    private init() { refresh() }

    // MARK: - Files

    nonisolated static var prefix: URL { MadeiraDock.prefix }
    nonisolated static var drive: URL { MadeiraDock.drive }
    nonisolated static var root: URL { drive.appendingPathComponent(SteamDesktop.relativeRoot, isDirectory: true) }
    nonisolated static var installed: Bool {
        FileManager.default.fileExists(atPath: root.appendingPathComponent(SteamDesktop.program).path)
    }

    /// The version Valve's own updater recorded once the client brought itself up to date; nil before that.
    nonisolated static var clientVersion: String? {
        SteamDesktopManifest.version(ofFile: root.appendingPathComponent(SteamDesktop.packageFolder + "/" + SteamDesktopManifest.fileName + ".manifest"))
    }

    /// The bytes the files under `folder` take on this device (0 without the folder).
    nonisolated static func folderBytes(_ folder: URL) -> Int64 {
        let keys: Set<URLResourceKey> = [.isRegularFileKey, .totalFileAllocatedSizeKey, .fileSizeKey]
        guard let files = FileManager.default.enumerator(at: folder, includingPropertiesForKeys: Array(keys), options: [], errorHandler: nil) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in files {
            guard let values = try? url.resourceValues(forKeys: keys), values.isRegularFile == true else { continue }
            total += Int64(values.totalFileAllocatedSize ?? values.fileSize ?? 0)
        }
        return total
    }

    /// A prefix that was never made gets its files first (the bridge does the same when a session starts), so the folders the
    /// install adds to and the registry files exist. A prefix that has registry files but no stamp is left as it is, as the Dock's
    /// setup leaves it.
    nonisolated private static func seedNewPrefix() {
        let fm = FileManager.default
        guard !fm.fileExists(atPath: prefix.appendingPathComponent(".update-timestamp").path),
              !["system.reg", "user.reg"].contains(where: { fm.fileExists(atPath: prefix.appendingPathComponent($0).path) }) else { return }
        madeira_seed_prefix_if_needed(prefix.path)
    }

    private static var sessionRunning: Bool {
        LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0
    }

    // MARK: - The card

    func refresh() {
        var next = state
        next.on = SteamDesktop.isEnabled
        next.fetchesPackages = SteamDesktop.fetchesPackages
        next.sharesDock = SteamDesktopLibraries.sharesDock
        next.installed = Self.installed
        if !next.installed { next.version = nil; next.diskBytes = nil }
        if next != state { state = next }
        // The folder is measured off the main thread, and never while a session runs (Steam writes into it then).
        guard next.on, next.installed, !measuring, !Self.sessionRunning else { return }
        measuring = true
        let root = Self.root
        Task.detached(priority: .utility) {
            let version = Self.clientVersion
            let bytes = Self.folderBytes(root)
            await MainActor.run {
                self.measuring = false
                guard self.state.installed else { return }
                if self.state.version != version { self.state.version = version }
                if self.state.diskBytes != bytes { self.state.diskBytes = bytes }
            }
        }
    }

    func setOn(_ on: Bool) {
        UserDefaults.standard.set(on, forKey: SteamDesktop.enabledKey)
        LogStore.shared.log("[steam-desktop] switch \(on ? "on" : "off") installed=\(Self.installed ? 1 : 0)")
        if on { ensureEntry() }
        NotificationCenter.default.post(name: SteamDesktop.didChange, object: nil)
        refresh()
    }

    func setFetchesPackages(_ on: Bool) {
        UserDefaults.standard.set(on, forKey: SteamDesktop.packagesKey)
        refresh()
    }

    /// BCD Dock's games in the client's library (SteamDesktopLibraries); the next session's start writes the list.
    func setSharesDock(_ on: Bool) {
        UserDefaults.standard.set(on, forKey: SteamDesktopLibraries.shareKey)
        LogStore.shared.log("[steam-desktop] share BCD Dock's games \(on ? "on" : "off")")
        // BCD Dock's own list follows the switch at once (the client's list at its next start): off, Steam for Windows' games
        // leave BCD's library now. While a session runs it waits for the next start.
        if !Self.sessionRunning, Self.installed {
            Task.detached(priority: .userInitiated) { LogStore.shared.log("[steam-desktop] " + Self.writeDockList(share: on)) }
        }
        refresh()
    }

    /// Room the install wants free: the packages and what Steam unpacks from them at its first start, or steam.exe alone and what
    /// Steam then downloads and unpacks itself. The plan's estimate for the client is 0.9 to 1.3 GB; the first device run measures it
    /// (the "[steam-desktop] disk:" line).
    static let wantedFreeBytes: Int64 = 2 << 30

    func install() {
        guard task == nil, state.work == nil else { return }
        state.error = nil
        guard !Self.sessionRunning else { state.error = SteamDesktopInstaller.Failure.activeSession.errorDescription; return }
        if let free = (try? URL.documentsDirectory.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]))?
            .volumeAvailableCapacityForImportantUsage, free < Self.wantedFreeBytes {
            LogStore.shared.log("[steam-desktop] install refused: free=\(free >> 20)MB wanted=\(Self.wantedFreeBytes >> 20)MB")
            state.error = "Not enough free space: Steam for Windows needs about 2 GB free on this device."
            return
        }
        let packages = SteamDesktop.fetchesPackages, drive = Self.drive
        LogStore.shared.log("[steam-desktop] install: start packages=\(packages ? 1 : 0)")
        state.work = BCDSteamDesktopState.Work(step: "Asking Valve's update server…")
        pausedInBackground = false
        // As BCD's other downloads: it goes on for iOS's grace period after BCD leaves the screen (and for `background-download`
        // minutes when that is set), then stops cleanly; what arrived stays for the next Install.
        background = BackgroundRun.shared.beginTask("Steam for Windows install") { [weak self] in
            MainActor.assumeIsolated {
                guard let self, self.state.work != nil else { return }
                self.pausedInBackground = true
                self.task?.cancel()
            }
        }
        let gate = ProgressGate()
        task = Task { [weak self] in
            await Task.detached(priority: .userInitiated) { Self.seedNewPrefix() }.value
            let source = SteamDesktopCDN()
            defer { source.close() }
            do {
                let outcome = try await SteamDesktopInstaller.install(
                    drive: drive, transport: source, packages: packages,
                    sessionRunning: { wine_process_is_running() != 0 || wineserver_is_running() != 0 },
                    log: { LogStore.shared.log($0) },
                    progress: { progress in
                        guard gate.passes(progress) else { return }
                        Task { @MainActor in self?.progressed(progress) }
                    })
                self?.finished(outcome)
            } catch {
                self?.failed(error)
            }
        }
    }

    func stopInstall() {
        guard state.work != nil else { return }
        LogStore.shared.log("[steam-desktop] install: cancel asked")
        task?.cancel()
    }

    private func progressed(_ progress: SteamDesktopInstaller.Progress) {
        guard state.work != nil else { return }
        let step: String
        switch progress.step {
        case .manifest: step = "Asking Valve's update server…"
        case .bootstrapper: step = "Getting steam.exe…"
        case .packages: step = "Getting the client's files…"
        case .installing: step = "Putting steam.exe in place…"
        }
        state.work = BCDSteamDesktopState.Work(step: step, received: progress.received, total: progress.total)
    }

    private func endBackgroundHold() {
        if let background { BackgroundRun.shared.endTask(background) }
        background = nil
    }

    private func finished(_ outcome: SteamDesktopInstaller.Outcome) {
        task = nil
        state.work = nil
        endBackgroundHold()
        ensureEntry()
        NotificationCenter.default.post(name: SteamDesktop.didChange, object: nil)
        refresh()
    }

    private func failed(_ error: Error) {
        task = nil
        state.work = nil
        endBackgroundHold()
        if error is CancellationError {
            LogStore.shared.log("[steam-desktop] install: \(pausedInBackground ? "stopped because BCD was not on screen" : "cancelled") "
                                + "(what is in the package folder stays for the next try)")
            if pausedInBackground { state.error = "The download stopped because BCD was not on screen. Install goes on where it stopped." }
        } else {
            let failure = error as? SteamDesktopInstaller.Failure
            LogStore.shared.log("[steam-desktop] install failed: \(failure?.logReason ?? "other")")
            state.error = failure?.errorDescription ?? "The install did not finish: \(error.localizedDescription)"
        }
        refresh()
    }

    func open() {
        guard !state.starting else { return }
        state.error = nil
        refresh()
        guard state.installed else { state.error = "Steam for Windows isn't installed on this device yet."; return }
        let library = LibraryModel.shared
        if Self.sessionRunning { state.error = "Another session is still running."; return }
        // One Wine session per run of BCD (ContentView.launchLibraryEntry): say so here, on the card.
        if LibraryModel.sessionsThisRun > 0, MadeiraConfig.flag("MADEIRA_ONE_SESSION_PER_RUN") {
            LogStore.shared.log("[steam-desktop] open held: \(LibraryModel.sessionsThisRun) session(s) already ran in this run of BCD")
            library.restartNotice = LibraryModel.restartMessage
            state.error = LibraryModel.restartMessage
            return
        }
        state.starting = true
        ensureEntry()
        let entry = Self.entry(stored: Self.storedEntry())
        LogStore.shared.log("[steam-desktop] open from Settings")
        BCDLiveStore.shared.startLauncherEntry(entry, game: BCDLiveID.local(entry.id))
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            self?.state.starting = false
        }
    }

    // MARK: - The library entry

    /// The library leaves BCD's own entry out while the switch is off (BCDLiveStore.rebuild). An entry the player added by hand for
    /// the same program is his and always shows.
    nonisolated static func hidesEntry(_ id: UUID) -> Bool { id == SteamDesktop.entryID && !SteamDesktop.isEnabled }

    /// A session of Valve's client: the entry's program is the client's steam.exe (BCD's own entry, or one added by hand).
    nonisolated static func isSession(_ entry: LibraryEntry) -> Bool {
        entry.desktop != true && entry.steamAppID == nil && SteamDesktop.isClient(windowsPath: entry.launchWindowsPath)
    }

    private static func storedEntry() -> LibraryEntry? {
        LibraryModel.shared.entries.first { $0.id == SteamDesktop.entryID }
    }

    /// BCD's entry for the client. A stored one keeps everything the player set (the size of the desktop, the Launch arguments, the
    /// display and the controls); what BCD owns is its program, that it starts in a Wine desktop, and that Windows services start first.
    static func entry(stored: LibraryEntry?) -> LibraryEntry {
        var entry: LibraryEntry
        if let stored {
            entry = stored
        } else {
            entry = (try? LibraryModel.inspect(drive.appendingPathComponent(SteamDesktop.relativeProgram)))
                ?? LibraryEntry(title: SteamDesktop.title, relativePath: SteamDesktop.relativeProgram, bits: 64)
            entry.id = SteamDesktop.entryID
            entry.title = SteamDesktop.title
            entry.arguments = SteamDesktop.defaultArguments
        }
        entry.relativePath = SteamDesktop.relativeProgram
        entry.launchMode = "desktop"
        entry.startServices = true
        return entry
    }

    /// The installed client has its library entry (the tile), made once; nothing without the client. A tile made by build 689 started
    /// no Windows services: it gets the services switch once, so the game page shows what its start does.
    private func ensureEntry() {
        guard Self.installed else { return }
        guard let stored = Self.storedEntry() else {
            LibraryModel.shared.save(Self.entry(stored: nil))
            LogStore.shared.log("[steam-desktop] library entry made")
            return
        }
        guard stored.startServices != true || stored.launchMode != "desktop" || stored.relativePath != SteamDesktop.relativeProgram else { return }
        LibraryModel.shared.save(Self.entry(stored: stored))
        LogStore.shared.log("[steam-desktop] library entry updated: Windows services first, in the Wine desktop")
    }

    // MARK: - A start

    /// The entry whose start is prepared and now goes on.
    private static var prepared: UUID?

    /// ContentView.startLibraryEntry, right before the launch is set up. True: go on. False: this is a Steam for Windows start that
    /// first waits for BCD's own Steam connection to log off and for the registry values; it comes back through `retry` (or stops
    /// with words when something changed meanwhile). Any other entry: true at once, nothing touched.
    static func preLaunch(_ entry: inout LibraryEntry, retry: @escaping (LibraryEntry) -> Void) -> Bool {
        guard isSession(entry) else { return true }
        if entry.id == SteamDesktop.entryID { entry.launchMode = "desktop" }
        // Valve's client asks the service manager (\pipe\svcctl) at its first step and quits with -1 when there is none (build 689,
        // 2026-11-21 21:01, 21 tries in 9 s), as Battle.net's agent did in build 593. Every start of it, BCD's entry or one added by
        // hand, runs the services batch first (services.exe, then steam.exe), whatever the entry's own switch says.
        entry.startServices = true
        entry.arguments = SteamDesktop.arguments(entry: entry.arguments)
        if prepared == entry.id {
            prepared = nil
            return true
        }
        let start = entry
        LogStore.shared.log("[steam-desktop] start: own-entry=\(start.id == SteamDesktop.entryID ? 1 : 0) desktop=\(start.runsInDesktop ? 1 : 0) "
                            + "services=\(start.startServices == true ? 1 : 0) "
                            + "switch=\(SteamDesktop.isEnabled ? 1 : 0) client=\(clientVersion ?? "bootstrapper-only") size=\(start.resolution) "
                            + "arguments=\(SteamDesktop.argumentsForLog(start.arguments))")
        if !start.runsInDesktop {
            LogStore.shared.log("[steam-desktop] start: this entry starts steam.exe on its own, so the session ends when Steam restarts itself; "
                                + "Start: In the Wine desktop keeps it")
        }
        Task { @MainActor in
            // BCD Dock's DLC choices for the client (SteamDesktopDLC, 2026-11-22): the owned DLC with files of each BCD Dock game the
            // client will list, asked over BCD's own Steam connection while it may still connect; written into the games' records
            // after the share below, so the client downloads none that BCD did not install.
            // Only the libraries BCD manages (its own and the drives', SteamInstallPaths.isManaged): a game Steam for Windows installed
            // in its own folder keeps the client's DLC handling, as BCD neither updates it nor changes its DLC.
            let dockGames = SteamDesktopLibraries.sharesDock
                ? MadeiraDock.games(drive: drive).filter { $0.installed && SteamInstallPaths.isManaged(library: $0.library) } : []
            let choices = SteamOwnedLibrary.shared.dlc.choices
            let wanted = Dictionary(uniqueKeysWithValues: dockGames.compactMap { game in choices.wanted(game.id).map { (game.id, $0) } })
            let dlcStarted = Date()
            let owned: [Int: Set<UInt32>]
            if dockGames.isEmpty { owned = [:] } else {
                owned = await SteamOwnedLibrary.shared.dlc.ownedWithContent(dockGames.map(\.id), seconds: SteamDesktopDLC.lookupSeconds)
            }
            let dlcSeconds = Date().timeIntervalSince(dlcStarted)
            // Only one sign-in of the account may be online: BCD's own Steam connection logs off before Valve's client can sign in,
            // and stays off until this session has ended (sessionEnded).
            await SteamOwnedLibrary.shared.prepareDesktopClient()
            guard !sessionRunning else {
                SteamOwnedLibrary.shared.desktopClientEnded()
                LibraryModel.shared.error = "Something changed while Steam for Windows was getting ready. Try again."
                return
            }
            await Task.detached(priority: .userInitiated) { seedNewPrefix() }.value
            let seeded = SteamDesktopRegistry.seed(prefix: prefix, softwareWebViews: SteamDesktop.softwareWebViews(start.arguments),
                                                   sessionRunning: false)
            LogStore.shared.log(seeded.line)
            // BCD Dock's games in the client's library (SteamDesktopLibraries), still while no session runs.
            let libraries = await Task.detached(priority: .userInitiated) { Self.shareLibraries() }.value
            LogStore.shared.log(libraries)
            if !dockGames.isEmpty {
                let records = dockGames.map { game in
                    (id: game.id, url: drive.appendingPathComponent(game.library, isDirectory: true).appendingPathComponent("appmanifest_\(game.id).acf"))
                }
                let kept = await Task.detached(priority: .userInitiated) {
                    Self.keepDockDLC(records, owned: owned, wanted: wanted, seconds: dlcSeconds)
                }.value
                LogStore.shared.log(kept.line)
                sessionDLC = kept.games
            }
            prepared = start.id
            retry(start)
            // The start did not become a session after all (its words are on screen): the account is BCD's again.
            if LibraryModel.shared.current == nil {
                prepared = nil
                sessionDLC = [:]
                SteamOwnedLibrary.shared.desktopClientEnded()
            }
        }
        return false
    }

    /// BCD Dock's libraries in the client's list before a session (SteamDesktopLibraries), while no session runs. With the share on,
    /// BCD Dock's library files are copied once first. Returns the log line; nothing here stops the start.
    nonisolated static func shareLibraries() -> String {
        let fm = FileManager.default
        let share = SteamDesktopLibraries.sharesDock
        let drives = ExternalDrives.steamLibraries.map { $0.hasSuffix("/steamapps") ? String($0.dropLast("/steamapps".count)) : $0 }
        let dockApps = drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot + "/steamapps", isDirectory: true)
        var dockGames = 0
        if share {
            let names = (try? fm.contentsOfDirectory(atPath: dockApps.path)) ?? []
            dockGames = names.filter { $0.lowercased().hasPrefix("appmanifest_") }.count
            backupDockLibraryOnce(names: names, from: dockApps)
        }
        let head = "[steam-desktop] libraries: dock=\(share ? "shared" : "not-shared") dock-games=\(dockGames) drives=\(drives.count)"
        let list = drive.appendingPathComponent(SteamDesktopLibraries.listRelative)
        do {
            let existing: Data?
            do { existing = try Data(contentsOf: list) }
            catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { existing = nil }
            guard let next = try SteamDesktopLibraries.list(existing, share: share, drives: drives) else {
                return head + " list=unchanged " + writeDockList(share: share)
            }
            try fm.createDirectory(at: list.deletingLastPathComponent(), withIntermediateDirectories: true)
            try next.write(to: list, options: .atomic)
            return head + " list=written " + writeDockList(share: share)
        } catch {
            return head + " list=kept (\(error.localizedDescription)) " + writeDockList(share: share)
        }
    }

    /// BCD Dock's own list with the client's library (`share`) or without it (SteamDesktopLibraries.dockList), so games installed in
    /// Steam for Windows show in BCD's library and start through BCD Dock. Only while no session runs. Returns its log words.
    nonisolated static func writeDockList(share: Bool) -> String {
        let url = drive.appendingPathComponent(SteamDesktopLibraries.dockListRelative)
        do {
            let existing: Data?
            do { existing = try Data(contentsOf: url) }
            catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError { existing = nil }
            guard let next = try SteamDesktopLibraries.dockList(existing, share: share) else { return "dock-list=unchanged" }
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try next.write(to: url, options: .atomic)
            return "dock-list=" + (share ? "client-added" : "client-removed")
        } catch {
            return "dock-list=kept (\(error.localizedDescription))"
        }
    }

    /// BCD Dock's DLC choices in its games' records before a session (SteamDesktopDLC), while no session runs: each record's
    /// `DisabledDLC` lists the owned DLC with files that BCD did not install, with the list's own entries, never an installed one.
    /// Only that value changes. Returns the log line; nothing here stops the start.
    nonisolated static func keepDockDLC(_ records: [(id: Int, url: URL)], owned: [Int: Set<UInt32>], wanted: [Int: Set<UInt32>],
                                        seconds: Double) -> (line: String, games: [Int: SessionDLC]) {
        var written = 0, unchanged = 0, unread = 0, failed = 0, off = 0
        var notLookedUp: [Int] = []
        var games: [Int: SessionDLC] = [:]
        for record in records {
            guard let data = try? Data(contentsOf: record.url), data.count <= 1 << 20 else { unread += 1; continue }
            if owned[record.id] == nil { notLookedUp.append(record.id) }
            let installed = SteamDesktopDLC.installed(data)
            let want = SteamDesktopDLC.disabled(owned: owned[record.id] ?? [], installed: installed,
                                                current: SteamDesktopDLC.listed(data), wanted: wanted[record.id])
            off += want.count
            games[record.id] = SessionDLC(record: record.url, written: want, installed: installed)
            guard let next = SteamDesktopDLC.record(data, disabled: want) else { unchanged += 1; continue }
            do { try next.write(to: record.url, options: .atomic); written += 1 } catch { failed += 1 }
        }
        var line = "[steam-desktop] dlc: games=\(records.count) looked-up=\(records.count - notLookedUp.count) records-written=\(written) "
            + "unchanged=\(unchanged) dlc-kept-off=\(off) lookup=\(String(format: "%.1f", seconds))s"
        if unread > 0 { line += " unread=\(unread)" }
        if failed > 0 { line += " write-failed=\(failed)" }
        if !notLookedUp.isEmpty {
            line += " not-looked-up=" + notLookedUp.prefix(12).map(String.init).joined(separator: ",")
                + " (the client may download their DLC)"
        }
        return (line, games)
    }

    /// A BCD Dock game's DLC as a session found it: the list BCD wrote into its record and what the record stated as installed.
    struct SessionDLC: Sendable {
        let record: URL
        let written: Set<UInt32>
        let installed: Set<UInt32>
    }

    /// The games of the session being started or running (keepDockDLC), for its end (adoptSessionDLC). Main thread only.
    private static var sessionDLC: [Int: SessionDLC] = [:]

    /// The session's end: BCD's DLC choice of each BCD Dock game takes what the player did in the client (SteamDesktopDLC.adopted),
    /// so BCD's next update keeps a DLC the client installed and does not bring back one the client took out. Returns the log line,
    /// or nil when this was no Steam for Windows session.
    private static func adoptSessionDLC() -> String? {
        let games = sessionDLC
        sessionDLC = [:]
        guard !games.isEmpty else { return nil }
        let choices = SteamOwnedLibrary.shared.dlc.choices
        var changed: [String] = []
        for (id, game) in games.sorted(by: { $0.key < $1.key }) {
            guard let data = try? Data(contentsOf: game.record), data.count <= 1 << 20 else { continue }
            let before = choices.wanted(id)
            guard let after = SteamDesktopDLC.adopted(wanted: before, installedBefore: game.installed, written: game.written,
                                                      installedAfter: SteamDesktopDLC.installed(data), listedAfter: SteamDesktopDLC.listed(data))
            else { continue }
            choices.set(id, wanted: after)
            let base = before ?? game.installed
            changed.append("\(id):+\(after.subtracting(base).count)-\(base.subtracting(after).count)")
        }
        return "[steam-desktop] dlc after the session: games=\(games.count) choices-changed=\(changed.count)"
            + (changed.isEmpty ? "" : " (" + changed.prefix(12).joined(separator: " ") + ")")
    }

    /// One copy of BCD Dock's library files from before the first shared session (SteamDesktopLibraries.backupFolder): made in a
    /// folder of its own and moved in place whole, so a copy that stopped half-way is made again next time.
    nonisolated private static func backupDockLibraryOnce(names: [String], from folder: URL) {
        let fm = FileManager.default
        let backup = drive.appendingPathComponent(SteamDesktopLibraries.backupFolder, isDirectory: true)
        guard !fm.fileExists(atPath: backup.path) else { return }
        let kept = names.filter(SteamDesktopLibraries.keepsInBackup)
        guard !kept.isEmpty else { return }
        let partial = backup.deletingLastPathComponent().appendingPathComponent("dock-library.partial", isDirectory: true)
        do {
            try? fm.removeItem(at: partial)
            try fm.createDirectory(at: partial, withIntermediateDirectories: true)
            for name in kept { try fm.copyItem(at: folder.appendingPathComponent(name), to: partial.appendingPathComponent(name)) }
            try fm.moveItem(at: partial, to: backup)
            LogStore.shared.log("[steam-desktop] libraries: BCD Dock's library files copied once (\(kept.count)) to \(SteamDesktopLibraries.backupFolder)")
        } catch {
            try? fm.removeItem(at: partial)
            LogStore.shared.log("[steam-desktop] libraries: the copy of BCD Dock's library files was not made (\(error.localizedDescription))")
        }
    }

    /// The session's settings (SteamDesktopProfile): on the launch worker, after LibraryEntry.applyEnvironment chose the file the
    /// game's own lines are read from and before the JIT pool is asked for. BCD's lines go above that file's text in a file of this
    /// session's own, which MADEIRA_CFG_GAME then names: the engine and the app read every key from it, the last line of a key wins,
    /// so the entry's own lines win over BCD's. Returns the lines for the log.
    nonisolated static func applySessionSettings() -> [String] {
        let chosen = getenv("MADEIRA_CFG_GAME").map { String(cString: $0) } ?? ""
        let own = chosen.isEmpty ? nil : try? String(contentsOfFile: chosen, encoding: .utf8)
        let text = SteamDesktopProfile.sessionFile(own: own)
        guard let folder = MadeiraConfig.gameURL?.deletingLastPathComponent() else {
            return ["[steam-desktop] settings: no place for the session's file; this session runs without BCD's settings"]
        }
        let url = folder.appendingPathComponent(SteamDesktopProfile.fileName)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try text.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            return ["[steam-desktop] settings: the session's file could not be written; this session runs without BCD's settings"]
        }
        setenv("MADEIRA_CFG_GAME", url.path, 1)
        return [SteamDesktopProfile.effectiveLine(sessionFile: text)]
    }

    /// The JIT pool this session got, in one line (ContentView, after the pool request).
    nonisolated static func poolLine(askedMB: Int, gotMB: Int) -> String {
        var line = "[steam-desktop] jit-pool: asked=\(askedMB)MB got=\(gotMB)MB"
        if let hole = StikJITHelper.poolHole { line += " split-hole=\((hole.end - hole.off) >> 20)MB" }
        if let low = StikJITHelper.poolLow { line += " region-c=\(low.size >> 20)MB" }
        return line + " (the pool counts in full against the memory limit)"
    }

    // MARK: - The session

    /// The running session's watch. LibraryModel.begin and finish, which set and clear it, run on the main thread.
    nonisolated(unsafe) private static var watch: SteamDesktopSessionWatch?

    /// LibraryModel.begin: a Steam for Windows session starts its watch; any other session, nothing.
    nonisolated static func sessionBegan(_ entry: LibraryEntry) {
        guard isSession(entry) else { return }
        watch?.end(summary: false)
        let next = SteamDesktopSessionWatch(root: root)
        watch = next
        next.start()
    }

    /// LibraryModel.finish (the main thread): the watch's last lines, and BCD's own Steam connection may come back once no session
    /// runs. Nothing for any other session.
    nonisolated static func sessionEnded() {
        watch?.end(summary: true)
        watch = nil
        MainActor.assumeIsolated {
            prepared = nil
            if let line = adoptSessionDLC() { LogStore.shared.log(line) }
            SteamOwnedLibrary.shared.desktopClientEnded()
        }
    }
}

/// Lets a progress report through four times a second at most, and always when the step changes.
private final class ProgressGate: @unchecked Sendable {
    private let lock = NSLock()
    private var last = 0.0
    private var step: SteamDesktopInstaller.Step?

    func passes(_ progress: SteamDesktopInstaller.Progress) -> Bool {
        let now = ProcessInfo.processInfo.systemUptime
        lock.lock(); defer { lock.unlock() }
        guard progress.step != step || now - last >= 0.25 || (progress.total > 0 && progress.received >= progress.total) else { return false }
        step = progress.step
        last = now
        return true
    }
}

/// The watch of one Steam for Windows session (SteamDesktopWatch has the rules): every three seconds, on a queue of its own, it reads
/// the engine's process table and what the session's log gained, and writes the "[steam-desktop]" lines; every thirty seconds one
/// status line. It reads only: nothing of the session is changed, no window is asked anything.
final class SteamDesktopSessionWatch: @unchecked Sendable {
    private let queue = DispatchQueue(label: "bcd.steam-desktop.watch", qos: .userInitiated)
    private let root: URL
    private var timer: DispatchSourceTimer?
    private var rules = SteamDesktopWatch()
    private var log: FileHandle?
    private var offset: UInt64 = 0
    private var endsAfter: UInt32 = 0
    private var started = 0.0
    private var ticks = 0
    private var ended = false

    static let interval = 3.0
    static let statusEvery = 10
    /// More new log text than this in one look is not read (a log storm): the watch goes on from the file's end.
    static let maxChunk: UInt64 = 8 << 20

    init(root: URL) { self.root = root }

    func start() {
        queue.async { [self] in
            started = ProcessInfo.processInfo.systemUptime
            endsAfter = madeira_child_ends_since(UInt32.max, nil, 0)
            if let handle = try? FileHandle(forReadingFrom: LogStore.shared.logFileURL) {
                log = handle
                offset = (try? handle.seekToEnd()) ?? 0
            }
            let source = DispatchSource.makeTimerSource(queue: queue)
            source.schedule(deadline: .now() + Self.interval, repeating: Self.interval, leeway: .milliseconds(500))
            source.setEventHandler { [weak self] in self?.look() }
            source.resume()
            timer = source
        }
    }

    private func memory() -> SteamDesktopWatch.Memory {
        var info = task_vm_info_data_t()
        var size = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(size)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &size) }
        }
        return SteamDesktopWatch.Memory(footprintMB: result == KERN_SUCCESS ? Int(info.phys_footprint >> 20) : 0,
                                        leftMB: Int(os_proc_available_memory() >> 20))
    }

    /// What the log gained since the last look, as text.
    private func newLogText() -> String {
        guard let log else { return "" }
        guard let end = try? log.seekToEnd(), end > offset else { return "" }
        if end - offset > Self.maxChunk {
            LogStore.shared.log("[steam-desktop] log: \((end - offset) >> 20)MB of new lines not read (too many at once)")
            offset = end
            return ""
        }
        guard (try? log.seek(toOffset: offset)) != nil, let data = try? log.read(upToCount: Int(end - offset)) else { return "" }
        offset += UInt64(data.count)
        return String(decoding: data, as: UTF8.self)
    }

    private func look() {
        guard !ended else { return }
        ticks += 1
        let seconds = ProcessInfo.processInfo.systemUptime - started
        let now = memory()
        var report = [CChar](repeating: 0, count: 16 * 1024)
        _ = madeira_child_report(&report, Int32(report.count))
        var ends = [CChar](repeating: 0, count: 8 * 1024)
        endsAfter = madeira_child_ends_since(endsAfter, &ends, Int32(ends.count))
        func text(_ buffer: [CChar]) -> String { String(decoding: buffer.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }, as: UTF8.self) }
        var lines = rules.read(processes: text(report), ends: text(ends), at: seconds, memory: now)
        lines += rules.read(log: newLogText(), at: seconds, memory: now)
        if ticks % Self.statusEvery == 0 { lines.append(rules.status(at: seconds, memory: now)) }
        for line in lines { LogStore.shared.log(line) }
        if let words = rules.notice {
            rules.notice = nil
            DispatchQueue.main.async {
                let library = LibraryModel.shared
                guard library.current != nil, library.sessionMessage.isEmpty else { return }
                library.sessionMessage = words
                DispatchQueue.main.asyncAfter(deadline: .now() + 20) { if library.sessionMessage == words { library.sessionMessage = "" } }
            }
        }
    }

    /// The session ended: one last look, the summary, what Valve's own updater said, and what the folder holds now.
    func end(summary: Bool) {
        queue.async { [self] in
            guard !ended else { return }
            if summary { look() }
            ended = true
            timer?.cancel()
            timer = nil
            guard summary else { return }
            LogStore.shared.log(rules.summary(at: ProcessInfo.processInfo.systemUptime - started, memory: memory()))
            for line in Self.updaterLines(root: root) { LogStore.shared.log(line) }
            let package = root.appendingPathComponent(SteamDesktop.packageFolder, isDirectory: true)
            LogStore.shared.log("[steam-desktop] disk: folder=\(BCDSteamDesktopLive.folderBytes(root) >> 20)MB "
                                + "of it packages=\(BCDSteamDesktopLive.folderBytes(package) >> 20)MB "
                                + "client=\(BCDSteamDesktopLive.clientVersion ?? "bootstrapper-only")")
            try? log?.close()
            log = nil
        }
    }

    /// The last lines of Valve's own updater log (logs\bootstrap_log.txt in the client's folder) that say what it did with the
    /// packages: downloaded, checked, unpacked, failed. At most 24 lines of 200 characters; the lines carry no account.
    static func updaterLines(root: URL) -> [String] {
        let url = root.appendingPathComponent("logs/bootstrap_log.txt")
        guard let handle = try? FileHandle(forReadingFrom: url) else { return ["[steam-desktop] updater: no bootstrap_log.txt yet"] }
        defer { try? handle.close() }
        let end = (try? handle.seekToEnd()) ?? 0
        try? handle.seek(toOffset: end > 64 * 1024 ? end - 64 * 1024 : 0)
        let text = String(decoding: (try? handle.readToEnd()) ?? Data(), as: UTF8.self)
        let words = ["download", "verif", "extract", "unzip", "install", "update", "manifest", "package", "fail", "error", "corrupt", "disk space"]
        let kept = text.split(whereSeparator: \.isNewline).filter { line in
            let lower = line.lowercased()
            return !lower.contains("launched with") && words.contains { lower.contains($0) }
        }.suffix(24)
        return kept.map { "[steam-desktop] updater: " + String($0.prefix(200)) }
    }
}
