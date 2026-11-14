// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The Swift half of tests/host/check-present-fx.py: app/Madeira/BCDPresentFX.swift (what a session's start does with a game's
// metalfx-upscale line, the "to the screen" factor, the in-game menu's rows) compiled as it is with small stand-ins for the menu's
// row types, and run on the host.
import Foundation

var failures = 0
func require(_ ok: @autoclosure () -> Bool, _ what: String) {
    if ok() { print("PASS: " + what) } else { print("FAIL: " + what); failures += 1 }
}

// ---- without present-fx and without "to the screen": nothing of this file's applies ---------------------------------------------
for value in [nil, "", "1.5", "2", "3", "0", "abc", " 2 "] as [String?] {
    require(BCDUpscale.route(value: value, presentFX: false, desktop: (1408, 648), screenLong: 2622, screenShort: 1206) == nil,
            "present-fx off, metalfx-upscale = \(value ?? "(none)"): the caller does what it always did (nil)")
}

// ---- "to the screen" without present-fx: a factor for Direct3D 11 from the desktop's size ----------------------------------------
require(BCDUpscale.fitFactor(width: 1408, height: 648, screenLong: 2622, screenShort: 1206) == 1.861, "1408x648 on a 2622x1206 screen: 1.861 (the height decides: 1206/648)")
require(BCDUpscale.fitFactor(width: 1280, height: 720, screenLong: 2622, screenShort: 1206) == 1.675, "1280x720 on the same screen: 1.675 (1206/720)")
require(BCDUpscale.fitFactor(width: 960, height: 540, screenLong: 2622, screenShort: 1206) == 2, "960x540: 2, never more")
require(BCDUpscale.fitFactor(width: 2622, height: 1206, screenLong: 2622, screenShort: 1206) == nil, "a picture of the screen's size: no scaler")
require(BCDUpscale.fitFactor(width: 2400, height: 1104, screenLong: 2622, screenShort: 1206) == nil, "a picture within a tenth of the screen (1.09): no scaler")
require(BCDUpscale.fitFactor(width: 2380, height: 1094, screenLong: 2622, screenShort: 1206) == 1.102, "just over a tenth (1.102): a scaler")
require(BCDUpscale.fitFactor(width: 648, height: 1408, screenLong: 2622, screenShort: 1206) == 1.861, "a portrait picture goes with the screen's short side across: the same factor")
require(BCDUpscale.fitFactor(width: 0, height: 720, screenLong: 2622, screenShort: 1206) == nil && BCDUpscale.fitFactor(width: 1280, height: 720, screenLong: 0, screenShort: 0) == nil,
        "a missing size: no scaler")
require(BCDUpscale.route(value: "fit", presentFX: false, desktop: (1408, 648), screenLong: 2622, screenShort: 1206) == .engine(factor: 1.861),
        "fit without present-fx: the old way at 1.861")
require(BCDUpscale.route(value: "fit", presentFX: false, desktop: (2622, 1206), screenLong: 2622, screenShort: 1206) == .off,
        "fit without present-fx on a desktop of the screen's size: no scaler at all")
require(BCDUpscale.route(value: "fit", presentFX: false, desktop: nil, screenLong: 2622, screenShort: 1206) == .off, "fit without a desktop size: no scaler")

// ---- with present-fx every choice goes to the presenter ---------------------------------------------------------------------------
require(BCDUpscale.route(value: nil, presentFX: true, desktop: nil, screenLong: 2622, screenShort: 1206) == .presenter("off"), "present-fx, no line: the presenter, off (the menu can switch it on)")
require(BCDUpscale.route(value: "fit", presentFX: true, desktop: nil, screenLong: 2622, screenShort: 1206) == .presenter("fit"), "present-fx, fit")
require(BCDUpscale.route(value: "1.5", presentFX: true, desktop: nil, screenLong: 2622, screenShort: 1206) == .presenter("1.5"), "present-fx, 1.5")
require(BCDUpscale.route(value: "3", presentFX: true, desktop: nil, screenLong: 2622, screenShort: 1206) == .presenter("2"), "present-fx, 3: at most 2")
require(BCDUpscale.route(value: "1.05", presentFX: true, desktop: nil, screenLong: 2622, screenShort: 1206) == .presenter("off"), "present-fx, under 1.1: off")

// ---- the launch's arguments ---------------------------------------------------------------------------------------------------------
require(BCDUpscale.desktopSize(arguments: "/desktop=shell,1408x648 \"C:\\Games\\a b\\game.exe\" -x").map { [$0.width, $0.height] } == [1408, 648], "the desktop's size out of /desktop=shell,WxH")
require(BCDUpscale.desktopSize(arguments: "/desktop=shell,1920X1080 C:\\windows\\system32\\services.exe").map { [$0.width, $0.height] } == [1920, 1080], "an upper-case X too")
require(BCDUpscale.desktopSize(arguments: "C:\\game.exe") == nil && BCDUpscale.desktopSize(arguments: nil) == nil && BCDUpscale.desktopSize(arguments: "/desktop=shell,10x10 a") == nil
        && BCDUpscale.desktopSize(arguments: "/desktop=shell") == nil, "no size, no arguments, a size that cannot be: nil")

// ---- the menu ---------------------------------------------------------------------------------------------------------------------
require(BCDUpscale.selected(nil) == "" && BCDUpscale.selected("fit") == "fit" && BCDUpscale.selected("1.5") == "1.5" && BCDUpscale.selected("2.0") == "2"
        && BCDUpscale.selected("5") == "2" && BCDUpscale.selected("x") == "", "a game's line as the menu's choice")
let enlarged = BCDUpscale.Status(mode: 2, gameWidth: 1408, gameHeight: 648, shownWidth: 2620, shownHeight: 1206, kind: 1)
require(enlarged.words == "The game draws 1408 × 648; the screen gets 2620 × 1206 (MetalFX).", "the status line of an enlarged picture")
require(BCDUpscale.Status(mode: 1, gameWidth: 1280, gameHeight: 720, shownWidth: 1920, shownHeight: 1080, kind: 2).words.contains("a plain enlargement"), "the bilinear pass is named as what it is")
require(BCDUpscale.Status(mode: 2, gameWidth: 2622, gameHeight: 1206, shownWidth: 2622, shownHeight: 1206, kind: 0).words.contains("nothing to enlarge"), "to the screen with nothing to do says so")
require(BCDUpscale.Status(mode: 0, gameWidth: 1408, gameHeight: 648, shownWidth: 1408, shownHeight: 648, kind: 0).words == "The game draws 1408 × 648 and it is shown as it is.", "off: shown as it is")
require(BCDUpscale.Status().words == "The game has not drawn a frame yet.", "before the first frame")
let section = BCDUpscale.section(value: "fit", status: enlarged)
require(section.id == "mm.upscale" && section.rows.map(\.id) == ["mm.upscale", "mm.upscale.note"], "the section: the choice and the status line, with the in-game menu's own ids")
if case .choice(let choices, let selected) = section.rows[0].kind {
    require(choices.map(\.id) == ["", "fit", "1.5", "2"] && choices.map(\.title) == ["Off", "To the screen", "1.5×", "2×"] && selected == "fit", "four choices, the game's own chosen")
} else { require(false, "the first row is a choice") }
if case .choice(let choices, let selected) = BCDUpscale.section(value: "1.75", status: enlarged).rows[0].kind {
    require(choices.count == 5 && choices.last?.id == "1.75" && selected == "1.75", "a factor the menu does not offer is shown as the game's own choice")
} else { require(false, "the first row is a choice") }

print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)
