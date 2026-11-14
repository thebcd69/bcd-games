//
//  GameRecommendationsApply.swift
//  Madeira
//
//  madeira-bcd: writes GameRecommendations.swift's lists into a game's
//  settings (owner's decision, 2026-10-09).
//
//  The first time Madeira sees a game it has a recommendation for (a Steam
//  download finishing, the library opening or saving the game, Game details
//  opening, a start, the madeira-bcd home screen's settings and starts):
//    - with nothing of its own yet (no config file, no lines in upstream's
//      `config` field), the game gets its whole recommendation;
//    - with a config of its own, it keeps it, and only Reset to Recommended
//      replaces it.
//  A config Madeira wrote that nobody has changed since follows a newer
//  version of its list (the config file only); one the player changed, or
//  emptied, is never written again by itself. "Reset to Recommended" at the
//  bottom of the game's settings writes the whole recommendation again,
//  whatever the game holds; for a game without one it empties the game's
//  config. A game without a list of its own gets the standard list (owner's
//  decision, 2026-10-12), which a list for the game replaces later as on
//  first sight while nobody has changed the file; only the Desktop gets none.
//
//  The config file and the switches LibraryPrefs keeps belong to the game's
//  Windows path, which both interfaces share; both are written for both. A
//  library entry's own fields (fastsync, FPS limit, resolution, scaling) are
//  written with them, or later for an entry new to the library.
//

import Foundation

extension GameRecommendations {
    /// What Madeira did for which game: [lowercased Windows path: "<id>#<version>#<file hash>"
    /// for a recommendation it wrote, "" for a game that kept its own config].
    private static let appliedKey = "madeira.recommended.applied"

    private enum Write: String { case firstSight = "first sight", updated, reset }

    /// The recommendation for a game in drive_c: a program, or a Steam game's install folder.
    /// A game Madeira has no list for gets the standard one; a path that names nothing, none.
    static func recommendation(windowsPath: String, steamAppID: Int?) -> GameRecommendation? {
        if let rec = match(windowsPath: windowsPath, steamAppID: steamAppID, fileExists: driveFileExists) { return rec }
        let parts = windowsPath.replacingOccurrences(of: "/", with: "\\").split(separator: "\\")
        return parts.count > 1 ? standard : nil
    }

    static func recommendation(for entry: LibraryEntry) -> GameRecommendation? {
        entry.desktop == true ? nil : recommendation(windowsPath: entry.windowsPath, steamAppID: entry.steamAppID)
    }

    /// A library entry's recommendation, when one is due (see above). `new`: the entry
    /// is not in the library yet; it then also takes the fields of a list written for
    /// its game before (by the home screen, or for an entry since removed). True when
    /// the entry's own fields changed, so the caller saves the entry.
    @discardableResult
    static func prepare(_ entry: inout LibraryEntry, new: Bool = false) -> Bool {
        guard entry.desktop != true else { return false }
        if let found = due(entry.windowsPath, steamAppID: entry.steamAppID, ownLines: entry.config, title: entry.title),
           put(found.rec, to: entry.windowsPath, found.kind, title: entry.title), found.kind == .firstSight {
            takeEntryFields(found.rec, &entry)
            return true
        }
        // After an update too: a new entry takes the list now recorded for its path.
        guard new, let mark = marks[entry.windowsPath.lowercased()], !mark.isEmpty,
              let rec = recommendation(for: entry), mark.hasPrefix(rec.id + "#") else { return false }
        takeEntryFields(rec, &entry)
        LogStore.shared.log("[recommended] \(entry.title): \(rec.id) v\(rec.version) fields for a new library entry")
        return true
    }

    /// A madeira-bcd home screen game (its program's path), before its settings or a start.
    /// madeira-bcd FORK-ONLY: the home screen's entry points here (prepare, libraryLines,
    /// reset(windowsPath:title:), note's `home`) stay out of a pull request to upstream.
    static func prepare(windowsPath: String, title: String) {
        guard let found = due(windowsPath, steamAppID: nil, ownLines: libraryLines(for: windowsPath), title: title) else { return }
        put(found.rec, to: windowsPath, found.kind, title: title)
    }

    /// The game's own lines in the library, which count as the home screen game's own:
    /// upstream's `config` field of the entry for the same program, and the config of the
    /// library Steam game whose install folder holds the program.
    private static func libraryLines(for windowsPath: String) -> String {
        let key = windowsPath.lowercased()
        var lines = ""
        for entry in LibraryModel.shared.entries where entry.desktop != true {
            let path = entry.windowsPath.lowercased()
            if path == key { lines += (entry.config ?? "") + "\n" }
            if entry.steamAppID != nil, key.hasPrefix(path + "\\") {
                lines += (entry.config ?? "") + "\n" + GameProfile(windowsPath: entry.windowsPath).text
            }
        }
        return lines
    }

    /// Game details › Reset to Recommended. Returns what was written; nil when the
    /// game has no recommendation and its config was emptied.
    @discardableResult
    static func reset(_ entry: inout LibraryEntry) -> GameRecommendation? {
        entry.config = nil
        guard let rec = recommendation(for: entry) else { clear(entry.windowsPath, title: entry.title); return nil }
        put(rec, to: entry.windowsPath, .reset, title: entry.title)
        takeEntryFields(rec, &entry)
        return rec
    }

    /// The madeira-bcd home screen's Reset to Recommended; the library entry for the same
    /// program takes the list's fields too. madeira-bcd FORK-ONLY (see prepare).
    @discardableResult
    static func reset(windowsPath: String, title: String) -> GameRecommendation? {
        guard let rec = recommendation(windowsPath: windowsPath, steamAppID: nil) else { clear(windowsPath, title: title); return nil }
        put(rec, to: windowsPath, .reset, title: title)
        let key = windowsPath.lowercased()
        if var entry = LibraryModel.shared.entries.first(where: { $0.desktop != true && $0.windowsPath.lowercased() == key }) {
            entry.config = nil
            takeEntryFields(rec, &entry)
            LibraryModel.shared.save(entry)
        }
        return rec
    }

    /// The line under Reset to Recommended; `home`: the madeira-bcd home screen's settings.
    static func note(_ rec: GameRecommendation?, home: Bool = false) -> String {
        guard let rec else { return "There is no recommended setup for this game yet. Resetting clears its config." }
        let lines = GameProfile.parse(rec.fileText).count
        if rec.id == standardID {
            return "There is no list for this game yet, so BCD suggests its standard settings: \(lines) config line\(lines == 1 ? "" : "s"). "
                + "They switch off the emulator's code-patch report, which makes many games close at start. Resetting puts them in place of this game's config."
        }
        let summary = rec.summary(home: home)
        let switches = summary.isEmpty ? "" : "; " + summary
        return "The recommended setup for \(rec.title): \(lines) config lines\(switches). A reset writes it over this game's config and those switches."
    }

    /// Whether a game follows its list: the config BCD wrote is still exactly its config, so a
    /// newer list reaches it by itself (GameRecommendationsSource.swift). false: the game keeps a
    /// config of its own or the player changed it, and only Reset to Recommended brings the list;
    /// nil: nothing was written for the game yet.
    static func followsList(windowsPath: String) -> Bool? {
        guard let mark = marks[windowsPath.lowercased()] else { return nil }
        return !mark.isEmpty && mark.hasSuffix("#" + fileHash(GameProfile(windowsPath: windowsPath).text))
    }

    /// The question before a reset.
    static func question(_ rec: GameRecommendation?) -> String {
        guard let rec else { return "This game has no recommended setup yet. Clear its config?" }
        if rec.id == standardID { return "Swap this game's config for BCD's standard settings? Your own edits to it will be gone." }
        return "Write BCD's recommended setup for \(rec.title) over this game's config and switches? Your own edits to them will be gone."
    }

    // MARK: -

    private static func due(_ windowsPath: String, steamAppID: Int?, ownLines: String?,
                            title: String) -> (rec: GameRecommendation, kind: Write)? {
        let mark = marks[windowsPath.lowercased()]
        guard mark != "", let rec = recommendation(windowsPath: windowsPath, steamAppID: steamAppID) else { return nil }
        let text = GameProfile(windowsPath: windowsPath).text
        if let mark {
            // Written before: a newer list replaces it while the file is exactly what was written.
            guard mark != stamp(rec), mark.hasSuffix("#" + fileHash(text)) else { return nil }
            // The standard list gives way to the game's own list as on first sight, switches too.
            let fromStandard = mark.hasPrefix(standardID + "#") && rec.id != standardID
            return (rec: rec, kind: fromStandard ? Write.firstSight : Write.updated)
        }
        if !(text + (ownLines ?? "")).trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            // First seen with a config of its own: it stays, now and later.
            setMark("", windowsPath)
            LogStore.shared.log("[recommended] \(title): keeps its own config; \(rec.id) v\(rec.version) is one Reset to Recommended away")
            return nil
        }
        return (rec: rec, kind: .firstSight)
    }

    /// The config file, and on first sight or a reset the switches LibraryPrefs keeps for
    /// the game's path: AVX, NVIDIA and Wine's C++ runtime, which both interfaces use,
    /// and the home screen's forms of the rest (Safe thread sync is fastsync off; Screen
    /// size takes the resolution when it is one of its sizes).
    @discardableResult
    private static func put(_ rec: GameRecommendation, to windowsPath: String, _ kind: Write, title: String) -> Bool {
        let file = GameProfile(windowsPath: windowsPath)
        file.text = rec.fileText
        guard file.text == rec.fileText else {
            LogStore.shared.log("[recommended] \(title): could not write \(rec.id)", level: .error)
            return false
        }
        if kind != .updated {
            if let on = rec.avx { LibraryPrefs.setAVX(on, for: windowsPath) }
            if let on = rec.nvidia { LibraryPrefs.setNvidia(on, for: windowsPath) }
            if let on = rec.wineVCRT { LibraryPrefs.setWineVCRT(on, for: windowsPath) }
            if let on = rec.fastSync { LibraryPrefs.setSafeSync(!on, for: windowsPath) }
            if let size = rec.resolution, LibraryPrefs.screenSizes.contains(size) { LibraryPrefs.setScreen(size, for: windowsPath) }
        }
        setMark(stamp(rec), windowsPath)
        LogStore.shared.log("[recommended] \(title): \(rec.id) v\(rec.version) written (\(kind.rawValue))")
        return true
    }

    private static func clear(_ windowsPath: String, title: String) {
        GameProfile(windowsPath: windowsPath).text = ""
        setMark(nil, windowsPath)
        LogStore.shared.log("[recommended] \(title): none for this game; its config was emptied (reset)")
    }

    /// The switches a library entry keeps itself.
    private static func takeEntryFields(_ rec: GameRecommendation, _ entry: inout LibraryEntry) {
        if let on = rec.fastSync { entry.fastSync = on }
        if let on = rec.semaphoreFastPath { entry.semaphoreFastPath = on }
        if let mode = rec.fpsMode { entry.fpsMode = mode }
        if let size = rec.resolution {
            // This screen's shape at 720 lines; a screen whose default has them keeps its own.
            if size != GameRecommendation.screen720 { entry.resolution = size }
            else if let lines720 = ResolutionChoices.screen720Value { entry.resolution = lines720 }
        }
        if let mode = rec.display { entry.display = mode == DisplayMode.fit.rawValue ? nil : mode }
    }

    private static var marks: [String: String] {
        (UserDefaults.standard.dictionary(forKey: appliedKey) as? [String: String]) ?? [:]
    }

    private static func setMark(_ value: String?, _ windowsPath: String) {
        var all = marks
        all[windowsPath.lowercased()] = value
        UserDefaults.standard.set(all, forKey: appliedKey)
    }

    private static func stamp(_ rec: GameRecommendation) -> String { "\(rec.id)#\(rec.version)#\(fileHash(rec.fileText))" }

    private static func fileHash(_ text: String) -> String { String(CoverStore.stableHash(text), radix: 16) }

    /// Whether a C:\ path exists in drive_c; the name's case does not matter, as on Windows.
    static func driveFileExists(_ windowsPath: String) -> Bool {
        let path = windowsPath.replacingOccurrences(of: "\\", with: "/")
        guard path.lowercased().hasPrefix("c:/") else { return false }
        let url = LibraryModel.drive.appendingPathComponent(String(path.dropFirst(3)))
        let name = url.lastPathComponent.lowercased()
        let names = (try? FileManager.default.contentsOfDirectory(atPath: url.deletingLastPathComponent().path)) ?? []
        return names.contains { $0.lowercased() == name }
    }
}

// MARK: - A heavy game's one-time graphics tip

/// madeira-bcd (owner, 2026-10-28, build 622): the heavy games BCD runs (RDR2, GTA V, Ghost of Tsushima, God of War,
/// the two Horizons, the three Spider-Man games, Rift Apart, the Crysis remasters) carry `low-graphics-tip = 1` in their
/// recommended lists. Before such a game's first start BCD says once to turn the game's own graphics to the lowest preset
/// and AMD FSR, where the game has it, to Performance (owner 2026-11-14; was "Balanced or Performance"); OK starts the game. The game's own file decides (a
/// player who sets 0 is not told); a game with no file yet is read from its list. Once per game (its Windows path).
enum BCDGraphicsTip {
    static let key = "low-graphics-tip"
    private static let shownKey = "bcd.lowGraphicsTip.shown"
    static let title = "Before you play"
    static let message = "This is a demanding game. In the game's own graphics settings, set everything to the lowest "
        + "preset (Very Low, or Low where there is no Very Low). If the game offers AMD FSR, set it to Performance.\n\nBCD's recommended settings limit it to 30 FPS; you can change the limit on the game's page."

    /// True when the tip is due before this start.
    static func due(_ entry: LibraryEntry) -> Bool {
        guard entry.desktop != true else { return false }
        let path = entry.windowsPath.lowercased()
        guard path.count > 3, !shown.contains(path) else { return false }
        let own = GameProfile.parse(GameProfile(windowsPath: entry.windowsPath).text)
        let value = !own.isEmpty
            ? own[key]
            : GameRecommendations.recommendation(for: entry).flatMap { GameProfile.parse($0.fileText)[key] }
        return value == "1"
    }

    /// The tip was shown for this game: never again.
    static func markShown(_ entry: LibraryEntry) {
        let path = entry.windowsPath.lowercased()
        var all = shown
        guard !all.contains(path) else { return }
        all.append(path)
        UserDefaults.standard.set(all, forKey: shownKey)
    }

    private static var shown: [String] { UserDefaults.standard.stringArray(forKey: shownKey) ?? [] }
}
