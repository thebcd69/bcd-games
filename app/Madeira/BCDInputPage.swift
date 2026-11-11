// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The sections round 604's input features add to a game's page (Options), as plain values built from the game's
// BCDInputProfile. They know nothing of the engine: BCDInputLive.swift hands them the profile and carries out a change.
// The preview app shows them on sample data.

enum BCDInputPage {
    /// The ids of the sections `groups` makes (the preview app opens them by name: -BCDTab game-<id>-<id>).
    static let groupIDs = ["turbo", "radial", "aim", "gestures", "pencil", "touchlook"]

    /// The sections, in the order Options lists them.
    static func groups(_ profile: BCDInputProfile) -> [BCDGameSettingGroup] {
        [turboGroup(profile), radialGroup(profile), aimGroup(profile), gestureGroup(profile), pencilGroup(profile), touchLookGroup(profile)]
    }

    // MARK: Things BCD can do (the radial menu's slots, the gestures' bindings)

    /// Every action, under its group's heading; built once and shared by every row that offers them.
    private static let actionChoices: [BCDGameChoice] = {
        var out: [BCDGameChoice] = []
        var group = ""
        for choice in BCDInputAction.choices {
            out.append(BCDGameChoice(value: choice.id, label: choice.title, group: choice.group == group ? nil : choice.group))
            group = choice.group
        }
        return out
    }()

    /// A row's choices: "" first (what it is without a line), then the actions.
    private static func actionList(first: BCDGameChoice, extra: [BCDGameChoice] = []) -> [BCDGameChoice] { [first] + extra + actionChoices }

    // MARK: Radial menu

    static func radialGroup(_ profile: BCDInputProfile) -> BCDGameSettingGroup {
        let radial = profile.radial
        var settings = [
            BCDGameSetting(id: "radial", title: "Radial menu", kind: .toggle, value: radial.isOn ? "1" : "0", defaultValue: "0",
                           note: "A small button at the edge of the screen. Put a finger on it and a ring of up to eight things opens round it: keys, Alt+Tab, a click, BCD's menu, a button of the controller. Slide to one and let go, or lift at once and tap one. Off until you turn it on; nothing is drawn while it is closed."),
        ]
        guard radial.isOn else {
            return BCDGameSettingGroup(id: "radial", title: "Radial menu", symbol: "circle.grid.cross", settings: settings, headline: "Off")
        }
        settings.append(BCDGameSetting(id: "radial-place", title: "Where the button sits",
                                       kind: .choice(BCDRadialSettings.Place.allCases.map { BCDGameChoice(value: $0.rawValue, label: $0.title) }),
                                       value: radial.place.rawValue, defaultValue: ""))
        for n in 1...BCDRadialSettings.slotCount {
            let stored = profile.get(BCDInputLines.radial, "radial-\(n)") ?? ""
            let fallback = BCDRadialSettings.defaultSlots[n - 1]
            let list = actionList(first: BCDGameChoice(value: "", label: "Default: " + (fallback?.title ?? "empty")),
                                  extra: [BCDGameChoice(value: "none", label: "Empty")])
            settings.append(BCDGameSetting(id: "radial-\(n)", title: "Slot \(n)" + (n == 1 ? ", at the top" : ""), kind: .choice(list),
                                           value: stored, defaultValue: "",
                                           note: n == 1 ? "The slots go clockwise from the top." : nil))
        }
        return BCDGameSettingGroup(id: "radial", title: "Radial menu", symbol: "circle.grid.cross", settings: settings,
                                   headline: "On · \(radial.filled.count) slots · " + radial.place.title)
    }

    // MARK: Aim mode

    static func aimGroup(_ profile: BCDInputProfile) -> BCDGameSettingGroup {
        let aim = profile.aim
        func number(_ v: Double) -> String { String(format: "%g", v) }
        var settings = [
            BCDGameSetting(id: "aim-zone", title: "A finger turns the camera in",
                           kind: .choice(BCDAimSettings.Zone.allCases.map { BCDGameChoice(value: $0.rawValue, label: $0.title) }),
                           value: aim.zone.rawValue, defaultValue: "",
                           note: "For first- and third-person games: drag a finger in that part of the screen and the camera turns, as a mouse turns it; the finger moves no pointer there. A finger on a touch control never reaches it. Off until you choose."),
        ]
        if aim.isOn {
            settings += [
                BCDGameSetting(id: "aim-as", title: "The finger moves", kind: .choice(BCDAimSettings.Output.allCases.map { BCDGameChoice(value: $0.rawValue, label: $0.title) }),
                               value: aim.output.rawValue, defaultValue: "",
                               note: "The mouse is what a finger always did. For a game that plays with a controller layout and ignores the mouse for the camera (NieR: Automata), choose the right stick: the finger turns the camera like the right stick of a pad and stops when it stops. It reaches the game as player 1's controller. Speed, the up and down setting and the invert row keep their meaning."),
                BCDGameSetting(id: "aim-sens", title: "Speed", kind: .choice(BCDAimSettings.sensitivityChoices.map { BCDGameChoice(value: number($0), label: number($0) + "×") }),
                               value: number(aim.sensitivity), defaultValue: "1",
                               note: "1× is two mouse counts for every point the finger travels. What that means for the camera depends on the game's own mouse sensitivity."),
                BCDGameSetting(id: "aim-vertical", title: "Up and down against left and right",
                               kind: .choice(BCDAimSettings.verticalChoices.map { BCDGameChoice(value: String($0), label: "\($0) %") }),
                               value: String(aim.vertical), defaultValue: "100"),
                BCDGameSetting(id: "aim-invert", title: "Invert up and down", kind: .toggle, value: aim.invertY ? "1" : "0", defaultValue: "0"),
                BCDGameSetting(id: "aim-tap", title: "A tap is a click", kind: .toggle, value: aim.tapClicks ? "1" : "0", defaultValue: "0",
                               note: "A short tap in the zone clicks the left mouse button (fire). Off, a tap does nothing; put a fire button on the touch controls instead."),
            ]
        }
        return BCDGameSettingGroup(id: "aim", title: "Aim mode", symbol: "scope", settings: settings,
                                   headline: aim.isOn ? aim.zone.title + " · " + number(aim.sensitivity) + "×" : "Off")
    }

    // MARK: Touch gestures

    static func gestureGroup(_ profile: BCDInputProfile) -> BCDGameSettingGroup {
        let map = profile.gestures
        let none = BCDGameChoice(value: "", label: "Nothing (as usual)")
        let settings = BCDGesture.allCases.enumerated().map { i, gesture in
            BCDGameSetting(id: gesture.line, title: gesture.title, kind: .choice(actionList(first: none)),
                           value: map.bindings[gesture]?.id ?? "", defaultValue: "",
                           note: i == 0 ? "Taps, swipes and pinches with several fingers, each running one thing: a key, Alt+Tab, a click, the wheel, BCD's menu, the key bar. They work wherever the game takes touches; with the touch controls on, a finger on a control is that control's. A game that also takes the first finger as a click may see it; the usual pointer setting decides." : nil,
                           heading: i == 0 ? "Fingers on the game" : nil)
        }
        return BCDGameSettingGroup(id: "gestures", title: "Touch gestures", symbol: "hand.draw", settings: settings,
                                   headline: map.isOn ? "\(map.bindings.count) bound" : "None")
    }

    // MARK: Apple Pencil

    static func pencilGroup(_ profile: BCDInputProfile) -> BCDGameSettingGroup {
        let pencil = profile.pencil
        var settings = [
            BCDGameSetting(id: "pencil", title: "The Apple Pencil is a mouse", kind: .toggle, value: pencil.isOn ? "1" : "0", defaultValue: "0",
                           note: "The tip puts the pointer exactly where it touches and the tip on the glass is the left button; hovering moves the pointer without a click on an iPad that has hover. A finger keeps doing what the pointer setting says. Off until you turn it on."),
        ]
        if pencil.isOn {
            settings.append(BCDGameSetting(id: "pencil-tap", title: "A squeeze or a double tap of the Pencil",
                                           kind: .choice(BCDPencilSettings.Tap.allCases.map { BCDGameChoice(value: $0.rawValue, label: $0.title) }),
                                           value: pencil.tap.rawValue, defaultValue: "",
                                           note: "The Pencil Pro's squeeze and the Pencil's double tap. It clicks where the Pencil is."))
        }
        return BCDGameSettingGroup(id: "pencil", title: "Apple Pencil", symbol: "pencil.tip", settings: settings, headline: pencil.isOn ? "A mouse" : "Off")
    }

    // MARK: Touch control look

    static func touchLookGroup(_ profile: BCDInputProfile) -> BCDGameSettingGroup {
        let look = profile.touchLook
        func choice<T: CaseIterable & RawRepresentable>(_ id: String, _ title: String, _ all: [T], _ current: T, _ name: (T) -> String,
                                                        note: String? = nil) -> BCDGameSetting where T.RawValue == String {
            BCDGameSetting(id: id, title: title, kind: .choice(all.map { BCDGameChoice(value: $0.rawValue, label: name($0)) }),
                           value: current.rawValue, defaultValue: "", note: note)
        }
        let settings = [
            choice("look-body", "Body colour", Array(BCDTouchLook.Body.allCases), look.body, { $0.title },
                   note: "The colour of the buttons and sticks drawn over the game. The same for every game; the in-game menu shows a sample."),
            choice("look-accent", "Colour when pressed", Array(BCDTouchLook.Accent.allCases), look.accent, { $0.title }),
            choice("look-press", "How a press shows", Array(BCDTouchLook.Press.allCases), look.press, { $0.title }),
            choice("look-icons", "Symbols", Array(BCDTouchLook.Icons.allCases), look.icons, { $0.title },
                   note: "On the face buttons and shoulders of a controller layout: A B X Y and LB RB LT RT, or the PlayStation symbols and L1 R1 L2 R2. What the game receives does not change."),
        ]
        return BCDGameSettingGroup(id: "touchlook", title: "Touch control look", symbol: "paintpalette", settings: settings, headline: look.summary)
    }

    // MARK: Turbo and macros

    static func turboGroup(_ profile: BCDInputProfile) -> BCDGameSettingGroup {
        let turbo = profile.turbo, macros = profile.macros
        var settings: [BCDGameSetting] = []
        let turboNote = "While you hold the button, the game sees it pressed and let go again and again. It works on the controller the game sees: the on-screen one or a paired one. Off until you turn it on."
        for (i, input) in BCDPadInput.main.enumerated() {
            settings.append(BCDGameSetting(id: BCDInputLines.turboToggle + input.rawValue, title: "Turbo " + input.title, kind: .toggle,
                                           value: turbo.pad.contains(input) ? "1" : "0", defaultValue: "0",
                                           note: i == 0 ? turboNote : nil, heading: i == 0 ? "Controller buttons" : nil))
        }
        settings.append(BCDGameSetting(id: BCDInputLines.turboToggle + BCDTurboSettings.mouseLeftName, title: "Turbo left click", kind: .toggle,
                                       value: turbo.mouseLeft ? "1" : "0", defaultValue: "0",
                                       note: "For the touch controls: a mouse button or a key you hold on a touch control repeats. The keys are named, like space, e, f.",
                                       heading: "Touch controls' mouse and keys"))
        settings.append(BCDGameSetting(id: BCDInputLines.turboToggle + BCDTurboSettings.mouseRightName, title: "Turbo right click", kind: .toggle,
                                       value: turbo.mouseRight ? "1" : "0", defaultValue: "0"))
        settings.append(BCDGameSetting(id: "turbo-keys", title: "Turbo keys", kind: .text(prompt: "space, e, f"),
                                       value: BCDKeyNames.listText(turbo.keys), defaultValue: "",
                                       note: "Up to eight keys, separated by commas: letters, digits, space, enter, tab, shift, ctrl, alt, the arrows (up, down, left, right), f1 to f12."))
        settings.append(BCDGameSetting(id: "turbo-rate", title: "Presses a second",
                                       kind: .choice(BCDTurboSettings.rateChoices.map { BCDGameChoice(value: String($0), label: String($0)) }),
                                       value: String(turbo.rate), defaultValue: String(BCDTurboSettings.defaultRate)))

        settings.append(macroHeading(profile))
        var shown = 0
        for n in 1...BCDMacroSettings.slots {
            let button = BCDPadInput.named(profile.get(BCDInputLines.turbo, "macro\(n)-button") ?? "")
            // A slot appears once the one before it has a button.
            if n > 1 && shown < n - 1 { break }
            shown = button == nil ? shown : n
            settings.append(BCDGameSetting(id: "macro\(n)-button", title: "Macro \(n) button",
                                           kind: .choice([BCDGameChoice(value: "", label: "Off")] + BCDPadInput.main.map { BCDGameChoice(value: $0.rawValue, label: $0.title) }),
                                           value: button?.rawValue ?? "", defaultValue: ""))
            guard button != nil else { continue }
            let mode = profile.get(BCDInputLines.turbo, "macro\(n)-mode") ?? ""
            settings.append(BCDGameSetting(id: "macro\(n)-mode", title: "Macro \(n) plays",
                                           kind: .choice([BCDGameChoice(value: "", label: "Once for each press"),
                                                          BCDGameChoice(value: "repeat", label: "Again and again while held"),
                                                          BCDGameChoice(value: "hold", label: "Held down until pressed again")]),
                                           value: BCDMacro.Mode(rawValue: mode)?.rawValue ?? "", defaultValue: ""))
            settings.append(BCDGameSetting(id: "macro\(n)-steps", title: "Macro \(n) steps", kind: .text(prompt: "a, a, lb+a, -, b"),
                                           value: profile.get(BCDInputLines.turbo, "macro\(n)-steps") ?? "", defaultValue: "",
                                           note: n == 1 ? "Presses in order, separated by commas: a, b, x, y, lb, rb, lt, rt, l3, r3, up, down, left, right, start, back. Join buttons that go together with +, like lb+a; a dash is a pause. Up to 12 steps." : nil))
        }
        settings.append(BCDGameSetting(id: "macro-step", title: "Step time",
                                       kind: .choice(BCDMacroSettings.stepChoices.map { BCDGameChoice(value: String($0), label: "\($0) ms") }),
                                       value: String(macros.stepMillis), defaultValue: String(BCDMacroSettings.defaultStep),
                                       note: "How long each step of a macro is held, and the pause after it."))
        return BCDGameSettingGroup(id: "turbo", title: "Turbo and macros", symbol: "bolt.fill", settings: settings,
                                   headline: turboHeadline(turbo, macros))
    }

    private static func macroHeading(_ profile: BCDInputProfile) -> BCDGameSetting {
        BCDGameSetting(id: "macro-info", title: "About macros",
                       kind: .info, value: "A button of the controller plays a chain of presses instead of itself. The button is not sent to the game while its macro is set.",
                       heading: "Macros")
    }

    static func turboHeadline(_ turbo: BCDTurboSettings, _ macros: BCDMacroSettings) -> String {
        var parts: [String] = []
        let names = turbo.pad.map(\.title) + (turbo.mouseLeft ? ["left click"] : []) + (turbo.mouseRight ? ["right click"] : []) + turbo.keys.compactMap(BCDKeyNames.label)
        if !names.isEmpty { parts.append("Turbo " + names.prefix(3).joined(separator: ", ") + (names.count > 3 ? " +\(names.count - 3)" : "")) }
        if macros.isOn { parts.append("\(macros.macros.count) macro" + (macros.macros.count == 1 ? "" : "s")) }
        return parts.isEmpty ? "Off" : parts.joined(separator: " · ")
    }
}
