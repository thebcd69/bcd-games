// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

/// Settings › JIT on sample data, for the preview app. Variants (-BCDTab settings-jit-<variant>):
/// none (iOS 27, paired, JIT on), "ios26" (an iPhone 12 on iOS 26: StikDebug needs Always Run
/// Scripts; a connection error), "ios18" (StikDebug only; Automatic comes down to it), "lc" (iOS 18 inside
/// LiveContainer: StikDebug unseen, so Automatic would pick Built-in; JIT switched on outside BCD), "lc-ready" and "lc-off"
/// (iOS 27 inside LiveContainer with its Helper Process Support ready, or switched off).
@MainActor final class BCDPreviewJITBackend: BCDJITBackend {
    @Published private(set) var state: BCDJITSectionState

    init(variant: String) {
        switch variant {
        case "ios26":
            var s = BCDJITSectionState(platform: BCDJITPlatform(osMajor: 26, osMinor: 1, model: "iPhone13,2"))
            s.now = .off
            s.method = .automatic
            s.resolved = .stikDebug
            s.stikDebugFound = true
            s.pairingFile = .imported
            s.vpn = .installed
            s.error = "There's no connection to this iPhone. Connect LocalDevVPN and try again."
            s.fixes = [.connectVPN]
            s.setupAvailable = true
            s.txm = false
            s.shortcutOn = true
            state = s
        case "ios18":
            var s = BCDJITSectionState(platform: BCDJITPlatform(osMajor: 18, osMinor: 6, model: "iPhone15,2"))
            s.now = .off
            s.method = .automatic
            s.resolved = .stikDebug
            s.stikDebugFound = true
            s.vpn = .connected
            s.probe = .answered
            s.setupAvailable = true
            state = s
        case "lc":
            var s = BCDJITSectionState(platform: BCDJITPlatform(osMajor: 18, osMinor: 6, model: "iPhone14,2", inLiveContainer: true))
            s.now = .enableAgain
            s.method = .automatic
            s.resolved = .builtIn
            s.vpn = .unknown
            state = s
        case "lc-ready", "lc-off":
            // iOS 27 inside LiveContainer: its Helper Process Support starts BCD's helper ("lc-ready"), or the switch is off ("lc-off").
            let ready = variant == "lc-ready"
            var s = BCDJITSectionState(platform: BCDJITPlatform(osMajor: 27, osMinor: 0, model: "iPhone18,2", inLiveContainer: true,
                                                                lcHelper: ready ? .ready : .switchOff))
            s.now = .off
            s.method = ready ? .builtIn : .automatic
            s.resolved = ready ? .builtIn : .stikDebug
            s.stikDebugFound = true
            s.pairingFile = ready ? .pairedHere : .none
            s.vpn = .connected
            s.setupAvailable = true
            state = s
        default:
            var s = BCDJITSectionState(platform: BCDJITPlatform(osMajor: 27, osMinor: 0, model: "iPhone18,2"))
            s.now = .on
            s.method = .builtIn
            s.resolved = .builtIn
            s.stikDebugFound = true
            s.pairingFile = .pairedHere
            s.vpn = .connected
            s.probe = .answered
            s.shortcutOn = true
            s.shortcutCopy = Bundle.main.bundleURL.appendingPathComponent("JIT.shortcut")
            s.message = "The built-in helper has attached."
            s.setupAvailable = true
            s.txm = true
            state = s
        }
    }

    func refresh() {}
    func enableJIT() {
        state.error = nil
        state.fixes = []
        state.now = .on
        state.message = "Sample: JIT is on."
    }
    func choose(_ method: BCDJITMethodChoice) {
        state.method = method
        state.resolved = method == .automatic ? (state.stikDebugFound ? .stikDebug : .builtIn) : method
    }
    func importPairingFile(_ url: URL) { state.pairingFile = .imported }
    func pairingImportFailed(_ message: String) {}
    func enableWithBuiltIn() { enableJIT() }
    func startPairing() { state.pairing = .code("482 913") }
    func stopPairing() { state.pairing = .idle }
    func checkSetup() { state.message = "Sample: the connection works and the disk image is ready." }
    func clearDiskImageCache() {}
    func openLocalDevVPN() { state.vpn = .connected }
    func testConnection() { state.probe = state.vpn == .connected ? .answered : .noAnswer }
    func setShortcut(_ on: Bool) { state.shortcutOn = on }
    func addShortcut() {}
    func runShortcut() { state.vpn = .connected }
    func runSetup() {}
}
