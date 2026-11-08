// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

// Updating and repairing an installed GOG game, and DLC (build 632; docs/GOG.md "Updates, repair and DLC"). The shared
// engine downloads a plan into a staging folder and commits it with one rename, which fits an install. An update or a
// repair plans only the files the installed game lacks or has wrong (the delta), downloads them into the same kind of
// staging folder, and then puts them into the game's folder one file at a time (a rename each, so a file is the old one or
// the new one, never half), after the files the new build no longer has are removed. Foundation only (the host checks
// compile it).
//
// What decides whether a file is already right:
// - an update: the installed build's depot manifests (their hashes are on the record) and the new build's say the file is
//   the same (a whole-file digest, else the MD5s of its chunks), and the file is on the disk at its size. The disk is not
//   read. When the old manifests cannot be read, every file is checked as a repair checks it.
// - a repair: the file on the disk is read and compared with the manifest, chunk by chunk by the chunk's MD5 (or by the
//   whole-file digest when the manifest has no chunk MD5s). A file that is missing, of another size or different is
//   downloaded again.

/// What a job on an installed game is.
enum BCDGOGJobKind: String, Codable, Equatable {
    case install, update, repair
}

/// What the plan knows of one file of a new build, for the question "is the installed copy already right".
struct BCDGOGFileFacts: Equatable {
    /// The path as the manifest has it, '/' between folders.
    var path: String
    var size: Int64
    /// The whole file's digest, when the manifest has one.
    var digest: BCDDigest?
    /// The chunks it is made of, in order, each with the MD5 of its decoded bytes.
    var chunks: [BCDContentChunk]
    /// What identifies the file's contents across builds (`BCDGOGUpdate.identity`): nil when the manifest says too little.
    var identity: String?
}

enum BCDGOGUpdate {
    // MARK: What a file is

    /// What identifies a file's contents across builds: its whole-file digest, else the MD5s of its chunks in order (the
    /// value gogdl compares between two builds). nil for a file whose bytes only come from the depot's small-files
    /// container and that has no digest: nothing says it is the same.
    static func identity(ofItem item: [String: Any]) -> String? {
        if let md5 = item["md5"] as? String, !md5.isEmpty { return "md5:" + md5.lowercased() }
        if let sha = item["sha256"] as? String, !sha.isEmpty { return "sha256:" + sha.lowercased() }
        let chunks = item["chunks"] as? [[String: Any]] ?? []
        guard !chunks.isEmpty else { return nil }
        let sums = chunks.compactMap { ($0["md5"] as? String)?.lowercased() }
        return sums.count == chunks.count ? "chunks:" + sums.joined(separator: ",") : nil
    }

    /// The files of one depot manifest by folded path, with their identities (support files left out): what an installed
    /// build's depot says about its files.
    static func identities(ofDepot object: [String: Any]) -> [String: String?] {
        var out: [String: String?] = [:]
        guard let items = (object["depot"] as? [String: Any])?["items"] as? [[String: Any]] else { return out }
        for item in items where (item["type"] as? String) == "DepotFile" {
            guard let path = item["path"] as? String, !((item["flags"] as? [String]) ?? []).contains("support") else { continue }
            out[BCDContentPaths.fold(path.replacingOccurrences(of: "\\", with: "/"))] = identity(ofItem: item)
        }
        return out
    }

    // MARK: Reading the disk

    /// The file at `relative` ('/'-separated, letter case free: the device's files are case-sensitive) under `folder`, when it is
    /// a regular file; nil otherwise. Symbolic links are not followed.
    static func existing(_ relative: String, in folder: URL) -> URL? {
        guard let spelled = BCDGOGInstall.onDisk(relative, in: folder, directory: false) else { return nil }
        let url = folder.appendingPathComponent(spelled)
        var info = stat()
        guard lstat(url.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG else { return nil }
        return url
    }

    /// Whether the file on the disk is what the manifest describes: its size, then every chunk's MD5 (one pass, read in 1 MiB
    /// pieces), or the whole-file digest when the chunks carry none. `progress` gets the bytes read.
    static func matches(_ url: URL, _ facts: BCDGOGFileFacts, progress: (Int64) -> Void = { _ in }) -> Bool {
        var info = stat()
        guard lstat(url.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, Int64(info.st_size) == facts.size else { return false }
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var buffer = [UInt8](repeating: 0, count: 1 << 20)

        /// Reads exactly `count` bytes into a hasher; false when the file ends early.
        func feed(_ hasher: inout BCDHasher, _ count: Int64) -> Bool {
            var left = count
            while left > 0 {
                if Task.isCancelled { return false }
                let n = buffer.withUnsafeMutableBytes { read(fd, $0.baseAddress!, min($0.count, Int(min(left, Int64(Int.max))))) }
                if n < 0 { if errno == EINTR { continue }; return false }
                if n == 0 { return false }
                buffer.withUnsafeBytes { hasher.update(UnsafeRawBufferPointer(rebasing: $0[0..<n])) }
                left -= Int64(n)
                progress(Int64(n))
            }
            return true
        }

        let chunkSum = facts.chunks.reduce(Int64(0)) { $0 + $1.size }
        if !facts.chunks.isEmpty, chunkSum == facts.size, facts.chunks.allSatisfy({ $0.digest != nil }) {
            for chunk in facts.chunks {
                var hasher = BCDHasher(chunk.digest!.kind)
                guard feed(&hasher, chunk.size), hasher.finalize() == chunk.digest!.hex else { return false }
            }
            return true
        }
        if let digest = facts.digest {
            var hasher = BCDHasher(digest.kind)
            return feed(&hasher, facts.size) && hasher.finalize() == digest.hex
        }
        return false   // nothing to compare with: download it
    }

    // MARK: Putting the downloaded files into the game's folder

    struct MergeResult: Equatable {
        var replaced = 0
        var added = 0
        var removed = 0
    }

    /// The files of `plan` in `stage` go into `folder` (a rename each, over the file there, which is replaced atomically); the
    /// files in `remove` ('/'-separated, the old build's, letter case free) are deleted from it. The device's spelling of a
    /// folder or file that is already there is kept. Throws without touching the rest when a file cannot be placed.
    static func merge(_ plan: BCDContentPlan, stage: URL, into folder: URL, remove: [String]) throws -> MergeResult {
        let fm = FileManager.default
        var result = MergeResult()
        for file in plan.files {
            let source = stage.appendingPathComponent(file.path)
            var info = stat()
            guard lstat(source.path, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG, Int64(info.st_size) == file.size else {
                throw BCDContentError.io(ENOENT)
            }
            // The folders: each as the device already spells it, else as the plan does.
            var directory = folder
            let parts = file.path.split(separator: "/").map(String.init)
            for part in parts.dropLast() {
                let names = (try? fm.contentsOfDirectory(atPath: directory.path)) ?? []
                let name = names.first { $0 == part } ?? names.first { $0.lowercased() == part.lowercased() } ?? part
                directory = directory.appendingPathComponent(name, isDirectory: true)
                var isDirectory: ObjCBool = false
                if fm.fileExists(atPath: directory.path, isDirectory: &isDirectory) {
                    guard isDirectory.boolValue else { throw BCDContentError.pathClash }
                } else {
                    try fm.createDirectory(at: directory, withIntermediateDirectories: false)
                }
            }
            let last = parts[parts.count - 1]
            let names = (try? fm.contentsOfDirectory(atPath: directory.path)) ?? []
            let existing = names.first { $0 == last } ?? names.first { $0.lowercased() == last.lowercased() }
            let target = directory.appendingPathComponent(existing ?? last)
            var targetInfo = stat()
            if lstat(target.path, &targetInfo) == 0, (targetInfo.st_mode & S_IFMT) == S_IFDIR { throw BCDContentError.pathClash }
            guard rename(source.path, target.path) == 0 else { throw BCDContentError.io(errno) }
            if existing == nil { result.added += 1 } else { result.replaced += 1 }
        }
        for path in remove {
            guard let url = existing(path, in: folder) else { continue }
            if (try? fm.removeItem(at: url)) != nil { result.removed += 1 }
        }
        return result
    }
}
