// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI
import UIKit

// The in-game menu: a still panel over the dimmed game (never blurred), built when it opens and
// gone when it closes. Its pages (Game, Display, Controls, Tools) and their rows come from a
// BCDSessionMenuSource (BCDSession.swift), so the view holds no meaning of its own: the live
// source decides what a session offers and carries it out on the engine.
//
// Touch: tap a row; drag a bar. Controller: the D-pad moves the ring, A chooses, left and right
// move a bar, LB and RB change the page, B goes back (a question, a page, then the game), and
// Menu closes it.

struct BCDInGameMenu: View {
    enum Presentation { case overlay, sheet }

    @ObservedObject var model: BCDSessionMenuModel
    let insets: EdgeInsets
    var presentation: Presentation = .overlay
    @StateObject private var focus: BCDFocus
    @State private var tab: BCDSessionTab
    @State private var pages: [String]
    @State private var asking: BCDMenuRow?
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    init(model: BCDSessionMenuModel, insets: EdgeInsets, presentation: Presentation = .overlay, focus: BCDFocus? = nil) {
        self.model = model
        self.insets = insets
        self.presentation = presentation
        _focus = StateObject(wrappedValue: focus ?? BCDFocus(watchControllers: false))
        _tab = State(initialValue: model.initialTab)
        _pages = State(initialValue: model.initialPage.map { [$0] } ?? [])
    }

    private var source: BCDSessionMenuSource { model.source }
    private var pad: Bool { horizontal == .regular && vertical == .regular }

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height
            ZStack(alignment: wide ? .leading : .bottom) {
                if presentation == .overlay {
                    BCDSessionStyle.dim
                        .contentShape(Rectangle())
                        .onTapGesture { source.close() }
                        .accessibilityLabel("Back to the game")
                        .accessibilityAddTraits(.isButton)
                }
                panel
                    .frame(width: panelWidth(geo.size, wide: wide), height: panelHeight(geo.size, wide: wide))
                    .background(BCDSessionStyle.panel.opacity(0.98), in: RoundedRectangle(cornerRadius: corner, style: .continuous))
                    .overlay(alignment: .top) {
                        // The logo's light along the top edge, drawn once.
                        LinearGradient(colors: [BCDStyle.violet.opacity(0.22), .clear], startPoint: .top, endPoint: .bottom)
                            .frame(height: 90)
                            .clipShape(RoundedRectangle(cornerRadius: corner, style: .continuous))
                            .allowsHitTesting(false)
                    }
                    .overlay(RoundedRectangle(cornerRadius: corner, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
                    .padding(.leading, wide && presentation == .overlay ? insets.leading + 12 : 0)
                    .padding(.bottom, !wide && presentation == .overlay ? insets.bottom + 8 : 0)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: wide ? .leading : .bottom)
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .coordinateSpace(name: BCDFocus.space)
        .environmentObject(focus)
        .foregroundStyle(BCDStyle.text)
        .onAppear {
            if let id = model.initialQuestion, let row = rowsShown.first(where: { $0.id == id }), row.confirm != nil { asking = row }
            installButtons()
            focusFirst()
            source.pageShown(pages.last)
        }
        .onDisappear { source.pageShown(nil) }
        .onChange(of: pages) { _, now in
            installButtons(); focusFirst(); source.pageShown(now.last)
        }
        .onChange(of: tab) { _, _ in installButtons(); focusFirst() }
        .onChange(of: asking) { _, _ in installButtons(); focusFirst() }
        .onReceive(model.commands) { command($0) }
    }

    private var corner: CGFloat { presentation == .sheet ? 0 : 24 }

    private func panelWidth(_ size: CGSize, wide: Bool) -> CGFloat {
        if presentation == .sheet { return size.width }
        if wide { return min(pad ? 470 : 410, max(320, size.width * 0.5)) }
        return min(pad ? 600 : size.width - 16, size.width - 16)
    }

    private func panelHeight(_ size: CGSize, wide: Bool) -> CGFloat {
        if presentation == .sheet { return size.height }
        if wide { return max(240, size.height - insets.top - insets.bottom - 24) }
        return min(size.height * (pad ? 0.7 : 0.8), size.height - insets.top - insets.bottom - 24)
    }

    // MARK: The panel

    private var panel: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, 18).padding(.top, 16).padding(.bottom, 10)
            if asking == nil && pages.isEmpty && source.tabs.count > 1 {
                tabBar.padding(.horizontal, 14).padding(.bottom, 8)
            }
            Rectangle().fill(BCDStyle.stroke.opacity(0.8)).frame(height: 1)
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if let asking, let question = asking.confirm {
                            confirmCard(asking, question)
                        } else if let id = pages.last {
                            pageContent(id)
                        } else {
                            ForEach(source.sections(tab)) { section($0) }
                        }
                    }
                    .padding(.horizontal, 14).padding(.vertical, 14)
                }
                .scrollIndicators(.hidden)
                .onChange(of: focus.focused) { _, id in
                    guard focus.controllerActive, let id else { return }
                    withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(id) }
                }
            }
            if focus.controllerActive { hintBar }
        }
        .clipShape(RoundedRectangle(cornerRadius: corner, style: .continuous))
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            if !pages.isEmpty && asking == nil {
                Button { back() } label: {
                    Image(systemName: "chevron.left").font(.system(size: 16, weight: .bold))
                        .frame(width: 36, height: 36)
                        .background(BCDStyle.surfaceRaised, in: Circle())
                }
                .buttonStyle(.plain)
                .bcdFocusable("menu.back", cornerRadius: 18) { back() }
                .accessibilityLabel("Back")
            }
            VStack(alignment: .leading, spacing: 3) {
                BCDEyebrow(text: pages.isEmpty || asking != nil ? source.subtitle : source.title)
                Text(pageTitle)
                    .font(BCDStyle.heading(pad ? 23 : 20)).lineLimit(2).minimumScaleFactor(0.85)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button { source.close() } label: {
                Image(systemName: "xmark").font(.system(size: 14, weight: .heavy))
                    .frame(width: 36, height: 36)
                    .background(BCDStyle.surfaceRaised, in: Circle())
                    .overlay(Circle().strokeBorder(BCDStyle.stroke, lineWidth: 1))
            }
            .buttonStyle(.plain)
            .bcdFocusable("menu.close", cornerRadius: 18) { source.close() }
            .accessibilityLabel("Back to the game")
        }
    }

    private var pageTitle: String {
        if asking != nil { return source.title }
        if let id = pages.last { return source.page(id)?.title ?? source.title }
        return source.title
    }

    private var tabBar: some View {
        HStack(spacing: 6) {
            if focus.controllerActive { shoulder("LB") }
            ForEach(source.tabs) { item in
                let selected = item == tab
                Button { select(item) } label: {
                    Text(item.title)
                        .font(BCDStyle.label(pad ? 15 : 14)).lineLimit(1)
                        .padding(.horizontal, 12).frame(minHeight: 32)
                        .foregroundStyle(selected ? BCDStyle.ground : BCDStyle.textSoft)
                        .background { if selected { Capsule().fill(BCDStyle.text) } }
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .bcdFocusable("menu.tab." + item.rawValue, cornerRadius: 16) { select(item) }
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
            if focus.controllerActive { shoulder("RB") }
            Spacer(minLength: 0)
        }
    }

    private func shoulder(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .heavy, design: .rounded))
            .foregroundStyle(BCDStyle.ground)
            .frame(width: 28, height: 20)
            .background(BCDStyle.text, in: Capsule())
            .accessibilityHidden(true)
    }

    private var hintBar: some View {
        HStack(spacing: 16) {
            Spacer(minLength: 0)
            ForEach(focus.hints.filter { $0.button != .leftShoulder && $0.button != .rightShoulder }) { BCDHintView(hint: $0) }
        }
        .padding(.horizontal, 14).frame(height: 40)
        .overlay(alignment: .top) { Rectangle().fill(BCDStyle.stroke.opacity(0.8)).frame(height: 1) }
    }

    // MARK: Sections and rows

    private func section(_ section: BCDMenuSection) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            if let title = section.title { BCDEyebrow(text: title).padding(.leading, 4) }
            let card = section.rows.filter { !isLoose($0) }
            ForEach(section.rows.filter(isLoose)) { row($0) }
            if !card.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(card.enumerated()), id: \.element.id) { index, item in
                        if index > 0 { Rectangle().fill(BCDStyle.stroke.opacity(0.7)).frame(height: 1).padding(.leading, 14) }
                        row(item)
                    }
                }
                .background(BCDStyle.surface.opacity(0.85), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
            }
        }
    }

    /// The panel's main button stands on its own, outside the card.
    private func isLoose(_ row: BCDMenuRow) -> Bool {
        if case .action(let primary, _) = row.kind { return primary }
        return false
    }

    @ViewBuilder private func row(_ row: BCDMenuRow) -> some View {
        let id = "menu.row." + row.id
        switch row.kind {
        case .action(let primary, let destructive):
            if primary {
                Button { activate(row) } label: {
                    Label(row.title, systemImage: row.symbol ?? "play.fill")
                        .font(BCDStyle.label(17)).foregroundStyle(BCDStyle.onAccent)
                        .frame(maxWidth: .infinity, minHeight: 50)
                        .background(BCDStyle.glow, in: Capsule())
                        .shadow(color: BCDStyle.fuchsia.opacity(0.3), radius: 10, y: 3)
                }
                .buttonStyle(.plain)
                .focusable(row, id: id, radius: 25) { activate(row) }
            } else {
                plainRow(row, id: id, tint: destructive ? BCDStyle.alert : BCDStyle.text) {
                    if row.confirm != nil && !destructive {
                        Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                    }
                } action: { activate(row) }
            }
        case .toggle(let on):
            plainRow(row, id: id, tint: BCDStyle.text) {
                BCDSwitch(on: on)
            } action: { run { source.toggle(row.id, !on) } }
                .accessibilityValue(on ? "On" : "Off")
        case .choice(let choices, let selected):
            chipsRow(row, choices: choices) { $0 == selected } choose: { value in run { source.choose(row.id, value) } }
        case .multi(let choices, let selected):
            chipsRow(row, choices: choices) { selected.contains($0) } choose: { value in run { source.choose(row.id, value) } }
        case .slider(let value, let range, let step, let text):
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    titles(row, tint: BCDStyle.text)
                    Spacer(minLength: 8)
                    Text(verbatim: text).font(BCDStyle.label(14).monospacedDigit()).foregroundStyle(BCDStyle.lavender)
                }
                BCDValueBar(value: value, range: range, step: step) { new in run { source.slide(row.id, new) } }
                    .frame(height: 28)
            }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .opacity(row.enabled ? 1 : 0.45)
            .focusable(row, id: id, radius: 14) {}
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(row.title)
            .accessibilityValue(text)
            .accessibilityAdjustableAction { direction in
                adjust(row, by: direction == .increment ? 1 : -1)
            }
        case .page(let page, let value):
            plainRow(row, id: id, tint: BCDStyle.text) {
                HStack(spacing: 6) {
                    if let value { Text(value).font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft).lineLimit(1) }
                    Image(systemName: "chevron.right").font(.system(size: 12, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                }
            } action: { pages.append(page) }
        case .pick(let selected):
            plainRow(row, id: id, tint: BCDStyle.text) {
                Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(selected ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.textSoft.opacity(0.6)))
            } action: { activate(row) }
                .accessibilityAddTraits(selected ? .isSelected : [])
        case .note(let tone):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let symbol = row.symbol { Image(systemName: symbol).font(.system(size: 13, weight: .semibold)) }
                Text(row.title).font(BCDStyle.body(13)).fixedSize(horizontal: false, vertical: true)
            }
            .foregroundStyle(tone == .soft ? BCDStyle.textSoft : tone.color)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14).padding(.vertical, 10)
        case .custom(let part):
            if let draw = model.custom[part] {
                draw()
                    .padding(.horizontal, 14).padding(.vertical, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        case .share(let files):
            ShareLink(items: files) {
                HStack(spacing: 12) {
                    titles(row, tint: BCDStyle.text)
                    Spacer(minLength: 8)
                    Image(systemName: "square.and.arrow.up").font(.system(size: 14, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
                }
                .padding(.horizontal, 14).frame(minHeight: 50)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(files.isEmpty || !row.enabled)
            .opacity(files.isEmpty || !row.enabled ? 0.45 : 1)
        }
    }

    /// A row: its symbol, title and detail on the left, `trailing` on the right; the whole row
    /// is the button.
    private func plainRow<Trailing: View>(_ row: BCDMenuRow, id: String, tint: Color, @ViewBuilder trailing: () -> Trailing,
                                          action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                if let data = row.picture, let picture = UIImage(data: data) {   // madeira-bcd (round 604): a window's small picture
                    Image(uiImage: picture).resizable().scaledToFit()
                        .frame(width: 64, height: 40)
                        .background(Color.black, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                        .accessibilityHidden(true)
                } else if let symbol = row.symbol {
                    Image(systemName: symbol).font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(tint == BCDStyle.text ? BCDStyle.lavender : tint)
                        .frame(width: 22)
                }
                titles(row, tint: tint)
                Spacer(minLength: 8)
                trailing()
            }
            .padding(.horizontal, 14).padding(.vertical, 8).frame(minHeight: 50)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(!row.enabled)
        .opacity(row.enabled ? 1 : 0.45)
        .focusable(row, id: id, radius: 14, action: action)
    }

    private func titles(_ row: BCDMenuRow, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(row.title).font(BCDStyle.label(15)).foregroundStyle(tint).fixedSize(horizontal: false, vertical: true)
            if let detail = row.detail {
                Text(detail).font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func chipsRow(_ row: BCDMenuRow, choices: [BCDChoice], isOn: @escaping (String) -> Bool,
                          choose: @escaping (String) -> Void) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            titles(row, tint: BCDStyle.text)
            BCDChipFlow(spacing: 7) {
                ForEach(choices) { choice in
                    let on = isOn(choice.id)
                    Button { choose(choice.id) } label: {
                        Text(choice.title)
                            .font(BCDStyle.label(14)).lineLimit(1)
                            .foregroundStyle(on ? Color.white : BCDStyle.text)
                            .padding(.horizontal, 13).frame(minHeight: 34)
                            .background {
                                if on { Capsule().fill(BCDStyle.glow) } else { Capsule().fill(BCDStyle.surfaceRaised) }
                            }
                            .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: on ? 0 : 1))
                            .contentShape(Capsule())
                    }
                    .buttonStyle(.plain)
                    .disabled(!row.enabled)
                    .bcdFocusable("menu.row.\(row.id).\(choice.id)", cornerRadius: 17) { if row.enabled { choose(choice.id) } }
                    .id("menu.row.\(row.id).\(choice.id)")
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 11)
        .opacity(row.enabled ? 1 : 0.45)
    }

    // MARK: A page and a question

    @ViewBuilder private func pageContent(_ id: String) -> some View {
        if let page = source.page(id) {
            switch page.kind {
            case .rows:
                ForEach(page.sections) { section($0) }
            case .log:
                ForEach(page.sections) { section($0) }   // madeira-bcd (round 604): pause, clear and export above the lines
                BCDLogLines(lines: source.logLines(), size: 10)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    .id("menu.log.\(model.revision)")
            }
        }
    }

    private func confirmCard(_ row: BCDMenuRow, _ question: BCDConfirm) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(question.title).font(BCDStyle.heading(19)).fixedSize(horizontal: false, vertical: true)
            if !question.message.isEmpty {
                Text(question.message).font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(question.answers) { answer in
                Button { reply(row, answer.id) } label: {
                    Text(answer.title)
                        .font(BCDStyle.label(16)).foregroundStyle(.white)
                        .frame(maxWidth: .infinity, minHeight: 48)
                        .background {
                            if answer.destructive { Capsule().fill(BCDStyle.alert.opacity(0.85)) } else { Capsule().fill(BCDStyle.glow) }
                        }
                }
                .buttonStyle(.plain)
                .bcdFocusable("menu.confirm." + answer.id, cornerRadius: 24) { reply(row, answer.id) }
                .id("menu.confirm." + answer.id)
            }
            Button { asking = nil } label: {
                Text("Cancel").font(BCDStyle.label(16)).foregroundStyle(BCDStyle.lavender)
                    .frame(maxWidth: .infinity, minHeight: 46)
                    .background(BCDStyle.surfaceRaised, in: Capsule())
            }
            .buttonStyle(.plain)
            .bcdFocusable("menu.confirm.cancel", cornerRadius: 23) { asking = nil }
            .id("menu.confirm.cancel")
        }
        .padding(16)
        .background(BCDStyle.surface.opacity(0.9), in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
    }

    // MARK: What a choice does

    private func run(_ work: () -> Void) {
        work()
        model.changed()
    }

    private func activate(_ row: BCDMenuRow) {
        guard row.enabled else { return }
        if row.confirm != nil { asking = row; return }
        if case .pick = row.kind, let page = pages.last {
            run { source.choose(page, row.id) }
            back()
            return
        }
        run { source.act(row.id) }
    }

    private func reply(_ row: BCDMenuRow, _ answer: String) {
        asking = nil
        run { source.answer(row.id, answer) }
        if case .pick = row.kind { back() }
    }

    private func select(_ next: BCDSessionTab) {
        guard next != tab else { return }
        tab = next
    }

    private func cycle(_ step: Int) {
        guard asking == nil, pages.isEmpty else { return }
        let all = source.tabs
        guard let index = all.firstIndex(of: tab), !all.isEmpty else { return }
        tab = all[(index + step + all.count) % all.count]
    }

    private func back() {
        if asking != nil { asking = nil } else if !pages.isEmpty { pages.removeLast() } else { source.close() }
    }

    /// The rows shown now, to find the one the ring is on.
    private var rowsShown: [BCDMenuRow] {
        if let id = pages.last { return source.page(id)?.sections.flatMap(\.rows) ?? [] }
        return source.sections(tab).flatMap(\.rows)
    }

    private func adjust(_ row: BCDMenuRow, by step: Int) {
        guard row.enabled, case .slider(let value, let range, let size, _) = row.kind else { return }
        let next = min(range.upperBound, max(range.lowerBound, value + Double(step) * size))
        run { source.slide(row.id, next) }
    }

    private func focusFirst() {
        let first: String?
        if let asking, let answer = asking.confirm?.answers.first {
            first = "menu.confirm." + answer.id
        } else if let row = rowsShown.first(where: { $0.enabled && isFocusable($0) }) {
            if case .choice(let choices, let selected) = row.kind { first = "menu.row.\(row.id).\(choices.first { $0.id == selected }?.id ?? choices.first?.id ?? "")" }
            else if case .multi(let choices, _) = row.kind { first = "menu.row.\(row.id).\(choices.first?.id ?? "")" }
            else { first = "menu.row." + row.id }
        } else {
            first = "menu.close"
        }
        focus.focused = first
    }

    private func isFocusable(_ row: BCDMenuRow) -> Bool {
        switch row.kind {
        case .note, .share, .custom: return false
        default: return true
        }
    }

    private func installButtons() {
        let backTitle = asking != nil ? "Cancel" : pages.isEmpty ? "Back to the game" : "Back"
        var list: [(BCDHint, () -> Void)] = [
            (BCDHint(button: .a, title: "Choose"), {}),
            (BCDHint(button: .b, title: backTitle), { back() }),
            (BCDHint(button: .menu, title: "Close"), { source.close() }),
        ]
        if asking == nil && pages.isEmpty && source.tabs.count > 1 {
            list += [(BCDHint(button: .leftShoulder, title: "Previous"), { cycle(-1) }),
                     (BCDHint(button: .rightShoulder, title: "Next"), { cycle(1) })]
        }
        focus.setHandlers(list)
    }

    private func command(_ command: BCDSessionCommand) {
        switch command {
        case .up: focus.move(.up)
        case .down: focus.move(.down)
        case .left, .right:
            let step = command == .left ? -1 : 1
            if focus.controllerActive, let id = focus.focused,
               let row = rowsShown.first(where: { "menu.row." + $0.id == id }), case .slider = row.kind {
                focus.press(.options)   // only marks the controller as in use; no handler is set for it
                adjust(row, by: step)
            } else {
                focus.move(command == .left ? .left : .right)
            }
        case .accept: focus.press(.a)
        case .back: focus.press(.b)
        case .previousTab: focus.press(.leftShoulder)
        case .nextTab: focus.press(.rightShoulder)
        case .menu: focus.press(.menu)
        }
    }
}

private extension View {
    /// A row the ring can reach, unless it is switched off; scrolled to by its id.
    @ViewBuilder func focusable(_ row: BCDMenuRow, id: String, radius: CGFloat, action: @escaping () -> Void) -> some View {
        if row.enabled {
            self.bcdFocusable(id, cornerRadius: radius, action: action).id(id)
        } else {
            self.id(id)
        }
    }
}

// MARK: - Small controls

/// An on/off switch in the logo's light.
struct BCDSwitch: View {
    let on: Bool
    var body: some View {
        ZStack(alignment: on ? .trailing : .leading) {
            Capsule()
                .fill(on ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.surfaceRaised))
                .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: on ? 0 : 1))
            Circle().fill(Color.white).frame(width: 22, height: 22).padding(3)
                .shadow(color: .black.opacity(0.25), radius: 2, y: 1)
        }
        .frame(width: 50, height: 28)
        .animation(.easeOut(duration: 0.16), value: on)
        .accessibilityHidden(true)
    }
}

/// A value along a bar: drag or tap to set it; it snaps to `step`.
struct BCDValueBar: View {
    let value: Double
    let range: ClosedRange<Double>
    let step: Double
    let set: (Double) -> Void

    var body: some View {
        GeometryReader { proxy in
            let span = max(0.0001, range.upperBound - range.lowerBound)
            let fraction = min(1, max(0, (value - range.lowerBound) / span))
            let width = proxy.size.width
            ZStack(alignment: .leading) {
                Capsule().fill(BCDStyle.surfaceRaised).frame(height: 6)
                Capsule().fill(BCDStyle.glow).frame(width: max(6, width * fraction), height: 6)
                Circle().fill(Color.white).frame(width: 22, height: 22)
                    .shadow(color: .black.opacity(0.3), radius: 2, y: 1)
                    .offset(x: max(0, min(width - 22, width * fraction - 11)))
            }
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { drag in
                let share = min(1, max(0, drag.location.x / max(1, width)))
                var next = range.lowerBound + share * span
                if step > 0 { next = (next / step).rounded() * step }
                next = min(range.upperBound, max(range.lowerBound, next))
                if abs(next - value) > step / 4 { set(next) }
            })
        }
    }
}

/// Chips in rows, wrapping to the next row when one is full.
struct BCDChipFlow: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, row: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width { y += row + spacing; x = 0; row = 0 }
            x += size.width + spacing
            row = max(row, size.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: proposal.width ?? widest, height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, row: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX { y += row + spacing; x = bounds.minX; row = 0 }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            row = max(row, size.height)
        }
    }
}
