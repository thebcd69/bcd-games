// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation
import Security
import SwiftUI
import UniformTypeIdentifiers

enum JITMethod: String, CaseIterable, Identifiable {
    case automatic
    case stikDebug
    case builtIn

    var id: String { rawValue }
    var title: String {
        switch self {
        case .automatic: return "Automatic"
        case .stikDebug: return "StikDebug"
        case .builtIn: return "Built-in StikJIT"
        }
    }
}

/// Where Built-in StikJIT's pairing file came from.
enum JITPairingSource: String {
    /// Made by Madeira on this device (iOS 27, OnDevicePairing).
    case onDevice
    /// Imported from a file made on a computer.
    case imported
}

/// This iPhone's remote pairing file for Built-in StikJIT. It is a credential
/// for the device itself, so it lives in the Keychain (this device only, while
/// unlocked), as the Steam sign-in token does, and never in Documents, where
/// the Files app and every Windows program in Madeira could read it. Its bytes
/// go only to the bundled helper, over XPC, for one request. A copy an earlier
/// build left at Documents/StikJIT/pairingFile.plist moves into the Keychain
/// and is deleted.
enum JITPairingFileStore {
    private static let service = "MadeiraJITPairing"
    private static let account = "rppairing"
    private static let sourceKey = "madeiraJITPairingSource"

    static var isImported: Bool { (try? data()) != nil }

    /// Files stored before on-device pairing existed were all imported.
    static var source: JITPairingSource? {
        guard isImported else { return nil }
        return UserDefaults.standard.string(forKey: sourceKey).flatMap(JITPairingSource.init(rawValue:)) ?? .imported
    }

    static func data() throws -> Data {
        moveLegacyFile()
        var result: AnyObject?
        let status = SecItemCopyMatching([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ] as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data, !data.isEmpty else {
            throw NSError(domain: "MadeiraJIT", code: 11,
                          userInfo: [NSLocalizedDescriptionKey: "This device has no saved pairing file."])
        }
        return data
    }

    static func importFile(from source: URL) throws {
        let scoped = source.startAccessingSecurityScopedResource()
        defer { if scoped { source.stopAccessingSecurityScopedResource() } }
        try store(try Data(contentsOf: source), source: .imported)
    }

    /// Validates an RPPairing plist and makes it the pairing file.
    static func store(_ data: Data, source: JITPairingSource) throws {
        guard !data.isEmpty,
              let plist = try? PropertyListSerialization.propertyList(from: data, options: [], format: nil),
              let dictionary = plist as? [String: Any],
              let publicKey = dictionary["public_key"] as? Data, publicKey.count == 32,
              let privateKey = dictionary["private_key"] as? Data, privateKey.count == 32,
              let identifier = dictionary["identifier"] as? String, !identifier.isEmpty else {
            throw NSError(domain: "MadeiraJIT", code: 10,
                          userInfo: [NSLocalizedDescriptionKey:
                            "This file isn't a StikDebug remote pairing file. Make one with iloader and import it instead."])
        }
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        SecItemDelete(item as CFDictionary)
        var add = item
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw NSError(domain: "MadeiraJIT", code: 12,
                          userInfo: [NSLocalizedDescriptionKey:
                            "BCD couldn't save the pairing file to the Keychain (error \(status)). Make sure this device is unlocked, then try again."])
        }
        UserDefaults.standard.set(source.rawValue, forKey: sourceKey)
    }

    /// Documents/StikJIT/pairingFile.plist, where builds before the Keychain kept it.
    private static var legacyFolder: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("StikJIT", isDirectory: true)
    }

    private static func moveLegacyFile() {
        let manager = FileManager.default
        let file = legacyFolder.appendingPathComponent("pairingFile.plist")
        guard manager.fileExists(atPath: file.path), let data = try? Data(contentsOf: file) else { return }
        let kept = UserDefaults.standard.string(forKey: sourceKey).flatMap(JITPairingSource.init(rawValue:)) ?? .imported
        // Deleted only once the Keychain holds it (a locked device keeps it for later).
        if (try? store(data, source: kept)) != nil {
            try? manager.removeItem(at: file)
            if (try? manager.contentsOfDirectory(atPath: legacyFolder.path))?.isEmpty == true {
                try? manager.removeItem(at: legacyFolder)
            }
            LogStore.shared.log("[jit] pairing file moved from Documents into the Keychain")
        }
    }
}

@MainActor
final class JITCoordinator: ObservableObject {
    static let shared = JITCoordinator()

    enum CoordinatorError: LocalizedError {
        case setupRequired(String)
        case pairingMissing
        case scriptMissing

        var errorDescription: String? {
            switch self {
            case .setupRequired(let message): return message
            case .pairingMissing: return "This device isn't paired yet. Pair it, or import its pairing file."
            case .scriptMissing: return "This copy of BCD is missing its JIT script. Install BCD again to restore it."
            }
        }
    }

    /// Why the built-in helper could not reach this device, read from its error.
    enum ConnectionProblem: Equatable {
        /// Nothing answered at LocalDevVPN's address: the VPN is off or not routing.
        case vpn
        /// The device closed the connection, usually because it no longer accepts this
        /// pairing (every new pairing replaces the last).
        case pairing

        init?(helperMessage: String) {
            let text = helperMessage.lowercased()
            let has = { (needles: [String]) in needles.contains(where: text.contains) }
            if has(["connectionreset", "connection reset", "device refused connection"]) {
                self = .pairing
            } else if has(["connectionrefused", "connection refused", "timedout", "timed out",
                           "networkunreachable", "network unreachable", "hostunreachable", "host unreachable", "no route"]) {
                self = .vpn
            } else {
                return nil
            }
        }

        var message: String {
            switch self {
            case .vpn:
                return "There's no connection to this device. Connect LocalDevVPN and try again."
            case .pairing:
                return "This device dropped the connection. Most often that means it has stopped accepting BCD's pairing. Pair again, and make sure LocalDevVPN is connected."
            }
        }
    }

    @Published var method: JITMethod {
        didSet { UserDefaults.standard.set(method.rawValue, forKey: "madeiraJITMethod") }
    }
    @Published private(set) var connectionProblem: ConnectionProblem?
    /// What the last loopback check found (nil: none ran). A JIT failure after it found no
    /// lockdownd is a LocalDevVPN problem whatever the helper's message says: a network
    /// that accepts any connection makes the helper's read end early ("early eof").
    private var loopbackAnswered: Bool?
    @Published var showSetup = false
    @Published private(set) var busy = false
    @Published private(set) var status: String?
    @Published private(set) var error: String?
    @Published private(set) var txmPresent: Bool?
    @Published private(set) var pairingImported = JITPairingFileStore.isImported
    @Published private(set) var pairingSource = JITPairingFileStore.source

    private init() {
        method = UserDefaults.standard.string(forKey: "madeiraJITMethod")
            .flatMap(JITMethod.init(rawValue:)) ?? .automatic
    }

    /// Round 660 (M138, owner 2026-11-15): with StikDebug installed, Automatic still takes BCD's own helper when this device is paired with
    /// it and the helper runs here. In testers' logs 8 of 109 StikDebug pool requests were never answered (7.3 %, six of them on a fresh
    /// attach) and none of 248 built-in ones; both go through the same LocalDevVPN. env.MADEIRA_JIT_AUTO_BUILTIN = 0 restores the order
    /// before (StikDebug whenever it is installed).
    static var automaticPrefersBuiltIn: Bool { MadeiraConfig.flag("MADEIRA_JIT_AUTO_BUILTIN") }

    var resolvedMethod: JITMethod {
        guard method == .automatic else { return method }
        guard StikJITHelper.isAvailable else { return .builtIn }
        return automaticTakesBuiltIn ? .builtIn : .stikDebug
    }

    /// Automatic with StikDebug installed takes the built-in helper (round 660, M138).
    var automaticTakesBuiltIn: Bool {
        Self.automaticPrefersBuiltIn && pairingImported && MadeiraBuiltInJIT.isAvailable
    }

    /// The method that attached the debugger through BCD in this app run; nil when none did (StikDebug started BCD with JIT on, say).
    private(set) var attachedMethod: JITMethod?

    /// The method the pool requests of this start go to (their log fields and the caption rule): the one that attached through BCD, else
    /// the order before round 660, since an attach BCD did not make is StikDebug's whenever it is installed.
    var requestMethod: JITMethod {
        if let attachedMethod { return attachedMethod }
        if method != .automatic { return method }
        return StikJITHelper.isAvailable ? .stikDebug : .builtIn
    }

    var automaticDescription: String {
        if StikJITHelper.isAvailable {
            return automaticTakesBuiltIn
                ? "BCD found StikDebug, but this device is paired with BCD, so it will enable JIT with the helper it ships with."
                : "BCD found StikDebug and will open it to enable JIT."
        }
        return "BCD didn't find StikDebug and will enable JIT with the helper it ships with."
    }

    func refreshPairingStatus() {
        pairingImported = JITPairingFileStore.isImported
        pairingSource = JITPairingFileStore.source
    }

    func enable(completion: @escaping (Result<Void, Error>) -> Void) {
        guard SigningStatus.current.debuggable else {
            completion(.failure(NSError(
                domain: "MadeiraJIT", code: 11,
                userInfo: [NSLocalizedDescriptionKey: SigningStatus.notDebuggableMessage])))
            return
        }
        if StikJITHelper.ready {
            completion(.success(()))
            return
        }
        error = nil
        status = nil
        connectionProblem = nil
        ensureLoopback(then: { [weak self] restoreOnFailure in
            self?.enableResolved { result in
                // Nothing will hold the network open now: put back what the shortcut changed.
                if restoreOnFailure, case .failure = result {
                    JITNetworkShortcut.shared.restoreIfNeeded {}
                }
                completion(result)
            }
        }, stopped: { [weak self] message in
            // The shortcut itself failed: say so (no connect action, which would only run
            // it again), skip a JIT attempt that cannot reach the device, and put back
            // whatever it changed before it stopped.
            self?.status = nil
            self?.error = message
            JITNetworkShortcut.shared.restoreIfNeeded {}
            completion(.failure(NSError(domain: "MadeiraJIT", code: 14,
                                        userInfo: [NSLocalizedDescriptionKey: message])))
        })
    }

    /// LocalDevVPN's loopback first (milliseconds when it already works). When it does
    /// not answer and the Madeira JIT shortcut is on, the shortcut turns Cellular Data
    /// off without Wi-Fi and connects LocalDevVPN, and the loopback is checked again
    /// while the VPN settles. Either way JIT is then attempted, so a failure still gets
    /// the usual explanation. `proceed`'s argument: the shortcut ran.
    private func ensureLoopback(then proceed: @escaping (Bool) -> Void, stopped: @escaping (String) -> Void) {
        let vpnWasUp = LoopbackProbe.vpnInterfaceUp
        LoopbackProbe.check { [weak self] probe in
            LogStore.shared.log(String(format: "[jit-loopback] %@ in %.0f ms (vpn-interface=%d, %@)",
                                       probe.reachable ? "reachable" : "unreachable", probe.milliseconds,
                                       vpnWasUp ? 1 : 0, probe.detail))
            self?.loopbackAnswered = probe.reachable
            guard let self, !probe.reachable, JITNetworkShortcut.shared.enabled else { proceed(false); return }
            status = "Starting the JIT shortcut…"
            JITNetworkShortcut.shared.start { [weak self] outcome in
                if case .failed(let why) = outcome {
                    stopped("The JIT shortcut stopped before it finished (\(why)). Review its steps in the Shortcuts app and try again.")
                    return
                }
                // LocalDevVPN's Connect returns before its tunnel routes (about 5 s
                // on the 18 Pro): go on the moment lockdownd answers.
                self?.status = "LocalDevVPN is connecting…"
                let waitStart = CFAbsoluteTimeGetCurrent()
                LoopbackProbe.waitUntilReachable(within: 15) { [weak self] probe in
                    LogStore.shared.log(String(format: "[jit-loopback] after the shortcut: %@ after %.1f s (vpn-interface=%d, %@)",
                                               probe.reachable ? "reachable" : "unreachable",
                                               CFAbsoluteTimeGetCurrent() - waitStart,
                                               LoopbackProbe.vpnInterfaceUp ? 1 : 0, probe.detail))
                    self?.loopbackAnswered = probe.reachable
                    proceed(true)
                }
            }
        }
    }

    /// JIT setup's connect action with the Madeira JIT shortcut on: the shortcut connects
    /// LocalDevVPN (turning Cellular Data off without Wi-Fi); then the loopback is checked.
    func connectWithShortcut() {
        busy = true
        error = nil
        status = "Starting the JIT shortcut…"
        JITNetworkShortcut.shared.start { [weak self] outcome in
            if case .failed(let why) = outcome {
                self?.busy = false
                self?.status = nil
                self?.error = "Couldn't run the JIT shortcut (\(why))."
                return
            }
            LoopbackProbe.waitUntilReachable(within: 15) { [weak self] probe in
                self?.busy = false
                if probe.reachable {
                    self?.connectionProblem = nil
                    self?.status = "This device answers through LocalDevVPN."
                } else {
                    self?.status = nil
                    self?.error = ConnectionProblem.vpn.message
                }
            }
        }
    }

    private func enableResolved(_ completion: @escaping (Result<Void, Error>) -> Void) {
        switch resolvedMethod {
        case .automatic:
            assertionFailure("Automatic must resolve to a concrete JIT method")
        case .stikDebug:
            enableStikDebug(completion: completion)
        case .builtIn:
            // Round 660 (M138): when Automatic took the helper over an installed StikDebug, a pairing this device no longer accepts (or
            // one that is gone) falls back to StikDebug in the same attempt, with its own pairing; a connection problem does not, since
            // StikDebug goes through the same LocalDevVPN.
            let stikDebugBehind = method == .automatic && StikJITHelper.isAvailable
            guard JITPairingFileStore.isImported else {
                if stikDebugBehind { fallBackToStikDebug("the pairing file is gone", completion: completion); return }
                let message = "Built-in StikJIT still needs a pairing. Pair this device, or import its pairing file."
                error = message
                showSetup = true
                completion(.failure(CoordinatorError.setupRequired(message)))
                return
            }
            enableBuiltIn { [weak self] result in
                guard let self, stikDebugBehind, case .failure(let failure) = result, pairingRefused(failure) else {
                    completion(result)
                    return
                }
                fallBackToStikDebug(failure.localizedDescription, completion: completion)
            }
        }
    }

    private func enableStikDebug(completion: @escaping (Result<Void, Error>) -> Void) {
        guard StikJITHelper.isAvailable else {
            let message = "BCD can't find StikDebug. Install it, or switch the method to Built-in StikJIT."
            error = message
            showSetup = true
            completion(.failure(CoordinatorError.setupRequired(message)))
            return
        }
        busy = true
        status = "StikDebug is attaching…"
        StikJITHelper.enableJIT { [weak self] result in
            Task { @MainActor in
                self?.busy = false
                self?.status = (try? result.get()).map { _ in "StikDebug has attached." }
                if case .failure(let failure) = result { self?.error = failure.localizedDescription }
                if case .success = result { self?.attachedMethod = .stikDebug }
                completion(result)
            }
        }
    }

    /// The built-in helper's failure was this device refusing (or missing) BCD's pairing, not the connection.
    private func pairingRefused(_ failure: Error) -> Bool {
        if case CoordinatorError.pairingMissing = failure { return true }
        return connectionProblem == .pairing
    }

    private func fallBackToStikDebug(_ why: String, completion: @escaping (Result<Void, Error>) -> Void) {
        LogStore.shared.log("[jit] Automatic: the built-in helper could not use this device's pairing (\(why)); StikDebug attaches instead")
        error = nil
        connectionProblem = nil
        enableStikDebug(completion: completion)
    }

    func importPairingFile(_ source: URL) {
        do {
            try JITPairingFileStore.importFile(from: source)
            OnDevicePairing.shared.cancel()
            refreshPairingStatus()
            status = "Pairing file saved."
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// OnDevicePairing finished: its file becomes the pairing file and Built-in StikJIT the method.
    func storeOnDevicePairing(_ data: Data) throws {
        try JITPairingFileStore.store(data, source: .onDevice)
        refreshPairingStatus()
        method = .builtIn
        status = "This device is now paired with BCD."
        error = nil
    }

    func prepareBuiltIn() {
        guard SigningStatus.current.debuggable else {
            error = SigningStatus.notDebuggableMessage
            return
        }
        guard MadeiraBuiltInJIT.isAvailable else {
            error = MadeiraBuiltInJIT.unavailableReason
            return
        }
        guard let pairing = try? JITPairingFileStore.data() else {
            error = CoordinatorError.pairingMissing.localizedDescription
            return
        }
        busy = true
        error = nil
        connectionProblem = nil
        status = "Testing LocalDevVPN and preparing the Developer Disk Image…"
        MadeiraBuiltInJIT.send(.prepare(pairingData: pairing)) { [weak self] result in
            guard let self else { return }
            busy = false
            switch result {
            case .success(let response):
                txmPresent = response.txmPresent
                status = response.success ? response.message : nil
                error = response.success ? nil : helperFailure(response.message)
            case .failure(let failure):
                error = failure.localizedDescription
            }
        }
    }

    func enableBuiltIn(completion: @escaping (Result<Void, Error>) -> Void = { _ in }) {
        guard MadeiraBuiltInJIT.isAvailable else {
            let failure = NSError(
                domain: "MadeiraJIT", code: 12,
                userInfo: [NSLocalizedDescriptionKey:
                    MadeiraBuiltInJIT.unavailableReason ?? "Built-in JIT can't be used on this device."])
            error = failure.localizedDescription
            completion(.failure(failure))
            return
        }
        guard let pairing = try? JITPairingFileStore.data() else {
            error = CoordinatorError.pairingMissing.localizedDescription
            completion(.failure(CoordinatorError.pairingMissing))
            return
        }
        guard let script = StikJITHelper.scriptData else {
            error = CoordinatorError.scriptMissing.localizedDescription
            completion(.failure(CoordinatorError.scriptMissing))
            return
        }

        busy = true
        error = nil
        status = "BCD's JIT helper is starting…"
        var readinessTimer: Timer?
        var readinessFinished = false
        MadeiraBuiltInJIT.send(
            .enable(targetPID: getpid(), pairingData: pairing,
                    scriptBase64: script.base64EncodedString()),
            started: { [weak self] in
                self?.status = "The JIT helper is attaching to BCD…"
                readinessTimer = StikJITHelper.waitForDebugger { [weak self] result in
                    guard !readinessFinished else { return }
                    readinessFinished = true
                    self?.busy = false
                    switch result {
                    case .success:
                        self?.status = "The built-in helper has attached."
                        self?.error = nil
                        self?.attachedMethod = .builtIn
                    case .failure(let failure):
                        self?.error = failure.localizedDescription
                    }
                    completion(result)
                }
            },
            completion: { [weak self] result in
                switch result {
                case .success(let response):
                    self?.txmPresent = response.txmPresent
                    LogStore.shared.log("[jit-built-in] \(response.message)",
                                        level: response.success ? .success : .error)
                    if !response.success && !readinessFinished, let self {
                        readinessFinished = true
                        readinessTimer?.invalidate()
                        busy = false
                        let message = helperFailure(response.message)
                        error = message
                        completion(.failure(NSError(
                            domain: "MadeiraJIT", code: 13,
                            userInfo: [NSLocalizedDescriptionKey: message])))
                    }
                case .failure(let failure):
                    if !readinessFinished {
                        readinessFinished = true
                        readinessTimer?.invalidate()
                        self?.busy = false
                        self?.error = failure.localizedDescription
                        completion(.failure(failure))
                    }
                }
            })
    }

    /// The helper's failure as shown: a connection problem gets its plain explanation
    /// (the helper's own message is already in the log).
    private func helperFailure(_ message: String) -> String {
        connectionProblem = ConnectionProblem(helperMessage: message) ?? (loopbackAnswered == false ? .vpn : nil)
        return connectionProblem?.message ?? message
    }

    func resetDDI() {
        busy = true
        error = nil
        status = "Clearing the cached Developer Disk Image…"
        MadeiraBuiltInJIT.send(.resetDDI) { [weak self] result in
            self?.busy = false
            switch result {
            case .success(let response):
                self?.status = response.success ? response.message : nil
                self?.error = response.success ? nil : response.message
            case .failure(let failure):
                self?.error = failure.localizedDescription
            }
        }
    }
}

/// LocalDevVPN, which built-in JIT and StikDebug reach the device through: open it to
/// connect when it is installed, otherwise its App Store page.
enum LocalDevVPN {
    static let appStore = URL(string: "https://apps.apple.com/us/app/localdevvpn/id6755608044")!
    /// `enable` connects the VPN; `scheme` has LocalDevVPN return to Madeira a second later.
    static let connect = URL(string: "localdevvpn://enable?scheme=bcd")!

    static var isInstalled: Bool { UIApplication.shared.canOpenURL(URL(string: "localdevvpn://")!) }
    static var actionTitle: String { isInstalled ? "Connect LocalDevVPN" : "Get LocalDevVPN" }

    static func open() {
        let installed = isInstalled
        LogStore.shared.log("[jit] LocalDevVPN \(installed ? "connect" : "app-store")")
        UIApplication.shared.open(installed ? connect : appStore)
    }
}

/// The fix a JIT connection problem offers: pair again (rejected pairing) and LocalDevVPN.
/// With the Madeira JIT shortcut on, LocalDevVPN is connected by the shortcut, never by
/// its link: `retry` enables JIT again (which runs the shortcut when the loopback does not
/// answer); without it the shortcut runs on its own.
@MainActor func jitConnectionActions(_ problem: JITCoordinator.ConnectionProblem,
                                     retry: (() -> Void)? = nil,
                                     then dismiss: @escaping () -> Void = {}) -> some View {
    Group {
        if problem == .pairing {
            Button(OnDevicePairing.isSupported ? "Pair Again" : "Open JIT Setup") {
                dismiss()
                if OnDevicePairing.isSupported { OnDevicePairing.shared.start() }
                JITCoordinator.shared.showSetup = true
            }
        }
        if JITNetworkShortcut.shared.enabled {
            Button("Run the JIT shortcut") {
                dismiss()
                if let retry { retry() } else { JITCoordinator.shared.connectWithShortcut() }
            }
        } else {
            Button(LocalDevVPN.actionTitle) { dismiss(); LocalDevVPN.open() }
        }
    }
}
