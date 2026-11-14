// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Porthole authors. The geometry in this file (BCDFloatingStick) was taken from
// https://github.com/tylerkaska112/Porthole (commit 99ec315, app/Sources/Core/FloatingStick.swift) under GPL-3.0-or-later with the
// Madeira Converter Exception. Modified by BCD (thebcd69), November 2026: BCD's names and words; the setting is the in-game menu's
// (BCDSessionFeel.floatingSticks) instead of an app-wide one, and a control in the zone keeps its touches by lying above it
// instead of by a list of neighbours. See THIRD-PARTY-NOTICES.md.

import CoreGraphics

// Floating sticks for BCD's touch controls (Porthole port, item 9). Plain geometry: TouchGamepad.swift's surface, BCDTouchControls.swift's
// layer and the controls' window (ContentView.swift, hitsInteractive) use it, and tests/host/check-touch-floating.py compiles it on the host.

/// madeira-bcd (Porthole port, item 9): floating sticks (the in-game menu's touch settings, BCDTouchFeel.floatingSticks; off by
/// default). A controller stick also takes a touch that lands in its zone, its own frame grown a little, unless a control there takes it;
/// its ring is then drawn where the thumb landed, moved only as far as it takes to keep the whole ring on the screen. Porthole's geometry
/// (FloatingStick.swift); the thumb's push is measured from the ring's centre, as Porthole's sticks do (HUD.swift).
enum BCDFloatingStick {
    /// How far the zone reaches beyond the stick's frame, in sticks' sizes.
    static let reachAcross: CGFloat = 1.0, reachUpDown: CGFloat = 0.8

    /// The area in which a floating stick takes a touch.
    static func zone(of frame: CGRect) -> CGRect {
        frame.insetBy(dx: -frame.width * reachAcross, dy: -frame.height * reachUpDown)
    }

    /// Where the ring is centred when a thumb lands at `touch`: on it, or the nearest place that keeps the whole ring (a circle of
    /// `diameter`) inside `field`. With no field, or one too small for the ring, the thumb's own point.
    static func centre(touch: CGPoint, diameter: CGFloat, field: CGRect?) -> CGPoint {
        guard let field, field.width >= diameter, field.height >= diameter else { return touch }
        let r = diameter / 2
        return CGPoint(x: min(max(touch.x, field.minX + r), field.maxX - r), y: min(max(touch.y, field.minY + r), field.maxY - r))
    }
}
