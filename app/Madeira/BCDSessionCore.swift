// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import CoreGraphics

// What a session leaves behind, as plain values: the one-line summary written at its end (the [session] line), the marker that says a
// session is open (a marker found at the next start means the app was closed from outside: the "the last session closed unexpectedly"
// question), and the rules for a screenshot. Nothing here touches the engine or UIKit; tests/host/check-bcd-hud.py compiles this file
// alone. The live side is BCDSessionRecorderLive.swift and BCDScreenshotLive.swift.

// MARK: - The session summary

/// One session's frame statistics, written into the log as a single line when the session ends (and when the app goes to the
/// background, marked partial) so that a log says how the game ran without anyone having watched a HUD:
///
///     [session] length=1234s play=1180s frames=71234 avg=58.7 low1=41.2 low01=22.5 worst=215ms stalls=2/14s peak=3412MB limit=5800MB
///               thermal=fair lpm=0 api=D3D11/64 res=1408x648 mfx=2 cap=60 game=Name.exe
///
/// `avg` is frames over the time the game was running between stalls; `low1` and `low01` are the frame rates of the 99th and 99.9th
/// percentile frame (the slowest 1% and 0.1%); `stalls` is the number of gaps of 2 s or more and their total (loading, a menu, the app in
/// the background), which are not in the other numbers. `peak` is the most memory iOS counted against the app this run, `thermal` the
/// highest heat level reached. The game's name is last: it may have spaces.
struct BCDSessionSummary: Equatable {
    var game = ""
    var lengthSeconds = 0
    var playSeconds = 0.0
    var frames: UInt64 = 0
    var activeSeconds = 0.0
    var p99Ms = 0.0
    var p999Ms = 0.0
    var worstMs = 0.0
    var stalls: UInt64 = 0
    var stallSeconds = 0.0
    var peakMB: Int?
    var limitMB: Int?
    /// The highest of nominal, fair, serious, critical (0 ... 3) that the session saw.
    var thermalPeak = 0
    var lowPowerSeen = false
    var api: String?
    var renderSize: String?
    var upscale: String?
    var cap: Int?
    var partial = false

    static let thermalWords = ["nominal", "fair", "serious", "critical"]

    var average: Double? { frames >= 2 && activeSeconds > 0 ? Double(frames) / activeSeconds : nil }
    /// The slowest 1% / 0.1% of frames, as a frame rate; nil when there were too few frames to say.
    var low1: Double? { frames >= 100 && p99Ms > 0 ? 1000 / p99Ms : nil }
    var low01: Double? { frames >= 1000 && p999Ms > 0 ? 1000 / p999Ms : nil }

    func line() -> String {
        var parts = ["length=\(max(0, lengthSeconds))s"]
        if partial { parts.append("partial=1") }
        parts.append(String(format: "play=%.0fs", max(0, playSeconds)))
        parts.append("frames=\(frames)")
        if let average { parts.append(String(format: "avg=%.1f", average)) }
        if let low1 { parts.append(String(format: "low1=%.1f", low1)) }
        if let low01 { parts.append(String(format: "low01=%.1f", low01)) }
        if frames > 0 || stalls > 0 { parts.append(String(format: "worst=%.0fms", worstMs)) }
        parts.append(String(format: "stalls=%llu/%.0fs", stalls, stallSeconds))
        if let peakMB { parts.append("peak=\(peakMB)MB") }
        if let limitMB { parts.append("limit=\(limitMB)MB") }
        parts.append("thermal=" + Self.thermalWords[min(max(thermalPeak, 0), 3)])
        parts.append("lpm=\(lowPowerSeen ? 1 : 0)")
        if let api, !api.isEmpty { parts.append("api=" + Self.token(api)) }
        if let renderSize, !renderSize.isEmpty { parts.append("res=" + Self.token(renderSize)) }
        if let upscale, !upscale.isEmpty { parts.append("mfx=" + Self.token(upscale)) }
        if let cap { parts.append("cap=\(cap)") }
        // The name goes last: it may have spaces, and a line is read to its end.
        parts.append("game=" + game.replacingOccurrences(of: "\n", with: " "))
        return "[session] " + parts.joined(separator: " ")
    }

    /// A value without spaces ("D3D11 64" is "D3D11/64").
    static func token(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespaces).split(separator: " ").joined(separator: "/")
    }

    /// The fields of a [session] line, as text, for the readers (a line this build or an earlier one wrote). nil for any other line.
    static func fields(_ line: String) -> [String: String]? {
        guard let start = line.range(of: "[session] ") else { return nil }
        var rest = Substring(line[start.upperBound...])
        var out: [String: String] = [:]
        while !rest.isEmpty {
            if rest.hasPrefix("game=") { out["game"] = String(rest.dropFirst(5)); break }
            guard let space = rest.firstIndex(of: " ") else { break }
            let word = rest[rest.startIndex..<space]
            if let eq = word.firstIndex(of: "=") { out[String(word[word.startIndex..<eq])] = String(word[word.index(after: eq)...]) }
            rest = rest[rest.index(after: space)...]
        }
        guard out["length"] != nil || out["frames"] != nil else { return nil }
        return out
    }

    /// What a person reads: "58.7 FPS on average, 41 for the slowest 1%, 21 minutes".
    var words: String {
        var parts: [String] = []
        if let average { parts.append(String(format: "%.0f FPS on average", average)) }
        if let low1 { parts.append(String(format: "%.0f for the slowest 1%%", low1)) }
        parts.append(playSeconds >= 90 ? "\(Int((playSeconds / 60).rounded())) minutes" : String(format: "%.0f seconds", max(0, playSeconds)))
        return parts.joined(separator: ", ")
    }
}

// MARK: - The marker of an open session

/// Written when a library session starts and removed when it ends cleanly (the game closed and the app is back at the library, or the
/// player closed BCD on purpose). One found at the next start says the app was closed from outside while the session was open: iOS ended
/// it (memory), it crashed, or the player closed it from the app switcher.
struct BCDSessionMarker: Codable, Equatable {
    var title: String
    /// The program's file name, as the session log is named.
    var program: String
    /// Seconds since 1970.
    var startedAt: Double
    var build: String
    /// The log file's inode: the session's log is found again by it (bcd-log.prev.txt, or a logs/ link), whatever its name became.
    var logInode: UInt64
    /// The last the app said about itself: in the background (true) or in front.
    var background = false
    /// When it last went to or came from the background.
    var changedAt: Double
}

struct BCDCrashOffer: Equatable {
    var title: String
    var minutes: Int
    var background: Bool
    /// madeira-bcd (round 624, agent N S14): how long the session lasted, in seconds (nil: not known).
    var seconds: Int? = nil

    var heading: String { "The last session closed unexpectedly" }

    var message: String {
        // madeira-bcd (round 624, agent N S14; FWXFQ1): a start that went to the background within 90 s was left while it started; iOS
        // pauses such a game and may close it. The memory story below is the wrong one for it.
        if background, let seconds, seconds < BCDCrashRules.shortStart {
            return "BCD went to the background \(seconds) seconds after you pressed Play, while \(title) was still starting. iOS pauses a game that "
                + "is started and left, and may close it. Stay in BCD until the game's picture shows. If something took you away (a sign-in, "
                + "StikDebug for JIT), come back and press Play again.\n\nYou can still send the log. Before it leaves the device BCD takes out "
                + "folder paths, user names, e-mail and IP addresses."
        }
        let played = minutes < 1 ? "" : " after about \(minutes) minute\(minutes == 1 ? "" : "s")"
        let what = background
            ? "BCD was closed while it was in the background and \(title) was running\(played). iOS does this when the device runs short of memory; closing BCD from the app switcher looks the same."
            : "BCD closed while \(title) was running\(played)."
        return what + "\n\nSending the log helps find out why. You can add a comment first. Before it leaves the device BCD takes out folder paths, user names, e-mail and IP addresses."
    }
}

enum BCDCrashRules {
    /// A marker older than this is not asked about: the player has moved on.
    static let maxAge: Double = 7 * 86400
    /// madeira-bcd (round 624, agent N S14): a session that went to the background less than `shortStart` seconds after Play was a start that
    /// was left (its own words); under `leftTooSoon` seconds nothing is asked: its log holds the first seconds of a start and nothing else.
    static let shortStart = 90
    static let leftTooSoon = 20

    /// The question for a marker found at start, or nil: no marker, the player turned the question off, the marker is too old or from
    /// the future. `endedAt`: when the session's log was last written (seconds since 1970; nil: unknown), the best sign of when it ended.
    static func offer(marker: BCDSessionMarker?, endedAt: Double?, now: Double, enabled: Bool) -> BCDCrashOffer? {
        guard enabled, let marker else { return nil }
        guard marker.startedAt > 0, marker.startedAt <= now + 60, now - marker.startedAt <= maxAge else { return nil }
        let end = background(marker, endedAt: endedAt)
        let minutes = Int(max(0, end - marker.startedAt) / 60)
        let seconds = Int(max(0, end - marker.startedAt))
        if marker.background && seconds < leftTooSoon { return nil }
        let title = marker.title.trimmingCharacters(in: .whitespacesAndNewlines)
        return BCDCrashOffer(title: title.isEmpty ? "a game" : title, minutes: minutes, background: marker.background, seconds: seconds)
    }

    /// The [session] line's reason when offer(...) gives nil for a marker that was found.
    static func notAsking(marker: BCDSessionMarker, now: Double, enabled: Bool) -> String {
        guard enabled else { return "turned off" }
        guard marker.startedAt > 0, marker.startedAt <= now + 60, now - marker.startedAt <= maxAge else { return "too old" }
        return marker.background ? "it went to the background within \(leftTooSoon) s of the start" : "too old"
    }

    /// The moment the session is taken to have ended: the log's last write, but never before the session began nor before the app
    /// went to the background (which is when it stopped being played).
    private static func background(_ marker: BCDSessionMarker, endedAt: Double?) -> Double {
        if marker.background { return max(marker.startedAt, marker.changedAt) }
        guard let endedAt, endedAt >= marker.startedAt else { return marker.startedAt }
        return endedAt
    }
}

// MARK: - Screenshots

enum BCDScreenshotRules {
    /// The picture's long edge in pixels at most: a screenshot is for a person or a bug report, not for print.
    static let longEdge: CGFloat = 2000
    static let jpegQuality = 0.85
    /// The most the kept copy (Files, Photos) is allowed to weigh: above this the picture is saved again at a lower quality. The kept copy
    /// is not the one that is sent.
    static let keptMaxBytes = 4 << 20
    /// The copy that goes with Send Log is made apart from the kept one (BCDScreenshotShrink): it stops being lowered as soon as it is
    /// this size or less (owner, 2026-10-25: 80-100 KB, never a 1-2 MB picture).
    static let uploadTargetBytes = 100 * 1024
    /// Where its search starts and ends: the long edge in pixels, and the JPEG quality at the floor. If even the floor is over the target,
    /// the floor's picture is what is sent.
    static let uploadStartEdge = 1280
    static let uploadFloorEdge = 640
    static let uploadFloorQuality = 0.4
    /// The most the intake takes (bcd-logs/worker/worker.js MAX_SHOT_BYTES): the target and some room for a floor picture that is over it.
    static let maxSendBytes = 160 * 1024
    /// The folder in the app's Documents (visible in Files).
    static let folder = "Screenshots"
    /// Free memory below which the app does not draw a picture: a game this close to iOS's limit must not be asked for 10 MB more.
    static let minFreeMB = 160

    /// The scale to draw a view of `size` points at: the screen's, but no more than keeps the long edge under `longEdge` pixels.
    static func scale(for size: CGSize, screenScale: CGFloat) -> CGFloat {
        let long = max(size.width, size.height)
        guard long > 0 else { return 1 }
        return max(0.25, min(screenScale, longEdge / long))
    }

    /// "Hollow Knight 2026-10-25 21.05.33.jpg": the game's name, cleaned of anything a file name should not hold, and the moment.
    static func fileName(game: String, year: Int, month: Int, day: Int, hour: Int, minute: Int, second: Int) -> String {
        let stamp = String(format: "%04d-%02d-%02d %02d.%02d.%02d", year, month, day, hour, minute, second)
        let allowed = Set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_()'")
        var name = String(game.map { allowed.contains($0) ? $0 : " " }).split(separator: " ").joined(separator: " ")
        if name.count > 40 { name = String(name.prefix(40)).trimmingCharacters(in: .whitespaces) }
        return (name.isEmpty ? "BCD" : name) + " " + stamp + ".jpg"
    }

    /// The start of a session log from its name ("war3.exe-2026-10-16_22-24-07.txt"), in the device's own time zone; nil for a name with none
    /// (bcd-log.txt).
    static func start(ofLogNamed name: String, calendar: Calendar = .current) -> Date? {
        let base = (name as NSString).deletingPathExtension
        guard let range = base.range(of: #"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$"#, options: .regularExpression) else { return nil }
        let digits = base[range].dropFirst().split(whereSeparator: { $0 == "-" || $0 == "_" }).compactMap { Int($0) }
        guard digits.count == 6 else { return nil }
        var c = DateComponents()
        c.year = digits[0]; c.month = digits[1]; c.day = digits[2]; c.hour = digits[3]; c.minute = digits[4]; c.second = digits[5]
        return calendar.date(from: c)
    }

    /// Whether a screenshot belongs to a log: it was taken while the session ran, within a minute either side. A log whose name has no
    /// start (bcd-log.txt) is taken to cover the 12 hours before its last line.
    static func belongs(shot: Date, logStart: Date?, logEnd: Date) -> Bool {
        let slack: TimeInterval = 60
        let from = logStart.map { $0.addingTimeInterval(-slack) } ?? logEnd.addingTimeInterval(-12 * 3600)
        return shot >= from && shot <= logEnd.addingTimeInterval(slack)
    }

    /// The words under the choice to send it: the picture cannot be cleaned like the log is.
    static let sentAsItIs = "A small copy of the picture (about 100 KB) is sent as it is: BCD cannot take anything out of it. Leave it out if the game shows your name or an account."
}

/// madeira-bcd (round 624, agent R S5: GTA V RT83GE and 6D6ZZS): after a session that did not end cleanly, the new log gets the last lines of
/// the failed run's log as "[prev-tail]" lines, because a Send Log of the new run does not hold them. The app reads the last 64 KB of that log;
/// this keeps whole lines only (a first line cut by the read is dropped), the last 60, each at most 400 characters. The live side scrubs every
/// line with the upload scrubber before it is logged.
enum BCDPrevTail {
    static let maxBytes = 65536
    static let maxLines = 60
    static let maxLineLength = 400

    /// `data` is the file from `offset` to its end. Split on the bytes (Swift's "\r\n" is one Character, which no "\n" split would cut).
    static func lines(_ data: Data, offset: UInt64) -> [String] {
        var parts = data.split(separator: UInt8(ascii: "\n"), omittingEmptySubsequences: true)
        if offset > 0, !parts.isEmpty, data.first != UInt8(ascii: "\n") { parts.removeFirst() }
        return parts.suffix(maxLines).compactMap { bytes in
            let text = bytes.last == UInt8(ascii: "\r") ? bytes.dropLast() : bytes
            return text.isEmpty ? nil : String(String(decoding: text, as: UTF8.self).prefix(maxLineLength))
        }
    }
}
