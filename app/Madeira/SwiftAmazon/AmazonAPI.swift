// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. See LICENSE.
// Taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Amazon/AmazonLibrary.swift: AmazonGame, AmazonAPI and the
// reading of the entitlements; AmazonInstaller.swift: the worker's file rules, the manifest and file downloads with the checksum and the
// renewed address, the folder name, fuel.json) under GPL-3.0-or-later with the Madeira Converter Exception. Modified by BCD (thebcd69),
// November 2026: BCD's network session and log tag; the downloads of one file are plain whole-file fetches (a part file is fetched again
// rather than continued, BCD has no ranged fetcher shared with GOG); the progress is BCD's AmazonProgress; nothing here touches the library
// (AmazonLibrary.swift, BCD's own, does); the messages name BCD. See THIRD-PARTY-NOTICES.md.
//
// Amazon's distribution service (JSON over HTTPS, an access token in a header): the account's entitlements and where a game's build is. And
// the work of one install off the main actor: the signed manifest read and checked, every file fetched, checked against the manifest's
// SHA-256 and moved into C:\Amazon\<name>, and the program the game's own fuel.json names. Nothing here logs a token or a download address
// (they carry credentials); only counts, sizes and states.

import Foundation
import CryptoKit

struct AmazonGame: Codable, Identifiable, Hashable {
    /// The product id ("amzn1.adg.product...").
    let id: String
    /// The entitlement id: what the download service wants when a game is installed.
    let entitlementID: String
    let title: String
    let sku: String
    let artURL: String?
    let logoURL: String?
    let developer: String?

    var art: URL? { (artURL ?? logoURL).flatMap(URL.init(string:)) }
    var logo: URL? { logoURL.flatMap(URL.init(string:)) }
}

enum AmazonError: LocalizedError {
    case invalid(String)
    var errorDescription: String? { if case .invalid(let message) = self { return message }; return nil }
}

/// How far an install is: BCD's Downloads page reads it.
struct AmazonProgress: Equatable, Sendable {
    enum Phase: String, Sendable { case preparing, verifying, downloading, finishing }
    var phase: Phase = .preparing
    var totalBytes: UInt64 = 0
    var doneBytes: UInt64 = 0
    var bytesPerSecond: Double = 0
    var fraction: Double { totalBytes > 0 ? min(1, Double(doneBytes) / Double(totalBytes)) : 0 }
}

/// The distribution service's calls.
enum AmazonAPI {
    private static let entitlements = URL(string: "https://gaming.amazon.com/api/distribution/entitlements")!
    private static let distribution = URL(string: "https://gaming.amazon.com/api/distribution/v2/public")!
    private static let targetPrefix = "com.amazon.animusdistributionservice."

    static func call(_ url: URL, target: String, token: String, body: [String: Any]) async throws -> (json: [String: Any], status: Int) {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(target, forHTTPHeaderField: "X-Amz-Target")
        request.setValue(token, forHTTPHeaderField: "x-amzn-token")
        request.setValue("com.amazon.agslauncher.win/3.0.9202.1", forHTTPHeaderField: "UserAgent")
        request.setValue(AmazonAuth.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("amz-1.0", forHTTPHeaderField: "Content-Encoding")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 30
        let data: Data, response: URLResponse
        do { (data, response) = try await AmazonNetwork.session.data(for: request) } catch { throw AmazonAuthError.network }
        guard let http = response as? HTTPURLResponse else { throw AmazonAuthError.network }
        return ((try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:], http.statusCode)
    }

    /// All entitlements, page by page.
    static func entitlementPages(token: String, serial: String) async throws -> [[String: Any]] {
        var all: [[String: Any]] = [], next: String?
        for _ in 0..<200 {
            let hardware = SHA256.hash(data: Data(serial.utf8)).map { String(format: "%02X", $0) }.joined()
            let body: [String: Any] = [
                "Operation": "GetEntitlements", "clientId": "Sonic", "syncPoint": NSNull(), "nextToken": next.map { $0 as Any } ?? NSNull(), "maxResults": 50,
                "productIdFilter": NSNull(), "keyId": "d5dc8b8b-86c8-4fc4-ae93-18c0def5314d", "hardwareHash": hardware,
            ]
            let (json, status) = try await call(entitlements, target: targetPrefix + "entitlement.AnimusEntitlementsService.GetEntitlements", token: token, body: body)
            guard status == 200 else { throw AmazonError.invalid("Amazon did not list your games (answer \(status)).") }
            all.append(contentsOf: (json["entitlements"] as? [[String: Any]]) ?? [])
            guard let more = json["nextToken"] as? String, !more.isEmpty else { return all }
            next = more
        }
        return all
    }

    /// Where a game's files are: the build id and the address the manifest and files hang off.
    static func download(entitlementID: String, token: String) async throws -> (version: String, base: URL) {
        let (json, status) = try await call(distribution, target: targetPrefix + "external.AnimusDistributionService.GetGameDownload", token: token,
                                            body: ["entitlementId": entitlementID, "Operation": "GetGameDownload"])
        guard status == 200, let version = json["versionId"] as? String, let link = (json["downloadUrl"] as? String).flatMap(URL.init(string:)),
              link.scheme == "https" else {
            LogStore.shared.log("[amazon] download details answered \(status)", level: .error)
            throw AmazonError.invalid("Amazon did not give a download for this game (answer \(status)).")
        }
        return (version, link)
    }

    /// The games among the entitlements (an account also holds in-game items and add-ons, which have nothing to download), sorted by name.
    static func games(from entitlements: [[String: Any]]) -> (games: [AmazonGame], summary: String) {
        var byProduct: [String: AmazonGame] = [:]
        var lines: [String: Int] = [:]
        for entitlement in entitlements {
            guard let product = entitlement["product"] as? [String: Any], let id = product["id"] as? String, let entitlementID = entitlement["id"] as? String,
                  let title = (product["title"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !title.isEmpty else { continue }
            let line = (product["productLine"] as? String) ?? ""
            lines[line, default: 0] += 1
            // Games are the "FuelGame" product line; an entitlement that does not say its line is kept rather than hidden.
            guard line.isEmpty || line.lowercased().contains("game") else { continue }
            let detail = product["productDetail"] as? [String: Any]
            let details = detail?["details"] as? [String: Any]
            func text(_ dict: [String: Any]?, _ key: String) -> String? { (dict?[key] as? String).flatMap { $0.isEmpty ? nil : $0 } }
            let art = text(details, "backgroundUrl1") ?? text(details, "backgroundUrl2") ?? text(details, "logoUrl") ?? text(detail, "iconUrl")
            byProduct[id] = AmazonGame(id: id, entitlementID: entitlementID, title: title, sku: (product["sku"] as? String) ?? "",
                                       artURL: art, logoURL: text(details, "logoUrl") ?? text(detail, "iconUrl"), developer: text(details, "developer"))
        }
        let summary = lines.sorted { $0.key < $1.key }.map { "\($0.key.isEmpty ? "-" : $0.key)=\($0.value)" }.joined(separator: " ")
        let games = byProduct.values.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
        return (games, "\(entitlements.count) entitlement(s), \(games.count) game(s); product lines: \(summary)")
    }
}

// MARK: - One install

/// An installed Amazon game, as BCD records it.
struct AmazonInstalledGame: Codable {
    var game: AmazonGame
    /// Relative to drive C ("Amazon/Some_Game_1a2b3c").
    var installDir: String
    /// The program, relative to drive C with forward slashes, and its arguments, from the game's own fuel.json.
    var programPath: String
    var arguments: String
    /// The program's working folder when fuel.json names one (a C:\ path).
    var workDir: String?
    /// Amazon's build id it was installed from, so a newer build can be told apart later.
    var version: String
    var installBytes: Int64
    /// The library entry this install made (nil until it has one).
    var entryID: UUID? = nil

    var windowsInstallPath: String { "C:\\" + installDir.replacingOccurrences(of: "/", with: "\\") }
}

/// The address a build's files hang off. It can carry a signature that runs out, so it can be asked for again.
actor AmazonSource {
    private var base: URL
    private var generation = 0
    private let renewal: @Sendable () async throws -> URL
    init(base: URL, renewal: @escaping @Sendable () async throws -> URL) { self.base = base; self.renewal = renewal }
    /// Asks Amazon for a fresh address, unless another download already did since `seen`.
    func renew(after seen: Int) async throws {
        guard seen == generation else { return }
        base = try await renewal()
        generation += 1
    }
    func file(_ hashHex: String) -> (url: URL, generation: Int) {
        var c = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        c.path = c.path.hasSuffix("/") ? c.path + "files/" + hashHex : c.path + "/files/" + hashHex
        return (c.url!, generation)
    }
    func manifest() -> URL {
        var c = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        c.path = c.path.hasSuffix("/") ? c.path + "manifest.proto" : c.path + "/manifest.proto"
        return c.url!
    }
}

/// Counts bytes from many downloads at once and reports at most four times a second.
final class AmazonMeter: @unchecked Sendable {
    private let lock = NSLock()
    private let total: UInt64
    private let start = Date()
    private var lastReport = Date.distantPast
    private var done: UInt64
    private var fetchedThisRun: UInt64 = 0
    init(total: UInt64, alreadyHave: UInt64) { self.total = total; done = alreadyHave }

    func add(_ bytes: UInt64) { lock.lock(); done += bytes; fetchedThisRun += bytes; lock.unlock() }

    func progress(force: Bool = false) -> AmazonProgress? {
        lock.lock(); defer { lock.unlock() }
        let now = Date()
        guard force || now.timeIntervalSince(lastReport) >= 0.25 else { return nil }
        lastReport = now
        return AmazonProgress(phase: .downloading, totalBytes: total, doneBytes: min(done, total),
                              bytesPerSecond: Double(fetchedThisRun) / max(0.5, now.timeIntervalSince(start)))
    }
}

/// File work and network of one install, off the main actor.
enum AmazonInstall {
    /// The folder name the game goes in: the title kept to plain letters, plus a few characters of the product id so two games never share one.
    static func folderName(for game: AmazonGame) -> String {
        let letters = game.title.map { $0.isASCII && ($0.isLetter || $0.isNumber) ? String($0) : "_" }.joined()
        let squeezed = letters.split(separator: "_", omittingEmptySubsequences: true).joined(separator: "_")
        let tag = SHA256.hash(data: Data(game.id.utf8)).prefix(3).map { String(format: "%02x", $0) }.joined()
        return (squeezed.isEmpty ? "game" : String(squeezed.prefix(40))) + "_" + tag
    }

    /// A path from the manifest ("Bin\\game.exe") as a relative path with "/", or nil if it could leave the game folder.
    static func safeRelativePath(_ raw: String) -> String? {
        let parts = raw.replacingOccurrences(of: "\\", with: "/").split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard !parts.isEmpty, !raw.hasPrefix("/"), !raw.hasPrefix("\\"), !raw.contains(":"), !parts.contains(".."), !parts.contains(".") else { return nil }
        return parts.joined(separator: "/")
    }

    private struct Job { let index: Int; let file: AmazonManifestFile; let target: URL }

    /// Downloads a build into `drive`/Amazon/<folder>, files in `staging` until each is checked. Returns the record (without its library entry).
    static func download(_ game: AmazonGame, drive: URL, staging: URL, session: @escaping @Sendable () async throws -> String,
                         progress: @escaping @Sendable (AmazonProgress) async -> Void) async throws -> AmazonInstalledGame {
        var status = AmazonProgress()
        await progress(status)
        let token = try await session()
        let entitlement = game.entitlementID
        let (version, base) = try await AmazonAPI.download(entitlementID: entitlement, token: token)
        let source = AmazonSource(base: base, renewal: {
            let fresh = try await session()
            return try await AmazonAPI.download(entitlementID: entitlement, token: fresh).base
        })
        let manifest = try await fetchManifest(source)

        // Which files, where. Windows paths are not case-sensitive: the first spelling of a path wins.
        let folder = folderName(for: game)
        let root = drive.appendingPathComponent("Amazon/\(folder)", isDirectory: true)
        var seen = Set<String>(), jobs: [Job] = []
        for file in manifest.files {
            guard let relative = safeRelativePath(file.path) else { throw AmazonError.invalid("Amazon's file list holds an unsafe path, so nothing was installed.") }
            guard file.hashAlgorithm == "sha256", file.hash.count == 32 else { throw AmazonError.invalid("This game's files use a checksum this version of BCD does not know.") }
            guard seen.insert(relative.lowercased()).inserted else { continue }
            jobs.append(Job(index: jobs.count, file: file, target: root.appendingPathComponent(relative)))
        }
        let total = jobs.reduce(UInt64(0)) { $0 + UInt64($1.file.size) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        for directory in manifest.directories {
            if let relative = safeRelativePath(directory) { try? FileManager.default.createDirectory(at: root.appendingPathComponent(relative), withIntermediateDirectories: true) }
        }
        let parts = staging.appendingPathComponent(folder, isDirectory: true)
        try FileManager.default.createDirectory(at: parts, withIntermediateDirectories: true)

        // Files already in place (an earlier try, or the same build): kept when their hash matches.
        status.phase = .verifying
        status.totalBytes = total
        await progress(status)
        var todo: [Job] = [], haveBytes: UInt64 = 0
        for job in jobs {
            try Task.checkCancellation()
            if let size = size(of: job.target), size == UInt64(job.file.size), (try? sha256(of: job.target)) == job.file.hash { haveBytes += size; continue }
            todo.append(job)
        }
        let needed = todo.reduce(UInt64(0)) { $0 + UInt64($1.file.size) }
        let values = try drive.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey, .volumeAvailableCapacityKey])
        let free = UInt64(max(0, max(values.volumeAvailableCapacityForImportantUsage ?? 0, Int64(values.volumeAvailableCapacity ?? 0))))
        guard free >= needed + 256 * 1024 * 1024 else {
            throw AmazonError.invalid("Not enough free space: this game needs about \(needed / 1_048_576) MB more.")
        }
        LogStore.shared.log("[amazon] build \(version.prefix(12)): \(jobs.count) file(s), \(todo.count) to fetch, \(total / 1_048_576) MB in all, \(haveBytes / 1_048_576) MB already here")

        let meter = AmazonMeter(total: total, alreadyHave: haveBytes)
        if let p = meter.progress(force: true) { await progress(p) }
        // The big files go one at a time; the small ones run several together.
        let big = todo.filter { $0.file.size >= 16 * 1024 * 1024 }, small = todo.filter { $0.file.size < 16 * 1024 * 1024 }
        for job in big {
            try Task.checkCancellation()
            try await fetch(job, parts: parts, source: source, meter: meter)
            if let p = meter.progress() { await progress(p) }
        }
        try await withThrowingTaskGroup(of: Void.self) { group in
            var iterator = small.makeIterator(), running = 0
            while true {
                while running < 8, let job = iterator.next() {
                    group.addTask { try await fetch(job, parts: parts, source: source, meter: meter) }
                    running += 1
                }
                guard running > 0 else { break }
                try await group.next()
                running -= 1
                if let p = meter.progress() { await progress(p) }
            }
        }
        if let p = meter.progress(force: true) { await progress(p) }
        status.phase = .finishing
        status.doneBytes = total
        await progress(status)
        try? FileManager.default.removeItem(at: parts)
        return try finish(game, folder: folder, root: root, version: version, installBytes: Int64(total))
    }

    /// Reads and checks the build's manifest.
    private static func fetchManifest(_ source: AmazonSource) async throws -> AmazonManifest {
        var request = URLRequest(url: await source.manifest())
        request.timeoutInterval = 60
        let data: Data, response: URLResponse
        do { (data, response) = try await AmazonNetwork.session.data(for: request) } catch { throw AmazonAuthError.network }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw AmazonError.invalid("Amazon did not give the file list for this game (answer \(status)).") }
        return try AmazonManifest.parse(data)
    }

    /// One file into place: fetched into the staging folder, checked against the manifest's hash, then moved in. An address that has run
    /// out of time answers 403: Amazon is asked for a fresh one (twice at most).
    private static func fetch(_ job: Job, parts: URL, source: AmazonSource, meter: AmazonMeter) async throws {
        try Task.checkCancellation()
        let part = parts.appendingPathComponent(job.file.hashHex + ".part")
        let expected = UInt64(job.file.size)
        var attempt = 0
        while true {
            attempt += 1
            try? FileManager.default.removeItem(at: part)
            let (url, generation) = await source.file(job.file.hashHex)
            if expected == 0 {
                FileManager.default.createFile(atPath: part.path, contents: nil)
            } else {
                var request = URLRequest(url: url)
                request.timeoutInterval = 120
                let (temporary, response) = try await AmazonNetwork.session.download(for: request)
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                if status == 403, attempt < 3 {
                    try? FileManager.default.removeItem(at: temporary)
                    try await source.renew(after: generation)
                    continue
                }
                guard status == 200 else {
                    try? FileManager.default.removeItem(at: temporary)
                    throw AmazonError.invalid("The download server answered \(status).")
                }
                try FileManager.default.moveItem(at: temporary, to: part)
            }
            guard size(of: part) == expected else {
                try? FileManager.default.removeItem(at: part)
                throw AmazonError.invalid("A game file arrived with the wrong size. Try again (finished files are kept).")
            }
            guard (try? sha256(of: part)) == job.file.hash else {
                try? FileManager.default.removeItem(at: part)
                throw AmazonError.invalid("\(job.file.path.split(separator: "\\").last.map(String.init) ?? "A game file") failed Amazon's checksum. Try again.")
            }
            try FileManager.default.createDirectory(at: job.target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: job.target)
            guard rename(part.path, job.target.path) == 0 else {
                throw AmazonError.invalid("A downloaded file could not be put in place. The device may be out of storage: free some space and start the download again.")
            }
            meter.add(expected)
            return
        }
    }

    // MARK: Helpers

    static func size(of url: URL) -> UInt64? { (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.uint64Value }

    static func sha256(of file: URL) throws -> Data {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hash = SHA256()
        while let block = try handle.read(upToCount: 4 * 1024 * 1024), !block.isEmpty { hash.update(data: block) }
        return Data(hash.finalize())
    }

    /// The path with each part spelled as it is on disk (matched ignoring case), or nil when some part is not there.
    static func resolveCase(root: URL, components: [String]) -> [String]? {
        var current = root, result: [String] = []
        for part in components {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: current.path)) ?? []
            guard let match = names.first(where: { $0 == part }) ?? names.first(where: { $0.lowercased() == part.lowercased() }) else { return nil }
            result.append(match)
            current.appendPathComponent(match)
        }
        return result
    }

    /// Reads the game's fuel.json for the program that starts it.
    static func finish(_ game: AmazonGame, folder: String, root: URL, version: String, installBytes: Int64) throws -> AmazonInstalledGame {
        let contents = (try? FileManager.default.contentsOfDirectory(atPath: root.path)) ?? []
        var command: String?, arguments: [String] = [], workSub: String?
        if let name = contents.first(where: { $0.lowercased() == "fuel.json" }), let data = try? Data(contentsOf: root.appendingPathComponent(name)),
           let launch = AmazonLaunch.parse(data) {
            command = launch.command; arguments = launch.arguments; workSub = launch.workingSubdirectory
            LogStore.shared.log("[amazon] fuel.json read")
        } else {
            let exes = contents.filter { $0.lowercased().hasSuffix(".exe") && !$0.lowercased().hasPrefix("unins") }
            if exes.count == 1 { command = exes[0]; LogStore.shared.log("[amazon] no fuel.json; using the only program in the folder") }
        }
        guard var program = command?.replacingOccurrences(of: "/", with: "\\"), safeRelativePath(program) != nil else {
            throw AmazonError.invalid("The game installed, but BCD could not tell which program starts it. Add it with Add a game (folder C:\\Amazon\\\(folder)).")
        }
        if let real = resolveCase(root: root, components: program.split(separator: "\\").map(String.init)) { program = real.joined(separator: "\\") }
        let windowsRoot = "C:\\Amazon\\\(folder)"
        let relative = program.replacingOccurrences(of: "\\", with: "/")
        guard FileManager.default.fileExists(atPath: root.appendingPathComponent(relative).path) else {
            throw AmazonError.invalid("The game installed but \(program) is not there.")
        }
        var workDir: String?
        if let sub = workSub?.replacingOccurrences(of: "/", with: "\\"), !sub.isEmpty, safeRelativePath(sub) != nil {
            let resolved = resolveCase(root: root, components: sub.split(separator: "\\").map(String.init))?.joined(separator: "\\") ?? sub
            workDir = windowsRoot + "\\" + resolved
        }
        let args = arguments.map { $0.contains(" ") ? "\"\($0)\"" : $0 }.joined(separator: " ")
        return AmazonInstalledGame(game: game, installDir: "Amazon/\(folder)", programPath: "Amazon/\(folder)/" + relative, arguments: args,
                                   workDir: workDir, version: version, installBytes: installBytes)
    }
}
