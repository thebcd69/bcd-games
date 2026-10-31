// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import Foundation

// MARK: - Steam install scripts

/// One "Run Process" entry of a game's Steam install script (installscript.vdf).
/// Valve's desktop client runs the entry's programs (typically runtime
/// redistributables) before a start unless the registry value `name` under `key`
/// is at least `value`, and records it once they have run (Steamworks, "Creating
/// and using InstallScripts").
struct SteamInstallRun: Equatable, Hashable, Sendable, Codable {
    enum Hive: String, Sendable, Codable { case machine, user }
    var name: String
    var hive: Hive
    var key: String      // under the hive, e.g. Software\Valve\Steam\Apps\<appid>
    var value: UInt32    // MinimumHasRunValue, else 1
}

/// One program of a "Run Process" entry, resolved to a Windows path.
struct SteamInstallProcess: Equatable, Sendable {
    var run: SteamInstallRun
    var executable: String      // C:\...
    var arguments: String
}

/// Reading install scripts and recording runs in the prefix's registry files.
/// The .reg files are only written while no session runs: the wineserver keeps
/// the registry in memory and writes it back when it stops.
enum SteamInstallScripts {
    static let maxScriptBytes = 1 << 20

    /// "HKEY_LOCAL_MACHINE\Software\..." -> (.machine, "Software\...").
    static func hive(_ path: String) -> (SteamInstallRun.Hive, String)? {
        let parts = path.replacingOccurrences(of: "/", with: "\\").split(separator: "\\").map(String.init)
        guard let first = parts.first?.uppercased() else { return nil }
        let rest = parts.dropFirst().joined(separator: "\\")
        switch first {
        case "HKEY_LOCAL_MACHINE", "HKLM": return (.machine, rest)
        case "HKEY_CURRENT_USER", "HKCU": return (.user, rest)
        default: return nil
        }
    }

    /// The keys a run is recorded under. A 32-bit reader sees HKLM\Software through
    /// Wow6432Node in a 64-bit prefix; the plain key covers a 64-bit reader.
    static func keys(_ run: SteamInstallRun) -> [String] {
        let lower = run.key.lowercased()
        guard run.hive == .machine, lower.hasPrefix("software\\"), !lower.hasPrefix("software\\wow6432node\\") else { return [run.key] }
        return [run.key, "Software\\Wow6432Node\\" + run.key.dropFirst("software\\".count)]
    }

    /// Install scripts: .vdf files with a "Run Process" section in `folder` and up to
    /// `depth` levels below it. Symbolic links are not followed.
    /// madeira-bcd (round 591): `needle` names the section looked for; "run process" (the default) is every
    /// caller before the "Registry" values (SteamInstallRegistry, MADEIRA_DOCK_SCRIPT_REGISTRY), which ask for "registry".
    static func scripts(folder: URL, depth: Int = 1, containing needle: String = "run process") -> [URL] {
        var found: [URL] = []
        let keys: [URLResourceKey] = [.isDirectoryKey, .isSymbolicLinkKey, .fileSizeKey]
        guard let items = try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles]) else { return [] }
        for item in items.sorted(by: { $0.path < $1.path }).prefix(500) {
            guard let values = try? item.resourceValues(forKeys: Set(keys)), values.isSymbolicLink != true else { continue }
            if values.isDirectory == true {
                if depth > 0 { found += scripts(folder: item, depth: depth - 1, containing: needle) }
            } else if item.pathExtension.lowercased() == "vdf", (values.fileSize ?? .max) <= maxScriptBytes,
                      let contents = try? Data(contentsOf: item),
                      String(decoding: contents, as: UTF8.self).range(of: needle, options: .caseInsensitive) != nil {
                found.append(item)
            }
        }
        return found
    }

    /// Where Valve's client records an entry that names no "HasRunKey": a DWORD named
    /// after the entry (lowercase, as its script parser keys it) under the app's own key.
    static func defaultRunKey(appID: Int) -> String { "Software\\Valve\\Steam\\Apps\\\(appID)" }

    /// The "HasRunKey" of an entry's fields, else (with a valid `appID` and a program to
    /// run) the per-app default key in full-path form.
    static func runKey(_ fields: [String: String], appID: Int?) -> String? {
        if let key = fields["hasrunkey"] { return key }
        guard let appID, appID > 0, appID < 1 << 31, fields.keys.contains(where: { $0.hasPrefix("process ") }) else { return nil }
        return "HKEY_LOCAL_MACHINE\\" + defaultRunKey(appID: appID)
    }

    /// Every "Run Process" entry with its fields (lowercased keys), read straight from the
    /// text. Scripts repeat the "Run Process" key, one section per installer, so a
    /// key-value reader that keeps the last copy of a repeated key would miss entries.
    static func entries(script: Data, appID: Int? = nil) -> [(SteamInstallRun, [String: String])] {
        var bytes = Array(script.prefix(maxScriptBytes))
        if bytes.starts(with: [0xef, 0xbb, 0xbf]) { bytes.removeFirst(3) }
        var position = 0
        func token() -> (text: String, quoted: Bool)? {
            while position < bytes.count {
                if bytes[position] <= 32 { position += 1; continue }
                if bytes[position] == 47, position + 1 < bytes.count, bytes[position + 1] == 47 {
                    while position < bytes.count, bytes[position] != 10 { position += 1 }
                    continue
                }
                break
            }
            guard position < bytes.count else { return nil }
            let first = bytes[position]; position += 1
            if first == 123 { return ("{", false) }
            if first == 125 { return ("}", false) }
            var value: [UInt8] = []
            if first == 34 {
                while position < bytes.count {
                    let byte = bytes[position]; position += 1
                    if byte == 34 { break }
                    if byte == 92, position < bytes.count, bytes[position] == 34 || bytes[position] == 92 { value.append(bytes[position]); position += 1 }
                    else { value.append(byte) }
                }
                return (String(decoding: value, as: UTF8.self), true)
            }
            value.append(first)
            while position < bytes.count, bytes[position] > 32, bytes[position] != 123, bytes[position] != 125 { value.append(bytes[position]); position += 1 }
            return (String(decoding: value, as: UTF8.self), true)
        }
        var result: [(SteamInstallRun, [String: String])] = []
        var path: [String] = []          // keys of the open sections, lowercased
        var pending: String?             // a key waiting for its value or section
        var fields: [String: String] = [:]
        var tokens = 0
        while let (text, quoted) = token() {
            tokens += 1
            if tokens > 200_000 || path.count > 32 { break }
            if !quoted && text == "{" {
                path.append(pending?.lowercased() ?? ""); pending = nil
                if path.count >= 2, path[path.count - 2] == "run process" { fields = [:] }
            } else if !quoted && text == "}" {
                // An entry section closes: path is [..., "run process", <entry>].
                if path.count >= 2, path[path.count - 2] == "run process", let name = path.last,
                   let key = runKey(fields, appID: appID), let (hive, sub) = hive(key), !sub.isEmpty {
                    let minimum = fields["minimumhasrunvalue"].flatMap { UInt32($0.trimmingCharacters(in: .whitespaces)) } ?? 1
                    let run = SteamInstallRun(name: name, hive: hive, key: sub, value: max(1, minimum))
                    if !result.contains(where: { $0.0 == run }) { result.append((run, fields)) }
                }
                if !path.isEmpty { path.removeLast() }
                pending = nil
            } else if let key = pending {
                if path.count >= 2, path[path.count - 2] == "run process" { fields[key.lowercased()] = text }
                pending = nil
            } else {
                pending = text
            }
        }
        return result
    }

    /// The .reg text with every run's value at least its minimum, and how many values changed.
    /// Wine's format: "[Key\\Sub] <time>" section headers, then "\"name\"=dword:00000001" lines.
    static func mark(_ runs: [SteamInstallRun], in text: String, now: Int) -> (text: String, changed: Int) {
        var lines = text.components(separatedBy: "\n")
        var changed = 0
        for run in runs {
            let valueName = "\"" + escape(run.name) + "\"="
            let valueLine = valueName + String(format: "dword:%08x", run.value)
            for key in keys(run) {
                let header = "[" + escape(key) + "]"
                if let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(header.lowercased()) }) {
                    var end = start + 1
                    while end < lines.count, !lines[end].hasPrefix("[") { end += 1 }
                    if let index = (start + 1..<end).first(where: { lines[$0].lowercased().hasPrefix(valueName.lowercased()) }) {
                        if let current = dword(lines[index]), current >= run.value { continue }
                        lines[index] = valueLine
                    } else {
                        var at = end
                        while at > start + 1, lines[at - 1].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { at -= 1 }
                        lines.insert(valueLine, at: at)
                    }
                } else {
                    while let last = lines.last, last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { lines.removeLast() }
                    lines += ["", header + " \(now)", valueLine, ""]
                }
                changed += 1
            }
        }
        return (lines.joined(separator: "\n"), changed)
    }

    /// The .reg text without the runs' values (both views), and how many lines were removed.
    /// Only the value named after each entry goes; the key and its other values stay.
    static func unmark(_ runs: [SteamInstallRun], in text: String) -> (text: String, removed: Int) {
        var lines = text.components(separatedBy: "\n")
        var removed = 0
        for run in runs {
            let valueName = ("\"" + escape(run.name) + "\"=").lowercased()
            for key in keys(run) {
                let header = ("[" + escape(key) + "]").lowercased()
                guard let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(header) }) else { continue }
                var index = start + 1
                while index < lines.count, !lines[index].hasPrefix("[") {
                    if lines[index].lowercased().hasPrefix(valueName) { lines.remove(at: index); removed += 1 } else { index += 1 }
                }
            }
        }
        return (lines.joined(separator: "\n"), removed)
    }

    /// Records the runs in the prefix's system.reg and user.reg (a .bcd-bak copy is kept
    /// once). Returns the number of values written. Only while no session runs.
    @discardableResult
    static func mark(_ runs: [SteamInstallRun], prefix: URL) throws -> Int {
        let now = Int(Date().timeIntervalSince1970)
        return try rewrite(runs, prefix: prefix) { selected, text in
            let result = mark(selected, in: text, now: now)
            return (result.text, result.changed)
        }
    }

    /// Removes the runs' records from the prefix's system.reg and user.reg (the same
    /// .bcd-bak copy is kept once). Returns the number of values removed. Only while no
    /// session runs.
    @discardableResult
    static func unmark(_ runs: [SteamInstallRun], prefix: URL) throws -> Int {
        try rewrite(runs, prefix: prefix) { selected, text in
            let result = unmark(selected, in: text)
            return (result.text, result.removed)
        }
    }

    /// Applies `change` (new text, values changed) to each hive file that holds some of the
    /// runs and writes the files it changed. Returns the total of values changed.
    private static func rewrite(_ runs: [SteamInstallRun], prefix: URL,
                                _ change: ([SteamInstallRun], String) -> (String, Int)) throws -> Int {
        var total = 0
        for (hive, file) in [(SteamInstallRun.Hive.machine, "system.reg"), (.user, "user.reg")] {
            let selected = runs.filter { $0.hive == hive }
            guard !selected.isEmpty else { continue }
            let registry = prefix.appendingPathComponent(file)
            guard let contents = try? Data(contentsOf: registry), !contents.isEmpty else { continue }   // a prefix not seeded yet
            let (updated, changed) = change(selected, String(decoding: contents, as: UTF8.self))
            guard changed > 0 else { continue }
            let backup = registry.appendingPathExtension("bcd-bak")
            if !FileManager.default.fileExists(atPath: backup.path) { try? FileManager.default.copyItem(at: registry, to: backup) }
            try Data(updated.utf8).write(to: registry, options: .atomic)
            total += changed
        }
        return total
    }

    static func escape(_ s: String) -> String { s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }
    private static func dword(_ line: String) -> UInt32? {
        guard let range = line.range(of: "=dword:", options: .caseInsensitive) else { return nil }
        return UInt32(line[range.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines), radix: 16)
    }
}

// MARK: - Planning, the batch and its results

/// What a Dock start does with one install-script program.
enum DockInstallStatus: String, Sendable {
    case done          // already recorded in the prefix's registry
    case missing       // the program is not on disk
    case unsupported   // this build cannot run it (see DockInstallers.runnable)
    case provided      // madeira-bcd: a .NET Framework setup; Wine Mono provides .NET, so it is not run and its record is written
    case pending       // runs in the session before the host
    case limit         // over the per-start limit; runs at a later start
}

struct DockInstallPlanItem: Equatable, Sendable {
    var process: SteamInstallProcess
    var status: DockInstallStatus
    var reason: String? = nil     // why an unsupported program cannot run
}

/// The batch's service-manager step: `.start` runs the Dock executable with
/// `--start-services` before the first installer; `.off` only records "services off".
enum DockInstallServices: Equatable, Sendable {
    case off
    case start(String)
}

/// Valve's client runs a game's install scripts as part of its own launch tasks,
/// before the step Madeira Dock asks it for (LaunchApp), so on the Dock route
/// nothing runs them. Madeira does: every program not yet recorded runs once in
/// the Dock session before the host, from a batch that reports each exit status.
/// Every program is treated alike; nothing is decided by program name.
enum DockInstallScripts {
    /// The programs of one install script. `installDir` replaces %INSTALLDIR% (a Windows
    /// path). `appID` records entries without a "HasRunKey" under Steam's per-app key.
    static func processes(script: Data, installDir: String, appID: Int? = nil) -> [SteamInstallProcess] {
        var result: [SteamInstallProcess] = []
        for (run, fields) in SteamInstallScripts.entries(script: script, appID: appID) {
            let numbers = fields.keys.compactMap { key -> Int? in
                guard key.hasPrefix("process ") else { return nil }
                return Int(key.dropFirst("process ".count).trimmingCharacters(in: .whitespaces))
            }.sorted()
            for number in numbers.prefix(8) {
                guard let raw = fields["process \(number)"], !raw.isEmpty, raw.utf8.count <= 1024 else { continue }
                let exe = expand(raw, installDir: installDir)
                let lower = exe.lowercased()
                guard exe.count > 3, exe.hasPrefix("C:\\"), !exe.contains("%"), !exe.contains("\""),
                      !exe.contains(".."), lower.hasSuffix(".exe") || lower.hasSuffix(".msi") else { continue }
                let args = expand(fields["command \(number)"] ?? "", installDir: installDir, path: false)
                    .trimmingCharacters(in: .whitespaces)
                // No cmd.exe metacharacters: the arguments go into a batch line as they are.
                guard args.utf8.count <= 1024, !args.contains("\r"), !args.contains("\n"), !args.contains("&"),
                      !args.contains("|"), !args.contains(">"), !args.contains("<"), !args.contains("^"),
                      !args.contains("%") else { continue }
                let process = SteamInstallProcess(run: run, executable: exe, arguments: args)
                if !result.contains(process) { result.append(process) }
            }
        }
        return result
    }

    /// A program path gets Windows separators; an argument list keeps its "/switches".
    static func expand(_ text: String, installDir: String, path: Bool = true) -> String {
        var value = path ? text.replacingOccurrences(of: "/", with: "\\") : text
        value = value.replacingOccurrences(of: "%INSTALLDIR%", with: installDir, options: .caseInsensitive)
        if path { while value.contains("\\\\") { value = value.replacingOccurrences(of: "\\\\", with: "\\") } }
        return value
    }

    /// The value a run has in a Wine .reg text (highest of both views), nil when absent.
    static func recorded(_ run: SteamInstallRun, in text: String) -> UInt32? {
        let lines = text.components(separatedBy: "\n")
        let valueName = ("\"" + SteamInstallScripts.escape(run.name) + "\"=").lowercased()
        var best: UInt32?
        for key in SteamInstallScripts.keys(run) {
            let header = ("[" + SteamInstallScripts.escape(key) + "]").lowercased()
            guard let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(header) }) else { continue }
            var index = start + 1
            while index < lines.count, !lines[index].hasPrefix("[") {
                let line = lines[index].lowercased()
                if line.hasPrefix(valueName), let range = line.range(of: "=dword:"),
                   let value = UInt32(line[range.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines), radix: 16) {
                    best = max(best ?? 0, value)
                }
                index += 1
            }
        }
        return best
    }

    /// Whether a run is recorded done (value at least its minimum in either view).
    static func marked(_ run: SteamInstallRun, in text: String) -> Bool {
        (recorded(run, in: text) ?? 0) >= run.value
    }

    /// Every program's fate at this start: done first, then whether the program exists,
    /// madeira-bcd: then whether Wine Mono provides what it installs (`provided`: it is not run),
    /// then whether this build can run it (`runnable` returns the reason it cannot);
    /// at most `limit` programs run per start.
    static func plan(_ found: [SteamInstallProcess], limit: Int = 8,
                     done: (SteamInstallRun) -> Bool, exists: (String) -> Bool,
                     runnable: (SteamInstallProcess) -> String? = { _ in nil },
                     provided: (SteamInstallProcess) -> Bool = { _ in false }) -> [DockInstallPlanItem] {
        var result: [DockInstallPlanItem] = []
        var queued = 0
        for process in found {
            if done(process.run) { result.append(DockInstallPlanItem(process: process, status: .done)); continue }
            if !exists(process.executable) { result.append(DockInstallPlanItem(process: process, status: .missing)); continue }
            if provided(process) { result.append(DockInstallPlanItem(process: process, status: .provided)); continue }
            if let reason = runnable(process) {
                result.append(DockInstallPlanItem(process: process, status: .unsupported, reason: reason)); continue
            }
            if queued < limit { queued += 1; result.append(DockInstallPlanItem(process: process, status: .pending)) }
            else { result.append(DockInstallPlanItem(process: process, status: .limit)) }
        }
        return result
    }

    /// The Dock sheet's line about the plan, or nil when every program is already done.
    static func note(_ items: [DockInstallPlanItem]) -> String? {
        func names(_ status: DockInstallStatus) -> String {
            var seen: [String] = []
            for item in items where item.status == status {
                var text = label(item.process.run)
                if let reason = item.reason { text += " (" + reason + ")" }
                if !seen.contains(text) { seen.append(text) }
            }
            return seen.joined(separator: ", ")
        }
        guard items.contains(where: { $0.status != .done }) else { return nil }
        var parts: [String] = []
        if !names(.pending).isEmpty { parts.append("To run: " + names(.pending)) }
        if !names(.done).isEmpty { parts.append("Done earlier: " + names(.done)) }
        for by in ["Wine Mono", "BCD"] {
            var seen: [String] = []
            for item in items where item.status == .provided && provider(item.process) == by {
                let text = label(item.process.run)
                if !seen.contains(text) { seen.append(text) }
            }
            if !seen.isEmpty { parts.append("Provided by \(by), not run: " + seen.joined(separator: ", ")) }
        }
        if !names(.missing).isEmpty { parts.append("Installer not found: " + names(.missing)) }
        if !names(.unsupported).isEmpty { parts.append("Not supported by this build: " + names(.unsupported)) }
        if !names(.limit).isEmpty { parts.append("Left for the next start: " + names(.limit)) }
        return "One-time installs for this start. " + parts.joined(separator: ". ") + "."
    }

    /// A program's label for logs, the batch and the sheet: the entry name, reduced to
    /// safe characters.
    static func label(_ run: SteamInstallRun) -> String {
        String(run.name.filter { $0.isLetter || $0.isNumber || $0 == " " || $0 == "." || $0 == "-" || $0 == "_" }.prefix(64))
    }

    /// madeira-bcd: whether an install-script program is a Microsoft .NET Framework redistributable
    /// (1.1 to 4.8), by the file name Microsoft gives it: dotnetfx*.exe (DotNetFx35.exe,
    /// dotNetFx40_Full_setup.exe), NetFx<digit>*.exe (NetFx20SP2_x86.exe) and NDP<digit>*.exe
    /// (NDP472-KB4054530-x86-x64-AllOS-ENU.exe). The one place a program is told by its name: these
    /// setups never finished under Wine in the fork's logs (Batman: Arkham City GOTY's DotNetFx35.exe
    /// stayed open for good, Star Wars: The Clone Wars Republic Heroes' dotnetfx35.exe exited 5100),
    /// and the runtime they install is Wine Mono's. The modern .NET runtimes (dotnet-runtime-*.exe,
    /// windowsdesktop-runtime-*.exe) are not Wine Mono's and are not matched.
    static func isDotNetFramework(_ executable: String) -> Bool {
        let file = (executable.split(separator: "\\").last.map(String.init) ?? executable).lowercased()
        guard file.hasSuffix(".exe") else { return false }
        if file.hasPrefix("dotnetfx") { return true }
        for prefix in ["netfx", "ndp"] where file.hasPrefix(prefix) {
            if let next = file.dropFirst(prefix.count).first, next.isASCII, next.isNumber { return true }
        }
        return false
    }

    /// madeira-bcd: whether an install-script program is Microsoft's DirectX End-User Runtime setup, by the file name
    /// Microsoft gives it: dxsetup.exe (the offline setup: in the fork's logs 12 games carry it, every entry is named
    /// `directx` and runs DXSETUP.exe, e.g. Saints Row: The Third's `common\Saints Row the Third\DirectX\DXSETUP.exe /silent`)
    /// and dxwebsetup.exe (its web form, which fetches the same packages). What they install, the D3DX9/10/11, D3DCompiler,
    /// XAudio2, X3DAudio, XAPOFX and XInput libraries (and XACT for 32-bit programs), BCD ships as Wine's builtin DLLs
    /// (tools/build-wine-extra-dlls.sh WANT list; app/Madeira/arm64ec-windows and i386-windows). Only a setup that already
    /// failed once in the game is acted on (DockInstallers.prepare: provided by BCD): a setup that succeeds writes
    /// Microsoft's own DLLs over the builtins, and nothing proves a game the same without them.
    static func isDirectXSetup(_ executable: String) -> Bool {
        let file = (executable.split(separator: "\\").last.map(String.init) ?? executable).lowercased()
        return file == "dxsetup.exe" || file == "dxwebsetup.exe"
    }

    /// madeira-bcd: who provides what a plan's provided program would have installed: "BCD" for DirectX's runtime libraries,
    /// "Wine Mono" for a .NET Framework.
    static func provider(_ process: SteamInstallProcess) -> String { isDirectXSetup(process.executable) ? "BCD" : "Wine Mono" }

    /// madeira-bcd: the entries whose provided programs are recorded as done at this start (every
    /// program of the entry is provided or done), and those held back: another program of the same
    /// entry still has to run, and a record would make the next plan skip it.
    static func providedRuns(_ items: [DockInstallPlanItem]) -> (record: [SteamInstallRun], held: [SteamInstallRun]) {
        var record: [SteamInstallRun] = [], held: [SteamInstallRun] = []
        for run in items.filter({ $0.status == .provided }).map({ $0.process.run }) where !record.contains(run) && !held.contains(run) {
            let others = items.contains { $0.process.run == run && $0.status != .provided && $0.status != .done }
            if others { held.append(run) } else { record.append(run) }
        }
        return (record, held)
    }

    /// The batch the Dock session runs before the host. Each program's start and exit
    /// status are appended to `resultFile` ("start <i> <label>", "exit <i> <status>"; every
    /// redirection follows a space, so a status digit is never read as a handle number).
    /// The batch records nothing in the registry itself: Madeira records the programs that
    /// exited with 0, 3010 or 1641 (installed, restart requested) at the next Dock start,
    /// when no session runs (DockInstallers.absorbResults).
    ///
    /// `services` first makes Wine's service manager reachable (`dockhost.exe
    /// --start-services` prints "services <outcome>" into the result file; "services failed"
    /// when it could not run) or records "services off".
    static func batch(_ processes: [SteamInstallProcess], resultFile: String, services: DockInstallServices) -> String {
        let result = " >>\"" + resultFile.replacingOccurrences(of: "\"", with: "") + "\""
        var lines = ["@echo off", "rem BCD Dock: a game's one-time installs, before the host starts the game",
                     "echo begin \(processes.count)" + result.replacingOccurrences(of: " >>", with: " >")]
        switch services {
        case .off: lines.append("echo services off" + result)
        case .start(let executable):
            lines.append("\"" + executable.replacingOccurrences(of: "\"", with: "") + "\" --start-services" + result)
            lines.append("if not \"%ERRORLEVEL%\"==\"0\" echo services failed" + result)
        }
        for (offset, process) in processes.enumerated() {
            let index = offset + 1
            let quoted = "\"" + process.executable + "\""
            let command = process.executable.lowercased().hasSuffix(".msi")
                ? "C:\\windows\\system32\\msiexec.exe /i " + quoted + (process.arguments.isEmpty ? "" : " " + process.arguments)
                : "call " + quoted + (process.arguments.isEmpty ? "" : " " + process.arguments)
            let text = label(process.run)
            lines.append("echo [dock-installers] running \(index)/\(processes.count) " + text)
            lines.append("echo start \(index) " + text + result)
            lines.append(command)
            lines.append("echo exit \(index) %ERRORLEVEL%" + result)
        }
        lines.append("echo end" + result)
        return lines.joined(separator: "\r\n") + "\r\n"
    }

    /// madeira-bcd: a failed program's exit status in the form Windows documents (the batch writes it as a signed
    /// number: -1073741701 is 0xC000007B) and, for the codes that mean the program could not start, what they mean.
    /// Empty for a small status (the program's own exit code).
    static func failureDetail(_ status: Int) -> String {
        guard status < 0 else { return "" }
        let code = UInt32(truncatingIfNeeded: status)
        let hex = "; exit 0x" + String(code, radix: 16, uppercase: true)
        switch code {
        case 0xC000007B: return hex + " (STATUS_INVALID_IMAGE_FORMAT: the loader refused the program or one of its DLLs as a wrong kind of image, e.g. 32-bit against 64-bit)"
        case 0xC0000135: return hex + " (STATUS_DLL_NOT_FOUND: a DLL the program needs was not found)"
        case 0xC0000139: return hex + " (STATUS_ENTRYPOINT_NOT_FOUND: a DLL lacks a function the program imports)"
        case 0xC0000142: return hex + " (STATUS_DLL_INIT_FAILED: a DLL refused to start)"
        default: return hex
        }
    }

    /// A result file read back: programs in the batch, which started (index -> label),
    /// which exited (index -> status), the service step's outcome and whether the batch ended.
    struct Results: Equatable, Sendable {
        var total = 0
        var started: [Int: String] = [:]
        var exits: [Int: Int] = [:]
        var ended = false
        var services: String?
        var running: Int? { started.keys.filter { exits[$0] == nil }.max() }
        static func succeeded(_ status: Int) -> Bool { status == 0 || status == 3010 || status == 1641 }
    }

    static func results(_ text: String) -> Results {
        var results = Results()
        // cmd.exe writes CRLF; "\r\n" is one Character, so split on any newline Character.
        for raw in text.split(whereSeparator: { $0.isNewline }).prefix(200) {
            let parts = raw.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: " ", maxSplits: 2).map(String.init)
            guard let verb = parts.first else { continue }
            switch verb {
            case "begin":
                results.total = parts.count > 1 ? min(Int(parts[1]) ?? 0, 64) : 0
            case "start":
                if parts.count > 1, let index = Int(parts[1]), (1...64).contains(index) {
                    results.started[index] = parts.count > 2 ? String(parts[2].prefix(64)) : ""
                }
            case "exit":
                if parts.count > 2, let index = Int(parts[1]), (1...64).contains(index),
                   let status = Int(parts[2].trimmingCharacters(in: .whitespaces)) { results.exits[index] = status }
            case "services":
                let word = parts.count > 1 ? String(parts[1].filter { $0.isLetter }.prefix(16)) : ""
                if !word.isEmpty { results.services = word }
            case "end":
                results.ended = true
            default:
                continue
            }
        }
        return results
    }
}

/// A request for one game's one-time installs, saved from the app at any time and applied
/// at that game's next Dock start, while no session runs (the registry is on disk then).
enum DockInstallRequest: String, Sendable {
    /// Record the game's own install-script programs as done: for an installer that did its
    /// work but never closed, so its batch never saw an exit status.
    case record
    /// Forget the game's own recorded programs and choose Run at next start, so they run
    /// again (for a clean reinstall). madeira-bcd: what the installers put into the prefix
    /// moves into a backup first (DockReset.swift; MADEIRA_DOCK_RESET_CLEAN=0: files stay).
    case reset
    /// madeira-bcd: `reset`, and the Rockstar launcher's and Social Club's sign-in data goes too.
    case resetSignOut = "reset-signout"
}

/// Kept next to the prefix's registry files, one small JSON file: the programs the last
/// batch runs, in its order (result lines refer to them by index), and each game's
/// One-time installs choice.
struct DockInstallLedger: Codable, Equatable, Sendable {
    static let fileName = "bcd-dock-installs.json"
    var session: [SteamInstallRun] = []
    var sessionApp: Int = 0
    /// App ID -> false: the game's next Dock start skips its one-time installs (Skip).
    /// Absent or true: the next start runs the pending ones ("Run at next start").
    var runNext: [String: Bool] = [:]
    /// App ID -> entry names the game's last batch started but never saw exit (an installer
    /// window that stayed open, a session ended early). Optional, like `requests`, so files
    /// written before these fields existed still load.
    var unfinished: [String: [String]]? = nil
    /// App ID -> the request its next Dock start applies (DockInstallers.applyRequest), by raw
    /// value: a value this build does not know reads as no request instead of a damaged file.
    var requests: [String: String]? = nil
    /// madeira-bcd: App ID -> entry names whose program ended a batch with a failing exit status and is not recorded
    /// (absorbResults). Only a DirectX setup acts on it (DockInstallers.prepare: provided by BCD); the others are kept
    /// for the log. Optional, like the fields above.
    var failed: [String: [String]]? = nil
    /// madeira-bcd: App ID -> why the game's choice is Skip, for the log (`choice=skip ... why=`): `picked` (the user
    /// picked it), `install-start` (a start that runs installs turned it, its session not read yet), then what that
    /// session came to once read: `ok`, `failed`, `unfinished`, `incomplete`, `nothing-ran`. Absent: set before this
    /// field existed. Optional.
    var skipWhy: [String: String]? = nil
    /// madeira-bcd: App IDs whose installs were offered once more after a session in which nothing ran (settleChoice);
    /// kept until the installs are tried or the user picks a choice, so the offer is made once and cannot loop.
    var offeredAgain: [String]? = nil

    static func load(prefix: URL) -> DockInstallLedger {
        let file = prefix.appendingPathComponent(fileName)
        guard let contents = try? Data(contentsOf: file), contents.count <= 1 << 20,
              let ledger = try? JSONDecoder().decode(DockInstallLedger.self, from: contents) else { return DockInstallLedger() }
        return ledger
    }
    func save(prefix: URL) throws {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(self).write(to: prefix.appendingPathComponent(Self.fileName), options: .atomic)
    }
    func runsNext(_ appID: Int) -> Bool { runNext[String(appID)] != false }
    func request(_ appID: Int) -> DockInstallRequest? {
        guard let raw = requests?[String(appID)] else { return nil }
        return DockInstallRequest(rawValue: raw)
    }
    func unfinishedRuns(_ appID: Int) -> [String] { unfinished?[String(appID)] ?? [] }
    func failedRuns(_ appID: Int) -> [String] { failed?[String(appID)] ?? [] }
    func whySkip(_ appID: Int) -> String? { skipWhy?[String(appID)] }

    /// Sets or clears a game's request; an empty map is stored as absent.
    mutating func setRequest(_ appID: Int, _ request: DockInstallRequest?) {
        var map = requests ?? [:]
        map[String(appID)] = request?.rawValue
        requests = map.isEmpty ? nil : map
    }

    /// Sets or clears a game's unfinished entries; an empty map is stored as absent.
    mutating func setUnfinished(_ appID: Int, _ names: [String]) {
        var map = unfinished ?? [:]
        map[String(appID)] = names.isEmpty ? nil : names
        unfinished = map.isEmpty ? nil : map
    }

    /// madeira-bcd: sets or clears a game's failed entries; an empty map is stored as absent.
    mutating func setFailed(_ appID: Int, _ names: [String]) {
        var map = failed ?? [:]
        map[String(appID)] = names.isEmpty ? nil : names
        failed = map.isEmpty ? nil : map
    }

    /// madeira-bcd: sets (nil: clears) why a game's choice is Skip; an empty map is stored as absent.
    mutating func setSkipWhy(_ appID: Int, _ why: String?) {
        var map = skipWhy ?? [:]
        map[String(appID)] = why
        skipWhy = map.isEmpty ? nil : map
    }

    /// madeira-bcd: a game's choice was set from outside a session (the user's pick, a Reset): nothing the last install
    /// session did to it counts any more, and a later session starts its own cycle (one more offer).
    mutating func forgetChoiceHistory(_ appID: Int) {
        setSkipWhy(appID, nil)
        if let list = offeredAgain, list.contains(String(appID)) {
            let rest = list.filter { $0 != String(appID) }
            offeredAgain = rest.isEmpty ? nil : rest
        }
    }

    /// madeira-bcd: what a game's choice does once the session that turned it to Skip has been read (absorbResults).
    /// A start that runs installs turns the choice to Skip when it plans them, before anything runs; if the session then
    /// never got to start a single program (the app was closed first, the session failed to boot), nothing was tried and
    /// the programs would be skipped from then on (Halo: The Master Chief Collection, VJSK5K: "the batch did not start",
    /// then `choice=skip pending=1`). That is the one case turned back to Run, once. A program that failed or never
    /// finished stays skipped (the stall hint and the restart alert say so), and so does a choice the user made after the
    /// session. Returns whether the programs are offered again. `retry` false: the old behaviour, only the reason is kept.
    mutating func settleChoice(_ appID: Int, after outcome: DockSessionOutcome, retry: Bool) -> Bool {
        let key = String(appID)
        guard skipWhy?[key] == "install-start" else { return false }
        if outcome == .nothingRan, retry, runNext[key] == false, !(offeredAgain ?? []).contains(key) {
            runNext[key] = nil
            setSkipWhy(appID, nil)
            offeredAgain = (offeredAgain ?? []) + [key]
            return true
        }
        setSkipWhy(appID, outcome.rawValue)
        if outcome != .nothingRan, let list = offeredAgain, list.contains(key) {
            let rest = list.filter { $0 != key }
            offeredAgain = rest.isEmpty ? nil : rest
        }
        return false
    }
}

/// madeira-bcd: what a finished or abandoned batch came to, read from its result file (absorbResults).
enum DockSessionOutcome: String, Sendable {
    case nothingRan = "nothing-ran"   // no program started and none exited (no result file, or only its first lines)
    case failed                       // a program exited with a failing status
    case unfinished                   // a program started and never exited
    case incomplete                   // none failed or hung, but some never started
    case ok                           // every program exited with a success status
}

// MARK: - A Dock start's one-time installs

/// The app side: plan the game's programs before a Dock start, write the batch the
/// session runs before the host, report progress, and record results at the next start.
///
/// Switches (`env.NAME` in bcd.cfg; "0" turns one off):
/// - `MADEIRA_DOCK_INSTALLERS`: the whole feature.
/// - `MADEIRA_INSTALL_DEFAULT_KEY`: entries without a "HasRunKey" use Steam's per-app key
///   (off: such entries are ignored).
/// - `MADEIRA_DOCK_INSTALL_CHOICE`: the per-game One-time installs choice (off: every start
///   runs whatever is pending).
/// - `MADEIRA_DOCK_INSTALL_SCM`: the service-manager step before the first installer.
/// - `MADEIRA_DOCK_INSTALL_SERVER_SYNC`: a start that runs installers turns madsync off for
///   its session (off: madsync stays and the service-manager step is left out).
/// - `MADEIRA_DOTNET_FUSION`: place Wine's fusion.dll in the .NET 2.0 folder when missing.
/// - `MADEIRA_DOCK_DOTNET_MONO` (madeira-bcd): a .NET Framework setup of an install script is not run
///   (off: it runs as before); with Wine Mono on this device its record is written as a finished
///   run's, without it the program waits until Wine Mono is downloaded (Settings › .NET Framework).
/// - `MADEIRA_DOCK_DIRECTX_PROVIDED` (madeira-bcd): a DirectX setup that already failed once in a game is not run
///   again; BCD ships the DirectX runtime libraries itself, so its record is written like a finished run's
///   (off: it stays pending, as before). A DirectX setup that succeeds is never touched.
/// - `MADEIRA_DOCK_INSTALL_RETRY` (madeira-bcd): installs whose session never started a single program (the app was
///   closed first, the session failed to boot) are offered once more at the next start instead of being skipped for good
///   (off: they are skipped, as before). A program that failed or never finished stays skipped.
/// - `MADEIRA_DOCK_INSTALL_SEPARATE` (madeira-bcd): a start that runs installers runs only
///   them; the game starts after Madeira is restarted (off: the host follows in the same session).
/// - `MADEIRA_DOCK_RESET_CLEAN` (madeira-bcd): Reset also moves what the installers put into
///   the prefix into a backup (DockReset.swift; off: only the records are forgotten).
/// - `MADEIRA_DOCK_SCRIPT_REGISTRY` (madeira-bcd, round 591; the one switch here that is off unless set): the
///   "Registry" values of the game's install script are written into the prefix before the start
///   (DockScriptRegistry, below), from the game's own lines or bcd.cfg.
@MainActor
enum DockInstallers {
    static let scriptName = "bcd-dock-installers.cmd"
    static let resultName = "bcd-dock-installers.result"

    /// The batch this start runs before the host (a C:\ path), nil when none runs.
    private(set) static var script: String?
    /// madeira-bcd: this start's session runs only the installers, not the host. In one session
    /// the installers' copies in the JIT pool left Red Dead Redemption 2 too little room at its
    /// start, and it stopped while loading (log 2026-10-12 08:13: "[jit-pool] EXHAUSTED"); a
    /// session of their own also leaves madsync on for the game. One session runs per app run,
    /// so the game starts after a restart of Madeira.
    private(set) static var installsOnly = false
    nonisolated static let restartWords = "The game starts after a restart of BCD: swipe BCD away in the app switcher, open it again, then start the game."
    /// What the starting screen says once the installs ended, until the session has closed (the
    /// wineserver writes the registry to disk as it stops; Madeira asks for the restart then).
    nonisolated static let closingWords = "This session is closing now. Wait until BCD asks you to restart it."
    /// The seconds after the batch at which a session that is still open is offered the restart (MadeiraDockModel): one number for the offer
    /// and the countdown below.
    nonisolated static let restartOfferDelay = 45
    /// madeira-bcd (round 624, agent R S25; 1EXE6P: both installs succeeded, the screen said only that the session was closing, and the player
    /// left 35 s later thinking it had crashed): between the end of the installs and the restart offer the starting screen counts down.
    static func closingCountdown(now: Date = Date()) -> String {
        guard let done = finishedAt else { return closingWords }
        let left = restartOfferDelay - Int(now.timeIntervalSince(done))
        guard left > 0 else { return closingWords }
        return "The installs are finished. BCD is saving the setup and asks you to restart in about \(left) second\(left == 1 ? "" : "s"). "
            + "Do not close BCD yet."
    }
    /// madeira-bcd: the game's lines a session of one-time installs runs without. Social Club's
    /// layout 2 (env.MADEIRA_SC_PA_POOLS) and RDR2's hold (env.MADEIRA_RDR2_VA_HOLD) take the
    /// 4 GB slots 32-bit programs run in, so the Visual C++ setups' second 32-bit process could
    /// not start ([wow-window] REJECTED, BOOT FAILED 'guest-window-reserve', exit 0x8007054f; logs
    /// 2026-10-03 16:00 and 2026-10-12 10:00). Both serve the game's Social Club only; the game's
    /// other lines stay (the Rockstar Service install needs env.MADEIRA_EXECREQ_LEAVE).
    nonisolated static let installSessionDroppedKeys: Set<String> = ["env.MADEIRA_SC_PA_POOLS", "env.MADEIRA_RDR2_VA_HOLD"]

    /// madeira-bcd: why a .NET Framework setup is held back on a device without Wine Mono (the note and the log).
    nonisolated static let dotNetNeedsMono = "needs Wine Mono; download it in Settings › .NET Framework"

    /// madeira-bcd: for a session of one-time installs (the app sets MADEIRA_DOCK_INSTALLS_SESSION),
    /// the game's config file (MADEIRA_CFG_GAME) is replaced by a copy without
    /// `installSessionDroppedKeys`. Returns the keys left out. MADEIRA_DOCK_INSTALL_LAYOUT=0 keeps
    /// every line.
    nonisolated static func applyInstallSessionConfig(directory: URL? = MadeiraConfig.gameURL?.deletingLastPathComponent()) -> [String] {
        guard ProcessInfo.processInfo.environment["MADEIRA_DOCK_INSTALLS_SESSION"] == "1",
              flag("MADEIRA_DOCK_INSTALL_LAYOUT"),   // 0: a session of one-time installs keeps the game's layout lines
              let path = ProcessInfo.processInfo.environment["MADEIRA_CFG_GAME"], !path.isEmpty,
              let text = try? String(contentsOfFile: path, encoding: .utf8), let directory else { return [] }
        var dropped: [String] = []
        let kept = text.components(separatedBy: "\n").filter { line in
            let key = line.split(separator: "=", maxSplits: 1).first.map { $0.trimmingCharacters(in: .whitespaces) } ?? ""
            guard installSessionDroppedKeys.contains(key) else { return true }
            dropped.append(key)
            return false
        }
        guard !dropped.isEmpty else { return [] }
        let copy = directory.appendingPathComponent("bcd-game-installs.cfg")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try kept.joined(separator: "\n").write(to: copy, atomically: true, encoding: .utf8)
        } catch {
            return []
        }
        setenv("MADEIRA_CFG_GAME", copy.path, 1)
        return dropped
    }
    /// This start's batch, in batch order (the result lines refer to a program by its place): poll names a failed
    /// program's file type from it. Empty for a start without installs.
    private static var sessionPrograms: [SteamInstallProcess] = []
    /// This start turned the game's choice to Skip (it runs pending programs): they are not run again at the next
    /// start, a failed one included. The restart alert says so.
    private(set) static var skipsNextStart = false
    /// madeira-bcd: a DirectX setup of this start's batch exited with a failing status (poll): the restart alert says BCD
    /// provides the DirectX runtime libraries itself.
    private(set) static var failedDirectX = false
    /// This start runs installers and turns madsync off for its session.
    private(set) static var serverSync = false
    /// This start's plan in words (the Dock sheet's status), nil when there is nothing to say.
    private(set) static var note: String?
    /// When poll first read the batch's end: the host starts next. nil before, and
    /// for a start without installs.
    private(set) static var finishedAt: Date?
    private static var logged: Set<String> = []
    /// The program poll last saw running and when it first saw it (the stall notice).
    private static var runningSince: (index: Int, at: Date)?
    /// After this long on one program, poll logs a notice and the status names the
    /// minutes. Nothing is stopped: an installer may legitimately take long.
    static let stallSeconds = 600
    /// madeira-bcd (round 624, agent A S4, Fall Guys 7NKYWT: the player closed BCD after 172 s, before the 600 s notice): in an installs-only
    /// session a program still running after this long gets one log line and the way out in the status. Text and log only.
    static let slowSeconds = 120
    /// The Dock app id of the batch prepare set up (the 120 s line names it; poll has no game).
    private static var sessionAppID = 0

    nonisolated static func flag(_ name: String) -> Bool { SteamSignIn.flag(name, default: true) }
    nonisolated static var enabled: Bool { flag("MADEIRA_DOCK_INSTALLERS") }   // 0: Dock starts run no install-script programs
    nonisolated static var choiceEnabled: Bool { enabled && flag("MADEIRA_DOCK_INSTALL_CHOICE") }   // 0: no per-game choice, every start runs what is pending

    /// Whether bcd.cfg selects madsync, as the engine reads it (madeira_cfg_sync_engine
    /// in build/madeira_cfg.h): only inproc-sync set to 1/on/true/yes; unset is fastsync,
    /// the default engine.
    static var madsyncConfigured: Bool {
        guard let value = MadeiraConfig.get("inproc-sync") else { return false }
        return ["1", "on", "true", "yes"].contains(value)
    }

    // MARK: Finding programs

    /// The install scripts' programs of a game: its own folder (one level down) and the
    /// shared redistributables Steam keeps next to it, and how many scripts were read.
    nonisolated static func found(_ game: DockGame, drive: URL,
                                  defaultKey: Bool = DockInstallers.flag("MADEIRA_INSTALL_DEFAULT_KEY")) -> (processes: [SteamInstallProcess], scripts: Int) {
        let common = game.library + "/common"
        let shared = common + "/Steamworks Shared"
        func windows(_ relative: String) -> String { "C:\\" + relative.replacingOccurrences(of: "/", with: "\\") }
        let roots: [(URL, Int, String, Int?)] = [
            (drive.appendingPathComponent(common + "/" + game.installDir, isDirectory: true), 1, game.windowsInstallPath,
             defaultKey ? game.id : nil),
            (drive.appendingPathComponent(shared + "/_CommonRedist", isDirectory: true), 3, windows(shared), nil),
        ]
        var result: [SteamInstallProcess] = []
        var scripts = 0
        for (root, depth, installDir, owner) in roots {
            guard inside(root, drive: drive) else { continue }
            for file in SteamInstallScripts.scripts(folder: root, depth: depth) {
                guard let contents = try? Data(contentsOf: file) else { continue }
                scripts += 1
                for process in DockInstallScripts.processes(script: contents, installDir: installDir, appID: owner)
                where !result.contains(process) {
                    result.append(process)
                }
            }
        }
        return (result, scripts)
    }

    /// The number of install-script programs a game has (the Dock sheet's list).
    nonisolated static func programCount(_ game: DockGame, drive: URL) -> Int {
        enabled ? found(game, drive: drive).processes.count : 0
    }

    nonisolated static func inside(_ url: URL, drive: URL) -> Bool {
        url.resolvingSymlinksInPath().standardizedFileURL.path.hasPrefix(drive.resolvingSymlinksInPath().standardizedFileURL.path + "/")
    }

    /// A C:\ path resolved on disk the way Wine resolves it (case-insensitive), staying
    /// inside drive_c; nil when it does not exist.
    nonisolated static func resolve(_ windowsPath: String, drive: URL) -> URL? {
        guard windowsPath.uppercased().hasPrefix("C:\\"), windowsPath.utf8.count < 1024 else { return nil }
        let parts = windowsPath.dropFirst(3).split(separator: "\\", omittingEmptySubsequences: false).map(String.init)
        guard !parts.isEmpty, !parts.contains(where: { $0.isEmpty || $0 == "." || $0 == ".." || $0.contains("/") }) else { return nil }
        var current = drive
        for part in parts {
            let exact = current.appendingPathComponent(part)
            if FileManager.default.fileExists(atPath: exact.path) { current = exact }
            else {
                guard let names = try? FileManager.default.contentsOfDirectory(atPath: current.path),
                      let match = names.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame }) else { return nil }
                current.appendPathComponent(match)
            }
            guard inside(current, drive: drive) else { return nil }
        }
        return current
    }

    /// The PE machine of an executable (0x14c: 32-bit x86, 0x8664: x64), nil when unreadable.
    nonisolated static func machine(_ file: URL) -> UInt16? {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        guard let dos = try? handle.read(upToCount: 64), dos.count == 64, dos[0] == 0x4d, dos[1] == 0x5a else { return nil }
        let offset = (0..<4).reduce(UInt64(0)) { $0 | UInt64(dos[60 + $1]) << ($1 * 8) }
        guard offset >= 64, offset <= 1 << 20, (try? handle.seek(toOffset: offset)) != nil,
              let header = try? handle.read(upToCount: 6), header.count == 6, header.starts(with: [0x50, 0x45, 0, 0]) else { return nil }
        return UInt16(header[4]) | UInt16(header[5]) << 8
    }

    /// madeira-bcd: a program's PE machine for the log: "0x8664" (x64), "0x14c" (32-bit), "0xaa64" (ARM64), "-" when
    /// the file cannot be found or read.
    nonisolated static func machineText(_ process: SteamInstallProcess, drive: URL) -> String {
        guard let file = resolve(process.executable, drive: drive), let machine = machine(file) else { return "-" }
        return "0x" + String(machine, radix: 16)
    }

    /// Why this build cannot run a program, nil when it can. Decided by what the program is
    /// (a Windows Installer package, a 32-bit executable) and what the bundle has, never by
    /// its name: a package needs msiexec.exe, a 32-bit executable needs 32-bit support.
    nonisolated static func runnable(_ process: SteamInstallProcess, drive: URL, has32Bit: Bool, hasMsiexec: Bool) -> String? {
        if process.executable.lowercased().hasSuffix(".msi") { return hasMsiexec ? nil : "Windows Installer package" }
        guard let file = resolve(process.executable, drive: drive), let machine = machine(file) else { return nil }
        return machine == 0x14c && !has32Bit ? "32-bit installer" : nil
    }

    nonisolated static var bundleHas32Bit: Bool { bundled("i386-windows") }
    nonisolated static var bundleHasMsiexec: Bool {
        ["aarch64-windows", "arm64ec-windows", "i386-windows"].contains { bundled($0 + "/msiexec.exe") }
    }
    nonisolated private static func bundled(_ relative: String) -> Bool {
        guard let root = Bundle.main.resourceURL else { return false }
        return FileManager.default.fileExists(atPath: root.appendingPathComponent(relative).path)
    }

    // MARK: The choice

    nonisolated static func runsNext(_ appID: Int, prefix: URL) -> Bool { DockInstallLedger.load(prefix: prefix).runsNext(appID) }

    static func setRunsNext(_ appID: Int, _ run: Bool, prefix: URL) {
        var ledger = DockInstallLedger.load(prefix: prefix)
        ledger.runNext[String(appID)] = run ? nil : false
        // madeira-bcd: a choice made here is the user's, not an install session's (settleChoice leaves it alone).
        ledger.forgetChoiceHistory(appID)
        if !run { ledger.setSkipWhy(appID, "picked") }
        do { try ledger.save(prefix: prefix) }
        catch { LogStore.shared.log("[dock-installers] app=\(appID) choice not saved: \(error.localizedDescription)", level: .error); return }
        LogStore.shared.log("[dock-installers] app=\(appID) choice=\(run ? "run" : "skip")")
    }

    /// Saves (or with nil cancels) a game's Record as done / Reset request. Nothing changes in
    /// the prefix now: the game's next Dock start applies it before planning (applyRequest).
    static func setRequest(_ appID: Int, _ request: DockInstallRequest?, prefix: URL) {
        var ledger = DockInstallLedger.load(prefix: prefix)
        ledger.setRequest(appID, request)
        do { try ledger.save(prefix: prefix) }
        catch { LogStore.shared.log("[dock-installers] app=\(appID) request not saved: \(error.localizedDescription)", level: .error); return }
        LogStore.shared.log("[dock-installers] app=\(appID) request=\(request?.rawValue ?? "none"); applied at the next Dock start")
    }

    /// The runs of the game's own install-script programs (under its install folder): what
    /// Record as done and Reset act on. Shared Steamworks redistributables serve every game
    /// and are left as they are.
    nonisolated static func own(_ found: [SteamInstallProcess], game: DockGame) -> [SteamInstallRun] {
        let folder = game.windowsInstallPath.lowercased() + "\\"
        var runs: [SteamInstallRun] = []
        for process in found where process.executable.lowercased().hasPrefix(folder) && !runs.contains(process.run) {
            runs.append(process.run)
        }
        return runs
    }

    /// Applies the game's saved request, if any, while no session runs: `.record` writes the
    /// own programs' records (as a finished run would), `.reset` removes them and sets the
    /// choice to Run at next start. The request is cleared either way, and with it the game's
    /// unfinished entries. Returns the request applied.
    /// madeira-bcd: a reset first moves what the installers put into the prefix into a backup
    /// (DockReset.clean; `.resetSignOut` takes the Rockstar sign-in data too). Those are shared:
    /// the games in `others` that record under the same keys or run the same installers get
    /// Run at next start as well (shareReset), so each installs them again.
    @discardableResult
    static func applyRequest(_ game: DockGame, found: [SteamInstallProcess], prefix: URL,
                             drive: URL? = nil, others: [DockGame] = []) -> DockInstallRequest? {
        var ledger = DockInstallLedger.load(prefix: prefix)
        let app = game.id
        guard ledger.requests?[String(app)] != nil else { return nil }
        let request = ledger.request(app)
        ledger.setRequest(app, nil)
        let runs = own(found, game: game)
        let names = runs.map { DockInstallScripts.label($0) }.joined(separator: ",")
        let shared = Set(found.map(\.run)).subtracting(runs).count
        let drive = drive ?? prefix.appendingPathComponent("drive_c", isDirectory: true)
        var changed = 0
        var failure: String?
        var cleaned: DockReset.Summary?
        var alsoRun: [String] = []
        switch request {
        case .record?:
            do { changed = try SteamInstallScripts.mark(runs, prefix: prefix) } catch { failure = error.localizedDescription }
        case .reset?, .resetSignOut?:
            if flag("MADEIRA_DOCK_RESET_CLEAN") {   // 0: Reset only forgets the records; installed files stay
                cleaned = DockReset.clean(drive: drive, prefix: prefix, documents: prefix.deletingLastPathComponent(),
                                          appID: app, signOut: request == .resetSignOut, baseline: DockReset.baselineProvider())
            }
            do { changed = try SteamInstallScripts.unmark(runs, prefix: prefix) } catch { failure = error.localizedDescription }
            if failure == nil {
                ledger.runNext[String(app)] = nil
                // madeira-bcd: a clean start for these installs: no earlier failure, no earlier reason for Skip.
                ledger.setFailed(app, []); ledger.forgetChoiceHistory(app)
                if cleaned != nil {
                    alsoRun = shareReset(app, runs: runs, found: found, others: others, drive: drive, prefix: prefix, ledger: &ledger)
                }
            }
        case nil:
            failure = "unknown request"
        }
        if failure == nil { ledger.setUnfinished(app, []) }
        do { try ledger.save(prefix: prefix) }
        catch { LogStore.shared.log("[dock-installers] app=\(app) ledger not saved: \(error.localizedDescription)", level: .error) }
        let tag = request == .record ? "[dock-installers]" : "[dock-reset]"
        let what = request == .record ? "record-as-done: records written"
            : cleaned != nil ? "reset: installed components moved to the backup, records removed, choice=run"
                             : "reset: records removed, choice=run; installed files left in place"
        if let cleaned {
            let failed = cleaned.failures.isEmpty ? "" : " failures=\(cleaned.failures.count)"
            LogStore.shared.log("[dock-reset] app=\(app) clean sign-out=\(request == .resetSignOut ? 1 : 0) moved=\(cleaned.moved.count) " +
                                "entries=\(cleaned.entries.count) keys=\(cleaned.keys) values=\(cleaned.values) " +
                                "template-keys=\(cleaned.restored) backup=Documents/\(cleaned.backup)" + failed,
                                level: cleaned.failures.isEmpty ? .info : .error)
            for path in cleaned.moved { LogStore.shared.log("[dock-reset]   moved C:\\" + path.replacingOccurrences(of: "/", with: "\\")) }
            for entry in cleaned.entries { LogStore.shared.log("[dock-reset]   uninstall entry removed: " + entry) }
            for problem in cleaned.failures { LogStore.shared.log("[dock-reset]   failed: " + problem, level: .error) }
            for other in alsoRun {
                LogStore.shared.log("[dock-reset] app=\(app) also \(other): records removed, choice=run; they share these installs")
            }
        }
        if let failure {
            LogStore.shared.log("\(tag) app=\(app) request=\(request?.rawValue ?? "?") failed: \(failure)", level: .error)
        } else {
            LogStore.shared.log("\(tag) app=\(app) \(what) programs=\(names.isEmpty ? "-" : names) values=\(changed) shared-kept=\(shared)")
        }
        return failure == nil ? request : nil
    }

    /// madeira-bcd: what a reset removed (DockReset.clean) belongs to the prefix, not to one
    /// game. Another installed game whose own programs record under one of the same keys, or
    /// run a program of the same file name (its own copy of the same installer), loses those
    /// records and gets Run at next start, so its next start installs them again. Returns
    /// "app=<id> programs=<labels> values=<removed>" for each such game, for the log.
    static func shareReset(_ app: Int, runs: [SteamInstallRun], found: [SteamInstallProcess], others: [DockGame],
                           drive: URL, prefix: URL, ledger: inout DockInstallLedger) -> [String] {
        func file(_ path: String) -> String { path.split(separator: "\\").last.map { $0.lowercased() } ?? "" }
        func key(_ run: SteamInstallRun) -> String { run.hive.rawValue + "|" + run.key.lowercased() }
        let keys = Set(runs.map { key($0) })
        let files = Set(found.filter { runs.contains($0.run) }.map { file($0.executable) })
        var result: [String] = []
        for other in others where other.id != app && other.installed {
            let theirs = Self.found(other, drive: drive).processes
            let ownRuns = own(theirs, game: other)
            var shared: [SteamInstallRun] = []
            for process in theirs where ownRuns.contains(process.run) && !shared.contains(process.run) {
                if keys.contains(key(process.run)) || files.contains(file(process.executable)) { shared.append(process.run) }
            }
            guard !shared.isEmpty else { continue }
            let removed: Int
            do { removed = try SteamInstallScripts.unmark(shared, prefix: prefix) }
            catch {
                LogStore.shared.log("[dock-reset] app=\(other.id) shared records not removed: \(error.localizedDescription)", level: .error)
                continue
            }
            ledger.runNext[String(other.id)] = nil
            ledger.setFailed(other.id, []); ledger.forgetChoiceHistory(other.id)
            result.append("app=\(other.id) programs=\(shared.map { DockInstallScripts.label($0) }.joined(separator: ",")) values=\(removed)")
        }
        return result
    }

    // MARK: A start

    /// Called right before a Dock start, while no session runs (the registry is on disk).
    /// Records the previous batch's results, plans the game's programs and writes the batch.
    /// `others`: the prefix's other Dock games (a reset's shared installs, applyRequest).
    static func prepare(_ game: DockGame, drive: URL, prefix: URL, others: [DockGame] = [],
                        has32Bit: Bool = DockInstallers.bundleHas32Bit, hasMsiexec: Bool = DockInstallers.bundleHasMsiexec,
                        hasWineMono: Bool = WineMonoModel.available,
                        fusionSource: URL? = Bundle.main.resourceURL?.appendingPathComponent("i386-windows/fusion.dll")) {
        script = nil; serverSync = false; note = nil; finishedAt = nil; logged = []; runningSince = nil; installsOnly = false
        allSucceeded = false; sessionPrograms = []; skipsNextStart = false; failedDirectX = false; sessionAppID = 0
        let app = game.id
        sessionAppID = app
        let batchFile = drive.appendingPathComponent(scriptName)
        try? FileManager.default.removeItem(at: batchFile)
        let offeredAgain = absorbResults(reason: "next-start", drive: drive, prefix: prefix)
        try? FileManager.default.removeItem(at: drive.appendingPathComponent(resultName))
        // madeira-bcd: a Vulkan loader an installer wrote into system32 goes back out, so the
        // session links Madeira's stand-in (DockReset.restoreStandIn).
        if flag("MADEIRA_DOCK_VULKAN_STANDIN"),   // 0: a Vulkan loader an installer wrote stays in system32
           let moved = DockReset.restoreStandIn(drive: drive, documents: prefix.deletingLastPathComponent(),
                                                standIn: Bundle.main.resourceURL?.appendingPathComponent("arm64ec-windows/vulkan-1.dll")) {
            LogStore.shared.log("[dock-installers] C:\\" + moved.replacingOccurrences(of: "/", with: "\\") +
                                " (an installer's Vulkan loader) moved to Documents/bcd-reset-backup; this session links BCD's stand-in")
        }
        // madeira-bcd (round 591): the values of the game's install script that the Windows client writes into the registry
        // and this feature's Run Process entries do not (env.MADEIRA_DOCK_SCRIPT_REGISTRY; off: this line does nothing).
        DockScriptRegistry.prepare(game, drive: drive, prefix: prefix)
        guard enabled else {
            LogStore.shared.log("[dock-installers] app=\(app) off (MADEIRA_DOCK_INSTALLERS=0)"); return
        }
        let defaultKey = flag("MADEIRA_INSTALL_DEFAULT_KEY")
        let (found, scripts) = Self.found(game, drive: drive, defaultKey: defaultKey)
        // A Record as done / Reset request saved since the last start, before the plan reads
        // the registry.
        applyRequest(game, found: found, prefix: prefix, drive: drive, others: others)
        guard !found.isEmpty else {
            LogStore.shared.log("[dock-installers] app=\(app) scripts=\(scripts) programs=0"); return
        }
        let registry = [SteamInstallRun.Hive.machine: "system.reg", .user: "user.reg"].mapValues {
            (try? String(contentsOf: prefix.appendingPathComponent($0), encoding: .utf8)) ?? ""
        }
        // madeira-bcd: a .NET Framework setup never finished under Wine (ajan6-dotnet: Batman: Arkham City GOTY's .NET 3.5
        // setup stayed open in builds 574 and 576, Star Wars: The Clone Wars Republic Heroes' exited 5100), and Wine Mono
        // is the runtime the game uses; the prefix template already carries Wine's NDP markers (v1.1 to v4 Full). With
        // Wine Mono the program is not run and its record is written; without it the program waits for Settings › .NET Framework.
        let dotNetMono = flag("MADEIRA_DOCK_DOTNET_MONO")   // 0: these setups run as before
        var ledger = DockInstallLedger.load(prefix: prefix)
        // madeira-bcd: a DirectX setup that already failed once in this game (absorbResults kept its entry name) is not run
        // again: BCD ships what it installs (D3DX9/10/11, D3DCompiler, XAudio2, X3DAudio, XAPOFX, XInput) as Wine's builtin
        // DLLs, so its record is written like a finished run's. One that succeeded is `done` and never gets here; one that
        // never failed here (first run, a hung or never-started one) is planned as before. The setup failed in the fork's logs
        // with 6, -9 and 0xC0000018 (infinst.exe of Saints Row: The Third, T3P8HK) and exited 0 in Just Cause 2 (JYFG4D).
        let directXProvided = flag("MADEIRA_DOCK_DIRECTX_PROVIDED")   // 0: a failed DirectX setup stays pending, as before
        let failedBefore = Set(ledger.failedRuns(app))
        let items = DockInstallScripts.plan(found,
                                            done: { DockInstallScripts.marked($0, in: registry[$0.hive] ?? "") },
                                            exists: { resolve($0, drive: drive) != nil },
                                            runnable: {
                                                if dotNetMono, !hasWineMono, DockInstallScripts.isDotNetFramework($0.executable) { return dotNetNeedsMono }
                                                return runnable($0, drive: drive, has32Bit: has32Bit, hasMsiexec: hasMsiexec)
                                            },
                                            provided: {
                                                (dotNetMono && hasWineMono && DockInstallScripts.isDotNetFramework($0.executable))
                                                    || (directXProvided && DockInstallScripts.isDirectXSetup($0.executable) && failedBefore.contains($0.run.name))
                                            })
        let providedRuns = DockInstallScripts.providedRuns(items)
        var providedWritten = 0
        if !providedRuns.record.isEmpty {
            do { providedWritten = try SteamInstallScripts.mark(providedRuns.record, prefix: prefix) }
            catch { LogStore.shared.log("[dock-installers] app=\(app) records of the programs Wine Mono or BCD provides not written: \(error.localizedDescription)", level: .error) }
        }
        var pending = items.filter { $0.status == .pending }.map(\.process)
        // Programs the game's last batch started but never saw exit (absorbResults).
        let unfinished = Set(ledger.unfinishedRuns(app))
        // The game's One-time installs choice: absent or "Run at next start" runs the pending
        // programs at this start, after which the choice becomes Skip (unless some wait for a
        // later start); Skip starts the game without them. MADEIRA_DOCK_INSTALL_CHOICE=0 ignores it.
        let choiceOn = flag("MADEIRA_DOCK_INSTALL_CHOICE")
        let skipped = choiceOn && !ledger.runsNext(app) && !pending.isEmpty
        var turnedToSkip = false
        if skipped {
            // madeira-bcd: `why=` says how the choice came to be Skip (DockInstallLedger.skipWhy); GTA V Legacy's logs
            // (P36E54, YFYGD4, BT9TE3, 0DHNMC) showed `choice=skip pending=2` with nothing to say which start set it.
            LogStore.shared.log("[dock-installers] app=\(app) choice=skip pending=\(pending.count) why=\(ledger.whySkip(app) ?? "unknown")")
            pending = []
        } else if choiceOn && !pending.isEmpty && !items.contains(where: { $0.status == .limit }) {
            ledger.runNext[String(app)] = false
            ledger.setSkipWhy(app, "install-start")
            turnedToSkip = true
            skipsNextStart = true
            LogStore.shared.log("[dock-installers] app=\(app) choice=run pending=\(pending.count); the next start skips them")
        }
        // Installers expect a service manager, and a Dock session starts none before the
        // host. With madsync, Wine's services.exe never answered its RPC clients in the
        // fork's device runs, so the service step and then every installer waited; msiexec's
        // custom actions use the same RPC server. A start that runs installers therefore
        // turns madsync off for its whole session (MADEIRA_MADSYNC_SESSION=0, set by the
        // caller); later sessions use the configured engine again.
        // MADEIRA_DOCK_INSTALL_SERVER_SYNC=0 keeps madsync and leaves the service step out.
        let sessionOff = !pending.isEmpty && flag("MADEIRA_DOCK_INSTALL_SERVER_SYNC")
        let services: DockInstallServices = flag("MADEIRA_DOCK_INSTALL_SCM") && (sessionOff || !madsyncConfigured)
            ? .start(MadeiraDock.executable) : .off
        if !pending.isEmpty {
            do {
                let text = DockInstallScripts.batch(pending, resultFile: "C:\\" + resultName, services: services)
                try Data(text.utf8).write(to: batchFile, options: .atomic)
                script = "C:\\" + scriptName
                serverSync = sessionOff
                installsOnly = flag("MADEIRA_DOCK_INSTALL_SEPARATE")   // 0: the host follows the installers in the same session
            } catch {
                LogStore.shared.log("[dock-installers] app=\(app) batch not written: \(error.localizedDescription)", level: .error)
                // madeira-bcd: nothing will run, so the choice this start turned to Skip goes back: the programs are not
                // skipped for good by a batch that was never there.
                if turnedToSkip && flag("MADEIRA_DOCK_INSTALL_RETRY") {
                    ledger.runNext[String(app)] = nil; ledger.setSkipWhy(app, nil); skipsNextStart = false
                    LogStore.shared.log("[dock-installers] app=\(app) choice=run again: the batch was not written, so nothing is skipped")
                }
            }
        }
        // madeira-bcd: an entry recorded as provided is no longer "not finished" (an earlier build ran its installer and never saw it exit).
        let providedNames = Set(providedRuns.record.map(\.name))
        if !providedNames.isDisjoint(with: unfinished) {
            ledger.setUnfinished(app, ledger.unfinishedRuns(app).filter { !providedNames.contains($0) })
        }
        ledger.session = script != nil ? pending.map(\.run) : []
        sessionPrograms = script != nil ? pending : []
        ledger.sessionApp = ledger.session.isEmpty ? 0 : app
        do { try ledger.save(prefix: prefix) }
        catch { LogStore.shared.log("[dock-installers] app=\(app) ledger not saved: \(error.localizedDescription)", level: .error) }
        for item in items {
            let run = item.process.run
            let recorded = DockInstallScripts.recorded(run, in: registry[run.hive] ?? "").map { String($0) } ?? "-"
            let file = item.process.executable.split(separator: "\\").last.map(String.init) ?? ""
            let lastRun: String = item.status != .done && unfinished.contains(run.name) ? " last-run=unfinished" : ""
            LogStore.shared.log("[dock-installers] app=\(app) program=\(DockInstallScripts.label(run)) file=\(file) " +
                                "status=\(item.status.rawValue)\(item.reason.map { " reason=" + $0.replacingOccurrences(of: " ", with: "-") } ?? "") " +
                                "key=\(run.hive == .machine ? "HKLM" : "HKCU")\\\(run.key) recorded=\(recorded) minimum=\(run.value)\(lastRun) " +
                                "machine=\(machineText(item.process, drive: drive))")
        }
        for item in items where item.status == .provided || (item.status == .unsupported && item.reason == dotNetNeedsMono) {
            let run = item.process.run
            let file = item.process.executable.split(separator: "\\").last.map(String.init) ?? ""
            if item.status == .provided {
                let recorded = providedRuns.held.contains(run) ? "no, another program of this entry still has to run"
                    : providedWritten > 0 ? "yes, \(providedWritten) values" : "no values written (the prefix's registry is not seeded yet)"
                if DockInstallScripts.provider(item.process) == "BCD" {
                    LogStore.shared.log("[dock-installers] app=\(app) program=\(DockInstallScripts.label(run)) skipped: provided by BCD; " +
                                        "file=\(file) is DirectX's runtime setup, it failed in this game before, and BCD ships what it installs " +
                                        "(D3DX9/10/11, D3DCompiler, XAudio2, X3DAudio, XAPOFX, XInput) as Wine's builtin DLLs; record: \(recorded)")
                } else {
                    LogStore.shared.log("[dock-installers] app=\(app) program=\(DockInstallScripts.label(run)) skipped: provided by Wine Mono; " +
                                        "file=\(file) is a .NET Framework setup, which does not finish under Wine, and the prefix already carries Wine's NDP markers; record: \(recorded)")
                }
            } else {
                LogStore.shared.log("[dock-installers] app=\(app) program=\(DockInstallScripts.label(run)) skipped: file=\(file) is a .NET Framework setup and this device has no Wine Mono; " +
                                    "Settings › .NET Framework downloads it, and the program is recorded at the start after that")
            }
        }
        if skipped {
            note = "The one-time installs were skipped this time. To have them run, pick Run at next start under One-time installs."
        } else {
            note = DockInstallScripts.note(items)
            if installsOnly {
                note = (note.map { $0 + " " } ?? "") + "This start runs only the installs. Restart BCD after them, then start the game again."
            } else if serverSync && madsyncConfigured {
                note = (note.map { $0 + " " } ?? "") + "For this start, Wine's standard synchronization replaces madsync; madsync is back at the next start."
            }
            // madeira-bcd: the last install session never started a program; they are offered once more (settleChoice).
            if offeredAgain && script != nil {
                note = "The one-time installs did not get to start last time, so they run now. " + (note ?? "")
            }
        }
        // Those still not recorded and not run again now are named in the note, so an
        // installer that did its work but never closed can be recorded with Record as done.
        let stillUnfinished = items.filter { item in
            item.status != .done && item.status != .provided && unfinished.contains(item.process.run.name) && !pending.contains(item.process)
        }.map { DockInstallScripts.label($0.process.run) }
        if !stillUnfinished.isEmpty {
            let names = stillUnfinished.joined(separator: ", ")
            let words = "Not finished last time: \(names) (its installer never closed). If it did install, choose Record as done under One-time installs."
            note = (note.map { $0 + " " } ?? "") + words
            LogStore.shared.log("[dock-installers] app=\(app) unfinished=\(stillUnfinished.joined(separator: ",")): the last batch started it and saw no exit status")
        }
        if script != nil && flag("MADEIRA_DOTNET_FUSION") {
            LogStore.shared.log("[dock-installers] app=\(app) dotnet-fusion=\(placeDotNetFusion(drive: drive, source: fusionSource))")
        }
        LogStore.shared.log("[dock-installers] app=\(app) scripts=\(scripts) programs=\(found.count) pending=\(pending.count) " +
                            "done=\(items.filter { $0.status == .done }.count) missing=\(items.filter { $0.status == .missing }.count) " +
                            "unsupported=\(items.filter { $0.status == .unsupported }.count) provided=\(items.filter { $0.status == .provided }.count) default-key=\(defaultKey ? 1 : 0) " +
                            "services-step=\(script == nil ? "-" : services == .off ? "off" : "start") session-madsync-off=\(serverSync ? 1 : 0) " +
                            "installs-only=\(installsOnly ? 1 : 0)")
        if installsOnly {
            LogStore.shared.log("[dock-installers] app=\(app) this session runs only the one-time installs; the game starts after BCD is restarted (MADEIRA_DOCK_INSTALL_SEPARATE=0: in the same session)")
        }
    }

    /// An installer with a managed step (DirectX setup's is one) loads fusion.dll from the
    /// .NET 2.0 framework folder through mscoree. Wine normally gets that file from its Mono
    /// package, which Madeira does not ship, and such a setup ended with -9 in the fork's
    /// device runs. Before a start that runs installers, Wine's own builtin 32-bit fusion.dll
    /// is placed there, only when missing. Returns "placed", "present", "no-source" or "failed".
    static func placeDotNetFusion(drive: URL, source: URL?) -> String {
        let fm = FileManager.default
        let folder = drive.appendingPathComponent("windows/Microsoft.NET/Framework/v2.0.50727", isDirectory: true)
        let target = folder.appendingPathComponent("fusion.dll")
        if fm.fileExists(atPath: target.path) { return "present" }
        guard let source, fm.fileExists(atPath: source.path) else { return "no-source" }
        do {
            try fm.createDirectory(at: folder, withIntermediateDirectories: true)
            try fm.copyItem(at: source, to: target)
            return "placed"
        } catch { return "failed" }
    }

    /// The previous batch's exit statuses, read at the next Dock start (no session runs, so
    /// the registry is on disk): programs that succeeded are recorded done, one summary line
    /// is logged, and the result file and session list are cleared.
    /// madeira-bcd: the programs that exited with a failing status are kept in the ledger (a DirectX setup among them is
    /// provided by BCD from now on), and a session in which no program started is offered once more (settleChoice).
    /// Returns whether the game's installs are offered again.
    @discardableResult
    static func absorbResults(reason: String, drive: URL, prefix: URL) -> Bool {
        var ledger = DockInstallLedger.load(prefix: prefix)
        guard !ledger.session.isEmpty else { return false }
        let app = ledger.sessionApp
        let file = drive.appendingPathComponent(resultName)
        var outcome = DockSessionOutcome.nothingRan
        if let contents = try? Data(contentsOf: file), contents.count <= 16384 {
            let results = DockInstallScripts.results(String(decoding: contents, as: UTF8.self))
            var succeeded: [SteamInstallRun] = []
            var statuses: [String] = []
            var unfinished: [String] = []
            var failedNames = Set(ledger.failedRuns(app))
            for (offset, run) in ledger.session.enumerated() {
                let index = offset + 1, text = DockInstallScripts.label(run)
                if let status = results.exits[index] {
                    if DockInstallScripts.Results.succeeded(status) { succeeded.append(run); failedNames.remove(run.name) }
                    else { failedNames.insert(run.name) }
                    statuses.append("\(text)=\(status)")
                } else {
                    if results.started[index] != nil { unfinished.append(run.name) }
                    statuses.append("\(text)=\(results.started[index] != nil ? "unfinished" : "not-started")")
                }
            }
            // Kept until the game's next batch or request: the Dock note names them, and
            // Record as done can record one whose installer did its work but never closed.
            ledger.setUnfinished(app, unfinished)
            ledger.setFailed(app, failedNames.sorted())
            var written = 0
            do { written = try SteamInstallScripts.mark(succeeded, prefix: prefix) }
            catch { LogStore.shared.log("[dock-installers] app=\(app) results not recorded: \(error.localizedDescription)", level: .error) }
            let failed = ledger.session.count - succeeded.count
            LogStore.shared.log("[dock-installers] app=\(app) results reason=\(reason) succeeded=\(succeeded.count) failed=\(failed) " +
                                "recorded-values=\(written) services=\(results.services ?? "-") ended=\(results.ended ? 1 : 0) " +
                                "statuses=\(statuses.joined(separator: ","))", level: failed > 0 ? .error : .info)
            if results.started.isEmpty && results.exits.isEmpty { outcome = .nothingRan }
            else if !unfinished.isEmpty { outcome = .unfinished }
            else if results.exits.contains(where: { !DockInstallScripts.Results.succeeded($0.value) }) { outcome = .failed }
            else { outcome = succeeded.count == ledger.session.count ? .ok : .incomplete }
        } else {
            LogStore.shared.log("[dock-installers] app=\(app) results reason=\(reason) none: the batch did not start", level: .error)
        }
        // madeira-bcd: a session that never started a program leaves the programs unrecorded and the choice at Skip, which
        // would skip them for good (Halo: The Master Chief Collection, VJSK5K L40-45). Once, they are offered again.
        let offered = ledger.settleChoice(app, after: outcome, retry: flag("MADEIRA_DOCK_INSTALL_RETRY"))
        if offered {
            LogStore.shared.log("[dock-installers] app=\(app) choice=run again: the last session never started a program, so nothing was tried; " +
                                "the installs are offered once more (MADEIRA_DOCK_INSTALL_RETRY=0 skips them as before)")
        }
        try? FileManager.default.removeItem(at: file)
        ledger.session = []; ledger.sessionApp = 0
        try? ledger.save(prefix: prefix)
        return offered
    }

    /// The batch's progress from its result file, for the Dock sheet and the starting
    /// screen. New start and exit lines, and the end, are logged once; the text names the
    /// program running now and any that failed, and at the end how many succeeded. A program
    /// that runs `stallSeconds` or longer (counted from when poll first saw it) gets its
    /// minutes in the text and one log line; nothing is stopped.
    static func poll(drive: URL, now: Date = Date()) -> String? {
        guard script != nil else { return nil }
        let file = drive.appendingPathComponent(resultName)
        guard let contents = try? Data(contentsOf: file), contents.count <= 16384 else { return "Preparing this game's one-time installs…" }
        let results = DockInstallScripts.results(String(decoding: contents, as: UTF8.self))
        if let services = results.services, logged.insert("services").inserted {
            LogStore.shared.log("[dock-installers] services=\(services)", level: services == "failed" || services == "timeout" ? .error : .info)
        }
        for (index, program) in results.started.sorted(by: { $0.key < $1.key }) where logged.insert("s\(index)").inserted {
            LogStore.shared.log("[dock-installers] start \(index)/\(results.total) program=\(program)")
        }
        var failed: [String] = []
        for (index, status) in results.exits.sorted(by: { $0.key < $1.key }) {
            let ok = DockInstallScripts.Results.succeeded(status)
            if !ok { failed.append("\(results.started[index] ?? "#\(index)") (exit \(status))") }
            if !ok, index >= 1, index <= sessionPrograms.count, DockInstallScripts.isDirectXSetup(sessionPrograms[index - 1].executable) { failedDirectX = true }
            guard logged.insert("e\(index)").inserted else { continue }
            var detail = ""
            if !ok {
                // madeira-bcd: what the status means and what kind of program it was (an installer that cannot start
                // gives the log nothing else: Monster Hunter Stories 3 Trial, InstallerMessage.exe, exit -1073741701).
                detail = DockInstallScripts.failureDetail(status)
                if index >= 1, index <= sessionPrograms.count { detail += "; machine=" + machineText(sessionPrograms[index - 1], drive: drive) }
            }
            LogStore.shared.log("[dock-installers] exit \(index)/\(results.total) program=\(results.started[index] ?? "") status=\(status) " +
                                (ok ? "succeeded; recorded at the next start" : "failed; not recorded" + detail), level: ok ? .info : .error)
        }
        let failures = failed.isEmpty ? "" : "\nFailed: " + failed.joined(separator: ", ")
        if let running = results.running {
            if runningSince?.index != running { runningSince = (running, now) }
            let seconds = max(0, Int(now.timeIntervalSince(runningSince?.at ?? now)))
            let program = results.started[running] ?? ""
            var text = "One-time install \(running) of \(max(results.total, running)): \(program) is running…"
            // madeira-bcd (round 624, 7NKYWT): an installs-only session whose program has run two minutes without an exit status says so once
            // in the log, and the status gives the time and (when the next start skips the installs) the way out, before the 600 s notice.
            if installsOnly, seconds >= slowSeconds, seconds < stallSeconds, results.exits[running] == nil, logged.insert("slow\(running)").inserted {
                LogStore.shared.log("[dock-installers] app=\(sessionAppID) install still running after \(seconds) s: program=\(program) " +
                                    "(no exit status yet; the session is for the installs only)")
            }
            if installsOnly, seconds >= slowSeconds, seconds < stallSeconds {
                text += " (\(seconds / 60) min \(seconds % 60) s)"
                if skipsNextStart { text += "\nIf it does not finish, close BCD (swipe it away in the app switcher) and open it again: the next start skips the installs and starts the game." }
            }
            if seconds >= stallSeconds {
                var hint = "If its window has stopped changing, the installer may be done but still open. Closing the session now leaves it unrecorded; Record as done under One-time installs can record it afterwards."
                // madeira-bcd: the way out of an installer that never finishes. This start turned the choice to Skip,
                // so after a restart the game starts without the installs (Arkham City's .NET 3.5 setup, builds 574 and 576).
                if skipsNextStart { hint += " If it never finishes, close BCD (swipe it away in the app switcher) and open it again: the next start skips the installs and starts the game." }
                text += " (\(seconds / 60) min)\n" + hint
                if logged.insert("stall\(running)").inserted {
                    let line = "[dock-installers] still running \(running)/\(results.total) program=\(program) after \(seconds) s; the host waits for it (an installer window that never closes keeps the batch here)"
                    LogStore.shared.log(line, level: .error)
                }
            }
            return text + failures
        }
        guard results.ended else { return "This game's one-time installs are running…" + failures }
        let total = max(results.total, results.exits.count), succeeded = results.exits.count - failed.count
        if logged.insert("end").inserted {
            finishedAt = Date()
            LogStore.shared.log("[dock-installers] end succeeded=\(succeeded) failed=\(failed.count) of=\(total); " +
                                (installsOnly ? "the session ends, the game starts after a restart" : "the host starts next"))
        }
        allSucceeded = total > 0 && succeeded == total
        return "Done with the one-time installs: \(succeeded) of \(total) succeeded." + failures
    }

    /// madeira-bcd: the batch ended and every program in it succeeded (poll).
    private(set) static var allSucceeded = false

    /// madeira-bcd: the restart alert ("BCD needs a restart") after one-time installs, in the
    /// owner's words (2026-10-12): all succeeded, or the count and the failed programs. `ended`
    /// is poll's text, nil when the batch never ended.
    static func restartNotice(_ ended: String?) -> String {
        if allSucceeded { return "One-time installs completed successfully. Close BCD and open it again to play the game." }
        var text = ended ?? "The session for the one-time installs has ended."
        // madeira-bcd: a program that failed is not run again (the choice is Skip now): say so, and how to try it again.
        if skipsNextStart, text.contains("\nFailed: ") {
            text += "\nA program that failed is not run again: the game starts without it at the next start. To try it again, pick Run at next start under One-time installs."
        }
        // madeira-bcd: DirectX's setup is not needed here: its record is written at the next start (MADEIRA_DOCK_DIRECTX_PROVIDED).
        if failedDirectX, flag("MADEIRA_DOCK_DIRECTX_PROVIDED"), text.contains("\nFailed: ") {
            text += "\nDirectX's setup is not needed: BCD ships the DirectX runtime libraries (D3DX, XAudio2, XInput, D3DCompiler) itself. The game starts without it, and it is recorded as provided at this game's next start. Reset under One-time installs would run it again."
        }
        return text + "\nClose BCD and open it again to play the game."
    }
}

// MARK: - Valve's client service for a custom-executable (CEG) start

/// madeira-bcd (round 578, ajan6/ceg): Valve's "Steam Client Service" is installed before a
/// custom-executable (CEG) start, with Valve's own files, as Windows has it.
///
/// Valve's client prepares a CEG game's per-user executable through its Windows service
/// (`SteamService.exe /RunAsService`, demand start), which Windows' Steam installs once with
/// admin rights (`SteamService.exe /install`: it copies itself to `Common Files\Steam` and
/// creates the service there). The Dock host looks for the service, and when it is missing runs
/// that same installer in the session (madeira-dock src/scm.c). The installer is a 32-bit
/// program, and in 3 of 5 logged sessions it failed on an RPC to Wine's service manager
/// ("RpcAssoc_BindConnection receive failed with error 1726", exit 1); in two of them it was
/// the first call ("Failed to open service control manager") and the host ended with
/// ceg-result -5 (JYFG4D, build 574; the CoD Black Ops session before codbo-cur, build 576).
/// The 64-bit host's own calls to the same service manager worked every time.
///
/// So the service is already there when the session starts: the file Valve's installer would
/// copy (Valve's own `bin\SteamService.exe`, byte for byte, never over an existing file) and
/// the service's registry key with the values the installer's CreateService call passed
/// (own-process type 0x10, demand start 3, error control 1, the 32-bit client's WOW64 mark,
/// image path `"C:\Program Files (x86)\Common Files\Steam\SteamService.exe" /RunAsService`).
/// Wine's services.exe loads the key when it starts; Valve's client starts the service through
/// the service manager exactly as it did after Valve's installer had run. Nothing is written
/// while a session runs, nothing for a game without per-user executables, and when the file
/// cannot be placed the key is not written either (the host's own installer step is then all
/// there is, as before). The Dock host still checks and would still run Valve's installer.
///
/// `MADEIRA_DOCK_CEG_SERVICE` (on): `0` leaves Valve's service alone (no key, no file, no
/// Steam log lines after a failed CEG start).
@MainActor
enum DockCEGService {
    nonisolated static let serviceName = "Steam Client Service"
    /// What Valve's installer registers: its own copy under Common Files.
    nonisolated static let installedImage = "C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe"
    nonisolated static let imagePath = "\"" + installedImage + "\" /RunAsService"
    /// Valve's installer, in the client's own folder (the Steam client package ships it).
    nonisolated static let installerImage = "C:\\Program Files (x86)\\Steam\\bin\\SteamService.exe"
    /// Wine writes services under the control set CurrentControlSet points to.
    nonisolated static let registryKey = "System\\ControlSet001\\Services\\Steam Client Service"

    /// Whether system.reg's text already has the service's key (either way Wine names the
    /// control set; sub-keys do not count).
    nonisolated static func registered(in text: String) -> Bool {
        let pattern = #"^\[System\\\\(?:ControlSet\d+|CurrentControlSet)\\\\Services\\\\Steam Client Service\]"#
        guard let expression = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive, .anchorsMatchLines]) else { return false }
        return expression.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    /// The registry text of the service's key, in the form Wine's wineserver writes it (the
    /// values services.exe saves for a service a 32-bit client created).
    nonisolated static func section(now: Int) -> [String] {
        let escape = SteamInstallScripts.escape
        return ["[" + escape(registryKey) + "] \(now)",
                "\"DisplayName\"=\"" + escape(serviceName) + "\"",
                "\"ErrorControl\"=dword:00000001",
                "\"ImagePath\"=str(2):\"" + escape(imagePath) + "\"",
                "\"ObjectName\"=\"LocalSystem\"",
                "\"PreshutdownTimeout\"=dword:0002bf20",
                "\"Start\"=dword:00000003",
                "\"Type\"=dword:00000010",
                "\"WOW64\"=dword:00000001"]
    }

    /// system.reg's text with the service's key added; unchanged when it is there already or
    /// the text is not a Wine registry file.
    nonisolated static func register(in text: String, now: Int) -> (text: String, added: Bool) {
        guard text.hasPrefix("WINE REGISTRY Version 2"), !registered(in: text) else { return (text, false) }
        var lines = text.components(separatedBy: "\n")
        while let last = lines.last, last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { lines.removeLast() }
        lines += [""] + section(now: now) + [""]
        return (lines.joined(separator: "\n"), true)
    }

    /// A folder of C:\ by its path parts, matched the way Wine matches names (case-insensitive)
    /// and created when it does not exist; nil if it would leave drive_c.
    private nonisolated static func folder(_ parts: [String], drive: URL) -> URL? {
        let fm = FileManager.default
        var current = drive
        for part in parts {
            let names = (try? fm.contentsOfDirectory(atPath: current.path)) ?? []
            let match = names.first(where: { $0 == part }) ?? names.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame })
            current = current.appendingPathComponent(match ?? part, isDirectory: true)
            if match == nil { guard (try? fm.createDirectory(at: current, withIntermediateDirectories: false)) != nil else { return nil } }
            guard DockInstallers.inside(current, drive: drive) else { return nil }
        }
        return current
    }

    /// Valve's service program where the registered image path points: "present" (already
    /// there, never replaced), "copied" (Valve's installer copy, byte for byte), "no-installer"
    /// (the client folder has no readable bin\SteamService.exe) or "failed".
    nonisolated static func placeImage(drive: URL) -> String {
        let fm = FileManager.default
        if let target = DockInstallers.resolve(installedImage, drive: drive) {
            // A leftover that is not a program (an interrupted copy) is not "there".
            return DockInstallers.machine(target) != nil ? "present" : "failed"
        }
        guard let source = DockInstallers.resolve(installerImage, drive: drive), DockInstallers.machine(source) != nil else { return "no-installer" }
        guard let parent = folder(["Program Files (x86)", "Common Files", "Steam"], drive: drive) else { return "failed" }
        let target = parent.appendingPathComponent("SteamService.exe")
        do {
            try fm.copyItem(at: source, to: target)
            guard fm.contentsEqual(atPath: source.path, andPath: target.path) else { try? fm.removeItem(at: target); return "failed" }
            return "copied"
        } catch {
            try? fm.removeItem(at: target)
            return "failed"
        }
    }

    /// Called right before a Dock start, while no session runs (the registry is on disk), for
    /// a start that asks Valve's client to prepare per-user executables. Returns the outcome
    /// word the log line carries: off, already (key and file were there), added, no-registry
    /// (the prefix has no system.reg yet), no-installer, image-failed or write-failed.
    @discardableResult
    static func prepare(_ game: DockGame, drive: URL, prefix: URL, now: Date = Date()) -> String {
        guard game.customExecutables, SteamSignIn.flag("MADEIRA_DOCK_CEG", default: true) else { return "not-needed" }
        let app = game.id
        // A game with per-user executables (CEG) needs Valve's client service, which the Dock host's own installer step
        // often failed to install in the session: BCD installs it before the session starts, with Valve's own files.
        // env.MADEIRA_DOCK_CEG_SERVICE = 0 leaves it to the host, as before, and logs no Steam log lines after a failed start.
        guard SteamSignIn.flag("MADEIRA_DOCK_CEG_SERVICE", default: true) else {
            LogStore.shared.log("[ceg-service] app=\(app) off (MADEIRA_DOCK_CEG_SERVICE=0)")
            return "off"
        }
        let registry = prefix.appendingPathComponent("system.reg")
        guard let data = try? Data(contentsOf: registry), !data.isEmpty else {
            LogStore.shared.log("[ceg-service] app=\(app) skipped: the prefix has no system.reg yet")
            return "no-registry"
        }
        let text = String(decoding: data, as: UTF8.self)
        guard text.hasPrefix("WINE REGISTRY Version 2") else {
            LogStore.shared.log("[ceg-service] app=\(app) skipped: system.reg is not a Wine registry file", level: .error)
            return "no-registry"
        }
        let keyThere = registered(in: text)
        let image = placeImage(drive: drive)
        if image == "no-installer" || image == "failed" {
            // Without the program the key would point at nothing, and the host would no longer
            // run Valve's installer: leave everything to the host, as before.
            LogStore.shared.log("[ceg-service] app=\(app) key=\(keyThere ? "present" : "absent") image=\(image): not registered here; the host's own installer step runs as before",
                                level: keyThere ? .info : .error)
            return image == "failed" ? "image-failed" : "no-installer"
        }
        if keyThere {
            LogStore.shared.log("[ceg-service] app=\(app) key=present image=\(image)")
            return "already"
        }
        let (updated, added) = register(in: text, now: Int(now.timeIntervalSince1970))
        guard added else { return "already" }
        do {
            let backup = registry.appendingPathExtension("bcd-bak")
            if !FileManager.default.fileExists(atPath: backup.path) { try? FileManager.default.copyItem(at: registry, to: backup) }
            try Data(updated.utf8).write(to: registry, options: .atomic)
        } catch {
            LogStore.shared.log("[ceg-service] app=\(app) key=absent image=\(image): system.reg not written (\(error.localizedDescription)); the host's own installer step runs as before", level: .error)
            return "write-failed"
        }
        LogStore.shared.log("[ceg-service] app=\(app) registered=added service=\"\(serviceName)\" image=\(image) type=0x10 start=3 wow64=1 " +
                            "path=\(installedImage); services.exe loads it at the session start, so the host finds it registered and runs no installer")
        return "added"
    }

    // MARK: After a failed CEG start

    /// madeira-bcd: a CEG start that ended with a failure (host result 49) leaves Valve's own
    /// account of it in the client's log files. Their lines about the custom-executable job,
    /// the service and DRM go to the log, a few dozen at most, with long numbers cut out; and
    /// the folder's file names and sizes, so the next log says which files exist. 22 -> 55
    /// (ceg-server-result 22, ceg-job-result 55) is what all three logged CEG sessions with a
    /// registered service ended with (the service ran in two), and nothing in the session log says why.
    private nonisolated static let logFiles = ["content_log.txt", "service_log.txt"]
    private nonisolated static let logWords = ["ceg", "drm", "custom", "binar", "service", "remote", "fail", "error", "timeout", "denied"]

    nonisolated static func steamLogLines(drive: URL, maxLines: Int = 40, maxBytes: Int = 96 * 1024) -> [String] {
        guard let folder = DockInstallers.resolve("C:\\Program Files (x86)\\Steam\\logs", drive: drive) else {
            return ["no logs folder in the client's folder"]
        }
        let fm = FileManager.default
        let names = ((try? fm.contentsOfDirectory(atPath: folder.path)) ?? []).sorted()
        var out = ["files=" + names.prefix(24).map { name in
            let size = (try? folder.appendingPathComponent(name).resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? -1
            return "\(name.filter { $0.isLetter || $0.isNumber || $0 == "." || $0 == "_" || $0 == "-" })(\(size))"
        }.joined(separator: ",")]
        for wanted in logFiles {
            guard let name = names.first(where: { $0.caseInsensitiveCompare(wanted) == .orderedSame }),
                  let handle = try? FileHandle(forReadingFrom: folder.appendingPathComponent(name)) else { continue }
            defer { try? handle.close() }
            let size = (try? handle.seekToEnd()) ?? 0
            let start = size > UInt64(maxBytes) ? size - UInt64(maxBytes) : 0
            try? handle.seek(toOffset: start)
            let text = String(decoding: (try? handle.readToEnd()) ?? Data(), as: UTF8.self)
            let lines = text.split(whereSeparator: { $0.isNewline }).map(String.init)
            let hits = lines.filter { line in let lower = line.lowercased(); return logWords.contains { lower.contains($0) } }
            out.append("\(wanted) lines=\(lines.count) matching=\(hits.count)")
            for line in hits.suffix(maxLines) { out.append("\(wanted): " + scrub(line)) }
        }
        return out
    }

    /// One log line, cut to 220 characters, with every run of nine or more digits (Steam IDs,
    /// account numbers) replaced.
    nonisolated static func scrub(_ line: String) -> String {
        var result = "", digits = ""
        func flush() { result += digits.count >= 9 ? "<n>" : digits; digits = "" }
        for character in line.prefix(220) {
            if character.isASCII, character.isNumber { digits.append(character) } else { flush(); result.append(character) }
        }
        flush()
        return result.trimmingCharacters(in: .whitespaces)
    }

    /// Writes `steamLogLines` to the log, once per failed CEG start.
    static func logSteamLogs(drive: URL) {
        guard SteamSignIn.flag("MADEIRA_DOCK_CEG_SERVICE", default: true) else { return }
        for line in steamLogLines(drive: drive) { LogStore.shared.log("[ceg-log] " + line) }
    }
}

// MARK: - A Steam install script's "Registry" values

/// madeira-bcd (round 591): one value of an install script's "Registry" section (installscript.vdf): the registry
/// entries Valve's Windows client writes for a game, the install folder above all. `key` is under the hive and always
/// starts with "Software\".
struct SteamInstallRegistryValue: Equatable, Sendable {
    enum Payload: Equatable, Sendable {
        case string(String)
        case dword(UInt32)
    }
    var hive: SteamInstallRun.Hive
    var key: String
    var name: String
    var payload: Payload
}

/// madeira-bcd (round 591): reading a script's "Registry" section and adding what is missing to a Wine .reg text.
///
/// The Windows client writes these values when it installs or starts a game; a Dock start runs Valve's client in the
/// host and never the script, and DockInstallers runs only the "Run Process" entries. Fallout: New Vegas' launcher
/// reads HKLM\Software\Bethesda Softworks\FalloutNV\Installed Path (the script's value, %INSTALLDIR%) and says "does not
/// appear to be installed and the installer could not be found" without it (GA6QN2). The section's shape, as the
/// scripts of the Bethesda games have it: the registry key in full, then the value type ("string", "dword"), then the
/// values; a value written straight under the key is a string. A value may be a section of language names ("english"
/// ...) instead of a text: the installed language's text is taken, else English's. `%INSTALLDIR%` and `%LANGUAGE%`
/// are the two tokens understood; a value with any other `%` is left out, and so is a type this does not know.
/// Only keys below Software\ are taken, and only HKLM and HKCU.
enum SteamInstallRegistry {
    static let maxValues = 64

    struct Parsed: Equatable, Sendable {
        var values: [SteamInstallRegistryValue] = []
        /// One short word for each entry left out: path, name, type:<word>, token, dword, language, limit.
        var skipped: [String] = []
        /// The first few key names the "Registry" section holds, as written (for the log when nothing came of them).
        var keys: [String] = []
        /// Round 623: up to six of the entries counted as "language" in `skipped`, as `key | type | name | the names under it` (printable ASCII,
        /// bounded), so the log shows the shape of what was left out. Far Cry 3's script left 34 entries out this way and its updater still
        /// found "essential information" missing (KNYQ40); how those entries are laid out is not known.
        var languageShapes: [String] = []
    }

    /// The registry values of one script, in its order, the first of a repeated value only.
    static func parse(script: Data, installDir: String, language: String) -> Parsed {
        var bytes = Array(script.prefix(SteamInstallScripts.maxScriptBytes))
        if bytes.starts(with: [0xef, 0xbb, 0xbf]) { bytes.removeFirst(3) }
        var position = 0
        // The same tokens SteamInstallScripts.entries reads (Valve's text format), kept separate so that
        // reader is not touched.
        func token() -> (text: String, quoted: Bool)? {
            while position < bytes.count {
                if bytes[position] <= 32 { position += 1; continue }
                if bytes[position] == 47, position + 1 < bytes.count, bytes[position + 1] == 47 {
                    while position < bytes.count, bytes[position] != 10 { position += 1 }
                    continue
                }
                break
            }
            guard position < bytes.count else { return nil }
            let first = bytes[position]; position += 1
            if first == 123 { return ("{", false) }
            if first == 125 { return ("}", false) }
            var value: [UInt8] = []
            if first == 34 {
                while position < bytes.count {
                    let byte = bytes[position]; position += 1
                    if byte == 34 { break }
                    if byte == 92, position < bytes.count, bytes[position] == 34 || bytes[position] == 92 { value.append(bytes[position]); position += 1 }
                    else { value.append(byte) }
                }
                return (String(decoding: value, as: UTF8.self), true)
            }
            value.append(first)
            while position < bytes.count, bytes[position] > 32, bytes[position] != 123, bytes[position] != 125 { value.append(bytes[position]); position += 1 }
            return (String(decoding: value, as: UTF8.self), true)
        }
        struct Raw { var key: String; var type: String; var name: String; var text: String?; var languages: [String: String] }
        var raws: [Raw] = []
        var keys: [String] = []
        var languageIndex: [String: Int] = [:]
        var path: [String] = []
        var pending: String?
        var tokens = 0
        while let (text, quoted) = token() {
            tokens += 1
            if tokens > 200_000 || path.count > 32 { break }
            if !quoted && text == "{" {
                path.append(pending ?? ""); pending = nil
            } else if !quoted && text == "}" {
                if !path.isEmpty { path.removeLast() }
                pending = nil
            } else if let name = pending {
                pending = nil
                guard let section = path.firstIndex(where: { $0.caseInsensitiveCompare("registry") == .orderedSame }) else { continue }
                let below = Array(path[(section + 1)...])
                if let first = below.first, keys.count < 3, !keys.contains(first) { keys.append(first) }
                switch below.count {
                case 1:   // a value straight under the key: a string
                    raws.append(Raw(key: below[0], type: "string", name: name, text: text, languages: [:]))
                case 2:   // key, type
                    raws.append(Raw(key: below[0], type: below[1].lowercased(), name: name, text: text, languages: [:]))
                case 3:   // key, type, value name: the text of one language
                    let id = below.joined(separator: "\u{1}").lowercased()
                    let index = languageIndex[id] ?? raws.count
                    if languageIndex[id] == nil {
                        languageIndex[id] = index
                        raws.append(Raw(key: below[0], type: below[1].lowercased(), name: below[2], text: nil, languages: [:]))
                    }
                    raws[index].languages[name.lowercased()] = text
                default:
                    break
                }
            } else {
                pending = text
            }
        }
        var result = Parsed()
        result.keys = keys
        var seen = Set<String>()
        let chosen = language.lowercased()
        for raw in raws {
            guard let text = raw.text ?? raw.languages[chosen] ?? raw.languages["english"] else {
                result.skipped.append("language")
                if result.languageShapes.count < 6 {
                    let shape = [raw.key, raw.type, raw.name, raw.languages.keys.sorted().prefix(8).joined(separator: ",")].joined(separator: " | ")
                    result.languageShapes.append(String(String.UnicodeScalarView(shape.unicodeScalars.filter { $0.value >= 32 && $0.value < 127 && $0 != "\"" }).prefix(150)))
                }
                continue
            }
            guard let (hive, key) = SteamInstallScripts.hive(raw.key), validKey(key) else { result.skipped.append("path"); continue }
            guard validText(raw.name, limit: 255) else { result.skipped.append("name"); continue }
            let payload: SteamInstallRegistryValue.Payload
            switch raw.type {
            case "string":
                guard let expanded = expand(text, installDir: installDir, language: language) else { result.skipped.append("token"); continue }
                guard validText(expanded, limit: 1024) else { result.skipped.append("name"); continue }
                payload = .string(expanded)
            case "dword":
                guard let number = dword(text) else { result.skipped.append("dword"); continue }
                payload = .dword(number)
            default:
                result.skipped.append("type:" + String(raw.type.filter { $0.isLetter || $0.isNumber || $0 == "_" }.prefix(12))); continue
            }
            guard seen.insert(hive.rawValue + "|" + key.lowercased() + "|" + raw.name.lowercased()).inserted else { continue }
            guard result.values.count < maxValues else { result.skipped.append("limit"); continue }
            result.values.append(SteamInstallRegistryValue(hive: hive, key: key, name: raw.name, payload: payload))
        }
        return result
    }

    /// `%INSTALLDIR%` and `%LANGUAGE%` replaced; nil when another `%` is left (a token this does not know).
    static func expand(_ text: String, installDir: String, language: String) -> String? {
        let tokens = ["%INSTALLDIR%", "%LANGUAGE%"]
        var rest = text
        for token in tokens { rest = rest.replacingOccurrences(of: token, with: "", options: .caseInsensitive) }
        if rest.contains("%") { return nil }
        return text.replacingOccurrences(of: "%INSTALLDIR%", with: installDir, options: .caseInsensitive)
            .replacingOccurrences(of: "%LANGUAGE%", with: language, options: .caseInsensitive)
    }

    private static func dword(_ text: String) -> UInt32? {
        let value = text.trimmingCharacters(in: .whitespaces).lowercased()
        return value.hasPrefix("0x") ? UInt32(value.dropFirst(2), radix: 16) : UInt32(value)
    }

    /// Printable ASCII only, no brackets or quotes (they are escaped in a key name's header), below Software\.
    private static func validKey(_ key: String) -> Bool {
        key.lowercased().hasPrefix("software\\") && key.utf8.count <= 400
            && key.unicodeScalars.allSatisfy { $0.value >= 32 && $0.value < 127 && $0 != "[" && $0 != "]" && $0 != "\"" }
    }

    /// No control characters, not empty, at most `limit` bytes.
    private static func validText(_ text: String, limit: Int) -> Bool {
        !text.isEmpty && text.utf8.count <= limit && !text.unicodeScalars.contains { $0.value < 32 || $0.value == 127 }
    }

    /// The keys a value is added to: a 32-bit reader sees HKLM\Software through Wow6432Node in a 64-bit prefix and a
    /// 64-bit reader the plain key, the rule SteamInstallScripts.keys applies to a run's record.
    static func views(_ value: SteamInstallRegistryValue) -> [String] {
        let lower = value.key.lowercased()
        guard value.hive == .machine, lower.hasPrefix("software\\"), !lower.hasPrefix("software\\wow6432node\\") else { return [value.key] }
        return [value.key, "Software\\Wow6432Node\\" + String(value.key.dropFirst("software\\".count))]
    }

    /// A name or text as Wine's registry file spells it: backslash and quote escaped, anything outside printable
    /// ASCII as \x and four hex digits per UTF-16 unit.
    static func quoted(_ text: String) -> String {
        var out = ""
        for unit in text.utf16 {
            switch unit {
            case 0x5c: out += "\\\\"
            case 0x22: out += "\\\""
            case 0x20...0x7e: out.unicodeScalars.append(Unicode.Scalar(UInt8(unit)))
            default: out += String(format: "\\x%04x", UInt32(unit))
            }
        }
        return out
    }

    /// The value's line in a .reg file.
    static func line(_ value: SteamInstallRegistryValue) -> String {
        let name = "\"" + quoted(value.name) + "\"="
        switch value.payload {
        case .string(let text): return name + "\"" + quoted(text) + "\""
        case .dword(let number): return name + String(format: "dword:%08x", number)
        }
    }

    /// Round 623: what the .reg text already holds of each value, read before `addMissing` changes it. One entry for each value that is in at
    /// least one of its views: how many views there are and hold it, whether every one holds exactly the text the script wants, and the first
    /// differing stored text (as the .reg file spells it, bounded). Far Cry 3's updater said "Cannot find essential information in the registry"
    /// with every value of the script present (KNYQ40: values=8 written=0 present=12), and a stored text is never overwritten, so a stale or
    /// different one has to be visible in the log.
    static func stored(_ values: [SteamInstallRegistryValue], in text: String)
        -> [(value: SteamInstallRegistryValue, views: Int, found: Int, same: Bool, stored: String, wanted: String)] {
        let lines = text.components(separatedBy: "\n")
        let lower = lines.map { $0.lowercased() }   // once: a Wine registry file has tens of thousands of lines
        var out: [(value: SteamInstallRegistryValue, views: Int, found: Int, same: Bool, stored: String, wanted: String)] = []
        for value in values {
            let prefix = ("\"" + quoted(value.name) + "\"=")
            let wanted = String(line(value).dropFirst(prefix.count))
            let keys = views(value)
            var found = 0, same = true, shown = ""
            for key in keys {
                let header = ("[" + SteamInstallScripts.escape(key) + "]").lowercased()
                guard let start = lower.firstIndex(where: { $0.hasPrefix(header) }) else { continue }
                var end = start + 1
                while end < lines.count, !lines[end].hasPrefix("[") { end += 1 }
                guard let at = (start + 1..<end).first(where: { lower[$0].hasPrefix(prefix.lowercased()) }) else { continue }
                found += 1
                let rest = String(lines[at].dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
                if rest != wanted { same = false }
                if shown.isEmpty || (rest != wanted && shown == wanted) { shown = rest }
            }
            if found > 0 { out.append((value, keys.count, found, same, String(shown.prefix(100)), String(wanted.prefix(100)))) }
        }
        return out
    }

    /// The .reg text with each value added where the key lacks one of that name (any case): into the key's section, or
    /// in a new section at the end when the key is not there. A value that is there, whatever it holds, stays.
    /// Returns the keys and values written and how many were there already.
    static func addMissing(_ values: [SteamInstallRegistryValue], in text: String, now: Int)
        -> (text: String, written: [(key: String, value: SteamInstallRegistryValue)], present: Int) {
        var lines = text.components(separatedBy: "\n")
        var written: [(key: String, value: SteamInstallRegistryValue)] = []
        var present = 0
        for value in values {
            let valueLine = line(value)
            let valueName = ("\"" + quoted(value.name) + "\"=").lowercased()
            for key in views(value) {
                let header = "[" + SteamInstallScripts.escape(key) + "]"
                if let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(header.lowercased()) }) {
                    var end = start + 1
                    while end < lines.count, !lines[end].hasPrefix("[") { end += 1 }
                    if (start + 1..<end).contains(where: { lines[$0].lowercased().hasPrefix(valueName) }) { present += 1; continue }
                    var at = end
                    while at > start + 1, lines[at - 1].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { at -= 1 }
                    lines.insert(valueLine, at: at)
                } else {
                    while let last = lines.last, last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { lines.removeLast() }
                    lines += ["", header + " \(now)", valueLine, ""]
                }
                written.append((key, value))
            }
        }
        return (lines.joined(separator: "\n"), written, present)
    }
}

/// madeira-bcd (round 591): `env.MADEIRA_DOCK_SCRIPT_REGISTRY = 1` (off unless set) writes the "Registry" values of a
/// game's Steam install script into the prefix before a Dock start, the way the Windows client does, where the key or
/// value is not there yet. Called from DockInstallers.prepare, while no session runs: the wineserver keeps the registry in
/// memory and writes it back when it stops, so the files are only rewritten now, like the run records. The switch is read
/// from the game's own lines first (the file MADEIRA_CFG_GAME names, the one the session exports to Wine), then bcd.cfg
/// and the environment. Logs one line for each value written and a summary line; nothing at all when off.
@MainActor
enum DockScriptRegistry {
    nonisolated static let switchName = "MADEIRA_DOCK_SCRIPT_REGISTRY"

    /// The game's own `env.MADEIRA_DOCK_SCRIPT_REGISTRY` line (the last one wins; "0" is off), else bcd.cfg / the
    /// environment (`SteamSignIn.flag`); off when none.
    nonisolated static func enabled(gameConfigPath: String? = ProcessInfo.processInfo.environment["MADEIRA_CFG_GAME"]) -> Bool {
        if let path = gameConfigPath, !path.isEmpty, let text = try? String(contentsOfFile: path, encoding: .utf8) {
            var value: String?
            for raw in text.components(separatedBy: .newlines) {
                let line = raw.trimmingCharacters(in: .whitespaces)
                guard !line.hasPrefix("#"), let equals = line.firstIndex(of: "=") else { continue }
                if line[..<equals].trimmingCharacters(in: .whitespaces) == "env." + switchName {
                    value = line[line.index(after: equals)...].trimmingCharacters(in: .whitespaces)
                }
            }
            if let value, !value.isEmpty { return value != "0" }
        }
        // A flag helper's own catalog entry: off unless a line or the environment turns it on.
        return SteamSignIn.flag("MADEIRA_DOCK_SCRIPT_REGISTRY", default: false)
    }

    /// The language the game's install record names (`"language"` under UserConfig, a lowercase word), else English:
    /// BCD's own installer writes "english".
    nonisolated static func installLanguage(manifest: String?) -> String {
        guard let manifest, let expression = try? NSRegularExpression(pattern: #""language"\s+"([a-z_]{2,24})""#, options: [.caseInsensitive]),
              let match = expression.firstMatch(in: manifest, range: NSRange(manifest.startIndex..., in: manifest)),
              let range = Range(match.range(at: 1), in: manifest) else { return "english" }
        return manifest[range].lowercased()
    }

    /// Returns the outcome word: off, none (no script with usable values), no-registry, written or present.
    @discardableResult
    static func prepare(_ game: DockGame, drive: URL, prefix: URL, now: Date = Date(),
                        gameConfigPath: String? = ProcessInfo.processInfo.environment["MADEIRA_CFG_GAME"]) -> String {
        guard enabled(gameConfigPath: gameConfigPath) else { return "off" }
        let app = game.id
        let folder = drive.appendingPathComponent(game.library + "/common/" + game.installDir, isDirectory: true)
        guard DockInstallers.inside(folder, drive: drive) else {
            LogStore.shared.log("[dock-script-registry] app=\(app) skipped: the game's folder is not inside the prefix", level: .error)
            return "none"
        }
        let manifest = try? String(contentsOf: drive.appendingPathComponent(game.library + "/appmanifest_\(app).acf"), encoding: .utf8)
        let language = installLanguage(manifest: manifest)
        var values: [SteamInstallRegistryValue] = []
        var skipped: [String] = []
        var keys: [String] = []
        var languageShapes: [String] = []
        var scripts = 0
        for file in SteamInstallScripts.scripts(folder: folder, depth: 1, containing: "registry") {
            guard let contents = try? Data(contentsOf: file) else { continue }
            let parsed = SteamInstallRegistry.parse(script: contents, installDir: game.windowsInstallPath, language: language)
            guard !parsed.values.isEmpty || !parsed.skipped.isEmpty else { continue }
            scripts += 1
            skipped += parsed.skipped
            for shape in parsed.languageShapes where languageShapes.count < 6 && !languageShapes.contains(shape) { languageShapes.append(shape) }
            for key in parsed.keys where keys.count < 3 && !keys.contains(key) { keys.append(key) }
            for value in parsed.values where !values.contains(where: {
                $0.hive == value.hive && $0.key.lowercased() == value.key.lowercased() && $0.name.lowercased() == value.name.lowercased()
            }) && values.count < SteamInstallRegistry.maxValues { values.append(value) }
        }
        let skippedText = skipped.isEmpty ? "-" : Dictionary(grouping: skipped, by: { $0 }).sorted { $0.key < $1.key }
            .map { "\($0.key)(\($0.value.count))" }.joined(separator: ",")
        // The key names as the script has them, when nothing came of them: the next log shows what shape to read.
        let keysText = keys.isEmpty ? "" : " keys=" + keys.map { String($0.filter { $0.isASCII && !$0.isNewline && $0 != "\"" }.prefix(60)) }.joined(separator: ";")
        for shape in languageShapes { LogStore.shared.log("[dock-script-registry] app=\(app) left out for language: \(shape)") }
        guard !values.isEmpty else {
            LogStore.shared.log("[dock-script-registry] app=\(app) scripts=\(scripts) values=0 skipped=\(skippedText)\(keysText)")
            return "none"
        }
        var written = 0, present = 0, missingFile = false
        var presentNotes: [String] = []
        for (hive, file) in [(SteamInstallRun.Hive.machine, "system.reg"), (.user, "user.reg")] {
            let selected = values.filter { $0.hive == hive }
            guard !selected.isEmpty else { continue }
            let registry = prefix.appendingPathComponent(file)
            guard let data = try? Data(contentsOf: registry), !data.isEmpty else { missingFile = true; continue }   // a prefix not seeded yet
            let text = String(decoding: data, as: UTF8.self)
            guard text.hasPrefix("WINE REGISTRY Version 2") else {
                LogStore.shared.log("[dock-script-registry] app=\(app) \(file) is not a Wine registry file: left as it is", level: .error)
                continue
            }
            // Round 623: what is there already, before anything is added (log lines only).
            for note in SteamInstallRegistry.stored(selected, in: text) {
                let shown = (hive == .machine ? "HKLM" : "HKCU") + "\\" + note.value.key
                presentNotes.append("[dock-script-registry] app=\(app) present \(shown) \"\(note.value.name)\": \(note.found) of \(note.views) view(s), " +
                                    (note.same ? "same as the script" : "differs: stored \(note.stored), the script has \(note.wanted)"))
            }
            let result = SteamInstallRegistry.addMissing(selected, in: text, now: Int(now.timeIntervalSince1970))
            present += result.present
            guard !result.written.isEmpty else { continue }
            do {
                let backup = registry.appendingPathExtension("bcd-bak")
                if !FileManager.default.fileExists(atPath: backup.path) { try? FileManager.default.copyItem(at: registry, to: backup) }
                try Data(result.text.utf8).write(to: registry, options: .atomic)
            } catch {
                LogStore.shared.log("[dock-script-registry] app=\(app) \(file) not written: \(error.localizedDescription)", level: .error)
                continue
            }
            for (key, value) in result.written {
                let shown: String
                switch value.payload {
                case .string(let text): shown = "\"" + String(text.prefix(160)) + "\""
                case .dword(let number): shown = "dword " + String(number)
                }
                LogStore.shared.log("[dock-script-registry] app=\(app) wrote \(hive == .machine ? "HKLM" : "HKCU")\\\(key) \"\(value.name)\" = \(shown)")
                written += 1
            }
        }
        for line in presentNotes.prefix(12) { LogStore.shared.log(line) }
        if presentNotes.count > 12 { LogStore.shared.log("[dock-script-registry] app=\(app) present: \(presentNotes.count - 12) more value(s) not listed") }
        LogStore.shared.log("[dock-script-registry] app=\(app) scripts=\(scripts) values=\(values.count) written=\(written) present=\(present) " +
                            "skipped=\(skippedText) language=\(language)\(missingFile ? " (the prefix has no registry file yet)" : "")")
        return missingFile && written == 0 ? "no-registry" : written > 0 ? "written" : "present"
    }
}
