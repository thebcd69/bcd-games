// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Apple frame interpolation in a running game, and its device test (winemetal's madeira_vti_* functions,
// tools/patch-winemetal-vt-interp.py). Only a session of a game whose own line `vt-interpolation` asks for it gets the engine's
// variable; in any other session `live` is false and nothing here reaches the engine. Engine-bound: the preview build leaves this file out.

/// The screen is held at its top rate while this session adds frames (read off the main thread by ProMotionIntent).
private var bcdVTInterpHolds = false

@MainActor enum BCDVTInterpLive {
    /// This session was started with the game's line (launch exported the engine's variable).
    nonisolated static var live: Bool { getenv(BCDVTInterp.variable) != nil }

    /// The in-game menu's switch in this session.
    private(set) static var on = true

    nonisolated static var holdsPanel: Bool { bcdVTInterpHolds }

    /// The session's start (ContentView, after Lossless Scaling's): exports the engine's variable for a game whose own line asks for
    /// it, and returns a line for the log; nil, and nothing exported, for every other game.
    nonisolated static func launch() -> String? {
        unsetenv(BCDVTInterp.variable)
        var ios26 = false
        if #available(iOS 26.0, *) { ios26 = true }
        let presentFX = (MadeiraConfig.gameValue(BCDVTInterp.presentKey) ?? MadeiraConfig.get(BCDVTInterp.presentKey)) == "1"
        let start = BCDVTInterp.start(line: MadeiraConfig.gameValue(BCDVTInterp.key), ios26: ios26,
                                      lossless: getenv(BCDVTInterp.losslessVariable) != nil, presentFX: presentFX)
        if case .on(let frames) = start {
            setenv(BCDVTInterp.variable, String(frames), 1)
            _ = madeira_fgl_set(0)   // one frame generator at a time: MetalFX's stays off for this session
            DispatchQueue.main.async { MainActor.assumeIsolated { sessionStarted() } }
        }
        return start.log
    }

    private static func sessionStarted() {
        on = true
        bcdVTInterpHolds = true
        ProMotionIntent.apply(mode: madeira_get_vsync_locked())   // the screen's top rate while frames are added
    }

    static var status: BCDVTInterp.Status {
        var w: Int32 = 0, h: Int32 = 0, game = 0.0, generated = 0.0, pair = 0.0
        let raw = madeira_vti_status(&w, &h, &game, &generated, &pair)
        var why = ""
        if raw == BCDVTInterp.Status.State.impossible.rawValue {
            var text = [CChar](repeating: 0, count: 240)
            if madeira_vti_why(&text, Int32(text.count)) > 0 { why = String(cString: text) }
        }
        return BCDVTInterp.Status(raw: raw, width: w, height: h, gameFPS: game, generatedFPS: generated, pairMs: pair, why: why)
    }

    /// The menu's choice was written to the game's file by the caller; in a session that started with it, off and on take effect at once.
    static func switched(on new: Bool) {
        guard live else { return }
        on = madeira_vti_set(new ? 1 : 0) != 0
        bcdVTInterpHolds = on
        ProMotionIntent.apply(mode: madeira_get_vsync_locked())
        LogStore.shared.log("[feel] Apple frame interpolation \(new ? "on" : "off") now, while the game runs" + (on == new ? "" : " (the engine answers \(on ? "on" : "off"))"))
    }

    static func sessionEnded() {
        madeira_vti_session_ended()   // its reading of MADEIRA_VT_INTERP goes too, also for a session that never asked for it
        bcdVTInterpHolds = false
        on = true
    }

    // MARK: The device test (Settings > Graphics > Experimental)

    /// Runs the engine's test on a background thread and hands its lines over on the main thread. It loads a model for every line of
    /// its ladder, so it takes a while.
    nonisolated static func probe(done: @escaping @MainActor (String) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            var text = [CChar](repeating: 0, count: 16384)
            _ = madeira_vti_probe(&text, Int32(text.count))
            let lines = String(cString: text)
            DispatchQueue.main.async { MainActor.assumeIsolated { done(lines) } }
        }
    }
}
