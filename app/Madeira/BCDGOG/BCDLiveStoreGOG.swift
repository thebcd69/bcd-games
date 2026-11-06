// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// GOG in BCD's interface: what BCDLiveStore adds for GOG through one-line hooks (BCD's screens
/// never name GOG's code; docs/BCD-UI-ARCHITECTURE.md). Engine-bound, and outside app/Madeira's
/// top level, so the preview build (tools/ui-preview.py) never compiles it.
///
/// - Games: the account's GOG games ("gog.<product ID>"), installed ones first by state, with GOG's
///   pictures. An installed GOG game is a library entry: the store's own entry for it (built as any
///   library game, with its recommended and auto-tune marks) becomes the GOG game.
/// - Play: the installed game's library entry, started by the same Play as any library game.
/// - Install, pause, resume, cancel and uninstall go to BCDGOG and the content queue.
/// With `env.BCD_GOG = 0` every hook does nothing: installed GOG games stay ordinary library games.
enum BCDGOGAction {
    case install, update, uninstall, verify, pause, resume, cancel
}

extension BCDLiveStore {
    /// Hook in init's `changes`: GOG's account, list, installs and downloads.
    var gogChanges: AnyPublisher<Void, Never> {
        BCDGOG.enabled ? BCDGOG.shared.changes : Empty<Void, Never>().eraseToAnyPublisher()
    }

    /// Hook in rebuild(), before the sort: the library entries of installed GOG games become GOG
    /// games, and the account's other GOG games (downloading or not installed) join them.
    func gogMerge(_ games: inout [BCDGame]) {
        guard BCDGOG.enabled else { return }
        let gog = BCDGOG.shared
        gog.start()
        let queue = BCDContentQueue.shared
        var byEntry: [String: BCDGOGInstallRecord] = [:]
        for record in gog.installs.values {
            if let id = record.entryID { byEntry[BCDLiveID.local(id)] = record }
        }
        var placed = Set<String>()
        for i in games.indices {
            guard let record = byEntry[games[i].id] else { continue }
            var game = games[i]
            game.id = Self.gogID(record.productID)
            game.store = .gog
            game.title = record.title
            game.size = record.bytes
            apply(Self.gogArt(record.productID, gog: gog), to: &game, keepCover: true)
            game.state = gogState(record.productID, installed: record, queue: queue)
            games[i] = game
            placed.insert(record.productID)
        }
        // Installed games whose entry is gone (it is made again at Play), downloads, owned games.
        var others: [(id: String, title: String)] = gog.installs.values.filter { !placed.contains($0.productID) }
            .map { ($0.productID, $0.title) }
        for job in queue.jobs.values where job.key.store == gog.store && gog.installs[job.key.id] == nil {
            others.append((job.key.id, job.title))
        }
        for product in gog.products where gog.installs[product.id] == nil && queue.job(gog.key(product.id)) == nil {
            others.append((product.id, product.title))
        }
        var seen = placed
        for (id, title) in others where seen.insert(id).inserted {
            var game = BCDGame(id: Self.gogID(id), title: title, store: .gog,
                               state: gogState(id, installed: gog.installs[id], queue: queue), size: gog.installs[id]?.bytes)
            if let job = queue.job(gog.key(id)), gog.installs[id] == nil, job.progress.totalBytes > 0 { game.size = job.progress.totalBytes }
            apply(Self.gogArt(id, gog: gog), to: &game, keepCover: false)
            games.append(game)
        }
    }

    /// Hook in rebuild(): GOG's downloads, after Steam's (`downloadList(names:) + gogDownloads()`).
    func gogDownloads() -> [BCDDownload] {
        guard BCDGOG.enabled else { return [] }
        let rows = BCDContentQueue.shared.jobs.values.filter { $0.key.store == BCDGOG.shared.store }.map { job -> (Int, BCDDownload) in
            var item = BCDDownload(id: Self.gogID(job.key.id), title: job.title, store: .gog, doneBytes: job.progress.doneBytes,
                                   totalBytes: job.progress.totalBytes, bytesPerSecond: job.state == .active ? job.progress.bytesPerSecond : 0)
            let rank: Int
            switch job.state {
            case .active: rank = 0
            case .queued: rank = 1
            case .paused: rank = 2; item.paused = true
            case .failed(let message): rank = 3; item.failed = message
            }
            return (rank, item)
        }
        return rows.sorted { $0.0 != $1.0 ? $0.0 < $1.0 : $0.1.title.localizedStandardCompare($1.1.title) == .orderedAscending }.map(\.1)
    }

    /// Hook in rebuild(): the GOG account row (replaces the "not in this build" line).
    func gogAccount() -> BCDAccount {
        let gog = BCDGOG.shared
        return BCDAccount(store: .gog, name: gog.signedIn ? (gog.userName ?? "GOG account") : nil, available: BCDGOG.enabled)
    }

    /// Hook in start(_:), after `guard let launch`: a GOG game's library entry, or nil for any other
    /// game (and for a GOG game that cannot start, with the reason shown as the library's error).
    func gogPlayEntry(_ game: BCDGame) -> LibraryEntry? {
        guard BCDGOG.enabled, let id = Self.gogProduct(game.id) else { return nil }
        switch BCDGOG.shared.playEntry(id) {
        case .success(let entry): return entry
        case .failure(let refusal): LibraryModel.shared.error = refusal.message; return nil
        }
    }

    /// Hook at the top of install, update, uninstall, verify, pauseDownload, resumeDownload and
    /// cancelDownload: true when the id is a GOG game's (the action is then done here).
    func gog(_ action: BCDGOGAction, _ id: String) -> Bool {
        guard BCDGOG.enabled, let product = Self.gogProduct(id) else { return false }
        let gog = BCDGOG.shared, queue = BCDContentQueue.shared
        switch action {
        case .install:
            gog.install(product)
        case .resume:
            gog.resume(product)   // an installed game's paused update or repair goes on; any other game installs
        case .update:
            gog.update(product)
        case .verify:
            gog.repair(product)   // the page's Repair: every installed file is read and compared with the build
        case .uninstall:
            gog.uninstall(product)
        case .pause:
            queue.pause(gog.key(product))
        case .cancel:
            queue.cancel(gog.key(product))
        }
        return true
    }

    /// Hook at the top of signIn(_:) and signOut(_:): true for GOG.
    func gogAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool {
        guard front == .gog, BCDGOG.enabled else { return false }
        if signIn { BCDGOG.shared.signIn() } else { BCDGOG.shared.signOut() }
        return true
    }

    /// For the game's page: what an installed GOG game's build names that BCD doesn't install
    /// yet ("May need: Visual C++ 2017, DirectX (June 2010)."), or nil.
    func gogNote(_ id: String) -> String? {
        guard BCDGOG.enabled, let product = Self.gogProduct(id) else { return nil }
        return BCDGOG.shared.note(product)
    }

    /// Hook in the game page's `opened`: a look for an update of an installed GOG game (once in six hours; BCDGOG.checkForUpdate).
    func gogOpened(_ id: String) {
        guard BCDGOG.enabled, let product = Self.gogProduct(id) else { return }
        BCDGOG.shared.checkForUpdate(product)
    }

    /// For the game's page: a download runs for an installed GOG game (an update or a repair, which Stop only stops).
    func gogUpdating(_ id: String) -> Bool {
        guard BCDGOG.enabled, let product = Self.gogProduct(id) else { return false }
        return BCDGOG.shared.isUpdating(product)
    }

    /// Hook in refreshLibrary(): GOG's list read again.
    func gogRefresh() {
        guard BCDGOG.enabled else { return }
        Task { await BCDGOG.shared.refreshLibrary(force: true) }
    }

    // MARK: Helpers

    static func gogID(_ product: String) -> String { "gog." + product }

    static func gogProduct(_ id: String) -> String? {
        guard id.hasPrefix("gog.") else { return nil }
        let product = String(id.dropFirst(4))
        return BCDGOGCatalog.validID(product) ? product : nil
    }

    private static func gogArt(_ id: String, gog: BCDGOG) -> BCDGOGArt {
        gog.product(id).map(BCDGOGCatalog.art) ?? BCDGOGArt()
    }

    /// GOG's pictures; a cover the player chose for the entry stays first.
    private func apply(_ art: BCDGOGArt, to game: inout BCDGame, keepCover: Bool) {
        let chosen = keepCover ? game.coverArt.filter(\.isFileURL) : []
        game.coverArt = chosen + art.cover
        game.headerArt = art.header
        game.heroArt = art.hero
        game.logoArt = []
    }

    private func gogState(_ id: String, installed: BCDGOGInstallRecord?, queue: BCDContentQueue) -> BCDGameState {
        if let job = queue.job(BCDGOG.shared.key(id)), installed == nil {
            switch job.state {
            case .queued: return .queued
            case .active:
                return job.progress.phase == .finishing || job.progress.phase == .verifying
                    ? .installing(progress: job.progress.fraction) : .downloading(progress: job.progress.fraction)
            case .paused: return .downloading(progress: job.progress.fraction)   // the Downloads tab says Paused
            case .failed: return .notInstalled
            }
        }
        guard let installed else { return .notInstalled }
        if installed.play == nil { return .broken(reason: BCDGOGError.noPlayTask.errorDescription ?? "") }
        // GOG has a newer build, or DLC the account owns that the game lacks (found when the game's page opened): Update is offered.
        // The size is not known before the update's own plan (it holds only the files that change).
        return BCDGOG.shared.updates[id] != nil && queue.job(BCDGOG.shared.key(id)) == nil ? .updateAvailable(bytes: 0) : .ready
    }
}
