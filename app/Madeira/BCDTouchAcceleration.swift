// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

/// Optional gain for finger-driven relative mouse movement. Measures swipe speed in
/// points per second, so the same gesture feels the same at different touch event rates.
/// At or below 200 pt/s the original speed is unchanged; at 1200 pt/s it reaches 2.5x.
enum BCDTouchAcceleration {
    static func multiplier(dx: Double, dy: Double, elapsed: Double, enabled: Bool) -> Double {
        guard enabled, dx.isFinite, dy.isFinite, elapsed.isFinite, elapsed > 0 else { return 1 }
        let speed = hypot(dx, dy) / elapsed
        guard speed.isFinite else { return 1 }
        // Touch timestamps are binary floating-point values (e.g. 1.01 - 1),
        // so a nominal 1200 pt/s sample can land infinitesimally below the cap.
        // Snap only within a negligible floating-point tolerance.
        let fraction = speed >= 1200 - 1e-8 ? 1 : min(1, max(0, (speed - 200) / 1000))
        return 1 + 1.5 * fraction
    }
}
