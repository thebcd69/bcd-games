// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import CoreGraphics

// The performance HUD, as plain values (docs/BCD-UI-ARCHITECTURE.md; design: the Winlator-family study, section 3, and the
// owner's choice of 2026-10-25, "4'lü HUD düzenini beğendim"): Off, four finished layouts (1 Pulse, 2 Strip, 3 Tune, 4 Report)
// and a Custom one the player builds from items. Nothing in this file touches the engine, UIKit or SwiftUI: what to show
// (BCDHUDLayout, BCDHUDItem, BCDHUDCustom), how an old setting carries over (BCDHUDMigration), the frame-rate arithmetic
// (BCDFrameMath), the colours' rules (BCDHUDRules), the rows a layout makes of the readings (BCDHUDRows) and where the HUD sits
// (BCDHUDPlacement). tests/host/check-bcd-hud.py compiles this file alone and checks every rule.
//
// Cost: the live side (BCDHUDLive.swift) reads the frame-time ring (BCDFrameRing.c) and the system's counters twice a second,
// only while a layout is on, and only the counters the layout shows (BCDHUDNeeds). Off, nothing here runs.

// MARK: - Layouts

enum BCDHUDLayout: String, CaseIterable, Codable, Identifiable {
    case off, pulse = "1", strip = "2", tune = "3", report = "4", custom

    var id: String { rawValue }

    /// The chip in the menu and in Settings.
    var title: String {
        switch self {
        case .off: return "Off"
        case .pulse: return "1"
        case .strip: return "2"
        case .tune: return "3"
        case .report: return "4"
        case .custom: return "Custom"
        }
    }

    var name: String {
        switch self {
        case .off: return "Off"
        case .pulse: return "Pulse"
        case .strip: return "Strip"
        case .tune: return "Tune"
        case .report: return "Report"
        case .custom: return "Custom"
        }
    }

    /// One sentence for the menu: what the layout shows.
    var summary: String {
        switch self {
        case .off: return "Nothing on the screen."
        case .pulse: return "One small number: the frame rate. A dot appears only if something is wrong."
        case .strip: return "One line: frame rate, frame time, memory against the iOS limit and heat."
        case .tune: return "Three short lines and a 15 second graph: frame rate and its 1% low, processor and graphics load, memory, heat."
        case .report: return "The whole picture for a bug report: graphics API and size, frame rate with its lows, a 30 second graph, load, memory, the JIT pool, heat, battery, HDR output and time."
        case .custom: return "Your own list of readings, where you want them."
        }
    }

    var shown: Bool { self != .off }

    /// The four finished layouts.
    static let presets: [BCDHUDLayout] = [.pulse, .strip, .tune, .report]

    /// What the live side has to measure for this layout (a Custom layout: for its items, see `BCDHUDCustom.needs`).
    var needs: BCDHUDNeeds {
        switch self {
        case .off: return []
        case .pulse: return [.frames, .memory]
        case .strip: return [.frames, .memory]
        case .tune: return [.frames, .lows, .graph, .cpu, .gpu, .memory]
        case .report: return [.frames, .lows, .graph, .cpu, .gpu, .memory, .jit, .swap, .battery, .hdr]
        case .custom: return []
        }
    }

    /// Layouts 3 and 4 and a Custom one with five items or more write what they show into the log (design rule 10).
    var logsReadings: Bool { self == .tune || self == .report }

    /// Layouts 3 and 4 fade after 6 calm seconds.
    var dimsWhenCalm: Bool { self == .tune || self == .report }
}

/// What the live side must measure. The frame ring is always on (BCDFrameRing.c); the rest is read only for a layout that shows it.
struct BCDHUDNeeds: OptionSet, Equatable {
    let rawValue: Int
    static let frames = BCDHUDNeeds(rawValue: 1 << 0)
    /// 1% and 0.1% lows, the worst frame and the stutters: a rolling window of frame times.
    static let lows = BCDHUDNeeds(rawValue: 1 << 1)
    /// The frame-time graph's buckets.
    static let graph = BCDHUDNeeds(rawValue: 1 << 2)
    static let cpu = BCDHUDNeeds(rawValue: 1 << 3)
    /// Switches the GPU meter on (a completion handler per command buffer): only while a GPU item is shown.
    static let gpu = BCDHUDNeeds(rawValue: 1 << 4)
    static let gpuMemory = BCDHUDNeeds(rawValue: 1 << 5)
    static let memory = BCDHUDNeeds(rawValue: 1 << 6)
    static let jit = BCDHUDNeeds(rawValue: 1 << 7)
    static let swap = BCDHUDNeeds(rawValue: 1 << 8)
    static let battery = BCDHUDNeeds(rawValue: 1 << 9)
    /// Samples the game's output only for Report or a Custom HDR item.
    static let hdr = BCDHUDNeeds(rawValue: 1 << 10)
}

// MARK: - Items (Custom)

/// A reading the player can put in a Custom HUD. Only what iOS lets BCD measure: no degrees, watts or clock speeds.
enum BCDHUDItem: String, CaseIterable, Codable, Identifiable {
    // Speed
    case fps, fpsCap, frameMs, frameGraph, low1, low01, worstFrame, stutters, limit
    // Processor, graphics
    case cpu, gpu, gpuMemory
    // Memory
    case memUsed, memory, memFree, memPeak, compressed, swap, jit
    // Heat and battery
    case thermal, lowPower, battery
    // Pipeline
    case api, resolution, metalFX, frameGen, gpuName, hdr
    // Session
    case playTime, clock

    var id: String { rawValue }

    enum Group: String, CaseIterable {
        case speed = "Speed", processor = "Processor", graphics = "Graphics", memory = "Memory", heat = "Heat and battery"
        case pipeline = "Pipeline", session = "Session"
    }

    var group: Group {
        switch self {
        case .fps, .fpsCap, .frameMs, .frameGraph, .low1, .low01, .worstFrame, .stutters, .limit: return .speed
        case .cpu: return .processor
        case .gpu, .gpuMemory: return .graphics
        case .memUsed, .memory, .memFree, .memPeak, .compressed, .swap, .jit: return .memory
        case .thermal, .lowPower, .battery: return .heat
        case .api, .resolution, .metalFX, .frameGen, .gpuName, .hdr: return .pipeline
        case .playTime, .clock: return .session
        }
    }

    var title: String {
        switch self {
        case .fps: return "Frame rate"
        case .fpsCap: return "Frame rate against the limit"
        case .frameMs: return "Frame time"
        case .frameGraph: return "Frame-time graph"
        case .low1: return "1% low"
        case .low01: return "0.1% low"
        case .worstFrame: return "Worst frame"
        case .stutters: return "Stutters"
        case .limit: return "Frame limit"
        case .cpu: return "Processor load"
        case .gpu: return "Graphics load"
        case .gpuMemory: return "Graphics memory"
        case .memUsed: return "Memory in use"
        case .memory: return "Memory against the limit"
        case .memFree: return "Memory left"
        case .memPeak: return "Memory peak"
        case .compressed: return "Compressed memory"
        case .swap: return "Swap file"
        case .jit: return "JIT pool"
        case .thermal: return "Heat"
        case .lowPower: return "Low Power Mode"
        case .battery: return "Battery"
        case .api: return "Graphics API"
        case .resolution: return "Resolution"
        case .metalFX: return "MetalFX"
        case .hdr: return "HDR output"
        case .frameGen: return "Frame generation"
        case .gpuName: return "GPU shown to the game"
        case .playTime: return "Play time"
        case .clock: return "Clock"
        }
    }

    /// One short line of what it shows.
    var note: String {
        switch self {
        case .fps: return "Frames a second, green or amber or red against the limit."
        case .fpsCap: return "For example 58/60."
        case .frameMs: return "The average of the last half second."
        case .frameGraph: return "A line of the slowest frame in every quarter second."
        case .low1: return "The frame rate of the slowest 1% of frames, over the last 30 seconds."
        case .low01: return "The slowest 0.1%: needs about a minute of play."
        case .worstFrame: return "The longest frame of the last 10 seconds."
        case .stutters: return "Frames in the last minute that took twice the usual time."
        case .limit: return "The frame limit and the screen's rate."
        case .cpu: return "All cores, and the busiest thread."
        case .gpu: return "How busy the graphics chip is. Switches a small meter on."
        case .gpuMemory: return "What the graphics chip has allocated, against what iOS recommends."
        case .memUsed: return "What iOS counts against the app."
        case .memory: return "In use against the limit iOS gives the app."
        case .memFree: return "What is left before iOS closes the app."
        case .memPeak: return "The most memory this run has used."
        case .compressed: return "Memory iOS has squeezed."
        case .swap: return "Game data kept in a file instead of memory."
        case .jit: return "How much of the code pool is handed out."
        case .thermal: return "Cool, warm, hot or too hot."
        case .lowPower: return "Whether the device is saving power."
        case .battery: return "The charge."
        case .api: return "Direct3D version and 32 or 64 bit."
        case .resolution: return "The size the game draws at, and the size MetalFX makes of it."
        case .metalFX: return "The upscaling factor."
        case .hdr: return "Sampled game peak and display headroom, relative to SDR white. Enables a small GPU probe."
        case .frameGen: return "Whether frame generation is on."
        case .gpuName: return "The graphics card name the game is told."
        case .playTime: return "Since the game started."
        case .clock: return "The time of day."
        }
    }

    var needs: BCDHUDNeeds {
        switch self {
        case .fps, .fpsCap, .frameMs, .limit: return .frames
        case .frameGraph: return [.frames, .graph]
        case .low1, .low01, .worstFrame, .stutters: return [.frames, .lows]
        case .cpu: return .cpu
        case .gpu: return .gpu
        case .gpuMemory: return .gpuMemory
        case .memUsed, .memory, .memFree, .memPeak, .compressed: return .memory
        case .swap: return .swap
        case .jit: return .jit
        case .hdr: return .hdr
        case .battery: return .battery
        case .thermal, .lowPower, .api, .resolution, .metalFX, .frameGen, .gpuName, .playTime, .clock: return []
        }
    }

    /// The old overlay's field names (LibraryEntry.overlayFields) and the item each one is.
    static func legacy(_ field: String) -> BCDHUDItem? {
        switch field {
        case "FPS": return .fps
        case "Frame time": return .frameMs
        case "CPU": return .cpu
        case "GPU": return .gpu
        case "RAM": return .memUsed
        case "Battery": return .battery
        case "Thermal": return .thermal
        default: return nil
        }
    }
}

/// The player's own HUD: which readings, in which shape, how large and how clear.
struct BCDHUDCustom: Codable, Equatable {
    enum Shape: String, Codable, CaseIterable {
        /// Lines of readings side by side, as the old overlay's plate.
        case strip
        /// One reading to a line.
        case list
        /// Two columns.
        case table
        var title: String {
            switch self {
            case .strip: return "Strip"
            case .list: return "List"
            case .table: return "Table"
            }
        }
    }

    enum Size: String, Codable, CaseIterable {
        case small, medium, large
        var title: String {
            switch self {
            case .small: return "S"
            case .medium: return "M"
            case .large: return "L"
            }
        }
        var points: CGFloat {
            switch self {
            case .small: return 10
            case .medium: return 12
            case .large: return 14
            }
        }
        var graphScale: CGFloat {
            switch self {
            case .small: return 0.85
            case .medium: return 1
            case .large: return 1.2
            }
        }
    }

    var items: [BCDHUDItem] = [.fps, .frameMs, .memory, .thermal]
    var shape = Shape.strip
    var size = Size.medium
    /// 0.4 ... 1.
    var textOpacity = 1.0
    /// 0 ... 1.
    var backgroundOpacity = 0.55
    /// The graph's length in seconds: 15, 30 or 60.
    var graphSeconds = 30
    var dimWhenCalm = false
    /// The player has chosen or changed readings (or this list came from an old overlay): choosing Custom keeps it. Until then choosing
    /// Custom starts as a copy of the finished layout the player was on.
    var touched = false

    static let `default` = BCDHUDCustom()

    var needs: BCDHUDNeeds { items.reduce(into: BCDHUDNeeds()) { $0.formUnion($1.needs) } }

    enum CodingKeys: String, CodingKey { case items, shape, size, textOpacity, backgroundOpacity, graphSeconds, dimWhenCalm, touched }

    init() {}

    /// Tolerant: a missing key keeps its default, an item this build does not know is dropped (a later build's file).
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let names = try c.decodeIfPresent([String].self, forKey: .items) {
            var seen = Set<BCDHUDItem>()
            items = names.compactMap { BCDHUDItem(rawValue: $0) }.filter { seen.insert($0).inserted }
        }
        shape = (try? c.decodeIfPresent(String.self, forKey: .shape)).flatMap(Shape.init(rawValue:)) ?? .strip
        size = (try? c.decodeIfPresent(String.self, forKey: .size)).flatMap(Size.init(rawValue:)) ?? .medium
        textOpacity = Self.clamp((try? c.decodeIfPresent(Double.self, forKey: .textOpacity)) ?? 1, 0.4, 1)
        backgroundOpacity = Self.clamp((try? c.decodeIfPresent(Double.self, forKey: .backgroundOpacity)) ?? 0.55, 0, 1)
        let seconds = (try? c.decodeIfPresent(Int.self, forKey: .graphSeconds)) ?? 30
        graphSeconds = [15, 30, 60].contains(seconds) ? seconds : 30
        dimWhenCalm = (try? c.decodeIfPresent(Bool.self, forKey: .dimWhenCalm)) ?? false
        touched = (try? c.decodeIfPresent(Bool.self, forKey: .touched)) ?? false
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(items.map(\.rawValue), forKey: .items)
        try c.encode(shape.rawValue, forKey: .shape)
        try c.encode(size.rawValue, forKey: .size)
        try c.encode(textOpacity, forKey: .textOpacity)
        try c.encode(backgroundOpacity, forKey: .backgroundOpacity)
        try c.encode(graphSeconds, forKey: .graphSeconds)
        try c.encode(dimWhenCalm, forKey: .dimWhenCalm)
        try c.encode(touched, forKey: .touched)
    }

    static func clamp(_ v: Double, _ lo: Double, _ hi: Double) -> Double { v.isFinite ? min(max(v, lo), hi) : lo }

    /// The items in the order the editor lists them, whatever order they were switched on in.
    static func canonical(_ items: [BCDHUDItem]) -> [BCDHUDItem] {
        let on = Set(items)
        return BCDHUDItem.allCases.filter(on.contains)
    }

    /// A Custom HUD that starts as a copy of a finished layout (so the editor never opens on an empty list).
    static func startingFrom(_ layout: BCDHUDLayout) -> BCDHUDCustom {
        var custom = BCDHUDCustom()
        switch layout {
        case .pulse:
            custom.items = [.fps]
        case .strip:
            custom.items = [.fps, .frameMs, .memory, .thermal]
        case .tune:
            custom.items = [.fps, .low1, .frameMs, .cpu, .gpu, .memory, .thermal, .frameGraph]
            custom.shape = .list
            custom.graphSeconds = 15
            custom.dimWhenCalm = true
        case .report:
            custom.items = [.api, .resolution, .hdr, .fps, .frameMs, .low1, .low01, .frameGraph, .cpu, .gpu, .memory, .memPeak, .swap, .jit, .thermal,
                            .lowPower, .battery, .playTime]
            custom.shape = .list
            custom.size = .small
            custom.dimWhenCalm = true
        case .off, .custom:
            break
        }
        return custom
    }
}

// MARK: - Where it sits

/// The shape of the window the HUD is drawn in, from its size alone (never from a device model or a fixed screen size): a dragged
/// position is remembered for each. A phone held sideways, an iPad window and a wide Stage Manager window are `landscape`; upright ones
/// are `portrait`; a near-square display (the inner screen of a folding phone, a square window) is `square`. The class is read again
/// whenever the window changes size, so a fold, an unfold, a rotation or a resized window picks up its own remembered place.
enum BCDHUDScreenClass: String, Codable, CaseIterable {
    case landscape, portrait, square

    static func of(_ size: CGSize) -> BCDHUDScreenClass {
        guard size.width > 0, size.height > 0 else { return .landscape }
        let ratio = size.width / size.height
        if ratio > 1.25 { return .landscape }
        if ratio < 0.8 { return .portrait }
        return .square
    }
}

enum BCDHUDSlot: String, CaseIterable, Codable {
    case topLeading = "top-leading", top, topTrailing = "top-trailing"
    case bottomLeading = "bottom-leading", bottom, bottomTrailing = "bottom-trailing"

    var title: String {
        switch self {
        case .topLeading: return "Top left"
        case .top: return "Top"
        case .topTrailing: return "Top right"
        case .bottomLeading: return "Bottom left"
        case .bottom: return "Bottom"
        case .bottomTrailing: return "Bottom right"
        }
    }
}

/// Where the HUD is: found by BCD (the default), in a corner or at an edge, or dragged by hand (the centre as shares of the screen,
/// as the old overlay kept it).
enum BCDHUDPlace: Equatable, Codable {
    case auto
    case slot(BCDHUDSlot)
    case dragged(x: Double, y: Double)

    var setting: String {
        switch self {
        case .auto: return "auto"
        case .slot(let slot): return slot.rawValue
        case .dragged(let x, let y): return String(format: "drag:%.4f,%.4f", x, y)
        }
    }

    init(setting: String) {
        if let slot = BCDHUDSlot(rawValue: setting) { self = .slot(slot); return }
        if setting.hasPrefix("drag:") {
            let parts = setting.dropFirst(5).split(separator: ",")
            if parts.count == 2, let x = Double(parts[0]), let y = Double(parts[1]), x.isFinite, y.isFinite {
                self = .dragged(x: min(max(x, 0), 1), y: min(max(y, 0), 1))
                return
            }
        }
        self = .auto
    }

    init(from decoder: Decoder) throws {
        self.init(setting: (try? decoder.singleValueContainer().decode(String.self)) ?? "auto")
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(setting)
    }

    var isAuto: Bool { if case .auto = self { return true } else { return false } }
}

/// A game's HUD: the layout, the Custom list, and where it sits on each kind of screen. Stored in the library entry
/// (LibraryEntry.hud); an entry without one follows the old overlay switch (BCDHUDMigration).
struct BCDHUDSetup: Codable, Equatable {
    var layout = BCDHUDLayout.off
    var custom = BCDHUDCustom.default
    var places: [String: BCDHUDPlace] = [:]

    init(layout: BCDHUDLayout = .off, custom: BCDHUDCustom = .default, places: [String: BCDHUDPlace] = [:]) {
        self.layout = layout
        self.custom = custom
        self.places = places
    }

    enum CodingKeys: String, CodingKey { case layout, custom, places }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        layout = (try? c.decodeIfPresent(String.self, forKey: .layout)).flatMap(BCDHUDLayout.init(rawValue:)) ?? .off
        custom = (try? c.decodeIfPresent(BCDHUDCustom.self, forKey: .custom)) ?? .default
        places = (try? c.decodeIfPresent([String: BCDHUDPlace].self, forKey: .places)) ?? [:]
    }

    func place(_ screen: BCDHUDScreenClass) -> BCDHUDPlace { places[screen.rawValue] ?? .auto }

    mutating func set(_ place: BCDHUDPlace, for screen: BCDHUDScreenClass) {
        if place.isAuto { places[screen.rawValue] = nil } else { places[screen.rawValue] = place }
    }

    /// What a layout needs measured now.
    var needs: BCDHUDNeeds { layout == .custom ? custom.needs.union(.frames) : layout.needs }
}

// MARK: - An old setting carries over

/// The old overlay (LibraryEntry.performance, overlayFields, and where the player had dragged it) becomes a HUD without any change
/// in what a working game shows: an overlay of the frame rate alone is layout 1; any other list becomes a Custom HUD with exactly
/// those readings, in their old order, on the old plate (62% black), where it was. A game that never turned the overlay on, and
/// has no HUD of its own, follows the choice for every game (Settings), which is Off unless the player changes it.
enum BCDHUDMigration {
    static let defaultFields = ["FPS", "Frame time", "RAM", "Battery"]

    /// `stored`: the entry's own HUD. `legacyPlace`: the shares the old overlay was dragged to (nil: never moved).
    static func resolve(stored: BCDHUDSetup?, performance: Bool, fields: [String]?, everyGame: BCDHUDLayout,
                        legacyPlace: (x: Double, y: Double)?) -> BCDHUDSetup {
        if let stored { return stored }
        guard performance else {
            var setup = BCDHUDSetup(layout: everyGame == .custom ? .off : everyGame)
            setup.custom = BCDHUDCustom.startingFrom(setup.layout)
            return setup
        }
        let names = fields ?? defaultFields
        var items: [BCDHUDItem] = []
        for name in names {
            if let item = BCDHUDItem.legacy(name), !items.contains(item) { items.append(item) }
        }
        if items.isEmpty { items = [.fps] }
        if items == [.fps] {
            return BCDHUDSetup(layout: .pulse, custom: BCDHUDCustom.startingFrom(.pulse), places: [:])
        }
        var custom = BCDHUDCustom()
        custom.items = items
        custom.shape = .strip
        custom.size = .medium
        custom.textOpacity = 1
        custom.backgroundOpacity = 0.62
        custom.graphSeconds = 30
        custom.touched = true
        var setup = BCDHUDSetup(layout: .custom, custom: custom, places: [:])
        // The old overlay started at the top centre and kept whatever the player dragged it to, on every screen.
        let at = legacyPlace ?? (0.5, 0)
        for screen in BCDHUDScreenClass.allCases {
            setup.places[screen.rawValue] = .dragged(x: at.x, y: at.y)
        }
        return setup
    }
}

// MARK: - Frame arithmetic

/// One frame: when it was presented (seconds) and how long after the one before (seconds).
struct BCDFrameSample: Equatable {
    var t: Double
    var dt: Double
}

enum BCDFrameMath {
    /// Frames per second over the newest frames: those of the last `window` seconds, or the last two windows when fewer than
    /// three fell in one (a game at 1 fps). Steady at any rate (60, 19, 1), and it falls as the frames stop.
    static func fps(_ frames: [BCDFrameSample], now: Double, window: Double = 1) -> Double {
        guard let newest = frames.last else { return 0 }
        func within(_ w: Double) -> ArraySlice<BCDFrameSample> { frames.trailing { $0.t >= now - w } }
        var recent = within(window)
        if recent.count < 3 { recent = within(window * 2) }
        guard recent.count >= 2, let first = recent.first else { return 0 }
        let span = newest.t - first.t
        guard span > 0 else { return 0 }
        let interval = span / Double(recent.count - 1)
        var rate = Double(recent.count - 1) / span
        // The frames have stopped: the silence since the last one counts once it is longer than a frame and a half.
        let gap = now - newest.t
        if gap > interval * 1.5 { rate = Double(recent.count - 1) / (span + gap - interval) }
        return rate
    }

    /// The average frame time (ms) of the last `window` seconds; nil with fewer than two frames.
    static func averageMs(_ frames: [BCDFrameSample], now: Double, window: Double = 0.5) -> Double? {
        let recent = frames.trailing { $0.t >= now - window }
        guard recent.count >= 2 else { return nil }
        let sum = recent.reduce(0.0) { $0 + $1.dt }
        return sum / Double(recent.count) * 1000
    }

    /// The value that `p` (0 ... 1) of the values are at or below (nearest rank). nil for no values.
    static func percentile(_ values: [Double], _ p: Double) -> Double? {
        guard !values.isEmpty, p.isFinite else { return nil }
        let sorted = values.sorted()
        let rank = Int((min(max(p, 0), 1) * Double(sorted.count)).rounded(.up))
        return sorted[min(max(rank, 1), sorted.count) - 1]
    }

    /// The frame rate of the slowest `percent` of frames (1 or 0.1): 1000 / the frame time that (100 - percent)% of the frames beat.
    /// nil when there are too few frames to say (100 for 1%, 1000 for 0.1%).
    static func low(_ frames: [BCDFrameSample], now: Double, window: Double, percent: Double) -> Double? {
        let recent = frames.trailing { $0.t >= now - window }.map(\.dt)
        let needed = Int((100 / percent).rounded())
        guard recent.count >= needed, let slow = percentile(recent, 1 - percent / 100), slow > 0 else { return nil }
        return 1 / slow
    }

    /// The longest frame (ms) of the last `window` seconds.
    static func worstMs(_ frames: [BCDFrameSample], now: Double, window: Double = 10) -> Double? {
        frames.trailing { $0.t >= now - window }.map(\.dt).max().map { $0 * 1000 }
    }

    /// Frames in the last `window` seconds that took at least twice the median frame time and at least 33 ms.
    static func stutters(_ frames: [BCDFrameSample], now: Double, window: Double = 60) -> Int? {
        let recent = frames.trailing { $0.t >= now - window }.map(\.dt)
        guard recent.count >= 30, let median = percentile(recent, 0.5) else { return nil }
        let line = max(median * 2, 0.033)
        return recent.filter { $0 >= line }.count
    }

    /// The graph: `count` buckets of `bucket` seconds ending now; each holds the slowest frame (ms) that ended in it. A bucket
    /// with no frame after the first one holds the bucket's own length: the stall. Buckets before the first frame hold 0.
    static func graph(_ frames: [BCDFrameSample], now: Double, bucket: Double = 0.25, count: Int) -> [Float] {
        guard count > 0, bucket > 0, let firstFrame = frames.first?.t else { return [] }
        var out = [Float](repeating: 0, count: count)
        let start = now - bucket * Double(count)
        var filled = [Bool](repeating: false, count: count)
        for frame in frames.trailing(while: { $0.t >= start }) {
            let index = min(count - 1, max(0, Int(((frame.t - start) / bucket).rounded(.down))))
            out[index] = max(out[index], Float(frame.dt * 1000))
            filled[index] = true
        }
        for index in 0..<count where !filled[index] {
            let bucketEnd = start + bucket * Double(index + 1)
            if bucketEnd > firstFrame { out[index] = Float(bucket * 1000) }
        }
        return out
    }

    /// A graph of more buckets than the HUD has room for, as pairs' maxima (60 s of quarter seconds is 240 buckets).
    static func squeeze(_ values: [Float], to count: Int) -> [Float] {
        guard count > 0, values.count > count else { return values }
        let step = Double(values.count) / Double(count)
        return (0..<count).map { i in
            let from = Int((Double(i) * step).rounded(.down)), to = max(from + 1, Int((Double(i + 1) * step).rounded(.down)))
            return values[from..<min(to, values.count)].max() ?? 0
        }
    }
}

extension Array {
    /// The elements from the end while `keep` holds, oldest first (frames are in time order).
    func trailing(while keep: (Element) -> Bool) -> ArraySlice<Element> {
        var start = endIndex
        while start > startIndex, keep(self[index(before: start)]) { start = index(before: start) }
        return self[start...]
    }
}

// MARK: - Colour rules

enum BCDHUDTone: Equatable {
    case normal, soft, good, warm, hot
}

enum BCDHUDRules {
    /// The frame rate the game is held to: the limit the player chose (1 is 60, 3 is 30, 4 is 40). The screen's maximum and no limit
    /// are no target.
    static func cap(fpsMode: Int) -> Double? {
        switch fpsMode {
        case 1: return 60
        case 3: return 30
        case 4: return 40
        default: return nil
        }
    }

    /// Green when the game keeps its limit (a locked 30 is green), amber from 60% of it, red below. With no limit to judge by,
    /// green from 50, amber from 30, as the old overlay did; grey before the first frame.
    static func fpsTone(_ fps: Double, cap: Double?) -> BCDHUDTone {
        guard fps > 0 else { return .soft }
        if let cap, cap > 0 {
            let share = fps / cap
            return share >= 0.9 ? .good : share >= 0.6 ? .warm : .hot
        }
        return fps >= 50 ? .good : fps >= 30 ? .warm : .hot
    }

    /// From what is left before iOS ends the app: more than 768 MB is fine, more than 128 is close, less is not.
    static func memoryTone(freeMB: Int?) -> BCDHUDTone {
        guard let freeMB else { return .soft }
        return freeMB > 768 ? .good : freeMB > 128 ? .warm : .hot
    }

    static func heat(_ state: Int) -> (word: String, tone: BCDHUDTone) {
        switch state {
        case 0: return ("Cool", .good)
        case 1: return ("Warm", .warm)
        case 2: return ("Hot", .hot)
        default: return ("Too hot", .hot)
        }
    }

    /// The dot of layouts 1 and 2 (design rule 7): amber or red when the heat is serious or critical, less than 128 MB is left, or the
    /// frame rate has been under 60% of its limit for 3 seconds; nil when all is well. `lowFor`: seconds the rate has been that low.
    static func quietWarning(thermal: Int, freeMB: Int?, lowFor: Double) -> BCDHUDTone? {
        if thermal >= 3 { return .hot }
        if let freeMB, freeMB < 128 { return .hot }
        if thermal == 2 { return .warm }
        if lowFor >= 3 { return .warm }
        return nil
    }
}

// MARK: - The readings

/// Everything the HUD can show, as the live side measured it. A nil is "not measured (yet)": the item then says nothing, or a dash.
struct BCDHUDData: Equatable {
    var fps = 0.0
    var cap: Double?
    var frameMs: Double?
    var low1: Double?
    var low01: Double?
    var worstMs: Double?
    var stutters: Int?
    var limitWords: String?
    var cpuTotal: Double?
    var cpuTop: Double?
    var gpuPercent: Double?
    var gpuMs: Double?
    var gpuMemoryMB: Int?
    var gpuBudgetMB: Int?
    var footprintMB: Int?
    var limitMB: Int?
    var peakMB: Int?
    var compressedMB: Int?
    var swapMB: Int?
    var jitUsedMB: Int?
    var jitTotalMB: Int?
    /// 0 cool, 1 warm, 2 hot, 3 too hot.
    var thermal = 0
    var lowPower = false
    var batteryPercent: Int?
    var api: String?
    var renderSize: String?
    var outputSize: String?
    var upscale: String?
    var hdr: String?
    var hdrTone: BCDHUDTone = .soft
    var frameGen: String?
    var gpuName: String?
    var playSeconds = 0
    var clock: String?
    var build: String?
    /// The graph's buckets (ms), oldest first.
    var graph: [Float] = []
    /// The dot of layouts 1 and 2 (nil: nothing is wrong).
    var warning: BCDHUDTone?

    var freeMB: Int? {
        guard let footprintMB, let limitMB else { return nil }
        return limitMB - footprintMB
    }
}

struct BCDHUDSegment: Equatable {
    var text: String
    var tone: BCDHUDTone = .normal
}

struct BCDHUDGraph: Equatable {
    var values: [Float]
    var tone: BCDHUDTone
    /// The top of the graph in ms; the guides are at 16.7 and 33.3.
    var ceiling: Float = 40
    var width: CGFloat
    var height: CGFloat
}

enum BCDHUDRow: Equatable {
    /// Readings side by side.
    case line([BCDHUDSegment])
    case graph(BCDHUDGraph)
    /// Layout 1: a bold number with a small caption, and the quiet dot.
    case pulse(number: BCDHUDSegment, caption: String, dot: BCDHUDTone?)
}

/// What the view draws: the rows and the look. Equatable, so the view is told only when something changed.
struct BCDHUDPlan: Equatable {
    var layout: BCDHUDLayout
    var rows: [BCDHUDRow]
    var separator = "  |  "
    /// 2: the table shape pairs the readings of each row.
    var columns = 1
    var textSize: CGFloat = 12
    var textOpacity = 1.0
    var background = 0.55
    var capsule = false
    var outlineText = false
    /// The plate's widest, in points.
    var maxWidth: CGFloat = 360
    /// What a screen reader says.
    var spoken = ""
}

enum BCDHUDRows {
    /// The plan for a layout from the readings; nil for Off. `width`: the room the plate may use, in points.
    static func plan(layout: BCDHUDLayout, custom: BCDHUDCustom, data: BCDHUDData, width: CGFloat, roomy: Bool = false) -> BCDHUDPlan? {
        guard var made = unfitted(layout: layout, custom: custom, data: data, width: width, roomy: roomy) else { return nil }
        // Whatever the window: no plate wider than the room there is, and a graph that fits inside it.
        let room = max(120, width)
        made.maxWidth = min(made.maxWidth, room)
        made.rows = made.rows.map { row in
            guard case .graph(var graph) = row else { return row }
            graph.width = min(graph.width, max(60, made.maxWidth - 18))
            return .graph(graph)
        }
        return made
    }

    private static func unfitted(layout: BCDHUDLayout, custom: BCDHUDCustom, data: BCDHUDData, width: CGFloat, roomy: Bool) -> BCDHUDPlan? {
        switch layout {
        case .off: return nil
        case .pulse: return pulse(data)
        case .strip: return strip(data, width: width)
        case .tune: return tune(data)
        case .report: return report(data, roomy: roomy)
        case .custom: return customPlan(custom, data, width: width)
        }
    }

    // MARK: Layout 1 - Pulse

    private static func pulse(_ d: BCDHUDData) -> BCDHUDPlan {
        let number = BCDHUDSegment(text: d.fps > 0 ? String(format: "%.0f", d.fps) : "–", tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap))
        var plan = BCDHUDPlan(layout: .pulse, rows: [.pulse(number: number, caption: "fps", dot: d.warning)])
        plan.background = 0.45
        plan.capsule = true
        plan.maxWidth = 120
        plan.spoken = d.fps > 0 ? String(format: "%.0f frames a second", d.fps) : "No frames yet"
        return plan
    }

    // MARK: Layout 2 - Strip

    private static func strip(_ d: BCDHUDData, width: CGFloat) -> BCDHUDPlan {
        var parts = [BCDHUDSegment(text: d.fps > 0 ? String(format: "%.0f FPS", d.fps) : "– FPS", tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap))]
        parts.append(BCDHUDSegment(text: d.frameMs.map { String(format: "%.1f ms", $0) } ?? "– ms"))
        if let used = d.footprintMB, let limit = d.limitMB {
            parts.append(BCDHUDSegment(text: gigabytes(used) + "/" + gigabytes(limit) + " GB", tone: BCDHUDRules.memoryTone(freeMB: d.freeMB)))
        }
        let heat = BCDHUDRules.heat(d.thermal)
        parts.append(BCDHUDSegment(text: heat.word, tone: heat.tone))
        if let dot = d.warning { parts.append(BCDHUDSegment(text: "●", tone: dot)) }
        var plan = BCDHUDPlan(layout: .strip, rows: [.line(parts)])
        plan.separator = "  |  "
        plan.maxWidth = max(160, width)
        plan.spoken = parts.map(\.text).joined(separator: ", ")
        return plan
    }

    // MARK: Layout 3 - Tune

    private static func tune(_ d: BCDHUDData) -> BCDHUDPlan {
        let fps = BCDHUDSegment(text: d.fps > 0 ? String(format: "%.0f FPS", d.fps) : "– FPS", tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap))
        var first = [fps]
        if let low = d.low1 { first.append(BCDHUDSegment(text: String(format: "1%% %.0f", low))) }
        first.append(BCDHUDSegment(text: d.frameMs.map { String(format: "%.1f ms", $0) } ?? "– ms", tone: .soft))
        var second: [BCDHUDSegment] = []
        second.append(BCDHUDSegment(text: d.cpuTotal.map { String(format: "CPU %.0f%%", $0) } ?? "CPU –"))
        second.append(BCDHUDSegment(text: d.gpuPercent.map { String(format: "GPU %.0f%%", $0) } ?? "GPU –"))
        var third: [BCDHUDSegment] = []
        if let used = d.footprintMB, let limit = d.limitMB {
            third.append(BCDHUDSegment(text: gigabytes(used) + "/" + gigabytes(limit) + " GB", tone: BCDHUDRules.memoryTone(freeMB: d.freeMB)))
        }
        let heat = BCDHUDRules.heat(d.thermal)
        third.append(BCDHUDSegment(text: heat.word, tone: heat.tone))
        var rows: [BCDHUDRow] = [.line(first), .line(second), .line(third)]
        rows.append(.graph(BCDHUDGraph(values: BCDFrameMath.squeeze(d.graph, to: 60), tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap),
                                       width: 150, height: 28)))
        var plan = BCDHUDPlan(layout: .tune, rows: rows)
        plan.separator = "  "
        plan.maxWidth = 190
        plan.spoken = (first + second + third).map(\.text).joined(separator: ", ")
        return plan
    }

    // MARK: Layout 4 - Report

    private static func report(_ d: BCDHUDData, roomy: Bool) -> BCDHUDPlan {
        var rows: [BCDHUDRow] = []
        // 1. The pipeline: API, sizes, upscaler, limit.
        var pipeline: [String] = []
        if let api = d.api { pipeline.append(api) }
        if let render = d.renderSize {
            var size = render
            if let output = d.outputSize, output != render { size += " > " + (d.upscale ?? output) } else if let upscale = d.upscale { size += " > " + upscale }
            pipeline.append(size)
        }
        if let gen = d.frameGen { pipeline.append(gen) }
        if let cap = d.cap { pipeline.append("cap " + String(format: "%.0f", cap)) }
        if !pipeline.isEmpty { rows.append(.line([BCDHUDSegment(text: pipeline.joined(separator: "  "), tone: .soft)])) }
        if let hdr = d.hdr { rows.append(.line([BCDHUDSegment(text: hdr, tone: d.hdrTone)])) }
        // 2. The speed.
        var speed = [BCDHUDSegment(text: d.fps > 0 ? String(format: "%.0f FPS", d.fps) : "– FPS", tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap))]
        if let ms = d.frameMs { speed.append(BCDHUDSegment(text: String(format: "avg %.1f", ms))) }
        if let low = d.low1 { speed.append(BCDHUDSegment(text: String(format: "1%% %.0f", low))) }
        if let low = d.low01 { speed.append(BCDHUDSegment(text: String(format: "0.1%% %.0f", low))) }
        rows.append(.line(speed))
        // 3. The graph.
        rows.append(.graph(BCDHUDGraph(values: BCDFrameMath.squeeze(d.graph, to: 120), tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap),
                                       width: 200, height: 30)))
        // 4. Load.
        var load = [BCDHUDSegment(text: d.cpuTotal.map { cpu in
            d.cpuTop.map { String(format: "CPU %.0f%% (top %.0f%%)", cpu, $0) } ?? String(format: "CPU %.0f%%", cpu)
        } ?? "CPU –")]
        load.append(BCDHUDSegment(text: d.gpuPercent.map { gpu in
            (d.gpuMs ?? 0) > 0 ? String(format: "GPU %.0f%% %.1f ms", gpu, d.gpuMs ?? 0) : String(format: "GPU %.0f%%", gpu)
        } ?? "GPU –"))
        rows.append(.line(load))
        // 5. Memory.
        var memory: [BCDHUDSegment] = []
        if let used = d.footprintMB, let limit = d.limitMB {
            memory.append(BCDHUDSegment(text: "Mem " + gigabytes(used) + "/" + gigabytes(limit) + " GB", tone: BCDHUDRules.memoryTone(freeMB: d.freeMB)))
        }
        if let peak = d.peakMB { memory.append(BCDHUDSegment(text: "peak " + gigabytes(peak))) }
        if let swap = d.swapMB { memory.append(BCDHUDSegment(text: "swap " + gigabytes(swap))) }
        if !memory.isEmpty { rows.append(.line(memory)) }
        // 6. JIT, heat, power, battery.
        var system: [BCDHUDSegment] = []
        if let used = d.jitUsedMB, let total = d.jitTotalMB, total > 0 { system.append(BCDHUDSegment(text: "JIT \(used)/\(total)")) }
        let heat = BCDHUDRules.heat(d.thermal)
        system.append(BCDHUDSegment(text: heat.word, tone: heat.tone))
        system.append(BCDHUDSegment(text: d.lowPower ? "LPM on" : "LPM off", tone: d.lowPower ? .warm : .soft))
        if let battery = d.batteryPercent { system.append(BCDHUDSegment(text: "\(battery)%", tone: battery < 20 ? .warm : .normal)) }
        rows.append(.line(system))
        // 7. Time and build.
        var tail = [BCDHUDSegment(text: clockTime(seconds: d.playSeconds), tone: .soft)]
        if let build = d.build { tail.append(BCDHUDSegment(text: "build " + build, tone: .soft)) }
        rows.append(.line(tail))
        var plan = BCDHUDPlan(layout: .report, rows: rows)
        plan.separator = "  "
        plan.textSize = 11
        plan.maxWidth = roomy ? 270 : 240
        plan.spoken = rows.compactMap { row -> String? in
            if case .line(let parts) = row { return parts.map(\.text).joined(separator: ", ") }
            return nil
        }.joined(separator: ". ")
        return plan
    }

    // MARK: Custom

    private static func customPlan(_ c: BCDHUDCustom, _ d: BCDHUDData, width: CGFloat) -> BCDHUDPlan {
        let textSize = c.size.points
        var plan = BCDHUDPlan(layout: .custom, rows: [])
        switch c.shape {
        case .strip: plan.separator = "  ·  "
        case .list: plan.separator = "  "
        case .table:
            plan.columns = 2
            plan.separator = "   "
        }
        // The old plate's packing for a strip: readings in lines of about as many characters as fit.
        let characters = max(12, Int((width - 22) / (textSize * 0.62)))
        func pack(_ segments: [BCDHUDSegment]) -> [BCDHUDRow] {
            switch c.shape {
            case .list:
                return segments.map { .line([$0]) }
            case .table:
                return stride(from: 0, to: segments.count, by: 2).map { .line(Array(segments[$0..<min($0 + 2, segments.count)])) }
            case .strip:
                var out: [BCDHUDRow] = [], line: [BCDHUDSegment] = [], used = 0
                for segment in segments {
                    let cost = segment.text.count + (line.isEmpty ? 0 : 3)
                    if !line.isEmpty && used + cost > characters { out.append(.line(line)); line = []; used = 0 }
                    used += line.isEmpty ? segment.text.count : cost
                    line.append(segment)
                }
                if !line.isEmpty { out.append(.line(line)) }
                return out
            }
        }
        // Readings in the order of the list; a graph stands between the readings before it and the ones after.
        var rows: [BCDHUDRow] = [], pending: [BCDHUDSegment] = [], spoken: [String] = []
        for item in c.items {
            if item == .frameGraph {
                rows += pack(pending)
                pending = []
                let values = BCDFrameMath.squeeze(Array(d.graph.suffix(c.graphSeconds * 4)), to: 120)
                rows.append(.graph(BCDHUDGraph(values: values, tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap),
                                               width: 120 * c.size.graphScale, height: 28 * c.size.graphScale)))
            } else if let segment = segment(item, d) {
                pending.append(segment)
                spoken.append(segment.text)
            }
        }
        rows += pack(pending)
        plan.rows = rows
        plan.textSize = textSize
        plan.textOpacity = c.textOpacity
        plan.background = c.backgroundOpacity
        plan.outlineText = c.backgroundOpacity < 0.25
        plan.maxWidth = max(100, width)
        plan.spoken = spoken.joined(separator: ", ")
        return plan
    }

    /// One item as the old overlay wrote it (60 FPS, 16.7 ms, CPU 41% (top 78%), GPU 63% (9.8 ms), 2104 MB, 78%, Cool), or nil when there is
    /// nothing to say yet.
    static func segment(_ item: BCDHUDItem, _ d: BCDHUDData) -> BCDHUDSegment? {
        switch item {
        case .fps:
            return BCDHUDSegment(text: String(format: "%.0f FPS", d.fps), tone: BCDHUDRules.fpsTone(d.fps, cap: d.cap))
        case .fpsCap:
            guard let cap = d.cap else { return nil }
            return BCDHUDSegment(text: String(format: "%.0f/%.0f", d.fps, cap), tone: BCDHUDRules.fpsTone(d.fps, cap: cap))
        case .frameMs:
            return BCDHUDSegment(text: d.frameMs.map { String(format: "%.1f ms", $0) } ?? "– ms")
        case .frameGraph:
            return nil
        case .low1:
            return BCDHUDSegment(text: d.low1.map { String(format: "1%% %.0f", $0) } ?? "1% –", tone: d.low1 == nil ? .soft : .normal)
        case .low01:
            return BCDHUDSegment(text: d.low01.map { String(format: "0.1%% %.0f", $0) } ?? "0.1% –", tone: d.low01 == nil ? .soft : .normal)
        case .worstFrame:
            return BCDHUDSegment(text: d.worstMs.map { String(format: "worst %.0f ms", $0) } ?? "worst –", tone: d.worstMs == nil ? .soft : .normal)
        case .stutters:
            return BCDHUDSegment(text: d.stutters.map { "\($0) stutter" + ($0 == 1 ? "" : "s") } ?? "stutters –", tone: d.stutters == nil ? .soft : .normal)
        case .limit:
            return d.limitWords.map { BCDHUDSegment(text: $0, tone: .soft) }
        case .cpu:
            return BCDHUDSegment(text: d.cpuTotal.map { total in
                d.cpuTop.map { String(format: "CPU %.0f%% (top %.0f%%)", total, $0) } ?? String(format: "CPU %.0f%%", total)
            } ?? "CPU –")
        case .gpu:
            return BCDHUDSegment(text: d.gpuPercent.map { gpu in
                (d.gpuMs ?? 0) > 0 ? String(format: "GPU %.0f%% (%.1f ms)", gpu, d.gpuMs ?? 0) : String(format: "GPU %.0f%%", gpu)
            } ?? "GPU –")
        case .gpuMemory:
            guard let used = d.gpuMemoryMB else { return nil }
            let budget = d.gpuBudgetMB.map { "/" + gigabytes($0) } ?? ""
            return BCDHUDSegment(text: "GPU mem " + (d.gpuBudgetMB == nil ? "\(used) MB" : gigabytes(used) + budget + " GB"))
        case .memUsed:
            return BCDHUDSegment(text: "\(d.footprintMB ?? 0) MB")
        case .memory:
            guard let used = d.footprintMB, let limit = d.limitMB else { return nil }
            return BCDHUDSegment(text: gigabytes(used) + "/" + gigabytes(limit) + " GB", tone: BCDHUDRules.memoryTone(freeMB: d.freeMB))
        case .memFree:
            guard let free = d.freeMB else { return nil }
            return BCDHUDSegment(text: "free " + gigabytes(max(0, free)) + " GB", tone: BCDHUDRules.memoryTone(freeMB: free))
        case .memPeak:
            return d.peakMB.map { BCDHUDSegment(text: "peak " + gigabytes($0) + " GB") }
        case .compressed:
            return d.compressedMB.map { BCDHUDSegment(text: "squeezed " + gigabytes($0) + " GB", tone: .soft) }
        case .swap:
            return d.swapMB.map { BCDHUDSegment(text: "swap " + gigabytes($0) + " GB", tone: $0 > 0 ? .normal : .soft) }
        case .jit:
            guard let used = d.jitUsedMB, let total = d.jitTotalMB, total > 0 else { return nil }
            return BCDHUDSegment(text: "JIT \(used)/\(total) MB")
        case .thermal:
            let heat = BCDHUDRules.heat(d.thermal)
            return BCDHUDSegment(text: heat.word, tone: heat.tone)
        case .lowPower:
            return BCDHUDSegment(text: d.lowPower ? "LPM on" : "LPM off", tone: d.lowPower ? .warm : .soft)
        case .battery:
            guard let level = d.batteryPercent else { return nil }
            return BCDHUDSegment(text: "\(level)%", tone: level < 20 ? .warm : .normal)
        case .api:
            return d.api.map { BCDHUDSegment(text: $0, tone: .soft) }
        case .resolution:
            guard let render = d.renderSize else { return nil }
            if let output = d.outputSize, output != render { return BCDHUDSegment(text: render + " > " + output, tone: .soft) }
            return BCDHUDSegment(text: render, tone: .soft)
        case .metalFX:
            return d.upscale.map { BCDHUDSegment(text: $0, tone: .soft) }
        case .hdr:
            return d.hdr.map { BCDHUDSegment(text: $0, tone: d.hdrTone) }
        case .frameGen:
            return d.frameGen.map { BCDHUDSegment(text: $0, tone: .soft) }
        case .gpuName:
            return d.gpuName.map { BCDHUDSegment(text: $0, tone: .soft) }
        case .playTime:
            return BCDHUDSegment(text: clockTime(seconds: d.playSeconds), tone: .soft)
        case .clock:
            return d.clock.map { BCDHUDSegment(text: $0, tone: .soft) }
        }
    }

    /// "3.1" for 3174 MB. One decimal, a thousand MB to the GB as the phone's own memory figures say.
    static func gigabytes(_ megabytes: Int) -> String {
        String(format: "%.1f", Double(megabytes) / 1000)
    }

    /// "42:10" or "1:42:10".
    static func clockTime(seconds: Int) -> String {
        let s = max(0, seconds)
        let h = s / 3600, m = s % 3600 / 60, r = s % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, r) : String(format: "%02d:%02d", m, r)
    }
}

// MARK: - Where it goes on the screen

struct BCDHUDInsets: Equatable {
    var top: CGFloat = 0, leading: CGFloat = 0, bottom: CGFloat = 0, trailing: CGFloat = 0
}

enum BCDHUDPlacement {
    /// A window with room to spare: its short side is 600 points or more (a big iPad window, the inner screen of a folding phone).
    /// Sizes only: the same rule for every device.
    static func roomy(_ viewport: CGSize) -> Bool { min(viewport.width, viewport.height) >= 600 }

    /// A margin from an edge: 8 pt from a safe-area edge, 20 pt from a screen edge with no inset (the display's rounded corners).
    static func margin(inset: CGFloat) -> CGFloat { inset >= 12 ? 8 : 20 }

    /// The HUD's center when the player dragged it: the shares of the screen, kept inside the safe area (what the old overlay did).
    static func center(dragged x: Double, y: Double, size: CGSize, viewport: CGSize, insets: BCDHUDInsets) -> CGPoint {
        clamp(center: CGPoint(x: viewport.width * x, y: viewport.height * y), size: size, viewport: viewport, insets: insets)
    }

    /// A center moved inside the safe area, 8 pt from its edges.
    static func clamp(center: CGPoint, size: CGSize, viewport: CGSize, insets: BCDHUDInsets) -> CGPoint {
        let left = insets.leading + size.width / 2 + 8
        let top = insets.top + 8 + size.height / 2
        let right = max(left, viewport.width - insets.trailing - size.width / 2 - 8)
        let bottom = max(top, viewport.height - insets.bottom - size.height / 2 - 8)
        return CGPoint(x: min(max(left, center.x), right), y: min(max(top, center.y), bottom))
    }

    /// The top-left of the HUD in a slot, inside the safe area.
    static func origin(slot: BCDHUDSlot, size: CGSize, viewport: CGSize, insets: BCDHUDInsets) -> CGPoint {
        let left = insets.leading + margin(inset: insets.leading)
        let right = viewport.width - insets.trailing - margin(inset: insets.trailing) - size.width
        let top = insets.top + margin(inset: insets.top)
        let bottom = viewport.height - insets.bottom - margin(inset: insets.bottom) - size.height
        let middle = (viewport.width - size.width) / 2
        let x: CGFloat, y: CGFloat
        switch slot {
        case .topLeading, .bottomLeading: x = left
        case .topTrailing, .bottomTrailing: x = max(left, right)
        case .top, .bottom: x = min(max(left, middle), max(left, right))
        }
        switch slot {
        case .topLeading, .top, .topTrailing: y = top
        case .bottomLeading, .bottom, .bottomTrailing: y = max(top, bottom)
        }
        return CGPoint(x: x, y: y)
    }

    /// Where to try, best first. A phone held upright is narrow, and the Dynamic Island is in the top inset, so the first choice is
    /// the trailing top corner under the island's strip.
    static func preference(layout: BCDHUDLayout, custom: BCDHUDCustom, landscape: Bool, roomy: Bool) -> [BCDHUDSlot] {
        switch layout {
        case .pulse:
            if roomy { return [.topTrailing, .top, .topLeading, .bottomTrailing, .bottomLeading] }
            return landscape ? [.top, .topLeading, .topTrailing, .bottomTrailing, .bottomLeading]
                             : [.topTrailing, .topLeading, .top, .bottomTrailing, .bottomLeading]
        case .strip:
            return [.top, .topLeading, .topTrailing, .bottom, .bottomLeading, .bottomTrailing]
        case .tune, .report:
            return [.topLeading, .topTrailing, .bottomLeading, .bottomTrailing, .top, .bottom]
        case .custom:
            if custom.shape == .strip { return [.top, .topLeading, .topTrailing, .bottom, .bottomLeading, .bottomTrailing] }
            return [.topLeading, .topTrailing, .bottomLeading, .bottomTrailing, .top, .bottom]
        case .off:
            return []
        }
    }

    /// Where an automatic placement settled: a slot (the HUD is anchored there, so its own size may change without moving it) or a
    /// bar of the picture's letterbox (the top-left of the room it was given).
    enum Spot: Equatable {
        case slot(BCDHUDSlot)
        case at(CGPoint)
    }

    /// The spot to use when nothing was chosen: a bar the picture leaves empty, if one is big enough for `size`, else the first slot
    /// of the layout's list that covers none of `avoid` (the touch controls, the menu button); else the slot that covers the least.
    static func autoSpot(layout: BCDHUDLayout, custom: BCDHUDCustom, size: CGSize, viewport: CGSize, insets: BCDHUDInsets,
                         avoid: [CGRect], bars: [CGRect]) -> Spot {
        let landscape = viewport.width > viewport.height
        // The room a HUD may use: inside the safe area, and as far from a bare screen edge as a slot is (the display's rounded corners).
        let left = insets.leading + margin(inset: insets.leading), right = viewport.width - insets.trailing - margin(inset: insets.trailing)
        let top = insets.top + margin(inset: insets.top), bottom = viewport.height - insets.bottom - margin(inset: insets.bottom)
        let inner = CGRect(x: left, y: top, width: max(0, right - left), height: max(0, bottom - top))
        // A bar of the picture's letterbox or pillarbox that the HUD fits in: it covers nothing of the game, and keeps 6 pt from the picture.
        for bar in bars {
            let room = bar.insetBy(dx: 6, dy: 6).intersection(inner)
            if !room.isNull, room.width >= size.width, room.height >= size.height {
                let at = CGRect(x: room.minX, y: room.minY, width: size.width, height: size.height)
                if !avoid.contains(where: { $0.intersects(at) }) { return .at(at.origin) }
            }
        }
        var best: (slot: BCDHUDSlot, overlap: CGFloat)?
        for slot in preference(layout: layout, custom: custom, landscape: landscape, roomy: roomy(viewport)) {
            let frame = CGRect(origin: origin(slot: slot, size: size, viewport: viewport, insets: insets), size: size)
            let overlap = avoid.reduce(CGFloat(0)) { sum, rect in
                let hit = rect.intersection(frame)
                return hit.isNull ? sum : sum + hit.width * hit.height
            }
            if overlap == 0 { return .slot(slot) }
            if best == nil || overlap < best!.overlap { best = (slot, overlap) }
        }
        return .slot(best?.slot ?? .topLeading)
    }

    /// The top-left of the HUD for an automatic placement, with the HUD's size as it is now.
    static func auto(layout: BCDHUDLayout, custom: BCDHUDCustom, size: CGSize, viewport: CGSize, insets: BCDHUDInsets,
                     avoid: [CGRect], bars: [CGRect]) -> CGPoint {
        switch autoSpot(layout: layout, custom: custom, size: size, viewport: viewport, insets: insets, avoid: avoid, bars: bars) {
        case .slot(let slot): return origin(slot: slot, size: size, viewport: viewport, insets: insets)
        case .at(let point): return point
        }
    }

    /// The bars the picture leaves on a screen: the strips between the screen's safe area and `picture`.
    static func bars(picture: CGRect, viewport: CGSize) -> [CGRect] {
        guard picture.width > 0, picture.height > 0 else { return [] }
        let screen = CGRect(origin: .zero, size: viewport)
        var out: [CGRect] = []
        if picture.minX > 1 { out.append(CGRect(x: 0, y: 0, width: picture.minX, height: viewport.height)) }
        if screen.maxX - picture.maxX > 1 { out.append(CGRect(x: picture.maxX, y: 0, width: screen.maxX - picture.maxX, height: viewport.height)) }
        if picture.minY > 1 { out.append(CGRect(x: 0, y: 0, width: viewport.width, height: picture.minY)) }
        if screen.maxY - picture.maxY > 1 { out.append(CGRect(x: 0, y: picture.maxY, width: viewport.width, height: screen.maxY - picture.maxY)) }
        return out
    }
}
