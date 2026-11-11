// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

// Steam DLC in the app (docs/STEAM_LIBRARY.md, "DLC"): the owned DLC of the game whose page is open, what the player
// chose, and the DLC part of every download job. SteamOwnedLibrary owns one (`SteamOwnedLibrary.dlc`) and asks it for
// the request each download carries; the game page's live backend (BCDGamePageLiveDLC.swift) shows and changes it.
//
// Keyed: `env.MADEIRA_STEAM_DLC = 0` turns all of it off (no lookup, no section, no question; a download is what it was).
// A game the player never chose DLC for, with none installed, never looks anything up at download time.
//
// BCD alone does it: its own Steam sign-in and license list, its own depot downloader. Valve's client, which BCD Dock
// starts the game through, downloads nothing; it finds the DLC depots installed and recorded.
// Log tag: [steam-dlc] (App IDs, counts and short reason codes; never account data).

@MainActor final class SteamDLCService {
    /// Off with `env.MADEIRA_STEAM_DLC = 0` (the library then downloads games as it did before DLC support).
    static var enabled: Bool { SteamSignIn.flag("MADEIRA_STEAM_DLC", default: true) }

    private let fetcher: SteamLibraryFetcher
    /// The library may connect now (signed in, no game session holds the account).
    private let canConnect: () -> Bool
    /// Queues a download of the game (SteamOwnedLibrary.install).
    private let startDownload: (Int) -> Void

    let choices: SteamDLCChoices
    /// A change the page should show: a lookup finished, a toggle moved.
    let changed = PassthroughSubject<Void, Never>()

    /// The owned DLC of the games looked up in this run, by App ID.
    private(set) var reports: [Int: SteamDLCReport] = [:]
    private var looking = Set<Int>()
    /// Apps whose "not started" line was logged (once a run).
    private var skippedLogged = Set<Int>()
    /// When each report was read: one older than two minutes is read again the next time the page opens (a DLC bought or
    /// a library refresh in between shows up), while the old one stays on screen until the new one arrives.
    private var loadedAt: [Int: Date] = [:]
    /// When a lookup failed, so a page that opens again waits a minute before it tries again.
    private var failedAt: [Int: Date] = [:]
    /// The toggles of an installed game that are not applied yet: the DLC the player wants installed.
    private(set) var pending: [Int: Set<UInt32>] = [:]
    /// Downloads that only take DLC away (nothing is downloaded or checked), until their job starts.
    private var removalOnly = Set<Int>()

    /// `choicesFile`: `steam-dlc.json` next to the library cache (SteamOwnedLibrary's support folder).
    init(fetcher: SteamLibraryFetcher, choicesFile: URL, canConnect: @escaping () -> Bool, startDownload: @escaping (Int) -> Void) {
        self.fetcher = fetcher
        self.canConnect = canConnect
        self.startDownload = startDownload
        choices = SteamDLCChoices(file: choicesFile)
    }

    /// The library `appmanifest_<appid>.acf` records live in: the one the game is installed in (BCD's own folder in BCD Dock's
    /// Steam library, or a SteamLibrary on an external drive, ExternalDrives.swift), else the one its download goes to.
    private func steamApps(_ appID: Int) -> URL {
        if let library = SteamGamesModel.shared.games.first(where: { $0.id == appID })?.library {
            return SteamOwnedLibrary.drive.appendingPathComponent(library, isDirectory: true)
        }
        return SteamOwnedLibrary.shared.steamApps(for: appID)
    }

    // MARK: What is installed and wanted

    /// The DLC the install record states as installed.
    func installed(_ appID: Int) -> Set<UInt32> { Set(SteamDLCRecord.installedDLC(appID: appID, steamApps: steamApps(appID)).keys) }

    /// What the page shows as chosen: the toggles not yet applied, else the saved choice, else what is installed.
    func desired(_ appID: Int) -> Set<UInt32> { pending[appID] ?? choices.wanted(appID) ?? installed(appID) }

    func report(_ appID: Int) -> SteamDLCReport? { reports[appID] }

    /// A lookup of the game's DLC runs now (the page's question waits for it).
    func isLooking(_ appID: Int) -> Bool { looking.contains(appID) }

    /// The last lookup failed recently (or could not start): the page then asks no DLC question.
    func lookupFailed(_ appID: Int) -> Bool {
        guard reports[appID] == nil else { return false }
        if let failed = failedAt[appID] { return Date().timeIntervalSince(failed) < 5 }
        return !looking.contains(appID)
    }

    // MARK: Lookup

    /// Looks the game's owned DLC up (one product-info request, the license scan once a run) unless it was read in the last
    /// two minutes. The page shows the result when `changed` fires.
    func load(_ appID: Int, force: Bool = false) {
        guard Self.enabled, appID > 0, appID <= Int(UInt32.max), !looking.contains(appID) else { return }
        guard canConnect() else {
            // Not signed in, or a game session holds the account: nothing is looked up (and the question is not asked).
            if skippedLogged.insert(appID).inserted { SteamLog.event("[steam-dlc] lookup app=\(appID) not started reason=no-connection-allowed") }
            return
        }
        if !force, reports[appID] != nil, let at = loadedAt[appID], Date().timeIntervalSince(at) < 120 { return }
        if !force, let failed = failedAt[appID], Date().timeIntervalSince(failed) < 5 { return }
        looking.insert(appID)
        let began = Date()
        SteamLog.event("[steam-dlc] lookup app=\(appID) start")
        // The Steam connection comes up on demand (SteamSession.ensureConnected), as it does for a download: the first request
        // of the lookup connects and logs on.
        Task { [weak self] in
            guard let self else { return }
            let ms = { Int(Date().timeIntervalSince(began) * 1000) }
            do {
                guard let report = try await self.fetcher.dlcReport(appID: UInt32(appID)) else {
                    self.looking.remove(appID)
                    self.failedAt[appID] = Date()
                    SteamLog.event("[steam-dlc] lookup app=\(appID) failed reason=no-app-info ms=\(ms())")
                    self.changed.send()
                    return
                }
                let news = self.reports[appID] != report
                self.reports[appID] = report
                self.loadedAt[appID] = Date()
                self.failedAt[appID] = nil
                self.looking.remove(appID)
                SteamLog.event("[steam-dlc] lookup app=\(appID) owned=\(report.owned.count) no-download=\(report.ownedWithoutContent) not-owned=\(report.notOwned) own-depots=\(report.viaOwnInfo) ms=\(ms())")
                if news { self.changed.send() }
            } catch is CancellationError {
                self.looking.remove(appID)
            } catch {
                self.looking.remove(appID)
                self.failedAt[appID] = Date()
                SteamLog.event("[steam-dlc] lookup app=\(appID) failed reason=\(SteamOwnedLibrary.reason(error)) ms=\(ms())")
                self.changed.send()
            }
        }
    }

    // MARK: The player's choice

    /// A toggle on an installed game's page.
    func toggle(_ appID: Int, dlc: UInt32, on: Bool) {
        var set = desired(appID)
        if on { set.insert(dlc) } else { set.remove(dlc) }
        pending[appID] = set
        changed.send()
    }

    /// Every DLC the account owns for the game goes on the list ("Install all owned DLC").
    func selectAllOwned(_ appID: Int) {
        guard let report = reports[appID] else { return }
        pending[appID] = desired(appID).union(report.owned.map(\.appID))
        changed.send()
    }

    /// The question before a first install, answered: exactly the DLC chosen in the chooser (`selected`), or the game alone (none).
    /// Only DLC the account owns with files for the game can be chosen (anything else is dropped). Remembered, so it is asked
    /// once for the game; the download that follows takes exactly this set.
    func chooseForNewInstall(_ appID: Int, selected: Set<UInt32>) {
        let owned = reports[appID]?.owned ?? []
        let valid = owned.isEmpty ? selected : selected.intersection(owned.map(\.appID))
        choices.set(appID, wanted: valid)
        pending[appID] = nil
        let bytes = owned.filter { valid.contains($0.appID) }.reduce(UInt64(0)) { $0.addingReportingOverflow($1.downloadBytes).overflow ? .max : $0 + $1.downloadBytes }
        SteamLog.event("[steam-dlc] choice app=\(appID) new-install selected=\(valid.count) of=\(owned.count) bytes=\(bytes)")
    }

    /// The same for "every owned DLC" or "the game alone".
    func chooseForNewInstall(_ appID: Int, includeAll: Bool) {
        chooseForNewInstall(appID, selected: includeAll ? Set(reports[appID]?.owned.map(\.appID) ?? []) : [])
    }

    /// The changes of the page's toggles: the DLC to download and the DLC to remove.
    func changes(_ appID: Int) -> (add: [SteamDLCItem], remove: Set<UInt32>) {
        let have = installed(appID), want = desired(appID)
        let add = (reports[appID]?.owned ?? []).filter { want.contains($0.appID) && !have.contains($0.appID) }
        return (add, have.subtracting(want))
    }

    /// Applies the toggles: the choice is kept and a download job starts. Only removals: a job that takes the DLC away
    /// without downloading or checking anything. Additions: the game's usual download, which verifies the files it has.
    func apply(_ appID: Int) {
        guard Self.enabled else { return }
        let (add, remove) = changes(appID)
        guard !add.isEmpty || !remove.isEmpty else { pending[appID] = nil; return }
        choices.set(appID, wanted: desired(appID))
        pending[appID] = nil
        if add.isEmpty { removalOnly.insert(appID) } else { removalOnly.remove(appID) }
        SteamLog.event("[steam-dlc] apply app=\(appID) add=\(add.count) remove=\(remove.count)")
        changed.send()
        startDownload(appID)
    }

    // MARK: A download job

    /// The DLC part of a download of `info`'s game (SteamOwnedLibrary.run). `.none` for a game nothing was chosen for
    /// and no DLC is installed in: no lookup, and the downloader runs as it always did.
    func request(for info: SteamAppInfo, steamApps: URL) async -> SteamDLCRequest {
        let appID = Int(info.appID)
        let only = removalOnly.remove(appID) != nil
        guard Self.enabled else { return .none }
        let installed = SteamDLCRecord.installedDLC(appID: appID, steamApps: steamApps)
        let wanted = choices.wanted(appID)
        if wanted == nil, installed.isEmpty { return .none }
        var owned: Set<UInt32>?
        do { owned = try await fetcher.ownedAppIDs() } catch { SteamLog.event("[steam-dlc] app=\(appID) owned-apps unavailable reason=\(SteamOwnedLibrary.reason(error))") }
        // DLC the game lists no Windows depot for (X-Plane 12's maps): their depots are in the DLC apps' own product info.
        var own: [SteamAppInfo.DepotInfo] = []
        if !only {
            let wish = wanted ?? Set(installed.keys)
            let lacking = SteamDLC.needingOwnInfo(app: info, candidates: wish.filter { owned?.contains($0) ?? true })
            if !lacking.isEmpty {
                do { own = try await fetcher.dlcOwnDepots(for: lacking) } catch {
                    SteamLog.event("[steam-dlc] app=\(appID) own-depots unavailable reason=\(SteamOwnedLibrary.reason(error))")
                }
            }
        }
        return SteamDLC.request(app: info, wanted: wanted, installed: installed, ownedApps: owned, removalOnly: only, ownDepots: own)
    }
}
