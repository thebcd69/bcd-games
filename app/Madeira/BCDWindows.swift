// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The words and the order of the in-game menu's Windows page (round 604, B1): what the Windows desktop has open, in front first, then the
// others as they were shown, then the ones minimized. Plain values (tests/host/check-bcd-windows.py compiles this file alone); the list
// itself comes from Winios (winios_windows_copy) in BCDWindowsLive.swift.

struct BCDListedWindow: Equatable {
    var hwnd: UInt64
    var pid: UInt32 = 0
    var title = ""
    /// The owning program's path as the server recorded it ("c:\windows\system32\explorer.exe"), lower case, or "".
    var image = ""
    var width = 0, height = 0
    var visible = false
    var minimized = false
    var foreground = false
    var metal = false
}

enum BCDWindowRules {
    /// The program's file name from its path ("explorer.exe"), or "" when the path is empty.
    static func program(_ image: String) -> String {
        let name = image.split(whereSeparator: { $0 == "\\" || $0 == "/" }).last.map(String.init) ?? ""
        return name
    }

    /// What the list calls a window: its title, else the program's name, else "Window".
    static func name(_ window: BCDListedWindow) -> String {
        let title = window.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !title.isEmpty { return title }
        let program = program(window.image)
        return program.isEmpty ? "Window" : program
    }

    /// "explorer.exe · 640 × 480 · in front".
    static func detail(_ window: BCDListedWindow) -> String {
        var parts: [String] = []
        let program = program(window.image)
        if !program.isEmpty { parts.append(program) }
        if window.width > 0, window.height > 0 { parts.append("\(window.width) × \(window.height)") }
        if window.foreground { parts.append("in front") }
        else if window.minimized { parts.append("minimized") }
        if window.metal { parts.append("draws the game") }
        return parts.joined(separator: " · ")
    }

    /// The windows a person would call windows: not the empty ones some programs keep for messages (no size, no title) and not the shell's
    /// desktop and tray ("Program Manager" and a tray window of explorer), which are not something to bring to the front.
    static func shown(_ all: [BCDListedWindow]) -> [BCDListedWindow] {
        all.filter { window in
            if window.width <= 1 && window.height <= 1 && !window.minimized { return false }
            let title = window.title.trimmingCharacters(in: .whitespacesAndNewlines)
            if program(window.image) == "explorer.exe" && (title == "Program Manager" || title.isEmpty) { return false }
            return true
        }
    }

    /// In front first, then the visible ones in the order Windows gave them (top of the stack first), then the minimized ones.
    static func ordered(_ all: [BCDListedWindow]) -> [BCDListedWindow] {
        let rest = all.enumerated().filter { !$0.element.foreground }
        let front = all.filter(\.foreground)
        let visible = rest.filter { !$0.element.minimized }.map(\.element)
        let minimized = rest.filter { $0.element.minimized }.map(\.element)
        return front + visible + minimized
    }

    /// The row id for a window and back: "mm.win.front.<hwnd in hex>".
    static let rowPrefix = "mm.win.front."
    static func rowID(_ window: BCDListedWindow) -> String { rowPrefix + String(window.hwnd, radix: 16) }
    static func hwnd(ofRow id: String) -> UInt64? {
        guard id.hasPrefix(rowPrefix) else { return nil }
        return UInt64(id.dropFirst(rowPrefix.count), radix: 16)
    }

    /// The page's summary under the Windows row: "3 open".
    static func summary(_ all: [BCDListedWindow]) -> String {
        let n = shown(all).count
        return n == 1 ? "1 open" : "\(n) open"
    }
}

// MARK: - The programs the game started (the Task Manager row)

/// One line of the process snapshot the engine keeps (madeira_child_report in process_ios.c):
///   R <helper> <seconds since start> <path>                                still runs
///   E <helper> <status in hex> <seconds since the end> <seconds it lived> <path>    ended (the last few)
struct BCDRunningProgram: Equatable {
    var name: String
    var path: String
    var running: Bool
    /// A crash reporter or other helper by the engine's file-name rule.
    var helper: Bool
    /// Seconds it has run (running) or lived (ended).
    var seconds: Double
    /// An ended program's exit status; nil while it runs.
    var status: UInt32?
}

enum BCDProgramsRules {
    static func parse(_ report: String) -> [BCDRunningProgram] {
        var out: [BCDRunningProgram] = []
        for line in report.split(separator: "\n", omittingEmptySubsequences: true) {
            let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
            switch f.first {
            case "R" where f.count >= 4:
                let path = f[3...].joined(separator: "\t")
                out.append(BCDRunningProgram(name: BCDWindowRules.program(path), path: path, running: true, helper: (Int(f[1]) ?? 0) & 1 != 0,
                                             seconds: Double(f[2]) ?? 0, status: nil))
            case "E" where f.count >= 6:
                let path = f[5...].joined(separator: "\t")
                out.append(BCDRunningProgram(name: BCDWindowRules.program(path), path: path, running: false, helper: (Int(f[1]) ?? 0) & 1 != 0,
                                             seconds: Double(f[4]) ?? 0, status: UInt32(f[2], radix: 16)))
            default:
                break
            }
        }
        return out
    }

    /// "3 min", "42 s": how long a program has run.
    static func duration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds))
        if s < 90 { return "\(s) s" }
        if s < 3600 { return "\(s / 60) min" }
        return "\(s / 3600) h \((s % 3600) / 60) min"
    }

    static func detail(_ program: BCDRunningProgram) -> String {
        if program.running { return "running for " + duration(program.seconds) + (program.helper ? " · a helper" : "") }
        let status = program.status.map { $0 == 0 ? "ended normally" : String(format: "ended with status 0x%X", $0) } ?? "ended"
        return status + " after " + duration(program.seconds)
    }
}
