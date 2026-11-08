// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The Steam Workshop pages' values, words and rules (app/Madeira/BCDWorkshop.swift), compiled and run by tests/host/check-steam-workshop.py with
// the page's rules (BCDGamePage.swift above its views) and BCDStore.swift. Synthetic items: no Steam, no network.

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: Any = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok ? "" : "\n     \(detail)"))
    if !ok { failures += 1 }
}

let content = BCDWorkshopListing(id: 1, title: "gm_construct 13", summary: "A map.", bytes: 48_000_000, kind: .content)
let collection = BCDWorkshopListing(id: 2, title: "Pack", kind: .collection, downloadable: false)
let removed = BCDWorkshopListing(id: 3, title: "Gone", kind: .removed, downloadable: false)
let guide = BCDWorkshopListing(id: 4, title: "A guide", kind: .notContent, downloadable: false)
let hidden = BCDWorkshopListing(id: 5, title: "Hidden", kind: .unavailable, downloadable: false)
let plain = BCDWorkshopStatus()

func buttons(_ item: BCDWorkshopListing, _ status: BCDWorkshopStatus, ready: Bool = true, installed: Bool = true) -> [BCDWorkshop.Action] {
    BCDWorkshop.actions(item, status, ready: ready, gameInstalled: installed)
}

@main struct Harness {
    static func main() {
        // ---- the lines under an item
        check("an item not downloaded says so, with its size", BCDWorkshop.caption(content, plain).text == "Not downloaded · 48 MB", BCDWorkshop.caption(content, plain).text)
        check("one without a size says only Not downloaded", BCDWorkshop.caption(BCDWorkshopListing(id: 9, title: "x"), plain).text == "Not downloaded")
        check("installed shows the size, and an update is amber",
              BCDWorkshop.caption(content, BCDWorkshopStatus(state: .installed(bytes: 5_000_000, update: false))) == ("Installed · 5 MB", false)
              && BCDWorkshop.caption(content, BCDWorkshopStatus(state: .installed(bytes: 5_000_000, update: true))) == ("Installed · 5 MB · Update available", true))
        check("installed without a size says Installed", BCDWorkshop.caption(content, BCDWorkshopStatus(state: .installed(bytes: 0, update: false))).text == "Installed")
        check("a download shows its percentage of the whole, waiting and failed say so",
              BCDWorkshop.caption(content, BCDWorkshopStatus(state: .downloading(fraction: 0.456, totalBytes: 48_000_000))).text == "45% of 48 MB"
              && BCDWorkshop.caption(content, BCDWorkshopStatus(state: .downloading(fraction: 0, totalBytes: 0))).text == "Preparing…"
              && BCDWorkshop.caption(content, BCDWorkshopStatus(state: .waiting)).text == "Waiting to download…"
              && BCDWorkshop.caption(content, BCDWorkshopStatus(state: .failed("No room."))) == ("No room.", true))
        check("a collection, a removed item, a guide and a hidden item are told apart",
              BCDWorkshop.caption(collection, plain).text == "Collection: subscribe to its items on Steam"
              && BCDWorkshop.caption(removed, plain) == ("Removed by Steam", true)
              && BCDWorkshop.caption(guide, plain).text == "Not game content" && BCDWorkshop.caption(hidden, plain).text == "Not available on Steam")

        // ---- what the card offers
        check("an item not on the device offers Download and Subscribe (the account is not touched by the first)",
              buttons(content, plain) == [.download, .subscribe])
        check("a subscribed item that is installed offers Download again, both unsubscribes",
              buttons(content, BCDWorkshopStatus(state: .installed(bytes: 1, update: false), subscribed: true)) == [.update, .unsubscribeAndRemove, .unsubscribeKeep])
        check("with an update the button says Update, without it Download again",
              BCDWorkshop.title(.update, BCDWorkshopStatus(state: .installed(bytes: 1, update: true))) == "Update"
              && BCDWorkshop.title(.update, BCDWorkshopStatus(state: .installed(bytes: 1, update: false))) == "Download again")
        check("a running download offers only Stop (and the account actions)",
              buttons(content, BCDWorkshopStatus(state: .downloading(fraction: 0.5, totalBytes: 10), subscribed: true)) == [.stop, .unsubscribeAndRemove])
        check("an installed item the account is not subscribed to offers Remove the files, not an unsubscribe",
              buttons(content, BCDWorkshopStatus(state: .installed(bytes: 1, update: false))).contains(.remove)
              && !buttons(content, BCDWorkshopStatus(state: .installed(bytes: 1, update: false))).contains(.unsubscribeKeep))
        check("while Steam cannot be asked (a game runs) no download and no account action is offered",
              buttons(content, plain, ready: false) == [] && buttons(content, BCDWorkshopStatus(state: .installed(bytes: 1, update: false), subscribed: true), ready: false) == [])
        check("a game that is not installed gets no download and no subscribe", buttons(content, plain, installed: false) == [])
        check("a collection can be viewed on Steam but not subscribed to here", buttons(collection, plain) == [])
        check("an item that cannot be downloaded offers no download", !buttons(removed, plain).contains(.download) && !buttons(guide, plain).contains(.download))

        // ---- what asks first
        check("subscribing asks, and says it changes the Steam account and shows on the PC",
              BCDWorkshop.question(.subscribe, content)?.message.contains("Steam account's subscriptions") == true
              && BCDWorkshop.question(.subscribe, content)?.message.contains("shows on your PC too") == true
              && BCDWorkshop.question(.subscribe, content)?.destructive == false)
        check("unsubscribing asks, in both forms; the one that removes files is destructive",
              BCDWorkshop.question(.unsubscribeAndRemove, content)?.destructive == true && BCDWorkshop.question(.unsubscribeKeep, content)?.destructive == false
              && BCDWorkshop.question(.unsubscribeKeep, content)?.message.contains("files stay") == true)
        check("removing files asks, and says the subscriptions stay",
              BCDWorkshop.question(.remove, content)?.message.contains("subscriptions stay as they are") == true && BCDWorkshop.question(.remove, content)?.destructive == true)
        check("a download, an update and a stop go ahead at once (they change no account)",
              BCDWorkshop.question(.download, content) == nil && BCDWorkshop.question(.update, content) == nil && BCDWorkshop.question(.stop, content) == nil)
        check("the item's name is in every question that names it", [BCDWorkshop.Action.subscribe, .unsubscribeKeep, .unsubscribeAndRemove, .remove].allSatisfy {
            BCDWorkshop.question($0, content)?.message.contains("gm_construct 13") == true })

        // ---- the page's words
        check("the page says where items go and that subscribing changes the account", BCDWorkshop.accountNote.contains("Workshop folder beside the game")
              && BCDWorkshop.accountNote.contains("change your Steam account"))
        check("download all, installed counts",
              BCDWorkshop.downloadAllTitle(1) == "Download 1 item" && BCDWorkshop.downloadAllTitle(3) == "Download 3 items"
              && BCDWorkshop.installedCount(0) == "No items installed" && BCDWorkshop.installedCount(1) == "1 item installed" && BCDWorkshop.installedCount(4) == "4 items installed")
        check("why the page cannot ask Steam: signed out, a game running, nothing when it can",
              BCDWorkshop.notReady(BCDWorkshopPageState(installed: true, signedIn: false, ready: false))?.contains("Sign in to Steam") == true
              && BCDWorkshop.notReady(BCDWorkshopPageState(installed: true, signedIn: true, ready: false))?.contains("while a game runs") == true
              && BCDWorkshop.notReady(BCDWorkshopPageState(installed: true, signedIn: true, ready: true)) == nil
              && BCDWorkshop.notReady(BCDWorkshopPageState(installed: false, signedIn: false, ready: false)) == nil)
        check("a picture address is https: http becomes https, anything else is refused",
              BCDWorkshop.secure("http://cloud-3.steamusercontent.com/ugc/1/")?.absoluteString == "https://cloud-3.steamusercontent.com/ugc/1/"
              && BCDWorkshop.secure("https://images.steamusercontent.com/ugc/1/") != nil
              && BCDWorkshop.secure("file:///etc/passwd") == nil && BCDWorkshop.secure("") == nil && BCDWorkshop.secure("ftp://x/y") == nil)
        check("Browse lists the three orders by their Steam names",
              BCDWorkshopOrder.allCases.map(\.title) == ["Popular this week", "Most subscribed", "Most recent"])
        check("the route ids are stable", BCDWorkshopRoute.area.id == "area" && BCDWorkshopRoute.game(4000).id == "game.4000")

        // ---- no \"Madeira\", nothing to buy
        var words: [String] = [BCDWorkshop.accountNote, BCDWorkshop.areaNote]
        for item in [content, collection, removed] {
            for action in BCDWorkshop.Action.allCases {
                words.append(BCDWorkshop.title(action, plain))
                if let question = BCDWorkshop.question(action, item) { words += [question.title, question.message, question.confirm] }
            }
        }
        check("no \"Madeira\" in anything the pages say", !words.contains { $0.localizedCaseInsensitiveContains("madeira") })
        let buying = ["buy", "purchase", "price", "checkout", "cart"]
        check("nothing to buy", !words.contains { text in buying.contains { word in text.localizedCaseInsensitiveContains(word) } })

        print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
