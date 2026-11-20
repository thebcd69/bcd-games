#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 Vugar
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
# This file was taken from Madeira's tests/host/check-dock-friends.py (willfaust/Madeira pull request #572 by Vugar, branch
# steam-friends-pr-572-replacement, commit ccd7a3d). See THIRD-PARTY-NOTICES.md.
# Modified by BCD (thebcd69), 2026-11-14: BCD's menu (DockFriendsMenu.swift on BCD's row types) in place of Madeira's Session menu,
# the status passed to Dock only when one is chosen (no variable for Offline), Settings > Accounts' choice, the report round BCD's
# pinned host writes (ml1830), and no L().
"""Compile the app's production Madeira Dock Friends model; synthetic files only.

Dock writes the friends feed to C:\\madeira-friends.bin and answers commands from
C:\\madeira-friends-cmd.bin with friends-cmd-seq / friends-cmd-result report lines
(madeira-dock README, "Friends"). Checked here, against a temporary drive_c:
feed bounds, the command bytes, matching a command to its own result, the
timeout, reset and session end, and that a missing, invalid or stale feed
leaves no old list behind. No Steam, Wine or device session is used.

madeira-bcd: Friends are off by default. Checked too: a start passes MADEIRA_DOCK_PERSONA_STATE only for a chosen status and removes
the variable otherwise; without it the in-game menu reads nothing and has no Friends row; BCD's Friends page (rows of BCD's menu)
offers Accept, Join and Invite by the feed's flags, sends one command at a time and never puts a SteamID into a row's id.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


friends = (app / 'DockFriends.swift').read_text()
dock = (app / 'MadeiraDock.swift').read_text()
menu = (app / 'DockFriendsMenu.swift').read_text()
session = (app / 'BCDSessionLive.swift').read_text()
ui = (app / 'BCDSession.swift').read_text()
accounts = (app / 'BCDAccounts.swift').read_text()
live = (app / 'BCDAccountsLive.swift').read_text()

# ------------------------------------------------------------------ static
cleanup = dock[dock.index('    @MainActor static func cleanup() {'):]
cleanup = cleanup[:cleanup.index('\n    }\n')]
require('DockFriends.shared.endSession()' in cleanup, 'MadeiraDock.cleanup() (session end, app start, sign-out) removes the friends files')
require('        if SteamOnlineStatus.everChosen { DockFriends.shared.endSession() }' in cleanup
        and 'static var everChosen: Bool { UserDefaults.standard.object(forKey: key) != nil }' in dock,
        '...only for a player who ever chose a status: for everyone else (the Dock writes no Friends file without one) nothing is touched')
require('friendsRequested = false' in cleanup and cleanup.index('friendsRequested = false') < cleanup.index('DockFriends.shared.endSession()'),
        'MadeiraDock.cleanup() also ends the session\'s Friends request')
for hook in ['if let friends = DockFriendsMenu.section() { out.append(friends) }', 'if let friends = DockFriendsMenu.page(id) { return friends }',
             'DockFriendsMenu.pageShown(id) {', 'DockFriendsMenu.menuClosed()',
             'if DockFriendsMenu.handles(row) { return DockFriendsMenu.choose(row, value) }',
             'if DockFriendsMenu.handles(row) { return DockFriendsMenu.answer(row, answer) }']:
    require(session.count(hook) == 1, f'the in-game menu calls the Friends page once: {hook[:48]}')
section = menu[menu.index('    static func section() -> BCDMenuSection? {'):]
section = section[:section.index('\n    }\n')]
require(section.index('guard MadeiraDock.friendsRequested else { return nil }') < section.index('read()') < section.index('guard friends.available else { return nil }'),
        'the in-game menu shows Friends only in a session started with a status, and only for a current feed; otherwise nothing is read')
for path, text in (('DockFriends.swift', friends), ('DockFriendsMenu.swift', menu)):
    require(' L("' not in text and '(L("' not in text, f'{path}: plain English texts (BCD has no L())')
require('SteamLog.' not in menu and 'LogStore' not in menu and 'print(' not in menu, 'DockFriendsMenu.swift logs nothing (names and SteamIDs stay in the page)')
for line in friends.splitlines():
    if 'SteamLog.' in line:
        require(re.search(r'\\\((?:[^)]*\.)?(name|steamID|id|friend|data|url)\b', line) is None,
                f'DockFriends.swift: no SteamID, name or path logged in "{line.strip()[:60]}"')
configure = dock[dock.index('    static func configure(_ game: DockGame, launchOption: UInt32 = 0, arguments: String = "") {'):]
configure = configure[:configure.index('\n    }\n')]
require('        if let state = SteamOnlineStatus.current.personaState {\n'
        '            setenv("MADEIRA_DOCK_PERSONA_STATE", String(state), 1)\n'
        '        } else {\n'
        '            unsetenv("MADEIRA_DOCK_PERSONA_STATE")\n'
        '        }\n'
        '        friendsRequested = SteamOnlineStatus.current != .offline\n' in configure,
        'the launch passes a chosen status to Dock and removes the variable for Offline (the default): Friends stay off')
require(len(re.findall(r'(?:setenv|unsetenv)\("MADEIRA_DOCK_PERSONA_STATE"', dock)) == 2 and 'MADEIRA_DOCK_PERSONA_STATE' not in friends + menu + live,
        'nothing else sets the status variable')
# Settings > Accounts > Steam: Off is the default, offered once signed in; the choice is SteamOnlineStatus (nothing else is stored).
require('var friendsAvailable = false' in accounts and 'var friendsStatus = BCDSteamFriends.Status.off' in accounts
        and 'case off = 0, online = 1, away = 3, invisible = 7' in accounts, 'Settings > Accounts: Steam Friends is off until a status is chosen')
require('if steam.available && steam.friendsAvailable { friendsRows(steam) }' in accounts
        and 'func setFriendsStatus(_ status: BCDSteamFriends.Status) {}' in accounts, 'the section shows the choice only where the backend offers it (the preview app does not)')
require('friendsAvailable: model.accountName != nil' in live
        and 'friendsStatus: BCDSteamFriends.Status(rawValue: SteamOnlineStatus.current.rawValue) ?? .off' in live
        and 'SteamOnlineStatus.current = SteamOnlineStatus(rawValue: status.rawValue) ?? .offline' in live, 'the live backend reads and writes SteamOnlineStatus')
require('SteamOnlineStatus' not in accounts and 'DockFriends' not in accounts, 'BCDAccounts.swift names no engine type')

# ------------------------------------------------------------------ compiled
model = friends[friends.index('@MainActor final class DockFriends'):]
# BCD's row types as the app has them (BCDSession.swift), without the SwiftUI colours of BCDSessionTone.
rows = ui[ui.index('/// One choice of a row of chips.'):ui.index('struct BCDLogLine')]
page = menu[menu.index('@MainActor enum DockFriendsMenu {'):]
status = dock[dock.index('/// The Steam status Dock signs in to Friends with'):]
stubs = r'''
import Foundation
import Combine
enum MadeiraDock { static var drive = URL(fileURLWithPath: "/nonexistent"); static var friendsRequested = false }
enum SteamLog { static var events: [String] = []; static func event(_ m: String) { events.append(m) } }
enum BCDSessionTone: Equatable { case normal, soft, good, warm, hot }
'''
checks = r'''
let drive = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
MadeiraDock.drive = drive
let feed = drive.appendingPathComponent("madeira-friends.bin")
let commandFile = drive.appendingPathComponent("madeira-friends-cmd.bin")
let report = drive.appendingPathComponent("madeira-dock.txt")
let fm = FileManager.default

func le32(_ v: UInt32) -> [UInt8] { (0..<4).map { UInt8(truncatingIfNeeded: v >> (8 * UInt32($0))) } }
func le64(_ v: UInt64) -> [UInt8] { (0..<8).map { UInt8(truncatingIfNeeded: v >> (8 * UInt64($0))) } }
func entry(_ id: UInt64, _ name: String, state: UInt32 = 1, app: UInt32 = 0, flags: UInt8 = 0) -> [UInt8] {
    le64(id) + le32(state) + le32(app) + [flags, UInt8(name.utf8.count)] + Array(name.utf8)
}
func encode(_ entries: [[UInt8]], count: UInt32? = nil) -> Data {
    Data(Array("MFRND001".utf8) + le32(count ?? UInt32(entries.count)) + entries.flatMap { $0 })
}
let alice: UInt64 = 0x0110000100000001, bob: UInt64 = 0x0110000100000002, carol: UInt64 = 0x0110000100000003

// Feed bounds (parseFeed).
let one = encode([entry(alice, "Alice")])
assert(DockFriends.parseFeed(one)?.first?.name == "Alice")
assert(DockFriends.parseFeed(encode([]))?.isEmpty == true)
assert(DockFriends.parseFeed(Data("MFRND002".utf8) + Data(le32(0))) == nil, "magic")
assert(DockFriends.parseFeed(Data(one.prefix(11))) == nil, "short header")
assert(DockFriends.parseFeed(Data(one.dropLast())) == nil, "truncated name")
assert(DockFriends.parseFeed(one + Data([0])) == nil, "trailing byte")
assert(DockFriends.parseFeed(encode([entry(alice, "Alice")], count: 2)) == nil, "count past the data")
assert(DockFriends.parseFeed(encode([], count: 251)) == nil, "count past 250")
assert(DockFriends.parseFeed(encode([entry(alice, String(repeating: "a", count: 63))])) != nil)
assert(DockFriends.parseFeed(encode([entry(alice, String(repeating: "a", count: 64))])) == nil, "64-byte name")
var badName = entry(alice, "ab"); badName[badName.count - 1] = 0xff
assert(DockFriends.parseFeed(encode([badName])) == nil, "invalid UTF-8")
let full = encode((0..<250).map { entry(alice + UInt64($0), String(repeating: "n", count: 63)) })
assert(full.count <= DockFriends.feedCap && DockFriends.parseFeed(full)?.count == 250, "the largest feed")
let oversized = Data(Array("MFRND001".utf8) + le32(0)) + Data(count: DockFriends.feedCap - 11)
assert(oversized.count == DockFriends.feedCap + 1 && DockFriends.parseFeed(oversized) == nil, "past the cap")

// Display order: invited, joinable, this game, another game, online, offline; by name.
let order = DockFriends.ordered(DockFriends.parseFeed(encode([
    entry(1, "zed", state: 0), entry(2, "Yan"), entry(3, "xia", flags: 1), entry(4, "Wu", flags: 3),
    entry(5, "vic", flags: 7), entry(6, "Uma", state: 0, flags: 8), entry(7, "abe")]))!).map(\.steamID)
assert(order == [6, 5, 4, 3, 7, 2, 1], "\(order)")

// Command bytes: magic, action, SteamID (LE), sequence (LE); sequences stay below 2^31.
assert([UInt8](DockFriends.command(.invite, id: alice, sequence: 0x01020304)) ==
       Array("MFCMD001".utf8) + [1] + le64(alice) + [4, 3, 2, 1])
assert(DockFriends.command(.setStatus, id: 7, sequence: 1).count == 21)
assert(DockFriends.nextSequence(after: 41) == 42)
assert(DockFriends.nextSequence(after: 0x7fff_fffe) == 0x7fff_ffff)
assert(DockFriends.nextSequence(after: 0x7fff_ffff) == 1 && DockFriends.nextSequence(after: 0xffff_ffff) == 1)
assert(DockFriends.nextSequence(after: 0x8000_0005) == 6)

// A command's own result only: after its friends-cmd-seq, complete lines only.
func lines(_ l: [String], end: String = "\r\n") -> Data { Data((l.joined(separator: "\r\n") + end).utf8) }
let r = { (s: String) in "[steam-host] ml1830 " + s }
assert(DockFriends.commandResult(in: lines([r("friends-cmd-seq=9"), r("friends-cmd-result=1")]), sequence: 9) == 1)
assert(DockFriends.commandResult(in: lines([r("friends-cmd-result=4"), r("friends-cmd-seq=9")]), sequence: 9) == nil, "a result before the sequence")
assert(DockFriends.commandResult(in: lines([r("friends-cmd-seq=19"), r("friends-cmd-result=1")]), sequence: 9) == nil, "another sequence")
assert(DockFriends.commandResult(in: lines([r("friends-cmd-seq=9"), r("friends-cmd-seq=10"), r("friends-cmd-result=1")]), sequence: 9) == nil, "a later command's result")
assert(DockFriends.commandResult(in: lines([r("friends-cmd-seq=9"), r("friends-count=3"), r("friends-cmd-result=2")]), sequence: 9) == 2)
assert(DockFriends.commandResult(in: lines([r("friends-cmd-seq=9"), r("friends-cmd-result=1")], end: ""), sequence: 9) == nil, "a partial final line")
assert(DockFriends.commandResult(in: lines([r("friends-cmd-seq=2147483647"), r("friends-cmd-result=3")]), sequence: 0x7fff_ffff) == 3)

MainActor.assumeIsolated {
    let model = DockFriends()
    let now = Date()
    func writeFeed(_ data: Data, age: TimeInterval = 0) {
        try! data.write(to: feed)
        try! fm.setAttributes([.modificationDate: now.addingTimeInterval(-age)], ofItemAtPath: feed.path)
    }

    // Valid -> missing, valid -> invalid, valid -> stale: never an old list.
    model.refresh(now: now)
    assert(!model.available && model.friends.isEmpty, "no feed")
    writeFeed(one); model.refresh(now: now)
    assert(model.available && model.friends.map(\.name) == ["Alice"])
    try! fm.removeItem(at: feed); model.refresh(now: now)
    assert(!model.available && model.friends.isEmpty, "valid to missing")
    writeFeed(one); model.refresh(now: now); assert(model.friends.count == 1)
    writeFeed(Data([0])); model.refresh(now: now)
    assert(!model.available && model.friends.isEmpty, "valid to invalid")
    writeFeed(one); model.refresh(now: now); assert(model.friends.count == 1)
    writeFeed(one, age: DockFriends.feedMaxAge + 1); model.refresh(now: now)
    assert(!model.available && model.friends.isEmpty, "valid to stale (a host that did not end cleanly)")
    writeFeed(oversized); model.refresh(now: now)
    assert(!model.available, "oversized feed")

    // No command without a current feed.
    model.send(.invite, to: DockFriends.Friend(steamID: alice, name: "Alice", dockFlags: 16), now: now)
    assert(model.pending == nil && !fm.fileExists(atPath: commandFile.path), "no command while friends are unavailable")

    // A command, its bytes, and its own result in the report written after it.
    writeFeed(encode([entry(alice, "Alice", flags: 16), entry(bob, "Bob", state: 3, app: 70, flags: 7)]))
    model.refresh(now: now)
    try! lines([r("friends-available=1"), r("friends-cmd-seq=1"), r("friends-cmd-result=4")]).write(to: report)
    let bobFriend = model.friends.first { $0.steamID == bob }!
    model.send(.join, to: bobFriend, now: now)
    let sent = model.pending!
    assert(sent.action == .join && sent.steamID == bob && sent.sequence <= 0x7fff_ffff)
    assert(try! Data(contentsOf: commandFile) == DockFriends.command(.join, id: bob, sequence: sent.sequence))
    model.send(.invite, to: bobFriend, now: now)
    assert(model.pending == sent, "one command at a time")
    // An earlier session's answer for the same number, before the command was sent, is not this one's.
    let before = (try! Data(contentsOf: report))
    model.refresh(now: now)
    assert(model.pending == sent)
    let handle = try! FileHandle(forWritingTo: report); handle.seekToEndOfFile()
    handle.write(lines([r("friends-cmd-seq=\(sent.sequence &+ 1)"), r("friends-cmd-result=4"),
                        r("friends-cmd-seq=\(sent.sequence)"), r("friends-cmd-result=1")])); handle.closeFile()
    model.refresh(now: now)
    assert(model.pending == nil && model.outcome?.steamID == bob && model.outcome?.result == 1 && model.outcome?.action == .join)
    assert(DockFriends.message(action: .join, result: 1) == "Sent to the game")

    // The earlier lines are ignored by offset: an answer that only exists there does not count.
    try! (before + lines([r("friends-cmd-seq=\(DockFriends.nextSequence(after: sent.sequence))"), r("friends-cmd-result=1")])).write(to: report)
    let reportSize = (try! fm.attributesOfItem(atPath: report.path))[.size] as! UInt64
    model.send(.invite, to: bobFriend, now: now)
    let second = model.pending!
    assert(second.sequence == DockFriends.nextSequence(after: sent.sequence))
    model.refresh(now: now)
    assert(model.pending == second, "an answer written before the command was sent is not its answer (report \(reportSize) bytes)")

    // No answer in commandTimeout: "No answer from Steam" (result 0).
    model.refresh(now: now.addingTimeInterval(DockFriends.commandTimeout - 1))
    assert(model.pending == second)
    model.refresh(now: now.addingTimeInterval(DockFriends.commandTimeout + 1))
    assert(model.pending == nil && model.outcome?.result == 0 && DockFriends.message(action: .invite, result: 0) == "No answer from Steam")

    // A new, shorter report (a new session) is read from its start.
    model.send(.invite, to: bobFriend, now: now)
    let third = model.pending!
    try! lines([r("friends-cmd-seq=\(third.sequence)"), r("friends-cmd-result=2")]).write(to: report)
    model.refresh(now: now)
    assert(model.pending == nil && model.outcome?.result == 2)

    // Status: Offline is never sent; the others are, with the status in the id field.
    model.setStatus(.offline, now: now)
    assert(model.pending == nil)
    model.setStatus(.away, now: now)
    assert(model.pending?.action == .setStatus && model.pending?.steamID == 3)
    assert(SteamOnlineStatus.current == .away)

    // Reset and session end.
    model.reset()
    assert(model.friends.isEmpty && !model.available && model.pending == nil && model.outcome == nil)
    writeFeed(one); try! Data([1]).write(to: commandFile); model.refresh(now: now)
    assert(model.available)
    model.endSession()
    assert(!fm.fileExists(atPath: feed.path) && !fm.fileExists(atPath: commandFile.path))
    assert(!model.available && model.friends.isEmpty)
    model.refresh(now: now)
    assert(!model.available, "nothing left for the next session")
}
assert(!SteamLog.events.contains { $0.contains("Alice") || $0.contains("Bob") || $0.contains(String(alice)) || $0.contains(String(bob)) },
       "logs carry no names or SteamIDs")

// Friends are opt-in: with nothing chosen the status is Offline (friends off).
UserDefaults.standard.removeObject(forKey: "steamOnlineStatus")
assert(SteamOnlineStatus.current == .offline)
SteamOnlineStatus.current = .invisible
assert(SteamOnlineStatus.current == .invisible && UserDefaults.standard.integer(forKey: "steamOnlineStatus") == 7)
UserDefaults.standard.set(5, forKey: "steamOnlineStatus")
assert(SteamOnlineStatus.current == .offline, "an unknown stored value is Offline")
UserDefaults.standard.removeObject(forKey: "steamOnlineStatus")
// madeira-bcd: Offline passes nothing to Dock; the others pass Steam's persona state.
assert(SteamOnlineStatus.offline.personaState == nil && SteamOnlineStatus.online.personaState == 1
       && SteamOnlineStatus.away.personaState == 3 && SteamOnlineStatus.invisible.personaState == 7)

// madeira-bcd: BCD's Friends page (DockFriendsMenu.swift on the app's row types).
MainActor.assumeIsolated {
    let now = Date()
    func writeFeed(_ data: Data) {
        try! data.write(to: feed)
        try! fm.setAttributes([.modificationDate: now], ofItemAtPath: feed.path)
    }
    try? fm.removeItem(at: commandFile)
    try! Data().write(to: report)
    // invited (flags 8|16), joinable in this game (7), online and invitable (16), online without a lobby of ours (0), offline (0).
    writeFeed(encode([entry(alice, "Alice", flags: 8 | 16), entry(bob, "Bob", app: 70, flags: 7), entry(carol, "Carol", flags: 16),
                      entry(carol + 1, "Dan"), entry(carol + 2, "", state: 0)]))

    // A session started without a status: no row, nothing read, although a valid feed lies there.
    MadeiraDock.friendsRequested = false
    assert(DockFriendsMenu.section() == nil, "no Friends row without a status")
    assert(!DockFriends.shared.available && DockFriends.shared.friends.isEmpty, "the feed is not read without a status")

    // With a status and a current feed: the row, with the invite count.
    MadeiraDock.friendsRequested = true
    SteamOnlineStatus.current = .away
    let section = DockFriendsMenu.section()!
    assert(section.rows.count == 1 && section.rows[0].id == "fr.open" && section.rows[0].kind == .page(DockFriendsMenu.pageID, value: "1 invite"))
    assert(DockFriendsMenu.handles("fr.open") && !DockFriendsMenu.handles("mm.pause") && DockFriendsMenu.page("binds") == nil)

    let page = DockFriendsMenu.page(DockFriendsMenu.pageID)!
    assert(page.sections.map(\.id) == ["fr.status", "fr.invited", "fr.list"], "\(page.sections.map(\.id))")
    // The status chips: Online, Away, Invisible (never Offline), the chosen one selected.
    assert(page.sections[0].rows[0].kind == .choice([BCDChoice(id: "1", title: "Online"), BCDChoice(id: "3", title: "Away"), BCDChoice(id: "7", title: "Invisible")], selected: "3"))
    func friendRow(_ name: String, in page: BCDMenuPage) -> BCDMenuRow { page.sections.flatMap(\.rows).first { $0.title == name }! }
    assert(page.sections[1].rows.map(\.title) == ["Alice"])
    assert(friendRow("Alice", in: page).confirm!.answers.map(\.title) == ["Accept", "Invite"] && friendRow("Alice", in: page).confirm!.answers.map(\.id) == ["join", "invite"])
    assert(friendRow("Bob", in: page).confirm!.answers.map(\.title) == ["Join"] && friendRow("Bob", in: page).detail == "In this game · joinable")
    assert(friendRow("Carol", in: page).confirm!.answers.map(\.title) == ["Invite"] && friendRow("Carol", in: page).detail == "Online")
    assert(friendRow("Dan", in: page).confirm == nil && !friendRow("Dan", in: page).enabled, "nothing to do with an online friend while there is no lobby")
    assert(friendRow("Unknown", in: page).confirm == nil && !friendRow("Unknown", in: page).enabled && friendRow("Unknown", in: page).detail == "Offline")
    // Row ids are the menu's own numbers, never a SteamID.
    for row in page.sections.flatMap(\.rows) {
        assert(row.id.hasPrefix("fr."))
        for id in [alice, bob, carol] { assert(!row.id.contains(String(id)), "a SteamID in a row id") }
    }

    // An answer the row does not offer sends nothing; one it offers sends that command, once.
    DockFriendsMenu.answer(friendRow("Carol", in: page).id, "join")
    DockFriendsMenu.answer(friendRow("Dan", in: page).id, "invite")
    DockFriendsMenu.answer("fr.f.999", "join")
    assert(DockFriends.shared.pending == nil && !fm.fileExists(atPath: commandFile.path), "an action the feed does not offer is not sent")
    DockFriendsMenu.answer(friendRow("Bob", in: page).id, "join")
    let sent = DockFriends.shared.pending!
    assert(sent.action == .join && sent.steamID == bob)
    assert(try! Data(contentsOf: commandFile) == DockFriends.command(.join, id: bob, sequence: sent.sequence))
    DockFriendsMenu.answer(friendRow("Alice", in: page).id, "invite")
    assert(DockFriends.shared.pending == sent, "one command at a time")
    // While it waits: every friend row and the status chips are disabled, and Bob's row says so.
    let busy = DockFriendsMenu.page(DockFriendsMenu.pageID)!
    assert(busy.sections.flatMap(\.rows).allSatisfy { !$0.enabled }, "rows are disabled while a command waits")
    assert(friendRow("Bob", in: busy).detail == "Waiting for Steam…")
    assert(friendRow("Bob", in: page).id == friendRow("Bob", in: busy).id, "a friend keeps its row id while the menu is open")
    // Dock's answer shows on the friend's row.
    try! lines([r("friends-cmd-seq=\(sent.sequence)"), r("friends-cmd-result=1")]).write(to: report)
    DockFriends.shared.refresh(now: now)
    assert(friendRow("Bob", in: DockFriendsMenu.page(DockFriendsMenu.pageID)!).detail == "Sent to the game")

    // The status chips: a change is sent as a command and saved; an unknown value and Offline are not.
    try? fm.removeItem(at: commandFile)
    DockFriends.shared.reset(); DockFriends.shared.refresh(now: now)
    DockFriendsMenu.choose("fr.status", "0")
    DockFriendsMenu.choose("fr.status", "5")
    DockFriendsMenu.choose("fr.other", "1")
    assert(DockFriends.shared.pending == nil && SteamOnlineStatus.current == .away)
    DockFriendsMenu.choose("fr.status", "7")
    assert(DockFriends.shared.pending?.action == .setStatus && DockFriends.shared.pending?.steamID == 7 && SteamOnlineStatus.current == .invisible)

    // The session ends (MadeiraDock.cleanup): the row goes, and nothing is read any more.
    DockFriendsMenu.menuClosed()
    MadeiraDock.friendsRequested = false
    DockFriends.shared.endSession()
    writeFeed(encode([entry(alice, "Alice")]))
    assert(DockFriendsMenu.section() == nil && !DockFriends.shared.available)
    UserDefaults.standard.removeObject(forKey: "steamOnlineStatus")
}
assert(!SteamLog.events.contains { $0.contains("Alice") || $0.contains("Bob") || $0.contains(String(alice)) || $0.contains(String(bob)) },
       "logs carry no names or SteamIDs")
print("PASS: Friends feed bounds, display order, command bytes, 31-bit sequences, own-result matching, timeout, reset, session end, valid-to-missing/invalid/stale feeds, opt-in status; BCD's Friends page: nothing without a status, rows by the feed's flags, one command at a time, no SteamID in a row id")
'''
with tempfile.TemporaryDirectory(prefix='madeira-dock-friends-') as directory:
    work = Path(directory)
    source = work / 'main.swift'; binary = work / 'check'; drive = work / 'drive_c'
    drive.mkdir()
    source.write_text(stubs + rows + '\n' + model + '\n' + status + '\n' + page + '\n' + checks)
    built = subprocess.run([SWIFTC, '-Onone', str(source), '-o', str(binary)])
    require(built.returncode == 0, 'production DockFriends model compiles on the host')
    if built.returncode == 0:
        env = dict(os.environ, CFFIXED_USER_HOME=str(work))
        require(subprocess.run([str(binary), str(drive)], env=env).returncode == 0, 'Friends model checks')

print('FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
