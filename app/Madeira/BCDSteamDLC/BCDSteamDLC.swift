// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Steam DLC for the games BCD downloads itself (docs/STEAM_LIBRARY.md, "DLC").
//
// The rules, Foundation only (tests/host/check-steam-dlc.py compiles this file as it is, with
// SteamAppInfo.swift, SteamKeyValues.swift, SteamInstall.swift and AppManifestWriter.swift):
//
// - which DLC an app has, which of it the account owns, and the depots each owned DLC adds;
// - which depots an install takes with the DLC the player chose (with none chosen: exactly the
//   depots SteamAppInfo.installDepots() takes, so a game without DLC and a player who does
//   nothing get today's install);
// - what an install writes into `appmanifest_<appid>.acf` for them (the DLC depots in
//   `InstalledDepots` with their `dlcappid`, as Valve's client records them), and how a DLC's
//   files are taken away again;
// - the player's choice, kept in a small file.
//
// BCD alone does all of it: BCD's own Steam sign-in, license list and depot downloader. Valve's
// client, which BCD Dock starts the game through, downloads nothing: it only finds the DLC depots
// installed and recorded.
// Log tag: [steam-dlc] (App IDs, counts and short reason codes; never account data).

// MARK: - Which depots an install takes

extension SteamAppInfo {
    /// The DLC apps this app knows: the ones it lists (`extended.listofdlc`) and the ones its depots
    /// name (`dlcappid`), without itself.
    var dlcApps: Set<UInt32> {
        var all = Set(dlcList)
        for depot in depots { if let dlc = depot.dlcAppID, dlc != appID { all.insert(dlc) } }
        all.remove(appID)
        return all
    }

    /// Whether a depot could be installed for `os` and `language` if it were not DLC content: the rules
    /// of `installDepots()` without its DLC rule. A depot another app owns (`depotfromapp`) is left out
    /// of the DLC's depots: Valve records such depots as shared ones, which BCD's DLC support does not do.
    private func ordinaryRules(_ d: DepotInfo, os: String, language: String) -> Bool {
        d.supports(os: os) && !d.isSharedInstall && d.publicManifestID != nil && !d.lowViolence &&
            (d.language.isEmpty || d.language.caseInsensitiveCompare(language) == .orderedSame)
    }

    /// The architecture `installDepots()` ends up with: "64", or "32" when the app publishes only
    /// 32-bit depots (its fallback), so a DLC's depots follow the game's.
    private func installArch(os: String, arch: String, language: String) -> String {
        guard arch == "64" else { return arch }
        let has64 = depots.contains { ordinaryRules($0, os: os, language: language) && $0.dlcAppID == nil && $0.osarch == "64" }
        let has32 = depots.contains { $0.osarch == "32" && $0.supports(os: os) }
        return !has64 && has32 ? "32" : arch
    }

    /// The depots that add DLC content a Windows install could take, by DLC app, in depot order.
    func dlcDepots(os: String = "windows", arch: String = "64", language: String? = nil) -> [UInt32: [DepotInfo]] {
        let language = language ?? depotLanguage   // the game language (SteamLanguage); English unless the player chose another
        let chosen = installArch(os: os, arch: arch, language: language)
        var byDLC: [UInt32: [DepotInfo]] = [:]
        for d in depots.sorted(by: { $0.depotID < $1.depotID }) {
            guard let dlc = d.dlcAppID, dlc != appID, d.fromApp == nil, ordinaryRules(d, os: os, language: language),
                  d.osarch.isEmpty || d.osarch == chosen else { continue }
            byDLC[dlc, default: []].append(d)
        }
        return byDLC
    }

    /// The depots this app (a DLC app's own product info) declares itself, tagged as that DLC's: the ones with a public
    /// manifest. Some DLC (X-Plane 12's maps) are not listed by the game with `dlcappid` at all; their depots are in the DLC's
    /// own product info. A depot another app owns (`depotfromapp`) stays out: the game lists it itself.
    func dlcOwnDepots() -> [DepotInfo] {
        depots.filter { $0.publicManifestID != nil && $0.fromApp == nil }.map { depot in
            var tagged = depot
            tagged.dlcAppID = appID
            return tagged
        }
    }

    /// A copy of this app with the DLC depots `extra` (from the DLC apps' own product info, tagged `dlcAppID`): a depot the
    /// app lists already stays as it is, unless it names no manifest (the DLC's own entry then supplies one).
    func addingDLCDepots(_ extra: [DepotInfo]) -> SteamAppInfo {
        guard !extra.isEmpty else { return self }
        var copy = self
        for depot in extra {
            if let index = copy.depots.firstIndex(where: { $0.depotID == depot.depotID }) {
                guard copy.depots[index].publicManifestID == nil else { continue }
                copy.depots[index] = depot
            } else {
                copy.depots.append(depot)
            }
        }
        return copy
    }

    /// The depots an install takes together with the content of the DLC apps in `dlc`. With none, this is
    /// `installDepots()` as it is.
    func installDepots(including dlc: Set<UInt32>, os: String = "windows", arch: String = "64",
                       language: String? = nil) -> [DepotInfo] {
        let language = language ?? depotLanguage
        let base = installDepots(os: os, arch: arch, language: language)
        guard !dlc.isEmpty else { return base }
        let extra = dlcDepots(os: os, arch: arch, language: language).filter { dlc.contains($0.key) }.flatMap(\.value)
        return (base + extra).sorted { $0.depotID < $1.depotID }
    }
}

// MARK: - What the account owns

/// One owned DLC with something to download, as the game page lists it.
struct SteamDLCItem: Equatable, Identifiable, Sendable {
    var appID: UInt32
    var name: String
    /// The depots that hold its content for a Windows install.
    var depotIDs: [UInt32]
    /// The compressed download, from PICS (the legacy `maxsize` when it sends none); about.
    var downloadBytes: UInt64
    /// The size on disk, from PICS.
    var sizeBytes: UInt64
    var id: UInt32 { appID }
}

/// What a game's page says about its DLC.
struct SteamDLCReport: Equatable, Sendable {
    var appID: UInt32
    /// The DLC the account owns that has depots to download, by name.
    var owned: [SteamDLCItem] = []
    /// DLC the account owns that needs no download for a Windows install (its depots are none, or other
    /// platforms', or it unlocks content the game already has): Valve's client checks the license itself.
    var ownedWithoutContent = 0
    /// DLC the game has that the account does not own.
    var notOwned = 0
    /// Of the owned DLC, how many have their depots in their own product info (the game does not list them).
    var viaOwnInfo = 0

    var isEmpty: Bool { owned.isEmpty }
    var downloadBytes: UInt64 { owned.reduce(0) { $0.addingReportingOverflow($1.downloadBytes).overflow ? .max : $0 + $1.downloadBytes } }
    var sizeBytes: UInt64 { owned.reduce(0) { $0.addingReportingOverflow($1.sizeBytes).overflow ? .max : $0 + $1.sizeBytes } }
}

enum SteamDLC {
    /// The App IDs a set of license packages (PICS package buffers) hold: what the account owns.
    static func ownedApps(inPackages buffers: [Data]) -> Set<UInt32> {
        var apps = Set<UInt32>()
        for buffer in buffers { apps.formUnion(VDFParser.parsePackageAppIDs(from: buffer)) }
        return apps
    }

    /// The compressed bytes a depot needs: PICS's modern figure, else the legacy `maxsize`.
    static func downloadBytes(_ depot: SteamAppInfo.DepotInfo) -> UInt64 {
        depot.publicDownloadBytes > 0 ? depot.publicDownloadBytes : depot.maxSize
    }

    private static func add(_ a: UInt64, _ b: UInt64) -> UInt64 {
        let (sum, overflow) = a.addingReportingOverflow(b)
        return overflow ? .max : sum
    }

    /// The DLC of `app` the account owns (`ownedApps`: the App IDs its licenses hold), with the depots each
    /// adds and its names (`names`: the DLC apps' own product info; a missing name reads "DLC <App ID>").
    static func report(for app: SteamAppInfo, ownedApps: Set<UInt32>, names: [UInt32: String] = [:],
                       os: String = "windows", arch: String = "64", language: String? = nil) -> SteamDLCReport {
        var report = SteamDLCReport(appID: app.appID)
        let language = language ?? app.depotLanguage
        let contents = app.dlcDepots(os: os, arch: arch, language: language)
        for dlc in app.dlcApps.sorted() {
            guard ownedApps.contains(dlc) else { report.notOwned += 1; continue }
            guard let depots = contents[dlc], !depots.isEmpty else { report.ownedWithoutContent += 1; continue }
            let name = (names[dlc] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            report.owned.append(SteamDLCItem(
                appID: dlc, name: name.isEmpty ? "DLC \(dlc)" : name, depotIDs: depots.map(\.depotID),
                downloadBytes: depots.reduce(0) { add($0, downloadBytes($1)) },
                sizeBytes: depots.reduce(0) { add($0, $1.publicSizeBytes > 0 ? $1.publicSizeBytes : $1.maxSize) }))
        }
        report.owned.sort {
            let order = $0.name.localizedStandardCompare($1.name)
            return order == .orderedSame ? $0.appID < $1.appID : order == .orderedAscending
        }
        return report
    }
}

// MARK: - What one install does about DLC

/// The DLC part of one download job, worked out before the downloader runs. `none` (nothing installed, nothing
/// chosen) is every download that was started before BCD had DLC support: the downloader then runs as it always did.
struct SteamDLCRequest: Equatable {
    /// The DLC whose depots this run takes (the ones to have installed afterwards): chosen, owned, listed.
    var selected: Set<UInt32> = []
    /// Installed DLC records the run keeps as they are, because the run does not take their depots (not listed any
    /// more, not owned any more, or Steam refused the key): the install record must not lose them.
    var carried: [SteamDLCRecord.Depot] = []
    /// The record entries of the DLC the player took away: their files go once the depots that stay are fetched
    /// (a file a removed depot shares with a depot that stays is kept).
    var removed: [SteamDLCRecord.Depot] = []
    /// The DLC apps `removed` belongs to.
    var removedApps: Set<UInt32> = []
    /// Depots of the selected DLC that come from the DLC apps' own product info (the game does not list them): the
    /// downloader adds them to the game's depots (`addingDLCDepots`) before it chooses.
    var extraDepots: [SteamAppInfo.DepotInfo] = []
    /// How many DLC the account owns for this app (for the log).
    var owned = 0
    /// Only the removal: no depot is downloaded, nothing is verified.
    var removalOnly = false

    static let none = SteamDLCRequest()
    /// Whether the run does anything about DLC (takes a depot, keeps a record entry or removes one).
    var hasWork: Bool { !selected.isEmpty || !removed.isEmpty || !carried.isEmpty }

    static func == (a: SteamDLCRequest, b: SteamDLCRequest) -> Bool {
        a.selected == b.selected && a.carried == b.carried && a.removed == b.removed && a.removedApps == b.removedApps
            && a.owned == b.owned && a.removalOnly == b.removalOnly
            && a.extraDepots.map(\.depotID) == b.extraDepots.map(\.depotID)
    }
}

extension SteamDLC {
    /// Works the job out.
    /// - `wanted`: what the player chose for the app (nil: never chosen, so what is installed stays).
    /// - `installed`: the DLC depots the install record states, by DLC app.
    /// - `ownedApps`: the account's apps, or nil when they could not be read (then nothing is filtered by it:
    ///   Steam's own refusal of a depot key decides).
    /// - `ownDepots`: depots of DLC the game does not list, from the DLC apps' own product info (`dlcOwnDepots()`).
    static func request(app base: SteamAppInfo, wanted: Set<UInt32>?, installed: [UInt32: [SteamDLCRecord.Depot]],
                        ownedApps: Set<UInt32>?, removalOnly: Bool = false,
                        ownDepots: [SteamAppInfo.DepotInfo] = []) -> SteamDLCRequest {
        // Nothing installed and nothing chosen: no DLC work at all, and no lookup was needed.
        if wanted == nil, installed.isEmpty { return .none }
        var request = SteamDLCRequest()
        let app = base.addingDLCDepots(ownDepots)
        let known = app.dlcApps
        let wish = wanted ?? Set(installed.keys)
        // A DLC the player took away: installed and not wanted. Nothing else is ever removed.
        for (dlc, depots) in installed where !wish.contains(dlc) {
            request.removedApps.insert(dlc)
            request.removed.append(contentsOf: depots)
        }
        request.removed.sort { $0.depotID < $1.depotID }
        let plannable = app.dlcDepots()
        for dlc in wish {
            let owns = ownedApps?.contains(dlc) ?? true
            if known.contains(dlc), owns, plannable[dlc]?.isEmpty == false {
                request.selected.insert(dlc)
            } else {
                // Installed, wanted, but not takeable now: keep the record entries as they are.
                request.carried.append(contentsOf: installed[dlc] ?? [])
            }
        }
        request.carried.sort { $0.depotID < $1.depotID }
        // Only the depots the install would take: the selected DLC's Windows depots (for the game's architecture and language).
        let takeable = Set(plannable.values.flatMap { $0.map(\.depotID) })
        request.extraDepots = ownDepots.filter { depot in
            takeable.contains(depot.depotID) && (depot.dlcAppID.map(request.selected.contains) ?? false)
        }
        request.owned = known.filter { ownedApps?.contains($0) ?? false }.count
        request.removalOnly = removalOnly
        return request
    }

    /// The DLC among `candidates` the game itself gives no Windows depot for: their own product info is where to look.
    static func needingOwnInfo(app: SteamAppInfo, candidates: Set<UInt32>) -> [UInt32] {
        let listed = app.dlcDepots()
        return candidates.intersection(app.dlcApps).filter { listed[$0]?.isEmpty != false }.sorted()
    }

    /// DLC depot -> DLC app, for the depots a run takes.
    static func dlcByDepot(_ depots: [SteamAppInfo.DepotInfo]) -> [UInt32: UInt32] {
        var map: [UInt32: UInt32] = [:]
        for d in depots { if let dlc = d.dlcAppID { map[d.depotID] = dlc } }
        return map
    }

    /// `[steam-dlc] app=N owned=K selected=S depots=a,b bytes=X`: what the download takes besides the game.
    static func summaryLine(app: UInt32, request: SteamDLCRequest, depots: [SteamAppInfo.DepotInfo]) -> String {
        let extra = depots.filter { $0.dlcAppID != nil }
        let bytes = extra.reduce(UInt64(0)) { add($0, downloadBytes($1)) }
        let ids = extra.map { String($0.depotID) }.joined(separator: ",")
        var line = "[steam-dlc] app=\(app) owned=\(request.owned) selected=\(request.selected.count) depots=\(ids.isEmpty ? "-" : ids) bytes=\(bytes)"
        if !request.removedApps.isEmpty { line += " removing=\(request.removedApps.count)" }
        if !request.carried.isEmpty { line += " carried=\(request.carried.count)" }
        return line
    }

    /// `[steam-dlc] result app=N dlc=D depots=K state=...`, one per DLC the run was about, in DLC order. The states:
    /// `installed` (every depot of it was fetched and recorded), `partial`, `refused` (Steam gave no key for any depot),
    /// `removed`, `remove-failed` (its files stay, and so does its record entry).
    static func resultLines(app: UInt32, request: SteamDLCRequest, depots: [SteamAppInfo.DepotInfo], planned: Set<UInt32>,
                            refused: Set<UInt32>, notRemoved: Set<UInt32>) -> [String] {
        var lines: [String] = []
        let byDLC = Dictionary(grouping: depots.filter { $0.dlcAppID != nil }, by: { $0.dlcAppID! })
        for dlc in request.selected.sorted() {
            let all = byDLC[dlc] ?? []
            let got = all.filter { planned.contains($0.depotID) }.count
            let state = got == all.count && !all.isEmpty ? "installed" : (got > 0 ? "partial" : (refused.contains(dlc) ? "refused" : "none"))
            lines.append("[steam-dlc] result app=\(app) dlc=\(dlc) depots=\(got)/\(all.count) state=\(state)")
        }
        for dlc in request.removedApps.sorted() {
            let count = request.removed.filter { $0.dlcAppID == dlc }.count
            lines.append("[steam-dlc] result app=\(app) dlc=\(dlc) depots=\(count) state=\(notRemoved.contains(dlc) ? "remove-failed" : "removed")")
        }
        return lines
    }
}

// MARK: - The install record

/// The install record (`appmanifest_<appid>.acf`) as far as DLC needs it: read back to see which DLC is installed, and
/// written again, as the downloader writes it, when a DLC is taken away.
enum SteamDLCRecord {
    /// One entry of `InstalledDepots`.
    struct Depot: Equatable, Sendable {
        var depotID: UInt32
        var manifest: UInt64?
        var size: Int64?
        var dlcAppID: UInt32?
    }

    struct SharedDepot: Equatable, Sendable {
        var depotID: Int
        var ownerAppID: Int
    }

    struct State: Equatable {
        var name: String
        var installDir: String
        var buildID: UInt32
        var lastOwner: UInt64
        var sizeOnDisk: UInt64
        var depots: [Depot]
        var shared: [SharedDepot]
        var customExecutables: [String]
        /// The beta branch the record names (`BetaKey`), "public" for none: a DLC taken away keeps the game on its version.
        var betaKey = "public"
    }

    static func url(appID: Int, steamApps: URL) -> URL { steamApps.appendingPathComponent("appmanifest_\(appID).acf") }

    /// The record of an app, or nil when there is no readable one (or it names another app).
    static func read(appID: Int, steamApps: URL) -> State? {
        guard let data = try? Data(contentsOf: url(appID: appID, steamApps: steamApps)), data.count <= 1 << 20,
              var parser = try? SteamKeyValues(data), let root = try? parser.read(),
              let record = root["AppState"], record["appid"]?.string == String(appID) else { return nil }
        var depots: [Depot] = []
        for (key, value) in (record["InstalledDepots"]?.fields ?? [:]).sorted(by: { $0.key < $1.key }).prefix(4096) {
            guard let id = UInt32(key), id > 0 else { continue }
            depots.append(Depot(depotID: id, manifest: value["manifest"]?.string.flatMap { UInt64($0) },
                                size: value["size"]?.string.flatMap { Int64($0) },
                                dlcAppID: value["dlcappid"]?.string.flatMap { UInt32($0) }))
        }
        depots.sort { $0.depotID < $1.depotID }
        var shared: [SharedDepot] = []
        for (key, value) in (record["SharedDepots"]?.fields ?? [:]).sorted(by: { $0.key < $1.key }).prefix(1024) {
            if let id = Int(key), let owner = value.string.flatMap({ Int($0) }) { shared.append(SharedDepot(depotID: id, ownerAppID: owner)) }
        }
        shared.sort { $0.depotID < $1.depotID }
        let custom = (record["CheckGuid"]?.fields ?? [:]).compactMap { entry in Int(entry.key).map { ($0, entry.value.string ?? "") } }
            .sorted { $0.0 < $1.0 }.map(\.1).filter { !$0.isEmpty }
        return State(name: record["name"]?.string ?? "", installDir: record["installdir"]?.string ?? "",
                     buildID: record["buildid"]?.string.flatMap { UInt32($0) } ?? 0,
                     lastOwner: record["LastOwner"]?.string.flatMap { UInt64($0) } ?? 0,
                     sizeOnDisk: record["SizeOnDisk"]?.string.flatMap { UInt64($0) } ?? 0,
                     depots: depots, shared: shared, customExecutables: custom,
                     betaKey: SteamInstallFiles.betaKey(in: record) ?? "public")
    }

    /// The DLC the record states as installed, by DLC app (every depot of it that is recorded).
    static func installedDLC(appID: Int, steamApps: URL) -> [UInt32: [Depot]] {
        guard let state = read(appID: appID, steamApps: steamApps) else { return [:] }
        return installedDLC(in: state)
    }

    static func installedDLC(in state: State) -> [UInt32: [Depot]] {
        var byDLC: [UInt32: [Depot]] = [:]
        for depot in state.depots { if let dlc = depot.dlcAppID { byDLC[dlc, default: []].append(depot) } }
        return byDLC
    }

    /// The record's `InstalledDepots` entry for a depot a run installed.
    static func entry(_ depot: Depot) -> AppManifestWriter.InstalledDepot {
        AppManifestWriter.InstalledDepot(depotID: Int(depot.depotID), manifestGID: depot.manifest, size: depot.size,
                                         dlcAppID: depot.dlcAppID.map(Int.init))
    }

    /// The record's depots for a run: the ones it installed (`installed`, with each DLC depot's `dlcappid`) and the
    /// DLC entries it keeps as they are. With nothing carried this is `installed` unchanged.
    static func installedDepots(_ installed: [AppManifestWriter.InstalledDepot],
                                carried: [Depot]) -> [AppManifestWriter.InstalledDepot] {
        guard !carried.isEmpty else { return installed }
        let have = Set(installed.map(\.depotID))
        return (installed + carried.map(entry).filter { !have.contains($0.depotID) }).sorted { $0.depotID < $1.depotID }
    }

    /// The DLC depots of `plans` get their `dlcappid`: `dlcByDepot` names the DLC app of each.
    static func tag(_ installed: [AppManifestWriter.InstalledDepot], dlcByDepot: [UInt32: UInt32]) -> [AppManifestWriter.InstalledDepot] {
        installed.map { depot in
            guard let dlc = dlcByDepot[UInt32(depot.depotID)] else { return depot }
            return AppManifestWriter.InstalledDepot(depotID: depot.depotID, manifestGID: depot.manifestGID, size: depot.size,
                                                    dlcAppID: Int(dlc))
        }
    }

    /// Writes the record again without the given depots (a DLC taken away), the way the downloader writes it: the
    /// same app, name, folder, build, owner and shared depots; the size on disk goes down by what the depots held.
    /// Throws when there is no readable record.
    static func rewrite(appID: Int, steamApps: URL, dropping depots: Set<UInt32>) throws {
        guard let state = read(appID: appID, steamApps: steamApps) else {
            throw SteamFileError.invalid("Steam's install record for this game can't be read.")
        }
        let gone = state.depots.filter { depots.contains($0.depotID) }
        let freed = gone.reduce(UInt64(0)) { $0 &+ UInt64(max(0, $1.size ?? 0)) }
        let kept = state.depots.filter { !depots.contains($0.depotID) }.map(entry)
        try AppManifestWriter.writeManifest(
            appID: UInt32(appID), name: state.name, installDir: state.installDir, buildID: state.buildID,
            steamID: state.lastOwner, sizeOnDisk: state.sizeOnDisk > freed ? state.sizeOnDisk - freed : 0,
            steamAppsPath: steamApps.path, installedDepots: kept,
            sharedDepots: state.shared.map { (depotID: $0.depotID, ownerAppID: $0.ownerAppID) },
            customExecutables: state.customExecutables, betaKey: state.betaKey)
    }
}

// MARK: - Taking a DLC's files away

enum SteamDLCRemoval {
    /// The files only the removed depots hold: the paths of `removed` (as the manifests spell them) that no path of
    /// `kept` names, compared as Windows compares them (without case, "/" and "\" the same). A DLC depot may replace
    /// a game file; such a file stays.
    static func exclusivePaths(removed: [String], kept: [String]) -> [String] {
        func key(_ path: String) -> String { path.replacingOccurrences(of: "\\", with: "/").lowercased() }
        let keep = Set(kept.map(key))
        var seen = Set<String>()
        return removed.filter { path in
            let k = key(path)
            return !keep.contains(k) && seen.insert(k).inserted
        }
    }

    /// Deletes files under `installURL`, given as install-relative paths: each found one name at a time, exactly or
    /// else without case (the downloader folds folder spellings), only regular files that stay inside `installURL`.
    /// Folders left empty go too, up to (never including) `installURL`. Returns how many files were deleted.
    @discardableResult
    static func delete(_ paths: [String], in installURL: URL) -> Int {
        let fm = FileManager.default
        let root = installURL.resolvingSymlinksInPath().standardizedFileURL
        var listings: [String: [String]] = [:]
        func names(_ directory: URL) -> [String] {
            if let cached = listings[directory.path] { return cached }
            let found = (try? fm.contentsOfDirectory(atPath: directory.path)) ?? []
            listings[directory.path] = found
            return found
        }
        var deleted = 0
        var touched = Set<String>()
        var folded: [String: String] = [:]
        for path in paths {
            guard let relative = SteamInstallFiles.safeRelativePath(path, folded: &folded) else { continue }
            var url = root
            var found = true
            for part in relative.split(separator: "/").map(String.init) {
                let list = names(url)
                guard let name = list.first(where: { $0 == part }) ?? list.first(where: { $0.caseInsensitiveCompare(part) == .orderedSame }) else {
                    found = false; break
                }
                url.appendPathComponent(name)
            }
            guard found, url.path.hasPrefix(root.path + "/") else { continue }
            // A regular file only: attributesOfItem does not follow a link, so a link reports as one.
            guard (try? fm.attributesOfItem(atPath: url.path))?[.type] as? FileAttributeType == .typeRegular else { continue }
            guard (try? fm.removeItem(at: url)) != nil else { continue }
            deleted += 1
            let parent = url.deletingLastPathComponent()
            listings[parent.path] = nil
            touched.insert(parent.path)
        }
        // Folders the removal emptied, deepest first.
        for path in touched.sorted(by: { $0.count > $1.count }) {
            var directory = URL(fileURLWithPath: path)
            while directory.path.hasPrefix(root.path + "/") {
                guard let rest = try? fm.contentsOfDirectory(atPath: directory.path), rest.isEmpty,
                      (try? fm.removeItem(at: directory)) != nil else { break }
                directory.deleteLastPathComponent()
            }
        }
        return deleted
    }
}

// MARK: - The player's choice

/// Which DLC the player chose for each game, kept in `steam-dlc.json` next to the library cache: the DLC App IDs to have
/// installed (an empty list: the base game only). A game with no entry was never asked, so nothing changes for it.
final class SteamDLCChoices {
    private let file: URL
    private var apps: [Int: [UInt32]] = [:]

    init(file: URL) {
        self.file = file
        guard let data = try? Data(contentsOf: file), data.count <= 4 << 20,
              let stored = try? JSONDecoder().decode(Stored.self, from: data), stored.version == 1 else { return }
        for (key, list) in stored.apps.prefix(4096) {
            if let id = Int(key), id > 0 { apps[id] = Array(Set(list)).sorted() }
        }
    }

    private struct Stored: Codable { var version: Int; var apps: [String: [UInt32]] }

    func wanted(_ appID: Int) -> Set<UInt32>? { apps[appID].map(Set.init) }

    func set(_ appID: Int, wanted: Set<UInt32>) {
        guard appID > 0 else { return }
        apps[appID] = wanted.sorted()
        save()
    }

    func forget(_ appID: Int) {
        guard apps.removeValue(forKey: appID) != nil else { return }
        save()
    }

    private func save() {
        let stored = Stored(version: 1, apps: Dictionary(uniqueKeysWithValues: apps.map { (String($0.key), $0.value) }))
        guard let data = try? JSONEncoder().encode(stored) else { return }
        try? FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: file, options: .atomic)
    }
}
