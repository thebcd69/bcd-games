// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, patch 0196)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// MetalFX upscaling's choices as plain values: what a session's start does with a game's `metalfx-upscale` line, the "to the screen"
// factor, and the in-game menu's rows for a session whose presenter enlarges the picture (env.MADEIRA_PRESENT_FX = 1).
// BCDPresentFXLive.swift reads and drives the engine; nothing here does, so the preview build and
// tests/host/bcd_present_fx_checks.swift use this file as it is. The "to the screen" rule (the smaller of screen / picture in both
// directions, at most 2, nothing under 1.1) is Porthole's patch 0196.

enum BCDUpscale {
    static let key = "metalfx-upscale"
    /// The switch: winemetal enlarges the picture and frame generation works before that (tools/patch-winemetal-present-fx.py).
    static let presentFXKey = "env.MADEIRA_PRESENT_FX"
    /// The value of "To the screen".
    static let fit = "fit"
    /// Where a launch's arguments are (read for the desktop's size; named here so that the settings catalog's scan of the sources does
    /// not take this reader for the variable's home).
    static let argumentsVariable = "MADEIRA_ARGS"

    /// What a session's start does about upscaling.
    enum Route: Equatable {
        /// No scaler anywhere.
        case off
        /// The old way: DXMT's MetalFX swap chain at this factor for Direct3D 11; the Direct3D 12 runtime reads the game's line itself.
        case engine(factor: Double)
        /// The presenter enlarges: MADEIRA_PFX_UPSCALE's value ("off", "fit" or a factor).
        case presenter(String)
    }

    /// "To the screen" for a picture of this size: the factor, or nil when the picture is already about as large as the screen
    /// (under 1.1) or a size is missing. The screen's long side goes with the picture's long side.
    static func fitFactor(width: Int, height: Int, screenLong: Int, screenShort: Int) -> Double? {
        guard width > 0, height > 0, screenLong > 0, screenShort > 0 else { return nil }
        let sw = Double(width >= height ? screenLong : screenShort), sh = Double(width >= height ? screenShort : screenLong)
        let factor = min(2, min(sw / Double(width), sh / Double(height)))
        return factor >= 1.1 ? (factor * 1000).rounded() / 1000 : nil
    }

    /// A game's line as a factor: 1.1 to 2 (larger values are 2), nil for anything else.
    static func factor(_ value: String?) -> Double? {
        guard let value, let f = Double(value), f >= 1.1 else { return nil }
        return min(f, 2)
    }

    /// nil: neither present-fx nor "to the screen", so the caller does what it always did with the line.
    /// With present-fx every choice goes to the presenter (also "off": the menu can turn it on while the game runs). "To the screen"
    /// without present-fx becomes a fixed factor for Direct3D 11, from the size of the game's desktop (the Direct3D 12 runtime works
    /// it out for each swap chain itself).
    static func route(value: String?, presentFX: Bool, desktop: (width: Int, height: Int)?, screenLong: Int, screenShort: Int) -> Route? {
        let value = (value ?? "").trimmingCharacters(in: .whitespaces)
        if presentFX {
            if value == fit { return .presenter(fit) }
            if let f = factor(value) { return .presenter(String(format: "%g", f)) }
            return .presenter("off")
        }
        guard value == fit else { return nil }
        guard let desktop, let f = fitFactor(width: desktop.width, height: desktop.height, screenLong: screenLong, screenShort: screenShort) else { return .off }
        return .engine(factor: f)
    }

    /// The desktop's size out of a launch's arguments ("/desktop=shell,1408x648 ...").
    static func desktopSize(arguments: String?) -> (width: Int, height: Int)? {
        guard let arguments, let start = arguments.range(of: "/desktop=") else { return nil }
        let token = arguments[start.upperBound...].prefix { !$0.isWhitespace }
        guard let comma = token.firstIndex(of: ",") else { return nil }
        let size = token[token.index(after: comma)...].lowercased().split(separator: "x").compactMap { Int($0) }
        guard size.count == 2, size[0] >= 320, size[1] >= 200, size[0] <= 8192, size[1] <= 8192 else { return nil }
        return (size[0], size[1])
    }

    /// What the presenter reports (winemetal's madeira_pfx_status).
    struct Status: Equatable {
        /// 0 off, 1 a factor, 2 to the screen.
        var mode = 0
        var gameWidth = 0, gameHeight = 0, shownWidth = 0, shownHeight = 0
        /// How the last frame reached the screen's size: 0 a copy, 1 MetalFX, 2 a plain (bilinear) enlargement.
        var kind = 0

        var words: String {
            guard gameWidth > 0, gameHeight > 0 else { return "The game has not drawn a frame yet." }
            let game = "\(gameWidth) × \(gameHeight)"
            if shownWidth == gameWidth && shownHeight == gameHeight {
                return mode == 2 ? "The game draws \(game), about as large as this screen already: nothing to enlarge. Choose a smaller resolution on the game's page to gain frames."
                                 : "The game draws \(game) and it is shown as it is."
            }
            let how = kind == 2 ? "a plain enlargement: MetalFX cannot do this pair of sizes" : "MetalFX"
            return "The game draws \(game); the screen gets \(shownWidth) × \(shownHeight) (\(how))."
        }
    }

    static let choices = [BCDChoice(id: "", title: "Off"), BCDChoice(id: fit, title: "To the screen"),
                          BCDChoice(id: "1.5", title: "1.5×"), BCDChoice(id: "2", title: "2×")]

    /// The choice that stands for a game's line in the menu: its own value when it is none of the four.
    static func selected(_ value: String?) -> String {
        let value = (value ?? "").trimmingCharacters(in: .whitespaces)
        if value == fit { return fit }
        guard let f = factor(value) else { return "" }
        return String(format: "%g", f)
    }

    /// The Display tab's "Upscaling" section of a session whose presenter enlarges the picture.
    static func section(value: String?, status: Status) -> BCDMenuSection {
        let now = selected(value)
        var all = choices
        if !all.contains(where: { $0.id == now }) { all.append(BCDChoice(id: now, title: now + "×")) }
        return BCDMenuSection(id: "mm.upscale", title: "Upscaling", rows: [
            BCDMenuRow(id: "mm.upscale",
                       title: "MetalFX upscaling",
                       detail: "Apple's MetalFX enlarges the picture the game draws. It switches now, while the game runs. To the screen picks the size that fills this screen, at most twice the game's; pair it with a small resolution for more frames.",
                       kind: .choice(all, selected: now)),
            BCDMenuRow(id: "mm.upscale.note", title: status.words, kind: .note(.soft)),
        ])
    }
}
