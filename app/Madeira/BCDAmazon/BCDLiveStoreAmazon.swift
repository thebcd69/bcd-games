// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// The experimental Amazon Games store in BCD's interface (Porthole port, item 13): what BCDLiveStore adds for it through one-line hooks, the
/// way BCDEA/BCDLiveStoreEA.swift does for the EA store (BCD's screens never name the store's code; docs/BCD-UI-ARCHITECTURE.md). Engine-bound,
/// and outside app/Madeira's top level, so the preview build never compiles it. The store's own code is app/Madeira/SwiftAmazon/.
///
/// It is OFF until the player turns it on (Settings › Accounts › Amazon Games; `env.BCD_AMAZON = 1`). Off, every hook does nothing: no Amazon
/// call in the library, in a start, in the accounts list; nothing is read, fetched, written or listened to.
///
/// - Games: the account's Amazon games ("amazon.<product ID>"), installed ones first by state, with Amazon's pictures. An installed game is a
///   library entry: the store's own entry for it becomes the Amazon game.
/// - Play: the installed game's library entry, started by the same Play as any library game.
/// - Install, pause, resume, cancel and uninstall go to AmazonLibrary, whose queue downloads one game at a time and pauses for a game session.
enum BCDAmazonAction {
    case install, uninstall, pause, resume, cancel
}

/// How the Amazon store tells BCDLiveStore that something changed, and how its error messages reach the library's one alert.
@MainActor final class BCDAmazonSignal {
    static let shared = BCDAmazonSignal()
    let subject = PassthroughSubject<Void, Never>()
    private var watchers = Set<AnyCancellable>()
    private var bound = false
    /// A code is being traded for the tokens.
    var signingIn = false

    func bind() {
        guard !bound, AmazonLibrary.enabled else { return }
        bound = true
        let amazon = AmazonLibrary.shared
        amazon.objectWillChange.sink { [subject] _ in subject.send() }.store(in: &watchers)
        amazon.$error.compactMap { $0 }.receive(on: DispatchQueue.main).sink { message in
            LibraryModel.shared.error = message
            AmazonLibrary.shared.error = nil
        }.store(in: &watchers)
    }

    func changed() {
        bind()
        subject.send()
    }
}

extension BCDLiveStore {
    /// Hook in init's `changes`: always the same publisher; it says something once the store is on.
    var amazonChanges: AnyPublisher<Void, Never> {
        BCDAmazonSignal.shared.subject.eraseToAnyPublisher()
    }

    /// Hook in rebuild(), after the EA store's merge: the library entries of installed Amazon games become Amazon games, and the account's
    /// other games (downloading or not installed) join them.
    func amazonMerge(_ games: inout [BCDGame]) {
        guard AmazonLibrary.enabled else { return }
        BCDAmazonSignal.shared.bind()
        let amazon = AmazonLibrary.shared
        amazon.start()
        var byEntry: [String: String] = [:]
        for (id, record) in amazon.installed {
            if let entry = record.entryID { byEntry[BCDLiveID.local(entry)] = id }
        }
        var placed = Set<String>()
        for i in games.indices {
            guard let id = byEntry[games[i].id], let record = amazon.installed[id] else { continue }
            var game = games[i]
            game.id = Self.amazonID(id)
            game.store = .amazon
            game.title = record.game.title
            game.size = record.installBytes
            amazonApply(record.game, to: &game, keepCover: true)
            game.state = amazonState(id)
            games[i] = game
            placed.insert(id)
        }
        var others: [AmazonGame] = amazon.installed.values.filter { !placed.contains($0.game.id) }.map(\.game)
        var seen = placed.union(others.map(\.id))
        for game in amazon.owned where seen.insert(game.id).inserted { others.append(game) }
        for id in amazon.downloads.keys where seen.insert(id).inserted {
            if let game = amazon.game(id) { others.append(game) }
        }
        for other in others {
            var game = BCDGame(id: Self.amazonID(other.id), title: other.title, store: .amazon, state: amazonState(other.id),
                               size: amazon.installed[other.id]?.installBytes)
            if amazon.installed[other.id] == nil, let progress = amazon.downloads[other.id]?.progress, progress.totalBytes > 0 {
                game.size = Int64(clamping: progress.totalBytes)
            }
            amazonApply(other, to: &game, keepCover: false)
            games.append(game)
        }
    }

    /// Hook in rebuild(): the Amazon store's downloads, after the EA store's.
    func amazonDownloads() -> [BCDDownload] {
        guard AmazonLibrary.enabled else { return [] }
        let amazon = AmazonLibrary.shared
        let rows = amazon.downloads.map { id, download -> (Int, BCDDownload) in
            let progress = download.progress
            var item = BCDDownload(id: Self.amazonID(id), title: amazon.game(id)?.title ?? "Amazon game", store: .amazon,
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

    /// Hook in rebuild(): the Amazon account row. Nothing while the store is off.
    func amazonAccounts() -> [BCDAccount] {
        guard AmazonLibrary.enabled else { return [] }
        let amazon = AmazonLibrary.shared
        return [BCDAccount(store: .amazon, name: amazon.signedIn ? (amazon.accountName ?? "Amazon account") : nil, available: amazon.signedIn)]
    }

    /// Hook in start(_:), after the EA store's: an Amazon game's library entry, or nil for any other game (and for an Amazon game that cannot
    /// start, with the reason shown as the library's error).
    func amazonPlayEntry(_ game: BCDGame) -> LibraryEntry? {
        guard AmazonLibrary.enabled, let id = Self.amazonGame(game.id) else { return nil }
        switch AmazonLibrary.shared.playEntry(id) {
        case .success(let entry): return entry
        case .failure(let refusal): LibraryModel.shared.error = refusal.message; return nil
        }
    }

    /// Hook at the top of install, update, uninstall, pauseDownload, resumeDownload and cancelDownload: true when the id is an Amazon game's.
    func amazon(_ action: BCDAmazonAction, _ id: String) -> Bool {
        guard AmazonLibrary.enabled, let game = Self.amazonGame(id) else { return false }
        let amazon = AmazonLibrary.shared
        switch action {
        case .install, .resume: amazon.install(game)
        case .uninstall: amazon.uninstall(game)
        case .pause: amazon.pause(game)
        case .cancel: amazon.cancel(game)
        }
        return true
    }

    /// Hook at the top of signIn(_:) and signOut(_:): true for the Amazon store. Sign in is Amazon's own login page (BCDWebSignIn).
    func amazonAccountAction(signIn: Bool, _ front: BCDStoreFront) -> Bool {
        guard front == .amazon, AmazonLibrary.enabled else { return false }
        if signIn { signInToAmazon() } else {
            AmazonLibrary.shared.signOut()
            BCDAmazonSignal.shared.changed()
        }
        return true
    }

    /// Hook in refreshLibrary(): the list read again.
    func amazonRefresh() {
        guard AmazonLibrary.enabled else { return }
        AmazonLibrary.shared.refresh(interactive: true)
    }

    /// Hook in libraryAppeared().
    func amazonAppeared() {
        guard AmazonLibrary.enabled else { return }
        BCDAmazonSignal.shared.bind()
        AmazonLibrary.shared.start()
        AmazonLibrary.shared.reconcileSession()
    }

    /// Hook in becameActive().
    func amazonBecameActive() {
        guard AmazonLibrary.enabled else { return }
        AmazonLibrary.shared.reconcileSession()
    }

    /// For the game's page: what to know about an installed Amazon game.
    func amazonNote(_ id: String) -> String? {
        guard AmazonLibrary.enabled, let game = Self.amazonGame(id), AmazonLibrary.shared.installed[game] != nil else { return nil }
        return "BCD starts the program the game's own fuel.json names, without the Amazon Games app. A game that needs the Amazon Games app running may not start."
    }

    /// For the game's page: the library entry an installed Amazon game is, or nil.
    func amazonEntryID(_ id: String) -> UUID? {
        guard AmazonLibrary.enabled, let game = Self.amazonGame(id) else { return nil }
        return AmazonLibrary.shared.installed[game]?.entryID
    }

    /// Hook in the game page's watch: the page reads its details again when the store's state moves.
    var amazonDetailChanges: AnyPublisher<Void, Never> {
        AmazonLibrary.enabled ? AmazonLibrary.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher() : Empty<Void, Never>().eraseToAnyPublisher()
    }

    // MARK: Sign-in

    /// Amazon's own login page, in BCD's sign-in sheet. It ends on www.amazon.com with a one-use code in the address: the sheet stops that
    /// navigation, reads the code and closes; the code is traded for the tokens at once and never logged. "Use Safari" is the way around a login
    /// the web view cannot finish: the address it ends on is pasted back.
    private func signInToAmazon() {
        guard !BCDAmazonSignal.shared.signingIn else { return }
        let page = BCDWebSignIn.Page(
            title: "Sign in to Amazon Games", start: AmazonAuth.shared.beginLogin(),
            match: { url in AmazonAuth.isLoginResult(url) ? AmazonAuth.extractCode(from: url.absoluteString) : nil },
            pasteHint: "Sign in to Amazon in Safari. When it is done, Safari shows Amazon's home page: copy the whole address from the address bar (it has openid.oa2.authorization_code in it) and paste it here.",
            pastePlaceholder: "https://www.amazon.com/?openid.oa2.authorization_code=…",
            pasteProblem: "That isn't the address Amazon's sign-in ends on. It starts with https://www.amazon.com/ and has openid.oa2.authorization_code in it.")
        BCDWebSignIn.present(page) { code in
            guard let code else { return }
            Task { @MainActor in
                BCDAmazonSignal.shared.signingIn = true
                defer { BCDAmazonSignal.shared.signingIn = false }
                do {
                    try await AmazonLibrary.shared.signIn(code: code)
                    BCDAmazonSignal.shared.changed()
                    BCDStoresUI.shared.refresh()
                } catch {
                    LogStore.shared.log("[amazon] sign-in failed: \(error.localizedDescription)", level: .error)
                    LibraryModel.shared.error = "Amazon didn't accept the sign-in (\(error.localizedDescription)) Try again, or use Safari from the sign-in page."
                }
            }
        }
    }

    // MARK: Helpers

    static func amazonID(_ product: String) -> String { "amazon." + product }

    /// The product of an Amazon game id: letters, digits, dots, dashes and underscores ("amzn1.adg.product.1234abcd-...").
    static func amazonGame(_ id: String) -> String? {
        guard id.hasPrefix("amazon.") else { return nil }
        let product = String(id.dropFirst("amazon.".count))
        guard !product.isEmpty, product.count <= 160,
              product.unicodeScalars.allSatisfy({ ($0.value < 128) && (CharacterSet.alphanumerics.contains($0) || ".-_".unicodeScalars.contains($0)) }) else { return nil }
        return product
    }

    /// Amazon's pictures: the wide background as the cover, header and hero, the logo as the logo. A cover the player chose for the entry stays first.
    private func amazonApply(_ source: AmazonGame, to game: inout BCDGame, keepCover: Bool) {
        let chosen = keepCover ? game.coverArt.filter(\.isFileURL) : []
        game.coverArt = chosen + [source.art].compactMap { $0 }
        game.headerArt = [source.art].compactMap { $0 }
        game.heroArt = [source.art].compactMap { $0 }
        game.logoArt = [source.logo].compactMap { $0 }
    }

    private func amazonState(_ id: String) -> BCDGameState {
        let amazon = AmazonLibrary.shared
        if let download = amazon.downloads[id] {
            let progress = download.progress
            switch download.state {
            case .queued: return .queued
            case .active:
                return progress.phase == .finishing || progress.phase == .verifying
                    ? .installing(progress: progress.fraction) : .downloading(progress: progress.fraction)
            case .paused: return .downloading(progress: progress.fraction)   // the Downloads tab says Paused
            case .failed(let message): return amazon.installed[id] != nil ? .broken(reason: message) : .notInstalled
            }
        }
        return amazon.installed[id] != nil ? .ready : .notInstalled
    }
}
