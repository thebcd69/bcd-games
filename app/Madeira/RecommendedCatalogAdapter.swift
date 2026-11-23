// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  RecommendedCatalogAdapter.swift
//  Madeira
//
//  Settings › Recommended settings (BCDRecommendedCatalog.swift): the catalog's rules from the list's own types
//  (GameRecommendationsList.swift). Foundation only, so tests/host/check-recommended-catalog.py compiles it with the
//  real recommended/recommendations.json. It is not a BCD*.swift file, so the preview build leaves it out
//  (tools/ui-preview.py builds BCD*.swift only); BCDRecommendedCatalogLive.swift is the one caller.
//
//  Two ways in:
//    - the list's file (RecommendationsFile): every rule, every line with its `minBuild` / `maxBuild`, the notes. What the
//      page is meant to show;
//    - the list the app applies (GameRecommendationList), for the rare run in which that file cannot be read again (a
//      download that went into use but could not be kept): the rules the build knows, their lines already cut to this
//      build, no notes. The catalog says so (`buildFilteredOnly`).

import Foundation

extension BCDRecommendedCatalog {
    /// The whole file as a catalog.
    static func make(from file: RecommendationsFile, origin: BCDRecommendedListInfo.Origin, build: Int?,
                     smallRAMDevice: Bool) -> BCDRecommendedCatalog {
        let info = BCDRecommendedListInfo(version: file.version, generated: file.generated, origin: origin, build: build,
                                          smallRAMDevice: smallRAMDevice)
        return BCDRecommendedCatalog(info: info, rules: file.rules.map(rule))
    }

    /// The list the app applies, when its file is not at hand.
    static func make(from list: GameRecommendationList, build: Int?, smallRAMDevice: Bool) -> BCDRecommendedCatalog {
        let info = BCDRecommendedListInfo(version: list.version, generated: list.generated,
                                          origin: list.origin == .builtIn ? .builtIn : .downloaded, build: build,
                                          smallRAMDevice: smallRAMDevice, buildFilteredOnly: true)
        return BCDRecommendedCatalog(info: info, rules: list.rules.map(rule))
    }

    // MARK: One rule

    private static func rule(_ rule: RecommendationRule) -> BCDRecommendedRule {
        var out = BCDRecommendedRule(id: rule.id, title: rule.title, version: rule.version, minBuild: rule.minBuild)
        if let match = rule.match { apply(match, to: &out) }
        out.lines = rule.config.map(line)
        out.smallRAMLines = (rule.smallRAMConfig ?? []).map(line)
        out.switches = switches(rule.switches)
        out.notes = rule.notes
        return out
    }

    private static func rule(_ entry: GameRecommendationList.Rule) -> BCDRecommendedRule {
        let recommendation = entry.recommendation
        var out = BCDRecommendedRule(id: recommendation.id, title: recommendation.title, version: recommendation.version)
        if let match = entry.match { apply(match, to: &out) }
        out.lines = recommendation.config.split(separator: "\n", omittingEmptySubsequences: true).map { BCDRecommendedLine(text: String($0)) }
        out.smallRAMLines = (recommendation.smallRAMConfig ?? "").split(separator: "\n", omittingEmptySubsequences: true)
            .map { BCDRecommendedLine(text: String($0)) }
        out.switches = BCDRecommendedSwitches.list(avx: recommendation.avx, nvidia: recommendation.nvidia, wineVCRT: recommendation.wineVCRT,
                                                   fastSync: recommendation.fastSync, semaphoreFastPath: recommendation.semaphoreFastPath,
                                                   fpsMode: recommendation.fpsMode, resolution: recommendation.resolution,
                                                   display: recommendation.display)
        return out
    }

    private static func apply(_ match: GameRecommendationMatch, to rule: inout BCDRecommendedRule) {
        rule.steamAppIDs = match.steamAppIDs
        rule.steamFolders = match.steamFolders
        rule.programs = match.programs.map {
            BCDRecommendedRule.Program(name: $0.name, steam: $0.steam, beside: $0.beside, notBeside: $0.notBeside)
        }
    }

    private static func line(_ line: RecommendationLine) -> BCDRecommendedLine {
        BCDRecommendedLine(text: line.text, minBuild: line.minBuild, maxBuild: line.maxBuild)
    }

    private static func switches(_ s: GameRecommendationSwitches) -> [BCDRecommendedRule.Switch] {
        BCDRecommendedSwitches.list(avx: s.avx, nvidia: s.nvidia, wineVCRT: s.wineVCRT, fastSync: s.fastSync,
                                    semaphoreFastPath: s.semaphoreFastPath, fpsMode: s.fpsMode, resolution: s.resolution,
                                    display: s.display)
    }
}

// MARK: - The list in use

/// Which file the catalog is made from: the one the list in use came from, checked against it.
enum BCDRecommendedCatalogSource {
    /// The catalog of the list the app applies now (`active`). The list's own file is read again (the built-in one, or the kept
    /// download, checked as at its arrival) and used when it is the very list in use: the same version, and the rules this build knows are
    /// the rules `active` holds, in order. Otherwise (no file, or a download that went into use without being kept) the catalog is made
    /// from `active` itself, with the lines it already cut to this build.
    static func load(active: GameRecommendationList = GameRecommendations.active,
                     builtIn: () -> RecommendationsFile? = { RecommendationsStore.builtInFile() },
                     kept: () -> RecommendationsFile? = { keptFile() },
                     build: Int? = RecommendationsStore.build,
                     smallRAMDevice: Bool = GameRecommendations.smallRAMDevice) -> BCDRecommendedCatalog {
        let file: RecommendationsFile?
        switch active.origin {
        case .builtIn: file = builtIn()
        case .downloaded: file = kept()
        }
        if let file, isTheList(file, active: active, build: build) {
            return .make(from: file, origin: active.origin == .builtIn ? .builtIn : .downloaded, build: build, smallRAMDevice: smallRAMDevice)
        }
        return .make(from: active, build: build, smallRAMDevice: smallRAMDevice)
    }

    /// The kept download, as RecommendationsStore.initialList checks it; nil when there is none or it does not check out.
    static func keptFile() -> RecommendationsFile? {
        guard let download = RecommendationsStore.kept() else { return nil }
        return try? RecommendationsStore.check(json: download.json, signature: download.signature)
    }

    static func isTheList(_ file: RecommendationsFile, active: GameRecommendationList, build: Int?) -> Bool {
        file.version == active.version
            && file.rules.filter { $0.applies(build: build) }.map(\.id) == active.rules.map { $0.recommendation.id }
    }
}
