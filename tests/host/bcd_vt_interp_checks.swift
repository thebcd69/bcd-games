// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-vt-interp.py for app/Madeira/BCDVTInterp.swift (compiled with it and with the few types it names).

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
}

enum Checks {
    static func main() {
        // the game's line
        check("the line: 1 and 3 are pictures, anything else is off",
              BCDVTInterp.frames("1") == 1 && BCDVTInterp.frames(" 3 ") == 3 && BCDVTInterp.frames(nil) == 0 && BCDVTInterp.frames("") == 0
              && BCDVTInterp.frames("2") == 0 && BCDVTInterp.frames("on") == 0 && BCDVTInterp.frames("13") == 0)
        check("the page offers off, one and up to three", BCDVTInterp.choices.map { $0.0 } == ["", "1", "3"])

        // what a session start does
        check("no line: nothing, and no line in the log",
              BCDVTInterp.start(line: nil, ios26: true, lossless: false, presentFX: true) == .off && BCDVTInterp.Start.off.log == nil
              && BCDVTInterp.start(line: "0", ios26: false, lossless: true, presentFX: false) == .off)
        check("the line with everything it needs: on, with the number", BCDVTInterp.start(line: "3", ios26: true, lossless: false, presentFX: true) == .on(3)
              && BCDVTInterp.Start.on(3).log?.contains("MADEIRA_VT_INTERP=3") == true && BCDVTInterp.Start.on(1).log?.contains("up to 1 picture between") == true)
        check("below iOS 26 it is refused, and the log says why",
              BCDVTInterp.start(line: "1", ios26: false, lossless: false, presentFX: true).log?.contains("needs iOS 26") == true)
        check("with Lossless Scaling in the session it is refused (one frame generator at a time)",
              BCDVTInterp.start(line: "1", ios26: true, lossless: true, presentFX: true).log?.contains("Lossless Scaling frame generation runs") == true)
        check("without the live present path it is refused, naming the switch",
              BCDVTInterp.start(line: "1", ios26: true, lossless: false, presentFX: false).log?.contains("env.MADEIRA_PRESENT_FX = 1") == true)

        // the engine's status in words
        let making = BCDVTInterp.Status(raw: 3, width: 1280, height: 720, gameFPS: 29.6, generatedFPS: 30.2, pairMs: 7.25, why: "")
        check("making pictures: the numbers, the size and iOS's time", making.words == "Making 30 extra frames a second for a game at 30 FPS, on a 1280 × 720 picture. One pair takes iOS 7.2 ms."
              || making.words == "Making 30 extra frames a second for a game at 30 FPS, on a 1280 × 720 picture. One pair takes iOS 7.3 ms.")
        check("making pictures reads as good", making.tone == .good)
        let impossible = BCDVTInterp.Status(raw: 4, width: 0, height: 0, gameFPS: 0, generatedFPS: 0, pairMs: 0, why: "VideoToolbox takes no pictures of 2560x1440")
        check("not possible: the engine's reason, as a sentence", impossible.words == "It cannot run here: VideoToolbox takes no pictures of 2560x1440." && impossible.tone == .warm)
        check("not possible without a reason still reads", BCDVTInterp.Status(state: .impossible).words == "It cannot run here: iOS gave no reason.")
        check("loading names the size", BCDVTInterp.Status(raw: 1, width: 960, height: 540, gameFPS: 0, generatedFPS: 0, pairMs: 0, why: "").words.contains("for a 960 × 540 picture"))
        check("a state the app does not know reads as loading; numbers that are not numbers read as 0",
              BCDVTInterp.Status(raw: 9, width: 0, height: 0, gameFPS: .nan, generatedFPS: -.infinity, pairMs: -1, why: "") == BCDVTInterp.Status(state: .loading))

        // the in-game menu
        let none = BCDVTInterp.section(wanted: 0, live: false, on: true, status: BCDVTInterp.Status())
        check("a game without the line: the choice at Off and nothing else", none.rows.count == 1 && none.rows[0].id == "mm.vti" && selected(none) == "")
        let next = BCDVTInterp.section(wanted: 3, live: false, on: true, status: BCDVTInterp.Status())
        check("chosen in a session that started without it: it applies at the next start", selected(next) == "3" && next.rows.count == 2
              && next.rows[1].title == "It applies the next time this game starts.")
        let live = BCDVTInterp.section(wanted: 1, live: true, on: true, status: making)
        check("a session that started with it: the status line follows the engine", selected(live) == "1" && live.rows[1].title == making.words)
        let off = BCDVTInterp.section(wanted: 1, live: true, on: false, status: making)
        check("switched off in the session: the choice shows Off, and says so", selected(off) == "" && off.rows[1].title == "Off in this session.")

        // the device test's words
        check("the test: not run, running, done", BCDVTInterp.Probe().value == "Not run" && BCDVTInterp.Probe(running: true).value == "Running"
              && BCDVTInterp.Probe(running: false, text: "x").value == "Done" && BCDVTInterp.Probe(running: false, text: "640x360: works").words == "640x360: works"
              && BCDVTInterp.Probe().words == BCDVTInterp.Probe.detail)

        print(failures == 0 ? "ALL PASS" : "FAILED: \(failures)")
        exit(failures == 0 ? 0 : 1)
    }

    static func selected(_ section: BCDMenuSection) -> String {
        if case .choice(_, let selected) = section.rows[0].kind { return selected }
        return "?"
    }
}

Checks.main()
