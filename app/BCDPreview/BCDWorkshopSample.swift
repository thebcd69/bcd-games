// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

/// The Steam Workshop pages' sample backend for the preview app: two installed games with a Workshop, subscribed items in different states,
/// Browse and downloads that run for a few seconds, kept in memory. Only the preview app compiles this file; the app's backend is
/// BCDWorkshopLive (BCDSteamWorkshop/).
@MainActor final class BCDWorkshopSample: BCDWorkshopBackend {
    var onChange: (() -> Void)?
    var available: Bool { true }

    private let titles: [Int: String] = [4000: "Garry's Mod", 72850: "The Elder Scrolls V: Skyrim"]
    private let workshopless = 367520
    private func item(_ id: UInt64, _ title: String, _ summary: String, bytes: UInt64, kind: BCDWorkshopListing.Kind = .content) -> BCDWorkshopListing {
        BCDWorkshopListing(id: id, title: title, summary: summary, preview: nil, bytes: bytes, kind: kind,
                           downloadable: kind == .content, link: URL(string: "https://steamcommunity.com/sharedfiles/filedetails/?id=\(id)"))
    }
    private lazy var all: [UInt64: BCDWorkshopListing] = {
        let list = [
            item(1001, "gm_construct 13", "The classic sandbox map, rebuilt with new lighting and the old props. Spawn anything, build anything.", bytes: 48_000_000),
            item(1002, "Realistic Car Pack", "Forty cars with working lights, doors and sounds.", bytes: 612_000_000),
            item(1003, "Weapon Skins Collection", "A collection of items: subscribe to its items on Steam.", bytes: 0, kind: .collection),
            item(1004, "Old Server Map", "Left on the device for a server; the account no longer subscribes to it.", bytes: 12_500_000),
            item(2001, "Unofficial Skyrim Patch", "Fixes thousands of bugs.", bytes: 3_200_000),
        ]
        return Dictionary(uniqueKeysWithValues: list.map { ($0.id, $0) })
    }()
    private var subscribed: [Int: [UInt64]] = [4000: [1001, 1002, 1003]]
    private var installed: [UInt64: UInt64] = [1001: 48_000_000, 1004: 12_500_000]
    private var updates: Set<UInt64> = [1001]
    private var progress: [UInt64: Double] = [:]
    private var waiting: Set<UInt64> = []
    private var failed: [UInt64: String] = [:]
    private var cancelled = Set<UInt64>()
    private var problem: String?

    func games() -> [BCDWorkshopGame] {
        [BCDWorkshopGame(id: 4000, title: titles[4000]!, installedItems: installed.keys.filter { $0 < 2000 }.count, hasWorkshop: true, pending: pending(4000)),
         BCDWorkshopGame(id: 72850, title: titles[72850]!, installedItems: 0, hasWorkshop: true, pending: 0),
         BCDWorkshopGame(id: workshopless, title: "Hollow Knight", installedItems: 0, hasWorkshop: false, pending: 0)]
    }

    private func pending(_ appID: Int) -> Int {
        (subscribed[appID] ?? []).filter { installed[$0] == nil && all[$0]?.downloadable == true || updates.contains($0) }.count
    }

    func page(_ appID: Int) -> BCDWorkshopPageState {
        var state = BCDWorkshopPageState(installed: true, signedIn: true, ready: true)
        state.subscribed = (subscribed[appID] ?? []).compactMap { all[$0] }
        if appID == 4000 { state.others = installed.keys.filter { !(subscribed[4000] ?? []).contains($0) && $0 < 2000 }.sorted().compactMap { all[$0] } }
        state.pending = pending(appID)
        state.problem = problem
        return state
    }

    func status(_ item: UInt64, appID: Int) -> BCDWorkshopStatus {
        var status = BCDWorkshopStatus()
        status.subscribed = (subscribed[appID] ?? []).contains(item)
        if let fraction = progress[item] {
            status.state = .downloading(fraction: fraction, totalBytes: all[item]?.bytes ?? 0)
        } else if waiting.contains(item) {
            status.state = .waiting
        } else if let why = failed[item] {
            status.state = .failed(why)
        } else if let bytes = installed[item] {
            status.state = .installed(bytes: bytes, update: updates.contains(item))
        }
        return status
    }

    func opened(_ appID: Int) {}

    func browse(_ appID: Int, order: BCDWorkshopOrder, text: String, page: Int) async throws -> BCDWorkshopBrowsePage {
        try? await Task.sleep(nanoseconds: 400_000_000)
        if text == "fail" { throw BCDGameError("Steam is limiting requests. Wait a minute and try again.") }
        guard appID == 4000 else { return BCDWorkshopBrowsePage(total: 0, items: []) }
        let names = ["Rooftop Parkour", "Zombie Survival", "Prop Hunt Pack", "Neon City", "Tank Battles", "Ragdoll Toolkit", "Drift Playground",
                     "Medieval Castle", "Space Station", "Hide and Seek"]
        let found = text.isEmpty ? names : names.filter { $0.localizedCaseInsensitiveContains(text) }
        let items = found.enumerated().map { index, name in
            item(UInt64(5000 + index + (order == .recent ? 100 : 0)), name, "A community item for \(titles[4000] ?? "this game"): \(name.lowercased()).", bytes: UInt64(2_000_000 * (index + 1)))
        }
        for listing in items { all[listing.id] = listing }
        return BCDWorkshopBrowsePage(total: items.count, items: page == 1 ? items : [])
    }

    func subscribe(_ item: BCDWorkshopListing, appID: Int) {
        subscribed[appID, default: []].insert(item.id, at: 0)
        all[item.id] = item
        download(item, appID: appID)
    }

    func unsubscribe(_ item: BCDWorkshopListing, appID: Int, removeFiles: Bool) {
        subscribed[appID]?.removeAll { $0 == item.id }
        if removeFiles { installed[item.id] = nil; updates.remove(item.id) }
        onChange?()
    }

    func download(_ item: BCDWorkshopListing, appID: Int) {
        cancelled.remove(item.id)
        waiting.insert(item.id)
        onChange?()
        Task {
            try? await Task.sleep(nanoseconds: 500_000_000)
            waiting.remove(item.id)
            for step in 1...10 {
                guard !cancelled.contains(item.id) else { return }
                progress[item.id] = Double(step) / 10
                onChange?()
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
            progress[item.id] = nil
            installed[item.id] = item.bytes
            updates.remove(item.id)
            onChange?()
        }
    }

    func downloadAll(_ appID: Int) {
        for id in subscribed[appID] ?? [] where (installed[id] == nil || updates.contains(id)) && all[id]?.downloadable == true {
            if let listing = all[id] { download(listing, appID: appID) }
        }
    }

    func cancel(_ item: UInt64) {
        cancelled.insert(item)
        waiting.remove(item)
        progress[item] = nil
        onChange?()
    }

    func remove(_ item: BCDWorkshopListing, appID: Int) {
        installed[item.id] = nil
        updates.remove(item.id)
        onChange?()
    }

    func clearProblem() {
        problem = nil
        onChange?()
    }
}

/// `-BCDTab workshop[-<game id>[-browse|-card]]`: the Workshop alone: the area, a game's page, its Browse, or an item's card.
struct BCDWorkshopPreviewRoot: View {
    let spec: String
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus

    var body: some View {
        let parts = spec.split(separator: "-").map(String.init)
        let id = parts.count > 1 ? Int(parts[1]) : nil
        let what = parts.count > 2 ? parts[2] : ""
        if let id {
            BCDWorkshopCover(route: .game(id), showing: what == "browse" ? .browse(id) : nil, card: what == "card") { print("[preview] back") }
        } else {
            BCDWorkshopCover(route: .area) { print("[preview] back") }
        }
    }
}
