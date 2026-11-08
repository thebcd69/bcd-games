// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 ft7t8drgwf-wq
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Modified by BCD (thebcd69), 2026-11-05: the ItchLibrary class of Madeira's ItchLibrary.swift, kept as it was except where BCD's library, config and
// files differ: the switch is env.BCD_ITCH, the files live under Application Support/BCD/itch, an install's library entry is made and removed through
// the record (entryID) instead of a LibraryEntry field, Play reads the record (playEntry), downloads pause for a game session through
// LibraryModel.current (and pause when env.BCD_ITCH = 0), the archive folder is kept out of device backups, and BCD's words in the messages. The pieces above the class (records, installer kinds, program finder, ItchTransfer) are in
// ItchInstall.swift.

import Combine
import CryptoKit
import Foundation
import UIKit

// The itch.io account's games and their downloads (docs/ITCH.md).
//
// itch.io decides ownership: the list is the account's owned keys (bought and claimed
// games), the games in its collections and the free games added in BCD (Find a
// game), and a paid game's files are only served for its download key. A game is one
// upload, its Windows file: a .zip unpacks into C:\itch\<folder>, a lone .exe goes
// there as it is, and the game starts as its own program in Wine (the folder's
// .itch.toml action, else its one program). An installer (ItchSetup) runs once in the
// Wine desktop, into that folder; then Play starts the game it installed. itch.io
// games are DRM-free and need no sign-in to start. env.BCD_ITCH = 0 turns the
// whole thing off.
// Log tags: [itch-library], [itch-download] (game ids, counts and short reason codes;
// never the key or account data).

@MainActor final class ItchLibrary: ObservableObject {
    static let shared = ItchLibrary()

    /// itch.io in BCD (on by default; nothing is fetched before an API key is entered). 0 hides it everywhere.
    static var enabled: Bool { MadeiraConfig.flag("BCD_ITCH", fallback: true) }
    /// madeira-bcd: itch.io is switched on and the player has begun with it (signed in once, or a game is installed): BCD's library, its
    /// hooks and its sign-in state read ItchLibrary only then, so a player who never uses itch.io costs it nothing (no Keychain read, no
    /// file, no network). Signing in makes the folder, so this is true from then on, also after a sign-out.
    static var active: Bool { enabled && FileManager.default.fileExists(atPath: support.path) }

    @Published private(set) var signedIn = false
    @Published private(set) var accountName: String?
    /// The library: owned keys, then the account's collections, then the games added
    /// in BCD; a game appears once, with its key when the account has one.
    @Published private(set) var owned: [ItchOwnedGame] = []
    /// The free games added in BCD (Find a game).
    @Published private(set) var added: [ItchOwnedGame] = []
    @Published private(set) var installed: [Int: ItchInstalledGame] = [:]
    @Published private(set) var refreshing = false
    @Published private(set) var libraryUpdated: Date?
    /// The upload BCD would install now, per game (its sheet and update check), or why there is none.
    enum Choice: Equatable { case upload(ItchUpload), unavailable(String) }
    @Published private(set) var choices: [Int: Choice] = [:]
    @Published var error: String?

    struct Download: Equatable {
        enum Kind: String { case install, update }
        enum State: Equatable { case queued, active, paused, failed(String) }
        var kind: Kind
        var state: State
        var progress = ItchDownloadProgress()
    }
    @Published private(set) var downloads: [Int: Download] = [:]
    private var queue: [Int] = []
    private var active: (id: Int, task: Task<Void, Never>)?
    private var inSession = false
    private var resumeAfterSession = Set<Int>()
    private var resumeAfterBackground = Set<Int>()
    private var started = false
    private var watchers = Set<AnyCancellable>()   // madeira-bcd
    private var timer: Timer?
    private var grace: UIBackgroundTaskIdentifier = .invalid
    private let api = ItchAPI.shared

    private init() { loadInstalled() }

    var hasActiveDownload: Bool { downloads.values.contains { $0.state == .active || $0.state == .queued } }
    func game(_ id: Int) -> ItchOwnedGame? { owned.first { $0.id == id } }

    // MARK: Files

    static var drive: URL { LibraryModel.drive }
    static let installRoot = "itch"
    /// A file or folder name safe on Windows and iOS, at most 120 characters.
    nonisolated static func safeName(_ text: String) -> String {
        let banned = CharacterSet(charactersIn: "/\\:*?\"<>|").union(.controlCharacters)
        let cleaned = String(text.unicodeScalars.map { banned.contains($0) ? "_" : Character($0) })
            .trimmingCharacters(in: CharacterSet(charactersIn: ". ").union(.whitespaces))
        return cleaned.isEmpty ? "game" : String(cleaned.prefix(120))
    }
    /// Free bytes on the volume of `url`, or nil when the volume does not say. Some
    /// volumes report zero for the important-usage estimate although they have free
    /// blocks, so the plain capacity is the fallback.
    nonisolated static func freeSpace(at url: URL) -> Int64? {
        guard let values = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey])
        else { return nil }
        let important = values.volumeAvailableCapacityForImportantUsage, plain = values.volumeAvailableCapacity.map(Int64.init)
        if important == nil && plain == nil { return nil }
        return max(important ?? 0, plain ?? 0)
    }
    private static var support: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCD/itch", isDirectory: true)   // madeira-bcd: BCD's own folder (BCDGOG keeps BCD/GOG)
    }
    private static var cacheURL: URL { support.appendingPathComponent("itch-library.json") }
    private static var installedURL: URL { support.appendingPathComponent("itch-installed.json") }
    /// The free games added in BCD (Find a game), per account.
    private static var addedURL: URL { support.appendingPathComponent("itch-added.json") }
    private struct Added: Codable { var account: String; var games: [ItchOwnedGame] }
    /// Downloads in progress: the file, and what a pause kept for resuming it.
    private static var downloadsFolder: URL { support.appendingPathComponent("itch-downloads", isDirectory: true) }
    private static func archiveURL(_ id: Int, upload: ItchUpload) -> URL {
        let ext = (upload.filename as NSString).pathExtension.lowercased()
        return downloadsFolder.appendingPathComponent("\(id)-\(upload.id)" + (ext.isEmpty ? "" : "." + ext))
    }
    private static func resumeURL(_ id: Int) -> URL { downloadsFolder.appendingPathComponent("\(id).resume") }
    private struct Cache: Codable { var version: Int; var updated: Date; var account: String; var games: [ItchOwnedGame] }
    private static func accountKey(_ name: String) -> String {
        SHA256.hash(data: Data(name.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    // MARK: Lifecycle

    /// Called when the library shows the itch.io section; once per app run.
    func start() {
        guard Self.enabled, !started else { return }
        started = true
        finishSetups()
        // madeira-bcd: a game session pauses the downloads and ends the installers' follow-up without ContentView calling sessionChanged: BCD's Play
        // starts every game through LibraryModel, whose `current` is the session (the content queue of GOG and Epic watches it the same way).
        LibraryModel.shared.$current.receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.reconcileSession() }.store(in: &watchers)
        NotificationCenter.default.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.beginGrace() }
        }
        NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.endGrace()
                self.resumeFromBackground()
                self.reconcileSession()
            }
        }
        Task {
            let name = await api.accountName()
            signedIn = await api.signedIn
            accountName = name
            if let name { loadCache(account: name); loadAdded(account: name) }
            ItchLog.event("[itch-library] start signed-in=\(signedIn ? 1 : 0) cached=\(owned.count) installed=\(installed.count)")
            if signedIn, Date().timeIntervalSince(libraryUpdated ?? .distantPast) > 6 * 3600 {
                await refreshLibrary(interactive: false)
            }
        }
    }

    private func loadCache(account: String) {
        guard let data = try? Data(contentsOf: Self.cacheURL), data.count <= 32 << 20,
              let cache = try? JSONDecoder().decode(Cache.self, from: data), cache.version == 1,
              cache.account == Self.accountKey(account) else { return }
        owned = cache.games; libraryUpdated = cache.updated
    }

    private func writeCache() {
        guard let updated = libraryUpdated, let accountName else { return }
        try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)
        try? JSONEncoder().encode(Cache(version: 1, updated: updated, account: Self.accountKey(accountName), games: owned))
            .write(to: Self.cacheURL, options: .atomic)
    }

    private func loadAdded(account: String) {
        guard let data = try? Data(contentsOf: Self.addedURL), data.count <= 8 << 20,
              let list = try? JSONDecoder().decode(Added.self, from: data), list.account == Self.accountKey(account) else { added = []; return }
        added = list.games
        owned = Self.merge(owned, added)
    }

    private func writeAdded() {
        guard let accountName else { return }
        try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)
        try? JSONEncoder().encode(Added(account: Self.accountKey(accountName), games: added)).write(to: Self.addedURL, options: .atomic)
    }

    /// Lists in order of precedence; a game keeps its first appearance (an owned key first).
    static func merge(_ lists: [ItchOwnedGame]...) -> [ItchOwnedGame] {
        var seen = Set<Int>()
        return lists.joined().filter { seen.insert($0.id).inserted }
            .sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
    }

    private func loadInstalled() {
        guard let data = try? Data(contentsOf: Self.installedURL),
              let records = try? JSONDecoder().decode([Int: ItchInstalledGame].self, from: data) else { return }
        // A record whose program is gone (the folder was deleted in Files) is dropped.
        installed = records.filter { FileManager.default.fileExists(atPath: Self.drive.appendingPathComponent($0.value.programPath).path) }
    }

    private func writeInstalled() {
        try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try? encoder.encode(installed).write(to: Self.installedURL, options: .atomic)
    }

    // MARK: Account

    /// An API key from itch.io's Settings › API keys (ItchSettingsSection).
    func signIn(key: String) async throws {
        let name = try await api.signIn(key: key)
        try? FileManager.default.createDirectory(at: Self.support, withIntermediateDirectories: true)   // madeira-bcd: marks itch.io as begun (`active`)
        signedIn = true
        accountName = name
        owned = []; libraryUpdated = nil; choices = [:]
        loadAdded(account: name)
        ItchLog.event("[itch-library] signed in")
        await refreshLibrary(interactive: true)
    }

    /// Removes the key and the cached list. Installed games stay and still start:
    /// itch.io games need no sign-in to play, only to download and update.
    func signOut() {
        for id in Array(downloads.keys) { pause(id) }
        Task { await api.signOut() }
        try? FileManager.default.removeItem(at: Self.cacheURL)
        signedIn = false; accountName = nil; owned = []; added = []; libraryUpdated = nil; choices = [:]
        ItchLog.event("[itch-library] signed out")
    }

    private func handle(_ error: Error, context: String, report: Bool) {
        if case ItchAPIError.signedOut = error { signedIn = false; accountName = nil }
        let reason = (error as? ItchAPIError)?.reason ?? (error as? ItchArchiveError)?.reason ?? (error as? ItchInstallError)?.reason
            ?? (error as? URLError).map { "url-\($0.code.rawValue)" } ?? String(describing: type(of: error))
        ItchLog.event("[itch-library] \(context) failed reason=\(reason)")
        if report { self.error = error.localizedDescription }
    }

    // MARK: Library

    func refreshLibrary(interactive: Bool = true) async {
        guard Self.enabled, signedIn, !refreshing else { return }
        refreshing = true
        defer { refreshing = false }
        do {
            let bought = try await api.ownedGames()
            // Collections are extra: one that fails leaves the owned games.
            var collected: [ItchOwnedGame] = []
            do { collected = try await api.collectionGames() } catch {
                handle(error, context: "collections", report: false)
            }
            owned = Self.merge(bought, collected, added)
            libraryUpdated = Date()
            writeCache()
            ItchLog.event("[itch-library] owned games=\(bought.count) collections=\(collected.count) added=\(added.count) shown=\(owned.count)")
        } catch {
            handle(error, context: "library", report: interactive)
        }
    }

    // MARK: Finding games

    /// itch.io's search, for Find a game.
    func search(_ query: String) async throws -> [ItchOwnedGame] { try await api.search(query) }

    /// The game a pasted itch.io link names.
    func lookUp(link: String) async throws -> ItchOwnedGame { try await api.game(link: link) }

    /// Whether a pasted text is an itch.io game link.
    nonisolated static func isLink(_ text: String) -> Bool { ItchFormat.dataURL(text) != nil && text.contains(".itch.io") }

    /// Adds a game found in BCD to the library (this device's list for the account).
    func add(_ game: ItchOwnedGame) {
        guard !owned.contains(where: { $0.id == game.id }) else { return }
        added.append(game)
        writeAdded()
        owned = Self.merge(owned, [game])
        writeCache()
        ItchLog.event("[itch-library] added id=\(game.id) free=\((game.minPrice ?? 0) == 0 ? 1 : 0)")
    }

    /// Takes an added game off the list (one that is not installed).
    func removeAdded(_ id: Int) {
        guard installed[id] == nil, added.contains(where: { $0.id == id }) else { return }
        added.removeAll { $0.id == id }
        writeAdded()
        owned.removeAll { $0.id == id && $0.downloadKeyID == 0 }
        writeCache()
    }

    /// Looks up the upload BCD would install (the download sheet, the update check).
    func loadChoice(_ id: Int, force: Bool = false) async {
        guard signedIn, force || choices[id] == nil, let game = self.game(id) else { return }
        do {
            let uploads = try await api.uploads(game: id, downloadKey: game.downloadKeyID)
            if let upload = ItchFormat.choose(uploads) { choices[id] = .upload(upload) }
            else { choices[id] = .unavailable(ItchAPIError.noWindowsUpload.localizedDescription) }
        } catch {
            let reason = (error as? ItchAPIError)?.reason ?? (error as? URLError).map { "url-\($0.code.rawValue)" } ?? "error"
            ItchLog.event("[itch-library] uploads id=\(id) failed reason=\(reason)")
            if error is ItchAPIError { choices[id] = .unavailable(error.localizedDescription) }
        }
    }

    func updateAvailable(_ id: Int) -> Bool {
        guard let record = installed[id], case .upload(let upload)? = choices[id] else { return false }
        return upload.id != record.uploadID || upload.buildID != record.buildID || (!upload.version.isEmpty && upload.version != record.version)
    }

    func hasPartialDownload(_ id: Int) -> Bool { FileManager.default.fileExists(atPath: Self.resumeURL(id).path) }

    // MARK: Downloads

    func install(_ id: Int) { enqueue(id, kind: installed[id] == nil ? .install : .update) }

    private func enqueue(_ id: Int, kind: Download.Kind) {
        guard Self.enabled else { return }
        guard signedIn else { error = "Sign in to itch.io to download games."; return }
        if active?.id == id || queue.contains(id) { return }
        if inSession { resumeAfterSession.insert(id); downloads[id] = Download(kind: kind, state: .paused); return }
        var item = downloads[id] ?? Download(kind: kind, state: .queued)
        item.state = .queued
        downloads[id] = item
        queue.append(id)
        startTimer()
        pump()
    }

    func pause(_ id: Int) {
        if let current = active, current.id == id {
            current.task.cancel()
        } else if let index = queue.firstIndex(of: id) {
            queue.remove(at: index)
            downloads[id]?.state = .paused
        }
        resumeAfterSession.remove(id)
    }

    /// Stops a download and deletes what it fetched; an installed game stays as it is.
    func cancel(_ id: Int) {
        let running = active?.id == id ? active?.task : nil
        pause(id)
        downloads[id] = nil
        Task {
            await running?.value
            let fm = FileManager.default
            try? fm.removeItem(at: Self.resumeURL(id))
            for name in (try? fm.contentsOfDirectory(atPath: Self.downloadsFolder.path)) ?? [] where name.hasPrefix("\(id)-") {
                try? fm.removeItem(at: Self.downloadsFolder.appendingPathComponent(name))
            }
            ItchLog.event("[itch-download] cancelled id=\(id) partial-files-removed=1")
        }
    }

    /// Deletes the game's files and its library entry (its per-game settings).
    func uninstall(_ id: Int) {
        guard let record = installed[id], !inSession else { return }
        pause(id); downloads[id] = nil
        installed[id] = nil
        writeInstalled()
        if let entryID = record.entryID { LibraryModel.shared.remove(entryID) }   // madeira-bcd: the record names its library entry
        let folder = Self.drive.appendingPathComponent(record.folder, isDirectory: true)
        Task.detached(priority: .utility) { try? FileManager.default.removeItem(at: folder) }
        ItchLog.event("[itch-download] uninstalled id=\(id)")
    }

    private func pump() {
        guard active == nil, !inSession, !queue.isEmpty else { return }
        let id = queue.removeFirst()
        downloads[id]?.state = .active
        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.run(id)
        }
        active = (id, task)
    }

    private func run(_ id: Int) async {
        let kind = downloads[id]?.kind ?? .install
        do {
            guard let game = self.game(id) else { throw ItchAPIError.invalid("unknown-game") }
            downloads[id]?.progress = ItchDownloadProgress(phase: .preparing)
            await loadChoice(id, force: true)
            guard case .upload(let upload)? = choices[id] else { throw ItchAPIError.noWindowsUpload }
            let name = upload.filename.lowercased()
            let isZip = name.hasSuffix(".zip") || name.hasSuffix(".7z"), isExe = name.hasSuffix(".exe") || name.hasSuffix(".msi")
            // A butler build's download is a .zip of the build whatever the upload's name.
            guard isZip || isExe || upload.buildID != 0 else {
                throw ItchInstallError.unsupportedFile("." + ((upload.filename as NSString).pathExtension.isEmpty ? "unknown" : (upload.filename as NSString).pathExtension))
            }
            let record = installed[id]
            let folder = record?.folder ?? (Self.installRoot + "/" + Self.safeName(game.title))
            let target = Self.drive.appendingPathComponent(folder, isDirectory: true)
            try FileManager.default.createDirectory(at: Self.downloadsFolder, withIntermediateDirectories: true)
            // madeira-bcd: a game's archive is gigabytes and is deleted once unpacked: it never belongs in a device backup.
            var archives = Self.downloadsFolder
            var excluded = URLResourceValues(); excluded.isExcludedFromBackup = true
            try? archives.setResourceValues(excluded)
            if let free = Self.freeSpace(at: URL.documentsDirectory), upload.size > 0 {
                // The archive, and about as much again unpacked.
                let needed = upload.size * (isExe ? 1 : 3) - free
                if needed > 0 { throw ItchInstallError.diskSpace(needed: needed) }
            }
            ItchLog.event("[itch-download] start id=\(id) kind=\(kind.rawValue) upload=\(upload.id) build=\(upload.buildID) bytes=\(upload.size)")
            let archive = Self.archiveURL(id, upload: upload)
            if !FileManager.default.fileExists(atPath: archive.path) {
                let request = try await api.downloadRequest(upload: upload.id, downloadKey: game.downloadKeyID)
                downloads[id]?.progress = ItchDownloadProgress(phase: .downloading, totalBytes: UInt64(max(0, upload.size)))
                let started = Date()
                let transfer = ItchTransfer(destination: archive, resumeFile: Self.resumeURL(id)) { done, total in
                    Task { @MainActor in
                        guard var item = ItchLibrary.shared.downloads[id] else { return }
                        item.progress.phase = .downloading
                        item.progress.doneBytes = done
                        if total > 0 { item.progress.totalBytes = total }
                        let elapsed = Date().timeIntervalSince(started)
                        if elapsed > 1 { item.progress.bytesPerSecond = Double(done) / elapsed }
                        ItchLibrary.shared.downloads[id] = item
                    }
                }
                try await transfer.run(request)
            }
            try Task.checkCancellation()
            downloads[id]?.progress = ItchDownloadProgress(phase: .unpacking)
            let title = game.title
            let wasSetup = record?.setup != nil
            let installBytes: Int64 = try await Task.detached(priority: .userInitiated) {
                let fm = FileManager.default
                try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
                let report: (UInt64, UInt64) -> Void = { done, total in
                    Task { @MainActor in
                        ItchLibrary.shared.downloads[id]?.progress = ItchDownloadProgress(phase: .unpacking, totalBytes: total, doneBytes: done)
                    }
                }
                if var bytes = try ItchArchive.unpackAny(archive, to: target, progress: report) {
                    try? fm.removeItem(at: archive)
                    // A download that only holds the game's real archive (a .7z beside a
                    // README, as some itch.io builds are): unpack that too.
                    if ItchProgram.find(in: target, title: title) == nil, ItchSetup.installer(in: target) == nil {
                        let inner = try ItchArchive.unpackInner(in: target, progress: report)
                        if inner.rar { throw ItchInstallError.innerRar }
                        if inner.unpacked > 0 || inner.removed > 0 {
                            ItchLog.event("[itch-download] inner-archive id=\(id) bytes=\(inner.unpacked)")
                        }
                        bytes = bytes - min(bytes, inner.removed) + inner.unpacked
                    }
                    return Int64(bytes)
                }
                // A lone program: the game itself, or its installer (ItchSetup), kept in the
                // hidden setup folder so that the game it installs is the folder's program.
                guard isExe else { throw ItchArchiveError.notZip }
                let file = Self.safeName(upload.filename)
                let setup = ItchSetup.kind(of: archive, fileName: file) != nil
                // An update of a game that was installed by its installer replaces only the
                // installer: the installer updates the folder itself.
                let keepGame = setup && record != nil && !wasSetup
                if fm.fileExists(atPath: target.path) && !keepGame { try fm.removeItem(at: target) }
                let place = setup ? target.appendingPathComponent(ItchSetup.folderName, isDirectory: true) : target
                if setup, fm.fileExists(atPath: place.path) { try fm.removeItem(at: place) }
                try fm.createDirectory(at: place, withIntermediateDirectories: true)
                let program = place.appendingPathComponent(file)
                try fm.moveItem(at: archive, to: program)
                return (try? fm.attributesOfItem(atPath: program.path)[.size] as? Int64) ?? upload.size
            }.value
            var finished = ItchInstalledGame(gameID: id, title: title, folder: folder, uploadID: upload.id, buildID: upload.buildID,
                                             version: upload.version, launchExe: "", arguments: "", installBytes: installBytes)
            // A downloaded installer waits in the hidden setup folder (it runs even when an
            // update finds the game it installed before); else the folder's program; else
            // the archive's one installer.
            let downloadedSetup = FileManager.default.fileExists(atPath: target.appendingPathComponent(ItchSetup.folderName).path)
            let program = downloadedSetup ? nil : ItchProgram.find(in: target, title: title)
            if let program {
                finished.launchExe = program
            } else if let found = ItchSetup.installer(in: target) {
                let (kind, installer) = found
                // An installer (alone, or the only one in the archive): Play runs it once.
                let script = target.appendingPathComponent(ItchSetup.folderName + "/" + ItchSetup.scriptName)
                try FileManager.default.createDirectory(at: script.deletingLastPathComponent(), withIntermediateDirectories: true)
                try Data(ItchSetup.script(kind: kind, installer: ItchSetup.windowsPath(folder + "/" + installer),
                                          target: ItchSetup.windowsPath(folder), title: title).utf8).write(to: script, options: .atomic)
                finished.launchExe = ItchSetup.folderName + "/" + ItchSetup.scriptName
                finished.setup = kind
                finished.setupSince = Date()
                ItchLog.event("[itch-setup] ready id=\(id) kind=\(kind.rawValue)")
            } else {
                throw ItchInstallError.noProgram
            }
            finished.entryID = record?.entryID   // madeira-bcd: an update follows the library entry the first install made
            finished.cover = game.cover
            installed[id] = finished
            writeInstalled()
            downloads[id] = nil
            await makeEntry(id)
            ItchLog.event("[itch-download] done id=\(id) bytes=\(installBytes)")
        } catch {
            if Task.isCancelled || error is CancellationError || (error as? URLError)?.code == .cancelled {
                if downloads[id] != nil { downloads[id]?.state = .paused }
                ItchLog.event("[itch-download] paused id=\(id)")
            } else {
                downloads[id]?.state = .failed(error.localizedDescription)
                handle(error, context: "download id=\(id)", report: false)
            }
        }
        active = nil
        pump()
    }

    // MARK: The library entry (madeira-bcd)

    /// The installed game as a library entry, made at the first install and followed at an update: an existing entry keeps its title, its
    /// settings and its cover. While the program is an installer (ItchSetup) the entry starts in the Wine desktop with Windows services
    /// first; once the installer has installed the game, it starts directly again (`setupFinished`). The record names the entry (entryID).
    private func entry(for game: ItchInstalledGame, setupFinished: Bool, inspected: LibraryEntry?) -> LibraryEntry {
        var entry = game.entryID.flatMap { entryID in LibraryModel.shared.entries.first { $0.id == entryID } }
            ?? LibraryEntry(title: game.title, relativePath: game.programPath, bits: 0)
        entry.relativePath = game.programPath
        if game.setup != nil {
            entry.launchMode = "desktop"; entry.startServices = true
        } else if setupFinished {
            entry.launchMode = nil; entry.startServices = nil
        }
        entry.folderBytes = game.installBytes
        if let inspected {
            entry.bits = inspected.bits
            entry.graphicsAPI = inspected.graphicsAPI ?? entry.graphicsAPI
        }
        entry.metadataChecked = nil
        return entry
    }

    /// Saves the entry and writes its ID on the record.
    private func commit(_ entry: LibraryEntry, to id: Int) {
        LibraryModel.shared.save(entry)
        guard var game = installed[id], game.entryID != entry.id else { return }
        game.entryID = entry.id
        installed[id] = game
        writeInstalled()
    }

    private func makeEntry(_ id: Int, setupFinished: Bool = false) async {
        guard let game = installed[id] else { return }
        let program = Self.drive.appendingPathComponent(game.programPath)
        let inspected = await Task.detached(priority: .utility, operation: { try? LibraryModel.inspect(program) }).value
        guard let current = installed[id] else { return }   // uninstalled while the program was read
        commit(entry(for: current, setupFinished: setupFinished, inspected: inspected), to: id)
    }

    /// Why Play cannot start an itch.io game, in words for the player.
    struct PlayRefusal: Error { var message: String }

    /// The library entry Play starts, made again if the library lost it. Not while the game has a download (its files are still coming).
    func playEntry(_ id: Int) -> Result<LibraryEntry, PlayRefusal> {
        guard let record = installed[id] else { return .failure(PlayRefusal(message: "This itch.io game isn't installed.")) }
        if downloads[id] != nil {   // as Madeira's Play: a game starts once its files are complete (an update waiting or failed counts)
            return .failure(PlayRefusal(message: "This game's download has not finished. Resume it and wait for it to complete before playing."))
        }
        var entry = record.entryID.flatMap { entryID in LibraryModel.shared.entries.first { $0.id == entryID } }
        if entry == nil {
            let made = self.entry(for: record, setupFinished: false,
                                  inspected: try? LibraryModel.inspect(Self.drive.appendingPathComponent(record.programPath)))
            commit(made, to: id)
            entry = LibraryModel.shared.entries.first { $0.id == made.id }
        }
        guard let entry else {
            return .failure(PlayRefusal(message: "BCD couldn't add this game to the library. Its folder is C:\\" + record.folder.replacingOccurrences(of: "/", with: "\\") + "."))
        }
        ItchLog.event("[itch-library] play id=\(id) installer=\(record.setup == nil ? 0 : 1)")
        return .success(entry)
    }

    // MARK: Installers

    /// Installers that ran: the game they installed becomes the program (its own folder,
    /// or the one the installer chose), and the installer goes. One that has not run yet,
    /// or found nothing, stays for Play to run again. After every session and at start.
    func finishSetups() {
        guard !inSession else { return }
        for (id, record) in installed where record.setup != nil {
            let target = Self.drive.appendingPathComponent(record.folder, isDirectory: true)
            // Only once the installer has run (an update's installer finds the old game there).
            guard FileManager.default.fileExists(atPath: target.appendingPathComponent(ItchSetup.folderName + "/" + ItchSetup.ranName).path) else { continue }
            var folder = record.folder
            var program = ItchProgram.find(in: target, title: record.title)
            if program == nil, let since = record.setupSince,
               let other = ItchSetup.installedElsewhere(drive: Self.drive, title: record.title, since: since) {
                program = ItchProgram.find(in: Self.drive.appendingPathComponent(other, isDirectory: true), title: record.title)
                if program != nil { folder = other }
            }
            guard let program else {
                // It ran and installed nothing BCD can find: Play runs it again.
                try? FileManager.default.removeItem(at: target.appendingPathComponent(ItchSetup.folderName + "/" + ItchSetup.ranName))
                ItchLog.event("[itch-setup] nothing installed id=\(id)")
                continue
            }
            let fm = FileManager.default
            try? fm.removeItem(at: target.appendingPathComponent(ItchSetup.folderName, isDirectory: true))
            if folder != record.folder, ((try? fm.contentsOfDirectory(atPath: target.path)) ?? []).isEmpty { try? fm.removeItem(at: target) }
            var done = record
            done.folder = folder
            done.launchExe = program
            done.setup = nil
            done.setupSince = nil
            installed[id] = done
            writeInstalled()
            Task { await makeEntry(id, setupFinished: true) }   // madeira-bcd
            ItchLog.event("[itch-setup] installed id=\(id) kind=\(record.setup?.rawValue ?? "") own-folder=\(folder == record.folder ? 1 : 0)")
        }
    }

    // MARK: Sessions and the background

    /// A game session is starting or ended: downloads pause for it and continue afterwards. Idempotent.
    func sessionChanged(active running: Bool) {
        guard running != inSession else { return }
        inSession = running
        if running {
            if let current = active { resumeAfterSession.insert(current.id); current.task.cancel() }
            for id in queue { resumeAfterSession.insert(id); downloads[id]?.state = .paused }
            queue.removeAll()
        } else {
            finishSetups()
            let resume = resumeAfterSession.sorted()
            resumeAfterSession.removeAll()
            for id in resume { enqueue(id, kind: downloads[id]?.kind ?? .install) }
        }
    }

    var sessionRunning: Bool {
        LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0
    }

    func reconcileSession() {
        // madeira-bcd: itch.io switched off (env.BCD_ITCH = 0) while a download runs: the downloads pause here, the rest of BCD no longer reads it.
        if !Self.enabled { for id in Array(downloads.keys) { pause(id) }; return }
        sessionChanged(active: sessionRunning)
    }

    private func startTimer() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.reconcileSession()
                if !self.hasActiveDownload { self.timer?.invalidate(); self.timer = nil }
            }
        }
    }

    private func beginGrace() {
        guard hasActiveDownload, grace == .invalid else { return }
        grace = UIApplication.shared.beginBackgroundTask(withName: "itch.io download") { [weak self] in
            MainActor.assumeIsolated {
                self?.pauseForBackground()
                self?.endGrace()
            }
        }
    }

    private func endGrace() {
        guard grace != .invalid else { return }
        UIApplication.shared.endBackgroundTask(grace)
        grace = .invalid
    }

    private func pauseForBackground() {
        if let current = active { resumeAfterBackground.insert(current.id); current.task.cancel() }
        for id in queue { resumeAfterBackground.insert(id); downloads[id]?.state = .paused }
        queue.removeAll()
        ItchLog.event("[itch-download] paused for the background count=\(resumeAfterBackground.count)")
    }

    private func resumeFromBackground() {
        let ids = resumeAfterBackground.sorted()
        resumeAfterBackground.removeAll()
        for id in ids { enqueue(id, kind: downloads[id]?.kind ?? .install) }
    }
}

