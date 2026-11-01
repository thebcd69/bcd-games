// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import UIKit
import QuartzCore

// The engine side of the experimental foldable-iPhone support (BCDFoldable.swift): it follows the size of the game's
// window while a game runs. Left out of the preview build (tools/ui-preview.py, ENGINE_BOUND).
//
// Why it is needed: rotation re-lays the game out because MetalBackedView lays out again, and re-frames the touch-control
// window because TouchControlsHost.attach() runs on UIDevice's orientation notification. A fold or an unfold changes the
// scene's size without any rotation, so no notification says so and the control window would keep its old frame. The only
// thing that always happens is the layout pass of the game view, and this follows it.
//
// Nothing here runs unless `BCDFoldable.active` (the foldable model and the experimental switch); the single call into this
// file is MetalBackedView.layoutSubviews, behind that test. Nothing is restarted or paused: a fold re-frames two windows and
// writes one log line.

@MainActor enum BCDFoldableLive {
    private static var tracker = BCDFoldableTracker()
    private static weak var tracked: UIView?
    private static var lastLogged: CFTimeInterval = 0
    private static var pendingLog: DispatchWorkItem?
    private static var pendingSettle: DispatchWorkItem?

    /// One layout pass of the game view (`view`, in `window`).
    static func layoutPass(_ view: UIView, in window: UIWindow) {
        if tracked !== view {
            // A new session has a new game view.
            tracked = view
            tracker.reset()
            lastLogged = 0
            pendingLog?.cancel(); pendingLog = nil
        }
        let safe = window.safeAreaInsets
        let now = BCDFoldableSnapshot(width: Double(window.bounds.width), height: Double(window.bounds.height),
                                      top: Double(safe.top), left: Double(safe.left), bottom: Double(safe.bottom), right: Double(safe.right))
        let verdict = tracker.note(now)
        switch verdict {
        case .unchanged:
            return
        case .first:
            report(.first, window)
        case .resized, .insets:
            // The control window is framed to the scene by hand (TouchControlsHost.attach), the game view by layout; the
            // layer's own settle pass (refreshDisplayMode) waits for the drawable size. UIKit reports pre-fold bounds while
            // a window is still animating, so the frame is also taken again once it has settled.
            DispatchQueue.main.async { TouchControlsHost.attach() }   // not inside this layout pass
            MetalBackedView.refreshDisplayMode(reason: "foldable")
            settleLater()
            report(verdict, window)
        }
    }

    private static func settleLater() {
        pendingSettle?.cancel()
        let item = DispatchWorkItem { TouchControlsHost.attach() }
        pendingSettle = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35, execute: item)
    }

    /// One line per change; a burst of passes while a window animates gives the first at once and the last half a second later.
    private static func report(_ verdict: BCDFoldableTracker.Verdict, _ window: UIWindow) {
        pendingLog?.cancel(); pendingLog = nil
        let t = CACurrentMediaTime()
        if verdict == .first || t - lastLogged >= 0.5 { emit(verdict, window); return }
        let item = DispatchWorkItem { [weak window] in
            guard let window else { return }
            emit(verdict, window)
        }
        pendingLog = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5, execute: item)
    }

    private static func emit(_ verdict: BCDFoldableTracker.Verdict, _ window: UIWindow) {
        guard let now = tracker.last else { return }
        lastLogged = CACurrentMediaTime()
        LogStore.shared.log(BCDFoldableTracker.line(verdict, now: now, was: tracker.previous, changes: tracker.changes,
                                                    extra: facts(window)))
    }

    /// What only UIKit knows: the scene against its screen, the scale, the size classes. This is what a person with the device
    /// sends in a log, since no simulator of the device is at hand.
    private static func facts(_ window: UIWindow) -> String {
        guard let scene = window.windowScene else { return "scene=none" }
        let screen = scene.screen
        let sceneSize = scene.coordinateSpace.bounds.size
        func word(_ c: UIUserInterfaceSizeClass) -> String { c == .compact ? "compact" : c == .regular ? "regular" : "unspecified" }
        let traits = window.traitCollection
        return String(format: "scene=%.0fx%.0f screen=%.0fx%.0f pt scale=%.2f native=%.0fx%.0f px fps=%d classes=%@x%@ scenes=%d",
                      sceneSize.width, sceneSize.height, screen.bounds.width, screen.bounds.height, screen.scale,
                      screen.nativeBounds.width, screen.nativeBounds.height, screen.maximumFramesPerSecond,
                      word(traits.horizontalSizeClass), word(traits.verticalSizeClass),
                      UIApplication.shared.connectedScenes.count)
    }
}
