// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315,
// app/Sources/Amazon/AmazonLibrary.swift and AmazonInstaller.swift: reading the account's list, the queue's start, pause and end) under
// GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Combine
import Foundation
import UIKit

// The experimental Amazon Games store in BCD (Porthole port, item 13): the signed-in account's games, what is installed and
// the download queue. The sign-in, the service's calls, the manifest and the work of one install are Porthole's (AmazonAuth.swift,
// AmazonAPI.swift, AmazonManifest.swift); this file is BCD's, shaped like BCD's EA store (SwiftEA/EALibrary.swift): an installed game is a
// library entry the record names (entryID), Play starts that entry as any library game, one download runs at a time and pauses for a game
// session. Off until the player turns it on (Settings › Accounts › Amazon Games; `env.BCD_AMAZON = 1`): off, nothing here is read, fetched,
// written or listened to.

@MainActor final class AmazonLibrary: ObservableObject {
    static let shared = AmazonLibrary()

    /// The experimental Amazon Games store in BCD (off by default; Settings › Accounts turns it on). Off, nothing of Amazon is read, fetched or started.
    static var enabled: Bool { MadeiraConfig.flag("BCD_AMAZON", fallback: false) }

    @Published private(set) var signedIn = false
    @Published private(set) var accountName: String?
    @Published private(set) var owned: [AmazonGame] = []
    @Published private(set) var installed: [String: AmazonInstalledGame] = [:]
    @Published private(set) var refreshing = false
    @Published var error: String?

    struct Download: Equatable {
        enum State: Equatable { case queued, active, paused, failed(String) }
        var state: State
        var progress = AmazonProgress()
    }
    @Published private(set) var downloads: [String: Download] = [:]
    private var queue: [String] = []
    private var active: (id: String, task: Task<Void, Never>)?
    private var inSession = false
    private var resumeAfterSession = Set<String>()
    private var started = false
    private var lastRefresh: Date?
    private var watchers = Set<AnyCancellable>()

    private init() { loadInstalled() }

    func game(_ id: String) -> AmazonGame? { owned.first { $0.id == id } ?? installed[id]?.game }

    // MARK: Files

    static var drive: URL { LibraryModel.drive }
    private static var support: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("BCD/Amazon", isDirectory: true)
    }
    private static var cacheURL: URL { support.appendingPathComponent("amazon-library.json") }
    private static var installedURL: URL { support.appendingPathComponent("amazon-installed.json") }
    private static var staging: URL { support.appendingPathComponent("staging", isDirectory: true) }

    private func loadInstalled() {
        guard let data = try? Data(contentsOf: Self.installedURL),
              let records = try? JSONDecoder().decode([AmazonInstalledGame].self, from: data) else { return }
        installed = Dictionary(records.map { ($0.game.id, $0) }, uniquingKeysWith: { a, _ in a })
    }

    private func writeInstalled() {
        try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)
        try? JSONEncoder().encode(installed.values.sorted { $0.game.id < $1.game.id }).write(to: Self.installedURL, options: .atomic)
    }

    // MARK: Lifecycle

    /// Called when the library shows the store; once per app run, only while it is on.
    func start() {
        guard Self.enabled, !started else { return }
        started = true
        // A game session pauses the downloads: BCD's Play starts every game through LibraryModel, whose `current` is the session.
        LibraryModel.shared.$current.receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.reconcileSession() }.store(in: &watchers)
        readAccount()
        if signedIn, let data = try? Data(contentsOf: Self.cacheURL), let cached = try? JSONDecoder().decode([AmazonGame].self, from: data) {
            owned = cached
        }
        if signedIn { refresh(interactive: false) }
    }

    private func readAccount() {
        let auth = AmazonAuth.shared
        signedIn = auth.signedIn
        accountName = auth.accountName
    }

    /// The code from Amazon's sign-in page (BCDLiveStoreAmazon's sheet).
    func signIn(code: String) async throws {
        try await AmazonAuth.shared.finishLogin(code: code)
        readAccount()
        owned = []
        lastRefresh = nil
        refresh(interactive: true)
    }

    /// Removes the tokens and the cached list. Installed games stay and still start: Amazon's games need no sign-in to run here.
    func signOut() {
        for id in Array(downloads.keys) { pause(id) }
        AmazonAuth.shared.signOut()
        try? FileManager.default.removeItem(at: Self.cacheURL)
        readAccount()
        owned = []
        lastRefresh = nil
    }

    /// The account's games read again (at most every ten minutes unless asked for).
    func refresh(interactive: Bool) {
        guard Self.enabled, signedIn, !refreshing else { return }
        if !interactive, let last = lastRefresh, Date().timeIntervalSince(last) < 600 { return }
        refreshing = true
        lastRefresh = Date()
        Task {
            defer { refreshing = false }
            do {
                let (token, serial) = try await AmazonAuth.shared.session()
                let raw = try await AmazonAPI.entitlementPages(token: token, serial: serial)
                let (list, summary) = AmazonAPI.games(from: raw)
                LogStore.shared.log("[amazon] \(summary)")
                try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)
                try? JSONEncoder().encode(list).write(to: Self.cacheURL, options: .atomic)
                owned = list
            } catch {
                readAccount()   // a refresh token Amazon no longer honours signs the account out
                LogStore.shared.log("[amazon] the list was not read: \(error.localizedDescription)", level: .error)
                if interactive { self.error = error.localizedDescription }
            }
        }
    }

    // MARK: Downloads

    func install(_ id: String) {
        guard signedIn else { error = "Sign in to Amazon Games in Settings › Accounts to download games."; return }
        guard let game = game(id) else { return }
        if let current = downloads[id], current.state == .active || current.state == .queued { return }
        downloads[id] = Download(state: .queued, progress: downloads[id]?.progress ?? AmazonProgress())
        if !queue.contains(id) { queue.append(id) }
        LogStore.shared.log("[amazon] queued \(game.title)")
        pump()
    }

    /// Stops a download; files already checked stay, and Resume goes on from them.
    func pause(_ id: String) {
        if let current = active, current.id == id {
            current.task.cancel()
        } else if let index = queue.firstIndex(of: id) {
            queue.remove(at: index)
            downloads[id]?.state = .paused
        }
        resumeAfterSession.remove(id)
    }

    /// Stops a first install and removes its files; an installed game is left alone.
    func cancel(_ id: String) {
        pause(id)
        downloads[id] = nil
        guard installed[id] == nil, let game = game(id) else { return }
        removeFiles(folder: AmazonInstall.folderName(for: game))
        LogStore.shared.log("[amazon] cancelled \(game.title)")
    }

    /// Deletes the game's files and record; its library entry goes with them.
    func uninstall(_ id: String) {
        pause(id)
        downloads[id] = nil
        guard let record = installed[id] else { return }
        removeFiles(folder: AmazonInstall.folderName(for: record.game))
        installed[id] = nil
        writeInstalled()
        if let entryID = record.entryID { LibraryModel.shared.remove(entryID) }
        LogStore.shared.log("[amazon] uninstalled \(record.game.title)")
    }

    private func removeFiles(folder: String) {
        guard !folder.isEmpty, !folder.contains("/"), !folder.contains("..") else { return }
        try? FileManager.default.removeItem(at: Self.staging.appendingPathComponent(folder, isDirectory: true))
        try? FileManager.default.removeItem(at: Self.drive.appendingPathComponent("Amazon/" + folder, isDirectory: true))
    }

    private func pump() {
        guard active == nil, !inSession, !queue.isEmpty else { return }
        let id = queue.removeFirst()
        guard let game = game(id) else { downloads[id] = nil; pump(); return }
        downloads[id]?.state = .active
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.run(game)
        }
        active = (id, task)
    }

    private func run(_ game: AmazonGame) async {
        let id = game.id
        do {
            let record = try await AmazonInstall.download(game, drive: Self.drive, staging: Self.staging, session: {
                try await AmazonAuth.shared.session().token
            }, progress: { progress in
                await MainActor.run { AmazonLibrary.shared.downloads[id]?.progress = progress }
            })
            try Task.checkCancellation()
            var finished = record
            finished.entryID = installed[id]?.entryID
            installed[id] = finished
            writeInstalled()
            downloads[id] = nil
            await makeEntry(id)
            LogStore.shared.log("[amazon] installed \(game.title)")
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled {
                if downloads[id] != nil { downloads[id]?.state = .paused }
                LogStore.shared.log("[amazon] paused \(game.title)")
            } else {
                downloads[id]?.state = .failed(error.localizedDescription)
                LogStore.shared.log("[amazon] \(game.title) failed: \(error.localizedDescription)", level: .error)
            }
        }
        active = nil
        pump()
    }

    // MARK: The library entry and Play

    /// The installed game as a library entry, made at the first install and followed at a reinstall: an existing entry keeps its title, settings and cover.
    private func entry(for record: AmazonInstalledGame, inspected: LibraryEntry?) -> LibraryEntry {
        let existing = record.entryID.flatMap { entryID in LibraryModel.shared.entries.first { $0.id == entryID } }
        var entry = existing ?? LibraryEntry(title: record.game.title, relativePath: record.programPath, bits: 0)
        entry.relativePath = record.programPath
        entry.folderBytes = record.installBytes
        if existing == nil {
            entry.arguments = record.arguments
            entry.workingDirectory = record.workDir
        }
        if let inspected {
            entry.bits = inspected.bits
            entry.graphicsAPI = inspected.graphicsAPI ?? entry.graphicsAPI
        }
        entry.metadataChecked = nil
        return entry
    }

    private func commit(_ entry: LibraryEntry, to id: String) {
        LibraryModel.shared.save(entry)
        guard var record = installed[id], record.entryID != entry.id else { return }
        record.entryID = entry.id
        installed[id] = record
        writeInstalled()
    }

    private func makeEntry(_ id: String) async {
        guard let record = installed[id] else { return }
        let program = Self.drive.appendingPathComponent(record.programPath)
        let inspected = await Task.detached(priority: .utility, operation: { try? LibraryModel.inspect(program) }).value
        guard let current = installed[id] else { return }
        commit(entry(for: current, inspected: inspected), to: id)
    }

    struct PlayRefusal: Error { var message: String }

    /// The library entry Play starts, made again if the library lost it.
    func playEntry(_ id: String) -> Result<LibraryEntry, PlayRefusal> {
        guard let record = installed[id] else { return .failure(PlayRefusal(message: "This Amazon game isn't installed.")) }
        if downloads[id] != nil {
            return .failure(PlayRefusal(message: "This game's download has not finished. Resume it and wait for it to complete before playing."))
        }
        var entry = record.entryID.flatMap { entryID in LibraryModel.shared.entries.first { $0.id == entryID } }
        if entry == nil {
            let made = self.entry(for: record, inspected: try? LibraryModel.inspect(Self.drive.appendingPathComponent(record.programPath)))
            commit(made, to: id)
            entry = LibraryModel.shared.entries.first { $0.id == made.id }
        }
        guard let entry else {
            return .failure(PlayRefusal(message: "BCD couldn't add this game to the library. Its folder is " + record.windowsInstallPath + "."))
        }
        return .success(entry)
    }

    // MARK: Sessions

    /// A game session is starting or ended: downloads pause for it and continue afterwards. Idempotent.
    func reconcileSession() {
        if !Self.enabled { switchedOff(); return }
        let running = LibraryModel.shared.current != nil
        guard running != inSession else { return }
        inSession = running
        if running {
            if let current = active { resumeAfterSession.insert(current.id); current.task.cancel() }
            for id in queue { resumeAfterSession.insert(id); downloads[id]?.state = .paused }
            queue.removeAll()
        } else {
            let resume = resumeAfterSession.sorted()
            resumeAfterSession.removeAll()
            for id in resume { install(id) }
        }
    }

    /// The player turned the store off: downloads pause. Installed games and the sign-in stay.
    func switchedOff() {
        for id in Array(downloads.keys) { pause(id) }
    }
}
