// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// The Steam Workshop pages (docs/BCD-UI-ARCHITECTURE.md): the Library's Workshop area (the installed Steam games, those with a Workshop first)
// and a game's Workshop page (the account's subscribed items, what is installed, Browse and search, the actions on each item). Full screen over
// the shell, from the Library's Workshop button and from Options › Steam › Steam Workshop. The values, words and rules are BCDWorkshop.swift's;
// everything is read and asked through BCDWorkshopBackend (BCDSteamWorkshop/BCDWorkshopLive.swift in the app, a sample in the preview app).
//
// Opening a page asks Steam for the account's subscribed items (a read). Nothing else happens until a button is pressed; subscribing and
// unsubscribing, which change the Steam account, ask a question first.

/// Which page the cover is on.
enum BCDWorkshopPage: Equatable {
    case area
    case game(Int)
    case browse(Int)
}

/// An item whose card is open.
private struct BCDWorkshopCardRoute: Equatable {
    var item: BCDWorkshopListing
    var appID: Int
}

/// An action waiting for the player's yes.
private struct BCDWorkshopAsk: Equatable {
    var action: BCDWorkshop.Action
    var item: BCDWorkshopListing
    var appID: Int
    var question: BCDGameQuestion
}

struct BCDWorkshopCover: View {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical
    @Environment(\.openURL) private var openURL
    let route: BCDWorkshopRoute
    let close: () -> Void
    private let backend: BCDWorkshopBackend?

    @State private var page: BCDWorkshopPage
    /// Bumped when the backend says something changed: the page reads again.
    @State private var tick = 0
    @State private var card: BCDWorkshopCardRoute?
    @State private var asking: BCDWorkshopAsk?
    // Browse
    @State private var order = BCDWorkshopOrder.popular
    @State private var query = ""
    @State private var searched = ""
    @State private var results: [BCDWorkshopListing] = []
    @State private var total = 0
    @State private var browsePage = 0
    @State private var loading = false
    @State private var failure: String?
    /// Each new Browse or search takes a number; the task of an older one ends without writing.
    @State private var browseToken = 0

    static let layer = "workshop."
    static let cardLayer = "workshop.card."
    static let askLayer = "workshop.ask."

    /// The preview app's screenshots: a page to start on, and an item's card to open once the page stands.
    private let preview: (page: BCDWorkshopPage?, card: Bool)

    init(route: BCDWorkshopRoute, backend: BCDWorkshopBackend? = nil, showing first: BCDWorkshopPage? = nil, card: Bool = false,
         close: @escaping () -> Void) {
        self.route = route
        self.close = close
        self.backend = backend ?? BCDWorkshopSource.backend
        preview = (first, card)
        switch route {
        case .area: _page = State(initialValue: first ?? .area)
        case .game(let id): _page = State(initialValue: first ?? .game(id))
        }
    }

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }

    var body: some View {
        let _ = tick   // the backend's changes (a download's progress, a subscription) read the page again
        ZStack {
            VStack(spacing: 0) {
                header
                ScrollViewReader { scroller in
                    ScrollView(.vertical, showsIndicators: false) {
                        VStack(alignment: .leading, spacing: 8) {
                            content
                        }
                        .frame(maxWidth: 680, alignment: .leading)
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal, layout.gutter).padding(.top, 4).padding(.bottom, 60)
                    }
                    .scrollDismissesKeyboard(.interactively)
                    .onChange(of: focus.focused) { _, id in
                        guard let id, id.hasPrefix(Self.layer), !id.hasPrefix(Self.cardLayer), !id.hasPrefix(Self.askLayer) else { return }
                        withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
                    }
                }
                if focus.controllerActive && !focus.hints.isEmpty {
                    HStack(spacing: 22) { Spacer(); ForEach(focus.hints) { BCDHintView(hint: $0) } }
                        .frame(height: 40).padding(.horizontal, layout.gutter)
                        .allowsHitTesting(false)
                }
            }
            if let open = card, asking == nil {
                cardView(open)
            }
            if let ask = asking {
                BCDGameQuestionView(question: ask.question, prefix: Self.askLayer) { yes in
                    answer(ask, yes)
                }
                .transition(.opacity)
            }
        }
        .background { BCDBackdrop().ignoresSafeArea() }
        .coordinateSpace(name: BCDFocus.space)
        .environment(\.bcdLayout, layout)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
        .onAppear {
            focus.openLayer(Self.layer)
            backend?.onChange = { tick += 1 }
            opened(page)
            installButtons()
            if preview.card {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                    if case .game(let id) = page, let first = backend?.page(id).subscribed?.first { card = BCDWorkshopCardRoute(item: first, appID: id) }
                }
            }
        }
        .onDisappear {
            backend?.onChange = nil
            focus.closeLayer(Self.layer)
        }
        .onChange(of: page) { _, now in
            opened(now)
            installButtons()
        }
        .onChange(of: card) { old, new in
            if old == nil, new != nil {
                focus.openLayer(Self.cardLayer)
                focus.focused = Self.cardLayer + "first"
            } else if old != nil, new == nil {
                focus.closeLayer(Self.cardLayer)
            }
            installButtons()
        }
        .onChange(of: asking) { old, new in
            if old == nil, let new {
                focus.openLayer(Self.askLayer)
                focus.focused = new.question.destructive ? Self.askLayer + "no" : Self.askLayer + "yes"
            } else if old != nil, new == nil {
                focus.closeLayer(Self.askLayer)
            }
            installButtons()
        }
        .animation(.easeOut(duration: 0.2), value: asking)
    }

    // MARK: Header

    private var title: String {
        switch page {
        case .area: return "Steam Workshop"
        case .game(let id), .browse(let id): return backend?.games().first { $0.id == id }?.title ?? store.games.first { $0.id == "steam.\(id)" }?.title ?? "Steam Workshop"
        }
    }

    private var subtitle: String {
        switch page {
        case .area: return "Items for your installed Steam games"
        case .game: return "Steam Workshop"
        case .browse: return "Browse the Workshop"
        }
    }

    private var canGoBack: Bool {
        switch page {
        case .area: return false
        case .game: return route == .area
        case .browse: return true
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            if canGoBack {
                Button(action: back) {
                    Image(systemName: "chevron.left").font(.system(size: 16, weight: .bold))
                        .frame(width: 40, height: 40).background(BCDStyle.surfaceRaised, in: Circle())
                }
                .buttonStyle(.plain)
                .bcdFocusable("workshop.back", cornerRadius: 20, action: back)
                .accessibilityLabel("Back")
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(BCDStyle.heading(22)).lineLimit(1)
                Text(subtitle).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
            }
            Spacer(minLength: 8)
            Button(action: close) {
                Image(systemName: "xmark").font(.system(size: 14, weight: .bold))
                    .frame(width: 40, height: 40).background(BCDStyle.surfaceRaised, in: Circle())
            }
            .buttonStyle(.plain)
            .bcdFocusable("workshop.close", cornerRadius: 20, action: close)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, layout.gutter).padding(.top, 12).padding(.bottom, 10)
        .frame(maxWidth: 680 + 2 * layout.gutter)
        .frame(maxWidth: .infinity)
    }

    // MARK: Pages

    @ViewBuilder private var content: some View {
        switch page {
        case .area: area
        case .game(let id): gamePage(id)
        case .browse(let id): browse(id)
        }
    }

    // The area: the installed games.

    @ViewBuilder private var area: some View {
        Text(BCDWorkshop.areaNote)
            .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
            .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.bottom, 4)
        let all = backend?.games() ?? []
        if all.isEmpty {
            Text("No Steam games are installed yet.").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        }
        let withWorkshop = all.filter { $0.hasWorkshop || $0.installedItems > 0 }
        if !withWorkshop.isEmpty {
            BCDEyebrow(text: "Games with a Workshop").padding(.top, 6).padding(.leading, 4)
            ForEach(withWorkshop) { gameRow($0) }
        }
        let others = all.filter { !($0.hasWorkshop || $0.installedItems > 0) }
        if !others.isEmpty {
            BCDEyebrow(text: "Other installed games").padding(.top, 10).padding(.leading, 4)
            Text("Steam hasn't said yet whether these games have a Workshop. Pull down on the Library to refresh it.")
                .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            ForEach(others) { gameRow($0) }
        }
    }

    private func gameRow(_ game: BCDWorkshopGame) -> some View {
        let id = "workshop.game.\(game.id)"
        let art = store.games.first { $0.id == "steam.\(game.id)" || $0.id == String(game.id) }?.coverArt ?? []   // the app's ids are steam.<App ID>; the preview app's are bare
        let open = { page = .game(game.id) }
        return Button(action: open) {
            HStack(spacing: 12) {
                BCDArtImage(urls: art, shape: 2.0 / 3.0) { BCDStyle.surfaceRaised }
                    .frame(width: 40, height: 60)
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                VStack(alignment: .leading, spacing: 3) {
                    Text(game.title).font(BCDStyle.label(15)).lineLimit(2)
                    Text(BCDWorkshop.installedCount(game.installedItems)).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                    if game.pending > 0 {
                        Text(game.pending == 1 ? "1 item to download" : "\(game.pending) items to download")
                            .font(BCDStyle.body(13)).foregroundStyle(BCDGameColors.amber)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.textSoft.opacity(0.7))
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 14, action: open)
        .id(id)
    }

    // A game's page.

    @ViewBuilder private func gamePage(_ appID: Int) -> some View {
        let state = backend?.page(appID) ?? BCDWorkshopPageState()
        if !state.installed {
            Text("Install this game to download its Workshop items.").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        } else if let why = BCDWorkshop.notReady(state) {
            Label(why, systemImage: "exclamationmark.triangle.fill")
                .font(BCDStyle.body(14)).foregroundStyle(BCDGameColors.amber)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
        }
        let ready = state.installed && state.ready
        actionRow(id: "workshop.browse", symbol: "magnifyingglass", title: "Browse the Workshop", chevron: true, enabled: ready) {
            beginBrowse(appID)
        }
        if state.pending > 0 {
            actionRow(id: "workshop.all", symbol: "arrow.down.circle", title: BCDWorkshop.downloadAllTitle(state.pending), chevron: false, enabled: ready) {
                backend?.downloadAll(appID)
            }
        }
        if let link = URL(string: "https://steamcommunity.com/app/\(appID)/workshop/") {
            actionRow(id: "workshop.safari", symbol: "safari", title: "Open the Workshop in Safari", chevron: false, enabled: true) { openURL(link) }
        }
        Text(BCDWorkshop.accountNote)
            .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
            .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4).padding(.vertical, 4)
        if let problem = state.problem {
            problemLine(problem)
        }
        let subscribedTitle = state.subscribed.map { "Subscribed (\($0.count))" } ?? "Subscribed"
        BCDEyebrow(text: subscribedTitle).padding(.top, 8).padding(.leading, 4)
        if let subscribed = state.subscribed {
            if subscribed.isEmpty {
                Text("No subscribed items. Browse the Workshop to add some.").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
            }
            ForEach(subscribed) { itemRow($0, appID: appID) }
        } else if state.loading {
            Text("Reading your subscriptions…").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        } else if state.ready {
            Text("Your subscriptions weren't read yet.").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        }
        if !state.others.isEmpty {
            BCDEyebrow(text: "Installed, not subscribed").padding(.top, 10).padding(.leading, 4)
            Text("Items still on this device that your account no longer subscribes to, for example ones a game downloaded for a server.")
                .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            ForEach(state.others) { itemRow($0, appID: appID) }
        }
    }

    private func problemLine(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Label(text, systemImage: "exclamationmark.triangle.fill")
                .font(BCDStyle.body(14)).foregroundStyle(BCDGameColors.amber)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 4)
            Button("OK") { backend?.clearProblem() }
                .font(BCDStyle.label(14)).buttonStyle(.plain).foregroundStyle(BCDStyle.lavender)
                .bcdFocusable("workshop.problem", cornerRadius: 10) { backend?.clearProblem() }
        }
        .padding(.horizontal, 4)
    }

    private func actionRow(id: String, symbol: String, title: String, chevron: Bool, enabled: Bool, run: @escaping () -> Void) -> some View {
        Button(action: run) {
            HStack(spacing: 12) {
                Image(systemName: symbol).font(.system(size: 16, weight: .semibold)).foregroundStyle(BCDStyle.lavender).frame(width: 26)
                Text(title).font(BCDStyle.label(15)).lineLimit(2)
                Spacer(minLength: 8)
                if chevron {
                    Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.textSoft.opacity(0.7))
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.55)
        .bcdFocusable(id, cornerRadius: 14) { if enabled { run() } }
        .id(id)
    }

    // One item.

    private func itemRow(_ item: BCDWorkshopListing, appID: Int) -> some View {
        let id = "workshop.item.\(item.id)"
        let status = backend?.status(item.id, appID: appID) ?? BCDWorkshopStatus()
        let open = { card = BCDWorkshopCardRoute(item: item, appID: appID) }
        return Button(action: open) {
            HStack(alignment: .top, spacing: 12) {
                preview(item)
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title).font(BCDStyle.label(15)).lineLimit(2).multilineTextAlignment(.leading)
                    statusView(item, status)
                    if !item.summary.isEmpty {
                        Text(item.summary).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).lineLimit(2).multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 6)
                if status.subscribed {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(BCDStyle.mint).accessibilityLabel("Subscribed")
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(id, cornerRadius: 14, action: open)
        .id(id)
    }

    private func preview(_ item: BCDWorkshopListing) -> some View {
        BCDArtImage(urls: item.preview.map { [$0] } ?? [], shape: 1) {
            ZStack {
                BCDStyle.surfaceRaised
                Image(systemName: "square.stack.3d.up").foregroundStyle(BCDStyle.textSoft)
            }
        }
        .frame(width: 56, height: 56)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityHidden(true)
    }

    @ViewBuilder private func statusView(_ item: BCDWorkshopListing, _ status: BCDWorkshopStatus) -> some View {
        let caption = BCDWorkshop.caption(item, status)
        if case .downloading(let fraction, _) = status.state {
            VStack(alignment: .leading, spacing: 3) {
                BCDProgressBar(fraction: fraction).frame(height: 5)
                Text(caption.text).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
            }
        } else {
            Text(caption.text).font(BCDStyle.body(12))
                .foregroundStyle(caption.warning ? BCDGameColors.amber : BCDStyle.textSoft)
                .multilineTextAlignment(.leading)
        }
    }

    // MARK: The item's card

    private func cardView(_ open: BCDWorkshopCardRoute) -> some View {
        let status = backend?.status(open.item.id, appID: open.appID) ?? BCDWorkshopStatus()
        let state = backend?.page(open.appID) ?? BCDWorkshopPageState()
        let actions = BCDWorkshop.actions(open.item, status, ready: state.ready, gameInstalled: state.installed)
        // A card taller than the screen (a phone held sideways: its top edge went off the screen and the description down to
        // nothing) scrolls inside its frame, and the controller's focus scrolls it to the button; where it fits it is the card as before.
        return ZStack {
            Color.black.opacity(0.55).ignoresSafeArea().onTapGesture { card = nil }
            ViewThatFits(in: .vertical) {
                cardContent(open, status, actions)
                    .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
                ScrollViewReader { scroller in
                    ScrollView(.vertical, showsIndicators: false) {
                        cardContent(open, status, actions)
                    }
                    .onChange(of: focus.focused) { _, id in
                        guard let id, id.hasPrefix(Self.cardLayer) else { return }
                        withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
                    }
                }
                .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
                .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            }
            .frame(maxWidth: 460)
            .padding(24)
        }
        .transition(.opacity)
    }

    private func cardContent(_ open: BCDWorkshopCardRoute, _ status: BCDWorkshopStatus, _ actions: [BCDWorkshop.Action]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                preview(open.item)
                VStack(alignment: .leading, spacing: 4) {
                    Text(open.item.title).font(BCDStyle.heading(18)).fixedSize(horizontal: false, vertical: true)
                    statusView(open.item, status)
                }
            }
            ScrollView(.vertical, showsIndicators: false) {
                Text(open.item.summary.isEmpty ? "Steam gives no description for this item." : open.item.summary)
                    .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 120)
            VStack(spacing: 8) {
                ForEach(Array(actions.enumerated()), id: \.element) { index, action in
                    cardButton(action, status, open, first: index == 0)
                }
                if let link = open.item.link {
                    Button { openURL(link) } label: { Label("View on Steam", systemImage: "safari") }
                        .buttonStyle(BCDOutlineButtonStyle())
                        .bcdFocusable(actions.isEmpty ? Self.cardLayer + "first" : Self.cardLayer + "steam", cornerRadius: 25) { openURL(link) }
                        .id(actions.isEmpty ? Self.cardLayer + "first" : Self.cardLayer + "steam")
                }
                Button("Close") { card = nil }
                    .buttonStyle(BCDOutlineButtonStyle())
                    .bcdFocusable(actions.isEmpty && open.item.link == nil ? Self.cardLayer + "first" : Self.cardLayer + "close", cornerRadius: 25) { card = nil }
                    .id(actions.isEmpty && open.item.link == nil ? Self.cardLayer + "first" : Self.cardLayer + "close")
            }
        }
        .padding(20)
        .frame(maxWidth: 460, alignment: .leading)
    }

    private func cardButton(_ action: BCDWorkshop.Action, _ status: BCDWorkshopStatus, _ open: BCDWorkshopCardRoute, first: Bool) -> some View {
        let run = { perform(action, open.item, appID: open.appID) }
        let id = first ? Self.cardLayer + "first" : Self.cardLayer + String(describing: action)
        return Button(action: run) {
            Label(BCDWorkshop.title(action, status), systemImage: BCDWorkshop.symbol(action))
        }
        .buttonStyle(BCDOutlineButtonStyle())
        .bcdFocusable(id, cornerRadius: 25, action: run)
        .id(id)
    }

    // MARK: Actions

    /// An action on an item: a question first when it changes the Steam account or deletes files, else at once.
    private func perform(_ action: BCDWorkshop.Action, _ item: BCDWorkshopListing, appID: Int) {
        if let question = BCDWorkshop.question(action, item) {
            asking = BCDWorkshopAsk(action: action, item: item, appID: appID, question: question)
            return
        }
        run(action, item, appID: appID)
        card = nil
    }

    private func answer(_ ask: BCDWorkshopAsk, _ yes: Bool) {
        asking = nil
        guard yes else { return }
        run(ask.action, ask.item, appID: ask.appID)
        card = nil
    }

    private func run(_ action: BCDWorkshop.Action, _ item: BCDWorkshopListing, appID: Int) {
        switch action {
        case .download, .update: backend?.download(item, appID: appID)
        case .stop: backend?.cancel(item.id)
        case .subscribe: backend?.subscribe(item, appID: appID)
        case .unsubscribeAndRemove: backend?.unsubscribe(item, appID: appID, removeFiles: true)
        case .unsubscribeKeep: backend?.unsubscribe(item, appID: appID, removeFiles: false)
        case .remove: backend?.remove(item, appID: appID)
        }
    }

    // MARK: Browse

    @ViewBuilder private func browse(_ appID: Int) -> some View {
        orderChips
        HStack(spacing: 10) {
            TextField("Search the Workshop", text: $query)
                .font(BCDStyle.body(16))
                .submitLabel(.search)
                .onSubmit { search(appID) }
                .autocorrectionDisabled()
                .padding(.horizontal, 14).frame(height: 46)
                .background(BCDStyle.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            Button { search(appID) } label: { Label("Search", systemImage: "magnifyingglass").font(BCDStyle.label(14)).padding(.horizontal, 14).frame(height: 46) }
                .buttonStyle(.plain)
                .background(BCDStyle.surface, in: Capsule())
                .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                .bcdFocusable("workshop.search", cornerRadius: 23) { search(appID) }
        }
        .padding(.bottom, 2)
        if let failure {
            Label(failure, systemImage: "exclamationmark.triangle.fill")
                .font(BCDStyle.body(14)).foregroundStyle(BCDGameColors.amber)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
        }
        ForEach(results) { itemRow($0, appID: appID) }
        if loading {
            Text("Looking…").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        } else if results.count < total, failure == nil {
            actionRow(id: "workshop.more", symbol: "ellipsis.circle", title: "Load more", chevron: false, enabled: true) { loadMore(appID) }
        } else if results.isEmpty, failure == nil, browsePage > 0 {
            Text(searched.isEmpty ? "This game's Workshop has no items to show." : "Nothing found.")
                .font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        }
        if total > 0 {
            Text("\(results.count) of \(total)").font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
        }
    }

    private var orderChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(BCDWorkshopOrder.allCases) { item in
                    let chosen = searched.isEmpty && order == item
                    Button { choose(item) } label: {
                        Text(item.title)
                            .font(BCDStyle.label(14))
                            .padding(.horizontal, 14).padding(.vertical, 8)
                            .foregroundStyle(chosen ? BCDStyle.text : BCDStyle.textSoft)
                            .background(chosen ? BCDStyle.surfaceRaised : Color.clear, in: Capsule())
                            .overlay(Capsule().strokeBorder(chosen ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke), lineWidth: chosen ? 2 : 1))
                    }
                    .buttonStyle(.plain)
                    .bcdFocusable("workshop.order.\(item.rawValue)", cornerRadius: 18) { choose(item) }
                    .id("workshop.order.\(item.rawValue)")
                }
            }
            .padding(.vertical, 6)
        }
    }

    private func beginBrowse(_ appID: Int) {
        order = .popular
        query = ""
        searched = ""
        results = []
        total = 0
        browsePage = 0
        failure = nil
        page = .browse(appID)
    }

    private func choose(_ next: BCDWorkshopOrder) {
        guard case .browse(let appID) = page else { return }
        order = next
        query = ""
        searched = ""
        restart(appID)
    }

    private func search(_ appID: Int) {
        let text = query.trimmingCharacters(in: .whitespacesAndNewlines)
        searched = text
        restart(appID)
    }

    private func restart(_ appID: Int) {
        results = []
        total = 0
        browsePage = 0
        failure = nil
        browseToken += 1
        fetch(appID, token: browseToken)
    }

    private func loadMore(_ appID: Int) {
        browseToken += 1
        fetch(appID, token: browseToken)
    }

    /// One page of Browse (or of the search). A page that was asked for before a newer Browse started is dropped.
    private func fetch(_ appID: Int, token: Int) {
        guard let backend else { return }
        loading = true
        failure = nil
        let next = browsePage + 1
        let text = searched
        let sort = order
        Task {
            do {
                let got = try await backend.browse(appID, order: sort, text: text, page: next)
                guard token == browseToken else { return }
                let known = Set(results.map(\.id))
                results += got.items.filter { !known.contains($0.id) }
                total = got.items.isEmpty ? results.count : got.total
                browsePage = next
            } catch {
                guard token == browseToken else { return }
                failure = (error as? BCDGameError)?.message ?? "Steam didn't answer. Try again in a moment."
                browsePage = max(browsePage, 1)
            }
            if token == browseToken { loading = false }
        }
    }

    // MARK: Moving about

    private func opened(_ now: BCDWorkshopPage) {
        switch now {
        case .area:
            focus.focused = backend?.games().first.map { "workshop.game.\($0.id)" } ?? "workshop.close"
        case .game(let id):
            backend?.opened(id)
            focus.focused = "workshop.browse"
        case .browse(let id):
            backend?.opened(id)
            restart(id)
            focus.focused = "workshop.search"
        }
    }

    private func back() {
        switch page {
        case .area: close()
        case .game: if route == .area { page = .area } else { close() }
        case .browse(let id): page = .game(id)
        }
    }

    private func installButtons() {
        if asking != nil {
            focus.setHandlers([(BCDHint(button: .a, title: "Select"), {}), (BCDHint(button: .b, title: "Cancel"), { answer(asking!, false) })])
            return
        }
        if card != nil {
            focus.setHandlers([(BCDHint(button: .a, title: "Select"), {}), (BCDHint(button: .b, title: "Close"), { card = nil })])
            return
        }
        focus.setHandlers([(BCDHint(button: .a, title: "Select"), {}),
                           (BCDHint(button: .b, title: canGoBack ? "Back" : "Close"), { back() })])
    }
}

extension View {
    /// Opens the Steam Workshop full screen over the shell: the Library's area, or a game's page.
    func bcdWorkshop(_ route: Binding<BCDWorkshopRoute?>) -> some View {
        modifier(BCDWorkshopPresenter(route: route))
    }
}

private struct BCDWorkshopPresenter: ViewModifier {
    @EnvironmentObject private var store: BCDStore
    @EnvironmentObject private var focus: BCDFocus
    @Binding var route: BCDWorkshopRoute?

    func body(content: Content) -> some View {
        content.fullScreenCover(item: $route, onDismiss: BCDPresentation.didClose) { opened in
            BCDWorkshopCover(route: opened) { route = nil }
                .environmentObject(store)
                .environmentObject(focus)
        }
    }
}
