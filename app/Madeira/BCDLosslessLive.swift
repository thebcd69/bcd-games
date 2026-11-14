// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315:
// app/Sources/Core/LosslessScaling.swift) under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation
import UIKit
import CryptoKit
import Security
import os

// Lossless Scaling frame generation, the part that touches Steam, the Keychain and files (the rules are in BCDLossless.swift).
// It checks the signed-in account's licence list, downloads Lossless Scaling from Steam with that account, seals its `Lossless.dll` on
// this device, and, for a game whose own line `lossless-scaling` asks for it, writes the frame generation's shaders into the prefix for
// the length of the session (C:\ProgramData\BCD\lsfg.bin) with the control file the engine reads. Nothing here runs for a game without
// that line, except removing those files if an earlier session left them. Engine-bound: the preview build leaves this file out.

/// The files and the Keychain: plain calls that any thread may make (a session's start runs on the launch worker).
enum BCDLosslessFiles {
    private static var fm: FileManager { FileManager.default }
    static var root: URL {
        let dir = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("LosslessScaling", isDirectory: true)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static var sealedURL: URL { root.appendingPathComponent("Lossless.dll.sealed") }
    static var receiptURL: URL { root.appendingPathComponent("license.json") }
    static var prefixFolder: URL { MadeiraDock.drive.appendingPathComponent(BCDLossless.folder, isDirectory: true) }
    static var shaderFileURL: URL { prefixFolder.appendingPathComponent(BCDLossless.shaderFile) }
    static var controlFileURL: URL { prefixFolder.appendingPathComponent(BCDLossless.controlFile) }
    static var statusFileURL: URL { prefixFolder.appendingPathComponent(BCDLossless.statusFile) }

    /// A random secret that exists only on this device (Keychain, this device only, after first unlock). The keys are made from it.
    static func deviceSecret() -> SymmetricKey {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "com.thebcd69.bcd.lossless-scaling",
                                    kSecAttrAccount as String: "device"]
        var read = query; read[kSecReturnData as String] = true; read[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        if SecItemCopyMatching(read as CFDictionary, &out) == errSecSuccess, let data = out as? Data, data.count == 32 { return SymmetricKey(data: data) }
        let fresh = SymmetricKey(size: .bits256)
        var add = query
        add[kSecValueData as String] = fresh.withUnsafeBytes { Data($0) }
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemDelete(query as CFDictionary)
        SecItemAdd(add as CFDictionary, nil)
        return fresh
    }
    static func receiptKey() -> SymmetricKey { BCDLosslessLicence.receiptKey(deviceSecret: deviceSecret()) }

    /// The signed-in account as the receipt knows it (nil: nobody is signed in).
    static var account: String? { SteamSignIn.accountName.map(BCDLosslessLicence.account(name:)) }

    static func receipt() -> BCDLosslessLicence.Receipt? {
        (try? Data(contentsOf: receiptURL)).flatMap { try? JSONDecoder().decode(BCDLosslessLicence.Receipt.self, from: $0) }
    }
    static func save(_ r: BCDLosslessLicence.Receipt) {
        if let data = try? JSONEncoder().encode(r) { try? data.write(to: receiptURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]) }
    }
    static var fileBytes: Int? { (try? fm.attributesOfItem(atPath: sealedURL.path)[.size] as? Int) ?? nil }
    static func status(_ receipt: BCDLosslessLicence.Receipt?) -> BCDLosslessLicence.Status? {
        guard let receipt else { return nil }
        return BCDLosslessLicence.status(of: receipt, now: Date(), signedInAccount: account, key: receiptKey())
    }

    /// The file's bytes, only while the receipt is valid for the account in use and the file opens with this device's and account's key.
    /// Every use moves the receipt's `lastSeen` forward, which is how a clock set back is noticed.
    static func dllData() -> Data? {
        guard fileBytes != nil, var r = receipt(), case .valid? = status(r), let account,
              let sealed = try? Data(contentsOf: sealedURL), let file = try? BCDLosslessLicence.open(sealed, deviceSecret: deviceSecret(), account: account) else { return nil }
        r = BCDLosslessLicence.seen(r, now: Date(), key: receiptKey())
        save(r)
        return file
    }

    static func removeSessionFiles() {
        try? fm.removeItem(at: shaderFileURL)
        try? fm.removeItem(at: controlFileURL)
        try? fm.removeItem(at: statusFileURL)
    }

    /// Writes the frame generation's shaders for the game about to start. False when the licence is not valid, the file will not open,
    /// or it is a version without the shaders the passes need.
    static func prepareForGame() -> Bool {
        guard let dll = dllData(), let box = BCDLosslessShaders.container(from: dll) else { return false }
        try? fm.createDirectory(at: prefixFolder, withIntermediateDirectories: true)
        do { try box.write(to: shaderFileURL, options: [.atomic]) } catch { return false }
        return true
    }

    static func write(_ control: BCDLossless.Control, hz: Int) {
        try? fm.createDirectory(at: prefixFolder, withIntermediateDirectories: true)
        try? control.text(hz: hz).write(to: controlFileURL, atomically: true, encoding: .utf8)
    }
}

/// Frames are being added in this session (read by ProMotionIntent from any thread; written on the main thread).
private var bcdLosslessGenerating = false

@MainActor
final class BCDLosslessLive: ObservableObject {
    static let shared = BCDLosslessLive()

    /// What Settings shows.
    @Published private(set) var busy: String?
    @Published private(set) var progress: Double?
    @Published private(set) var message: String?
    /// nil: not known yet (never checked, or signed out). true/false: what Steam's licence list said at the last check.
    @Published private(set) var owned: Bool?
    @Published private(set) var receipt: BCDLosslessLicence.Receipt?
    @Published private(set) var fileBytes: Int?
    /// What is set for the game that runs now.
    @Published private(set) var control = BCDLossless.Control()
    /// This session was started with the game's line on: the engine reads the control file.
    private(set) var live = false

    private let fm = FileManager.default

    private init() { reload() }

    // MARK: State

    var signedIn: Bool { SteamSignIn.isSignedIn }
    var installed: Bool { fileBytes != nil }

    /// What the stored receipt allows now.
    var status: BCDLosslessLicence.Status? { BCDLosslessFiles.status(receipt) }

    /// The file may be opened: it is stored, and the receipt is valid for the account in use.
    var usable: Bool { if installed, case .valid? = status { return true } else { return false } }

    var setup: BCDLossless.Setup {
        BCDLossless.Setup(signedIn: signedIn, owned: owned, installed: installed, status: status, busy: busy, progress: progress, message: message)
    }

    func reload() {
        receipt = BCDLosslessFiles.receipt()
        fileBytes = BCDLosslessFiles.fileBytes
    }

    // MARK: Checking the licence

    /// Asks Steam, over the network, whether the signed-in account has a licence for Lossless Scaling, and writes a fresh receipt when it
    /// has. A refund or a revoked licence is noticed here: the stored file is removed.
    @discardableResult
    func verify() async -> Bool {
        guard signedIn, let account = BCDLosslessFiles.account else { message = "Sign in to Steam first (Settings, Accounts)."; return false }
        guard busy == nil else { return false }
        busy = "Checking your Steam purchase\u{2026}"; message = nil
        defer { busy = nil }
        do {
            if try await SteamOwnedLibrary.shared.licenceNow(appID: UInt32(BCDLosslessLicence.appID)) {
                owned = true
                BCDLosslessFiles.save(BCDLosslessLicence.issue(account: account, now: Date(), key: BCDLosslessFiles.receiptKey()))
                reload()
                LogStore.shared.log("[lossless] Steam lists Lossless Scaling on this account: receipt renewed")
                return true
            }
            owned = false
            if installed || receipt != nil {
                remove()
                message = "This Steam account has no license for Lossless Scaling any more, so the stored file was removed."
            } else {
                message = "This Steam account does not own Lossless Scaling. Buy it on Steam, then check again."
            }
            LogStore.shared.log("[lossless] Steam does not list Lossless Scaling on this account")
            return false
        } catch {
            message = "Checking your Steam purchase did not work: " + SteamSignIn.message(error)
            return false
        }
    }

    // MARK: The file

    /// Downloads Lossless Scaling from Steam with the person's own account (Steam only releases the files to a licensed account), keeps
    /// only `Lossless.dll`, sealed, and deletes the rest. There is no other way to bring the file in.
    func download() async {
        guard busy == nil else { return }
        guard await verify(), let account = BCDLosslessFiles.account else { return }
        busy = "Downloading from Steam\u{2026}"; progress = 0; message = nil
        defer { busy = nil; progress = nil }
        let work = BCDLosslessFiles.root.appendingPathComponent("download", isDirectory: true)
        try? fm.removeItem(at: work)
        defer { try? fm.removeItem(at: work) }
        do {
            try fm.createDirectory(at: work, withIntermediateDirectories: true)
            let folder = try await SteamOwnedLibrary.shared.downloadForApp(appID: UInt32(BCDLosslessLicence.appID), steamApps: work) { [weak self] p in
                self?.progress = p.totalBytes > 0 ? min(1, Double(p.doneBytes) / Double(p.totalBytes)) : nil
                self?.busy = p.phase == .finishing ? "Finishing\u{2026}" : "Downloading from Steam\u{2026}"
            }
            guard let dll = fm.enumerator(at: folder, includingPropertiesForKeys: nil)?.compactMap({ $0 as? URL }).first(where: { $0.lastPathComponent.lowercased() == "lossless.dll" }),
                  let data = try? Data(contentsOf: dll), data.count > 1024 else {
                message = "The download finished but Lossless.dll was not in it."
                return
            }
            guard BCDLosslessShaders.container(from: data) != nil else {
                message = "This version of Lossless Scaling does not have the frame generation shaders BCD knows (LSFG 3.1)."
                return
            }
            busy = "Sealing the file on this device\u{2026}"
            let sealed = try BCDLosslessLicence.seal(data, deviceSecret: BCDLosslessFiles.deviceSecret(), account: account)
            try sealed.write(to: BCDLosslessFiles.sealedURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            LogStore.shared.log("[lossless] Lossless.dll sealed on this device: \(data.count) bytes, sha256 \(BCDLosslessLicence.sha256(data).prefix(16))")
            reload()
        } catch is CancellationError {
            message = "Cancelled."
        } catch {
            message = "Downloading Lossless Scaling did not work: " + SteamSignIn.message(error)
        }
    }

    /// Removes the sealed file and the receipt (and anything a session left in the prefix).
    func remove() {
        try? fm.removeItem(at: BCDLosslessFiles.sealedURL)
        try? fm.removeItem(at: BCDLosslessFiles.receiptURL)
        try? fm.removeItem(at: BCDLosslessFiles.root.appendingPathComponent("download", isDirectory: true))
        BCDLosslessFiles.removeSessionFiles()
        reload()
    }

    // MARK: For a running game

    /// A session is about to start (ContentView's launch worker, before Wine's environment is built). With the game's line
    /// `lossless-scaling` (or Settings' line for every game) and a valid setup, the shaders and a fresh control file go into the prefix
    /// and the engine is told where they are; the line for the log is returned. Without the line nothing is exported, anything an
    /// earlier session left in the prefix is removed, and nil is returned.
    nonisolated static func launch() -> String? {
        BCDLosslessFiles.removeSessionFiles()
        unsetenv("MADEIRA_LSFG_CTL")
        unsetenv("MADEIRA_LSFG_SHADERS")
        guard let generated = BCDLossless.generated(MadeiraConfig.gameValue(BCDLossless.key) ?? MadeiraConfig.get(BCDLossless.key)) else { return nil }
        guard BCDLosslessFiles.prepareForGame() else {
            let setup = BCDLossless.Setup(signedIn: SteamSignIn.isSignedIn, installed: BCDLosslessFiles.fileBytes != nil, status: BCDLosslessFiles.status(BCDLosslessFiles.receipt()))
            return "[lossless] this game asks for Lossless Scaling frame generation, but it is not ready (\(setup.words)): off for this session"
        }
        let quality = (MadeiraConfig.gameValue(BCDLossless.qualityKey) ?? MadeiraConfig.get(BCDLossless.qualityKey)) == "1"
        let flow = BCDLossless.flow(MadeiraConfig.gameValue(BCDLossless.flowKey) ?? MadeiraConfig.get(BCDLossless.flowKey))
        let start = BCDLossless.Control(enabled: true, generated: generated, flow: flow, force: false, measure: false, performance: !quality)
        let hz = ProMotionIntent.panelMaxFPS
        BCDLosslessFiles.write(start, hz: hz)
        setenv("MADEIRA_LSFG_CTL", BCDLossless.windowsControlPath, 1)
        setenv("MADEIRA_LSFG_SHADERS", BCDLossless.windowsShaderPath, 1)
        // A 64-bit game's Direct3D 11 library with the passes is BCD's source build (tools/build-d3d11-dll.sh); a 32-bit game's has them.
        setenv("MADEIRA_D3D11_SRC", "1", 1)
        // One frame generator at a time: MetalFX's stays off for this session (the app's switch wins over the game's line).
        _ = madeira_fgl_set(0)
        madeira_lsfg_set_shown(Int32(generated + 1))
        DispatchQueue.main.async { MainActor.assumeIsolated { shared.sessionStarted(start) } }
        return "[lossless] Lossless Scaling frame generation on for this session: \(generated) added per frame, flow 1/\(flow), \(quality ? "quality" : "lighter") passes; "
            + "control file \(BCDLossless.windowsControlPath); MADEIRA_D3D11_SRC=1; MetalFX frame generation off; the frame-rate limit counts every frame shown"
    }

    private func sessionStarted(_ start: BCDLossless.Control) {
        live = true
        control = start
        bcdLosslessGenerating = start.enabled
        ProMotionIntent.apply(mode: madeira_get_vsync_locked())   // the screen's top rate while frames are added
    }

    /// The screen is held at its top rate while frames are added. ProMotionIntent asks.
    nonisolated static var holdsPanel: Bool { bcdLosslessGenerating }

    /// Changes the setting of the running game (the in-game menu). Only a session that was started with the game's line can.
    func apply(_ new: BCDLossless.Control) {
        guard live else { return }
        var next = new
        if next.enabled, !fm.fileExists(atPath: BCDLosslessFiles.shaderFileURL.path), !BCDLosslessFiles.prepareForGame() { next.enabled = false }
        control = next
        BCDLosslessFiles.write(next, hz: ProMotionIntent.panelMaxFPS)
        bcdLosslessGenerating = next.enabled
        madeira_lsfg_set_shown(next.enabled && !next.measure ? Int32(next.generated + 1) : 1)
        ProMotionIntent.apply(mode: madeira_get_vsync_locked())
        LogStore.shared.log("[lossless] menu: \(next.enabled ? "on, \(next.generated) added" : "off"), flow 1/\(next.flow), \(next.performance ? "lighter" : "quality") passes"
                            + (next.force ? ", always" : "") + (next.measure ? ", measure only" : ""))
    }

    /// What the engine said last (state, game, generated, cost, message ...). Empty when it has said nothing.
    func engineStatus() -> [String: String] {
        guard live, let text = try? String(contentsOf: BCDLosslessFiles.statusFileURL, encoding: .utf8) else { return [:] }
        return BCDLossless.parse(status: text)
    }

    /// The game is over: the shaders and the control files do not stay in the prefix.
    func sessionEnded() {
        BCDLosslessFiles.removeSessionFiles()
        guard live else { return }
        live = false
        control = BCDLossless.Control()
        bcdLosslessGenerating = false
        madeira_lsfg_set_shown(1)
        _ = madeira_fgl_set(-1)
    }
}
