// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

/// Sample data for the preview app: a library like the owner's, a download, a controller.
/// Only the preview app compiles this file; the real app fills BCDStore from the engine.
@MainActor final class BCDPreviewActions: BCDActions {
    func play(_ game: BCDGame) { print("[preview] play \(game.id)") }
    func install(_ game: BCDGame, place: BCDInstallPlace) { print("[preview] install \(game.id)") }
    func update(_ game: BCDGame) {}
    func uninstall(_ game: BCDGame) {}
    func verify(_ game: BCDGame) {}
    func pauseDownload(_ id: String) {}
    func resumeDownload(_ id: String) {}
    func cancelDownload(_ id: String) {}
    func enableJIT() {}
    func signIn(_ store: BCDStoreFront) {}
    func signOut(_ store: BCDStoreFront) {}
    func addGameFromFiles() {}
    func refreshLibrary() {}
}

extension BCDStore {
    static func preview(empty: Bool = false, actions: BCDActions) -> BCDStore {
        let store = BCDStore(actions: actions)
        let now = Date()
        let h: TimeInterval = 3600
        if !empty {
            store.games = [
                BCDGame(id: "wc3", title: "Warcraft III: Reign of Chaos", store: .local, state: .ready, size: 1_200_000_000,
                        lastPlayed: now - 0.2 * h, minutesPlayed: 220, autoTuned: true, thirtyTwoBit: true, added: 4),
                BCDGame(id: "432940", title: "Breakneck", store: .steam, state: .downloading(progress: 0.34), size: 2_400_000_000,
                        lastPlayed: now - 30 * h),
                BCDGame(id: "367520", title: "Hollow Knight", store: .steam, state: .ready, size: 9_100_000_000,
                        place: .externalDrive, lastPlayed: now - 3 * h, minutesPlayed: 1840, added: 7),
                BCDGame(id: "620", title: "Portal 2", store: .steam, state: .notInstalled, size: 12_800_000_000),
                BCDGame(id: "wc2", title: "Warcraft II: Battle.net Edition", store: .local, state: .updateAvailable(bytes: 120_000_000),
                        size: 400_000_000, lastPlayed: now - 50 * h, minutesPlayed: 95, thirtyTwoBit: true, added: 1),
                BCDGame(id: "epic.deathsdoor", title: "Death's Door", store: .epic, state: .notInstalled, size: 4_100_000_000),
                BCDGame(id: "gog.moonscars", title: "Moonscars", store: .gog, state: .notInstalled, size: 2_200_000_000),
                BCDGame(id: "271590", title: "Grand Theft Auto V Legacy", store: .steam, state: .ready, size: 98_000_000_000,
                        lastPlayed: now - 140 * h, minutesPlayed: 600, recommended: true, added: 6),
                BCDGame(id: "1715130", title: "Crysis Remastered", store: .steam, state: .notInstalled, size: 21_000_000_000),
                BCDGame(id: "2420110", title: "Horizon Forbidden West", store: .steam, state: .notInstalled, size: 150_000_000_000),
                BCDGame(id: "1895880", title: "Ratchet & Clank: Rift Apart", store: .steam, state: .notInstalled, size: 75_000_000_000),
                BCDGame(id: "gog.witcher", title: "The Witcher: Enhanced Edition", store: .gog, state: .ready, size: 15_000_000_000,
                        lastPlayed: now - 400 * h, minutesPlayed: 45, added: 2),
            ]
            let steamApp = ["432940": 432940, "367520": 367520, "620": 620, "epic.deathsdoor": 894020, "gog.moonscars": 1374970,
                            "271590": 271590, "1715130": 1715130, "2420110": 2420110, "1895880": 1895880, "gog.witcher": 20900]
            store.games = store.games.map { game in steamApp[game.id].map { withArt(game, steam: $0) } ?? game }
            store.downloads = [
                BCDDownload(id: "432940", title: "Breakneck", store: .steam, doneBytes: 816_000_000, totalBytes: 2_400_000_000,
                            bytesPerSecond: 23_600_000),
                // A paused and a failed one, so the Downloads tab shows each kind of row's buttons.
                BCDDownload(id: "1715130", title: "Crysis Remastered", store: .steam, doneBytes: 6_300_000_000,
                            totalBytes: 21_000_000_000, bytesPerSecond: 0, paused: true),
                BCDDownload(id: "2420110", title: "Horizon Forbidden West", store: .steam, doneBytes: 41_000_000_000,
                            totalBytes: 150_000_000_000, bytesPerSecond: 0,
                            failed: "Steam didn't answer in time. Check your connection, then try again."),
            ]
            store.finishedToday = [
                BCDFinishedDownload(id: "367520", title: "Hollow Knight", bytes: 9_100_000_000, finished: now - 2 * h),
            ]
        }
        store.accounts = [
            BCDAccount(store: .steam, name: empty ? nil : "player", available: true),
            BCDAccount(store: .epic, name: nil, available: true),
            BCDAccount(store: .gog, name: nil, available: true),
            BCDAccount(store: .battlenet, name: nil, available: true),
        ]
        store.system = BCDSystemStatus(jit: .on, memoryLimitBytes: 6_442_450_944, increasedMemoryLimit: true,
                                       controllerName: "DualSense", controllerBattery: 82,
                                       freeDeviceBytes: 58_600_000_000, totalDeviceBytes: 256_000_000_000,
                                       freeExternalBytes: empty ? nil : 412_000_000_000)
        return store
    }
}

/// Store art for a sample game, when tools/ui-preview.py put it in the app: it fetches Steam's
/// public store pictures into ~/Library/Caches once and copies them into the preview app only.
/// The pictures belong to their publishers and are never committed.
private func withArt(_ game: BCDGame, steam app: Int) -> BCDGame {
    var game = game
    func file(_ name: String, _ ext: String) -> [URL] {
        Bundle.main.url(forResource: name, withExtension: ext, subdirectory: "PreviewArt/\(app)").map { [$0] } ?? []
    }
    game.coverArt = file("library_600x900", "jpg")
    game.headerArt = file("header", "jpg")
    game.heroArt = file("library_hero", "jpg")
    game.logoArt = file("logo", "png")
    return game
}
