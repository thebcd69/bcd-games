// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation
import Metal
import UIKit
import os

// The performance HUD on the engine (design: BCDHUDCore.swift; views: BCDHUDView.swift; rows of the menu: BCDHUDMenu.swift). The view
// layer, the menu and the preview app know nothing of the engine; this file reads it:
//
//  - BCDHUDState is the player's choice for the session that is running: the layout, the Custom list and where it sits. It is resolved
//    from the library entry when a session starts (an entry that never had a HUD keeps what its old overlay switch said), changed by
//    the in-game menu, and written back with the entry (LibraryEntry.hud) only when the player changed something.
//  - BCDHUDLive is the sampler. While a layout is on it wakes four times a second (two when there is no graph), takes the new presents
//    out of the frame-time ring (BCDFrameRing.c), reads only the counters the layout shows (BCDHUDNeeds) and gives the view a new plan
//    when the plan changed. Off, it does not exist: nothing is scheduled, nothing is read, and the GPU meter stays off.
//
// Cost: the ring is written by the present path whether or not a HUD is on (one clock read and two atomics per frame); the sampler's
// own work is a few system calls and some arithmetic on at most a few hundred frame times, on the main thread, a few times a second.
// The graph and the lows are the dearest part and exist only in layouts 3 and 4 and in a Custom list that has them.

// MARK: - The choice

/// The HUD the running session takes, and the player's changes to it.
final class BCDHUDState: ObservableObject {
    static let shared = BCDHUDState()

    @Published private(set) var setup = BCDHUDSetup()
    /// The player is moving the HUD by hand: it takes touches and shows Auto and Done.
    @Published private(set) var moving = false
    /// The player changed the HUD this session: only then is it written to the library entry.
    private(set) var dirty = false

    /// Settings › Graphics › Performance HUD: the layout every game without a HUD of its own takes. Off unless the player says otherwise;
    /// Custom is not offered here (a Custom list belongs to a game).
    static let everyGameKey = "madeira.bcd.hud.everygame"
    static var everyGame: BCDHUDLayout {
        get {
            guard let word = UserDefaults.standard.string(forKey: everyGameKey), let layout = BCDHUDLayout(rawValue: word), layout != .custom else { return .off }
            return layout
        }
        set { UserDefaults.standard.set(newValue == .off || newValue == .custom ? nil : newValue.rawValue, forKey: everyGameKey) }
    }

    /// Where the old overlay had been dragged to (BCDDraggable kept it as shares of the screen under these keys); nil if it never was.
    static func oldOverlayPlace() -> (x: Double, y: Double)? {
        let defaults = UserDefaults.standard
        guard let x = defaults.object(forKey: "bcdPerformanceOverlayX") as? Double, let y = defaults.object(forKey: "bcdPerformanceOverlayY") as? Double else { return nil }
        return (x, y)
    }

    /// The HUD an entry takes: its own, else what its old overlay switch said, else the choice for every game.
    static func setup(for entry: LibraryEntry) -> BCDHUDSetup {
        BCDHUDMigration.resolve(stored: entry.hud, performance: entry.performance, fields: entry.overlayFields,
                                everyGame: everyGame, legacyPlace: oldOverlayPlace())
    }

    /// The game's page chose a layout (outside a session): the entry keeps it, and its old overlay switch agrees.
    static func apply(_ layout: BCDHUDLayout, to entry: inout LibraryEntry) {
        entry.hud = BCDHUDMenu.choose(BCDHUDMenu.layoutRow, layout.rawValue, setup(for: entry), screen: .landscape).setup
        entry.performance = layout.shown
    }

    /// A library session starts (LibraryModel.begin).
    func sessionBegan(_ entry: LibraryEntry) {
        dirty = false
        moving = false
        setup = Self.setup(for: entry)
        guard setup.layout.shown else { return }
        let source = entry.hud != nil ? "the game's own setting" : entry.performance ? "carried over from the old overlay switch" : "the choice for every game"
        let detail = setup.layout == .custom ? ": " + setup.custom.items.map(\.rawValue).joined(separator: ",") : ""
        LogStore.shared.log("[hud] layout \(setup.layout.rawValue) (\(source))\(detail)")
    }

    /// The player picked something in the menu.
    func change(_ next: BCDHUDSetup, why: String) {
        guard next != setup else { return }
        let layoutChanged = next.layout != setup.layout
        setup = next
        dirty = true
        LibraryModel.shared.performance = next.layout.shown   // the legacy switch follows, so an older reading of the entry agrees
        if layoutChanged { LogStore.shared.log("[hud] layout \(next.layout.rawValue) (\(why))") }
    }

    func beginMoving() {
        moving = true
        dirty = true
    }

    /// The HUD was dropped (or handed back to BCD's own placement) on a kind of screen.
    func drop(_ place: BCDHUDPlace, on screen: BCDHUDScreenClass) {
        var next = setup
        next.set(place, for: screen)
        guard next != setup else { return }
        setup = next
        dirty = true
    }

    func endMoving() {
        moving = false
        LibraryModel.shared.saveCurrentProfile()
    }

    /// LibraryModel.saveCurrentProfile: the entry keeps the HUD once the player has touched it (or it already had one).
    func save(into entry: inout LibraryEntry) {
        if dirty || entry.hud != nil { entry.hud = setup }
    }

    /// The session ended.
    func sessionEnded() {
        moving = false
    }
}

// MARK: - The sampler

@MainActor final class BCDHUDLive {
    let feed = BCDHUDFeed()
    private let state = BCDHUDState.shared
    private let model = LibraryModel.shared

    private var timer: Timer?
    private var interval = 0.5
    private var setup = BCDHUDSetup()
    private var needs: BCDHUDNeeds = []
    private var reader = bcd_frame_reader_start()
    private var buffer = [UInt64](repeating: 0, count: 512)
    private var frames: [BCDFrameSample] = []
    private var lastFrame: Double?
    private var tick = 0

    private let cpuMeter = CPUMeter()
    private var cpu: (total: Double, top: Double)?
    private var gpuOn = false
    private var gpuBusy = 0.0, gpuBuffers: UInt64 = 0, gpuTime = 0.0, gpuCount: UInt64 = 0
    private var gpuPercent: Double?
    private var gpuFrameMs = 0.0
    private var presentCount: UInt64 = 0
    private var batteryOn = false
    private var metal: MTLDevice?

    private var lowSince: Double?
    private var calmKey = 0
    private var calmSince = 0.0
    private var lastLogged = 0.0
    private var lastPlan: BCDHUDPlan?
    private var lastPicture = CGRect.zero
    private var lowsAt = 0.0
    private var lows: (low1: Double?, low01: Double?, worst: Double?, stutters: Int?) = (nil, nil, nil, nil)
    private var lastClear: [Int] = []
    private var pipeline = Pipeline()
    private var hdrReading = BCDHDRReading()

    private struct Pipeline {
        var api: String?, renderSize: String?, outputSize: String?, upscale: String?, frameGen: String?, gpuName: String?
    }

    init() {
        feed.avoid = { [weak self] viewport in self?.avoidRects(viewport) ?? [] }
        feed.picture = { MetalHostView.shared.window == nil ? .zero : MetalHostView.shared.frame }
        feed.moved = { [weak self] place, screen in self?.state.drop(place, on: screen) }
        feed.finishedMoving = { [weak self] in self?.state.endMoving() }
        feed.relayout = { [weak self] in self?.render(now: bcd_frame_ticks_to_seconds(bcd_frame_now_ticks()), force: true) }
    }

    // MARK: Start and stop

    func start() {
        guard timer == nil else { return }
        reader = bcd_frame_reader_start()
        frames.removeAll()
        lastFrame = nil
        pipeline = Self.pipeline(for: model.activeEntry)
        configure()
        sample()
        schedule()
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        BCDHDRLive.measure(false)
        if gpuOn { madeira_gpu_meter_enable(0); gpuOn = false }
        if batteryOn { UIDevice.current.isBatteryMonitoringEnabled = false; batteryOn = false }
        frames.removeAll()
        feed.plan = nil
        feed.editing = false
        lastPlan = nil
    }

    /// The setup changed (the menu): what is measured and drawn follows at once.
    func setupChanged() {
        guard timer != nil else { return }
        let before = interval
        configure()
        if interval != before { schedule() }
        sample()
    }

    private func schedule() {
        timer?.invalidate()
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.sample() } }
        timer.tolerance = interval * 0.2   // the system may fold it into another wake-up
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func configure() {
        setup = state.setup
        needs = setup.needs
        BCDHDRLive.measure(needs.contains(.hdr))
        interval = needs.contains(.graph) ? 0.25 : 0.5
        feed.places = setup.places
        feed.shapeKey = "\(setup.layout.rawValue)\(setup.layout == .custom ? setup.custom.shape.rawValue : "")"
        feed.editing = state.moving
        let wantGPU = needs.contains(.gpu)
        if wantGPU != gpuOn {
            gpuOn = wantGPU
            madeira_gpu_meter_enable(wantGPU ? 1 : 0)
            gpuBusy = madeira_gpu_meter_busy_seconds(); gpuBuffers = madeira_gpu_meter_cmdbufs()
            gpuTime = CACurrentMediaTime(); gpuCount = madeira_get_present_count(); gpuPercent = nil
        }
        let wantBattery = needs.contains(.battery)
        if wantBattery != batteryOn {
            batteryOn = wantBattery
            UIDevice.current.isBatteryMonitoringEnabled = wantBattery
        }
    }

    // MARK: A reading

    private var retain: Double {
        var seconds = 3.0
        if needs.contains(.lows) { seconds = 61 }
        if needs.contains(.graph) { seconds = max(seconds, Double(setup.layout == .custom ? setup.custom.graphSeconds : setup.layout == .tune ? 15 : 30) + 1) }
        return seconds
    }

    private func sample() {
        let now = bcd_frame_ticks_to_seconds(bcd_frame_now_ticks())
        // Moving the HUD is the menu's doing; it can end by itself when the session ends.
        if feed.editing != state.moving { feed.editing = state.moving }
        // The game's picture finds a new place when the window changes (a fold, an unfold, a resized window, a turn): the layer is told, so
        // the HUD moves clear of it without anything restarting.
        let picture = feed.picture()
        let controls = TouchControlsModel.shared
        let clear = [controls.visible ? controls.controls.count : -1, Int(model.menuButtonRect.minX), Int(model.menuButtonRect.minY)]
        if picture != lastPicture || clear != lastClear { lastPicture = picture; lastClear = clear; feed.layoutRevision &+= 1 }
        drain()
        let cutoff = now - retain
        if let first = frames.firstIndex(where: { $0.t >= cutoff }), first > 0 { frames.removeSubrange(0..<first) }
        else if frames.last.map({ $0.t < cutoff }) == true { frames.removeAll() }
        render(now: now, force: false)
    }

    private func drain() {
        var lost: UInt64 = 0
        repeat {
            let count = Int(bcd_frame_ring_drain(&reader, &buffer, UInt32(buffer.count), &lost))
            for i in 0..<count {
                let t = bcd_frame_ticks_to_seconds(buffer[i])
                if let previous = lastFrame, t > previous { frames.append(BCDFrameSample(t: t, dt: t - previous)) }
                lastFrame = t
            }
            if count < buffer.count { break }
        } while true
    }

    private func render(now: Double, force: Bool) {
        guard setup.layout.shown else { return }
        tick += 1
        var d = BCDHUDData()
        d.fps = BCDFrameMath.fps(frames, now: now)
        d.cap = BCDHUDRules.cap(fpsMode: model.fpsMode)
        d.frameMs = BCDFrameMath.averageMs(frames, now: now)
        if needs.contains(.lows) {
            // Sorting a minute of frame times is the dearest thing here: twice a second is plenty for a reading that moves slowly.
            if now - lowsAt >= 0.5 || force {
                lowsAt = now
                lows = (BCDFrameMath.low(frames, now: now, window: 30, percent: 1), BCDFrameMath.low(frames, now: now, window: 60, percent: 0.1),
                        BCDFrameMath.worstMs(frames, now: now), BCDFrameMath.stutters(frames, now: now))
            }
            d.low1 = lows.low1; d.low01 = lows.low01; d.worstMs = lows.worst; d.stutters = lows.stutters
        }
        if needs.contains(.graph) {
            let seconds = setup.layout == .custom ? setup.custom.graphSeconds : setup.layout == .tune ? 15 : 30
            d.graph = BCDFrameMath.graph(frames, now: now, count: seconds * 4)
        }
        d.limitWords = Self.limitWords(model.fpsMode)
        if needs.contains(.cpu) {
            cpu = cpuMeter.sample() ?? cpu
            d.cpuTotal = cpu?.total
            d.cpuTop = cpu?.top
        }
        if gpuOn { sampleGPU(now: now); d.gpuPercent = gpuPercent; d.gpuMs = gpuFrameMs }
        if needs.contains(.gpuMemory) {
            if metal == nil { metal = MTLCreateSystemDefaultDevice() }
            if let metal {
                d.gpuMemoryMB = metal.currentAllocatedSize >> 20
                d.gpuBudgetMB = Int(metal.recommendedMaxWorkingSetSize >> 20)
            }
        }
        // One task_info and one os_proc_available_memory: every layout's warning dot and tones need the memory left.
        let memory = Self.memory()
        d.footprintMB = memory?.footprintMB
        d.limitMB = memory.map { $0.footprintMB + $0.availableMB }
        d.peakMB = memory?.peakMB
        d.compressedMB = memory?.compressedMB
        if needs.contains(.swap) {
            let bytes = madeira_hud_swap_bytes()
            d.swapMB = Int(bytes >> 20)
        }
        if needs.contains(.jit) {
            var used: UInt64 = 0, total: UInt64 = 0
            madeira_hud_jit_pool(&used, &total)
            if total > 0 { d.jitUsedMB = Int(used >> 20); d.jitTotalMB = Int(total >> 20) }
        }
        let process = ProcessInfo.processInfo
        d.thermal = Self.thermalLevel(process.thermalState)
        d.lowPower = process.isLowPowerModeEnabled
        if batteryOn {
            let level = UIDevice.current.batteryLevel
            if level >= 0 { d.batteryPercent = Int((level * 100).rounded()) }
        }
        if needs.contains(.hdr) {
            hdrReading = BCDHDRLive.reading()
            d.hdr = hdrReading.hudText
            d.hdrTone = hdrReading.confirmed ? .good : .soft
        }
        d.api = pipeline.api; d.renderSize = pipeline.renderSize; d.outputSize = pipeline.outputSize
        d.upscale = pipeline.upscale; d.frameGen = pipeline.frameGen; d.gpuName = pipeline.gpuName
        d.playSeconds = max(0, Int(Date().timeIntervalSince(model.launchStartedAt)))
        let civil = BCDFormat.civil(Date())
        d.clock = String(format: "%02d:%02d", civil.hour, civil.minute)
        d.build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String

        // The quiet dot of layouts 1 and 2: the rate under 60% of its limit for 3 seconds, serious heat, little memory.
        if let cap = d.cap, d.fps > 0, d.fps < cap * 0.6 { lowSince = lowSince ?? now } else { lowSince = nil }
        d.warning = BCDHUDRules.quietWarning(thermal: d.thermal, freeMB: d.freeMB, lowFor: lowSince.map { now - $0 } ?? 0)

        // Calm: the same colours for 6 seconds. Layouts 3 and 4 (and a Custom list that asks) then fade; any change brings them back.
        let key = [BCDHUDRules.fpsTone(d.fps, cap: d.cap), BCDHUDRules.memoryTone(freeMB: d.freeMB)].map { "\($0)" }.joined() + "\(d.thermal)"
        if key.hashValue != calmKey { calmKey = key.hashValue; calmSince = now }
        let dims = setup.layout.dimsWhenCalm || (setup.layout == .custom && setup.custom.dimWhenCalm)
        let dimmed = dims && now - calmSince >= 6 && !state.moving
        if feed.dimmed != dimmed { feed.dimmed = dimmed }

        let plan = BCDHUDRows.plan(layout: setup.layout, custom: setup.custom, data: d, width: feed.available, roomy: feed.roomy)
        if plan != lastPlan || force {
            lastPlan = plan
            feed.plan = plan
        }
        logReadings(d, now: now)
    }

    /// Layouts 3 and 4 and a Custom list of five readings or more put what they show into the log every 5 seconds, so a screenshot and
    /// the log say the same.
    private func logReadings(_ d: BCDHUDData, now: Double) {
        let writes = setup.layout.logsReadings || (setup.layout == .custom && setup.custom.items.count >= 5)
        guard writes, now - lastLogged >= 5 else { return }
        lastLogged = now
        var parts = ["layout=\(setup.layout.rawValue)", String(format: "fps=%.0f", d.fps)]
        if let ms = d.frameMs { parts.append(String(format: "ms=%.1f", ms)) }
        if let low = d.low1 { parts.append(String(format: "low1=%.0f", low)) }
        if let cpu = d.cpuTotal { parts.append(String(format: "cpu=%.0f", cpu)) }
        if let gpu = d.gpuPercent { parts.append(String(format: "gpu=%.0f", gpu)) }
        if let used = d.footprintMB, let limit = d.limitMB { parts.append("mem=\(used)/\(limit)") }
        if let used = d.jitUsedMB, let total = d.jitTotalMB { parts.append("jit=\(used)/\(total)") }
        if let swap = d.swapMB { parts.append("swap=\(swap)") }
        parts.append("thermal=" + BCDSessionSummary.thermalWords[min(max(d.thermal, 0), 3)])
        parts.append("lpm=\(d.lowPower ? 1 : 0)")
        if let battery = d.batteryPercent { parts.append("batt=\(battery)") }
        parts.append("t=" + BCDHUDRows.clockTime(seconds: d.playSeconds))
        LogStore.shared.log("[hud] " + parts.joined(separator: " "))
        if needs.contains(.hdr) {
            var evidence = ["fps": String(format: "%.2f", d.fps), "thermal": String(d.thermal),
                            "low_power": d.lowPower ? "1" : "0", "layout": setup.layout.rawValue, "fps_window_s": "1", "frame_ms_window_s": "0.5",
                            "api": d.api ?? "unknown", "render_size": d.renderSize ?? "unknown",
                            "output_size": d.outputSize ?? d.renderSize ?? "unknown", "framegen": d.frameGen ?? "off",
                            "metalfx": d.upscale ?? "off"]
            if let cap = d.cap { evidence["fps_cap"] = String(format: "%.0f", cap) }
            if let battery = d.batteryPercent { evidence["battery_percent"] = String(battery) }
            if let ms = d.frameMs { evidence["frame_ms"] = String(format: "%.3f", ms) }
            if let low = d.low1 { evidence["low1_fps"] = String(format: "%.2f", low) }
            if let gpu = d.gpuPercent { evidence["gpu_percent"] = String(format: "%.1f", gpu) }
            if let cpu = d.cpuTotal { evidence["cpu_percent"] = String(format: "%.1f", cpu) }
            if let memory = d.footprintMB { evidence["memory_mb"] = String(memory) }
            BCDHDRLive.record(hdrReading, performance: evidence)
        }
    }

    // MARK: Counters

    /// ml1174, as the old overlay did: the GPU's busy time against the wall clock, and per frame.
    private func sampleGPU(now: Double) {
        let time = CACurrentMediaTime()
        let count = madeira_get_present_count()
        let busy = madeira_gpu_meter_busy_seconds(), buffers = madeira_gpu_meter_cmdbufs()
        let used = max(0, busy - gpuBusy), dt = max(0.001, time - gpuTime)
        let presented = count >= gpuCount ? count - gpuCount : 0
        gpuPercent = buffers > gpuBuffers ? min(100, used / dt * 100) : nil
        gpuFrameMs = presented > 0 ? used * 1000 / Double(presented) : 0
        gpuBusy = busy; gpuBuffers = buffers; gpuTime = time; gpuCount = count
    }

    struct Memory {
        var footprintMB: Int
        var peakMB: Int
        var compressedMB: Int
        var availableMB: Int
    }

    /// What iOS counts against the app (the footprint and its peak this run), what it has squeezed, and what the app may still use.
    static func memory() -> Memory? {
        var info = task_vm_info_data_t()
        var size = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let result = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(size)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &size) }
        }
        guard result == KERN_SUCCESS else { return nil }
        let available = os_proc_available_memory()
        return Memory(footprintMB: Int(info.phys_footprint >> 20), peakMB: Int(max(0, info.ledger_phys_footprint_peak) >> 20),
                      compressedMB: Int(info.compressed >> 20), availableMB: available > 0 ? Int(available >> 20) : 0)
    }

    static func thermalLevel(_ state: ProcessInfo.ThermalState) -> Int {
        switch state {
        case .nominal: return 0
        case .fair: return 1
        case .serious: return 2
        case .critical: return 3
        @unknown default: return 0
        }
    }

    /// "cap 60 · 120 Hz": the frame limit the player chose and the screen's own rate.
    static func limitWords(_ mode: Int) -> String {
        let hz = UIScreen.main.maximumFramesPerSecond
        switch mode {
        case 1: return "cap 60 · \(hz) Hz"
        case 3: return "cap 30 · \(hz) Hz"
        case 4: return "cap 40 · \(hz) Hz"
        case 0: return "cap \(hz) · \(hz) Hz"
        default: return "no cap · \(hz) Hz"
        }
    }

    // MARK: What is on the screen

    /// The size of the window the game is in now (the foreground scene's own window), never the device's screen size: a window can be
    /// resized (Stage Manager) or swapped for another display (a folding phone) while a game runs.
    static func windowSize() -> CGSize {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        let window = scene?.windows.first(where: \.isKeyWindow) ?? scene?.windows.first
        return window?.bounds.size ?? scene?.coordinateSpace.bounds.size ?? .zero
    }

    /// The rectangles the HUD keeps clear of in a window of `viewport`: the touch controls (in landscape, where they are drawn) and
    /// the menu button.
    private func avoidRects(_ viewport: CGSize) -> [CGRect] {
        var out: [CGRect] = []
        let controls = TouchControlsModel.shared
        if controls.visible, viewport.width > viewport.height {
            for control in controls.controls {
                let size = TouchControlsModel.size(control)
                let x = CGFloat(control.nx) * viewport.width, y = CGFloat(control.ny) * viewport.height
                out.append(CGRect(x: x - size.width / 2 - 4, y: y - size.height / 2 - 4, width: size.width + 8, height: size.height + 8))
            }
        }
        if model.menuButtonRect != .zero { out.append(model.menuButtonRect.insetBy(dx: -4, dy: -4)) }
        return out
    }

    /// What the game draws with, said once for the session from the library entry and the game's own file.
    private static func pipeline(for entry: LibraryEntry?) -> Pipeline {
        var out = Pipeline()
        guard let entry else { return out }
        if let api = entry.graphicsAPI, !api.isEmpty, entry.desktop != true {
            let words = api.replacingOccurrences(of: " / ", with: "/")
            out.api = entry.bits > 0 ? words + " \(entry.bits)" : words
        }
        let render = entry.resolution
        out.renderSize = render.isEmpty ? nil : render
        let profile = GameProfile(windowsPath: entry.windowsPath)
        let factor = (profile.get("metalfx-upscale") ?? MadeiraConfig.get("metalfx-upscale")).flatMap(Double.init)
        if let factor, factor > 1 {
            let parts = render.lowercased().split(separator: "x").compactMap { Int($0) }
            if parts.count == 2 { out.outputSize = "\(Int(Double(parts[0]) * factor))x\(Int(Double(parts[1]) * factor))" }
            out.upscale = "MetalFX " + (factor == factor.rounded() ? String(Int(factor)) : String(format: "%.1f", factor)) + "x"
        }
        if (profile.get("env.MADEIRA_FRAMEGEN") ?? MadeiraConfig.get("env.MADEIRA_FRAMEGEN")) == "1" { out.frameGen = "frame gen" }
        out.gpuName = entry.reportNVIDIA == true ? "GeForce RTX 3060" : "Apple GPU"
        return out
    }
}
