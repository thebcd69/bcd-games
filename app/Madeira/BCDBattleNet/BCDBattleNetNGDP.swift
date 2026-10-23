// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import CryptoKit

/// Bounded readers for Blizzard's public NGDP formats. Unknown versions/codecs fail closed.
struct BCDBnetReader {
    let data: Data
    var offset = 0
    var remaining: Int { data.count - offset }
    mutating func bytes(_ count: Int) throws -> Data {
        guard count >= 0, count <= remaining else { throw BCDBattleNetError.format("truncated") }
        defer { offset += count }
        return data.subdata(in: offset..<offset + count)
    }
    mutating func number(_ count: Int, little: Bool = false) throws -> UInt64 {
        guard (1...8).contains(count) else { throw BCDBattleNetError.format("integer") }
        let bytes = try bytes(count)
        return (little ? Array(bytes.reversed()) : Array(bytes)).reduce(0) { ($0 << 8) | UInt64($1) }
    }
    mutating func string() throws -> String {
        guard let end = data[offset...].firstIndex(of: 0), end - offset <= 4096,
              let value = String(data: data.subdata(in: offset..<end), encoding: .utf8) else {
            throw BCDBattleNetError.format("string")
        }
        offset = end + 1
        return value
    }
}

enum BCDBnetNGDP {
    static let products = ["wow", "wow_classic", "wow_classic_era"]
    static let regions = ["us", "eu", "kr", "tw"]
    static func title(_ product: String) -> String {
        ["wow": "World of Warcraft", "wow_classic": "World of Warcraft Classic", "wow_classic_era": "World of Warcraft Classic Era"][product] ?? "Battle.net game"
    }
    static func client(_ product: String) -> String {
        product == "wow" ? "_retail_" : product == "wow_classic" ? "_classic_" : "_classic_era_"
    }
    static func program(_ product: String) -> String { client(product) + "/" + (product == "wow" ? "Wow.exe" : "WowClassic.exe") }
    static func hex(_ data: Data) -> String { data.map { String(format: "%02x", $0) }.joined() }
    static func md5(_ data: Data) -> String { hex(Data(Insecure.MD5.hash(data: data))) }
    static func key(_ value: String) -> Bool {
        value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
    static func keyBytes(_ value: String) throws -> Data {
        guard key(value) else { throw BCDBattleNetError.format("key") }
        let chars = Array(value)
        return Data(stride(from: 0, to: 32, by: 2).map { UInt8(String(chars[$0...$0 + 1]), radix: 16)! })
    }
    static func table(_ data: Data) throws -> [[String: String]] {
        guard data.count <= 1 << 20, let text = String(data: data, encoding: .utf8) else {
            throw BCDBattleNetError.format("table")
        }
        let lines = text.split(whereSeparator: \.isNewline).filter { !$0.hasPrefix("#") }
        guard let first = lines.first else { throw BCDBattleNetError.format("table") }
        let headers = first.split(separator: "|", omittingEmptySubsequences: false).map { String($0.split(separator: "!").first ?? "") }
        guard !headers.contains(""), Set(headers).count == headers.count, headers.count <= 32 else { throw BCDBattleNetError.format("table") }
        return try lines.dropFirst().map { line in
            let cells = line.split(separator: "|", omittingEmptySubsequences: false).map(String.init)
            guard cells.count == headers.count else { throw BCDBattleNetError.format("table-row") }
            return Dictionary(uniqueKeysWithValues: zip(headers, cells))
        }
    }
    static func config(_ data: Data, key: String) throws -> [String: [String]] {
        guard self.key(key), md5(data) == key, data.count <= 2 << 20,
              let text = String(data: data, encoding: .utf8) else { throw BCDContentError.checkFailed }
        var result: [String: [String]] = [:]
        for line in text.split(whereSeparator: \.isNewline) where !line.hasPrefix("#") {
            let pair = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            guard pair.count == 2, result[pair[0]] == nil else { throw BCDBattleNetError.format("config") }
            result[pair[0]] = pair[1].split(whereSeparator: \.isWhitespace).map(String.init)
        }
        return result
    }

    struct Tag { var name: String; var kind: Int; var mask: Data
        func contains(_ i: Int) -> Bool { mask[i / 8] & (0x80 >> (i % 8)) != 0 }
    }
    static func tags(_ reader: inout BCDBnetReader, count: Int, entries: Int) throws -> [Tag] {
        guard count <= 1024 else { throw BCDBattleNetError.format("tags") }
        return try (0..<count).map { _ in
            let name = try reader.string(), kind = try Int(reader.number(2))
            return Tag(name: name, kind: kind, mask: try reader.bytes((entries + 7) / 8))
        }
    }
    /// Tags of one kind are alternatives; different kinds are intersected. Untagged entries
    /// are common content. Do not silently pick a different platform or language.
    static func selected(_ i: Int, tags: [Tag], locale: String) -> Bool {
        let wanted: Set<String> = ["Windows", "Win", "x86_64", "64", locale, "US", "EU", "speech", "text"]
        let groups = Dictionary(grouping: tags, by: \.kind)
        return groups.values.allSatisfy { group in
            let present = group.filter { $0.contains(i) }
            return present.isEmpty || present.contains { wanted.contains($0.name) }
        }
    }
    struct Payload: Codable, Equatable {
        var key: String
        var size: Int64
        var archive: String? = nil
        var offset: Int64? = nil
    }
    struct Install: Codable, Equatable { var path: String; var content: String; var size: Int64; var encoded: String = "" }
    static func download(_ data: Data, locale: String) throws -> [Payload] {
        var r = BCDBnetReader(data: data)
        guard try r.bytes(2) == Data("DL".utf8) else { throw BCDBattleNetError.format("download") }
        let version = try r.number(1), keySize = try r.number(1), checksums = try r.number(1)
        let count = try Int(r.number(4)), tagCount = try Int(r.number(2))
        guard (1...3).contains(version), keySize == 16, checksums <= 1, count > 0, count <= 2_000_000 else {
            throw BCDBattleNetError.format("download-version")
        }
        let flags = version >= 2 ? try Int(r.number(1)) : 0
        guard flags <= 16 else { throw BCDBattleNetError.format("download-flags") }
        if version >= 3 { _ = try r.bytes(4) }
        var entries: [Payload] = []; entries.reserveCapacity(count)
        for _ in 0..<count {
            let key = hex(try r.bytes(16)), size = try Int64(r.number(5))
            _ = try r.bytes(1 + (checksums == 1 ? 4 : 0) + flags)
            guard size >= 8, size < (1 << 30) - 30 else { throw BCDBattleNetError.format("payload-size") }
            entries.append(Payload(key: key, size: size))
        }
        let tags = try tags(&r, count: tagCount, entries: count)
        guard r.remaining == 0 else { throw BCDBattleNetError.format("download-trailing") }
        return entries.enumerated().filter { selected($0.offset, tags: tags, locale: locale) }.map(\.element)
    }
    static func install(_ data: Data, locale: String) throws -> [Install] {
        var r = BCDBnetReader(data: data)
        guard try r.bytes(2) == Data("IN".utf8), try r.number(1) == 1, try r.number(1) == 16 else {
            throw BCDBattleNetError.format("install-version")
        }
        let tagCount = try Int(r.number(2)), count = try Int(r.number(4))
        guard count <= 100_000 else { throw BCDBattleNetError.format("install-count") }
        let tags = try tags(&r, count: tagCount, entries: count)
        var entries: [Install] = []
        for i in 0..<count {
            let path = try r.string().replacingOccurrences(of: "\\", with: "/")
            let content = hex(try r.bytes(16)), size = try Int64(r.number(4))
            if selected(i, tags: tags, locale: locale) {
                _ = try BCDContentPaths.components(path)
                guard size <= 256 << 20 else { throw BCDBattleNetError.format("install-size") }
                entries.append(Install(path: path, content: content, size: size))
            }
        }
        guard r.remaining == 0, !entries.isEmpty else { throw BCDBattleNetError.format("install") }
        return entries
    }
    /// Resolve only requested content keys rather than materialising the full encoding table.
    static func encoding(_ data: Data, wanted: Set<String>) throws -> [String: String] {
        var r = BCDBnetReader(data: data)
        guard try r.bytes(2) == Data("EN".utf8), try r.number(1) == 1,
              try r.number(1) == 16, try r.number(1) == 16 else { throw BCDBattleNetError.format("encoding") }
        let pageSize = try Int(r.number(2)) * 1024
        _ = try r.number(2)
        let pages = try Int(r.number(4)); _ = try r.number(4); _ = try r.number(1)
        let specs = try Int(r.number(4))
        guard pageSize > 0, pageSize <= 1 << 20, pages <= 100_000, specs <= r.remaining else {
            throw BCDBattleNetError.format("encoding-header")
        }
        _ = try r.bytes(specs)
        let index = try r.bytes(pages * 32)
        var found: [String: String] = [:]
        for p in 0..<pages {
            let page = try r.bytes(pageSize)
            guard md5(page) == hex(index.subdata(in: p * 32 + 16..<p * 32 + 32)) else { throw BCDContentError.checkFailed }
            var row = BCDBnetReader(data: page)
            while row.remaining > 0 {
                let n = try Int(row.number(1)); if n == 0 { break }
                _ = try row.number(5)
                let content = hex(try row.bytes(16))
                let keys = try row.bytes(n * 16)
                if wanted.contains(content) { found[content] = hex(keys.prefix(16)) }
            }
        }
        guard wanted.isSubset(of: Set(found.keys)) else { throw BCDBattleNetError.format("encoding-missing") }
        return found
    }
    /// Archive indexes are paged, with a TOC/footer after the pages. Only keep wanted EKeys.
    static func archive(_ data: Data, archive: String, wanted: Set<String>) throws -> [String: Payload] {
        guard key(archive), data.count >= 28 else { throw BCDBattleNetError.format("archive") }
        let f = [UInt8](data.suffix(28))
        guard f[8] <= 1, f[12] == 4, f[13] == 4, f[14] == 16, f[15] == 8 else {
            throw BCDBattleNetError.format("archive-version")
        }
        let pageSize = Int(f[11]) * 1024
        let count = (0..<4).reduce(0) { $0 | (Int(f[16 + $1]) << ($1 * 8)) }
        guard pageSize >= 24, pageSize <= 1 << 20, count <= 2_000_000,
              ((count + pageSize / 24 - 1) / (pageSize / 24)) * pageSize <= data.count - 28 else {
            throw BCDBattleNetError.format("archive-count")
        }
        var found: [String: Payload] = [:]
        for i in 0..<count {
            let position = (i / (pageSize / 24)) * pageSize + (i % (pageSize / 24)) * 24
            var r = BCDBnetReader(data: data.subdata(in: position..<position + 24))
            let key = hex(try r.bytes(16)), size = try Int64(r.number(4)), offset = try Int64(r.number(4))
            if wanted.contains(key) {
                guard size >= 8, size < (1 << 30) - 30 else { throw BCDBattleNetError.format("archive-size") }
                found[key] = Payload(key: key, size: size, archive: archive, offset: offset)
            }
        }
        return found
    }
    static func loose(_ data: Data, wanted: Set<String>) throws -> [String: Int64] {
        guard data.count >= 28 else { throw BCDBattleNetError.format("loose-index") }
        let f = [UInt8](data.suffix(28))
        guard f[8] <= 1, f[12] == 0, f[13] == 4, f[14] == 16, f[15] == 8 else { throw BCDBattleNetError.format("loose-index-version") }
        let page = Int(f[11]) * 1024
        let count = (0..<4).reduce(0) { $0 | (Int(f[16 + $1]) << ($1 * 8)) }
        guard page >= 20, count <= 2_000_000, ((count + page / 20 - 1) / (page / 20)) * page <= data.count - 28 else {
            throw BCDBattleNetError.format("loose-index-count")
        }
        var result: [String: Int64] = [:]
        for i in 0..<count {
            let start = (i / (page / 20)) * page + (i % (page / 20)) * 20
            var r = BCDBnetReader(data: data.subdata(in: start..<start + 20))
            let key = hex(try r.bytes(16)), size = try Int64(r.number(4))
            if wanted.contains(key) { result[key] = size }
        }
        return result
    }
}
