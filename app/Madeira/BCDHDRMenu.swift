// SPDX-License-Identifier: GPL-3.0-or-later
// Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
import SwiftUI

/// Signal evidence, separate from the user's requested HDR setting. All values
/// are relative to SDR white; this is not a measurement of physical display nits.
struct BCDHDRReading: Equatable {
    var requested = false
    var enabled = true
    var pattern = false
    var measuring = false
    var prepared = false
    var valid = false
    var edr = false
    var linear = false
    var format: UInt32 = 0
    var appliedFlags: UInt32 = 0
    var samples: UInt32 = 0
    var above: UInt32 = 0
    var invalid: UInt32 = 0
    var error: UInt32 = 0
    var ageMS: UInt32 = 0
    var revision: UInt32 = 0
    var sampleNS: UInt64 = 0
    var probeState: UInt32 = 0
    var preparations: UInt32 = 0
    var allocations: UInt32 = 0
    var refreshes: UInt32 = 0
    var appliedPeak: Double = 0
    var peak: Double = 0
    var headroom: Double = 1

    var confirmed: Bool {
        requested && error == 0 && ageMS <= 2000 && valid && prepared && measuring && edr && linear && format == 115 && samples > 0
            && invalid == 0 && above > 0 && peak > 1.001 && headroom > 1 && appliedFlags & 1 != 0
    }
    var title: String {
        if !requested { return "Auto HDR off" }
        if !prepared { return "Auto HDR unavailable for this output" }
        if !measuring { return enabled ? "HDR comparison ready" : "SDR comparison" }
        if error != 0 { return "HDR measurement unavailable" }
        if !valid || ageMS > 2000 { return "Waiting for a measured frame" }
        if invalid > 0 || samples == 0 { return "Invalid pixel samples" }
        if !edr || !linear || format != 115 { return "HDR output is not configured" }
        if appliedFlags & 1 == 0 { return "SDR comparison" }
        if confirmed { return appliedFlags & 2 != 0 ? "HDR test pattern confirmed" : "Game frame contains an HDR signal" }
        if above > 0 && headroom <= 1 { return "HDR signal; display headroom is 1×" }
        return "No HDR highlights in sampled pixels"
    }
    var detail: String {
        if !requested || !prepared { return "Enable Auto HDR in the game's Display settings before starting it. Live comparison needs the updated DXMT renderer; Direct3D 12 is not measured." }
        if !measuring { return "Switch between HDR and SDR without restarting. Both use the same output layer, so SDR white stays stable. This choice lasts for this session." }
        if error != 0 { return "The frame could not be sampled. Resume the game and check the log's [hdr-signal] lines." }
        if !valid { return "Resume the game to apply the change and measure its next frames. Old measurements are cleared when you change a control." }
        let percent = samples > 0 ? 100 * Double(above) / Double(samples) : 0
        let source = appliedFlags & 2 != 0 ? "Test pattern" : "Game drawable"
        return String(format: "Last measured %@ · sampled peak %.2f× SDR white · %.1f%% of %u samples above SDR · display headroom %.2f×. EDR %@, linear %@, float %@. Sparse samples can miss small highlights.",
                      source, peak, percent, samples, headroom, edr ? "on" : "off", linear ? "yes" : "no", format == 115 ? "yes" : "no")
    }
    var hudText: String {
        if !requested { return "Auto HDR off" }
        if !prepared { return "Auto HDR unavailable" }
        let view = pattern ? "HDR test" : (enabled ? "Auto HDR" : "SDR reference")
        guard measuring && valid && ageMS <= 2000 && error == 0 else { return view + " · waiting" }
        return String(format: "%@ · sampled %.2f× / display %.2f×", view, peak, headroom)
    }
    var evidence: [String: String] {
        ["requested": requested ? "1" : "0", "view": requested ? (enabled ? "hdr" : "sdr") : "off", "pattern": pattern ? "1" : "0",
         "probe": measuring ? "1" : "0", "prepared": prepared ? "1" : "0", "valid": valid ? "1" : "0",
         "revision": String(revision), "sample_ns": String(sampleNS), "sample_age_ms": String(ageMS),
         "sampled_peak": String(format: "%.3f", peak), "headroom": String(format: "%.3f", headroom),
         "applied_peak_limit": String(format: "%.3f", appliedPeak), "applied_flags": String(appliedFlags),
         "above_sdr": String(above), "samples": String(samples), "invalid": String(invalid),
         "edr": edr ? "1" : "0", "linear": linear ? "1" : "0", "pixel_format": String(format),
         "probe_state": String(probeState), "probe_preparations_total": String(preparations),
         "probe_buffer_allocations_total": String(allocations), "headroom_refreshes_total": String(refreshes),
         "confirmed": confirmed ? "1" : "0", "error": String(error)]
    }
}

enum BCDHDRMenu {
    static func section(_ reading: BCDHDRReading) -> BCDMenuSection {
        BCDMenuSection(id: "hdr", rows: [
            BCDMenuRow(id: "hdr.view", title: "Auto HDR comparison",
                detail: reading.prepared ? "Applies when the game resumes. HDR readings are in performance HUD 4." : "Enable Auto HDR in this game's Display settings before launching a supported DXMT game.",
                kind: .choice([BCDChoice(id: "sdr", title: "SDR"), BCDChoice(id: "hdr", title: "HDR")],
                              selected: reading.enabled ? "hdr" : "sdr"), enabled: reading.prepared)
        ])
    }
    static func calibration(peak: String, pattern: String) -> BCDGameSettingGroup {
        BCDGameSettingGroup(id: "hdr-calibration", title: "HDR calibration (advanced)", symbol: "sun.max",
            settings: [
                BCDGameSetting(id: "hdr-peak", title: "Highlight ceiling", kind: .choice(
                    ["1", "1.25", "1.5", "2", "3", "4", "8"].map { BCDGameChoice(value: $0, label: $0 + "× SDR white") }),
                    value: peak, defaultValue: "2", note: "The display's current headroom may limit this further. 1× is the SDR reference. Restart the game after changing it."),
                BCDGameSetting(id: "hdr-pattern", title: "Reference pattern at start", kind: .choice([
                    BCDGameChoice(value: "0", label: "Off"), BCDGameChoice(value: "10", label: "10 seconds"), BCDGameChoice(value: "30", label: "30 seconds")]),
                    value: pattern, defaultValue: "0", note: "Shows five gray bands at the first supported DXMT frames, then returns to gameplay automatically. Requires Auto HDR. Performance HUD 4 labels pattern measurements separately.")
            ], note: "For checking this game's HDR output. Enable Auto HDR in Display first. Readings are multiples of SDR white, rather than measured panel nits.", parent: "display")
    }
}
