#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""What a session leaves behind (round 603): the [session] summary line, and the one question after a session that closed unexpectedly.

Behaviour (swiftc; --require-swift makes its absence a failure):
  - app/Madeira/BCDSessionCore.swift compiled with tests/host/bcd_session_checks.swift: the summary line (average, 1% and 0.1% lows from
    the 99th and 99.9th percentile frames, the worst frame, stalls kept apart, the peak, the heat reached, the API), reading it back,
    the marker rules (one question per marker, in front or in the background, never for an old or future marker, never when turned off),
    and the screenshot's rules (size, file name, which log a picture belongs to);
  - the line the app writes is read by the two readers the lead uses: tools/log-facts.py (its card says how the session ran) and the
    scanner behind `pull-logs.py --replay` (AutoTuneCore.swift: whole numbers; nothing decides by them).

Source (every host):
  1. The recorder reads the frame statistics once, at the end and when the app goes to the background; it samples nothing while the game
     runs (no timer; the heat is a notification, the memory peak is iOS's own ledger); the observers go with the session.
  2. The marker is written when a session starts, removed when it ends cleanly and before BCD closes on purpose, kept in Application
     Support, and removed at the next start BEFORE it is read: one question per session whatever happens after.
  3. The question: never while a game starts or runs, never over a page, a sheet or an alert, only while the app is in front, dropped
     after five minutes, with Send log (and the comment box), Not now and Don't ask again, and a switch in Settings › Logs (on by default).
  4. The hooks in Library.swift are the ones listed; the new files name no auto-tune (check-autotune.py keeps hooks registered).
  5. CI runs this check.

  check-bcd-session-record.py [--require-swift]
"""
import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

R = Path(__file__).resolve().parents[2]
APP = R / "app/Madeira"
ap = argparse.ArgumentParser()
ap.add_argument("--require-swift", action="store_true")
args = ap.parse_args()
failures = 0


def check(cond, what, detail=""):
    global failures
    print(("PASS: " if cond else "FAIL: ") + what + (("\n      " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures += 1


def code(text):
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        out.append(re.sub(r'\s//[^"]*$', "", line))
    return "\n".join(out)


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
core_text = (APP / "BCDSessionCore.swift").read_text()
rec_text = (APP / "BCDSessionRecorderLive.swift").read_text()
library = (APP / "Library.swift").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()
sections = (APP / "BCDSettingsSections.swift").read_text()
settings_live = (APP / "BCDSettingsLive.swift").read_text()
rec = code(rec_text)
core = code(core_text)

# ---- 1. licence and layering ---------------------------------------------------------------------------------
check(core_text.startswith(HEADER) and rec_text.startswith(HEADER), "BCDSessionCore.swift and BCDSessionRecorderLive.swift: BCD's licence header")
check(re.findall(r"^import (\w+)", core, re.M) == ["Foundation", "CoreGraphics"] and "UIKit" not in core and "SwiftUI" not in core,
      "the core knows Foundation and CoreGraphics only")
check(not re.search(r"\b(LibraryModel|LogStore|MadeiraConfig|madeira_|winios_|wine_|bcd_frame_)", core), "the core names no engine type")
check(not [l for l in rec.splitlines() if "AutoTune" in l] and not re.search(r"(?i)auto-?tune", core), "the new files name no auto-tune in code")
check("python3 tests/host/check-bcd-session-record.py" in workflow, "CI runs this check")

# ---- 2. the recorder --------------------------------------------------------------------------------------------
live = block(rec, "@MainActor final class BCDSessionRecorderLive {")
check(live.count("bcd_frame_stats_read(&frames)") == 1 and live.count("Timer(") == 0 and "RunLoop" not in live,
      "the frame statistics are read in one place, and the recorder has no timer: nothing is sampled while the game runs")
began = block(live, "func began(_ entry: LibraryEntry) {")
check("bcd_frame_stats_begin()" in began and "writeMarker(for: entry)" in began and began.count("addObserver(") == 4,
      "a session starts the statistics, writes the marker and watches the heat, Low Power Mode and the app's coming and going")
recover = block(live, "func recoverUnfinished() {")
tail_fn = block(live, "static func logTail(of url: URL) {")
check("if let log { Self.logTail(of: log) }" in recover and recover.index("did not end cleanly") < recover.index("Self.logTail(of: log)")
      < recover.index("guard let offer, let log else { return }")
      and "BCDPrevTail.lines(data, offset: from)" in tail_fn and 'LogStore.shared.log("[prev-tail] " + LogUploadCore.scrub(line))' in tail_fn
      and "BCDPrevTail.maxBytes" in tail_fn and "write(" not in tail_fn,
      "round 624 (R S5): after an unclean end the failed run's last lines go into the new log, each scrubbed, whether or not BCD asks to send it")
ended = block(live, "func ended() {")
check("LogStore.shared.log(summary(partial: false).line())" in ended and "removeObservers()" in ended and "removeMarker()" in ended,
      "a session ends with the summary line, the observers gone and the marker removed")
lifecycle = block(live, "private func lifecycle(background: Bool) {")
check("summary(partial: true).line()" in lifecycle and "if background {" in lifecycle and "write(current)" in lifecycle,
      "going to the background writes a partial summary (the last the log may hear) and records where the app is")
check("ledger_phys_footprint_peak" in (APP / "BCDHUDLive.swift").read_text() and "BCDHUDLive.memory()" in live,
      "the memory peak is iOS's own ledger (the footprint's peak this run), not sampled")
check('ProcessInfo.thermalStateDidChangeNotification' in live and "noteHeat()" in live, "the heat reached is kept by notification")
check("applicationSupportDirectory" in live and "documentDirectory" not in live.split("static var markerURL")[1].split("}")[0],
      "the marker lives in Application Support, not in the Documents folder the player sees")
check(rec.count("bcd-session-open.json") == 1, "one marker file")
# Round 624 (agent B S3, Blasphemous P3M757 and 31 logs that end inside the JIT-pool request): the start-up phase is on disk before its step
content = (APP / "ContentView.swift").read_text()
phase_type = block(rec, "enum BCDStartPhase {")
recover_p = block(live, "func recoverUnfinished() {")
check(rec.count("bcd-session-phase.txt") == 1 and "func winiosPhase(_ name: String)" in rec
      and "BCDStartPhase.note(name)\n    winios_phase(name)" in rec and "try? data.write(to: url, options: .atomic)" in phase_type,
      "round 624 (B S3): one phase file beside the marker, written synchronously from the calling thread before the [phase] line")
check(content.count('winiosPhase("') == 5 and 'winios_phase("' not in content
      and content.index('winiosPhase("pool-alloc-begin")') < content.index('StikJITHelper.allocatePool('),
      "...by all five start-up phases of ContentView.swift, the pool request's before the request")
check(recover_p.index("BCDStartPhase.take()") < recover_p.index("guard let url = Self.markerURL")
      and "BCDStartPhase.clear()" in block(live, "func removeMarker() {")
      and 'let stop = phase.map { ", last start-up phase' in recover_p and "\\(stop)); " in recover_p,
      "...read and removed at the next start (named in the [session] line only when there is one), removed at a clean end")
recover = block(live, "func recoverUnfinished() {")
check(recover.index("try? FileManager.default.removeItem(at: url)") < recover.index("JSONDecoder().decode(BCDSessionMarker.self"),
      "the marker is removed BEFORE it is read: one question per session, whatever happens next")
check("BCDCrashRules.offer(marker: found" in recover and "enabled: Self.asks" in recover and "BCDCrashPrompt.shared.schedule(offer, log: log)" in recover,
      "the rules decide (turned off, too old); a log that is gone is not asked about")
check("BCDCrashRules.notAsking(marker: found, now: Date().timeIntervalSince1970, enabled: Self.asks)" in recover,
      "round 624 (N S14): the [session] line names the rules' reason for not asking (turned off, too old, a start left within 20 s)")
starting = block((APP / "BCDSessionLive.swift").read_text(), "private func startingInfo() -> BCDStartingInfo {")
check('if dock.failure == nil && info.seconds < BCDCrashRules.shortStart {' in starting and 'BCDStartNote(id: "stay-in-front", text: "Stay in BCD until the game\'s picture shows.' in starting,
      "round 624 (N S14): the starting screen says to stay in BCD for the first 90 s of a start that has not stopped")
_sl = (APP / "BCDSessionLive.swift").read_text()
_ls = (APP / "BCDLiveStore.swift").read_text()
check('if BCDDesktopHint.armed, model.activeEntry?.desktop == true {' in starting and 'BCDStartNote(id: "desktop-empty", text: BCDDesktopHint.text)' in starting
      and 'guard games <= 1, !defaults.bool(forKey: flag) else { return }' in _sl and 'static let flag = "bcd.desktop-hint-shown"' in _sl
      and 'if entry.desktop == true { BCDDesktopHint.arm(games: store.games.count) }' in _ls
      and _ls.index('BCDDesktopHint.arm(games: store.games.count)') < _ls.index('begin(entry, launch)', _ls.index('BCDDesktopHint.arm(')),
      "round 624 (D2 S2): the Windows desktop's first start on an install with no other game shows one note on the starting screen, once")


def _swift_words(src, marker):
    """The text of a Swift string constant written as "..." + "..." pieces, from `marker` on."""
    rest = src[src.index(marker) + len(marker):]
    out, first = [], True
    for line in rest.splitlines():
        s = line.strip()
        if not first and not s.startswith("+"):
            break
        out += re.findall(r'"((?:[^"\\]|\\.)*)"', s)
        first = False
    return "".join(out)


_pv = (R / "app/BCDPreview/BCDPreviewSession.swift").read_text()
check(_swift_words(_sl, "static let text = ") == _swift_words(_pv, "static let desktopHint = ")
      and "Start, Run, Browse" in _swift_words(_pv, "static let desktopHint = "),
      "the preview app's copy of the desktop note (starting-desktop) says exactly what BCDDesktopHint.text says")
check("static func findLog(inode: UInt64) -> URL?" in live and "bcd-log.prev.txt" in live and "systemFileNumber" in live,
      "the session's log is found again by its inode: the game's own name under logs/, else the rotated bcd-log.prev.txt")
facade = block(rec, "enum BCDSessionRecorder {")
check("static func closingOnPurpose()" in facade and "static func appStarted()" in facade and "BCDScreenshotLive.install()" in facade,
      "the facade has the hooks: start, a close on purpose, and Send Log learning screenshots")
sessionlive = (APP / "BCDSessionLive.swift").read_text()
upload = block(sessionlive, "private func uploadAndClose(replace: Bool) {")
check(upload.index("BCDSessionRecorder.closingOnPurpose()") < upload.index("exit(0)"), "'Upload saves and close BCD' removes the marker before it closes the app")
begin = block(library, "    func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil) {")
finish = block(library, "    private func finish() {")
content = (APP / "ContentView.swift").read_text()
check(content.count("BCDSessionRecorder.closingOnPurpose()") == 1 and content.index("BCDSessionRecorder.closingOnPurpose()") < content.index("exit(0)", content.index("[session-once] closed by the user for a restart")),
      "'BCD needs a restart' > Close BCD removes the marker first: a session that was still open is no crash")
check(begin.count("BCDSessionRecorder.sessionBegan(entry)") == 1 and finish.count("BCDSessionRecorder.sessionEnded()") == 1
      and finish.index("BCDSessionRecorder.sessionEnded()") < finish.index("autoTuneSessionEnded"),
      "LibraryModel.begin starts the recorder and finish() ends it first, before anything reads the log")
check(library.count("BCDSessionRecorder.appStarted()") == 1, "LibraryModel.init asks about the session of the run before")

# ---- 3. the question -------------------------------------------------------------------------------------------
prompt = block(rec, "@MainActor final class BCDCrashPrompt {")
look = block(prompt, "private func look() {")
check("if tries > 100 || library.current != nil { finish(); return }" in look, "it is dropped when a game starts or five minutes go by (3 s a look, 100 looks)")
check("library.error == nil" in look and "library.restartNotice == nil" in look and "UIApplication.shared.applicationState == .active" in look and "Self.presenter()" in look,
      "it waits for the app to be in front, with no error or restart notice up")
presenter = block(prompt, "static func presenter() -> UIViewController? {")
check("root.presentedViewController == nil" in presenter, "... and for nothing to be presented over the root screen (no page, sheet or alert): it never takes a screen away")
present = block(prompt, "private func present(on top: UIViewController) {")
check('title: "Send log"' in present and 'title: "Not now"' in present and 'title: "Don\'t ask again"' in present and "BCDLogComment.ask(for: log" in present,
      "Send log (with the comment box and the screenshot choice), Not now, Don't ask again")
check("self.offer = nil" in present and "self.log = nil" in present, "the offer is spent once it is shown")
check("BCDSessionRecorderLive.asks = false" in present and 'static let askKey = "madeira.bcd.crashprompt"' in live
      and "UserDefaults.standard.object(forKey: askKey) == nil || UserDefaults.standard.bool(forKey: askKey)" in live, "Don't ask again turns it off; on unless turned off")
check("asksAfterUnexpectedClose" in sections and 'BCDSettingsID.control(.logs, "crashprompt")' in sections and "BCDSessionRecorderLive.asks" in settings_live,
      "Settings › Logs has the switch")
check("LogUpload.sentWords(code)" in block(prompt, "static func report(_ result: Result<String, Error>) {"), "the answer to a send says the code (and how a screenshot fared)")

# ---- behaviour --------------------------------------------------------------------------------------------------
swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-session-") as t:
        t = Path(t)
        exe = t / "run"
        b = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(exe), str(APP / "BCDSessionCore.swift"),
                            str(R / "tests/host/bcd_session_checks.swift")], capture_output=True, text=True)
        check(b.returncode == 0, "the session core and its checks compile on the host", b.stderr[-1500:])
        line = ""
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            oks = [l for l in r.stdout.splitlines() if l.startswith("ok")]
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            check(r.returncode == 0 and oks, f"the session rules: {len(oks)} checks", "\n      ".join(bad[:12]))
            line = next((l[len("note: "):] for l in r.stdout.splitlines() if l.startswith("note: [session]")), "")

        # tools/log-facts.py reads the line the app writes
        spec = importlib.util.spec_from_file_location("log_facts", R / "tools/log-facts.py")
        facts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(facts)
        parsed = facts.parse_session("[21:05:03.123] [INFO] " + line) if line else None
        check(parsed is not None and parsed.get("avg") == "58.7" and parsed.get("low1") == "41.2" and parsed.get("low01") == "22.5"
              and parsed.get("stalls") == "2/14s" and parsed.get("api") == "D3D11/64" and parsed.get("game") == "Hollow Knight" and parsed.get("thermal") == "fair",
              "log-facts.py reads the line the app writes, field for field", str(parsed))
        words = facts.session_words(parsed or {})
        check("20 min played" in words and "58.7 FPS avg" in words and "1% low 41.2" in words and "0.1% low 22.5" in words and "worst 215ms" in words
              and "stalls 2/14s" in words and "peak 3412MB of 5800MB" in words and "thermal fair" in words and "API D3D11/64" in words and "cap 60" in words,
              "... and says it in words", words)
        check(facts.parse_session("[session] the last session of X did not end cleanly (in front); asking") is None
              and facts.parse_session("[present] count=3") is None, "... and nothing else is a summary")
        log = [
            "[bcd] library launch Hollow Knight: x",
            "[session] length=300s partial=1 play=250s frames=15000 avg=60.0 low1=50.0 worst=40ms stalls=0/0s peak=2000MB limit=5800MB thermal=nominal lpm=0 game=Hollow Knight",
            "[session] length=1234s play=1180s frames=71234 avg=58.7 low1=41.2 low01=22.5 worst=215ms stalls=2/14s peak=3412MB limit=5800MB thermal=fair lpm=0 api=D3D11/64 game=Hollow Knight",
            "[session] the last session of Old Game did not end cleanly (in front); asking whether to send its log",
            "[screenshot] saved Hollow Knight 2026-10-25 21.05.03.jpg 1864x860 412 KB photos=asked black=0",
        ]
        found, unclean, shots = facts.session_facts(log)
        check(len(found) == 2 and len(unclean) == 1 and shots == 1 and found[-1][1].get("partial") is None and found[0][1].get("partial") == "1",
              "log-facts.py finds the partial and the whole summary, the unclean-close line and the screenshots", f"{found} {unclean} {shots}")

        # the scanner behind `pull-logs.py --replay`
        replay = t / "replay"
        b = subprocess.run(["xcrun", "swiftc", "-parse-as-library", "-swift-version", "5", "-o", str(replay), str(APP / "AutoTuneCore.swift"),
                            str(APP / "AutoTuneRules.generated.swift"), str(R / "tests/host/autotune_replay.swift")], capture_output=True, text=True)
        check(b.returncode == 0, "the replay tool builds with the scanner", b.stderr[-800:])
        if b.returncode == 0:
            for name, lines, want in (("whole", log[2:3], (58, 41, 1180, False)), ("partial only", log[1:2], (60, 50, 250, True)),
                                      ("both: the newest wins", log[1:3], (58, 41, 1180, False)),
                                      ("none", [log[3], log[4]], (None, None, None, False))):
                f = t / (name.split()[0] + ".txt")
                f.write_text("[build] v0.1.603 (603)\n" + "\n".join(lines) + "\n")
                r = subprocess.run([str(replay), str(f)], capture_output=True, text=True)
                try:
                    j = json.loads(r.stdout.splitlines()[-1])["facts"]
                    got = (j.get("sessionAverageFPS"), j.get("sessionLow1FPS"), j.get("sessionPlaySeconds"), j.get("sessionPartial", False))
                except (IndexError, ValueError, KeyError):
                    got = None
                check(got == want, f"the replay's scanner reads the summary ({name}): average, 1% low, seconds played, partial", f"{got} want {want} {r.stderr[-200:]}")

print("check-bcd-session-record: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
