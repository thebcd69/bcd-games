// SPDX-License-Identifier: GPL-3.0-or-later
// Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
import Foundation
import UIKit
import Darwin

@MainActor enum BCDHDRLive {
    static let changed = Notification.Name("BCDHDRChanged")
    private static var session = ""
    private static var beganAt = 0.0
    private static var segment = 0
    private static var revision: UInt32 = 0
    private static var observer: NSObjectProtocol?
    private static var intervals = 0
    private static var maximumPeak = 0.0
    private static var confirmedIntervals = 0
    private static var calibrationIntervals = 0
    private static var calibrationPeak = 0.0

    static func reading() -> BCDHDRReading {
        var raw = BCDHDRSnapshot()
        bcd_hdr_read(&raw)
        var r = BCDHDRReading()
        r.requested = raw.requested != 0
        r.enabled = raw.flags & 1 != 0; r.pattern = raw.flags & 2 != 0; r.measuring = raw.flags & 4 != 0
        r.prepared = raw.prepared != 0; r.valid = raw.valid != 0
        r.edr = raw.edr != 0; r.linear = raw.linear != 0; r.format = raw.pixel_format
        r.appliedFlags = raw.applied_flags; r.samples = raw.samples; r.above = raw.above_sdr
        r.invalid = raw.invalid; r.error = raw.error; r.ageMS = raw.age_ms
        r.probeState = raw.probe_state; r.preparations = raw.probe_preparations
        r.allocations = raw.probe_allocations; r.refreshes = raw.headroom_refreshes
        r.revision = raw.revision; r.sampleNS = raw.sample_ns
        r.peak = raw.peak.isFinite ? Double(raw.peak) : 0
        r.headroom = raw.headroom.isFinite ? max(1, Double(raw.headroom)) : 1
        r.appliedPeak = raw.applied_peak.isFinite ? Double(raw.applied_peak) : 0
        return r
    }

    @discardableResult static func choose(_ id: String, _ value: String) -> Bool {
        guard id == "hdr.view" else { return false }
        bcd_hdr_set_enabled(value == "hdr" ? 1 : 0)
        controlChanged(reason: "comparison")
        return true
    }

    /// Level 4 (or a Custom HUD's HDR item) owns sampling. There is no second
    /// timer or independent overlay; leaving that layout switches the probe off.
    static func measure(_ on: Bool) { bcd_hdr_set_probe(on ? 1 : 0) }

    static func began(_ entry: LibraryEntry) {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        session = String(UUID().uuidString.prefix(8)).lowercased()
        beganAt = ProcessInfo.processInfo.systemUptime
        segment = 0; revision = 0; intervals = 0; maximumPeak = 0; confirmedIntervals = 0; calibrationIntervals = 0; calibrationPeak = 0
        let profile = GameProfile(windowsPath: entry.windowsPath)
        func value(_ key: String, _ fallback: String) -> String { profile.get(key) ?? MadeiraConfig.get(key) ?? fallback }
        let info = Bundle.main
        let fields = ["game": entry.title, "api": entry.graphicsAPI ?? "unknown", "bits": String(entry.bits),
                      "resolution": entry.resolution, "build": info.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
                      "commit": info.object(forInfoDictionaryKey: "MadeiraCommit") as? String ?? "unknown",
                      "device": hardwareModel(), "os": UIDevice.current.systemVersion,
                      "auto_hdr": value("env.DXMT_AUTO_HDR", "0"), "peak_limit": value("env.DXMT_AUTO_HDR_PEAK", "2"),
                      "pattern_seconds": value("env.DXMT_AUTO_HDR_PATTERN", "0"),
                      "d3d9_native": value("d3d9", "default"), "d3d9_x64": value("env.MADEIRA_D3D9_X64", "default"),
                      "framegen": value("env.MADEIRA_FRAMEGEN", "0"), "metalfx": value("metalfx-upscale", "1")]
        log("begin", fields: fields)
        observer = NotificationCenter.default.addObserver(forName: changed, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { controlChanged(reason: "control") }
        }
    }

    private static func controlChanged(reason: String) {
        guard !session.isEmpty else { return }
        let r = reading()
        guard revision != r.revision else { return }
        revision = r.revision; segment += 1
        log(reason, fields: r.evidence)
    }

    /// Called by the existing HUD's five-second logger. Every measured interval
    /// carries its session/segment and timestamp, alongside the performance data.
    static func record(_ r: BCDHDRReading, performance: [String: String]) {
        guard !session.isEmpty else { return }
        controlChanged(reason: "control")
        var fields = r.evidence.merging(performance) { _, new in new }
        fields["low1_window_s"] = "30" // rolling 1% low, shared with the performance HUD
        if r.valid && r.ageMS <= 2000 {
            intervals += 1
            if r.appliedFlags & 2 != 0 {
                calibrationIntervals += 1; calibrationPeak = max(calibrationPeak, r.peak)
            } else {
                maximumPeak = max(maximumPeak, r.peak)
                if r.confirmed { confirmedIntervals += 1 }
            }
        }
        log("sample", fields: fields)
    }

    static func lifecycle(_ background: Bool) { log(background ? "background" : "foreground", fields: reading().evidence) }
    static func ended() {
        guard !session.isEmpty else { return }
        log("end", fields: ["logged_intervals": String(intervals), "game_hdr_intervals": String(confirmedIntervals),
                            "maximum_logged_game_peak": String(format: "%.3f", maximumPeak),
                            "calibration_intervals": String(calibrationIntervals), "maximum_logged_calibration_peak": String(format: "%.3f", calibrationPeak)])
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil; session = ""
    }

    private static func hardwareModel() -> String {
        var count = 0
        guard sysctlbyname("hw.machine", nil, &count, nil, 0) == 0, count > 0 else { return UIDevice.current.model }
        var name = [CChar](repeating: 0, count: count)
        guard sysctlbyname("hw.machine", &name, &count, nil, 0) == 0 else { return UIDevice.current.model }
        return String(cString: name)
    }

    private static func log(_ event: String, fields: [String: String]) {
        guard !session.isEmpty else { return }
        let data = fields.merging(["event": event, "session": session, "segment": String(segment),
            "elapsed_s": String(format: "%.3f", max(0, ProcessInfo.processInfo.systemUptime - beganAt))]) { _, new in new }
        // JSON keeps game names and other free text on one line and unambiguous.
        guard let bytes = try? JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]),
              let text = String(data: bytes, encoding: .utf8) else { return }
        LogStore.shared.log("[hdr-evidence] " + text)
    }
}
