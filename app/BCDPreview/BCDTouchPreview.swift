// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2026 sky9662: multiselect and alignment guides in the touch-control editor, from thebcd69/bcd-games pull request #69

import SwiftUI
import UIKit

/// BCD's touch controls on sample data, over a game's picture (tools/ui-preview.py photographs
/// them). Only the preview app compiles this file. `-BCDTab`:
///   touch           a full controller layout in play, A held and the left stick pushed
///   touch-keys      a keyboard-and-mouse layout, with the bar a game outside the library shows
///   touch-editor    the editor, a control selected (its panel docked away from it)
///   touch-layouts   the editor's layouts panel
///   touch-picker    the bar's layout picker
///   touch-menu      the section an in-game menu shows (BCDTouchMenuSection)
///   touch-off       no controls (for a footprint comparison)
///   touch-custom    controls with shapes and words of their own (circle, square, rectangles, long and short words)
///   touch-look      the editor on a control's Look tab (a rectangle with words)
///   touch-text      the card a control's words are typed in
/// BCDTouchPreviewModel also stands in for the engine in other previews (an in-game menu).
@MainActor final class BCDTouchPreviewModel: ObservableObject, BCDTouchInput, BCDTouchEditorActions, BCDTouchMenuActions {
    @Published var items: [BCDTouchItem] = []
    @Published var selected: UUID?
    @Published var selectedIDs: Set<UUID> = []
    private var dragOrigins: [UUID: CGPoint] = [:]
    @Published var visible = true
    @Published var opacity = 0.7
    @Published var layoutScale = 1.0
    @Published var layouts = BCDTouchLayouts()
    /// Controls shown held, with their stick's deflection.
    var held: [UUID: CGSize] = [:]
    private var baseline: [BCDTouchItem] = []

    init(keys: Bool = false, screen: CGSize = CGSize(width: 844, height: 390)) {
        configure(keys: keys, screen: screen)
    }

    /// The sample laid out for `screen` (the preview app starts upright and then turns, so this
    /// runs again for the size it ends up with). `custom`: the controls with looks of their own.
    func configure(keys: Bool, screen: CGSize, held names: [String: CGSize] = [:], select: String? = nil, custom: Bool = false) {
        items = custom ? Self.customLayout() : keys ? Self.keyboardLayout() : Self.controllerLayout(screen)
        baseline = items
        held = [:]
        for item in items { if let name = item.action.padName, let v = names[name] { held[item.id] = v } }
        selected = select.flatMap { name in items.first { $0.action.padName == name }?.id }
        selectedIDs = selected.map { [$0] } ?? []
        layouts = BCDTouchLayouts(entries: [
            .init(id: "builtin.xbox", name: "Full controller", builtIn: true),
            .init(id: "u1", name: "Custom Layout 1", builtIn: false),
            .init(id: "u2", name: "Racing", builtIn: false),
        ], activeID: keys ? "u1" : "builtin.xbox", nextName: "Custom Layout 2")
    }

    var editorState: BCDTouchEditorState {
        BCDTouchEditorState(items: items, selected: selected, selectedIDs: selectedIDs,
                            layoutScale: layoutScale, layouts: layouts, opacity: opacity,
                            opacityRange: 0.15...1, playSize: layoutScale,
                            padLinks: padLinks, changed: items != baseline)
    }

    var menuState: BCDTouchMenuState {
        BCDTouchMenuState(visible: visible, layouts: layouts, opacity: opacity, opacityRange: 0.15...1, layoutScale: layoutScale)
    }

    private var padLinks: [String] {
        guard let a = items.first(where: { $0.id == selected })?.action, !a.isPad, a != .none, a != .keyboard else { return [] }
        return a.keyStick != nil ? ["LS", "RS"]
            : ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "L3", "R3", "Menu", "View", "D↑", "D↓", "D←", "D→"]
    }

    // MARK: Input

    func send(_ event: BCDTouchEvent) { print("[preview] touch \(event)") }

    func padSurface(control: UUID, name: String, changed: @escaping (CGSize, Bool) -> Void) -> AnyView {
        let held = self.held[control]
        return AnyView(Color.clear.contentShape(Rectangle()).onAppear {
            if let held { changed(held, true) }
        })
    }

    // MARK: Editing

    func select(_ id: UUID?) {
        selected = id
        selectedIDs = id.map { [$0] } ?? []
    }
    func selectMany(_ ids: Set<UUID>) {
        selectedIDs = ids.intersection(Set(items.map(\.id)))
        selected = selectedIDs.count == 1 ? selectedIDs.first : nil
    }
    func beginDrag(_ id: UUID) {
        if !(selectedIDs.count > 1 && selectedIDs.contains(id)) { select(id) }
        guard items.first(where: { $0.id == id })?.locked != true else { dragOrigins = [:]; return }
        dragOrigins = Dictionary(uniqueKeysWithValues: items.filter {
            selectedIDs.contains($0.id) && !$0.locked
        }.map { ($0.id, CGPoint(x: $0.x, y: $0.y)) })
    }
    func drag(_ id: UUID, delta: CGSize, screen: CGSize) {
        guard !dragOrigins.isEmpty, screen.width > 0, screen.height > 0 else { return }
        var minX = -Double.infinity, maxX = Double.infinity
        var minY = -Double.infinity, maxY = Double.infinity
        for p in dragOrigins.values {
            minX = max(minX, BCDTouchGeometry.placeRange.lowerBound - Double(p.x))
            maxX = min(maxX, BCDTouchGeometry.placeRange.upperBound - Double(p.x))
            minY = max(minY, BCDTouchGeometry.placeRange.lowerBound - Double(p.y))
            maxY = min(maxY, BCDTouchGeometry.placeRange.upperBound - Double(p.y))
        }
        let dx = min(max(Double(delta.width / screen.width), minX), maxX)
        let dy = min(max(Double(delta.height / screen.height), minY), maxY)
        for i in items.indices {
            guard let p = dragOrigins[items[i].id] else { continue }
            items[i].x = Double(p.x) + dx
            items[i].y = Double(p.y) + dy
        }
    }
    func endDrag(_ id: UUID, moved: Bool) {
        dragOrigins = [:]
        if !moved { select(id) }
    }
    func move(_ id: UUID, x: Double, y: Double) { update(id) { if !$0.locked { $0.x = x; $0.y = y } } }
    func add() {
        let p = BCDTouchGeometry.addPosition(count: items.count)
        let item = BCDTouchItem(id: UUID(), x: p.x, y: p.y, scale: 1, action: .mouseLeft)
        items.append(item)
        select(item.id)
    }
    func remove(_ id: UUID) {
        if items.first(where: { $0.id == id })?.locked == true { return }
        items.removeAll { $0.id == id }; select(nil)
    }
    func setAction(_ id: UUID, _ action: BCDTouchAction) { update(id) { $0.action = action } }
    func setScale(_ id: UUID, _ scale: Double) { update(id) { if !$0.locked { $0.scale = BCDTouchGeometry.clampScale(scale) } } }
    func setGroupAverageScale(_ ids: Set<UUID>, _ average: Double) {
        let chosen = items.filter { ids.contains($0.id) && !$0.locked }
        guard !chosen.isEmpty else { return }
        let current = chosen.map(\.scale).reduce(0, +) / Double(chosen.count)
        guard current > 0 else { return }
        for i in items.indices where ids.contains(items[i].id) && !items[i].locked {
            items[i].scale = BCDTouchGeometry.clampScale(items[i].scale * average / current)
        }
    }
    func setLocked(_ id: UUID, _ locked: Bool) { update(id) { $0.locked = locked } }
    func setLatch(_ id: UUID, _ on: Bool) { update(id) { $0.latch = on } }
    func bringToFront(_ id: UUID) { items = BCDTouchLayering.moved(items, id, toFront: true) }
    func sendToBack(_ id: UUID) { items = BCDTouchLayering.moved(items, id, toFront: false) }
    func setPadLink(_ id: UUID, _ name: String?) { update(id) { $0.padLink = name } }
    func setCustomShape(_ id: UUID, _ shape: BCDTouchCustomShape?) { update(id) { if !$0.locked { $0.customShape = shape } } }
    func setCustomText(_ id: UUID, _ text: String?) { update(id) { $0.customText = BCDTouchText.clean(text) } }
    func setOpacity(_ value: Double) { opacity = value }
    func setLayoutScale(_ value: Double) { layoutScale = value }
    func saveAsNewLayout() {
        layouts.entries.append(.init(id: UUID().uuidString, name: layouts.nextName, builtIn: false))
        layouts.activeID = layouts.entries.last?.id
    }
    func revert() { items = baseline; select(nil) }
    func done() { select(nil) }
    func chooseLayout(_ id: String, keepCurrentFirst: Bool) { layouts.activeID = id; layouts.unsaved = false }
    func newLayout(keepCurrentFirst: Bool) { saveAsNewLayout(); items = []; baseline = [] }
    func deleteLayout(_ id: String) { layouts.entries.removeAll { $0.id == id }; layouts.activeID = "builtin.xbox" }
    func setVisible(_ on: Bool) { visible = on }
    func editControls() {}

    private func update(_ id: UUID, _ change: (inout BCDTouchItem) -> Void) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        change(&items[i])
    }

    // MARK: Sample layouts

    /// A whole controller, placed for the screen it is shown on.
    static func controllerLayout(_ screen: CGSize) -> [BCDTouchItem] {
        let w = Double(screen.width), h = Double(screen.height)
        let k = min(max(h / 400, 1), 1.3)
        let side = w > 700 ? 59.0 : 24.0
        var out: [BCDTouchItem] = []
        func add(_ name: String, _ scale: Double, _ x: Double, _ y: Double) {
            out.append(BCDTouchItem(id: UUID(), x: x / w, y: y / h, scale: scale, action: .pad(name)))
        }
        let stick = 64 * 1.45 * k, stickY = h - 21 - 12 * k - stick / 2
        add("LS", 1.45 * k, side + 76 * k, stickY)
        add("RS", 1.45 * k, w - side - 76 * k, stickY)
        let sh = 64 * 0.88 * k
        add("LT", 0.88 * k, side + sh * 0.8 + 6, 10 + sh * 0.37)
        add("LB", 0.88 * k, side + sh * 0.8 + 6, 18 + sh * 1.11)
        add("RT", 0.88 * k, w - side - sh * 0.8 - 6, 10 + sh * 0.37)
        add("RB", 0.88 * k, w - side - sh * 0.8 - 6, 18 + sh * 1.11)
        let dp = 64 * 0.7 * k, arm = dp + 6 * k
        let dx = side + sh * 1.6 + 26 + arm + dp / 2, dy = stickY - stick / 2 - 22 * k - arm - dp / 2
        add("D↑", 0.7 * k, dx, dy - arm); add("D←", 0.7 * k, dx - arm, dy)
        add("D→", 0.7 * k, dx + arm, dy); add("D↓", 0.7 * k, dx, dy + arm)
        let face = 64 * 0.78 * k, o = face / 2 + 22 * k
        let fx = w - side - sh * 1.6 - 26 - o - face / 2, fy = stickY - stick / 2 - 22 * k - o - face / 2
        add("Y", 0.78 * k, fx, fy - o); add("X", 0.78 * k, fx - o, fy)
        add("B", 0.78 * k, fx + o, fy); add("A", 0.78 * k, fx, fy + o)
        let sysY = h - 21 - 20 * k - 64 * 0.75 * k / 2
        add("View", 0.75 * k, w / 2 - 46 * k, sysY); add("Menu", 0.75 * k, w / 2 + 46 * k, sysY)
        let click = 64 * 0.62 * k, clickY = stickY + stick / 2 - click / 2
        add("L3", 0.62 * k, side + 76 * k + stick / 2 + 20 * k + click / 2, clickY)
        add("R3", 0.62 * k, w - side - 76 * k - stick / 2 - 20 * k - click / 2, clickY)
        return out
    }

    /// Controls with looks of their own (BCD's editor, Look): every shape, short and long words, words on a standard
    /// control, a stick that keeps its ring, and a word too long for its control (cut short).
    static func customLayout() -> [BCDTouchItem] {
        func item(_ a: BCDTouchAction, _ x: Double, _ y: Double, _ s: Double = 1, shape: BCDTouchCustomShape? = nil,
                  text: String? = nil) -> BCDTouchItem {
            BCDTouchItem(id: UUID(), x: x, y: y, scale: s, action: a, customShape: shape, customText: text)
        }
        return [
            item(.keyStick(.wasd), 0.14, 0.72, 1.5, shape: .square, text: "MOVE"),
            item(.key(0x49), 0.82, 0.3, 1.0, shape: .rectangle(aspect: 2), text: "INV"),
            item(.key(0x4D), 0.62, 0.3, 1.0, shape: .square, text: "MAP"),
            item(.key(0x46), 0.72, 0.55, 1.1, shape: .circle, text: "USE"),
            item(.key(0x74), 0.9, 0.55, 0.9, shape: .rectangle(aspect: 0.5), text: "SAVE"),
            item(.mouseLeft, 0.88, 0.8, 1.2, text: "FIRE"),
            item(.key(0x20), 0.68, 0.8, 1.0, shape: .rectangle(aspect: 3), text: "QUICK SAVE"),
            item(.pad("LB"), 0.35, 0.15, 0.9, text: "GRENADE"),
            item(.pad("A"), 0.45, 0.5, 0.8, shape: .square),
            item(.key(0x51), 0.3, 0.45, 0.5, text: "INVENTORY"),
            item(.key(0x1B), 0.07, 0.12, 0.7, shape: .rectangle(aspect: 1.6), text: "🎒"),
        ]
    }

    /// Keys and the mouse, as a shooter on a keyboard wants them.
    static func keyboardLayout() -> [BCDTouchItem] {
        func item(_ a: BCDTouchAction, _ x: Double, _ y: Double, _ s: Double = 1) -> BCDTouchItem {
            BCDTouchItem(id: UUID(), x: x, y: y, scale: s, action: a)
        }
        return [
            item(.keyStick(.wasd), 0.15, 0.72, 1.6),
            item(.key(0x10), 0.08, 0.38, 0.8), item(.key(0x11), 0.2, 0.42, 0.8),
            item(.mouseLeft, 0.88, 0.7, 1.2), item(.mouseRight, 0.76, 0.82),
            item(.key(0x20), 0.75, 0.56), item(.key(0x45), 0.86, 0.43, 0.8), item(.key(0x52), 0.93, 0.31, 0.8),
            item(.key(0x1B), 0.07, 0.12, 0.7), item(.keyboard, 0.93, 0.12, 0.7),
            item(.key(0x09), 0.3, 0.88, 0.7), item(.keyStick(.arrows), 0.46, 0.8, 1.0),
            item(.key(0x74), 0.6, 0.88, 0.7), item(.none, 0.6, 0.6, 0.7),
        ]
    }
}

/// One sample case, over a game's picture, laid out as the overlay's window lays out the real
/// controls (the whole screen, the safe area ignored).
struct BCDTouchPreviewRoot: View {
    let spec: String
    let store: BCDStore

    var body: some View {
        GeometryReader { geo in
            if spec == "touch-tabletop" {
                BCDTouchTabletopPreview(store: store, available: geo.size)   // iPhone Duo's tabletop pose with the touchpad below the fold
            } else {
                BCDTouchPreviewCase(spec: spec, screen: geo.size, insets: BCDTouchSafeArea.insets())
            }
        }
        .ignoresSafeArea()
        .background {
            ZStack {
                Color.black
                if let game = store.games.first(where: { $0.id == "367520" }) ?? store.games.first {
                    BCDGameArt(game: game, kind: .hero, showTitle: false)
                }
            }
            .ignoresSafeArea()
        }
        .statusBarHidden(true)
        .preferredColorScheme(.dark)
    }
}

private struct BCDTouchPreviewCase: View {
    let spec: String
    let screen: CGSize
    let insets: EdgeInsets
    @StateObject private var model: BCDTouchPreviewModel

    init(spec: String, screen: CGSize, insets: EdgeInsets) {
        self.spec = spec
        self.screen = screen
        self.insets = insets
        _model = StateObject(wrappedValue: BCDTouchPreviewModel())
    }

    private var custom: Bool { spec == "touch-custom" || spec == "touch-look" || spec == "touch-text" }

    private func configure() {
        model.configure(keys: spec == "touch-keys" || spec == "touch-picker", screen: screen,
                        held: spec == "touch" ? ["A": .zero, "LS": CGSize(width: 0.72, height: -0.69)] : [:],
                        select: spec == "touch-editor" ? "Y" : nil, custom: custom)
        if spec == "touch-look" || spec == "touch-text" { model.selected = model.items.first { $0.customText == "INV" }?.id }
    }

    private var editing: Bool { spec == "touch-editor" || spec == "touch-layouts" || spec == "touch-look" || spec == "touch-text" }

    var body: some View {
        ZStack(alignment: .top) {
            // touch-off: the game's picture alone, to measure what the controls cost (footprint).
            if spec != "touch-menu" && spec != "touch-off" {
                BCDTouchLayer(items: model.items, screen: screen, layoutScale: model.layoutScale,
                              opacity: editing ? 1 : model.opacity, editing: editing, selected: model.selected,
                              selectedIDs: model.selectedIDs,
                              input: model, arranging: model, look: BCDTouchLookModel.shared.look)   // -BCDLook body,accent,icons,press (round 604)
            }
            switch spec {
            case "touch-editor", "touch-layouts":
                BCDTouchEditor(state: model.editorState, screen: screen, insets: insets, actions: model,
                               showLayouts: spec == "touch-layouts")
            case "touch-look", "touch-text":
                // Made again once the sample's control is selected, so it opens on the Look tab or the words' card.
                BCDTouchEditor(state: model.editorState, screen: screen, insets: insets, actions: model,
                               tab: .look, typing: spec == "touch-text")
                    .id(model.selected)
            case "touch-keys":
                bar
            case "touch-picker":
                bar
                BCDTouchLayoutPicker(layouts: model.layouts, screen: screen, actions: model, close: {}, finished: { _ in })
            case "touch-menu":
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Menu").font(BCDStyle.title(26)).foregroundStyle(BCDStyle.text)
                        BCDTouchMenuSection(state: model.menuState, actions: model, opened: {}, layoutsOpen: true)
                    }
                    .padding(20)
                }
                .frame(width: min(460, screen.width - 32), height: min(640, screen.height - insets.top - insets.bottom - 24))
                .background(BCDTouchPanelBackground())
                .clipShape(RoundedRectangle(cornerRadius: 20, style: .continuous))
                .coordinateSpace(name: BCDFocus.space)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            default:
                EmptyView()
            }
        }
        .frame(width: screen.width, height: screen.height, alignment: .top)
        .onAppear { configure() }
        .onChange(of: screen) { _, _ in configure() }
    }

    private var bar: some View {
        BCDTouchPlayBar(visible: true, showsLayouts: true, keyboard: {}, toggleVisible: {}, layouts: {}, edit: {}, menu: {})
            .frame(width: screen.width)
    }
}

/// -BCDTab touch-tabletop (2026-11-20): iPhone Duo's tabletop pose as TouchControlsOverlay lays it out, on the inner display held upright
/// (669 x 951 pt, which no simulator can show: the picture is scaled to fit). The fold is the screen's middle, 16 pt; the regions come
/// from the app's own BCDTabletop.regions: the game above, the touchpad's surface and the controls below.
private struct BCDTouchTabletopPreview: View {
    let store: BCDStore
    let available: CGSize
    @StateObject private var model = BCDTouchPreviewModel()
    private let screen = CGSize(width: 669, height: 951)

    var body: some View {
        let fold = CGRect(x: 0, y: screen.height / 2 - 8, width: screen.width, height: 16)
        let split = BCDTabletop.regions(bounds: CGRect(origin: .zero, size: screen), division: fold)
        let top = split?.top ?? CGRect(origin: .zero, size: screen)
        let bottom = split?.bottom ?? .zero
        let scale = min(available.width / screen.width, available.height / screen.height)
        ZStack(alignment: .topLeading) {
            Color.black
            if let game = store.games.first(where: { $0.id == "367520" }) ?? store.games.first {
                BCDGameArt(game: game, kind: .hero, showTitle: false)
                    .frame(width: top.width, height: top.height)
                    .clipped()
            }
            Rectangle().fill(Color(white: 0.1)).frame(width: fold.width, height: fold.height).offset(y: fold.minY)   // the hinge
            BCDTabletopTouchpadSurface(area: bottom, screen: screen)
            BCDTouchLayer(items: model.items, screen: bottom.size, layoutScale: model.layoutScale, opacity: model.opacity, editing: false,
                          selected: nil, input: model, arranging: model, look: BCDTouchLookModel.shared.look)
                .frame(width: bottom.width, height: bottom.height)
                .offset(x: bottom.minX, y: bottom.minY)
        }
        .frame(width: screen.width, height: screen.height, alignment: .topLeading)
        .scaleEffect(scale, anchor: .center)
        .frame(width: available.width, height: available.height)
        .background(Color(white: 0.16))
        .onAppear { model.configure(keys: false, screen: bottom.size) }
    }
}
