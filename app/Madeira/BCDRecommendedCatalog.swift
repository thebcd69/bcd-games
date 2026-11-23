// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  BCDRecommendedCatalog.swift
//  Madeira
//
//  Settings › Recommended settings: the rules of the recommended list the app is using, as one
//  read-only catalog. This file is the pure part, Foundation only, and names no engine type:
//  tests/host/check-recommended-catalog.py compiles it (with RecommendedCatalogAdapter.swift,
//  which turns a RecommendationsFile into this model) and runs tests/host/recommended_catalog_checks.swift
//  over the committed recommended/recommendations.json.
//
//    - the model: a rule as the list holds it (its match, its lines with their builds, the lines only a
//      device with little RAM gets, the switches, the notes), the list's version and source;
//    - where a game's copy comes from: Steam, Epic, GOG, DRM-free / any copy. The list format has no
//      field for a store, so the classification is made here, from what a rule says (below);
//    - search by game name (and program, Steam App ID), the filter by store, the letter groups, the counts;
//    - whether THIS build has a rule or a line (`minBuild` / `maxBuild`, as RecommendationLine.applies reads them).
//
//  The catalog is built once per list in use (BCDRecommendedCatalogLive.swift) and never changes while
//  the page shows; a search or a filter only walks the 373-odd precomputed rows.

import Foundation

// MARK: - Where a copy comes from

/// Where a game's copy comes from. One rule can be in several.
enum BCDRecommendedStore: String, CaseIterable, Identifiable, Hashable {
    case steam, epic, gog, drmFree

    var id: String { rawValue }

    /// The filter chip.
    var title: String {
        switch self {
        case .steam: return "Steam"
        case .epic: return "Epic"
        case .gog: return "GOG"
        case .drmFree: return "DRM-free"
        }
    }

    /// What the store means in the list, in full.
    var meaning: String {
        switch self {
        case .steam: return "Steam: the rule matches a Steam game by its App ID or its folder, or a program inside a Steam library."
        case .epic: return "Epic: the rule is named for, or says it was tested on, an Epic Games Store copy."
        case .gog: return "GOG: the rule says it was tested on a GOG copy."
        case .drmFree: return "DRM-free / any copy: the rule matches a program by its file name wherever it is installed, so a copy from Epic, GOG or plain files gets it too."
        }
    }
}

/// What the page filters by.
enum BCDRecommendedFilter: Hashable, Identifiable {
    case all
    case store(BCDRecommendedStore)

    var id: String {
        switch self {
        case .all: return "all"
        case .store(let store): return store.rawValue
        }
    }

    var title: String {
        switch self {
        case .all: return "All"
        case .store(let store): return store.title
        }
    }

    static let choices: [BCDRecommendedFilter] = [.all] + BCDRecommendedStore.allCases.map { .store($0) }
}

// MARK: - A line and the builds that get it

/// Whether a build has a rule or a line. `RecommendationLine.applies(build:)` and `RecommendationRule.applies(build:)`
/// (GameRecommendationsList.swift) decide what the app writes; this reads the same limits for the page.
enum BCDRecommendedAvailability: Equatable {
    /// No limit: every build.
    case everyBuild
    /// A limit, and this build is inside it.
    case has(from: Int?, to: Int?)
    /// This build is older than `from`.
    case notYet(from: Int)
    /// This build is newer than `to`.
    case ended(to: Int)
    /// A limit, and this build's own number is unknown: the app gives such a line to no build.
    case unknownBuild(from: Int?, to: Int?)

    static func of(minBuild: Int?, maxBuild: Int?, build: Int?) -> BCDRecommendedAvailability {
        guard minBuild != nil || maxBuild != nil else { return .everyBuild }
        guard let build else { return .unknownBuild(from: minBuild, to: maxBuild) }
        if let lower = minBuild, build < lower { return .notYet(from: lower) }
        if let upper = maxBuild, build > upper { return .ended(to: upper) }
        return .has(from: minBuild, to: maxBuild)
    }

    /// Whether this build gets it.
    var thisBuildHasIt: Bool {
        switch self {
        case .everyBuild, .has: return true
        case .notYet, .ended, .unknownBuild: return false
        }
    }

    /// The limit, as the list writes it: "From build 665", "Up to build 590", "Builds 600 to 650"; nil without a limit.
    var limit: String? {
        switch self {
        case .everyBuild: return nil
        case .has(let from, let to), .unknownBuild(let from, let to): return Self.limit(from: from, to: to)
        case .notYet(let from): return Self.limit(from: from, to: nil)
        case .ended(let to): return Self.limit(from: nil, to: to)
        }
    }

    /// Whether this build gets it, in a few words.
    var verdict: String {
        switch self {
        case .everyBuild: return "Every build"
        case .has: return "This build has it"
        case .notYet: return "This build is older and does not have it"
        case .ended: return "This build is newer and does not have it"
        case .unknownBuild: return "This build's number is unknown, so it gets no line like this"
        }
    }

    /// One line: the limit and the verdict.
    var words: String {
        guard let limit else { return verdict }
        return limit + " · " + verdict
    }

    private static func limit(from: Int?, to: Int?) -> String? {
        switch (from, to) {
        case (nil, nil): return nil
        case (let from?, nil): return "From build \(from)"
        case (nil, let to?): return "Up to build \(to)"
        case (let from?, let to?): return from == to ? "Build \(from) only" : "Builds \(from) to \(to)"
        }
    }
}

/// One line of a game's config file as the list writes it.
struct BCDRecommendedLine: Equatable {
    var text: String
    var minBuild: Int? = nil
    var maxBuild: Int? = nil

    var isComment: Bool { text.hasPrefix("#") }

    /// "key" of "key = value" ("env.NAME" for an environment line); nil for a comment.
    var key: String? {
        guard !isComment, let range = text.range(of: " = ") else { return nil }
        return String(text[..<range.lowerBound])
    }

    var value: String? {
        guard !isComment, let range = text.range(of: " = ") else { return nil }
        return String(text[range.upperBound...])
    }

    func availability(build: Int?) -> BCDRecommendedAvailability {
        .of(minBuild: minBuild, maxBuild: maxBuild, build: build)
    }
}

// MARK: - A rule

/// One rule of the list: how it recognises a game and what it sets.
struct BCDRecommendedRule: Identifiable, Equatable {
    struct Program: Equatable {
        var name: String
        /// true: only inside a Steam library; false: only outside one; nil: anywhere.
        var steam: Bool? = nil
        /// A file that must be in the program's folder.
        var beside: String? = nil
        /// A file that must not be.
        var notBeside: String? = nil
    }

    /// A switch the rule sets outside the config file, as the game's settings page words it ("AVX", "on").
    struct Switch: Equatable {
        var label: String
        var value: String
    }

    var id: String
    var title: String
    var version: Int
    /// Builds before this one do not know the rule.
    var minBuild: Int? = nil
    var steamAppIDs: [Int] = []
    var steamFolders: [String] = []
    var programs: [Program] = []
    var lines: [BCDRecommendedLine] = []
    /// The lines only a device with little RAM gets, after `lines`.
    var smallRAMLines: [BCDRecommendedLine] = []
    var switches: [Switch] = []
    var notes: [String] = []

    /// The rule every game without a rule of its own gets: it has no match.
    var isStandard: Bool { id == BCDRecommendedCatalog.standardID }

    /// The title the page shows ("games without a list of their own" starts with a capital).
    var displayTitle: String {
        guard isStandard, let first = title.first else { return title }
        return first.uppercased() + title.dropFirst()
    }

    func availability(build: Int?) -> BCDRecommendedAvailability {
        .of(minBuild: minBuild, maxBuild: nil, build: build)
    }

    // MARK: Where it comes from

    /// Steam: a library Steam game by its App ID or its folder, or a program that counts only inside a Steam library.
    var matchesSteam: Bool {
        !steamAppIDs.isEmpty || !steamFolders.isEmpty || programs.contains { $0.steam == true }
    }

    /// DRM-free / any copy: a program by its file name that counts outside a Steam library too (the flag is false or left out).
    var matchesAnyCopy: Bool { programs.contains { $0.steam != true } }

    /// The stores this rule is in. Steam and DRM-free / any copy are what the rule matches (`matchesSteam`, `matchesAnyCopy`); the
    /// list format has no field for Epic or GOG, so those two are what the rule says about itself: its id or title has the word, or
    /// a note names an Epic Games Store copy or a GOG copy (`BCDRecommendedStores`).
    var stores: Set<BCDRecommendedStore> {
        var out = Set<BCDRecommendedStore>()
        if matchesSteam { out.insert(.steam) }
        if matchesAnyCopy { out.insert(.drmFree) }
        if BCDRecommendedStores.names(.epic, id: id, title: title, notes: notes) { out.insert(.epic) }
        if BCDRecommendedStores.names(.gog, id: id, title: title, notes: notes) { out.insert(.gog) }
        return out
    }

    // MARK: What it holds

    /// The lines that set something, comments left out.
    var settingLines: [BCDRecommendedLine] { lines.filter { !$0.isComment } }

    /// How many lines carry a build limit (`lines` and `smallRAMLines`).
    var limitedLineCount: Int {
        (lines + smallRAMLines).filter { $0.minBuild != nil || $0.maxBuild != nil }.count
    }

    /// The lowest build any of its lines needs, or the rule's own `minBuild`.
    var needsBuild: Int? {
        ([minBuild] + (lines + smallRAMLines).map(\.minBuild)).compactMap { $0 }.max()
    }

    /// How the rule recognises its game, one phrase each, in the list's order.
    var matchWords: [String] {
        if isStandard && steamAppIDs.isEmpty && steamFolders.isEmpty && programs.isEmpty {
            return ["Every game that has no list of its own gets this one."]
        }
        var out: [String] = []
        if !steamAppIDs.isEmpty {
            out.append((steamAppIDs.count == 1 ? "Steam App ID " : "Steam App IDs ") + steamAppIDs.map(String.init).joined(separator: ", "))
        }
        for folder in steamFolders { out.append("Steam folder \u{201C}\(folder)\u{201D} (under steamapps\\common)") }
        for program in programs { out.append(Self.words(for: program)) }
        return out
    }

    static func words(for program: Program) -> String {
        var text = "Program \(program.name)"
        switch program.steam {
        case true?: text += ", only inside a Steam library"
        case false?: text += ", only outside a Steam library"
        case nil: text += ", wherever it is installed"
        }
        if let beside = program.beside { text += ", only when \(beside) is in the same folder" }
        if let notBeside = program.notBeside { text += ", only when \(notBeside) is not in the same folder" }
        return text
    }
}

/// The two stores the list format cannot name: what a rule says about itself.
enum BCDRecommendedStores {
    /// "Epic" as a word of the id or the title, or a note that names an Epic Games Store copy ("the Epic Games Store copy", "Epic copies",
    /// "from the Epic store", "Epic version"); "GOG" as a word of the id, the title or a note. Not "Epic Online Services" or "the Epic login".
    private static let epicNote = try! NSRegularExpression(
        pattern: #"\bEpic (?:Games )?(?:Store|copy|copies|version)\b|\bfrom (?:the )?Epic\b|\bEpic'?s? (?:Store|copy|copies)\b|\bEpic/"#,
        options: [.caseInsensitive])
    private static let epicWord = try! NSRegularExpression(pattern: #"\bepic\b"#, options: [.caseInsensitive])
    private static let gogWord = try! NSRegularExpression(pattern: #"\bgog\b"#, options: [.caseInsensitive])

    static func names(_ store: BCDRecommendedStore, id: String, title: String, notes: [String]) -> Bool {
        let head = id.replacingOccurrences(of: "-", with: " ") + " " + title
        switch store {
        case .epic: return has(epicWord, head) || notes.contains { has(epicNote, $0) }
        case .gog: return has(gogWord, head) || notes.contains { has(gogWord, $0) }
        case .steam, .drmFree: return false
        }
    }

    private static func has(_ expression: NSRegularExpression, _ text: String) -> Bool {
        expression.firstMatch(in: text, options: [], range: NSRange(text.startIndex..., in: text)) != nil
    }
}

// MARK: - The list

/// Where the list in use came from, and which build reads it.
struct BCDRecommendedListInfo: Equatable {
    enum Origin: String, Equatable { case builtIn, downloaded }
    var version: Int
    /// "2026-11-22".
    var generated: String
    var origin: Origin
    /// The build that reads the list; nil when its number is unknown.
    var build: Int?
    /// A device with less than 10 GB of RAM (GameRecommendations.smallRAMDevice) gets the rules' `smallRAMLines`.
    var smallRAMDevice: Bool
    /// The rules came from the list the app applies, which holds only the lines this build has: no line's build limit is known.
    var buildFilteredOnly = false

    var sourceWords: String { origin == .builtIn ? "Built into this version" : "Downloaded" }
}

/// What the page filters and groups by one row at a time: precomputed, so a keystroke walks strings only.
struct BCDRecommendedCatalog {
    /// The rule every game without a rule of its own gets (GameRecommendations.standardID).
    static let standardID = "standard"

    struct Group: Identifiable, Equatable {
        /// "A" to "Z", "#", "Results" while a search is on, and "Every other game" for the standard rule.
        var title: String
        var rules: [Int]
        var id: String { title }
    }

    let info: BCDRecommendedListInfo
    /// In the page's order: the standard rule first, then by title.
    let rules: [BCDRecommendedRule]
    private let index: [String]       // folded title, id, programs, App IDs and folders
    private let titles: [String]      // folded title
    private let stores: [Set<BCDRecommendedStore>]
    private let positions: [String: Int]

    init(info: BCDRecommendedListInfo, rules unsorted: [BCDRecommendedRule]) {
        let keyed = unsorted.map { (rule: $0, title: Self.fold($0.displayTitle)) }
        let ordered = keyed.sorted { a, b in
            if a.rule.isStandard != b.rule.isStandard { return a.rule.isStandard }
            if a.title != b.title { return a.title < b.title }
            return a.rule.id < b.rule.id
        }
        self.info = info
        self.rules = ordered.map(\.rule)
        self.titles = ordered.map(\.title)
        self.index = ordered.map { entry in
            let rule = entry.rule
            let extra = [rule.id.replacingOccurrences(of: "-", with: " ")] + rule.programs.map(\.name)
                + rule.steamAppIDs.map(String.init) + rule.steamFolders
            return entry.title + " " + extra.map(Self.fold).joined(separator: " ")
        }
        self.stores = ordered.map { $0.rule.stores }
        var positions: [String: Int] = [:]
        for (i, entry) in ordered.enumerated() { positions[entry.rule.id] = i }
        self.positions = positions
    }

    var isEmpty: Bool { rules.isEmpty }

    func position(of id: String) -> Int? { positions[id] }
    func rule(id: String) -> BCDRecommendedRule? { positions[id].map { rules[$0] } }
    func stores(at position: Int) -> Set<BCDRecommendedStore> { stores[position] }

    // MARK: Search, filter, count, group

    /// The words a person typed, as the index holds its own: lower case, no accents, no apostrophes, other marks a space.
    static func fold(_ text: String) -> String {
        let plain = text.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
        var out = ""
        var spaced = true
        for character in plain {
            if character == "'" || character == "\u{2019}" { continue }
            if character.isLetter || character.isNumber {
                out.append(character)
                spaced = false
            } else if !spaced {
                out.append(" ")
                spaced = true
            }
        }
        if out.hasSuffix(" ") { out.removeLast() }
        return out
    }

    /// The rows that match, as positions in `rules`: in the page's order without a query; with one, the rows whose title starts with it first,
    /// then the rows with it inside the title, then the rows that have every word in the title, then the rest (a program or an App ID).
    func matches(filter: BCDRecommendedFilter = .all, query: String = "") -> [Int] {
        let wanted = Self.fold(query)
        let words = wanted.split(separator: " ").map(String.init)
        var hits: [(rank: Int, position: Int)] = []
        for position in rules.indices {
            if case .store(let store) = filter, !stores[position].contains(store) { continue }
            if words.isEmpty { hits.append((0, position)); continue }
            let haystack = index[position]
            guard words.allSatisfy({ haystack.contains($0) }) else { continue }
            let title = titles[position]
            let rank: Int
            if title.hasPrefix(wanted) { rank = 0 }
            else if title.contains(wanted) { rank = 1 }
            else if words.allSatisfy({ title.contains($0) }) { rank = 2 }
            else { rank = 3 }
            hits.append((rank, position))
        }
        if !words.isEmpty { hits.sort { ($0.rank, $0.position) < ($1.rank, $1.position) } }
        return hits.map(\.position)
    }

    /// How many rows each filter would show for this query.
    func counts(query: String = "") -> [BCDRecommendedFilter: Int] {
        var out: [BCDRecommendedFilter: Int] = [.all: 0]
        for store in BCDRecommendedStore.allCases { out[.store(store)] = 0 }
        for position in matches(filter: .all, query: query) {
            out[.all, default: 0] += 1
            for store in stores[position] { out[.store(store), default: 0] += 1 }
        }
        return out
    }

    /// The page's list: groups by first letter without a query, in this order: the standard rule alone ("Every other game"), "#" (a digit
    /// or another script), then A to Z, each group in title order; with a query, one group of results.
    func groups(filter: BCDRecommendedFilter = .all, query: String = "") -> [Group] {
        let positions = matches(filter: filter, query: query)
        guard !positions.isEmpty else { return [] }
        if !Self.fold(query).isEmpty { return [Group(title: "Results", rules: positions)] }
        var buckets: [String: [Int]] = [:]
        for position in positions {
            buckets[rules[position].isStandard ? Self.standardGroup : Self.letter(of: titles[position]), default: []].append(position)
        }
        let order = [Self.standardGroup, "#"] + (UInt8(ascii: "A")...UInt8(ascii: "Z")).map { String(UnicodeScalar($0)) }
        return order.compactMap { title in buckets[title].map { Group(title: title, rules: $0) } }
    }

    static let standardGroup = "Every other game"

    /// "A" to "Z" for a title that starts with a Latin letter; "#" for a digit or any other script.
    static func letter(of foldedTitle: String) -> String {
        guard let first = foldedTitle.unicodeScalars.first, first.value >= 97, first.value <= 122 else { return "#" }
        return String(Character(first)).uppercased()
    }

    // MARK: A rule's page

    /// The rule's lines with whether this build has each (a limited line only), in the file's order.
    func lineRows(_ rule: BCDRecommendedRule) -> [(line: BCDRecommendedLine, availability: BCDRecommendedAvailability)] {
        rule.lines.map { ($0, $0.availability(build: info.build)) }
    }

    func smallRAMRows(_ rule: BCDRecommendedRule) -> [(line: BCDRecommendedLine, availability: BCDRecommendedAvailability)] {
        rule.smallRAMLines.map { ($0, $0.availability(build: info.build)) }
    }

    /// Whether this build knows the rule at all (its `minBuild`).
    func availability(_ rule: BCDRecommendedRule) -> BCDRecommendedAvailability { rule.availability(build: info.build) }

    /// Which of the rule's setting lines this build writes, and which device-only lines this device adds, in the order the app writes
    /// them (RecommendationRule.recommendation, GameRecommendation.fileText): a rule this build does not know writes nothing.
    func written(_ rule: BCDRecommendedRule) -> [BCDRecommendedLine] {
        guard availability(rule).thisBuildHasIt else { return [] }
        let base = lineRows(rule).filter { $0.availability.thisBuildHasIt }.map(\.line)
        let small = info.smallRAMDevice ? smallRAMRows(rule).filter { $0.availability.thisBuildHasIt }.map(\.line) : []
        return (base + small).filter { !$0.isComment }
    }
}

// MARK: - Words the page shows

enum BCDRecommendedWords {
    static let title = "Recommended settings"
    static let summary = "Every game BCD has a list for"

    static let intro = "The settings BCD gives a game the first time you play it, and when you choose Reset to Recommended. Choose a game to see all its list holds. Nothing here can be changed."

    /// The list's version, its day and where it came from, one line.
    static func listLine(_ info: BCDRecommendedListInfo, rules: Int) -> String {
        "List \(info.version) of \(info.generated) · \(info.sourceWords) · \(rules) rules"
    }

    static func buildLine(_ info: BCDRecommendedListInfo) -> String {
        info.build.map { "This build is \($0)." } ?? "This build's number is unknown."
    }

    /// Under the filter chips.
    static let storeFooter = "Steam and DRM-free / any copy are what a rule matches. The list cannot name Epic or GOG: those two show the rules that say they were made for, or tested on, a copy from that store."

    static func smallRAMWords(thisDevice: Bool) -> String {
        "Only a device with 8 GB of RAM or less (under 10 GB installed) gets these lines, after the ones above. "
            + (thisDevice ? "This device is one, so it gets them." : "This device is not one, so it does not get them.")
    }

    static let buildFilteredNote = "These are the lines this build applies; the list's build limits are not shown."

    static func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }

    static func subtitle(_ rule: BCDRecommendedRule, stores: Set<BCDRecommendedStore>) -> String {
        var parts = BCDRecommendedStore.allCases.filter { stores.contains($0) }.map(\.title)
        if rule.isStandard { parts = ["Every other game"] }
        parts.append(count(rule.settingLines.count, "line"))
        return parts.joined(separator: " · ")
    }
}

// MARK: - A switch's words

enum BCDRecommendedSwitches {
    /// The switches a rule sets outside its config file, as the game's settings page names them (GameRecommendation.summary).
    static func list(avx: Bool?, nvidia: Bool?, wineVCRT: Bool?, fastSync: Bool?, semaphoreFastPath: Bool?, fpsMode: Int?,
                     resolution: String?, display: String?) -> [BCDRecommendedRule.Switch] {
        var out: [BCDRecommendedRule.Switch] = []
        func onOff(_ flag: Bool) -> String { flag ? "On" : "Off" }
        if let avx { out.append(.init(label: "AVX", value: onOff(avx))) }
        if let nvidia { out.append(.init(label: "Report an NVIDIA GPU", value: onOff(nvidia))) }
        if let wineVCRT { out.append(.init(label: "Wine's C++ runtime", value: onOff(wineVCRT))) }
        if let fastSync { out.append(.init(label: "Fast synchronization", value: onOff(fastSync))) }
        if let semaphoreFastPath { out.append(.init(label: "Fast semaphore waits", value: onOff(semaphoreFastPath))) }
        if let fpsMode { out.append(.init(label: "FPS limit", value: fps(fpsMode))) }
        if let resolution { out.append(.init(label: "Resolution", value: resolutionWords(resolution))) }
        if let display { out.append(.init(label: "Aspect & scaling", value: displayWords(display))) }
        return out
    }

    /// LibraryEntry.fpsMode: 1 = 60, 3 = 30, 4 = 40, 0 = display maximum, 2 = uncapped.
    static func fps(_ mode: Int) -> String {
        [1: "60", 3: "30", 4: "40", 0: "Display maximum", 2: "Uncapped"][mode] ?? "\(mode)"
    }

    /// "1408x648" as "1408 × 648"; "screen@720" (GameRecommendation.screen720) as this screen's shape at 720 lines.
    static func resolutionWords(_ value: String) -> String {
        if value == "screen@720" { return "This screen's shape at 720 lines" }
        return value.replacingOccurrences(of: "x", with: " × ")
    }

    /// DisplayMode's labels (GuestDisplay.swift).
    static func displayWords(_ raw: String) -> String {
        ["fit": "Fit", "fill": "Fill", "stretch": "Stretch", "aspect": "Aspect"][raw] ?? raw
    }
}
