// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Save backups per game (round 603), the files side: where a session's changes are looked for, the zip a backup is,
// the folder it goes to, and the restore. The rules (which files, which folders, the names) are GameSaveRules.swift.
//
// What a backup is: one zip in Documents/Save Backups/<game>/, named by its date and kind ("2026-10-25 14.32.07
// Manual.zip"), holding every file under every save root of the game with its path from drive_c, plus
// bcd-saves.json (the game, the kind, the roots). The folder shows in Files. The zip is the same stored zip
// the whole-prefix backup writes (SaveZipWriter), so any zip tool opens it.
//
// What BCD never does here: write over a backup (a taken name gets a number), delete anything but the automatic
// backups beyond the number the player kept (GameSaveRules.toRemove), write outside drive_c on a restore, or touch a
// folder that is not under Documents. A restore replaces the files the backup holds and leaves every other file where it is,
// and it first makes a "Before restore" backup of what is there.

enum GameSaveError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

/// What a backup made.
struct GameSaveBackupResult: Equatable {
    var url: URL
    var files: Int
    var bytes: Int64
}

/// One backup in the game's folder.
struct GameSaveBackupEntry: Equatable {
    var url: URL
    var name: String
    var date: Date
    var kind: GameSaveKind
    var bytes: Int64
}

/// What a scan of the places games write to found.
struct GameSaveScan: Equatable {
    var files: [GameSaveFile] = []
    /// The scan stopped at GameSaveRules.maxScanEntries.
    var truncated = false
    var entriesSeen = 0
}

enum GameSaveBackups {
    /// Set by the engine side to the app's log; the tests leave it silent.
    nonisolated(unsafe) static var log: (String) -> Void = { _ in }

    /// Documents/Save Backups, where the Files app shows it.
    static var folder: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Save Backups", isDirectory: true)
    }

    static func folder(for title: String, in root: URL = GameSaveBackups.folder) -> URL {
        root.appendingPathComponent(GameSaveRules.safeFolderName(title), isDirectory: true)
    }

    // MARK: Looking at the prefix

    /// The Windows users' folder names under drive_c/users (not Public).
    static func users(drive: URL) -> [String] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: drive.appendingPathComponent("users").path)) ?? []
        return names.filter { !$0.hasPrefix(".") && $0.caseInsensitiveCompare("Public") != .orderedSame }.sorted()
    }

    /// Follows `parts` below `base` the way Wine does: without regard to case, the given spelling for what does not exist.
    static func resolve(base: URL, parts: [String]) -> URL {
        var url = base
        for (index, part) in parts.enumerated() {
            let isFolder = index < parts.count - 1
            let exact = url.appendingPathComponent(part, isDirectory: isFolder)
            if FileManager.default.fileExists(atPath: exact.path) { url = exact; continue }
            let names = (try? FileManager.default.contentsOfDirectory(atPath: url.path)) ?? []
            if let match = names.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame }) {
                url = url.appendingPathComponent(match, isDirectory: isFolder)
            } else {
                url = exact
            }
        }
        return url
    }

    /// The files under the places games write to that were modified at or after `since`. Reads directories and attributes,
    /// changes nothing; it stops at the entry limit.
    static func changedFiles(since: Date, drive: URL, users: [String], install: String?, limit: Int = GameSaveRules.maxScanEntries) -> GameSaveScan {
        var scan = GameSaveScan()
        let fm = FileManager.default
        let cutoff = since.timeIntervalSince1970
        for base in GameSaveRules.bases(users: users, install: install) {
            guard let baseParts = GameSaveRules.parts(base.path) else { continue }
            let dir = resolve(base: drive, parts: baseParts)
            guard let walk = fm.enumerator(atPath: dir.path) else { continue }
            // The enumerator reports paths below `dir`; the scan reports them from drive_c in the spelling the disk has.
            let prefix = diskSpelling(of: dir, below: drive)
            while let rel = walk.nextObject() as? String {
                scan.entriesSeen += 1
                if scan.entriesSeen > limit { scan.truncated = true; return scan }
                let attributes = walk.fileAttributes
                let type = attributes?[.type] as? FileAttributeType
                if type == .typeDirectory {
                    if GameSaveRules.skippedFolders.contains((rel as NSString).lastPathComponent.lowercased()) { walk.skipDescendants() }
                    continue
                }
                guard type == .typeRegular, let modified = (attributes?[.modificationDate] as? Date)?.timeIntervalSince1970,
                      modified >= cutoff else { continue }
                let size = (attributes?[.size] as? NSNumber)?.int64Value ?? 0
                scan.files.append(GameSaveFile(path: prefix + "/" + rel, size: size, modified: modified))
            }
        }
        return scan
    }

    /// `dir`'s path below `drive`, in the disk's spelling ("users/user/AppData/Roaming").
    private static func diskSpelling(of dir: URL, below drive: URL) -> String {
        let d = drive.resolvingSymlinksInPath().pathComponents, p = dir.resolvingSymlinksInPath().pathComponents
        if p.count >= d.count, Array(p[..<d.count]) == d { return p[d.count...].joined(separator: "/") }
        // A folder reached through an external drive's link (drive_c/External/<name>, ExternalDrives.swift) resolves outside
        // drive_c: its path as it was built, below the drive.
        let dl = drive.standardizedFileURL.pathComponents, pl = dir.standardizedFileURL.pathComponents
        guard pl.count > dl.count, Array(pl[..<dl.count]) == dl else { return dir.lastPathComponent }
        return pl[dl.count...].joined(separator: "/")
    }

    /// Every file under the roots that a backup would hold, with sizes. Caches and logs inside a root are left out, as in a
    /// scan. `tooBig`: the roots hold more than a backup may (the files so far are returned).
    static func collect(roots: [GameSaveRoot], drive: URL, maxFiles: Int = GameSaveRules.maxBackupFiles) -> (files: [GameSaveFile], tooBig: Bool, leftOut: Int) {
        var files: [GameSaveFile] = []
        var seen = Set<String>()
        var total: Int64 = 0
        var leftOut = 0
        let fm = FileManager.default
        func add(_ path: String, _ url: URL, _ attributes: [FileAttributeKey: Any]?) -> Bool {
            guard seen.insert(path.lowercased()).inserted else { return true }
            let size = (attributes?[.size] as? NSNumber)?.int64Value ?? 0
            if size > GameSaveRules.maxFileBytes { leftOut += 1; return true }
            if total + size > GameSaveRules.maxBackupBytes || files.count >= maxFiles { return false }
            total += size
            files.append(GameSaveFile(path: path, size: size, modified: (attributes?[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0))
            return true
        }
        for root in roots {
            guard let rootParts = GameSaveRules.parts(root.path) else { continue }
            let url = resolve(base: drive, parts: rootParts)
            let prefix = diskSpelling(of: url, below: drive)
            if root.isFile {
                guard let a = try? fm.attributesOfItem(atPath: url.path), (a[.type] as? FileAttributeType) == .typeRegular else { continue }
                if !add(prefix, url, a) { return (files, true, leftOut) }
                continue
            }
            guard let walk = fm.enumerator(atPath: url.path) else { continue }
            while let rel = walk.nextObject() as? String {
                let a = walk.fileAttributes
                let type = a?[.type] as? FileAttributeType
                if type == .typeDirectory {
                    if GameSaveRules.skippedFolders.contains((rel as NSString).lastPathComponent.lowercased()) { walk.skipDescendants() }
                    continue
                }
                guard type == .typeRegular else { continue }   // links are not followed
                let name = (rel as NSString).lastPathComponent
                if GameSaveRules.noiseExtensions.contains((name as NSString).pathExtension.lowercased()) { continue }
                if !add(prefix + "/" + rel, url, a) { return (files, true, leftOut) }
            }
        }
        return (files, false, leftOut)
    }

    // MARK: Backups

    static func list(title: String, in root: URL = GameSaveBackups.folder, timeZone: TimeZone = .current) -> [GameSaveBackupEntry] {
        let dir = folder(for: title, in: root)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        var out: [GameSaveBackupEntry] = []
        for name in names {
            guard let parsed = GameSaveRules.parseBackupName(name, timeZone: timeZone) else { continue }
            let url = dir.appendingPathComponent(name)
            let bytes = ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value ?? 0
            out.append(GameSaveBackupEntry(url: url, name: name, date: parsed.date, kind: parsed.kind, bytes: bytes))
        }
        return out.sorted { $0.date > $1.date }
    }

    /// Writes a backup of everything under `roots`. Throws when there is nothing to back up, when it would be too big, or
    /// when the zip cannot be written; a partial file is never left behind.
    static func backUp(title: String, roots: [GameSaveRoot], drive: URL, kind: GameSaveKind, now: Date = Date(),
                       into root: URL = GameSaveBackups.folder, timeZone: TimeZone = .current, maxFiles: Int = GameSaveRules.maxBackupFiles) throws -> GameSaveBackupResult {
        let collected = collect(roots: roots, drive: drive, maxFiles: maxFiles)
        guard !collected.tooBig else {
            throw GameSaveError.message("The saves of this game are bigger than \(GameSaveRules.maxBackupBytes >> 20) MB or hold more than \(GameSaveRules.maxBackupFiles) files, more than BCD backs up on its own.")
        }
        guard !collected.files.isEmpty else { throw GameSaveError.message("There are no save files to back up yet.") }
        let fm = FileManager.default
        let dir = folder(for: title, in: root)
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let partial = dir.appendingPathComponent(".partial-\(UUID().uuidString).zip")
        defer { try? fm.removeItem(at: partial) }
        let zip = try SaveZipWriter(url: partial)
        var count = 0
        var bytes: Int64 = 0
        for file in collected.files {
            guard let parts = GameSaveRules.parts(file.path) else { continue }
            let source = resolve(base: drive, parts: parts)
            guard let data = try? Data(contentsOf: source) else { continue }   // a file that went away meanwhile
            try zip.add(name: file.path, data: data)
            count += 1
            bytes += Int64(data.count)
        }
        guard count > 0 else { throw GameSaveError.message("The save files could not be read.") }
        let manifest: [String: Any] = [
            "game": title, "kind": kind.rawValue, "made": GameSaveRules.isoUTC(now),
            "roots": roots.map { ["path": $0.path, "file": $0.isFile] },
        ]
        if let json = try? JSONSerialization.data(withJSONObject: manifest, options: [.prettyPrinted, .sortedKeys]) {
            try zip.add(name: manifestName, data: json)
        }
        try zip.finish()
        // The name is chosen last and the file is moved, never copied over: a name taken meanwhile gets a number.
        var name = GameSaveRules.backupName(date: now, kind: kind, taken: Set((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []), timeZone: timeZone)
        var final = dir.appendingPathComponent(name)
        var attempt = 0
        while true {
            do { try fm.moveItem(at: partial, to: final); break }
            catch {
                attempt += 1
                guard fm.fileExists(atPath: final.path), attempt < 20 else { throw error }
                name = GameSaveRules.backupName(date: now.addingTimeInterval(Double(attempt)), kind: kind, taken: Set((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []), timeZone: timeZone)
                final = dir.appendingPathComponent(name)
            }
        }
        log("[saves] backup (\(kind.rawValue.lowercased())): \(count) files, \(bytes) bytes\(collected.leftOut > 0 ? ", \(collected.leftOut) too big left out" : "")")
        return GameSaveBackupResult(url: final, files: count, bytes: bytes)
    }

    static let manifestName = "bcd-saves.json"

    /// The roots a backup says it holds (its manifest), for the "before restore" copy and for a game BCD has no record of.
    static func roots(ofBackup url: URL) -> [GameSaveRoot] {
        guard let zip = try? SaveZipReader(url: url), let entry = zip.entries.first(where: { $0.name == manifestName }),
              let data = try? zip.extract(entry), let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = object["roots"] as? [[String: Any]] else { return [] }
        return list.compactMap { item in
            guard let path = item["path"] as? String, GameSaveRules.safeParts(path) != nil else { return nil }
            return GameSaveRoot(path: path, isFile: item["file"] as? Bool ?? false, source: .played)
        }
    }

    /// Puts a backup's files back under drive_c, replacing the files of the same names. Entries that are not plain relative
    /// paths are skipped. Returns how many files were written and how many entries were refused.
    static func restore(_ url: URL, drive: URL) throws -> (written: Int, refused: Int) {
        let zip = try SaveZipReader(url: url)
        let fm = FileManager.default
        var written = 0, refused = 0
        for entry in zip.entries where !entry.name.hasSuffix("/") && entry.name != manifestName {
            guard let parts = GameSaveRules.safeParts(entry.name) else { refused += 1; continue }
            let destination = resolve(base: drive, parts: parts)
            try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            try zip.extract(entry).write(to: destination, options: .atomic)
            written += 1
        }
        guard written > 0 else { throw GameSaveError.message("This backup holds no save files.") }
        log("[saves] restored \(written) files\(refused > 0 ? ", \(refused) refused" : "")")
        return (written, refused)
    }

    /// The automatic rotation: removes the automatic backups beyond the `keep` newest. Returns how many went.
    @discardableResult
    static func rotate(title: String, keep: Int, in root: URL = GameSaveBackups.folder, timeZone: TimeZone = .current) -> Int {
        let backups = list(title: title, in: root, timeZone: timeZone)
        let names = GameSaveRules.toRemove(backups.map { ($0.name, $0.date, $0.kind) }, keep: keep)
        var removed = 0
        for entry in backups where names.contains(entry.name) && entry.kind == .automatic {
            if (try? FileManager.default.removeItem(at: entry.url)) != nil { removed += 1 }
        }
        if removed > 0 { log("[saves] \(removed) older automatic backup\(removed == 1 ? "" : "s") replaced (the newest \(keep) are kept)") }
        return removed
    }
}
