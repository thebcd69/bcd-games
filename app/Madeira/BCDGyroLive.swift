// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import CoreMotion
import Foundation
import SwiftUI
import UIKit

// Gyro aim on the engine (round 603; BCDGyroAim.swift has the settings and the arithmetic).
//
// Per game, off until its player turns it on, from the game's page (Options › Gyro aim) or the in-game
// menu (Controls › Gyro aim). The choices are kept by the game's Windows path in UserDefaults, not in
// the game's config file: they are how the player holds a phone, not a compatibility setting, so they
// stay out of the recommended lists and out of the fingerprint auto-tune compares.
//
// Cost. Off, nothing runs: no sensor is created, the stick hook in GamepadInput is one static flag, and
// the session view's hook reads six UserDefaults strings when the session or its menu changes. On, CoreMotion's
// device motion runs at 100 Hz on a queue of its own while the game runs, is on screen and no menu covers it,
// and stops the moment any of that stops. Mouse aim posts whole relative counts through HardwareInput.postRelative
// (the way the controller's right-stick mouse does); stick aim hands the right stick to GamepadInput, which merges
// it into player 1 as the touch stick does.
// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).

/// A game's gyro choices.
enum BCDGyroStore {
    private static let key = "bcd.gyro"

    /// The lines of a game's choices ("gyro-aim", ...) as stored; empty when it has none.
    static func lines(_ windowsPath: String) -> [String: String] {
        ((UserDefaults.standard.dictionary(forKey: key) as? [String: [String: String]]) ?? [:])[windowsPath] ?? [:]
    }

    static func settings(_ windowsPath: String) -> BCDGyroSettings {
        let stored = lines(windowsPath)
        return BCDGyroSettings.parse { stored[$0] }
    }

    /// One line of a game's choices; an empty value removes it, and a game with no line left is forgotten.
    static func set(_ windowsPath: String, _ line: String, _ value: String) {
        guard BCDGyroSettings.keys.contains(line) else { return }
        var all = (UserDefaults.standard.dictionary(forKey: key) as? [String: [String: String]]) ?? [:]
        var mine = all[windowsPath] ?? [:]
        mine[line] = value.isEmpty ? nil : value
        all[windowsPath] = mine.isEmpty ? nil : mine
        UserDefaults.standard.set(all, forKey: key)
    }
}

final class BCDGyroLive: @unchecked Sendable {
    static let shared = BCDGyroLive()

    /// The gyro drives the right stick of player 1 right now (GamepadInput.sample reads it on its queue).
    nonisolated(unsafe) static var stickActive = false
    /// The gyro wants to see player 1's controller: it drives the stick, or waits for a button.
    nonisolated(unsafe) static var padNeeded = false

    private let lock = NSLock()
    private let queue: OperationQueue = {
        let q = OperationQueue()
        q.name = "bcd.gyro"
        q.maxConcurrentOperationCount = 1
        q.qualityOfService = .userInteractive
        return q
    }()
    private var motion: CMMotionManager?

    // Under `lock`.
    private var settings = BCDGyroSettings()
    private var screen = BCDGyroScreen.landscapeRight
    private var holdOpen = true
    private var carry = BCDGyroMath.Carry()
    private var lastStamp: TimeInterval = 0
    private var stick: (rx: Int16, ry: Int16) = (0, 0)
    private var lastMoveNote: TimeInterval = 0
    private var posted = 0

    // Main thread.
    private var observers: [NSObjectProtocol] = []
    private var runningFor = ""
    /// Whether the app is in front, kept from its own notifications while they are watched (the state read inside
    /// willResignActive still says active).
    private var appActive = true

    // MARK: When it runs (main thread)

    /// The settings of the running game and whether the game is being played right now; starts or stops the sensor.
    /// Called when the session, its menu or the app's state changes.
    @MainActor func refresh() {
        let library = LibraryModel.shared
        let entry = library.activeEntry
        let wanted = entry.map { BCDGyroStore.settings($0.windowsPath) } ?? BCDGyroSettings()
        if observers.isEmpty { appActive = UIApplication.shared.applicationState == .active }
        let playing = library.current != nil && !library.desktopHome && !library.launching && !library.menu && appActive
        let on = wanted.isOn && playing && entry != nil
        let wasStick = Self.stickActive
        lock.lock()
        let changed = settings != wanted
        settings = wanted
        if !on || changed { stick = (0, 0); carry.reset() }
        // A gate on a button stays shut until the controller says the button is held.
        if changed || !on { holdOpen = wanted.hold.isEmpty }
        lock.unlock()
        Self.stickActive = on && wanted.target == .stick
        Self.padNeeded = on && (wanted.target == .stick || !wanted.hold.isEmpty)
        // While a game that turned the gyro on is running, the app's own state is watched, so the sensor comes back
        // when the app does; otherwise nothing is watched.
        if wanted.isOn && entry != nil && library.current != nil {
            if observers.isEmpty { observe() }
        } else {
            unobserve()
        }
        if on {
            startIfNeeded(wanted, title: entry?.title ?? "")
        } else {
            stopIfNeeded()
        }
        // A stick the gyro was holding, or now holds, is published again.
        if wasStick || Self.stickActive { GamepadInput.shared.gyroChanged() }
    }

    @MainActor private func startIfNeeded(_ wanted: BCDGyroSettings, title: String) {
        updateScreen()
        guard motion == nil else { return }
        let manager = CMMotionManager()
        guard manager.isDeviceMotionAvailable else {
            LogStore.shared.log("[gyro] \(title): this device has no motion sensor")
            return
        }
        manager.deviceMotionUpdateInterval = 1.0 / 100.0
        motion = manager
        lock.lock(); lastStamp = 0; carry.reset(); posted = 0; lock.unlock()
        manager.startDeviceMotionUpdates(to: queue) { [weak self] data, _ in
            if let data { self?.handle(data) }
        }
        runningFor = title
        LogStore.shared.log("[gyro] \(title): \(wanted.target.rawValue) sensitivity \(wanted.sensitivity) dead zone \(wanted.deadzone)°/s"
                            + (wanted.hold.isEmpty ? "" : " while \(wanted.hold.uppercased())") + " (CoreMotion, 100 Hz)")
    }

    @MainActor private func stopIfNeeded() {
        if let manager = motion {
            manager.stopDeviceMotionUpdates()
            motion = nil
            lock.lock(); let count = posted; stick = (0, 0); carry.reset(); lock.unlock()
            LogStore.shared.log("[gyro] \(runningFor): stopped" + (count > 0 ? " after \(count) mouse moves" : ""))
            runningFor = ""
        }
    }

    @MainActor private func unobserve() {
        for token in observers { NotificationCenter.default.removeObserver(token) }
        observers.removeAll()
    }

    /// The way the phone is turned changes while a game runs; the app leaving the foreground stops the sensor.
    @MainActor private func observe() {
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: UIDevice.orientationDidChangeNotification, object: nil, queue: .main) { [weak self] _ in
            // The interface turns a moment after the device does.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { MainActor.assumeIsolated { self?.updateScreen() } }
        })
        for (name, active) in [(UIApplication.willResignActiveNotification, false), (UIApplication.didBecomeActiveNotification, true)] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated {
                    self?.appActive = active
                    self?.refresh()
                }
            })
        }
    }

    @MainActor private func updateScreen() {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) ?? scenes.first else { return }
        let value: BCDGyroScreen
        switch scene.interfaceOrientation {
        case .portrait: value = .portrait
        case .portraitUpsideDown: value = .portraitUpsideDown
        case .landscapeLeft: value = .landscapeLeft
        case .landscapeRight: value = .landscapeRight
        default: return
        }
        lock.lock(); screen = value; lock.unlock()
    }

    // MARK: The sensor (its queue)

    private func handle(_ data: CMDeviceMotion) {
        lock.lock()
        let s = settings, screen = screen, open = holdOpen
        let dt = lastStamp == 0 ? 0 : data.timestamp - lastStamp
        lastStamp = data.timestamp
        lock.unlock()
        guard dt > 0 else { return }
        let rates = BCDGyroMath.lookRates(
            rotation: BCDGyroVec(x: data.rotationRate.x, y: data.rotationRate.y, z: data.rotationRate.z),
            gravity: BCDGyroVec(x: data.gravity.x, y: data.gravity.y, z: data.gravity.z), screen: screen, turn: s.turn)
        let shaped = BCDGyroMath.shaped(rates, s)
        switch s.target {
        case .off:
            return
        case .mouse:
            // The mouse moves only while the game view has the pointer, as the controller's right-stick mouse does.
            guard open, HardwareInput.shared.baseFocused else {
                lock.lock(); carry.reset(); lock.unlock()
                return
            }
            lock.lock()
            let counts = BCDGyroMath.mouse(shaped, dt: dt, s, carry: &carry)
            let due = data.timestamp - lastMoveNote > 0.1
            if due { lastMoveNote = data.timestamp }
            if counts.dx != 0 || counts.dy != 0 { posted += 1 }
            lock.unlock()
            guard counts.dx != 0 || counts.dy != 0 else { return }
            HardwareInput.shared.postRelative(counts.dx, counts.dy)
            if due { DispatchQueue.main.async { HardwareInput.shared.stickMoved() } }
        case .stick:
            let out = open ? BCDGyroMath.stick(shaped, s) : (rx: Int16(0), ry: Int16(0))
            lock.lock()
            let changed = stick.rx != out.rx || stick.ry != out.ry
            stick = out
            lock.unlock()
            if changed { GamepadInput.shared.gyroChanged() }
        }
    }

    // MARK: The controller (GamepadInput's queue)

    /// Player 1's controller as the game is about to see it, merged touch and physical: notes whether the button the gyro waits
    /// for is held, and puts the gyro on the right stick when it drives it.
    func padSample(_ state: inout winios_gamepad) {
        lock.lock()
        let s = settings
        guard s.isOn else { lock.unlock(); return }
        let open = BCDGyroHold.isOpen(s.hold, buttons: state.buttons, lt: state.left_trigger, rt: state.right_trigger)
        holdOpen = open
        let gyro = open && s.target == .stick ? stick : (rx: 0, ry: 0)
        lock.unlock()
        guard s.target == .stick, gyro.rx != 0 || gyro.ry != 0 else { return }
        let merged = BCDGyroMath.merged(physical: (state.rx, state.ry), gyro: gyro)
        state.rx = merged.rx
        state.ry = merged.ry
    }
}

/// The session's view tells the gyro when to look: the session, its menu and the app's state decide.
struct BCDGyroSessionHook: ViewModifier {
    /// One publisher for the whole app run, so the view never subscribes again; @Published announces before it stores,
    /// so the change is read a moment later.
    @MainActor private static let changes: AnyPublisher<Void, Never> = {
        let library = LibraryModel.shared
        return Publishers.Merge4(library.$current.map { _ in () }, library.$menu.map { _ in () }, library.$launching.map { _ in () }, library.$desktopHome.map { _ in () })
            .receive(on: DispatchQueue.main)
            .eraseToAnyPublisher()
    }()

    @MainActor func body(content: Content) -> some View {
        content
            .onAppear { BCDGyroLive.shared.refresh() }
            .onDisappear { BCDGyroLive.shared.refresh() }
            .onReceive(Self.changes) { BCDGyroLive.shared.refresh() }
    }
}

// MARK: - The in-game menu's section (Controls › Gyro aim)

@MainActor enum BCDGyroMenu {
    static let prefix = "gyro."

    static func handles(_ row: String) -> Bool { row.hasPrefix(prefix) }

    static func section(for entry: LibraryEntry?) -> BCDMenuSection? {
        guard let entry else { return nil }
        let s = BCDGyroStore.settings(entry.windowsPath)
        var rows = [BCDMenuRow(id: "gyro.aim", title: "Turn the phone to aim",
                               detail: "Off, the mouse, or the controller's right stick. It works while the game runs and this menu is closed.",
                               kind: .choice([BCDChoice(id: "", title: "Off"), BCDChoice(id: "mouse", title: "Mouse"), BCDChoice(id: "stick", title: "Right stick")],
                                             selected: s.target.rawValue))]
        if s.isOn {
            rows.append(BCDMenuRow(id: "gyro.sens", title: "Sensitivity",
                                   kind: .choice(BCDGyroSettings.sensitivityChoices.map { BCDChoice(id: number($0), title: number($0) + "×") },
                                                 selected: number(s.sensitivity))))
            rows.append(BCDMenuRow(id: "gyro.hold", title: "Only while this is held",
                                   detail: "A button of the controller (on screen or paired): the gyro aims only while you hold it.",
                                   kind: .choice(BCDGyroHold.choices.map { BCDChoice(id: $0.0, title: $0.1) }, selected: s.hold)))
            rows.append(BCDMenuRow(id: "gyro.invert", title: "Invert",
                                   kind: .choice([BCDChoice(id: "", title: "None"), BCDChoice(id: "x", title: "Left-right"),
                                                  BCDChoice(id: "y", title: "Up-down"), BCDChoice(id: "xy", title: "Both")], selected: s.invertWord)))
            rows.append(BCDMenuRow(id: "gyro.deadzone", title: "Still below",
                                   detail: "Turning slower than this, in degrees a second, is taken as the phone being still.",
                                   kind: .choice(BCDGyroSettings.deadzoneChoices.map { BCDChoice(id: number($0), title: number($0) + "°/s") },
                                                 selected: number(s.deadzone))))
            rows.append(BCDMenuRow(id: "gyro.turn", title: "Turn around",
                                   detail: "The world's vertical follows gravity, so a phone tilted back still turns the view as your arms turn. The screen's vertical is the phone's own.",
                                   kind: .choice([BCDChoice(id: "", title: "The world"), BCDChoice(id: "screen", title: "The screen")], selected: s.turn.rawValue)))
            if s.target == .stick {
                rows.append(BCDMenuRow(id: "gyro.note", title: "The right stick reaches the game as player 1's controller. It works with the on-screen controller or a paired one.",
                                       kind: .note(.soft)))
            }
        }
        return BCDMenuSection(id: "gyro", title: "Gyro aim", rows: rows)
    }

    /// A choice of the section: kept for the game, and the gyro looks again.
    static func choose(_ row: String, _ value: String, entry: LibraryEntry?) {
        guard let entry else { return }
        let line: String
        switch row {
        case "gyro.aim": line = "gyro-aim"
        case "gyro.sens": line = "gyro-sens"
        case "gyro.hold": line = "gyro-hold"
        case "gyro.invert": line = "gyro-invert"
        case "gyro.deadzone": line = "gyro-deadzone"
        case "gyro.turn": line = "gyro-turn"
        default: return
        }
        BCDGyroStore.set(entry.windowsPath, line, value)
        BCDGyroLive.shared.refresh()
    }

    /// 1.5 -> "1.5", 2 -> "2", 0.25 -> "0.25".
    static func number(_ value: Double) -> String { String(format: "%g", value) }
}
