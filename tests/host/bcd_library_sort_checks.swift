// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The Library's sort choices (BCDLibrarySort), compiled on the host by tests/host/check-bcd-library.py
// with app/Madeira/BCDStore.swift and the enum cut out of BCDLibraryView.swift, then run.

import Foundation
import SwiftUI

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: Any = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok ? "" : "\n     \(detail)"))
    if !ok { failures += 1 }
}

@main struct Main {
    @MainActor static func main() {
        let now = Date()
        func game(_ id: String, _ title: String, _ state: BCDGameState = .ready, size: Int64? = nil, played: Double? = nil,
                  added: Int? = nil) -> BCDGame {
            var g = BCDGame(id: id, title: title, store: .steam, state: state, size: size)
            g.lastPlayed = played.map { now - $0 }
            g.added = added
            return g
        }
        let list = [
            game("a", "Warcraft III", size: 1_200_000_000, played: 3600, added: 4),
            game("b", "Warcraft II", size: 400_000_000, played: 90_000, added: 9),
            game("c", "Hollow Knight", size: 9_100_000_000, played: 60, added: 1),
            game("d", "Portal 2", .notInstalled, size: 12_800_000_000),
            game("e", "Astro", size: 50_000_000, played: 100),
            game("f", "Bastion", .downloading(progress: 0.2), size: 2_400_000_000, added: 12),
        ]
        func ids(_ sort: BCDLibrarySort, _ games: [BCDGame]? = nil) -> String { sort.sorted(games ?? list).map(\.id).joined() }

        check("the choices are the classic library's four, by its words",
              BCDLibrarySort.allCases.map(\.rawValue) == ["played", "name", "added", "size"]
              && BCDLibrarySort.allCases.map(\.title) == ["Last played", "Name", "Recently added", "Folder size"],
              BCDLibrarySort.allCases.map(\.title))
        check("Last played: games on this device first, the latest play on top, then the rest by name",
              ids(.played) == "ceabfd", ids(.played))
        check("Name: A to Z, whatever the state", ids(.name) == "efcdba", ids(.name))
        check("Recently added: the newest entry first, games with no entry after them by name", ids(.added) == "fbaced", ids(.added))
        check("Folder size: the largest on disk first; a game not on this device has no folder", ids(.size) == "cabefd", ids(.size))
        check("the order does not depend on the order the games come in",
              BCDLibrarySort.allCases.allSatisfy { ids($0, list.reversed()) == ids($0) }, BCDLibrarySort.allCases.map { ids($0, list.reversed()) })
        var same = [game("z", "Same"), game("y", "Same")]
        check("games alike in everything go by id", ids(.name, same) == "yz" && ids(.size, same) == "yz", ids(.name, same))
        same.removeAll()
        check("an empty library sorts to nothing", BCDLibrarySort.allCases.allSatisfy { $0.sorted([]).isEmpty })
        check("every choice has a symbol", BCDLibrarySort.allCases.allSatisfy { !$0.symbol.isEmpty })

        print(failures == 0 ? "sort rules: all passed" : "sort rules: \(failures) failed")
        exit(failures == 0 ? 0 : 1)
    }
}
