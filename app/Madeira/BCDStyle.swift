// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// Shared semantic palette. The historical color names remain as aliases so every BCD
/// screen follows the selected profile, including panels hosted in their own windows.
enum BCDStyle {
    static var ground: Color { BCDThemeStore.shared.color("ground") }
    static var groundTop: Color { BCDThemeStore.shared.color("groundTop") }
    static var surface: Color { BCDThemeStore.shared.color("surface") }
    static var surfaceRaised: Color { BCDThemeStore.shared.color("surfaceRaised") }
    static var stroke: Color { BCDThemeStore.shared.color("stroke") }
    static var text: Color { BCDThemeStore.shared.color("text") }
    static var textSoft: Color { BCDThemeStore.shared.color("textSoft") }
    static var violet: Color { BCDThemeStore.shared.color("accent") }
    static var fuchsia: Color { BCDThemeStore.shared.color("accentSecondary") }
    static var lavender: Color { BCDThemeStore.shared.color("accentSoft") }
    static var mint: Color { BCDThemeStore.shared.color("success") }
    static var alert: Color { BCDThemeStore.shared.color("danger") }
    static var amber: Color { BCDThemeStore.shared.color("warning") }
    static var panel: Color { BCDThemeStore.shared.color("panel") }
    static var onAccent: Color { BCDThemeStore.shared.color("onAccent") }

    static var glow: LinearGradient { LinearGradient(colors: [violet, fuchsia], startPoint: .leading, endPoint: .trailing) }
    static var backdrop: LinearGradient { LinearGradient(colors: [groundTop, ground, ground], startPoint: .top, endPoint: .bottom) }

    static func title(_ size: CGFloat = 32) -> Font { .system(size: size, weight: .heavy, design: .rounded) }
    static func heading(_ size: CGFloat = 19) -> Font { .system(size: size, weight: .bold, design: .rounded) }
    static func body(_ size: CGFloat = 16) -> Font { .system(size: size, weight: .regular, design: .rounded) }
    static func label(_ size: CGFloat = 15) -> Font { .system(size: size, weight: .semibold, design: .rounded) }
}

/// The full-screen ground behind BCD's own screens, with the logo's glow at the top.
struct BCDBackdrop: View {
    var body: some View {
        // The light is an overlay, so it never sizes the backdrop: a fixed 420-point light
        // in the layout made screens shorter than that (a phone on its side) taller than the
        // screen, pushing their top and bottom bars off it.
        BCDStyle.backdrop
            .overlay(alignment: .top) {
                RadialGradient(colors: [BCDStyle.violet.opacity(0.35), .clear], center: .top, startRadius: 0, endRadius: 420)
                    .frame(height: 420)
            }
            .clipped()
            .ignoresSafeArea()
    }
}

/// The BCD logo (the app icon's artwork, 256 px: enough for 84 pt at 3x) with the platform's corner and a soft glow.
struct BCDLogoMark: View {
    var size: CGFloat = 76
    var body: some View {
        Image("BCDLogo")
            .resizable()
            .interpolation(.high)
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.2237, style: .continuous))
            .shadow(color: BCDStyle.fuchsia.opacity(0.45), radius: size * 0.25)
            .accessibilityLabel("BCD")
    }
}

/// The main action: a glowing violet-to-fuchsia capsule.
struct BCDPrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(BCDStyle.label(17))
            .foregroundStyle(BCDStyle.onAccent)
            .frame(maxWidth: .infinity, minHeight: 54)
            .background(BCDStyle.glow, in: Capsule())
            .shadow(color: BCDStyle.fuchsia.opacity(configuration.isPressed ? 0.15 : 0.35), radius: 14, y: 4)
            .opacity(enabled ? 1 : 0.45)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

/// A quiet second choice: lavender text, full width so it is easy to reach.
struct BCDSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(BCDStyle.label(16))
            .foregroundStyle(BCDStyle.lavender.opacity(configuration.isPressed ? 0.6 : 1))
            .frame(maxWidth: .infinity, minHeight: 46)
            .contentShape(Rectangle())
    }
}

/// An outlined alternative to the primary action.
struct BCDOutlineButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(BCDStyle.label(16))
            .foregroundStyle(BCDStyle.text)
            .frame(maxWidth: .infinity, minHeight: 50)
            .background(BCDStyle.surface.opacity(configuration.isPressed ? 0.6 : 1), in: Capsule())
            .overlay(Capsule().strokeBorder(BCDStyle.glow, lineWidth: 1.5))
    }
}

extension View {
    /// A BCD card: raised surface, hairline border, rounded corners.
    func bcdCard(padding: CGFloat = 16) -> some View {
        self.padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
    }
}
