// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The launch readiness check (round 604, D6): before a player presses Play, the game page can say "this game wants X, which this device does
// not have yet", in one line, with where to get it. A game that stops at start with c0000135 ("a DLL was not found") says nothing that a
// player understands; the log of 1,426 sessions shows the usual few: MFC 2015-2022 (mfc140u.dll, mfc140.dll: five games), PhysX
// (physxloader.dll), OpenAL (openal32.dll), and a .NET program with no Wine Mono. The check reads what the game's own program files import
// (BCDGraphicsScan.inspect: the import tables, a few kilobytes of each) and compares it with what this device has. It changes nothing and
// blocks nothing: it is a note on the page. Plain values: tests/host/check-bcd-readiness.py compiles this file alone with BCDGraphicsScan.swift.

/// What one program file of the game imports.
struct BCDReadinessModule: Equatable {
    var file: String
    var is64: Bool
    var imports: Set<String>
}

/// What this device has for a game to find, as the live side reads it.
struct BCDReadinessProvided: Equatable {
    /// Microsoft's Visual C++ 2015-2022 x64 copy (Settings › Visual C++, or bundled): it carries mfc140.dll and mfc140u.dll.
    var vcRuntimeX64 = false
    /// Round 625 (the owner's item 26): Microsoft's 32-bit MFC (Settings › Visual C++ › MFC, 32-bit): mfc140.dll, mfc140u.dll,
    /// mfcm140.dll and mfcm140u.dll, which a 32-bit game gets with env.MADEIRA_MFC32 = 1.
    var vcRuntimeX86 = false
    /// Wine Mono (Settings › .NET Framework).
    var wineMono = false
    /// The Start menu's BCD Runtimes installers that have run (their markers in C:\ProgramData\BCD\Runtimes).
    var openAL = false
    var physX = false
    var xna = false
    var directX2010 = false
    /// Round 624 (agent N S12/S13): the install has the increased memory limit and extended VA (EntitlementStatus); true by default, so a caller
    /// that does not know adds no finding.
    var increasedMemory = true
    var extendedVA = true
    /// The device's task address map in GB (jit_task_map_range: 17, 63, 454, 512 ...); nil when unknown.
    var mapGB: Int? = nil
}

struct BCDReadinessFinding: Equatable, Identifiable {
    var id: String
    /// One sentence for the game's page.
    var text: String
    /// Whether BCD can supply it at all (false: nothing BCD offers fixes this).
    var fixable: Bool
}

enum BCDReadiness {
    /// The most the page shows: the first one or two, most useful first.
    static let maxShown = 2

    /// The 32-bit DLLs Microsoft's DirectX June 2010 redistributable has and BCD's 32-bit farm does not (ajan49's list, round 603).
    static let missing32DirectX: Set<String> = [
        "xapofx1_0.dll", "d3dcsx_42.dll", "d3dcsx_43.dll", "d3dx10.dll",
        "xactengine2_1.dll", "xactengine2_2.dll", "xactengine2_3.dll", "xactengine2_5.dll", "xactengine2_6.dll", "xactengine2_8.dll", "xactengine2_10.dll",
    ]

    /// BCD has no 64-bit XACT at all (a 64-bit program importing any xactengine finds none), and lacks the same few others.
    static func missing64DirectX(_ name: String) -> Bool {
        name.hasPrefix("xactengine") || ["xapofx1_0.dll", "d3dcsx_42.dll", "d3dcsx_43.dll", "d3dx10.dll"].contains(name)
    }

    static func isMFC(_ name: String) -> Bool {
        name.hasPrefix("mfc140") || name.hasPrefix("mfcm140")
    }

    /// Round 625: what Microsoft's 32-bit package gives (VCRuntimePackage.x86).
    static let mfc32Names: Set<String> = ["mfc140.dll", "mfc140u.dll", "mfcm140.dll", "mfcm140u.dll"]

    /// The findings for a game: `modules` are the program files that were read, `files` every file name (lowercased) found in the game's
    /// folder tree (a DLL the game carries itself is found next to it, so it is not missing). `complete` is false when the scan stopped at
    /// its limit: a file the game carries may then simply not have been reached, so nothing is called missing from the game's own copy.
    static func evaluate(modules: [BCDReadinessModule], files: Set<String>, provided: BCDReadinessProvided,
                         complete: Bool = true, mainFile: String? = nil) -> [BCDReadinessFinding] {
        var out: [BCDReadinessFinding] = []
        func add(_ id: String, _ text: String, fixable: Bool = true) {
            if !out.contains(where: { $0.id == id }) { out.append(BCDReadinessFinding(id: id, text: text, fixable: fixable)) }
        }
        for m in modules {
            for name in m.imports.sorted() {
                // madeira-bcd (round 624, agent E1 S1, Aliens: Colonial Marines VMJ4WV): Valve's steam_api is part of the game's own copy, never
                // of Windows or BCD; a program that imports it from a folder tree without it ends with 0xC0000135 before its first window.
                if complete && (name == "steam_api.dll" || name == "steam_api64.dll") && !files.contains(name) {
                    add("steamapi", "\(m.file) needs \(name), which is not in the game's folder. This copy of the game is incomplete: add the "
                        + "missing file (or install the game again) and try again.", fixable: false)
                }
                if isMFC(name) && !files.contains(name) {
                    if m.is64 {
                        // Microsoft's own copy has mfc140.dll and mfc140u.dll; the other MFC files (debug, language, mfcm) it does not.
                        if !provided.vcRuntimeX64 || !["mfc140.dll", "mfc140u.dll"].contains(name) {
                            add("mfc64", provided.vcRuntimeX64
                                ? "\(m.file) wants \(name), which Microsoft's Visual C++ runtime does not have. BCD cannot supply it."
                                : "\(m.file) wants \(name) (Microsoft's MFC). Download the Visual C++ runtime in Settings first.",
                                fixable: !provided.vcRuntimeX64)
                        }
                    } else {
                        // Round 625 (the owner's item 26): the 32-bit package's four MFC DLLs, downloaded in Settings; the game takes them
                        // with env.MADEIRA_MFC32 = 1 (auto-tune adds it after the start that fails for want of them).
                        let carried = mfc32Names.contains(name)
                        if !provided.vcRuntimeX86 || !carried {
                            add("mfc32", carried
                                ? "\(m.file) is a 32-bit program that wants \(name) (Microsoft's MFC). Download the 32-bit MFC in Settings › Visual C++ first."
                                : "\(m.file) is a 32-bit program that wants \(name), which Microsoft's 32-bit MFC does not have. BCD cannot supply it.",
                                fixable: carried)
                        }
                    }
                }
                switch name {
                case "mscoree.dll" where !provided.wineMono:
                    add("mono", "\(m.file) is a .NET program. Download Wine Mono in Settings › .NET Framework first.")
                case "openal32.dll" where !provided.openAL && !files.contains(name):
                    add("openal", "\(m.file) wants OpenAL. Install it from the Start menu: BCD Runtimes › OpenAL.")
                case "physxloader.dll" where !provided.physX && !files.contains(name):
                    add("physx", "\(m.file) wants NVIDIA PhysX. Install it from the Start menu: BCD Runtimes › PhysX (legacy).")
                default:
                    break
                }
                if !provided.directX2010 && !files.contains(name) && (m.is64 ? missing64DirectX(name) : missing32DirectX.contains(name)) {
                    add("dx2010", "\(m.file) wants \(name), an older DirectX file. Install it from the Start menu: BCD Runtimes › DirectX June 2010.")
                }
            }
        }
        // Round 624 (agent N S12/S12b): Unity asks for several 512 MB blocks of address space when it starts; an install without the limit has too
        // little of it (28 of 28 such logs failed on it, 0 of 202 with the limit), and so does a small map. Nothing is blocked: a note.
        if files.contains("unityplayer.dll") {
            if !provided.increasedMemory {
                add("unity-no-rights", "This is a Unity game. Unity asks for several 512 MB blocks of address space when it starts, and an install without the "
                    + "increased memory limit has too little of it, so most Unity games stop at once here. Add the limit with GetMoreRam, or sign BCD "
                    + "with a paid developer account.", fixable: false)
            } else if let gb = provided.mapGB, gb < 40 {
                add("unity-small-map", "This is a Unity game, and this device gives BCD only \(gb) GB of address space, even with the memory limit, "
                    + "so most Unity games cannot start on it.", fixable: false)
            }
        }
        // Round 624 (agent N S13): a 32-bit MAIN program needs a 4 GB block of address space of its own; an install without the limit and without
        // extended VA has none (57 of 57 such starts were refused, "[wow-window] refused"). Only the launched program: a 64-bit game with 32-bit
        // helpers is never flagged.
        if !provided.increasedMemory && !provided.extendedVA, let main = mainFile, let m = modules.first(where: { $0.file.lowercased() == main }), !m.is64 {
            add("wow-window", "\(m.file) is a 32-bit game, and 32-bit games need a 4 GB block of address space that this install does not have. "
                + "Add the increased memory limit with GetMoreRam, or sign BCD with a paid developer account.", fixable: false)
        }
        // XNA is a set of .NET assemblies a game carries a reference to: the game's folder names them.
        if !provided.xna && files.contains("microsoft.xna.framework.dll") && !files.contains("xnafx40_redist.msi") {
            add("xna", "This game uses Microsoft XNA. Install it from the Start menu: BCD Runtimes › XNA Framework 4.0.")
        }
        // Blocking findings (nothing BCD offers fixes them) come last: the fixable ones tell the player what to do.
        return out.filter(\.fixable) + out.filter { !$0.fixable }
    }

    /// The note for the page: nil when nothing is missing.
    static func note(_ findings: [BCDReadinessFinding]) -> String? {
        guard !findings.isEmpty else { return nil }
        let shown = findings.prefix(maxShown).map(\.text)
        let more = findings.count - shown.count
        return shown.joined(separator: " ") + (more > 0 ? " (\(more) more in the log.)" : "")
    }

    /// The line the log gets when the check ran.
    static func logLine(game: String, modules: Int, findings: [BCDReadinessFinding]) -> String {
        "[readiness] \(game): \(modules) program file\(modules == 1 ? "" : "s") read; "
            + (findings.isEmpty ? "nothing missing" : findings.map { $0.id + (($0.fixable) ? "" : "(no fix)") }.joined(separator: ", "))
    }

    /// A program file name for the check's reading of a folder: .exe and .dll only.
    static func isProgram(_ name: String) -> Bool {
        let ext = (name as NSString).pathExtension.lowercased()
        return ext == "exe" || ext == "dll"
    }

    /// Reads a game folder: every program file's imports (bounded) and every file name. Never follows a symbolic link, never writes, skips the
    /// redistributable folders. `limits` keeps a start from waiting: the page asks on a background queue and shows nothing until it is done.
    static func scan(_ root: URL, maxFiles: Int = 4000, maxModules: Int = 400, seconds: Double = 3.0,
                     now: () -> Date = Date.init) -> (modules: [BCDReadinessModule], files: Set<String>, complete: Bool) {
        let started = now()
        var modules: [BCDReadinessModule] = []
        var files = Set<String>()
        var seen = 0
        let keys: [URLResourceKey] = [.isRegularFileKey, .isSymbolicLinkKey, .isDirectoryKey]
        guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: keys, options: [], errorHandler: { _, _ in true }) else {
            return ([], [], false)
        }
        while let item = walker.nextObject() as? URL {
            seen += 1
            if seen > maxFiles || (seen % 128 == 0 && now().timeIntervalSince(started) > seconds) { return (modules, files, false) }
            guard let values = try? item.resourceValues(forKeys: Set(keys)) else { continue }
            if values.isSymbolicLink == true { walker.skipDescendants(); continue }
            let name = item.lastPathComponent.lowercased()
            if values.isDirectory == true {
                if BCDGraphicsScan.skippedFolders.contains(name) { walker.skipDescendants() }
                continue
            }
            guard values.isRegularFile == true else { continue }
            files.insert(name)
            if isProgram(name), modules.count < maxModules, let info = BCDGraphicsScan.inspect(item) {
                modules.append(BCDReadinessModule(file: item.lastPathComponent, is64: info.is64, imports: info.imports))
            }
        }
        return (modules, files, modules.count < maxModules)
    }
}
