// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
#if canImport(UIKit)
import UIKit
#endif

// The screen orientation of a game session (Settings › Graphics, a game's page › Controls).
//
// Off, which is the default, is what BCD always did: the interface follows the way the device is held, in the
// three orientations Info.plist declares (upright and both landscapes). On, for a game's session only, the
// interface stays in landscape whatever way the device is held (lying down, say), and it goes back to following
// the device when the session ends. The choice is the game's own when it has one (its page), else the one in
// Settings; Settings keeps it in bcd.cfg as `orientation-lock`, a game's page in its library entry.
//
// This file is the choice and the rule that picks it (plain Swift: tests/host/check-bcd-orientation.py compiles it
// on the Mac and runs it) and, under UIKit, the lock itself: the app delegate answers which orientations the
// windows may take, and the scenes are asked to turn. BCDOrientationLive.swift joins it to the engine.

/// What a session does with the orientation.
enum BCDOrientationChoice: String, CaseIterable, Equatable {
    /// The interface follows the device.
    case free = "off"
    /// Landscape on either side.
    case landscape = "landscape"
    /// Landscape with the front camera on the left of the screen, whichever way the device is held.
    case landscapeCameraLeft = "landscape-camera-left"
    /// Landscape with the front camera on the right.
    case landscapeCameraRight = "landscape-camera-right"

    /// What bcd.cfg's `orientation-lock` says; unset, empty or a word this build does not know is `.free`.
    init(setting: String?) {
        self = setting.flatMap { BCDOrientationChoice(rawValue: $0.trimmingCharacters(in: .whitespaces).lowercased()) } ?? .free
    }

    /// The value bcd.cfg keeps: nil (the key is removed) for `.free`.
    var setting: String? { self == .free ? nil : rawValue }

    /// Whether the interface is held in landscape.
    var locked: Bool { self != .free }

    /// The Settings row's words.
    var label: String {
        switch self {
        case .free: return "Follow the device"
        case .landscape: return "Landscape"
        case .landscapeCameraLeft: return "Landscape, camera on the left"
        case .landscapeCameraRight: return "Landscape, camera on the right"
        }
    }

    /// The choices a person is offered, in order. (`allCases` is the same, in the order of the declaration.)
    static var offered: [BCDOrientationChoice] { [.free, .landscape, .landscapeCameraLeft, .landscapeCameraRight] }
}

/// Which choice a session takes.
enum BCDOrientationRules {
    /// The game's own choice when it has one ("" or nothing: it has none, or one this build does not know),
    /// else the one in Settings (`global`, bcd.cfg's value).
    static func effective(global: String?, game: String?) -> BCDOrientationChoice {
        if let game, !game.isEmpty, let own = BCDOrientationChoice(rawValue: game) { return own }
        return BCDOrientationChoice(setting: global)
    }

    /// Night round 2026-11-11 (A5 S15; Q4XKJ6 and VW8QG1: "when the app is exited and then opened a second time to enter the game, if landscape mode is
    /// locked, only half the screen shows"): whether an interface orientation is one the lock allows. `mask` is a UIInterfaceOrientationMask's raw
    /// value, `interfaceRaw` a UIInterfaceOrientation's (portrait 1, upside down 2, right 3, left 4; the mask has the bit of that number).
    static func allows(mask: UInt, interfaceRaw: Int) -> Bool {
        interfaceRaw > 0 && interfaceRaw < 8 && mask & (1 << UInt(interfaceRaw)) != 0
    }

    /// The `[orientation] check:` line (log only): what the lock is, what the scene is, and whether the lock turns it again.
    static func checkLine(lock: String, reason: String, sceneW: Double, sceneH: Double, interfaceRaw: Int, fits: Bool) -> String {
        String(format: "[orientation] check: lock=%@ reason=%@ scene=%.0fx%.0f interface=%d %@", lock, reason, sceneW, sceneH, interfaceRaw,
               fits ? "matches" : "turns again")
    }

    /// The game page's choices: "" follows Settings (and says what that is now), the others are the game's own.
    static func gameChoices(global: String?) -> [(value: String, label: String)] {
        let now = BCDOrientationChoice(setting: global)
        let said = now.label.prefix(1).lowercased() + String(now.label.dropFirst())
        return [("", "As in Settings: " + said)] + BCDOrientationChoice.offered.map { ($0.rawValue, $0.label) }
    }
}

/// The `[device] window:` line of a launch (2026-10-24, log only): the window the session will run in. An iPad app runs in a
/// full-screen scene or in a window (Windowed Apps, Stage Manager, Split View, an external display); iPadOS lets a window be
/// resized only when it treats the app as resizable, and the facts below are what a log needs to say why BCD's window can or
/// cannot be resized (players ask for it, for a 16:9 monitor): the scene against the screen, whether iPadOS lets the scene
/// state size limits, and the orientations Info.plist and the app delegate declare (BCD declares three of the four).
enum BCDWindowReport {
    struct Facts: Equatable {
        /// The scene's size and its screen's, in points; zero when there is no scene yet.
        var scene: (w: Double, h: Double)
        var screen: (w: Double, h: Double)
        /// UIWindowScene.isFullScreen and whether sizeRestrictions is there; nil when there is no scene.
        var fullScreen: Bool?
        var restrictions: Bool?
        var scenes: Int
        var screens: Int
        var idiom: String
        /// How many orientations Info.plist declares for this idiom, and whether it sets UIRequiresFullScreen.
        var plistOrientations: Int
        var requiresFullScreen: Bool
        /// How many orientations the app delegate answers now.
        var delegateOrientations: Int

        static func == (a: Facts, b: Facts) -> Bool {
            a.scene == b.scene && a.screen == b.screen && a.fullScreen == b.fullScreen && a.restrictions == b.restrictions
                && a.scenes == b.scenes && a.screens == b.screens && a.idiom == b.idiom && a.plistOrientations == b.plistOrientations
                && a.requiresFullScreen == b.requiresFullScreen && a.delegateOrientations == b.delegateOrientations
        }
    }

    static func line(_ f: Facts) -> String {
        func flag(_ value: Bool?) -> String { value.map { $0 ? "1" : "0" } ?? "unknown" }
        let fills = (abs(f.scene.w - f.screen.w) < 1 && abs(f.scene.h - f.screen.h) < 1)
            || (abs(f.scene.w - f.screen.h) < 1 && abs(f.scene.h - f.screen.w) < 1)
        let shape = f.fullScreen == nil ? "no scene" : String(format: "scene=%.0fx%.0f pt screen=%.0fx%.0f pt (%@)", f.scene.w, f.scene.h,
                                                              f.screen.w, f.screen.h, fills ? "full size" : "a window")
        return "[device] window: \(shape) full-screen=\(flag(f.fullScreen)) size-restrictions=\(flag(f.restrictions)) scenes=\(f.scenes)"
            + " screens=\(f.screens) idiom=\(f.idiom) plist-orientations=\(f.plistOrientations) requires-full-screen=\(f.requiresFullScreen ? 1 : 0)"
            + " delegate-orientations=\(f.delegateOrientations)"
    }
}

#if canImport(UIKit)

/// The orientations Info.plist declares for the app (upright and both landscapes): what the windows may take
/// whenever no lock is on, so an app without a lock behaves as it did before this existed.
let bcdDeclaredOrientations: UIInterfaceOrientationMask = [.portrait, .landscapeLeft, .landscapeRight]

extension BCDOrientationChoice {
    /// The orientations the interface may take; nil when it is free. UIKit names an interface orientation by the
    /// side the device's home edge is on: with the device turned so that its top points left (the front camera
    /// on the left), the interface is `.landscapeRight`.
    var mask: UIInterfaceOrientationMask? {
        switch self {
        case .free: return nil
        case .landscape: return .landscape
        case .landscapeCameraLeft: return .landscapeRight
        case .landscapeCameraRight: return .landscapeLeft
        }
    }
}

/// The app delegate's one answer: which orientations a window may take (SwiftUI's own app delegate stays in
/// charge of everything else; MadeiraApp and the preview app adopt this one with UIApplicationDelegateAdaptor).
@MainActor
final class BCDOrientationDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask {
        BCDOrientationLock.shared.supported
    }
}

extension UIDeviceOrientation {
    /// The interface orientation that matches the way the device is held, among the three the app declares; nil
    /// when the device lies flat, is upside down or has not said. (UIKit names the two landscapes the other way
    /// round for a device and for an interface.)
    var interfaceMask: UIInterfaceOrientationMask? {
        switch self {
        case .portrait: return .portrait
        case .landscapeLeft: return .landscapeRight
        case .landscapeRight: return .landscapeLeft
        default: return nil
        }
    }
}

/// The lock on the interface's orientation. Nothing here touches UIKit until a choice other than `.free` is
/// applied, and `.free` applied again (or applied while it is on already) does nothing.
@MainActor
final class BCDOrientationLock {
    static let shared = BCDOrientationLock()

    private(set) var choice = BCDOrientationChoice.free
    /// What the app delegate answers now.
    var supported: UIInterfaceOrientationMask { choice.mask ?? bcdDeclaredOrientations }
    /// Called with a line for the log when a lock turns on or off or UIKit refuses a turn.
    var onLog: ((String) -> Void)?

    /// Puts `new` in force: the windows are asked again which orientations they may take, and while locked the
    /// scenes are asked to turn to the locked ones now. When a lock ends, the interface turns to the way the device
    /// is held now (UIKit leaves it where it is until the device moves).
    func apply(_ new: BCDOrientationChoice) {
        guard new != choice else {
            // Night round 2026-11-11 (A5 S15): the lock is in force already (the app was left and opened again with a session, or a session did not
            // end cleanly): the scene may have come back in the orientation iOS chose, so look again instead of trusting the lock.
            if new.locked { reassert(reason: "start") }
            return
        }
        let old = choice
        choice = new
        let device = UIDevice.current
        if new.locked && !old.locked { device.beginGeneratingDeviceOrientationNotifications() }
        if new.locked { watchActivation() } else { unwatchActivation() }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        for scene in scenes {
            for window in scene.windows { window.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations() }
            if let mask = new.mask ?? (old.locked ? device.orientation.interfaceMask : nil) {
                scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { [weak self] error in
                    self?.onLog?("[orientation] the screen could not turn to \(mask.rawValue): \(error.localizedDescription)")
                }
            }
        }
        if !new.locked && old.locked { device.endGeneratingDeviceOrientationNotifications() }
        onLog?("[orientation] \(old.rawValue) -> \(new.rawValue)")
    }

    /// Night round 2026-11-11 (A5 S15): while a lock is on, the app coming back to the front looks at the scenes once more.
    private var activeObserver: NSObjectProtocol?

    private func watchActivation() {
        guard activeObserver == nil else { return }
        activeObserver = NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.reassert(reason: "activate") }
        }
    }

    private func unwatchActivation() {
        if let observer = activeObserver { NotificationCenter.default.removeObserver(observer) }
        activeObserver = nil
    }

    /// One look at every scene while a lock is on: one `[orientation] check:` line each, and a scene whose interface orientation the lock does not
    /// allow is asked again to turn (the windows are asked which orientations they may take, then the geometry update). A scene that already
    /// matches is left alone, so a lock that works stays as it is. Nothing happens without a lock.
    func reassert(reason: String) {
        guard choice.locked, let mask = choice.mask else { return }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        for scene in scenes {
            let raw = scene.interfaceOrientation.rawValue
            let fits = BCDOrientationRules.allows(mask: mask.rawValue, interfaceRaw: raw)
            let size = scene.coordinateSpace.bounds.size
            onLog?(BCDOrientationRules.checkLine(lock: choice.rawValue, reason: reason, sceneW: Double(size.width), sceneH: Double(size.height),
                                                 interfaceRaw: raw, fits: fits))
            guard !fits else { continue }
            for window in scene.windows { window.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations() }
            scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { [weak self] error in
                self?.onLog?("[orientation] the screen could not turn to \(mask.rawValue): \(error.localizedDescription)")
            }
        }
    }
}

extension BCDWindowReport {
    /// The line for now. Main thread (it reads the scenes). Reads, logs nothing itself and changes nothing.
    @MainActor static func current() -> String {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        let info = Bundle.main.infoDictionary ?? [:]
        let idiom = UIDevice.current.userInterfaceIdiom == .pad ? "pad" : "phone"
        let declared = (info["UISupportedInterfaceOrientations~ipad"] as? [String]).flatMap { idiom == "pad" ? $0 : nil }
            ?? (info["UISupportedInterfaceOrientations"] as? [String]) ?? []
        let screens = Set(scenes.map { ObjectIdentifier($0.screen) }).count
        let size = scene?.coordinateSpace.bounds.size ?? .zero, full = scene?.screen.bounds.size ?? .zero
        return line(Facts(scene: (Double(size.width), Double(size.height)), screen: (Double(full.width), Double(full.height)),
                          fullScreen: scene?.isFullScreen, restrictions: scene.map { $0.sizeRestrictions != nil },
                          scenes: scenes.count, screens: screens, idiom: idiom, plistOrientations: declared.count,
                          requiresFullScreen: (info["UIRequiresFullScreen"] as? Bool) == true,
                          delegateOrientations: BCDOrientationLock.shared.supported.rawValue.nonzeroBitCount))
    }
}

#endif
