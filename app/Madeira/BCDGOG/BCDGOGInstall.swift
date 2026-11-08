// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// An installed GOG game: its record, its folder under C:\GOG Games, and the program its build
// names in goggame-<id>.info (gog.md §2.4.1), which becomes an ordinary library entry, so Play is
// the library's normal start of a game added from drive_c. Foundation only.

/// What the game's build says to start: the primary file task, else the first one of category
/// "game". Paths are relative to the game's folder ('/' between folders, as on the disk).
struct BCDGOGPlayTask: Codable, Equatable {
    var path: String
    var arguments: String
    var workingDirectory: String
    var category: String
}

/// One installed game. Paths are drive-relative (a root of a drive letter and a folder), so a
/// later D:\GOG Games needs no migration.
struct BCDGOGInstallRecord: Codable, Equatable {
    var version = 1
    var productID: String
    var title: String
    var buildID: String
    var versionName: String
    var language: String
    var root: BCDInstallRoot
    var folder: String
    var bytes: Int64
    var depots: [String]
    /// Redistributables the build names (VC++, OpenAL, ...): shown, not installed yet (M2).
    var dependencies: [String]
    var supportFiles: Int
    var play: BCDGOGPlayTask?
    /// The library entry Play starts.
    var entryID: UUID?
    var installed: Date
    /// Installed (or repaired) by BCD with the install script in mind: the game's `goggame-<id>.script` registry values are written
    /// before each start (BCDGOGRegistry; env.BCD_GOG_SCRIPT). nil in a record from before: such a game starts exactly as it
    /// always did unless the key asks for the values.
    var scripts: Bool?
    /// Build 632: the redistributables' installers the game's first start runs (GOGSetup; each once per prefix), and the redistributables
    /// left out with the reason (the game's page says so). nil in a record from before: such a game has none of its redistributables
    /// downloaded and runs no installer (Repair adds them).
    var setup: [GOGSetupProgram]?
    var setupNotes: [GOGSetupNote]?
    /// Build 632: the DLC products whose files the game has (`depots` holds their depots). nil in a record from before: such a game
    /// has none, and its first update or repair installs the DLC the account owns.
    var dlc: [String]?

    var windowsFolder: String { root.windowsPath(folder) }
    var hostFolder: URL? { root.url?.appendingPathComponent(folder, isDirectory: true) }
}

enum BCDGOGInstall {
    /// The game's play task from its goggame-<id>.info.
    static func playTask(fromInfo data: Data) throws -> BCDGOGPlayTask {
        guard data.count <= 4 << 20, let info = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw BCDGOGError.badReply("info")
        }
        let tasks = (info["playTasks"] as? [[String: Any]] ?? []).filter { ($0["type"] as? String) == "FileTask" }
        guard let task = tasks.first(where: { ($0["isPrimary"] as? Bool) == true })
                ?? tasks.first(where: { ($0["category"] as? String)?.lowercased() == "game" }),
              let raw = task["path"] as? String else { throw BCDGOGError.noPlayTask }
        func relative(_ text: String) throws -> String {
            var text = text.replacingOccurrences(of: "\\", with: "/")
            while text.hasPrefix("./") { text.removeFirst(2) }
            while text.hasSuffix("/") { text.removeLast() }
            return text.isEmpty ? "" : try BCDContentPaths.components(text).joined(separator: "/")
        }
        guard let path = try? relative(raw), !path.isEmpty else { throw BCDGOGError.noPlayTask }
        let arguments = (task["arguments"] as? String ?? "").replacingOccurrences(of: "\0", with: "")
        let working = (try? relative(task["workingDir"] as? String ?? "")) ?? ""
        return BCDGOGPlayTask(path: path, arguments: String(arguments.prefix(2000)), workingDirectory: working,
                              category: (task["category"] as? String ?? "game").lowercased())
    }

    /// The build's own info file in the game's folder, whatever its letter case.
    static func infoFile(in folder: URL, productID: String) -> URL? {
        let wanted = "goggame-\(productID).info"
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return names.first { $0.lowercased() == wanted }.map { folder.appendingPathComponent($0) }
    }

    /// A Windows-cased relative path as the device spells it: the device's files are case-sensitive
    /// and the build's info file may spell a folder differently from its manifest. nil when it is
    /// not there.
    static func onDisk(_ relative: String, in folder: URL, directory: Bool) -> String? {
        guard !relative.isEmpty else { return directory ? "" : nil }
        var spelled: [String] = []
        var at = folder
        let pieces = relative.split(separator: "/").map(String.init)
        for (i, piece) in pieces.enumerated() {
            let names = (try? FileManager.default.contentsOfDirectory(atPath: at.path)) ?? []
            guard let name = names.first(where: { $0 == piece }) ?? names.first(where: { $0.lowercased() == piece.lowercased() }) else {
                return nil
            }
            spelled.append(name)
            at = at.appendingPathComponent(name)
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: at.path, isDirectory: &isDirectory),
                  isDirectory.boolValue == (i < pieces.count - 1 || directory) else { return nil }
        }
        return spelled.joined(separator: "/")
    }

    /// The install folder's name: the build's own, then "<name> (GOG)", "(GOG 2)", ... when a folder
    /// of that name is already there.
    static func folderName(preferred: String, in root: URL) -> String {
        let base = BCDContentPaths.folderName(preferred) ?? "GOG game"
        var candidate = base
        var n = 1
        while FileManager.default.fileExists(atPath: root.appendingPathComponent(candidate).path) {
            candidate = n == 1 ? base + " (GOG)" : base + " (GOG \(n))"
            n += 1
        }
        return candidate
    }

    /// The library entry's launch fields for a play task: the program relative to drive_c, its
    /// arguments as GOG wrote them, the working folder as a Windows path (the game's folder when
    /// the task names none), and the Wine desktop for a launcher (it may exit once the game runs).
    static func launch(_ record: BCDGOGInstallRecord, play: BCDGOGPlayTask, program: String, working: String)
        -> (relativePath: String, arguments: String, workingDirectory: String, launchMode: String?) {
        let relative = [record.root.folder, record.folder, program].joined(separator: "/")
        let folder = working.isEmpty ? record.windowsFolder : record.root.windowsPath(record.folder, working)
        return (relative, play.arguments, folder, play.category == "launcher" ? "desktop" : nil)
    }

    /// The downloaded files go into place (one rename into the root, under a free folder name),
    /// and the build's play task is read and matched to the files on the disk. Off the main thread.
    static func finish(_ plan: BCDContentPlan, info: BCDGOGPlanInfo, stage: URL, root: BCDInstallRoot,
                       now: Date = Date()) throws -> BCDGOGInstallRecord {
        guard let rootURL = root.url else { throw BCDContentError.driveUnavailable(root.drive) }
        let folder = folderName(preferred: plan.folder, in: rootURL)
        let gameFolder = rootURL.appendingPathComponent(folder, isDirectory: true)
        try BCDContentEngine.commit(stage: stage, to: gameFolder)
        var record = BCDGOGInstallRecord(productID: plan.key.id, title: info.title, buildID: info.buildID, versionName: info.version,
                                         language: info.language, root: root, folder: folder, bytes: plan.totalBytes,
                                         depots: info.depots, dependencies: info.dependencies, supportFiles: info.supportFiles,
                                         play: nil, entryID: nil, installed: now, scripts: true,
                                         setup: info.setup, setupNotes: info.setupNotes, dlc: info.dlc)
        if let file = infoFile(in: gameFolder, productID: plan.key.id), let data = try? Data(contentsOf: file),
           var play = try? playTask(fromInfo: data), let program = onDisk(play.path, in: gameFolder, directory: false) {
            play.path = program
            play.workingDirectory = onDisk(play.workingDirectory, in: gameFolder, directory: true) ?? ""
            record.play = play
        }
        BCDContentLog.write("[gog-install] installed product=\(plan.key.id) files=\(plan.files.count) bytes=\(plan.totalBytes) " +
                            "play=\(record.play == nil ? 0 : 1) redist=\(info.dependencies.count)")
        return record
    }

    // MARK: Updates and repairs

    /// An update or a repair is done: the downloaded files in `stage` go into the game's folder (BCDGOGUpdate.merge: a rename each, the
    /// files the new build no longer has removed), and the record takes the new build: its id and version, depots, DLC, size,
    /// redistributables and the script mark (the game is now installed with them in mind). The play task is read again from the build's
    /// own info file; the one the record had stays when the new build names none that is on the disk. Off the main thread.
    static func finishUpdate(_ plan: BCDContentPlan, info: BCDGOGPlanInfo, stage: URL, record: BCDGOGInstallRecord)
        throws -> (record: BCDGOGInstallRecord, merged: BCDGOGUpdate.MergeResult) {
        guard let gameFolder = record.hostFolder else { throw BCDContentError.driveUnavailable(record.root.drive) }
        let merged = try BCDGOGUpdate.merge(plan, stage: stage, into: gameFolder, remove: info.removed ?? [])
        var record = record
        record.buildID = info.buildID
        record.versionName = info.version
        record.depots = info.depots
        record.dependencies = info.dependencies
        record.supportFiles = info.supportFiles
        record.bytes = info.installBytes ?? plan.totalBytes
        record.scripts = true
        record.dlc = info.dlc ?? record.dlc
        if info.setup != nil { record.setup = info.setup; record.setupNotes = info.setupNotes }
        if let file = infoFile(in: gameFolder, productID: record.productID), let data = try? Data(contentsOf: file),
           var play = try? playTask(fromInfo: data), let program = onDisk(play.path, in: gameFolder, directory: false) {
            play.path = program
            play.workingDirectory = onDisk(play.workingDirectory, in: gameFolder, directory: true) ?? ""
            record.play = play
        }
        try? FileManager.default.removeItem(at: stage)
        BCDContentLog.write("[gog-install] \(info.kind?.rawValue ?? "update") done product=\(record.productID) replaced=\(merged.replaced) added=\(merged.added) " +
                            "removed=\(merged.removed) dlc=\(record.dlc?.count ?? 0) play=\(record.play == nil ? 0 : 1)")
        return (record, merged)
    }

    // MARK: Redistributables

    /// The line on a game's page about its redistributables (build 632): the installers its first start runs (once for the whole prefix,
    /// so a game that needs the same one finds it done), and what was left out and why. nil: nothing to say.
    static func setupNote(setup: [GOGSetupProgram], notes: [GOGSetupNote]) -> String? {
        func sentence(_ text: String) -> String { text.prefix(1).lowercased() + text.dropFirst() }
        func more(_ count: Int) -> String { count > 6 ? " and \(count - 6) more" : "" }
        var parts: [String] = []
        if !setup.isEmpty {
            let names: String = setup.prefix(6).map { $0.name }.joined(separator: ", ")
            parts.append("Redistributables BCD installs once, at the game's first start: " + names + more(setup.count) + ".")
        }
        let left = notes.filter { !$0.note.isEmpty }
        if !left.isEmpty {
            var items: [String] = []
            for note in left.prefix(6) {
                let name: String = note.name.isEmpty ? note.id : note.name
                items.append(name + " (" + sentence(note.note) + ")")
            }
            parts.append("Left out: " + items.joined(separator: "; ") + more(left.count) + ".")
        }
        return parts.isEmpty ? nil : String(parts.joined(separator: " ").prefix(600))
    }

    // MARK: Records

    static func recordsFolder(_ base: URL) -> URL { base.appendingPathComponent("installs", isDirectory: true) }

    static func loadRecords(_ base: URL) -> [String: BCDGOGInstallRecord] {
        let folder = recordsFolder(base)
        var out: [String: BCDGOGInstallRecord] = [:]
        for name in (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [] where name.hasSuffix(".json") {
            guard let data = try? Data(contentsOf: folder.appendingPathComponent(name)),
                  let record = try? JSONDecoder().decode(BCDGOGInstallRecord.self, from: data),
                  name == record.productID + ".json", BCDGOGCatalog.validID(record.productID),
                  BCDContentPaths.folderName(record.folder) == record.folder else { continue }
            out[record.productID] = record
        }
        return out
    }

    static func save(_ record: BCDGOGInstallRecord, base: URL) throws {
        let folder = recordsFolder(base)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(record).write(to: folder.appendingPathComponent(record.productID + ".json"), options: .atomic)
    }

    static func delete(_ productID: String, base: URL) {
        try? FileManager.default.removeItem(at: recordsFolder(base).appendingPathComponent(productID + ".json"))
    }
}
