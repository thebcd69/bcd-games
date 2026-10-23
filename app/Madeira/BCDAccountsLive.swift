// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import UIKit
import SwiftUI

/// Settings › Accounts on the engine, for a host to show (Settings' detail pane): it owns its
/// BCDAccountsLive while it is on screen, so nothing of it stays alive once the section closes.
/// Other screens that need a Steam sign-in (setup, a game's page) open it with
/// `startOn: .signIn(qr: false)`.
struct BCDAccountsLiveSection: View {
    var showsTitle = true
    var scrolls = true
    var startOn: BCDAccountsPage = .main
    var back: (() -> Void)? = nil
    var backHook: BCDSectionKit.BackHook? = nil
    @StateObject private var backend = BCDAccountsLive()

    init(showsTitle: Bool = true, scrolls: Bool = true, startOn: BCDAccountsPage = .main,
         back: (() -> Void)? = nil, backHook: BCDSectionKit.BackHook? = nil) {
        self.showsTitle = showsTitle
        self.scrolls = scrolls
        self.startOn = startOn
        self.back = back
        self.backHook = backHook
    }

    var body: some View {
        BCDAccountsSection(backend: backend, showsTitle: showsTitle, scrolls: scrolls, startOn: startOn,
                           back: back, backHook: backHook)
    }
}

/// BCD's Steam sign-in on its own, for screens outside Settings (first-run setup, the shell's
/// sign-in sheet): the sign-in page on BCD's ground. It closes itself once Steam signs you in,
/// or on its back button; a sheet swiped away cancels the sign-in. Touch only, as the sheet it
/// replaces, unless the host passes its BCDFocus.
struct BCDSteamSignInSheet: View {
    var focus: BCDFocus? = nil
    @StateObject private var backend = BCDAccountsLive()
    @StateObject private var ownFocus = BCDFocus(watchControllers: false)
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        BCDSteamSignInPage(backend: backend, startWithQR: UIDevice.current.userInterfaceIdiom == .pad) { dismiss() }
            .background { BCDBackdrop() }
            .coordinateSpace(name: BCDFocus.space)
            .environmentObject(focus ?? ownFocus)
            .foregroundStyle(BCDStyle.text)
            .preferredColorScheme(.dark)
    }
}

/// Settings › Accounts on the engine (engine-bound: tools/ui-preview.py's ENGINE_BOUND keeps
/// it out of the preview app). Everything goes through what the app already had:
/// SteamSignInModel (password and Steam Guard, or the QR code; SwiftSteam/SteamSignIn.swift)
/// for signing in and out, SteamCloudSetting (SteamGames.swift) for Steam Cloud saves and
/// OnboardingModel for setup. GOG goes through BCDLiveStore's GOG hooks (gogAccount, signIn(.gog),
/// signOut(.gog); BCDGOG/BCDLiveStoreGOG.swift), so this file names only what they publish. The
/// password is handed to SteamSignInModel.signIn as it arrives and is neither kept nor logged here. Steam's profile name and picture are not
/// fetched by the app (SteamSession never fills its persona name), so they stay empty.
@MainActor final class BCDAccountsLive: BCDAccountsBackend {
    @Published private(set) var steam = BCDSteamAccount()
    @Published private(set) var signInProgress = BCDSteamSignIn()
    @Published private(set) var gogAccount = BCDAccount(store: .gog, name: nil, available: false)
    @Published private(set) var battleNetAccount = BCDBnetAccount()
    @Published private(set) var battleNetError: String?
    private var watching: Set<AnyCancellable> = []

    init() {
        let changes: [AnyPublisher<Void, Never>] = [
            SteamSignInModel.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            SteamCloudSetting.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            NotificationCenter.default.publisher(for: SteamSignIn.didChange).map { _ in () }.eraseToAnyPublisher(),
            BCDGOG.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            BCDBattleNet.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
        ]
        // objectWillChange fires before the value changes: read on the next turn of the main queue.
        Publishers.MergeMany(changes).receive(on: DispatchQueue.main)
            .sink { [weak self] in MainActor.assumeIsolated { self?.read() } }
            .store(in: &watching)
        read()
    }

    func refresh() {
        SteamSignInModel.shared.refresh()
        read()
    }

    private func read() {
        let model = SteamSignInModel.shared
        let account = BCDSteamAccount(available: SteamSignIn.isEnabled, accountName: model.accountName,
                                      personaName: nil, avatar: nil,
                                      cloudAvailable: SteamOwnedLibrary.enabled, cloudOn: SteamCloudSetting.shared.on,
                                      setupAvailable: OnboardingModel.shared.available)
        if account != steam { steam = account }
        let progress = BCDSteamSignIn(busy: model.signInBusy, steamGuard: model.guardPrompt.map(Self.steamGuard),
                                      qrImage: model.qrImage, qrLink: model.qrLink, error: model.signInError)
        if progress != signInProgress { signInProgress = progress }
        let gog = BCDLiveStore.shared.gogAccount()
        if gog != gogAccount { gogAccount = gog }
        BCDBattleNet.shared.start()
        let battleNet = BCDLiveStore.shared.battleNetAccount()
        let accountState = BCDBnetAccount(available: battleNet.available, accountName: battleNet.name)
        if accountState != battleNetAccount { battleNetAccount = accountState }
        battleNetError = BCDBattleNet.shared.error
    }

    private static func steamGuard(_ prompt: SteamGuardPrompt) -> BCDSteamGuard {
        let code: BCDSteamGuard.Code? = prompt.codeType.map { $0 == .device ? .steamApp : .email(hint: prompt.hint) }
        return BCDSteamGuard(code: code, canApprove: prompt.canApprove)
    }

    func signIn(account: String, password: String) {
        SteamSignInModel.shared.signIn(account: account, password: password)
    }

    func submitGuardCode(_ code: String) { SteamSignInModel.shared.submitGuardCode(code) }
    func beginQR() { SteamSignInModel.shared.beginQR() }
    func cancelSignIn() { SteamSignInModel.shared.cancelSignIn() }
    func clearSignInError() { SteamSignInModel.shared.signInError = nil }

    /// The same sign-out the old Settings › Steam used; the app's own listeners
    /// (SteamSignIn.didChange) pause Steam downloads, clear the Steam list and the Dock's
    /// transfer.
    func signOut() { SteamSignInModel.shared.signOut() }

    func setCloud(_ on: Bool) { SteamCloudSetting.shared.on = on }
    func runSetup() { OnboardingModel.shared.rerun() }

    /// GOG's own sign-in page and its sign-out, through the store's GOG hooks (gogAccountAction).
    func signInGOG() { BCDLiveStore.shared.signIn(.gog) }
    func signOutGOG() { BCDLiveStore.shared.signOut(.gog) }
    func signInBattleNet() { BCDLiveStore.shared.signIn(.battlenet) }
    func signOutBattleNet() { BCDLiveStore.shared.signOut(.battlenet) }
}
