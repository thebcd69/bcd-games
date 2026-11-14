// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import UIKit

// The performance trace on the engine (values and rules: BCDPerfTraceCore.swift). Off unless the player turned it on (Settings › Logs ›
// "Performance trace") or started a guided debug run (BCDDebugRunLive.swift): then, and only then, one timer wakes once a second on the
// main thread, takes the new presents out of the frame-time ring (its own reader: the HUD's is not touched), reads the CPU, the memory,
// the JIT pool, the swap and the heat, and appends one CSV row to Documents/logs/traces/trace-<inode of the session log>.trace.csv.
// Off, nothing exists: no timer, no file, no reader, no battery monitoring.
//
// Cost: about two dozen system calls and one 90-byte write a second; the frame ring is the one the HUD and the session summary already
// use, so the present path has no new work. The file is cut back (the first two minutes and the newest 58) when it passes an hour of
// rows plus 300, and at most 20 traces are kept. Send Log appends the trace to the log it sends (LogUpload, LogUploadCore.pack's
// trailer) and the comment box says so (BCDLogComment.traceNote).
//
// Left out of the preview build (tools/ui-preview.py, ENGINE_BOUND).

@MainActor final class BCDPerfTraceLive {
    static let shared = BCDPerfTraceLive()

    /// Settings › Logs › Performance trace.
    static let switchKey = "madeira.bcd.trace"
    static var switchOn: Bool {
        get { UserDefaults.standard.bool(forKey: switchKey) }
        set { UserDefaults.standard.set(newValue, forKey: switchKey) }
    }
    /// A guided debug run asks for the trace for the session that starts next. Never saved: an app that is closed meanwhile forgets it.
    static var guidedRun = false

    nonisolated static var folder: URL? { MadeiraConfig.documents?.appendingPathComponent("logs").appendingPathComponent(BCDTraceRules.folder, isDirectory: true) }
    nonisolated static var store: BCDTraceStore? { folder.map { BCDTraceStore(folder: $0) } }

    private var timer: Timer?
    private var handle: FileHandle?
    private var reader = bcd_frame_reader()
    private var buffer = [UInt64](repeating: 0, count: 512)
    private var cpu: CPUMeter?
    private var batteryMonitored = false
    private var fileURL: URL?
    private var begun = 0.0
    private var lastSample = 0.0
    private var lastFrame: Double?
    private var rows = 0
    private var cutAt = 0
    private var seconds = 0
    /// madeira-bcd (Porthole port, item 11): the session's seconds for its [benchmark] line, and the switches it started with (nil: no line).
    private var benchmark: [BCDTraceSample]?
    private var benchmarkSwitches: [String] = []
    /// The trace's file is being written this session (the switch or a guided run).
    private var recording = false

    var running: Bool { timer != nil }

    // MARK: A session

    /// The session started (BCDSessionRecorderLive.began).
    func began() {
        stop()
        // madeira-bcd (Porthole port, item 11): the [benchmark] line (bcd.cfg or the game's own `benchmark-line = 1`; off by default) needs the
        // same second-by-second readings; with it alone the sampler runs without a file. Without either, nothing below runs, as before.
        benchmark = BCDBenchmark.on(MadeiraConfig.gameValue(BCDBenchmark.key) ?? MadeiraConfig.get(BCDBenchmark.key)) ? [] : nil
        benchmarkSwitches = benchmark == nil ? [] : BCDBenchmark.switchNames.compactMap { name in
            getenv(name).map { name + "=" + String(cString: $0) }.flatMap { $0.hasSuffix("=") ? nil : $0 }
        }
        let records = BCDTraceRules.records(switchOn: Self.switchOn, guidedRun: Self.guidedRun)
        guard records || benchmark != nil, let store = Self.store else { return }
        recording = records
        if records {
            let fm = FileManager.default
            try? fm.createDirectory(at: store.folder, withIntermediateDirectories: true)
            let inode = ((try? fm.attributesOfItem(atPath: LogStore.shared.logFileURL.path))?[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
            guard inode != 0 else {
                LogStore.shared.log("[trace] not recording: the session log has no file number")
                return
            }
            store.prune(keep: BCDTraceRules.keptFiles - 1)
            let url = store.url(inode: inode)
            let now = Date()
            let civil = BCDFormat.civil(now)
            let date = String(format: "%04d-%02d-%02d", civil.year, civil.month, civil.day)
            let start = String(format: "%02d:%02d:%02d.%03d", civil.hour, civil.minute, civil.second, civil.millisecond)
            var used: UInt64 = 0, total: UInt64 = 0
            madeira_hud_jit_pool(&used, &total)
            let header = BCDTraceStore.header(date: date, start: start, jitTotalMB: total > 0 ? Int(total >> 20) : nil) + "\n"
            guard fm.createFile(atPath: url.path, contents: Data(header.utf8)), let h = try? FileHandle(forWritingTo: url) else {
                LogStore.shared.log("[trace] not recording: the file could not be made")
                return
            }
            _ = try? h.seekToEnd()
            handle = h
            fileURL = url
        }
        reader = bcd_frame_reader_start()
        cpu = CPUMeter()
        _ = cpu?.sample()   // the first reading only sets the baseline
        if !UIDevice.current.isBatteryMonitoringEnabled {
            UIDevice.current.isBatteryMonitoringEnabled = true
            batteryMonitored = true
        }
        begun = bcd_frame_ticks_to_seconds(bcd_frame_now_ticks())
        lastSample = begun
        lastFrame = nil
        rows = 0
        cutAt = BCDTraceRules.maxLines + BCDTraceRules.slack
        seconds = 0
        let t = Timer(timeInterval: BCDTraceRules.intervalSeconds, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.sample() } }
        t.tolerance = 0.25   // the system may fold it into another wake-up
        RunLoop.main.add(t, forMode: .common)
        timer = t
        if let url = fileURL, records {
            LogStore.shared.log("[trace] recording one line a second to logs/traces/\(url.lastPathComponent)"
                                + (Self.switchOn ? "" : " (guided debug run)"))
        }
        if benchmark != nil { LogStore.shared.log("[benchmark] measuring this session for its [benchmark] line (benchmark-line = 1)") }
        sample()
    }

    /// The session ended (BCDSessionRecorderLive.ended).
    func ended() {
        guard timer != nil else { return }
        sample()
        let lines = rows
        let measured = benchmark, switches = benchmarkSwitches
        stop()
        if recording { LogStore.shared.log("[trace] \(lines) lines kept") }
        // madeira-bcd (Porthole port, item 11): only when the session asked for it, and only when the game showed a frame.
        if let measured, let line = BCDBenchmark.line(game: LibraryModel.shared.activeEntry?.title ?? "Game",
                                                      build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?",
                                                      samples: measured, switches: switches) {
            LogStore.shared.log(line)
        }
        benchmark = nil
        recording = false
    }

    private func stop() {
        timer?.invalidate()
        timer = nil
        try? handle?.close()
        handle = nil
        cpu = nil
        if batteryMonitored { UIDevice.current.isBatteryMonitoringEnabled = false; batteryMonitored = false }
    }

    // MARK: A second

    private func sample() {
        guard handle != nil || benchmark != nil else { return }
        let now = bcd_frame_ticks_to_seconds(bcd_frame_now_ticks())
        var times: [Double] = []
        var lost: UInt64 = 0
        repeat {
            let count = Int(bcd_frame_ring_drain(&reader, &buffer, UInt32(buffer.count), &lost))
            for i in 0..<count { times.append(bcd_frame_ticks_to_seconds(buffer[i])) }
            if count < buffer.count { break }
        } while true
        let elapsed = max(0.001, now - lastSample)
        let frames = BCDTraceFrames.summarize(times: times, previous: lastFrame, now: now, elapsed: elapsed)
        if let newest = times.last { lastFrame = newest }
        lastSample = now

        var s = BCDTraceSample()
        s.t = Int((now - begun).rounded())
        s.fps = frames.fps; s.frameMs = frames.frameMs; s.worstMs = frames.worstMs
        if let reading = cpu?.sample() { s.cpu = reading.total; s.top = reading.top }
        if let memory = BCDHUDLive.memory() {
            s.memoryMB = memory.footprintMB
            s.freeMB = memory.availableMB > 0 ? memory.availableMB : nil
        }
        s.swapMB = Int(madeira_hud_swap_bytes() >> 20)
        var used: UInt64 = 0, total: UInt64 = 0
        madeira_hud_jit_pool(&used, &total)
        if total > 0 { s.jitMB = Int(used >> 20) }
        let process = ProcessInfo.processInfo
        s.thermal = BCDHUDLive.thermalLevel(process.thermalState)
        s.lowPower = process.isLowPowerModeEnabled
        if UIDevice.current.isBatteryMonitoringEnabled {
            let level = UIDevice.current.batteryLevel
            if level >= 0 { s.batteryPercent = Int((level * 100).rounded()) }
        }
        seconds = s.t
        if (benchmark?.count ?? Int.max) < 86_400 { benchmark?.append(s) }   // madeira-bcd (Porthole port, item 11): a day of seconds at most
        guard let handle else { return }
        try? handle.write(contentsOf: Data((s.row() + "\n").utf8))
        rows += 1
        if rows >= cutAt { cutBack() }
    }

    /// An hour and five minutes of rows: the file keeps the first two minutes and the newest 58 minutes. A rewrite, once in 300 seconds.
    private func cutBack() {
        guard let url = fileURL, let text = try? String(contentsOf: url, encoding: .utf8) else { return }
        let (header, all) = BCDTraceStore.read(text)
        let cut = BCDTraceRules.cut(all)
        let body = ((header.map { [$0] } ?? []) + cut.rows).joined(separator: "\n") + "\n"
        try? handle?.close()
        guard (try? body.write(to: url, atomically: true, encoding: .utf8)) != nil, let h = try? FileHandle(forWritingTo: url) else {
            handle = nil   // the sampler stops writing; the timer ends with the session
            return
        }
        _ = try? h.seekToEnd()
        handle = h
        rows = cut.rows.count
    }
}

// MARK: - Send Log

enum BCDPerfTraceSend {
    /// The inode of a session log (its hard links and the file the app rotated it to share it).
    static func inode(of log: URL) -> UInt64 {
        ((try? FileManager.default.attributesOfItem(atPath: log.path))?[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
    }

    /// The lines Send Log appends to this log; empty without a trace for it.
    static func lines(for log: URL) -> [String] {
        guard let store = BCDPerfTraceLive.store else { return [] }
        return store.linesToSend(inode: inode(of: log))
    }

    /// Hooks the comment box (BCDComponents.swift) so it can say that a trace goes with the log.
    static func install() {
        BCDLogComment.traceNote = { log in
            guard let store = BCDPerfTraceLive.store else { return nil }
            return store.note(inode: inode(of: log))
        }
    }
}
