#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright 2026 125hz
# Madeira Converter Exception: see LICENSE-EXCEPTION.md
"""Madeira Dock: a game's one-time installs before the host starts it.

Synthetic data only: no Steam, Wine, iOS or credentials. Compiles production
Swift (app/Madeira/DockInstallers.swift plus DockGame from MadeiraDock.swift)
under AddressSanitizer and checks:
  * install scripts: repeated "Run Process" sections, entries without a
    "HasRunKey" under Steam's per-app key, both registry views, argument and
    path rules;
  * the plan: done / missing / unsupported (by package type and PE machine
    against the bundle, never by name) / pending / per-start limit, and its note;
    madeira-bcd: a .NET Framework setup (told by Microsoft's file name, the one
    program name the plan reads) is "provided" by Wine Mono: not run, its record
    written like a finished run's; without Wine Mono it waits ("needs Wine Mono");
    MADEIRA_DOCK_DOTNET_MONO=0 runs it as before;
    madeira-bcd (round 579): a DirectX setup (dxsetup.exe) that already failed once in a game is provided by BCD from the next
    start (record written, not run; MADEIRA_DOCK_DIRECTX_PROVIDED=0 keeps it pending); one that succeeds, never ran, hung or never
    started is untouched; the one-time installs of a session that never started a program are offered once more instead of being
    skipped for good (GTA V Legacy / Halo MCC state machine; MADEIRA_DOCK_INSTALL_RETRY=0 skips them as before);
  * the batch: result file, service-manager step (or "services off"), every
    redirection after a space, nothing written to the registry by the batch;
  * the result parser and the progress text;
  * prepare() end to end in a synthetic prefix: case-insensitive paths, the
    One-time installs choice (a start that runs them turns the choice to Skip,
    Skip runs nothing), results recorded in system.reg (both views) at the next
    start, the per-session madsync request, fusion.dll placement, every switch;
  * madsync_enabled() from build/madsync/madsync.c with the production
    madeira_cfg.h: default on, MADEIRA_MADSYNC_SESSION=0 turns it off, nothing
    else does.
When Windows cmd.exe is reachable (WSL interop) the generated batch also runs for
real with stand-in installers, and with the staged dockhost.exe when it is built.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
CC = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


dock = (app / 'MadeiraDock.swift').read_text(encoding='utf-8')
installers = (app / 'DockInstallers.swift').read_text(encoding='utf-8')
content = (app / 'ContentView.swift').read_text(encoding='utf-8')
view = (app / 'MadeiraDockModel.swift').read_text(encoding='utf-8')
# Wave 3 (2026-10-19): the classic Dock sheet (MadeiraDockView, DockInstallRequestRows) and the game page's copy of its
# rows (SteamEntrySection) are deleted; BCD's Settings › BCD Dock section is where the choices are (BCDDock.swift,
# BCDDockLive.swift) and its starting screen shows the progress (BCDSessionLive.swift).
bcd_dock = (app / 'BCDDock.swift').read_text(encoding='utf-8')
bcd_session = (app / 'BCDSessionLive.swift').read_text(encoding='utf-8')
steam_games = (app / 'SteamGames.swift').read_text(encoding='utf-8')
madsync = (root / 'build/madsync/madsync.c').read_text(encoding='utf-8')

# ------------------------------------------------------------------ static rules
# madeira-bcd: the one program name the plan reads is Microsoft's .NET Framework file name (isDotNetFramework, with its
# comment): these setups never finished under Wine, and Wine Mono is the runtime. Every other redistributable stays unnamed.
net_fn = re.search(r'(?:    ///[^\n]*\n)+    static func isDotNetFramework\(.*?\n    \}\n', installers, re.S)
require(net_fn is not None, 'isDotNetFramework is one function with its comment')
# madeira-bcd (round 579, ajan7-installers): the second program name the plan reads is Microsoft's DirectX setup (isDirectXSetup), and
# only for a setup that already failed once in the game.
dx_fn = re.search(r'(?:    ///[^\n]*\n)+    static func isDirectXSetup\(.*?\n    \}\n', installers, re.S)
require(dx_fn is not None, 'isDirectXSetup is one function with its comment')
lower = installers
for fn in (net_fn, dx_fn):
    if fn:
        lower = lower.replace(fn.group(0), '')
lower = lower.lower()
require(not any(n in lower for n in ('dxsetup', 'vcredist', 'vc_redist', 'dotnetfx', 'dxwebsetup', 'physx', 'ndp4')),
        'no redistributable names outside isDotNetFramework and isDirectXSetup: nothing else is decided by program name')
require(net_fn is not None and not any(n in net_fn.group(0).lower() for n in ('dxsetup', 'vcredist', 'vc_redist', 'dxwebsetup', 'physx')),
        'isDotNetFramework names only .NET Framework files')
require(dx_fn is not None and re.findall(r'"([^"]+\.exe)"', dx_fn.group(0)) == ['dxsetup.exe', 'dxwebsetup.exe'],
        'isDirectXSetup names only the two DirectX setup files')
require('SteamSignIn.flag(name, default: true)' in installers, 'every installer switch defaults on and reads env.NAME')
for switch in ('MADEIRA_DOCK_INSTALLERS', 'MADEIRA_INSTALL_DEFAULT_KEY', 'MADEIRA_DOCK_INSTALL_CHOICE',
               'MADEIRA_DOCK_INSTALL_SCM', 'MADEIRA_DOCK_INSTALL_SERVER_SYNC', 'MADEIRA_DOTNET_FUSION', 'MADEIRA_DOCK_DOTNET_MONO',
               'MADEIRA_DOCK_DIRECTX_PROVIDED', 'MADEIRA_DOCK_INSTALL_RETRY'):
    require(f'flag("{switch}")' in installers, f'switch {switch} is read')
require('reg.exe' not in installers, 'the batch never writes the registry (no reg.exe in the bundle)')
require(content.count('setenv("MADEIRA_MADSYNC_SESSION", "0", 1)') == 1 and
        re.search(r'if DockInstallers\.serverSync \{\n\s*setenv\("MADEIRA_MADSYNC_SESSION", "0", 1\)', content) and
        'unsetenv("MADEIRA_MADSYNC_SESSION")' in content, 'madsync is turned off only for a Dock start that runs installers')
for name, text in [('MadeiraDock.swift', dock), ('MadeiraDockModel.swift', view), ('BCDDock.swift', bcd_dock)]:
    require('MADEIRA_MADSYNC_SESSION' not in text, f'{name}: does not touch the madsync session switch')
start = content.index('private func startDock(')
body = content[start:content.index('\n    }\n', start)]
require(body.index('try MadeiraDock.writeHandoff(') < body.index('DockInstallers.prepare(') < body.index('runWineFullSequence('),
        'installers are planned after the launch checks, before the session starts')
require('installers: DockInstallers.script' in body, 'the planned batch goes into the launch arguments')
require('DockInstallers.poll(drive: MadeiraDock.drive)' in bcd_session
        and '(value: true, title: "Run at next start"), (value: false, title: "Skip")' in bcd_dock,
        "BCD's Dock section shows the choice and the starting screen the progress")
require('Copyright 2026 125hz' in installers.split('\n', 3)[1], 'new file carries the owner copyright')
# Record as done / Reset: saved from the app, applied by prepare() before it reads the registry.
prep = installers[installers.index('    static func prepare('):]
prep = prep[:prep.index('\n    }\n')]
require(prep.index('absorbResults(') < prep.index('applyRequest(game, found: found, prefix: prefix, drive: drive, others: others)') < prep.index('let registry = '),
        'a saved request is applied after the last results and before the plan reads the registry')
require('var unfinished: [String: [String]]? = nil' in installers and 'var requests: [String: String]? = nil' in installers,
        'new ledger fields are optional (older files still load) and requests are stored by raw value')
require('var failed: [String: [String]]? = nil' in installers and 'var skipWhy: [String: String]? = nil' in installers and
        'var offeredAgain: [String]? = nil' in installers, 'the ledger fields of round 579 are optional too')
require('DockInstallRequestRows' not in steam_games and 'DockInstallRequestRows' not in view
        and 'title: "Record as done…"' in bcd_dock and 'title: "Reset…"' in bcd_dock
        and 'recordQuestion(game)' in bcd_dock and 'resetQuestion(game)' in bcd_dock,
        "BCD's Dock section offers Record as done and Reset, each confirmed")
require('[dock-reset]' in installers and 'still running' in installers and 'last-run=unfinished' in installers,
        'reset, stall and unfinished log tags')

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
/// A minimal PE header with the given machine.
func pe(_ url: URL, machine: UInt16) throws {
    var bytes = [UInt8](repeating: 0, count: 64 + 24)
    bytes[0] = 0x4d; bytes[1] = 0x5a; bytes[60] = 64
    bytes[64] = 0x50; bytes[65] = 0x45
    bytes[68] = UInt8(machine & 0xff); bytes[69] = UInt8(machine >> 8)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try Data(bytes).write(to: url)
}
@MainActor func logged(_ text: String) -> Bool { LogStore.shared.lines.contains { $0.contains(text) } }

@main struct Checks {
    @MainActor static func main() throws {
        setvbuf(stdout, nil, _IONBF, 0)
        // ---- install scripts
        let script = """
        "installscript"
        {
            "registry" { "HKEY_LOCAL_MACHINE\\\\SOFTWARE\\\\Fixture Vendor" { "string" { "Install Dir" "%INSTALLDIR%" } } }
            "run process"
            {
                "Runtime A"
                {
                    "process 1"   "%INSTALLDIR%\\\\_CommonRedist\\\\RuntimeA\\\\setup32.exe"
                    "command 1"   "/silent"
                }
            }
            "run process"
            {
                "Tool"
                {
                    "HasRunKey"   "HKEY_LOCAL_MACHINE\\\\SOFTWARE\\\\Fixture Tool"
                    "process 1"   "%INSTALLDIR%\\\\redist\\\\tool.exe"
                    "MinimumHasRunValue" "81017"
                }
                "Package" { "process 1" "%INSTALLDIR%/redist/pkg.msi" "command 1" "/qn" }
                "Gone" { "process 1" "%INSTALLDIR%\\\\missing.exe" }
                "Unsafe" { "process 1" "%INSTALLDIR%\\\\x.exe" "command 1" "/a & calc" }
                "Escape" { "process 1" "%INSTALLDIR%\\\\..\\\\..\\\\x.exe" }
                "NoProgram" { "NoCleanUp" "1" }
            }
            "run process on uninstall" { "Cleanup" { "process 1" "%INSTALLDIR%\\\\cleanup.exe" } }
        }
        """
        let dir = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Fixture Game"
        let withKey = DockInstallScripts.processes(script: Data(script.utf8), installDir: dir, appID: 7000)
        require(withKey.map(\.run.name) == ["runtime a", "tool", "package", "gone"], "repeated sections all count; unsafe, escaping and uninstall entries dropped (\(withKey.map(\.run.name)))")
        let withoutKey = DockInstallScripts.processes(script: Data(script.utf8), installDir: dir)
        require(withoutKey.map(\.run.name) == ["tool"], "without an app ID only HasRunKey entries count")
        let runtime = withKey[0], tool = withKey[1], package = withKey[2]
        require(runtime.run.hive == .machine && runtime.run.key == "Software\\Valve\\Steam\\Apps\\7000" && runtime.run.value == 1, "no HasRunKey: Steam's per-app key, value 1")
        require(tool.run.key == "SOFTWARE\\Fixture Tool" && tool.run.value == 81017, "HasRunKey and minimum kept")
        require(runtime.arguments == "/silent" && runtime.executable == dir + "\\_CommonRedist\\RuntimeA\\setup32.exe", "program and arguments expanded")
        require(package.executable == dir + "\\redist\\pkg.msi" && package.arguments == "/qn", "forward slashes become Windows separators")
        require(SteamInstallScripts.keys(runtime.run) == ["Software\\Valve\\Steam\\Apps\\7000", "Software\\Wow6432Node\\Valve\\Steam\\Apps\\7000"], "both registry views")
        require(SteamInstallScripts.keys(SteamInstallRun(name: "u", hive: .user, key: "Software\\X", value: 1)) == ["Software\\X"], "HKCU: one view")
        require(SteamInstallScripts.entries(script: Data(script.utf8), appID: 0).count == 1, "invalid app IDs add nothing")

        // ---- recorded values
        let record = """
        WINE REGISTRY Version 2

        [Software\\\\Wow6432Node\\\\Valve\\\\Steam\\\\Apps\\\\7000] 1700000000
        "runtime a"=dword:00000001

        [Software\\\\Wow6432Node\\\\Fixture Tool] 1700000000
        "Tool"=dword:00000010
        """
        require(DockInstallScripts.marked(runtime.run, in: record), "a Windows client's own record (32-bit view) counts")
        require(DockInstallScripts.recorded(tool.run, in: record) == 16 && !DockInstallScripts.marked(tool.run, in: record), "a value below the minimum is not done")
        let (marked, changed) = SteamInstallScripts.mark([tool.run], in: record, now: 1)
        require(changed == 2 && DockInstallScripts.recorded(tool.run, in: marked) == 81017 && DockInstallScripts.marked(tool.run, in: marked), "mark raises both views")
        require(SteamInstallScripts.mark([tool.run], in: marked, now: 2).changed == 0, "marking again changes nothing")
        let (unmarked, removedValues) = SteamInstallScripts.unmark([tool.run], in: marked)
        require(removedValues == 2 && DockInstallScripts.recorded(tool.run, in: unmarked) == nil &&
                unmarked.contains("[SOFTWARE\\\\Fixture Tool]") && DockInstallScripts.marked(runtime.run, in: unmarked),
                "unmark removes the entry's value from both views; keys and other entries stay")
        require(SteamInstallScripts.unmark([tool.run], in: unmarked).removed == 0, "unmarking again removes nothing")

        // ---- plan and note
        let none: (SteamInstallRun) -> Bool = { _ in false }
        var plan = DockInstallScripts.plan(withKey, done: none, exists: { _ in true })
        require(plan.map(\.status) == [.pending, .pending, .pending, .pending], "everything pending, whatever the program")
        plan = DockInstallScripts.plan(withKey, done: { $0.name == "tool" }, exists: { !$0.hasSuffix("missing.exe") },
                                       runnable: { $0.executable.hasSuffix(".msi") ? "Windows Installer package" : nil })
        require(plan.map(\.status) == [.pending, .done, .unsupported, .missing], "done, missing, unsupported, pending (\(plan.map(\.status.rawValue)))")
        let note = DockInstallScripts.note(plan) ?? ""
        require(note.contains("To run: runtime a") && note.contains("Done earlier: tool") && note.contains("Installer not found: gone") &&
                note.contains("Not supported by this build: package (Windows Installer package)"), "the note names every fate (\(note))")
        require(DockInstallScripts.note(DockInstallScripts.plan(withKey, done: { _ in true }, exists: { _ in true })) == nil, "all done: no note")
        plan = DockInstallScripts.plan(withKey, limit: 1, done: none, exists: { _ in true })
        require(plan.map(\.status) == [.pending, .limit, .limit, .limit], "per-start limit")
        require(DockInstallScripts.label(SteamInstallRun(name: "a&b|c>%d\"e f", hive: .machine, key: "k", value: 1)) == "abcde f", "labels keep only safe characters")

        // ---- the batch
        let result = "C:\\bcd-dock-installers.result"
        let r = " >>\"C:\\bcd-dock-installers.result\""
        let batch = DockInstallScripts.batch([runtime, package], resultFile: result, services: .start("C:\\windows\\system32\\dockhost.exe"))
        let lines = batch.components(separatedBy: "\r\n")
        require(batch.hasPrefix("@echo off\r\n") && batch.hasSuffix("\r\n"), "CRLF batch")
        let order = ["echo begin 2 >\"C:\\bcd-dock-installers.result\"",
                     "\"C:\\windows\\system32\\dockhost.exe\" --start-services" + r,
                     "if not \"%ERRORLEVEL%\"==\"0\" echo services failed" + r,
                     "echo start 1 runtime a" + r, "call \"" + runtime.executable + "\" /silent", "echo exit 1 %ERRORLEVEL%" + r,
                     "echo start 2 package" + r, "C:\\windows\\system32\\msiexec.exe /i \"" + package.executable + "\" /qn", "echo exit 2 %ERRORLEVEL%" + r,
                     "echo end" + r].map { lines.firstIndex(of: $0) }
        require(order.allSatisfy { $0 != nil } && zip(order, order.dropFirst()).allSatisfy { $0! < $1! }, "begin, services, each program with its exit, end")
        require(!lines.contains { $0.contains(">>\"") && !$0.contains(" >>\"") }, "every redirection follows a space")
        require(lines.filter { $0.contains("--start-services") }.count == 1, "services started once")
        let off = DockInstallScripts.batch([runtime], resultFile: result, services: .off)
        require(off.contains("echo services off" + r) && !off.contains("--start-services"), "services off: recorded, dockhost not run")
        require(!batch.lowercased().contains("reg.exe") && !batch.contains("errorlevel 1"), "the batch records nothing itself; no 'if not errorlevel 1'")

        // ---- results
        let parsed = DockInstallScripts.results("begin 3 \r\nservices started\r\nstart 1 runtime a \r\nexit 1 -9 \r\nstart 2 tool \r\nexit 2 3010 \r\nstart 3 package \r\n")
        require(parsed.total == 3 && parsed.exits == [1: -9, 2: 3010] && parsed.started[3] == "package" && parsed.services == "started", "parsed statuses and labels")
        require(parsed.running == 3 && !parsed.ended, "running program, not ended")
        require(!DockInstallScripts.Results.succeeded(-9) && DockInstallScripts.Results.succeeded(3010) &&
                DockInstallScripts.Results.succeeded(1641) && DockInstallScripts.Results.succeeded(0), "success statuses")
        require(DockInstallScripts.results("services timeout\n").services == "timeout" && DockInstallScripts.results("services st&a|rted>\n").services == "started", "service outcomes, letters only")
        require(DockInstallScripts.results("start 99 x\nexit abc\nbogus\nservices\n") == DockInstallScripts.Results(), "malformed lines ignored")

        // ---- ledger
        let base = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("dock-installers-\(getpid())")
        defer { try? FileManager.default.removeItem(at: base) }
        let prefix = base.appendingPathComponent("wine"), drive = prefix.appendingPathComponent("drive_c")
        try FileManager.default.createDirectory(at: drive, withIntermediateDirectories: true)
        require(DockInstallLedger.load(prefix: prefix) == DockInstallLedger() && DockInstallLedger().runsNext(1), "no file: empty ledger; a fresh game runs its installs")
        var ledger = DockInstallLedger(); ledger.session = [tool.run]; ledger.sessionApp = 7000; ledger.runNext["7000"] = false
        try ledger.save(prefix: prefix)
        require(DockInstallLedger.load(prefix: prefix) == ledger && !ledger.runsNext(7000), "JSON round trip")
        try write(prefix.appendingPathComponent(DockInstallLedger.fileName), "{not json")
        require(DockInstallLedger.load(prefix: prefix) == DockInstallLedger(), "a damaged file reads as empty")
        // A file written before the unfinished/requests fields existed still loads.
        try write(prefix.appendingPathComponent(DockInstallLedger.fileName), "{\"runNext\":{\"7000\":false},\"session\":[],\"sessionApp\":0}")
        let older = DockInstallLedger.load(prefix: prefix)
        require(!older.runsNext(7000) && older.requests == nil && older.unfinished == nil, "an older ledger file keeps its choice")
        try write(prefix.appendingPathComponent(DockInstallLedger.fileName), "{\"runNext\":{\"7000\":false},\"session\":[],\"sessionApp\":0,\"requests\":{\"7000\":\"later\"}}")
        let unknown = DockInstallLedger.load(prefix: prefix)
        require(!unknown.runsNext(7000) && unknown.request(7000) == nil, "an unknown request value reads as none, the choice stays")
        var requested = DockInstallLedger()
        requested.setRequest(7000, .record); requested.setUnfinished(7000, ["tool"])
        require(requested.request(7000) == .record && requested.unfinishedRuns(7000) == ["tool"], "request and unfinished entries kept per game")
        requested.setRequest(7000, nil); requested.setUnfinished(7000, [])
        require(requested == DockInstallLedger(), "cleared entries are stored as absent")

        // ---- paths and what this build can run
        let game = DockGame(id: 7000, name: "Fixture", installDir: "Fixture Game", library: "Program Files (x86)/Steam/steamapps",
                            installed: true, customExecutables: false)
        let common = drive.appendingPathComponent("Program Files (x86)/Steam/steamapps/common")
        let folder = common.appendingPathComponent("Fixture Game")
        try write(folder.appendingPathComponent("installscript.vdf"), script)
        try pe(folder.appendingPathComponent("_CommonRedist/RuntimeA/setup32.exe"), machine: 0x14c)
        try pe(folder.appendingPathComponent("Redist/Tool.EXE"), machine: 0x8664)
        try write(folder.appendingPathComponent("Redist/PKG.msi"), "msi")
        try write(common.appendingPathComponent("Steamworks Shared/_CommonRedist/Shared/2015/installscript.vdf"),
                  "\"installscript\" { \"run process\" { \"Shared Runtime\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\Software\\\\Fixture Shared\" \"process 1\" \"%INSTALLDIR%\\\\_CommonRedist\\\\Shared\\\\2015\\\\setup.exe\" } } }")
        try pe(common.appendingPathComponent("Steamworks Shared/_CommonRedist/Shared/2015/setup.exe"), machine: 0x8664)
        try write(folder.appendingPathComponent("bin/other.vdf"), "\"nothing\" { }")
        require(DockInstallers.resolve(dir + "\\redist\\tool.exe", drive: drive)?.lastPathComponent.lowercased() == "tool.exe", "paths resolve case-insensitively")
        require(DockInstallers.resolve(dir + "\\..\\..\\x.exe", drive: drive) == nil && DockInstallers.resolve("D:\\x.exe", drive: drive) == nil &&
                DockInstallers.resolve(dir + "\\nothing.exe", drive: drive) == nil, "parent references, other drives and absent files do not resolve")
        try FileManager.default.createSymbolicLink(at: folder.appendingPathComponent("link"), withDestinationURL: base)
        require(DockInstallers.resolve(dir + "\\link\\wine", drive: drive) == nil, "a link out of drive_c does not resolve")
        require(DockInstallers.machine(folder.appendingPathComponent("_CommonRedist/RuntimeA/setup32.exe")) == 0x14c &&
                DockInstallers.machine(folder.appendingPathComponent("Redist/PKG.msi")) == nil, "PE machine read")
        require(DockInstallers.runnable(runtime, drive: drive, has32Bit: false, hasMsiexec: false) == "32-bit installer" &&
                DockInstallers.runnable(runtime, drive: drive, has32Bit: true, hasMsiexec: false) == nil, "a 32-bit installer needs 32-bit support")
        require(DockInstallers.runnable(tool, drive: drive, has32Bit: false, hasMsiexec: false) == nil, "an x64 installer runs")
        require(DockInstallers.runnable(package, drive: drive, has32Bit: true, hasMsiexec: false) == "Windows Installer package" &&
                DockInstallers.runnable(package, drive: drive, has32Bit: false, hasMsiexec: true) == nil, "a package needs msiexec.exe")
        let (found, scripts) = DockInstallers.found(game, drive: drive, defaultKey: true)
        require(scripts == 2 && found.map(\.run.name) == ["runtime a", "tool", "package", "gone", "shared runtime"], "game and shared scripts (\(found.map(\.run.name)))")
        require(found[4].executable == "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Steamworks Shared\\_CommonRedist\\Shared\\2015\\setup.exe",
                "shared scripts expand %INSTALLDIR% to Steamworks Shared")
        require(DockInstallers.found(game, drive: drive, defaultKey: false).processes.map(\.run.name) == ["tool", "shared runtime"],
                "MADEIRA_INSTALL_DEFAULT_KEY=0: entries without a key are ignored")
        require(DockInstallers.programCount(game, drive: drive) == 5, "the sheet's program count")

        // ---- prepare(): a first start on a bundle without 32-bit support or msiexec
        MadeiraConfig.values["inproc-sync"] = "1"   // madsync chosen: the per-session request applies
        setenv("MADEIRA_DOCK_INSTALL_SEPARATE", "0", 1)   // the same session; the installs-only default is checked below
        try? FileManager.default.removeItem(at: prefix.appendingPathComponent(DockInstallLedger.fileName))
        try write(prefix.appendingPathComponent("system.reg"), "WINE REGISTRY Version 2\n\n[Software\\\\Wow6432Node\\\\Fixture Shared] 1700000000\n\"Shared Runtime\"=dword:00000001\n")
        try write(prefix.appendingPathComponent("user.reg"), "WINE REGISTRY Version 2\n")
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: false, hasMsiexec: false, fusionSource: nil)
        require(DockInstallers.script == "C:\\bcd-dock-installers.cmd" && DockInstallers.serverSync, "pending program: batch and a per-session madsync request")
        let written = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        require(written.contains("echo start 1 tool") && !written.contains("start 2") && written.contains("--start-services"), "the batch runs only the runnable pending program, after the service step")
        ledger = DockInstallLedger.load(prefix: prefix)
        require(ledger.session == [tool.run] && ledger.sessionApp == 7000 && !ledger.runsNext(7000), "session recorded; the choice turns to Skip")
        let firstNote = DockInstallers.note ?? ""
        require(firstNote.contains("To run: tool") && firstNote.contains("runtime a (32-bit installer)") && firstNote.contains("package (Windows Installer package)") &&
                firstNote.contains("Installer not found: gone") && firstNote.contains("Done earlier: shared runtime") && firstNote.contains("standard synchronization"),
                "the sheet's note (\(firstNote))")
        require(logged("dotnet-fusion=no-source") && logged("program=runtime a file=setup32.exe status=unsupported reason=32-bit-installer"), "fusion and per-program log lines")
        // madeira-bcd (ajan5-mhs3): the plan line names each program's PE machine (a failed installer of unknown kind, InstallerMessage.exe
        // of Monster Hunter Stories 3 Trial, left the log nothing to say whether it was 32-bit, x64 or ARM64).
        require(DockInstallers.machineText(tool, drive: drive) == "0x8664" && DockInstallers.machineText(runtime, drive: drive) == "0x14c" &&
                DockInstallers.machineText(package, drive: drive) == "-", "the PE machine of a program, a dash when it is not a PE file")
        require(logged("program=runtime a file=setup32.exe status=unsupported reason=32-bit-installer key=") && logged(" machine=0x14c") &&
                logged(" machine=0x8664") && logged("program=gone file=") && logged("status=missing") && logged(" machine=-"), "the plan lines end with the machine")
        require(MadeiraDockLaunch.arguments(DockInstallers.script) ==
                "/desktop=madeira,1280x720 C:\\windows\\system32\\cmd.exe /c call C:\\bcd-dock-installers.cmd & C:\\windows\\system32\\dockhost.exe",
                "installers first, then the host, in one session, no quoted tokens")
        require(DockInstallers.poll(drive: drive) == "Preparing this game's one-time installs…", "progress before the batch starts")
        try write(drive.appendingPathComponent("bcd-dock-installers.result"), "begin 1 \r\nservices started\r\nstart 1 tool \r\n")
        require(DockInstallers.poll(drive: drive) == "One-time install 1 of 1: tool is running…" && logged("services=started") && logged("start 1/1 program=tool"), "progress while running")
        require(DockInstallers.finishedAt == nil, "not finished while a program runs")
        let finished = "begin 1 \r\nservices started\r\nstart 1 tool \r\nexit 1 0 \r\nend \r\n"
        try write(drive.appendingPathComponent("bcd-dock-installers.result"), finished)
        require(DockInstallers.poll(drive: drive) == "Done with the one-time installs: 1 of 1 succeeded." && logged("exit 1/1 program=tool status=0 succeeded"), "progress when finished")
        let ended = DockInstallers.finishedAt
        _ = DockInstallers.poll(drive: drive)
        require(ended != nil && DockInstallers.finishedAt == ended &&
                LogStore.shared.lines.filter({ $0.contains("[dock-installers] end succeeded=1 failed=0 of=1; the host starts next") }).count == 1,
                "the end is logged once and its time kept (the starting screen times the host from it)")
        unsetenv("MADEIRA_DOCK_INSTALL_SEPARATE")
        // The words count what succeeded and name what failed (the text reads only the result file).
        try write(drive.appendingPathComponent("bcd-dock-installers.result"),
                  "begin 3 \r\nservices started\r\nstart 1 tool \r\nexit 1 0 \r\nstart 2 b \r\nexit 2 5 \r\nstart 3 c \r\nexit 3 -1073741819 \r\nend \r\n")
        require(DockInstallers.poll(drive: drive) == "Done with the one-time installs: 1 of 3 succeeded.\nFailed: b (exit 5), c (exit -1073741819)",
                "finished with failures: how many succeeded, which failed")
        // madeira-bcd (ajan5-mhs3): the failure's log line carries the status in hex and what it means; a program's own small exit code
        // stays as it was; the restart alert says a failed program is not run again (the choice turned to Skip) and how to try again.
        require(logged("program=b status=5 failed; not recorded") && !logged("program=b status=5 failed; not recorded;") &&
                logged("program=c status=-1073741819 failed; not recorded; exit 0xC0000005"), "a failed exit: the status in hex, nothing added for a small code")
        require(DockInstallScripts.failureDetail(0) == "" && DockInstallScripts.failureDetail(5) == "" &&
                DockInstallScripts.failureDetail(-1073741701).hasPrefix("; exit 0xC000007B (STATUS_INVALID_IMAGE_FORMAT: ") &&
                DockInstallScripts.failureDetail(-1073741515).hasPrefix("; exit 0xC0000135 (STATUS_DLL_NOT_FOUND") &&
                DockInstallScripts.failureDetail(-1073741819) == "; exit 0xC0000005", "failureDetail names the load failures and nothing else")
        let failedWords = "Done with the one-time installs: 0 of 1 succeeded.\nFailed: tool (exit -1073741701)"
        require(DockInstallers.skipsNextStart && DockInstallers.restartNotice(failedWords).hasPrefix(failedWords) &&
                DockInstallers.restartNotice(failedWords).contains("A program that failed is not run again: the game starts without it at the next start. To try it again, pick Run at next start under One-time installs.") &&
                DockInstallers.restartNotice(failedWords).hasSuffix("\nClose BCD and open it again to play the game."), "the restart alert after a failed install")
        require(!DockInstallers.restartNotice("Done with the one-time installs: 2 of 2 succeeded.").contains("not run again") &&
                DockInstallers.restartNotice(nil) == "The session for the one-time installs has ended.\nClose BCD and open it again to play the game.",
                "no such sentence without a failure")
        try write(drive.appendingPathComponent("bcd-dock-installers.result"), finished)

        // ---- the next start records the result and plans again: nothing is left to run
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: false, hasMsiexec: false, fusionSource: nil)
        let system = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        require(DockInstallScripts.recorded(tool.run, in: system) == 81017 && system.contains("[Software\\\\Wow6432Node\\\\Fixture Tool]") &&
                system.contains("[SOFTWARE\\\\Fixture Tool]"), "a program that exited 0 is recorded in both views at the next start")
        require(FileManager.default.fileExists(atPath: prefix.appendingPathComponent("system.reg.bcd-bak").path), "a backup of system.reg is kept once")
        require(logged("results reason=next-start succeeded=1 failed=0 recorded-values=2 services=started ended=1 statuses=tool=0"), "one summary line")
        require(DockInstallers.script == nil && !DockInstallers.serverSync && DockInstallers.finishedAt == nil,
                "nothing pending: no batch, madsync untouched, no installs' end")
        require(!FileManager.default.fileExists(atPath: drive.appendingPathComponent("bcd-dock-installers.result").path) &&
                DockInstallLedger.load(prefix: prefix).session.isEmpty, "result file and session list cleared")

        // ---- Skip, then Run at next start, with 32-bit support and fusion.dll
        let fusion = base.appendingPathComponent("bundle/i386-windows/fusion.dll")
        try write(fusion, "MZ fusion")
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: fusion)
        require(DockInstallers.script == nil && logged("choice=skip pending=1") && (DockInstallers.note ?? "").contains("skipped"), "Skip starts without the pending program")
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        require(DockInstallLedger.load(prefix: prefix).runsNext(7000), "Run at next start is saved")
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: fusion)
        let second = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        require(DockInstallers.script != nil && second.contains("echo start 1 runtime a") && second.contains("/silent"), "with 32-bit support the 32-bit installer runs")
        let placed = drive.appendingPathComponent("windows/Microsoft.NET/Framework/v2.0.50727/fusion.dll")
        require((try? String(contentsOf: placed, encoding: .utf8)) == "MZ fusion" && logged("dotnet-fusion=placed"), "fusion.dll placed before a start that runs installers")
        require(DockInstallers.placeDotNetFusion(drive: drive, source: fusion) == "present", "an existing fusion.dll is never replaced")
        // The batch did not run at all (the app was closed first): nothing recorded, the program is pending again.
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: fusion)
        require(logged("none: the batch did not start") && DockInstallers.script != nil, "a batch that never ran records nothing")

        // ---- switches
        setenv("MADEIRA_DOCK_INSTALL_CHOICE", "0", 1)
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: nil)
        require(DockInstallers.script != nil && DockInstallLedger.load(prefix: prefix).runNext["7000"] == nil, "MADEIRA_DOCK_INSTALL_CHOICE=0: runs every start, choice untouched")
        unsetenv("MADEIRA_DOCK_INSTALL_CHOICE")
        setenv("MADEIRA_DOCK_INSTALL_SERVER_SYNC", "0", 1)
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: nil)
        let keep = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        require(DockInstallers.script != nil && !DockInstallers.serverSync && keep.contains("echo services off") && !keep.contains("--start-services"),
                "MADEIRA_DOCK_INSTALL_SERVER_SYNC=0 with madsync: madsync kept, service step left out")
        MadeiraConfig.values["inproc-sync"] = "0"
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: nil)
        require(!DockInstallers.serverSync && ((try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? "").contains("--start-services"),
                "madsync already off in bcd.cfg: the service step runs without a session request")
        MadeiraConfig.values = [:]
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: nil)
        require(!DockInstallers.serverSync && ((try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? "").contains("--start-services")
                && !(DockInstallers.note ?? "").contains("standard synchronization"),
                "no inproc-sync (fastsync, the default engine): the service step runs, no madsync note")
        require((DockInstallers.note ?? "").contains("This start runs only the installs. Restart BCD after them, then start the game again.")
                && logged("this session runs only the one-time installs"),
                "MADEIRA_DOCK_INSTALL_SEPARATE (on by default): a start that runs installers runs only them")
        unsetenv("MADEIRA_DOCK_INSTALL_SERVER_SYNC")
        setenv("MADEIRA_DOCK_INSTALL_SCM", "0", 1)
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: nil)
        require(((try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? "").contains("echo services off"),
                "MADEIRA_DOCK_INSTALL_SCM=0: services off")
        unsetenv("MADEIRA_DOCK_INSTALL_SCM")
        try? FileManager.default.removeItem(at: placed)
        setenv("MADEIRA_DOTNET_FUSION", "0", 1)
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: false, fusionSource: fusion)
        require(!FileManager.default.fileExists(atPath: placed.path), "MADEIRA_DOTNET_FUSION=0 leaves the folder alone")
        unsetenv("MADEIRA_DOTNET_FUSION")
        setenv("MADEIRA_DOCK_INSTALLERS", "0", 1)
        DockInstallers.setRunsNext(7000, true, prefix: prefix)
        DockInstallers.prepare(game, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, fusionSource: nil)
        require(DockInstallers.script == nil && !DockInstallers.serverSync && DockInstallers.note == nil && !DockInstallers.choiceEnabled &&
                DockInstallers.programCount(game, drive: drive) == 0, "MADEIRA_DOCK_INSTALLERS=0: nothing runs, nothing is shown")
        unsetenv("MADEIRA_DOCK_INSTALLERS")

        // ---- per-start limit keeps the choice
        var many = "\"installscript\" { \"run process\" {"
        for i in 1...9 { many += " \"P\(i)\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\Software\\\\Many\" \"process 1\" \"%INSTALLDIR%\\\\p\(i).exe\" }" }
        many += " } }"
        let big = DockGame(id: 7001, name: "Many", installDir: "Many", library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
        try write(common.appendingPathComponent("Many/installscript.vdf"), many)
        for i in 1...9 { try pe(common.appendingPathComponent("Many/p\(i).exe"), machine: 0x8664) }
        DockInstallers.prepare(big, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, fusionSource: nil)
        require(DockInstallLedger.load(prefix: prefix).session.count == 8 && DockInstallLedger.load(prefix: prefix).runsNext(7001) &&
                (DockInstallers.note ?? "").contains("Left for the next start: p9"), "over the limit: 8 run, the rest next start, choice stays Run")

        // ---- an installer that never closes: the stall notice, its unfinished record, Record as done, Reset
        let solo = DockGame(id: 7002, name: "Solo", installDir: "Solo", library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
        try write(common.appendingPathComponent("Solo/installscript.vdf"),
                  "\"installscript\" { \"run process\" { \"Setup\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\Software\\\\Solo\" \"process 1\" \"%INSTALLDIR%\\\\setup.exe\" } } }")
        try pe(common.appendingPathComponent("Solo/setup.exe"), machine: 0x8664)
        let soloFound = DockInstallers.found(solo, drive: drive, defaultKey: true).processes
        let setupRun = soloFound.first(where: { $0.run.name == "setup" })!.run
        let sharedRun = soloFound.first(where: { $0.run.name == "shared runtime" })!.run
        require(DockInstallers.own(soloFound, game: solo) == [setupRun], "Record as done / Reset act on the game's own programs, not shared redistributables")
        DockInstallers.prepare(solo, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, fusionSource: nil)
        require(DockInstallers.script != nil && !DockInstallLedger.load(prefix: prefix).runsNext(7002), "first start runs the game's installer")
        try write(drive.appendingPathComponent("bcd-dock-installers.result"), "begin 1 \r\nservices started\r\nstart 1 setup \r\n")
        let t0 = Date()
        require(DockInstallers.poll(drive: drive, now: t0) == "One-time install 1 of 1: setup is running…", "a fresh run: no stall words")
        // madeira-bcd (round 624, 7NKYWT): two minutes in an installs-only session: the time in the status, the way out when the next start
        // skips the installs, and one log line naming the app and the program.
        require(DockInstallers.poll(drive: drive, now: t0.addingTimeInterval(100)) == "One-time install 1 of 1: setup is running…",
                "100 s: no words yet")
        let slow = DockInstallers.poll(drive: drive, now: t0.addingTimeInterval(130)) ?? ""
        require(!DockInstallers.installsOnly ||
                (slow.hasPrefix("One-time install 1 of 1: setup is running… (2 min 10 s)") &&
                 slow.contains("the next start skips the installs and starts the game.") == DockInstallers.skipsNextStart &&
                 !slow.contains("If its window has stopped changing")),
                "an installs-only session after 130 s: the time, the way out only when the next start skips the installs (\(slow))")
        _ = DockInstallers.poll(drive: drive, now: t0.addingTimeInterval(200))
        require(!DockInstallers.installsOnly ||
                (LogStore.shared.lines.filter({ $0.contains("[dock-installers] app=7002 install still running after 130 s: program=setup") }).count == 1 &&
                 !logged("install still running after 200 s")), "the two-minute line is logged once, with the app and the program")
        let stalled = DockInstallers.poll(drive: drive, now: t0.addingTimeInterval(601)) ?? ""
        require(stalled.hasPrefix("One-time install 1 of 1: setup is running… (10 min)\nIf its window has stopped changing") && stalled.contains("Record as done"),
                "after 10 minutes on one program the text names the minutes and the way out (\(stalled))")
        _ = DockInstallers.poll(drive: drive, now: t0.addingTimeInterval(900))
        require(LogStore.shared.lines.filter({ $0.contains("[dock-installers] still running 1/1 program=setup after 601 s") }).count == 1 &&
                !logged("after 900 s"), "the stall is logged once per program")
        // The session ended with the installer still open: the next start keeps it unfinished.
        DockInstallers.prepare(solo, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, fusionSource: nil)
        require(logged("statuses=setup=unfinished") && DockInstallLedger.load(prefix: prefix).unfinishedRuns(7002) == ["setup"], "an unfinished program is remembered")
        require(DockInstallers.script == nil && logged("program=setup file=setup.exe status=pending") && logged("last-run=unfinished") &&
                logged("app=7002 unfinished=setup") && (DockInstallers.note ?? "").contains("Not finished last time: setup") &&
                (DockInstallers.note ?? "").contains("Record as done"), "Skip: the log and the note name the unfinished program (\(DockInstallers.note ?? ""))")
        // Record as done: saved now, applied at the next start.
        DockInstallers.setRequest(7002, .record, prefix: prefix)
        var soloSystem = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        require(DockInstallLedger.load(prefix: prefix).request(7002) == .record && !DockInstallScripts.marked(setupRun, in: soloSystem),
                "a request changes nothing in the prefix until the next start")
        DockInstallers.setRequest(7002, nil, prefix: prefix)
        require(DockInstallLedger.load(prefix: prefix).requests == nil, "a request can be cancelled")
        DockInstallers.setRequest(7002, .record, prefix: prefix)
        DockInstallers.prepare(solo, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, fusionSource: nil)
        soloSystem = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        require(logged("[dock-installers] app=7002 record-as-done: records written programs=setup values=2 shared-kept=1") &&
                DockInstallScripts.marked(setupRun, in: soloSystem) && DockInstallers.script == nil && DockInstallers.note == nil,
                "Record as done: recorded in both views before the plan, nothing runs")
        ledger = DockInstallLedger.load(prefix: prefix)
        require(ledger.request(7002) == nil && ledger.unfinishedRuns(7002).isEmpty, "the request and the unfinished entry are cleared")
        // madeira-bcd: other games of the prefix. Twin's own installer records under Solo's key,
        // Triplet runs its own copy of Solo's setup.exe, Other shares neither (shareReset).
        func extra(_ id: Int, _ name: String, _ script: String, _ file: String) throws -> (DockGame, [SteamInstallRun]) {
            let game = DockGame(id: id, name: name, installDir: name, library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
            try write(common.appendingPathComponent(name + "/installscript.vdf"), script)
            try pe(common.appendingPathComponent(name + "/" + file), machine: 0x8664)
            let runs = DockInstallers.own(DockInstallers.found(game, drive: drive, defaultKey: true).processes, game: game)
            _ = try SteamInstallScripts.mark(runs, prefix: prefix)
            return (game, runs)
        }
        let (twin, twinRuns) = try extra(7003, "Twin", "\"installscript\" { \"run process\" { \"Helper\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\Software\\\\Solo\" \"process 1\" \"%INSTALLDIR%\\\\helper.exe\" } } }", "helper.exe")
        let (triplet, tripletRuns) = try extra(7004, "Triplet", "\"installscript\" { \"run process\" { \"Copy\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\Software\\\\Triplet\" \"process 1\" \"%INSTALLDIR%\\\\Setup.exe\" } } }", "Setup.exe")
        let (other, otherRuns) = try extra(7005, "Other", "\"installscript\" { \"run process\" { \"Own\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\Software\\\\Other\" \"process 1\" \"%INSTALLDIR%\\\\own.exe\" } } }", "own.exe")
        DockInstallers.setRunsNext(7003, false, prefix: prefix); DockInstallers.setRunsNext(7004, false, prefix: prefix); DockInstallers.setRunsNext(7005, false, prefix: prefix)
        require(twinRuns.count == 1 && tripletRuns.count == 1 && otherRuns.count == 1, "the other games each have one own program")
        // Reset: the records go, the choice turns to Run, and the installer runs at that start.
        // madeira-bcd: what the installers put in moves into a backup first (DockReset.swift;
        // this prefix has none of it, so nothing moves).
        DockInstallers.setRequest(7002, .reset, prefix: prefix)
        DockInstallers.prepare(solo, drive: drive, prefix: prefix, others: [solo, twin, triplet, other], has32Bit: true, hasMsiexec: true, fusionSource: nil)
        soloSystem = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        let rerun = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        require(logged("[dock-reset] app=7002 reset: installed components moved to the backup, records removed, choice=run programs=setup values=2 shared-kept=1") &&
                DockInstallScripts.recorded(setupRun, in: soloSystem) == nil && DockInstallScripts.marked(sharedRun, in: soloSystem),
                "Reset removes only the game's own records; the shared redistributable stays recorded")
        require(logged("[dock-reset] app=7002 clean sign-out=0 moved=0 entries=0 keys=0 values=0 template-keys=0 backup=Documents/bcd-reset-backup/") &&
                FileManager.default.fileExists(atPath: base.appendingPathComponent("bcd-reset-backup").path),
                "Reset backs up the registry next to the prefix and moves nothing the installers did not put in")
        let shareLedger = DockInstallLedger.load(prefix: prefix)
        require(logged("[dock-reset] app=7002 also app=7003 programs=helper values=2: records removed, choice=run") &&
                logged("[dock-reset] app=7002 also app=7004 programs=copy values=2: records removed, choice=run") && !logged("also app=7005") &&
                DockInstallScripts.recorded(twinRuns[0], in: soloSystem) == nil && DockInstallScripts.recorded(tripletRuns[0], in: soloSystem) == nil &&
                DockInstallScripts.marked(otherRuns[0], in: soloSystem) && shareLedger.runsNext(7003) && shareLedger.runsNext(7004) && !shareLedger.runsNext(7005),
                "the reset's installs go back to Run for games under the same key or with the same installer, not for others")
        require(DockInstallers.script != nil && rerun.contains("echo start 1 setup") && !DockInstallLedger.load(prefix: prefix).runsNext(7002) &&
                DockInstallLedger.load(prefix: prefix).requests == nil, "after Reset the installer runs at that start, then the choice is Skip again")

        // ---- madeira-bcd: a .NET Framework setup is provided by Wine Mono (not run, its record written); without Wine Mono it waits
        let netNames: [(String, Bool)] = [
            ("C:\\Program Files (x86)\\Steam\\steamapps\\common\\G\\Setup\\DotNetFx35.exe", true), ("C:\\x\\dotnetfx35.exe", true),
            ("C:\\x\\dotNetFx35setup.exe", true), ("C:\\x\\dotNetFx40_Full_setup.exe", true), ("C:\\x\\dotnetfx.exe", true),
            ("C:\\x\\NDP472-KB4054530-x86-x64-AllOS-ENU.exe", true), ("C:\\x\\ndp48-x86-x64-allos-enu.exe", true), ("C:\\x\\NetFx20SP2_x86.exe", true),
            ("C:\\x\\dotnetfx.msi", false), ("C:\\x\\dotnet-runtime-8.0.1-win-x64.exe", false), ("C:\\x\\windowsdesktop-runtime-6.0.0-win-x64.exe", false),
            ("C:\\x\\ndpsetup.exe", false), ("C:\\x\\netfxsomething.exe", false), ("C:\\x\\vcredist_x86.exe", false), ("C:\\x\\dxsetup.exe", false),
            ("C:\\x\\mydotnetfx.exe", false), ("C:\\dotnetfx\\setup.exe", false), ("C:\\x\\setup32.exe", false),
        ]
        require(netNames.allSatisfy { DockInstallScripts.isDotNetFramework($0.0) == $0.1 },
                ".NET Framework setups are told by Microsoft's file name; modern runtimes, packages and other redistributables are not (\(netNames.filter { DockInstallScripts.isDotNetFramework($0.0) != $0.1 }.map(\.0)))")
        func netScript(_ ndpKey: String) -> String {
            "\"installscript\" { \"run process\" {" +
            " \"dotnet\" { \"process 1\" \"%INSTALLDIR%\\\\Setup\\\\DotNetFx35.exe\" \"command 1\" \"/q\" }" +
            " \"runtime\" { \"process 1\" \"%INSTALLDIR%\\\\Setup\\\\vc.exe\" }" +
            " \"mixed\" { \"process 1\" \"%INSTALLDIR%\\\\Setup\\\\dotnetfx.exe\" \"process 2\" \"%INSTALLDIR%\\\\Setup\\\\after.exe\" }" +
            " \"modern\" { \"process 1\" \"%INSTALLDIR%\\\\Setup\\\\dotnet-runtime-8.0.1-win-x64.exe\" }" +
            " \"ndp\" { \"HasRunKey\" \"HKEY_LOCAL_MACHINE\\\\" + ndpKey + "\" \"process 1\" \"%INSTALLDIR%\\\\Setup\\\\NDP472-KB4054530-x86-x64-AllOS-ENU.exe\" }" +
            " } }"
        }
        // Game 7010 uses Microsoft's own NDP key as its HasRunKey (Terraria does); the others each have a key of their own.
        func netGame(_ id: Int, _ name: String) throws -> DockGame {
            let g = DockGame(id: id, name: name, installDir: name, library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
            try write(common.appendingPathComponent(name + "/installscript.vdf"),
                      netScript(id == 7010 ? "SOFTWARE\\\\Microsoft\\\\NET Framework Setup\\\\NDP\\\\v4\\\\Full" : "SOFTWARE\\\\Fixture NDP \(id)"))
            for file in ["DotNetFx35.exe", "vc.exe", "dotnetfx.exe", "after.exe", "dotnet-runtime-8.0.1-win-x64.exe", "NDP472-KB4054530-x86-x64-AllOS-ENU.exe"] {
                try pe(common.appendingPathComponent(name + "/Setup/" + file), machine: 0x14c)
            }
            return g
        }
        let netFound = DockInstallers.found(try netGame(7010, "Net"), drive: drive, defaultKey: true).processes
        var netPlan = DockInstallScripts.plan(netFound, done: { _ in false }, exists: { _ in true },
                                              provided: { DockInstallScripts.isDotNetFramework($0.executable) })
        require(netPlan.map(\.status) == [.provided, .pending, .provided, .pending, .pending, .provided, .pending],
                "plan: the .NET Framework setups are provided, the rest pending (\(netPlan.map(\.status.rawValue)))")
        netPlan = DockInstallScripts.plan(netFound, done: { $0.name == "dotnet" }, exists: { _ in true }, provided: { _ in true })
        require(netPlan.first?.status == .done && netPlan[1].status == .provided, "plan: done comes before provided")
        netPlan = DockInstallScripts.plan(netFound, done: { _ in false }, exists: { !$0.hasSuffix("DotNetFx35.exe") },
                                          provided: { DockInstallScripts.isDotNetFramework($0.executable) })
        require(netPlan.first?.status == .missing, "plan: a setup that is not on disk is missing, not provided")
        let netNote = DockInstallScripts.note(DockInstallScripts.plan(netFound, done: { _ in false }, exists: { _ in true },
                                                                       provided: { DockInstallScripts.isDotNetFramework($0.executable) })) ?? ""
        require(netNote.contains("Provided by Wine Mono, not run: dotnet, mixed, ndp") && netNote.contains("To run: runtime, mixed, modern"), "the note names what Wine Mono provides (\(netNote))")
        let runs = DockInstallScripts.providedRuns(DockInstallScripts.plan(netFound, done: { _ in false }, exists: { _ in true },
                                                                           provided: { DockInstallScripts.isDotNetFramework($0.executable) }))
        require(runs.record.map(\.name) == ["dotnet", "ndp"] && runs.held.map(\.name) == ["mixed"],
                "an entry is recorded only when all its programs are provided or done; the others are held back (\(runs.record.map(\.name)) \(runs.held.map(\.name)))")

        // prepare(): with Wine Mono the .NET setups are not in the batch and are recorded in both views
        let netGameWith = try netGame(7010, "Net")
        DockInstallers.prepare(netGameWith, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, hasWineMono: true, fusionSource: nil)
        let netBatch = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        require(DockInstallers.script != nil && netBatch.contains("vc.exe") && netBatch.contains("after.exe") && netBatch.contains("dotnet-runtime-8.0.1-win-x64.exe") &&
                !netBatch.lowercased().contains("dotnetfx") && !netBatch.contains("NDP472"), "the batch holds the other programs and no .NET Framework setup")
        var netSystem = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        let netRuns = netFound.map(\.run)
        require(DockInstallScripts.marked(netRuns[0], in: netSystem) && netSystem.contains("[Software\\\\Wow6432Node\\\\Valve\\\\Steam\\\\Apps\\\\7010]") &&
                netSystem.contains("[Software\\\\Valve\\\\Steam\\\\Apps\\\\7010]"), "dotnet: recorded under Valve's per-app key, both views")
        require(DockInstallScripts.marked(netRuns[5], in: netSystem) && netSystem.contains("[SOFTWARE\\\\Microsoft\\\\NET Framework Setup\\\\NDP\\\\v4\\\\Full]"),
                "ndp: recorded under its own HasRunKey (Microsoft's NDP key)")
        require(!DockInstallScripts.marked(netRuns[1], in: netSystem) && !DockInstallScripts.marked(netRuns[2], in: netSystem) &&
                !DockInstallScripts.marked(netRuns[4], in: netSystem), "the programs that still run, and an entry with one of them, are not recorded")
        require(logged("app=7010 program=dotnet skipped: provided by Wine Mono; file=DotNetFx35.exe") && logged("record: yes, ") &&
                logged("app=7010 program=mixed skipped: provided by Wine Mono; file=dotnetfx.exe") && logged("record: no, another program of this entry still has to run") &&
                logged("program=dotnet file=DotNetFx35.exe status=provided key=HKLM\\Software\\Valve\\Steam\\Apps\\7010 recorded=- minimum=1") &&
                logged("app=7010 scripts=2 programs=7 pending=3 done=1 missing=0 unsupported=0 provided=3 "), "the log names each skipped program and the summary counts them")
        require((DockInstallers.note ?? "").contains("Provided by Wine Mono, not run: dotnet, mixed, ndp"), "the sheet's note says what Wine Mono provides")
        // The next start: what was recorded is done, nothing is skipped again.
        let beforeLines = LogStore.shared.lines.count
        DockInstallers.prepare(netGameWith, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, hasWineMono: true, fusionSource: nil)
        let afterLines = LogStore.shared.lines.dropFirst(beforeLines).joined(separator: "\n")
        require(afterLines.contains("program=dotnet file=DotNetFx35.exe status=done") && afterLines.contains("program=ndp file=NDP472-KB4054530-x86-x64-AllOS-ENU.exe status=done") &&
                afterLines.contains("program=mixed file=dotnetfx.exe status=provided") && !afterLines.contains("program=dotnet skipped"), "the next start: recorded setups are done, an entry held back is provided again")
        // Without Wine Mono the setup is held back, not recorded, and the note says where to get it.
        let noMono = try netGame(7011, "NoMono")
        DockInstallers.prepare(noMono, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, hasWineMono: false, fusionSource: nil)
        let noMonoBatch = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        netSystem = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        let noMonoRuns = DockInstallers.found(noMono, drive: drive, defaultKey: true).processes.map(\.run)
        require(!noMonoBatch.lowercased().contains("dotnetfx") && !noMonoBatch.contains("NDP472") && noMonoBatch.contains("vc.exe") &&
                DockInstallScripts.recorded(noMonoRuns[0], in: netSystem) == nil && DockInstallScripts.recorded(noMonoRuns[5], in: netSystem) == nil,
                "no Wine Mono: the .NET Framework setups do not run and are not recorded")
        require(logged("app=7011 program=dotnet file=DotNetFx35.exe status=unsupported reason=needs-Wine-Mono;-download-it-in-Settings-›-.NET-Framework") &&
                logged("app=7011 program=dotnet skipped: file=DotNetFx35.exe is a .NET Framework setup and this device has no Wine Mono") &&
                (DockInstallers.note ?? "").contains("Not supported by this build: dotnet (needs Wine Mono; download it in Settings › .NET Framework)") &&
                !logged("app=7011 program=dotnet skipped: provided by Wine Mono"), "no Wine Mono: the note and the log say to download it in Settings › .NET Framework")
        // MADEIRA_DOCK_DOTNET_MONO=0: as before, the setups run.
        setenv("MADEIRA_DOCK_DOTNET_MONO", "0", 1)
        let legacy = try netGame(7012, "Legacy")
        DockInstallers.prepare(legacy, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, hasWineMono: true, fusionSource: nil)
        let legacyBatch = (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? ""
        require(legacyBatch.contains("DotNetFx35.exe") && legacyBatch.contains("NDP472") && !logged("app=7012 program=dotnet skipped") &&
                logged("app=7012 scripts=2 programs=7 pending=6 done=1 missing=0 unsupported=0 provided=0 "), "MADEIRA_DOCK_DOTNET_MONO=0: the .NET Framework setups run as before")
        // A stalled installer: the way out is named (the choice is Skip for the next start).
        try write(drive.appendingPathComponent("bcd-dock-installers.result"), "begin 6 \r\nservices started\r\nstart 1 dotnet \r\n")
        let tNet = Date()
        _ = DockInstallers.poll(drive: drive, now: tNet)
        let stalledNet = DockInstallers.poll(drive: drive, now: tNet.addingTimeInterval(601)) ?? ""
        require(stalledNet.contains("If it never finishes, close BCD (swipe it away in the app switcher) and open it again: the next start skips the installs and starts the game."),
                "a stalled installer: the text names the way out")
        unsetenv("MADEIRA_DOCK_DOTNET_MONO")
        // An entry an earlier build left "unfinished" is cleared once it is recorded as provided.
        let stale = try netGame(7013, "Stale")
        var staleLedger = DockInstallLedger.load(prefix: prefix); staleLedger.setUnfinished(7013, ["dotnet", "runtime"]); try staleLedger.save(prefix: prefix)
        DockInstallers.prepare(stale, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, hasWineMono: true, fusionSource: nil)
        require(DockInstallLedger.load(prefix: prefix).unfinishedRuns(7013) == ["runtime"],
                "an entry recorded as provided is no longer unfinished")
        require(!(DockInstallers.note ?? "").contains("Not finished last time: dotnet"), "the note does not call a provided setup unfinished")

        // ---- madeira-bcd (round 579, ajan7-installers): DirectX's setup that failed once, and the choice after a session that never started
        require(["C:\\x\\DXSETUP.exe", "C:\\x\\DXSetup.exe", "C:\\x\\dxsetup.exe", "C:\\x\\DXWebSetup.exe", "dxsetup.exe"].allSatisfy { DockInstallScripts.isDirectXSetup($0) } &&
                !["C:\\x\\dxsetup.msi", "C:\\x\\mydxsetup.exe", "C:\\DirectX\\setup.exe", "C:\\x\\dxsetup.exe.bak", "C:\\x\\vcredist_x86.exe",
                  "C:\\x\\DotNetFx35.exe", "C:\\dxsetup.exe\\x.exe", "C:\\x\\dxsetup64.exe"].contains { DockInstallScripts.isDirectXSetup($0) },
                "DirectX's setup is told by Microsoft's file name only")
        let dxProcess = SteamInstallProcess(run: SteamInstallRun(name: "directx", hive: .machine, key: "Software\\Valve\\Steam\\Apps\\7500", value: 1),
                                            executable: "C:\\g\\DirectX\\DXSETUP.exe", arguments: "/silent")
        let netProcess = SteamInstallProcess(run: SteamInstallRun(name: "dotnet", hive: .machine, key: "Software\\Valve\\Steam\\Apps\\7500", value: 1),
                                             executable: "C:\\g\\DotNetFx35.exe", arguments: "/q")
        let dxPlan = DockInstallScripts.plan([dxProcess, netProcess], done: { _ in false }, exists: { _ in true }, provided: { _ in true })
        let dxNote = DockInstallScripts.note(dxPlan) ?? ""
        require(DockInstallScripts.provider(dxProcess) == "BCD" && DockInstallScripts.provider(netProcess) == "Wine Mono" &&
                dxNote.contains("Provided by Wine Mono, not run: dotnet") && dxNote.contains("Provided by BCD, not run: directx") &&
                !dxNote.contains("Provided by Wine Mono, not run: dotnet, directx"), "the note names who provides what (\(dxNote))")
        let plainNote = DockInstallScripts.note(DockInstallScripts.plan([dxProcess], done: { _ in false }, exists: { _ in true })) ?? ""
        require(plainNote == "One-time installs for this start. To run: directx." &&
                DockInstallScripts.plan([dxProcess], done: { _ in false }, exists: { _ in true }).map(\.status) == [.pending],
                "without the provided closure a DirectX setup is planned like any program")

        // The ledger's new fields are optional: an older file loads, a new one round-trips (the ledger the earlier checks left is put back).
        let ledgerBefore = DockInstallLedger.load(prefix: prefix)
        try write(prefix.appendingPathComponent(DockInstallLedger.fileName), "{\"runNext\":{\"7000\":false},\"session\":[],\"sessionApp\":0}")
        let oldLedger = DockInstallLedger.load(prefix: prefix)
        require(oldLedger.failed == nil && oldLedger.skipWhy == nil && oldLedger.offeredAgain == nil && oldLedger.whySkip(7000) == nil && oldLedger.failedRuns(7000).isEmpty,
                "an older ledger file loads without the round 579 fields")
        var fresh = DockInstallLedger()
        fresh.setFailed(7000, ["directx"]); fresh.setSkipWhy(7000, "failed"); fresh.offeredAgain = ["7000"]
        try fresh.save(prefix: prefix)
        require(DockInstallLedger.load(prefix: prefix) == fresh && fresh.failedRuns(7000) == ["directx"] && fresh.whySkip(7000) == "failed", "the new fields round-trip")
        fresh.setFailed(7000, []); fresh.setSkipWhy(7000, nil); fresh.offeredAgain = nil
        require(fresh == DockInstallLedger(), "cleared fields are stored as absent")
        try ledgerBefore.save(prefix: prefix)

        // settleChoice: the decision, on its own.
        func settle(_ why: String?, runNext: Bool, again: [String]?, _ outcome: DockSessionOutcome, retry: Bool = true) -> (offered: Bool, ledger: DockInstallLedger) {
            var l = DockInstallLedger()
            l.runNext["7000"] = runNext ? nil : false; l.setSkipWhy(7000, why); l.offeredAgain = again
            let offered = l.settleChoice(7000, after: outcome, retry: retry)
            return (offered, l)
        }
        let nothing = settle("install-start", runNext: false, again: nil, .nothingRan)
        require(nothing.offered && nothing.ledger.runsNext(7000) && nothing.ledger.whySkip(7000) == nil && nothing.ledger.offeredAgain == ["7000"],
                "settleChoice: a session in which nothing ran turns the choice back to Run, once")
        let twice = settle("install-start", runNext: false, again: ["7000"], .nothingRan)
        require(!twice.offered && !twice.ledger.runsNext(7000) && twice.ledger.whySkip(7000) == "nothing-ran", "settleChoice: not offered a second time (no loop)")
        for outcome in [DockSessionOutcome.failed, .unfinished, .incomplete, .ok] {
            let r = settle("install-start", runNext: false, again: ["7000"], outcome)
            require(!r.offered && !r.ledger.runsNext(7000) && r.ledger.whySkip(7000) == outcome.rawValue && r.ledger.offeredAgain == nil,
                    "settleChoice: \(outcome.rawValue) stays Skip, the reason is kept, the offer counter is cleared")
        }
        require(!settle("picked", runNext: false, again: nil, .nothingRan).offered && !settle(nil, runNext: false, again: nil, .nothingRan).offered &&
                settle("picked", runNext: false, again: nil, .nothingRan).ledger.whySkip(7000) == "picked",
                "settleChoice: a choice the user made, or one set before these fields, is left alone")
        let noRetry = settle("install-start", runNext: false, again: nil, .nothingRan, retry: false)
        require(!noRetry.offered && !noRetry.ledger.runsNext(7000) && noRetry.ledger.whySkip(7000) == "nothing-ran", "settleChoice: retry off keeps the old behaviour")

        // Fixtures: games whose install scripts run a few programs from their own folder (each entry has its own file).
        func dxScript(_ entries: [(String, String)]) -> String {
            "\"installscript\" { \"run process\" {" + entries.map { " \"\($0.0)\" { \"process 1\" \"%INSTALLDIR%/\($0.1)\" }" }.joined() + " } }"
        }
        func dxGame(_ id: Int, _ name: String, _ entries: [(String, String, UInt16)]) throws -> DockGame {
            let g = DockGame(id: id, name: name, installDir: name, library: "Program Files (x86)/Steam/steamapps", installed: true, customExecutables: false)
            try write(common.appendingPathComponent(name + "/installscript.vdf"), dxScript(entries.map { ($0.0, $0.1) }))
            for e in entries { try pe(common.appendingPathComponent(name + "/" + e.1), machine: e.2) }
            return g
        }
        func since(_ mark: Int) -> String { LogStore.shared.lines.dropFirst(mark).joined(separator: "\n") }
        func putResult(_ text: String) throws { try write(drive.appendingPathComponent("bcd-dock-installers.result"), text) }
        func start(_ g: DockGame, mono: Bool = true) {
            DockInstallers.prepare(g, drive: drive, prefix: prefix, has32Bit: true, hasMsiexec: true, hasWineMono: mono, fusionSource: nil)
        }
        func now() -> Int { LogStore.shared.lines.count }
        func batchText() -> String { (try? String(contentsOf: drive.appendingPathComponent("bcd-dock-installers.cmd"), encoding: .utf8)) ?? "" }
        let rockEntries: [(String, String, UInt16)] = [("240240", "Redistributables/Social-Club-Setup.exe", 0x8664), ("101093031", "Redistributables/Rockstar-Games-Launcher.exe", 0x8664)]

        // ---- GTA V Legacy's one-time-install state machine (P36E54, YFYGD4, BT9TE3, 0DHNMC, NM88NQ, FMWZC5), reproduced from the logs
        let rock = try dxGame(7020, "Rock", rockEntries)
        var mark = now()
        start(rock)
        var out = since(mark)
        ledger = DockInstallLedger.load(prefix: prefix)
        require(DockInstallers.script != nil && DockInstallers.installsOnly && out.contains("app=7020 choice=run pending=2; the next start skips them") &&
                !ledger.runsNext(7020) && ledger.whySkip(7020) == "install-start" && ledger.session.count == 2 && ledger.sessionApp == 7020,
                "an install start turns the choice to Skip when it plans the installs, before any program ran (the hypothesis' mechanism)")
        // The session was cut short before it started its batch (no result file): at the next start the installs are offered once more.
        mark = now()
        start(rock)
        out = since(mark)
        ledger = DockInstallLedger.load(prefix: prefix)
        require(out.contains("app=7020 results reason=next-start none: the batch did not start") &&
                out.contains("app=7020 choice=run again: the last session never started a program") && !out.contains("choice=skip pending=") &&
                DockInstallers.script != nil && DockInstallers.skipsNextStart && (ledger.offeredAgain ?? []).contains("7020") && ledger.whySkip(7020) == "install-start" &&
                (DockInstallers.note ?? "").hasPrefix("The one-time installs did not get to start last time, so they run now. One-time installs for this start. To run: 240240, 101093031."),
                "nothing ran: offered again, the note says so (\(DockInstallers.note ?? ""))")
        // It happened again: skipped as before, with the reason in the log, not offered a third time.
        mark = now()
        start(rock)
        out = since(mark)
        require(out.contains("results reason=next-start none: the batch did not start") && !out.contains("choice=run again") &&
                out.contains("app=7020 choice=skip pending=2 why=nothing-ran") && DockInstallers.script == nil &&
                logged("program=240240 file=Social-Club-Setup.exe status=pending") && (DockInstallers.note ?? "").contains("skipped this time"),
                "nothing ran twice: skipped (no loop), choice=skip pending=2 recorded=- with its reason")
        // The user picks Run (NM88NQ): a new cycle; both succeed; the next start records them (FMWZC5: done=2).
        DockInstallers.setRunsNext(7020, true, prefix: prefix)
        ledger = DockInstallLedger.load(prefix: prefix)
        require(ledger.runsNext(7020) && ledger.whySkip(7020) == nil && !(ledger.offeredAgain ?? []).contains("7020"), "the user's pick starts a new cycle")
        start(rock)
        try putResult("begin 2 \r\nservices started\r\nstart 1 240240 \r\nexit 1 0 \r\nstart 2 101093031 \r\nexit 2 0 \r\nend \r\n")
        mark = now()
        start(rock)
        out = since(mark)
        require(out.contains("results reason=next-start succeeded=2 failed=0") && out.contains("program=240240 file=Social-Club-Setup.exe status=done") &&
                out.contains("program=101093031 file=Rockstar-Games-Launcher.exe status=done") && !out.contains("choice=run again") && DockInstallers.script == nil &&
                DockInstallLedger.load(prefix: prefix).whySkip(7020) == "ok", "installs that ran and succeeded: recorded, nothing changes")
        // A session that got as far as its service step but started no program is the same as none.
        let rock3 = try dxGame(7023, "Rock3", rockEntries)
        start(rock3)
        try putResult("begin 2 \r\nservices started\r\n")
        mark = now()
        start(rock3)
        out = since(mark)
        require(out.contains("app=7023 results reason=next-start succeeded=0 failed=2") && out.contains("statuses=240240=not-started,101093031=not-started") &&
                out.contains("app=7023 choice=run again") && DockInstallers.script != nil, "a result file with no start line: nothing ran, offered again")
        // What fails or hangs is NOT offered again (b30ff87e; the stall hint of 9bfcf72e relies on it): BT9TE3's state from 5BY8XF's statuses.
        let rock2 = try dxGame(7021, "Rock2", rockEntries)
        start(rock2)
        try putResult("begin 2 \r\nservices started\r\nstart 1 240240 \r\nexit 1 1 \r\nstart 2 101093031 \r\nexit 2 -1073740791 \r\nend \r\n")
        mark = now()
        start(rock2)
        out = since(mark)
        require(out.contains("statuses=240240=1,101093031=-1073740791") && out.contains("app=7021 choice=skip pending=2 why=failed") && !out.contains("choice=run again") &&
                DockInstallers.script == nil && DockInstallLedger.load(prefix: prefix).failedRuns(7021) == ["101093031", "240240"],
                "failed exit statuses: skipped from now on, exactly the state of the tester's logs, now with its reason")
        let rock4 = try dxGame(7024, "Rock4", rockEntries)
        start(rock4)
        try putResult("begin 2 \r\nservices started\r\nstart 1 240240 \r\n")
        mark = now()
        start(rock4)
        out = since(mark)
        require(out.contains("statuses=240240=unfinished,101093031=not-started") && out.contains("app=7024 choice=skip pending=2 why=unfinished") && !out.contains("choice=run again") &&
                DockInstallers.script == nil, "an installer that never closed: skipped as before, the way out of the stall hint")
        // A choice the user makes after the interrupted session wins.
        let rock5 = try dxGame(7025, "Rock5", rockEntries)
        start(rock5)
        DockInstallers.setRunsNext(7025, false, prefix: prefix)
        mark = now()
        start(rock5)
        out = since(mark)
        require(out.contains("app=7025 choice=skip pending=2 why=picked") && !out.contains("choice=run again") && DockInstallers.script == nil,
                "Skip picked after an interrupted session stays Skip")
        // MADEIRA_DOCK_INSTALL_RETRY=0: as before.
        setenv("MADEIRA_DOCK_INSTALL_RETRY", "0", 1)
        let rock6 = try dxGame(7026, "Rock6", rockEntries)
        start(rock6)
        mark = now()
        start(rock6)
        out = since(mark)
        require(out.contains("app=7026 choice=skip pending=2 why=nothing-ran") && !out.contains("choice=run again") && DockInstallers.script == nil &&
                !(DockInstallLedger.load(prefix: prefix).offeredAgain ?? []).contains("7026"), "MADEIRA_DOCK_INSTALL_RETRY=0: skipped as before")
        unsetenv("MADEIRA_DOCK_INSTALL_RETRY")
        // A batch that could not be written runs nothing: the choice this start turned to Skip goes back to Run.
        if getuid() != 0 {
            let rock7 = try dxGame(7027, "Rock7", rockEntries)
            chmod(drive.path, 0o555)
            mark = now()
            start(rock7)
            chmod(drive.path, 0o755)
            out = since(mark)
            ledger = DockInstallLedger.load(prefix: prefix)
            require(out.contains("app=7027 batch not written") && out.contains("app=7027 choice=run again: the batch was not written") && DockInstallers.script == nil &&
                    ledger.runsNext(7027) && ledger.whySkip(7027) == nil && !DockInstallers.skipsNextStart, "a batch that was not written: nothing is skipped")
        }

        // ---- DirectX's setup: only one that already failed once is provided by BCD
        let dxEntries: [(String, String, UInt16)] = [("directx", "DirectX/DXSetup.exe", 0x14c), ("extra", "extra.exe", 0x8664)]
        let dxg = try dxGame(7030, "Dx", dxEntries)
        mark = now()
        start(dxg)
        out = since(mark)
        require(DockInstallers.script != nil && batchText().contains("DXSetup.exe") && batchText().contains("extra.exe") && !out.contains("provided by BCD") &&
                out.contains("program=directx file=DXSetup.exe status=pending") && (DockInstallers.note ?? "").contains("To run: directx, extra"),
                "the first time a DirectX setup is planned and run like any program")
        try putResult("begin 2 \r\nservices started\r\nstart 1 directx \r\nexit 1 6 \r\nstart 2 extra \r\nexit 2 0 \r\nend \r\n")
        let dxFound = DockInstallers.found(dxg, drive: drive, defaultKey: true).processes
        mark = now()
        start(dxg)
        out = since(mark)
        let dxSystem = (try? String(contentsOf: prefix.appendingPathComponent("system.reg"), encoding: .utf8)) ?? ""
        ledger = DockInstallLedger.load(prefix: prefix)
        require(out.contains("statuses=directx=6,extra=0") && ledger.failedRuns(7030) == ["directx"], "a failed DirectX setup is kept in the ledger")
        require(out.contains("app=7030 program=directx file=DXSetup.exe status=provided") &&
                out.contains("app=7030 program=directx skipped: provided by BCD; file=DXSetup.exe is DirectX's runtime setup, it failed in this game before") &&
                out.contains("record: yes, 2 values") && out.contains("provided=1") && DockInstallers.script == nil &&
                DockInstallScripts.marked(dxFound[0].run, in: dxSystem) && dxSystem.contains("[Software\\\\Wow6432Node\\\\Valve\\\\Steam\\\\Apps\\\\7030]") &&
                (DockInstallers.note ?? "").contains("Provided by BCD, not run: directx"),
                "the next start records it as provided (both views), runs nothing and says so (\(DockInstallers.note ?? ""))")
        mark = now()
        start(dxg)
        out = since(mark)
        require(out.contains("program=directx file=DXSetup.exe status=done") && !out.contains("skipped: provided by BCD") && DockInstallers.script == nil,
                "after that it is simply done")
        // A DirectX setup that succeeded is recorded by the normal path and never touched.
        let dxOk = try dxGame(7031, "DxOk", [("directx", "DirectX/DXSETUP.exe", 0x14c)])
        start(dxOk)
        require(DockInstallers.script != nil && batchText().contains("DXSETUP.exe"), "a first start runs the DirectX setup")
        try putResult("begin 1 \r\nservices started\r\nstart 1 directx \r\nexit 1 0 \r\nend \r\n")
        mark = now()
        start(dxOk)
        out = since(mark)
        require(out.contains("results reason=next-start succeeded=1 failed=0") && out.contains("program=directx file=DXSETUP.exe status=done") &&
                !out.contains("provided by BCD") && DockInstallLedger.load(prefix: prefix).failedRuns(7031).isEmpty && DockInstallers.script == nil,
                "a DirectX setup that succeeds: done through the normal record, never provided")
        // One that hung, or never started, is not a failure.
        let dxHung = try dxGame(7032, "DxHung", [("directx", "DirectX/DXSETUP.exe", 0x14c)])
        start(dxHung)
        try putResult("begin 1 \r\nservices started\r\nstart 1 directx \r\n")
        mark = now()
        start(dxHung)
        out = since(mark)
        require(out.contains("statuses=directx=unfinished") && out.contains("program=directx file=DXSETUP.exe status=pending") && !out.contains("provided by BCD") &&
                DockInstallLedger.load(prefix: prefix).failedRuns(7032).isEmpty, "a DirectX setup that never closed is not provided")
        let dxNever = try dxGame(7033, "DxNever", [("directx", "DirectX/DXSETUP.exe", 0x14c)])
        start(dxNever)
        mark = now()
        start(dxNever)
        out = since(mark)
        require(out.contains("choice=run again") && DockInstallers.script != nil && batchText().contains("DXSETUP.exe") && !out.contains("provided by BCD"),
                "a DirectX setup whose session never started is offered again, not provided")
        // MADEIRA_DOCK_DIRECTX_PROVIDED=0: a failed setup stays pending, as before.
        setenv("MADEIRA_DOCK_DIRECTX_PROVIDED", "0", 1)
        let dxOff = try dxGame(7034, "DxOff", [("directx", "DirectX/DXSETUP.exe", 0x14c)])
        start(dxOff)
        try putResult("begin 1 \r\nservices started\r\nstart 1 directx \r\nexit 1 -1073741800 \r\nend \r\n")
        mark = now()
        start(dxOff)
        out = since(mark)
        require(out.contains("statuses=directx=-1073741800") && out.contains("app=7034 program=directx file=DXSETUP.exe status=pending") && !out.contains("provided by BCD") &&
                out.contains("app=7034 choice=skip pending=1 why=failed") && out.contains("provided=0"), "MADEIRA_DOCK_DIRECTX_PROVIDED=0: stays pending and skipped, as before")
        unsetenv("MADEIRA_DOCK_DIRECTX_PROVIDED")
        // The default again, on the same game: the record is written at that start.
        DockInstallers.setRunsNext(7034, true, prefix: prefix)
        mark = now()
        start(dxOff)
        out = since(mark)
        require(out.contains("app=7034 program=directx skipped: provided by BCD") && DockInstallers.script == nil, "the failure kept earlier is acted on once the key is on")
        // Reset forgets the failure: the setup runs again at that start.
        DockInstallers.setRequest(7034, .reset, prefix: prefix)
        mark = now()
        start(dxOff)
        out = since(mark)
        require(DockInstallLedger.load(prefix: prefix).failedRuns(7034).isEmpty && DockInstallers.script != nil && batchText().contains("DXSETUP.exe") && !out.contains("provided by BCD"),
                "Reset clears the failure: the DirectX setup runs again")
        // The restart alert after a failed DirectX setup.
        let dxAlert = try dxGame(7036, "DxAlert", [("directx", "DirectX/DXSETUP.exe", 0x14c), ("other", "other.exe", 0x8664)])
        start(dxAlert)
        try putResult("begin 2 \r\nservices started\r\nstart 1 directx \r\nexit 1 6 \r\nstart 2 other \r\nexit 2 0 \r\nend \r\n")
        let dxEnded = DockInstallers.poll(drive: drive) ?? ""
        let dxWords = DockInstallers.restartNotice(dxEnded)
        require(dxEnded == "Done with the one-time installs: 1 of 2 succeeded.\nFailed: directx (exit 6)" && DockInstallers.failedDirectX &&
                dxWords.contains("A program that failed is not run again") &&
                dxWords.contains("\nDirectX's setup is not needed: BCD ships the DirectX runtime libraries (D3DX, XAudio2, XInput, D3DCompiler) itself. The game starts without it, and it is recorded as provided at this game's next start.") &&
                dxWords.hasSuffix("\nClose BCD and open it again to play the game."), "the restart alert says BCD provides DirectX (\(dxWords))")
        let otherAlert = try dxGame(7037, "OtherAlert", [("other", "other.exe", 0x8664)])
        start(otherAlert)
        try putResult("begin 1 \r\nservices started\r\nstart 1 other \r\nexit 1 5 \r\nend \r\n")
        let otherEnded = DockInstallers.poll(drive: drive) ?? ""
        require(!DockInstallers.failedDirectX && !DockInstallers.restartNotice(otherEnded).contains("DirectX"), "no such sentence after another program failed")
        setenv("MADEIRA_DOCK_DIRECTX_PROVIDED", "0", 1)
        start(dxAlert)
        try putResult("begin 2 \r\nservices started\r\nstart 1 directx \r\nexit 1 6 \r\nstart 2 other \r\nexit 2 0 \r\nend \r\n")
        require(!DockInstallers.restartNotice(DockInstallers.poll(drive: drive)).contains("DirectX's setup is not needed"), "MADEIRA_DOCK_DIRECTX_PROVIDED=0: no such sentence")
        unsetenv("MADEIRA_DOCK_DIRECTX_PROVIDED")
        // An entry that holds the DirectX setup and another program that still has to run is held back (the .NET rule).
        let dxMixed = try dxGame(7038, "DxMixed", [("directx", "DirectX/DXSETUP.exe", 0x14c)])
        try write(common.appendingPathComponent("DxMixed/installscript.vdf"),
                  "\"installscript\" { \"run process\" { \"directx\" { \"process 1\" \"%INSTALLDIR%/DirectX/DXSETUP.exe\" \"process 2\" \"%INSTALLDIR%/after.exe\" } } }")
        try pe(common.appendingPathComponent("DxMixed/after.exe"), machine: 0x8664)
        var mixedLedger = DockInstallLedger.load(prefix: prefix); mixedLedger.setFailed(7038, ["directx"]); try mixedLedger.save(prefix: prefix)
        mark = now()
        start(dxMixed)
        out = since(mark)
        require(out.contains("app=7038 program=directx skipped: provided by BCD") && out.contains("record: no, another program of this entry still has to run") &&
                DockInstallers.script != nil && batchText().contains("after.exe") && !batchText().contains("DXSETUP"), "an entry with another program still to run is held back, only the other runs")

        // Fixture batch for the optional real cmd.exe run: stand-in programs under @DIR@.
        func fixture(_ name: String, _ file: String) -> SteamInstallProcess {
            SteamInstallProcess(run: SteamInstallRun(name: name, hive: .machine, key: "Software\\Fixture\\" + name, value: 1),
                                executable: "@DIR@\\" + file, arguments: "/quiet")
        }
        let real = DockInstallScripts.batch([fixture("ok", "ok.cmd"), fixture("negative", "negative.cmd"), fixture("restart", "restart.cmd"), fixture("fails", "fails.cmd")],
                                            resultFile: "@DIR@\\result.txt", services: .start("@DOCK@"))
        try Data(real.utf8).write(to: URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/dev/null"))

        if failures > 0 { print("FAILURES: \(failures)"); exit(1) }
        print("PASS: all Dock installer Swift checks")
    }
}
'''
launch = dock[dock.index('    static func launchArguments('):]
launch = launch[:launch.index('\n    }\n') + 7]
launch_src = ('enum MadeiraDockLaunch {\n    static let executable = MadeiraDock.executable\n' + launch +
              '    static func arguments(_ installers: String?) -> String { launchArguments(width: 1280, height: 720, installers: installers) }\n}\n')

with tempfile.TemporaryDirectory(prefix='madeira-dock-installers-') as tmp:
    tmp = Path(tmp)
    (tmp / 'stubs.swift').write_text(stubs + launch_src, encoding='utf-8')
    (tmp / 'checks.swift').write_text(checks, encoding='utf-8')
    exe = tmp / 'check'
    batch_text = ''
    if not Path(SWIFTC).exists() and not shutil.which(SWIFTC):
        # The compiled checks need a Swift toolchain; without one only the static rules and
        # the C part run. --require-swift turns a missing compiler into a failure.
        if '--require-swift' in sys.argv:
            require(False, 'Swift compiler available (--require-swift)')
        else:
            print('SKIP: Swift compiler unavailable; the compiled installer checks did not run')
        build = None
    else:
        # DockReset's backup folder name comes from BCD's date helpers (BCDStore.swift, Foundation only; no ICU date formatter).
        store_source = (app / 'BCDStore.swift').read_text(encoding='utf-8')
        (tmp / 'dates.swift').write_text('import Foundation\n\nenum BCDFormat {\n' + store_source[
            store_source.index('    // dates-begin'):store_source.index('    // dates-end')] + '}\n', encoding='utf-8')
        build = subprocess.run([SWIFTC, '-parse-as-library', '-swift-version', '5', '-sanitize=address', '-o', str(exe),
                                str(tmp / 'stubs.swift'), str(tmp / 'checks.swift'), str(app / 'DockInstallers.swift'),
                                str(app / 'DockReset.swift'), str(tmp / 'dates.swift')])
        require(build.returncode == 0, 'production installer Swift compiles on the host')
    if build is not None and build.returncode == 0:
        run = subprocess.run([str(exe), str(tmp / 'fixture.cmd')], env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        require(run.returncode == 0, 'installer checks pass under AddressSanitizer')
        batch_text = (tmp / 'fixture.cmd').read_text() if (tmp / 'fixture.cmd').exists() else ''

    # ---- madsync_enabled() with the production madeira_cfg.h
    fn = madsync[madsync.index('int madsync_enabled(void)'):]
    fn = fn[:fn.index('\n}\n') + 3]
    (tmp / 'm.c').write_text('#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include "madeira_cfg.h"\n' + fn +
                             'int main(void) { int a = madsync_enabled(); int b = madsync_enabled(); printf("madsync=%d\\n", a && b); return a != b; }\n')
    cc = subprocess.run([CC, '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined',
                         '-I', str(root / 'build'), str(tmp / 'm.c'), '-o', str(tmp / 'm')])
    require(cc.returncode == 0, 'madsync_enabled() compiles with the production madeira_cfg.h')
    if cc.returncode == 0:
        docs = tmp / 'docs'; docs.mkdir()

        def madsync_run(cfg=None, session=None):
            (docs / 'bcd.cfg').unlink(missing_ok=True)
            if cfg is not None:
                (docs / 'bcd.cfg').write_text(cfg)
            env = {k: v for k, v in os.environ.items() if k != 'MADEIRA_MADSYNC_SESSION'}
            env.update(MADEIRA_DOCS_DIR=str(docs), HOME=str(tmp))
            if session is not None:
                env['MADEIRA_MADSYNC_SESSION'] = session
            out = subprocess.run([str(tmp / 'm')], env=env, capture_output=True, text=True)
            return int(re.search(r'madsync=(\d)', out.stdout).group(1)), out.stderr

        on, log = madsync_run()
        require(on == 0 and 'disabled' in log and 'off for this session' not in log, 'no bcd.cfg, no session switch: madsync off (fastsync is the default)')
        require(madsync_run('inproc-sync = 1\n')[0] == 1 and madsync_run('other = 1\n')[0] == 0, 'madsync only with inproc-sync = 1')
        require(madsync_run('inproc-sync = 0\n')[0] == 0, 'inproc-sync = 0 still turns it off')
        on, log = madsync_run('inproc-sync = 1\n', session='0')
        require(on == 0 and '[madsync] off for this session (MADEIRA_MADSYNC_SESSION=0' in log, 'MADEIRA_MADSYNC_SESSION=0: off for this session, logged')
        require(all(madsync_run('inproc-sync = 1\n', session=v)[0] == 1 for v in ('1', '', '00', 'off', '0 ')), 'any other session value leaves madsync on')
        require(madsync_run('inproc-sync = 0\n', session='1')[0] == 0, 'the session switch never turns madsync on')

# ---- optional: run the generated batch with Windows cmd.exe (WSL interop)
cmd = shutil.which('cmd.exe') or ('/mnt/c/Windows/System32/cmd.exe' if Path('/mnt/c/Windows/System32/cmd.exe').exists() else None)
staged = app / 'arm64ec-windows/dockhost.exe'
if batch_text and cmd and shutil.which('wslpath'):
    win_temp = subprocess.run([cmd, '/d', '/c', 'echo %TEMP%'], capture_output=True, text=True, cwd='/mnt/c').stdout.strip()
    work_win = win_temp + '\\madeira-dock-installers-%d' % os.getpid()
    work = Path(subprocess.run(['wslpath', '-u', work_win], capture_output=True, text=True).stdout.strip())
    work.mkdir(parents=True, exist_ok=True)
    try:
        for name, code in [('ok', 0), ('negative', -9), ('restart', 3010), ('fails', 1603)]:
            (work / (name + '.cmd')).write_text('@exit /b %d\r\n' % code, newline='')

        def run(dock_win):
            (work / 'result.txt').unlink(missing_ok=True)
            (work / 'run.cmd').write_text(batch_text.replace('@DIR@', work_win).replace('@DOCK@', dock_win), newline='')
            subprocess.run([cmd, '/d', '/c', 'call', work_win + '\\run.cmd'], cwd='/mnt/c', capture_output=True, text=True, timeout=120)
            return [l.strip() for l in (work / 'result.txt').read_text(errors='replace').replace('\r', '').split('\n') if l.strip()]

        programs = ['start 1 ok', 'exit 1 0', 'start 2 negative', 'exit 2 -9', 'start 3 restart', 'exit 3 3010', 'start 4 fails', 'exit 4 1603', 'end']
        lines = run(work_win + '\\missing-dockhost.exe')
        require(lines == ['begin 4', 'services failed'] + programs, f'cmd.exe ran the batch: statuses 0/-9/3010/1603 reported; a service step that cannot run records "services failed" ({lines})')
        if staged.exists():
            shutil.copy(staged, work / 'dockhost.exe')
            lines = run(work_win + '\\dockhost.exe')
            require(lines[:2] == ['begin 4', 'services already'] and lines[2:] == programs,
                    f'with the staged dockhost.exe --start-services (connect only on Windows): "services already" ({lines[:2]})')
        else:
            print('SKIP: dockhost.exe not staged (build/madeira-dock/build.sh); service step checked with a missing executable only')
    finally:
        shutil.rmtree(work, ignore_errors=True)
else:
    print('SKIP: cmd.exe not reachable; batch flow checked textually only')

if failures:
    print(f'FAILURES: {failures}')
    sys.exit(1)
print('PASS: all Dock installer checks; device execution still required')
