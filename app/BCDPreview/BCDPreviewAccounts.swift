// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import CoreImage
import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

/// Settings › Accounts on sample data, for the preview app. Variants (-BCDTab
/// settings-accounts-<variant>): none (Steam, Epic Games and GOG signed in), "out" (all signed out),
/// "signin", "qr", "guard", "error".
/// It never contacts Steam, and it ignores the password it is handed.
@MainActor final class BCDPreviewAccountsBackend: BCDAccountsBackend {
    @Published private(set) var steam: BCDSteamAccount
    @Published private(set) var signInProgress = BCDSteamSignIn()
    @Published private(set) var gogAccount: BCDAccount
    @Published private(set) var epicAccount: BCDAccount
    private var pendingName: String?

    init(variant: String) {
        let signedOut = ["out", "signin", "qr", "guard", "error"].contains(variant)
        steam = BCDSteamAccount(available: true, accountName: signedOut ? nil : "player", personaName: nil, avatar: nil,
                                cloudAvailable: true, cloudOn: true, setupAvailable: true, lanAvailable: true, lanOn: variant == "lan", lanHost: variant == "lan" ? "192.168.1.20" : "")
        gogAccount = BCDAccount(store: .gog, name: signedOut ? nil : "gogplayer", available: true)
        epicAccount = BCDAccount(store: .epic, name: signedOut ? nil : "epicplayer", available: true)
        switch variant {
        case "guard":
            signInProgress = BCDSteamSignIn(busy: false, steamGuard: BCDSteamGuard(code: .steamApp, canApprove: true))
        case "error":
            signInProgress = BCDSteamSignIn(error: "Steam didn't accept that account name and password. Check them and try again.")
        default:
            break
        }
    }

    /// Which page the sample opens on.
    static func page(_ variant: String) -> BCDAccountsPage {
        switch variant {
        case "signin", "guard", "error": return .signIn(qr: false)
        case "qr": return .signIn(qr: true)
        default: return .main
        }
    }

    func refresh() {}

    func signIn(account: String, password: String) {
        pendingName = account
        signInProgress = BCDSteamSignIn(busy: false, steamGuard: BCDSteamGuard(code: .email(hint: "example.com"), canApprove: true))
    }

    func submitGuardCode(_ code: String) {
        steam.accountName = pendingName ?? "player"
        signInProgress = BCDSteamSignIn()
    }

    func beginQR() {
        signInProgress = BCDSteamSignIn(busy: false, qrImage: Self.sampleCode, qrLink: URL(string: "https://example.com/bcd-preview"))
    }

    func cancelSignIn() { signInProgress = BCDSteamSignIn() }
    func clearSignInError() { signInProgress.error = nil }
    func signOut() { steam.accountName = nil }
    func setCloud(_ on: Bool) { steam.cloudOn = on }
    func setLAN(_ on: Bool) { steam.lanOn = on }
    func setLANHost(_ address: String) { steam.lanHost = address }
    func runSetup() {}
    func signInGOG() { gogAccount.name = "gogplayer" }
    func signOutGOG() { gogAccount.name = nil }
    func signInEpic() { epicAccount.name = "epicplayer" }
    func signOutEpic() { epicAccount.name = nil }

    /// A QR code that says only that it is a sample.
    static let sampleCode: UIImage? = {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data("BCD preview: a sample, not a sign-in".utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage,
              let image = CIContext(options: [.useSoftwareRenderer: true]).createCGImage(output, from: output.extent) else { return nil }
        return UIImage(cgImage: image)
    }()
}
