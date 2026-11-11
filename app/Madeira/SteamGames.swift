// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Modified by BCD (thebcd69), 2026-10-24: Steam launch option choice for a Dock start (SteamDirectStart.dockOptions, dockLaunch).
// Modified by BCD (thebcd69), 2026-11-05: `SteamGamesModel.branches`, the beta branch each install record names (version selection).
// Portions copyright 2026 wamei: code taken from https://github.com/willfaust/Madeira (pull request #381)
// under GPL-3.0-or-later and modified for BCD in October and November 2026. See THIRD-PARTY-NOTICES.md.
// Portions copyright 2026 Connor Gow: code taken from https://github.com/willfaust/Madeira (pull request #464)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import SwiftUI

// Steam games in the library (docs/LIBRARY.md, "Steam setup", and
// docs/STEAM_LIBRARY.md): the games Steam has installed in the prefix, which is
// Madeira Dock's own discovery (MadeiraDock.games: Steam's
// appmanifest_<appid>.acf records in the client's library and the other C:
// libraries its libraryfolders.vdf lists), and the account's owned games that
// are not installed yet (SteamOwnedLibrary), which can be installed here.
// Play starts an installed game through Madeira Dock's launch path
// (ContentView.startDock), so Valve's own client signs in, checks the licence
// and starts it. No program names are involved: a game is its App ID.
// Log tag: [steam-games] (App IDs and counts only).

// MARK: - Rules (Foundation only; tests/host/check-onboarding.py compiles this part)

enum SteamGamesRules {
    /// One game of the section: installed by Steam, owned by the account, or both.
    struct Item: Identifiable, Equatable {
        let id: Int
        var name: String
        var installed: DockGame?
        var owned: SteamOwnedGame?
    }

    /// What a game's card says about it.
    enum Status: Equatable {
        case notInstalled, partlyInstalled, installed, updateAvailable
        case queued, downloading(Int), paused, failed

        /// The card's state pill, or nil for an installed game: its card shows only
        /// the pills of any library game (32-bit or 64-bit, graphics API, install
        /// size), and a newer build adds "Update" to them.
        var badge: String? {
            switch self {
            case .notInstalled: return "Not installed"
            case .partlyInstalled: return "Partly installed"
            case .installed: return nil
            case .updateAvailable: return "Update"
            case .queued: return "Queued"
            case .downloading(let percent): return "\(percent)% downloaded"
            case .paused: return "Paused"
            case .failed: return "Download error"
            }
        }

        /// Whether the card shows the installed game's library pills.
        var showsFormat: Bool { self == .installed || self == .updateAvailable }
    }

    /// A download's state, as far as the status needs it.
    enum Transfer: Equatable { case queued, active(percent: Int), paused, failed }

    static func status(installed: DockGame?, transfer: Transfer?, updateAvailable: Bool) -> Status {
        if let transfer {
            switch transfer {
            case .queued: return .queued
            case .active(let percent): return .downloading(max(0, min(100, percent)))
            case .paused: return .paused
            case .failed: return .failed
            }
        }
        guard let installed else { return .notInstalled }
        if !installed.installed { return .partlyInstalled }
        return updateAvailable ? .updateAvailable : .installed
    }

    /// The games of both lists by App ID, installed ones first (each group by
    /// name), filtered by the library's search text. A game Steam installed
    /// but the account does not list (or that is listed before the library
    /// loaded) keeps the name of its install record.
    static func items(installed: [DockGame], owned: [SteamOwnedGame], search: String) -> [Item] {
        var byID: [Int: Item] = [:]
        for game in owned { byID[game.id] = Item(id: game.id, name: game.name, installed: nil, owned: game) }
        for game in installed {
            if var item = byID[game.id] { item.installed = game; byID[game.id] = item }
            else { byID[game.id] = Item(id: game.id, name: game.name, installed: game, owned: nil) }
        }
        let text = search.trimmingCharacters(in: .whitespaces)
        let all = byID.values.filter { text.isEmpty || $0.name.localizedCaseInsensitiveContains(text) }
        return all.sorted {
            let a = $0.installed != nil, b = $1.installed != nil
            if a != b { return a }
            let order = $0.name.localizedStandardCompare($1.name)
            return order == .orderedSame ? $0.id < $1.id : order == .orderedAscending
        }
    }

    /// The Steam section's groups, as in the fork's sectioned library: games
    /// being downloaded that Steam has no install record for yet, the games
    /// Steam has installed (both listed under the section's title), and the
    /// account's other games (its "Not installed" group). Each keeps `items`'
    /// order.
    struct Groups: Equatable {
        var downloading: [Item] = []
        var installed: [Item] = []
        var notInstalled: [Item] = []
    }

    static func groups(_ items: [Item], downloading: Set<Int>) -> Groups {
        var groups = Groups()
        for item in items {
            if item.installed != nil { groups.installed.append(item) }
            else if downloading.contains(item.id) { groups.downloading.append(item) }
            else { groups.notInstalled.append(item) }
        }
        return groups
    }

    /// What a game's library entry records, for the library's Sort by menu:
    /// when Madeira last started it, its size, and its place in the library.
    struct Recorded: Equatable {
        var lastPlayed: Date?
        var bytes: Int64?
        var position: Int
    }

    /// Installed games in the library's Sort by order ("played", "name",
    /// "added" or "size"), compared as the library compares the games you
    /// added. A game without a library entry yet (never opened, or installed
    /// by Steam's client) sorts as never played, of unknown size and, for
    /// "added", after the games that have one; ties go by name.
    static func sorted(_ items: [Item], by sort: String, recorded: [Int: Recorded]) -> [Item] {
        func byName(_ a: Item, _ b: Item) -> Bool {
            let order = a.name.localizedStandardCompare(b.name)
            return order == .orderedSame ? a.id < b.id : order == .orderedAscending
        }
        return items.sorted { a, b in
            let ra = recorded[a.id], rb = recorded[b.id]
            switch sort {
            case "added":
                if ra?.position != rb?.position { return (ra?.position ?? -1) > (rb?.position ?? -1) }
            case "played":
                if ra?.lastPlayed != rb?.lastPlayed { return (ra?.lastPlayed ?? .distantPast) > (rb?.lastPlayed ?? .distantPast) }
            case "size":
                if ra?.bytes != rb?.bytes { return (ra?.bytes ?? -1) > (rb?.bytes ?? -1) }
            default:
                break
            }
            return byName(a, b)
        }
    }

    /// Whether the library shows the Steam section: Dock is available, and there is
    /// a game to show, a sign-in whose library is on its way, or (with the owned
    /// library on) a signed-out account the section invites to sign in.
    static func showsSection(dock: Bool, library: Bool = false, signedIn: Bool, count: Int) -> Bool {
        dock && (count > 0 || signedIn || library)
    }

    /// Whether the section shows its "Sign in to Steam" card instead of the account's games.
    static func showsSignIn(library: Bool, signedIn: Bool) -> Bool { library && !signedIn }

    /// Why Play is not offered yet, or nil when Dock can be asked to start the game.
    /// Valve's client still decides at launch.
    static func blocker(installed: Bool, client: Bool, signedIn: Bool, updating: Bool = false) -> String? {
        if !installed { return "Steam still lists this game as partly installed." }
        if updating { return "This game is still downloading. You can play once it finishes." }
        if !client { return "BCD Dock can't start games without Valve's client components. You can download them in Settings, under BCD Dock." }
        if !signedIn { return "You're not signed in to Steam. Sign in to Steam in Settings, then play." }
        return nil
    }

    /// Steam's public store artwork for an App ID (no account data).
    static func cover(_ appID: Int) -> URL? {
        guard appID > 0 else { return nil }
        return URL(string: "https://cdn.cloudflare.steamstatic.com/steam/apps/\(appID)/library_600x900.jpg")
    }

    static let assetBase = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/"

    /// A store artwork file name is a relative path of plain characters.
    static func safeAssetName(_ name: String) -> Bool {
        !name.isEmpty && name.utf8.count <= 256 && !name.hasPrefix("/") && !name.contains("..") &&
            name.unicodeScalars.allSatisfy { $0.isASCII && (CharacterSet.alphanumerics.contains($0) || "._-/".unicodeScalars.contains($0)) }
    }

    /// Artwork candidates for an App ID, in order: the capsule named in the
    /// game's product info (newer apps publish it only under a hashed folder),
    /// the legacy path, the same for a demo's full game, then the header image.
    /// A card tries them one after another.
    static func artwork(appID: Int, owned: (Int) -> SteamOwnedGame?) -> [URL] {
        var urls: [URL] = []
        func add(_ url: URL?) { if let url, !urls.contains(url) { urls.append(url) } }
        func asset(_ id: Int, _ name: String?) -> URL? {
            guard let name, safeAssetName(name) else { return nil }
            return URL(string: assetBase + "\(id)/" + name)
        }
        func direct(_ id: Int) {
            add(asset(id, owned(id)?.libraryCapsule))
            add(cover(id))
        }
        direct(appID)
        if let parent = owned(appID)?.parentID, parent != appID { direct(parent) }
        add(asset(appID, owned(appID)?.headerImage))
        return urls
    }
}

/// "Start with: The game" on a Steam game's Game details page: the game's own
/// program runs in Wine without Steam, which suits games that do not need Steam
/// (DRM-free ones). Which program is Steam's own launch configuration for the app
/// (its product info's `config.launch`), never a list of program names; when that
/// names nothing that can run here, the user picks one of the install folder's
/// programs. Madeira Dock stays the default.
enum SteamDirectStart {
    /// LibraryEntry.steamStart for this start; nil there is Madeira Dock.
    static let mode = "game"

    /// What "The game" starts: the program, relative to the install folder and
    /// spelt as on disk, its arguments, and its working folder (nil: the program's
    /// own folder; "": the install folder).
    struct Choice: Equatable {
        var program: String
        var arguments: String
        var folder: String?
        var launchIndex: UInt32? = nil
    }

    /// Launch types Steam gives entries that are not the game itself.
    static let otherKinds: Set<String> = ["server", "editor", "vr", "othervr", "openvroverlay", "osvr", "manual"]

    /// A path from Steam's launch configuration in slash form (bin/game.exe), or nil
    /// for anything that could leave the install folder or is not a plain name: an
    /// absolute path, a drive, "..", a control or reserved character. "." parts go.
    static func relativePath(_ raw: String) -> String? {
        let text = raw.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "\\", with: "/")
        guard !text.hasPrefix("/"), text.utf8.count <= 512,
              !text.unicodeScalars.contains(where: { $0.value < 0x20 || "<>:\"|?*".unicodeScalars.contains($0) }) else { return nil }
        var parts: [Substring] = []
        for part in text.split(separator: "/") where part != "." {
            if part == ".." { return nil }
            parts.append(part)
        }
        return parts.joined(separator: "/")
    }

    /// `relative` found under `root` one name at a time, exactly or else without case
    /// (as Windows finds it): its spelling on disk, or nil when a name is missing, the
    /// last one is not of the kind asked for, or the result leaves `root`.
    static func onDisk(_ relative: String, in root: URL, directory: Bool) -> String? {
        let fm = FileManager.default
        var url = root
        var spelled: [String] = []
        for part in relative.split(separator: "/").map(String.init) {
            guard let names = try? fm.contentsOfDirectory(atPath: url.path),
                  let name = names.first(where: { $0 == part }) ?? names.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame })
            else { return nil }
            url.appendPathComponent(name)
            spelled.append(name)
        }
        var isDirectory: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue == directory else { return nil }
        let base = root.resolvingSymlinksInPath().path
        let target = url.resolvingSymlinksInPath().path
        guard target == base ? directory : target.hasPrefix(base + "/") else { return nil }
        return spelled.joined(separator: "/")
    }

    /// The extensions of a Windows program or startup script ("exe", "bat", "cmd").
    static let programExtensions: Set<String> = ["exe", "bat", "cmd"]

    /// The launch entry "The game" starts. Candidates are the Windows entries (no
    /// platform list, or one naming Windows) outside beta branches and of a kind that is
    /// the game itself; Steam's default comes first ("default", then no type or "none",
    /// then the other options), each in Steam's order with a 64-bit entry before one
    /// for any architecture before a 32-bit one. The first candidate whose program is a
    /// Windows program (.exe, .bat or .cmd) inside the install folder, and whose working
    /// folder (when it names one) exists there, is taken.
    static func choose(_ options: [SteamLaunchOption], installFolder root: URL) -> Choice? {
        func rank(_ option: SteamLaunchOption) -> Int? {
            let type = option.type.lowercased()
            guard option.betaKey.isEmpty, !otherKinds.contains(type),
                  option.requiredDLC == nil || option.requiredDLC == "" || option.requiredDLC == "0",
                  option.oslist.isEmpty || option.oslist.lowercased().contains("windows") else { return nil }
            let kind: Int
            if type == "default" { kind = 0 } else if type.isEmpty || type == "none" { kind = 1 } else { kind = 2 }
            let arch: Int
            if option.osarch == "64" { arch = 0 } else if option.osarch.isEmpty { arch = 1 } else { arch = 2 }
            return kind * 3 + arch
        }
        var ranked: [(rank: Int, index: Int, option: SteamLaunchOption)] = []
        for (index, option) in options.enumerated() {
            if let value = rank(option) { ranked.append((rank: value, index: index, option: option)) }
        }
        ranked.sort { a, b in a.rank != b.rank ? a.rank < b.rank : a.index < b.index }
        let spaces = CharacterSet.whitespaces
        for entry in ranked {
            let option = entry.option
            guard let path = relativePath(option.executable), !path.isEmpty,
                  programExtensions.contains(URL(fileURLWithPath: path).pathExtension.lowercased()),
                  let program = onDisk(path, in: root, directory: false) else { continue }
            var folder: String? = nil
            if !option.workingDir.trimmingCharacters(in: spaces).isEmpty {
                guard let relative = relativePath(option.workingDir) else { continue }
                if relative.isEmpty {
                    folder = ""
                } else {
                    guard let found = onDisk(relative, in: root, directory: true) else { continue }
                    folder = found
                }
            }
            return Choice(program: program, arguments: option.arguments.trimmingCharacters(in: spaces), folder: folder,
                          launchIndex: option.index)
        }
        return nil
    }

    // MARK: Steam launch option (a BCD Dock start)

    /// A launch entry a BCD Dock start can ask Valve's client for: Game details › Steam › Steam launch option
    /// (2026-10-24). Valve's client starts whatever `config.launch.<key>` names; Dock hands it the key.
    struct DockOption: Equatable {
        /// The config.launch key.
        var index: UInt32
        /// The program, relative to the install folder and spelt as on disk.
        var program: String
        var arguments: String
        /// Steam's description of the entry, cleaned for the screen ("" when it has none).
        var description: String

        /// `Launch: <program>`, or `<program> <arguments>` when Steam gave no description.
        var label: String {
            let call = arguments.isEmpty ? program : program + " " + arguments
            return description.isEmpty ? call : description + ": " + call
        }
    }

    /// The entries the choice lists, in Steam's order: Windows entries of the public branch whose program is an .exe
    /// inside the install folder (and whose working folder exists there), that need no DLC and are of a kind that is the
    /// game itself (not a server, editor or VR entry). Entries `choose` would skip are skipped here too, so a listed entry
    /// is always one a Dock start can run; nothing is listed for a cache that has no keys.
    static func dockOptions(_ options: [SteamLaunchOption], installFolder root: URL) -> [DockOption] {
        var listed: [DockOption] = []
        let spaces = CharacterSet.whitespaces
        for option in options {
            let type = option.type.lowercased()
            guard let index = option.index, option.betaKey.isEmpty, !otherKinds.contains(type),
                  option.requiredDLC == nil || option.requiredDLC == "" || option.requiredDLC == "0",
                  option.oslist.isEmpty || option.oslist.lowercased().contains("windows"),
                  let path = relativePath(option.executable), !path.isEmpty,
                  programExtensions.contains(URL(fileURLWithPath: path).pathExtension.lowercased()),
                  let program = onDisk(path, in: root, directory: false) else { continue }
            if !option.workingDir.trimmingCharacters(in: spaces).isEmpty {
                guard let relative = relativePath(option.workingDir) else { continue }
                if !relative.isEmpty, onDisk(relative, in: root, directory: true) == nil { continue }
            }
            let words = String(String.UnicodeScalarView((option.description ?? "").unicodeScalars.filter { $0.value >= 0x20 && $0.value != 0x7f }))
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let shown = String(String.UnicodeScalarView(option.arguments.unicodeScalars.filter { $0.value >= 0x20 && $0.value != 0x7f }))
                .trimmingCharacters(in: spaces)
            listed.append(DockOption(index: index, program: program, arguments: String(shown.prefix(80)),
                                     description: String(words.prefix(80))))
        }
        return listed
    }

    /// What a BCD Dock start asks Valve's client to launch: the key, and the log lines that say so.
    struct DockLaunch: Equatable {
        var index: UInt32
        var lines: [String]
    }

    /// The key a Dock start hands Valve's client. Without a choice (`chosen` nil: the game's page never set one) it is
    /// exactly what every Dock start used before: `choose`'s entry, else key 0, with the same log line. A choice counts
    /// only while the entry is still listed for this install (`dockOptions`); a stale one falls back to the default and
    /// says so.
    static func dockLaunch(chosen: Int?, options: [SteamLaunchOption]?, installFolder root: URL) -> DockLaunch {
        let fallback = options.flatMap { choose($0, installFolder: root)?.launchIndex }
        let standard = "[madeira-dock] launch option \(fallback ?? 0)\(fallback == nil ? " (none chosen: the default)" : "")"
        guard let chosen else { return DockLaunch(index: fallback ?? 0, lines: [standard]) }
        if let options, let key = UInt32(exactly: chosen), dockOptions(options, installFolder: root).contains(where: { $0.index == key }) {
            return DockLaunch(index: key, lines: ["[madeira-dock] launch option \(key) (chosen on the game's page)"])
        }
        return DockLaunch(index: fallback ?? 0, lines: [
            "[madeira-dock] the launch option chosen on the game's page (\(chosen)) is not in Steam's list for this install: the default is used",
            standard])
    }

    /// Night round 2026-11-11 (G5 S5; GeoGuessr PX27QG, Hollow Knight 2KHWEG and 5RHVB9: Valve's launch error 45 before any process): the launch entry the
    /// start asks for, field by field, so a log with a refusal names what the client may have disliked. The program's path inside the install folder (never
    /// a path outside it), the arguments cut at 80 characters, the platform fields, and whether a required DLC, a beta key or a working directory is set.
    static func launchEntryLine(options: [SteamLaunchOption]?, key: UInt32) -> [String] {
        guard let option = options?.first(where: { $0.index == key }) else { return [] }
        func clean(_ text: String, limit: Int) -> String {
            String(String(String.UnicodeScalarView(text.unicodeScalars.map { $0.value == 9 ? " " : $0 }.filter { $0.value >= 0x20 && $0.value != 0x7f })).prefix(limit))
        }
        let program = relativePath(option.executable).map { clean($0, limit: 120) } ?? "(outside the install folder)"
        return ["[madeira-dock] launch option \(key) entry: program=\(program) arguments=\"\(clean(option.arguments, limit: 80))\" oslist=\(clean(option.oslist, limit: 40)) "
                + "osarch=\(clean(option.osarch, limit: 16)) type=\(clean(option.type, limit: 24)) dlc=\((option.requiredDLC ?? "").isEmpty ? 0 : 1) "
                + "beta=\(option.betaKey.isEmpty ? 0 : 1) workingDir=\(option.workingDir.isEmpty ? 0 : 1)"]
    }

    /// madeira-bcd (round 623): the log lines that say what a Dock start's .bat or .cmd launch entry will run. Valve's client starts the
    /// entry's program as it is, and when that is a script the log shows only "cmd.exe /s/c" and an exit code: Messiah's "Launch Messiah.bat"
    /// ends with exit code 255 at once without starting a program (MZYTWC, build 622; ANV0TK, 2D4QK1 and 53W6G9, build 614) and nothing in
    /// the log says why (Wine's cmd exits 255 from a pipe whose left program cannot be started, or from a failed choice). `key` is the
    /// config.launch key the start asks for. Returns no line for an entry that is not a .bat or .cmd inside the install folder. Text only: the
    /// first 40 lines of the file (the first 16 KiB), at most 160 characters each, control characters taken out; a file with a zero byte is
    /// not text and is only named.
    static func batchPreview(options: [SteamLaunchOption]?, key: UInt32, installFolder root: URL) -> [String] {
        guard let option = options?.first(where: { $0.index == key }), let path = relativePath(option.executable), !path.isEmpty,
              ["bat", "cmd"].contains(URL(fileURLWithPath: path).pathExtension.lowercased()),
              let program = onDisk(path, in: root, directory: false) else { return [] }
        func clean(_ text: String, limit: Int) -> String {
            let kept = String(String.UnicodeScalarView(text.unicodeScalars.map { $0.value == 9 ? " " : $0 }.filter { $0.value >= 0x20 && $0.value != 0x7f }))
            return String(kept.prefix(limit))
        }
        let arguments = clean(option.arguments, limit: 80)
        let head = "[bat-start] launch option \(key): \(clean(program, limit: 160))" + (arguments.isEmpty ? "" : " arguments=\"\(arguments)\"")
        guard let handle = FileHandle(forReadingAtPath: root.appendingPathComponent(program).path) else { return [head + " (could not be opened)"] }
        defer { try? handle.close() }
        let data = handle.readData(ofLength: 16384)
        if data.contains(0) { return [head + " (not a text file, \(data.count) bytes read)"] }
        var lines = String(decoding: data, as: UTF8.self).replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: CharacterSet(charactersIn: "\n\r"))
        while let last = lines.last, last.trimmingCharacters(in: .whitespaces).isEmpty { lines.removeLast() }
        let shown = lines.prefix(40)
        return [head + " (\(lines.count) line\(lines.count == 1 ? "" : "s")\(lines.count > shown.count ? ", the first \(shown.count) follow" : " follow"))"]
            + shown.enumerated().map { "[bat-start] \($0.offset + 1): " + clean($0.element, limit: 160) }
    }

    /// The install folder's Windows programs for the Program picker: every .exe, .bat
    /// or .cmd up to six folders deep (linked folders are not followed), at most 400, sorted.
    static func programs(in root: URL) -> [String] {
        let fm = FileManager.default
        var found: [String] = []
        func walk(_ url: URL, _ prefix: String, _ depth: Int) {
            guard depth <= 6, let names = try? fm.contentsOfDirectory(atPath: url.path) else { return }
            for name in names.sorted() where !name.hasPrefix(".") && found.count < 400 {
                let child = url.appendingPathComponent(name)
                let relative = prefix.isEmpty ? name : prefix + "/" + name
                var isDirectory: ObjCBool = false
                guard fm.fileExists(atPath: child.path, isDirectory: &isDirectory) else { continue }
                if isDirectory.boolValue {
                    if ["_commonredist", "commonredist", "redist"].contains(name.lowercased()) { continue }
                    if (try? fm.destinationOfSymbolicLink(atPath: child.path)) == nil { walk(child, relative, depth + 1) }
                } else if programExtensions.contains(child.pathExtension.lowercased()) {
                    found.append(relative)
                }
            }
        }
        walk(root, "", 0)
        return found.sorted { $0.localizedStandardCompare($1) == .orderedAscending }
    }

    /// The program a Steam game's library pills describe (32-bit or 64-bit, graphics
    /// API), found as "The game" finds it: the user's pick while it is installed, else
    /// Steam's launch configuration, else the install folder's only program; nil when
    /// none is known (the card then shows only the install size).
    static func program(picked: String?, options: [SteamLaunchOption]?, installFolder root: URL) -> String? {
        if let picked, !picked.isEmpty, let found = onDisk(picked, in: root, directory: false) { return found }
        if let options, let choice = choose(options, installFolder: root) { return choice.program }
        let found = programs(in: root)
        return found.count == 1 ? found[0] : nil
    }

    /// Why Play is not offered for "The game" yet, or nil. No Steam client or sign-in
    /// is involved; the game's own program must be chosen.
    static func blocker(installed: Bool, program: String?, updating: Bool = false) -> String? {
        if !installed { return "Steam still lists this game as partly installed." }
        if updating { return "This game is still downloading. You can play once it finishes." }
        if (program ?? "").isEmpty { return "No program is set for this game. Pick one on the game's page, under Steam › Program." }
        return nil
    }
}

// MARK: - Model

/// The games Steam has installed in the prefix (Dock's discovery, off the main
/// thread), with the build each Madeira-managed install records.
@MainActor final class SteamGamesModel: ObservableObject {
    static let shared = SteamGamesModel()
    @Published private(set) var games: [DockGame] = []
    /// `buildid` of each install in Madeira's own library folder, by App ID.
    @Published private(set) var builds: [Int: Int] = [:]
    /// The language each of those install records names (`UserConfig.language`).
    @Published private(set) var languages: [Int: String] = [:]
    /// The depots each of those install records holds.
    @Published private(set) var depots: [Int: Set<Int>] = [:]
    /// The beta branch each of those install records names (`BetaKey`); none for public.
    @Published private(set) var branches: [Int: String] = [:]
    private var scanning = false
    /// A refresh was asked for while a scan ran (an install record was just
    /// written): scan again once it ends.
    private var rescan = false
    private var lastCount = -1

    /// Reads the install records again, off the main thread.
    func refresh() {
        guard MadeiraDock.enabled else { return }
        guard !scanning else { rescan = true; return }
        scanning = true
        let drive = MadeiraDock.drive
        Task.detached(priority: .utility) {
            let found = MadeiraDock.games(drive: drive)
            var builds: [Int: Int] = [:], languages: [Int: String] = [:], depots: [Int: Set<Int>] = [:]
            var branches: [Int: String] = [:]
            for game in found where SteamInstallPaths.isManaged(library: game.library) && game.installed {
                let steamApps = drive.appendingPathComponent(game.library, isDirectory: true)
                if let build = SteamInstallFiles.buildID(appID: game.id, steamApps: steamApps) {
                    builds[game.id] = build
                }
                languages[game.id] = SteamInstallFiles.language(appID: game.id, steamApps: steamApps)
                depots[game.id] = SteamInstallFiles.depots(appID: game.id, steamApps: steamApps)
                branches[game.id] = SteamInstallFiles.betaKey(appID: game.id, steamApps: steamApps)
            }
            let recorded = builds, recordedLanguages = languages, recordedDepots = depots
            let recordedBranches = branches
            await MainActor.run {
                self.scanning = false
                let again = self.rescan
                self.rescan = false
                if self.games != found { self.games = found }
                if self.builds != recorded { self.builds = recorded }
                if self.languages != recordedLanguages { self.languages = recordedLanguages }
                if self.depots != recordedDepots { self.depots = recordedDepots }
                if self.branches != recordedBranches { self.branches = recordedBranches }
                if found.count != self.lastCount {
                    self.lastCount = found.count
                    LogStore.shared.log("[steam-games] installed=\(found.count) ready=\(found.filter(\.installed).count)")
                }
                if again { self.refresh() }
            }
        }
    }

    /// Pull to refresh: the install records again and, when signed in, the account's
    /// library. Wave 3 (2026-10-19): moved here unchanged from the classic library's Steam
    /// section when that was deleted; BCDLiveStore.refreshLibrary calls it.
    @MainActor static func refreshAll() async {
        SteamGamesModel.shared.refresh()
        if SteamOwnedLibrary.enabled && SteamOwnedLibrary.shared.signedIn {
            await SteamOwnedLibrary.shared.refreshLibrary(interactive: true)
        }
    }
}

/// Why Play cannot start this Steam game yet, or nil (always nil for a game that is not a
/// Steam game). A Steam game starts through Madeira Dock (ContentView.launchLibraryEntry)
/// once its files are complete and Dock can sign in; "The game" once its program is chosen
/// (no client or sign-in involved); neither while an update still downloads.
/// Modified by BCD (thebcd69), 2026-10-18: moved out of the classic Game details page's start()
/// unchanged, so that BCD's interface asks exactly what that page asked before Play
/// (BCDLiveStore.start). Wave 3 (2026-10-19): moved here, body unchanged, when that page was
/// deleted (git history: Library.swift, steamBlocker).
enum SteamPlayCheck {
    @MainActor static func blocker(_ entry: LibraryEntry) -> String? {
        guard let appID = entry.steamAppID else { return nil }
        if SteamOwnedLibrary.shared.downloads[appID] != nil {
            return "This game is still updating. Resume the update and let it finish, then play."
        }
        let installed = SteamGamesModel.shared.games.first { $0.id == appID }?.installed ?? false
        if entry.startsSteamGameDirectly {
            return SteamDirectStart.blocker(installed: installed, program: entry.steamProgram)
        }
        return SteamGamesRules.blocker(installed: installed, client: MadeiraDock.clientInstalled, signedIn: SteamSignIn.isSignedIn)
    }
}

// MARK: - Steam Cloud

/// Whether Steam Cloud saves sync: Settings › Steam Cloud saves, kept in bcd.cfg as
/// env.MADEIRA_STEAM_CLOUD (on unless it is 0; docs/STEAM_CLOUD.md). Turning it back on
/// syncs the installed games at once.
@MainActor final class SteamCloudSetting: ObservableObject {
    static let shared = SteamCloudSetting()
    @Published var on = SteamSignIn.flag("MADEIRA_STEAM_CLOUD", default: true) {
        didSet {
            guard on != oldValue else { return }
            MadeiraConfig.set("env.MADEIRA_STEAM_CLOUD", on ? nil : "0")
            SteamLog.event("[steam-cloud] setting on=\(on ? 1 : 0)")
            if on { SteamOwnedLibrary.shared.cloudTurnedOn() } else { SteamOwnedLibrary.shared.cloudTurnedOff() }
        }
    }
}
