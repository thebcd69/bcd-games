// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// itch.io in BCD's interface: what BCDLiveStore adds for itch.io through one-line hooks (BCD's
/// screens never name the store's code; docs/BCD-UI-ARCHITECTURE.md). Engine-bound, and outside
/// app/Madeira's top level, so the preview build (tools/ui-preview.py) never compiles it. The store's
/// own code is app/Madeira/SwiftItch/ (docs/ITCH.md).
///
/// - Games: the account's itch.io games ("itch.<game ID>"): the ones it owns, the ones in its collections
///   and the free ones added with Find a game, installed ones first by state, with itch.io's cover. An
///   installed itch.io game is a library entry: the store's own entry for it (built as any library game,
///   with its recommended and auto-tune marks) becomes the itch.io game.
/// - Play: the installed game's library entry, started by the same Play as any library game (an
///   installer's entry first runs the installer in the Wine desktop, ItchSetup).
/// - Install, update, pause, resume, cancel and uninstall go to ItchLibrary, whose queue downloads one
///   game at a time and pauses for a game session.
/// Nothing here touches ItchLibrary until there is something of itch.io on the device (ItchLibrary.active:
/// the player signed in once or a game is installed): a player who never uses itch.io costs it no Keychain
/// read, no file and no network. `env.BCD_ITCH = 0` hides everything.
enum BCDItchAction {
    case install, update, uninstall, pause, resume, cancel
}

/// How itch.io tells BCDLiveStore that something changed (its list, an install, a download), and how its
/// error messages reach the library's one alert. Bound the first time itch.io is in use.
@MainActor final class BCDItchSignal {
    static let shared = BCDItchSignal()
    let subject = PassthroughSubject<Void, Never>()
    private var watchers = Set<AnyCancellable>()
    private var bound = false

    /// Starts listening to ItchLibrary (once), when there is something of itch.io on the device.
    func bind() {
        guard !bound, ItchLibrary.active else { return }
        bound = true
        let itch = ItchLibrary.shared
        itch.objectWillChange.sink { [subject] _ in subject.send() }.store(in: &watchers)
        // The library's message (an alert BCD's interface shows) is where "Sign in to itch.io to download games" and a refused key go.
        itch.$error.compactMap { $0 }.receive(on: DispatchQueue.main).sink { message in
            LibraryModel.shared.error = message
            ItchLibrary.shared.error = nil
        }.store(in: &watchers)
    }

    /// The interface is asked to read itch.io again (the player signed in or out).
    func changed() {
        bind()
        subject.send()
    }
}

extension BCDLiveStore {
    /// Hook in init's `changes`: itch.io's account, list, installs and downloads. Always the same publisher; it says
    /// something once ItchLibrary is in use (BCDItchSignal.bind), so nothing of itch.io is read before.
    var itchChanges: AnyPublisher<Void, Never> {
        BCDItchSignal.shared.subject.eraseToAnyPublisher()
    }

    /// Hook in rebuild(), after Epic's merge: the library entries of installed itch.io games become itch.io games, and
    /// the account's other games (downloading or not installed) join them.
    func itchMerge(_ games: inout [BCDGame]) {
        guard ItchLibrary.active else { return }
        BCDItchSignal.shared.bind()
        let itch = ItchLibrary.shared
        itch.start()
        var byEntry: [String: Int] = [:]
        for (id, record) in itch.installed {
            if let entry = record.entryID { byEntry[BCDLiveID.local(entry)] = id }
        }
        var placed = Set<Int>()
        for i in games.indices {
            guard let id = byEntry[games[i].id], let record = itch.installed[id] else { continue }
            var game = games[i]
            game.id = Self.itchID(id)
            game.store = .itch
            game.title = record.title
            game.size = record.installBytes
            itchApply(record.cover ?? itch.game(id)?.cover, to: &game, keepCover: true)
            game.state = itchState(id)
            games[i] = game
            placed.insert(id)
        }
        // Installed games whose entry is gone (it is made again at Play), downloads, owned games.
        var others: [(id: Int, title: String, cover: URL?)] = itch.installed.values.filter { !placed.contains($0.gameID) }
            .map { ($0.gameID, $0.title, $0.cover ?? itch.game($0.gameID)?.cover) }
        var seen = placed.union(others.map(\.id))
        for game in itch.owned where seen.insert(game.id).inserted { others.append((game.id, game.title, game.cover)) }
        for id in itch.downloads.keys where seen.insert(id).inserted { others.append((id, itch.game(id)?.title ?? "itch.io game", itch.game(id)?.cover)) }
        for other in others {
            var game = BCDGame(id: Self.itchID(other.id), title: other.title, store: .itch, state: itchState(other.id),
                               size: itch.installed[other.id]?.installBytes)
            if itch.installed[other.id] == nil, case .upload(let upload)? = itch.choices[other.id], upload.size > 0 { game.size = upload.size }
            if let progress = itch.downloads[other.id]?.progress, itch.installed[other.id] == nil, progress.totalBytes > 0 {
                game.size = Int64(clamping: progress.totalBytes)
            }
            itchApply(other.cover, to: &game, keepCover: false)
            games.append(game)
        }
    }

    /// Hook in rebuild(): itch.io's downloads, after Epic's (`... + epicDownloads() + itchDownloads()`).
    func itchDownloads() -> [BCDDownload] {
        guard ItchLibrary.active else { return [] }
        let itch = ItchLibrary.shared
        let rows = itch.downloads.map { id, download -> (Int, BCDDownload) in
            let progress = download.progress
            var item = BCDDownload(id: Self.itchID(id), title: itch.installed[id]?.title ?? itch.game(id)?.title ?? "itch.io game", store: .itch,
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
        return rows.sorted { $0.0 != $1.0 ? $0.0 < $1.0 : $0.1.title.localizedStandardCompare($1.1.title) == .orderedAscending }.map(\.1)
    }

    /// Hook in rebuild(): the itch.io account row. Nothing until itch.io is in use. Signed out (after a sign-out) the row has no
    /// name: a game's page then says downloading needs the sign-in.
    func itchAccounts() -> [BCDAccount] {
        guard ItchLibrary.active else { return [] }
        let itch = ItchLibrary.shared
        return [BCDAccount(store: .itch, name: itch.signedIn ? (itch.accountName ?? "itch.io account") : nil, available: itch.signedIn)]
    }

    /// Hook in start(_:), after Epic's: an itch.io game's library entry, or nil for any other game (and for an itch.io game that
    /// cannot start, with the reason shown as the library's error).
    func itchPlayEntry(_ game: BCDGame) -> LibraryEntry? {
        guard ItchLibrary.active, let id = Self.itchGame(game.id) else { return nil }
        switch ItchLibrary.shared.playEntry(id) {
        case .success(let entry): return entry
        case .failure(let refusal): LibraryModel.shared.error = refusal.message; return nil
        }
    }

    /// Hook at the top of install, update, uninstall, pauseDownload, resumeDownload and cancelDownload: true when the id is an
    /// itch.io game's (the action is then done here). Check files has no itch.io form (the archive is checked as it is unpacked).
    func itch(_ action: BCDItchAction, _ id: String) -> Bool {
        guard ItchLibrary.active, let game = Self.itchGame(id) else { return false }
        let itch = ItchLibrary.shared
        switch action {
        case .install, .update, .resume:
            itch.install(game)
        case .uninstall:
            itch.uninstall(game)
        case .pause:
            itch.pause(game)
        case .cancel:
            itch.cancel(game)
        }
        return true
    }

    /// Hook at the top of signIn(_:) and signOut(_:): true for itch.io. Sign in asks for the API key in BCD's own sheet.
    func itchAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool {
        guard front == .itch, ItchLibrary.enabled else { return false }
        if signIn {
            presentItchKey()
        } else {
            ItchLibrary.shared.signOut()
            BCDItchSignal.shared.changed()
        }
        return true
    }

    /// Hook in refreshLibrary(): itch.io's list read again.
    func itchRefresh() {
        guard ItchLibrary.active, ItchLibrary.shared.signedIn else { return }
        Task { await ItchLibrary.shared.refreshLibrary(interactive: true) }
    }

    /// Hook in libraryAppeared(): downloads continue after a session; the list is read if it is stale (ItchLibrary.start).
    func itchAppeared() {
        guard ItchLibrary.active else { return }
        BCDItchSignal.shared.bind()
        ItchLibrary.shared.start()
        ItchLibrary.shared.reconcileSession()
    }

    /// Hook in becameActive().
    func itchBecameActive() {
        guard ItchLibrary.active else { return }
        ItchLibrary.shared.reconcileSession()
    }

    /// For the game's page, once installed or before: what to know ("Play runs this game's installer..."), or nil.
    func itchNote(_ id: String) -> String? {
        guard ItchLibrary.active, let game = Self.itchGame(id), let record = ItchLibrary.shared.installed[game] else { return nil }
        if record.setup != nil, ItchLibrary.shared.downloads[game] == nil {
            return "This game comes with an installer. Play runs it once in the Wine desktop, into C:\\" + record.folder.replacingOccurrences(of: "/", with: "\\")
                + " (some installers ask a question or two). When it closes, leave the session: BCD finds the game it installed, and from then on Play starts the game."
        }
        if !ItchLibrary.shared.signedIn { return "Sign in to itch.io in Settings › Accounts to update this game. It plays without a sign-in." }
        return nil
    }

    /// For the game's page: the library entry an installed itch.io game is, or nil.
    func itchEntryID(_ id: String) -> UUID? {
        guard ItchLibrary.active, let game = Self.itchGame(id) else { return nil }
        return ItchLibrary.shared.installed[game]?.entryID
    }

    /// For the game's page, before Install: why BCD cannot install the game (nil: it can), and what to know first. The upload BCD would install
    /// is looked up when the page opens (itchOpened), so these read what it found.
    func itchInstallAdvice(_ id: String) -> (blocker: String?, warning: String?) {
        guard ItchLibrary.active, let game = Self.itchGame(id) else { return (nil, nil) }
        let itch = ItchLibrary.shared
        guard itch.installed[game] == nil, itch.downloads[game] == nil else { return (nil, nil) }
        switch itch.choices[game] {
        case .unavailable(let reason)?:
            if let owned = itch.game(game), !owned.owned, (owned.minPrice ?? 0) > 0 {
                return ("This game is not free and this itch.io account has not bought it. Buy it on its store page, then refresh the library.", nil)
            }
            return (reason, nil)
        case .upload(let upload)?:
            let name = upload.filename.lowercased()
            let packed = name.hasSuffix(".zip") || name.hasSuffix(".7z"), program = name.hasSuffix(".exe") || name.hasSuffix(".msi")
            // A butler build's download is a .zip of the build whatever the upload's name (ItchLibrary.run).
            if !packed && !program && upload.buildID == 0 {
                return (ItchInstallError.unsupportedFile("." + ((upload.filename as NSString).pathExtension.isEmpty ? "unknown" : (upload.filename as NSString).pathExtension)).localizedDescription, nil)
            }
            var notes: [String] = []
            if upload.demo { notes.append("itch.io only offers a demo of this game for Windows.") }
            if program { notes.append("If this file is an installer, Play runs it once in the Wine desktop, into C:\\itch; after that, Play starts the game.") }
            return (nil, notes.isEmpty ? nil : notes.joined(separator: " "))
        case nil:
            return (nil, nil)
        }
    }

    /// Hook in the game page's `opened`: the upload BCD would install (the size before Install, the update check after).
    func itchOpened(_ id: String) {
        guard ItchLibrary.active, let game = Self.itchGame(id) else { return }
        let itch = ItchLibrary.shared
        Task { await itch.loadChoice(game, force: itch.installed[game] != nil) }
    }

    /// Hook in the game page's watch: the page reads its details again when itch.io's state moves.
    var itchDetailChanges: AnyPublisher<Void, Never> {
        ItchLibrary.active ? ItchLibrary.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher() : Empty<Void, Never>().eraseToAnyPublisher()
    }

    /// Library › the itch.io chip: Find a game (BCDActions.openStoreTool).
    func presentItchFind() {
        guard ItchLibrary.enabled, ItchLibrary.shared.signedIn else { return }
        ItchLibrary.shared.start()
        present(.itchFind)
    }

    func presentItchKey() {
        guard ItchLibrary.enabled else { return }
        present(.itchKey)
    }

    // MARK: Helpers

    static func itchID(_ game: Int) -> String { "itch.\(game)" }

    static func itchGame(_ id: String) -> Int? {
        guard id.hasPrefix("itch."), let game = Int(id.dropFirst(5)), game > 0 else { return nil }
        return game
    }

    /// itch.io's cover; a cover the player chose for the entry stays first. The cover is the page's only picture (itch.io has no tall
    /// cover, hero or logo).
    private func itchApply(_ cover: URL?, to game: inout BCDGame, keepCover: Bool) {
        let chosen = keepCover ? game.coverArt.filter(\.isFileURL) : []
        game.coverArt = chosen + [cover].compactMap { $0 }
        game.headerArt = [cover].compactMap { $0 }
        game.heroArt = []
        game.logoArt = []
    }

    private func itchState(_ id: Int) -> BCDGameState {
        let itch = ItchLibrary.shared
        if let download = itch.downloads[id] {
            let progress = download.progress
            switch download.state {
            case .queued: return .queued
            case .active: return progress.phase == .unpacking ? .installing(progress: progress.fraction) : .downloading(progress: progress.fraction)
            case .paused: return .downloading(progress: progress.fraction)   // the Downloads tab says Paused
            case .failed(let message): return itch.installed[id] != nil ? .broken(reason: message) : .notInstalled
            }
        }
        guard itch.installed[id] != nil else { return .notInstalled }
        return itch.updateAvailable(id) ? .updateAvailable(bytes: 0) : .ready   // itch.io names no size before the download starts
    }
}
