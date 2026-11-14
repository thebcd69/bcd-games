// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// Epic's cloud saves on the game's page, engine side (build 632; docs/EPIC.md). The page (BCDGamePage.swift) shows a store's cloud saves
// in that store's words and names no store; this extension of the live backend (BCDGamePageLive.swift reaches it through five one-line
// hooks with store-neutral names) fills the page's cloud state in from BCDEpicCloud, routes the page's Sync now and "keep which copy"
// to it and writes the game's own switch. Engine-bound, and outside app/Madeira's top level, so the preview build never compiles it.
// Every function does nothing and returns false/empty for any game that is not an Epic game BCD installed.

extension BCDCloudNames {
    /// Epic's: its cloud saves are off until the player turns them on (a sync writes to the Epic account), and a sync only adds snapshots.
    static let epic = BCDCloudNames(store: "Epic Games", cloud: "Epic Cloud", backups: "Epic Cloud Backups", front: .epic, optIn: true, keepsOlder: true)
}

extension BCDGamePageLive {
    /// The Epic game's app name when its cloud saves are in play (Epic on); nil for any other game.
    private func storeCloudApp(_ game: BCDGame) -> String? {
        game.store == .epic ? BCDEpicRules.claimedApp(game.id, enabled: BCDEpic.enabled) : nil
    }

    /// The page's Sync now: true when the game is Epic's (the sync is Epic Cloud's, and does nothing for a game whose switch is off).
    /// madeira-bcd: this game's cloud saves are another store's (the page's Check again is Steam's comparison and leaves such a game alone).
    func storeCloudOwns(_ game: BCDGame) -> Bool { storeCloudApp(game) != nil }

    func storeCloudSync(_ game: BCDGame) -> Bool {
        guard let app = storeCloudApp(game) else { return false }
        Task { await BCDEpicCloud.shared.syncNow(app) }
        return true
    }

    /// The player's choice for saves that differ.
    func storeCloudKeep(_ game: BCDGame, fromCloud: Bool) -> Bool {
        guard let app = storeCloudApp(game) else { return false }
        Task { await BCDEpicCloud.shared.resolve(app, useCloud: fromCloud) }
        return true
    }

    /// The page's Turn on and Turn off: the game's own `env.BCD_EPIC_CLOUD` line (the page asked before Turn on). True for that setting.
    func storeCloudSet(_ setting: String, to value: String, for game: BCDGame) -> Bool {
        guard setting == BCDCloudSetting.enabled, let app = storeCloudApp(game) else { return false }
        setLine(game, "env.BCD_EPIC_CLOUD", value == "1" ? "1" : "")
        onChange?()
        if value == "1" { Task { await BCDEpicCloud.shared.check(app) } }
        return true
    }

    /// A sync of the game's cloud saves moved on: the page reads its details again.
    var storeCloudChanges: AnyPublisher<Void, Never> {
        BCDEpicCloud.shared.$states.dropFirst().map { _ in () }.eraseToAnyPublisher()
    }

    /// The game's cloud state for the page: off until the player turns it on for the game, then where a sync stands, the saves the two
    /// sides disagree on and what the last one moved.
    func storeCloudDetails(_ game: BCDGame, _ entry: LibraryEntry, into d: inout BCDGameDetails) {
        guard let app = storeCloudApp(game) else { return }
        d.cloudNames = .epic
        guard BCDEpicCloud.isOn(entry) else { d.cloud = .off; return }
        guard BCDEpic.shared.signedIn else { d.cloud = .signedOut; return }
        let state = BCDEpicCloud.shared.state(app)
        d.cloudReceived = state.received ?? 0
        d.cloudBackedUp = state.backedUp
        d.cloudSent = state.sent ?? 0
        switch state.phase {
        case .idle: d.cloud = .notChecked
        case .checking: d.cloud = .checking
        case .downloading: d.cloud = .moving(up: false, done: 0, total: state.cloudFiles)
        case .uploading: d.cloud = .moving(up: true, done: 0, total: state.deviceFiles)
        case .unsupported: d.cloud = .unsupported
        case .failed(let message): d.cloud = .failed(message)
        case .ready:
            switch state.verdict {
            case .conflict?:
                d.cloudConflicts = state.differences.map { save in
                    BCDCloudConflict(name: save.name, cloudDate: save.cloudDate, cloudBytes: Int64(clamping: save.cloudBytes),
                                     deviceDate: save.deviceBytes == nil ? nil : (save.deviceDate ?? .distantPast), deviceBytes: Int64(clamping: save.deviceBytes ?? 0))
                }
                d.cloud = .differ(max(1, state.differences.count))
            case .upload?: d.cloud = .waiting(max(1, state.deviceFiles))
            case .download?: d.cloud = .waiting(max(1, state.cloudFiles))
            case .empty?: d.cloud = .synced(matching: 0)
            default: d.cloud = .synced(matching: state.deviceFiles)
            }
        }
    }
}
