#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A game that ignores Close (round 707, G1-S15: Hollow Knight DEQCHZ; 31 of 330 archive logs with a close request show no return to the library a
minute later): 45 s after the player's Close the session is still up, BCD says so and the menu's quit row becomes "End the game now", which ends the
session the way the app already ends one whose game is gone (wine_session_dock_game_quit), and nothing else.

  1. SessionForceEnd.swift (Foundation only) compiled with tests/host/session_force_end_checks.swift: the phases and their clocks, who it applies to, the words.
  2. Source: the clock (first Close only, reset at a start and at the end), the watch in the library's poll, the one engine call and no other kill, the log
     lines, the menu row, its question and its answer, the end going through finish() (the open-session marker is cleared, no crash question at the next
     start, no error box), the engine function it calls and what that function leaves alone, the files in the project.

  check-session-force-end.py [--require-swift]
"""
import argparse
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


def block(text, header):
    p = text.index(header)
    a = text.index("{", p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == "{") - (text[b] == "}")
        b += 1
    return text[p:b]


def code(text):
    return "\n".join(re.sub(r'\s//.*$', "", l) for l in text.splitlines() if not l.strip().startswith("//"))


HEADER = ("// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n"
          "// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.\n")
rules = (APP / "SessionForceEnd.swift").read_text()
library = (APP / "Library.swift").read_text()
sessionlive = (APP / "BCDSessionLive.swift").read_text()
bridge = (APP / "WineProcessBridge.m").read_text()
header = (APP / "WineProcessBridge.h").read_text()
recorder = (APP / "BCDSessionRecorderLive.swift").read_text()
project = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
workflow = (R / ".github/workflows/build-ipa.yml").read_text()

check(rules.startswith(HEADER) and (R / "tests/host/session_force_end_checks.swift").read_text().startswith(HEADER), "licence headers")
check(re.findall(r"^import (\w+)", rules, re.M) == ["Foundation"], "the rules import Foundation only")
check(re.search(r"SwiftUI|MadeiraConfig|LogStore|getenv|wine_|LibraryModel", code(rules)) is None, "the rules name no engine type and make no call")

# ---- the library -----------------------------------------------------------------------------------------------------
check("private var quitRequestedAt: Date?" in library and "private var forceEndAskedAt: Date?" in library
      and "@Published private(set) var forceEnd = SessionForceEnd.Phase.none" in library, "LibraryModel keeps the two clocks and the phase")
begin = block(library, "func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)")
check("quitRequestedAt = nil; forceEndAskedAt = nil; forceEnd = .none" in begin, "a start forgets them")
fin = block(library, "private func finish()")
check("quitRequestedAt = nil; forceEndAskedAt = nil; forceEnd = .none" in fin, "the end of a session forgets them")
quit = block(library, "func requestQuit()")
game_path = quit.split("BCDPauseLive.resume()   // madeira-bcd (round 604)")[1]
check("if quitRequestedAt == nil { quitRequestedAt = Date() }" in game_path and "quitRequestedAt" not in quit.split("BCDPauseLive.resume()   // madeira-bcd (round 604)")[0],
      "the first Close of a game stamps the clock (a second Close does not restart it); the Duo desktop's own close does not")
poll = block(library, "private func poll()")
running = poll.split("if wine_process_is_running() != 0 {")[1].split("} else if sawProcess")[0]
check("watchForceEnd()" in running and running.index("watchDesktopClose()") < running.index("watchForceEnd()"),
      "the watch runs each half second while the session is up, after every older watch (they keep their places)")
watch = block(library, "private func watchForceEnd()")
check("guard let asked = quitRequestedAt, SessionForceEnd.applies(desktop: activeEntry?.desktop == true, launching: launching) else { return }" in watch
      and "SessionForceEnd.phase(sinceClose: sinceClose, sinceEnd: forceEndAskedAt.map { -$0.timeIntervalSinceNow }, sessionUp: true)" in watch
      and "guard phase != forceEnd else { return }" in watch, "the watch asks the rules and acts once per phase, for a game that is showing and was asked to close")
check("sessionMessage = SessionForceEnd.offerWords" in watch and "sessionMessage = SessionForceEnd.stuckWords" in watch
      and '"[force-end] the game is still up' in watch and '"[force-end] the session is still up' in watch,
      "it says what to do on the session line and in the log")
check(not re.search(r"wine_|exit\(|kill\(|abort\(|pthread_|signal", code(watch)), "the watch ends nothing")
end = block(library, "func endGameNow()")
check("guard current != nil, forceEnd == .offered, let asked = quitRequestedAt else { return }" in end, "it can only be chosen while it is offered")
calls = re.findall(r"\b(wine_\w+|madeira_\w+|exit|kill|abort|pthread_\w+|wineserver_\w+|winios_\w+)\(", code(end))
check(calls == ["madeira_live_user_processes", "wine_session_dock_game_quit"] or calls == ["madeira_live_user_processes", "madeira_live_user_processes", "wine_session_dock_game_quit"],
      "its only engine calls: which processes of the game are left (for the log), and the engine's own end of a session; no new kill", str(calls))
check("DispatchQueue.global(qos: .userInitiated).async {\n            let ended = wine_session_dock_game_quit(0)" in end, "the end runs off the main thread, with status 0 (the player's quit)")
check("SessionForceEnd.logLine(" in end and '"[force-end] the engine\'s end work "' in end, "the log says what was left and whether the end work ran")
check("forceEnd = .ending" in end and "menu = false" in end and "sessionMessage = SessionForceEnd.endingWords" in end, "the menu closes and the line says it is under way")
check("DockGameEndRules" not in code(end) and "endedByDockGame" not in code(end) and "quitRequested = " not in code(end),
      "it does not borrow the Dock watch's flags or touch the quit request")
# the session's end is recorded as any other end is
check("BCDSessionRecorder.sessionEnded()" in fin and "autoTuneSessionEnded()" in fin and "GameSaveAuto.sessionEnded(activeEntry" in fin,
      "finish() records the end as it always does: the session line, auto-tune, the saves")
check("func ended() {" in recorder and "removeMarker()" in block(recorder, "func ended() {"),
      "BCDSessionRecorder.ended() removes the open-session marker: the next start asks no crash question")
check("guard !quitRequested, MadeiraConfig.flag(\"MADEIRA_EXIT_REPORT\") else { return nil }" in library, "after a Close, no exit report and no error box")
check("else if sawProcess && wineserver_is_running() == 0 { finish() }" in poll, "poll() calls finish() once the engine's session and its server are over")
# the engine's side
check("int wine_session_dock_game_quit(int32_t status);" in header, "WineProcessBridge.h declares the engine call")
quit_fn = block(bridge, "int wine_session_dock_game_quit(int32_t status)")
check("if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;" in quit_fn and "wine_session_end();" in quit_fn
      and "g_launch_exit" not in quit_fn.replace("g_launch_exit stays", "") and "__atomic_store_n(&g_wine_exit_code, (int)status, __ATOMIC_RELEASE);" in quit_fn,
      "the engine call: once per session, sets the exit status, runs the shared end work, and does not make the status a crash")
dead = block(bridge, "static void wine_session_end(void)\n{")
check("g_wine_running = 0;" in dead and "wineserver_stop();" in dead and "if (__atomic_exchange_n(&g_session_end_claim, 1, __ATOMIC_ACQ_REL)) return;" in dead,
      "the shared end work: running off, per-launch switches unset, the server stopped, once")
check("g_session_end_claim = 0;" in block(bridge, "int wine_process_start(const char *prefix_path)") and "g_wine_running = 1;" in block(bridge, "int wine_process_start(const char *prefix_path)"),
      "the next start clears the claim and starts again in the same app run (a session whose end work ran is startable)")

# ---- the menu ----------------------------------------------------------------------------------------------------------
check("model.$forceEnd.dropFirst()" in sessionlive and "self?.hud.menu?.changed()" in sessionlive, "an open menu shows the new row at once")
game = block(sessionlive, "private var gameSections: [BCDMenuSection]")
sw = game.split("switch model.forceEnd {")[1].split("case .none:")[0]
check("title: SessionForceEnd.menuTitle" in sw and "BCDConfirm(title: SessionForceEnd.confirmTitle" in sw and 'answers: [.init(id: "force", title: SessionForceEnd.answerTitle, destructive: true)]' in sw
      and sw.index(".offered") < sw.index(".ending") < sw.index(".stuck"), "the quit row becomes End the game now, asks first, and says Ending... and the last words after")
check(game.index("switch model.forceEnd {") < game.index('title: BCDDuoFeatures.active && model.activeEntry?.desktop == true ? "Close Windows" : "Quit game"'),
      "with nothing offered the row is Quit game as before")
check('case ("quit", "force"): model.endGameNow()' in sessionlive and 'case ("quit", "quit"): model.requestQuit()' in sessionlive, "the answer ends the game; Quit game asks it to close as before")
check(project.count("/* SessionForceEnd.swift in Sources */") == 2 and project.count("path = SessionForceEnd.swift;") == 1, "SessionForceEnd.swift is in the Xcode project")
if "python3 tests/host/check-session-force-end.py" not in workflow:
    print("NOTE: .github/workflows/build-ipa.yml does not run this check yet (the step is in the hand-back)")

swiftc = shutil.which("swiftc")
if not swiftc:
    check(not args.require_swift, "swiftc on the host (behaviour checks)")
else:
    with tempfile.TemporaryDirectory(prefix="bcd-forceend-") as t:
        tmp = Path(t)
        shutil.copy(R / "tests/host/session_force_end_checks.swift", tmp / "main.swift")
        exe = tmp / "run"
        b = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(exe), str(APP / "SessionForceEnd.swift"), str(tmp / "main.swift")], capture_output=True, text=True)
        check(b.returncode == 0, "the rules and their checks compile on the host", b.stderr[-2000:])
        if b.returncode == 0:
            r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
            bad = [l for l in r.stdout.splitlines() if l.startswith("FAIL") or l.startswith("     ")]
            summary = [l for l in r.stdout.splitlines() if l.startswith("session force end:")]
            check(r.returncode == 0 and summary, summary[0] if summary else "the force-end rules", "\n      ".join(bad[:10]))

print("check-session-force-end: " + ("PASS" if failures == 0 else f"{failures} FAILED"))
sys.exit(0 if failures == 0 else 1)
