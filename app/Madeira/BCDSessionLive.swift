// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import SwiftUI
import UIKit
import os

// BCD's in-game layer on the engine (docs/BCD-UI-ARCHITECTURE.md). The views are BCD's own
// (BCDSession.swift, BCDInGameMenu.swift, BCDStartingScreen.swift, BCDPerformanceOverlay.swift);
// this file fills them from the session and carries out what a person asks through the very
// engine calls the classic in-game layer used (deleted in wave 3, 2026-10-19; LibraryKeyboard's
// key row stays), so a game plays exactly as before:
// - a library session: BCDSessionLiveLayer, in the touch-controls window above the game.
//   LibraryModel keeps deciding when the starting screen ends, what the menu's state is and how
//   a game is asked to quit; DockStartScreen keeps deciding what a BCD Dock start shows behind it;
// - the touch controls' own menu (BCDDeskSessionMenu) and the starting screen's host
//   (BCDStartingHost), with the rules the classic layer had (the first frame, the [win-pos] line,
//   ProMotion's hold).
// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).
// Nothing here runs while its part is not on screen: each feed's timer belongs to the part.

// MARK: - A library session's layer

/// What TouchControlsOverlay shows over a library session (the touch controls aside).
struct BCDSessionLiveLayer: View {
    @StateObject private var live = BCDLiveSession()

    var body: some View {
        BCDSessionLayer(hud: live.hud, actions: live)
            .onAppear { live.appeared() }
            .onDisappear { live.disappeared() }
            .onReceive(LibraryController.shared.commands) { live.command($0) }
            .modifier(BCDGyroSessionHook())   // madeira-bcd (2026-10-25, round 603): gyro aim looks when the session, its menu or the app changes
            .modifier(BCDInputSessionHook())   // madeira-bcd (2026-10-25, round 604): the input features look when the session, its menu or the app changes
    }
}

@MainActor final class BCDLiveSession: ObservableObject, BCDSessionHUDActions {
    let hud = BCDSessionHUDModel()
    private let model = LibraryModel.shared
    private let dock = DockStartScreen.shared
    private var watchers = Set<AnyCancellable>()
    private var startingTimer: Timer?
    private var startingGame: BCDGame?
    private var hudLive: BCDHUDLive?
    private var logWatch: AnyCancellable?
    private var menuSource: BCDLibraryMenuSource?

    init() {
        // @Published announces a change before it is stored: read the model a moment later.
        let changes: [AnyPublisher<Void, Never>] = [
            model.$launching.map { _ in () }.eraseToAnyPublisher(),
            model.$menu.map { _ in () }.eraseToAnyPublisher(),
            model.$performance.map { _ in () }.eraseToAnyPublisher(),
            BCDHUDState.shared.$setup.map { _ in () }.eraseToAnyPublisher(),
            BCDHUDState.shared.$moving.map { _ in () }.eraseToAnyPublisher(),
            model.$sessionMessage.map { _ in () }.eraseToAnyPublisher(),
            model.$liveLogs.map { _ in () }.eraseToAnyPublisher(),
            model.$launchLogs.map { _ in () }.eraseToAnyPublisher(),
            model.$launchSlow.map { _ in () }.eraseToAnyPublisher(),
            model.$current.map { _ in () }.eraseToAnyPublisher(),
            dock.$failure.map { _ in () }.eraseToAnyPublisher(),
            dock.$holding.map { _ in () }.eraseToAnyPublisher(),
            dock.$attention.map { _ in () }.eraseToAnyPublisher(),
        ]
        Publishers.MergeMany(changes)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] in self?.refresh() }
            .store(in: &watchers)

    }

    func appeared() {
        model.saveCurrentProfile()
        refresh()
    }

    func disappeared() {
        stopStarting()
        stopOverlay()
        stopLog()
        closeMenuModel(save: false)
    }

    // MARK: What is on screen

    private func refresh() {
        let running = model.current != nil
        hud.message = running ? model.sessionMessage : ""
        hud.showsMenuButton = running && !model.launching
        if running && model.launching { startStarting() } else { stopStarting() }
        if running && model.menu { openMenuModel() } else { closeMenuModel(save: true) }
        if running && !model.launching && BCDHUDState.shared.setup.layout.shown { startOverlay() } else { stopOverlay() }
        if running && !model.launching && model.liveLogs { startLog() } else { stopLog() }
        tickStarting()
    }

    // MARK: Starting screen

    private func startStarting() {
        guard hud.starting == nil else { return }
        startingGame = BCDLiveSessionArt.game(for: model.activeEntry, dockAppID: dock.appID)
        hud.starting = BCDStartingFeed(startingInfo())
        let timer = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tickStarting() }
        }
        RunLoop.main.add(timer, forMode: .common)
        startingTimer = timer
    }

    private func tickStarting() {
        guard let feed = hud.starting else { return }
        let info = startingInfo()
        if feed.info != info { feed.info = info }
    }

    private func stopStarting() {
        startingTimer?.invalidate()
        startingTimer = nil
        hud.starting = nil
        startingGame = nil
    }

    /// The starting screen's values, from the session (LibraryModel) and a BCD Dock start
    /// (DockStartScreen, MadeiraDock's report, the one-time installs), read once a second.
    private func startingInfo() -> BCDStartingInfo {
        let game = startingGame ?? BCDLiveSessionArt.game(for: model.activeEntry, dockAppID: dock.appID)
        var info = BCDStartingInfo(game: game)
        info.seconds = max(0, Int(Date().timeIntervalSince(model.launchStartedAt)))
        info.tip = BCDLiveSession.menuTip
        if let failure = dock.failure {
            info.step = .stopped
            info.steps = BCDDockStart.steps
            info.failure = failure
            info.canClose = true
        } else if dock.active {
            let progress = DockInstallers.poll(drive: MadeiraDock.drive)
            let hostDue = DockInstallers.finishedAt ?? model.launchStartedAt
            let read = BCDDockStart.read(MadeiraDock.pollReport().fields, installers: DockInstallers.script != nil,
                                         installerProgress: progress, installsFinished: DockInstallers.finishedAt != nil,
                                         waited: Date().timeIntervalSince(hostDue),
                                         restartHint: DockInstallers.installsOnly ? DockInstallers.closingWords : nil)
            info.step = read.step
            info.steps = BCDDockStart.steps
            info.notes = read.notes
            if let note = DockInstallers.note { info.notes.append(BCDStartNote(id: "installs-note", text: note)) }
            if dock.attention {
                info.notes.append(BCDStartNote(id: "attention", text: "A Steam window may need an answer. Show desktop to see it.",
                                               tone: .warm))
            }
        } else {
            info.step = wine_process_is_running() != 0 ? .loading : .starting
            if model.launchSlow {
                info.notes.append(BCDStartNote(id: "slow", text: "This is taking longer than usual. A game's first start can take a few minutes.",
                                               tone: .warm))
            }
        }
        info.canShowDesktop = dock.holding && dock.failure == nil
        info.canShowGame = model.launchSlow && !dock.holding
        info.logShown = model.launchLogs
        if info.logShown { info.logLines = BCDLiveSessionLog.lines(60) }
        return info
    }

    /// How to open the game menu, as the starting screen says it.
    static var menuTip: String {
        if BCDChordSetting.mode == .holdStart {   // madeira-bcd (round 604): the menu's "Open this menu from a controller with"
            return "The game menu: hold Start on a controller, press Back + Start, or tap the menu button."
        }
        return LibraryController.homeHold || BCDChordSetting.mode == .holdGuide
            ? "The game menu: hold the controller's Home button, press Back + Start, or tap the menu button."
            : "The game menu: Back + Start on a controller, or the menu button on the screen."
    }

    func starting(_ action: BCDStartingAction) {
        switch action {
        case .toggleLog: model.toggleLaunchLogs()
        case .showDesktop: dock.showDesktop(model)
        case .showGame: model.showGameView(reason: "button")
        case .close: model.requestQuit()
        case .skip: break
        }
        refresh()
    }

    // MARK: Menu

    private func openMenuModel() {
        guard hud.menu == nil else { return }
        // As the classic menu: the controller drives the menu and the game sees a pad at rest.
        LibraryController.shared.configure(enabled: true, ownsInput: true)
        let source = BCDLibraryMenuSource()
        let menu = BCDSessionMenuModel(source: source)
        menu.custom["touch"] = { AnyView(BCDTouchMenuSectionLive(closeMenu: { LibraryModel.shared.menu = false })) }
        menu.custom[BCDInputMenu.lookCustom] = { AnyView(BCDTouchLookSampleLive()) }   // madeira-bcd (2026-10-25, round 604): the touch look's sample
        source.menu = menu
        menuSource = source
        hud.menu = menu
    }

    private func closeMenuModel(save: Bool) {
        guard hud.menu != nil else { return }
        menuSource?.dispose()
        menuSource = nil
        hud.menu = nil
        LibraryController.shared.configure(enabled: true, ownsInput: false)
        if save { model.saveCurrentProfile() }
    }

    func openMenu() { model.showMenu() }
    func closeMenu() { model.menu = false }

    /// LibraryController's commands: Back + Start (and, opted in, a held Home button) open and
    /// close the menu; while it is open the D-pad, A, B and the shoulders drive it.
    func command(_ name: String) {
        guard model.current != nil else { return }
        if name == "menu" || name == "home" {
            if model.menu { model.menu = false } else { model.showMenu() }
            return
        }
        guard let menu = hud.menu else { return }
        let map: [String: BCDSessionCommand] = ["up": .up, "down": .down, "left": .left, "right": .right, "accept": .accept,
                                                "back": .back, "tab-previous": .previousTab, "tab-next": .nextTab]
        if let command = map[name] { menu.commands.send(command) }
    }

    // MARK: Touch regions (ControlsWindow lets these through to the layer)

    func menuButtonFrame(_ frame: CGRect) { model.menuButtonRect = frame }
    func overlayFrame(_ frame: CGRect) { model.performanceRect = frame }

    // MARK: Overlay and log

    /// The performance HUD (BCDHUDLive.swift): made when a layout is on, told when the player changes it, dropped when it goes off.
    private func startOverlay() {
        if let hudLive {
            hudLive.setupChanged()
            return
        }
        let live = BCDHUDLive()
        hudLive = live
        hud.performance = live.feed
        live.start()
    }

    private func stopOverlay() {
        hudLive?.stop()
        hudLive = nil
        hud.performance = nil
    }

    private func startLog() {
        guard hud.log == nil else { return }
        let feed = BCDLogFeed(lines: BCDLiveSessionLog.lines(20))
        hud.log = feed
        logWatch = LogStore.shared.$entries
            .throttle(for: .seconds(1), scheduler: DispatchQueue.main, latest: true)
            .sink { [weak feed] _ in DispatchQueue.main.async { feed?.lines = BCDLiveSessionLog.lines(20) } }
    }

    private func stopLog() {
        logWatch = nil
        hud.log = nil
    }
}

/// The live log as lines, newest last.
@MainActor enum BCDLiveSessionLog {
    static func lines(_ count: Int) -> [BCDLogLine] {
        let entries = LogStore.shared.entries.sorted { $0.lastTimestamp < $1.lastTimestamp }.suffix(count)
        return entries.enumerated().map { index, entry in
            BCDLogLine(id: index, text: entry.lastRaw.trimmingCharacters(in: .whitespacesAndNewlines), error: entry.level == .error)
        }
    }
}

/// A session's game as the starting screen shows it: its title and store pictures, the same
/// pictures BCD's library uses (BCDLiveStore), worked out once when the screen opens. The
/// library's own lists are dropped while a game runs, so nothing is looked up there.
@MainActor enum BCDLiveSessionArt {
    static func game(for entry: LibraryEntry?, dockAppID: Int?) -> BCDGame {
        let title = entry.map { $0.desktop == true ? "Windows desktop" : $0.title } ?? "Game"
        let appID = dockAppID ?? entry?.steamAppID
        var game = BCDGame(id: appID.map { BCDLiveID.steam($0) } ?? entry.map { BCDLiveID.local($0.id) } ?? "session",
                           title: title, store: appID == nil ? .local : .steam, state: .ready)
        if let appID {
            let owned = SteamOwnedLibrary.shared.game(appID)
            let pictures = BCDLiveStore.storePictures(appID, owned: owned)
            game.heroArt = pictures.hero
            game.headerArt = pictures.header
            game.logoArt = pictures.logo
            game.coverArt = SteamGamesRules.artwork(appID: appID) { SteamOwnedLibrary.shared.game($0) }
        } else if let entry, let name = entry.coverFile {
            game.coverArt = [LibraryModel.documents.appendingPathComponent("bcd-art/" + URL(fileURLWithPath: name).lastPathComponent)]
        } else if let entry, let id = entry.steamID {
            let pictures = BCDLiveStore.storePictures(id, owned: nil)
            game.heroArt = pictures.hero
            game.headerArt = pictures.header
            game.logoArt = pictures.logo
            game.coverArt = SteamCatalog.cover(id).map { [$0] } ?? []
        }
        return game
    }

    /// A game started from the old home screen: its title and the cover it keeps for it.
    static func game(title: String) -> BCDGame {
        var game = BCDGame(id: "home." + String(CoverStore.stableHash(title), radix: 16), title: title, store: .local, state: .ready)
        if CoverStore.hasCover(title),
           let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first {
            game.coverArt = [base.appendingPathComponent("Covers/" + String(format: "%016llx.jpg", CoverStore.stableHash(title)))]
        }
        return game
    }
}

// MARK: - The library session's menu

/// The in-game menu of a library session: everything the classic in-game menu offered, with the same
/// calls on LibraryModel, InputSettings, GameProfile, SteamOwnedLibrary and the engine's switches;
/// the touch controls' part is BCD's touch section (BCDTouchMenuSectionLive).
@MainActor final class BCDLibraryMenuSource: BCDSessionMenuSource {
    weak var menu: BCDSessionMenuModel?
    private let model = LibraryModel.shared
    private let controls = TouchControlsModel.shared
    private let input = InputSettings.shared
    private let steam = SteamOwnedLibrary.shared
    /// The menu's tools, as the classic menu: MADEIRA_SESSION_TOOLS=0 hides Aspect & scaling and
    /// the diagnostics; the diagnostics need MADEIRA_SESSION_DIAGNOSTICS=1.
    private let sessionTools = MadeiraConfig.flag("MADEIRA_SESSION_TOOLS")
    private let sessionDiagnostics = MadeiraConfig.flag("MADEIRA_SESSION_DIAGNOSTICS", fallback: false)
    /// Read again each time the menu opens (the developer overlay can change them).
    private var eco = madeira_get_eco() != 0
    private var fenceMode = FPSOverlayFenceMode.current
    private var padMode: String
    private var watchers = Set<AnyCancellable>()
    private var logShown = false

    init() {
        padMode = BCDLivePadMode.mode(of: LibraryModel.shared.activeEntry)
        // An upload's progress shows while the menu is open.
        steam.$cloudQuit.receive(on: DispatchQueue.main).sink { [weak self] _ in self?.menu?.changed() }.store(in: &watchers)
        NotificationCenter.default.publisher(for: BCDHDRLive.changed).receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.menu?.changed() }.store(in: &watchers)
    }

    func dispose() {
        BCDMenuMore.menuClosed()   // madeira-bcd (round 604): the Windows and Task Manager pages stop their timers
        pageShown(nil)
        watchers.removeAll()
    }

    var title: String {
        guard let entry = model.activeEntry else { return "Game" }
        return entry.desktop == true ? "Windows desktop" : entry.title
    }

    var subtitle: String {
        if model.launching { return "Starting" }
        let minutes = max(0, Int(Date().timeIntervalSince(model.launchStartedAt) / 60))
        return minutes < 1 ? "Playing" : "Playing · " + BCDFormat.playtime(minutes)
    }

    var tabs: [BCDSessionTab] { BCDSessionTab.allCases }

    private var appID: Int? { model.activeEntry?.steamAppID }

    func sections(_ tab: BCDSessionTab) -> [BCDMenuSection] {
        let own: [BCDMenuSection]
        switch tab {
        case .game: own = gameSections
        case .display: own = displaySections
        case .controls: own = controlSections
        case .tools: own = toolSections
        }
        return BCDMenuMore.arrange(own, tab: tab)   // madeira-bcd (2026-10-25, round 604): pause, windows, picture, touch feel, debug channels (BCDMenuMore.swift)
    }

    // MARK: Game

    private var gameSections: [BCDMenuSection] {
        var out = [BCDMenuSection(id: "resume", rows: [BCDMenuRow(id: "resume", title: "Resume", symbol: "play.fill", kind: .action(primary: true))])]
        out.append(BCDMenuSection(id: "quick", rows: [
            BCDMenuRow(id: "keyboard", title: "Keyboard", detail: "Type into the game; Esc, Ctrl, Alt and the arrows are above the keys. Fn there has F1 to F12, Win and Alt+Tab.",
                       symbol: "keyboard", kind: .action()),
            BCDExtraKeysMenu.row(),   // madeira-bcd (2026-10-25, round 603): the key bar over the game
        ]))
        if let cloud = cloudSection { out.append(cloud) }
        out.append(BCDMenuSection(id: "quit", rows: [
            BCDMenuRow(id: "quit", title: "Quit game", detail: "Progress you haven't saved is lost.", symbol: "stop.circle",
                       kind: .action(destructive: true),
                       confirm: BCDConfirm(title: "Quit the game?",
                                           message: "BCD asks the game to close, as Alt+F4 does. What you haven't saved is lost, and the game may ask you to confirm.",
                                           answers: [.init(id: "quit", title: "Quit game", destructive: true)])),
        ]))
        return out
    }

    /// "Upload saves and close BCD" (the classic quit row's function): only for a Steam game, with
    /// Steam Cloud on and signed in.
    private var cloudSection: BCDMenuSection? {
        guard appID != nil, SteamOwnedLibrary.cloudQuitEnabled, steam.signedIn else { return nil }
        var working = false
        var rows: [BCDMenuRow] = []
        switch steam.cloudQuit {
        case .working(let text)?:
            working = true
            rows.append(BCDMenuRow(id: "cloud.status", title: text, symbol: "arrow.up.circle", kind: .note(.soft)))
        case .done(let count)?:
            working = true
            rows.append(BCDMenuRow(id: "cloud.status", title: count == 0 ? "Nothing new to upload. BCD is closing…"
                                    : "\(count) save\(count == 1 ? "" : "s") sent to Steam Cloud. BCD is closing…",
                                   symbol: "checkmark.circle.fill", kind: .note(.good)))
        case .failed(let message)?:
            rows.append(BCDMenuRow(id: "cloud.status", title: "The upload didn't go through, so BCD stays open. " + message,
                                   symbol: "exclamationmark.triangle.fill", kind: .note(.warm)))
        case .conflict(let count)?:
            rows.append(BCDMenuRow(id: "cloud.status",
                                   title: "Nothing was uploaded: \(count) save\(count == 1 ? "" : "s") also changed in Steam Cloud, on another device or without a sync record here. Uploading would replace the cloud's \(count == 1 ? "copy" : "copies").",
                                   symbol: "exclamationmark.triangle.fill", kind: .note(.warm)))
            rows.append(BCDMenuRow(id: "cloud.replace", title: "Replace the cloud's saves and close", symbol: "icloud.and.arrow.up",
                                   kind: .action(destructive: true),
                                   confirm: BCDConfirm(title: "Replace Steam Cloud's saves with this device's?",
                                                       message: "This changes Steam Cloud for all your devices. BCD first keeps a copy of the cloud's saves in Files › BCD › Steam Cloud Backups.",
                                                       answers: [.init(id: "replace", title: "Replace and close", destructive: true)])))
        case nil:
            break
        }
        rows.insert(BCDMenuRow(id: "cloud.upload", title: "Upload saves and close BCD",
                               detail: "Save in the game first. BCD sends this game's saves to Steam Cloud, then closes. Steam in this session signs out as the upload starts.",
                               symbol: "icloud.and.arrow.up", kind: .action(), enabled: !working), at: 0)
        return BCDMenuSection(id: "cloud", title: "Steam Cloud", rows: rows)
    }

    /// The classic quit row's upload: the app takes the account back, uploads, and closes once done.
    private func uploadAndClose(replace: Bool) {
        guard let appID else { return }
        let steam = self.steam
        Task {
            await steam.uploadForQuit(appID, replaceCloud: replace)
            guard case .done(let count) = steam.cloudQuit else { return }
            LogStore.shared.log("[steam-cloud] app=\(appID) quit-upload done files=\(count): closing BCD")
            try? await Task.sleep(nanoseconds: 1_200_000_000)   // long enough to read the result
            BCDSessionRecorder.closingOnPurpose()   // BCD closes on purpose: the open session is no crash to ask about
            exit(0)
        }
    }

    // MARK: Display

    private var displaySections: [BCDMenuSection] {
        var out: [BCDMenuSection] = []
        let mode = model.fpsMode
        var limits: [BCDChoice] = []
        if ProMotionIntent.has30Cap || mode == 3 { limits.append(BCDChoice(id: "3", title: "30")) }
        if ProMotionIntent.has40Cap || mode == 4 { limits.append(BCDChoice(id: "4", title: "40")) }
        limits += [BCDChoice(id: "1", title: "60"), BCDChoice(id: "0", title: "Screen max"), BCDChoice(id: "2", title: "No limit")]
        out.append(BCDMenuSection(id: "fps", title: "Frame rate", rows: [
            BCDMenuRow(id: "fps", title: "Frame-rate limit", detail: BCDLiveMenuWords.fps(mode), kind: .choice(limits, selected: String(mode))),
        ]))
        if sessionTools {
            let choices = DisplayMode.allCases.map { BCDChoice(id: $0.rawValue, title: BCDLiveMenuWords.scale($0)) }
            out.append(BCDMenuSection(id: "scale", title: "Picture", rows: [
                BCDMenuRow(id: "scale", title: "Aspect & scaling", detail: BCDLiveMenuWords.scaleDetail(model.displayMode),
                           kind: .choice(choices, selected: model.displayMode.rawValue)),
            ]))
        }
        // The performance HUD: Off, 1 to 4 or Custom, and where it sits (BCDHUDMenu.swift).
        out.append(BCDHDRMenu.section(BCDHDRLive.reading()))
        out += BCDHUDMenu.sections(BCDHUDState.shared.setup, screen: hudScreen)
        out.append(BCDMenuSection(id: "eco", title: "Battery & heat", rows: [
            BCDMenuRow(id: "eco", title: "Eco mode",
                       detail: "The game's threads run on the efficiency cores: the phone stays cooler, the game runs slower. Handy while a game loads; off again for play.",
                       kind: .toggle(eco)),
        ]))
        return out
    }

    // MARK: Controls

    private var controlSections: [BCDMenuSection] {
        // The touch controls (on or off, layout, opacity, size, the editor): BCD's touch section
        // (BCDTouchMenuSectionLive, ui-touch), drawn into the menu (BCDSessionMenuModel.custom).
        var out = [BCDMenuSection(id: "touch", rows: [BCDMenuRow(id: "touch", title: "Touch controls", kind: .custom("touch"))])]

        var controller: [BCDMenuRow] = []
        if GamepadInput.keyboardMouseAvailable {
            let mode = model.controllerMode ?? ""
            controller.append(BCDMenuRow(id: "pad.mode", title: "The controller reaches the game as", kind: .choice([
                BCDChoice(id: "", title: "Its own support"), BCDChoice(id: "dinput", title: "XInput + DirectInput"),
                BCDChoice(id: "keys", title: "Keyboard & mouse"),
            ], selected: mode)))
            if mode == "keys" {
                controller.append(BCDMenuRow(id: "binds", title: "Controller binds", symbol: "gamecontroller",
                                             kind: .page("binds", value: model.controllerBinds.isEmpty ? "Default" : "\(model.controllerBinds.count) changed")))
            } else if mode == "dinput" {
                controller.append(BCDMenuRow(id: "pad.mode.note", title: "XInput + DirectInput takes effect when the game starts next.", kind: .note(.soft)))
            }
        }
        if let entry = model.activeEntry, entry.desktop != true {
            controller.append(BCDMenuRow(id: "pad.api", title: "Controller API",
                                         kind: .choice(BCDLivePadMode.choices(padMode).map { BCDChoice(id: $0.0, title: $0.1) }, selected: padMode)))
            if GamepadInput.enabled {
                let live = BCDLivePadMode.isLive(padMode)
                controller.append(BCDMenuRow(id: "pad.api.status", title: live ? "Live in this session" : "Needs a restart: quit BCD, then start this game again",
                                             symbol: live ? "checkmark.circle" : "arrow.clockwise.circle", kind: .note(live ? .good : .warm)))
            }
            controller.append(BCDMenuRow(id: "pad.api.note", title: BCDLivePadMode.note(), kind: .note(.soft)))
        }
        if !controller.isEmpty { out.append(BCDMenuSection(id: "controller", title: "Controller", rows: controller)) }
        if let gyro = BCDGyroMenu.section(for: model.activeEntry) { out.append(gyro) }   // madeira-bcd (2026-10-25, round 603): gyro aim
        out += BCDInputMenuLive.sections(model.activeEntry)   // madeira-bcd (2026-10-25, round 604): turbo and macros and the other input features

        let pointer = input.touchMode ? "touch" : (input.relative ? "relative" : "absolute")
        let speed = input.relative ? input.sensRel : input.sensAbs
        out.append(BCDMenuSection(id: "pointer", title: "Mouse & pointer", rows: [
            BCDMenuRow(id: "pointer", title: "A finger on the game", detail: BCDLiveMenuWords.pointer(pointer),
                       kind: .choice(BCDLiveMenuWords.pointers, selected: pointer)),
            BCDMenuRow(id: "speed", title: "Touch speed", kind: .slider(speed, 0.1...8, step: 0.1, text: String(format: "%.1f×", speed))),
        ]))
        return out
    }

    // MARK: Tools

    private var toolSections: [BCDMenuSection] {
        var out: [BCDMenuSection] = []
        if sessionTools && sessionDiagnostics {
            out.append(BCDMenuSection(id: "diagnostics", title: "Diagnostics", rows: [
                BCDMenuRow(id: "capture", title: "Capture the next frame", detail: "Each render pass of the next frame goes to Documents/capture, its draw list to the log.",
                           symbol: "camera.viewfinder", kind: .action()),
                BCDMenuRow(id: "gpu.sync", title: "GPU sync", detail: BCDLiveMenuWords.gpuSync(fenceMode),
                           kind: .choice(BCDLiveMenuWords.gpuSyncs, selected: String(fenceMode))),
                BCDMenuRow(id: "diagnostics.note", title: "Both affect Direct3D 12 games only.", kind: .note(.soft)),
            ]))
        }
        out.append(BCDMenuSection(id: "screenshot", title: "Screenshot", rows: [
            BCDMenuRow(id: "shot", title: "Take a screenshot",
                       detail: "The game's picture, without the menu, the HUD or the touch controls. It is kept in Files › BCD › Screenshots, and you can send it with a log from Settings › Logs.",
                       symbol: "camera", kind: .action()),
            BCDMenuRow(id: "shot.photos", title: "Also add it to Photos",
                       detail: "BCD only adds pictures; it never reads your photos. The first time, iOS asks you.", kind: .toggle(BCDScreenshotLive.toPhotos)),
        ]))
        out.append(BCDMenuSection(id: "log", title: "Log", rows: [
            BCDMenuRow(id: "log", title: "Live log", symbol: "text.alignleft", kind: .page("log", value: nil)),
            BCDMenuRow(id: "share", title: "Share logs", kind: .share(LogStore.shared.exportableLogs)),
        ]))
        return out
    }

    // MARK: Performance HUD (BCDHUDMenu.swift)

    /// The kind of window the HUD's place is kept for: from the size of the window now.
    private var hudScreen: BCDHUDScreenClass { BCDHUDScreenClass.of(BCDHUDLive.windowSize()) }

    private func applyHUD(_ outcome: BCDHUDMenu.Outcome) {
        BCDHUDState.shared.change(outcome.setup, why: "the in-game menu")
        if outcome.moving {
            // The menu closes and the HUD waits for a drag, with Auto and Done beside it.
            BCDHUDState.shared.beginMoving()
            model.menu = false
        }
    }

    // MARK: Pages

    func page(_ id: String) -> BCDMenuPage? {
        if let own = BCDMenuMore.page(id) { return own }   // madeira-bcd (round 604)
        if BCDInputMenu.handles(id) { return BCDInputMenuLive.page(id, model.activeEntry) }   // madeira-bcd (2026-10-25, round 604)
        switch id {
        case "binds": return bindsPage
        case "log": return BCDMenuMore.logPage()   // madeira-bcd (round 604): pause, clear and export on the live log page
        case BCDHUDMenu.customPage: return BCDHUDMenu.page(id, BCDHUDState.shared.setup)
        default:
            if id.hasPrefix("bind.") { return BCDLiveBinds.page(input: String(id.dropFirst(5)), binds: model.controllerBinds, controls: controls.controls) }
            return nil
        }
    }

    private var bindsPage: BCDMenuPage {
        BCDLiveBinds.overview(binds: model.controllerBinds, mouseVertical: model.padMouseVertical, controls: controls.controls)
    }

    func logLines() -> [BCDLogLine] { BCDMenuMore.logLines() }

    func pageShown(_ id: String?) {
        BCDMenuMore.pageShown(id) { [weak self] in self?.menu?.changed() }   // madeira-bcd (round 604): the Windows and Task Manager pages keep themselves fresh while open
        let wantLog = id == "log"
        guard wantLog != logShown else { return }
        logShown = wantLog
        if wantLog {
            // The log is read for the screen only while a page shows it (LogStore's idle mode).
            LogStore.shared.setDisplayActive(true)
            LogStore.shared.$entries
                .throttle(for: .seconds(1), scheduler: DispatchQueue.main, latest: true)
                .sink { [weak self] _ in DispatchQueue.main.async { self?.menu?.changed() } }
                .store(in: &logWatchers)
        } else {
            logWatchers.removeAll()
            LogStore.shared.setDisplayActive(model.launching ? model.launchLogs : model.liveLogs)
        }
    }
    private var logWatchers = Set<AnyCancellable>()

    // MARK: Doing

    func act(_ row: String) {
        if BCDMenuMore.handles(row) { return BCDMenuMore.act(row) { [weak self] in self?.menu?.changed() } }   // madeira-bcd (round 604)
        if BCDInputMenu.handles(row), BCDInputMenuLive.act(row) { menu?.changed(); return }   // madeira-bcd (2026-10-25, round 604): choosing a controller for a player
        switch row {
        case "resume": close()
        case "keyboard":
            model.menu = false
            LibraryKeyboard.show()
        case BCDExtraKeysMenu.rowID:
            model.menu = false   // madeira-bcd (2026-10-25, round 603): the key bar
            BCDExtraKeysHost.shared.toggle()
        case "cloud.upload": uploadAndClose(replace: false)
        case "shot":
            model.menu = false
            // After the menu has gone, so the picture is the game as the player sees it.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { BCDScreenshotLive.take() }
        case "capture":
            model.menu = false
            // After the menu has gone, so the frame shows what the player saw.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                madeira_capture_request(1)
                LogStore.shared.log("[capture] frame capture requested from the in-game menu")
            }
        case "bind.reset":
            model.controllerBinds = [:]
            model.padMouseVertical = 1
            model.saveCurrentProfile()
        default: break
        }
    }

    func toggle(_ row: String, _ on: Bool) {
        if BCDMenuMore.handles(row) { return BCDMenuMore.toggle(row, on) { [weak self] in self?.menu?.changed() } }   // madeira-bcd (round 604)
        if BCDInputMenu.handles(row) { return BCDInputMenuLive.change(row, on ? "1" : "0", entry: model.activeEntry) }   // madeira-bcd (2026-10-25, round 604)
        if BCDHUDMenu.owns(row) { return applyHUD(BCDHUDMenu.toggle(row, on, BCDHUDState.shared.setup)) }
        switch row {
        case "shot.photos": BCDScreenshotLive.toPhotos = on
        case "eco":
            eco = on
            madeira_set_eco(on ? 1 : 0)
        default: break
        }
    }

    func choose(_ row: String, _ value: String) {
        if BCDHDRLive.choose(row, value) { return }
        if BCDMenuMore.handles(row) { return BCDMenuMore.choose(row, value) { [weak self] in self?.menu?.changed() } }   // madeira-bcd (round 604)
        if BCDInputMenu.handles(row) { return BCDInputMenuLive.change(row, value, entry: model.activeEntry) }   // madeira-bcd (2026-10-25, round 604)
        if BCDHUDMenu.owns(row) { return applyHUD(BCDHUDMenu.choose(row, value, BCDHUDState.shared.setup, screen: hudScreen)) }
        switch row {
        case "fps": if let mode = Int(value) { model.setFPS(mode) }
        case "scale": if let mode = DisplayMode(rawValue: value) { model.displayMode = mode }
        case "pad.mode":
            model.controllerMode = value.isEmpty ? nil : value
            model.saveCurrentProfile()
        case "pad.api":
            guard let entry = model.activeEntry else { return }
            padMode = value
            BCDLivePadMode.set(value, for: entry)
        case "pointer":
            input.touchMode = value == "touch"
            input.relative = value == "relative"
            fputs("[frontend-pointer] mode=\(value)\n", stderr)
        case "gpu.sync":
            guard let mode = Int(value) else { return }
            fenceMode = mode
            FPSOverlayFenceMode.current = mode
            madeira_set_fence_mode(Int32(mode == 0 ? 7 : mode))
        default:
            if BCDGyroMenu.handles(row) { BCDGyroMenu.choose(row, value, entry: model.activeEntry); return }   // madeira-bcd (2026-10-25, round 603): gyro aim
            if row.hasPrefix("bind.") {
                var table = model.controllerBinds
                BCDLiveBinds.apply(value, input: String(row.dropFirst(5)), to: &table)
                model.controllerBinds = table
                model.saveCurrentProfile()
            }
        }
    }

    func slide(_ row: String, _ value: Double) {
        if BCDHUDMenu.owns(row) { return applyHUD(BCDHUDMenu.slide(row, value, BCDHUDState.shared.setup)) }
        switch row {
        case "speed": if input.relative { input.sensRel = value } else { input.sensAbs = value }
        case "bind.vertical":
            model.padMouseVertical = abs(value - 1) < 0.01 ? 1 : value
            model.saveCurrentProfile()
        default: break
        }
    }

    func answer(_ row: String, _ answer: String) {
        if BCDMenuMore.handles(row) { return BCDMenuMore.answer(row, answer) { [weak self] in self?.menu?.changed() } }   // madeira-bcd (round 604)
        if BCDHUDMenu.owns(row) { return applyHUD(BCDHUDMenu.answer(row, answer, BCDHUDState.shared.setup)) }
        switch (row, answer) {
        case ("quit", "quit"): model.requestQuit()
        case ("cloud.replace", "replace"): uploadAndClose(replace: true)
        default: break
        }
    }

    func close() { model.menu = false }
}

/// A game's controller API (env.MADEIRA_PAD_MODE in its own file), read and written as the
/// classic menu did: the game's file, else bcd.cfg's, else XInput.
@MainActor enum BCDLivePadMode {
    static func mode(of entry: LibraryEntry?) -> String {
        if let entry, let value = GameProfile(windowsPath: entry.windowsPath).get(GamepadInput.padModeKey) { return value.lowercased() }
        if let value = MadeiraConfig.get(GamepadInput.padModeKey), !value.isEmpty { return value.lowercased() }
        return "xinput"
    }

    /// "xinput" removes the key, unless bcd.cfg sets another mode for every game: then this game
    /// keeps an explicit "xinput", or the global value would come back at its next start.
    static func set(_ value: String, for entry: LibraryEntry) {
        let global = MadeiraConfig.get(GamepadInput.padModeKey)?.lowercased() ?? ""
        let explicit = value != "xinput" || !(global.isEmpty || global == "xinput")
        GameProfile(windowsPath: entry.windowsPath).set(GamepadInput.padModeKey, explicit ? value : nil)
        LogStore.shared.log("[hid-pad] ml2100 next start of this game: mode=\(value)")
    }

    /// Whether `mode` is what this session runs (GamepadInput.beginPadSession).
    static func isLive(_ mode: String) -> Bool {
        let session = GamepadInput.sessionHIDKind
        switch mode {
        case "hid": return session != nil
        case "dualsense", "generic": return session == mode
        default: return session == nil
        }
    }

    /// The choices (BCDPadModel.swift: the Xbox pad through XInput, a DualSense, a generic gamepad, the controller itself), plus a value typed
    /// into the game's file by hand.
    static func choices(_ current: String) -> [(String, String)] {
        var modes = BCDPadModel.choices().map { ($0 == .xbox ? "xinput" : $0.line, $0.title) }
        if !modes.contains(where: { $0.0 == current }) { modes.append((current, current)) }
        return modes
    }

    static func note() -> String {
        guard GamepadInput.enabled else {
            return "Physical controllers are switched off (env.MADEIRA_XINPUT = 0), so neither choice applies."
        }
        let session: String
        switch GamepadInput.sessionHIDKind ?? "" {
        case "dualsense": session = "a DualSense (HID)"
        case "generic": session = "a HID gamepad"
        default: session = "an XInput pad"
        }
        return "This session shows player 1 as \(session). A game looks for its controllers when it starts, so a change "
            + "applies from its next start. The Xbox controller (XInput) is what almost every game knows; a 360 and an One are the same to it. "
            + "DualSense shows Sony's PC games the controller they look for, Generic gamepad is what DirectInput games list, and The controller "
            + "itself is a DualSense when the paired one is and a generic gamepad otherwise; a game that reads only XInput then sees no controller."
    }
}

/// The keyboard-and-mouse binds (the classic binds page's function) as menu pages.
@MainActor enum BCDLiveBinds {
    static let groups: [(String, [String])] = [
        ("Sticks", ["LS", "RS"]), ("Face buttons", ["A", "B", "X", "Y"]), ("Bumpers & triggers", ["LB", "RB", "LT", "RT"]),
        ("D-pad", ["D↑", "D↓", "D←", "D→"]), ("System", ["Menu", "View", "L3", "R3"]),
    ]

    /// What an input does in this session: the table's entry, else the layout's, else the template's.
    static func effective(_ name: String, binds: [String: ControlAction], controls: [TouchControl]) -> ControlAction {
        let layout = PadBindings.build(controls: controls)
        return binds[name] ?? (name == "LS" ? layout.leftStick : name == "RS" ? layout.rightStick : layout.buttons[name] ?? .none)
    }

    static func overview(binds: [String: ControlAction], mouseVertical: Double, controls: [TouchControl]) -> BCDMenuPage {
        var sections = groups.map { group in
            BCDMenuSection(id: "binds." + group.0, title: group.0, rows: group.1.map { name in
                BCDMenuRow(id: "bind." + name, title: title(name), detail: binds[name] != nil ? "Changed" : nil,
                           kind: .page("bind." + name, value: words(effective(name, binds: binds, controls: controls), input: name)))
            })
        }
        if effective("RS", binds: binds, controls: controls).stickKeys == nil {
            sections.append(BCDMenuSection(id: "binds.mouse", title: "Right stick as mouse", rows: [
                BCDMenuRow(id: "bind.vertical", title: "Up-and-down speed",
                           detail: "A share of the side-to-side speed. Games turn and look up at different rates; lower it if looking up and down is too fast.",
                           kind: .slider(mouseVertical, 0.25...1.5, step: 0.05, text: "\(Int((mouseVertical * 100).rounded()))%")),
            ]))
        }
        sections.append(BCDMenuSection(id: "binds.reset", rows: [
            BCDMenuRow(id: "bind.reset", title: "Reset all to defaults", symbol: "arrow.counterclockwise", kind: .action(destructive: true),
                       enabled: !binds.isEmpty || mouseVertical != 1),
            BCDMenuRow(id: "bind.defaults",
                       title: "Defaults: left stick WASD, right stick the mouse, RT and LT click, D-pad arrows, A Space, B Ctrl, X E, Y R, LB Q, RB F, L3 Shift, R3 C, Start Esc, Select Tab. On its default, an input also follows the binding a touch control has in the editor.",
                       kind: .note(.soft)),
        ]))
        return BCDMenuPage(id: "binds", title: "Controller binds", sections: sections)
    }

    /// The keys an input can be given, grouped.
    static func page(input name: String, binds: [String: ControlAction], controls: [TouchControl]) -> BCDMenuPage {
        let current = effective(name, binds: binds, controls: controls)
        func pick(_ id: String, _ title: String, _ action: ControlAction) -> BCDMenuRow {
            BCDMenuRow(id: id, title: title, kind: .pick(selected: action == current))
        }
        func keys(_ list: [(String, Int32)]) -> [BCDMenuRow] { list.map { pick("key.\($0.1)", $0.0, .key($0.1)) } }
        var sections: [BCDMenuSection] = []
        if name == "LS" || name == "RS" {
            sections.append(BCDMenuSection(id: "stick", rows: [
                pick("none", name == "RS" ? "The mouse" : "Nothing", .none), pick("wasd", "WASD", .joystickWASD),
                pick("arrows", "Arrow keys", .joystickArrows),
            ]))
        } else {
            sections.append(BCDMenuSection(id: "mouse", title: "Mouse", rows: [pick("left", "Left click", .mouseLeft), pick("right", "Right click", .mouseRight)]))
            sections.append(BCDMenuSection(id: "letters", title: "Letters", rows: keys((0x41...0x5A).map { (String(UnicodeScalar(UInt8($0))), Int32($0)) })))
            sections.append(BCDMenuSection(id: "numbers", title: "Numbers", rows: keys((0x30...0x39).map { (String(UnicodeScalar(UInt8($0))), Int32($0)) })))
            sections.append(BCDMenuSection(id: "function", title: "Function keys", rows: keys((0...11).map { ("F\($0 + 1)", Int32(0x70 + $0)) })))
            sections.append(BCDMenuSection(id: "modifiers", title: "Modifiers & editing", rows: keys([
                ("Escape", 0x1B), ("Tab", 0x09), ("Shift", 0x10), ("Ctrl", 0x11), ("Alt", 0x12), ("Space", 0x20), ("Enter", 0x0D),
                ("Backspace", 0x08), ("Caps Lock", 0x14)])))
            sections.append(BCDMenuSection(id: "navigation", title: "Navigation", rows: keys([
                ("↑", 0x26), ("↓", 0x28), ("←", 0x25), ("→", 0x27), ("Insert", 0x2D), ("Delete", 0x2E), ("Home", 0x24), ("End", 0x23),
                ("Page Up", 0x21), ("Page Down", 0x22)])))
            sections.append(BCDMenuSection(id: "symbols", title: "Symbols", rows: keys([
                ("-", 0xBD), ("=", 0xBB), ("[", 0xDB), ("]", 0xDD), ("\\", 0xDC), (";", 0xBA), ("'", 0xDE), (",", 0xBC), (".", 0xBE),
                ("/", 0xBF), ("`", 0xC0)])))
            sections.append(BCDMenuSection(id: "numpad", title: "Numpad", rows: keys((0...9).map { ("Numpad \($0)", Int32(0x60 + $0)) } + [
                ("Numpad *", 0x6A), ("Numpad +", 0x6B), ("Numpad −", 0x6D), ("Numpad .", 0x6E), ("Numpad /", 0x6F)])))
            sections.append(BCDMenuSection(id: "other", title: "Other", rows: [pick("keyboard", "Show the keyboard", .keyboardToggle),
                                                                             pick("nothing", "Nothing", .none)]))
        }
        if binds[name] != nil {
            sections.insert(BCDMenuSection(id: "default", rows: [BCDMenuRow(id: "default", title: "Back to the default",
                                                                             symbol: "arrow.counterclockwise", kind: .pick(selected: false))]), at: 0)
        }
        return BCDMenuPage(id: "bind." + name, title: title(name), sections: sections)
    }

    /// An input's name on the page: the sticks in words, the rest as the pad prints them.
    static func title(_ name: String) -> String {
        switch name {
        case "LS": return "Left stick"
        case "RS": return "Right stick"
        default: return PadBindings.displayName(name)
        }
    }

    /// A choice of `page(input:)` into the binds table ("default" removes the input's entry).
    static func apply(_ choice: String, input name: String, to table: inout [String: ControlAction]) {
        let action: ControlAction?
        switch choice {
        case "default": action = nil
        case "none", "nothing": action = ControlAction.none
        case "left": action = .mouseLeft
        case "right": action = .mouseRight
        case "wasd": action = .joystickWASD
        case "arrows": action = .joystickArrows
        case "keyboard": action = .keyboardToggle
        default:
            guard choice.hasPrefix("key."), let vk = Int32(choice.dropFirst(4)) else { return }
            action = .key(vk)
        }
        table[name] = action
    }

    /// An action in the menu's words.
    static func words(_ action: ControlAction, input: String) -> String {
        switch action {
        case .none: return input == "RS" ? "The mouse" : "Nothing"
        case .mouseLeft: return "Left click"
        case .mouseRight: return "Right click"
        case .joystickWASD: return "WASD"
        case .joystickArrows: return "Arrow keys"
        case .keyboardToggle: return "Show the keyboard"
        case .pad(let name): return name
        case .key(let vk):
            switch vk {
            case 0x20: return "Space"
            case 0x0D: return "Enter"
            case 0x09: return "Tab"
            case 0x1B: return "Escape"
            case 0x10: return "Shift"
            case 0x11: return "Ctrl"
            case 0x12: return "Alt"
            case 0x08: return "Backspace"
            case 0x14: return "Caps Lock"
            case 0x2D: return "Insert"
            case 0x2E: return "Delete"
            case 0x24: return "Home"
            case 0x23: return "End"
            case 0x21: return "Page Up"
            case 0x22: return "Page Down"
            case 0x70...0x7B: return "F\(vk - 0x6F)"
            case 0x60...0x69: return "Numpad \(vk - 0x60)"
            case 0x6A: return "Numpad *"
            case 0x6B: return "Numpad +"
            case 0x6D: return "Numpad −"
            case 0x6E: return "Numpad ."
            case 0x6F: return "Numpad /"
            default: return ControlAction.keyLabel(vk)
            }
        }
    }
}

/// The menu's words for the engine's choices.
enum BCDLiveMenuWords {
    static let pointers = [BCDChoice(id: "absolute", title: "Trackpad"), BCDChoice(id: "touch", title: "Tap to click"),
                           BCDChoice(id: "relative", title: "Mouse-look")]
    static let gpuSyncs = [BCDChoice(id: "1", title: "F1"), BCDChoice(id: "6", title: "F6"), BCDChoice(id: "5", title: "F5"),
                           BCDChoice(id: "0", title: "F0")]

    static func fps(_ mode: Int) -> String {
        switch mode {
        case 0: return "As fast as the screen refreshes (\(UIScreen.main.maximumFramesPerSecond) Hz)."
        case 2: return "No limit: the game runs as fast as it can; the screen shows what it can."
        case 3: return "30 frames a second: cooler and longer on battery."
        case 4: return "40 frames a second, on a 120 Hz screen."
        default: return "60 frames a second."
        }
    }

    static func scale(_ mode: DisplayMode) -> String {
        switch mode {
        case .fit: return "Fit"
        case .fill: return "Fill"
        case .stretch: return "Stretch"
        case .aspect: return "Game's shape"
        }
    }

    static func scaleDetail(_ mode: DisplayMode) -> String {
        switch mode {
        case .fit: return "The whole picture, as large as fits, with bars where the shapes differ."
        case .fill: return "The screen filled; the picture's edges are cut."
        case .stretch: return "The picture stretched to the screen's shape."
        case .aspect: return "Like Fit, on the shape the game really draws."
        }
    }

    static func pointer(_ mode: String) -> String {
        switch mode {
        case "touch": return "The pointer jumps to your finger and a tap clicks there. Hold, then move, to drag; two fingers right-click or scroll."
        case "relative": return "Dragging moves the mouse, for looking around. A tap clicks."
        default: return "Drag to move the pointer, as on a trackpad. A tap clicks."
        }
    }

    static func gpuSync(_ mode: Int) -> String {
        switch mode {
        case 6: return "F6: waits only where the game's barriers ask for it."
        case 5: return "F5: holds render passes at the fragment stage."
        case 0: return "F0: no sync at all. Flicker is likely; for testing."
        default: return "F1, the default: each pass waits for the one before."
        }
    }
}

// MARK: - The developer overlay's readings

/// Samples the engine's counters for the developer interface's overlay twice a second, only while it is shown: memory against the limit, the
/// present count, the frame-rate limit and the heat, with its switches (as the classic overlay did, which also held ProMotion for the limit
/// and logged [present]). A library session's performance HUD is BCDHUDLive.swift.
@MainActor final class BCDOverlaySampler {
    let feed = BCDPerformanceFeed()
    private var timer: Timer?
    private var samples: [(t: Double, count: UInt64)] = []
    private var thermal = ProcessInfo.processInfo.thermalState
    private var ticks = 0
    private var lastLoggedPresent: UInt64 = .max
    private var captureUntil = 0.0

    init() {
        feed.tapChip = { [weak self] id in self?.tap(id) }
    }

    func start() {
        guard timer == nil else { return }
        // The classic overlay held the panel's rate for the limit in force when it appeared.
        ProMotionIntent.apply(mode: madeira_get_vsync_locked())
        samples = [(CACurrentMediaTime(), madeira_get_present_count())]
        tick()
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.tick() } }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        let now = CACurrentMediaTime()
        let count = madeira_get_present_count()
        samples.append((now, count))
        if samples.count > 8 { samples.removeFirst(samples.count - 8) }
        let fps = BCDFrameRate.fps(samples)
        let state = ProcessInfo.processInfo.thermalState
        if state != thermal {
            LogStore.shared.log("[thermal] \(DeviceLoadDiagnostics.thermalName(thermal)) -> \(DeviceLoadDiagnostics.thermalName(state)) at present \(count), \(String(format: "%.0f", fps)) FPS")
            thermal = state
        }
        let footprint = Self.footprintMB()
        var reading = BCDPerformanceReading()
        let limit = Self.limitMB(footprint: footprint)
        reading.readings = deskReadings(fps: fps, count: count, footprint: footprint, limit: limit)
        reading.chips = chips()
        ticks += 1
        // The present count in the log: every 2 s while it changes or is still zero.
        if ticks % 4 == 0, count != lastLoggedPresent || count == 0 {
            LogStore.shared.log("[present] count=\(count) fps=\(String(format: "%.1f", fps)) mem=\(footprint)/\(limit)MB thermal=\(Self.shortHeat(thermal))\(ProcessInfo.processInfo.isLowPowerModeEnabled ? " lowpower" : "")",
                                level: count == 0 ? .debug : .info)
            lastLoggedPresent = count
        }
        if feed.reading != reading { feed.reading = reading }
    }

    private func deskReadings(fps: Double, count: UInt64, footprint: Int, limit: Int) -> [BCDReading] {
        let free = limit - footprint
        let memoryTone: BCDSessionTone = footprint == 0 ? .soft : free > 768 ? .good : free > 128 ? .warm : .hot
        return [
            BCDReading(id: "fps", text: String(format: "%.1f FPS", fps), tone: BCDFrameRate.tone(fps)),
            BCDReading(id: "mem", text: "\(footprint)/\(limit) MB", tone: memoryTone),
            BCDReading(id: "present", text: "#\(count)", tone: .soft),
            heat(),
        ]
    }

    /// The [present] line's word for the heat, as before.
    static func shortHeat(_ state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal: return "OK"
        case .fair: return "WARM"
        case .serious: return "HOT"
        case .critical: return "CRIT"
        @unknown default: return "?"
        }
    }

    private func heat() -> BCDReading {
        switch thermal {
        case .nominal: return BCDReading(id: "heat", text: "Cool", tone: .good)
        case .fair: return BCDReading(id: "heat", text: "Warm", tone: .warm)
        case .serious: return BCDReading(id: "heat", text: "Hot", tone: .hot)
        case .critical: return BCDReading(id: "heat", text: "Too hot", tone: .hot)
        @unknown default: return BCDReading(id: "heat", text: "?", tone: .soft)
        }
    }

    // MARK: The developer's switches (the classic overlay's pills)

    private func chips() -> [BCDOverlayChip] {
        let mode = madeira_get_vsync_locked()
        let fence = FPSOverlayFenceMode.current
        return [
            BCDOverlayChip(id: "limit", text: Self.limitLabel(mode), tone: mode == 2 ? .warm : .normal),
            BCDOverlayChip(id: "eco", text: "ECO", tone: .good, on: madeira_get_eco() != 0),
            BCDOverlayChip(id: "sync", text: "F\(fence)", tone: fence == 1 ? .normal : fence == 0 ? .hot : .warm),
            BCDOverlayChip(id: "capture", text: "CAP", tone: .soft, on: CACurrentMediaTime() < captureUntil),
        ]
    }

    static func limitLabel(_ mode: Int32) -> String {
        switch mode {
        case 1: return "60"
        case 3: return "30"
        case 4: return "40"
        case 0: return "MAX \(UIScreen.main.maximumFramesPerSecond)"
        default: return "RAW"
        }
    }

    /// 60 → MAX → RAW → 30 → 40 → 60, the 30 and 40 only where DXMT has them.
    static func nextLimit(_ mode: Int32) -> Int32 {
        switch mode {
        case 1: return 0
        case 0: return 2
        case 2: return ProMotionIntent.has30Cap ? 3 : ProMotionIntent.has40Cap ? 4 : 1
        case 3: return ProMotionIntent.has40Cap ? 4 : 1
        default: return 1
        }
    }

    private func tap(_ id: String) {
        switch id {
        case "limit":
            let next = Self.nextLimit(madeira_get_vsync_locked())
            fputs("[hud] tap fps-cap -> \(Self.limitLabel(next))\n", stderr)
            ThermalPacing.shared.userChose()   // env.MADEIRA_THERMAL_CAP: a cap chosen by hand is never replaced or restored
            madeira_set_vsync_locked(next)
            ProMotionIntent.apply(mode: next)
        case "eco":
            madeira_set_eco(madeira_get_eco() != 0 ? 0 : 1)
        case "sync":
            let now = FPSOverlayFenceMode.current
            let next = now == 1 ? 6 : now == 6 ? 5 : now == 5 ? 0 : 1
            FPSOverlayFenceMode.current = next
            madeira_set_fence_mode(Int32(next == 0 ? 7 : next))
        case "capture":
            madeira_capture_request(1)
            captureUntil = CACurrentMediaTime() + 1
        default: break
        }
        feed.reading.chips = chips()
    }

    // MARK: Memory

    /// The footprint jetsam counts (phys_footprint), in MB.
    static func footprintMB() -> Int {
        var info = task_vm_info_data_t()
        var size = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(size)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &size) }
        }
        return result == KERN_SUCCESS ? Int(info.phys_footprint / 1_048_576) : 0
    }

    /// The limit on this device, measured: the footprint plus what iOS still allows.
    static func limitMB(footprint: Int) -> Int {
        let available = os_proc_available_memory()
        return available > 0 ? footprint + Int(available / 1_048_576) : 4096
    }
}

// MARK: - The touch-controls window: overlay and menu

/// The developer overlay: beside the game in landscape (`compact`) or
/// above it upright. A tap on the readings folds it to a dot, a tap on the dot brings it back.
struct BCDDeskOverlay: View {
    var compact = false
    @StateObject private var sampler = BCDDeskSamplerHolder()
    @State private var folded = false

    var body: some View {
        Group {
            if folded {
                Circle().fill(Color.black.opacity(0.35)).frame(width: 12, height: 12)
            } else {
                BCDPerformanceOverlay(feed: sampler.sampler.feed, maxWidth: compact ? 140 : 420, vertical: compact)
            }
        }
        .onTapGesture { folded.toggle() }
        .onAppear { sampler.sampler.start() }
        .onDisappear { sampler.sampler.stop() }
    }
}

@MainActor final class BCDDeskSamplerHolder: ObservableObject {
    let sampler = BCDOverlaySampler()
}

/// The in-game menu of the touch controls' window: on the
/// touch-controls window in landscape (`.overlay`), as a sheet upright (`.sheet`).
struct BCDDeskSessionMenu: View {
    var presentation: BCDInGameMenu.Presentation = .overlay
    let onClose: () -> Void
    @StateObject private var holder: BCDDeskMenuHolder

    init(presentation: BCDInGameMenu.Presentation = .overlay, onClose: @escaping () -> Void) {
        self.presentation = presentation
        self.onClose = onClose
        _holder = StateObject(wrappedValue: BCDDeskMenuHolder(onClose: onClose))
    }

    var body: some View {
        GeometryReader { geo in
            BCDInGameMenu(model: holder.model, insets: BCDSessionInsets.of(geo), presentation: presentation)
        }
        .ignoresSafeArea(edges: presentation == .overlay ? .all : [])
        .preferredColorScheme(.dark)
        .background(presentation == .sheet ? BCDSessionStyle.panel : Color.clear)
    }
}

@MainActor final class BCDDeskMenuHolder: ObservableObject {
    let model: BCDSessionMenuModel
    init(onClose: @escaping () -> Void) {
        let source = BCDDeskMenuSource(onClose: onClose)
        model = BCDSessionMenuModel(source: source)
        model.custom["touch"] = { AnyView(BCDTouchMenuSectionLive(closeMenu: onClose)) }
        source.menu = model
    }
}

/// The classic session panel's functions: frame-rate limit, picture fit, the overlay, Eco, the pointer, the
/// diagnostics, the live log and sharing the logs; the touch controls through BCD's touch section.
@MainActor final class BCDDeskMenuSource: BCDSessionMenuSource {
    weak var menu: BCDSessionMenuModel?
    private let onClose: () -> Void
    private let input = InputSettings.shared
    private let session = GameSession.shared
    private var eco = madeira_get_eco() != 0
    private var limit = FrameLimit.current
    private var fit = DisplayFit.current
    private var fence = FPSOverlayFenceMode.current
    private var logWatch: AnyCancellable?
    private var hdrWatch: AnyCancellable?

    init(onClose: @escaping () -> Void) {
        self.onClose = onClose
        hdrWatch = NotificationCenter.default.publisher(for: BCDHDRLive.changed).receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.menu?.changed() }
    }

    var title: String { session.title.isEmpty ? "This session" : session.title }
    var subtitle: String { "Playing" }
    var tabs: [BCDSessionTab] { BCDSessionTab.allCases }

    func sections(_ tab: BCDSessionTab) -> [BCDMenuSection] {
        switch tab {
        case .game:
            return [
                BCDMenuSection(id: "resume", rows: [BCDMenuRow(id: "resume", title: "Resume", symbol: "play.fill", kind: .action(primary: true))]),
                BCDMenuSection(id: "quick", rows: [
                    BCDMenuRow(id: "keyboard", title: "Keyboard", symbol: "keyboard", kind: .action()),
                    BCDExtraKeysMenu.row(),   // madeira-bcd (2026-10-25, round 603): the key bar over the game
                ]),
            ]
        case .display:
            let limits = FrameLimit.allCases.map { BCDChoice(id: String($0.rawValue), title: $0 == .display ? "Screen max" : $0 == .unlimited ? "No limit" : String($0.label.prefix(2))) }
            return [
                BCDMenuSection(id: "fps", title: "Frame rate", rows: [
                    BCDMenuRow(id: "fps", title: "Frame-rate limit", kind: .choice(limits, selected: String(limit.rawValue))),
                ]),
                BCDMenuSection(id: "fit", title: "Picture", rows: [
                    BCDMenuRow(id: "fit", title: "Picture", detail: fit == .fit ? "The whole picture, as large as fits." : "The picture stretched to the screen.",
                               kind: .choice(DisplayFit.allCases.map { BCDChoice(id: $0.rawValue, title: $0 == .fit ? "Fit" : "Stretch") }, selected: fit.rawValue)),
                    BCDMenuRow(id: "overlay", title: "Performance overlay", kind: .toggle(session.showPerformance)),
                ]),
                BCDHDRMenu.section(BCDHDRLive.reading()),
                BCDMenuSection(id: "eco", title: "Battery & heat", rows: [
                    BCDMenuRow(id: "eco", title: "Eco mode",
                               detail: "The game's threads run on the efficiency cores: cooler and slower. It does not limit the frame rate. Handy while a game loads; off again for play.",
                               kind: .toggle(eco)),
                ]),
            ]
        case .controls:
            let pointer: String = input.mode == .touch ? "touch" : input.mode == .relative ? "relative" : "absolute"
            return [
                // BCD's touch section (BCDTouchMenuSectionLive): on or off, layout, opacity, the editor.
                BCDMenuSection(id: "touch", rows: [
                    BCDMenuRow(id: "touch", title: "Touch controls", kind: .custom("touch")),
                    BCDMenuRow(id: "touch.note", title: "Drawn in landscape. A controller reaches games as an Xbox pad.", kind: .note(.soft)),
                ]),
                BCDMenuSection(id: "pointer", title: "Mouse & pointer", rows: [
                    BCDMenuRow(id: "pointer", title: "A finger on the game", detail: BCDLiveMenuWords.pointer(pointer),
                               kind: .choice(BCDLiveMenuWords.pointers, selected: pointer)),
                    BCDMenuRow(id: "sens.abs", title: "Touch speed", kind: .slider(input.sensAbs, 0.1...8, step: 0.1, text: String(format: "%.2f", input.sensAbs))),
                    BCDMenuRow(id: "sens.rel", title: "Mouse-look speed", kind: .slider(input.sensRel, 0.1...8, step: 0.1, text: String(format: "%.2f", input.sensRel))),
                ]),
            ]
        case .tools:
            return [
                BCDMenuSection(id: "diagnostics", title: "Diagnostics", rows: [
                    BCDMenuRow(id: "capture", title: "Capture the next frame", detail: "Each render pass of the next frame goes to Documents/capture, its draw list to the log.",
                               symbol: "camera.viewfinder", kind: .action()),
                    BCDMenuRow(id: "gpu.sync", title: "GPU sync", detail: BCDLiveMenuWords.gpuSync(fence),
                               kind: .choice(BCDLiveMenuWords.gpuSyncs, selected: String(fence))),
                ]),
                BCDMenuSection(id: "log", title: "Log", rows: [
                    BCDMenuRow(id: "log", title: "Live log", symbol: "text.alignleft", kind: .page("log", value: nil)),
                    BCDMenuRow(id: "share", title: "Share logs", kind: .share(LogStore.shared.exportableLogs)),
                ]),
            ]
        }
    }

    func page(_ id: String) -> BCDMenuPage? { id == "log" ? BCDMenuPage(id: "log", title: "Live log", kind: .log) : nil }
    func logLines() -> [BCDLogLine] { BCDLiveSessionLog.lines(120) }

    func pageShown(_ id: String?) {
        if id == "log" {
            guard logWatch == nil else { return }
            logWatch = LogStore.shared.$entries
                .throttle(for: .seconds(1), scheduler: DispatchQueue.main, latest: true)
                .sink { [weak self] _ in DispatchQueue.main.async { self?.menu?.changed() } }
        } else {
            logWatch = nil
        }
    }

    func act(_ row: String) {
        switch row {
        case "resume": close()
        case "keyboard":
            close()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { MetalBackedView.toggleKeyboard() }
        case BCDExtraKeysMenu.rowID:
            close()   // madeira-bcd (2026-10-25, round 603): the key bar
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { BCDExtraKeysHost.shared.toggle() }
        case "capture":
            close()
            // After the menu has gone, so the frame shows what the player saw.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                madeira_capture_request(1)
                LogStore.shared.log("[capture] frame capture requested from the game menu")
            }
        default: break
        }
    }

    func toggle(_ row: String, _ on: Bool) {
        switch row {
        case "overlay": session.showPerformance = on
        case "eco":
            eco = on
            madeira_set_eco(on ? 1 : 0)
        default: break
        }
    }

    func choose(_ row: String, _ value: String) {
        if BCDHDRLive.choose(row, value) { return }
        switch row {
        case "fps":
            guard let raw = Int32(value), let next = FrameLimit(rawValue: raw) else { return }
            limit = next
            FrameLimit.apply(next)
        case "fit":
            guard let next = DisplayFit(rawValue: value) else { return }
            fit = next
            DisplayFit.current = next
        case "pointer":
            input.mode = value == "touch" ? .touch : value == "relative" ? .relative : .absolute
        case "gpu.sync":
            guard let mode = Int(value) else { return }
            fence = mode
            FPSOverlayFenceMode.current = mode
            madeira_set_fence_mode(Int32(mode == 0 ? 7 : mode))
            LogStore.shared.log("[hud] GPU sync F\(mode) from the game menu")
        default: break
        }
    }

    func slide(_ row: String, _ value: Double) {
        switch row {
        case "sens.abs": input.sensAbs = value
        case "sens.rel": input.sensRel = value
        default: break
        }
    }

    func answer(_ row: String, _ answer: String) {}

    func close() {
        pageShown(nil)
        onClose()
    }
}

// MARK: - The starting screen's host

/// The starting screen's host: a window above the game and the touch controls that covers a
/// session's start, with the classic splash's rules: it closes when the game has shown frames on
/// two looks in a row, or 8 s after a window of the game appeared ([win-pos]) for programs that
/// never present one, or on Skip; a refused launch hides it.
@MainActor enum BCDStartingHost {
    private static var window: UIWindow?
    private static var hiding = false

    static func show(title: String) {
        guard window == nil else { return }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first else { return }
        let w = UIWindow(windowScene: scene)
        // Above the touch-controls window (+101) and the joystick pad (+100).
        w.windowLevel = .normal + 102
        w.backgroundColor = .black
        let host = UIHostingController(rootView: BCDDeskStarting(title: title))
        host.view.backgroundColor = .black
        w.rootViewController = host
        w.isHidden = false
        window = w
    }

    static func hide() {
        guard let w = window, !hiding else { return }
        hiding = true
        UIView.animate(withDuration: 0.2, animations: { w.alpha = 0 }, completion: { _ in
            w.isHidden = true
            w.rootViewController = nil
            BCDStartingHost.window = nil
            BCDStartingHost.hiding = false
        })
    }
}

struct BCDDeskStarting: View {
    let title: String
    @StateObject private var watch: BCDDeskStartingWatch

    init(title: String) {
        self.title = title
        _watch = StateObject(wrappedValue: BCDDeskStartingWatch(title: title))
    }

    var body: some View {
        GeometryReader { geo in
            BCDStartingScreen(feed: watch.feed, insets: BCDSessionInsets.of(geo)) { watch.act($0) }
        }
        .ignoresSafeArea()
        .preferredColorScheme(.dark)
        .onAppear { watch.start() }
        .onDisappear { watch.stop() }
    }
}

@MainActor final class BCDDeskStartingWatch: ObservableObject {
    let feed: BCDStartingFeed
    private var timer: Timer?
    private var ticks = 0
    private var framesSeen = 0
    private var windowSeenAt: Int?
    private let started = Date()

    init(title: String) {
        var info = BCDStartingInfo(game: BCDLiveSessionArt.game(title: title))
        info.canSkip = true
        info.tip = "The game menu: the ≡ button beside the game."
        feed = BCDStartingFeed(info)
    }

    func start() {
        guard timer == nil else { return }
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.check() } }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    func act(_ action: BCDStartingAction) {
        switch action {
        case .toggleLog:
            feed.info.logShown.toggle()
            feed.info.logLines = feed.info.logShown ? BCDLiveSessionLog.lines(60) : []
        case .skip: BCDStartingHost.hide()
        default: break
        }
    }

    /// The classic splash's rule, four times a second; the words once a second.
    private func check() {
        ticks += 1
        if madeira_get_present_count() > 0 {
            framesSeen += 1
            if framesSeen >= 2 { BCDStartingHost.hide(); stop() }
            return
        }
        if windowSeenAt == nil, LogStore.shared.entries.contains(where: { $0.lastRaw.contains("[win-pos]") }) { windowSeenAt = ticks }
        if let seen = windowSeenAt, ticks - seen >= 32 { BCDStartingHost.hide(); stop(); return }
        guard ticks % 4 == 0 else { return }
        var info = feed.info
        info.seconds = Int(Date().timeIntervalSince(started))
        info.step = wine_process_is_running() != 0 ? .loading : .starting
        if info.logShown { info.logLines = BCDLiveSessionLog.lines(60) }
        if info != feed.info { feed.info = info }
    }
}

// MARK: - The keyboard's key bar

/// The bar over the iOS keyboard (LibraryKeyboard), in BCD's look: the same keys and the same
/// posting as before (Ctrl, Shift and Alt stay down until pressed again; Done hides it).
@MainActor enum BCDKeyboardAccessory {
    private static let codes: [String: Int32] = ["esc": 0x1B, "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "tab": 0x09, "enter": 0x0D,
                                                 "left": 0x25, "up": 0x26, "down": 0x28, "right": 0x27]

    static func view(for input: LibraryKeyInput) -> UIView {
        let model = BCDKeyboardBarModel()
        let bar = BCDKeyboardBar(model: model, press: { [weak input] key in
            // Paste types the clipboard text (read only now, on this tap); any other key of the bar ends a paste still being delivered.
            if key.id == "paste" { HardwareInput.shared.pasteClipboard(); return }
            HardwareInput.shared.cancelPaste()
            guard let input, let vk = codes[key.id] else { return }
            if key.latches {
                if input.held.contains(vk) { input.held.remove(vk); winios_post_key(vk, 0) } else { input.held.insert(vk); winios_post_key(vk, 1) }
                model.latched = Set(codes.filter { input.held.contains($0.value) }.map(\.key))
            } else {
                input.press(vk)
            }
        }, done: { LibraryKeyboard.hide() }, extra: { [weak input] key in
            // madeira-bcd (2026-10-25, round 603): the second page's keys (BCDExtraKeys.swift) go out with the held modifiers left alone;
            // its Ctrl, Shift and Alt are the first page's latches (above), and the first page's keys are posted as before.
            guard let input, let extra = BCDExtraKeys.key(key.id), !extra.isModifier else { return }
            BCDKeySender.send(extra.action, held: input.held)
        })
        return BCDHostedBar(host: UIHostingController(rootView: bar))
    }
}

/// A UIKit view holding the SwiftUI bar (and its controller) for UIKit's keyboard.
final class BCDHostedBar: UIView {
    private let host: UIViewController
    init(host: UIViewController) {
        self.host = host
        super.init(frame: CGRect(x: 0, y: 0, width: UIScreen.main.bounds.width, height: 52))
        autoresizingMask = [.flexibleWidth]
        host.view.backgroundColor = .clear
        host.view.frame = bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        addSubview(host.view)
    }
    required init?(coder: NSCoder) { nil }
    override var intrinsicContentSize: CGSize { CGSize(width: UIView.noIntrinsicMetric, height: 52) }
}
