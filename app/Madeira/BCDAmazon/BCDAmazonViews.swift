// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// The experimental Amazon Games store in BCD's interface (Porthole port, item 13): its card in Settings › Accounts, with the
// switch that turns it on. Engine-bound (it reads AmazonLibrary), and outside app/Madeira's top level, so the preview build never compiles it.
// Built like the EA card beside it (BCDEA/BCDEAViews.swift). Amazon's sign-in is Amazon's own login page in BCD's sign-in sheet
// (BCDLiveStoreAmazon.signInToAmazon), not a form of ours.

/// The switch and the sign-out.
@MainActor enum BCDAmazonActions {
    /// Turns the experimental store on or off (`env.BCD_AMAZON` in bcd.cfg; removed when off). Off, nothing of Amazon is read, fetched, written
    /// or listened to; installed games and the sign-in stay.
    static func setEnabled(_ on: Bool) {
        MadeiraConfig.set("env.BCD_AMAZON", on ? "1" : nil)
        if on {
            LogStore.shared.log("[amazon] switched on")
        } else {
            AmazonLibrary.shared.switchedOff()
            LogStore.shared.log("[amazon] switched off")
        }
        BCDAmazonSignal.shared.changed()
        BCDStoresUI.shared.refresh()
    }

    static func signOut() {
        AmazonLibrary.shared.signOut()
        BCDAmazonSignal.shared.changed()
        BCDStoresUI.shared.refresh()
    }
}

struct BCDAmazonCard: View {
    @ObservedObject private var ui = BCDStoresUI.shared
    private static var prefix: String { "settings.accounts.amazon." }

    var body: some View {
        BCDSectionKit.Card(eyebrow: "Amazon Games (experimental)", symbol: "flask") {
            BCDSectionKit.SwitchRow(
                title: "Use Amazon Games in BCD",
                detail: AmazonLibrary.enabled
                    ? "On. Your Amazon games are in the Library, under Amazon Games."
                    : "Off. BCD does not contact Amazon.",
                isOn: AmazonLibrary.enabled, id: Self.prefix + "switch") { BCDAmazonActions.setEnabled($0) }
            if AmazonLibrary.enabled {
                BCDAmazonAccount(amazon: AmazonLibrary.shared)
            }
            BCDSectionKit.Note(
                text: "Experimental. BCD signs in to your Amazon account as the Amazon Games app does, lists the games you bought or claimed with Prime Gaming, downloads them from Amazon's servers (every file is checked against Amazon's signed list) and starts the program each game names, without the Amazon Games app. Amazon does not make or support this, and a game that needs the Amazon Games app running may not start.",
                symbol: "flask")
        }
    }
}

/// The signed-in or signed-out part once the store is on.
private struct BCDAmazonAccount: View {
    @ObservedObject var amazon: AmazonLibrary
    @ObservedObject private var ui = BCDStoresUI.shared
    private static var prefix: String { "settings.accounts.amazon." }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if amazon.signedIn {
                HStack(spacing: 14) {
                    BCDSectionKit.StoreAvatar(name: amazon.accountName ?? "Amazon account")
                    VStack(alignment: .leading, spacing: 3) {
                        Text(amazon.accountName ?? "Amazon account").bcdFont(.heading, 19).lineLimit(1)
                        Text(amazon.refreshing ? "Amazon account · reading your games…" : "Amazon account · \(amazon.owned.count) games")
                            .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    BCDStatusChip(text: "Signed in", color: BCDStyle.mint)
                }
                .accessibilityElement(children: .combine)
                if ui.question == "amazon.signout" {
                    BCDSectionKit.Confirm(
                        title: "Sign out of Amazon Games?",
                        message: "BCD tells Amazon this device is gone, removes the sign-in from this \(BCDSectionKit.deviceWord)'s Keychain and forgets the list of your Amazon games. Installed games stay.",
                        choices: [
                            .init(title: "Sign out", kind: .danger, id: Self.prefix + "signout.confirm") {
                                _ = ui.close(focusing: Self.prefix + "signin")
                                BCDAmazonActions.signOut()
                            },
                            .init(title: "Cancel", kind: .outline, id: Self.prefix + "signout.cancel") {
                                _ = ui.close(focusing: Self.prefix + "signout")
                            },
                        ])
                } else {
                    BCDSectionKit.ActionButton(title: "Sign out", symbol: "rectangle.portrait.and.arrow.right", kind: .danger,
                                               id: Self.prefix + "signout") {
                        ui.remember(Self.prefix + "signout")
                        ui.ask("amazon.signout", focusing: Self.prefix + "signout.cancel")
                    }
                }
            } else {
                Text("Sign in to see the games your Amazon account owns, download them and play them in BCD.")
                    .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                BCDSectionKit.ActionButton(title: "Sign in to Amazon", symbol: "person.crop.circle.badge.plus", kind: .primary, fill: true,
                                           id: Self.prefix + "signin") { BCDLiveStore.shared.signIn(.amazon) }
                BCDSectionKit.Note(text: "You sign in on Amazon's own page: BCD never sees your password. If the page cannot finish inside BCD, tap Use Safari at the top of Amazon's sign-in page.",
                                   symbol: "safari")
            }
        }
        .onAppear { amazon.start() }
    }
}
