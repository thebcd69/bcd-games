// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (commit adec653)
// under GPL-3.0-or-later and modified for BCD in October and November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// "Look like this GPU" (round 603): the graphics card a game is told it has. BCD reports Apple's GPU
// (vendor 106B) to every game, or an NVIDIA GeForce RTX 3060 when the game's "Show the game an NVIDIA
// card" switch is on. A game that checks the card against a table of makers, or only runs on a card
// it knows, can be shown another one here: one choice per game (the game's line `gpu-card`), the
// same name, maker and model in every place a game can ask:
//   - DXGI (Direct3D 10, 11 and 12 take their adapter from it): DXMT's dxgi.customVendorId,
//     dxgi.customDeviceId and dxgi.customDeviceDesc;
//   - Direct3D 9: d3d9.customVendorId, d3d9.customDeviceId and d3d9.customDeviceDesc;
//   - Windows itself (EnumDisplayDevices, SetupAPI's PCI entry, the display driver key, D3DKMT):
//     win32u reads MADEIRA_GPU_VENDOR, MADEIRA_GPU_DEVICE and MADEIRA_GPU_NAME
//     (build/win32u-unix/sysparams_ios.c), which BCDGPUCardsLive.swift exports at the launch.
// Nothing changes without a choice. Video memory is the game's `vram-mb` line, one number for DXGI
// (whole), Direct3D 9 (half, as before) and Windows' registry; it is not part of a card, because a
// bigger number than the phone can hold ends with iOS closing the game.
//
// Plain values and rules, no engine type: the table below must match the one in sysparams_ios.c
// (tests/host/check-gpu-cards.py compares them).

struct BCDGPUCard: Identifiable, Equatable {
    var id: String
    /// What the picker shows.
    var title: String
    var vendor: UInt16
    var device: UInt16
    /// The adapter name a game reads (DXGI's Description, Direct3D 9's, Windows' DeviceString and driver description).
    var name: String
    /// Direct3D 9's driver file name (d3d9.customDriver, build 623), nil = DXMT's own. Fallout 3 and New Vegas pick their
    /// render path from the description and the driver together.
    var driver: String? = nil

    var vendorHex: String { String(format: "%04x", vendor) }
    var deviceHex: String { String(format: "%04x", device) }
    var isNVIDIA: Bool { vendor == BCDGPUCards.nvidia }
    /// The card BCD reports when no choice is made: nothing is overridden for it.
    var isApple: Bool { vendor == BCDGPUCards.apple }
}

enum BCDGPUCards {
    /// The game's line.
    static let key = "gpu-card"
    /// The game's line for video memory (read by winemetal, DXMT's DXGI and win32u; Settings › Memory sets it for every game).
    static let memoryKey = "vram-mb"

    static let apple: UInt16 = 0x106B, nvidia: UInt16 = 0x10DE, amd: UInt16 = 0x1002, intel: UInt16 = 0x8086

    /// DXMT reads DXMT_CONFIG into a buffer of this many characters and drops all of it from a longer value.
    static let dxmtConfigLimit = 259

    static let cards: [BCDGPUCard] = [
        // The identity BCD already used for "Show the game an NVIDIA card" (10DE:2544).
        .init(id: "nvidia", title: "NVIDIA GeForce RTX 3060", vendor: nvidia, device: 0x2544, name: "NVIDIA GeForce RTX 3060"),
        // Build 632 (upstream Madeira #528): for games that only know cards up to 2016-2018 (a GeForce GTX 1060 6GB, 10DE:1C03).
        .init(id: "nvidia-gtx1060", title: "NVIDIA GeForce GTX 1060 6GB", vendor: nvidia, device: 0x1C03, name: "NVIDIA GeForce GTX 1060 6GB"),
        .init(id: "amd", title: "AMD Radeon RX 6700 XT", vendor: amd, device: 0x73DF, name: "AMD Radeon RX 6700 XT"),
        .init(id: "amd-rx580", title: "AMD Radeon RX 580", vendor: amd, device: 0x67DF, name: "AMD Radeon RX 580 Series"),
        .init(id: "intel", title: "Intel UHD Graphics 630", vendor: intel, device: 0x3E92, name: "Intel(R) UHD Graphics 630"),
        .init(id: "intel-arc", title: "Intel Arc A770", vendor: intel, device: 0x56A0, name: "Intel(R) Arc(TM) A770 Graphics"),
        // Build 623 (upstream Madeira adec653 + willfaust/dxmt f25e5568d): an older NVIDIA card with NVIDIA's
        // driver file, as the d3d9.dll wrapper Fallout: New Vegas players install reports. With DXMT's own name and driver New
        // Vegas takes its basic render path and crashes loading a save at 00B57AA9 (BCD's fallout-new-vegas notes: four logs,
        // the same address); with this identity it loads (upstream, iPhone, 2026-10-26).
        .init(id: "nvidia-7900gs", title: "NVIDIA GeForce 7900 GS (older Direct3D 9 games)", vendor: nvidia, device: 0x0292, name: "NVIDIA GeForce 7900 GS", driver: "nv4_disp.dll"),
        // The phone's own GPU under its real name (the Metal device's, "Apple A17 Pro GPU"), chosen on purpose: it names it the same in
        // every place a game can ask (the default says "BCD Display" in Windows' registry), and turns the older NVIDIA switch off for this
        // game. DXGI's and Direct3D 9's own default is already the Metal device's name, so no DXMT option is needed.
        .init(id: "apple", title: "Apple GPU (this phone's own)", vendor: apple, device: 0x0001, name: "Apple GPU"),
    ]

    static func card(_ id: String) -> BCDGPUCard? { cards.first { $0.id == id } }

    /// The picker: "" (no choice) and the cards, as (value, label).
    static var choices: [(String, String)] { [("", "As BCD does by default")] + cards.map { ($0.id, $0.title) } }

    /// The ids of the registry adapters win32u may have left in a saved prefix (every card's pair, once each):
    /// the ones a launch does not report are removed (sysparams_ios.c, ios_forget_virtual_gpu).
    static var knownPairs: [(vendor: UInt16, device: UInt16)] {
        var seen = Set<UInt32>(), out: [(UInt16, UInt16)] = []
        for card in cards where seen.insert(UInt32(card.vendor) << 16 | UInt32(card.device)).inserted { out.append((card.vendor, card.device)) }
        return out
    }

    // MARK: DXMT's options

    /// The DXMT options for a card (DXGI and Direct3D 9), ids first, then names. The Apple card
    /// overrides nothing: BCD's own values are the defaults.
    static func dxmtOptions(_ card: BCDGPUCard, names: Bool = true) -> [String] {
        guard !card.isApple else { return [] }
        var out: [String] = []
        for api in ["dxgi", "d3d9"] {
            out.append("\(api).customVendorId=\(card.vendorHex)")
            out.append("\(api).customDeviceId=\(card.deviceHex)")
        }
        // The driver file counts like the ids (a game reads it with them), so it stays when the names have to go.
        if let driver = card.driver { out.append("d3d9.customDriver=\(driver)") }
        if names {
            // A name has spaces: DXMT's parser keeps what is inside double quotes whole.
            for api in ["dxgi", "d3d9"] { out.append("\(api).customDeviceDesc=\"\(card.name)\"") }
        }
        return out
    }

    /// An option that names an adapter: a card replaces every one of them, the older NVIDIA switch's
    /// dxgi.customDeviceId=2544 and a game's own lines included (the choice is the player's, per game).
    static func isIdentityOption(_ option: String) -> Bool {
        let key = option.split(separator: "=", maxSplits: 1).first.map { $0.trimmingCharacters(in: .whitespaces) } ?? option
        return key.hasPrefix("dxgi.custom") || key.hasPrefix("d3d9.custom")
    }

    /// The launch's DXMT options with a card's: the adapter options already there are replaced; when the
    /// whole would pass DXMT's limit, the names go first (the ids are what a game's table reads) and
    /// `namesDropped` says so.
    static func merged(_ options: [String], with card: BCDGPUCard) -> (options: [String], replaced: Int, namesDropped: Bool) {
        let kept = options.filter { !isIdentityOption($0) }
        let replaced = options.count - kept.count
        var out = kept + dxmtOptions(card)
        var dropped = false
        if out.joined(separator: ";").utf16.count > dxmtConfigLimit {
            out = kept + dxmtOptions(card, names: false)
            dropped = !card.isApple
        }
        return (out, replaced, dropped)
    }

    // MARK: A game's own record of the card

    /// Games that keep the card's name in their prefs (sD3DDevice) and open their launcher when the card they see differs:
    /// a second program, which ends the session (upstream Madeira adec653). App ID -> the file under Documents/My Games.
    static let prefsFiles: [Int: String] = [
        22380: "FalloutNV/FalloutPrefs.ini",                                    // Fallout: New Vegas
        22300: "Fallout3/FalloutPrefs.ini", 22370: "Fallout3/FalloutPrefs.ini", // Fallout 3, Game of the Year
    ]
    /// The same files by the program a library entry starts, for a copy that is not a Steam entry (build 623: the
    /// auto-tune rule fallout-nv-older-nvidia-card gives the card to any copy of New Vegas).
    static let prefsFilesByProgram: [String: String] = [
        "falloutnv.exe": "FalloutNV/FalloutPrefs.ini", "falloutnvlauncher.exe": "FalloutNV/FalloutPrefs.ini",
        "fallout3.exe": "Fallout3/FalloutPrefs.ini", "falloutlauncher.exe": "Fallout3/FalloutPrefs.ini",
    ]

    /// The prefs file of the game a launch starts: by its Steam App ID, else by its program's file name.
    static func prefsFile(appID: Int?, program: String?) -> String? {
        if let appID, let file = prefsFiles[appID] { return file }
        guard let name = program?.split(whereSeparator: { $0 == "/" || $0 == "\\" }).last?.lowercased() else { return nil }
        return prefsFilesByProgram[name]
    }

    /// `text` with sD3DDevice naming the card the game will see, or nil when nothing changes. With a card chosen
    /// (`cardName`) its name goes in; with none, the phone's own name (`metalName`, what DXMT reports then) goes back only
    /// over a name one of BCD's cards wrote, never over a name the game or the player chose.
    static func prefsText(_ text: String, cardName: String?, metalName: String?) -> String? {
        var lines = text.components(separatedBy: "\n")
        var changed = false
        let ours = Set(cards.filter { !$0.isApple }.map(\.name))
        for (i, raw) in lines.enumerated() {
            let cr = raw.hasSuffix("\r")
            let line = cr ? String(raw.dropLast()) : raw
            guard line.lowercased().hasPrefix("sd3ddevice=") else { continue }
            let key = line.prefix { $0 != "=" }
            let current = line.dropFirst(key.count + 1).trimmingCharacters(in: CharacterSet(charactersIn: "\" \t"))
            let wanted: String?
            if let cardName { wanted = current == cardName ? nil : cardName }
            else { wanted = ours.contains(current) ? metalName : nil }
            guard let wanted, !wanted.isEmpty, wanted != current else { continue }
            lines[i] = "\(key)=\"\(wanted)\"" + (cr ? "\r" : "")
            changed = true
        }
        return changed ? lines.joined(separator: "\n") : nil
    }

    // MARK: Windows' side

    /// What the launch exports for a card. `set` are exported, `unset` removed. NVAPI and DXMT's NVIDIA
    /// switches follow the card's maker: an NVIDIA card turns them on, any other turns them off, which is
    /// how a card overrides the older "Show the game an NVIDIA card" switch. `nativeName` is the Metal
    /// device's name, the Apple card's: Windows says it too (without it, the Apple card leaves Windows'
    /// own default, "BCD Display").
    static func environment(_ card: BCDGPUCard, nativeName: String? = nil) -> (set: [String: String], unset: [String]) {
        let nvidiaSwitches = ["DXMT_ENABLE_NVEXT", "DXMT_WSI_MONITOR_IDENTITY", "DXMT_WSI_MODE_TABLE"]
        let identity = ["MADEIRA_GPU_VENDOR", "MADEIRA_GPU_DEVICE", "MADEIRA_GPU_NAME"]
        var set: [String: String] = [:]
        var unset: [String] = []
        if card.isNVIDIA { for name in nvidiaSwitches { set[name] = "1" } } else { unset += nvidiaSwitches }
        if card.isApple {
            if let nativeName, !nativeName.isEmpty {
                set["MADEIRA_GPU_VENDOR"] = card.vendorHex
                set["MADEIRA_GPU_DEVICE"] = card.deviceHex
                set["MADEIRA_GPU_NAME"] = nativeName
            } else {
                unset += identity
            }
        } else {
            set["MADEIRA_GPU_VENDOR"] = card.vendorHex
            set["MADEIRA_GPU_DEVICE"] = card.deviceHex
            set["MADEIRA_GPU_NAME"] = card.name
        }
        return (set, unset)
    }

    // MARK: Video memory

    /// The per-game video memory choices (the same sizes Settings › Memory offers, 0 = automatic) as (value, label),
    /// worded as Settings words them.
    static func memoryChoices(_ megabytes: [Int]) -> [(String, String)] {
        megabytes.map { mb in
            mb <= 0 ? ("", "Automatic") : (String(mb), String(format: "%g GB", Double(mb) / 1024))
        }
    }
}
