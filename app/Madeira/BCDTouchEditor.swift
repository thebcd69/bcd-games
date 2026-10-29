// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// BCD's touch-control editor (docs/BCD-UI-ARCHITECTURE.md), over the game or over Settings.
//
// The controls stay where they are and are moved by dragging them (BCDTouchLayer in editing);
// a pinch anywhere resizes the selected one. Around them:
//   - a bar at the top: Add, Layouts, Revert (back to how the layout was when the editor
//     opened) and Done;
//   - a selected control's panel, docked to the side of the screen away from it so it never
//     covers it: what the control does (keys, mouse and key sticks, controller inputs), its
//     size, Lock (it then cannot be moved, resized or removed), To front / To back (which of
//     two overlapping controls is on top), the controller button that also presses it in
//     keyboard-and-mouse mode, Remove;
//   - the layouts panel: switch, new, save as new, delete, and the controls' opacity and size
//     in play.
// Every change goes through BCDTouchEditorActions (BCDTouchLive in the app), which keeps the
// stored controls and layouts in the formats they always had.

// MARK: - What the editor shows and does

struct BCDTouchEditorState: Equatable {
    var items: [BCDTouchItem] = []
    var selected: UUID?
    var layoutScale = 1.0
    var layouts = BCDTouchLayouts()
    /// The controls' opacity in play, and its range (a library game's own, or the shared one).
    var opacity = 1.0
    var opacityRange: ClosedRange<Double> = 0.2...1
    /// The whole layout's size in play (a library game's Control size); nil: not offered here.
    var playSize: Double?
    /// Controller inputs that can also press the selected control in keyboard-and-mouse mode;
    /// empty when the mode is off or the control cannot take one.
    var padLinks: [String] = []
    /// The layout differs from when the editor opened.
    var changed = false
}

@MainActor protocol BCDTouchEditorActions: BCDTouchArranging, BCDTouchLayoutActions {
    func add()
    func remove(_ id: UUID)
    func setAction(_ id: UUID, _ action: BCDTouchAction)
    func setScale(_ id: UUID, _ scale: Double)
    /// A locked control cannot be moved, resized or removed until it is unlocked.
    func setLocked(_ id: UUID, _ locked: Bool)
    /// In front of, or behind, the controls it overlaps.
    func bringToFront(_ id: UUID)
    func sendToBack(_ id: UUID)
    func setPadLink(_ id: UUID, _ name: String?)
    func setOpacity(_ value: Double)
    func setLayoutScale(_ value: Double)
    /// What is on screen becomes a new layout of its own (named BCDTouchLayouts.nextName).
    func saveAsNewLayout()
    /// Back to how the layout was when the editor opened.
    func revert()
    /// Ends the edit (edits to a layout of the player's own are kept in it).
    func done()
}

// MARK: - The editor

struct BCDTouchEditor: View {
    let state: BCDTouchEditorState
    let screen: CGSize
    let insets: EdgeInsets
    let actions: BCDTouchEditorActions

    @State private var layoutsOpen: Bool
    @State private var tab: BCDTouchEditorTab?

    /// `showLayouts`: open on the layouts panel.
    init(state: BCDTouchEditorState, screen: CGSize, insets: EdgeInsets, actions: BCDTouchEditorActions,
         showLayouts: Bool = false) {
        self.state = state
        self.screen = screen
        self.insets = insets
        self.actions = actions
        _layoutsOpen = State(initialValue: showLayouts)
    }

    /// The bar's height plus its gaps: the panels start below it.
    private static let barSpace = 60.0

    private var selectedItem: BCDTouchItem? { state.items.first { $0.id == state.selected } }

    var body: some View {
        ZStack(alignment: .topLeading) {
            if let item = selectedItem {
                let rect = dock(avoiding: frame(of: item))
                BCDTouchInspector(item: item, layoutScale: state.layoutScale, padLinks: state.padLinks,
                                  tab: Binding(get: { tab ?? BCDTouchEditorTab.of(item.action) }, set: { tab = $0 }),
                                  actions: actions, roomy: roomy,
                                  inFront: BCDTouchLayering.isFront(state.items, item.id),
                                  atBack: BCDTouchLayering.isBack(state.items, item.id))
                    .frame(width: rect.width, height: rect.height)
                    .background(BCDTouchPanelBackground())
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .position(x: rect.midX, y: rect.midY)
            } else if layoutsOpen {
                let rect = dock(avoiding: nil)
                layoutPanel
                    .frame(width: rect.width, height: rect.height)
                    .background(BCDTouchPanelBackground())
                    .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .position(x: rect.midX, y: rect.midY)
            }
            bar
                .frame(width: screen.width)
                .padding(.top, max(insets.top, 6) + 6)
        }
        .frame(width: screen.width, height: screen.height, alignment: .topLeading)
        .onChange(of: state.selected) { _, id in
            tab = nil
            if id != nil { layoutsOpen = false }
        }
        .transaction { $0.animation = nil }
    }

    private func frame(of item: BCDTouchItem) -> CGRect {
        let d = BCDTouchGeometry.diameter(scale: item.scale, layoutScale: state.layoutScale)
        let size = BCDTouchGeometry.size(item.action.shape, diameter: d)
        return CGRect(x: item.x * Double(screen.width) - Double(size.width) / 2,
                      y: item.y * Double(screen.height) - Double(size.height) / 2,
                      width: Double(size.width), height: Double(size.height))
    }

    /// An iPad: roomier panels with larger type.
    private var roomy: Bool { min(screen.width, screen.height) >= 700 }

    private func dock(avoiding rect: CGRect?) -> CGRect {
        BCDTouchPanelPlacement.dock(screen: screen, left: Double(insets.leading), right: Double(insets.trailing),
                                    top: Double(insets.top), bottom: Double(insets.bottom),
                                    bar: Self.barSpace, avoid: rect, wide: roomy ? 420 : 340)
    }

    // MARK: Bar

    private var bar: some View {
        HStack(spacing: 6) {
            barButton("Add", "plus") {
                layoutsOpen = false
                actions.add()
            }
            if state.layouts.enabled {
                barButton("Layouts", "square.grid.2x2", on: layoutsOpen && state.selected == nil) {
                    actions.select(nil)
                    layoutsOpen.toggle()
                }
            }
            barButton("Revert", "arrow.uturn.backward", enabled: state.changed) { actions.revert() }
            Button {
                BCDTouchHaptic.tap()
                actions.done()
            } label: {
                Text("Done")
                    .font(BCDStyle.label(15)).foregroundStyle(BCDStyle.onAccent)
                    .padding(.horizontal, 18).frame(height: 36)
                    .background(Capsule().fill(BCDStyle.glow))
            }
            .buttonStyle(BCDTouchPressStyle())
            .accessibilityLabel("Done editing")
        }
        .padding(4)
        .background(Capsule().fill(BCDStyle.surface.opacity(0.97)))
        .overlay(Capsule().stroke(BCDStyle.stroke, lineWidth: 1))
    }

    private func barButton(_ title: String, _ symbol: String, on: Bool = false, enabled: Bool = true,
                           action: @escaping () -> Void) -> some View {
        Button { BCDTouchHaptic.tap(); action() } label: {
            HStack(spacing: 5) {
                Image(systemName: symbol).font(.system(size: 13, weight: .bold))
                Text(title).font(BCDStyle.label(14))
            }
            .foregroundStyle(enabled ? (on ? Color.white : BCDStyle.text) : BCDStyle.textSoft.opacity(0.5))
            .padding(.horizontal, 12).frame(height: 36)
            .background(Capsule().fill(on ? BCDStyle.surfaceRaised : Color.clear))
            .overlay(Capsule().stroke(on ? BCDStyle.lavender.opacity(0.6) : Color.clear, lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(BCDTouchPressStyle())
        .disabled(!enabled)
    }

    // MARK: Layouts panel

    private var layoutPanel: some View {
        VStack(spacing: 0) {
            BCDTouchPanelHeader(title: "Layouts", subtitle: state.layouts.activeName) { layoutsOpen = false }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    BCDTouchLayoutList(layouts: state.layouts, actions: actions, saveAsNew: { actions.saveAsNewLayout() })
                    VStack(alignment: .leading, spacing: 10) {
                        BCDEyebrow(text: "In play")
                        HStack(alignment: .center, spacing: 12) {
                            BCDTouchSliderRow(title: "Opacity", value: state.opacity, range: state.opacityRange,
                                              text: "\(Int((state.opacity * 100).rounded())) %") { actions.setOpacity($0) }
                            // What a button will look like at this opacity.
                            BCDTouchFace(action: .pad("A"), diameter: 34, opacity: state.opacity)
                                .padding(6)
                                .background(RoundedRectangle(cornerRadius: 10).fill(BCDStyle.lavender.opacity(0.25)))
                                .accessibilityHidden(true)
                        }
                        if let size = state.playSize {
                            BCDTouchSliderRow(title: "Size", value: size, range: 0.5...2,
                                              text: String(format: "%.1f\u{00D7}", size)) { actions.setLayoutScale($0) }
                        }
                    }
                }
                .padding(14)
            }
        }
    }
}

// MARK: - A selected control's panel

enum BCDTouchEditorTab: String, CaseIterable, Identifiable {
    case keys, mouse, controller, link
    var id: String { rawValue }
    var title: String {
        switch self {
        case .keys: return "Keys"
        case .mouse: return "Mouse & more"
        case .controller: return "Controller"
        case .link: return "Pad link"
        }
    }
    static func of(_ action: BCDTouchAction) -> BCDTouchEditorTab {
        switch action {
        case .key: return .keys
        case .pad: return .controller
        case .none, .mouseLeft, .mouseRight, .keyStick, .keyboard: return .mouse
        }
    }
}

/// What the selected control does, its size and its controller link.
struct BCDTouchInspector: View {
    let item: BCDTouchItem
    let layoutScale: Double
    let padLinks: [String]
    @Binding var tab: BCDTouchEditorTab
    let actions: BCDTouchEditorActions
    /// An iPad: larger type and chips.
    var roomy = false
    /// The control is already the frontmost / the rearmost one.
    var inFront = false
    var atBack = false

    private var tabs: [BCDTouchEditorTab] {
        padLinks.isEmpty ? [.keys, .mouse, .controller] : BCDTouchEditorTab.allCases
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            BCDTouchSliderRow(title: "Size", value: item.scale, range: BCDTouchGeometry.scaleRange,
                              text: String(format: "%.1f\u{00D7}", item.scale)) { actions.setScale(item.id, $0) }
                .disabled(item.locked).opacity(item.locked ? 0.45 : 1)
                .padding(.horizontal, 14).padding(.bottom, 6)
            arrangeRow
            tabBar
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    switch tabs.contains(tab) ? tab : BCDTouchEditorTab.of(item.action) {
                    case .keys: keys
                    case .mouse: mouse
                    case .controller: controller
                    case .link: link
                    }
                }
                .padding(14)
            }
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            BCDTouchFace(action: item.action, diameter: 34)
                .frame(width: 44, height: 40)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(BCDTouchNames.title(item.action))
                    .font(BCDStyle.heading(roomy ? 19 : 16)).foregroundStyle(BCDStyle.text)
                    .lineLimit(1).minimumScaleFactor(0.7)
                Text(item.locked ? "Locked \u{00B7} unlock to move, resize or remove" : "Drag to move \u{00B7} pinch to resize")
                    .font(BCDStyle.body(roomy ? 14 : 12)).foregroundStyle(BCDStyle.textSoft).lineLimit(1).minimumScaleFactor(0.7)
            }
            Spacer(minLength: 4)
            Button(role: .destructive) { BCDTouchHaptic.tap(); actions.remove(item.id) } label: {
                Image(systemName: "trash").font(.system(size: 14, weight: .bold))
                    .foregroundStyle(BCDStyle.alert)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(BCDStyle.alert.opacity(0.14)))
            }
            .buttonStyle(BCDTouchPressStyle())
            .disabled(item.locked).opacity(item.locked ? 0.35 : 1)
            .accessibilityLabel("Remove this control")
            BCDTouchCloseButton { actions.select(nil) }
        }
        .padding(.horizontal, 12).padding(.top, 10).padding(.bottom, 6)
    }

    /// Lock it in place, or put it in front of or behind the controls it overlaps.
    private var arrangeRow: some View {
        HStack(spacing: 6) {
            BCDTouchPanelButton(title: item.locked ? "Locked" : "Lock", symbol: item.locked ? "lock.fill" : "lock.open",
                                prominent: item.locked) { actions.setLocked(item.id, !item.locked) }
                .accessibilityAddTraits(item.locked ? .isSelected : [])
            BCDTouchPanelButton(title: "To front", symbol: "square.2.layers.3d.top.filled") { actions.bringToFront(item.id) }
                .disabled(inFront).opacity(inFront ? 0.45 : 1)
            BCDTouchPanelButton(title: "To back", symbol: "square.2.layers.3d.bottom.filled") { actions.sendToBack(item.id) }
                .disabled(atBack).opacity(atBack ? 0.45 : 1)
        }
        .padding(.horizontal, 12).padding(.bottom, 6)
    }

    private var tabBar: some View {
        HStack(spacing: 4) {
            ForEach(tabs) { t in
                Button { BCDTouchHaptic.tap(); tab = t } label: {
                    Text(t.title)
                        .font(BCDStyle.label(roomy ? 15 : 12.5)).lineLimit(1).minimumScaleFactor(0.7)
                        .foregroundStyle(t == tab ? Color.white : BCDStyle.textSoft)
                        .frame(maxWidth: .infinity, minHeight: 32)
                        .background(Capsule().fill(t == tab ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(Color.clear)))
                        .contentShape(Capsule())
                }
                .buttonStyle(BCDTouchPressStyle())
                .accessibilityAddTraits(t == tab ? .isSelected : [])
            }
        }
        .padding(3)
        .background(Capsule().fill(BCDStyle.ground.opacity(0.6)))
        .padding(.horizontal, 12).padding(.bottom, 4)
    }

    // MARK: Tabs

    private var keys: some View {
        VStack(alignment: .leading, spacing: 14) {
            section("Common", [0x1B, 0x0D, 0x20, 0x09, 0x10, 0x11, 0x12, 0x08, 0x14, 0x5B].map(key))
            section("Letters", (0x41...0x5A).map { key(Int32($0)) })
            section("Numbers", (0x30...0x39).map { key(Int32($0)) })
            section("F keys", (0x70...0x7B).map { key(Int32($0)) })
            section("Arrows & navigation", [0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22].map(key))
            section("Symbols", [0xBD, 0xBB, 0xDB, 0xDD, 0xDC, 0xBA, 0xDE, 0xBC, 0xBE, 0xBF, 0xC0].map(key))
            section("Numpad", ((0x60...0x69).map { Int32($0) } + [0x6A, 0x6B, 0x6D, 0x6E, 0x6F]).map(key))
        }
    }

    private func key(_ vk: Int32) -> (String, BCDTouchAction) { (BCDTouchNames.keyLabel(vk), .key(vk)) }

    private var mouse: some View {
        VStack(alignment: .leading, spacing: 14) {
            section("Mouse", [("Left click", .mouseLeft), ("Right click", .mouseRight)], wide: true)
            section("Sticks", [("WASD", .keyStick(.wasd)), ("Arrow keys", .keyStick(.arrows))], wide: true)
            section("Other", [("Keyboard", .keyboard), ("Nothing", .none)], wide: true)
        }
    }

    private var controller: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("These act as controller 1. The sticks are analog; LT and RT press all the way. A real controller works alongside.")
                .font(BCDStyle.body(roomy ? 15 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
            section("Buttons", ["A", "B", "X", "Y"].map(pad))
            section("D-pad", ["D↑", "D↓", "D←", "D→"].map(pad))
            section("Shoulders", ["LB", "RB", "LT", "RT"].map(pad))
            section("Sticks", [("Left stick", .pad("LS")), ("Right stick", .pad("RS")),
                               ("L3", .pad("L3")), ("R3", .pad("R3"))], wide: true)
            section("Middle", [("Menu", .pad("Menu")), ("View", .pad("View")), ("Guide", .pad("Guide"))], wide: true)
        }
    }

    private func pad(_ name: String) -> (String, BCDTouchAction) { (BCDTouchNames.padLabel(name), .pad(name)) }

    private var link: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("In keyboard-and-mouse mode, this controller input presses this control too. Tap it again to unlink.")
                .font(BCDStyle.body(roomy ? 15 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
            grid(padLinks.map { name in
                (BCDTouchNames.padLabel(name) == name ? name : BCDTouchNames.padName(name), name)
            }, wide: padLinks.count <= 2) { label, name in
                chip(label, on: item.padLink == name) {
                    actions.setPadLink(item.id, item.padLink == name ? nil : name)
                }
            }
        }
    }

    // MARK: Chips

    private func section(_ title: String, _ choices: [(String, BCDTouchAction)], wide: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            BCDEyebrow(text: title)
            grid(choices, wide: wide) { label, action in
                chip(label, on: item.action == action) { actions.setAction(item.id, action) }
            }
        }
    }

    private func grid<T>(_ items: [(String, T)], wide: Bool,
                         @ViewBuilder cell: @escaping (String, T) -> some View) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: (wide ? 96 : 44) * (roomy ? 1.2 : 1)), spacing: 6)], spacing: 6) {
            ForEach(items.indices, id: \.self) { i in cell(items[i].0, items[i].1) }
        }
    }

    private func chip(_ label: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button { BCDTouchHaptic.tap(); action() } label: {
            Text(label)
                .font(BCDStyle.label(roomy ? 16.5 : 14)).lineLimit(1).minimumScaleFactor(0.55)
                .foregroundStyle(on ? Color.white : BCDStyle.text)
                .padding(.horizontal, 4)
                .frame(maxWidth: .infinity, minHeight: roomy ? 44 : 36)
                .background(RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(on ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.surfaceRaised)))
                .overlay(RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .stroke(on ? Color.clear : BCDStyle.stroke, lineWidth: 1))
                .contentShape(Rectangle())
        }
        .buttonStyle(BCDTouchPressStyle())
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

// MARK: - The layout picker over a game outside the library

/// The layouts as a card in the middle of the screen, from the bar's Layouts button; choosing
/// one puts it on screen and closes the card.
struct BCDTouchLayoutPicker: View {
    let layouts: BCDTouchLayouts
    let screen: CGSize
    let actions: BCDTouchLayoutActions
    let close: () -> Void
    /// A layout was chosen, made or deleted; true when the editor opened on a new one.
    let finished: (_ openedEditor: Bool) -> Void

    var body: some View {
        ZStack {
            Color.black.opacity(0.45)
                .contentShape(Rectangle())
                .onTapGesture { close() }
                .accessibilityLabel("Close")
            VStack(spacing: 0) {
                BCDTouchPanelHeader(title: "Layouts", subtitle: layouts.activeName, close: close)
                ScrollView {
                    BCDTouchLayoutList(layouts: layouts, actions: actions, done: finished)
                    .padding(14)
                }
            }
            .frame(width: min(380, screen.width - 32), height: min(440, screen.height - 32))
            .background(BCDTouchPanelBackground())
            .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
        }
        .frame(width: screen.width, height: screen.height)
        .transaction { $0.animation = nil }
    }
}

// MARK: - Pieces

/// The device's safe area. The overlay over a game ignores the safe area (its controls go edge to
/// edge), and SwiftUI then reports no insets inside it; the editor's bar and panels still keep
/// clear of the notch, the Dynamic Island and the home indicator.
enum BCDTouchSafeArea {
    @MainActor static func insets() -> EdgeInsets {
        let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
        let i = (windows.first { $0.isKeyWindow } ?? windows.first)?.safeAreaInsets ?? .zero
        return EdgeInsets(top: i.top, leading: i.left, bottom: i.bottom, trailing: i.right)
    }
}

struct BCDTouchPanelHeader: View {
    let title: String
    var subtitle: String? = nil
    let close: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(BCDStyle.heading(18)).foregroundStyle(BCDStyle.text)
                if let subtitle {
                    Text(subtitle).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.lavender).lineLimit(1)
                }
            }
            Spacer()
            BCDTouchCloseButton(action: close)
        }
        .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 4)
    }
}

struct BCDTouchCloseButton: View {
    let action: () -> Void
    var body: some View {
        Button { BCDTouchHaptic.tap(); action() } label: {
            Image(systemName: "xmark").font(.system(size: 13, weight: .heavy))
                .foregroundStyle(BCDStyle.text)
                .frame(width: 36, height: 36)
                .background(Circle().fill(BCDStyle.ground.opacity(0.6)))
                .overlay(Circle().stroke(BCDStyle.stroke, lineWidth: 1))
        }
        .buttonStyle(BCDTouchPressStyle())
        .accessibilityLabel("Close")
    }
}
