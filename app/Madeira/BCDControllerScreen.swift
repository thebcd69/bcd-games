// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

/// BCD's controller screen (BCDControllerHub.swift): the controllers connected, with their
/// battery; a live tester; the switch that makes the controller a keyboard and mouse, with when
/// to use it; the table of what each control sends; and XInput's state. A phone on its side shows
/// the tester first, beside the switch; an upright phone stacks everything; an iPad shows it all
/// with room to spare, the table in two columns.
///
/// Opened from Settings › Controls and from the classic settings and developer screens. Inside
/// BCD's shell pass the shell's focus (`focus:`), so the controller reaches the switches; without
/// it the screen brings its own (touch only). `close` adds a Done button.
struct BCDControllerScreen: View {
    @ObservedObject private var hub: BCDControllerHub
    private let shellFocus: BCDFocus?
    private let close: (() -> Void)?
    @StateObject private var ownFocus = BCDFocus(watchControllers: false)

    /// `hub` nil: the app's (BCDControllerHub.shared).
    @MainActor init(hub: BCDControllerHub? = nil, focus: BCDFocus? = nil, close: (() -> Void)? = nil) {
        _hub = ObservedObject(wrappedValue: hub ?? .shared)
        shellFocus = focus
        self.close = close
    }

    var body: some View {
        BCDControllerPage(hub: hub, close: close)
            .environmentObject(shellFocus ?? ownFocus)
    }
}

private struct BCDControllerPage: View {
    @ObservedObject var hub: BCDControllerHub
    let close: (() -> Void)?
    @EnvironmentObject private var focus: BCDFocus
    /// The screen's last focused control: a turn of the device rebuilds the arrangement, which
    /// drops the focus of the controls it takes down; it comes back here.
    @State private var lastFocus: String?
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    private var layout: BCDLayout { BCDLayout.of(horizontal: horizontal, vertical: vertical) }
    private var roomy: Bool { layout == .pad }
    private var cardPadding: CGFloat { roomy ? 20 : 14 }
    private var spacing: CGFloat { roomy ? 20 : 14 }
    /// The table's names: PlayStation's for a PlayStation controller, else Xbox's.
    private var family: BCDPadFamily { hub.family == .playStation ? .playStation : .xbox }

    var body: some View {
        GeometryReader { proxy in
            let current = arrangement(proxy.size)
            VStack(spacing: 0) {
                header
                ScrollViewReader { scroller in
                    ScrollView(.vertical) {
                        content(current)
                            .padding(.horizontal, layout.gutter)
                            .padding(.top, 6)
                            .padding(.bottom, 28)
                    }
                    .modifier(BCDSoftTopEdge())
                    // The ring goes to a switch below the fold: the page follows it.
                    .onChange(of: focus.focused) { _, id in
                        guard let id, id.hasPrefix("controllers.") else { return }
                        withAnimation(.easeOut(duration: 0.2)) { scroller.scrollTo(id, anchor: .center) }
                    }
                }
                // Inside a shell the buttons it assigned show along the bottom (a screen with no focus of its
                // own, a sheet over the classic screens, has none).
                if focus.controllerActive && !focus.hints.isEmpty {
                    HStack(spacing: 22) {
                        Spacer()
                        ForEach(focus.hints) { BCDHintView(hint: $0) }
                    }
                    .padding(.horizontal, layout.gutter).frame(height: 40)
                    .allowsHitTesting(false)
                }
            }
            .onChange(of: current) { _, _ in
                DispatchQueue.main.async {
                    if focus.focused == nil, let lastFocus { focus.focused = lastFocus }
                }
            }
        }
        .onAppear { if let id = focus.focused, id.hasPrefix("controllers.") { lastFocus = id } }
        .onChange(of: focus.focused) { _, id in
            if let id, id.hasPrefix("controllers.") { lastFocus = id }
        }
        .background { BCDBackdrop() }
        .coordinateSpace(name: BCDFocus.space)
        .environment(\.bcdLayout, layout)
        .foregroundStyle(BCDStyle.text)
        .preferredColorScheme(.dark)
        .onAppear {
            hub.refresh()
            hub.watchTester(true)
        }
        .onDisappear { hub.watchTester(false) }
    }

    private var testerCard: some View {
        BCDPadTesterCard(tester: hub.tester, ready: hub.isOn && hub.pads.contains { $0.gamepad }, on: hub.isOn,
                         family: family, roomy: roomy, well: roomy ? 118 : layout == .phoneLandscape ? 78 : 92,
                         padding: cardPadding)
    }

    /// Round 604: which controller is Player 1 to 4, and how hard each rumbles (BCDPlayers.swift).
    private var playersCard: some View {
        BCDPlayersCard(roomy: roomy, padding: cardPadding)
    }

    private var tableCard: some View {
        BCDPadTableCard(tester: hub.tester, mapping: hub.mapping, family: family, roomy: roomy, padding: cardPadding)
    }

    private enum Arrangement { case stacked, columns, padUpright }

    /// An upright phone, or anything narrow (an iPad's sheet): one column. On its side: two
    /// columns, the tester first on a phone (what a controller in hand looks for), the table under
    /// the switch. An upright iPad: the controllers beside the tester, then the switch and the
    /// table across, so it all fits without scrolling.
    private func arrangement(_ size: CGSize) -> Arrangement {
        if layout == .phonePortrait || size.width < 640 { return .stacked }
        if size.width > size.height { return .columns }
        return layout == .pad && size.width >= 760 ? .padUpright : .stacked
    }

    @ViewBuilder private func content(_ arrangement: Arrangement) -> some View {
        switch arrangement {
        case .stacked:
            VStack(spacing: spacing) {
                connectedCard
                playersCard
                testerCard
                keysCard
                tableCard
            }
        case .columns:
            HStack(alignment: .top, spacing: spacing) {
                VStack(spacing: spacing) {
                    if layout == .phoneLandscape {
                        testerCard
                        connectedCard
                        playersCard
                    } else {
                        connectedCard
                        playersCard
                        testerCard
                    }
                }
                .frame(maxWidth: .infinity)
                VStack(spacing: spacing) {
                    keysCard
                    tableCard
                }
                .frame(maxWidth: .infinity)
            }
        case .padUpright:
            VStack(spacing: spacing) {
                HStack(alignment: .top, spacing: spacing) {
                    connectedCard.frame(maxWidth: .infinity)
                    testerCard
                }
                playersCard
                keysCard
                tableCard
            }
        }
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 12) {
            if layout == .phoneLandscape {
                // A phone on its side has height for little: title and summary share one line.
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text("Controllers").font(BCDStyle.title(24))
                    Text(summary).font(BCDStyle.body(13)).foregroundStyle(BCDStyle.textSoft)
                        .lineLimit(1).minimumScaleFactor(0.85)
                }
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Controllers").font(BCDStyle.title(roomy ? 34 : 28))
                    Text(summary)
                        .font(BCDStyle.body(roomy ? 15 : 13))
                        .foregroundStyle(BCDStyle.textSoft)
                        .lineLimit(1).minimumScaleFactor(0.85)
                }
            }
            Spacer(minLength: 8)
            if let close {
                Button("Done", action: close)
                    .buttonStyle(.plain)
                    .font(BCDStyle.label(17))
                    .foregroundStyle(BCDStyle.lavender)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .bcdFocusable("controllers.done", cornerRadius: 12, action: close)
            }
        }
        .padding(.horizontal, layout.gutter)
        .padding(.top, layout == .phoneLandscape ? 4 : 14)
        .padding(.bottom, layout == .phoneLandscape ? 4 : 8)
    }

    private var summary: String {
        guard hub.isOn else { return "Not reading controllers" }
        let count = hub.pads.count
        let pads = count == 0 ? "No controller" : count == 1 ? "1 controller" : "\(count) controllers"
        return "\(pads) · XInput \(hub.xinput ? "on" : "off") · keys \(hub.mapping ? "on" : "off")"
    }

    // MARK: Connected

    private var connectedCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            BCDEyebrow(text: "Connected")
            if !hub.isOn {
                BCDPadNotice(symbol: "pause.circle", title: "Not reading controllers", roomy: roomy, text:
                       "Turn it on below to see and test them. Games still get them through XInput.")
            } else if hub.pads.isEmpty {
                BCDPadNotice(symbol: "gamecontroller", title: "No controller yet", roomy: roomy, text:
                       "Pair one in the device's Bluetooth settings. It shows here as soon as it connects.")
            } else {
                ForEach(hub.pads) { padRow($0) }
            }
            Rectangle().fill(BCDStyle.stroke).frame(height: 1)
            switchRow("controllers.watch", "Read controllers here",
                      note: "For this screen, the status chips and the keys. XInput does not depend on it.",
                      isOn: hub.isOn) { hub.setOn($0) }
        }
        .bcdCard(padding: cardPadding)
    }

    private func padRow(_ pad: BCDPad) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "gamecontroller.fill")
                .font(.system(size: roomy ? 20 : 17, weight: .semibold))
                .foregroundStyle(BCDStyle.lavender)
                .frame(width: roomy ? 46 : 40, height: roomy ? 46 : 40)
                .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 11, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(pad.name).font(BCDStyle.label(roomy ? 17 : 15)).lineLimit(roomy ? 2 : 1).minimumScaleFactor(0.8)
                    .fixedSize(horizontal: false, vertical: roomy)
                Text(padDetail(pad)).font(BCDStyle.body(roomy ? 14 : 12)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
            }
            Spacer(minLength: 6)
            if let battery = pad.battery { batteryLabel(battery, charging: pad.charging) }
        }
        .accessibilityElement(children: .combine)
    }

    private func padDetail(_ pad: BCDPad) -> String {
        guard pad.gamepad else { return "Too few controls to test or map" }
        switch pad.family {
        case .playStation: return "PlayStation buttons"
        case .xbox: return "Xbox buttons"
        case .other: return "Standard buttons"
        }
    }

    private func batteryLabel(_ level: Int, charging: Bool) -> some View {
        let symbol = charging ? "battery.100.bolt"
            : level > 85 ? "battery.100" : level > 60 ? "battery.75" : level > 35 ? "battery.50"
            : level > 10 ? "battery.25" : "battery.0"
        return HStack(spacing: 5) {
            Image(systemName: symbol)
            Text("\(level)%")
        }
        .font(BCDStyle.label(roomy ? 14 : 12))
        .foregroundStyle(level <= 15 && !charging ? BCDStyle.alert : BCDStyle.textSoft)
        .accessibilityLabel(charging ? "Battery \(level) percent, charging" : "Battery \(level) percent")
    }

    // MARK: Keyboard and mouse

    private var keysCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            BCDEyebrow(text: "Keyboard and mouse")
            switchRow("controllers.mapping", "Controller as keyboard and mouse",
                      note: "For Windows games that only read a keyboard and mouse: the sticks type keys, "
                          + "the triggers click.",
                      isOn: hub.mapping) { hub.setMapping($0) }
            xinputLine
            if hub.mapping && hub.xinput {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(BCDPadParts.amber)
                    Text("XInput is on as well: a game that reads controllers gets each press twice, once "
                         + "from the controller and once as a key.")
                        .font(BCDStyle.body(roomy ? 15 : 13))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(BCDPadParts.amber.opacity(0.12), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(BCDPadParts.amber.opacity(0.35), lineWidth: 1))
            }
        }
        .bcdCard(padding: cardPadding)
    }

    private var xinputLine: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(hub.xinput ? BCDStyle.mint : BCDPadParts.amber).frame(width: 8, height: 8).padding(.top, 6)
            VStack(alignment: .leading, spacing: 2) {
                Text(hub.xinput ? "XInput is on" : "XInput is off").font(BCDStyle.label(roomy ? 16 : 14))
                Text((hub.xinput
                      ? "Games that read controllers see a paired one as an Xbox controller; keep the keys off for them. "
                      : "Games get no controller from BCD, so the keys are the way to play with one. ")
                     + "Settings › Controls changes it; it applies when BCD starts.")
                    .font(BCDStyle.body(roomy ? 14 : 12))
                    .foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Parts

    private func switchRow(_ id: String, _ title: String, note: String?, isOn: Bool,
                           set: @escaping (Bool) -> Void) -> some View {
        Toggle(isOn: Binding(get: { isOn }, set: set)) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(BCDStyle.label(roomy ? 17 : 16))
                if let note {
                    Text(note).font(BCDStyle.body(roomy ? 14 : 13)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .tint(BCDStyle.violet)
        .padding(.vertical, 2)
        .bcdFocusable(id, cornerRadius: 12) { set(!isOn) }
        .id(id)
    }
}

/// The live tester. It observes only the tester's values, so nothing else redraws with them.
private struct BCDPadTesterCard: View {
    @ObservedObject var tester: BCDPadTester
    /// A controller that can be tested is connected and the hub reads controllers.
    let ready: Bool
    let on: Bool
    let family: BCDPadFamily
    let roomy: Bool
    let well: CGFloat
    let padding: CGFloat

    private var reading: BCDPadReading { tester.reading }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                BCDEyebrow(text: "Test")
                Spacer()
                if ready && tester.live {
                    HStack(spacing: 6) {
                        Circle().fill(BCDStyle.mint).frame(width: 7, height: 7)
                        Text("Live").font(BCDStyle.label(12))
                    }
                } else {
                    Text(on ? "Waiting" : "Off").font(BCDStyle.label(12)).foregroundStyle(BCDStyle.textSoft)
                }
            }
            if ready {
                HStack(alignment: .top, spacing: roomy ? 22 : 14) {
                    stickWell(.leftStick, x: reading.leftX, y: reading.leftY)
                    triggerBar(.leftTrigger, value: reading.leftTrigger)
                    triggerBar(.rightTrigger, value: reading.rightTrigger)
                    stickWell(.rightStick, x: reading.rightX, y: reading.rightY)
                }
                .frame(maxWidth: .infinity)
                lastPressed
            } else if !on {
                BCDPadNotice(symbol: "pause.circle", title: "The tester is off", roomy: roomy,
                             text: "It works while BCD reads controllers.")
            } else {
                BCDPadNotice(symbol: "hand.tap", title: "Nothing to test yet", roomy: roomy,
                             text: "Connect a controller, then press its buttons here.")
            }
        }
        .bcdCard(padding: padding)
    }

    private func stickWell(_ control: BCDPadControl, x: Float, y: Float) -> some View {
        let size = well, dot = size * 0.2
        let reach = (size - dot) / 2
        var dx = CGFloat(x.isFinite ? x : 0), dy = CGFloat(y.isFinite ? y : 0)
        let length = (dx * dx + dy * dy).squareRoot()
        if length > 1 { dx /= length; dy /= length }
        let away = reading.held.contains(control)
        let rest = 2 * reach * CGFloat(BCDPadRules.deadZone)
        return VStack(spacing: 5) {
            ZStack {
                Circle().fill(BCDStyle.ground.opacity(0.7))
                Circle().strokeBorder(away ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.stroke), lineWidth: away ? 2 : 1)
                Path { path in
                    path.move(to: CGPoint(x: size / 2, y: dot / 2)); path.addLine(to: CGPoint(x: size / 2, y: size - dot / 2))
                    path.move(to: CGPoint(x: dot / 2, y: size / 2)); path.addLine(to: CGPoint(x: size - dot / 2, y: size / 2))
                }
                .stroke(BCDStyle.stroke.opacity(0.7), lineWidth: 1)
                Circle()
                    .strokeBorder(BCDStyle.textSoft.opacity(0.35), style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .frame(width: rest, height: rest)
                Circle()
                    .fill(away ? BCDStyle.fuchsia : BCDStyle.lavender)
                    .frame(width: dot, height: dot)
                    .shadow(color: away ? BCDStyle.fuchsia.opacity(0.6) : .clear, radius: 6)
                    .offset(x: dx * reach, y: -dy * reach)
            }
            .frame(width: size, height: size)
            Text(control.title(family)).font(BCDStyle.label(roomy ? 14 : 12)).lineLimit(1)
            Text("\(BCDPadRules.number(x))  \(BCDPadRules.number(y))")
                .font(.system(size: roomy ? 12 : 11, weight: .medium, design: .monospaced))
                .foregroundStyle(BCDStyle.textSoft)
                .lineLimit(1).fixedSize()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(control.title(family)): x \(BCDPadRules.number(x)), y \(BCDPadRules.number(y))")
    }

    private func triggerBar(_ control: BCDPadControl, value: Float) -> some View {
        let height = well, width: CGFloat = roomy ? 22 : 18
        let fraction = CGFloat(max(0, min(1, value.isFinite ? value : 0)))
        let down = reading.held.contains(control)
        return VStack(spacing: 5) {
            ZStack(alignment: .bottom) {
                Capsule().fill(BCDStyle.ground.opacity(0.7))
                if fraction > 0 {
                    Capsule()
                        .fill(down ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(BCDStyle.lavender.opacity(0.75)))
                        .frame(height: max(width, height * fraction))
                }
                // Where the trigger counts as a mouse button held down.
                Rectangle()
                    .fill(BCDStyle.text.opacity(0.55))
                    .frame(width: width - 6, height: 1.5)
                    .offset(y: -height * CGFloat(BCDPadRules.triggerThreshold))
            }
            .frame(width: width, height: height)
            .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
            Text(control.mark(family)).font(BCDStyle.label(roomy ? 14 : 12)).lineLimit(1).fixedSize()
            Text(BCDPadRules.number(value))
                .font(.system(size: roomy ? 12 : 11, weight: .medium, design: .monospaced))
                .foregroundStyle(BCDStyle.textSoft)
                .lineLimit(1).fixedSize()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(control.title(family)): \(BCDPadRules.number(value))")
    }

    private var lastPressed: some View {
        HStack(spacing: 10) {
            Text("LAST")
                .font(.system(size: 10, weight: .heavy, design: .rounded)).tracking(1.6)
                .foregroundStyle(BCDStyle.textSoft)
            if let control = reading.lastControl { BCDPadBadge(control: control, family: family) }
            Text(reading.last ?? "Press a button or move a stick")
                .font(BCDStyle.label(roomy ? 16 : 14))
                .foregroundStyle(reading.last == nil ? BCDStyle.textSoft : BCDStyle.text)
                .lineLimit(1).minimumScaleFactor(0.75)
            Spacer(minLength: 4)
            if reading.presses > 0 {
                Text(reading.presses == 1 ? "1 press" : "\(reading.presses) presses")
                    .font(BCDStyle.body(12)).foregroundStyle(BCDStyle.textSoft).lineLimit(1).fixedSize()
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// What each control sends. It observes the tester to light the rows held now.
private struct BCDPadTableCard: View {
    @ObservedObject var tester: BCDPadTester
    let mapping: Bool
    let family: BCDPadFamily
    let roomy: Bool
    let padding: CGFloat

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            BCDEyebrow(text: "What each control sends")
            Text(mapping
                 ? "Keys reach a game while it runs and BCD's menu is closed. A stick held between two "
                   + "directions holds both keys."
                 : "Sent only while the controller is a keyboard and mouse.")
                .font(BCDStyle.body(roomy ? 14 : 12)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
            // Two columns where both fit side by side (an iPad, a phone on its side), else one.
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: roomy ? 24 : 16) {
                    tableColumn(Array(BCDPadRules.tableOrder.prefix(8)))
                    tableColumn(Array(BCDPadRules.tableOrder.dropFirst(8)))
                }
                tableColumn(BCDPadRules.tableOrder)
            }
            .opacity(mapping ? 1 : 0.6)
            Text("For a single game, its own keyboard and mouse mode (on its page or in the in\u{2011}game "
                 + "menu) fits better: each of its buttons can be changed.")
                .font(BCDStyle.body(roomy ? 14 : 12)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
        .bcdCard(padding: padding)
    }

    private func tableColumn(_ controls: [BCDPadControl]) -> some View {
        VStack(spacing: 3) {
            ForEach(controls, id: \.self) { tableRow($0) }
        }
        .frame(maxWidth: .infinity, alignment: .top)
    }

    private func tableRow(_ control: BCDPadControl) -> some View {
        let lit = tester.live && tester.reading.held.contains(control)
        return HStack(spacing: 10) {
            BCDPadBadge(control: control, family: family)
            VStack(alignment: .leading, spacing: 1) {
                Text(control.title(family)).font(BCDStyle.label(roomy ? 15 : 14)).lineLimit(1)
                Text(alias(control)).font(BCDStyle.body(roomy ? 12 : 11)).foregroundStyle(BCDStyle.textSoft).lineLimit(1)
            }
            Spacer(minLength: 8)
            HStack(spacing: 4) {
                if BCDPadRules.triggerTargets[control] != nil {
                    keyCap(control == .leftTrigger ? "Right click" : "Left click", mouse: true)
                } else {
                    ForEach(BCDPadRules.sends(control) ?? [], id: \.self) { keyCap($0, mouse: false) }
                }
            }
        }
        .padding(.horizontal, 8).padding(.vertical, roomy ? 6 : 4)
        .background(lit ? BCDStyle.surfaceRaised : Color.clear, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous)
            .strokeBorder(lit ? AnyShapeStyle(BCDStyle.glow) : AnyShapeStyle(Color.clear), lineWidth: 1.5))
        .accessibilityElement(children: .combine)
    }

    /// The other family's name under a control's, or a stick's eight directions.
    private func alias(_ control: BCDPadControl) -> String {
        switch control {
        case .leftStick, .rightStick, .dpad: return "8 directions"
        default:
            return family == .playStation ? "\(control.title(.xbox)) on Xbox" : "\(control.title(.playStation)) on PlayStation"
        }
    }

    private func keyCap(_ text: String, mouse: Bool) -> some View {
        HStack(spacing: 4) {
            if mouse { Image(systemName: "computermouse.fill").font(.system(size: 11, weight: .semibold)) }
            Text(text).lineLimit(1).fixedSize()
        }
        .font(.system(size: roomy ? 13 : 12, weight: .bold, design: .rounded))
        .padding(.horizontal, text.count > 1 || mouse ? 7 : 0)
        .frame(minWidth: roomy ? 28 : 24, minHeight: roomy ? 28 : 24)
        .background(BCDStyle.ground.opacity(0.65), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(BCDStyle.stroke, lineWidth: 1))
    }
}

private enum BCDPadParts {
    static var amber: Color { BCDStyle.amber }
}

/// A control's mark, as the bottom bar's hints draw a button.
private struct BCDPadBadge: View {
    let control: BCDPadControl
    let family: BCDPadFamily

    var body: some View {
        let mark = control.mark(family)
        return Text(mark)
            .font(.system(size: mark.count > 1 ? 10 : 12, weight: .heavy, design: .rounded))
            .foregroundStyle(BCDStyle.ground)
            .frame(minWidth: 24, minHeight: 24)
            .padding(.horizontal, mark.count > 1 ? 4 : 0)
            .background(BCDStyle.text, in: Capsule())
            .accessibilityHidden(true)
    }
}

/// A card's message when there is nothing to show.
private struct BCDPadNotice: View {
    let symbol: String
    let title: String
    let roomy: Bool
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(BCDStyle.lavender)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(BCDStyle.label(roomy ? 17 : 15))
                Text(text).font(BCDStyle.body(roomy ? 14 : 13)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

private extension BCDPadControl {
    /// A short mark for a badge: the face buttons' letters or shapes, the shoulders' and
    /// triggers' abbreviations.
    func mark(_ family: BCDPadFamily) -> String {
        let ps = family == .playStation
        switch self {
        case .a: return ps ? "✕" : "A"
        case .b: return ps ? "○" : "B"
        case .x: return ps ? "□" : "X"
        case .y: return ps ? "△" : "Y"
        case .leftShoulder: return ps ? "L1" : "LB"
        case .rightShoulder: return ps ? "R1" : "RB"
        case .leftTrigger: return ps ? "L2" : "LT"
        case .rightTrigger: return ps ? "R2" : "RT"
        case .leftStickClick: return ps ? "L3" : "LS"
        case .rightStickClick: return ps ? "R3" : "RS"
        case .menu: return "≡"
        case .options: return "⧉"
        case .home: return "⌂"
        case .dpad: return "✛"
        case .leftStick: return "L"
        case .rightStick: return "R"
        }
    }
}
