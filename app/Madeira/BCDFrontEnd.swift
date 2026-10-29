// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// BCD's interface: the shell around Home, Library and Downloads (docs/BCD-UI-ARCHITECTURE.md).
/// Tabs across the top (LB/RB), the device's state on the right, the buttons' hints along
/// the bottom; on an upright iPhone the tabs move to the bottom. While a game runs
/// (`store.inGame`) the whole tree is gone, so it draws nothing and holds no animation.
struct BCDFrontEnd: View {
    @ObservedObject var store: BCDStore
    @StateObject private var focus: BCDFocus
    @State private var tab: BCDTab
    @State private var showSettings = false
    /// The section Settings opens at (the update chip's Updates); nil is its list.
    @State private var settingsSection: BCDSettingsSection?
    /// Y on the library tab: each press opens or closes its search.
    @State private var librarySearch = 0
    /// The menus over the shell (BCDMenu.swift): a game's, on a long press or X, and the library's sort.
    /// A plain reference: the screens only call it; the overlay (BCDMenuHost) is what observes it.
    @State private var cardMenu = BCDMenuModel()
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    init(store: BCDStore, tab: BCDTab = .home, focus: BCDFocus? = nil) {
        self.store = store
        _tab = State(initialValue: tab)
        _focus = StateObject(wrappedValue: focus ?? BCDFocus())
    }

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }

    /// The hint bar under the screen: a controller was the last input and the screen has buttons.
    /// (Never on an upright iPhone, which has the tab bar there.) The screens read it as `\.bcdHintBar`.
    private var hintBarShown: Bool { layout != .phonePortrait && focus.controllerActive && !focus.hints.isEmpty }

    var body: some View {
        Group {
            if store.inGame {
                Color.black.ignoresSafeArea()
            } else {
                shell
            }
        }
        .onChange(of: store.inGame) { _, playing in
            focus.enabled = !playing
            // The game gets the memory the pictures held; they decode again afterwards.
            if playing { BCDArt.shared.purge() }
        }
    }

    private var shell: some View {
        VStack(spacing: 0) {
            if layout != .phonePortrait { topBar }
            else { portraitTitle }
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            if layout == .phonePortrait { portraitTabs }
            else if hintBarShown { hintBar }
        }
        .background { BCDBackdrop() }
        .coordinateSpace(name: BCDFocus.space)
        // The menu reads the focus and the layout from the environment: it sits inside them.
        .modifier(BCDMenuHost(model: cardMenu))
        .environment(\.bcdLayout, layout)
        .environment(\.bcdHintBar, hintBarShown)
        .environmentObject(focus)
        .environmentObject(store)
        .environment(\.bcdMenu, cardMenu)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
        // Settings clears the buttons when it goes: the shell's come back once it has.
        .fullScreenCover(isPresented: $showSettings, onDismiss: { installButtons(); BCDPresentation.didClose() }) {
            BCDSettingsRoot(store: store, close: { showSettings = false; settingsSection = nil }, section: settingsSection)
                .environmentObject(focus)
        }
        .onAppear {
            cardMenu.store = store
            installButtons()
        }
        .onChange(of: tab) { _, _ in installButtons() }
        // X shows while the ring is on a game's card.
        .onChange(of: focus.focused) { _, _ in installButtons() }
    }

    // MARK: Screens

    @ViewBuilder private var content: some View {
        switch tab {
        case .home: BCDHomeView(openLibrary: { tab = .library }, openDownloads: { tab = .downloads })
        case .library: BCDLibraryView(searchRequest: librarySearch)
        case .downloads: BCDDownloadsView()
        }
    }

    private func installButtons() {
        // A page or menu over the shell has its own buttons; Settings sets its own too.
        guard !focus.layerOpen, !showSettings else { return }
        var list: [(BCDHint, () -> Void)] = [
            (BCDHint(button: .leftShoulder, title: "Previous"), { cycle(-1) }),
            (BCDHint(button: .rightShoulder, title: "Next"), { cycle(1) }),
            (BCDHint(button: .a, title: tab == .downloads ? "Pause or resume" : "Open"), {}),
        ]
        // The library's search: Y opens it (and closes it again).
        if tab == .library { list.append((BCDHint(button: .y, title: "Search"), { librarySearch += 1 })) }
        // A game's menu: X while the ring is on its card (Home's recent games and Continue playing, the Library's grid).
        if tab != .downloads, let game = BCDMenuModel.gameID(focused: focus.focused, store: store) {
            list.append((BCDHint(button: .x, title: "Menu"), { cardMenu.showCard(game) }))
        }
        list.append((BCDHint(button: .menu, title: "Settings"), { openSettings() }))
        focus.setHandlers(list)
    }

    private func openSettings() {
        showSettings = true
    }

    private func cycle(_ step: Int) {
        let all = BCDTab.allCases
        let index = (all.firstIndex(of: tab)! + step + all.count) % all.count
        withAnimation(.easeOut(duration: 0.18)) { tab = all[index] }
    }

    // MARK: Bars

    private var topBar: some View {
        HStack(spacing: 8) {
            if focus.controllerActive { shoulder("LB") }
            ForEach(BCDTab.allCases) { item in tabButton(item) }
            if focus.controllerActive { shoulder("RB") }
            Spacer(minLength: 12)
            statusChips
            settingsButton
        }
        .padding(.horizontal, layout.gutter).padding(.top, layout == .pad ? 10 : 6).padding(.bottom, 6)
    }

    private var portraitTitle: some View {
        HStack {
            Text(tab.title).font(BCDStyle.title(30))
            Spacer()
            statusChips
            settingsButton
        }
        .padding(.horizontal, 20).padding(.top, 8).padding(.bottom, 6)
    }

    private var portraitTabs: some View {
        HStack {
            ForEach(BCDTab.allCases) { item in
                Button { tab = item } label: {
                    VStack(spacing: 4) {
                        Image(systemName: item.symbol).font(.system(size: 20, weight: .semibold))
                        Text(item.title).font(BCDStyle.label(11))
                    }
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .foregroundStyle(item == tab ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.textSoft))
                    .overlay(alignment: .topTrailing) {
                        if item == .downloads && !store.downloads.isEmpty { badge.offset(x: -22, y: -2) }
                    }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 6)
        .background(BCDStyle.ground.opacity(0.92).ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke).frame(height: 1) }
    }

    private func tabButton(_ item: BCDTab) -> some View {
        let selected = item == tab
        return Button { withAnimation(.easeOut(duration: 0.18)) { tab = item } } label: {
            HStack(spacing: 7) {
                Text(item.title).font(BCDStyle.heading(layout == .pad ? 18 : 16))
                if item == .downloads && !store.downloads.isEmpty { badge }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .foregroundStyle(selected ? BCDStyle.ground : BCDStyle.textSoft)
            .background { if selected { Capsule().fill(BCDStyle.text) } }
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var badge: some View {
        Text("\(store.downloads.count)")
            .font(.system(size: 11, weight: .heavy, design: .rounded))
            .foregroundStyle(BCDStyle.onAccent)
            .frame(minWidth: 18, minHeight: 18)
            .background(BCDStyle.glow, in: Capsule())
    }

    private func shoulder(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 11, weight: .heavy, design: .rounded))
            .foregroundStyle(BCDStyle.ground)
            .frame(width: 30, height: 22)
            .background(BCDStyle.text, in: Capsule())
            .accessibilityHidden(true)
    }

    @ViewBuilder private var statusChips: some View {
        let system = store.system
        HStack(spacing: 8) {
            BCDUpdateChip { settingsSection = .updates; showSettings = true }
            if layout != .phonePortrait || system.jit != .on {
                BCDStatusChip(text: system.jit == .on ? "JIT on" : system.jit == .waiting ? "JIT…" : "JIT off",
                              color: system.jit == .on ? BCDStyle.mint : BCDStyle.amber)
            }
            if layout == .pad, let limit = system.memoryLimitBytes {
                BCDStatusChip(text: "Memory \(BCDFormat.bytes(limit))",
                              color: system.increasedMemoryLimit ? BCDStyle.mint : BCDStyle.amber)
            }
            if layout != .phonePortrait, let name = system.controllerName {
                HStack(spacing: 6) {
                    Image(systemName: "gamecontroller.fill")
                    Text(system.controllerBattery.map { "\(name) · \($0)%" } ?? name).lineLimit(1)
                }
                .font(BCDStyle.label(12)).foregroundStyle(BCDStyle.textSoft)
            }
        }
    }

    private var settingsButton: some View {
        Button { openSettings() } label: {
            Image(systemName: "gearshape.fill")
                .font(.system(size: 17, weight: .semibold))
                .frame(width: 38, height: 38)
                .background(BCDStyle.surfaceRaised, in: Circle())
                .overlay(Circle().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Settings")
    }

    private var hintBar: some View {
        HStack(spacing: 22) {
            Spacer()
            ForEach(focus.hints.filter { $0.button != .leftShoulder && $0.button != .rightShoulder }) { BCDHintView(hint: $0) }
        }
        // Under a menu or page the overlay draws its own hints; the bar keeps its room.
        .opacity(focus.layerOpen ? 0 : 1)
        .padding(.horizontal, layout.gutter).frame(height: 40)
        .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke.opacity(0.7)).frame(height: 1) }
    }
}

enum BCDTab: String, CaseIterable, Identifiable {
    case home, library, downloads
    var id: String { rawValue }
    var title: String {
        switch self {
        case .home: return "Home"
        case .library: return "Library"
        case .downloads: return "Downloads"
        }
    }
    var symbol: String {
        switch self {
        case .home: return "house.fill"
        case .library: return "square.grid.2x2.fill"
        case .downloads: return "arrow.down.circle.fill"
        }
    }
}
