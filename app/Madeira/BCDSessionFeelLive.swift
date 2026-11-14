// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import UIKit

// The in-game menu's "feel" settings on the engine (round 604; BCDSessionFeel.swift has the values and rules). A session starts with the values
// its game saved for itself (B15), or with none, which is how every session ran before: every field that is nil leaves the engine exactly as
// it was. A change in the menu applies at once and lasts for the session; "Save these settings for this game" keeps it for the game.
//
// What each value reaches:
//   touch tick, colour, auto-hide      BCDTouchFeel / BCDTouchFeelModel (the touch drawing and the controls' window)
//   touches that miss the controls      TouchMouseGate.override (ContentView.swift)
//   phone vibration                     BCDPhoneRumble.m, called from PadOutput.m's pass
//   picture filter                      the presented layer's magnification filter
//   pointer mode and speed, eco         InputSettings and madeira_set_eco, put back when the session ends (they are the device's own settings,
//                                       which a game that saved its own must not leave behind)
//   the screen's turn                   BCDOrientationLock; saved into the game's own orientation setting
//   the controller's chord              BCDChordSetting, one choice for the device (read by LibraryController.sample)
// Engine-bound: the preview build leaves this file out (tools/ui-preview.py, ENGINE_BOUND).

@MainActor final class BCDFeelLive {
    static let shared = BCDFeelLive()

    /// The values this session runs with. nil fields are "as always".
    private(set) var current = BCDSessionFeel()
    /// What the game has saved (nil fields: nothing).
    private(set) var saved = BCDSessionFeel()
    private var entry: LibraryEntry?
    private var all: [String: BCDSessionFeel] = [:]
    private var watchers = Set<AnyCancellable>()
    private var filterSet = false

    // What the session found, to put back or to compare.
    private var pointerAtStart: (mode: InputSettings.PointerMode, speed: Double)?
    private var pointerBefore: (mode: InputSettings.PointerMode, sensAbs: Double, sensRel: Double)?
    private var ecoBefore: Bool?
    private var orientationAtStart = BCDOrientationChoice.free

    /// The calls LibraryModel makes: from the main thread; anything else is put on it.
    nonisolated static func sessionBegan(_ entry: LibraryEntry) { onMain { shared.takeSession(entry) } }
    nonisolated static func sessionEnded() { onMain { shared.leaveSession(); BCDMenuMore.sessionEnded() } }

    private nonisolated static func onMain(_ work: @escaping @MainActor () -> Void) {
        if Thread.isMainThread { MainActor.assumeIsolated(work) } else { DispatchQueue.main.async { MainActor.assumeIsolated(work) } }
    }

    // MARK: Device-wide choices

    /// The controller's chord for opening the menu: one choice for the device.
    static var chord: BCDMenuChord {
        get { BCDChordSetting.mode }
        set {
            UserDefaults.standard.set(newValue.rawValue, forKey: BCDChordSetting.key)
            BCDChordSetting.mode = newValue
        }
    }

    // MARK: A session

    func takeSession(_ entry: LibraryEntry) {
        leaveSession()
        BCDPauseLive.clear()
        self.entry = entry
        orientationAtStart = BCDOrientationLive.choice(for: entry)   // what the session takes (the game's own choice, else Settings'); the lock itself is applied a moment later
        loadAll()
        saved = all[BCDSessionFeelFile.key(forWindowsPath: entry.windowsPath)] ?? BCDSessionFeel()
        current = saved
        applySavedDevice()
        applyAll()
        watch()
        if !saved.isEmpty { LogStore.shared.log("[feel] this game's saved settings: " + saved.words.joined(separator: ", ")) }
    }

    func leaveSession() {
        watchers.removeAll()
        BCDTouchFeel.reset()
        BCDTouchFeelModel.shared.reset()
        BCDTouchFeelModel.shared.colorChanged()
        TouchMouseGate.override = nil
        bcd_phone_rumble_set_enabled(0)
        bcd_phone_rumble_stop()
        if filterSet { MetalHostView.shared.metalLayer.magnificationFilter = .linear; filterSet = false }
        BCDMagnifierLive.shared.reset()
        BCDWindowsLive.shared.stop()
        BCDPauseLive.clear()
        if let before = pointerBefore {
            InputSettings.shared.mode = before.mode
            InputSettings.shared.sensAbs = before.sensAbs
            InputSettings.shared.sensRel = before.sensRel
        }
        pointerBefore = nil
        pointerAtStart = nil
        if let eco = ecoBefore { madeira_set_eco(eco ? 1 : 0) }
        ecoBefore = nil
        current = BCDSessionFeel()
        saved = BCDSessionFeel()
        entry = nil
    }

    private func watch() {
        let library = LibraryModel.shared
        library.$menu
            .sink { menu in
                MainActor.assumeIsolated {
                    BCDTouchFeelModel.shared.setSuspended(menu || TouchControlsModel.shared.editing)
                    if menu { bcd_phone_rumble_stop() }
                }
            }
            .store(in: &watchers)
        TouchControlsModel.shared.$editing
            .sink { editing in
                MainActor.assumeIsolated { BCDTouchFeelModel.shared.setSuspended(editing || LibraryModel.shared.menu) }
            }
            .store(in: &watchers)
    }

    /// The saved pointer mode and speed, eco: the device's own settings for the length of the session.
    private func applySavedDevice() {
        let input = InputSettings.shared
        pointerAtStart = (input.mode, input.relative ? input.sensRel : input.sensAbs)
        if let word = saved.pointerMode, let mode = Self.pointer(word) {
            pointerBefore = (input.mode, input.sensAbs, input.sensRel)
            input.mode = mode
            pointerAtStart = (mode, input.relative ? input.sensRel : input.sensAbs)
        }
        if let speed = saved.touchSpeed {
            if pointerBefore == nil { pointerBefore = (input.mode, input.sensAbs, input.sensRel) }
            if input.relative { input.sensRel = speed } else { input.sensAbs = speed }
            pointerAtStart = (input.mode, speed)
        }
        if saved.eco == true {
            ecoBefore = madeira_get_eco() != 0
            madeira_set_eco(1)
        }
    }

    static func pointer(_ word: String) -> InputSettings.PointerMode? {
        switch word {
        case "absolute": return .absolute
        case "touch": return .touch
        case "relative": return .relative
        default: return nil
        }
    }

    static func word(_ mode: InputSettings.PointerMode) -> String {
        switch mode {
        case .absolute: return "absolute"
        case .touch: return "touch"
        case .relative: return "relative"
        }
    }

    // MARK: Applying the values

    /// Puts every value in force. Idempotent, so every change just calls it.
    private func applyAll() {
        BCDTouchFeel.haptics = current.touchHaptics ?? true
        let revision = BCDTouchFeel.tintRevision
        BCDTouchFeel.setTint(BCDControlColor(rawValue: current.controlColor ?? "") ?? .violet)
        if revision != BCDTouchFeel.tintRevision { BCDTouchFeelModel.shared.colorChanged() }
        BCDTouchFeelModel.shared.configure(seconds: current.hideAfter ?? 0)
        // madeira-bcd (Porthole port, item 9): floating sticks; the controls are drawn again only when it changes.
        if BCDTouchFeel.floatingSticks != (current.floatingSticks == true) {
            BCDTouchFeel.floatingSticks = current.floatingSticks == true
            BCDTouchFeelModel.shared.colorChanged()
        }
        TouchMouseGate.override = Self.gateMode(BCDTouchMouse(rawValue: current.touchMouse ?? "") ?? .automatic)
        bcd_phone_rumble_set_enabled(current.phoneVibration == true && bcd_phone_rumble_available() != 0 ? 1 : 0)
        let sharp = current.filter == BCDPictureFilter.sharp.rawValue
        if sharp || filterSet {
            MetalHostView.shared.metalLayer.magnificationFilter = sharp ? .nearest : .linear
            filterSet = sharp
        }
    }

    private static func gateMode(_ choice: BCDTouchMouse) -> TouchMouseGate.Mode? {
        switch choice {
        case .automatic: return nil
        case .never: return .off
        case .always: return .on
        }
    }

    /// One change of the session's values.
    func update(_ change: (inout BCDSessionFeel) -> Void) {
        var next = current
        change(&next)
        current = next.sanitized()
        applyAll()
    }

    // MARK: Saving for the game

    var hasSaved: Bool { !saved.isEmpty || savedOrientation }
    private var savedOrientation: Bool { entry?.orientationLock != nil }

    /// Keeps what the session runs with for this game. The pointer, its speed and eco are kept when the player changed them this session or the
    /// game had them saved already; the screen's turn goes into the game's own orientation setting. Returns the words for the note.
    @discardableResult
    func saveForGame() -> [String] {
        guard let entry else { return [] }
        var next = current
        let input = InputSettings.shared
        let mode = input.mode, speed = input.relative ? input.sensRel : input.sensAbs
        if let start = pointerAtStart {
            if mode != start.mode || saved.pointerMode != nil { next.pointerMode = Self.word(mode) }
            if abs(speed - start.speed) > 0.0001 || saved.touchSpeed != nil { next.touchSpeed = speed }
        }
        let eco = madeira_get_eco() != 0
        next.eco = eco ? true : nil
        next = next.sanitized()
        var words = next.words
        all[BCDSessionFeelFile.key(forWindowsPath: entry.windowsPath)] = next.isEmpty ? nil : next
        persist()
        saved = next
        current = next
        // The screen's turn is the game's own orientation setting.
        let choice = BCDOrientationLock.shared.choice
        if choice != orientationAtStart, var stored = LibraryModel.shared.activeEntry {
            stored.orientationLock = choice.rawValue
            LibraryModel.shared.save(stored)
            self.entry = stored
            orientationAtStart = choice
            words.append("screen turn")
        }
        LogStore.shared.log("[feel] saved for this game: " + (words.isEmpty ? "nothing different from the defaults" : words.joined(separator: ", ")))
        return words
    }

    /// Forgets what the game saved: the session goes back to the defaults.
    func forgetForGame() {
        guard let entry else { return }
        all[BCDSessionFeelFile.key(forWindowsPath: entry.windowsPath)] = nil
        persist()
        saved = BCDSessionFeel()
        current = BCDSessionFeel()
        if let before = pointerBefore {
            InputSettings.shared.mode = before.mode
            InputSettings.shared.sensAbs = before.sensAbs
            InputSettings.shared.sensRel = before.sensRel
            pointerBefore = nil
        }
        if let eco = ecoBefore { madeira_set_eco(eco ? 1 : 0); ecoBefore = nil }
        applyAll()
        LogStore.shared.log("[feel] this game's saved settings were forgotten")
    }

    // MARK: The file

    private static var fileURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent(BCDSessionFeelFile.name)
    }

    private func loadAll() {
        all = BCDSessionFeelFile.decode(Self.fileURL.flatMap { try? Data(contentsOf: $0) })
    }

    private func persist() {
        guard let url = Self.fileURL else { return }
        if all.isEmpty { try? FileManager.default.removeItem(at: url); return }
        guard let data = BCDSessionFeelFile.encode(all) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }
}
