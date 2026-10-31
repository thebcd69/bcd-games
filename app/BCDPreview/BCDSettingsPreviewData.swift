// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

// Sample data for Settings in the preview app: a device like the owner's, a few logs, part of
// the settings catalog. Only the preview app compiles this file; in the app the same `make()`
// comes from BCDSettingsLive.swift.

extension BCDSettingsBackends {
    static func make() -> BCDSettingsBackends {
        BCDSettingsBackends(memory: BCDPreviewMemory(), graphics: BCDPreviewGraphics(), controls: BCDPreviewControls(),
                            storage: BCDPreviewStorage(), dotNet: BCDPreviewDotNet(), logs: BCDPreviewLogs(), feedback: BCDPreviewFeedback(),
                            allSettings: BCDPreviewAllSettings(), about: BCDPreviewAbout(), developer: BCDPreviewDeveloper(),
                            vcRuntime: BCDPreviewVCRuntime(), gecko: BCDPreviewGecko(), vcRuntime32: BCDPreviewVCRuntime())
    }
}

@MainActor final class BCDPreviewMemory: BCDMemoryBackend {
    @Published private var values: [BCDMemoryKey: String] = [.videoMemory: "4352", .swap: "3072", .syncEngine: "fastsync"]
    @Published private(set) var eco = false
    @Published private(set) var restartNeeded = false
    @Published private(set) var extensionTestRunning = false
    @Published private(set) var extensionTestLine: String?
    let status = BCDMemoryStatus(limitBytes: 6_379_000_000, deviceBytes: 8_589_934_592, increasedLimit: true,
                                 extendedAddressing: false, addressSpaceBytes: 67_645_734_912)
    var switchesShown: Bool { true }
    var extensionTestAvailable: Bool { true }

    func choices(_ key: BCDMemoryKey) -> [String] {
        switch key {
        case .pool: return ["", "256", "512", "640", "768", "1024", "1152"]
        case .videoMemory: return ["", "512", "1024", "1536", "2048", "3072", "4096", "4352", "4608", "5120", "6144"]
        case .swap: return ["", "1024", "2048", "3072", "4096"]
        case .swapCoverage: return ["", "blocks", "wide", "broad"]
        case .memoryPool: return ["", "2048", "4096", "6144"]
        case .syncEngine: return ["fastsync", "madsync", "wine"]
        }
    }
    func value(_ key: BCDMemoryKey) -> String { values[key] ?? "" }
    func set(_ key: BCDMemoryKey, _ value: String) { values[key] = value; restartNeeded = true }
    func setEco(_ on: Bool) { eco = on; restartNeeded = true }
    func runExtensionTest() { extensionTestLine = "The helper's memory stayed outside BCD's limit (sample)." }
    func refresh() {}
}

@MainActor final class BCDPreviewGraphics: BCDGraphicsBackend {
    let resolutions: [BCDResolution] = [
        BCDResolution(width: 1176, height: 544, shape: .screen), BCDResolution(width: 1408, height: 648, shape: .screen),
        BCDResolution(width: 1568, height: 720, shape: .screen), BCDResolution(width: 2112, height: 976, shape: .screen),
        BCDResolution(width: 2532, height: 1170, shape: .native),
        BCDResolution(width: 960, height: 540, shape: .wide), BCDResolution(width: 1280, height: 720, shape: .wide),
        BCDResolution(width: 1600, height: 900, shape: .wide), BCDResolution(width: 1920, height: 1080, shape: .wide),
        BCDResolution(width: 640, height: 480, shape: .classic), BCDResolution(width: 800, height: 600, shape: .classic),
        BCDResolution(width: 1024, height: 768, shape: .classic),
    ]
    let defaultResolution = "1408x648"
    @Published private(set) var resolution = ""
    @Published private(set) var frameCap: Int?
    @Published private(set) var holdMaximumRate = false
    @Published private(set) var metalFX = ""
    @Published private(set) var direct3D9 = ""
    @Published private(set) var orientation = ""
    @Published private(set) var restartNeeded = false
    var displayRate: Int { 120 }
    var displaySwitchesShown: Bool { true }
    func setResolution(_ value: String) { resolution = value; restartNeeded = true }
    func setFrameCap(_ fps: Int?) { frameCap = fps }
    func setHoldMaximumRate(_ on: Bool) { holdMaximumRate = on }
    func setMetalFX(_ value: String) { metalFX = value }
    func setDirect3D9(_ value: String) { direct3D9 = value }
    func setOrientation(_ value: String) { orientation = value }
}

@MainActor final class BCDPreviewControls: BCDControlsBackend {
    var canShareLayouts: Bool { true }   // round 604: the two rows show in the preview; the app's backend opens the share sheet and the picker
    func shareLayout(_ id: String) {}
    func importLayoutFromFile() {}
    @Published private(set) var touchControls = true
    @Published private(set) var opacity = 0.7
    @Published private(set) var activeLayout: String? = "custom.1"
    @Published private(set) var pointer = BCDPointerMode.touch
    @Published private(set) var touchSpeed = 2.0
    @Published private(set) var lookSpeed = 2.0
    @Published private(set) var mouseSpeed = 1.0
    @Published private(set) var rightStickMouse = false
    @Published private(set) var controllers = true
    @Published private(set) var controllerAPI = ""
    @Published private(set) var controllerOutput = ""
    let layouts = [BCDControlLayout(id: "builtin.xbox", name: "Xbox controller", builtIn: true),
                   BCDControlLayout(id: "custom.1", name: "Custom Layout 1", builtIn: false)]
    var layoutsShown: Bool { true }
    var unsavedControls: Bool { false }
    var canSaveLayouts: Bool { true }
    var nextLayoutName: String { "Custom Layout 2" }
    var restartNeeded: Bool { false }
    let controllerAPIChoices = [BCDSettingsChoice(value: "", label: "XInput (default)"),
                                BCDSettingsChoice(value: "hid", label: "DirectInput / HID"),
                                BCDSettingsChoice(value: "dualsense", label: "HID, always a DualSense"),
                                BCDSettingsChoice(value: "generic", label: "HID, always a generic gamepad")]
    let controllerOutputChoices = [BCDSettingsChoice(value: "", label: "On (default)"),
                                   BCDSettingsChoice(value: "hid", label: "DualSense output only"),
                                   BCDSettingsChoice(value: "xinput", label: "XInput rumble only"),
                                   BCDSettingsChoice(value: "0", label: "Off")]
    let keyboardMouseTemplate = [
        BCDSettingsBinding(input: "LS", action: "W A S D"), BCDSettingsBinding(input: "RS", action: "Mouse"),
        BCDSettingsBinding(input: "RT", action: "Left click"), BCDSettingsBinding(input: "LT", action: "Right click"),
        BCDSettingsBinding(input: "A", action: "Space"), BCDSettingsBinding(input: "B", action: "Ctrl"),
        BCDSettingsBinding(input: "X", action: "E"), BCDSettingsBinding(input: "Y", action: "R"),
        BCDSettingsBinding(input: "LB", action: "Q"), BCDSettingsBinding(input: "RB", action: "F"),
        BCDSettingsBinding(input: "L3", action: "Shift"), BCDSettingsBinding(input: "R3", action: "C"),
        BCDSettingsBinding(input: "D-pad", action: "Arrow keys"), BCDSettingsBinding(input: "Start", action: "Esc"),
        BCDSettingsBinding(input: "Select", action: "Tab"),
    ]
    func setTouchControls(_ on: Bool) { touchControls = on }
    func setOpacity(_ value: Double) { opacity = value }
    func useLayout(_ id: String, keepCurrentFirst: Bool) { activeLayout = id }
    func newLayout() -> Bool { true }
    func deleteLayout(_ id: String) { activeLayout = "builtin.xbox" }
    func editor(close: @escaping () -> Void) -> AnyView? { nil }
    func setPointer(_ mode: BCDPointerMode) { pointer = mode }
    func setTouchSpeed(_ value: Double) { touchSpeed = value }
    func setLookSpeed(_ value: Double) { lookSpeed = value }
    func setMouseSpeed(_ value: Double) { mouseSpeed = value }
    func setRightStickMouse(_ on: Bool) { rightStickMouse = on }
    func setControllers(_ on: Bool) { controllers = on }
    func setControllerAPI(_ value: String) { controllerAPI = value }
    func setControllerOutput(_ value: String) { controllerOutput = value }
    @Published private(set) var controllerKeys = false
    func setControllerKeys(_ on: Bool) { controllerKeys = on }
    let mouseConnected = true
    var pointerLockAvailable: Bool { UIDevice.current.userInterfaceIdiom != .phone }
    @Published private(set) var pointerLocked = false
    let pointerLockUsable = true
    func togglePointerLock() { pointerLocked.toggle() }
    @Published private(set) var mouseNeedsAssistiveTouch = UIDevice.current.userInterfaceIdiom == .phone
    func dismissMouseHint() { mouseNeedsAssistiveTouch = false }
}

/// Not installed; Accept and download walks through the download, the checks and the install.
/// -BCDSection vcruntime+downloading|installed|bundled|failed starts in that state.
@MainActor final class BCDPreviewVCRuntime: BCDVCRuntimeBackend {
    @Published private(set) var state = BCDVCRuntimeState.missing
    @Published private(set) var source = BCDVCRuntimeSource.none
    @Published private(set) var lastError: String?
    private var timer: Timer?

    init() {
        switch UserDefaults.standard.string(forKey: "BCDSection")?.split(separator: "+").dropFirst().first {
        case "downloading": state = .downloading(received: 9_830_400, expected: 25_165_824)
        case "installed": state = .installed(version: "14.44.35211"); source = .downloaded
        case "bundled": source = .bundled
        case "failed": lastError = "The download failed: The network connection was lost."
        default: break
        }
    }

    func install() {
        lastError = nil
        state = .downloading(received: 0, expected: 25_165_824)
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func tick() {
        switch state {
        case .downloading(let received, let expected):
            let next = received + 1_400_000
            state = next >= expected ? .installing(0) : .downloading(received: next, expected: expected)
        case .installing(let fraction):
            if fraction >= 1 {
                timer?.invalidate()
                state = .installed(version: "14.44.35211")
                source = .downloaded
            } else {
                state = .installing(fraction + 0.2)
            }
        default: timer?.invalidate()
        }
    }

    func cancel() {
        timer?.invalidate()
        state = source == .downloaded ? .installed(version: "14.44.35211") : .missing
        lastError = "Cancelled."
    }

    func remove() {
        state = .missing
        source = .none
    }

    func refresh() {}
}

/// Not downloaded; Download walks through both files. -BCDSection gecko+downloading|installed|failed starts in that state.
@MainActor final class BCDPreviewGecko: BCDGeckoBackend {
    @Published private(set) var state = BCDGeckoState.missing
    @Published private(set) var lastError: String?
    private var timer: Timer?

    init() {
        switch UserDefaults.standard.string(forKey: "BCDSection")?.split(separator: "+").dropFirst().first {
        case "downloading": state = .downloading(received: 71_000_000, total: BCDGeckoPin.totalBytes, arch: "x86_64")
        case "installed": state = .installed
        case "failed": lastError = "WineHQ's download server answered 503. Try again in a little while."
        default: break
        }
    }

    func install() {
        lastError = nil
        state = .downloading(received: 0, total: BCDGeckoPin.totalBytes, arch: "x86")
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func tick() {
        guard case .downloading(let received, let total, _) = state else { timer?.invalidate(); return }
        let next = received + 6_000_000
        if next >= total { timer?.invalidate(); state = .installed }
        else { state = .downloading(received: next, total: total, arch: next > BCDGeckoPin.x86.bytes ? "x86_64" : "x86") }
    }

    func cancel() {
        timer?.invalidate()
        state = .missing
        lastError = "Cancelled."
    }

    func remove() { state = .missing }
    func refresh() {}
}

@MainActor final class BCDPreviewStorage: BCDStorageBackend {
    @Published private(set) var items = [
        BCDStorageItem(id: "capture", name: "Frame captures", bytes: 121_000_000, clearable: true),
        BCDStorageItem(id: "logs", name: "Session logs", bytes: 46_300_000, clearable: true),
        BCDStorageItem(id: "sc12", name: "D3D12 shader cache", bytes: 1_310_000_000, clearable: true),
        BCDStorageItem(id: "sc11", name: "D3D11 shader cache", bytes: 312_000_000, clearable: true),
        BCDStorageItem(id: "mono", name: "Wine Mono", bytes: 137_000_000, clearable: false),
        BCDStorageItem(id: "drive", name: "Windows drive (C:)", bytes: 118_400_000_000, clearable: false),
    ]
    var deviceFree: Int64? { nil }        // the store's numbers, as the app falls back to
    var deviceTotal: Int64? { nil }
    var measuring: Bool { false }
    func refresh() {}
    func clear(_ id: String) {
        items = items.map { item in var item = item; if item.id == id { item.bytes = 0 }; return item }
    }
    @Published private(set) var savesMessage: String? = "412 files backed up, 1.3 GB."
    @Published private(set) var addGamesMessage: String?
    var savesBusy: Bool { false }
    var addingGames: Bool { false }
    func backupSaves(_ done: @escaping (URL?) -> Void) { done(nil) }
    func discardBackup(_ url: URL) {}
    func restoreSaves(from url: URL) { savesMessage = "412 files put back." }
    func addEveryGame() { addGamesMessage = "2 games added to your library." }
    var prefixToolsView: AnyView? { AnyView(BCDPrefixToolsGroup(backend: BCDPreviewPrefix.shared)) }
}

/// Round 604: Settings › Storage › Windows prefix, with a few sample copies.
@MainActor final class BCDPreviewPrefix: BCDPrefixBackend {
    static let shared = BCDPreviewPrefix()
    var busy: Bool { false }
    @Published private(set) var message: String? = "Copy made: 312 files, 14.2 MB, in Files › BCD › Prefix Copies › 2026-10-25 14.32.07."
    @Published private(set) var copies = [BCDPrefixCopyItem(id: "2026-10-25 14.32.07", made: Date() - 3600),
                                          BCDPrefixCopyItem(id: "2026-10-24 21.05.41 Before reconfigure", made: Date() - 86_400)]
    var blocked: String? { nil }
    func refresh() {}
    func makeCopy() {}
    func putBack(_ id: String) {}
    func deleteCopy(_ id: String) { copies.removeAll { $0.id == id } }
    func exportKit() { message = "Exported 312 files (14.2 MB) to Files › BCD › Prefix Exports › 2026-10-25 15.00.00 Prefix.zip." }
    func describeImport(_ url: URL) -> String { "Made 2026-10-25 15:00:00 UTC; 312 files, Windows user mythic." }
    func importKit(from url: URL) {}
    func reconfigure() {}
    func reinstallSystemFiles() {}
}

@MainActor final class BCDPreviewDeveloper: BCDDeveloperBackend {
    func startDesktop() { print("[preview] start the Windows desktop") }
    func logTail() -> String {
        (1...120).map { "[sample] line \($0) of BCD's log" }.joined(separator: "\n")
    }
    func clearLog() {}
    var controllerName: String? { "DualSense" }
    var controllerInput: [String] {
        ["Last: Cross (A)", "Left stick   x +0.00  y +0.00", "Right stick  x +0.31  y -0.12", "Triggers     L 0.00  R 0.84"]
    }
}

@MainActor final class BCDPreviewDotNet: BCDDotNetBackend {
    @Published private(set) var state = BCDMonoState.installed(version: "11.0.0")
    @Published private(set) var error: String?
    let version = "11.0.0"
    let downloadBytes: Int64 = 41_643_568
    var installedBytes: Int64? { if case .installed = state { return 137_000_000 } else { return nil } }
    func install() { state = .installed(version: version) }
    func remove() { state = .missing }
    func refresh() {}
}

@MainActor final class BCDPreviewLogs: BCDLogsBackend {
    private static let now = Date()
    let recent = [
        BCDLogFile(id: "a", name: "bcd-log.txt", date: BCDPreviewLogs.now - 300, bytes: 2_310_000),
        BCDLogFile(id: "b", name: "war3.exe-2026-10-18_17-02-11.txt", date: BCDPreviewLogs.now - 3_600, bytes: 18_700_000),
        BCDLogFile(id: "c", name: "bcd-log.prev.txt", date: BCDPreviewLogs.now - 7_400, bytes: 9_400_000),
        BCDLogFile(id: "d", name: "hollow_knight.exe-2026-10-18_14-40-02.txt", date: BCDPreviewLogs.now - 11_000, bytes: 4_200_000),
        BCDLogFile(id: "e", name: "GTA5.exe-2026-10-17_22-10-45.txt", date: BCDPreviewLogs.now - 80_000, bytes: 61_000_000),
    ]
    let sent = [BCDSentLog(code: "K7Q2-9XPA", name: "war3.exe-2026-10-16_22-24-07.txt", date: BCDPreviewLogs.now - 150_000)]
    @Published private(set) var sending = false
    @Published private(set) var sendStatus: String?
    @Published private(set) var extendedLogging = false
    @Published private(set) var perfTrace = false   // round 604
    @Published private var diagnostics: Set<BCDDiagnostic> = []
    @Published private(set) var crashTuning = true
    var crashTuningRunning: Bool { true }
    var restartNeeded: Bool { false }
    func send(_ log: BCDLogFile, comment: String) { sendStatus = "Sent. Code: P3MV-77KD (sample)" + (comment.isEmpty ? "" : ", with your comment") }
    func copy(_ code: String) {}
    func exportable() -> [URL] { [] }
    func setExtendedLogging(_ on: Bool) { extendedLogging = on }
    func setPerfTrace(_ on: Bool) { perfTrace = on }
    func diagnostic(_ item: BCDDiagnostic) -> Bool { diagnostics.contains(item) }
    func setDiagnostic(_ item: BCDDiagnostic, _ on: Bool) { if on { diagnostics.insert(item) } else { diagnostics.remove(item) } }
    func setCrashTuning(_ on: Bool) { crashTuning = on }
    func refresh() {}
}

/// Feedback on sample data. -BCDSection feedback+typed|sending|sent|failed|nolog starts in that state;
/// Send walks through sending to a code.
@MainActor final class BCDPreviewFeedback: BCDFeedbackBackend {
    private static let now = Date()
    let draft = BCDFeedbackDraft()
    @Published private(set) var newestLog: BCDFeedbackLog? = BCDFeedbackLog(name: "war3.exe-2026-10-19_09-12-40.txt", date: BCDPreviewFeedback.now - 600, bytes: 18_700_000)
    @Published private(set) var sent = [
        BCDSentFeedback(code: "R4D8TQ", kind: .idea, date: BCDPreviewFeedback.now - 90_000, line: "A button to hide the touch controls for a while"),
        BCDSentFeedback(code: "M2XK7V", kind: .bug, date: BCDPreviewFeedback.now - 260_000, line: "Hollow Knight shows a black screen after the logo"),
    ]
    @Published private(set) var state = BCDFeedbackState.idle

    init() {
        switch UserDefaults.standard.string(forKey: "BCDSection")?.split(separator: "+").dropFirst().first {
        case "typed":
            draft.kind = .bug
            draft.text = "Warcraft II closes by itself a minute after the first mission starts. It worked yesterday.\nI was on the Wi-Fi, in landscape, with a controller connected."
            draft.attachLog = true
        case "sending":
            draft.text = "The touch controls do not come back after I pause the game."
            state = .sending("Sending your message…")
        case "sent":
            state = .sent("K7Q2MX")
        case "failed":
            draft.kind = .idea
            draft.text = "Let me rename a game in the library."
            state = .failed("Not sent: The Internet connection appears to be offline. Your message is still here.")
        case "nolog":
            newestLog = nil
            sent = []
        default: break
        }
    }

    func send() {
        state = .sending("Sending your message…")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
            guard let self else { return }
            state = .sent("K7Q2MX")
            sent.insert(BCDSentFeedback(code: "K7Q2MX", kind: draft.kind, date: Date(), line: String(draft.text.prefix(60))), at: 0)
            draft.text = ""
        }
    }

    func copy(_ code: String) {}
    func refresh() {}
}

/// A slice of the real catalog, notes as the generator writes them (the old name included,
/// which Settings must never show).
@MainActor final class BCDPreviewAllSettings: BCDAllSettingsBackend {
    let entries: [BCDConfigEntry] = [
        BCDConfigEntry(key: "desktop-size", title: "Virtual desktop size (WxH)", kind: .text, defaultValue: "", category: "App & front end",
                       note: "The Wine desktop's size. BCD Dock sessions without a game's Resolution use it too. Applies at the next app start.", choices: []),
        BCDConfigEntry(key: "env.MADEIRA_PROMOTE", title: "Hold the display at its maximum rate", kind: .toggle, defaultValue: "0",
                       category: "App & front end", note: "The opt-in above; read when a session's pacing is applied.", choices: []),
        BCDConfigEntry(key: "env.MADEIRA_LIQUID_METAL", title: "", kind: .toggle, defaultValue: "0", category: "App & front end",
                       note: "Kept in bcd.cfg as env.MADEIRA_LIQUID_METAL (off unless it is 1).", choices: []),
        BCDConfigEntry(key: "env.MADEIRA_PAD_MODE", title: "Controller API (player 1)", kind: .choice, defaultValue: "", category: "Controllers",
                       note: "XInput (default): every controller is an Xbox pad. hid: player 1 becomes a HID game controller.",
                       choices: [BCDSettingsChoice(value: "", label: "XInput (default)"), BCDSettingsChoice(value: "hid", label: "DirectInput / HID")]),
        BCDConfigEntry(key: "env.MADEIRA_XINPUT", title: "Physical controllers (XInput)", kind: .toggle, defaultValue: "1",
                       category: "Controllers", note: "Mirrors GamepadInput.enabled (bcd.cfg env.MADEIRA_XINPUT, default on).", choices: []),
        BCDConfigEntry(key: "env.MADEIRA_POWER_PROBE", title: "Log GPU and device power every 5 s", kind: .toggle, defaultValue: "0",
                       category: "Debugging / logs", note: "1: every 5 s a [power] line next to the CPU energy.", choices: []),
        BCDConfigEntry(key: "metalfx-upscale", title: "MetalFX upscaling factor", kind: .choice, defaultValue: "", category: "Direct3D 12",
                       note: "Scales the presented picture with Apple's MetalFX spatial scaler (Direct3D 11 and 12).",
                       choices: [BCDSettingsChoice(value: "", label: "Off"), BCDSettingsChoice(value: "1.5", label: "1.5x"),
                                 BCDSettingsChoice(value: "2", label: "2x")]),
        BCDConfigEntry(key: "fence-chain", title: "", kind: .number, defaultValue: "1", category: "Direct3D 12",
                       note: "madeira-bcd: how many command lists one fence may follow before the runtime waits.", choices: []),
        BCDConfigEntry(key: "dxmt", title: "DXMT options (a=b;c=d)", kind: .text, defaultValue: "", category: "Direct3D 9/10/11 (DXMT)",
                       note: "Exported as DXMT_CONFIG with the options joined by ';'.", choices: []),
        BCDConfigEntry(key: "env.MADEIRA_FPS_CAP", title: "Fixed frame cap (fps)", kind: .number, defaultValue: "", category: "Windows, display & input",
                       note: "Unset (default): no cap beyond the FPS limit. 10-120: frames start at most N times a second.", choices: []),
        BCDConfigEntry(key: "env.FEX_MULTIBLOCK", title: "", kind: .toggle, defaultValue: "1", category: "x86 emulation (FEX)",
                       note: "Lets the emulator translate several blocks at once. Until Madeira closed, they stayed in use.", choices: []),
    ]
    let categories = ["App & front end", "Controllers", "Debugging / logs", "Direct3D 12", "Direct3D 9/10/11 (DXMT)",
                      "Windows, display & input", "x86 emulation (FEX)"]
    var catalogKeys: Set<String> { Set(entries.map(\.key)) }
    @Published private(set) var values: [String: String] = ["env.MADEIRA_XINPUT": "1", "fence-chain": "6", "env.FEX_TSOENABLED": "1"]
    @Published private(set) var changed = false
    func set(_ key: String, _ value: String?) {
        values[key] = (value?.isEmpty ?? true) ? nil : value
        changed = true
    }
    func refresh() {}
}

@MainActor final class BCDPreviewAbout: BCDAboutBackend {
    var version: String { "0.1.570" }
    var build: String { "570" }
    var stamp: String? { "Built 18 Oct 2026, 18:20 (sample)" }
    let licences = [BCDLicenceText(id: "gpl", title: "GNU General Public License, version 3"),
                    BCDLicenceText(id: "dxmt", title: "DXMT · MIT license"),
                    BCDLicenceText(id: "fex", title: "FEX-Emu · MIT license"),
                    BCDLicenceText(id: "wine", title: "Wine · GNU LGPL 2.1")]
    func credits() -> AnyView { AnyView(BCDPreviewCredits()) }
    func text(of licence: BCDLicenceText) -> String {
        "In the app this page shows \(licence.title) exactly as it ships inside BCD.\n\nThe preview app carries no license files."
    }
}

/// The credits as the app shows them (BCDSettingsLive.swift draws them with BCD's legal-notice
/// views); the words are the same, checked by tests/host/check-bcd-settings.py.
private struct BCDPreviewCredits: View {
    private let people: [(String, String, String)] = [
        ("Will Faust", "willfaust", "Created Madeira"),
        ("Nick", "125hz", "32-bit game support, the game library and Madeira Dock (BCD Dock in this app)"),
        ("Jfishin", "Jfishin", "The original native Steam sign-in, library and downloads"),
        ("Jesse", "JesseLovelace", "Steam Cloud saves, faster game launches, and fixes that let more games run"),
        ("Dan Perks", "danperks", "In-app JIT without StikDebug, and pairing without a computer"),
        ("spitefulowl", "spitefulowl", "Wine and FEX runtime fixes, DXMT texture and memory fixes, audio, the swap tier, and library launch options"),
        ("meshoklv", "meshoklv", "Controller fixes for games that ship their own XInput or need focus, touch taps that stay off the mouse, and a crash-guard fix"),
        ("TheHadesc", "TheHadesc", "Madeira Dock starts for games whose Steam launch entries do not start at zero, and a touch gamepad that survives the in-game keyboard"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            BCDCreditLink(id: "credit.bcd", link: URL(string: "https://github.com/thebcd69/bcd-games")) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("BCD").font(.body.weight(.semibold))
                    Text("A fork of Madeira by Will Faust and its contributors, below.").font(.subheadline).foregroundStyle(.secondary)
                    Text("Includes work from BCD by thebcd69 (https://github.com/thebcd69/bcd-games)").font(.subheadline).foregroundStyle(.tint)
                }
            }
            ForEach(people, id: \.1) { person in
                BCDCreditLink(id: "credit.\(person.1)", link: URL(string: "https://github.com/\(person.1)")) {
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Text(person.0).font(.body.weight(.semibold))
                            Text("@\(person.1)").font(.subheadline).foregroundStyle(.tint)
                        }
                        Text(person.2).font(.subheadline).foregroundStyle(.secondary)
                    }
                }
            }
            Text("BCD and Madeira are built on Wine, FEX-Emu, DXMT by Feifan He (3Shain) with the Direct3D 9 frontend by David Acevedo (dacevedo12), rpmalloc by Mattias Jansson, StikDebug, StikJIT and idevice. Thank you to everyone who contributes to these projects.\n\nBCD is free software under the GNU General Public License, version 3, with additional terms for BCD's own files (LICENSE-BCD.md), and comes with NO WARRANTY. The licence: gnu.org/licenses/gpl-3.0. The source: github.com/thebcd69/bcd-games.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }
}

extension BCDUpdateChecker {
    /// The preview app's answer: a newer release than the sample copy, without asking GitHub.
    func usePreviewSample() {
        version = "0.1.570"
        build = "570"
        fetch = { _ in
            (Data(#"{"tag_name": "v0.1.580", "html_url": "https://github.com/thebcd69/bcd-games/releases/tag/v0.1.580"}"#.utf8), 200)
        }
    }
}
