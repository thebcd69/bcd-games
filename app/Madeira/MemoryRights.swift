// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

import Foundation

// Engine side (it reads the entitlements and writes the log), so not a BCD*.swift screen file: the preview
// build leaves it out. An install without the increased memory limit (owner, 2026-10-21: say so, never block). iOS then closes large
// games early, while small games can still run: of 89 logs from such installs, Doom 3, GTA V Legacy, Moonlighter and
// Tomb Raider: Underworld got in game, so nothing is refused. BCD says it once per build in a small box with OK over
// the library, and again where a log is sent, because such a log names the setup, not a bug BCD can fix.
// The entitlement read agrees with the provisioning profile line (`profile-increased-memory=`) on all 427 logged
// installs that have both (89 without, 338 with), so it is the signal.

enum MemoryRights {
    /// Read once per run; the entitlements do not change while BCD runs.
    static let lacking: Bool = !EntitlementStatus.check().increasedMemory

    /// Where a log is sent (Settings › Logs, a game's page).
    static let sendLogAdvice = "This install lacks the increased memory limit, the usual cause of crashes on it. "
        + "Add it with GetMoreRam, or sign BCD with a paid developer account, before sending a log."

    /// The box at start.
    static let startNotice = "This install lacks the increased memory limit, so iOS closes large games early; "
        + "small games can still run. Add it with GetMoreRam (with SideStore or LiveContainer), or sign BCD with "
        + "a paid developer account. BCD's README explains how."

    /// Where a game has just stopped on an install without the limit (the Dock's stopped screen, auto-tune's alert): round 624 (agent N S1;
    /// Q9NC0W/YNBVQ8 wrote "does not start on iPhone 15 Pro" and "Wine starts but not the game"); the install, not the game, is the usual reason.
    static let failureNote = "This install lacks the increased memory limit, the usual reason a game stops this early. "
        + "Add it with GetMoreRam (with SideStore or LiveContainer), or sign BCD with a paid developer account."

    /// The defaults key holding the build the box was last shown for.
    static let shownBuildKey = "bcd.memoryNotice.shownBuild"

    /// The box is due when the limit is lacking and it has not been shown for this build.
    static func due(lacking: Bool, shownBuild: String?, build: String?) -> Bool {
        lacking && build != nil && shownBuild != build
    }
}

/// The start box's state (BCDLiveFrontEnd shows it; OK clears it).
@MainActor final class MemoryRightsNotice: ObservableObject {
    static let shared = MemoryRightsNotice()

    @Published var message: String?
    /// The box's title: "Memory limit", or the one of the box below.
    @Published var title = "Memory limit"
    private var started = false
    private var poolStop: String?

    /// madeira-bcd (round 624, agent N S16; 0ZHPYJ and 34 more archive logs): the last run never came back from the JIT memory request
    /// (BCDSessionRecorderLive.recoverUnfinished reads the start-up phase file). Said once, in this box, before and never together with the
    /// memory-limit words (those wait for the next run).
    static let poolStopTitle = "The last start stopped early"
    /// Round 630 (agent A1 S6): the box's title for a copy that cannot use JIT.
    static let notDebuggableTitle = "JIT can't turn on"
    /// Round 630 (agent A1 S8 and S19; Cult of the Lamb 4CTGPT, GTA V Legacy RT7HMV): over 1,057 archive starts with a JIT memory request, a
    /// request more than 30 s after JIT turned on stopped 20 to 25 times as often, and the games that ask several times (the Rockstar shape:
    /// three requests) 9.4 % against 1.4 %; the built-in way answers the first request in 0.74 s, StikDebug in 1.8 s. Text only.
    static let poolStopWords = "Last time BCD stopped while JIT was being set up for the game: its JIT memory request did not come back. "
        + "Check in Settings › JIT that JIT turns on, that LocalDevVPN is connected and that the pairing file is set, then press Play again. "
        + "It happens more often when JIT turned on long before Play, and with games that ask for JIT memory several times (GTA V): press "
        + "Play soon after JIT turns on. The built-in way answers these requests faster than StikDebug."
    func lastStartStoppedInPoolRequest() {
        if started {
            if message == nil { title = Self.poolStopTitle; message = Self.poolStopWords }
        } else {
            poolStop = Self.poolStopWords
        }
    }

    /// When the library first appears, once per run.
    func start() {
        guard !started else { return }
        started = true
        if let words = poolStop {
            poolStop = nil
            title = Self.poolStopTitle
            message = words
            return
        }
        // Round 630 (agent A1 S6; YKKBBN): a copy signed without get-task-allow can never turn JIT on, and said so only when Play was
        // pressed. Said at the first library screen of every run, before the memory-limit words (which then wait for a later run).
        if !SigningStatus.current.debuggable {
            title = Self.notDebuggableTitle
            message = SigningStatus.notDebuggableMessage
            return
        }
        title = "Memory limit"
        let build = Bundle.main.infoDictionary?["CFBundleVersion"] as? String
        guard MemoryRights.due(lacking: MemoryRights.lacking,
                                  shownBuild: UserDefaults.standard.string(forKey: MemoryRights.shownBuildKey),
                                  build: build) else { return }
        UserDefaults.standard.set(build, forKey: MemoryRights.shownBuildKey)
        LogStore.shared.log("[memory-rights] increased-memory=0: told once for build \(build ?? "?")")
        message = MemoryRights.startNotice
    }
}
