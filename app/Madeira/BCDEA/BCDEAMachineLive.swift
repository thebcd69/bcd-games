// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

extension BCDEAMachine {
    /// One line of a game's settings as its launch reads it: the game's own file (GameProfiles.swift) first, then the entry's own lines, then
    /// bcd.cfg (the order BCDEpic.flag and MadeiraConfig.applyGame use). Empty values count as not set.
    static func line(_ key: String, entry: LibraryEntry) -> String? {
        if let value = GameProfile(windowsPath: entry.windowsPath).get(key), !value.isEmpty { return value }
        if let value = MadeiraConfig.parse(entry.gameConfigText ?? "")[key], !value.isEmpty { return value }
        return MadeiraConfig.get(key).flatMap { $0.isEmpty ? nil : $0 }
    }

    /// What this game's launch reports about the machine.
    static func choice(for entry: LibraryEntry) -> Choice {
        choose(line: { line($0, entry: entry) }, nvidiaSwitch: LibraryPrefs.nvidia(entry.windowsPath), avxSwitch: LibraryPrefs.avx(entry.windowsPath))
    }
}
