// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// The Steam Workshop pages, engine side (BCDWorkshop.swift has their values and words, BCDWorkshopView.swift the screens): the installed
// games, a game's subscribed items and install record, Browse, and the actions on an item, all through SteamWorkshopModel (the app's own Steam
// connection) and SteamOwnedLibrary. Engine-bound like BCDGamePageLive.swift, so the preview build leaves it out (it lives in a folder of its
// own, which the preview build does not read). BCDWorkshopSource finds this class by its Objective-C name.
//
// Nothing here runs until a Workshop page opens. Opening one asks Steam for the account's subscribed items (a read); the model's subscribe,
// unsubscribe and download calls are made only from the page's buttons, after its questions.
// Keyed: `env.MADEIRA_STEAM_WORKSHOP = 0` hides the pages and refuses every call.
// Log tag: [steam-workshop] (App IDs, item IDs, counts and short reason codes; never account data).

@objc(BCDWorkshopLive)
@MainActor final class BCDWorkshopLive: NSObject, BCDWorkshopBackend {
    var onChange: (() -> Void)? { didSet { watch() } }
    private var watching = Set<AnyCancellable>()
    /// The items the pages listed, by ID: the model's own values behind a listing the page holds.
    private var known: [UInt64: SteamWorkshopItem] = [:]

    nonisolated override init() { super.init() }

    private var model: SteamWorkshopModel { SteamWorkshopModel.shared }
    private var steam: SteamOwnedLibrary { SteamOwnedLibrary.shared }

    private func watch() {
        watching.removeAll()
        guard onChange != nil else { return }
        let changes: [AnyPublisher<Void, Never>] = [
            model.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            steam.$signedIn.dropFirst().map { _ in () }.eraseToAnyPublisher(),
            SteamGamesModel.shared.$games.dropFirst().map { _ in () }.eraseToAnyPublisher(),
        ]
        // objectWillChange fires before the value changes: the page reads on the next turn of the main queue.
        Publishers.MergeMany(changes)
            .debounce(for: .milliseconds(150), scheduler: RunLoop.main)
            .sink { [weak self] in self?.onChange?() }
            .store(in: &watching)
    }

    // MARK: What the pages ask

    var available: Bool { SteamOwnedLibrary.workshopEnabled && steam.signedIn }

    func games() -> [BCDWorkshopGame] {
        let installed = SteamGamesModel.shared.games.filter(\.installed)
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        return installed.prefix(300).compactMap { game in
            let appID = UInt32(clamping: game.id)
            let items = model.record(appID).installed.count
            let said = steam.game(game.id)?.workshop
            // Steam says there is none and nothing is installed: the game is not listed.
            if said == false && items == 0 { return nil }
            let pending = model.subscribed[appID] != nil ? model.pending(appID).count : 0
            return BCDWorkshopGame(id: game.id, title: game.name, installedItems: items, hasWorkshop: said == true, pending: pending)
        }
    }

    func page(_ appID: Int) -> BCDWorkshopPageState {
        let id = UInt32(clamping: appID)
        var state = BCDWorkshopPageState()
        state.installed = model.steamApps(id) != nil
        state.signedIn = steam.signedIn
        state.ready = steam.workshopReady
        state.loading = model.loading.contains(id)
        state.subscribed = model.subscribed[id].map { $0.map(listing) }
        state.others = (model.unsubscribedInstalled[id] ?? []).map(listing)
        state.pending = model.subscribed[id] != nil ? model.pending(id).count : 0
        state.problem = model.error
        return state
    }

    func status(_ item: UInt64, appID: Int) -> BCDWorkshopStatus {
        let id = UInt32(clamping: appID)
        var status = BCDWorkshopStatus()
        let known = self.known[item]
        status.subscribed = known.map { model.isSubscribed($0, appID: id) } ?? (model.subscribed[id]?.contains { $0.id == item } ?? false)
        switch model.jobs[item] {
        case .queued?:
            status.state = .waiting
        case .active(let progress)?:
            status.state = progress.totalBytes > 0 ? .downloading(fraction: progress.fraction, totalBytes: progress.totalBytes) : .waiting
        case .failed(let message)?:
            status.state = .failed(message)
        case nil:
            let record = model.record(id)
            if let known, model.isOnDisk(known, appID: id) {
                status.state = .installed(bytes: record.entries[item]?.size ?? 0, update: record.needsUpdate(known))
            } else if model.steamApps(id) != nil, record.entries[item]?.installed == true {
                status.state = .installed(bytes: record.entries[item]?.size ?? 0, update: false)
            }
        }
        return status
    }

    func opened(_ appID: Int) {
        guard SteamOwnedLibrary.workshopEnabled else { return }
        let id = UInt32(clamping: appID)
        Task { await model.refresh(id) }
    }

    func browse(_ appID: Int, order: BCDWorkshopOrder, text: String, page: Int) async throws -> BCDWorkshopBrowsePage {
        guard SteamOwnedLibrary.workshopEnabled else { throw BCDGameError("The Workshop is switched off.") }
        let id = UInt32(clamping: appID)
        let sort: SteamWorkshopRequests.Order
        if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            sort = .search
        } else {
            switch order {
            case .popular: sort = .trend
            case .subscribed: sort = .subscribed
            case .recent: sort = .recent
            }
        }
        do {
            let result = try await model.browse(id, order: sort, text: text, page: UInt32(clamping: page))
            return BCDWorkshopBrowsePage(total: result.total, items: result.items.map(listing))
        } catch {
            throw BCDGameError((error as? SteamWorkshopModel.Refused)?.errorDescription ?? SteamSignIn.message(error))
        }
    }

    // MARK: What the player does

    func subscribe(_ item: BCDWorkshopListing, appID: Int) {
        guard SteamOwnedLibrary.workshopEnabled, let real = known[item.id] else { return }
        Task { await model.subscribe(real, appID: UInt32(clamping: appID)) }
    }

    func unsubscribe(_ item: BCDWorkshopListing, appID: Int, removeFiles: Bool) {
        guard SteamOwnedLibrary.workshopEnabled, let real = known[item.id] else { return }
        Task { await model.unsubscribe(real, appID: UInt32(clamping: appID), remove: removeFiles) }
    }

    func download(_ item: BCDWorkshopListing, appID: Int) {
        guard SteamOwnedLibrary.workshopEnabled, let real = known[item.id] else { return }
        model.download(real, appID: UInt32(clamping: appID))
    }

    func downloadAll(_ appID: Int) {
        guard SteamOwnedLibrary.workshopEnabled else { return }
        model.downloadAll(UInt32(clamping: appID))
    }

    func cancel(_ item: UInt64) { model.cancel(item) }

    func remove(_ item: BCDWorkshopListing, appID: Int) {
        guard let real = known[item.id] else { return }
        Task { await model.remove(real, appID: UInt32(clamping: appID)) }
    }

    func clearProblem() { model.error = nil }

    // MARK: Values

    /// A page's listing of a model item; the model's own value is kept for the actions.
    private func listing(_ item: SteamWorkshopItem) -> BCDWorkshopListing {
        known[item.id] = item
        let kind: BCDWorkshopListing.Kind
        if item.banned {
            kind = .removed
        } else if item.result != 1 && item.result != 0 {
            kind = .unavailable
        } else if item.isCollection {
            kind = .collection
        } else if !item.isContent {
            kind = .notContent
        } else {
            kind = .content
        }
        return BCDWorkshopListing(id: item.id, title: item.title, summary: item.summary, preview: BCDWorkshop.secure(item.previewURL),
                                  bytes: item.fileSize, kind: kind, downloadable: item.downloadable, link: item.storeURL)
    }
}
