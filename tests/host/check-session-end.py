#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The end of a session whose program quit on a thread other than the boot thread, on the host (round 577).

GTA IV (2026-10-21, build 576, log gta4-freeze): the game's thread 00b8 died of an uncaught C++ exception and
called ExitProcess(3). The session ends when __wine_main returns on the boot thread (0024); exit() long-jumps only
on that thread (build/ntdll-unix/shims/wine_ios_exit.h), so __wine_main never returned, g_wine_running stayed 1,
and the library kept the last frame for good. Then the app crashed in iOS: getprogname() pointed into the boot
thread's stack (env_ios.c set_process_name -> setprogname keeps the pointer), which was gone.

1. Swift: SessionEndRules (Library.swift): the verdict (wait / keep / leave) and the user's words.
2. C: process_ios.c's child table: Wine's own processes and helpers do not count, a child that ended (also off its
   boot thread) does not count, a child the table could not track does.
3. C: WineProcessBridge.m's "Session end": the stuck test, the once-only end work, the program name given back.
4. Source checks: the wiring (hook, poll, exit report, headers), no game names, the normal path untouched.

Synthetic data only: no Wine, no game.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

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


library = (app / 'Library.swift').read_text(encoding='utf-8')
bridge = (app / 'WineProcessBridge.m').read_text(encoding='utf-8')
header = (app / 'WineProcessBridge.h').read_text(encoding='utf-8')
process = (root / 'build/ntdll-unix/process_ios.c').read_text(encoding='utf-8')
server = (root / 'build/ntdll-unix/server_ios.c').read_text(encoding='utf-8')
signal = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text(encoding='utf-8')
shim = (root / 'build/ntdll-unix/shims/wine_ios_exit.h').read_text(encoding='utf-8')
env = (root / 'build/ntdll-unix/env_ios.c').read_text(encoding='utf-8')


def block(text, header_text):
    p = text.index(header_text)
    a = text.index('{', p)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[p:b]


# ------------------------------------------------------------------ 1. Swift
rules = library[library.index('// MARK: - Rules (session end)'):library.index('enum LibraryError: LocalizedError {')]
require(re.search(r'\bView\b|SwiftUI|MadeiraConfig|LogStore|getenv|wine_', rules.replace('// MARK: - Rules (session end)', '')) is None,
        'SessionEndRules is Foundation-only and pure (the engine\'s answers are passed in)')
swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
@main struct Checks {
    static func main() {
        typealias R = SessionEndRules
        // GTA IV: the program was gone for 21 s, no process of its own left.
        require(R.verdict(secondsSinceExit: 21, userProcesses: 0, dockSession: false) == .leave, "gone for 21 s, nothing left: leave")
        require(R.verdict(secondsSinceExit: R.grace, userProcesses: 0, dockSession: false) == .leave, "exactly the grace time: leave")
        require(R.verdict(secondsSinceExit: R.grace - 0.1, userProcesses: 0, dockSession: false) == .wait,
                "inside the grace time: wait (the boot thread may still come back by itself)")
        require(R.verdict(secondsSinceExit: 0, userProcesses: 0, dockSession: false) == .wait, "just ended: wait")
        // A launcher whose game still runs, a game with a helper of its own: the session goes on.
        require(R.verdict(secondsSinceExit: 60, userProcesses: 1, dockSession: false) == .keep(1), "a process it started still runs: keep")
        require(R.verdict(secondsSinceExit: 600, userProcesses: 3, dockSession: false) == .keep(3), "however long: keep")
        require(R.verdict(secondsSinceExit: 2, userProcesses: 1, dockSession: false) == .wait, "inside the grace time with a child: still wait")
        // A BCD Dock session is never ended from here.
        require(R.verdict(secondsSinceExit: 600, userProcesses: 0, dockSession: true) == .wait, "Dock: left as it is")
        require(R.verdict(secondsSinceExit: 600, userProcesses: 5, dockSession: true) == .wait, "Dock with children: left as it is")
        require(R.grace >= 2 && R.grace <= 10, "the grace time is a few seconds")

        // The words.
        require(R.message(exitCode: 0) == nil, "exit code 0 (a normal quit): no message")
        require(R.message(exitCode: 3) == "The game closed (exit code 3). If it keeps happening, open its page and send its log.",
                "exit code 3: short, factual, points at the game page's Send Log")
        require(R.message(exitCode: 1)?.contains("exit code 1") == true, "exit code 1")
        require(R.message(exitCode: 70000)?.contains("0x11170") == true, "a large code reads in hex")
        require(R.message(exitCode: -2)?.contains("0xFFFFFFFE") == true, "a negative code reads as the 32-bit value")
        require(!(R.message(exitCode: 3) ?? "").contains("GTA"), "no game names")

        // Round 624 (agent E1 S12-b, S13-d): an NTSTATUS exit names what the log already knows (ExitWords).
        typealias X = ExitWords
        let grid = "0024:err:module:loader_init \"other.dll\" failed to initialize, aborting\n0024:err:module:loader_init \"steam_api.dll\" failed to initialize, aborting\n"
        require(X.failedLibrary(inTail: grid) == "steam_api.dll", "GRID 2 (26MVCW): the last library whose start failed")
        require(X.failedLibrary(inTail: "loader_init \"x.dll\" is fine\n") == nil && X.failedLibrary(inTail: "") == nil, "no such line: no name")
        require(X.libraryStart("steam_api.dll") == "The game quit with Windows error 0xC0000142 (a program library failed to start). Windows could not "
                + "start steam_api.dll. To report the problem, export the diagnostic log.", "0xC0000142 with the name")
        require(X.libraryStart(nil) == "The game quit with Windows error 0xC0000142 (a program library failed to start). To report the problem, "
                + "export the diagnostic log.", "0xC0000142 without a name: the kind, no guess")
        let hog = "[OK] ml1040: executable window [0x140000000,+128MB) held since image load\n[OK] [exe-window] executable window widened to "
            + "[0x140000000,+448MB) for this game (exe-window-mb)\nml985: map_image_view size=0x4b000 base=0x140000000 info_base=0x140000000\n"
            + "ml985: map_image_view size=0x1c3ad000 base=0x140000000 info_base=0x140000000 map_addr=0 flags=0 charact=0x23\n"
        require(X.fixedProgramMiB(inTail: hog) == 452 && X.windowMB(inTail: hog) == 448, "Hogwarts Legacy (GBQRKH): a 452 MB program, a 448 MB window")
        require(X.windowTooSmall(programMiB: 452, windowMB: 448) == "The game quit with Windows error 0xC0000018 (conflicting addresses). The "
                + "game's program is 452 MB and needs that much room at its fixed address, but its settings keep 448 MB for it. Set exe-window-mb "
                + "to 512 in the game's settings, or tap Reset to Recommended after updating BCD.", "...and the words say so, with 512")
        require(X.windowTooSmall(programMiB: 419, windowMB: 128)?.contains("Set exe-window-mb to 448") == true, "Dead Space (2023): 448")
        require(X.windowTooSmall(programMiB: 300, windowMB: 448) == nil, "a program that fits: no such words (the conflict is something else)")
        require(X.fixedProgramMiB(inTail: "ml985: map_image_view size=0x1c3ad000 base=0x180000000 info_base=0x180000000\n") == nil,
                "a program at another base: not this case")
        require(X.windowMB(inTail: "[0x140000000,+abcMB)") == nil && X.fixedProgramMiB(inTail: "") == nil, "unreadable lines: nothing")

        // Round 579: a Steam game started without Dock that found no Steam (SteamStartHint), text only.
        typealias H = SteamStartHint
        require(H.applies(directSteamStart: true, quitRequested: false, seconds: 2), "direct start, ended by itself in 2 s: the log is read")
        require(!H.applies(directSteamStart: false, quitRequested: false, seconds: 2), "a BCD Dock session (no local program): never")
        require(!H.applies(directSteamStart: true, quitRequested: true, seconds: 2), "the player quit: never")
        require(!H.applies(directSteamStart: true, quitRequested: false, seconds: H.maxSeconds + 1), "a long session: never (the game ran)")
        require(H.applies(directSteamStart: true, quitRequested: false, seconds: H.maxSeconds), "exactly the limit still reads the log")
        require(!H.applies(directSteamStart: true, quitRequested: false, seconds: -1), "a negative time (clock change): never")
        func said(_ lines: [String], chunk: Int = 1 << 20) -> Bool {
            var scan = H.LogScan()
            let bytes = Array((lines.joined(separator: "\n") + "\n").utf8)
            var at = 0
            while at < bytes.count { let to = min(bytes.count, at + chunk); scan.feed(Array(bytes[at..<to])); at = to }
            scan.finish()
            return scan.said
        }
        let start = "[05:47:24.100] [INFO] [steam-start] app=3375780 direct source=steam args=0 folder=program"
        require(said([start, "PackFile Mount failed(a)", "SteamAPI_Init() failed.", "0024:err:process:NtTerminateProcess"]),
                "the game's own SteamAPI_Init() failed line after a direct start is found")
        require(said([start, "Steam must be running to play this game"]), "a game's own 'Steam must be running' line is found")
        require(said([start, "0024:err:seh:ios_steam_api_diag [steam-api] steam_api found no running Steam; process 0020 sees:"]),
                "BCD's own steam_api diagnostic line is found")
        require(said([start, "[S_API] SteamAPI_Init(): SteamAPI_IsSteamRunning() did not locate a running instance of Steam.."]),
                "Valve's steam_api text is found")
        require(!said(["SteamAPI_Init() failed.", "x"]), "a failure line with no direct start before it (a Dock session's child) is not")
        require(!said([start, "wine: all is well", "[xp] 1 2 3"]), "a direct start whose log says nothing about Steam: no")
        require(!said(["SteamAPI_Init() failed.", start, "ready"]), "an earlier session's failure line does not count after a later direct start")
        require(said([start, "SteamAPI_Init() failed.", start, "x"]) == false, "the LAST direct start resets the answer")
        require(said(["[steam-start] app=1 mode=game", "SteamAPI_Init() failed."]) == false, "choosing the mode on the page is not a start")
        require(said([start, "SteamAPI_Init() failed."], chunk: 7), "lines cut across chunks of 7 bytes are put back together")
        require(said([start, "SteamAPI_Init() failed."], chunk: 1), "byte by byte")
        require(said([start, "SteamAPI_Init() failed."]) == said([start, "SteamAPI_Init() failed."], chunk: 13), "the chunk size changes nothing")
        // Round 624 (agent C S8, Mafia: Definitive Edition 49T3ST): a program added by hand that ended by itself is read too, with its own words.
        require(H.applies(directSteamStart: false, localProgram: true, quitRequested: false, seconds: 3),
                "a program added by hand that ended by itself in 3 s: the log is read")
        require(!H.applies(directSteamStart: false, localProgram: true, quitRequested: true, seconds: 3)
                && !H.applies(directSteamStart: false, localProgram: true, quitRequested: false, seconds: H.maxSeconds + 1),
                "...not when the player quit, nor after a long session")
        func saidLocal(_ lines: [String]) -> Bool {
            var scan = H.LogScan(local: true)
            scan.feed(Array((lines.joined(separator: "\n") + "\n").utf8)); scan.finish()
            return scan.said
        }
        let mafia = ["[23:00:49.674] [INFO] [bcd-ui] play local",
                     "[23:00:49.679] [INFO] [bcd] library launch mafiadefinitiveedition.exe: avx=1 wine-vcrt=1 nvidia=0 game-config=1 metalfx=off",
                     "0024:err:seh:ios_exc_dump_str [exc] ml812   OutputDebugStringA: \"[S_API FAIL] SteamAPI_Init() failed; SteamAPI_IsSteamRunning() failed..\"",
                     "[steam-api] steam_api found no running Steam; process 0020 sees:"]
        require(saidLocal(mafia), "49T3ST's own lines: a program added by hand said it found no Steam")
        require(!said(mafia), "...which the direct-start scan does not read (no direct-start line)")
        require(!saidLocal(mafia + ["wine: Unhandled page fault on read access to 0000003C at address 0000003C (thread 0024)"]),
                "a crash after the Steam line is the likelier end: no Steam words (Hello Neighbor TAW359)")
        require(!saidLocal([mafia[1], "[18:10:02.000] [INFO] [epic-launch] start app=gta5", mafia[2]]),
                "an Epic start (its launcher probes Steam itself): no Steam words")
        require(!saidLocal([mafia[2], "x"]), "a Steam line before any library launch: no")
        require(saidLocal([mafia[2], mafia[1], mafia[3]]) && !saidLocal([mafia[1], mafia[2], mafia[1], "ready"]),
                "the LAST library launch counts")
        require(H.localText.contains("Steam tab") && H.localText.contains("If this copy needs Steam") && !H.localText.contains("Mafia"),
                "the words for a program added by hand: the Steam tab, conditional, no game name")
        var noEnd = H.LogScan()
        noEnd.feed(Array((start + "\nSteamAPI_Init() failed.").utf8)); noEnd.finish()
        require(noEnd.said, "a last line without a newline is read at the end")
        var longLine = H.LogScan()
        longLine.feed(Array((start + "\n" + String(repeating: "x", count: 100_000) + "\nSteamAPI_Init() failed.\n").utf8)); longLine.finish()
        require(longLine.said, "a line far longer than the carry limit does not hide the next one")
        require(H.text.contains("BCD Dock") && H.text.contains("Start with") && H.text.contains("needs Steam"),
                "the words name the Start with choice and Dock")
        require(!H.text.contains("Katana") && !H.text.contains("Trails") && !H.text.contains("crack"), "no game names")
        exit(failures == 0 ? 0 : 1)
    }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / 'rules.swift').write_text('import Foundation\n' + rules)
    (Path(tmp) / 'main.swift').write_text(swift)
    exe = Path(tmp) / 'rules'
    r = subprocess.run([SWIFTC, '-parse-as-library', '-o', str(exe), str(Path(tmp) / 'rules.swift'), str(Path(tmp) / 'main.swift')],
                       capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the Swift rules compile')
    else:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            failures += 1

# ------------------------------------------------------------------ 2. C: the child table
table = process[process.index('#define IOS_CHILD_SLOTS 32'):process.index("/* the machine of the child's main image, published to")]
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
static int spawn(const char *path)
{
    static WCHAR wide[260];
    UNICODE_STRING u;
    size_t n = strlen(path), i;
    for (i = 0; i < n; i++) wide[i] = (unsigned char)path[i];
    u.Length = (unsigned short)(n * sizeof(WCHAR)); u.MaximumLength = u.Length; u.Buffer = wide;
    return ios_child_slot_take(&u);
}
static int live(char *names, int len) { return madeira_live_user_processes(names, len); }
int main(void)
{
    char names[256];
    int s_explorer, s_services, s_conhost, s_wine, s_dock, s_game, s_helper, s_crash, i;
    void *peb_game = (void *)0x1000, *peb_game2 = (void *)0x2000;

    expect(live(names, sizeof names) == 0 && names[0] == 0, "no child: nothing runs");

    /* Wine's own processes and the Dock host do not keep a session alive */
    s_explorer = spawn("C:\\windows\\system32\\explorer.exe");
    s_services = spawn("C:\\windows\\system32\\services.exe");
    s_conhost = spawn("C:\\windows\\system32\\conhost.exe");
    s_wine = spawn("C:\\windows\\system32\\winedevice.exe");
    spawn("C:\\windows\\system32\\plugplay.exe"); spawn("C:\\windows\\system32\\rpcss.exe"); spawn("C:\\windows\\system32\\svchost.exe");
    s_dock = spawn("C:\\windows\\system32\\dockhost.exe");
    expect(live(names, sizeof names) == 0, "explorer, services, winedevice, conhost, plugplay, rpcss, svchost and the Dock host do not count");
    /* helpers (crash reporters and the like) do not count either */
    s_helper = spawn("C:\\Game\\Binaries\\CrashReportClient.exe");
    s_crash = spawn("C:\\Game\\UnityCrashHandler64.exe");
    spawn("C:\\Game\\steamwebhelper.exe");
    expect(live(names, sizeof names) == 0, "crash reporters and helpers do not count");
    /* upper case, a bare name and a Wine-looking name in a game's folder: matched by file name only */
    s_game = spawn("C:\\Games\\Some Game\\Game.exe");
    expect(live(names, sizeof names) == 1 && !strcmp(names, "game.exe"), "a game's own process counts, named by its lower-cased file name");
    spawn("C:\\Games\\Some Game\\explorer_helper_tool.exe");   /* contains 'helper' */
    spawn("C:\\Games\\Some Game\\explorer.exe.bak");
    expect(live(names, sizeof names) == 2, "only the exact system file names are Wine's own (a lookalike counts)");
    ios_child_slot_release(spawn("C:\\Games\\Some Game\\explorer.exe.bak"));

    /* a child that ended does not count, whichever thread ended it; its slot stays used until its boot thread returns */
    ios_child_slot_current = s_game;
    ios_child_slot_bind_peb(peb_game);
    expect(live(names, sizeof names) == 2, "binding a PEB changes nothing by itself");
    ios_child_slot_exited(peb_game);
    expect(live(names, sizeof names) == 2, "a child that has just ended still counts: its boot thread releases the slot within milliseconds (a healthy exit is unchanged)");
    ios_child_slots[s_game].exited_at -= IOS_CHILD_EXIT_SETTLE + 1.0;
    expect(live(names, sizeof names) == 1, "after the settle time without a release the child no longer counts (its boot thread never returned)");
    ios_child_slot_exited(peb_game);
    expect(live(names, sizeof names) == 1, "marking twice changes nothing");
    expect(ios_child_slots[s_game].used == 1, "its slot is still used: only its boot thread releases it");
    ios_child_slot_release(s_game);
    /* a new child may reuse the PEB of one that ended: only the live one is marked later */
    {
        int again = spawn("C:\\Games\\Some Game\\Game.exe");
        ios_child_slot_current = again;
        ios_child_slot_bind_peb(peb_game);
        expect(live(names, sizeof names) == 2, "a child that starts after the other ended counts again");
        ios_child_slot_exited(peb_game);
        ios_child_slots[again].exited_at -= IOS_CHILD_EXIT_SETTLE + 1.0;
        expect(live(names, sizeof names) == 1, "and ends the same way");
        ios_child_slot_release(again);
    }
    /* an unbound slot (a child still starting) counts */
    {
        int starting = spawn("C:\\Games\\Some Game\\Launcher.exe");
        expect(live(names, sizeof names) == 2 && strstr(names, "launcher.exe"), "a child that has not registered yet counts");
        ios_child_slot_current = starting;
        ios_child_slot_bind_peb(peb_game2);
        ios_child_slot_exited(peb_game2);
        ios_child_slot_release(starting);
        expect(ios_child_slots[starting].exited == 0 && ios_child_slots[starting].peb == NULL, "releasing a slot clears its PEB and its ended mark");
    }
    ios_child_slot_current = -1;
    ios_child_slot_bind_peb(peb_game);   /* not a child's boot thread: ignored */
    ios_child_slot_exited(NULL);          /* nothing to mark */

    /* the launcher wait (madeira_live_game_children) ignores an ended child too: Hello Neighbor TAW359 */
    {
        char gnames[256];
        int g = spawn("C:\\Games\\Hello\\HelloNeighbor-Win64-Shipping.exe");
        void *peb_hn = (void *)0x3000;
        ios_child_slot_current = g;
        ios_child_slot_bind_peb(peb_hn);
        expect(madeira_live_game_children(gnames, sizeof gnames, -1.0) >= 1 && strstr(gnames, "helloneighbor-win64-shipping.exe"),
               "the launcher wait sees the running game");
        ios_child_slot_exited(peb_hn);
        expect(madeira_live_game_children(gnames, sizeof gnames, -1.0) >= 1 && strstr(gnames, "helloneighbor-win64-shipping.exe"),
               "the launcher wait still counts a game that has only just ended (a healthy exit is unchanged)");
        ios_child_slots[g].exited_at -= IOS_CHILD_EXIT_SETTLE + 1.0;
        expect(strstr((madeira_live_game_children(gnames, sizeof gnames, -1.0), gnames), "helloneighbor-win64-shipping.exe") == NULL,
               "the launcher wait no longer waits for a game that is gone for good (its boot thread never returned)");
        ios_child_slot_release(g);
        ios_child_slot_current = -1;
    }

    /* the table full: a child it cannot see counts as running */
    for (i = 0; i < IOS_CHILD_SLOTS * 2; i++) spawn("C:\\Games\\Some Game\\Worker.exe");
    expect(ios_child_untracked > 0, "spawns past the table are counted");
    expect(live(names, sizeof names) >= (int)ios_child_untracked && strstr(names, "untracked"), "an untracked child counts as running, and the list says so");
    (void)s_explorer; (void)s_services; (void)s_conhost; (void)s_wine; (void)s_dock; (void)s_helper; (void)s_crash;
    return failed ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    cp = Path(tmp) / 'table.c'
    cp.write_text(c_table)
    exe = Path(tmp) / 'table'
    r = subprocess.run([CC, '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-fsanitize=address,undefined', '-o', str(exe), str(cp)],
                       capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the child table compiles')
    else:
        env_vars = dict(os.environ, ASAN_OPTIONS='symbolize=0')
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=env_vars)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1

# ------------------------------------------------------------------ 3. C: the bridge's Session end
start = bridge.index('static volatile int g_wine_main_returned = 0;')
end = bridge.index('/* Export MADEIRA_DOCS_DIR before main(), while HOME is still the app container.')
# the end work itself sits after wine_process_thread (before wine_process_start): the file's order is the order the checks
# of the per-launch switches (check-quiet-log, check-srv-direct, check-srv-eco-follow) read
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
static uint64_t g_launch_exit = 0;   /* the status word defined above the section (wine_session_dock_game_ended, round 579, records into it) */
void wineserver_stop(void) { g_stops++; }
void wine_session_note_launch_exit(int status);
int wine_launched_exit_stuck(int32_t *code, double *seconds);
void wine_session_abandon(void);
''' + bridge[start:end] + bridge[end_start:end_end] + r'''
static int failed;
static void expect(int cond, const char *what) { printf("%s: %s\n", cond ? "PASS" : "FAIL", what); if (!cond) failed++; }
static void *other_thread(void *arg) { wine_session_note_launch_exit((int)(intptr_t)arg); return NULL; }
static void end_off_boot(int status)
{
    pthread_t t;
    pthread_create(&t, NULL, other_thread, (void *)(intptr_t)status);
    pthread_join(t, NULL);
}
static void new_session(void)   /* wine_process_start's resets */
{
    g_wine_main_returned = 0; g_launch_exit_claim = 0; g_launch_exit_seen = 0; g_launch_exit_boot = 0;
    g_launch_exit_code = 0; g_launch_exit_ns = 0; g_session_end_claim = 0; g_wine_running = 1; g_stops = 0;
}
int main(void)
{
    int32_t code = -1;
    double seconds = -1;
    char pretend_stack[64] = "C:\\Grand Theft Auto IV\\GTAIV.exe";

    g_app_progname = strdup("Madeira");
    g_wine_thread = pthread_self();   /* this thread is the boot thread */

    /* a session that is running */
    new_session();
    expect(!wine_launched_exit_stuck(&code, &seconds), "a running program: not stuck");

    /* the normal path: the program ends on the boot thread */
    wine_session_note_launch_exit(0);
    expect(!wine_launched_exit_stuck(&code, &seconds), "the program ended on the boot thread: the normal path follows, not stuck");
    wine_session_abandon();
    expect(g_stops == 0 && g_wine_running == 1, "abandon does nothing then");

    /* GTA IV: the program ends on another thread */
    new_session();
    end_off_boot(3);
    expect(wine_launched_exit_stuck(&code, &seconds) && code == 3 && seconds >= 0 && seconds < 30, "ended off the boot thread: stuck, with its code (3) and the time");
    expect(wine_launched_exit_stuck(NULL, NULL), "NULL out parameters are allowed");
    end_off_boot(7);
    expect(wine_launched_exit_stuck(&code, &seconds) && code == 3, "a second end keeps the first code");
    usleep(1100 * 1000);
    expect(wine_launched_exit_stuck(&code, &seconds) && seconds >= 1.0 && seconds < 60.0, "the time since the end grows");

    /* the boot thread comes back by itself: nothing to abandon */
    g_wine_main_returned = 1;
    expect(!wine_launched_exit_stuck(&code, &seconds), "__wine_main returned: not stuck");
    wine_session_abandon();
    expect(g_stops == 0, "abandon does nothing then");

    /* a boot-thread end after an off-thread one (the boot thread was the last): the normal path wins */
    new_session();
    end_off_boot(3);
    wine_session_note_launch_exit(3);
    expect(!wine_launched_exit_stuck(&code, &seconds), "ended on the boot thread as well: not stuck");

    /* abandon: the end work runs once, whoever gets there */
    new_session();
    setenv("MADEIRA_WER", "1", 1); setenv("MADEIRA_FASTSYNC", "1", 1); setenv("MADEIRA_KEEP_ME", "1", 1);
    g_madeira_srv_fgetpath = 1; g_madeira_srv_direct = 2;
    end_off_boot(3);
    wine_session_abandon();
    expect(g_stops == 1 && g_wine_running == 0, "abandon stops the wineserver and clears the running flag");
    expect(!getenv("MADEIRA_WER") && !getenv("MADEIRA_FASTSYNC") && getenv("MADEIRA_KEEP_ME"), "it unsets the per-launch switches and only those");
    expect(g_madeira_srv_fgetpath == 0 && g_madeira_srv_direct == 0, "and the server's switches");
    expect(!wine_launched_exit_stuck(&code, &seconds), "ended: not stuck any more");
    wine_session_abandon();
    wine_session_end();
    expect(g_stops == 1, "the end work does not run twice (a boot thread that returns late)");

    /* the program name: given back when the program ends */
    new_session();
    setprogname(pretend_stack + 23);
    expect(!strcmp(getprogname(), "GTAIV.exe") && (const char *)getprogname() == pretend_stack + 23, "setprogname keeps the pointer it is given (the dangling-name cause)");
    end_off_boot(3);
    expect(!strcmp(getprogname(), "Madeira") && getprogname() != pretend_stack + 23, "the hook gives the app's own name back as the program ends");
    setprogname(pretend_stack + 23);
    wine_session_end();
    expect(!strcmp(getprogname(), "Madeira"), "and so does the end work");
    free(g_app_progname); g_app_progname = NULL;
    setprogname(pretend_stack + 23);
    new_session();
    end_off_boot(0);
    expect(getprogname() == pretend_stack + 23, "no saved name (never started here): nothing is touched");
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
        require(False, 'the Session end section compiles')
    else:
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1
    # thread sanitizer on the same program: the hook runs on the exiting thread while the library polls
    exe = Path(tmp) / 'end_tsan'
    r = subprocess.run([CC, '-std=gnu11', '-fsanitize=thread', '-o', str(exe), str(cp), '-lpthread'], capture_output=True, text=True)
    if r.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=dict(os.environ, TSAN_OPTIONS='report_signal_unsafe=0'))
        require(r.returncode == 0 and 'ThreadSanitizer' not in r.stderr, 'the same under ThreadSanitizer: no race in the bookkeeping')
        if r.returncode or 'ThreadSanitizer' in r.stderr:
            sys.stdout.write(r.stdout[-1500:] + r.stderr[-2500:])

# ------------------------------------------------------------------ 4. Source checks
hook = block(bridge, 'void wine_launched_process_did_exit(int status)')
require('wine_session_note_launch_exit(status);' in hook and hook.count('wine_session_note_launch_exit') == 1,
        'the initial process\'s exit hook also notes the end (any status, which thread)')
thread_fn = bridge[bridge.index('static void *wine_process_thread('):] if 'static void *wine_process_thread(' in bridge else bridge[bridge.index('wine_process_thread('):]
thread_fn = thread_fn[:thread_fn.index('/* What the boot thread does after __wine_main')]
require(thread_fn.count('wine_session_end();') == 1 and 'unsetenv(' not in thread_fn[thread_fn.index('wine_session_end();'):],
        'the boot thread\'s tail calls the shared end work once, and carries no switch list of its own')
require(thread_fn.index('__wine_main(argc, argv);') < thread_fn.index('g_wine_main_returned, 1') < thread_fn.index('madeira_live_game_children(names, sizeof names, 60.0)')
        < thread_fn.index('wine_session_end();') < thread_fn.index('Wine process thread finished cleanly'),
        'normal path order: __wine_main returns, the flag is set, the launcher wait runs, the end work, the thread finishes')
require('MADEIRA_WAIT_CHILDREN' in thread_fn and 'madeira_live_game_children(names, sizeof names, 60.0)' in thread_fn and
        'madeira_live_game_children(names, sizeof names, -1.0)' in thread_fn, 'the launcher wait itself is as it was')
end_fn = block(bridge, 'static void wine_session_end(void)\n{')
require(end_fn.index('g_session_end_claim') < end_fn.index('g_wine_running = 0;') < end_fn.index('wine_progname_restore();') < end_fn.index('unsetenv(')
        < end_fn.index('wineserver_stop();'), 'wine_session_end: once-guard, running flag, name, switches, wineserver')
start_fn = block(bridge, 'int wine_process_start(const char *prefix_path)')
require('g_app_progname' in start_fn and 'g_session_end_claim = 0' in start_fn and start_fn.index('g_session_end_claim = 0') < start_fn.index('g_wine_running = 1;'),
        'a session start takes the app\'s own program name once and resets the bookkeeping before it runs')
for name in ('wine_launched_exit_stuck', 'wine_session_abandon', 'madeira_live_user_processes'):
    require(name in header, '%s is declared for Swift (WineProcessBridge.h)' % name)
poll = block(library, 'private func poll()')
require(poll.count('watchProgramEnd()') == 1 and poll.index('if wine_process_is_running() != 0 {') < poll.index('watchProgramEnd()')
        < poll.index('} else if sawProcess && wineserver_is_running() == 0 { finish() }'),
        'poll: the watch runs only while the engine still says Windows runs; the end is still finish() on both flags clear')
watch = block(library, 'private func watchProgramEnd()')
require('wine_launched_exit_stuck(&code, &seconds) != 0 else { return }' in watch and 'DockStartScreen.shared.active' in watch
        and 'DispatchQueue.global(qos: .userInitiated).async { wine_session_abandon() }' in watch
        and '[session-end] main program exited code=\\(code); no game process left; returning' in watch,
        'the watch asks the engine first, leaves Dock sessions alone, ends off the main queue, and logs [session-end]')
require('endedByWatch = code' in watch and 'guard endedByWatch == nil else { return }' in watch, 'the watch ends a session once')
report = block(library, 'private func exitReport() -> String?')
require('guard !quitRequested, MadeiraConfig.flag("MADEIRA_EXIT_REPORT") else { return nil }' in report and 'SessionEndRules.message(exitCode: code)' in report
        and report.index('wine_crash_exit_status(&status)') < report.index('SessionEndRules.message'),
        'the exit report: a Windows error keeps its own words; a plain code gets the new ones; Quit and MADEIRA_EXIT_REPORT=0 stay silent')
require('if status == 0xC0000142 || status == 0xC0000018 {' in report and 'return ExitWords.libraryStart(name)' in report
        and report.index('StikJITHelper.exeWindowWidenFailedMB') < report.index('if status == 0xC0000142 || status == 0xC0000018 {')
        < report.index('let kind = status == 0xC0000005'),
        'round 624: 0xC0000142 names the library, 0xC0000018 the program and window, after the widen-failed case and before the plain words')
fin = block(library, 'private func finish()')
say = block(library, 'private func sayNoSteam(replacing report: String?, local: Bool = false)')
require('SteamStartHint.applies(directSteamStart: activeEntry?.startsSteamGameDirectly == true' in fin and 'sawProcess,' in fin
        and fin.index('SteamStartHint.applies') < fin.index('current = nil; activeEntry = nil'),
        'finish(): the hint is asked only after a session that ran, for a direct Steam start, before the entry is cleared')
require('let localProgram = activeEntry != nil && activeEntry?.steamAppID == nil && activeEntry?.desktop != true' in fin
        and 'localProgram: localProgram,' in fin and 'sayNoSteam(replacing: report, local: localProgram)' in fin
        and fin.index('let localProgram') < fin.index('SteamStartHint.applies'),
        'finish() (round 624, 49T3ST): a program added by hand (no Steam app, not the desktop) is asked too, and says which it was')
require('DispatchQueue.global(qos: .utility).async' in say and 'guard let self, self.error == report else { return }' in say
        and 'SteamStartHint.logSaysNoSteam(log, local: local)' in say and say.index('logSaysNoSteam') < say.index('DispatchQueue.main.async')
        and 'self.error = local ? ' not in say and 'self.error = SteamStartHint.localText' in say and 'self.error = SteamStartHint.text' in say,
        'the log is read off the main queue; the alert changes only if nothing else took it meanwhile; each kind of start gets its own words')
require('SteamStartHint' not in watch and 'SteamStartHint' not in report, 'the watch and the exit report are as they were')
require('endedByWatch = nil' in block(library, 'func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)'),
        'a new session forgets the last one\'s watch state')
# the process tree: bound at registration, marked at the exit chokepoint, never at the normal child exit
require('ios_child_slot_bind_peb( ios_jit_current_peb() );' in block(server, 'size_t server_init_process_child( int child_fd_socket )'),
        'a child binds its PEB to its table slot when it registers with the server')
wrapper = block(server, 'void process_exit_wrapper( int status )')
slot_branch = wrapper[:wrapper.index('    else\n    {')]
require('ios_child_slot_exited( dead_peb );' in slot_branch and wrapper.count('ios_child_slot_exited( dead_peb );') == 1,
        'a child\'s exit (any thread: process_exit_wrapper) marks its table slot ended')
require('ios_child_slot_current = args->slot;' in block(process, 'static void *ios_child_thread_entry( void *arg )'),
        'a child\'s boot thread tells the table which slot is its own')
require(not re.findall(r'"[^"\n]*\.exe"', bridge[bridge.index('Session end (madeira-bcd'):bridge.index('Export MADEIRA_DOCS_DIR')]),
        'no program names in the bridge\'s Session end section')
names = re.search(r'static const char \*const names\[\] = \{(.*?)\};', process[process.index('static int ios_child_is_system'):], re.S).group(1)
listed = set(re.findall(r'"([^"]+)"', names))
require(listed == {'explorer.exe', 'services.exe', 'winedevice.exe', 'conhost.exe', 'plugplay.exe', 'rpcss.exe', 'svchost.exe', 'dockhost.exe'},
        'Wine\'s own processes and the Dock host, and nothing else: %s' % sorted(listed))
# the shim and the normal path are untouched
require('longjmp(wine_ios_exit_jmpbuf, 1);' in shim and 'pthread_exit(NULL);' in shim and 'pthread_equal(pthread_self(), wine_ios_main_thread)' in shim,
        'the exit shim is as it was (boot thread long-jumps, any other thread leaves by pthread_exit)')
require(env.count('setprogname( name );') == 1, 'Wine\'s set_process_name is untouched (the name is given back when the program ends)')
# the diagnostic for the next log
require('[native-bt] progname ptr=0x%llx == x%d' in signal and 'getprogname()' in signal, 'a native fault logs whether the faulting string is the program name')

print('check-session-end: ' + ('PASS' if failures == 0 else 'FAIL (%d)' % failures))
sys.exit(1 if failures else 0)
