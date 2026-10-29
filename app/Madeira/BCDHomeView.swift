// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// Home: the game you were playing in its store's banner art, what is downloading, the
/// device's room, and your recent games as covers that glow in their own colours.
struct BCDHomeView: View {
    @EnvironmentObject private var store: BCDStore
    @Environment(\.bcdLayout) private var layout
    /// The controller's hint bar is up: the screen is 40 pt shorter (see `fit` and `downloadsCard`).
    @Environment(\.bcdHintBar) private var hintBar
    /// An iPhone on its side with the hint bar up: the one case that gets the tighter layout below.
    /// (Without the bar, upright, or on an iPad nothing here differs from what Home always drew.)
    private var short: Bool { hintBar && layout == .phoneLandscape }
    let openLibrary: () -> Void
    let openDownloads: () -> Void
    @State private var opened: BCDGame?
    @Environment(\.bcdMenu) private var menu

    var body: some View {
        GeometryReader { proxy in
            ScrollView(.vertical, showsIndicators: false) {
                if layout == .phonePortrait {
                    VStack(alignment: .leading, spacing: 18) {
                        heroCard().frame(height: 236)
                        recentRow(tile: 150)
                        downloadsCard()
                        storageCard
                    }
                    .bcdGlowLayer()
                    .padding(.horizontal, layout.gutter).padding(.top, 6).padding(.bottom, 16)
                } else {
                    // With the hint bar up a phone has 40 pt less height: the layout is tighter then (`short`).
                    let sizes = fit(proxy.size.height, short: short)
                    VStack(alignment: .leading, spacing: sizes.gap) {
                        HStack(alignment: .top, spacing: 14) {
                            heroCard(compact: short && sizes.hero < 130).frame(maxWidth: .infinity)
                            sidePanels(short: short).frame(width: layout == .pad ? 320 : 236)
                        }
                        // A frame smaller than its content centres it: a side card taller than the hero used to push
                        // both cards up under the top bar. `short` keeps them on the top (the card is made to fit).
                        .frame(height: sizes.hero, alignment: short ? .top : .center)
                        recentRow(tile: sizes.tile)
                    }
                    .bcdGlowLayer()
                    .padding(.horizontal, layout.gutter).padding(.top, 6).padding(.bottom, 12)
                }
            }
            // The ring of the first card is drawn above the card's top edge: the fade of the top 14 pt would
            // hide it (nothing scrolls under it while the hint bar is up, the page fits).
            .modifier(BCDSoftTopEdge(height: short ? 0 : 14))
        }
        .bcdGamePage($opened, from: "Home")
        // The game menu's "Game page" opens the page over Home.
        .onAppear { menu?.openPage = { opened = $0 } }
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
        if let game = store.continuePlaying {
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
                                .font(BCDStyle.label(14)).foregroundStyle(BCDStyle.onAccent)
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
            .bcdFocusable("home.continue", cornerRadius: 22) { store.actions?.play(game) }
            .accessibilityLabel("Continue playing \(game.title)")
        } else {
            Button(action: openLibrary) {
                VStack(alignment: .leading, spacing: 10) {
                    BCDEyebrow(text: "Start here")
                    Text("Pick a game to play").font(BCDStyle.title(28))
                    Text("Your library shows every game you own, installed or not.")
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
            }
            if let external = store.system.freeExternalBytes {
                Text("\(BCDFormat.bytes(external)) free on the external drive").font(BCDStyle.label(14))
                    .foregroundStyle(BCDStyle.lavender)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: layout == .phonePortrait ? nil : .infinity, alignment: .topLeading)
        .bcdCard(padding: 16)
    }

    // MARK: Recent games

    /// Nothing but the Wine desktop (which the app always lists) and no store game: no game of the
    /// player's yet. The Library's drive_c line uses the same test for the games added by hand.
    private var noGamesYet: Bool { store.games.allSatisfy { $0.store == .local } && store.count(.local) <= 1 }

    private func recentRow(tile: CGFloat) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Recently played").font(BCDStyle.heading(layout == .pad ? 20 : 16))
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: layout == .pad ? 18 : 12) {
                    ForEach(store.recent.prefix(10)) { game in
                        BCDCoverCard(game: game, focusID: "home.recent.\(game.id)", showText: false) { opened = game }
                            .frame(width: (tile * 2 / 3).rounded(), height: tile)
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
                    // A new install: beside the "+", how games get in (the Library says it too).
                    if noGamesYet {
                        Text("Copy a game's folder into BCD › wine › drive_c with the Files app, then tap + Add a game.")
                            .font(BCDStyle.body(layout == .pad ? 15 : 13)).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(width: layout == .pad ? 340 : layout == .phonePortrait ? 220 : 250, alignment: .leading)
                    }
                }
                .bcdGlowLayer()
                .padding(.vertical, 6).padding(.horizontal, 2)
            }
            .scrollClipDisabled()
        }
    }
}
