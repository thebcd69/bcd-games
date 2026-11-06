// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// GOG in BCD, milestone 1 (docs/GOG.md; docs/research/open-fork-2026-10-18/gog.md §5 M1): sign-in
/// in GOG's own page, the owned Windows games with their pictures, the newest build downloaded
/// and installed into C:\GOG Games\<game> by the shared content engine (BCDContentQueue), the
/// build's play task as an ordinary library entry (Play is the library's start of a game added
/// from drive_c: no client in Wine), and uninstall. Engine-bound: BCD's interface reaches it only
/// through BCDLiveStore (BCDLiveStoreGOG.swift).
///
/// - `env.BCD_GOG = 0` hides everything GOG (installed GOG games stay as ordinary library games).
/// - `env.BCD_GOG_SFC = 1` cuts small files from their depot's small-files container (one fetch
///   instead of one per file); off until a device run shows the container downloads.
/// - `env.BCD_GOG_SCRIPT` (build 632; unset follows the install, 1 on, 0 off; also in a game's own config): the registry values of a
///   game's install script (`goggame-<id>.script`) are written into the prefix before each start (BCDGOGRegistry). Unset, only a game
///   BCD installed or repaired from build 632 on gets them; a game installed before starts as it always did.
/// - Not in M1: updates, verify and repair, DLC, a language picker, redistributables and GOG's
///   support setups (a build's redistributables are listed on its record), the Galaxy stand-in.
/// Log tags: [gog-signin], [gog-library], [gog-content], [gog-install] (product IDs, counts,
/// status: never a token, code, secret, account name or path).
@MainActor final class BCDGOG: ObservableObject, BCDContentDriver {
    static let shared = BCDGOG()

    /// GOG in BCD (on by default; nothing is fetched before a sign-in). 0 hides it everywhere.
    static var enabled: Bool { MadeiraConfig.flag("BCD_GOG", fallback: true) }
    /// Small files from their depot's container (one download for hundreds of files). Off by default.
    static var containers: Bool { MadeiraConfig.flag("BCD_GOG_SFC", fallback: false) }
    /// A game's redistributables (GOG's dependencies: Open AL, Visual C++ 2010-2013, ...) are downloaded into its folder with the
    /// game, and their installers run once at its first start (on by default). `env.BCD_GOG_SETUP = 0` in bcd.cfg downloads none for
    /// the games installed from then on; in a game's own config it runs none of that game's installers. It concerns only games
    /// installed or repaired from build 632 on (their record lists the installers): a game installed before runs none.
    static var redistributables: Bool { MadeiraConfig.flag("BCD_GOG_SETUP", fallback: true) }
    /// Owned DLC is installed with the game and by an update or a repair (on by default). `env.BCD_GOG_DLC = 0` in bcd.cfg installs the
    /// game alone and looks for no DLC when it checks for updates.
    static var dlc: Bool { MadeiraConfig.flag("BCD_GOG_DLC", fallback: true) }
    static let root = BCDInstallRoot(drive: "C", folder: "GOG Games")
    static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCD/GOG", isDirectory: true)
    }

    @Published private(set) var signedIn = false
    @Published private(set) var userName: String?
    @Published private(set) var signingIn = false
    @Published private(set) var products: [BCDGOGProduct] = []
    @Published private(set) var installs: [String: BCDGOGInstallRecord] = [:]
    @Published private(set) var refreshing = false
    @Published private(set) var updated: Date?
    @Published var error: String?
    /// Installed games GOG has an update for (a newer build, or owned DLC the game lacks), found when a game's page opened.
    @Published private(set) var updates: [String: BCDGOGContent.Available] = [:]
    private var checked: [String: Date] = [:]
    /// What the next plan of an installed game's job is (set by update() and repair(), kept until the job is done or cancelled; a
    /// job that outlived the app is read from its pending file).
    private var requested: [String: BCDGOGJobKind] = [:]

    let store = "gog"
    let storeTitle = "GOG"
    private let http = BCDHTTPStream()
    private lazy var session = BCDGOGSession(secrets: BCDKeychain.shared, http: http)
    private lazy var api = BCDGOGAPI(session: session, http: http)
    private var started = false
    private var watchers = Set<AnyCancellable>()
    private var artTask: Task<Void, Never>?
    private var queue: BCDContentQueue { BCDContentQueue.shared }

    private init() {}

    func key(_ id: String) -> BCDContentKey { BCDContentKey(store: store, id: id) }

    /// What BCD's store watches to show GOG's changes.
    var changes: AnyPublisher<Void, Never> {
        Publishers.Merge(objectWillChange.map { _ in () }, queue.$jobs.map { _ in () }).eraseToAnyPublisher()
    }

    /// Once per run, at the first look: the records, the cached list, the queue's wiring. No network
    /// until the list is stale (6 h) and someone is signed in.
    func start() {
        guard Self.enabled, !started else { return }
        started = true
        BCDContentLog.sink = { LogStore.shared.log($0) }
        installs = BCDGOGInstall.loadRecords(Self.folder)
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
        Task {
            let tokens = await session.current()
            signedIn = tokens != nil
            userName = tokens?.userName
            if let tokens { loadCache(account: BCDGOGCatalog.accountKey(tokens.userID)) }
            BCDContentLog.write("[gog-library] start signed-in=\(signedIn ? 1 : 0) cached=\(products.count) installed=\(installs.count)")
            await refreshLibrary(force: false)
        }
    }

    // MARK: Account

    func signIn() {
        guard Self.enabled, !signingIn else { return }
        start()
        let page = BCDWebSignIn.Page(
            title: "Sign in to GOG", start: BCDGOGAuth.loginURL, match: BCDGOGAuth.code(from:),
            pasteHint: "Sign in to GOG in Safari. When it's done, the page shows nothing or an error: copy the whole address from Safari's address bar and paste it here.")
        BCDWebSignIn.present(page) { [weak self] code in
            guard let self, let code else { return }
            Task { await self.finishSignIn(code) }
        }
    }

    private func finishSignIn(_ code: String) async {
        signingIn = true
        defer { signingIn = false }
        do {
            let tokens = try await session.signIn(code: code)
            signedIn = true
            userName = tokens.userName
            error = nil
            await refreshLibrary(force: true)
        } catch {
            tell("GOG didn't accept the sign-in. Try again, or use Safari from the sign-in page.")
            BCDContentLog.write("[gog-signin] failed reason=\(Self.reason(error))")
        }
    }

    /// GOG has no way to end a sign-in from outside: BCD forgets its tokens (they lapse on their own)
    /// and the list of games. Installed games stay and still start; their downloads pause.
    func signOut() {
        Task { await session.signOut() }
        for job in queue.jobs.values where job.key.store == store { queue.pause(job.key) }
        artTask?.cancel()
        artTask = nil
        signedIn = false
        userName = nil
        products = []
        updated = nil
        updates = [:]
        checked = [:]
        try? FileManager.default.removeItem(at: Self.folder.appendingPathComponent("library.json"))
        BCDContentLog.write("[gog-signin] signed out")
    }

    // MARK: Library

    func refreshLibrary(force: Bool) async {
        guard Self.enabled, signedIn, !refreshing, LibraryModel.shared.current == nil else { return }
        if !force, let updated, Date().timeIntervalSince(updated) < 6 * 3600 { return }
        refreshing = true
        defer { refreshing = false }
        do {
            var found: [BCDGOGProduct] = []
            var page = 1, pages = 1
            repeat {
                let reply = try BCDGOGCatalog.parsePage(await api.json(BCDGOGCatalog.pageURL(page), signedIn: true))
                found += reply.products
                pages = reply.pages
                page += 1
            } while page <= pages && page <= 200
            let known = Dictionary(products.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
            var seen = Set<String>()
            products = found.filter { seen.insert($0.id).inserted }.map { product in
                var product = product
                if let old = known[product.id] { product.cover = old.cover; product.background = old.background; product.artChecked = old.artChecked }
                return product
            }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
            updated = Date()
            error = nil
            await saveCache()
            BCDContentLog.write("[gog-library] refreshed games=\(products.count) pages=\(pages)")
            resolveArt()
        } catch {
            if case BCDGOGError.signInRefused = error { signedIn = false; userName = nil }
            let message = (error as? LocalizedError)?.errorDescription ?? "BCD couldn't read your GOG library. Try again later."
            self.error = message
            if force { tell(message) }   // asked for: say so; a refresh in the background stays quiet
            BCDContentLog.write("[gog-library] refresh failed reason=\(Self.reason(error))")
        }
    }

    /// The tall covers and backdrops from GOG's public game database, one game at a time in the
    /// background, once per game (kept with the list). Stops while a game runs.
    private func resolveArt() {
        guard artTask == nil else { return }
        let missing = products.filter { $0.artChecked == nil }.map(\.id)
        guard !missing.isEmpty else { return }
        artTask = Task { [weak self] in
            var resolved = 0
            for id in missing {
                guard let self, !Task.isCancelled, LibraryModel.shared.current == nil else { break }
                guard let reply = try? await self.api.json(BCDGOGCatalog.gamesDBURL(id), signedIn: false, limit: 4 << 20) else { continue }
                let art = BCDGOGCatalog.parseGamesDB(reply)
                if let i = self.products.firstIndex(where: { $0.id == id }) {
                    self.products[i].cover = art.cover
                    self.products[i].background = art.background
                    self.products[i].artChecked = Date()
                    resolved += 1
                }
            }
            await self?.saveCache()
            self?.artTask = nil
            BCDContentLog.write("[gog-library] art resolved=\(resolved) of=\(missing.count)")
        }
    }

    private func loadCache(account: String) {
        guard let data = try? Data(contentsOf: Self.folder.appendingPathComponent("library.json")),
              let cache = try? JSONDecoder().decode(BCDGOGCatalog.Cache.self, from: data),
              cache.version == 1, cache.account == account else { return }
        products = cache.products
        updated = cache.updated
    }

    private func saveCache() async {
        guard let tokens = await session.current() else { return }
        let cache = BCDGOGCatalog.Cache(account: BCDGOGCatalog.accountKey(tokens.userID), updated: updated ?? Date(), products: products)
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        try? JSONEncoder().encode(cache).write(to: Self.folder.appendingPathComponent("library.json"), options: .atomic)
    }

    func product(_ id: String) -> BCDGOGProduct? { products.first { $0.id == id } }

    // MARK: Install, play, uninstall

    func install(_ id: String) {
        guard Self.enabled, BCDGOGCatalog.validID(id), installs[id] == nil else { return }
        guard signedIn else { tell(BCDGOGError.signedOut.errorDescription!); return }
        start()
        queue.enqueue(key(id), title: product(id)?.title ?? queue.job(key(id))?.title ?? "GOG game")
    }

    // MARK: Updates, repair and DLC (build 632)

    /// What a job of this game is: an installed game never installs again.
    func kind(_ id: String) -> BCDGOGJobKind {
        guard installs[id] != nil else { return .install }
        let kind = requested[id] ?? pending(id)?.kind ?? .install
        return kind == .install ? .update : kind
    }

    /// A job runs for a game that is installed (an update or a repair, which Stop only stops).
    func isUpdating(_ id: String) -> Bool { installs[id] != nil && queue.job(key(id)) != nil }

    /// The Update button: the files that changed, and the owned DLC the game lacks.
    func update(_ id: String) { enqueueInstalled(id, .update) }

    /// The Repair button: every installed file is read and compared with the build.
    func repair(_ id: String) { enqueueInstalled(id, .repair) }

    /// Resume of a paused job: an installed game's update or repair goes on, an install is installed again.
    func resume(_ id: String) {
        if installs[id] != nil { enqueueInstalled(id, kind(id)) } else { install(id) }
    }

    private func enqueueInstalled(_ id: String, _ kind: BCDGOGJobKind) {
        guard Self.enabled, BCDGOGCatalog.validID(id), let record = installs[id] else { return }
        guard signedIn else { tell(BCDGOGError.signedOut.errorDescription!); return }
        guard LibraryModel.shared.current == nil, wine_process_is_running() == 0 else { tell("Close the game first."); return }
        start()
        if queue.job(key(id)) == nil || !queue.hasPartial(key(id)) { requested[id] = kind }
        BCDContentLog.write("[gog-install] \(requested[id]?.rawValue ?? kind.rawValue) asked product=\(id)")
        queue.enqueue(key(id), title: record.title)
    }

    /// Looks for an update of an installed game (when its page opens; at most once in six hours, never while a job runs for it, never
    /// signed out): GOG's build list, and for the same build the owned DLC the game lacks. A failure is logged and forgotten.
    func checkForUpdate(_ id: String, force: Bool = false) {
        guard Self.enabled, signedIn, let record = installs[id], queue.job(key(id)) == nil else { return }
        if !force, let last = checked[id], Date().timeIntervalSince(last) < 6 * 3600 { return }
        checked[id] = Date()
        let api = self.api
        let fetch: BCDGOGContent.Fetch = { url, signedIn in try await api.json(url, signedIn: signedIn, limit: 64 << 20) }
        Task { [weak self] in
            do {
                let found = try await BCDGOGContent.available(product: id, buildID: record.buildID, depots: record.depots, language: record.language,
                                                              dlc: Self.dlc, fetch: fetch)
                guard let self, self.installs[id]?.buildID == record.buildID else { return }
                if self.updates[id] != found { self.updates[id] = found }
                BCDContentLog.write("[gog-install] update check product=\(id) newer=\(found?.newerBuild == true ? 1 : 0) dlc=\(found?.dlc.count ?? 0)")
            } catch {
                self?.checked[id] = nil
                if !(error is CancellationError) { BCDContentLog.write("[gog-install] update check failed product=\(id) reason=\(Self.reason(error))") }
            }
        }
    }

    // MARK: Starting an installed game

    /// An `env.` switch for one game: its own config line (its settings file, then the entry's own lines), else bcd.cfg's; nil when
    /// none says. An empty value means unset.
    static func gameSwitch(_ name: String, for entry: LibraryEntry) -> Bool? {
        let key = "env." + name
        for text in [GameProfile(windowsPath: entry.windowsPath).get(key), MadeiraConfig.parse(entry.gameConfigText ?? "")[key], MadeiraConfig.get(key)] {
            if let text, !text.isEmpty { return text != "0" }
        }
        return nil
    }

    /// `env.BCD_GOG_SCRIPT` for one game (the install decides when none says, BCDGOGRegistry.applies).
    static func scriptSwitch(for entry: LibraryEntry) -> Bool? { gameSwitch("BCD_GOG_SCRIPT", for: entry) }

    /// The installed game whose folder holds the entry's program: the entry Play starts, or a program the player added from the
    /// game's folder (The Witcher's System\witcher.exe). Reads the records itself when the store has not started (no network, no queue).
    func record(forEntry entry: LibraryEntry) -> BCDGOGInstallRecord? {
        let all = started ? installs : BCDGOGInstall.loadRecords(Self.folder)
        if let own = all.values.first(where: { $0.entryID == entry.id }) { return own }
        let path = entry.relativePath.lowercased()
        return all.values.first { path.hasPrefix(($0.root.folder + "/" + $0.folder + "/").lowercased()) }
    }

    /// Called by ContentView.startLibraryEntry for every library start, before the launch is configured, while no session runs:
    /// - the install script's registry values of a GOG game go into the prefix (BCDGOGRegistry), for a game BCD installed or repaired
    ///   with the script in mind, or one the game's env.BCD_GOG_SCRIPT line asks for;
    /// - the redistributable installers a game BCD installed from build 632 on has not run yet go into the services batch that
    ///   starts it (LibraryEntry.setGOGSetup, GOGSetup.batchLines), unless the game's env.BCD_GOG_SETUP line is 0.
    /// Any other start is not touched.
    func prepareStart(_ entry: LibraryEntry) {
        guard Self.enabled, entry.desktop != true, let record = record(forEntry: entry), let folder = record.hostFolder,
              wineserver_is_running() == 0, wine_process_is_running() == 0 else { return }
        BCDContentLog.sink = { LogStore.shared.log($0) }
        if record.entryID == entry.id { prepareSetup(entry, record) }
        guard BCDGOGRegistry.applies(scripts: record.scripts, switch: Self.scriptSwitch(for: entry)) else { return }
        let prepared = BCDGOGRegistry.prepared(folder: folder, windowsFolder: record.windowsFolder, language: record.language)
        guard prepared.scripts > 0 || prepared.unreadable > 0 else { return }   // a game without a script: nothing to do, nothing to say
        guard !prepared.values.isEmpty else {
            BCDContentLog.write(BCDGOGRegistry.logLine(product: record.productID, prepared: prepared, outcome: nil))
            return
        }
        do {
            let outcome = try BCDGOGRegistry.apply(prepared.values, prefix: LibraryEntry.hostDrive.deletingLastPathComponent())
            BCDContentLog.write(BCDGOGRegistry.logLine(product: record.productID, prepared: prepared, outcome: outcome))
        } catch {
            BCDContentLog.write("[gog-script] product=\(record.productID) registry not written reason=\(Self.reason(error))")
        }
    }

    /// The batch lines of the game's redistributable installers not yet run in this prefix, or none (LibraryEntry.servicesScript
    /// starts the game as before when there are none).
    private func prepareSetup(_ entry: LibraryEntry, _ record: BCDGOGInstallRecord) {
        var lines: [String] = []
        if let programs = record.setup, !programs.isEmpty, Self.gameSwitch("BCD_GOG_SETUP", for: entry) ?? true {
            // A batch file is read with the console's code page (see LibraryEntry.servicesScriptEnvironment): a folder name outside ASCII
            // would not reach the installer, so such a game runs none (its files are downloaded all the same).
            if record.windowsFolder.allSatisfy(\.isASCII) {
                lines = GOGSetup.batchLines(programs, folder: record.windowsFolder, drive: LibraryEntry.hostDrive)
            } else {
                BCDContentLog.write("[gog-setup] installers not run product=\(record.productID) reason=folder-not-ascii")
            }
        }
        LibraryEntry.setGOGSetup(lines.isEmpty ? nil : lines.joined(separator: "\r\n"), for: entry.id)
        if !lines.isEmpty {
            BCDContentLog.write("[gog-setup] this start runs installers product=\(record.productID) count=\(lines.filter { $0.hasPrefix("echo [gog-setup]") }.count)")
        }
    }

    /// The library entry Play starts, made again if the library lost it.
    func playEntry(_ id: String) -> Result<LibraryEntry, BCDGOGPlayRefusal> {
        guard var record = installs[id] else { return .failure(.init(message: "This GOG game isn't installed.")) }
        // An update puts its files in place in one short step at its end: not while the game starts.
        if let job = queue.job(key(id)), job.state == .active, job.progress.phase == .finishing {
            return .failure(.init(message: "BCD is finishing this game's update. Try again in a moment."))
        }
        guard record.play != nil else { return .failure(.init(message: BCDGOGError.noPlayTask.errorDescription!)) }
        if record.entryID.flatMap({ id in LibraryModel.shared.entries.first { $0.id == id } }) == nil {
            makeEntry(&record)
            try? BCDGOGInstall.save(record, base: Self.folder)
            installs[id] = record
        }
        guard let entry = record.entryID.flatMap({ id in LibraryModel.shared.entries.first { $0.id == id } }) else {
            return .failure(.init(message: "BCD couldn't add this game to the library. Its folder is \(record.windowsFolder)."))
        }
        BCDContentLog.write("[gog-install] play product=\(id)")
        return .success(entry)
    }

    /// The game's files go (renamed out of sight at once, deleted in the background), with its
    /// record and library entry. Its saves in the Windows user profile stay.
    func uninstall(_ id: String) {
        if queue.job(key(id)) != nil { queue.cancel(key(id)) }
        guard let record = installs[id] else { return }
        guard LibraryModel.shared.current == nil, wine_process_is_running() == 0 else {
            tell("Close the game before uninstalling it.")
            return
        }
        if let entryID = record.entryID { LibraryModel.shared.remove(entryID) }
        BCDGOGInstall.delete(id, base: Self.folder)
        installs[id] = nil
        if let folder = record.hostFolder, let trash = record.root.stagingURL?.appendingPathComponent("trash", isDirectory: true) {
            Task { await BCDContentEngine.remove(folder, trash: trash) }
        }
        BCDContentLog.write("[gog-install] uninstalled product=\(id)")
    }

    /// The record's play task as a library entry: an existing entry keeps its settings.
    private func makeEntry(_ record: inout BCDGOGInstallRecord) {
        guard let play = record.play, let folder = record.hostFolder else { return }
        let launch = BCDGOGInstall.launch(record, play: play, program: play.path, working: play.workingDirectory)
        var entry = record.entryID.flatMap { id in LibraryModel.shared.entries.first { $0.id == id } }
            ?? (try? LibraryModel.inspect(folder.appendingPathComponent(play.path)))
            ?? LibraryEntry(title: record.title, relativePath: launch.relativePath, bits: 0)
        entry.title = record.title
        entry.relativePath = launch.relativePath
        entry.arguments = launch.arguments
        entry.workingDirectory = launch.workingDirectory
        if entry.launchMode == nil { entry.launchMode = launch.launchMode }
        entry.folderBytes = record.bytes
        LibraryModel.shared.save(entry)
        record.entryID = entry.id
    }

    /// Something the player asked for went wrong: BCD's interface shows the library's message.
    private func tell(_ message: String) { LibraryModel.shared.error = message }

    /// What an installed game's build names as redistributables, in words, for the game's page. A game installed from build 632 on
    /// says what BCD did with them (BCDGOGInstall.setupNote); an older one, that BCD did not download them.
    func note(_ id: String) -> String? {
        guard let record = installs[id], !record.dependencies.isEmpty else { return nil }
        if let setup = record.setup { return BCDGOGInstall.setupNote(setup: setup, notes: record.setupNotes ?? []) }
        let names = record.dependencies.prefix(6).map(Self.readable)
        return "May need: " + names.joined(separator: ", ") + ". BCD doesn't install these from GOG yet."
    }

    static func readable(_ dependency: String) -> String {
        var name = dependency
        let wide = name.hasSuffix("_x64")
        if wide { name.removeLast(4) }
        if name.hasPrefix("MSVC"), let year = Int(name.dropFirst(4)) { name = "Visual C++ \(year)" }
        else if name == "DirectX" { name = "DirectX (June 2010)" }
        else if name.lowercased().hasPrefix("dotnet") { name = ".NET " + String(name.dropFirst(6)) }
        return wide ? name + " (64-bit)" : name
    }

    static func reason(_ error: Error) -> String {
        if let error = error as? BCDGOGError { return error.logReason }
        return BCDContentQueue.reason(error)
    }

    // MARK: BCDContentDriver

    func blocker(_ key: BCDContentKey) -> String? {
        if !signedIn { return BCDGOGError.signedOut.errorDescription }
        // An installed game's update or repair is allowed (asked for just now, or found in the job's pending file after a restart).
        if installs[key.id] != nil, requested[key.id] == nil, (pending(key.id)?.kind ?? .install) == .install { return "This game is already installed." }
        return nil
    }

    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan {
        let api = self.api
        let fetch: BCDGOGContent.Fetch = { url, signedIn in try await api.json(url, signedIn: signedIn, limit: 64 << 20) }
        let id = key.id
        let record = installs[id]
        let kind: BCDGOGJobKind = record == nil ? .install : self.kind(id)
        let build = try await BCDGOGContent.newest(id, fetch: fetch)
        if let stored, stored.version == build.id, let info = pending(id), (info.kind ?? .install) == kind { return stored }
        let title = product(id)?.title ?? queue.job(key)?.title ?? "GOG game"
        // Owned DLC comes with the game; an update or a repair must have GOG's list (see BCDGOGContent.owned), and with DLC off keeps
        // the DLC the game already has.
        let owned: Set<String> = Self.dlc || kind != .install ? try await BCDGOGContent.owned(fetch: fetch, required: kind != .install) : []
        let dlc = Self.dlc ? owned : owned.intersection(record?.dlc ?? [])
        var current: BCDGOGContent.Current?
        if kind != .install, let record, let folder = record.hostFolder {
            let queue = self.queue
            current = BCDGOGContent.Current(kind: kind, folder: folder, oldDepots: record.depots, language: record.language,
                                            report: { done, total in Task { @MainActor in queue.planning(key, done: done, of: total) } })
        }
        let made = try await BCDGOGContent.plan(key, title: title, build: build, preferred: Locale.preferredLanguages,
                                                containers: Self.containers, redistributables: Self.redistributables,
                                                hasMsiexec: DockInstallers.bundleHasMsiexec, ownedDLC: dlc, current: current, fetch: fetch)
        try savePending(made.info)
        return made.plan
    }

    func source(for plan: BCDContentPlan) async throws -> BCDChunkSource {
        let api = self.api
        return BCDGOGChunkSource(fetch: { product in
            // The redistributables' chunks come from GOG's open link: no sign-in.
            product == BCDGOGContent.redistGroup
                ? try await api.json(BCDGOGContent.openLinkURL, signedIn: false, limit: 1 << 20)
                : try await api.json(BCDGOGContent.secureLinkURL(product), signedIn: true, limit: 1 << 20)
        })
    }

    func root(for key: BCDContentKey) -> BCDInstallRoot { Self.root }

    func options(for plan: BCDContentPlan) -> BCDContentOptions { BCDContentOptions() }

    func finish(_ plan: BCDContentPlan, stage: URL) async throws {
        let id = plan.key.id
        guard let info = pending(id) else { throw BCDGOGError.badReply("pending") }
        if let old = installs[id], (info.kind ?? .install) != .install {
            var record = try await Task.detached(priority: .utility) {
                try BCDGOGInstall.finishUpdate(plan, info: info, stage: stage, record: old).record
            }.value
            makeEntry(&record)
            try BCDGOGInstall.save(record, base: Self.folder)
            installs[id] = record
            // A repair runs the game's redistributable installers again at the next start; an update only the ones it brings.
            let had = Set((old.setup ?? []).map(\.id))
            let ids = (info.setup ?? []).map(\.id)
            GOGSetup.forget(info.kind == .repair ? ids : ids.filter { !had.contains($0) }, drive: LibraryEntry.hostDrive)
            updates[id] = nil
            checked[id] = Date()
        } else {
            let root = Self.root
            var record = try await Task.detached(priority: .utility) {
                try BCDGOGInstall.finish(plan, info: info, stage: stage, root: root)
            }.value
            makeEntry(&record)
            try BCDGOGInstall.save(record, base: Self.folder)
            installs[record.productID] = record
        }
        requested[id] = nil
        try? FileManager.default.removeItem(at: pendingURL(id))
    }

    func cancelled(_ key: BCDContentKey) {
        requested[key.id] = nil
        try? FileManager.default.removeItem(at: pendingURL(key.id))
    }

    private func pendingURL(_ id: String) -> URL { Self.folder.appendingPathComponent("pending/\(id).json") }

    private func pending(_ id: String) -> BCDGOGPlanInfo? {
        (try? Data(contentsOf: pendingURL(id))).flatMap { try? JSONDecoder().decode(BCDGOGPlanInfo.self, from: $0) }
    }

    private func savePending(_ info: BCDGOGPlanInfo) throws {
        let url = pendingURL(info.productID)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(info).write(to: url, options: .atomic)
    }
}

/// Why Play cannot start a GOG game, in words for the player.
struct BCDGOGPlayRefusal: Error {
    var message: String
}
