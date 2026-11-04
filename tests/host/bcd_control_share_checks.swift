// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The shared control layout file's rules (app/Madeira/BCDControlShareCore.swift), compiled on the host by tests/host/check-bcd-control-share.py.

import Foundation

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

func control(_ nx: Double = 0.5, _ ny: Double = 0.5, scale: Double = 1, action: Any = ["pad": ["_0": "A"]], extra: [String: Any] = [:]) -> [String: Any] {
    var c: [String: Any] = ["id": "6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F", "nx": nx, "ny": ny, "scale": scale, "action": action]
    for (k, v) in extra { c[k] = v }
    return c
}

func file(_ controls: [Any], name: Any = "Keys", game: Any? = nil, version: Any = 1) -> Data {
    var root: [String: Any] = ["bcdControls": version, "name": name, "controls": controls]
    if let game { root["game"] = game }
    return try! JSONSerialization.data(withJSONObject: root)
}

func problem(_ data: Data) -> BCDControlShare.Problem? {
    if case .failure(let p) = BCDControlShare.parse(data) { return p }
    return nil
}

@main enum Run {
    static func main() {
        // ---- a good file -----------------------------------------------------------------------------------
        let good = file([control(0.1, 0.8), control(0.9, 0.7, scale: 1.5, action: "mouseLeft", extra: ["padBinding": "RT"])], name: "Hollow Knight keys", game: "Hollow Knight")
        guard case .success(let parsed) = BCDControlShare.parse(good) else { print("FAIL a good file did not parse"); exit(1) }
        check("a good file reads: its name, its game, the number of controls", parsed.name == "Hollow Knight keys" && parsed.game == "Hollow Knight" && parsed.count == 2)
        let list = (try! JSONSerialization.jsonObject(with: parsed.controlsJSON)) as! [[String: Any]]
        check("every control gets a fresh id (two imports never share a control's id)",
              list.count == 2 && list.allSatisfy { ($0["id"] as? String).flatMap(UUID.init) != nil && $0["id"] as? String != "6F1B5E2A-1C1D-4B7E-9C84-0A0B0C0D0E0F" }
              && (list[0]["id"] as! String) != (list[1]["id"] as! String))
        check("the rest of a control travels as it is (a field a later build adds too)", (list[1]["padBinding"] as? String) == "RT" && (list[1]["scale"] as? Double) == 1.5)

        // ---- the round trip through export ------------------------------------------------------------------
        let controlsJSON = try! JSONSerialization.data(withJSONObject: [control(0.2, 0.3), control(0.6, 0.4, action: ["key": ["_0": 87]])])
        let exported = try! BCDControlShare.export(name: "  My\nlayout\t ", game: "A Game", build: "604", controlsJSON: controlsJSON)
        guard case .success(let again) = BCDControlShare.parse(exported) else { print("FAIL the export did not parse"); exit(1) }
        check("an export reads back: the name made safe, the game, both controls", again.name == "My layout" && again.game == "A Game" && again.count == 2)
        check("the file says what it is", String(decoding: exported, as: UTF8.self).contains("\"bcdControls\" : 1") && String(decoding: exported, as: UTF8.self).contains("\"app\" : \"604\""))
        check("exporting something that is not a list of controls is refused", (try? BCDControlShare.export(name: "x", game: nil, build: "1", controlsJSON: Data("{}".utf8))) == nil)

        // ---- refusals ----------------------------------------------------------------------------------------
        check("not JSON", problem(Data("hello".utf8)) == .notJSON && problem(Data()) == .notJSON)
        check("JSON that is not an object", problem(Data("[1,2]".utf8)) == .notJSON)
        check("an object without the marker is not a layout", problem(Data("{\"controls\":[]}".utf8)) == .notALayoutFile)
        check("a newer format is said to need a newer BCD", problem(file([control()], version: 2)) == .newerVersion(2))
        check("format 0 is not a layout", problem(file([control()], version: 0)) == .notALayoutFile)
        check("no controls", problem(file([])) == .noControls)
        check("more than 120 controls", problem(file(Array(repeating: control(), count: 121))) == .tooMany(121) && problem(file(Array(repeating: control(), count: 120))) == nil)
        check("a file over 256 KB is refused before it is read as JSON", problem(Data(count: 256 * 1024 + 1)) == .tooBig && BCDControlShare.maxBytes == 256 * 1024)
        check("a control that is not an object", problem(file([5])) == .badControl(0, "not an object"))
        check("a position that is not a number, is a bool, or is out of range",
              problem(file([control(), ["id": "x", "nx": "0.5", "ny": 0.5, "scale": 1, "action": "mouseLeft"]])) == .badControl(1, "nx is not a number")
              && problem(file([["nx": true, "ny": 0.5, "scale": 1, "action": "a"]])) == .badControl(0, "nx is not a number")
              && problem(file([control(2.0, 0.5)])) == .badControl(0, "nx is out of range")
              && problem(file([control(0.5, -1)])) == .badControl(0, "ny is out of range"))
        check("a scale that is zero, negative, huge or missing", problem(file([control(scale: 0)])) == .badControl(0, "scale is out of range")
              && problem(file([control(scale: -2)])) == .badControl(0, "scale is out of range")
              && problem(file([control(scale: 50)])) == .badControl(0, "scale is out of range")
              && problem(file([["nx": 0.5, "ny": 0.5, "action": "a"]])) == .badControl(0, "scale is not a number"))
        check("a control with no action", problem(file([["nx": 0.5, "ny": 0.5, "scale": 1]])) == .badControl(0, "no action"))
        check("a long text", problem(file([control(extra: ["padBinding": String(repeating: "x", count: 65)])])) == .badControl(0, "a text is too long"))
        var deep: Any = "x"
        for _ in 0..<8 { deep = ["a": deep] }
        check("nesting too deep", problem(file([control(extra: ["more": deep])])) == .badControl(0, "nested too deep"))
        check("a list too long inside a control", problem(file([control(extra: ["l": Array(repeating: 1, count: 17)])])) == .badControl(0, "a list is too long"))
        check("a control with too many fields", problem(file([control(extra: Dictionary(uniqueKeysWithValues: (0..<30).map { ("k\($0)", 1) }))])) == .badControl(0, "too many fields"))
        check("every refusal says it in words", [BCDControlShare.Problem.tooBig, .notJSON, .notALayoutFile, .newerVersion(3), .noControls, .tooMany(200), .badControl(2, "x")].allSatisfy { !$0.words.isEmpty })

        // ---- a control's own look (2026-11-03: its shape and words, TouchControl.face) -----------------------------------------
        var lookTravels = false
        if case .success(let p) = BCDControlShare.parse(file([control(extra: ["face": ["shape": "rectangle", "aspect": 2, "text": "INV"]])])),
           let list = (try? JSONSerialization.jsonObject(with: p.controlsJSON)) as? [[String: Any]], let face = list.first?["face"] as? [String: Any] {
            lookTravels = face["shape"] as? String == "rectangle" && (face["aspect"] as? Double) == 2 && face["text"] as? String == "INV" && face.count == 3
        }
        check("a control's own look travels as it is", lookTravels)
        check("a look this build cannot read is passed on, not refused (the app's decoder leaves it out and the control loads)",
              problem(file([control(extra: ["face": ["shape": "hexagon", "aspect": "x", "text": 5]])])) == nil && problem(file([control(extra: ["face": 7])])) == nil)
        check("words longer than a shared file allows a text are refused, as any long text is",
              problem(file([control(extra: ["face": ["text": String(repeating: "W", count: 65)]])])) == .badControl(0, "a text is too long")
              && problem(file([control(extra: ["face": ["text": String(repeating: "W", count: 64)]])])) == nil)

        // ---- names ------------------------------------------------------------------------------------------
        check("a name is one line without control or format characters, at most 40 characters, never empty",
              BCDControlShare.cleanName("a\u{0}b\u{202E}c\nd") == "abc d" && BCDControlShare.cleanName(String(repeating: "x", count: 100)).count == 40
              && BCDControlShare.cleanName("   ") == "Shared layout" && BCDControlShare.cleanName(" ", fallback: "") == "")
        check("a file without a name is called Shared layout", { if case .success(let p) = BCDControlShare.parse(file([control()], name: 5)) { return p.name == "Shared layout" }; return false }())
        check("a game name is kept to 80 characters and an empty one is left out", { if case .success(let p) = BCDControlShare.parse(file([control()], game: String(repeating: "g", count: 200))) { return p.game?.count == 80 }; return false }()
              && { if case .success(let p) = BCDControlShare.parse(file([control()], game: "  ")) { return p.game == nil }; return false }())
        check("an imported name is its own when free, and 'Name (2)' when a layout has it, whatever the case",
              BCDControlShare.importName("Keys", taken: ["Xbox controller"]) == "Keys" && BCDControlShare.importName("Keys", taken: ["keys "]) == "Keys (2)"
              && BCDControlShare.importName("Keys", taken: ["Keys", "Keys (2)", "KEYS (3)"]) == "Keys (4)"
              && BCDControlShare.importName("Xbox controller", taken: ["Xbox controller", "PC keyboard"]) == "Xbox controller (2)")
        check("... and the suffix never pushes the name past 40 characters", BCDControlShare.importName(String(repeating: "n", count: 40), taken: [String(repeating: "n", count: 40)]).count <= 40)
        check("a file name is safe: no slash, no dots at the ends, the suffix is .bcd-controls.json",
              BCDControlShare.fileName(for: "../etc/passwd") == "---etc-passwd.bcd-controls.json" && BCDControlShare.fileName(for: "Keys 1") == "Keys 1.bcd-controls.json"
              && BCDControlShare.fileName(for: "...") .hasSuffix(".bcd-controls.json") && !BCDControlShare.fileName(for: "a/b").contains("/"))
        check("the success words say how many controls and that nothing of the player's changed",
              BCDControlShare.added(parsed, as: "Keys").contains("2 controls") && BCDControlShare.added(parsed, as: "Keys").contains("made for Hollow Knight")
              && BCDControlShare.added(parsed, as: "Keys").contains("your own layouts are unchanged"))
        print(failures == 0 ? "bcd_control_share_checks: PASS" : "bcd_control_share_checks: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
