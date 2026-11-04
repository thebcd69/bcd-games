// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Game setup (round 604), the tables of the page's choices: which DLLs a game's own copies may be told apart from Wine's,
// the named FEX presets, and the Wine desktop's text size and colours. Foundation only, no engine type (the preview app
// builds it; tests/host/check-game-setup.py runs the rules). The words the page shows are BCDGameSetupPage.swift's.

// MARK: - DLL components

/// A set of DLLs that games ship next to their program.
struct BCDDllComponent: Equatable, Identifiable {
    var id: String
    var title: String
    var note: String
    /// File names, lower case.
    var names: [String]
}

enum BCDDllComponents {
    static let shadowKey = "env.MADEIRA_APP_DLL_SHADOW"
    static let overridesKey = "env.WINEDLLOVERRIDES"

    private static func numbered(_ prefix: String, _ range: ClosedRange<Int>) -> [String] { range.map { "\(prefix)\($0).dll" } }

    static let all: [BCDDllComponent] = [
        BCDDllComponent(id: "d3dx", title: "DirectX helper libraries",
                        note: "D3DX and the shader compiler (d3dx9_NN, d3dx10_NN, d3dx11_NN, d3dcompiler_NN), which many games ship because DirectX needs them installed.",
                        names: numbered("d3dx9_", 24...43) + ["d3dx10.dll"] + numbered("d3dx10_", 33...43) + ["d3dx11_42.dll", "d3dx11_43.dll"]
                            + numbered("d3dcompiler_", 33...47)),
        BCDDllComponent(id: "xaudio", title: "DirectX audio libraries",
                        note: "XAudio2, X3DAudio, XAPOFX and the XACT engines.",
                        names: numbered("xaudio2_", 0...9) + numbered("x3daudio1_", 0...7) + numbered("xapofx1_", 1...5)
                            + numbered("xactengine2_", 0...10) + numbered("xactengine3_", 0...7)),
        BCDDllComponent(id: "xinput", title: "XInput",
                        note: "The controller library (xinput1_1 to xinput1_4, xinput9_1_0).",
                        names: ["xinput1_1.dll", "xinput1_2.dll", "xinput1_3.dll", "xinput1_4.dll", "xinput9_1_0.dll"]),
        BCDDllComponent(id: "dinput", title: "DirectInput", note: "dinput.dll and dinput8.dll, also what input wrappers pose as.",
                        names: ["dinput.dll", "dinput8.dll"]),
        BCDDllComponent(id: "dsound", title: "DirectSound", note: "dsound.dll, also what sound wrappers pose as.", names: ["dsound.dll"]),
        BCDDllComponent(id: "vcrt", title: "Visual C++ runtime",
                        note: "The runtime libraries a program ships beside itself (msvcp140, vcruntime140, concrt140, msvcr120 and the older ones).",
                        names: ["msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll",
                                "msvcp120.dll", "msvcr120.dll", "msvcp110.dll", "msvcr110.dll", "msvcp100.dll", "msvcr100.dll"]),
        BCDDllComponent(id: "graphics", title: "DirectX wrappers",
                        note: "d3d8, d3d9, d3d10, d3d11, dxgi and ddraw copies that shim or replace DirectX (ReShade, dgVoodoo, d3d8to9 and the like).",
                        names: ["d3d8.dll", "d3d9.dll", "d3d10.dll", "d3d10_1.dll", "d3d10core.dll", "d3d11.dll", "dxgi.dll", "ddraw.dll"]),
    ]

    /// The names of a ';', ',' or blank separated list, lower case, each once, in order.
    static func names(_ list: String?) -> [String] {
        var seen = Set<String>(), out: [String] = []
        for part in (list ?? "").split(whereSeparator: { ";, ".contains($0) }) {
            let name = part.lowercased()
            if seen.insert(name).inserted { out.append(name) }
        }
        return out
    }

    static func joined(_ names: [String]) -> String { names.joined(separator: ";") }

    /// A DLL name the lines accept: letters, digits, underscore, dot and minus, ending in .dll.
    static func isDllName(_ name: String) -> Bool {
        guard name.lowercased().hasSuffix(".dll"), name.utf8.count <= 64 else { return false }
        return name.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || "_.-".contains($0)) }
    }

    /// Whether the component is on Wine's side in the shadow list: any of its names is listed.
    static func isWines(_ component: BCDDllComponent, shadow: [String]) -> Bool {
        let set = Set(shadow)
        return component.names.contains { set.contains($0) }
    }

    /// The shadow list with the component taken to Wine's own (`wine` true: the names `shipped` in the game's folder are added)
    /// or given back to the game (all of the component's names leave the list). Names of other components and the player's
    /// own stay where they are.
    static func shadow(_ component: BCDDllComponent, wine: Bool, shipped: [String], current: [String]) -> [String] {
        let mine = Set(component.names)
        var list = current.filter { !mine.contains($0) }
        if wine {
            for name in shipped.map({ $0.lowercased() }) where mine.contains(name) && !list.contains(name) { list.append(name) }
        }
        return list
    }

    /// The DLLs the game's own folder holds that belong to some component, by file name (lower case) as the disk spells them.
    static func shipped(files: [String]) -> [String] {
        let known = Set(all.flatMap(\.names))
        var seen = Set<String>(), out: [String] = []
        for file in files where known.contains(file.lowercased()) && seen.insert(file.lowercased()).inserted { out.append(file) }
        return out.sorted { $0.lowercased() < $1.lowercased() }
    }

    // MARK: Switched off

    /// WINEDLLOVERRIDES entries are "name,name=mode" separated by ';'. An entry with no mode switches the DLLs off.
    static func entries(_ overrides: String?) -> [String] {
        (overrides ?? "").split(separator: ";", omittingEmptySubsequences: true).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    /// The DLLs the line switches off.
    static func switchedOff(_ overrides: String?) -> [String] {
        var out: [String] = []
        for entry in entries(overrides) {
            guard let eq = entry.firstIndex(of: "="), entry[entry.index(after: eq)...].trimmingCharacters(in: .whitespaces).isEmpty else { continue }
            out += entry[..<eq].split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased() }.filter { !$0.isEmpty }
        }
        return out
    }

    /// The line with its "switched off" entries replaced by `names` (the others stay as written); "" when nothing is left.
    static func settingOff(_ overrides: String?, to names: [String]) -> String {
        var keep: [String] = []
        for entry in entries(overrides) {
            if let eq = entry.firstIndex(of: "="), entry[entry.index(after: eq)...].trimmingCharacters(in: .whitespaces).isEmpty { continue }
            keep.append(entry)
        }
        var seen = Set<String>(), off: [String] = []
        for name in names where seen.insert(name).inserted { off.append(name) }
        if !off.isEmpty { keep.append(off.joined(separator: ",") + "=") }
        return keep.joined(separator: ";")
    }
}

// MARK: - FEX presets

/// A named set of FEX's memory-ordering and block options, written as explicit lines in the game's own file.
struct BCDFEXPreset: Equatable, Identifiable {
    var id: String
    var title: String
    var note: String
    /// Key -> value for every key in BCDFEXPresets.keys.
    var lines: [String: String]
}

enum BCDFEXPresets {
    static let tso = "env.FEX_TSOENABLED"
    static let vector = "env.FEX_VECTORTSOENABLED"
    static let memcpy = "env.FEX_MEMCPYSETTSOENABLED"
    static let halfBarrier = "env.FEX_HALFBARRIERTSOENABLED"
    static let multiblock = "env.FEX_MULTIBLOCK"
    static let smc = "env.FEX_SMCCHECKS"
    static let keys = [tso, vector, memcpy, halfBarrier, multiblock]

    static let all: [BCDFEXPreset] = [
        BCDFEXPreset(id: "stability", title: "Stability",
                     note: "Memory is ordered as an Intel processor orders it, vector and block-copy accesses included; unaligned accesses are not cut down to half barriers; code is translated in small blocks. The slowest choice, for a game that fails on some runs and not on others. Every 16 bytes a game copies or clears costs a memory barrier here, so a game that moves megabytes a frame can fall to a few frames per second.",
                     lines: [tso: "1", vector: "1", memcpy: "1", halfBarrier: "0", multiblock: "0"]),
        BCDFEXPreset(id: "compatibility", title: "Compatibility",
                     note: "Vector and block-copy accesses are ordered too, with the usual way of translating blocks. For a game that needs the strict order and runs fast enough. Block copies are ordered too, which is slower for a game that copies a lot.",
                     lines: [tso: "1", vector: "1", memcpy: "1", halfBarrier: "1", multiblock: "1"]),
        BCDFEXPreset(id: "performance", title: "Performance",
                     note: "Loads and stores are ordered, but vector and block-copy accesses are not: fewer barriers, a faster processor side. Six of BCD's tested lists switch vector ordering off. A game that breaks only sometimes may need Stability instead.",
                     lines: [tso: "1", vector: "0", memcpy: "0", halfBarrier: "1", multiblock: "1"]),
    ]

    /// "" when the file sets none of the keys (BCD's own choice), a preset's id when the file sets exactly its lines, else "custom".
    static func current(_ values: [String: String]) -> String {
        let set = keys.filter { !(values[$0] ?? "").isEmpty }
        if set.isEmpty { return "" }
        for preset in all where keys.allSatisfy({ (values[$0] ?? "") == preset.lines[$0] }) { return preset.id }
        return "custom"
    }

    /// The edits that make a preset current: key -> value, "" meaning the line goes.
    static func edits(for id: String) -> [String: String] {
        var out: [String: String] = [:]
        let preset = all.first { $0.id == id }
        for key in keys { out[key] = preset?.lines[key] ?? "" }
        return out
    }
}

// MARK: - The Wine desktop

enum BCDWineDesktop {
    static let dpiKey = "desktop.dpi"
    static let themeKey = "desktop.theme"
    static let wallpaperKey = "desktop.wallpaper"

    /// "Text size" is the desktop's DPI (what winecfg's Graphics tab sets): 96 is Windows' normal.
    static let dpiChoices: [(value: String, label: String)] = [
        ("", "Normal (96)"), ("120", "Larger (120)"), ("144", "Large (144)"), ("168", "Larger still (168)"), ("192", "Very large (192)"), ("240", "Huge (240)"),
    ]

    static func isDPI(_ text: String) -> Bool { Int(text).map { (72...480).contains($0) } ?? false }

    /// Dark colours for the classic controls Wine draws without a theme: name -> "R G B". Light is Wine's own set, so
    /// choosing it only removes what dark added.
    static let darkColors: [(name: String, rgb: String)] = [
        ("ActiveBorder", "60 60 60"), ("ActiveTitle", "40 40 52"), ("AppWorkSpace", "24 24 28"), ("Background", "16 16 24"),
        ("ButtonAlternateFace", "58 58 58"), ("ButtonDkShadow", "0 0 0"), ("ButtonFace", "48 48 52"), ("ButtonHilight", "92 92 96"),
        ("ButtonLight", "72 72 76"), ("ButtonShadow", "28 28 30"), ("ButtonText", "232 232 232"), ("GradientActiveTitle", "56 56 80"),
        ("GradientInactiveTitle", "44 44 48"), ("GrayText", "140 140 140"), ("Hilight", "0 96 176"), ("HilightText", "255 255 255"),
        ("HotTrackingColor", "90 170 255"), ("InactiveBorder", "50 50 52"), ("InactiveTitle", "44 44 48"), ("InactiveTitleText", "160 160 160"),
        ("InfoText", "232 232 232"), ("InfoWindow", "52 52 56"), ("Menu", "40 40 44"), ("MenuBar", "40 40 44"), ("MenuHilight", "0 96 176"),
        ("MenuText", "232 232 232"), ("Scrollbar", "64 64 68"), ("TitleText", "255 255 255"), ("Window", "32 32 36"), ("WindowFrame", "90 90 94"),
        ("WindowText", "232 232 232"),
    ]

    /// A wallpaper must be a .bmp Wine's desktop can draw, on drive C:.
    static func isWallpaperPath(_ text: String) -> Bool {
        let t = text.replacingOccurrences(of: "/", with: "\\")
        return t.count > 3 && t.prefix(3).uppercased() == "C:\\" && t.lowercased().hasSuffix(".bmp") && !t.contains("..") && !t.contains("\0")
            && !t.contains("\n") && t.utf8.count < 260
    }
}
