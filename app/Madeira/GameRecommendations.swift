//
//  GameRecommendations.swift
//  Madeira
//
//  madeira-bcd: the settings Madeira recommends for the games it has been
//  tested with, shipped in the app (owner's decision, 2026-10-09).
//
//  A recommendation is the game's own config file (GameProfiles.swift,
//  bcd.cfg syntax) plus the switches the game's settings page keeps
//  elsewhere: AVX, Report an NVIDIA GPU, Wine's C++ runtime, the fastsync
//  switches, the FPS limit, the resolution and the scaling. A game that
//  arrives with no config of its own gets its recommendation once (a Steam
//  install, a game added by hand, a game already in the library);
//  "Reset to Recommended" at the bottom of its settings brings it back at
//  any time (GameRecommendationsApply.swift).
//
//  The lists are data since 2026-10-18 (owner's decision): recommended/recommendations.json,
//  which every build bundles and BCD's public repository serves, signed, so a game fixed by a
//  new line gets it without a new build (GameRecommendationsList.swift reads the format,
//  GameRecommendationsSource.swift fetches and checks it). The app uses the newer of its own
//  copy and the last good download; `active` below is that list.
//
//  How a game is recognised (each rule's "match"; the first rule that matches wins):
//    - A library Steam game by its App ID or by its folder under
//      steamapps\common, which Steam keeps the same from build to build.
//    - A program by its file name, wherever it was installed; only the
//      game's own programs count, not a redistributable in its folder.
//    - GTA V Enhanced by PlayGTAV.exe (or GTA5_Enhanced.exe) beside
//      GTA5_Enhanced.exe: inside a Steam library it is the Steam copy,
//      anywhere else another copy, and each has its own list. Red Dead
//      Redemption 2's RDR2.exe and PlayRDR2.exe the same way.
//    - GTA V Legacy only from Steam: App ID 271590, the folder "Grand
//      Theft Auto V", GTA5.exe, or PlayGTAV.exe beside GTA5.exe and without
//      GTA5_Enhanced.exe (Enhanced is checked first).
//    - Any other game gets the standard list (owner's decision, 2026-10-12;
//      GameRecommendationsApply.swift); only the Desktop gets none.
//
//  This file is Foundation only: tests/host/check-game-recommendations.py
//  compiles it with a test harness. The lists are the working ones recorded
//  in the private notes (OYUN-AYARLARI); a game that gets a newer working
//  list gets it in recommended/recommendations.json too, with a higher rule
//  `version` and a higher file `version` (with every build, owner's order
//  2026-10-09; tools/publish-recommendations.py checks and signs it).
//

import Foundation

/// The recommended setup of one game. A switch left nil is not touched.
struct GameRecommendation: Equatable {
    /// Stable identity ("rdr2-steam"); the record of what was applied keys on it.
    let id: String
    /// Shown under "Reset to Recommended".
    let title: String
    /// Raised whenever `config` or a switch changes.
    let version: Int
    /// The game's own config file, bcd.cfg syntax.
    let config: String
    var avx: Bool? = nil
    var nvidia: Bool? = nil
    var wineVCRT: Bool? = nil
    /// Game details › Fast synchronization (LibraryEntry.fastSync).
    var fastSync: Bool? = nil
    /// Game details › Fast semaphore waits (LibraryEntry.semaphoreFastPath).
    var semaphoreFastPath: Bool? = nil
    /// LibraryEntry.fpsMode: 1 = 60, 3 = 30, 4 = 40, 0 = display maximum, 2 = uncapped.
    var fpsMode: Int? = nil
    /// "WxH", or `GameRecommendation.screen720`. nil keeps the device's own
    /// default, which is what the lists recorded as 1408x648 were on a 19.5:9 iPhone.
    var resolution: String? = nil
    /// `resolution`: this screen's shape at 720 lines (ResolutionChoices.screen720,
    /// 1568x720 on a 19.5:9 iPhone), for games that offer full screen only at 720
    /// lines or more. A screen whose default already has them keeps its default.
    static let screen720 = "screen@720"
    /// Aspect & scaling (DisplayMode raw value); "fit" is the default.
    var display: String? = nil

    /// Lines only a device with less than 12 GB of RAM gets, after `config`
    /// (GameRecommendations.smallRAMDevice). nil: every device gets the same list.
    var smallRAMConfig: String? = nil

    /// The config file's text as GameProfile stores it (one trailing newline): `config`,
    /// and on a device with less than 12 GB of RAM `smallRAMConfig` after it.
    var fileText: String {
        var text = config.hasSuffix("\n") ? config : config + "\n"
        if let extra = smallRAMConfig, GameRecommendations.smallRAMDevice {
            text += extra.hasSuffix("\n") ? extra : extra + "\n"
        }
        return text
    }

    /// A short list of the switches it sets, for Game details.
    var switchSummary: String { summary(home: false) }

    /// The same for the madeira-bcd home screen's settings, which have no fast
    /// semaphore or FPS switches and show fastsync as Safe thread sync.
    /// madeira-bcd FORK-ONLY: the `home` variant, for the earlier home screen's settings (that screen is
    /// gone, wave 3, 2026-10-19; the variant stays pinned by tests/host/check-game-recommendations.py).
    func summary(home: Bool) -> String {
        var parts: [String] = []
        if let avx { parts.append("AVX \(avx ? "on" : "off")") }
        if let nvidia { parts.append("NVIDIA \(nvidia ? "on" : "off")") }
        if let wineVCRT { parts.append("Wine's C++ runtime \(wineVCRT ? "on" : "off")") }
        if fastSync == false { parts.append(home ? "safe thread sync on" : "fast synchronization off") }
        if !home, let semaphoreFastPath { parts.append("fast semaphore waits \(semaphoreFastPath ? "on" : "off")") }
        if !home, let fpsMode {
            let label = [1: "60", 3: "30", 4: "40", 0: "display maximum", 2: "uncapped"][fpsMode] ?? "\(fpsMode)"
            parts.append("FPS limit \(label)")
        }
        if let resolution { parts.append(resolution == Self.screen720 ? "this screen's shape at 720 lines" : resolution) }
        return parts.joined(separator: ", ")
    }
}

enum GameRecommendations {
    // MARK: The device

    /// Installed RAM (hw.memsize): a 12 GB iPhone reports 11,695 MB, an 8 GB one
    /// about 7,500 MB. A variable so the host test can try both.
    static var deviceRAMBytes: UInt64 = ProcessInfo.processInfo.physicalMemory

    /// An 8 GB (or smaller) device: under 10 GB installed. It gets a list's
    /// `smallRAMConfig`; a 12 GB phone or a 16 GB iPad does not.
    static var smallRAMDevice: Bool { deviceRAMBytes < 10 << 30 }

    // MARK: The lists

    /// The rule every game without one of its own gets (GameRecommendationsApply.swift).
    static let standardID = "standard"

    private static let lock = NSLock()
    private static var loaded: GameRecommendationList?

    /// The list in use: the built-in one, or a newer signed download kept from an earlier run
    /// (RecommendationsStore.initialList, read once, at the first use). A newer download
    /// replaces it while the app runs (RecommendationsSource).
    static var active: GameRecommendationList {
        lock.lock()
        defer { lock.unlock() }
        if let list = loaded { return list }
        let list = RecommendationsStore.initialList()
        loaded = list
        return list
    }

    /// Puts a newer list in use (RecommendationsSource, after its checks).
    static func install(_ list: GameRecommendationList) {
        lock.lock()
        loaded = list
        lock.unlock()
    }

    /// The next use reads the files again, as at an app start (the host checks).
    static func reload() {
        lock.lock()
        loaded = nil
        lock.unlock()
    }

    /// Every list, in the file's order.
    static var all: [GameRecommendation] { active.recommendations }

    /// The list of every game without one of its own; nil only without any list at all.
    static var standard: GameRecommendation? { active.standard }

    // MARK: Recognising a game

    /// The recommendation for a game, or nil.
    /// - windowsPath: a program ("C:\Games\GoW\GoW.exe"), or a library Steam
    ///   game's install folder ("C:\...\steamapps\common\Red Dead Redemption 2").
    /// - steamAppID: set for a library Steam game.
    /// - fileExists: whether a C:\ path exists (GTA5_Enhanced.exe beside a program).
    /// - programsIn: the programs in a library Steam game's install folder (GameRecommendationList.match).
    static func match(windowsPath: String, steamAppID: Int?, fileExists: (String) -> Bool,
                      programsIn: ((String) -> [String])? = nil) -> GameRecommendation? {
        active.match(windowsPath: windowsPath, steamAppID: steamAppID, fileExists: fileExists, programsIn: programsIn)
    }

    /// The folder name right under steamapps\common, lowercased, or nil.
    static func steamCommonFolder(_ parts: [String]) -> String? {
        guard parts.count >= 3 else { return nil }
        for i in 0..<(parts.count - 2) where parts[i] == "steamapps" && parts[i + 1] == "common" {
            return parts[i + 2]
        }
        return nil
    }
}

extension GameRecommendationList {
    /// The first rule that matches the game (see GameRecommendations.match).
    /// - programsIn: the programs (.exe files, as C:\ paths) in a library Steam game's install folder, or nil
    ///   (GameRecommendations.drivePrograms).
    func match(windowsPath: String, steamAppID: Int?, fileExists: (String) -> Bool,
               programsIn: ((String) -> [String])? = nil) -> GameRecommendation? {
        let path = windowsPath.replacingOccurrences(of: "/", with: "\\").trimmingSuffix("\\")
        let parts = path.lowercased().split(separator: "\\").map(String.init)
        guard let last = parts.last else { return nil }
        let steamFolder = GameRecommendations.steamCommonFolder(parts)
        guard last.hasSuffix(".exe") else {
            // A library Steam game: its App ID, or its install folder right under
            // steamapps\common (not a batch file inside it).
            let installFolder = last == steamFolder ? steamFolder : nil
            if let rec = rules.first(where: { rule in
                guard let match = rule.match else { return false }
                if let steamAppID, match.steamAppIDs.contains(steamAppID) { return true }
                if let installFolder, match.steamFolders.contains(installFolder) { return true }
                return false
            })?.recommendation { return rec }
            // madeira-bcd (owner 2026-11-17, round 668 N110): a rule that names only programs (no App ID, no Steam
            // folder) also reaches a library Steam start, by one of its programs in the game's install folder. The
            // program's Steam flag and its beside / notBeside files count as for a program start (inSteam).
            guard steamAppID != nil || installFolder != nil, let programsIn else { return nil }
            var folders: [String: [String]] = [:]   // a program's lowercased file name: the folders it is in
            for program in programsIn(path) {
                let p = program.replacingOccurrences(of: "/", with: "\\")
                guard let cut = p.lastIndex(of: "\\") else { continue }
                folders[p[p.index(after: cut)...].lowercased(), default: []].append(String(p[..<cut]))
            }
            guard !folders.isEmpty else { return nil }
            for rule in rules {
                guard let match = rule.match, match.steamAppIDs.isEmpty, match.steamFolders.isEmpty else { continue }
                for program in match.programs where program.steam != false {
                    for folder in folders[program.name] ?? [] {
                        if let beside = program.beside, !fileExists(folder + "\\" + beside) { continue }
                        if let notBeside = program.notBeside, fileExists(folder + "\\" + notBeside) { continue }
                        return rule.recommendation
                    }
                }
            }
            return nil
        }
        // A program, wherever it was installed: only the game's own programs, so a
        // redistributable inside a game's folder gets nothing.
        let inSteam = steamAppID != nil || steamFolder != nil
        let folder = path.lastIndex(of: "\\").map { String(path[..<$0]) } ?? ""
        for rule in rules {
            guard let match = rule.match else { continue }
            for program in match.programs where program.name == last {
                if let steam = program.steam, steam != inSteam { continue }
                if let beside = program.beside, !fileExists(folder + "\\" + beside) { continue }
                if let notBeside = program.notBeside, fileExists(folder + "\\" + notBeside) { continue }
                return rule.recommendation
            }
        }
        return nil
    }
}

private extension String {
    func trimmingSuffix(_ suffix: String) -> String {
        var s = self
        while s.hasSuffix(suffix) { s.removeLast(suffix.count) }
        return s
    }
}
