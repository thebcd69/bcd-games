// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Combine
import Foundation

/// The log of the content engine and the stores built on it. Lines carry store names, product
/// IDs, counts, sizes and short reasons: never a token, a code, a secret, an account name, a URL
/// or a path (`redact` cleans any text that came from a server before it could be logged).
enum BCDContentLog {
    /// Where lines go: the app sets LogStore; the host checks collect them.
    static var sink: (String) -> Void = { line in FileHandle.standardError.write(Data((line + "\n").utf8)) }

    static func write(_ line: String) { sink(redact(line)) }

    /// Masks what must never reach a log: OAuth codes and tokens in queries or JSON, bearer
    /// tokens, client secrets, passwords.
    static func redact(_ text: String) -> String {
        var out = text
        let rules = [
            #"(?i)((?:access_token|refresh_token|id_token|token|code|client_secret|password|secret)["']?\s*[=:]\s*["']?)[^&\s"',}]+"#,
            #"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"#,
        ]
        for rule in rules {
            out = out.replacingOccurrences(of: rule, with: "$1<redacted>", options: .regularExpression)
        }
        return out
    }
}

/// What a store adds to the queue: how to plan a job, where its chunks come from, and what to do
/// with the finished files. One driver per store; the queue runs one job at a time across stores.
@MainActor protocol BCDContentDriver: AnyObject {
    /// The store's name in keys ("gog") and for people ("GOG").
    var store: String { get }
    var storeTitle: String { get }
    /// Why a job cannot run now (signed out, ...), or nil.
    func blocker(_ key: BCDContentKey) -> String?
    /// The plan for a job: a new one, or `stored` when it still describes what the store serves
    /// (then the journal and staged files are kept).
    func plan(for key: BCDContentKey, stored: BCDContentPlan?) async throws -> BCDContentPlan
    func source(for plan: BCDContentPlan) async throws -> BCDChunkSource
    func root(for key: BCDContentKey) -> BCDInstallRoot
    func options(for plan: BCDContentPlan) -> BCDContentOptions
    /// Every chunk is in `stage` and checked: move the files into place and record the install.
    func finish(_ plan: BCDContentPlan, stage: URL) async throws
    /// A job was cancelled; its staged files and journal are already gone.
    func cancelled(_ key: BCDContentKey)
}

/// Where the queue reports to the system while BCD is in the background (BCDContentBackground on
/// the device; nothing in the host checks).
@MainActor protocol BCDContentBackgroundHost: AnyObject {
    func jobStarted(title: String, store: String)
    func progress(_ progress: BCDContentProgress)
    func jobEnded(title: String, store: String, outcome: BCDContentQueue.Outcome, queueEmpty: Bool)
}

/// The stores' downloads: a persistent list of jobs keyed by (store, ID), one running at a time.
///
/// - A Wine session pauses the running job and the queue (memory and I/O belong to the game);
///   they continue when it ends, as Steam's downloads do.
/// - Another downloader at work (Steam's) is waited for, and a job running when one starts steps
///   back to the head of the queue: one download at a time on the device.
/// - Pausing keeps the journal and the staged files: a resume fetches only what is missing.
///   Cancelling removes both.
/// - The list survives the app: after a relaunch, jobs that were running or waiting show as paused.
@MainActor final class BCDContentQueue: ObservableObject {
    static let shared = BCDContentQueue(folder: BCDContentQueue.defaultFolder)

    static var defaultFolder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("BCD/Content", isDirectory: true)
    }

    enum Outcome: Equatable { case completed, failed(String), paused }

    struct Job: Codable, Equatable, Identifiable {
        enum State: Codable, Equatable { case queued, active, paused, failed(String) }
        var key: BCDContentKey
        var title: String
        var state: State
        var progress = BCDContentProgress()
        var id: String { key.description }
    }

    /// Why a job stopped without the user asking: it continues when the reason ends.
    private enum Hold { case session, background }

    @Published private(set) var jobs: [BCDContentKey: Job] = [:]
    private var order: [BCDContentKey] = []
    private var active: (key: BCDContentKey, task: Task<Void, Never>)?
    private var held: [BCDContentKey: Hold] = [:]
    private var stepBack = Set<BCDContentKey>()
    private var cancelling = Set<BCDContentKey>()
    private var drivers: [String: BCDContentDriver] = [:]
    private var inSession = false
    private var timer: Timer?
    let folder: URL

    weak var background: BCDContentBackgroundHost?
    /// A Wine session runs (set by the app).
    var sessionRunning: () -> Bool = { false }
    /// Another downloader is busy (Steam's, set by the app).
    var otherDownloadActive: () -> Bool = { false }

    init(folder: URL) {
        self.folder = folder
        restore()
    }

    func register(_ driver: BCDContentDriver) { drivers[driver.store] = driver }
    func job(_ key: BCDContentKey) -> Job? { jobs[key] }
    var activeKey: BCDContentKey? { active?.key }
    /// A job is running or waiting to run.
    var hasWork: Bool { active != nil || !order.isEmpty }

    func jobFolder(_ key: BCDContentKey) -> URL {
        folder.appendingPathComponent("jobs/" + key.folderName, isDirectory: true)
    }

    func stage(_ key: BCDContentKey, root: BCDInstallRoot) -> URL? {
        root.stagingURL?.appendingPathComponent(key.folderName, isDirectory: true)
    }

    /// Whether a paused or failed job has work saved (a resume continues it).
    func hasPartial(_ key: BCDContentKey) -> Bool {
        FileManager.default.fileExists(atPath: jobFolder(key).appendingPathComponent("plan.json").path)
    }

    // MARK: Asked for by the interface

    /// Installs, or resumes: the job waits for its turn.
    func enqueue(_ key: BCDContentKey, title: String) {
        guard drivers[key.store] != nil, !cancelling.contains(key) else { return }
        if active?.key == key || order.contains(key) { return }
        var job = jobs[key] ?? Job(key: key, title: title, state: .queued)
        job.title = title
        if inSession {
            held[key] = .session
            job.state = .paused
            jobs[key] = job
            save()
            return
        }
        held[key] = nil
        job.state = .queued
        jobs[key] = job
        order.append(key)
        save()
        startTimer()
        pump()
    }

    func pause(_ key: BCDContentKey) {
        held[key] = nil
        stepBack.remove(key)
        if active?.key == key {
            active?.task.cancel()
        } else if let i = order.firstIndex(of: key) {
            order.remove(at: i)
            jobs[key]?.state = .paused
            save()
        }
    }

    /// Stops a job and removes its staged files and journal. Installed games are not touched.
    func cancel(_ key: BCDContentKey) {
        let running = active?.key == key ? active?.task : nil
        held[key] = nil
        stepBack.remove(key)
        order.removeAll { $0 == key }
        jobs[key] = nil
        save()
        let driver = drivers[key.store]
        let stage = driver.flatMap { self.stage(key, root: $0.root(for: key)) }
        let jobFolder = self.jobFolder(key)
        cancelling.insert(key)
        running?.cancel()
        Task { @MainActor in
            await running?.value
            let trash = stage?.deletingLastPathComponent().appendingPathComponent("trash", isDirectory: true)
            if let stage, let trash { await BCDContentEngine.remove(stage, trash: trash) }
            try? FileManager.default.removeItem(at: jobFolder)
            self.cancelling.remove(key)
            driver?.cancelled(key)
            BCDContentLog.write("[content] cancelled \(key)")
        }
    }

    // MARK: Told by the app

    /// A Wine session started or ended. Idempotent.
    func sessionChanged(active running: Bool) {
        guard running != inSession else { return }
        inSession = running
        if running {
            if let current = active { held[current.key] = .session; current.task.cancel() }
            for key in order { held[key] = .session; jobs[key]?.state = .paused }
            order.removeAll()
            save()
            if !held.isEmpty { BCDContentLog.write("[content] paused for a game session count=\(held.count)") }
        } else {
            resume(.session)
        }
    }

    /// iOS ended BCD's background time: everything pauses, to continue when BCD is active again.
    func pauseForBackground() {
        if let current = active { held[current.key] = .background; current.task.cancel() }
        for key in order { held[key] = .background; jobs[key]?.state = .paused }
        order.removeAll()
        save()
    }

    func resumeAfterBackground() { resume(.background) }

    /// Another downloader's state changed: step back while it works, continue when it is done.
    func otherDownloadChanged() {
        if otherDownloadActive(), let current = active {
            stepBack.insert(current.key)
            current.task.cancel()
            BCDContentLog.write("[content] waiting for another download job=\(current.key)")
        } else {
            pump()
        }
    }

    /// Compares the app's state with what the queue assumed: every few seconds while there is work.
    func reconcile() {
        sessionChanged(active: sessionRunning())
        otherDownloadChanged()
    }

    private func resume(_ reason: Hold) {
        let keys = held.filter { $0.value == reason }.map(\.key).sorted { $0.description < $1.description }
        for key in keys {
            held[key] = nil
            if let title = jobs[key]?.title { enqueue(key, title: title) }
        }
        if !keys.isEmpty { BCDContentLog.write("[content] resumed count=\(keys.count)") }
    }

    private func startTimer() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.reconcile()
                if !self.hasWork { self.timer?.invalidate(); self.timer = nil }
            }
        }
    }

    // MARK: Running

    private func pump() {
        guard active == nil, !inSession, !otherDownloadActive(), let key = order.first else { return }
        order.removeFirst()
        guard let driver = drivers[key.store] else { return }
        if let blocker = driver.blocker(key) {
            jobs[key]?.state = .failed(blocker)
            save()
            pump()
            return
        }
        jobs[key]?.state = .active
        save()
        let title = jobs[key]?.title ?? key.description
        background?.jobStarted(title: title, store: driver.storeTitle)
        BCDContentLog.write("[content] started \(key)")
        active = (key, Task { [weak self] in await self?.run(key, driver: driver) })
    }

    private func run(_ key: BCDContentKey, driver: BCDContentDriver) async {
        var outcome = Outcome.paused
        let jobFolder = self.jobFolder(key)
        do {
            let stored = loadPlan(key)
            let plan = try await driver.plan(for: key, stored: stored)
            try Task.checkCancellation()
            let root = driver.root(for: key)
            guard let stage = stage(key, root: root) else { throw BCDContentError.driveUnavailable(root.drive) }
            if stored?.identity != plan.identity {
                // A new plan: nothing staged or journaled before belongs to it.
                try? FileManager.default.removeItem(at: stage)
                try? FileManager.default.removeItem(at: jobFolder)
                try savePlan(plan, key)
            }
            let source = try await driver.source(for: plan)
            BCDContentLog.write("[content] plan \(key) files=\(plan.files.count) chunks=\(plan.chunks.count) " +
                                "download=\(plan.downloadBytes) install=\(plan.totalBytes) resumed=\(stored?.identity == plan.identity ? 1 : 0)")
            try await BCDContentEngine.download(plan, stage: stage, jobFolder: jobFolder, source: source,
                                                options: driver.options(for: plan)) { progress in
                Task { @MainActor [weak self] in self?.update(key, progress) }
            }
            try Task.checkCancellation()
            update(key, BCDContentProgress(phase: .finishing, doneBytes: plan.downloadBytes, totalBytes: plan.downloadBytes))
            try await driver.finish(plan, stage: stage)
            try? FileManager.default.removeItem(at: jobFolder)
            jobs[key] = nil
            outcome = .completed
            BCDContentLog.write("[content] finished \(key)")
        } catch {
            if error is CancellationError || Task.isCancelled {
                if stepBack.remove(key) != nil, jobs[key] != nil {
                    jobs[key]?.state = .queued
                    order.insert(key, at: 0)
                } else {
                    jobs[key]?.state = .paused
                }
                BCDContentLog.write("[content] paused \(key)")
            } else {
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                jobs[key]?.state = .failed(message)
                outcome = .failed(message)
                BCDContentLog.write("[content] failed \(key) reason=\(Self.reason(error))")
            }
        }
        active = nil
        save()
        background?.jobEnded(title: jobs[key]?.title ?? driver.storeTitle, store: driver.storeTitle, outcome: outcome,
                             queueEmpty: order.isEmpty)
        pump()
    }

    /// A driver reports how far its own planning has come (a repair reading the files the game has): the job's progress, in the
    /// verifying phase, until the download begins.
    func planning(_ key: BCDContentKey, done: Int64, of total: Int64) {
        update(key, BCDContentProgress(phase: .verifying, doneBytes: done, totalBytes: max(total, done)))
    }

    private func update(_ key: BCDContentKey, _ progress: BCDContentProgress) {
        guard active?.key == key, jobs[key]?.state == .active, jobs[key]?.progress != progress else { return }
        jobs[key]?.progress = progress
        background?.progress(progress)
    }

    static func reason(_ error: Error) -> String {
        if let error = error as? BCDLogReasonError { return error.logReason }   // BCDContentError, BCDGOGError, ...
        if let error = error as? URLError { return "url-\(error.code.rawValue)" }
        return String(describing: type(of: error))
    }

    // MARK: Files

    private var listURL: URL { folder.appendingPathComponent("jobs.json") }

    private func save() {
        let list = jobs.values.sorted { $0.key.description < $1.key.description }
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            try JSONEncoder().encode(list).write(to: listURL, options: .atomic)
        } catch {
            BCDContentLog.write("[content] could not save the list reason=\(Self.reason(error))")
        }
    }

    private func restore() {
        guard let data = try? Data(contentsOf: listURL), let list = try? JSONDecoder().decode([Job].self, from: data) else { return }
        for var job in list {
            if job.state == .active || job.state == .queued { job.state = .paused }
            jobs[job.key] = job
        }
    }

    func loadPlan(_ key: BCDContentKey) -> BCDContentPlan? {
        let url = jobFolder(key).appendingPathComponent("plan.json")
        guard let data = try? Data(contentsOf: url), let plan = try? JSONDecoder().decode(BCDContentPlan.self, from: data),
              plan.key == key, (try? plan.validate()) != nil else { return nil }
        return plan
    }

    private func savePlan(_ plan: BCDContentPlan, _ key: BCDContentKey) throws {
        let folder = jobFolder(key)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        try JSONEncoder().encode(plan).write(to: folder.appendingPathComponent("plan.json"), options: .atomic)
    }
}
