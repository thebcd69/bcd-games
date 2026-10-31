// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
// The session summary, the open-session marker's rules and the screenshot rules, compiled on the host by tests/host/check-bcd-session-record.py
// with app/Madeira/BCDSessionCore.swift.

import Foundation
import CoreGraphics

var failures = 0
func check(_ what: String, _ ok: Bool, _ detail: String = "") {
    print((ok ? "ok   " : "FAIL ") + what + (ok || detail.isEmpty ? "" : "\n     " + detail))
    if !ok { failures += 1 }
}

func sample() -> BCDSessionSummary {
    var s = BCDSessionSummary()
    s.game = "Hollow Knight"
    s.lengthSeconds = 1234
    s.playSeconds = 1180.4
    s.frames = 71234
    s.activeSeconds = 1213.2
    s.p99Ms = 24.3
    s.p999Ms = 44.4
    s.worstMs = 215
    s.stalls = 2
    s.stallSeconds = 14.2
    s.peakMB = 3412
    s.limitMB = 5800
    s.thermalPeak = 1
    s.lowPowerSeen = false
    s.api = "D3D11 64"
    s.renderSize = "1408x648"
    s.upscale = "2"
    s.cap = 60
    return s
}

@main enum Run {
    static func main() {
        // ---- the summary line ---------------------------------------------------------------------------------
        let line = sample().line()
        print("note: " + line)
        check("the line is one line with the [session] tag first", line.hasPrefix("[session] length=1234s ") && !line.contains("\n"))
        check("it carries the numbers: play, frames, average, the two lows, the worst frame, the stalls, the peak, the heat, the pipeline",
              ["play=1180s", "frames=71234", "avg=58.7", "low1=41.2", "low01=22.5", "worst=215ms", "stalls=2/14s", "peak=3412MB", "limit=5800MB",
               "thermal=fair", "lpm=0", "api=D3D11/64", "res=1408x648", "mfx=2", "cap=60"].allSatisfy { line.contains(" " + $0 + " ") },
              line)
        check("the game's name is last and may have spaces", line.hasSuffix(" game=Hollow Knight"))
        check("the average is frames over the time between stalls (71234 / 1213.2 s = 58.7)", abs((sample().average ?? 0) - 58.72) < 0.01)
        check("1% low is the frame rate of the 99th percentile frame (24.3 ms is 41.2 FPS); 0.1% of the 99.9th (44.4 ms is 22.5 FPS)",
              abs((sample().low1 ?? 0) - 41.15) < 0.01 && abs((sample().low01 ?? 0) - 22.52) < 0.01)
        var few = sample(); few.frames = 60
        check("under 100 frames there is no 1% low, under 1000 no 0.1% low", few.low1 == nil && few.low01 == nil && few.average != nil)
        var some = sample(); some.frames = 500
        check("500 frames: a 1% low, no 0.1% low", some.low1 != nil && some.low01 == nil)
        var none = BCDSessionSummary(); none.game = "Nothing"; none.lengthSeconds = 5
        let empty = none.line()
        check("a session with no frame says so: frames=0 and no average or lows", empty.contains(" frames=0 ") && !empty.contains("avg=") && !empty.contains("low1=")
              && !empty.contains("worst=") && empty.contains("stalls=0/0s"), empty)
        var partial = sample(); partial.partial = true
        check("a partial line (the app went to the background) says so right after the length", partial.line().hasPrefix("[session] length=1234s partial=1 play="))
        var hot = sample(); hot.thermalPeak = 3; hot.lowPowerSeen = true
        check("the heat reached and Low Power Mode are written", hot.line().contains(" thermal=critical ") && hot.line().contains(" lpm=1 "))
        var odd = sample(); odd.game = "Two\nLines"; odd.api = "D3D9 / D3D11 32"
        check("a name with a newline stays on one line; the API has no spaces", !odd.line().contains("\n") && odd.line().contains("api=D3D9/"), odd.line())
        var weird = sample(); weird.thermalPeak = 99
        check("a heat level out of range is the last word, not a crash", weird.line().contains("thermal=critical"))

        // ---- reading it back -------------------------------------------------------------------------------------
        let back = BCDSessionSummary.fields(line)
        check("the fields read back as written", back?["length"] == "1234s" && back?["avg"] == "58.7" && back?["low1"] == "41.2" && back?["stalls"] == "2/14s"
              && back?["api"] == "D3D11/64" && back?["game"] == "Hollow Knight", "\(String(describing: back))")
        check("a line with a time stamp in front reads too", BCDSessionSummary.fields("[21:05:03.123] " + line)?["frames"] == "71234")
        check("another line is not a summary", BCDSessionSummary.fields("[session] the last session of X did not end cleanly") == nil
              && BCDSessionSummary.fields("[present] count=3") == nil && BCDSessionSummary.fields("") == nil)
        check("the words a person reads", sample().words == "59 FPS on average, 41 for the slowest 1%, 20 minutes", sample().words)

        // ---- the open-session marker and the question ----------------------------------------------------------
        let now = 1_800_000_000.0
        func marker(background: Bool = false, startedAgo: Double = 1500, changedAgo: Double = 200, title: String = "Hollow Knight") -> BCDSessionMarker {
            BCDSessionMarker(title: title, program: "hollow_knight.exe", startedAt: now - startedAgo, build: "603", logInode: 77, background: background, changedAt: now - changedAgo)
        }
        let foreground = BCDCrashRules.offer(marker: marker(), endedAt: now - 60, now: now, enabled: true)
        check("a marker found at start becomes one question", foreground != nil && foreground?.title == "Hollow Knight")
        check("... that says how long the game ran, to the log's last line: 24 minutes", foreground?.minutes == 24, "\(String(describing: foreground?.minutes))")
        check("... in front: 'BCD closed while Hollow Knight was running after about 24 minutes.'",
              foreground?.message.hasPrefix("BCD closed while Hollow Knight was running after about 24 minutes.") == true, foreground?.message ?? "")
        let background = BCDCrashRules.offer(marker: marker(background: true, changedAgo: 400), endedAt: now - 10, now: now, enabled: true)
        check("in the background it says so, and counts the play to the moment the app left the front (18 minutes)",
              background?.background == true && background?.minutes == 18 && background?.message.contains("closed while it was in the background") == true, background?.message ?? "")
        check("it tells what sending does: the log is cleaned on the device, a comment may be added",
              foreground?.message.contains("add a comment") == true && foreground?.message.contains("takes out folder paths, user names, e-mail and IP addresses") == true)
        check("no marker, no question", BCDCrashRules.offer(marker: nil, endedAt: nil, now: now, enabled: true) == nil)
        check("turned off, no question", BCDCrashRules.offer(marker: marker(), endedAt: now - 60, now: now, enabled: false) == nil)
        check("a marker older than a week is not asked about", BCDCrashRules.offer(marker: marker(startedAgo: 8 * 86400), endedAt: nil, now: now, enabled: true) == nil
              && BCDCrashRules.offer(marker: marker(startedAgo: 6 * 86400), endedAt: nil, now: now, enabled: true) != nil)
        check("a marker from the future (the clock was set back) is not asked about", BCDCrashRules.offer(marker: marker(startedAgo: -3600), endedAt: nil, now: now, enabled: true) == nil)
        check("a game with no name is 'a game'", BCDCrashRules.offer(marker: marker(title: "  "), endedAt: nil, now: now, enabled: true)?.title == "a game")
        check("a log last written before the session began counts no time", BCDCrashRules.offer(marker: marker(), endedAt: now - 99999, now: now, enabled: true)?.minutes == 0)
        check("a minute is 'minute', not 'minutes'", BCDCrashOffer(title: "X", minutes: 1, background: false).message.contains("after about 1 minute.")
              && BCDCrashOffer(title: "X", minutes: 0, background: false).message.hasPrefix("BCD closed while X was running."))
        // round 624 (agent N S14, FWXFQ1): a start that went to the background within seconds of Play
        check("a start left for the background after 3 s is not asked about",
              BCDCrashRules.offer(marker: marker(background: true, startedAgo: 100, changedAgo: 97), endedAt: nil, now: now, enabled: true) == nil)
        let left40 = BCDCrashRules.offer(marker: marker(background: true, startedAgo: 100, changedAgo: 60), endedAt: nil, now: now, enabled: true)
        check("... after 40 s it is, with the words of a start that was left",
              left40?.seconds == 40
              && left40?.message.hasPrefix("BCD went to the background 40 seconds after you pressed Play, while Hollow Knight was still starting.") == true
              && left40?.message.contains("Stay in BCD until the game's picture shows.") == true
              && left40?.message.contains("takes out folder paths, user names, e-mail and IP addresses") == true, left40?.message ?? "nil")
        let front3 = BCDCrashRules.offer(marker: marker(startedAgo: 100, changedAgo: 97), endedAt: now - 97, now: now, enabled: true)
        check("a 3-second session that closed in front is asked about, with the words it had",
              front3?.seconds == 3 && front3?.message.hasPrefix("BCD closed while Hollow Knight was running.") == true, front3?.message ?? "nil")
        let left20 = BCDCrashRules.offer(marker: marker(background: true, startedAgo: 100, changedAgo: 80), endedAt: nil, now: now, enabled: true)
        let left90 = BCDCrashRules.offer(marker: marker(background: true, startedAgo: 100, changedAgo: 10), endedAt: nil, now: now, enabled: true)
        check("exactly 20 s in the background is asked about; exactly 90 s has the background words it had",
              left20?.seconds == 20 && left20?.message.hasPrefix("BCD went to the background 20 seconds") == true
              && left90?.seconds == 90 && left90?.message.hasPrefix("BCD was closed while it was in the background and Hollow Knight was running after about 1 minute.") == true,
              "\(left20?.message ?? "nil") | \(left90?.message ?? "nil")")
        check("an offer made without its seconds keeps the words it had",
              BCDCrashOffer(title: "X", minutes: 0, background: true).message.hasPrefix("BCD was closed while it was in the background and X was running."))
        check("the [session] line says why nothing is asked: a start left within 20 s, turned off, too old",
              BCDCrashRules.notAsking(marker: marker(background: true, startedAgo: 100, changedAgo: 97), now: now, enabled: true) == "it went to the background within 20 s of the start"
              && BCDCrashRules.notAsking(marker: marker(), now: now, enabled: false) == "turned off"
              && BCDCrashRules.notAsking(marker: marker(startedAgo: 8 * 86400), now: now, enabled: true) == "too old")
        let data = try? JSONEncoder().encode(marker())
        check("the marker is a small JSON file that reads back",
              data.flatMap { try? JSONDecoder().decode(BCDSessionMarker.self, from: $0) } == marker() && (data?.count ?? 9999) < 400)

        // ---- the screenshot -------------------------------------------------------------------------------------
        check("a picture of a 932x430 view on a 3x screen is drawn at 2x (long edge 1864 px), a small one at the screen's scale",
              abs(BCDScreenshotRules.scale(for: CGSize(width: 932, height: 430), screenScale: 3) - 2000.0 / 932) < 0.001
              && BCDScreenshotRules.scale(for: CGSize(width: 480, height: 320), screenScale: 2) == 2)
        check("a huge view never draws above 2000 px on the long side", { let s = BCDScreenshotRules.scale(for: CGSize(width: 1366, height: 1024), screenScale: 2); return 1366 * s <= 2000.01 }()
              && BCDScreenshotRules.scale(for: .zero, screenScale: 3) == 1)
        check("the file name: the game, then the moment, .jpg",
              BCDScreenshotRules.fileName(game: "Hollow Knight", year: 2026, month: 10, day: 25, hour: 21, minute: 5, second: 3) == "Hollow Knight 2026-10-25 21.05.03.jpg")
        check("... cleaned of anything a file name should not hold, and short",
              BCDScreenshotRules.fileName(game: "../../etc/pass:wd\\*?", year: 2026, month: 1, day: 2, hour: 3, minute: 4, second: 5) == "etc pass wd 2026-01-02 03.04.05.jpg"
              && BCDScreenshotRules.fileName(game: "", year: 2026, month: 1, day: 2, hour: 3, minute: 4, second: 5) == "BCD 2026-01-02 03.04.05.jpg"
              && BCDScreenshotRules.fileName(game: String(repeating: "A", count: 90), year: 2026, month: 1, day: 2, hour: 3, minute: 4, second: 5).count == 40 + 1 + 19 + 4)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 3 * 3600)!   // UTC+3, no daylight saving
        let start = BCDScreenshotRules.start(ofLogNamed: "war3.exe-2026-10-16_22-24-07.txt", calendar: calendar)
        check("a session log's name has its start: 2026-10-16 22:24:07 local", start.map { Int($0.timeIntervalSince1970) } == 1_792_178_647, "\(String(describing: start))")
        check("bcd-log.txt has none", BCDScreenshotRules.start(ofLogNamed: "bcd-log.txt", calendar: calendar) == nil)
        let begin = Date(timeIntervalSince1970: 1_000_000), end = Date(timeIntervalSince1970: 1_003_600)
        check("a picture taken during the session belongs to its log (a minute either side)",
              BCDScreenshotRules.belongs(shot: Date(timeIntervalSince1970: 1_001_000), logStart: begin, logEnd: end)
              && BCDScreenshotRules.belongs(shot: Date(timeIntervalSince1970: 1_003_650), logStart: begin, logEnd: end)
              && BCDScreenshotRules.belongs(shot: Date(timeIntervalSince1970: 999_950), logStart: begin, logEnd: end))
        check("... one from the day before or after does not",
              !BCDScreenshotRules.belongs(shot: Date(timeIntervalSince1970: 900_000), logStart: begin, logEnd: end)
              && !BCDScreenshotRules.belongs(shot: Date(timeIntervalSince1970: 1_010_000), logStart: begin, logEnd: end))
        check("a log with no start in its name covers the 12 hours before its last line",
              BCDScreenshotRules.belongs(shot: end.addingTimeInterval(-3 * 3600), logStart: nil, logEnd: end)
              && !BCDScreenshotRules.belongs(shot: end.addingTimeInterval(-13 * 3600), logStart: nil, logEnd: end))
        check("a picture is sent as it is, and the words say so",
              BCDScreenshotRules.sentAsItIs.contains("sent as it is") && BCDScreenshotRules.sentAsItIs.contains("cannot take anything out of it"))
        check("the words say it is a small copy of about 100 KB", BCDScreenshotRules.sentAsItIs.contains("small copy") && BCDScreenshotRules.sentAsItIs.contains("100 KB"))
        check("the upload copy: 100 KB or less wanted, 1280 px down to 640 px, quality down to 0.4; the intake takes 160 KB; the kept copy keeps its 4 MB",
              BCDScreenshotRules.uploadTargetBytes == 100 * 1024 && BCDScreenshotRules.uploadStartEdge == 1280 && BCDScreenshotRules.uploadFloorEdge == 640
              && BCDScreenshotRules.uploadFloorQuality == 0.4 && BCDScreenshotRules.maxSendBytes == 160 * 1024
              && BCDScreenshotRules.keptMaxBytes == 4 << 20 && BCDScreenshotRules.uploadTargetBytes < BCDScreenshotRules.maxSendBytes)
        check("160 MB of free memory to draw one", BCDScreenshotRules.minFreeMB == 160)

        // ---- round 624 (agent R S5, RT83GE/6D6ZZS): the failed run's last lines in the new log ------------------------------------------
        let whole = (1...100).map { "line \($0)" }.joined(separator: "\n") + "\n"
        let fromStart = BCDPrevTail.lines(Data(whole.utf8), offset: 0)
        check("the last 60 lines of a file read from its start", fromStart.count == 60 && fromStart.first == "line 41" && fromStart.last == "line 100",
              "\(fromStart.first ?? "") .. \(fromStart.last ?? "")")
        let cut = BCDPrevTail.lines(Data(("ne 7\nline 8\nline 9\r\n").utf8), offset: 4096)
        check("a first line cut by a read from the middle is dropped, and a CR at a line end goes", cut == ["line 8", "line 9"], "\(cut)")
        let atBoundary = BCDPrevTail.lines(Data(("\nline 8\n").utf8), offset: 4096)
        check("...unless the read starts at a line break", atBoundary == ["line 8"], "\(atBoundary)")
        let long = BCDPrevTail.lines(Data((String(repeating: "x", count: 1000) + "\n").utf8), offset: 0)
        check("a line is cut to 400 characters", long.count == 1 && long[0].count == 400)
        check("an empty file gives nothing", BCDPrevTail.lines(Data(), offset: 0).isEmpty)
        check("the read is the last 64 KB", BCDPrevTail.maxBytes == 65536 && BCDPrevTail.maxLines == 60)

        print(failures == 0 ? "bcd_session_checks: PASS" : "bcd_session_checks: \(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }
}
