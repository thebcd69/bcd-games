// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 Vugar: code taken from https://github.com/willfaust/Madeira (pull request #572, branch steam-friends-pr-572-replacement)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// Steam Friends in BCD's in-game menu (Game › Friends), for a game started through BCD Dock with a Friends status chosen in
// Settings › Accounts › Steam. It is Madeira's Friends page (its Session menu's friendsMenu and DockFriendRow) as rows of BCD's menu:
// the status as chips, then the friends; a friend's row opens a question with what can be done (Accept, Join, Invite).
//
// Every row's id starts with "fr." (DockFriendsMenu.handles). BCDLibraryMenuSource (BCDSessionLive.swift) calls `section` for the Game
// tab, and `page`, `choose`, `answer`, `pageShown` and `menuClosed` for the ids that are ours. Nothing is read in a session that was
// started without a Friends status (MadeiraDock.friendsRequested): no file is opened and the menu has no Friends row, as before.
// SteamIDs and names never leave DockFriends: a row's id carries a number of this menu's own, not the SteamID.
@MainActor enum DockFriendsMenu {
    static let pageID = "fr.page"
    static func handles(_ row: String) -> Bool { row.hasPrefix("fr.") }

    private static var friends: DockFriends { DockFriends.shared }

    // MARK: Reading

    /// The feed is read at most once a second for the menu's own rows; the open page reads it on its timer.
    private static var lastRead = Date.distantPast
    private static func read(force: Bool = false) {
        let now = Date()
        guard force || now.timeIntervalSince(lastRead) >= 1 else { return }
        lastRead = now
        friends.refresh(now: now)
    }

    /// A friend's number in this menu (row ids and nothing else): the same friend keeps it while the menu is open.
    private static var numbers: [UInt64: Int] = [:]
    private static func number(of steamID: UInt64) -> Int {
        if let n = numbers[steamID] { return n }
        let n = numbers.count + 1
        numbers[steamID] = n
        return n
    }
    private static func friend(row: String) -> DockFriends.Friend? {
        guard row.hasPrefix("fr.f."), let n = Int(row.dropFirst(5)),
              let id = numbers.first(where: { $0.value == n })?.key else { return nil }
        return friends.friends.first { $0.steamID == id }
    }

    // MARK: The Game tab's row

    /// "Friends" under the Game tab: only in a session started with a Friends status, and only while Dock's list is current.
    static func section() -> BCDMenuSection? {
        guard MadeiraDock.friendsRequested else { return nil }
        read()
        guard friends.available else { return nil }
        let online = friends.friends.filter { $0.state > 0 }.count
        let invited = friends.friends.filter(\.invitedYou).count
        let value = invited > 0 ? (invited == 1 ? "1 invite" : "\(invited) invites") : online > 0 ? "\(online) online" : nil
        return BCDMenuSection(id: "fr.section", rows: [
            BCDMenuRow(id: "fr.open", title: "Friends", detail: "Your Steam friends: join one in this game, or invite one to yours.",
                       symbol: "person.2", kind: .page(pageID, value: value)),
        ])
    }

    // MARK: The page

    static func page(_ id: String) -> BCDMenuPage? {
        guard id == pageID else { return nil }
        read()
        var sections: [BCDMenuSection] = []
        let busy = friends.pending != nil
        // Offline is not offered here: it is chosen before a start and leaves Friends off for the session.
        let choices = SteamOnlineStatus.allCases.filter { $0 != .offline }.map { BCDChoice(id: String($0.rawValue), title: $0.title) }
        var statusRows = [BCDMenuRow(id: "fr.status", title: "Your status", detail: "What your Steam friends see while you play.",
                                     kind: .choice(choices, selected: String(shownStatus.rawValue)), enabled: friends.available && !busy)]
        if let outcome = friends.outcome, outcome.action == .setStatus {
            statusRows.append(BCDMenuRow(id: "fr.status.note", title: DockFriends.message(action: .setStatus, result: outcome.result),
                                         kind: .note(outcome.result == 1 ? .good : .warm)))
        }
        sections.append(BCDMenuSection(id: "fr.status", rows: statusRows))
        if friends.friends.isEmpty {
            sections.append(BCDMenuSection(id: "fr.none", rows: [
                BCDMenuRow(id: "fr.none", title: friends.available ? "No friends yet" : "Friends are available while the game runs", kind: .note(.soft)),
            ]))
            return BCDMenuPage(id: pageID, title: "Friends", sections: sections)
        }
        let invited = friends.friends.filter(\.invitedYou)
        let others = friends.friends.filter { !$0.invitedYou }
        if !invited.isEmpty { sections.append(BCDMenuSection(id: "fr.invited", title: "Invited you", rows: invited.map { row($0, busy: busy) })) }
        if !others.isEmpty { sections.append(BCDMenuSection(id: "fr.list", title: "Friends", rows: others.map { row($0, busy: busy) })) }
        return BCDMenuPage(id: pageID, title: "Friends", sections: sections)
    }

    /// The status the chips show: the one last chosen (the launch's, or a change made here that Dock took).
    private static var shownStatus: SteamOnlineStatus {
        let current = SteamOnlineStatus.current
        return current == .offline ? .online : current
    }

    private static func row(_ friend: DockFriends.Friend, busy: Bool) -> BCDMenuRow {
        let name = friend.name.isEmpty ? "Unknown" : friend.name
        var answers: [BCDConfirm.Answer] = []
        if friend.invitedYou { answers.append(.init(id: "join", title: "Accept")) }
        else if friend.joinable { answers.append(.init(id: "join", title: "Join")) }
        if friend.invitable { answers.append(.init(id: "invite", title: "Invite")) }
        var detail = friend.statusWords
        if friends.pending?.steamID == friend.steamID, friends.pending?.action != .setStatus {
            detail = "Waiting for Steam…"
        } else if let outcome = friends.outcome, outcome.steamID == friend.steamID, outcome.action != .setStatus {
            detail = DockFriends.message(action: outcome.action, result: outcome.result)
        }
        let symbol = friend.invitedYou ? "envelope.fill" : friend.dockFlags & 3 != 0 ? "gamecontroller.fill" : friend.state > 0 ? "circle.fill" : "circle"
        return BCDMenuRow(id: "fr.f.\(number(of: friend.steamID))", title: name, detail: detail, symbol: symbol, kind: .action(),
                          enabled: !answers.isEmpty && !busy,
                          confirm: answers.isEmpty ? nil : BCDConfirm(title: name, message: friend.statusWords, answers: answers))
    }

    // MARK: Doing

    /// The status chips.
    static func choose(_ row: String, _ value: String) {
        guard row == "fr.status", let raw = Int(value), let status = SteamOnlineStatus(rawValue: raw) else { return }
        friends.setStatus(status)
    }

    /// A friend's question: "join" (also Accept) or "invite".
    static func answer(_ row: String, _ answer: String) {
        guard let friend = friend(row: row) else { return }
        switch answer {
        case "join": if friend.invitedYou || friend.joinable { friends.send(.join, to: friend) }
        case "invite": if friend.invitable { friends.send(.invite, to: friend) }
        default: break
        }
    }

    // MARK: Keeping the open page fresh

    private static var timer: Timer?
    private static var ticks = 0
    private static var shown: Signature?

    /// What the page draws from, to tell a change from a tick without one.
    private struct Signature: Equatable {
        var friends: [DockFriends.Friend]
        var available: Bool
        var pending: DockFriends.Pending?
        var outcomeID: UInt64?
        var outcomeResult: Int?
        var status: Int
    }
    private static var signature: Signature {
        Signature(friends: friends.friends, available: friends.available, pending: friends.pending,
                  outcomeID: friends.outcome?.steamID, outcomeResult: friends.outcome?.result, status: SteamOnlineStatus.current.rawValue)
    }

    /// While the Friends page shows: Dock's list is read every 3 s, and every 0.5 s while a command waits for its answer (as Madeira's page).
    static func pageShown(_ id: String?, refresh: @escaping () -> Void) {
        timer?.invalidate()
        timer = nil
        guard id == pageID else { return }
        ticks = 0
        shown = signature
        let t = Timer(timeInterval: 0.5, repeats: true) { _ in
            MainActor.assumeIsolated {
                ticks += 1
                if friends.pending != nil || ticks % 6 == 0 { read(force: true) }
                let now = signature
                if now != shown { shown = now; refresh() }
            }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    /// The menu closed: the timer stops and the row numbers are forgotten.
    static func menuClosed() {
        timer?.invalidate()
        timer = nil
        shown = nil
        numbers.removeAll()
    }
}
