// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 ft7t8drgwf-wq
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Modified by BCD (thebcd69), 2026-11-05: the Steam Workshop's model for BCD, taken without its views (BCD's pages are BCDWorkshop*.swift);
// the item journal's folder is named `bcd`; nothing here runs until the player opens a Workshop page and acts on it.
//
//  Steam Workshop (docs/STEAM_LIBRARY.md, "Steam Workshop"): over the app's own
//  Steam connection, the account's subscribed items for a game, browsing and
//  searching the game's Workshop, subscribing and unsubscribing, and downloading
//  items into Valve's Workshop folder with the item record Valve's client reads
//  (SteamWorkshopFiles.swift). A game started through BCD Dock then finds them
//  installed. Nothing runs while a game session holds the account.
//

import Combine
import Foundation

@MainActor
final class SteamWorkshopModel: ObservableObject {
    static let shared = SteamWorkshopModel()

    /// The account's subscribed items, per app, in Steam's order.
    @Published private(set) var subscribed: [UInt32: [SteamWorkshopItem]] = [:]
    /// Items on disk that are no longer subscribed, per app.
    @Published private(set) var unsubscribedInstalled: [UInt32: [SteamWorkshopItem]] = [:]
    @Published private(set) var records: [UInt32: SteamWorkshopRecord] = [:]
    @Published private(set) var loading = Set<UInt32>()
    @Published var error: String?

    enum Job: Equatable {
        case queued
        case active(SteamDownloadProgress)
        case failed(String)
    }
    /// Downloads by item ID: waiting, running, or failed with a reason.
    @Published private(set) var jobs: [UInt64: Job] = [:]
    private var queue: [SteamWorkshopItem] = []
    private var worker: Task<Void, Never>?
    /// Subscribe and unsubscribe calls in flight.
    @Published private(set) var changing = Set<UInt64>()

    private var steam: SteamOwnedLibrary { SteamOwnedLibrary.shared }

    /// The Steam library folder the game is installed in: its items go beside it.
    func steamApps(_ appID: UInt32) -> URL? {
        guard let game = SteamGamesModel.shared.games.first(where: { $0.id == Int(appID) }) else { return nil }
        return SteamOwnedLibrary.drive.appendingPathComponent(game.library, isDirectory: true)
    }

    func record(_ appID: UInt32) -> SteamWorkshopRecord { records[appID] ?? SteamWorkshopRecord(appID: appID) }

    /// Reads the item record again (off the main thread).
    func reloadRecord(_ appID: UInt32) async {
        guard let steamApps = steamApps(appID) else { records[appID] = nil; return }
        records[appID] = await Task.detached(priority: .utility) {
            SteamWorkshopRecord.read(appID: appID, steamApps: steamApps)
        }.value
    }

    func isOnDisk(_ item: SteamWorkshopItem, appID: UInt32) -> Bool {
        guard let steamApps = steamApps(appID) else { return false }
        return record(appID).isOnDisk(item.id, steamApps: steamApps)
    }

    /// Subscribed items that are not on disk or have a newer version on Steam.
    func pending(_ appID: UInt32) -> [SteamWorkshopItem] {
        (subscribed[appID] ?? []).filter { $0.downloadable && (!isOnDisk($0, appID: appID) || record(appID).needsUpdate($0)) }
    }

    // MARK: Steam

    /// The subscribed items (every page, at most 1000) and the details of
    /// installed items that are no longer subscribed.
    func refresh(_ appID: UInt32) async {
        await reloadRecord(appID)
        guard steam.workshopReady, !loading.contains(appID) else { return }
        loading.insert(appID)
        defer { loading.remove(appID) }
        do {
            var items: [SteamWorkshopItem] = []
            var page: UInt32 = 1
            while page <= 10 {
                let response = try await steam.workshopCall(.workshopGetUserFiles) {
                    SteamWorkshopRequests.subscribed(steamID: $0, appID: appID, page: page)
                }
                try Self.check(response.eresult)
                let result = try SteamWorkshopRequests.page(response.body, itemsField: 3)
                let known = Set(items.map(\.id))
                items += result.items.filter { !known.contains($0.id) }
                if result.items.count < Int(SteamWorkshopRequests.subscribedPageSize) || items.count >= result.total { break }
                page += 1
            }
            subscribed[appID] = items
            let subscribedIDs = Set(items.map(\.id))
            let others = record(appID).installed.keys.filter { !subscribedIDs.contains($0) }.sorted()
            if others.isEmpty {
                unsubscribedInstalled[appID] = []
            } else {
                let response = try await steam.workshopCall(.workshopGetDetails) { _ in
                    SteamWorkshopRequests.details(appID: appID, ids: Array(others))
                }
                let found = (try? SteamWorkshopRequests.page(response.body, itemsField: 1, totalField: nil).items) ?? []
                unsubscribedInstalled[appID] = others.map { id in found.first { $0.id == id } ?? SteamWorkshopItem(id: id, appID: appID, title: "Item \(id)") }
            }
            SteamLog.event("[steam-workshop] app=\(appID) subscribed=\(items.count) installed=\(record(appID).installed.count) pending=\(pending(appID).count)")
        } catch {
            report(error, context: "list app=\(appID)")
        }
    }

    /// One page of the game's Workshop.
    func browse(_ appID: UInt32, order: SteamWorkshopRequests.Order, text: String, page: UInt32) async throws -> SteamWorkshopPage {
        let response = try await steam.workshopCall(.workshopQueryFiles) { _ in
            SteamWorkshopRequests.query(appID: appID, order: order, text: text, page: page)
        }
        try Self.check(response.eresult)
        let result = try SteamWorkshopRequests.page(response.body, itemsField: 2)
        SteamLog.event("[steam-workshop] browse app=\(appID) order=\(order.rawValue) page=\(page) items=\(result.items.count) total=\(result.total)")
        return result
    }

    func isSubscribed(_ item: SteamWorkshopItem, appID: UInt32) -> Bool {
        subscribed[appID]?.contains { $0.id == item.id } ?? false
    }

    /// Subscribes on Steam (as the Workshop's Subscribe button does) and
    /// downloads the item.
    func subscribe(_ item: SteamWorkshopItem, appID: UInt32) async {
        guard !changing.contains(item.id) else { return }
        changing.insert(item.id)
        defer { changing.remove(item.id) }
        do {
            let response = try await steam.workshopCall(.workshopSubscribe) { _ in
                SteamWorkshopRequests.subscription(itemID: item.id, appID: appID)
            }
            try Self.check(response.eresult)
            SteamLog.event("[steam-workshop] subscribed app=\(appID) item=\(item.id)")
            if !isSubscribed(item, appID: appID) { subscribed[appID, default: []].insert(item, at: 0) }
            unsubscribedInstalled[appID]?.removeAll { $0.id == item.id }
            download(item, appID: appID)
        } catch {
            report(error, context: "subscribe app=\(appID) item=\(item.id)")
        }
    }

    /// Unsubscribes on Steam and, with `remove`, deletes the item's files.
    func unsubscribe(_ item: SteamWorkshopItem, appID: UInt32, remove: Bool) async {
        guard !changing.contains(item.id) else { return }
        changing.insert(item.id)
        defer { changing.remove(item.id) }
        do {
            let response = try await steam.workshopCall(.workshopUnsubscribe) { _ in
                SteamWorkshopRequests.subscription(itemID: item.id, appID: appID)
            }
            try Self.check(response.eresult)
            SteamLog.event("[steam-workshop] unsubscribed app=\(appID) item=\(item.id) remove=\(remove ? 1 : 0)")
            subscribed[appID]?.removeAll { $0.id == item.id }
            if remove {
                await self.remove(item, appID: appID)
            } else if isOnDisk(item, appID: appID) {
                unsubscribedInstalled[appID, default: []].append(item)
            }
        } catch {
            report(error, context: "unsubscribe app=\(appID) item=\(item.id)")
        }
    }

    /// Deletes an item's files and its record entry.
    func remove(_ item: SteamWorkshopItem, appID: UInt32) async {
        guard let steamApps = steamApps(appID) else { return }
        cancel(item.id)
        do {
            try await Task.detached(priority: .userInitiated) {
                try? FileManager.default.removeItem(at: SteamWorkshopRecord.itemFolder(appID: appID, itemID: item.id, steamApps: steamApps))
                try? FileManager.default.removeItem(at: SteamWorkshopRecord.journalFolder(appID: appID, itemID: item.id, steamApps: steamApps))
                var record = SteamWorkshopRecord.read(appID: appID, steamApps: steamApps)
                guard record.entries[item.id] != nil else { return }
                record.entries[item.id] = nil
                try record.write(steamApps: steamApps)
            }.value
            SteamLog.event("[steam-workshop] removed app=\(appID) item=\(item.id)")
        } catch {
            report(error, context: "remove app=\(appID) item=\(item.id)")
        }
        unsubscribedInstalled[appID]?.removeAll { $0.id == item.id }
        await reloadRecord(appID)
    }

    // MARK: Downloads

    func download(_ item: SteamWorkshopItem, appID: UInt32) {
        guard item.downloadable, steamApps(appID) != nil else { return }
        if case .active = jobs[item.id] { return }
        if jobs[item.id] == .queued { return }
        var item = item
        item.appID = appID
        jobs[item.id] = .queued
        queue.append(item)
        runQueue()
    }

    func downloadAll(_ appID: UInt32) {
        for item in pending(appID) { download(item, appID: appID) }
    }

    func cancel(_ itemID: UInt64) {
        queue.removeAll { $0.id == itemID }
        jobs[itemID] = nil
        if current == itemID { worker?.cancel() }
    }

    /// A game session starts: Valve's client takes the account, so downloads stop
    /// (finished chunks stay journaled; Download continues them later).
    func stopForSession() {
        guard worker != nil || !queue.isEmpty else { return }
        for item in queue { jobs[item.id] = nil }
        queue.removeAll()
        worker?.cancel()
        SteamLog.event("[steam-workshop] downloads stopped for a game session")
    }

    private var current: UInt64?

    private func runQueue() {
        guard worker == nil else { return }
        worker = Task { [weak self] in
            while let self, !Task.isCancelled, !self.queue.isEmpty {
                let item = self.queue.removeFirst()
                self.current = item.id
                await self.run(item)
                self.current = nil
            }
            self?.worker = nil
            if let self, !self.queue.isEmpty { self.runQueue() }
        }
    }

    private func run(_ item: SteamWorkshopItem) async {
        let appID = item.appID
        guard let steamApps = steamApps(appID) else { jobs[item.id] = nil; return }
        jobs[item.id] = .active(SteamDownloadProgress())
        let folder = SteamWorkshopRecord.itemFolder(appID: appID, itemID: item.id, steamApps: steamApps)
        let journal = SteamWorkshopRecord.journalFolder(appID: appID, itemID: item.id, steamApps: steamApps)
        let previous = SteamWorkshopRecord.read(appID: appID, steamApps: steamApps).entries[item.id]
        do {
            // A new version replaces the item's files, as Valve's client does.
            if let previous, previous.installed, previous.manifest != item.content || item.content == 0 {
                try? FileManager.default.removeItem(at: folder)
            }
            let size: UInt64
            if item.content != 0 && item.fileURL.isEmpty {
                size = try await steam.downloadWorkshopItem(appID: appID, itemID: item.id, manifestGID: item.content,
                                                            into: folder, journalDir: journal) { [weak self] progress in
                    Task { @MainActor in
                        if case .active = self?.jobs[item.id] { self?.jobs[item.id] = .active(progress) }
                    }
                }
            } else {
                size = try await Self.downloadFile(item, into: folder)
            }
            try Task.checkCancellation()
            let accountID = steam.workshopAccountID
            try await Task.detached(priority: .userInitiated) {
                var record = SteamWorkshopRecord.read(appID: appID, steamApps: steamApps)
                var entry = record.entries[item.id] ?? SteamWorkshopRecord.Entry()
                entry.installed = true
                entry.size = size
                entry.manifest = item.content
                entry.timeUpdated = item.timeUpdated
                entry.timeTouched = UInt32(Date().timeIntervalSince1970)
                if entry.subscribedBy == 0 { entry.subscribedBy = accountID }
                record.entries[item.id] = entry
                try record.write(steamApps: steamApps)
            }.value
            jobs[item.id] = nil
            SteamLog.event("[steam-workshop] installed app=\(appID) item=\(item.id) bytes=\(size) legacy=\(item.content == 0 || !item.fileURL.isEmpty ? 1 : 0)")
        } catch is CancellationError {
            jobs[item.id] = nil
        } catch {
            if Task.isCancelled { jobs[item.id] = nil; return }
            jobs[item.id] = .failed(SteamSignIn.message(error))
            SteamLog.event("[steam-workshop] download failed app=\(appID) item=\(item.id) reason=\(SteamOwnedLibrary.reason(error))")
        }
        await reloadRecord(appID)
    }

    /// An older item: one file at a Steam URL, saved in the item's folder.
    private nonisolated static func downloadFile(_ item: SteamWorkshopItem, into folder: URL) async throws -> UInt64 {
        var text = item.fileURL
        if text.hasPrefix("http://") { text = "https://" + text.dropFirst("http://".count) }
        guard let url = URL(string: text), url.scheme == "https", let host = url.host?.lowercased(),
              host.hasSuffix(".steamusercontent.com") || host.hasSuffix(".steamstatic.com") || host.hasSuffix(".steampowered.com")
                || host.hasSuffix(".akamaihd.net") else {
            throw SteamError.chunkDownloadFailed("Steam gave no usable download address for this item.")
        }
        let (temporary, response) = try await URLSession.shared.download(from: url)
        defer { try? FileManager.default.removeItem(at: temporary) }
        guard let status = (response as? HTTPURLResponse)?.statusCode, (200...299).contains(status) else {
            throw SteamError.chunkDownloadFailed("Steam returned HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0) for this item.")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let target = folder.appendingPathComponent(SteamWorkshopRecord.legacyFileName(item))
        try? FileManager.default.removeItem(at: target)
        try FileManager.default.moveItem(at: temporary, to: target)
        let size = (try? target.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        return UInt64(max(size, 0))
    }

    // MARK: Errors

    struct Refused: LocalizedError {
        let eresult: Int32
        var errorDescription: String? {
            switch eresult {
            case 2: return "Steam could not do this right now (generic failure). Try again later."
            case 9: return "Steam does not know this item. It may have been removed."
            case 15: return "Steam refused: this item is not available to this account."
            case 25: return "Steam's subscription limit was reached."
            case 84: return "Steam is limiting requests. Wait a minute and try again."
            default: return "Steam refused the request (error \(eresult))."
            }
        }
    }

    private static func check(_ eresult: Int32) throws {
        guard eresult == 1 || eresult == 0 else { throw Refused(eresult: eresult) }
    }

    private func report(_ error: Error, context: String) {
        self.error = (error as? Refused)?.errorDescription ?? SteamSignIn.message(error)
        SteamLog.event("[steam-workshop] \(context) failed reason=\((error as? Refused).map { "eresult-\($0.eresult)" } ?? SteamOwnedLibrary.reason(error))")
    }
}
