// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CryptoKit
import Foundation

// BCD's content engine, shared by the stores (docs/GOG.md; epic.md §4): a store's adapter
// describes what to install as a plan, files made of parts, each part a slice of a chunk the
// store serves. The engine fetches every chunk once, checks it, writes each of its parts,
// journals it and commits the folder with one rename. An adapter adds only its protocol, its
// manifests and where its chunks come from. Foundation only, so the host checks compile it
// (tests/host/check-content-engine.py).

/// A job's identity across the app: the store and the store's own ID ("gog", "1207658930").
struct BCDContentKey: Hashable, Codable, CustomStringConvertible {
    var store: String
    var id: String
    var description: String { store + "." + id }
    /// The job's folder name: ASCII letters, digits, '-' and '.' only.
    var folderName: String {
        String((store + "-" + id).map { c -> Character in
            c.isASCII && (c.isLetter || c.isNumber || c == "-" || c == ".") ? c : "_"
        })
    }
}

enum BCDDigestKind: String, Codable {
    case md5, sha1, sha256
    var hexLength: Int {
        switch self {
        case .md5: return 32
        case .sha1: return 40
        case .sha256: return 64
        }
    }
}

/// A checksum the store gives for a chunk or a file, kept as lowercase hex.
struct BCDDigest: Hashable, Codable, CustomStringConvertible {
    let kind: BCDDigestKind
    let hex: String

    init?(_ kind: BCDDigestKind, _ hex: String) {
        let lower = hex.lowercased()
        guard lower.utf8.count == kind.hexLength,
              lower.utf8.allSatisfy({ ($0 >= 48 && $0 <= 57) || ($0 >= 97 && $0 <= 102) }) else { return nil }
        self.kind = kind
        self.hex = lower
    }

    var description: String { kind.rawValue + ":" + hex }

    init(from decoder: Decoder) throws {
        let text = try decoder.singleValueContainer().decode(String.self)
        let pieces = text.split(separator: ":", maxSplits: 1).map(String.init)
        guard pieces.count == 2, let kind = BCDDigestKind(rawValue: pieces[0]), let made = BCDDigest(kind, pieces[1]) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "not a digest"))
        }
        self = made
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(description)
    }
}

/// How a chunk travels: as it is, a zlib stream (GOG), or an Epic chunk file (a header, then the
/// bytes stored or as a zlib stream: BCDChunkFileDecoder).
enum BCDChunkCodec: String, Codable {
    case stored, zlib, epic
}

/// One piece the store serves, fetched once however many files use it.
struct BCDContentChunk: Codable, Hashable {
    /// The store's name for it (GOG: its compressed MD5), which the source turns into addresses.
    var locator: String
    /// Which addresses reach it (GOG: the product whose secure link serves it).
    var group: String
    /// Bytes on the wire, and after decoding.
    var fetchSize: Int64
    var size: Int64
    var fetchDigest: BCDDigest?
    var digest: BCDDigest?
    var codec: BCDChunkCodec
}

/// A slice of a chunk's decoded bytes and where it goes in a file.
struct BCDContentPart: Codable, Hashable {
    var chunk: Int
    var chunkOffset: Int64
    var length: Int64
    var file: Int
    var fileOffset: Int64

    private enum CodingKeys: String, CodingKey {
        case chunk = "c", chunkOffset = "o", length = "l", file = "f", fileOffset = "p"
    }
}

struct BCDContentFile: Codable, Hashable {
    /// Relative to the install folder, '/' between folders, folder names folded (BCDContentPaths).
    var path: String
    var size: Int64
    /// The whole file's checksum, when the store gives one.
    var digest: BCDDigest?
}

/// An error that has a short reason of its own for the log (BCDContentQueue.reason): the queue
/// would otherwise print the error's type name, which says nothing about the step that failed.
protocol BCDLogReasonError: Error {
    var logReason: String { get }
}

enum BCDContentError: Error, Equatable, LocalizedError {
    case notEnoughSpace(needed: Int64, available: Int64)
    case badPlan(String)
    case unsafePath
    case pathClash
    case driveUnavailable(String)
    case http(Int)
    case checkFailed
    case tooLarge
    case decode
    case chunkFailed(String)
    case diskFull
    case io(Int32)
    case folderExists
    case refused(String)

    var errorDescription: String? {
        switch self {
        case .notEnoughSpace(let needed, let available):
            let f = { (v: Int64) in ByteCountFormatter.string(fromByteCount: v, countStyle: .file) }
            return "Not enough free space: this needs \(f(needed)) more and \(f(available)) is free. Free some space, then resume."
        case .badPlan: return "The store's description of this game didn't make sense to BCD. Try again later."
        case .unsafePath: return "The store listed a file BCD won't write (a name outside the game's folder)."
        case .pathClash: return "The store listed a file and a folder with the same name."
        case .driveUnavailable(let drive): return "Drive \(drive): isn't connected."
        case .http(let status): return "The download server answered \(status)."
        case .checkFailed: return "A downloaded piece failed its check."
        case .tooLarge: return "The server sent more than expected."
        case .decode: return "A downloaded piece couldn't be unpacked."
        case .chunkFailed(let why): return "A piece of the game kept failing to download (\(why)). Check the connection, then resume."
        case .diskFull: return "The device ran out of space while writing. Free some space, then resume."
        case .io(let code): return "Writing the game's files failed (error \(code))."
        case .folderExists: return "A folder with this game's name is already there."
        case .refused(let why): return why
        }
    }

    /// A short, path- and secret-free reason for the log.
    var logReason: String {
        switch self {
        case .notEnoughSpace: return "space"
        case .badPlan: return "plan"
        case .unsafePath: return "path"
        case .pathClash: return "clash"
        case .driveUnavailable: return "drive"
        case .http(let status): return "http-\(status)"
        case .checkFailed: return "check"
        case .tooLarge: return "size"
        case .decode: return "decode"
        case .chunkFailed(let why): return "chunk-" + why
        case .diskFull: return "disk-full"
        case .io(let code): return "io-\(code)"
        case .folderExists: return "folder-exists"
        case .refused: return "refused"
        }
    }
}

extension BCDContentError: BCDLogReasonError {}

// MARK: - Paths

/// The rules every path in a plan follows: relative, no "." or "..", no drive letter, no
/// character Windows refuses, no reserved device name, no trailing dot or space (Windows drops
/// them, so two names would meet), bounded lengths. Store manifests use '\', plans '/'.
enum BCDContentPaths {
    /// Bytes of a path inside the install folder: the device's whole path must stay under 1024.
    static let maxPath = 768
    static let maxComponent = 255
    private static let reserved: Set<String> = {
        var names: Set<String> = ["con", "prn", "aux", "nul", "conin$", "conout$"]
        for n in 1...9 { names.insert("com\(n)"); names.insert("lpt\(n)") }
        return names
    }()

    static func components(_ raw: String) throws -> [String] {
        let path = raw.replacingOccurrences(of: "\\", with: "/")
        guard !path.isEmpty, path.utf8.count <= maxPath, !path.hasPrefix("/") else { throw BCDContentError.unsafePath }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        for part in parts { try check(component: part) }
        return parts
    }

    static func check(component part: String) throws {
        guard !part.isEmpty, part != ".", part != "..", part.utf8.count <= maxComponent,
              !part.hasSuffix("."), !part.hasSuffix(" ") else { throw BCDContentError.unsafePath }
        for scalar in part.unicodeScalars {
            if scalar.value < 0x20 || scalar.value == 0x7F || "<>:\"|?*".unicodeScalars.contains(scalar) {
                throw BCDContentError.unsafePath
            }
        }
        let stem = part.split(separator: ".", maxSplits: 1, omittingEmptySubsequences: false).first.map(String.init) ?? part
        if reserved.contains(stem.lowercased().trimmingCharacters(in: .whitespaces)) { throw BCDContentError.unsafePath }
    }

    /// One folder name chosen from a store's text (an install folder): unsafe characters become
    /// '_', trailing dots and spaces go; nil when nothing usable is left.
    static func folderName(_ text: String) -> String? {
        var name = String(text.unicodeScalars.map { s -> Character in
            s.value < 0x20 || s.value == 0x7F || "<>:\"|?*/\\".unicodeScalars.contains(s) ? "_" : Character(s)
        })
        while name.hasSuffix(".") || name.hasSuffix(" ") { name.removeLast() }
        name = name.trimmingCharacters(in: .whitespaces)
        while name.utf8.count > 120 { name.removeLast() }
        guard (try? check(component: name)) != nil else { return nil }
        return name
    }

    static func fold(_ path: String) -> String { path.lowercased() }
}

/// Folders spelled two ways in one manifest ("Data" and "data") are one folder on Windows; the
/// device's file system tells them apart, so the first spelling is kept for every later path.
/// A file and a folder of the same name cannot both exist.
struct BCDPathFolding {
    private var directories: [String: String] = [:]     // folded path → spelled path
    private var files: Set<String> = []                 // folded paths

    /// The canonical spelling of a file path (folders folded to their first spelling).
    mutating func file(_ components: [String]) throws -> String {
        let directory = try self.directory(Array(components.dropLast()))
        let spelled = directory.isEmpty ? components.last! : directory + "/" + components.last!
        let folded = BCDContentPaths.fold(spelled)
        guard directories[folded] == nil else { throw BCDContentError.pathClash }
        files.insert(folded)
        return spelled
    }

    /// The canonical spelling of a folder path; "" for the install folder itself.
    mutating func directory(_ components: [String]) throws -> String {
        var spelled = ""
        for component in components {
            let candidate = spelled.isEmpty ? component : spelled + "/" + component
            let folded = BCDContentPaths.fold(candidate)
            guard !files.contains(folded) else { throw BCDContentError.pathClash }
            if let known = directories[folded] {
                spelled = known
            } else {
                directories[folded] = candidate
                spelled = candidate
            }
        }
        return spelled
    }

    var directoryPaths: [String] { directories.values.sorted() }
}

// MARK: - The plan

struct BCDContentPlan: Codable {
    static let currentFormat = 1
    var format = BCDContentPlan.currentFormat
    var key: BCDContentKey
    var title: String
    /// The store's build or version this plan installs.
    var version: String
    /// The install folder's name under its root.
    var folder: String
    var directories: [String]
    var files: [BCDContentFile]
    var chunks: [BCDContentChunk]
    /// Ordered by chunk, then by offset in the chunk.
    var parts: [BCDContentPart]
    /// A SHA-256 over everything that decides what lands on disk: a journal belongs to one plan.
    var identity: String

    var totalBytes: Int64 { files.reduce(0) { $0 + $1.size } }
    var downloadBytes: Int64 { chunks.reduce(0) { $0 + $1.fetchSize } }

    func computeIdentity() -> String {
        var hash = SHA256()
        func add(_ line: String) { hash.update(data: Data((line + "\n").utf8)) }
        add("bcd-content-plan \(format) \(key.store) \(key.id) \(version) \(folder)")
        for directory in directories { add("D \(directory)") }
        for file in files { add("F \(file.path)\t\(file.size)\t\(file.digest?.description ?? "-")") }
        for chunk in chunks {
            add("C \(chunk.group)\t\(chunk.locator)\t\(chunk.fetchSize)\t\(chunk.size)\t\(chunk.fetchDigest?.description ?? "-")\t\(chunk.digest?.description ?? "-")\t\(chunk.codec.rawValue)")
        }
        for part in parts { add("P \(part.chunk) \(part.chunkOffset) \(part.length) \(part.file) \(part.fileOffset)") }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// Everything the engine relies on, checked again for a plan read back from disk.
    func validate() throws {
        guard format == Self.currentFormat, identity == computeIdentity() else { throw BCDContentError.badPlan("identity") }
        guard BCDContentPaths.folderName(folder) == folder else { throw BCDContentError.unsafePath }
        var folding = BCDPathFolding()
        for directory in directories {
            guard try folding.directory(BCDContentPaths.components(directory)) == directory else { throw BCDContentError.badPlan("folder spelling") }
        }
        for file in files {
            guard file.size >= 0, try folding.file(BCDContentPaths.components(file.path)) == file.path else {
                throw BCDContentError.badPlan("file spelling")
            }
        }
        guard Set(files.map { BCDContentPaths.fold($0.path) }).count == files.count else { throw BCDContentError.badPlan("duplicate file") }
        for chunk in chunks {
            guard chunk.size > 0, chunk.fetchSize > 0, chunk.size <= 1 << 30, chunk.fetchSize <= 1 << 30,
                  chunk.codec != .stored || chunk.size == chunk.fetchSize, !chunk.locator.isEmpty else {
                throw BCDContentError.badPlan("chunk")
            }
        }
        var lastChunk = -1, lastOffset: Int64 = -1
        var used = [Bool](repeating: false, count: chunks.count)
        var byFile = [[(Int64, Int64)]](repeating: [], count: files.count)
        for part in parts {
            guard chunks.indices.contains(part.chunk), files.indices.contains(part.file), part.length > 0,
                  part.chunkOffset >= 0, part.fileOffset >= 0,
                  part.chunkOffset + part.length <= chunks[part.chunk].size,
                  part.fileOffset + part.length <= files[part.file].size,
                  part.chunk > lastChunk || (part.chunk == lastChunk && part.chunkOffset >= lastOffset) else {
                throw BCDContentError.badPlan("part")
            }
            lastChunk = part.chunk
            lastOffset = part.chunkOffset
            used[part.chunk] = true
            byFile[part.file].append((part.fileOffset, part.length))
        }
        guard !used.contains(false) else { throw BCDContentError.badPlan("unused chunk") }
        // Every byte of every file comes from exactly one part.
        for (index, ranges) in byFile.enumerated() {
            var end: Int64 = 0
            for (offset, length) in ranges.sorted(by: { $0.0 < $1.0 }) {
                guard offset == end else { throw BCDContentError.badPlan("coverage") }
                end += length
            }
            guard end == files[index].size else { throw BCDContentError.badPlan("coverage") }
        }
    }

    /// Lookups the engine needs, made once per run.
    struct Index {
        /// parts[partRanges[c]] are chunk c's parts.
        let partRanges: [Range<Int>]
        /// The chunks each file is made of.
        let fileChunks: [[Int]]

        init(_ plan: BCDContentPlan) {
            var ranges = [Range<Int>](repeating: 0..<0, count: plan.chunks.count)
            var fileChunks = [[Int]](repeating: [], count: plan.files.count)
            var start = 0
            while start < plan.parts.count {
                let chunk = plan.parts[start].chunk
                var end = start
                while end < plan.parts.count && plan.parts[end].chunk == chunk {
                    let file = plan.parts[end].file
                    if fileChunks[file].last != chunk { fileChunks[file].append(chunk) }
                    end += 1
                }
                ranges[chunk] = start..<end
                start = end
            }
            partRanges = ranges
            self.fileChunks = fileChunks
        }
    }
}

/// Builds a plan from a store's manifests. Paths are checked and folded as they come; a file
/// listed again at the same path (letter case aside) replaces the earlier one, so a later depot
/// wins a path; identical chunks (same group and locator) are fetched once.
struct BCDContentPlanBuilder {
    private struct ChunkKey: Hashable { var group: String; var locator: String }

    let key: BCDContentKey
    let title: String
    let version: String
    let folder: String
    private var folding = BCDPathFolding()
    private var files: [BCDContentFile] = []
    private var fileByPath: [String: Int] = [:]
    private var fileParts: [[BCDContentPart]] = []
    private var chunks: [BCDContentChunk] = []
    private var chunkByKey: [ChunkKey: Int] = [:]

    init(key: BCDContentKey, title: String, version: String, folder: String) throws {
        guard BCDContentPaths.folderName(folder) == folder else { throw BCDContentError.unsafePath }
        self.key = key
        self.title = title
        self.version = version
        self.folder = folder
    }

    var fileCount: Int { files.count }

    /// Whether a file of this path (letter case aside) is planned already.
    func hasFile(_ raw: String) -> Bool {
        guard let parts = try? BCDContentPaths.components(raw) else { return false }
        return fileByPath[BCDContentPaths.fold(parts.joined(separator: "/"))] != nil
    }

    mutating func addDirectory(_ raw: String) throws {
        _ = try folding.directory(BCDContentPaths.components(raw))
    }

    @discardableResult
    mutating func addFile(_ raw: String, size: Int64, digest: BCDDigest?) throws -> Int {
        guard size >= 0, size <= 1 << 40 else { throw BCDContentError.badPlan("file size") }
        let path = try folding.file(BCDContentPaths.components(raw))
        let folded = BCDContentPaths.fold(path)
        if let index = fileByPath[folded] {
            files[index] = BCDContentFile(path: path, size: size, digest: digest)
            fileParts[index] = []
            return index
        }
        files.append(BCDContentFile(path: path, size: size, digest: digest))
        fileParts.append([])
        fileByPath[folded] = files.count - 1
        return files.count - 1
    }

    mutating func addChunk(_ chunk: BCDContentChunk) throws -> Int {
        let key = ChunkKey(group: chunk.group, locator: chunk.locator)
        if let index = chunkByKey[key] {
            guard chunks[index] == chunk else { throw BCDContentError.badPlan("chunk listed twice, differently") }
            return index
        }
        chunks.append(chunk)
        chunkByKey[key] = chunks.count - 1
        return chunks.count - 1
    }

    mutating func addPart(chunk: Int, chunkOffset: Int64, length: Int64, file: Int, fileOffset: Int64) throws {
        guard chunks.indices.contains(chunk), files.indices.contains(file) else { throw BCDContentError.badPlan("part") }
        fileParts[file].append(BCDContentPart(chunk: chunk, chunkOffset: chunkOffset, length: length, file: file, fileOffset: fileOffset))
    }

    func build() throws -> BCDContentPlan {
        // Chunks no remaining file uses (their file was replaced) are dropped and renumbered.
        var all = fileParts.flatMap { $0 }
        let usedChunks = Set(all.map(\.chunk)).sorted()
        var renumber = [Int: Int]()
        for (new, old) in usedChunks.enumerated() { renumber[old] = new }
        for i in all.indices { all[i].chunk = renumber[all[i].chunk]! }
        all.sort { ($0.chunk, $0.chunkOffset, $0.file, $0.fileOffset) < ($1.chunk, $1.chunkOffset, $1.file, $1.fileOffset) }
        var plan = BCDContentPlan(key: key, title: title, version: version, folder: folder,
                                  directories: folding.directoryPaths, files: files,
                                  chunks: usedChunks.map { chunks[$0] }, parts: all, identity: "")
        plan.identity = plan.computeIdentity()
        try plan.validate()
        return plan
    }
}

// MARK: - Install roots

/// Where a store installs: a Windows drive and a folder on it ("C", "GOG Games"). Records keep
/// this drive-relative form, so games on the external drive (D:, docs/research/
/// external-ssd-2026-10-17/PLAN.md) need no migration. Only C: resolves today: D: answers once
/// the external drive's folder is mapped (that plan's Phase 1).
struct BCDInstallRoot: Codable, Hashable {
    var drive: String
    var folder: String

    /// Drive letter → the folder on the device. Replaced by the host checks.
    static var hostDrive: (String) -> URL? = { letter in
        guard letter == "C" else { return nil }
        return FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("wine/drive_c", isDirectory: true)
    }

    var url: URL? { Self.hostDrive(drive)?.appendingPathComponent(folder, isDirectory: true) }
    /// Staged downloads, on the same volume as the games so a commit is a rename. Hidden: the
    /// drive_c scan for "Add every game" skips it.
    var stagingURL: URL? { url?.appendingPathComponent(".bcd-stage", isDirectory: true) }

    /// C:\<folder>\<a>\<b> for the given folder-relative '/' path pieces.
    func windowsPath(_ relative: String...) -> String {
        ([drive + ":", folder] + relative.filter { !$0.isEmpty })
            .joined(separator: "\\").replacingOccurrences(of: "/", with: "\\")
    }
}
