// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

/// The parts the Accounts, JIT and BCD Dock sections of Settings are built from
/// (BCDAccounts.swift, BCDJIT.swift, BCDDock.swift), kept in one namespace so they never
/// clash with another screen's names. Every control here registers with BCDFocus, so a
/// controller reaches it and A runs it, and a page scrolls to whatever the controller
/// focuses. Nothing moves on its own: a switch slides and a page scrolls only when someone
/// acts (docs/BCD-UI-ARCHITECTURE.md, "Performance").
enum BCDSectionKit {
    /// The warm amber BCD uses for "needs a look" (the status chips use the same).
    static var amber: Color { BCDStyle.amber }

    enum Tone {
        case good, warn, bad, info, soft
        var color: Color {
            switch self {
            case .good: return BCDStyle.mint
            case .warn: return BCDSectionKit.amber
            case .bad: return BCDStyle.alert
            case .info: return BCDStyle.lavender
            case .soft: return BCDStyle.textSoft
            }
        }
    }

    enum ButtonKind { case primary, outline, quiet, danger }

    /// "iPhone" or "iPad", for sentences about this device.
    static var deviceWord: String { UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone" }

    /// BCDStyle's four kinds of type, sized through the page's scale (larger on an iPad).
    enum Face { case title, heading, body, label }

    struct ScaledFont: ViewModifier {
        @Environment(\.bcdSectionScale) private var scale
        let face: Face
        let size: CGFloat
        func body(content: Content) -> some View {
            let points = (size * scale).rounded()
            switch face {
            case .title: return content.font(BCDStyle.title(points))
            case .heading: return content.font(BCDStyle.heading(points))
            case .body: return content.font(BCDStyle.body(points))
            case .label: return content.font(BCDStyle.label(points))
            }
        }
    }
}

private struct BCDSectionScaleKey: EnvironmentKey { static let defaultValue: CGFloat = 1 }

extension EnvironmentValues {
    /// How much larger a section's type is drawn: 1 on an iPhone, a little more on an iPad.
    var bcdSectionScale: CGFloat {
        get { self[BCDSectionScaleKey.self] }
        set { self[BCDSectionScaleKey.self] = newValue }
    }
}

extension View {
    /// BCDStyle's type at `size` points on an iPhone, scaled up on an iPad (BCDSectionKit.Page).
    func bcdFont(_ face: BCDSectionKit.Face, _ size: CGFloat) -> some View {
        modifier(BCDSectionKit.ScaledFont(face: face, size: size))
    }
}

// MARK: - The page

extension BCDSectionKit {
    /// A section's page: its title, then its cards, in one column or, from 640 points of
    /// width (an iPhone on its side, an iPad), in two. On its own (`scrolls`) it scrolls and
    /// keeps the control a controller focuses in view; inside a host that scrolls already
    /// (Settings' detail pane) it only lays out, and the host scrolls. A sub-page (`back` set)
    /// gets a back button before its title.
    struct Page<Content: View>: View {
        @EnvironmentObject private var focus: BCDFocus
        @Environment(\.horizontalSizeClass) private var horizontal
        @Environment(\.verticalSizeClass) private var vertical
        let title: String
        var subtitle: String? = nil
        var showsTitle = true
        var scrolls = true
        /// The focus IDs this page owns start with it ("settings.jit.").
        let focusPrefix: String
        var back: (() -> Void)? = nil
        @ViewBuilder let content: () -> Content

        private var pad: Bool { horizontal == .regular && vertical == .regular }

        var body: some View {
            Group {
                if scrolls {
                    ScrollViewReader { reader in
                        ScrollView(.vertical, showsIndicators: false) {
                            stack
                                .padding(.horizontal, pad ? 28 : 18)
                                .padding(.top, pad ? 18 : 10).padding(.bottom, 28)
                                .frame(maxWidth: 1180, alignment: .leading)
                                .frame(maxWidth: .infinity)
                        }
                        .onChange(of: focus.focused) { _, id in
                            guard focus.controllerActive, let id, id.hasPrefix(focusPrefix) else { return }
                            withAnimation(.easeOut(duration: 0.2)) { reader.scrollTo(id, anchor: .center) }
                        }
                    }
                } else {
                    stack
                }
            }
            .environment(\.bcdSectionScale, pad ? 1.12 : 1)
        }

        private var stack: some View {
            VStack(alignment: .leading, spacing: pad ? 20 : 16) {
                if showsTitle || back != nil { header }
                content()
            }
        }

        private var header: some View {
            HStack(alignment: .center, spacing: 14) {
                if let back {
                    let id = focusPrefix + "back"
                    Button(action: back) {
                        Image(systemName: "chevron.left")
                            .font(.system(size: 17, weight: .bold))
                            .frame(width: 42, height: 42)
                            .background(BCDStyle.surfaceRaised, in: Circle())
                            .overlay(Circle().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .bcdFocusable(id, cornerRadius: 21, action: back)
                    .id(id)
                    .accessibilityLabel("Back")
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).bcdFont(.title, 28).accessibilityAddTraits(.isHeader)
                    if let subtitle {
                        Text(subtitle).bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// Two columns side by side when the width they get is 640 points or more, else one
    /// under the other. It decides from the width it is offered, so it works in any host.
    struct Columns<Left: View, Right: View>: View {
        @ViewBuilder let left: () -> Left
        @ViewBuilder let right: () -> Right
        var body: some View {
            ColumnsLayout(spacing: 16) {
                VStack(spacing: 16) { left() }
                VStack(spacing: 16) { right() }
            }
        }
    }

    /// Lays its two views out side by side (each half the width) from `minimumWide` points
    /// on, else stacked. Both stay in the view either way, so the controller's grid keeps them.
    struct ColumnsLayout: Layout {
        var spacing: CGFloat = 16
        var minimumWide: CGFloat = 640

        private func wide(_ width: CGFloat?) -> Bool { (width ?? 0) >= minimumWide }

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            guard let width = proposal.width, width.isFinite else {
                // No width offered: one column at its natural width.
                let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
                return CGSize(width: sizes.map(\.width).max() ?? 0,
                              height: sizes.map(\.height).reduce(0, +) + spacing * CGFloat(max(0, sizes.count - 1)))
            }
            if wide(width) {
                let column = ProposedViewSize(width: (width - spacing) / 2, height: nil)
                return CGSize(width: width, height: subviews.map { $0.sizeThatFits(column).height }.max() ?? 0)
            }
            let full = ProposedViewSize(width: width, height: nil)
            let heights = subviews.map { $0.sizeThatFits(full).height }
            return CGSize(width: width, height: heights.reduce(0, +) + spacing * CGFloat(max(0, heights.count - 1)))
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            if wide(bounds.width) {
                let columnWidth = (bounds.width - spacing) / 2
                for (index, view) in subviews.enumerated() {
                    view.place(at: CGPoint(x: bounds.minX + CGFloat(index) * (columnWidth + spacing), y: bounds.minY),
                               proposal: ProposedViewSize(width: columnWidth, height: nil))
                }
            } else {
                var y = bounds.minY
                for view in subviews {
                    let full = ProposedViewSize(width: bounds.width, height: nil)
                    view.place(at: CGPoint(x: bounds.minX, y: y), proposal: full)
                    y += view.sizeThatFits(full).height + spacing
                }
            }
        }
    }

    /// A host's hook for B. A host that runs the controller's buttons itself (Settings) passes
    /// one to a section and receives the section's own B: it returns true when it closed
    /// something of the section's (a page, a question), and the host goes back only when it
    /// returns false. The section hands nil when it goes away.
    typealias BackHook = (_ sectionBack: (() -> Bool)?) -> Void

    /// What A and B do in a section, set again whenever `key` changes (a sub-page opens, a
    /// question appears). With a host's `hook`, B is the host's and asks `inner` first; on its
    /// own (the preview app) the section sets A and B itself: `inner`, else `back`.
    struct Buttons<Key: Equatable>: ViewModifier {
        let focus: BCDFocus
        let key: Key
        /// Closes the section's own page or question; false when none is open.
        let inner: () -> Bool
        /// B on the section's first page with nothing open, when the section sets B itself.
        var back: (() -> Void)? = nil
        var hook: BackHook? = nil

        func body(content: Content) -> some View {
            content
                .onAppear(perform: install)
                .onChange(of: key) { _, _ in install() }
                .onDisappear { hook?(nil) }
        }

        private func install() {
            if let hook {
                hook(inner)
                return
            }
            let inner = inner, back = back
            focus.setHandlers([
                (BCDHint(button: .a, title: "Select"), {}),
                (BCDHint(button: .b, title: "Back"), { if !inner() { back?() } }),
            ])
        }
    }
}

// MARK: - Cards and words

extension BCDSectionKit {
    /// A card with a small spaced label over its contents. `accent` lights its border in
    /// the logo's colours, for the card that matters most right now.
    struct Card<Content: View>: View {
        var eyebrow: String? = nil
        var symbol: String? = nil
        var accent = false
        @ViewBuilder let content: () -> Content
        var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                if eyebrow != nil || symbol != nil {
                    HStack(spacing: 8) {
                        if let symbol {
                            Image(systemName: symbol).font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.lavender)
                        }
                        if let eyebrow { BCDEyebrow(text: eyebrow) }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(.isHeader)
                }
                content()
            }
            .bcdCard(padding: 16)
            .overlay {
                if accent {
                    RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(BCDStyle.glow, lineWidth: 1.5)
                }
            }
        }
    }

    /// A state in large type with a round icon in its colour, and a line under it.
    struct Headline: View {
        let text: String
        let symbol: String
        var tone: Tone = .info
        var detail: String? = nil
        var body: some View {
            HStack(alignment: .top, spacing: 14) {
                ZStack {
                    Circle().fill(tone.color.opacity(0.16))
                    Image(systemName: symbol).font(.system(size: 21, weight: .bold)).foregroundStyle(tone.color)
                }
                .frame(width: 46, height: 46)
                .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(text).bcdFont(.heading, 20).fixedSize(horizontal: false, vertical: true)
                    if let detail {
                        Text(detail).bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .accessibilityElement(children: .combine)
        }
    }

    /// A line of text with an icon, in a tone.
    struct Note: View {
        let text: String
        var symbol = "info.circle"
        var tone: Tone = .soft
        var body: some View {
            Label {
                Text(text).fixedSize(horizontal: false, vertical: true)
            } icon: {
                Image(systemName: symbol)
            }
            .bcdFont(.body, 14)
            .foregroundStyle(tone.color)
        }
    }

    /// Numbered steps. A step's text may use **bold**; a done step shows a check.
    struct Step: Identifiable {
        let id: Int
        let text: String
        var done = false
    }

    struct Steps: View {
        let steps: [Step]
        var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(steps.enumerated()), id: \.element.id) { index, step in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        ZStack {
                            if step.done {
                                Circle().fill(BCDStyle.mint.opacity(0.18))
                                Image(systemName: "checkmark").font(.system(size: 11, weight: .heavy)).foregroundStyle(BCDStyle.mint)
                            } else {
                                Circle().fill(BCDStyle.glow)
                                Text("\(index + 1)").font(.system(size: 12, weight: .heavy, design: .rounded)).foregroundStyle(BCDStyle.onAccent)
                            }
                        }
                        .frame(width: 24, height: 24)
                        .alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + 5 }
                        .accessibilityHidden(true)
                        Text(LocalizedStringKey(step.text))
                            .bcdFont(.body, 15)
                            .foregroundStyle(step.done ? BCDStyle.textSoft : BCDStyle.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(Text("Step \(index + 1): ") + Text(LocalizedStringKey(step.text)))
                    .accessibilityValue(step.done ? "Done" : "")
                }
            }
        }
    }

    /// A segmented progress bar: `of` segments, lit up to `lit`. No motion while it waits.
    struct StepBar: View {
        let lit: Int
        let of: Int
        var body: some View {
            HStack(spacing: 5) {
                ForEach(0..<max(1, of), id: \.self) { index in
                    Capsule()
                        .fill(index < lit ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke))
                        .frame(height: 6)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("Step \(min(lit, of)) of \(of)")
        }
    }
}

// MARK: - Controls

extension BCDSectionKit {
    /// A capsule button: the glowing main action, an outlined one, quiet lavender text, or
    /// a red-edged one for what removes something. `fill` stretches it across its column.
    struct ActionButton: View {
        let title: String
        var symbol: String? = nil
        var kind: ButtonKind = .outline
        var fill = false
        let id: String
        var enabled = true
        let action: () -> Void

        var body: some View {
            Button { if enabled { action() } } label: {
                HStack(spacing: 8) {
                    if let symbol { Image(systemName: symbol).font(.system(size: 15, weight: .semibold)) }
                    Text(title).lineLimit(2).multilineTextAlignment(.center).minimumScaleFactor(0.9)
                }
            }
            .buttonStyle(KitButtonStyle(kind: kind, fill: fill))
            .disabled(!enabled)
            .bcdFocusable(id, cornerRadius: 22) { if enabled { action() } }
            .id(id)
        }
    }

    struct KitButtonStyle: ButtonStyle {
        let kind: ButtonKind
        let fill: Bool
        @Environment(\.isEnabled) private var enabled

        func makeBody(configuration: Configuration) -> some View {
            configuration.label
                .bcdFont(.label, 15)
                .foregroundStyle(foreground)
                .padding(.horizontal, kind == .quiet ? 8 : 18).padding(.vertical, 10)
                .frame(maxWidth: fill ? .infinity : nil, minHeight: 44)
                .background {
                    switch kind {
                    case .primary: Capsule().fill(BCDStyle.glow)
                    case .outline: Capsule().fill(BCDStyle.surfaceRaised)
                    case .danger: Capsule().fill(BCDStyle.alert.opacity(0.10))
                    case .quiet: Color.clear
                    }
                }
                .overlay {
                    switch kind {
                    case .outline: Capsule().strokeBorder(BCDStyle.glow, lineWidth: 1.5)
                    case .danger: Capsule().strokeBorder(BCDStyle.alert.opacity(0.7), lineWidth: 1.5)
                    default: EmptyView()
                    }
                }
                .shadow(color: kind == .primary ? BCDStyle.fuchsia.opacity(0.3) : .clear, radius: 10, y: 3)
                .contentShape(Capsule())
                .opacity(enabled ? (configuration.isPressed ? 0.7 : 1) : 0.4)
        }

        private var foreground: Color {
            switch kind {
            case .primary: return .white
            case .outline: return BCDStyle.text
            case .quiet: return BCDStyle.lavender
            case .danger: return BCDStyle.alert
            }
        }
    }

    /// A row that switches something on or off. A (or a tap) flips it; the knob slides.
    struct SwitchRow: View {
        let title: String
        var detail: String? = nil
        let isOn: Bool
        let id: String
        var enabled = true
        let set: (Bool) -> Void

        var body: some View {
            Button { if enabled { set(!isOn) } } label: {
                HStack(alignment: .center, spacing: 14) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title).bcdFont(.label, 16).foregroundStyle(BCDStyle.text)
                        if let detail {
                            Text(detail).bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    ZStack(alignment: isOn ? .trailing : .leading) {
                        Capsule().fill(isOn ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke))
                        Circle().fill(.white).padding(3)
                    }
                    .frame(width: 52, height: 31)
                    .animation(.easeOut(duration: 0.15), value: isOn)
                }
                .padding(.vertical, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!enabled)
            .opacity(enabled ? 1 : 0.45)
            .bcdFocusable(id, cornerRadius: 14) { if enabled { set(!isOn) } }
            .id(id)
            .accessibilityLabel(title)
            .accessibilityValue(isOn ? "On" : "Off")
        }
    }

    /// One of several choices: a round mark that fills when chosen, a title and a line.
    struct ChoiceRow: View {
        let title: String
        let detail: String
        let symbol: String
        let selected: Bool
        let id: String
        let choose: () -> Void

        var body: some View {
            Button(action: choose) {
                HStack(alignment: .center, spacing: 14) {
                    Image(systemName: symbol).font(.system(size: 20, weight: .semibold))
                        .foregroundStyle(BCDStyle.glow).frame(width: 30)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(title).bcdFont(.heading, 17).foregroundStyle(BCDStyle.text)
                        Text(detail).bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundStyle(selected ? BCDStyle.mint : BCDStyle.stroke)
                        .accessibilityHidden(true)
                }
                .padding(12)
                .background(selected ? BCDStyle.surfaceRaised : Color.clear,
                            in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(selected ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke),
                                  lineWidth: selected ? 1.5 : 1))
                .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            }
            .buttonStyle(.plain)
            .bcdFocusable(id, cornerRadius: 16, action: choose)
            .id(id)
            .accessibilityAddTraits(selected ? .isSelected : [])
        }
    }

    /// Two or three options in a row, one of them chosen.
    struct Segments<Value: Hashable>: View {
        let options: [(value: Value, title: String)]
        let selected: Value
        let idPrefix: String
        let choose: (Value) -> Void

        var body: some View {
            HStack(spacing: 4) {
                ForEach(options.indices, id: \.self) { index in
                    let option = options[index]
                    let on = option.value == selected
                    let id = idPrefix + String(index)
                    Button { choose(option.value) } label: {
                        Text(option.title)
                            .bcdFont(.label, 14).lineLimit(1).minimumScaleFactor(0.8)
                            .foregroundStyle(on ? BCDStyle.ground : BCDStyle.textSoft)
                            .padding(.horizontal, 14).frame(maxWidth: .infinity, minHeight: 38)
                            .background { if on { Capsule().fill(BCDStyle.text) } }
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .bcdFocusable(id, cornerRadius: 19) { choose(option.value) }
                    .id(id)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
            .padding(4)
            .background(BCDStyle.ground.opacity(0.55), in: Capsule())
            .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        }
    }

    /// A text field in BCD's look. A on it (from a controller) puts the cursor there.
    struct FieldBox<Field: View>: View {
        let id: String
        let activate: () -> Void
        @ViewBuilder let field: () -> Field
        var body: some View {
            field()
                .bcdFont(.body, 17)
                .foregroundStyle(BCDStyle.text)
                .tint(BCDStyle.lavender)
                .padding(.horizontal, 14).frame(minHeight: 48)
                .background(BCDStyle.ground.opacity(0.7), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
                .bcdFocusable(id, cornerRadius: 14, action: activate)
                .id(id)
        }
    }

    /// A question asked in place, instead of a system alert a controller cannot answer.
    struct Confirm: View {
        struct Choice {
            let title: String
            let kind: ButtonKind
            let id: String
            let action: () -> Void
        }
        let title: String
        let message: String
        let choices: [Choice]

        var body: some View {
            VStack(alignment: .leading, spacing: 12) {
                Label(title, systemImage: "questionmark.circle.fill")
                    .bcdFont(.heading, 17).foregroundStyle(BCDSectionKit.amber)
                    .fixedSize(horizontal: false, vertical: true)
                Text(message).bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                if choices.count <= 2 {
                    HStack(spacing: 10) { buttons }
                } else {
                    VStack(spacing: 10) { buttons }
                }
            }
            .padding(14)
            .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(BCDSectionKit.amber.opacity(0.6), lineWidth: 1))
        }

        private var buttons: some View {
            ForEach(choices.indices, id: \.self) { index in
                let choice = choices[index]
                ActionButton(title: choice.title, kind: choice.kind, fill: true, id: choice.id, action: choice.action)
            }
        }
    }

    /// Buttons side by side while they fit, the rest on the next line. Every button stays in
    /// the view (unlike ViewThatFits), so each keeps its place in the controller's grid.
    struct Flow: Layout {
        var spacing: CGFloat = 8

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            let rows = arrange(subviews, width: proposal.width ?? .infinity)
            let height = rows.last.map { $0.y + $0.height } ?? 0
            return CGSize(width: proposal.width ?? (rows.map(\.width).max() ?? 0), height: height)
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            for row in arrange(subviews, width: bounds.width) {
                for item in row.items {
                    subviews[item.index].place(at: CGPoint(x: bounds.minX + item.x, y: bounds.minY + row.y),
                                               proposal: ProposedViewSize(width: item.size.width, height: item.size.height))
                }
            }
        }

        private struct Row {
            var items: [(index: Int, x: CGFloat, size: CGSize)] = []
            var y: CGFloat = 0
            var width: CGFloat = 0
            var height: CGFloat = 0
        }

        private func arrange(_ subviews: Subviews, width: CGFloat) -> [Row] {
            var rows: [Row] = []
            var row = Row()
            for index in subviews.indices {
                let ideal = subviews[index].sizeThatFits(.unspecified)
                // Wider than the line: give it the line, and let it wrap its words.
                let size = ideal.width <= width ? ideal : subviews[index].sizeThatFits(ProposedViewSize(width: width, height: nil))
                if !row.items.isEmpty && row.width + spacing + size.width > width {
                    let y = row.y + row.height + spacing
                    rows.append(row)
                    row = Row(y: y)
                }
                let x = row.items.isEmpty ? 0 : row.width + spacing
                row.items.append((index, x, CGSize(width: min(size.width, width), height: size.height)))
                row.width = x + min(size.width, width)
                row.height = max(row.height, size.height)
            }
            if !row.items.isEmpty { rows.append(row) }
            return rows
        }
    }

    /// The share sheet, for a file to hand to another app.
    struct ShareSheet: UIViewControllerRepresentable {
        let items: [Any]
        func makeUIViewController(context: Context) -> UIActivityViewController {
            UIActivityViewController(activityItems: items, applicationActivities: nil)
        }
        func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
    }
}
