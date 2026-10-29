// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers

// A game's page (docs/BCD-UI-ARCHITECTURE.md), opened full screen from Home and Library: the
// game's own art behind everything, its logo, the one thing to do next (Play, Install, Update,
// Pause or Resume), the lines that say what stands in the way, the facts, and Options (a panel
// from the right, a sheet on an upright phone) with every setting the game has, in sections.
//
// What the store has, the page reads from the store, and the main actions go through
// BCDActions. What it lacks (the game's settings, Steam Cloud, recommended settings, auto-tune,
// saves, logs) comes through BCDGameDetailBackend: BCDGamePageLive in the app, the only file of
// the page that touches the engine, and BCDGamePageSample in the preview app.

// MARK: - The page's rules (no views: tests/host/check-bcd-game-page.py compiles this part)

/// One of a setting's choices.
struct BCDGameChoice: Identifiable, Equatable {
    var value: String
    var label: String
    /// A heading over the first choice of a group ("16:9 widescreen").
    var group: String? = nil
    /// Asked before this choice is made (one that removes something).
    var question: BCDGameQuestion? = nil
    /// A shorter label for a key cap ("0" for "Numpad 0").
    var cap: String? = nil
    var id: String { value }
}

/// A quick pick under a text setting: tapping it puts its value into the text (a launch flag), or takes it out again.
struct BCDGameChip: Identifiable, Equatable {
    var value: String
    /// What it is for, in a few words.
    var note: String
    var id: String { value }
}

/// A screen of Options that a row opens.
enum BCDGameScreen: Equatable {
    /// Another section, reached from this row (Controller binds, under Controls).
    case section(String)
    /// The game looked up in Steam's store, for its name and pictures.
    case storeSearch
    /// A picture from Files as the game's cover.
    case coverPicture
    /// A picture from Files as the Wine desktop's wallpaper (round 604).
    case wallpaperPicture
}

/// One setting of a game, as a row of Options.
struct BCDGameSetting: Identifiable, Equatable {
    enum Kind: Equatable {
        /// One of a list; the setting's own page lists them.
        case choice([BCDGameChoice])
        /// On ("1") or off ("0").
        case toggle
        /// Typed text; `prompt` fills the empty field.
        case text(prompt: String)
        /// Shown, not changed: the program that starts, the App ID.
        case info
        /// Something done at once (`question` asks first).
        case action
        /// Opens a screen of its own; `value` is what the row says about it.
        case opens(BCDGameScreen)
    }

    var id: String
    var title: String
    var kind: Kind
    var value: String = ""
    var defaultValue: String = ""
    /// What it does, in a sentence or two.
    var note: String? = nil
    /// Why it cannot change now; nil: it can.
    var locked: String? = nil
    var question: BCDGameQuestion? = nil
    var destructive = false
    /// A heading over this setting, which starts a run of them ("Face buttons").
    var heading: String? = nil
    /// Quick picks under a text setting's field (launch flags, round 604); empty for every other setting.
    var chips: [BCDGameChip] = []

    var isOn: Bool { value == "1" }
    var isDefault: Bool { value == defaultValue }
    /// A choice, a switch or what a screen holds away from its default (a dot marks it).
    var changed: Bool {
        switch kind {
        case .choice, .toggle, .opens: return !isDefault
        default: return false
        }
    }
    var choices: [BCDGameChoice] {
        if case .choice(let list) = kind { return list }
        return []
    }

    /// The value as its row shows it: "Default · 60 FPS" until a choice is changed.
    var summary: String {
        switch kind {
        case .choice(let list):
            let label = list.first { $0.value == value }?.label ?? value
            if isDefault && !label.localizedCaseInsensitiveContains("default") { return "Default · " + label }
            return label
        case .toggle: return isOn ? "On" : "Off"
        case .text: return value.isEmpty ? "None" : value
        case .info, .opens: return value
        case .action: return ""
        }
    }
}

/// A section of Options: Display, Direct3D, Compatibility, Controls, Launch, Steam, Library, and
/// Controller binds under Controls.
struct BCDGameSettingGroup: Identifiable, Equatable {
    var id: String
    var title: String
    var symbol: String
    var settings: [BCDGameSetting]
    /// A line at the top of the section (how its settings work together, a warning).
    var note: String? = nil
    /// The section's row says this; nil: the changed settings, else "As BCD sets it".
    var headline: String? = nil
    /// The section whose row opens this one; nil: a row of Options' own list.
    var parent: String? = nil

    var summary: String {
        if let headline { return headline }
        let changed = settings.filter(\.changed)
        guard !changed.isEmpty else { return "As BCD sets it" }
        let words = changed.prefix(2).map { $0.kind == .toggle ? "\($0.title) \($0.isOn ? "on" : "off")" : $0.summary }
        return words.joined(separator: " · ") + (changed.count > 2 ? " · \(changed.count - 2) more" : "")
    }
}

/// A game's Steam Cloud saves, as far as the page tells them.
enum BCDCloudSaves: Equatable {
    case signedOut
    case notChecked
    case checking
    case moving(up: Bool, done: Int, total: Int)
    case synced(matching: Int)
    /// Saves on one side only, waiting for the next sync.
    case waiting(Int)
    /// Saves that differ on both sides: the player picks which to keep.
    case differ(Int)
    case failed(String)

    var summary: String {
        switch self {
        case .signedOut: return "Sign in to Steam"
        case .notChecked: return "Not checked yet"
        case .checking: return "Checking…"
        case .moving(let up, let done, let total): return up ? "Sending \(done) of \(total)" : "Receiving \(done) of \(total)"
        case .synced(let matching): return matching == 0 ? "No saves yet" : "In sync"
        case .waiting(let count): return "\(count) waiting"
        case .differ(let count): return "\(count) differ"
        case .failed: return "Couldn't sync"
        }
    }

    var explanation: String {
        switch self {
        case .signedOut: return "Cloud saves need a Steam sign-in."
        case .notChecked: return "BCD hasn't compared this game's saves with Steam Cloud yet."
        case .checking: return "Comparing the saves on this device with Steam Cloud."
        case .moving(let up, let done, let total):
            return up ? "Sending saves to Steam Cloud: \(done) of \(total)." : "Bringing saves from Steam Cloud: \(done) of \(total)."
        case .synced(let matching):
            return matching == 0 ? "Neither Steam Cloud nor this device has saves of this game yet."
                : "This device and Steam Cloud hold the same \(BCDGameText.saves(matching))."
        case .waiting(let count): return "\(BCDGameText.saves(count).capitalizedFirst) on one side only go across at the next sync."
        case .differ(let count):
            return "Steam Cloud and this device disagree on \(BCDGameText.saves(count)). BCD changes neither until you pick the copies to keep."
        case .failed(let why): return why
        }
    }
}

/// A save that differs between Steam Cloud and this device, with what each side holds.
struct BCDCloudConflict: Identifiable, Equatable {
    var name: String
    var cloudDate: Date?
    var cloudBytes: Int64
    /// nil: missing on this device, though it was synced here before.
    var deviceDate: Date?
    var deviceBytes: Int64 = 0
    var id: String { name }

    /// "Steam Cloud: 18 Oct 2026 at 14:05 · 2 MB · newer", and the same for this device.
    var lines: (cloud: String, device: String) {
        func when(_ date: Date?) -> String { date.map { BCDFormat.dateTime($0) } ?? "date unknown" }
        let cloudNewer = deviceDate != nil && (cloudDate ?? .distantPast) > (deviceDate ?? .distantPast)
        let deviceNewer = deviceDate != nil && (deviceDate ?? .distantPast) > (cloudDate ?? .distantPast)
        let cloud = "Steam Cloud: \(when(cloudDate)) · \(BCDFormat.bytes(cloudBytes))" + (cloudNewer ? " · newer" : "")
        guard deviceDate != nil else { return (cloud, "This device: missing, though it was synced here before") }
        return (cloud, "This device: \(when(deviceDate)) · \(BCDFormat.bytes(deviceBytes))" + (deviceNewer ? " · newer" : ""))
    }
}

/// A game in Steam's store, found by name (Find on Steam).
struct BCDStoreMatch: Identifiable, Hashable {
    var id: Int
    var name: String
    /// The store's small picture of it.
    var picture: URL? = nil
}

/// What went wrong, in BCD's words.
struct BCDGameError: LocalizedError {
    var message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// How a game leaves this device, if it can from here.
enum BCDGameRemoval: Equatable {
    case none
    /// Its files go; `frees` is their size when known.
    case uninstall(frees: Int64?)
    /// A game added from Files: only its place in the library goes, its files stay.
    case removeFromLibrary
}

/// Auto-tune for one game (nil in BCDGameDetails while Settings › Auto-tune is off).
struct BCDGameTuning: Equatable {
    var on: Bool
    var status: String?
    var canUndo: Bool
    /// The lines it kept, which can go into the game's own config.
    var found: [String] = []
    /// What it tried, newest first: a title and a line under it each.
    var history: [(title: String, detail: String)] = []

    static func == (a: BCDGameTuning, b: BCDGameTuning) -> Bool {
        a.on == b.on && a.status == b.status && a.canUndo == b.canUndo && a.found == b.found
            && a.history.map(\.title) == b.history.map(\.title) && a.history.map(\.detail) == b.history.map(\.detail)
    }
}

/// The newest session log of a game, which Report a problem sends.
struct BCDGameLog: Hashable {
    var file: URL
    var name: String
    var date: Date
    var bytes: Int
}

struct BCDSavesBackup: Equatable {
    var file: URL
    var files: Int
    var bytes: Int64
    var leftOut: Int
}

/// The settings BCD recommends for a game: a tested list, or the standard one for any game.
struct BCDGameRecommendation: Equatable {
    /// The game's own list ("Red Dead Redemption 2"); nil: BCD's standard settings.
    var title: String?
    /// The version of the list of recommended settings in use, when known.
    var version: Int? = nil
    /// The day that list was made.
    var made: Date? = nil
    /// Where it comes from: built into this version of BCD, or downloaded.
    var downloaded = false
    /// When BCD last heard from the online list; nil: not yet.
    var checked: Date? = nil
    /// The game's config is the list as BCD wrote it.
    var inUse = false

    /// "List 3 of 18 Oct 2026, built into this version of BCD. Checked online 2 h ago."
    var source: String {
        var line = version.map { "List \($0)" } ?? "The list"
        if let made { line += " of " + BCDFormat.fullDate(made) }
        line += downloaded ? ", downloaded." : ", built into this version of BCD."
        if let checked { line += " Checked online \(BCDFormat.ago(checked))." }
        return line
    }
}

/// Get the latest recommended settings: what the newest list means for this game.
enum BCDRecommendedRefresh: Equatable {
    /// The newest list is in use and this game has it.
    case upToDate
    /// A newer list came and this game took it: this many of its settings changed.
    case updated(changes: Int)
    /// This game's settings differ from the newest list in this many places (its own changes, or
    /// a list it does not follow): BCD asks before it replaces them.
    case available(changes: Int)
    /// No list came. `builtIn`: the built-in list is in use, else the last one downloaded;
    /// `nothingOnline`: there is no list online yet.
    case offline(builtIn: Bool, nothingOnline: Bool)
    /// A list came that did not pass BCD's checks; the one in use stays.
    case rejected

    /// The line under the button; nil when BCD asks instead.
    var line: String? {
        switch self {
        case .upToDate: return "Up to date"
        case .updated(let changes): return "Updated: \(changes) change\(changes == 1 ? "" : "s")"
        case .available: return nil
        case .offline(let builtIn, let nothingOnline):
            return (nothingOnline ? "No list online yet" : "Offline") + ": using the \(builtIn ? "built-in" : "downloaded") list"
        case .rejected: return "The list online didn't pass BCD's checks: the one in use stays"
        }
    }
}

/// What the page shows beyond BCDGame. Every field may be missing: the page then leaves out
/// the line, the row or the section.
struct BCDGameDetails: Equatable {
    var developer: String? = nil
    /// "64-bit · Direct3D 11".
    var program: String? = nil
    var sizeOnDisk: Int64? = nil
    /// A Steam game that is not installed: about how much it downloads and how much it takes on this device
    /// (Steam's product info, selected depots only; nil when Steam states none).
    var downloadBytes: Int64? = nil
    var installBytes: Int64? = nil
    /// A game that is not installed has settings that matter before the install (a Steam game's language): Options opens
    /// with them, and Options is offered although the game has no settings of its own yet.
    var offersDownloadOptions = false
    var lastPlayed: Date? = nil
    var minutesPlayed: Int? = nil
    /// "Recommended", "Standard", "Your own", "Auto-tune on".
    var settings: String? = nil
    /// Why Play would not start, in the engine's words.
    var blockers: [String] = []
    /// The game's settings, in sections: filled only while Options is open (memory comes first).
    var groups: [BCDGameSettingGroup] = []
    var cloud: BCDCloudSaves? = nil
    /// The saves Steam Cloud and this device disagree on.
    var cloudConflicts: [BCDCloudConflict] = []
    /// Why the last sync stopped, beside what it compared.
    var cloudProblem: String? = nil
    /// The last sync: saves received (and how many replaced ones were backed up first), sent.
    var cloudReceived = 0
    var cloudBackedUp = 0
    var cloudSent = 0
    var canRepair = false
    /// How many settings the game's own config holds; nil: no config to edit.
    var configSettings: Int? = nil
    var recommendation: BCDGameRecommendation? = nil
    var canReset = false
    var tuning: BCDGameTuning? = nil
    var shortcut = false
    var saves = false
    var report = false
    /// Said before a log is sent: on an install without the increased memory limit, what fixes it (MemoryRights.swift).
    var sendLogAdvice: String? = nil
    var removal: BCDGameRemoval = .none
    /// A download runs for a game that is installed: an update, which Stop only stops.
    var updating = false
    /// The game's page in its store, for a game from a store.
    var storePage: URL? = nil
    /// What the store says the game may need that BCD does not install for it (GOG's runtimes).
    var storeNote: String? = nil
    /// Why BCD cannot install this game at all (Epic: anti-cheat, another company's launcher, a check BCD
    /// cannot pass yet): Install gives way to the reason.
    var installBlocker: String? = nil
    /// What to know before installing a game that may not start (Epic: it must be online): shown beside Install.
    var installWarning: String? = nil
    /// The game's last start failed because a .NET program found no Wine Mono, and this device
    /// still has none: the page says where to download it.
    var needsWineMono = false
    /// What the game's program files import that this device does not have yet, in a sentence or two (BCDReadinessLive.swift, round 604); nil: nothing.
    var readiness: String? = nil
    /// A game that is not installed, and the owned DLC it has files for: what the question before its first install
    /// asks (BCDGameDLC.swift). nil: nothing to ask (no DLC, DLC support off, or the player was asked already).
    var dlcOffer: BCDDLCOffer? = nil
    /// The lookup behind `dlcOffer` is still running (Install waits for it), or failed (Install does not wait or ask).
    var dlcCheck: BCDDLCCheck = .none

    /// Check and repair files for this game: what the backend offers, and never for a GOG game
    /// (its file check comes with a later version of BCD, as its updates do).
    func offersRepair(_ game: BCDGame?) -> Bool { canRepair && game?.store != .gog }

    func setting(_ id: String) -> (group: BCDGameSettingGroup, setting: BCDGameSetting)? {
        for group in groups {
            if let setting = group.settings.first(where: { $0.id == id }) { return (group, setting) }
        }
        return nil
    }
}

/// The game page's data and actions beyond BCDStore and BCDActions.
@MainActor protocol BCDGameDetailBackend: AnyObject {
    /// Set by the open page: anything it shows may have changed (a sync moved on, a setting was
    /// saved elsewhere), so it reads `details` again.
    var onChange: (() -> Void)? { get set }
    /// Set by the open page: the engine has something to say on the main screen (an error, a
    /// notice, a game starting), so the page closes and lets the shell show it.
    var onNotice: (() -> Void)? { get set }
    /// `settings`: Options is open, so the sections are built too; otherwise `groups` stays empty.
    func details(for game: BCDGame, settings: Bool) -> BCDGameDetails
    /// The page opened: what a game's page does on opening (recommended settings for a
    /// game seen for the first time, a first Steam Cloud check, the install records read again).
    func opened(_ game: BCDGame)
    /// Changes a setting (a choice's value, "1" or "0" for a switch, text) or does a section's
    /// action ("run").
    func set(_ setting: String, to value: String, for game: BCDGame)
    func syncCloud(_ game: BCDGame)
    func keepCloudSaves(_ game: BCDGame, fromCloud: Bool)
    func resetToRecommended(_ game: BCDGame)
    /// Looks for the newest recommended settings. A remote list replaces the built-in one here.
    func refreshRecommendedSettings(for game: BCDGame) async -> BCDRecommendedRefresh
    /// Puts the newest recommended settings over the game's config and switches.
    func applyRecommendedSettings(_ game: BCDGame)
    func setTuning(_ on: Bool, for game: BCDGame)
    func undoTuning(_ game: BCDGame)
    /// Writes the lines auto-tune kept into the game's own config.
    func writeTuningLines(_ game: BCDGame)
    /// Auto-tune's report for this game, to share; nil: nothing to report.
    func tuningReport(for game: BCDGame) -> String?
    /// The link a Home Screen shortcut opens to start the game.
    func shortcutLink(for game: BCDGame) -> String?
    func backUpSaves() async throws -> BCDSavesBackup
    func restoreSaves(from file: URL) async throws -> Int
    func lastLog(of game: BCDGame) async -> BCDGameLog?
    /// Sends the log; the answer is the code to quote.
    /// The sender's comment for a log about to be sent (BCDLogComment): the text (empty for none), nil when they cancel.
    func logComment() async -> String?
    /// The same for a known log: when a screenshot was taken during its session the dialog offers to send that too.
    func logComment(for log: BCDGameLog) async -> String?
    /// `comment`: the sender's words for the top of the log (BCDLogComment), empty for none.
    func send(_ log: BCDGameLog, of game: BCDGame, comment: String, progress: @escaping (String) -> Void) async throws -> String
    /// The game's own config, as text (key = value lines); nil: none to edit.
    func configText(for game: BCDGame) -> String?
    func saveConfig(_ text: String, for game: BCDGame)
    /// Find on Steam: the store's games whose name matches.
    func searchStore(_ text: String) async throws -> [BCDStoreMatch]
    /// The game takes the match's name and the store's pictures (its own cover picture goes).
    func useStoreMatch(_ match: BCDStoreMatch, for game: BCDGame)
    /// A picture from Files becomes the game's cover; throws with the reason in BCD's words.
    func setCover(from file: URL, for game: BCDGame) async throws
    /// A picture from Files becomes the Wine desktop's wallpaper, cut to the game's desktop size (round 604).
    func setWallpaper(from file: URL, for game: BCDGame) async throws
}

extension BCDGameDetailBackend {
    func setWallpaper(from file: URL, for game: BCDGame) async throws { throw BCDGameError("A wallpaper cannot be set here.") }
    func logComment(for log: BCDGameLog) async -> String? { await logComment() }
}

/// Where the page finds its backend. The app's is BCDGamePageLive, which this file cannot name
/// (it touches the engine, so the preview build leaves it out): it is found by its Objective-C
/// name. The preview app sets its sample here. nil: the page shows what the store has.
@MainActor enum BCDGameDetailSource {
    static var backend: BCDGameDetailBackend? =
        (NSClassFromString("BCDGamePageLive") as? NSObject.Type)?.init() as? BCDGameDetailBackend
}

/// Everything the page can do for a game.
enum BCDGameAction: Hashable {
    case play
    case install(BCDInstallPlace)
    case update
    case pause
    case resume
    case cancelDownload
    case repair
    case enableJIT
    case signIn(BCDStoreFront)
    case uninstall
    case removeFromLibrary
    /// A setting's new value, or "run" for a section's action.
    case set(String, String)
    case resetToRecommended
    case applyRecommended(changes: Int)
    case syncCloud
    case keepCloud(fromCloud: Bool)
    case setTuning(Bool)
    case undoTuning
    case writeTuningLines
    case restoreSaves(URL)
    case sendLog(BCDGameLog)
    case saveConfig(String)
    case useStoreMatch(BCDStoreMatch)
    case setCover(URL)
    case setWallpaper(URL)
    /// Install after the question about the game's owned DLC (BCDGameDLC.swift), with exactly the DLC chosen (none: the game alone).
    case installChoosingDLC(BCDInstallPlace, chosen: [UInt32])
    /// The question's "Choose DLC…": opens the chooser for the game's owned DLC.
    case chooseDLC(BCDInstallPlace)

    /// What shows something on the main screen (the game, an alert, a sign-in, JIT's steps):
    /// the page closes first, so BCD's live store can show it there.
    var closesPage: Bool {
        switch self {
        case .play, .install, .installChoosingDLC, .repair, .enableJIT, .signIn, .uninstall, .removeFromLibrary: return true
        default: return false
        }
    }
}

/// A question before an action that deletes, replaces or sends something.
struct BCDGameQuestion: Equatable {
    var title: String
    var message: String
    var confirm: String
    var destructive: Bool
    /// A second way to say yes, shown between Cancel and the confirm button (the DLC question's "Game only").
    var alternative: String? = nil
}

extension BCDGameAction {
    /// What the page asks before doing it; nil: it goes ahead at once.
    func question(for game: BCDGame, details: BCDGameDetails) -> BCDGameQuestion? {
        switch self {
        case .uninstall:
            var frees = ""
            if case .uninstall(let bytes?) = details.removal { frees = " and frees \(BCDFormat.bytes(bytes))" }
            return BCDGameQuestion(title: "Uninstall \(game.title)?",
                                   message: "This deletes the game's files from this device\(frees). Saves kept outside its folder stay.",
                                   confirm: "Uninstall", destructive: true)
        case .removeFromLibrary:
            return BCDGameQuestion(title: "Remove \(game.title) from your library?",
                                   message: "It leaves the library at once. Its files and saves stay on this device, and you can add it again from Files.",
                                   confirm: "Remove", destructive: true)
        case .cancelDownload:
            return BCDGameQuestion(title: "Stop downloading \(game.title)?",
                                   message: game.state.isInstalled || details.updating
                                       ? "The game stays as it is; the update can start again later."
                                       : "The files downloaded so far are deleted.",
                                   confirm: "Stop download", destructive: true)
        case .resetToRecommended:
            let what = details.recommendation.map { $0.title.map { "BCD's tested settings for \($0)" } ?? "BCD's standard settings" }
            return BCDGameQuestion(title: "Go back to the recommended settings?",
                                   message: what.map { "\($0) replace this game's config and its switches. Your own changes to them are lost." }
                                       ?? "This game has no recommended settings yet, so its config is emptied.",
                                   confirm: "Reset", destructive: true)
        case .applyRecommended(let changes):
            return BCDGameQuestion(title: "Use the latest recommended settings?",
                                   message: "\(changes) change\(changes == 1 ? "" : "s") to this game's config and switches. Your own changes to them are replaced.",
                                   confirm: "Use them", destructive: true)
        case .keepCloud(let fromCloud):
            return fromCloud
                ? BCDGameQuestion(title: "Use Steam Cloud's saves?",
                                  message: "Where the copies differ, Steam Cloud's replace the ones on this device. BCD backs up this device's copies first, in Files › BCD › Steam Cloud Backups.",
                                  confirm: "Use Steam Cloud's", destructive: true)
                : BCDGameQuestion(title: "Keep this device's saves?",
                                  message: "Where the copies differ, this device's replace Steam Cloud's, for every device you play on. BCD backs up the cloud's copies first, in Files › BCD › Steam Cloud Backups.",
                                  confirm: "Keep this device's", destructive: true)
        case .undoTuning:
            return BCDGameQuestion(title: "Undo auto-tune for \(game.title)?",
                                   message: "The lines it added and what it tried so far are removed. Your own config stays.",
                                   confirm: "Undo", destructive: true)
        case .writeTuningLines:
            let lines = details.tuning?.found ?? []
            return BCDGameQuestion(title: "Keep what auto-tune found?",
                                   message: "\(lines.joined(separator: "; ")) go into this game's own config, where they stay with auto-tune off. Its history is cleared.",
                                   confirm: "Write", destructive: false)
        case .restoreSaves(let file):
            return BCDGameQuestion(title: "Put back the saves from \(file.lastPathComponent)?",
                                   message: "Files in the Windows users' folders with the same names are replaced, for every game.",
                                   confirm: "Restore", destructive: true)
        case .sendLog(let log):
            return BCDGameQuestion(title: "Send the log of \(game.title)?",
                                   message: "\(log.name), \(BCDFormat.ago(log.date)), \(BCDFormat.bytes(Int64(log.bytes))). Folder paths, names and addresses are removed on this device before it goes. You get a code to quote when you describe the problem."
                                    + (details.sendLogAdvice.map { "\n\n" + $0 } ?? ""),   // the setup first (MemoryRights.swift, set by the live page)
                                   confirm: "Send", destructive: false)
        case .set(let id, let value):
            guard let found = details.setting(id)?.setting else { return nil }
            if found.kind == .action { return found.question }
            return found.choices.first { $0.value == value }?.question
        default:
            return nil
        }
    }
}

/// Small words the page uses in several places.
enum BCDGameText {
    static func saves(_ count: Int) -> String { "\(count) save\(count == 1 ? "" : "s")" }
    static func settings(_ count: Int) -> String { count == 0 ? "None" : "\(count) setting\(count == 1 ? "" : "s")" }
    static func storeLine(_ game: BCDGame, developer: String?) -> String {
        let store = game.store == .local ? "Added by you" : game.store.title
        return [store, developer].compactMap { $0 }.joined(separator: " · ")
    }

    /// How many settings a game config's text holds: its key = value lines, one per key; blank
    /// lines and # notes do not count.
    static func configCount(_ text: String) -> Int {
        var keys = Set<String>()
        for raw in text.split(whereSeparator: \.isNewline) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.hasPrefix("#"), let equals = line.firstIndex(of: "=") else { continue }
            let key = line[..<equals].trimmingCharacters(in: .whitespaces)
            if !key.isEmpty { keys.insert(key) }
        }
        return keys.count
    }
}

/// Launch arguments' common flags, each a switch: on while the arguments hold it. Only one
/// Direct3D flag at a time, and -windowed and -fullscreen rule each other out; arguments in
/// quotes stay whole.
enum BCDLaunchFlags {
    static let flags = ["-dx11", "-dx12", "-dx10", "-dx9", "-windowed", "-fullscreen", "-nosplash"]
    /// Flags of which one at a time makes sense: asking for one takes the others out.
    private static let exclusive: [Set<String>] = [
        ["-dx9", "-dx10", "-dx11", "-dx12"],
        ["-windowed", "-fullscreen"],
        ["-force-dx9", "-force-d3d9", "-force-d3d11"],
    ]

    /// The quick picks under Launch arguments (round 604; Winlator's list of arguments that fix older and Unity games, and
    /// the flags above). A flag the program does not know is ignored by most programs; nothing here is added unless it is tapped.
    static let quick: [BCDGameChip] = [
        BCDGameChip(value: "-force-gfx-direct", note: "Unity: draws on the game's main thread. For a Unity game that freezes or shows nothing."),
        BCDGameChip(value: "-force-d3d11-singlethreaded", note: "Unity: Direct3D 11 on one thread. For a Unity game that crashes while loading."),
        BCDGameChip(value: "-force-d3d11", note: "Unity: Direct3D 11."),
        BCDGameChip(value: "-force-d3d9", note: "Unity: Direct3D 9."),
        BCDGameChip(value: "-force-dx9", note: "Unity: Direct3D 9, spelled the other way."),
        BCDGameChip(value: "/d3d9", note: "Some older games: Direct3D 9."),
        BCDGameChip(value: "-dx11", note: "Ask for Direct3D 11."),
        BCDGameChip(value: "-dx12", note: "Ask for Direct3D 12."),
        BCDGameChip(value: "-dx10", note: "Ask for Direct3D 10."),
        BCDGameChip(value: "-dx9", note: "Ask for Direct3D 9."),
        BCDGameChip(value: "-windowed", note: "Ask for a window."),
        BCDGameChip(value: "-fullscreen", note: "Ask for full screen."),
        BCDGameChip(value: "-popupwindow", note: "Unity: a borderless window."),
        BCDGameChip(value: "-nosplash", note: "Skip the splash screen."),
    ]

    /// The arguments as the program gets them: split at spaces and tabs outside quotes.
    static func words(_ arguments: String) -> [String] {
        var words: [String] = []
        var word = ""
        var quoted = false
        for character in arguments {
            if character == "\"" { quoted.toggle() }
            if (character == " " || character == "\t") && !quoted {
                if !word.isEmpty { words.append(word) }
                word = ""
            } else {
                word.append(character)
            }
        }
        if !word.isEmpty { words.append(word) }
        return words
    }

    static func has(_ arguments: String, _ flag: String) -> Bool {
        words(arguments).contains { $0.lowercased() == flag.lowercased() }
    }

    /// The arguments with the flag taken out, or put in at the end (taking out what it rules out).
    static func toggled(_ arguments: String, _ flag: String) -> String {
        let key = flag.lowercased()
        var words = words(arguments)
        if has(arguments, flag) {
            words.removeAll { $0.lowercased() == key }
        } else {
            let excluded = exclusive.first { $0.contains(key) } ?? []
            words.removeAll { excluded.contains($0.lowercased()) }
            words.append(flag)
        }
        return words.joined(separator: " ")
    }
}

/// Controller binds in keyboard-and-mouse mode: the controller's inputs in their sections, and
/// what each can press. A bind's value: "" (the default: a touch control bound to the input in
/// the control editor, else BCD's layout), "none", "mouse.left", "mouse.right", "stick.wasd",
/// "stick.arrows", "keyboard" (shows the keyboard) or "key.<Windows virtual-key code>".
enum BCDPadBinds {
    /// The inputs as the engine names them, under the headings Controller binds shows.
    static let sections: [(title: String, inputs: [String])] = [
        ("Sticks", ["LS", "RS"]),
        ("Face buttons", ["A", "B", "X", "Y"]),
        ("Bumpers and triggers", ["LB", "RB", "LT", "RT"]),
        ("D-pad", ["D↑", "D↓", "D←", "D→"]),
        ("Start, Select and stick clicks", ["Menu", "View", "L3", "R3"]),
    ]

    static func isStick(_ input: String) -> Bool { input == "LS" || input == "RS" }

    static func inputName(_ input: String) -> String {
        switch input {
        case "LS": return "Left stick"
        case "RS": return "Right stick"
        case "Menu": return "Start"
        case "View": return "Select"
        case "L3": return "Left stick click"
        case "R3": return "Right stick click"
        case "D↑": return "D-pad up"
        case "D↓": return "D-pad down"
        case "D←": return "D-pad left"
        case "D→": return "D-pad right"
        case "A", "B", "X", "Y": return input + " button"
        case "LB": return "Left bumper (LB)"
        case "RB": return "Right bumper (RB)"
        case "LT": return "Left trigger (LT)"
        case "RT": return "Right trigger (RT)"
        default: return input
        }
    }

    /// A key a bind can press: Windows' virtual-key code, the key's name and, where it is
    /// shorter, what its cap says.
    struct Key {
        var code: Int
        var name: String
        var cap: String? = nil
    }

    /// The keys, in the groups the choice page shows them in.
    static let keys: [(group: String, keys: [Key])] = {
        func letter(_ code: Int) -> Key { Key(code: code, name: String(UnicodeScalar(UInt8(code)))) }
        var numpad: [Key] = (0...9).map { Key(code: 0x60 + $0, name: "Numpad \($0)", cap: "\($0)") }
        numpad += [Key(code: 0x6A, name: "Numpad *", cap: "*"), Key(code: 0x6B, name: "Numpad +", cap: "+"),
                   Key(code: 0x6D, name: "Numpad −", cap: "−"), Key(code: 0x6E, name: "Numpad .", cap: "."),
                   Key(code: 0x6F, name: "Numpad /", cap: "/")]
        let editing = [Key(code: 0x1B, name: "Escape", cap: "Esc"), Key(code: 0x09, name: "Tab"), Key(code: 0x10, name: "Shift"),
                       Key(code: 0x11, name: "Ctrl"), Key(code: 0x12, name: "Alt"), Key(code: 0x20, name: "Space"),
                       Key(code: 0x0D, name: "Enter"), Key(code: 0x08, name: "Backspace", cap: "⌫"),
                       Key(code: 0x14, name: "Caps Lock", cap: "Caps")]
        let moving = [Key(code: 0x26, name: "Up arrow", cap: "↑"), Key(code: 0x28, name: "Down arrow", cap: "↓"),
                      Key(code: 0x25, name: "Left arrow", cap: "←"), Key(code: 0x27, name: "Right arrow", cap: "→"),
                      Key(code: 0x2D, name: "Insert", cap: "Ins"), Key(code: 0x2E, name: "Delete", cap: "Del"),
                      Key(code: 0x24, name: "Home"), Key(code: 0x23, name: "End"),
                      Key(code: 0x21, name: "Page Up", cap: "PgUp"), Key(code: 0x22, name: "Page Down", cap: "PgDn")]
        let symbols: [(Int, String)] = [(0xBD, "-"), (0xBB, "="), (0xDB, "["), (0xDD, "]"), (0xDC, "\\"), (0xBA, ";"),
                                        (0xDE, "'"), (0xBC, ","), (0xBE, "."), (0xBF, "/"), (0xC0, "`")]
        return [
            ("Letters", (0x41...0x5A).map(letter)),
            ("Numbers", (0x30...0x39).map(letter)),
            ("Function keys", (1...12).map { Key(code: 0x6F + $0, name: "F\($0)") }),
            ("Modifiers and editing", editing),
            ("Arrows and navigation", moving),
            ("Symbols", symbols.map { Key(code: $0.0, name: $0.1) }),
            ("Numpad", numpad),
        ]
    }()

    static func keyName(_ code: Int) -> String {
        for group in keys { if let key = group.keys.first(where: { $0.code == code }) { return key.name } }
        return "Key " + String(format: "%02X", code)
    }

    /// What a bind's value does, as a row says it.
    static func label(_ value: String, input: String) -> String {
        switch value {
        case "none": return input == "RS" ? "Mouse" : "Nothing"
        case "mouse.left": return "Left click"
        case "mouse.right": return "Right click"
        case "stick.wasd": return "WASD"
        case "stick.arrows": return "Arrow keys"
        case "keyboard": return "Show the keyboard"
        default:
            if value.hasPrefix("key."), let code = Int(value.dropFirst(4)) { return keyName(code) }
            if value.hasPrefix("pad.") { return String(value.dropFirst(4)) }
            return value
        }
    }

    /// An input's choices: the default first (`standard` names what it does), then what the
    /// input can do; a value saved elsewhere that none of them is stays listed.
    static func choices(for input: String, current: String, standard: String) -> [BCDGameChoice] {
        var list = [BCDGameChoice(value: "", label: "Default (\(standard))")]
        if isStick(input) {
            let ways = input == "RS" ? ["none", "stick.wasd", "stick.arrows"] : ["stick.wasd", "stick.arrows", "none"]
            list += ways.map { BCDGameChoice(value: $0, label: label($0, input: input)) }
        } else {
            list += ["mouse.left", "mouse.right"].map { BCDGameChoice(value: $0, label: label($0, input: input), group: "Mouse") }
            for group in keys {
                list += group.keys.map { BCDGameChoice(value: "key.\($0.code)", label: $0.name, group: group.group, cap: $0.cap) }
            }
            list += ["keyboard", "none"].map { BCDGameChoice(value: $0, label: label($0, input: input), group: "Other") }
        }
        if !list.contains(where: { $0.value == current }) {
            list.append(BCDGameChoice(value: current, label: label(current, input: input), group: isStick(input) ? nil : "Other"))
        }
        return list
    }

    /// The right stick as the mouse: how fast it moves the pointer up and down, as a share of
    /// the speed across (25% to 150%).
    static func speeds(current: Double) -> [BCDGameChoice] {
        var list = stride(from: 25, through: 150, by: 5).map { BCDGameChoice(value: String(format: "%.2f", Double($0) / 100), label: "\($0)%") }
        let now = String(format: "%.2f", current)
        if !list.contains(where: { $0.value == now }) { list.append(BCDGameChoice(value: now, label: "\(Int((current * 100).rounded()))%")) }
        return list
    }
}

extension String {
    fileprivate var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}

/// What the page offers for a game in its current state: the main action, the ones beside it,
/// the download's progress and at most two lines about what stands in the way.
struct BCDGamePagePlan: Equatable {
    struct Choice: Equatable {
        var action: BCDGameAction
        var title: String
        var symbol: String
    }
    struct Progress: Equatable {
        var fraction: Double
        var title: String
        var detail: String
        var failed: String?
    }
    struct Note: Equatable {
        var text: String
        var warning: Bool
        /// A way past it, offered beside the line.
        var fix: Choice? = nil
    }

    var main: Choice?
    var others: [Choice] = []
    var progress: Progress?
    var notes: [Note] = []
    /// Under the main action: what Install takes, and where it goes.
    var installLine: String?

    init(game: BCDGame?, download: BCDDownload?, system: BCDSystemStatus, accounts: [BCDAccount] = [],
         otherDownloads: Bool, details: BCDGameDetails) {
        guard let game else { return }
        var notes: [Note] = []
        var playable = false
        switch game.state {
        case .ready:
            main = Choice(action: .play, title: "Play", symbol: "play.fill")
            playable = true
        case .updateAvailable(let bytes):
            main = Choice(action: .play, title: "Play", symbol: "play.fill")
            // GOG's updates come with a later version of BCD: no Update that would do nothing.
            if game.store != .gog {
                others.append(Choice(action: .update, title: bytes > 0 ? "Update · \(BCDFormat.bytes(bytes))" : "Update",
                                     symbol: "arrow.down.circle"))
            }
            playable = true
        case .broken(let reason):
            let repairable = details.offersRepair(game)
            main = repairable ? Choice(action: .repair, title: "Repair", symbol: "wrench.and.screwdriver.fill")
                : Choice(action: .play, title: "Play", symbol: "play.fill")
            playable = !repairable
            notes.append(Note(text: reason, warning: true))
        case .notInstalled:
            if let blocker = details.installBlocker {
                // The store's rules say BCD cannot install this game: no Install, and the reason in its place.
                notes.append(Note(text: blocker, warning: true))
                break
            }
            main = Choice(action: .install(.device), title: "Install", symbol: "arrow.down.circle.fill")
            if system.freeExternalBytes != nil {
                others.append(Choice(action: .install(.externalDrive), title: "External drive", symbol: "externaldrive"))
            }
            // A game whose size is not known yet (Epic's, until its first plan) starts the line with the place, capitalised.
            // A Steam game's size comes from Steam's product info before the download starts: about this much (shared
            // depots whose owner was not read count nothing).
            let size = game.size ?? details.installBytes
            let estimate = game.size == nil && details.installBytes != nil
            var line = [size.map { (estimate ? "About " : "") + BCDFormat.bytes($0) }, size == nil ? "On this device" : "on this device"]
                .compactMap { $0 }.joined(separator: " ")
            if let free = system.freeDeviceBytes { line += ", \(BCDFormat.bytes(free)) free" }
            installLine = line
            if game.store != .local, let account = accounts.first(where: { $0.store == game.store }), account.name == nil {
                notes.append(Note(text: "Downloading needs your \(game.store.title) sign-in.", warning: true,
                                  fix: Choice(action: .signIn(game.store), title: "Sign in", symbol: "person.crop.circle")))
            }
            if let size, let free = system.freeDeviceBytes, size > free {
                notes.append(Note(text: "It needs \(estimate ? "about " : "")\(BCDFormat.bytes(size)); this device has \(BCDFormat.bytes(free)) free.", warning: true))
            }
            if let warning = details.installWarning { notes.append(Note(text: warning, warning: false)) }
        case .queued, .downloading, .installing:
            if download?.failed != nil {
                main = Choice(action: .resume, title: "Try again", symbol: "arrow.clockwise")
            } else if download?.paused == true {
                main = Choice(action: .resume, title: "Resume", symbol: "arrow.down.circle.fill")
            } else {
                main = Choice(action: .pause, title: "Pause", symbol: "pause.fill")
            }
            others.append(Choice(action: .cancelDownload, title: "Stop", symbol: "xmark"))
            progress = Self.progress(game.state, download)
        }
        if playable {
            notes += details.blockers.map { Note(text: $0, warning: true) }
            if system.jit == .off {
                notes.append(Note(text: "JIT is off. Games need it to start.", warning: true,
                                  fix: Choice(action: .enableJIT, title: "Turn on JIT", symbol: "bolt.fill")))
            } else if system.jit == .waiting {
                notes.append(Note(text: "Turning on JIT…", warning: false))
            }
            // The last start died for want of Wine Mono: the cause is known, so it comes before the guesses below.
            if details.needsWineMono {
                notes.append(Note(text: "This game needs Wine Mono (.NET). Download it in Settings › .NET Framework.", warning: true))
            }
            if let readiness = details.readiness { notes.append(Note(text: readiness, warning: true)) }   // round 604: BCDReadinessLive.swift
            // Before the Cloud note: only two notes show, and on an install without the increased memory
            // limit most failures come from it (2026-10-20: 54 of 257 logs, none of the large games played).
            // Only the increased memory limit is named (owner, 2026-10-22): the address map follows that entitlement
            // and the iOS version (iOS 18/26: 17 -> 63 GB, iOS 27: 454 -> 512 GB); extended virtual addressing changed
            // it on no iOS version in the logs (126 logs on iOS 27, 6 on iOS 18.3/26.6.1), so a missing VA+ gets no note.
            if let limit = system.memoryLimitBytes, !system.increasedMemoryLimit {
                notes.append(Note(text: "iOS gives BCD only \(BCDFormat.bytes(limit)) here: this install lacks the increased memory limit, so large games close early. Add it with GetMoreRam, or sign BCD with a paid developer account.", warning: true))
            }
            if case .differ(let count)? = details.cloud {
                notes.append(Note(text: "Steam Cloud and this device disagree on \(BCDGameText.saves(count)). Pick the copies to keep in Options.", warning: true))
            }
            if let note = details.storeNote { notes.append(Note(text: note, warning: false)) }
            if otherDownloads {
                notes.append(Note(text: "Downloads pause while you play and carry on afterwards.", warning: false))
            }
            if system.controllerName == nil {
                notes.append(Note(text: "No controller connected. Touch controls are in the game's menu.", warning: false))
            }
        }
        self.notes = Array(notes.prefix(2))
    }

    private static func progress(_ state: BCDGameState, _ download: BCDDownload?) -> Progress {
        var fraction = download?.fraction ?? 0
        var title = "Waiting to download"
        switch state {
        case .downloading(let p): fraction = download?.fraction ?? p; title = "Downloading"
        case .installing(let p): fraction = p; title = "Installing"
        default: break
        }
        if download?.paused == true { title = "Paused" }
        if download?.failed != nil { title = "Download stopped" }
        var parts: [String] = []
        if let download, download.totalBytes > 0 {
            parts.append("\(BCDFormat.bytes(download.doneBytes)) of \(BCDFormat.bytes(download.totalBytes))")
            if !download.paused, download.failed == nil {
                if download.bytesPerSecond > 0 { parts.append("\(BCDFormat.bytes(Int64(download.bytesPerSecond)))/s") }
                if let eta = BCDFormat.eta(download) { parts.append(eta) }
            }
        }
        return Progress(fraction: fraction, title: fraction > 0 ? "\(title) · \(Int(fraction * 100))%" : title,
                        detail: parts.joined(separator: " · "), failed: download?.failed)
    }
}

/// One row of the facts card.
struct BCDGameFact: Identifiable, Equatable {
    var label: String
    var value: String
    var id: String { label }

    static func list(_ game: BCDGame, _ details: BCDGameDetails) -> [BCDGameFact] {
        var facts: [BCDGameFact] = []
        let last = game.lastPlayed ?? details.lastPlayed
        facts.append(BCDGameFact(label: "Last played", value: last.map { BCDFormat.ago($0) } ?? "Not yet"))
        let minutes = max(game.minutesPlayed, details.minutesPlayed ?? 0)
        if minutes > 0 { facts.append(BCDGameFact(label: "Play time", value: BCDFormat.playtime(minutes))) }
        if game.state.isInstalled {
            if let bytes = details.sizeOnDisk ?? game.size { facts.append(BCDGameFact(label: "Size", value: BCDFormat.bytes(bytes))) }
            facts.append(BCDGameFact(label: "Installed on", value: game.place == .externalDrive ? "External drive" : "This device"))
        } else if details.downloadBytes != nil || details.installBytes != nil {
            // A Steam game before its install: what Steam's product info says it downloads and takes.
            if let bytes = details.downloadBytes { facts.append(BCDGameFact(label: "Download size", value: "About " + BCDFormat.bytes(bytes))) }
            if let bytes = details.installBytes { facts.append(BCDGameFact(label: "Size on this device", value: "About " + BCDFormat.bytes(bytes))) }
        } else if let bytes = game.size {
            facts.append(BCDGameFact(label: "Download", value: BCDFormat.bytes(bytes)))
        }
        facts.append(BCDGameFact(label: "Store", value: game.store == .local ? "Added by you" : game.store.title))
        if let program = details.program {
            facts.append(BCDGameFact(label: "Program", value: program))
        } else if game.thirtyTwoBit {
            facts.append(BCDGameFact(label: "Program", value: "32-bit"))
        }
        if let settings = details.settings ?? (game.autoTuned ? "Auto-tuned" : game.recommended ? "Recommended" : nil) {
            facts.append(BCDGameFact(label: "Settings", value: settings))
        }
        return facts
    }
}

/// A question waiting for the player's answer, and the action it is about.
struct BCDGamePending: Equatable {
    var action: BCDGameAction
    var question: BCDGameQuestion
    /// What the question's second yes does (`question.alternative` names it).
    var alternative: BCDGameAction? = nil
    /// Not a question yet: Install waits for the DLC lookup ("Checking your DLC…"); the card turns into the DLC question, or
    /// goes and installs the game alone, when the lookup is done.
    var checking = false
}

/// The page's state that is not layout: the details, the question asked, the lines that report
/// what an action did, and the actions themselves.
@MainActor final class BCDGamePageModel: ObservableObject {
    @Published private(set) var details = BCDGameDetails()
    @Published private(set) var pending: BCDGamePending?
    /// What an action did, by the Options row it came from ("report", "saves", ...).
    @Published var messages: [String: String] = [:]
    @Published private(set) var busy: Set<String> = []
    /// Options is open: the details carry the game's settings.
    private(set) var settingsShown = false
    /// The game config text being edited, kept here so it survives the page being rebuilt (the phone turned on its side,
    /// Settings feedback AQF0EA: "when you lay the iPhone horizontally, the app deletes the saved text"). nil when not editing.
    var configDraft: String?

    let gameID: String
    let backend: BCDGameDetailBackend?
    private(set) weak var store: BCDStore?
    /// Closes the page: before an action that shows something on the main screen, for the
    /// engine's notices, and when the game leaves the library.
    var close: () -> Void = {}
    /// How long Install waits for the DLC lookup (BCDGameDLC.checkSeconds; a test sets it short).
    var checkSeconds = BCDGameDLC.checkSeconds
    private var checkTimeout: Task<Void, Never>?
    /// The DLC chooser ("Choose DLC…" on the question) while it is open.
    @Published private(set) var chooser: BCDDLCChooser?

    init(gameID: String, backend: BCDGameDetailBackend?) {
        self.gameID = gameID
        self.backend = backend
    }

    var game: BCDGame? { store?.games.first { $0.id == gameID } }

    func open(_ store: BCDStore) {
        self.store = store
        backend?.onChange = { [weak self] in self?.reload() }
        backend?.onNotice = { [weak self] in self?.close() }
        if let game { backend?.opened(game) }
        reload()
    }

    /// The page closed: the backend lets go of it, and the page keeps nothing it read.
    func leave() {
        checkTimeout?.cancel()
        checkTimeout = nil
        chooser = nil
        backend?.onChange = nil
        backend?.onNotice = nil
        store = nil
        settingsShown = false
        details = BCDGameDetails()
    }

    /// Options opened or closed: the settings are read, or let go.
    func showSettings(_ shown: Bool) {
        guard shown != settingsShown else { return }
        settingsShown = shown
        reload()
    }

    func reload() {
        guard let game else { return }
        let next = backend?.details(for: game, settings: settingsShown) ?? BCDGameDetails()
        if next != details { details = next }
        resolveChecking()
    }

    /// Install is waiting for the DLC lookup: the lookup is done, so the card becomes the DLC question when the player owns DLC
    /// with files, and otherwise the game installs alone (a failed lookup is logged by the backend).
    private func resolveChecking() {
        guard let asked = pending, asked.checking, let game, case .install(let place) = asked.action else { return }
        if let offer = details.dlcOffer {
            checkTimeout?.cancel()
            pending = offerPending(place, offer, game)
        } else if details.dlcCheck != .checking {
            checkTimeout?.cancel()
            pending = nil
            if details.dlcCheck == .failed { backend?.set(BCDGameDLC.skipID, to: "lookup-failed", for: game) }
            run(.install(place), game)
        }
    }

    /// The question about the owned DLC: "Choose DLC…" opens the chooser, "Game only" installs the game alone.
    private func offerPending(_ place: BCDInstallPlace, _ offer: BCDDLCOffer, _ game: BCDGame) -> BCDGamePending {
        BCDGamePending(action: .chooseDLC(place), question: BCDGameDLC.offerQuestion(offer, title: game.title),
                       alternative: .installChoosingDLC(place, chosen: []))
    }

    // MARK: The DLC chooser

    func chooserToggle(_ id: UInt32) { chooser?.toggle(id) }
    func chooserSelectAll() { chooser?.selectAll() }
    func chooserSelectNone() { chooser?.selectNone() }

    /// Install with exactly the DLC chosen (none chosen: the game alone). The choice is remembered for the game.
    func chooserInstall() {
        guard let open = chooser, let game else { return }
        chooser = nil
        run(.installChoosingDLC(open.place, chosen: open.chosen.map(\.id)), game)
    }

    /// Back to the question; nothing is stored.
    func chooserBack() {
        guard let open = chooser else { return }
        chooser = nil
        guard let game, let offer = details.dlcOffer else { return }
        pending = offerPending(open.place, offer, game)
    }

    /// The wait ran out: the game installs alone.
    private func checkingTimedOut() {
        guard let asked = pending, asked.checking, let game, case .install(let place) = asked.action else { return }
        pending = nil
        backend?.set(BCDGameDLC.skipID, to: "timeout", for: game)
        run(.install(place), game)
    }

    private func startChecking(_ place: BCDInstallPlace, _ game: BCDGame) {
        pending = BCDGamePending(action: .install(place), question: BCDGameDLC.checkingQuestion(title: game.title), checking: true)
        checkTimeout?.cancel()
        checkTimeout = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64((self?.checkSeconds ?? 15) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.checkingTimedOut()
        }
    }

    var plan: BCDGamePagePlan {
        let download = store?.downloads.first { $0.id == gameID }
        let others = store?.downloads.contains { $0.id != gameID && !$0.paused && $0.failed == nil } ?? false
        return BCDGamePagePlan(game: game, download: download, system: store?.system ?? BCDSystemStatus(),
                               accounts: store?.accounts ?? [], otherDownloads: others, details: details)
    }

    /// Does the action, or asks first when it deletes, replaces or sends something.
    func request(_ action: BCDGameAction) {
        guard let game else { return }
        if case .set(let id, let value) = action {
            // An unchanged value, or a setting that cannot change now, does nothing.
            guard let setting = details.setting(id)?.setting, setting.locked == nil,
                  setting.kind == .action || setting.value != value else { return }
        }
        // A first install of a game with owned DLC asks once whether to take the DLC too (BCDGameDLC.swift). While Steam is
        // still being asked which DLC the player owns, Install waits for the answer ("Checking your DLC…").
        if case .install(let place) = action {
            if pending?.checking == true { return }
            if chooser != nil { return }
            if let offer = details.dlcOffer {
                pending = offerPending(place, offer, game)
                return
            }
            if details.dlcCheck == .checking { startChecking(place, game); return }
            if details.dlcCheck == .failed { backend?.set(BCDGameDLC.skipID, to: "lookup-failed", for: game) }
        }
        if let question = action.question(for: game, details: details) {
            pending = BCDGamePending(action: action, question: question)
            return
        }
        run(action, game)
    }

    func answer(_ yes: Bool) {
        guard let asked = pending else { return }
        if asked.checking { checkTimeout?.cancel() }
        pending = nil
        if yes, let game {
            run(asked.action, game)
        } else if case .applyRecommended(let changes) = asked.action {
            messages["recommended"] = "Not applied: \(changes) change\(changes == 1 ? "" : "s") waiting"
        }
    }

    /// The question's second yes ("Game only").
    func answerAlternative() {
        guard let asked = pending else { return }
        if asked.checking { checkTimeout?.cancel() }
        pending = nil
        if let other = asked.alternative, let game { run(other, game) }
    }

    private func run(_ action: BCDGameAction, _ game: BCDGame) {
        let actions = store?.actions
        // BCD's live store shows these on the main screen once nothing covers it.
        if action.closesPage { close() }
        switch action {
        // Play, Install and Update are the store's actions and nothing else: BCD's live store starts them.
        case .play: store?.actions?.play(game)
        case .install(let place): store?.actions?.install(game, place: place)
        case .chooseDLC(let place):
            if let offer = details.dlcOffer { chooser = BCDDLCChooser(place: place, offer: offer, gameBytes: game.size ?? 0) }
        case .installChoosingDLC(let place, let chosen):
            // The choice is kept first, so the download that starts reads it.
            backend?.set(BCDGameDLC.chooseID, to: BCDGameDLC.chooseValue(chosen), for: game)
            store?.actions?.install(game, place: place)
        case .update: store?.actions?.update(game)
        case .pause: actions?.pauseDownload(game.id)
        case .resume: actions?.resumeDownload(game.id)
        case .cancelDownload: actions?.cancelDownload(game.id)
        case .repair: actions?.verify(game)
        case .enableJIT: actions?.enableJIT()
        case .signIn(let front): actions?.signIn(front)
        // A game added from Files leaves the library through the same action (its files stay).
        case .uninstall, .removeFromLibrary: actions?.uninstall(game)
        case .set(let id, let value):
            configDraft = nil
            backend?.set(id, to: value, for: game)
        case .resetToRecommended:
            configDraft = nil
            backend?.resetToRecommended(game)
            messages["recommended"] = "The recommended settings are back."
        case .applyRecommended(let changes):
            configDraft = nil
            backend?.applyRecommendedSettings(game)
            messages["recommended"] = "Updated: \(changes) change\(changes == 1 ? "" : "s")"
        case .syncCloud: backend?.syncCloud(game)
        case .keepCloud(let fromCloud): backend?.keepCloudSaves(game, fromCloud: fromCloud)
        case .setTuning(let on): backend?.setTuning(on, for: game)
        case .undoTuning:
            configDraft = nil
            backend?.undoTuning(game)
        case .writeTuningLines:
            configDraft = nil
            backend?.writeTuningLines(game)
            messages["tuning"] = "Written into this game's config."
        case .restoreSaves(let file):
            work("restore") { backend in
                let count = try await backend.restoreSaves(from: file)
                return "Put back \(count) file\(count == 1 ? "" : "s")."
            }
        case .sendLog(let log):
            guard let backend else { return }
            Task { [weak self] in
                // The sender's comment first (BCDLogComment through the backend); Cancel sends nothing.
                guard let comment = await backend.logComment(for: log) else { return }
                self?.work("report") { [weak self] backend in
                    let code = try await backend.send(log, of: game, comment: comment) { step in self?.messages["report"] = step }
                    return "Sent. Quote this code: \(code)"
                }
            }
        case .saveConfig(let text):
            backend?.saveConfig(text, for: game)
            let count = BCDGameText.configCount(text)
            messages["config"] = "Saved: " + (count == 0 ? "no settings" : BCDGameText.settings(count))
        case .useStoreMatch(let match):
            backend?.useStoreMatch(match, for: game)
            messages["findstore"] = "Now \(match.name), with the store's pictures."
        case .setCover(let file):
            work("cover") { backend in
                try await backend.setCover(from: file, for: game)
                return "Your picture is the cover now."
            }
        case .setWallpaper(let file):
            work("setup.desktop-wallpaper") { backend in
                try await backend.setWallpaper(from: file, for: game)
                return "Your picture is the wallpaper from the next start."
            }
        }
        reload()
    }

    /// Get the latest recommended settings: up to date, updated, offline, or this game's own
    /// changes, which BCD offers to replace.
    func refreshRecommended() {
        guard let game, let backend, !busy.contains("recommended") else { return }
        busy.insert("recommended")
        messages["recommended"] = "Looking for the latest list…"
        Task { [weak self] in
            let result = await backend.refreshRecommendedSettings(for: game)
            guard let self else { return }
            self.busy.remove("recommended")
            self.messages["recommended"] = result.line
            if case .available(let changes) = result { self.request(.applyRecommended(changes: changes)) }
            self.reload()
        }
    }

    /// Report a problem: finds the game's newest log, then asks before sending it.
    func report() {
        guard let game, let backend, !busy.contains("report") else { return }
        busy.insert("report")
        messages["report"] = "Looking for the log…"
        Task { [weak self] in
            let log = await backend.lastLog(of: game)
            guard let self else { return }
            self.busy.remove("report")
            guard let log else {
                self.messages["report"] = "There's no log of this game yet. Play it once, then report."
                return
            }
            self.messages["report"] = nil
            self.request(.sendLog(log))
        }
    }

    /// Back up saves: the zip to hand over, or nil (the row says why).
    func backUpSaves() async -> URL? {
        guard let backend, !busy.contains("saves") else { return nil }
        busy.insert("saves")
        messages["saves"] = "Packing every game's saves…"
        defer { busy.remove("saves") }
        do {
            let made = try await backend.backUpSaves()
            messages["saves"] = "\(made.files) file\(made.files == 1 ? "" : "s"), \(BCDFormat.bytes(made.bytes))"
                + (made.leftOut > 0 ? "; \(made.leftOut) very large file\(made.leftOut == 1 ? "" : "s") left out." : ".")
            return made.file
        } catch {
            messages["saves"] = error.localizedDescription
            return nil
        }
    }

    private func work(_ row: String, _ job: @escaping (BCDGameDetailBackend) async throws -> String) {
        guard let backend, !busy.contains(row) else { return }
        busy.insert(row)
        Task { [weak self] in
            let line: String
            do { line = try await job(backend) } catch { line = error.localizedDescription }
            self?.busy.remove(row)
            self?.messages[row] = line
            self?.reload()
        }
    }
}

// MARK: - Opening the page

extension View {
    /// Opens a game's page full screen over the shell (Home, Library). `from` names the screen
    /// B and the back button return to.
    func bcdGamePage(_ game: Binding<BCDGame?>, from: String) -> some View {
        modifier(BCDGamePagePresenter(game: game, from: from))
    }
}

private struct BCDGamePagePresenter: ViewModifier {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Binding var game: BCDGame?
    let from: String

    func body(content: Content) -> some View {
        content.fullScreenCover(item: $game, onDismiss: BCDPresentation.didClose) { opened in
            BCDGamePage(gameID: opened.id, from: from) { game = nil }
                .environmentObject(store)
                .environmentObject(focus)
        }
    }
}

/// What Options shows: its list, a section, one setting (its choices or its text), Steam Cloud,
/// the recommended settings, auto-tune, the game's config, or the game looked up in Steam's store.
enum BCDGameOptionsPage: Equatable {
    case main
    case group(String)
    case setting(String)
    case cloud
    case recommended
    case tuning
    case config
    case storeSearch

    /// Where B goes from here, and the row the focus returns to.
    func back(_ details: BCDGameDetails) -> (page: BCDGameOptionsPage, focus: String)? {
        switch self {
        case .main: return nil
        case .group(let id):
            // A section opened from a row of another (Controller binds) goes back to that row.
            if let parent = details.groups.first(where: { $0.id == id })?.parent { return (.group(parent), "game.panel.row." + id) }
            return (.main, "game.panel.row.group." + id)
        case .setting(let id):
            guard let group = details.setting(id)?.group else { return (.main, "game.panel.close") }
            return (.group(group.id), "game.panel.row." + id)
        case .cloud: return (.main, "game.panel.row.cloud")
        case .recommended: return (.main, "game.panel.row.recommended")
        case .tuning: return (.main, "game.panel.row.tuning")
        case .config: return (.main, "game.panel.row.config")
        case .storeSearch:
            guard let group = details.setting("findstore")?.group else { return (.main, "game.panel.close") }
            return (.group(group.id), "game.panel.row.findstore")
        }
    }
}

// MARK: - The page

struct BCDGamePage: View {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical
    @Environment(\.displayScale) private var displayScale
    @Environment(\.openURL) private var openURL
    @StateObject private var model: BCDGamePageModel
    /// Options: nil while closed. A panel from the right, a sheet from the bottom on an upright
    /// phone; turning the phone moves it from one to the other.
    @State private var options: BCDGameOptionsPage?
    /// The layout as the size classes last said. Closures handed to the focus outlive the render
    /// that made them, so they read this, never the environment they captured.
    @State private var phoneUpright = false
    let from: String
    let close: () -> Void
    private let preview: (panel: BCDGameOptionsPage?, ask: BCDGameAction?, after: Double)

    /// `backend`: BCDGameDetailSource's unless given. `panel` and `ask` open Options or a
    /// question `after` seconds, once the page stands: Cloud saves for the Play question's Choose
    /// saves, the Library section for a game just added, the preview app's screenshots (which
    /// turn the screen first).
    init(gameID: String, from: String, backend: BCDGameDetailBackend? = nil,
         panel: BCDGameOptionsPage? = nil, ask: BCDGameAction? = nil, after: Double = 1, close: @escaping () -> Void) {
        self.from = from
        self.close = close
        preview = (panel, ask, after)
        _model = StateObject(wrappedValue: BCDGamePageModel(gameID: gameID, backend: backend ?? BCDGameDetailSource.backend))
    }

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }

    var body: some View {
        GeometryReader { proxy in
            let m = BCDGamePageMetrics(layout: layout, size: proxy.size, insets: proxy.safeAreaInsets,
                                       hints: focus.controllerActive && !focus.hints.isEmpty)
            ZStack {
                if let game = model.game {
                    backdrop(game, m)
                    if m.upright { upright(game, m) } else { landscape(game, m) }
                } else {
                    BCDBackdrop()
                    gone
                }
                if options != nil {
                    Color.black.opacity(0.5).ignoresSafeArea()
                        .onTapGesture { closeOptions() }
                        .transition(.opacity)
                }
                if let page = options, let game = model.game {
                    if phoneUpright {
                        bottomSheet(game, page, m).transition(.move(edge: .bottom))
                    } else {
                        panel(game, page, m).transition(.move(edge: .trailing))
                    }
                }
                if let asked = model.pending {
                    BCDGameQuestionView(question: asked.question, answerAlternative: { model.answerAlternative() }) { model.answer($0) }
                        .transition(.opacity)
                }
                if let open = model.chooser, let game = model.game {
                    BCDDLCChooserView(chooser: open, title: game.title, size: proxy.size, insets: proxy.safeAreaInsets, hints: m.hints,
                                      toggle: { model.chooserToggle($0) }, selectAll: { model.chooserSelectAll() },
                                      selectNone: { model.chooserSelectNone() }, install: { model.chooserInstall() },
                                      back: { model.chooserBack() })
                        .transition(.opacity)
                }
                hintBar(m, beside: options != nil && !phoneUpright && model.pending == nil)
            }
        }
        .coordinateSpace(name: BCDFocus.space)
        .environment(\.bcdLayout, layout)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
        .onAppear(perform: appeared)
        .onDisappear {
            model.leave()
            focus.closeLayer(Self.layer)
        }
        .onChange(of: model.pending) { old, new in
            if old == nil, new != nil {
                focus.openLayer(Self.askLayer)
                focus.focused = new?.question.destructive == true ? "game.ask.no" : "game.ask.yes"
            } else if old != nil, new == nil {
                focus.closeLayer(Self.askLayer)
            }
            installButtons()
        }
        // The DLC chooser: it holds the controller (layer "game.dlc.") and starts with the ring on its first row.
        .onChange(of: model.chooser != nil) { _, open in
            if open {
                focus.openLayer(Self.dlcLayer)
                focus.focused = model.chooser?.rows.first.map { BCDDLCChooserView.rowID($0.id) } ?? "game.dlc.install"
            } else {
                focus.closeLayer(Self.dlcLayer)
            }
            installButtons()
        }
        .onChange(of: model.game?.state) { _, _ in
            model.reload()
            installButtons()
        }
        .onChange(of: model.game == nil) { _, gone in if gone { closeOptions() } }
        // Uninstalled (or nothing left to set): Options closes.
        .onChange(of: hasOptions) { _, has in if !has { closeOptions() } }
        .onChange(of: focus.focused) { _, _ in installButtons() }
        .onChange(of: options) { _, _ in installButtons() }
        .onChange(of: model.details) { _, _ in installButtons() }
        // A game BCD finds it cannot install (a store's look at its files) loses Install while the page is open: the focus goes to Back.
        .onChange(of: model.plan.main == nil) { _, none in
            if none, focus.focused == "game.main" { focus.focused = "game.back" }
        }
        .onChange(of: layout) { _, now in phoneUpright = now == .phonePortrait }
        .animation(.easeOut(duration: 0.2), value: model.pending)
        .animation(.easeOut(duration: 0.2), value: model.chooser != nil)
    }

    static let layer = "game."
    static let panelLayer = "game.panel."
    static let askLayer = "game.ask."
    static let dlcLayer = BCDDLCChooserView.layer

    private func appeared() {
        phoneUpright = layout == .phonePortrait
        model.close = close
        model.open(store)
        focus.openLayer(Self.layer)
        if focus.focused == nil { focus.focused = model.plan.main != nil ? "game.main" : "game.back" }
        installButtons()
        if preview.panel != nil || preview.ask != nil {
            DispatchQueue.main.asyncAfter(deadline: .now() + preview.after) {
                if let first = preview.panel { openOptions(first) }
                if let ask = preview.ask { model.request(ask) }
            }
        }
    }

    // MARK: Buttons on the controller

    /// A on the focused control (the main action unless the focus moved), X Options, Y Update when
    /// there is one, B back; in Options A chooses, Y puts a setting back to its default, B goes
    /// back or closes.
    private func installButtons() {
        // The DLC chooser: A switches a row or presses a button, X selects all, Y none, B goes back to the question (short words: four hints must fit an upright phone).
        if model.chooser != nil {
            focus.setHandlers([(BCDHint(button: .a, title: "Switch"), {}),
                               (BCDHint(button: .x, title: "All"), { model.chooserSelectAll() }),
                               (BCDHint(button: .y, title: "None"), { model.chooserSelectNone() }),
                               (BCDHint(button: .b, title: "Back"), { model.chooserBack() })])
            return
        }
        if model.pending != nil {
            focus.setHandlers([(BCDHint(button: .a, title: "Select"), {}),
                               (BCDHint(button: .b, title: "Cancel"), { model.answer(false) })])
            return
        }
        if let page = options {
            var list: [(BCDHint, () -> Void)] = [(BCDHint(button: .a, title: "Choose"), {})]
            if case .setting(let id) = page, let setting = model.details.setting(id)?.setting,
               !setting.choices.isEmpty, !setting.isDefault {
                list.append((BCDHint(button: .y, title: "Default"), { model.request(.set(id, setting.defaultValue)) }))
            }
            if let back = page.back(model.details) {
                list.append((BCDHint(button: .b, title: "Back"), { showOptions(back.page, focusing: back.focus) }))
            } else {
                list.append((BCDHint(button: .b, title: "Close"), { closeOptions() }))
            }
            focus.setHandlers(list)
            return
        }
        let plan = model.plan
        var list: [(BCDHint, () -> Void)] = []
        if let main = plan.main {
            let onMain = focus.focused == nil || focus.focused == "game.main"
            list.append((BCDHint(button: .a, title: onMain ? main.title : "Select"), { model.request(main.action) }))
        }
        if hasOptions { list.append((BCDHint(button: .x, title: "Options"), { openOptions(.main) })) }
        if plan.others.contains(where: { $0.action == .update }) {
            list.append((BCDHint(button: .y, title: "Update"), { model.request(.update) }))
        }
        list.append((BCDHint(button: .b, title: "Back"), close))
        focus.setHandlers(list)
    }

    // MARK: Options

    private var hasOptions: Bool { !BCDGameOptionsList.sections(model.details, game: model.game).isEmpty }

    private func openOptions(_ page: BCDGameOptionsPage) {
        guard model.game != nil else { return }
        focus.openLayer(Self.panelLayer)
        model.showSettings(true)
        withAnimation(.easeOut(duration: 0.22)) { options = page }
        focus.focused = BCDGameOptionsList.firstRow(page, model.details, game: model.game)
    }

    private func showOptions(_ page: BCDGameOptionsPage, focusing id: String? = nil) {
        if options == .config && page != .config { model.configDraft = nil }
        withAnimation(.easeOut(duration: 0.18)) { options = page }
        focus.focused = id ?? BCDGameOptionsList.firstRow(page, model.details, game: model.game)
    }

    private func closeOptions() {
        guard options != nil else { return }
        model.configDraft = nil
        withAnimation(.easeOut(duration: 0.2)) { options = nil }
        focus.closeLayer(Self.panelLayer)
        model.showSettings(false)
        installButtons()
    }

    private func panel(_ game: BCDGame, _ page: BCDGameOptionsPage, _ m: BCDGamePageMetrics) -> some View {
        // The list stays inside the safe area; its ground reaches the screen's edges.
        HStack(spacing: 0) {
            Spacer(minLength: 0)
            optionsList(game, page)
                .frame(width: m.panelWidth)
                .frame(maxHeight: .infinity)
                .background {
                    BCDStyle.ground
                        .overlay(alignment: .leading) { Rectangle().fill(BCDStyle.stroke).frame(width: 1) }
                        .ignoresSafeArea()
                }
        }
    }

    private func optionsList(_ game: BCDGame, _ page: BCDGameOptionsPage) -> BCDGameOptionsList {
        BCDGameOptionsList(game: game, model: model, page: page,
                           show: { showOptions($0, focusing: $1) }, close: closeOptions)
    }

    /// An upright phone: Options rises from the bottom over most of the screen.
    private func bottomSheet(_ game: BCDGame, _ page: BCDGameOptionsPage, _ m: BCDGamePageMetrics) -> some View {
        VStack(spacing: 0) {
            Spacer(minLength: m.insets.top + 40)
            VStack(spacing: 0) {
                Capsule().fill(BCDStyle.stroke).frame(width: 40, height: 5).padding(.top, 8)
                optionsList(game, page)
            }
            .frame(maxWidth: .infinity)
            .frame(height: (m.size.height + m.insets.bottom) * 0.8)
            .background {
                UnevenRoundedRectangle(topLeadingRadius: 24, topTrailingRadius: 24, style: .continuous)
                    .fill(BCDStyle.ground)
                    .overlay {
                        UnevenRoundedRectangle(topLeadingRadius: 24, topTrailingRadius: 24, style: .continuous)
                            .strokeBorder(BCDStyle.stroke, lineWidth: 1)
                    }
                    .ignoresSafeArea(edges: .bottom)
            }
        }
        .ignoresSafeArea(edges: .bottom)
    }

    // MARK: Art

    /// The game's hero art filling a frame. BCDArtImage decodes it at the size it is drawn at
    /// (filling the frame) times the display scale, never larger: the game shares this process's
    /// memory (docs/BCD-UI-ARCHITECTURE.md, Memory comes first).
    private func heroArt(_ game: BCDGame, frame: CGSize) -> some View {
        let shape: CGFloat = 1920.0 / 620.0
        return BCDArtImage(urls: game.heroArt + game.headerArt + game.coverArt, shape: shape) {
            BCDArtPlaceholder(game: game, kind: .hero, showTitle: false)
        }
    }

    @ViewBuilder private func backdrop(_ game: BCDGame, _ m: BCDGamePageMetrics) -> some View {
        let screen = CGSize(width: m.size.width + m.insets.leading + m.insets.trailing,
                            height: m.size.height + m.insets.top + m.insets.bottom)
        if m.upright {
            VStack(spacing: 0) {
                heroArt(game, frame: CGSize(width: screen.width, height: m.artHeight))
                    .frame(height: m.artHeight)
                    .overlay {
                        LinearGradient(stops: [.init(color: BCDStyle.ground.opacity(0.25), location: 0),
                                               .init(color: BCDStyle.ground.opacity(0.4), location: 0.5),
                                               .init(color: BCDStyle.ground, location: 1)],
                                       startPoint: .top, endPoint: .bottom)
                    }
                    .clipped()
                BCDStyle.ground
            }
            .ignoresSafeArea()
        } else {
            heroArt(game, frame: screen)
                .overlay {
                    LinearGradient(stops: [.init(color: BCDStyle.ground.opacity(0.94), location: 0),
                                           .init(color: BCDStyle.ground.opacity(0.78), location: 0.4),
                                           .init(color: BCDStyle.ground.opacity(0.3), location: 0.78),
                                           .init(color: BCDStyle.ground.opacity(0.22), location: 1)],
                                   startPoint: .leading, endPoint: .trailing)
                }
                .overlay {
                    LinearGradient(colors: [.clear, BCDStyle.ground.opacity(0.85)], startPoint: .center, endPoint: .bottom)
                }
                .ignoresSafeArea()
        }
    }

    // MARK: Layouts

    /// A phone on its side and an iPad: the art across the screen, the game's name and actions
    /// low on the left, the facts on the right.
    private func landscape(_ game: BCDGame, _ m: BCDGamePageMetrics) -> some View {
        let plan = model.plan
        return ZStack(alignment: .topLeading) {
            ScrollView(.vertical, showsIndicators: false) {
                HStack(alignment: .bottom, spacing: m.columnGap) {
                    mainColumn(game, plan, m)
                        .frame(maxWidth: m.mainColumn, alignment: .leading)
                    Spacer(minLength: 0)
                    factsCard(game, m).frame(width: m.factsWidth)
                }
                .padding(.horizontal, m.gutter)
                .padding(.top, m.pad ? 84 : 58)
                .padding(.bottom, m.bottomRoom)
                .frame(minHeight: m.size.height, alignment: .bottom)
            }
            .scrollBounceBehavior(.basedOnSize)
            backButton.padding(.leading, m.gutter).padding(.top, m.pad ? 18 : 10)
        }
    }

    /// An upright phone or iPad: the art at the top, everything below it in one scrolling column
    /// (an iPad keeps the facts beside the actions).
    private func upright(_ game: BCDGame, _ m: BCDGamePageMetrics) -> some View {
        let plan = model.plan
        return ZStack(alignment: .topLeading) {
            ScrollViewReader { scroller in
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 22) {
                        Color.clear.frame(height: m.uprightLead)
                        if m.pad {
                            HStack(alignment: .bottom, spacing: m.columnGap) {
                                mainColumn(game, plan, m).frame(maxWidth: .infinity, alignment: .leading)
                                factsCard(game, m).frame(width: m.factsWidth)
                            }
                        } else {
                            mainColumn(game, plan, m)
                            factsCard(game, m)
                        }
                    }
                    .padding(.horizontal, m.gutter)
                    .padding(.bottom, m.bottomRoom)
                }
                .onChange(of: focus.focused) { _, id in
                    guard let id, id.hasPrefix(Self.layer), !id.hasPrefix(Self.panelLayer), !id.hasPrefix(Self.askLayer),
                          !id.hasPrefix(Self.dlcLayer) else { return }
                    withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
                }
            }
            backButton.padding(.leading, m.gutter).padding(.top, 8)
        }
    }

    private var gone: some View {
        VStack(spacing: 16) {
            Image(systemName: "questionmark.square.dashed").font(.system(size: 40, weight: .semibold))
                .foregroundStyle(BCDStyle.textSoft)
            Text("This game isn't in your library any more").font(BCDStyle.heading(20)).multilineTextAlignment(.center)
            Button(action: close) { Label("Back to \(from)", systemImage: "chevron.left") }
                .buttonStyle(BCDGameChipStyle())
                .bcdFocusable("game.main", cornerRadius: 25, action: close)
        }
        .padding(24)
    }

    // MARK: The game, its actions and the lines under them

    private func mainColumn(_ game: BCDGame, _ plan: BCDGamePagePlan, _ m: BCDGamePageMetrics) -> some View {
        VStack(alignment: .leading, spacing: m.pad ? 16 : 11) {
            VStack(alignment: .leading, spacing: 6) {
                // A store logo gets its box: in a scrolling column nothing else gives it a height.
                BCDGameLogo(game: game, titleSize: m.titleSize, maxWidth: m.logoWidth, maxHeight: m.logoHeight)
                    .frame(height: game.logoArt.isEmpty ? nil : m.logoHeight, alignment: .bottomLeading)
                Text(BCDGameText.storeLine(game, developer: model.details.developer))
                    .font(BCDStyle.label(m.pad ? 16 : 14)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
            }
            if let progress = plan.progress { progressBlock(progress, m) }
            actionRows(plan, m)
            if let line = plan.installLine {
                Text(line).font(BCDStyle.body(m.pad ? 15 : 13)).foregroundStyle(BCDStyle.textSoft)
            }
            notes(plan.notes, m)
        }
    }

    private func progressBlock(_ progress: BCDGamePagePlan.Progress, _ m: BCDGamePageMetrics) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(progress.title).font(BCDStyle.label(m.pad ? 16 : 14)).monospacedDigit()
            BCDProgressBar(fraction: progress.fraction)
            if !progress.detail.isEmpty {
                Text(progress.detail).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).monospacedDigit().lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
            if let failed = progress.failed {
                Label(failed, systemImage: "exclamationmark.triangle.fill").font(BCDStyle.body(13)).foregroundStyle(BCDStyle.alert)
            }
        }
        .frame(maxWidth: m.upright && !m.pad ? .infinity : 380, alignment: .leading)
    }

    /// The main action and the ones beside it on one row when they fit, else two.
    @ViewBuilder private func actionRows(_ plan: BCDGamePagePlan, _ m: BCDGamePageMetrics) -> some View {
        let others = otherButtons(plan)
        let width = (m.upright && !m.pad ? 0 : m.mainWidth) + others.reduce(0) { $0 + 10 + Self.chipWidth($1.title) }
        if m.upright && !m.pad {
            VStack(alignment: .leading, spacing: 10) {
                mainButton(plan, m)
                if !others.isEmpty { HStack(spacing: 10) { chips(others) } }
            }
        } else if width <= m.mainColumn {
            HStack(spacing: 10) { mainButton(plan, m); chips(others) }
        } else {
            VStack(alignment: .leading, spacing: 10) {
                mainButton(plan, m)
                HStack(spacing: 10) { chips(others) }
            }
        }
    }

    private struct Other: Identifiable {
        let id: String
        let title: String
        let symbol: String
        let run: () -> Void
    }

    private func otherButtons(_ plan: BCDGamePagePlan) -> [Other] {
        var list: [Other] = []
        if hasOptions { list.append(Other(id: "game.options", title: "Options", symbol: "slider.horizontal.3") { openOptions(.main) }) }
        for (index, choice) in plan.others.enumerated() {
            list.append(Other(id: "game.other.\(index)", title: choice.title, symbol: choice.symbol) { model.request(choice.action) })
        }
        return list
    }

    /// About how wide a chip is: its padding, symbol and title.
    private static func chipWidth(_ title: String) -> CGFloat { 62 + CGFloat(title.count) * 8.6 }

    private func chips(_ list: [Other]) -> some View {
        ForEach(list) { item in
            Button(action: item.run) { Label(item.title, systemImage: item.symbol) }
                .buttonStyle(BCDGameChipStyle())
                .bcdFocusable(item.id, cornerRadius: 25, action: item.run)
                .id(item.id)
        }
    }

    @ViewBuilder private func mainButton(_ plan: BCDGamePagePlan, _ m: BCDGamePageMetrics) -> some View {
        if let main = plan.main {
            let run = { model.request(main.action) }
            Group {
                if main.action == .pause {
                    Button(action: run) { Label(main.title, systemImage: main.symbol) }.buttonStyle(BCDOutlineButtonStyle())
                } else {
                    Button(action: run) { Label(main.title, systemImage: main.symbol) }.buttonStyle(BCDPrimaryButtonStyle())
                }
            }
            .frame(width: m.upright && !m.pad ? nil : m.mainWidth)
            .bcdFocusable("game.main", cornerRadius: 27, action: run)
            .id("game.main")
        }
    }

    private func notes(_ notes: [BCDGamePagePlan.Note], _ m: BCDGamePageMetrics) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(notes.enumerated()), id: \.offset) { index, note in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: note.warning ? "exclamationmark.triangle.fill" : "info.circle.fill")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(note.warning ? BCDGameColors.amber : BCDStyle.lavender)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(note.text).font(BCDStyle.body(m.pad ? 15 : 14)).foregroundStyle(BCDStyle.text.opacity(0.92))
                            .fixedSize(horizontal: false, vertical: true)
                        if let fix = note.fix {
                            let run = { model.request(fix.action) }
                            Button(action: run) { Label(fix.title, systemImage: fix.symbol) }
                                .buttonStyle(BCDGameChipStyle(height: 36))
                                .bcdFocusable("game.fix.\(index)", cornerRadius: 18, action: run)
                                .id("game.fix.\(index)")
                        }
                    }
                }
            }
        }
        .frame(maxWidth: m.upright ? .infinity : 440, alignment: .leading)
    }

    private func factsCard(_ game: BCDGame, _ m: BCDGamePageMetrics) -> some View {
        let facts = BCDGameFact.list(game, model.details)
        return VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(facts.enumerated()), id: \.element.id) { index, fact in
                if fact.label == "Store", let page = model.details.storePage {
                    // The store's own page for the game, in the browser.
                    let open = { openURL(page) }
                    Button(action: open) { factRow(fact, m, link: true) }
                        .buttonStyle(.plain)
                        .bcdFocusable("game.store", cornerRadius: 10, action: open)
                        .accessibilityLabel("\(fact.value) store page")
                        .id("game.store")
                } else {
                    factRow(fact, m, link: false)
                }
                if index < facts.count - 1 { Rectangle().fill(BCDStyle.stroke.opacity(0.8)).frame(height: 1) }
            }
        }
        .padding(.horizontal, 16).padding(.vertical, m.pad ? 10 : 6)
        .background(BCDStyle.surface.opacity(0.88), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
    }

    private func factRow(_ fact: BCDGameFact, _ m: BCDGamePageMetrics, link: Bool) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(fact.label).font(BCDStyle.body(m.pad ? 15 : 13)).foregroundStyle(BCDStyle.textSoft)
            Spacer(minLength: 8)
            Text(fact.value).font(BCDStyle.label(m.pad ? 15 : 13)).multilineTextAlignment(.trailing).lineLimit(2)
                .foregroundStyle(link ? BCDStyle.lavender : BCDStyle.text)
            if link {
                Image(systemName: "arrow.up.right").font(.system(size: m.pad ? 12 : 11, weight: .bold)).foregroundStyle(BCDStyle.lavender)
            }
        }
        .padding(.vertical, m.pad ? 10 : 7)
        .contentShape(Rectangle())
    }

    private var backButton: some View {
        Button(action: close) {
            Label(from, systemImage: "chevron.left")
                .font(BCDStyle.label(15))
                .padding(.horizontal, 14).frame(height: 38)
                .background(BCDStyle.surfaceRaised.opacity(0.92), in: Capsule())
                .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .bcdFocusable("game.back", cornerRadius: 19, action: close)
        .accessibilityLabel("Back to \(from)")
        .id("game.back")
    }

    /// The controller's hints along the bottom, over everything; beside the Options panel (on its
    /// left) while it is open.
    @ViewBuilder private func hintBar(_ m: BCDGamePageMetrics, beside panel: Bool) -> some View {
        if m.hints {
            VStack(spacing: 0) {
                Spacer()
                BCDGameHints(hints: focus.hints, leading: panel)
                    .padding(.leading, m.gutter)
                    .padding(.trailing, panel ? m.panelWidth + m.gutter : m.gutter)
                    .background {
                        if !panel {
                            LinearGradient(colors: [BCDStyle.ground.opacity(0), BCDStyle.ground.opacity(0.92)],
                                           startPoint: .top, endPoint: .bottom)
                                .ignoresSafeArea(edges: .bottom)
                        }
                    }
            }
            .allowsHitTesting(false)
        }
    }
}

/// Sizes for each layout: a phone on its side, an upright phone, an iPad either way.
private struct BCDGamePageMetrics {
    let layout: BCDLayout
    let size: CGSize
    let insets: EdgeInsets
    /// The controller's hints show along the bottom.
    let hints: Bool

    var pad: Bool { layout == .pad }
    var upright: Bool { size.height > size.width }
    var gutter: CGFloat { pad ? 36 : 20 }
    var columnGap: CGFloat { pad ? 36 : 22 }
    var factsWidth: CGFloat { pad ? 320 : (size.width < 700 ? 214 : 236) }
    var mainColumn: CGFloat { max(260, size.width - 2 * gutter - factsWidth - columnGap) }
    var titleSize: CGFloat { pad ? 46 : 32 }
    var logoWidth: CGFloat { pad ? 480 : (upright ? 300 : 320) }
    var logoHeight: CGFloat { pad ? 130 : (upright ? 90 : 80) }
    var mainWidth: CGFloat { pad ? 240 : 180 }
    var panelWidth: CGFloat { pad ? 440 : min(400, max(320, size.width * 0.52)) }
    var bottomRoom: CGFloat { hints ? 52 : (pad ? 28 : 14) }
    /// An upright screen: how tall the art is, and how far down the name starts on it.
    var artHeight: CGFloat { max(250, (size.height + insets.top + insets.bottom) * (pad ? 0.5 : 0.44)) }
    var uprightLead: CGFloat { max(40, artHeight - insets.top - (pad ? 210 : 150)) }
}

/// The warm amber of the warnings, as the shell's JIT and memory chips use it.
enum BCDGameColors {
    static var amber: Color { BCDStyle.amber }
}

/// The hints along the bottom: each button's glyph and what it does here.
private struct BCDGameHints: View {
    let hints: [BCDHint]
    /// On the left, beside a panel on the right.
    var leading = false
    var body: some View {
        HStack(spacing: 22) {
            if !leading { Spacer() }
            ForEach(hints) { BCDHintView(hint: $0) }
            if leading { Spacer() }
        }
        .frame(height: 40)
    }
}

/// A rounded, content-sized button beside the main action.
private struct BCDGameChipStyle: ButtonStyle {
    var height: CGFloat = 50
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(BCDStyle.label(height < 44 ? 14 : 15))
            .lineLimit(1)
            .foregroundStyle(BCDStyle.text)
            .padding(.horizontal, height < 44 ? 14 : 18)
            .frame(height: height)
            .background(BCDStyle.surface.opacity(configuration.isPressed ? 0.7 : 0.92), in: Capsule())
            .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// The answer that deletes or replaces something.
private struct BCDGameDangerStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(BCDStyle.label(16))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity, minHeight: 50)
            .background(BCDStyle.alert.opacity(configuration.isPressed ? 0.7 : 0.92), in: Capsule())
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

// MARK: - The question

/// Also the card menu's question (BCDMenu.swift), under its own focus ids: `prefix` + "no" and "yes".
struct BCDGameQuestionView: View {
    let question: BCDGameQuestion
    var prefix = "game.ask."
    /// The question's second yes, when it has one (`question.alternative`).
    var answerAlternative: (() -> Void)? = nil
    let answer: (Bool) -> Void

    var body: some View {
        ZStack {
            Color.black.opacity(0.55).ignoresSafeArea().onTapGesture { answer(false) }
            VStack(alignment: .leading, spacing: 14) {
                Text(question.title).font(BCDStyle.heading(20)).fixedSize(horizontal: false, vertical: true)
                Text(question.message).font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    Button("Cancel") { answer(false) }
                        .buttonStyle(BCDOutlineButtonStyle())
                        .bcdFocusable(prefix + "no", cornerRadius: 25) { answer(false) }
                    if let other = question.alternative, let alternative = answerAlternative {
                        Button { alternative() } label: { Text(other).lineLimit(1).minimumScaleFactor(0.75) }
                            .buttonStyle(BCDOutlineButtonStyle())
                            .bcdFocusable(prefix + "alt", cornerRadius: 25) { alternative() }
                    }
                    if question.destructive {
                        Button(question.confirm) { answer(true) }
                            .buttonStyle(BCDGameDangerStyle())
                            .bcdFocusable(prefix + "yes", cornerRadius: 25) { answer(true) }
                    } else {
                        Button(question.confirm) { answer(true) }
                            .buttonStyle(BCDPrimaryButtonStyle())
                            .bcdFocusable(prefix + "yes", cornerRadius: 27) { answer(true) }
                    }
                }
                .padding(.top, 4)
            }
            .padding(22)
            .frame(maxWidth: 460, alignment: .leading)
            .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .padding(24)
        }
    }
}

// MARK: - Options

/// One row of Options.
private struct BCDGameRow: Identifiable {
    enum Tone { case normal, warning, destructive }
    var id: String
    var title: String
    var symbol: String? = nil
    var value: String? = nil
    var detail: String? = nil
    var changed = false
    var tone = Tone.normal
    /// A switch: on or off.
    var toggle: Bool? = nil
    var chevron = false
    var enabled = true
    /// Shown, not pressed (the focus still stops on it, so a controller scrolls to it).
    var still = false
    var run: () -> Void = {}

    var focusID: String { "game.panel.row." + id }
}

private struct BCDGameSection: Identifiable {
    var id: String { title }
    var title: String
    var rows: [BCDGameRow]
    var note: String? = nil
}

/// The Options panel's (or sheet's) contents: the sections and the game's own rows, a section's
/// settings, a setting's choices or text, Steam Cloud, the recommended settings, auto-tune.
private struct BCDGameOptionsList: View {
    let game: BCDGame
    @ObservedObject var model: BCDGamePageModel
    let page: BCDGameOptionsPage
    let show: (BCDGameOptionsPage, String?) -> Void
    let close: () -> Void
    @EnvironmentObject private var focus: BCDFocus
    @State private var sharing: BCDGameShare?
    /// A saves backup handed to the share sheet: a copy, removed once the sheet closes.
    @State private var shared: URL?
    /// Files is open: for a saves backup to restore, or for a cover picture.
    @State private var importing = false
    @State private var importKind = ImportKind.saves

    enum ImportKind { case saves, cover, wallpaper }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            ScrollViewReader { scroller in
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 8) { content }
                        .padding(.horizontal, 18).padding(.top, 6).padding(.bottom, 60)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: focus.focused) { _, id in
                    guard let id, id.hasPrefix(BCDGamePage.panelLayer) else { return }
                    withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
                }
            }
        }
        .sheet(item: $sharing, onDismiss: {
            if let file = shared { try? FileManager.default.removeItem(at: file) }
            shared = nil
        }) { share in
            BCDGameShareSheet(items: [share.item])
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: importKind == .saves ? [.zip] : [.image]) { result in
            guard case .success(let file) = result else { return }
            switch importKind {
            case .saves: model.request(.restoreSaves(file))
            case .cover: model.request(.setCover(file))
            case .wallpaper: model.request(.setWallpaper(file))
            }
        }
    }

    private func importFile(_ kind: ImportKind) {
        importKind = kind
        importing = true
    }

    // MARK: Header

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            if let back = page.back(model.details) {
                let run = { show(back.page, back.focus) }
                Button(action: run) {
                    Image(systemName: "chevron.left").font(.system(size: 16, weight: .bold))
                        .frame(width: 38, height: 38)
                        .background(BCDStyle.surfaceRaised, in: Circle())
                }
                .buttonStyle(.plain)
                .bcdFocusable("game.panel.back", cornerRadius: 19, action: run)
                .accessibilityLabel("Back")
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(BCDStyle.heading(20)).lineLimit(1)
                Text(game.title).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
            }
            Spacer(minLength: 8)
            Button(action: close) {
                Image(systemName: "xmark").font(.system(size: 14, weight: .bold))
                    .frame(width: 38, height: 38)
                    .background(BCDStyle.surfaceRaised, in: Circle())
            }
            .buttonStyle(.plain)
            .bcdFocusable("game.panel.close", cornerRadius: 19, action: close)
            .accessibilityLabel("Close Options")
        }
        .padding(.horizontal, 18).padding(.top, 16).padding(.bottom, 10)
    }

    private var title: String {
        switch page {
        case .main: return "Options"
        case .group(let id): return model.details.groups.first { $0.id == id }?.title ?? "Options"
        case .setting(let id): return model.details.setting(id)?.setting.title ?? "Options"
        case .cloud: return "Cloud saves"
        case .recommended: return "Recommended settings"
        case .tuning: return "Auto-tune"
        case .config: return "Game config"
        case .storeSearch: return "Find on Steam"
        }
    }

    @ViewBuilder private var content: some View {
        switch page {
        case .main:
            let sections = Self.sections(model.details, game: game, model: model, commands: commands)
            if sections.isEmpty {
                Text("There's nothing to set for this game until it's installed.")
                    .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft).padding(.top, 8)
            }
            sectionViews(sections)
        case .group(let id):
            if let group = model.details.groups.first(where: { $0.id == id }) { groupPage(group) }
        case .setting(let id):
            if let setting = model.details.setting(id)?.setting {
                if case .text(let prompt) = setting.kind {
                    BCDGameTextPage(setting: setting, prompt: prompt,
                                    save: { model.request(.set(id, $0)); backFromSetting(id) },
                                    cancel: { backFromSetting(id) })
                } else {
                    choices(setting)
                }
            }
        case .cloud:
            if let cloud = model.details.cloud { cloudPage(cloud) }
        case .recommended:
            recommendedPage
        case .tuning:
            if let tuning = model.details.tuning { tuningPage(tuning) }
        case .config:
            BCDGameConfigPage(game: game, model: model, done: { show(.main, "game.panel.row.config") })
        case .storeSearch:
            BCDGameStoreSearchPage(game: game, model: model, done: {
                if let back = BCDGameOptionsPage.storeSearch.back(model.details) { show(back.page, back.focus) }
            })
        }
    }

    private func sectionViews(_ sections: [BCDGameSection]) -> some View {
        ForEach(sections) { section in
            BCDEyebrow(text: section.title).padding(.top, 10).padding(.leading, 4)
            ForEach(section.rows) { BCDGameRowView(row: $0) }
            if let note = section.note {
                Text(note).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func backFromSetting(_ id: String) {
        guard let back = BCDGameOptionsPage.setting(id).back(model.details) else { return }
        show(back.page, back.focus)
    }

    // MARK: The main list

    /// What the view itself does for some rows: another screen, the share sheet, Files.
    struct Commands {
        var show: (BCDGameOptionsPage, String?) -> Void
        var copyLink: () -> Void
        var backUp: () -> Void
        var restore: () -> Void
    }

    private var commands: Commands {
        Commands(show: show, copyLink: copyLink, backUp: backUp, restore: { importFile(.saves) })
    }

    private func copyLink() {
        guard let link = model.backend?.shortcutLink(for: game) else { return }
        UIPasteboard.general.string = link
        model.messages["shortcut"] = "Link copied. In Shortcuts, make a shortcut with Open URLs, paste the link, then choose Share › Add to Home Screen."
    }

    private func backUp() {
        Task {
            guard let file = await model.backUpSaves() else { return }
            shared = file
            sharing = BCDGameShare(item: file)
        }
    }

    /// The main list's sections for these details; empty when there is nothing to set.
    static func sections(_ details: BCDGameDetails, game: BCDGame?) -> [BCDGameSection] {
        sections(details, game: game, model: nil, commands: nil)
    }

    static func firstRow(_ page: BCDGameOptionsPage, _ details: BCDGameDetails, game: BCDGame?) -> String? {
        switch page {
        case .main: return sections(details, game: game).first?.rows.first?.focusID ?? "game.panel.close"
        case .group(let id):
            return details.groups.first { $0.id == id }?.settings.first.map { "game.panel.row." + $0.id } ?? "game.panel.back"
        case .setting(let id):
            guard let setting = details.setting(id)?.setting else { return "game.panel.back" }
            if case .text = setting.kind { return "game.panel.save" }
            return "game.panel.choice.\(setting.choices.firstIndex { $0.value == setting.value } ?? 0)"
        case .cloud: return "game.panel.row.sync"
        case .recommended: return "game.panel.row.latest"
        case .tuning: return "game.panel.row.tuning"
        case .config: return "game.panel.save"
        case .storeSearch: return "game.panel.search"
        }
    }

    private static func sections(_ d: BCDGameDetails, game: BCDGame?, model: BCDGamePageModel?,
                                 commands: Commands?) -> [BCDGameSection] {
        let request: (BCDGameAction) -> () -> Void = { action in { model?.request(action) } }
        let message: (String) -> String? = { model?.messages[$0] }
        let busy: (String) -> Bool = { model?.busy.contains($0) ?? false }
        var list: [BCDGameSection] = []

        // Sections reached from a row of another (Controller binds) are not listed here.
        let groups = d.groups.filter { $0.parent == nil }.map { group in
            BCDGameRow(id: "group." + group.id, title: group.title, symbol: group.symbol, detail: group.summary,
                       changed: group.settings.contains(where: \.changed), chevron: true,
                       run: { commands?.show(.group(group.id), nil) })
        }
        if !groups.isEmpty {
            list.append(BCDGameSection(title: "Settings", rows: groups))
        } else if d.offersDownloadOptions {
            // The settings are built when Options opens (details(settings:)): until then one row stands for them.
            list.append(BCDGameSection(title: "Settings", rows: [
                BCDGameRow(id: "group.download", title: "Download", symbol: "arrow.down.circle", chevron: true,
                           run: { commands?.show(.group("download"), nil) })]))
        }

        var rows: [BCDGameRow] = []
        if let cloud = d.cloud {
            var tone = BCDGameRow.Tone.normal
            if case .differ = cloud { tone = .warning }
            rows.append(BCDGameRow(id: "cloud", title: "Cloud saves", symbol: "icloud", value: cloud.summary, tone: tone, chevron: true,
                                   run: { commands?.show(.cloud, nil) }))
        }
        if d.canReset {
            let what = d.recommendation.map { rec in rec.title.map { "Tested for \($0)" } ?? "BCD's standard settings" }
            rows.append(BCDGameRow(id: "recommended", title: "Recommended settings", symbol: "checkmark.seal",
                                   detail: message("recommended") ?? what ?? "No tested list for this game yet", chevron: true,
                                   run: { commands?.show(.recommended, nil) }))
        }
        if let tuning = d.tuning {
            rows.append(BCDGameRow(id: "tuning", title: "Auto-tune", symbol: "wand.and.stars", value: tuning.on ? "On" : "Off",
                                   detail: tuning.status, chevron: true, run: { commands?.show(.tuning, nil) }))
        }
        if let count = d.configSettings {
            rows.append(BCDGameRow(id: "config", title: "Game config", symbol: "doc.text", value: BCDGameText.settings(count),
                                   detail: message("config") ?? "Config lines for this game alone, for switches Options has no row for.",
                                   chevron: true, run: { commands?.show(.config, nil) }))
        }
        if d.shortcut {
            rows.append(BCDGameRow(id: "shortcut", title: "Add to Home Screen", symbol: "apps.iphone.badge.plus",
                                   detail: message("shortcut") ?? "Copies a link that starts this game, for the Shortcuts app.",
                                   run: { commands?.copyLink() }))
        }
        if d.saves {
            rows.append(BCDGameRow(id: "backup", title: "Back up saves", symbol: "externaldrive.badge.icloud",
                                   detail: message("saves") ?? "Every game's saves in one zip, to keep in Files.",
                                   enabled: !busy("saves"), run: { commands?.backUp() }))
            rows.append(BCDGameRow(id: "restore", title: "Restore saves", symbol: "arrow.uturn.backward.circle",
                                   detail: message("restore") ?? "Puts back the saves from a backup zip.",
                                   enabled: !busy("restore"), run: { commands?.restore() }))
        }
        if d.offersRepair(game) {
            rows.append(BCDGameRow(id: "repair", title: "Check and repair files", symbol: "wrench.and.screwdriver",
                                   detail: "Compares every file with Steam and downloads what's missing or changed.",
                                   run: request(.repair)))
        }
        if d.report {
            rows.append(BCDGameRow(id: "report", title: "Report a problem", symbol: "exclamationmark.bubble",
                                   detail: message("report") ?? "Sends the log of this game's last session.",
                                   enabled: !busy("report"), run: { model?.report() }))
        }
        switch d.removal {
        case .uninstall(let frees):
            rows.append(BCDGameRow(id: "remove", title: "Uninstall", symbol: "trash", value: frees.map { "Frees \(BCDFormat.bytes($0))" },
                                   tone: .destructive, run: request(.uninstall)))
        case .removeFromLibrary:
            rows.append(BCDGameRow(id: "remove", title: "Remove from library", symbol: "minus.circle",
                                   detail: "Its files stay on this device.", tone: .destructive, run: request(.removeFromLibrary)))
        case .none:
            break
        }
        if !rows.isEmpty { list.append(BCDGameSection(title: "This game", rows: rows)) }
        return list
    }

    // MARK: A section

    private func groupPage(_ group: BCDGameSettingGroup) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let note = group.note {
                Label(note, systemImage: "exclamationmark.triangle.fill")
                    .font(BCDStyle.body(13)).foregroundStyle(BCDGameColors.amber)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.bottom, 4)
            }
            ForEach(group.settings) { setting in
                if let heading = setting.heading {
                    BCDEyebrow(text: heading).padding(.top, setting.id == group.settings.first?.id ? 2 : 12).padding(.leading, 4)
                }
                BCDGameRowView(row: row(setting))
            }
        }
    }

    private func row(_ setting: BCDGameSetting) -> BCDGameRow {
        let id = setting.id
        // What the row's action just did (a picture chosen, a match used) stands in for its note.
        var row = BCDGameRow(id: id, title: setting.title, detail: model.messages[id] ?? setting.locked ?? setting.note,
                             changed: setting.changed,
                             tone: setting.locked != nil ? .warning : (setting.destructive ? .destructive : .normal))
        switch setting.kind {
        case .opens(let screen):
            row.value = setting.summary.isEmpty ? nil : setting.summary
            row.chevron = screen != .coverPicture && screen != .wallpaperPicture
            row.enabled = !model.busy.contains(id)
            switch screen {
            case .section(let group): row.run = { show(.group(group), nil) }
            case .storeSearch: row.run = { show(.storeSearch, nil) }
            case .coverPicture: row.run = { importFile(.cover) }
            case .wallpaperPicture: row.run = { importFile(.wallpaper) }
            }
        case .choice:
            row.value = setting.summary
            row.chevron = true
            row.run = { show(.setting(id), nil) }
        case .toggle:
            row.toggle = setting.isOn
            row.enabled = setting.locked == nil
            row.run = { model.request(.set(id, setting.isOn ? "0" : "1")) }
        case .text:
            row.value = setting.summary
            row.chevron = true
            row.run = { show(.setting(id), nil) }
        case .info:
            row.detail = [setting.value, setting.note].compactMap { $0 }.joined(separator: "\n")
            row.still = true
        case .action:
            row.run = { model.request(.set(id, "run")) }
        }
        return row
    }

    // MARK: A setting's choices

    /// A run of choices under one heading; `keys`: short labels, shown as caps in a grid.
    private struct ChoiceRun: Identifiable {
        var id: Int { start }
        var start: Int
        var group: String?
        var choices: [BCDGameChoice]
        var keys: Bool
    }

    private static func runs(_ list: [BCDGameChoice]) -> [ChoiceRun] {
        var runs: [ChoiceRun] = []
        for (index, choice) in list.enumerated() {
            if let last = runs.last, last.group == choice.group {
                runs[runs.count - 1].choices.append(choice)
            } else {
                runs.append(ChoiceRun(start: index, group: choice.group, choices: [choice], keys: false))
            }
        }
        // Eight or more short labels (keys, percentages) read better as caps than as long rows.
        for i in runs.indices {
            runs[i].keys = runs[i].choices.count >= 8 && runs[i].choices.allSatisfy { ($0.cap ?? $0.label).count <= 6 }
        }
        return runs
    }

    private func choices(_ setting: BCDGameSetting) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Self.runs(setting.choices)) { run in
                if let group = run.group {
                    BCDEyebrow(text: group).padding(.top, run.start == 0 ? 4 : 12).padding(.leading, 4)
                }
                if run.keys {
                    capsGrid(setting, run)
                } else {
                    ForEach(Array(run.choices.enumerated()), id: \.element.id) { offset, choice in
                        choiceRow(setting, choice, run.start + offset)
                    }
                }
            }
            if let note = setting.note {
                Text(note).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.top, 6)
            }
        }
    }

    private func pick(_ setting: BCDGameSetting, _ choice: BCDGameChoice) -> () -> Void {
        {
            model.request(.set(setting.id, choice.value))
            backFromSetting(setting.id)
        }
    }

    /// Short choices as caps, a row of them at a time (no lazy grid: the controller's focus
    /// needs every cap laid out to find its neighbours).
    private func capsGrid(_ setting: BCDGameSetting, _ run: ChoiceRun) -> some View {
        let columns = run.choices.allSatisfy { ($0.cap ?? $0.label).count <= 3 } ? 6 : 4
        let rows = stride(from: 0, to: run.choices.count, by: columns).map { Array($0..<min($0 + columns, run.choices.count)) }
        return Grid(horizontalSpacing: 6, verticalSpacing: 6) {
            ForEach(rows, id: \.first) { row in
                GridRow {
                    ForEach(row, id: \.self) { offset in capView(setting, run.choices[offset], run.start + offset) }
                    if row.count < columns {
                        ForEach(row.count..<columns, id: \.self) { _ in Color.clear.frame(height: 1) }
                    }
                }
            }
        }
    }

    private func capView(_ setting: BCDGameSetting, _ choice: BCDGameChoice, _ index: Int) -> some View {
        let chosen = choice.value == setting.value
        let id = "game.panel.choice.\(index)"
        let run = pick(setting, choice)
        return Button(action: run) {
            Text(choice.cap ?? choice.label)
                .font(BCDStyle.label(15)).lineLimit(1).minimumScaleFactor(0.7)
                .foregroundStyle(chosen ? Color.white : BCDStyle.text)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(chosen ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.surface))
                }
                .overlay(alignment: .bottom) {
                    // BCD's own choice, until another is made.
                    if choice.value == setting.defaultValue && !chosen {
                        Capsule().fill(BCDStyle.lavender).frame(width: 14, height: 3).padding(.bottom, 5)
                    }
                }
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 10, action: run)
        .id(id)
        .accessibilityLabel(choice.label + (choice.value == setting.defaultValue ? ", default" : ""))
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }

    private func choiceRow(_ setting: BCDGameSetting, _ choice: BCDGameChoice, _ index: Int) -> some View {
        let chosen = choice.value == setting.value
        let id = "game.panel.choice.\(index)"
        let pick = self.pick(setting, choice)
        return Button(action: pick) {
            HStack(spacing: 12) {
                ZStack {
                    Circle().strokeBorder(chosen ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke), lineWidth: 2)
                    if chosen { Circle().fill(BCDStyle.glow).padding(5) }
                }
                .frame(width: 20, height: 20)
                Text(choice.label).font(BCDStyle.label(15)).lineLimit(2)
                Spacer(minLength: 6)
                if choice.value == setting.defaultValue && !choice.label.localizedCaseInsensitiveContains("default") {
                    Text("Default").font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                }
            }
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 46, alignment: .leading)
            .background(chosen ? BCDStyle.surfaceRaised : BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 14, action: pick)
        .id(id)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }

    // MARK: Steam Cloud

    private func cloudPage(_ cloud: BCDCloudSaves) -> some View {
        let working: Bool = {
            switch cloud {
            case .checking, .moving: return true
            default: return false
            }
        }()
        let d = model.details
        var rows: [BCDGameRow] = []
        if cloud == .signedOut {
            rows.append(BCDGameRow(id: "sync", title: "Sign in to Steam", symbol: "person.crop.circle",
                                   detail: "Cloud saves come with your Steam sign-in.", run: { model.request(.signIn(.steam)) }))
        } else {
            var title = "Sync now"
            if working { title = "Syncing…" } else if case .failed = cloud { title = "Try again" }
            rows.append(BCDGameRow(id: "sync", title: title, symbol: "arrow.triangle.2.circlepath", enabled: !working,
                                   run: { model.request(.syncCloud) }))
        }
        if case .differ = cloud {
            rows.append(BCDGameRow(id: "cloudwins", title: "Use Steam Cloud's saves",
                                   detail: "This device's copies are backed up first.", run: { model.request(.keepCloud(fromCloud: true)) }))
            rows.append(BCDGameRow(id: "devicewins", title: "Keep this device's saves",
                                   detail: "Steam Cloud's copies are backed up first. Saves missing here stay missing, and Steam Cloud keeps them.",
                                   run: { model.request(.keepCloud(fromCloud: false)) }))
        }
        let files = d.cloudConflicts.map { conflict in
            BCDGameRow(id: "save." + conflict.id, title: conflict.name,
                       detail: conflict.lines.cloud + "\n" + conflict.lines.device, still: true)
        }
        var history: [String] = []
        if d.cloudReceived > 0 {
            history.append("Received \(BCDGameText.saves(d.cloudReceived)) from Steam Cloud"
                           + (d.cloudBackedUp > 0 ? ", after backing up the \(BCDGameText.saves(d.cloudBackedUp)) it replaced." : "."))
        }
        if d.cloudSent > 0 { history.append("Sent \(BCDGameText.saves(d.cloudSent)) to Steam Cloud.") }
        return VStack(alignment: .leading, spacing: 10) {
            Text(cloud.explanation).font(BCDStyle.body(15)).fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4).padding(.bottom, 4)
            if let problem = d.cloudProblem, cloud != .failed(problem) {
                Label(problem, systemImage: "exclamationmark.triangle.fill")
                    .font(BCDStyle.body(13)).foregroundStyle(BCDGameColors.amber)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            }
            ForEach(rows) { BCDGameRowView(row: $0) }
            if !files.isEmpty {
                BCDEyebrow(text: "Saves that differ").padding(.top, 10).padding(.leading, 4)
                ForEach(files) { BCDGameRowView(row: $0) }
            }
            ForEach(history, id: \.self) { line in
                Text(line).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            }
            Text("BCD syncs a game's saves when BCD starts and when this page opens, never during play. After playing, choose Upload saves and close BCD in the game's menu, or they go up the next time BCD starts. Every save a sync replaces is kept in Files › BCD › Steam Cloud Backups.")
                .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.top, 4)
        }
    }

    // MARK: Recommended settings

    private var recommendedPage: some View {
        let d = model.details
        let rec = d.recommendation
        var lines: [String] = []
        if let rec {
            lines.append((rec.title.map { "BCD's tested settings for \($0)." } ?? "BCD's standard settings, as no list for this game exists yet."))
            lines.append(rec.inUse ? "This game uses them as they are." : "This game's settings differ: your own changes, or an older list.")
        } else {
            lines.append("There are no recommended settings for this game yet.")
        }
        let busy = model.busy.contains("recommended")
        let rows = [
            BCDGameRow(id: "latest", title: "Get the latest recommended settings",
                       detail: model.messages["recommended"] ?? "Looks online for a newer list and offers what it changes.",
                       enabled: !busy, run: { model.refreshRecommended() }),
            BCDGameRow(id: "reset", title: "Reset to Recommended",
                       detail: "Puts the list back over this game's config and switches.", tone: .destructive,
                       run: { model.request(.resetToRecommended) }),
        ]
        return VStack(alignment: .leading, spacing: 10) {
            Text(lines.joined(separator: " ")).font(BCDStyle.body(15)).fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
            if let rec {
                Text(rec.source).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.bottom, 4)
            }
            ForEach(rows) { BCDGameRowView(row: $0) }
            Text("Recommended settings are the config lines and switches this game ran with in BCD's tests. A game gets them the first time BCD sees it; after that, your own changes stay until you reset.")
                .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.top, 4)
        }
    }

    // MARK: Auto-tune

    private func tuningPage(_ tuning: BCDGameTuning) -> some View {
        var rows = [BCDGameRow(id: "tuning", title: "Auto-tune this game", detail: model.messages["tuning"] ?? tuning.status,
                               toggle: tuning.on, run: { model.request(.setTuning(!tuning.on)) })]
        if !tuning.found.isEmpty {
            rows.append(BCDGameRow(id: "write", title: "Keep what it found", detail: tuning.found.joined(separator: "\n"),
                                   run: { model.request(.writeTuningLines) }))
        }
        if !tuning.history.isEmpty {
            rows.append(BCDGameRow(id: "share", title: "Share the auto-tune report", detail: "What it tried and how each start ended, as text.",
                                   run: {
                                       if let text = model.backend?.tuningReport(for: game) { sharing = BCDGameShare(item: text) }
                                   }))
        }
        if tuning.canUndo {
            rows.append(BCDGameRow(id: "untune", title: "Undo auto-tune", detail: "Removes the lines it added and what it tried.",
                                   tone: .destructive, run: { model.request(.undoTuning) }))
        }
        let history = tuning.history.enumerated().map { index, step in
            BCDGameRow(id: "step.\(index)", title: step.title, detail: step.detail, still: true)
        }
        return VStack(alignment: .leading, spacing: 10) {
            ForEach(rows) { BCDGameRowView(row: $0) }
            if !history.isEmpty {
                BCDEyebrow(text: "What it tried").padding(.top, 10).padding(.leading, 4)
                ForEach(history) { BCDGameRowView(row: $0) }
            }
            Text("Off by default. After this game crashes, freezes or is closed by iOS, the next start tries one known fix line and keeps it only if the game gets further. Your own config always wins, and each try needs BCD to restart.")
                .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.top, 4)
        }
    }
}

private struct BCDGameRowView: View {
    let row: BCDGameRow

    var body: some View {
        if row.still {
            // Shown, not pressed: no button, yet the focus stops on it so a controller scrolls to it.
            content
                .bcdFocusable(row.focusID, cornerRadius: 14) {}
                .id(row.focusID)
        } else {
            Button(action: row.run) { content }
                .buttonStyle(.plain)
                .disabled(!row.enabled)
                .opacity(row.enabled ? 1 : 0.55)
                .bcdFocusable(row.focusID, cornerRadius: 14) { if row.enabled { row.run() } }
                .id(row.focusID)
        }
    }

    /// The title, the value and the arrow on one line; what it does under them, across the row.
    private var content: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center, spacing: 10) {
                if let symbol = row.symbol {
                    Image(systemName: symbol).font(.system(size: 16, weight: .semibold))
                        .foregroundStyle(row.tone == .destructive ? BCDStyle.alert : BCDStyle.lavender)
                        .frame(width: 26)
                }
                Text(row.title).font(BCDStyle.label(15))
                    .foregroundStyle(row.tone == .destructive ? BCDStyle.alert : BCDStyle.text)
                    .lineLimit(2).layoutPriority(1)
                Spacer(minLength: 8)
                if row.changed { Circle().fill(BCDStyle.lavender).frame(width: 7, height: 7).accessibilityLabel("changed") }
                if let on = row.toggle {
                    BCDGameSwitch(on: on)
                } else if let value = row.value {
                    Text(value).font(BCDStyle.body(14))
                        .foregroundStyle(row.tone == .warning ? BCDGameColors.amber : BCDStyle.textSoft)
                        .lineLimit(1).minimumScaleFactor(0.8)
                }
                if row.chevron {
                    Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.textSoft.opacity(0.7))
                }
            }
            if let detail = row.detail, !detail.isEmpty {
                Text(detail).font(BCDStyle.body(13))
                    .foregroundStyle(row.tone == .warning ? BCDGameColors.amber : BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, row.symbol == nil ? 0 : 36)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
        .background(row.still ? BCDStyle.surface.opacity(0.55) : BCDStyle.surface,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contentShape(Rectangle())
    }
}

/// A switch drawn in BCD's colours; the row it sits in turns it (the DLC chooser's rows too: BCDGameDLCChooser.swift).
struct BCDGameSwitch: View {
    let on: Bool
    var body: some View {
        Capsule()
            .fill(on ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke))
            .frame(width: 46, height: 28)
            .overlay(alignment: on ? .trailing : .leading) {
                Circle().fill(.white).frame(width: 22, height: 22).padding(3)
            }
            .animation(.easeOut(duration: 0.18), value: on)
            .accessibilityLabel(on ? "On" : "Off")
    }
}

/// A setting typed as text: the field, what it does, Save and Cancel. (A controller reaches the
/// buttons; typing needs the keyboard.)
private struct BCDGameTextPage: View {
    let setting: BCDGameSetting
    let prompt: String
    let save: (String) -> Void
    let cancel: () -> Void
    @State private var draft: String
    @FocusState private var typing: Bool

    init(setting: BCDGameSetting, prompt: String, save: @escaping (String) -> Void, cancel: @escaping () -> Void) {
        self.setting = setting
        self.prompt = prompt
        self.save = save
        self.cancel = cancel
        _draft = State(initialValue: setting.value)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField(prompt, text: $draft, axis: .vertical)
                .font(.system(size: 15, design: .monospaced))
                .lineLimit(1...5)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($typing)
                .padding(14)
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            if let note = setting.note {
                Text(note).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            }
            HStack(spacing: 12) {
                Button("Cancel", action: cancel)
                    .buttonStyle(BCDOutlineButtonStyle())
                    .bcdFocusable("game.panel.cancel", cornerRadius: 25, action: cancel)
                Button("Save") { save(draft) }
                    .buttonStyle(BCDPrimaryButtonStyle())
                    .bcdFocusable("game.panel.save", cornerRadius: 27) { save(draft) }
            }
            .padding(.top, 4)
            if !setting.chips.isEmpty { BCDGameChipGrid(chips: setting.chips, text: $draft) }   // round 604: BCDGameChips.swift, under Save and Cancel
        }
        .onAppear { typing = true }
        .bcdKeyboardDone { typing = false }
    }
}

/// The game's own config: its key = value lines, edited as text, with Save and Cancel. The text
/// is read when the page opens and let go when it closes.
private struct BCDGameConfigPage: View {
    let game: BCDGame
    @ObservedObject var model: BCDGamePageModel
    let done: () -> Void
    @Environment(\.verticalSizeClass) private var vertical
    @State private var draft = ""
    @State private var loaded = false
    @FocusState private var typing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Lines for this game alone, in the key = value form of BCD's own config. For this game they win over BCD's config, from its next start.")
                .font(BCDStyle.body(14)).fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            // A phone on its side: the keyboard covers the lower half, so Save and Cancel go above the text (feedback AQF0EA).
            if vertical == .compact { buttons }
            TextEditor(text: $draft)
                .font(.system(size: 14, design: .monospaced))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .scrollContentBackground(.hidden)
                .focused($typing)
                // A phone on its side has little height: Save and Cancel stay in sight under it.
                .frame(height: vertical == .compact ? 120 : 210)
                .padding(8)
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
                .accessibilityLabel("Game config")
                .onChange(of: draft) { _, text in
                    if loaded, text != (model.backend?.configText(for: game) ?? "") { model.configDraft = text }
                }
            if vertical != .compact { buttons }
            Text("One setting a line; a line that starts with # is a note. For example: fence-chain = 6, dxmt = d3d11.mipClampBC=1, env.FEX_MULTIBLOCK = 1. An env.NAME = value line sets an environment variable for the game.")
                .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
        }
        .onAppear {
            guard !loaded else { return }
            // The text being edited when the page was rebuilt (turned on its side) comes back; else the saved config.
            draft = model.configDraft ?? model.backend?.configText(for: game) ?? ""
            loaded = true
        }
        .bcdKeyboardDone { typing = false }
    }

    private var buttons: some View {
        HStack(spacing: 12) {
            Button("Cancel", action: cancel)
                .buttonStyle(BCDOutlineButtonStyle())
                .bcdFocusable("game.panel.cancel", cornerRadius: 25, action: cancel)
            Button("Save", action: save)
                .buttonStyle(BCDPrimaryButtonStyle())
                .bcdFocusable("game.panel.save", cornerRadius: 27, action: save)
        }
    }

    private func save() {
        model.request(.saveConfig(draft))
        model.configDraft = nil
        done()
    }

    private func cancel() {
        model.configDraft = nil
        done()
    }
}

/// Find on Steam: the game's name looked up in Steam's store; the match chosen gives the game
/// its name and the store's pictures. The results live only while this screen is open.
private struct BCDGameStoreSearchPage: View {
    let game: BCDGame
    @ObservedObject var model: BCDGamePageModel
    let done: () -> Void
    @Environment(\.displayScale) private var displayScale
    @State private var query: String
    /// The name last searched for, and how many searches there were: each starts a new one.
    @State private var asked = ""
    @State private var tries = 0
    @State private var results: [BCDStoreMatch] = []
    @State private var phase = Phase.idle

    enum Phase: Equatable { case idle, searching, found, nothing, failed(String) }

    init(game: BCDGame, model: BCDGamePageModel, done: @escaping () -> Void) {
        self.game = game
        self.model = model
        self.done = done
        _query = State(initialValue: game.title)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Look this game up in Steam's store. The one you choose gives it its name and the store's pictures.")
                .font(BCDStyle.body(14)).fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            HStack(spacing: 10) {
                TextField("Name", text: $query)
                    .font(BCDStyle.body(16))
                    .submitLabel(.search)
                    .onSubmit(search)
                    .autocorrectionDisabled()
                    .padding(.horizontal, 14).frame(height: 46)
                    .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
                Button(action: search) { Label("Search", systemImage: "magnifyingglass") }
                    .buttonStyle(BCDGameChipStyle(height: 46))
                    .bcdFocusable("game.panel.search", cornerRadius: 23, action: search)
                    .id("game.panel.search")
            }
            status
            ForEach(results) { match in matchRow(match) }
        }
        .onAppear { if tries == 0 { search() } }
        .task(id: "\(tries) \(asked)") { await look(asked) }
    }

    @ViewBuilder private var status: some View {
        switch phase {
        case .idle, .found: EmptyView()
        case .searching: line("Looking in Steam's store…", warning: false)
        case .nothing: line("Nothing found. Try another name.", warning: false)
        case .failed(let why): line(why, warning: true)
        }
    }

    private func line(_ text: String, warning: Bool) -> some View {
        Text(text).font(BCDStyle.body(14)).foregroundStyle(warning ? BCDGameColors.amber : BCDStyle.textSoft)
            .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.top, 4)
    }

    private func search() {
        let name = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        asked = name
        tries += 1
    }

    private func look(_ name: String) async {
        guard !name.isEmpty, let backend = model.backend else { return }
        phase = .searching
        do {
            let found = try await backend.searchStore(name)
            try Task.checkCancellation()
            results = found
            phase = found.isEmpty ? .nothing : .found
        } catch is CancellationError {
            return
        } catch {
            results = []
            phase = .failed("Steam's store didn't answer. Try again in a moment, or set the name and the cover picture yourself.")
        }
    }

    private func matchRow(_ match: BCDStoreMatch) -> some View {
        let id = "game.panel.row.match.\(match.id)"
        let choose = {
            model.request(.useStoreMatch(match))
            done()
        }
        return Button(action: choose) {
            HStack(spacing: 12) {
                // The store's small capsule picture, decoded at the size drawn here.
                BCDArtImage(urls: match.picture.map { [$0] } ?? [], shape: 92.0 / 35.0) {
                    BCDStyle.surfaceRaised
                }
                .frame(width: 92, height: 35)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                Text(match.name).font(BCDStyle.label(15)).lineLimit(2)
                Spacer(minLength: 6)
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.textSoft.opacity(0.7))
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 14, action: choose)
        .id(id)
    }
}

private struct BCDGameShare: Identifiable {
    let item: Any
    let id = UUID()
}

/// The system's share sheet: a saves backup, auto-tune's report.
private struct BCDGameShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
