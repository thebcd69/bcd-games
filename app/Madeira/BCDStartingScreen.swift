// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation
import SwiftUI

// The starting screen: from Play until the game's first frame. The game's hero art, its logo or
// title, one progress line with the step in a word, the notes that do not stop the start, and
// how to open the game menu. A BCD Dock start also tells what Steam is doing, can show the
// Windows desktop behind it, and says so when it stops. The screen is gone, tree and timer, the
// moment the game draws (BCDSessionLive.swift decides when; the classic rules are unchanged).

// MARK: - What it shows

/// The step a start is at, in a word.
enum BCDStartStep: String, CaseIterable {
    case starting, installing, connecting, signingIn, checking, preparing, waiting, launching, loading, stopped

    var word: String {
        switch self {
        case .starting: return "Starting"
        case .installing: return "Installing"
        case .connecting: return "Connecting"
        case .signingIn: return "Signing in"
        case .checking: return "Checking"
        case .preparing: return "Preparing"
        case .waiting: return "Waiting"
        case .launching: return "Launching"
        case .loading: return "Loading"
        case .stopped: return "Stopped"
        }
    }
}

struct BCDStartNote: Identifiable, Equatable {
    var id: String
    var text: String
    var tone: BCDSessionTone = .soft
}

/// Everything the starting screen shows.
struct BCDStartingInfo: Equatable {
    var game: BCDGame
    var step: BCDStartStep = .starting
    /// The steps this kind of start goes through, in order (the progress line's segments).
    var steps: [BCDStartStep] = [.starting, .loading]
    var seconds = 0
    var notes: [BCDStartNote] = []
    /// The start stopped: what happened. The screen stays until the session is closed.
    var failure: String?
    /// How to open the game menu.
    var tip = ""
    var canShowDesktop = false
    var canShowGame = false
    var canClose = false
    var canSkip = false
    var logShown = false
    var logLines: [BCDLogLine] = []

    /// How far along the line is filled: the steps passed, and half of the current one. A step
    /// that is not a segment of its own sits where it happens: Steam's waits and the last loading
    /// at the end, the program's preparation with the licence, the one-time installs first.
    var progress: Double {
        guard !steps.isEmpty else { return 0 }
        func at(_ wanted: BCDStartStep, else fallback: Int) -> Int { steps.firstIndex(of: wanted) ?? fallback }
        let index: Int
        switch step {
        case _ where steps.contains(step): index = at(step, else: 0)
        case .waiting, .loading, .stopped: index = at(.launching, else: steps.count - 1)
        case .preparing: index = at(.checking, else: steps.count - 1)
        default: index = 0
        }
        return min(1, (Double(index) + 0.5) / Double(steps.count))
    }
}

/// The screen's values, refreshed by the live side once a second while the screen is up.
@MainActor final class BCDStartingFeed: ObservableObject {
    @Published var info: BCDStartingInfo
    init(_ info: BCDStartingInfo) { self.info = info }
}

enum BCDStartingAction: Equatable {
    case toggleLog, showDesktop, showGame, close, skip
}

// MARK: - A BCD Dock start, in BCD's words

/// What a BCD Dock start is doing, read from the Dock host's report fields (the same fields and
/// the same order of precedence as the classic starting screen), as a step and notes.
enum BCDDockStart {
    /// Seconds without the host's first field before the screen says it is slow.
    static let slowAfter = 30.0
    static let steps: [BCDStartStep] = [.starting, .connecting, .signingIn, .checking, .launching]

    /// `installers`: this start runs the game's one-time installs first; `installerProgress`:
    /// their progress in words; `installsFinished`: they ended; `waited`: seconds since the host
    /// could start; `restartHint`: a start that runs only the installs says what comes next.
    static func read(_ fields: [String: String], installers: Bool, installerProgress: String?,
                     installsFinished: Bool, waited: Double, restartHint: String? = nil) -> (step: BCDStartStep, notes: [BCDStartNote]) {
        func note(_ id: String, _ text: String, _ tone: BCDSessionTone = .soft) -> [BCDStartNote] { [BCDStartNote(id: id, text: text, tone: tone)] }
        if fields["launch-option-missing"] != nil || fields["launch-option-invalid"] != nil {
            return (.launching, note("launch-option", "Steam doesn't accept this game's launch option.", .warm))
        }
        if fields["launch-update-wait"] != nil && fields["launch-update-ready"] == nil {
            return (.waiting, note("update", "Steam is installing content this game needs. The game starts right after."))
        }
        if fields["launch-session-wait"] != nil && fields["launch-client-error"] == "35" {
            return (.waiting, note("session", "Steam still counts this account as playing somewhere else. BCD waits up to "
                                   + "3 minutes for Steam to end that session.", .warm))
        }
        if fields["launch-config-wait"] != nil && (fields["launch-client-error"] == "22" || fields["launch-client-error"] == "23") {
            return (.waiting, note("config", "Steam is still loading this game's settings."))
        }
        if fields["launch-client-error"] == "0" {
            return (.launching, note("window", "Steam started the game. Waiting for its window."))
        }
        if ["ceg-scm", "ceg-request-busy", "ceg-request"].contains(where: { fields[$0] != nil }) && fields["ceg-result"] == nil {
            return (.preparing, note("ceg", "Steam is preparing this game's program."))
        }
        if fields["session-offline-listed"] == "1" {
            return (.launching, note("offline", "Offline: Steam's saved data has this game's license."))
        }
        if fields["session-offline-logon-result"] == "1" || fields["session-offline-logon-retry"] == "1" {
            return (.checking, note("offline", "Offline: signed in with Steam's saved sign-in."))
        }
        if fields["session-offline-requested"] != nil || fields["session-offline-fallback"] != nil {
            return (.signingIn, note("offline", "No connection. Steam is asked for an offline sign-in."))
        }
        if fields["session-requested-app-listed"] == "1" { return (.launching, []) }
        if fields["session-authenticated-online"] == "1" { return (.checking, []) }
        if fields["session-native-token-submitted"] != nil || fields["session-logon-start-result"] != nil { return (.signingIn, []) }
        if fields["probe-start-bits"] != nil { return (.connecting, []) }
        // Before the host's first field: the one-time installs, then the host.
        let slow = waited >= slowAfter ? note("slow", "BCD Dock is taking longer than usual to start.", .warm) : []
        guard installers else { return (.starting, slow) }
        guard installsFinished else {
            return (.installing, note("installs", installerProgress ?? "This game's one-time installs are running."))
        }
        let done = installerProgress ?? "The one-time installs are done."
        if let restartHint { return (.installing, note("installs", done) + note("restart", restartHint, .warm)) }
        return (.starting, note("installs", done) + slow)
    }
}

// MARK: - The screen

struct BCDStartingScreen: View {
    @ObservedObject var feed: BCDStartingFeed
    let insets: EdgeInsets
    let act: (BCDStartingAction) -> Void
    @Environment(\.horizontalSizeClass) private var horizontal
    @Environment(\.verticalSizeClass) private var vertical

    var body: some View {
        let info = feed.info
        GeometryReader { geo in
            let wide = geo.size.width > geo.size.height
            let pad = horizontal == .regular && vertical == .regular
            ZStack(alignment: .bottomLeading) {
                backdrop(info, wide: wide)
                if wide {
                    HStack(alignment: .bottom, spacing: 24) {
                        column(info, pad: pad).frame(maxWidth: pad ? 520 : 440, alignment: .leading)
                        Spacer(minLength: 0)
                        if info.logShown {
                            log(info).frame(width: min(pad ? 460 : 380, geo.size.width * 0.42))
                                .frame(maxHeight: min(pad ? 420 : 280, geo.size.height * 0.62))
                        }
                    }
                    .padding(.leading, insets.leading + (pad ? 48 : 28))
                    .padding(.trailing, insets.trailing + (pad ? 40 : 20))
                    .padding(.bottom, insets.bottom + (pad ? 40 : 18))
                    .padding(.top, insets.top + 16)
                } else {
                    VStack(alignment: .leading, spacing: 16) {
                        Spacer(minLength: 0)
                        if info.logShown { log(info).frame(maxHeight: min(260, geo.size.height * 0.32)) }
                        column(info, pad: pad)
                    }
                    .padding(.horizontal, pad ? 48 : 22)
                    .padding(.bottom, insets.bottom + (pad ? 40 : 20))
                    .padding(.top, insets.top + 16)
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
        }
        .background(Color.black)
        .accessibilityElement(children: .contain)
    }

    /// The hero art over the whole screen, darkened towards the words.
    private func backdrop(_ info: BCDStartingInfo, wide: Bool) -> some View {
        BCDGameArt(game: info.game, kind: .hero, showTitle: false)
            .overlay(LinearGradient(colors: [.black.opacity(0.25), .black.opacity(0.55), .black.opacity(0.92)],
                                    startPoint: .top, endPoint: .bottom))
            .overlay(LinearGradient(colors: [.black.opacity(wide ? 0.55 : 0.2), .clear],
                                    startPoint: .leading, endPoint: .trailing))
            .accessibilityHidden(true)
    }

    private func column(_ info: BCDStartingInfo, pad: Bool) -> some View {
        VStack(alignment: .leading, spacing: pad ? 16 : 11) {
            BCDGameLogo(game: info.game, titleSize: pad ? 40 : 30, maxWidth: pad ? 420 : 300, maxHeight: pad ? 120 : 84)
            stepLine(info, pad: pad)
            if let failure = info.failure {
                Text(failure)
                    .font(BCDStyle.body(pad ? 16 : 14)).foregroundStyle(BCDStyle.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(info.notes) { note in
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Circle().fill(note.tone == .soft ? BCDStyle.lavender : note.tone.color).frame(width: 6, height: 6)
                        .alignmentGuide(.firstTextBaseline) { $0[.bottom] - 1 }
                    Text(note.text)
                        .font(BCDStyle.body(pad ? 15 : 13))
                        .foregroundStyle(note.tone == .soft ? BCDStyle.text.opacity(0.85) : note.tone.color)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if info.failure == nil && !info.tip.isEmpty {
                Label(info.tip, systemImage: "gamecontroller")
                    .font(BCDStyle.body(pad ? 14 : 12)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
            }
            buttons(info)
        }
    }

    private func stepLine(_ info: BCDStartingInfo, pad: Bool) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline) {
                Text(info.step.word)
                    .font(BCDStyle.heading(pad ? 22 : 18))
                    .foregroundStyle(info.step == .stopped ? BCDStyle.alert : BCDStyle.text)
                Spacer(minLength: 12)
                Text(verbatim: String(format: "%d:%02d", info.seconds / 60, info.seconds % 60))
                    .font(.system(size: pad ? 14 : 12, weight: .semibold, design: .rounded).monospacedDigit())
                    .foregroundStyle(BCDStyle.textSoft)
                    .accessibilityLabel("\(info.seconds) seconds")
            }
            BCDStepLine(steps: info.steps.count, progress: info.progress, stopped: info.step == .stopped)
                .frame(height: 5)
        }
        .frame(maxWidth: pad ? 460 : 380)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func buttons(_ info: BCDStartingInfo) -> some View {
        let any = info.canClose || info.canShowDesktop || info.canShowGame || info.canSkip
        HStack(spacing: 8) {
            if info.canClose { pill("Close session", "xmark", primary: true) { act(.close) } }
            if info.canShowDesktop { pill("Show desktop", "macwindow") { act(.showDesktop) } }
            if info.canShowGame { pill("Show the game", "play.rectangle") { act(.showGame) } }
            pill(info.logShown ? "Hide log" : "Live log", "text.alignleft", on: info.logShown) { act(.toggleLog) }
            if info.canSkip { pill("Skip", "forward.end") { act(.skip) } }
        }
        .padding(.top, any ? 4 : 2)
    }

    private func pill(_ title: String, _ symbol: String, primary: Bool = false, on: Bool = false,
                      action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Image(systemName: symbol).font(.system(size: 13, weight: .bold))
                Text(title).font(BCDStyle.label(13)).lineLimit(1)
            }
            .foregroundStyle(primary || on ? BCDStyle.onAccent : .white)
            .padding(.horizontal, 13).frame(minHeight: 36)
            .background {
                if primary || on { Capsule().fill(BCDStyle.glow) } else { Capsule().fill(Color.white.opacity(0.14)) }
            }
            .overlay(Capsule().strokeBorder(Color.white.opacity(primary || on ? 0 : 0.18), lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }

    private func log(_ info: BCDStartingInfo) -> some View {
        BCDLogLines(lines: info.logLines, size: 9)
            .padding(10)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
            .frame(minHeight: 120)
            .background(Color.black.opacity(0.7), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .accessibilityLabel("Live log")
    }
}

/// The progress line: one segment per step, lit up to the current one. Still: it moves only
/// when the step changes.
struct BCDStepLine: View {
    let steps: Int
    let progress: Double
    let stopped: Bool

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.white.opacity(0.16))
                Capsule()
                    .fill(stopped ? AnyShapeStyle(BCDStyle.alert) : AnyShapeStyle(BCDStyle.glow))
                    .frame(width: max(6, proxy.size.width * progress))
                // The steps' borders.
                ForEach(1..<max(1, steps), id: \.self) { i in
                    Rectangle().fill(Color.black.opacity(0.55)).frame(width: 2)
                        .offset(x: proxy.size.width * Double(i) / Double(steps) - 1)
                }
            }
        }
        .animation(.easeOut(duration: 0.25), value: progress)
        .accessibilityHidden(true)
    }
}
