// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import SwiftUI

// Settings › Recommended settings: every rule of the recommended list the app is using, read-only. The list of games is searchable
// by name and filtered by where a copy comes from (Steam, Epic, GOG, DRM-free / any copy: BCDRecommendedStore); choosing a game
// shows everything its rule holds: how it recognises the game, every config line with the builds that get it and whether this build
// does, the lines only a device with little RAM gets, the switches, the notes, the rule's id and version, and the list's version and
// source. The rows, the words and the arithmetic are BCDRecommendedCatalog.swift's (built once, in BCDRecommendedCatalogLive.swift); this
// page draws them. Nothing here writes a setting. A controller reaches every row (search, filter, a game, a line); B leaves a game for
// the list, and the list for Settings' own list.

/// What the page needs from the app.
@MainActor protocol BCDRecommendedBackend: AnyObject {
    /// The catalog of the list in use, built once; nil when the app has no list.
    func load() async -> BCDRecommendedCatalog?
    /// A config line or a note on the clipboard.
    func copy(_ text: String)
}

extension BCDSettingsBackends {
    /// The app's backend; in the preview build only, a sample.
    var recommendedBackend: (any BCDRecommendedBackend)? {
        if let recommended { return recommended }
        #if BCD_PREVIEW
        return BCDRecommendedSample.backend
        #else
        return nil
        #endif
    }
}

struct BCDRecommendedSettings: View {
    let backend: (any BCDRecommendedBackend)?
    @EnvironmentObject private var model: BCDSettingsModel
    @EnvironmentObject private var focus: BCDFocus
    @Environment(\.bcdLayout) private var layout
    @State private var phase = Phase.loading
    @State private var query = ""
    @State private var filter = BCDRecommendedFilter.all
    @State private var openRule: String?
    @State private var lastOpened: String?
    @State private var copied: String?
    @FocusState private var typing: Bool

    private enum Phase {
        case loading
        case missing
        case ready(BCDRecommendedCatalog)
    }

    private static let topID = "settings.recommended.top"

    static func ruleID(_ id: String) -> String { BCDSettingsID.control(.recommended, "rule." + id) }
    private static func control(_ name: String) -> String { BCDSettingsID.control(.recommended, name) }

    var body: some View {
        ScrollViewReader { proxy in
            VStack(spacing: 0) {
                Color.clear.frame(height: 0).id(Self.topID)
                content
            }
            .task {
                guard case .loading = phase else { return }
                if let catalog = await backend?.load() { phase = .ready(catalog) } else { phase = .missing }
                #if BCD_PREVIEW
                previewState()
                #endif
            }
            .onChange(of: openRule) { old, new in
                copied = nil
                // B leaves a game for the list (BCDSettingsSectionBack), and the list for Settings' own list.
                BCDSettingsSectionBack.shared.inner = new == nil ? nil : { openRule = nil; return true }
                if new != nil {
                    proxy.scrollTo(Self.topID, anchor: .top)
                } else if let old {
                    DispatchQueue.main.async { proxy.scrollTo(Self.ruleID(old), anchor: .center) }
                }
                // A controller's ring was on the row that went: it goes to the game's first control, and back to the game's row from there.
                DispatchQueue.main.async {
                    guard focus.controllerActive else { return }
                    focus.focused = new == nil ? old.map(Self.ruleID) : Self.control("back")
                }
            }
            .onDisappear { BCDSettingsSectionBack.shared.inner = nil }
        }
    }

    #if BCD_PREVIEW
    /// The preview app's `-BCDSection recommended+<what>`: `rule=<id>` opens a game, `q=<words>` types a search, `store=<steam|epic|gog|drmFree>` filters.
    private func previewState() {
        guard let what = UserDefaults.standard.string(forKey: "BCDSection")?.split(separator: "+").dropFirst().first else { return }
        let parts = what.split(separator: "=", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return }
        switch parts[0] {
        case "rule": openRule = parts[1]
        case "q": query = parts[1].replacingOccurrences(of: "_", with: " ")
        case "store": filter = BCDRecommendedStore(rawValue: parts[1]).map { .store($0) } ?? .all
        default: break
        }
    }
    #endif

    @ViewBuilder private var content: some View {
        switch phase {
        case .loading:
            note("Reading the list…")
        case .missing:
            note("This copy of BCD has no recommended list.")
        case .ready(let catalog):
            if let id = openRule, let rule = catalog.rule(id: id) {
                RuleDetail(rule: rule, catalog: catalog, backend: backend, copied: $copied, back: { openRule = nil })
            } else {
                list(catalog)
            }
        }
    }

    private func note(_ text: String) -> some View {
        Text(text).font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft)
            .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 4)
    }

    // MARK: The list of games

    private func list(_ catalog: BCDRecommendedCatalog) -> some View {
        let counts = catalog.counts(query: query)
        let groups = catalog.groups(filter: filter, query: query)
        return VStack(alignment: .leading, spacing: 16) {
            Text(BCDRecommendedWords.intro)
                .font(BCDStyle.body(layout == .pad ? 15 : 13.5)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: 3) {
                Text(BCDRecommendedWords.listLine(catalog.info, rules: catalog.rules.count))
                Text(BCDRecommendedWords.buildLine(catalog.info))
                if catalog.info.buildFilteredOnly { Text(BCDRecommendedWords.buildFilteredNote) }
            }
            .font(BCDStyle.label(layout == .pad ? 14 : 12.5)).foregroundStyle(BCDStyle.lavender)
            .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            .accessibilityElement(children: .combine)
            searchField(shown: groups.reduce(0) { $0 + $1.rules.count })
            filterRow(counts)
            Text(BCDRecommendedWords.storeFooter)
                .font(BCDStyle.body(layout == .pad ? 14 : 12.5)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true).padding(.horizontal, 4)
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(groups) { group in
                    BCDEyebrow(text: group.title).padding(.leading, 4).padding(.top, 14).padding(.bottom, 8)
                    ForEach(Array(group.rules.enumerated()), id: \.element) { index, position in
                        let rule = catalog.rules[position]
                        BCDRecommendedRow(rule: rule, stores: catalog.stores(at: position), availability: catalog.availability(rule),
                                          separator: index > 0) { open(rule) }
                            .background(BCDStyle.surface, in: UnevenRoundedRectangle(
                                topLeadingRadius: index == 0 ? 18 : 0, bottomLeadingRadius: index == group.rules.count - 1 ? 18 : 0,
                                bottomTrailingRadius: index == group.rules.count - 1 ? 18 : 0, topTrailingRadius: index == 0 ? 18 : 0,
                                style: .continuous))
                            .id(Self.ruleID(rule.id))
                    }
                }
            }
            if groups.isEmpty {
                Text(query.isEmpty ? "No rule is in this store." : "Nothing matches \u{201C}\(query)\u{201D}.")
                    .font(BCDStyle.body(15)).foregroundStyle(BCDStyle.textSoft).padding(.horizontal, 4)
            }
        }
        // The rows below the screen are not built yet, so the whole order comes from here.
        .transformPreference(BCDSettingsOrderKey.self) { order in
            order = [Self.control("search"), Self.control("filter")] + groups.flatMap { $0.rules }.map { Self.ruleID(catalog.rules[$0].id) }
        }
    }

    private func open(_ rule: BCDRecommendedRule) {
        typing = false
        lastOpened = rule.id
        openRule = rule.id
    }

    // MARK: Search and filter

    private func searchField(shown: Int) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass").foregroundStyle(BCDStyle.textSoft)
            TextField("Find a game", text: $query)
                .font(BCDStyle.body(16))
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .focused($typing)
                .submitLabel(.search)
                .onSubmit { typing = false }   // a single-line field has a return key: no Done on the keyboard (check-bcd-keyboard-done.py)
            if !query.isEmpty {
                Text("\(shown)").font(BCDStyle.label(13)).foregroundStyle(BCDStyle.textSoft).monospacedDigit()
                Button { query = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(BCDStyle.textSoft) }
                    .buttonStyle(.plain).accessibilityLabel("Clear the search")
            }
        }
        .padding(.horizontal, 14).frame(height: 46)
        .background(BCDStyle.surface, in: Capsule())
        .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
        .bcdSettingsControl(Self.control("search"), cornerRadius: 23) { typing = true }
    }

    /// Five chips. A controller sees one row: left and right change the filter, A moves to the next.
    private func filterRow(_ counts: [BCDRecommendedFilter: Int]) -> some View {
        HStack(spacing: 6) {
            ForEach(BCDRecommendedFilter.choices) { choice in
                let chosen = choice == filter
                Button { filter = choice } label: {
                    VStack(spacing: 1) {
                        Text(choice.title).font(BCDStyle.label(layout == .pad ? 15 : 13)).lineLimit(1).minimumScaleFactor(0.7)
                        Text("\(counts[choice] ?? 0)").font(BCDStyle.body(11.5)).monospacedDigit()
                            .foregroundStyle(chosen ? Color.white.opacity(0.85) : BCDStyle.textSoft)
                    }
                    .foregroundStyle(chosen ? Color.white : BCDStyle.textSoft)
                    .frame(maxWidth: .infinity, minHeight: 46)
                    .background(chosen ? BCDStyle.violet : BCDStyle.surfaceRaised, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(chosen ? Color.clear : BCDStyle.stroke, lineWidth: 1))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(choice.title), \(counts[choice] ?? 0) games")
                .accessibilityAddTraits(chosen ? .isSelected : [])
            }
        }
        .padding(.vertical, 2)
        .bcdSettingsControl(Self.control("filter"), token: filter.id, cornerRadius: 18,
                            step: { stepFilter($0) }, action: { stepFilter(1, wrap: true) })
    }

    private func stepFilter(_ delta: Int, wrap: Bool = false) {
        let all = BCDRecommendedFilter.choices
        let from = all.firstIndex(of: filter) ?? 0
        let to = wrap ? (from + delta + all.count) % all.count : min(max(from + delta, 0), all.count - 1)
        filter = all[to]
    }
}

// MARK: - A game in the list

private struct BCDRecommendedRow: View {
    let rule: BCDRecommendedRule
    let stores: Set<BCDRecommendedStore>
    let availability: BCDRecommendedAvailability
    let separator: Bool
    let open: () -> Void
    @Environment(\.bcdLayout) private var layout

    var body: some View {
        Button(action: open) {
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(BCDSettingsText.scrub(rule.displayTitle)).font(BCDStyle.label(layout == .pad ? 16 : 14.5))
                        .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                    Text(BCDRecommendedWords.subtitle(rule, stores: stores))
                        .font(BCDStyle.body(layout == .pad ? 13.5 : 12)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                    if !availability.thisBuildHasIt, let limit = availability.limit {
                        Text("\(limit): not in this build").font(BCDStyle.label(11.5)).foregroundStyle(BCDStyle.amber)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.right").font(.system(size: 13, weight: .bold)).foregroundStyle(BCDStyle.textSoft)
            }
            .padding(.horizontal, 14).padding(.vertical, 11)
            .frame(maxWidth: .infinity, minHeight: 50, alignment: .leading)
            .overlay(alignment: .top) { if separator { BCDSettingsSeparator() } }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdSettingsControl(BCDRecommendedSettings.ruleID(rule.id), action: open)
        .accessibilityHint("Shows everything this rule holds")
    }
}

// MARK: - One rule

private struct RuleDetail: View {
    let rule: BCDRecommendedRule
    let catalog: BCDRecommendedCatalog
    let backend: (any BCDRecommendedBackend)?
    @Binding var copied: String?
    let back: () -> Void
    @EnvironmentObject private var model: BCDSettingsModel
    @Environment(\.bcdLayout) private var layout

    private func control(_ name: String) -> String { BCDSettingsID.control(.recommended, name) }

    var body: some View {
        let info = catalog.info
        let inStores = catalog.position(of: rule.id).map { catalog.stores(at: $0) } ?? rule.stores
        let stores = BCDRecommendedStore.allCases.filter { inStores.contains($0) }
        VStack(alignment: .leading, spacing: 18) {
            backRow
            header(stores)
            BCDSettingsGroup(title: "This rule") {
                BCDSettingsInfoRow(title: "Rule id", value: rule.id)
                BCDSettingsInfoRow(title: "Rule version", value: "\(rule.version)")
                BCDSettingsInfoRow(title: "This build", note: info.buildFilteredOnly ? nil : catalog.availability(rule).words,
                                   value: catalog.availability(rule).thisBuildHasIt ? "Has it" : "Not yet")
                BCDSettingsInfoRow(title: "Lines this build writes",
                                   note: "On this device. A line for other builds writes nothing, and neither does a rule this build does not know.",
                                   value: "\(catalog.written(rule).count)")
            }
            BCDSettingsGroup(title: "How it recognises the game",
                             footer: "The first rule that matches a game is its list. Matching is by Steam App ID, Steam folder or program name; the list holds no Epic or GOG ids.") {
                ForEach(Array(rule.matchWords.enumerated()), id: \.offset) { _, words in
                    Text(words).font(BCDStyle.body(layout == .pad ? 15 : 14)).foregroundStyle(BCDStyle.text)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 14).padding(.vertical, 11)
                        .textSelection(.enabled)
                }
            }
            if !rule.switches.isEmpty {
                BCDSettingsGroup(title: "Switches", footer: "Kept on the game's page, next to its config; Reset to Recommended sets them with the lines.") {
                    ForEach(Array(rule.switches.enumerated()), id: \.offset) { _, item in
                        BCDSettingsInfoRow(title: item.label, value: item.value)
                    }
                }
            }
            linesGroup
            if !rule.smallRAMLines.isEmpty { smallRAMGroup }
            if !rule.notes.isEmpty { notesGroup }
            BCDSettingsGroup(title: "The list in use", footer: info.buildFilteredOnly ? BCDRecommendedWords.buildFilteredNote : nil) {
                BCDSettingsInfoRow(title: "List version", value: "\(info.version)")
                BCDSettingsInfoRow(title: "Made on", value: info.generated)
                BCDSettingsInfoRow(title: "Source", value: info.sourceWords)
                BCDSettingsInfoRow(title: "This build", value: info.build.map(String.init) ?? "Unknown")
            }
        }
    }

    // MARK: Header

    private var backRow: some View {
        Button(action: back) {
            HStack(spacing: 6) {
                Image(systemName: "chevron.left").font(.system(size: 14, weight: .bold))
                Text("All games").font(BCDStyle.label(16))
                Spacer(minLength: 0)
            }
            .foregroundStyle(BCDStyle.lavender)
            .padding(.horizontal, 4).frame(minHeight: 40)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .bcdSettingsControl(control("back"), cornerRadius: 14, action: back)
    }

    private func header(_ stores: [BCDRecommendedStore]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(BCDSettingsText.scrub(rule.displayTitle)).font(BCDStyle.heading(layout == .pad ? 26 : 22))
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            if rule.isStandard {
                Text("Every game that has no list of its own").font(BCDStyle.body(14)).foregroundStyle(BCDStyle.textSoft)
            } else {
                HStack(spacing: 6) {
                    ForEach(stores) { store in
                        Text(store.title).font(BCDStyle.label(12.5)).foregroundStyle(BCDStyle.lavender)
                            .padding(.horizontal, 10).frame(height: 26)
                            .background(BCDStyle.surfaceRaised, in: Capsule())
                            .overlay(Capsule().strokeBorder(BCDStyle.stroke, lineWidth: 1))
                    }
                }
                ForEach(stores) { store in
                    Text(store.meaning).font(BCDStyle.body(layout == .pad ? 13.5 : 12)).foregroundStyle(BCDStyle.textSoft)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(.horizontal, 4)
    }

    // MARK: Lines

    private var linesGroup: some View {
        let rows = catalog.lineRows(rule)
        let limited = rule.lines.filter { $0.minBuild != nil || $0.maxBuild != nil }.count
        return BCDSettingsGroup(title: "Config lines (\(rule.settingLines.count))",
                                footer: (limited > 0 ? "\(BCDRecommendedWords.count(limited, "line")) for some builds only; each says whether this build has it. " : "")
                                    + "Choose a line to copy it.") {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                lineRow(row.line, row.availability, id: "line.\(index)")
            }
        }
    }

    private var smallRAMGroup: some View {
        let rows = catalog.smallRAMRows(rule)
        return BCDSettingsGroup(title: "Only on a device with little RAM",
                                footer: BCDRecommendedWords.smallRAMWords(thisDevice: catalog.info.smallRAMDevice)) {
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                lineRow(row.line, row.availability, id: "small.\(index)")
            }
        }
    }

    @ViewBuilder private func lineRow(_ line: BCDRecommendedLine, _ availability: BCDRecommendedAvailability, id: String) -> some View {
        let shown = BCDSettingsText.scrub(line.text)
        let words = catalog.info.buildFilteredOnly ? nil : availability.limit.map { _ in availability.words }
        if line.isComment {
            Text(shown).font(.system(size: layout == .pad ? 13.5 : 12, design: .monospaced)).foregroundStyle(BCDStyle.textSoft)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading).padding(.horizontal, 14).padding(.vertical, 9)
                .textSelection(.enabled)
        } else {
            Button { copy(line.text, id: id) } label: {
                HStack(alignment: .top, spacing: 10) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(shown).font(.system(size: layout == .pad ? 14 : 12.5, design: .monospaced))
                            .foregroundStyle(availability.thisBuildHasIt ? BCDStyle.text : BCDStyle.textSoft)
                            .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                        if let words {
                            Text(words).font(BCDStyle.label(11.5))
                                .foregroundStyle(availability.thisBuildHasIt ? BCDStyle.mint : BCDStyle.amber)
                                .multilineTextAlignment(.leading).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if copied == id {
                        Text("Copied").font(BCDStyle.label(12)).foregroundStyle(BCDStyle.lavender)
                    }
                }
                .padding(.horizontal, 14).padding(.vertical, 9)
                .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .bcdSettingsControl(control(id), token: copied == id ? "copied" : "", cornerRadius: 12) { copy(line.text, id: id) }
            .accessibilityHint("Copies the line")
        }
    }

    private func copy(_ text: String, id: String) {
        backend?.copy(text)
        copied = id
    }

    // MARK: Notes

    private var notesGroup: some View {
        BCDSettingsGroup(title: "Notes", footer: "Why the lines are what they are, from the people who tested them.") {
            BCDSettingsActionRow(id: control("notes"), title: "Read the notes",
                                 note: BCDRecommendedWords.count(rule.notes.count, "note") + " for this rule",
                                 symbol: "text.alignleft") {
                model.read(BCDSettingsText.scrub(rule.displayTitle), text: BCDSettingsText.scrub(rule.notes.joined(separator: "\n\n")))
            }
        }
    }
}

// MARK: - The preview build's sample

#if BCD_PREVIEW
/// What the preview app shows: a few rules of the real list, as they are written there.
@MainActor final class BCDRecommendedSample: BCDRecommendedBackend {
    static let backend: BCDRecommendedBackend = BCDRecommendedSample()
    func copy(_ text: String) {}
    func load() async -> BCDRecommendedCatalog? { Self.catalog }

    private static func lines(_ texts: [String]) -> [BCDRecommendedLine] { texts.map { BCDRecommendedLine(text: $0) } }

    static let catalog: BCDRecommendedCatalog = {
        let info = BCDRecommendedListInfo(version: 92, generated: "2026-11-22", origin: .builtIn, build: 708, smallRAMDevice: true)
        var rules: [BCDRecommendedRule] = []
        rules.append(BCDRecommendedRule(
            id: "standard", title: "games without a list of their own", version: 1,
            lines: lines(["# BCD's recommended settings for a game without a list of its own.",
                          "# Reset to Recommended, at the bottom of the game's settings, brings them back.",
                          "env.EC_HOOK_TRACE = 0"]),
            notes: ["Every other game: most games tried outside these lists closed at start until the reporter was off."]))
        rules.append(BCDRecommendedRule(
            id: "rdr2-steam", title: "Red Dead Redemption 2 (Steam)", version: 16, steamAppIDs: [1174180],
            steamFolders: ["red dead redemption 2"],
            programs: [.init(name: "rdr2.exe", steam: true), .init(name: "playrdr2.exe", steam: true)],
            lines: lines(["# BCD's recommended settings for Red Dead Redemption 2 (Steam).",
                          "# Reset to Recommended, at the bottom of the game's settings, brings them back.",
                          "d3d12-caps-log = 2", "vram-mb = 4352", "swap-mb = 3072", "env.POOL_MORE = 1"])
                + [BCDRecommendedLine(text: "env.BG_QOS_DELAY_S = 20", minBuild: 665),
                   BCDRecommendedLine(text: "env.POOL_FEX = 1", minBuild: 700),
                   BCDRecommendedLine(text: "env.OLD_TRIAL = 1", minBuild: 600, maxBuild: 650)],
            smallRAMLines: lines(["swap-mb = 3072", "mempool-mb = 2048"]),
            switches: [.init(label: "AVX", value: "On"), .init(label: "Report an NVIDIA GPU", value: "On"),
                       .init(label: "FPS limit", value: "30"), .init(label: "Resolution", value: "This screen's shape at 720 lines")],
            notes: ["Version 16 (build 665): the pool lines came from a tester's own run.", "Tested on an iPhone with 12 GB."]))
        rules.append(BCDRecommendedRule(
            id: "rdr2-other", title: "Red Dead Redemption 2 (not Steam)", version: 7,
            programs: [.init(name: "rdr2.exe", steam: false, beside: "playrdr2.exe"), .init(name: "playrdr2.exe", steam: false)],
            lines: lines(["d3d12-caps-log = 2", "env.STEAM_IDENTITY = none", "env.BCD_EPIC_HOST_START = 1"]),
            notes: ["Epic's RDR2 starts through BCD Host."]))
        rules.append(BCDRecommendedRule(
            id: "hollow-knight", title: "Hollow Knight", version: 3, steamAppIDs: [367520], steamFolders: ["hollow knight"],
            programs: [.init(name: "hollow_knight.exe")], lines: lines(["fastsync = 0", "env.REAL_SUSPEND = 1"]),
            switches: [.init(label: "Fast synchronization", value: "Off")],
            notes: ["Unity Mono: the game's own garbage collector needs real suspends."]))
        rules.append(BCDRecommendedRule(
            id: "satisfactory", title: "Satisfactory", version: 5, steamAppIDs: [526870],
            programs: [.init(name: "factorygame-win64-shipping.exe", steam: true), .init(name: "factorygameegs-win64-shipping.exe")],
            lines: lines(["env.SC_PA_POOLS = 2", "vram-mb = 3072"]),
            notes: ["Satisfactory (Steam 526870, Epic copy FactoryGameEGS.exe)."]))
        rules.append(BCDRecommendedRule(
            id: "bioshock-remastered-epic", title: "BioShock Remastered (Epic)", version: 2,
            programs: [.init(name: "bioshockhd.exe", steam: false)], lines: lines(["env.GDI_SHARED_SECTION = 1"]),
            notes: ["BioShock Remastered from the Epic Games Store."]))
        rules.append(BCDRecommendedRule(
            id: "cyberpunk-2077", title: "Cyberpunk 2077", version: 9, steamAppIDs: [1091500], steamFolders: ["cyberpunk 2077"],
            programs: [.init(name: "cyberpunk2077.exe")], lines: lines(["vram-mb = 4096", "swap-mb = 4096"]),
            notes: ["Steam 1091500 through BCD Dock; a GOG copy."]))
        rules.append(BCDRecommendedRule(
            id: "dark-souls-iii", title: "DARK SOULS III", version: 4, steamAppIDs: [374320], steamFolders: ["dark souls iii"],
            programs: [.init(name: "darksoulsiii.exe")], lines: lines(["env.SRV_GETDESC_RESOLVE = 1"]),
            smallRAMLines: lines(["swap-mb = 3072"])))
        rules.append(BCDRecommendedRule(
            id: "glyphica-typing-survival", title: "Glyphica: Typing Survival", version: 1, minBuild: 740,
            programs: [.init(name: "glyphica.exe")], lines: lines(["env.ZERO_MITIGATION_POLICY = 1"])))
        rules.append(BCDRecommendedRule(
            id: "1000xresist", title: "1000xRESIST", version: 2, steamAppIDs: [1675830],
            programs: [.init(name: "1000xresist.exe")], lines: lines(["vram-mb = 3072"])))
        for (i, name) in ["A Hat in Time", "Albion Online", "Alice: Madness Returns", "Animal Well", "Battle Brothers", "Celeste",
                          "Cuphead", "Dead Cells", "Disco Elysium", "Hades", "Hotline Miami", "Inside", "Limbo", "Outlast",
                          "Stardew Valley", "Terraria", "The Witness", "Undertale", "Vampire Survivors"].enumerated() {
            rules.append(BCDRecommendedRule(
                id: "sample-\(i)", title: name, version: 1 + i % 4, steamAppIDs: [100_000 + i],
                programs: [.init(name: name.lowercased().replacingOccurrences(of: " ", with: "") + ".exe")],
                lines: lines(["vram-mb = \(2048 + 512 * (i % 5))", "fastsync = 1"])))
        }
        return BCDRecommendedCatalog(info: info, rules: rules)
    }()
}
#endif
