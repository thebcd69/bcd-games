// SPDX-License-Identifier: GPL-3.0-or-later
// Additional permission: Madeira Converter Exception, version 1; see LICENSE-EXCEPTION.md.
// Compiled with the production BCDHDRReading value type by the host check.
var r = BCDHDRReading(requested: true, enabled: true, measuring: true, prepared: true, valid: true,
    edr: true, linear: true, format: 115, appliedFlags: 9,
    samples: 2304, above: 123, peak: 1.7, headroom: 2)
precondition(r.confirmed && r.title == "Game frame contains an HDR signal")
r.appliedFlags = 11
precondition(r.title == "HDR test pattern confirmed")
r.appliedFlags = 8
precondition(!r.confirmed && r.title == "SDR comparison")
r.appliedFlags = 9
for change: (inout BCDHDRReading) -> Void in [
    { $0.requested = false }, { $0.ageMS = 2500 }, { $0.valid = false }, { $0.prepared = false }, { $0.measuring = false },
    { $0.edr = false }, { $0.linear = false }, { $0.format = 80 },
    { $0.samples = 0 }, { $0.above = 0 }, { $0.invalid = 1 },
    { $0.peak = 1 }, { $0.headroom = 1 }, { $0.error = 1 }
] {
    var missing = r; change(&missing); precondition(!missing.confirmed)
}
r.above = 0; r.peak = 1
precondition(r.title == "No HDR highlights in sampled pixels")
precondition(r.evidence["view"] == "hdr" && r.evidence["sampled_peak"] == "1.000")
let json = try JSONSerialization.data(withJSONObject: r.evidence, options: [.sortedKeys])
let decoded = try JSONSerialization.jsonObject(with: json) as! [String: String]
precondition(decoded == r.evidence)
r.prepared = false
precondition(r.title == "Auto HDR unavailable for this output")
r.requested = false
precondition(r.title == "Auto HDR off")
print("PASS: HDR evidence UI requires measured game pixels, float/linear/EDR output and display headroom; distinguishes pattern and SDR")
