// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

/// BCD's interface in the app (docs/BCD-UI-ARCHITECTURE.md). ContentView shows it whenever no
/// game's session runs, with three connections to the engine: Play (ContentView.launchLibraryEntry),
/// Enable JIT and the Dock start. A game's session replaces it, so while a game runs none of this
/// exists. Around BCDFrontEnd it does what the library's screen once did:
/// - first-run setup (OnboardingView) opens over it;
/// - the library's and Steam's errors show here;
/// - Steam's lists are read again when it appears and when BCD comes back to the front;
/// - BCD's own Steam sign-in (BCDSteamSignInSheet), Add a game (BCDAddGame) and game page
///   (BCDGamePage: after Add a game, and for the Play question's Choose saves) open from here;
/// - a start waiting for its Steam Cloud saves says so (docs/research/parity-2026-10-18.md).
/// A controller drives BCDFocus through LibraryController's commands (GamepadInput's sampler),
/// so BCD's interface never takes the GameController handlers the engine sets.
/// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).
struct BCDLiveFrontEnd: View {
    let play: (LibraryEntry) -> Void
    let enableJIT: () -> Void
    let startDock: (DockGame, Bool) -> Void

    @ObservedObject private var live = BCDLiveStore.shared
    @ObservedObject private var library = LibraryModel.shared
    @ObservedObject private var steam = SteamOwnedLibrary.shared
    @ObservedObject private var onboarding = OnboardingModel.shared
    @ObservedObject private var jit = JITCoordinator.shared
    @ObservedObject private var update = BCDUpdateNotice.shared
    @ObservedObject private var memory = MemoryRightsNotice.shared
    @StateObject private var focus = BCDFocus(watchControllers: false)
    @Environment(\.scenePhase) private var scenePhase
    /// What a game's page opens on: Cloud saves for Choose saves, Library for a game just added.
    @State private var opening: BCDGameOptionsPage?

    /// The library's error is a JIT connection problem with a fix to offer.
    private var jitProblem: JITCoordinator.ConnectionProblem? {
        jit.connectionProblem.flatMap { library.error == $0.message ? $0 : nil }
    }

    var body: some View {
        presenting(watching(shell))
    }

    private var shell: some View {
        // The settings button and Menu open BCD's own Settings (BCDFrontEnd's).
        BCDFrontEnd(store: live.store, focus: focus)
            .toolbar(.hidden, for: .navigationBar)
    }

    // MARK: What the library's screen did around itself

    private func watching<Content: View>(_ content: Content) -> some View {
        content
            .onAppear {
                live.connect(play: play, enableJIT: enableJIT)
                live.dockStart = startDock
                // An ended desktop session's surface never stays over the interface, and
                // first-run setup is considered when the library appears.
                EndedSessionSurface.install()
                EndedSessionSurface.hide(reason: "library-appeared")
                onboarding.presentIfNeeded()
                live.libraryAppeared()
                // A newer BCD, at most once a day (BCDUpdateCheck.swift); never blocks anything.
                update.start()
                // An install without the increased memory limit, once per build (MemoryRights.swift).
                memory.start()
            }
            .onChange(of: scenePhase) { _, phase in
                guard phase == .active else { return }
                library.refreshFlag()
                live.becameActive()
                update.recheckIfDue()
            }
            .onReceive(LibraryController.shared.commands) { command($0) }
            // An open page closes when the session starts or a message needs the screen.
            .onChange(of: library.current) { _, current in if current != nil { live.sheet = nil } }
            .onChange(of: library.error) { _, error in if error != nil { live.sheet = nil } }
            .onChange(of: library.restartNotice) { _, notice in if notice != nil { live.sheet = nil } }
            .onChange(of: library.jitNotice) { _, notice in if notice != nil { live.sheet = nil } }
            .onChange(of: library.cloudNotice) { _, notice in if notice != nil { live.sheet = nil } }
            .onChange(of: steam.error) { _, error in if error != nil { live.sheet = nil } }
            .onChange(of: jit.showSetup) { _, show in if show { live.sheet = nil } }
            // A Steam Cloud choice opens the game's page (ContentView's cloud notice).
            .onChange(of: library.showDetail) { _, id in
                guard let id else { return }
                library.showDetail = nil
                guard let entry = library.entries.first(where: { $0.id == id }) else { return }
                opening = entry.steamAppID != nil && SteamOwnedLibrary.cloudEnabled ? .cloud : nil
                live.sheet = .details(entry)
            }
    }

    private func presenting<Content: View>(_ content: Content) -> some View {
        presentingSheets(
            content
                .alert(jitProblem == nil ? "Library" : "Enabling JIT Failed",
                       isPresented: Binding<Bool>(get: { library.error != nil }, set: { if !$0 { library.error = nil } })) {
                    if let jitProblem { jitConnectionActions(jitProblem, retry: enableJIT) { library.error = nil } }
                    Button("OK", role: .cancel) { library.error = nil }
                } message: { Text(library.error ?? "") }
                .alert("Steam", isPresented: Binding<Bool>(get: { steam.error != nil }, set: { if !$0 { steam.error = nil } })) {
                    Button("OK", role: .cancel) { steam.error = nil }
                } message: { Text(steam.error ?? "") }
                .modifier(BCDEpicPromptAlert())   // Epic's sign-in question when a start can't be signed in (BCDEpicStart.swift); nothing until one asks
                // An install without the increased memory limit (MemoryRights.swift): told, never blocked;
                // only over the library, OK is the only button.
                .alert(memory.title, isPresented: Binding<Bool>(
                    get: {
                        guard memory.message != nil, library.current == nil, !onboarding.presented else { return false }
                        return library.error == nil && steam.error == nil && live.graphicsTip == nil
                    },
                    set: { if !$0 { memory.message = nil } })) {
                    Button("OK", role: .cancel) { memory.message = nil }
                } message: { Text(memory.message ?? "") }
                // A newer BCD (BCDUpdateCheck.swift): only over the library, never during a game, first-run
                // setup or another alert; OK is the only button.
                .alert("Update available", isPresented: Binding<Bool>(
                    get: {
                        guard update.message != nil, library.current == nil, !onboarding.presented else { return false }
                        guard library.error == nil, steam.error == nil, memory.message == nil else { return false }
                        return live.sheet == nil && live.graphicsTip == nil
                    },
                    set: { if !$0 { update.message = nil } })) {
                    Button("OK", role: .cancel) { update.message = nil }
                } message: { Text(update.message ?? "") }
                // madeira-bcd (build 622): a heavy game's one-time graphics tip before its first start (BCDGraphicsTip,
                // BCDLiveStore.begin); OK is the only button and starts the game.
                .alert(BCDGraphicsTip.title, isPresented: Binding<Bool>(
                    get: { live.graphicsTip != nil && library.error == nil && steam.error == nil },
                    set: { if !$0 { live.graphicsTipAnswered() } })) {
                    Button("OK") { live.graphicsTipAnswered() }
                } message: { Text(BCDGraphicsTip.message) }
        )
    }

    private func presentingSheets<Content: View>(_ content: Content) -> some View {
        content
            // First-run setup (Onboarding.swift).
            .fullScreenCover(isPresented: $onboarding.presented, onDismiss: BCDPresentation.didClose) { OnboardingView() }
            .sheet(item: sheetShown, onDismiss: BCDPresentation.didClose) { sheet in screen(sheet) }
            .fullScreenCover(item: pageShown, onDismiss: BCDPresentation.didClose) { page in pageScreen(page) }
            // A start waiting for its Steam Cloud saves.
            .overlay(alignment: .bottom) { cloudWaitNote }
    }

    @ViewBuilder private func screen(_ sheet: BCDLiveSheet) -> some View {
        switch sheet {
        case .steamSignIn:
            BCDSteamSignInSheet()   // BCD's own sign-in (BCDAccountsLive.swift)
        case .addGame, .details:
            EmptyView()   // full screen (pageScreen)
        }
    }

    // MARK: BCD's own pages over the shell

    /// Add a game and a game's page are full screen; the rest are sheets.
    private static func fullScreen(_ sheet: BCDLiveSheet) -> Bool {
        switch sheet {
        case .addGame, .details: return true
        default: return false
        }
    }

    private var sheetShown: Binding<BCDLiveSheet?> {
        Binding(get: { live.sheet.flatMap { Self.fullScreen($0) ? nil : $0 } },
                set: { new in if new != nil || live.sheet.map(Self.fullScreen) == false { live.sheet = new } })
    }

    private var pageShown: Binding<BCDLiveSheet?> {
        Binding(get: { live.sheet.flatMap { Self.fullScreen($0) ? $0 : nil } },
                set: { new in if new != nil || live.sheet.map(Self.fullScreen) == true { live.sheet = new } })
    }

    @ViewBuilder private func pageScreen(_ sheet: BCDLiveSheet) -> some View {
        switch sheet {
        case .addGame:
            BCDAddGame(close: { live.sheet = nil }, added: { id in
                // The game's page opens on its Library section: its name, Find on Steam, a cover.
                guard case .local(let added)? = BCDLiveID.parse(id), let entry = library.entries.first(where: { $0.id == added }) else {
                    live.sheet = nil
                    return
                }
                opening = .group("library")
                live.sheet = .details(entry)
            })
            .environmentObject(live.store)
            .environmentObject(focus)
        case .details(let entry):
            BCDGamePage(gameID: entry.steamAppID.map(BCDLiveID.steam) ?? BCDLiveID.local(entry.id), from: "Library",
                        panel: opening, after: 0.45) {
                opening = nil
                live.sheet = nil
            }
            .environmentObject(live.store)
            .environmentObject(focus)
        default:
            EmptyView()
        }
    }

    // MARK: What the library's screen said

    /// "Sync first" in the Steam Cloud question (ContentView.cloudWait) brings a Steam game's saves
    /// up to date, then starts it: this says so until the game starts. Static (no spinner):
    /// nothing in BCD's interface animates on its own.
    @ViewBuilder private var cloudWaitNote: some View {
        if let appID = steam.cloudWaitingFor {
            let name = SteamGamesModel.shared.games.first { $0.id == appID }?.name
                ?? steam.game(appID)?.name ?? "the game"
            HStack(spacing: 10) {
                Image(systemName: "icloud.and.arrow.down").font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(BCDStyle.lavender)
                    .accessibilityHidden(true)
                Text("Bringing \(name)'s saves up to date with Steam Cloud before it starts…")
                    .font(BCDStyle.label(14)).foregroundStyle(BCDStyle.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .frame(maxWidth: 560)
            .padding(.horizontal, 16).padding(.bottom, 64)
            .accessibilityElement(children: .combine)
            .allowsHitTesting(false)
        }
    }

    // MARK: Controller

    /// LibraryController's commands, while BCD's shell has the screen: no game, no setup, no
    /// page over it (those take the controller themselves).
    private func command(_ name: String) {
        guard library.current == nil, live.sheet == nil, !onboarding.presented else { return }
        switch name {
        case "up": focus.move(.up)
        case "down": focus.move(.down)
        case "left": focus.move(.left)
        case "right": focus.move(.right)
        case "accept": focus.press(.a)
        case "back": focus.press(.b)
        case "x": focus.press(.x)
        case "add": focus.press(.y)
        case "menu": focus.press(.menu)
        case "options": focus.press(.options)
        case "tab-previous": focus.press(.leftShoulder)
        case "tab-next": focus.press(.rightShoulder)
        default: break
        }
    }
}
