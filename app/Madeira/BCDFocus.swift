// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import GameController
import UIKit

// Controller navigation for BCD's interface (docs/BCD-UI-ARCHITECTURE.md).
//
// Every control a controller can reach is marked .bcdFocusable(id:action:). It reports its
// frame in the screen's coordinate space; the D-pad or left stick moves the focus to the
// nearest control in that direction, A runs its action, B goes back, and the shoulder,
// X, Y and Menu buttons go to whatever the current screen assigned them (BCDHints). The
// focused control wears the logo's light as a ring. Touch works as usual and hides the
// ring until a controller is used again. Nothing animates unless the focus moves, so the
// interface costs no GPU while nobody touches it.

enum BCDMove { case up, down, left, right }

enum BCDButton: Hashable {
    case a, b, x, y, leftShoulder, rightShoulder, menu, options
}

/// What one screen does with the buttons, and the hints the bottom bar shows for them.
struct BCDHint: Identifiable, Equatable {
    var id: BCDButton { button }
    var button: BCDButton
    var title: String
    static func == (a: BCDHint, b: BCDHint) -> Bool { a.button == b.button && a.title == b.title }
}

enum BCDRailPosition {
    static func offset(index: Int, count: Int, width: CGFloat, spacing: CGFloat, viewport: CGFloat, padding: CGFloat = 2) -> CGFloat {
        guard count > 0, width > 0, viewport > 0 else { return 0 }
        let content = CGFloat(count) * width + CGFloat(max(0, count - 1)) * spacing + 2 * padding
        let center = padding + CGFloat(max(0, min(index, count - 1))) * (width + spacing) + width / 2
        return max(0, min(max(0, content - viewport), center - viewport / 2))
    }
}

/// Home follows its visible sections rather than distances between moving scroll frames.
enum BCDHomeNavigation {
    static func target(from id: String, direction: BCDMove, recent: [String], desktop: [String],
                       hero: String, downloadsBelow: Bool, downloadsVisible: Bool = true, remembered: String?) -> String? {
        let returning = remembered.flatMap { recent.contains($0) ? $0 : nil } ?? recent.first
        if let index = recent.firstIndex(of: id) {
            switch direction {
            case .left: return recent[max(0, index - 1)]
            case .right: return recent[min(recent.count - 1, index + 1)]
            case .up: return hero
            case .down: return downloadsBelow ? "home.downloads" : desktop.first ?? id
            }
        }
        if let index = desktop.firstIndex(of: id) {
            switch direction {
            case .left: return desktop[max(0, index - 1)]
            case .right: return desktop[min(desktop.count - 1, index + 1)]
            case .up: return downloadsBelow ? "home.downloads" : returning ?? hero
            case .down: return id
            }
        }
        if id == hero {
            switch direction {
            case .down: return returning ?? id
            case .right: return downloadsBelow || !downloadsVisible ? id : "home.downloads"
            case .left, .up: return id
            }
        }
        if id == "home.downloads" {
            switch direction {
            case .up: return downloadsBelow ? returning ?? hero : id
            case .down: return downloadsBelow ? desktop.first ?? id : returning ?? hero
            case .left: return downloadsBelow ? id : hero
            case .right: return id
            }
        }
        return nil
    }
}

/// Logical grid positions remain reachable when a lazy grid has not materialized their views.
enum BCDGridNavigation {
    static func target(index: Int, columns: Int, count: Int, direction: BCDMove) -> Int? {
        guard count > 0, columns > 0, (0..<count).contains(index) else { return nil }
        switch direction {
        case .left: return max(0, index - 1)
        case .right: return min(count - 1, index + 1)
        case .up: return index < columns ? nil : index - columns
        case .down: return min(count - 1, index + columns)
        }
    }
}

@MainActor final class BCDFocus: ObservableObject {
    static let space = "bcd.focus"

    @Published var focused: String?
    /// The last input was a controller: the ring shows. Touch hides it.
    @Published private(set) var controllerActive = false
    @Published private(set) var hints: [BCDHint] = []

    private var frames: [String: CGRect] = [:]
    private var actions: [String: () -> Void] = [:]
    private var owners: [String: UUID] = [:]
    private var handlers: [BCDButton: () -> Void] = [:]
    private var navigators: [String: (UUID, (BCDMove) -> Bool)] = [:]
    private var observers: [NSObjectProtocol] = []
    private var repeatTask: Task<Void, Never>?
    private var heldMove: BCDMove?
    /// Set false while a game runs: no controller input is read for the interface.
    var enabled = true {
        didSet {
            if !enabled { stopRepeat() }
            updatePolling()
        }
    }
    /// A screen that leads the focus itself (Settings: its list, its rows in order, a value
    /// stepped with left and right) sees each direction first; true means it used the press.
    var steer: ((BCDMove) -> Bool)?

    init(watchControllers: Bool = true) {
        guard watchControllers else { return }
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: .GCControllerDidConnect, object: nil, queue: .main) { [weak self] note in
            guard let controller = note.object as? GCController else { return }
            MainActor.assumeIsolated { self?.attach(controller) }
        })
        observers.append(center.addObserver(forName: .GCControllerDidDisconnect, object: nil, queue: .main) { [weak self] note in
            guard let controller = note.object as? GCController else { return }
            MainActor.assumeIsolated { self?.detach(controller) }
        })
        observers.append(center.addObserver(forName: UIApplication.willResignActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.appActive = false; self?.updatePolling() }
        })
        observers.append(center.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.appActive = true; self?.updatePolling() }
        })
        GCController.controllers().forEach(attach)
    }

    // MARK: Registration (from .bcdFocusable)

    func register(_ id: String, frame: CGRect) { frames[id] = frame }
    /// Where a control is, in `BCDFocus.space` (a screen that scrolls keeps its ring in view with it).
    func frame(of id: String) -> CGRect? { frames[id] }
    /// `owner`: the view that marks the control. A turn of the device (or a layout change) builds the
    /// new view of an id before the old one goes: the old one's going must not take the new one's
    /// action, frame and ring with it, so only the latest owner can unregister the id.
    func setAction(_ id: String, _ action: @escaping () -> Void, owner: UUID? = nil) {
        actions[id] = action
        if let owner { owners[id] = owner }
    }
    func unregister(_ id: String, owner: UUID? = nil) {
        if let owner, let latest = owners[id], latest != owner { return }
        frames[id] = nil; actions[id] = nil; owners[id] = nil
        if focused == id { focused = nil }
    }

    /// The current screen's buttons and their hints, replaced whenever the screen changes.
    func setHandlers(_ list: [(BCDHint, () -> Void)]) {
        handlers = Dictionary(list.map { ($0.0.button, $0.1) }, uniquingKeysWith: { _, last in last })
        hints = list.map(\.0)
    }

    func setNavigator(_ prefix: String, owner: UUID, move: @escaping (BCDMove) -> Bool) {
        navigators[prefix] = (owner, move)
    }
    func removeNavigator(_ prefix: String, owner: UUID) {
        if navigators[prefix]?.0 == owner { navigators[prefix] = nil }
    }

    // MARK: Layers (a page over the shell)

    /// A page shown over the shell (a game's page, a panel on it, a question) holds the
    /// controller while it is up: the D-pad and A reach only controls whose id starts with its
    /// prefix, and its buttons replace the screen's. Closing it brings back the buttons, hints
    /// and focus of what lies under it. With no layer open nothing changes.
    private struct Layer {
        let prefix: String
        let handlers: [BCDButton: () -> Void]
        let hints: [BCDHint]
        let focused: String?
        let steer: ((BCDMove) -> Bool)?
    }
    private var layers: [Layer] = []
    private var rootPrefix: String?

    /// Native tabs may retain offscreen views; only the selected tab owns root focus.
    func scope(to prefix: String) {
        rootPrefix = prefix
        if let id = focused, !reachable(id) { focused = nil }
    }

    func openLayer(_ prefix: String) {
        guard layers.last?.prefix != prefix else { return }
        layers.append(Layer(prefix: prefix, handlers: handlers, hints: hints, focused: focused, steer: steer))
        steer = nil
        if let id = focused, !id.hasPrefix(prefix) { focused = nil }
    }

    func closeLayer(_ prefix: String) {
        guard let index = layers.lastIndex(where: { $0.prefix == prefix }) else { return }
        let below = layers[index]
        layers.removeSubrange(index...)
        handlers = below.handlers
        hints = below.hints
        steer = below.steer
        focused = below.focused
    }

    /// A page or menu is up over the shell: its buttons are in force, not the shell's.
    var layerOpen: Bool { !layers.isEmpty }
    func ownsLayer(_ prefix: String) -> Bool { layers.last?.prefix == prefix }
    func ownsLayerTree(_ prefix: String) -> Bool { layers.last?.prefix.hasPrefix(prefix) == true }

    private func reachable(_ id: String) -> Bool {
        if let layer = layers.last { return id.hasPrefix(layer.prefix) }
        return rootPrefix.map { id.hasPrefix($0) } ?? true
    }

    // MARK: Input



    func touched() { if controllerActive { controllerActive = false } }

    /// The preview app (app/BCDPreview) shows a screen as a controller user would see it.
    func preview(focused id: String?) {
        controllerActive = id != nil
        focused = id
    }

    func press(_ button: BCDButton) {
        guard enabled else { return }
        controllerActive = true
        if button == .a, let id = focused, reachable(id), let action = actions[id] { action(); return }
        handlers[button]?()
    }

    func move(_ direction: BCDMove) {
        guard enabled else { return }
        let wasActive = controllerActive
        controllerActive = true
        if wasActive, let steer, steer(direction) { return }
        if wasActive, let id = focused, reachable(id), let route = navigators.first(where: { id.hasPrefix($0.key) }), route.value.1(direction) { return }
        guard let current = focused.flatMap({ reachable($0) ? frames[$0] : nil }) else {
            focused = topLeft(); return
        }
        if !wasActive { return }   // the first press only shows where the focus is
        if BCDFocusSteppers.step(focused, direction) { return }   // BCD (2026-10-19): left and right step a value under the ring (BCDTouchLayouts.swift)
        if let next = nearest(from: current, toward: direction) {
            withAnimation(.easeOut(duration: 0.14)) { focused = next }
        }
    }

    private func topLeft() -> String? {
        frames.filter { reachable($0.key) }.min { a, b in
            a.value.minY == b.value.minY ? a.value.minX < b.value.minX : a.value.minY < b.value.minY
        }?.key
    }

    /// The closest control whose centre lies in the 45° cone of the direction, distance
    /// weighted so that staying in the same row or column wins.
    func nearest(from rect: CGRect, toward direction: BCDMove) -> String? {
        let origin = CGPoint(x: rect.midX, y: rect.midY)
        var best: (String, CGFloat)?
        for (id, frame) in frames where frame != rect && reachable(id) {
            let dx = frame.midX - origin.x, dy = frame.midY - origin.y
            let (along, across): (CGFloat, CGFloat)
            switch direction {
            case .up: (along, across) = (-dy, dx)
            case .down: (along, across) = (dy, dx)
            case .left: (along, across) = (-dx, dy)
            case .right: (along, across) = (dx, dy)
            }
            guard along > 1, abs(across) <= along * 1.2 + 24 else { continue }
            let score = along + abs(across) * 2.2
            if best == nil || score < best!.1 { best = (id, score) }
        }
        return best?.0
    }

    // MARK: Controllers
    //
    // The interface reads controllers by looking at them 30 times a second, and only while one
    // is connected, the interface is on screen and the app is active. It installs no
    // GameController handlers: each button and stick holds a single handler, the app's other
    // controller code (BCDControllerHub's key mapping and tester) sets those same
    // ones, and whichever is set last silences the other; and GamepadInput (XInput) moves the
    // pads' handler queue off the main thread, where a main-actor handler would crash.
    // Looking shares nothing and runs on the main run loop. (In the app the front end is
    // driven by the library's controller commands instead: BCDFocus(watchControllers: false).)

    private var pads: [GCController] = []
    private var pollTimer: Timer?
    private var wasPressed: Set<BCDButton> = []
    private var appActive = true

    private func attach(_ controller: GCController) {
        guard controller.extendedGamepad != nil, !pads.contains(where: { $0 === controller }) else { return }
        pads.append(controller)
        updatePolling()
    }

    private func detach(_ controller: GCController) {
        pads.removeAll { $0 === controller }
        updatePolling()
    }

    private func updatePolling() {
        let wanted = enabled && appActive && !pads.isEmpty
        if wanted, pollTimer == nil {
            let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.poll() }
            }
            RunLoop.main.add(timer, forMode: .common)
            pollTimer = timer
        } else if !wanted, let timer = pollTimer {
            timer.invalidate()
            pollTimer = nil
            wasPressed = []
            heldMove = nil
            stopRepeat()
        }
    }

    /// The strongest direction of any pad's D-pad or left stick, and the buttons newly down.
    private func poll() {
        var x: Float = 0, y: Float = 0
        var pressed: Set<BCDButton> = []
        for controller in pads {
            guard let pad = controller.extendedGamepad else { continue }
            for (px, py) in [(pad.dpad.xAxis.value, pad.dpad.yAxis.value),
                             (pad.leftThumbstick.xAxis.value, pad.leftThumbstick.yAxis.value)]
            where abs(px) + abs(py) > abs(x) + abs(y) {
                (x, y) = (px, py)
            }
            let map: [(GCControllerButtonInput?, BCDButton)] = [
                (pad.buttonA, .a), (pad.buttonB, .b), (pad.buttonX, .x), (pad.buttonY, .y),
                (pad.leftShoulder, .leftShoulder), (pad.rightShoulder, .rightShoulder),
                (pad.buttonMenu, .menu), (pad.buttonOptions, .options),
            ]
            for (input, button) in map where input?.isPressed == true { pressed.insert(button) }
        }
        stick(x: x, y: y)
        for button in pressed.subtracting(wasPressed) { press(button) }
        wasPressed = pressed
    }

    /// The stick or D-pad: one step at once, then repeats while held (0.35 s, then every 0.12 s).
    private func stick(x: Float, y: Float) {
        let move: BCDMove? = abs(x) < 0.5 && abs(y) < 0.5 ? nil
            : abs(x) > abs(y) ? (x > 0 ? .right : .left) : (y > 0 ? .up : .down)
        guard move != heldMove else { return }
        heldMove = move
        stopRepeat()
        guard let move else { return }
        self.move(move)
        repeatTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 350_000_000)
            while !Task.isCancelled {
                self?.move(move)
                try? await Task.sleep(nanoseconds: 120_000_000)
            }
        }
    }

    private func stopRepeat() { repeatTask?.cancel(); repeatTask = nil }
}

// MARK: - Marking a control

private struct BCDFocusFrameKey: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

private struct BCDFocusable: ViewModifier {
    @EnvironmentObject private var focus: BCDFocus
    let id: String
    let cornerRadius: CGFloat
    let action: () -> Void
    @State private var owner = UUID()

    func body(content: Content) -> some View {
        let isFocused = focus.controllerActive && focus.focused == id
        content
            .overlay {
                if isFocused {
                    RoundedRectangle(cornerRadius: cornerRadius + 4, style: .continuous)
                        .strokeBorder(BCDStyle.glow, lineWidth: 3)
                        .padding(-5)
                        .shadow(color: BCDStyle.fuchsia.opacity(0.55), radius: 10)
                        .allowsHitTesting(false)
                }
            }
            .scaleEffect(isFocused ? 1.03 : 1)
            .zIndex(isFocused ? 1 : 0)
            .background(GeometryReader { proxy in
                Color.clear.preference(key: BCDFocusFrameKey.self,
                                       value: [id: proxy.frame(in: .named(BCDFocus.space))])
            })
            .onPreferenceChange(BCDFocusFrameKey.self) { frames in
                if let frame = frames[id] { focus.register(id, frame: frame) }
            }
            .onAppear { focus.setAction(id, action, owner: owner) }
            .onDisappear { focus.unregister(id, owner: owner) }
            .simultaneousGesture(TapGesture().onEnded { focus.touched() })
            .accessibilityAddTraits(.isButton)
    }
}

extension View {
    /// A control the controller can reach. `action` is what A does on it; a tap still goes
    /// through the control's own button.
    func bcdFocusable(_ id: String, cornerRadius: CGFloat = 16, action: @escaping () -> Void) -> some View {
        modifier(BCDFocusable(id: id, cornerRadius: cornerRadius, action: action))
    }

    /// Disabled actions are omitted from custom controller navigation as well as touch.
    @ViewBuilder func bcdFocusable(_ id: String, enabled: Bool, cornerRadius: CGFloat = 16,
                                  action: @escaping () -> Void) -> some View {
        if enabled { bcdFocusable(id, cornerRadius: cornerRadius, action: action) }
        else { self }
    }

    /// The buttons a screen uses and their hints in the bottom bar.
    func bcdButtons(_ focus: BCDFocus, _ list: [(BCDHint, () -> Void)]) -> some View {
        onAppear { focus.setHandlers(list) }
    }
}

/// One hint in the bottom bar: the button's glyph and what it does.
struct BCDHintView: View {
    let hint: BCDHint
    var body: some View {
        HStack(spacing: 7) {
            Text(glyph)
                .font(.system(size: glyph.count > 1 ? 10 : 12, weight: .heavy, design: .rounded))
                .foregroundStyle(BCDStyle.ground)
                .frame(minWidth: 24, minHeight: 24)
                .padding(.horizontal, glyph.count > 1 ? 4 : 0)
                .background(BCDStyle.text, in: Capsule())
            Text(hint.title).font(BCDStyle.label(14)).foregroundStyle(BCDStyle.text)
        }
        .accessibilityElement(children: .combine)
    }
    private var glyph: String {
        switch hint.button {
        case .a: return "A"
        case .b: return "B"
        case .x: return "X"
        case .y: return "Y"
        case .leftShoulder: return "LB"
        case .rightShoulder: return "RB"
        case .menu: return "≡"
        case .options: return "⧉"
        }
    }
}
