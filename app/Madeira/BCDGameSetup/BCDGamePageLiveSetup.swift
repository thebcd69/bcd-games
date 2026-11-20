// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The game page's setup sections, engine side (BCDGameSetupPage.swift has the words and rows; BCDGameSetupLaunch.swift does the work
// at a start). Engine-bound like BCDGamePageLive.swift, so the preview build leaves it out (it lives in a folder the preview build
// does not read).
//
// Every row is a line of the game's own file (GameProfiles.swift). The state is read from that file and the game's folder when
// Options is open (nothing is scanned while a game runs); a change writes the line and the page reads again.

@MainActor final class BCDGameSetupNotes {
    static let shared = BCDGameSetupNotes()
    /// What an action last said, by game id and setting id.
    var notes: [String: [String: String]] = [:]
    /// The DLLs of the components in a game's folder, with when they were looked for.
    var shipped: [String: (at: Date, names: [String])] = [:]
}

extension BCDGamePageLive {
    // MARK: What the sections show

    /// The sections for an installed game: for the Wine desktop only its own; for another game Windows, Components, CPU translation
    /// and Game fixes, and Wine desktop when the game starts in one.
    func setupGroups(_ game: BCDGame, _ entry: LibraryEntry, _ profile: GameProfile) -> [BCDGameSettingGroup] {
        let values = profile.values
        var state = BCDGameSetupState()
        state.windowsVersion = values[BCDWindowsVersion.key] ?? ""
        state.is64Bit = entry.bits == 64
        state.fexPreset = BCDFEXPresets.current(values)
        state.smcFull = values[BCDFEXPresets.smc] == "full"
        state.notes = BCDGameSetupNotes.shared.notes[game.id] ?? [:]
        if entry.desktop == true || entry.runsInDesktop {
            state.desktop = BCDSetupDesktopState(dpi: values[BCDWineDesktop.dpiKey] ?? "", theme: values[BCDWineDesktop.themeKey] ?? "",
                                                wallpaper: values[BCDWineDesktop.wallpaperKey] ?? "")
        }
        if entry.desktop == true {
            return BCDGameSetupPage.desktopSection(state).map { [$0] } ?? []
        }
        let drive = BCDGameSetup.drive
        let context = BCDFixPaths.Context(users: BCDFixPaths.users(drive: drive) + ["Public"], install: BCDGameSetup.installFolder(entry))
        let listed = BCDGameFixes.list(values)
        state.fixes = listed.fixes.map {
            BCDSetupFixItem(number: $0.number, enabled: $0.enabled, summary: $0.action.summary, problem: BCDGameFixRunner.problem($0, context))
        }
        state.fixProblems = listed.problems
        state.dll.shadow = BCDDllComponents.names(values[BCDDllComponents.shadowKey])
        state.dll.off = BCDDllComponents.switchedOff(values[BCDDllComponents.overridesKey])
        state.dll.nativeFirst = BCDDllComponents.nativeFirst(values[BCDDllComponents.overridesKey])
        state.dll.shipped = shippedDLLs(game, entry)
        state.dll.runtimes = Self.runtimesInstalled(drive: drive)
        let key = GameSaveAuto.key(entry)
        let folder = BCDSaveRedirect.folder(documents: LibraryModel.documents)
        state.saves = GameSaveStore.record(key).roots.filter(BCDSaveRedirect.isEligible).map { root in
            let place: BCDSetupSaveItem.Place
            switch BCDSaveRedirect.state(of: root, game: key, drive: drive, folder: folder) {
            case .inPrefix: place = .prefix
            case .redirected: place = .files
            case .needsLink: place = .needsLink
            case .missing: place = .missing
            }
            return BCDSetupSaveItem(path: root.path, label: GameSaveRules.display(root), place: place)
        }
        state.savesBlocked = Self.sessionRunning ? "Quit the game first." : nil
        var start = BCDGameStartState()
        start.plan = BCDStartPlan.read(values)
        start.swap = values[BCDStartPlan.swapKey] ?? ""
        start.ownStart = entry.usesLaunchOptions
        start.notes = state.notes
        return BCDGameSetupPage.sections(state) + [BCDGameStartPage.group(start)]
    }

    /// The DLLs of the components that the game's folder holds (down to three folders deep, 3,000 entries at most), looked for
    /// again at most every 20 seconds.
    private func shippedDLLs(_ game: BCDGame, _ entry: LibraryEntry) -> [String] {
        let notes = BCDGameSetupNotes.shared
        if let cached = notes.shipped[game.id], Date().timeIntervalSince(cached.at) < 20 { return cached.names }
        var files: [String] = []
        if let folder = BCDGameSetup.installFolder(entry) {
            let base = BCDFixPaths.onDisk(base: BCDGameSetup.drive, parts: folder.split(separator: "/").map(String.init))
            var seen = 0
            if let walk = FileManager.default.enumerator(atPath: base.path) {
                while let rel = walk.nextObject() as? String, seen < 3000 {
                    seen += 1
                    if rel.split(separator: "/").count > 4 { walk.skipDescendants(); continue }
                    if rel.lowercased().hasSuffix(".dll") { files.append((rel as NSString).lastPathComponent) }
                }
            }
        }
        let names = BCDDllComponents.shipped(files: files)
        notes.shipped[game.id] = (Date(), names)
        return names
    }

    /// The runtimes BCD's installer says are installed (its status file), by name; nil when the installer never ran.
    nonisolated static func runtimesInstalled(drive: URL) -> [String]? {
        let file = drive.appendingPathComponent("ProgramData/BCD/Runtimes/status.txt")
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        let names = ["xna": "XNA Framework 4.0", "physx": "PhysX (legacy)", "openal": "OpenAL", "dx2010": "DirectX June 2010"]
        var out: [String] = []
        for line in text.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }) {
            let parts = line.split(separator: "=", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
            if parts.count == 2, parts[1] == "installed" || parts[1] == "partly", let name = names[parts[0]] {
                out.append(parts[1] == "partly" ? name + " (partly)" : name)
            }
        }
        return out
    }

    nonisolated static var sessionRunning: Bool {
        wine_process_is_running() != 0 || wineserver_is_running() != 0
    }

    /// The Library section's extra row: a game with a program of its own and no store picture or cover of its own. The program on the
    /// external drive is read like any other (it is only read).
    func exeIconRows(_ entry: LibraryEntry) -> [BCDGameSetting] {
        guard entry.desktop != true, entry.steamAppID == nil, entry.steamID == nil, entry.coverFile == nil, entry.launchRelativePath.lowercased().hasSuffix(".exe") else { return [] }
        return [BCDGameSetupPage.exeIconRow([:])]
    }

    // MARK: What a row does

    private func setupSay(_ game: BCDGame, _ setting: String, _ text: String?) {
        var all = BCDGameSetupNotes.shared.notes[game.id] ?? [:]
        all[setting] = text
        BCDGameSetupNotes.shared.notes[game.id] = all
        onChange?()
    }

    /// Sets (or, with "", removes) a line of the game's own file; an entry the library does not hold yet is saved with it.
    private func setupLine(_ entry: LibraryEntry, _ key: String, _ value: String) {
        let file = GameProfile(windowsPath: entry.windowsPath)
        if (file.get(key) ?? "") != value { file.set(key, value.isEmpty ? nil : value) }
        if !LibraryModel.shared.entries.contains(where: { $0.id == entry.id }) { LibraryModel.shared.save(entry) }
    }

    /// The setup sections' rows: true when `setting` was one of them.
    func setupSet(_ setting: String, to value: String, for game: BCDGame) -> Bool {
        guard setting.hasPrefix("setup."), let entry = entry(for: game) else { return false }
        let profile = GameProfile(windowsPath: entry.windowsPath)
        switch setting {
        case BCDGameSetupPage.version:
            guard value.isEmpty || BCDWindowsVersion.entry(value) != nil else { return true }
            setupLine(entry, BCDWindowsVersion.key, value)
            setupSay(game, setting, nil)

        case BCDGameSetupPage.fex:
            guard value.isEmpty || BCDFEXPresets.all.contains(where: { $0.id == value }) else { return true }
            for (key, line) in BCDFEXPresets.edits(for: value) { setupLine(entry, key, line) }
            setupSay(game, setting, nil)

        case BCDGameSetupPage.smc:
            setupLine(entry, BCDFEXPresets.smc, value == "1" ? "full" : "")
            setupSay(game, setting, nil)

        case BCDGameSetupPage.dllOff:
            let parsed = BCDGameSetupPage.parseOff(value)
            guard parsed.rejected.isEmpty else {
                setupSay(game, setting, "Not saved: \(parsed.rejected.joined(separator: ", ")) \(parsed.rejected.count == 1 ? "is" : "are") not a DLL name (letters, digits, _ . - and ending in .dll).")
                return true
            }
            let first = Set(BCDDllComponents.nativeFirst(profile.get(BCDDllComponents.overridesKey)).map(BCDDllComponents.bare))
            let both = parsed.names.filter { first.contains(BCDDllComponents.bare($0)) }
            guard both.isEmpty else {
                setupSay(game, setting, "Not saved: \(both.joined(separator: ", ")) \(both.count == 1 ? "is" : "are") set to the game's own copy first below. Take \(both.count == 1 ? "it" : "them") out there first.")
                return true
            }
            let line = BCDDllComponents.settingOff(profile.get(BCDDllComponents.overridesKey), to: parsed.names)
            setupLine(entry, BCDDllComponents.overridesKey, line)
            setupSay(game, setting, nil)

        case BCDGameSetupPage.dllNativeFirst:   // 2026-11-20: a mod loader's DLL from the game's folder first (WINEDLLOVERRIDES n,b)
            let parsed = BCDGameSetupPage.parseOff(value)
            guard parsed.rejected.isEmpty else {
                setupSay(game, setting, "Not saved: \(parsed.rejected.joined(separator: ", ")) \(parsed.rejected.count == 1 ? "is" : "are") not a DLL name (letters, digits, _ . - and ending in .dll).")
                return true
            }
            let current = profile.get(BCDDllComponents.overridesKey)
            let off = Set(BCDDllComponents.switchedOff(current).map(BCDDllComponents.bare))
            let both = parsed.names.filter { off.contains(BCDDllComponents.bare($0)) }
            guard both.isEmpty else {
                setupSay(game, setting, "Not saved: \(both.joined(separator: ", ")) \(both.count == 1 ? "is" : "are") switched off above. Take \(both.count == 1 ? "it" : "them") out there first.")
                return true
            }
            setupLine(entry, BCDDllComponents.overridesKey, BCDDllComponents.settingNativeFirst(current, to: parsed.names))
            setupSay(game, setting, nil)

        case let id where id.hasPrefix(BCDGameSetupPage.componentsPrefix):
            let name = String(id.dropFirst(BCDGameSetupPage.componentsPrefix.count))
            guard let component = BCDDllComponents.all.first(where: { $0.id == name }) else { return true }
            let wine = value == "wine"
            // The engine's switch acts on 64-bit programs only; the row says so, and a pick that could do nothing writes nothing.
            if wine && entry.bits != 64 {
                setupSay(game, id, "Wine's own copies can be forced for 64-bit games only: a 32-bit game loads a DLL from its own folder first.")
                return true
            }
            let shipped = shippedDLLs(game, entry)
            let mine = Set(component.names)
            let ships = shipped.filter { mine.contains($0.lowercased()) }
            if wine && ships.isEmpty {
                setupSay(game, id, "Nothing to do: the game's folder has none of these DLLs.")
                return true
            }
            let list = BCDDllComponents.shadow(component, wine: wine, shipped: shipped, current: BCDDllComponents.names(profile.get(BCDDllComponents.shadowKey)))
            setupLine(entry, BCDDllComponents.shadowKey, BCDDllComponents.joined(list))
            setupSay(game, id, nil)

        case BCDGameSetupPage.exeIcon:
            useProgramIcon(game, entry)

        case let id where id.hasPrefix(BCDGameSetupPage.savePrefix):
            moveSaves(String(id.dropFirst(BCDGameSetupPage.savePrefix.count)), to: value, game: game, entry: entry, setting: id)

        case BCDGameStartPage.delay:
            guard let n = Int(value), (0...300).contains(n) else { return true }
            setupLine(entry, BCDStartPlan.delayKey, n == 0 ? "" : String(n))
            setupSay(game, setting, nil)
        case BCDGameStartPage.second:
            let text = value.trimmingCharacters(in: .whitespaces)
            guard text.isEmpty || BCDStartPlan.isProgram(text) else {
                setupSay(game, setting, "Not saved: a program on drive C: that ends in .exe, .bat or .cmd, like C:\\Tools\\mapper.exe. A program that protects a game is not accepted.")
                return true
            }
            setupLine(entry, BCDStartPlan.secondKey, text)
            setupSay(game, setting, nil)
        case BCDGameStartPage.secondArgs:
            let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.contains(where: { "\n\r\0".contains($0) }), text.utf8.count <= 500 else { return true }
            setupLine(entry, BCDStartPlan.secondArgsKey, text)
            setupSay(game, setting, nil)
        case BCDGameStartPage.secondStop:
            setupLine(entry, BCDStartPlan.secondStopKey, value == "1" ? "" : "0")
            setupSay(game, setting, nil)
        case BCDGameStartPage.ruleAdd:
            let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return true }
            let values = profile.values
            let number = BCDStartPlan.nextRuleNumber(values)
            let parsed = BCDWindowRule.parse(text, number: number)
            guard let rule = parsed.rule else { setupSay(game, setting, "Not saved: \(parsed.error ?? "the rule cannot be read")."); return true }
            guard BCDStartPlan.read(values).rules.count < BCDStartPlan.maxRules else {
                setupSay(game, setting, "Not saved: a game can have \(BCDStartPlan.maxRules) window rules at most.")
                return true
            }
            setupLine(entry, BCDStartPlan.rulePrefix + String(number), rule.line)
            setupSay(game, setting, nil)
        case let id where BCDGameStartPage.ruleNumber(id) != nil:
            if value == "remove", let number = BCDGameStartPage.ruleNumber(id) { setupLine(entry, BCDStartPlan.rulePrefix + String(number), "") }
            setupSay(game, id, nil)
        case BCDGameStartPage.swap:
            let text = value.trimmingCharacters(in: .whitespaces)
            if !text.isEmpty, let why = BCDStartPlan.swapProblem(text) {
                setupSay(game, setting, "Not saved: \(why).")
                return true
            }
            setupLine(entry, BCDStartPlan.swapKey, text)
            setupSay(game, setting, nil)

        case BCDGameSetupPage.fixAdd:
            let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return true }
            let parsed = BCDFixAction.parse(text)
            guard let action = parsed.action else {
                setupSay(game, setting, "Not saved: \(parsed.error ?? "the line cannot be read").")
                return true
            }
            let values = profile.values
            guard BCDGameFixes.list(values).fixes.count < BCDGameFixes.maxFixes else {
                setupSay(game, setting, "Not saved: a game can have \(BCDGameFixes.maxFixes) fixes at most.")
                return true
            }
            setupLine(entry, BCDGameFixes.key(BCDGameFixes.nextNumber(values), enabled: true), action.line)
            setupSay(game, setting, nil)

        case let id where BCDGameSetupPage.fixNumber(id) != nil:
            guard let number = BCDGameSetupPage.fixNumber(id) else { return true }
            let values = profile.values
            let onKey = BCDGameFixes.key(number, enabled: true), offKey = BCDGameFixes.key(number, enabled: false)
            guard let line = values[onKey] ?? values[offKey] else { return true }
            switch value {
            case "on": setupLine(entry, offKey, ""); setupLine(entry, onKey, line)
            case "off": setupLine(entry, onKey, ""); setupLine(entry, offKey, line)
            case "remove": setupLine(entry, onKey, ""); setupLine(entry, offKey, "")
            default: break
            }
            setupSay(game, id, nil)

        case BCDGameSetupPage.desktopDPI:
            guard value.isEmpty || BCDWineDesktop.isDPI(value) else { return true }
            setupLine(entry, BCDWineDesktop.dpiKey, value)
            setupSay(game, setting, nil)
        case BCDGameSetupPage.desktopTheme:
            guard value.isEmpty || value == "dark" else { return true }
            setupLine(entry, BCDWineDesktop.themeKey, value)
            setupSay(game, setting, nil)
        case BCDGameSetupPage.desktopWallpaper:
            let path = value.trimmingCharacters(in: .whitespaces)
            guard path.isEmpty || BCDWineDesktop.isWallpaperPath(path) else {
                setupSay(game, setting, "Not saved: the wallpaper is a .bmp file on drive C:, like C:\\bcd-wallpaper.bmp.")
                return true
            }
            setupLine(entry, BCDWineDesktop.wallpaperKey, path)
            setupSay(game, setting, nil)

        default:
            break
        }
        return true
    }
}

extension BCDGamePageLive {
    // MARK: The program's icon as the cover

    private func useProgramIcon(_ game: BCDGame, _ entry: LibraryEntry) {
        let id = BCDGameSetupPage.exeIcon
        setupNote(game, id, "Reading the program's icon…")
        let relative = entry.launchRelativePath
        Task { [weak self] in
            let made = await Task.detached(priority: .userInitiated) { () -> (png: Data, width: Int)? in
                guard let program = try? LibraryModel.executable(relative), let found = BCDExeIcon.find(at: program),
                      let png = BCDExeIconCover.png(fromICO: found.ico) else { return nil }
                return (png, found.width)
            }.value
            guard let self else { return }
            guard let made else {
                self.setupNote(game, id, "This program has no icon BCD can read.")
                return
            }
            let temporary = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-icon-cover-\(UUID().uuidString).png")
            defer { try? FileManager.default.removeItem(at: temporary) }
            do {
                try made.png.write(to: temporary)
                try await self.setCover(from: temporary, for: game)
                self.setupNote(game, id, "The cover is now the program's \(made.width) × \(made.width) icon. Choose a picture of your own, or the store's, to change it.")
            } catch {
                self.setupNote(game, id, "The cover could not be made: \(error.localizedDescription)")
            }
        }
    }

    // MARK: Saves outside the prefix

    private func moveSaves(_ path: String, to value: String, game: BCDGame, entry: LibraryEntry, setting: String) {
        guard value == "prefix" || value == "files" else { return }
        let key = GameSaveAuto.key(entry)
        guard let root = GameSaveStore.record(key).roots.first(where: { $0.path.lowercased() == path.lowercased() && BCDSaveRedirect.isEligible($0) }) else { return }
        guard !Self.sessionRunning, LibraryModel.shared.current == nil else {
            setupNote(game, setting, "Quit the game first.")
            return
        }
        let drive = BCDGameSetup.drive, folder = BCDSaveRedirect.folder(documents: LibraryModel.documents), title = entry.title
        setupNote(game, setting, value == "files" ? "Moving the folder…" : "Moving the folder back…")
        Task { [weak self] in
            let outcome = await Task.detached(priority: .userInitiated) { () -> String in
                do {
                    if value == "files" {
                        let moved = try BCDSaveRedirect.redirect(root, game: key, title: title, drive: drive, folder: folder)
                        return "Moved \(moved.files) file\(moved.files == 1 ? "" : "s") (\(BCDFormat.bytes(moved.bytes))) to Files › BCD › Game Saves › \(moved.place.replacingOccurrences(of: "/", with: " › "))."
                    }
                    try BCDSaveRedirect.restore(root, game: key, drive: drive, folder: folder)
                    return "The folder is back in the prefix."
                } catch {
                    return error.localizedDescription
                }
            }.value
            LogStore.shared.log("[saves] \(title): \(value == "files" ? "move out of the prefix" : "move back"): \(outcome.hasPrefix("Moved") || outcome.hasPrefix("The folder is back") ? "done" : "refused")")
            self?.setupNote(game, setting, outcome)
        }
    }

    fileprivate func setupNote(_ game: BCDGame, _ setting: String, _ text: String?) {
        var all = BCDGameSetupNotes.shared.notes[game.id] ?? [:]
        all[setting] = text
        BCDGameSetupNotes.shared.notes[game.id] = all
        onChange?()
    }
}

extension BCDGamePageLive {
    /// A picture from Files as the Wine desktop's wallpaper: cut to the desktop's size (the game's Resolution), written as
    /// C:\bcd-wallpaper.bmp, and set as `desktop.wallpaper`. Throws with the reason in BCD's words.
    func setWallpaper(from file: URL, for game: BCDGame) async throws {
        guard let entry = entry(for: game), let size = BCDWallpaper.size(entry.resolution) else { throw BCDGameError("This game's desktop size is not known.") }
        let target = BCDGameSetup.drive.appendingPathComponent("bcd-wallpaper.bmp")
        try await Task.detached(priority: .userInitiated) {
            let opened = file.startAccessingSecurityScopedResource()
            defer { if opened { file.stopAccessingSecurityScopedResource() } }
            guard let data = BCDWallpaper.bmp(from: file, width: size.width, height: size.height) else {
                throw BCDGameError("BCD couldn't read this picture. Choose a JPEG, PNG or HEIC picture under 40 MB.")
            }
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: target, options: .atomic)
        }.value
        let profile = GameProfile(windowsPath: entry.windowsPath)
        profile.set(BCDWineDesktop.wallpaperKey, "C:\\bcd-wallpaper.bmp")
        if !LibraryModel.shared.entries.contains(where: { $0.id == entry.id }) { LibraryModel.shared.save(entry) }
    }
}
