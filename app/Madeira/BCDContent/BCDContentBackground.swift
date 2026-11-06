// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import BackgroundTasks
import UIKit
import UserNotifications

/// The stores' downloads keep going when BCD leaves the foreground, as Steam's do (owner,
/// 2026-10-18; docs/YOL-HARITASI-OZELLIKLER.md §C).
///
/// - iOS 26 and later: a continued-processing task, asked for when a job starts, keeps BCD running
///   for the whole queue; iOS shows its title ("Downloading <game>", "From GOG, in BCD"), the
///   percentage and a stop control. Its identifier is "<bundle id>.download.stores", permitted by
///   Info.plist's "$(PRODUCT_BUNDLE_IDENTIFIER).download.*" entry; Steam keeps its own
///   ".download.queue" (SteamDownloadBackground), and the queue never runs beside a Steam download,
///   so the two never run at once.
/// - Earlier iOS, or when iOS refuses: the short background grace period (or, with Settings > Memory & performance >
///   In the background > Downloads on, BCD keeps itself running for that long: BackgroundRun.swift), then a clean pause
///   (the journal keeps every finished chunk) until BCD is active again.
/// - Notifications when a download finishes, fails or pauses while BCD is in the background.
/// - The same two switches as Steam's downloads (read from SteamDownloadBackground, so a line in
///   bcd.cfg governs both): `env.MADEIRA_BACKGROUND_DOWNLOADS = 0`, no continued processing;
///   `env.MADEIRA_DOWNLOAD_NOTIFICATIONS = 0`, no notifications.
/// Log tag: [bg-content] (counts and states only).
@MainActor final class BCDContentBackground: BCDContentBackgroundHost {
    static let shared = BCDContentBackground()

    private weak var queue: BCDContentQueue?
    private var grace: BackgroundRun.Token?   // BackgroundRun.swift (madeira-bcd: it was an iOS background task of its own)
    private var task: AnyObject?          // BGContinuedProcessingTask (iOS 26+)
    private var waitingForTask = false
    private var registeredIdentifier: String?
    private var askedPermission = false
    private var title = ""
    private var subtitle = ""
    private var lines = 0

    private init() {}

    private func log(_ line: String) {
        guard lines < 40 else { return }
        lines += 1
        BCDContentLog.write("[bg-content] " + line)
    }

    private var inBackground: Bool { UIApplication.shared.applicationState != .active }
    private var queueBusy: Bool { queue?.hasWork == true }

    /// "<bundle id>.download.stores", from the permitted "<bundle id>.download.*".
    private var identifier: String? {
        let permitted = Bundle.main.object(forInfoDictionaryKey: "BGTaskSchedulerPermittedIdentifiers") as? [String] ?? []
        return permitted.first { $0.hasSuffix(".download.*") }.map { String($0.dropLast()) + "stores" }
    }

    func attach(_ queue: BCDContentQueue) {
        guard self.queue == nil else { return }
        self.queue = queue
        queue.background = self
        let center = NotificationCenter.default
        center.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.beginGrace() }
        }
        center.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.endGrace()
                self?.queue?.resumeAfterBackground()
                self?.queue?.reconcile()
            }
        }
    }

    // MARK: BCDContentBackgroundHost

    func jobStarted(title game: String, store: String) {
        title = "Downloading \(game)"
        subtitle = "From \(store), in BCD"
        askPermission()
        if #available(iOS 26.0, *) {
            if SteamDownloadBackground.continuedEnabled { submit() }
            (task as? BGContinuedProcessingTask)?.updateTitle(title, subtitle: subtitle)
        }
        if inBackground { beginGrace() }
    }

    func progress(_ progress: BCDContentProgress) {
        guard #available(iOS 26.0, *), let task = task as? BGContinuedProcessingTask else { return }
        let total = max(progress.totalBytes, 1)
        if task.progress.totalUnitCount != total { task.progress.totalUnitCount = total }
        task.progress.completedUnitCount = min(progress.doneBytes, total)
    }

    func jobEnded(title game: String, store: String, outcome: BCDContentQueue.Outcome, queueEmpty: Bool) {
        if inBackground {
            switch outcome {
            case .completed: notify("\(game) is ready", "Finished downloading from \(store). Open BCD to play.")
            case .failed(let reason): notify("Couldn't download \(game)", reason)
            case .paused: break
            }
        }
        guard queueEmpty else { return }
        complete(success: outcome == .completed)
        endGrace()
    }

    // MARK: Continued processing (iOS 26+)

    @available(iOS 26.0, *)
    private func submit() {
        guard task == nil, !waitingForTask, let identifier else { return }
        if registeredIdentifier != identifier {
            let registered = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: .main) { [weak self] given in
                guard let given = given as? BGContinuedProcessingTask else { given.setTaskCompleted(success: false); return }
                MainActor.assumeIsolated { self?.began(given) }
            }
            guard registered else { log("register refused id-suffix=stores"); return }
            registeredIdentifier = identifier
        }
        let request = BGContinuedProcessingTaskRequest(identifier: identifier, title: title, subtitle: subtitle)
        request.strategy = .fail
        do {
            try BGTaskScheduler.shared.submit(request)
            waitingForTask = true
            log("continued-processing submitted")
        } catch {
            log("continued-processing refused")
        }
    }

    @available(iOS 26.0, *)
    private func began(_ given: BGContinuedProcessingTask) {
        waitingForTask = false
        task = given
        given.progress.totalUnitCount = 1
        given.expirationHandler = { [weak self] in
            DispatchQueue.main.async {
                guard let self else { return }
                self.log("continued-processing expired; pausing")
                self.queue?.pauseForBackground()
                self.notify("Download paused", "Open BCD again to resume it.")
                self.complete(success: false)
            }
        }
        log("continued-processing running")
        if !queueBusy { complete(success: true) }
    }

    private func complete(success: Bool) {
        if #available(iOS 26.0, *), let running = task as? BGContinuedProcessingTask {
            running.setTaskCompleted(success: success)
            log("continued-processing completed success=\(success ? 1 : 0)")
        }
        task = nil
    }

    // MARK: Grace period (every iOS)

    private func beginGrace() {
        guard grace == nil, task == nil, queueBusy else { return }
        grace = BackgroundRun.shared.beginTask("store download") { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                if self.task == nil {
                    self.log("background time over; pausing")
                    self.queue?.pauseForBackground()
                    self.notify("Download paused", "Open BCD again to resume it.")
                }
                self.endGrace()
            }
        }
        log("grace begun continued=\(task == nil ? 0 : 1)")
    }

    private func endGrace() {
        guard let held = grace else { return }
        grace = nil
        BackgroundRun.shared.endTask(held)
    }

    // MARK: Notifications

    private func askPermission() {
        guard !askedPermission, SteamDownloadBackground.notificationsEnabled else { return }
        askedPermission = true
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, _ in
            DispatchQueue.main.async { BCDContentLog.write("[bg-content] notifications granted=\(granted ? 1 : 0)") }
        }
    }

    private func notify(_ heading: String, _ body: String) {
        guard SteamDownloadBackground.notificationsEnabled else { return }
        let content = UNMutableNotificationContent()
        content.title = heading
        content.body = body
        content.sound = .default
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "bcd.content.\(UUID().uuidString)",
                                                                     content: content, trigger: nil))
    }
}
