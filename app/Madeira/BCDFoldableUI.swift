// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// The interface side of the experimental foldable-iPhone support (BCDFoldable.swift): the iPad layout needs about 700 points, and the
// foldable's inner display is regular x regular even at 626 points wide (BCDFoldableLayout). Below that width every window of the
// scene has its horizontal size class put at compact, so the library, its sheets and a game's in-game layer draw the layout of an
// iPhone held upright; at 700 points or more the override is taken off again and nothing is different from today.
//
// The override is a trait of the window (UIView.traitOverrides, iOS 17), not an environment value set on one SwiftUI view, so the
// Settings cover, the game page and the in-game windows (the touch overlay, the starting screen: windows of their own) follow it
// together. A window that appears later gets it when it becomes visible. (Tried in the simulator, 2026-10-25: the same override on
// the UIWindowScene itself did nothing to the SwiftUI environment; on each window, and on each window's root controller, it did.)
//
// Nothing here runs unless `BCDFoldable.active`: BCDFoldableWatch adds nothing to the view then.

enum BCDFoldableSceneClass {
    /// What the windows should have now: set by `follow`, applied to a window that appears afterwards.
    private static var wanted = false
    private static var watching = false

    /// Puts the horizontal size class of every window of `scene` at compact while `width` (the window's, in points) is under the iPad
    /// layout's minimum and takes the override off otherwise. Idempotent. Returns whether it changed anything.
    @MainActor @discardableResult
    static func follow(scene: UIWindowScene, width: Double) -> Bool {
        watchNewWindows()
        wanted = BCDFoldableLayout.compact(forWidth: width)
        var changed = false
        for window in scene.windows where apply(wanted, to: window) { changed = true }
        return changed
    }

    @MainActor private static func apply(_ want: Bool, to window: UIWindow) -> Bool {
        guard want != window.traitOverrides.contains(UITraitHorizontalSizeClass.self) else { return false }
        if want { window.traitOverrides.horizontalSizeClass = .compact } else { window.traitOverrides.remove(UITraitHorizontalSizeClass.self) }
        return true
    }

    /// The touch overlay, the starting screen and the keyboard's window are made while a session starts, after the last layout pass.
    @MainActor private static func watchNewWindows() {
        guard !watching else { return }
        watching = true
        NotificationCenter.default.addObserver(forName: UIWindow.didBecomeVisibleNotification, object: nil, queue: .main) { note in
            guard let window = note.object as? UIWindow else { return }
            MainActor.assumeIsolated { _ = apply(wanted, to: window) }
        }
    }
}

/// A modifier for the app's root: on the foldable iPhone with its switch on, a zero-cost UIKit view behind the content follows the
/// window's width on every layout pass (the library, a game's session: the root shows one or the other). Otherwise the content as it is.
struct BCDFoldableWatch: ViewModifier {
    @ViewBuilder func body(content: Content) -> some View {
        if BCDFoldable.active {
            content.background(BCDFoldableProbe())
        } else {
            content
        }
    }
}

private struct BCDFoldableProbe: UIViewRepresentable {
    func makeUIView(context: Context) -> BCDFoldableProbeView { BCDFoldableProbeView() }
    func updateUIView(_ view: BCDFoldableProbeView, context: Context) {}
}

/// Clear, untouchable, as large as the content: its layout follows every change of the window's size.
final class BCDFoldableProbeView: UIView {
    override init(frame: CGRect) {
        super.init(frame: frame)
        isUserInteractionEnabled = false
        backgroundColor = .clear
    }
    required init?(coder: NSCoder) { fatalError() }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        setNeedsLayout()
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        guard let window, let scene = window.windowScene else { return }
        BCDFoldableSceneClass.follow(scene: scene, width: Double(window.bounds.width))
    }
}
