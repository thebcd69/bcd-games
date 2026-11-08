// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// Epic Games in BCD. Milestone 0 (docs/research/open-fork-2026-10-18/epic.md §5): sign-in on Epic's
/// own page in a sheet (BCDWebSignIn), the owned games with their pictures, cached on disk, refreshed on
/// demand and at the start of the app when the list is stale (6 h). Milestone 1 (BCDEpicDriver.swift): the
/// game's build downloaded and installed into C:\Program Files\Epic Games\<game> by the shared content
/// engine (BCDContentQueue; Epic's binary manifests and chunk files), the program as an ordinary library
/// entry that Play starts with Epic's public arguments (no launcher in Wine, no secret crosses into the
/// game), and uninstall. Engine-bound: BCD's interface reaches it only through BCDLiveStore
/// (BCDLiveStoreEpic.swift).
///
/// - `env.BCD_EPIC = 0` hides everything Epic (on by default; nothing is fetched before a sign-in).
/// - `env.BCD_EPIC_INSTALL = 0` keeps the list and hides Install: installed Epic games stay ordinary
///   library games that start.
/// - `env.BCD_EPIC_CDN = <part of a host name>` tries that CDN first.
/// - Milestone 2 (BCDEpicStart.swift): an installed game starts signed in to Epic: its one-time exchange code (and an
///   ownership token for games whose copy protection asks) reach it through BCD Host, never through a log, the
///   environment or a file that stays. `env.BCD_EPIC_SIGNIN = 0` (also in a game's own config) starts it with Epic's public
///   arguments only; `env.BCD_EPIC_HOST = 0` starts it directly (public arguments only); `env.BCD_EPIC_TREE = 0` has the host
///   wait for the game's own process only, not everything it starts.
/// - `epic-user-agent = <text>` replaces the user agent of Epic's sign-in page and API calls (an
///   emergency lever, should Epic's pages refuse WebKit's own or the launcher-shaped one).
/// - Tokens: one Keychain item (BCDEpicSession.itemName), nowhere else. The list's cache holds
///   no token and no account ID (an account hash).
/// Log tags: [epic-auth], [epic-library], [epic-install], [epic-cdn] and [epic-launch] (counts, HTTP
/// statuses, timings, app names and short reasons: never a token, a code, a cookie, a name, an e-mail
/// address or an account ID in full).
@MainActor final class BCDEpic: ObservableObject {
    static let shared = BCDEpic()

    /// Epic in BCD (on by default; nothing is fetched before a sign-in). 0 hides it everywhere.
    static var enabled: Bool { MadeiraConfig.flag("BCD_EPIC", fallback: true) }

    /// Installing Epic games (on by default). 0 keeps the list and hides Install; installed games stay.
    static var installEnabled: Bool { MadeiraConfig.flag("BCD_EPIC_INSTALL", fallback: true) }
    /// A CDN to try first, by a part of its host name; empty: Epic's own order.
    static var preferredCDN: String? {
        guard let text = MadeiraConfig.get("env.BCD_EPIC_CDN")?.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty, text.utf8.count <= 60 else { return nil }
        return text
    }

    /// A game starts signed in to Epic (on by default; also settable in a game's own config). 0: Epic's public arguments only.
    static var signInEnabled: Bool { MadeiraConfig.flag("BCD_EPIC_SIGNIN", fallback: true) }
    /// The signed-in start goes through BCD Host (on by default). 0 starts the game directly with the public arguments only.
    static var hostEnabled: Bool { MadeiraConfig.flag("BCD_EPIC_HOST", fallback: true) }
    /// The signed-in start KEEPS BCD Host as the first program (off by default, a game's own config line turns it on). Builds 590 to 597 chose the
    /// host but a second configureLaunch put the game's own program back, so the host never ran; 1 holds the host's values and sets them again.
    static var hostStartDefault: Bool { MadeiraConfig.flag("BCD_EPIC_HOST_START", fallback: false) }
    /// BCD Host waits for every process the game starts, not only the game's own (on by default).
    static var treeWait: Bool { MadeiraConfig.flag("BCD_EPIC_TREE", fallback: true) }
    /// bcdhost.exe is in this build (it is a build output; without it every Epic game starts as in milestone 1).
    static var hostAvailable: Bool {
        FileManager.default.fileExists(atPath: Bundle.main.bundlePath + "/arm64ec-windows/bcdhost.exe")
    }
    /// Games start signed in to Epic in this copy of BCD: the notes and the rules of the game's page follow.
    var signedStartAvailable: Bool { Self.signInEnabled && Self.hostEnabled && Self.hostAvailable }

    /// Empty: Epic's sign-in page uses WebKit's own user agent and API calls a launcher-shaped one. Text: used for both (an emergency lever, should Epic's pages refuse them).
    static var userAgentOverride: String? {
        guard let text = MadeiraConfig.get("epic-user-agent")?.trimmingCharacters(in: .whitespacesAndNewlines),
              !text.isEmpty, text.utf8.count <= 300 else { return nil }
        return text
    }

    static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCD/Epic", isDirectory: true)
    }

    @Published private(set) var signedIn = false
    @Published private(set) var displayName: String?
    @Published private(set) var signingIn = false
    /// Every owned item the last refresh read (games, and what BCD does not list).
    @Published private(set) var items: [BCDEpicItem] = []
    @Published private(set) var refreshing = false
    @Published private(set) var updated: Date?
    @Published var error: String?
    /// The installed games by app name, and the builds BCD found it cannot install (milestone 1).
    @Published var installs: [String: BCDEpicInstallRecord] = [:]

    /// Whether a library entry is an Epic game BCD installed: its launch gets the Epic session's switches (ContentView).
    func owns(entryID: UUID) -> Bool { installs.values.contains { $0.entryID == entryID } }
    @Published var refusals = BCDEpicRefusals()

    // Milestone 2: a signed-in start (BCDEpicStart.swift).
    /// The question shown when a signed-in start could not be made (BCDEpicPromptModel holds what the screen shows).
    var promptRetry: (() -> Void)?
    var promptEntry: UUID?
    /// The game being signed in right now (its entry), so a second Play does not start a second try.
    var starting: UUID?
    /// What Epic gave a start, for the one start that is about to begin (nil otherwise).
    var ticket: (entry: UUID, credentials: BCDEpicCredentials)?
    /// The entry the player chose "Play offline" for, for one start.
    var offlineOnce: UUID?
    /// A signed-in start has begun and its files and report are not yet cleaned up.
    var hostRan = false
    /// The exact values of that start, kept in memory only for the search after its session (BCDEpicAudit).
    var auditSecrets: (code: String, id: String, token: String?)?
    /// The game's own folder, for that search (its log files).
    var hostGame: URL?
    var sessionWatch: AnyCancellable?

    /// The games BCD lists: owned, Windows, not downloadable content, with a title.
    var games: [BCDEpicItem] { items.filter { $0.kind == .game && $0.title != nil } }

    let store = "epic"
    lazy var transport = BCDEpicURLSession(userAgent: Self.userAgentOverride ?? BCDEpicAuth.launcherUserAgent)
    lazy var session = BCDEpicSession(secrets: BCDKeychain.shared, transport: transport)
    lazy var api = BCDEpicAPI(session: session, transport: transport)
    private var started = false
    /// The CDN folders of the build a job is installing (set when its plan is made, read for its source).
    var folders: [String: [URL]] = [:]
    /// The installed games whose pictures are on the disk (BCDEpic.artFile).
    var artKept = Set<String>()
    /// What a look at a game's manifest said (its size, a refusal, Epic Online Services), taken when its page opens.
    @Published var looks: [String: BCDEpicPreflight] = [:]
    var checking = Set<String>()
    var watchers = Set<AnyCancellable>()
    var queue: BCDContentQueue { BCDContentQueue.shared }
    /// The pages the sign-in sheet went to (their labels), so each is logged once.
    private var pagesSeen = Set<String>()

    private init() {}

    /// What BCD's store watches to show Epic's changes: the account, the list, the installs and the downloads.
    var changes: AnyPublisher<Void, Never> {
        Publishers.Merge(objectWillChange.map { _ in () }, queue.$jobs.map { _ in () }).eraseToAnyPublisher()
    }

    /// Once per run, at the first look: the cached list, then (signed in) Epic is asked whether the
    /// session is still good and the list is read again when it is older than 6 hours.
    func start() {
        guard Self.enabled, !started else { return }
        started = true
        BCDContentLog.sink = { LogStore.shared.log($0) }
        wireInstalls()
        startSignedStarts()
        Task {
            let tokens = await session.current()
            signedIn = tokens != nil
            displayName = tokens?.displayName
            if let account = tokens?.accountID { loadCache(account: BCDEpicCatalog.accountKey(account)) }
            BCDEpicLog.write("[epic-auth] start signed-in=\(signedIn ? 1 : 0)")
            BCDEpicLog.write("[epic-library] start cached=\(items.count) listed=\(games.count)")
            if signedIn {
                await checkSession()
                await refreshLibrary(force: false)
                BCDEpicCloud.shared.startupCheck()   // build 632: only the games whose cloud saves the player turned on
            }
        }
    }

    // MARK: Account

    /// Epic's own sign-in page in a sheet. The password goes to Epic's page; BCD never sees it.
    func signIn() {
        guard Self.enabled, !signingIn else { return }
        start()
        pagesSeen = []
        BCDEpicLog.write("[epic-auth] sign-in sheet opened")
        present(BCDEpicAuth.loginURL, title: "Sign in to Epic Games", ifClosed: nil)
    }

    private func present(_ start: URL, title: String, ifClosed: String?) {
        let page = BCDWebSignIn.Page(
            title: title, start: start, match: { [weak self] url in self?.noted(url); return BCDEpicAuth.code(from: url) },
            pasteHint: "Sign in to Epic in Safari. When it's done, Safari shows a short line of text that includes authorizationCode: select all of it, copy it and paste it here.",
            readsPage: BCDEpicAuth.isRedirectPage(_:),
            matchPage: { [weak self] _, text in self?.read(redirectText: text) ?? .keepWaiting },
            matchPasted: BCDEpicAuth.code(fromPasted:),
            pastePlaceholder: "Paste the text or the code here",
            pasteProblem: "That doesn't have the sign-in code in it. After signing in, Safari shows a line of text with authorizationCode in it: copy all of it.",
            userAgent: Self.userAgentOverride)
        BCDWebSignIn.present(page) { [weak self] code in
            guard let self else { return }
            if let code {
                Task { await self.finishSignIn(code) }
            } else if let ifClosed {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.tell(ifClosed) }
            }
        }
    }

    /// Logs where the sheet's web view goes (Epic's pages by host and the first path segments, any other host as
    /// "other-host"; never a query), each once and at most sixteen: where a sign-in stalls shows in the log.
    private func noted(_ url: URL) {
        let label = BCDEpicAuth.pageLabel(url)
        guard pagesSeen.count < 16, pagesSeen.insert(label).inserted else { return }
        BCDEpicLog.write("[epic-auth] page \(BCDEpicAuth.pageLabel(url))")
    }

    /// What the redirect page's text says (the page is the only one whose text is read; it is never logged,
    /// only its key names).
    private func read(redirectText text: String) -> BCDWebSignIn.PageResult {
        switch BCDEpicAuth.page(from: text) {
        case .code(let found):
            BCDEpicLog.write("[epic-auth] redirect page: sign-in found")
            return .code(found)
        case .corrective(let action, let step):
            let next = step != nil
            BCDEpicLog.write("[epic-auth] redirect page: corrective action=\(action) next=\(next ? 1 : 0)")
            if let step { return .load(step) }
            return .keepWaiting
        case .other:
            BCDEpicLog.write("[epic-auth] redirect page: no sign-in keys=\(BCDEpicAuth.keys(of: text)) chars=\(text.count)")
            return .keepWaiting
        }
    }

    private func finishSignIn(_ authorization: String) async {
        signingIn = true
        defer { signingIn = false }
        do {
            let tokens = try await session.signIn(code: authorization)
            signedIn = true
            displayName = tokens.displayName
            error = nil
            items = []
            updated = nil
            BCDEpicCloud.shared.accountChanged()
            if let account = tokens.accountID { loadCache(account: BCDEpicCatalog.accountKey(account)) }
            await refreshLibrary(force: true)
            BCDEpicCloud.shared.startupCheck()
        } catch BCDEpicError.corrective(let action, let step) {
            let next = step != nil
            BCDEpicLog.write("[epic-auth] failed reason=corrective-\(action) next=\(next ? 1 : 0)")
            if let step {
                present(step, title: "Finish this step on Epic",
                        ifClosed: "Once Epic's step is finished, tap Sign in to Epic Games again.")
            } else {
                tell(BCDEpicError.corrective(action: action, continuation: nil).errorDescription ?? "")
            }
        } catch {
            tell("Epic didn't accept the sign-in. Try again, or use Safari from the sign-in page.")
            BCDEpicLog.write("[epic-auth] failed reason=\(BCDEpicLog.reason(error))")
        }
    }

    /// Asks Epic whether the stored session is still good (start of the app). Epic out of reach keeps
    /// the person signed in with the cached list; only a refusal ends the sign-in.
    private func checkSession() async {
        do {
            let tokens = try await session.verify()
            displayName = tokens.displayName
        } catch BCDEpicError.signInRefused {
            clearAccount()
            BCDEpicLog.write("[epic-auth] session refused at start; signed out")
        } catch {
            BCDEpicLog.write("[epic-auth] verify skipped reason=\(BCDEpicLog.reason(error))")
        }
    }

    /// Ends the session at Epic (when it can be reached) and forgets the tokens and the list.
    func signOut() {
        guard signedIn else { return }
        let session = self.session
        Task { _ = await session.signOut() }
        for job in queue.jobs.values where job.key.store == store { queue.pause(job.key) }
        clearAccount()
        endSignedStarts()
        BCDEpicLog.write("[epic-auth] signed out")
    }

    func clearAccount() {
        BCDEpicCloud.shared.accountChanged()
        signedIn = false
        displayName = nil
        items = []
        updated = nil
        try? FileManager.default.removeItem(at: Self.folder.appendingPathComponent("library.json"))
    }

    // MARK: Library

    func refreshLibrary(force: Bool) async {
        guard Self.enabled, signedIn, !refreshing, LibraryModel.shared.current == nil else { return }
        if !force, let updated, Date().timeIntervalSince(updated) < 6 * 3600 { return }
        refreshing = true
        defer { refreshing = false }
        do {
            let result = try await BCDEpicLibrary.refresh(api: api, known: items, now: Date())
            items = result.items
            updated = Date()
            error = nil
            await saveCache()
            BCDEpicLog.write("[epic-library] refreshed " + result.stats.summary)
            let listed = games
            let sites = Set(listed.flatMap { [$0.tall, $0.wide] }.compactMap { $0.flatMap(URL.init(string:))?.host })
            BCDEpicLog.write("[epic-library] art tall=\(listed.filter { $0.tall != nil }.count) wide=\(listed.filter { $0.wide != nil }.count) sites=\(sites.sorted().prefix(4).joined(separator: ","))")
            if listed.isEmpty && result.stats.records > 0 {
                let message = "BCD read your Epic library (\(result.stats.records) items) but Epic's catalog gave no games to list. Try again later."
                error = message
                if force { tell(message) }
            }
        } catch {
            if case BCDEpicError.signInRefused = error { clearAccount() }
            let message = (error as? LocalizedError)?.errorDescription ?? "BCD couldn't read your Epic library. Try again later."
            self.error = message
            if force { tell(message) }   // asked for: say so; a refresh in the background stays quiet
            BCDEpicLog.write("[epic-library] refresh failed reason=\(BCDEpicLog.reason(error))")
        }
    }

    private func loadCache(account: String) {
        guard let data = try? Data(contentsOf: Self.folder.appendingPathComponent("library.json")),
              let cache = try? JSONDecoder().decode(BCDEpicCatalog.Cache.self, from: data),
              cache.version == 1, cache.account == account else { return }
        items = cache.items
        updated = cache.updated
    }

    private func saveCache() async {
        guard let account = await session.current()?.accountID else { return }
        let cache = BCDEpicCatalog.Cache(account: BCDEpicCatalog.accountKey(account), updated: updated ?? Date(), items: items)
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        try? JSONEncoder().encode(cache).write(to: Self.folder.appendingPathComponent("library.json"), options: .atomic)
    }

    func item(_ id: String) -> BCDEpicItem? { items.first { $0.id == id } }

    /// Something the player asked for went wrong: BCD's interface shows the library's message.
    func tell(_ message: String) { LibraryModel.shared.error = message }
}
