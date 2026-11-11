// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import SwiftUI

/// Settings › BCD Dock on the engine, for a host to show (Settings' detail pane): it owns its
/// BCDDockLive while it is on screen, so nothing of it stays alive once the section closes.
/// `startDock` is the interface's own Dock start (ContentView's startDock, which the library
/// and the old Dock sheet used: JIT first, Steam Cloud, one session per run); the host closes
/// Settings in it, as the old sheet closed itself. `signIn` opens Accounts' sign-in page.
struct BCDDockLiveSection: View {
    var showsTitle = true
    var scrolls = true
    var back: (() -> Void)? = nil
    var backHook: BCDSectionKit.BackHook? = nil
    var signIn: (() -> Void)? = nil
    @StateObject private var backend: BCDDockLive

    init(startDock: @escaping (DockGame, Bool) -> Void, signIn: (() -> Void)? = nil, showsTitle: Bool = true,
         scrolls: Bool = true, back: (() -> Void)? = nil, backHook: BCDSectionKit.BackHook? = nil) {
        self.showsTitle = showsTitle
        self.scrolls = scrolls
        self.back = back
        self.backHook = backHook
        self.signIn = signIn
        _backend = StateObject(wrappedValue: BCDDockLive(startDock: startDock))
    }

    var body: some View {
        BCDDockSection(backend: backend, showsTitle: showsTitle, scrolls: scrolls, back: back, backHook: backHook,
                       signIn: signIn)
    }
}

/// Settings › BCD Dock on the engine (engine-bound: tools/ui-preview.py's ENGINE_BOUND keeps it
/// out of the preview app). It reads MadeiraDockModel and the Steam sign-in, and acts only
/// through what the old Dock sheet used: MadeiraDockModel.prepareClient for Valve's client
/// files (SteamRuntimeInstaller, pinned SHA-256), the start closure it is given (the old
/// sheet's `start`) with MadeiraDockModel.compactPool, setRunsInstallers for the one-time
/// installs choice and setInstallRequest for Record as done and Reset, which the game's next
/// Dock start applies (DockInstallers.applyRequest, DockReset.clean).
@MainActor final class BCDDockLive: BCDDockBackend {
    @Published private(set) var state = BCDDockSectionState()
    private let startDock: (DockGame, Bool) -> Void
    /// The last step the download reported, kept while it says something without one
    /// ("Checking the download…").
    private var lastStep: Int?
    private var watching: Set<AnyCancellable> = []

    init(startDock: @escaping (DockGame, Bool) -> Void) {
        self.startDock = startDock
        let changes: [AnyPublisher<Void, Never>] = [
            MadeiraDockModel.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            SteamSignInModel.shared.objectWillChange.map { _ in () }.eraseToAnyPublisher(),
            NotificationCenter.default.publisher(for: SteamSignIn.didChange).map { _ in () }.eraseToAnyPublisher(),
        ]
        Publishers.MergeMany(changes).receive(on: DispatchQueue.main)
            .sink { [weak self] in MainActor.assumeIsolated { self?.read() } }
            .store(in: &watching)
        read()
    }

    func refresh() {
        MadeiraDockModel.shared.refresh()
        SteamSignInModel.shared.refresh()
        read()
    }

    private func read() {
        let dock = MadeiraDockModel.shared
        var next = BCDDockSectionState()
        next.inBuild = MadeiraDock.bundled
        next.switchedOn = MadeiraDock.enabled
        next.accountName = SteamSignInModel.shared.accountName

        let parts = SteamRuntimeFiles.packages.count
        var components = BCDDockComponents()
        components.installed = dock.clientInstalled
        components.unsupportedBuild = dock.clientUnsupportedBuild
        components.downloadBytes = Int64(SteamRuntimeFiles.packages.reduce(0) { $0 + $1.bytes })
        components.working = dock.preparing
        components.error = dock.error
        if dock.preparing {
            components.step = dock.progress.isEmpty ? nil : dock.progress
            if let step = Self.step(dock.progress, parts: parts) { lastStep = step }
            components.stepIndex = lastStep
            components.steps = parts + 1
        } else {
            lastStep = nil
        }
        next.components = components

        next.installsChoice = DockInstallers.choiceEnabled
        next.installGames = dock.games.compactMap { game in
            guard let programs = dock.installPrograms[game.id] else { return nil }
            return BCDDockInstallGame(id: game.id, name: game.name, programs: programs,
                                      runNext: dock.installRunNext[game.id] ?? true,
                                      pending: dock.installRequest[game.id].map(Self.request))
        }
        next.games = dock.games.map { BCDDockGame(id: $0.id, name: $0.name, installed: $0.installed) }
        next.compactPool = dock.compactPool
        next.lastResult = dock.status
        if next != state { state = next }
    }

    /// The download's step from its progress line (SteamRuntime.swift): "…part 2 of 3…" is
    /// step 2; installing into Windows is the last step. Nil for a line without a step.
    static func step(_ text: String, parts: Int) -> Int? {
        if let range = text.range(of: #"part (\d+) of \d+"#, options: .regularExpression) {
            let digits = text[range].dropFirst("part ".count).prefix { $0.isNumber }
            return Int(digits).map { min(max($0, 1), parts) }
        }
        if text.hasPrefix("Installing the components") || text.hasPrefix("Done") { return parts + 1 }
        return nil
    }

    private static func request(_ request: DockInstallRequest) -> BCDDockRequest {
        switch request {
        case .record: return .record
        case .reset: return .reset
        case .resetSignOut: return .resetAndSignOut
        }
    }

    private static func engine(_ request: BCDDockRequest) -> DockInstallRequest {
        switch request {
        case .record: return .record
        case .reset: return .reset
        case .resetAndSignOut: return .resetSignOut
        }
    }

    func downloadComponents() { MadeiraDockModel.shared.prepareClient() }
    func replaceComponents() { MadeiraDockModel.shared.prepareClient(replacing: true) }

    /// As the old Dock sheet started a game: the interface's start with the pool choice.
    func start(_ appID: Int) {
        let dock = MadeiraDockModel.shared
        guard let game = dock.games.first(where: { $0.id == appID }) else { return }
        startDock(game, dock.compactPool)
    }

    func setCompactPool(_ on: Bool) { MadeiraDockModel.shared.compactPool = on }
    func setRunsInstalls(_ appID: Int, _ run: Bool) { MadeiraDockModel.shared.setRunsInstallers(appID, run) }
    func setRequest(_ appID: Int, _ request: BCDDockRequest?) {
        MadeiraDockModel.shared.setInstallRequest(appID, request.map(Self.engine))
    }
}
