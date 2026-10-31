#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A BCD Dock session whose game died ends (round 579).

Dying Light 32FEAT (Dock, build 578, c0000005) and The Binding of Isaac VV5NHZ (Dock, build 576, c0000005): the game's
process ended, Valve's client counted the game over and the Dock host finished, but the session's own program is Wine's
desktop (explorer), which never closes by itself on iOS, so the session stayed on the dead desktop for good.

1. Swift: DockGameEnd.swift (DockGameEndRules: parse, verdict, grace, isCrash) with the starting screen's classification of
   where a program lives (SteamLaunchScene.owner): which process is the game, when the session is over, when not.
2. C: process_ios.c's child table (the path of each process, the ring of ended ones with their status, the text report)
   under AddressSanitizer / UndefinedBehaviorSanitizer; its text then goes through the Swift rules end to end.
3. C: WineProcessBridge.m's wine_session_dock_game_ended (once, the status becomes the session's exit status, the end work).
4. Auto-tune: the rule dock-game-died-session-open matches the old logs, stays out of the way of the locale experiment, and
   does not match a log that shows the new path ran.
5. Source checks: the wiring, the keys, the project file, the failing-path-only boundaries, no program names.

Synthetic data and facts read from the logs only: no Wine, no game, no Steam.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
CC = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
failures = 0


def require(condition, label):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label)
    if not condition:
        failures += 1


def block(text, header_text):
    p = text.index(header_text)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


rules_src = (app / 'DockGameEnd.swift').read_text(encoding='utf-8')
screen = (app / 'DockStartScreen.swift').read_text(encoding='utf-8')
library = (app / 'Library.swift').read_text(encoding='utf-8')
bridge = (app / 'WineProcessBridge.m').read_text(encoding='utf-8')
header = (app / 'WineProcessBridge.h').read_text(encoding='utf-8')
process = (root / 'build/ntdll-unix/process_ios.c').read_text(encoding='utf-8')
server = (root / 'build/ntdll-unix/server_ios.c').read_text(encoding='utf-8')
project = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text(encoding='utf-8')
catalog = (root / 'build/tools/gen-config-catalog.py').read_text(encoding='utf-8')
rules_py = (root / 'build/tools/autotune_rules.py').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
screen_rules = screen[screen.index('// MARK: - Rules'):screen.index('// MARK: - Model')]
table = process[process.index('#define IOS_CHILD_SLOTS 32'):process.index("/* the machine of the child's main image, published to")]

# ------------------------------------------------------------------ 1. Swift: the rules
swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
typealias R = DockGameEndRules
let places = SteamLaunchScene.Places(clientRoot: "C:\\Program Files (x86)\\Steam")
let game = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Some Game\\Game.exe"
func ended(_ path: String, _ status: UInt32, ago: Double, lived: Double = 30, helper: Bool = false) -> DockProcess {
    DockProcess(path: path, helper: helper, running: false, status: status, endedAgo: ago, lived: lived)
}
func runs(_ path: String, helper: Bool = false) -> DockProcess { DockProcess(path: path, helper: helper, running: true) }
let hostDone = DockHostState(gameRan: true, gameOver: true, finished: true)
var hostAsked = 0
func verdict(_ processes: [DockProcess], untracked: Int = 0, complete: Bool = true, grace: Double = 10,
             host: DockHostState = hostDone) -> R.Verdict {
    hostAsked = 0
    return R.verdict(DockProcessTable(processes: processes, untracked: untracked, complete: complete), places: places, grace: grace) {
        hostAsked += 1; return host
    }
}
// round 624: the same with the player's Close (anyStatus: a game that ended with any status is over)
func verdictAsked(_ processes: [DockProcess], untracked: Int = 0, complete: Bool = true, grace: Double = R.graceRange.lowerBound,
                  host: DockHostState = hostDone) -> R.Verdict {
    hostAsked = 0
    return R.verdict(DockProcessTable(processes: processes, untracked: untracked, complete: complete), places: places, grace: grace,
                     anyStatus: true) { hostAsked += 1; return host }
}
// round 625 (N-S18): the same before any frame reached the screen (rendered: false)
func verdictNoPicture(_ processes: [DockProcess], untracked: Int = 0, complete: Bool = true, grace: Double = 10,
                      host: DockHostState = hostDone) -> R.Verdict {
    hostAsked = 0
    return R.verdict(DockProcessTable(processes: processes, untracked: untracked, complete: complete), places: places, grace: grace,
                     rendered: false) { hostAsked += 1; return host }
}
// Wine's own programs and the Dock host, as the table lists them in a Dock session.
let dockhost = ended("C:\\windows\\system32\\dockhost.exe", 0, ago: 7)
let conhost = ended("C:\\windows\\system32\\conhost.exe", 0, ago: 7)

@main struct Checks {
    static func main() {
        // ---- the statuses
        require(R.isCrash(0xC0000005) && R.isCrash(0xC0000409) && R.isCrash(0xC000001D) && R.isCrash(0xC0000374),
                "access violation, stack overrun, illegal instruction, heap corruption are crashes")
        require(R.isCrash(0xC0000135) && R.isCrash(0xC0000018) && R.isCrash(0xC0000017), "a missing DLL, conflicting addresses, no memory are failures")
        require(R.isCrash(0xE06D7363) && R.isCrash(0xE000BEEF), "a C++ exception code and a game's own 0xE... exit are failures")
        require(R.isCrash(0x80000003) && R.isCrash(0xFFFFFFFF) && R.isCrash(0xFFFFFC8F), "the breakpoint exit and negative exit codes are failures")
        require(!R.isCrash(0) && !R.isCrash(1) && !R.isCrash(3) && !R.isCrash(0xFF) && !R.isCrash(0x2329) && !R.isCrash(0xEAC9),
                "exit code 0 and small plain codes are not crashes (a game may end with 1 after a quit)")
        require(!R.isCrash(0x8007054F), "an installer's HRESULT-like exit code is not a crash")

        // ---- the grace key
        require(R.grace(nil) == 10 && R.defaultGrace == 10, "no key: 10 s")
        require(R.grace("20") == 20 && R.grace(" 5 ") == 5 && R.grace("3") == 3, "a number of seconds")
        require(R.grace("0") == 3 && R.grace("1") == 3 && R.grace("100000") == 300, "clamped to 3...300")
        require(R.grace("abc") == 10 && R.grace("") == 10 && R.grace("nan") == 10 && R.grace("inf") == 10, "not a number: the default")

        // ---- the engine's text
        let text = "R\t0\t3.2\tC:\\Games\\X\\Game.exe\nE\t1\tc0000005\t12.5\t3.0\tC:\\Games\\X\\crashpad_handler.exe\nU\t2\n"
        let parsed = R.parse(text)
        require(parsed.complete && parsed.untracked == 2 && parsed.processes.count == 2, "R, E and U lines are read")
        require(parsed.processes[0].running && !parsed.processes[0].helper && parsed.processes[0].path == "C:\\Games\\X\\Game.exe", "a running process")
        require(!parsed.processes[1].running && parsed.processes[1].helper && parsed.processes[1].status == 0xC0000005
                && parsed.processes[1].endedAgo == 12.5 && parsed.processes[1].lived == 3.0, "an ended one: status in hex, helper flag, times")
        require(parsed.processes[1].fileName == "crashpad_handler.exe" && DockProcess(path: "", helper: false, running: true).fileName == "?",
                "the file name only, lower case")
        require(!R.parse("R\t0\t3.2\tC:\\a.exe\nT\n").complete, "a cut text (T) is incomplete")
        require(!R.parse("R\t0\tx\tC:\\a.exe\n").complete && !R.parse("E\t0\tzz\t1\t1\tC:\\a.exe\n").complete && !R.parse("X\t1\n").complete,
                "a malformed or unknown line is incomplete")
        require(R.parse("").processes.isEmpty && R.parse("").complete, "an empty snapshot is empty and complete")

        // ---- the verdict: Dying Light 32FEAT (facts from the log)
        // game 6.4 s before the host's "game ended", host and its conhost gone: the game ended 12 s ago, 3 s lived
        let dying = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Dying Light\\DyingLightGame.exe"
        let finished = verdict([dockhost, conhost, ended(dying, 0xC0000005, ago: 12.0, lived: 3.1)])
        require(finished == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 3.1), "32FEAT: game ended c0000005 12 s ago, host finished: the session ends")
        require(hostAsked == 1, "the host's report is read only once everything else favours the end")
        require(verdict([dockhost, conhost, ended(dying, 0xC0000005, ago: 4)]) == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "grace"),
                "4 s after the crash: inside the grace time, wait")
        require(hostAsked == 0, "inside the grace time the report is not read")
        require(verdict([dockhost, conhost, ended(dying, 0xC0000005, ago: 10)]) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30),
                "exactly the grace time: end")
        require(verdict([dockhost, ended(dying, 0xC0000005, ago: 5)], grace: 3) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30),
                "the key's grace is used")

        // ---- the host must agree (Valve's client counted the game over, the host finished)
        let gone = [dockhost, ended(dying, 0xC0000005, ago: 20)]
        require(verdict(gone, host: DockHostState(gameRan: false, gameOver: false, finished: false))
                == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "host-never-saw-the-game-run"), "the host never saw the game run: wait")
        require(verdict(gone, host: DockHostState(gameRan: true, gameOver: false, finished: false))
                == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "host-still-counts-the-game"), "Valve's client still counts the game: wait")
        require(verdict(gone, host: DockHostState(gameRan: true, gameOver: true, finished: false))
                == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "host-not-finished"), "the host has not finished: wait")

        // ---- anything of the game still running keeps the session
        require(verdict([dockhost, ended(dying, 0xC0000005, ago: 60), runs(game)]) == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "game-still-runs"),
                "a launcher's relaunch (another program of the game runs): wait, however long ago the first ended")
        require(verdict([dockhost, runs(dying)]) == .none, "the game runs, nothing ended: nothing to say")
        require(verdict([ended(dying, 0xC0000005, ago: 60), runs("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Some Game\\Launcher.exe")])
                == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "game-still-runs"), "a running launcher keeps the session")
        require(verdict([ended(dying, 0xC0000005, ago: 60), runs("")]) == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "game-still-runs"),
                "a running program whose folder is unknown may be the game: wait")
        require(verdict([ended(dying, 0xC0000005, ago: 60), ended("", 0xC0000005, ago: 1)]) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30),
                "an ended program with an unknown folder is never counted as the game")

        // ---- spawns the engine could not track, and a cut table: never end
        require(verdict([dockhost, ended(dying, 0xC0000005, ago: 60)], untracked: 1) == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "untracked-process"),
                "a spawn past the table: wait")
        require(verdict([dockhost, ended(dying, 0xC0000005, ago: 60)], complete: false) == .wait(status: 0xC0000005, file: "dyinglightgame.exe", why: "table-incomplete"),
                "a cut table: wait")

        // ---- exit code 0 and plain codes keep today's behaviour
        require(verdict([dockhost, ended(dying, 0, ago: 60)]) == .none, "the game ended with 0: nothing")
        require(verdict([dockhost, ended(dying, 1, ago: 60)]) == .none && verdict([dockhost, ended(dying, 3, ago: 60)]) == .none, "plain codes 1 and 3: nothing")
        require(verdict([dockhost, ended(dying, 0xC0000005, ago: 300), ended(game, 0, ago: 50)]) == .none,
                "an early crash of one program, the game's own last end is 0: nothing (the LAST program decides)")

        // ---- round 625 (N-S18; QQ0BEX and about 40 Dock logs): it closed by itself with 0 within 30 s, before any frame
        let brotato = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Brotato\\Brotato.exe"
        require(R.shortExitSeconds == 30, "the short exit is 30 s")
        require(verdictNoPicture([dockhost, ended(brotato, 0, ago: 60, lived: 5)]) == .end(status: 0, file: "brotato.exe", lived: 5),
                "0 after 5 s, no frame, the host finished, nothing runs: end")
        require(verdict([dockhost, ended(brotato, 0, ago: 60, lived: 5)]) == .none, "the same after frames were drawn: nothing (today's rule)")
        require(verdictNoPicture([dockhost, ended(brotato, 0, ago: 60, lived: 40)]) == .none
                && verdictNoPicture([dockhost, ended(brotato, 0, ago: 60, lived: 30)]) == .none, "it lived 30 s or more: nothing")
        require(verdictNoPicture([dockhost, ended(brotato, 1, ago: 60, lived: 5)]) == .none, "a plain code 1 is not a short clean exit: nothing")
        require(verdictNoPicture([dockhost, ended(brotato, 0, ago: 4, lived: 5)]) == .wait(status: 0, file: "brotato.exe", why: "grace"),
                "within the grace: wait")
        require(verdictNoPicture([dockhost, ended(brotato, 0, ago: 60, lived: 5)], host: DockHostState(gameRan: true, gameOver: false, finished: false))
                == .wait(status: 0, file: "brotato.exe", why: "host-still-counts-the-game"), "Valve's client still counts the game (a relaunch): wait")
        require(verdictNoPicture([dockhost, ended(game, 0, ago: 60, lived: 5), runs(brotato)]) == .wait(status: 0, file: "game.exe", why: "game-still-runs"),
                "a launcher that started the game (a program of it runs): wait")
        require(verdictNoPicture([dockhost, ended(brotato, 0, ago: 60, lived: 5)], untracked: 1) == .wait(status: 0, file: "brotato.exe", why: "untracked-process"),
                "an untracked program: wait")
        require(verdictNoPicture([dockhost, ended(dying, 0xC0000005, ago: 60, lived: 5)]) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 5),
                "a crash before any frame ends as before")
        require(verdictNoPicture([dockhost]) == .none, "no program of the game: nothing")

        // ---- round 624 (Cuphead PDKQP7 "Close game bug"): after the player's Close, a game that ended with any status is over
        let cuphead = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Cuphead\\Cuphead.exe"
        require(verdictAsked([dockhost, ended(cuphead, 0, ago: 4, lived: 330)]) == .end(status: 0, file: "cuphead.exe", lived: 330),
                "Close, the game quit with 0, the host finished, nothing runs: end")
        require(verdictAsked([dockhost, ended(cuphead, 1, ago: 4)]) == .end(status: 1, file: "cuphead.exe", lived: 30), "a plain code after Close: end")
        require(verdict([dockhost, ended(cuphead, 0, ago: 60)]) == .none, "without the Close the clean quit keeps today's behaviour")
        require(verdictAsked([dockhost, ended(cuphead, 0, ago: 1)]) == .wait(status: 0, file: "cuphead.exe", why: "grace"),
                "within the 3 s grace (a launcher that hands over to the game): wait")
        require(verdictAsked([dockhost, ended(game, 0, ago: 60), runs(cuphead)]) == .wait(status: 0, file: "game.exe", why: "game-still-runs"),
                "Close while a program of the game runs: wait (the Alt+F4 closes it as before)")
        require(verdictAsked([dockhost, ended(cuphead, 0, ago: 60)], host: DockHostState(gameRan: true, gameOver: true, finished: false))
                == .wait(status: 0, file: "cuphead.exe", why: "host-not-finished"), "the host has not finished: wait")
        require(verdictAsked([dockhost, ended(cuphead, 0, ago: 60)], host: DockHostState(gameRan: false, gameOver: false, finished: false))
                == .wait(status: 0, file: "cuphead.exe", why: "host-never-saw-the-game-run"), "Close before the game ever ran: wait")
        require(verdictAsked([dockhost, ended(cuphead, 0, ago: 60)], untracked: 1) == .wait(status: 0, file: "cuphead.exe", why: "untracked-process"),
                "an untracked program: wait")
        require(verdictAsked([dockhost, ended(cuphead, 0, ago: 60)], complete: false) == .wait(status: 0, file: "cuphead.exe", why: "table-incomplete"),
                "a cut table: wait")
        require(verdictAsked([dockhost]) == .none && verdictAsked([]) == .none, "Close with no program of the game: nothing")
        require(verdictAsked([dockhost, ended(dying, 0xC0000005, ago: 10)]) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30),
                "a crash after Close still ends the session")
        require(verdict([dockhost, ended(game, 0, ago: 300), ended(dying, 0xC0000005, ago: 50)])
                == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30), "the last program crashed: end")

        // ---- who is not the game
        require(verdict([dockhost, ended("C:\\windows\\system32\\dockhost.exe", 0xC0000005, ago: 60)]) == .none, "the Dock host and Wine's own programs (under C:\\windows) are never the game")
        require(verdict([ended("C:\\windows\\system32\\conhost.exe", 0xC0000005, ago: 60), ended(game, 0, ago: 70)]) == .none, "a crashed console host does not decide")
        let client = "C:\\Program Files (x86)\\Steam\\bin\\steamservice.exe"
        require(verdict([ended(client, 0xC0000005, ago: 60)]) == .none, "a program of Valve's client folder is not the game")
        require(verdict([ended(dying, 0xC0000005, ago: 60), runs(client)]) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30),
                "a running program of Valve's client does not keep the session")
        // Valve's service program lives beside the client's folder (Common Files\Steam), from the CEG preparation
        let service = "C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe"
        require(R.valveShared(places) == "c:\\program files (x86)\\common files\\steam\\", "Valve's shared folder is derived from the client folder")
        require(R.valveShared(SteamLaunchScene.Places(clientRoot: "D:\\Valve")) == "d:\\common files\\valve\\" && R.valveShared(SteamLaunchScene.Places(clientRoot: "C:")) == nil,
                "and follows another root; a root without a parent gives none")
        require(verdict([ended(dying, 0xC0000005, ago: 60), runs(service)]) == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30),
                "Valve's service program running does not keep the session")
        require(verdict([ended(service, 0xC0000005, ago: 60), ended(game, 0, ago: 80)]) == .none && verdict([ended(service, 0xC0000005, ago: 60)]) == .none,
                "and one that crashed is not the game")
        require(verdict([ended("C:\\Program Files (x86)\\Steam\\steamapps\\common\\G\\Game.exe", 0xC0000005, ago: 60)]) == .end(status: 0xC0000005, file: "game.exe", lived: 30),
                "a game inside the client's steamapps library IS the game")
        require(verdict([ended("C:\\SteamLibrary\\steamapps\\common\\G\\Game.exe", 0xC0000005, ago: 60)]) == .end(status: 0xC0000005, file: "game.exe", lived: 30),
                "a game in another library folder is the game")
        require(verdict([ended("\\??\\C:\\Games\\G\\Game.exe", 0xC0000005, ago: 60)]) == .end(status: 0xC0000005, file: "game.exe", lived: 30), "an NT-form path is read the same way")
        require(verdict([ended("C:\\Games\\G\\CrashReportClient.exe", 0xC0000005, ago: 60, helper: true), ended(game, 0, ago: 80)]) == .none,
                "a crash reporter (the engine's helper flag) that crashed does not decide")
        require(verdict([ended(dying, 0xC0000005, ago: 60), runs("C:\\Games\\G\\UnityCrashHandler64.exe", helper: true)])
                == .end(status: 0xC0000005, file: "dyinglightgame.exe", lived: 30), "a running crash handler does not keep the session")
        require(verdict([]) == .none && verdict([dockhost]) == .none, "no program of the game: nothing")
        // one-time installers ran before the host: their (non-zero) ends come before the game's, which then decides
        let installer = "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Steamworks Shared\\_CommonRedist\\vcredist\\vc_redist.x86.exe"
        require(verdict([ended(installer, 0x8007054F, ago: 400), ended(installer, 0xC000001D, ago: 395), runs(dying)]) == .wait(status: 0xC000001D, file: "vc_redist.x86.exe", why: "game-still-runs"),
                "an installer's crash while the game runs: wait (never an end)")
        require(verdict([ended(installer, 0xC000001D, ago: 395), ended(dying, 0, ago: 20)]) == .none,
                "an installer's crash earlier and the game's normal end later: nothing")

        // ---- a launch that failed and no game ran (round 580): Spec Ops 2FRXXM / NEXHHM and Risen 2 9DBDZ6, probe-result 49
        func failed(_ processes: [DockProcess], untracked: Int = 0, complete: Bool = true,
                    host: R.DockHostResult = R.DockHostResult(result: 49, gameRan: false)) -> R.FailedLaunch? {
            hostAsked = 0
            return R.failedLaunch(DockProcessTable(processes: processes, untracked: untracked, complete: complete), places: places) {
                hostAsked += 1; return host
            }
        }
        let cegService = "C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe"
        let steamClient = "C:\\Program Files (x86)\\Steam\\steam.exe"
        // the processes of 2FRXXM after the host finished: Wine's own programs, Valve's client and its service, nothing of the game
        let ceg = [conhost, ended("C:\\windows\\system32\\dockhost.exe", 49, ago: 20), runs(client), runs(service), runs(steamClient)]
        require(failed(ceg) == R.FailedLaunch(result: 49, blocked: nil), "CEG error 49, no game process: a failed launch")
        require(hostAsked == 1, "the report is read once, when nothing of the game runs")
        require(R.failedLaunchGrace == 20, "the failure must stand 20 s")
        require(failed(ceg, host: R.DockHostResult(result: 34, gameRan: false)) == R.FailedLaunch(result: 34, blocked: nil)
                && failed(ceg, host: R.DockHostResult(result: 42, gameRan: false)) == R.FailedLaunch(result: 42, blocked: nil),
                "any non-zero result of a host that finished and never saw the game run (a sign-in, a registry step)")
        require(failed(ceg, host: R.DockHostResult(result: nil, gameRan: false)) == nil, "the host has not finished: nothing")
        require(failed(ceg, host: R.DockHostResult(result: 0, gameRan: false)) == nil, "the host finished well: nothing")
        require(failed(ceg, host: R.DockHostResult(result: 46, gameRan: true)) == nil && failed(ceg, host: R.DockHostResult(result: 0, gameRan: true)) == nil,
                "the game ran: that is the crash rule's, never this one")
        require(failed([runs(game)] + ceg) == nil && hostAsked == 0, "a program of the game runs: nothing, and the report is not read")
        require(failed([runs(game, helper: true)] + ceg) == R.FailedLaunch(result: 49, blocked: nil), "a running crash handler is not the game")
        require(failed([runs("")] + ceg) == nil, "a running program whose folder is unknown may be the game: nothing")
        require(failed([ended(dying, 0xC0000005, ago: 1)] + ceg, host: R.DockHostResult(result: 49, gameRan: false)) == R.FailedLaunch(result: 49, blocked: nil),
                "a program of the game that ended does not stop the rule (it is the host's report that decides)")
        require(failed(ceg, untracked: 1) == R.FailedLaunch(result: 49, blocked: "untracked-process"), "a spawn past the table: blocked")
        require(failed(ceg, complete: false) == R.FailedLaunch(result: 49, blocked: "table-incomplete"), "a cut table: blocked")
        require(failed([]) == R.FailedLaunch(result: 49, blocked: nil), "an empty table with a failed host: the failure stands")

        // ---- the engine's text, end to end (the file comes from the C table below)
        if CommandLine.arguments.count > 1, let text = try? String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8) {
            let table = R.parse(text)
            require(table.complete && table.untracked == 0, "the C table's own text is complete")
            let v = R.verdict(table, places: places, grace: 10) { hostDone }
            if case let .end(status, file, lived) = v {
                require(status == 0xC0000005 && file == "dyinglightgame.exe" && lived > 2.9 && lived < 3.3,
                        "the C table's report of the 32FEAT processes ends the session with c0000005 after 3 s (\(v))")
            } else {
                require(false, "the C table's report of the 32FEAT processes ends the session (\(v))")
            }
        }
        exit(failures == 0 ? 0 : 1)
    }
}
'''

# ------------------------------------------------------------------ 2. C: the child table
c_table = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef unsigned short WCHAR;
typedef struct { unsigned short Length; unsigned short MaximumLength; WCHAR *Buffer; } UNICODE_STRING;
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
''' + table + r'''
static int failed;
static void expect(int cond, const char *what) { printf("%s: %s\n", cond ? "PASS" : "FAIL", what); if (!cond) failed++; }
static int spawn_w(const WCHAR *wide, size_t n)
{
    static WCHAR buf[600];
    UNICODE_STRING u;
    memcpy(buf, wide, n * sizeof(WCHAR));
    u.Length = (unsigned short)(n * sizeof(WCHAR)); u.MaximumLength = u.Length; u.Buffer = buf;
    return ios_child_slot_take(&u);
}
static int spawn(const char *path)
{
    WCHAR wide[600];
    size_t n = strlen(path), i;
    for (i = 0; i < n; i++) wide[i] = (unsigned char)path[i];
    return spawn_w(wide, n);
}
static void *peb_of(int n) { return (void *)(uintptr_t)(0x10000 + 0x1000 * n); }
/* a child that registered with the server (bound its PEB) */
static int start(const char *path, int n)
{
    int slot = spawn(path);
    ios_child_slot_current = slot;
    ios_child_slot_bind_peb(peb_of(n));
    ios_child_slot_current = -1;
    return slot;
}
/* what process_exit_wrapper does for a child */
static void finish_child(int n, int status)
{
    ios_child_slot_exited(peb_of(n));
    if (!ios_child_slot_note_exit(peb_of(n), status)) { printf("FAIL: the note found no slot for peb %d\n", n); failed++; }
}
static void age_ended(const char *needle, double ended_ago, double lived)
{
    unsigned i;
    for (i = 0; i < ios_child_ended_count && i < IOS_CHILD_ENDED; i++)
        if (strstr(ios_child_ended[i].path, needle))
        {
            ios_child_ended[i].ended_at = ios_child_now() - ended_ago;
            ios_child_ended[i].started = ios_child_ended[i].ended_at - lived;
        }
}
static int count_lines(const char *text, char kind)
{
    int n = 0;
    const char *p = text;
    while (*p) { if (p[0] == kind && p[1] == '\t' && (p == text || p[-1] == '\n')) n++; p = strchr(p, '\n'); if (!p) break; p++; }
    return n;
}
int main(int argc, char **argv)
{
    static char text[32768];
    char small[96];
    int lines, i, s_host, s_con, s_game, s_helper;
    FILE *f;

    expect(madeira_child_report(text, sizeof text) == 0 && text[0] == 0, "an empty table: no lines");

    /* the processes of a Dock session (32FEAT): the host, its console host, the game */
    s_host = start("C:\\windows\\system32\\dockhost.exe", 1);
    s_con = start("C:\\windows\\system32\\conhost.exe", 2);
    s_game = start("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Dying Light\\DyingLightGame.exe", 3);
    s_helper = start("C:\\Games\\X\\CrashReportClient.exe", 4);
    lines = madeira_child_report(text, sizeof text);
    expect(lines == 4 && count_lines(text, 'R') == 4 && count_lines(text, 'E') == 0, "four running processes: four R lines");
    expect(strstr(text, "R\t0\t") && strstr(text, "\tC:\\Program Files (x86)\\Steam\\steamapps\\common\\Dying Light\\DyingLightGame.exe\n"),
           "an R line: the helper flag, the seconds since the start and the image path as it was spawned");
    expect(strstr(text, "R\t1\t") && strstr(text, "CrashReportClient.exe"), "the helper flag is set by the file-name rule");

    /* build 580 (Crash Bandicoot N. Sane Trilogy 3CH670): "crash" at the start of a game's name does not make it a crash reporter */
    {
        /* every helper name found in the 1421 intake logs, and the common ones of other engines (lower case, as stored) */
        static const char *const helpers[] = { "crashpad_handler.exe", "crashpad_handler.dll", "unitycrashhandler64.exe", "unitycrashhandler32.exe",
            "steamerrorreporter64.exe", "steamerrorreporter.exe", "crs-handler.exe", "rockstarerrorhandler.exe", "socialclubhelper.exe",
            "rockstarsteamhelper.exe", "crashreportclient.exe", "ue4crashreportclient.exe", "crash_reporter.exe", "crashreporter.exe",
            "crash.exe", "foo_crash.exe", "crashpad.exe", "crash_service.exe", "crashservice.exe", "crash-sender.exe", "crashsender1403.exe",
            "crashrpt.exe", "crashdump.exe", "crashdumper.exe", "crashlogger.exe", "crashmonitor.exe", "crashuploader.exe", "crash handler.exe",
            "steamwebhelper.exe", "chrome_helper.exe", "bugreporter.exe", "eacrashreporter.exe", "webhelper.exe", "gamehelper.exe", "errorhandler.exe" };
        static const char *const games[] = { "crashbandicootnsanetrilogy.exe", "crashlands.exe", "crashteamracingnitrofueled.exe", "crashday.exe",
            "crashbash.exe", "crashdive.exe", "crashforce.exe", "crash bandicoot.exe", "crash_bandicoot_n._sane_trilogy.exe", "dyinglightgame.exe",
            "game.exe", "cultofthelamb.exe", "bluecrashes.exe", "launcher.exe", "dockhost.exe", "conhost.exe" };
        unsigned k;
        for (k = 0; k < sizeof helpers / sizeof helpers[0]; k++)
        {
            char msg[128];
            snprintf(msg, sizeof msg, "%s is still a helper", helpers[k]);
            expect(ios_child_is_helper(helpers[k]), msg);
        }
        for (k = 0; k < sizeof games / sizeof games[0]; k++)
        {
            char msg[128];
            snprintf(msg, sizeof msg, "%s is not a helper", games[k]);
            expect(!ios_child_is_helper(games[k]), msg);
        }
    }

    /* the game ends with c0000005 on its boot thread: the status is kept even after the slot is released */
    finish_child(3, (int)0xC0000005u);
    lines = madeira_child_report(text, sizeof text);
    expect(lines == 4 && count_lines(text, 'E') == 1 && count_lines(text, 'R') == 3, "an ended process moves from R to E (no line twice)");
    expect(strstr(text, "E\t0\tc0000005\t") && strstr(text, "\tC:\\Program Files (x86)\\Steam\\steamapps\\common\\Dying Light\\DyingLightGame.exe\n"),
           "the E line carries the status in hex and the path");
    ios_child_slot_release(s_game);
    lines = madeira_child_report(text, sizeof text);
    expect(lines == 3 + 1 && count_lines(text, 'E') == 1 && strstr(text, "E\t0\tc0000005\t"), "the ring keeps the status after the boot thread released the slot");
    { char names[256]; expect(madeira_live_user_processes(names, sizeof names) == 0, "madeira_live_user_processes is as it was: the host, its console host and the helper do not count, the ended game neither"); }
    /* a second note for the same process, or one for a PEB the table does not know, changes nothing */
    expect(ios_child_slot_note_exit(peb_of(3), 7) == 0 && ios_child_slot_note_exit(peb_of(99), 7) == 0 && ios_child_slot_note_exit(NULL, 7) == 0,
           "the note says 0 for a PEB no slot holds (the caller logs it for an error status)");
    expect(madeira_child_report(text, sizeof text) == 4 && count_lines(text, 'E') == 1, "an unknown PEB, NULL and a process that already ended add nothing");

    /* the rest of the session ends: the host and its console host with 0 */
    finish_child(1, 0); finish_child(2, 0);
    ios_child_slot_release(s_host); ios_child_slot_release(s_con);
    expect(madeira_child_report(text, sizeof text) == 4 && count_lines(text, 'E') == 3, "three ended");
    /* age them as in the log: the game lived 3.1 s and ended 12 s ago, the host and its console host ended 5 s ago */
    age_ended("Dying", 12.0, 3.1);
    age_ended("dockhost", 5.0, 100.0);
    age_ended("conhost", 5.0, 100.0);
    ios_child_slot_release(s_helper);
    if (argc > 1)
    {
        madeira_child_report(text, sizeof text);
        f = fopen(argv[1], "w");
        if (f) { fputs(text, f); fclose(f); }
    }

    /* the text is cut, never overrun */
    expect(madeira_child_report(small, sizeof small) >= 0 && strlen(small) < sizeof small, "a small buffer is never overrun");
    expect(strstr(small, "T\n") != NULL, "a cut report ends with the T mark");
    expect(madeira_child_report(text, 8) >= 0 && strlen(text) < 8, "a tiny buffer is never overrun");
    expect(madeira_child_report(NULL, 100) == 0 && madeira_child_report(text, 4) == 0, "no buffer or a smaller one than a mark: nothing written");

    /* a path with unprintable characters and a tab is made printable; long paths are cut */
    {
        WCHAR odd[] = { 'C', ':', '\\', 0x00e9, 'x', '\t', 'y', '.', 'e', 'x', 'e' };
        int slot = spawn_w(odd, ARRAY_SIZE(odd));
        WCHAR wide[400];
        int j, slot2;
        expect(strchr(ios_child_slots[slot].path, '\t') == NULL && strstr(ios_child_slots[slot].path, "C:\\?x?y.exe"), "unprintable characters and tabs become '?'");
        ios_child_slot_release(slot);
        for (j = 0; j < 400; j++) wide[j] = 'a';
        slot2 = spawn_w(wide, 400);
        expect(strlen(ios_child_slots[slot2].path) == IOS_CHILD_PATH - 1, "a path longer than the field is cut to fit, terminated");
        ios_child_slot_release(slot2);
    }

    /* the Crash Bandicoot session of 3CH670: the game ends with c0000005 and is seen as the game (helper flag 0) in the report */
    {
        int s_cb = start("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Crash Bandicoot - N Sane Trilogy\\CrashBandicootNSaneTrilogy.exe", 5);
        lines = madeira_child_report(text, sizeof text);
        {
            const char *cb = strstr(text, "CrashBandicootNSaneTrilogy.exe");
            const char *ls = cb;
            while (ls && ls > text && ls[-1] != '\n') ls--;
            expect(cb && ls && !strncmp(ls, "R\t0\t", 4), "a running Crash Bandicoot is an R line with the helper flag 0");
        }
        { char names[256]; expect(madeira_live_user_processes(names, sizeof names) == 1 && strstr(names, "crashbandicoot"),
                                  "madeira_live_user_processes counts the running Crash Bandicoot as a program of the session"); }
        finish_child(5, (int)0xC0000005u);
        lines = madeira_child_report(text, sizeof text);
        expect(strstr(text, "E\t0\tc0000005\t") && strstr(strstr(text, "E\t0\tc0000005\t"), "CrashBandicootNSaneTrilogy.exe"),
               "its end is an E line with the helper flag 0 and c0000005 (DockGameEnd.verdict now sees it)");
        ios_child_slot_release(s_cb);
    }

    /* the ring: the last IOS_CHILD_ENDED ends, in order, however many */
    {
        int k;
        char name[64];
        for (k = 0; k < IOS_CHILD_ENDED + 20; k++)
        {
            int slot;
            snprintf(name, sizeof name, "C:\\Games\\X\\p%d.exe", k);
            slot = start(name, 100 + k);
            finish_child(100 + k, k);
            ios_child_slot_release(slot);
        }
        expect(ios_child_ended_count >= (unsigned)(IOS_CHILD_ENDED + 20), "more ends than the ring holds");
        madeira_child_report(text, sizeof text);
        /* 3 earlier ends + 84 = 87; the ring keeps the last 64: p20 ... p83 */
        expect(!strstr(text, "\\p0.exe") && !strstr(text, "\\p19.exe") && strstr(text, "\\p20.exe") && strstr(text, "\\p83.exe"), "only the last IOS_CHILD_ENDED are kept");
        expect(count_lines(text, 'E') == IOS_CHILD_ENDED, "exactly IOS_CHILD_ENDED E lines");
        expect(strstr(text, "\\p20.exe\n") < strstr(text, "\\p83.exe\n"), "oldest first");
    }

    /* spawns past the table are reported */
    for (i = 0; i < IOS_CHILD_SLOTS * 2; i++) spawn("C:\\Games\\X\\Worker.exe");
    madeira_child_report(text, sizeof text);
    expect(count_lines(text, 'U') == 1, "a U line for spawns the table could not track");
    return failed ? 1 : 0;
}
'''

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'table.c').write_text(c_table)
    exe = tmp / 'table'
    r = subprocess.run([CC, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(exe), str(tmp / 'table.c')],
                       capture_output=True, text=True)
    report_file = tmp / 'report.txt'
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the child table compiles')
    else:
        env_vars = dict(os.environ, ASAN_OPTIONS='symbolize=0')
        r = subprocess.run([str(exe), str(report_file)], capture_output=True, text=True, env=env_vars)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1
    # the Swift rules, with the starting screen's classification
    (tmp / 'screen.swift').write_text('import Foundation\n' + screen_rules)
    (tmp / 'rules.swift').write_text(rules_src)
    (tmp / 'main.swift').write_text(swift)
    sexe = tmp / 'rules'
    r = subprocess.run([SWIFTC, '-parse-as-library', '-o', str(sexe), str(tmp / 'screen.swift'), str(tmp / 'rules.swift'), str(tmp / 'main.swift')],
                       capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the Swift rules compile')
    else:
        args = [str(sexe)] + ([str(report_file)] if report_file.exists() else [])
        r = subprocess.run(args, capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1

# ------------------------------------------------------------------ 3. C: the bridge's wine_session_dock_game_ended
start_i = bridge.index('static volatile int g_wine_main_returned = 0;')
end_i = bridge.index('/* Export MADEIRA_DOCS_DIR before main(), while HOME is still the app container.')
exit_start = bridge.index('static uint64_t g_launch_exit = 0;')
exit_end = bridge.index('static char *g_prefix_path = NULL;')
end_start = bridge.index('/* What the boot thread does after __wine_main: the session is over, stop the wineserver.')
end_end = bridge.index('int wine_process_start(const char *prefix_path) {')
c_end = r'''
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static pthread_t g_wine_thread;
static volatile int g_wine_running = 0;
volatile int g_madeira_srv_fgetpath = 0, g_madeira_srv_eco_follow = 0, g_madeira_srv_direct = 0, g_madeira_srv_idle_us = 0, g_madeira_srv_log_all = 0;
static int g_stops;
static int g_wine_exit_code = 0;   /* wine_process_exit_code()'s plain global (the boot thread copies the thread-local status into it) */
_Thread_local volatile int wine_ios_exit_code = 0;
void wineserver_stop(void) { g_stops++; }
void wine_session_note_launch_exit(int status);
int wine_launched_exit_stuck(int32_t *code, double *seconds);
void wine_session_abandon(void);
int wine_session_dock_game_ended(int32_t status);
int wine_crash_exit_status(uint32_t *status);
''' + bridge[exit_start:exit_end] + bridge[start_i:end_i] + bridge[end_start:end_end] + r'''
static int failed;
static void expect(int cond, const char *what) { printf("%s: %s\n", cond ? "PASS" : "FAIL", what); if (!cond) failed++; }
static void new_session(void)
{
    g_wine_main_returned = 0; g_launch_exit_claim = 0; g_launch_exit_seen = 0; g_launch_exit_boot = 0;
    g_launch_exit_code = 0; g_launch_exit_ns = 0; g_session_end_claim = 0; g_wine_running = 1; g_stops = 0;
    wine_exit_status_reset();
}
int main(void)
{
    uint32_t status = 0;
    int32_t code = -1;
    double seconds = -1;
    g_app_progname = strdup("Madeira");
    g_wine_thread = pthread_self();

    /* no session running: nothing */
    g_wine_running = 0; g_stops = 0; g_session_end_claim = 0;
    expect(wine_session_dock_game_ended((int32_t)0xC0000005u) == 0 && g_stops == 0 && !wine_crash_exit_status(&status), "no session running: nothing happens");

    /* Dying Light 32FEAT: the game's status becomes the session's exit status and the end work runs */
    new_session();
    setenv("MADEIRA_WER", "1", 1); setenv("MADEIRA_KEEP_ME", "1", 1); g_madeira_srv_direct = 2;
    expect(!wine_crash_exit_status(&status), "before: no exit status");
    expect(wine_session_dock_game_ended((int32_t)0xC0000005u) == 1, "the session ends");
    expect(g_stops == 1 && g_wine_running == 0, "the wineserver is stopped and the running flag cleared (the library's finish() follows)");
    expect(wine_crash_exit_status(&status) == 1 && status == 0xC0000005u, "the library's exit report and auto-tune read the game's status (c0000005)");
    expect(!getenv("MADEIRA_WER") && getenv("MADEIRA_KEEP_ME") && g_madeira_srv_direct == 0, "the per-launch switches are cleared, nothing else");
    expect(wine_session_dock_game_ended((int32_t)0xC0000409u) == 0 && g_stops == 1, "a second call does nothing (the end work runs once)");
    expect(wine_crash_exit_status(&status) == 1 && status == 0xC0000005u, "and the first status stays");
    expect(!wine_launched_exit_stuck(&code, &seconds), "not 'stuck' afterwards");
    wine_session_end();
    expect(g_stops == 1, "a boot thread that returns late does not run the end work again");

    /* an exit status that was already recorded is not replaced */
    new_session();
    wine_launched_process_did_exit((int)0xC0000409u);
    expect(wine_session_dock_game_ended((int32_t)0xC0000005u) == 1 && wine_crash_exit_status(&status) && status == 0xC0000409u,
           "a status the initial program already left stays");

    /* the breakpoint exit and a negative exit code are recorded too */
    new_session();
    expect(wine_session_dock_game_ended((int32_t)0x80000003u) == 1 && wine_crash_exit_status(&status) && status == 0x80000003u, "0x80000003 is recorded");
    new_session();
    expect(wine_session_dock_game_ended(-1) == 1 && wine_crash_exit_status(&status) && status == 0xFFFFFFFFu, "-1 is recorded");
    /* a new session forgets it */
    new_session();
    expect(!wine_crash_exit_status(&status), "a new session starts without an exit status");
    return failed ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    cp = Path(tmp) / 'end.c'
    cp.write_text(c_end)
    exe = Path(tmp) / 'end'
    r = subprocess.run([CC, '-std=gnu11', '-Wall', '-Werror', '-o', str(exe), str(cp), '-lpthread'], capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the bridge\'s Session end section compiles')
    else:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1

# ------------------------------------------------------------------ 4. auto-tune: the rules and the log
FIX = root / 'tests/fixtures/autotune'
fixture_dl = FIX / 'dying-light-dock-game-end-2026-10-21.log'
require(fixture_dl.exists(), 'the fixture (32FEAT) is there')
if SWIFTC and fixture_dl.exists():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        exe = tmp / 'replay'
        b = subprocess.run([SWIFTC, '-O', '-parse-as-library', '-swift-version', '5', '-o', str(exe), str(app / 'AutoTuneCore.swift'),
                            str(app / 'AutoTuneRules.generated.swift'), str(root / 'tests/host/autotune_replay.swift')], capture_output=True, text=True)
        if b.returncode:
            sys.stdout.write(b.stderr[-3000:])
            require(False, 'the auto-tune replay compiles')
        else:
            def replay(name, lines, end):
                p = tmp / (name + '.log')
                p.write_text(lines)
                r = subprocess.run([str(exe), '--end', json.dumps(end), str(p)], capture_output=True, text=True)
                return json.loads(r.stdout.splitlines()[0]) if r.stdout.strip() else {}
            dl = fixture_dl.read_text()
            crash = {'crashStatus': 3221225477, 'presents': 0, 'seconds': 80}
            a = replay('a', dl, crash)
            require(a.get('outcome') == 'crashed', 'auto-tune sees the Dock session as crashed when the library hands it the game\'s status (outcome %s)' % a.get('outcome'))
            require(a.get('matched') == ['locale-en-us-ansi', 'dock-game-died-session-open'] and a.get('next') == ['env.LC_ALL = en_US.UTF-8'],
                    'Dying Light: the locale experiment stays the best match, the hang rule comes after it (%s)' % a.get('matched'))
            nostatus = replay('a2', dl, {})
            require(nostatus.get('outcome') == 'crashed', 'and from the log alone (an old build) the session also reads as crashed')
            b2 = replay('b', dl + '[12:00:30.000] [INFO] [dock-game-end] dyinglightgame.exe ended with status 0xc0000005 after 3 s, the host finished and nothing of the game runs: ending the session\n', crash)
            require(b2.get('matched') == ['locale-en-us-ansi'], 'a log that shows the new path ran does not match the hang rule (%s)' % b2.get('matched'))
            no_locale = ''.join(l for l in dl.splitlines(True) if 'type=11 id=1254' not in l)
            c2 = replay('c', no_locale, {'killed': True})
            require(c2.get('matched') == ['dock-game-died-session-open'] and c2.get('status') == 'known',
                    'a Dock game that died without another rule: "known", fixed in 579, update BCD (%s %s)' % (c2.get('matched'), c2.get('status')))
            no_over = ''.join(l for l in no_locale.splitlines(True) if 'launch-game-ended=1' not in l)
            d2 = replay('d', no_over, {'killed': True})
            require('dock-game-died-session-open' not in d2.get('matched', []), 'without the host\'s "game ended" the rule does not match')
            no_exit = ''.join(l for l in no_locale.splitlines(True) if 'process_exit_wrapper(-10737' not in l)
            e2 = replay('e', no_exit, {'killed': True})
            require('dock-game-died-session-open' not in e2.get('matched', []), 'without a process that ended with an error the rule does not match')

            # a Dock launch that failed with no game (Spec Ops NEXHHM, build 579)
            fixture_fl = FIX / 'spec-ops-the-line-dock-launch-failed-2026-10-21.log'
            require(fixture_fl.exists(), 'the fixture (NEXHHM) is there')
            if fixture_fl.exists():
                fl = fixture_fl.read_text()
                f1 = replay('f1', fl, {'killed': True})
                require(f1.get('matched') == ['dock-launch-failed-session-open'] and f1.get('status') == 'known',
                        'a Dock session whose host failed and no game ran: "known", fixed in 580, update BCD (%s %s)' % (f1.get('matched'), f1.get('status')))
                f2 = replay('f2', fl + '[12:00:30.000] [INFO] [dock-game-end] the host reported the launch failed (code 49) and no game process ran: ending the session\n', {'killed': True})
                require('dock-launch-failed-session-open' not in f2.get('matched', []), 'a log that shows the new path ran does not match the rule')
                f3 = replay('f3', fl + '[12:00:30.000] [INFO] [dock-report] launch-game-running=1\n', {'killed': True})
                require('dock-launch-failed-session-open' not in f3.get('matched', []), 'a game that ran is the crash rule\'s, not this one')
                f4 = replay('f4', fl.replace('probe-result=49', 'probe-result=0'), {'killed': True})
                require('dock-launch-failed-session-open' not in f4.get('matched', []), 'a host that finished well does not match')
                f5 = replay('f5', ''.join(l for l in fl.splitlines(True) if '[dock-report] probe-result=' not in l), {'killed': True})
                require('dock-launch-failed-session-open' not in f5.get('matched', []), 'without the host\'s result the rule does not match')
                f6 = replay('f6', fl + '[frontend] returned to library\n', {'killed': True})
                require('dock-launch-failed-session-open' not in f6.get('matched', []), 'a session that returned to the library on its own does not match')

# ------------------------------------------------------------------ 5. source checks
require(rules_src.startswith('// SPDX-License-Identifier: GPL-3.0-only\n// Copyright 2026 thebcd69 (BCD)\n// BCD Material:'),
        'DockGameEnd.swift: BCD licence header')
require('/* DockGameEnd.swift in Sources */,' in project and 'path = DockGameEnd.swift' in project and project.count('DockGameEnd.swift') == 6,
        'DockGameEnd.swift is built by the Xcode project (build file, file reference, group and sources entries)')
code = '\n'.join(l for l in rules_src.splitlines() if not l.strip().startswith('//'))
require(re.search(r'\bView\b|SwiftUI|MadeiraConfig|MadeiraDock\b|LogStore|getenv|madeira_|wine_', code) is None, 'the rules are Foundation-only and pure (the engine\'s and the report\'s answers are passed in)')
additions = '\n'.join([process[process.index('static void ios_child_path_text('):process.index('static int ios_child_slot_take(')],
                       block(process, 'int ios_child_slot_note_exit( void *peb, int status )'),
                       block(process, 'int madeira_child_report( char *buf, int len )')])
require(not re.findall(r'"[^"\n]*\.exe"', code) and not re.findall(r'"[^"\n]*\.exe"', additions)
        and not re.findall(r'"[^"\n]*\.exe"', block(bridge, 'int wine_session_dock_game_ended(int32_t status)')),
        'no program names in the rules, the table additions or the bridge function: the game is found by where it lives')

poll = block(library, 'private func poll()')
require(poll.count('watchDockGameEnd()') == 1 and poll.index('watchProgramEnd()') < poll.index('watchDockGameEnd()')
        < poll.index('} else if sawProcess && wineserver_is_running() == 0 { finish() }'),
        'poll: the Dock watch runs only while the engine says Windows runs, after the 578 watch; the end is still finish()')
watch = block(library, 'private func watchDockGameEnd()')
require('guard !endedByDockGame, DockStartScreen.shared.active, MadeiraConfig.flag("MADEIRA_DOCK_GAME_END") else { return }' in watch,
        'the watch runs only for a Dock session, once, and not when env.MADEIRA_DOCK_GAME_END = 0')
require('DockGameEndRules.grace(MadeiraConfig.get("env.MADEIRA_DOCK_GAME_END_GRACE"))' in watch and 'places: .standard' in watch,
        'the grace comes from its key; programs are classed by the starting screen\'s places')
require('launch-game-running' in watch and 'launch-game-ended' in watch and 'report.result != nil' in watch
        and watch.index('MadeiraDock.pollReport()') > watch.index('DockGameEndRules.verdict('), 'the host\'s report says: game ran, game over, host finished (read lazily)')
require('DispatchQueue.global(qos: .userInitiated).async { _ = wine_session_dock_game_ended(Int32(bitPattern: status)) }' in watch
        and 'endedByDockGame = true' in watch and '[dock-game-end]' in watch, 'it ends the session off the main queue, once, and logs [dock-game-end]')
require('endedByDockGame = false; dockEndNote = ""' in block(library, 'func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)'),
        'a new session forgets the last one\'s watch state')
failed_watch = block(library, 'private func watchDockFailedLaunch(_ table: DockProcessTable)')
require('DockGameEndRules.failedLaunch(table, places: .standard)' in failed_watch and 'report.fields["launch-game-running"] == "1"' in failed_watch
        and 'report.result' in failed_watch and 'DockGameEndRules.failedLaunchGrace' in failed_watch,
        'the failed-launch watch asks the rule, reads the host\'s result and whether the game ran, and waits the grace')
require(failed_watch.index(' s unless one starts') < failed_watch.index('endedByDockGame = true')
        < failed_watch.index('DispatchQueue.global(qos: .userInitiated).async { _ = wine_session_dock_game_ended(Int32(failed.result)) }'),
        'it says so when the failure is first seen, then ends the session once, off the main queue')
require(watch.count('watchDockFailedLaunch(table)') == 2 and watch.index('case .none:') < watch.index('watchDockFailedLaunch(table)') < watch.index('case .end('),
        'the watch asks the failed-launch rule when the crash rule has nothing to end (none, wait), never after its own end')
require('endedByDockGame = false; dockEndNote = ""; dockFailedAt = nil; endedByDockFailure = nil' in block(library, 'func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)'),
        'a new session forgets the failed-launch state too')
require('if let words = endedByDockFailure {' in block(library, 'private func exitReport() -> String?')
        and block(library, 'private func exitReport() -> String?').index('guard !quitRequested') < block(library, 'private func exitReport() -> String?').index('endedByDockFailure'),
        'the exit report shows the host\'s words for a session the library ended for a failed launch (not after the user quit)')
report = block(library, 'private func exitReport() -> String?')
require('wine_crash_exit_status(&status)' in report and 'The game quit with Windows error' in report,
        'the exit report is the 578 one: it reads wine_crash_exit_status, which the Dock end fills')
for name in ('madeira_child_report', 'wine_session_dock_game_ended', 'wine_session_dock_game_quit'):
    require(name in header, '%s is declared for Swift (WineProcessBridge.h)' % name)
# round 624 (Cuphead PDKQP7): the player's Close
require('let asked = quitRequested' in watch and 'anyStatus: asked' in watch
        and 'let grace = asked ? DockGameEndRules.graceRange.lowerBound' in watch,
        'after the player\'s Close the watch ends a game that ended with any status, after the shortest grace')
require('if DockGameEndRules.isCrash(status) {' in watch
        and watch.index('if DockGameEndRules.isCrash(status) {') < watch.index('wine_session_dock_game_ended(Int32(bitPattern: status))')
        < watch.index('DispatchQueue.global(qos: .userInitiated).async { _ = wine_session_dock_game_quit(Int32(bitPattern: status)) }'),
        'a crash status ends the session as before; any other status (after Close) through the quit call, off the main queue')
require('(the player had asked to close)' in watch, 'the [dock-game-end] line says the player had asked')
# round 625 (N-S18): a short clean exit before any frame
require('let rendered = !MadeiraConfig.flag("MADEIRA_DOCK_SHORT_EXIT_END") || madeira_get_present_count() >= launchPresent + 3' in watch
        and 'anyStatus: asked, rendered: rendered)' in watch,
        'the watch passes whether frames reached the screen (the starting screen\'s own test); the key off passes rendered = true')
short_branch = watch[watch.index('if !asked && !DockGameEndRules.isCrash(status) {'):]
short_branch = short_branch[:short_branch.index('                return\n')]
require('endedByDockShortExit = ' in short_branch and 'before it showed a picture. If it showed a message, that is the reason.' in short_branch
        and 'wine_session_dock_game_left(Int32(bitPattern: status))' in short_branch and 'wine_session_dock_game_ended' not in short_branch
        and 'no picture was drawn' in short_branch,
        'a short clean exit ends the session with the reason in words, through the left call (not a crash status), and logs it')
require(watch.index('if !asked && !DockGameEndRules.isCrash(status) {') < watch.index('if DockGameEndRules.isCrash(status) {'),
        'the short-exit branch comes before the crash and quit calls')
require('if let words = endedByDockShortExit {' in report and report.index('guard !quitRequested') < report.index('endedByDockShortExit'),
        'the exit report shows the short-exit words (not after the user quit)')
require('endedByDockFailure = nil; endedByDockShortExit = nil' in block(library, 'func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)'),
        'a new session forgets the short-exit words')
left_fn = block(bridge, 'int wine_session_dock_game_left(int32_t status)')
require('if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;' in left_fn
        and 'g_launch_exit' not in left_fn and 'wine_session_end();' in left_fn and 'g_wine_exit_code' in left_fn
        and 'asked to close' not in left_fn,
        'the left call acts only on a running session and does the end work without making the status a crash status or claiming a Close')
require('int wine_session_dock_game_left(int32_t status);' in header, 'wine_session_dock_game_left is declared for Swift')
quit_fn = block(bridge, 'int wine_session_dock_game_quit(int32_t status)')
require('if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;' in quit_fn
        and 'g_launch_exit' not in quit_fn and 'wine_session_end();' in quit_fn and 'g_wine_exit_code' in quit_fn,
        'the quit call acts only on a running session and does the end work without making the status a crash status')
require(not re.findall(r'"[^"\n]*\.exe"', quit_fn), 'no program names in the quit call')

wrapper = block(server, 'void process_exit_wrapper( int status )')
slot_branch = wrapper[:wrapper.index('    else\n    {')]
init_branch = wrapper[wrapper.index('    else\n    {'):]
require('ios_child_slot_note_exit( dead_peb, status )' in slot_branch and slot_branch.index('ios_child_slot_exited( dead_peb );') < slot_branch.index('ios_child_slot_note_exit( dead_peb, status )')
        and '[child-table] a process ended with status 0x%x and the table has no slot for it' in slot_branch,
        'a child\'s exit leaves its status in the table\'s ring, right after its slot is marked ended')
require('ios_child_slot_note_exit' not in init_branch and 'wine_launched_process_did_exit( status )' in init_branch, 'the initial process\'s branch is as it was')
note_fn = block(process, 'int ios_child_slot_note_exit( void *peb, int status )')
require('malloc' not in note_fn and 'wine_log_write' not in note_fn and 'ERR(' not in note_fn and 'dprintf' not in note_fn and 'pthread_mutex_lock( &ios_child_lock )' in note_fn,
        'the ring write takes the table lock and allocates and logs nothing')
report_fn = block(process, 'int madeira_child_report( char *buf, int len )')
require('wine_log_write' not in report_fn and 'ERR(' not in report_fn and 'dprintf' not in report_fn and 'fprintf' not in report_fn,
        'the report logs nothing: paths go nowhere but the library\'s buffer')

check_start = block(bridge, 'int wine_session_dock_game_ended(int32_t status)')
require('if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;' in check_start
        and check_start.index('__atomic_compare_exchange_n(&g_launch_exit') < check_start.index('wine_session_end();'),
        'the bridge function acts only on a running session, records the status first, then runs the end work')

# keys
for key, default, kind in (('env.MADEIRA_DOCK_GAME_END', '1', 'bool'), ('env.MADEIRA_DOCK_GAME_END_GRACE', '10', 'int'),
                           ('env.MADEIRA_DOCK_SHORT_EXIT_END', '1', 'bool')):
    m = re.search(r'"%s": \{[^}]*?"kind": "%s", "default": "%s"' % (re.escape(key), kind, default), catalog, re.S)
    require(m is not None, '%s is in the settings catalog generator (%s, default %s); ConfigCatalog.generated.swift is regenerated in the full tree' % (key, kind, default))
# auto-tune rules
for rid in ('dock-game-died-session-open',):
    chunk = rules_py[rules_py.index('"id": "%s"' % rid):]
    chunk = chunk[:chunk.index('\n    },\n')]
    require('"lines": []' in chunk and '"fixedIn": 579' in chunk and '"none"' in chunk, 'rule %s: a code fix (no lines, fixedIn 579) with a guard for the new path' % rid)
for rid in ('dock-game-quit-session-open',):
    chunk = rules_py[rules_py.index('"id": "%s"' % rid):]
    chunk = chunk[:chunk.index('\n    },\n')]
    require('"lines": []' in chunk and '"fixedIn": 624' in chunk and '"session": "dock"' in chunk and '"minStage": 4' in chunk
            and 's("[dock-game-end]")' in chunk and 's("[frontend] graceful close requested")' in chunk,
            'rule %s: a code fix for Dock sessions that played (no lines, fixedIn 624, minStage 4) with a guard for the new path' % rid)
for rid in ('dock-launch-failed-session-open',):
    chunk = rules_py[rules_py.index('"id": "%s"' % rid):]
    chunk = chunk[:chunk.index('\n    },\n')]
    require('"lines": []' in chunk and '"fixedIn": 580' in chunk and '"none"' in chunk and '"session": "dock"' in chunk,
            'rule %s: a code fix for Dock sessions (no lines, fixedIn 580) with a guard for the new path' % rid)
require('"session": "dock"' in rules_py[rules_py.index('"id": "dock-game-died-session-open"'):][:3500].split('\n    },\n')[0]
        and '"priority": 13' in rules_py[rules_py.index('"id": "dock-game-died-session-open"'):][:3500].split('\n    },\n')[0],
        'the Dock rule is for Dock sessions and comes after the locale experiment (priority 12)')
require('tests/host/check-dock-game-end.py' in workflow, 'CI runs this check')

print('check-dock-game-end: ' + ('PASS' if failures == 0 else 'FAIL (%d)' % failures))
sys.exit(1 if failures else 0)
