// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Host test for BCDGOGRegistry and the install script's files in BCDGOGContent (tests/host/check-gog-script.py). Synthetic data only.
import Foundation

var failures = 0
func check(_ ok: Bool, _ label: String) { print((ok ? "PASS: " : "FAIL: ") + label); if !ok { failures += 1 } }

typealias Value = GOGInstallScript.Value

func value(_ root: Value.Root, _ key: String, _ name: String, _ data: Value.Content, follows: Bool = false) -> Value {
    Value(root: root, key: key, name: name, data: data, followsInstall: follows)
}

// ---- the lines ---------------------------------------------------------------------------------
let installFolder = value(.machine, "Software\\CD Projekt Red\\The Witcher", "InstallFolder", .string("C:\\GOG Games\\The Witcher"), follows: true)
check(BCDGOGRegistry.line(installFolder) == "\"InstallFolder\"=\"C:\\\\GOG Games\\\\The Witcher\"", "a string: name quoted, backslashes doubled")
check(BCDGOGRegistry.line(value(.machine, "Software\\X", "Flag", .dword(1))) == "\"Flag\"=dword:00000001", "a dword: eight hex digits")
check(BCDGOGRegistry.line(value(.machine, "Software\\X", "", .string("hello"))) == "@=\"hello\"", "the default value is @")
check(BCDGOGRegistry.line(value(.user, "Software\\X", "N\"q", .string("a\"b\u{00e9}"))) == "\"N\\\"q\"=\"a\\\"b\\x00e9\"", "quotes escaped, non-ASCII as \\x and four digits")
check(BCDGOGRegistry.views(installFolder) == ["Software\\CD Projekt Red\\The Witcher", "Software\\Wow6432Node\\CD Projekt Red\\The Witcher"],
      "HKLM\\Software goes into both registry views")
check(BCDGOGRegistry.views(value(.user, "Software\\Y", "n", .dword(2))) == ["Software\\Y"], "HKCU has one view")
check(BCDGOGRegistry.views(value(.machine, "Software\\Wow6432Node\\Y", "n", .dword(2))) == ["Software\\Wow6432Node\\Y"], "a Wow6432Node key stays itself")

// ---- the text ----------------------------------------------------------------------------------
let base = "WINE REGISTRY Version 2\n;; All keys relative to \\\\Machine\n\n#arch=win64\n\n[Software\\\\Existing] 1700000000\n\"a\"=\"1\"\n\n"
var first = BCDGOGRegistry.write([installFolder, value(.machine, "Software\\CD Projekt Red\\The Witcher", "IsDjinniInstalled", .dword(1))], in: base, now: 1_800_000_000)
check(first.outcome.written == 4 && first.outcome.present == 0 && first.outcome.followed == 0, "two values, two views each: four lines written")
check(first.text.contains("[Software\\\\CD Projekt Red\\\\The Witcher] 1800000000\n\"InstallFolder\"=\"C:\\\\GOG Games\\\\The Witcher\"\n\"IsDjinniInstalled\"=dword:00000001")
      && first.text.contains("[Software\\\\Wow6432Node\\\\CD Projekt Red\\\\The Witcher] 1800000000"), "a missing key gets a section with the values, in both views")
check(first.text.hasPrefix(base.trimmingCharacters(in: .newlines)), "the file's own text is kept in front")
let second = BCDGOGRegistry.write([installFolder, value(.machine, "Software\\CD Projekt Red\\The Witcher", "IsDjinniInstalled", .dword(1))], in: first.text, now: 1_800_000_001)
check(second.outcome.written == 0 && second.outcome.present == 4 && second.text == first.text, "a second run writes nothing: every value is there")

// A key that exists gets the value inside its section, not a new one.
let withKey = base + "[Software\\\\CD Projekt Red\\\\The Witcher] 1700000000\n\"Language\"=\"3\"\n\n[Software\\\\Other] 1700000000\n\"z\"=\"1\"\n"
let into = BCDGOGRegistry.write([installFolder.with(follows: false)], in: withKey, now: 5)
check(into.text.contains("[Software\\\\CD Projekt Red\\\\The Witcher] 1700000000\n\"Language\"=\"3\"\n\"InstallFolder\"=\"C:\\\\GOG Games\\\\The Witcher\"\n\n[Software\\\\Other]"),
      "a value added to an existing key goes inside its section")

// A value that is there stays, unless it follows the folder and differs.
let moved = value(.machine, "Software\\CD Projekt Red\\The Witcher", "InstallFolder", .string("C:\\GOG Games\\The Witcher (GOG)"), follows: true)
let stale = BCDGOGRegistry.write([moved], in: first.text, now: 9)
check(stale.outcome.followed == 2 && stale.outcome.written == 2 && stale.text.contains("\"InstallFolder\"=\"C:\\\\GOG Games\\\\The Witcher (GOG)\"")
      && !stale.text.contains("\"InstallFolder\"=\"C:\\\\GOG Games\\\\The Witcher\""), "a string that follows the folder is rewritten in both views when the folder moved")
let kept = BCDGOGRegistry.write([value(.machine, "Software\\CD Projekt Red\\The Witcher", "InstallFolder", .string("D:\\elsewhere"), follows: false),
                                  value(.machine, "Software\\CD Projekt Red\\The Witcher", "IsDjinniInstalled", .dword(0))], in: first.text, now: 9)
check(kept.text == first.text && kept.outcome.written == 0 && kept.outcome.present == 4, "a value the prefix has stays as it is (a game or its launcher may have changed it)")
let user = BCDGOGRegistry.write([value(.user, "Software\\CD Projekt Red\\The Witcher", "Exe", .string("C:\\x"))], in: base, now: 7)
check(user.outcome.written == 1 && !user.text.contains("Wow6432Node"), "an HKCU value has one view")
check(BCDGOGRegistry.write([], in: base, now: 1).text == base, "no values: the text is returned as it came")
let defaultValue = BCDGOGRegistry.write([value(.user, "Software\\Z", "", .string("d"))], in: base, now: 7)
check(defaultValue.text.contains("[Software\\\\Z] 7\n@=\"d\"") && BCDGOGRegistry.write([value(.user, "Software\\Z", "", .string("e"))], in: defaultValue.text, now: 8).outcome.present == 1,
      "a default value is written as @ and recognised as there")
// A section whose name only starts like the key's is not the key.
let near = base + "[Software\\\\CD Projekt Red\\\\The Witcher\\\\Sub] 1\n\"InstallFolder\"=\"sub\"\n"
check(BCDGOGRegistry.write([moved], in: near, now: 3).outcome.present == 0, "a sub-key's section is not the key's section")

// ---- the files ---------------------------------------------------------------------------------
let fm = FileManager.default
let work = fm.temporaryDirectory.appendingPathComponent("gog-script-bcd-\(getpid())")
try? fm.createDirectory(at: work, withIntermediateDirectories: true)
defer { try? fm.removeItem(at: work) }
let prefix = work.appendingPathComponent("prefix")
try? fm.createDirectory(at: prefix, withIntermediateDirectories: true)
try! base.write(to: prefix.appendingPathComponent("system.reg"), atomically: true, encoding: .utf8)
try! base.write(to: prefix.appendingPathComponent("user.reg"), atomically: true, encoding: .utf8)
let both = [installFolder, value(.user, "Software\\CD Projekt Red\\The Witcher", "Exe", .string("C:\\x"))]
let applied = try! BCDGOGRegistry.apply(both, prefix: prefix, now: Date(timeIntervalSince1970: 1_800_000_000))
let systemText = try! String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)
let userText = try! String(contentsOf: prefix.appendingPathComponent("user.reg"), encoding: .utf8)
check(applied.written == 3 && systemText.contains("InstallFolder") && userText.contains("\"Exe\"") && !userText.contains("InstallFolder"), "apply: HKLM into system.reg, HKCU into user.reg")
check((try? String(contentsOf: prefix.appendingPathComponent("system.reg.bcd-bak"), encoding: .utf8)) == base, "apply: the first-time backup is the file as it was")
let again = try! BCDGOGRegistry.apply(both, prefix: prefix)
check(again.written == 0 && again.present == 3, "apply twice: nothing more is written")
try! "x".write(to: prefix.appendingPathComponent("system.reg.bcd-bak"), atomically: true, encoding: .utf8)
let moveTo = try! BCDGOGRegistry.apply([moved], prefix: prefix)
check(moveTo.followed == 2 && (try? String(contentsOf: prefix.appendingPathComponent("system.reg.bcd-bak"), encoding: .utf8)) == "x", "apply: a later change keeps the first backup")
let empty = work.appendingPathComponent("empty")
try? fm.createDirectory(at: empty, withIntermediateDirectories: true)
check(try! BCDGOGRegistry.apply(both, prefix: empty).missingFile, "a prefix with no registry file yet: reported, nothing created")
check(!fm.fileExists(atPath: empty.appendingPathComponent("system.reg").path), "no registry file is made")
let other = work.appendingPathComponent("other")
try? fm.createDirectory(at: other, withIntermediateDirectories: true)
try! "not wine".write(to: other.appendingPathComponent("system.reg"), atomically: true, encoding: .utf8)
check(try! BCDGOGRegistry.apply([installFolder], prefix: other).notWine && (try? String(contentsOf: other.appendingPathComponent("system.reg"), encoding: .utf8)) == "not wine",
      "a file that is not Wine's registry is left as it is")

// ---- the scripts in a game's folder ------------------------------------------------------------
let game = work.appendingPathComponent("game")
try? fm.createDirectory(at: game, withIntermediateDirectories: true)
let realWitcher = #"{ "actions": [ { "install": { "action": "Execute", "arguments": { "argument": "", "executable": "galaxy_the_witcher_enhanced_edition_2.1.0.15.exe", "runAsAdmin": true, "workingDir": "" } }, "languages": ["*"], "name": "Install the game" } ], "productId": "1207658924" }"#
try! realWitcher.write(to: game.appendingPathComponent("goggame-1207658924.script"), atomically: true, encoding: .utf8)
let setReg = #"{ "productId": "2", "actions": [ { "name": "a", "install": { "action": "setRegistry", "arguments": { "root": "HKLM", "subkey": "Software\\Acme", "valueName": "Path", "valueData": "{app}", "valueType": "string" } } }, { "name": "b", "install": { "action": "setIni", "arguments": {} } }, { "name": "c", "install": { "action": "savePath", "arguments": {} } } ] }"#
try! setReg.write(to: game.appendingPathComponent("GOGGAME-2.SCRIPT"), atomically: true, encoding: .utf8)
try! "not json".write(to: game.appendingPathComponent("goggame-3.script"), atomically: true, encoding: .utf8)
try! "{}".write(to: game.appendingPathComponent("goggame-3.info"), atomically: true, encoding: .utf8)
let prepared = BCDGOGRegistry.prepared(folder: game, windowsFolder: "C:\\GOG Games\\Game", language: "en-US")
check(prepared.scripts == 2 && prepared.unreadable == 1, "two scripts read, the one that is not JSON counted (a script is found by name, any letter case)")
check(prepared.values.count == 5 && prepared.values.contains { $0.name == "InstallFolder" && $0.data == .string("C:\\GOG Games\\Game") }
      && prepared.values.contains { $0.name == "Path" && $0.data == .string("C:\\GOG Games\\Game") }, "the Witcher's known values and the other script's setRegistry value")
check(prepared.leftOut == ["setini", "savepath"], "the actions left out are named by kind only: \(prepared.leftOut)")
let line = BCDGOGRegistry.logLine(product: "2", prepared: prepared, outcome: BCDGOGRegistry.Outcome(written: 10, followed: 1, present: 2))
check(line == "[gog-script] product=2 scripts=2 unreadable=1 values=5 written=10 followed=1 present=2 left-out=setini,savepath", "the log line: counts and words: \(line)")
check(!line.contains("Software") && !line.contains("GOG Games") && !line.contains("Witcher"), "the log line holds no key, value or path")
check(BCDGOGRegistry.prepared(folder: work.appendingPathComponent("none"), windowsFolder: "C:\\x", language: "en-US") == BCDGOGRegistry.Prepared(), "a folder without a script: nothing")

// ---- the install record -----------------------------------------------------------------------
let record = BCDGOGInstallRecord(productID: "1", title: "T", buildID: "1", versionName: "1", language: "en-US", root: BCDInstallRoot(drive: "C", folder: "GOG Games"),
                                 folder: "T", bytes: 1, depots: [], dependencies: [], supportFiles: 0, play: nil, entryID: nil,
                                 installed: Date(timeIntervalSince1970: 0))
let recordJSON = String(decoding: try! JSONEncoder().encode(record), as: UTF8.self)
check(record.scripts == nil && !recordJSON.contains("scripts"), "a record from before has no script mark and writes none")
var oldJSON = try! JSONSerialization.jsonObject(with: try! JSONEncoder().encode(record)) as! [String: Any]
oldJSON.removeValue(forKey: "scripts")
check((try? JSONDecoder().decode(BCDGOGInstallRecord.self, from: JSONSerialization.data(withJSONObject: oldJSON)))?.scripts == nil, "a record file from before decodes, with no mark")
var marked = record
marked.scripts = true
check((try? JSONDecoder().decode(BCDGOGInstallRecord.self, from: JSONEncoder().encode(marked)))?.scripts == true, "the mark survives the file")

// ---- when a start writes -----------------------------------------------------------------------
check(BCDGOGRegistry.applies(scripts: true, switch: nil) && !BCDGOGRegistry.applies(scripts: nil, switch: nil) && !BCDGOGRegistry.applies(scripts: false, switch: nil),
      "no switch: only a game BCD installed with the script in mind writes (a game from before starts as it always did)")
check(BCDGOGRegistry.applies(scripts: nil, switch: true) && BCDGOGRegistry.applies(scripts: false, switch: true), "the switch on: a game from before writes too")
check(!BCDGOGRegistry.applies(scripts: true, switch: false), "the switch off: nothing is written, whatever the install says")

// ---- the script and the support folder's app files in a plan -----------------------------------
func chunk(_ n: Int, size: Int64 = 100) -> [String: Any] {
    let hex = String(format: "%032x", n), packed = String(format: "%032x", n + 1000)
    return ["md5": hex, "size": size, "compressedMd5": packed, "compressedSize": size / 2]
}
let items: [[String: Any]] = [
    ["type": "DepotFile", "path": "Bin\\Game.exe", "chunks": [chunk(1)]],
    ["type": "DepotFile", "path": "support\\goggame-1207658924.script", "chunks": [chunk(2, size: 40)], "flags": ["support"]],
    ["type": "DepotFile", "path": "app\\dosbox_game.conf", "chunks": [chunk(3, size: 30)], "flags": ["support"]],
    ["type": "DepotFile", "path": "app\\Sub\\other.conf", "chunks": [chunk(4, size: 20)], "flags": ["support"]],
    ["type": "DepotFile", "path": "app\\bin\\game.exe", "chunks": [chunk(5, size: 20)], "flags": ["support"]],
    ["type": "DepotFile", "path": "setup_game.exe", "chunks": [chunk(6)], "flags": ["support", "executable"]],
    ["type": "DepotFile", "path": "app\\broken.conf", "chunks": [["md5": "nothex"]], "flags": ["support"]],
    ["type": "DepotFile", "path": "goggame-2.script", "chunks": [], "flags": ["support"]],
]
do {
    var builder = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1207658924"), title: "T", version: "1", folder: "Game")
    let added = try BCDGOGContent.add(depot: ["depot": ["items": items]], product: "1207658924", to: &builder, containers: false)
    check(added.files == 1 && added.support == 3 && added.scripts == 1 && added.appFiles == 3, "support files: the script and the app/ files kept; the setup program, an unreadable one and a script without chunks left out")
    check(!builder.hasFile("goggame-1207658924.script") && !builder.hasFile("dosbox_game.conf"), "kept support files are not in the plan until every depot is in")
    let result = BCDGOGContent.addSupportFiles(added.kept, to: &builder)
    check(result.scripts == 1 && result.app == 2 && result.skipped == 1, "added after the depots; app/bin/game.exe is the game's own Bin/Game.exe (any letter case) and stays out: \(result)")
    let plan = try builder.build()
    check(plan.files.map(\.path) == ["Bin/Game.exe", "goggame-1207658924.script", "dosbox_game.conf", "Sub/other.conf"]
          && plan.files[1].size == 40, "the plan: the script at the root, the app/ files below it: \(plan.files.map(\.path))")
    check(builder.hasFile("GOGGAME-1207658924.SCRIPT") && builder.hasFile("Bin\\GAME.EXE") && !builder.hasFile("nothing.txt") && !builder.hasFile("../x"), "hasFile ignores letter case and refuses unsafe paths")
    // supportData does not overwrite: the game's own file of that name wins.
    var depotItems = items
    depotItems.append(["type": "DepotFile", "path": "Dosbox_Game.conf", "chunks": [chunk(9)]])
    var third = try BCDContentPlanBuilder(key: .init(store: "gog", id: "1207658924"), title: "T", version: "1", folder: "Game")
    let withOwn = try BCDGOGContent.add(depot: ["depot": ["items": depotItems]], product: "1207658924", to: &third, containers: false)
    let r3 = BCDGOGContent.addSupportFiles(withOwn.kept, to: &third)
    check(r3.app == 1 && r3.skipped == 2, "a support app/ file never replaces the game's own file of the same name (any letter case): \(r3)")
    let p3 = try third.build()
    check(p3.files.contains { $0.path == "Dosbox_Game.conf" } && !p3.files.contains { $0.path == "dosbox_game.conf" }, "the game's own spelling stays")
} catch {
    check(false, "plan with support files: \(error)")
}
check(BCDGOGContent.supportKept("support\\goggame-5.script") == .script("goggame-5.script") && BCDGOGContent.supportKept("GOGGAME-5.SCRIPT") == .script("GOGGAME-5.SCRIPT")
      && BCDGOGContent.supportKept("App/a/b.conf") == .app("a/b.conf") && BCDGOGContent.supportKept("app") == nil && BCDGOGContent.supportKept("setup.exe") == nil
      && BCDGOGContent.supportKept("goggame-5.info") == nil, "supportKept: the script by name, app/ by its first folder, nothing else")

print(failures == 0 ? "ALL OK" : "\(failures) FAILED")
exit(failures == 0 ? 0 : 1)

extension GOGInstallScript.Value {
    func with(follows: Bool) -> GOGInstallScript.Value { var copy = self; copy.followsInstall = follows; return copy }
}
