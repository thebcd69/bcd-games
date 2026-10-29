// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

/// How much room the interface has (docs/BCD-UI-ARCHITECTURE.md): an iPhone on its side
/// (the main, console-like layout), an iPhone upright, or an iPad.
enum BCDLayout: Equatable {
    case phoneLandscape, phonePortrait, pad

    static func of(horizontal: UserInterfaceSizeClass?, vertical: UserInterfaceSizeClass?) -> BCDLayout {
        if horizontal == .regular && vertical == .regular { return .pad }
        return vertical == .compact ? .phoneLandscape : .phonePortrait
    }
    var gridColumns: Int {
        switch self {
        case .phonePortrait: return 2
        case .phoneLandscape: return 4
        case .pad: return 5
        }
    }
    var gutter: CGFloat { self == .pad ? 32 : 20 }
}

private struct BCDLayoutKey: EnvironmentKey { static let defaultValue = BCDLayout.phoneLandscape }
extension EnvironmentValues {
    var bcdLayout: BCDLayout {
        get { self[BCDLayoutKey.self] }
        set { self[BCDLayoutKey.self] = newValue }
    }
    /// The controller's hint bar is up under the screen (BCDFrontEnd.hintBar): a screen has 40 pt
    /// less height than the bare layout says. Home draws a tighter layout while it is; false otherwise.
    var bcdHintBar: Bool {
        get { self[BCDHintBarKey.self] }
        set { self[BCDHintBarKey.self] = newValue }
    }
}

private struct BCDHintBarKey: EnvironmentKey { static let defaultValue = false }

/// A game's picture in one of the stores' shapes, with the placeholder (the game's colours,
/// its initial and title) for a game no store has a picture of, such as one added from Files.
/// Pictures come through BCDArt: decoded at the size the frame shows (its points times the
/// screen's scale), held only while on screen, and dropped for a game.
struct BCDGameArt: View {
    enum Kind { case cover, header, hero }

    let game: BCDGame
    var kind: Kind = .cover
    var glow: BCDGlowRequest? = nil
    var showTitle = true

    var body: some View {
        BCDArtImage(urls: urls, shape: shape, glow: glow) {
            BCDArtPlaceholder(game: game, kind: kind, showTitle: showTitle)
        }
    }

    /// The shape asked for first, then the others (shown whole when the shape is wrong).
    private var urls: [URL] {
        switch kind {
        case .cover: return game.coverArt + game.headerArt
        case .header: return game.headerArt + game.heroArt + game.coverArt
        case .hero: return game.heroArt + game.headerArt + game.coverArt
        }
    }
    private var shape: CGFloat {
        switch kind {
        case .cover: return 2.0 / 3.0
        case .header: return 460.0 / 215.0
        case .hero: return 1920.0 / 620.0
        }
    }
}

/// A game's title logo from its store (transparent, fitted in `maxWidth` x `maxHeight`), else
/// its title in type: up to two lines, shrinking a little before it is cut.
struct BCDGameLogo: View {
    let game: BCDGame
    var titleSize: CGFloat = 30
    var maxWidth: CGFloat = 260
    var maxHeight: CGFloat = 70

    var body: some View {
        if game.logoArt.isEmpty {
            title
        } else {
            BCDLogoImage(urls: game.logoArt, alignment: .bottomLeading) { title }
                .frame(maxWidth: maxWidth, maxHeight: maxHeight, alignment: .bottomLeading)
        }
    }

    private var title: some View {
        Text(game.title)
            .font(BCDStyle.title(titleSize)).lineLimit(2).minimumScaleFactor(0.75)
            .multilineTextAlignment(.leading)
            .shadow(color: .black.opacity(0.35), radius: 6)
            .frame(maxWidth: maxWidth * 1.5, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
    }
}

/// A transparent picture fitted whole inside its frame, at `alignment`, decoded for that frame
/// and held only while on screen (as BCDArtImage).
private struct BCDLogoImage<Fallback: View>: View {
    let urls: [URL]
    let alignment: Alignment
    let fallback: () -> Fallback
    @Environment(\.displayScale) private var scale
    @State private var size: CGSize = .zero
    @State private var loaded: BCDArt.Loaded?
    @State private var failed = false

    init(urls: [URL], alignment: Alignment, @ViewBuilder fallback: @escaping () -> Fallback) {
        self.urls = urls
        self.alignment = alignment
        self.fallback = fallback
        _loaded = State(initialValue: BCDArt.shared.cached(urls, glow: false))
    }

    var body: some View {
        Color.clear
            .overlay(alignment: alignment) {
                if let image = loaded?.image {
                    Image(uiImage: image).resizable().scaledToFit()
                        .shadow(color: .black.opacity(0.4), radius: 8)
                } else if failed {
                    fallback()
                }
            }
            .onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }
            .onAppear { if loaded == nil { loaded = BCDArt.shared.cached(urls, glow: false) } }
            .onDisappear { loaded = nil }
            .task(id: BCDArtRequest(urls: urls, pixels: pixels)) {
                guard pixels.width >= 1, pixels.height >= 1 else { return }
                let result = await BCDArt.shared.load(urls, pixels: pixels, fit: .fit, glow: false)
                guard !Task.isCancelled else { return }
                loaded = result
                failed = result == nil
            }
    }

    private var pixels: CGSize {
        CGSize(width: size.width.rounded(.up) * scale, height: size.height.rounded(.up) * scale)
    }
}

/// Art for a game no store has a picture of: its own two colours, its initial large and
/// faint, and its title with room around it. Type and margins follow the card's size, so a
/// small tile gets small type instead of words broken in half.
struct BCDArtPlaceholder: View {
    let game: BCDGame
    var kind: BCDGameArt.Kind = .cover
    var showTitle = true

    var body: some View {
        GeometryReader { proxy in
            let width = proxy.size.width
            let margin = kind == .cover ? min(16, max(8, (width * 0.12).rounded())) : 22
            ZStack(alignment: .bottomLeading) {
                LinearGradient(colors: [game.tint.0, game.tint.1], startPoint: .topLeading, endPoint: .bottomTrailing)
                Text(initial)
                    .font(.system(size: max(24, min(width, proxy.size.height) * 0.75), weight: .black, design: .rounded))
                    .foregroundStyle(.white.opacity(0.08))
                    .lineLimit(1).minimumScaleFactor(0.3)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: kind == .cover ? .center : .trailing)
                    .padding(.trailing, kind == .cover ? 0 : 22)
                if showTitle {
                    Text(game.title)
                        .font(BCDStyle.heading(kind == .cover ? min(17, max(11, (width * 0.15).rounded())) : 24))
                        .foregroundStyle(.white)
                        .lineLimit(kind == .cover ? 4 : 2).minimumScaleFactor(0.8)
                        .shadow(color: .black.opacity(0.45), radius: 4)
                        .padding(margin)
                }
            }
            .frame(width: proxy.size.width, height: proxy.size.height)
        }
    }

    private var initial: String { game.title.first.map { String($0).uppercased() } ?? "?" }
}

/// Fades the top few points of a scrolling area instead of cutting its content (and the
/// covers' glow) in a hard line. The mask is drawn only while the content moves.
struct BCDSoftTopEdge: ViewModifier {
    var height: CGFloat = 14
    func body(content: Content) -> some View {
        content.mask {
            VStack(spacing: 0) {
                LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom).frame(height: height)
                Color.black
            }
            .ignoresSafeArea(edges: [.bottom, .horizontal])
        }
    }
}

/// A game in a grid or a row: its cover, darkened a little (more when it is not installed),
/// its glow in its own colours, and under it the title and state. The focused card lights up.
struct BCDCoverCard: View {
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.bcdLayout) private var layout
    let game: BCDGame
    let focusID: String
    var showText = true
    let open: () -> Void

    var body: some View {
        let lit = focus.controllerActive && focus.focused == focusID
        // A tap opens the game; a long press opens its menu (BCDMenu.swift), and X does when the ring is on the card.
        BCDGameButton(gameID: game.id, tap: open) {
            VStack(alignment: .leading, spacing: 7) {
                BCDGameArt(game: game, kind: .cover,
                           glow: BCDGlowRequest(id: focusID, strength: lit ? 1 : (game.state.isInstalled ? 0.85 : 0.5)))
                    .overlay(Color.black.opacity(lit ? 0 : (game.state.isInstalled ? 0.12 : 0.42)))
                    .overlay(alignment: .bottom) {
                        if let progress { BCDProgressBar(fraction: progress).padding(8) }
                    }
                    .aspectRatio(2.0 / 3.0, contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: radius, style: .continuous))
                    // The hairline fading down the card's edge, drawn by layers: a gradient-filled
                    // SwiftUI stroke made every card in the grid a card-sized bitmap (7 MB of the
                    // Library's 8 on an iPhone 13 Pro; tools/ui-memory.py).
                    .overlay(BCDHairline(radius: radius, top: lit ? 0.6 : 0.32, bottom: 0.04).allowsHitTesting(false))
                if showText {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(game.title).font(BCDStyle.label(layout == .pad ? 14 : 13)).lineLimit(1)
                        BCDGameStatusLine(game: game, compact: true)
                    }
                    .padding(.horizontal, 2)
                }
            }
        }
        .bcdFocusable(focusID, cornerRadius: radius, action: open)
        .accessibilityLabel(game.title)
    }

    private var radius: CGFloat { layout == .pad ? 14 : 12 }

    /// A download or install in progress shows as a thin bar across the cover.
    private var progress: Double? {
        switch game.state {
        case .downloading(let p), .installing(let p): return p
        default: return nil
        }
    }
}

/// A 1-point border fading from `top` to `bottom` white inside a rounded rectangle of `radius`
/// (continuous corners), as `strokeBorder` with a vertical LinearGradient draws it, but by Core
/// Animation layers: a gradient layer masked by a stroked shape, which the system draws without a
/// bitmap of the card's size in BCD's memory. Nothing in it animates.
struct BCDHairline: UIViewRepresentable {
    var radius: CGFloat
    var top: Double
    var bottom: Double

    func makeUIView(context: Context) -> Line { Line() }
    func updateUIView(_ view: Line, context: Context) { view.set(radius: radius, top: top, bottom: bottom) }

    final class Line: UIView {
        override class var layerClass: AnyClass { CAGradientLayer.self }
        private let shape = CAShapeLayer()
        private var radius: CGFloat = 0

        override init(frame: CGRect) {
            super.init(frame: frame)
            isUserInteractionEnabled = false
            isAccessibilityElement = false
            backgroundColor = .clear
            shape.fillColor = nil
            shape.strokeColor = UIColor.black.cgColor
            shape.lineWidth = 1
            layer.mask = shape
        }

        required init?(coder: NSCoder) { fatalError("BCDHairline is made in code") }

        func set(radius: CGFloat, top: Double, bottom: Double) {
            let gradient = layer as! CAGradientLayer   // the view's own layer: no implicit animation
            gradient.colors = [UIColor(white: 1, alpha: top).cgColor, UIColor(white: 1, alpha: bottom).cgColor]
            gradient.startPoint = CGPoint(x: 0.5, y: 0)
            gradient.endPoint = CGPoint(x: 0.5, y: 1)
            if self.radius != radius {
                self.radius = radius
                setNeedsLayout()
            }
        }

        override func layoutSubviews() {
            super.layoutSubviews()
            // The mask is a free-standing layer: no implicit animation when the card's size changes.
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            shape.frame = bounds
            shape.path = UIBezierPath(roundedRect: bounds.insetBy(dx: 0.5, dy: 0.5), cornerRadius: max(0, radius - 0.5)).cgPath
            CATransaction.commit()
        }
    }
}

/// The small coloured dot and words under a library card: Ready, Not installed, 34%...
struct BCDGameStatusLine: View {
    let game: BCDGame
    /// Under a narrow cover: the state in a word or two.
    var compact = false
    var body: some View {
        HStack(spacing: compact ? 5 : 6) {
            if let color = dot { Circle().fill(color).frame(width: compact ? 7 : 8, height: compact ? 7 : 8) }
            if compact, case .notInstalled = game.state { Image(systemName: "arrow.down.circle").font(.system(size: 11, weight: .semibold)) }
            Text(compact ? short : text).lineLimit(1).minimumScaleFactor(compact ? 0.8 : 1)
            if compact, game.state.isInstalled, game.place == .externalDrive {
                Image(systemName: "externaldrive.fill").font(.system(size: 10)).accessibilityLabel("on the external drive")
            }
        }
        .font(BCDStyle.body(compact ? 12 : 13))
        .foregroundStyle(dot == nil ? BCDStyle.textSoft : (compact ? BCDStyle.textSoft : BCDStyle.text))
    }
    private var short: String {
        switch game.state {
        case .ready: return "Ready"
        case .notInstalled: return game.size.map { BCDFormat.bytes($0) } ?? "Not installed"
        case .queued: return "Waiting"
        case .downloading: return "Downloading"
        case .installing: return "Installing"
        // A store that names no size before the download (Steam) reports 0.
        case .updateAvailable(let bytes): return bytes > 0 ? "Update \(BCDFormat.bytes(bytes))" : "Update"
        case .broken: return "Needs a look"
        }
    }
    private var dot: Color? {
        switch game.state {
        case .ready: return BCDStyle.mint
        case .downloading, .installing, .queued: return BCDStyle.lavender
        case .updateAvailable: return BCDStyle.amber
        case .broken: return BCDStyle.alert
        case .notInstalled: return nil
        }
    }
    private var text: String {
        switch game.state {
        case .ready:
            if let last = game.lastPlayed { return "Ready · played \(BCDFormat.ago(last))" }
            return game.place == .externalDrive ? "Ready · on the external drive" : "Ready"
        case .notInstalled:
            let store = game.store == .local ? "" : " · \(game.store.title)"
            return game.size.map { "Not installed\(store) · \(BCDFormat.bytes($0))" } ?? "Not installed\(store)"
        case .queued: return "Waiting to download"
        case .downloading(let p): return "Downloading · \(Int(p * 100))%"
        case .installing(let p): return "Installing · \(Int(p * 100))%"
        case .updateAvailable(let bytes): return bytes > 0 ? "Update · \(BCDFormat.bytes(bytes))" : "Update available"
        case .broken(let reason): return reason
        }
    }
}

/// A label in small capitals with wide spacing, over a card's contents.
struct BCDEyebrow: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 11, weight: .heavy, design: .rounded))
            .tracking(2.2)
            .foregroundStyle(BCDStyle.textSoft)
    }
}

/// A thin progress bar in the logo's light.
struct BCDProgressBar: View {
    let fraction: Double
    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(BCDStyle.stroke)
                Capsule().fill(BCDStyle.glow).frame(width: max(6, proxy.size.width * fraction))
            }
        }
        .frame(height: 6)
        .accessibilityValue(Text("\(Int(fraction * 100)) percent"))
    }
}

/// A small rounded chip for the status row: a dot and a word or two.
struct BCDStatusChip: View {
    let text: String
    var color: Color = BCDStyle.mint
    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).font(BCDStyle.label(12)).lineLimit(1)
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(BCDStyle.surface, in: Capsule())
        .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
    }
}

// MARK: - Send Log's comment

/// Before a log is sent by hand (Settings › Send Log, a game's page, auto-tune's Send Log), the sender may say in a few words what
/// the log cannot show: low FPS and where, a stutter, what was on screen (the owner, 2026-10-24). A system alert with one text
/// field, over whatever is on screen (a sheet, a page, nothing), so it works from every place the same way. Send sends with the
/// text (empty is fine); Cancel sends nothing. The text goes first in the sent log as "[user-comment]" lines (LogUploadCore).
enum BCDLogComment {
    /// The newest screenshot that belongs to a log (BCDScreenshotLive.install sets it; the preview app has none): offered with the comment.
    static var screenshotFor: ((URL) -> URL?)?
    /// The player chose to send `shot` along with `log`.
    static var screenshotChosen: ((URL, URL) -> Void)?
    /// What the box says about the session's performance trace, when there is one for that log (BCDPerfTraceSend.install sets it; the preview has none).
    static var traceNote: ((URL) -> String?)?

    /// `log`: the log about to be sent; when a screenshot was taken during its session the dialog offers to send that too (2026-10-25).
    /// Words the next box starts with (a guided debug run's notes, BCDHelpLive.swift); the person can change them. Taken by the next `ask`.
    static var prefill = ""

    static func ask(for log: URL? = nil, _ send: @escaping (String) -> Void, cancelled: (() -> Void)? = nil) {
        let starting = prefill
        prefill = ""

        // A moment later: the button that asked may be closing its own alert or menu.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
            guard let top = topController() else { return send("") }
            let shot = log.flatMap { screenshotFor?($0) }
            var message = "Add a comment if you like: what happened, what the log can't show (low FPS and "
                + "where, a stutter, what was on screen). Please don't write personal details."
            if shot != nil { message += "\n\nYou took a screenshot during this session. " + BCDScreenshotRules.sentAsItIs }
            if let log, let note = traceNote?(log) { message += "\n\n" + note }
            let alert = UIAlertController(title: "Add a comment", message: message, preferredStyle: .alert)
            alert.addTextField { field in
                field.placeholder = "Comment (optional)"
                field.text = starting.isEmpty ? nil : starting
                field.autocapitalizationType = .sentences
                field.returnKeyType = .send
                field.accessibilityIdentifier = "log.comment"
            }
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in cancelled?() })
            let sendAction = UIAlertAction(title: "Send", style: .default) { [weak alert] _ in
                send(alert?.textFields?.first?.text ?? "")
            }
            alert.addAction(sendAction)
            if let shot, let log {
                alert.addAction(UIAlertAction(title: "Send with the screenshot", style: .default) { [weak alert] _ in
                    screenshotChosen?(log, shot)
                    send(alert?.textFields?.first?.text ?? "")
                })
            }
            alert.preferredAction = sendAction
            top.present(alert, animated: true)
        }
    }

    private static func topController() -> UIViewController? {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let scene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        var top = scene?.keyWindow?.rootViewController
        while let presented = top?.presentedViewController, !presented.isBeingDismissed { top = presented }
        return top
    }
}

// MARK: - A page or sheet closed

/// A page, sheet or cover of BCD's interface closed (the game page, Settings, first-run setup, the library's sheets). Whoever
/// waits for the app's root screen to be free looks again then: auto-tune's question after a session (AutoTuneAlertGate), which
/// iOS would otherwise show by taking the open page down (2026-10-24). Nothing runs in between; the look comes a moment after
/// the close so that the page is gone from UIKit's presentation chain.
enum BCDPresentation {
    static var closed: (() -> Void)?
    static func didClose() {
        guard let closed else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { closed() }
    }
}

// MARK: - A Done button on the keyboard

/// The keyboard closes through its Done button where a person types config lines or long text (a game's config, a
/// setting typed as text, the lines of All settings, the feedback message): the text is kept, and a field that saves
/// when it loses focus saves. Tapping outside the field works as before. Attach it once to the screen that holds the
/// fields (the keyboard's bar collects the buttons of every view in it, so one for each field would show several).
struct BCDKeyboardDone: ViewModifier {
    /// Ends the screen's own focus state (`typing = false`), when it has one; the keyboard is closed either way.
    var closing: (() -> Void)? = nil

    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") {
                    closing?()
                    // Whichever field has the keyboard lets go of it.
                    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                }
                .accessibilityIdentifier("keyboard.done")
            }
        }
        .onAppear { BCDKeyboardDoneBar.install() }
    }
}

/// madeira-bcd (2026-10-24, the owner: the keyboard still had no Done button): SwiftUI's keyboard bar above does not show in a
/// screen that is not inside a navigation stack, and BCD's game page (a full-screen cover) and its Game config (a sheet) are not: on
/// iOS 27 the config editor's keyboard had no Done. So a multi-line editor (a UITextView: TextEditor, TextField with axis .vertical)
/// that starts editing without a bar of its own gets one here, with the same Done: the editor lets go of the keyboard, the text stays,
/// and a screen's focus state follows (it tracks the first responder). Where SwiftUI's bar does show, the editor already has it and
/// nothing is added. Single-line fields keep their return key. The game's own keyboard input (MetalBackedView, LibraryKeyInput: views
/// with UIKeyInput, not text views) and web pages never post this notification.
enum BCDKeyboardDoneBar {
    private static var observer: NSObjectProtocol?

    static func install() {
        guard observer == nil else { return }
        observer = NotificationCenter.default.addObserver(forName: UITextView.textDidBeginEditingNotification, object: nil,
                                                          queue: .main) { note in
            guard let editor = note.object as? UITextView else { return }
            // A turn later: SwiftUI puts its own bar on first, where it shows one.
            DispatchQueue.main.async {
                guard editor.isFirstResponder, editor.isEditable, editor.inputAccessoryView == nil else { return }
                editor.inputAccessoryView = bar()
                editor.reloadInputViews()
            }
        }
    }

    private static func bar() -> UIToolbar {
        let bar = UIToolbar(frame: CGRect(x: 0, y: 0, width: 320, height: 44))
        let done = UIBarButtonItem(title: "Done", primaryAction: UIAction { _ in
            UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
        })
        done.style = .done
        done.accessibilityIdentifier = "keyboard.done"
        bar.items = [UIBarButtonItem(systemItem: .flexibleSpace), done]
        bar.sizeToFit()
        return bar
    }
}

extension View {
    /// A Done button on the keyboard while this screen's fields are being typed into (BCDKeyboardDone).
    func bcdKeyboardDone(_ closing: (() -> Void)? = nil) -> some View { modifier(BCDKeyboardDone(closing: closing)) }
}
