// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2026 Paulo Eduardo Correa (pauloec): the native iPhone Duo interface (Liquid Glass, system tabs, controller navigation, Windows desktop on Home), from thebcd69/bcd-games pull request #43; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Combine
import Foundation
import SwiftUI
import UIKit

// Round 604's input features on the engine: reads the running game's choices (BCDInputPrefs, by the game's Windows path),
// hands them to each feature when the game starts, its menu opens or closes, a choice changes or the game ends, and answers
// the game page's and the in-game menu's rows (BCDInputPage.swift, BCDInputMenu.swift).
//
// Off means nothing: a game with no line makes the default profile, every feature is told to be off, and each feature's hook
// in the older files is a static flag that is false. The session view's hook below reads the game's lines when the session, its
// menu or the app's state changes, never while the game runs.
// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).

@MainActor final class BCDInputLive {
    static let shared = BCDInputLive()

    /// All of a game's choices.
    func profile(_ windowsPath: String) -> BCDInputProfile {
        var profile = BCDInputProfile()
        for feature in BCDInputLines.features {
            // A choice that is for every game is kept under no game's path.
            let lines = BCDInputPrefs.shared.lines(feature, game: BCDInputLines.global.contains(feature) ? BCDInputPrefs.everyone : windowsPath)
            if !lines.isEmpty { profile.lines[feature] = lines }
        }
        return profile
    }

    /// The running game's entry: a library session in progress.
    var runningEntry: LibraryEntry? {
        let library = LibraryModel.shared
        return library.current != nil && !library.desktopHome ? library.activeEntry : nil
    }

    /// Looks at the running game's choices and tells each feature. Called when the session, its menu or the app's state changes,
    /// and after a choice.
    func refresh() {
        let entry = runningEntry
        let profile = entry.map { self.profile($0.windowsPath) } ?? BCDInputProfile()
        BCDTurboLive.shared.configure(turbo: profile.turbo, macros: profile.macros, title: entry?.title ?? "")
        if entry != nil { BCDPlayersLive.shared.reload() }   // the players' rumble reaches PadOutput.m once a session starts
        let library = LibraryModel.shared
        let title = entry?.title ?? ""
        BCDTouchExtras.configure(aim: profile.aim, gestures: profile.gestures, pencil: profile.pencil, title: title)
        BCDRadialHost.shared.configure(profile.radial, running: entry != nil && !library.launching, title: title)
    }

    /// A setting of the game's page or a row of the in-game menu: written to the line that keeps it. Returns whether it was one of this round's.
    @discardableResult
    func write(setting: String, value: String, windowsPath: String) -> Bool {
        let current = profile(windowsPath)
        guard let write = BCDInputLines.write(setting: setting, value: value, current: { current.get($0, $1) }) else { return false }
        BCDInputPrefs.shared.set(write.feature, game: BCDInputLines.global.contains(write.feature) ? BCDInputPrefs.everyone : windowsPath,
                                 line: write.line, value: write.value)
        if write.feature == BCDInputLines.touchLook { BCDTouchLookModel.shared.reload() }
        if write.feature == BCDInputLines.players { BCDPlayersLive.shared.reload() }
        refresh()
        return true
    }
}

/// The session view's hook: the session, its menu and the app's state decide when the features look again. One publisher for the
/// whole app run, so the view never subscribes twice; @Published announces before it stores, so the change is read a moment later.
struct BCDInputSessionHook: ViewModifier {
    @MainActor private static let changes: AnyPublisher<Void, Never> = {
        let library = LibraryModel.shared
        return Publishers.Merge4(library.$current.map { _ in () }, library.$menu.map { _ in () }, library.$launching.map { _ in () }, library.$desktopHome.map { _ in () })
            .receive(on: DispatchQueue.main)
            .eraseToAnyPublisher()
    }()

    @MainActor func body(content: Content) -> some View {
        content
            .onAppear { BCDInputLive.shared.refresh() }
            .onDisappear { BCDInputLive.shared.refresh() }
            .onReceive(Self.changes) { BCDInputLive.shared.refresh() }
            .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in BCDTurboLive.shared.releaseKeys() }
    }
}

// MARK: - The in-game menu

@MainActor enum BCDInputMenuLive {
    static func sections(_ entry: LibraryEntry?) -> [BCDMenuSection] {
        guard let entry else { return [] }
        return BCDInputMenu.sections(BCDInputLive.shared.profile(entry.windowsPath), players: BCDPlayersModel.shared.snapshot)
    }

    static func page(_ id: String, _ entry: LibraryEntry?) -> BCDMenuPage? {
        guard let entry else { return nil }
        return BCDInputMenu.page(id, BCDInputLive.shared.profile(entry.windowsPath), players: BCDPlayersModel.shared.snapshot)
    }

    /// A row that does something (choosing a controller for a player): true when it was one of this round's.
    static func act(_ row: String) -> Bool { BCDPlayersMenuLive.act(row) }

    /// A choice or a switch of a row (`value` "1" or "0" for a switch).
    static func change(_ row: String, _ value: String, entry: LibraryEntry?) {
        guard let entry else { return }
        BCDInputLive.shared.write(setting: BCDInputMenu.setting(of: row), value: value, windowsPath: entry.windowsPath)
    }
}

// MARK: - The game's page

extension BCDGamePageLive {
    /// The sections round 604's input features add to Options.
    func inputGroups(_ entry: LibraryEntry) -> [BCDGameSettingGroup] {
        BCDInputPage.groups(BCDInputLive.shared.profile(entry.windowsPath))
    }

    /// A setting of those sections: true when `setting` was one.
    func inputSet(_ setting: String, to value: String, for game: BCDGame) -> Bool {
        guard BCDInputLines.handles(setting), let entry = entry(for: game) else { return false }
        return BCDInputLive.shared.write(setting: setting, value: value, windowsPath: entry.windowsPath)
    }
}
