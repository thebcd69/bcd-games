// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The Swift half of tests/host/check-framegen-live.py: app/Madeira/BCDFrameGen.swift (the status words and the in-game menu's rows of live
// frame generation) compiled as it is with small stand-ins for the menu's row types, and run on the host.
import Foundation

var failures = 0
func require(_ ok: @autoclosure () -> Bool, _ what: String) {
    if ok() { print("PASS: " + what) } else { print("FAIL: " + what); failures += 1 }
}

// ---- the engine's answer -------------------------------------------------------------------------------------------------------
let off = BCDFrameGenStatus(raw: 0, width: 0, height: 0, gameFPS: 0, generatedFPS: 0, gpuBusy: -1)
require(off.state == .off && off.words == "Off." && off.gpuBusy == nil, "state 0 is off; a GPU share of -1 is no reading")
let gen = BCDFrameGenStatus(raw: 2, width: 1920, height: 886, gameFPS: 41.4, generatedFPS: 40.6, gpuBusy: 0.714)
require(gen.state == .generating && gen.words == "Making 41 extra frames a second for a game at 41 FPS, on a 1920 × 886 picture. The GPU is busy 71% of the time.",
        "generating: the numbers the engine gave, rounded, in one sentence")
require(BCDFrameGenStatus(raw: 2, width: 0, height: 0, gameFPS: 30, generatedFPS: 30, gpuBusy: -1).words == "Making 30 extra frames a second for a game at 30 FPS.",
        "generating without a size or a GPU reading leaves both out")
let paused = BCDFrameGenStatus(raw: 3, width: 1280, height: 720, gameFPS: 28, generatedFPS: 0, gpuBusy: 0.97)
require(paused.words.hasPrefix("Paused: the GPU is full (97%)") && paused.tone == .warm, "paused says why, with the share, in the warm tone")
require(BCDFrameGenStatus(raw: 1, width: 0, height: 0, gameFPS: 0, generatedFPS: 0, gpuBusy: -1).words.hasPrefix("On. No extra frames right now"), "waiting says it is on")
require(BCDFrameGenStatus(raw: 4, width: 0, height: 0, gameFPS: 0, generatedFPS: 0, gpuBusy: -1).tone == .warm, "cannot run here is warm")
require(BCDFrameGenStatus(raw: 5, width: 0, height: 0, gameFPS: 0, generatedFPS: 0, gpuBusy: -1).words.hasPrefix("Measuring"), "measuring only says so")
require(BCDFrameGenStatus(raw: 77, width: 0, height: 0, gameFPS: 0, generatedFPS: 0, gpuBusy: -1).state == .waiting, "a state this build does not know reads as waiting")
let odd = BCDFrameGenStatus(raw: 2, width: 100, height: 100, gameFPS: .nan, generatedFPS: -5, gpuBusy: 3)
require(odd.gameFPS == 0 && odd.generatedFPS == 0 && odd.gpuBusy == 1, "numbers that cannot be are kept out (not a number, negative, a share over 1)")
require(gen.tone == .good && off.tone == .soft, "generating is good, off is soft")

// ---- the menu's rows -----------------------------------------------------------------------------------------------------------
let offSection = BCDFrameGenMenu.section(on: false, status: off, guardOn: true, measuring: false)
require(offSection.id == "mm.fgen" && offSection.rows.count == 1 && offSection.rows[0].id == "mm.fgen",
        "off: one row, the choice, with the id the menu already handles")
if case .choice(let choices, let selected) = offSection.rows[0].kind {
    require(choices.map(\.id) == ["", "1"] && choices.map(\.title) == ["Off", "MetalFX 2×"] && selected == "", "off: the same two choices as before, Off chosen")
} else { require(false, "the first row is a choice") }
let onSection = BCDFrameGenMenu.section(on: true, status: gen, guardOn: true, measuring: false)
require(onSection.rows.map(\.id) == ["mm.fgen", "mm.fgen.note", "mm.fgen.guard", "mm.fgen.measure"], "on: the choice, the status line, the guard and measure only")
require(onSection.rows[1].title == gen.words && onSection.rows[1].kind == .note(.good), "on: the status line is the engine's sentence in its tone")
require(onSection.rows[2].kind == .toggle(true) && onSection.rows[3].kind == .toggle(false), "on: the guard is on and measure only off as given")
require(BCDFrameGenMenu.section(on: true, status: gen, guardOn: false, measuring: true).rows[2].kind == .toggle(false), "the guard's switch follows what it is given")
require(onSection.rows.allSatisfy { $0.id.hasPrefix("mm.") }, "every row id is one of the in-game menu's own (mm.)")
require(!BCDFrameGenMenu.switchNote.isEmpty && BCDFrameGenMenu.switchTitle.contains("experimental"), "the switch has a title that says experimental and a note")

print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
exit(failures == 0 ? 0 : 1)
