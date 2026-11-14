// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315:
// app/Sources/Core/LosslessLicence.swift, LosslessShaders.swift and parts of LosslessScaling.swift)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
//
// The list of shader ids below (BCDLosslessShaders.required) is the one lsfg-vk 1.0.0 names for the frame generation
// (https://github.com/PancakeTAS/lsfg-vk, tag v1.0.0, MIT licence: Copyright (c) 2025 lsfg-vk; the permission notice is in
// LICENSES/MIT-lsfg-vk.txt). Nothing of lsfg-vk later than 1.0.0 is used.

import Foundation
import CryptoKit

// Lossless Scaling frame generation, the part that is plain values: the licence receipt, the sealed file, reading the frame generation's
// compiled shaders out of the person's own Lossless.dll, the control file the engine reads and the words the menu shows. Nothing of
// Lossless Scaling is in BCD: the shaders are read on the device, at run time, from the copy the person downloaded from Steam with
// their own account. BCDLosslessLive.swift does the Steam, Keychain and file work; nothing here does, so the preview build and
// tests/host/bcd_lossless_checks.swift use this file as it is.

/// How BCD makes sure Lossless Scaling's file is only ever used by someone who bought it.
///
///  1. **Ownership comes from Steam.** The signed-in account's licence list (Steam's servers) must hold a licence for app 993090. That is
///     checked online; the result is kept as a *receipt* with the account and the time, signed with a key that exists only on this device
///     (HMAC-SHA256, key in the Keychain, this device only).
///  2. **The file only comes from Steam.** BCD downloads Lossless Scaling's depot with the person's own account (Steam gives the depot key
///     only to a licensed account), keeps nothing of it but `Lossless.dll`, and offers no way to bring a file in from anywhere else.
///  3. **The file is sealed.** The DLL is stored encrypted (AES-GCM) with a key made from a secret that exists only on this device and the
///     account. It is decrypted in memory only, and only while the receipt is valid.
///  4. **The receipt runs out.** It must be renewed online every `graceDays` days (a refund or a revoked licence stops the feature at the
///     next check), a receipt that was edited does not verify, a receipt for another account does not open the file, and a clock set back
///     does not extend it.
/// What this cannot do: BCD is open source and runs on a device its owner controls, so someone who builds a modified copy, or reads the
/// decrypted file out of memory with a debugger, is not stopped. It makes the normal path need a purchase; it is not copy protection.
enum BCDLosslessLicence {
    /// Lossless Scaling's Steam app id.
    static let appID = 993090
    /// How long a verified receipt keeps the feature working without the device being online.
    static let graceDays = 7

    struct Receipt: Codable, Equatable {
        var appID: Int
        /// The Steam account the licence was seen on: a SHA-256 of its name (BCDLosslessLicence.account), so the file holds no name.
        var account: String
        /// When Steam's licence list last showed the licence.
        var verifiedAt: Date
        /// The latest time the receipt was checked at all (it only moves forward): a clock set back below it is noticed.
        var lastSeen: Date
        /// Hex of the HMAC over the fields above.
        var mac: String
    }

    enum Status: Equatable {
        /// Usable, and for how many more whole days without going online.
        case valid(daysLeft: Int)
        /// The week is up: go online and check again.
        case expired
        /// The receipt was changed, or the device's clock was set back.
        case tampered
        /// The receipt belongs to another Steam account than the one signed in, or nobody is signed in.
        case otherAccount
    }

    /// The account as the receipt and the sealed file know it.
    static func account(name: String) -> String { sha256(Data(name.lowercased().utf8)) }

    // MARK: Receipt

    private static func message(_ r: Receipt) -> Data {
        Data("\(r.appID)|\(r.account)|\(Int64(r.verifiedAt.timeIntervalSince1970))|\(Int64(r.lastSeen.timeIntervalSince1970))".utf8)
    }

    static func mac(_ r: Receipt, key: SymmetricKey) -> String {
        Data(HMAC<SHA256>.authenticationCode(for: message(r), using: key)).map { String(format: "%02x", $0) }.joined()
    }

    /// A fresh receipt: Steam's licence list showed the licence just now.
    static func issue(account: String, now: Date, key: SymmetricKey) -> Receipt {
        var r = Receipt(appID: appID, account: account, verifiedAt: now, lastSeen: now, mac: "")
        r.mac = mac(r, key: key)
        return r
    }

    /// What a receipt allows now. `signedInAccount`: the account that is signed in to Steam in BCD (nil: nobody).
    static func status(of r: Receipt, now: Date, signedInAccount: String?, key: SymmetricKey) -> Status {
        guard r.appID == appID, mac(r, key: key) == r.mac else { return .tampered }
        guard let signedInAccount, signedInAccount == r.account else { return .otherAccount }
        // A clock set back: before the last time the receipt was seen (a day of slack for time zones and corrections), or a licence
        // "verified" in the future.
        if now < r.lastSeen.addingTimeInterval(-86_400) || r.verifiedAt > now.addingTimeInterval(86_400) { return .tampered }
        let age = now.timeIntervalSince(r.verifiedAt)
        if age > Double(graceDays) * 86_400 { return .expired }
        return .valid(daysLeft: max(0, graceDays - Int(age / 86_400)))
    }

    /// The receipt after a use: `lastSeen` moves forward (never back) and the MAC is made again.
    static func seen(_ r: Receipt, now: Date, key: SymmetricKey) -> Receipt {
        var out = r
        out.lastSeen = max(r.lastSeen, now)
        out.mac = mac(out, key: key)
        return out
    }

    // MARK: Sealed file

    enum VaultError: Error { case sealFailed, opened(String) }

    /// The key the DLL is sealed with: made from this device's secret and the account, so the file does not open for another account or
    /// on another device.
    static func vaultKey(deviceSecret: SymmetricKey, account: String) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(inputKeyMaterial: deviceSecret, salt: Data(account.utf8), info: Data("bcd-lossless-scaling-file".utf8), outputByteCount: 32)
    }

    static func receiptKey(deviceSecret: SymmetricKey) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(inputKeyMaterial: deviceSecret, salt: Data(), info: Data("bcd-lossless-scaling-receipt".utf8), outputByteCount: 32)
    }

    static func seal(_ file: Data, deviceSecret: SymmetricKey, account: String) throws -> Data {
        let box = try AES.GCM.seal(file, using: vaultKey(deviceSecret: deviceSecret, account: account), authenticating: Data("\(appID)".utf8))
        guard let combined = box.combined else { throw VaultError.sealFailed }
        return combined
    }

    static func open(_ sealed: Data, deviceSecret: SymmetricKey, account: String) throws -> Data {
        do {
            let box = try AES.GCM.SealedBox(combined: sealed)
            return try AES.GCM.open(box, using: vaultKey(deviceSecret: deviceSecret, account: account), authenticating: Data("\(appID)".utf8))
        } catch { throw VaultError.opened("\(error)") }
    }

    static func sha256(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
}

/// Reads the compiled shaders of the frame generation out of Lossless Scaling's `Lossless.dll` (in memory: the file itself is never
/// written anywhere in the clear).
///
/// The DLL keeps its Direct3D 11 compute shaders as resources of type RCDATA (10), each one a DXBC blob. Which ids are the frame
/// generation's is taken from lsfg-vk 1.0.0 (see the head of this file); the reader itself is Porthole's own PE reader. BCD takes the
/// frame generation's shaders only: every other resource of the DLL, the upscalers' shaders among them, is left where it is.
enum BCDLosslessShaders {
    /// RCDATA, where the DLL keeps them.
    static let resourceType: UInt32 = 10

    /// The shader ids of the frame generation: the "quality" layout and the lighter "performance" one (the engine's kQuality and
    /// kPerformance tables in d3d11_lsfg.hpp name the same ids).
    static let required: [Int] = [255, 256, 267, 268, 269, 270, 275, 276, 277, 278, 279, 257, 259, 260, 261, 262, 263, 264, 265, 266, 258, 271, 272, 273, 274,
                                  290, 291, 292, 293, 298, 299, 300, 301, 302, 280, 282, 283, 284, 285, 286, 287, 288, 289, 281, 294, 295, 296, 297]

    /// Resource id -> the DXBC blob, for the ids in `wanted` only.
    static func extract(from dll: Data, wanted: Set<Int> = Set(required)) -> [Int: Data] {
        func u16(_ o: Int) -> Int? { o >= 0 && o + 2 <= dll.count ? Int(dll[dll.startIndex + o]) | Int(dll[dll.startIndex + o + 1]) << 8 : nil }
        func u32(_ o: Int) -> Int? { o >= 0 && o + 4 <= dll.count ? (0..<4).reduce(0) { $0 | Int(dll[dll.startIndex + o + $1]) << (8 * $1) } : nil }
        guard dll.count > 0x40, u16(0) == 0x5a4d, let pe = u32(0x3c), u32(pe) == 0x4550, let nsec = u16(pe + 6), let opt = u16(pe + 20) else { return [:] }
        // The sections: where each one is in the file and in memory.
        var secs: [(name: String, va: Int, size: Int, raw: Int)] = []
        for i in 0..<min(nsec, 96) {
            let o = pe + 24 + opt + i * 40
            guard let vs = u32(o + 8), let va = u32(o + 12), let rs = u32(o + 16), let rp = u32(o + 20), o + 8 <= dll.count else { return [:] }
            let name = String(decoding: dll.subdata(in: (dll.startIndex + o)..<(dll.startIndex + o + 8)).prefix { $0 != 0 }, as: UTF8.self)
            secs.append((name, va, max(vs, rs), rp))
        }
        func fileOffset(_ rva: Int) -> Int? { secs.first { $0.va <= rva && rva < $0.va + $0.size }.map { $0.raw + (rva - $0.va) } }
        guard let rsrc = secs.first(where: { $0.name == ".rsrc" }) else { return [:] }
        let base = rsrc.raw
        func entries(_ dir: Int) -> [(id: Int, offset: Int)] {
            guard let named = u16(dir + 12), let ids = u16(dir + 14), named + ids < 4096 else { return [] }
            return (0..<(named + ids)).compactMap { i in
                guard let n = u32(dir + 16 + i * 8), let o = u32(dir + 20 + i * 8) else { return nil }
                return (n & 0x8000_0000 != 0 ? -1 : n, o)
            }
        }
        var out: [Int: Data] = [:]
        for type in entries(base) where type.id == Int(resourceType) && type.offset & 0x8000_0000 != 0 {
            for item in entries(base + (type.offset & 0x7fff_ffff)) where item.id >= 0 && wanted.contains(item.id) && item.offset & 0x8000_0000 != 0 {
                // One language per resource is all the DLL has; the first is taken.
                guard let lang = entries(base + (item.offset & 0x7fff_ffff)).first, lang.offset & 0x8000_0000 == 0,
                      let rva = u32(base + lang.offset), let size = u32(base + lang.offset + 4), size > 32, size < 16 << 20,
                      let off = fileOffset(rva), off >= 0, off + size <= dll.count else { continue }
                let blob = dll.subdata(in: (dll.startIndex + off)..<(dll.startIndex + off + size))
                if blob.prefix(4) == Data("DXBC".utf8) { out[item.id] = blob }
            }
        }
        return out
    }

    /// The frame generation's shaders as the file the engine reads inside the game: "LSFG", u32 version 1, u32 count, then
    /// (u32 id, u32 size, bytes) each, little-endian. nil when a needed shader is missing (a different version of the program).
    static func container(from dll: Data) -> Data? {
        let shaders = extract(from: dll)
        guard required.allSatisfy({ shaders[$0] != nil }) else { return nil }
        var out = Data("LSFG".utf8)
        func put(_ v: Int) { var le = UInt32(v).littleEndian; withUnsafeBytes(of: &le) { out.append(contentsOf: $0) } }
        put(1); put(shaders.count)
        for (id, blob) in shaders.sorted(by: { $0.key < $1.key }) { put(id); put(blob.count); out.append(blob) }
        return out
    }
}

/// A game's choice and what the engine is told while it runs.
enum BCDLossless {
    /// The game's own line: frames added between two of the game's ("1" to "3"); no line is off.
    static let key = "lossless-scaling"
    /// The motion search runs at the picture's size divided by this (1 to 4); no line is 1.
    static let flowKey = "lossless-flow"
    /// "1": the quality layout; no line is the lighter one.
    static let qualityKey = "lossless-quality"

    /// Where the engine finds its files inside the game (the prefix's C: drive), and the same folder below drive_c.
    static let windowsFolder = #"C:\ProgramData\BCD"#
    static let folder = "ProgramData/BCD"
    static let shaderFile = "lsfg.bin", controlFile = "lsfg.ctl", statusFile = "lsfg.ctl.status"
    static var windowsControlPath: String { windowsFolder + "\\" + controlFile }
    static var windowsShaderPath: String { windowsFolder + "\\" + shaderFile }

    static let choices: [(String, String)] = [("", "Off"), ("1", "2× (one frame added)"), ("2", "3× (two added)"), ("3", "4× (three added)")]
    static let title = "Lossless Scaling frame generation (experimental)"
    static let note = "Your own copy of Lossless Scaling makes one to three pictures between every two frames the game draws: it needs no help from the game, and it works on the picture before MetalFX enlarges it. For Direct3D 11 and 12 games. Set it up once in Settings, under Graphics: BCD checks the purchase with Steam and downloads the program with your account. Extra frames cost GPU time and add a little delay; the frame-rate limit then counts every frame shown. A 64-bit game runs on BCD's own build of the Direct3D 11 library while this is on; a Direct3D 12 game uses that library for the extra frames only."

    static let setupFooter = "For frame generation with your own copy of Lossless Scaling, a program sold on Steam. Nothing of it is in BCD: BCD asks Steam whether your account owns it, downloads it with your account, and keeps its file sealed on this device. The purchase is checked again every week. A game uses it when its page says so, under Display."

    /// The frames a game's line asks for: 1 to 3, nil for anything else.
    static func generated(_ value: String?) -> Int? {
        guard let value, let n = Int(value.trimmingCharacters(in: .whitespaces)), (1...3).contains(n) else { return nil }
        return n
    }

    static func flow(_ value: String?) -> Double {
        guard let value, let f = Double(value.trimmingCharacters(in: .whitespaces)), f.isFinite else { return 1 }
        return min(4, max(1, f))
    }

    /// What is set for the game that runs now (the menu changes it while the game runs).
    struct Control: Equatable {
        var enabled = false
        /// Frames made between two real ones.
        var generated = 1
        /// The motion search runs at the picture's size divided by this.
        var flow = 1.0
        /// Generate even when the game is fast enough not to need it (for looking at the picture).
        var force = false
        /// Run the passes but show nothing extra: tells what the passes cost apart from what showing more frames costs.
        var measure = false
        /// The lighter pass layout: fewer and smaller intermediate pictures, less graphics work.
        var performance = true

        /// The control file's text. `hz`: the screen's top rate.
        func text(hz: Int) -> String {
            "enabled=\(enabled ? 1 : 0)\ngenerated=\(min(3, max(1, generated)))\nflow=\(min(4, max(1, flow)))\nforce=\(force ? 1 : 0)\n"
                + "performance=\(performance ? 1 : 0)\nmeasure=\(measure ? 1 : 0)\nhz=\(hz)\n"
        }
    }

    /// The status file's lines "key=value".
    static func parse(status text: String) -> [String: String] {
        var out: [String: String] = [:]
        for line in text.split(whereSeparator: \.isNewline) {
            guard let eq = line.firstIndex(of: "=") else { continue }
            out[String(line[..<eq])] = String(line[line.index(after: eq)...])
        }
        return out
    }

    /// The engine's status as a sentence for the menu.
    static func words(control: Control, status st: [String: String]) -> String {
        guard control.enabled else { return "Off." }
        switch st["state"] {
        case "generating":
            return "Making \(st["generated"] ?? "?") extra frames a second. The game draws \(st["game"] ?? "?") FPS; the passes take \(st["cost"] ?? "?") ms of the game's thread per frame; the motion search works on \(st["flow"] ?? "?") of a \(st["size"] ?? "?") picture."
        case "running":
            return "On, but no frames are being made now: the game draws \(st["game"] ?? "?") FPS and the screen (\(st["screen"] ?? "?") Hz) cannot show more than the game already gives it."
        case "starting":
            return "Starting: the shaders are being prepared. The game may stutter for a few seconds."
        case "error":
            return "Not working: \(st["message"] ?? "unknown reason")."
        case "off":
            return "Switched off in the game."
        default:
            return "Waiting for the game to draw. Direct3D 11 and 12 games are supported; if nothing appears after a few seconds this game may draw with something else."
        }
    }

    /// What Settings shows.
    struct Setup: Equatable {
        var signedIn = false
        /// nil: not known yet. true/false: what Steam's licence list said at the last check.
        var owned: Bool? = nil
        var installed = false
        var status: BCDLosslessLicence.Status? = nil
        var busy: String? = nil
        var progress: Double? = nil
        var message: String? = nil

        var usable: Bool { if installed, case .valid? = status { return true } else { return false } }

        /// One line for the page.
        var words: String {
            if let busy { return busy }
            if !signedIn { return "Sign in to Steam first (Settings, Accounts)." }
            guard installed else {
                if owned == false { return "This Steam account does not own Lossless Scaling." }
                return "Not set up. BCD checks your Steam purchase and downloads Lossless Scaling with your account; nothing of it is in BCD itself."
            }
            switch status {
            case .valid(let days)?: return "Ready. The Steam purchase is checked again within \(days) day\(days == 1 ? "" : "s")."
            case .expired?: return "The last check of your Steam purchase is more than a week old. Check again to go on using it."
            case .otherAccount?: return "It was set up with another Steam account. Sign in to that account, or set it up again with this one."
            case .tampered?, nil: return "Its record on this device cannot be read. Set it up again."
            }
        }
    }
}

/// The in-game menu's "Lossless Scaling" section (Display).
enum BCDLosslessMenu {
    static let flowChoices = [BCDChoice(id: "1", title: "Full"), BCDChoice(id: "1.33", title: "3/4"), BCDChoice(id: "2", title: "Half"), BCDChoice(id: "4", title: "Quarter")]

    static func flowChoice(_ flow: Double) -> String {
        if flow >= 3 { return "4" }
        if flow >= 1.6 { return "2" }
        return flow >= 1.15 ? "1.33" : "1"
    }

    /// `live`: this session was started with the game's choice on, so everything switches while the game runs. `wanted`: the game's line.
    /// `ready`: Lossless Scaling is set up on this device.
    static func section(live: Bool, ready: Bool, wanted: Int?, control: BCDLossless.Control, status: [String: String]) -> BCDMenuSection? {
        guard ready || live || wanted != nil else { return nil }
        let choices = [BCDChoice(id: "", title: "Off"), BCDChoice(id: "1", title: "2×"), BCDChoice(id: "2", title: "3×"), BCDChoice(id: "3", title: "4×")]
        let selected = live ? (control.enabled ? String(control.generated) : "") : (wanted.map(String.init) ?? "")
        var rows = [BCDMenuRow(id: "mm.lsfg",
                               title: "Frame generation",
                               detail: "Your copy of Lossless Scaling makes one to three pictures between every two frames the game draws. It costs GPU time and adds a little delay. The frame-rate limit counts every frame shown while it is on.",
                               kind: .choice(choices, selected: selected))]
        guard live else {
            if !ready {
                rows.append(BCDMenuRow(id: "mm.lsfg.note", title: "Set Lossless Scaling up first, in Settings under Graphics.", kind: .note(.warm)))
            } else if wanted != nil {
                rows.append(BCDMenuRow(id: "mm.lsfg.note", title: "It applies the next time this game starts.", symbol: "arrow.clockwise.circle", kind: .note(.warm)))
            }
            return BCDMenuSection(id: "mm.lsfg", title: "Lossless Scaling", rows: rows)
        }
        rows.append(BCDMenuRow(id: "mm.lsfg.note", title: BCDLossless.words(control: control, status: status),
                               kind: .note(status["state"] == "error" ? .warm : status["state"] == "generating" ? .good : .soft)))
        guard control.enabled else { return BCDMenuSection(id: "mm.lsfg", title: "Lossless Scaling", rows: rows) }
        rows.append(BCDMenuRow(id: "mm.lsfg.light", title: "Lighter passes",
                               detail: "Lossless Scaling's performance layout: fewer and smaller intermediate pictures, clearly less GPU work, a slightly softer result. Changing it rebuilds the passes: a short hitch.",
                               kind: .toggle(control.performance)))
        rows.append(BCDMenuRow(id: "mm.lsfg.flow", title: "Motion search detail",
                               detail: "How much of the picture's size the motion search works on. Less is cheaper and a little less exact.",
                               kind: .choice(flowChoices, selected: flowChoice(control.flow))))
        rows.append(BCDMenuRow(id: "mm.lsfg.force", title: "Always generate",
                               detail: "Also while the game is as fast as the screen can show. For looking at the picture; it is off again the next time the game starts.",
                               kind: .toggle(control.force)))
        rows.append(BCDMenuRow(id: "mm.lsfg.measure", title: "Measure only",
                               detail: "Runs the passes without showing their pictures, so the frame rate shows what the passes cost. Off again the next time the game starts.",
                               kind: .toggle(control.measure)))
        return BCDMenuSection(id: "mm.lsfg", title: "Lossless Scaling", rows: rows)
    }
}
