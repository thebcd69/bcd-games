// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import GameController

// A game that never asks for a controller, while it runs (BCDPadAuto.swift has the two rules and the words; the engine counts the
// game's XInput reads, app/Madeira/Winios/WiniosGamepad.c). Only a session of a game whose own line `pad-auto = 1` asks gets any of
// it: without the line `begin` returns at its first test, no timer runs and nothing is read. Engine-bound: the preview build leaves
// this file out.

@MainActor enum BCDPadAutoLive {
    private static var timer: Timer?
    private static var pad = BCDPadAuto()
    private static var hint = BCDLayoutHint()
    private static var path = ""
    /// The controller rule runs this session: the player left Controller mode and the controller's identity at their standard choices.
    private static var padRule = false
    private static var hintRule = false
    /// The keyboard-and-mouse mode in force is this file's doing (never written to the game's settings).
    private static var ours = false

    /// A session's start (ContentView's launch code runs on a worker; the work itself is done on the main thread).
    nonisolated static func launch() {
        DispatchQueue.main.async { MainActor.assumeIsolated { begin() } }
    }

    static func begin() {
        guard timer == nil, BCDPadAuto.on(MadeiraConfig.gameValue(BCDPadAuto.key)), GamepadInput.keyboardMouseAvailable else { return }
        let model = LibraryModel.shared
        guard let entry = model.activeEntry, entry.desktop != true else { return }
        path = entry.windowsPath
        let defaults = UserDefaults.standard
        let now = ProcessInfo.processInfo.systemUptime, reads = winios_gamepad_read_count()
        // The player's own choice comes first: a Controller mode for this game, or another controller identity than XInput.
        padRule = entry.controllerMode == nil && GamepadInput.configuredPadMode().source == "default"
        hintRule = BCDLayoutHint.mayTell(path, told: defaults.stringArray(forKey: BCDLayoutHint.toldKey) ?? [])
        pad = BCDPadAuto()
        pad.start(now: now, reads: reads, preferKeys: padRule && BCDPadAuto.remembered(path, in: defaults.stringArray(forKey: BCDPadAuto.rememberedKey) ?? []))
        hint = BCDLayoutHint()
        hint.start(now: now, reads: reads)
        ours = false
        LogStore.shared.log("[pad-auto] on for this game: the controller rule \(padRule ? "runs" : "stands down (a controller mode was chosen)")"
                            + (pad.usingKeys ? ", starting as keyboard and mouse (the last run never asked for a controller)" : ""))
        if pad.usingKeys { useKeys(say: false) }
        let t = Timer(timeInterval: 1, repeats: true) { _ in MainActor.assumeIsolated { tick() } }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private static func useKeys(say: Bool) {
        let model = LibraryModel.shared
        ours = true
        GamepadInput.shared.setKeyboardMouse(PadBindings.build(controls: TouchControlsModel.shared.controls, binds: model.controllerBinds,
                                                               mouseVertical: model.padMouseVertical))
        if say { self.say(BCDPadAuto.switchedToKeys) }
    }

    private static func tick() {
        let model = LibraryModel.shared
        guard model.current != nil else { return }
        let now = ProcessInfo.processInfo.systemUptime, reads = winios_gamepad_read_count()
        let connected = !GCController.controllers().isEmpty
        if padRule, model.controllerMode != nil {
            // The player chose a mode in the in-game menu: that choice is in force (the library applied it), and the rule is done.
            padRule = false; ours = false
            LogStore.shared.log("[pad-auto] a controller mode was chosen in the menu: the rule stands down")
        }
        if padRule {
            switch pad.update(now: now, reads: reads, padConnected: connected) {
            case .useKeys:
                LogStore.shared.log("[pad-auto] no controller read in \(Int(pad.grace)) s: the controller works as keyboard and mouse")
                useKeys(say: true)
            case .useXInput:
                LogStore.shared.log("[pad-auto] the game reads the controller: it is a controller again")
                ours = false
                model.applyControllerMode()   // the game's own setting (none): XInput
                say(BCDPadAuto.switchedBack)
            case .none:
                // The library hands the sampler the game's own mode again when the binds page changes something (and once at the
                // session's start): while the rule has the controller as keys, it takes it back with the new binds.
                if ours, pad.usingKeys, !GamepadInput.shared.keyboardMouseOn { useKeys(say: false) }
            }
        }
        if hintRule {
            let controls = TouchControlsModel.shared
            let eligible = !connected && controls.visible && controls.layoutID == ControlPresetLayout.xboxID && !model.menu
            if hint.update(now: now, reads: reads, eligible: eligible) {
                hintRule = false
                let defaults = UserDefaults.standard
                defaults.set(BCDLayoutHint.remember(path, told: defaults.stringArray(forKey: BCDLayoutHint.toldKey) ?? []), forKey: BCDLayoutHint.toldKey)
                LogStore.shared.log("[pad-auto] no controller read in \(Int(hint.grace)) s with the on-screen controller: the keyboard layout is suggested")
                say(BCDLayoutHint.text, seconds: 8)
            }
        }
    }

    /// The line over the game for a few seconds (the session line, which the layer already shows).
    private static func say(_ text: String, seconds: Double = 5) {
        let model = LibraryModel.shared
        guard model.current != nil else { return }
        model.sessionMessage = text
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
            if model.sessionMessage == text { model.sessionMessage = "" }
        }
    }

    /// The session ended: whether this run read the controller is kept for the next start, and the mode is the game's own again.
    static func end() {
        guard timer != nil else { return }
        timer?.invalidate()
        timer = nil
        if padRule {
            let defaults = UserDefaults.standard
            defaults.set(BCDPadAuto.remember(path, noReads: !pad.gameReads, in: defaults.stringArray(forKey: BCDPadAuto.rememberedKey) ?? []),
                         forKey: BCDPadAuto.rememberedKey)
        }
        if ours { GamepadInput.shared.setKeyboardMouse(nil) }
        padRule = false; hintRule = false; ours = false
    }
}
