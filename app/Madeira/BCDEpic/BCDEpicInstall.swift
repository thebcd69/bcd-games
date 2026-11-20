// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// An installed Epic game (docs/research/open-fork-2026-10-18/epic.md §4.4, §4.8): its record, its folder
// under Epic's own default root (C:\Program Files\Epic Games\<game>), the manifest kept beside the record
// (for the verify, update and repair of later milestones), and the library entry's launch fields. Play
// is the library's normal start of a game added from drive_c, with Epic's public arguments in the entry
// (BCDEpicLaunch): no launcher, no secret. Foundation only.

/// One installed game. Paths are drive-relative (a root of a drive letter and a folder), so a later
/// D:\Epic Games needs no migration.
struct BCDEpicInstallRecord: Codable, Equatable {
    var version = 1
    var app: String
    var namespace: String
    var catalogItemId: String
    var title: String
    var buildVersion: String
    /// SHA-1 of the kept manifest file.
    var manifestHash: String
    var featureLevel: Int
    var root: BCDInstallRoot
    var folder: String
    var bytes: Int64
    /// The program, relative to the game's folder ('/' between folders, spelled as on the disk).
    var launchPath: String
    var launchCommand: String
    var extraCommand: String
    var deploymentID: String?
    var canRunOffline: Bool?
    var eos: Bool
    var prerequisiteNote: String?
    /// Milestone 2: the catalog says the game asks Epic for an ownership token each time it starts; the anti-cheat exception it falls
    /// under (its id), whose option BCD starts it with. Both optional: a record of an earlier version has neither.
    var ownershipToken: Bool?
    var exceptionID: String?
    /// The arguments BCD last wrote into the library entry: an entry that still holds them is refreshed at
    /// Play; one the player edited is left alone.
    var arguments: String
    /// The library entry Play starts.
    var entryID: UUID?
    var installed: Date
    /// The pictures Epic's catalog gave (https addresses): kept so the game keeps its cover when the list is gone
    /// (signed out), and fetched once into the art folder (BCDEpic.artFile).
    var tall: String?
    var wide: String?

    var windowsFolder: String { root.windowsPath(folder) }
    var hostFolder: URL? { root.url?.appendingPathComponent(folder, isDirectory: true) }

    /// The anti-cheat exception this game falls under, if any.
    var exception: BCDEpicRules.AntiCheatException? { exceptionID.flatMap { id in BCDEpicRules.antiCheatExceptions.first { $0.id == id } } }

    /// One line about what may keep the game from starting, for its page (nil: nothing to say). `signedStart`: BCD starts games signed in to Epic.
    func note(signedStart: Bool = true) -> String? {
        if let exception { return exception.reason }
        if ownershipToken == true { return signedStart ? BCDEpicRules.tokenNote : BCDEpicRules.offlineNote }
        if canRunOffline == false { return signedStart ? BCDEpicRules.onlineNote : BCDEpicRules.offlineNote }
        if eos { return BCDEpicRules.eosNote(signedStart: signedStart) }
        return prerequisiteNote
    }
}

enum BCDEpicInstall {
    /// Where Epic games install: Epic's own default root, C:\Program Files\Epic Games (the owner's answer,
    /// 2026-10-22). The only place that says so: the external drive's merge changes this and nothing else
    /// (the records keep a drive and a folder, the engine takes a root per job).
    static let root = BCDInstallRoot(drive: "C", folder: "Program Files/Epic Games")

    /// 2026-11-20: Epic's folder on an external drive (Settings › Storage › External drives), reached through the drive's
    /// C:\External\<name> link, so the record, the library entry and the engine take it as any root of drive C. The default
    /// stays `root`; a game goes here only when the player chooses External drive on its page.
    static func driveRoot(_ name: String) -> BCDInstallRoot { BCDInstallRoot(drive: "C", folder: "External/\(name)/Epic Games") }

    /// The drive's name of a root on an external drive, nil for the device's.
    static func driveName(_ root: BCDInstallRoot) -> String? {
        let parts = root.folder.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard root.drive == "C", parts.count == 3, parts[0] == "External", parts[2] == "Epic Games",
              BCDContentPaths.folderName(parts[1]) == parts[1] else { return nil }
        return parts[1]
    }

    /// A root a record may hold: the device's, or Epic's folder on a drive (a record naming another folder is not loaded, so an
    /// uninstall can only ever remove a folder under one of these).
    static func validRoot(_ root: BCDInstallRoot) -> Bool { root == Self.root || driveName(root) != nil }

    /// The install folder's name: the catalog's own, then "<name> (Epic)", "(Epic 2)", ... when a folder of
    /// that name is already there.
    static func folderName(preferred: String, in root: URL) -> String {
        let base = BCDContentPaths.folderName(preferred) ?? "Epic game"
        var candidate = base
        var n = 1
        while FileManager.default.fileExists(atPath: root.appendingPathComponent(candidate).path) {
            candidate = n == 1 ? base + " (Epic)" : base + " (Epic \(n))"
            n += 1
        }
        return candidate
    }

    /// What the library entry holds for a game: the program relative to drive_c and the arguments Play passes.
    static func launch(_ record: BCDEpicInstallRecord, preferredLanguages: [String] = Locale.preferredLanguages)
        -> (relativePath: String, arguments: String, dropped: Int) {
        let made = BCDEpicLaunch.arguments(app: record.app, manifestCommand: record.launchCommand, extraCommand: record.extraCommand,
                                           deploymentID: record.deploymentID, preferredLanguages: preferredLanguages,
                                           exceptionArgument: record.exception?.argument)
        let relative = [record.root.folder, record.folder, record.launchPath].joined(separator: "/")
        return (relative, made.text, made.dropped)
    }

    /// The downloaded files go into place (one rename into the root, under a free folder name), the
    /// manifest is kept, and the program is checked. Off the main thread.
    static func finish(_ plan: BCDContentPlan, info: BCDEpicPlanInfo, pendingManifest: URL?, stage: URL, root: BCDInstallRoot,
                       base: URL, now: Date = Date()) throws -> BCDEpicInstallRecord {
        guard let rootURL = root.url else { throw BCDContentError.driveUnavailable(root.drive) }
        // The program must be there before anything is moved into place: a folder without one would be left behind with no record.
        guard FileManager.default.fileExists(atPath: stage.appendingPathComponent(info.launchPath).path) else {
            throw BCDContentError.refused("The game was downloaded, but its program (\(info.launchPath.split(separator: "/").last.map(String.init) ?? "")) isn't among its files.")
        }
        let folder = folderName(preferred: plan.folder, in: rootURL)
        let gameFolder = rootURL.appendingPathComponent(folder, isDirectory: true)
        try BCDContentEngine.commit(stage: stage, to: gameFolder)
        if let pendingManifest, FileManager.default.fileExists(atPath: pendingManifest.path) {
            let kept = manifestFile(info.app, base: base)
            try? FileManager.default.createDirectory(at: kept.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: kept)
            try? FileManager.default.moveItem(at: pendingManifest, to: kept)
        }
        var record = BCDEpicInstallRecord(app: info.app, namespace: info.namespace, catalogItemId: info.catalogItemId, title: info.title,
                                          buildVersion: info.buildVersion, manifestHash: info.manifestHash, featureLevel: info.featureLevel,
                                          root: root, folder: folder, bytes: plan.totalBytes, launchPath: info.launchPath,
                                          launchCommand: info.launchCommand, extraCommand: info.extraCommand, deploymentID: info.deploymentID,
                                          canRunOffline: info.canRunOffline, eos: info.eos, prerequisiteNote: info.prerequisiteNote,
                                          ownershipToken: info.ownershipToken, exceptionID: info.exceptionID,
                                          arguments: "", entryID: nil, installed: now)
        record.arguments = launch(record).arguments
        BCDEpicLog.write("[epic-install] installed app=\(BCDEpicLog.app(info.app)) files=\(plan.files.count) bytes=\(plan.totalBytes) eos=\(info.eos ? 1 : 0)")
        return record
    }

    // MARK: Records

    static func recordsFolder(_ base: URL) -> URL { base.appendingPathComponent("installs", isDirectory: true) }
    static func manifestFile(_ app: String, base: URL) -> URL { base.appendingPathComponent("manifests/\(app).manifest") }
    static func pendingInfoFile(_ app: String, base: URL) -> URL { base.appendingPathComponent("pending/\(app).json") }
    static func pendingManifestFile(_ app: String, base: URL) -> URL { base.appendingPathComponent("pending/\(app).manifest") }

    static func loadRecords(_ base: URL) -> [String: BCDEpicInstallRecord] {
        let folder = recordsFolder(base)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        var out: [String: BCDEpicInstallRecord] = [:]
        for name in (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [] where name.hasSuffix(".json") {
            guard let data = try? Data(contentsOf: folder.appendingPathComponent(name)),
                  let record = try? decoder.decode(BCDEpicInstallRecord.self, from: data),
                  name == record.app + ".json", BCDEpicCatalog.validAppName(record.app),
                  BCDContentPaths.folderName(record.folder) == record.folder, validRoot(record.root) else { continue }
            out[record.app] = record
        }
        return out
    }

    static func save(_ record: BCDEpicInstallRecord, base: URL) throws {
        let folder = recordsFolder(base)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.dateEncodingStrategy = .secondsSince1970
        try encoder.encode(record).write(to: folder.appendingPathComponent(record.app + ".json"), options: .atomic)
    }

    static func delete(_ app: String, base: URL) {
        try? FileManager.default.removeItem(at: recordsFolder(base).appendingPathComponent(app + ".json"))
        try? FileManager.default.removeItem(at: manifestFile(app, base: base))
    }
}

// MARK: - Games BCD refused

/// The games a build's manifest showed BCD cannot install (anti-cheat, no program, a format BCD does not read), kept so the game's page says
/// why instead of offering Install again at once. Each is forgotten after three days (Epic may have changed the build) and by another
/// version of BCD (which may read what this one could not). Foundation only.
struct BCDEpicRefusals: Codable, Equatable {
    struct Entry: Codable, Equatable {
        var code: String
        var message: String
        var when: Date
        /// The app's build number when it was noted.
        var build: String?
    }

    static let lifetime: TimeInterval = 3 * 86_400
    var entries: [String: Entry] = [:]

    func refusal(_ app: String, now: Date, build: String) -> BCDEpicRules.Refusal? {
        guard let entry = entries[app], entry.build == build, now.timeIntervalSince(entry.when) < Self.lifetime else { return nil }
        return BCDEpicRules.Refusal(code: entry.code, message: entry.message)
    }

    mutating func record(_ refusal: BCDEpicRules.Refusal, app: String, now: Date, build: String) {
        entries[app] = Entry(code: refusal.code, message: refusal.message, when: now, build: build)
    }

    mutating func forget(_ app: String) { entries[app] = nil }

    static func load(_ url: URL) -> BCDEpicRefusals {
        guard let data = try? Data(contentsOf: url) else { return BCDEpicRefusals() }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return (try? decoder.decode(BCDEpicRefusals.self, from: data)) ?? BCDEpicRefusals()
    }

    func save(_ url: URL) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? encoder.encode(self).write(to: url, options: .atomic)
    }
}
