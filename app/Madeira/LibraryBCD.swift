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
