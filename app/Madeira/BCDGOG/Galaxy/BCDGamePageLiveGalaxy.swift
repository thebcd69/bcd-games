// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// GOG Galaxy features on the game's page, engine side (docs/GOG.md, "Galaxy"). BCDGamePageLive.swift reaches this through two one-line
// hooks. The group is there only for a GOG game BCD installed, and holds one switch, off until the player turns it on: it writes
// `env.BCD_GOG_GALAXY = 1` into the game's own file and takes the line out again when off (BCDGOGGalaxy reads it at the next start).
// Engine-bound, and outside app/Madeira's top level, so the preview build never compiles it.

extension BCDGamePageLive {
    static let galaxySetting = "gog.galaxy"

    /// The GOG Galaxy group of a GOG game BCD installed (GOG on); nil for every other game.
    func galaxyGroup(_ game: BCDGame, _ entry: LibraryEntry) -> BCDGameSettingGroup? {
        guard game.store == .gog, BCDGOG.enabled, BCDGOG.shared.record(forEntry: entry) != nil else { return nil }
        let on = BCDGOGGalaxy.isOn(entry)
        let note = "For a GOG game that has achievements, statistics or leaderboards (it looks for GOG Galaxy while it runs). On: while this game runs, "
            + "BCD answers the game in GOG Galaxy's place and sends its achievements, statistics, leaderboard scores and play time to your GOG account, "
            + "signed in as the account BCD is signed in to. Off (the default): the game finds no GOG Galaxy, as before. No overlay, no friends, "
            + "no multiplayer through Galaxy, no cloud saves. Experimental. From the game's next start."
        return BCDGameSettingGroup(id: "gog-galaxy", title: "GOG Galaxy", symbol: "trophy", settings: [
            BCDGameSetting(id: Self.galaxySetting, title: "Achievements and leaderboards (experimental)", kind: .toggle,
                           value: on ? "1" : "0", defaultValue: "0", note: note),
        ])
    }

    /// The switch: true when the setting was this group's.
    func galaxySet(_ setting: String, to value: String, for game: BCDGame) -> Bool {
        guard setting == Self.galaxySetting else { return false }
        // Off while bcd.cfg has it on for every GOG game needs a line of its own ("0"); otherwise off is no line at all.
        let everywhere = MadeiraConfig.get("env." + BCDGOGGalaxy.switchName) == "1"
        setLine(game, "env." + BCDGOGGalaxy.switchName, value == "1" ? "1" : (everywhere ? "0" : ""))
        onChange?()
        return true
    }
}
