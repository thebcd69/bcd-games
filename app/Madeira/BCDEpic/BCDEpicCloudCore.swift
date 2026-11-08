// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 Tabîrcă Nicolae-Eduard: code taken from https://github.com/willfaust/Madeira (pull request #443)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// Epic's cloud saves in BCD, the parts that need no app (build 632; docs/EPIC.md "Cloud saves"): the save service over BCD's own Epic
// session, what is remembered between runs, where a game says its saves are, and what a page shows of a sync. The formats and the sync
// decision are upstream Madeira's EpicFormats.swift and EpicCloudSaves.swift (Legendary-derived); the app side is BCDEpicCloud.swift.
// Foundation only: the host checks compile it.
//
// A cloud save is a snapshot (a manifest and the 1 MiB chunks it names, under the account's savesync folder for the game). A newer upload
// is a new snapshot and the older ones stay: nothing is ever overwritten or deleted in Epic's cloud.

/// The save service (`datastorage-public-service-liveegs`) through BCD's session: the token goes only to Epic's hosts and is refreshed once
/// when refused (BCDEpicAPI.send); the pre-signed links the service hands out are fetched and written without it, in this transport's
/// own session (no cookies, no cache, no redirect), with the launcher's long user agent that the service expects.
struct BCDEpicCloudHTTP: EpicCloudTransport {
    static let host = "datastorage-public-service-liveegs.live.use1a.on.epicgames.com"
    static let userAgent = "UELauncher/11.0.1-14907503+++Portal+Release-Live Windows/10.0.19041.1.256.64bit"
    /// The most one downloaded save file or chunk may hold.
    static let sizeLimit = 128 * 1024 * 1024

    let api: BCDEpicAPI
    let accountID: String

    init(api: BCDEpicAPI, accountID: String) {
        self.api = api
        self.accountID = accountID
    }

    /// The service's folder for one game of the account. nil for a name or an account that is not one.
    static func savesyncURL(account: String, app: String) -> URL? {
        guard BCDEpicCatalog.validAppName(app), account.utf8.count <= 64, account.utf8.allSatisfy({ ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x61 && $0 <= 0x7A) || ($0 >= 0x41 && $0 <= 0x5A) }) else { return nil }
        return URL(string: "https://\(host)/api/v1/access/egstore/savesync/\(account)/\(app)/")
    }

    private func request(_ url: URL, method: String, json: Any? = nil) throws -> URLRequest {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = 30
        request.setValue(Self.userAgent, forHTTPHeaderField: "User-Agent")
        if let json {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
        }
        return request
    }

    private func decode(_ data: Data) throws -> [EpicCloudObject] {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let files = json["files"] as? [String: Any] else {
            throw EpicContentError.invalid("Unexpected answer from Epic's save service.")
        }
        return files.keys.sorted().map { key in
            let entry = files[key] as? [String: Any]
            return EpicCloudObject(path: key, readLink: entry?["readLink"] as? String, writeLink: entry?["writeLink"] as? String)
        }
    }

    func list(app: String) async throws -> [EpicCloudObject] {
        guard let url = Self.savesyncURL(account: accountID, app: app) else { throw EpicContentError.invalid("Invalid Epic save URL.") }
        return try decode(try await api.send(try request(url, method: "GET")).data)
    }

    func writeTargets(app: String, paths: [String]) async throws -> [EpicCloudObject] {
        guard let url = Self.savesyncURL(account: accountID, app: app) else { throw EpicContentError.invalid("Invalid Epic save URL.") }
        return try decode(try await api.send(try request(url, method: "POST", json: ["files": paths])).data)
    }

    /// A pre-signed link: https, no user name, and no token goes with it.
    private static func signed(_ text: String?) -> URL? {
        guard let text, let url = URL(string: text), url.scheme == "https", url.user == nil, url.host != nil else { return nil }
        return url
    }

    func read(_ object: EpicCloudObject) async throws -> Data {
        guard let url = Self.signed(object.readLink) else { throw EpicContentError.invalid("Epic gave no download link for a save file.") }
        let (status, body) = try await api.transport.send(try request(url, method: "GET"))
        guard status == 200 else { throw EpicContentError.httpStatus(status) }
        guard body.count <= Self.sizeLimit else { throw EpicContentError.invalid("The cloud save download is too large.") }
        return body
    }

    func write(_ object: EpicCloudObject, data: Data) async throws {
        guard let url = Self.signed(object.writeLink) else { throw EpicContentError.invalid("Epic gave no upload link for a save file.") }
        var put = try request(url, method: "PUT")
        put.timeoutInterval = 60
        put.httpBody = data
        let (status, _) = try await api.transport.send(put)
        guard (200..<300).contains(status) else { throw EpicContentError.httpStatus(status) }
    }
}

// MARK: Where a game's saves are

enum BCDEpicCloudRules {
    /// The game's save rule from its catalog entry's `customAttributes` (CloudSaveFolder, CloudIncludeList, CloudExcludeList), out of the
    /// bulk reply the catalog service gave for `catalogItemId` (a dictionary by item id, or lists of entries). nil: no cloud saves.
    static func saveConfig(fromBulk object: [String: Any], catalogItemId: String) -> EpicSaveConfig? {
        var entry = object[catalogItemId] as? [String: Any]
        if entry == nil {
            for value in object.values {
                if let list = value as? [[String: Any]], let found = list.first(where: { ($0["id"] as? String) == catalogItemId }) { entry = found; break }
            }
        }
        guard let entry else { return nil }
        return EpicSaveConfig.from(customAttributes: entry["customAttributes"] as? [String: Any])
    }

    /// Where replaced saves are copied first (Files > BCD > Epic Cloud Backups > <game> (<app>) > <yyyy.MM.dd-HH.mm.ss>).
    static func backupFolder(title: String, app: String, root: URL, now: Date = Date()) -> URL {
        let bad = Set("/\\:*?\"<>|")
        let safe = String(title.map { bad.contains($0) ? "-" : $0 }.prefix(80)).trimmingCharacters(in: .whitespacesAndNewlines.union(CharacterSet(charactersIn: ".")))
        let stamp = EpicBuildTime.format(now)   // 2026.11.05-14.20.01, UTC (no ICU date formatter anywhere in BCD)
        return root.appendingPathComponent(safe.isEmpty ? app : "\(safe) (\(app))", isDirectory: true).appendingPathComponent(stamp, isDirectory: true)
    }
}

// MARK: What is remembered

/// What BCD keeps for one game between runs: its save rule (so the catalog is read once), the account it was read for (a hash, never the
/// ID), and the state both sides agreed on at the last sync. No token, no name.
struct BCDEpicCloudRecord: Codable, Equatable {
    var account: String?
    var config: EpicSaveConfig?
    var noCloud = false
    var baseline: EpicSaveBaseline?
}

enum BCDEpicCloudStore {
    static let maxBytes = 4 << 20

    static func load(_ url: URL) -> [String: BCDEpicCloudRecord] {
        guard let data = try? Data(contentsOf: url), data.count <= maxBytes,
              let records = try? JSONDecoder().decode([String: BCDEpicCloudRecord].self, from: data) else { return [:] }
        return records.filter { BCDEpicCatalog.validAppName($0.key) }
    }

    static func save(_ records: [String: BCDEpicCloudRecord], to url: URL) {
        guard let data = try? JSONEncoder().encode(records) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }
}

// MARK: What a page shows

/// A game's sync as the page tells it.
struct BCDEpicCloudState: Equatable {
    enum Phase: Equatable {
        case idle, checking, downloading, uploading, ready
        /// The game has no cloud saves (its catalog entry names no save folder).
        case unsupported
        case failed(String)
    }

    /// One save the two sides disagree on (a path, with what each side holds).
    struct Difference: Equatable {
        var name: String
        var cloudBytes: UInt64
        var cloudDate: Date?
        /// nil: not on this device.
        var deviceBytes: UInt64?
        var deviceDate: Date?
    }

    var phase = Phase.idle
    var verdict: EpicSaveVerdict?
    var deviceFiles = 0, cloudFiles = 0
    var deviceBytes: UInt64 = 0, cloudBytes: UInt64 = 0
    var deviceDate: Date?, cloudDate: Date?
    /// The saves that differ, listed when both sides changed.
    var differences: [Difference] = []
    /// The last sync's work: files received (and replaced ones backed up first), files sent.
    var received: Int?, backedUp = 0, sent: Int?
    var note: String?

    /// The files that differ between the device's scan and the cloud's newest snapshot: a save only one side has, or one whose
    /// content is not the same (by its SHA-1).
    static func differences(device: [EpicDeviceSave], cloud: EpicCloudSnapshot?) -> [Difference] {
        let cloudFiles = cloud?.files ?? [:]
        var out: [Difference] = []
        var names = Set(cloudFiles.keys)
        for save in device { names.insert(save.path) }
        let byName = Dictionary(device.map { ($0.path, $0) }, uniquingKeysWith: { first, _ in first })
        for name in names.sorted(by: { $0.lowercased() < $1.lowercased() }) {
            let mine = byName[name], theirs = cloudFiles[name]
            if let mine, let theirs, mine.sha == theirs { continue }
            out.append(Difference(name: name, cloudBytes: cloud?.sizes[name] ?? 0, cloudDate: cloud?.date,
                                  deviceBytes: mine?.size, deviceDate: mine?.modified))
        }
        return out
    }
}
