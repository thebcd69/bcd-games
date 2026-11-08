// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// The experimental EA store in BCD's interface (docs/EA.md): its card in Settings › Accounts, with the switch that turns it on. Engine-bound
// (it reads EALibrary), and outside app/Madeira's top level, so the preview build never compiles it. Built from BCDSectionKit like the Accounts
// cards beside it. EA's sign-in is EA's own login page in BCD's sign-in sheet (BCDLiveStoreEA.signInToEA), not a form of ours.

/// The switch and the sign-out.
@MainActor enum BCDEAActions {
    /// Turns the experimental store on or off (`env.BCD_EA` in bcd.cfg; removed when off). Off, nothing of EA is read, fetched, written or listened
    /// to (EALibrary.switchedOff stops what runs); installed games and the sign-in stay.
    static func setEnabled(_ on: Bool) {
        MadeiraConfig.set("env.BCD_EA", on ? "1" : nil)
        if on {
            EALog.event("[ea-library] switched on")
        } else {
            EALibrary.shared.switchedOff()
            EALog.event("[ea-library] switched off")
        }
        BCDEASignal.shared.changed()
        BCDStoresUI.shared.refresh()
    }

    static func signOut() {
        EALibrary.shared.signOut()
        BCDEASignal.shared.changed()
        BCDStoresUI.shared.refresh()
    }
}

struct BCDEACard: View {
    @ObservedObject private var ui = BCDStoresUI.shared
    private static var prefix: String { "settings.accounts.ea." }

    var body: some View {
        BCDSectionKit.Card(eyebrow: "EA games (experimental)", symbol: "flask") {
            BCDSectionKit.SwitchRow(
                title: "Use EA games in BCD",
                detail: EALibrary.enabled
                    ? "On. Your EA games are in the Library, under EA games."
                    : "Off. BCD does not contact EA.",
                isOn: EALibrary.enabled, id: Self.prefix + "switch") { BCDEAActions.setEnabled($0) }
            if EALibrary.enabled {
                BCDEAAccount(ea: EALibrary.shared)
            }
            BCDSectionKit.Note(
                text: "Experimental. BCD signs in to your EA account itself, downloads your EA games from EA's servers and starts them without the EA app, standing in for the EA app's local service while a game runs. EA does not make or support this, and it may not work for every game. Games that need EA's kernel anti-cheat (Battlefield 2042 and later, EA SPORTS FC, Madden) cannot run. Cloud saves, friends and the overlay are not there. The EA app itself, run through Wine, is separate: see the EA app in the Library.",
                symbol: "flask")
        }
    }
}

/// The signed-in or signed-out part once the store is on.
private struct BCDEAAccount: View {
    @ObservedObject var ea: EALibrary
    @ObservedObject private var ui = BCDStoresUI.shared
    private static var prefix: String { "settings.accounts.ea." }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if ea.signedIn {
                HStack(spacing: 14) {
                    BCDSectionKit.StoreAvatar(name: ea.accountName ?? "EA account")
                    VStack(alignment: .leading, spacing: 3) {
                        Text(ea.accountName ?? "EA account").bcdFont(.heading, 19).lineLimit(1)
                        Text("EA account").bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    BCDStatusChip(text: "Signed in", color: BCDStyle.mint)
                }
                .accessibilityElement(children: .combine)
                BCDSectionKit.Note(text: "Your EA games are in the Library. An EA game needs this sign-in to start, as its license and the EA app's settings come from the account.", symbol: "info.circle")
                if ui.question == "ea.signout" {
                    BCDSectionKit.Confirm(
                        title: "Sign out of EA?",
                        message: "BCD removes the EA sign-in from this \(BCDSectionKit.deviceWord)'s Keychain and forgets the list of your EA games. Installed games stay, but they need the sign-in to start.",
                        choices: [
                            .init(title: "Sign out", kind: .danger, id: Self.prefix + "signout.confirm") {
                                _ = ui.close(focusing: Self.prefix + "signin")
                                BCDEAActions.signOut()
                            },
                            .init(title: "Cancel", kind: .outline, id: Self.prefix + "signout.cancel") {
                                _ = ui.close(focusing: Self.prefix + "signout")
                            },
                        ])
                } else {
                    BCDSectionKit.ActionButton(title: "Sign out", symbol: "rectangle.portrait.and.arrow.right", kind: .danger,
                                               id: Self.prefix + "signout") {
                        ui.remember(Self.prefix + "signout")
                        ui.ask("ea.signout", focusing: Self.prefix + "signout.cancel")
                    }
                }
            } else {
                Text("Sign in to see the games your EA account owns, download them and play them in BCD.")
                    .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                BCDSectionKit.ActionButton(title: "Sign in to EA", symbol: "person.crop.circle.badge.plus", kind: .primary, fill: true,
                                           id: Self.prefix + "signin") { BCDLiveStore.shared.signIn(.ea) }
                BCDSectionKit.Note(text: "You sign in on EA's own page: BCD never sees your password. If the page cannot finish inside BCD, tap Use Safari at the top of EA's sign-in page.",
                                   symbol: "safari")
            }
        }
        .onAppear { ea.start() }
    }
}
