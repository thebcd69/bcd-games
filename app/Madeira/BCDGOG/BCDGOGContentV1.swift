// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2025-2026 Paweł Lidwin (imLinguin): GOG's language names and codes (`v1Languages`), from heroic-gogdl's gogdl/languages.py (https://github.com/Heroic-Games-Launcher/heroic-gogdl, commit 6c6ef53f); GPL-3.0, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// GOG's older content system, generation 1 (build 663; docs/GOG.md "Generation 1"), for a game whose builds list has no
// public generation-2 build (Return to Castle Wolfenstein, the Quake games: builds GOG made before Galaxy 2.0 and never
// made again). The builds list is the same one generation 2 reads; a generation-1 build's link is its repository
// (repository.json: JSON, sometimes zlib), which names the build's folder (`timestamp`), the depots by GOG's language names
// ("English", "Neutral") and products, and the redistributables. Each depot manifest in that folder lists the files with
// their size, MD5 and offset in ONE uncompressed file per product, main.bin, which the product's generation-1 secure link
// serves; a file's bytes are fetched with an HTTP Range. The plan is the shared engine's: the slices are stored chunks
// (BCDContentChunk.rangeStart), so the install folder, resume, progress, pause, cancel and every error text are those of
// generation 2. The formats follow GOG's own public replies and heroic-gogdl's v1 manager (GPL-3.0); only the language table
// is gogdl's (see the header). Generation 1 has no 32/64-bit depots (no repository field says one); a depot for another
// system than Windows is left out. Foundation only (the host checks compile it).

/// A generation-1 build's repository: the parts BCD uses.
struct BCDGOGRepositoryV1: Equatable {
    struct Depot: Equatable {
        /// The depot manifest's file name in the build's folder ("<uuid>.json").
        var manifest: String
        /// The products the depot is for (the game, a DLC); the manifest lies in the first one's folder.
        var products: [String]
        /// GOG's language names as the repository writes them ("English", "Neutral").
        var languages: [String]
        /// The depot's files' size on the disk (0 when the repository states none): a repair's progress.
        var size: Int64
    }
    var rootProductID: String
    /// The build's folder in the content system (its link's path names it too).
    var timestamp: String
    var installDirectory: String
    var depots: [Depot]
    /// The redistributables the build names (`redist`): GOG's dependency IDs ("DirectX", "nGlide"), read from GOG's dependency
    /// repository as generation 2's are.
    var dependencies: [String]
    /// The products the build holds (the game and its DLC).
    var products: [String]
}

extension BCDGOGContent {
    /// The longest slice of main.bin one chunk is: a bigger file is cut into slices of this size (a dropped stream or a pause loses
    /// at most one slice per stream), checked together by the file's MD5 once its slices are in.
    static let v1Piece: Int64 = 16 << 20

    /// The MD5 of no bytes: an empty file's digest, whatever GOG lists for it.
    static let v1EmptyMD5 = "d41d8cd98f00b204e9800998ecf8427e"

    // MARK: Generation 1: the build

    private static func v1Digits(_ text: String) -> Bool {
        (1...20).contains(text.utf8.count) && text.utf8.allSatisfy { $0 >= 48 && $0 <= 57 }
    }

    /// A generation-1 repository link on a CDN outside GOG's own hosts, read from cdn.gog.com instead (nil when it is not a
    /// repository link). Like generation 2's manifests (gogHostLink), the list names the CDN it picks for the player: on
    /// 2026-11-15 the list of Return to Castle Wolfenstein named gog-cdn.gcdn.co, and cdn.gog.com and gog-cdn-fastly.gog.com
    /// served the same bytes under the same path. Only a link of exactly the form
    /// /content-system/v1/manifests/<product>/windows/<timestamp>/repository.json moves.
    static func gogHostLinkV1(_ link: URL) -> URL? {
        guard link.scheme == "https", link.user == nil, link.query == nil, link.fragment == nil else { return nil }
        let parts = link.path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard parts.count == 8, parts[0].isEmpty, parts[1] == "content-system", parts[2] == "v1", parts[3] == "manifests",
              BCDGOGCatalog.validID(parts[4]), parts[5] == "windows", v1Digits(parts[6]), parts[7] == "repository.json" else { return nil }
        return URL(string: "https://cdn.gog.com/content-system/v1/manifests/\(parts[4])/windows/\(parts[6])/repository.json")
    }

    /// The newest public generation-1 build (the list is newest first): what newest() reads when newestBuild found no public
    /// generation-2 build but a generation-1 one.
    static func newestBuildV1(_ object: [String: Any]) throws -> BCDGOGBuild {
        let items = object["items"] as? [[String: Any]] ?? []
        for item in items {
            guard item["branch"] == nil || item["branch"] is NSNull else { continue }
            guard (item["generation"] as? NSNumber)?.intValue == 1 else { continue }
            let id = (item["build_id"] as? String) ?? (item["build_id"] as? NSNumber)?.stringValue ?? ""
            let given = (item["link"] as? String).flatMap(URL.init(string:))
            let moved = given.flatMap { BCDGOGAPI.trusted($0) ? nil : gogHostLinkV1($0) }
            guard BCDGOGCatalog.validID(id), let given, let link = BCDGOGAPI.trusted(given) ? given : moved else {
                // As for generation 2: the kind of host is named (GOG's to say), never the address.
                let target: String
                if let text = item["link"] as? String, let url = URL(string: text) {
                    target = BCDGOGAPI.trusted(url) ? "gog" : (url.scheme == "https" ? "other-host" : "not-secure")
                } else {
                    target = "none"
                }
                let idState = BCDGOGCatalog.validID(id) ? "ok" : "bad"
                BCDContentLog.write("[gog-content] generation-1 build entry: id=\(idState) target=\(target) " + BCDGOGAPI.shape(item, lists: []))
                throw BCDGOGError.badReply("build")
            }
            if moved != nil {
                BCDContentLog.write("[gog-content] generation-1 repository read from cdn.gog.com (the list named a CDN outside gog.com)")
            }
            BCDContentLog.write("[gog-content] generation-1 build: no public generation-2 build, the older content system is read")
            let version = ((item["version_name"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return BCDGOGBuild(id: id, version: String(version.prefix(80)), link: link, generation: 1)
        }
        throw BCDGOGError.olderBuildsOnly   // newestBuild saw one: not reached
    }

    /// A depot manifest's name in a repository: "<letters, digits, dashes>.json" (GOG writes "<uuid>.json"), so it can only name a
    /// file in the build's own folder.
    static func validManifestNameV1(_ name: String) -> Bool {
        guard name.hasSuffix(".json") else { return false }
        let stem = name.utf8.dropLast(5)
        return (1...64).contains(stem.count) && stem.allSatisfy { ($0 >= 48 && $0 <= 57) || ($0 >= 65 && $0 <= 90) || ($0 >= 97 && $0 <= 122) || $0 == 45 }
    }

    /// A count or an offset in a generation-1 reply: a whole number, or a decimal string (the repository writes sizes as "1274800").
    static func v1Number(_ value: Any?) -> Int64? {
        if let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() {
            let whole = number.int64Value
            return number.doubleValue == Double(whole) ? whole : nil
        }
        if let text = value as? String, (1...19).contains(text.utf8.count), text.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }) { return Int64(text) }
        return nil
    }

    /// A build's repository: strict on what decides which files come from where (the build's folder, each depot's manifest name
    /// and products), lenient on the rest. A redistributable entry with no usable name is left out, as generation 2's are.
    static func parseRepositoryV1(_ object: [String: Any]) throws -> BCDGOGRepositoryV1 {
        func text(_ value: Any?) -> String? { (value as? String) ?? (value as? NSNumber)?.stringValue }
        guard (object["version"] as? NSNumber)?.intValue == 1, let product = object["product"] as? [String: Any],
              let root = text(product["rootGameID"]), BCDGOGCatalog.validID(root),
              let timestamp = text(product["timestamp"]), v1Digits(timestamp),
              let depots = product["depots"] as? [[String: Any]] else {
            let inner = (object["product"] as? [String: Any]).map { BCDGOGAPI.shape($0, lists: ["depots"]) } ?? "none"
            BCDContentLog.write("[gog-content] generation-1 repository: " + BCDGOGAPI.shape(object, lists: []) + " product: " + inner)
            throw BCDGOGError.badReply("v1-repository")
        }
        var list: [BCDGOGRepositoryV1.Depot] = []
        var dependencies: [String] = []
        for depot in depots {
            if depot["redist"] != nil {
                if let id = depot["redist"] as? String, (1...64).contains(id.count) { dependencies.append(id) }
                continue
            }
            let ids = (depot["gameIDs"] as? [Any] ?? []).map { text($0) ?? "" }
            let languages = (depot["languages"] as? [Any] ?? []).map { ($0 as? String ?? "").trimmingCharacters(in: .whitespaces) }
            guard let manifest = depot["manifest"] as? String, validManifestNameV1(manifest), !ids.isEmpty, ids.allSatisfy(BCDGOGCatalog.validID),
                  !languages.isEmpty, languages.allSatisfy({ (1...64).contains($0.count) }) else {
                BCDContentLog.write("[gog-content] generation-1 repository depot: " + BCDGOGAPI.shape(depot, lists: []))
                throw BCDGOGError.badReply("v1-depot")
            }
            if let systems = depot["systems"] as? [String], !systems.contains(where: { $0.caseInsensitiveCompare("Windows") == .orderedSame }) {
                continue   // another system's depot
            }
            list.append(.init(manifest: manifest, products: ids, languages: languages, size: max(0, v1Number(depot["size"]) ?? 0)))
        }
        var products = (product["gameIDs"] as? [[String: Any]] ?? []).compactMap { text($0["gameID"]) }.filter(BCDGOGCatalog.validID)
        if !products.contains(root) { products.insert(root, at: 0) }
        var folder = (product["installDirectory"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        while folder.hasSuffix("/") || folder.hasSuffix("\\") { folder.removeLast() }
        while folder.hasPrefix("/") || folder.hasPrefix("\\") { folder.removeFirst() }
        return BCDGOGRepositoryV1(rootProductID: root, timestamp: timestamp, installDirectory: folder, depots: list,
                                  dependencies: dependencies, products: products)
    }

    // MARK: Generation 1: languages and depots

    /// GOG's language names and codes, taken from heroic-gogdl's gogdl/languages.py (its list without the native names): a
    /// generation-1 depot names its language by the English name ("English", "German"), sometimes by a code. The code is what the
    /// rest of BCD's GOG speaks (BCDGOGContent.language, the record).
    static let v1Languages: [(code: String, name: String, also: [String])] = [
        ("af-ZA", "Afrikaans", []), ("ar", "Arabic", []), ("az-AZ", "Azeri", []), ("be-BY", "Belarusian", ["be"]),
        ("bn-BD", "Bengali", ["bn_BD"]), ("bg-BG", "Bulgarian", ["bg", "bl"]), ("bs-BA", "Bosnian", []), ("ca-ES", "Catalan", ["ca"]),
        ("cs-CZ", "Czech", ["cz"]), ("cy-GB", "Welsh", []), ("da-DK", "Danish", ["da"]), ("de-DE", "German", ["de"]),
        ("dv-MV", "Divehi", []), ("el-GR", "Greek", ["gk", "el-GK"]), ("en-GB", "British English", ["en_GB"]), ("en-US", "English", ["en"]),
        ("es-ES", "Spanish", ["es"]), ("es-MX", "Latin American Spanish", ["es_mx"]), ("et-EE", "Estonian", ["et"]), ("eu-ES", "Basque", []),
        ("fa-IR", "Persian", ["fa"]), ("fi-FI", "Finnish", ["fi"]), ("fo-FO", "Faroese", []), ("fr-FR", "French", ["fr"]),
        ("gl-ES", "Galician", []), ("gu-IN", "Gujarati", ["gu"]), ("he-IL", "Hebrew", ["he"]), ("hi-IN", "Hindi", ["hi"]),
        ("hr-HR", "Croatian", []), ("hu-HU", "Hungarian", ["hu"]), ("hy-AM", "Armenian", []), ("id-ID", "Indonesian", []),
        ("is-IS", "Icelandic", ["is"]), ("it-IT", "Italian", ["it"]), ("ja-JP", "Japanese", ["jp"]), ("jv-ID", "Javanese", ["jv"]),
        ("ka-GE", "Georgian", []), ("kk-KZ", "Kazakh", []), ("kn-IN", "Kannada", []), ("ko-KR", "Korean", ["ko"]),
        ("kok-IN", "Konkani", []), ("ky-KG", "Kyrgyz", []), ("la", "Latin", []), ("lt-LT", "Lithuanian", []),
        ("lv-LV", "Latvian", []), ("ml-IN", "Malayalam", ["ml"]), ("mi-NZ", "Maori", []), ("mk-MK", "Macedonian", []),
        ("mn-MN", "Mongolian", []), ("mr-IN", "Marathi", ["mr"]), ("ms-MY", "Malay", []), ("mt-MT", "Maltese", []),
        ("nb-NO", "Norwegian", ["no"]), ("nl-NL", "Dutch", ["nl"]), ("ns-ZA", "Northern Sotho", []), ("pa-IN", "Punjabi", []),
        ("pl-PL", "Polish", ["pl"]), ("ps-AR", "Pashto", []), ("pt-BR", "Portuguese (Brazilian)", ["br"]), ("pt-PT", "Portuguese", ["pt"]),
        ("ro-RO", "Romanian", ["ro"]), ("ru-RU", "Russian", ["ru"]), ("sa-IN", "Sanskrit", []), ("sk-SK", "Slovak", ["sk"]),
        ("sl-SI", "Slovenian", []), ("sq-AL", "Albanian", []), ("sr-SP", "Serbian", ["sb"]), ("sv-SE", "Swedish", ["sv"]),
        ("sw-KE", "Kiswahili", []), ("ta-IN", "Tamil", ["ta_IN"]), ("te-IN", "Telugu", ["te"]), ("th-TH", "Thai", ["th"]),
        ("tl-PH", "Tagalog", []), ("tn-ZA", "Setswana", []), ("tr-TR", "Turkish", ["tr"]), ("tt-RU", "Tatar", []),
        ("uk-UA", "Ukrainian", ["uk"]), ("ur-PK", "Urdu", ["ur_PK"]), ("uz-UZ", "Uzbek", []), ("vi-VN", "Vietnamese", ["vi"]),
        ("xh-ZA", "isiXhosa", []), ("zh-Hans", "Chinese (Simplified)", ["zh_Hans", "zh", "cn"]), ("zh-Hant", "Chinese (Traditional)", ["zh_Hant"]),
        ("zu-ZA", "isiZulu", []),
    ]

    /// A generation-1 language as a code: "Neutral" is "*" (every language's files), a name, code or older code of the table is
    /// its code (letter case aside, as gogdl compares them), anything else is kept as GOG wrote it.
    static func v1LanguageCode(_ name: String) -> String {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        let lower = trimmed.lowercased()
        if lower == "neutral" || lower == "*" { return "*" }
        for entry in v1Languages where entry.code.lowercased() == lower || entry.name.lowercased() == lower || entry.also.contains(where: { $0.lowercased() == lower }) {
            return entry.code
        }
        return trimmed
    }

    /// The depots to install for `products`: every depot of one of them that is neutral or in the language, each manifest once
    /// (gogdl's rule for generation 1).
    static func depotsV1(_ repository: BCDGOGRepositoryV1, products: Set<String>, language: String) -> [BCDGOGRepositoryV1.Depot] {
        var seen = Set<String>()
        return repository.depots.filter { depot in
            let codes = depot.languages.map(v1LanguageCode)
            let languageOK = codes.contains("*") || codes.contains { $0.caseInsensitiveCompare(language) == .orderedSame }
            return depot.products.contains(where: products.contains) && languageOK && seen.insert(depot.manifest).inserted
        }
    }

    /// A depot manifest's address: the build's folder under its first product, on the host the build's own link uses.
    static func depotURLV1(_ depot: BCDGOGRepositoryV1.Depot, timestamp: String, near link: URL) -> URL? {
        guard let product = depot.products.first, BCDGOGCatalog.validID(product), v1Digits(timestamp), validManifestNameV1(depot.manifest) else { return nil }
        let host = BCDGOGAPI.trusted(link) ? link.host! : "cdn.gog.com"
        return URL(string: "https://\(host)/content-system/v1/manifests/\(product)/windows/\(timestamp)/\(depot.manifest)")
    }

    // MARK: Generation 1: chunks

    /// The group of a product's main.bin slices: the product and the build's folder, which its secure link names.
    static func v1Group(product: String, timestamp: String) -> String { "v1:\(product):\(timestamp)" }

    static func v1GroupParts(_ group: String) -> (product: String, timestamp: String)? {
        let parts = group.split(separator: ":", omittingEmptySubsequences: false).map(String.init)
        guard parts.count == 3, parts[0] == "v1", BCDGOGCatalog.validID(parts[1]), v1Digits(parts[2]) else { return nil }
        return (parts[1], parts[2])
    }

    /// The stored chunks of the bytes [offset, offset + size) of a product's main.bin: one chunk checked by the file's MD5 when the
    /// file fits one, else slices of v1Piece bytes without a check of their own (the engine checks the file once they are in). The
    /// locator is the slice, and the MD5 of a checked one: files GOG keeps once (the same bytes, the same MD5) share their chunk,
    /// and a checked chunk never meets an unchecked one of the same bytes.
    static func v1Chunks(product: String, timestamp: String, offset: Int64, size: Int64, digest: BCDDigest) -> [BCDContentChunk] {
        let group = v1Group(product: product, timestamp: timestamp)
        let whole = size <= v1Piece
        var chunks: [BCDContentChunk] = []
        var at: Int64 = 0
        while at < size {
            let length = min(v1Piece, size - at)
            chunks.append(BCDContentChunk(locator: "\(offset + at)+\(length)" + (whole ? ":" + digest.hex : ""), group: group,
                                          fetchSize: length, size: length, fetchDigest: nil, digest: whole ? digest : nil,
                                          codec: .stored, rangeStart: offset + at))
            at += length
        }
        return chunks
    }

    private static let v1FileKeys: Set<String> = ["path", "size", "offset", "hash", "url", "support", "executable", "directory"]

    /// The product whose main.bin a file's `url` names ("<product>/main.bin"); nil for anything else.
    static func v1MainBinProduct(_ url: String) -> String? {
        let parts = url.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard parts.count == 2, BCDGOGCatalog.validID(parts[0]), parts[1] == "main.bin" else { return nil }
        return parts[0]
    }

    /// One generation-1 depot manifest into the plan. A file is its slice of its product's main.bin (v1Chunks); files at the same
    /// bytes (GOG keeps identical files once) share their chunks; a folder entry is an empty folder to create. Support files (GOG's
    /// setup helper) are left out, except the support folder's app/ files, which generation 2 keeps too (DOSBox games' .conf files:
    /// "<product>/app/<path>" goes to <path> in the game's folder, never over a file of the game's own; addSupportFiles). A file
    /// that names another source than its product's main.bin, a field BCD does not know, a size, offset or MD5 that is not one, or a
    /// path out of the game's folder refuses the plan.
    ///
    /// `isCurrent` (an update or a repair): as for generation 2, true leaves a file out of the plan because the game has it right.
    static func addV1(depot object: [String: Any], depot: BCDGOGRepositoryV1.Depot, timestamp: String, to builder: inout BCDContentPlanBuilder,
                      isCurrent: ((BCDGOGFileFacts) -> Bool)? = nil) throws -> DepotResult {
        guard (object["version"] as? NSNumber)?.intValue ?? 1 == 1, let body = object["depot"] as? [String: Any],
              let items = body["files"] as? [[String: Any]], let owner = depot.products.first else {
            BCDContentLog.write("[gog-content] generation-1 depot manifest: " + BCDGOGAPI.shape(object, lists: []))
            throw BCDGOGError.badReply("v1-depot-manifest")
        }
        func clean(_ raw: String) -> String {
            var text = raw.replacingOccurrences(of: "\\", with: "/")
            while text.hasPrefix("/") { text.removeFirst() }
            return text
        }
        var result = DepotResult()
        for item in items {
            guard Set(item.keys).isSubset(of: v1FileKeys) else {
                BCDContentLog.write("[gog-content] generation-1 file entry of an unknown kind: " + BCDGOGAPI.shape(item, lists: []))
                throw BCDGOGError.unknownFileSource
            }
            guard let raw = item["path"] as? String else { throw BCDGOGError.badReply("v1-item") }
            if item["directory"] != nil {
                var folder = clean(raw)
                while folder.hasSuffix("/") { folder.removeLast() }
                if !folder.isEmpty { try builder.addDirectory(folder) }
                continue
            }
            guard let size = v1Number(item["size"]), let offset = v1Number(item["offset"]), size >= 0, size <= 1 << 40,
                  offset >= 0, offset <= 1 << 50 else {
                BCDContentLog.write("[gog-content] generation-1 file entry: " + BCDGOGAPI.shape(item, lists: []))
                throw BCDGOGError.badReply("v1-file")
            }
            // Every byte is checked by the file's MD5: a file without one is not planned blind.
            guard let digest = size == 0 ? BCDDigest(.md5, v1EmptyMD5) : (item["hash"] as? String).flatMap({ BCDDigest(.md5, $0) }) else {
                BCDContentLog.write("[gog-content] generation-1 file entry without an MD5: " + BCDGOGAPI.shape(item, lists: []))
                throw BCDGOGError.badReply("v1-file")
            }
            var product = owner
            if let url = item["url"] {
                guard let text = url as? String, let named = v1MainBinProduct(text), depot.products.contains(named) else {
                    BCDContentLog.write("[gog-content] generation-1 file from another source than its product's main.bin: " + BCDGOGAPI.shape(item, lists: []))
                    throw BCDGOGError.unknownFileSource
                }
                product = named
            }
            let listed = clean(raw)
            let chunks = v1Chunks(product: product, timestamp: timestamp, offset: offset, size: size, digest: digest)
            if (item["support"] as? Bool) == true {
                // "<product>/app/x" is generation 2's "app/x"; "<product>/<helper>.exe" is GOG's setup helper, left out.
                let parts = listed.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
                let below = parts.count > 1 && BCDGOGCatalog.validID(parts[0]) ? parts.dropFirst().joined(separator: "/") : listed
                if size > 0, let kept = supportKept(below) {
                    switch kept {
                    case .script(let name): result.kept.append(SupportFile(path: name, isScript: true, size: size, digest: digest, chunks: chunks))
                    case .app(let path): result.kept.append(SupportFile(path: path, isScript: false, size: size, digest: digest, chunks: chunks))
                    }
                } else {
                    result.support += 1
                }
                continue
            }
            if let isCurrent {
                result.seen.append(BCDContentPaths.fold(listed))
                result.installBytes += size
                let facts = BCDGOGFileFacts(path: listed, size: size, digest: digest, chunks: [], identity: "md5:" + digest.hex)
                if isCurrent(facts) {
                    result.current += 1
                    result.currentBytes += size
                    continue
                }
            }
            let file = try builder.addFile(listed, size: size, digest: digest)
            result.files += 1
            var at: Int64 = 0
            for chunk in chunks {
                try builder.addPart(chunk: builder.addChunk(chunk), chunkOffset: 0, length: chunk.size, file: file, fileOffset: at)
                at += chunk.size
            }
        }
        return result
    }

    // MARK: Generation 1: the plan

    /// The plan to install (update, repair) a generation-1 build: as plan() does for generation 2 (the device's language, the
    /// owned DLC's depots, the support folder's app/ files after every depot's own files, the redistributables through GOG's
    /// dependency repository), from the build's repository and its depot manifests. An update compares every file with the disk
    /// as a repair does (the old build's manifests are not read), and removes no file.
    static func planV1(_ key: BCDContentKey, title: String, build: BCDGOGBuild, preferred: [String], redistributables: Bool,
                       hasMsiexec: Bool, ownedDLC: Set<String>, current: Current?, fetch: Fetch) async throws -> (plan: BCDContentPlan, info: BCDGOGPlanInfo) {
        let repository = try parseRepositoryV1(await fetch(build.link, false))
        let products: Set<String> = Set([key.id]).union(ownedDLC.filter { $0 != key.id && BCDGOGCatalog.validID($0) })
        // The game's own depots decide the language (a DLC with another language list must not change it).
        var seen = Set<String>()
        let offers = repository.depots.filter { $0.products.contains(key.id) }.flatMap { $0.languages.map(v1LanguageCode) }.filter { seen.insert($0).inserted }
        var language = Self.language(offered: offers, preferred: preferred)
        if let current, offers.contains(where: { $0.caseInsensitiveCompare(current.language) == .orderedSame }) { language = current.language }
        let chosen = depotsV1(repository, products: products, language: language)
        guard chosen.contains(where: { $0.products.contains(key.id) }) else { throw BCDGOGError.noWindowsBuild }
        let folder = BCDContentPaths.folderName(repository.installDirectory) ?? BCDContentPaths.folderName(title) ?? "GOG \(key.id)"
        var builder = try BCDContentPlanBuilder(key: key, title: title, version: build.id, folder: folder)

        var isCurrent: ((BCDGOGFileFacts) -> Bool)?
        if let current {
            let meter = V1Meter(total: chosen.reduce(0) { $0 + $1.size })
            isCurrent = { facts in
                guard let url = BCDGOGUpdate.existing(facts.path, in: current.folder) else { return false }
                return BCDGOGUpdate.matches(url, facts) { current.report(meter.add($0), meter.total) }
            }
        }

        var total = DepotResult()
        for depot in chosen {
            try Task.checkCancellation()
            guard let url = depotURLV1(depot, timestamp: repository.timestamp, near: build.link) else { throw BCDGOGError.badReply("v1-depot") }
            let added = try addV1(depot: await fetch(url, false), depot: depot, timestamp: repository.timestamp, to: &builder, isCurrent: isCurrent)
            total.files += added.files
            total.support += added.support
            total.kept += added.kept
            total.seen += added.seen
            total.current += added.current
            total.currentBytes += added.currentBytes
        }
        // A support app/ file never goes over a file of the game's own. In an install the game's file is in the plan, which
        // addSupportFiles looks at; in an update or a repair a game file that is right already is not, so the depots' list is asked.
        let listed = Set(total.seen)
        let kept = total.kept.filter { !listed.contains(BCDContentPaths.fold($0.path)) }
        total.support += total.kept.count - kept.count
        let supportKept = addSupportFiles(kept, to: &builder, isCurrent: isCurrent)
        total.support += supportKept.skipped
        var setup: GOGSetup.Plan?
        if redistributables, !repository.dependencies.isEmpty {
            setup = try await addRedistributables(repository.dependencies, hasMsiexec: hasMsiexec, to: &builder, isCurrent: isCurrent, fetch: fetch)
        }
        let plan = try builder.build()
        var info = BCDGOGPlanInfo(productID: key.id, title: title, buildID: build.id, version: build.version, language: language,
                                  depots: chosen.map(\.manifest), dependencies: repository.dependencies,
                                  supportFiles: total.support, links: 0, containers: false)
        if redistributables {
            info.setup = setup?.run ?? []
            info.setupNotes = setup?.notes ?? []
        }
        info.kind = current?.kind ?? .install
        info.dlc = Array(Set(chosen.flatMap(\.products)).intersection(products).subtracting([key.id])).sorted()
        info.installBytes = plan.totalBytes + total.currentBytes
        let ranged = plan.chunks.filter { $0.rangeStart != nil }
        let split = Set(plan.parts.filter { plan.chunks[$0.chunk].rangeStart != nil && plan.chunks[$0.chunk].digest == nil }.map(\.file)).count
        BCDContentLog.write("[gog-content] plan product=\(key.id) generation=1 kind=\(info.kind?.rawValue ?? "install") depots=\(chosen.count) " +
                            "dlc=\(info.dlc?.count ?? 0) files=\(plan.files.count) current=\(total.current) support-skipped=\(total.support) " +
                            "support-app-files=\(supportKept.app) slices=\(ranged.count) split-files=\(split) redist=\(repository.dependencies.count) " +
                            "redist-run=\(setup?.run.count ?? 0) redist-left-out=\(setup?.notes.count ?? 0)")
        return (plan, info)
    }

    /// Owned DLC with depots in the installed generation-1 build that the game's `depots` do not hold (available(), same build).
    static func availableDLCV1(product: String, build: BCDGOGBuild, depots installed: [String], language: String, owned: Set<String>,
                               fetch: Fetch) async throws -> Available? {
        let repository = try parseRepositoryV1(await fetch(build.link, false))
        let wanted = depotsV1(repository, products: Set([product]).union(owned), language: language)
        let have = Set(installed)
        let added = Set(wanted.filter { !have.contains($0.manifest) }.flatMap { $0.products.filter { $0 != product && owned.contains($0) } })
        return added.isEmpty ? nil : Available(buildID: build.id, version: build.version, dlc: added.sorted(), newerBuild: false)
    }

    /// Bytes checked so far across the calls of one plan (read on the planning thread only).
    private final class V1Meter: @unchecked Sendable {
        let total: Int64
        private var done: Int64 = 0
        init(total: Int64) { self.total = total }
        func add(_ bytes: Int64) -> Int64 { done += bytes; return done }
    }

    // MARK: Generation 1: secure links

    /// A product's generation-1 secure link for a build's folder (signed in; gogdl's form).
    static func secureLinkURLV1(_ product: String, timestamp: String) -> URL? {
        guard BCDGOGCatalog.validID(product), v1Digits(timestamp) else { return nil }
        return URL(string: "https://content-system.gog.com/products/\(product)/secure_link?_version=2&type=depot&path=/windows/\(timestamp)/")
    }

    /// The reply is generation 2's kind (urls with url_format, parameters, priority, fallback_only); an endpoint that says which
    /// generations it serves must serve generation 1. The rest is generation 2's reading (parseSecureLink: order, expiry).
    static func parseSecureLinkV1(_ object: [String: Any], now: Date) throws -> (endpoints: [Endpoint], expires: Date) {
        guard let urls = object["urls"] as? [[String: Any]] else { throw BCDGOGError.badReply("secure-link") }
        let usable = urls.compactMap { item -> [String: Any]? in
            if let generations = item["supports_generation"] as? [NSNumber], !generations.contains(1) { return nil }
            var item = item
            item["supports_generation"] = nil
            return item
        }
        return try parseSecureLink(["urls": usable], now: now)
    }

    /// The address of a product's main.bin on one endpoint: its `path` parameter gains "/main.bin" (as gogdl asks for it), then
    /// every {parameter} of the format is filled. https only.
    static func mainBinURL(_ endpoint: Endpoint) -> URL? {
        var parameters = endpoint.parameters
        parameters["path"] = (parameters["path"] ?? "") + "/main.bin"
        var text = endpoint.format.contains("{path}") ? endpoint.format : endpoint.format + "{path}"
        for (key, value) in parameters { text = text.replacingOccurrences(of: "{\(key)}", with: value) }
        guard !text.contains("{"), let url = URL(string: text), url.scheme == "https", url.host != nil else { return nil }
        return url
    }

    /// The chunk source of a generation-1 job (BCDGOG.source): the main.bin slices through each product's generation-1 secure link,
    /// the job's redistributables (generation 2's chunks) through GOG's open link as for generation 2.
    static func v1Source(api: BCDGOGAPI) -> BCDChunkSource {
        let inner = BCDGOGChunkSource(fetch: { product in
            product == redistGroup
                ? try await api.json(openLinkURL, signedIn: false, limit: 1 << 20)
                : try await api.json(secureLinkURL(product), signedIn: true, limit: 1 << 20)
        })
        return BCDGOGV1ChunkSource(fetch: { product, timestamp in
            guard let url = secureLinkURLV1(product, timestamp: timestamp) else { throw BCDGOGError.badReply("secure-link") }
            return try await api.json(url, signedIn: true, limit: 1 << 20)
        }, inner: inner)
    }
}

/// The chunks of a generation-1 job: a product's main.bin through its generation-1 secure link, fetched when first needed and
/// again when under two minutes are left or a host refuses it (as BCDGOGChunkSource does for generation 2); every other chunk of
/// the job (its redistributables) through `inner`.
actor BCDGOGV1ChunkSource: BCDChunkSource {
    private let fetch: @Sendable (String, String) async throws -> [String: Any]
    private let inner: BCDChunkSource
    private let now: @Sendable () -> Date
    private var links: [String: (endpoints: [BCDGOGContent.Endpoint], expires: Date)] = [:]

    /// `fetch` reads a product's generation-1 secure link for a build's folder (signed in).
    init(fetch: @escaping @Sendable (String, String) async throws -> [String: Any], inner: BCDChunkSource,
         now: @escaping @Sendable () -> Date = { Date() }) {
        self.fetch = fetch
        self.inner = inner
        self.now = now
    }

    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        guard chunk.rangeStart != nil, let parts = BCDGOGContent.v1GroupParts(chunk.group) else { return try await inner.urls(for: chunk) }
        return try await link(chunk.group, parts).compactMap(BCDGOGContent.mainBinURL)
    }

    func refresh(group: String) async throws {
        guard let parts = BCDGOGContent.v1GroupParts(group) else { return try await inner.refresh(group: group) }
        links[group] = nil
        _ = try await link(group, parts)
    }

    private func link(_ group: String, _ parts: (product: String, timestamp: String)) async throws -> [BCDGOGContent.Endpoint] {
        if let held = links[group], held.expires.timeIntervalSince(now()) > 120 { return held.endpoints }
        let made = try BCDGOGContent.parseSecureLinkV1(await fetch(parts.product, parts.timestamp), now: now())
        links[group] = made
        let product = parts.product
        BCDContentLog.write("[gog-content] link generation=1 product=\(product) endpoints=\(made.endpoints.count)")
        return made.endpoints
    }
}
