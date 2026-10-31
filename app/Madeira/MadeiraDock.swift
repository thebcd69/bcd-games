// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation
import Darwin
import Network

/// JIT pool size of a Dock session. A Dock session runs no desktop Steam
/// client (no Chromium helper fan-out), so it can live with a smaller pool.
/// Opt-in per launch (the Dock sheet's toggle, default from
/// `env.MADEIRA_DOCK_COMPACT_POOL`, off unless set to 1). It never applies
/// outside a Dock launch, and an explicit bcd.cfg `pool` still wins
/// because the caller applies that afterwards.
enum DockPerformancePolicy {
    static let compactPoolMB = 512

    static func sessionPoolMB(standard: Int, dock: Bool, compact: Bool) -> Int {
        dock && compact ? min(standard, compactPoolMB) : standard
    }
}

enum DockError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

/// A game Steam's client has installed in the prefix, read from its
/// `steamapps/appmanifest_<appid>.acf`.
struct DockGame: Identifiable, Equatable, Sendable {
    let id: Int                 // App ID
    let name: String
    let installDir: String      // one folder name under <library>/common
    let library: String         // drive-relative steamapps folder
    let installed: Bool         // StateFlags has "fully installed"
    let customExecutables: Bool // the install record lists per-user executables (CheckGuid)

    /// Windows path of the game's folder, which Dock requires Valve's client to resolve to.
    var windowsInstallPath: String {
        "C:\\" + (library + "/common/" + installDir).replacingOccurrences(of: "/", with: "\\")
    }
}

/// Madeira Dock: a small headless host (madeira-dock, built by
/// build/madeira-dock/build.sh into arm64ec-windows/dockhost.exe) that loads
/// Valve's genuine Windows Steam client inside the Wine session, signs in
/// with the user's own refresh token and asks the client to start an
/// installed game with Valve's own LaunchApp. Valve's client performs the
/// online authentication and licence checks; the game and its DRM are
/// untouched. The host drives undocumented client interfaces and supports
/// only client builds it has verified by SHA-256; anything else fails closed
/// with a report code.
///
/// The app side is this contract: environment variables, a one-use sign-in
/// transfer file and a numeric report file (C:\madeira-dock.txt).
enum MadeiraDock {
    /// `env.MADEIRA_DOCK = 0` hides Dock. Without a built dockhost.exe it is hidden too.
    static var enabled: Bool { SteamSignIn.flag("MADEIRA_DOCK", default: true) && bundled }
    static var bundled: Bool {
        Bundle.main.url(forResource: "dockhost", withExtension: "exe", subdirectory: "arm64ec-windows") != nil
    }
    static let executable = "C:\\windows\\system32\\dockhost.exe"

    static var prefix: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!.appendingPathComponent("wine")
    }
    static var drive: URL { prefix.appendingPathComponent("drive_c") }
    static var clientRoot: URL { drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot, isDirectory: true) }
    static var clientInstalled: Bool {
        FileManager.default.fileExists(atPath: clientRoot.appendingPathComponent("steamclient64.dll").path)
    }

    // MARK: Installed games

    static func validAppID(_ value: Int) -> Bool { value > 0 && UInt64(value) < UInt64(UInt32.max) }

    /// One folder name: no separators, drive letters, parent references or control characters.
    static func validFolderName(_ name: String) -> Bool {
        !name.isEmpty && name.utf8.count <= 240 && name != "." && name != ".." &&
            !name.contains("/") && !name.contains("\\") && !name.contains(":") && !name.contains("\"") &&
            !name.unicodeScalars.contains { $0.value < 32 } && !name.hasSuffix(" ") && !name.hasSuffix(".")
    }

    /// The game an app manifest describes, or nil when it is not one Dock can start.
    static func game(manifest data: Data, library: String) -> DockGame? {
        guard data.count <= 1 << 20, var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let state = root["AppState"], let appID = state["appid"]?.string.flatMap({ Int($0) }), validAppID(appID),
              let folder = state["installdir"]?.string, validFolderName(folder) else { return nil }
        let flags = state["StateFlags"]?.string.flatMap { UInt32($0) } ?? 0
        let name = state["name"]?.string.map { String($0.prefix(200)) } ?? ""
        return DockGame(id: appID, name: name.isEmpty ? "Steam app \(appID)" : name, installDir: folder, library: library,
                        installed: flags & 4 != 0, customExecutables: !(state["CheckGuid"]?.fields.isEmpty ?? true))
    }

    /// A record's depots: the ones it lends from each owner app (`SharedDepots`, depot -> owner)
    /// and the ones installed for the app itself (`InstalledDepots`).
    static func depots(manifest data: Data) -> (lent: [Int: Set<String>], installed: Set<String>) {
        guard data.count <= 1 << 20, var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let state = root["AppState"], let appID = state["appid"]?.string.flatMap({ Int($0) }) else { return ([:], []) }
        var lent: [Int: Set<String>] = [:]
        for (depot, owner) in (state["SharedDepots"]?.fields ?? [:]).prefix(64) {
            guard let ownerID = owner.string.flatMap({ Int($0) }), validAppID(ownerID), ownerID != appID else { continue }
            lent[ownerID, default: []].insert(depot)
        }
        return (lent, Set((state["InstalledDepots"]?.fields ?? [:]).keys.prefix(256)))
    }

    /// Installed games in the client's own library (`<client>/steamapps`) and the other
    /// libraries on C: that its `libraryfolders.vdf` lists. Read-only.
    static func games(drive: URL) -> [DockGame] {
        let fm = FileManager.default
        let clientLibrary = SteamRuntimeFiles.relativeRoot + "/steamapps"
        var libraries = [clientLibrary]
        if let data = try? Data(contentsOf: drive.appendingPathComponent(clientLibrary + "/libraryfolders.vdf")),
           data.count <= 1 << 20, var parser = try? SteamKeyValues(data), let root = try? parser.read() {
            for (_, entry) in (root["libraryfolders"]?.fields ?? [:]).sorted(by: { $0.key < $1.key }) {
                guard let path = entry["path"]?.string?.replacingOccurrences(of: "\\\\", with: "\\"),
                      let relative = libraryRelative(path) else { continue }
                if !libraries.contains(where: { $0.caseInsensitiveCompare(relative) == .orderedSame }) { libraries.append(relative) }
            }
        }
        var result: [DockGame] = []
        // Apps that only lend depots to another install ("Shared Only", e.g. GTA V
        // Enhanced's RGL/SC 1899670) have a record but nothing to start. A game that
        // lends depots but has its own installed too (a base game whose depots its
        // expansions share) stays: the same test as the uninstall (SteamInstall.swift).
        var lent: [Int: Set<String>] = [:], installed: [Int: Set<String>] = [:]
        for library in libraries.prefix(16) {
            let folder = drive.appendingPathComponent(library, isDirectory: true)
            guard folder.resolvingSymlinksInPath().path.hasPrefix(drive.resolvingSymlinksInPath().path + "/"),
                  let names = try? fm.contentsOfDirectory(atPath: folder.path) else { continue }
            for file in names.sorted().prefix(2000) where file.hasPrefix("appmanifest_") && file.hasSuffix(".acf") {
                guard let data = try? Data(contentsOf: folder.appendingPathComponent(file)) else { continue }
                let held = depots(manifest: data)
                lent.merge(held.lent) { $0.union($1) }
                guard let game = game(manifest: data, library: library),
                      !result.contains(where: { $0.id == game.id }) else { continue }
                installed[game.id] = held.installed
                result.append(game)
            }
        }
        result.removeAll { game in lent[game.id].map { installed[game.id, default: []].isSubset(of: $0) } ?? false }
        return result.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
    }

    /// "C:\\Games\\SteamLibrary" -> "Games/SteamLibrary/steamapps" (C: only).
    static func libraryRelative(_ windowsPath: String) -> String? {
        let path = windowsPath.replacingOccurrences(of: "\\", with: "/")
        guard path.lowercased().hasPrefix("c:/") else { return nil }
        let parts = path.dropFirst(3).split(separator: "/").map(String.init)
        guard !parts.isEmpty, parts.count <= 16, parts.allSatisfy(validFolderName) else { return nil }
        return (parts + ["steamapps"]).joined(separator: "/")
    }

    /// Everything a launch needs, checked before any sign-in is handed over.
    static func validate(_ game: DockGame, drive: URL, bundled: Bool = MadeiraDock.bundled) throws {
        guard bundled else { throw DockError.message("This copy of BCD was built without BCD Dock. Build the Dock host with build/madeira-dock/build.sh.") }
        guard validAppID(game.id), validFolderName(game.installDir) else {
            throw DockError.message("The Steam install record for this game has an invalid App ID or folder name.")
        }
        guard FileManager.default.fileExists(atPath: drive.appendingPathComponent(SteamRuntimeFiles.relativeRoot + "/steamclient64.dll").path) else {
            throw DockError.message("First download Valve's client components: the Steam client files aren't installed yet.")
        }
        var isFolder: ObjCBool = false
        guard game.installed,
              FileManager.default.fileExists(atPath: drive.appendingPathComponent(game.library + "/common/" + game.installDir).path, isDirectory: &isFolder),
              isFolder.boolValue else {
            throw DockError.message("According to Steam, this game isn't fully installed.")
        }
    }

    // MARK: Sign-in transfer

    /// Tokens stay in Keychain except for this bounded one-use transfer. The
    /// payload is not an ownership claim: Valve authenticates it in the guest.
    static func envelope(account: String, token: String, steamID: UInt64, appID: Int) throws -> Data {
        let name = Array(account.utf8), secret = Array(token.utf8)
        guard (1...64).contains(name.count), (1...8192).contains(secret.count),
              name.allSatisfy({ (33...126).contains($0) }),
              secret.allSatisfy({ (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || [45, 46, 95].contains($0) }),
              steamID >> 56 == 1, (steamID >> 52) & 15 == 1, (steamID >> 32) & 0xfffff == 1,
              steamID & 0xffffffff != 0, validAppID(appID) else {
            throw DockError.message("This Steam sign-in can't be passed to BCD Dock. Sign in to Steam in BCD once more.")
        }
        var data = Data("MDOCK001".utf8)
        func append(_ value: UInt64, bytes: Int) {
            for i in 0..<bytes { data.append(UInt8(truncatingIfNeeded: value >> (i * 8))) }
        }
        append(steamID, bytes: 8); append(UInt64(appID), bytes: 4)
        append(UInt64(name.count), bytes: 2); append(UInt64(secret.count), bytes: 2)
        data.append(contentsOf: name); data.append(contentsOf: secret)
        return data
    }

    /// The JWT subject selects the account; it is deliberately not treated as
    /// authenticated identity. Only Valve accepting the token can establish it.
    static func subject(_ token: String) throws -> UInt64 {
        guard token.utf8.count <= 8192 else { throw DockError.message("The saved Steam sign-in is too long to use.") }
        let parts = token.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3 else { throw DockError.message("The saved Steam sign-in has to be renewed.") }
        var encoded = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        encoded += String(repeating: "=", count: (4 - encoded.count % 4) % 4)
        guard let data = Data(base64Encoded: encoded),
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = json["sub"] as? String, let id = UInt64(text) else {
            throw DockError.message("The saved Steam sign-in has to be renewed.")
        }
        return id
    }

    /// The seeded prefix has C: but no guaranteed Z: mapping. Wine's Unix
    /// namespace reaches the protected native file without a drive mapping
    /// and without moving the transfer into the user-visible Documents folder.
    static func handoffGuestPath(_ url: URL, unixNamespace: Bool = true) -> String {
        (unixNamespace ? "\\\\?\\unix" : "Z:") + url.path.replacingOccurrences(of: "/", with: "\\")
    }

    // MARK: Report

    /// The host's report: a bounded set of numeric fields, never arbitrary guest text.
    struct Report {
        var fields: [String: String] = [:]
        var result: Int? { fields["probe-result"].flatMap(Int.init) }
        /// Valve's EResult values that mean the saved sign-in is no longer accepted: SteamError.signInExpiredCodes, as text
        /// (tests/host/check-dock-report.py keeps the two equal).
        static let expiredSignInResults: Set<String> = ["5", "15", "18", "26", "27", "94"]
        var failure: String? {
            guard let result, result != 0 else { return nil }
            if result == 30 {
                if fields["session-unsupported-client"] == "1" || fields["session-user-method-mismatch"] != nil {
                    return "This build of the Steam client isn't one BCD Dock supports. Dock works only with client builds it has checked; the client's fingerprint and the check that failed are in the log."
                }
                return "BCD Dock couldn't set up the Steam session (code 30). Export the log to see which check failed."
            }
            // The host waits 90 s after sign-in for Valve's client to count the game
            // among the account's subscriptions (madeira-dock src/session.c).
            if result == 34 {
                if fields["session-authenticated-online"] == "1" {
                    return "Steam signed in, but the license check for this game timed out. Export the log, then try again."
                }
                // Round 620 (Half-Life 2, 6XBJKH): Valve's client reported EResult 15 (access denied) for the saved sign-in
                // (callback 102/103, "session-connection-result=15"); the old text sent the player to their connection. The
                // codes are SteamError.signInExpiredCodes (the library's own "no longer accepts your sign-in" answer).
                if let code = fields["session-connection-result"], Self.expiredSignInResults.contains(code) {
                    return "Steam no longer accepts your sign-in. Sign out of Steam in Settings, then sign in again."
                }
                return "Steam's sign-in didn't complete. Check your connection, then try again."
            }
            if result == 35 { return "Steam couldn't confirm that the signed-in account has a license for this game." }
            // Offline start (madeira-dock docs/OFFLINE.md): Valve's client decides.
            if result == 50 {
                return fields["session-offline-abi"] == "0"
                    ? "You're offline, and BCD Dock can't start this Steam client build without a connection. Connect to the internet, then try again."
                    : "You're offline, and Steam has no saved data yet that lets this account sign in offline. Start any game once while online; after that, Steam allows offline starts until its saved offline sign-in expires."
            }
            if result == 51 { return "You're offline, and Steam turned down the offline sign-in. Connect to the internet and start the game once. If this keeps happening, export the log." }
            if result == 52 { return "Steam signed in offline, but its saved data doesn't include this game. Play it once while online, and it will work offline after that." }
            if result == 37 {
                if fields["session-native-handoff-app-mismatch"] == "1" {
                    return "The sign-in transfer BCD Dock got was meant for another launch. Close the session, then try again."
                }
                return "BCD Dock couldn't read the single-use Steam sign-in transfer, so Steam hasn't checked the login yet. Export the log, then try again."
            }
            // Valve's client could not prepare the game's per-user executable.
            if result == 49 {
                switch fields["ceg-result"].flatMap(Int.init) {
                case -1: return "Preparing this game's executable took Steam too long. Check your connection, then try again."
                case 0: return "Steam never began preparing this game's executable. Have Steam verify the game's files, then try again."
                case 10: return "Steam is still busy with this game, updating or running it. Wait a moment, then try again."
                case -4: return "Steam couldn't prepare this game's executable because the Windows service manager it needs didn't start."
                case -5: return "Steam couldn't prepare this game's executable: its Windows service isn't installed in BCD's Windows setup."
                // Round 623 (Just Cause 2, feedback BEQD9Y): result 55 (Valve's "remote call failed") ends every CEG start whose service is
                // registered (Call of Duty: Black Ops, Saints Row, Just Cause 2); the auto-tune rule dock-ceg-result-55 names it the same way.
                case 55: return "Steam's copy protection couldn't start the game: Steam couldn't get this game's executable ready for your account (code 55). This is a known stop: Steam's request to build it failed, and no BCD setting is known to change it."
                default: return "Steam's copy protection couldn't start the game: Steam couldn't get this game's executable ready for your account (code \(fields["ceg-result"] ?? "?"))."
                }
            }
            // The host sets the Steam client's discovery entries in the Windows registry (HKCU Software\Valve\Steam\ActiveProcess and
            // HKLM Software\Valve\Steam) before it asks Steam to start the game; 42 is its answer when one of those cannot be
            // opened or written (madeira-dock src/launch.c). Steam was never asked, so no game ran (Firewatch GSDQM2, CEEP3E).
            if result == 42 {
                return "BCD Dock couldn't open or write the Steam client entries it needs in BCD's Windows registry, so it never asked Steam to start this game (code 42). Export the log, then try again."
            }
            if result == 45 || result == 48, fields["launch-option-missing"] != nil || fields["launch-option-invalid"] != nil {
                return "Steam won't accept this game's launch option. Refresh the Steam library, then try again."
            }
            if result == 45 || result == 48, let error = fields["launch-client-error"].flatMap(Int.init),
               (22...23).contains(error), fields["launch-config-wait"] != nil {
                return "After signing in, Steam was still loading this game's configuration. Give it a minute, then start the game again."
            }
            // The host asked again for up to three minutes while Steam still counted
            // another session of this account as playing (refusal 35); it still did.
            if result == 45 || result == 48, fields["launch-client-error"] == "35", fields["launch-session-wait"] != nil {
                return "Steam still reports this account as playing in another session. Quit the game on your other device (if you just closed it, allow a few minutes), then start it again."
            }
            if result == 45 || result == 48, let error = fields["launch-client-error"].flatMap(Int.init),
               let reason = Self.launchRefusal(error, waited: result == 48, installLacksMemory: MemoryRights.lacking) {
                return reason
            }
            return "The Steam launch from BCD Dock failed (code \(result)). Export the log, then try again."
        }

        /// Valve's own launch refusal (EAppUpdateError) in words. Numbers only
        /// come from the host report; nothing here changes what Steam decided.
        static func launchRefusal(_ error: Int, waited: Bool, installLacksMemory: Bool = false) -> String? {
            switch error {
            case 5: return "Steam couldn't confirm that the signed-in account has a license for this game."
            case 6, 21: return "To start this game, Steam has to reach its servers, and it couldn't. Check your connection, then try again."
            case 16: return "Steam sees this game as already running. Quit it first, then try again."
            case 17, 19, 20:
                return waited
                    ? "Content this game needs is still missing: Steam couldn't finish installing it. Try again later."
                    : "Before this game can start, Steam has to install or update content it depends on. Start the game again and BCD Dock will wait for Steam."
            case 18: return "Steam doesn't consider this game installed."
            case 28: return "Steam can't find this game's executable."
            // Steam's CreateProcess for the game failed: Madeira could not load the program.
            case 29: return "Steam launched this game's program, but BCD couldn't load it (Steam reports an invalid platform). The log says why, so export it."
            case 22, 23, 24: return "Steam hit a problem reading this game's configuration. Try again."
            case 25: return "Steam lists this game as not released yet."
            case 26: return "Steam lists this game as unavailable in your region."
            case 35: return "Steam reports this account as playing in another session. Quit the game on your other device, then start it here again."
            // Round 624 (agent N S13; QH4XFC, V71SS6, 9Z31S2): the 32-bit program's 4 GB address window was refused ("[wow-window] refused");
            // 58 of 61 logs with this error are installs without the limit. With the limit the generic words stay (other causes).
            case 46:
                return installLacksMemory
                    ? "BCD couldn't start this game's program: it needs more address space than this install has, because it lacks the increased "
                        + "memory limit (32-bit games need the most). Add the limit with GetMoreRam (with SideStore or LiveContainer), or sign BCD with "
                        + "a paid developer account."
                    : nil
            default: return nil
            }
        }
    }

    static let reportAllowed: Set<String> = ["probe-start-bits", "client-machine", "client-pe-timestamp",
        "load-client-begin", "load-client-error", "public-client021-present", "engine005-present",
        "engine-factory-result", "session-client-adapter", "session-unsupported-client", "session-user-method-mismatch",
        "session-private-abi-verified", "session-login-disabled", "session-native-handoff-invalid",
        "session-native-handoff-app-mismatch", "session-handoff-stage", "session-handoff-error",
        "session-account-input-invalid", "session-app-input-invalid",
        "session-native-token-submitted", "session-logon-start-result", "session-connection-result",
        "session-authenticated-online", "session-requested-app-entitled", "session-subscription-count",
        "session-requested-app-listed", "session-auth-test-result",
        "session-online-subscription-count", "session-online-app-zero-query", "session-online-callback-id",
        "session-timeout-subscription-count", "session-timeout-app-listed", "session-timeout-still-online",
        "session-online-blip", "session-online-blips", "session-online-lost", "session-entitlement-source",
        "session-offline-abi", "session-offline-requested", "session-offline-can", "session-offline-logon-result",
        "session-offline-logon-retry", "session-offline-logon-state", "session-offline-callback-id",
        "session-offline-entitled", "session-offline-listed", "session-offline-timeout-state",
        "session-offline-fallback", "session-offline-ready",
        "launch-client-error", "launch-option-index", "launch-option-invalid", "launch-option-missing",
        "launch-update-wait", "launch-update-retry", "launch-update-ready",
        "launch-config-wait", "launch-config-gave-up", "launch-session-wait", "launch-session-gave-up",
        "ceg-request", "ceg-request-result", "ceg-request-busy", "ceg-server-result", "ceg-job-result",
        "ceg-finished-jobs", "ceg-result", "ceg-disabled", "ceg-unsupported-client",
        "ceg-scm", "ceg-scm-started", "ceg-scm-error", "ceg-service-registered", "ceg-service-install", "ceg-service-stop", "ceg-scm-stopped",
        "game-scm", "game-scm-timeout", "launch-keep-alive",
        // round 624 (Steam 113; tools/patch-dock-steam-readback.py): the published values read back while the game starts (bits:
        // 1 pid, 2 ActiveUser, 4 SteamPID intact, 8 a read failed), the wait loop's longest gap, the experiments' writes.
        "launch-discovery-readback", "launch-loop-gap-ms", "launch-discovery-readback-running", "launch-discovery-reasserted",
        // The host's own account of the launch (madeira-dock src/launch.c): when it published the Steam client
        // for the game to find, asked Valve's client to start it, saw it run, saw it end, and gave the
        // registry values back. A Rockstar launcher that says "Steam failed to initialize" while the host
        // reported a launch without error (20 of 40 sessions, 2026-10-19/20) needs these to be told apart.
        "launch-install-directory-verified", "launch-install-directory-mismatch",
        "launch-client-discovery-published", "launch-request-submitted", "launch-game-running",
        "launch-game-ended", "launch-discovery-restored", "launch-host-result",
        "shutdown-begin", "shutdown-complete", "probe-result"]
    /// The host's report rounds (madeira-dock src/main.c).
    static let reportRounds: Set<String> = ["ml1820", "ml1830", "ml1860", "ml1870", "ml1970", "ml1990", "ml2000", "ml2011", "ml2015", "ml2016"]

    static func parseReport(_ data: Data) -> Report {
        guard data.count <= 32768, let text = String(data: data, encoding: .utf8) else { return Report() }
        var report = Report()
        // Ignore a partial final line until the writer completes and flushes it.
        for line in text.replacingOccurrences(of: "\r\n", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false).dropLast() {
            let parts = line.split(separator: " ", omittingEmptySubsequences: false)
            guard parts.count == 3, parts[0] == "[steam-host]", reportRounds.contains(String(parts[1])) else { continue }
            let field = parts[2].trimmingCharacters(in: .newlines).split(separator: "=", maxSplits: 1)
            guard field.count == 2 else { continue }
            let key = String(field[0]), value = String(field[1])
            if key == "client-sha256", value.utf8.count == 64,
               value.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) {
                report.fields[key] = value
            } else if key == "session-callback-id", let number = Int32(value) {
                // Valve's callback IDs after sign-in (the host reports the first 16), in
                // order, as one field: the poll logs a field only when it changes.
                let ids = report.fields["session-callback-ids"]
                if (ids?.split(separator: ",").count ?? 0) < 16 {
                    report.fields["session-callback-ids"] = (ids.map { $0 + "," } ?? "") + String(number)
                }
            } else if reportAllowed.contains(key), let number = Int32(value) {
                report.fields[key] = String(number)
            }
        }
        return report
    }

    @MainActor private static var lastReport = Report()

    /// Reads C:\madeira-dock.txt and logs fields that changed ([dock-report]).
    @MainActor static func pollReport() -> Report {
        let url = drive.appendingPathComponent("madeira-dock.txt")
        guard let file = try? FileHandle(forReadingFrom: url) else { return lastReport }
        defer { try? file.close() }
        guard let data = try? file.read(upToCount: 32769), data.count <= 32768 else { return lastReport }
        let report = parseReport(data)
        for key in report.fields.keys.sorted() where report.fields[key] != lastReport.fields[key] {
            SteamLog.event("[dock-report] \(key)=\(report.fields[key]!)")
        }
        // The host ended: its whole report, in order, so the sequence and repeats of
        // its numeric fields are in the diagnostic log (each line is `[steam-host]
        // <round> <field>=<number>`; nothing else is in the file).
        if report.fields["probe-result"] != nil, lastReport.fields["probe-result"] == nil {
            let lines = String(decoding: data, as: UTF8.self).split(separator: "\n").filter { $0.hasPrefix("[steam-host] ") }
            SteamLog.event("[dock-report-file] lines=\(lines.count)")
            for line in lines.prefix(400) { SteamLog.event("[dock-report-file] " + line.trimmingCharacters(in: .whitespacesAndNewlines)) }
        }
        lastReport = report
        return report
    }

    // MARK: Launch

    private static var transferURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("MadeiraDock", isDirectory: true).appendingPathComponent("launch.auth")
    }

    /// Removes an unconsumed transfer (session end, sign-out, app start).
    @MainActor static func cleanup() {
        if let url = transferURL { try? FileManager.default.removeItem(at: url) }
        unsetenv("MADEIRA_DOCK_AUTH_FILE")
        unsetenv("MADEIRA_DOCK_OFFLINE")
    }

    /// Writes the one-use transfer to protected Application Support storage
    /// (complete file protection, mode 0600, excluded from backups, created
    /// exclusively) and puts only its guest path in the environment. No
    /// token, account name or path is logged.
    @MainActor static func writeHandoff(account: String, token: String, appID: Int) throws {
        cleanup()
        lastReport = Report()
        let report = drive.appendingPathComponent("madeira-dock.txt")
        if FileManager.default.fileExists(atPath: report.path) { try FileManager.default.removeItem(at: report) }
        guard let url = transferURL else { throw DockError.message("BCD Dock can't reach its private transfer folder.") }
        var data = try envelope(account: account, token: token, steamID: subject(token), appID: appID)
        defer { data.resetBytes(in: data.startIndex..<data.endIndex) }
        let fm = FileManager.default
        var folder = url.deletingLastPathComponent()
        try fm.createDirectory(at: folder, withIntermediateDirectories: true,
                               attributes: [.posixPermissions: 0o700, .protectionKey: FileProtectionType.complete])
        guard try folder.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink != true else {
            throw DockError.message("BCD Dock won't use its transfer folder: it isn't a plain folder.")
        }
        var values = URLResourceValues(); values.isExcludedFromBackup = true
        try folder.setResourceValues(values)
        let descriptor = open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard descriptor >= 0 else { throw DockError.message("BCD couldn't write the private sign-in transfer for Dock.") }
        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        do {
            try fm.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: url.path)
            try file.write(contentsOf: data); try file.close()
            setenv("MADEIRA_DOCK_AUTH_FILE", handoffGuestPath(url), 1)
            SteamLog.event("[dock-handoff] protected-file-ready=1")
        } catch { try? file.close(); cleanup(); throw error }
    }

    /// The host's environment for one launch.
    static func configure(_ game: DockGame, launchOption: UInt32 = 0) {
        for key in ["MADEIRA_STEAM_HOST_PROBE", "MADEIRA_STEAM_HOST_SESSION", "MADEIRA_STEAM_HOST_LOGIN", "MADEIRA_STEAM_HOST_LAUNCH"] {
            setenv(key, "1", 1)
        }
        setenv("MADEIRA_STEAM_HOST_APPID", String(game.id), 1)
        setenv("MADEIRA_STEAM_HOST_LAUNCH_OPTION", String(launchOption), 1)
        setenv("MADEIRA_STEAM_HOST_CLIENT_DIR", SteamRuntimeFiles.windowsRoot, 1)
        setenv("MADEIRA_STEAM_HOST_EXPECTED_INSTALL", game.windowsInstallPath, 1)
        setenv("MADEIRA_STEAM_HOST_LOG", "C:\\madeira-dock.txt", 1)
        // The install record lists per-user executables: Dock asks Valve's client to prepare
        // them before it launches. env.MADEIRA_DOCK_CEG = 0 never asks.
        if game.customExecutables && SteamSignIn.flag("MADEIRA_DOCK_CEG", default: true) {
            setenv("MADEIRA_STEAM_HOST_CEG", "1", 1)
        } else {
            unsetenv("MADEIRA_STEAM_HOST_CEG")
        }
        // Never let a stale environment choose the PC cached-account test path.
        unsetenv("MADEIRA_STEAM_HOST_ACCOUNT"); unsetenv("MADEIRA_STEAM_HOST_STEAMID")
        // Valve's client unloads DLLs while it starts and the loader can map a different
        // DLL of the same size at the same address. The engine's opt-in image-retire
        // switch (MADEIRA_JIT_IMAGE_RETIRE, off for every other session) gives that DLL
        // fresh code instead of the unloaded one's translation. Dock sessions turn it on;
        // an engine without the switch ignores the variable. env.MADEIRA_DOCK_IMAGE_RETIRE = 0
        // leaves it off, and an explicit env.MADEIRA_JIT_IMAGE_RETIRE in bcd.cfg still wins.
        let retire = SteamSignIn.flag("MADEIRA_DOCK_IMAGE_RETIRE", default: true)
        if retire { setenv("MADEIRA_JIT_IMAGE_RETIRE", "1", 1) }
        SteamLog.event("[dock-launch] image-retire=\(retire ? 1 : 0)")
        // While the loader loads Valve's client and its many imports, the emulator's
        // image-map handler can commit a page of its own heap under its interval lock; the
        // commit's notification then waits for that same lock and the host parks for good
        // (no report after load-client-begin). Wine's opt-in MADEIRA_IMAGE_MAP_GUARD (off for
        // every other session) keeps the emulator's own memory calls un-notified there, as
        // on the syscall path. Dock sessions turn it on; ntdll without the switch ignores it.
        // env.MADEIRA_DOCK_IMAGE_MAP_GUARD = 0 leaves it off.
        let guardMap = SteamSignIn.flag("MADEIRA_DOCK_IMAGE_MAP_GUARD", default: true)
        if guardMap { setenv("MADEIRA_IMAGE_MAP_GUARD", "1", 1) }
        SteamLog.event("[dock-launch] image-map-guard=\(guardMap ? 1 : 0)")
        DockOffline.configure(appID: game.id)
    }

    /// Wine's explorer opens a virtual desktop and starts the host in it. With
    /// `installers` (DockInstallers.script), cmd.exe first runs the game's one-time
    /// installs in the same session, then the host. No token is quoted: MADEIRA_ARGS is
    /// split at spaces and passed on as is, so quote characters would reach Wine
    /// literally, and none of these paths contains a space.
    /// madeira-bcd: `host` false runs only the installers; the session ends after them
    /// (DockInstallers.installsOnly).
    static func launchArguments(width: Int, height: Int, installers: String? = nil, host: Bool = true) -> String {
        guard let installers else { return "/desktop=madeira,\(width)x\(height) \(executable)" }
        guard host else { return "/desktop=madeira,\(width)x\(height) C:\\windows\\system32\\cmd.exe /c call \(installers)" }
        return "/desktop=madeira,\(width)x\(height) C:\\windows\\system32\\cmd.exe /c call \(installers) & \(executable)"
    }

    /// Set on the main actor right before a Dock launch; read (and cleared) once by the
    /// launch worker, so only that launch sees it.
    nonisolated(unsafe) private static var launchRequest: (dock: Bool, compact: Bool) = (false, false)
    static func requestLaunch(compactPool: Bool) { launchRequest = (true, compactPool) }
    /// madeira-bcd: a Dock launch is asked for and not yet taken (DeviceCapability's gate reads it).
    static var launchPending: Bool { launchRequest.dock }
    static func takeLaunchRequest() -> (dock: Bool, compact: Bool) {
        defer { launchRequest = (false, false) }
        return launchRequest
    }
}

/// Offline play for Steam games started through Madeira Dock.
///
/// Valve's client can log an account on without a connection, with an offline
/// logon ticket Steam issues to it during an online logon (desktop Steam's
/// Offline Mode). The host asks the client for exactly that (madeira-dock
/// docs/OFFLINE.md): Madeira decides nothing about ownership and stores no
/// ticket. What lives here is only what the app needs around it:
///
/// - whether the device has a network path, so a start without one asks for the
///   offline logon straight away (`MADEIRA_DOCK_OFFLINE=1`) instead of waiting
///   out a connection that cannot happen;
/// - a note of the last online start in which Valve's client confirmed a licence
///   and said the account can log on offline (the host's
///   `session-offline-ready=1`), for the Game details mark. Steam's offline
///   sign-in belongs to the ACCOUNT: with it, Valve's client answers the licence
///   question for any game from the list it cached, whether or not that game was
///   ever started online. So the note is one date for the account, plus the games
///   started online since, which matters only for a game whose per-user
///   executable Steam prepares on its first start (that needs a connection). It
///   is a display aid: an offline start is always decided by Valve's client.
///
/// `env.MADEIRA_DOCK_OFFLINE_LOGON = 0` removes the feature in the app and in
/// the host (which reads the same variable).
enum DockOffline {
    static let enabled = SteamSignIn.flag("MADEIRA_DOCK_OFFLINE_LOGON", default: true)

    private static let monitor = NWPathMonitor()
    private static var started = false
    /// Starts the path monitor. Called at app start, so the path is current by
    /// the time anything is launched.
    static func begin() {
        guard enabled, !started else { return }
        started = true
        monitor.start(queue: DispatchQueue(label: "madeira.dock-offline.path"))
    }
    /// No usable network path right now. False until the monitor has started.
    static var networkDown: Bool { started && monitor.currentPath.status != .satisfied }

    private static let key = "madeiraDockOfflineVerified", accountKey = "madeiraDockOfflineAccount"
    /// When Valve's client last reported, in an online start, that it can log this
    /// account on offline; nil if it never has on this installation.
    static var accountSaved: Date? {
        guard enabled else { return nil }
        var t = UserDefaults.standard.double(forKey: accountKey)
        // A build that kept only the per-game dates: the newest of those is the account's.
        if t <= 0, let all = UserDefaults.standard.dictionary(forKey: key) as? [String: Double] {
            t = all.values.max() ?? 0
        }
        return t > 0 ? Date(timeIntervalSince1970: t) : nil
    }
    /// When this game last started online in such a session; nil if it never has.
    static func verified(_ appID: Int) -> Date? {
        guard enabled, let all = UserDefaults.standard.dictionary(forKey: key) as? [String: Double],
              let t = all[String(appID)] else { return nil }
        return Date(timeIntervalSince1970: t)
    }
    /// What Game details says about a game the Dock starts.
    enum Mark: Equatable {
        case ready(Date)        // Steam can sign in offline; this game needs nothing more
        case needsFirstStart    // Steam can sign in offline, but prepares this game's program online first
        case needsOnline        // Steam has no offline sign-in saved yet
    }
    /// `preparedOnline`: the game's install record lists per-user executables,
    /// which Valve's client prepares on the first start, over the network.
    static func mark(_ appID: Int, preparedOnline: Bool) -> Mark {
        guard let saved = accountSaved else { return .needsOnline }
        if preparedOnline, verified(appID) == nil { return .needsFirstStart }
        return .ready(saved)
    }
    private static func save(_ appID: Int) {
        let now = Date().timeIntervalSince1970
        var all = (UserDefaults.standard.dictionary(forKey: key) as? [String: Double]) ?? [:]
        all[String(appID)] = now
        UserDefaults.standard.set(all, forKey: key)
        UserDefaults.standard.set(now, forKey: accountKey)
    }
    /// Sign-in removed or changed: the notes belonged to that account.
    static func clear() {
        UserDefaults.standard.removeObject(forKey: key)
        UserDefaults.standard.removeObject(forKey: accountKey)
    }

    /// The app the running Dock session was started for.
    private(set) static var sessionApp: Int?
    private static var sessionMarked = false

    /// Part of MadeiraDock.configure: tells the host when there is no network.
    static func configure(appID: Int) {
        sessionApp = appID
        sessionMarked = false
        let offline = enabled && networkDown
        if offline { setenv("MADEIRA_DOCK_OFFLINE", "1", 1) } else { unsetenv("MADEIRA_DOCK_OFFLINE") }
        SteamLog.event("[dock-offline] enabled=\(enabled ? 1 : 0) network=\(networkDown ? "down" : "up") request=\(offline ? 1 : 0) account-saved=\(accountSaved != nil ? 1 : 0) game-started-online=\(verified(appID) != nil ? 1 : 0)")
    }

    /// Follows the host's report (MadeiraDockModel.watchReport): an online start
    /// in which Valve's client confirmed the licence and reported that it can log
    /// the account on offline saves the game for offline play.
    @MainActor static func observe(_ fields: [String: String]) {
        guard enabled, !sessionMarked, let app = sessionApp,
              fields["session-offline-requested"] == nil,
              fields["session-requested-app-listed"] == "1",
              fields["session-offline-ready"] == "1" else { return }
        sessionMarked = true
        save(app)
        SteamLog.event("[dock-offline] Valve's client can log this account on offline: saved")
        NotificationCenter.default.post(name: changed, object: nil)
    }
    static let changed = Notification.Name("MadeiraDockOfflineChanged")
}
