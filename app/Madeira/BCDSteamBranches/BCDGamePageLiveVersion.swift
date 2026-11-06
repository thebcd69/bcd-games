// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// The game page's Version section, engine side (BCDGameVersion.swift has the section's rules and words): which Steam games get it, the
// state it shows and what its rows do. Engine-bound like BCDGamePageLive.swift, so the preview build leaves it out (it lives in a
// folder of its own, which the preview build does not read).
//
// The branch list comes from BCD's own Steam sign-in (SteamOwnedLibrary.refreshBranches, the library's product info); a beta code is
// checked with Steam over the same connection; the downloads are BCD's own depot downloader's. Valve's client plays no part in
// choosing: BCD Dock starts the game through it, and it finds the install record naming the version.
// Keyed: `env.MADEIRA_STEAM_BRANCHES = 0` turns all of it off (no section, no lookup; a download takes the public build).

extension BCDGamePageLive {
    private func versionAppID(_ game: BCDGame) -> Int? {
        if case .steam(let id)? = BCDLiveID.parse(game.id) { return id }
        return nil
    }

    /// BCD's downloads own the install (BCD Dock's own library folder), or there is none yet.
    private func versionManaged(_ game: BCDGame, _ id: Int) -> Bool {
        guard game.state.isInstalled else { return true }
        guard let installed = SteamGamesModel.shared.games.first(where: { $0.id == id }) else { return false }
        return SteamInstallPaths.isManaged(library: installed.library)
    }

    /// The page opened: the game's branches are read from Steam once a run, so a beta added since the library was cached shows up.
    /// Nothing is asked for a game BCD did not download or while a game session holds the account.
    func versionOpened(_ game: BCDGame) {
        guard SteamOwnedLibrary.branchesEnabled, let id = versionAppID(game), versionManaged(game, id) else { return }
        Task { await SteamOwnedLibrary.shared.refreshBranches(id) }
    }

    /// Changes of the version state the page should read again.
    var versionChanges: AnyPublisher<Void, Never> {
        SteamOwnedLibrary.shared.branchesChanged.eraseToAnyPublisher()
    }

    /// The Version section, or nil when the game has no beta (or the feature is off, or BCD did not download the game).
    func versionGroup(_ game: BCDGame) -> BCDGameSettingGroup? {
        guard SteamOwnedLibrary.branchesEnabled, let id = versionAppID(game), versionManaged(game, id) else { return nil }
        let steam = SteamOwnedLibrary.shared
        guard let owned = steam.game(id), let listed = owned.branches else { return nil }
        let unlocked = Set(SteamBranch.unlocked(id))
        let choices = listed.filter { !$0.passwordRequired || unlocked.contains($0.name) }.map {
            BCDVersionChoice(name: $0.name, description: $0.description, protected: $0.passwordRequired)
        }
        let installed: String? = game.state.isInstalled ? (SteamGamesModel.shared.branches[id] ?? SteamAppInfo.publicBranch) : nil
        let state = BCDVersionState(
            choices: choices, chosen: steam.branchToDownload(id), installed: installed,
            hasProtected: listed.contains(where: \.passwordRequired) || !unlocked.isEmpty,
            codeKept: SteamBranch.code(id) != nil, codeNote: steam.betaCodeNotes[id],
            checking: steam.betaChecking.contains(id), signedIn: steam.signedIn, busy: steam.downloads[id] != nil)
        return BCDGameVersion.group(state)
    }

    /// A setting of the Version section. True when the setting was one of the section's.
    func versionSet(_ setting: String, to value: String, for game: BCDGame) -> Bool {
        guard setting == BCDGameVersion.choiceID || setting.hasPrefix(BCDGameVersion.choiceID + "."), let id = versionAppID(game) else { return false }
        let steam = SteamOwnedLibrary.shared
        guard SteamOwnedLibrary.branchesEnabled else { return true }
        switch setting {
        case BCDGameVersion.choiceID:
            // Only a version the section lists: the public build, an open beta, or a protected one whose code was entered.
            let unlocked = Set(SteamBranch.unlocked(id))
            let listed = steam.game(id)?.branches ?? []
            let allowed = value == SteamAppInfo.publicBranch || listed.contains { $0.name == value && (!$0.passwordRequired || unlocked.contains(value)) }
            guard allowed, steam.downloads[id] == nil else { return true }
            SteamBranch.choose(value, for: id)
            steam.branchesChanged.send()
        case BCDGameVersion.codeID:
            guard steam.signedIn else { steam.noteBetaCode("Sign in to Steam to enter a beta code.", appID: id); return true }
            steam.submitBetaCode(value, appID: id)
        case BCDGameVersion.forgetID:
            steam.forgetBetaCode(appID: id)
        case BCDGameVersion.downloadID:
            // The same call as an update or a repair: it takes the chosen version and keeps the files that match.
            guard steam.signedIn, steam.downloads[id] == nil else { return true }
            steam.install(id)
        default:
            break
        }
        return true
    }
}
