// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  AutoTuneViews.swift
//  Madeira
//
//  madeira-bcd: auto-tune's views (docs/AUTO-TUNE.md): the switch in
//  Settings and the section on a game's details page. The page shows its section
//  only while Settings › Auto-tune is on for this run (AutoTune.enabled).
//

import SwiftUI

/// Auto-tune's result after a tuned session, after the exit report and any restart notice.
/// A modifier of its own: ContentView's chain of alerts is at the type checker's limit.
/// The offer is only ever set by AutoTune's analysis, which runs only while it is on.
struct AutoTuneAlert: ViewModifier {
    /// Round 625 (owner's item 20): the verdicts that name an install limit (build/tools/autotune_rules.py); no Send Log button for them.
    static let installLimits: Set<String> = ["wow-window-no-address-space", "reserve-no-address-space-without-rights"]
    @ObservedObject private var offers = AutoTuneOffers.shared
    @ObservedObject private var library = LibraryModel.shared
    @ObservedObject private var gate = AutoTuneAlertGate.shared

    func body(content: Content) -> some View {
        content.alert(offers.offer?.headline ?? "Auto-tune", isPresented: presented, presenting: offers.offer) { offer in
            if offer.ask {
                Button("It froze") { offers.offer = nil; AutoTune.answer("froze") }
                Button("It closed by itself") { offers.offer = nil; AutoTune.answer("closed-by-itself") }
                Button("I closed BCD", role: .cancel) { offers.offer = nil; AutoTune.answer("i-closed") }
            } else {
                if offer.state.candidate != nil {
                    Button("Close BCD") {
                        LogStore.shared.log("[autotune] closed by the user for the next attempt")
                        exit(0)
                    }
                }
                // madeira-bcd FORK-ONLY: a search that ended without a fix sends its log in one tap. Round 625 (owner's item 20, N-S15): not
                // for an install limit (V71SS6, 9Z31S2: the players sent the log right after reading "add the limit with GetMoreRam"); every
                // such log reads the same and the alert already says what to do. Settings › Logs and the game's page still send any log.
                if ["stuck", "gave-up", "known"].contains(offer.state.status),
                   !(offer.state.status == "known" && Self.installLimits.contains(offer.state.known ?? "")),
                   let name = offer.state.attempts.last?.log, let log = LogUpload.log(named: name) {
                    Button("Send Log") {
                        offers.offer = nil
                        BCDLogComment.ask(for: log) { LogUpload.send(log, game: offer.title, comment: $0) }
                    }
                }
                Button("Stop for this game", role: .destructive) {
                    AutoTune.setOn(offer.windowsPath, false)
                    offers.offer = nil
                }
                Button("OK", role: .cancel) { offers.offer = nil }
            }
        } message: { offer in
            Text(offer.message)
        }
    }

    private var presented: Binding<Bool> {
        Binding(get: { AutoTune.enabled && offers.offer != nil && library.error == nil && library.restartNotice == nil
                       && gate.mayShow(offers.offer != nil) },
                set: { if !$0 { offers.offer = nil } })
    }
}

/// madeira-bcd (2026-10-24, the owner): an earlier session's auto-tune question came up while a game's page was open, and whatever
/// was pressed, the page was gone and the library's first screen was back. The alert hangs on ContentView, under every page and
/// sheet BCD presents (a game's page is a full-screen cover, its Game config a sheet), and iOS made room for it by taking them down.
/// Now an offer waits while anything is presented over the app's root screen and comes up once the person is back on it; once up it
/// stays until answered. What the alert says and what its buttons do are unchanged. Nothing runs while it waits: the pages and
/// sheets of BCD's interface call BCDPresentation.didClose when they close, and that is when it looks again.
final class AutoTuneAlertGate: ObservableObject {
    static let shared = AutoTuneAlertGate()
    /// The alert is up (or on its way) for the current offer: nothing is checked again until the offer is gone.
    private var showing = false

    /// Whether the alert may come up now (`pending`: an offer is waiting).
    func mayShow(_ pending: Bool) -> Bool {
        guard pending else { showing = false; return false }
        if showing { return true }
        if Self.nothingPresented() {
            showing = true
            return true
        }
        BCDPresentation.closed = { AutoTuneAlertGate.shared.lookAgain() }
        return false
    }

    /// A page or sheet closed: if an offer still waits, the alert's binding is read again.
    func lookAgain() {
        guard AutoTuneOffers.shared.offer != nil, !showing else { return }
        objectWillChange.send()
    }

    /// Nothing is presented over the app's root screen: no page, sheet, cover or alert of BCD's, no UIKit sheet.
    static func nothingPresented() -> Bool {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        guard let root = scene?.keyWindow?.rootViewController else { return true }
        return root.presentedViewController == nil
    }
}
