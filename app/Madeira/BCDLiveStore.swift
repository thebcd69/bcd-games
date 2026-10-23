// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import GameController
import SwiftUI
import UIKit
import os

/// BCD's interface in the app: the one BCDStore its screens read, filled from the engine, and
/// BCDActions carried out through the functions the classic library uses
/// (docs/BCD-UI-ARCHITECTURE.md). Engine-bound: the preview build leaves this file out
/// (tools/ui-preview.py, ENGINE_BOUND).
///
/// - Games: the library's own entries (games added from Files, and the Wine desktop) and the
///   Steam games Steam has installed or the account owns, merged as the classic library's Steam
///   section merges them (SteamGamesRules.items), with their downloads.
/// - Play: ContentView's launchLibraryEntry (the classic library's Play), handed over by
///   BCDLiveFrontEnd, with the entry the classic library passes: the stored entry, or for a
///   Steam game LibraryModel.steamEntry, after the checks of its Game details page
///   (SteamPlayCheck.blocker: the check the classic Game details page asked, moved unchanged).
/// - Downloads: Install, Update, Resume and Check files call SteamOwnedLibrary.install (repair)
///   at once, as the classic download sheet and Game details do, so its queue submits iOS 26's
///   continued-processing task (SteamDownloadBackground) while BCD is still in front. Nothing
///   here pauses, cancels or ends a download or that task on its own: only the player's Pause
///   and Stop do.
/// - A game runs from LibraryModel.begin until its session ends (LibraryModel.current): `inGame`
///   is true, the pictures and the interface's copies of the lists are dropped, and nothing is
///   rebuilt or read until it ends.
/// Log tag: [bcd-ui] (App IDs and counts only).
@MainActor final class BCDLiveStore: ObservableObject, BCDActions {
    static let shared = BCDLiveStore()

    let store = BCDStore()
    /// A screen of the existing interface an action opens (BCDLiveFrontEnd presents it).
    @Published var sheet: BCDLiveSheet?

    /// ContentView's Play (launchLibraryEntry) and Enable JIT, from BCDLiveFrontEnd.
    private var launch: ((LibraryEntry) -> Void)?
    private var requestJIT: (() -> Void)?
    /// ContentView's own BCD Dock start (BCDLiveFrontEnd hands it in): Settings › BCD Dock.
    var dockStart: ((DockGame, Bool) -> Void)?
    /// A Play waits for the page that asked for it to close.
    private var starting = false
    /// The entry BCD's Play last handed over and its game: a Steam game's entry may not be in the
    /// library yet, and a GOG game's entry is not its game's id, so LibraryModel.startingJIT alone
    /// cannot name the game.
    private var playing: (entry: UUID, game: String)?
    /// The game start(_:) is starting, for `launch` to note.
    private var startingGame: String?
    private var watchers = Set<AnyCancellable>()
    private var metadata: Task<Void, Never>?
    private var metadataRun = 0
    /// Store pictures by App ID, made again when the account's list changes.
    private var art: [Int: BCDLiveArt] = [:]
    /// The downloads as last seen, to tell a finished download from a cancelled one.
    private var seenDownloads: [Int: SteamOwnedLibrary.Download] = [:]
    private var finished: [BCDFinishedDownload] = []

    private init() {
        store.actions = self
        finished = Self.loadFinished()
        let library = LibraryModel.shared, games = SteamGamesModel.shared, account = SteamOwnedLibrary.shared
        let changes: [AnyPublisher<Void, Never>] = [
            library.$entries.map { _ in () }.eraseToAnyPublisher(),
            games.$games.map { _ in () }.eraseToAnyPublisher(),
            games.$builds.map { _ in () }.eraseToAnyPublisher(),
            account.$owned.map { _ in () }.eraseToAnyPublisher(),
            account.$downloads.map { _ in () }.eraseToAnyPublisher(),
            account.$playtime.map { _ in () }.eraseToAnyPublisher(),
            SteamSignInModel.shared.$accountName.map { _ in () }.eraseToAnyPublisher(),
            gogChanges,
        ]
        // @Published sends before the value is stored: the rebuild runs a moment later, and at
        // most a few times a second while a download reports its progress.
        Publishers.MergeMany(changes)
            .receive(on: DispatchQueue.main)
            .throttle(for: .milliseconds(400), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak self] in self?.rebuild() }
            .store(in: &watchers)
        account.$owned.sink { [weak self] _ in self?.art.removeAll() }.store(in: &watchers)
        // The classic library's Steam line "different saves for ...": the games Steam Cloud and this
        // device disagree on (SteamOwnedLibrary.cloudUndecided), read as the cloud state moves.
        account.$cloud.map { _ in () }
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.noteSavesDiffer() }
            .store(in: &watchers)
        account.$downloads.sink { [weak self] now in self?.noteFinished(now) }.store(in: &watchers)
        let device: [AnyPublisher<Void, Never>] = [
            LibraryJITState.shared.$enabled.map { _ in () }.eraseToAnyPublisher(),
            JITCoordinator.shared.$busy.map { _ in () }.eraseToAnyPublisher(),
            library.$startingJIT.map { _ in () }.eraseToAnyPublisher(),
            NotificationCenter.default.publisher(for: .GCControllerDidConnect).map { _ in () }.eraseToAnyPublisher(),
            NotificationCenter.default.publisher(for: .GCControllerDidDisconnect).map { _ in () }.eraseToAnyPublisher(),
            NotificationCenter.default.publisher(for: .GCControllerDidBecomeCurrent).map { _ in () }.eraseToAnyPublisher(),
        ]
        Publishers.MergeMany(device)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.refreshSystem() }
            .store(in: &watchers)
        // A game runs from LibraryModel.begin until its session ends (finish, or a failed launch).
        library.$current.sink { [weak self] current in self?.session(running: current != nil) }.store(in: &watchers)
        rebuild()
        refreshSystem()
    }

    // MARK: - From BCDLiveFrontEnd

    func connect(play: @escaping (LibraryEntry) -> Void, enableJIT: @escaping () -> Void) {
        // Every start (GOG, a game from Files or the desktop, Steam) hands its entry to ContentView's
        // Play through here, which first notes which game it is (`playing`).
        launch = { [weak self] entry in
            if let game = self?.startingGame { self?.playing = (entry.id, game) }
            play(entry)
        }
        requestJIT = enableJIT
    }

    /// The interface appeared (at start and after every session): what the classic library's
    /// Steam section does when it appears, then the lists and the device's state.
    func libraryAppeared() {
        SteamGamesModel.shared.refresh()
        if SteamOwnedLibrary.enabled { SteamOwnedLibrary.shared.start(); SteamOwnedLibrary.shared.reconcileSession() }
        rebuild()
        refreshSystem()
        refreshMetadata()
        LogStore.shared.log("[bcd-ui] shown games=\(store.games.count) downloads=\(store.downloads.count)")
    }

    /// BCD came back to the front.
    func becameActive() {
        SteamGamesModel.shared.refresh()
        if SteamOwnedLibrary.enabled { SteamOwnedLibrary.shared.reconcileSession() }
        refreshSystem()
    }

    // MARK: - A game runs

    private func session(running: Bool) {
        guard store.inGame != running else { return }
        store.inGame = running
        LogStore.shared.log("[bcd-ui] in-game=\(running ? 1 : 0)")
        if running {
            // Nothing reads files for the interface while a game runs, and the game gets the
            // memory the pictures and the interface's copies of the lists held (the shell draws
            // nothing meanwhile; they are made again when the game ends).
            metadata?.cancel()
            metadata = nil
            BCDArt.shared.purge()
            art.removeAll()
            store.startingJIT = nil
            store.savesDiffer = []
            store.games = []
            store.downloads = []
            store.finishedToday = []
        } else {
            DispatchQueue.main.async { [weak self] in
                self?.rebuild()
                self?.refreshSystem()
            }
        }
    }

    // MARK: - Games, downloads, accounts

    private func rebuild() {
        guard !store.inGame else { return }
        let library = LibraryModel.shared, account = SteamOwnedLibrary.shared
        var games: [BCDGame] = []
        var steamEntries: [Int: LibraryEntry] = [:]
        // Where each entry stands in the library's list (a later one is newer): Sort by › Recently added.
        var steamPositions: [Int: Int] = [:]
        var desktop = false
        for (position, entry) in library.entries.enumerated() {
            if let appID = entry.steamAppID {
                if steamEntries[appID] == nil { steamEntries[appID] = entry; steamPositions[appID] = position }
                continue
            }
            if entry.desktop == true {
                if desktop { continue }
                desktop = true
            }
            var game = localGame(entry)
            game.added = position
            games.append(game)
        }
        // The Wine desktop, as the classic library's Desktop button offers it before its first start.
        if !desktop { games.append(localGame(.desktopEntry)) }
        let owned = SteamOwnedLibrary.enabled ? account.owned : []
        let byID = Dictionary(owned.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var names: [Int: String] = [:]
        for item in SteamGamesRules.items(installed: SteamGamesModel.shared.games, owned: owned, search: "") {
            names[item.id] = item.name
            var game = steamGame(item, entry: steamEntries[item.id], owned: { byID[$0] })
            game.added = steamPositions[item.id]
            games.append(game)
        }
        gogMerge(&games)
        games.sort { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
        if store.games != games { store.games = games }

        let downloads = downloadList(names: names) + gogDownloads()
        if store.downloads != downloads { store.downloads = downloads }
        let today = finished.filter { Calendar.current.isDateInToday($0.finished) }
        if store.finishedToday != today { store.finishedToday = today }
        let accounts = [
            BCDAccount(store: .steam, name: SteamSignInModel.shared.accountName, available: SteamSignIn.isEnabled || MadeiraDock.enabled),
            // Not in this build yet.
            BCDAccount(store: .epic, name: nil, available: false),
            gogAccount(),
        ]
        if store.accounts != accounts { store.accounts = accounts }
        noteSavesDiffer()
        refreshStorage()
    }

    /// The games whose saves differ, as ids (SteamOwnedLibrary.cloudUndecided is what the classic
    /// library's Steam section reads); changed only when the list is.
    private func noteSavesDiffer() {
        guard !store.inGame else { return }
        let ids = SteamOwnedLibrary.cloudEnabled ? SteamOwnedLibrary.shared.cloudUndecided.map(BCDLiveID.steam) : []
        if store.savesDiffer != ids { store.savesDiffer = ids }
    }

    /// A game added from Files, or the Wine desktop.
    private func localGame(_ entry: LibraryEntry) -> BCDGame {
        let isDesktop = entry.desktop == true
        var game = BCDGame(id: BCDLiveID.local(entry.id), title: isDesktop ? "Windows desktop" : entry.title,
                           store: .local, state: .ready, size: entry.folderBytes)
        if entry.relativePath.hasPrefix("External/") { game.place = .externalDrive }
        game.lastPlayed = entry.lastPlayed
        game.thirtyTwoBit = entry.bits == 32
        if !isDesktop {
            game.recommended = GameRecommendations.match(windowsPath: entry.windowsPath, steamAppID: nil,
                                                         fileExists: GameRecommendations.driveFileExists) != nil
            game.autoTuned = Self.autoTuned(entry.windowsPath)
        }
        // The cover the player chose; else, for a game matched to a store page (Game details ›
        // Find on Steam), that page's public pictures, as the classic library shows them.
        if let cover = Self.chosenCover(entry.coverFile) {
            game.coverArt = [cover]
        } else if let id = entry.steamID {
            let pictures = Self.storePictures(id, owned: nil)
            game.coverArt = SteamCatalog.cover(id).map { [$0] } ?? []
            game.headerArt = pictures.header
            game.heroArt = pictures.hero
            game.logoArt = pictures.logo
        }
        return game
    }

    /// A Steam game: installed by Steam (Madeira's downloads or Valve's client), owned, or both.
    private func steamGame(_ item: SteamGamesRules.Item, entry: LibraryEntry?, owned: (Int) -> SteamOwnedGame?) -> BCDGame {
        let account = SteamOwnedLibrary.shared
        let installed = item.installed
        var size = entry?.folderBytes
        let state: BCDGameState
        if let download = account.downloads[item.id] {
            let progress = download.progress
            switch download.state {
            case .queued:
                state = .queued
            case .active:
                state = progress.phase == .finishing ? .installing(progress: progress.fraction) : .downloading(progress: progress.fraction)
            case .paused:
                // The bar stays where it stopped; the Downloads tab says Paused.
                state = .downloading(progress: progress.fraction)
            case .failed(let message):
                // A failed update leaves a game that cannot start until it finishes; a failed first
                // download leaves nothing to play, and Install tries again (the classic Try again).
                state = installed?.installed == true ? .broken(reason: message) : .notInstalled
            }
            if installed == nil, progress.totalBytes > 0 { size = Int64(clamping: progress.totalBytes) }
        } else if let installed {
            if !installed.installed {
                state = .broken(reason: SteamGamesRules.blocker(installed: false, client: true, signedIn: true) ?? "")
            } else if account.updateAvailable(appID: item.id, installedBuild: SteamGamesModel.shared.builds[item.id]) {
                state = .updateAvailable(bytes: 0)   // Steam names no size before the download starts
            } else {
                state = .ready
            }
        } else {
            state = .notInstalled
        }
        var game = BCDGame(id: BCDLiveID.steam(item.id), title: item.name, store: .steam, state: state, size: size)
        let playtime = account.playtime[item.id]
        let steamPlayed = playtime.flatMap { $0.lastPlayed > 0 ? Date(timeIntervalSince1970: TimeInterval($0.lastPlayed)) : nil }
        game.lastPlayed = [entry?.lastPlayed, steamPlayed].compactMap { $0 }.max()
        game.minutesPlayed = playtime?.minutes ?? 0
        game.thirtyTwoBit = entry?.bits == 32
        let folder = installed?.windowsInstallPath ?? "C:\\steamapps\\common\\" + (item.owned?.installDir ?? String(item.id))
        game.recommended = GameRecommendations.match(windowsPath: folder, steamAppID: item.id,
                                                     fileExists: GameRecommendations.driveFileExists) != nil
        game.autoTuned = Self.autoTuned(entry?.windowsPath ?? folder)
        let pictures = steamArt(item.id, owned: owned)
        // A cover the player chose for the game (its page) comes first, the store's after it.
        game.coverArt = (Self.chosenCover(entry?.coverFile).map { [$0] } ?? []) + pictures.cover
        game.headerArt = pictures.header
        game.heroArt = pictures.hero
        game.logoArt = pictures.logo
        return game
    }

    /// The cover the player chose (Documents/bcd-art), or nil when none is chosen or its file is
    /// gone. Its change time is in the address, so a picture chosen again under the same name is
    /// read again instead of BCDArt's copy of the old one.
    static func chosenCover(_ name: String?) -> URL? {
        guard let name else { return nil }
        let file = LibraryModel.documents.appendingPathComponent("bcd-art/" + URL(fileURLWithPath: name).lastPathComponent)
        guard let changed = (try? file.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate else { return nil }
        var address = URLComponents(url: file, resolvingAgainstBaseURL: false)
        address?.queryItems = [URLQueryItem(name: "changed", value: String(Int64(changed.timeIntervalSince1970 * 1000)))]
        return address?.url ?? file
    }

    /// Auto-tune found the lines the game plays with. Off, nothing is read.
    private static func autoTuned(_ windowsPath: String) -> Bool {
        AutoTune.enabled && AutoTune.isOn(windowsPath) && AutoTune.state(windowsPath).status == "found"   // madeira-bcd: BCD's interface shows a tuned game; off, nothing is read
    }

    /// The covers the classic library's Steam cards try (SteamGamesRules.artwork), then the
    /// store's header, hero and logo: the owned game's own asset when Steam named one, then the
    /// public CDN's.
    private func steamArt(_ appID: Int, owned: (Int) -> SteamOwnedGame?) -> BCDLiveArt {
        if let known = art[appID] { return known }
        let pictures = Self.storePictures(appID, owned: owned(appID))
        let made = BCDLiveArt(cover: SteamGamesRules.artwork(appID: appID, owned: owned),
                              header: pictures.header, hero: pictures.hero, logo: pictures.logo)
        art[appID] = made
        return made
    }

    static func storePictures(_ appID: Int, owned: SteamOwnedGame?) -> (header: [URL], hero: [URL], logo: [URL]) {
        let cdn = "https://cdn.cloudflare.steamstatic.com/steam/apps/\(appID)/"
        func asset(_ name: String?) -> URL? {
            guard let name, SteamGamesRules.safeAssetName(name) else { return nil }
            return URL(string: SteamGamesRules.assetBase + "\(appID)/" + name)
        }
        return ([asset(owned?.headerImage), URL(string: cdn + "header.jpg")].compactMap { $0 },
                [asset(owned?.libraryHero), URL(string: cdn + "library_hero.jpg")].compactMap { $0 },
                [URL(string: cdn + "logo.png")].compactMap { $0 })
    }

    /// Steam's downloads: the running one first, then the queue, the paused and the failed ones.
    private func downloadList(names: [Int: String]) -> [BCDDownload] {
        let rows = SteamOwnedLibrary.shared.downloads.map { appID, download -> (rank: Int, item: BCDDownload) in
            let progress = download.progress
            var item = BCDDownload(id: BCDLiveID.steam(appID), title: names[appID] ?? "Steam game", store: .steam,
                                   doneBytes: Int64(clamping: progress.doneBytes), totalBytes: Int64(clamping: progress.totalBytes),
                                   bytesPerSecond: download.state == .active ? progress.bytesPerSecond : 0)
            let rank: Int
            switch download.state {
            case .active: rank = 0
            case .queued: rank = 1
            case .paused: rank = 2; item.paused = true
            case .failed(let message): rank = 3; item.failed = message
            }
            return (rank, item)
        }
        return rows.sorted { a, b in
            a.rank != b.rank ? a.rank < b.rank : a.item.title.localizedStandardCompare(b.item.title) == .orderedAscending
        }.map(\.item)
    }

    /// A download that left the list while it was finishing went in; one that was paused,
    /// cancelled or removed did not.
    private func noteFinished(_ now: [Int: SteamOwnedLibrary.Download]) {
        defer { seenDownloads = now }
        var added = false
        for (appID, before) in seenDownloads where now[appID] == nil {
            guard before.state == .active, before.progress.phase == .finishing else { continue }
            let title = SteamOwnedLibrary.shared.game(appID)?.name
                ?? SteamGamesModel.shared.games.first { $0.id == appID }?.name ?? "Steam game"
            finished.insert(BCDFinishedDownload(id: BCDLiveID.steam(appID), title: title,
                                                bytes: Int64(clamping: before.progress.totalBytes), finished: Date()), at: 0)
            added = true
            LogStore.shared.log("[bcd-ui] download finished app=\(appID)")
        }
        guard added else { return }
        finished = Array(finished.filter { Calendar.current.isDateInToday($0.finished) }.prefix(20))
        Self.saveFinished(finished)
    }

    private struct Finished: Codable { var id: String; var title: String; var bytes: Int64; var finished: Date }
    private static let finishedKey = "bcd.ui.finishedDownloads"

    private static func loadFinished() -> [BCDFinishedDownload] {
        guard let data = UserDefaults.standard.data(forKey: finishedKey),
              let list = try? JSONDecoder().decode([Finished].self, from: data) else { return [] }
        return list.filter { Calendar.current.isDateInToday($0.finished) }
            .map { BCDFinishedDownload(id: $0.id, title: $0.title, bytes: $0.bytes, finished: $0.finished) }
    }

    private static func saveFinished(_ list: [BCDFinishedDownload]) {
        let stored = list.map { Finished(id: $0.id, title: $0.title, bytes: $0.bytes, finished: $0.finished) }
        if let data = try? JSONEncoder().encode(stored) { UserDefaults.standard.set(data, forKey: finishedKey) }
    }

    /// What the classic library's cards read as they appear: a game's install size and graphics
    /// API (LibraryModel.refreshMetadata, at most once a day) and an installed Steam game's
    /// 32/64-bit, API and size (refreshSteamMetadata, once per install). One at a time, and
    /// stopped when a game starts.
    private func refreshMetadata() {
        guard !store.inGame, metadata == nil else { return }
        let library = LibraryModel.shared, account = SteamOwnedLibrary.shared
        let added = library.entries.filter { $0.steamAppID == nil && $0.desktop != true }.map(\.id)
        let steam = SteamGamesModel.shared.games.filter { $0.installed && account.downloads[$0.id] == nil }
        guard !added.isEmpty || !steam.isEmpty else { return }
        metadataRun += 1
        let run = metadataRun
        metadata = Task { [weak self] in
            for id in added where !Task.isCancelled { await library.refreshMetadata(id) }
            for game in steam where !Task.isCancelled {
                await library.refreshSteamMetadata(game, title: account.game(game.id)?.name ?? game.name)
            }
            if self?.metadataRun == run { self?.metadata = nil }
        }
    }

    // MARK: - The device

    private func refreshSystem() {
        guard !store.inGame else { return }
        var system = store.system
        // The classic library's JIT state (LibraryJITState: StikJITHelper.ready, every 2 s).
        system.jit = LibraryJITState.shared.enabled ? .on
            : (LibraryModel.shared.startingJIT != nil || JITCoordinator.shared.busy) ? .waiting : .off
        // The game whose Play is turning JIT on (the classic Play button's "Enabling JIT").
        let waiting = LibraryModel.shared.startingJIT.flatMap(gameID)
        if store.startingJIT != waiting { store.startingJIT = waiting }
        // The limit does not change while BCD runs: measured once.
        if system.memoryLimitBytes == nil {
            system.memoryLimitBytes = Self.memoryLimit()
            let entitlements = EntitlementStatus.check()
            system.increasedMemoryLimit = entitlements.increasedMemory
            // The measured address map, not the entitlement string: the map ends at 63 GB without
            // extended virtual addressing and at 454-512 GB with it, while the entitlement check read 0
            // on 253 of 364 tester logs, most of them 512 GB installs that play large games (and 1 on a
            // 63 GB one, DVXWGA). Unknown: no note.
            var low: UInt64 = 0, high: UInt64 = 0
            system.extendedAddressing = jit_task_map_range(&low, &high) ? high >= 128 << 30 : true
        }
        let pad = GCController.current.flatMap { $0.extendedGamepad != nil ? $0 : nil }
            ?? GCController.controllers().first { $0.extendedGamepad != nil }
        system.controllerName = pad.map(Self.controllerName)
        system.controllerBattery = pad?.battery.flatMap { battery in
            battery.batteryState == .unknown || !battery.batteryLevel.isFinite ? nil : Int((battery.batteryLevel * 100).rounded())
        }
        let space = Self.deviceSpace()
        storageAsked = Date()
        system.freeDeviceBytes = space.free
        system.totalDeviceBytes = space.total
        system.freeExternalBytes = nil   // the engine has no external drive yet (docs/HARICI-SSD.md)
        if store.system != system { store.system = system }
    }

    /// The free room, from a rebuild: at once when a download, an install or a library entry came
    /// or went, else at most every few seconds. A download's progress rebuilds the lists a few
    /// times a second, and iOS works out the room it could free on each ask (refreshSystem, on
    /// appearing and on coming back, always asks).
    /// The BCD game of a library entry id (LibraryModel.startingJIT): the entry BCD's Play handed
    /// over, else the library's entry of that id (a Play from an existing page), else none.
    private func gameID(_ entry: UUID) -> String? {
        if let playing, playing.entry == entry { return playing.game }
        if let stored = LibraryModel.shared.entries.first(where: { $0.id == entry }) {
            return stored.steamAppID.map(BCDLiveID.steam) ?? BCDLiveID.local(stored.id)
        }
        return entry == LibraryEntry.desktopID ? BCDLiveID.local(entry) : nil
    }

    private func refreshStorage() {
        let key = "\(SteamOwnedLibrary.shared.downloads.keys.sorted())|\(SteamGamesModel.shared.games.count)|\(LibraryModel.shared.entries.count)"
        guard key != storageKey || Date().timeIntervalSince(storageAsked) > 5 else { return }
        storageKey = key
        storageAsked = Date()
        let space = Self.deviceSpace()
        guard store.system.freeDeviceBytes != space.free || store.system.totalDeviceBytes != space.total else { return }
        store.system.freeDeviceBytes = space.free
        store.system.totalDeviceBytes = space.total
    }
    private var storageAsked = Date.distantPast
    private var storageKey = ""

    private static func deviceSpace() -> (free: Int64?, total: Int64?) {
        let values = try? URL.documentsDirectory.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey,
                                                                           .volumeTotalCapacityKey])
        return (values?.volumeAvailableCapacityForImportantUsage, values?.volumeTotalCapacity.map(Int64.init))
    }

    /// What iOS lets BCD use: what is already charged (phys_footprint) plus what remains before
    /// jetsam (os_proc_available_memory), as the performance overlay measures it.
    private static func memoryLimit() -> Int64? {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        let available = os_proc_available_memory()
        guard result == KERN_SUCCESS, available > 0 else { return nil }
        return Int64(info.phys_footprint) + Int64(available)
    }

    private static func controllerName(_ pad: GCController) -> String {
        let category = pad.productCategory
        if !category.isEmpty, category != "MFi" { return category }
        return pad.vendorName ?? "Controller"
    }

    // MARK: - BCDActions

    func play(_ game: BCDGame) {
        guard !starting, LibraryModel.shared.current == nil else { return }
        starting = true
        settled { [weak self] in
            self?.starting = false
            self?.start(game)
        }
    }

    /// Runs `work` once nothing is presented over the interface: a BCD page that asked for it
    /// (the game's sheet) closes first, so what the work shows (the session, an alert, an
    /// existing screen) comes up over BCD's interface itself. At most a second's wait.
    private func settled(_ work: @escaping () -> Void, tries: Int = 0) {
        let presented = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow?.rootViewController?.presentedViewController }.first
        guard presented != nil, tries < 20 else { work(); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in self?.settled(work, tries: tries + 1) }
    }

    private func start(_ game: BCDGame) {
        guard let launch else { return }
        startingGame = game.id
        if let entry = gogPlayEntry(game) { launch(entry); return }
        let library = LibraryModel.shared
        switch BCDLiveID.parse(game.id) {
        case .local(let id)?:
            guard let entry = library.entries.first(where: { $0.id == id })
                    ?? (id == LibraryEntry.desktopID ? LibraryEntry.desktopEntry : nil) else {
                library.error = "This game isn't in your library anymore."
                return
            }
            LogStore.shared.log("[bcd-ui] play \(entry.desktop == true ? "desktop" : "local")")
            launch(entry)
        case .steam(let appID)?:
            // The entry the classic Steam card opens Game details with, and that page's own check
            // before Play (SteamPlayCheck.blocker: an update still downloading, then Steam's
            // rules for BCD Dock or "The game").
            guard let installed = SteamGamesModel.shared.games.first(where: { $0.id == appID }) else {
                // Steam's records changed under the card: BCD reads them again at once (the
                // classic library's pull to refresh), so the card shows what is there now.
                refreshLibrary()
                library.error = "Steam no longer lists this game as installed. BCD is reading Steam's installs again; try once more in a moment."
                return
            }
            let title = SteamOwnedLibrary.enabled ? SteamOwnedLibrary.shared.game(appID)?.name ?? installed.name : installed.name
            let entry = library.steamEntry(installed, title: title)
            if let blocker = SteamPlayCheck.blocker(entry) {
                library.error = blocker
                return
            }
            LogStore.shared.log("[bcd-ui] play app=\(appID)")
            launch(entry)
        case nil:
            // Epic: the engine starts no game of its own yet, and the store lists none (rebuild).
            // Its Play plugs in where GOG's does (gogPlayEntry, above): a library entry handed to
            // the same connected Play.
            return
        }
    }

    func install(_ game: BCDGame, place: BCDInstallPlace) {
        if gog(.install, game.id) { return }
        // A game from Files is added, not installed. Epic: its downloads plug in here once the
        // engine has them (the store lists none of its games until then).
        guard case .steam(let appID)? = BCDLiveID.parse(game.id) else { return }
        if place == .externalDrive { LogStore.shared.log("[bcd-ui] no external drive in this build: app=\(appID) goes to the device") }
        download(appID)
    }

    func update(_ game: BCDGame) {
        if gog(.update, game.id) { return }
        guard case .steam(let appID)? = BCDLiveID.parse(game.id) else { return }
        download(appID)
    }

    /// Install, Update and Resume: SteamOwnedLibrary.install at once, as the classic download
    /// sheet's buttons call it, so its queue starts the download (and submits iOS 26's
    /// continued-processing task) while BCD is in front: a player who leaves BCD right after
    /// tapping keeps the download running. Only a start that can only fail (no Steam library, no
    /// sign-in) waits for the page to close, so the Steam message shows over BCD's interface.
    private func download(_ appID: Int) {
        let account = SteamOwnedLibrary.shared
        if SteamOwnedLibrary.enabled && account.signedIn {
            account.install(appID)
        } else {
            settled { account.install(appID) }
        }
    }

    /// Steam's Check and repair files, for a game BCD downloaded itself (as its Game details page
    /// offers it): at once, as a download.
    func verify(_ game: BCDGame) {
        if gog(.verify, game.id) { return }
        guard case .steam(let appID)? = BCDLiveID.parse(game.id) else { return }
        guard let installed = SteamGamesModel.shared.games.first(where: { $0.id == appID }),
              SteamOwnedLibrary.enabled, SteamInstallPaths.isManaged(library: installed.library) else {
            settled { LibraryModel.shared.error = "BCD checks only the Steam games it downloaded itself." }
            return
        }
        SteamOwnedLibrary.shared.repair(appID)
    }

    /// A Steam game BCD downloaded goes with its files (Game details › Uninstall); a game added
    /// from Files leaves the library, its files and saves staying in drive_c (Game details › Remove).
    func uninstall(_ game: BCDGame) {
        if gog(.uninstall, game.id) { return }
        switch BCDLiveID.parse(game.id) {
        case .steam(let appID)?:
            settled {
                guard let installed = SteamGamesModel.shared.games.first(where: { $0.id == appID }),
                      SteamInstallPaths.isManaged(library: installed.library) else {
                    LibraryModel.shared.error = "BCD removes only the Steam games it downloaded itself."
                    return
                }
                SteamOwnedLibrary.shared.uninstall(installed)
            }
        case .local(let id)?:
            guard id != LibraryEntry.desktopID else { return }
            LibraryModel.shared.remove(id)
        case nil:
            return
        }
    }

    func pauseDownload(_ id: String) {
        if gog(.pause, id) { return }
        guard case .steam(let appID)? = BCDLiveID.parse(id) else { return }
        SteamOwnedLibrary.shared.pause(appID)
    }

    func resumeDownload(_ id: String) {
        if gog(.resume, id) { return }
        guard case .steam(let appID)? = BCDLiveID.parse(id) else { return }
        download(appID)
    }

    /// As the classic download sheet's Cancel download: a first install loses its partial files,
    /// an update of an installed game is only stopped.
    func cancelDownload(_ id: String) {
        if gog(.cancel, id) { return }
        guard case .steam(let appID)? = BCDLiveID.parse(id) else { return }
        SteamOwnedLibrary.shared.cancelInstall(appID, installed: SteamGamesModel.shared.games.contains { $0.id == appID })
    }

    func enableJIT() { settled { [weak self] in self?.requestJIT?() } }

    /// Settings › BCD Dock's start: ContentView's own, once nothing covers the main screen.
    func startDock(_ game: DockGame, _ compactPool: Bool) { settled { [weak self] in self?.dockStart?(game, compactPool) } }

    func signIn(_ front: BCDStoreFront) {
        if gogAccountAction(signIn: true, front) { return }
        guard front == .steam else { return }   // Epic is not in this build yet: its sign-in plugs in here
        settled { [weak self] in self?.sheet = .steamSignIn }
    }

    func signOut(_ front: BCDStoreFront) {
        if gogAccountAction(signIn: false, front) { return }
        guard front == .steam else { return }
        SteamSignInModel.shared.signOut()
    }

    func addGameFromFiles() { settled { [weak self] in self?.sheet = .addGame } }

    func refreshLibrary() {
        Task { await SteamGamesModel.refreshAll() }
        gogRefresh()
        rebuild()
    }
}

/// The screens BCD's actions open over the shell (BCDLiveFrontEnd presents them).
enum BCDLiveSheet: Identifiable {
    case steamSignIn
    /// "+": a program in drive_c, then the game's page.
    case addGame
    /// A game's page (the "+" flow, and a Steam Cloud choice).
    case details(LibraryEntry)

    var id: String {
        switch self {
        case .steamSignIn: return "steam-sign-in"
        case .addGame: return "add-game"
        case .details(let entry): return "details." + entry.id.uuidString
        }
    }
}

/// BCDGame ids: "steam.<App ID>" and "local.<library entry id>".
enum BCDLiveID {
    enum Key: Equatable { case steam(Int), local(UUID) }
    static func steam(_ appID: Int) -> String { "steam.\(appID)" }
    static func local(_ id: UUID) -> String { "local." + id.uuidString }
    static func parse(_ id: String) -> Key? {
        if id.hasPrefix("steam."), let appID = Int(id.dropFirst(6)) { return .steam(appID) }
        if id.hasPrefix("local."), let uuid = UUID(uuidString: String(id.dropFirst(6))) { return .local(uuid) }
        return nil
    }
}

private struct BCDLiveArt {
    var cover: [URL]
    var header: [URL]
    var hero: [URL]
    var logo: [URL]
}
