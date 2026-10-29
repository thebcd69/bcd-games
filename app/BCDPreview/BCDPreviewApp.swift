// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

/// BCD's interface on sample data, for the simulator (tools/ui-preview.py builds it from the
/// app's BCD*.swift files and photographs every screen on every device size). Arguments:
///   -BCDTab home|library|downloads|settings   -BCDOrientation landscape|portrait
///   -BCDTab touch[-keys|-editor|-layouts|-picker|-menu]   the touch controls (BCDTouchPreview.swift)
///   -BCDTab controllers                      the controller screen (BCDControllerHubSample.swift)
///   -BCDTab keybar[-held|-fn]                the extra keys: the floating key bar, the keyboard bar's second page (BCDPreviewExtraKeys.swift)
///   -BCDLook <body>,<accent>,<symbols>,<press>   the touch controls' look (BCDTouchLook.swift): graphite,green,playstation,ring; empty is BCD's own
///   -BCDTab input-players | input-radial[-open|-slide|-left]   the Players card and the radial menu (BCDPreviewInput.swift)
///   -BCDFocus <control id>                    -BCDEmpty YES (a new install's library)
///   -BCDButtons x,down,a                      a controller's presses, one every 0.4 s from 1.6 s after the
///                                             screen is up (up, down, left, right, a, b, x, y, lb, rb, menu;
///                                             anything else, such as w, waits a slot: a page takes a moment to come up)
///   -BCDSection differ                        with -BCDTab library: a game whose Steam Cloud saves differ
///   -BCDSection launcher-battlenet[+installed|downloading|failed|starting]   with -BCDTab library: a launcher's chip and card (BCDLauncherSample.swift)
///   -BCDSection memory|graphics|...           with -BCDTab settings: open that section;
///                                             section+state picks a sample's state (vcruntime+installed)
///   -BCDTab game[-<game id>[-<what>]]          a game's page (BCDGamePageSample.swift)
///   -BCDTab add[-games|-pick]                  Add a game (BCDAddGameSample.swift)
///   -BCDTab epic-ask[-offline|-owned|-signin]  the question BCD asks when an Epic game's signed-in start can't be made (BCDPreviewEpicAsk.swift)
///   -BCDOrientationLock landscape|landscape-camera-left|landscape-camera-right   the screen orientation lock of a game's session
///                                             (BCDOrientation.swift), turned on 2 s after the screen is up; -BCDOrientationRelease <seconds>
///                                             turns it off that long after that. It names the same lock and app delegate as the app's.
@main
struct BCDPreviewApp: App {
    @UIApplicationDelegateAdaptor(BCDOrientationDelegate.self) private var orientationDelegate
    @StateObject private var store: BCDStore
    @StateObject private var focus = BCDFocus(watchControllers: false)
    private let actions = BCDPreviewActions()

    init() {
        let defaults = UserDefaults.standard
        if let theme = defaults.string(forKey: "BCDTheme") { BCDThemeStore.shared.select(theme) }
        let actions = BCDPreviewActions()
        BCDUpdateChecker.shared.usePreviewSample()
        // -BCDLook graphite,green,playstation,ring: the touch controls' look (round 604; body, colour when pressed, symbols, how a press shows).
        if let look = defaults.string(forKey: "BCDLook") {
            let parts = look.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
            for (line, value) in zip(BCDTouchLook.lineNames, parts) {
                BCDInputPrefs.shared.set(BCDTouchLookModel.feature, game: BCDInputPrefs.everyone, line: line, value: value)
            }
            BCDTouchLookModel.shared.reload()
        }
        let sample = BCDStore.preview(empty: defaults.bool(forKey: "BCDEmpty"), actions: actions)
        // -BCDSection differ (screen "library.differ"): Hollow Knight's Steam Cloud saves differ from the device's.
        if defaults.string(forKey: "BCDSection") == "differ", sample.games.contains(where: { $0.id == "367520" }) {
            sample.savesDiffer = ["367520"]
        }
        _store = StateObject(wrappedValue: sample)
        BCDGameDetailSource.backend = BCDGamePageSample()
        BCDControllerHub.shareSample(empty: defaults.bool(forKey: "BCDEmpty"))   // Settings › Controls › Controllers
        // Round 604: the controller screen's Players card on sample controllers (an empty install has none).
        BCDPlayersModel.shared.preview(names: defaults.bool(forKey: "BCDEmpty") ? [] : ["Xbox Wireless Controller", "DualSense Wireless Controller", nil, nil],
                                       rumble: [1: 50], touchSlot: defaults.bool(forKey: "BCDEmpty") ? 0 : 1)
        BCDAddGameSource.backend = BCDAddGameSample()
        BCDLauncherSample.prepare(defaults.string(forKey: "BCDSection"))   // the launchers' chips (Battle.net, EA app) on sample data
    }

    var body: some Scene {
        WindowGroup {
            BCDPreviewSettingsSection.over(root, focus: focus)
                .modifier(BCDPreviewExtraKeysScreens(store: store))   // -BCDTab keybar[-held|-fn] (BCDPreviewExtraKeys.swift)
                .onAppear {
                    store.actions = actions
                    orient()
                    focus.preview(focused: UserDefaults.standard.string(forKey: "BCDFocus"))
                    BCDPreviewMeasure.start(store: store, focus: focus)   // -BCDSampleGames, -BCDMeasure (tools/ui-memory.py)
                    pressButtons(UserDefaults.standard.string(forKey: "BCDButtons"))
                    lockOrientation()
                    // tools/ui-preview.py waits for this line before it photographs the screen.
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                        print("BCDPreview: ready")
                        fflush(stdout)
                    }
                }
                .modifier(BCDPreviewSessionScreens(store: store, focus: focus))   // -BCDTab ingame-menu|starting|overlay (BCDPreviewSession.swift)
        }
    }

    @ViewBuilder private var root: some View {
        let tab = UserDefaults.standard.string(forKey: "BCDTab") ?? "home"
        if tab == "settings" {
            let section = UserDefaults.standard.string(forKey: "BCDSection")
                .flatMap { BCDSettingsSection(name: String($0.split(separator: "+").first ?? "")) }
            BCDSettingsRoot(store: store, close: {}, section: section).environmentObject(focus)
        } else if tab == "controllers" {
            BCDControllerPreview(focus: focus)
        } else if tab.hasPrefix("game") {
            BCDGamePreviewRoot(spec: tab).environmentObject(store).environmentObject(focus)
        } else if tab.hasPrefix("epic-ask") {
            BCDEpicAskPreview(spec: tab, store: store, focus: focus)
        } else if tab.hasPrefix("add") {
            BCDAddGamePreviewRoot(spec: tab).environmentObject(store).environmentObject(focus)
        } else if tab.hasPrefix("input-") {
            BCDPreviewInputRoot(spec: tab, store: store).environmentObject(focus)   // round 604: the Players card, the radial menu (BCDPreviewInput.swift)
        } else if tab.hasPrefix("touch") {
            BCDTouchPreviewRoot(spec: tab, store: store).environmentObject(focus)
        } else {
            BCDFrontEnd(store: store, tab: BCDTab(rawValue: tab) ?? .home, focus: focus)
        }
    }

    /// -BCDButtons: a controller's presses through the same BCDFocus the app's controller drives, so a
    /// screenshot shows what a person with a controller sees after them.
    private func pressButtons(_ keys: String?) {
        guard let keys else { return }
        // The first press comes after the screen has turned (a turn replaces some controls, and a control that
        // goes takes the ring with it): the ring is put back where -BCDFocus said just before it.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.4) {
            focus.preview(focused: UserDefaults.standard.string(forKey: "BCDFocus"))
        }
        for (index, key) in keys.split(separator: ",").enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.6 + 0.4 * Double(index)) {
                switch key {
                case "up": focus.move(.up)
                case "down": focus.move(.down)
                case "left": focus.move(.left)
                case "right": focus.move(.right)
                case "a": focus.press(.a)
                case "b": focus.press(.b)
                case "x": focus.press(.x)
                case "y": focus.press(.y)
                case "lb": focus.press(.leftShoulder)
                case "rb": focus.press(.rightShoulder)
                case "menu": focus.press(.menu)
                default: break
                }
            }
        }
    }

    /// -BCDOrientationLock: the session's lock, on 2 s after the screen is up, and off again after -BCDOrientationRelease seconds.
    private func lockOrientation() {
        let defaults = UserDefaults.standard
        guard let word = defaults.string(forKey: "BCDOrientationLock"), case let choice = BCDOrientationChoice(setting: word), choice.locked else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            BCDOrientationLock.shared.onLog = { print($0); fflush(stdout) }
            BCDOrientationLock.shared.apply(choice)
        }
        let release = defaults.double(forKey: "BCDOrientationRelease")
        if release > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2 + release) { BCDOrientationLock.shared.apply(.free) }
        }
    }

    private func orient() {
        let wanted = UserDefaults.standard.string(forKey: "BCDOrientation") ?? "landscape"
        guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene else { return }
        let mask: UIInterfaceOrientationMask = wanted == "portrait" ? .portrait : .landscapeRight
        scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { _ in }
    }
}
