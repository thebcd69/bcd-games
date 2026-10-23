// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

// BCD's interface reads everything it shows from one store and asks for everything it does
// through one set of actions. It never touches the engine (Wine, FEX, the Steam client,
// JIT, Dock) directly: in the app BCDLiveStore fills the store from the engine's models and
// carries the actions out; in the preview app (app/BCDPreview) BCDStore.preview() fills it
// with sample data, so every screen can be built and photographed in the simulator.
// docs/BCD-UI-ARCHITECTURE.md.

// MARK: - What the interface shows

enum BCDStoreFront: String, CaseIterable, Codable, Hashable {
    case steam, epic, gog, battlenet, local
    var title: String {
        switch self {
        case .steam: return "Steam"
        case .epic: return "Epic Games"
        case .gog: return "GOG"
        case .battlenet: return "Battle.net"
        case .local: return "Local"
        }
    }
}

enum BCDInstallPlace: String, Codable, Hashable {
    case device, externalDrive
}

enum BCDGameState: Hashable {
    case ready
    case notInstalled
    case queued
    case downloading(progress: Double)
    case installing(progress: Double)
    case updateAvailable(bytes: Int64)
    case broken(reason: String)

    var isInstalled: Bool {
        switch self {
        case .ready, .updateAvailable, .broken: return true
        default: return false
        }
    }
}

struct BCDGame: Identifiable, Hashable {
    var id: String
    var title: String
    var store: BCDStoreFront
    var state: BCDGameState
    /// Bytes on disk when installed, else the download size when known.
    var size: Int64?
    var place: BCDInstallPlace = .device
    var lastPlayed: Date?
    var minutesPlayed: Int = 0
    /// The store's pictures, each a list of addresses tried in order (a store keeps some only
    /// under newer paths): the tall cover (about 600x900), the wide header (about 460x215),
    /// the wide hero banner (about 1920x620) and the title logo (transparent).
    var coverArt: [URL] = []
    var headerArt: [URL] = []
    var heroArt: [URL] = []
    var logoArt: [URL] = []
    /// Two colours for the artless placeholder, stable per game.
    var tint: (Color, Color) { BCDGame.tint(for: id) }
    var autoTuned = false
    var recommended = false
    var thirtyTwoBit = false
    /// Its place in the library's list of games (a later number is newer): what Library › Sort by ›
    /// Recently added orders by. nil for a game the library has no entry for (an account's game
    /// Steam has not installed, the Wine desktop before its first start).
    var added: Int? = nil

    static func == (a: BCDGame, b: BCDGame) -> Bool {
        a.id == b.id && a.title == b.title && a.store == b.store && a.state == b.state && a.size == b.size
            && a.place == b.place && a.lastPlayed == b.lastPlayed && a.minutesPlayed == b.minutesPlayed
            && a.coverArt == b.coverArt && a.headerArt == b.headerArt && a.heroArt == b.heroArt
            && a.logoArt == b.logoArt && a.autoTuned == b.autoTuned
            && a.recommended == b.recommended && a.thirtyTwoBit == b.thirtyTwoBit && a.added == b.added
    }
    func hash(into h: inout Hasher) { h.combine(id) }

    static func tint(for id: String) -> (Color, Color) {
        let palette: [(Color, Color)] = [
            (Color(red: 0.23, green: 0.11, blue: 0.05), Color(red: 0.54, green: 0.23, blue: 0.07)),
            (Color(red: 0.05, green: 0.16, blue: 0.28), Color(red: 0.17, green: 0.42, blue: 0.66)),
            (Color(red: 0.23, green: 0.05, blue: 0.18), Color(red: 0.70, green: 0.23, blue: 0.42)),
            (Color(red: 0.06, green: 0.19, blue: 0.12), Color(red: 0.25, green: 0.56, blue: 0.35)),
            (Color(red: 0.16, green: 0.07, blue: 0.25), Color(red: 0.42, green: 0.25, blue: 0.70)),
            (Color(red: 0.17, green: 0.17, blue: 0.06), Color(red: 0.56, green: 0.50, blue: 0.16)),
        ]
        var hash: UInt64 = 1469598103934665603
        for byte in id.utf8 { hash = (hash ^ UInt64(byte)) &* 1099511628211 }
        return palette[Int(hash % UInt64(palette.count))]
    }
}

struct BCDDownload: Identifiable, Hashable {
    var id: String              // the game's id
    var title: String
    var store: BCDStoreFront
    var doneBytes: Int64
    var totalBytes: Int64
    var bytesPerSecond: Double
    var paused = false
    var failed: String?
    var fraction: Double { totalBytes > 0 ? min(1, Double(doneBytes) / Double(totalBytes)) : 0 }
}

struct BCDFinishedDownload: Identifiable, Hashable {
    var id: String
    var title: String
    var bytes: Int64
    var finished: Date
}

struct BCDAccount: Identifiable, Hashable {
    var id: BCDStoreFront { store }
    var store: BCDStoreFront
    var name: String?           // nil: not signed in
    var available: Bool         // the store is offered in this build
}

enum BCDJITState: Hashable {
    case on, off, waiting
}

struct BCDSystemStatus: Hashable {
    var jit: BCDJITState = .off
    /// What iOS lets the app use, measured; nil before the first measurement.
    var memoryLimitBytes: Int64?
    var increasedMemoryLimit = false
    /// The extended virtual addressing entitlement (a ~63 GB address map without it). True until measured, so nothing shows early.
    var extendedAddressing = true
    var controllerName: String?
    var controllerBattery: Int?     // percent
    var freeDeviceBytes: Int64?
    var totalDeviceBytes: Int64?
    var freeExternalBytes: Int64?
}

// MARK: - What the interface asks for

@MainActor protocol BCDActions: AnyObject {
    func play(_ game: BCDGame)
    func install(_ game: BCDGame, place: BCDInstallPlace)
    func update(_ game: BCDGame)
    func uninstall(_ game: BCDGame)
    func verify(_ game: BCDGame)
    func pauseDownload(_ id: String)
    func resumeDownload(_ id: String)
    func cancelDownload(_ id: String)
    func enableJIT()
    func signIn(_ store: BCDStoreFront)
    func signOut(_ store: BCDStoreFront)
    func addGameFromFiles()
    func refreshLibrary()
}

// MARK: - The store

@MainActor final class BCDStore: ObservableObject {
    @Published var games: [BCDGame] = []
    @Published var downloads: [BCDDownload] = []
    @Published var finishedToday: [BCDFinishedDownload] = []
    @Published var accounts: [BCDAccount] = []
    @Published var system = BCDSystemStatus()
    /// A game is running: the interface is torn down and draws nothing until it ends.
    @Published var inGame = false
    /// The game whose Play is turning JIT on before it starts (its Play reads so); nil otherwise.
    @Published var startingJIT: String?
    /// The games whose Steam Cloud saves differ from this device's: they wait for a choice on each
    /// game's page (Options › Cloud saves), and the Library says so. Empty without Steam Cloud.
    @Published var savesDiffer: [String] = []
    weak var actions: BCDActions?

    init(actions: BCDActions? = nil) { self.actions = actions }

    var recent: [BCDGame] {
        games.filter { $0.lastPlayed != nil }.sorted { ($0.lastPlayed ?? .distantPast) > ($1.lastPlayed ?? .distantPast) }
    }
    var continuePlaying: BCDGame? { recent.first { $0.state.isInstalled } }
    func count(_ store: BCDStoreFront?) -> Int { store.map { s in games.filter { $0.store == s }.count } ?? games.count }
    var installedCount: Int { games.filter { $0.state.isInstalled }.count }
}

// MARK: - Formatting shared by the screens

enum BCDFormat {
    static func bytes(_ value: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: value, countStyle: .file)
    }
    static func ago(_ date: Date, now: Date = Date()) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        switch seconds {
        case ..<60: return "just now"
        case ..<3600: return "\(Int(seconds / 60)) min ago"
        case ..<86400: return "\(Int(seconds / 3600)) h ago"
        case ..<(86400 * 7): return "\(Int(seconds / 86400)) d ago"
        default: return shortDate(date, now: now)
        }
    }
    // dates-begin
    // Every date the interface or a file name shows is made here, from numbers. `DateFormatter`, `Date.formatted(date:)`
    // and `ISO8601DateFormatter` each build an ICU date formatter (`udat_open`), and that call killed BCD itself in three
    // tester logs (KDY8E6 on build 574, A7V9T2 and DFSTWB on 576: the interface died while it drew a date). The screens are English, so a fixed table of month names does. tests/host/check-bcd-game-page.py
    // forbids those calls in the files that used them; the numbers are checked against Foundation in
    // tests/host/bcd_game_page_checks.swift.

    /// A moment as the calendar numbers a person reads (Gregorian, whatever the device's calendar and digits).
    struct Civil: Equatable {
        var year = 1970, month = 1, day = 1, hour = 0, minute = 0, second = 0, millisecond = 0
    }

    /// `date` in the device's time zone (or in UTC), from arithmetic only. Dates outside any real calendar
    /// (an infinite or NaN interval, or one beyond the year 300,000) come out as 1970-01-01, not as a trap.
    static func civil(_ date: Date, utc: Bool = false) -> Civil {
        let seconds = date.timeIntervalSince1970
        guard seconds.isFinite, abs(seconds) < 1e13 else { return Civil() }
        let offset = utc ? 0 : Double(TimeZone.current.secondsFromGMT(for: date))
        // To the nearest millisecond, as a date formatter does (a moment 0.4 ms short of a second belongs to that second).
        let local = ((seconds + offset) * 1000).rounded() / 1000
        let whole = local.rounded(.down)
        let days = Int((whole / 86400).rounded(.down))
        let secondOfDay = Int(whole) - days * 86400
        let z = days + 719_468
        let era = (z >= 0 ? z : z - 146_096) / 146_097
        let doe = z - era * 146_097
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
        let mp = (5 * doy + 2) / 153
        let month = mp < 10 ? mp + 3 : mp - 9
        return Civil(year: yoe + era * 400 + (month <= 2 ? 1 : 0), month: month, day: doy - (153 * mp + 2) / 5 + 1,
                     hour: secondOfDay / 3600, minute: secondOfDay % 3600 / 60, second: secondOfDay % 60,
                     millisecond: min(999, Int(((local - whole) * 1000).rounded())))
    }

    private static let monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    private static func two(_ value: Int) -> String { value < 10 ? "0\(value)" : "\(value)" }

    /// "12 Oct" (this year) or "12 Oct 2025".
    static func shortDate(_ date: Date, now: Date = Date()) -> String {
        let then = civil(date)
        let text = "\(then.day) \(monthNames[then.month - 1])"
        return then.year == civil(now).year ? text : text + " \(then.year)"
    }
    /// "12 Oct 2025": the date with its year, as the game page's list line and the log lists show it.
    static func fullDate(_ date: Date) -> String {
        let then = civil(date)
        return "\(then.day) \(monthNames[then.month - 1]) \(then.year)"
    }
    /// "12 Oct 2025 at 14:05", in 24-hour time.
    static func dateTime(_ date: Date) -> String {
        let then = civil(date)
        return fullDate(date) + " at \(two(then.hour)):\(two(then.minute))"
    }
    /// "Today", "Yesterday" or "12 Oct 2025": a day compared with today, as Steam's "Last played" says it.
    static func dayWord(_ date: Date, now: Date = Date()) -> String {
        // Whole days since 1970 in the device's time zone; a clock change cannot move a day to its neighbour.
        func dayNumber(_ d: Date) -> Int {
            let seconds = d.timeIntervalSince1970
            guard seconds.isFinite, abs(seconds) < 1e13 else { return 0 }
            return Int(((seconds + Double(TimeZone.current.secondsFromGMT(for: d))) / 86400).rounded(.down))
        }
        let ago = dayNumber(now) - dayNumber(date)
        return ago == 0 ? "Today" : ago == 1 ? "Yesterday" : fullDate(date)
    }
    /// "2026-10-18 14:05": a line of a report.
    static func reportTime(_ date: Date) -> String {
        let c = civil(date)
        return "\(c.year)-\(two(c.month))-\(two(c.day)) \(two(c.hour)):\(two(c.minute))"
    }
    /// File-name stamps, in the device's time zone: "2026-10-18_14-05-07" (with seconds) or "2026-10-18_14-05".
    static func fileStamp(_ date: Date, seconds: Bool = true) -> String {
        let c = civil(date)
        let day = "\(c.year)-\(two(c.month))-\(two(c.day))_\(two(c.hour))-\(two(c.minute))"
        return seconds ? day + "-\(two(c.second))" : day
    }
    /// "20261018-140507": a backup folder's name.
    static func compactStamp(_ date: Date) -> String {
        let c = civil(date)
        return "\(c.year)\(two(c.month))\(two(c.day))-\(two(c.hour))\(two(c.minute))\(two(c.second))"
    }
    /// "2026-10-18T14-05-07Z": UTC, as an ISO 8601 stamp with the colons replaced (a folder name).
    static func utcFolderStamp(_ date: Date) -> String {
        let c = civil(date, utc: true)
        return "\(c.year)-\(two(c.month))-\(two(c.day))T\(two(c.hour))-\(two(c.minute))-\(two(c.second))Z"
    }
    /// "14:05:07.123": the time that starts a log line.
    static func logClock(_ date: Date) -> String {
        let c = civil(date)
        let ms = c.millisecond < 10 ? "00\(c.millisecond)" : c.millisecond < 100 ? "0\(c.millisecond)" : "\(c.millisecond)"
        return "\(two(c.hour)):\(two(c.minute)):\(two(c.second)).\(ms)"
    }
    /// "2026-10-18" (exactly that shape, a real day) as noon of that day in the device's time zone; nil otherwise.
    /// Noon, not midnight: a clock change near midnight cannot move it to the next or the last day.
    static func day(_ text: String) -> Date? {
        let bytes = Array(text.utf8)
        guard bytes.count == 10, bytes[4] == 0x2D, bytes[7] == 0x2D else { return nil }
        func number(_ range: Range<Int>) -> Int? {
            var value = 0
            for byte in bytes[range] { guard byte >= 0x30, byte <= 0x39 else { return nil }; value = value * 10 + Int(byte - 0x30) }
            return value
        }
        guard let year = number(0..<4), let month = number(5..<7), let day = number(8..<10), year >= 1, month >= 1, month <= 12, day >= 1 else { return nil }
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
        guard day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1] else { return nil }
        let y = month <= 2 ? year - 1 : year
        let era = y / 400
        let yoe = y - era * 400
        let doy = (153 * (month > 2 ? month - 3 : month + 9) + 2) / 5 + day - 1
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy
        let utcNoon = Double((era * 146_097 + doe - 719_468) * 86_400 + 43_200)
        let guess = Date(timeIntervalSince1970: utcNoon)
        return Date(timeIntervalSince1970: utcNoon - Double(TimeZone.current.secondsFromGMT(for: guess)))
    }
    // dates-end
    static func playtime(_ minutes: Int) -> String {
        minutes < 60 ? "\(minutes) min" : String(format: "%.1f h", Double(minutes) / 60)
    }
    static func eta(_ download: BCDDownload) -> String? {
        guard download.bytesPerSecond > 1, download.totalBytes > download.doneBytes else { return nil }
        let seconds = Double(download.totalBytes - download.doneBytes) / download.bytesPerSecond
        if seconds < 90 { return "1 min left" }
        let minutes = Int((seconds / 60).rounded())
        return minutes < 60 ? "\(minutes) min left" : "\(minutes / 60) h \(minutes % 60) min left"
    }
}
