// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Frame generation's live side as plain values: what the engine reports (winemetal's madeira_fgl_status) and the in-game menu's rows for a
// session that can switch it while the game runs (env.MADEIRA_PRESENT_FX = 1). BCDFrameGenLive.swift reads and drives the engine; nothing
// here does, so the preview build and tests/host/bcd_frame_gen_checks.swift use this file as it is.

/// What frame generation is doing right now.
struct BCDFrameGenStatus: Equatable {
    /// The numbers of madeira_fgl_status.
    enum State: Int32 { case off = 0, waiting = 1, generating = 2, paused = 3, unsupported = 4, measuring = 5 }
    var state: State = .off
    /// The picture the frames are made on.
    var width = 0, height = 0
    var gameFPS = 0.0, generatedFPS = 0.0
    /// The share of the time the GPU is busy, 0 to 1; nil while nothing measures it (the GPU guard is off).
    var gpuBusy: Double? = nil

    init(state: State = .off, width: Int = 0, height: Int = 0, gameFPS: Double = 0, generatedFPS: Double = 0, gpuBusy: Double? = nil) {
        self.state = state; self.width = width; self.height = height
        self.gameFPS = gameFPS; self.generatedFPS = generatedFPS; self.gpuBusy = gpuBusy
    }

    /// From the engine's raw answer; a state it does not know reads as waiting.
    init(raw: Int32, width: Int32, height: Int32, gameFPS: Double, generatedFPS: Double, gpuBusy: Double) {
        self.init(state: State(rawValue: raw) ?? .waiting, width: Int(width), height: Int(height),
                  gameFPS: gameFPS.isFinite ? max(0, gameFPS) : 0, generatedFPS: generatedFPS.isFinite ? max(0, generatedFPS) : 0,
                  gpuBusy: gpuBusy >= 0 && gpuBusy.isFinite ? min(1, gpuBusy) : nil)
    }

    private var busyWords: String? { gpuBusy.map { "\(Int(($0 * 100).rounded()))%" } }
    private var sizeWords: String? { width > 0 && height > 0 ? "\(width) × \(height)" : nil }

    /// One plain sentence for the menu.
    var words: String {
        switch state {
        case .off:
            return "Off."
        case .waiting:
            return "On. No extra frames right now: the game has only just started, or it is already faster than the screen can show twice."
        case .generating:
            var out = "Making \(Int(generatedFPS.rounded())) extra frames a second for a game at \(Int(gameFPS.rounded())) FPS"
            if let size = sizeWords { out += ", on a \(size) picture" }
            out += "."
            if let busy = busyWords { out += " The GPU is busy \(busy) of the time." }
            return out
        case .paused:
            return "Paused: the GPU is full" + (busyWords.map { " (\($0))" } ?? "") + ", so extra frames would slow the game itself. They come back when there is room."
        case .unsupported:
            return "It cannot run here: this device, or this game's picture format, has no MetalFX frame interpolation."
        case .measuring:
            return "Measuring: the extra frames are made and not shown, so the frame rate shows what making them costs."
        }
    }

    var tone: BCDSessionTone {
        switch state {
        case .paused, .unsupported: return .warm
        case .generating: return .good
        default: return .soft
        }
    }
}

/// The Display tab's "Frame generation" section of a session that can switch it while the game runs.
enum BCDFrameGenMenu {
    /// The switch itself (env.MADEIRA_PRESENT_FX), in Settings and on a game's page.
    static let switchTitle = "Live upscaling and frame generation (experimental)"
    static let switchNote = "MetalFX upscaling and frame generation are then switched from the in-game menu, under Display, without restarting the game, and frame generation works on the picture before it is enlarged, which costs much less. Frame generation also stops waiting for a free screen buffer, pauses while the GPU is full, and works for games below 15 FPS. Off, both are as they always were and are read when the game starts. Frame generation needs iOS 26."
    static let guardDetail = "Extra frames cost GPU time. While the GPU is busy nearly all the time they would slow the game itself, so they stop until there is room again."
    static let measureDetail = "Makes the extra frames without showing them, so the frame rate shows what making them costs. For comparing; it is off again the next time the game starts."

    static func section(on: Bool, status: BCDFrameGenStatus, guardOn: Bool, measuring: Bool) -> BCDMenuSection {
        var rows = [BCDMenuRow(id: "mm.fgen",
                               title: "Frame generation",
                               detail: "MetalFX makes up a frame between every two the game draws: twice the frames, for GPU time, a little delay and some smearing at edges and the HUD. It switches now, while the game runs, and the screen is held at its top rate while it is on.",
                               kind: .choice([BCDChoice(id: "", title: "Off"), BCDChoice(id: "1", title: "MetalFX 2×")], selected: on ? "1" : ""))]
        guard on else { return BCDMenuSection(id: "mm.fgen", title: "Frame generation", rows: rows) }
        rows.append(BCDMenuRow(id: "mm.fgen.note", title: status.words, kind: .note(status.tone)))
        rows.append(BCDMenuRow(id: "mm.fgen.guard", title: "Pause when the GPU is full", detail: guardDetail, kind: .toggle(guardOn)))
        rows.append(BCDMenuRow(id: "mm.fgen.measure", title: "Measure only", detail: measureDetail, kind: .toggle(measuring)))
        return BCDMenuSection(id: "mm.fgen", title: "Frame generation", rows: rows)
    }
}
