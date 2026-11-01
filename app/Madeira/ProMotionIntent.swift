import SwiftUI
import UIKit
import QuartzCore
import os

/// Frame-rate intent for the ProMotion panel. CAMetalLayer presents alone
/// don't express one: iOS parks the display at 60Hz and only promotes on
/// touch (observed 2026-07-05: MAX mode ran 60 except ~119 bursts while
/// touching). An active CADisplayLink with a preferredFrameRateRange is the
/// documented way for present-driven Metal apps to hold the panel higher.
/// The tick itself does nothing.
///
/// Default (as on main): armed only outside the 60 and 30 caps, i.e. for
/// MAX and RAW, where the game is meant to run above 60, and for the 40 cap,
/// whose 25ms spacing needs the 120Hz grid (at 60Hz it would land on 33.3ms).
///
/// Opt-in `env.MADEIRA_PROMOTE = 1` (Settings > Display > Hold the display
/// at its maximum rate) also arms it in the 60 cap. The panel rate is the
/// grid a present snaps to: the 60 cap is a minimum spacing between
/// presentations (afterMinimumDuration), and a drawable becomes visible at
/// a vblank, never between two. With the panel parked at 60Hz a frame
/// ready at 17-25ms waits for the 33.3ms vblank, so a 20ms pipeline reports
/// 30 FPS; at 120Hz the same frame lands at 25ms (40 FPS). The cap itself is
/// unchanged, but the panel runs at 120Hz for the whole session, which
/// costs power, so it is not the default. The 30 cap keeps a 60Hz intent
/// (33.3ms is an exact multiple of 16.67ms).
final class ProMotionIntent {
    static let shared = ProMotionIntent()
    private var link: CADisplayLink?
    private var activeMax: Float = 0
    /// madeira-bcd (round 626, agent H S1): what the link's callbacks say about the rate iOS grants (log only).
    private var meter = DisplayRateMeter()
    /// The intent apply(mode:) published last (has30Cap's first read republishes it instead of 0).
    private static var lastIntent = 0

    /// Honest report of what the panel can do, for the native `[frame]` line.
    static var panelMaxFPS: Int { UIScreen.main.maximumFramesPerSecond }

    /// The opt-in above; read when a session's pacing is applied.
    static var holdMaximum: Bool { MadeiraConfig.flag("MADEIRA_PROMOTE", fallback: false) }

    /// Whether DXMT has the 30 FPS cap (vsync mode 3) and the display-rate
    /// hook. Both arrive with willfaust/dxmt#1; without it mode 3 would run
    /// uncapped, so the 30 choice is offered only when this is true.
    static var has30Cap: Bool = {
        // madeira-bcd (round 626, agent H S1): the last intent, not 0: a first read after apply(mode:) armed the link (the HUD's limit
        // list, the in-game menu) cleared DXMT's recorded intent, so the [frame] line said "intent 0" while the link ran.
        madeira_set_display_max_fps(Int32(panelMaxFPS), Int32(lastIntent))
        return madeira_dxmt_has_display_pacing() != 0
    }()

    /// Whether the 40 FPS cap (vsync mode 4) is offered: DXMT must have it
    /// (madeira_dxmt_has_40_cap; without it mode 4 would present uncapped) and
    /// the panel must reach 120 Hz, because 25 ms is three refreshes at 120 Hz
    /// but rounds to 33 ms at 60 Hz. maxHz(for:) holds the panel at its maximum
    /// while it runs.
    static var has40Cap: Bool { madeira_dxmt_has_40_cap() != 0 && panelMaxFPS >= 120 }

    /// The pacing mode a saved FPS limit runs as: 30 and 40 run as 60 when
    /// they are not offered.
    static func supportedMode(_ mode: Int) -> Int32 {
        if mode == 3 && !has30Cap { return 1 }
        if mode == 4 && !has40Cap { return 1 }
        return Int32(mode)
    }

    /// `maxHz` 0 means "tear it down".
    func setActive(_ active: Bool, maxHz: Int = ProMotionIntent.panelMaxFPS) {
        let want = Float(max(maxHz, 0))
        if active && want > 0 {
            if link != nil && activeMax == want { return }
            link?.invalidate()
            let l = CADisplayLink(target: self, selector: #selector(tick))
            // `minimum` must not exceed the panel's own maximum or CoreAnimation
            // clamps the whole range away (a 60Hz device asked for min 60 /
            // max 120 gets nothing useful).
            let lo = Float(min(60, maxHz))
            l.preferredFrameRateRange = CAFrameRateRange(minimum: lo, maximum: want, preferred: want)
            l.add(to: .main, forMode: .common)
            link = l
            activeMax = want
            meter.reset()
            // madeira-bcd (round 626): LogStore, not stderr: a session's first stderr lines are not in the log yet.
            LogStore.shared.log("[promote] display link armed preferred=\(Int(want))Hz panel_max=\(ProMotionIntent.panelMaxFPS)Hz")
        } else {
            if link != nil { LogStore.shared.log("[promote] display link released") }
            link?.invalidate()
            link = nil
            activeMax = 0
        }
    }

    /// The intent that belongs to a given pacing mode (0 = none). See the type comment.
    static func maxHz(for mode: Int32) -> Int {
        if mode == 3 { return holdMaximum ? min(60, panelMaxFPS) : 0 }   // 30 cap
        if mode == 1 { return holdMaximum ? panelMaxFPS : 0 }            // 60 cap
        return panelMaxFPS                                                // MAX, RAW, 40 cap
    }

    /// madeira-bcd (round 626, agent H S2): the rate a session holds the panel at for a pacing mode: 60 for the 60 and 30 caps, the
    /// panel's maximum for MAX, uncapped and the 40 cap. Published as BCD_SESSION_HZ before Wine starts (applyEnvironment).
    static func sessionHz(for mode: Int32) -> Int { (mode == 1 || mode == 3) ? 60 : max(60, panelMaxFPS) }

    /// What win32u reports to Windows programs: 60, or with env.MADEIRA_REPORT_HZ = 1 the session's rate (build/win32u-unix/sysparams_ios.c).
    static func reportedHz(for mode: Int32) -> Int {
        guard let on = getenv("MADEIRA_REPORT_HZ"), String(cString: on) == "1" else { return 60 }
        let hz = sessionHz(for: mode)
        return hz > 60 && hz <= 240 ? hz : 60
    }

    /// Arms or releases the link for `mode` and publishes the rates to DXMT.
    static func apply(mode: Int32) {
        let hz = maxHz(for: mode)
        lastIntent = hz
        LogStore.shared.log("[promote] apply mode=\(mode) intent=\(hz)Hz panel_max=\(panelMaxFPS)Hz hold-in-60-cap=\(holdMaximum ? 1 : 0)")
        shared.setActive(hz > 0, maxHz: hz)
        madeira_set_display_max_fps(Int32(panelMaxFPS), Int32(hz))
    }

    @objc private func tick(_ sender: CADisplayLink) {
        // madeira-bcd (round 626, agent H S1): a few double operations a callback; a line now and then (12 a session at most).
        if let line = meter.note(timestamp: sender.timestamp, targetTimestamp: sender.targetTimestamp,
                                 preferred: Int(activeMax), panelMax: ProMotionIntent.panelMaxFPS) {
            LogStore.shared.log(line)
        }
    }
}

/// ml1137: process-wide fence-mode display state for the overlay pill.
enum FPSOverlayFenceMode {
    static var current: Int = Int(MadeiraConfig.gameValue("fence-chain") ?? MadeiraConfig.get("fence-chain") ?? "1") ?? 1
}

/// Opt-in `env.MADEIRA_THERMAL_CAP = 1`: when iOS reports the device as
/// serious or critical, a session paced at the 60 cap, MAX or RAW drops to the
/// 40 cap (three refreshes at 120 Hz, so evenly spaced; the 30 cap where 40 is
/// not offered). A hot device clocks its CPU down, and at the 60 cap a frame
/// that misses 16.7 ms waits for the next 60 Hz vblank and shows at 33.3 ms, so
/// an emulated game swings between 60 and the 30s; a steady 40 does less work
/// per second and stays even. The session's own mode comes back once the
/// device is nominal again. A cap chosen during the session (the HUD pill or
/// the in-game menu) wins: it is never replaced or restored. Not saved.
final class ThermalPacing {
    static let shared = ThermalPacing()
    static var enabled: Bool { MadeiraConfig.flag("MADEIRA_THERMAL_CAP", fallback: false) }
    private var observer: NSObjectProtocol?
    private var restoreMode: Int32?
    private var ourMode: Int32 = -1

    /// From a session's start, after its pacing is applied.
    func begin() {
        end()
        guard Self.enabled else { return }
        observer = NotificationCenter.default.addObserver(forName: ProcessInfo.thermalStateDidChangeNotification,
                                                          object: nil, queue: .main) { [weak self] _ in self?.evaluate() }
        LogStore.shared.log("[thermal-cap] on: 40 FPS while the device is serious or critical (env.MADEIRA_THERMAL_CAP)")
        evaluate()
    }

    /// From a session's end.
    func end() {
        if let o = observer { NotificationCenter.default.removeObserver(o) }
        observer = nil; restoreMode = nil; ourMode = -1
    }

    /// The session's cap was changed by hand: leave it alone from now on.
    func userChose() { restoreMode = nil; ourMode = -1 }

    /// madeira-bcd (round 604): switched on from the in-game menu (begin(), under a name the launch check's "no BCD file starts a session" leaves alone).
    func arm() { begin() }

    /// madeira-bcd (round 604, the in-game menu's "Cool the game when the phone is hot"): the mode this holds the session at now, or nil
    /// while it holds nothing.
    var heldMode: Int32? { restoreMode == nil ? nil : ourMode }

    /// madeira-bcd (round 604): switched off from the in-game menu. A lower cap this took is put back first (unless the player chose a cap since).
    func disarm() {
        if let restore = restoreMode, madeira_get_vsync_locked() == ourMode {
            madeira_set_vsync_locked(restore)
            ProMotionIntent.apply(mode: restore)
            LogStore.shared.log("[thermal-cap] switched off in the menu: back to the session's mode \(restore)")
        }
        end()
    }

    private func evaluate() {
        let state = ProcessInfo.processInfo.thermalState
        let current = madeira_get_vsync_locked()
        if let restore = restoreMode {
            guard current == ourMode else { userChose(); return }
            guard state == .nominal else { return }
            madeira_set_vsync_locked(restore)
            ProMotionIntent.apply(mode: restore)
            LogStore.shared.log("[thermal-cap] nominal again: back to the session's mode \(restore)")
            restoreMode = nil; ourMode = -1
            return
        }
        guard state == .serious || state == .critical else { return }
        let target: Int32 = ProMotionIntent.has40Cap ? 4 : ProMotionIntent.has30Cap ? 3 : -1
        guard target >= 0, current == 1 || current == 0 || current == 2 else {
            LogStore.shared.log("[thermal-cap] hot, but mode \(current) is kept (no lower cap offered, or already capped)")
            return
        }
        restoreMode = current; ourMode = target
        madeira_set_vsync_locked(target)
        ProMotionIntent.apply(mode: target)
        LogStore.shared.log("[thermal-cap] \(state == .critical ? "critical" : "serious"): mode \(current) -> \(target == 4 ? "40" : "30") FPS cap")
    }
}

// display-rate-meter:begin (tests/host/check-display-rate.py compiles this region alone: Foundation only)
/// madeira-bcd (round 626, agent H S1; Hollow Knight: Silksong DF0537): what the display link's callbacks say about the rate iOS really
/// grants. ProMotionIntent.tick feeds it; the lines go to the log. Two numbers, because they answer different questions:
///   granted = the link's own frame duration (targetTimestamp - timestamp) averaged over the window: the rate iOS decided on for this
///             app; a busy main thread does not change it;
///   arrived = callbacks per second of wall time: what the main run loop received. Lower than granted = the main thread was late.
struct DisplayRateMeter {
    /// The first measurement after `settle` seconds, then one only when the granted rate moved by `change` (or every `quiet` seconds at
    /// most), `maxLines` a session. A pause of `gap` seconds (the app in the background) starts the window again.
    static let settle = 2.0, quiet = 20.0, change = 0.08, gap = 1.0, maxLines = 12

    private(set) var lines = 0
    private var ticks = 0
    private var first = 0.0, last = 0.0, durationSum = 0.0, durations = 0
    private var loggedHz = 0.0, loggedAt = 0.0

    mutating func reset() { self = DisplayRateMeter() }

    /// One callback of the link: its `timestamp` and `targetTimestamp` (seconds). Returns the line to log, or nil.
    mutating func note(timestamp: Double, targetTimestamp: Double, preferred: Int, panelMax: Int) -> String? {
        if ticks > 0 && timestamp - last > Self.gap { ticks = 0; durationSum = 0; durations = 0 }   // background or a long stall
        if ticks == 0 { first = timestamp; last = timestamp; ticks = 1; return nil }
        ticks += 1
        last = timestamp
        let d = targetTimestamp - timestamp
        if d > 0.0005 && d < 0.2 { durationSum += d; durations += 1 }
        let span = last - first
        guard span >= Self.settle, ticks > 2, durations > 0 else { return nil }
        let arrived = Double(ticks - 1) / span
        let granted = Double(durations) / durationSum
        let callbacks = ticks - 1
        first = last; ticks = 1; durationSum = 0; durations = 0            // the next window starts here
        guard lines < Self.maxLines else { return nil }
        let moved = loggedHz > 0 && abs(granted - loggedHz) / loggedHz >= Self.change
        guard loggedHz == 0 || moved || last - loggedAt >= Self.quiet else { return nil }
        lines += 1; loggedHz = granted; loggedAt = last
        var note = ""
        if granted < Double(preferred) * 0.8 { note = " -- BELOW the request" }
        else if arrived < granted * 0.9 { note = " -- callbacks arrived late (main thread busy)" }
        return String(format: "[promote] display link: granted %.1f Hz (%.2f ms per frame), %d callbacks in %.1f s = %.1f Hz arrived; asked preferred=%dHz, panel_max=%dHz",
                      granted, 1000 / granted, callbacks, span, arrived, preferred, panelMax) + note
    }
}
// display-rate-meter:end
