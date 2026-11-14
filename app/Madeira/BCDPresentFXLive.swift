// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import os

// present-fx's engine side in the app (winemetal's madeira_pfx_* functions, tools/patch-winemetal-present-fx.py): what a session's start
// exports for upscaling, and the in-game menu's live switch. Without env.MADEIRA_PRESENT_FX and without the "to the screen" choice
// `launch()` answers nil and the callers do what they always did. Engine-bound: the preview build leaves this file out.

@MainActor enum BCDPresentFXLive {
    /// This session's presenter enlarges the picture (env.MADEIRA_PRESENT_FX = 1 was exported when it started).
    nonisolated static var live: Bool {
        guard let v = getenv("MADEIRA_PRESENT_FX") else { return false }
        return String(cString: v) == "1"
    }

    /// The game's own line, or Settings' line for every game.
    nonisolated private static func setting(_ key: String) -> String? { MadeiraConfig.gameValue(key) ?? MadeiraConfig.get(key) }

    /// A launch whose profile is at hand, before the session's start: whether `launch()` will decide (BCDLaunch.applyExtras then leaves
    /// the old export alone).
    nonisolated static func decidesAtStart(_ profile: GameProfile?) -> Bool {
        let on = (profile?.get(BCDUpscale.presentFXKey) ?? MadeiraConfig.get(BCDUpscale.presentFXKey)) == "1"
        let value = (profile?.get(BCDUpscale.key) ?? MadeiraConfig.get(BCDUpscale.key))?.trimmingCharacters(in: .whitespaces)
        return on || value == BCDUpscale.fit
    }

    /// The session's start (ContentView, where DXMT's options are put together): with present-fx, or for "to the screen", this sets what
    /// the engine reads and returns DXMT's option of the old way (if any) and a line for the log. nil in every other case.
    nonisolated static func launch() -> (option: String?, log: String)? {
        let value = setting(BCDUpscale.key)
        let on = setting(BCDUpscale.presentFXKey) == "1"
        let screen = ResolutionChoices.screen.pixels
        let long = Int(max(screen.width, screen.height)), short = Int(min(screen.width, screen.height))
        let desktop = BCDUpscale.desktopSize(arguments: getenv(BCDUpscale.argumentsVariable).map { String(cString: $0) })
            ?? { let d = ResolutionChoices.defaultSize(); return (width: d.0, height: d.1) }()
        guard let route = BCDUpscale.route(value: value, presentFX: on, desktop: desktop, screenLong: long, screenShort: short) else { return nil }
        madeira_pfx_set_screen(Int32(long), Int32(short))   // "to the screen" needs it: the presenter, or the Direct3D 12 runtime (MadeiraCtl op 30)
        switch route {
        case .presenter(let choice):
            unsetenv("DXMT_METALFX_SPATIAL_SWAPCHAIN")
            setenv("MADEIRA_PFX_UPSCALE", choice, 1)
            return (nil, "present-fx: the presenter enlarges the picture (upscaling \(choice), screen \(long)x\(short)); it can be switched from the in-game menu")
        case .engine(let factor):
            unsetenv("MADEIRA_PFX_UPSCALE")
            setenv("DXMT_METALFX_SPATIAL_SWAPCHAIN", "1", 1)
            return ("d3d11.metalSpatialUpscaleFactor=\(factor)",
                    "MetalFX upscaling: to the screen is \(factor)x for a \(desktop.width)x\(desktop.height) desktop on a \(long)x\(short) screen (Direct3D 11; Direct3D 12 works it out per swap chain)")
        case .off:
            unsetenv("MADEIRA_PFX_UPSCALE")
            unsetenv("DXMT_METALFX_SPATIAL_SWAPCHAIN")
            return (nil, "MetalFX upscaling: to the screen needs no scaler for a \(desktop.width)x\(desktop.height) desktop on a \(long)x\(short) screen")
        }
    }

    static var status: BCDUpscale.Status {
        var gw: Int32 = 0, gh: Int32 = 0, sw: Int32 = 0, sh: Int32 = 0, kind: Int32 = 0
        let mode = madeira_pfx_status(&gw, &gh, &sw, &sh, &kind)
        return BCDUpscale.Status(mode: Int(max(0, mode)), gameWidth: Int(gw), gameHeight: Int(gh), shownWidth: Int(sw), shownHeight: Int(sh), kind: Int(kind))
    }

    /// The session ended: the presenter's upscaling reading goes with it (a later session of the same app run reads its own).
    static func sessionEnded() {
        madeira_pfx_session_reset()
    }

    /// The menu's choice: written to the game's file by the caller; here the presenter takes it at once.
    static func chosen(_ value: String) {
        guard live else { return }
        if value == BCDUpscale.fit {
            madeira_pfx_set_upscale(2, 0)
        } else if let f = BCDUpscale.factor(value) {
            madeira_pfx_set_upscale(1, Int32((f * 1000).rounded()))
        } else {
            madeira_pfx_set_upscale(0, 0)
        }
        LogStore.shared.log("[feel] MetalFX upscaling \(value.isEmpty ? "off" : value) now, while the game runs")
    }
}
