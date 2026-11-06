// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// Epic Games in BCD, milestone 1 (docs/research/open-fork-2026-10-18/epic.md §4.4, §5 M1): the install
// side of BCDEpic. Epic is a driver of the shared content queue, as GOG is: it describes a job as a plan
// (BCDEpicContent), says where its chunks come from (BCDEpicChunkSource) and records the finished
// install (BCDEpicInstall). The installed game is an ordinary library entry; Play is the library's start
// of a game added from drive_c, with Epic's public arguments in the entry and no launcher in Wine.
// Engine-bound. Log tags: [epic-install], [epic-cdn], [epic-launch] (app names, counts, sizes, short reasons).

extension BCDEpic: BCDContentDriver {
    var storeTitle: String { "Epic Games" }
    static var root: BCDInstallRoot { BCDEpicInstall.root }
    static var refusalsFile: URL { folder.appendingPathComponent("refusals.json") }
    /// This copy of BCD's build number: a refusal is remembered only by the build that noted it.
    static var appBuild: String { (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "0" }

    func key(_ app: String) -> BCDContentKey { BCDContentKey(store: store, id: app) }

    // MARK: Wiring

    /// Once per run, at the first look: the install records and the refusals, and (installs on) the queue's wiring.
    /// No network, and nothing started: a download begins only when the player taps Install.
    func wireInstalls() {
        installs = BCDEpicInstall.loadRecords(Self.folder)
        refusals = BCDEpicRefusals.load(Self.refusalsFile)
        guard Self.installEnabled else { return }
        let queue = self.queue
        queue.register(self)
        queue.sessionRunning = { LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0 }
        queue.otherDownloadActive = { SteamOwnedLibrary.shared.hasActiveDownload }
        BCDContentBackground.shared.attach(queue)
        // @Published sends before the value is stored: look on the next turn of the main queue.
        LibraryModel.shared.$current.receive(on: DispatchQueue.main)
            .sink { [weak queue] _ in queue?.reconcile() }.store(in: &watchers)
        SteamOwnedLibrary.shared.$downloads.receive(on: DispatchQueue.main)
            .sink { [weak queue] _ in queue?.otherDownloadChanged() }.store(in: &watchers)
        if let trash = Self.root.stagingURL?.appendingPathComponent("trash", isDirectory: true),
           FileManager.default.fileExists(atPath: trash.path) {
            Task.detached(priority: .utility) { try? FileManager.default.removeItem(at: trash) }
        }
        BCDEpicLog.write("[epic-install] start installed=\(installs.count) refused=\(refusals.entries.count)")
        for app in installs.keys.sorted() { keepArt(app) }
    }

    // MARK: Install, play, uninstall

    func install(_ app: String) {
        guard Self.enabled, Self.installEnabled, BCDEpicCatalog.validAppName(app), installs[app] == nil else { return }
        guard signedIn else { tell(BCDEpicError.signedOut.errorDescription ?? ""); return }
        guard let item = item(app), item.kind == .game else {
            tell("BCD doesn't have this game's details yet. Refresh the library, then try again.")
            return
        }
        let title = item.title ?? app
        if let blocker = advice(app).blocker {
            tell("Can't install \(title). " + blocker)
            BCDEpicLog.write("[epic-install] refused app=\(BCDEpicLog.app(app)) before the download")
            return
        }
        start()
        BCDEpicLog.write("[epic-install] install asked app=\(BCDEpicLog.app(app))")
        queue.enqueue(key(app), title: title)
    }

    /// Why a game cannot be installed, and what to know before it is: for the game's page. The catalog's rules come first; once the
    /// page's look at the manifest has answered (`preflight`), anti-cheat and the like are said before a single chunk is fetched.
    func advice(_ app: String, now: Date = Date()) -> (blocker: String?, warning: String?) {
        guard installs[app] == nil, let item = item(app) else { return (nil, nil) }
        return BCDEpicRules.advice(item: item, remembered: refusals.refusal(app, now: now, build: Self.appBuild), facts: fresh(app, now: now))
    }

    /// What the last look at the game's manifest said, while it is recent (half an hour).
    func fresh(_ app: String, now: Date = Date()) -> BCDEpicFacts? {
        guard let held = looks[app], let facts = held.facts, now.timeIntervalSince(held.at) < 1800 else { return nil }
        return facts
    }

    /// Looks at a game's manifest for its page (once in half an hour, or in three minutes after a failure): the asset
    /// pointer and the manifest, nothing downloaded of the game itself. Only for a game BCD could install, signed in.
    func preflight(_ app: String, now: Date = Date()) {
        guard Self.enabled, Self.installEnabled, signedIn, BCDEpicCatalog.validAppName(app), installs[app] == nil,
              let item = item(app), item.kind == .game, BCDEpicRules.catalogRefusal(item) == nil, refusals.refusal(app, now: now, build: Self.appBuild) == nil,
              queue.job(key(app)) == nil, !checking.contains(app) else { return }
        if let held = looks[app], now.timeIntervalSince(held.at) < (held.facts == nil ? 180 : 1800) { return }
        checking.insert(app)
        let api = self.api, preferred = Self.preferredCDN
        Task.detached(priority: .utility) { [weak self] in
            let started = BCDEpicLog.now()
            var facts: BCDEpicFacts?
            var reason = ""
            do {
                let (asset, file) = Self.fetchers(api)
                facts = BCDEpicContent.facts(try await BCDEpicContent.load(app: app, namespace: item.namespace, catalogItem: item.catalogItemId,
                                                                           preferring: preferred, fetchAsset: asset, fetchFile: file))
            } catch {
                reason = BCDEpicLog.reason(error)
                facts = BCDEpicContent.facts(from: error)
            }
            guard let owner = self else { return }
            let found = facts, why = reason
            await MainActor.run {
                owner.checking.remove(app)
                owner.looks[app] = BCDEpicPreflight(facts: found, at: Date())
                if let found {
                    BCDEpicLog.write("[epic-install] look app=\(BCDEpicLog.app(app)) download=\(found.downloadBytes) install=\(found.installBytes) "
                                     + "eos=\(found.eos ? 1 : 0) refused=\(BCDEpicRules.sanitized(found.refusal?.code ?? "none")) ms=\(BCDEpicLog.millis(since: started))")
                } else {
                    BCDEpicLog.write("[epic-install] look failed app=\(BCDEpicLog.app(app)) reason=\(why) ms=\(BCDEpicLog.millis(since: started))")
                }
            }
        }
    }

    /// The two reads an install and a look both make: Epic's own service with the token, a CDN without one.
    nonisolated static func fetchers(_ api: BCDEpicAPI) -> (BCDEpicContent.FetchAsset, BCDEpicContent.FetchFile) {
        ({ url in
            let (_, data) = try await api.get(url)
            guard let object = BCDEpicAuth.jsonObject(data) else { throw BCDEpicContentError.badAsset("json") }
            return object
        }, { url in
            let stream = BCDHTTPStream()
            defer { stream.close() }
            var request = URLRequest(url: url)
            request.timeoutInterval = 60
            return try await stream.data(request, limit: 256 << 20)
        })
    }

    /// The library entry Play starts, made again if the library lost it, with Epic's public arguments.
    func playEntry(_ app: String) -> Result<LibraryEntry, BCDEpicPlayRefusal> {
        guard var record = installs[app] else { return .failure(.init(message: "This Epic game isn't installed.")) }
        // Build 632: a cloud-save sync of this game (only ever running when the player turned it on) finishes first.
        if BCDEpicCloud.shared.isSyncing(app) { return .failure(.init(message: "BCD is syncing this game's saves with Epic Cloud. Try again in a moment.")) }
        guard let folder = record.hostFolder, FileManager.default.fileExists(atPath: folder.appendingPathComponent(record.launchPath).path) else {
            return .failure(.init(message: "BCD can't find this game's files. Uninstall it, then install it again."))
        }
        let existing = record.entryID.flatMap { id in LibraryModel.shared.entries.first { $0.id == id } }
        if existing == nil {
            makeEntry(&record)
        } else if let entry = existing {
            // The arguments BCD wrote are refreshed (the language may have changed); ones the player edited are left alone.
            let launch = BCDEpicInstall.launch(record)
            if entry.arguments == record.arguments, launch.arguments != entry.arguments {
                var changed = entry
                changed.arguments = launch.arguments
                LibraryModel.shared.save(changed)
                record.arguments = launch.arguments
            }
        }
        try? BCDEpicInstall.save(record, base: Self.folder)
        installs[app] = record
        guard let entry = record.entryID.flatMap({ id in LibraryModel.shared.entries.first { $0.id == id } }) else {
            return .failure(.init(message: "BCD couldn't add this game to the library. Its folder is \(record.windowsFolder)."))
        }
        let own = entry.arguments == record.arguments
        BCDEpicLog.write("[epic-launch] play app=\(BCDEpicLog.app(app)) arguments=\(own ? "epic" : "yours") deployment=\(record.deploymentID == nil ? 0 : 1)")
        return .success(entry)
    }

    /// The game's files go (renamed out of sight at once, deleted in the background), with its record, its kept
    /// manifest and its library entry. Its saves in the Windows user profile stay.
    func uninstall(_ app: String) {
        if queue.job(key(app)) != nil { queue.cancel(key(app)) }
        guard let record = installs[app] else { return }
        guard LibraryModel.shared.current == nil, wine_process_is_running() == 0 else {
            tell("Close the game before uninstalling it.")
            return
        }
        if let entryID = record.entryID { LibraryModel.shared.remove(entryID) }
        BCDEpicInstall.delete(app, base: Self.folder)
        installs[app] = nil
        for wide in [false, true] { try? FileManager.default.removeItem(at: Self.artFile(app, wide: wide)) }
        artKept.remove(app)
        if let folder = record.hostFolder, let trash = record.root.stagingURL?.appendingPathComponent("trash", isDirectory: true) {
            Task { await BCDContentEngine.remove(folder, trash: trash) }
        }
        BCDEpicCloud.shared.forget(app)   // build 632: the remembered save rule and common state go; Epic's cloud is not touched
        BCDEpicLog.write("[epic-install] uninstalled app=\(BCDEpicLog.app(app))")
    }

    /// The record's program as a library entry: an existing entry keeps its settings.
    private func makeEntry(_ record: inout BCDEpicInstallRecord) {
        guard let folder = record.hostFolder else { return }
        let launch = BCDEpicInstall.launch(record)
        var entry = record.entryID.flatMap { id in LibraryModel.shared.entries.first { $0.id == id } }
            ?? (try? LibraryModel.inspect(folder.appendingPathComponent(record.launchPath)))
            ?? LibraryEntry(title: record.title, relativePath: launch.relativePath, bits: 0)
        entry.title = record.title
        entry.relativePath = launch.relativePath
        if entry.arguments.isEmpty || entry.arguments == record.arguments {
            entry.arguments = launch.arguments
            record.arguments = launch.arguments
        }
        entry.folderBytes = record.bytes
        LibraryModel.shared.save(entry)
        record.entryID = entry.id
    }

    // MARK: BCDContentDriver

    func blocker(_ key: BCDContentKey) -> String? {
        if !Self.installEnabled { return "Installing Epic games is switched off in this copy of BCD." }
        if !signedIn { return BCDEpicError.signedOut.errorDescription }
        if installs[key.id] != nil { return "This game is already installed." }
        return nil
    }

    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan {
        guard let item = item(key.id), item.kind == .game else { throw BCDEpicContentError.noBuild }
        let title = item.title ?? key.id
        let api = self.api
        let loaded: BCDEpicContent.Loaded
        do {
            let (asset, file) = Self.fetchers(api)
            loaded = try await BCDEpicContent.load(app: key.id, namespace: item.namespace, catalogItem: item.catalogItemId, preferring: Self.preferredCDN,
                                                   fetchAsset: asset, fetchFile: file)
        } catch BCDEpicError.rejected(let status, _) where status == 404 {
            throw BCDEpicContentError.noBuild
        } catch let error as BCDEpicManifestError {
            switch error {
            case .encrypted, .newer:
                refuse(.init(code: error.logReason, message: error.errorDescription ?? ""), app: key.id, title: title)
            default: break
            }
            throw error
        }
        folders[key.id] = loaded.bases
        if let stored, stored.version == loaded.build.buildVersion, pendingInfo(key.id)?.manifestHash == loaded.hash {
            BCDEpicLog.write("[epic-install] plan kept app=\(BCDEpicLog.app(key.id))")
            return stored
        }
        let folder = BCDContentPaths.folderName(item.folderName ?? "") ?? BCDContentPaths.folderName(title) ?? "Epic game"
        do {
            let made = try await BCDEpicContent.plan(key, title: title, folder: folder, loaded: loaded, item: item)
            try await savePending(made.info, manifest: loaded.file)
            return made.plan
        } catch let refused as BCDEpicRefused {
            refuse(refused.refusal, app: key.id, title: title)
            throw refused
        }
    }

    func source(for plan: BCDContentPlan) async throws -> BCDChunkSource {
        guard let bases = folders[plan.key.id], !bases.isEmpty, let item = item(plan.key.id) else { throw BCDEpicContentError.noManifest }
        let api = self.api, preferred = Self.preferredCDN
        // Where the chunks come from, for the log of the first device run: the CDNs' host names (public) and the shape of a path.
        let hosts = bases.compactMap(\.host).map { BCDEpicRules.sanitized($0, limit: 60) }
        let shape = plan.chunks.first.map { $0.locator.split(separator: "/").prefix(2).joined(separator: "/") } ?? "none"
        BCDEpicLog.write("[epic-cdn] chunks from \(hosts.prefix(4).joined(separator: ",")) path \(shape)")
        let url = BCDEpicContent.assetURL(namespace: item.namespace, catalogItem: item.catalogItemId, app: plan.key.id)
        return BCDEpicChunkSource(bases: bases, renew: {
            let (_, data) = try await api.get(url)
            guard let object = BCDEpicAuth.jsonObject(data) else { throw BCDEpicContentError.badAsset("json") }
            return BCDEpicContent.order(try BCDEpicContent.parseAsset(object).bases, preferring: preferred)
        })
    }

    func root(for key: BCDContentKey) -> BCDInstallRoot { Self.root }

    /// Eight chunks in flight (about 300 KiB each), and every file checked against its SHA-1 when its chunks are in.
    func options(for plan: BCDContentPlan) -> BCDContentOptions {
        var options = BCDContentOptions()
        options.streams = 8
        options.verifyFiles = true
        return options
    }

    func finish(_ plan: BCDContentPlan, stage: URL) async throws {
        guard let info = pendingInfo(plan.key.id) else { throw BCDEpicContentError.badAsset("pending") }
        let root = Self.root, base = Self.folder
        var record = try await Task.detached(priority: .utility) {
            try BCDEpicInstall.finish(plan, info: info, pendingManifest: BCDEpicInstall.pendingManifestFile(info.app, base: base),
                                      stage: stage, root: root, base: base)
        }.value
        makeEntry(&record)
        record.tall = item(record.app)?.tall
        record.wide = item(record.app)?.wide
        try BCDEpicInstall.save(record, base: base)
        installs[record.app] = record
        keepArt(record.app)
        if refusals.entries[record.app] != nil {
            refusals.forget(record.app)
            refusals.save(Self.refusalsFile)
        }
        try? FileManager.default.removeItem(at: BCDEpicInstall.pendingInfoFile(record.app, base: base))
    }

    func cancelled(_ key: BCDContentKey) {
        removePending(key.id)
    }

    // MARK: Covers

    /// An installed game's picture on the disk: the tall cover, or the wide picture (header and hero).
    static func artFile(_ app: String, wide: Bool) -> URL {
        folder.appendingPathComponent("art/\(app).\(wide ? "wide" : "tall")")
    }

    /// Keeps an installed game's pictures on the disk, once (they are public pictures from Epic's image servers; BCDArt's
    /// own cache may be emptied, and the list that names them goes at sign-out). Best effort: a failure leaves the
    /// addresses, which BCDArt fetches when it can.
    func keepArt(_ app: String) {
        guard let record = installs[app] else { return }
        let tall = item(app)?.tall ?? record.tall, wide = item(app)?.wide ?? record.wide
        let wanted: [(String?, URL)] = [(tall, Self.artFile(app, wide: false)), (wide, Self.artFile(app, wide: true))]
            .filter { !FileManager.default.fileExists(atPath: $0.1.path) }
        guard !wanted.isEmpty else { artKept.insert(app); return }
        Task.detached(priority: .utility) { [weak self] in
            var saved = 0
            for (address, file) in wanted {
                guard let address, let url = URL(string: address), url.scheme == "https", let data = await BCDArtFiles.data(for: url),
                      (try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)) != nil,
                      (try? data.write(to: file, options: .atomic)) != nil else { continue }
                saved += 1
            }
            guard let owner = self else { return }
            let total = saved
            await MainActor.run {
                BCDEpicLog.write("[epic-install] cover app=\(BCDEpicLog.app(app)) saved=\(total) of=\(wanted.count)")
                if total > 0 { owner.artKept.insert(app); owner.objectWillChange.send() }
            }
        }
    }

    // MARK: Pending (what a plan was made from, until the install is recorded)

    func pendingInfo(_ app: String) -> BCDEpicPlanInfo? {
        (try? Data(contentsOf: BCDEpicInstall.pendingInfoFile(app, base: Self.folder))).flatMap { try? JSONDecoder().decode(BCDEpicPlanInfo.self, from: $0) }
    }

    private func savePending(_ info: BCDEpicPlanInfo, manifest: Data) async throws {
        let base = Self.folder
        try await Task.detached(priority: .utility) {
            try FileManager.default.createDirectory(at: base.appendingPathComponent("pending", isDirectory: true), withIntermediateDirectories: true)
            try JSONEncoder().encode(info).write(to: BCDEpicInstall.pendingInfoFile(info.app, base: base), options: .atomic)
            try manifest.write(to: BCDEpicInstall.pendingManifestFile(info.app, base: base), options: .atomic)
        }.value
    }

    private func removePending(_ app: String) {
        try? FileManager.default.removeItem(at: BCDEpicInstall.pendingInfoFile(app, base: Self.folder))
        try? FileManager.default.removeItem(at: BCDEpicInstall.pendingManifestFile(app, base: Self.folder))
    }

    // MARK: Refusals

    /// A build the manifest shows BCD cannot install: remembered (the game's page says why), told to the player
    /// once, and the failed job cleared away a moment later.
    private func refuse(_ refusal: BCDEpicRules.Refusal, app: String, title: String) {
        refusals.record(refusal, app: app, now: Date(), build: Self.appBuild)
        refusals.save(Self.refusalsFile)
        BCDEpicLog.write("[epic-install] refused app=\(BCDEpicLog.app(app)) reason=\(BCDEpicRules.sanitized(refusal.code))")
        Task { @MainActor in
            tell("Can't install \(title). " + refusal.message)
            try? await Task.sleep(nanoseconds: 800_000_000)
            if case .failed(_)? = queue.job(key(app))?.state { queue.cancel(key(app)) }
        }
    }
}

/// Why Play cannot start an Epic game, in words for the player.
struct BCDEpicPlayRefusal: Error {
    var message: String
}
