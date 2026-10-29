// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// BCD's on-screen touch controls over a running game (docs/BCD-UI-ARCHITECTURE.md).
//
// Drawn to cost the game nothing: plain fills and strokes with their opacity in the colour (no
// blur, no shadow, no material, no group opacity), and nothing moves unless a finger does. A
// press only changes colour; a stick's knob follows the thumb. No animation at all: SwiftUI
// animations over a running game have crashed the app before.
//
// Touches: a controller control (A, LS, LT, ...) hands its touches to the touch gamepad's own
// surface (BCDTouchInput.padSurface), the same one the game always got its input through. Every
// other control has a BCDTouchKeySurface: one finger, from touch-down to lift, through
// BCDTouchPress (BCDTouchCore.swift). Both cover exactly the area they always covered.

// MARK: - Where touches and edits go

@MainActor protocol BCDTouchInput: AnyObject {
    /// An event for the game.
    func send(_ event: BCDTouchEvent)
    /// The surface that turns touches on a controller control into player 1's input. It fills
    /// the control's frame; `changed` reports the thumb's deflection (x right, y down, within
    /// the unit circle) and whether the control is held.
    func padSurface(control: UUID, name: String, changed: @escaping (CGSize, Bool) -> Void) -> AnyView
}

/// The editor's hands on the layout.
@MainActor protocol BCDTouchArranging: AnyObject {
    func select(_ id: UUID?)
    func move(_ id: UUID, x: Double, y: Double)
}

// MARK: - Colours

/// BCD's palette for drawing over a game: the night-violet ground, kept see-through, with the
/// logo's violet-to-fuchsia light for a pressed control.
enum BCDTouchPalette {
    static var fill: Color { BCDStyle.ground }
    /// The player's colour for the rims and labels (BCDTouchFeel.tint, set from the in-game menu), else BCD's own.
    static var rim: Color { BCDTouchFeel.tint.map { Color(red: $0.r, green: $0.g, blue: $0.b) } ?? BCDStyle.lavender }
    static var label: Color { BCDTouchFeel.tint.map { Color(red: $0.r, green: $0.g, blue: $0.b) } ?? BCDStyle.text }
    static var selected: Color { BCDStyle.fuchsia }
    static func pressed(_ opacity: Double) -> LinearGradient {
        LinearGradient(colors: [BCDStyle.violet.opacity(0.92 * opacity), BCDStyle.fuchsia.opacity(0.92 * opacity)],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
    }
    /// The four face buttons keep their colours, so a layout reads at a glance.
    static func face(_ name: String?) -> Color? {
        switch name {
        case "A": return BCDStyle.mint
        case "B": return BCDStyle.alert
        case "X": return Color(red: 0.376, green: 0.647, blue: 0.980)   // #60A5FA
        case "Y": return Color(red: 0.984, green: 0.749, blue: 0.141)   // #FBBF24
        default: return nil
        }
    }
}

// MARK: - The layer

/// Every control of the layout, at its place on the screen.
struct BCDTouchLayer: View {
    let items: [BCDTouchItem]
    let screen: CGSize
    /// The whole layout's size (the game's Control size).
    var layoutScale = 1.0
    /// Baked into each control's colours (a game's Control opacity); 1 while editing.
    var opacity = 1.0
    var editing = false
    var selected: UUID? = nil
    let input: BCDTouchInput
    var arranging: BCDTouchArranging? = nil
    /// How the controls look (round 604, BCDTouchLook.swift); BCD's own unless the player chose another.
    var look = BCDTouchLook()

    var body: some View {
        ZStack(alignment: .topLeading) {
            if editing, let arranging {
                // A tap on empty space puts the selected control down.
                Color.clear
                    .contentShape(Rectangle())
                    .onTapGesture { arranging.select(nil) }
            }
            ForEach(items) { item in
                BCDTouchControl(item: item, screen: screen, layoutScale: layoutScale, opacity: opacity,
                                editing: editing, selected: editing && selected == item.id,
                                input: input, arranging: arranging, look: look)
                    .equatable()
            }
        }
        .frame(width: screen.width, height: screen.height, alignment: .topLeading)
        .transaction { $0.animation = nil }
    }
}

/// One control: its face, and either its touch surface (play) or the editor's drag (editing).
struct BCDTouchControl: View, Equatable {
    let item: BCDTouchItem
    let screen: CGSize
    let layoutScale: Double
    let opacity: Double
    let editing: Bool
    let selected: Bool
    let input: BCDTouchInput
    let arranging: BCDTouchArranging?
    let look: BCDTouchLook

    @State private var down = false
    @State private var direction = -1
    @State private var vector = CGSize.zero
    @State private var dragBase: CGPoint?

    nonisolated static func == (a: BCDTouchControl, b: BCDTouchControl) -> Bool {
        a.item == b.item && a.screen == b.screen && a.layoutScale == b.layoutScale && a.opacity == b.opacity
            && a.editing == b.editing && a.selected == b.selected && a.input === b.input && a.arranging === b.arranging && a.look == b.look
    }

    private var diameter: CGFloat { CGFloat(BCDTouchGeometry.diameter(scale: item.scale, layoutScale: layoutScale)) }
    private var size: CGSize { BCDTouchGeometry.size(item.action.shape, diameter: Double(diameter)) }

    var body: some View {
        BCDTouchFace(action: item.action, diameter: diameter, down: down, direction: direction, vector: vector,
                     opacity: opacity, selected: selected)
            .environment(\.bcdTouchLook, look)
            .frame(width: size.width, height: size.height)
            .overlay { if !editing { surface } }
            .position(x: CGFloat(item.x) * screen.width, y: CGFloat(item.y) * screen.height)
            .gesture(editDrag, including: editing ? .all : .subviews)
            .onChange(of: editing) { _, _ in rest() }
            .onChange(of: screen) { _, _ in rest() }
            .onChange(of: item.action) { _, _ in rest() }
            .onDisappear { rest() }
            .accessibilityElement()
            .accessibilityLabel(BCDTouchNames.title(item.action))
    }

    @ViewBuilder private var surface: some View {
        if let name = item.action.padName {
            input.padSurface(control: item.id, name: name) { deflection, held in
                vector = deflection
                down = held
            }
        } else {
            BCDTouchKeySurface(action: item.action, diameter: Double(diameter), input: input) { held, toward in
                down = held
                direction = toward
            }
        }
    }

    private func rest() {
        down = false
        direction = -1
        vector = .zero
    }

    /// Editing: touch a control to select it, drag it to move it.
    private var editDrag: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                guard editing, let arranging else { return }
                arranging.select(item.id)
                if item.locked { return }   // selected to unlock it, resize nothing, move nothing
                if dragBase == nil { dragBase = CGPoint(x: item.x, y: item.y) }
                let base = dragBase ?? .zero
                arranging.move(item.id,
                               x: BCDTouchGeometry.clampPlace(Double(base.x) + Double(value.translation.width / screen.width)),
                               y: BCDTouchGeometry.clampPlace(Double(base.y) + Double(value.translation.height / screen.height)))
            }
            .onEnded { _ in dragBase = nil }
    }
}

// MARK: - The face

/// How a control looks: a see-through night-violet face with a lavender rim and its label;
/// pressed, it fills with the logo's light. Sticks are a ring with a knob that follows the thumb.
struct BCDTouchFace: View {
    let action: BCDTouchAction
    let diameter: CGFloat
    var down = false
    /// A key stick's direction (-1 centred, else 0 up and on clockwise).
    var direction = -1
    /// A controller stick's deflection (x right, y down, unit circle).
    var vector = CGSize.zero
    var opacity = 1.0
    /// Selected in the editor: a fuchsia ring around it.
    var selected = false
    @Environment(\.bcdTouchLook) var look

    private var paint: BCDTouchPaint { BCDTouchPaint(look: look) }
    private var size: CGSize { BCDTouchGeometry.size(action.shape, diameter: Double(diameter)) }

    var body: some View {
        Group {
            if action.isStick { stick } else { button }
        }
        .frame(width: size.width, height: size.height)
        .overlay {
            if selected {
                outline.stroke(BCDTouchPalette.selected, lineWidth: 2.5).padding(-5)
            }
        }
    }

    private var outline: AnyShape {
        switch action.shape {
        case .wide: return AnyShape(RoundedRectangle(cornerRadius: size.height * 0.32, style: .continuous))
        case .pill: return AnyShape(Capsule())
        case .round, .small: return AnyShape(Circle())
        }
    }

    private var tint: Color? { paint.tint(pad: action.padName) }
    private var rimWidth: CGFloat { max(1.2, diameter * 0.024) * (down ? paint.pressedRimFactor : 1) }

    // MARK: Buttons

    private var button: some View {
        ZStack {
            if down {
                outline.fill(paint.pressedFill(opacity))
            } else {
                outline.fill(paint.fill.opacity(paint.fillStrength * opacity))
            }
            outline.stroke(rimColor, lineWidth: rimWidth)
            label
        }
    }

    private var rimColor: Color {
        if down { return paint.pressedRim.opacity(0.95 * opacity) }
        if let tint { return tint.opacity(0.8 * opacity) }
        if action == .none { return paint.rim.opacity(0.3 * opacity) }
        return paint.rim.opacity(0.6 * opacity)
    }

    private var labelColor: Color {
        if down { return paint.pressedLabel.opacity(opacity) }
        if let tint, paint.tintsLabels { return tint.opacity(opacity) }
        if action == .none { return paint.label.opacity(0.45 * opacity) }
        return paint.label.opacity(0.94 * opacity)
    }

    /// Type sized to the control: a letter fills more of a button than a word does.
    private func text(_ s: String, box: CGFloat) -> some View {
        let scale: CGFloat = s.count <= 1 ? 0.44 : s.count == 2 ? 0.36 : s.count == 3 ? 0.29 : s.count == 4 ? 0.24 : 0.2
        return Text(s)
            .font(.system(size: max(9, box * scale), weight: .bold, design: .rounded))
            .lineLimit(1)
            .minimumScaleFactor(0.5)
            .foregroundStyle(labelColor)
            .padding(.horizontal, box * 0.08)
    }

    private func symbol(_ name: String, _ fraction: CGFloat) -> some View {
        Image(systemName: name)
            .font(.system(size: max(9, min(size.width, size.height) * fraction), weight: .bold))
            .foregroundStyle(labelColor)
    }

    @ViewBuilder private var label: some View {
        let box = min(size.width, size.height)
        switch action {
        case .none:
            text("–", box: box)
        case .key(let vk):
            text(BCDTouchNames.keyLabel(vk), box: box)
        case .mouseLeft, .mouseRight:
            BCDMouseGlyph(right: action == .mouseRight, color: labelColor)
                .frame(width: box * 0.34, height: box * 0.5)
        case .keyboard:
            symbol("keyboard", 0.36)
        case .keyStick:
            EmptyView()
        case .pad(let name):
            padLabel(name, box: box)
        }
    }

    /// A controller input's face: its letters or symbol, or the PlayStation symbols of the player's look (BCDTouchLook.swift).
    @ViewBuilder private func padLabel(_ name: String, box: CGFloat) -> some View {
        if let glyph = look.glyph(pad: name) {
            switch glyph {
            case .symbol(let symbolName): symbol(symbolName, 0.36)
            case .text(let words): text(words, box: size.height * 1.05)
            }
        } else {
            switch name {
            case "D↑": symbol("chevron.up", 0.36)
            case "D↓": symbol("chevron.down", 0.36)
            case "D←": symbol("chevron.left", 0.36)
            case "D→": symbol("chevron.right", 0.36)
            case "Menu": symbol("line.3.horizontal", 0.5)
            case "View": symbol("rectangle.on.rectangle", 0.46)
            case "LB", "RB", "LT", "RT": text(name, box: size.height * 1.05)
            default: text(name, box: box)
            }
        }
    }

    // MARK: Sticks

    private var stick: some View {
        let knob = diameter * 0.44
        let travel = diameter * 0.28
        return ZStack {
            Circle().fill(paint.fill.opacity(paint.stickFillStrength * opacity))
            Circle().stroke(down ? paint.pressedRim.opacity(0.9 * opacity) : paint.rim.opacity(0.55 * opacity),
                            lineWidth: rimWidth)
            // The gate the knob travels in.
            Circle().stroke(paint.rim.opacity(0.18 * opacity), lineWidth: 1)
                .frame(width: diameter * 0.74, height: diameter * 0.74)
            if let stick = action.keyStick { compass(stick) }
            ZStack {
                if down {
                    Circle().fill(paint.knobPressedFill(opacity))
                } else {
                    Circle().fill(paint.rim.opacity(0.4 * opacity))
                }
                Circle().stroke(Color.white.opacity(0.6 * opacity), lineWidth: 1)
                if let name = action.padName {
                    Text(BCDTouchNames.padLabel(name))
                        .font(.system(size: max(8, knob * 0.42), weight: .heavy, design: .rounded))
                        .foregroundStyle(Color.white.opacity(0.95 * opacity))
                }
            }
            .frame(width: knob, height: knob)
            .offset(knobOffset(travel))
        }
    }

    private func knobOffset(_ travel: CGFloat) -> CGSize {
        if action.keyStick != nil {
            guard direction >= 0 else { return .zero }
            let angle = Double(direction) * .pi / 4
            return CGSize(width: travel * CGFloat(sin(angle)), height: -travel * CGFloat(cos(angle)))
        }
        return CGSize(width: vector.width * travel, height: vector.height * travel)
    }

    /// A key stick's four keys around its ring; the held ones light up.
    private func compass(_ stick: BCDKeyStick) -> some View {
        let held = Set(BCDTouchRules.held(direction, stick.keys))
        let r = diameter * 0.37
        let spots: [(Int32, CGSize)] = [
            (stick.keys[0], CGSize(width: 0, height: -r)), (stick.keys[1], CGSize(width: r, height: 0)),
            (stick.keys[2], CGSize(width: 0, height: r)), (stick.keys[3], CGSize(width: -r, height: 0)),
        ]
        return ZStack {
            ForEach(spots, id: \.0) { key, offset in
                Text(BCDTouchNames.keyLabel(key))
                    .font(.system(size: max(7, diameter * 0.14), weight: .heavy, design: .rounded))
                    .foregroundStyle(held.contains(key) ? paint.pressedLabel.opacity(opacity)
                                                        : paint.label.opacity(0.62 * opacity))
                    .offset(offset)
            }
        }
    }
}

/// A mouse with its left or right button filled.
struct BCDMouseGlyph: View {
    let right: Bool
    let color: Color
    var body: some View {
        GeometryReader { g in
            let w = g.size.width, h = g.size.height, split = h * 0.42
            ZStack {
                RoundedRectangle(cornerRadius: w * 0.5, style: .continuous)
                    .stroke(color, lineWidth: max(1.2, w * 0.1))
                Path { p in
                    p.move(to: CGPoint(x: w / 2, y: 0))
                    p.addLine(to: CGPoint(x: w / 2, y: split))
                    p.move(to: CGPoint(x: 0, y: split))
                    p.addLine(to: CGPoint(x: w, y: split))
                }
                .stroke(color, lineWidth: max(1, w * 0.08))
                Path { p in
                    // The pressed button: the top quarter on its side, inside the outline.
                    let inset = w * 0.12
                    let x0 = right ? w / 2 + w * 0.04 : inset, x1 = right ? w - inset : w / 2 - w * 0.04
                    p.addRoundedRect(in: CGRect(x: x0, y: inset, width: x1 - x0, height: split - inset * 1.4),
                                     cornerSize: CGSize(width: w * 0.12, height: w * 0.12))
                }
                .fill(color)
            }
        }
    }
}

// MARK: - The key surface (UIKit)

/// The touch area of a key, mouse, keyboard or key-stick control: the circle the control is
/// drawn in. It follows the first finger down until that finger lifts (or is cancelled, or the
/// app leaves the foreground, or the control leaves the screen); other fingers on it are held
/// and ignored, so they never reach the game as a mouse.
struct BCDTouchKeySurface: UIViewRepresentable {
    let action: BCDTouchAction
    let diameter: Double
    let input: BCDTouchInput
    /// Pressed, and a key stick's direction.
    let changed: (Bool, Int) -> Void

    func makeUIView(context: Context) -> BCDTouchKeyView { BCDTouchKeyView() }
    func updateUIView(_ view: BCDTouchKeyView, context: Context) {
        view.configure(action: action, diameter: diameter, input: input, changed: changed)
    }
    static func dismantleUIView(_ view: BCDTouchKeyView, coordinator: ()) { view.finish() }
}

final class BCDTouchKeyView: UIView {
    private var press: BCDTouchPress?
    private weak var input: BCDTouchInput?
    /// The finger this control follows (UIKit reuses touch objects, so it is kept by identity).
    private var finger: ObjectIdentifier?
    private var start = CGPoint.zero
    private var diameter = 64.0

    init() {
        super.init(frame: .zero)
        isMultipleTouchEnabled = true
        backgroundColor = .clear
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted),
                                               name: UIApplication.willResignActiveNotification, object: nil)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    deinit { NotificationCenter.default.removeObserver(self) }

    func configure(action: BCDTouchAction, diameter: Double, input: BCDTouchInput, changed: @escaping (Bool, Int) -> Void) {
        self.diameter = diameter
        self.input = input
        if let press {
            press.rebind(action)
            press.diameter = diameter
            press.changed = changed
        } else {
            let p = BCDTouchPress(action: action, diameter: diameter,
                                  send: { [weak self] event in self?.input?.send(event) },
                                  tap: { BCDTouchHaptic.tap() })
            p.changed = changed
            press = p
        }
    }

    /// A circle of the control's diameter around its centre.
    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        let dx = Double(point.x - bounds.midX), dy = Double(point.y - bounds.midY)
        return (dx * dx + dy * dy).squareRoot() <= diameter / 2
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard UIApplication.shared.applicationState == .active else { interrupted(); return }
        guard finger == nil, let touch = touches.first else { return }
        finger = ObjectIdentifier(touch)
        start = touch.location(in: self)
        press?.begin()
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        guard UIApplication.shared.applicationState == .active else { interrupted(); return }
        guard let finger, let touch = touches.first(where: { ObjectIdentifier($0) == finger }) else { return }
        let p = touch.location(in: self)
        press?.move(dx: Double(p.x - start.x), dy: Double(p.y - start.y))
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) { lift(touches) }
    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) { lift(touches) }

    private func lift(_ touches: Set<UITouch>) {
        guard let finger, touches.contains(where: { ObjectIdentifier($0) == finger }) else { return }
        finish()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil { finish() }
    }

    @objc private func interrupted() { finish() }

    /// Lets go of whatever this control holds.
    func finish() {
        finger = nil
        press?.end()
    }
}

// MARK: - The bar over a game outside the library

/// The round buttons at the top of the screen while a game runs outside the library (the
/// developer interface): keyboard, controls on or off, layouts, edit and the menu. They sit
/// where the overlay's window takes touches: 44-point circles 10 points apart, centred, 10
/// points down.
struct BCDTouchPlayBar: View {
    let visible: Bool
    let showsLayouts: Bool
    let keyboard: () -> Void
    let toggleVisible: () -> Void
    let layouts: () -> Void
    let edit: () -> Void
    let menu: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            round("keyboard", "Keyboard", action: keyboard)
            round(visible ? "gamecontroller.fill" : "gamecontroller", visible ? "Hide touch controls" : "Show touch controls",
                  dim: !visible, action: toggleVisible)
            if showsLayouts { round("square.grid.2x2", "Layouts", action: layouts) }
            round("slider.horizontal.3", "Edit controls", action: edit)
            round("line.3.horizontal", "Menu", action: menu)
        }
        .padding(.top, 10)
    }

    private func round(_ symbol: String, _ label: String, dim: Bool = false, action: @escaping () -> Void) -> some View {
        Button { BCDTouchHaptic.tap(); action() } label: {
            Image(systemName: symbol)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(BCDStyle.text.opacity(dim ? 0.4 : 0.95))
                .frame(width: 44, height: 44)
        }
        .buttonStyle(BCDTouchRoundButtonStyle())
        .accessibilityLabel(label)
    }
}

/// A round chrome button over a game: a darker face while pressed, never animated.
struct BCDTouchRoundButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(Circle().fill(BCDStyle.ground.opacity(configuration.isPressed ? 0.82 : 0.58)))
            .overlay(Circle().stroke(configuration.isPressed ? BCDStyle.fuchsia.opacity(0.9) : BCDStyle.lavender.opacity(0.4),
                                     lineWidth: 1))
            .contentShape(Circle())
            .transaction { $0.animation = nil }
    }
}
