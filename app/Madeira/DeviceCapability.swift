// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  DeviceCapability.swift
//
//  What this device's GPU and CPU can run: logged at start-up and checked before a game
//  starts (2026-10-18, docs/research/ios18-2026-10-18/gameplay.md section 1.1).
//
//  iOS 18 brings devices iOS 26 does not support: the iPhone XS, XS Max and XR (A12) and the
//  7th-generation iPad (A10); the A12 iPads run iPadOS 26 as well. Their GPUs have Tier 1
//  argument buffers. DXMT (Direct3D 9/10/11) and the D3D12 runtime write GPU addresses and
//  resource IDs straight into argument buffers, which Metal allows on Tier 2 only (A13 and
//  later): there a game drew garbage, faulted the GPU or was ended by it. The A10 also lacks
//  the LSE atomics and LDAPR that the 64-bit emulator module (xtajit64.dll) is compiled with,
//  so every 64-bit program stopped at its first such instruction. Nothing said why.
//
//  Now:
//  - "[device] gpu=..." names the GPU family, the argument-buffer tier and the CPU features;
//  - a game is refused with a message on a Tier 1 GPU, and a 64-bit program on a CPU without
//    LSE or LDAPR; the Desktop still starts there (what runs in it is the user's choice);
//  - an A13 (no mesh shaders, so no geometry shaders or tessellation) gets a one-time notice.
//  An A14 or later device answers yes to everything: no refusal, no notice, only the log line.
//  env.MADEIRA_DEVICE_GATE = 0 starts games anyway (on such a GPU they crash or draw garbage).

import Foundation
import Metal

enum DeviceCapability {
    /// What the decision is made from (a test builds them by hand).
    struct Facts: Equatable {
        /// Argument buffers Tier 2 (A13 and later).
        var tier2: Bool
        /// Apple7 (A14, M1 and later): mesh shaders, through which DXMT and the D3D12 runtime
        /// run geometry shaders and tessellation.
        var apple7: Bool
        /// hw.optional.arm.FEAT_LSE and FEAT_LRCPC; nil when the system does not say.
        var lse: Bool?
        var lrcpc: Bool?
        /// The 64-bit emulator module runs here. An unknown answer never refuses.
        var x64: Bool { lse != false && lrcpc != false }
    }

    /// What one launch runs.
    struct Launch: Equatable {
        /// The Desktop: explorer with no program of its own.
        var desktop: Bool
        /// A 64-bit program (x64 or ARM64EC), or BCD Dock, whose host is x64.
        var x64: Bool
    }

    static let gpuMessage = "This device's graphics chip (A12 or older) cannot run Direct3D games in BCD: they "
        + "need Metal's Tier 2 argument buffers, which it lacks, and would crash or draw garbage. The Desktop "
        + "and 2D programs may still work: a launcher, an installer or a client that does not draw with Direct3D "
        + "starts from the Desktop's Start menu (Run)."   // round 660 (M108): the way past the refusal for such a program
    static let cpuMessage = "This device's processor (A10) cannot run 64-bit Windows programs in BCD: the x64 "
        + "emulator needs instructions (LSE atomics, LDAPR) it lacks. 32-bit programs may still work."
    static let meshNotice = "This device's graphics chip (A13) has no mesh shaders. Direct3D 10 and 11 games run "
        + "without their geometry shader and tessellation passes, which can leave parts of a scene missing, and "
        + "most Direct3D 12 games do not start. Direct3D 9 games are not affected."

    /// The gate: nil starts the launch, otherwise the message that refuses it.
    static func refusal(_ facts: Facts, _ launch: Launch, gateOn: Bool) -> String? {
        guard gateOn else { return nil }
        if !facts.x64 && launch.x64 { return cpuMessage }
        if !facts.tier2 && !launch.desktop { return gpuMessage }
        return nil
    }

    /// The one-time notice an A13 gets, or nil.
    static func notice(_ facts: Facts) -> String? { facts.tier2 && !facts.apple7 ? meshNotice : nil }

    // MARK: - This device

    private static let gpu: MTLDevice? = MTLCreateSystemDefaultDevice()

    /// Without a GPU or an answer, nothing is refused.
    static let facts = Facts(tier2: gpu.map { $0.argumentBuffersSupport == .tier2 } ?? true,
                             apple7: gpu.map { $0.supportsFamily(.apple7) } ?? true,
                             lse: cpuFeature("hw.optional.arm.FEAT_LSE"),
                             lrcpc: cpuFeature("hw.optional.arm.FEAT_LRCPC"))

    private static func cpuFeature(_ name: String) -> Bool? {
        var value: Int32 = 0
        var size = MemoryLayout<Int32>.size
        guard sysctlbyname(name, &value, &size, nil, 0) == 0 else { return nil }
        return value != 0
    }

    /// The highest Apple GPU family this device supports ("apple3" ... "apple10").
    static var family: String {
        guard let gpu else { return "none" }
        for raw in stride(from: 1010, through: 1001, by: -1) {
            if let f = MTLGPUFamily(rawValue: raw), gpu.supportsFamily(f) { return "apple\(raw - 1000)" }
        }
        return "unknown"
    }

    /// env.MADEIRA_DEVICE_GATE, the game's own file first: "0" turns the gate off.
    static var gateOn: Bool {
        if let v = MadeiraConfig.gameValue("env.MADEIRA_DEVICE_GATE") { return v != "0" }
        return MadeiraConfig.flag("MADEIRA_DEVICE_GATE")   // 0: games start even where this device's GPU or CPU cannot run them (they crash or draw garbage); unset/1: refused with a message
    }

    private static let noticeKey = "bcd.deviceNotice.a13"

    /// Once per app run, after DeviceDiagnostics.logStartup.
    static func logStartup() {
        let f = facts
        func bit(_ b: Bool?) -> String { b.map { $0 ? "1" : "0" } ?? "?" }
        let verdict = refusal(f, Launch(desktop: false, x64: false), gateOn: true) != nil ? "refused"
            : notice(f) != nil ? "no-mesh" : "ok"
        LogStore.shared.log("[device] gpu=\"\(gpu?.name ?? "none")\" family=\(family)"
            + " argument-buffers=tier\(f.tier2 ? 2 : 1) mesh=\(f.apple7 ? 1 : 0)"
            + " bc=\(gpu?.supportsBCTextureCompression == true ? 1 : 0)"
            + " atomics64=\(gpu?.supportsFamily(.apple9) == true ? 1 : 0)"
            + " cpu-lse=\(bit(f.lse)) cpu-lrcpc=\(bit(f.lrcpc)) direct3d=\(verdict) x64=\(f.x64 ? "ok" : "refused")"
            + ((!f.tier2 || !f.x64) && !gateOn ? " gate=off" : ""))
        guard let text = notice(f) else { return }
        LogStore.shared.log("[device] " + text)
        if !UserDefaults.standard.bool(forKey: noticeKey) {
            UserDefaults.standard.set(true, forKey: noticeKey)
            LibraryModel.shared.error = text
        }
    }

    // MARK: - Launches

    /// Play in the library, before JIT is asked for: a Steam game through BCD Dock, or the
    /// entry's own program.
    static func libraryRefusal(_ entry: LibraryEntry) -> String? {
        guard !facts.tier2 || !facts.x64 else { return nil }   // A13 and later: nothing to read or decide
        let dock = entry.steamAppID != nil && !entry.startsSteamGameDirectly
        let desktop = entry.desktop == true
        let program = desktop || dock ? nil : try? LibraryModel.executable(entry.launchRelativePath)
        return refused(Launch(desktop: desktop, x64: dock || program.map(is64Bit) == true), "library")
    }

    /// Any launch (ContentView.runWineFullSequence), from what the buttons, the library and
    /// BCD Dock exported: MADEIRA_EXE, MADEIRA_ARGS.
    static func launchRefusal(desktopEntry: Bool, dock: Bool) -> String? {
        guard !facts.tier2 || !facts.x64 else { return nil }   // A13 and later: nothing to read or decide
        let env = ProcessInfo.processInfo.environment
        let exe = env["MADEIRA_EXE"].flatMap { $0.isEmpty ? nil : $0 } ?? "cube.exe"
        let viaExplorer = exe.lowercased() == "explorer.exe"
        let program = viaExplorer ? quotedProgram(env["MADEIRA_ARGS"] ?? "") : exe
        let desktop = !dock && (desktopEntry || (viaExplorer && program == nil))
        return refused(Launch(desktop: desktop, x64: dock || program.map(windowsProgramIs64Bit) == true), "launch")
    }

    private static func refused(_ launch: Launch, _ via: String) -> String? {
        guard let text = refusal(facts, launch, gateOn: true), gateOn else { return nil }
        LogStore.shared.log("[device] \(via) refused (desktop=\(launch.desktop ? 1 : 0) x64=\(launch.x64 ? 1 : 0); "
                            + "env.MADEIRA_DEVICE_GATE = 0 starts it anyway): \(text)", level: .error)
        return text
    }

    /// The program explorer is asked to start, the first quoted path in its arguments.
    static func quotedProgram(_ args: String) -> String? {
        guard let open = args.firstIndex(of: "\""),
              let close = args[args.index(after: open)...].firstIndex(of: "\"") else { return nil }
        let path = String(args[args.index(after: open)..<close])
        return path.isEmpty ? nil : path
    }

    /// "C:\..." is read from drive_c; a bare name with "x64" is one of the bundled 64-bit test
    /// programs (cube-x64.exe), as WineProcessBridge.m routes them. Anything else is not known.
    static func windowsProgramIs64Bit(_ program: String) -> Bool {
        let lower = program.lowercased()
        if lower.hasPrefix("c:\\"), program.count > 3 {
            let relative = String(program.dropFirst(3)).replacingOccurrences(of: "\\", with: "/")
            return is64Bit(LibraryModel.drive.appendingPathComponent(relative))
        }
        if program.contains("\\") || program.contains("/") || program.contains(":") { return false }
        return lower.contains("x64")
    }

    static func is64Bit(_ url: URL) -> Bool {
        ["x64", "ARM64EC"].contains(PEInfo.archLabel(url) ?? "")
    }
}
