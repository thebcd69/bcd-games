// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

struct BCDBnetBuild: Codable {
    var product: String
    var region: String
    var version: String
    var build: String
    var cdn: String
    var path: String
    var hosts: [String]
    var locale: String
    var buildConfig: Data
    var cdnConfig: Data
    var payloads: [BCDBnetNGDP.Payload]
    var install: [BCDBnetNGDP.Install]

    static func host(_ value: String) -> Bool {
        value == "blizzard.com" || value.hasSuffix(".blizzard.com")
    }
    func urls(_ kind: String, key: String, suffix: String = "") throws -> [URL] {
        guard ["config", "data"].contains(kind), BCDBnetNGDP.key(key), suffix == "" || suffix == ".index",
              !hosts.isEmpty, hosts.count <= 16, path == "tpr/wow" else { throw BCDBattleNetError.format("cdn-url") }
        return try hosts.map { host in
            guard Self.host(host), !host.contains(where: { "/:@?#".contains($0) }),
                  let url = URL(string: "https://" + host + "/" + path + "/" + kind + "/" + key.prefix(2) + "/" + key.dropFirst(2).prefix(2) + "/" + key + suffix) else {
                throw BCDBattleNetError.format("cdn-host")
            }
            return url
        }
    }
}

enum BCDBnetDownload {
    static func get(_ urls: [URL], limit: Int) async throws -> Data {
        let http = BCDHTTPStream(redirectAllowed: { url in
            url.scheme == "https" && url.user == nil && url.password == nil && BCDBnetBuild.host(url.host ?? "")
        })
        defer { http.close() }
        var last: Error = BCDBattleNetError.http(0)
        for url in urls {
            try Task.checkCancellation()
            do { return try await http.data(URLRequest(url: url), limit: limit) }
            catch is CancellationError { throw CancellationError() }
            catch { last = error }
        }
        throw last
    }
    static func metadata(_ product: String, region: String) async throws -> BCDBnetBuild {
        guard BCDBnetNGDP.products.contains(product), BCDBnetNGDP.regions.contains(region) else { throw BCDBattleNetError.format("product") }
        let base = "https://" + region + ".version.battle.net/v2/products/" + product + "/"
        // The version service is a distinct trusted host, and never receives account cookies.
        let http = BCDHTTPStream(redirectAllowed: { _ in false }); defer { http.close() }
        let versions = try BCDBnetNGDP.table(await http.data(URLRequest(url: URL(string: base + "versions")!), limit: 1 << 20))
        let cdns = try BCDBnetNGDP.table(await http.data(URLRequest(url: URL(string: base + "cdns")!), limit: 1 << 20))
        guard let version = versions.first(where: { $0["Region"] == region }),
              let cdn = cdns.first(where: { $0["Name"] == region }), let build = version["BuildConfig"],
              let cdnKey = version["CDNConfig"], BCDBnetNGDP.key(build), BCDBnetNGDP.key(cdnKey),
              let name = version["VersionsName"], name.count < 128, name.allSatisfy({ $0.isNumber || $0 == "." }),
              cdn["Path"] == "tpr/wow" else { throw BCDBattleNetError.format("version") }
        var hosts = (cdn["Hosts"] ?? "").split(separator: " ").map(String.init).filter(BCDBnetBuild.host)
        for server in (cdn["Servers"] ?? "").split(separator: " ") {
            if let url = URL(string: String(server)), url.scheme == "https", let host = url.host, BCDBnetBuild.host(host), !hosts.contains(host) { hosts.append(host) }
        }
        guard !hosts.isEmpty, hosts.count <= 16 else { throw BCDBattleNetError.format("cdn-hosts") }
        var made = BCDBnetBuild(product: product, region: region, version: name, build: build, cdn: cdnKey,
                               path: "tpr/wow", hosts: hosts, locale: "enUS", buildConfig: Data(), cdnConfig: Data(), payloads: [], install: [])
        made.buildConfig = try await get(made.urls("config", key: build), limit: 2 << 20)
        made.cdnConfig = try await get(made.urls("config", key: cdnKey), limit: 2 << 20)
        _ = try BCDBnetNGDP.config(made.buildConfig, key: build)
        _ = try BCDBnetNGDP.config(made.cdnConfig, key: cdnKey)
        return made
    }
    static func prepare(_ product: String, region: String) async throws -> BCDBnetBuild {
        var build = try await metadata(product, region: region)
        let config = try BCDBnetNGDP.config(build.buildConfig, key: build.build)
        let cdn = try BCDBnetNGDP.config(build.cdnConfig, key: build.cdn)
        var bootstrap: [BCDBnetNGDP.Payload] = []
        func manifest(_ name: String, limit: Int) async throws -> Data {
            guard let pair = config[name], pair.count == 2, pair.allSatisfy(BCDBnetNGDP.key),
                  let sizes = config[name + "-size"], sizes.count == 2,
                  let encoded = Int64(sizes[1]), encoded > 0, encoded <= limit,
                  let decoded = Int(sizes[0]), decoded > 0, decoded <= limit else { throw BCDBattleNetError.format("manifest-config") }
            let raw = try await get(build.urls("data", key: pair[1]), limit: Int(encoded))
            guard Int64(raw.count) == encoded else { throw BCDContentError.checkFailed }
            let data = try BCDBnetBLTE.decode(raw, key: pair[1], limit: decoded)
            guard data.count == decoded, BCDBnetNGDP.md5(data) == pair[0] else { throw BCDContentError.checkFailed }
            bootstrap.append(.init(key: pair[1], size: encoded))
            return data
        }
        let encoding = try await manifest("encoding", limit: 256 << 20)
        build.install = try BCDBnetNGDP.install(await manifest("install", limit: 32 << 20), locale: build.locale)
        let download = try BCDBnetNGDP.download(await manifest("download", limit: 128 << 20), locale: build.locale)
        let wantedContent = Set(build.install.map(\.content) + Array((config["root"] ?? []).prefix(1)))
        let mapping = try BCDBnetNGDP.encoding(encoding, wanted: wantedContent)
        for i in build.install.indices { build.install[i].encoded = mapping[build.install[i].content]! }
        var payloads: [String: BCDBnetNGDP.Payload] = [:]
        for payload in download + bootstrap { payloads[payload.key] = payload }
        // These bootstrap and loose-client files are required even when the DL tags omit them.
        for key in mapping.values where payloads[key] == nil { payloads[key] = .init(key: key, size: 0) }
        let wanted = Set(payloads.keys)
        let archives = cdn["archives"] ?? []
        guard !archives.isEmpty, archives.count <= 10000, archives.allSatisfy(BCDBnetNGDP.key) else { throw BCDBattleNetError.format("archives") }
        // Only four small indexes in memory at once. The map keeps selected payloads only.
        for start in stride(from: 0, to: archives.count, by: 4) {
            try Task.checkCancellation()
            let batch = Array(archives[start..<min(start + 4, archives.count)])
            let locations = try await withThrowingTaskGroup(of: [String: BCDBnetNGDP.Payload].self) { group in
                for archive in batch {
                    let addresses = try build.urls("data", key: archive, suffix: ".index")
                    group.addTask { try BCDBnetNGDP.archive(await get(addresses, limit: 16 << 20), archive: archive, wanted: wanted) }
                }
                var result: [String: BCDBnetNGDP.Payload] = [:]
                for try await map in group { result.merge(map) { first, _ in first } }
                return result
            }
            for (key, location) in locations {
                guard let old = payloads[key], old.size == 0 || old.size == location.size else { throw BCDContentError.checkFailed }
                payloads[key] = location
            }
        }
        // Files not archived yet are listed in the CDN's loose-file index.
        if let index = cdn["file-index"]?.first {
            let raw = try await get(build.urls("data", key: index, suffix: ".index"), limit: 16 << 20)
            let loose = try BCDBnetNGDP.loose(raw, wanted: wanted)
            for (key, size) in loose where payloads[key]?.size == 0 { payloads[key]?.size = size }
        }
        guard payloads.values.allSatisfy({ $0.size >= 8 && $0.size < (1 << 30) - 30 }) else { throw BCDBattleNetError.format("missing-payload-size") }
        build.payloads = payloads.values.sorted { $0.key < $1.key }
        return build
    }
    static func plan(_ build: BCDBnetBuild) throws -> BCDContentPlan {
        guard BCDBnetNGDP.products.contains(build.product), !build.payloads.isEmpty else {
            throw BCDBattleNetError.format("product")
        }
        var archive = -1, position: Int64 = 1 << 30
        for payload in build.payloads {
            guard payload.size >= 8, payload.size < (1 << 30) - 30 else { throw BCDBattleNetError.format("payload-size") }
            if position + payload.size + 30 >= 1 << 30 { archive += 1; position = 0 }
            guard archive < 256 else { throw BCDBattleNetError.unsupported("install over 256 GiB") }
            position += payload.size + 30
        }
        var builder = try BCDContentPlanBuilder(key: .init(store: "battlenet", id: build.product),
                title: BCDBnetNGDP.title(build.product), version: build.build, folder: build.product)
        for payload in build.payloads {
            let path = "payload/" + payload.key.prefix(2) + "/" + payload.key
            let file = try builder.addFile(path, size: payload.size, digest: nil)
            let group = payload.archive.map { $0 + ":" + String(payload.offset ?? 0) } ?? "loose"
            let c = try builder.addChunk(.init(locator: payload.key, group: group, fetchSize: payload.size,
                                               size: payload.size, fetchDigest: nil, digest: nil, codec: .stored))
            try builder.addPart(chunk: c, chunkOffset: 0, length: payload.size, file: file, fileOffset: 0)
        }
        return try builder.build()
    }
}

final class BCDBnetChunkSource: BCDChunkSource, @unchecked Sendable {
    let build: BCDBnetBuild
    init(_ build: BCDBnetBuild) { self.build = build }
    func urls(for chunk: BCDContentChunk) async throws -> [URL] {
        guard chunk.group == "loose" || chunk.group.split(separator: ":").count == 2 else { throw BCDBattleNetError.format("range") }
        let key = chunk.group == "loose" ? chunk.locator : String(chunk.group.split(separator: ":").first!)
        return try build.urls("data", key: key)
    }
    func refresh(group: String) async throws { try Task.checkCancellation() }
    func fetch(_ chunk: BCDContentChunk, url: URL, into consumer: BCDHTTPConsumer, stream: BCDHTTPStream) async throws {
        let verifier = BCDBnetVerifiedConsumer(target: consumer, key: chunk.locator, size: chunk.fetchSize)
        var range: Range<Int64>?
        if chunk.group != "loose" {
            let pair = chunk.group.split(separator: ":")
            guard pair.count == 2, BCDBnetNGDP.key(String(pair[0])), let start = Int64(pair[1]), start >= 0,
                  start <= Int64.max - chunk.fetchSize else { throw BCDBattleNetError.format("range") }
            range = start..<start + chunk.fetchSize
        }
        try await stream.fetch(URLRequest(url: url), into: verifier, range: range)
        try verifier.finish()
    }
}
