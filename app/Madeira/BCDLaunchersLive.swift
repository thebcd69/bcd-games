// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// PC launchers on the engine (BCDLaunchers.swift; docs/BCD-UI-ARCHITECTURE.md): the files in drive_c, the library
/// entries and the session. Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).
///
/// - Installed: one of the launcher's programs exists in the prefix (BCDLauncherFiles).
/// - Install: the company's installer is downloaded into drive_c/bcd-launchers (BCDLauncherInstaller: the company's
///   own HTTPS host, every redirect checked, the file looked at) and started as the session's first program through
///   the same Play every game uses. A library entry made for the run is not kept in the library (see isSetup).
/// - Open: the installed launcher's program is started the same way, from a library entry of its own (BCDLauncherSpec.entryID),
///   which holds its per-game settings and takes the recommended list and auto-tune by its program's name like any
///   other program.
/// - Both start directly: Wine's first process is the program, with no explorer desktop behind it
///   (BCDLauncherStart); the launcher's own session lines (BCDLauncherSpec.sessionConfig) go into the entry's config.
///
/// Log tag: [launcher] (the launcher's id, host names, byte counts and short reasons; never a login or an address with a path).
@objc(BCDLaunchersLive)
@MainActor final class BCDLaunchersLive: NSObject, BCDLauncherEngine {
    private var control: BCDLauncherDownloadControl?

    nonisolated override init() { super.init() }

    // MARK: - Files

    func installedProgram(_ spec: BCDLauncherSpec) -> String? {
        BCDLauncherFiles.installedProgram(spec, in: LibraryModel.drive)
    }

    // MARK: - Can a session start?

    func sessionBlocker() -> String? {
        let library = LibraryModel.shared
        if library.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0 {
            return "Another session is still running."
        }
        // One Wine session per run of BCD (ContentView.launchLibraryEntry): ask for the restart now, before anything is downloaded.
        if LibraryModel.sessionsThisRun > 0, MadeiraConfig.flag("MADEIRA_ONE_SESSION_PER_RUN") {
            LogStore.shared.log("[launcher] held: \(LibraryModel.sessionsThisRun) session(s) already ran in this run of BCD")
            library.restartNotice = LibraryModel.restartMessage
            return LibraryModel.restartMessage
        }
        return nil
    }

    // MARK: - The installer

    func downloadInstaller(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        let wanted = Int64(spec.spaceGigabytes) << 30
        if let free = Self.freeBytes(), free < wanted {
            LogStore.shared.log("[launcher] \(spec.id) refused: free=\(free / (1 << 20))MB needs=\(wanted / (1 << 20))MB")
            throw BCDLauncherError.notEnoughSpace(title: spec.title, gigabytes: spec.spaceGigabytes)
        }
        let control = BCDLauncherDownloadControl()
        self.control = control
        defer { self.control = nil }
        let folder = LibraryModel.drive.appendingPathComponent(spec.installerFolder, isDirectory: true)
        do {
            _ = try await BCDLauncherInstaller.fetch(spec, into: folder, control: control,
                                                     log: { LogStore.shared.log($0) }, progress: progress)
        } catch {
            LogStore.shared.log("[launcher] \(spec.id) download failed reason=\((error as? BCDLauncherError)?.logReason ?? "other")")
            throw error
        }
    }

    func cancelDownload() {
        control?.cancel()
        BCDGeckoModel.shared.cancel()
    }

    // MARK: - Wine Gecko for installers that show a web page

    func missingComponent(_ spec: BCDLauncherSpec) -> String? {
        spec.needsGecko && !BCDGeckoModel.shared.isReady ? "the HTML engine" : nil
    }

    func downloadComponent(_ spec: BCDLauncherSpec, progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        let gecko = BCDGeckoModel.shared
        gecko.log = { LogStore.shared.log($0) }
        LogStore.shared.log("[launcher] \(spec.id) needs the HTML engine (Wine Gecko)")
        try await gecko.ensure(progress: progress)
    }

    func startInstaller(_ spec: BCDLauncherSpec) {
        let start = BCDLauncherStart.installer(spec)
        let entry = Self.entry(spec, relative: spec.installerRelativePath, title: spec.title + " setup",
                               id: UUID(uuidString: spec.setupEntryID)!, stored: nil, start: start)
        LogStore.shared.log("[launcher] \(spec.id) installer start bits=\(entry.bits) desktop=\(entry.runsInDesktop ? 1 : 0) "
                            + "services=\(entry.startServices == true ? 1 : 0) args=\(start.arguments.isEmpty ? 0 : 1)")
        BCDLiveStore.shared.startLauncherEntry(entry, game: BCDLauncherSpec.gameID(spec.id))
    }

    // MARK: - The launcher

    func startLauncher(_ spec: BCDLauncherSpec, program: String) {
        let library = LibraryModel.shared
        let ours = UUID(uuidString: spec.entryID)!
        // Our own entry, else a program the player added by hand under the same path (it keeps its own settings and id).
        let stored = library.entries.first { $0.id == ours }
            ?? library.entries.first { entry in
                entry.steamAppID == nil && entry.desktop != true
                    && spec.programs.contains { $0.caseInsensitiveCompare(entry.relativePath) == .orderedSame }
            }
        let start = BCDLauncherStart.program(spec, relative: program)
        let entry = Self.entry(spec, relative: program, title: spec.title, id: ours, stored: stored, start: start)
        LogStore.shared.log("[launcher] \(spec.id) open bits=\(entry.bits) own-entry=\(stored == nil || stored?.id == ours ? 1 : 0) "
                            + "desktop=\(entry.runsInDesktop ? 1 : 0) services=\(entry.startServices == true ? 1 : 0)")
        BCDLiveStore.shared.startLauncherEntry(entry, game: BCDLauncherSpec.gameID(spec.id))
    }

    // MARK: - The library entry of a start

    /// Whether `entry` is the one made for an installer run: it is started like any program but not kept in the library
    /// (ContentView.startLibraryEntry hands LibraryModel.begin `remember: false`).
    static func isSetup(_ entry: LibraryEntry) -> Bool {
        BCDLauncherSpec.all.contains { $0.setupEntryID.caseInsensitiveCompare(entry.id.uuidString) == .orderedSame }
    }

    /// The entry a start hands to Play: in a Wine desktop with Windows services first (BCDLauncherStart, build 594). A
    /// stored entry keeps everything the player set; BCD's own entry whose start options were never set (build 593 made
    /// such entries "on its own") gets the launcher's start options. What BCD owns besides is the program's path of its
    /// own entry, the table's arguments when it has any, and the table's block of config lines.
    static func entry(_ spec: BCDLauncherSpec, relative: String, title: String, id: UUID, stored: LibraryEntry?,
                      start: BCDLauncherStart) -> LibraryEntry {
        var entry: LibraryEntry
        if let stored {
            entry = stored
            if stored.id == id { entry.relativePath = relative }
        } else {
            let url = LibraryModel.drive.appendingPathComponent(relative)
            entry = (try? LibraryModel.inspect(url)) ?? LibraryEntry(title: title, relativePath: relative, bits: 0)
            entry.title = title
            entry.relativePath = relative
            entry.id = id
        }
        if stored == nil || (stored?.id == id && stored?.launchMode == nil && stored?.startServices == nil) {
            entry.launchMode = start.inWineDesktop ? "desktop" : nil
            entry.startServices = start.startServices ? true : nil
        }
        entry.arguments = BCDLauncherArguments.merged(entry.arguments, table: start.arguments)
        entry.config = BCDLauncherConfig.merged(entry.config, lines: start.sessionConfig, id: spec.id)
        entry.demandStartServices = start.demandStartServices.isEmpty ? nil : start.demandStartServices   // round 625: BCD's, at every start
        return entry
    }

    private static func freeBytes() -> Int64? {
        let values = try? URL.documentsDirectory.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage
    }
}
