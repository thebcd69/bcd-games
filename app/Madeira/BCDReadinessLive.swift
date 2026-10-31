// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The launch readiness check on the engine (rules: BCDReadinessCore.swift). A game's page asks for its note; the first time (and again when
// the game's folder changed) a utility queue reads the game's program files' import tables, a few kilobytes each, at most three seconds, and
// the page reloads when it is done. What the device has is read each time the note is asked for, so installing Wine Mono or a runtime takes
// the note away at the next look. Nothing is blocked, nothing is written but one log line per scan. Left out of the preview build.

@MainActor final class BCDReadinessLive {
    static let shared = BCDReadinessLive()

    /// Settings › Logs is not the place: the check is a note on a page and costs a read of the game's headers once. Off with this key.
    static let enabledKey = "madeira.bcd.readiness"
    static var enabled: Bool { UserDefaults.standard.object(forKey: enabledKey) == nil || UserDefaults.standard.bool(forKey: enabledKey) }

    private struct Scan {
        var signature: String
        var modules: [BCDReadinessModule]
        var files: Set<String>
        var complete: Bool
    }
    private var scans: [String: Scan] = [:]
    private var running: Set<String> = []

    /// The note for a game's page, or nil (nothing missing, not scanned yet, or the check is off). `changed` runs on the main thread when a
    /// scan that was started by this call has finished.
    func note(for entry: LibraryEntry, changed: @escaping () -> Void) -> String? {
        guard Self.enabled, let root = BCDGraphicsDefaults.folder(of: entry) else { return nil }
        let signature = BCDGraphicsDefaults.folderSignature(root, target: LibraryModel.drive.appendingPathComponent(entry.launchRelativePath))
        if let scan = scans[root.path], scan.signature == signature {
            return BCDReadiness.note(BCDReadiness.evaluate(modules: scan.modules, files: scan.files, provided: Self.provided(), complete: scan.complete,
                                                           mainFile: Self.mainFile(entry)))
        }
        guard !running.contains(root.path) else { return nil }
        running.insert(root.path)
        let title = entry.title, main = Self.mainFile(entry)
        DispatchQueue.global(qos: .utility).async {
            let result = BCDReadiness.scan(root)
            DispatchQueue.main.async { [weak self] in
                MainActor.assumeIsolated {
                    guard let self else { return }
                    self.running.remove(root.path)
                    self.scans[root.path] = Scan(signature: signature, modules: result.modules, files: result.files, complete: result.complete)
                    let findings = BCDReadiness.evaluate(modules: result.modules, files: result.files, provided: Self.provided(),
                                                         complete: result.complete, mainFile: main)
                    LogStore.shared.log(BCDReadiness.logLine(game: title, modules: result.modules.count, findings: findings)
                                        + (result.complete ? "" : " (the scan stopped at its limit)"))
                    if !findings.isEmpty { changed() }
                }
            }
        }
        return nil
    }

    /// Whether the check has an answer for this game (also true when there is nothing to check: no folder, or the check is off).
    func checked(_ entry: LibraryEntry) -> Bool {
        guard Self.enabled, let root = BCDGraphicsDefaults.folder(of: entry) else { return true }
        let signature = BCDGraphicsDefaults.folderSignature(root, target: LibraryModel.drive.appendingPathComponent(entry.launchRelativePath))
        return scans[root.path]?.signature == signature
    }

    /// What this device has right now.
    static func provided() -> BCDReadinessProvided {
        var p = BCDReadinessProvided()
        p.vcRuntimeX64 = VCRuntime.shared.isAvailable
        p.vcRuntimeX86 = VCRuntime.x86.isAvailable   // round 625 (the owner's item 26)
        p.wineMono = WineMonoModel.available
        let drive = LibraryModel.drive
        let fm = FileManager.default
        func has(_ path: String) -> Bool { fm.fileExists(atPath: drive.appendingPathComponent(path).path) }
        let runtimes = "ProgramData/BCD/Runtimes/"
        p.openAL = has("windows/syswow64/OpenAL32.dll") || has("windows/system32/OpenAL32.dll") || has(runtimes + "openal.txt")
        p.physX = has("Program Files (x86)/NVIDIA Corporation/PhysX/Common/PhysXLoader.dll") || has(runtimes + "physx.txt")
        p.xna = has(runtimes + "xna.txt") || has("windows/Microsoft.NET/assembly/GAC_32/Microsoft.Xna.Framework")
            || has("windows/assembly/GAC_32/Microsoft.Xna.Framework")
        p.directX2010 = has(runtimes + "dx2010.txt")
        // Round 624 (agent N S12/S13): the install's memory rights and the device's address map, for the Unity and 32-bit notes.
        let rights = EntitlementStatus.check()
        p.increasedMemory = rights.increasedMemory
        p.extendedVA = rights.extendedVA
        var low: UInt64 = 0, high: UInt64 = 0
        if jit_task_map_range(&low, &high) { p.mapGB = Int(high >> 30) }
        return p
    }

    /// The launched program's file name, lowercased (round 624, agent N S13: the 32-bit note looks at that program only).
    static func mainFile(_ entry: LibraryEntry) -> String {
        URL(fileURLWithPath: entry.launchRelativePath).lastPathComponent.lowercased()
    }
}
