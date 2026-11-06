// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// Harness for app/Madeira/BCDOpenGL.swift (tests/host/check-opengl-ui.py compiles both as main.swift + the file).

import Foundation

var failures = 0
func expect(_ ok: Bool, _ what: String) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
}

expect(BCDOpenGL.key == "gl-backend", "the game's line is gl-backend (the key WineProcessBridge.m reads)")
expect(BCDOpenGL.choices.map { $0.0 } == ["", "zink", "gles"], "the picker offers off (the empty value removes the line), zink and gles")
expect(BCDOpenGL.choices.map { $0.1 } == ["Off", "Desktop OpenGL", "OpenGL ES"], "... in BCD's words")
expect(BCDOpenGL.word("zink") == "zink" && BCDOpenGL.word("gles") == "gles", "the two words are the engine's")
expect(BCDOpenGL.word(" ZINK ") == "zink" && BCDOpenGL.word("Gles") == "gles", "... read in any case, trimmed, as WineProcessBridge.m reads them")
for bad in [nil, "", "on", "1", "true", "opengl", "vulkan", "zink2", "kosmickrisp"] as [String?] {
    expect(BCDOpenGL.word(bad) == nil && !BCDOpenGL.isOn(bad), "\(bad.map { "\"\($0)\"" } ?? "no line") is off")
}
expect(BCDOpenGL.isOn("zink") && BCDOpenGL.isOn("gles"), "both words are on")

let texts = [BCDOpenGL.pageNote, BCDOpenGL.settingsNote, BCDOpenGL.settingsTitle, BCDOpenGL.settingsValue]
expect(texts.allSatisfy { !$0.lowercased().contains("licence") }, "no text says \"licence\" (the interface spells license)")
expect(texts.allSatisfy { !$0.contains("—") && !$0.contains("  ") }, "no dash or double space in the texts")
expect(BCDOpenGL.pageNote.hasSuffix("From the next start."), "the row's note says when it applies")
expect(BCDOpenGL.pageNote.contains("Direct3D game gains nothing") && BCDOpenGL.pageNote.contains("Desktop OpenGL") && BCDOpenGL.pageNote.contains("OpenGL ES"),
       "the note says what each choice is and that a Direct3D game gains nothing")
expect(BCDOpenGL.pageNote.count < 700, "the row's note stays short (\(BCDOpenGL.pageNote.count) characters)")
expect(BCDOpenGL.settingsNote.contains("Options") && BCDOpenGL.settingsNote.contains("Direct3D") && BCDOpenGL.settingsNote.contains("OpenGL"),
       "Settings says where the choice is made")

if failures > 0 { exit(1) }
print("PASS: BCDOpenGL")
