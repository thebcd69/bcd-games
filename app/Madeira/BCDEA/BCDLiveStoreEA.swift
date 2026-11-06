// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// The experimental EA store in BCD's interface: what BCDLiveStore adds for it through one-line hooks (BCD's screens never name the
/// store's code; docs/BCD-UI-ARCHITECTURE.md). Engine-bound, and outside app/Madeira's top level, so the preview build
/// (tools/ui-preview.py) never compiles it. The store's own code is app/Madeira/SwiftEA/ (docs/EA.md).
///
/// It is OFF until the player turns it on (Settings › Accounts › EA games; `env.BCD_EA = 1`). Off, every hook does nothing: no EA call
/// in the library, in a start, in the accounts list; nothing is read, fetched, written or listened to.
///
/// - Games: the account's EA games ("ea.<offer ID>"), installed ones first by state, with EA's pictures. An installed EA game is a
///   library entry: the store's own entry for it (built as any library game, with its recommended and auto-tune marks) becomes the EA game.
/// - Play: the installed game's library entry, started by the same Play as any library game; ContentView's start first asks
///   EALibrary.preLaunch for the licence, the registry keys, the EA app's environment and the stand-in service.
/// - Install, update, repair, pause, resume, cancel and uninstall go to EALibrary, whose queue downloads one game at a time and pauses for
///   a game session.
enum BCDEAAction {
    case install, update, repair, uninstall, pause, resume, cancel
}

/// How the EA store tells BCDLiveStore that something changed (its list, an install, a download, the switch), and how its error
/// messages reach the library's one alert. Bound the first time the store is on.
@MainActor final class BCDEASignal {
    static let shared = BCDEASignal()
    let subject = PassthroughSubject<Void, Never>()
    private var watchers = Set<AnyCancellable>()
    private var bound = false
    /// A code is being traded for the tokens.
    var signingIn = false

    func bind() {
        guard !bound, EALibrary.enabled else { return }
        bound = true
        let ea = EALibrary.shared
        ea.objectWillChange.sink { [subject] _ in subject.send() }.store(in: &watchers)
        ea.$error.compactMap { $0 }.receive(on: DispatchQueue.main).sink { message in
            LibraryModel.shared.error = message
            EALibrary.shared.error = nil
        }.store(in: &watchers)
    }

    func changed() {
        bind()
        subject.send()
    }
}

extension BCDLiveStore {
    /// Hook in init's `changes`: always the same publisher; it says something once the store is on (BCDEASignal.bind).
    var eaChanges: AnyPublisher<Void, Never> {
        BCDEASignal.shared.subject.eraseToAnyPublisher()
    }

    /// Hook in rebuild(), after itch.io's merge: the library entries of installed EA games become EA games, and the account's other EA
    /// games (downloading or not installed) join them.
    func eaMerge(_ games: inout [BCDGame]) {
        guard EALibrary.enabled else { return }
        BCDEASignal.shared.bind()
        let ea = EALibrary.shared
        ea.start()
        var byEntry: [String: String] = [:]
        for (id, record) in ea.installed {
            if let entry = record.entryID { byEntry[BCDLiveID.local(entry)] = id }
        }
        var placed = Set<String>()
        for i in games.indices {
            guard let id = byEntry[games[i].id], let record = ea.installed[id] else { continue }
            var game = games[i]
            game.id = Self.eaID(id)
            game.store = .ea
            game.title = record.title
            game.size = record.installBytes
            eaApply(cover: record.cover ?? ea.game(id)?.cover, wide: ea.game(id)?.wide, to: &game, keepCover: true)
            game.state = eaState(id)
            games[i] = game
            placed.insert(id)
        }
        // Installed games whose entry is gone (it is made again at Play), downloads, owned games.
        var others: [(id: String, title: String, cover: URL?, wide: URL?)] = ea.installed.values.filter { !placed.contains($0.offerID) }
            .map { ($0.offerID, $0.title, $0.cover ?? ea.game($0.offerID)?.cover, ea.game($0.offerID)?.wide) }
        var seen = placed.union(others.map(\.id))
        for game in ea.owned where seen.insert(game.id).inserted { others.append((game.id, game.title, game.cover, game.wide)) }
        for id in ea.downloads.keys where seen.insert(id).inserted {
            others.append((id, ea.game(id)?.title ?? "EA game", ea.game(id)?.cover, ea.game(id)?.wide))
        }
        for other in others {
            var game = BCDGame(id: Self.eaID(other.id), title: other.title, store: .ea, state: eaState(other.id), size: ea.installed[other.id]?.installBytes)
            if ea.installed[other.id] == nil {
                if case .known(_, let install)? = ea.sizes[other.id], install > 0 { game.size = install }
                if let progress = ea.downloads[other.id]?.progress, progress.totalBytes > 0 { game.size = Int64(clamping: progress.totalBytes) }
            }
            eaApply(cover: other.cover, wide: other.wide, to: &game, keepCover: false)
            games.append(game)
        }
    }

    /// Hook in rebuild(): the EA store's downloads, after itch.io's.
    func eaDownloads() -> [BCDDownload] {
        guard EALibrary.enabled else { return [] }
        let ea = EALibrary.shared
        let rows = ea.downloads.map { id, download -> (Int, BCDDownload) in
            let progress = download.progress
            var item = BCDDownload(id: Self.eaID(id), title: ea.installed[id]?.title ?? ea.game(id)?.title ?? "EA game", store: .ea,
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

    /// Hook in rebuild(): the EA account row. Nothing while the store is off.
    func eaAccounts() -> [BCDAccount] {
        guard EALibrary.enabled else { return [] }
        let ea = EALibrary.shared
        return [BCDAccount(store: .ea, name: ea.signedIn ? (ea.accountName ?? "EA account") : nil, available: ea.signedIn)]
    }

    /// Hook in start(_:), after itch.io's: an EA game's library entry, or nil for any other game (and for an EA game that cannot start,
    /// with the reason shown as the library's error).
    func eaPlayEntry(_ game: BCDGame) -> LibraryEntry? {
        guard EALibrary.enabled, let id = Self.eaGame(game.id) else { return nil }
        switch EALibrary.shared.playEntry(id) {
        case .success(let entry): return entry
        case .failure(let refusal): LibraryModel.shared.error = refusal.message; return nil
        }
    }

    /// Hook at the top of install, update, uninstall, verify (repair), pauseDownload, resumeDownload and cancelDownload: true when the id
    /// is an EA game's (the action is then done here).
    func ea(_ action: BCDEAAction, _ id: String) -> Bool {
        guard EALibrary.enabled, let game = Self.eaGame(id) else { return false }
        let ea = EALibrary.shared
        switch action {
        case .install, .update, .resume:
            ea.install(game)
        case .repair:
            ea.repair(game)
        case .uninstall:
            ea.uninstall(game)
        case .pause:
            ea.pause(game)
        case .cancel:
            ea.cancel(game)
        }
        return true
    }

    /// Hook at the top of signIn(_:) and signOut(_:): true for the EA store. Sign in is EA's own login page (BCDWebSignIn).
    func eaAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool {
        guard front == .ea, EALibrary.enabled else { return false }
        if signIn { signInToEA() } else {
            EALibrary.shared.signOut()
            BCDEASignal.shared.changed()
        }
        return true
    }

    /// Hook in refreshLibrary(): the EA list read again.
    func eaRefresh() {
        guard EALibrary.enabled, EALibrary.shared.signedIn else { return }
        Task { await EALibrary.shared.refreshLibrary(interactive: true) }
    }

    /// Hook in libraryAppeared(): the list is read if it is stale, downloads continue after a session.
    func eaAppeared() {
        guard EALibrary.enabled else { return }
        BCDEASignal.shared.bind()
        EALibrary.shared.start()
        EALibrary.shared.reconcileSession()
    }

    /// Hook in becameActive().
    func eaBecameActive() {
        guard EALibrary.enabled else { return }
        EALibrary.shared.reconcileSession()
    }

    /// For the game's page: what to know about an EA game ("An EA game needs the sign-in to start", what is not available).
    func eaNote(_ id: String) -> String? {
        guard EALibrary.enabled, let game = Self.eaGame(id), EALibrary.shared.installed[game] != nil else { return nil }
        let signedIn = EALibrary.shared.signedIn
        return (signedIn ? "" : "Sign in to EA in Settings › Accounts to play or update this game: an EA game needs the sign-in to start. ")
            + "BCD starts the game the way the EA app would: it fetches the game's license for your account and stands in for the EA app's local service while the game runs. There is no EA app here, so friends, the overlay and cloud saves are not available."
    }

    /// For the game's page, before Install: why BCD cannot install the game (nil: it can), and what to know first.
    func eaInstallAdvice(_ id: String) -> (blocker: String?, warning: String?) {
        guard EALibrary.enabled, let game = Self.eaGame(id) else { return (nil, nil) }
        let ea = EALibrary.shared
        guard ea.installed[game] == nil, ea.downloads[game] == nil else { return (nil, nil) }
        if let owned = ea.game(game), !owned.downloadable {
            return ("EA does not offer a PC download of this game. It stays in the list, but BCD cannot install it.", nil)
        }
        var note = "Games with EA's kernel anti-cheat (Battlefield 2042 and later, EA SPORTS FC, Madden) cannot run in BCD."
        if case .unavailable(let reason)? = ea.sizes[game] { note += " EA did not say how large this game is: \(reason)" }
        return (nil, note)
    }

    /// For the game's page: the library entry an installed EA game is, or nil.
    func eaEntryID(_ id: String) -> UUID? {
        guard EALibrary.enabled, let game = Self.eaGame(id) else { return nil }
        return EALibrary.shared.installed[game]?.entryID
    }

    /// For the game's page: whether Check files (repair) is offered.
    func eaCanRepair(_ id: String) -> Bool {
        guard EALibrary.enabled, let game = Self.eaGame(id) else { return false }
        let ea = EALibrary.shared
        return ea.installed[game] != nil && ea.downloads[game] == nil && ea.signedIn
    }

    /// Hook in the game page's `opened`: the game's size before Install, the newest build for an installed game (Update).
    func eaOpened(_ id: String) {
        guard EALibrary.enabled, let game = Self.eaGame(id) else { return }
        let ea = EALibrary.shared
        if ea.installed[game] != nil { Task { await ea.checkForUpdate(game) } } else { Task { await ea.loadSize(game) } }
    }

    /// Hook in the game page's watch: the page reads its details again when the EA store's state moves.
    var eaDetailChanges: AnyPublisher<Void, Never> {
        EALibrary.enabled ? EALibrary.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher() : Empty<Void, Never>().eraseToAnyPublisher()
    }

    // MARK: Sign-in

    /// EA's own login page, in BCD's sign-in sheet. It ends on the EA app's private address (qrc:///html/login_successful.html) with a
    /// one-use code: the sheet stops that navigation (the matcher sees every address before the page goes there), reads the code and closes;
    /// the code is traded for the tokens at once and never logged. "Use Safari" is the way around a login the web view cannot finish: the
    /// address it ends on is pasted back.
    private func signInToEA() {
        let ea = EALibrary.shared
        guard !BCDEASignal.shared.signingIn else { return }
        let page = BCDWebSignIn.Page(
            title: "Sign in to EA", start: ea.loginURL(),
            match: { url in EAAPI.isRedirectPage(url) ? EAAPI.authorizationCode(in: url.absoluteString) : nil },
            pasteHint: "Sign in to EA in Safari. Safari cannot open the page EA ends on, but its address bar shows the address (it starts with qrc:///html/login_successful.html): copy the whole address and paste it here.",
            pastePlaceholder: "qrc:///html/login_successful.html?code=…",
            pasteProblem: "That isn't the address EA's sign-in ends on. It starts with qrc:///html/login_successful.html and has a code in it.")
        BCDWebSignIn.present(page) { code in
            guard let code else { return }
            Task { @MainActor in
                BCDEASignal.shared.signingIn = true
                defer { BCDEASignal.shared.signingIn = false }
                do {
                    try await ea.signIn(code: code)
                    BCDEASignal.shared.changed()
                } catch {
                    EALog.event("[ea-signin] failed reason=\((error as? EAAPIError)?.reason ?? String(describing: type(of: error)))")
                    LibraryModel.shared.error = "EA didn't accept the sign-in (\(error.localizedDescription)) Try again, or use Safari from the sign-in page."
                }
            }
        }
    }

    // MARK: Helpers

    static func eaID(_ offer: String) -> String { "ea." + offer }

    /// The offer of an EA game id: EA's offer IDs are letters, digits, dots, dashes and underscores ("Origin.OFR.50.0001234").
    static func eaGame(_ id: String) -> String? {
        guard id.hasPrefix("ea.") else { return nil }
        let offer = String(id.dropFirst(3))
        guard !offer.isEmpty, offer.count <= 120,
              offer.unicodeScalars.allSatisfy({ ($0.value < 128) && (CharacterSet.alphanumerics.contains($0) || ".-_".unicodeScalars.contains($0)) }) else { return nil }
        return offer
    }

    /// EA's pictures: the tall pack art as the cover, the wide key art as the header and hero. A cover the player chose for the entry stays first.
    private func eaApply(cover: URL?, wide: URL?, to game: inout BCDGame, keepCover: Bool) {
        let chosen = keepCover ? game.coverArt.filter(\.isFileURL) : []
        game.coverArt = chosen + [cover, wide].compactMap { $0 }
        game.headerArt = [wide, cover].compactMap { $0 }
        game.heroArt = [wide].compactMap { $0 }
        game.logoArt = []
    }

    private func eaState(_ id: String) -> BCDGameState {
        let ea = EALibrary.shared
        if let download = ea.downloads[id] {
            let progress = download.progress
            switch download.state {
            case .queued: return .queued
            case .active:
                return progress.phase == .finishing || progress.phase == .verifying
                    ? .installing(progress: progress.fraction) : .downloading(progress: progress.fraction)
            case .paused: return .downloading(progress: progress.fraction)   // the Downloads tab says Paused
            case .failed(let message): return ea.installed[id] != nil ? .broken(reason: message) : .notInstalled
            }
        }
        guard ea.installed[id] != nil else { return .notInstalled }
        return ea.updateAvailable(id) ? .updateAvailable(bytes: 0) : .ready   // EA names no size before the download starts
    }
}
