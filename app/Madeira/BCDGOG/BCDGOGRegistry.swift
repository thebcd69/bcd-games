// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// A GOG build's install script in the Wine prefix (docs/GOG.md, "Install script"). GOG Galaxy runs a
// build's `goggame-<id>.script` after an install; its `setRegistry` actions are values a game or its
// launcher reads to find itself (The Witcher: Enhanced Edition's launcher quits after half a second,
// without a message, when HKLM\Software\CD Projekt Red\The Witcher has no InstallFolder). The script
// itself is read by GOGInstallScript.swift (upstream Madeira's file, kept as it came); this file puts
// its values into the prefix's registry files. Foundation only (the host checks compile it).
//
// The registry files are plain text that the wineserver keeps in memory and writes back when it stops,
// so they are only rewritten while no session runs (ContentView.startLibraryEntry checks that before
// BCDGOG.prepareStart): a first-time backup (`.bcd-bak`) is kept and the file is replaced atomically.
// The encoding rules are those of BCD's Steam install-script writer (SteamInstallRegistry, in
// DockInstallers.swift, which this does not touch): HKLM\Software values go into both registry views,
// non-ASCII text is written as \x and four hex digits per UTF-16 unit. A value the prefix already has
// stays as it is (a game or its launcher may have changed it), except a string made from the game's
// folder, which follows the folder (a game installed again under another name).
enum BCDGOGRegistry {
    typealias Value = GOGInstallScript.Value

    /// What the scripts in a game's folder write, and what was left out.
    struct Prepared: Equatable {
        var values: [Value] = []
        /// How many scripts were read, and how many could not be.
        var scripts = 0
        var unreadable = 0
        /// One short word for each action left out (the action's kind, bounded), for the log.
        var leftOut: [String] = []
    }

    struct Outcome: Equatable {
        /// Values added or rewritten, summed over both views of HKLM\Software.
        var written = 0
        /// Of those, strings rewritten because they follow the game's folder.
        var followed = 0
        /// Values already there that stay as they are.
        var present = 0
        /// A registry file was not there yet (a prefix not seeded), or not Wine's.
        var missingFile = false
        var notWine = false
    }

    /// Whether a start writes the install script's values: the game's switch (env.BCD_GOG_SCRIPT) when it has one, else
    /// whether BCD installed or repaired the game with the script in mind (BCDGOGInstallRecord.scripts). A game installed
    /// before has no mark, so it starts exactly as it always did.
    static func applies(scripts: Bool?, switch choice: Bool?) -> Bool { choice ?? (scripts == true) }

    // MARK: Reading the game's scripts

    /// The values the install scripts at the root of `folder` write for a game installed in `language`
    /// ("en-US"), its folder spelled as Windows sees it (`windowsFolder`, "C:\GOG Games\...").
    static func prepared(folder: URL, windowsFolder: String, language: String) -> Prepared {
        var result = Prepared()
        for file in GOGInstallScript.scripts(in: folder) {
            guard let data = try? Data(contentsOf: file), let script = GOGInstallScript.parse(data) else {
                result.unreadable += 1
                continue
            }
            result.scripts += 1
            let plan = script.plan(folder: windowsFolder, language: language)
            for value in plan.values {
                result.values.removeAll {
                    $0.root == value.root && $0.key.lowercased() == value.key.lowercased() && $0.name.lowercased() == value.name.lowercased()
                }
                result.values.append(value)
            }
            for action in script.actions {
                let kind = action.kind.lowercased()
                // setRegistry is written, a known Execute is replaced by its values, a supportData copy of the
                // support folder's app files is done by the download: the rest is left out.
                if kind == "setregistry" || (kind == "execute" && GOGInstallScript.knownExecuteValues[script.productID] != nil)
                    || (kind == "supportdata" && GOGInstallScript.isSupportAppCopy(action.arguments)) { continue }
                let word = String(String.UnicodeScalarView(kind.unicodeScalars.filter { $0.isASCII && ($0.properties.isAlphabetic || $0.properties.numericType != nil) }.prefix(24)))
                if !result.leftOut.contains(word) && result.leftOut.count < 8 { result.leftOut.append(word.isEmpty ? "unnamed" : word) }
            }
        }
        return result
    }

    // MARK: The registry text

    /// A name or text as Wine's registry file spells it: backslash and quote escaped, anything outside
    /// printable ASCII as \x and four hex digits per UTF-16 unit.
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

    /// The value's name as the file spells it: `@` for the key's default value.
    private static func namePart(_ value: Value) -> String {
        value.name.isEmpty ? "@=" : "\"" + quoted(value.name) + "\"="
    }

    /// The value's line in a .reg file.
    static func line(_ value: Value) -> String {
        switch value.data {
        case .string(let text): return namePart(value) + "\"" + quoted(text) + "\""
        case .dword(let number): return namePart(value) + String(format: "dword:%08x", number)
        }
    }

    /// The keys a value is added to: a 32-bit reader sees HKLM\Software through Wow6432Node in a 64-bit
    /// prefix and a 64-bit reader the plain key.
    static func views(_ value: Value) -> [String] {
        let lower = value.key.lowercased()
        guard value.root == .machine, lower.hasPrefix("software\\"), !lower.hasPrefix("software\\wow6432node\\") else { return [value.key] }
        return [value.key, "Software\\Wow6432Node\\" + String(value.key.dropFirst("software\\".count))]
    }

    /// A key name as a section header spells it (backslash and quote escaped).
    private static func header(_ key: String) -> String {
        "[" + key.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "]"
    }

    /// The .reg text with the values written: a missing value goes into its key's section (a missing key gets
    /// a section at the end), a value that is there stays, except a string that follows the game's folder and
    /// differs, which is rewritten.
    static func write(_ values: [Value], in text: String, now: Int) -> (text: String, outcome: Outcome) {
        var lines = text.components(separatedBy: "\n")
        var outcome = Outcome()
        for value in values {
            let wanted = line(value)
            let namePrefix = namePart(value).lowercased()
            for key in views(value) {
                let head = header(key)
                if let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(head.lowercased()) }) {
                    var end = start + 1
                    while end < lines.count, !lines[end].hasPrefix("[") { end += 1 }
                    if let at = (start + 1..<end).first(where: { lines[$0].lowercased().hasPrefix(namePrefix) }) {
                        guard value.followsInstall, lines[at].trimmingCharacters(in: .whitespacesAndNewlines) != wanted else {
                            outcome.present += 1
                            continue
                        }
                        lines[at] = wanted
                        outcome.followed += 1
                    } else {
                        var at = end
                        while at > start + 1, lines[at - 1].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { at -= 1 }
                        lines.insert(wanted, at: at)
                    }
                } else {
                    while let last = lines.last, last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { lines.removeLast() }
                    lines += ["", head + " \(now)", wanted, ""]
                }
                outcome.written += 1
            }
        }
        return (lines.joined(separator: "\n"), outcome)
    }

    // MARK: The prefix

    /// Writes the values into the prefix's system.reg (HKLM) and user.reg (HKCU). Only while no session runs.
    @discardableResult
    static func apply(_ values: [Value], prefix: URL, now: Date = Date()) throws -> Outcome {
        var total = Outcome()
        for (root, file) in [(Value.Root.machine, "system.reg"), (.user, "user.reg")] {
            let selected = values.filter { $0.root == root }
            guard !selected.isEmpty else { continue }
            let registry = prefix.appendingPathComponent(file)
            guard let data = try? Data(contentsOf: registry), !data.isEmpty else { total.missingFile = true; continue }   // a prefix not seeded yet
            let text = String(decoding: data, as: UTF8.self)
            guard text.hasPrefix("WINE REGISTRY Version 2") else { total.notWine = true; continue }
            let result = write(selected, in: text, now: Int(now.timeIntervalSince1970))
            total.present += result.outcome.present
            guard result.outcome.written > 0 else { continue }
            let backup = registry.appendingPathExtension("bcd-bak")
            if !FileManager.default.fileExists(atPath: backup.path) { try? FileManager.default.copyItem(at: registry, to: backup) }
            try Data(result.text.utf8).write(to: registry, options: .atomic)
            total.written += result.outcome.written
            total.followed += result.outcome.followed
        }
        return total
    }

    /// The log line for one game's start: counts and words, never a key, a value or a path.
    static func logLine(product: String, prepared: Prepared, outcome: Outcome?) -> String {
        var text = "[gog-script] product=\(product) scripts=\(prepared.scripts) unreadable=\(prepared.unreadable) values=\(prepared.values.count)"
        if let outcome {
            text += " written=\(outcome.written) followed=\(outcome.followed) present=\(outcome.present)"
            if outcome.missingFile { text += " no-registry-file=1" }
            if outcome.notWine { text += " not-wine-registry=1" }
        }
        if !prepared.leftOut.isEmpty { text += " left-out=" + prepared.leftOut.joined(separator: ",") }
        return text
    }
}
