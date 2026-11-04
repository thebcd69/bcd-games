// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import SwiftUI
import UIKit

// BCD's touch controls on the engine: the only touch-control file that names it (it is in
// tools/ui-preview.py's ENGINE_BOUND). docs/BCD-UI-ARCHITECTURE.md.
//
// - The controls, the layouts and every setting stay in the engine's models and files, as
//   before: TouchControlsModel (bcd-controls.json), ControlPresetsModel
//   (bcd-control-presets.json) and a library game's own entry (LibraryModel.saveCurrentProfile).
//   BCD's views get them as BCDTouchItem values and hand changes back here; the conversion
//   loses nothing, so a layout saved by BCD is byte for byte what the old editor wrote.
// - What reaches the game: keys through winios_post_key, mouse buttons through winios_pointer
//   at the pointer, the keyboard through MetalBackedView, and controller controls through the
//   touch gamepad's own surface (TouchPadSurface, TouchGamepad.swift): the same calls as always.
// - Where it shows: TouchControlsOverlay (ContentView) keeps hosting the controls, gating them
//   (landscape, a session's menu, editing), configuring the touch gamepad and the pinch; its
//   `controls` and `topBar` are BCDTouchControlsLive and BCDTouchChromeLive, so every place
//   that hosts that overlay (the controls window over a game, Settings' editor) shows BCD's.

// MARK: - Stored controls <-> BCD's values

extension BCDTouchAction {
    init(stored action: ControlAction) {
        switch action {
        case .none: self = .none
        case .key(let vk): self = .key(vk)
        case .mouseLeft: self = .mouseLeft
        case .mouseRight: self = .mouseRight
        case .joystickWASD: self = .keyStick(.wasd)
        case .joystickArrows: self = .keyStick(.arrows)
        case .keyboardToggle: self = .keyboard
        case .pad(let name): self = .pad(name)
        }
    }

    var stored: ControlAction {
        switch self {
        case .none: return .none
        case .key(let vk): return .key(vk)
        case .mouseLeft: return .mouseLeft
        case .mouseRight: return .mouseRight
        case .keyStick(.wasd): return .joystickWASD
        case .keyStick(.arrows): return .joystickArrows
        case .keyboard: return .keyboardToggle
        case .pad(let name): return .pad(name)
        }
    }
}

extension BCDTouchItem {
    init(stored c: TouchControl) {
        self.init(id: c.id, x: c.nx, y: c.ny, scale: c.scale, action: BCDTouchAction(stored: c.action), padLink: c.padBinding,
                  locked: c.locked == true, customShape: BCDTouchCustomShape(name: c.face?.shape, aspect: c.face?.aspect),
                  customText: BCDTouchText.clean(c.face?.text))
    }

    var stored: TouchControl {
        var c = TouchControl()
        c.id = id
        c.nx = x
        c.ny = y
        c.scale = scale
        c.action = action.stored
        c.padBinding = padLink
        c.locked = locked ? true : nil   // never written as false: a layout with nothing locked is the file it always was
        c.face = TouchControl.Face.from(shape: customShape, text: customText)
        return c
    }
}

extension TouchControl.Face {
    /// The stored look for the player's shape and words; nil when there is neither, so a control without a look of its
    /// own is written as it always was.
    static func from(shape: BCDTouchCustomShape?, text: String?) -> TouchControl.Face? {
        let face = TouchControl.Face(shape: shape?.name, aspect: shape?.aspect, text: BCDTouchText.clean(text))
        return face.isEmpty ? nil : face
    }
}

extension TouchControl {
    /// The player's own shape as drawn, where it counts (never on a stick): the overlay window's hit test and the HUD's
    /// room for the controls ask it (TouchControlsModel), without the rest of the view value.
    var bcdOwnShape: BCDTouchShape? {
        guard let face, let shape = BCDTouchCustomShape(name: face.shape, aspect: face.aspect) else { return nil }
        return BCDTouchAction(stored: action).isStick ? nil : shape.drawn
    }
}

// MARK: - Input and edits

@MainActor final class BCDTouchLive: BCDTouchInput, BCDTouchEditorActions, BCDTouchMenuActions {
    static let shared = BCDTouchLive()

    private let model = TouchControlsModel.shared
    private let presets = ControlPresetsModel.shared
    /// The controls as they were when the editor opened (Revert, and whether an edited built-in
    /// would be lost on switching). Taken at the same moment as the model's own baseline.
    private var baseline: [TouchControl]
    private var watch: AnyCancellable?

    private init() {
        baseline = TouchControlsModel.shared.controls
        // @Published reports before the change: `on` while the model still says off is the edit starting.
        watch = model.$editing.sink { [weak self] on in
            guard let self, on, !self.model.editing else { return }
            self.baseline = self.model.controls
        }
    }

    private var library: LibraryModel { LibraryModel.shared }
    private var inLibrarySession: Bool { library.current != nil }

    // MARK: BCDTouchInput

    func send(_ event: BCDTouchEvent) {
        if BCDTurboLive.touchNeeded, !BCDTurboLive.shared.passes(event) { return }   // madeira-bcd (2026-10-25, round 604): turbo on a touch control's key or mouse button
        switch event {
        case .key(let vk, let down): winios_post_key(vk, down ? 1 : 0)
        case .mouse(let flags): winios_pointer(0, 0, flags, 0)
        case .toggleKeyboard: MetalBackedView.toggleKeyboard()
        }
    }

    func padSurface(control: UUID, name: String, changed: @escaping (CGSize, Bool) -> Void) -> AnyView {
        AnyView(TouchPadSurface(control: control, action: name, changed: changed))
    }

    // MARK: State for the views

    var items: [BCDTouchItem] { model.controls.map(BCDTouchItem.init(stored:)) }

    var layouts: BCDTouchLayouts {
        let builtInEdited = model.editing && (model.layoutID.map(ControlPresetStore.isBuiltIn) ?? false)
            && model.controls != baseline
        return BCDTouchLayouts(
            entries: presets.available.map {
                BCDTouchLayouts.Entry(id: $0.id, name: BCDTouchNames.layoutName(id: $0.id, stored: $0.name),
                                      builtIn: ControlPresetStore.isBuiltIn($0.id))
            },
            activeID: model.layoutID,
            unsaved: presets.currentIsUnsaved || builtInEdited,
            readOnly: presets.readOnly,
            nextName: presets.store.nextCustomName(),
            enabled: ControlPresetsModel.enabled)
    }

    /// A library game's own opacity (0.15...1), else the shared one (0.2...1), as before.
    private var opacity: (value: Double, range: ClosedRange<Double>) {
        inLibrarySession ? (library.opacity, 0.15...1) : (model.opacity, 0.2...1)
    }

    var editorState: BCDTouchEditorState {
        let selected = model.index(of: model.selected).map { model.controls[$0] }
        var links: [String] = []
        if GamepadInput.keyboardMouseAvailable, let a = selected?.action, !a.isPad, a != .none, a != .keyboardToggle {
            links = a.stickKeys != nil ? PadBindings.stickNames : PadBindings.buttonNames
        }
        return BCDTouchEditorState(items: items, selected: model.selected, layoutScale: model.sizeScale, layouts: layouts,
                                   opacity: opacity.value, opacityRange: opacity.range,
                                   playSize: inLibrarySession ? model.sizeScale : nil,
                                   padLinks: links, changed: model.controls != baseline)
    }

    var menuState: BCDTouchMenuState {
        BCDTouchMenuState(visible: model.visible, layouts: layouts, opacity: opacity.value, opacityRange: opacity.range,
                          layoutScale: inLibrarySession ? model.sizeScale : nil)
    }

    // MARK: Arranging and editing

    func select(_ id: UUID?) { if model.selected != id { model.selected = id } }

    func move(_ id: UUID, x: Double, y: Double) {
        update(id) { if $0.locked != true { $0.nx = x; $0.ny = y } }
    }

    func add() {
        var c = TouchControl()
        let p = BCDTouchGeometry.addPosition(count: model.controls.count)
        c.nx = p.x
        c.ny = p.y
        model.controls.append(c)
        model.selected = c.id
    }

    func remove(_ id: UUID) {
        if let i = model.index(of: id), model.controls[i].locked == true { return }   // a locked control stays until unlocked
        model.controls.removeAll { $0.id == id }
        model.selected = nil
    }

    func setAction(_ id: UUID, _ action: BCDTouchAction) { update(id) { $0.action = action.stored } }
    func setScale(_ id: UUID, _ scale: Double) { update(id) { if $0.locked != true { $0.scale = BCDTouchGeometry.clampScale(scale) } } }
    func setLocked(_ id: UUID, _ locked: Bool) { update(id) { $0.locked = locked ? true : nil } }

    /// A shape changes the control's size, so a locked control keeps its own; its words stay as they are.
    func setCustomShape(_ id: UUID, _ shape: BCDTouchCustomShape?) {
        update(id) { c in
            guard c.locked != true else { return }
            let face = TouchControl.Face(shape: shape?.name, aspect: shape?.aspect, text: c.face?.text)
            c.face = face.isEmpty ? nil : face
        }
    }

    /// The words, cleaned (BCDTouchText); empty words take them off. Its shape, even one this build does not know, stays.
    func setCustomText(_ id: UUID, _ text: String?) {
        update(id) { c in
            let face = TouchControl.Face(shape: c.face?.shape, aspect: c.face?.aspect, text: BCDTouchText.clean(text))
            c.face = face.isEmpty ? nil : face
        }
    }

    /// In front of (last) or behind (first) the other controls: the list is the layer order.
    func bringToFront(_ id: UUID) { reorder(id, toFront: true) }
    func sendToBack(_ id: UUID) { reorder(id, toFront: false) }
    private func reorder(_ id: UUID, toFront: Bool) {
        let moved = BCDTouchLayering.moved(model.controls, id, toFront: toFront)
        if moved != model.controls { model.controls = moved }
    }
    func setPadLink(_ id: UUID, _ name: String?) { update(id) { $0.padBinding = name } }

    /// One change to one control: one write of the controls file.
    private func update(_ id: UUID, _ change: (inout TouchControl) -> Void) {
        guard let i = model.index(of: id) else { return }
        var c = model.controls[i]
        change(&c)
        if c != model.controls[i] { model.controls[i] = c }
    }

    func setOpacity(_ value: Double) {
        if inLibrarySession { library.opacity = min(max(value, 0.15), 1) }
        else { model.opacity = min(max(value, 0.2), 1) }
    }

    func setLayoutScale(_ value: Double) { model.sizeScale = min(max(value, 0.5), 2) }

    func revert() {
        model.selected = nil
        if model.controls != baseline { model.controls = baseline }
    }

    func done() {
        model.selected = nil
        model.editing = false
    }

    func setVisible(_ on: Bool) {
        model.visible = on
        // Touch controls turned on in a game with nothing on screen: the built-in that suits the game, not an empty overlay.
        if on, let entry = library.activeEntry, inLibrarySession {
            presets.applyGameDefault(steam: entry.steamAppID != nil || entry.id == LibraryEntry.dockSessionID, reason: "touch-on")
        }
    }

    func editControls() {
        model.visible = true
        model.editing = true
    }

    // MARK: Layouts
    //
    // Outside the editor these are the old layout menu's steps. Inside it, the edit is ended
    // first (edits to a layout of the player's own are written to it then, as at Done) and
    // started again after, so the next Done compares against the layout now on screen; a
    // built-in loaded mid-edit would otherwise be taken for an edited one.

    func chooseLayout(_ id: String, keepCurrentFirst: Bool) {
        let editing = model.editing
        if keepCurrentFirst { presets.keepCurrent() }
        if editing { pauseEdit() }
        presets.load(id, screen: ControlPresetsModel.currentScreen())
        if editing { model.editing = true } else { profileChanged() }
    }

    func newLayout(keepCurrentFirst: Bool) {
        let editing = model.editing
        if keepCurrentFirst { presets.keepCurrent() }
        if editing { pauseEdit() }
        if presets.createLayout() {
            if !editing { profileChanged() }
        } else if editing {
            model.editing = true
        }
    }

    func deleteLayout(_ id: String) {
        let editing = model.editing
        if editing { pauseEdit() }
        let deleted = presets.delete(id)
        if editing { model.editing = true } else if deleted { profileChanged() }
    }

    func saveAsNewLayout() {
        // Kept under the new name before the edit ends, so the layout it came from keeps what
        // it had and a built-in stays as shipped.
        guard presets.keepCurrent() else { return }
        pauseEdit()
        model.editing = true
    }

    private func pauseEdit() {
        model.selected = nil
        model.editing = false
    }

    /// A library game remembers its layout, as the old menu saved it.
    private func profileChanged() {
        if inLibrarySession { library.saveCurrentProfile() }
    }

    /// The layout picker over a game outside the library takes every touch while it is open,
    /// as the editor does (the overlay's window gives the whole screen to an edit).
    func beginPicking() {
        model.selected = nil
        model.editing = true
    }

    func endPicking() {
        if model.editing { model.editing = false }
    }
}

// MARK: - Views for the overlay and the menus

/// TouchControlsOverlay's controls: BCD's layer.
struct BCDTouchControlsLive: View {
    let screen: CGSize
    let session: Bool
    @ObservedObject private var model = TouchControlsModel.shared
    @ObservedObject private var library = LibraryModel.shared
    @ObservedObject private var feel = BCDTouchFeelModel.shared   // madeira-bcd (2026-10-25, round 604): the controls are drawn again when their colour changes
    @ObservedObject private var looks = BCDTouchLookModel.shared   // round 604: how the controls look

    var body: some View {
        BCDTouchLayer(items: BCDTouchLive.shared.items, screen: screen, layoutScale: model.sizeScale,
                      // A library game's Control opacity; full while editing.
                      opacity: session && !model.editing ? library.opacity : 1,
                      editing: model.editing, selected: model.selected,
                      input: BCDTouchLive.shared, arranging: BCDTouchLive.shared, look: looks.look)
            .id(feel.revision)
    }
}

/// TouchControlsOverlay's top bar: the editor while editing; over a game outside the library,
/// the round buttons (keyboard, controls on or off, layouts, edit, menu).
struct BCDTouchChromeLive: View {
    @ObservedObject private var model = TouchControlsModel.shared
    @ObservedObject private var presets = ControlPresetsModel.shared
    @ObservedObject private var library = LibraryModel.shared
    @State private var picking = false

    var body: some View {
        GeometryReader { geo in
            let live = BCDTouchLive.shared
            if model.editing && picking {
                BCDTouchLayoutPicker(layouts: live.layouts, screen: geo.size, actions: live, close: {
                    picking = false
                    live.endPicking()
                }, finished: { openedEditor in
                    // A new layout opens in the editor; anything else returns to the game.
                    picking = false
                    if !openedEditor { live.endPicking() }
                })
            } else if model.editing {
                BCDTouchEditor(state: live.editorState, screen: geo.size, insets: BCDTouchSafeArea.insets(), actions: live)
            } else {
                BCDTouchPlayBar(visible: model.visible, showsLayouts: TouchControlsOverlay.showsLayoutMenu(model),
                                keyboard: { MetalBackedView.toggleKeyboard() },
                                toggleVisible: { model.visible.toggle() },
                                layouts: {
                                    picking = true
                                    live.beginPicking()
                                },
                                edit: { model.editing = true },
                                menu: { model.sessionPanel = true })
                    .frame(width: geo.size.width)
            }
        }
        .onChange(of: model.editing) { _, editing in if !editing { picking = false } }
    }
}

/// The touch controls' section of an in-game menu, on the engine. `closeMenu` closes that menu
/// when the editor opens over the game.
struct BCDTouchMenuSectionLive: View {
    let closeMenu: () -> Void
    @ObservedObject private var model = TouchControlsModel.shared
    @ObservedObject private var presets = ControlPresetsModel.shared
    @ObservedObject private var library = LibraryModel.shared

    var body: some View {
        BCDTouchMenuSection(state: BCDTouchLive.shared.menuState, actions: BCDTouchLive.shared, opened: closeMenu)
    }
}

/// The classic in-game menu's layout row, in BCD's look.
struct BCDTouchLayoutRowLive: View {
    @ObservedObject private var model = TouchControlsModel.shared
    @ObservedObject private var presets = ControlPresetsModel.shared
    @State private var open = false

    var body: some View {
        BCDTouchLayoutRow(layouts: BCDTouchLive.shared.layouts, actions: BCDTouchLive.shared, open: $open) { editor in
            if editor { LibraryModel.shared.menu = false }
        }
    }
}
