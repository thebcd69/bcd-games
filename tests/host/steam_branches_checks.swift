// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The game page's Version section (app/Madeira/BCDGameVersion.swift), compiled and run by tests/host/check-steam-branches.py with the
// page's rules (BCDGamePage.swift above its views) and BCDStore.swift. Synthetic states: no Steam, no network.

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: Any = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok ? "" : "\n     \(detail)"))
    if !ok { failures += 1 }
}

let publicBuild = BCDVersionChoice(name: "public")
let beta = BCDVersionChoice(name: "beta", description: "The next patch, for testing")
let secret = BCDVersionChoice(name: "playtest", description: "Private playtest", protected: true)

func setting(_ group: BCDGameSettingGroup?, _ id: String) -> BCDGameSetting? { group?.settings.first { $0.id == id } }

@main struct Harness {
    static func main() {
        // ---- the section is there only when Steam lists a beta (or one is chosen)
        let plain = BCDVersionState(choices: [publicBuild], chosen: "public", installed: "public")
        check("a game with only the public build has no Version section", BCDGameVersion.group(plain) == nil && !BCDGameVersion.offers(plain))
        check("a game Steam lists a beta for has one", BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "public", installed: nil)) != nil)
        check("a beta chosen earlier keeps the section even when the list lost the beta",
              BCDGameVersion.group(BCDVersionState(choices: [publicBuild], chosen: "beta", installed: nil)) != nil)
        check("a game that lists only a protected beta (no code yet) has the section, with the code row",
              BCDGameVersion.group(BCDVersionState(choices: [publicBuild], chosen: "public", installed: nil, hasProtected: true)).flatMap { setting($0, BCDGameVersion.codeID) } != nil)

        // ---- before the install: the choice, nothing to download
        let fresh = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "public", installed: nil))
        let choice = setting(fresh, BCDGameVersion.choiceID)
        check("the section is called Version, with its own symbol, and says Public", fresh?.id == "version" && fresh?.title == "Version" && fresh?.symbol == "arrow.triangle.branch" && fresh?.summary == "Public")
        check("the choice lists the public build first, as Steam does, and the beta with its description",
              choice?.choices.map(\.label) == ["Public (default)", "beta – The next patch, for testing"], choice?.choices.map(\.label) ?? [])
        check("the choice's value is the branch name and its default is public", choice?.value == "public" && choice?.defaultValue == "public" && choice?.isDefault == true)
        check("the choice says it applies to the next download, update or repair", choice?.note?.contains("next download, update or repair") == true)
        check("nothing is offered to download for a game that is not installed", setting(fresh, BCDGameVersion.downloadID) == nil && setting(fresh, BCDGameVersion.installedID) == nil)
        check("the public build has no warning", fresh?.note == nil)

        // ---- an installed game whose chosen version differs from the installed one
        let toBeta = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "beta", installed: "public"))
        let download = setting(toBeta, BCDGameVersion.downloadID)
        check("choosing a beta for a public install offers Download beta, behind a question",
              download?.title == "Download beta" && download?.kind == .action && download?.question?.title == "Switch to the beta version?" && download?.question?.destructive == false)
        check("the question says files that match stay and a beta can be unstable",
              download?.question?.message.contains("files that match stay") == true && download?.question?.message.contains("can be unstable") == true)
        check("the installed version is shown", setting(toBeta, BCDGameVersion.installedID)?.value == "Public")
        check("the beta carries the amber warning, and the headline says it is not downloaded yet",
              toBeta?.note?.hasPrefix("A beta build") == true && toBeta?.summary == "Beta: beta · not downloaded yet", toBeta?.summary ?? "")
        let back = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "public", installed: "beta"))
        check("going back to the public build is Download the public build, with its own question",
              setting(back, BCDGameVersion.downloadID)?.title == "Download the public build"
              && setting(back, BCDGameVersion.downloadID)?.question?.title == "Go back to the public build?"
              && setting(back, BCDGameVersion.downloadID)?.question?.message.contains("can be unstable") == false)
        let same = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "beta", installed: "beta"))
        check("a chosen version that is the installed one offers no download", setting(same, BCDGameVersion.downloadID) == nil && same?.summary == "Beta: beta")

        // ---- a download runs: the choice waits
        let busy = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "beta", installed: "public", busy: true))
        check("while the game downloads the choice and the download row wait, and say why",
              setting(busy, BCDGameVersion.choiceID)?.locked?.contains("download of this game is running") == true
              && setting(busy, BCDGameVersion.downloadID)?.locked != nil)

        // ---- protected betas and the beta code
        let hidden = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "public", installed: nil, hasProtected: true))
        check("a protected beta is not on the list until its code opened it",
              setting(hidden, BCDGameVersion.choiceID)?.choices.map(\.value) == ["public", "beta"])
        let code = setting(hidden, BCDGameVersion.codeID)
        check("the code row is typed text that is never shown: its value stays empty, its row says Type it in",
              code?.kind == .text(prompt: "Beta code") && code?.value == "" && code?.summary == "Type it in" && code?.title == "Enter a beta code")
        check("the code row says Steam checks the code and that it stays on this device",
              code?.note?.contains("Steam, which checks it") == true && code?.note?.contains("stays on this device") == true)
        check("no code kept: no remove row and no note row", setting(hidden, BCDGameVersion.forgetID) == nil && setting(hidden, BCDGameVersion.codeNoteID) == nil)
        let opened = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta, secret], chosen: "playtest", installed: "public",
                                                           hasProtected: true, codeKept: true, codeNote: "Opened: playtest."))
        check("an opened protected beta joins the list", setting(opened, BCDGameVersion.choiceID)?.choices.map(\.value) == ["public", "beta", "playtest"])
        check("with a code kept: Enter another, what Steam said, and a destructive Remove behind a question",
              setting(opened, BCDGameVersion.codeID)?.title == "Enter another beta code"
              && setting(opened, BCDGameVersion.codeNoteID)?.value == "Opened: playtest."
              && setting(opened, BCDGameVersion.forgetID)?.destructive == true && setting(opened, BCDGameVersion.forgetID)?.question?.title == "Remove the beta code?")
        check("a kept code without a note says one is kept",
              setting(BCDGameVersion.group(BCDVersionState(choices: [publicBuild, secret], chosen: "public", installed: nil, hasProtected: true, codeKept: true)), BCDGameVersion.codeNoteID)?.value == "A beta code is kept for this game.")
        let needs = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, secret], chosen: "playtest", installed: nil, hasProtected: true))
        check("a protected beta chosen without a kept code says it needs the code", needs?.note?.contains("needs its beta code") == true, needs?.note ?? "")
        let signedOut = setting(BCDGameVersion.group(BCDVersionState(choices: [publicBuild, secret], chosen: "public", installed: nil, hasProtected: true, signedIn: false)), BCDGameVersion.codeID)
        check("signed out of Steam the code row says to sign in", signedOut?.locked == "Sign in to Steam to enter a beta code.")
        let checking = setting(BCDGameVersion.group(BCDVersionState(choices: [publicBuild, secret], chosen: "public", installed: nil, hasProtected: true, checking: true)), BCDGameVersion.codeID)
        check("while Steam checks a code the row says so", checking?.locked?.contains("checking") == true)

        // ---- a version Steam no longer lists
        let gone = BCDGameVersion.group(BCDVersionState(choices: [publicBuild, beta], chosen: "old-beta", installed: "old-beta"))
        check("a version chosen earlier that is gone stays on the list, with a warning that the public build is taken next",
              setting(gone, BCDGameVersion.choiceID)?.choices.map(\.value) == ["public", "beta", "old-beta"]
              && gone?.note?.contains("isn't on Steam's list") == true, gone?.note ?? "")

        // ---- words
        let longBeta = BCDVersionChoice(name: "x", description: String(repeating: "a", count: 90))
        check("a long description is cut to 60 characters", BCDGameVersion.label(longBeta) == "x – " + String(repeating: "a", count: 60) + "…")
        check("a beta without a description is its name", BCDGameVersion.label(BCDVersionChoice(name: "alpha")) == "alpha")
        let every = [fresh, toBeta, back, opened, needs, gone, busy].compactMap { $0 }
        var words: [String] = []
        for group in every {
            words += [group.title, group.note ?? "", group.summary]
            for item in group.settings {
                words += [item.title, item.note ?? "", item.value, item.locked ?? ""]
                words += [item.question?.title ?? "", item.question?.message ?? ""]
                words += item.choices.map { $0.label }
            }
        }
        check("no \"Madeira\" in anything the section says", !words.contains { $0.localizedCaseInsensitiveContains("madeira") })
        let buying = ["buy", "purchase", "price", "checkout"]
        check("nothing to buy and no store link in the section",
              !words.contains { text in buying.contains { word in text.localizedCaseInsensitiveContains(word) } })
        check("every setting id of the section is version or version.<x>",
              every.flatMap(\.settings).allSatisfy { $0.id == "version" || $0.id.hasPrefix("version.") })
        check("the question for the download is not destructive and confirms with Download",
              BCDGameVersion.switchQuestion(BCDVersionState(choices: [publicBuild, beta], chosen: "beta", installed: "public")).confirm == "Download")

        print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
