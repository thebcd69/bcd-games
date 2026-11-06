// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// The downloader's DLC steps (docs/STEAM_LIBRARY.md, "DLC"). DepotDownloader.install(_:steamApps:dlc:) calls them; with
// `SteamDLCRequest.none` none of them runs and the install is what it always was.
//
// The same downloader, the same keys, manifests and chunk checks as the game's own depots: a DLC depot is one more depot
// of the same install, recorded in the install record with its `dlcappid`. Steam still decides what the account may
// download: a depot key is only issued for an owned depot, and a refusal leaves that DLC out without failing the game.

extension DepotDownloader {
    /// The key of a DLC depot and the app it was issued under. Steam lists a DLC's depots with the game that owns it, so
    /// the game's App ID is tried first; the DLC's own App ID second. nil when Steam refuses both (the account does not
    /// own that content); any other failure (no connection, a timeout) is thrown, as it is for the game's own depots.
    func dlcDepotKey(depotID: UInt32, appID: UInt32, dlcAppID: UInt32) async throws -> (key: Data, appID: UInt32)? {
        do {
            return (try await depotKey(depotID: depotID, appID: appID, logRefusal: false), appID)
        } catch SteamError.depotKeyNotFound {
            // The next try. Not a refusal yet (device round 3: X-Plane 12's map DLC get their keys under their own App ID).
            SteamLog.event("[steam-dlc] key depot=\(depotID) not under the game's App ID \(appID); trying the DLC's own (\(dlcAppID))")
        }
        do {
            let key = try await depotKey(depotID: depotID, appID: dlcAppID, logRefusal: false)
            SteamLog.event("[steam-dlc] key depot=\(depotID) issued under the DLC's App ID \(dlcAppID)")
            return (key, dlcAppID)
        } catch SteamError.depotKeyNotFound {
            SteamLog.event("[steam-dlc] key depot=\(depotID) refused under both App IDs (game \(appID), DLC \(dlcAppID)): that DLC is left out")
            return nil
        }
    }

    /// The manifest of a depot an install record names (a removed DLC's, or one that stays): the recorded one first, the
    /// current public one when the content servers no longer have it.
    private func recordedManifest(_ depot: SteamDLCRecord.Depot, app: SteamAppInfo, hosts: [String]) async throws -> DepotManifest {
        let info = app.depots.first { $0.depotID == depot.depotID }
        let dlc = depot.dlcAppID ?? info?.dlcAppID
        let issued: (key: Data, appID: UInt32)
        if let dlc {
            guard let got = try await dlcDepotKey(depotID: depot.depotID, appID: app.appID, dlcAppID: dlc) else {
                throw SteamError.depotKeyNotFound(depot.depotID)
            }
            issued = got
        } else {
            issued = (try await depotKey(depotID: depot.depotID, appID: app.appID), app.appID)
        }
        let contentApp = dlc == nil && !app.freeToDownload ? (info?.fromApp ?? app.appID) : issued.appID
        var candidates: [UInt64] = []
        if let recorded = depot.manifest { candidates.append(recorded) }
        if let current = info?.manifestID(app.branch), !candidates.contains(current) { candidates.append(current) }
        var failure: Error = SteamError.manifestFetchFailed("Steam has no manifest for it.")
        for gid in candidates {
            try Task.checkCancellation()
            do {
                return try await fetchManifest(depotID: depot.depotID, appID: contentApp, manifestGID: gid, key: issued.key, hosts: hosts)
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                failure = error
            }
        }
        throw failure
    }

    private static func filePaths(_ manifests: [DepotManifest]) -> [String] {
        manifests.flatMap { $0.files.filter { !$0.isDirectory && $0.flags & 0x200 == 0 }.map(\.filename) }
    }

    /// Takes the files of removed DLC away, once the depots that stay are known (`kept`: their manifests): the files only
    /// the removed depots hold. Returns the record entries of the DLC that could not be removed (Steam had no manifest
    /// for one of its depots): those stay installed and recorded, and the next try takes them away.
    func pruneDLC(app: SteamAppInfo, installURL: URL, request: SteamDLCRequest, kept: [DepotManifest],
                  hosts: [String]) async throws -> [SteamDLCRecord.Depot] {
        let keptPaths = Self.filePaths(kept)
        var leftover: [SteamDLCRecord.Depot] = []
        for dlc in request.removedApps.sorted() {
            let depots = request.removed.filter { $0.dlcAppID == dlc }
            var manifests: [DepotManifest] = []
            do {
                for depot in depots { manifests.append(try await recordedManifest(depot, app: app, hosts: hosts)) }
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                leftover.append(contentsOf: depots)
                SteamLog.event("[steam-dlc] app=\(app.appID) dlc=\(dlc) remove-skipped reason=\(SteamOwnedLibrary.reason(error))")
                continue
            }
            let paths = SteamDLCRemoval.exclusivePaths(removed: Self.filePaths(manifests), kept: keptPaths)
            let deleted = SteamDLCRemoval.delete(paths, in: installURL)
            SteamLog.event("[steam-dlc] app=\(app.appID) dlc=\(dlc) files-removed=\(deleted) of=\(paths.count)")
        }
        return leftover
    }

    /// Only the removal of DLC the player took away: the manifests of the depots the install record lists (those that
    /// stay, and the removed ones), the removed depots' own files deleted, the record written again without them. No depot
    /// is downloaded and no file is checked. Nothing is deleted and the record stays as it is when a manifest is missing.
    func removeDLCOnly(_ app: SteamAppInfo, steamApps: URL, request: SteamDLCRequest) async throws -> URL {
        let folderName = SteamInstallFiles.safeFolderName(app.installDir.isEmpty ? "app_\(app.appID)" : app.installDir)
        let installURL = steamApps.appendingPathComponent("common", isDirectory: true)
            .appendingPathComponent(folderName, isDirectory: true)
        guard let state = SteamDLCRecord.read(appID: Int(app.appID), steamApps: steamApps) else {
            throw SteamFileError.invalid("Steam's install record for this game can't be read.")
        }
        let gone = Set(request.removed.map(\.depotID))
        guard !gone.isEmpty else { return installURL }
        SteamLog.event(SteamDLC.summaryLine(app: app.appID, request: request, depots: []))
        let hosts = try await contentServers(appID: app.appID)
        guard !hosts.isEmpty else { throw SteamError.chunkDownloadFailed("Steam offered no content servers.") }
        let shared = Set(state.shared.map(\.depotID))
        var kept: [DepotManifest] = []
        for depot in state.depots where !gone.contains(depot.depotID) && !shared.contains(Int(depot.depotID)) {
            try Task.checkCancellation()
            kept.append(try await recordedManifest(depot, app: app, hosts: hosts))
        }
        let leftover = try await pruneDLC(app: app, installURL: installURL, request: request, kept: kept, hosts: hosts)
        // What was removed leaves the record; a DLC that could not be removed stays recorded, and the next try takes it away.
        let notDone = Set(leftover.map(\.depotID))
        if gone.subtracting(notDone).isEmpty == false {
            try SteamDLCRecord.rewrite(appID: Int(app.appID), steamApps: steamApps, dropping: gone.subtracting(notDone))
        }
        let failedApps = Set(leftover.compactMap(\.dlcAppID))
        for line in SteamDLC.resultLines(app: app.appID, request: request, depots: [], planned: [], refused: [], notRemoved: failedApps) {
            SteamLog.event(line)
        }
        guard leftover.isEmpty else { throw SteamError.manifestFetchFailed("Steam didn't send what BCD needs to remove this DLC. Try again.") }
        return installURL
    }
}
