// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// Epic Games in BCD's interface: what BCDLiveStore adds for Epic through one-line hooks (BCD's
/// screens never name Epic's code; docs/BCD-UI-ARCHITECTURE.md). Engine-bound, and outside
/// app/Madeira's top level, so the preview build (tools/ui-preview.py) never compiles it.
///
/// - Games: the account's Epic games ("epic.<app name>"), installed ones first by state, with Epic's
///   pictures. An installed Epic game is a library entry: the store's own entry for it (built as any
///   library game, with its recommended and auto-tune marks) becomes the Epic game.
/// - Play: the installed game's library entry, started by the same Play as any library game, with Epic's
///   public arguments in the entry (BCDEpicLaunch).
/// - Install, pause, resume, cancel and uninstall go to BCDEpic and the content queue.
/// With `env.BCD_EPIC = 0` every hook does nothing: installed Epic games stay ordinary library games.
enum BCDEpicAction {
    case install, update, uninstall, verify, pause, resume, cancel
}

extension BCDLiveStore {
    /// Hook in init's `changes`: Epic's account, list, installs and downloads.
    var epicChanges: AnyPublisher<Void, Never> {
        BCDEpic.enabled ? BCDEpic.shared.changes : Empty<Void, Never>().eraseToAnyPublisher()
    }

    /// Hook in rebuild(), after GOG's merge: the library entries of installed Epic games become Epic
    /// games, and the account's other Epic games (downloading or not installed) join them.
    func epicMerge(_ games: inout [BCDGame]) {
        guard BCDEpic.enabled else { return }
        let epic = BCDEpic.shared
        epic.start()
        let queue = BCDContentQueue.shared
        var byEntry: [String: BCDEpicInstallRecord] = [:]
        for record in epic.installs.values {
            if let id = record.entryID { byEntry[BCDLiveID.local(id)] = record }
        }
        var placed = Set<String>()
        for i in games.indices {
            guard let record = byEntry[games[i].id] else { continue }
            var game = games[i]
            game.id = BCDEpicRules.gameID(record.app)
            game.store = .epic
            game.title = record.title
            game.size = record.bytes
            epicApply(Self.epicArt(record.app, epic), to: &game, keepCover: true)
            game.state = epicState(record.app, installed: record, queue: queue)
            games[i] = game
            placed.insert(record.app)
        }
        // Installed games whose entry is gone (it is made again at Play), downloads, owned games.
        var others: [(id: String, title: String)] = epic.installs.values.filter { !placed.contains($0.app) }.map { ($0.app, $0.title) }
        for job in queue.jobs.values where job.key.store == epic.store && epic.installs[job.key.id] == nil {
            others.append((job.key.id, job.title))
        }
        for item in epic.games where epic.installs[item.id] == nil && queue.job(epic.key(item.id)) == nil {
            others.append((item.id, item.title ?? item.id))
        }
        var seen = placed
        for (id, title) in others where seen.insert(id).inserted {
            var game = BCDGame(id: BCDEpicRules.gameID(id), title: title, store: .epic,
                               state: epicState(id, installed: epic.installs[id], queue: queue), size: epic.installs[id]?.bytes)
            if let job = queue.job(epic.key(id)), epic.installs[id] == nil, job.progress.totalBytes > 0 {
                game.size = job.progress.totalBytes
            } else if epic.installs[id] == nil, let facts = epic.fresh(id), facts.installBytes > 0 {
                game.size = facts.installBytes   // what the page's look at the manifest said the install takes
            }
            epicApply(Self.epicArt(id, epic), to: &game, keepCover: false)
            games.append(game)
        }
    }

    /// Hook in rebuild(): Epic's downloads, after GOG's (`... + gogDownloads() + epicDownloads()`).
    func epicDownloads() -> [BCDDownload] {
        guard BCDEpic.enabled else { return [] }
        let rows = BCDContentQueue.shared.jobs.values.filter { $0.key.store == BCDEpic.shared.store }.map { job -> (Int, BCDDownload) in
            var item = BCDDownload(id: BCDEpicRules.gameID(job.key.id), title: job.title, store: .epic, doneBytes: job.progress.doneBytes,
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

    /// Hook in rebuild(): the Epic account row (replaces the "not in this build" line).
    func epicAccount() -> BCDAccount {
        let epic = BCDEpic.shared
        return BCDAccount(store: .epic, name: epic.signedIn ? (epic.displayName ?? "Epic account") : nil, available: BCDEpic.enabled)
    }

    /// Hook in start(_:), after GOG's: an Epic game's library entry, or nil for any other game (and for an Epic
    /// game that cannot start, with the reason shown as the library's error).
    func epicPlayEntry(_ game: BCDGame) -> LibraryEntry? {
        guard let app = BCDEpicRules.claimedApp(game.id, enabled: BCDEpic.enabled) else { return nil }
        switch BCDEpic.shared.playEntry(app) {
        case .success(let entry): return entry
        case .failure(let refusal): LibraryModel.shared.error = refusal.message; return nil
        }
    }

    /// Hook at the top of install, update, uninstall, verify, pauseDownload, resumeDownload and
    /// cancelDownload: true when the id is an Epic game's (the action is then done here).
    func epic(_ action: BCDEpicAction, _ id: String, place: BCDInstallPlace = .device) -> Bool {
        guard let app = BCDEpicRules.claimedApp(id, enabled: BCDEpic.enabled) else { return false }
        let epic = BCDEpic.shared, queue = BCDContentQueue.shared
        switch action {
        case .install where place == .externalDrive:
            // Epic's games install on the device in this version (the external drive comes with its own merge).
            epic.tell("Epic games install on this device for now. Choose Install.")
        case .install, .resume:
            epic.install(app)
        case .update, .verify:
            // Milestone 3: an Epic game's updates, file checks and repairs come with a later version.
            BCDEpicLog.write("[epic-install] \(action == .update ? "update" : "verify") not in this version app=\(BCDEpicLog.app(app))")
        case .uninstall:
            epic.uninstall(app)
        case .pause:
            queue.pause(epic.key(app))
        case .cancel:
            queue.cancel(epic.key(app))
        }
        return true
    }

    /// Hook at the top of signIn(_:) and signOut(_:): true for Epic.
    func epicAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool {
        guard front == .epic, BCDEpic.enabled else { return false }
        if signIn { BCDEpic.shared.signIn() } else { BCDEpic.shared.signOut() }
        return true
    }

    /// Hook in refreshLibrary(): Epic's list read again.
    func epicRefresh() {
        guard BCDEpic.enabled else { return }
        Task { await BCDEpic.shared.refreshLibrary(force: true) }
    }

    /// For the game's page, before Install: why BCD cannot install the game (nil: it can), and what to know first.
    func epicInstallAdvice(_ id: String) -> (blocker: String?, warning: String?) {
        guard let app = BCDEpicRules.claimedApp(id, enabled: BCDEpic.enabled) else { return (nil, nil) }
        return BCDEpic.shared.advice(app)
    }

    /// Hook in the game page's `opened`: a look at the game's manifest for its size and its reasons (once in half an hour).
    func epicOpened(_ id: String) {
        guard let app = BCDEpicRules.claimedApp(id, enabled: BCDEpic.enabled) else { return }
        BCDEpic.shared.preflight(app)
        Task { await BCDEpicCloud.shared.check(app) }   // build 632: acts only for a game whose cloud saves the player turned on
    }

    /// Hook in the game page's watch: the page reads its details again when a look at a manifest has answered.
    var epicDetailChanges: AnyPublisher<Void, Never> {
        BCDEpic.enabled ? BCDEpic.shared.$looks.dropFirst().map { _ in () }.eraseToAnyPublisher() : Empty<Void, Never>().eraseToAnyPublisher()
    }

    /// For the game's page, once installed: what may keep the game from starting ("Epic Online Services..."), or nil.
    func epicNote(_ id: String) -> String? {
        guard let app = BCDEpicRules.claimedApp(id, enabled: BCDEpic.enabled) else { return nil }
        return BCDEpic.shared.installs[app]?.note(signedStart: BCDEpic.shared.signedStartAvailable)
    }

    /// For the game's page: the library entry an installed Epic game is, or nil.
    func epicEntryID(_ id: String) -> UUID? {
        guard let app = BCDEpicRules.claimedApp(id, enabled: BCDEpic.enabled) else { return nil }
        return BCDEpic.shared.installs[app]?.entryID
    }

    // MARK: Helpers

    static func epicID(_ app: String) -> String { BCDEpicRules.gameID(app) }

    /// A game's pictures: the catalog's addresses, and for an installed game its own copies on the disk first (they stay when the
    /// list is gone), and the addresses its record kept when the catalog no longer names them.
    private static func epicArt(_ id: String, _ epic: BCDEpic) -> BCDEpicArt {
        var art = epic.item(id).map(BCDEpicCatalog.art) ?? BCDEpicArt()
        guard let record = epic.installs[id] else { return art }
        func address(_ text: String?) -> URL? { text.flatMap { BCDEpicCatalog.imageURL($0) }.flatMap(URL.init(string:)) }
        let tall = address(record.tall), wide = address(record.wide)
        if art.cover.isEmpty { art = BCDEpicArt(cover: [tall, wide].compactMap { $0 }, header: [wide, tall].compactMap { $0 }, hero: [wide].compactMap { $0 }) }
        if epic.artKept.contains(id) {
            let own = BCDEpic.artFile(id, wide: false), picture = BCDEpic.artFile(id, wide: true)
            let fm = FileManager.default
            if fm.fileExists(atPath: own.path) { art.cover.insert(own, at: 0) }
            if fm.fileExists(atPath: picture.path) { art.header.insert(picture, at: 0); art.hero.insert(picture, at: 0) }
        }
        return art
    }

    /// Epic's pictures; a cover the player chose for the entry stays first.
    private func epicApply(_ art: BCDEpicArt, to game: inout BCDGame, keepCover: Bool) {
        let chosen = keepCover ? game.coverArt.filter(\.isFileURL) : []
        game.coverArt = chosen + art.cover
        game.headerArt = art.header
        game.heroArt = art.hero
        game.logoArt = []
    }

    private func epicState(_ id: String, installed: BCDEpicInstallRecord?, queue: BCDContentQueue) -> BCDGameState {
        if let job = queue.job(BCDEpic.shared.key(id)), installed == nil {
            switch job.state {
            case .queued: return .queued
            case .active:
                return job.progress.phase == .finishing || job.progress.phase == .verifying
                    ? .installing(progress: job.progress.fraction) : .downloading(progress: job.progress.fraction)
            case .paused: return .downloading(progress: job.progress.fraction)   // the Downloads tab says Paused
            case .failed: return .notInstalled
            }
        }
        return installed == nil ? .notInstalled : .ready
    }
}
