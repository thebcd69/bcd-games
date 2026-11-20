// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// What an Epic game's download is, turned into a BCDContent plan (docs/research/open-fork-2026-10-18/
// epic.md §2.3, §4.4): the asset service's pointer to the build (a manifest address on each CDN, with
// the CDN's token, the manifest's SHA-1 and an optional deployment ID), the manifest (BCDEpicManifest),
// and the plan the shared engine runs: every file made of parts, each a slice of an Epic chunk file
// (codec `.epic`), fetched once from the folder the manifest sits in (the chunks need no token). Written
// from the design's fact tables; no code of any other Epic client. Foundation only.
//
// Where the facts come from (the design's §6 table): the asset endpoint and its reply shape, the CDN
// rule ("chunks under the same folder need no token") and the SHA-1 of the manifest file, from the
// documentation column (the PlayPort README and evidence records, as the design cites them); the chunk
// path (directory by feature level, group number, rolling hash and GUID in upper-case hexadecimal) from
// the design's chunk table. None of it has been checked against Epic's servers from this code: the
// first install on a device is the check (BCDEpicContentError.logReason tells what failed).

/// Why a build could not be turned into a plan, in words for the player and a short reason for the log.
enum BCDEpicContentError: Error, Equatable, LocalizedError {
    case noBuild
    case badAsset(String)
    case noManifest
    case manifestHash

    var errorDescription: String? {
        switch self {
        case .noBuild: return "Epic doesn't offer a Windows download of this game right now."
        case .badAsset: return "Epic's answer about this game's download wasn't what BCD expects (it may have changed). Try again later."
        case .noManifest: return "BCD couldn't download the game's file list from any of Epic's servers. Check the connection and try again."
        case .manifestHash: return "The game's file list from Epic's servers didn't match its checksum. Try again later."
        }
    }

    var logReason: String {
        switch self {
        case .noBuild: return "no-build"
        case .badAsset(let what): return "asset-" + what
        case .noManifest: return "no-manifest"
        case .manifestHash: return "manifest-checksum"
        }
    }
}

extension BCDEpicContentError: BCDLogReasonError {}

/// What the asset service says about one build of a game.
struct BCDEpicBuild: Equatable {
    var buildVersion: String
    /// The SHA-1 of the manifest file as lower-case hexadecimal, when the service gives it.
    var manifestHash: String?
    /// Where the manifest can be fetched on each CDN (with that CDN's token), in the service's order.
    var manifestURLs: [URL]
    /// The folder each manifest sits in: the chunks are below it, and they need no token.
    var bases: [URL]
    /// A public build identifier some games take on their command line.
    var deploymentID: String?
    var revision: Int?
}

/// A snapshot of what a plan was made from, kept until the install is recorded (like GOG's).
struct BCDEpicPlanInfo: Codable, Equatable {
    var app: String
    var namespace: String
    var catalogItemId: String
    var title: String
    var buildVersion: String
    /// SHA-1 of the manifest file.
    var manifestHash: String
    var featureLevel: Int
    /// The program to start, relative to the game's folder, spelled as the plan spells it.
    var launchPath: String
    var launchCommand: String
    var extraCommand: String
    var deploymentID: String?
    var revision: Int?
    var canRunOffline: Bool?
    var eos: Bool
    var prerequisiteNote: String?
    var symlinksSkipped: Int
    /// The catalog says the game asks Epic for an ownership token each time it starts (milestone 2: the signed-in start passes it).
    var ownershipToken: Bool?
    /// The anti-cheat exception the build falls under (BCDEpicRules.antiCheatExceptions), by id.
    var exceptionID: String?
}

enum BCDEpicContent {
    static let assetHost = "launcher-public-service-prod06.ol.epicgames.com"

    static func assetURL(namespace: String, catalogItem: String, app: String) -> URL {
        URL(string: "https://\(assetHost)/launcher/api/public/assets/v2/platform/Windows/namespace/\(BCDEpicAuth.encode(namespace))"
            + "/catalogItem/\(BCDEpicAuth.encode(catalogItem))/app/\(BCDEpicAuth.encode(app))/label/Live")!
    }

    // MARK: The asset service's reply

    /// True in the app. The host checks' local CDN speaks plain http, so they turn this off.
    nonisolated(unsafe) static var secureOnly = true

    /// An address kept only as https (the CDN's reply may say http; ATS needs https), without credentials.
    static func secure(_ text: String) -> URL? {
        guard var parts = URLComponents(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = parts.scheme?.lowercased(), scheme == "https" || scheme == "http",
              let host = parts.host, !host.isEmpty, parts.user == nil, parts.password == nil else { return nil }
        if secureOnly {
            parts.scheme = "https"
            if parts.port == 80 || parts.port == 443 { parts.port = nil }
        }
        return parts.url
    }

    /// The folder an address is in: the address without its last path part and without a query.
    static func folder(of url: URL) -> URL? {
        guard var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        var path = parts.percentEncodedPath
        guard let slash = path.lastIndex(of: "/") else { return nil }
        path = String(path[..<slash])
        parts.percentEncodedPath = path
        parts.query = nil
        parts.fragment = nil
        return parts.url
    }

    /// The first element of the asset service's reply: the build's version, its manifest on each CDN and
    /// the SHA-1 of the manifest file, and (when there is one) the sidecar's deployment ID.
    static func parseAsset(_ object: [String: Any]) throws -> BCDEpicBuild {
        guard let elements = object["elements"] as? [[String: Any]], let element = elements.first else { throw BCDEpicContentError.noBuild }
        guard let version = element["buildVersion"] as? String, !version.isEmpty, version.utf8.count <= 200 else { throw BCDEpicContentError.badAsset("version") }
        guard let list = element["manifests"] as? [[String: Any]], !list.isEmpty else { throw BCDEpicContentError.badAsset("manifests") }
        var urls: [URL] = [], bases: [URL] = []
        for item in list.prefix(16) {
            guard let uri = item["uri"] as? String, let plain = secure(uri), let base = folder(of: plain) else { continue }
            var query: [String] = []
            for param in (item["queryParams"] as? [[String: Any]]) ?? [] {
                guard let name = param["name"] as? String, let value = param["value"] as? String, !name.isEmpty else { continue }
                query.append(BCDEpicAuth.encode(name) + "=" + BCDEpicAuth.encode(value))
            }
            var parts = URLComponents(url: plain, resolvingAgainstBaseURL: false)!
            if !query.isEmpty { parts.percentEncodedQuery = query.joined(separator: "&") }
            guard let withQuery = parts.url else { continue }
            urls.append(withQuery)
            bases.append(base)
        }
        guard !urls.isEmpty else { throw BCDEpicContentError.badAsset("uri") }
        var hash: String?
        if let text = element["hash"] as? String, text.utf8.count == 40, BCDDigest(.sha1, text) != nil { hash = text.lowercased() }
        var deployment: String?
        var revision: Int?
        if let sidecar = element["sidecar"] as? [String: Any] {
            revision = (sidecar["rvn"] as? NSNumber)?.intValue
            if let config = sidecar["config"] as? String, config.utf8.count <= 65_536,
               let object = try? JSONSerialization.jsonObject(with: Data(config.utf8)) as? [String: Any],
               let id = object["deploymentId"] as? String, BCDEpicLaunch.validDeploymentID(id) {
                deployment = id.lowercased()
            }
        }
        return BCDEpicBuild(buildVersion: version, manifestHash: hash, manifestURLs: urls, bases: bases, deploymentID: deployment, revision: revision)
    }

    /// `bases` with the one whose host contains `preferred` (a bcd.cfg line, `env.BCD_EPIC_CDN`) first.
    static func order(_ bases: [URL], preferring preferred: String?) -> [URL] {
        guard let preferred = preferred?.lowercased(), !preferred.isEmpty else { return bases }
        return bases.enumerated().sorted {
            let a = ($0.element.host ?? "").lowercased().contains(preferred), b = ($1.element.host ?? "").lowercased().contains(preferred)
            return a != b ? a : $0.offset < $1.offset
        }.map(\.element)
    }

    // MARK: The manifest

    typealias FetchAsset = (URL) async throws -> [String: Any]
    typealias FetchFile = (URL) async throws -> Data

    struct Loaded {
        var build: BCDEpicBuild
        /// The CDN folders, the one that served the manifest first.
        var bases: [URL]
        /// The manifest file as served (kept next to the install), its SHA-1 and what it says.
        var file: Data
        var hash: String
        var manifest: BCDEpicManifest
    }

    /// The asset pointer, then the manifest from the first CDN that serves one whose SHA-1 matches the
    /// pointer's. A CDN that fails or serves something else is skipped (and counted in the log).
    static func load(app: String, namespace: String, catalogItem: String, preferring: String?,
                     fetchAsset: FetchAsset, fetchFile: FetchFile) async throws -> Loaded {
        let build = try parseAsset(try await fetchAsset(assetURL(namespace: namespace, catalogItem: catalogItem, app: app)))
        var failed = 0, mismatched = 0
        for (index, url) in build.manifestURLs.enumerated() {
            try Task.checkCancellation()
            let data: Data
            do { data = try await fetchFile(url) } catch is CancellationError { throw CancellationError() } catch { failed += 1; continue }
            let hash = BCDHasher.hex(.sha1, of: data)
            if let expected = build.manifestHash, expected != hash { mismatched += 1; continue }
            let manifest: BCDEpicManifest
            do { manifest = try BCDEpicManifest.parse(data) } catch {
                // Round 668 (merge N008; Bus Simulator 21 0C8N69): which form the file was when neither reader took it (the first eight bytes
                // of a manifest are not personal data).
                BCDEpicLog.write("[epic-cdn] manifest not read: reason=\(BCDEpicLog.reason(error)) bytes=\(data.count) "
                                 + "head=\(data.prefix(8).map { String(format: "%02x", $0) }.joined())")
                throw error
            }
            let rest = build.bases.enumerated().filter { $0.offset != index }.map(\.element)
            BCDEpicLog.write("[epic-cdn] manifest from cdn \(index + 1) of \(build.manifestURLs.count) failed=\(failed) mismatched=\(mismatched) "
                             + "level=\(manifest.featureLevel) files=\(manifest.files.count) chunks=\(manifest.chunks.count) bytes=\(data.count) "
                             + "checked=\(build.manifestHash == nil ? 0 : 1)")
            return Loaded(build: build, bases: order([build.bases[index]] + rest, preferring: preferring), file: data, hash: hash, manifest: manifest)
        }
        BCDEpicLog.write("[epic-cdn] no manifest from \(build.manifestURLs.count) cdn failed=\(failed) mismatched=\(mismatched)")
        throw mismatched > 0 && failed == 0 ? BCDEpicContentError.manifestHash : BCDEpicContentError.noManifest
    }

    // MARK: Chunks

    /// A chunk's path below the manifest's folder: the directory follows the feature level, then the group
    /// number, then the rolling hash (16 digits) and the GUID (32 digits), upper-case hexadecimal.
    static func chunkPath(featureLevel: Int, _ chunk: BCDEpicManifest.Chunk) -> String {
        let directory = featureLevel >= 15 ? "ChunksV4" : featureLevel >= 6 ? "ChunksV3" : "ChunksV2"
        return directory + "/" + String(format: "%02d", Int(chunk.group)) + "/" + String(format: "%016llX", chunk.rollingHash) + "_" + chunk.guid.hex + ".chunk"
    }

    /// A locator is what `chunkPath` makes: nothing else is ever appended to a CDN folder.
    static func validLocator(_ text: String) -> Bool {
        guard text.utf8.count <= 96, text.hasSuffix(".chunk") else { return false }
        let parts = text.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 3, ["ChunksV2", "ChunksV3", "ChunksV4"].contains(String(parts[0])),
              (2...3).contains(parts[1].utf8.count), parts[1].utf8.allSatisfy({ $0 >= 0x30 && $0 <= 0x39 }) else { return false }
        let name = parts[2].dropLast(6).split(separator: "_", omittingEmptySubsequences: false)
        func upperHex(_ s: Substring, _ n: Int) -> Bool { s.utf8.count == n && s.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x46) } }
        return name.count == 2 && upperHex(name[0], 16) && upperHex(name[1], 32)
    }

    // MARK: The plan

    /// The plan that installs `loaded`'s manifest into `folder`, and what it was made from. Throws
    /// BCDEpicRefused for a build BCD will not install (anti-cheat, no program). Symlinks are skipped (Windows
    /// builds hold none); install tags are ignored (everything is installed).
    static func plan(_ key: BCDContentKey, title: String, folder: String, loaded: Loaded, item: BCDEpicItem) async throws -> (plan: BCDContentPlan, info: BCDEpicPlanInfo) {
        let manifest = loaded.manifest
        let names = manifest.files.map(\.name)
        if let refusal = BCDEpicRules.manifestRefusal(files: names, launchExe: manifest.launchExe) { throw BCDEpicRefused(refusal) }
        var builder = try BCDContentPlanBuilder(key: key, title: title, version: loaded.build.buildVersion, folder: folder)
        var skipped = 0
        for file in manifest.files {
            if !file.symlinkTarget.isEmpty { skipped += 1; continue }
            let digest = file.sha1.contains { $0 != 0 } ? BCDDigest(.sha1, file.sha1.map { String(format: "%02x", $0) }.joined()) : nil
            let index = try builder.addFile(file.name, size: file.size, digest: digest)
            var offset: Int64 = 0
            for part in file.parts where part.size > 0 {
                let chunk = manifest.chunks[part.chunk]
                let shaZero = !chunk.sha1.contains { $0 != 0 }
                let made = BCDContentChunk(locator: chunkPath(featureLevel: manifest.featureLevel, chunk), group: "epic",
                                           fetchSize: chunk.fileSize, size: chunk.window, fetchDigest: nil,
                                           digest: shaZero ? nil : BCDDigest(.sha1, chunk.sha1.map { String(format: "%02x", $0) }.joined()),
                                           codec: .epic)
                try builder.addPart(chunk: try builder.addChunk(made), chunkOffset: part.offset, length: part.size, file: index, fileOffset: offset)
                offset += part.size
            }
        }
        let plan = try builder.build()
        // The program: the manifest names it, and the plan spells its path (the device's files are case-sensitive).
        var launch = manifest.launchExe.replacingOccurrences(of: "\\", with: "/")
        while launch.hasPrefix("./") { launch.removeFirst(2) }
        guard let spelled = plan.files.first(where: { BCDContentPaths.fold($0.path) == BCDContentPaths.fold(launch) })?.path else {
            throw BCDEpicRefused(.init(code: "no-program", message: "The program Epic names for this game isn't among its files."))
        }
        let info = BCDEpicPlanInfo(app: key.id, namespace: item.namespace, catalogItemId: item.catalogItemId, title: title,
                                   buildVersion: loaded.build.buildVersion, manifestHash: loaded.hash, featureLevel: manifest.featureLevel,
                                   launchPath: spelled, launchCommand: manifest.launchCommand, extraCommand: item.extraArguments ?? "",
                                   deploymentID: loaded.build.deploymentID, revision: loaded.build.revision, canRunOffline: item.canRunOffline,
                                   eos: BCDEpicRules.usesEOS(names),
                                   prerequisiteNote: BCDEpicRules.prerequisiteNote(name: manifest.prereqName, path: manifest.prereqPath),
                                   symlinksSkipped: skipped, ownershipToken: item.ownershipToken,
                                   exceptionID: BCDEpicRules.antiCheatException(files: names, launchExe: manifest.launchExe)?.id)
        BCDEpicLog.write("[epic-install] plan app=\(BCDEpicLog.app(key.id)) level=\(manifest.featureLevel) files=\(plan.files.count) chunks=\(plan.chunks.count) "
                         + "download=\(plan.downloadBytes) install=\(plan.totalBytes) symlinks=\(skipped) eos=\(info.eos ? 1 : 0) "
                         + "prereq=\(info.prerequisiteNote == nil ? 0 : 1) deployment=\(info.deploymentID == nil ? 0 : 1) "
                         + "exception=\(BCDEpicRules.sanitized(info.exceptionID ?? "none"))")
        return (plan, info)
    }
}

/// What a game's manifest says before anything is downloaded, for the game's page: how big the install is, whether it can be
/// installed at all, and whether it may not start (milestone 1's "download sheet": sizes and reasons ahead of the download).
struct BCDEpicFacts: Equatable {
    var buildVersion: String
    /// Bytes the install writes, and the bytes of the chunk files it fetches.
    var installBytes: Int64
    var downloadBytes: Int64
    var eos: Bool
    var refusal: BCDEpicRules.Refusal?
    /// The anti-cheat exception the build falls under: the game installs and starts without its online anti-cheat.
    var exception: BCDEpicRules.AntiCheatException?
}

/// The facts a look at the manifest gave (nil: it could not be read) and when.
struct BCDEpicPreflight: Equatable {
    var facts: BCDEpicFacts?
    var at: Date
}

extension BCDEpicContent {
    /// What a look learns from a manifest BCD will not read (encrypted, or a newer format): a refusal with no sizes. Any other error is
    /// nil (the look failed; the page says nothing and looks again later).
    static func facts(from error: Error) -> BCDEpicFacts? {
        guard let error = error as? BCDEpicManifestError else { return nil }
        switch error {
        case .encrypted, .newer:
            return BCDEpicFacts(buildVersion: "", installBytes: 0, downloadBytes: 0, eos: false,
                                refusal: BCDEpicRules.Refusal(code: error.logReason, message: error.errorDescription ?? ""))
        default:
            return nil
        }
    }

    static func facts(_ loaded: Loaded) -> BCDEpicFacts {
        let manifest = loaded.manifest
        let names = manifest.files.map(\.name)
        return BCDEpicFacts(buildVersion: loaded.build.buildVersion, installBytes: manifest.totalBytes, downloadBytes: manifest.downloadBytes,
                            eos: BCDEpicRules.usesEOS(names), refusal: BCDEpicRules.manifestRefusal(files: names, launchExe: manifest.launchExe),
                            exception: BCDEpicRules.antiCheatException(files: names, launchExe: manifest.launchExe))
    }
}

/// A build BCD will not install: the queue shows its message and the facade remembers it.
struct BCDEpicRefused: Error, LocalizedError, BCDLogReasonError {
    var refusal: BCDEpicRules.Refusal
    init(_ refusal: BCDEpicRules.Refusal) { self.refusal = refusal }
    var errorDescription: String? { refusal.message }
    var logReason: String { "refused-" + refusal.code }
}

/// Epic's chunks for one job: the CDN folders in preference order, each chunk's path below them. They need no
/// token, so a host that refuses (401, 403, 410) gets its folders renewed from the asset service.
actor BCDEpicChunkSource: BCDChunkSource {
    private var bases: [URL]
    private let renew: @Sendable () async throws -> [URL]

    init(bases: [URL], renew: @escaping @Sendable () async throws -> [URL]) {
        self.bases = bases
        self.renew = renew
    }

    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        guard BCDEpicContent.validLocator(chunk.locator) else { return [] }
        return bases.compactMap { URL(string: $0.absoluteString + "/" + chunk.locator) }
    }

    func refresh(group: String) async throws {
        let fresh = try await renew()
        if !fresh.isEmpty { bases = fresh }
        BCDEpicLog.write("[epic-cdn] folders renewed count=\(bases.count)")
    }
}
