// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// What round 604's input features add to the in-game menu's Controls tab, as plain values built from the running
// game's BCDInputProfile. Every row id starts with "in.": the rest of it is the game page's setting id, so one
// change goes to the same line whichever screen made it (BCDInputLines.write). BCDInputLive.swift answers the rows.

enum BCDInputMenu {
    static let prefix = "in."

    static func handles(_ row: String) -> Bool { row.hasPrefix(prefix) }

    /// The game page's setting id behind a row.
    static func setting(of row: String) -> String { String(row.dropFirst(prefix.count)) }

    static func sections(_ profile: BCDInputProfile, players: BCDPlayersSnapshot = BCDPlayersSnapshot()) -> [BCDMenuSection] {
        [playersSection(profile, players), turboSection(profile), radialSection(profile), aimSection(profile), gestureSection(profile), pencilSection(profile),
         touchLookSection(profile)]
    }

    // MARK: Radial menu, aim mode, gestures and the Pencil

    static func radialSection(_ profile: BCDInputProfile) -> BCDMenuSection {
        let radial = profile.radial
        var rows = [BCDMenuRow(id: "in.radial", title: "Radial menu", detail: "A button at the edge opens a ring of keys and actions round your thumb.", symbol: "circle.grid.cross", kind: .toggle(radial.isOn))]
        if radial.isOn {
            rows.append(BCDMenuRow(id: "in.radial-place", title: "Where the button sits",
                                   kind: .choice(BCDRadialSettings.Place.allCases.map { BCDChoice(id: $0.rawValue, title: $0.title) }, selected: radial.place.rawValue)))
            rows.append(BCDMenuRow(id: "in.radial.note", title: "The slots are set on the game's page: Options › Radial menu.", kind: .note(.soft)))
        }
        return BCDMenuSection(id: "radial", title: "Radial menu", rows: rows)
    }

    static func aimSection(_ profile: BCDInputProfile) -> BCDMenuSection {
        let aim = profile.aim
        func number(_ v: Double) -> String { String(format: "%g", v) }
        var rows = [BCDMenuRow(id: "in.aim-zone", title: "A finger turns the camera in",
                               detail: "For first- and third-person games. A finger on a touch control never turns it.",
                               kind: .choice(BCDAimSettings.Zone.allCases.map { BCDChoice(id: $0.rawValue, title: $0.title) }, selected: aim.zone.rawValue))]
        if aim.isOn {
            rows.append(BCDMenuRow(id: "in.aim-as", title: "The finger moves",
                                   detail: "The right stick is for a game that plays with a controller layout and ignores the mouse for the camera.",
                                   kind: .choice(BCDAimSettings.Output.allCases.map { BCDChoice(id: $0.rawValue, title: $0.title) }, selected: aim.output.rawValue)))
            rows.append(BCDMenuRow(id: "in.aim-sens", title: "Speed",
                                   kind: .choice(BCDAimSettings.sensitivityChoices.map { BCDChoice(id: number($0), title: number($0) + "×") }, selected: number(aim.sensitivity))))
            rows.append(BCDMenuRow(id: "in.aim-vertical", title: "Up and down against left and right",
                                   kind: .choice(BCDAimSettings.verticalChoices.map { BCDChoice(id: String($0), title: "\($0) %") }, selected: String(aim.vertical))))
            rows.append(BCDMenuRow(id: "in.aim-invert", title: "Invert up and down", kind: .toggle(aim.invertY)))
            rows.append(BCDMenuRow(id: "in.aim-tap", title: "A tap is a click", kind: .toggle(aim.tapClicks)))
        }
        return BCDMenuSection(id: "aim", title: "Aim mode", rows: rows)
    }

    static func gestureSection(_ profile: BCDInputProfile) -> BCDMenuSection {
        let map = profile.gestures
        let text = map.isOn
            ? map.bindings.sorted { $0.key.rawValue < $1.key.rawValue }.map { "\($0.key.title): \($0.value.title)" }.joined(separator: "  ·  ")
            : "No gesture is bound. Taps, swipes and pinches with several fingers can each run a key or an action: Options › Touch gestures."
        return BCDMenuSection(id: "gestures", title: "Touch gestures", rows: [BCDMenuRow(id: "in.gestures.note", title: text, kind: .note(.soft))])
    }

    static func pencilSection(_ profile: BCDInputProfile) -> BCDMenuSection {
        let pencil = profile.pencil
        var rows = [BCDMenuRow(id: "in.pencil", title: "The Apple Pencil is a mouse",
                               detail: "Its tip puts the pointer exactly where it touches; a squeeze or a double tap clicks.", symbol: "pencil.tip", kind: .toggle(pencil.isOn))]
        if pencil.isOn {
            rows.append(BCDMenuRow(id: "in.pencil-tap", title: "A squeeze or a double tap",
                                   kind: .choice(BCDPencilSettings.Tap.allCases.map { BCDChoice(id: $0.rawValue, title: $0.title) }, selected: pencil.tap.rawValue)))
        }
        return BCDMenuSection(id: "pencil", title: "Apple Pencil", rows: rows)
    }

    /// The page a `.page` row names, or nil.
    static func page(_ id: String, _ profile: BCDInputProfile, players: BCDPlayersSnapshot = BCDPlayersSnapshot()) -> BCDMenuPage? {
        switch id {
        case playersPage: return playersPageContent(profile, players)
        case turboPage: return turbo(profile)
        case lookPage: return lookPageContent(profile)
        default: return nil
        }
    }

    // MARK: Players

    static let playersPage = "in.players"
    static let assignRow = "in.players.assign."
    static let cancelRow = "in.players.cancel"
    static let resetRow = "in.players.reset"

    static func playersSection(_ profile: BCDInputProfile, _ players: BCDPlayersSnapshot) -> BCDMenuSection {
        let count = players.connected
        return BCDMenuSection(id: "players", title: "Players", rows: [
            BCDMenuRow(id: playersPage, title: "Players", detail: "Which controller is Player 1, 2, 3 or 4, how hard each rumbles, and which player the touch controls are.",
                       symbol: "person.2.fill", kind: .page(playersPage, value: count == 0 ? "No controller" : count == 1 ? "1 controller" : "\(count) controllers")),
        ])
    }

    static func playersPageContent(_ profile: BCDInputProfile, _ players: BCDPlayersSnapshot) -> BCDMenuPage {
        var sections: [BCDMenuSection] = []
        for row in players.rows {
            let listening = players.listening == row.slot
            let rows = [
                BCDMenuRow(id: assignRow + String(row.slot), title: row.title,
                           detail: listening ? "Press a button on the controller now." : (row.name ?? "No controller"),
                           symbol: listening ? "dot.radiowaves.left.and.right" : "gamecontroller", kind: .action(primary: listening)),
                BCDMenuRow(id: "in.p\(row.slot + 1)-rumble", title: "Rumble",
                           kind: .choice(BCDPlayerSettings.rumbleChoices.map { BCDChoice(id: String($0), title: BCDPlayerWords.rumble($0)) }, selected: String(row.rumble))),
            ]
            sections.append(BCDMenuSection(id: "players.\(row.slot)", rows: rows))
        }
        var last = [BCDMenuRow(id: "in.touch-player", title: "The touch controls play as",
                               kind: .choice((0..<BCDPlayerSlots.count).map { BCDChoice(id: String($0 + 1), title: "P\($0 + 1)") }, selected: String(players.touchSlot + 1)))]
        if players.listening != nil {
            last.insert(BCDMenuRow(id: cancelRow, title: "Cancel choosing", kind: .action()), at: 0)
        }
        last.append(BCDMenuRow(id: resetRow, title: "Back to first come, first served", kind: .action()))
        last.append(BCDMenuRow(id: "in.players.note", title: "Nothing is remembered until you choose. The touch controls play as Player 1 unless you change it here.", kind: .note(.soft)))
        sections.append(BCDMenuSection(id: "players.rest", rows: last))
        return BCDMenuPage(id: playersPage, title: "Players", sections: sections)
    }

    // MARK: Touch control look

    static let lookCustom = "touchlook"
    static let lookPage = "in.look"

    /// One row on the Controls tab; the choices and a sample are on its page.
    static func touchLookSection(_ profile: BCDInputProfile) -> BCDMenuSection {
        BCDMenuSection(id: "touchlook", title: "Touch control look", rows: [
            BCDMenuRow(id: lookPage, title: "Colours and symbols", detail: "How the buttons and sticks over the game look and light up.",
                       symbol: "paintpalette", kind: .page(lookPage, value: profile.touchLook.summary)),
        ])
    }

    static func lookPageContent(_ profile: BCDInputProfile) -> BCDMenuPage {
        let look = profile.touchLook
        func chips<T: CaseIterable & RawRepresentable>(_ id: String, _ title: String, _ all: [T], _ current: T, _ name: (T) -> String) -> BCDMenuRow where T.RawValue == String {
            BCDMenuRow(id: "in." + id, title: title, kind: .choice(all.map { BCDChoice(id: $0.rawValue, title: name($0)) }, selected: current.rawValue))
        }
        return BCDMenuPage(id: lookPage, title: "Touch control look", sections: [
            BCDMenuSection(id: "touchlook.sample", rows: [BCDMenuRow(id: "in.look-sample", title: "Sample", kind: .custom(lookCustom))]),
            BCDMenuSection(id: "touchlook.choices", rows: [
                chips("look-body", "Body", Array(BCDTouchLook.Body.allCases), look.body) { $0.title },
                chips("look-accent", "Colour when pressed", Array(BCDTouchLook.Accent.allCases), look.accent) { $0.title },
                chips("look-press", "How a press shows", Array(BCDTouchLook.Press.allCases), look.press) { $0.title },
                chips("look-icons", "Symbols", Array(BCDTouchLook.Icons.allCases), look.icons) { $0.title },
                BCDMenuRow(id: "in.look-note", title: "The same for every game. Symbols change what the buttons show, not what the game receives. The colour chosen under Touch controls tints the rims and labels of the Night body only; any other body has its own.", kind: .note(.soft)),
            ]),
        ])
    }

    // MARK: Turbo and macros

    static let turboPage = "in.turbo"

    static func turboSection(_ profile: BCDInputProfile) -> BCDMenuSection {
        let turbo = profile.turbo, macros = profile.macros
        var rows = [BCDMenuRow(id: turboPage, title: "Turbo buttons",
                               detail: "Hold a button and the game sees it pressed again and again.", symbol: "bolt.fill",
                               kind: .page(turboPage, value: BCDInputPage.turboHeadline(turbo, BCDMacroSettings())))]
        if macros.isOn {
            rows.append(BCDMenuRow(id: "in.macros.note", title: macros.macros.map { "\($0.trigger.title): " + BCDMacroSettings.stepsText($0.steps) }.joined(separator: "  ·  "),
                                   kind: .note(.soft)))
        }
        rows.append(BCDMenuRow(id: "in.macros.edit", title: "Macros are set on the game's page: Options › Turbo and macros.", kind: .note(.soft)))
        return BCDMenuSection(id: "turbo", title: "Turbo and macros", rows: rows)
    }

    static func turbo(_ profile: BCDInputProfile) -> BCDMenuPage {
        let s = profile.turbo
        var rows: [BCDMenuRow] = [
            BCDMenuRow(id: "in.turbo-rate", title: "Presses a second",
                       kind: .choice(BCDTurboSettings.rateChoices.map { BCDChoice(id: String($0), title: String($0)) }, selected: String(s.rate))),
        ]
        for input in BCDPadInput.main {
            rows.append(BCDMenuRow(id: "in." + BCDInputLines.turboToggle + input.rawValue, title: input.title, kind: .toggle(s.pad.contains(input))))
        }
        rows.append(BCDMenuRow(id: "in." + BCDInputLines.turboToggle + BCDTurboSettings.mouseLeftName, title: "Left click (touch controls)", kind: .toggle(s.mouseLeft)))
        rows.append(BCDMenuRow(id: "in." + BCDInputLines.turboToggle + BCDTurboSettings.mouseRightName, title: "Right click (touch controls)", kind: .toggle(s.mouseRight)))
        return BCDMenuPage(id: turboPage, title: "Turbo buttons", sections: [BCDMenuSection(id: "turbo.buttons", rows: rows)])
    }
}
