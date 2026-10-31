// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// A Windows program's request to open a web page (round 625, the owner's item 23). Wine's registry opens a page with
// "C:\windows\system32\winebrowser.exe" "<url>", Unreal Engine's LaunchURL and ShellExecute follow it, and BCD had no winebrowser.exe
// that could open anything on iOS: Satisfactory's Epic sign-in stopped at "CreateProc failed: File not found" (SAZFJQ), BeamNG's crash
// page and the Steam client's links went nowhere. BCD's own winebrowser.exe (bcd-openurl/openurl.c) leaves an http or https address in
// drive_c/ProgramData/BCD/OpenURL/<ticks>-<pid>.url; while a session runs the app takes each one and asks the player, who opens it
// inside BCD (the game keeps running), in Safari, copies it or leaves it. Nothing opens without a tap. Plain values only; the live side
// is in Library.swift (watchOpenURL); tests/host/check-open-url.py compiles this file alone.
enum BCDOpenURL {
    /// The folder under drive_c the program writes into.
    static let folder = "ProgramData/BCD/OpenURL"
    /// The longest address taken, as the program's limit (OU_MAX_URL).
    static let maxLength = 2048
    /// A request file is read only this far.
    static let maxFileBytes = 4096
    /// At most this many questions a minute; a request past it, or while a question is up, is left (and logged).
    static let perMinute = 3

    /// The web page in a request: http or https, a host, no user name or password, at most 2048 printable ASCII characters (the
    /// program's own test, made again here). nil for anything else.
    static func page(_ text: String) -> URL? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, t.utf8.count <= maxLength, t.utf8.allSatisfy({ $0 >= 0x21 && $0 <= 0x7e }),
              let url = URL(string: t), let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = url.host, !host.isEmpty, !host.hasPrefix("."), url.user == nil, url.password == nil else { return nil }
        return url
    }

    /// The finished requests in `folder`, oldest first (a name starts with the tick count in 20 digits); a ".part" file is still
    /// being written and is left alone.
    static func requests(in folder: URL, fm: FileManager = .default) -> [URL] {
        guard let names = try? fm.contentsOfDirectory(atPath: folder.path) else { return [] }
        return names.filter { $0.hasSuffix(".url") }.sorted().map { folder.appendingPathComponent($0) }
    }

    /// A request's text, read at most `maxFileBytes` far; nil when it cannot be read or is not UTF-8.
    static func read(_ file: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        guard let data = try? handle.read(upToCount: maxFileBytes) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// One question at a time, at most `perMinute` a minute.
    struct Gate: Equatable {
        var asked: [Date] = []
        var showing = false

        mutating func admit(now: Date) -> Bool {
            asked = asked.filter { now.timeIntervalSince($0) < 60 }
            guard !showing, asked.count < BCDOpenURL.perMinute else { return false }
            asked.append(now)
            showing = true
            return true
        }

        mutating func answered() { showing = false }
    }

    /// The address as the question shows it: at most 160 characters.
    static func shown(_ url: URL) -> String {
        let s = url.absoluteString
        return s.count <= 160 ? s : String(s.prefix(157)) + "..."
    }

    static let title = "Open a web page?"

    /// The question's words.
    static func message(game: String, url: URL) -> String {
        "\(game) wants to open a web page:\n\n\(shown(url))\n\nOpen shows it inside BCD, so the game keeps running."
    }
}
