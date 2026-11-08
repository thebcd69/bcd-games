// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 125hz
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Modified by BCD (thebcd69), 2026-11-05: taken from the registry-value writer of Madeira's DockInstallers.swift (SteamInstallValue,
// SteamInstallRegistry.line/text/write, SteamInstallScripts.keys and .rewrite) under names of its own (EARegistryValue, EARegistry): BCD's
// DockInstallers.swift has a Steam install-script registry writer of its own under the old names, and the EA code needs only this part.
// Foundation only, so tests/host/check-ea.py compiles it on the Mac.
// Portions copyright 2026 ft7t8drgwf-wq: code taken from https://github.com/willfaust/Madeira (commit 89ff82a)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Foundation

// The registry values an EA game expects (docs/EA.md, "Playing"): the EA app's install marker and, when the build's manifest named one, the
// game's own install folder. Written into the prefix's system.reg, only while no session runs: the wineserver keeps the registry in memory
// and writes it back when it stops.

/// One registry value to put into a prefix.
struct EARegistryValue: Equatable, Sendable {
    enum Hive: String, Sendable { case machine, user }
    enum Content: Equatable, Sendable { case string(String), dword(UInt32) }
    var hive: Hive
    var key: String      // under the hive
    var name: String     // "" is the key's default value
    var data: Content
    var followsInstall: Bool   // a string made from the install folder: rewritten when it differs
}

/// Writing values into the prefix's .reg files. A value the prefix already has stays as it is (a game or its launcher may have changed it),
/// except a string that follows the install folder (`followsInstall`), which is rewritten.
enum EARegistry {
    /// A Wine .reg line for the value.
    static func line(_ value: EARegistryValue) -> String {
        let name = value.name.isEmpty ? "@" : "\"" + Self.text(value.name, quote: "\"") + "\""
        switch value.data {
        case .string(let string): return name + "=\"" + Self.text(string, quote: "\"") + "\""
        case .dword(let number): return name + String(format: "=dword:%08x", number)
        }
    }

    /// Text the way wineserver writes it (server/unicode.c, dump_strW): non-ASCII as \x
    /// escapes of UTF-16 units, control characters as C or octal escapes, and a backslash
    /// before \ and the quote characters.
    static func text(_ string: String, quote: String) -> String {
        let units = Array(string.utf16)
        let escapes = Array(".......abtnvfr.............e....".utf8)
        var out = ""
        for (index, unit) in units.enumerated() {
            let next = index + 1 < units.count ? units[index + 1] : nil
            if unit > 127 {
                let hexNext = next.map { $0 < 128 && Character(Unicode.Scalar(UInt8($0))).isHexDigit } ?? false
                out += hexNext ? String(format: "\\x%04x", UInt32(unit)) : String(format: "\\x%x", UInt32(unit))
            } else if unit < 32 {
                if escapes[Int(unit)] != UInt8(ascii: ".") { out += "\\" + String(Character(Unicode.Scalar(escapes[Int(unit)]))) }
                else if let next, next >= 48, next <= 55 { out += String(format: "\\%03o", UInt32(unit)) }
                else { out += String(format: "\\%o", UInt32(unit)) }
            } else {
                let character = Character(Unicode.Scalar(UInt8(unit)))
                if character == "\\" || quote.contains(character) { out += "\\" }
                out.append(character)
            }
        }
        return out
    }

    /// The keys a value is recorded under. A 32-bit reader sees HKLM\Software through
    /// Wow6432Node in a 64-bit prefix; the plain key covers a 64-bit reader.
    static func keys(_ hive: EARegistryValue.Hive, _ key: String) -> [String] {
        let lower = key.lowercased()
        guard hive == .machine, lower.hasPrefix("software\\"), !lower.hasPrefix("software\\wow6432node\\") else { return [key] }
        return [key, "Software\\Wow6432Node\\" + key.dropFirst("software\\".count)]
    }

    /// The .reg text with the values written (both registry views of HKLM\Software), and
    /// how many lines changed. Missing values are added; a present one is replaced only
    /// when it follows the install folder and differs.
    static func write(_ values: [EARegistryValue], in text: String, now: Int) -> (text: String, changed: Int) {
        var lines = text.components(separatedBy: "\n")
        var changed = 0
        for value in values {
            let wanted = Self.line(value)
            let namePrefix = value.name.isEmpty ? "@=" : "\"" + Self.text(value.name, quote: "\"").lowercased() + "\"="
            for key in Self.keys(value.hive, value.key) {
                let header = "[" + Self.text(key, quote: "[]") + "]"
                if let start = lines.firstIndex(where: { $0.lowercased().hasPrefix(header.lowercased()) }) {
                    var end = start + 1
                    while end < lines.count, !lines[end].hasPrefix("[") { end += 1 }
                    if let index = (start + 1..<end).first(where: { lines[$0].lowercased().hasPrefix(namePrefix) }) {
                        guard value.followsInstall, lines[index] != wanted else { continue }
                        lines[index] = wanted
                    } else {
                        var at = end
                        while at > start + 1, lines[at - 1].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { at -= 1 }
                        lines.insert(wanted, at: at)
                    }
                } else {
                    while let last = lines.last, last.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { lines.removeLast() }
                    lines += ["", header + " \(now)", wanted, ""]
                }
                changed += 1
            }
        }
        return (lines.joined(separator: "\n"), changed)
    }

    /// Writes the values into the prefix's system.reg and user.reg, keeping a one-time .bcd-bak copy of each file it changes (the name
    /// BCD's own registry writes use). Returns the number of lines changed. Only while no session runs.
    @discardableResult
    static func write(_ values: [EARegistryValue], prefix: URL) throws -> Int {
        let now = Int(Date().timeIntervalSince1970)
        var total = 0
        for (hive, file) in [(EARegistryValue.Hive.machine, "system.reg"), (.user, "user.reg")] {
            let selected = values.filter { $0.hive == hive }
            guard !selected.isEmpty else { continue }
            let registry = prefix.appendingPathComponent(file)
            guard let contents = try? Data(contentsOf: registry), !contents.isEmpty else { continue }   // a prefix not seeded yet
            let (updated, changed) = write(selected, in: String(decoding: contents, as: UTF8.self), now: now)
            guard changed > 0 else { continue }
            let backup = registry.appendingPathExtension("bcd-bak")
            if !FileManager.default.fileExists(atPath: backup.path) { try? FileManager.default.copyItem(at: registry, to: backup) }
            try Data(updated.utf8).write(to: registry, options: .atomic)
            total += changed
        }
        return total
    }
}
