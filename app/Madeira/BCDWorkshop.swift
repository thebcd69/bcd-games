// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Steam Workshop in BCD (docs/BCD-UI-ARCHITECTURE.md; docs/STEAM_LIBRARY.md, "Steam Workshop"): the values, the words and the rules of the
// Workshop pages, with no views and no engine types. A game's Workshop page (Options › Steam › Steam Workshop) and the Library's Workshop area
// (BCDWorkshopView.swift) read and ask through BCDWorkshopBackend, which BCDSteamWorkshop/BCDWorkshopLive.swift carries out on the app's own Steam
// connection and the preview app on sample data. tests/host/check-steam-workshop.py compiles this file with the page's rules.
//
// Nothing here happens by itself. A page asks Steam for the account's subscribed items when it opens; downloads, subscribing and unsubscribing
// happen only when the player presses them, and subscribing and unsubscribing, which change the Steam account, ask a question first.

// MARK: - What a page shows

/// One Workshop item, the part that does not change while it is on screen.
struct BCDWorkshopListing: Identifiable, Hashable {
    enum Kind: Hashable {
        /// Content a game loads.
        case content
        /// A collection of items: its items are subscribed to on Steam.
        case collection
        /// Removed by Steam.
        case removed
        /// Not game content (guides, art, videos).
        case notContent
        /// One Steam does not offer this account.
        case unavailable
    }
    var id: UInt64
    var title: String
    var summary = ""
    /// Steam's picture of it (https), when there is one.
    var preview: URL? = nil
    /// Its size on Steam, when Steam states one.
    var bytes: UInt64 = 0
    var kind = Kind.content
    /// There are files to download (Steam answered for it and it is content).
    var downloadable = true
    /// Its page on Steam.
    var link: URL? = nil
}

/// Where an item stands on this device.
enum BCDWorkshopState: Equatable {
    case notDownloaded
    case waiting
    case downloading(fraction: Double, totalBytes: UInt64)
    case failed(String)
    case installed(bytes: UInt64, update: Bool)
}

/// An item's state and whether the Steam account is subscribed to it.
struct BCDWorkshopStatus: Equatable {
    var state = BCDWorkshopState.notDownloaded
    var subscribed = false

    var installed: Bool { if case .installed = state { return true }; return false }
    var updateAvailable: Bool { if case .installed(_, let update) = state { return update }; return false }
    var running: Bool {
        switch state {
        case .waiting, .downloading: return true
        default: return false
        }
    }
}

/// One game's Workshop page as the backend reads it.
struct BCDWorkshopPageState: Equatable {
    /// The game is installed (its items go beside it).
    var installed = false
    var signedIn = false
    /// Workshop calls can be made now: signed in and no game session holds the account.
    var ready = false
    /// The subscribed list is being read.
    var loading = false
    /// The account's subscribed items for the game, in Steam's order; nil before they were read.
    var subscribed: [BCDWorkshopListing]? = nil
    /// Items on this device that the account is not subscribed to.
    var others: [BCDWorkshopListing] = []
    /// Subscribed items not on disk or with a newer version on Steam.
    var pending = 0
    /// What the last request that failed said, in BCD's words; nil when nothing failed.
    var problem: String? = nil
}

/// One game in the Workshop area.
struct BCDWorkshopGame: Identifiable, Equatable {
    var id: Int
    var title: String
    var installedItems = 0
    /// Steam says the game has a Workshop (or items are installed); false: Steam has not said.
    var hasWorkshop = true
    /// Subscribed items waiting to be downloaded (known once the game's page was read).
    var pending = 0
}

/// What Browse lists.
enum BCDWorkshopOrder: String, CaseIterable, Identifiable {
    case popular, subscribed, recent
    var id: String { rawValue }
    var title: String {
        switch self {
        case .popular: return "Popular this week"
        case .subscribed: return "Most subscribed"
        case .recent: return "Most recent"
        }
    }
}

/// One page of Browse.
struct BCDWorkshopBrowsePage: Equatable {
    var total = 0
    var items: [BCDWorkshopListing] = []
}

/// Where the Workshop cover opens: the area, or one game's page.
enum BCDWorkshopRoute: Equatable, Identifiable {
    case area
    case game(Int)
    var id: String {
        switch self {
        case .area: return "area"
        case .game(let id): return "game.\(id)"
        }
    }
}

// MARK: - The backend

@MainActor protocol BCDWorkshopBackend: AnyObject {
    /// Set by the open page: anything it shows may have changed, so it reads again.
    var onChange: (() -> Void)? { get set }
    /// The Workshop is part of this copy and BCD is signed in to Steam: the Library offers the area.
    var available: Bool { get }
    /// The installed Steam games, those with a Workshop first.
    func games() -> [BCDWorkshopGame]
    func page(_ appID: Int) -> BCDWorkshopPageState
    func status(_ item: UInt64, appID: Int) -> BCDWorkshopStatus
    /// The page opened: the install record is read again, and Steam is asked for the subscribed items (read-only).
    func opened(_ appID: Int)
    /// One page of the game's Workshop (a read; nothing changes on the account).
    func browse(_ appID: Int, order: BCDWorkshopOrder, text: String, page: Int) async throws -> BCDWorkshopBrowsePage
    /// Subscribes on the Steam account and downloads the item. Only called after the player's yes.
    func subscribe(_ item: BCDWorkshopListing, appID: Int)
    /// Unsubscribes on the Steam account; with `removeFiles` the item's files go from this device too. Only called after the player's yes.
    func unsubscribe(_ item: BCDWorkshopListing, appID: Int, removeFiles: Bool)
    func download(_ item: BCDWorkshopListing, appID: Int)
    func downloadAll(_ appID: Int)
    func cancel(_ item: UInt64)
    /// Deletes an item's files from this device (the account's subscriptions stay as they are).
    func remove(_ item: BCDWorkshopListing, appID: Int)
    func clearProblem()
}

/// Where the pages find their backend: BCDWorkshopLive, found by its Objective-C name (it touches the engine, so the preview build leaves it
/// out); the preview app sets its sample here.
@MainActor enum BCDWorkshopSource {
    static var backend: BCDWorkshopBackend? =
        (NSClassFromString("BCDWorkshopLive") as? NSObject.Type)?.init() as? BCDWorkshopBackend
}

// MARK: - Words and rules

enum BCDWorkshop {
    /// The line under every page: what happens to the account and to the files.
    static let accountNote = "Items download from Steam with your account into Steam's Workshop folder beside the game, so the game finds them when it starts "
        + "through BCD Dock. Subscribing and unsubscribing change your Steam account, the same as on a PC."

    static let areaNote = "Your installed Steam games, those with a Workshop first. Items are your Steam account's: what you subscribe to here shows on your PC too."

    static func bytes(_ value: UInt64) -> String { BCDFormat.bytes(Int64(clamping: value)) }

    /// "Installed · 12 MB · Update available", "Not downloaded · 3 MB", and so on; `warning` for the amber ones.
    static func caption(_ item: BCDWorkshopListing, _ status: BCDWorkshopStatus) -> (text: String, warning: Bool) {
        switch status.state {
        case .waiting: return ("Waiting to download…", false)
        case .downloading(let fraction, let total):
            return (total > 0 ? "\(Int((fraction * 100).rounded(.down)))% of \(bytes(total))" : "Preparing…", false)
        case .failed(let why): return (why, true)
        case .installed(let size, let update):
            let installed = size > 0 ? "Installed · \(bytes(size))" : "Installed"
            return update ? (installed + " · Update available", true) : (installed, false)
        case .notDownloaded:
            switch item.kind {
            case .collection: return ("Collection: subscribe to its items on Steam", false)
            case .removed: return ("Removed by Steam", true)
            case .notContent: return ("Not game content", false)
            case .unavailable: return ("Not available on Steam", false)
            case .content: break
            }
            if !item.downloadable { return ("Not available on Steam", false) }
            return (item.bytes > 0 ? "Not downloaded · \(bytes(item.bytes))" : "Not downloaded", false)
        }
    }

    /// The items to show above the rest: the number of subscribed items waiting, in the button's words.
    static func downloadAllTitle(_ pending: Int) -> String { pending == 1 ? "Download 1 item" : "Download \(pending) items" }

    static func installedCount(_ count: Int) -> String {
        count == 0 ? "No items installed" : count == 1 ? "1 item installed" : "\(count) items installed"
    }

    /// Why the page cannot ask Steam now, or nil.
    static func notReady(_ state: BCDWorkshopPageState) -> String? {
        if !state.installed { return nil }
        if !state.signedIn { return "Sign in to Steam (Settings › Accounts) to see your Workshop items." }
        if !state.ready { return "Not available while a game runs. Steam gives your account to the game until it ends." }
        return nil
    }

    // MARK: Questions (what changes the Steam account asks first)

    static func subscribeQuestion(_ item: BCDWorkshopListing) -> BCDGameQuestion {
        BCDGameQuestion(
            title: "Subscribe to this item?",
            message: "\"\(item.title)\" is added to your Steam account's subscriptions, as the Subscribe button on Steam's Workshop does, "
                + "so it shows on your PC too. BCD then downloads it" + (item.bytes > 0 ? " (\(bytes(item.bytes)))" : "") + " beside the game.",
            confirm: "Subscribe", destructive: false)
    }

    /// Unsubscribing: the files can stay or go. `removeFiles` picks which question to ask.
    static func unsubscribeQuestion(_ item: BCDWorkshopListing, removeFiles: Bool) -> BCDGameQuestion {
        BCDGameQuestion(
            title: removeFiles ? "Unsubscribe and remove?" : "Unsubscribe and keep the files?",
            message: "\"\(item.title)\" leaves your Steam account's subscriptions, so it leaves your PC's too. "
                + (removeFiles ? "BCD deletes its files from this device." : "Its files stay on this device."),
            confirm: "Unsubscribe", destructive: removeFiles)
    }

    static func removeQuestion(_ item: BCDWorkshopListing) -> BCDGameQuestion {
        BCDGameQuestion(title: "Remove the files?", message: "BCD deletes \"\(item.title)\" from this device. Your Steam account's subscriptions stay as they are.",
                        confirm: "Remove", destructive: true)
    }

    // MARK: What an item's card offers

    enum Action: Hashable, CaseIterable {
        case download, update, stop, subscribe, unsubscribeAndRemove, unsubscribeKeep, remove
    }

    /// The actions the card offers an item, in order. `ready`: Steam can be asked now; the file actions need an installed game.
    static func actions(_ item: BCDWorkshopListing, _ status: BCDWorkshopStatus, ready: Bool, gameInstalled: Bool) -> [Action] {
        var list: [Action] = []
        if status.running {
            list.append(.stop)
        } else if ready, gameInstalled, item.downloadable {
            if !status.installed {
                list.append(.download)
            } else {
                list.append(.update)   // "Update" when Steam has a newer version, "Download again" when not (the title says which)
            }
        }
        if ready {
            if status.subscribed {
                list.append(.unsubscribeAndRemove)
                if status.installed { list.append(.unsubscribeKeep) }
            } else if item.kind == .content, gameInstalled {
                list.append(.subscribe)
            }
        }
        if !status.subscribed, status.installed { list.append(.remove) }
        return list
    }

    static func title(_ action: Action, _ status: BCDWorkshopStatus) -> String {
        switch action {
        case .download: return "Download"
        case .update: return status.updateAvailable ? "Update" : "Download again"
        case .stop: return "Stop the download"
        case .subscribe: return "Subscribe"
        case .unsubscribeAndRemove: return "Unsubscribe and remove"
        case .unsubscribeKeep: return "Unsubscribe, keep the files"
        case .remove: return "Remove the files"
        }
    }

    static func symbol(_ action: Action) -> String {
        switch action {
        case .download: return "arrow.down.circle"
        case .update: return "arrow.triangle.2.circlepath"
        case .stop: return "stop.circle"
        case .subscribe: return "plus.circle"
        case .unsubscribeAndRemove, .unsubscribeKeep: return "minus.circle"
        case .remove: return "trash"
        }
    }

    /// The question an action asks first; nil: it goes ahead at once (a download or a stop changes no account).
    static func question(_ action: Action, _ item: BCDWorkshopListing) -> BCDGameQuestion? {
        switch action {
        case .subscribe: return subscribeQuestion(item)
        case .unsubscribeAndRemove: return unsubscribeQuestion(item, removeFiles: true)
        case .unsubscribeKeep: return unsubscribeQuestion(item, removeFiles: false)
        case .remove: return removeQuestion(item)
        case .download, .update, .stop: return nil
        }
    }

    /// An https address for a Steam picture: Steam sometimes hands out http ones, which BCD's picture cache refuses.
    static func secure(_ address: String) -> URL? {
        guard !address.isEmpty else { return nil }
        let text = address.hasPrefix("http://") ? "https://" + address.dropFirst("http://".count) : address
        guard let url = URL(string: text), url.scheme == "https", url.host != nil else { return nil }
        return url
    }
}
