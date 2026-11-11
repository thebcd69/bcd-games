// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

/// Settings › BCD Dock on sample data, for the preview app. Variants (-BCDTab
/// settings-dock-<variant>): none (ready, two games with one-time installs), "new" (no client
/// files, nobody signed in), "downloading".
@MainActor final class BCDPreviewDockBackend: BCDDockBackend {
    @Published private(set) var state: BCDDockSectionState

    init(variant: String) {
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
