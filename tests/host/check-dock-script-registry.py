#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1: a Steam install script's "Registry" values before a Dock start (round 591).

Fallout: New Vegas through BCD Dock (GA6QN2): the launcher says "does not appear to be installed and the installer could
not be found" because HKLM\\Software\\(Wow6432Node\\)Bethesda Softworks\\FalloutNV\\Installed Path, which the "Registry"
section of the game's installscript.vdf has Valve's Windows client write, is not there; a Dock start runs Valve's client in
the host and never the script, and DockInstallers runs only its "Run Process" entries.

Synthetic data only. Compiles production Swift (app/Madeira/DockInstallers.swift, DockReset.swift, DockGame from
MadeiraDock.swift) with the stubs check-dock-installers.py uses, under AddressSanitizer, and checks:
  * the script reader: values straight under a key and under "string" / "dword", a language section (the installed
    language's text, else English), %INSTALLDIR% / %LANGUAGE%, a value with another % left out, a type this does not know,
    keys outside Software\\ and hives other than HKLM / HKCU left out, repeated values once, a cap;
  * the registry text: a missing value goes into its key's section, a missing key gets a section, a value that is there
    (any case, any content) stays, a second run writes nothing, Wine's escapes for quotes, backslashes and non-ASCII;
  * prepare() in a synthetic prefix: off (unset, or the game's own line 0) touches no file and logs nothing; on (the game's
    own line, which wins over the environment; or the environment) writes both registry views and HKCU once, logs one
    line per value and a summary, keeps a first-time backup, skips a prefix with no registry file or a file that is not
    Wine's; reads the game's own folder only; DockInstallers.prepare calls it (also with the installers switched off);
  * the default scripts(folder:) is unchanged: it lists "Run Process" scripts only.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


dock = (app / 'MadeiraDock.swift').read_text(encoding='utf-8')
installers = (app / 'DockInstallers.swift').read_text(encoding='utf-8')
content = (app / 'ContentView.swift').read_text(encoding='utf-8')

# ------------------------------------------------------------------ static rules
prep = installers[installers.index('    static func prepare(_ game: DockGame, drive: URL, prefix: URL, others: [DockGame] = []'):]
prep = prep[:prep.index('\n    }\n')]
require(prep.index('absorbResults(') < prep.index('DockScriptRegistry.prepare(game, drive: drive, prefix: prefix)') < prep.index('guard enabled else'),
        'DockInstallers.prepare writes the registry values right before it looks at the installers switch (so also with installers off)')
require(installers.count('SteamSignIn.flag("MADEIRA_DOCK_SCRIPT_REGISTRY", default: false)') == 1,
        'the environment / bcd.cfg switch is read once, off by default')
info_text = (app / 'SwiftSteam/Library/SteamAppInfo.swift').read_text(encoding='utf-8')
steam_languages = set(re.findall(r'\("([a-z]+)", "[^"]+"\)', info_text[info_text.index('static let all: [(code: String, name: String)]'):info_text.index('static func name(')]))
reader_languages = set(re.findall(r'"([a-z]+)"', installers[installers.index('private static let languageNames: Set<String> = ['):][:900].split(']')[0]))
require(steam_languages and steam_languages == reader_languages,
        'round 632: the install-script reader knows exactly the Steam language names of SteamLanguage.all (%d)' % len(steam_languages))
require('reg.exe' not in installers, 'nothing runs reg.exe (it is not in the bundle); the files are rewritten while no session runs')
require(content.index('DockInstallers.prepare(game, drive: MadeiraDock.drive') < content.index('runWineFullSequence(profile: profile)', content.index('private func startDock(')),
        'a Dock start plans its installs (and so writes the values) before the session begins')
reg = installers[installers.index('enum DockScriptRegistry {'):]
require('wine_session' not in reg and 'wineserver' not in reg.lower().replace('the wineserver keeps', ''), 'the writer touches no running session')
require(re.search(r'static func scripts\(folder: URL, depth: Int = 1, containing needle: String = "run process"\)', installers) is not None,
        "SteamInstallScripts.scripts looks for 'run process' unless asked otherwise")
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
require(re.search(r'"env\.MADEIRA_DOCK_SCRIPT_REGISTRY": \{[^}]*"kind": "bool", "default": "0"', gen, re.S) is not None,
        'the settings catalog documents env.MADEIRA_DOCK_SCRIPT_REGISTRY, off by default')
rules = (root / 'build/tools/autotune_rules.py').read_text()
block = rules[rules.index('"id": "install-script-registry-missing"'):] if '"id": "install-script-registry-missing"' in rules else ''
block = block[:block.index('\n    },\n')] if block else ''
require('"lines": ["env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1"]' in block and '"session": "dock"' in block
        and 'does not appear to be installed and the installer could not be found' in block and '[msgbox-origin] box' in block
        and 'none": [s("[dock-script-registry] app=")]' in block, 'auto-tune: rule install-script-registry-missing proposes the switch for the launcher\'s box')
require((root / 'tests/fixtures/autotune/fallout-new-vegas-install-script-registry-2026-10-22.log').exists()
        and (root / 'tests/fixtures/autotune/fallout-new-vegas-install-script-registry-2026-10-22.expect').exists(), '  with the log of the game that showed it')

# ------------------------------------------------------------------ compiled Swift
game_src = dock[dock.index('/// A game Steam\'s client has installed'):dock.index('/// Madeira Dock: a small headless host')]
stubs = r'''
import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif
enum SteamSignIn {
    static func flag(_ name: String, default fallback: Bool) -> Bool { getenv(name).map { String(cString: $0) != "0" } ?? fallback }
}
enum MadeiraConfig {
    nonisolated(unsafe) static var values: [String: String] = [:]
    static func get(_ key: String) -> String? { values[key] }
    static var gameURL: URL? { nil }
}
struct LogEntry { enum Level { case info, success, error, debug } }
@MainActor final class LogStore {
    static let shared = LogStore()
    var lines: [String] = []
    func log(_ message: String, level: LogEntry.Level = .info) { lines.append(message) }
}
enum MadeiraDock { static let executable = "C:\\windows\\system32\\dockhost.exe" }
enum WineMonoModel { static var available: Bool { false } }
''' + game_src

checks = r'''
import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
func write(_ url: URL, _ text: String) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(text.utf8).write(to: url)
}
func read(_ url: URL) -> String { (try? String(contentsOf: url, encoding: .utf8)) ?? "" }
@MainActor func registryLines() -> [String] { LogStore.shared.lines.filter { $0.contains("[dock-script-registry]") } }

let dir = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fallout New Vegas"

// A script as the Bethesda games' have it, with the other shapes this reader tolerates.
let script = """
"InstallScript"
{
    "Registry"
    {
        "HKEY_LOCAL_MACHINE\\\\Software\\\\Bethesda Softworks\\\\FalloutNV"
        {
            "string"
            {
                "Installed Path"    "%INSTALLDIR%"
            }
        }
        "HKEY_LOCAL_MACHINE\\\\Software\\\\Wow6432Node\\\\Bethesda Softworks\\\\FalloutNV"
        {
            "string" { "Installed Path" "%installdir%" }
        }
        "HKEY_LOCAL_MACHINE\\\\Software\\\\Fixture Vendor"
        {
            "string"
            {
                "Language" { "english" "English" "german" "Deutsch" }
                "Version" "1.0"
                "Bad" "%WINDIR%\\\\x"
                "Lang2" "%LANGUAGE%"
                "Quote" "say \\"hi\\" \\\\ caf\u{e9}"
            }
            "dword" { "Installed" "1" "Flags" "0x10" "Broken" "abc" }
            "binary" { "Blob" "00" }
            "Direct" "straight"
        }
        "HKEY_LOCAL_MACHINE\\\\System\\\\CurrentControlSet\\\\Services\\\\Evil" { "string" { "ImagePath" "x" } }
        "HKEY_CLASSES_ROOT\\\\Foo" { "string" { "a" "b" } }
        "HKEY_CURRENT_USER\\\\Software\\\\Fixture User" { "dword" { "Seen" "7" } }
        "HKEY_LOCAL_MACHINE\\\\Software\\\\Fixture Vendor" { "string" { "Version" "2.0" "Late" "late" } }
    }
    "Run Process"
    {
        "Runtime A" { "process 1" "%INSTALLDIR%\\\\setup.exe" }
    }
}
"""

@main struct Checks {
    @MainActor static func main() throws {
        setvbuf(stdout, nil, _IONBF, 0)

        // ---- the reader
        let parsed = SteamInstallRegistry.parse(script: Data(script.utf8), installDir: dir, language: "english")
        func value(_ hive: SteamInstallRun.Hive, _ key: String, _ name: String) -> SteamInstallRegistryValue.Payload? {
            parsed.values.first { $0.hive == hive && $0.key == key && $0.name == name }?.payload
        }
        require(value(.machine, "Software\\Bethesda Softworks\\FalloutNV", "Installed Path") == .string(dir), "%INSTALLDIR% is the game's folder (a Windows path, no trailing backslash)")
        require(value(.machine, "Software\\Wow6432Node\\Bethesda Softworks\\FalloutNV", "Installed Path") == .string(dir), "the token is read in any case; the script's own Wow6432Node key is its own value")
        require(value(.machine, "Software\\Fixture Vendor", "Language") == .string("English"), "a language section: the installed language's text (English here)")
        require(value(.machine, "Software\\Fixture Vendor", "Lang2") == .string("english"), "%LANGUAGE% is the installed language")
        require(value(.machine, "Software\\Fixture Vendor", "Version") == .string("1.0"), "a repeated value: the first one counts")
        require(value(.machine, "Software\\Fixture Vendor", "Late") == .string("late"), "a repeated key adds its other values")
        require(value(.machine, "Software\\Fixture Vendor", "Direct") == .string("straight"), "a value straight under the key is a string")
        require(value(.machine, "Software\\Fixture Vendor", "Installed") == .dword(1) && value(.machine, "Software\\Fixture Vendor", "Flags") == .dword(16), "dword values: decimal and 0x")
        require(value(.user, "Software\\Fixture User", "Seen") == .dword(7), "HKCU is read")
        require(value(.machine, "Software\\Fixture Vendor", "Quote") == .string("say \"hi\" \\ caf\u{e9}"), "the script's escapes are undone")
        require(value(.machine, "Software\\Fixture Vendor", "Bad") == nil && value(.machine, "Software\\Fixture Vendor", "Broken") == nil &&
                value(.machine, "Software\\Fixture Vendor", "Blob") == nil, "an unknown token, a bad dword and a type this does not know are left out")
        require(!parsed.values.contains { $0.key.lowercased().hasPrefix("system") || $0.name == "ImagePath" || $0.name == "a" }, "keys outside Software\\ and other hives are left out")
        require(parsed.values.count == 11, "eleven values in all (\(parsed.values.count))")
        require(Dictionary(grouping: parsed.skipped, by: { $0 }).mapValues(\.count) == ["token": 1, "dword": 1, "type:binary": 1, "path": 2], "what was left out is counted by reason (\(parsed.skipped))")
        let german = SteamInstallRegistry.parse(script: Data(script.utf8), installDir: dir, language: "german")
        require(german.values.first { $0.name == "Language" }?.payload == .string("Deutsch") && german.values.first { $0.name == "Lang2" }?.payload == .string("german"), "another installed language is used")
        let polish = SteamInstallRegistry.parse(script: Data(script.utf8), installDir: dir, language: "polish")
        require(polish.values.first { $0.name == "Language" }?.payload == .string("English"), "a language the script lacks falls back to English")
        require(SteamInstallRegistry.parse(script: Data("\"installscript\" { \"run process\" { } }".utf8), installDir: dir, language: "english") == SteamInstallRegistry.Parsed(), "a script without a Registry section gives nothing")
        require(SteamInstallRegistry.parse(script: Data("\u{FEFF}\"InstallScript\" { // note\n \"registry\" { \"HKLM\\\\Software\\\\Short\" { \"string\" { \"A\" \"b\" } } } }".utf8), installDir: dir, language: "english").values.count == 1,
                "a byte order mark, comments, HKLM and any case of the section name")
        let many = "\"installscript\" { \"registry\" { \"HKLM\\\\Software\\\\Many\" { \"string\" { " + (0..<80).map { "\"v\($0)\" \"x\"" }.joined(separator: " ") + " } } } }"
        let capped = SteamInstallRegistry.parse(script: Data(many.utf8), installDir: dir, language: "english")
        require(capped.values.count == SteamInstallRegistry.maxValues && capped.skipped.count == 80 - SteamInstallRegistry.maxValues, "at most 64 values per script")
        let control = SteamInstallRegistry.parse(script: Data("\"installscript\" { \"registry\" { \"HKLM\\\\Software\\\\X\" { \"string\" { \"A\" \"line\nbreak\" \"B\" \"\" } } } }".utf8), installDir: dir, language: "english")
        require(control.values.isEmpty && control.skipped == ["name", "name"], "a control character or an empty text is left out (\(control.skipped))")
        let views = { (hive: SteamInstallRun.Hive, key: String) in SteamInstallRegistry.views(SteamInstallRegistryValue(hive: hive, key: key, name: "n", payload: .dword(1))) }
        require(views(.machine, "Software\\A\\B") == ["Software\\A\\B", "Software\\Wow6432Node\\A\\B"] && views(.machine, "Software\\Wow6432Node\\A") == ["Software\\Wow6432Node\\A"]
                && views(.user, "Software\\A") == ["Software\\A"], "HKLM\\Software gets both registry views, a Wow6432Node key and HKCU one")

        // ---- the registry text
        let reg = """
        WINE REGISTRY Version 2
        ;; All keys relative to \\\\Machine

        #arch=win64

        [Software\\\\Bethesda Softworks\\\\FalloutNV] 1700000000
        #time=1d0
        "Existing"="x"
        "installed path"="D:\\\\Elsewhere"

        [Software\\\\Fixture Vendor] 1700000000
        "Other"="b"

        [Software\\\\Zeta] 1700000000
        "z"="z"
        """
        let two = parsed.values.filter { $0.name == "Installed Path" } + parsed.values.filter { $0.key == "Software\\Fixture Vendor" && ($0.name == "Version" || $0.name == "Quote" || $0.name == "Installed") }
        let (text, written, present) = SteamInstallRegistry.addMissing(two, in: reg, now: 42)
        require(text.contains("\"installed path\"=\"D:\\\\Elsewhere\"") && !text.contains("\"Installed Path\"=\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Fallout New Vegas\"\n\n[Software\\\\Fixture"),
                "a value that is there (any case) stays as it is")
        require(present == 2, "…and is counted (the script's own Wow6432Node key too: it was just added by the first value's second view) (\(present))")
        require(text.contains("[Software\\\\Wow6432Node\\\\Bethesda Softworks\\\\FalloutNV] 42\n\"Installed Path\"=\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Fallout New Vegas\""),
                "a missing key gets its own section with the value")
        require(text.contains("[Software\\\\Fixture Vendor] 1700000000\n\"Other\"=\"b\"\n\"Version\"=\"1.0\"\n\"Quote\"=\"say \\\"hi\\\" \\\\ caf\\x00e9\"\n\"Installed\"=dword:00000001\n\n[Software\\\\Zeta]"),
                "a missing value goes at the end of its key's section; quote, backslash and non-ASCII are escaped as Wine writes them")
        require(text.contains("[Software\\\\Wow6432Node\\\\Fixture Vendor] 42\n\"Version\"=\"1.0\""), "the second registry view of an HKLM\\Software key is written too")
        require(written.count == 7, "every key and value written is reported (\(written.count))")
        require(text.hasPrefix("WINE REGISTRY Version 2\n;; All keys relative to \\\\Machine\n") && text.contains("[Software\\\\Zeta] 1700000000\n\"z\"=\"z\""), "nothing else of the file changes")
        let again = SteamInstallRegistry.addMissing(two, in: text, now: 99)
        require(again.text == text && again.written.isEmpty && again.present == 9, "a second run writes nothing (everything is there: \(again.present))")
        require(SteamInstallRegistry.line(SteamInstallRegistryValue(hive: .machine, key: "Software\\X", name: "N\"a\\me", payload: .string("v"))) == "\"N\\\"a\\\\me\"=\"v\"", "a name is escaped")

        // ---- the language of the install record
        require(DockScriptRegistry.installLanguage(manifest: "\"AppState\"\n{\n\t\"UserConfig\"\n\t{\n\t\t\"language\"\t\t\"german\"\n\t}\n}") == "german", "the install record's language")
        require(DockScriptRegistry.installLanguage(manifest: nil) == "english" && DockScriptRegistry.installLanguage(manifest: "\"language\" \"BAD NAME!\"") == "english", "English without a record or with a bad word")

        // ---- the switch
        let base = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("dock-script-registry-\(getpid())")
        try? FileManager.default.removeItem(at: base)   // start empty: exit() skips defer, so a folder with a reused process id may be left
        defer { try? FileManager.default.removeItem(at: base) }
        let cfg = base.appendingPathComponent("game.cfg")
        unsetenv("MADEIRA_DOCK_SCRIPT_REGISTRY")
        require(!DockScriptRegistry.enabled(gameConfigPath: nil) && !DockScriptRegistry.enabled(gameConfigPath: cfg.path), "unset, and no game file: off")
        try write(cfg, "# comment\nswap-mb = 3072\nenv.MADEIRA_DOCK_SCRIPT_REGISTRY = 1\n")
        require(DockScriptRegistry.enabled(gameConfigPath: cfg.path), "the game's own line turns it on")
        try write(cfg, "env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1\nenv.MADEIRA_DOCK_SCRIPT_REGISTRY = 0\n")
        require(!DockScriptRegistry.enabled(gameConfigPath: cfg.path), "the last line wins; 0 is off")
        setenv("MADEIRA_DOCK_SCRIPT_REGISTRY", "1", 1)
        require(!DockScriptRegistry.enabled(gameConfigPath: cfg.path), "the game's own 0 beats the environment")
        try write(cfg, "swap-mb = 1\n# env.MADEIRA_DOCK_SCRIPT_REGISTRY = 0\n")
        require(DockScriptRegistry.enabled(gameConfigPath: cfg.path) && DockScriptRegistry.enabled(gameConfigPath: nil), "no line in the game's file: bcd.cfg / the environment decide")
        unsetenv("MADEIRA_DOCK_SCRIPT_REGISTRY")
        require(!DockScriptRegistry.enabled(gameConfigPath: cfg.path), "…and off again when they say nothing")

        // ---- scripts() looks for what it is asked
        let prefix = base.appendingPathComponent("wine"), drive = prefix.appendingPathComponent("drive_c")
        let game = DockGame(id: 22380, name: "Fallout: New Vegas", installDir: "Fallout New Vegas", library: "Program Files (x86)/Steam/steamapps",
                            installed: true, customExecutables: false)
        let common = drive.appendingPathComponent("Program Files (x86)/Steam/steamapps/common")
        let folder = common.appendingPathComponent("Fallout New Vegas")
        try write(folder.appendingPathComponent("installscript.vdf"), script)
        try write(folder.appendingPathComponent("redist/runonly.vdf"), "\"installscript\" { \"run process\" { \"X\" { \"process 1\" \"%INSTALLDIR%\\\\x.exe\" } } }")
        try write(folder.appendingPathComponent("other.vdf"), "\"nothing\" { }")
        try write(common.appendingPathComponent("Steamworks Shared/_CommonRedist/Shared/installscript.vdf"),
                  "\"installscript\" { \"registry\" { \"HKLM\\\\Software\\\\Shared Redist\" { \"string\" { \"Path\" \"%INSTALLDIR%\" } } } }")
        try write(drive.deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("outside.vdf"), "registry")
        try FileManager.default.createSymbolicLink(at: folder.appendingPathComponent("link.vdf"), withDestinationURL: base.appendingPathComponent("game.cfg"))
        let names = { (urls: [URL]) in urls.map(\.lastPathComponent).sorted() }
        require(names(SteamInstallScripts.scripts(folder: folder)) == ["installscript.vdf", "runonly.vdf"], "the default look is for 'run process' scripts, as before")
        require(names(SteamInstallScripts.scripts(folder: folder, depth: 1, containing: "registry")) == ["installscript.vdf"], "asked for 'registry': only the scripts that have it; a link is not followed")
        let manifest = drive.appendingPathComponent("Program Files (x86)/Steam/steamapps/appmanifest_22380.acf")
        try write(manifest, "\"AppState\"\n{\n\t\"appid\"\t\t\"22380\"\n\t\"UserConfig\"\n\t{\n\t\t\"language\"\t\t\"english\"\n\t}\n}\n")

        // ---- prepare() in a synthetic prefix
        let machine = """
        WINE REGISTRY Version 2
        ;; All keys relative to \\\\Machine

        #arch=win64

        [Software\\\\Zeta] 1700000000
        "z"="z"
        """
        let user = "WINE REGISTRY Version 2\n;; All keys relative to \\\\User\\\\S-1-5-21-0-0-0-1000\n\n#arch=win64\n\n[Software\\\\Other] 1700000000\n\"o\"=\"o\"\n"
        try write(prefix.appendingPathComponent("system.reg"), machine)
        try write(prefix.appendingPathComponent("user.reg"), user)
        let off = DockScriptRegistry.prepare(game, drive: drive, prefix: prefix, gameConfigPath: cfg.path)
        require(off == "off" && read(prefix.appendingPathComponent("system.reg")) == machine && read(prefix.appendingPathComponent("user.reg")) == user
                && registryLines().isEmpty && !FileManager.default.fileExists(atPath: prefix.appendingPathComponent("system.reg.bcd-bak").path),
                "off: no file touched, no backup, nothing logged")
        try write(cfg, "env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1\n")
        let first = DockScriptRegistry.prepare(game, drive: drive, prefix: prefix, now: Date(timeIntervalSince1970: 1_800_000_000), gameConfigPath: cfg.path)
        let system = read(prefix.appendingPathComponent("system.reg")), userNow = read(prefix.appendingPathComponent("user.reg"))
        let installed = "\"Installed Path\"=\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Fallout New Vegas\""
        require(first == "written", "on: written")
        require(system.contains("[Software\\\\Bethesda Softworks\\\\FalloutNV] 1800000000\n" + installed) &&
                system.contains("[Software\\\\Wow6432Node\\\\Bethesda Softworks\\\\FalloutNV] 1800000000\n" + installed), "Fallout: New Vegas' Installed Path in both registry views")
        require(!system.contains("Shared Redist") && !system.contains("Evil"), "only the game's own folder is read, and nothing outside Software\\")
        require(userNow.contains("[Software\\\\Fixture User] 1800000000\n\"Seen\"=dword:00000007") && userNow.contains("[Software\\\\Other] 1700000000\n\"o\"=\"o\""), "HKCU values go into user.reg, the rest of it stays")
        require(read(prefix.appendingPathComponent("system.reg.bcd-bak")) == machine && read(prefix.appendingPathComponent("user.reg.bcd-bak")) == user, "a first-time backup of each file")
        let lines = registryLines()
        require(lines.filter { $0.contains(" wrote ") }.count == 19 && lines.count == 20, "one line for each key and value written (19: 8 HKLM values in two views, Installed Path in two, one HKCU) and a summary (\(lines.count))")
        require(lines.contains { $0.contains("wrote HKLM\\Software\\Wow6432Node\\Bethesda Softworks\\FalloutNV \"Installed Path\" = \"C:\\Program Files (x86)") }, "the line names the hive, the key, the value and what was written")
        require(lines.last?.contains("scripts=1 values=11 written=19 present=1") == true && lines.last?.contains("language=english") == true &&
                lines.last?.contains("skipped=dword(1),path(2),token(1),type:binary(1)") == true, "the summary says what was read, written and left out (\(lines.last ?? ""))")
        LogStore.shared.lines.removeAll()
        let second = DockScriptRegistry.prepare(game, drive: drive, prefix: prefix, now: Date(timeIntervalSince1970: 1_900_000_000), gameConfigPath: cfg.path)
        let secondLines = registryLines()
        require(second == "present" && read(prefix.appendingPathComponent("system.reg")) == system && read(prefix.appendingPathComponent("user.reg")) == userNow
                && secondLines.count == 12 && secondLines.last?.contains("written=0") == true && secondLines.filter { $0.contains(" present ") }.count == 11
                && secondLines.filter { $0.contains(" wrote ") }.isEmpty,
                "a second start writes nothing: files unchanged; round 623 lists the 11 values that are there, then the summary (\(secondLines.count) lines)")
        require(secondLines.contains { $0.contains("present HKLM\\Software\\Bethesda Softworks\\FalloutNV \"Installed Path\": 2 of 2 view(s), same as the script") }
                && secondLines.contains { $0.contains("present HKCU\\Software\\Fixture User \"Seen\": 1 of 1 view(s), same as the script") },
                "a present value is named with its hive, key, how many registry views hold it and whether the text is the script's (\(secondLines.prefix(3)))")
        // A value an earlier session or the user changed is never overwritten.
        try write(prefix.appendingPathComponent("system.reg"), system.replacingOccurrences(of: "\"Installed Path\"=\"C:\\\\Program Files (x86)\\\\Steam\\\\steamapps\\\\common\\\\Fallout New Vegas\"", with: "\"Installed Path\"=\"E:\\\\Mine\""))
        LogStore.shared.lines.removeAll()
        _ = DockScriptRegistry.prepare(game, drive: drive, prefix: prefix, gameConfigPath: cfg.path)
        require(!read(prefix.appendingPathComponent("system.reg")).contains("steamapps\\\\common\\\\Fallout New Vegas\"") && read(prefix.appendingPathComponent("system.reg")).contains("E:\\\\Mine"), "a value that is there is not overwritten")
        require(registryLines().contains { $0.contains("\"Installed Path\": 2 of 2 view(s), differs: stored \"E:\\\\Mine\", the script has \"C:\\\\Program Files (x86)") },
                "…and the log says it differs, with the stored text and the script's (\(registryLines().filter { $0.contains("differs") }))")

        // A prefix that is not seeded yet, or a registry file that is not Wine's.
        let empty = base.appendingPathComponent("empty")
        try FileManager.default.createDirectory(at: empty, withIntermediateDirectories: true)
        LogStore.shared.lines.removeAll()
        require(DockScriptRegistry.prepare(game, drive: drive, prefix: empty, gameConfigPath: cfg.path) == "no-registry" && registryLines().last?.contains("no registry file yet") == true,
                "no registry file yet: nothing written, said in the summary")
        let odd = base.appendingPathComponent("odd")
        try write(odd.appendingPathComponent("system.reg"), "not a registry")
        try write(odd.appendingPathComponent("user.reg"), user)
        _ = DockScriptRegistry.prepare(game, drive: drive, prefix: odd, gameConfigPath: cfg.path)
        require(read(odd.appendingPathComponent("system.reg")) == "not a registry" && read(odd.appendingPathComponent("user.reg")).contains("Fixture User"), "a file that is not Wine's is left as it is; the other is still written")
        // A game with no install script, or one without registry values.
        let bare = DockGame(id: 5, name: "Bare", installDir: "Bare", library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
        try FileManager.default.createDirectory(at: common.appendingPathComponent("Bare"), withIntermediateDirectories: true)
        LogStore.shared.lines.removeAll()
        require(DockScriptRegistry.prepare(bare, drive: drive, prefix: prefix, gameConfigPath: cfg.path) == "none" && registryLines().count == 1 && registryLines()[0].contains("scripts=0 values=0"), "no script: one line says so")

        // A script shaped differently from what is read: nothing is written, and the log shows its key names and types.
        let shaped = DockGame(id: 6, name: "Shaped", installDir: "Shaped", library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
        try write(common.appendingPathComponent("Shaped/installscript.vdf"),
                  "\"InstallScript\" { \"Registry\" { \"HKEY_LOCAL_MACHINE\\\\Software\\\\Odd Vendor\" { \"REG_SZ\" { \"A\" \"b\" } } } }")
        LogStore.shared.lines.removeAll()
        let systemBefore = read(prefix.appendingPathComponent("system.reg"))
        require(DockScriptRegistry.prepare(shaped, drive: drive, prefix: prefix, gameConfigPath: cfg.path) == "none" && read(prefix.appendingPathComponent("system.reg")) == systemBefore
                && registryLines().count == 1 && registryLines()[0].contains("scripts=1 values=0 skipped=type:reg_sz(1) keys=HKEY_LOCAL_MACHINE\\Software\\Odd Vendor"),
                "a shape that is not read: nothing written; the log names the type and the key (\(registryLines()))")

        // Round 623: entries a language section leaves out show their shape; stored() names what the registry text holds.
        // Round 632: a value that has texts for other languages only (no English, no installed language) is still left out and described.
        let oddLang = "\"InstallScript\" { \"Registry\" { \"HKEY_LOCAL_MACHINE\\\\Software\\\\Odd Lang\" { \"string\" { \"Name\" { \"french\" \"Impair\" \"polish\" \"Nieparzysty\" } \"Q\" { \"german\" \"x\" } } } } }"
        let shapes = SteamInstallRegistry.parse(script: Data(oddLang.utf8), installDir: dir, language: "english")
        require(shapes.values.isEmpty && shapes.skipped == ["language", "language"]
                && shapes.languageShapes == ["HKEY_LOCAL_MACHINE\\Software\\Odd Lang | string | Name | french,polish", "HKEY_LOCAL_MACHINE\\Software\\Odd Lang | string | Q | german"],
                "entries left out for want of a text in the installed language or English are described: key, type, name and the languages (\(shapes.languageShapes))")
        // Round 632 (Far Cry 3: 34 entries left out in 7 logs, builds 614-623; upstream Madeira #550): the type holds one section per language,
        // each with the name/text pairs of that language.
        let fc3 = "\"InstallScript\" { \"Registry\" { \"HKEY_LOCAL_MACHINE\\\\SOFTWARE\\\\Ubisoft\\\\Far Cry 3\" { \"string\" { \"danish\" { \"language\" \"Dansk\" } \"english\" { \"language\" \"English\" \"Extra\" \"e\" } \"german\" { \"language\" \"Deutsch\" } } \"dword\" { \"english\" { \"Region\" \"3\" } } } } }"
        let fcEnglish = SteamInstallRegistry.parse(script: Data(fc3.utf8), installDir: dir, language: "english")
        func fc(_ parsed: SteamInstallRegistry.Parsed, _ name: String) -> SteamInstallRegistryValue.Payload? { parsed.values.first { $0.name == name }?.payload }
        require(fc(fcEnglish, "language") == .string("English") && fc(fcEnglish, "Extra") == .string("e") && fc(fcEnglish, "Region") == .dword(3)
                && fcEnglish.values.count == 3 && fcEnglish.skipped.isEmpty && fcEnglish.languageShapes.isEmpty,
                "Far Cry 3's layout, a language section holding name/text pairs: the installed language's text (English), nothing left out (\(fcEnglish.skipped))")
        let fcGerman = SteamInstallRegistry.parse(script: Data(fc3.utf8), installDir: dir, language: "german")
        require(fc(fcGerman, "language") == .string("Deutsch") && fc(fcGerman, "Extra") == .string("e") && fcGerman.values.count == 3,
                "another installed language is used, a value that language lacks falls back to English")
        let fcPolish = SteamInstallRegistry.parse(script: Data(fc3.utf8), installDir: dir, language: "polish")
        require(fc(fcPolish, "language") == .string("English") && fcPolish.values.count == 3, "a language the script lacks falls back to English")
        let both = SteamInstallRegistry.parse(script: Data("\"InstallScript\" { \"Registry\" { \"HKLM\\\\Software\\\\Both\" { \"string\" { \"Language\" { \"english\" \"E\" \"german\" \"G\" } \"english\" { \"english\" \"same\" } \"french\" { \"Name\" \"N\" } } } } }".utf8), installDir: dir, language: "english")
        require(fc(both, "Language") == .string("E") && fc(both, "english") == .string("same") && both.skipped == ["language"],
                "both layouts in one script: a value name holding languages stays one; a language section whose pair is a language name is a value named so; a language with no English text is left out (\(both.skipped))")
        let sixMany = "\"InstallScript\" { \"Registry\" { \"HKLM\\\\Software\\\\S\" { \"string\" { " + (0..<9).map { "\"l\($0)\" { \"A\\\"B\" \"c\" }" }.joined(separator: " ") + " } } } }"
        let capped6 = SteamInstallRegistry.parse(script: Data(sixMany.utf8), installDir: dir, language: "english")
        require(capped6.skipped.count == 9 && capped6.languageShapes.count == 6 && !capped6.languageShapes.joined().contains("\""), "at most six shapes, quotes taken out (\(capped6.languageShapes.first ?? ""))")
        require(SteamInstallRegistry.parse(script: Data(script.utf8), installDir: dir, language: "english").languageShapes.isEmpty, "a script with no language entries left out describes none")
        let probe = [SteamInstallRegistryValue(hive: .machine, key: "Software\\Probe", name: "Path", payload: .string("C:\\Game")),
                     SteamInstallRegistryValue(hive: .machine, key: "Software\\Probe", name: "Flag", payload: .dword(1)),
                     SteamInstallRegistryValue(hive: .machine, key: "Software\\Probe", name: "Gone", payload: .string("g")),
                     SteamInstallRegistryValue(hive: .machine, key: "Software\\Probe", name: "Other", payload: .string("same"))]
        let probeText = "WINE REGISTRY Version 2\n\n[Software\\\\Probe] 1\n\"Path\"=\"C:\\\\Game\"\n\"FLAG\"=dword:00000002\n\"Other\"=\"same\"\n\n[Software\\\\Wow6432Node\\\\Probe] 1\n\"path\"=\"D:\\\\Else\"\n"
        let stored = SteamInstallRegistry.stored(probe, in: probeText)
        require(stored.map { $0.value.name } == ["Path", "Flag", "Other"], "stored(): only the values that are there, in the script's order (\(stored.map { $0.value.name }))")
        require(stored[0].views == 2 && stored[0].found == 2 && !stored[0].same && stored[0].stored == "\"D:\\\\Else\"" && stored[0].wanted == "\"C:\\\\Game\"",
                "a value held by both views with a different text in one: not the same, the differing text is shown (\(stored[0]))")
        require(stored[1].found == 1 && stored[1].same == false && stored[1].stored == "dword:00000002" && stored[1].wanted == "dword:00000001", "a dword, found in any case: its text and the script's")
        require(stored[2].found == 1 && stored[2].views == 2 && stored[2].same, "present in one view only, with the script's text: same")
        require(SteamInstallRegistry.stored(probe, in: "WINE REGISTRY Version 2\n").isEmpty, "an empty registry holds none")
        // prepare(): the shapes come before the summary, which stays the last line.
        let langGame = DockGame(id: 7, name: "Lang", installDir: "Lang", library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
        try write(common.appendingPathComponent("Lang/installscript.vdf"),
                  "\"InstallScript\" { \"Registry\" { \"HKLM\\\\Software\\\\Lang Vendor\" { \"string\" { \"Dir\" \"%INSTALLDIR%\" } } \"HKLM\\\\Software\\\\Lang Vendor2\" { \"string\" { \"N\" { \"french\" \"x\" } } } } }")
        try write(common.appendingPathComponent("Lang/appmanifest.txt"), "x")
        LogStore.shared.lines.removeAll()
        _ = DockScriptRegistry.prepare(langGame, drive: drive, prefix: prefix, gameConfigPath: cfg.path)
        let langLines = registryLines()
        require(langLines.count >= 3 && langLines.last?.contains("scripts=1 values=1") == true && langLines.last?.contains("skipped=language(1)") == true
                && langLines.contains { $0.contains("app=7 left out for language: HKLM\\Software\\Lang Vendor2 | string | N | french") }
                && langLines.firstIndex { $0.contains("left out for language") }! < langLines.count - 1,
                "prepare logs the shape of a language entry it left out, before the summary (\(langLines))")

        // ---- DockInstallers.prepare calls it
        let before = read(prefix.appendingPathComponent("user.reg"))
        try write(prefix.appendingPathComponent("user.reg"), user)
        try write(prefix.appendingPathComponent("system.reg"), machine)
        setenv("MADEIRA_CFG_GAME", cfg.path, 1)
        setenv("MADEIRA_DOCK_INSTALLERS", "0", 1)
        try write(cfg, "swap-mb = 1\n")
        DockInstallers.prepare(game, drive: drive, prefix: prefix)
        require(read(prefix.appendingPathComponent("system.reg")) == machine && read(prefix.appendingPathComponent("user.reg")) == user, "DockInstallers.prepare with the switch unset leaves the registry files as they were")
        try write(cfg, "env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1\n")
        DockInstallers.prepare(game, drive: drive, prefix: prefix)
        require(read(prefix.appendingPathComponent("system.reg")).contains(installed) && read(prefix.appendingPathComponent("user.reg")) != before.replacingOccurrences(of: "x", with: "y"),
                "DockInstallers.prepare writes the values from the game's own line, the installers switched off too")
        unsetenv("MADEIRA_DOCK_INSTALLERS"); unsetenv("MADEIRA_CFG_GAME")

        print(failures == 0 ? "ALL PASS" : "FAILURES: \(failures)")
        exit(failures == 0 ? 0 : 1)
    }
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-dock-script-registry-') as tmp:
    tmp = Path(tmp)
    (tmp / 'stubs.swift').write_text(stubs, encoding='utf-8')
    (tmp / 'checks.swift').write_text(checks, encoding='utf-8')
    store_source = (app / 'BCDStore.swift').read_text(encoding='utf-8')
    (tmp / 'dates.swift').write_text('import Foundation\n\nenum BCDFormat {\n' + store_source[
        store_source.index('    // dates-begin'):store_source.index('    // dates-end')] + '}\n', encoding='utf-8')
    exe = tmp / 'check'
    if not Path(SWIFTC).exists() and not shutil.which(SWIFTC):
        if '--require-swift' in sys.argv:
            require(False, 'Swift compiler available (--require-swift)')
        else:
            print('SKIP: Swift compiler unavailable; the compiled checks did not run')
    else:
        build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-sanitize=address', '-o', str(exe),
                                str(tmp / 'stubs.swift'), str(tmp / 'checks.swift'), str(app / 'DockInstallers.swift'),
                                str(app / 'DockReset.swift'), str(tmp / 'dates.swift')])
        require(build.returncode == 0, 'production Dock install-script Swift compiles on the host')
        if build.returncode == 0:
            run = subprocess.run([str(exe)], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
            require(run.returncode == 0, 'registry checks pass under AddressSanitizer')

print('PASS' if failures == 0 else 'FAILED')
sys.exit(0 if failures == 0 else 1)
