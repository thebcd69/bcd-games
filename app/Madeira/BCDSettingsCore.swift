// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Settings' sections, where a controller press leads in them, and the engine's words as
// Settings shows them. Plain Swift on purpose: tests/host/check-bcd-settings.py compiles
// this file on the Mac, walks every section with the D-pad and B, and runs the text rules,
// so they are proved, not only drawn.

/// Settings' sections, in the order the list shows them.
enum BCDSettingsSection: String, CaseIterable, Identifiable {
    case accounts, jit, dock, memory, graphics, appearance, controls, storage, dotnet, vcruntime, gecko, logs, feedback, all, developer, updates, about

    var id: String { rawValue }

    var title: String {
        switch self {
        case .accounts: return "Accounts"
        case .jit: return "JIT"
        case .dock: return "BCD Dock"
        case .memory: return "Memory & performance"
        case .graphics: return "Graphics"
        case .appearance: return "Appearance"
        case .controls: return "Controls"
        case .storage: return "Storage"
        case .dotnet: return ".NET Framework"
        case .vcruntime: return "Visual C++ runtime"
        case .gecko: return "HTML engine for installers"
        case .logs: return "Logs"
        case .feedback: return "Feedback"
        case .all: return "All settings"
        case .developer: return "Developer"
        case .updates: return "Updates"
        case .about: return "About"
        }
    }

    /// One line under the title in the upright list.
    var summary: String {
        switch self {
        case .accounts: return "Steam, Epic Games and GOG"
        case .jit: return "How BCD gets JIT on this device"
        case .dock: return "Valve's client for your Steam games"
        case .memory: return "Limits, the swap file, sync and eco mode"
        case .graphics: return "Resolution, frame cap and the display"
        case .appearance: return "App colors and saved profiles"
        case .controls: return "Touch controls, pointer and controllers"
        case .storage: return "What BCD keeps on this device"
        case .dotnet: return "Wine Mono for games made with .NET"
        case .vcruntime: return "Microsoft's runtime for Windows games"
        case .gecko: return "Wine Gecko, for installers that show web pages"
        case .logs: return "Send a log, export it, diagnostics"
        case .feedback: return "Say what is broken or could be better"
        case .all: return "Every option, searchable"
        case .developer: return "The Windows desktop, the log, a controller check"
        case .updates: return "Whether a newer BCD is out"
        case .about: return "Version, credits and licenses"
        }
    }

    var symbol: String {
        switch self {
        case .accounts: return "person.crop.circle"
        case .jit: return "bolt.fill"
        case .dock: return "shippingbox.fill"
        case .memory: return "memorychip"
        case .graphics: return "display"
        case .appearance: return "paintpalette.fill"
        case .controls: return "gamecontroller.fill"
        case .storage: return "internaldrive.fill"
        case .dotnet: return "cube.fill"
        case .vcruntime: return "puzzlepiece.extension.fill"
        case .gecko: return "globe"
        case .logs: return "doc.text.fill"
        case .feedback: return "text.bubble.fill"
        case .all: return "slider.horizontal.3"
        case .developer: return "hammer.fill"
        case .updates: return "arrow.down.circle.fill"
        case .about: return "info.circle.fill"
        }
    }

    /// Sections another part of the interface builds. Until one is wired in
    /// (BCDSettingsBackends.custom), its page says what it will hold.
    var placeholder: Bool { [.accounts, .jit, .dock].contains(self) }

    /// The preview app's `-BCDSection <name>`: the raw name or the title, any case.
    init?(name: String) {
        let wanted = name.lowercased().trimmingCharacters(in: .whitespaces)
        guard let found = Self.allCases.first(where: { $0.rawValue == wanted || $0.title.lowercased() == wanted }) else {
            return nil
        }
        self = found
    }
}

/// The focus ids Settings uses: "settings.list.<section>" for the list, "settings.<section>.<name>"
/// for a section's own controls.
enum BCDSettingsID {
    static let done = "settings.done"
    static let back = "settings.back"
    static let listPrefix = "settings.list."

    static func list(_ section: BCDSettingsSection) -> String { listPrefix + section.rawValue }
    static func control(_ section: BCDSettingsSection, _ name: String) -> String { "settings.\(section.rawValue).\(name)" }

    static func listSection(_ id: String) -> BCDSettingsSection? {
        guard id.hasPrefix(listPrefix) else { return nil }
        return BCDSettingsSection(rawValue: String(id.dropFirst(listPrefix.count)))
    }

    /// The section a control id belongs to: "settings.memory.pool" is Memory's.
    static func section(_ id: String) -> BCDSettingsSection? {
        if let listed = listSection(id) { return listed }
        let parts = id.split(separator: ".")
        guard parts.count >= 3, parts[0] == "settings" else { return nil }
        return BCDSettingsSection(rawValue: String(parts[1]))
    }
}

/// Where a press leads. Up and down walk the list (and show each section beside it), right
/// or A enters a section, left and B come back, B on the list closes Settings; inside a
/// section up and down go row by row and left and right step a value row.
struct BCDSettingsNavigator {
    enum Mode: Equatable {
        /// The list beside the chosen section: a phone on its side, an iPad.
        case split
        /// An upright phone: the list, and a section pushed over it.
        case stacked
    }

    enum Outcome: Equatable {
        /// Move the ring to this control.
        case focus(String)
        /// Show this section beside the list; the ring stays on the list.
        case select(BCDSettingsSection)
        /// Into this section: the ring goes to its first control (nil: the first one it shows).
        case enter(BCDSettingsSection, first: String?)
        /// Back to the list, the ring on this section's row.
        case leave(BCDSettingsSection)
        /// A value row: one choice back (-1) or on (+1).
        case step(String, Int)
        /// Settings closes.
        case close
        /// Used, nothing changes (an end of the list, say).
        case stay
        /// Not one of Settings' own controls: the focus's usual nearest-control rule.
        case pass
    }

    var mode: Mode
    var sections: [BCDSettingsSection] = BCDSettingsSection.allCases
    var selected: BCDSettingsSection
    /// Upright phone: the section's page is showing.
    var pushed = false
    /// The shown section's controls, top to bottom.
    var order: [String] = []
    /// The value rows among them: left and right step these.
    var adjustable: Set<String> = []

    /// The list is what the ring is on, or what B returns to.
    private var onList: Bool { mode == .split || !pushed }

    func move(_ move: BCDMove, from id: String?) -> Outcome {
        guard let id else { return .focus(onList ? BCDSettingsID.list(selected) : (order.first ?? BCDSettingsID.back)) }
        // Upright, only one of the list and the section is on screen: a ring left on the other
        // one comes back to what is shown.
        if mode == .stacked {
            if pushed, BCDSettingsID.listSection(id) != nil { return .focus(order.first ?? BCDSettingsID.back) }
            if !pushed, id == BCDSettingsID.back || order.contains(id) { return .focus(BCDSettingsID.list(selected)) }
        }
        if id == BCDSettingsID.done {
            switch move {
            case .down, .left:
                if mode == .stacked && pushed { return .focus(order.first ?? BCDSettingsID.back) }
                return .focus(BCDSettingsID.list(sections.first ?? selected))
            case .up, .right: return .stay
            }
        }
        if id == BCDSettingsID.back {
            switch move {
            case .down: return order.first.map { .focus($0) } ?? .stay
            case .right: return .focus(BCDSettingsID.done)
            case .left: return .leave(selected)
            case .up: return .stay
            }
        }
        if let section = BCDSettingsID.listSection(id) {
            guard let index = sections.firstIndex(of: section) else { return .pass }
            switch move {
            case .up:
                guard index > 0 else { return .focus(BCDSettingsID.done) }
                return mode == .split ? .select(sections[index - 1]) : .focus(BCDSettingsID.list(sections[index - 1]))
            case .down:
                guard index + 1 < sections.count else { return .stay }
                return mode == .split ? .select(sections[index + 1]) : .focus(BCDSettingsID.list(sections[index + 1]))
            case .right:
                // A section wired in later may list no controls of its own here: the usual rule finds them.
                if mode == .split && section == selected && order.isEmpty { return .pass }
                return .enter(section, first: mode == .split && section == selected ? order.first : nil)
            case .left:
                return .stay
            }
        }
        if let index = order.firstIndex(of: id) {
            switch move {
            case .left, .right:
                if adjustable.contains(id) { return .step(id, move == .left ? -1 : 1) }
                return move == .left ? .leave(selected) : .stay
            case .up:
                if index > 0 { return .focus(order[index - 1]) }
                return mode == .stacked ? .focus(BCDSettingsID.back) : .stay
            case .down:
                return index + 1 < order.count ? .focus(order[index + 1]) : .stay
            }
        }
        return .pass
    }

    /// B: out of a section to the list, and from the list out of Settings.
    func back(from id: String?) -> Outcome {
        guard let id else { return onList ? .close : .leave(selected) }
        if id == BCDSettingsID.done || BCDSettingsID.listSection(id) != nil { return .close }
        if mode == .stacked && !pushed { return .close }
        return .leave(selected)
    }
}

/// The engine's own words (the settings catalog's keys, titles and notes, log file names) as
/// Settings shows them. No "Madeira" anywhere a person reads (owner, 2026-10-18): only the
/// text drawn changes. Keys, files and folders keep their real names on disk and in the
/// code, so existing installs keep working: env.MADEIRA_ONBOARDING is read and written as
/// it is and shown as env.ONBOARDING.
enum BCDSettingsText {
    /// A config key as shown: env.MADEIRA_ONBOARDING is env.ONBOARDING.
    static func key(_ key: String) -> String { scrub(key) }

    /// A catalog title or note, a choice's label, a file name: the same words without the
    /// old name. Identifiers lose it (MADEIRA_X is X, a madeira-x file is x), the config file is
    /// bcd.cfg, as it is called now, and the name itself becomes BCD.
    static func scrub(_ text: String) -> String {
        guard text.range(of: "madeira", options: .caseInsensitive) != nil else { return text }
        var out = text
        for (old, new) in [("env.MADEIRA_", "env."), ("MADEIRA_", ""), ("_MADEIRA", ""), ("madeira-bcd", "BCD")] {
            out = out.replacingOccurrences(of: old, with: new)
        }
        for (pattern, new) in [("(?i)madeira[.]cfg", "bcd.cfg"), ("(?i)madeira[-_]", ""), ("Madeira(?=[A-Z])", ""),
                               ("(?i)madeira", "BCD")] {
            out = out.replacingOccurrences(of: pattern, with: new, options: .regularExpression)
        }
        return out
    }

    /// The real key for one typed into "Add an entry": a key as Settings shows it
    /// (env.ONBOARDING) stands for the real one (env.MADEIRA_ONBOARDING).
    static func realKey(_ typed: String, known: [String]) -> String {
        let wanted = typed.trimmingCharacters(in: .whitespaces)
        if known.contains(wanted) { return wanted }
        return known.first { key($0).caseInsensitiveCompare(wanted) == .orderedSame } ?? wanted
    }

    /// Search finds an option by what is shown (its title, note, shown key, category) and by
    /// its real key.
    static func matches(_ query: String, key: String, title: String, note: String, category: String) -> Bool {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return true }
        return [key, Self.key(key), scrub(title), scrub(note), category].contains { $0.localizedCaseInsensitiveContains(q) }
    }

    /// A log as the Logs section names it: BCD's own log by what it is, a game's by its file.
    static func logName(_ file: String) -> String {
        switch file {
        case "bcd-log.txt": return "BCD's log, this run"
        case "bcd-log.prev.txt": return "BCD's log, the run before"
        default: return scrub(file)
        }
    }
}
