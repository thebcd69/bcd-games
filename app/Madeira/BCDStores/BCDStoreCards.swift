// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › Accounts: the cards of the stores added after GOG and Epic Games (itch.io, the experimental EA store). BCDAccountsLive hands them
// to the section through BCDAccountsBackend.extraStoreCards; the preview app has none. Engine-bound, and outside app/Madeira's top level, so the
// preview build never compiles it. Built from BCDSectionKit like the cards beside them: every control registers with BCDFocus, sign-out is
// asked in place (BCDSectionKit.Confirm), nothing moves on its own.

/// The question a store card has open (one at a time) and where the ring goes when it closes. The section asks it for B
/// (BCDAccountsBackend.closeExtraStoreQuestion) and folds `question` into the key that re-installs A and B and the ring's order.
@MainActor final class BCDStoresUI: ObservableObject {
    static let shared = BCDStoresUI()
    @Published private(set) var question = ""
    private var returnFocus: String?
    weak var focus: BCDFocus?

    var key: String { question }

    func ask(_ question: String, focusing id: String) {
        self.question = question
        if focus?.controllerActive == true { focus?.focused = id }
    }

    /// Closes the open question, putting the ring on `returnFocus`. False when none is open.
    func close(focusing id: String? = nil) -> Bool {
        guard !question.isEmpty else { return false }
        let ring = id ?? returnFocus
        question = ""
        if focus?.controllerActive == true, let ring { focus?.focused = ring }
        return true
    }

    /// Where B puts the ring while this question is open.
    func remember(_ id: String) { returnFocus = id }

    /// The itch.io and EA cards read the stores again (a sign-in, a sign-out, the switch).
    func refresh() { objectWillChange.send() }
}

/// The two cards, under GOG's and Epic's.
struct BCDStoreCards: View {
    @ObservedObject private var ui = BCDStoresUI.shared
    @EnvironmentObject private var focus: BCDFocus

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            BCDItchCard()
            BCDEACard()
            BCDAmazonCard()   // madeira-bcd (Porthole port, item 13): the experimental Amazon Games store, off by default
        }
        .onAppear { ui.focus = focus }
    }
}

extension BCDSectionKit {
    /// The account's first letter on the logo's colours, as the other cards draw it.
    struct StoreAvatar: View {
        let name: String
        var body: some View {
            ZStack {
                Circle().fill(BCDStyle.glow)
                Text(String(name.first ?? "?").uppercased()).font(.system(size: 22, weight: .heavy, design: .rounded)).foregroundStyle(.white)
            }
            .frame(width: 54, height: 54)
            .clipShape(Circle())
            .overlay(Circle().strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .accessibilityHidden(true)
        }
    }
}
