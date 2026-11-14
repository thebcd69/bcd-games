// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Darwin
import Foundation
import UIKit
import SwiftUI

/// Settings › JIT on the engine, for a host to show (Settings' detail pane): it owns its
/// BCDJITLive while it is on screen. `enableJIT` is the interface's own Enable JIT
/// (BCDActions.enableJIT), the same path Play takes.
struct BCDJITLiveSection: View {
    var showsTitle = true
    var scrolls = true
    var back: (() -> Void)? = nil
    var backHook: BCDSectionKit.BackHook? = nil
    @StateObject private var backend: BCDJITLive

    init(enableJIT: @escaping () -> Void, showsTitle: Bool = true, scrolls: Bool = true,
         back: (() -> Void)? = nil, backHook: BCDSectionKit.BackHook? = nil) {
        self.showsTitle = showsTitle
        self.scrolls = scrolls
        self.back = back
        self.backHook = backHook
        _backend = StateObject(wrappedValue: BCDJITLive(enableJIT: enableJIT))
    }

    var body: some View {
        BCDJITSection(backend: backend, showsTitle: showsTitle, scrolls: scrolls, back: back, backHook: backHook)
    }
}

/// The JIT setup the app opens when a method still needs setting up (JITCoordinator.showSetup,
/// ContentView): Settings › JIT on its own, with Done. `enableJIT` is ContentView's Enable JIT,
/// the path Play takes. Touch only, as the sheet it replaces, unless the host passes its BCDFocus.
struct BCDJITSetupSheet: View {
    let enableJIT: () -> Void
    var focus: BCDFocus? = nil
    @StateObject private var ownFocus = BCDFocus(watchControllers: false)
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        BCDJITLiveSection(enableJIT: enableJIT, back: { dismiss() })
            .overlay(alignment: .topTrailing) {
                BCDSectionKit.ActionButton(title: "Done", kind: .outline, id: "settings.jit.done") { dismiss() }
                    .padding(.horizontal, 18).padding(.top, 10)
            }
            .background { BCDBackdrop() }
            .coordinateSpace(name: BCDFocus.space)
            .environmentObject(focus ?? ownFocus)
            .foregroundStyle(BCDStyle.text)
            .preferredColorScheme(.dark)
    }
}

/// Settings › JIT on the engine (engine-bound: tools/ui-preview.py's ENGINE_BOUND keeps it out
/// of the preview app). It only reads the JIT code's state and calls what the old JIT
/// settings and setup called; it changes nothing about how JIT is turned on:
/// - the state: StikJITHelper.ready and .flaggedWithoutDebugger (what Play itself checks),
///   SigningStatus, JITCoordinator (method, pairing file, progress, errors),
///   OnDevicePairing, LocalDevVPN, LoopbackProbe, JITNetworkShortcut;
/// - Enable JIT: the closure it is given, the interface's own Enable JIT (BCDActions.enableJIT,
///   carried out with ContentView's enableJIT), so the section and Play take the same path;
/// - the rest: JITCoordinator's method, pairing import, setup check, the built-in helper on
///   demand (the old setup's Built-in Enable JIT) and disk image reset, OnDevicePairing.start/
///   cancel, LocalDevVPN.open, the shortcut's switch and links.
@MainActor final class BCDJITLive: BCDJITBackend {
    @Published private(set) var state: BCDJITSectionState
    private let enable: () -> Void
    private var probe: BCDJITProbe = .notRun
    private var watching: Set<AnyCancellable> = []

    /// `enableJIT`: what Enable JIT does in the rest of the interface (BCDActions.enableJIT).
    init(enableJIT: @escaping () -> Void) {
        enable = enableJIT
        state = BCDJITSectionState(platform: Self.platform)
        let changes: [AnyPublisher<Void, Never>] = [
            JITCoordinator.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            OnDevicePairing.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            JITNetworkShortcut.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            // Back from StikDebug, LocalDevVPN or Settings: JIT, the VPN or the pairing may have changed.
            NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification).map { _ in () }.eraseToAnyPublisher(),
        ]
        Publishers.MergeMany(changes).receive(on: DispatchQueue.main)
            .sink { [weak self] in MainActor.assumeIsolated { self?.read() } }
            .store(in: &watching)
        read()
    }

    /// This device, its iOS version and whether BCD runs inside LiveContainer (it sets
    /// LC_HOME_PATH for its guests, as JITBuiltInHost.swift checks).
    static var platform: BCDJITPlatform {
        let version = ProcessInfo.processInfo.operatingSystemVersion
        var info = utsname()
        uname(&info)
        let model = withUnsafePointer(to: &info.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(cString: $0) }
        }
#if targetEnvironment(simulator)
        let simulator = true
#else
        let simulator = false
#endif
        // Inside LiveContainer, whether its Helper Process Support starts BCD's helper (JITBuiltInHost.swift; .notAvailable outside).
        return BCDJITPlatform(osMajor: version.majorVersion, osMinor: version.minorVersion, model: model,
                              inLiveContainer: getenv("LC_HOME_PATH") != nil, simulator: simulator,
                              lcHelper: LiveContainerHelper.state)
    }

    /// The app's pairing on the device (OnDevicePairing), as BCDJITPairingPanel shows it.
    static func pairingRun() -> BCDJITPairingRun {
        let pairing = OnDevicePairing.shared
        switch pairing.phase {
        case .idle: return .idle
        case .waiting: return .waiting(shortBackground: pairing.backgroundLimited)
        case .pin(let code): return .code(code)
        case .paired(let device): return .paired(device)
        case .failed(let message): return .failed(message)
        }
    }

    func refresh() {
        JITCoordinator.shared.refreshPairingStatus()
        read()
        if state.vpn == .connected, probe == .notRun { testConnection() }
    }

    private func read() {
        let jit = JITCoordinator.shared
        let platform = state.platform
        var next = BCDJITSectionState(platform: platform)

        if !SigningStatus.current.debuggable {
            next.now = .impossible
        } else if jit.busy {
            next.now = .working(jit.status)
        } else if StikJITHelper.ready {
            next.now = .on
        } else if StikJITHelper.flaggedWithoutDebugger {
            next.now = .enableAgain
        } else {
            next.now = .off
        }
        next.method = BCDJITMethodChoice(rawValue: jit.method.rawValue) ?? .automatic
        next.resolved = BCDJITMethodChoice(rawValue: jit.resolvedMethod.rawValue) ?? .stikDebug
        next.stikDebugFound = StikJITHelper.isAvailable
        next.pairingFile = !jit.pairingImported ? .none : (jit.pairingSource == .onDevice ? .pairedHere : .imported)

        next.pairing = Self.pairingRun()

        // Inside LiveContainer canOpenURL cannot see LocalDevVPN, but the route still tells
        // whether its tunnel is up (a socket call, no traffic).
        let routed = LoopbackProbe.vpnInterfaceUp
        if platform.inLiveContainer {
            next.vpn = routed ? .connected : .unknown
        } else {
            next.vpn = !LocalDevVPN.isInstalled ? .notInstalled : (routed ? .connected : .installed)
        }
        // A test that passed says nothing once the tunnel is down.
        if !routed, probe == .answered { probe = .notRun }
        next.probe = probe

        let shortcut = JITNetworkShortcut.shared
        next.shortcutOn = shortcut.enabled
        next.shortcutCopy = BCDJITRules.shortcutAvailable(platform) ? JITShortcutFile.url : nil
        next.busy = jit.busy
        next.message = jit.busy ? nil : jit.status
        next.error = jit.error
        // The same fixes the old JIT alerts offered (jitConnectionActions, JITSetup.swift).
        if let problem = jit.connectionProblem {
            if problem == .pairing { next.fixes.append(BCDJITRules.canPairOnDevice(platform) ? .pairAgain : .importFile) }
            // The shortcut is the fix only where it can work (iOS 27, not inside LiveContainer).
            next.fixes.append(shortcut.enabled && BCDJITRules.shortcutAvailable(platform) ? .runShortcut : .connectVPN)
        }
        next.setupAvailable = OnboardingModel.shared.available
        next.txm = jit.txmPresent
        if next != state { state = next }
    }

    func enableJIT() { enable() }

    func choose(_ method: BCDJITMethodChoice) {
        guard let engine = JITMethod(rawValue: method.rawValue) else { return }
        JITCoordinator.shared.method = engine
        LogStore.shared.log("[bcd-settings] JIT method=\(method.rawValue)")
        read()
    }

    func importPairingFile(_ url: URL) {
        JITCoordinator.shared.importPairingFile(url)
        LogStore.shared.log("[bcd-settings] JIT pairing file import ok=\(JITCoordinator.shared.pairingImported ? 1 : 0)")
        read()
    }

    /// The old JIT setup's log line for a file picker that failed.
    func pairingImportFailed(_ message: String) {
        LogStore.shared.log("[jit-built-in] pairing import failed: \(message)", level: .error)
    }

    func startPairing() { OnDevicePairing.shared.start() }
    func stopPairing() { OnDevicePairing.shared.cancel() }
    func checkSetup() { JITCoordinator.shared.prepareBuiltIn() }

    /// The old JIT setup's Built-in "Enable JIT": the built-in helper now, whatever the method.
    func enableWithBuiltIn() { JITCoordinator.shared.enableBuiltIn() }
    func clearDiskImageCache() { JITCoordinator.shared.resetDDI() }
    func openLocalDevVPN() { LocalDevVPN.open() }

    /// LocalDevVPN's loopback, as Enable JIT checks it first (at most 0.4 s, off the main thread).
    func testConnection() {
        probe = .checking
        read()
        LoopbackProbe.check { [weak self] result in
            LogStore.shared.log("[bcd-settings] JIT loopback reachable=\(result.reachable ? 1 : 0) (\(result.detail))")
            self?.probe = result.reachable ? .answered : .noAnswer
            self?.read()
        }
    }

    func setShortcut(_ on: Bool) { JITNetworkShortcut.shared.enabled = on }

    func addShortcut() {
        LogStore.shared.log("[jit-shortcut] add: iCloud link")
        UIApplication.shared.open(JITShortcutFile.iCloudLink)
    }

    func runShortcut() { JITCoordinator.shared.connectWithShortcut() }
    func runSetup() { OnboardingModel.shared.rerun() }
}
