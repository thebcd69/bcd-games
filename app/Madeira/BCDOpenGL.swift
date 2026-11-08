// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// OpenGL (docs/OPENGL.md): BCD's own OpenGL driver for the games that draw with OpenGL only (The Binding of Isaac: Rebirth, Mewgenics,
// DOOM 3: BFG Edition, Darkest Dungeon, Balatro, ...). It is a choice of the game, in the game's own file, and off until it is made:
//   gl-backend = zink   desktop OpenGL: Mesa's OSMesa and Zink on MoltenVK, and so on Metal (build/mesa-ios, build/moltenvk-ios)
//   gl-backend = gles   Apple's OpenGL ES through EAGL (a game that asks for desktop OpenGL fails its own version check there)
// No line, or any other word: nothing changes, the game gets no OpenGL, as always (WineProcessBridge.m: bcd_gl_session_setup exports
// MADEIRA_GL_BACKEND only for the two words, and everything OpenGL in the engine waits for it). The recommended lists and auto-tune write
// the same line for a game that cannot start without OpenGL.
//
// Plain values and rules, no engine type: the game page's row (BCDGamePageLive.swift, BCDGamePageSample.swift) and the Settings note
// (BCDSettingsSections.swift) take their words from here, so the three say the same.

enum BCDOpenGL {
    /// The game's line (also a bcd.cfg key, for every game).
    static let key = "gl-backend"
    static let desktop = "zink"
    static let es = "gles"

    /// What the game page's picker shows; the empty value removes the line.
    static var choices: [(String, String)] {
        [("", "Off"), (desktop, "Desktop OpenGL"), (es, "OpenGL ES")]
    }

    /// The word the engine acts on, or nil for "off" (no line, an empty value, or a word the engine does not know).
    static func word(_ value: String?) -> String? {
        let v = (value ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        return v == desktop || v == es ? v : nil
    }

    static func isOn(_ value: String?) -> Bool { word(value) != nil }

    /// The game page's note under the row.
    static let pageNote = "For a game that draws with OpenGL and stops at its start because it cannot make its window or finds no OpenGL. "
        + "A game gets OpenGL only when it is chosen here, or when a recommended list or auto-tune chooses it for that game. Choose Desktop OpenGL "
        + "(Mesa and Metal): it suits nearly every OpenGL game. OpenGL ES is Apple's own and only for a game whose recommended settings say so: "
        + "a game that asks for desktop OpenGL stops at its version check there. Both are new: they use more memory, the game compiles "
        + "its shaders as it goes, and some games stay slow. A Direct3D game gains nothing. From the next start."

    /// Settings › Graphics says where the choice is made (it is a game's, not every game's).
    static let settingsTitle = "OpenGL games"
    static let settingsValue = "Per game"
    static let settingsNote = "A game that draws with OpenGL has its own switch: open the game, then Options, Direct3D, OpenGL. It stays off until Desktop OpenGL or OpenGL ES is chosen there, or a recommended list or auto-tune chooses it for that game."
}
