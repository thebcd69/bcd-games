//
//  LibraryBCD.swift
//  Madeira
//
//  madeira-bcd: this fork's per-game options inside upstream's library engine.
//
//  A game's madeira-bcd options are keyed by its Windows path: the switches in
//  LibraryPrefs (AVX, Wine's C++ runtime, NVIDIA) and the game's own config
//  file (GameProfiles.swift: MetalFX, frame generation, D3D12 switches, any
//  bcd.cfg key). BCDLaunch exports them at every launch.
//

import SwiftUI
import UIKit

/// What a launch exports on top of the exe, its arguments and its screen.
enum BCDLaunch {
    /// The switches both interfaces share. Everything is set or unset, so a
    /// previous launch's choice never leaks into this one.
    static func applyExtras(avx: Bool, wineVCRT: Bool, nvidia: Bool, profile: GameProfile?) {
        if avx { setenv("MADEIRA_FEX_AVX", "1", 1) } else { unsetenv("MADEIRA_FEX_AVX") }
        if wineVCRT { setenv("MADEIRA_WINE_VCRT", "1", 1) } else { unsetenv("MADEIRA_WINE_VCRT") }
        var dxmtExtra: [String] = []
        if nvidia {
            setenv("DXMT_ENABLE_NVEXT", "1", 1)
            // The same GeForce RTX 3060 (10DE:2544) win32u registers as the
            // display adapter (sysparams_ios.c, ios_virtual_gpu_ids); merged
            // into DXMT_CONFIG by ContentView.
            dxmtExtra.append("dxgi.customDeviceId=2544")
            // DXGI's output carries user32's monitor handle and user32's mode
            // list, so a game can find its monitor on the adapter. Default-on
            // in this fork until the build 222 switch to upstream, where both
            // became 32-bit-only switches; Ghost of Tsushima has said "No
            // installed graphics card ... monitor connected to it" on every
            // launch since (logs 2026-10-01 16:15 / 16:16 / 17:04; 2026-09-28
            // ran with both). A game file's env.NAME = 0 still wins.
            setenv("DXMT_WSI_MONITOR_IDENTITY", "1", 1)
            setenv("DXMT_WSI_MODE_TABLE", "1", 1)
        } else {
            unsetenv("DXMT_ENABLE_NVEXT")
            unsetenv("DXMT_WSI_MONITOR_IDENTITY")
            unsetenv("DXMT_WSI_MODE_TABLE")
        }
        // The game's own settings (GameProfiles.swift).
        if let p = profile, p.hasSettings, let u = p.url {
            setenv("MADEIRA_CFG_GAME", u.path, 1)
        } else {
            unsetenv("MADEIRA_CFG_GAME")
        }
        // MetalFX: the D3D12 runtime reads metalfx-upscale from that file; a
        // D3D11 game gets DXMT's own MetalFX swapchain at the same factor.
        // madeira-bcd present-fx: with env.MADEIRA_PRESENT_FX = 1, or for "to the screen", the session's start decides
        // (BCDPresentFXLive.launch in ContentView, which knows the desktop's size); in every other case this is as before.
        if BCDPresentFXLive.decidesAtStart(profile) {
            unsetenv("DXMT_METALFX_SPATIAL_SWAPCHAIN")
        } else
        if let f = profile?.metalFXFactor {
            setenv("DXMT_METALFX_SPATIAL_SWAPCHAIN", "1", 1)
            dxmtExtra.append("d3d11.metalSpatialUpscaleFactor=\(f)")
        } else {
            unsetenv("DXMT_METALFX_SPATIAL_SWAPCHAIN")
        }
        if dxmtExtra.isEmpty { unsetenv("MADEIRA_DXMT_EXTRA") }
        else { setenv("MADEIRA_DXMT_EXTRA", dxmtExtra.joined(separator: ";"), 1)   /* DXMT splits on ";" */ }
    }

    /// A start from upstream's library: this game's madeira-bcd options and
    /// its own session log. The FPS limit is the entry's (upstream's FPS
    /// picker, which offers 40 FPS too).
    /// `sessionLog: false` for a Steam game started through Madeira Dock: the
    /// native side names that log after the program Valve's client starts
    /// (process_ios.c, madeira_steam_session_log).
    static func applyLibrary(_ entry: LibraryEntry, sessionLog: Bool = true) {
        if entry.desktop == true {
            applyExtras(avx: false, wineVCRT: false, nvidia: false, profile: nil)
            LogStore.shared.startSessionLog(program: "explorer.exe")
            return
        }
        let path = entry.windowsPath
        let profile = GameProfile(windowsPath: path)
        applyExtras(avx: LibraryPrefs.avx(path), wineVCRT: LibraryPrefs.wineVCRT(path),
                    nvidia: LibraryPrefs.nvidia(path), profile: profile)
        // madeira-bcd: auto-tune's lines for a game it tunes (AutoTune.swift); off, nothing.
        if AutoTune.enabled { AutoTune.exportLayer(for: entry) }
        LibraryPrefs.markPlayed(entry.title)
        let program = path.split(separator: "\\").last.map(String.init) ?? path
        LogStore.shared.log("[bcd] library launch \(program): avx=\(LibraryPrefs.avx(path) ? 1 : 0) "
                            + "wine-vcrt=\(LibraryPrefs.wineVCRT(path) ? 1 : 0) nvidia=\(LibraryPrefs.nvidia(path) ? 1 : 0) "
                            + "game-config=\(profile.hasSettings ? 1 : 0) metalfx=\(profile.metalFXFactor.map { String($0) } ?? "off")"
                            // madeira-bcd (round 624, agent D2 S1 / Discord W1): whether BCD runs inside LiveContainer (the three wineserver
                            // bind failures were all LiveContainer installs); appended last, so readers of the fields before it are unchanged.
                            + " lc=\(getenv("LC_HOME_PATH") != nil ? 1 : 0)")
        if sessionLog { LogStore.shared.startSessionLog(program: program) }
        // Round 677 (the owner's decision, agent S1 S1-S2): a library start measures the game's folder too (a Dock start does it again in
        // ContentView.startDock, with its install folder). exe-window-auto = 0 in the game's file or bcd.cfg turns it off; an exe-window-mb
        // of the game's own wins in allocatePool. Only a folder under C:\ that is not C:\ itself.
        StikJITHelper.exeWindowAutoMB = 0
        if StikJITHelper.exeWindowAutoWanted(profile.get("exe-window-auto") ?? MadeiraConfig.get("exe-window-auto")),
           let exe = LibraryEntry.hostURL(ofWindowsPath: entry.launchWindowsPath) {
            let folder = exe.deletingLastPathComponent()
            if folder.standardizedFileURL.path != LibraryEntry.hostDrive.standardizedFileURL.path,
               let need = StikJITHelper.fixedBaseWindowMB(in: folder) {
                StikJITHelper.exeWindowAutoMB = need.mb
                LogStore.shared.log("[exe-window] \(need.exe) is a fixed-base image ending \(need.mb)MB above 0x140000000: "
                    + "the executable window is widened to \(need.mb)MB for this session (exe-window-auto; exe-window-mb in the game's file overrides)")
            }
        }
        // Round 677 (the owner's decision, agent S1 S1-S8): Wine's wintypes.dll for a program that imports the WinRT buffer API set.
        BCDWinTypesAuto.prepare(folder: LibraryEntry.hostURL(ofWindowsPath: entry.launchWindowsPath)?.deletingLastPathComponent(),
                                gameValue: profile.get("env.MADEIRA_WINTYPES"))
    }

    /// The size that MetalFX 1.5x brings back to this screen's shape at 720
    /// lines ("WxH"), as madeira-bcd's home screen offers it (fill-mfx15).
    static var metalFX15Resolution: String {
        let s = LibraryPrefs.screenPixels("fill-mfx15")
        return "\(s.0)x\(s.1)"
    }
}

/// Adds every game madeira-bcd's home screen finds in drive_c (its main exe)
/// to upstream's library, once.
enum BCDLibraryImport {
    static func run(done: @escaping (Int) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let games = LibraryGame.group(GameLibrary.scan())
            var found: [LibraryEntry] = []
            for game in games {
                let exe = game.primary
                guard var entry = try? LibraryModel.inspect(exe.url) else { continue }
                entry.title = game.title
                found.append(entry)
            }
            DispatchQueue.main.async {
                let model = LibraryModel.shared
                let known = Set(model.entries.map { $0.relativePath.lowercased() })
                var added = 0
                for entry in found where !known.contains(entry.relativePath.lowercased()) {
                    model.save(entry)
                    added += 1
                }
                LogStore.shared.log("[bcd] library import: \(added) added, \(found.count - added) already there")
                done(added)
            }
        }
    }
}

/// BCD (2026-11-07): a game whose Windows path changes keeps what BCD stores under that path. A Move between this device and an
/// external drive (GameMover) and the earlier External SSD folder becoming a drive (LibraryModel.rebaseExternal) change a game's
/// path; without this the game's own config file, Auto-tune's state and switch, the recommended-settings mark (without which the
/// next Play counts as the first and puts the recommended FPS, resolution and display back), the LibraryPrefs switches, the gyro
/// choices, the save-backup record and the graphics tip's mark would stay under the old path. Each moves only where the new path
/// holds nothing yet; nothing is overwritten. The literal UserDefaults keys are their owners' (tests/host/check-external-storage.py
/// checks that they still are).
enum BCDGameRekey {
    static func carry(from old: String, to new: String) {
        guard old.lowercased() != new.lowercased() else { return }
        let fm = FileManager.default
        // The game's own config file and Auto-tune's state file, named from the path (GameProfiles.swift, AutoTune.swift).
        for (from, to) in [(GameProfile(windowsPath: old).url, GameProfile(windowsPath: new).url),
                           (AutoTune.stateURL(old), AutoTune.stateURL(new))] {
            guard let from, let to, fm.fileExists(atPath: from.path), !fm.fileExists(atPath: to.path) else { continue }
            try? fm.moveItem(at: from, to: to)
        }
        // Dictionaries in UserDefaults: lower-case keys, then exact ones.
        for key in [AutoTune.gamesKey, "madeira.recommended.applied", "bcd.saves.lastSession"] {
            moveEntry(key, from: old.lowercased(), to: new.lowercased())
        }
        for key in ["madeira.library.inDesktop", "madeira.library.avx", "madeira.library.wineVCRT", "madeira.library.nvidia",
                    "madeira.library.safeSync", "madeira.library.screen", "bcd.gyro"] {
            moveEntry(key, from: old, to: new)
        }
        // The low-graphics tip's list of games it was shown for (GameRecommendationsApply.swift).
        let tipKey = "bcd.lowGraphicsTip.shown"
        if var shown = UserDefaults.standard.stringArray(forKey: tipKey), let at = shown.firstIndex(of: old.lowercased()) {
            shown[at] = new.lowercased()
            UserDefaults.standard.set(shown, forKey: tipKey)
        }
        // The save-backup record (BCDSaveBackups/GameSaveWork.swift).
        let record = GameSaveStore.record(old.lowercased())
        if record != GameSaveRecord(), GameSaveStore.record(new.lowercased()) == GameSaveRecord() {
            GameSaveStore.update(new.lowercased()) { $0 = record }
            GameSaveStore.update(old.lowercased()) { $0 = GameSaveRecord() }
        }
        LogStore.shared.log("[external] a game's settings followed it to its new place")
    }

    private static func moveEntry(_ key: String, from old: String, to new: String) {
        let defaults = UserDefaults.standard
        guard var all = defaults.dictionary(forKey: key), let value = all[old], all[new] == nil else { return }
        all[new] = value
        all[old] = nil
        defaults.set(all, forKey: key)
    }
}

/// Round 677 (the owner, 2026-11-19, from agent S1's S1-S8): a 64-bit program that statically imports Windows' WinRT buffer API set
/// (api-ms-win-core-winrt-robuffer-l1-1-0.dll, which Wine maps to wintypes.dll) or wintypes.dll itself cannot start without
/// env.MADEIRA_WINTYPES = 1: Unity IL2CPP's GameAssembly.dll does it, and every archive log with the "import_dll ... robuffer ...
/// not found" line had no frame (36 of 36; Car For Sale Simulator, Tears of Metal, Turbo Dismount 2, The Long Dark, CatMailCo ...).
/// Before a start, the program folder's own 64-bit DLLs and EXEs (top level, at most 64 files, headers and the import table only)
/// are read; when one imports either name and the game's file sets nothing, the key is on for this session (the session's end
/// unsets it, WineProcessBridge.m). env.MADEIRA_WINTYPES = 0 in the game's file wins (its lines are applied after this).
enum BCDWinTypesAuto {
    /// The key this code turned on for the last start, so the next start never inherits it.
    nonisolated(unsafe) static var setByUs = false

    /// The first program file in `folder` (top level) that imports the WinRT buffer API set or wintypes.dll, and the name it imports.
    static func importer(in folder: URL) -> (file: String, name: String)? {
        guard let items = try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil,
                                                                         options: [.skipsHiddenFiles]) else { return nil }
        let programs = items.filter { ["dll", "exe"].contains($0.pathExtension.lowercased()) }
            .sorted { a, b in   // GameAssembly.dll first: the file that has the import in every case seen
                let ga = a.lastPathComponent.lowercased() == "gameassembly.dll", gb = b.lastPathComponent.lowercased() == "gameassembly.dll"
                return ga != gb ? ga : a.lastPathComponent < b.lastPathComponent
            }
        for url in programs.prefix(64) {
            if let name = imports(of: url).first(where: { wanted($0) }) { return (url.lastPathComponent, name) }
        }
        return nil
    }

    static func wanted(_ name: String) -> Bool {
        let n = name.lowercased()
        return n.hasPrefix("api-ms-win-core-winrt-robuffer-") || n == "wintypes.dll"
    }

    /// The DLL names a 64-bit (PE32+) program imports statically; [] for anything else or anything unreadable.
    static func imports(of url: URL) -> [String] {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return [] }
        defer { try? fh.close() }
        func read(_ offset: UInt64, _ count: Int) -> [UInt8] {
            guard (try? fh.seek(toOffset: offset)) != nil, let d = try? fh.read(upToCount: count) else { return [] }
            return [UInt8](d)
        }
        let h = read(0, 4096)
        func u16(_ b: [UInt8], _ o: Int) -> Int { o >= 0 && o + 2 <= b.count ? Int(b[o]) | Int(b[o + 1]) << 8 : 0 }
        func u32(_ b: [UInt8], _ o: Int) -> Int { u16(b, o) | u16(b, o + 2) << 16 }
        guard h.count >= 0x40, h[0] == 0x4d, h[1] == 0x5a else { return [] }
        let pe = u32(h, 0x3c)
        guard pe > 0, pe + 24 + 128 <= h.count, u32(h, pe) == 0x4550 else { return [] }
        let sections = u16(h, pe + 6), optSize = u16(h, pe + 20), opt = pe + 24
        guard u16(h, opt) == 0x20b, sections > 0, sections <= 96 else { return [] }      // PE32+ only: the 64-bit set lacks wintypes.dll
        let importRVA = u32(h, opt + 112 + 8), importSize = u32(h, opt + 112 + 12)
        guard importRVA > 0, importSize > 0 else { return [] }
        var table: [(va: Int, size: Int, raw: Int, rawSize: Int)] = []
        for i in 0..<sections {
            let s = opt + optSize + i * 40
            guard s + 40 <= h.count else { break }
            table.append((u32(h, s + 12), max(u32(h, s + 8), u32(h, s + 16)), u32(h, s + 20), u32(h, s + 16)))
        }
        func offset(_ rva: Int) -> UInt64? {
            for t in table where rva >= t.va && rva < t.va + t.size && rva - t.va < t.rawSize { return UInt64(t.raw + rva - t.va) }
            return nil
        }
        guard let start = offset(importRVA) else { return [] }
        let descriptors = read(start, min(importSize, 20 * 512))
        var names: [String] = []
        var i = 0
        while i + 20 <= descriptors.count {
            let nameRVA = u32(descriptors, i + 12)
            if nameRVA == 0 && u32(descriptors, i) == 0 && u32(descriptors, i + 16) == 0 { break }   // the zero descriptor ends the table
            if let at = offset(nameRVA) {
                let raw = read(at, 96)
                if let end = raw.firstIndex(of: 0), let s = String(bytes: raw[..<end], encoding: .ascii) { names.append(s) }
            }
            i += 20
        }
        return names
    }

    /// Before a start: undo our own earlier key, then turn it on for this session if a program in `folder` needs it.
    static func prepare(folder: URL?, gameValue: String?) {
        if setByUs { unsetenv("MADEIRA_WINTYPES"); setByUs = false }
        guard let folder, gameValue == nil, getenv("MADEIRA_WINTYPES") == nil, let hit = importer(in: folder) else { return }
        setenv("MADEIRA_WINTYPES", "1", 0)
        setByUs = true
        LogStore.shared.log("[wintypes-auto] \(hit.file) imports \(hit.name): Wine's wintypes.dll for this session "
            + "(env.MADEIRA_WINTYPES = 0 in the game's file turns this off)")
    }
}
