// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › Feedback: the person says what is broken or what should be better, picks a kind
// and sends it. The message reaches BCD's own intake (FeedbackUpload.swift, through the backend
// below; BCDSettingsLive.swift is the only engine side). The page draws everything the person
// reads, so the preview app shows exactly what the app shows. A controller reaches every
// control: left and right on the kind row change the kind, A on the message opens the keyboard.

/// What the message is about.
enum BCDFeedbackKind: String, CaseIterable, Identifiable {
    case bug, idea, other

    var id: String { rawValue }

    var title: String {
        switch self {
        case .bug: return "Bug"
        case .idea: return "Idea"
        case .other: return "Other"
        }
    }

    var symbol: String {
        switch self {
        case .bug: return "ladybug.fill"
        case .idea: return "lightbulb.fill"
        case .other: return "ellipsis.bubble.fill"
        }
    }

    /// What the empty message field asks.
    var prompt: String {
        switch self {
        case .bug: return "What went wrong? Which game, and what were you doing?"
        case .idea: return "What should BCD do, or do better?"
        case .other: return "What would you like to tell us?"
        }
    }
}

enum BCDFeedbackRules {
    /// The most characters a message holds (the intake's FEEDBACK_TEXT_MAX; Unicode scalars, as it counts them).
    static let maxCharacters = 4000
    /// The one line about what leaves the phone.
    static let privacy = "BCD sends what you write, the BCD build, the iOS version and the device model. No name or account."
    static func count(_ text: String) -> Int { text.unicodeScalars.count }
    static func limited(_ text: String) -> String {
        count(text) <= maxCharacters ? text : String(String.UnicodeScalarView(text.unicodeScalars.prefix(maxCharacters)))
    }
}

/// What the person has written so far. The backend keeps it, so it survives a visit to another
/// section while Settings is open.
@MainActor final class BCDFeedbackDraft: ObservableObject {
    @Published var kind = BCDFeedbackKind.bug
    @Published var text = ""
    /// A bug report: the stored choice, on until the person turns it off (BCDFeedbackAttach). Any other message: the switch for it, off
    /// until the person turns it on, as it always was.
    @Published private var bugLog = BCDFeedbackAttach.bugChoice()
    @Published private var otherLog = false

    /// Whether the log goes with a message of the kind chosen now. Turning it off for a bug report is kept: that is the option.
    var attachLog: Bool {
        get { kind == .bug ? bugLog : otherLog }
        set {
            if kind == .bug {
                bugLog = newValue
                BCDFeedbackAttach.setBugChoice(newValue)
            } else {
                otherLog = newValue
            }
        }
    }
}

enum BCDFeedbackState: Equatable {
    case idle
    case sending(String)
    case sent(String)
    case failed(String)
}

struct BCDSentFeedback: Identifiable, Hashable {
    var code: String
    var kind: BCDFeedbackKind
    var date: Date
    var line: String
    var id: String { code }
}

/// The log "Attach my last game's log" would send.
struct BCDFeedbackLog: Hashable {
    var name: String
    var date: Date
    var bytes: Int64
    var hasGame = true        // round 624 (agent N S6): false: BCD's own start-up only, and no game log of the last day
}

@MainActor protocol BCDFeedbackBackend: ObservableObject {
    var draft: BCDFeedbackDraft { get }
    /// The log a message can carry: the newest game log on this device, or BCD's own newest one when no game ran in the last day; nil before
    /// a game has run.
    var newestLog: BCDFeedbackLog? { get }
    /// What was sent, newest first (the last 20 are kept).
    var sent: [BCDSentFeedback] { get }
    var state: BCDFeedbackState { get }
    /// Sends the draft: with `newestLog` first when `draft.attachLog` is on (a bug report: unless turned off). On success the draft's
    /// text is cleared and the state says the code.
    func send()
    func copy(_ code: String)
    func refresh()
}

struct BCDFeedbackSettings<Backend: BCDFeedbackBackend>: View {
    @ObservedObject var backend: Backend
    @ObservedObject var draft: BCDFeedbackDraft
    @Environment(\.bcdLayout) private var layout
    @FocusState private var typing: Bool
    @State private var copied: String?

    init(backend: Backend) {
        self.backend = backend
        self.draft = backend.draft
    }

    private var sending: Bool {
        if case .sending = backend.state { return true }
        return false
    }
    private var canSend: Bool {
        !sending && !draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            BCDSettingsGroup(title: "Your message") {
                kindRow
                messageRow
                attachRow
            }
            sendBlock
            if !backend.sent.isEmpty {
                BCDSettingsGroup(title: "Sent", footer: "Choose a code to copy it.") {
                    ForEach(backend.sent.prefix(5)) { item in
                        BCDSettingsActionRow(id: BCDSettingsID.control(.feedback, "sent.\(item.code)"), title: item.code,
                                             note: sentNote(item), detail: copied == item.code ? "Copied" : nil,
                                             symbol: "doc.on.doc") {
                            backend.copy(item.code)
                            copied = item.code
                        }
                    }
                }
            }
        }
        .onAppear { backend.refresh() }
    }

    // MARK: The kind

    /// Three chips. A controller sees one row: left and right change the kind, A moves to the next.
    private var kindRow: some View {
        HStack(spacing: 8) {
            ForEach(BCDFeedbackKind.allCases) { kind in
                let chosen = kind == draft.kind
                Button { draft.kind = kind } label: {
                    HStack(spacing: 6) {
                        Image(systemName: kind.symbol).font(.system(size: 13, weight: .semibold))
                        Text(kind.title).font(BCDStyle.label(layout == .pad ? 16 : 14))
                    }
                    .foregroundStyle(chosen ? Color.white : BCDStyle.textSoft)
                    .frame(maxWidth: .infinity, minHeight: 40)
                    .background(chosen ? BCDStyle.violet : BCDStyle.surfaceRaised, in: Capsule())
                    .overlay(Capsule().strokeBorder(chosen ? Color.clear : BCDStyle.stroke, lineWidth: 1))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(chosen ? .isSelected : [])
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .bcdSettingsControl(BCDSettingsID.control(.feedback, "kind"), token: draft.kind.rawValue, cornerRadius: 22,
                            step: { stepKind($0) }, action: { stepKind(1, wrap: true) })
    }

    private func stepKind(_ delta: Int, wrap: Bool = false) {
        let all = BCDFeedbackKind.allCases
        let from = all.firstIndex(of: draft.kind) ?? 0
        let to = wrap ? (from + delta + all.count) % all.count : min(max(from + delta, 0), all.count - 1)
        draft.kind = all[to]
    }

    // MARK: The message

    private var messageRow: some View {
        VStack(alignment: .leading, spacing: 12) {
            ZStack(alignment: .topLeading) {
                TextEditor(text: $draft.text)
                    .font(BCDStyle.body(16))
                    .scrollContentBackground(.hidden)
                    .focused($typing)
                    .frame(height: layout == .pad ? 190 : layout == .phoneLandscape ? 96 : 150)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .onChange(of: draft.text) { _, new in
                        if BCDFeedbackRules.count(new) > BCDFeedbackRules.maxCharacters { draft.text = BCDFeedbackRules.limited(new) }
                    }
                    .accessibilityLabel("Your message")
                if draft.text.isEmpty {
                    Text(draft.kind.prompt)
                        .font(BCDStyle.body(16)).foregroundStyle(BCDStyle.textSoft.opacity(0.75))
                        .padding(.horizontal, 12).padding(.vertical, 10)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
            .background(BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(typing ? BCDStyle.lavender.opacity(0.7) : BCDStyle.stroke, lineWidth: 1))
            .bcdSettingsControl(BCDSettingsID.control(.feedback, "message"), cornerRadius: 12) { typing = true }
            counterRow
        }
        .padding(.horizontal, 14).padding(.vertical, 12)
        .bcdKeyboardDone { typing = false }
    }

    private var counterRow: some View {
        let used = BCDFeedbackRules.count(draft.text)
        return HStack(spacing: 10) {
            if typing {
                Button { typing = false } label: {
                    Text("Done").font(BCDStyle.label(13))
                        .padding(.horizontal, 14).frame(height: 28)
                        .background(BCDStyle.surfaceRaised, in: Capsule())
                        .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .bcdSettingsControl(BCDSettingsID.control(.feedback, "done"), cornerRadius: 14) { typing = false }
                .accessibilityLabel("Close the keyboard")
            }
            Spacer(minLength: 0)
            Text("\(used) / \(BCDFeedbackRules.maxCharacters)")
                .font(BCDStyle.body(12.5)).monospacedDigit()
                .foregroundStyle(used >= BCDFeedbackRules.maxCharacters - 100 ? BCDSettingsColor.amber : BCDStyle.textSoft)
                .accessibilityLabel("\(used) of \(BCDFeedbackRules.maxCharacters) characters")
        }
        .padding(.horizontal, 2)
    }

    // MARK: The log

    /// What the log is: the game, the day, the size. A bug report carries it unless the person turns this off (and that is kept);
    /// any other message only when the person turns it on.
    private var attachRow: some View {
        let log = backend.newestLog
        let on = log != nil && draft.attachLog
        let shown = log.map { BCDFeedbackAttach.Shown(name: $0.name, when: BCDFormat.dateTime($0.date), size: BCDFormat.bytes($0.bytes), hasGame: $0.hasGame) }
        return BCDSettingsToggleRow(id: BCDSettingsID.control(.feedback, "log"), title: BCDFeedbackAttach.title,
                                    note: BCDFeedbackAttach.note(isBug: draft.kind == .bug, on: on, log: shown),
                                    isOn: on, enabled: log != nil && !sending) { draft.attachLog = $0 }
    }

    // MARK: Send

    private var sendBlock: some View {
        VStack(alignment: .leading, spacing: 10) {
            Button { send() } label: {
                Label(sending ? "Sending…" : "Send", systemImage: "paperplane.fill")
            }
            .buttonStyle(BCDPrimaryButtonStyle())
            .disabled(!canSend)
            .frame(maxWidth: 360)
            .bcdSettingsControl(BCDSettingsID.control(.feedback, "send"), token: "\(canSend)", cornerRadius: 27) { send() }
            status
            Text(BCDFeedbackRules.privacy)
                .font(BCDStyle.body(layout == .pad ? 14 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 4)
            if backend.newestLog != nil && draft.attachLog {
                Text(BCDFeedbackAttach.privacyWithLog)
                    .font(BCDStyle.body(layout == .pad ? 14 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            }
        }
    }

    private func send() {
        guard canSend else { return }
        typing = false
        backend.send()
    }

    @ViewBuilder private var status: some View {
        switch backend.state {
        case .idle:
            EmptyView()
        case .sending(let line):
            Text(line).font(BCDStyle.label(14)).foregroundStyle(BCDStyle.lavender).padding(.horizontal, 4)
        case .sent(let code):
            // Gone as soon as the next message is started.
            if draft.text.isEmpty {
                Label("Sent. Your code: \(code)", systemImage: "checkmark.circle.fill")
                    .font(BCDStyle.label(14)).foregroundStyle(BCDStyle.mint).padding(.horizontal, 4)
            }
        case .failed(let why):
            Label { Text(why).fixedSize(horizontal: false, vertical: true) } icon: { Image(systemName: "exclamationmark.triangle.fill") }
                .font(BCDStyle.label(14)).foregroundStyle(BCDStyle.alert).padding(.horizontal, 4)
        }
    }

    private func sentNote(_ item: BCDSentFeedback) -> String {
        let when = BCDFormat.dateTime(item.date)
        return item.line.isEmpty ? "\(item.kind.title) · \(when)" : "\(item.kind.title) · \(when) · \(item.line)"
    }
}
