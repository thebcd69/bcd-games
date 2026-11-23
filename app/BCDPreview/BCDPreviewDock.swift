// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

/// Settings › BCD Dock on sample data, for the preview app. Variants (-BCDTab
/// settings-dock-<variant>): none (ready, two games with one-time installs), "new" (no client
/// files, nobody signed in), "downloading", "newer" (ready on the January 2026 client with the November 2026 one chosen: the Switch
/// button), "september" (the same with the September 2026 one chosen), "working" (a switch between builds under way: the choice is dimmed).
/// Steam for Windows' card under the Dock's own (branch steam-client): its switch is off in every variant but "steam" (on, the client not
/// installed), "steam-downloading" and "steam-installed", which show the default Dock sample above it.
@MainActor final class BCDPreviewDockBackend: BCDDockBackend {
    @Published private(set) var state: BCDDockSectionState
    private let steamDesktop: BCDPreviewSteamDesktopBackend

    var extraCards: AnyView { AnyView(BCDSteamDesktopCard(backend: steamDesktop)) }

    init(variant: String) {
        steamDesktop = BCDPreviewSteamDesktopBackend(variant: variant)
        var s = BCDDockSectionState()
        s.components.downloadBytes = 72_361_220
        switch variant {
        case "new":
            s.components.installed = false
        case "downloading":
            s.accountName = "player"
            s.components.working = true
            s.components.step = "Getting the client components, part 1 of 3…"
            s.components.stepIndex = 1
            s.components.steps = 4
        default:
            s.accountName = "player"
            s.components.installed = true
            s.components.installedBuild = 202601
            if variant == "newer" { s.components.chosenBuild = 202611; s.components.downloadBytes = 67_355_075 }
            if variant == "september" { s.components.chosenBuild = 202609; s.components.downloadBytes = 66_368_576 }
            if variant == "working" {
                s.components.chosenBuild = 202611
                s.components.downloadBytes = 67_355_075
                s.components.working = true
                s.components.step = "Getting the client components, part 1 of 2…"
                s.components.stepIndex = 1
                s.components.steps = 3
            }
            s.games = [
                BCDDockGame(id: 271590, name: "Grand Theft Auto V Legacy", installed: true),
                BCDDockGame(id: 367520, name: "Hollow Knight", installed: true),
                BCDDockGame(id: 620, name: "Portal 2", installed: false),
                BCDDockGame(id: 1174180, name: "Red Dead Redemption 2", installed: true),
            ]
            s.installGames = [
                BCDDockInstallGame(id: 271590, name: "Grand Theft Auto V Legacy", programs: 3, runNext: false, pending: nil),
                BCDDockInstallGame(id: 1174180, name: "Red Dead Redemption 2", programs: 2, runNext: true, pending: .reset),
            ]
            s.lastResult = "BCD Dock ended without errors."
        }
        state = s
    }

    func refresh() {}
    func downloadComponents() {
        state.components.working = true
        state.components.step = "Getting the client components, part 1 of 3…"
        state.components.stepIndex = 1
        state.components.steps = 4
    }
    func replaceComponents() { downloadComponents() }
    func setClientBuild(_ id: Int) {
        state.components.chosenBuild = id
        state.components.downloadBytes = id == 202611 ? 67_355_075 : id == 202609 ? 66_368_576 : 72_361_220
    }
    func start(_ appID: Int) {
        let name = state.games.first { $0.id == appID }?.name ?? "the game"
        state.lastResult = "Sample: starting \(name) through BCD Dock."
    }
    func setCompactPool(_ on: Bool) { state.compactPool = on }
    func setRunsInstalls(_ appID: Int, _ run: Bool) {
        guard let index = state.installGames.firstIndex(where: { $0.id == appID }) else { return }
        state.installGames[index].runNext = run
    }
    func setRequest(_ appID: Int, _ request: BCDDockRequest?) {
        guard let index = state.installGames.firstIndex(where: { $0.id == appID }) else { return }
        state.installGames[index].pending = request
    }
}

/// Steam for Windows' card on sample data (BCDSteamDesktop.swift).
@MainActor final class BCDPreviewSteamDesktopBackend: BCDSteamDesktopBackend {
    @Published private(set) var state = BCDSteamDesktopState()

    init(variant: String) {
        switch variant {
        case "steam":
            state.on = true
        case "steam-downloading":
            state.on = true
            state.work = BCDSteamDesktopState.Work(step: "Getting the client's files…", received: 96_468_992, total: 257_535_965)
        case "steam-installed":
            state.on = true
            state.installed = true
            state.version = "1794536281"
            state.diskBytes = 1_288_490_188
        default:
            break
        }
    }

    func refresh() {}
    func setOn(_ on: Bool) { state.on = on }
    func setFetchesPackages(_ on: Bool) { state.fetchesPackages = on }
    func setSharesDock(_ on: Bool) { state.sharesDock = on }
    func install() { state.work = BCDSteamDesktopState.Work(step: "Asking Valve's update server…") }
    func stopInstall() { state.work = nil }
    func open() { state.error = "Sample: Steam for Windows would open in a Windows desktop." }
}
