// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2026 Russell Barton: code taken from https://github.com/thebcd69/bcd-games (pull request #46 by rbarton124)
// under GPL-3.0-or-later with the Madeira Converter Exception and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// What a game sees as player 1's controller (round 604): an Xbox controller through XInput, as every game gets it today, or a
// PlayStation or generic gamepad as a HID device, which is what Sony's PC games and many DirectInput games look for. The choice
// is the game's own line `env.MADEIRA_PAD_MODE` (GamepadInput.configuredPadMode reads it when the game starts), so this file only
// names the choices in plain words, for the game page and the in-game menu. Plain values, Foundation only.
//
// Xbox 360 and Xbox One controllers cannot be told apart by a game that reads XInput: the API gives it buttons and sticks and no
// model, so there is one Xbox choice and it says so. The Xbox 360 choice is that same XInput pad plus the device Windows lists beside
// a wired 360 controller, for the games that look for an Xbox controller among the devices before they read XInput (Far Cry 4).

enum BCDPadModel: String, CaseIterable {
    /// The line's value for the model ("" for the Xbox controller: no line, the default).
    case xbox = "", xbox360 = "xbox360", dualSense = "dualsense", dualShock = "ds4", generic = "generic", controller = "hid"

    var title: String {
        switch self {
        case .xbox: return "Xbox controller (XInput)"
        case .xbox360: return "Xbox 360 controller (XInput + device)"
        case .dualSense: return "DualSense"
        case .dualShock: return "DualShock 4"
        case .generic: return "Generic gamepad (DirectInput)"
        case .controller: return "The controller itself"
        }
    }

    /// What the game sees, in a sentence.
    var detail: String {
        switch self {
        case .xbox:
            return "An Xbox pad, which almost every game knows. Xbox 360 and Xbox One controllers are the same to a game: it reads both through XInput, which names no model."
        case .xbox360:
            return "The same XInput pad, plus the device Windows lists for a wired Xbox 360 controller (Microsoft 045E:028E). For games that look for an Xbox controller among the devices before they read XInput, such as Far Cry 4; to other games it is the Xbox controller."
        case .dualSense:
            return "A PlayStation 5 controller (Sony 054C:0CE6, wired), as Sony's PC games and games that show PlayStation buttons look for. Rumble, the light bar and the adaptive triggers reach a DualSense. A game that reads only XInput then sees no controller."
        case .dualShock:
            return "A PlayStation 4 controller (Sony 054C:09CC, wired): sticks, buttons, triggers, the touchpad's click and motion, rumble and the light bar. A game that reads only XInput then sees no controller."
        case .generic:
            return "A plain HID gamepad that DirectInput games list (X, Y, Rx, Ry, a hat, eleven buttons). A game that reads only XInput then sees no controller."
        case .controller:
            return "A DualSense if the paired controller is one, else a plain HID gamepad."
        }
    }

    /// The value of `env.MADEIRA_PAD_MODE` in the game's file ("" removes the line).
    var line: String { rawValue }

    /// Whether the game sees player 1 as a HID device (XInput is left, except under the Xbox 360 choice).
    var isHID: Bool { self != .xbox }

    /// The model a line's value means; "xinput" is the Xbox controller; nil for a value this build does not know.
    static func named(_ text: String?) -> BCDPadModel? {
        let value = (text ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        if value == "xinput" { return .xbox }
        return BCDPadModel(rawValue: value)
    }

    /// Whether this build's HID controller can be a DualShock 4 (the wineserver's report builders, build/hidpad). It cannot yet, so no list
    /// offers it and GamepadInput does not accept "ds4": the choice and its words are here for the day it can.
    static let dualShockServed = false

    /// The choices a list shows, in order; the DualShock 4 only where this build serves one.
    static func choices(dualShock: Bool = BCDPadModel.dualShockServed) -> [BCDPadModel] {
        allCases.filter { dualShock || $0 != .dualShock }
    }

    /// The words under the choice on the game page.
    static let pageNote = "What player 1's controller is to the game. Nearly every game reads XInput and sees an Xbox pad (a 360 or an One is the same to it). "
        + "A DualSense shows Sony's PC games the controller they look for, with rumble, the light bar and the adaptive triggers; a generic gamepad is "
        + "what DirectInput games list. A game that reads only XInput sees no controller under the HID choices; the Xbox 360 controller is "
        + "for a game that shows no controller under the Xbox choice although it supports one. From the game's next start."
}
