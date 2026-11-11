#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""The Windows desktop session can be closed from inside (night round 2026-11-11, A4 S1).

A Windows desktop session (the Start menu's Windows desktop) had no end: BCD's Close posts Alt+F4 into a desktop with no window to close, and
Start > Shut Down runs wineboot --end-session --shutdown, which ends with 0 and kills nothing (user32 ExitWindowsEx, wineboot.c); the desktop's
explorer and services never exit, so the player swiped BCD away (Send Logs 1MQV99, DR1MW9, C7DQPR, CPFCRJ, FQHV90, H09PJJ, J043W2, KTD3HS,
M7AMKZ, HN2342, Q6610X, C86TQF; 19 of the 85 desktop logs of the archive have a Shut Down; DR1MW9's comment: "it didn't wanna shut down").

1. Swift, Library.swift DesktopExitRules: isShutdown (wineboot.exe ending with 0), closeVerdict (the decision table: when the session ends)
   and the words, compiled and driven.
2. Source: the bridge's wine_session_desktop_closed (status 0, never the session's crash status), the library's wiring (a desktop session
   only, env.MADEIRA_DESKTOP_CLOSE, begin resets, the poll runs it after the exit watch), the in-game menu's Close row, the preview app's
   copies of the words, the catalog entry and CI.
"""
from pathlib import Path
import os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failures = 0


def require(condition, label, detail=''):
    global failures
    print(('PASS: ' if condition else 'FAIL: ') + label + (('\n      ' + str(detail)[:1500]) if (detail and not condition) else ''))
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


library = (app / 'Library.swift').read_text(encoding='utf-8')
bridge = (app / 'WineProcessBridge.m').read_text(encoding='utf-8')
header = (app / 'WineProcessBridge.h').read_text(encoding='utf-8')
live = (app / 'BCDSessionLive.swift').read_text(encoding='utf-8')
preview = (root / 'app/BCDPreview/BCDPreviewSession.swift').read_text(encoding='utf-8')
catalog = (root / 'build/tools/gen-config-catalog.py').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')
rules = block(library, 'enum DesktopExitRules {')

# ------------------------------------------------------------------ 1. Swift: the rules
swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
typealias D = DesktopExitRules
func end(_ path: String, _ status: UInt32) -> D.End { D.End(number: 0, status: status, lived: 1, openGL: false, helper: false, path: path) }

@main struct Checks {
    static func main() {
        // ---- Start > Shut Down: wineboot.exe ending with 0 (DR1MW9 L4526-L4734)
        require(D.isShutdown(end("C:\\windows\\system32\\wineboot.exe", 0)), "DR1MW9: wineboot.exe ended with 0: Shut Down")
        require(D.isShutdown(end("C:\\windows\\system32\\WineBoot.EXE", 0)) && D.isShutdown(end("wineboot.exe", 0)), "the case and the folder do not matter")
        require(!D.isShutdown(end("C:\\windows\\system32\\wineboot.exe", 1)) && !D.isShutdown(end("C:\\windows\\system32\\wineboot.exe", 0xC0000005)),
                "wineboot.exe ending with an error is not a Shut Down")
        require(!D.isShutdown(end("C:\\windows\\system32\\explorer.exe", 0)) && !D.isShutdown(end("C:\\Games\\wineboot.exe.bak", 0))
                && !D.isShutdown(end("C:\\Games\\X\\game.exe", 0)), "no other program is a Shut Down")

        // ---- the decision table
        func v(_ asks: Int, _ ask: Double?, _ down: Double?, _ programs: Int) -> D.CloseVerdict {
            D.closeVerdict(asks: asks, sinceAsk: ask, sinceShutdown: down, programs: programs)
        }
        require(v(0, nil, nil, 0) == .wait && v(0, nil, nil, 3) == .wait, "nothing pressed: the desktop stays (a desktop nobody closed is never ended)")
        require(v(1, 0.0, nil, 0) == .wait && v(1, 1.0, nil, 0) == .wait && v(1, 1.49, nil, 0) == .wait, "Close, nothing running: Alt+F4 gets 1.5 s first")
        require(v(1, 1.5, nil, 0) == .end && v(1, 9.0, nil, 0) == .end, "1MQV99: Close, nothing of the player's runs: the session ends")
        require(v(1, 1.6, nil, 1) == .wait && v(1, 30.0, nil, 2) == .wait, "Close with a program still running: it is asked to close, the desktop stays")
        require(v(2, 1.6, nil, 1) == .end && v(3, 5.0, nil, 4) == .end, "a second Close ends the desktop although a program (or a service of the prefix) runs")
        require(v(2, 1.0, nil, 1) == .wait, "the second Close also gets its 1.5 s")
        require(v(0, nil, 1.9, 0) == .wait && v(0, nil, 0.0, 0) == .wait, "Shut Down: wineboot ended less than 2 s ago: wait")
        require(v(0, nil, 2.0, 0) == .end && v(0, nil, 3.0, 0) == .end, "DR1MW9: Shut Down confirmed, nothing runs: the session ends")
        require(v(0, nil, 3.0, 1) == .wait && v(0, nil, 7.9, 2) == .wait, "Shut Down with a program still running: it gets time to close")
        require(v(0, nil, 8.0, 1) == .end && v(0, nil, 20.0, 3) == .end, "Shut Down: 8 s later the session ends anyway")
        require(v(1, 0.2, 5.0, 0) == .end && v(1, 3.0, 2.5, 1) == .wait, "both: either path may end it")
        require(D.closeSeconds == 1.5 && D.shutdownSeconds == 2.0 && D.shutdownForceSeconds == 8.0, "1.5 s, 2 s, 8 s")

        // ---- the words
        require(D.closingWords == "The Windows desktop is closing.", "the session line while it closes")
        require(D.programOpenWords == "A program is still open on the desktop. Close it, or choose Close again to end the desktop.", "the line for a program still open")
        require(D.menuTitle == "Close the Windows desktop" && D.menuAnswer == "Close desktop"
                && D.menuConfirmTitle == "Close the Windows desktop?"
                && D.menuDetail == "Programs on it are closed; what they haven't saved is lost."
                && D.menuConfirmMessage == "BCD asks the programs on the desktop to close and ends this session. What you haven't saved is lost.",
                "the menu row's words")
        exit(failures == 0 ? 0 : 1)
    }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'rules.swift').write_text('import Foundation\n' + rules + '\n')
    (tmp / 'main.swift').write_text(swift)
    r = subprocess.run([SWIFTC, '-parse-as-library', '-o', str(tmp / 'rules'), str(tmp / 'rules.swift'), str(tmp / 'main.swift')],
                       capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the Swift rules compile')
    else:
        r = subprocess.run([str(tmp / 'rules')], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1

# ------------------------------------------------------------------ 2. source: the bridge
fn = block(bridge, 'int wine_session_desktop_closed(void)')
require('if (!g_wine_running || __atomic_load_n(&g_session_end_claim, __ATOMIC_ACQUIRE)) return 0;' in fn
        and '__atomic_store_n(&g_wine_exit_code, 0, __ATOMIC_RELEASE);' in fn and 'wine_session_end();' in fn and 'return 1;' in fn,
        'the bridge ends the session once, with exit code 0, as wine_session_dock_game_quit does')
require('g_launch_exit' not in fn, "and never makes its status the session's crash status (no error box)")
require('int wine_session_desktop_closed(void);' in header, 'the bridge header declares it')

# ------------------------------------------------------------------ 3. source: the library
begin = block(library, 'func begin(_ entry: LibraryEntry, remember: Bool = true, dock: DockGame? = nil)')
require('desktopAsks = 0; desktopAskedAt = nil; desktopShutdownAt = nil; desktopCloseRunning = false; desktopProgramNoted = false' in begin,
        'a session begins: the close state is reset')
poll = block(library, 'private func poll()')
running = poll[poll.index('if wine_process_is_running() != 0 {'):poll.index('} else if sawProcess && wineserver_is_running() == 0 { finish() }')]
require(running.index('watchDesktopExits()') < running.index('watchDesktopClose()'), 'the close watch runs in the session poll, after the exit watch')
watch = block(library, 'private func watchDesktopClose()')
require(watch.startswith('private func watchDesktopClose() {\n        guard activeEntry?.desktop == true, !desktopCloseRunning, desktopAskedAt != nil || desktopShutdownAt != nil, !BCDDuoFeatures.active,\n'
                         '              MadeiraConfig.flag("MADEIRA_DESKTOP_CLOSE") else { return }'),
        'only a Windows desktop session, once the player pressed Close or wineboot ended, once, never in the native Duo interface (its own desktop quit), and env.MADEIRA_DESKTOP_CLOSE is not 0')
require('madeira_live_user_processes(&names' in watch and 'DesktopExitRules.closeVerdict(asks: desktopAsks, sinceAsk: sinceAsk, sinceShutdown: sinceShutdown, programs: programs)' in watch,
        'the engine counts the programs, the rules decide')
require(watch.count('wine_session_desktop_closed()') == 1 and 'wine_session_dock_game' not in watch and 'wine_session_abandon' not in watch,
        'the only end it makes is wine_session_desktop_closed, off the main queue')
require('DispatchQueue.global(qos: .userInitiated).async { _ = wine_session_desktop_closed() }' in watch, 'off the main queue (it joins the wineserver thread)')
require('[desktop-close] ending the session: closes=' in watch and '[desktop-close] a program is still open' in watch, 'both decisions are logged')
exits = block(library, 'private func watchDesktopExits()')
require('if DesktopExitRules.isShutdown(end), desktopShutdownAt == nil, MadeiraConfig.flag("MADEIRA_DESKTOP_CLOSE") {' in exits
        and not re.search(r'wine_session_|finish\(\)', exits), 'the exit watch only notes the Shut Down and still ends nothing')
quit_ = block(library, 'func requestQuit()')
require('if activeEntry?.desktop == true, MadeiraConfig.flag("MADEIRA_DESKTOP_CLOSE") {\n            desktopAsks += 1; desktopAskedAt = Date()' in quit_
        and 'sessionMessage = DesktopExitRules.closingWords' in quit_
        and 'sessionMessage = "The game was asked to close. Confirm in the game if it asks."' in quit_
        and 'winios_post_key(0x12, 1); winios_post_key(0x73, 1)' in quit_,
        'Close still posts Alt+F4 (a library game or a Dock session is unchanged: its words stay); a desktop counts the press and says it closes')
require('quitRequested = true' in quit_, 'quitRequested stays set (no error box, auto-tune reads a quit)')

# ------------------------------------------------------------------ 4. the in-game menu and the preview's copies
require('model.activeEntry?.desktop == true, !BCDDuoFeatures.active, MadeiraConfig.flag("MADEIRA_DESKTOP_CLOSE")' in live
        and 'title: DesktopExitRules.menuTitle, detail: DesktopExitRules.menuDetail' in live
        and 'answers: [.init(id: "quit", title: DesktopExitRules.menuAnswer, destructive: true)]' in live
        and 'BCDMenuRow(id: "quit", title: BCDDuoFeatures.active && model.activeEntry?.desktop == true ? "Close Windows" : "Quit game", detail: "Progress you haven\'t saved is lost."' in live,
        "the in-game menu's Close row is the desktop's only, not in the Duo interface (a game's Quit game row and Duo's Close Windows row are untouched); the answer id stays quit")


def swift_words(src, marker):
    rest = src[src.index(marker) + len(marker):]
    out, first = [], True
    for line in rest.splitlines():
        t = line.strip()
        if not first and not t.startswith('+'):
            break
        out += re.findall(r'"((?:[^"\\]|\\.)*)"', t)
        first = False
    return ''.join(out)


for prod, copy in (('static let closingWords = ', 'static let desktopClosingWords = '),
                   ('static let programOpenWords = ', 'static let desktopProgramOpenWords = '),
                   ('static let menuTitle = ', 'static let desktopCloseTitle = '),
                   ('static let menuDetail = ', 'static let desktopCloseDetail = '),
                   ('static let menuConfirmTitle = ', 'static let desktopCloseConfirmTitle = '),
                   ('static let menuConfirmMessage = ', 'static let desktopCloseConfirmMessage = '),
                   ('static let menuAnswer = ', 'static let desktopCloseAnswer = ')):
    require(swift_words(rules, prod) == swift_words(preview, copy) and swift_words(rules, prod) != '',
            f"the preview app's {copy.split()[-2]} says exactly what DesktopExitRules.{prod.split()[-2]} says")
require('case "message-closing": hud.message = BCDPreviewSessionSamples.desktopClosingWords' in preview
        and 'case "message-program": hud.message = BCDPreviewSessionSamples.desktopProgramOpenWords' in preview
        and 'BCDPreviewSessionSamples.desktopCloseTitle' in preview, "the preview app shows the two session lines and the menu row")

# ------------------------------------------------------------------ 5. the catalog and CI
require('"env.MADEIRA_DESKTOP_CLOSE": {"category": "App & front end"' in catalog and '"kind": "bool", "default": "1"' in catalog[catalog.index('"env.MADEIRA_DESKTOP_CLOSE"'):][:400],
        'the key is in the settings catalog, default on')
require('python3 tests/host/check-desktop-close.py' in workflow, 'CI runs this check')

print(f'\n{"PASS" if failures == 0 else "FAIL"}: desktop close ({failures} failure(s))')
sys.exit(1 if failures else 0)
