// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
// The recommended-settings catalog (BCDRecommendedCatalog.swift and RecommendedCatalogAdapter.swift) over the committed
// recommended/recommendations.json, with the app's own list code (GameRecommendationsList.swift) beside it. tests/host/check-recommended-catalog.py
// compiles this file as main.swift with those files and a stub LogStore, and hands it the JSON and what Python worked out on its own.

import Foundation

var failures = 0
var passed = 0
func check(_ ok: Bool, _ what: String, _ detail: @autoclosure () -> String = "", line: Int = #line) {
    if ok { passed += 1; print("ok   " + what) } else { failures += 1; print("FAIL " + what + " (line \(line))" + (detail().isEmpty ? "" : "\n     " + detail())) }
}

let env = ProcessInfo.processInfo.environment
let data = try! Data(contentsOf: URL(fileURLWithPath: env["BCD_RECS_JSON"]!))
let expected = try! JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: env["BCD_RECS_EXPECTED"]!))) as! [String: Any]
let file = try! RecommendationsFile.parse(data)

func now() -> Double { Date().timeIntervalSince1970 }

// MARK: - Build it

let t0 = now()
let catalog = BCDRecommendedCatalog.make(from: file, origin: .builtIn, build: 708, smallRAMDevice: false)
let buildSeconds = now() - t0
check(catalog.rules.count == file.rules.count && catalog.rules.count == expected["rules"] as! Int,
      "every rule of the list is in the catalog (\(catalog.rules.count))")
check(catalog.info.version == expected["version"] as! Int && catalog.info.generated == (expected["generated"] as! String)
      && catalog.info.origin == .builtIn && catalog.info.build == 708 && !catalog.info.buildFilteredOnly,
      "the list's version, day, source and this build are kept")
check(catalog.rules[0].isStandard && catalog.rules.filter(\.isStandard).count == 1 && catalog.rules[0].displayTitle.hasPrefix("Games without"),
      "the standard rule is first and its title starts with a capital")
check(Set(catalog.rules.map(\.id)).count == catalog.rules.count && catalog.rules.allSatisfy { catalog.position(of: $0.id).map { catalog.rules[$0].id } == $0.id },
      "ids are unique and position(of:) finds each rule")
let byID = Dictionary(uniqueKeysWithValues: file.rules.map { ($0.id, $0) })
check(catalog.rules.allSatisfy { rule in
    let source = byID[rule.id]!
    return rule.title == source.title && rule.version == source.version && rule.minBuild == source.minBuild
        && rule.lines.map(\.text) == source.config.map(\.text) && rule.lines.map(\.minBuild) == source.config.map(\.minBuild)
        && rule.lines.map(\.maxBuild) == source.config.map(\.maxBuild) && rule.notes == source.notes
        && rule.smallRAMLines.map(\.text) == (source.smallRAMConfig ?? []).map(\.text)
        && rule.steamAppIDs == (source.match?.steamAppIDs ?? []) && rule.steamFolders == (source.match?.steamFolders ?? [])
        && rule.programs.map(\.name) == (source.match?.programs ?? []).map(\.name)
}, "every rule carries its title, version, lines with their builds, small-RAM lines, match and notes exactly as the file has them")

// MARK: - Stores

let storeIDs = expected["stores"] as! [String: [String]]
for store in BCDRecommendedStore.allCases {
    let ids = Set(catalog.matches(filter: .store(store)).map { catalog.rules[$0].id })
    check(ids == Set(storeIDs[store.rawValue]!), "\(store.title): the rules in it are the ones worked out apart (\(ids.count))",
          "swift only: \(ids.subtracting(Set(storeIDs[store.rawValue]!)).sorted().prefix(5)) python only: \(Set(storeIDs[store.rawValue]!).subtracting(ids).sorted().prefix(5))")
}
let counts = catalog.counts()
check(counts[.all] == catalog.rules.count && BCDRecommendedStore.allCases.allSatisfy { counts[.store($0)] == storeIDs[$0.rawValue]!.count },
      "the filter chips' counts are those sizes")
check(catalog.rules.filter { catalog.stores(at: catalog.position(of: $0.id)!).count >= 2 }.count > 100, "one rule can be in several stores (more than 100 are)")
check(catalog.rules.filter(\.isStandard).allSatisfy { catalog.stores(at: catalog.position(of: $0.id)!).isEmpty }, "the standard rule is in no store")
let stores = Dictionary(uniqueKeysWithValues: catalog.rules.map { ($0.id, $0.stores) })
check(stores["rdr2-steam"]?.contains(.steam) == true && stores["rdr2-steam"]?.contains(.drmFree) == false, "RDR2 from Steam is in Steam only by its match")
check(stores["rdr2-other"]?.contains(.drmFree) == true && stores["rdr2-other"]?.contains(.steam) == false, "RDR2 outside Steam is DRM-free / any copy and not Steam")
check(stores["bioshock-remastered-epic"]?.contains(.epic) == true && stores["bioshock-remastered-epic"]?.contains(.drmFree) == true,
      "BioShock Remastered (Epic) is in Epic and, matching by program outside Steam, in DRM-free / any copy")
check(stores["chuchel"]?.contains(.epic) == false, "a note about the Epic Online Services DLL does not make a rule Epic's")
// the names rule, on its own
check(BCDRecommendedStores.names(.epic, id: "x-epic", title: "X", notes: []) && BCDRecommendedStores.names(.epic, id: "x", title: "X (Epic)", notes: [])
      && BCDRecommendedStores.names(.epic, id: "x", title: "X", notes: ["tested on the Epic Games Store copy"])
      && BCDRecommendedStores.names(.epic, id: "x", title: "X", notes: ["Epic copies, Send Logs"])
      && BCDRecommendedStores.names(.epic, id: "x", title: "X", notes: ["from the Epic store"])
      && BCDRecommendedStores.names(.epic, id: "x", title: "X", notes: ["the Epic/DRM-free copy"])
      && !BCDRecommendedStores.names(.epic, id: "x", title: "X", notes: ["the Epic Online Services DLL", "the Epic login by browser", "epicenter"])
      && !BCDRecommendedStores.names(.epic, id: "x", title: "Epicurus", notes: []),
      "Epic: the id or title word, or a note about an Epic copy; nothing else")
check(BCDRecommendedStores.names(.gog, id: "x", title: "X", notes: ["a GOG copy"]) && BCDRecommendedStores.names(.gog, id: "gog-x", title: "X", notes: [])
      && !BCDRecommendedStores.names(.gog, id: "x", title: "Gogol", notes: ["gogh"]) && !BCDRecommendedStores.names(.steam, id: "x", title: "GOG", notes: ["GOG"]),
      "GOG: the word in the id, the title or a note; nothing else")

// MARK: - Search

func ids(_ positions: [Int]) -> [String] { positions.map { catalog.rules[$0].id } }
let all = catalog.matches()
check(all == Array(catalog.rules.indices), "no query: every rule, in the page's order")
var roundTrip = true, upper = true, folded = true
for (position, rule) in catalog.rules.enumerated() {
    if !catalog.matches(query: rule.displayTitle).contains(position) { roundTrip = false }
    if !catalog.matches(query: rule.displayTitle.uppercased()).contains(position) { upper = false }
    if !catalog.matches(query: rule.displayTitle.folding(options: .diacriticInsensitive, locale: nil)).contains(position) { folded = false }
}
check(roundTrip, "searching a rule's own title finds it (all \(catalog.rules.count))")
check(upper, "... in capitals too")
check(folded, "... without its accents too")
check(ids(catalog.matches(query: "1174180")).contains("rdr2-steam"), "a Steam App ID finds its rule")
check(Set(["rdr2-steam", "rdr2-other"]).isSubset(of: Set(ids(catalog.matches(query: "rdr2.exe")))), "a program name finds its rules")
check(ids(catalog.matches(query: "red dead")).prefix(2).allSatisfy { $0.hasPrefix("rdr") }, "\"red dead\" lists the two Red Dead Redemption 2 rules first")
check(catalog.matches(query: "qzxqzxqzx").isEmpty && catalog.matches(query: "   ").count == catalog.rules.count, "nothing matches nonsense; blanks are no query")
if let apostrophe = catalog.rules.first(where: { $0.title.contains("'") }) {
    let plain = apostrophe.title.replacingOccurrences(of: "'", with: "").lowercased()
    check(catalog.matches(query: plain).contains(catalog.position(of: apostrophe.id)!), "an apostrophe is not needed: \"\(plain)\" finds \(apostrophe.id)")
}
check(catalog.matches(query: "red  DEAD\t redemption").contains(catalog.position(of: "rdr2-steam")!), "several words, any case, any spacing: all must match")
check(catalog.matches(filter: .store(.gog), query: "cyberpunk").count == 1 && catalog.matches(filter: .store(.gog), query: "hollow").isEmpty, "filter and search together")
let both = catalog.matches(filter: .store(.epic), query: "bioshock")
check(both.allSatisfy { catalog.stores(at: $0).contains(.epic) && catalog.rules[$0].title.lowercased().contains("bioshock") } && !both.isEmpty, "Epic and \"bioshock\" together: only Epic's BioShock rules")
let queryCounts = catalog.counts(query: "bioshock")
check(queryCounts[.all] == catalog.matches(query: "bioshock").count && queryCounts[.store(.epic)] == both.count, "the counts follow the query")

// ranking, on a small catalog whose order is known
func rule(_ id: String, _ title: String, programs: [String] = [], appIDs: [Int] = []) -> BCDRecommendedRule {
    BCDRecommendedRule(id: id, title: title, version: 1, steamAppIDs: appIDs, programs: programs.map { .init(name: $0) }, lines: [.init(text: "a = 1")])
}
let tiny = BCDRecommendedCatalog(info: BCDRecommendedListInfo(version: 1, generated: "2026-01-01", origin: .downloaded, build: nil, smallRAMDevice: false),
                                 rules: [rule("zed", "Zero Alpha"), rule("beta", "Beta", programs: ["alpha.exe"]), rule("alpha", "Alpha Zero"),
                                         rule("standard", "games without a list of their own"), rule("digit", "7 Days"), rule("kana", "ノラ")])
check(tiny.matches(query: "alpha").map { tiny.rules[$0].id } == ["alpha", "zed", "beta"], "a title that starts with the words first, then one that has them, then a program or ID match",
      "\(tiny.matches(query: "alpha").map { tiny.rules[$0].id })")
check(tiny.groups().map(\.title) == ["Every other game", "#", "A", "B", "Z"], "groups: the standard rule, \"#\" once for digits and other scripts, then letters",
      "\(tiny.groups().map(\.title))")
check(tiny.groups().first { $0.title == "#" }.map { $0.rules.map { tiny.rules[$0].id } } == ["digit", "kana"], "\"#\" holds the digit and the other script in title order")

// MARK: - Groups

let groups = catalog.groups()
let flat = groups.flatMap(\.rules)
check(Set(flat).count == flat.count && flat.count == catalog.rules.count, "the groups hold every rule once")
check(Set(groups.map(\.title)).count == groups.count && groups.first?.title == "Every other game", "group titles are unique, the standard rule's group first",
      "\(groups.map(\.title))")
let letters = groups.map(\.title).dropFirst()
check(Array(letters) == letters.sorted { a, b in (a == "#" ? "" : a) < (b == "#" ? "" : b) }, "\"#\" then A to Z in order")
check(groups.dropFirst().allSatisfy { group in group.rules == group.rules.sorted() }, "inside a group the rules are in title order")
check(catalog.groups(filter: .store(.gog)).flatMap(\.rules).count == storeIDs["gog"]!.count, "a store's groups hold its rules")
check(catalog.groups(query: "red").map(\.title) == ["Results"], "a search is one group of results")
check(catalog.groups(query: "qzxqzx").isEmpty, "no result, no group")
check(BCDRecommendedCatalog.letter(of: "abc") == "A" && BCDRecommendedCatalog.letter(of: "1000x") == "#" && BCDRecommendedCatalog.letter(of: "ノラ") == "#"
      && BCDRecommendedCatalog.letter(of: "") == "#", "the letter of a title")

// MARK: - Which build has a line

let a = BCDRecommendedAvailability.self
check(a.of(minBuild: nil, maxBuild: nil, build: 700) == .everyBuild && a.of(minBuild: nil, maxBuild: nil, build: nil) == .everyBuild, "no limit: every build, even when this build's number is unknown")
check(a.of(minBuild: 665, maxBuild: nil, build: 665) == .has(from: 665, to: nil) && a.of(minBuild: 665, maxBuild: nil, build: 664) == .notYet(from: 665)
      && a.of(minBuild: nil, maxBuild: 590, build: 590) == .has(from: nil, to: 590) && a.of(minBuild: nil, maxBuild: 590, build: 591) == .ended(to: 590)
      && a.of(minBuild: 600, maxBuild: 650, build: 650) == .has(from: 600, to: 650) && a.of(minBuild: 600, maxBuild: 650, build: 599) == .notYet(from: 600)
      && a.of(minBuild: 600, maxBuild: 650, build: nil) == .unknownBuild(from: 600, to: 650), "the ends are inside; an unknown build gets no limited line")
check(a.of(minBuild: 665, maxBuild: nil, build: 700).words == "From build 665 · This build has it"
      && a.of(minBuild: 665, maxBuild: nil, build: 600).words == "From build 665 · This build is older and does not have it"
      && a.of(minBuild: nil, maxBuild: 590, build: 600).words == "Up to build 590 · This build is newer and does not have it"
      && a.of(minBuild: 600, maxBuild: 650, build: 620).words == "Builds 600 to 650 · This build has it"
      && a.of(minBuild: 600, maxBuild: 600, build: 600).words == "Build 600 only · This build has it"
      && a.of(minBuild: nil, maxBuild: nil, build: 1).words == "Every build"
      && a.of(minBuild: 600, maxBuild: nil, build: nil).words == "From build 600 · This build's number is unknown, so it gets no line like this",
      "the words")

// The page agrees with the app: the same lines, line for line, for every rule, build and device.
var agreed = true, ruleAgreed = true, lineAgreed = true
var comparisons = 0
let savedRAM = GameRecommendations.deviceRAMBytes
for small in [false, true] {
    GameRecommendations.deviceRAMBytes = small ? 8 << 30 : 16 << 30
    for build in [nil, 100, 560, 600, 632, 650, 665, 700, 708, 5000] as [Int?] {
        let c = BCDRecommendedCatalog.make(from: file, origin: .builtIn, build: build, smallRAMDevice: GameRecommendations.smallRAMDevice)
        for source in file.rules {
            let mine = c.rule(id: source.id)!
            if c.availability(mine).thisBuildHasIt != source.applies(build: build) { ruleAgreed = false }
            let app = source.applies(build: build)
                ? source.recommendation(build: build).fileText.split(separator: "\n").map(String.init).filter { !$0.hasPrefix("#") && !$0.isEmpty }
                : []
            if c.written(mine).map(\.text) != app { agreed = false; print("     differs: \(source.id) build \(String(describing: build)) small \(small)") }
            for (index, row) in c.lineRows(mine).enumerated() where row.availability.thisBuildHasIt != source.config[index].applies(build: build) { lineAgreed = false }
            for (index, row) in c.smallRAMRows(mine).enumerated() where row.availability.thisBuildHasIt != source.smallRAMConfig![index].applies(build: build) { lineAgreed = false }
            comparisons += 1
        }
    }
}
GameRecommendations.deviceRAMBytes = savedRAM
check(ruleAgreed, "whether this build knows a rule is RecommendationRule.applies, for every rule at ten builds (\(comparisons / 2) rules and builds)")
check(lineAgreed, "whether this build has a line is RecommendationLine.applies, for every line, at ten builds, on both device sizes")
check(agreed, "the lines the page says this build writes are the lines the app writes (RecommendationRule.recommendation, fileText), for every rule, build and device size")
let limited = catalog.rules.filter { $0.limitedLineCount > 0 }
check(limited.count > 20, "many rules carry lines for some builds only (\(limited.count))")
if let rdr = catalog.rule(id: "rdr2-steam") {
    let rows = catalog.lineRows(rdr)
    check(rows.contains { $0.availability.limit != nil && $0.availability.thisBuildHasIt }, "RDR2 (Steam) has limited lines this build (708) has")
    check(rdr.needsBuild != nil, "needsBuild is the highest build any line asks for (\(String(describing: rdr.needsBuild)))")
}
let future = catalog.rules.first { $0.minBuild != nil }
if let future {
    let early = BCDRecommendedCatalog.make(from: file, origin: .builtIn, build: (future.minBuild ?? 1) - 1, smallRAMDevice: false)
    check(early.availability(early.rule(id: future.id)!) == .notYet(from: future.minBuild!) && early.written(early.rule(id: future.id)!).isEmpty,
          "a rule from a later build writes nothing on an earlier one (\(future.id))")
}
check(catalog.written(catalog.rule(id: "standard")!).map(\.text) == ["env.MADEIRA_EC_HOOK_TRACE = 0"], "the standard rule writes its one line")

// MARK: - Match words and switches

let match = catalog.rule(id: "rdr2-steam")!.matchWords
check(match.first == "Steam App ID 1174180" && match.contains("Steam folder \u{201C}red dead redemption 2\u{201D} (under steamapps\\common)")
      && match.contains("Program rdr2.exe, only inside a Steam library"), "how RDR2 (Steam) recognises the game", "\(match)")
check(catalog.rule(id: "standard")!.matchWords == ["Every game that has no list of its own gets this one."], "how the standard rule does")
check(BCDRecommendedRule.words(for: .init(name: "a.exe", steam: false, beside: "b.exe", notBeside: "c.exe"))
      == "Program a.exe, only outside a Steam library, only when b.exe is in the same folder, only when c.exe is not in the same folder"
      && BCDRecommendedRule.words(for: .init(name: "a.exe")) == "Program a.exe, wherever it is installed", "a program's words: Steam flag, beside, not beside")
check(catalog.rules.filter { !$0.isStandard }.allSatisfy { !$0.matchWords.isEmpty }, "every rule says how it matches")
let switchExpected = expected["switches"] as! [String: [String]]
check(catalog.rules.allSatisfy { $0.switches.map { "\($0.label)=\($0.value)" } == (switchExpected[$0.id] ?? []) }, "the switches are the ones worked out apart, with their labels and values (\(switchExpected.count) rules have some)")
check(BCDRecommendedSwitches.fps(1) == "60" && BCDRecommendedSwitches.fps(3) == "30" && BCDRecommendedSwitches.fps(4) == "40"
      && BCDRecommendedSwitches.fps(0) == "Display maximum" && BCDRecommendedSwitches.fps(2) == "Uncapped",
      "FPS limit values are LibraryEntry.fpsMode's")
check(BCDRecommendedSwitches.resolutionWords("1408x648") == "1408 × 648" && BCDRecommendedSwitches.resolutionWords("screen@720") == "This screen's shape at 720 lines"
      && BCDRecommendedSwitches.displayWords("aspect") == "Aspect", "resolution and scaling words")

// MARK: - The list in use

let work = FileManager.default.temporaryDirectory.appendingPathComponent("bcd-catalog-\(UUID().uuidString)", isDirectory: true)
try! FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
RecommendationsStore.builtInURL = URL(fileURLWithPath: env["BCD_RECS_JSON"]!)
RecommendationsStore.directory = work
RecommendationsStore.build = 708
GameRecommendations.reload()
let active = GameRecommendations.active
check(active.origin == .builtIn && active.version == file.version, "the app's list in use is the built-in one (version \(active.version))")
let live = BCDRecommendedCatalogSource.load(active: active)
check(live.rules.count == file.rules.count && !live.info.buildFilteredOnly && live.info.origin == .builtIn && live.info.build == 708,
      "the catalog of the list in use is the whole file (\(live.rules.count) rules), with their build limits")
check(live.rules.contains { $0.notes.count > 0 } && live.rule(id: "rdr2-steam")!.lines.contains { $0.minBuild != nil }, "... with the notes and the lines' builds")
check(file.rules.filter { $0.applies(build: 708) }.count == active.rules.count, "the rules this build knows are the rules the app applies (\(active.rules.count)); a later rule is in the catalog, not in the list in use")
let fallback = BCDRecommendedCatalogSource.load(active: active, builtIn: { nil }, build: 708, smallRAMDevice: false)
check(fallback.info.buildFilteredOnly && fallback.rules.count == active.rules.count && fallback.rules.allSatisfy { $0.notes.isEmpty },
      "no file to read again: the catalog is made from the list in use, says its lines are cut to this build, holds no notes")
let fallbackRule = fallback.rule(id: "rdr2-steam")!
check(fallback.written(fallbackRule).map(\.text) == catalog.written(catalog.rule(id: "rdr2-steam")!).map(\.text) && fallbackRule.lines.allSatisfy { $0.minBuild == nil && $0.maxBuild == nil },
      "... and its lines are exactly the ones the app writes")
check(fallback.rules.allSatisfy { r in
    let w = active.rules.first { $0.recommendation.id == r.id }!
    return r.version == w.recommendation.version && r.title == w.recommendation.title
},
      "... with the list's versions and titles")
var newer = file
newer.version += 1
check(BCDRecommendedCatalogSource.load(active: active, builtIn: { newer }, build: 708, smallRAMDevice: false).info.buildFilteredOnly,
      "a file of another version is not the list in use: the catalog falls back to the list")
var fewer = file
fewer.rules.removeAll { $0.id == "hollow-knight" }
check(BCDRecommendedCatalogSource.load(active: active, builtIn: { fewer }, build: 708, smallRAMDevice: false).info.buildFilteredOnly,
      "... and so is a file whose rules differ from the list's")
let downloadedActive = file.list(build: 708, origin: .downloaded)
let fromKept = BCDRecommendedCatalogSource.load(active: downloadedActive, builtIn: { nil }, kept: { file }, build: 708, smallRAMDevice: false)
check(fromKept.info.origin == .downloaded && !fromKept.info.buildFilteredOnly && fromKept.rules.count == file.rules.count, "a downloaded list's catalog is made from the kept download")
check(BCDRecommendedCatalogSource.load(active: downloadedActive, builtIn: { file }, kept: { nil }, build: 708, smallRAMDevice: false).info.buildFilteredOnly,
      "a downloaded list with no kept file falls back to the list in use, never to the built-in file")
let none = BCDRecommendedCatalogSource.load(active: .empty, builtIn: { nil }, kept: { nil }, build: 708, smallRAMDevice: false)
check(none.isEmpty && none.groups().isEmpty && none.matches(query: "x").isEmpty, "no list at all: an empty catalog")
check(BCDRecommendedCatalogSource.isTheList(file, active: active, build: 708) && !BCDRecommendedCatalogSource.isTheList(file, active: active, build: 100),
      "the file is the list in use at the build it was cut for, and not at another")
try? FileManager.default.removeItem(at: work)

// MARK: - Words

check(BCDRecommendedWords.listLine(catalog.info, rules: 373) == "List \(catalog.info.version) of \(catalog.info.generated) · Built into this version · 373 rules"
      && BCDRecommendedWords.count(1, "rule") == "1 rule" && BCDRecommendedWords.count(2, "line") == "2 lines" && BCDRecommendedWords.buildLine(catalog.info) == "This build is 708."
      && BCDRecommendedWords.buildLine(BCDRecommendedListInfo(version: 1, generated: "x", origin: .builtIn, build: nil, smallRAMDevice: false)) == "This build's number is unknown.",
      "the list line, counts and the build line")
check(BCDRecommendedWords.smallRAMWords(thisDevice: true).hasSuffix("This device is one, so it gets them.")
      && BCDRecommendedWords.smallRAMWords(thisDevice: false).hasSuffix("This device is not one, so it does not get them.")
      && BCDRecommendedWords.smallRAMWords(thisDevice: true).contains("under 10 GB installed"), "the small-RAM words say whether this device gets them")
let rdr = catalog.rule(id: "rdr2-steam")!
check(BCDRecommendedWords.subtitle(rdr, stores: catalog.stores(at: catalog.position(of: "rdr2-steam")!)).hasPrefix("Steam")
      && BCDRecommendedWords.subtitle(catalog.rule(id: "standard")!, stores: []).hasPrefix("Every other game"), "a row's subtitle names its stores and line count")
let own = BCDRecommendedStore.allCases.map(\.meaning) + BCDRecommendedStore.allCases.map(\.title)
    + [BCDRecommendedWords.intro, BCDRecommendedWords.storeFooter, BCDRecommendedWords.buildFilteredNote, BCDRecommendedWords.title, BCDRecommendedWords.summary,
       BCDRecommendedWords.smallRAMWords(thisDevice: true), BCDRecommendedWords.smallRAMWords(thisDevice: false)]
    + catalog.rules.flatMap { $0.matchWords }
check(!own.isEmpty && !own.contains { $0.localizedCaseInsensitiveContains("madeira") || $0.contains("@") || $0.contains("http") },
      "the page's own words and the match words name no old name, no address, no e-mail")

// MARK: - Speed

let t1 = now()
var searched = 0
for rule in catalog.rules {
    searched += catalog.matches(query: rule.displayTitle).count
    searched += catalog.counts(query: String(rule.displayTitle.prefix(3))).count
    searched += catalog.groups(filter: .store(.steam), query: String(rule.displayTitle.prefix(2))).count
}
let searchSeconds = now() - t1
print(String(format: "     build %.0f ms; %d searches with counts and groups %.0f ms (%.2f ms each)", buildSeconds * 1000, catalog.rules.count * 3, searchSeconds * 1000,
             searchSeconds * 1000 / Double(catalog.rules.count * 3)))
check(buildSeconds < 2.0 && searchSeconds < 10.0 && searched > 0, "the catalog builds in well under 2 s and a search is a few milliseconds")

print("recommended catalog: \(passed) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
