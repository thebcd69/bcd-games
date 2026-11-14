// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// Portions copyright 2026 creeper4004: colour profiles and Settings > Appearance, from thebcd69/bcd-games pull request #27; GPL-3.0-only, modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import SwiftUI

/// BCD's interface: the shell around Home, Library and Downloads (docs/BCD-UI-ARCHITECTURE.md).
/// Tabs across the top (LB/RB), the device's state on the right, the buttons' hints along
/// the bottom; on an upright iPhone the tabs move to the bottom. While a game runs
/// (`store.inGame`) the whole tree is gone, so it draws nothing and holds no animation.
// The native Duo interface (below, BCDDuoFrontEnd) is thebcd69/bcd-games#43 by Paulo Eduardo Correa / pauloec; it runs only on the foldable
// iPhone with Duo enhancements on (BCDDuoFeatures). Every other device draws BCDOriginalFrontEnd, the shell BCD had before.
struct BCDFrontEnd: View {
    @ObservedObject var store: BCDStore
    var tab: BCDTab = .home
    var focus: BCDFocus? = nil
    var body: some View {
        if BCDDuoFeatures.active { BCDDuoFrontEnd(store: store, tab: tab, focus: focus) }
        else { BCDOriginalFrontEnd(store: store, tab: tab, focus: focus) }
    }
}

private struct BCDOriginalFrontEnd: View {
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
            BCDUpdateChip(compact: layout == .phonePortrait && system.jit != .on) { settingsSection = .updates; showSettings = true }
            if layout != .phonePortrait || system.jit != .on {
                // Its words are never cut (beside "Update available" on a phone it used to shrink to its dot); there the update
                // chip says "Update" so the title keeps its line.
                let chip = BCDStatusChip(text: system.jit == .on ? "JIT on" : system.jit == .waiting ? "JIT…" : "JIT off",
                                         color: system.jit == .on ? BCDStyle.mint : BCDStyle.amber)
                    .fixedSize()
                // Build 655 (owner, 2026-11-11; a tester's idea): "JIT off" is a button for Enable JIT, the same action as
                // Settings › JIT's own button. "JIT on" and "JIT…" stay plain labels.
                if system.jit == .off {
                    Button { store.actions?.enableJIT() } label: { chip }
                        .buttonStyle(.plain)
                        .accessibilityLabel("JIT is off. Turn JIT on")
                        .accessibilityIdentifier("bcd.home.jit-chip")
                } else {
                    chip
                }
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

// Native Duo navigation; the original interface above remains the fallback.
private enum BCDDuoTab: String, CaseIterable, Identifiable {
    case home, library, downloads, settings
    var id: String { rawValue }
    var title: String { self == .settings ? "Settings" : BCDTab(rawValue: rawValue)!.title }
    var symbol: String { self == .settings ? "gearshape.fill" : BCDTab(rawValue: rawValue)!.symbol }
}
private struct BCDDuoFrontEnd: View {
    @ObservedObject var store: BCDStore
    @StateObject private var focus: BCDFocus
    @State private var tab: BCDDuoTab
    /// The section Settings opens at (the update chip's Updates); nil is its list.
    @State private var settingsSection: BCDSettingsSection?
    @State private var settingsRequest = 0
    /// Y on the library tab: each press opens or closes its search.
    @State private var librarySearch = 0
    /// The menus over the shell (BCDMenu.swift): a game's, on a long press or X, and the library's sort.
    /// A plain reference: the screens only call it; the overlay (BCDMenuHost) is what observes it.
    @State private var cardMenu = BCDMenuModel()
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    init(store: BCDStore, tab: BCDTab = .home, focus: BCDFocus? = nil) {
        self.store = store
        _tab = State(initialValue: BCDDuoTab(rawValue: tab.rawValue)!)
        _focus = StateObject(wrappedValue: focus ?? BCDFocus())
    }

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }

    /// The hint bar under the screen: a controller was the last input and the screen has buttons.
    /// (Never on an upright iPhone, which has the tab bar there.) The screens read it as `\.bcdHintBar`.
    private var hintBarShown: Bool { focus.controllerActive && !focus.hints.isEmpty }
    private var hideTabsForController: Bool { focus.controllerActive && (!focus.layerOpen || tab == .settings) }

    var body: some View {
        Group {
            if store.inGame {
                Color.black.ignoresSafeArea()
            } else {
                shell
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("BCDDesktopDidQuit"))) { _ in
            select(.home)
        }
        .onChange(of: store.restartMessage) { _, message in
            guard let message else { return }
            store.restartMessage = nil
            cardMenu.confirm(BCDGameQuestion(title: "Close BCD to restart?", message: message,
                confirm: "Close BCD", destructive: false, defaultCancel: true)) { store.closeForRestart?() }
        }
        .simultaneousGesture(TapGesture().onEnded { focus.touched() })
        .onChange(of: store.inGame) { _, playing in
            focus.enabled = !playing
            // The game gets the memory the pictures held; they decode again afterwards.
            if playing { BCDArt.shared.purge() }
        }
    }

    private var shell: some View {
        VStack(spacing: 0) {
            if focus.controllerActive && hideTabsForController { controllerTabs }
            // Reserve actual layout space: TabView's UIKit containers do not
            // consistently propagate an ancestor's safe-area inset to pages.
            nativeTabs.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
            .coordinateSpace(name: BCDFocus.space)
            .modifier(BCDMenuHost(model: cardMenu))
            .environment(\.bcdLayout, layout)
            .environment(\.bcdHintBar, hintBarShown)
            .environmentObject(focus)
            .environmentObject(store)
            .environment(\.bcdMenu, cardMenu)
            .foregroundStyle(BCDStyle.text)
            .tint(BCDStyle.lavender)
            .preferredColorScheme(.dark)
            .onAppear { focus.scope(to: tab.rawValue + "."); cardMenu.store = store; installButtons() }
            .onChange(of: tab) { _, _ in DispatchQueue.main.async { installButtons() } }
            .onChange(of: focus.focused) { _, _ in installButtons() }
    }

    /// System tabs own their glass and placement: a sidebar on the inner display,
    /// vertical bars where Duo requires them, and standard tabs on other phones.
    private var nativeTabs: some View {
        TabView(selection: Binding(get: { tab }, set: select)) {
            ForEach(BCDDuoTab.allCases) { item in
                Tab(item.title, systemImage: item.symbol, value: item) {
                    page(item)
                        .toolbarVisibility(hideTabsForController ? .hidden : .visible, for: .tabBar)
                }
                .badge(item == .downloads ? store.downloads.count : 0)
            }
        }
        .tabViewStyle(.sidebarAdaptable)
        .defaultAdaptableTabBarPlacement(.sidebar)
        .tabViewSidebarHeader {
            Label("BCD", systemImage: "gamecontroller.fill")
                .font(.title2.bold()).padding(.vertical, 12)
        }
        .tabViewSidebarFooter {
            jitStatus.padding(.vertical, 10)
        }
    }

    @ViewBuilder private func page(_ item: BCDDuoTab) -> some View {
        if item == .settings {
            BCDDuoSettingsRoot(store: store, close: { select(.home) }, section: settingsSection, requestID: settingsRequest,
                               active: tab == .settings, cycleTabs: cycle)
        } else {
        NavigationStack {
            GeometryReader { proxy in
                // A sidebar can leave a compact content column in a regular window.
                // Adapt that content without changing the system tab bar traits.
                let pageLayout = BCDLayout.of(horizontal: proxy.size.width < 700 ? .compact : horizontal,
                                              vertical: vertical)
                VStack(spacing: 0) {
                    content(for: item)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                    if hintBarShown { hintBar }
                }
                .environment(\.bcdLayout, pageLayout)
                .background { BCDBackdrop() }
            }
            .navigationTitle(item.title)
            .navigationBarTitleDisplayMode(.large)
            .toolbar(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    jitStatus
                }
                ToolbarItem(placement: .primaryAction) {
                    BCDUpdateChip { settingsSection = .updates; settingsRequest += 1; select(.settings) }
                }
            }
        }
        }
    }

    @ViewBuilder private func content(for item: BCDDuoTab) -> some View {
        switch item {
        case .home: BCDHomeView(desktopAsUtility: true, openLibrary: { select(.library) }, openDownloads: { select(.downloads) })
        case .library: BCDDuoLibraryView(searchRequest: librarySearch)
        case .downloads: BCDDownloadsView(nativeControllerNavigation: true)
        case .settings: EmptyView()
        }
    }

    private func installButtons() {
        // A page or menu over the shell has its own buttons; Settings sets its own too.
        guard !focus.layerOpen, tab != .settings else { return }
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

    private func select(_ next: BCDDuoTab) {
        guard next != tab else { return }
        BCDDuoPageOwner.end()
        focus.scope(to: next.rawValue + ".")
        tab = next
    }

    private func openSettings() { select(.settings) }

    private func cycle(_ step: Int) {
        let all = BCDDuoTab.allCases
        let index = (all.firstIndex(of: tab)! + step + all.count) % all.count
        withAnimation(.easeOut(duration: 0.18)) { select(all[index]) }
    }

    // MARK: Native navigation controls

    @ViewBuilder private var jitStatus: some View {
        let ready = store.system.jit == .on
        let label = Label(ready ? "JIT on" : store.system.jit == .waiting ? "JIT…" : "JIT off",
                          systemImage: ready ? "bolt.fill" : "bolt.slash")
            .font(.caption.weight(.semibold))
            .foregroundStyle(ready ? BCDStyle.mint : BCDStyle.textSoft)
        // Build 655 (owner, 2026-11-11): "JIT off" runs Enable JIT here too, as on the classic Home.
        if store.system.jit == .off {
            Button { store.actions?.enableJIT() } label: { label }
                .buttonStyle(.plain)
                .accessibilityLabel("JIT is off. Turn JIT on")
                .accessibilityIdentifier("bcd.duo.jit-chip")
        } else {
            label.accessibilityLabel(ready ? "JIT is enabled" : "JIT is not enabled")
        }
    }

    /// The system bar yields space to gamepad browsing, but destinations remain visible.
    private var controllerTabs: some View {
        let canCycle = focus.hints.contains { $0.button == .leftShoulder }
            && focus.hints.contains { $0.button == .rightShoulder }
        return HStack(spacing: 8) {
            Text("LB").font(.caption2.bold()).padding(6).background(BCDStyle.surfaceRaised, in: Capsule())
            ForEach(BCDDuoTab.allCases) { item in
                Button { select(item) } label: {
                    Label(item.title, systemImage: item.symbol)
                        .font(.caption.weight(tab == item ? .bold : .medium))
                        .lineLimit(1).minimumScaleFactor(0.8)
                        .padding(.horizontal, 10).padding(.vertical, 8)
                        .frame(maxWidth: .infinity)
                        .background(tab == item ? BCDStyle.violet : .clear, in: Capsule())
                }
                .buttonStyle(.plain)
                .disabled(!canCycle)
                .accessibilityAddTraits(tab == item ? .isSelected : [])
            }
            Text("RB").font(.caption2.bold()).padding(6).background(BCDStyle.surfaceRaised, in: Capsule())
        }
        .foregroundStyle(BCDStyle.text)
        .opacity(canCycle ? 1 : 0.55)
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(BCDStyle.ground)
        .overlay(alignment: .bottom) { Rectangle().fill(BCDStyle.stroke).frame(height: 1) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Main tabs. Use the left and right shoulder buttons to switch.")
    }

    private var hintBar: some View {
        HStack(spacing: 22) {
            Spacer()
            ForEach(focus.hints.filter { $0.button != .leftShoulder && $0.button != .rightShoulder }) { BCDHintView(hint: $0) }
        }
        .opacity(focus.layerOpen ? 0 : 1)
        .padding(.horizontal, layout.gutter).frame(height: 40)
        .background(BCDStyle.ground.ignoresSafeArea(edges: .bottom))
        .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke).frame(height: 1) }
    }
}
