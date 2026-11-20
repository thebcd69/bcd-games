// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Epic's binary build manifest (docs/research/open-fork-2026-10-18/epic.md §2.3 and §6): the file the
// asset service points a build at. It names a game's files and the chunks they are made of. Written
// from the layout the design records (its first source is the permissively licensed Kaitai spec of the
// format, up to feature level 21; no code of any other Epic client). Foundation only (the host checks
// compile it); every length and count is checked before it is used, and the body's SHA-1 against the
// header's.
//
// Layout, little-endian, strings "FStrings" (an int32 length that counts the closing NUL; a negative
// length means UTF-16 and counts characters; 0 is empty):
//   header: magic 0x44BEC00C, header size, decoded size, stored size (u32 each), SHA-1 of the decoded
//     body (20 bytes), stored-as (u8: bit 0 zlib, bit 1 encrypted), feature level (u32).
//   body, as four sections that each start with their own size (u32) and data version (u8), so a section
//     is skipped by its size whatever it holds after what BCD reads:
//   - meta: feature level, is-file-data, app ID, app name, build version, launch exe, launch command,
//     prerequisite IDs, name, path and arguments; data version 1 adds the build ID.
//   - chunk list, one column after another: GUIDs (16 bytes), rolling hashes (u64), SHA-1s (20 bytes),
//     group numbers (u8), window sizes (u32, the decoded size), chunk file sizes (i64).
//   - file list, one column after another: names, symlink targets, SHA-1s, flags (u8), install tags,
//     chunk parts (each: its size u32, chunk GUID, offset u32, size u32).
//   - custom fields (not read).
//
// Round 668 (merge N008; Bus Simulator 21, Send Log 0C8N69: "reason=manifest-magic"): older builds are described by Epic's JSON
// manifest instead (4 of the 34 games docs/research/open-fork-2026-10-18/epic.md surveyed). It holds the same lists keyed by chunk GUID
// (32 hex digits, four big-endian words), with numbers and hashes as "blobs": three decimal digits per byte, the bytes little-endian;
// the chunk SHA-1s are hex. It stores no window size (the chunks of that era are 1 MiB) and no body hash (the CDN file's SHA-1 against
// the asset pointer is the check). The same format facts are read by app/Madeira/BCDEpic/EpicFormats.swift (Akshay CM, after Legendary's
// json_manifest.py) for cloud saves; this reader is BCD's own, into BCD's types and with BCD's bounds.

enum BCDEpicManifestError: Error, Equatable, LocalizedError {
    case notManifest
    case truncated
    case malformed(String)
    case badHash
    case encrypted
    case newer(Int)

    var errorDescription: String? {
        switch self {
        case .notManifest, .truncated, .malformed, .badHash:
            return "Epic's description of this game wasn't what BCD expects (it may have changed). Try again later."
        case .encrypted:
            return "This build of the game is encrypted by Epic (a pre-release download). BCD can't read it."
        case .newer:
            return "Epic describes this game in a newer format than this version of BCD reads. It's planned for a later update."
        }
    }

    var logReason: String {
        switch self {
        case .notManifest: return "manifest-magic"
        case .truncated: return "manifest-truncated"
        case .malformed(let what): return "manifest-" + what
        case .badHash: return "manifest-hash"
        case .encrypted: return "manifest-encrypted"
        case .newer(let level): return "manifest-level-\(level)"
        }
    }
}

extension BCDEpicManifestError: BCDLogReasonError {}

/// A chunk's identity: four little-endian 32-bit words, written as 32 upper-case hexadecimal digits.
struct BCDEpicGUID: Hashable {
    var a: UInt32, b: UInt32, c: UInt32, d: UInt32
    var hex: String { String(format: "%08X%08X%08X%08X", a, b, c, d) }
}

struct BCDEpicManifest {
    struct Chunk: Equatable {
        var guid: BCDEpicGUID
        var rollingHash: UInt64
        var sha1: [UInt8]
        var group: UInt8
        /// Bytes after decoding (the chunk's window).
        var window: Int64
        /// Bytes of the chunk file.
        var fileSize: Int64
    }

    struct Part: Equatable {
        /// Index into `chunks`.
        var chunk: Int
        var offset: Int64
        var size: Int64
    }

    struct File: Equatable {
        var name: String
        var symlinkTarget: String
        var sha1: [UInt8]
        var flags: UInt8
        var tags: [String]
        var parts: [Part]
        var size: Int64 { parts.reduce(0) { $0 + $1.size } }
    }

    /// The header's feature level (the format's version).
    var featureLevel = 0
    var appName = ""
    var buildVersion = ""
    var launchExe = ""
    var launchCommand = ""
    var prereqIDs: [String] = []
    var prereqName = ""
    var prereqPath = ""
    var prereqArgs = ""
    var buildID: String?
    var chunks: [Chunk] = []
    var files: [File] = []

    /// Epic's own launcher never wrote a level above this one in the layout BCD reads.
    static let newestLevel = 21
    static let maxBody = 512 << 20
    static let maxChunks = 4_000_000
    static let maxFiles = 2_000_000
    static let maxParts = 12_000_000

    var totalBytes: Int64 { files.reduce(0) { $0 + $1.size } }

    /// The chunks the files use, each once (what a download fetches), by their file sizes.
    var downloadBytes: Int64 {
        var used = Set<Int>()
        for file in files { for part in file.parts { used.insert(part.chunk) } }
        return used.reduce(0) { $0 + chunks[$1].fileSize }
    }

    // MARK: Parsing

    /// A bounds-checked reader over a byte range.
    private struct Cursor {
        let bytes: [UInt8]
        var position: Int
        var limit: Int

        init(_ bytes: [UInt8], from: Int = 0, to: Int? = nil) {
            self.bytes = bytes
            position = from
            limit = to ?? bytes.count
        }

        var remaining: Int { limit - position }

        mutating func skip(_ count: Int) throws {
            guard count >= 0, count <= remaining else { throw BCDEpicManifestError.truncated }
            position += count
        }

        mutating func u8() throws -> UInt8 {
            guard remaining >= 1 else { throw BCDEpicManifestError.truncated }
            defer { position += 1 }
            return bytes[position]
        }

        mutating func u32() throws -> UInt32 {
            guard remaining >= 4 else { throw BCDEpicManifestError.truncated }
            defer { position += 4 }
            return UInt32(bytes[position]) | UInt32(bytes[position + 1]) << 8 | UInt32(bytes[position + 2]) << 16 | UInt32(bytes[position + 3]) << 24
        }

        mutating func i32() throws -> Int32 { Int32(bitPattern: try u32()) }

        mutating func u64() throws -> UInt64 {
            guard remaining >= 8 else { throw BCDEpicManifestError.truncated }
            var value: UInt64 = 0
            for i in 0..<8 { value |= UInt64(bytes[position + i]) << (8 * UInt64(i)) }
            position += 8
            return value
        }

        mutating func i64() throws -> Int64 { Int64(bitPattern: try u64()) }

        mutating func take(_ count: Int) throws -> [UInt8] {
            guard count >= 0, count <= remaining else { throw BCDEpicManifestError.truncated }
            defer { position += count }
            return Array(bytes[position..<position + count])
        }

        mutating func guid() throws -> BCDEpicGUID {
            BCDEpicGUID(a: try u32(), b: try u32(), c: try u32(), d: try u32())
        }

        /// An FString. Text longer than 64 KiB is not a name or an argument line.
        mutating func string() throws -> String {
            let length = Int(try i32())
            guard length > -65_536, length < 65_536 else { throw BCDEpicManifestError.malformed("string") }
            if length == 0 { return "" }
            if length > 0 {
                let raw = try take(length)
                guard raw.last == 0 else { throw BCDEpicManifestError.malformed("string") }
                return String(decoding: raw.dropLast(), as: UTF8.self)
            }
            let raw = try take(-length * 2)
            guard raw.suffix(2).elementsEqual([0, 0]) else { throw BCDEpicManifestError.malformed("string") }
            var units: [UInt16] = []
            units.reserveCapacity(-length - 1)
            var i = 0
            while i + 3 < raw.count { units.append(UInt16(raw[i]) | UInt16(raw[i + 1]) << 8); i += 2 }
            return String(decoding: units, as: UTF16.self)
        }

        /// A section: its size covers itself, so the cursor for its contents ends where it does.
        mutating func section() throws -> (cursor: Cursor, end: Int, version: UInt8) {
            let start = position
            let size = Int(try u32())
            guard size >= 5, size <= limit - start else { throw BCDEpicManifestError.malformed("section") }
            let end = start + size
            var inner = Cursor(bytes, from: position, to: end)
            let version = try inner.u8()
            position = end
            return (inner, end, version)
        }
    }

    /// The manifest in `data` (the file as the CDN serves it). Throws what it cannot read, and
    /// `.encrypted` and `.newer` for what BCD refuses.
    static func parse(_ data: Data) throws -> BCDEpicManifest {
        if let first = data.first(where: { ![0x20, 0x09, 0x0A, 0x0D, 0xEF, 0xBB, 0xBF].contains($0) }), first == 0x7B {   // "{" (after a BOM)
            return try parseJSON(data)
        }
        var header = Cursor(Array(data.prefix(4096)))
        guard data.count >= 41, try header.u32() == 0x44BE_C00C else { throw BCDEpicManifestError.notManifest }
        let headerSize = Int(try header.u32())
        let decodedSize = Int(try header.u32()), storedSize = Int(try header.u32())
        let sha = try header.take(20)
        let storedAs = try header.u8()
        let level = Int(try header.u32())
        guard headerSize >= 41, headerSize <= data.count, storedSize >= 0, storedSize <= data.count - headerSize,
              decodedSize >= 0, decodedSize <= maxBody else { throw BCDEpicManifestError.malformed("header") }
        guard storedAs & 2 == 0 else { throw BCDEpicManifestError.encrypted }
        guard level <= newestLevel else { throw BCDEpicManifestError.newer(level) }
        let stored = data.subdata(in: data.startIndex + headerSize..<data.startIndex + headerSize + storedSize)
        let body: Data
        if storedAs & 1 != 0 {
            do { body = try BCDZlib.inflate(stored, limit: maxBody) } catch { throw BCDEpicManifestError.malformed("zlib") }
        } else {
            body = stored
        }
        guard body.count == decodedSize else { throw BCDEpicManifestError.malformed("size") }
        guard BCDHasher.hex(.sha1, of: body) == sha.map({ String(format: "%02x", $0) }).joined() else { throw BCDEpicManifestError.badHash }

        let bytes = Array(body)
        var cursor = Cursor(bytes)
        var manifest = BCDEpicManifest()
        manifest.featureLevel = level

        // Meta.
        let metaSection = try cursor.section()
        var meta = metaSection.cursor
        _ = try meta.u32()                    // the feature level again
        _ = try meta.u8()                     // is file data
        _ = try meta.u32()                    // app ID
        manifest.appName = try meta.string()
        manifest.buildVersion = try meta.string()
        manifest.launchExe = try meta.string()
        manifest.launchCommand = try meta.string()
        let prereqCount = Int(try meta.u32())
        guard prereqCount >= 0, prereqCount <= 1024 else { throw BCDEpicManifestError.malformed("prereqs") }
        for _ in 0..<prereqCount { manifest.prereqIDs.append(try meta.string()) }
        manifest.prereqName = try meta.string()
        manifest.prereqPath = try meta.string()
        manifest.prereqArgs = try meta.string()
        if metaSection.version >= 1, meta.remaining > 0 { manifest.buildID = try? meta.string() }

        // Chunk list.
        var chunkList = try cursor.section().cursor
        let chunkCount = Int(try chunkList.u32())
        guard chunkCount >= 0, chunkCount <= maxChunks, chunkCount * (16 + 8 + 20 + 1 + 4 + 8) <= chunkList.remaining else {
            throw BCDEpicManifestError.malformed("chunks")
        }
        var guids: [BCDEpicGUID] = [], hashes: [UInt64] = [], shas: [[UInt8]] = [], groups: [UInt8] = []
        var windows: [Int64] = [], sizes: [Int64] = []
        guids.reserveCapacity(chunkCount)
        for _ in 0..<chunkCount { guids.append(try chunkList.guid()) }
        for _ in 0..<chunkCount { hashes.append(try chunkList.u64()) }
        for _ in 0..<chunkCount { shas.append(try chunkList.take(20)) }
        for _ in 0..<chunkCount { groups.append(try chunkList.u8()) }
        for _ in 0..<chunkCount { windows.append(Int64(try chunkList.u32())) }
        for _ in 0..<chunkCount { sizes.append(try chunkList.i64()) }
        var index: [BCDEpicGUID: Int] = [:]
        index.reserveCapacity(chunkCount)
        manifest.chunks.reserveCapacity(chunkCount)
        for i in 0..<chunkCount {
            guard windows[i] > 0, windows[i] <= 1 << 30, sizes[i] > 0, sizes[i] <= 1 << 30 else { throw BCDEpicManifestError.malformed("chunk-size") }
            guard index.updateValue(i, forKey: guids[i]) == nil else { throw BCDEpicManifestError.malformed("chunk-guid") }
            manifest.chunks.append(Chunk(guid: guids[i], rollingHash: hashes[i], sha1: shas[i], group: groups[i], window: windows[i], fileSize: sizes[i]))
        }

        // File list.
        var fileList = try cursor.section().cursor
        let fileCount = Int(try fileList.u32())
        guard fileCount >= 0, fileCount <= maxFiles, fileCount * (4 + 4 + 20 + 1 + 4 + 4) <= fileList.remaining else { throw BCDEpicManifestError.malformed("files") }
        var names: [String] = [], links: [String] = [], fileShas: [[UInt8]] = [], flags: [UInt8] = []
        var tags: [[String]] = [], parts: [[Part]] = []
        for _ in 0..<fileCount { names.append(try fileList.string()) }
        for _ in 0..<fileCount { links.append(try fileList.string()) }
        for _ in 0..<fileCount { fileShas.append(try fileList.take(20)) }
        for _ in 0..<fileCount { flags.append(try fileList.u8()) }
        for _ in 0..<fileCount {
            let count = Int(try fileList.u32())
            guard count >= 0, count <= 4096 else { throw BCDEpicManifestError.malformed("tags") }
            var list: [String] = []
            for _ in 0..<count { list.append(try fileList.string()) }
            tags.append(list)
        }
        var partTotal = 0
        for _ in 0..<fileCount {
            let count = Int(try fileList.u32())
            partTotal += count
            guard count >= 0, count * 28 <= fileList.remaining, partTotal <= maxParts else { throw BCDEpicManifestError.malformed("parts") }
            var list: [Part] = []
            list.reserveCapacity(count)
            for _ in 0..<count {
                let start = fileList.position
                let size = Int(try fileList.u32())
                guard size >= 28, size <= fileList.remaining + 4 else { throw BCDEpicManifestError.malformed("part") }
                let guid = try fileList.guid()
                let offset = Int64(try fileList.u32()), length = Int64(try fileList.u32())
                guard let chunk = index[guid] else { throw BCDEpicManifestError.malformed("part-guid") }
                guard offset + length <= manifest.chunks[chunk].window else { throw BCDEpicManifestError.malformed("part-range") }
                fileList.position = start + size
                list.append(Part(chunk: chunk, offset: offset, size: length))
            }
            parts.append(list)
        }
        manifest.files.reserveCapacity(fileCount)
        for i in 0..<fileCount {
            manifest.files.append(File(name: names[i], symlinkTarget: links[i], sha1: fileShas[i], flags: flags[i], tags: tags[i], parts: parts[i]))
        }
        return manifest
    }

    // MARK: The JSON manifest (round 668, merge N008; see the header)

    /// A blob's bytes: "012255" is [12, 255].
    static func jsonBlob(_ value: Any?) throws -> [UInt8] {
        guard let text = value as? String else { throw BCDEpicManifestError.malformed("json-blob") }
        let digits = Array(text.utf8)
        guard digits.count % 3 == 0, digits.count <= 3 * 64, digits.allSatisfy({ (0x30...0x39).contains($0) }) else {
            throw BCDEpicManifestError.malformed("json-blob")
        }
        var bytes: [UInt8] = []
        bytes.reserveCapacity(digits.count / 3)
        var i = 0
        while i < digits.count {
            let value = Int(digits[i] - 0x30) * 100 + Int(digits[i + 1] - 0x30) * 10 + Int(digits[i + 2] - 0x30)
            guard value < 256 else { throw BCDEpicManifestError.malformed("json-blob") }
            bytes.append(UInt8(value))
            i += 3
        }
        return bytes
    }

    /// A blob of at most eight bytes as a little-endian number.
    static func jsonNumber(_ value: Any?) throws -> UInt64 {
        let bytes = try jsonBlob(value)
        guard bytes.count <= 8 else { throw BCDEpicManifestError.malformed("json-number") }
        var number: UInt64 = 0
        for (shift, byte) in bytes.enumerated() { number |= UInt64(byte) << (8 * UInt64(shift)) }
        return number
    }

    /// A GUID key: 32 hex digits, the four words big-endian as written.
    static func jsonGUID(_ text: String) throws -> BCDEpicGUID {
        let chars = Array(text.utf8)
        guard chars.count == 32 else { throw BCDEpicManifestError.malformed("json-guid") }
        var words: [UInt32] = []
        for start in stride(from: 0, to: 32, by: 8) {
            guard let word = UInt32(String(decoding: chars[start..<start + 8], as: UTF8.self), radix: 16) else {
                throw BCDEpicManifestError.malformed("json-guid")
            }
            words.append(word)
        }
        return BCDEpicGUID(a: words[0], b: words[1], c: words[2], d: words[3])
    }

    /// Forty hex digits, or nil.
    static func jsonSHA1(_ text: String) -> [UInt8]? {
        let chars = Array(text.utf8)
        guard chars.count == 40 else { return nil }
        var bytes: [UInt8] = []
        for start in stride(from: 0, to: 40, by: 2) {
            guard let byte = UInt8(String(decoding: chars[start..<start + 2], as: UTF8.self), radix: 16) else { return nil }
            bytes.append(byte)
        }
        return bytes
    }

    /// The JSON manifest in `data`: the same lists as the binary one, the same bounds, the same results.
    static func parseJSON(_ data: Data) throws -> BCDEpicManifest {
        guard data.count <= maxBody, let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            throw BCDEpicManifestError.malformed("json")
        }
        guard let sizes = root["ChunkFilesizeList"] as? [String: Any], let hashes = root["ChunkHashList"] as? [String: Any],
              let groups = root["DataGroupList"] as? [String: Any], let fileList = root["FileManifestList"] as? [[String: Any]] else {
            throw BCDEpicManifestError.malformed("json-lists")
        }
        // A manifest whose file entries carry the data itself (bIsFileData) has no chunks to fetch.
        if root["bIsFileData"] as? Bool == true { throw BCDEpicManifestError.malformed("file-data") }
        let shas = root["ChunkShaList"] as? [String: Any] ?? [:]
        let level: UInt64 = try root["ManifestFileVersion"].map { try jsonNumber($0) } ?? 13
        guard level <= UInt64(newestLevel) else { throw BCDEpicManifestError.newer(Int(min(level, 1 << 20))) }
        guard sizes.count <= maxChunks, fileList.count <= maxFiles else { throw BCDEpicManifestError.malformed("json-counts") }

        var manifest = BCDEpicManifest()
        manifest.featureLevel = Int(level)
        func text(_ key: String) -> String { root[key] as? String ?? "" }
        manifest.appName = text("AppNameString")
        manifest.buildVersion = text("BuildVersionString")
        manifest.launchExe = text("LaunchExeString")
        manifest.launchCommand = text("LaunchCommand")
        manifest.prereqIDs = (root["PrereqIds"] as? [String] ?? []).prefix(1024).map { $0 }
        manifest.prereqName = text("PrereqName")
        manifest.prereqPath = text("PrereqPath")
        manifest.prereqArgs = text("PrereqArgs")

        // Chunks, in the order of their GUIDs (the JSON object has none of its own).
        var index: [BCDEpicGUID: Int] = [:]
        index.reserveCapacity(sizes.count)
        manifest.chunks.reserveCapacity(sizes.count)
        for key in sizes.keys.sorted() {
            let guid = try jsonGUID(key)
            let fileSize = try jsonNumber(sizes[key])
            guard fileSize > 0, fileSize <= 1 << 30 else { throw BCDEpicManifestError.malformed("chunk-size") }
            // No SHA-1 is all zeros, which the content plan treats as "no digest" (the rolling hash and the file's SHA-1 still check).
            var sha = [UInt8](repeating: 0, count: 20)
            if let listed = shas[key] {
                guard let hex = listed as? String, let bytes = jsonSHA1(hex) else { throw BCDEpicManifestError.malformed("chunk-sha") }
                sha = bytes
            }
            let rolling = try jsonNumber(hashes[key])
            let group = UInt8(truncatingIfNeeded: try jsonNumber(groups[key]))
            guard index.updateValue(manifest.chunks.count, forKey: guid) == nil else { throw BCDEpicManifestError.malformed("chunk-guid") }
            manifest.chunks.append(Chunk(guid: guid, rollingHash: rolling, sha1: sha, group: group, window: 1 << 20, fileSize: Int64(fileSize)))
        }

        var partTotal = 0
        manifest.files.reserveCapacity(fileList.count)
        for item in fileList {
            guard let name = item["Filename"] as? String, let chunkParts = item["FileChunkParts"] as? [[String: Any]] else {
                throw BCDEpicManifestError.malformed("json-file")
            }
            let sha = try jsonBlob(item["FileHash"])
            guard sha.count == 20 else { throw BCDEpicManifestError.malformed("json-file-hash") }
            var flags: UInt8 = 0
            if item["bIsReadOnly"] as? Bool == true { flags |= 1 }
            if item["bIsCompressed"] as? Bool == true { flags |= 2 }
            if item["bIsUnixExecutable"] as? Bool == true { flags |= 4 }
            let tags = item["InstallTags"] as? [String] ?? []
            guard tags.count <= 4096 else { throw BCDEpicManifestError.malformed("tags") }
            partTotal += chunkParts.count
            guard partTotal <= maxParts else { throw BCDEpicManifestError.malformed("parts") }
            var parts: [Part] = []
            parts.reserveCapacity(chunkParts.count)
            for part in chunkParts {
                guard let key = part["Guid"] as? String else { throw BCDEpicManifestError.malformed("part-guid") }
                guard let chunk = index[try jsonGUID(key)] else { throw BCDEpicManifestError.malformed("part-guid") }
                let offset = Int64(try jsonNumber(part["Offset"]))
                let size = Int64(try jsonNumber(part["Size"]))
                guard offset >= 0, size >= 0, offset + size <= manifest.chunks[chunk].window else { throw BCDEpicManifestError.malformed("part-range") }
                parts.append(Part(chunk: chunk, offset: offset, size: size))
            }
            manifest.files.append(File(name: name, symlinkTarget: item["SymlinkTarget"] as? String ?? "", sha1: sha, flags: flags,
                                       tags: tags, parts: parts))
        }
        return manifest
    }
}
