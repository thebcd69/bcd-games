// SPDX-License-Identifier: GPL-3.0-or-later
// Madeira Converter Exception: see LICENSE-EXCEPTION.md
// Adapted by BCD (build 632) from upstream Madeira's EpicCloud.swift (when a sync runs, its steps and its decisions): BCD's Epic session,
// install records and per-game switches instead of upstream's, and BCD's game page (BCDGamePageLive.swift) instead of upstream's section view.
// Copyright 2026 thebcd69 (BCD) for the adaptation.
// Portions copyright 2026 Tabîrcă Nicolae-Eduard: code taken from https://github.com/willfaust/Madeira (pull request #443)
// under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.

import Combine
import Foundation

// Epic's cloud saves for the Epic games BCD installed (docs/EPIC.md, "Cloud saves"). OFF until the player turns it on for a game: a sync
// writes to the player's Epic account, so nothing is read from or sent to Epic's save service before that.
//
// - `env.BCD_EPIC_CLOUD = 1` (the game's own config, set by the page's "Turn on Epic cloud saves"; or bcd.cfg for every Epic game):
//   this game's saves are compared with Epic's cloud when BCD starts and when the game's page opens, never while a game runs; a newer
//   cloud save comes down (the files it replaces are copied to Files > BCD > Epic Cloud Backups first). Without the line nothing happens.
// - `env.BCD_EPIC_CLOUD_AUTO_UPLOAD = 1` (off by default): a newer save of this device also goes up without being asked. Otherwise
//   it goes up on the page's "Sync now".
// - Both sides changed, or there is no common record: BCD asks which copy to keep and changes neither before the answer.
// - An upload is a new snapshot; Epic's older ones stay. A download checks every chunk, hash and target path before it writes a file.
// Log tag [epic-cloud]: counts and short reasons; never a path inside a save folder, a name, a token or an account ID.
@MainActor final class BCDEpicCloud: ObservableObject {
    static let shared = BCDEpicCloud()

    @Published private(set) var states: [String: BCDEpicCloudState] = [:]
    private var busy = Set<String>()
    private var records: [String: BCDEpicCloudRecord] = BCDEpicCloudStore.load(BCDEpicCloud.storeURL)
    private var epic: BCDEpic { BCDEpic.shared }
    /// Counts the changes of Epic's account (a sign-out, a sign-in): a sync that began under another count stops before it writes.
    private(set) var generation = 0

    private init() {}

    /// Epic's account changed (BCDEpic.clearAccount, a finished sign-in): what is shown is dropped and a running sync stops before it writes.
    func accountChanged() {
        generation += 1
        states = [:]
    }

    static var storeURL: URL { BCDEpic.folder.appendingPathComponent("cloud-sync.json") }
    /// Copies of the saves a download replaced, where the Files app shows them (BCD > Epic Cloud Backups).
    static var backupsRoot: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Epic Cloud Backups", isDirectory: true)
    }

    // MARK: The switches

    /// This game's saves sync: its own config line, else bcd.cfg's; off when none says.
    static func isOn(_ entry: LibraryEntry) -> Bool { BCDEpic.flag("BCD_EPIC_CLOUD", entry: entry, fallback: false) }
    static func autoUpload(_ entry: LibraryEntry) -> Bool { BCDEpic.flag("BCD_EPIC_CLOUD_AUTO_UPLOAD", entry: entry, fallback: false) }

    func state(_ app: String) -> BCDEpicCloudState { states[app] ?? BCDEpicCloudState() }
    /// A sync of this game is running (Play waits for it).
    func isSyncing(_ app: String) -> Bool { busy.contains(app) }
    var isBusy: Bool { !busy.isEmpty }

    private func update(_ app: String, _ change: (inout BCDEpicCloudState) -> Void) {
        var next = states[app] ?? BCDEpicCloudState()
        change(&next)
        if states[app] != next { states[app] = next }
    }

    private func log(_ text: String) { BCDEpicLog.write("[epic-cloud] " + text) }

    private func persist() { BCDEpicCloudStore.save(records, to: Self.storeURL) }

    private var sessionRunning: Bool { LibraryModel.shared.current != nil || wine_process_is_running() != 0 || wineserver_is_running() != 0 }

    // MARK: What a run needs

    private struct Context {
        var record: BCDEpicInstallRecord
        var entry: LibraryEntry
        var config: EpicSaveConfig
        var folder: URL
        var account: String
        var transport: BCDEpicCloudHTTP
        var generation: Int
    }

    private struct Looked {
        var context: Context
        var device: [EpicDeviceSave]
        var latest: (snapshot: EpicCloudSnapshot, all: [EpicCloudObject])?
        var verdict: EpicSaveVerdict
    }

    private func entry(of record: BCDEpicInstallRecord) -> LibraryEntry? {
        record.entryID.flatMap { id in LibraryModel.shared.entries.first { $0.id == id } }
    }

    /// The game's catalog entry says where its saves are (customAttributes.CloudSaveFolder, CloudIncludeList, CloudExcludeList).
    private func fetchConfig(_ record: BCDEpicInstallRecord) async throws -> EpicSaveConfig? {
        guard BCDEpicCatalog.validNamespace(record.namespace), BCDEpicCatalog.validCatalogID(record.catalogItemId) else { return nil }
        let reply = try await epic.api.get(BCDEpicCatalog.bulkURL(namespace: record.namespace, ids: [record.catalogItemId]))
        guard let object = try? JSONSerialization.jsonObject(with: reply.data) as? [String: Any] else { throw BCDEpicError.badReply("catalog") }
        return BCDEpicCloudRules.saveConfig(fromBulk: object, catalogItemId: record.catalogItemId)
    }

    /// nil: the game is not one of BCD's, or its switch is off, or it has no cloud saves.
    private func context(_ app: String) async throws -> Context? {
        guard let record = epic.installs[app], let entry = entry(of: record), Self.isOn(entry) else { return nil }
        guard BCDContentQueue.shared.job(epic.key(app)) == nil else {
            throw EpicContentError.invalid("Finish or cancel this game's download before you sync saves.")
        }
        guard let tokens = await epic.session.current(), let account = tokens.accountID else {
            throw EpicContentError.invalid("Sign in to Epic Games to sync saves.")
        }
        let hash = BCDEpicCatalog.accountKey(account)
        var stored = records[app] ?? BCDEpicCloudRecord()
        if stored.account != hash { stored = BCDEpicCloudRecord(account: hash) }
        if stored.config == nil && !stored.noCloud {
            if let config = try await fetchConfig(record) { stored.config = config } else { stored.noCloud = true }
            records[app] = stored
            persist()
        }
        guard let config = stored.config else { return nil }
        let drive = LibraryModel.drive
        guard let user = SteamCloudPaths.userFolder(drive: drive)?.url else {
            throw EpicContentError.invalid("The Wine prefix has no user folder yet. Start a game once, then sync.")
        }
        let paths = EpicSavePaths(drive: drive, userFolder: user, installFolder: record.hostFolder ?? drive, accountID: account)
        guard let folder = paths.resolve(config.folder) else {
            throw EpicContentError.invalid("This game keeps its saves in a place BCD does not know yet.")
        }
        return Context(record: record, entry: entry, config: config, folder: folder, account: account,
                       transport: BCDEpicCloudHTTP(api: epic.api, accountID: account), generation: generation)
    }

    nonisolated private static func scan(_ folder: URL, _ config: EpicSaveConfig) async throws -> [EpicDeviceSave] {
        try await Task.detached(priority: .utility) { try EpicSaveScan.scan(folder, config: config) }.value
    }

    private func look(_ app: String) async throws -> Looked? {
        guard let context = try await context(app) else { return nil }
        let device = try await Self.scan(context.folder, context.config)
        let latest = try await EpicCloudSaves.latest(app: app, transport: context.transport)
        guard await epic.session.current()?.accountID == context.account, epic.signedIn, !sessionRunning, context.generation == generation else {
            throw EpicContentError.invalid("The account or game session changed. Try sync again when no game is running.")
        }
        let deviceMap = Dictionary(device.map { ($0.path, $0.sha) }, uniquingKeysWith: { first, _ in first })
        let verdict = EpicSavePlanner.decide(device: deviceMap, cloud: latest?.snapshot.files, baseline: records[app]?.baseline)
        return Looked(context: context, device: device, latest: latest, verdict: verdict)
    }

    private func show(_ app: String, _ looked: Looked) {
        update(app) { state in
            state.phase = .ready
            state.verdict = looked.verdict
            state.deviceFiles = looked.device.count
            state.deviceBytes = looked.device.reduce(0) { $0 + $1.size }
            state.deviceDate = looked.device.map(\.modified).max()
            state.cloudFiles = looked.latest?.snapshot.files.count ?? 0
            state.cloudBytes = looked.latest?.snapshot.sizes.values.reduce(0, +) ?? 0
            state.cloudDate = looked.latest?.snapshot.date
            state.differences = looked.verdict == .conflict ? BCDEpicCloudState.differences(device: looked.device, cloud: looked.latest?.snapshot) : []
        }
    }

    // MARK: Actions

    /// Compares the device with the cloud. `automatic` (opening the page, starting BCD) also downloads a newer cloud save and, only if
    /// the game's switch asks for it, uploads a newer device save.
    func check(_ app: String, automatic: Bool = true) async {
        // Off for this game (the default): nothing is read, nothing is sent, no state is made.
        guard let installed = epic.installs[app], let entry = entry(of: installed), Self.isOn(entry) else { return }
        guard epic.signedIn, !sessionRunning, !busy.contains(app) else { return }
        busy.insert(app); defer { busy.remove(app) }
        update(app) { $0.phase = .checking; $0.note = nil }
        do {
            guard let looked = try await look(app) else {
                update(app) { $0.phase = records[app]?.noCloud == true ? .unsupported : .idle }
                return
            }
            show(app, looked)
            log("check app=\(BCDEpicLog.app(app)) verdict=\(looked.verdict) device=\(looked.device.count) cloud=\(looked.latest?.snapshot.files.count ?? 0)")
            if looked.verdict == .inSync || looked.verdict == .empty { remember(app, looked) }
            guard automatic else { return }
            switch looked.verdict {
            case .download: await download(app, looked)
            case .upload where Self.autoUpload(looked.context.entry): await upload(app, looked)
            default: break
            }
        } catch {
            fail(app, error)
        }
    }

    /// The Sync now button: does what the comparison says. A conflict waits for the player's choice.
    func syncNow(_ app: String) async {
        guard epic.signedIn, !sessionRunning, !busy.contains(app) else { return }
        busy.insert(app); defer { busy.remove(app) }
        update(app) { $0.phase = .checking; $0.note = nil }
        do {
            guard let looked = try await look(app) else { update(app) { $0.phase = .unsupported }; return }
            show(app, looked)
            switch looked.verdict {
            case .download: await download(app, looked)
            case .upload: await upload(app, looked)
            case .inSync, .empty:
                remember(app, looked)
                update(app) { $0.note = looked.verdict == .empty ? "No saves yet." : "Already in sync." }
            case .conflict: update(app) { $0.note = "The saves differ. Choose which to keep." }
            }
        } catch {
            fail(app, error)
        }
    }

    /// The player's choice for a conflict: Epic's saves come down, or this device's go up as a new snapshot.
    func resolve(_ app: String, useCloud: Bool) async {
        guard epic.signedIn, !sessionRunning, !busy.contains(app) else { return }
        busy.insert(app); defer { busy.remove(app) }
        update(app) { $0.phase = .checking; $0.note = nil }
        do {
            guard let looked = try await look(app) else { update(app) { $0.phase = .unsupported }; return }
            show(app, looked)
            if useCloud {
                guard looked.latest != nil else { update(app) { $0.note = "Epic has no saves for this game." }; return }
                await download(app, looked)
            } else {
                await upload(app, looked)
            }
        } catch {
            fail(app, error)
        }
    }

    /// When BCD starts (and after a sign-in): every installed game whose switch is on is compared with the cloud, one after another.
    func startupCheck() {
        guard epic.signedIn else { return }
        Task {
            for app in epic.installs.keys.sorted() {
                guard let record = epic.installs[app], let entry = entry(of: record), Self.isOn(entry) else { continue }
                await check(app, automatic: true)
            }
        }
    }

    private func remember(_ app: String, _ looked: Looked) {
        var record = records[app] ?? BCDEpicCloudRecord()
        record.baseline = EpicSaveBaseline(device: Dictionary(looked.device.map { ($0.path, $0.sha) }, uniquingKeysWith: { first, _ in first }),
                                           cloud: looked.latest?.snapshot.files ?? [:], snapshot: looked.latest?.snapshot.buildVersion)
        records[app] = record
        persist()
    }

    private func validate(_ context: Context) async throws {
        guard epic.signedIn, await epic.session.current()?.accountID == context.account, !sessionRunning, context.generation == generation else {
            throw EpicContentError.invalid("The account or game session changed. Try sync again when no game is running.")
        }
    }

    private func download(_ app: String, _ looked: Looked) async {
        guard let latest = looked.latest else { return }
        update(app) { $0.phase = .downloading }
        do {
            try await validate(looked.context)
            let backup = BCDEpicCloudRules.backupFolder(title: looked.context.record.title, app: app, root: Self.backupsRoot)
            let result = try await EpicCloudSaves.download(latest.snapshot, all: latest.all, into: looked.context.folder, backup: backup,
                                                           transport: looked.context.transport, validate: { [self] in
                guard epic.signedIn, !sessionRunning, looked.context.generation == generation else {
                    throw EpicContentError.invalid("The account or game session changed. Try sync again when no game is running.")
                }
            })
            let after = try await Self.scan(looked.context.folder, looked.context.config)
            var record = records[app] ?? BCDEpicCloudRecord()
            record.baseline = EpicSaveBaseline(device: Dictionary(after.map { ($0.path, $0.sha) }, uniquingKeysWith: { first, _ in first }),
                                               cloud: latest.snapshot.files, snapshot: latest.snapshot.buildVersion)
            records[app] = record
            persist()
            log("downloaded app=\(BCDEpicLog.app(app)) files=\(result.files) backed-up=\(result.backedUp)")
            var next = looked
            next.device = after
            next.verdict = .inSync
            show(app, next)
            update(app) {
                $0.received = result.files
                $0.backedUp = result.backedUp
                $0.note = "Received \(result.files) save file\(result.files == 1 ? "" : "s")."
                    + (result.backedUp > 0 ? " \(result.backedUp) replaced on this device \(result.backedUp == 1 ? "was" : "were") backed up." : "")
            }
        } catch {
            fail(app, error)
        }
    }

    private func upload(_ app: String, _ looked: Looked) async {
        update(app) { $0.phase = .uploading }
        do {
            try await validate(looked.context)
            let result = try await EpicCloudSaves.upload(looked.device, app: app, accountID: looked.context.account, config: looked.context.config,
                                                         transport: looked.context.transport, validate: { [self] in
                guard epic.signedIn, !sessionRunning, looked.context.generation == generation else {
                    throw EpicContentError.invalid("The account or game session changed. Try sync again when no game is running.")
                }
            })
            let map = Dictionary(looked.device.map { ($0.path, $0.sha) }, uniquingKeysWith: { first, _ in first })
            var record = records[app] ?? BCDEpicCloudRecord()
            record.baseline = EpicSaveBaseline(device: map, cloud: map, snapshot: result.version)
            records[app] = record
            persist()
            log("uploaded app=\(BCDEpicLog.app(app)) files=\(result.files)")
            var next = looked
            next.verdict = .inSync
            show(app, next)
            update(app) {
                $0.cloudFiles = result.files
                $0.cloudDate = EpicCloudSaves.date(ofBuildVersion: result.version)
                $0.sent = result.files
                $0.note = "Sent \(result.files) save file\(result.files == 1 ? "" : "s") as a new snapshot."
            }
        } catch {
            fail(app, error)
        }
    }

    private func fail(_ app: String, _ error: Error) {
        let message = (error as? LocalizedError)?.errorDescription ?? "BCD couldn't reach Epic's save service. Try again later."
        log("failed app=\(BCDEpicLog.app(app)) reason=\(BCDEpicLog.reason(error))")
        update(app) { $0.phase = .failed(message) }
    }

    /// The game's cloud saves are forgotten here (uninstalled, signed out): the remembered rule and common state go; Epic's cloud stays as it is.
    func forget(_ app: String) {
        records[app] = nil
        states[app] = nil
        persist()
    }
}
