// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers

// Settings › JIT: whether JIT is on now, how this device turns it on (only the methods that
// work on this iOS version and in this environment), the pairing and LocalDevVPN they
// need, a guide for each, and Enable JIT. The section names no engine type: it reads and
// asks through BCDJITBackend, which BCDJITLive.swift carries out on the app's JIT code and
// the preview app on sample data (docs/BCD-UI-ARCHITECTURE.md). Which method works where
// comes from the iOS 18 runtime audit (docs/research/ios18-2026-10-18/runtime.md, 2.2-2.5).

// MARK: - Rules (tests/host/check-bcd-settings-accounts.py compiles this part on its own)

enum BCDJITMethodChoice: String, CaseIterable, Identifiable, Hashable {
    case automatic, stikDebug, builtIn
    var id: String { rawValue }
}

/// Where BCD runs, as far as turning JIT on is concerned.
struct BCDJITPlatform: Hashable {
    var osMajor: Int
    var osMinor: Int = 0
    /// The hardware model, "iPhone14,2" style.
    var model: String
    /// BCD runs as a guest of LiveContainer.
    var inLiveContainer = false
    var simulator = false
    /// Inside LiveContainer: whether its Helper Process Support starts BCD's own helper (LiveContainerHelper, JITBuiltInHost.swift).
    var lcHelper: BCDLCHelper = .notAvailable
}

/// LiveContainer's Helper Process Support (LiveContainer 3.8.13, the nightly of 2026-11-08) as BCD finds it: with it LiveContainer
/// starts a guest's own app extensions, so BCD's built-in helper can run inside LiveContainer (follow-up of the night round 2026-11-11,
/// owner's go). Only `ready`, which BCD proves, lets the helper run there; the other states choose the words.
enum BCDLCHelper: String, Hashable {
    /// Not inside LiveContainer, or not looked at.
    case notAvailable
    case ready
    /// BCD runs in a LiveContainer multitask window, where LiveContainer starts no helper.
    case multitask
    case switchOff
    /// This LiveContainer has no Helper Process Support.
    case tooOld
    /// The switch is on, but LiveContainer has not prepared BCD's helper: it does that only when BCD is installed.
    case notPrepared
    /// Everything looks right, but LiveContainer handed BCD no helper.
    case noHook
}

enum BCDJITRules {
    enum BuiltInBlock: Equatable { case needsIOS26, liveContainer, simulator }

    /// Why BCD's built-in helper cannot run here, or nil when it can: it needs iOS 26 or
    /// newer and a real device, and inside LiveContainer it starts only through LiveContainer's
    /// Helper Process Support (the same gates as the app's helper host, JITBuiltInHost.swift).
    static func builtInBlock(_ p: BCDJITPlatform) -> BuiltInBlock? {
        if p.simulator { return .simulator }
        if p.osMajor < 26 { return .needsIOS26 }
        if p.inLiveContainer && p.lcHelper != .ready { return .liveContainer }
        return nil
    }

    /// The methods worth offering here, in the order shown. StikDebug works on every iOS
    /// version BCD runs on; Built-in, and Automatic (which can pick Built-in), only where
    /// the helper can run. On iOS 18 and inside LiveContainer that leaves StikDebug alone,
    /// unless LiveContainer's Helper Process Support is ready: then Built-in too, chosen by
    /// hand (Automatic stays StikDebug there, so it is not offered).
    static func offered(_ p: BCDJITPlatform) -> [BCDJITMethodChoice] {
        guard builtInBlock(p) == nil else { return [.stikDebug] }
        return p.inLiveContainer ? [.stikDebug, .builtIn] : [.automatic, .stikDebug, .builtIn]
    }

    /// What BCD says about its built-in helper inside LiveContainer, for each state.
    static func lcHelperText(_ s: BCDLCHelper) -> String {
        switch s {
        case .ready:
            return "Built-in works here through LiveContainer's Helper Process Support."
        case .multitask:
            return "BCD is open in a LiveContainer multitask window, where its built-in helper can't start. Open BCD with a normal launch, or use StikDebug."
        case .switchOff:
            return "To use BCD's built-in helper here, turn on Helper Process Support: in LiveContainer, hold BCD, tap Settings, turn it on, then close BCD and open it again. Until then, StikDebug is the way here."
        case .tooOld:
            return "BCD's built-in helper runs inside LiveContainer 3.8.13 (the nightly of 8 November 2026) or newer, with Helper Process Support on for BCD. Until then, StikDebug is the way here."
        case .notPrepared:
            return "Helper Process Support is on, but LiveContainer prepares BCD's helper only when BCD is installed. Install BCD's IPA again in LiveContainer (choose Replace), then open it. Until then, StikDebug is the way here."
        case .noHook:
            return "LiveContainer didn't hand BCD its helper. Check that LiveContainer was installed with its app extensions. Until then, StikDebug is the way here."
        case .notAvailable:
            return "BCD's built-in helper can't run inside LiveContainer, so StikDebug is the way here."
        }
    }

    /// Pairing on the device itself, with no computer: iOS 27 and the helper.
    static func canPairOnDevice(_ p: BCDJITPlatform) -> Bool {
        builtInBlock(p) == nil && p.osMajor >= 27
    }

    /// The JIT shortcut needs iOS 27 (it keeps the VPN with Store Content, which iOS 26
    /// lacks), and its way back to BCD cannot reach an app inside LiveContainer.
    static func shortcutAvailable(_ p: BCDJITPlatform) -> Bool {
        p.osMajor >= 27 && !p.inLiveContainer && !p.simulator
    }

    /// StikDebug hands BCD's script to its debugger by itself only on devices it counts as
    /// having TXM: none before iOS 26; on iOS 26, iPhone14,2 and newer phones and iPad14,5
    /// and newer iPads; on iOS 27 and later every device but iPad8,11 and iPad8,12.
    /// Everywhere else it attaches and leaves without the script, and games cannot get
    /// their JIT memory, unless its Always Run Scripts setting is on.
    static func stikDebugRunsScripts(_ p: BCDJITPlatform) -> Bool {
        if p.osMajor >= 27 { return !["iPad8,11", "iPad8,12"].contains(p.model) }
        guard p.osMajor == 26, let (family, major, minor) = model(p.model) else { return false }
        switch family {
        case "iPhone": return (major, minor) >= (14, 2)
        case "iPad": return (major, minor) >= (14, 5)
        default: return false
        }
    }

    /// The StikDebug guide asks for Always Run Scripts.
    static func needsAlwaysRunScripts(_ p: BCDJITPlatform) -> Bool { !stikDebugRunsScripts(p) }

    /// "iPhone14,2" -> ("iPhone", 14, 2).
    static func model(_ text: String) -> (String, Int, Int)? {
        let family = String(text.prefix { $0.isLetter })
        let numbers = text.dropFirst(family.count).split(separator: ",", omittingEmptySubsequences: false)
        guard !family.isEmpty, numbers.count == 2, let major = Int(numbers[0]), let minor = Int(numbers[1]) else { return nil }
        return (family, major, minor)
    }
}

// MARK: - End of rules

// MARK: - What the section shows and asks for

/// Whether JIT can serve a game right now.
enum BCDJITNow: Equatable {
    case on
    case off
    /// JIT was switched on outside BCD (StikDebug's own app list, say): no debugger stayed
    /// to set up BCD's JIT memory, so it has to be turned on again from BCD.
    case enableAgain
    /// Enable JIT is under way; the step, when known.
    case working(String?)
    /// This copy is signed so that no debugger may attach.
    case impossible
}

enum BCDJITPairingFile: Equatable { case none, imported, pairedHere }

/// Pairing on the device itself (iOS 27), while it runs.
enum BCDJITPairingRun: Equatable {
    case idle
    /// Waiting for Settings; `shortBackground`: BCD gets only a short while in the background.
    case waiting(shortBackground: Bool)
    case code(String)
    case paired(String)
    case failed(String)
}

enum BCDJITVPN: Equatable {
    case notInstalled, installed, connected
    /// BCD cannot tell (inside LiveContainer, which hides other apps from it).
    case unknown
}

enum BCDJITProbe: Equatable { case notRun, checking, answered, noAnswer }

/// What a failed Enable JIT offers next.
enum BCDJITFix: String, Equatable { case connectVPN, runShortcut, pairAgain, importFile }

struct BCDJITSectionState: Equatable {
    var platform: BCDJITPlatform
    var now: BCDJITNow = .off
    /// The method chosen in Settings, and the one Enable JIT will use.
    var method: BCDJITMethodChoice = .automatic
    var resolved: BCDJITMethodChoice = .stikDebug
    var stikDebugFound = false
    var pairingFile: BCDJITPairingFile = .none
    var pairing: BCDJITPairingRun = .idle
    var vpn: BCDJITVPN = .unknown
    var probe: BCDJITProbe = .notRun
    var shortcutOn = false
    /// The JIT shortcut's copy inside BCD, for adding it without a connection.
    var shortcutCopy: URL?
    var busy = false
    /// What the last action reported, and what went wrong.
    var message: String?
    var error: String?
    var fixes: [BCDJITFix] = []
    var setupAvailable = false
    /// TXM (SPTM) on this device, as the built-in helper last reported it; nil until it ran.
    var txm: Bool?
}

@MainActor protocol BCDJITBackend: ObservableObject {
    var state: BCDJITSectionState { get }
    func refresh()
    func enableJIT()
    func choose(_ method: BCDJITMethodChoice)
    func importPairingFile(_ url: URL)
    /// The file picker failed (for the log).
    func pairingImportFailed(_ message: String)
    func startPairing()
    func stopPairing()
    /// Tests the connection and prepares the Developer Disk Image (Built-in).
    func checkSetup()
    /// Turns JIT on with the built-in helper now, whatever method is chosen.
    func enableWithBuiltIn()
    func clearDiskImageCache()
    /// Connects LocalDevVPN, or opens its App Store page when it is missing.
    func openLocalDevVPN()
    func testConnection()
    func setShortcut(_ on: Bool)
    func addShortcut()
    func runShortcut()
    func runSetup()
}

/// The guides: pairing on the device (iOS 27), a pairing file from a computer, StikDebug.
enum BCDJITGuide: String, Hashable { case pairHere, pairingFile, stikDebug }

/// Pages on the web the guides point to.
enum BCDJITLinks {
    static let stikDebug = URL(string: "https://github.com/StikDebug/StikDebug/releases/latest")!
    static let pairingFileGuide = URL(string: "https://github.com/StikDebug/StikDebug-Guide/blob/main/pairing_file.md")!
}

// MARK: - The section

/// Settings › JIT. A host that scrolls and runs the buttons itself (Settings) passes
/// `scrolls: false` and `backHook`; on its own, `back` is what B does here.
struct BCDJITSection<Backend: BCDJITBackend>: View {
    @ObservedObject var backend: Backend
    var showsTitle = true
    var scrolls = true
    var back: (() -> Void)? = nil
    var backHook: BCDSectionKit.BackHook? = nil
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.openURL) private var openURL
    @State private var guide: BCDJITGuide?
    @State private var importing = false
    @State private var importError: String?
    @State private var sharing = false

    private static var prefix: String { "settings.jit." }
    private var state: BCDJITSectionState { backend.state }
    private var platform: BCDJITPlatform { backend.state.platform }
    private var device: String { BCDSectionKit.deviceWord }

    var body: some View {
        BCDSectionKit.Page(title: "JIT", subtitle: "BCD translates Windows games while they run, and iOS allows that only with JIT.",
                           showsTitle: showsTitle, scrolls: scrolls, focusPrefix: Self.prefix) {
            BCDSectionKit.Columns {
                statusCard
                methodCard
                guideCard
            } right: {
                if BCDJITRules.builtInBlock(platform) == nil { pairingCard }
                vpnCard
                if BCDJITRules.shortcutAvailable(platform) || state.shortcutOn { shortcutCard }
            }
        }
        .modifier(BCDSectionKit.Buttons(focus: focus, key: 0, inner: { false }, back: back, hook: backHook))
        .onAppear { backend.refresh() }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.propertyList, .data]) { result in
            switch result {
            case .success(let url):
                importError = nil
                backend.importPairingFile(url)
            case .failure(let failure):
                importError = failure.localizedDescription
                backend.pairingImportFailed(failure.localizedDescription)
            }
        }
        .sheet(isPresented: $sharing) {
            if let copy = state.shortcutCopy { BCDSectionKit.ShareSheet(items: [copy]) }
        }
    }

    // MARK: Now

    private var statusCard: some View {
        BCDSectionKit.Card(accent: state.now != .on && state.now != .impossible) {
            headline
            if case .working(let step) = state.now, let step {
                BCDSectionKit.Note(text: step, symbol: "hourglass", tone: .info)
            } else if let error = state.error {
                BCDSectionKit.Note(text: error, symbol: "exclamationmark.triangle.fill", tone: .bad)
                ForEach(state.fixes, id: \.self) { fix in fixButton(fix) }
            } else if let message = state.message {
                BCDSectionKit.Note(text: message, symbol: "checkmark.circle", tone: .good)
            }
            if state.now != .on && state.now != .impossible {
                BCDSectionKit.ActionButton(title: state.busy ? "Turning JIT on…" : "Enable JIT", symbol: state.busy ? "hourglass" : "bolt.fill",
                                           kind: .primary, fill: true, id: Self.prefix + "enable",
                                           enabled: !state.busy) { backend.enableJIT() }
            }
            Text(environmentLine).bcdFont(.body, 12).foregroundStyle(BCDStyle.textSoft)
        }
    }

    @ViewBuilder private var headline: some View {
        switch state.now {
        case .on:
            BCDSectionKit.Headline(text: "JIT is on", symbol: "bolt.fill", tone: .good,
                                   detail: "Games can start. It stays on until BCD closes.")
        case .off:
            BCDSectionKit.Headline(text: "JIT is off", symbol: "bolt.slash.fill", tone: .warn,
                                   detail: "Turn it on here, or press Play: BCD turns it on before the game starts.")
        case .enableAgain:
            BCDSectionKit.Headline(text: "Turn JIT on again from BCD", symbol: "bolt.badge.clock", tone: .warn,
                                   detail: "JIT was switched on outside BCD, for example from StikDebug's app list, so BCD can't set up the memory games run in. Enable JIT here fixes it.")
        case .working:
            BCDSectionKit.Headline(text: "Turning JIT on…", symbol: "hourglass", tone: .info,
                                   detail: "Another app may open for a moment and then send you back.")
        case .impossible:
            BCDSectionKit.Headline(text: "This copy of BCD can't use JIT", symbol: "xmark.octagon.fill", tone: .bad,
                                   detail: "It was signed in a way that lets no debugger attach to it. Install BCD again with a development signature, for example through SideStore, AltStore or Xcode.")
        }
    }

    private var environmentLine: String {
        var parts = [platform.osMinor > 0 ? "iOS \(platform.osMajor).\(platform.osMinor)" : "iOS \(platform.osMajor)"]
        if platform.inLiveContainer { parts.append("inside LiveContainer") }
        if let txm = state.txm { parts.append(txm ? "TXM present" : "no TXM") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder private func fixButton(_ fix: BCDJITFix) -> some View {
        switch fix {
        case .connectVPN:
            BCDSectionKit.ActionButton(title: state.vpn == .notInstalled ? "Get LocalDevVPN" : "Connect LocalDevVPN", symbol: "network",
                                       fill: true, id: Self.prefix + "fix.vpn") { backend.openLocalDevVPN() }
        case .runShortcut:
            BCDSectionKit.ActionButton(title: "Run the JIT shortcut", symbol: "bolt.horizontal.circle", fill: true,
                                       id: Self.prefix + "fix.shortcut") { backend.runShortcut() }
        case .pairAgain:
            BCDSectionKit.ActionButton(title: "Pair again", symbol: "link", fill: true,
                                       id: Self.prefix + "fix.pair") { backend.startPairing() }
        case .importFile:
            BCDSectionKit.ActionButton(title: "Import a new pairing file", symbol: "doc.badge.plus", fill: true,
                                       id: Self.prefix + "fix.import") { importing = true }
        }
    }

    // MARK: Method

    private var methodCard: some View {
        BCDSectionKit.Card(eyebrow: "How JIT turns on", symbol: "switch.2") {
            let offered = BCDJITRules.offered(platform)
            ForEach(offered) { method in
                BCDSectionKit.ChoiceRow(title: title(method), detail: detail(method), symbol: symbol(method),
                                        selected: shownAsChosen(method, offered: offered),
                                        id: Self.prefix + "method." + method.rawValue) {
                    backend.choose(method)
                }
            }
            if !offered.contains(state.resolved) {
                BCDSectionKit.Note(text: state.method == .automatic
                                   ? "Automatic is chosen and would use \(title(state.resolved)), which can't work here. Choose \(title(offered[0]))."
                                   : "\(title(state.method)) is chosen, but it can't work here. Choose \(title(offered[0])).",
                                   symbol: "exclamationmark.triangle.fill", tone: .warn)
            }
            if let block = BCDJITRules.builtInBlock(platform) {
                BCDSectionKit.Note(text: blockText(block), symbol: "info.circle")
            } else if platform.inLiveContainer {
                BCDSectionKit.Note(text: BCDJITRules.lcHelperText(.ready), symbol: "checkmark.circle")
            }
            if state.setupAvailable {
                BCDSectionKit.ActionButton(title: "Run setup again", symbol: "wand.and.stars", kind: .quiet,
                                           id: Self.prefix + "setup") { backend.runSetup() }
            }
        }
    }

    /// The chosen method; when that one isn't offered here (Automatic on iOS 18, say), the
    /// offered method it comes down to.
    private func shownAsChosen(_ method: BCDJITMethodChoice, offered: [BCDJITMethodChoice]) -> Bool {
        offered.contains(state.method) ? state.method == method : state.resolved == method
    }

    private func title(_ method: BCDJITMethodChoice) -> String {
        switch method {
        case .automatic: return "Automatic"
        case .stikDebug: return "StikDebug"
        case .builtIn: return "Built-in"
        }
    }

    private func symbol(_ method: BCDJITMethodChoice) -> String {
        switch method {
        case .automatic: return "wand.and.stars"
        case .stikDebug: return "ant"
        case .builtIn: return "iphone.radiowaves.left.and.right"
        }
    }

    private func detail(_ method: BCDJITMethodChoice) -> String {
        switch method {
        case .automatic:
            return "BCD uses StikDebug when it's installed, otherwise its own helper. Right now: \(title(state.resolved))."
        case .stikDebug:
            if platform.inLiveContainer { return "The StikDebug app attaches to BCD. Inside LiveContainer BCD can't see whether it's installed." }
            return state.stikDebugFound ? "The StikDebug app attaches to BCD for a moment, then sends you back. Installed."
                                        : "The StikDebug app attaches to BCD for a moment. It isn't installed yet."
        case .builtIn:
            switch state.pairingFile {
            case .none: return "BCD's own helper, with no other app. Needs this \(device)'s pairing first."
            case .imported: return "BCD's own helper, with no other app. Pairing file added."
            case .pairedHere: return "BCD's own helper, with no other app. Paired on this \(device)."
            }
        }
    }

    private func blockText(_ block: BCDJITRules.BuiltInBlock) -> String {
        switch block {
        case .needsIOS26: return "BCD's built-in helper needs iOS 26 or newer, so StikDebug is the way on this \(device)."
        case .liveContainer: return BCDJITRules.lcHelperText(platform.lcHelper)
        case .simulator: return "BCD's built-in helper needs a real device."
        }
    }

    // MARK: Pairing (Built-in)

    private var pairingCard: some View {
        BCDSectionKit.Card(eyebrow: "Pairing", symbol: "link") {
            switch state.pairingFile {
            case .none:
                BCDSectionKit.Note(text: "Not paired yet. The built-in helper needs this \(device)'s pairing.", symbol: "link.badge.plus", tone: .warn)
            case .imported:
                BCDSectionKit.Note(text: "Pairing file added.", symbol: "checkmark.circle.fill", tone: .good)
            case .pairedHere:
                BCDSectionKit.Note(text: "Paired on this \(device).", symbol: "checkmark.circle.fill", tone: .good)
            }
            BCDJITPairingPanel(run: state.pairing, idPrefix: Self.prefix) { backend.stopPairing() }
            let pairingActive: Bool = {
                switch state.pairing {
                case .waiting, .code: return true
                default: return false
                }
            }()
            BCDSectionKit.Flow {
                if BCDJITRules.canPairOnDevice(platform) {
                    BCDSectionKit.ActionButton(title: state.pairingFile == .pairedHere ? "Pair again" : "Pair this \(device)",
                                               symbol: "iphone.radiowaves.left.and.right", kind: state.pairingFile == .none ? .primary : .outline,
                                               id: Self.prefix + "pair", enabled: !pairingActive) { backend.startPairing() }
                }
                BCDSectionKit.ActionButton(title: state.pairingFile == .imported ? "Choose another file" : "Import a pairing file",
                                           symbol: "doc.badge.plus",
                                           kind: state.pairingFile == .none && !BCDJITRules.canPairOnDevice(platform) ? .primary : .outline,
                                           id: Self.prefix + "import", enabled: !pairingActive) {
                    importError = nil
                    importing = true
                }
            }
            if let importError {
                BCDSectionKit.Note(text: importError, symbol: "exclamationmark.triangle.fill", tone: .bad)
            }
            BCDSectionKit.Flow(spacing: 4) {
                if state.resolved != .builtIn {
                    BCDSectionKit.ActionButton(title: "Enable JIT with it now", symbol: "bolt", kind: .quiet, id: Self.prefix + "builtin.enable",
                                               enabled: !state.busy && state.pairingFile != .none && state.now != .on) {
                        backend.enableWithBuiltIn()
                    }
                }
                BCDSectionKit.ActionButton(title: "Check setup", symbol: "checkmark.seal", kind: .quiet, id: Self.prefix + "check",
                                           enabled: !state.busy && state.pairingFile != .none) { backend.checkSetup() }
                BCDSectionKit.ActionButton(title: "Clear disk image", symbol: "trash", kind: .quiet, id: Self.prefix + "ddi",
                                           enabled: !state.busy) { backend.clearDiskImageCache() }
            }
            Text(state.resolved != .builtIn
                 ? "Enable JIT with it now uses the built-in helper this once, whatever method is chosen. Check setup tests the connection and gets the Developer Disk Image ready. Clear disk image makes BCD fetch that image again."
                 : "Check setup tests the connection and gets the Developer Disk Image ready. Clear disk image makes BCD fetch that image again.")
                .bcdFont(.body, 12).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            BCDSectionKit.Note(text: "BCD keeps the pairing in this \(device)'s Keychain.", symbol: "lock.fill")
        }
    }

    // MARK: LocalDevVPN

    private var vpnCard: some View {
        BCDSectionKit.Card(eyebrow: "LocalDevVPN", symbol: "network") {
            Text("StikDebug and the built-in helper reach this \(device) through LocalDevVPN, a free app from the App Store. Keep it connected while JIT turns on.")
                .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            switch state.vpn {
            case .notInstalled:
                BCDSectionKit.Note(text: "Not installed.", symbol: "xmark.circle", tone: .warn)
            case .installed:
                BCDSectionKit.Note(text: "Installed, not connected.", symbol: "circle.dashed", tone: .warn)
            case .connected:
                BCDSectionKit.Note(text: "Connected.", symbol: "checkmark.circle.fill", tone: .good)
            case .unknown:
                BCDSectionKit.Note(text: "Inside LiveContainer, BCD can't see whether it's installed.", symbol: "questionmark.circle")
            }
            switch state.probe {
            case .notRun: EmptyView()
            case .checking: BCDSectionKit.Note(text: "Checking the connection…", symbol: "hourglass")
            case .answered: BCDSectionKit.Note(text: "This \(device) answers through it.", symbol: "checkmark.circle.fill", tone: .good)
            case .noAnswer: BCDSectionKit.Note(text: "No answer through it yet. Connect it, then test again.", symbol: "exclamationmark.circle", tone: .warn)
            }
            BCDSectionKit.Flow {
                BCDSectionKit.ActionButton(title: state.vpn == .notInstalled ? "Get LocalDevVPN" : (state.vpn == .connected ? "Open LocalDevVPN" : "Connect"),
                                           symbol: state.vpn == .notInstalled ? "arrow.down.app" : "network",
                                           kind: state.vpn == .connected ? .outline : .primary, id: Self.prefix + "vpn") {
                    backend.openLocalDevVPN()
                }
                BCDSectionKit.ActionButton(title: "Test", symbol: "antenna.radiowaves.left.and.right", kind: .quiet,
                                           id: Self.prefix + "vpn.test", enabled: state.probe != .checking) { backend.testConnection() }
            }
        }
    }

    // MARK: The JIT shortcut (iOS 27)

    private var shortcutCard: some View {
        BCDSectionKit.Card(eyebrow: "JIT shortcut", symbol: "bolt.horizontal.circle") {
            BCDSectionKit.SwitchRow(title: "Use the JIT shortcut",
                                    detail: "When LocalDevVPN isn't reachable, Enable JIT runs the shortcut. It connects LocalDevVPN, turns Cellular Data off if there's no Wi-Fi, and puts both back once the game runs. Shortcuts shows for a moment each time.",
                                    isOn: state.shortcutOn, id: Self.prefix + "shortcut") { backend.setShortcut($0) }
            if !BCDJITRules.shortcutAvailable(platform) {
                BCDSectionKit.Note(text: platform.inLiveContainer
                                   ? "The shortcut can't hand back to BCD inside LiveContainer, so Enable JIT stops at it. Turn it off."
                                   : "The shortcut needs iOS 27 or newer, so on this \(device) Enable JIT stops at it. Turn it off.",
                                   symbol: "exclamationmark.triangle.fill", tone: .warn)
            } else {
                shortcutButtons
            }
        }
    }

    private var shortcutButtons: some View {
        Group {
            BCDSectionKit.Flow {
                BCDSectionKit.ActionButton(title: "Add the shortcut", symbol: "plus.square.on.square", id: Self.prefix + "shortcut.add") {
                    backend.addShortcut()
                }
                if state.shortcutCopy != nil {
                    BCDSectionKit.ActionButton(title: "Offline copy", symbol: "square.and.arrow.up", kind: .quiet,
                                               id: Self.prefix + "shortcut.share") { sharing = true }
                }
            }
            if state.shortcutCopy != nil {
                Text("No connection? Offline copy shares the shortcut that comes with BCD; pick Shortcuts in the list.")
                    .bcdFont(.body, 12).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: Guides

    private var guideCard: some View {
        let offered = guides
        let shown = guide.flatMap { offered.contains($0) ? $0 : nil } ?? defaultGuide(offered)
        return BCDSectionKit.Card(eyebrow: "Guides", symbol: "book") {
            if offered.count > 1 {
                BCDSectionKit.Segments(options: offered.map { (value: $0, title: guideTitle($0)) }, selected: shown,
                                       idPrefix: Self.prefix + "guide.") { guide = $0 }
            }
            Text(guideTitle(shown, long: true)).bcdFont(.heading, 17)
            BCDSectionKit.Steps(steps: steps(shown))
            BCDSectionKit.Flow {
                if shown == .stikDebug {
                    BCDSectionKit.ActionButton(title: "Get StikDebug", symbol: "arrow.down.circle", kind: .outline,
                                               id: Self.prefix + "guide.get") { openURL(BCDJITLinks.stikDebug) }
                }
                if shown != .pairHere {
                    BCDSectionKit.ActionButton(title: "Pairing-file guide", symbol: "doc.text", kind: .quiet,
                                               id: Self.prefix + "guide.pairing") { openURL(BCDJITLinks.pairingFileGuide) }
                }
            }
            if shown == .stikDebug {
                BCDSectionKit.Note(text: "Always turn JIT on with Enable JIT in BCD, not from StikDebug's own list: only then does StikDebug bring BCD's script.",
                                   symbol: "exclamationmark.circle", tone: .info)
            }
        }
    }

    /// The guides worth showing: StikDebug everywhere; the built-in helper's where it runs.
    private var guides: [BCDJITGuide] {
        var list: [BCDJITGuide] = []
        if BCDJITRules.builtInBlock(platform) == nil {
            if BCDJITRules.canPairOnDevice(platform) { list.append(.pairHere) }
            list.append(.pairingFile)
        }
        list.append(.stikDebug)
        return list
    }

    /// The guide for the method Enable JIT uses now.
    private func defaultGuide(_ offered: [BCDJITGuide]) -> BCDJITGuide {
        guard state.resolved == .builtIn else { return .stikDebug }
        if offered.contains(.pairHere), state.pairingFile != .imported { return .pairHere }
        return offered.contains(.pairingFile) ? .pairingFile : .stikDebug
    }

    private func guideTitle(_ guide: BCDJITGuide, long: Bool = false) -> String {
        switch guide {
        case .pairHere: return long ? "Built-in, paired on this \(device)" : "Pair here"
        case .pairingFile: return long ? "Built-in, with a pairing file" : "Pairing file"
        case .stikDebug: return "StikDebug"
        }
    }

    private func steps(_ guide: BCDJITGuide) -> [BCDSectionKit.Step] {
        let vpnDone = state.vpn == .connected
        switch guide {
        case .pairHere:
            return [
                .init(id: 1, text: "Tap **Pair this \(device)** above and let BCD use the local network.", done: state.pairingFile == .pairedHere),
                .init(id: 2, text: "In **Settings › Privacy & Security › Developer Mode**, scroll down and tap **Pair with BCD**.",
                      done: state.pairingFile == .pairedHere),
                .init(id: 3, text: "Type the code BCD shows. It's also in a notification and at the top of the screen.",
                      done: state.pairingFile == .pairedHere),
                .init(id: 4, text: "Connect **LocalDevVPN**, or switch on the JIT shortcut, then tap **Enable JIT**.", done: vpnDone),
            ]
        case .pairingFile:
            return [
                .init(id: 1, text: "Make this \(device)'s pairing file on a computer, as the pairing-file guide shows.",
                      done: state.pairingFile != .none),
                .init(id: 2, text: "Send it to this \(device) with AirDrop or Files, then tap **Import a pairing file** above.",
                      done: state.pairingFile != .none),
                .init(id: 3, text: "Connect **LocalDevVPN**.", done: vpnDone),
                .init(id: 4, text: "Tap **Enable JIT**. BCD's own helper attaches; no other app opens."),
            ]
        case .stikDebug:
            var list: [BCDSectionKit.Step] = [
                .init(id: 1, text: "Install **StikDebug** and add this \(device)'s pairing file to it.", done: state.stikDebugFound),
                .init(id: 2, text: "Install **LocalDevVPN** from the App Store and connect it.", done: vpnDone),
            ]
            if BCDJITRules.needsAlwaysRunScripts(platform) {
                list.append(.init(id: 3, text: "In StikDebug, open **Settings** and switch on **Always Run Scripts**. On this \(device) StikDebug runs BCD's script only with it on, and without the script games can't start."))
            }
            list.append(.init(id: 4, text: "Come back to BCD and tap **Enable JIT**. StikDebug opens (allow the request if it asks), attaches with BCD's script and sends you back."))
            return list
        }
    }
}

// MARK: - Pairing on the device, while it runs

/// Pairing on the device itself (iOS 27) while it runs: the steps while BCD waits for
/// Settings, the code to type, the outcome; nothing when no pairing runs. Settings › JIT shows
/// it, and BCD's setup can too (BCDJITLive.pairingRun() reads the app's pairing).
struct BCDJITPairingPanel: View {
    let run: BCDJITPairingRun
    /// Its Stop button's focus ID is this plus "pair.stop".
    var idPrefix = "settings.jit."
    let stop: () -> Void

    var body: some View {
        switch run {
        case .idle:
            EmptyView()
        case .waiting(let shortBackground):
            VStack(alignment: .leading, spacing: 10) {
                BCDSectionKit.Steps(steps: [
                    .init(id: 1, text: "Open **Settings › Privacy & Security › Developer Mode**."),
                    .init(id: 2, text: "Scroll to the bottom and tap **Pair with BCD**. Keep Wi-Fi on."),
                    .init(id: 3, text: "Come back here for the code."),
                ])
                if shortBackground {
                    BCDSectionKit.Note(text: "This copy of BCD gets only about 30 seconds in the background, so go to Settings right away.",
                                       symbol: "timer", tone: .warn)
                }
                stopButton
            }
        case .code(let pin):
            VStack(alignment: .leading, spacing: 8) {
                Text("Type this code in Settings when it asks for it:").bcdFont(.body, 15)
                Text(pin).font(.system(size: 38, weight: .bold, design: .monospaced)).tracking(6)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 8)
                    .background(BCDStyle.ground.opacity(0.7), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .accessibilityLabel("Code: \(pin.map(String.init).joined(separator: " "))")
                stopButton
            }
        case .paired(let name):
            BCDSectionKit.Note(text: "Pairing complete for \(name).", symbol: "checkmark.circle.fill", tone: .good)
        case .failed(let message):
            BCDSectionKit.Note(text: message, symbol: "exclamationmark.triangle.fill", tone: .bad)
        }
    }

    private var stopButton: some View {
        BCDSectionKit.ActionButton(title: "Stop pairing", symbol: "xmark", kind: .quiet, id: idPrefix + "pair.stop", action: stop)
    }
}
