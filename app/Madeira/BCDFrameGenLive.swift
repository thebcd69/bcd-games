// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import os

// Frame generation while the game runs (winemetal's madeira_fgl_* functions, tools/patch-winemetal-framegen-live.py). Only a session that
// started with env.MADEIRA_PRESENT_FX = 1 is switched here; in any other session `live` is false and nothing in this file runs, so the
// in-game menu's row writes the game's line for the next start as it always did. Engine-bound: the preview build leaves this file out.

@MainActor enum BCDFrameGenLive {
    /// The GPU guard's own line in the game's file: "0" turns it off, a number from 50 to 100 is its limit, no line is the default (95).
    static let guardKey = "env.MADEIRA_FRAMEGEN_GPU_GUARD"

    /// This session can switch frame generation while the game runs.
    nonisolated static var live: Bool {
        guard #available(iOS 26.0, *), let v = getenv("MADEIRA_PRESENT_FX") else { return false }
        return String(cString: v) == "1"
    }

    /// Frame generation is in force in the engine now.
    nonisolated static var on: Bool { live && madeira_fgl_get() != 0 }

    /// The screen is held at its top rate while live frame generation is on (two pictures per game frame need it). ProMotionIntent asks.
    nonisolated static var holdsPanel: Bool { on }

    static var status: BCDFrameGenStatus {
        var w: Int32 = 0, h: Int32 = 0, game = 0.0, generated = 0.0, busy = -1.0
        let raw = madeira_fgl_status(&w, &h, &game, &generated, &busy)
        return BCDFrameGenStatus(raw: raw, width: w, height: h, gameFPS: game, generatedFPS: generated, gpuBusy: busy)
    }

    private(set) static var measuring = false

    /// The guard as this session has it: the menu's switch if it was used, else the game's line, else on.
    static func guardOn(_ profile: GameProfile) -> Bool {
        if let set = guardSet { return set }
        return (profile.get(guardKey) ?? MadeiraConfig.get(guardKey)) != "0"
    }
    private static var guardSet: Bool?

    /// The menu's choice was written to the game's file by the caller; here it takes effect at once.
    static func switched(on: Bool) {
        guard live else { return }
        let now = madeira_fgl_set(on ? 1 : 0) != 0
        if !on, measuring { measuring = false; madeira_fgl_set_measure(0) }
        ProMotionIntent.apply(mode: madeira_get_vsync_locked())   // hold or release the screen's top rate
        LogStore.shared.log("[feel] frame generation \(on ? "on" : "off") now, while the game runs" + (now == on ? "" : " (the engine answers \(now ? "on" : "off"))"))
    }

    static func setGuard(_ on: Bool, profile: GameProfile) {
        guard live else { return }
        guardSet = on
        let line = profile.get(guardKey)
        let limit = line.flatMap { Int32($0) }.flatMap { $0 >= 50 ? $0 : nil }   // a limit the game's file names stays in force
        if !on { profile.set(guardKey, "0") } else if line == "0" { profile.set(guardKey, nil) }
        madeira_fgl_set_guard(on ? (limit ?? 1) : 0)
        LogStore.shared.log("[feel] frame generation pauses when the GPU is full: \(on ? "on" : "off")")
    }

    static func setMeasuring(_ on: Bool) {
        guard live else { return }
        measuring = on
        madeira_fgl_set_measure(on ? 1 : 0)
    }

    // MARK: The status line follows the engine while the menu shows it

    private static var refresh: (() -> Void)?
    private static var built = Date.distantPast
    private static var timer: Timer?

    /// The menu's way of drawing itself again (BCDMenuMore.pageShown hands it over whenever the menu or one of its pages appears).
    static func menuShown(refresh: @escaping () -> Void) { self.refresh = refresh }

    /// The section was just built: keep it fresh once a second for as long as the menu keeps building it.
    static func sectionBuilt() {
        built = Date()
        guard timer == nil, refresh != nil else { return }
        let t = Timer(timeInterval: 1, repeats: true) { _ in
            MainActor.assumeIsolated {
                if Date().timeIntervalSince(built) > 3 { stopWatching() } else { refresh?() }
            }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    static func stopWatching() {
        timer?.invalidate()
        timer = nil
    }

    static func sessionEnded() {
        stopWatching()
        refresh = nil
        guardSet = nil
        measuring = false
        madeira_fgl_session_reset()   // madeira-bcd: the engine's readings of the keys and the menu's switches go with the session
    }
}
