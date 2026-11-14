// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The checks of tests/host/check-touch-floating.py, compiled after the pure blocks of ContentView.swift (ControlAction, TouchControl),
// TouchGamepad.swift (BCDFloatingStick) and TouchControlPresets.swift (the layouts).

var failures = 0
func check(_ what: String, _ ok: Bool) {
    print((ok ? "PASS: " : "FAIL: ") + what)
    if !ok { failures += 1 }
}

// ---- floating sticks
let frame = CGRect(x: 100, y: 200, width: 90, height: 90)
let zone = BCDFloatingStick.zone(of: frame)
check("the zone is the stick's frame grown by its width each side and 0.8 of its height above and below",
      zone == CGRect(x: 10, y: 128, width: 270, height: 234))
check("the zone holds the stick's own frame", zone.contains(frame))
let field = CGRect(x: 59, y: 0, width: 756, height: 381)
check("a thumb in the open: the ring is centred on it", BCDFloatingStick.centre(touch: CGPoint(x: 300, y: 200), diameter: 90, field: field) == CGPoint(x: 300, y: 200))
check("a thumb near the left edge: the ring moves in just far enough to be whole",
      BCDFloatingStick.centre(touch: CGPoint(x: 60, y: 370), diameter: 90, field: field) == CGPoint(x: 104, y: 336))
check("no field, or one smaller than the ring: the thumb's own point",
      BCDFloatingStick.centre(touch: CGPoint(x: 5, y: 5), diameter: 90, field: nil) == CGPoint(x: 5, y: 5)
      && BCDFloatingStick.centre(touch: CGPoint(x: 5, y: 5), diameter: 90, field: CGRect(x: 0, y: 0, width: 80, height: 400)) == CGPoint(x: 5, y: 5))

// ---- the layouts
let screens: [(String, ControlPresetScreen)] = [
    ("6.3in phone", ControlPresetScreen(width: 874, height: 402, left: 59, right: 59, top: 0, bottom: 21)),
    ("6.1in phone", ControlPresetScreen(width: 844, height: 390, left: 47, right: 47, top: 0, bottom: 21)),
    ("6.9in phone", ControlPresetScreen(width: 956, height: 440, left: 62, right: 62, top: 0, bottom: 21)),
    ("4.7in phone", ControlPresetScreen(width: 667, height: 375)),
    ("insets unreported", ControlPresetScreen(width: 874, height: 402)),
    ("11in tablet", ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20)),
    ("13in tablet", ControlPresetScreen(width: 1366, height: 1024, top: 24, bottom: 20)),
    ("mini tablet", ControlPresetScreen(width: 1133, height: 744, top: 24, bottom: 20)),
    ("phone, portrait", ControlPresetScreen(width: 402, height: 874, left: 0, right: 0, top: 62, bottom: 34)),
]
let wanted: [ControlPresetLayout.Arrangement: [String]] = [
    .leftHanded: ["LS", "RS", "D↑", "D↓", "D←", "D→", "A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu", "L3", "R3"],
    .middleSticks: ["LS", "RS", "D↑", "D↓", "D←", "D→", "A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu"],
    .handheld: ["LS", "RS", "D↑", "D↓", "D←", "D→", "A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu"],
    .retro: ["D↑", "D↓", "D←", "D→", "A", "B", "X", "Y", "LB", "RB", "View", "Menu"],
    .racing: ["LS", "A", "B", "X", "Y", "LB", "RB", "LT", "RT", "View", "Menu"],
    .small: ["LS", "A", "B", "X", "Y", "LB", "RB", "View", "Menu"],
]
for a in ControlPresetLayout.Arrangement.allCases {
    var complete = true, mapped = true, clamped = true, fits = true
    for (_, sc) in screens {
        let cs = ControlPresetLayout.arranged(a, for: sc)
        let names = cs.compactMap { $0.action.padName }
        if names.count != cs.count || Set(names) != Set(wanted[a]!) || names.count != wanted[a]!.count { complete = false }
        if !names.allSatisfy(TouchPadAction.supported) { mapped = false }
        if !cs.allSatisfy({ $0.scale >= 0.5 && $0.scale <= 3.0 && $0.nx >= 0.03 && $0.nx <= 0.97 && $0.ny >= 0.03 && $0.ny <= 0.97 }) { clamped = false }
        if !ControlPresetLayout.fits(cs, sc.landscape) { fits = false }
    }
    check("\(a.name): its controls once each, on all nine screens", complete)
    check("\(a.name): every control a supported controller input", mapped)
    check("\(a.name): sizes and places inside the editor's clamps", clamped)
    check("\(a.name): inside the safe area, nothing touching, the overlay's top buttons free", fits)
}
let phone = screens[0].1
let full = ControlPresetLayout.xbox(for: phone), left = ControlPresetLayout.arranged(.leftHanded, for: phone)
let m = ControlPresetLayout.margins(phone.landscape)
check("the left-handed layout is the controller layout turned round across the room between the margins",
      zip(full, left).allSatisfy { a, b in a.action == b.action && a.scale == b.scale && a.ny == b.ny
          && abs((a.nx + b.nx) * phone.width - (m.left + phone.width - m.right)) < 1e-6 })
let racing = ControlPresetLayout.arranged(.racing, for: phone)
let pedals = racing.filter { $0.action.padName == "LT" || $0.action.padName == "RT" }
check("the racing pad's triggers are upright pedals (a rectangle of the player's kind), the gas at the edge",
      pedals.count == 2 && pedals.allSatisfy { $0.face?.shape == "rectangle" && ($0.face?.aspect ?? 1) < 1 }
      && pedals.first { $0.action.padName == "RT" }!.nx > pedals.first { $0.action.padName == "LT" }!.nx)
check("the retro pad has no stick", !ControlPresetLayout.arranged(.retro, for: phone).contains { $0.action.padName == "LS" || $0.action.padName == "RS" })
let sticks = ControlPresetLayout.arranged(.middleSticks, for: phone)
let ls = sticks.first { $0.action.padName == "LS" }!, rs = sticks.first { $0.action.padName == "RS" }!
let dpad = sticks.first { $0.action.padName == "D←" }!, x = sticks.first { $0.action.padName == "X" }!
check("sticks in the middle: both sticks between the direction pad and the face buttons", dpad.nx < ls.nx && ls.nx < rs.nx && rs.nx < x.nx)

// ---- the list
let all = ControlPresetStore.builtIns
check("the two built-ins stay first, as they were", all.count == 8 && all[0].id == ControlPresetLayout.xboxID && all[0].name == "Xbox controller"
      && all[1].id == ControlPresetLayout.keyboardID && all[1].name == "PC keyboard")
check("then the six, with their ids and names", all.dropFirst(2).map(\.id) == ControlPresetLayout.Arrangement.allCases.map(\.id)
      && Set(all.map(\.id)).count == 8 && Set(all.map { $0.name.lowercased() }).count == 8)
check("each is a built-in: loaded, never saved over", ControlPresetLayout.Arrangement.allCases.allSatisfy { ControlPresetStore.isBuiltIn($0.id) }
      && { var s = ControlPresetStore(); return s.save(name: "Racing pad", controls: []) == .refusedBuiltIn }())
let tablet = ControlPresetScreen(width: 1194, height: 834, top: 24, bottom: 20)
let loaded = ControlPresetStore.layout(of: all[2 + ControlPresetLayout.Arrangement.allCases.firstIndex(of: .racing)!], screen: tablet)
check("loading one lays it out again for the screen it is loaded on",
      loaded.map(\.nx) == ControlPresetLayout.arranged(.racing, for: tablet).map(\.nx) && Set(loaded.map(\.id)).count == loaded.count)
check("every pad layout needs the touch controller; the PC keyboard does not",
      all.allSatisfy { ControlPresetLayout.needsTouchPad($0.id) == ($0.id != ControlPresetLayout.keyboardID) })
check("a user's layout named like none of them is untouched", ControlPresetStore(user: [ControlPreset(id: "u", name: "Mine", controls: [])]).named("Mine")?.id == "u")

print(failures == 0 ? "ALL PASS" : "\(failures) FAILED")
exit(failures == 0 ? 0 : 1)
