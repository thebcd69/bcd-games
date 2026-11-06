// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// A Steam game's version on its page (docs/BCD-UI-ARCHITECTURE.md; docs/STEAM_LIBRARY.md, "Version"): Options' Version section, in
// BCD's words, like Steam's own Betas tab. No views and no engine types: the live backend (BCDSteamBranches/BCDGamePageLiveVersion.swift)
// fills the state in and carries the choices out, the page's own list draws the section, and the preview app builds the same section
// from sample rows (tests/host/check-steam-branches.py compiles this file with BCDGamePage.swift's rules).
//
// The section exists only for a game Steam lists a beta for (or one the player chose a beta of before). Choosing a version changes
// nothing by itself: it applies to the game's next download, update or repair, or at once through the section's Download row.

/// One version the player may pick: Steam's public build or one of the game's beta branches.
struct BCDVersionChoice: Equatable {
    /// Steam's branch name ("public", "preview", ...).
    var name: String
    /// What the developer wrote for the beta; empty for the public build.
    var description = ""
    /// A password-protected beta (its code opens it).
    var protected = false
}

/// The section's data.
struct BCDVersionState: Equatable {
    /// The versions the player can pick, public first: the public build, the open betas, the protected betas whose code was entered.
    var choices: [BCDVersionChoice]
    /// The branch the next download takes ("public" without a choice).
    var chosen: String
    /// The branch the install on this device names; nil before the game is installed.
    var installed: String?
    /// The game lists a protected beta (or a code is kept): the section offers the beta-code row.
    var hasProtected = false
    /// A beta code is kept for the game.
    var codeKept = false
    /// What the last beta-code check said (shown under the row); nil before one.
    var codeNote: String? = nil
    /// Steam is checking a beta code now.
    var checking = false
    /// BCD is signed in to Steam (a code is checked with Steam).
    var signedIn = true
    /// A download of this game runs: the section waits for it.
    var busy = false

    static let publicName = "public"
    var chosenIsPublic: Bool { chosen == Self.publicName }
    /// The chosen version is not the installed one: the Download row appears.
    var differs: Bool { installed.map { $0 != chosen } ?? false }
}

enum BCDGameVersion {
    /// The section's settings: the choice, the beta-code rows and the Download row.
    static let choiceID = "version", codeID = "version.code", forgetID = "version.forget", downloadID = "version.download"
    static let installedID = "version.installed", codeNoteID = "version.codenote"

    /// The section is there when Steam lists a beta for the game, or the player has a beta chosen (a beta that was dropped must
    /// still be shown, so it can be put right).
    static func offers(_ state: BCDVersionState) -> Bool {
        state.choices.count > 1 || !state.chosenIsPublic || state.hasProtected
    }

    static func label(_ choice: BCDVersionChoice) -> String {
        if choice.name == BCDVersionState.publicName { return "Public (default)" }
        let text = choice.description.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return choice.name }
        let short = text.count > 60 ? String(text.prefix(60)) + "…" : text
        return "\(choice.name) – \(short)"
    }

    /// "Public" or the beta's name, as the section's row and the page say it.
    static func name(_ branch: String) -> String { branch == BCDVersionState.publicName ? "Public" : branch }

    /// The words under the choice.
    static func choiceNote(_ state: BCDVersionState) -> String {
        var note = "Which version of the game BCD downloads, as in the Betas tab of Steam. It applies to the next download, update or repair."
        if state.installed != nil { note += " The files that match stay; only what differs is downloaded." }
        return note
    }

    /// The amber line at the top of the section: what to know about the chosen version; nil for the public build.
    static func warning(_ state: BCDVersionState) -> String? {
        guard !state.chosenIsPublic else { return nil }
        if let choice = state.choices.first(where: { $0.name == state.chosen }) {
            return choice.protected && !state.codeKept
                ? "This beta needs its beta code. Enter it below, or pick another version."
                : "A beta build: it can be unstable, and its saves may not work on the public build."
        }
        return "The version chosen earlier isn't on Steam's list for this game now. The next download takes the public build."
    }

    /// The question before a download that changes the installed version.
    static func switchQuestion(_ state: BCDVersionState) -> BCDGameQuestion {
        let target = name(state.chosen)
        let goingPublic = state.chosenIsPublic
        return BCDGameQuestion(
            title: goingPublic ? "Go back to the public build?" : "Switch to the \(target) version?",
            message: "BCD compares the game's files with that version and downloads what differs; files that match stay."
                + (goingPublic ? "" : " A beta can be unstable, and its saves may not work on the public build.")
                + " The install record then names this version, as Steam's own client writes it.",
            confirm: "Download", destructive: false)
    }

    static func forgetQuestion() -> BCDGameQuestion {
        BCDGameQuestion(title: "Remove the beta code?",
                        message: "BCD forgets the code, and the protected betas it opened leave the list. A game already installed from one stays as it is.",
                        confirm: "Remove", destructive: true)
    }

    /// Options' Version section, or nil when the game has no beta to pick.
    static func group(_ state: BCDVersionState) -> BCDGameSettingGroup? {
        guard offers(state) else { return nil }
        let lock = state.busy ? "A download of this game is running. Change its version when it has finished." : nil
        var list = state.choices.map { BCDGameChoice(value: $0.name, label: label($0)) }
        // A version chosen earlier that Steam no longer lists (or whose code is gone) stays on the list, so it can be put right.
        if !state.choices.contains(where: { $0.name == state.chosen }) {
            list.append(BCDGameChoice(value: state.chosen, label: state.chosen))
        }
        var settings = [BCDGameSetting(id: choiceID, title: "Version", kind: .choice(list), value: state.chosen,
                                       defaultValue: BCDVersionState.publicName, note: choiceNote(state), locked: lock)]
        settings[0].heading = "Version"
        if let installed = state.installed {
            settings.append(BCDGameSetting(id: installedID, title: "Installed", kind: .info, value: name(installed)))
        }
        if state.differs {
            let title = state.chosenIsPublic ? "Download the public build" : "Download \(name(state.chosen))"
            settings.append(BCDGameSetting(id: downloadID, title: title, kind: .action,
                                           note: "Replaces the files that differ, now.", locked: lock,
                                           question: switchQuestion(state), heading: "Change"))
        }
        if state.hasProtected || state.codeKept {
            let codeLock = !state.signedIn ? "Sign in to Steam to enter a beta code." : (state.checking ? "Steam is checking the code…" : nil)
            settings.append(BCDGameSetting(
                id: codeID, title: state.codeKept ? "Enter another beta code" : "Enter a beta code",
                kind: .text(prompt: "Beta code"), value: "",
                note: "The code the game's developer gave for a private beta. BCD sends it to Steam, which checks it; the code stays on this device.",
                locked: codeLock, heading: "Beta code", blank: "Type it in"))
            if let note = state.codeNote ?? (state.codeKept ? "A beta code is kept for this game." : nil) {
                settings.append(BCDGameSetting(id: codeNoteID, title: "Beta code", kind: .info, value: note))
            }
            if state.codeKept {
                settings.append(BCDGameSetting(id: forgetID, title: "Remove the beta code", kind: .action,
                                               question: forgetQuestion(), destructive: true))
            }
        }
        var headline = state.chosenIsPublic ? "Public" : "Beta: \(state.chosen)"
        if state.differs { headline += " · not downloaded yet" }
        return BCDGameSettingGroup(id: "version", title: "Version", symbol: "arrow.triangle.branch", settings: settings,
                                   note: warning(state), headline: headline)
    }
}
