// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// madeira-bcd: stand-ins for the app types app/Madeira/AutoTune.swift uses, so
// tests/host/check-autotune.py can compile it on the host (docs/AUTO-TUNE.md).
// Paths come from the environment the check sets up.

import Foundation

struct LibraryEntry {
    var windowsPath: String
    var title: String
    var desktop: Bool?
    var gameConfigText: String?
}

enum LogEntry { enum Level { case info, success, error } }

final class LogStore {
    static let shared = LogStore()
    private let lock = NSLock()
    private var collected: [String] = []
    var lines: [String] { lock.lock(); defer { lock.unlock() }; return collected }
    let logFileURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["AUTOTUNE_TEST_LOG"] ?? "/dev/null")
    func log(_ message: String, level: LogEntry.Level = .info) {
        lock.lock(); collected.append(message); lock.unlock()
    }
}

enum LibraryPrefs {
    static func avx(_ windowsPath: String) -> Bool { false }
    static func wineVCRT(_ windowsPath: String) -> Bool { false }
    static func nvidia(_ windowsPath: String) -> Bool { true }
    static func stableHash(_ s: String) -> UInt64 {
        var h: UInt64 = 0xcbf29ce484222325
        for b in s.utf8 { h = (h ^ UInt64(b)) &* 0x100000001b3 }
        return h
    }
}

enum MadeiraConfig {
    static var documents: URL? { ProcessInfo.processInfo.environment["AUTOTUNE_TEST_DOCS"].map { URL(fileURLWithPath: $0) } }
    static func all() -> [String: String] {
        guard let d = documents, let t = try? String(contentsOf: d.appendingPathComponent("bcd.cfg"), encoding: .utf8) else { return [:] }
        return parse(t)
    }
    static func parse(_ text: String) -> [String: String] {
        var out: [String: String] = [:]
        for raw in text.split(whereSeparator: { $0 == "\n" || $0 == "\r\n" }) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let k = line[..<eq].trimmingCharacters(in: .whitespaces)
            if !k.isEmpty { out[k] = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces) }
        }
        return out
    }
}

struct GameProfile {
    let windowsPath: String
    static var directory: URL? {
        ProcessInfo.processInfo.environment["AUTOTUNE_TEST_CONFIGS"].map { URL(fileURLWithPath: $0, isDirectory: true) }
    }
    var url: URL? {
        GameProfile.directory?.appendingPathComponent(String(format: "%016llx.cfg", LibraryPrefs.stableHash(windowsPath.lowercased())))
    }
    var text: String {
        get { url.flatMap { try? String(contentsOf: $0, encoding: .utf8) } ?? "" }
        nonmutating set { if let u = url { try? newValue.write(to: u, atomically: true, encoding: .utf8) } }
    }
    var values: [String: String] { MadeiraConfig.parse(text) }
}

/// WineMono.swift's model: whether Wine Mono is here (AUTOTUNE_TEST_MONO=1 says it is).
// Round 624 (agent N S1): the install's memory rights, read by the stuck alert (MemoryRights.swift); an install with the limit here.
enum MemoryRights {
    static let lacking = false
    static let failureNote = "This install lacks the increased memory limit, the usual reason a game stops this early."
}

@MainActor enum WineMonoModel {
    nonisolated static var available: Bool { ProcessInfo.processInfo.environment["AUTOTUNE_TEST_MONO"] == "1" }
}

/// GameRecommendationsApply.swift's record of what BCD wrote from the recommended list: AUTOTUNE_TEST_FOLLOWS_LIST=1 says the game's file is exactly
/// what the list wrote, 0 that the player changed it; absent: nothing was written for the game.
enum GameRecommendations {
    static func followsList(windowsPath: String) -> Bool? {
        switch ProcessInfo.processInfo.environment["AUTOTUNE_TEST_FOLLOWS_LIST"] {
        case "1": return true
        case "0": return false
        default: return nil
        }
    }
}
