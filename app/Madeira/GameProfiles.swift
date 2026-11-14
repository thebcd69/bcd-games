//
//  GameProfiles.swift
//  Madeira
//
//  madeira-bcd: a game's own settings file -- Winlator's per-shortcut
//  overrides, in this app's terms.
//
//  Same syntax as Documents/bcd.cfg (key = value, env.NAME = value, # for
//  comments). The launch exports its path as MADEIRA_CFG_GAME; the native
//  reader (build/madeira_cfg.h) lets a key set here win over bcd.cfg, and
//  WineProcessBridge exports its env.* lines after bcd.cfg's. So any
//  runtime switch -- a D3D12 experiment, a FEX option, a DXMT setting -- can be
//  set for one game without touching the others, and without a new build.
//
//  The game settings sheet edits the common ones with pickers; the raw file is
//  one tap further for everything else.
//

import Foundation
import SwiftUI

struct GameProfile {
    let windowsPath: String

    static var directory: URL? {
        guard let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            return nil
        }
        let dir = base.appendingPathComponent("GameConfigs", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    var url: URL? {
        GameProfile.directory?.appendingPathComponent(
            String(format: "%016llx.cfg", CoverStore.stableHash(windowsPath.lowercased())))
    }

    /// The whole file ("" when there is none). Setting "" deletes it.
    var text: String {
        get {
            guard let u = url, let t = try? String(contentsOf: u, encoding: .utf8) else { return "" }
            return t
        }
        nonmutating set {
            guard let u = url else { return }
            if newValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                try? FileManager.default.removeItem(at: u)
            } else {
                try? (newValue.hasSuffix("\n") ? newValue : newValue + "\n").write(to: u, atomically: true, encoding: .utf8)
            }
        }
    }

    /// key -> value, the last line winning, as the native reader does.
    var values: [String: String] { GameProfile.parse(text) }

    static func values(ofFile url: URL) -> [String: String] {
        parse((try? String(contentsOf: url, encoding: .utf8)) ?? "")
    }

    static func parse(_ text: String) -> [String: String] {
        var out: [String: String] = [:]
        for raw in text.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let k = line[..<eq].trimmingCharacters(in: .whitespaces)
            if !k.isEmpty { out[k] = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces) }
        }
        return out
    }

    /// True when the file sets anything at all.
    var hasSettings: Bool { !values.isEmpty }

    func get(_ key: String) -> String? {
        guard let v = values[key], !v.isEmpty else { return nil }
        return v
    }

    /// Sets or (nil / "") removes `key`, keeping every other line as written.
    func set(_ key: String, _ value: String?) {
        var lines = text.components(separatedBy: "\n")
        while lines.last == "" { lines.removeLast() }
        if lines.isEmpty { lines = ["# BCD settings for \(windowsPath)", "# bcd.cfg syntax; keys here win over bcd.cfg."] }
        lines.removeAll { raw in
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { return false }
            return line[..<eq].trimmingCharacters(in: .whitespaces) == key
        }
        if let value, !value.isEmpty { lines.append("\(key) = \(value)") }
        let kept = lines.filter { !$0.trimmingCharacters(in: .whitespaces).hasPrefix("#") && !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        text = kept.isEmpty ? "" : lines.joined(separator: "\n")
    }

    // MARK: The curated switches

    /// MetalFX spatial upscaling factor: the D3D12 runtime reads
    /// metalfx-upscale itself; a D3D11 game gets DXMT's own MetalFX swapchain
    /// with the same factor (BCDLaunch.applyExtras).
    static let metalFXChoices: [(String, String)] = [("", "Off"), ("fit", "To the screen"), ("1.5", "1.5×"), ("2", "2×")]

    /// Frame limit the session starts with (the Session panel still changes it).
    static let fpsChoices: [(String, String)] = [("", "Default (60)"), ("30", "30 FPS"), ("40", "40 FPS"),
                                                  ("60", "60 FPS"), ("max", "Display max"), ("raw", "Unlimited")]

    /// The D3D12 runtime's cap on low-detail tessellation (dxil-tess-max-factor).
    static let tessChoices: [(String, String)] = [("", "Default (3)"), ("2", "Low (2)"), ("4", "Medium (4)"),
                                                   ("8", "High (8)"), ("0", "Full (no cap)")]

    /// ml1120/ml1121 async-submit: the D3D12 runtime encodes the game's command
    /// lists on its own worker thread instead of inside the game's
    /// ExecuteCommandLists (10-16 ms of every Ghost of Tsushima frame in the
    /// 2026-09-28 14:04 log).
    static let submitChoices: [(String, String)] = [("", "Game's thread (default)"), ("1", "Worker thread")]

    /// fence-chain: F1 = every GPU pass waits for the one before (accurate,
    /// default); F6 = only where the game's barriers say so (the GPU overlaps
    /// work; the overlay's F pill switches the same thing live).
    static let gpuSyncChoices: [(String, String)] = [("", "Every pass (F1, default)"), ("6", "Barriers only (F6)")]
    /// env.MADEIRA_FRAMEGEN: MetalFX frame interpolation in the present path
    /// (tools/patch-dxmt-framegen.py), D3D11 and D3D12 alike.
    static let frameGenChoices: [(String, String)] = [("", "Off"), ("1", "MetalFX 2× (experimental)")]
    /// ml2100 env.MADEIRA_PAD_MODE (GamepadInput.configuredPadMode): player 1 as an
    /// XInput pad (default) or as a HID controller -- a DualSense when it is one.
    static let padModeChoices: [(String, String)] = [("", "XInput (default)"), ("hid", "DirectInput / HID")]

    var frameLimit: FrameLimit? {
        switch get("fps-limit") {
        case "30": return .locked30
        case "40": return .locked40
        case "60": return .locked60
        case "max": return .display
        case "raw": return .unlimited
        default: return nil
        }
    }

    var metalFXFactor: Double? {
        guard let s = get("metalfx-upscale"), let f = Double(s), f >= 1.1 else { return nil }
        return min(f, 3)
    }
}

/// The raw file, for any switch the sheet has no picker for.
struct GameConfigEditor: View {
    let profile: GameProfile
    let onSave: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @FocusState private var typing: Bool

    var body: some View {
        Form {
            Section {
                TextEditor(text: $text)
                    .focused($typing)
                    .font(.caption.monospaced())
                    .frame(minHeight: 260)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
            } footer: {
                Text("Write one key = value per line, the same as bcd.cfg. An env.NAME = value line exports "
                     + "an environment variable (FEX_*, DXMT_*, MADEIRA_*). A key set here beats bcd.cfg, for "
                     + "this game only. For example: dxil-tess-max-factor = 0, metalfx-upscale = 2, "
                     + "env.FEX_MULTIBLOCK = 1.")
            }
        }
        .navigationTitle("Game config")
        .navigationBarTitleDisplayMode(.inline)
        .bcdKeyboardDone { typing = false }
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Save") {
                    profile.text = text
                    onSave()
                    dismiss()
                }
            }
        }
        .onAppear { text = profile.text }
    }
}
