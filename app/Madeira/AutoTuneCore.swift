// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
//
//  AutoTuneCore.swift
//  Madeira
//
//  madeira-bcd: auto-tune's logic (docs/AUTO-TUNE.md). Foundation only:
//  no UI, no app state, nothing that lives past a call. tests/host/check-autotune.py
//  compiles it alone with the rule table and replays real session logs through it.
//
//  Auto-tune never runs while a game runs. A session log is read after the session
//  ended (or at the next start, after a killed run), as a stream of 64 KB chunks
//  with fixed-size state, so a 50 MB log costs no more memory than a 3 MB one.
//

import Foundation

// MARK: - Rules

/// One thing to look for in a session log: every string of `has` on one line.
struct AutoTuneSignal: Codable, Hashable {
    var has: [String]
    /// At least this many such lines (1 when absent).
    var count: Int?
}

/// One known fix: the lines an attempt adds when a session failed with these signals.
/// The table is generated from build/tools/autotune_rules.py
/// (AutoTuneRules.generated.swift); Documents/autotune-rules.json may add rules or
/// replace built-in ones of the same id.
struct AutoTuneRule: Codable {
    var id: String
    /// "key = value" each.
    var lines: [String]
    /// Lower is tried first among the rules one session matches.
    var priority: Int
    var all: [AutoTuneSignal]
    var any: [AutoTuneSignal]?
    var none: [AutoTuneSignal]?
    /// The failures it answers; every failure when absent.
    var outcomes: [String]?
    /// "library" (a direct start) or "dock" (a Steam game through Madeira Dock); both when absent.
    var session: String?
    /// "d3d11" or "d3d12"; either when absent.
    var api: String?
    var ramMinMB: Int?
    var ramMaxMB: Int?
    /// How far the session got (AutoTuneLogFacts.stage).
    var minStage: Int?
    var maxStage: Int?
    /// The largest image that got no JIT-pool copy ("[jit-pool] EXHAUSTED (image BASE+SIZE)") was at most this many
    /// MiB. A session whose log has no such line passes (an anonymous allocation that failed, or no pool failure).
    /// pool-split-exhausted uses it: no region of a split pool takes an image of 300 MiB or more after the 100-190 MiB of
    /// Wine copies that are already placed, so the attempt cannot help (Stellar Blade Demo, FINAL FANTASY RESONANCE DEMO).
    var imageMaxMB: Int?
    /// The session's main program (the image at 0x140000000 whose relocations are stripped, "ml985: map_image_view size=0x...") was at most
    /// this many MiB. A log with no such line passes. exe-window-fixed-base uses it: its line asks for a 512 MB window, which cannot hold
    /// a program of 512 MiB or more (Persona 4 Golden's 888 MiB), and no window is larger than the free run below the system's thread stacks.
    var exeMaxMB: Int?
    /// The main program (as exeMaxMB reads it) was at least this many MiB, and the log has to have such a line. exe-window-fixed-base-large uses
    /// it for the programs the 512 MB window of exe-window-fixed-base cannot hold (Resident Evil Requiem's 575 MiB, round 591).
    var exeMinMB: Int?
    /// The last footprint line was at least this many percent of the memory limit the marker or the log names; a session with no footprint line
    /// or no limit passes. The swap rules for a memory kill use it: a kill with 2 GB of an 8 GB limit used is not iOS closing the game for memory
    /// (Albino Lullaby 41D565, round 591).
    var memoryMinPct: Int?
    /// May replace a line auto-tune kept earlier with the same key (a larger swap file
    /// after the first was not enough). Never a key the user sets.
    var upgrade: Bool?
    /// The failure comes back the same on every start (the JIT pool filling up while DLLs
    /// load): when it matches, the unchanged retry is skipped and the first line is tried at once.
    var noRetry: Bool?
    /// After a `killed` verdict this rule's lines may replace the recommended list's own lines, but only with a larger number
    /// (the 12 GB tier's swap-mb = 6144 over GTA V Enhanced's list line swap-mb = 3072): the list's file entries are otherwise as good as the
    /// user's own and keep a rule out. Never a user's key, and never a lower or equal value.
    var listUpgrade: Bool?
    /// Round 625 (owner's item 21, agent E1 S13-c; Hogwarts Legacy GBQRKH): with `listUpgrade`, a crashed or exited-early verdict may
    /// raise the list's number too, not only a memory kill; the larger-number test still applies, and never a user's key.
    var listUpgradeFailure: Bool?
    /// One sentence the app shows with the attempt.
    var reason: String
    /// Fixture names and notes the rule was taken from.
    var evidence: [String]?
    /// A failure only a change in Madeira fixes: the build that fixed it. Such a rule has no
    /// lines; when it is the best match, auto-tune stops and says so instead of spending
    /// attempts on lines that cannot help (Warcraft III's 16-bit back buffer, build 560).
    var fixedIn: Int?
    /// A failure no setting changes and no build is known to fix: Valve's client said the game's program cannot be prepared on this device
    /// (CEG result 55), or a bug of BCD's that is open (a Metal texture released twice). Like fixedIn, the rule has no lines and, when it is
    /// the best match, auto-tune stops at once (status "known") instead of spending a retry; its `reason` says what it is, and the advice does
    /// not promise a build.
    var noFix: Bool?
    /// madeira-bcd (round 624, RETRO-2 RQ1): a noFix rule that is also evaluated for a session scored "ran" or "quit": the session looked
    /// played (the clock raised its stage, or the player quit), yet the rule's signals say why it was none (Forza Horizon 4 waiting 314 s
    /// with no frame, Diablo II: Resurrected inside its loader's protection, Porsche's Direct3D 7 box). Only a noFix rule may carry it, and
    /// it only changes the verdict's text ("known"): nothing is tried.
    var evaluateWhenRan: Bool?
    /// madeira-bcd (round 624, Fallout 4 voices, WRSNC8): an evaluateWhenRan rule that speaks ONLY for a session that played: its signals are
    /// the game's own side effect (a missing audio decoder), never the reason a failed session failed, so a crash keeps its own verdict.
    var ranOnly: Bool?
}

enum AutoTuneRules {
    /// The built-in table, then the override file's rules (a rule with a built-in id
    /// replaces it; "lines": [] in an override disables it). Decoded for one analysis.
    static func load(builtIn: String, override: URL?) -> [AutoTuneRule] {
        let decoder = JSONDecoder()
        var rules = (try? decoder.decode([AutoTuneRule].self, from: Data(builtIn.utf8))) ?? []
        if let override, let data = try? Data(contentsOf: override),
           let extra = try? decoder.decode([AutoTuneRule].self, from: data) {
            for rule in extra {
                rules.removeAll { $0.id == rule.id }
                if !rule.lines.isEmpty || rule.fixedIn != nil || rule.noFix == true { rules.append(rule) }
            }
        }
        return rules
    }

    /// The key of a "key = value" line.
    static func key(_ line: String) -> String {
        (line.split(separator: "=", maxSplits: 1).first.map(String.init) ?? line)
            .trimmingCharacters(in: .whitespaces)
    }
    /// The value of a "key = value" line (empty when there is none).
    static func value(_ line: String) -> String {
        let parts = line.split(separator: "=", maxSplits: 1)
        return parts.count == 2 ? parts[1].trimmingCharacters(in: .whitespaces) : ""
    }
}

// MARK: - Log scan

/// What one session log says, gathered with fixed-size state.
struct AutoTuneLogFacts: Codable, Equatable {
    var build: Int?
    var ramMB: Int?
    var availableMB: Int?
    /// madeira-bcd (round 624, agent D3 S5-3; Sekiro XJ8YM3, Cloudpunk F88RB4): the free storage the device reported at start, whole GB
    /// ("[device] settings: ... free-disk=2GB"); nil when it said "unknown" or the log has no such line.
    var freeDiskGB: Int?
    var dock = false
    var d3d12 = false
    /// 0 nothing, 1 Wine started, 2 the game's modules loaded, 3 first frame,
    /// 4 presenting for 20 s, 5 played on (5 minutes alive).
    var stage = 0
    var aliveSeconds = 0
    var lastFootprintMB: Int?
    var peakFootprintMB: Int?
    /// madeira-bcd (round 624, Metro 2033 Redux Y1H618/CTJQ9D): the last slow "[footprint] rev=ml358" value, and whether the newest
    /// footprint line was the fast sampler's (a jump the slow line did not see yet).
    var lastSlowFootprintMB: Int?
    var lastFootprintFast = false
    /// The memory limit the log itself names: "ml992: os_proc_available_memory=A MB + phys_footprint=B MB = L MB".
    /// A kill is told from the footprint's last line against it when the app's own marker had none (Clair Obscur
    /// A5HRFG died at 6105 of 6144 MB and was not seen as killed: the fallback below guessed 6366).
    var limitMB: Int?
    var quitRequested = false
    var returnedToLibrary = false
    var exitStatus: UInt32?
    var unhandled = 0
    var runtimeErrorDialog = false
    /// madeira-bcd (round 624, Portal 2 HZE5E9, RoboCop TK6WRV): "[steam-api] steam_api found no running Steam", the game's own Steam
    /// library said no Steam client was running (the Steam IPC family, 82 archive logs).
    var steamNotFound = false
    /// madeira-bcd (round 624, agent R S29: GTA V Legacy AX6XNN, GTA IV 5T9WRM): the Rockstar Games Launcher's own error line, mirrored
    /// by [guest-log] ("[error] * Code:      113 (LAUNCHER_ERR_STEAM_FAILED)"; 58 archive logs, all 113). AX6XNN showed it, never drew a
    /// game frame and was scored "ran, ok" because the Launcher's window presented for two minutes.
    var launcherErrors = 0
    /// A Launcher error line came before the player's close (the close of a Launcher that had already failed is no play).
    var launcherErrorBeforeQuit = false
    var freezeGaps = 0
    var mainStalls = 0
    /// The highest "detach-wait: presents=N" (every 30 s while the app waits on the game;
    /// in a Madeira Dock session the only frame count the log has).
    var presentsLogged = 0
    /// Whether any "detach-wait: presents=N" line was seen at all. Only Dock sessions print it, so a
    /// zero count means "no frame" only when the counter was there.
    var presentsCounted: Bool? = nil
    /// Metal command buffers that failed ("Device error at frame"): a GPU hang or fault.
    var gpuErrors = 0
    /// What the game's own error boxes said ([win-name] texts that read like an error),
    /// at most 4, 200 characters each: Darksiders II's "resolution 1112 x 832 is not
    /// supported for fullscreen display" is the reason, not anything in Wine's lines.
    var dialogs: [String] = []
    /// A process that ended itself with an NTSTATUS error, a child the Dock saw exit with a
    /// negative code, or a DLL whose initialisation failed: in none of the logs of a session
    /// that played (2026-10-16 corpus: 16 of 16 clean), in most crashes.
    var errorExits = 0
    /// A .NET Framework program asked Wine's mscoree for Wine Mono and this device has none
    /// ("err:mscoree:CLRRuntimeInfo_GetRuntimeHost Wine Mono is not installed", Panzer Paladin,
    /// logs 5G8ZKE and AXKB2X). Only that err line counts: the "[wine-mono] ml1275 neither this
    /// build nor a download has Wine Mono" line is written at every start of a device without
    /// Mono, for games that do not need .NET too.
    var monoMissing = false
    /// The largest image in the log's "[jit-pool] EXHAUSTED (image 0xBASE+0xSIZE)" lines, in MiB (rounded down): the
    /// loader found no room in the JIT pool for that image's copy. nil when no image failed that way.
    var jitImageFailMB: Int?
    /// The largest main-program image the log maps at its fixed base 0x140000000 with its relocations stripped ("ml985: map_image_view
    /// size=0xS base=0x140000000 ... charact=0x23", printed for any image of 64 MiB or more), in MiB (rounded down). nil when there is none.
    var fixedExeMB: Int?
    /// Crash evidence already in the log when the user asked to quit: a Steam game through
    /// Madeira Dock can crash and leave the session open until the user closes it.
    var crashEvidenceAtQuit: Int?
    /// This app run was a Madeira Dock session of the one-time installs only ("[madeira-dock] app N: this session runs only
    /// the one-time installs; the host starts after a restart"): no game runs in it.
    var installsOnly = false
    /// The install batch ended ("[dock-installers] end succeeded=N failed=M of=T; the session ends, the game starts after a
    /// restart"): what the session does after that is BCD's own restart, not the game.
    var installsEnded = false
    /// The one-time installs are over and no game ran: BCD closes the session itself for a restart (Grand Theft Auto V's
    /// Rockstar installs, NM88NQ), or an installer's helper failed and the batch ended with it (Saints Row: The Third's DirectX
    /// setup, T3P8HK). Such a run is no session of the game: nothing in it is a failure of the game's settings. A batch that
    /// never ended (Batman: Arkham City's .NET setup) is not this: its hang is scored as before.
    var installsOver: Bool { installsOnly && installsEnded }
    /// The Wine process ended with exit code 0 ("[WineProc] Wine exited with code 0 (caught by longjmp)"). What the log shows after it is
    /// teardown: a worker thread that was still running when the modules were unmapped faults (Shade: Wrath of Angels, 2Y4GAX and TK5QJY, the
    /// game had presented for 75 s and the app scored a crash). Faults and error exits counted before this line stay crash evidence; after it
    /// nothing is counted (unhandled, errorExits, exitStatus) and the app's own crash status is not taken for the game's.
    var cleanMainExit = false
    /// madeira-bcd (round 624): the launcher's Wine process ended with code 0 while a child process it started still ran ("[WineProc]
    /// madeira-bcd: the main process exited but N child process(es) it started still run ... a launcher started the game"). What a child
    /// does after that line is the game's own run, not the teardown of the launcher: Control (FGTGM8) died in its child control_dx11.exe,
    /// 11 s into the start, with the launcher already at code 0, and the session was scored "exited-early" with no rule matched.
    var childrenPending = false
    /// The crash evidence counted after that line (the three counters above, kept apart): it is the session's own only when the game
    /// never presented (stage below 4, AutoTuneEngine.outcome).
    var childUnhandled = 0
    var childExitStatus: UInt32?
    var childErrorExits = 0
    /// The last "[hwinput] keyboard focus:" line said the keyboard went elsewhere because the app went inactive or the pointer left the
    /// game view: the user was away when the log ended. Final Fantasy Tactics 7QVF78 (19 s, "app inactive") and THPS 3+4 NZECAK
    /// (24 s, "pointer left the game view") were scored as the game ending early.
    var focusLeft = false
    /// Something printed a frame: "Game is presenting (#1", "Early detach: game presenting and settled", a "detach-wait: presents=N" with
    /// N of 1 or more, or the launch cover coming down because of a present. The stage rises with other lines too, and with the clock:
    /// the cover coming down to the desktop ("reason=show-desktop") counts as a first frame (stage 3) and five minutes alive as stage 5.
    var frameSeen = false
    /// The Dock's frame counter was in the log ("detach-wait: presents=N") and stayed at 0, and nothing else printed a frame: a run whose
    /// stage came from the clock or from the cover going to the desktop did not play (Call of Duty HQ waited 331 s with presents=0; the
    /// Rockstar launcher sessions of GTA V Legacy and GTA IV; Doom 3, which draws through OpenGL).
    var noFrames: Bool { presentsCounted == true && presentsLogged == 0 && !frameSeen }

    /// The session summary line BCD writes when a session ends, or when the app goes to the background ("[session] length=1234s play=1180s
    /// frames=71234 avg=58.7 low1=41.2 ..." in BCDSessionCore.swift): the whole numbers of the newest one in the log. A replay and the log
    /// cards show them; nothing decides by them. nil: the log has no such line (a session that was closed from outside, or an older build).
    var sessionAverageFPS: Int?
    var sessionLow1FPS: Int?
    var sessionPlaySeconds: Int?
    /// The newest summary was the partial one written when the app went to the background (the session did not end after it).
    var sessionPartial = false
    /// madeira-bcd (round 624): "[fexlock] STUCK write-wait" lines: a thread waits for a FEX lock that is never released (R.E.P.O. AJ7RNE: the
    /// thread waits for a lock it holds itself). Counted, so a session that never presented and logged such a deadlock is a hang, not a run.
    var fexlockStuck = 0
    /// madeira-bcd (round 624, Kerbal Space Program 2G3Q3V): the last "detach-wait: presents=N" value and how many times in a row the same value
    /// came again (the line comes every 30 s), so that a frame counter which stopped can be told from one that never ran.
    var presentsLast = -1
    var presentsFlat = 0
    /// A thread waited for a FEX lock it owned ("[fexlock] STUCK") and the frame counter had not moved for three readings (90 s): the game is frozen.
    var lockFreeze: Bool { fexlockStuck > 0 && presentsCounted == true && presentsLast > 0 && presentsFlat >= 3 }
    /// The same, as it stood when the player asked to quit (a frozen game the player closed is not one that was quit after playing).
    var lockFreezeAtQuit = false
    /// madeira-bcd (round 624, RETRO-1 R4, WRC 9 JDJ8KE/WV1P7T): "[spin] ml1140 ... look 1: x64->EC" reports (a guest thread in a tight loop).
    /// With the Dock's frame counter standing still for two readings (60 s) after frames, the game is frozen, like lockFreeze.
    var spinReports = 0
    var spinFreeze: Bool { spinReports > 0 && presentsCounted == true && presentsLast > 0 && presentsFlat >= 2 }
    var spinFreezeAtQuit = false
    /// madeira-bcd (round 624, RETRO-2 RQ3, Ghost of Tsushima JKJA9Q): the last error exit of a program of the session came after a frame had
    /// been seen (the game itself failing), not before the first frame (a helper such as crs-video.exe ending with an error while the game loads).
    var lastErrorExitAfterFrame = false
    /// madeira-bcd (round 624, Farming Simulator 22 on Discord): "[mach_exc] UNHANDLED #1 ... x18=0x0": a host fault in a thread with no TEB
    /// that nothing handled (the game's process stopped there while the app kept waiting).
    var machFaultNoTeb = 0

    var crashEvidence: Int { unhandled + errorExits + (runtimeErrorDialog ? 1 : 0) + (exitStatus == nil ? 0 : 1) }
}

final class AutoTuneLogScanner {
    static let chunk = 65536
    static let maxLine = 16384

    private(set) var facts = AutoTuneLogFacts()
    /// Lines that matched each signal, in the order of `signals`.
    private(set) var counts: [Int]
    /// The first line that matched each signal (at most 24 lines, 240 bytes each).
    private(set) var evidence: [String] = []
    private let needles: [[[UInt8]]]
    private var carry: [UInt8] = []

    init(signals: [AutoTuneSignal]) {
        needles = signals.map { $0.has.map { Array($0.utf8) } }
        counts = Array(repeating: 0, count: signals.count)
    }

    /// Streams the whole file. bcd-log.txt holds one app run, which runs one Wine
    /// session, and its build and device lines are written before the session starts.
    static func scan(_ url: URL, signals: [AutoTuneSignal]) -> AutoTuneLogScanner? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        let scanner = AutoTuneLogScanner(signals: signals)
        while let data = try? handle.read(upToCount: chunk), !data.isEmpty {
            scanner.feed(data)
        }
        scanner.finish()
        return scanner
    }

    func feed(_ data: Data) {
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let base = raw.baseAddress else { return }
            var start = 0
            while start < raw.count {
                let rest = raw.count - start
                if let nl = memchr(base + start, 0x0A, rest) {
                    let end = start + (UnsafeRawPointer(nl) - (base + start))
                    let piece = UnsafeRawBufferPointer(start: base + start, count: end - start)
                    if carry.isEmpty {
                        line(piece)
                    } else {
                        append(piece)
                        carry.withUnsafeBytes { line($0) }
                        carry.removeAll(keepingCapacity: true)
                    }
                    start = end + 1
                } else {
                    append(UnsafeRawBufferPointer(start: base + start, count: rest))
                    break
                }
            }
        }
    }

    func finish() {
        if !carry.isEmpty {
            carry.withUnsafeBytes { line($0) }
            carry.removeAll()
        }
        if facts.aliveSeconds >= 300 { facts.stage = max(facts.stage, 5) }
    }

    /// A line longer than maxLine is kept to its first maxLine bytes.
    private func append(_ piece: UnsafeRawBufferPointer) {
        let room = Self.maxLine - carry.count
        if room > 0 { carry.append(contentsOf: piece.prefix(room)) }
    }

    private func line(_ whole: UnsafeRawBufferPointer) {
        // The same cut whether the line came whole from one chunk or through `carry`.
        let l = whole.count > Self.maxLine ? UnsafeRawBufferPointer(rebasing: whole[0..<Self.maxLine]) : whole
        guard l.count > 4 else { return }
        for i in needles.indices where Self.hasAll(l, needles[i]) {
            counts[i] += 1
            if counts[i] == 1 && evidence.count < 24 {
                evidence.append(String(decoding: l.prefix(240), as: UTF8.self))
            }
        }
        markers(l)
    }

    private static func find(_ l: UnsafeRawBufferPointer, _ needle: [UInt8]) -> Int? {
        guard let base = l.baseAddress, !needle.isEmpty, l.count >= needle.count else { return nil }
        return needle.withUnsafeBytes { n -> Int? in
            guard let hit = memmem(base, l.count, n.baseAddress!, n.count) else { return nil }
            return UnsafeRawPointer(hit) - base
        }
    }

    private static func hasAll(_ l: UnsafeRawBufferPointer, _ list: [[UInt8]]) -> Bool {
        for n in list where find(l, n) == nil { return false }
        return !list.isEmpty
    }

    /// The decimal number right after `marker`, if any.
    private static func number(_ l: UnsafeRawBufferPointer, after marker: [UInt8], hex: Bool = false) -> UInt64? {
        guard let at = find(l, marker) else { return nil }
        var i = at + marker.count, value: UInt64 = 0, digits = 0
        while i < l.count, digits < 16 {
            let c = l[i]
            let d: UInt64
            if c >= 0x30 && c <= 0x39 { d = UInt64(c - 0x30) }
            else if hex && c >= 0x61 && c <= 0x66 { d = UInt64(c - 0x61 + 10) }
            else if hex && c >= 0x41 && c <= 0x46 { d = UInt64(c - 0x41 + 10) }
            else { break }
            value = value * (hex ? 16 : 10) + d
            digits += 1
            i += 1
        }
        return digits > 0 ? value : nil
    }

    private static let mBuild = Array("[build] v0.1.".utf8)
    private static let mDevice = Array("[device] model=".utf8)
    private static let mRam = Array(" ram=".utf8)
    private static let mLaunch = Array("[device] launch:".utf8)
    private static let mAvailable = Array("available=".utf8)
    private static let mFreeDisk = Array(" free-disk=".utf8)
    private static let mDock = Array("[dock-launch]".utf8)
    /// A D3D12 game opens a command queue; a D3D11 game may only probe D3D12CreateDevice (GTA V Legacy).
    private static let mD3D12 = Array("[madeira-d3d12] command queue: type".utf8)
    private static let mWineStart = Array("[phase] wine-start".utf8)
    private static let mModule = Array("Load module ".utf8)
    private static let mX64Image = Array("[x64-image]".utf8)
    private static let mJitImageFail = Array("[jit-pool] EXHAUSTED (image 0x".utf8)
    /// "+0x" as bytes, not as an Array("...".utf8) literal: tools/autotune-fixture.py keeps every line a literal of that
    /// shape matches, and "+0x" would match far more than the pool's line.
    private static let mPlus0x: [UInt8] = [0x2B, 0x30, 0x78]
    private static let mMapImage = Array("ml985: map_image_view size=0x".utf8)
    private static let mFixedBase = Array("base=0x140000000 ".utf8)
    private static let mRelocsStripped = Array("charact=0x23 ".utf8)
    private static let mPresenting = Array("Game is presenting (#1".utf8)
    private static let mSettled = Array("Early detach: game presenting and settled".utf8)
    private static let mAlive = Array("[freeze] alive t+".utf8)
    private static let mLimitLine = Array("ml992: os_proc_available_memory=".utf8)
    /// " MB = " as bytes, not as an Array("...".utf8) literal: tools/autotune-fixture.py keeps every line a literal of that
    /// shape matches, and this one would match far more than the ml992 line.
    private static let mLimitEq: [UInt8] = [0x20, 0x4D, 0x42, 0x20, 0x3D, 0x20]
    private static let mFootprint = Array("[footprint] rev=ml358 phys=".utf8)
    /// The fast sampler's line ("[footprint] rev=ml1060 fast phys=N MB"): near the limit it is written more often than the
    /// ml358 census, so a kill's last reading is often only there (Miles Morales V4JGSJ: 8154 MB fast vs 7975 MB census).
    private static let mFootprintFast = Array("[footprint] rev=ml1060 fast phys=".utf8)
    private static let mPeak = Array("(peak ".utf8)
    private static let mQuit = Array("[frontend] graceful close requested".utf8)
    private static let mReturned = Array("[frontend] returned to library".utf8)
    private static let mExitReport = Array("[exit-report] status=0x".utf8)
    private static let mUnhandled = Array("Unhandled exception".utf8)
    private static let mRootFrame = Array("ACCEPTING terminal root frame".utf8)
    private static let mRuntimeError = Array("Runtime error!".utf8)
    /// madeira-bcd (round 624, Gal*Gun 2 B2B7BR): an Unreal Engine 4 program's own crash window ("The UE4-<project> Game has crashed and
    /// will close"), shown when the program crashed; the session had no exit status and was scored "ran".
    private static let mUE4Crash = Array("has crashed and will close".utf8)
    private static let mSteamNotFound = Array("steam_api found no running Steam".utf8)
    private static let mLauncherError = Array("(LAUNCHER_ERR_".utf8)
    private static let mFreeze = Array("cause=unexplained-FREEZE".utf8)
    private static let mMainStall = Array("[main-stall] #".utf8)
    private static let mFexlockStuck = Array("[fexlock] STUCK write-wait".utf8)
    /// madeira-bcd (round 624, RETRO-1 R4): the [spin] report's look at a guest thread in a tight loop.
    private static let mSpinLook = Array(" look 1: x64->EC ".utf8)
    /// madeira-bcd (round 624, Farming Simulator 22): an unhandled host fault in a thread with no TEB (both on one line).
    private static let mMachUnhandled1 = Array("[mach_exc] UNHANDLED #1 pc=".utf8)   // "#1 pc=": not #12400 (Watch Dogs 2's SMC traps)
    private static let mX18Zero = Array(" x18=0x0 ".utf8)
    private static let mDetachWait = Array("detach-wait: presents=".utf8)
    private static let mLaunchView = Array("[launch-view] dismissed reason=".utf8)
    private static let mCoverByPresent = Array("dismissed reason=present".utf8)
    private static let mDeviceError = Array("Device error at frame".utf8)
    /// madeira-bcd (round 624, Rise of the Tomb Raider): the game's own message (mirrored by [guest-log]) that its swapchain could not be
    /// created. It ends itself with exit 0 and never presents, and without this the session was scored "ran" or "quit" (8 Dock logs).
    private static let mSwapChainFail = Array("[SwapChain] Error occurred during creation.".utf8)
    private static let mTerminate = Array("NtTerminateProcess(".utf8)
    private static let mErrorCode = Array("exit_code=0xc".utf8)
    /// The whole Wine process finished with code 0 (the trailing " (" keeps code 0 from matching 01, 0x.. and the like).
    private static let mWineExit0 = Array("[WineProc] Wine exited with code 0 (".utf8)
    private static let mMainExitedChildren = Array("[WineProc] madeira-bcd: the main process exited but".utf8)
    private static let mFocus = Array("[hwinput] keyboard focus: ".utf8)
    private static let mFocusElsewhereInactive = Array("elsewhere (app inactive)".utf8)
    private static let mFocusElsewherePointer = Array("elsewhere (pointer left the game view)".utf8)
    /// 0x8... codes too: a breakpoint (0x80000003) or Wine's assertion exit (0x80000101).
    private static let mWarningCode = Array("exit_code=0x8".utf8)
    private static let mChildNegative = Array("child exited with code -".utf8)
    private static let mInitFailed = Array("\" failed to initialize, aborting".utf8)
    /// The whole app ended: an Objective-C exception (Warcraft III's 16-bit back buffer,
    /// 'CAMetalLayerInvalid'), or the redelivery guard ending a fault loop (Warcraft II's
    /// self-modifying code). Both leave a killed run whose log says why.
    private static let mObjCException = Array("*** Terminating app due to uncaught exception".utf8)
    private static let mRedelivKill = Array("[redeliv] terminating process".utf8)
    private static let mMscoreeErr = Array("err:mscoree:".utf8)
    private static let mMonoMissing = Array("Wine Mono is not installed".utf8)
    private static let mWinName = Array("[win-name] #".utf8)
    private static let mText = Array(" text=\"".utf8)
    /// The app's own words for a Madeira Dock session that runs only the one-time installs: in the "[madeira-dock] app N: ..." line
    /// and again in the "[dock-installers] app=N ..." line, once per such session and in no other.
    /// The session summary line (BCDSessionCore.swift), not the line that says a session did not end cleanly.
    private static let mSession = Array("[session] length=".utf8)
    private static let mSessionAverage: [UInt8] = [0x20, 0x61, 0x76, 0x67, 0x3D]   // " avg=" as bytes: tools/autotune-fixture.py keeps every line a literal matches
    private static let mSessionLow1: [UInt8] = [0x20, 0x6C, 0x6F, 0x77, 0x31, 0x3D]   // " low1=" as bytes: tools/autotune-fixture.py keeps every line a literal matches
    private static let mSessionPlay: [UInt8] = [0x20, 0x70, 0x6C, 0x61, 0x79, 0x3D]   // " play=" as bytes: tools/autotune-fixture.py keeps every line a literal matches
    private static let mSessionPartial: [UInt8] = [0x20, 0x70, 0x61, 0x72, 0x74, 0x69, 0x61, 0x6C, 0x3D, 0x31, 0x20]   // " partial=1 " as bytes: tools/autotune-fixture.py keeps every line a literal matches
    private static let mInstallsOnly = Array("this session runs only the one-time installs".utf8)
    private static let mInstallsEnded = Array("[dock-installers] end succeeded=".utf8)
    /// Window titles that read like an error and are not one. GameMaker's runner creates a hidden form named "Error Messages"
    /// at start, long before the game's own window (Hotline Miami, logs 339R82 and 1VPXSG, lines 1913 and 1919 of 12,400);
    /// counted as the game's error box it made a game that ended itself with exit 0 a "crash".
    private static let notDialogs: Set<String> = ["Error Messages"]
    private static let errorWords = ["error", "Error", "ERROR", "could not", "Could not", "Could Not", "failed", "Failed",
                                     "not supported", "unable", "Unable", "cannot", "Cannot", "Fatal", "fatal"].map { Array($0.utf8) }

    private func markers(_ l: UnsafeRawBufferPointer) {
        typealias S = AutoTuneLogScanner
        if S.find(l, S.mSession) != nil {
            facts.sessionPartial = S.find(l, S.mSessionPartial) != nil
            facts.sessionAverageFPS = S.number(l, after: S.mSessionAverage).map { Int($0) }
            facts.sessionLow1FPS = S.number(l, after: S.mSessionLow1).map { Int($0) }
            facts.sessionPlaySeconds = S.number(l, after: S.mSessionPlay).map { Int($0) }
        }
        if facts.build == nil, let v = S.number(l, after: S.mBuild) { facts.build = Int(v) }
        if facts.ramMB == nil, S.find(l, S.mDevice) != nil, let v = S.number(l, after: S.mRam) { facts.ramMB = Int(v) }
        if facts.availableMB == nil, S.find(l, S.mLaunch) != nil, let v = S.number(l, after: S.mAvailable) {
            facts.availableMB = Int(v)
        }
        if facts.freeDiskGB == nil, S.find(l, S.mFreeDisk) != nil, let v = S.number(l, after: S.mFreeDisk) { facts.freeDiskGB = Int(v) }
        if !facts.dock, S.find(l, S.mDock) != nil { facts.dock = true }
        if !facts.d3d12, S.find(l, S.mD3D12) != nil { facts.d3d12 = true }
        if facts.stage < 1, S.find(l, S.mWineStart) != nil { facts.stage = 1 }
        if facts.stage < 2, S.find(l, S.mModule) != nil || S.find(l, S.mX64Image) != nil { facts.stage = 2 }
        if facts.stage < 3, S.find(l, S.mPresenting) != nil { facts.stage = 3 }
        if facts.stage < 4, S.find(l, S.mSettled) != nil { facts.stage = 4 }
        if !facts.frameSeen, S.find(l, S.mPresenting) != nil || S.find(l, S.mSettled) != nil { facts.frameSeen = true }
        if let at = S.find(l, S.mJitImageFail),
           let size = S.number(UnsafeRawBufferPointer(rebasing: l[at...]), after: S.mPlus0x, hex: true) {
            facts.jitImageFailMB = max(facts.jitImageFailMB ?? 0, Int(min(size >> 20, 1 << 20)))
        }
        if S.find(l, S.mMapImage) != nil, S.find(l, S.mFixedBase) != nil, S.find(l, S.mRelocsStripped) != nil,
           let size = S.number(l, after: S.mMapImage, hex: true) {
            facts.fixedExeMB = max(facts.fixedExeMB ?? 0, Int(min(size >> 20, 1 << 20)))
        }
        if let v = S.number(l, after: S.mAlive) { facts.aliveSeconds = max(facts.aliveSeconds, Int(v)) }
        if facts.limitMB == nil, let at = S.find(l, S.mLimitLine),
           let v = S.number(UnsafeRawBufferPointer(rebasing: l[at...]), after: S.mLimitEq), v >= 1024, v <= 65536 {
            facts.limitMB = Int(v)
        }
        if let v = S.number(l, after: S.mFootprint) {
            facts.lastFootprintMB = Int(v)
            facts.lastSlowFootprintMB = Int(v)
            facts.lastFootprintFast = false
            if let p = S.number(l, after: S.mPeak) { facts.peakFootprintMB = max(facts.peakFootprintMB ?? 0, Int(p)) }
        } else if let v = S.number(l, after: S.mFootprintFast) {
            facts.lastFootprintMB = Int(v)
            facts.lastFootprintFast = true
            facts.peakFootprintMB = max(facts.peakFootprintMB ?? 0, Int(v))
        }
        if S.find(l, S.mQuit) != nil {
            if !facts.quitRequested {
                facts.crashEvidenceAtQuit = facts.crashEvidence
                facts.launcherErrorBeforeQuit = facts.launcherErrors > 0
                facts.lockFreezeAtQuit = facts.lockFreeze
                facts.spinFreezeAtQuit = facts.spinFreeze
            }
            facts.quitRequested = true
        }
        if S.find(l, S.mReturned) != nil { facts.returnedToLibrary = true }
        if !facts.cleanMainExit, S.find(l, S.mWineExit0) != nil { facts.cleanMainExit = true }
        if S.find(l, S.mFocus) != nil {
            facts.focusLeft = S.find(l, S.mFocusElsewhereInactive) != nil || S.find(l, S.mFocusElsewherePointer) != nil
        }
        if facts.cleanMainExit, !facts.childrenPending, S.find(l, S.mMainExitedChildren) != nil { facts.childrenPending = true }
        if let v = S.number(l, after: S.mExitReport, hex: true) {
            if !facts.cleanMainExit { facts.exitStatus = UInt32(truncatingIfNeeded: v) }
            else if facts.childrenPending { facts.childExitStatus = UInt32(truncatingIfNeeded: v) }
        }
        if S.find(l, S.mUnhandled) != nil || S.find(l, S.mRootFrame) != nil {
            if !facts.cleanMainExit { facts.unhandled += 1 } else if facts.childrenPending { facts.childUnhandled += 1 }
        }
        if S.find(l, S.mRuntimeError) != nil || S.find(l, S.mUE4Crash) != nil { facts.runtimeErrorDialog = true }
        if S.find(l, S.mSteamNotFound) != nil { facts.steamNotFound = true }
        if S.find(l, S.mLauncherError) != nil { facts.launcherErrors += 1 }
        if S.find(l, S.mFreeze) != nil { facts.freezeGaps += 1 }
        if S.find(l, S.mMainStall) != nil { facts.mainStalls += 1 }
        if S.find(l, S.mFexlockStuck) != nil { facts.fexlockStuck += 1 }
        if S.find(l, S.mSpinLook) != nil { facts.spinReports += 1 }
        if S.find(l, S.mMachUnhandled1) != nil, S.find(l, S.mX18Zero) != nil { facts.machFaultNoTeb += 1 }
        if !facts.cleanMainExit, S.find(l, S.mSwapChainFail) != nil { facts.errorExits += 1; facts.lastErrorExitAfterFrame = facts.frameSeen }
        if let v = S.number(l, after: S.mDetachWait) {
            facts.presentsCounted = true
            facts.presentsLogged = max(facts.presentsLogged, Int(v))
            if Int(v) == facts.presentsLast { facts.presentsFlat += 1 } else { facts.presentsLast = Int(v); facts.presentsFlat = 0 }
            if v >= 1 { facts.stage = max(facts.stage, 3); facts.frameSeen = true }
            if v >= 600 { facts.stage = max(facts.stage, 4) }   // about 20 s of frames
        }
        if facts.stage < 3, S.find(l, S.mLaunchView) != nil { facts.stage = 3 }
        if S.find(l, S.mLaunchView) != nil, S.find(l, S.mCoverByPresent) != nil { facts.frameSeen = true }
        if S.find(l, S.mDeviceError) != nil { facts.gpuErrors += 1 }
        if (S.find(l, S.mTerminate) != nil && (S.find(l, S.mErrorCode) != nil || S.find(l, S.mWarningCode) != nil))
            || S.find(l, S.mChildNegative) != nil || S.find(l, S.mInitFailed) != nil
            || S.find(l, S.mObjCException) != nil || S.find(l, S.mRedelivKill) != nil {
            if !facts.cleanMainExit { facts.errorExits += 1; facts.lastErrorExitAfterFrame = facts.frameSeen }
            else if facts.childrenPending { facts.childErrorExits += 1 }
        }
        if !facts.monoMissing, S.find(l, S.mMscoreeErr) != nil, S.find(l, S.mMonoMissing) != nil { facts.monoMissing = true }
        if !facts.installsOnly, S.find(l, S.mInstallsOnly) != nil { facts.installsOnly = true }
        if !facts.installsEnded, S.find(l, S.mInstallsEnded) != nil { facts.installsEnded = true }
        if facts.dialogs.count < 4, S.find(l, S.mWinName) != nil, let t = S.find(l, S.mText) {
            let start = t + S.mText.count
            var end = start
            while end < l.count, l[end] != 0x22 { end += 1 }      // the closing quote
            let text = String(decoding: UnsafeRawBufferPointer(rebasing: l[start..<min(end, start + 200)]), as: UTF8.self)
            // A caption alone ("Critical error") says less than the box's own line; keep both, once each.
            if S.errorWords.contains(where: { S.find(UnsafeRawBufferPointer(rebasing: l[start..<end]), $0) != nil }),
               !S.notDialogs.contains(text), !facts.dialogs.contains(text) { facts.dialogs.append(text) }
        }
    }
}

// MARK: - One session's result

/// What the app knew when the session ended (LibraryModel.finish), or what a
/// leftover session marker says about a run that never came back.
struct AutoTuneSessionEnd: Codable {
    var sawProcess = true
    var quitRequested = false
    var crashStatus: UInt32?
    var presents: Int?
    var seconds: Double = 0
    /// The app did not come back: recovered from a leftover marker at the next start.
    var killed = false
    /// After a killed run the app asks: "closed-by-itself", "froze" or "i-closed".
    var userSaid: String?
    /// Memory limit at launch (phys_footprint + available), for a likely memory kill.
    var limitMB: Int?
}

struct AutoTuneReport: Codable, Equatable {
    /// "crashed", "killed", "hung", "exited-early" (failures); "ran", "quit" (it played);
    /// "inconclusive" (never started, or the user closed the app).
    var outcome: String
    var stage: Int
    var seconds: Double
    var presents: Int
    var peakMB: Int?
    var dock: Bool
    var d3d12: Bool
    var ramMB: Int?
    /// Ids of the rules whose signals this session showed, best first.
    var matched: [String]
    var evidence: [String]
    /// The game's own error boxes (AutoTuneLogFacts.dialogs).
    var dialogs: [String] = []
    /// The Madeira build the session ran ("[build] v0.1.N").
    var build: Int?
    /// The session failed and a .NET Framework program in it found no Wine Mono
    /// (AutoTuneLogFacts.monoMissing). Optional, so that a saved state without it still loads.
    var monoMissing: Bool?
    /// The largest image that got no JIT-pool copy, in MiB (AutoTuneLogFacts.jitImageFailMB). Optional, so that a saved
    /// state without it still loads.
    var jitImageFailMB: Int?
    /// The main program's image in MiB (AutoTuneLogFacts.fixedExeMB). Optional, so that a saved state without it still loads.
    var fixedExeMB: Int?
    /// The last footprint line as a percentage of the memory limit (AutoTuneRule.memoryMinPct); nil when the log has no footprint line or
    /// neither the marker nor the log names the limit. Optional, so that a saved state without it still loads.
    var memoryPct: Int?
    /// madeira-bcd (round 624): the free storage in whole GB at the session's start (AutoTuneLogFacts.freeDiskGB); a swap attempt is sized to
    /// it. Optional, so that a saved state without it still loads.
    var freeDiskGB: Int?

    var failed: Bool { ["crashed", "killed", "hung", "exited-early"].contains(outcome) }
    var played: Bool { outcome == "ran" || outcome == "quit" }
}

enum AutoTuneEngine {
    static let maxAttempts = 8

    static func outcome(_ end: AutoTuneSessionEnd, _ f: AutoTuneLogFacts) -> String {
        // madeira-bcd (round 579): an app run that only ran the one-time installs (Dock, DockInstallers.installsOnly), and whose
        // batch ended, ran no game: BCD closes it for a restart (the next start asked what happened, the user said "closed by
        // itself", and it was scored "killed ... matched swap-8gb-memory-kill": GTA V Legacy NM88NQ), or an installer's helper
        // failed (Saints Row: The Third's DirectX setup, T3P8HK, scored "crashed" and given exe-window-mb = 512 for the game).
        // Nothing to learn about the game's settings.
        if f.installsOver { return "inconclusive" }
        // madeira-bcd (round 624, E1): in a Dock session an error box after the game's steam_api found no running Steam is a failure at any
        // stage (Portal 2 HZE5E9: "Engine Error: Steam is not running", a black window presenting behind it, scored "ran"). Dock only: the
        // Rockstar launcher's own probe of an Epic start prints the same line and an "Error" box and is left as it was (VX8ZZ0, YZZFYA, PA6PEB).
        // madeira-bcd (round 624, agent R S29, AX6XNN): the Rockstar Launcher said it failed (LAUNCHER_ERR_..., 113 in all 58 archive
        // logs) and no game frame came: below stage 4, or the Dock's frame counter stayed at 0 (its window presenting is no game).
        let launcherFailed = f.launcherErrors > 0 && (f.stage < 4 || (f.presentsCounted == true && f.presentsLogged == 0))
        // madeira-bcd (round 624, RETRO-2 RQ3, Ghost of Tsushima JKJA9Q): a program of the session that ended with an error before the first frame
        // (crs-video.exe, c0000135), in a session that went on to draw and reached stage 4, is a helper, not the game's crash. An error exit after
        // a frame (the game itself failing) still counts, and so do an unhandled exception, a runtime-error box and an exit report.
        let helperExitOnly = f.errorExits > 0 && f.frameSeen && f.stage >= 4 && !f.lastErrorExitAfterFrame
        let errorExitsEff = helperExitOnly ? 0 : f.errorExits
        let crashedInLog = f.unhandled > 0 || f.runtimeErrorDialog || f.exitStatus != nil || errorExitsEff > 0
            || (!f.dialogs.isEmpty && f.stage < 4) || (f.dock && f.steamNotFound && !f.dialogs.isEmpty) || launcherFailed
        // madeira-bcd (round 624, Kerbal Space Program 2G3Q3V): the game drew 3474 frames, then the Dock's counter stayed at that number for
        // 5 minutes while a thread waited for a FEX lock it owned; the player closed the frozen game, and "quit after playing" (and "ran") hid
        // the freeze from every rule. As it stood at the quit, when the player asked to quit.
        // madeira-bcd (round 624, RETRO-1 R4, WRC 9 JDJ8KE/WV1P7T): a [spin] report with the frame counter standing still for 60 s is the same
        // freeze; the player pressed Close 75-80 s into the frozen picture and "quit" hid it.
        let frozeLocked = f.quitRequested ? (f.lockFreezeAtQuit || f.spinFreezeAtQuit) : (f.lockFreeze || f.spinFreeze)
        if end.killed {
            // madeira-bcd (round 624, Cult of the Lamb VHRFNE): "I closed it" after a minute of nothing on screen, with a thread waiting
            // for a BCD lock it owns itself ("[fexlock] STUCK write-wait") and no frame ever drawn, is not a player leaving a game that
            // works: keep the evidence (the Dock's frame counter is there, it stayed at 0, nothing crashed or was quit).
            let frozenWithoutFrames = f.fexlockStuck > 0 && f.presentsCounted == true && f.presentsLogged == 0 && !crashedInLog && !f.quitRequested
            if end.userSaid == "i-closed" { return frozenWithoutFrames || (frozeLocked && !crashedInLog) ? "hung" : "inconclusive" }
            // madeira-bcd (round 591): the user asked a game that was playing to close ("graceful close requested" after the Dock counted frames
            // for 20 s or more), and the app was killed before it came back to the library: the game played, and the end is the app's, not a hang
            // (Cult of the Lamb T0K6FH at stage 5 with 8,296 frames was scored "hung" and its next start was spent on a retry; MGS Peace Walker
            // NMYVVM likewise). Not when the user said the app froze, when the app saw a freeze or a GPU error, or when the game had crashed before the
            // quit.
            let quitAfterPlay = f.quitRequested && !f.returnedToLibrary && f.stage >= 4 && f.presentsCounted == true && f.presentsLogged >= 1
                && f.freezeGaps == 0 && f.gpuErrors == 0 && (f.crashEvidenceAtQuit ?? 0) == 0 && !frozeLocked
            if end.userSaid != "froze" && quitAfterPlay { return "quit" }
            // madeira-bcd (round 624, Cloudpunk F88RB4): the player answered "it froze", but the app saw no freeze, no GPU error, no crash and no
            // quit, and the last footprint line is at the memory limit (4094 of 4095 MB 16 s after the start): iOS closed the game for memory while
            // it looked frozen to the player. Scored "hung", the swap rules never ran. A real freeze the app saw keeps "hung" below.
            if end.userSaid == "froze", f.freezeGaps == 0, f.gpuErrors == 0, !crashedInLog, !(f.quitRequested && !f.returnedToLibrary),
               let last = f.lastFootprintMB, let limit = end.limitMB ?? f.limitMB, last >= limit - max(150, limit / 50) { return "killed" }
            // madeira-bcd (round 624, Metro 2033 Redux Y1H618 and CTJQ9D on 4096 MB phones): the footprint jumped by 1.5 GB in 0.35 s before the
            // first frame and the app was gone, at 82-88 % of the limit (the last fast line; the slow one is 2 s behind). The player's "it
            // froze" scored it "hung" with no rule. Only below stage 4, with no freeze, GPU error, crash or quit, the newest footprint line the
            // fast sampler's at 80 % of the limit or more and 1.4 times the last slow line (a burst, not a plateau): 5 of 2,270 archive logs,
            // of which only these two change (the other three were already "killed").
            if f.stage <= 3, f.freezeGaps == 0, f.gpuErrors == 0, !f.quitRequested, !crashedInLog,
               let last = f.lastFootprintMB, let slow = f.lastSlowFootprintMB, f.lastFootprintFast,
               let limit = end.limitMB ?? f.limitMB, limit >= 2048,
               last * 100 >= limit * 80, last * 10 >= slow * 14 { return "killed" }
            if end.userSaid == "froze" || f.freezeGaps > 0 || f.gpuErrors > 0 || frozeLocked
                || (f.quitRequested && !f.returnedToLibrary) { return "hung" }
            // madeira-bcd 578 (lead, from ajan6-mm): a session killed at the memory limit whose only crash evidence is
            // a helper program's error exit (Miles Morales' crs-video.exe, c0000135) is a memory kill, not a crash:
            // otherwise the swap rules can never fire for such a game (V4JGSJ, VWKPTS ended at 8154 of 8192 MB).
            let onlyHelperExits = f.errorExits > 0 && f.unhandled == 0 && !f.runtimeErrorDialog && f.exitStatus == nil
                && (f.dialogs.isEmpty || f.stage >= 4)
            if onlyHelperExits, let last = f.lastFootprintMB, let limit = end.limitMB ?? f.limitMB,
               last >= limit - max(150, limit / 50) { return "killed" }
            if crashedInLog { return "crashed" }
            // The limit the marker measured, else the memory left at launch plus the app's own
            // share then; the last footprint line can be 2 s behind the kill, hence a margin.
            // Kept narrow on purpose: a kill missed here is asked about, a session wrongly
            // called a memory kill would steer the search (HFW 2026-10-13 17:10 ended at
            // 7932 MB of about 8317 and had played).
            if let last = f.lastFootprintMB {
                // The app's marker first, else the limit the log names (ml992), else the guess below.
                let known = end.limitMB ?? f.limitMB
                if let limit = known, last >= limit - max(150, limit / 50) { return "killed" }
                if known == nil, let avail = f.availableMB {
                    let limit = avail + 256
                    if last >= limit - max(200, limit * 3 / 100) { return "killed" }
                }
            }
            // madeira-bcd (round 624, Fallout 4 0PT2YV and WRSNC8): the game played (frames for 20 s or more, 58 FPS), the player left BCD
            // (focus elsewhere) and iOS closed the app in the background ("partial=1" summary), far below the memory limit, with nothing crashed
            // or frozen. The player's "it closed by itself" is true, but the game is fine: scored "killed", auto-tune spent starts on it.
            // Round 624 (RETRO-2 RQ2, Batman: Arkham Knight E2NZQ3): without the focus line too; "partial=1" is written only when the app went to
            // the background, and the memory test below still keeps a kill at the limit a kill.
            let playedThenLeft = f.sessionPartial && f.frameSeen && f.stage >= 4 && (f.sessionPlaySeconds ?? 0) >= 20
                && f.freezeGaps == 0 && f.gpuErrors == 0 && !crashedInLog
            if playedThenLeft, let last = f.lastFootprintMB, let limit = end.limitMB ?? f.limitMB, last < limit * 9 / 10 { return "ran" }
            if end.userSaid == "closed-by-itself" { return "killed" }
            return "inconclusive"
        }
        // The app's crash status of a run whose Wine process ended with code 0 is a leftover thread's (cleanMainExit).
        let crashStatus = f.cleanMainExit ? nil : end.crashStatus
        if !end.sawProcess && crashStatus == nil { return "inconclusive" }
        // madeira-bcd (round 624, R.E.P.O. AJ7RNE): a thread waited for a FEX lock forever ("[fexlock] STUCK write-wait") and the Dock's frame
        // counter stayed at 0 for 6.5 minutes, yet the stage the clock raises (5 after five minutes) scored the session "ran, ok" and auto-tune
        // tried nothing. Only with the Dock's counter (a game that draws without it keeps the old rule, round 580) and no crash evidence.
        if f.fexlockStuck > 0 && f.presentsCounted == true && f.presentsLogged == 0 && crashStatus == nil && !crashedInLog { return "hung" }
        // madeira-bcd (round 624, Farming Simulator 22 on Discord): a host fault in a thread with no TEB that nothing handled, and the Dock's frame
        // counter at 0 for a session that stayed alive two minutes or more (695 s): the game's process stopped there; the clock's stage 5 scored it
        // "ran". A session that ended soon after such a fault (MGS Ground Zeroes PFDA0D, 73 s; The Swapper 6AJPF0) keeps "exited-early".
        if f.machFaultNoTeb > 0 && f.presentsCounted == true && f.presentsLogged == 0 && f.aliveSeconds >= 120 && crashStatus == nil
            && !crashedInLog && !f.quitRequested {
            return "hung"
        }
        // madeira-bcd (round 624, Kerbal Space Program 2G3Q3V): frames, then a counter that stood still for 90 s or more with a FEX lock held.
        if frozeLocked && crashStatus == nil && !crashedInLog { return "hung" }
        // The user quit a game that was on screen: whatever it logged on the way out, it played,
        // unless it had crashed before the user asked (the quit line's moment, when it is logged).
        if end.quitRequested && f.stage >= 3 && (f.crashEvidenceAtQuit ?? 0) == 0 && !(launcherFailed && f.launcherErrorBeforeQuit) { return "quit" }
        if crashStatus != nil || crashedInLog { return "crashed" }
        // madeira-bcd (round 624): a launcher's child that failed before the game ever presented (Control's control_dx11.exe, FGTGM8: the
        // launcher ended with code 0 and the child died in the game's audio start, 11 s in). Only here, after the killed and quit verdicts,
        // so that a memory kill or a quit is read as before; and only below stage 4, so that a child that played and then printed an
        // error exit is still "ran".
        if f.childrenPending && f.stage < 4 && (f.childUnhandled > 0 || f.childExitStatus != nil || f.childErrorExits > 0) { return "crashed" }
        if end.quitRequested { return "exited-early" }
        // Alive for two minutes is not playing when no frame was ever presented (No Man's Sky 4KAT5E sat 151 s at
        // stage 2 with presents=0): only a session that presented counts as having run.
        // madeira-bcd (round 577, lead): narrowed. Only the Dock's "detach-wait: presents=N" counts frames, so a session
        // without that counter (a local program, GOG) keeps the old rule; a session is "not run" only when the counter
        // was there, stayed at 0 and the game never reached the presenting stage (stage 3).
        // madeira-bcd (round 580, lead): a wider "no frames" rule was tried and left out: a game that draws without the
        // Dock's frame counter (OpenGL, GDI) would read as not run, and auto-tune would add lines to a game that plays.
        let noFrameProof = (f.presentsCounted ?? false) && f.presentsLogged == 0 && f.stage < 3
        // madeira-bcd (round 624, WRC 9 JDJ8KE/WV1P7T): alive for two minutes is not playing when the session's own summary says it played
        // 10 s or less after a few frames (32 frames, then the loading screen stayed for 100 s): "hung", not "ran". Of the 82 archive logs
        // alive 100 s or more with 15 s or less of play, only these two change.
        if f.stage <= 3, f.aliveSeconds >= 120, let play = f.sessionPlaySeconds, play <= 10, f.presentsLogged > 0 { return "hung" }
        return f.stage >= 4 || (f.aliveSeconds >= 120 && !noFrameProof) ? "ran" : "exited-early"
    }

    static func report(_ end: AutoTuneSessionEnd, _ scanner: AutoTuneLogScanner,
                       rules: [AutoTuneRule], signals: [AutoTuneSignal]) -> AutoTuneReport {
        let f = scanner.facts
        let out = outcome(end, f)
        var stage = f.stage
        let presents = max(end.presents ?? 0, f.presentsLogged)
        if presents >= 3000 { stage = max(stage, 5) }
        // madeira-bcd (round 591): how far the memory was used. The marker's limit first, else the log's own (ml992), else the memory left at
        // launch plus 256 MB (the guess outcome() makes); nothing when the log has no footprint line.
        var memoryPct: Int?
        if let last = f.lastFootprintMB, let limit = end.limitMB ?? f.limitMB ?? f.availableMB.map({ $0 + 256 }), limit > 0 {
            memoryPct = last * 100 / limit
        }
        let r = AutoTuneReport(outcome: out, stage: stage, seconds: max(end.seconds, Double(f.aliveSeconds)),
                               presents: presents, peakMB: f.peakFootprintMB, dock: f.dock,
                               d3d12: f.d3d12, ramMB: f.ramMB, matched: [], evidence: scanner.evidence,
                               dialogs: f.dialogs, build: f.build, jitImageFailMB: f.jitImageFailMB, fixedExeMB: f.fixedExeMB,
                               memoryPct: memoryPct, freeDiskGB: f.freeDiskGB)
        var report = r
        if f.monoMissing && report.failed { report.monoMissing = true }
        report.matched = rules.filter { matches($0, report: r, counts: scanner.counts, signals: signals) }
            .sorted { ($0.priority, $0.id) < ($1.priority, $1.id) }
            .map(\.id)
        // madeira-bcd (round 580): a run that did not get far, with no crash, no quit and no sign of any known failure, whose log ends
        // with the user away (the app inactive or the pointer off the game view), is the user leaving: it says nothing about the game's
        // settings, and as a failure it would send a line that was being tried back (Final Fantasy Tactics 7QVF78: 19 s, "app
        // inactive", judged against a memory kill) or leave the game "stuck". A session that matched a rule keeps its verdict: the
        // rule's signals are in the log whoever left.
        if report.outcome == "exited-early", f.focusLeft, !end.quitRequested, report.matched.isEmpty {
            report.outcome = "inconclusive"
        }
        return report
    }

    /// Every signal a rule set needs, deduplicated, in a stable order.
    static func signals(_ rules: [AutoTuneRule]) -> [AutoTuneSignal] {
        var seen = Set<AutoTuneSignal>(), list: [AutoTuneSignal] = []
        for r in rules {
            for s in r.all + (r.any ?? []) + (r.none ?? []) {
                let plain = AutoTuneSignal(has: s.has)
                if seen.insert(plain).inserted { list.append(plain) }
            }
        }
        return list
    }

    static func matches(_ rule: AutoTuneRule, report: AutoTuneReport, counts: [Int],
                        signals: [AutoTuneSignal]) -> Bool {
        func seen(_ s: AutoTuneSignal) -> Bool {
            guard let i = signals.firstIndex(of: AutoTuneSignal(has: s.has)) else { return false }
            return counts[i] >= (s.count ?? 1)
        }
        // madeira-bcd (round 624, RETRO-2 RQ1): a noFix rule marked evaluateWhenRan also speaks for a session scored ran/quit.
        let ranRule = rule.evaluateWhenRan == true && rule.noFix == true && report.played
        guard report.failed || ranRule else { return false }
        if rule.ranOnly == true && !ranRule { return false }
        if !ranRule, let o = rule.outcomes, !o.contains(report.outcome) { return false }
        if let s = rule.session, s != (report.dock ? "dock" : "library") { return false }
        if let a = rule.api, a != (report.d3d12 ? "d3d12" : "d3d11") { return false }
        if let lo = rule.ramMinMB, (report.ramMB ?? 0) < lo { return false }
        if let hi = rule.ramMaxMB, (report.ramMB ?? Int.max) > hi { return false }
        if let lo = rule.minStage, report.stage < lo { return false }
        if let hi = rule.maxStage, report.stage > hi { return false }
        if let hi = rule.imageMaxMB, let image = report.jitImageFailMB, image > hi { return false }
        if let hi = rule.exeMaxMB, let exe = report.fixedExeMB, exe > hi { return false }
        if let lo = rule.exeMinMB, (report.fixedExeMB ?? 0) < lo { return false }
        if let lo = rule.memoryMinPct, let pct = report.memoryPct, pct < lo { return false }
        guard !rule.all.isEmpty, rule.all.allSatisfy(seen) else { return false }
        if let any = rule.any, !any.isEmpty, !any.contains(where: seen) { return false }
        if let none = rule.none, none.contains(where: seen) { return false }
        return true
    }

    /// Did `now` get further than `best`? `tried`: the rule whose lines this session added.
    static func better(_ now: AutoTuneReport, than best: AutoTuneReport, tried: String? = nil) -> Bool {
        if now.played != best.played { return now.played }
        // madeira-bcd (round 580): the JIT pool refusing an image is a failure at load time. A start that has no such line any more, whose
        // modules loaded (stage 2 at least) and that stopped in the game's own code got further than one that could not load, whatever the
        // stage counter and the clock say: the failing load leaves the game's error box on screen until the user closes it (RAIDOU
        // Remastered, B07FN7 without the line: 384 MiB refused, 60 s), while the start that loaded stops in the game's code after 21 s
        // (DCXTB5 with env.MADEIRA_X64_IMAGE_NOCOPY = 1, the same stage): the line was reverted and the next start failed to load again.
        if let tried, now.failed, best.failed, best.matched.contains(tried), !now.matched.contains(tried),
           best.jitImageFailMB != nil, now.jitImageFailMB == nil, now.stage >= 2 { return true }
        if now.stage != best.stage { return now.stage > best.stage }
        // Same stage: a longer life by half again, and at least 10 s, counts.
        if now.seconds >= best.seconds * 1.5 && now.seconds - best.seconds >= 10 { return true }
        // Same stage, still failing, but the failure the line was for is gone from the log:
        // the line did its job and the game now stops on something else.
        if let tried, now.failed, best.failed, best.matched.contains(tried), !now.matched.contains(tried) { return true }
        return false
    }
}

// MARK: - One game's search

struct AutoTuneAttempt: Codable, Equatable {
    var date: Date
    /// The lines this session ran with on top of the kept ones (empty: the baseline or the retry).
    var lines: [String]
    var rule: String?
    var outcome: String
    var stage: Int
    var seconds: Double
    var presents: Int
    /// nil while undecided or for an inconclusive session.
    var kept: Bool?
    var log: String?
}

struct AutoTuneState: Codable, Equatable {
    var version = 1
    /// "baseline" (play once), "trying", "found" (it plays with the kept lines),
    /// "ok" (it played with nothing added), "stuck" (no known fix fits), "gave-up",
    /// "known" (a crash only a change in Madeira fixes: a rule with fixedIn).
    var status = "baseline"
    var kept: [String] = []
    var candidate: [String]?
    var candidateRule: String?
    var tried: [String] = []
    var retried = false
    var best: AutoTuneReport?
    var attempts: [AutoTuneAttempt] = []
    /// The game's own settings when the baseline ran; a change starts a new baseline.
    var fingerprint: String?
    /// status "known": the rule (one with fixedIn) that named a failure only Madeira can fix,
    /// its reason, the build that fixed it and the build the session ran.
    var known: String?
    var knownReason: String?
    var knownFixedIn: Int?
    var knownBuild: Int?
    /// status "known" from a rule with noFix: no build is named, and the advice says no setting changes it. Optional: a state saved
    /// before it existed still loads.
    var knownNoFix: Bool?
    /// The last session that reached a verdict failed with a .NET program finding no Wine Mono
    /// (AutoTuneReport.monoMissing); a later one that did not clears it. The game page says where
    /// to download Wine Mono, while this device has none. Optional: a state saved before it existed
    /// still loads, and a game without the problem keeps the same file as before.
    var monoMissing: Bool?
    /// madeira-bcd (round 624, agent D3 S5-3): a swap attempt was left out because the device had this many whole GB free, too little
    /// (AutoTuneEngine.swapRoomMB). Set only with the status "stuck". Optional: a state saved before it existed still loads.
    var lowDiskGB: Int?
    /// madeira-bcd (round 624, agent E1 S5b): with the status "stuck", each matched rule that was passed over and why ("rule (tried)"),
    /// for the log. Optional: a state saved before it existed still loads.
    var skippedNote: String?

    /// Why no swap file was tried, for "stuck" (nil unless an attempt was left out for lack of room).
    var lowDiskNote: String? {
        lowDiskGB.map { "A swap file would help this game, but it needs about 3 GB of free storage and this device has \($0) GB free. "
            + "Free some space and play again." }
    }

    /// What the search tried, for "no known fix" (the rule ids, and the unchanged retry).
    var triedSummary: String {
        var list = tried
        if retried { list.insert("the same settings once more", at: 0) }
        return list.isEmpty ? "nothing: no rule matched" : list.joined(separator: ", ")
    }

    /// The words that start the advice for a "known" failure: a crash BCD fixed in a build, or one no setting and no known build changes
    /// (Valve's copy protection refusing the game's program, an open bug of BCD's).
    var knownLead: String { knownNoFix == true ? "No setting changes this" : "A crash in BCD itself, not a setting" }
    /// What to tell the user about a "known" failure.
    var knownAdvice: String {
        var text = "\(knownLead): \(knownReason ?? known ?? "")"
        if knownNoFix != true, let fixed = knownFixedIn {
            if let ran = knownBuild, ran >= fixed {
                text += " Build \(fixed) fixed this crash, yet build \(ran) still showed it: export the report to send it."
            } else {
                text += " Fixed in build \(fixed): update BCD."
            }
        }
        return text
    }

    /// The layer file's lines for the next session: a candidate's key replaces a kept one.
    var layerLines: [String] { AutoTuneState.merge(kept, candidate ?? []) }
    static func merge(_ kept: [String], _ add: [String]) -> [String] {
        let keys = Set(add.map(AutoTuneRules.key))
        return kept.filter { !keys.contains(AutoTuneRules.key($0)) } + add
    }
    var decided: Int { attempts.filter { $0.kept != nil && $0.rule != nil }.count }
}

extension AutoTuneEngine {
    /// A rule's lines as the attempt writes them. "{exeWindowMB}" is the window a fixed-base program needs: its size (the image's MiB,
    /// rounded down, as the scanner reads it) plus 17 and then up to a multiple of 16 (575 MiB gives 592, 379 gives 400), so the
    /// image and 16 MiB or more fit (exe-window-fixed-base-large). Without a main program in the report the line is left as it is.
    static func resolve(_ lines: [String], report: AutoTuneReport) -> [String] {
        var out = lines
        if let exe = report.fixedExeMB {
            let window = (exe + 17 + 15) / 16 * 16
            out = out.map { $0.replacingOccurrences(of: "{exeWindowMB}", with: String(window)) }
        }
        // madeira-bcd (round 624, agent D3 S5-3): a swap file is sized to the room the device has, never more than it can hold (swapRoomMB).
        if let free = report.freeDiskGB {
            out = out.map { line in
                guard AutoTuneRules.key(line) == "swap-mb", let want = Int(AutoTuneRules.value(line)) else { return line }
                return "swap-mb = \(min(want, swapRoomMB(freeGB: free)))"
            }
        }
        return out
    }

    /// madeira-bcd (round 624, Sekiro XJ8YM3 and Cloudpunk F88RB4, both with 2 GB free and a 3072 MB swap file in the attempt): the most a swap
    /// file may take of `freeGB` whole GB of free storage: all but 1.5 GB (saves, shader caches, iOS itself); 0 when less than 1 GB would be left
    /// for the file, since a swap file that small moves too little to change a memory kill.
    static func swapRoomMB(freeGB: Int) -> Int {
        let room = freeGB * 1024 - 1536
        return room < 1024 ? 0 : room
    }

    /// One session ended: decide on the line it tried and pick the next one.
    /// `userKeys`: keys the game's own file, the entry's lines or bcd.cfg set (never proposed).
    static func advance(_ state: AutoTuneState, report: AutoTuneReport, rules: [AutoTuneRule],
                        userKeys: Set<String>, fingerprint: String, log: String?, now: Date = Date(),
                        listValues: [String: String] = [:]) -> AutoTuneState {
        var s = state
        s.skippedNote = nil   // madeira-bcd (round 624, E1 S5b): this session's own, set below only for a "stuck" verdict
        if s.fingerprint != fingerprint {
            // The user changed the game's settings: what follows is compared with a new baseline.
            s.fingerprint = fingerprint
            s.best = nil
            s.candidate = nil
            s.candidateRule = nil
            if s.status != "baseline" { s.status = "baseline" }
        }
        var attempt = AutoTuneAttempt(date: now, lines: s.candidate ?? [], rule: s.candidateRule,
                                      outcome: report.outcome, stage: report.stage, seconds: report.seconds,
                                      presents: report.presents, kept: nil, log: log)
        if report.outcome == "inconclusive" {
            s.attempts.append(attempt)
            return s   // the same lines run once more
        }
        s.monoMissing = report.monoMissing == true ? true : nil
        if let best = s.best, s.candidateRule != nil {
            let keep = better(report, than: best, tried: s.candidateRule)
            attempt.kept = keep
            if keep {
                s.kept = AutoTuneState.merge(s.kept, s.candidate ?? [])
                s.best = report
            }
            s.candidate = nil
            s.candidateRule = nil
        } else {
            s.best = report
        }
        s.attempts.append(attempt)

        // madeira-bcd (round 624, RETRO-2 RQ1): a session that looked played, with nothing kept, whose log carries the signals of a noFix rule
        // marked evaluateWhenRan: say what it was ("known") instead of "it played without changes". Nothing is tried.
        if report.played, s.kept.isEmpty,
           let rule = report.matched.lazy.compactMap({ id in rules.first { $0.id == id } }).first(where: { $0.evaluateWhenRan == true && $0.noFix == true }) {
            s.status = "known"
            s.known = rule.id
            s.knownReason = rule.reason
            s.knownFixedIn = nil
            s.knownNoFix = true
            s.knownBuild = report.build
            s.candidate = nil
            s.candidateRule = nil
            return s
        }
        if report.played {
            s.status = s.kept.isEmpty ? "ok" : "found"
            return s
        }
        // The best match is a crash only a change in Madeira fixes: no line can help, so no
        // attempt (not even the unchanged retry) is spent on it.
        if let rule = report.matched.lazy.compactMap({ id in rules.first { $0.id == id } }).first,
           rule.fixedIn != nil || rule.noFix == true {
            s.status = "known"
            s.known = rule.id
            s.knownReason = rule.reason
            s.knownFixedIn = rule.fixedIn
            s.knownNoFix = rule.noFix == true ? true : nil
            s.knownBuild = report.build
            s.candidate = nil
            s.candidateRule = nil
            return s
        }
        if s.decided >= maxAttempts {
            s.status = "gave-up"
            return s
        }
        // A failure the first time round may be the first start after an update
        // (four such failures in the history cleared on the second start): run once unchanged.
        // Not for a failure that comes back the same every time (a rule with noRetry): Counter-Strike 2
        // filled the JIT pool on both starts, and the line that helped came only on the third.
        let firm = report.matched.contains { id in rules.first { $0.id == id }?.noRetry == true }
        if !s.retried && s.kept.isEmpty && s.tried.isEmpty && !firm {
            s.retried = true
            s.candidate = []
            s.candidateRule = "retry"
            s.status = "trying"
            return s
        }
        let keptKeys = Set(s.kept.map(AutoTuneRules.key))
        // madeira-bcd (round 580): a rule that adds the very lines an earlier try of another rule added is not a new try (the NOCOPY line
        // belongs to x64-image-nocopy, x64-image-smc-nocopy and x64-image-rwx-protector-nocopy; the plain-RWX line to two more): when that
        // earlier try was reverted the same start would only be spent again.
        let triedLines = s.tried.compactMap { id in rules.first { $0.id == id }?.lines }
        var noRoom: Int?
        // madeira-bcd (round 624, agent E1 S5b; Supermarket Together 45T9YP, Hogwarts Legacy GBQRKH): why each matched rule was passed
        // over, for the [autotune] line of a "stuck" verdict (text only: the choice below is the same).
        var skipped: [String] = []
        let next = report.matched.compactMap { id in rules.first { $0.id == id } }.first { rule in
            func skip(_ why: String) -> Bool { skipped.append("\(rule.id) (\(why))"); return false }
            if rule.fixedIn != nil || rule.noFix == true { return skip("no line fixes it") }
            // madeira-bcd (round 624, agent D3 S5-3): a swap attempt the device has no room for is no attempt (swapRoomMB); one that would not
            // raise the swap file auto-tune already kept is none either.
            if let free = report.freeDiskGB,
               let swap = rule.lines.first(where: { AutoTuneRules.key($0) == "swap-mb" }).flatMap({ Int(AutoTuneRules.value($0)) }) {
                let room = swapRoomMB(freeGB: free)
                let have = s.kept.first(where: { AutoTuneRules.key($0) == "swap-mb" }).flatMap { Int(AutoTuneRules.value($0)) } ?? 0
                if room == 0 || (rule.upgrade == true && min(swap, room) <= have) {
                    noRoom = free
                    return skip(room == 0 ? "no storage for a swap file" : "no larger swap file fits")
                }
            }
            let keys = rule.lines.map(AutoTuneRules.key)
            if s.tried.contains(rule.id) { return skip("tried") }
            if triedLines.contains(rule.lines) { return skip("its lines were tried") }
            if keys.contains(where: userKeys.contains) { return skip("a key you set") }
            // madeira-bcd (round 591): the recommended list's own lines in the game's file are as good as the user's, except for a rule that is
            // marked as an upgrade of them, after a memory kill, and only with a larger number (GTA V Enhanced's list swap-mb = 3072 kept the
            // 12 GB tier's swap-mb = 6144 out, A24ES3). A line equal to the list's is already in force; a rule with nothing new is no attempt.
            if !listValues.isEmpty {
                if rule.listUpgrade == true && (report.outcome == "killed"
                    || (rule.listUpgradeFailure == true && (report.outcome == "crashed" || report.outcome == "exited-early"))) {
                    var changes = false
                    for line in resolve(rule.lines, report: report) {   // round 625: "{exeWindowMB}" as the attempt would write it
                        guard let had = listValues[AutoTuneRules.key(line)] else { changes = true; continue }
                        let value = AutoTuneRules.value(line)
                        if value == had { continue }
                        guard let new = Int(value), let old = Int(had), new > old else { return skip("the recommended list's value is larger") }
                        changes = true
                    }
                    if !changes { return skip("the recommended list has its lines") }
                } else if keys.contains(where: { listValues[$0] != nil }) {
                    return skip("a key of the recommended list")   // any other rule or verdict: the list's keys are the user's, as before
                }
            }
            // An upgrade raises what auto-tune kept; anything else adds only new keys.
            if rule.upgrade == true {
                return keys.contains(where: keptKeys.contains) ? true : skip("nothing kept to raise")
            }
            return !keys.contains(where: keptKeys.contains) ? true : skip("a key auto-tune kept")
        }
        guard let rule = next else {
            s.status = "stuck"
            s.lowDiskGB = noRoom
            s.skippedNote = skipped.isEmpty ? nil : skipped.joined(separator: ", ")
            return s
        }
        s.lowDiskGB = nil
        s.tried.append(rule.id)
        s.candidate = resolve(rule.lines, report: report)
        s.candidateRule = rule.id
        s.status = "trying"
        return s
    }
}
