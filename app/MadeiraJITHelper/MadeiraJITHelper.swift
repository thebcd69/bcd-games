// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Portions copyright 2026 alantangok: code taken from https://github.com/willfaust/Madeira (pull request #541)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

// Madeira's JIT helper: a classic app extension that Madeira starts by its bundle ID
// (app/Madeira/JITBuiltInHost.swift), so a sideloader's renamed install still finds it.
// One MadeiraJITRequest per extension request, answered when the request completes.

import Foundation
#if !targetEnvironment(simulator)
import StikJIT
#endif

#if targetEnvironment(simulator)
private struct DDIPaths {
    static func `default`(in directory: URL) -> DDIPaths { DDIPaths() }
}

private enum StikJIT {
    enum Script { case customBase64(String) }
    struct DeviceSecurityState { let isTXMPresent: Bool? }
    enum PreparationStage { case unavailable }
    enum JITReadiness {
        case unreachable(reason: String)
        case preparationFailed(reason: String)
        case ready(DeviceSecurityState)
    }

    static var isTXMPresent: Bool? { nil }
    static func prepareDevice(pairingFile: URL, paths: DDIPaths,
                              progress: (PreparationStage) -> Void) -> JITReadiness {
        .unreachable(reason: "Built-in JIT works only on a real device, not in the simulator.")
    }
    static func enableJIT(targetPID: Int32, pairingFile: URL, ddiPaths: DDIPaths,
                          script: Script, forceScript: Bool,
                          preparationProgress: (PreparationStage) -> Void,
                          progress: (String) -> Void) throws {
        throw NSError(domain: "MadeiraJITHelper", code: 100,
                      userInfo: [NSLocalizedDescriptionKey:
                        "Built-in JIT works only on a real device, not in the simulator."])
    }
    static func resetCachedDDI(at paths: DDIPaths) throws {}
    static func isDDIMounted(pairingFile: URL) throws -> Bool { false }
}
#endif

private enum MadeiraJITWork {
    /// One request at a time, as the XPC handler ran them before.
    static let queue = DispatchQueue(label: "com.willfaust.madeora.jit-helper")

    /// Another preparation may install the cryptex after StikJIT's initial check
    /// ("system: cryptex already exists", com.apple.security.cryptex 17).
    /// Verify that it is now mounted, then retry the normal operation just once.
    static func retryInstalledCryptex<T>(verifyMounted: () throws -> Bool,
                                        operation: () throws -> T) throws -> T {
        do { return try operation() }
        catch {
            let message = error.localizedDescription.lowercased()
            guard message.contains("cryptexd("), message.contains("routine: \"install\""),
                  message.contains("system: cryptex already exists:"),
                  message.contains("com.apple.security.cryptex: 17)"),
                  (try? verifyMounted()) == true else { throw error }
            NSLog("[MadeiraJIT] DDI cryptex already installed and mounted; retrying once")
            return try operation()
        }
    }

    static func handle(_ request: MadeiraJITRequest) -> MadeiraJITRequest.Response {
        let manager = FileManager.default
        let root = manager.urls(for: .libraryDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("StikJIT", isDirectory: true)
        let paths = DDIPaths.default(in: root)

        do {
            try manager.createDirectory(at: root, withIntermediateDirectories: true)
            if request.operation == .resetDDI {
                try StikJIT.resetCachedDDI(at: paths)
                return .init(success: true,
                             message: "The cached Developer Disk Image was cleared.",
                             txmPresent: StikJIT.isTXMPresent)
            }

            guard let pairingData = request.pairingData, !pairingData.isEmpty else {
                throw NSError(domain: "MadeiraJITHelper", code: 1,
                              userInfo: [NSLocalizedDescriptionKey: "No pairing file came with the request."])
            }
            let pairingURL = root.appendingPathComponent("pairingFile-\(UUID().uuidString).plist")
            try pairingData.write(to: pairingURL, options: .atomic)
            defer { try? manager.removeItem(at: pairingURL) }

            switch request.operation {
            case .prepare:
                let readiness = try retryInstalledCryptex(verifyMounted: {
                    try StikJIT.isDDIMounted(pairingFile: pairingURL)
                }) {
                    let state = StikJIT.prepareDevice(
                        pairingFile: pairingURL, paths: paths,
                        progress: { NSLog("[MadeiraJIT] prepare: %@", String(describing: $0)) })
                    if case .preparationFailed(let reason) = state {
                        throw NSError(domain: "MadeiraJITHelper", code: 3,
                                      userInfo: [NSLocalizedDescriptionKey: reason])
                    }
                    return state
                }
                switch readiness {
                case .ready(let security):
                    return .init(success: true,
                                 message: "All set: this device answers through LocalDevVPN, and the Developer Disk Image is ready.",
                                 txmPresent: security.isTXMPresent)
                case .unreachable(let reason), .preparationFailed(let reason):
                    return .init(success: false, message: reason,
                                 txmPresent: StikJIT.isTXMPresent)
                @unknown default:
                    return .init(success: false,
                                 message: "StikJIT reported a preparation state BCD doesn't recognize.",
                                 txmPresent: StikJIT.isTXMPresent)
                }
            case .enable:
                guard let targetPID = request.targetPID,
                      let scriptBase64 = request.scriptBase64,
                      !scriptBase64.isEmpty else {
                    throw NSError(domain: "MadeiraJITHelper", code: 2,
                                  userInfo: [NSLocalizedDescriptionKey:
                                    "The request is missing the target process or BCD's JIT script."])
                }
                // madeira-bcd: no retry here (upstream #541 retries this call too). Enable attaches a debugger to BCD, and a retry
                // that ran while another debugger session was live would attach a second one (U1 counter-review F1). The app joins
                // overlapping Enables (JITBuiltInHost.swift, MadeiraBuiltInJIT.start); a duplicate install is reported as before.
                try StikJIT.enableJIT(
                    targetPID: targetPID,
                    pairingFile: pairingURL,
                    ddiPaths: paths,
                    script: .customBase64(scriptBase64),
                    forceScript: true,
                    preparationProgress: {
                        NSLog("[MadeiraJIT] prepare: %@", String(describing: $0))
                    },
                    progress: { NSLog("[MadeiraJIT] %@", $0) })
                return .init(success: true,
                             message: "BCD detached cleanly from its built-in JIT helper.",
                             txmPresent: StikJIT.isTXMPresent)
            case .resetDDI:
                preconditionFailure("Handled above")
            }
        } catch {
            return .init(success: false, message: error.localizedDescription,
                         txmPresent: StikJIT.isTXMPresent)
        }
    }
}

/// NSExtensionPrincipalClass (Info.plist). The request arrives as JSON in the first input
/// item; the response goes back as JSON in the item the request completes with. Enable
/// completes only when Madeira's script has detached, so the request, and this process,
/// last as long as the debugger does.
@objc(MadeiraJITHelperHandler)
final class MadeiraJITHelperHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        let info = (context.inputItems.first as? NSExtensionItem)?.userInfo
        let data = info?[MadeiraJITRequest.itemKey] as? Data
        MadeiraJITWork.queue.async {
            let response: MadeiraJITRequest.Response
            if let data, let request = try? JSONDecoder().decode(MadeiraJITRequest.self, from: data) {
                response = MadeiraJITWork.handle(request)
            } else {
                response = .init(success: false, message: "The JIT helper didn't get a request from BCD.",
                                 txmPresent: nil)
            }
            let item = NSExtensionItem()
            item.userInfo = [MadeiraJITRequest.Response.itemKey: (try? JSONEncoder().encode(response)) ?? Data()]
            context.completeRequest(returningItems: [item], completionHandler: nil)
        }
    }
}
