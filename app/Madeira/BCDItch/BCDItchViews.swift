// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (pull request #499)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import SwiftUI
import UIKit

// itch.io in BCD's interface (docs/ITCH.md): the card in Settings › Accounts, the API key form (in the card and in a sheet of its own, for a
// game's page and the Library) and Find a game. Engine-bound (it reads ItchLibrary), and outside app/Madeira's top level, so the preview build
// never compiles it. Built from BCDSectionKit like the Accounts cards beside it.

/// The two things the screens do with the account.
@MainActor enum BCDItchActions {
    /// A key is kept only once itch.io accepts it (ItchAPI.signIn); then the interface reads itch.io (the Library's chip appears).
    static func signIn(key: String) async throws {
        try await ItchLibrary.shared.signIn(key: key)
        BCDItchSignal.shared.changed()
        BCDStoresUI.shared.refresh()
    }

    static func signOut() {
        ItchLibrary.shared.signOut()
        BCDItchSignal.shared.changed()
        BCDStoresUI.shared.refresh()
    }
}

// MARK: - Settings › Accounts

struct BCDItchCard: View {
    @ObservedObject private var ui = BCDStoresUI.shared
    private static var prefix: String { "settings.accounts.itch." }

    var body: some View {
        BCDSectionKit.Card(eyebrow: "itch.io", symbol: "gamecontroller") {
            if !ItchLibrary.enabled {
                BCDSectionKit.Note(text: "itch.io is switched off in this copy of BCD.", symbol: "nosign")
            } else if ItchLibrary.active {
                BCDItchAccount(itch: ItchLibrary.shared)
            } else {
                // Nothing of itch.io has been touched yet: the key form is all there is.
                VStack(alignment: .leading, spacing: 14) {
                    Text("Sign in with an API key to see the games you bought or claimed on itch.io, add free games, and install them in BCD.")
                        .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                    BCDItchKeyForm(prefix: Self.prefix)
                }
            }
        }
    }
}

/// The signed-in or signed-out card once itch.io is in use.
private struct BCDItchAccount: View {
    @ObservedObject var itch: ItchLibrary
    @ObservedObject private var ui = BCDStoresUI.shared
    private static var prefix: String { "settings.accounts.itch." }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if itch.signedIn {
                HStack(spacing: 14) {
                    BCDSectionKit.StoreAvatar(name: itch.accountName ?? "itch.io account")
                    VStack(alignment: .leading, spacing: 3) {
                        Text(itch.accountName ?? "itch.io account").bcdFont(.heading, 19).lineLimit(1)
                        Text("itch.io account").bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    BCDStatusChip(text: "Signed in", color: BCDStyle.mint)
                }
                .accessibilityElement(children: .combine)
                BCDSectionKit.Note(text: "Your itch.io games are in the Library, under itch.io. For a free game that is not there, use Find a game.", symbol: "info.circle")
                BCDSectionKit.ActionButton(title: "Find a game", symbol: "magnifyingglass", kind: .outline, fill: true, id: Self.prefix + "find") {
                    BCDLiveStore.shared.openStoreTool(.itch)
                }
                if ui.question == "itch.signout" {
                    BCDSectionKit.Confirm(
                        title: "Sign out of itch.io?",
                        message: "BCD removes your API key from this \(BCDSectionKit.deviceWord)'s Keychain and forgets the list of your itch.io games. Installed games stay and still play. You can also delete the key on itch.io.",
                        choices: [
                            .init(title: "Sign out", kind: .danger, id: Self.prefix + "signout.confirm") {
                                _ = ui.close(focusing: Self.prefix + "key")
                                BCDItchActions.signOut()
                            },
                            .init(title: "Cancel", kind: .outline, id: Self.prefix + "signout.cancel") {
                                _ = ui.close(focusing: Self.prefix + "signout")
                            },
                        ])
                } else {
                    BCDSectionKit.ActionButton(title: "Sign out", symbol: "rectangle.portrait.and.arrow.right", kind: .danger,
                                               id: Self.prefix + "signout") {
                        ui.remember(Self.prefix + "signout")
                        ui.ask("itch.signout", focusing: Self.prefix + "signout.cancel")
                    }
                }
            } else {
                Text("Signed out. Installed itch.io games stay and still play. Sign in again with an API key to see your library and to update games.")
                    .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                BCDItchKeyForm(prefix: Self.prefix)
            }
        }
        .onAppear { itch.start() }
    }
}

// MARK: - The API key

/// Signing in with an API key: itch.io's own page makes one, the player pastes it. It is checked with itch.io and kept in the Keychain only once
/// itch.io accepts it (ItchAPI.signIn); the field is emptied at once and the key is never logged.
struct BCDItchKeyForm: View {
    let prefix: String
    var done: () -> Void = {}
    @EnvironmentObject private var focus: BCDFocus
    @State private var key = ""
    @State private var busy = false
    @State private var failure: String?
    @FocusState private var typing: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            BCDSectionKit.Steps(steps: [
                .init(id: 1, text: "Open **itch.io's API keys page** and sign in there if asked."),
                .init(id: 2, text: "Tap **Generate new API key**, then copy the key it shows."),
                .init(id: 3, text: "Paste the key here and tap **Sign in**."),
            ])
            BCDSectionKit.ActionButton(title: "Open itch.io's API keys page", symbol: "safari", kind: .outline, fill: true, id: prefix + "open") {
                UIApplication.shared.open(ItchAPI.keysPage)
            }
            BCDSectionKit.FieldBox(id: prefix + "key", activate: { typing = true }) {
                SecureField("", text: $key, prompt: Text("Paste the API key").foregroundStyle(BCDStyle.textSoft))
                    .textContentType(.password)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($typing)
                    .submitLabel(.go)
                    .onSubmit(submit)
            }
            if let failure {
                BCDSectionKit.Note(text: failure, symbol: "exclamationmark.triangle.fill", tone: .bad)
            }
            BCDSectionKit.ActionButton(title: busy ? "Checking…" : "Sign in", symbol: busy ? "hourglass" : "person.crop.circle.badge.plus", kind: .primary,
                                       fill: true, id: prefix + "submit", enabled: canSubmit && !busy, action: submit)
            Text("The key lets BCD see your itch.io library and download your games. BCD keeps it in this \(BCDSectionKit.deviceWord)'s Keychain, for this device only, and only once itch.io accepts it. Deleting the key on itch.io stops it everywhere.")
                .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
        }
        .onDisappear { key = "" }
    }

    private var canSubmit: Bool { !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    /// The only place the key leaves this form: once, to ItchAPI, then the field is empty.
    private func submit() {
        guard canSubmit, !busy else { return }
        let entered = key
        busy = true
        failure = nil
        key = ""
        typing = false
        Task { @MainActor in
            do {
                try await BCDItchActions.signIn(key: entered)
                busy = false
                done()
            } catch {
                busy = false
                failure = error.localizedDescription
            }
        }
    }
}

/// The key form on a sheet of its own: from a game's page (Downloading needs your itch.io sign-in) and from the Library.
struct BCDItchKeySheet: View {
    @StateObject private var ownFocus = BCDFocus(watchControllers: false)
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        BCDSectionKit.Page(title: "Sign in to itch.io", subtitle: "BCD talks to itch.io directly with an API key you make there.",
                           focusPrefix: "itch.key.", back: { dismiss() }) {
            BCDSectionKit.Columns {
                BCDSectionKit.Card(eyebrow: "Your itch.io account", symbol: "person.fill") {
                    BCDItchKeyForm(prefix: "itch.key.") { dismiss() }
                }
            } right: {
                EmptyView()
            }
        }
        .background { BCDBackdrop() }
        .coordinateSpace(name: BCDFocus.space)
        .environmentObject(ownFocus)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
    }
}

// MARK: - Find a game

/// Search itch.io, or paste a game's link, and add a free game to the itch.io library. A paid game links to its store page: it shows in the
/// library by itself once the account has it (and so do the games in the account's collections).
struct BCDItchFindSheet: View {
    @StateObject private var ownFocus = BCDFocus(watchControllers: false)
    @ObservedObject private var itch = ItchLibrary.shared
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var results: [ItchOwnedGame] = []
    @State private var busy = false
    @State private var failure: String?
    @State private var searched = false
    @FocusState private var typing: Bool
    private static var prefix: String { "itch.find." }

    var body: some View {
        BCDSectionKit.Page(title: "Find a game on itch.io",
                           subtitle: "Search itch.io or paste a game's link. Free and pay-what-you-want games install without buying.",
                           focusPrefix: Self.prefix, back: { dismiss() }) {
            BCDSectionKit.Columns {
                BCDSectionKit.Card(eyebrow: "Search", symbol: "magnifyingglass") {
                    BCDSectionKit.FieldBox(id: Self.prefix + "text", activate: { typing = true }) {
                        TextField("", text: $text, prompt: Text("Game name, or its itch.io link").foregroundStyle(BCDStyle.textSoft))
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .focused($typing)
                            .submitLabel(.search)
                            .onSubmit(run)
                    }
                    BCDSectionKit.ActionButton(title: busy ? "Searching…" : (ItchLibrary.isLink(text) ? "Open this link" : "Search itch.io"),
                                               symbol: busy ? "hourglass" : "magnifyingglass", kind: .primary, fill: true,
                                               id: Self.prefix + "run", enabled: !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !busy,
                                               action: run)
                    Text("A paid game needs buying on itch.io first. It then shows in the Library by itself, as do the games in your itch.io collections.")
                        .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                    if let failure { BCDSectionKit.Note(text: failure, symbol: "exclamationmark.triangle.fill", tone: .bad) }
                    if searched && results.isEmpty && failure == nil && !busy {
                        BCDSectionKit.Note(text: "No Windows games found.", symbol: "magnifyingglass")
                    }
                }
            } right: {
                if !results.isEmpty {
                    BCDSectionKit.Card(eyebrow: "Results", symbol: "list.bullet") {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(results) { game in row(game) }
                        }
                    }
                }
            }
        }
        .background { BCDBackdrop() }
        .coordinateSpace(name: BCDFocus.space)
        .environmentObject(ownFocus)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
    }

    private func row(_ game: ItchOwnedGame) -> some View {
        let inLibrary = itch.owned.contains { $0.id == game.id }
        let free = (game.minPrice ?? 0) == 0
        return HStack(spacing: 12) {
            BCDArtImage(urls: [game.cover].compactMap { $0 }, shape: 315.0 / 250.0) { BCDStyle.surfaceRaised }
                .frame(width: 63, height: 50)
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(game.title).bcdFont(.label, 15).lineLimit(2)
                Text([game.author, inLibrary ? "In your library" : free ? "Free" : "Paid"].compactMap { $0 }.joined(separator: " · "))
                    .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
            }
            Spacer(minLength: 6)
            if inLibrary {
                BCDStatusChip(text: "Added", color: BCDStyle.mint)
            } else if free {
                BCDSectionKit.ActionButton(title: "Add", symbol: "plus", kind: .primary, id: Self.prefix + "add.\(game.id)") {
                    itch.add(game)
                    BCDItchSignal.shared.changed()
                }
            } else if let url = game.url {
                BCDSectionKit.ActionButton(title: "Store page", symbol: "safari", kind: .outline, id: Self.prefix + "store.\(game.id)") {
                    UIApplication.shared.open(url)
                }
            }
        }
    }

    private func run() {
        let query = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, !busy else { return }
        busy = true
        failure = nil
        typing = false
        Task { @MainActor in
            do {
                results = ItchLibrary.isLink(query) ? [try await itch.lookUp(link: query)] : try await itch.search(query)
            } catch {
                results = []
                failure = error.localizedDescription
            }
            searched = true
            busy = false
        }
    }
}
