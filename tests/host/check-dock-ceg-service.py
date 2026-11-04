#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Valve's client service before a custom-executable (CEG) Dock start (round 578, ajan6/ceg).

Synthetic data only: no Steam, Wine, iOS or credentials. Logs 2026-10-19 JYFG4D and 2026-10-21 codbo-prev:
the Dock host started services.exe, found "Steam Client Service" missing and ran Valve's own bin\\SteamService.exe
/install, whose first RPC to the service manager failed (RpcAssoc_BindConnection receive failed with error 1726,
"Failed to open service control manager", exit 1) and the start ended with ceg-result -5. The app now has the service
installed before the session (DockCEGService in app/Madeira/DockInstallers.swift). This compiles that production Swift
under AddressSanitizer and checks:
  * the registry text: found (either control set name, any case, sub-keys and look-alikes do not count), added once,
    the exact values, a file that is not a Wine registry left alone;
  * prepare() in a synthetic prefix: nothing for a game without per-user executables or with MADEIRA_DOCK_CEG=0,
    nothing and the host's own step kept when Valve's program is missing or the copy cannot be made (a key pointing at
    nothing would also stop the host from running Valve's installer), the copy byte for byte and never over an existing
    file, case-insensitive folders, no folder outside drive_c, the key written once with a backup, the log lines;
  * the lines the app takes from Valve's own log files after a failed CEG start (filtered, long numbers cut out);
  * the app's wiring (start order, installs-only sessions, the failure hook) by text;
  * with a Wine source tree (WINE_SRC, default wine/): the value names Wine's services.exe reads and writes for a
    service, and the text the app writes read back by Wine's own wineserver string parser (parse_strW).
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
CC = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


installers = (app / 'DockInstallers.swift').read_text(encoding='utf-8')
content = (app / 'ContentView.swift').read_text(encoding='utf-8')
model = (app / 'MadeiraDockModel.swift').read_text(encoding='utf-8')
dock = (app / 'MadeiraDock.swift').read_text(encoding='utf-8')

# ------------------------------------------------------------------ wiring, by text
start = content.index('private func startDock(')
body = content[start:content.index('\n    }\n', start)]
require(body.index('DockInstallers.prepare(') < body.index('DockCEGService.prepare(game, drive: MadeiraDock.drive, prefix: MadeiraDock.prefix)')
        < body.index('runWineFullSequence('), 'the service is prepared after the installs plan, before the session starts')
call = body[body.index('DockCEGService.prepare(') - 80:body.index('DockCEGService.prepare(') + 120]
require('if !DockInstallers.installsOnly' in call, 'not for a session that only runs one-time installs (no host runs)')
require('MadeiraDock.writeHandoff(' in body and body.index('MadeiraDock.writeHandoff(') < body.index('DockCEGService.prepare('),
        'only after the launch checks passed')
require(re.search(r'if report\.result == 49, report\.fields\["ceg-result"\] != nil \{ DockCEGService\.logSteamLogs\(drive: MadeiraDock\.drive\) \}',
                  model) is not None, "Valve's own log lines are taken only after a failed CEG start (host result 49 with a ceg-result)")
require(installers.count('SteamSignIn.flag("MADEIRA_DOCK_CEG_SERVICE", default: true)') == 2,
        'one switch, on by default, read by the preparation and by the log lines')
require('MADEIRA_STEAM_HOST_CEG' in dock and 'game.customExecutables && SteamSignIn.flag("MADEIRA_DOCK_CEG", default: true)' in dock,
        'the host asks for CEG under the same condition the preparation uses')
service_code = installers.split('enum DockCEGService')[1]
require(not any(word in service_code for word in ('reg.exe', 'Process(', 'posix_spawn', 'NSTask', 'system(', 'OpenSCManager', 'sc.exe')),
        'the app starts no program and calls no service manager: it only places a file and a registry key before the session')
require('Copyright 2026 125hz' in installers.split('\n', 3)[1], 'DockInstallers.swift keeps its header (the addition is marked madeira-bcd)')

workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
require('python3 tests/host/check-dock-ceg-service.py' in workflow, 'the workflow runs this check')
rules = (root / 'build/tools/autotune_rules.py').read_text(encoding='utf-8')
rid = rules.find('"id": "ceg-service-not-installed"')
require(rid >= 0 and '"fixedIn": 578' in rules[rid:rid + 200] and '"session": "dock"' in rules[rid:rid + 200] and '"lines": []' in rules[rid:rid + 200],
        'auto-tune names the -5 as fixed in 578: no config line, Dock sessions only')
block = rules[rid:rules.index('\n    },', rid)]
require('"all": [s("[dock-report] ceg-result=-5")]' in block and block.count('s("') == 1,
        'its one signal is the report line of the -5, not the 55 (sessions that got the service registered still end with 55)')
expect = root / 'tests/fixtures/autotune/ceg-service-not-installed-2026-10-21.expect'
require(expect.exists() and '"ceg-service-not-installed"' in expect.read_text(encoding='utf-8') and
        (root / 'tests/fixtures/autotune/ceg-service-not-installed-2026-10-21.log').exists(),
        'the fixture cut from the codbo-prev log gets this rule')

# ------------------------------------------------------------------ compiled Swift
game_src = dock[dock.index("/// A game Steam's client has installed"):dock.index('/// Madeira Dock: a small headless host')]
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
    var errors: [String] = []
    func log(_ message: String, level: LogEntry.Level = .info) { lines.append(message); if case .error = level { errors.append(message) } }
}
enum MadeiraDock { static let executable = "C:\\windows\\system32\\dockhost.exe" }
enum WineMonoModel { static var available: Bool { false } }   // 578: DockInstallers.swift asks it (the .NET setups)
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
func write(_ url: URL, _ data: Data) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try data.write(to: url)
}
func write(_ url: URL, _ text: String) throws { try write(url, Data(text.utf8)) }
/// A minimal PE header (a 32-bit program) followed by a marker, so a copy can be told from another file.
func pe(_ marker: String) -> Data {
    var bytes = [UInt8](repeating: 0, count: 64 + 24)
    bytes[0] = 0x4d; bytes[1] = 0x5a; bytes[60] = 64
    bytes[64] = 0x50; bytes[65] = 0x45; bytes[68] = 0x4c; bytes[69] = 0x01
    return Data(bytes) + Data(marker.utf8)
}
@MainActor func logged(_ text: String) -> Bool { LogStore.shared.lines.contains { $0.contains(text) } }
@MainActor func count(_ text: String) -> Int { LogStore.shared.lines.filter { $0.contains(text) }.count }

let wineHead = "WINE REGISTRY Version 2\n;; All keys relative to REGISTRY\\\\Machine\n\n#arch=win64\n\n[Software\\\\Valve\\\\Steam] 1776969663\n#time=1dcd350bcd95418\n\"InstallPath\"=\"C:\\\\Program Files (x86)\\\\Steam\"\n\n"

@main struct Checks {
    @MainActor static func main() throws {
        setvbuf(stdout, nil, _IONBF, 0)
        // ---- the registry text
        let key = "[System\\\\ControlSet001\\\\Services\\\\Steam Client Service] 1776969663\n\"Start\"=dword:00000003\n\n"
        require(!DockCEGService.registered(in: wineHead) && DockCEGService.registered(in: wineHead + key), "found by its key")
        require(DockCEGService.registered(in: wineHead + key.replacingOccurrences(of: "ControlSet001", with: "CurrentControlSet")) &&
                DockCEGService.registered(in: wineHead + key.lowercased()) && DockCEGService.registered(in: wineHead + key.replacingOccurrences(of: "ControlSet001", with: "ControlSet002")),
                "either name of the control set, any case")
        require(!DockCEGService.registered(in: wineHead + key.replacingOccurrences(of: "Steam Client Service]", with: "Steam Client Service\\\\Parameters]")) &&
                !DockCEGService.registered(in: wineHead + key.replacingOccurrences(of: "Steam Client Service]", with: "Steam Client Service2]")) &&
                !DockCEGService.registered(in: wineHead + "\"note\"=\"[System\\\\ControlSet001\\\\Services\\\\Steam Client Service]\"\n"),
                "a sub-key, a look-alike and a value that quotes the name are not the service")
        let added = DockCEGService.register(in: wineHead, now: 1776969700)
        require(added.added && added.text.hasPrefix(wineHead.trimmingCharacters(in: .whitespacesAndNewlines)) && added.text.hasSuffix("\n") &&
                DockCEGService.registered(in: added.text), "added at the end, after the existing text")
        let expected = [
            "[System\\\\ControlSet001\\\\Services\\\\Steam Client Service] 1776969700",
            "\"DisplayName\"=\"Steam Client Service\"",
            "\"ErrorControl\"=dword:00000001",
            "\"ImagePath\"=str(2):\"\\\"C:\\\\Program Files (x86)\\\\Common Files\\\\Steam\\\\SteamService.exe\\\" /RunAsService\"",
            "\"ObjectName\"=\"LocalSystem\"",
            "\"PreshutdownTimeout\"=dword:0002bf20",
            "\"Start\"=dword:00000003",
            "\"Type\"=dword:00000010",
            "\"WOW64\"=dword:00000001"]
        require(DockCEGService.section(now: 1776969700) == expected, "the values (type 0x10, demand start 3, error control 1, WOW64, Valve's image path)")
        require(added.text.components(separatedBy: "\n").filter { $0.hasPrefix("[") }.count == 2, "one section added")
        let again = DockCEGService.register(in: added.text, now: 1)
        require(!again.added && again.text == added.text, "adding twice changes nothing")
        let foreign = "not a registry\n"
        require(!DockCEGService.register(in: foreign, now: 1).added && DockCEGService.register(in: foreign, now: 1).text == foreign, "a file that is not a Wine registry is left alone")
        let crlf = DockCEGService.register(in: wineHead + "\n\n\n", now: 5)
        require(crlf.added && !crlf.text.contains("\n\n\n"), "trailing blank lines are not piled up")

        // ---- prepare() in a synthetic prefix
        let base = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("dock-ceg-service-\(getpid())")
        defer { try? FileManager.default.removeItem(at: base) }
        let prefix = base.appendingPathComponent("wine"), drive = prefix.appendingPathComponent("drive_c")
        try FileManager.default.createDirectory(at: drive, withIntermediateDirectories: true)
        let registry = prefix.appendingPathComponent("system.reg")
        let ceg = DockGame(id: 42700, name: "Call of Duty: Black Ops", installDir: "Call of Duty Black Ops", library: "Program Files (x86)/Steam/steamapps",
                           installed: true, customExecutables: true)
        let plain = DockGame(id: 7000, name: "Plain", installDir: "Plain", library: "Program Files (x86)/Steam/steamapps",
                             installed: true, customExecutables: false)
        func text() -> String { (try? String(contentsOf: registry, encoding: .utf8)) ?? "" }
        let image = drive.appendingPathComponent("Program Files (x86)/Common Files/Steam/SteamService.exe")

        require(DockCEGService.prepare(plain, drive: drive, prefix: prefix) == "not-needed" && LogStore.shared.lines.isEmpty,
                "a game without per-user executables: nothing done, nothing logged")
        setenv("MADEIRA_DOCK_CEG", "0", 1)
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "not-needed" && LogStore.shared.lines.isEmpty, "MADEIRA_DOCK_CEG=0: the client is not asked, so nothing is prepared")
        unsetenv("MADEIRA_DOCK_CEG")
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "no-registry" && logged("[ceg-service] app=42700 skipped: the prefix has no system.reg yet"),
                "no system.reg yet: skipped, logged")
        try write(registry, wineHead)
        let before = text()
        // Valve's program is not in the client folder: the host's own installer step stays the way.
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "no-installer" && text() == before &&
                !FileManager.default.fileExists(atPath: drive.appendingPathComponent("Program Files (x86)/Common Files").path) &&
                logged("key=absent image=no-installer: not registered here; the host's own installer step runs as before"),
                "no bin\\SteamService.exe: nothing written, no folder made, the host's step kept")
        // The client folder has it, under other letter cases.
        let installer = drive.appendingPathComponent("Program Files (x86)/Steam/BIN/steamservice.exe")
        try write(installer, Data([1, 2, 3]))   // not a program
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "no-installer" && text() == before, "a file that is no program is not copied")
        try write(installer, pe("valve-service-installer"))
        // A folder of the same name in another letter case is used, not duplicated.
        try FileManager.default.createDirectory(at: drive.appendingPathComponent("Program Files (x86)/COMMON FILES"), withIntermediateDirectories: true)
        let first = DockCEGService.prepare(ceg, drive: drive, prefix: prefix, now: Date(timeIntervalSince1970: 1776969700))
        let placed = try? Data(contentsOf: drive.appendingPathComponent("Program Files (x86)/Common Files/Steam/SteamService.exe"))
        require(first == "added" && placed == pe("valve-service-installer"), "added: Valve's program copied byte for byte")
        let folders = (try? FileManager.default.contentsOfDirectory(atPath: drive.appendingPathComponent("Program Files (x86)").path)) ?? []
        require(folders.filter { $0.lowercased() == "common files" }.count == 1 && folders.filter { $0.lowercased() == "steam" }.count == 1, "no second folder in another letter case")
        require(text().hasSuffix(DockCEGService.section(now: 1776969700).joined(separator: "\n") + "\n") && text().hasPrefix(before.trimmingCharacters(in: .whitespacesAndNewlines)) &&
                FileManager.default.fileExists(atPath: registry.appendingPathExtension("bcd-bak").path) &&
                (try? String(contentsOf: registry.appendingPathExtension("bcd-bak"), encoding: .utf8)) == before, "the key is written once, with a backup of the registry")
        require(logged("[ceg-service] app=42700 registered=added service=\"Steam Client Service\" image=copied type=0x10 start=3 wow64=1 path=C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe") &&
                LogStore.shared.errors.filter { $0.contains("[ceg-service]") }.count == 2, "what was done is logged (the two earlier skips were logged as errors)")
        let afterFirst = text()
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "already" && text() == afterFirst && logged("[ceg-service] app=42700 key=present image=present"),
                "the next start finds both and changes nothing")
        // Never over an existing file: a service Valve's installer put there stays, whatever it is.
        try write(image, pe("a newer service program"))
        try write(registry, wineHead + key)
        let keyed = text()
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "already" && text() == keyed &&
                (try? Data(contentsOf: image)) == pe("a newer service program"), "Valve's own registration and program are never touched")
        // Registered but the program is gone: the program is put back, the key stays as it is.
        try FileManager.default.removeItem(at: image)
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "already" && text() == keyed && (try? Data(contentsOf: image)) == pe("valve-service-installer") &&
                logged("key=present image=copied"), "a registered service without its program gets the program back")
        // A leftover that is no program is not "there": nothing is registered, the host's step stays.
        try write(registry, wineHead)
        try write(image, Data([9, 9]))
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "image-failed" && text() == wineHead && (try? Data(contentsOf: image)) == Data([9, 9]),
                "an interrupted copy is not registered and not replaced")
        try FileManager.default.removeItem(at: image)
        // Another folder that leaves drive_c.
        let outside = base.appendingPathComponent("outside")
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try FileManager.default.removeItem(at: drive.appendingPathComponent("Program Files (x86)/Common Files"))
        try FileManager.default.createSymbolicLink(at: drive.appendingPathComponent("Program Files (x86)/Common Files"), withDestinationURL: outside)
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "image-failed" && text() == wineHead &&
                ((try? FileManager.default.contentsOfDirectory(atPath: outside.path)) ?? []).isEmpty, "a folder that links out of drive_c is not written into")
        try FileManager.default.removeItem(at: drive.appendingPathComponent("Program Files (x86)/Common Files"))
        // The switch.
        setenv("MADEIRA_DOCK_CEG_SERVICE", "0", 1)
        let off = text()
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "off" && text() == off && logged("[ceg-service] app=42700 off (MADEIRA_DOCK_CEG_SERVICE=0)") &&
                !FileManager.default.fileExists(atPath: drive.appendingPathComponent("Program Files (x86)/Common Files").path), "MADEIRA_DOCK_CEG_SERVICE=0: nothing written, one line")
        let silent = LogStore.shared.lines.count
        DockCEGService.logSteamLogs(drive: drive)
        require(LogStore.shared.lines.count == silent, "MADEIRA_DOCK_CEG_SERVICE=0: no Steam log lines either")
        unsetenv("MADEIRA_DOCK_CEG_SERVICE")
        // A registry that is not Wine's.
        try write(registry, "not a registry\n")
        require(DockCEGService.prepare(ceg, drive: drive, prefix: prefix) == "no-registry" && text() == "not a registry\n" && logged("system.reg is not a Wine registry file"),
                "a registry file that is not Wine's: skipped before anything is copied")

        // ---- the final registry, for the Wine parser check
        try write(registry, wineHead)
        _ = DockCEGService.prepare(ceg, drive: drive, prefix: prefix, now: Date(timeIntervalSince1970: 1776969700))
        try Data(text().utf8).write(to: URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/dev/null"))

        // ---- Valve's own log lines after a failed CEG start
        let logs = drive.appendingPathComponent("Program Files (x86)/Steam/logs")
        require(DockCEGService.steamLogLines(drive: drive) == ["no logs folder in the client's folder"], "no logs folder: said so")
        let logBase = LogStore.shared.lines.count
        var contentLog = (1...60).map { "[2026-10-21 16:55:\($0 % 60)] AppID 42700 CEG job \($0) failed: result 55 for 76561198000000000 and 123456789 (user 1234)" }
        contentLog += ["[2026-10-21 16:55:59] AppID 42700 scheduler idle", "[2026-10-21 16:56:00] DRM: custom binary pending"]
        try write(logs.appendingPathComponent("Content_Log.txt"), contentLog.joined(separator: "\r\n") + "\r\n")
        try write(logs.appendingPathComponent("service_log.txt"), "Service started\nSomething unrelated\nremote call denied at 12345678\n")
        try write(logs.appendingPathComponent("cef_log.txt"), "never read: error everywhere\n")
        let lines = DockCEGService.steamLogLines(drive: drive)
        require(lines[0].hasPrefix("files=") && lines[0].contains("Content_Log.txt(") && lines[0].contains("service_log.txt(") && lines[0].contains("cef_log.txt("), "the folder's file names and sizes")
        require(lines.contains("content_log.txt lines=62 matching=61") && lines.contains("service_log.txt lines=3 matching=2"), "line counts are said (\(lines.filter { $0.contains("lines=") }))")
        require(lines.filter { $0.hasPrefix("content_log.txt: ") }.count == 40 && lines.last(where: { $0.hasPrefix("content_log.txt: ") })!.contains("DRM: custom binary pending"),
                "at most 40 lines of a file, the last ones")
        require(!lines.joined().contains("76561198000000000") && !lines.joined().contains("123456789") && lines.joined().contains("1234)") && lines.joined().contains("12345678") ,
                "numbers of nine digits or more are cut out; shorter ones stay")
        require(lines.contains("service_log.txt: remote call denied at 12345678") && !lines.contains { $0.contains("Something unrelated") } && !lines.joined().contains("never read"),
                "only lines with the words, only the two files")
        require(DockCEGService.scrub(String(repeating: "x", count: 500)).count == 220 && DockCEGService.scrub("id 76561198000000000.") == "id <n>.", "a line is cut to 220 characters")
        DockCEGService.logSteamLogs(drive: drive)
        require(LogStore.shared.lines.count - logBase == lines.count && logged("[ceg-log] files="), "logged with the [ceg-log] tag")
        try FileManager.default.removeItem(at: logs)
        try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        require(DockCEGService.steamLogLines(drive: drive) == ["files="], "an empty logs folder: no file names, nothing else")

        // ---- round 630 (agent U S3): Valve's client's connection lines after a start that ended with code 34
        require(DockCEGService.connectionLogLines(drive: drive) == ["no connection_log.txt"], "no connection log: said so")
        var connection = (1...50).map { "[2026-10-31 03:03:\($0 % 60)] [0,0] Connect() starting connection to CM 162.254.\($0).1:27017 attempt \($0)" }
        connection += ["[2026-10-31 03:04:18] [U:1:123456789] Logged On [U:1:123456789]",
                       "[2026-10-31 03:04:18] Log session started for user someplayer",
                       "[2026-10-31 03:04:19] Logon: account name sent",
                       "[2026-10-31 03:04:20] PICS: license list request timeout after 30000 ms",
                       "[2026-10-31 03:04:21] Something unrelated 9999999"]
        try write(logs.appendingPathComponent("Connection_Log.txt"), connection.joined(separator: "\r\n") + "\r\n")
        let conn = DockCEGService.connectionLogLines(drive: drive)
        require(conn.first == "connection_log.txt lines=55 matching=51" && conn.count == 41,
                "the count is said, at most 40 lines follow (\(conn.first ?? "-"), \(conn.count))")
        require(conn.last == "[2026-10-<n> <n>:<n>:<n>] PICS: license list request timeout after <n> ms" || conn.last?.contains("license list request timeout after <n> ms") == true,
                "the licence line is kept, with every run of five or more digits replaced (\(conn.last ?? "-"))")
        require(!conn.joined().contains("someplayer") && !conn.joined().contains("123456789") && !conn.contains { $0.lowercased().contains("account") }
                && !conn.contains { $0.contains("[U:1:") } && !conn.joined().contains("Something unrelated"),
                "no account, user name or Steam ID, and only lines about the logon, the connection and the licence list")
        let connBase = LogStore.shared.lines.count
        DockCEGService.logConnectionLog(drive: drive)
        require(LogStore.shared.lines.count - connBase == conn.count && logged("[steam-connection] connection_log.txt lines=55"), "logged with the [steam-connection] tag")

        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: all Dock CEG service Swift checks")
    }
}
'''

with tempfile.TemporaryDirectory(prefix='madeira-dock-ceg-service-') as tmp:
    tmp = Path(tmp)
    (tmp / 'stubs.swift').write_text(stubs, encoding='utf-8')
    (tmp / 'checks.swift').write_text(checks, encoding='utf-8')
    exe = tmp / 'check'
    registry_out = tmp / 'system.reg'
    if not Path(SWIFTC).exists() and not shutil.which(SWIFTC):
        if '--require-swift' in sys.argv:
            require(False, 'Swift compiler available (--require-swift)')
        else:
            print('SKIP: Swift compiler unavailable; the compiled CEG service checks did not run')
        build = None
    else:
        # DockReset's backup folder name comes from BCD's date helpers (BCDStore.swift, Foundation only; no ICU date formatter).
        store_source = (app / 'BCDStore.swift').read_text(encoding='utf-8')
        (tmp / 'dates.swift').write_text('import Foundation\n\nenum BCDFormat {\n' + store_source[
            store_source.index('    // dates-begin'):store_source.index('    // dates-end')] + '}\n', encoding='utf-8')
        build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-sanitize=address', '-o', str(exe),
                                str(tmp / 'stubs.swift'), str(tmp / 'checks.swift'), str(app / 'DockInstallers.swift'),
                                str(app / 'DockReset.swift'), str(tmp / 'dates.swift')])
        require(build.returncode == 0, 'production CEG service Swift compiles on the host')
    if build is not None and build.returncode == 0:
        run = subprocess.run([str(exe), str(registry_out)], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        require(run.returncode == 0, 'CEG service checks pass under AddressSanitizer')

    # ---- Wine's own reading of what the app writes
    services_c = wine / 'programs/services/services.c'
    unicode_c = wine / 'server/unicode.c'
    if registry_out.exists() and services_c.exists() and unicode_c.exists() and CC:
        reg = registry_out.read_text(encoding='utf-8')
        section = reg[reg.index('[System\\\\ControlSet001\\\\Services\\\\Steam Client Service]'):].strip().split('\n')
        svc = services_c.read_text(encoding='utf-8', errors='replace')
        written = set(re.findall(r'reg_set_(?:string|multisz|dword)_value\(\s*hKey,\s*L"(\w+)"', svc))
        loaded = set(re.findall(r'load_reg_(?:string|multisz|dword)\(\s*hKey,\s*L"(\w+)"', svc))
        names = [re.match(r'"(\w+)"=', l).group(1) for l in section[1:]]
        require(set(names) <= written and set(names) <= loaded | {'WOW64'},
                f'every value the app writes is one services.exe saves and loads ({names})')
        require({'ImagePath', 'Type', 'Start', 'ErrorControl'} <= set(names) and 'WOW64' in svc and 'save_service_config' in svc,
                'the values services.exe needs to load a Win32 service are there')
        # parse_strW from wine's server/unicode.c, with the helpers it needs, then the loader's steps for a key and its values.
        text = unicode_c.read_text(encoding='utf-8', errors='replace')
        fn = text[text.index('int parse_strW('):]
        fn = fn[:fn.index('\n}\n') + 3]
        tstart = text.index('static const char utf8_length[128]')
        tables = text[tstart:text.index(';', text.index('utf8_minval', tstart)) + 1]
        harness = r'''
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef uint16_t WCHAR;
typedef uint32_t data_size_t;
static inline char to_hex( char ch ) { if (isdigit((unsigned char)ch)) return ch - '0'; return tolower((unsigned char)ch) - 'a' + 10; }
''' + tables + '\n' + fn + r'''
static void narrow(const WCHAR *w, data_size_t bytes)
{
    for (data_size_t i = 0; i < bytes / 2 && w[i]; i++) putchar(w[i] < 0x80 ? (int)w[i] : '?');
}
int main(void)
{
    char line[4096];
    WCHAR buf[2048];
    while (fgets(line, sizeof(line), stdin))
    {
        line[strcspn(line, "\r\n")] = 0;
        data_size_t len = sizeof(buf);
        if (line[0] == '[')   /* load_key: the name up to "]", then the time */
        {
            int res = parse_strW(buf, &len, line + 1, ']');
            unsigned int mod = 0;
            if (res == -1 || sscanf(line + 1 + res, " %u", &mod) != 1) { puts("key: MALFORMED"); continue; }
            printf("key: "); narrow(buf, len); printf(" time=%u\n", mod);
        }
        else if (line[0] == '"')   /* parse_value_name, then the data type tag */
        {
            int res = parse_strW(buf, &len, line + 1, '"');
            if (res == -1) { puts("value: MALFORMED"); continue; }
            printf("value: "); narrow(buf, len); printf(" = ");
            const char *p = line + 1 + res;
            int expand = strncmp(p, "=str(2):\"", 9) == 0;
            if (expand || strncmp(p, "=\"", 2) == 0)
            {
                p += expand ? 9 : 2;
                len = sizeof(buf);
                if (parse_strW(buf, &len, p, '"') == -1) { puts("MALFORMED"); continue; }
                printf("%s ", expand ? "expand" : "sz"); narrow(buf, len); putchar('\n');
            }
            else if (strncmp(p, "=dword:", 7) == 0) printf("dword %lu\n", strtoul(p + 7, NULL, 16));
            else puts("UNKNOWN TYPE");
        }
    }
    return 0;
}
'''
        (tmp / 'wine_parse.c').write_text(harness)
        cc = subprocess.run([CC, '-std=gnu11', '-Wall', '-Wno-unused-function', '-Wno-implicit-fallthrough', '-fsanitize=address,undefined',
                             str(tmp / 'wine_parse.c'), '-o', str(tmp / 'wine_parse')], capture_output=True, text=True)
        require(cc.returncode == 0, "wineserver's parse_strW compiles from the pinned source" + ('' if cc.returncode == 0 else '\n' + cc.stderr[-1500:]))
        if cc.returncode == 0:
            parsed = subprocess.run([str(tmp / 'wine_parse')], input='\n'.join(section) + '\n', capture_output=True, text=True).stdout.strip().split('\n')
            want = [
                'key: System\\ControlSet001\\Services\\Steam Client Service time=1776969700',
                'value: DisplayName = sz Steam Client Service',
                'value: ErrorControl = dword 1',
                'value: ImagePath = expand "C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe" /RunAsService',
                'value: ObjectName = sz LocalSystem',
                'value: PreshutdownTimeout = dword 180000',
                'value: Start = dword 3',
                'value: Type = dword 16',
                'value: WOW64 = dword 1']
            require(parsed == want, "Wine's own string parser reads the key and every value back as intended")
            if parsed != want:
                print('  got:  ' + ' | '.join(parsed) + '\n  want: ' + ' | '.join(want))
    else:
        print('SKIP: no Wine source tree (WINE_SRC / wine/) or no C compiler; services.exe names and the string parser were not checked')

if failures:
    print(f'FAILURES: {failures}')
    sys.exit(1)
print('PASS: all Dock CEG service checks; device execution still required')
