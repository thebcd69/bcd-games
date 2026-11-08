// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// Home: the game you were playing in its store's banner art, what is downloading, the
/// device's room, and your recent games as covers that glow in their own colours.
struct BCDHomeView: View {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus

    /// Round 624 (agent N S10; 2FJV7P: "How do i start my game with steam"): a signed-in Steam account with games, none of them installed.
    private var steamNothingInstalled: Bool {
        let steam = store.games.filter { $0.store == .steam }
        return store.accounts.contains { $0.store == .steam && $0.name != nil } && !steam.isEmpty && !steam.contains { $0.state.isInstalled }
    }
    @Environment(\.bcdLayout) private var layout
    /// The controller's hint bar is up: the screen is 40 pt shorter (see `fit` and `downloadsCard`).
    @Environment(\.bcdHintBar) private var hintBar
    /// An iPhone on its side with the hint bar up: the one case that gets the tighter layout below.
    /// (Without the bar, upright, or on an iPad nothing here differs from what Home always drew.)
    private var short: Bool { hintBar && layout == .phoneLandscape }
    var desktopAsUtility = false
    private var homeRecent: [BCDGame] { store.recent.filter { !desktopAsUtility || !$0.isWindowsDesktop } }
    private var homeContinue: BCDGame? { desktopAsUtility ? homeRecent.first { $0.state.isInstalled } : store.continuePlaying }
    let openLibrary: () -> Void
    let openDownloads: () -> Void
    @State private var opened: BCDGame?
    @State private var recentPosition = ScrollPosition(x: 0)
    @State private var positionedRecentInitially = false
    @State private var navigationOwner = UUID()
    @State private var rememberedRecent: String?
    @State private var homeScrollSection: String?
    @State private var recentViewport: CGRect = .zero
    @Environment(\.bcdMenu) private var menu

    var body: some View {
        GeometryReader { proxy in
            ScrollViewReader { verticalScroll in
            ScrollView(.vertical, showsIndicators: false) {
                if layout == .phonePortrait {
                    VStack(alignment: .leading, spacing: 18) {
                        heroCard().frame(height: 236).id("home.hero")
                        recentRow(tile: 150).id("home.recent.row")
                        downloadsCard()
                        storageCard
                        desktopUtility
                    }
                    .bcdGlowLayer()
                    .padding(.horizontal, layout.gutter).padding(.top, 6).padding(.bottom, 16)
                } else {
                    // With the hint bar up a phone has 40 pt less height: the layout is tighter then (`short`).
                    let sizes = fit(proxy.size.height, short: short)
                    VStack(alignment: .leading, spacing: sizes.gap) {
                        HStack(alignment: .top, spacing: 14) {
                            heroCard(compact: short && sizes.hero < 130).frame(maxWidth: .infinity).id("home.hero")
                            sidePanels(short: short).frame(width: layout == .pad ? 320 : 236)
                        }
                        // A frame smaller than its content centres it: a side card taller than the hero used to push
                        // both cards up under the top bar. `short` keeps them on the top (the card is made to fit).
                        .frame(height: sizes.hero, alignment: short ? .top : .center)
                        recentRow(tile: sizes.tile).id("home.recent.row")
                        desktopUtility
                    }
                    .bcdGlowLayer()
                    .padding(.horizontal, layout.gutter).padding(.top, 6).padding(.bottom, 12)
                }
            }
            // The ring of the first card is drawn above the card's top edge: the fade of the top 14 pt would
            // hide it (nothing scrolls under it while the hint bar is up, the page fits).
            .modifier(BCDSoftTopEdge(height: short ? 0 : 14))
            .onScrollPhaseChange { _, phase in if desktopAsUtility && phase == .interacting { focus.touched() } }
            .onChange(of: focus.focused) { _, _ in revealHome(verticalScroll) }
            .onChange(of: focus.controllerActive) { _, _ in homeScrollSection = nil; revealHome(verticalScroll) }
            .onGeometryChange(for: CGSize.self) { $0.size } action: { _ in homeScrollSection = nil; revealHome(verticalScroll) }
            }
        }
        .bcdGamePage($opened, from: "Home")
        .onChange(of: store.windowsRunning) { _, running in
            if desktopAsUtility && focus.controllerActive && running && focus.focused == "home.windows" {
                DispatchQueue.main.async {
                    guard focus.controllerActive, focus.ownsRoot("home."), store.windowsRunning,
                          focus.focused == "home.windows" else { return }
                    focus.focused = "home.windows.open"
                }
            }
        }
        .onChange(of: store.windowsNeedsRestart) { _, required in
            if desktopAsUtility && focus.controllerActive && required {
                DispatchQueue.main.async {
                    guard focus.controllerActive, focus.ownsRoot("home."), store.windowsNeedsRestart else { return }
                    focus.focused = "home.windows.restart"
                }
            }
        }
        // The game menu's "Game page" opens the page over Home.
        .onAppear { menu?.openPage = { opened = $0 }; installRecentNavigation() }
        .onDisappear { focus.removeNavigator("home.", owner: navigationOwner) }
        .onChange(of: homeRecent.prefix(10).map(\.id)) { _, _ in installRecentNavigation() }
        .onChange(of: layout) { _, _ in installRecentNavigation() }
    }

    private func revealHome(_ reader: ScrollViewProxy) {
        guard desktopAsUtility, focus.controllerActive, let id = focus.focused else { return }
        let target: String
        if id.hasPrefix("home.recent.") || id == "home.add" { target = "home.recent.row" }
        else if id.hasPrefix("home.windows") { target = "home.windows.section" }
        else if id == "home.downloads" { target = "home.downloads.section" }
        else if id.hasPrefix("home.continue") || id == "home.start" { target = "home.hero" }
        else { return }
        guard homeScrollSection != target else { return }
        homeScrollSection = target
        DispatchQueue.main.async {
            guard focus.controllerActive, homeScrollSection == target else { return }
            withAnimation(.easeOut(duration: 0.18)) { reader.scrollTo(target, anchor: .center) }
        }
    }

    private func askToStopWindows() {
        menu?.confirm(BCDGameQuestion(title: "Close Windows?",
            message: "All Windows programs will close. Unsaved changes will be lost.",
            confirm: "Stop Windows", destructive: true)) { store.stopWindows?() }
    }

    /// On a phone on its side the hero and one row of covers share the height left between
    /// the bars; the covers take about a third of it. An iPad has room for larger ones.
    ///
    /// `short` (the controller's hint bar is up, an iPhone 13 Pro then has 279 pt, not 319): the covers
    /// and the hero may go below their usual floors (96 and 130 pt), which together were 17 pt more than
    /// the height there was. No phone without the bar is that short (an iPhone 13 Pro has 319 pt, an SE 325),
    /// the floors do not bite on it, so its layout is what it was.
    private func fit(_ height: CGFloat, short: Bool = false) -> (hero: CGFloat, tile: CGFloat, gap: CGFloat) {
        let pad = layout == .pad
        let gap: CGFloat = pad ? 22 : 12
        let tile = min(pad ? 210 : 150, max(pad ? 150 : short ? 88 : 96, (height * (pad ? 0.32 : 0.34)).rounded()))
        let row = (pad ? 26 : 20) + 8 + tile + 12
        let hero = min(pad ? 420 : 260, max(short ? 108 : 130, height - 6 - 12 - gap - row))
        return (hero, tile, gap)
    }

    // MARK: The game you were playing

    /// `compact`: a hero under 130 pt (the controller's hint bar is up) keeps its three lines inside
    /// the card with less padding and spacing and a smaller title.
    @ViewBuilder private func heroCard(compact: Bool = false) -> some View {
        if let game = homeContinue {
            BCDGameButton(gameID: game.id, tap: { store.actions?.play(game) }) {
                ZStack(alignment: .bottomLeading) {
                    BCDGameArt(game: game, kind: .hero, glow: BCDGlowRequest(id: "home.hero", strength: 0.5), showTitle: false)
                    LinearGradient(stops: [.init(color: BCDStyle.ground.opacity(0.92), location: 0),
                                           .init(color: BCDStyle.ground.opacity(0.45), location: 0.5),
                                           .init(color: .clear, location: 1)],
                                   startPoint: .bottomLeading, endPoint: .topTrailing)
                    VStack(alignment: .leading, spacing: compact ? 5 : 8) {
                        HStack(spacing: 10) {
                            BCDEyebrow(text: "Continue playing")
                            if game.autoTuned {
                                Text("Auto-tuned").font(BCDStyle.label(11))
                                    .padding(.horizontal, 8).padding(.vertical, 3)
                                    .background(BCDStyle.violet.opacity(0.35), in: Capsule())
                            }
                        }
                        BCDGameLogo(game: game, titleSize: layout == .pad ? 38 : compact ? 22 : 26,
                                    maxWidth: layout == .pad ? 380 : 230, maxHeight: layout == .pad ? 110 : compact ? 40 : 58)
                        HStack(spacing: 10) {
                            // While its Play turns JIT on first, as the classic Play button said so.
                            let waiting = store.startingJIT == game.id
                            Label(waiting ? "Turning on JIT…" : "Play", systemImage: waiting ? "bolt.fill" : "play.fill")
                                .font(BCDStyle.label(14)).foregroundStyle(.white)
                                .padding(.horizontal, 14).padding(.vertical, compact ? 5 : 7)
                                .background(BCDStyle.glow, in: Capsule())
                            Text(heroDetail(game)).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.text.opacity(0.9))
                                .lineLimit(1).minimumScaleFactor(0.85)
                        }
                    }
                    .padding(layout == .pad ? 26 : compact ? 12 : 18)
                }
                .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(Color.white.opacity(0.1), lineWidth: 1))
            }
            .bcdFocusable(desktopAsUtility ? "home.continue.\(game.id)" : "home.continue", cornerRadius: 22) { store.actions?.play(game) }
            .accessibilityLabel("Continue playing \(game.title)")
        } else {
            Button(action: openLibrary) {
                VStack(alignment: .leading, spacing: 10) {
                    BCDEyebrow(text: "Start here")
                    Text("Pick a game to play").font(BCDStyle.title(28))
                    Text(steamNothingInstalled
                         ? "Open a Steam game and tap Install: BCD downloads it itself. When it is done, tap Play."
                         : "Your library shows every game you own, installed or not.")
                        .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
                .padding(22)
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 22, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(BCDStyle.glow, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .bcdFocusable("home.start", cornerRadius: 22, action: openLibrary)
        }
    }

    private func heroDetail(_ game: BCDGame) -> String {
        var parts: [String] = []
        if let last = game.lastPlayed { parts.append("Played \(BCDFormat.ago(last))") }
        if game.minutesPlayed > 0 && layout != .phoneLandscape { parts.append("\(BCDFormat.playtime(game.minutesPlayed)) in all") }
        return parts.joined(separator: " · ")
    }

    // MARK: Downloads and room

    /// Beside the hero: on a phone one card (the download when there is one, else the room),
    /// on an iPad both.
    @ViewBuilder private func sidePanels(short: Bool) -> some View {
        if layout == .pad {
            VStack(spacing: 12) { downloadsCard(); storageCard }
        } else if store.downloads.isEmpty {
            storageCard
        } else {
            downloadsCard(short: short)
        }
    }

    /// How much of the download card is drawn (`short` only): the card sits beside the hero and must fit
    /// its height. The first of these that fits is the one shown; with the room it had before the
    /// controller's hint bar, `full` is.
    private enum DownloadsDetail { case full, noQueue, small, tiny }

    private func downloadsCard(short: Bool = false) -> some View {
        Button(action: openDownloads) {
            if short {
                ViewThatFits(in: .vertical) {
                    downloadsContent(.full)
                    downloadsContent(.noQueue)   // the Downloads tab's badge counts the queue
                    downloadsContent(.small)
                    downloadsContent(.tiny)
                }
            } else {
                downloadsContent(.full)
            }
        }
        .buttonStyle(.plain)
        .bcdFocusable("home.downloads", cornerRadius: 20, action: openDownloads)
        .id("home.downloads.section")
    }

    private func downloadsContent(_ detail: DownloadsDetail) -> some View {
        let small = detail == .small || detail == .tiny
        let tiny = detail == .tiny
        return VStack(alignment: .leading, spacing: tiny ? 6 : 9) {
            BCDEyebrow(text: store.downloads.isEmpty ? "Downloads" : "Downloading")
            if let first = store.downloads.first {
                HStack(alignment: .top, spacing: small ? 10 : 12) {
                    if let game = store.games.first(where: { $0.id == first.id }) {
                        BCDGameArt(game: game, kind: .cover, showTitle: false)
                            .frame(width: small ? 32 : 42, height: small ? 48 : 63)
                            .clipShape(RoundedRectangle(cornerRadius: small ? 6 : 8, style: .continuous))
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        Text(first.title).font(BCDStyle.heading(17)).lineLimit(small ? 1 : 2)
                        Text("\(Int(first.fraction * 100))% · \(BCDFormat.eta(first) ?? BCDFormat.bytes(first.totalBytes))")
                            .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
                    }
                }
                BCDProgressBar(fraction: first.fraction)
                if layout != .phonePortrait && !tiny {
                    Text("\(BCDFormat.bytes(first.doneBytes)) of \(BCDFormat.bytes(first.totalBytes))"
                         + (first.bytesPerSecond > 0 ? " · \(BCDFormat.bytes(Int64(first.bytesPerSecond)))/s" : ""))
                        .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).monospacedDigit().lineLimit(1)
                }
                if store.downloads.count > 1 && detail == .full {
                    Text("+\(store.downloads.count - 1) more waiting").font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                }
            } else {
                Text("Nothing downloading").font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: layout == .phonePortrait ? nil : .infinity, alignment: .topLeading)
        .bcdCard(padding: tiny ? 12 : 16)
    }

    private var storageCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            BCDEyebrow(text: "Storage")
            if let free = store.system.freeDeviceBytes {
                Text("\(BCDFormat.bytes(free)) free on this device").font(BCDStyle.label(14))
                if let total = store.system.totalDeviceBytes, total > 0 {
                    BCDProgressBar(fraction: 1 - Double(free) / Double(total))
                }
                if free < Int64(3) << 30 {   // round 624 (agent N S11, H0TBAZ with 1 GB free): the prefix, a swap file and a game's files need room
                    Text("Very little space is left. iOS closes apps in the background when it needs room, and games need space to start. "
                         + "Free some up before you play.")
                        .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let external = store.system.freeExternalBytes {
                Text("\(BCDFormat.bytes(external)) free on the external drive").font(BCDStyle.label(14))
                    .foregroundStyle(BCDStyle.lavender)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: layout == .phonePortrait ? nil : .infinity, alignment: .topLeading)
        .bcdCard(padding: 16)
    }

    @ViewBuilder private var desktopUtility: some View {
        if desktopAsUtility, let desktop = store.games.first(where: \.isWindowsDesktop) {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Label("Windows desktop", systemImage: "desktopcomputer").font(BCDStyle.heading(17))
                    Spacer()
                    Text(store.windowsError != nil ? "Could not close" : store.windowsClosing ? "Closing…" : store.windowsStarting ? "Starting…" : store.windowsRunning ? "Running" : "Stopped")
                        .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                }
                HStack(spacing: 18) {
                    Button("Start") {
                        if store.windowsRunning { store.showWindows?() } else { store.actions?.play(desktop) }
                    }
                    .disabled(store.windowsRunning || store.windowsClosing || store.windowsNeedsRestart)
                    .bcdFocusable("home.windows", enabled: !store.windowsRunning && !store.windowsClosing && !store.windowsNeedsRestart) {
                        guard !store.windowsClosing && !store.windowsNeedsRestart else { return }
                        if store.windowsRunning { store.showWindows?() } else { store.actions?.play(desktop) }
                    }
                    Button("Open") { store.showWindows?() }
                        .disabled(!store.windowsRunning || store.windowsStarting || store.windowsClosing)
                        .bcdFocusable("home.windows.open", enabled: store.windowsRunning && !store.windowsStarting && !store.windowsClosing) { store.showWindows?() }
                        .id("home.windows.open")
                    Button("Stop", role: .destructive, action: askToStopWindows)
                        .disabled(!store.windowsRunning || store.windowsStarting || store.windowsClosing)
                        .bcdFocusable("home.windows.stop", enabled: store.windowsRunning && !store.windowsStarting && !store.windowsClosing, action: askToStopWindows)
                        .id("home.windows.stop")
                    Button("Restart BCD") { store.restartWindows?() }
                        .disabled(store.windowsError == nil && (store.windowsRunning || !store.windowsNeedsRestart))
                        .bcdFocusable("home.windows.restart", enabled: store.windowsError != nil || (!store.windowsRunning && store.windowsNeedsRestart)) { store.restartWindows?() }
                        .id("home.windows.restart")
                    Spacer()
                    Button { opened = desktop } label: { Image(systemName: "slider.horizontal.3") }
                        .accessibilityLabel("Windows desktop options")
                        .disabled(store.windowsRunning)
                        .bcdFocusable("home.windows.options", enabled: !store.windowsRunning) { opened = desktop }
                        .id("home.windows.options")
                }
                .font(BCDStyle.label(14)).buttonStyle(.plain)
                if store.windowsError != nil || store.windowsNeedsRestart {
                    Text(store.windowsError ?? "Starting Windows again requires restarting BCD.")
                        .font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                }
            }
            .foregroundStyle(BCDStyle.lavender)
            .bcdCard(padding: 16)
            .id("home.windows.section")
        }
    }

    // MARK: Recent games

    /// Nothing but the Wine desktop (which the app always lists) and no store game: no game of the
    /// player's yet. The Library's drive_c line uses the same test for the games added by hand.
    private var noGamesYet: Bool { store.games.allSatisfy { $0.store == .local } && store.count(.local) <= 1 }

    private var desktopFocusIDs: [String] {
        var ids: [String] = []
        if !store.windowsRunning && !store.windowsClosing && !store.windowsNeedsRestart { ids.append("home.windows") }
        if store.windowsRunning && !store.windowsStarting && !store.windowsClosing { ids += ["home.windows.open", "home.windows.stop"] }
        if store.windowsError != nil || (!store.windowsRunning && store.windowsNeedsRestart) { ids.append("home.windows.restart") }
        if !store.windowsRunning { ids.append("home.windows.options") }
        return ids
    }

    private func installRecentNavigation() {
        guard desktopAsUtility else { return }
        focus.setNavigator("home.", owner: navigationOwner) { direction in
            let ids = homeRecent.prefix(10).map { "home.recent." + $0.id } + ["home.add"]
            let hero = homeContinue.map { "home.continue." + $0.id } ?? "home.start"
            guard let current = focus.focused,
                  let next = BCDHomeNavigation.target(from: current, direction: direction, recent: ids,
                      desktop: desktopFocusIDs, hero: hero, downloadsBelow: layout == .phonePortrait,
                      downloadsVisible: layout != .phoneLandscape || !store.downloads.isEmpty, remembered: rememberedRecent) else { return false }
            if ids.contains(current) { rememberedRecent = current }
            focus.focused = next
            return true
        }
    }

    private func positionRecentInitially(tile: CGFloat) {
        guard desktopAsUtility, !positionedRecentInitially, !homeRecent.isEmpty else { return }
        positionedRecentInitially = true
        if focus.controllerActive, let id = focus.focused, id.hasPrefix("home.recent.") || id == "home.add" {
            revealRecent(tile: tile)
        } else {
            recentPosition.scrollTo(x: 0)
        }
    }

    private func revealRecent(tile: CGFloat) {
        guard desktopAsUtility, focus.controllerActive, let id = focus.focused,
              id.hasPrefix("home.recent.") || id == "home.add" else { return }
        DispatchQueue.main.async {
            guard focus.controllerActive, focus.focused == id else { return }
            let ids = homeRecent.prefix(10).map { "home.recent." + $0.id } + ["home.add"]
            guard let index = ids.firstIndex(of: id) else { return }
            let x = BCDRailPosition.offset(index: index, count: ids.count, width: (tile * 2 / 3).rounded(),
                                           spacing: layout == .pad ? 18 : 12, viewport: recentViewport.width)
            withAnimation(.easeOut(duration: 0.14)) { recentPosition.scrollTo(x: x) }
        }
    }

    private func recentRow(tile: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recently played").font(BCDStyle.heading(layout == .pad ? 20 : 16))
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: layout == .pad ? 18 : 12) { recentCards(tile: tile) }
                .bcdGlowLayer()
                .padding(.vertical, 6).padding(.horizontal, 2)
            }
            .modifier(BCDHomeRecentPosition(enabled: desktopAsUtility, height: tile + 12, position: $recentPosition))
            .onAppear { positionRecentInitially(tile: tile) }
            .onChange(of: homeRecent.prefix(10).map(\.id)) { _, _ in positionRecentInitially(tile: tile); revealRecent(tile: tile) }
            .scrollClipDisabled()
            .onScrollPhaseChange { _, phase in if desktopAsUtility && phase == .interacting { focus.touched() } }
            .onChange(of: focus.focused) { _, _ in revealRecent(tile: tile) }
            .onChange(of: focus.controllerActive) { _, _ in revealRecent(tile: tile) }
            .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(BCDFocus.space)) } action: { frame in recentViewport = frame; revealRecent(tile: tile) }
        }
    }
    @ViewBuilder private func recentCards(tile: CGFloat) -> some View {
                    ForEach(homeRecent.prefix(10).map { BCDHomeRecentItem(game: $0) }) { item in
                        let game = item.game
                        BCDCoverCard(game: game, focusID: "home.recent.\(game.id)", showText: false) { opened = game }
                            .frame(width: (tile * 2 / 3).rounded(), height: tile)
                            .id("home.recent.\(game.id)")
                    }
                    Button { store.actions?.addGameFromFiles() } label: {
                        VStack(spacing: 8) {
                            Image(systemName: "plus").font(.system(size: 20, weight: .semibold))
                            Text("Add a game").font(BCDStyle.label(13)).multilineTextAlignment(.center)
                        }
                        .foregroundStyle(BCDStyle.textSoft)
                        .frame(width: (tile * 2 / 3).rounded(), height: tile)
                        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .strokeBorder(BCDStyle.stroke, style: StrokeStyle(lineWidth: 2, dash: [6, 5])))
                    }
                    .buttonStyle(.plain)
                    .bcdFocusable("home.add", cornerRadius: 12) { store.actions?.addGameFromFiles() }
                    .id("home.add")
                    // A new install: beside the "+", how games get in (the Library says it too).
                    if noGamesYet {
                        Text("Copy a game's folder into BCD › wine › drive_c with the Files app, then tap + Add a game.")
                            .font(BCDStyle.body(layout == .pad ? 15 : 13)).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(width: layout == .pad ? 340 : layout == .phonePortrait ? 220 : 250, alignment: .leading)
                    }
    }

}

/// Keep the original Home scroll behavior outside the native Duo interface.
private struct BCDHomeRecentPosition: ViewModifier {
    var enabled: Bool
    var height: CGFloat
    @Binding var position: ScrollPosition
    @ViewBuilder func body(content: Content) -> some View {
        if enabled { content.frame(height: height).defaultScrollAnchor(.leading).scrollPosition($position) }
        else { content }
    }
}

/// The scroll target and ForEach identity must be the same as the controller focus ID.
private struct BCDHomeRecentItem: Identifiable {
    let game: BCDGame
    var id: String { "home.recent." + game.id }
}
