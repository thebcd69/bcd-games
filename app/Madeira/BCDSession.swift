// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import SwiftUI
import UIKit

// BCD's in-game layer (docs/BCD-UI-ARCHITECTURE.md): what a running game has over it. The
// starting screen until the game's first frame, the menu button, the performance HUD, the
// session line, the live log and the in-game menu. Everything here is drawn from plain values;
// BCDSessionLive.swift (engine-bound) fills them from the session and carries out what a person
// asks, through the same engine calls the classic layer used. The preview app fills them with
// samples (app/BCDPreview/BCDPreviewSession.swift).
//
// Cost (memory first): a part that is not shown does not exist. The menu is built when it opens
// and dropped when it closes; the starting screen, the overlay and the log each have their own
// small feed with a timer that runs only while that part is on screen. Nothing animates on its
// own and nothing is blurred over the running game.

// MARK: - Values the menu shows

/// The menu's pages along its top (LB / RB).
enum BCDSessionTab: String, CaseIterable, Identifiable {
    case game, display, controls, tools
    var id: String { rawValue }
    var title: String {
        switch self {
        case .game: return "Game"
        case .display: return "Display"
        case .controls: return "Controls"
        case .tools: return "Tools"
        }
    }
}

/// How a word, a value or a reading is coloured.
enum BCDSessionTone: Equatable {
    case normal, soft, good, warm, hot
    var color: Color {
        switch self {
        case .normal: return BCDStyle.text
        case .soft: return BCDStyle.textSoft
        case .good: return BCDStyle.mint
        case .warm: return BCDSessionStyle.amber
        case .hot: return BCDStyle.alert
        }
    }
}

enum BCDSessionStyle {
    static let amber = Color(red: 1.0, green: 0.76, blue: 0.29)      // #FFC24A
    static let panel = Color(red: 0.063, green: 0.047, blue: 0.122)   // #100C1F
    /// The game under the menu is dimmed, never blurred.
    static let dim = Color.black.opacity(0.55)
}

/// One choice of a row of chips.
struct BCDChoice: Identifiable, Equatable {
    var id: String
    var title: String
}

/// A question before something that cannot be undone or that replaces something.
struct BCDConfirm: Equatable {
    struct Answer: Identifiable, Equatable {
        var id: String
        var title: String
        var destructive = false
    }
    var title: String
    var message: String
    var answers: [Answer]
}

/// One row of the menu. `id` names it to the source; the view keeps no meaning of its own.
struct BCDMenuRow: Identifiable, Equatable {
    enum Kind: Equatable {
        /// A button. `primary` draws it as the panel's main button.
        case action(primary: Bool = false, destructive: Bool = false)
        case toggle(Bool)
        /// One of several, as chips.
        case choice([BCDChoice], selected: String)
        /// Any of several, as chips.
        case multi([BCDChoice], selected: Set<String>)
        /// A bar: the value, its range, the step a controller moves it by, and the value in words.
        case slider(Double, ClosedRange<Double>, step: Double, text: String)
        /// Opens the page named by `page`.
        case page(String, value: String?)
        /// One of a list (a page of choices); choosing it goes back.
        case pick(selected: Bool)
        /// Words only, in `tone`.
        case note(BCDSessionTone)
        /// The share sheet with these files.
        case share([URL])
        /// A part another screen draws (BCDSessionMenuModel.custom), such as the touch controls'.
        case custom(String)
    }
    var id: String
    var title: String
    var detail: String? = nil
    var symbol: String? = nil
    var kind: Kind
    var enabled = true
    /// Asked before the row does anything.
    var confirm: BCDConfirm? = nil
    /// A small picture (JPEG bytes) shown in place of the symbol: the Windows page's thumbnails.
    var picture: Data? = nil
}

struct BCDMenuSection: Identifiable, Equatable {
    var id: String
    var title: String? = nil
    var rows: [BCDMenuRow]
}

/// A page under the tabs: the controller binds, the on-screen layouts, a bind's keys, the log.
struct BCDMenuPage: Equatable {
    enum Kind: Equatable { case rows, log }
    var id: String
    var title: String
    var kind: Kind = .rows
    var sections: [BCDMenuSection] = []
}

struct BCDLogLine: Identifiable, Equatable {
    var id: Int
    var text: String
    var error = false
}

/// What the menu reads and asks for. BCDSessionLive.swift implements it on the engine.
@MainActor protocol BCDSessionMenuSource: AnyObject {
    var title: String { get }
    /// "Playing · 12 min", "Starting" and the like.
    var subtitle: String { get }
    var tabs: [BCDSessionTab] { get }
    func sections(_ tab: BCDSessionTab) -> [BCDMenuSection]
    func page(_ id: String) -> BCDMenuPage?
    /// The live log's newest lines (the log page).
    func logLines() -> [BCDLogLine]
    func act(_ row: String)
    func toggle(_ row: String, _ on: Bool)
    func choose(_ row: String, _ value: String)
    func slide(_ row: String, _ value: Double)
    /// The answer to a row's question (`BCDConfirm.Answer.id`).
    func answer(_ row: String, _ answer: String)
    /// A page opened or closed (nil): the log page reads the log only while it is shown.
    func pageShown(_ id: String?)
    /// Resume: the menu closes.
    func close()
}

/// A controller's buttons while the menu is open.
enum BCDSessionCommand: Equatable {
    case up, down, left, right, accept, back, previousTab, nextTab, menu
}

/// The open menu. It exists only while the menu is open.
@MainActor final class BCDSessionMenuModel: ObservableObject {
    let source: BCDSessionMenuSource
    var initialTab: BCDSessionTab
    var initialPage: String?
    /// A row whose question shows at once (the preview app's photographs).
    var initialQuestion: String?
    /// The parts other screens draw into the menu, by the id of their `.custom` row: the touch
    /// controls' section (BCDTouchMenuSection) in the app and the preview app.
    var custom: [String: () -> AnyView] = [:]
    /// Something the source shows changed on its own (an upload's progress, the log).
    @Published private(set) var revision = 0
    let commands = PassthroughSubject<BCDSessionCommand, Never>()

    init(source: BCDSessionMenuSource, tab: BCDSessionTab = .game, page: String? = nil) {
        self.source = source
        self.initialTab = tab
        self.initialPage = page
    }

    func changed() { revision &+= 1 }
}

// MARK: - The layer over a running game

/// What the layer shows. Each part is nil while it is not on screen.
@MainActor final class BCDSessionHUDModel: ObservableObject {
    @Published var starting: BCDStartingFeed?
    @Published var menu: BCDSessionMenuModel?
    /// The performance HUD (BCDHUDView.swift); nil while the player has it off.
    @Published var performance: BCDHUDFeed?
    @Published var log: BCDLogFeed?
    /// The session line ("Starting…", "The game was asked to close…").
    @Published var message = ""
    @Published var showsMenuButton = true
}

/// The newest log lines for the strip over the game; the live side refreshes it while shown.
@MainActor final class BCDLogFeed: ObservableObject {
    @Published var lines: [BCDLogLine]
    init(lines: [BCDLogLine] = []) { self.lines = lines }
}

/// What the layer asks for.
@MainActor protocol BCDSessionHUDActions: AnyObject {
    func openMenu()
    func closeMenu()
    /// Where the menu button and the overlay are, in the window, for the touches they take.
    func menuButtonFrame(_ frame: CGRect)
    func overlayFrame(_ frame: CGRect)
    func starting(_ action: BCDStartingAction)
}

/// The whole layer, in the window above the game. It ignores the safe area and keeps its parts
/// inside it itself, so the frames it reports are the window's.
struct BCDSessionLayer: View {
    @ObservedObject var hud: BCDSessionHUDModel
    let actions: BCDSessionHUDActions

    var body: some View {
        GeometryReader { geo in
            let insets = BCDSessionLayer.insets(geo)
            ZStack(alignment: .topLeading) {
                if let starting = hud.starting {
                    BCDStartingScreen(feed: starting, insets: insets) { actions.starting($0) }
                        .transition(.opacity)
                }
                if hud.starting == nil, let log = hud.log {
                    BCDLogStrip(feed: log)
                        .frame(maxWidth: 560).frame(height: 150)
                        .padding(.top, insets.top + 64).padding(.leading, insets.leading + 14)
                        .allowsHitTesting(false)
                }
                if hud.starting == nil, let performance = hud.performance {
                    // Where nothing of the game is in the way (BCDHUDPlacement); it takes touches only while the player moves it.
                    BCDHUDHost(feed: performance, viewport: geo.size, insets: insets, report: { actions.overlayFrame($0) })
                }
                if !hud.message.isEmpty {
                    Text(hud.message)
                        .font(BCDStyle.label(13)).foregroundStyle(BCDStyle.text)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 16).padding(.vertical, 9)
                        .background(BCDSessionStyle.panel.opacity(0.92), in: Capsule())
                        .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                        .frame(maxWidth: min(520, geo.size.width - 40))
                        .frame(maxWidth: .infinity)
                        .padding(.top, insets.top + 12)
                        .allowsHitTesting(false)
                }

                if hud.starting == nil && hud.showsMenuButton {
                    // The top right corner, at the screen's own edge (owner, 2026-10-19: it sat on the
                    // touch controls' RT). See BCDDraggable's `edge`.
                    BCDDraggable(key: "bcdMenuButton", start: CGPoint(x: 1, y: 0), viewport: geo.size,
                                 insets: insets, edge: true, report: { actions.menuButtonFrame($0) }) { dragging in
                        BCDMenuButton(dragging: dragging) { actions.openMenu() }
                    }
                }
                if let menu = hud.menu {
                    BCDInGameMenu(model: menu, insets: insets)
                        .transition(.opacity)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height, alignment: .topLeading)
            .animation(.easeOut(duration: 0.18), value: hud.menu == nil)
            .animation(.easeOut(duration: 0.2), value: hud.starting == nil)
        }
        .ignoresSafeArea()
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
    }

    static func insets(_ geo: GeometryProxy) -> EdgeInsets { BCDSessionInsets.of(geo) }
}

/// The safe area as the window has it. SwiftUI hands zero insets to a view inside one that
/// ignores the safe area (the layer, the touch-controls window, the preview's stage); the
/// window always knows them, and the status bar's height when one shows.
enum BCDSessionInsets {
    static func of(_ geo: GeometryProxy) -> EdgeInsets {
        var insets = geo.safeAreaInsets
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        if let window = scene?.windows.first(where: \.isKeyWindow) ?? scene?.windows.first {
            let edges = window.safeAreaInsets
            insets.top = max(insets.top, edges.top)
            insets.bottom = max(insets.bottom, edges.bottom)
            insets.leading = max(insets.leading, edges.left)
            insets.trailing = max(insets.trailing, edges.right)
        }
        insets.top = max(insets.top, scene?.statusBarManager?.statusBarFrame.height ?? 0)
        return insets
    }
}

/// A part a finger can move, kept where it was left (a share of the screen, per device).
struct BCDDraggable<Content: View>: View {
    let viewport: CGSize
    let insets: EdgeInsets
    /// To the screen's own sides, not the safe area's: on an iPhone held sideways the safe area
    /// leaves a strip at each side (the corners, the Dynamic Island) that the touch controls never
    /// use, so a small button there covers nothing. It stays clear of the rounded corner (44 pt
    /// down where the sides have insets) and of the island in the middle only by where it starts.
    let edge: Bool
    let report: (CGRect) -> Void
    let content: (Bool) -> Content
    @AppStorage private var nx: Double
    @AppStorage private var ny: Double
    @GestureState private var drag = CGSize.zero
    @State private var size = CGSize(width: 44, height: 44)

    /// `key`: where the place is kept (`key`X, `key`Y); `start`: the first place, as shares.
    init(key: String, start: CGPoint, viewport: CGSize, insets: EdgeInsets, edge: Bool = false,
         report: @escaping (CGRect) -> Void, @ViewBuilder content: @escaping (Bool) -> Content) {
        self.viewport = viewport
        self.insets = insets
        self.edge = edge
        self.report = report
        self.content = content
        _nx = AppStorage(wrappedValue: Double(start.x), key + "X")
        _ny = AppStorage(wrappedValue: Double(start.y), key + "Y")
    }

    private func center(_ shift: CGSize) -> CGPoint {
        let sides = insets.leading > 0 || insets.trailing > 0
        let left = (edge ? 0 : insets.leading) + size.width / 2 + 8
        let top = (edge && sides ? max(insets.top + 8, 44) : insets.top + 8) + size.height / 2
        let right = max(left, viewport.width - (edge ? 0 : insets.trailing) - size.width / 2 - 8)
        let bottom = max(top, viewport.height - insets.bottom - size.height / 2 - 8)
        return CGPoint(x: min(max(left, viewport.width * nx + shift.width), right),
                       y: min(max(top, viewport.height * ny + shift.height), bottom))
    }

    var body: some View {
        let point = center(drag)
        let frame = CGRect(x: point.x - size.width / 2, y: point.y - size.height / 2, width: size.width, height: size.height)
        content(drag != .zero)
            .fixedSize()
            .onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }
            .contentShape(Rectangle())
            .highPriorityGesture(
                DragGesture(minimumDistance: 6, coordinateSpace: .global)
                    .updating($drag) { value, state, transaction in
                        transaction.animation = nil
                        state = value.translation
                    }
                    .onEnded { value in
                        let end = center(value.translation)
                        nx = end.x / max(1, viewport.width)
                        ny = end.y / max(1, viewport.height)
                    })
            .position(point)
            .onAppear { report(frame) }
            .onChange(of: frame) { _, value in report(value) }
            .onDisappear { report(.zero) }
    }
}

/// The round button that opens the menu. Quiet while the game runs; it never fades or moves
/// on its own.
struct BCDMenuButton: View {
    let dragging: Bool
    let open: () -> Void

    var body: some View {
        Button(action: open) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 18, weight: .bold))
                .foregroundStyle(BCDStyle.text)
                .frame(width: 44, height: 44)
                .background(BCDSessionStyle.panel.opacity(0.78), in: Circle())
                .overlay(Circle().strokeBorder(BCDStyle.glow.opacity(0.7), lineWidth: 1.5))
        }
        .buttonStyle(.plain)
        .opacity(dragging ? 1 : 0.6)
        .accessibilityLabel("Game menu")
        .accessibilityHint("Opens the game menu. Drag to move it.")
    }
}

/// The live log over the game: the newest lines, oldest at the top, never scrolled.
struct BCDLogStrip: View {
    @ObservedObject var feed: BCDLogFeed

    var body: some View {
        BCDLogLines(lines: feed.lines, size: 9)
            .padding(10)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            .background(Color.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .accessibilityLabel("Live log")
    }
}

/// Log lines in a small fixed type, the newest last; errors in the alert colour.
struct BCDLogLines: View {
    let lines: [BCDLogLine]
    var size: CGFloat = 10

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(lines) { line in
                Text(verbatim: line.text)
                    .font(.system(size: size, design: .monospaced))
                    .foregroundStyle(line.error ? BCDStyle.alert : BCDStyle.text.opacity(0.85))
                    .lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

// MARK: - The keyboard's key bar

/// A key on the bar over the keyboard.
struct BCDKeyboardKey: Identifiable, Equatable {
    var id: String
    var title: String
    /// Ctrl, Shift and Alt stay down until pressed again.
    var latches = false

    static let all: [BCDKeyboardKey] = [
        .init(id: "paste", title: "Paste"),
        .init(id: "esc", title: "Esc"), .init(id: "ctrl", title: "Ctrl", latches: true),
        .init(id: "shift", title: "Shift", latches: true), .init(id: "alt", title: "Alt", latches: true),
        .init(id: "tab", title: "Tab"), .init(id: "enter", title: "Enter"),
        .init(id: "left", title: "←"), .init(id: "up", title: "↑"), .init(id: "down", title: "↓"),
        .init(id: "right", title: "→"),
    ]

    /// madeira-bcd (2026-10-25, round 603): the second page (BCDExtraKeys.keyboardPage), F1 to F12, Win, Alt+Tab and the rest, with the
    /// latches again. A key of its own beside Done (BCDKeyboardBar) turns to it and back.
    static let more: [BCDKeyboardKey] = BCDExtraKeys.keyboardPage.map { .init(id: $0.id, title: $0.title, latches: $0.isModifier) }
}

/// What the key bar shows: the keys held down, and which page.
@MainActor final class BCDKeyboardBarModel: ObservableObject {
    @Published var latched: Set<String> = []
    @Published var secondPage = false
}

/// The bar over the iOS keyboard while a game runs: Paste, Esc, the modifiers, Tab, Enter, the arrows
/// and Done. A held modifier glows. Paste types the iPad's clipboard text into the program (HardwareInput.pasteClipboard).
struct BCDKeyboardBar: View {
    @ObservedObject var model: BCDKeyboardBarModel
    let press: (BCDKeyboardKey) -> Void
    let done: () -> Void
    /// madeira-bcd (2026-10-25, round 603): what the second page's keys that are not latches send (BCDExtraKeys.swift); the latches and
    /// the first page's keys go through `press`, as they always did.
    var extra: (BCDKeyboardKey) -> Void = { _ in }

    var body: some View {
        HStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(model.secondPage ? BCDKeyboardKey.more : BCDKeyboardKey.all) { key in
                        let held = model.latched.contains(key.id)
                        Button { if model.secondPage && !key.latches { extra(key) } else { press(key) } } label: {
                            Text(key.title)
                                .font(BCDStyle.label(15))
                                .foregroundStyle(held ? Color.white : BCDStyle.text)
                                .frame(minWidth: 46, minHeight: 38)
                                .padding(.horizontal, 6)
                                .background {
                                    if held { Capsule().fill(BCDStyle.glow) } else { Capsule().fill(BCDStyle.surfaceRaised) }
                                }
                                .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: held ? 0 : 1))
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel(key.title)
                        .accessibilityAddTraits(held ? .isSelected : [])
                    }
                }
                .padding(.horizontal, 10)
            }
            // madeira-bcd (2026-10-25, round 603): Fn turns to the second page (F1 to F12, Win, Alt+Tab ...) and back; sends no key.
            Button { model.secondPage.toggle() } label: {
                Text(model.secondPage ? "‹ Keys" : "Fn ›")
                    .font(BCDStyle.label(15)).foregroundStyle(BCDStyle.text)
                    .frame(minWidth: 46, minHeight: 38)
                    .padding(.horizontal, 6)
                    .background(Capsule().fill(model.secondPage ? AnyShapeStyle(BCDStyle.violet.opacity(0.55)) : AnyShapeStyle(BCDStyle.surfaceRaised)))
                    .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .accessibilityLabel(model.secondPage ? "Back to the first keys" : "More keys: F1 to F12, Win, Alt+Tab")
            Button(action: done) {
                Text("Done")
                    .font(BCDStyle.label(15)).foregroundStyle(BCDStyle.lavender)
                    .frame(minWidth: 64, minHeight: 38)
            }
            .buttonStyle(.plain)
            .padding(.trailing, 8)
        }
        .frame(maxWidth: .infinity, minHeight: 52, maxHeight: 52)
        .background(BCDSessionStyle.panel)
        .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke).frame(height: 1) }
        .preferredColorScheme(.dark)
    }
}
