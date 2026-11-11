// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2026 Paulo Eduardo Correa (pauloec): the native iPhone Duo interface (Liquid Glass, system tabs, controller navigation, Windows desktop on Home), from thebcd69/bcd-games pull request #43; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
// BCD's menus: the model's rules, compiled on the host by tests/host/check-bcd-menu.py with
// app/Madeira/BCDStore.swift, the rules part of BCDGamePage.swift and the model part of BCDMenu.swift.

import Foundation
import SwiftUI

@MainActor final class Recorder: BCDActions {
    var calls: [String] = []
    func play(_ game: BCDGame) { calls.append("play \(game.id)") }
    func install(_ game: BCDGame, place: BCDInstallPlace) { calls.append("install \(game.id) \(place)") }
    func update(_ game: BCDGame) { calls.append("update \(game.id)") }
    func uninstall(_ game: BCDGame) { calls.append("uninstall \(game.id)") }
    func verify(_ game: BCDGame) { calls.append("verify \(game.id)") }
    func pauseDownload(_ id: String) { calls.append("pause \(id)") }
    func resumeDownload(_ id: String) { calls.append("resume \(id)") }
    func cancelDownload(_ id: String) { calls.append("cancel \(id)") }
    func enableJIT() { calls.append("jit") }
    func signIn(_ store: BCDStoreFront) { calls.append("signin") }
    func signOut(_ store: BCDStoreFront) { calls.append("signout") }
    func addGameFromFiles() { calls.append("add") }
    func refreshLibrary() { calls.append("refresh") }
}

/// A backend that answers `details` with the removal it was given; nothing else is asked of it.
@MainActor final class Backend: BCDGameDetailBackend {
    var onChange: (() -> Void)?
    var onNotice: (() -> Void)?
    var removal = BCDGameRemoval.none
    /// The DLC state a game not installed reports (BCDGameDLC.swift): the card's Install opens the page for it.
    var shown = BCDGameDetails()
    var asked: [Bool] = []
    func details(for game: BCDGame, settings: Bool) -> BCDGameDetails {
        asked.append(settings)
        var d = BCDGameDetails()
        d.removal = removal
        d.dlcOffer = shown.dlcOffer
        d.dlcCheck = shown.dlcCheck
        return d
    }
    func opened(_ game: BCDGame) {}
    func set(_ setting: String, to value: String, for game: BCDGame) {}
    func syncCloud(_ game: BCDGame) {}
    func keepCloudSaves(_ game: BCDGame, fromCloud: Bool) {}
    func resetToRecommended(_ game: BCDGame) {}
    func refreshRecommendedSettings(for game: BCDGame) async -> BCDRecommendedRefresh { .upToDate }
    func applyRecommendedSettings(_ game: BCDGame) {}
    func setTuning(_ on: Bool, for game: BCDGame) {}
    func undoTuning(_ game: BCDGame) {}
    func writeTuningLines(_ game: BCDGame) {}
    func tuningReport(for game: BCDGame) -> String? { nil }
    func shortcutLink(for game: BCDGame) -> String? { nil }
    func backUpSaves() async throws -> BCDSavesBackup { throw BCDGameError("no") }
    func restoreSaves(from file: URL) async throws -> Int { 0 }
    func lastLog(of game: BCDGame) async -> BCDGameLog? { nil }
    func logComment() async -> String? { "" }
    func send(_ log: BCDGameLog, of game: BCDGame, comment: String, progress: @escaping (String) -> Void) async throws -> String { "" }
    func configText(for game: BCDGame) -> String? { nil }
    func saveConfig(_ text: String, for game: BCDGame) {}
    func searchStore(_ text: String) async throws -> [BCDStoreMatch] { [] }
    func useStoreMatch(_ match: BCDStoreMatch, for game: BCDGame) {}
    func setCover(from file: URL, for game: BCDGame) async throws {}
}

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: Any = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok ? "" : "\n     \(detail)"))
    if !ok { failures += 1 }
}

@main struct Main {
    @MainActor static func main() {
        let actions = Recorder()
        let backend = Backend()
        BCDGameDetailSource.backend = backend
        let store = BCDStore(actions: actions)
        store.system.jit = .on
        store.system.freeDeviceBytes = 50_000_000_000
        let portal = BCDGame(id: "steam.620", title: "Portal 2", store: .steam, state: .ready, size: 12_800_000_000,
                             lastPlayed: Date())
        let wc3 = BCDGame(id: "local.1", title: "Warcraft III", store: .local, state: .ready)
        let hades = BCDGame(id: "steam.1145360", title: "Hades", store: .steam, state: .notInstalled, size: 15_000_000_000)
        let patch = BCDGame(id: "steam.7", title: "Patched", store: .steam, state: .updateAvailable(bytes: 0))
        let loading = BCDGame(id: "steam.8", title: "Loading", store: .steam, state: .downloading(progress: 0.3))
        store.games = [portal, wc3, hades, patch, loading]
        store.downloads = [BCDDownload(id: "steam.8", title: "Loading", store: .steam, doneBytes: 3, totalBytes: 10, bytesPerSecond: 1)]
        let menu = BCDMenuModel()
        menu.store = store
        func titles() -> [String] { menu.content?.items.map(\.title) ?? [] }
        func item(_ id: String) -> BCDMenuItem? { menu.content?.items.first { $0.id == id } }

        // What a game's menu offers.
        backend.removal = .uninstall(frees: 9_100_000_000)
        menu.showCard("steam.620")
        check("an installed Steam game: Play, Game page, Uninstall", titles() == ["Play", "Game page", "Uninstall"], titles())
        check("the menu is made from the page's details, without its sections (memory)", backend.asked == [false], backend.asked)
        check("Uninstall says what it frees and asks first, in the page's words",
              item("remove")?.detail == "Frees \(BCDFormat.bytes(9_100_000_000))" && item("remove")?.destructive == true
              && item("remove")?.question?.title == "Uninstall Portal 2?" && item("remove")?.question?.destructive == true,
              item("remove") as Any)
        check("Play and Game page do not ask", item("play")?.question == nil && item("page")?.question == nil)
        check("the header carries the game", menu.content?.title == "Portal 2" && menu.content?.game == portal)
        menu.dismiss()

        backend.removal = .removeFromLibrary
        menu.showCard("local.1")
        check("a game added from Files: Remove from library, which asks first",
              titles() == ["Play", "Game page", "Remove from library"]
              && item("remove")?.question?.title == "Remove Warcraft III from your library?", titles())
        menu.dismiss()

        backend.removal = .none
        menu.showCard("steam.1145360")
        check("a game not installed: Install, Game page, nothing to remove", titles() == ["Install", "Game page"], titles())
        menu.dismiss()
        // A game not installed whose DLC the player owns (or Steam is still being asked): the card's Install opens the game's page, where
        // the question is asked; otherwise it installs as it did.
        var pages: [String] = []
        menu.openPage = { pages.append($0.id) }
        backend.shown.dlcOffer = BCDDLCOffer(items: [BCDDLCOfferItem(id: 1, name: "Alpha", bytes: 500_000_000), BCDDLCOfferItem(id: 2, name: "Beta", bytes: 500_000_000)])
        menu.showCard("steam.1145360")
        menu.pick(item("install")!)
        menu.closed()?()
        check("Install on the card of a game with owned DLC opens its page instead of installing it alone",
              pages == ["steam.1145360"] && actions.calls.isEmpty, [pages, actions.calls])
        backend.shown.dlcOffer = nil
        backend.shown.dlcCheck = .checking
        menu.showCard("steam.1145360")
        menu.pick(item("install")!)
        menu.closed()?()
        check("... and while the DLC lookup still runs", pages == ["steam.1145360", "steam.1145360"] && actions.calls.isEmpty, [pages, actions.calls])
        backend.shown.dlcCheck = .none
        menu.showCard("steam.1145360")
        menu.pick(item("install")!)
        menu.closed()?()
        check("... with no DLC to ask about it installs, as before", pages.count == 2 && actions.calls == ["install steam.1145360 device"], [pages, actions.calls])
        actions.calls = []
        menu.openPage = nil
        menu.showCard("steam.7")
        check("an update: Play, Update, Game page", titles() == ["Play", "Update", "Game page"], titles())
        menu.dismiss()
        menu.showCard("steam.8")
        check("a running download: Pause, Game page", titles() == ["Pause", "Game page"], titles())
        menu.dismiss()
        store.downloads[0].paused = true
        menu.showCard("steam.8")
        check("a paused download: Resume", titles().first == "Resume", titles())
        menu.dismiss()
        menu.showCard("no.such.game")
        check("a game the store no longer lists opens nothing", menu.content == nil && !menu.isOpen)

        // Choosing.
        backend.removal = .uninstall(frees: nil)
        menu.showCard("steam.620")
        menu.showCard("local.1")
        check("a menu that is open is not replaced by a second one", menu.content?.title == "Portal 2")
        menu.pick(item("play")!)
        check("a choice closes the menu and waits for the host", menu.content == nil && !menu.isOpen && actions.calls.isEmpty)
        menu.closed()?()
        check("... then runs the store's action", actions.calls == ["play steam.620"], actions.calls)
        check("... once", menu.closed() == nil)

        menu.showCard("steam.620")
        menu.pick(item("remove")!)
        check("Uninstall asks: the question is up, the menu is not, and nothing ran",
              menu.content == nil && menu.ask?.question.title == "Uninstall Portal 2?" && menu.isOpen && actions.calls.count == 1)
        menu.answer(false)
        check("Cancel does nothing", !menu.isOpen && menu.closed() == nil && actions.calls.count == 1)
        menu.showCard("steam.620")
        menu.pick(item("remove")!)
        menu.answer(true)
        menu.closed()?()
        check("Uninstall runs the store's uninstall once it is confirmed", actions.calls.last == "uninstall steam.620", actions.calls)

        backend.removal = .removeFromLibrary
        menu.showCard("local.1")
        menu.pick(item("remove")!)
        menu.answer(true)
        menu.closed()?()
        check("Remove from library is the same store action", actions.calls.last == "uninstall local.1", actions.calls)

        backend.removal = .none
        menu.showCard("steam.1145360")
        menu.pick(item("install")!)
        menu.closed()?()
        check("Install installs on this device", actions.calls.last == "install steam.1145360 device", actions.calls)
        menu.showCard("steam.7")
        menu.pick(item("update")!)
        menu.closed()?()
        check("Update updates", actions.calls.last == "update steam.7", actions.calls)
        store.downloads[0].paused = false
        menu.showCard("steam.8")
        menu.pick(item("pause")!)
        menu.closed()?()
        check("Pause pauses the download", actions.calls.last == "pause steam.8", actions.calls)

        var opened: [String] = []
        menu.openPage = { opened.append($0.id) }
        menu.showCard("steam.620")
        menu.pick(item("page")!)
        check("Game page waits for the menu to close", opened.isEmpty)
        menu.closed()?()
        check("... then opens that game's page", opened == ["steam.620"], opened)

        // A sort menu: a plain list, the choice in force first.
        var chosen = ""
        menu.show(BCDMenuContent(title: "Sort by", items: [
            BCDMenuItem(id: "played", title: "Last played", symbol: "clock", checked: true, run: { chosen = "played" }),
            BCDMenuItem(id: "name", title: "Name", symbol: "textformat", run: { chosen = "name" }),
        ]))
        menu.pick(menu.content!.items[1])
        menu.closed()?()
        check("a plain menu runs its choice", chosen == "name" && !menu.isOpen, chosen)

        // X: the game under the ring.
        store.games = [portal, wc3]
        check("X on a Library card names its game", BCDMenuModel.gameID(focused: "library.game.steam.620", store: store) == "steam.620")
        check("X on a recent card names its game", BCDMenuModel.gameID(focused: "home.recent.local.1", store: store) == "local.1")
        check("Duo hero names its visible game even when another entry was played last",
              BCDMenuModel.gameID(focused: "home.continue.local.other", store: store) == "local.other")
        check("X on Continue playing names the game to continue", BCDMenuModel.gameID(focused: "home.continue", store: store) == "steam.620")
        check("X anywhere else names nothing",
              BCDMenuModel.gameID(focused: "library.add", store: store) == nil && BCDMenuModel.gameID(focused: "home.add", store: store) == nil
              && BCDMenuModel.gameID(focused: "menu.item.play", store: store) == nil && BCDMenuModel.gameID(focused: nil, store: store) == nil)

        var dismissedPrompt = 0
        let prompt = BCDMenuModel()
        prompt.show(BCDMenuContent(title: "Steam Cloud", items: [], message: "Choose saves", cancel: { dismissedPrompt += 1 }))
        prompt.dismiss()
        check("prompt B waits until its controller layer closes", dismissedPrompt == 0)
        prompt.closed()?()
        check("prompt cancellation runs exactly once after restoration", dismissedPrompt == 1)
        prompt.closed()?()
        check("a closed prompt cannot replay its callback", dismissedPrompt == 1)
        print(failures == 0 ? "menu rules: all passed" : "menu rules: \(failures) failed")
        exit(failures == 0 ? 0 : 1)
    }
}
