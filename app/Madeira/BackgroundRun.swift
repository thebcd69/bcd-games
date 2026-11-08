// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright 2026 ft7t8drgwf-wq
// Madeira Converter Exception: see LICENSE-EXCEPTION.md

import AVFoundation
import UIKit
import UserNotifications

// madeira-bcd: from Madeira #498 (7a158d4 and 1dbd877), made a feature that is OFF until the player turns it on
// (Settings > Memory & performance > In the background; the keys are `background-run` and `background-download`,
// both 0 = off when absent). The changes carry "madeira-bcd".
//
// BCD keeps running when you switch apps or lock the screen.
//
// iOS suspends an app a few seconds after it leaves the screen, unless the app
// plays audio and declares the "audio" background mode (Info.plist
// UIBackgroundModes). That suspension stopped a game in the middle of loading
// (Project Zomboid on build 48: 44 s and 77 s with nothing running while
// the app was in the app switcher) and cut downloads off ("The network
// connection was lost").
//
// So while a game session runs, or a store download holds a task here,
// BCD's one RemoteIO endpoint (build/ntdll-unix/audio_null_ios.c) keeps
// running when BCD leaves the screen: silent, and the game's own sound is
// muted. CPU work (loading, shader compiles, downloads) carries on. GPU work
// does not: iOS refuses it in the background, so a thread that commits a Metal
// command buffer waits until BCD is back (Winios/MetalBackgroundGate.m,
// bcd.cfg background-gpu-gate).
//
// iOS ends an app that keeps running this way once it averages more than about
// 80% of one core for a minute, and a loading game uses several. So while
// BCD keeps itself running, its CPU use is held under `background-cpu`
// percent of one core (default 50; Winios/BackgroundCPUCap.c): the game loads
// slower in the background instead of being ended.
//
// It stops on its own, and then iOS suspends BCD as before:
//   `background-run` minutes after BCD left the screen while a game runs
//   (madeira-bcd: default 0 = never keep a game running), and
//   `background-download` minutes for a download (madeira-bcd: default 0 = never).
// Both are bcd.cfg keys; a game's own config can set background-run,
// background-cpu and background-sound. With notifications allowed (the download
// notifications ask), a notification says the game is paused. `background-sound = 1`
// lets the game's sound play while you are in another app. iOS can still end BCD in
// the background when another app needs the memory; a call or Siri interrupts it too.
//
// madeira-bcd, the feature off: BCD now declares the "audio" background mode, and a game that
// is playing sound would keep BCD (and the game) running in the background for as long as
// its audio endpoint runs. BCD never did that, so with nothing to keep running the endpoint
// is halted the moment BCD enters the background (a halted endpoint: iOS suspends the app
// as it always did) and released again when BCD is active. Nothing else of this file runs
// then: no category change, no CPU cap, no GPU gate, no interruption handling.
//
// Downloads use `beginTask`/`endTask` in place of UIApplication's
// beginBackgroundTask/endBackgroundTask: the expiration handler then runs when
// BCD stops keeping itself running (or, when it cannot, when iOS's short
// grace period ends, exactly as before).
//
// Log tag: [bg-run].
@MainActor final class BackgroundRun {
    static let shared = BackgroundRun()

    /// Minutes a running game keeps going after BCD leaves the screen. 0 (the default): iOS pauses it within seconds, as before.
    static var gameMinutes: Double { minutes(MadeiraConfig.gameValue("background-run") ?? MadeiraConfig.get("background-run"), default: 0) }
    /// Minutes a download keeps going after BCD leaves the screen. 0 (the default): iOS's short grace period, then the download pauses.
    static var downloadMinutes: Double { minutes(MadeiraConfig.get("background-download"), default: 0) }
    /// Percent of one core BCD may use on average while it keeps running in the background. 0: no cap (iOS may end BCD).
    static var cpuPercent: Int32 {
        guard let raw = MadeiraConfig.gameValue("background-cpu") ?? MadeiraConfig.get("background-cpu"), let v = Int32(raw) else { return 50 }
        return max(0, v)
    }
    /// 1: the game's sound keeps playing while BCD is in the background.
    static var gameSound: Bool { (MadeiraConfig.gameValue("background-sound") ?? MadeiraConfig.get("background-sound")) == "1" }

    private static func minutes(_ raw: String?, default dflt: Double) -> Double {
        guard let raw, let v = Double(raw) else { return dflt }
        return max(0, v)
    }

    /// A download (or other work) that wants to keep going in the background.
    struct Token: Hashable { fileprivate let id: Int }

    private struct Hold {
        let name: String
        let expiration: () -> Void
        var system: UIBackgroundTaskIdentifier
        var since: Date?            // when it began counting in the background
    }

    private var holds: [Int: Hold] = [:]
    private var nextID = 1
    private var started = false
    private var inBackground = false
    private var keeping = false          // our endpoint runs to keep BCD going
    private var gameSince: Date?         // a game was running when BCD left the screen
    private var gameNotified = false
    private var tick: Timer?
    private var activatedSession = false // we made the audio session active (no game session)
    private var changedCategory = false  // we made it mix with other apps' audio
    private var touchedAudio = false     // madeira-bcd: we told the audio driver to start, mute or halt: undone when BCD is active
    private var cpuCapOn = false         // madeira-bcd: the governor was asked to hold the CPU
    private var gateInstalled = false    // madeira-bcd: the Metal gate exists (made the first time a game or a download needs it)
    private var gateTried = false
    private var ticks = 0

    private func log(_ line: String) { LogStore.shared.log("[bg-run] " + line) }

    private var gameRunning: Bool { wine_process_is_running() != 0 }

    /// Called once at app start.
    func start() {
        guard !started else { return }
        started = true
        // madeira-bcd: upstream installs the Metal gate here, at every start. It wraps every command buffer commit of the
        // process, so BCD makes it only when something is really kept running (gateAllows below): a player who never
        // turns the feature on never has it.
        let nc = NotificationCenter.default
        nc.addObserver(forName: UIApplication.willResignActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.leaving() }
        }
        nc.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.enteredBackground() }
        }
        nc.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.becameActive() }
        }
        nc.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] n in
            let ended = (n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt)
                .flatMap(AVAudioSession.InterruptionType.init) == .ended
            MainActor.assumeIsolated { self?.interruption(ended: ended) }
        }
    }

    // MARK: Downloads

    /// Like UIApplication.beginBackgroundTask: `expiration` runs once, when BCD can no longer keep this going.
    func beginTask(_ name: String, expiration: @escaping () -> Void) -> Token {
        let id = nextID
        nextID += 1
        var hold = Hold(name: name, expiration: expiration, system: .invalid, since: inBackground ? Date() : nil)
        hold.system = UIApplication.shared.beginBackgroundTask(withName: "BCD " + name) { [weak self] in
            MainActor.assumeIsolated { self?.systemExpired(id) }
        }
        holds[id] = hold
        if inBackground { keepGoing() }
        return Token(id: id)
    }

    func endTask(_ token: Token) {
        guard let hold = holds.removeValue(forKey: token.id) else { return }
        if hold.system != .invalid { UIApplication.shared.endBackgroundTask(hold.system) }
        if inBackground { update() }
    }

    /// iOS's own grace period is over. While our endpoint keeps BCD running the
    /// download goes on; otherwise it pauses, as it always did.
    private func systemExpired(_ id: Int) {
        guard var hold = holds[id] else { return }
        if hold.system != .invalid { UIApplication.shared.endBackgroundTask(hold.system) }
        hold.system = .invalid
        if keeping {
            holds[id] = hold
            return
        }
        holds[id] = nil
        hold.expiration()
    }

    // MARK: The Metal gate

    /// madeira-bcd: may a game run on in the background? Its GPU work must not reach iOS from there, so only with the Metal
    /// gate: installed now (the first time, on the main thread, while BCD is still in front), or left out by
    /// `background-gpu-gate = 0`. An install that wrapped no command buffer class is not a gate: no game is kept running.
    private func gateAllows() -> Bool {
        if MadeiraConfig.get("background-gpu-gate") == "0" { return true }
        if !gateTried {
            gateTried = true
            let classes = madeira_metal_gate_install()
            log("Metal background gate: \(classes) command buffer class(es)")
            gateInstalled = classes > 0
        }
        return gateInstalled
    }

    // MARK: Leaving and coming back

    private func leaving() {
        // Also a pull of Control Center or a notification banner. While BCD is still
        // in front, where iOS allows it, the session mixes with other apps' audio and the
        // endpoint is already running (not muted yet), so nothing has to start from the
        // background.
        // madeira-bcd: also for a download held while a game runs (the game would run on beside it)
        guard gameRunning, Self.gameMinutes > 0 || (Self.downloadMinutes > 0 && !holds.isEmpty) else { return }
        guard gateAllows() else { log("the game is not kept running: the Metal gate could not be made"); return }
        // madeira-bcd: a gate made just now missed this notification; hold by hand (and the gate's own observer
        // releases it when BCD is active)
        if gateInstalled { madeira_metal_gate_hold(1) }
        mixWithOthers()
        touchedAudio = true
        _ = madeira_audio_background(1, 0, 0)
    }

    private func enteredBackground() {
        inBackground = true
        gameNotified = false
        gameSince = gameRunning && Self.gameMinutes > 0 ? Date() : nil   // madeira-bcd: with 0 minutes there is no game to keep (nor a notification that says it was paused)
        let now = Date()
        for id in holds.keys { holds[id]?.since = now }
        keepGoing()
    }

    private func becameActive() {
        let wasKeeping = keeping
        inBackground = false
        tick?.invalidate(); tick = nil
        keeping = false
        gameSince = nil
        for id in holds.keys { holds[id]?.since = nil }
        // madeira-bcd: if BCD changed nothing there is nothing to undo (every return with the feature off and nothing running).
        guard wasKeeping || touchedAudio || cpuCapOn || changedCategory || activatedSession else { return }
        if cpuCapOn { madeira_bg_cpu_cap(0); cpuCapOn = false }
        touchedAudio = false
        _ = madeira_audio_background(0, 0, 0)
        restoreSession()
        if wasKeeping { log("BCD is active again" + cpuSummary()) }
        // The category change can stop the endpoint without a word: check it renders.
        // (madeira-bcd: not when there is no game and nothing was kept: then there is no endpoint to look at.)
        guard gameRunning || wasKeeping else { return }
        _ = madeira_audio_check()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
            MainActor.assumeIsolated {
                guard let self, !self.inBackground else { return }
                if madeira_audio_check() != 0 { self.log("game audio had stopped; restarted") }
            }
        }
    }

    private func interruption(ended: Bool) {
        // madeira-bcd: only while BCD keeps itself running. With the feature off, the audio driver's own
        // MADEIRA_AUDIO_RESUME handling stays the only answer to a call, as before.
        guard keeping else { return }
        guard ended else { log("audio interrupted (a call, Siri or another app's audio)"); return }
        do { try AVAudioSession.sharedInstance().setActive(true) } catch {
            log("audio session could not be made active after the interruption: \(error.localizedDescription)")
            return
        }
        madeira_audio_resume_after_interruption()
        if inBackground { log("audio interruption ended; still running in the background") }
    }

    // MARK: Keeping BCD running

    /// What still wants BCD running: names for the log, empty when nothing.
    private func wanted(at now: Date) -> [String] {
        var out: [String] = []
        if let since = gameSince, gameRunning, now.timeIntervalSince(since) < Self.gameMinutes * 60 { out.append("game") }
        let dl = Self.downloadMinutes * 60
        for hold in holds.values {
            if let since = hold.since, now.timeIntervalSince(since) < dl { out.append(hold.name) }
        }
        return out
    }

    private func keepGoing() {
        guard inBackground else { return }
        var reasons = wanted(at: Date())
        // madeira-bcd: whatever keeps BCD running keeps a running game running too (it is the same process), and a
        // game must not commit GPU work from the background: without the Metal gate nothing is kept.
        if !reasons.isEmpty, gameRunning {
            if gateAllows() {
                if gateInstalled { madeira_metal_gate_hold(1) }
            } else {
                log("nothing is kept running: a game runs and the Metal gate could not be made")
                reasons = []
                gameSince = nil
            }
        }
        if reasons.isEmpty { stop(reason: nil); return }
        if !keeping {
            mixWithOthers()
            if !gameRunning, !activatedSession {
                do {
                    try AVAudioSession.sharedInstance().setActive(true)
                    activatedSession = true
                } catch {
                    log("cannot keep running: audio session not active (\(error.localizedDescription))")
                    stop(reason: nil)
                    return
                }
            }
            touchedAudio = true
            let running = madeira_audio_background(1, Self.gameSound ? 0 : 1, 0) != 0
            guard running else {
                log("cannot keep running: the audio endpoint did not start")
                stop(reason: nil)
                return
            }
            keeping = true
            ticks = 0
            let cap = Self.cpuPercent
            if cap > 0 { madeira_bg_cpu_cap(cap); cpuCapOn = true }
            _ = madeira_audio_check()
            log("keeping BCD running in the background for: \(reasons.joined(separator: ", ")) "
                + "(game \(Int(Self.gameMinutes)) min, downloads \(Int(Self.downloadMinutes)) min, "
                + (cap > 0 ? "CPU held under \(cap)% of one core)" : "no CPU cap)"))
        }
        if tick == nil {
            tick = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.update() }
            }
        }
    }

    /// Every 15 s in the background, and when a task ends: stop once nothing wants BCD running.
    private func update() {
        guard inBackground else { return }
        let now = Date()
        if keeping {
            if madeira_audio_check() != 0 { log("the audio endpoint had stopped; restarted") }
            ticks += 1
            if ticks % 4 == 0 { log("still running in the background" + cpuSummary()) }
        }
        // Downloads past their time pause now. madeira-bcd: not with the feature off (0 minutes): then iOS's own grace
        // period decides, as before, whatever else is held.
        let dl = Self.downloadMinutes * 60
        if dl > 0 {
            for (id, hold) in holds {
                if let since = hold.since, now.timeIntervalSince(since) >= dl {
                    holds[id] = nil
                    if hold.system != .invalid { UIApplication.shared.endBackgroundTask(hold.system) }
                    log("\(hold.name): \(Int(Self.downloadMinutes)) min in the background; pausing it")
                    hold.expiration()
                }
            }
        }
        var gameReason: String?
        if let since = gameSince, !gameNotified {
            if !gameRunning {
                gameSince = nil
                log("the game ended while BCD was in the background")
            } else if now.timeIntervalSince(since) >= Self.gameMinutes * 60 {
                gameNotified = true
                gameReason = "after \(Int(Self.gameMinutes)) min in the background"
                notifyGamePaused()
            }
        }
        if wanted(at: now).isEmpty { stop(reason: gameReason) }
    }

    /// Nothing wants BCD running: the endpoint stops so iOS suspends BCD as before.
    /// A task whose iOS grace period is already over pauses now; one still inside it gets
    /// the rest of it, as before.
    private func stop(reason: String?) {
        tick?.invalidate(); tick = nil
        let was = keeping
        keeping = false
        gameSince = nil
        if cpuCapOn { madeira_bg_cpu_cap(0); cpuCapOn = false }
        touchedAudio = true
        _ = madeira_audio_background(0, 1, 1)
        // madeira-bcd: only after a run (was): with the feature off nothing here may pause a download earlier than iOS's grace period does.
        if was {
            for (id, hold) in holds where hold.system == .invalid {
                holds[id] = nil
                hold.expiration()
            }
        }
        if was { log("stopped keeping BCD running" + (reason.map { " (\($0))" } ?? "") + "; iOS pauses it now" + cpuSummary()) }
    }

    /// ": 45% of one core, threads held 70% of the time over 300 s" for the current or last run.
    private func cpuSummary() -> String {
        var cpu = 0.0, wall = 0.0, held = 0.0
        madeira_bg_cpu_stats(&cpu, &wall, &held)
        guard wall > 0 else { return "" }
        return ": \(Int(100 * cpu / wall))% of one core, threads held \(Int(100 * held / wall))% of the time over \(Int(wall)) s"
    }

    // MARK: Audio session

    /// Other apps' audio plays alongside BCD's while it is away, so starting music
    /// in another app does not interrupt (and so end) BCD's background run.
    private func mixWithOthers() {
        guard !changedCategory else { return }
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, options: [.mixWithOthers])
            changedCategory = true
        } catch {
            log("audio session: mix with others refused (\(error.localizedDescription))")
        }
    }

    /// Back as it was: the game session's own category (WineProcessBridge.m), and a
    /// session we only made active for a download made inactive again.
    private func restoreSession() {
        let session = AVAudioSession.sharedInstance()
        if changedCategory {
            try? session.setCategory(.playback, options: [])
            changedCategory = false
        }
        if activatedSession, !gameRunning {
            try? session.setActive(false, options: [.notifyOthersOnDeactivation])
        }
        activatedSession = false
    }

    // MARK: Notification

    private func notifyGamePaused() {
        // madeira-bcd: the switch of the download notifications (env.MADEIRA_DOWNLOAD_NOTIFICATIONS = 0: none) governs this one too.
        guard SteamDownloadBackground.notificationsEnabled else { return }
        let minutes = Int(Self.gameMinutes)
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else { return }
            let content = UNMutableNotificationContent()
            content.title = "Your game is paused"
            content.body = "BCD kept it running for \(minutes) minutes in the background. Open BCD to carry on."
            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "bcd.background-run",
                                                                         content: content, trigger: nil))
        }
    }
}
