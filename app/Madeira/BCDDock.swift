// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › BCD Dock: what Dock is, whether it is ready, Valve's client files it needs
// (download, size, progress) and each game's one-time installs, with their reset. The
// section names no engine type: it reads and asks through BCDDockBackend, which
// BCDDockLive.swift carries out on the app's Dock model and the preview app on sample data
// (docs/BCD-UI-ARCHITECTURE.md).

// MARK: - What the section shows and asks for

/// A request a game's next Dock start carries out, before Valve's client starts.
enum BCDDockRequest: String, Equatable {
    /// Mark the game's one-time install programs as done without running them.
    case record
    /// Take out what they installed (into a backup), then run them again.
    case reset
    /// The same, and remove the Rockstar sign-in too.
    case resetAndSignOut
}

/// A Steam game Valve's client installed on this device, which Dock can start.
struct BCDDockGame: Identifiable, Equatable {
    /// The Steam App ID.
    var id: Int
    var name: String
    /// Steam's install record says the install is complete.
    var installed: Bool
}

/// An installed Steam game whose install script has one-time programs.
struct BCDDockInstallGame: Identifiable, Equatable {
    /// The Steam App ID.
    var id: Int
    var name: String
    var programs: Int
    /// The next Dock start runs them (else it skips them).
    var runNext: Bool
    var pending: BCDDockRequest?
}

/// Valve's Steam client files Dock needs.
struct BCDDockComponents: Equatable {
    var installed = false
    /// How much the download is.
    var downloadBytes: Int64 = 0
    var working = false
    /// What the download is doing, in words, and as a step of `steps` when known.
    var step: String?
    var stepIndex: Int?
    var steps = 0
    var error: String?
    /// Night round 2026-11-11 (A1 S7): the client files are a Valve build the Dock host does not support (the first 12 hex digits of its SHA-256);
    /// nil for a supported build. Settings then offers "Replace client components".
    var unsupportedBuild: String?
}

struct BCDDockSectionState: Equatable {
    /// This copy of BCD was built with Dock.
    var inBuild = true
    /// Dock is not switched off in the advanced settings.
    var switchedOn = true
    /// The Steam account Dock signs in with; nil when nobody is signed in.
    var accountName: String?
    var components = BCDDockComponents()
    /// Each game's one-time installs can be chosen (else every start runs what is pending).
    var installsChoice = true
    var installGames: [BCDDockInstallGame] = []
    /// The games Valve's client installed on this device.
    var games: [BCDDockGame] = []
    /// Dock starts use the smaller JIT pool (512 MB) until BCD closes.
    var compactPool = false
    /// How the last Dock start went, in words.
    var lastResult: String?
}

@MainActor protocol BCDDockBackend: ObservableObject {
    var state: BCDDockSectionState { get }
    func refresh()
    func downloadComponents()
    /// Night round 2026-11-11 (A1 S7): renames the client folder of an unsupported build aside and installs the pinned one.
    func replaceComponents()
    /// Starts the game through Dock, as the rest of the interface starts a Steam game.
    func start(_ appID: Int)
    func setCompactPool(_ on: Bool)
    func setRunsInstalls(_ appID: Int, _ run: Bool)
    /// Saves a request for the game's next Dock start; nil cancels the saved one.
    func setRequest(_ appID: Int, _ request: BCDDockRequest?)
}

// MARK: - The section

/// Settings › BCD Dock. While a question shows, B closes it first. A host that scrolls and
/// runs the buttons itself (Settings) passes `scrolls: false` and `backHook`; on its own,
/// `back` is what B does here. `signIn` opens the Steam sign-in (Accounts' sign-in page).
struct BCDDockSection<Backend: BCDDockBackend>: View {
    @ObservedObject var backend: Backend
    var showsTitle = true
    var scrolls = true
    var back: (() -> Void)? = nil
    var backHook: BCDSectionKit.BackHook? = nil
    var signIn: (() -> Void)? = nil
    @EnvironmentObject private var focus: BCDFocus
    /// The question on screen: record or reset, for a game.
    @State private var asking: Question?

    private struct Question: Equatable {
        enum Kind { case record, reset }
        let kind: Kind
        let appID: Int
    }

    private static var prefix: String { "settings.dock." }
    private var state: BCDDockSectionState { backend.state }

    var body: some View {
        BCDSectionKit.Page(title: "BCD Dock", subtitle: "How BCD starts your Steam games.",
                           showsTitle: showsTitle, scrolls: scrolls, focusPrefix: Self.prefix) {
            BCDSectionKit.Columns {
                aboutCard
                componentsCard
                gamesCard
            } right: {
                installsCard
                if let result = state.lastResult {
                    BCDSectionKit.Card(eyebrow: "Last Dock start", symbol: "clock.arrow.circlepath") {
                        Text(result).bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
        .modifier(BCDSectionKit.Buttons(focus: focus, key: asking, inner: closeQuestion, back: back, hook: backHook))
        .onAppear { backend.refresh() }
    }

    /// B inside the section: the question closes. False: none was open.
    private func closeQuestion() -> Bool {
        guard let question = asking else { return false }
        asking = nil
        focusButton(question.kind == .record ? "record" : "reset", question.appID)
        return true
    }

    private func focusButton(_ name: String, _ appID: Int) {
        if focus.controllerActive { focus.focused = Self.prefix + "installs.\(appID).\(name)" }
    }

    // MARK: What Dock is, and whether it is ready

    private var aboutCard: some View {
        BCDSectionKit.Card(accent: !ready) {
            readiness
            if state.inBuild && state.switchedOn && state.accountName == nil, let signIn {
                BCDSectionKit.ActionButton(title: "Sign in to Steam", symbol: "person.crop.circle.badge.plus", kind: .primary,
                                           fill: true, id: Self.prefix + "signin", action: signIn)
            }
            Text("BCD Dock starts your installed Steam games with Valve's own Steam client, which runs out of sight inside BCD instead of opening its desktop window. The client signs in as you and checks that each game is yours, so Steam alone decides whether a game may start.")
                .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// Dock can start a game now.
    private var ready: Bool {
        state.inBuild && state.switchedOn && state.components.installed && state.accountName != nil
    }

    @ViewBuilder private var readiness: some View {
        if !state.inBuild {
            BCDSectionKit.Headline(text: "Not in this copy of BCD", symbol: "shippingbox", tone: .soft,
                                   detail: "This build of BCD came without BCD Dock.")
        } else if !state.switchedOn {
            BCDSectionKit.Headline(text: "Switched off", symbol: "pause.circle", tone: .soft,
                                   detail: "BCD Dock is turned off in the advanced settings.")
        } else if state.components.working {
            BCDSectionKit.Headline(text: "Getting Valve's client files", symbol: "arrow.down.circle", tone: .info,
                                   detail: "Dock is ready as soon as they're in place.")
        } else if !state.components.installed {
            BCDSectionKit.Headline(text: "Needs Valve's client files", symbol: "arrow.down.circle", tone: .warn,
                                   detail: "Download them below once; Dock is ready after that.")
        } else if let name = state.accountName {
            let count = state.games.filter(\.installed).count
            BCDSectionKit.Headline(text: "Ready", symbol: "checkmark.circle.fill", tone: .good,
                                   detail: count == 1 ? "Signs in as \(name). 1 Steam game installed."
                                                      : "Signs in as \(name). \(count) Steam games installed.")
        } else {
            BCDSectionKit.Headline(text: "Sign in to Steam first", symbol: "person.crop.circle.badge.exclamationmark", tone: .warn,
                                   detail: signIn == nil ? "Dock signs in with your Steam account. Sign in under Accounts."
                                                         : "Dock signs in with your Steam account.")
        }
    }

    // MARK: Valve's client files

    @ViewBuilder private var componentsCard: some View {
        if state.inBuild && state.switchedOn {
            let parts = state.components
            BCDSectionKit.Card(eyebrow: "Valve's client files", symbol: "shippingbox") {
                if parts.installed, let build = parts.unsupportedBuild, !parts.working {
                    // Night round 2026-11-11 (A1 S7): a Steam run on the Desktop can replace the client files; Dock refuses any build it has not checked.
                    BCDSectionKit.Note(text: "These client files (build \(build)) are not a build BCD Dock supports, so Dock cannot start games.",
                                       symbol: "exclamationmark.triangle.fill", tone: .bad)
                    Text("Replace them with the pinned build. BCD renames the old folder (nothing is deleted) and keeps your installed games.")
                        .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                    if let error = parts.error {
                        BCDSectionKit.Note(text: error, symbol: "exclamationmark.triangle.fill", tone: .bad)
                    }
                    BCDSectionKit.ActionButton(title: "Replace client components (\(BCDFormat.bytes(parts.downloadBytes)))",
                                               symbol: "arrow.triangle.2.circlepath", kind: .primary, fill: true,
                                               id: Self.prefix + "replace") { backend.replaceComponents() }
                } else if parts.installed {
                    BCDSectionKit.Note(text: "Installed and checked.", symbol: "checkmark.circle.fill", tone: .good)
                    Text("Dock uses only the client build it knows; any other build is refused.")
                        .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft).fixedSize(horizontal: false, vertical: true)
                } else if parts.working {
                    if let index = parts.stepIndex, parts.steps > 0 {
                        BCDSectionKit.StepBar(lit: index, of: parts.steps)
                    }
                    BCDSectionKit.Note(text: parts.step ?? "Starting…", symbol: "arrow.down.circle", tone: .info)
                    Text("You can leave this page; the download carries on.")
                        .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                } else {
                    Text("About \(BCDFormat.bytes(parts.downloadBytes)) from Valve's own update servers, once. Every file is checked against a fixed SHA-256 before BCD uses it.")
                        .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                    if let error = parts.error {
                        BCDSectionKit.Note(text: error, symbol: "exclamationmark.triangle.fill", tone: .bad)
                    }
                    BCDSectionKit.ActionButton(title: parts.error == nil ? "Download (\(BCDFormat.bytes(parts.downloadBytes)))" : "Try again",
                                               symbol: "arrow.down.circle.fill", kind: .primary, fill: true,
                                               id: Self.prefix + "download") { backend.downloadComponents() }
                }
            }
        }
    }

    // MARK: Installed games

    /// The games Valve's client installed here, each started through Dock, and the pool switch.
    @ViewBuilder private var gamesCard: some View {
        if state.inBuild && state.switchedOn {
            BCDSectionKit.Card(eyebrow: "Installed Steam games", symbol: "gamecontroller") {
                if state.games.isEmpty {
                    BCDSectionKit.Note(text: "Valve's client hasn't installed a Steam game on this \(BCDSectionKit.deviceWord) yet.",
                                       symbol: "tray")
                } else {
                    ForEach(state.games) { game in gameRow(game) }
                }
                Divider().overlay(BCDStyle.stroke)
                BCDSectionKit.SwitchRow(title: "Smaller JIT pool (512 MB)",
                                        detail: "Dock starts until BCD closes use a 512 MB JIT pool instead of the usual one, which leaves more memory to the game.",
                                        isOn: state.compactPool, id: Self.prefix + "pool") { backend.setCompactPool($0) }
            }
        }
    }

    private func gameRow(_ game: BCDDockGame) -> some View {
        let startable = game.installed && state.components.installed && state.accountName != nil
        return HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(game.name).bcdFont(.heading, 16).lineLimit(2)
                if !game.installed {
                    Text("Not fully installed").bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            BCDSectionKit.ActionButton(title: "Start", symbol: "play.fill", kind: .outline, id: Self.prefix + "game.\(game.id)",
                                       enabled: startable) { backend.start(game.id) }
        }
    }

    // MARK: One-time installs

    @ViewBuilder private var installsCard: some View {
        if state.inBuild && state.switchedOn {
            BCDSectionKit.Card(eyebrow: "One-time installs", symbol: "square.stack.3d.down.right") {
                Text("Some games ask Steam to run setup programs once, such as runtimes, before their first start. Dock runs the ones not done yet before Valve's client starts, then sets the game to Skip.")
                    .bcdFont(.body, 14).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                if !state.installsChoice {
                    BCDSectionKit.Note(text: "Every Dock start runs whatever is still pending.", symbol: "info.circle")
                } else if state.installGames.isEmpty {
                    BCDSectionKit.Note(text: "None of your installed Steam games has one-time installs.", symbol: "checkmark.circle")
                } else {
                    ForEach(state.installGames) { game in
                        Divider().overlay(BCDStyle.stroke)
                        installRow(game)
                    }
                }
            }
        }
    }

    private func installRow(_ game: BCDDockInstallGame) -> some View {
        let id = Self.prefix + "installs.\(game.id)."
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(game.name).bcdFont(.heading, 16).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(game.programs == 1 ? "1 program" : "\(game.programs) programs")
                    .bcdFont(.body, 13).foregroundStyle(BCDStyle.textSoft)
            }
            BCDSectionKit.Segments(options: [(value: true, title: "Run at next start"), (value: false, title: "Skip")],
                                   selected: game.runNext, idPrefix: id + "run.") { backend.setRunsInstalls(game.id, $0) }
            if let pending = game.pending {
                HStack(alignment: .center, spacing: 8) {
                    BCDSectionKit.Note(text: pendingText(pending), symbol: "clock", tone: .info)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    BCDSectionKit.ActionButton(title: "Cancel", kind: .quiet, id: id + "cancel") {
                        backend.setRequest(game.id, nil)
                        focusButton("reset", game.id)
                    }
                }
            } else if asking == Question(kind: .record, appID: game.id) {
                recordQuestion(game)
            } else if asking == Question(kind: .reset, appID: game.id) {
                resetQuestion(game)
            } else {
                BCDSectionKit.Flow {
                    BCDSectionKit.ActionButton(title: "Record as done…", symbol: "checkmark.circle", kind: .quiet, id: id + "record") {
                        ask(.record, game.id)
                    }
                    BCDSectionKit.ActionButton(title: "Reset…", symbol: "arrow.counterclockwise", kind: .danger, id: id + "reset") {
                        ask(.reset, game.id)
                    }
                }
            }
        }
    }

    private func ask(_ kind: Question.Kind, _ appID: Int) {
        asking = Question(kind: kind, appID: appID)
        if focus.controllerActive { focus.focused = Self.prefix + "installs.\(appID).no" }
    }

    private func pendingText(_ request: BCDDockRequest) -> String {
        switch request {
        case .record: return "At the next start: mark the one-time installs as done."
        case .reset: return "At the next start: take out what the one-time installs added, then run them again."
        case .resetAndSignOut: return "At the next start: take out what the one-time installs added and the Rockstar sign-in, then run them again."
        }
    }

    /// Record as done is saved only from here, after the question.
    private func recordQuestion(_ game: BCDDockInstallGame) -> some View {
        let id = Self.prefix + "installs.\(game.id)."
        return BCDSectionKit.Confirm(
            title: "Record \(game.name)'s one-time installs as done?",
            message: "For an installer that did its work but never closed its window, so BCD never saw it finish. At the next start BCD marks this game's install programs as done, as a finished run would, and they won't run again. Nothing is installed or removed.",
            choices: [
                .init(title: "Record as done", kind: .outline, id: id + "yes") {
                    asking = nil
                    backend.setRequest(game.id, .record)
                    focusButton("cancel", game.id)
                },
                .init(title: "Cancel", kind: .quiet, id: id + "no") {
                    asking = nil
                    focusButton("record", game.id)
                },
            ])
    }

    /// A reset is saved only from here, after the question. The game's next Dock start
    /// carries it out, through the same reset the Dock has always used.
    private func resetQuestion(_ game: BCDDockInstallGame) -> some View {
        let id = Self.prefix + "installs.\(game.id)."
        return BCDSectionKit.Confirm(
            title: "Reset \(game.name)'s one-time installs?",
            message: "At this game's next start through Dock, BCD moves what the one-time installs added to Windows (the Rockstar Games Launcher, Social Club, the Visual C++ 2015-2022 runtimes and the Vulkan runtime) into a backup folder in BCD's Documents, then runs only the installers, as on a new device. Restart BCD after that to play. Other games that share these installs set them up again at their next start. Your game files and saves stay.",
            choices: [
                .init(title: "Reset", kind: .danger, id: id + "yes") {
                    asking = nil
                    backend.setRequest(game.id, .reset)
                    focusButton("cancel", game.id)
                },
                .init(title: "Reset and sign out of Rockstar", kind: .danger, id: id + "signout") {
                    asking = nil
                    backend.setRequest(game.id, .resetAndSignOut)
                    focusButton("cancel", game.id)
                },
                .init(title: "Cancel", kind: .quiet, id: id + "no") {
                    asking = nil
                    focusButton("reset", game.id)
                },
            ])
    }
}
