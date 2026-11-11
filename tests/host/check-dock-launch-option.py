#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Game details > Steam > Steam launch option (BCD Dock start, 2026-10-24), on the host. No Steam, Wine or account.

A BCD Dock start hands Valve's client the key of one `config.launch` entry (MADEIRA_STEAM_HOST_LAUNCH_OPTION; the Dock host passes it to
LaunchApp). Until now BCD chose it alone: SteamDirectStart.choose's entry, else key 0. A tester asked to choose; the game page now lists the
game's launch entries and remembers the pick in LibraryEntry.steamLaunchOption. A game that never picked starts exactly as before.

What this checks:
  1. Compiled with the production SteamLaunchOption and SteamDirectStart (sliced out of SteamAppInfo.swift and SteamGames.swift), on a synthetic
     install folder:
     - without a choice (`chosen: nil`) `dockLaunch` returns the key and the one log line that ContentView.startDock produced before, over a
       range of launch configurations (a verbatim copy of the old two lines is the reference): no options, old caches, a default 64-bit entry,
       32-bit only, a missing program, a beta-branch entry, DLC-only, server/editor entries, a launcher started through a .bat;
     - with a choice that Steam still lists, that key and a line that says so; with one it no longer lists (or no options at all), the default
       and a line that says so; entries `choose` skips (beta branch, DLC, server, editor, other platform, missing or non-.exe program,
       missing working folder) are never listed and so can never be chosen;
     - Fallout 4 (Steam 377160): its launch configuration is ONE entry, Fallout4Launcher.exe (key 0, type none, osarch 64; the real product
       info), so the page shows no choice for it and its Dock start is unchanged - which is why its list uses env.MADEIRA_SPAWN_SWAP instead;
     - a launch cache written before `description` existed still decodes; the description is read, cleaned and bounded, and an oversized one
       does not drop the entry.
  2. Source: startDock asks SteamDirectStart.dockLaunch with the entry's choice and the old inline lines are gone; the only code that sets
     LibraryEntry.steamLaunchOption is the game page's own "launchoption" setting, shown for a Dock start only; the library file field is
     optional.
  3. The CI step exists.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


games = (app / 'SteamGames.swift').read_text()
info = (app / 'SwiftSteam/Library/SteamAppInfo.swift').read_text()
content = (app / 'ContentView.swift').read_text()
library = (app / 'Library.swift').read_text()
page = (app / 'BCDGamePageLive.swift').read_text()
workflow = (root / '.github/workflows/build-ipa.yml').read_text()

direct = games[games.index('enum SteamDirectStart {'):games.index('// MARK: - Model')]
launch_option = info[info.index("/// One entry of an app's launch configuration"):info.index("// MARK: - Download language")]   # the game language (SteamLanguage) is not part of this slice

harness = r'''
import Foundation

var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
func write(_ url: URL, _ text: String = "x") throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(text.utf8).write(to: url)
}

/// What ContentView.startDock did before this change, verbatim in effect (the reference for "a game that never chose starts as before").
func before(_ options: [SteamLaunchOption]?, _ root: URL) -> (UInt32, String) {
    let chosen = options.flatMap { SteamDirectStart.choose($0, installFolder: root)?.launchIndex }
    let launchOption = chosen ?? 0
    return (launchOption, "[madeira-dock] launch option \(launchOption)\(chosen == nil ? " (none chosen: the default)" : "")")
}

@main struct Checks {
    static func main() throws {
        typealias D = SteamDirectStart
        let fm = FileManager.default
        let install = fm.temporaryDirectory.appendingPathComponent("madeira-dock-option-" + UUID().uuidString)
        defer { try? fm.removeItem(at: install) }
        for file in ["bin64/game.exe", "bin32/game.exe", "Launcher.exe", "tools/editor.exe", "server.exe", "Start.bat", "dlc/dlc.exe", "beta.exe", "mac.app/x", "readme.txt"] {
            try write(install.appendingPathComponent(file))
        }
        try fm.createDirectory(at: install.appendingPathComponent("data"), withIntermediateDirectories: true)

        func opt(_ key: UInt32?, _ exe: String, type: String = "", os: String = "", arch: String = "", dlc: String? = nil, beta: String = "",
                 args: String = "", dir: String = "", text: String? = nil) -> SteamLaunchOption {
            SteamLaunchOption(executable: exe, index: key, requiredDLC: dlc, arguments: args, workingDir: dir, type: type, oslist: os,
                              osarch: arch, betaKey: beta, description: text)
        }
        let sets: [(String, [SteamLaunchOption]?)] = [
            ("no configuration at all", nil),
            ("an empty configuration", []),
            ("old cache: no keys", [opt(nil, "bin64/game.exe", type: "default", os: "windows", arch: "64")]),
            ("a 64-bit default entry", [opt(0, "bin32/game.exe", os: "windows", arch: "32"), opt(3, "bin64/game.exe", type: "default", os: "windows", arch: "64")]),
            ("32-bit only", [opt(2, "bin32/game.exe", os: "windows")]),
            ("the program is not on disk", [opt(0, "missing.exe", os: "windows")]),
            ("a beta-branch entry first", [opt(0, "beta.exe", beta: "beta"), opt(1, "Launcher.exe")]),
            ("DLC-only entry first", [opt(0, "dlc/dlc.exe", dlc: "12345"), opt(1, "Launcher.exe")]),
            ("server and editor entries", [opt(0, "server.exe", type: "server"), opt(1, "tools/editor.exe", type: "editor"), opt(2, "bin64/game.exe")]),
            ("a launcher started through a .bat", [opt(0, "Start.bat", type: "default")]),
            ("other platforms", [opt(0, "mac.app/x", os: "macos"), opt(5, "bin64/game.exe", os: "windows,linux")]),
            ("a working folder that does not exist", [opt(0, "bin64/game.exe", dir: "nothere"), opt(1, "Launcher.exe")]),
            ("keys with gaps", [opt(4, "Launcher.exe", type: "none"), opt(9, "bin64/game.exe", type: "option1")]),
        ]
        for (name, options) in sets {
            let reference = before(options, install)
            let now = D.dockLaunch(chosen: nil, options: options, installFolder: install)
            require(now.index == reference.0 && now.lines == [reference.1], "no choice, \(name): key \(now.index), the line as before")
        }

        // The real Fallout 4 configuration (Steam 377160, product info config.launch): one entry.
        let fallout = install.deletingLastPathComponent().appendingPathComponent("madeira-fo4-" + UUID().uuidString)
        defer { try? fm.removeItem(at: fallout) }
        try write(fallout.appendingPathComponent("Fallout4Launcher.exe"))
        try write(fallout.appendingPathComponent("Fallout4.exe"))
        let fo4 = SteamLaunchOption.parse(["0": ["description": "Launch", "executable": "Fallout4Launcher.exe", "type": "none", "config": ["osarch": "64"]] as [String: Any]])
        require(fo4.count == 1 && fo4[0].index == 0 && fo4[0].description == "Launch", "Fallout 4: one launch entry, key 0, described \"Launch\"")
        let fo4Listed = D.dockOptions(fo4, installFolder: fallout)
        require(fo4Listed.count == 1 && fo4Listed[0].program == "Fallout4Launcher.exe" && fo4Listed[0].label == "Launch: Fallout4Launcher.exe",
                "Fallout 4: the only listed entry is the launcher (the page shows no choice for a single entry): \(fo4Listed)")
        let fo4Start = D.dockLaunch(chosen: nil, options: fo4, installFolder: fallout)
        require(fo4Start.index == 0 && fo4Start.lines == ["[madeira-dock] launch option 0"], "Fallout 4: a Dock start asks key 0 as it always did: \(fo4Start)")

        // Listing: only what a Dock start can run.
        let mixed = [
            opt(0, "server.exe", type: "server"), opt(1, "tools/editor.exe", type: "editor"), opt(2, "beta.exe", beta: "beta"),
            opt(3, "dlc/dlc.exe", dlc: "999"), opt(4, "mac.app/x", os: "macos"), opt(5, "readme.txt"), opt(6, "gone.exe"),
            opt(7, "Launcher.exe", dir: "nothere"), opt(8, "bin32/game.exe", type: "none", os: "windows", arch: "32", text: "Play (32-bit)"),
            opt(9, "BIN64/Game.exe", type: "default", os: "windows", arch: "64", args: "-dx11 -skipintro", text: "Play"),
            opt(10, "Launcher.exe", dir: "data", text: "  Launcher\u{1}  "), opt(11, "Launcher.exe", dlc: "0"), opt(nil, "Launcher.exe"),
            opt(12, "../outside.exe"),
        ]
        let listed = D.dockOptions(mixed, installFolder: install)
        require(listed.map(\.index) == [8, 9, 10, 11], "listed: Windows entries of the public branch that run from the install folder, in Steam's order: \(listed.map(\.index))")
        require(listed[1].program == "bin64/game.exe", "the program is spelt as on disk: \(listed[1].program)")
        require(listed[0].label == "Play (32-bit): bin32/game.exe" && listed[1].label == "Play: bin64/game.exe -dx11 -skipintro" && listed[2].label == "Launcher: Launcher.exe",
                "labels: description, program and arguments; control characters out: \(listed.map(\.label))")
        require(listed[3].label == "Launcher.exe", "no description: the program alone")

        // A choice.
        let picked = D.dockLaunch(chosen: 8, options: mixed, installFolder: install)
        require(picked.index == 8 && picked.lines == ["[madeira-dock] launch option 8 (chosen on the game's page)"], "a listed choice is the key handed on: \(picked)")
        let defaultOfMixed = before(mixed, install)
        for stale in [0, 1, 2, 3, 4, 5, 6, 7, 12, 99, -1] {
            let r = D.dockLaunch(chosen: stale, options: mixed, installFolder: install)
            require(r.index == defaultOfMixed.0 && r.lines.count == 2 && r.lines[1] == defaultOfMixed.1 && r.lines[0].contains("is not in Steam's list"),
                    "choice \(stale) is not listed: the default key \(r.index) and a line that says so")
        }
        let noOptions = D.dockLaunch(chosen: 8, options: nil, installFolder: install)
        require(noOptions.index == 0 && noOptions.lines.count == 2 && noOptions.lines[1] == before(nil, install).1, "a choice with no launch configuration to check: the default (key 0)")
        require(D.dockLaunch(chosen: Int(UInt32.max) + 5, options: mixed, installFolder: install).index == defaultOfMixed.0, "a choice beyond a key's range: the default")

        // The cache and the parse.
        let oldCache = Data(#"[{"executable":"Launcher.exe","index":2,"requiredDLC":"","arguments":"","workingDir":"","type":"none","oslist":"windows","osarch":"64","betaKey":""}]"#.utf8)
        let decoded = try JSONDecoder().decode([SteamLaunchOption].self, from: oldCache)
        require(decoded.count == 1 && decoded[0].description == nil && decoded[0].index == 2, "a launch cache written before `description` existed still decodes")
        let again = try JSONDecoder().decode([SteamLaunchOption].self, from: JSONEncoder().encode(mixed))
        require(again == mixed, "the new field round-trips through the cache")
        let big = String(repeating: "x", count: 300)
        let parsed = SteamLaunchOption.parse([
            "0": ["executable": "a.exe", "description": big] as [String: Any],
            "1": ["executable": "b.exe", "description": "Fine"] as [String: Any],
            "2": ["executable": "c.exe"] as [String: Any],
        ])
        require(parsed.count == 3 && parsed[0].description == nil && parsed[1].description == "Fine" && parsed[2].description == nil,
                "parse: an oversized description is dropped, the entry stays (as before it existed)")
        require(D.dockOptions(parsed, installFolder: install).isEmpty, "entries whose programs are not installed list nothing")
        let longText = D.dockOptions([opt(1, "Launcher.exe", args: String(repeating: "a", count: 200), text: big)], installFolder: install)
        require(longText.count == 1 && longText[0].description.count <= 80 && longText[0].arguments.count <= 80, "description and arguments are bounded for the screen")

        // Round 623: a launch entry that is a script shows its first lines in the log (Messiah, MZYTWC). Text only: dockLaunch above is unchanged.
        let scripts = install.deletingLastPathComponent().appendingPathComponent("madeira-bat-" + UUID().uuidString)
        defer { try? fm.removeItem(at: scripts) }
        try write(scripts.appendingPathComponent("Launch Game.bat"), "@echo off\r\nif exist \"%~dp0Game.exe\" start \"\" \"%~dp0Game.exe\"\r\n\r\nexit /b 0\r\n\r\n")
        try write(scripts.appendingPathComponent("Sub/Run.CMD"), (1...60).map { "echo line \($0)" }.joined(separator: "\n"))
        try write(scripts.appendingPathComponent("tool.bat"), "echo a\u{1}b\tc " + String(repeating: "z", count: 300))
        try fm.createDirectory(at: scripts, withIntermediateDirectories: true)
        try Data([0x4d, 0x5a, 0x00, 0x01, 0x02]).write(to: scripts.appendingPathComponent("binary.bat"))
        try write(scripts.appendingPathComponent("game.exe"))
        try write(scripts.appendingPathComponent("empty.bat"), "")
        let batOptions = [opt(0, "launch game.BAT", type: "default", text: "Launch"), opt(1, "game.exe"), opt(2, "sub/run.cmd", args: "-x  -y"), opt(3, "tool.bat"),
                          opt(4, "binary.bat"), opt(5, "missing.bat"), opt(6, "../outside.bat"), opt(7, "empty.bat"), opt(nil, "Launch Game.bat")]
        let first = D.batchPreview(options: batOptions, key: 0, installFolder: scripts)
        require(first.count == 5 && first[0] == "[bat-start] launch option 0: Launch Game.bat (4 lines follow)"
                && first[1] == "[bat-start] 1: @echo off" && first[2] == "[bat-start] 2: if exist \"%~dp0Game.exe\" start \"\" \"%~dp0Game.exe\""
                && first[3] == "[bat-start] 3: " && first[4] == "[bat-start] 4: exit /b 0" && !first.joined().contains("\r"),
                "a .bat entry: its spelling on disk, CRLF read as lines, trailing blank lines dropped: \(first)")
        require(D.batchPreview(options: batOptions, key: 1, installFolder: scripts).isEmpty, "an .exe entry logs nothing")
        let sub = D.batchPreview(options: batOptions, key: 2, installFolder: scripts)
        require(sub.count == 41 && sub[0] == "[bat-start] launch option 2: Sub/Run.CMD arguments=\"-x  -y\" (60 lines, the first 40 follow)" && sub[40] == "[bat-start] 40: echo line 40",
                "a .cmd entry below a folder, found without regard to case: the first 40 of 60 lines and its arguments: \(sub.first ?? "") ... \(sub.last ?? "")")
        let odd = D.batchPreview(options: batOptions, key: 3, installFolder: scripts)
        require(odd.count == 2 && odd[1] == "[bat-start] 1: echo ab c " + String(repeating: "z", count: 160 - "echo ab c ".count), "control characters out, a tab is a space, a line is cut at 160: \(odd.map { $0.count })")
        require(D.batchPreview(options: batOptions, key: 4, installFolder: scripts) == ["[bat-start] launch option 4: binary.bat (not a text file, 5 bytes read)"], "a file with a zero byte is only named")
        require(D.batchPreview(options: batOptions, key: 5, installFolder: scripts).isEmpty, "a program that is not on disk logs nothing")
        // Night round 2026-11-11 (G5 S5; GeoGuessr PX27QG, Valve's launch error 45): the launch entry's fields in one log line, no path outside the install folder
        let entryOptions = [opt(1, "bin\\Game.exe", type: "option1", os: "windows", arch: "64", dlc: "", beta: "", args: String(repeating: "a", count: 100) + "\t-x"),
                            opt(2, "../outside.exe", dlc: "123", beta: "beta", dir: "x"), opt(3, "x.exe")]
        let entryLine = D.launchEntryLine(options: entryOptions, key: 1)
        require(entryLine == ["[madeira-dock] launch option 1 entry: program=bin/Game.exe arguments=\"" + String(repeating: "a", count: 80) + "\" oslist=windows osarch=64 type=option1 dlc=0 beta=0 workingDir=0"],
                "the launch entry's fields: program, arguments cut at 80, platform, type, and whether a DLC, beta key or working directory is set: \(entryLine)")
        require(D.launchEntryLine(options: entryOptions, key: 2) == ["[madeira-dock] launch option 2 entry: program=(outside the install folder) arguments=\"\" oslist= osarch= type= dlc=1 beta=1 workingDir=1"],
                "a program outside the install folder is not printed")
        require(D.launchEntryLine(options: entryOptions, key: 9).isEmpty && D.launchEntryLine(options: nil, key: 1).isEmpty, "an unknown key or no options: no line")
        require(D.batchPreview(options: batOptions, key: 6, installFolder: scripts).isEmpty, "a path that leaves the install folder logs nothing")
        require(D.batchPreview(options: batOptions, key: 7, installFolder: scripts) == ["[bat-start] launch option 7: empty.bat (0 lines follow)"], "an empty script is named with zero lines")
        require(D.batchPreview(options: batOptions, key: 99, installFolder: scripts).isEmpty && D.batchPreview(options: nil, key: 0, installFolder: scripts).isEmpty,
                "a key that is not in the list, and no launch configuration: nothing")
        let beforeBat = D.dockLaunch(chosen: nil, options: batOptions, installFolder: scripts)
        require(beforeBat.lines.count == 1 && beforeBat.lines[0].hasPrefix("[madeira-dock] launch option "), "dockLaunch still returns its one line for a .bat entry: \(beforeBat)")

        // The default path was not touched: choose() is what it was.
        require(D.choose(sets[3].1!, installFolder: install)?.launchIndex == 3, "choose still picks the 64-bit default entry")
        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: all Steam launch option checks")
    }
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-dock-option-') as tmp:
    tmp = Path(tmp)
    (tmp / 'rules.swift').write_text('import Foundation\n' + launch_option + '\n' + direct)
    (tmp / 'checks.swift').write_text(harness)
    exe = tmp / 'check'
    build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-sanitize=address', '-o', str(exe),
                            str(tmp / 'rules.swift'), str(tmp / 'checks.swift')], capture_output=True, text=True)
    check('the production SteamLaunchOption and SteamDirectStart compile on the host', build.returncode == 0)
    if build.returncode:
        sys.stdout.write(build.stderr[-5000:])
    else:
        run = subprocess.run([str(exe)], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'), capture_output=True, text=True)
        sys.stdout.write(run.stdout.replace('PASS: ', 'ok   ').replace('FAIL: ', 'FAIL '))
        if run.returncode:
            sys.stdout.write(run.stderr[-4000:])
        check('the launch option checks pass under AddressSanitizer', run.returncode == 0)

# ------------------------------------------------------------------ source
start = content[content.index('private func startDock('):]
start = start[:start.index('\n    }\n') + 6]
check('startDock hands Valve\'s client SteamDirectStart.dockLaunch(chosen: profile?.steamLaunchOption, ...) and logs its lines',
      'SteamDirectStart.dockLaunch(chosen: profile?.steamLaunchOption, options: options, installFolder: installFolder)' in start
      and 'let launchOption = dockLaunch.index' in start and 'for line in dockLaunch.lines { LogStore.shared.log(line) }' in start
      and 'MadeiraDock.configure(game, launchOption: launchOption)' in start)
check('startDock logs the .bat preview right after the launch option lines, with the key it hands on (round 623)',
      'for line in SteamDirectStart.batchPreview(options: options, key: launchOption, installFolder: installFolder) { LogStore.shared.log(line) }' in start
      and start.index('for line in dockLaunch.lines') < start.index('SteamDirectStart.batchPreview(') < start.index('await SteamOwnedLibrary.shared.prepareDock()'))
check('the old inline choice and its log line are gone from startDock',
      'SteamDirectStart.choose(' not in start and 'launch option \\(launchOption)' not in start)
check('the choice is read before the app\'s Steam connection closes, as the old lines were',
      start.index('SteamDirectStart.dockLaunch(') < start.index('await SteamOwnedLibrary.shared.prepareDock()'))
setters = []
for path in sorted(app.rglob('*.swift')):
    text = path.read_text()
    for m in re.finditer(r'steamLaunchOption', text):
        line = text[text.rfind('\n', 0, m.start()) + 1:text.find('\n', m.start())]
        if not line.lstrip().startswith('///') and not line.lstrip().startswith('//'):
            setters.append((path.name, line.strip()))
names = sorted({n for n, _ in setters})
check('steamLaunchOption appears only in Library.swift (the field), SteamGames.swift is free of it (the rule takes a plain Int), the page and startDock',
      names == ['BCDGamePageLive.swift', 'ContentView.swift', 'Library.swift'], str(setters))
check('the only assignment is the game page\'s "launchoption" setting',
      [l for n, l in setters if re.search(r'steamLaunchOption\s*=(?!=)', l)] == ['change(game) { $0.steamLaunchOption = key }'], str(setters))
check('the field is an optional Int, so older library files decode', 'var steamLaunchOption: Int?' in library)
dock_branch = page[page.index('    private func steamGroup('):]
dock_branch = dock_branch[:dock_branch.index('\n    }\n')]
check('the setting belongs to the Dock branch (not "The game\'s own program") and is shown for more than one entry, or to undo an earlier pick',
      dock_branch.index('if direct {') < dock_branch.index('} else {') < dock_branch.index('id: "launchoption"')
      and 'if listed.count > 1 || entry.steamLaunchOption != nil {' in dock_branch)
check('the list is read from the cached library only, off the main thread, when the page opens',
      'let launches = SteamOwnedLibrary.shared.game(appID)?.launches' in page and 'launchOptions(appID: appID)' in page[page.index('private func resolveProgram('):]
      and 'launchOptions(appID' not in page[page.index('private func readDockOptions('):page.index('/// The Direct3D version (or other API)')]
      and 'Task.detached(priority: .userInitiated) { SteamDirectStart.dockOptions(' in page)
check('the handler accepts the empty value (Steam\'s choice) or a non-negative key, and logs no path or name',
      'case "launchoption":' in page and 'guard value.isEmpty || (key != nil && key! >= 0), let id = appID(game) else { return }' in page
      and '[steam-start] app=\\(id) launch-option=\\(key == nil ? "default" : "choice")' in page)
check('SteamGames.swift stays free of program names', not re.findall(r'"[^"\n]*\.exe"', games))
check('CI runs this check', 'python3 tests/host/check-dock-launch-option.py' in workflow)

print('check-dock-launch-option: ' + ('PASS' if ok else 'FAILED'))
sys.exit(0 if ok else 1)
