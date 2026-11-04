// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// BCD's menus (docs/BCD-UI-ARCHITECTURE.md): a short list of choices over the shell, shown
// the same way for touch and for a controller. A game's menu opens on a long press of its card
// (or of Home's Continue playing banner) and on X when a controller's ring is on it; the
// Library's sort menu opens from its chip. A choice that deletes something asks first, as the
// game page does (BCDGameQuestionView). Screens never build the overlay: the shell owns one
// BCDMenuModel, draws it (BCDMenuHost) and gives it to the screens in the environment.
//
// Memory: nothing here lives in a card. A card holds one Bool; the menu's content exists only
// while it is open.

// MARK: - What a menu holds

struct BCDMenuItem: Identifiable {
    var id: String
    var title: String
    var symbol: String
    /// A line under the title ("Frees 9.1 GB").
    var detail: String? = nil
    var destructive = false
    /// The choice in force (the sort menu).
    var checked = false
    /// Asked before `run`, the way the game page asks (its words, its Cancel first).
    var question: BCDGameQuestion? = nil
    var run: () -> Void
}

struct BCDMenuContent: Identifiable {
    let id = UUID()
    var title: String
    /// A game's menu shows its state under the title.
    var game: BCDGame? = nil
    var items: [BCDMenuItem]
    var message: String? = nil
    var cancel: (() -> Void)? = nil
}

struct BCDMenuAsk {
    var question: BCDGameQuestion
    var yes: () -> Void
}

// MARK: - The model

/// One per shell. The screens read it from the environment only to call it, never to observe
/// it (a plain reference), so opening a menu redraws the overlay and nothing else.
@MainActor final class BCDMenuModel: ObservableObject {
    @Published private(set) var content: BCDMenuContent?
    @Published private(set) var ask: BCDMenuAsk?
    /// The shell's store: a game's menu is made from the game as it is when the menu opens.
    weak var store: BCDStore?
    /// Set by the screen that shows the cards (Home, Library): opens a game's page over it.
    var openPage: ((BCDGame) -> Void)?
    /// What the choice does, run by the host once the menu has closed.
    private var afterClose: (() -> Void)?

    var isOpen: Bool { content != nil || ask != nil }

    func show(_ content: BCDMenuContent) {
        guard !isOpen else { return }
        self.content = content
    }

    func confirm(_ question: BCDGameQuestion, run: @escaping () -> Void) {
        guard !isOpen else { return }
        ask = BCDMenuAsk(question: question, yes: run)
    }

    func pick(_ item: BCDMenuItem) {
        guard content != nil else { return }
        content = nil
        if let question = item.question {
            ask = BCDMenuAsk(question: question, yes: item.run)
        } else {
            afterClose = item.run
        }
    }

    func answer(_ yes: Bool) {
        guard let asked = ask else { return }
        ask = nil
        if yes { afterClose = asked.yes }
    }

    func dismiss() {
        afterClose = content?.cancel
        content = nil
        ask = nil
    }

    /// The host calls this once the menu is gone and the controller is back under it.
    func closed() -> (() -> Void)? {
        defer { afterClose = nil }
        return afterClose
    }

    // MARK: A game's menu

    /// The focus ids of a game's card, and the game each names (the shell's X reads the ring).
    static let cardPrefixes = ["library.game.", "home.recent.", "home.continue."]
    static let continueID = "home.continue"

    static func gameID(focused id: String?, store: BCDStore) -> String? {
        guard let id else { return nil }
        if id == continueID { return store.continuePlaying?.id }
        for prefix in cardPrefixes where id.hasPrefix(prefix) { return String(id.dropFirst(prefix.count)) }
        return nil
    }

    /// Play (Install, Pause, Resume or Try again, whichever the game's page offers first), Update
    /// when there is one, the game's page, and Uninstall or Remove when the game has it: the same
    /// choices and the same store actions as the page.
    func showCard(_ gameID: String) {
        guard !isOpen, let store, let game = store.games.first(where: { $0.id == gameID }) else { return }
        let details = BCDGameDetailSource.backend?.details(for: game, settings: false) ?? BCDGameDetails()
        let plan = BCDGamePagePlan(game: game, download: store.downloads.first { $0.id == game.id }, system: store.system,
                                   accounts: store.accounts, otherDownloads: false, details: details)
        var items: [BCDMenuItem] = []
        func add(_ action: BCDGameAction, _ title: String, _ symbol: String, detail: String? = nil, destructive: Bool = false) {
            items.append(BCDMenuItem(id: Self.key(action), title: title, symbol: symbol, detail: detail, destructive: destructive,
                                     question: action.question(for: game, details: details),
                                     run: { [weak store] in Self.perform(action, game, store?.actions) }))
        }
        if let main = plan.main { add(main.action, main.title, main.symbol) }
        // Install on a game with owned DLC: the question about it lives on the game's page, so the card opens the page for such a
        // game (and for one whose DLC lookup still runs) instead of installing it alone. Showing the card starts the lookup.
        if let main = plan.main, case .install = main.action, case .notInstalled = game.state, !items.isEmpty {
            BCDGameDetailSource.backend?.opened(game)
            items[0].run = { [weak self, weak store] in
                let now = BCDGameDetailSource.backend?.details(for: game, settings: false) ?? BCDGameDetails()
                if now.dlcOffer != nil || now.dlcCheck == .checking { self?.openPage?(game); return }
                Self.perform(main.action, game, store?.actions)
            }
        }
        if let update = plan.others.first(where: { $0.action == .update }) { add(.update, update.title, update.symbol) }
        items.append(BCDMenuItem(id: "page", title: "Game page", symbol: "info.circle",
                                 run: { [weak self] in self?.openPage?(game) }))
        switch details.removal {
        case .none: break
        case .uninstall(let frees):
            add(.uninstall, "Uninstall", "trash", detail: frees.map { "Frees \(BCDFormat.bytes($0))" }, destructive: true)
        case .removeFromLibrary:
            add(.removeFromLibrary, "Remove from library", "minus.circle", destructive: true)
        }
        show(BCDMenuContent(title: game.title, game: game, items: items))
    }

    private static func key(_ action: BCDGameAction) -> String {
        switch action {
        case .play: return "play"
        case .install: return "install"
        case .update: return "update"
        case .pause: return "pause"
        case .resume: return "resume"
        case .repair: return "repair"
        case .uninstall, .removeFromLibrary: return "remove"
        default: return "other"
        }
    }

    /// The store's actions, as the page's run calls them (Uninstall and Remove from library are
    /// the one action: a game added from Files leaves the library, its files stay).
    private static func perform(_ action: BCDGameAction, _ game: BCDGame, _ actions: BCDActions?) {
        switch action {
        case .play: actions?.play(game)
        case .install(let place): actions?.install(game, place: place)
        case .update: actions?.update(game)
        case .pause: actions?.pauseDownload(game.id)
        case .resume: actions?.resumeDownload(game.id)
        case .repair: actions?.verify(game)
        case .uninstall, .removeFromLibrary: actions?.uninstall(game)
        default: break
        }
    }
}

private struct BCDMenuKey: EnvironmentKey { static let defaultValue: BCDMenuModel? = nil }
extension EnvironmentValues {
    var bcdMenu: BCDMenuModel? {
        get { self[BCDMenuKey.self] }
        set { self[BCDMenuKey.self] = newValue }
    }
}

// MARK: - A game's button

/// A game's card: a tap runs `tap`, a long press opens the game's menu (and a VoiceOver user
/// finds the menu among the card's actions). The release that ends a long press is not a tap.
struct BCDGameButton<Label: View>: View {
    let gameID: String
    let tap: () -> Void
    @ViewBuilder let label: () -> Label
    @Environment(\.bcdMenu) private var menu
    @State private var held = false

    var body: some View {
        Button {
            if held { held = false } else { tap() }
        } label: { label() }
        .buttonStyle(BCDGameButtonStyle { held = false })
        .simultaneousGesture(LongPressGesture(minimumDuration: 0.45).onEnded { _ in
            guard let menu, !menu.isOpen else { return }
            held = true
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            menu.showCard(gameID)
        })
        .accessibilityAction(named: "Game menu") { menu?.showCard(gameID) }
    }
}

/// The plain button, fading a little while pressed; once the finger is up the long press that may
/// have opened the menu is over.
private struct BCDGameButtonStyle: ButtonStyle {
    let released: () -> Void
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.82 : 1)
            .onChange(of: configuration.isPressed) { _, down in
                if !down { DispatchQueue.main.async(execute: released) }
            }
    }
}

// MARK: - The shell draws it

/// The menu over the shell: a dimmed screen, the list in the middle, the buttons' hints along the
/// bottom. It holds the controller while it is up (the layer "menu."): the D-pad and A reach only
/// its rows, B closes it, and closing gives back the shell's buttons and ring.
struct BCDMenuHost: ViewModifier {
    @ObservedObject var model: BCDMenuModel
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.bcdLayout) private var layout
    static let layer = "menu."
    var prefix = BCDMenuHost.layer

    func body(content: Content) -> some View {
        content
            .overlay { if model.isOpen { menu.transition(.opacity) } }
            .animation(.easeOut(duration: 0.18), value: model.isOpen)
            .onChange(of: model.isOpen) { _, open in
                if open {
                    focus.openLayer(prefix)
                    placeFocus()
                } else {
                    focus.closeLayer(prefix)
                    // What the choice does comes after the controller is back under the menu.
                    model.closed()?()
                }
            }
            .onChange(of: model.content?.id) { _, _ in if model.isOpen { placeFocus() } }
            .onChange(of: model.ask != nil) { _, _ in if model.isOpen { placeFocus() } }
            .onDisappear {
                guard model.isOpen else { return }
                model.dismiss()
                focus.closeLayer(prefix)
            }
    }

    private var menu: some View {
        ZStack {
            if let content = model.content {
                Color.black.opacity(0.55).ignoresSafeArea().onTapGesture { model.dismiss() }
                BCDMenuCard(content: content, prefix: prefix) { model.pick($0) }
            } else if let asked = model.ask {
                BCDGameQuestionView(question: asked.question, prefix: prefix + "ask.") { model.answer($0) }
            }
        }
        .overlay(alignment: .bottom) {
            // An upright phone's shell shows no hint bar (its tabs are at the bottom): neither does the menu.
            if focus.controllerActive && !focus.hints.isEmpty && layout != .phonePortrait {
                HStack(spacing: 22) {
                    Spacer()
                    ForEach(focus.hints) { BCDHintView(hint: $0) }
                }
                .padding(.horizontal, layout.gutter).frame(height: 40)
                .allowsHitTesting(false)
            }
        }
    }

    /// The ring goes to the row in force (the sort menu), else the first, or to Cancel when the
    /// question deletes something; the buttons follow what is up.
    private func placeFocus() {
        if let asked = model.ask {
            focus.focused = (asked.question.destructive || asked.question.defaultCancel) ? prefix + "ask.no" : prefix + "ask.yes"
            focus.setHandlers([(BCDHint(button: .a, title: "Select"), {}),
                               (BCDHint(button: .b, title: "Cancel"), { model.answer(false) })])
        } else if let content = model.content {
            let first = content.items.first(where: \.checked) ?? content.items.first
            focus.focused = first.map { prefix + "item." + $0.id }
            focus.setHandlers([(BCDHint(button: .a, title: "Choose"), {}),
                               (BCDHint(button: .b, title: "Close"), { model.dismiss() })])
        }
    }
}

private struct BCDMenuCard: View {
    let content: BCDMenuContent
    var prefix = BCDMenuHost.layer
    let pick: (BCDMenuItem) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 5) {
                Text(content.title).font(BCDStyle.heading(19)).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                if let message = content.message { Text(message).font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true) }
                if let game = content.game { BCDGameStatusLine(game: game) }
            }
            .padding(.horizontal, 20).padding(.top, 18).padding(.bottom, 12)
            Rectangle().fill(BCDStyle.stroke).frame(height: 1)
            ViewThatFits(in: .vertical) {
                rows
                ScrollView(.vertical, showsIndicators: false) { rows }
            }
        }
        .frame(maxWidth: 360)
        .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 24, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
        .padding(24)
    }

    private var rows: some View {
        VStack(spacing: 2) { ForEach(content.items) { row($0) } }
            .padding(8)
    }

    private func row(_ item: BCDMenuItem) -> some View {
        Button { pick(item) } label: {
            HStack(spacing: 14) {
                Image(systemName: item.symbol).font(.system(size: 17, weight: .semibold)).frame(width: 24)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title).font(BCDStyle.label(16))
                    if let detail = item.detail {
                        Text(detail).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft)
                    }
                }
                Spacer(minLength: 8)
                if item.checked {
                    Image(systemName: "checkmark").font(.system(size: 14, weight: .bold)).foregroundStyle(BCDStyle.lavender)
                }
            }
            .foregroundStyle(item.destructive ? BCDStyle.alert : BCDStyle.text)
            .padding(.horizontal, 14).frame(minHeight: 50)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdFocusable(prefix + "item." + item.id, cornerRadius: 14) { pick(item) }
        .accessibilityAddTraits(item.checked ? .isSelected : [])
    }
}
