// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// What BCD will and will not do with an Epic game (docs/research/open-fork-2026-10-18/epic.md §2.2, §2.4,
// §2.6, §5): which games it refuses to install and says why, which it installs with a word of warning, and
// the arguments Play passes. Foundation only (the host checks compile it). This file holds the PUBLIC
// arguments only (the app name, the environment, the portal marker, the language and the deployment ID):
// nothing secret is named here. An exchange code, the account's ID and name, and an ownership token are
// milestone 2 and live in BCDEpicSigned.swift and BCDEpicCredentials.swift.
//
// Milestone 2 adds (1) the signed-in start, so an ownership-token game is no longer refused (when the
// signed-in start is on) and the notes say BCD starts games signed in, and (2) a short, explicit list of
// games whose anti-cheat guards only the online mode and which the game itself can start without it
// (`antiCheatExceptions`: GTA V, with its own -nobattleye option), never a general "ignore anti-cheat".

enum BCDEpicRules {
    /// A reason BCD will not install a game, in words for the player.
    struct Refusal: Equatable {
        /// A short word for the log and for tests: "ownership-token", "anti-cheat", ...
        var code: String
        var message: String
    }

    /// The game id BCD's interface uses for an Epic app ("epic.<app name>"), and back. A hook claims an
    /// id only while Epic is on and only when it has that exact shape: Steam's ("steam.<App ID>"), GOG's,
    /// the library's ("local.<UUID>") and anything else are never Epic's.
    static func gameID(_ app: String) -> String { "epic." + app }

    static func claimedApp(_ id: String, enabled: Bool) -> String? {
        guard enabled, id.hasPrefix("epic.") else { return nil }
        let app = String(id.dropFirst(5))
        return BCDEpicCatalog.validAppName(app) ? app : nil
    }

    // MARK: From the catalog (known before anything is downloaded)

    /// Why a game cannot be installed at all, from what Epic's catalog says about it. `signedStart`: BCD starts games signed in to Epic
    /// (env.BCD_EPIC_SIGNIN and BCD Host), so a game that asks Epic for an ownership token can run; without it such a game is refused.
    static func catalogRefusal(_ item: BCDEpicItem, signedStart: Bool = true) -> Refusal? {
        if let provider = item.thirdParty {
            let name = provider == "yes" ? "" : sanitized(provider)
            let who = name.isEmpty ? "another company's launcher" : "another company's launcher (\(name))"
            return Refusal(code: "other-launcher",
                           message: "This game starts through \(who), which BCD can't run yet.")
        }
        if item.accessControl == true {
            return Refusal(code: "access-control",
                           message: "This game checks Epic's online access every time it starts (as Fortnite does). BCD can't run those.")
        }
        if item.ownershipToken == true, !signedStart {
            return Refusal(code: "ownership-token",
                           message: "This game asks Epic to confirm you own it each time it starts. BCD's Epic sign-in for games is switched off, so it wouldn't start.")
        }
        return nil
    }

    /// A word of warning shown beside Install: the game can be installed and may still not start.
    static func catalogWarning(_ item: BCDEpicItem, signedStart: Bool = true) -> String? {
        if item.ownershipToken == true { return signedStart ? tokenNote : nil }
        if item.canRunOffline == false { return signedStart ? onlineNote : offlineNote }
        return nil
    }

    static let offlineNote = "Epic says this game must be online to start. BCD's Epic sign-in for games is switched off, so it may stop at a sign-in or online check."
    static let onlineNote = "Epic says this game must be online to start. BCD starts it signed in to your Epic account, so it needs an internet connection."
    static let tokenNote = "This game asks Epic to confirm you own it each time it starts. BCD does that when it starts the game signed in, so it needs an internet connection. Games like this often add copy protection of their own that may not run here."

    /// What a game's page says before Install: why BCD cannot install it (the catalog's rules, then a refusal remembered from an
    /// earlier try, then what a look at the manifest found), or a word of warning when it can (it must be online; it uses Epic
    /// Online Services; its anti-cheat is one BCD starts the game without).
    static func advice(item: BCDEpicItem, remembered: Refusal?, facts: BCDEpicFacts?, signedStart: Bool = true) -> (blocker: String?, warning: String?) {
        let refusal = catalogRefusal(item, signedStart: signedStart) ?? remembered ?? facts?.refusal
        let warning = catalogWarning(item, signedStart: signedStart) ?? facts?.exception?.reason ?? (facts?.eos == true ? eosNote(signedStart: signedStart) : nil)
        return (refusal?.message, refusal == nil ? warning : nil)
    }

    // MARK: From the manifest (known once its files are listed)

    /// Anti-cheat programs that cannot run here, found by their files. Returns the name to show.
    static func antiCheat(in files: [String]) -> String? { antiCheats(in: files).first }

    /// Every anti-cheat the files show, in the order found (each once).
    static func antiCheats(in files: [String]) -> [String] {
        var names: [String] = []
        func add(_ name: String) { if !names.contains(name) { names.append(name) } }
        for path in files {
            for part in path.replacingOccurrences(of: "\\", with: "/").split(separator: "/") {
                let name = part.lowercased()
                if name.contains("easyanticheat") { add("Easy Anti-Cheat") }
                if name == "battleye" || name.hasPrefix("beclient") || name.hasPrefix("beservice") { add("BattlEye") }
                if name.contains("equ8") { add("EQU8") }
            }
        }
        return names
    }

    /// A game whose anti-cheat guards only its online mode, and which can be started without it by an option of the game's own: BCD
    /// installs it and starts it with that option, so it plays offline (its story mode). A short, explicit list: each item names the
    /// game by its program and a file beside it (as recommended/recommendations.json does, never by the folder it is in), the one
    /// anti-cheat it may carry and the option that turns it off. Any other anti-cheat in the files still refuses the game.
    struct AntiCheatException: Equatable {
        var id: String
        /// The launch program's file name, lower case.
        var program: String
        /// A file that must be among the game's files, and one that must not (file names, lower case).
        var beside: String
        var notBeside: String?
        var antiCheat: String
        /// The game's own option that starts it without that anti-cheat.
        var argument: String
        /// Shown beside Install and on the installed game's page.
        var reason: String
    }

    static let antiCheatExceptions: [AntiCheatException] = [
        AntiCheatException(id: "gta5-enhanced", program: "playgtav.exe", beside: "gta5_enhanced.exe", notBeside: nil, antiCheat: "BattlEye",
                           argument: "-nobattleye", reason: gtaReason),
        AntiCheatException(id: "gta5-legacy", program: "playgtav.exe", beside: "gta5.exe", notBeside: "gta5_enhanced.exe", antiCheat: "BattlEye",
                           argument: "-nobattleye", reason: gtaReason),
    ]

    /// madeira-bcd (round 624, agent R S24; 7Z8VKR, TV6H06): Epic's GTA V (both editions) starts Rockstar's launcher, which installs and starts
    /// a Windows service (RockstarService.exe); without the service manager every start failed ("Unable to open SC manager! (1722)" five times,
    /// "RockstarService.exe install/start" exit 1). The Dock does the same for the Steam copies (env.MADEIRA_DOCK_GAME_SCM).
    static func startsRockstarLauncher(_ exceptionID: String?) -> Bool {
        exceptionID == "gta5-enhanced" || exceptionID == "gta5-legacy"
    }

    static let gtaReason = "This game's anti-cheat (BattlEye) guards only GTA Online. BCD starts the game with its own -nobattleye option, so Story Mode plays and GTA Online stays off."

    /// The exception a build falls under: its launch program and the files beside it match an item, and BattlEye (or what that item allows)
    /// is the only anti-cheat in the files.
    static func antiCheatException(files: [String], launchExe: String) -> AntiCheatException? {
        func name(_ path: String) -> String { (path.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? "").lowercased() }
        let names = Set(files.map(name))
        let program = name(launchExe)
        let found = antiCheats(in: files)
        return antiCheatExceptions.first { item in
            item.program == program && names.contains(item.beside) && !(item.notBeside.map(names.contains) ?? false)
                && !found.isEmpty && found.allSatisfy { $0 == item.antiCheat }
        }
    }

    /// Why this build cannot be installed, from its files and its launch program.
    static func manifestRefusal(files: [String], launchExe: String) -> Refusal? {
        let exe = launchExe.replacingOccurrences(of: "\\", with: "/")
        guard !exe.isEmpty, exe.lowercased().hasSuffix(".exe") else {
            return Refusal(code: "no-program", message: "Epic lists no Windows program to start for this game.")
        }
        let wanted = BCDContentPaths.fold(exe.hasPrefix("./") ? String(exe.dropFirst(2)) : exe)
        guard files.contains(where: { BCDContentPaths.fold($0.replacingOccurrences(of: "\\", with: "/")) == wanted }) else {
            return Refusal(code: "no-program", message: "The program Epic names for this game isn't among its files.")
        }
        if let name = antiCheat(in: files), antiCheatException(files: files, launchExe: launchExe) == nil {
            return Refusal(code: "anti-cheat", message: "This game comes with anti-cheat software (\(name)), which can't run in BCD.")
        }
        return nil
    }

    /// The game ships Epic Online Services' SDK: it may ask for an Epic sign-in or show an online error.
    static func usesEOS(_ files: [String]) -> Bool {
        files.contains {
            let name = ($0.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? "").lowercased()
            return name == "eossdk-win64-shipping.dll" || name == "eossdk-win32-shipping.dll"
        }
    }

    /// `signedStart`: BCD starts the game signed in to your Epic account. A first sign-in on a web page that a game's Epic
    /// Online Services may ask for is not supported yet.
    static func eosNote(signedStart: Bool) -> String {
        signedStart ? "This game uses Epic Online Services. BCD starts it signed in to your Epic account. If it still asks you to sign in on a web page, that part isn't supported yet."
                    : "This game uses Epic Online Services. It may ask you to sign in to Epic or show an online error: BCD's Epic sign-in for games is switched off."
    }

    /// The setup step the manifest lists for the game (not run by BCD yet), or nil when it is Unreal Engine's own
    /// (the Visual C++ runtimes it installs are BCD's own).
    static func prerequisiteNote(name: String, path: String) -> String? {
        let label = sanitized(name)
        let lower = (name + " " + path).lowercased()
        guard !label.isEmpty || !path.isEmpty else { return nil }
        if lower.contains("prereqsetup") || lower.contains("ue4prereq") || lower.contains("ue5prereq") || lower.contains("ue prerequisites") { return nil }
        let shown = label.isEmpty ? "a setup program" : label
        return "Epic lists a setup step for this game (\(shown)). BCD doesn't run it yet."
    }

    /// Text from a server kept to letters, digits and a few marks, for a message or a log.
    static func sanitized(_ text: String, limit: Int = 40) -> String {
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-+()&")
        return String(String(text.filter { allowed.contains($0) }).trimmingCharacters(in: .whitespaces).prefix(limit))
    }
}

// MARK: - Starting a game

enum BCDEpicLaunch {
    /// The language part of the device's first preferred language, as Epic's launcher passes it ("en", "tr").
    static func locale(preferred: [String]) -> String {
        guard let first = preferred.first else { return "en" }
        let code = first.replacingOccurrences(of: "_", with: "-").split(separator: "-").first.map { $0.lowercased() } ?? ""
        return (2...3).contains(code.count) && code.utf8.allSatisfy({ $0 >= 0x61 && $0 <= 0x7A }) ? code : "en"
    }

    /// Epic's 32-digit identifiers (the deployment ID): hexadecimal, any case.
    static func validDeploymentID(_ text: String) -> Bool {
        text.utf8.count == 32 && text.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x46) || ($0 >= 0x61 && $0 <= 0x66) }
    }

    /// An argument string from Epic's own data (a manifest's launch command, a catalog's extra command line),
    /// kept only when it is plain: no control characters, quotes closed, a bounded length. nil: dropped.
    static func clean(_ text: String) -> String? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        guard trimmed.utf8.count <= 1500, !trimmed.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7F }) else { return nil }
        guard trimmed.filter({ $0 == "\"" }).count % 2 == 0 else { return nil }
        return trimmed
    }

    /// The game's own arguments: the program's launch command and the catalog's extra command line (each kept only when plain), then the
    /// option an anti-cheat exception starts the game with. `dropped` counts the strings left out as unsafe.
    static func gameParts(manifestCommand: String, extraCommand: String, exceptionArgument: String? = nil) -> (parts: [String], dropped: Int) {
        var game: [String] = []
        var dropped = 0
        for source in [manifestCommand, extraCommand] {
            if let cleaned = clean(source) { if !cleaned.isEmpty { game.append(cleaned) } } else { dropped += 1 }
        }
        if let exceptionArgument, !exceptionArgument.isEmpty, !game.joined(separator: " ").lowercased().contains(exceptionArgument.lowercased()) { game.append(exceptionArgument) }
        return (game, dropped)
    }

    /// What Play passes the game: the program's own launch command and the catalog's extra command line, then
    /// Epic's public arguments (the same a launcher gives a game it starts with no sign-in): the app name, the
    /// environment, the portal marker, the language and, when the build names one, the deployment ID. Never an
    /// exchange code, an account ID or name, or a token (a signed-in start adds those: BCDEpicSigned.swift). `dropped`
    /// counts the strings left out as unsafe.
    static func arguments(app: String, manifestCommand: String, extraCommand: String, deploymentID: String?,
                          preferredLanguages: [String], exceptionArgument: String? = nil) -> (text: String, dropped: Int) {
        var (game, dropped) = gameParts(manifestCommand: manifestCommand, extraCommand: extraCommand, exceptionArgument: exceptionArgument)
        var epic = ["-epicapp=" + app, "-epicenv=Prod", "-EpicPortal", "-epiclocale=" + locale(preferred: preferredLanguages)]
        if let deploymentID, validDeploymentID(deploymentID) { epic.append("-epicdeploymentid=" + deploymentID) }
        var text = (game + epic).joined(separator: " ")
        // The library takes at most 64 arguments in 4 KB (LibraryEntry.validate): Epic's own come last, so
        // when the game's strings make it too long they go, not the arguments.
        if text.utf8.count > 3500 || text.split(separator: " ").count > 60 {
            dropped += game.count
            game = []
            text = epic.joined(separator: " ")
        }
        return (text, dropped)
    }
}
