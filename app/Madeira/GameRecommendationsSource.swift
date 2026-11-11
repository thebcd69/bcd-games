// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  GameRecommendationsSource.swift
//  Madeira
//
//  BCD: where the recommended settings come from (owner's decision, 2026-10-18). Every build
//  carries recommended/recommendations.json (a bundle resource). BCD's public repository serves
//  the newest one beside its Ed25519 signature, so a game fixed by one new line gets it without
//  a new build:
//
//    https://raw.githubusercontent.com/thebcd69/bcd-games/main/recommended/recommendations.json
//    https://raw.githubusercontent.com/thebcd69/bcd-games/main/recommended/recommendations.json.sig
//
//  A download is used only when all of this holds; otherwise the list in use stays, and one
//  "[recs]" log line says why:
//    - its signature is valid for its exact bytes under the public key below (the private key
//      never leaves the owner's Mac; tools/publish-recommendations.py signs);
//    - it is format 1, laid out as GameRecommendationsList.swift says, with no unknown field;
//    - every line this build applies sets a key RecommendationsPolicy allows;
//    - it keeps every rule of the list in use and of the built-in list, none at a lower version;
//    - its version is higher than the list in use.
//  The last good download is kept in Application Support/Recommendations (signature and bytes
//  in one file, written atomically) and checked again at every start; the app uses the newer
//  of it and its own built-in list. Applying is unchanged (GameRecommendationsApply.swift): a
//  new or untouched game follows the list, a config the player changed is never written, and
//  Reset to Recommended writes the newest list.
//
//  API (the interface calls it through BCD's backend; this file draws no UI):
//
//    RecommendationsSource.shared            ObservableObject; its published members change on
//                                            the main thread
//      version: Int                          the list in use ("version" of its file)
//      generated: String                     the day it was made, "2026-10-18"
//      origin: GameRecommendationList.Origin .builtIn or .downloaded
//      sourceDescription: String             one line for the UI
//      lastChecked: Date?                    when BCD last got an answer online
//      isRefreshing: Bool                    a check is running
//      lastResult: RecommendationsRefreshResult?
//      refresh() async -> RecommendationsRefreshResult
//                                            asks online now (the button); joins a check
//                                            already running
//    RecommendationsSource.start(idle:onChange:)
//                                            once, at app start (LibraryModel.init): asks online
//                                            in the background unless an answer came in the
//                                            last 6 hours. A newer list goes into use only while
//                                            idle() (no game running): one that comes during a
//                                            session is kept and used from the next start, so a
//                                            running game's config never changes under it.
//                                            onChange runs on the main thread after a newer list
//                                            with changes was put in use
//    RecommendationsRefreshResult            .upToDate, .updated (with the changed rules),
//                                            .offline (no connection, or no online list yet:
//                                            a 404 while the repository is not public), .rejected
//      message: String                       one line for the person
//
//  Log tag: [recs].
//

import Foundation
import CryptoKit
import Combine

// MARK: - Signature

enum RecommendationsSignature {
    /// BCD's Ed25519 public key for recommended/recommendations.json: the raw 32 bytes, base64.
    /// The private key is ~/madeira-local/state/recommendations-signing.key on the owner's Mac.
    static let publicKey = "liaGBvtO1cgr3YnOzcvRALS6yZ2q/IinzEFqruNimcA="

    /// A .sig file: the 64-byte signature in base64 on one line.
    static func signature(fromFile data: Data) -> Data? {
        guard data.count <= 256, let text = String(data: data, encoding: .utf8),
              let raw = Data(base64Encoded: text.trimmingCharacters(in: .whitespacesAndNewlines)), raw.count == 64 else {
            return nil
        }
        return raw
    }

    static func isValid(_ signature: Data, for data: Data, publicKey: String) -> Bool {
        guard let raw = Data(base64Encoded: publicKey),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: raw) else { return false }
        return key.isValidSignature(signature, for: data)
    }
}

// MARK: - The files

enum RecommendationsStore {
    static let jsonURL = URL(string: "https://raw.githubusercontent.com/thebcd69/bcd-games/main/recommended/recommendations.json")!
    static let signatureURL = URL(string: "https://raw.githubusercontent.com/thebcd69/bcd-games/main/recommended/recommendations.json.sig")!

    // Set by the host checks before the first use; the app keeps these.
    static var builtInURL: URL? = Bundle.main.url(forResource: "recommendations", withExtension: "json")
    static var directory: URL? = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
        .appendingPathComponent("Recommendations", isDirectory: true)
    /// This build's number (CFBundleVersion): a rule's or a line's minBuild / maxBuild compare with it.
    static var build: Int? = (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String).flatMap(buildNumber)
    /// The build number in a CFBundleVersion: "634", or the part before the first dot of a re-signed copy's "634.4" (night round 2026-11-11,
    /// T1: "634.4" gave "build unknown", so every line with a minBuild was dropped).
    static func buildNumber(_ text: String) -> Int? {
        Int(text) ?? text.split(separator: ".", maxSplits: 1).first.flatMap { Int($0) }
    }
    static var publicKey = RecommendationsSignature.publicKey

    /// What a download may set: the settings catalog's game settings and the allowed environment lines.
    static var policy: RecommendationsPolicy {
        RecommendationsPolicy(catalog: ConfigCatalog.generated.map { (key: $0.key, category: $0.category) })
    }

    /// The last good download: its signature (base64) on the first line, then the file's exact bytes.
    static var keptURL: URL? { directory?.appendingPathComponent("last-good.json.signed") }

    /// The built-in list's file; nil (logged) when the build lacks it or it does not read, which
    /// tests/host/check-remote-recommendations.py rules out.
    static func builtInFile() -> RecommendationsFile? {
        guard let url = builtInURL, let data = try? Data(contentsOf: url) else {
            LogStore.shared.log("[recs] this build has no built-in list (recommendations.json)", level: .error)
            return nil
        }
        do {
            return try RecommendationsFile.parse(data)
        } catch {
            LogStore.shared.log("[recs] the built-in list does not read: \(error)", level: .error)
            return nil
        }
    }

    /// A download, checked as at its arrival: signature, layout, this build's policy.
    static func check(json: Data, signature: Data) throws -> RecommendationsFile {
        guard RecommendationsSignature.isValid(signature, for: json, publicKey: publicKey) else {
            throw RecommendationsError.signature("its signature is not BCD's")
        }
        let file = try RecommendationsFile.parse(json)
        try file.checkPolicy(policy, build: build)
        return file
    }

    /// The list a checked download gives this build, or why it may not replace `lists`.
    static func list(_ file: RecommendationsFile, against lists: [GameRecommendationList]) throws -> GameRecommendationList {
        let list = file.list(build: build, origin: .downloaded)
        for old in lists {
            if let why = list.regression(from: old) { throw RecommendationsError.regression(why) }
        }
        return list
    }

    static func kept() -> (json: Data, signature: Data)? {
        guard let url = keptURL, let data = try? Data(contentsOf: url),
              let newline = data.firstIndex(of: 0x0a),
              let signature = RecommendationsSignature.signature(fromFile: data[data.startIndex..<newline]) else { return nil }
        return (Data(data[(newline + 1)...]), signature)
    }

    static func keep(json: Data, signature: Data) throws {
        guard let dir = directory, let url = keptURL else { throw CocoaError(.fileNoSuchFile) }
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var data = Data((signature.base64EncodedString() + "\n").utf8)
        data.append(json)
        try data.write(to: url, options: .atomic)
    }

    /// The list at the first use (GameRecommendations.active): the built-in one, or the kept
    /// download when it still checks out and is newer.
    static func initialList() -> GameRecommendationList {
        let builtIn = builtInFile()?.list(build: build, origin: .builtIn)
        var chosen = builtIn ?? .empty
        if let download = kept() {
            do {
                let file = try check(json: download.json, signature: download.signature)
                let list = try self.list(file, against: builtIn.map { [$0] } ?? [])
                if list.version > chosen.version || builtIn == nil {
                    chosen = list
                }
            } catch {
                LogStore.shared.log("[recs] the kept download was not used: \(error)")
            }
        }
        LogStore.shared.log("[recs] in use: \(chosen.origin.rawValue) list v\(chosen.version) (\(chosen.generated)), "
                            + "\(chosen.rules.count) rules, build \(build.map(String.init) ?? "unknown")")
        return chosen
    }
}

// MARK: - The network

/// What a check online got.
enum RecommendationsFetch: Equatable {
    case ok(json: Data, signature: Data)
    /// 404: no list online yet (the repository is not public).
    case notPublished
    /// No list: no connection, a timeout, a server's error (5xx, a rate limit).
    case offline(String)
    /// An answer that is no list: a redirect elsewhere, too large, not HTTP.
    case invalid(String)
}

enum RecommendationsHTTP {
    static let maxJSONBytes = 2 << 20
    static let maxSignatureBytes = 256

    private static let session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 60
        configuration.waitsForConnectivity = false
        return URLSession(configuration: configuration)
    }()

    static func fetch() async -> RecommendationsFetch {
        let json: Data
        switch await get(RecommendationsStore.jsonURL, limit: maxJSONBytes) {
        case .success(let data): json = data
        case .failure(let failure): return failure.fetch
        }
        switch await get(RecommendationsStore.signatureURL, limit: maxSignatureBytes) {
        case .success(let signature): return .ok(json: json, signature: signature)
        case .failure(let failure): return failure.fetch
        }
    }

    private struct Failure: Error { let fetch: RecommendationsFetch }

    private static func get(_ url: URL, limit: Int) async -> Result<Data, Failure> {
        do {
            let (data, response) = try await session.data(from: url)
            guard let http = response as? HTTPURLResponse else { return .failure(Failure(fetch: .invalid("no HTTP answer"))) }
            if http.statusCode == 404 { return .failure(Failure(fetch: .notPublished)) }
            guard http.statusCode == 200 else { return .failure(Failure(fetch: .offline("HTTP \(http.statusCode)"))) }
            guard http.url?.scheme == "https", http.url?.host == url.host else {
                return .failure(Failure(fetch: .invalid("redirected to \(http.url?.host ?? "?")")))
            }
            guard data.count <= limit else { return .failure(Failure(fetch: .invalid("\(url.lastPathComponent) is \(data.count) bytes"))) }
            return .success(data)
        } catch {
            return .failure(Failure(fetch: .offline((error as NSError).localizedDescription)))
        }
    }
}

// MARK: - The model

/// What a check online did.
enum RecommendationsRefreshResult: Equatable {
    /// The online list is not newer than the one in use.
    case upToDate(version: Int)
    /// A newer list is in use; `changed`: the titles of the rules that are new or different.
    case updated(version: Int, changed: [String])
    /// No list came: no connection (`notPublished` false), or none online yet (a 404).
    case offline(version: Int, origin: GameRecommendationList.Origin, notPublished: Bool)
    /// A list came and was not used; the list in use stays.
    case rejected(reason: String, version: Int)

    var message: String {
        switch self {
        case .upToDate(let version):
            return "Up to date: these are the newest recommended settings (list \(version))."
        case .updated(let version, let changed):
            let what = changed.isEmpty ? "no game's settings changed"
                : changed.count == 1 ? "1 change (\(changed[0]))" : "\(changed.count) changes"
            return "Updated to list \(version): \(what)."
        case .offline(let version, let origin, let notPublished):
            let kind = origin == .builtIn ? "the built-in list" : "the downloaded list"
            return (notPublished ? "No online list yet" : "Offline") + ": using \(kind) (list \(version))."
        case .rejected(let reason, let version):
            return "Rejected: \(reason). Still using list \(version)."
        }
    }
}

final class RecommendationsSource: ObservableObject {
    static let shared = RecommendationsSource()

    /// An answer online this recent spares the start's check (the button always asks).
    static let backgroundInterval: TimeInterval = 6 * 3600
    static let lastCheckKey = "bcd.recommended.lastCheck"

    @Published private(set) var version: Int
    @Published private(set) var generated: String
    @Published private(set) var origin: GameRecommendationList.Origin
    @Published private(set) var lastChecked: Date?
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastResult: RecommendationsRefreshResult?

    /// The network; the host checks give it their own.
    var fetch: () async -> RecommendationsFetch
    /// LibraryModel's: the library's games take a newer list at once (start(idle:onChange:)).
    var onChange: (() -> Void)?
    /// LibraryModel's: no game session runs, so a newer list may go into use now.
    var idle: () -> Bool = { true }
    @MainActor private var running: Task<RecommendationsRefreshResult, Never>?
    private static var started = false

    init(fetch: @escaping () async -> RecommendationsFetch = RecommendationsHTTP.fetch) {
        self.fetch = fetch
        let list = GameRecommendations.active
        version = list.version
        generated = list.generated
        origin = list.origin
        let seconds = UserDefaults.standard.double(forKey: Self.lastCheckKey)
        lastChecked = seconds > 0 ? Date(timeIntervalSince1970: seconds) : nil
    }

    var sourceDescription: String {
        let kind = origin == .builtIn ? "Built into this version" : "Downloaded"
        return "\(kind): list \(version) of \(generated)"
    }

    /// App start (LibraryModel.init), once per run: a check in the background, unless an
    /// answer came in the last 6 hours. A newer list goes into use only when `idle()`; one that
    /// comes while a game runs is kept for the next start. `onChange` runs on the main thread
    /// whenever a newer list with changes is put in use, by this check or a later refresh().
    static func start(idle: @escaping () -> Bool, onChange: @escaping () -> Void) {
        DispatchQueue.main.async {
            guard !started else { return }
            started = true
            shared.idle = idle
            shared.onChange = onChange
            guard backgroundCheckDue(lastChecked: shared.lastChecked) else { return }
            Task { @MainActor in _ = await shared.refresh() }
        }
    }

    /// The start's check runs unless an answer came less than 6 hours ago (a clock set back
    /// does not stop it).
    static func backgroundCheckDue(lastChecked: Date?, now: Date = Date()) -> Bool {
        guard let lastChecked, now >= lastChecked else { return true }
        return now.timeIntervalSince(lastChecked) >= backgroundInterval
    }

    /// Asks online now; a check already running is joined.
    @MainActor
    func refresh() async -> RecommendationsRefreshResult {
        if let running { return await running.value }
        let task = Task { @MainActor () -> RecommendationsRefreshResult in
            isRefreshing = true
            let fetched = await fetch()
            let result = await Task.detached(priority: .utility) { Self.evaluate(fetched) }.value
            finish(fetched, result)
            isRefreshing = false
            return result.result
        }
        running = task
        let result = await task.value
        running = nil
        return result
    }

    private struct Outcome {
        var result: RecommendationsRefreshResult
        var install: (list: GameRecommendationList, json: Data, signature: Data)?
    }

    /// Off the main thread: what to do with what came.
    private static func evaluate(_ fetched: RecommendationsFetch) -> Outcome {
        let current = GameRecommendations.active
        switch fetched {
        case .notPublished:
            LogStore.shared.log("[recs] no online list yet (404); \(current.origin.rawValue) list v\(current.version) stays")
            return Outcome(result: .offline(version: current.version, origin: current.origin, notPublished: true))
        case .offline(let why):
            LogStore.shared.log("[recs] offline (\(why)); \(current.origin.rawValue) list v\(current.version) stays")
            return Outcome(result: .offline(version: current.version, origin: current.origin, notPublished: false))
        case .invalid(let why):
            LogStore.shared.log("[recs] rejected the online list: \(why); \(current.origin.rawValue) list v\(current.version) stays")
            return Outcome(result: .rejected(reason: why, version: current.version))
        case .ok(let json, let signatureFile):
            do {
                guard let signature = RecommendationsSignature.signature(fromFile: signatureFile) else {
                    throw RecommendationsError.signature("its .sig file holds no signature")
                }
                let file = try RecommendationsStore.check(json: json, signature: signature)
                guard file.version > current.version else {
                    LogStore.shared.log("[recs] up to date: online v\(file.version), in use \(current.origin.rawValue) v\(current.version)")
                    return Outcome(result: .upToDate(version: current.version))
                }
                var against = [current]
                if current.origin != .builtIn, let builtIn = RecommendationsStore.builtInFile() {
                    against.append(builtIn.list(build: RecommendationsStore.build, origin: .builtIn))
                }
                let list = try RecommendationsStore.list(file, against: against)
                let changed = list.changes(from: current)
                LogStore.shared.log("[recs] online list v\(list.version) (\(list.generated)) in use, was \(current.origin.rawValue) v\(current.version): "
                                    + (changed.isEmpty ? "no rule changed" : changed.map { "\($0.id) v\($0.version)" }.joined(separator: ", ")))
                return Outcome(result: .updated(version: list.version, changed: changed.map(\.title)),
                               install: (list, json, signature))
            } catch {
                LogStore.shared.log("[recs] rejected the online list: \(error); \(current.origin.rawValue) list v\(current.version) stays",
                                    level: .error)
                let reason = (error as? RecommendationsError)?.description ?? "\(error)"
                return Outcome(result: .rejected(reason: reason, version: current.version))
            }
        }
    }

    @MainActor
    private func finish(_ fetched: RecommendationsFetch, _ outcome: Outcome) {
        if case .offline = fetched {} else {
            // The server answered: the start's check waits 6 hours from here.
            let now = Date()
            UserDefaults.standard.set(now.timeIntervalSince1970, forKey: Self.lastCheckKey)
            lastChecked = now
        }
        if let install = outcome.install {
            var kept = true
            do {
                try RecommendationsStore.keep(json: install.json, signature: install.signature)
            } catch {
                kept = false
                LogStore.shared.log("[recs] the download could not be kept (\(error)); it is used until BCD closes", level: .error)
            }
            guard idle() else {
                // A game runs: its config must not change under it (one session per run).
                LogStore.shared.log("[recs] a game is running: list v\(install.list.version) "
                                    + (kept ? "is used from the next start" : "is not used"))
                lastResult = outcome.result
                return
            }
            GameRecommendations.install(install.list)
            version = install.list.version
            generated = install.list.generated
            origin = install.list.origin
            if case .updated(_, let changed) = outcome.result, !changed.isEmpty { onChange?() }
        }
        lastResult = outcome.result
    }
}
