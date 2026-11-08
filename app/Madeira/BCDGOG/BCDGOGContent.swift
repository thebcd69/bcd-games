// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// What a GOG game's download is, turned into a BCDContent plan (gog.md §2.3; the format as GOG's
// own public manifests show it, docs/GOG.md): the newest public build of content system
// generation 2, its build manifest (zlib JSON), the depots for the game, the language and 64-bit
// (or neutral) builds, each depot's manifest of files made of chunks (zlib, checked by MD5 before
// and after unpacking), and the secure links that serve the chunks. Foundation only.

struct BCDGOGBuild: Equatable {
    var id: String
    var version: String
    var link: URL
}

struct BCDGOGBuildManifest {
    struct Depot: Equatable {
        var manifest: String
        var productID: String
        var languages: [String]
        var bitness: [String]?
        var gogDepot: Bool
        /// The depot's files' size on the disk, as the build manifest states it (0 when it states none): a repair's progress.
        var size: Int64 = 0
    }
    var baseProductID: String
    var buildID: String
    var installDirectory: String
    var depots: [Depot]
    var dependencies: [String]
    /// The game's own Galaxy identity (for BCD Pier, M3): never logged.
    var clientID: String?
}

/// What the plan was made from, kept until the install is recorded.
struct BCDGOGPlanInfo: Codable, Equatable {
    var productID: String
    var title: String
    var buildID: String
    var version: String
    var language: String
    var depots: [String]
    var dependencies: [String]
    var supportFiles: Int
    var links: Int
    var containers: Bool
    /// Build 632: the redistributables' installers the first start runs, and the ones left out with the reason. nil: the plan did not
    /// ask for redistributables (a plan made before, or redistributables off).
    var setup: [GOGSetupProgram]? = nil
    var setupNotes: [GOGSetupNote]? = nil
    /// Build 632: what the job is (nil: an install, as every plan before), the DLC products whose depots the plan chose, the old
    /// build's files the new one no longer has (an update), and the size the game takes on the disk once the job is done (the plan
    /// of an update or a repair holds only the files that change).
    var kind: BCDGOGJobKind? = nil
    var dlc: [String]? = nil
    var removed: [String]? = nil
    var installBytes: Int64? = nil
}

enum BCDGOGContent {
    static func buildsURL(_ id: String) -> URL {
        URL(string: "https://content-system.gog.com/products/\(id)/os/windows/builds?generation=2")!
    }

    static func secureLinkURL(_ id: String) -> URL {
        URL(string: "https://content-system.gog.com/products/\(id)/secure_link?generation=2&_version=2&path=/")!
    }

    /// The group of the redistributables' chunks: their links are GOG's open link (no sign-in), not a product's secure link.
    static let redistGroup = "redist"

    /// GOG's dependency repository (a pointer to a zlib JSON manifest that lists every redistributable), GOG's open link for the
    /// chunks of redistributables, and one redistributable's file list on GOG's CDN. No sign-in for any of them.
    static var repositoryURL: URL { URL(string: "https://content-system.gog.com/dependencies/repository?generation=2")! }
    static var openLinkURL: URL { URL(string: "https://content-system.gog.com/open_link?generation=2&_version=2&path=/dependencies/store/")! }
    static func dependencyDepotURL(_ hash: String) -> URL? {
        let hash = hash.lowercased()
        guard validHash(hash) else { return nil }
        return URL(string: "https://cdn.gog.com/content-system/v2/dependencies/meta/\(hash.prefix(2))/\(hash.dropFirst(2).prefix(2))/\(hash)")
    }

    static func validHash(_ text: String) -> Bool {
        text.utf8.count == 32 && text.utf8.allSatisfy { ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102) }
    }

    /// A depot manifest's address, on the host the build's own link uses (GOG's CDN).
    static func depotURL(_ hash: String, near link: URL) -> URL {
        let host = BCDGOGAPI.trusted(link) ? link.host! : "cdn.gog.com"
        return URL(string: "https://\(host)/content-system/v2/meta/\(hash.prefix(2))/\(hash.dropFirst(2).prefix(2))/\(hash)")!
    }

    /// A build link on a CDN outside GOG's own hosts, read from GOG's cdn.gog.com instead (nil when it is not a
    /// manifest link). GOG's build list names the CDN it picks for the player, and in some regions that is not a gog.com
    /// host (2026-10-24: gog-cdn.gcdn.co; BioShock 2, Cult of the Lamb and RollerCoaster Tycoon Deluxe stopped with "GOG's
    /// reply wasn't what BCD expects"). A manifest is a file named by the MD5 of its bytes under
    /// /content-system/v2/meta/<c0c1>/<c2c3>/<md5>, and every GOG CDN serves the same file under that path, so only a link
    /// of exactly that form moves to cdn.gog.com (the host depotURL already falls back to). A link on a gog.com host is
    /// used as before.
    static func gogHostLink(_ link: URL) -> URL? {
        guard link.scheme == "https", link.user == nil, link.query == nil, link.fragment == nil else { return nil }
        let parts = link.path.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count == 7, parts[0].isEmpty, parts[1] == "content-system", parts[2] == "v2", parts[3] == "meta" else { return nil }
        let hash = String(parts[6])
        guard validHash(hash), parts[4] == hash.prefix(2), parts[5] == hash.dropFirst(2).prefix(2) else { return nil }
        return URL(string: "https://cdn.gog.com/content-system/v2/meta/\(parts[4])/\(parts[5])/\(hash)")
    }

    /// The newest public generation-2 build (the list is newest first).
    static func newestBuild(_ object: [String: Any]) throws -> BCDGOGBuild {
        guard let items = object["items"] as? [[String: Any]] else {
            BCDContentLog.write("[gog-content] builds reply: " + BCDGOGAPI.shape(object, lists: ["items"]))
            throw BCDGOGError.badReply("builds")
        }
        var older = false
        for item in items {
            let generation = (item["generation"] as? NSNumber)?.intValue ?? 0
            guard item["branch"] == nil || item["branch"] is NSNull else { continue }
            if generation == 1 { older = true; continue }
            guard generation == 2 else { continue }
            let id = (item["build_id"] as? String) ?? (item["build_id"] as? NSNumber)?.stringValue ?? ""
            let given = (item["link"] as? String).flatMap(URL.init(string:))
            let moved = given.flatMap { BCDGOGAPI.trusted($0) ? nil : gogHostLink($0) }
            guard BCDGOGCatalog.validID(id), let given, let link = BCDGOGAPI.trusted(given) ? given : moved else {
                // The host of the link is GOG's own to say, so it is the one thing named: BCD follows only gog.com
                // and gog-statics.com hosts. The build's id is a number of GOG's, not the player's.
                let where_: String
                if let text = item["link"] as? String, let target = URL(string: text) {
                    where_ = BCDGOGAPI.trusted(target) ? "gog" : (target.scheme == "https" ? "other-host" : "not-secure")
                } else {
                    where_ = "none"
                }
                BCDContentLog.write("[gog-content] build entry: id=\(BCDGOGCatalog.validID(id) ? "ok" : "bad") target=\(where_) "
                                    + BCDGOGAPI.shape(item, lists: []))
                throw BCDGOGError.badReply("build")
            }
            if moved != nil {
                BCDContentLog.write("[gog-content] build manifest read from cdn.gog.com (the list named a CDN outside gog.com)")
            }
            let version = ((item["version_name"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return BCDGOGBuild(id: id, version: String(version.prefix(80)), link: link)
        }
        throw older ? BCDGOGError.olderBuildsOnly : BCDGOGError.noWindowsBuild
    }

    static func parseBuildManifest(_ object: [String: Any]) throws -> BCDGOGBuildManifest {
        func text(_ value: Any?) -> String? { (value as? String) ?? (value as? NSNumber)?.stringValue }
        guard (object["version"] as? NSNumber)?.intValue == 2, let base = text(object["baseProductId"]), BCDGOGCatalog.validID(base),
              let depots = object["depots"] as? [[String: Any]] else {
            BCDContentLog.write("[gog-content] build manifest: " + BCDGOGAPI.shape(object, lists: ["depots"]))
            throw BCDGOGError.badReply("manifest")
        }
        var list: [BCDGOGBuildManifest.Depot] = []
        for depot in depots {
            guard let hash = depot["manifest"] as? String, validHash(hash), let product = text(depot["productId"]),
                  BCDGOGCatalog.validID(product) else {
                BCDContentLog.write("[gog-content] build manifest depot: " + BCDGOGAPI.shape(depot, lists: []) +
                                    " hash-bytes=\((depot["manifest"] as? String)?.utf8.count ?? -1)")
                throw BCDGOGError.badReply("depot")
            }
            let languages = (depot["languages"] as? [String] ?? []).map { $0.trimmingCharacters(in: .whitespaces) }
            list.append(.init(manifest: hash, productID: product, languages: languages,
                              bitness: depot["osBitness"] as? [String], gogDepot: depot["isGogDepot"] as? Bool ?? false,
                              size: max(0, (depot["size"] as? NSNumber)?.int64Value ?? 0)))
        }
        let client = text(object["clientId"]).flatMap { BCDGOGCatalog.validID($0) ? $0 : nil }
        return BCDGOGBuildManifest(baseProductID: base, buildID: text(object["buildId"]) ?? "",
                                   installDirectory: (object["installDirectory"] as? String) ?? "",
                                   depots: list, dependencies: (object["dependencies"] as? [String] ?? []).filter { $0.count <= 64 },
                                   clientID: client)
    }

    /// The language to install: the device's first preferred language GOG offers (exactly, then by
    /// language and script, then by language alone), else US English, else any English, else the
    /// first one offered. GOG writes "en-US", "zh-Hans" and sometimes "en".
    static func language(offered: [String], preferred: [String]) -> String {
        let offered = offered.filter { $0 != "*" && !$0.isEmpty }
        guard let first = offered.first else { return "*" }
        func pieces(_ code: String) -> [String] { code.replacingOccurrences(of: "_", with: "-").lowercased().split(separator: "-").map(String.init) }
        for wanted in preferred {
            let w = pieces(wanted)
            guard let primary = w.first else { continue }
            if let exact = offered.first(where: { pieces($0) == w }) { return exact }
            if w.count >= 2, let script = offered.first(where: { Array(pieces($0).prefix(2)) == Array(w.prefix(2)) }) { return script }
            if let bare = offered.first(where: { pieces($0) == [primary] }) { return bare }
            if let same = offered.first(where: { pieces($0).first == primary }) { return same }
        }
        return offered.first { pieces($0) == ["en", "us"] } ?? offered.first { pieces($0).first == "en" } ?? first
    }

    /// The depots to install for `products`: the language's and the neutral ones, 64-bit when the
    /// build has 64-bit depots (else 32-bit), each manifest once.
    static func depots(_ manifest: BCDGOGBuildManifest, products: Set<String>, language: String) -> [BCDGOGBuildManifest.Depot] {
        let mine = manifest.depots.filter { products.contains($0.productID) }
        let bits = mine.contains { $0.bitness?.contains("64") == true } ? "64" : "32"
        var seen = Set<String>()
        return mine.filter { depot in
            let languageOK = depot.languages.contains("*") || depot.languages.contains { $0.caseInsensitiveCompare(language) == .orderedSame }
                || (language == "*" && depot.languages.isEmpty)
            let bitsOK = depot.bitness == nil || depot.bitness!.isEmpty || depot.bitness!.contains(bits)
            return languageOK && bitsOK && seen.insert(depot.manifest).inserted
        }
    }

    struct DepotResult: Equatable {
        var files = 0
        var support = 0
        var links = 0
        var containerFiles = 0
        /// Support-flagged files kept: the install script and the support folder's app/ files. They are added after every
        /// depot's own files, and never over a file of the game's own (`addSupportFiles`).
        var kept: [SupportFile] = []
        /// An update or a repair: the folded path of every file the depot lists (the files an old build had and this one has not are
        /// removed), the files left out of the plan because the game has them right, and their size.
        var seen: [String] = []
        var current = 0
        var currentBytes: Int64 = 0
        var installBytes: Int64 = 0
        var scripts: Int { kept.filter(\.isScript).count }
        var appFiles: Int { kept.filter { !$0.isScript }.count }
    }

    /// A support-flagged file of a depot that the install keeps: GOG's install script (goggame-<id>.script, read for its
    /// registry values by BCDGOGRegistry) at the folder's root, or a file of the support folder's `app/` (DOSBox games' .conf
    /// files), which the script's supportData action copies into the game's folder.
    struct SupportFile: Equatable {
        var path: String
        var isScript: Bool
        var size: Int64
        var digest: BCDDigest?
        var chunks: [BCDContentChunk]
    }

    /// What a support-flagged file of a depot is kept as: the install script (its name, at the root) or a file of the
    /// support folder's `app/` (the path below it). nil: left out, as every support file was before.
    enum SupportKept: Equatable {
        case script(String)
        case app(String)
    }

    static func supportKept(_ path: String) -> SupportKept? {
        let parts = path.replacingOccurrences(of: "\\", with: "/").split(separator: "/").map(String.init)
        if let name = parts.last, GOGInstallScript.isScriptName(name) { return .script(name) }
        if parts.count > 1, parts[0].lowercased() == "app" { return .app(parts.dropFirst().joined(separator: "/")) }
        return nil
    }

    private static let knownFileKeys: Set<String> = ["path", "type", "chunks", "md5", "sha256", "flags", "sfcRef"]

    /// One depot manifest into the plan. Files flagged `support` (GOG's own setup and its helpers)
    /// are left out until BCD runs them (M2). Links are macOS-only and skipped. With `containers`,
    /// small files GOG also packs into the depot's small-files container are cut from it (one
    /// fetch instead of one per file) when every reference is consistent; otherwise, and by
    /// default, each file comes from its own chunks.
    ///
    /// `isCurrent` (an update or a repair): asked for every file with its facts; true leaves the file out of the plan, because the
    /// game has it right already.
    static func add(depot object: [String: Any], product: String, to builder: inout BCDContentPlanBuilder,
                    containers: Bool, isCurrent: ((BCDGOGFileFacts) -> Bool)? = nil) throws -> DepotResult {
        guard let depot = object["depot"] as? [String: Any], let items = depot["items"] as? [[String: Any]] else {
            throw BCDGOGError.badReply("depot-manifest")
        }
        func number(_ value: Any?) -> Int64? { (value as? NSNumber)?.int64Value }
        func chunk(_ c: [String: Any]) throws -> BCDContentChunk {
            guard let md5 = c["md5"] as? String, let packed = c["compressedMd5"] as? String,
                  let size = number(c["size"]), let packedSize = number(c["compressedSize"]), size > 0, packedSize > 0,
                  let digest = BCDDigest(.md5, md5), let fetchDigest = BCDDigest(.md5, packed) else {
                throw BCDGOGError.badReply("chunk")
            }
            return BCDContentChunk(locator: fetchDigest.hex, group: product, fetchSize: packedSize, size: size,
                                   fetchDigest: fetchDigest, digest: digest, codec: .zlib)
        }

        // The small-files container: usable when every reference lies inside it and matches its
        // file. A file listed without chunks of its own can only come from it.
        var container: [(index: Int, start: Int64)] = []
        var containerSize: Int64 = 0
        if let sfc = depot["smallFilesContainer"] as? [String: Any], let chunks = sfc["chunks"] as? [[String: Any]], !chunks.isEmpty {
            for c in chunks {
                let made = try chunk(c)
                container.append((try builder.addChunk(made), containerSize))
                containerSize += made.size
            }
            for item in items {
                guard let ref = item["sfcRef"] as? [String: Any] else { continue }
                let own = item["chunks"] as? [[String: Any]] ?? []
                let size = own.reduce(Int64(0)) { $0 + (number($1["size"]) ?? -1) }
                guard let offset = number(ref["offset"]), let length = number(ref["size"]), offset >= 0, length >= 0,
                      own.isEmpty || length == size, offset + length <= containerSize else { container = []; break }
            }
        }

        var result = DepotResult()
        for item in items {
            let type = item["type"] as? String ?? ""
            guard let listed = item["path"] as? String else { throw BCDGOGError.badReply("item") }
            switch type {
            case "DepotDirectory":
                try builder.addDirectory(listed)
                continue
            case "DepotLink":
                result.links += 1
                continue
            case "DepotFile":
                break
            default:
                throw BCDGOGError.unknownFileSource
            }
            if (item["flags"] as? [String] ?? []).contains("support") {
                // GOG's setup and its helpers stay out. The install script (read for its registry values) and the support
                // folder's app/ files (copied into the game's folder by the script's supportData action) are kept when they
                // have chunks of their own and read cleanly; anything else about them leaves them out, as before.
                let own = item["chunks"] as? [[String: Any]] ?? []
                if let kept = supportKept(listed), !own.isEmpty, let chunks = try? own.map(chunk) {
                    let digest = (item["md5"] as? String).flatMap { BCDDigest(.md5, $0) } ?? (item["sha256"] as? String).flatMap { BCDDigest(.sha256, $0) }
                    let size = chunks.reduce(Int64(0)) { $0 + $1.size }
                    switch kept {
                    case .script(let name): result.kept.append(SupportFile(path: name, isScript: true, size: size, digest: digest, chunks: chunks))
                    case .app(let below): result.kept.append(SupportFile(path: below, isScript: false, size: size, digest: digest, chunks: chunks))
                    }
                } else {
                    result.support += 1
                }
                continue
            }
            let path = listed
            guard Set(item.keys).isSubset(of: knownFileKeys) || !(item["chunks"] as? [[String: Any]] ?? []).isEmpty else {
                throw BCDGOGError.unknownFileSource
            }
            let chunks = try (item["chunks"] as? [[String: Any]] ?? []).map(chunk)
            let ref = item["sfcRef"] as? [String: Any]
            let sliced = !container.isEmpty && ref != nil && (containers || chunks.isEmpty)
            if chunks.isEmpty, ref != nil, !sliced {
                throw BCDGOGError.unknownFileSource   // its bytes are only in a container BCD can't use
            }
            let size = sliced ? (number(ref?["size"]) ?? 0) : chunks.reduce(Int64(0)) { $0 + $1.size }
            let digest = (item["md5"] as? String).flatMap { BCDDigest(.md5, $0) }
                ?? (item["sha256"] as? String).flatMap { BCDDigest(.sha256, $0) }
            if let isCurrent {
                let slash = path.replacingOccurrences(of: "\\", with: "/")
                result.seen.append(BCDContentPaths.fold(slash))
                result.installBytes += size
                let facts = BCDGOGFileFacts(path: slash, size: size, digest: digest, chunks: sliced ? [] : chunks, identity: BCDGOGUpdate.identity(ofItem: item))
                if isCurrent(facts) {
                    result.current += 1
                    result.currentBytes += size
                    continue
                }
            }
            let file = try builder.addFile(path, size: size, digest: digest)
            result.files += 1
            if sliced {
                // The file's bytes are [offset, offset + size) of the container: a slice of each
                // container chunk that range crosses.
                let offset = number(ref?["offset"]) ?? 0
                for (index, (chunkIndex, start)) in container.enumerated() {
                    let end = index + 1 < container.count ? container[index + 1].start : containerSize
                    let from = max(offset, start), to = min(offset + size, end)
                    if from < to {
                        try builder.addPart(chunk: chunkIndex, chunkOffset: from - start, length: to - from, file: file, fileOffset: from - offset)
                    }
                }
                result.containerFiles += 1
                continue
            }
            var offset: Int64 = 0
            for c in chunks {
                try builder.addPart(chunk: builder.addChunk(c), chunkOffset: 0, length: c.size, file: file, fileOffset: offset)
                offset += c.size
            }
        }
        return result
    }

    // MARK: The plan

    /// GOG's reply for an address, signed in or not (BCDGOGAPI.json in the app, a fixture in the
    /// host checks).
    typealias Fetch = (URL, Bool) async throws -> [String: Any]

    /// The newest public build of a product.
    static func newest(_ id: String, fetch: Fetch) async throws -> BCDGOGBuild {
        try newestBuild(await fetch(buildsURL(id), true))
    }

    // MARK: DLC and updates

    /// The products the account owns (games and DLC): GOG's own list, signed in.
    static var ownedURL: URL { URL(string: "https://embed.gog.com/user/data/games")! }

    static func parseOwned(_ object: [String: Any]) -> Set<String> {
        guard let list = object["owned"] as? [Any] else { return [] }
        return Set(list.compactMap { (($0 as? NSNumber)?.stringValue ?? ($0 as? String)).flatMap { BCDGOGCatalog.validID($0) ? $0 : nil } })
    }

    /// The products the account owns, for the DLC of an install, an update or a repair. `required`: an update or a repair must not go on
    /// without it (a DLC's files would be taken for files the build no longer has), so its failure is thrown; an install without it is
    /// the game alone (logged).
    static func owned(fetch: Fetch, required: Bool) async throws -> Set<String> {
        do {
            return parseOwned(try await fetch(ownedURL, true))
        } catch {
            if error is CancellationError || Task.isCancelled || (error as? URLError)?.code == .cancelled || required { throw error }
            BCDContentLog.write("[gog-content] the owned list was not read reason=\(reasonText(error)); the game alone")
            return []
        }
    }

    /// What GOG has for an installed game that the game does not: a newer build, or an owned DLC with depots (`dlc`).
    struct Available: Equatable {
        var buildID: String
        var version: String
        var dlc: [String]
        var newerBuild: Bool
    }

    /// Whether an installed game has an update: the newest public build is another one than the installed `buildID`, or (same build)
    /// the account owns DLC whose depots the game's `depots` do not hold. nil: nothing. Reads the build list, and for the same build
    /// the build's manifest and the owned list (`dlc` off: not the DLC).
    static func available(product: String, buildID: String, depots installed: [String], language: String, dlc: Bool,
                          fetch: Fetch) async throws -> Available? {
        let build = try await newest(product, fetch: fetch)
        if build.id != buildID { return Available(buildID: build.id, version: build.version, dlc: [], newerBuild: true) }
        guard dlc else { return nil }
        let owned = try await owned(fetch: fetch, required: true)
        guard owned.contains(where: { $0 != product }) else { return nil }
        let manifest = try parseBuildManifest(await fetch(build.link, false))
        let wanted = depots(manifest, products: Set([product]).union(owned), language: language)
        let have = Set(installed)
        let added = Set(wanted.filter { !have.contains($0.manifest) && $0.productID != product }.map(\.productID))
        return added.isEmpty ? nil : Available(buildID: build.id, version: build.version, dlc: added.sorted(), newerBuild: false)
    }

    /// What a plan for an installed game compares with (build 632): an update or a repair plans only the files the game lacks or has
    /// wrong.
    struct Current {
        /// `.update` or `.repair`.
        var kind: BCDGOGJobKind
        /// The game's folder on the device.
        var folder: URL
        /// The installed build's depot manifests (their hashes are on the record): an update compares with them, so the disk is not read.
        var oldDepots: [String]
        /// The language the game was installed in: kept, so the files of another language are not taken for removed ones.
        var language: String
        /// Bytes read and bytes to read, while files are being checked (a repair's progress).
        var report: @Sendable (Int64, Int64) -> Void = { _, _ in }
    }

    /// The plan to install `build` of the game `key.id`: its depots for the device's language,
    /// files from the depot manifests, a later depot winning a path. Off the main thread: big
    /// manifests take a moment to read.
    ///
    /// Build 632: with `redistributables`, the redistributables the build names (GOG's dependency repository; DirectX, Visual C++
    /// 2015-2022 and .NET Framework are left out, GOGSetup) have their files added under the game's folder after the game's own
    /// files, and the installers the first start runs are listed on the info. Never fails the plan: a repository that cannot be
    /// read leaves the redistributables out with a note (Repair tries again). `ownedDLC` are the products of DLC the account owns:
    /// their depots are planned with the game's (same language, same bitness rule). `current`: an update or a repair of the game
    /// installed in `current.folder`, whose plan holds only the files that change; the info says which files of the old build the new
    /// one no longer has.
    static func plan(_ key: BCDContentKey, title: String, build: BCDGOGBuild, preferred: [String], containers: Bool,
                     redistributables: Bool = false, hasMsiexec: Bool = false, ownedDLC: Set<String> = [], current: Current? = nil,
                     fetch: Fetch) async throws -> (plan: BCDContentPlan, info: BCDGOGPlanInfo) {
        let manifest = try parseBuildManifest(await fetch(build.link, false))
        let products: Set<String> = Set([key.id]).union(ownedDLC.filter { $0 != key.id && BCDGOGCatalog.validID($0) })
        // The game's own depots decide the language (a DLC with another language list must not change it).
        let offered = manifest.depots.filter { $0.productID == key.id }.flatMap(\.languages)
        var seen = Set<String>()
        let offers = offered.filter { seen.insert($0).inserted }
        var language = Self.language(offered: offers, preferred: preferred)
        if let current, offers.contains(where: { $0.caseInsensitiveCompare(current.language) == .orderedSame }) { language = current.language }
        let chosen = depots(manifest, products: products, language: language)
        guard chosen.contains(where: { $0.productID == key.id }) else { throw BCDGOGError.noWindowsBuild }
        let folder = BCDContentPaths.folderName(manifest.installDirectory) ?? BCDContentPaths.folderName(title) ?? "GOG \(key.id)"
        var builder = try BCDContentPlanBuilder(key: key, title: title, version: build.id, folder: folder)

        // An update or a repair: which files are right already.
        var isCurrent: ((BCDGOGFileFacts) -> Bool)?
        var old: [String: String?]?
        if let current {
            if current.kind == .update, !current.oldDepots.isEmpty {
                do {
                    var map: [String: String?] = [:]
                    for hash in current.oldDepots {
                        try Task.checkCancellation()
                        map.merge(BCDGOGUpdate.identities(ofDepot: try await fetch(depotURL(hash, near: build.link), false))) { _, new in new }
                    }
                    old = map
                } catch {
                    if error is CancellationError || Task.isCancelled { throw CancellationError() }
                    BCDContentLog.write("[gog-update] the installed build's file lists were not read reason=\(reasonText(error)); every file is checked")
                }
            }
            let meter = Meter(total: chosen.reduce(0) { $0 + $1.size })
            let oldMap = old
            isCurrent = { facts in
                guard let url = BCDGOGUpdate.existing(facts.path, in: current.folder) else { return false }
                if let known = oldMap?[BCDContentPaths.fold(facts.path)], let before = known, let after = facts.identity {
                    var info = stat()
                    return before == after && lstat(url.path, &info) == 0 && Int64(info.st_size) == facts.size   // the same in both builds: the disk is not read
                }
                return BCDGOGUpdate.matches(url, facts) { current.report(meter.add($0), meter.total) }
            }
        }

        var total = DepotResult()
        for depot in chosen {
            try Task.checkCancellation()
            let object = try await fetch(depotURL(depot.manifest, near: build.link), false)
            let added = try add(depot: object, product: depot.productID, to: &builder, containers: containers, isCurrent: isCurrent)
            total.files += added.files
            total.support += added.support
            total.links += added.links
            total.containerFiles += added.containerFiles
            total.kept += added.kept
            total.seen += added.seen
            total.current += added.current
            total.currentBytes += added.currentBytes
        }
        let supportKept = addSupportFiles(total.kept, to: &builder, isCurrent: isCurrent)
        total.support += supportKept.skipped
        var setup: GOGSetup.Plan?
        if redistributables, !manifest.dependencies.isEmpty {
            setup = try await addRedistributables(manifest.dependencies, hasMsiexec: hasMsiexec, to: &builder, isCurrent: isCurrent, fetch: fetch)
        }
        let plan = try builder.build()
        var info = BCDGOGPlanInfo(productID: key.id, title: title, buildID: build.id, version: build.version, language: language,
                                  depots: chosen.map(\.manifest), dependencies: manifest.dependencies,
                                  supportFiles: total.support, links: total.links, containers: total.containerFiles > 0)
        if redistributables {
            info.setup = setup?.run ?? []
            info.setupNotes = setup?.notes ?? []
        }
        info.kind = current?.kind ?? .install
        info.dlc = Array(Set(chosen.map(\.productID)).subtracting([key.id])).sorted()
        info.installBytes = plan.totalBytes + total.currentBytes
        if let old, current?.kind == .update {
            let now = Set(total.seen)
            info.removed = old.keys.filter { !now.contains($0) }.sorted().prefix(20_000).map { $0 }
        }
        BCDContentLog.write("[gog-content] plan product=\(key.id) kind=\(info.kind?.rawValue ?? "install") depots=\(chosen.count) dlc=\(info.dlc?.count ?? 0) " +
                            "files=\(plan.files.count) current=\(total.current) removed=\(info.removed?.count ?? 0) " +
                            "support-skipped=\(total.support) links-skipped=\(total.links) container-files=\(total.containerFiles) " +
                            "redist=\(manifest.dependencies.count) script-files=\(supportKept.scripts) support-app-files=\(supportKept.app) " +
                            "redist-run=\(setup?.run.count ?? 0) redist-left-out=\(setup?.notes.count ?? 0)")
        return (plan, info)
    }

    /// Bytes checked so far, summed across the calls of one plan (read on the planning thread only).
    private final class Meter: @unchecked Sendable {
        let total: Int64
        private var done: Int64 = 0
        init(total: Int64) { self.total = total }
        func add(_ bytes: Int64) -> Int64 { done += bytes; return done }
    }

    private static func reasonText(_ error: Error) -> String {
        if let error = error as? BCDLogReasonError { return error.logReason }
        if let error = error as? URLError { return "url-\(error.code.rawValue)" }
        return String(describing: type(of: error))
    }

    /// The build's redistributables into the plan. `GOGSetup.plan` says which are downloaded and which installers run; each
    /// downloaded one's file list is read from GOG's CDN and added (group "redist"). A redistributable whose list cannot be
    /// read or planned is left out with a note; a repository that cannot be read leaves all out. Only a cancel is thrown.
    static func addRedistributables(_ ids: [String], hasMsiexec: Bool, to builder: inout BCDContentPlanBuilder,
                                    isCurrent: ((BCDGOGFileFacts) -> Bool)? = nil, fetch: Fetch) async throws -> GOGSetup.Plan {
        func cancelled(_ error: Error) -> Bool { error is CancellationError || (error as? URLError)?.code == .cancelled || Task.isCancelled }
        let wanted: GOGSetup.Plan
        do {
            let pointer = try await fetch(repositoryURL, false)
            guard let text = pointer["repository_manifest"] as? String, let url = URL(string: text), BCDGOGAPI.trusted(url) else {
                throw BCDGOGError.badReply("repository")
            }
            wanted = GOGSetup.plan(ids, repository: GOGDependency.parse(try await fetch(url, false)), hasMsiexec: hasMsiexec)
        } catch {
            if cancelled(error) { throw CancellationError() }
            BCDContentLog.write("[gog-setup] repository not read reason=\(reasonText(error))")
            return GOGSetup.Plan(notes: [GOGSetupNote(id: "", name: "Redistributables", note: "Could not be read from GOG this time.")])
        }
        var result = GOGSetup.Plan(download: [], run: [], notes: wanted.notes)
        var failed = Set<String>()
        var files = 0
        for dependency in wanted.download {
            try Task.checkCancellation()
            var trial = builder
            do {
                guard let url = dependencyDepotURL(dependency.manifest) else { throw BCDGOGError.badReply("dependency-hash") }
                let added = try add(depot: try await fetch(url, false), product: redistGroup, to: &trial, containers: false, isCurrent: isCurrent)
                guard added.files + added.current > 0 else { throw BCDGOGError.badReply("dependency-empty") }
                if wanted.run.contains(where: { $0.id == dependency.id }), !trial.hasFile(dependency.executable),
                   !added.seen.contains(BCDContentPaths.fold(dependency.executable)) {
                    throw BCDGOGError.badReply("dependency-installer")
                }
                builder = trial
                files += added.files
            } catch {
                if cancelled(error) { throw CancellationError() }
                failed.insert(dependency.id)
                result.notes.append(GOGSetupNote(id: dependency.id, name: dependency.name, note: "Could not be read from GOG this time."))
                BCDContentLog.write("[gog-setup] a redistributable was not planned reason=\(reasonText(error))")
            }
        }
        result.run = wanted.run.filter { !failed.contains($0.id) }
        BCDContentLog.write("[gog-setup] plan asked=\(ids.count) files=\(files) run=\(result.run.count) left-out=\(result.notes.count)")
        return result
    }

    /// The kept support files into the plan, after every depot's own files: a file of the game's own at the same path (letter
    /// case aside) stays (supportData does not overwrite), and one that cannot be added leaves the plan as it was.
    static func addSupportFiles(_ kept: [SupportFile], to builder: inout BCDContentPlanBuilder,
                                isCurrent: ((BCDGOGFileFacts) -> Bool)? = nil) -> (scripts: Int, app: Int, skipped: Int) {
        var scripts = 0, app = 0, skipped = 0
        for file in kept {
            guard !builder.hasFile(file.path) else { skipped += 1; continue }
            // An update or a repair: the game has this one right already (it counts as added: it is part of the game).
            if let isCurrent, isCurrent(BCDGOGFileFacts(path: file.path, size: file.size, digest: file.digest, chunks: file.chunks, identity: nil)) {
                if file.isScript { scripts += 1 } else { app += 1 }
                continue
            }
            var trial = builder
            do {
                let index = try trial.addFile(file.path, size: file.size, digest: file.digest)
                var offset: Int64 = 0
                for c in file.chunks {
                    try trial.addPart(chunk: trial.addChunk(c), chunkOffset: 0, length: c.size, file: index, fileOffset: offset)
                    offset += c.size
                }
                builder = trial
                if file.isScript { scripts += 1 } else { app += 1 }
            } catch {
                skipped += 1
            }
        }
        return (scripts, app, skipped)
    }

    // MARK: Secure links

    struct Endpoint: Equatable {
        var format: String
        var parameters: [String: String]
        var priority: Int
        var fallbackOnly: Bool
    }

    /// A product's secure link: its endpoints, preferred first (priority, fallback-only last), and
    /// when the link lapses.
    ///
    /// `open`: GOG's open link, which serves the redistributables without a sign-in; an endpoint of it may have no parameters.
    static func parseSecureLink(_ object: [String: Any], now: Date, open: Bool = false) throws -> (endpoints: [Endpoint], expires: Date) {
        guard let urls = object["urls"] as? [[String: Any]] else { throw BCDGOGError.badReply("secure-link") }
        var endpoints: [Endpoint] = []
        var expires = now.addingTimeInterval(3600)
        for item in urls {
            guard let format = item["url_format"] as? String, let raw = (item["parameters"] as? [String: Any]) ?? (open ? [:] : nil) else { continue }
            if let generations = item["supports_generation"] as? [NSNumber], !generations.contains(2) { continue }
            var parameters: [String: String] = [:]
            for (key, value) in raw {
                if let s = value as? String { parameters[key] = s } else if let n = value as? NSNumber { parameters[key] = n.stringValue }
            }
            if let at = parameters["expires_at"].flatMap(Double.init) { expires = min(expires, Date(timeIntervalSince1970: at)) }
            endpoints.append(Endpoint(format: format, parameters: parameters, priority: (item["priority"] as? NSNumber)?.intValue ?? 0,
                                      fallbackOnly: item["fallback_only"] as? Bool ?? false))
        }
        guard !endpoints.isEmpty else { throw BCDGOGError.badReply("secure-link") }
        let ordered = endpoints.enumerated().sorted { a, b in
            if a.element.fallbackOnly != b.element.fallbackOnly { return !a.element.fallbackOnly }
            if a.element.priority != b.element.priority { return a.element.priority > b.element.priority }
            return a.offset < b.offset
        }.map(\.element)
        return (ordered, expires)
    }

    /// A chunk's address on one endpoint: its `path` parameter gains /<c0c1>/<c2c3>/<compressed MD5>,
    /// then every {parameter} of the format is filled. https only.
    static func chunkURL(_ endpoint: Endpoint, locator: String) -> URL? {
        guard validHash(locator) else { return nil }
        var parameters = endpoint.parameters
        parameters["path"] = (parameters["path"] ?? "") + "/\(locator.prefix(2))/\(locator.dropFirst(2).prefix(2))/\(locator)"
        // A format without {path} (the open link's) is the store's address and the chunk's path follows it.
        var text = endpoint.format.contains("{path}") ? endpoint.format : endpoint.format + "{path}"
        for (key, value) in parameters { text = text.replacingOccurrences(of: "{\(key)}", with: value) }
        guard !text.contains("{"), let url = URL(string: text), url.scheme == "https", url.host != nil else { return nil }
        return url
    }
}

/// GOG's chunks for one job: a secure link per product (a DLC's chunks under its own), fetched
/// when first needed and again when under two minutes are left or a host refuses it.
actor BCDGOGChunkSource: BCDChunkSource {
    private let fetch: @Sendable (String) async throws -> [String: Any]
    private let now: @Sendable () -> Date
    private var links: [String: (endpoints: [BCDGOGContent.Endpoint], expires: Date)] = [:]

    /// `fetch` reads a product's secure link (signed in).
    init(fetch: @escaping @Sendable (String) async throws -> [String: Any], now: @escaping @Sendable () -> Date = { Date() }) {
        self.fetch = fetch
        self.now = now
    }

    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        try await link(chunk.group).compactMap { BCDGOGContent.chunkURL($0, locator: chunk.locator) }
    }

    func refresh(group: String) async throws {
        links[group] = nil
        _ = try await link(group)
    }

    private func link(_ product: String) async throws -> [BCDGOGContent.Endpoint] {
        if let held = links[product], held.expires.timeIntervalSince(now()) > 120 { return held.endpoints }
        let made = try BCDGOGContent.parseSecureLink(await fetch(product), now: now(), open: product == BCDGOGContent.redistGroup)
        links[product] = made
        BCDContentLog.write("[gog-content] link product=\(product) endpoints=\(made.endpoints.count)")
        return made.endpoints
    }
}
