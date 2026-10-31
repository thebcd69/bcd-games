// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  GameRecommendationsList.swift
//  Madeira
//
//  BCD: the recommended settings as data (owner's decision, 2026-10-18). The lists live in
//  recommended/recommendations.json. Every build bundles that file; BCD's public repository
//  serves the newest one with an Ed25519 signature, and GameRecommendationsSource.swift fetches
//  it, checks it and keeps it. This file reads the format, checks it and turns it into the
//  GameRecommendation values GameRecommendationsApply.swift writes into a game's settings.
//  Foundation only: tests/host/check-remote-recommendations.py, check-game-recommendations.py
//  and tools/publish-recommendations.py compile it.
//
//  The file (format 1):
//
//    "format": 1                 the layout below; an app reads only the formats it knows
//    "version": 7                the whole list's version, raised with every change; an app
//                                uses the newer of its built-in list and the last good download
//    "generated": "2026-10-18"   the day it was made
//    "rules": [ ... ]            in order: the first rule that matches a game is its list
//
//  A rule:
//
//    "id": "rdr2-steam"          stable: the record of what was applied keys on it
//    "title": "..."              shown under Reset to Recommended
//    "version": 10               raised whenever its lines, switches or match change
//    "minBuild": 575             optional: builds before 575 do not know the rule
//    "match": {                  how the game is recognised; every rule but "standard" has one
//      "steamAppIDs": [1174180]              a library Steam game's App ID
//      "steamFolders": ["red dead redemption 2"]
//                                            its folder right under steamapps\common, lowercased
//      "programs": [{"name": "rdr2.exe", "steam": true, "beside": "X.exe", "notBeside": "Y.exe"}]
//                                            a program by its lowercased file name, wherever it is;
//                                            "steam" true: only inside a Steam library (or with an
//                                            App ID), false: only outside one, left out: anywhere;
//                                            "beside" / "notBeside": a file that must / must not be
//                                            in the program's folder
//    }
//    "config": ["# comment", "key = value", {"line": "key = value", "minBuild": 575, "maxBuild": 590}]
//                                the game's config file, line by line; a line as an object goes
//                                only to the builds it names (both ends included)
//    "smallRAMConfig": [...]     optional: lines after config on a device with less than 12 GB
//    "switches": {"avx", "nvidia", "wineVCRT", "fastSync", "semaphoreFastPath": true/false,
//                 "fpsMode": 0-4, "resolution": "WxH" or "screen@720", "display": "fit"...}
//    "notes": ["..."]            why, from which build and log; never shown, never applied
//
//  The rule "standard" has no match: every game without a rule of its own gets it (the
//  Desktop none). An unknown field anywhere makes the whole file invalid: a field this build
//  does not understand is never half-applied. A downloaded file may set only the keys
//  RecommendationsPolicy allows (the settings catalog's game settings and the environment
//  names below); the built-in file is checked against the same policy by the host checks.
//

import Foundation

// MARK: - The format

/// One line of a game's config file as a list writes it, and the builds that get it.
struct RecommendationLine: Equatable {
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

    /// Whether a build gets the line. A line limited to some builds goes to none when the
    /// app's own build number is unknown.
    func applies(build: Int?) -> Bool {
        guard minBuild != nil || maxBuild != nil else { return true }
        guard let build else { return false }
        if let lower = minBuild, build < lower { return false }
        if let upper = maxBuild, build > upper { return false }
        return true
    }
}

/// How a rule recognises its game (GameRecommendations.swift, GameRecommendationList.match).
struct GameRecommendationMatch: Equatable {
    struct Program: Equatable {
        var name: String
        var steam: Bool? = nil
        var beside: String? = nil
        var notBeside: String? = nil
    }
    var steamAppIDs: [Int] = []
    var steamFolders: [String] = []
    var programs: [Program] = []
}

/// The switches a game's settings keep outside its config file (GameRecommendation).
struct GameRecommendationSwitches: Equatable {
    var avx: Bool? = nil
    var nvidia: Bool? = nil
    var wineVCRT: Bool? = nil
    var fastSync: Bool? = nil
    var semaphoreFastPath: Bool? = nil
    var fpsMode: Int? = nil
    var resolution: String? = nil
    var display: String? = nil
}

/// A rule as the file writes it.
struct RecommendationRule: Equatable {
    var id: String
    var title: String
    var version: Int
    var minBuild: Int? = nil
    var match: GameRecommendationMatch? = nil
    var config: [RecommendationLine]
    var smallRAMConfig: [RecommendationLine]? = nil
    var switches = GameRecommendationSwitches()
    var notes: [String] = []

    /// Builds before `minBuild` (or of an unknown number) do not know the rule.
    func applies(build: Int?) -> Bool {
        guard let minBuild else { return true }
        guard let build else { return false }
        return build >= minBuild
    }

    /// What the app applies on `build`: the lines that build gets, in the file's order.
    func recommendation(build: Int?) -> GameRecommendation {
        let small = smallRAMConfig?.filter { $0.applies(build: build) }.map(\.text)
        return GameRecommendation(
            id: id, title: title, version: version,
            config: config.filter { $0.applies(build: build) }.map(\.text).joined(separator: "\n"),
            avx: switches.avx, nvidia: switches.nvidia, wineVCRT: switches.wineVCRT, fastSync: switches.fastSync,
            semaphoreFastPath: switches.semaphoreFastPath, fpsMode: switches.fpsMode,
            resolution: switches.resolution, display: switches.display,
            smallRAMConfig: (small?.isEmpty ?? true) ? nil : small!.joined(separator: "\n"))
    }
}

enum RecommendationsError: Error, Equatable, CustomStringConvertible {
    /// Not JSON, or not this layout.
    case format(String)
    /// A key or value a downloaded list may not set.
    case policy(String)
    /// The list would take a game's rule away or lower its version.
    case regression(String)
    /// A download whose signature is missing or not BCD's.
    case signature(String)

    var description: String {
        switch self {
        case .format(let why): return "not a valid list: \(why)"
        case .policy(let why): return "a line BCD does not take from a download: \(why)"
        case .regression(let why): return "older than the list in use: \(why)"
        case .signature(let why): return why
        }
    }
}

struct RecommendationsFile: Equatable {
    static let supportedFormat = 1
    var format: Int
    var version: Int
    var generated: String
    var rules: [RecommendationRule]

    /// Reads the file. Throws `.format` for anything that is not exactly this layout.
    static func parse(_ data: Data) throws -> RecommendationsFile {
        let root: Any
        do {
            root = try JSONSerialization.jsonObject(with: data, options: [])
        } catch {
            throw RecommendationsError.format("not JSON")
        }
        let top = try JSONRead.object(root, "the file", required: ["format", "version", "generated", "rules"])
        let format = try JSONRead.int(top["format"], "format")
        guard format == supportedFormat else {
            throw RecommendationsError.format("format \(format) (this build reads format \(supportedFormat))")
        }
        let rules = try JSONRead.array(top["rules"], "rules").enumerated().map { i, value in
            try rule(value, "rules[\(i)]")
        }
        let file = RecommendationsFile(format: format, version: try JSONRead.int(top["version"], "version"),
                                       generated: try JSONRead.string(top["generated"], "generated"), rules: rules)
        try file.checkLayout()
        return file
    }

    private static func rule(_ value: Any, _ path: String) throws -> RecommendationRule {
        let o = try JSONRead.object(value, path, required: ["id", "title", "version", "config"],
                                    optional: ["minBuild", "match", "smallRAMConfig", "switches", "notes"])
        var rule = RecommendationRule(id: try JSONRead.string(o["id"], path + ".id"),
                                      title: try JSONRead.string(o["title"], path + ".title"),
                                      version: try JSONRead.int(o["version"], path + ".version"),
                                      config: try lines(o["config"], path + ".config"))
        if o["minBuild"] != nil { rule.minBuild = try JSONRead.int(o["minBuild"], path + ".minBuild") }
        if o["smallRAMConfig"] != nil { rule.smallRAMConfig = try lines(o["smallRAMConfig"], path + ".smallRAMConfig") }
        if let m = o["match"] {
            let mo = try JSONRead.object(m, path + ".match", optional: ["steamAppIDs", "steamFolders", "programs"])
            var match = GameRecommendationMatch()
            if mo["steamAppIDs"] != nil {
                match.steamAppIDs = try JSONRead.array(mo["steamAppIDs"], path + ".match.steamAppIDs").enumerated().map {
                    try JSONRead.int($1, path + ".match.steamAppIDs[\($0)]")
                }
            }
            if mo["steamFolders"] != nil {
                match.steamFolders = try JSONRead.array(mo["steamFolders"], path + ".match.steamFolders").enumerated().map {
                    try JSONRead.string($1, path + ".match.steamFolders[\($0)]")
                }
            }
            if mo["programs"] != nil {
                match.programs = try JSONRead.array(mo["programs"], path + ".match.programs").enumerated().map { i, p in
                    let pp = path + ".match.programs[\(i)]"
                    let po = try JSONRead.object(p, pp, required: ["name"], optional: ["steam", "beside", "notBeside"])
                    var program = GameRecommendationMatch.Program(name: try JSONRead.string(po["name"], pp + ".name"))
                    if po["steam"] != nil { program.steam = try JSONRead.bool(po["steam"], pp + ".steam") }
                    if po["beside"] != nil { program.beside = try JSONRead.string(po["beside"], pp + ".beside") }
                    if po["notBeside"] != nil { program.notBeside = try JSONRead.string(po["notBeside"], pp + ".notBeside") }
                    return program
                }
            }
            rule.match = match
        }
        if let s = o["switches"] {
            let sp = path + ".switches"
            let so = try JSONRead.object(s, sp, optional: ["avx", "nvidia", "wineVCRT", "fastSync", "semaphoreFastPath",
                                                          "fpsMode", "resolution", "display"])
            var sw = GameRecommendationSwitches()
            if so["avx"] != nil { sw.avx = try JSONRead.bool(so["avx"], sp + ".avx") }
            if so["nvidia"] != nil { sw.nvidia = try JSONRead.bool(so["nvidia"], sp + ".nvidia") }
            if so["wineVCRT"] != nil { sw.wineVCRT = try JSONRead.bool(so["wineVCRT"], sp + ".wineVCRT") }
            if so["fastSync"] != nil { sw.fastSync = try JSONRead.bool(so["fastSync"], sp + ".fastSync") }
            if so["semaphoreFastPath"] != nil { sw.semaphoreFastPath = try JSONRead.bool(so["semaphoreFastPath"], sp + ".semaphoreFastPath") }
            if so["fpsMode"] != nil { sw.fpsMode = try JSONRead.int(so["fpsMode"], sp + ".fpsMode") }
            if so["resolution"] != nil { sw.resolution = try JSONRead.string(so["resolution"], sp + ".resolution") }
            if so["display"] != nil { sw.display = try JSONRead.string(so["display"], sp + ".display") }
            rule.switches = sw
        }
        if o["notes"] != nil {
            rule.notes = try JSONRead.array(o["notes"], path + ".notes").enumerated().map {
                try JSONRead.string($1, path + ".notes[\($0)]")
            }
        }
        return rule
    }

    private static func lines(_ value: Any?, _ path: String) throws -> [RecommendationLine] {
        try JSONRead.array(value, path).enumerated().map { i, item in
            let lp = "\(path)[\(i)]"
            if let text = item as? String { return RecommendationLine(text: text) }
            let o = try JSONRead.object(item, lp, required: ["line"], optional: ["minBuild", "maxBuild"])
            var line = RecommendationLine(text: try JSONRead.string(o["line"], lp + ".line"))
            if o["minBuild"] != nil { line.minBuild = try JSONRead.int(o["minBuild"], lp + ".minBuild") }
            if o["maxBuild"] != nil { line.maxBuild = try JSONRead.int(o["maxBuild"], lp + ".maxBuild") }
            guard line.minBuild != nil || line.maxBuild != nil else {
                throw RecommendationsError.format("\(lp): a line object names no build (write the line as a plain string)")
            }
            return line
        }
    }

    // MARK: Checks

    /// The layout's own rules, whoever wrote the file (the built-in one too).
    func checkLayout() throws {
        func fail(_ why: String) -> RecommendationsError { .format(why) }
        guard version >= 1 else { throw fail("version \(version)") }
        guard generated.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil else {
            throw fail("generated \"\(generated)\" is not a date (YYYY-MM-DD)")
        }
        guard !rules.isEmpty else { throw fail("no rules") }
        var ids = Set<String>()
        for (i, rule) in rules.enumerated() {
            let path = "rules[\(i)] (\(rule.id))"
            guard rule.id.range(of: #"^[a-z0-9][a-z0-9-]{0,63}$"#, options: .regularExpression) != nil else {
                throw fail("\(path): the id is not lowercase letters, digits and dashes")
            }
            guard ids.insert(rule.id).inserted else { throw fail("\(path): the id comes twice") }
            guard !rule.title.isEmpty, rule.title.count <= 120, JSONRead.printable(rule.title) else {
                throw fail("\(path): the title is empty, too long or has control characters")
            }
            guard rule.version >= 1 else { throw fail("\(path): version \(rule.version)") }
            if let minBuild = rule.minBuild, minBuild < 0 { throw fail("\(path): minBuild \(minBuild)") }
            if rule.id == GameRecommendations.standardID {
                guard rule.match == nil, rule.minBuild == nil else {
                    throw fail("\(path): the standard rule has no match and no minBuild (every game without a rule gets it)")
                }
            } else {
                guard let match = rule.match else { throw fail("\(path): no match") }
                try Self.checkMatch(match, path)
            }
            guard !rule.config.isEmpty else { throw fail("\(path): config is empty") }
            try Self.checkLines(rule.config, path + ".config")
            if let small = rule.smallRAMConfig {
                guard !small.isEmpty else { throw fail("\(path): smallRAMConfig is empty (leave it out)") }
                try Self.checkLines(small, path + ".smallRAMConfig")
            }
            try Self.checkSwitches(rule.switches, path + ".switches")
            guard rule.notes.allSatisfy({ JSONRead.printable($0) }) else { throw fail("\(path): a note has control characters") }
        }
        guard ids.contains(GameRecommendations.standardID) else { throw fail("no rule \"\(GameRecommendations.standardID)\"") }
    }

    private static func checkMatch(_ match: GameRecommendationMatch, _ path: String) throws {
        func fail(_ why: String) -> RecommendationsError { .format("\(path).match: \(why)") }
        guard !match.steamAppIDs.isEmpty || !match.steamFolders.isEmpty || !match.programs.isEmpty else {
            throw fail("matches nothing")
        }
        guard match.steamAppIDs.allSatisfy({ $0 > 0 }), Set(match.steamAppIDs).count == match.steamAppIDs.count else {
            throw fail("a Steam App ID is not a positive number, or comes twice")
        }
        for folder in match.steamFolders {
            guard isName(folder), folder == folder.lowercased() else {
                throw fail("the Steam folder \"\(folder)\" is not one lowercased folder name")
            }
        }
        for program in match.programs {
            guard isName(program.name), program.name == program.name.lowercased(), program.name.hasSuffix(".exe") else {
                throw fail("the program \"\(program.name)\" is not a lowercased .exe file name")
            }
            for file in [program.beside, program.notBeside].compactMap({ $0 }) where !isName(file) {
                throw fail("\"\(file)\" is not a file name")
            }
        }
    }

    /// One name, no folder: no separators, not "." or "..", no control characters.
    private static func isName(_ s: String) -> Bool {
        !s.isEmpty && s.count <= 260 && !s.contains("\\") && !s.contains("/") && s != "." && s != ".."
            && s.trimmingCharacters(in: .whitespaces) == s && JSONRead.printable(s)
    }

    private static func checkLines(_ lines: [RecommendationLine], _ path: String) throws {
        for (i, line) in lines.enumerated() {
            let lp = "\(path)[\(i)]"
            let text = line.text
            guard !text.isEmpty, text.count <= 1024, text.trimmingCharacters(in: .whitespaces) == text,
                  JSONRead.printable(text) else {
                throw RecommendationsError.format("\(lp): an empty line, spaces at an end, or a control character")
            }
            if let lower = line.minBuild, lower < 0 { throw RecommendationsError.format("\(lp): minBuild \(lower)") }
            if let upper = line.maxBuild, upper < (line.minBuild ?? 0) {
                throw RecommendationsError.format("\(lp): maxBuild below minBuild")
            }
            if line.isComment { continue }
            // "key = value": the first "=" is the one in " = ", as GameProfile.parse reads it.
            guard let key = line.key, let value = line.value, !value.isEmpty, !key.contains("="),
                  key.range(of: #"^(env\.[A-Za-z_][A-Za-z0-9_]*|[a-z0-9][a-z0-9.-]*)$"#, options: .regularExpression) != nil else {
                throw RecommendationsError.format("\(lp): \"\(text)\" is not a comment or \"key = value\"")
            }
        }
        // A key twice would leave the earlier line dead (the last one wins): only where the two
        // lines go to different builds.
        for (i, a) in lines.enumerated() {
            guard let key = a.key else { continue }
            for b in lines[(i + 1)...] where b.key == key {
                let aLower = a.minBuild ?? Int.min, aUpper = a.maxBuild ?? Int.max
                let bLower = b.minBuild ?? Int.min, bUpper = b.maxBuild ?? Int.max
                if aLower <= bUpper && bLower <= aUpper {
                    throw RecommendationsError.format("\(path): \(key) twice for the same builds")
                }
            }
        }
    }

    /// DisplayMode's raw values (GuestDisplay.swift).
    static let displayModes: Set<String> = ["fit", "fill", "stretch", "aspect"]

    private static func checkSwitches(_ s: GameRecommendationSwitches, _ path: String) throws {
        if let mode = s.fpsMode, !(0...4).contains(mode) { throw RecommendationsError.format("\(path).fpsMode \(mode)") }
        if let size = s.resolution, size != GameRecommendation.screen720,
           size.range(of: #"^[1-9][0-9]{2,3}x[1-9][0-9]{2,3}$"#, options: .regularExpression) == nil {
            throw RecommendationsError.format("\(path).resolution \"\(size)\"")
        }
        if let mode = s.display, !displayModes.contains(mode) {
            throw RecommendationsError.format("\(path).display \"\(mode)\"")
        }
    }

    /// The lines a downloaded list may set (RecommendationsPolicy). `build`: only the lines and
    /// rules that build gets are checked (a line for a later build may use a key this build
    /// does not know); `everyLine`: all of them (tools/publish-recommendations.py).
    func checkPolicy(_ policy: RecommendationsPolicy, build: Int?, everyLine: Bool = false) throws {
        for (i, rule) in rules.enumerated() where everyLine || rule.applies(build: build) {
            for (name, lines) in [("config", rule.config), ("smallRAMConfig", rule.smallRAMConfig ?? [])] {
                for (j, line) in lines.enumerated() where everyLine || line.applies(build: build) {
                    try policy.check(line, at: "rules[\(i)] (\(rule.id)).\(name)[\(j)]")
                }
            }
        }
    }

    /// The list a build applies.
    func list(build: Int?, origin: GameRecommendationList.Origin) -> GameRecommendationList {
        GameRecommendationList(version: version, generated: generated, origin: origin,
                               rules: rules.filter { $0.applies(build: build) }.map {
                                   GameRecommendationList.Rule(recommendation: $0.recommendation(build: build), match: $0.match)
                               })
    }
}

// MARK: - What a downloaded list may set

struct RecommendationsPolicy {
    /// Plain keys: the settings catalog's (ConfigCatalog.generated.swift), but not its app-level
    /// category, which holds the app's own experiments and switches (a remote Metal host, launch
    /// arguments, fault injection); of those, only the two game settings below.
    static let appLevelCategory = "App & front end"
    static let appLevelGameKeys: Set<String> = ["dxmt", "swap-min-kb"]

    /// Environment lines: the names the built-in lists use and auto-tune's known fixes add
    /// (build/tools/autotune_rules.py), nothing else. A new name joins this list in the build
    /// that reads it; a downloaded line that uses it carries that build as its minBuild.
    static let allowedEnvNames: Set<String> = [
        "BCD_EPIC_HOST_START",
        "_JAVA_OPTIONS", "DOTNET_EnableWriteXorExecute", "DOTNET_GCRegionRange",
        "DXMT_SMALL_RINGS", "DXMT_WSI_MODE_TABLE", "DXMT_WSI_MONITOR_IDENTITY", "FEX_MEMCPYSETTSOENABLED", "FEX_TSOENABLED", "FEX_VECTORTSOENABLED", "FEX_X87REDUCEDPRECISION", "LC_ALL",
        "MADEIRA_ALIAS_RELEASE", "MADEIRA_ANON_CAS_FLUSH", "MADEIRA_ANON_LDADDAL32", "MADEIRA_APP_DLL_SHADOW", "MADEIRA_BAND_CENSUS", "MADEIRA_CAGE_RELEASE",
        "MADEIRA_CHILD_ARGS", "MADEIRA_CTX_FRAME", "MADEIRA_D3D11_SRC", "MADEIRA_D3D9", "MADEIRA_D3D9_LAST", "MADEIRA_D3D9_X64", "MADEIRA_DEVICE_STATS", "MADEIRA_STEAM_IDENTITY", "MADEIRA_DOCK_GAME_SCM",
        "MADEIRA_DOCK_KEEP_ALIVE", "MADEIRA_DOCK_SCRIPT_REGISTRY", "MADEIRA_DXGI_BUDGET_EVENTS", "MADEIRA_DXGI_SRC", "MADEIRA_EC_HOOK_TRACE", "MADEIRA_ECO_NATIVE",
        "MADEIRA_EXECREQ_LEAVE", "MADEIRA_FASTSYNC_SEM", "MADEIRA_FAULT_GUARD_GUEST_RIP", "MADEIRA_FAULT_GUARD_RATE", "MADEIRA_FEX_AVX", "MADEIRA_FEX_BAND_KEEP_MB",
        "MADEIRA_FILE_TRACE", "MADEIRA_FILE_TRACE_LIMIT", "MADEIRA_FREE_DELAY_MS", "MADEIRA_GUEST_LOG", "MADEIRA_GUEST_LOG_LIMIT",
        "MADEIRA_INPUT_FOREGROUND", "MADEIRA_JUMBO_HALF", "MADEIRA_KMT_ADAPTER", "MADEIRA_LD_BOUNDS",
        "MADEIRA_OPTIN_DLLS", "MADEIRA_WINTYPES",
        "MADEIRA_LEAN_DIAG", "MADEIRA_LOCK_ORPHAN", "MADEIRA_METAL_HUD_MAIN", "MADEIRA_MFC_DOCK", "MADEIRA_NLS_EXTRA", "MADEIRA_PAD_MODE",
        "MADEIRA_PIN_GRAPHICS_DLLS", "MADEIRA_POOL_HEAD_RESERVE_MB", "MADEIRA_POOL_LOW_IMAGES",
        "MADEIRA_POOL_RECYCLE_IMAGES", "MADEIRA_PRELOAD_DLLS", "MADEIRA_RDR2_VA_HOLD", "MADEIRA_RECOVERY_LOG_SAMPLE", "MADEIRA_SC_PA_POOLS",
        "MADEIRA_SMALL_STACK_EXES", "MADEIRA_SPAWN_BLOCK", "MADEIRA_SPAWN_SWAP", "MADEIRA_SRV_FGETPATH", "MADEIRA_SWAP_MIN_KB",
        "MADEIRA_SWAP_RESERVE_MAX_MB", "MADEIRA_THREAD_STACK_SPILL", "MADEIRA_TINY_WINDOW_FIX", "MADEIRA_TLS_CLEAR_OWN_PROCESS",
        "MADEIRA_TOUCH_MOUSE",
        "MADEIRA_USER_VA_GB", "MADEIRA_VA_PRESSURE_GAP_OK", "MADEIRA_VCRT_ARM64_KEY", "MADEIRA_VDCFG_PATH", "MADEIRA_WGI_HOST_PADS", "MADEIRA_WG_64BIT",
        "MADEIRA_WG_VIDEO",
        "MADEIRA_WOW_MIN_FREE_GB",
        "MADEIRA_WOW_RWX_PLAIN",
        "DXMT_DUMP_D9_SHADER", "MADEIRA_FRAME_STATS", "MADEIRA_GPU_TRACE",   // diagnostics read since build 592: LEGO Batman, Silent Hunter 3, Just Cause 3 (build 598 lists)
        "MADEIRA_GPU_STALL_MS",   // build 624 (agent D2 S15, Just Cause 3): a command buffer unfinished that many ms is named; 100 to 60000 (checked below)
        "DXMT_QUERY_TS_WATCHDOG",   // build 624 (agent D2 S11, WRC 9): a timestamp query pending 2 s is answered as complete; 1 only (checked below)
        "MADEIRA_AUDIO_IO_MS",   // build 625 (Dead Rising's crackle, agent D1 S3-b): the audio IO buffer iOS is asked for; 1 to 100 ms (checked below)
        "MADEIRA_MIP_CLAMP_AUTO",   // build 625 (Outlast 2, agent L4 2.6): DXMT's memory-pressure BC texture clamp (read since ml2000); 0 or 1 (checked below)
        "MADEIRA_MFC32",   // build 625 (the owner's item 26): Microsoft's 32-bit MFC, downloaded in Settings, in a 32-bit game's syswow64; 0 or 1 (checked below)
        "MADEIRA_X86_FAULT_DUMP",
        "MADEIRA_WIN_TEXT",   // build 624 (FINAL FANTASY VII's launcher MMJZ8K): an error box's read-only details pane in the log; a diagnostic
        "MADEIRA_D2R_PROBE_RECOVERY", "MADEIRA_FAULT_LOOP_GUEST_PROGRESS", "MADEIRA_NTDLL_DATA_SYNC_RANGE", "MADEIRA_NTDLL_XSEC", "MADEIRA_SHARED_SECTION_EXEC",   // Diablo II: Resurrected's executable-section switches (build 598; thebcd69/bcd-games#4)
        "MADEIRA_X64_GRAPHICS_ENTRY", "MADEIRA_X64_IMAGE_NOCOPY", "MADEIRA_X64_MACHINE", "SteamDeck", "WINEDEBUG",
        "MADEIRA_FEX_ALIAS_MAX",   // build 624 (agent A S7, Borderlands GOTY Enhanced): FEX's alias table limit, 256 to 1024 (checked below)
        "MADEIRA_DXGI_COMPOSITION",   // build 624 (agent E2 S2b, Slay the Spire 2): dxgi-src.dll serves a composition swap chain on the game's window; 0 or 1
        "MADEIRA_ZERO_MITIGATION_POLICY",   // build 600 (Glyphica: Typing Survival, ChakraCore's fail-fast over an unwritten policy buffer)
        "MADEIRA_KUSER_KEEP_REGS",   // build 602 (The Adventures of Elliot: registers that only hold a KUSER-looking value stay as the guest set them)
        "MADEIRA_DLL_LOCAL",
        "WINEDLLOVERRIDES",
        "WINE_D3D_CONFIG",
        "MADEIRA_D3D8", "MADEIRA_DDRAW",   // build 602: d3d8to9 as a 32-bit game's d3d8.dll and cnc-ddraw as its ddraw.dll (BCDGraphicsRoutes.c); one value each
        "MADEIRA_REAL_SUSPEND",   // build 620: SuspendThread really stops the thread (wineserver mach_ios.c, opt-in): Unity IL2CPP games whose GC marks objects while it believes the world is stopped (thebcd69/bcd-games#18: Big Walk, Megabonk, BALL x PIT)
        "MADEIRA_BUFFER_RTV_EMPTY",   // build 620: a render target view on a buffer gets S_OK and no view (DXMT, opt-in; Frostbite 2: Need for Speed: The Run)
        "MADEIRA_PAUSE_IN_BACKGROUND",   // build 623: commits and presents wait while BCD is not active (auto-tune's gpu-work-refused-in-background; F1 2020)
        "MADEIRA_MZ_VIEW_CHECK",   // build 624: a Mono code chunk behind a plain-mapped assembly is not that file's image tail (R.E.P.O., ios_mz_view.h)
        "MADEIRA_KEYED_EVENT_PER_PROCESS",   // build 624 (read since 598): every pseudo-process its own keyed event (Control: RtlRunOnceComplete)
        "MADEIRA_SRV_GETDESC_RESOLVE",   // build 624: an SRV's GetDesc reports the real mip count (Dark Souls III; tools/patch-dxmt-srv-getdesc-mips.py)
        "MADEIRA_FEX_SHA",   // build 624: 0 tells the guest the processor has no SHA extension (Gal*Gun 2, Hello Neighbor: OpenSSL 1.0.2's SHA-NI routine)
        "DXMT_D9_IB_RANGE",   // build 624: an indexed Direct3D 9 draw refreshes only the index bytes it reads, on by default; 0 turns it off (tools/patch-dxmt-d9-ib-range.py)
        "MADEIRA_REG_TRACE",   // build 624: the wineserver logs each failed registry request ([reg-miss], 400 at most; Far Cry 3's updater)
        "MADEIRA_WOW_UNITY_PLAIN",   // build 624: a 32-bit Unity game's own Mono makes its window's RWX memory plain (From the Darkness)
    ]

    var configKeys: Set<String>
    var envNames: Set<String>

    /// `catalog`: the settings catalog's keys and categories.
    init(catalog: [(key: String, category: String)], envNames: Set<String> = RecommendationsPolicy.allowedEnvNames) {
        configKeys = Set(catalog.filter {
            !$0.key.hasPrefix("env.") && ($0.category != Self.appLevelCategory || Self.appLevelGameKeys.contains($0.key))
        }.map(\.key))
        self.envNames = envNames
    }

    func check(_ line: RecommendationLine, at path: String) throws {
        guard let key = line.key, let value = line.value else { return }   // a comment
        if key.hasPrefix("env.") {
            let name = String(key.dropFirst(4))
            guard envNames.contains(name) else {
                throw RecommendationsError.policy("\(path): \(key) is not on the list of environment lines a download may set")
            }
            switch name {
            case "MADEIRA_OPTIN_DLLS":
                // Only DLLs BCD ships as <name>-optin.dll (tools/build-wine-extra-dlls.sh: OPTIN_WANT, and setupapi,
                // the shipped setupapi.dll with a lock round its device node table). mfasfsrcsnk, wmvdecod and
                // wmadmod: Wine's ASF byte stream handler and the WMV and WMA decoder MFTs (Resident Evil 2).
                // oledlg, fwpuclnt and sensapi: small system DLLs a program imports (2026-10-23). ddraw: Dying Light's renderer (same day).
                // msi, mspatcha, odbccp32, sxs and rstrtmgr: what the EA app's 64-bit EABackgroundService.exe imports (same day).
                // gamingtcui: what Forza Horizon 4's exe imports as api-ms-win-gaming-tcui-l1-1-0.dll (round 620).
                // qwave: what the Discord Social SDK plugin of Bloons TD 6 imports (round 623).
                // kernelbase (round 624, Glyphica: Typing Survival): the Wine fork's own kernelbase, built from the tree as kernelbase-optin.dll
                // (the shipped kernelbase.dll is a prebuilt file without the fork's changes).
                // dcomp (round 624, Slay the Spire 2): a DirectComposition device that draws nothing (dcomp-optin.dll).
                let names = value.lowercased().split(whereSeparator: { ",; ".contains($0) })
                guard !names.isEmpty, names.allSatisfy({ ["dxva2", "evr", "xpsprint", "sfc", "sfc_os", "tbs", "twinapi.appcore", "setupapi"].contains($0)
                        || ["mfasfsrcsnk", "wmvdecod", "wmadmod"].contains($0) || ["oledlg", "fwpuclnt", "sensapi", "ddraw"].contains($0)
                        || ["msi", "mspatcha", "odbccp32", "sxs", "rstrtmgr"].contains($0) || ["glu32", "tdh", "gamingtcui", "qwave"].contains($0)
                        || $0 == "kernelbase" || $0 == "dcomp" }) else {
                    throw RecommendationsError.policy("\(path): MADEIRA_OPTIN_DLLS may only name dxva2, evr, xpsprint, sfc, sfc_os, tbs, twinapi.appcore or setupapi, or mfasfsrcsnk, wmvdecod, wmadmod, or oledlg, fwpuclnt, sensapi, ddraw, or msi, mspatcha, odbccp32, sxs, rstrtmgr, or glu32, tdh, gamingtcui, qwave, or kernelbase, dcomp")
                }
            case "MADEIRA_DXGI_COMPOSITION":
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_DXGI_COMPOSITION is 0 or 1")
                }
            case "DXMT_QUERY_TS_WATCHDOG":
                // The Direct3D 11 timestamp-query watchdog (build 624, tools/patch-dxmt-d11-query-stuck.py): on (1) or absent.
                guard value == "1" else {
                    throw RecommendationsError.policy("\(path): DXMT_QUERY_TS_WATCHDOG is 1")
                }
            case "MADEIRA_MFC32":
                // The downloaded 32-bit MFC in the game's syswow64 (WineProcessBridge.m madeira_mfc32; build 625 in lists): 1 on, 0 off.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_MFC32 is 0 or 1")
                }
            case "MADEIRA_MIP_CLAMP_AUTO":
                // DXMT's memory-pressure clamp (d3d11_texture_device.cpp GetMipClampAutoConfig; build 625 in lists): 1 on, 0 off. A list gives
                // it this way, not as a dxmt line, so the game's dxmt key stays free for auto-tune's blanket clamp (Outlast 2).
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_MIP_CLAMP_AUTO is 0 or 1")
                }
            case "MADEIRA_AUDIO_IO_MS":
                // The audio IO buffer (WineProcessBridge.m madeira_audio_io_slice, build 625 in lists): a whole number of milliseconds, 1 to 100.
                guard let ms = Int(value), (1...100).contains(ms) else {
                    throw RecommendationsError.policy("\(path): MADEIRA_AUDIO_IO_MS is a whole number of milliseconds from 1 to 100")
                }
            case "MADEIRA_GPU_STALL_MS":
                // The GPU stall watch (build 624, tools/patch-winemetal-gpu-stall.py): a whole number of milliseconds from 100 to 60000.
                guard let n = Int(value), (100...60000).contains(n), String(n) == value else {
                    throw RecommendationsError.policy("\(path): MADEIRA_GPU_STALL_MS is a whole number from 100 to 60000")
                }
            case "MADEIRA_FEX_ALIAS_MAX":
                // FEX's alias table limit (build 624, tools/patch-fex-ios-alias-capacity.py): a whole number from 256 to 1024.
                guard let n = Int(value), (256...1024).contains(n), String(n) == value else {
                    throw RecommendationsError.policy("\(path): MADEIRA_FEX_ALIAS_MAX is a whole number from 256 to 1024")
                }
            case "MADEIRA_STEAM_IDENTITY":
                // No Steam game identity for this session (build 598, Rockstar's launcher of the Epic GTA V / RDR2): the one value, none.
                guard value == "none" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_STEAM_IDENTITY may only be none")
                }
            case "MADEIRA_D3D9":
                // The 32-bit Direct3D 9 frontend (build 598): the one value, emulated, the standard frontend. A game whose native frontend
                // crashes (Resident Evil 6, Resident Evil Revelations 2: a call of address 0) gets the standard one over the app's global
                // Settings > Graphics choice; "native" is the player's own switch and no list sets it.
                guard value == "emulated" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_D3D9 may only be emulated")
                }
            case "MADEIRA_D3D9_X64":
                // DXMT's Direct3D 9 for a 64-bit game (build 595): dxmt; Wine's d3d9.dll stays for everything else. Build 602: BCD picks dxmt
                // by itself for a game whose folder shows no Direct3D 11, DXGI or Direct3D 12 (BCDGraphicsDefaults.swift), and "wined3d" is the
                // game's way to keep Wine's whatever the folder shows (a list line for a game the default must leave alone).
                guard value == "dxmt" || value == "wined3d" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_D3D9_X64 may only be dxmt or wined3d")
                }
            case "MADEIRA_D3D8":
                // crosire's d3d8to9 as a 32-bit game's d3d8.dll (build 602): the one value, d3d9; Wine's d3d8.dll (OpenGL, which BCD lacks) stays for everything else.
                guard value == "d3d9" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_D3D8 may only be d3d9")
                }
            case "MADEIRA_DDRAW":
                // cnc-ddraw as a 32-bit game's ddraw.dll (build 602): the one value, cnc; Wine's ddraw.dll stays for everything else.
                guard value == "cnc" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_DDRAW may only be cnc")
                }
            case "MADEIRA_WINTYPES", "MADEIRA_X64_MACHINE", "MADEIRA_TINY_WINDOW_FIX", "BCD_EPIC_HOST_START", "MADEIRA_ZERO_MITIGATION_POLICY":
                // MADEIRA_ZERO_MITIGATION_POLICY (build 600): Wine's GetProcessMitigationPolicy writes zeros into the caller's buffer, as it
                // should (it wrote nothing); only the exact value 1 turns it on, and only Glyphica's list line does.
                // BCD_EPIC_HOST_START (build 598): an Epic game's signed-in start keeps BCD Host as its first program; off unless a game's list says 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_KUSER_KEEP_REGS":
                // The exception handler's KUSER register rebase skips what the sub-floor service handled (build 602, The Adventures of
                // Elliot): only the exact value 1 turns it on, and only that game's list line does.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_D3D9_LAST", "MADEIRA_FILE_TRACE":
                // Diagnostics (build 597, Darksiders II's crash after its intro video): DXMT's last-Direct3D-9-calls ring and the 64-bit
                // fault report of d3d9-dxmt.dll; Wine's file open trace. They only add log lines; 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_GUEST_LOG":
                // Diagnostics (build 598, BioShock Infinite's silent exit): the mirror of the game's own text log files into BCD's log, every line
                // ("all") instead of the error/GPU/display lines; only log lines are added.
                guard value == "all" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_GUEST_LOG may only be all")
                }
            case "MADEIRA_GUEST_LOG_LIMIT":
                // How many lines the mirror may write: a whole number from 100 to 4000 (the mirror caps itself there).
                guard value.range(of: #"^[0-9]{3,4}$"#, options: .regularExpression) != nil, let n = Int(value), n >= 100, n <= 4000 else {
                    throw RecommendationsError.policy("\(path): MADEIRA_GUEST_LOG_LIMIT is a whole number from 100 to 4000")
                }
            case "MADEIRA_X86_FAULT_DUMP":
                // A diagnostic (build 598, Need for Speed Most Wanted and Underground 2): the report of a 32-bit program's access violation or
                // int3 (the code, the stack, the modules and the objects the registers point at). It only adds log lines; 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_X86_FAULT_DUMP is 0 or 1")
                }
            case "MADEIRA_FILE_TRACE_LIMIT":
                // How many lines the file trace may write: a whole number from 1000 to 100000 (the trace caps itself there).
                guard value.range(of: #"^[0-9]{4,6}$"#, options: .regularExpression) != nil, let n = Int(value), n >= 1000, n <= 100000 else {
                    throw RecommendationsError.policy("\(path): MADEIRA_FILE_TRACE_LIMIT is a whole number from 1000 to 100000")
                }
            case "FEX_X87REDUCEDPRECISION", "MADEIRA_TLS_CLEAR_OWN_PROCESS", "MADEIRA_VDCFG_PATH":
                // Three engine switches that are 0 or 1 (build 580 reads these names from a list: Batman: Arkham City GOTY's exact
                // x87 math and per-program TlsFree, Trails in the Sky 1st Chapter's display path).
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_WG_64BIT":
                // BCD's own media decoders for a 64-bit game (Resident Evil 2 and 7); only 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_WG_64BIT is 0 or 1")
                }
            case "MADEIRA_RECOVERY_LOG_SAMPLE":
                // Build 598 (thebcd69/bcd-games#16 by le0-VV, Minecraft Dungeons II): samples FEX's routine invalid-instruction recovery records; log output only; 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_RECOVERY_LOG_SAMPLE is 0 or 1")
                }
            case "MADEIRA_FAULT_GUARD_GUEST_RIP":
                // Build 598 (thebcd69/bcd-games#16 by le0-VV, Minecraft Dungeons II): the fault guards tell FEX's shared invalid-instruction
                // thunk apart by the guest instruction, and the 50-fault page breaker counts memory faults only. Off unless a game's list says 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_FAULT_GUARD_GUEST_RIP is 0 or 1")
                }
            case "MADEIRA_KEYED_EVENT_PER_PROCESS", "MADEIRA_SRV_GETDESC_RESOLVE":
                // build 624: switches, off unless a game's list says 1 (Control; Dark Souls III).
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_FEX_SHA":
                // build 624: on unless a game's list says 0 (Gal*Gun 2, Hello Neighbor: OpenSSL 1.0.2's SHA-extension routine).
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_FEX_SHA is 0 or 1")
                }
            case "MADEIRA_WOW_UNITY_PLAIN":
                // build 624 (32-bit Unity games, From the Darkness PA7PQB): off unless a game's list says 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_WOW_UNITY_PLAIN is 0 or 1")
                }
            case "MADEIRA_WIN_TEXT":
                // build 624 (agent B S6): asks a visible read-only multi-line Edit control for its text once (a diagnostic); 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_WIN_TEXT is 0 or 1")
                }
            case "MADEIRA_REG_TRACE":
                // build 624 (Far Cry 3's updater, "Cannot find essential information in the registry"): a diagnostic, log only; 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_REG_TRACE is 0 or 1")
                }
            case "DXMT_D9_IB_RANGE":
                // build 624: on by default (the owner's decision, 2026-10-30); a game's list may say 0 to turn it off.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): DXMT_D9_IB_RANGE is 0 or 1")
                }
            case "MADEIRA_MZ_VIEW_CHECK":
                // Build 624 (round 624, R.E.P.O. and other Unity games on Mono): an executable request outside the Wine view of its
                // nearest MZ page is handled as anonymous RWX instead of that image's tail (virtual_ios.c, ios_mz_view.h); 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_MZ_VIEW_CHECK is 0 or 1")
                }
            case "MADEIRA_ANON_LDADDAL32":
                // Build 608: default off; only a matching game's auto-tune opts into the runtime code-write workaround.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_ANON_LDADDAL32 is 0 or 1")
                }
            case "MADEIRA_FAULT_GUARD_RATE", "MADEIRA_MFC_DOCK", "MADEIRA_DOCK_SCRIPT_REGISTRY", "MADEIRA_ANON_CAS_FLUSH", "MADEIRA_NLS_EXTRA", "MADEIRA_WG_VIDEO":
                // Six engine and app switches that are 0 or 1 (build 591 reads the first five from a list: the fault-storm guards that count a
                // real storm only, the MFC DLLs in a Dock session, a Steam install script's Registry values before a Dock start, FEX's
                // translation dropped after an emulated CAS, the extra code-page tables; MADEIRA_WG_VIDEO is older: 0 limits the media
                // parser to MP3 and WAV, Albino Lullaby's intro).
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_CTX_FRAME":
                // Build 598 (round 2026-10-24, the Unity IL2CPP crash that follows a garbage collection): the Wine server reports a thread parked
                // in a system call by its saved call frame, not by the Mach-O registers it happens to be executing (build/wineserver/mach_ios.c,
                // ml716). The garbage collector reads that report to find a stopped thread's registers and stack; 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): MADEIRA_CTX_FRAME is 0 or 1")
                }
            case "MADEIRA_NTDLL_XSEC", "MADEIRA_SHARED_SECTION_EXEC", "MADEIRA_NTDLL_DATA_SYNC_RANGE", "MADEIRA_FAULT_LOOP_GUEST_PROGRESS", "MADEIRA_D2R_PROBE_RECOVERY":
                // Build 598 (round 2026-10-24, thebcd69/bcd-games#4 by mckenna654, Diablo II: Resurrected): five engine switches for a program
                // whose loader maps one section twice (a write view and a run view) and runs the second. Each reads the value 1 only and is off
                // otherwise; only that game's list line turns them on: the executable section view keeps its shared backing
                // (MADEIRA_SHARED_SECTION_EXEC), the emulator is told about it by the ntdll-xsec.dll variant (MADEIRA_NTDLL_XSEC) whose
                // unreadable-target recovery of the emulator's own probe is MADEIRA_D2R_PROBE_RECOVERY, ntdll's .data syncs only the requested
                // bytes (MADEIRA_NTDLL_DATA_SYNC_RANGE) and a run of breakpoints at different guest addresses is not a stuck thread
                // (MADEIRA_FAULT_LOOP_GUEST_PROGRESS); 0 or 1.
                guard value == "0" || value == "1" else {
                    throw RecommendationsError.policy("\(path): \(name) is 0 or 1")
                }
            case "MADEIRA_USER_VA_GB":
                // The address space advertised to the game, in whole GiB (FINAL FANTASY TACTICS asks for half of what is advertised).
                // BCD's map has at most 512 GiB and its largest free hole is 32 GiB, so a cap under 8 would starve a game of address
                // space and one over 512 does nothing.
                guard value.range(of: #"^[0-9]{1,3}$"#, options: .regularExpression) != nil, let gib = Int(value), gib >= 8, gib <= 512 else {
                    throw RecommendationsError.policy("\(path): MADEIRA_USER_VA_GB is a whole number of GiB from 8 to 512")
                }
            case "_JAVA_OPTIONS":
                // The JVM's own variable, read after the command line: only the one value that turns ZGC off (Project Zomboid's JVM
                // reserves its ZGC heap at 4 TiB, which BCD's map does not have). Never another option: it would reach every JVM.
                guard value == "-XX:-UseZGC" else {
                    throw RecommendationsError.policy("\(path): _JAVA_OPTIONS may only be -XX:-UseZGC")
                }
            case "DOTNET_EnableWriteXorExecute":
                // The .NET runtime's own switch; only turning it off is ever listed.
                guard value == "0" else {
                    throw RecommendationsError.policy("\(path): DOTNET_EnableWriteXorExecute may only be 0")
                }
            case "DOTNET_GCRegionRange":
                // The .NET runtime's own switch (hexadecimal bytes, with or without 0x; .NET 7 and later): the address range
                // its garbage collector reserves for the heap, 256 GB by default. Only a range of 1 to 64 GB: the largest
                // free gap of BCD's address map is 32 GB.
                let digits = value.lowercased().hasPrefix("0x") ? String(value.dropFirst(2)) : value
                guard digits.range(of: #"^[0-9A-Fa-f]{1,11}$"#, options: .regularExpression) != nil,
                      let bytes = UInt64(digits, radix: 16), bytes >= 0x4000_0000, bytes <= 0x10_0000_0000 else {
                    throw RecommendationsError.policy("\(path): DOTNET_GCRegionRange is a hexadecimal size from 1 GB to 64 GB")
                }
            case "WINEDLLOVERRIDES":
                // Switching a DLL off, to Wine's own, or (for rdr2-other from build 616) socialclub/version to native,builtin.
                let entry = #"([A-Za-z0-9_.,-]+=b?|(socialclub|version)=n,b)"#
                guard value.range(of: "^" + entry + "(;" + entry + ")*$", options: .regularExpression) != nil else {
                    throw RecommendationsError.policy("\(path): WINEDLLOVERRIDES may only switch DLLs off, to Wine's own, or socialclub/version=n,b")
                }
            case "MADEIRA_DLL_LOCAL":
                let dll = #"[A-Za-z0-9_.-]+\.[Dd][Ll][Ll]"#
                guard value.range(of: "^" + dll + "=" + dll + "(;" + dll + "=" + dll + ")*$", options: .regularExpression) != nil else {
                    throw RecommendationsError.policy("\(path): MADEIRA_DLL_LOCAL is <target.dll>=<local.dll>")
                }
            case "MADEIRA_SPAWN_SWAP":
                // Build 598 (Fallout 4, Steam 377160): a program that is about to start is replaced by another program of the same folder,
                // <from.exe>=<to.exe>, several separated by ';'. Plain file names only (ASCII letters, digits, blanks inside, . _ - ( ) + ,
                // and ~), both ending in .exe, no folder: the replacement is always a file next to the program Valve's client asked for.
                let program = #"[A-Za-z0-9_.()+,~-]([A-Za-z0-9_.()+,~ -]{0,253}[A-Za-z0-9_.()+,~-])?\.[Ee][Xx][Ee]"#
                guard value.range(of: "^" + program + "=" + program + "(;" + program + "=" + program + ")*$", options: .regularExpression) != nil else {
                    throw RecommendationsError.policy("\(path): MADEIRA_SPAWN_SWAP is <from.exe>=<to.exe>, program names of one folder")
                }
            case "MADEIRA_PRELOAD_DLLS", "MADEIRA_APP_DLL_SHADOW":
                guard value.range(of: #"^[A-Za-z0-9_.-]+(;[A-Za-z0-9_.-]+)*$"#, options: .regularExpression) != nil else {
                    throw RecommendationsError.policy("\(path): \(name) takes DLL names only")
                }
            default:
                break
            }
        } else {
            guard configKeys.contains(key) else {
                throw RecommendationsError.policy("\(path): \(key) is not a game setting in the settings catalog")
            }
        }
        guard value.count <= 512 else { throw RecommendationsError.policy("\(path): the value is longer than 512 characters") }
    }
}

// MARK: - The list in use

/// The rules a build applies, in order (GameRecommendations.active).
struct GameRecommendationList {
    enum Origin: String { case builtIn = "built-in", downloaded }
    struct Rule: Equatable {
        var recommendation: GameRecommendation
        var match: GameRecommendationMatch?
    }

    var version: Int
    var generated: String
    var origin: Origin
    var rules: [Rule]

    /// No list at all (a build without its file, which the host checks rule out): nothing matches
    /// and nothing is written.
    static let empty = GameRecommendationList(version: 0, generated: "", origin: .builtIn, rules: [])

    var recommendations: [GameRecommendation] { rules.map(\.recommendation) }
    var standard: GameRecommendation? { rules.first { $0.recommendation.id == GameRecommendations.standardID }?.recommendation }

    /// Why this list may not replace `old`, or nil: it must keep every rule `old` has, at the same
    /// version or a higher one. A game whose rule went away would get the standard list over its own.
    func regression(from old: GameRecommendationList) -> String? {
        let mine = Dictionary(rules.map { ($0.recommendation.id, $0.recommendation.version) }, uniquingKeysWith: { a, _ in a })
        for rule in old.rules {
            let id = rule.recommendation.id
            guard let version = mine[id] else { return "it has no rule \(id)" }
            if version < rule.recommendation.version { return "\(id) v\(version) is older than v\(rule.recommendation.version)" }
        }
        return nil
    }

    /// The rules that are new or different from `old`'s.
    func changes(from old: GameRecommendationList) -> [GameRecommendation] {
        let before = Dictionary(old.rules.map { ($0.recommendation.id, $0) }, uniquingKeysWith: { a, _ in a })
        return rules.filter { before[$0.recommendation.id] != $0 }.map(\.recommendation)
    }
}

// MARK: - Reading JSON exactly

/// JSONSerialization's values, read without Foundation's lenient bridging: true is not 1, 4.0
/// is not 4, and a field nobody asked for is an error.
enum JSONRead {
    static func object(_ value: Any?, _ path: String, required: Set<String> = [], optional: Set<String> = []) throws -> [String: Any] {
        guard let object = value as? [String: Any] else { throw RecommendationsError.format("\(path) is not an object") }
        if let unknown = Set(object.keys).subtracting(required).subtracting(optional).sorted().first {
            throw RecommendationsError.format("\(path): unknown field \"\(unknown)\"")
        }
        if let missing = required.subtracting(object.keys).sorted().first {
            throw RecommendationsError.format("\(path): no \"\(missing)\"")
        }
        return object
    }

    static func array(_ value: Any?, _ path: String) throws -> [Any] {
        guard let array = value as? [Any] else { throw RecommendationsError.format("\(path) is not a list") }
        return array
    }

    static func string(_ value: Any?, _ path: String) throws -> String {
        guard let string = value as? String else { throw RecommendationsError.format("\(path) is not text") }
        return string
    }

    static func bool(_ value: Any?, _ path: String) throws -> Bool {
        guard let value, CFGetTypeID(value as CFTypeRef) == CFBooleanGetTypeID(), let flag = value as? Bool else {
            throw RecommendationsError.format("\(path) is not true or false")
        }
        return flag
    }

    static func int(_ value: Any?, _ path: String) throws -> Int {
        guard let value, CFGetTypeID(value as CFTypeRef) == CFNumberGetTypeID() else {
            throw RecommendationsError.format("\(path) is not a whole number")
        }
        let number = value as! CFNumber
        var result: Int64 = 0
        guard !CFNumberIsFloatType(number), CFNumberGetValue(number, .sInt64Type, &result),
              result >= Int64(Int32.min), result <= Int64(Int32.max) else {
            throw RecommendationsError.format("\(path) is not a whole number")
        }
        return Int(result)
    }

    /// No control characters (a tab included).
    static func printable(_ s: String) -> Bool {
        !s.unicodeScalars.contains { $0.value < 0x20 || $0.value == 0x7f || (0x80..<0xa0).contains($0.value) }
    }
}
