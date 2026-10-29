// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// Settings, full screen over the shell (docs/BCD-UI-ARCHITECTURE.md). On a phone on its side
// and on an iPad the list of sections sits on the left and the chosen section on the right;
// upright, the list pushes each section. A controller walks the list with up and down (each
// section shows beside it), enters with right or A, comes back with left or B, and B on the
// list closes Settings; inside a section up and down go row by row, left and right step a
// value, A switches a switch. The rules themselves are BCDSettingsNavigator's
// (BCDSettingsCore.swift); this file draws Settings and carries the rules out.

// MARK: - Where the sections' data comes from

/// What Settings' sections show and do. In the app it comes from BCDSettingsLive.swift (the
/// only part of Settings that touches the engine), in the preview app from its sample data
/// (app/BCDPreview/BCDSettingsPreviewData.swift). Each build compiles exactly one `make()`,
/// so nothing has to wire Settings up when the app starts.
@MainActor struct BCDSettingsBackends {
    var memory: any BCDMemoryBackend
    var graphics: any BCDGraphicsBackend
    var controls: any BCDControlsBackend
    var storage: any BCDStorageBackend
    var dotNet: any BCDDotNetBackend
    var logs: any BCDLogsBackend
    var feedback: any BCDFeedbackBackend
    var allSettings: any BCDAllSettingsBackend
    var about: any BCDAboutBackend
    var developer: any BCDDeveloperBackend
    var vcRuntime: any BCDVCRuntimeBackend
    var gecko: any BCDGeckoBackend
    /// Sections built elsewhere (Accounts, JIT, BCD Dock, Visual C++ runtime), wired in here:
    /// each draws its whole page beside the list (scrolling and title included). Controls
    /// that use `.bcdSettingsControl` are walked row by row; plain `.bcdFocusable` ones by the
    /// nearest-control rule, and B still comes back to the list.
    var custom: [BCDSettingsSection: () -> AnyView] = [:]
    /// A game's page, from Storage's list of games; nil until the game page is wired in.
    var openGame: ((BCDGame) -> Void)? = nil
}

/// B in a section built elsewhere (Accounts, JIT, BCD Dock): the section hands Settings its own
/// "close my page or question" through BCDSectionKit's BackHook, and Settings' B asks it first.
/// nil while no such section shows (the hook is called with nil when the section goes).
@MainActor final class BCDSettingsSectionBack {
    static let shared = BCDSettingsSectionBack()
    var inner: (() -> Bool)?
    var hook: BCDSectionKit.BackHook { { [weak self] inner in self?.inner = inner } }
}

// MARK: - State and the controller's rules

/// A question over Settings: its choices, then Cancel.
struct BCDSettingsConfirm: Identifiable {
    struct Choice {
        var title: String
        var destructive = false
        var run: () -> Void
    }
    let id = UUID()
    var title: String
    var message: String
    var choices: [Choice]
}

/// A long text read in place (a licence): up and down move through it a paragraph at a time.
struct BCDSettingsReading: Identifiable {
    let id = UUID()
    var title: String
    var paragraphs: [String]
}

@MainActor final class BCDSettingsModel: ObservableObject {
    let backends: BCDSettingsBackends
    @Published var selected: BCDSettingsSection
    /// Upright phone: the selected section's page is showing.
    @Published var pushed: Bool
    @Published private(set) var confirm: BCDSettingsConfirm?
    @Published private(set) var reading: BCDSettingsReading?
    /// Controls › Controllers: BCD's controller screen shows over Settings (BCDSettingsRoot draws it).
    @Published private(set) var controllersShown = false
    @Published var readerLine = 0
    /// What All settings searches for when it opens (from "More graphics options", say).
    @Published var allQuery = ""
    /// About's "Developer options": the Developer section shows in the list.
    @Published private(set) var developerShown = UserDefaults.standard.bool(forKey: BCDSettingsModel.developerKey)
    static let developerKey = "bcd.settings.developer"

    var mode: BCDSettingsNavigator.Mode = .split
    /// The shown section's controls, top to bottom, and what left/right and Y do on them.
    private(set) var order: [String] = []
    private var steppers: [String: (Int) -> Void] = [:]
    private var resetters: [String: () -> Void] = [:]
    /// The ring goes to the first control of the section about to show.
    private var focusFirst = false
    private var returnFocus: String?
    /// The last place the ring was in Settings, to bring it back after a turn of the device
    /// (the other layout's rows replace the ones it was on).
    private var lastFocus: String?
    /// A section's own full-screen page is up (the layout editor): the controller's B closes it
    /// and Settings' rows underneath stay put.
    private var covered: (() -> Void)?
    /// The focus layer of a covering page that has controls of its own (BCDControllerScreen's
    /// "controllers."): its D-pad moves are not Settings' while it is up.
    private var coveredLayer: String?
    private weak var focus: BCDFocus?
    private var close: () -> Void = {}

    init(section: BCDSettingsSection?) {
        backends = .make()
        selected = section ?? BCDSettingsSection.allCases[0]
        pushed = section != nil
        // Opened straight at Developer (the preview's -BCDSection developer): list it for this visit.
        if section == .developer { developerShown = true }
    }

    var navigator: BCDSettingsNavigator {
        BCDSettingsNavigator(mode: mode, sections: visibleSections, selected: selected, pushed: pushed, order: order,
                             adjustable: Set(steppers.keys))
    }

    /// The list: Developer only while About's "Developer options" is on.
    var visibleSections: [BCDSettingsSection] {
        BCDSettingsSection.allCases.filter { $0 != .developer || developerShown }
    }

    func showDeveloper(_ on: Bool) {
        developerShown = on
        UserDefaults.standard.set(on, forKey: Self.developerKey)
    }

    func steps(_ id: String?) -> Bool { id.map { steppers[$0] != nil } ?? false }

    // MARK: Start and end

    func start(focus: BCDFocus, close: @escaping () -> Void) {
        self.focus = focus
        self.close = close
        focus.steer = { [weak self] move in self?.steer(move) ?? false }
        if let id = focus.focused, id.hasPrefix("settings."), let section = BCDSettingsID.section(id) {
            selected = section          // the preview app's -BCDFocus on a section's control
        } else if mode == .stacked && pushed {
            focus.focused = nil
            focusFirst = true
        } else {
            focus.focused = BCDSettingsID.list(selected)
        }
        installButtons()
    }

    func stop() {
        guard let focus else { return }
        // Settings goes with its page over it: the page's layer must not keep the shell's controls out of reach.
        if let layer = coveredLayer { focus.closeLayer(layer); coveredLayer = nil; covered = nil }
        focus.steer = nil
        focus.setHandlers([])
        if focus.focused?.hasPrefix("settings.") == true { focus.focused = nil }
    }

    // MARK: Rows

    func register(_ id: String, step: ((Int) -> Void)?, reset: (() -> Void)?) {
        let hadReset = resetters[id] != nil
        steppers[id] = step
        resetters[id] = reset
        if focus?.focused == id && hadReset != (reset != nil) { installButtons() }
    }

    func unregister(_ id: String) {
        steppers[id] = nil
        resetters[id] = nil
    }

    func orderChanged(_ ids: [String]) {
        order = ids
        guard focusFirst, let first = ids.first else { return }
        focusFirst = false
        DispatchQueue.main.async { [weak self] in self?.setFocus(first) }
    }

    // MARK: Moves

    func steer(_ move: BCDMove) -> Bool {
        // A page over Settings swallows the D-pad (its controls are not Settings' rows), unless it
        // brought a focus layer of its own: then the focus walks that page's controls.
        if covered != nil { return coveredLayer == nil }
        if confirm != nil { stepConfirm(move); return true }
        if let reading {
            switch move {
            case .up: readerLine = max(0, readerLine - 1)
            case .down: readerLine = min(max(0, reading.paragraphs.count - 1), readerLine + 1)
            case .left, .right: break
            }
            return true
        }
        return apply(navigator.move(move, from: focus?.focused))
    }

    /// B: a question or a text closes first, then a section, then Settings.
    func back() {
        if let covered { covered(); return }
        if confirm != nil { answer(nil); return }
        if reading != nil { closeReader(); return }
        if let inner = BCDSettingsSectionBack.shared.inner, inner() { return }
        apply(navigator.back(from: focus?.focused))
    }

    @discardableResult private func apply(_ outcome: BCDSettingsNavigator.Outcome) -> Bool {
        switch outcome {
        case .focus(let id): setFocus(id)
        case .select(let section):
            show(section)
            setFocus(BCDSettingsID.list(section))
        case .enter(let section, let first): enter(section, first: first)
        case .leave(let section): leave(section)
        case .step(let id, let delta): steppers[id]?(delta)
        case .close: close()
        case .stay: break
        case .pass: return false
        }
        return true
    }

    private func setFocus(_ id: String) {
        withAnimation(.easeOut(duration: 0.14)) { focus?.focused = id }
    }

    /// Shows a section beside the list; its rows report their order once drawn.
    private func show(_ section: BCDSettingsSection) {
        guard section != selected else { return }
        selected = section
        order = []
        focusFirst = false
    }

    /// The ring reached a row of the list some other way (the nearest-control rule from a
    /// section wired in later, a tap): beside the list, that section shows.
    func focusMoved(to id: String?) {
        if let id, id.hasPrefix("settings.") { lastFocus = id }
        guard mode == .split, let id, let section = BCDSettingsID.listSection(id) else { return }
        show(section)
    }

    /// The device turned: the other layout's rows replace the ones the ring was on, so it comes
    /// back to the same place (a section's row upright only while its page is open).
    func layoutChanged(to new: BCDSettingsNavigator.Mode) {
        mode = new
        if new == .stacked, let last = lastFocus {
            // Upright, the page the ring was on: the section for one of its rows, else the list.
            if BCDSettingsID.listSection(last) != nil {
                pushed = false
            } else if BCDSettingsID.section(last) == selected {
                pushed = true
            }
        }
        installButtons()
        guard let last = lastFocus else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
            guard let focus = self?.focus, focus.focused == nil else { return }
            focus.focused = last
        }
    }

    private func enter(_ section: BCDSettingsSection, first: String?) {
        let fresh = section != selected || (mode == .stacked && !pushed)
        selected = section
        if mode == .stacked && !pushed { withAnimation(.easeOut(duration: 0.22)) { pushed = true } }
        if !fresh, let first {
            setFocus(first)
        } else {
            order = []
            focusFirst = true
        }
    }

    private func leave(_ section: BCDSettingsSection) {
        if mode == .stacked {
            withAnimation(.easeOut(duration: 0.22)) { pushed = false }
            order = []
            focusFirst = false
        }
        setFocus(BCDSettingsID.list(section))
    }

    /// A tap on the list: beside it the section shows; upright it opens.
    func choose(_ section: BCDSettingsSection) {
        if mode == .stacked {
            enter(section, first: nil)
        } else {
            show(section)
            focus?.focused = BCDSettingsID.list(section)
        }
    }

    /// A on the list, or a tap on an upright card: into the section.
    func open(_ section: BCDSettingsSection) {
        apply(.enter(section, first: section == selected && mode == .split ? order.first : nil))
    }

    func leaveToList() { leave(selected) }

    /// Straight to another section (About's Updates row): it shows, the ring on its first row.
    func go(to section: BCDSettingsSection) { enter(section, first: nil) }

    /// The Windows desktop: Settings closes, then the desktop starts as a game does.
    func startDesktop() {
        let start = backends.developer.startDesktop
        close()
        start()
    }

    /// All settings, searching for `query` (a section's "more options" row).
    func openAll(_ query: String) {
        allQuery = query
        enter(.all, first: nil)
        if mode == .split { setFocus(BCDSettingsID.list(.all)); focusFirst = true }
    }

    /// A section put a full-screen page over Settings (`close` ends it), or took it down (nil). A page
    /// with controls of its own for the controller to walk passes the prefix of their focus ids as
    /// `layer`: meanwhile the D-pad and A reach only them (BCDFocus.openLayer), B closes the page, and
    /// Settings' rows and ring come back with it. A page without one takes every move (it is touch).
    func cover(_ close: (() -> Void)?, layer: String? = nil) {
        if let old = coveredLayer { focus?.closeLayer(old) }
        covered = close
        coveredLayer = close == nil ? nil : layer
        if let layer = coveredLayer { focus?.openLayer(layer) }
        installButtons()
    }

    /// The controller screen over Settings. It is drawn inside Settings, not presented as a cover: it
    /// has controls of its own (switches, Done), so it takes a focus layer ("controllers."), and the
    /// D-pad and A walk them while it is up; B closes it and Settings' rows and ring come back.
    func showControllers() {
        guard !controllersShown else { return }
        controllersShown = true
        cover({ [weak self] in self?.hideControllers() }, layer: BCDSettingsModel.controllersLayer)
    }

    func hideControllers() {
        guard controllersShown else { return }
        controllersShown = false
        cover(nil)
    }

    static let controllersLayer = "controllers."

    // MARK: Questions and texts

    func ask(_ question: BCDSettingsConfirm) {
        returnFocus = focus?.focused
        confirm = question
        setFocus("settings.confirm.0")
        installButtons()
    }

    /// The question's answer: a choice's index, or nil for Cancel.
    func answer(_ index: Int?) {
        guard let question = confirm else { return }
        confirm = nil
        if let index, question.choices.indices.contains(index) { question.choices[index].run() }
        if let back = returnFocus { setFocus(back) }
        returnFocus = nil
        installButtons()
    }

    private func stepConfirm(_ move: BCDMove) {
        guard let question = confirm, let focus else { return }
        let last = question.choices.count               // Cancel comes after the choices
        let current = focus.focused.flatMap { Int($0.replacingOccurrences(of: "settings.confirm.", with: "")) } ?? 0
        let next = move == .up || move == .left ? max(0, current - 1) : min(last, current + 1)
        setFocus("settings.confirm.\(next)")
    }

    func read(_ title: String, text: String) {
        returnFocus = focus?.focused
        readerLine = 0
        // Paragraphs, and a long one (a log has no blank lines) in pieces of 40 lines, so up and
        // down move through it in steps and the reader builds only what shows.
        var paragraphs: [String] = []
        for paragraph in text.components(separatedBy: "\n\n") {
            let lines = paragraph.trimmingCharacters(in: .newlines).split(separator: "\n", omittingEmptySubsequences: false)
            guard !lines.isEmpty, !(lines.count == 1 && lines[0].isEmpty) else { continue }
            for start in stride(from: 0, to: lines.count, by: 40) {
                paragraphs.append(lines[start..<min(start + 40, lines.count)].joined(separator: "\n"))
            }
        }
        reading = BCDSettingsReading(title: title, paragraphs: paragraphs.isEmpty ? [text] : paragraphs)
        setFocus("settings.reader.close")
        installButtons()
    }

    func closeReader() {
        reading = nil
        if let back = returnFocus { setFocus(back) }
        returnFocus = nil
        installButtons()
    }

    // MARK: Buttons

    /// The buttons and their hints for where the ring is.
    func installButtons() {
        guard let focus else { return }
        let id = focus.focused
        var list: [(BCDHint, () -> Void)] = []
        if let covered {
            // A page with controls of its own (coveredLayer): A runs the one under the ring.
            list = (coveredLayer == nil ? [] : [(BCDHint(button: .a, title: "Select"), {})]) + [(BCDHint(button: .b, title: "Done"), covered)]
        } else if confirm != nil {
            list = [(BCDHint(button: .a, title: "Choose"), {}),
                    (BCDHint(button: .b, title: "Cancel"), { [weak self] in self?.back() })]
        } else if reading != nil {
            list = [(BCDHint(button: .b, title: "Close"), { [weak self] in self?.back() })]
        } else {
            let onList = id.flatMap(BCDSettingsID.listSection) != nil
            list.append((BCDHint(button: .a, title: onList ? "Open" : steps(id) ? "Next" : "Choose"), {}))
            if let id, resetters[id] != nil {
                list.append((BCDHint(button: .y, title: "Default"), { [weak self] in self?.resetFocused() }))
            }
            list.append((BCDHint(button: .b, title: navigator.back(from: id) == .close ? "Close" : "Back"),
                         { [weak self] in self?.back() }))
            list.append((BCDHint(button: .menu, title: "Close"), { [weak self] in self?.close() }))
        }
        focus.setHandlers(list)
    }

    private func resetFocused() {
        guard let id = focus?.focused else { return }
        resetters[id]?()
    }
}

// MARK: - The screen

/// Settings, full screen. `section` opens one section straight away (the preview app's
/// -BCDSection); the shell opens Settings on its list.
struct BCDSettingsRoot: View {
    @ObservedObject var store: BCDStore
    let close: () -> Void
    @EnvironmentObject private var focus: BCDFocus
    @StateObject private var model: BCDSettingsModel
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    init(store: BCDStore, close: @escaping () -> Void, section: BCDSettingsSection? = nil) {
        self.store = store
        self.close = close
        _model = StateObject(wrappedValue: BCDSettingsModel(section: section))
    }

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }
    private var mode: BCDSettingsNavigator.Mode { layout == .phonePortrait ? .stacked : .split }

    var body: some View {
        ZStack {
            BCDBackdrop()
            VStack(spacing: 0) {
                if layout == .phonePortrait { stacked } else { split }
                if focus.controllerActive && !focus.hints.isEmpty { hintBar }
            }
            if let question = model.confirm {
                BCDSettingsConfirmView(question: question)
            }
            if let reading = model.reading {
                BCDSettingsReaderView(reading: reading)
            }
            if model.controllersShown {
                BCDControllerScreen(focus: focus, close: { model.hideControllers() })
                    .contentShape(Rectangle())
                    .accessibilityAddTraits(.isModal)
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.18), value: model.controllersShown)
        .coordinateSpace(name: BCDFocus.space)
        .environment(\.bcdLayout, layout)
        .environmentObject(store)
        .environmentObject(model)
        .foregroundStyle(BCDStyle.text)
        .tint(BCDStyle.lavender)
        .preferredColorScheme(.dark)
        .onAppear {
            model.mode = mode
            model.start(focus: focus, close: close)
        }
        .onDisappear { model.stop() }
        .onChange(of: mode) { _, new in model.layoutChanged(to: new) }
        .onChange(of: focus.focused) { _, id in
            model.focusMoved(to: id)
            model.installButtons()
        }
    }

    // MARK: Side by side (a phone on its side, an iPad)

    private var split: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text("Settings").font(BCDStyle.title(layout == .pad ? 30 : 22))
                Spacer(minLength: 12)
                doneButton
            }
            .padding(.horizontal, layout.gutter).padding(.top, layout == .pad ? 16 : 6).padding(.bottom, 6)
            HStack(spacing: 0) {
                sidebar.frame(width: layout == .pad ? 280 : 228)
                Rectangle().fill(BCDStyle.stroke).frame(width: 1).padding(.vertical, 4)
                BCDSettingsDetail(section: model.selected)
            }
        }
    }

    private var sidebar: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: false) {
                VStack(spacing: layout == .pad ? 6 : 3) {
                    ForEach(model.visibleSections) { section in sidebarRow(section) }
                }
                .padding(.leading, layout == .pad ? layout.gutter - 8 : 8).padding(.trailing, 10).padding(.vertical, 8)
            }
            .onChange(of: focus.focused) { _, id in
                guard focus.controllerActive, let id, BCDSettingsID.listSection(id) != nil else { return }
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id) }
            }
            .onAppear { proxy.scrollTo(BCDSettingsID.list(model.selected)) }
        }
    }

    private func sidebarRow(_ section: BCDSettingsSection) -> some View {
        let chosen = section == model.selected
        let id = BCDSettingsID.list(section)
        return Button { model.choose(section) } label: {
            HStack(spacing: 10) {
                Image(systemName: section.symbol)
                    .font(.system(size: layout == .pad ? 16 : 14, weight: .semibold))
                    .foregroundStyle(chosen ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.textSoft))
                    .frame(width: 22)
                Text(section.title)
                    .font(BCDStyle.label(layout == .pad ? 16 : 14))
                    .foregroundStyle(chosen ? BCDStyle.text : BCDStyle.textSoft)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 9)
            .frame(height: layout == .pad ? 44 : 34)
            .background(chosen ? BCDStyle.surfaceRaised : Color.clear, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay {
                if chosen { RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 10) { model.open(section) }
        .id(id)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }

    // MARK: Upright: the list, then a section over it

    private var stacked: some View {
        ZStack {
            if model.pushed {
                VStack(spacing: 0) {
                    HStack {
                        backButton
                        Spacer(minLength: 12)
                        doneButton
                    }
                    .padding(.horizontal, 14).padding(.top, 6).padding(.bottom, 2)
                    BCDSettingsDetail(section: model.selected)
                }
                .transition(.move(edge: .trailing))
            } else {
                VStack(spacing: 0) {
                    HStack {
                        Text("Settings").font(BCDStyle.title(30))
                        Spacer(minLength: 12)
                        doneButton
                    }
                    .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 8)
                    portraitList
                }
                .transition(.move(edge: .leading))
            }
        }
        .clipped()
    }

    private var portraitList: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: false) {
                VStack(spacing: 10) {
                    ForEach(model.visibleSections) { section in portraitCard(section) }
                }
                .padding(.horizontal, 20).padding(.top, 4).padding(.bottom, 20)
            }
            .onChange(of: focus.focused) { _, id in
                guard focus.controllerActive, let id, BCDSettingsID.listSection(id) != nil else { return }
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id) }
            }
            .onAppear { proxy.scrollTo(BCDSettingsID.list(model.selected)) }
        }
    }

    private func portraitCard(_ section: BCDSettingsSection) -> some View {
        let id = BCDSettingsID.list(section)
        return Button { model.choose(section) } label: {
            HStack(spacing: 14) {
                Image(systemName: section.symbol)
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(BCDStyle.glow)
                    .frame(width: 40, height: 40)
                    .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 11, style: .continuous))
                VStack(alignment: .leading, spacing: 2) {
                    Text(section.title).font(BCDStyle.heading(17))
                    Text(section.summary).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                        .lineLimit(1).minimumScaleFactor(0.9)
                }
                Spacer(minLength: 6)
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
            }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 18) { model.open(section) }
        .id(id)
    }

    // MARK: Bars and buttons

    private var doneButton: some View {
        Button(action: close) {
            Text("Done").font(BCDStyle.label(16))
                .padding(.horizontal, 18).frame(height: 36)
                .background(BCDStyle.surfaceRaised, in: Capsule())
                .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .bcdFocusable(BCDSettingsID.done, cornerRadius: 18, action: close)
        .accessibilityLabel("Close Settings")
    }

    private var backButton: some View {
        Button { model.leaveToList() } label: {
            HStack(spacing: 4) {
                Image(systemName: "chevron.left").font(.system(size: 15, weight: .bold))
                Text("Settings").font(BCDStyle.label(16))
            }
            .foregroundStyle(BCDStyle.lavender)
            .padding(.horizontal, 10).frame(height: 36)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(BCDSettingsID.back, cornerRadius: 18) { model.leaveToList() }
    }

    private var hintBar: some View {
        HStack(spacing: layout == .phonePortrait ? 14 : 20) {
            Spacer(minLength: 0)
            if model.steps(focus.focused) {
                HStack(spacing: 7) {
                    Text("◀ ▶")
                        .font(.system(size: 10, weight: .heavy, design: .rounded))
                        .foregroundStyle(BCDStyle.ground)
                        .padding(.horizontal, 6).frame(minHeight: 24)
                        .background(BCDStyle.text, in: Capsule())
                    Text("Change").font(BCDStyle.label(14)).lineLimit(1)
                }
                .fixedSize()
                .accessibilityElement(children: .combine)
            }
            // Upright there is room for three: Menu's Close goes (B closes from the list too).
            ForEach(focus.hints.filter { layout != .phonePortrait || $0.button != .menu }) {
                BCDHintView(hint: $0).fixedSize()
            }
        }
        .padding(.horizontal, layout == .phonePortrait ? 16 : layout.gutter).frame(height: 40)
        .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke.opacity(0.7)).frame(height: 1) }
    }
}

/// One section's page: its title, then its rows. Scrolls to keep the ring in view.
struct BCDSettingsDetail: View {
    let section: BCDSettingsSection
    @EnvironmentObject private var model: BCDSettingsModel
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.bcdLayout) private var layout
    /// The page's visible frame, in the focus ring's coordinates.
    @State private var viewport = CGRect.zero

    var body: some View {
        if let custom = model.backends.custom[section] {
            // A section built elsewhere draws its whole page, scrolling and title included.
            custom().id(section)
        } else {
            page.id(section)
        }
    }

    private var page: some View {
        ScrollViewReader { proxy in
            ScrollView(.vertical, showsIndicators: false) {
                VStack(alignment: .leading, spacing: layout == .pad ? 22 : 16) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(section.title).font(BCDStyle.title(layout == .pad ? 32 : 24))
                        Text(section.summary).font(BCDStyle.body(layout == .pad ? 16 : 14)).foregroundStyle(BCDStyle.textSoft)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                    content
                }
                .padding(.horizontal, layout == .pad ? 28 : 18).padding(.top, 10).padding(.bottom, 28)
                .frame(maxWidth: layout == .pad ? 780 : .infinity, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                // Rows above the ring can still grow after it was scrolled to (text finding its
                // lines, a note arriving): keep the ring's row in view.
                .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { _ in keepRingInView(proxy) }
            }
            .onPreferenceChange(BCDSettingsOrderKey.self) { ids in
                model.orderChanged(ids)
                // A ring already on one of the rows (a turn of the device, the preview) is shown.
                keepRingInView(proxy)
            }
            .onChange(of: focus.focused) { _, id in
                // Only for a controller: a tap never makes the page jump.
                guard focus.controllerActive, let id, model.order.contains(id) else { return }
                reveal(id, proxy, animated: true)
            }
            .onChange(of: focus.controllerActive) { _, active in
                // The controller's first press shows the ring where it is: bring that row into view,
                // once the hint bar has taken its place at the bottom.
                guard active, let id = focus.focused, model.order.contains(id) else { return }
                DispatchQueue.main.async { reveal(id, proxy, animated: true) }
            }
            // The page got shorter (the hint bar arrived after the ring, a turn of the device):
            // the ring's row stays in view instead of ending up below the bar.
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(BCDFocus.space)) } action: { frame in
                viewport = frame
                keepRingInView(proxy)
            }
        }
    }

    /// For a controller only (a tap never makes the page jump).
    private func keepRingInView(_ proxy: ScrollViewProxy) {
        guard focus.controllerActive, let id = focus.focused, model.order.contains(id) else { return }
        DispatchQueue.main.async { reveal(id, proxy, animated: false) }
    }

    /// Scrolls only when the ring's row is not wholly in view, and then well inside the page.
    /// ScrollViewReader's least-scrolling scrollTo(id) left a card's row (BCDSettingsGroup lays its
    /// rows out through Group(subviews:)) with its top on the page's bottom edge, out of sight (the
    /// simulator, iOS 18.1), so the row's own frame decides and an anchor places it.
    private func reveal(_ id: String, _ proxy: ScrollViewProxy, animated: Bool) {
        let anchor: UnitPoint
        if let row = focus.frame(of: id), viewport.height > 0 {
            if row.maxY > viewport.maxY - 6 {
                anchor = UnitPoint(x: 0.5, y: 0.55)
            } else if row.minY < viewport.minY + 6 {
                anchor = UnitPoint(x: 0.5, y: 0.3)
            } else {
                return
            }
        } else {
            anchor = .center
        }
        if animated {
            withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(id, anchor: anchor) }
        } else {
            proxy.scrollTo(id, anchor: anchor)
        }
    }

    @ViewBuilder private var content: some View {
        switch section {
        case .accounts, .jit, .dock: BCDSettingsPlaceholder(section: section)
        case .vcruntime: Self.vcRuntime(model.backends.vcRuntime)
        case .gecko: Self.gecko(model.backends.gecko)
        case .memory: Self.memory(model.backends.memory)
        case .graphics: Self.graphics(model.backends.graphics)
        case .appearance: BCDAppearanceSettings()
        case .controls: Self.controls(model.backends.controls)
        case .storage: Self.storage(model.backends.storage)
        case .dotnet: Self.dotNet(model.backends.dotNet)
        case .logs: Self.logs(model.backends.logs)
        case .feedback: Self.feedback(model.backends.feedback)
        case .all: Self.allSettings(model.backends.allSettings)
        case .developer: Self.developer(model.backends.developer)
        case .updates: BCDUpdatesSettings()
        case .about: Self.about(model.backends.about)
        }
    }

    // Each backend is opened to its own type here, so each section observes it directly.
    private static func memory(_ backend: some BCDMemoryBackend) -> AnyView { AnyView(BCDMemorySettings(backend: backend)) }
    private static func graphics(_ backend: some BCDGraphicsBackend) -> AnyView { AnyView(BCDGraphicsSettings(backend: backend)) }
    private static func controls(_ backend: some BCDControlsBackend) -> AnyView { AnyView(BCDControlsSettings(backend: backend)) }
    private static func storage(_ backend: some BCDStorageBackend) -> AnyView { AnyView(BCDStorageSettings(backend: backend)) }
    private static func dotNet(_ backend: some BCDDotNetBackend) -> AnyView { AnyView(BCDDotNetSettings(backend: backend)) }
    private static func logs(_ backend: some BCDLogsBackend) -> AnyView { AnyView(BCDLogsSettings(backend: backend)) }
    private static func feedback(_ backend: some BCDFeedbackBackend) -> AnyView { AnyView(BCDFeedbackSettings(backend: backend)) }
    private static func allSettings(_ backend: some BCDAllSettingsBackend) -> AnyView { AnyView(BCDAllSettings(backend: backend)) }
    private static func about(_ backend: some BCDAboutBackend) -> AnyView { AnyView(BCDAboutSettings(backend: backend)) }
    private static func developer(_ backend: some BCDDeveloperBackend) -> AnyView { AnyView(BCDDeveloperSettings(backend: backend)) }
    private static func vcRuntime(_ backend: some BCDVCRuntimeBackend) -> AnyView { AnyView(BCDVCRuntimeSettings(backend: backend)) }
    private static func gecko(_ backend: some BCDGeckoBackend) -> AnyView { AnyView(BCDGeckoSettings(backend: backend)) }
}

// MARK: - Rows

/// Collects a section's controls in the order they are drawn: the controller's up and down.
struct BCDSettingsOrderKey: PreferenceKey {
    static var defaultValue: [String] = []
    static func reduce(value: inout [String], nextValue: () -> [String]) { value += nextValue() }
}

extension View {
    /// A control in a Settings section: the controller reaches it row by row, A runs `action`,
    /// left and right run `step` (a value row), Y runs `reset` (back to the default). `token`
    /// is the row's current value: the closures are taken again whenever it changes.
    func bcdSettingsControl(_ id: String, token: String = "", cornerRadius: CGFloat = 16,
                            step: ((Int) -> Void)? = nil, reset: (() -> Void)? = nil,
                            action: @escaping () -> Void) -> some View {
        modifier(BCDSettingsControl(id: id, token: token, cornerRadius: cornerRadius, step: step, reset: reset, action: action))
    }
}

private struct BCDSettingsControl: ViewModifier {
    @EnvironmentObject private var focus: BCDFocus
    @EnvironmentObject private var model: BCDSettingsModel
    let id: String
    let token: String
    let cornerRadius: CGFloat
    let step: ((Int) -> Void)?
    let reset: (() -> Void)?
    let action: () -> Void

    func body(content: Content) -> some View {
        content
            .bcdFocusable(id, cornerRadius: cornerRadius, action: action)
            .id(id)
            .preference(key: BCDSettingsOrderKey.self, value: [id])
            .onAppear { take() }
            .onChange(of: token) { _, _ in take() }
            .onDisappear { model.unregister(id) }
    }

    private func take() {
        focus.setAction(id, action)
        model.register(id, step: step, reset: reset)
    }
}

/// A group of rows on one card, a heading in small capitals over it and a note under it.
struct BCDSettingsGroup<Content: View>: View {
    var title: String? = nil
    var footer: String? = nil
    @ViewBuilder var content: Content
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title { BCDEyebrow(text: title).padding(.leading, 4) }
            // A hairline between rows, never above the first (it would cross the card's corners).
            Group(subviews: content) { rows in
                VStack(spacing: 0) {
                    ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                        if index > 0 { BCDSettingsSeparator() }
                        row
                    }
                }
            }
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            if let footer {
                Text(footer)
                    .font(BCDStyle.body(layout == .pad ? 14 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            }
        }
    }
}

/// The hairline between two rows of a card.
struct BCDSettingsSeparator: View {
    var body: some View {
        Rectangle().fill(BCDStyle.stroke.opacity(0.55)).frame(height: 1).padding(.leading, 14)
    }
}

/// A row's words on the left (title, and a note under it) and its control on the right.
struct BCDSettingsRowLabel<Trailing: View>: View {
    let title: String
    var note: String? = nil
    var enabled = true
    var titleColor: Color = BCDStyle.text
    @ViewBuilder var trailing: Trailing
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(BCDStyle.label(layout == .pad ? 17 : 15)).foregroundStyle(titleColor)
                    .fixedSize(horizontal: false, vertical: true)
                if let note, !note.isEmpty {
                    Text(note).font(BCDStyle.body(layout == .pad ? 14 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .multilineTextAlignment(.leading)    // a Menu's label would centre it
            .frame(maxWidth: .infinity, alignment: .leading)
            trailing
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
        .contentShape(Rectangle())
        .opacity(enabled ? 1 : 0.45)
    }
}

/// A switch: A or a tap flips it.
struct BCDSettingsToggleRow: View {
    let id: String
    let title: String
    var note: String? = nil
    let isOn: Bool
    var enabled = true
    let set: (Bool) -> Void

    var body: some View {
        Button { flip() } label: {
            BCDSettingsRowLabel(title: title, note: note, enabled: enabled) { BCDSettingsSwitchMark(isOn: isOn) }
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .bcdSettingsControl(id, token: "\(isOn)|\(enabled)", action: flip)
        .accessibilityValue(isOn ? "On" : "Off")
    }

    private func flip() { if enabled { set(!isOn) } }
}

/// The switch drawn at the end of a row.
struct BCDSettingsSwitchMark: View {
    let isOn: Bool
    var body: some View {
        Capsule()
            .fill(isOn ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke))
            .frame(width: 48, height: 29)
            .overlay(alignment: isOn ? .trailing : .leading) {
                Circle().fill(.white).frame(width: 23, height: 23).padding(3).shadow(color: .black.opacity(0.25), radius: 2)
            }
            .animation(.easeOut(duration: 0.15), value: isOn)
            .accessibilityHidden(true)
    }
}

/// One choice of a value row: what is stored and what the row shows.
struct BCDSettingsChoice: Hashable {
    var value: String
    var label: String
}

/// A value from a fixed list: left and right step it, A moves on to the next, a tap lists them.
struct BCDSettingsValueRow: View {
    let id: String
    let title: String
    var note: String? = nil
    let choices: [BCDSettingsChoice]
    let value: String
    var enabled = true
    let set: (String) -> Void
    @Environment(\.bcdLayout) private var layout

    private var index: Int? { choices.firstIndex { $0.value == value } }
    private var shown: String { index.map { choices[$0].label } ?? (value.isEmpty ? "Default" : value) }

    var body: some View {
        Menu {
            ForEach(choices, id: \.self) { choice in
                Button { set(choice.value) } label: {
                    if choice.value == value { Label(choice.label, systemImage: "checkmark") } else { Text(choice.label) }
                }
            }
        } label: {
            BCDSettingsRowLabel(title: title, note: note, enabled: enabled) {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.left").font(.system(size: 11, weight: .bold))
                        .opacity((index ?? 1) > 0 ? 0.8 : 0.25)
                    Text(shown)
                        .font(BCDStyle.label(layout == .pad ? 15 : 14)).foregroundStyle(BCDStyle.lavender)
                        .multilineTextAlignment(.trailing).lineLimit(2).minimumScaleFactor(0.85)
                    Image(systemName: "chevron.right").font(.system(size: 11, weight: .bold))
                        .opacity((index ?? -1) < choices.count - 1 ? 0.8 : 0.25)
                }
                .foregroundStyle(BCDStyle.textSoft)
                .frame(maxWidth: layout == .phonePortrait ? 150 : 230, alignment: .trailing)
            }
        }
        .disabled(!enabled)
        .bcdSettingsControl(id, token: "\(value)|\(enabled)|\(choices.count)", step: step, action: cycle)
        .accessibilityValue(shown)
    }

    /// Left and right: one choice back or on, stopping at the ends.
    private func step(_ delta: Int) {
        guard enabled, !choices.isEmpty else { return }
        let from = index ?? (delta > 0 ? -1 : choices.count)
        let to = min(max(from + delta, 0), choices.count - 1)
        if to != index { set(choices[to].value) }
    }

    /// A: the next choice, round to the first after the last.
    private func cycle() {
        guard enabled, !choices.isEmpty else { return }
        set(choices[((index ?? -1) + 1) % choices.count].value)
    }
}

/// A row that does something: opens a page, starts a download, clears a cache.
struct BCDSettingsActionRow: View {
    let id: String
    let title: String
    var note: String? = nil
    var detail: String? = nil
    var symbol: String? = "chevron.right"
    var destructive = false
    var enabled = true
    let action: () -> Void
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        Button { run() } label: {
            BCDSettingsRowLabel(title: title, note: note, enabled: enabled,
                                titleColor: destructive ? BCDStyle.alert : BCDStyle.text) {
                HStack(spacing: 8) {
                    if let detail {
                        Text(detail).font(BCDStyle.body(layout == .pad ? 15 : 13.5)).foregroundStyle(BCDStyle.textSoft)
                            .monospacedDigit().lineLimit(1)
                    }
                    if let symbol {
                        Image(systemName: symbol).font(.system(size: 13, weight: .bold))
                            .foregroundStyle(destructive ? BCDStyle.alert : BCDStyle.textSoft)
                    }
                }
            }
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .bcdSettingsControl(id, token: "\(enabled)|\(detail ?? "")", action: run)
    }

    private func run() { if enabled { action() } }
}

/// A fact, not a control: a title and note on the left, the value on the right.
struct BCDSettingsInfoRow: View {
    let title: String
    var note: String? = nil
    let value: String
    var valueColor: Color = BCDStyle.textSoft
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        BCDSettingsRowLabel(title: title, note: note) {
            Text(value).font(BCDStyle.label(layout == .pad ? 15 : 14)).foregroundStyle(valueColor)
                .monospacedDigit().multilineTextAlignment(.trailing)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A short line in amber: a change that waits for BCD's next start, say.
struct BCDSettingsNotice: View {
    let text: String
    var symbol = "arrow.clockwise.circle.fill"
    var color = BCDSettingsColor.amber
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        Label { Text(text).fixedSize(horizontal: false, vertical: true) } icon: { Image(systemName: symbol) }
            .font(BCDStyle.body(layout == .pad ? 15 : 13.5)).foregroundStyle(color)
            .padding(.horizontal, 4)
    }
}

enum BCDSettingsColor {
    /// Warnings: a change that needs a restart, an entitlement that is missing.
    static var amber: Color { BCDStyle.amber }
}

/// What Settings says about a change that applies from BCD's next start.
let bcdSettingsRestartNote = "Applies after BCD restarts: close it in the app switcher, then open it again."

// MARK: - A question over Settings

struct BCDSettingsConfirmView: View {
    let question: BCDSettingsConfirm
    @EnvironmentObject private var model: BCDSettingsModel
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        ZStack {
            Color.black.opacity(0.6).ignoresSafeArea()
                .onTapGesture { model.answer(nil) }
            VStack(alignment: .leading, spacing: 14) {
                Text(question.title).font(BCDStyle.heading(layout == .pad ? 22 : 19))
                    .fixedSize(horizontal: false, vertical: true)
                if !question.message.isEmpty {
                    Text(question.message).font(BCDStyle.body(layout == .pad ? 16 : 14)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
                VStack(spacing: 8) {
                    ForEach(Array(question.choices.enumerated()), id: \.offset) { index, choice in
                        button(choice.title, index: index, destructive: choice.destructive)
                    }
                    button("Cancel", index: question.choices.count, destructive: false)
                }
                .padding(.top, 4)
            }
            .padding(22)
            .frame(maxWidth: 420)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            .padding(24)
        }
    }

    private func button(_ title: String, index: Int, destructive: Bool) -> some View {
        let cancel = index == question.choices.count
        let answer = { model.answer(cancel ? nil : index) }
        return Button(action: answer) {
            Text(title).font(BCDStyle.label(16))
                .foregroundStyle(destructive ? BCDStyle.alert : cancel ? BCDStyle.textSoft : BCDStyle.text)
                .frame(maxWidth: .infinity, minHeight: 46)
                .background(cancel ? Color.clear : BCDStyle.surfaceRaised, in: Capsule())
                .overlay(Capsule().strokeBorder(cancel ? BCDStyle.stroke : Color.clear, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .bcdFocusable("settings.confirm.\(index)", cornerRadius: 23, action: answer)
    }
}

// MARK: - A long text, read in place

struct BCDSettingsReaderView: View {
    let reading: BCDSettingsReading
    @EnvironmentObject private var model: BCDSettingsModel
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Text(reading.title).font(BCDStyle.heading(layout == .pad ? 22 : 18)).lineLimit(1).minimumScaleFactor(0.8)
                Spacer(minLength: 12)
                Button { model.closeReader() } label: {
                    Text("Close").font(BCDStyle.label(16))
                        .padding(.horizontal, 18).frame(height: 36)
                        .background(BCDStyle.surfaceRaised, in: Capsule())
                        .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .bcdFocusable("settings.reader.close", cornerRadius: 18) { model.closeReader() }
            }
            .padding(.horizontal, layout.gutter).padding(.vertical, 10)
            ScrollViewReader { proxy in
                ScrollView(.vertical) {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(reading.paragraphs.enumerated()), id: \.offset) { index, paragraph in
                            Text(paragraph)
                                .font(.system(size: layout == .pad ? 14 : 12, design: .monospaced))
                                .foregroundStyle(BCDStyle.text.opacity(index == model.readerLine ? 1 : 0.82))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .fixedSize(horizontal: false, vertical: true)
                                .id("settings.reader.\(index)")
                        }
                    }
                    .padding(.horizontal, layout.gutter).padding(.bottom, 24)
                    .textSelection(.enabled)
                }
                .onChange(of: model.readerLine) { _, line in
                    withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("settings.reader.\(line)", anchor: .top) }
                }
            }
        }
        .background(BCDStyle.ground.ignoresSafeArea())
    }
}
