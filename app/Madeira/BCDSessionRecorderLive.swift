// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import UIKit
import os

// What a library session leaves behind (values and rules: BCDSessionCore.swift):
//
//  - The summary. The present path keeps every frame's time in a ring and a histogram (BCDFrameRing.c) whether or not a HUD is on; when
//    the session ends BCD reads the histogram once and writes one [session] line into the log (and a partial one when the app goes to
//    the background, which may be the last the log hears of it). Nothing is sampled while the game runs: the heat is a notification, the
//    memory peak is iOS's own ledger.
//  - The marker. While a session is open a small file says so (Application Support/bcd-session-open.json). A clean end removes it. One
//    found at the next start means the app was closed from outside, and BCD asks, once, whether to send the log (BCDCrashPrompt).
//
// Left out of the preview build (tools/ui-preview.py, ENGINE_BOUND).

/// The calls LibraryModel makes: from the main thread; anything else is put on it.
enum BCDSessionRecorder {
    static func sessionBegan(_ entry: LibraryEntry) { onMain { BCDSessionRecorderLive.shared.began(entry) } }
    static func sessionEnded() { onMain { BCDSessionRecorderLive.shared.ended() } }
    /// BCD is about to close on purpose (exit(0) after an upload): the open session is no crash.
    static func closingOnPurpose() { onMain { BCDSessionRecorderLive.shared.removeMarker() } }
    /// LibraryModel.init: a marker from the run before says that session never came back; Send Log's comment box learns to offer a screenshot.
    static func appStarted() {
        onMain {
            BCDScreenshotLive.install()
            BCDPerfTraceSend.install()   // round 604: the comment box tells that a performance trace goes with a log
            BCDDebugRunLive.shared.appStarted()   // round 604: a guided debug run that was open when BCD closed (before the crash question below)
            if let last = BCDBrkTrail.takeUnfinished() {   // round 624 (agent R S2): before the session's own lines
                LogStore.shared.log("[jit-brk-prev] the previous run ended inside a debugger region request: \(last)")
            }
            BCDSessionRecorderLive.shared.recoverUnfinished()
        }
    }

    private static func onMain(_ work: @escaping @MainActor () -> Void) {
        if Thread.isMainThread { MainActor.assumeIsolated(work) } else { DispatchQueue.main.async { MainActor.assumeIsolated(work) } }
    }
}

/// Round 624 (agent B S3; Blasphemous P3M757 and 31 more logs that end inside the JIT-pool request): the last start-up phase of the run, in a
/// one-line file beside the session marker, written by the engine thread BEFORE the step it names (the pool request suspends the whole process
/// while the debugger works, so nothing can be logged from inside it, and the marker's main-actor writes would come too late). A clean end
/// removes it (removeMarker); the next start reads it with the marker and says where the last start stopped.
enum BCDStartPhase {
    static var url: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("bcd-session-phase.txt")
    }
    /// Called from any thread, synchronously: one small atomic write per phase (five per start).
    static func note(_ name: String) {
        guard let url, let data = "\(name)\t\(Date().timeIntervalSince1970)\n".data(using: .utf8) else { return }
        try? data.write(to: url, options: .atomic)
    }
    /// Reads and removes the file: (phase, seconds since 1970) or nil.
    static func take() -> (name: String, at: Double)? {
        guard let url else { return nil }
        defer { try? FileManager.default.removeItem(at: url) }
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        let parts = text.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "\t")
        guard parts.count == 2, let at = Double(parts[1]) else { return nil }
        return (String(parts[0]), at)
    }
    static func clear() { if let url { try? FileManager.default.removeItem(at: url) } }
}

/// Round 624 (agent R S2; GTA V Enhanced 6D6ZZS and the archive's "window held" logs that end inside a pool step): one record per debugger
/// region request (BRK), written and fsynced BEFORE the request (the whole process is stopped inside it until the debugger answers and may be
/// killed there, so nothing logged from inside survives), and a "returned" line after it. A next start that finds a record without
/// "returned" logs which request the last run ended in, with the device's state when it began. Application Support, beside the marker.
enum BCDBrkTrail {
    /// A stored value so that the host check can point it at a temporary file.
    static var url: URL? = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("jit-brk-last.txt")
    private static let lock = NSLock()
    private static var began = 0.0   // systemUptime of the open request

    /// `site`: the caller's line in StikJITHelper.swift; `attempt`: the pool loop's try (nil elsewhere).
    static func begin(site: Int, size: Int, attempt: Int?) {
        guard let url else { return }
        lock.lock(); defer { lock.unlock() }
        let info = ProcessInfo.processInfo
        began = info.systemUptime
        let line = "begin site=L\(site) size=\(size >> 20)MB attempt=\(attempt.map(String.init) ?? "-") uptime=\(String(format: "%.1f", began)) "
            + "wall=\(Int(Date().timeIntervalSince1970)) thermal=\(info.thermalState.rawValue) lpm=\(info.isLowPowerModeEnabled ? 1 : 0) "
            + "avail=\(os_proc_available_memory() >> 20)MB\n"
        write(line, to: url, fresh: true)   // the file holds the last request only
    }

    static func end() {
        guard let url else { return }
        lock.lock(); defer { lock.unlock() }
        write("returned ms=\(Int((ProcessInfo.processInfo.systemUptime - began) * 1000))\n", to: url, fresh: false)
    }

    private static func write(_ text: String, to url: URL, fresh: Bool) {
        if fresh || !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: Data(text.utf8))
        try? handle.synchronize()   // fsync: the record must survive a kill inside the request
    }

    /// At the next start: the last record when it has no "returned" line, else nil; the file goes either way.
    static func takeUnfinished() -> String? {
        guard let url else { return nil }
        defer { try? FileManager.default.removeItem(at: url) }
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        guard let last = text.split(separator: "\n").last.map(String.init), last.hasPrefix("begin ") else { return nil }
        return last
    }
}

/// The engine thread's call: the phase on disk first, then the existing "[phase]" line.
func winiosPhase(_ name: String) {
    BCDStartPhase.note(name)
    winios_phase(name)
}

@MainActor final class BCDSessionRecorderLive {
    static let shared = BCDSessionRecorderLive()

    /// The player turned the question off ("Don't ask again", or Settings › Logs).
    static let askKey = "madeira.bcd.crashprompt"
    static var asks: Bool {
        get { UserDefaults.standard.object(forKey: askKey) == nil || UserDefaults.standard.bool(forKey: askKey) }
        set { UserDefaults.standard.set(newValue, forKey: askKey) }
    }

    private var started: Date?
    private var entry: LibraryEntry?
    private var thermalPeak = 0
    private var lowPowerSeen = false
    private var observers: [NSObjectProtocol] = []
    private var marker: BCDSessionMarker?

    static var markerURL: URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("bcd-session-open.json")
    }

    // MARK: A session

    func began(_ entry: LibraryEntry) {
        removeObservers()
        self.entry = entry
        started = Date()
        bcd_frame_stats_begin()
        let process = ProcessInfo.processInfo
        thermalPeak = BCDHUDLive.thermalLevel(process.thermalState)
        lowPowerSeen = process.isLowPowerModeEnabled
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: ProcessInfo.thermalStateDidChangeNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.noteHeat() }
        })
        observers.append(center.addObserver(forName: .NSProcessInfoPowerStateDidChange, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { if ProcessInfo.processInfo.isLowPowerModeEnabled { self?.lowPowerSeen = true } }
        })
        observers.append(center.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.lifecycle(background: true) }
        })
        observers.append(center.addObserver(forName: UIApplication.willEnterForegroundNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.lifecycle(background: false) }
        })
        writeMarker(for: entry)
        BCDDebugRunLive.shared.sessionBegan(entry)   // round 604: a guided debug run turns the trace and the detailed log on for this one session
        BCDPerfTraceLive.shared.began()   // round 604: one line a second to a trace file, only when the player asked for it (BCDPerfTraceLive.swift); off, nothing
    }

    func ended() {
        BCDHUDState.shared.sessionEnded()
        guard started != nil else { return }
        LogStore.shared.log(summary(partial: false).line())
        BCDPerfTraceLive.shared.ended()
        BCDDebugRunLive.shared.sessionEnded()
        removeObservers()
        removeMarker()
        started = nil
        entry = nil
    }

    private func noteHeat() {
        thermalPeak = max(thermalPeak, BCDHUDLive.thermalLevel(ProcessInfo.processInfo.thermalState))
    }

    private func lifecycle(background: Bool) {
        guard started != nil else { return }
        if background {
            // The line a log may end with if iOS closes the app in the background.
            LogStore.shared.log(summary(partial: true).line())
        }
        guard var current = marker else { return }
        current.background = background
        current.changedAt = Date().timeIntervalSince1970
        marker = current
        write(current)
    }

    private func removeObservers() {
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers.removeAll()
    }

    // MARK: The summary

    func summary(partial: Bool) -> BCDSessionSummary {
        var frames = bcd_frame_summary()
        bcd_frame_stats_read(&frames)
        noteHeat()
        var out = BCDSessionSummary()
        out.game = entry?.title ?? "Game"
        out.lengthSeconds = Int(Date().timeIntervalSince(started ?? Date()))
        if frames.first_ticks != 0, frames.last_ticks >= frames.first_ticks {
            out.playSeconds = bcd_frame_ticks_to_seconds(frames.last_ticks - frames.first_ticks)
        }
        out.frames = frames.frames
        out.activeSeconds = Double(frames.active_us) / 1e6
        out.p99Ms = Double(frames.p99_us) / 1000
        out.p999Ms = Double(frames.p999_us) / 1000
        out.worstMs = Double(frames.worst_us) / 1000
        out.stalls = frames.stalls
        out.stallSeconds = Double(frames.stall_us) / 1e6
        if let memory = BCDHUDLive.memory() {
            out.peakMB = max(memory.peakMB, memory.footprintMB)
            out.limitMB = memory.availableMB > 0 ? memory.footprintMB + memory.availableMB : nil
        }
        out.thermalPeak = thermalPeak
        out.lowPowerSeen = lowPowerSeen
        if let entry {
            if let api = entry.graphicsAPI, !api.isEmpty, entry.desktop != true {
                out.api = api.replacingOccurrences(of: " / ", with: "/") + (entry.bits > 0 ? " \(entry.bits)" : "")
            }
            out.renderSize = entry.resolution
            let profile = GameProfile(windowsPath: entry.windowsPath)
            if let factor = (profile.get("metalfx-upscale") ?? MadeiraConfig.get("metalfx-upscale")).flatMap(Double.init), factor > 1 {
                out.upscale = factor == factor.rounded() ? String(Int(factor)) : String(format: "%.1f", factor)
            }
            out.cap = BCDHUDRules.cap(fpsMode: LibraryModel.shared.fpsMode).map { Int($0) }
        }
        out.partial = partial
        return out
    }

    // MARK: The marker

    private func writeMarker(for entry: LibraryEntry) {
        let log = LogStore.shared.logFileURL
        let inode = ((try? FileManager.default.attributesOfItem(atPath: log.path))?[.systemFileNumber] as? NSNumber)?.uint64Value ?? 0
        let now = Date().timeIntervalSince1970
        let made = BCDSessionMarker(title: entry.desktop == true ? "the Windows desktop" : entry.title,
                                    program: entry.windowsPath.split(whereSeparator: { $0 == "\\" || $0 == "/" }).last.map(String.init) ?? "",
                                    startedAt: now, build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "",
                                    logInode: inode, background: false, changedAt: now)
        marker = made
        write(made)
    }

    private func write(_ marker: BCDSessionMarker) {
        guard let url = Self.markerURL, let data = try? JSONEncoder().encode(marker) else { return }
        try? data.write(to: url, options: .atomic)
    }

    func removeMarker() {
        marker = nil
        if let url = Self.markerURL { try? FileManager.default.removeItem(at: url) }
        BCDStartPhase.clear()
    }

    // MARK: The next start

    func recoverUnfinished() {
        let phase = BCDStartPhase.take()   // read and removed here; the [session] line below names it when a marker is found
        // madeira-bcd (round 624, agent N S16; 0ZHPYJ): "pool-alloc-begin" is written before the JIT memory request and "pool-ready" as soon
        // as it returns, failed or not; a run whose last phase is the first never came back from it (the process is stopped while the
        // debugger serves it). Said once, with or without a marker (the question may be turned off).
        if let phase, phase.name == "pool-alloc-begin", Date().timeIntervalSince1970 - phase.at < 86400 {
            LogStore.shared.log("[jit-pool] the last run ended inside the JIT memory request (\(max(0, Int(Date().timeIntervalSince1970 - phase.at)) / 60) min ago); told once")
            MemoryRightsNotice.shared.lastStartStoppedInPoolRequest()
        }
        guard let url = Self.markerURL, let data = try? Data(contentsOf: url) else { return }
        // One question per session, whatever the answer: the marker goes now.
        try? FileManager.default.removeItem(at: url)
        guard let found = try? JSONDecoder().decode(BCDSessionMarker.self, from: data) else { return }
        let log = Self.findLog(inode: found.logInode)
        let offer = BCDCrashRules.offer(marker: found, endedAt: log.map { LogUpload.modified($0).timeIntervalSince1970 },
                                        now: Date().timeIntervalSince1970, enabled: Self.asks)
        let stop = phase.map { ", last start-up phase \($0.name) \(max(0, Int($0.at - found.startedAt))) s after the start" } ?? ""
        LogStore.shared.log("[session] the last session of \(found.title) did not end cleanly (\(found.background ? "in the background" : "in front")\(stop)); "
                            + (offer == nil ? "not asking (\(BCDCrashRules.notAsking(marker: found, now: Date().timeIntervalSince1970, enabled: Self.asks)))" : log == nil ? "its log is gone, not asking" : "asking whether to send its log"))
        // madeira-bcd (round 624, agent R S5, RT83GE/6D6ZZS): the failed run's last lines in this log too (scrubbed; log only).
        if let log { Self.logTail(of: log) }
        guard let offer, let log else { return }
        // Round 604: a guided debug run that was open when BCD closed asks its own question about the same log (BCDHelpLive.swift): not twice.
        if BCDDebugRunLive.shared.coversUnfinished { return }
        BCDCrashPrompt.shared.schedule(offer, log: log)
    }

    /// madeira-bcd (round 624, agent R S5): the last 60 lines of the failed run's log, each through the upload scrubber, as "[prev-tail]" lines
    /// (BCDPrevTail keeps whole lines of the last 64 KB). Once, at the start after an unclean end.
    static func logTail(of url: URL) {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return }
        defer { try? handle.close() }
        let size = (try? handle.seekToEnd()) ?? 0
        let from = size > UInt64(BCDPrevTail.maxBytes) ? size - UInt64(BCDPrevTail.maxBytes) : 0
        guard (try? handle.seek(toOffset: from)) != nil, let data = try? handle.readToEnd(), !data.isEmpty else { return }
        let lines = BCDPrevTail.lines(data, offset: from)
        LogStore.shared.log("[prev-tail] the last \(lines.count) line(s) of the previous run's log (\(url.lastPathComponent)), scrubbed:")
        for line in lines { LogStore.shared.log("[prev-tail] " + LogUploadCore.scrub(line)) }
    }

    /// The session's log after a restart: a logs/ link named after the game (preferred, the intake files it by that name), or the rotated
    /// bcd-log.prev.txt; they are one file, so one inode.
    static func findLog(inode: UInt64) -> URL? {
        guard inode != 0, let docs = MadeiraConfig.documents else { return nil }
        let fm = FileManager.default
        var candidates: [URL] = []
        let dir = docs.appendingPathComponent("logs")
        if let names = try? fm.contentsOfDirectory(atPath: dir.path) {
            candidates += names.filter { $0.hasSuffix(".txt") }.sorted().reversed().map { dir.appendingPathComponent($0) }
        }
        candidates.append(docs.appendingPathComponent("bcd-log.prev.txt"))
        return candidates.first { ((try? fm.attributesOfItem(atPath: $0.path))?[.systemFileNumber] as? NSNumber)?.uint64Value == inode }
    }
}

// MARK: - The question

/// "The last session closed unexpectedly. Send the log?", once. It waits until the person is on the library's own screen (no game
/// starting, no page, sheet or alert over it), so it never takes a screen away; if that does not happen within five minutes it is
/// dropped.
@MainActor final class BCDCrashPrompt {
    static let shared = BCDCrashPrompt()
    private var timer: Timer?
    private var offer: BCDCrashOffer?
    private var log: URL?
    private var tries = 0

    func schedule(_ offer: BCDCrashOffer, log: URL) {
        self.offer = offer
        self.log = log
        tries = 0
        timer?.invalidate()
        let timer = Timer(timeInterval: 3, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.look() } }
        timer.tolerance = 1
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }

    private func look() {
        tries += 1
        let library = LibraryModel.shared
        // A game started, or five minutes went by: not now, and not later either (one question per session).
        if tries > 100 || library.current != nil { finish(); return }
        guard library.error == nil, library.restartNotice == nil, UIApplication.shared.applicationState == .active, let top = Self.presenter() else { return }
        finish()
        present(on: top)
    }

    private func finish() {
        timer?.invalidate()
        timer = nil
    }

    /// The root screen's controller when nothing is presented over it.
    static func presenter() -> UIViewController? {   // not private: the debug run's question (BCDHelpLive.swift) uses it
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        guard let root = scene?.keyWindow?.rootViewController, root.presentedViewController == nil else { return nil }
        return root
    }

    private func present(on top: UIViewController) {
        guard let offer, let log else { return }
        let alert = UIAlertController(title: offer.heading, message: offer.message, preferredStyle: .alert)
        let send = UIAlertAction(title: "Send log", style: .default) { _ in
            BCDLogComment.ask(for: log, { comment in
                LogUpload.send(log, game: LogUpload.game(of: log), comment: comment, done: { result in
                    Self.report(result)
                })
            }, cancelled: nil)
        }
        alert.addAction(send)
        alert.addAction(UIAlertAction(title: "Not now", style: .cancel))
        alert.addAction(UIAlertAction(title: "Don't ask again", style: .destructive) { _ in
            BCDSessionRecorderLive.asks = false
            LogStore.shared.log("[session] the question about a session that closed unexpectedly is turned off")
        })
        alert.preferredAction = send
        top.present(alert, animated: true)
        self.offer = nil
        self.log = nil
    }

    static func report(_ result: Result<String, Error>) {   // not private: the debug run's question uses it
        guard let top = presenter() ?? topAnywhere() else { return }
        let alert: UIAlertController
        switch result {
        case .success(let code):
            alert = UIAlertController(title: "Log sent", message: LogUpload.sentWords(code) + "\n\nQuote this code when you report the problem.", preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Copy code", style: .default) { _ in UIPasteboard.general.string = code })
            alert.addAction(UIAlertAction(title: "OK", style: .cancel))
        case .failure(let error):
            alert = UIAlertController(title: "The log was not sent", message: error.localizedDescription, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .cancel))
        }
        top.present(alert, animated: true)
    }

    private static func topAnywhere() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        var top = (scenes.first { $0.activationState == .foregroundActive } ?? scenes.first)?.keyWindow?.rootViewController
        while let next = top?.presentedViewController, !next.isBeingDismissed { top = next }
        return top
    }
}
