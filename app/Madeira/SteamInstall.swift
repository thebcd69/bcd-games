// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation

// Where Madeira's own Steam downloads live, and how one is read and removed
// (docs/STEAM_LIBRARY.md). Foundation only, so tests/host/check-steam-library.py
// compiles this file as it is.
//
// A download goes into Madeira Dock's own Steam library folder,
// C:\Program Files (x86)\Steam\steamapps, as `common/<installdir>` plus Steam's
// `appmanifest_<appid>.acf` install record. That is exactly the layout
// Madeira Dock's discovery (MadeiraDock.games) reads and Valve's client, which
// Dock starts the game through, understands: a game is "installed" for Dock
// once its record says so, and the record is written last.

enum SteamInstallPaths {
    /// The library folder, relative to drive_c (the value `DockGame.library` has).
    static let libraryRelative = "Program Files (x86)/Steam/steamapps"

    static func steamApps(drive: URL) -> URL { drive.appendingPathComponent(libraryRelative, isDirectory: true) }
    static func common(drive: URL) -> URL { steamApps(drive: drive).appendingPathComponent("common", isDirectory: true) }

    /// Whether an install (its drive-relative library folder) is in the library
    /// Madeira downloads into, and so can be updated or removed here.
    static func isManaged(library: String) -> Bool {
        library.caseInsensitiveCompare(libraryRelative) == .orderedSame
    }
}

enum SteamInstallFiles {
    /// The `buildid` the install record of an app states, or nil when there is
    /// no readable record.
    static func buildID(appID: Int, steamApps: URL) -> Int? {
        guard let state = record(appID: appID, steamApps: steamApps) else { return nil }
        return state["buildid"]?.string.flatMap { Int($0) }
    }

    /// The language the install record states (`UserConfig.language`), or nil.
    static func language(appID: Int, steamApps: URL) -> String? {
        guard let state = record(appID: appID, steamApps: steamApps),
              let language = state["UserConfig"]?["language"]?.string, !language.isEmpty else { return nil }
        return language.lowercased()
    }

    /// The depots the install record holds: its own (`InstalledDepots`) and
    /// those shared from other apps (`SharedDepots`).
    static func depots(appID: Int, steamApps: URL) -> Set<Int> {
        guard let state = record(appID: appID, steamApps: steamApps) else { return [] }
        let keys = Array((state["InstalledDepots"]?.fields ?? [:]).keys) + Array((state["SharedDepots"]?.fields ?? [:]).keys)
        return Set(keys.prefix(512).compactMap { Int($0) })
    }

    /// The install size the record states (`SizeOnDisk`, bytes), or nil.
    static func sizeOnDisk(appID: Int, steamApps: URL) -> Int64? {
        guard let state = record(appID: appID, steamApps: steamApps),
              let size = state["SizeOnDisk"]?.string.flatMap({ Int64($0) }), size > 0 else { return nil }
        return size
    }

    /// Removes an app's install: its folder under `common`, its record, its
    /// resume journal and the records of the apps that own its shared depots
    /// (those describe the same folder). Only paths strictly inside
    /// `steamApps/common` are removed.
    nonisolated static func delete(appID: Int, folderName: String, steamApps: URL) {
        let fm = FileManager.default
        let common = steamApps.appendingPathComponent("common", isDirectory: true).resolvingSymlinksInPath().standardizedFileURL
        let folder = common.appendingPathComponent(safeFolderName(folderName)).resolvingSymlinksInPath().standardizedFileURL
        if folder.path.hasPrefix(common.path + "/"), folder.deletingLastPathComponent().path == common.path {
            try? fm.removeItem(at: folder)
        }
        let recordURL = steamApps.appendingPathComponent("appmanifest_\(appID).acf")
        if let state = record(appID: appID, steamApps: steamApps) {
            let shared = (state["SharedDepots"]?.fields ?? [:]).sorted(by: { $0.key < $1.key }).prefix(64)
            for (_, owner) in shared {
                guard let ownerID = owner.string.flatMap({ Int($0) }), ownerID > 0, ownerID != appID,
                      let ownerState = record(appID: ownerID, steamApps: steamApps),
                      let dir = ownerState["installdir"]?.string
                else { continue }
                // An owner in another folder (its own installdir) goes too when
                // it holds nothing but depots shared into this app.
                let mine = Set(shared.filter { $0.value.string == owner.string }.map { $0.key })
                let held = Set((ownerState["InstalledDepots"]?.fields ?? [:]).keys)
                guard safeFolderName(dir).caseInsensitiveCompare(safeFolderName(folderName)) == .orderedSame ||
                      held.isSubset(of: mine) else { continue }
                try? fm.removeItem(at: steamApps.appendingPathComponent("appmanifest_\(ownerID).acf"))
            }
        }
        try? fm.removeItem(at: recordURL)
        try? fm.removeItem(at: steamApps.appendingPathComponent("downloading/\(appID)", isDirectory: true))
    }

    /// Validates a manifest path and folds directory spelling to the first
    /// one seen (case-insensitively). Returns nil for anything that could
    /// escape the install folder.
    nonisolated static func safeRelativePath(_ name: String, folded: inout [String: String]) -> String? {
        safeRelativePath(name, folded: &folded, existing: { _ in [] })
    }

    /// madeira-bcd (round 624, Discord forum item 10: Kingdom Come: Deliverance's empty `localization` next to its `Localization`):
    /// the same fold, seeded from the folders already on disk. `existing` lists an install-relative folder ("" is the install folder
    /// itself). A run whose manifest spells a folder differently from the one an earlier run (an update, a DLC, a language depot)
    /// created used to make an empty twin, which a case-sensitive volume keeps and Wine finds first; now a spelling already on disk
    /// wins: the exact name first, then one that differs only in case, as the DLC removal looks names up. `note` hears each folder
    /// whose manifest spelling was replaced, and each pair of twins already on disk.
    nonisolated static func safeRelativePath(_ name: String, folded: inout [String: String], existing: (String) -> [String],
                                             note: (String) -> Void = { _ in }) -> String? {
        let parts = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/", omittingEmptySubsequences: true).map(String.init)
        guard !parts.isEmpty, parts.count <= 64, name.utf8.count < 1024,
              !parts.contains(where: { $0 == "." || $0 == ".." || $0.contains(":") ||
                  $0.unicodeScalars.contains(where: { $0.value < 0x20 }) }) else { return nil }
        var built: [String] = []
        for (index, part) in parts.enumerated() {
            if index == parts.count - 1 { built.append(part); break }
            let key = (built + [part]).joined(separator: "/").lowercased()
            if let known = folded[key] {
                built = known.split(separator: "/").map(String.init)
            } else {
                let parent = built.joined(separator: "/")
                let list = existing(parent)
                let twins = list.filter { $0.caseInsensitiveCompare(part) == .orderedSame }
                let onDisk = twins.first(where: { $0 == part }) ?? twins.first
                if twins.count > 1 {
                    note("case twins on disk in \(parent.isEmpty ? "the install folder" : parent): \(twins.sorted().joined(separator: ", "))")
                }
                if let onDisk, onDisk != part {
                    note("the manifest spells \((built + [part]).joined(separator: "/")), the disk has \(onDisk): kept")
                }
                built.append(onDisk ?? part)
                folded[key] = built.joined(separator: "/")
            }
        }
        return built.joined(separator: "/")
    }

    /// Install folders come from app metadata; keep them to one safe component.
    nonisolated static func safeFolderName(_ name: String) -> String {
        let cleaned = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? ""
        guard !cleaned.isEmpty, cleaned != ".", cleaned != "..", !cleaned.contains(":"),
              !cleaned.unicodeScalars.contains(where: { $0.value < 0x20 }) else { return "app" }
        return cleaned
    }

    /// The `AppState` block of `appmanifest_<appid>.acf` when the file is
    /// readable, bounded, and names this app.
    private static func record(appID: Int, steamApps: URL) -> SteamValue? {
        let url = steamApps.appendingPathComponent("appmanifest_\(appID).acf")
        guard let data = try? Data(contentsOf: url), data.count <= 1 << 20,
              var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let state = root["AppState"], state["appid"]?.string == String(appID) else { return nil }
        return state
    }
}

/// madeira-bcd (2026-10-21): Steamworks Common Redistributables (app 228980) and a game's Dock start.
///
/// Many games list Valve's redistributable installers (DirectX, VC++) as `sharedinstall` depots of app
/// 228980. BCD's downloader never installs them (Wine needs none of them), so 228980 is never "Fully
/// Installed" here. While Counter-Strike 2 ran, Valve's client added the game's dependency on it
/// (content_log.txt: "Dependency added: parent 228980, child 730, depot 228988"), wrote `SharedDepots`
/// entries naming 228980 into the game's record and marked 228980 "Update Required"; from then on every
/// start ended with "Failed running app 730 (required app 228980 not ready)" (Dock launch error 17, the
/// "Steam is installing content" wait), since Valve's client downloads nothing in a Dock session. The
/// record without those entries, as BCD's own download writes it, started the game (owner's iPhone,
/// 2026-10-21 10:33, log AJJ4RC; the next start, with the entries back, waited: SK27KV).
///
/// Before a Dock start, the entries naming 228980 come out of the game's record, and only while
/// 228980's own record is missing or not fully installed: the one state in which the start cannot work
/// today. Everything else in the file stays byte for byte. `env.MADEIRA_DOCK_SHARED_REDIST = 0` turns
/// it off. Foundation only (tests/host/check-steam-games.py).
enum SteamSharedRedist {
    static let ownerAppID = 228980

    /// The record's text with the `SharedDepots` entries that name the owner removed (the whole block
    /// when none is left), and the depots taken out; nil when it names none, or when anything about the
    /// text is not the plain shape Valve's client writes (then the file is left alone).
    static func withoutOwner(_ text: String, appID: Int) -> (text: String, depots: [Int])? {
        var lines = text.components(separatedBy: "\n")
        func bare(_ line: String) -> String { line.trimmingCharacters(in: .whitespacesAndNewlines) }
        var depth = 0, key: Int?
        for (index, line) in lines.enumerated() {
            let t = bare(line)
            if t == "{" { depth += 1; continue }
            if t == "}" { depth -= 1; if depth < 0 { return nil }; continue }
            if depth == 1, t.caseInsensitiveCompare("\"SharedDepots\"") == .orderedSame {
                guard key == nil else { return nil }   // one block, as Valve writes it
                key = index
            }
        }
        guard depth == 0, let start = key, start + 2 < lines.count, bare(lines[start + 1]) == "{" else { return nil }
        var end: Int?
        var drop: [Int] = [], depots: [Int] = [], kept = 0
        for index in (start + 2)..<lines.count {
            let t = bare(lines[index])
            if t == "}" { end = index; break }
            let parts = t.split(separator: "\"", omittingEmptySubsequences: false)
            // "<depot>"<tabs>"<owner>" splits into ["", depot, tabs, owner, ""].
            guard parts.count == 5, parts[0].isEmpty, parts[4].isEmpty, parts[2].allSatisfy({ $0 == " " || $0 == "\t" }),
                  !parts[2].isEmpty, let depot = Int(parts[1]), let owner = Int(parts[3]), depot > 0, owner > 0 else { return nil }
            if owner == ownerAppID { drop.append(index); depots.append(depot) } else { kept += 1 }
        }
        guard let close = end, !drop.isEmpty else { return nil }
        if kept == 0 { lines.removeSubrange(start...close) } else { for index in drop.reversed() { lines.remove(at: index) } }
        let edited = lines.joined(separator: "\n")
        // The edit must leave the same record: same app, state, build, folder and installed depots,
        // and no entry naming the owner.
        func state(_ s: String) -> SteamValue? {
            guard var parser = try? SteamKeyValues(Data(s.utf8)), let root = try? parser.read(),
                  let st = root["AppState"], st["appid"]?.string == String(appID) else { return nil }
            return st
        }
        guard let before = state(text), let after = state(edited) else { return nil }
        for name in ["StateFlags", "buildid", "installdir", "name"] where before[name]?.string != after[name]?.string { return nil }
        let installed = { (v: SteamValue) in v["InstalledDepots"]?.fields.keys.sorted() ?? [] }
        guard installed(before) == installed(after), Set(before.fields.keys) == Set(after.fields.keys).union(kept == 0 ? ["shareddepots"] : []),
              !(after["SharedDepots"]?.fields.values.contains { $0.string == String(ownerAppID) } ?? false) else { return nil }
        return (edited, depots.sorted())
    }

    /// 228980's own `StateFlags`, or nil when it has no readable record.
    static func ownerFlags(steamApps: URL) -> Int? {
        let url = steamApps.appendingPathComponent("appmanifest_\(ownerAppID).acf")
        guard let data = try? Data(contentsOf: url), data.count <= 1 << 20,
              var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let state = root["AppState"], state["appid"]?.string == String(ownerAppID) else { return nil }
        return state["StateFlags"]?.string.flatMap { Int($0) }
    }

    /// Before a Dock start of `appID`: takes the entries out when 228980 is not fully installed.
    /// Returns the log line when the record changed, nil otherwise.
    nonisolated static func prepare(appID: Int, steamApps: URL) -> String? {
        let flags = ownerFlags(steamApps: steamApps)
        if let flags, flags & 4 != 0 { return nil }
        let url = steamApps.appendingPathComponent("appmanifest_\(appID).acf")
        guard let data = try? Data(contentsOf: url), data.count <= 1 << 20,
              let text = String(data: data, encoding: .utf8),
              let result = withoutOwner(text, appID: appID) else { return nil }
        do {
            try Data(result.text.utf8).write(to: url, options: .atomic)
        } catch {
            return "[dock-shared-redist] app=\(appID) could not rewrite its record (\(type(of: error))); left as it was"
        }
        return "[dock-shared-redist] app=\(appID) owner=\(ownerAppID) owner-state=\(flags.map(String.init) ?? "missing") " +
            "removed depots=\(result.depots.map(String.init).joined(separator: ","))"
    }
}
