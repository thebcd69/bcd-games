// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

struct BCDBnetInstall: Codable {
    var product: String
    var build: String
    var version: String
    var bytes: Int64
    var entryID: UUID?
}

/// Native WoW installs through the shared journalled download queue. No Windows launcher.
@MainActor final class BCDBnetDownloads: ObservableObject, BCDContentDriver {
    static let shared = BCDBnetDownloads()
    static let root = BCDInstallRoot(drive: "C", folder: "Battle.net Games")
    static var folder: URL { BCDContentQueue.defaultFolder.appendingPathComponent("battlenet", isDirectory: true) }
    let store = "battlenet", storeTitle = "Battle.net"
    @Published private(set) var installs: [String: BCDBnetInstall] = [:]
    private var started = false
    private var watchers = Set<AnyCancellable>()
    private var queue: BCDContentQueue { .shared }
    func key(_ product: String) -> BCDContentKey { .init(store: store, id: product) }
    func start() {
        guard !started else { return }; started = true
        for product in BCDBnetNGDP.products {
            if let data = try? Data(contentsOf: recordURL(product)),
               let record = try? JSONDecoder().decode(BCDBnetInstall.self, from: data), record.product == product,
               BCDBnetNGDP.key(record.build) { installs[product] = record }
        }
        queue.register(self)
        queue.sessionRunning = { LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0 }
        queue.otherDownloadActive = { SteamOwnedLibrary.shared.hasActiveDownload }
        BCDContentBackground.shared.attach(queue)
        LibraryModel.shared.$current.receive(on: DispatchQueue.main).sink { [weak self] _ in self?.queue.reconcile() }.store(in: &watchers)
        SteamOwnedLibrary.shared.$downloads.receive(on: DispatchQueue.main).sink { [weak self] _ in self?.queue.otherDownloadChanged() }.store(in: &watchers)
        queue.$jobs.receive(on: DispatchQueue.main).sink { [weak self] _ in self?.objectWillChange.send() }.store(in: &watchers)
    }
    func install(_ product: String) {
        start()
        guard BCDBnetNGDP.products.contains(product) else { return }
        if let reason = blocker(key(product)) { LibraryModel.shared.error = reason; return }
        queue.enqueue(key(product), title: BCDBnetNGDP.title(product))
    }
    func pauseAll() {
        for job in queue.jobs.values where job.key.store == store { queue.pause(job.key) }
    }
    func blocker(_ key: BCDContentKey) -> String? {
        guard BCDBnetNGDP.products.contains(key.id) else { return "This Battle.net game isn't supported yet." }
        if !BCDBattleNet.shared.signedIn { return BCDBattleNetError.signedOut.errorDescription }
        if installs[key.id] != nil { return "This WoW client is already installed." }
        return nil
    }
    private func pendingURL(_ product: String) -> URL { Self.folder.appendingPathComponent(product + ".pending.json") }
    private func recordURL(_ product: String) -> URL { Self.folder.appendingPathComponent(product + ".installed.json") }
    private func pending(_ product: String) -> BCDBnetBuild? {
        guard BCDBnetNGDP.products.contains(product), let data = try? Data(contentsOf: pendingURL(product)),
              let build = try? JSONDecoder().decode(BCDBnetBuild.self, from: data), build.product == product else { return nil }
        return build
    }
    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan {
        if let stored, let build = pending(key.id), stored.version == build.build { return stored }
        guard BCDBnetNGDP.products.contains(key.id), installs[key.id] == nil else { throw BCDBattleNetError.format("product") }
        let product = key.id, region = BCDBattleNet.shared.downloadRegion
        let work = Task.detached(priority: .utility) { try await BCDBnetDownload.prepare(product, region: region) }
        let build = try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
        try Task.checkCancellation()
        let plan = try BCDBnetDownload.plan(build)
        try FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        try JSONEncoder().encode(build).write(to: pendingURL(product), options: .atomic)
        return plan
    }
    func source(for plan: BCDContentPlan) async throws -> BCDChunkSource {
        guard let build = pending(plan.key.id), build.build == plan.version else { throw BCDBattleNetError.format("pending") }
        return BCDBnetChunkSource(build)
    }
    func root(for key: BCDContentKey) -> BCDInstallRoot { Self.root }
    func options(for plan: BCDContentPlan) -> BCDContentOptions {
        var options = BCDContentOptions()
        let clients = pending(plan.key.id)?.install.reduce(Int64(0)) { $0 + $1.size } ?? 0
        options.spaceMargin = plan.totalBytes + clients + (512 << 20)
        return options
    }
    func finish(_ plan: BCDContentPlan, stage: URL) async throws {
        guard let build = pending(plan.key.id), build.build == plan.version, let root = Self.root.url else { throw BCDBattleNetError.format("pending") }
        let destination = root.appendingPathComponent(build.product, isDirectory: true)
        let work = Task.detached(priority: .utility) {
            if BCDBnetCASC.committed(build, at: destination) { return }
            let made = try BCDBnetCASC.assemble(build, stage: stage)
            try Task.checkCancellation()
            try BCDContentEngine.commit(stage: made, to: destination)
        }
        try await withTaskCancellationHandler { try await work.value } onCancel: { work.cancel() }
        var record = BCDBnetInstall(product: build.product, build: build.build, version: build.version,
                                    bytes: plan.totalBytes + build.install.reduce(0) { $0 + $1.size })
        let entry = makeEntry(record)
        record.entryID = entry.id
        try JSONEncoder().encode(record).write(to: recordURL(build.product), options: .atomic)
        installs[build.product] = record
        try? FileManager.default.removeItem(at: pendingURL(build.product))
        if let trash = Self.root.stagingURL?.appendingPathComponent("trash") {
            await BCDContentEngine.remove(stage, trash: trash)
        }
    }
    func cancelled(_ key: BCDContentKey) { try? FileManager.default.removeItem(at: pendingURL(key.id)) }
    private func makeEntry(_ record: BCDBnetInstall) -> LibraryEntry {
        let path = "Battle.net Games/" + record.product + "/" + BCDBnetNGDP.program(record.product)
        let library = LibraryModel.shared
        var entry = library.entries.first { $0.id == record.entryID || $0.relativePath == path }
            ?? LibraryEntry(title: BCDBnetNGDP.title(record.product), relativePath: path, bits: 64)
        entry.folderBytes = record.bytes
        library.save(entry)
        return entry
    }
    func entry(_ product: String) -> LibraryEntry? {
        guard var record = installs[product], let root = Self.root.url,
              FileManager.default.fileExists(atPath: root.appendingPathComponent(product + "/" + BCDBnetNGDP.program(product)).path) else { return nil }
        if let entry = LibraryModel.shared.entries.first(where: { $0.id == record.entryID }) { return entry }
        let entry = makeEntry(record)
        record.entryID = entry.id
        installs[product] = record
        try? JSONEncoder().encode(record).write(to: recordURL(product), options: .atomic)
        return entry
    }
    func uninstall(_ product: String) {
        guard LibraryModel.shared.current == nil, wine_process_is_running() == 0, wineserver_is_running() == 0 else {
            LibraryModel.shared.error = "Close the game before uninstalling it."; return
        }
        guard let record = installs[product], let folder = Self.root.url?.appendingPathComponent(product),
              let trash = Self.root.stagingURL?.appendingPathComponent("trash") else { return }
        queue.cancel(key(product))
        if let id = record.entryID { LibraryModel.shared.remove(id) }
        installs[product] = nil
        try? FileManager.default.removeItem(at: recordURL(product))
        Task { await BCDContentEngine.remove(folder, trash: trash) }
    }
}
