// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import Combine

enum BCDBnetAction { case install, update, uninstall, verify, pause, resume, cancel }

extension BCDLiveStore {
    var battleNetChanges: AnyPublisher<Void, Never> { BCDBnetDownloads.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher() }
    func battleNetAccount() -> BCDAccount {
        let account = BCDBattleNet.shared
        return BCDAccount(store: .battlenet, name: account.signedIn ? (account.name ?? "Battle.net account") : nil, available: true)
    }

    /// Imported WoW clients retain their library IDs, settings, play history and ordinary Play.
    /// Account discovery adds missing flavours without claiming to have installed them.
    func battleNetMerge(_ games: inout [BCDGame]) {
        let account = BCDBattleNet.shared
        account.start()
        let downloads = BCDBnetDownloads.shared
        downloads.start()
        for product in Array(downloads.installs.keys) { _ = downloads.entry(product) }
        var installed = Set<String>()
        for entry in LibraryModel.shared.entries {
            guard entry.desktop != true, entry.steamAppID == nil,
                  let product = BCDBattleNetAccount.installedProduct(entry.relativePath),
                  let index = games.firstIndex(where: { $0.id == BCDLiveID.local(entry.id) }) else { continue }
            games[index].store = .battlenet
            if downloads.installs[product]?.entryID == entry.id { games[index].id = "battlenet." + product }
            installed.insert(product)
        }
        var products = Dictionary(account.products.map { ($0.id, $0.title) }, uniquingKeysWith: { a, _ in a })
        for product in downloads.installs.keys { products[product] = BCDBnetNGDP.title(product) }
        for job in BCDContentQueue.shared.jobs.values where job.key.store == "battlenet" { products[job.key.id] = job.title }
        for (id, title) in products where !installed.contains(id) {
            games.append(BCDGame(id: "battlenet." + id, title: title, store: .battlenet,
                                 state: downloads.installs[id] == nil ? .notInstalled : .broken(reason: "WoW's program is missing. Uninstall this client and install it again.")))
        }
        for i in games.indices where games[i].id.hasPrefix("battlenet.") {
            let product = String(games[i].id.dropFirst(10))
            if let job = BCDContentQueue.shared.job(downloads.key(product)), downloads.installs[product] == nil {
                switch job.state {
                case .queued: games[i].state = .queued
                case .active: games[i].state = job.progress.phase == .finishing ? .installing(progress: 1) : .downloading(progress: job.progress.fraction)
                case .paused: games[i].state = .downloading(progress: job.progress.fraction)
                case .failed: games[i].state = .notInstalled
                }
                if job.progress.totalBytes > 0 { games[i].size = job.progress.totalBytes }
            }
        }
    }
    func battleNetDownloads() -> [BCDDownload] {
        BCDContentQueue.shared.jobs.values.filter { $0.key.store == "battlenet" }.map { job in
            var item = BCDDownload(id: "battlenet." + job.key.id, title: job.title, store: .battlenet,
                                  doneBytes: job.progress.doneBytes, totalBytes: job.progress.totalBytes, bytesPerSecond: job.progress.bytesPerSecond)
            if case .paused = job.state { item.paused = true }
            if case .failed(let message) = job.state { item.failed = message }
            return item
        }.sorted { $0.title < $1.title }
    }
    func battleNetAction(_ action: BCDBnetAction, _ id: String) -> Bool {
        guard id.hasPrefix("battlenet.") else { return false }
        let product = String(id.dropFirst(10)), downloads = BCDBnetDownloads.shared
        guard BCDBnetNGDP.products.contains(product) else { return true }
        switch action {
        case .install, .resume: downloads.install(product)
        case .pause: BCDContentQueue.shared.pause(downloads.key(product))
        case .cancel: BCDContentQueue.shared.cancel(downloads.key(product))
        case .uninstall: downloads.uninstall(product)
        case .update, .verify: LibraryModel.shared.error = "WoW updates and repairs aren't available yet."
        }
        return true
    }
}
