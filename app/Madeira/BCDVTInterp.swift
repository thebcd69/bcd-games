// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Apple's VideoToolbox frame interpolation as a frame generator (an experiment; winemetal's madeira_vti_* functions,
// tools/patch-winemetal-vt-interp.py), as plain values: the game's line, what the engine reports, the in-game menu's rows and the words of
// the device test in Settings. BCDVTInterpLive.swift reads and drives the engine; nothing here does, so the preview build and
// tests/host/bcd_vt_interp_checks.swift use this file as it is.

enum BCDVTInterp {
    /// The game's own line: "1" or "3" pictures between every two frames; no line is off.
    static let key = "vt-interpolation"
    /// What the app exports for the engine when the line is there (and iOS is 26 or later).
    static let variable = "MADEIRA_VT_INTERP"
    /// It works on the picture the live present path holds, so that switch has to be on for the game too.
    static let presentKey = "env.MADEIRA_PRESENT_FX"
    /// The app sets this for a session with Lossless Scaling frame generation (BCDLosslessLive.launch); one generator runs at a time.
    static let losslessVariable = "MADEIRA_LSFG_CTL"

    static let title = "Apple frame interpolation (experimental)"
    static let choices = [("", "Off"), ("1", "One frame between two"), ("3", "Up to three")]
    static let note = "An experiment. iOS's own video frame interpolation makes one or three pictures between every two frames the game draws, from the two pictures alone: it needs no help from the game and nothing but iOS 26. Whether this device takes the game's resolution, and how long a pair takes, is what the test in Settings (Graphics, Experimental) finds out; try a small resolution first. It switches on Live upscaling and frame generation for this game, turns MetalFX frame generation off, and a frame-rate limit then holds the game's own frames with the extra ones on top. Extra frames add a little delay."

    /// The line's value as a number of pictures: 1, 3, or 0 for anything else.
    static func frames(_ value: String?) -> Int {
        switch value?.trimmingCharacters(in: .whitespaces) {
        case "1": return 1
        case "3": return 3
        default: return 0
        }
    }

    /// What a session start does with the line. `ios26`: the system has the processor. `lossless`: Lossless Scaling runs in this
    /// session (one frame generator at a time). `presentFX`: the live present path is on for this game.
    enum Start: Equatable {
        case off
        case on(Int)
        case refused(String)

        var log: String? {
            switch self {
            case .off: return nil
            case .on(let n):
                return "[vt-interp] Apple frame interpolation on for this session: up to \(n) picture\(n == 1 ? "" : "s") between two frames (\(BCDVTInterp.variable)=\(n)); MetalFX frame generation off"
            case .refused(let why):
                return "[vt-interp] this game asks for Apple frame interpolation, but \(why): off for this session"
            }
        }
    }

    static func start(line: String?, ios26: Bool, lossless: Bool, presentFX: Bool) -> Start {
        let n = frames(line)
        guard n > 0 else { return .off }
        guard ios26 else { return .refused("it needs iOS 26") }
        guard !lossless else { return .refused("Lossless Scaling frame generation runs in this session") }
        guard presentFX else { return .refused("Live upscaling and frame generation (\(presentKey) = 1) is off for it") }
        return .on(n)
    }

    /// What the engine reports (madeira_vti_status).
    struct Status: Equatable {
        enum State: Int32 { case off = 0, loading = 1, ready = 2, making = 3, impossible = 4 }
        var state: State = .off
        var width = 0, height = 0
        var gameFPS = 0.0, generatedFPS = 0.0
        /// VideoToolbox's time for one pair of frames, in milliseconds.
        var pairMs = 0.0
        /// Why no pictures can be made (state impossible), as the engine says it.
        var why = ""

        init(state: State = .off, width: Int = 0, height: Int = 0, gameFPS: Double = 0, generatedFPS: Double = 0, pairMs: Double = 0, why: String = "") {
            self.state = state; self.width = width; self.height = height
            self.gameFPS = gameFPS; self.generatedFPS = generatedFPS; self.pairMs = pairMs; self.why = why
        }

        init(raw: Int32, width: Int32, height: Int32, gameFPS: Double, generatedFPS: Double, pairMs: Double, why: String) {
            self.init(state: State(rawValue: raw) ?? .loading, width: Int(width), height: Int(height),
                      gameFPS: gameFPS.isFinite ? max(0, gameFPS) : 0, generatedFPS: generatedFPS.isFinite ? max(0, generatedFPS) : 0,
                      pairMs: pairMs.isFinite ? max(0, pairMs) : 0, why: why)
        }

        private var sizeWords: String? { width > 0 && height > 0 ? "\(width) × \(height)" : nil }

        var words: String {
            switch state {
            case .off:
                return "Off."
            case .loading:
                return "Starting: iOS is loading its model" + (sizeWords.map { " for a \($0) picture" } ?? "") + ". The game runs as usual meanwhile."
            case .ready:
                return "Ready. No extra frames right now: the game is faster than the screen can show twice, or it has only just started."
            case .making:
                var out = "Making \(Int(generatedFPS.rounded())) extra frames a second for a game at \(Int(gameFPS.rounded())) FPS"
                if let size = sizeWords { out += ", on a \(size) picture" }
                out += "."
                if pairMs > 0 { out += String(format: " One pair takes iOS %.1f ms.", pairMs) }
                return out
            case .impossible:
                return "It cannot run here: " + (why.isEmpty ? "iOS gave no reason" : why) + "."
            }
        }

        var tone: BCDSessionTone {
            switch state {
            case .impossible: return .warm
            case .making: return .good
            default: return .soft
            }
        }
    }

    /// The Display tab's section. `wanted`: the pictures the game's line asks for (0: none). `live`: this session was started with it
    /// (only then can it be switched now). `on`: the menu's switch in this session.
    static func section(wanted: Int, live: Bool, on: Bool, status: Status) -> BCDMenuSection {
        let selected = wanted == 0 || (live && !on) ? "" : String(wanted)
        var rows = [BCDMenuRow(id: "mm.vti",
                               title: "Apple frame interpolation",
                               detail: "An experiment: iOS's video frame interpolation makes one or three pictures between every two frames the game draws. In a session that started with it, Off and on switch at once; the number of pictures, and a session that started without it, follow the next time the game starts.",
                               kind: .choice([BCDChoice(id: "", title: "Off"), BCDChoice(id: "1", title: "1 frame"), BCDChoice(id: "3", title: "Up to 3")], selected: selected))]
        if live {
            rows.append(BCDMenuRow(id: "mm.vti.note", title: on ? status.words : "Off in this session.", kind: .note(on ? status.tone : .soft)))
        } else if wanted > 0 {
            rows.append(BCDMenuRow(id: "mm.vti.note", title: "It applies the next time this game starts.", symbol: "arrow.clockwise.circle", kind: .note(.warm)))
        }
        return BCDMenuSection(id: "mm.vti", title: "Apple frame interpolation", rows: rows)
    }

    /// The device test in Settings (madeira_vti_probe): no game needed.
    struct Probe: Equatable {
        var running = false
        /// The lines the test wrote, newest run; empty before the first.
        var text = ""

        static let title = "Test Apple frame interpolation on this device"
        static let detail = "Finds out what iOS's frame interpolation does here, without a game: which picture sizes it takes, how long one pair of frames takes, and whether its result is right. It takes up to a minute and needs iOS 26. The result is also in the log, under [vt-interp] probe."
        /// What the log's lines start with.
        static let logPrefix = "[vt-interp] probe: "
        static let needsIOS26 = "This needs iOS 26 or later."
        static let whileGameRuns = "Not while a game is running: the test would take its memory and its GPU time."

        var value: String { running ? "Running" : text.isEmpty ? "Not run" : "Done" }
        var words: String { running ? "Running. This takes up to a minute." : text.isEmpty ? Probe.detail : text }
    }
}
