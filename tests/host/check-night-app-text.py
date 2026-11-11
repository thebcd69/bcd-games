#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Small text and log changes of the night round 2026-11-11 (W-APP): each one is words or a log line only, nothing behaves differently.

  A5 S1   [game-cfg] note: for a line of a game's config that is a program pair with no key (Fallout 3 GOTY K2J61G, YBZ3HX), compiled and driven.
  G3 S11  the Qt language chooser language_setup.exe is not offered as the game by the folder scan (DOOM 3: BFG Edition DVXP0H, RATRZ0).
  A5 S12  the starting screen says so when Low Power Mode is on (Borderlands GOTY Enhanced WK5EAC): words in BCDStartHints, shown by the live layer and the preview.
  A5 S14  the game page's "Start over with BCD's settings" row is the same confirmed reset as Reset to Recommended; the page's footer says what stays.
  T2 S-PERF1  the Resolution row's note says, on an iPad only, that a lower Resolution is the first thing to try.
  T2 S-LOG1  "Wine still running after 1200s" is not an error and "Wine finished" is not printed while the game runs (Fallout: New Vegas 2FMXYN).
  T1 S2 / T3 S3  the memory-kill advice (AutoTune.swift): checked in tests/host/autotune_glue_checks.swift, run by check-autotune.py.
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
glib = (app / 'GameLibrary.swift').read_text(encoding='utf-8')
starting = (app / 'BCDStartingScreen.swift').read_text(encoding='utf-8')
live = (app / 'BCDSessionLive.swift').read_text(encoding='utf-8')
preview = (root / 'app/BCDPreview/BCDPreviewSession.swift').read_text(encoding='utf-8')
page = (app / 'BCDGamePage.swift').read_text(encoding='utf-8')
page_live = (app / 'BCDGamePageLive.swift').read_text(encoding='utf-8')
content = (app / 'ContentView.swift').read_text(encoding='utf-8')
workflow = (root / '.github/workflows/build-ipa.yml').read_text(encoding='utf-8')

# ------------------------------------------------------------------ A5 S1: compiled
notes = block(library, 'enum GameConfigNotes {')
swift = r'''
import Foundation
var failures = 0
func require(_ condition: @autoclosure () -> Bool, _ label: String) {
    if condition() { print("PASS: " + label) } else { print("FAIL: " + label); failures += 1 }
}
@main struct Checks {
    static func main() {
        let n = GameConfigNotes.notes(["FalloutLauncher.exe": "Fallout3.exe"])
        require(n == ["\"FalloutLauncher.exe=Fallout3.exe\" is a line without a key; a program swap is written env.MADEIRA_SPAWN_SWAP = FalloutLauncher.exe=Fallout3.exe (and it only changes a program that another program starts, not the one BCD starts first)"],
                "K2J61G: a pair pasted without its key is named, with the line it should be")
        require(GameConfigNotes.notes(["env.MADEIRA_SPAWN_SWAP": ""]) == ["env.MADEIRA_SPAWN_SWAP has no value; write it as env.MADEIRA_SPAWN_SWAP = launcher.exe=game.exe"]
                && GameConfigNotes.notes(["env.MADEIRA_SPAWN_SWAP": "  "]).count == 1, "YBZ3HX: an empty program swap is named")
        require(GameConfigNotes.notes(["env.MADEIRA_SPAWN_SWAP": "FalloutLauncher.exe=Fallout3.exe", "pool": "896", "env.MADEIRA_EC_HOOK_TRACE": "0", "gpu-card": "nvidia"]).isEmpty,
                "a working config says nothing (a program swap with its key, ordinary lines)")
        require(GameConfigNotes.notes(["A.EXE": "b.exe", "c.exe": "d.exe"]).count == 2 && GameConfigNotes.notes([:]).isEmpty, "the case of .exe does not matter; no pairs, no note")
        exit(failures == 0 ? 0 : 1)
    }
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'rules.swift').write_text('import Foundation\n' + notes + '\n')
    (tmp / 'main.swift').write_text(swift)
    r = subprocess.run([SWIFTC, '-parse-as-library', '-o', str(tmp / 'run'), str(tmp / 'rules.swift'), str(tmp / 'main.swift')], capture_output=True, text=True)
    if r.returncode:
        sys.stdout.write(r.stderr[-3000:])
        require(False, 'the notes compile')
    else:
        r = subprocess.run([str(tmp / 'run')], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode:
            sys.stdout.write(r.stderr[-2000:])
            failures += 1

env_block = library[library.index('let pairs = try MadeiraConfig.applyGame(gameConfigText)'):]
env_block = env_block[:env_block.index('} catch {')]
require('for note in GameConfigNotes.notes(pairs) { LogStore.shared.log("[game-cfg] note: " + note) }' in env_block
        and env_block.index('[game-cfg] "') < env_block.index('GameConfigNotes.notes(pairs)') and 'setenv' not in env_block,
        'A5 S1: the notes follow the [game-cfg] line and only log (the config is exported exactly as before)')

# ------------------------------------------------------------------ G3 S11
noise = block(glib, 'private static let noiseNames: Set<String> =')
require('"language_setup.exe", "languagesetup.exe"' in noise and '"cleanup.exe", "touchup.exe"' in noise, 'G3 S11: the language chooser is noise for the folder scan, the others stay')

# ------------------------------------------------------------------ A5 S12
require('static let lowPower = "Low Power Mode is on, so this device slows its processor and games and launchers start and run much slower. Turn it off for play."' in starting
        and 'BCDStartNote(id: "low-power", text: BCDStartHints.lowPower, tone: .warm)' in live
        and 'if ProcessInfo.processInfo.isLowPowerModeEnabled, dock.failure == nil {' in live
        and '[hint] low-power-mode' in block(live, 'private func startStarting()') and 'case "starting-lowpower":' in preview
        and 'BCDStartNote(id: "low-power", text: BCDStartHints.lowPower, tone: .warm)' in preview,
        'A5 S12: the words live in BCDStartHints (the preview shows the same), the live starting screen adds the note only with Low Power Mode on, one log line a start')

# ------------------------------------------------------------------ A5 S14
require('rows.append(BCDGameRow(id: "startover", title: "Start over with BCD\'s settings", symbol: "arrow.counterclockwise",' in page
        and 'tone: .destructive, run: request(.resetToRecommended)))' in page[page.index('id: "startover"'):][:600]
        and page.index('id: "recommended", title: "Recommended settings"') < page.index('id: "startover"') < page.index('if let tuning = d.tuning {'),
        'A5 S14: the row asks the same confirmed request as Reset to Recommended (.resetToRecommended), right after Recommended settings, only where that row is')
require("A reset does not touch the game's own files: its saves and its in-game options." in page, 'A5 S14: the Recommended page says what a reset leaves')

# ------------------------------------------------------------------ T2 S-PERF1
require('UIDevice.current.userInterfaceIdiom == .pad' in block(page_live, 'private static var iPadResolutionHint: String')
        and '+ Self.iPadResolutionHint),' in page_live and '? " An iPad\'s screen has many pixels, so a lower Resolution (with MetalFX upscaling below) is the first thing to try when a game stutters."' in page_live
        and ': ""' in block(page_live, 'private static var iPadResolutionHint: String'),
        "T2 S-PERF1: the Resolution row's note gets the sentence on an iPad and nothing elsewhere")

# ------------------------------------------------------------------ T2 S-LOG1
require('logStore.log("The session is still running after \\(Int(maxWait)) s; the detach wait ends here")' in content
        and 'level: .error)\n                    break' not in content[content.index('The session is still running after'):][:300]
        and 'if wine_process_is_running() == 0 { logStore.log("Wine finished after' in content and 'Wine still running after' not in content,
        'T2 S-LOG1: the long-session line is info and says what it is; "Wine finished" only when it did')

require('python3 tests/host/check-night-app-text.py' in workflow, 'CI runs this check')
print(f'\n{"PASS" if failures == 0 else "FAIL"}: night app text ({failures} failure(s))')
sys.exit(1 if failures else 0)
