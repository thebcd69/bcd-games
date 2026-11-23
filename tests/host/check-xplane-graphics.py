#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""X-Plane 12 at its lowest graphics settings (`xplane-graphics = lowest`, build 701; app/Madeira/XPlaneGraphics.swift).

Compiles the production file with a small driver and runs it on a settings file this check writes itself, in the layout of
X-Plane 12's Resources/settings.txt (no Laminar Research file is held here):

  lowered    every graphics setting's SETTING_DEFAULT value at its lowest for every config but "safe"; G-load dimming, the
             SETTING_1 tables, TIMEDEMO lines, comments (UTF-8), tabs and CRLF line ends byte for byte as they were; a
             lowered file lowered again is the same; a file that is not X-Plane's settings file is refused;
  wanted     the game's settings: `xplane-graphics = lowest`, the last line winning, comments and other values ignored;
  prepare    on a scratch install folder: the original kept as settings.txt.bcd-original and the lowered file written; a
             second start changes nothing; the line taken out puts X-Plane's own file back; an update's new file is kept as the
             new original (wanted) or left alone with the old copy removed (not wanted); a folder without the file is untouched;
  wiring     the launch hook after the recommended settings, the Xcode project, the settings catalog, the list line
             (minBuild 701) and CI.

    tests/host/check-xplane-graphics.py
    tests/host/check-xplane-graphics.py --real <settings.txt>   also lowers a real file (read only; never kept here)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
app = root / 'app/Madeira'
SWIFTC = os.environ.get('SWIFTC') or shutil.which('swiftc') or str(Path.home() / '.local/share/swiftly/bin/swiftc')
failed = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok or not detail else '  [' + str(detail)[-1500:] + ']'))
    if not ok:
        failed.append(name)


# A settings file in X-Plane 12's layout: one block per setting, the defaults per config, the tables of what each value
# sets. The names are X-Plane's; the comments, tables and values are made up for the check.
SYNTHETIC = (
    "A\n1000\nSETTINGS\n\n"
    "#############################\n"
    "# Made for BCD's check — not X-Plane's own file.\n"
    "#############################\n\n"
    "SETTING DRAW_MISC CHECK renopt_gload 1\n"
    "SETTING_NAME |G-load dimming\n"
    "SETTING_DEFAULT all 1\n"
    "SETTING_1 renopt_gload 0 misc/gload 0\n\n"
    "SETTING DRAW POPUP renopt_draw_3d_04 4\n"
    "SETTING_MENU minimal-low-medium-high-maximum\n"
    "SETTING_DEFAULT all 3\n"
    "SETTING_DEFAULT vkmtl_async 4\n"
    "SETTING_DEFAULT safe 1\n"
    "SETTING_1 renopt_draw_3d_04 0 draw/objects 0\n"
    "SETTING_1 renopt_draw_3d_04 4 draw/objects 4\n\n"
    "SETTING DRAW CHECK renopt_static_acf 1\r\n"
    "SETTING_DEFAULT all 1\r\n"
    "SETTING_DEFAULT safe 0\r\n\r\n"
    "SETTING DRAW CHECK renopt_boats 1\n"
    "SETTING_DEFAULT\tall\t1\n\n"
    "SETTING EFFECTS POPUP renopt_vegetation_3d 1\n"
    "SETTING_DEFAULT all 1\n\n"
    "SETTING EFFECTS POPUP renopt_vegetation_quality_04 4\n"
    "SETTING_DEFAULT all 3\n"
    "SETTING_DEFAULT vkmtl_async 4\n\n"
    "SETTING DRAW POPUP renopt_draw_distance04 4\n"
    "SETTING_DEFAULT all 3\n\n"
    "SETTING EFFECTS POPUP renopt_clouds_02 3\n"
    "SETTING_DEFAULT all 2\n"
    "SETTING_DEFAULT m1 2\n"
    "SETTING_1 renopt_clouds_02 2 cloud/steps 5\n\n"
    "SETTING EFFECTS POPUP renopt_FSR_04 4\n"
    "SETTING_DEFAULT all 0\n\n"
    "SETTING EFFECTS POPUP renopt_SSAO_04 4\n"
    "SETTING_DEFAULT all 0\n"
    "SETTING_DEFAULT vkmtl_async 2\n\n"
    "SETTING EFFECTS POPUP renopt_shadow_quality_04 4\n"
    "SETTING_DEFAULT all 2\n"
    "SETTING_DEFAULT m1 1\n"
    "REQUIRE_RELOAD 1 SCENERY_SHADOWS\n\n"
    "SETTING EFFECTS POPUP renopt_MSAA 4\n"
    "SETTING_DEFAULT all 1 # trailing words stay\n\n"
    "SETTING EFFECTS CHECK renopt_FXAA 1\n"
    "SETTING_DEFAULT all 1\n\n"
    "SETTING EFFECTS POPUP renopt_tex_res_04 4\n"
    "SETTING_DEFAULT all 3\n\n"
    "SETTING EFFECTS POPUP renopt_ANISO 32\n"
    "SETTING_1 renopt_ANISO 1 reno/aniso_filter 1\n"
    "SETTING_1 renopt_ANISO 4 reno/aniso_filter 4\n"
    "PLATFORM all\n"
    "SETTING_DEFAULT all 4\n"
    "SETTING_DEFAULT vkmtl_async 16\n"
    "SETTING_DEFAULT safe 1\n\n"
    "TIMEDEMO 4\n"
    "DEMO_SETTING renopt_draw_3d_04 3\n"
    "DEMO_SETTING renopt_clouds_02 1"          # no line end at the end of the file
)

# The same file with what BCD changes, by hand: every SETTING_DEFAULT of a graphics setting but "safe".
EXPECTED = (SYNTHETIC
    .replace("SETTING_DEFAULT all 3\nSETTING_DEFAULT vkmtl_async 4\nSETTING_DEFAULT safe 1\nSETTING_1 renopt_draw_3d_04",
             "SETTING_DEFAULT all 0\nSETTING_DEFAULT vkmtl_async 0\nSETTING_DEFAULT safe 1\nSETTING_1 renopt_draw_3d_04")
    .replace("renopt_static_acf 1\r\nSETTING_DEFAULT all 1\r\n", "renopt_static_acf 1\r\nSETTING_DEFAULT all 0\r\n")
    .replace("SETTING_DEFAULT\tall\t1\n", "SETTING_DEFAULT\tall\t0\n")
    .replace("renopt_vegetation_3d 1\nSETTING_DEFAULT all 1\n", "renopt_vegetation_3d 1\nSETTING_DEFAULT all 0\n")
    .replace("renopt_vegetation_quality_04 4\nSETTING_DEFAULT all 3\nSETTING_DEFAULT vkmtl_async 4\n",
             "renopt_vegetation_quality_04 4\nSETTING_DEFAULT all 0\nSETTING_DEFAULT vkmtl_async 0\n")
    .replace("renopt_draw_distance04 4\nSETTING_DEFAULT all 3\n", "renopt_draw_distance04 4\nSETTING_DEFAULT all 0\n")
    .replace("SETTING_DEFAULT all 2\nSETTING_DEFAULT m1 2\n", "SETTING_DEFAULT all 0\nSETTING_DEFAULT m1 0\n")
    .replace("renopt_SSAO_04 4\nSETTING_DEFAULT all 0\nSETTING_DEFAULT vkmtl_async 2\n",
             "renopt_SSAO_04 4\nSETTING_DEFAULT all 0\nSETTING_DEFAULT vkmtl_async 0\n")
    .replace("SETTING_DEFAULT all 2\nSETTING_DEFAULT m1 1\n", "SETTING_DEFAULT all 0\nSETTING_DEFAULT m1 0\n")
    .replace("SETTING_DEFAULT all 1 # trailing words stay\n", "SETTING_DEFAULT all 0 # trailing words stay\n")
    .replace("renopt_FXAA 1\nSETTING_DEFAULT all 1\n", "renopt_FXAA 1\nSETTING_DEFAULT all 0\n")
    .replace("renopt_tex_res_04 4\nSETTING_DEFAULT all 3\n", "renopt_tex_res_04 4\nSETTING_DEFAULT all 0\n")
    .replace("PLATFORM all\nSETTING_DEFAULT all 4\nSETTING_DEFAULT vkmtl_async 16\n",
             "PLATFORM all\nSETTING_DEFAULT all 1\nSETTING_DEFAULT vkmtl_async 1\n"))

DRIVER = r'''
import Foundation

func arg(_ i: Int) -> String { CommandLine.arguments[i] }
func read(_ p: String) -> String { (try? String(contentsOfFile: p, encoding: .utf8)) ?? "<missing>" }
switch arg(1) {
case "lower":
    // lower <in> <out>: the lowered text, or "<nil>"
    let out = XPlaneGraphics.lowered(read(arg(2))) ?? "<nil>"
    try! Data(out.utf8).write(to: URL(fileURLWithPath: arg(3)))
case "settings-file":
    print(XPlaneGraphics.isSettingsFile(read(arg(2))))
case "wanted":
    print(XPlaneGraphics.wanted(settings: read(arg(2))))
case "prepare":
    // prepare <folder> <1|0>: the log line, or "<nil>"
    print(XPlaneGraphics.prepare(folder: URL(fileURLWithPath: arg(2), isDirectory: true), wanted: arg(3) == "1") ?? "<nil>")
default:
    fatalError("unknown command")
}
'''


def main():
    real = sys.argv[sys.argv.index('--real') + 1] if '--real' in sys.argv else None
    src = (app / 'XPlaneGraphics.swift').read_text()
    check('XPlaneGraphics.swift needs only Foundation', re.findall(r'^import (\w+)', src, re.M) == ['Foundation'])
    check('EXPECTED differs from the fixture in 18 lines',
          sum(a != b for a, b in zip(SYNTHETIC.split('\n'), EXPECTED.split('\n'))) == 18)

    if not Path(SWIFTC).exists() and not shutil.which('xcrun'):
        check('swiftc is here', False, SWIFTC)
        return
    with tempfile.TemporaryDirectory(prefix='bcd-xplane-graphics-') as tmp:
        t = Path(tmp)
        (t / 'main.swift').write_text(DRIVER)
        cmd = ['xcrun', 'swiftc'] if shutil.which('xcrun') else [SWIFTC]
        r = subprocess.run(cmd + ['-swift-version', '5', '-o', str(t / 'drv'), str(t / 'main.swift'), str(app / 'XPlaneGraphics.swift')],
                           capture_output=True, text=True)
        check('the production file compiles with the driver', r.returncode == 0, r.stderr)
        if r.returncode != 0:
            return

        def run(*a):
            return subprocess.run([str(t / 'drv')] + [str(x) for x in a], capture_output=True, text=True).stdout.strip()

        def lower(text):
            (t / 'in.txt').write_bytes(text.encode('utf-8'))
            run('lower', t / 'in.txt', t / 'out.txt')
            return (t / 'out.txt').read_bytes().decode('utf-8')

        # lowered
        low = lower(SYNTHETIC)
        check('lowered: every graphics default at its lowest, every other byte kept', low == EXPECTED,
              next((f'line {i + 1}: {a!r} -> {b!r}' for i, (a, b) in enumerate(zip(low.split('\n'), EXPECTED.split('\n'))) if a != b), ''))
        check('lowered: safe-mode defaults untouched',
              [l for l in low.split('\n') if 'SETTING_DEFAULT safe' in l] == [l for l in SYNTHETIC.split('\n') if 'SETTING_DEFAULT safe' in l])
        check('lowered: G-load dimming untouched', 'renopt_gload 1\nSETTING_NAME |G-load dimming\nSETTING_DEFAULT all 1\n' in low)
        check('lowered: CRLF ends, tabs and the missing last line end kept',
              low.count('\r\n') == SYNTHETIC.count('\r\n') and low.count('\t') == SYNTHETIC.count('\t') and not low.endswith('\n'))
        check('lowered: TIMEDEMO and SETTING_1 lines untouched',
              [l for l in low.split('\n') if l.startswith(('DEMO_SETTING', 'SETTING_1'))]
              == [l for l in SYNTHETIC.split('\n') if l.startswith(('DEMO_SETTING', 'SETTING_1'))])
        check('lowered: a lowered file lowered again is the same', lower(low) == low)
        check('lowered: a file without the header is refused', lower('SETTING DRAW POPUP renopt_draw_3d_04 4\nSETTING_DEFAULT all 3\n') == '<nil>')
        check('lowered: another settings file of X-Plane is refused',
              lower('A\n1110 version\nFEATURES\nFEATURE: MMO 1\n') == '<nil>')
        check('lowered: a header without a known setting is refused',
              lower('A\n1000\nSETTINGS\nSETTING DRAW POPUP renopt_other 4\nSETTING_DEFAULT all 3\n') == '<nil>')
        (t / 'in.txt').write_text(SYNTHETIC)
        check('settings-file: the fixture is X-Plane 12\'s settings file', run('settings-file', t / 'in.txt') == 'true')

        # wanted
        def wanted(text):
            (t / 'cfg.txt').write_text(text)
            return run('wanted', t / 'cfg.txt')
        check('wanted: xplane-graphics = lowest', wanted('vulkan = kosmickrisp\nxplane-graphics = lowest\n') == 'true')
        check('wanted: spaces and case', wanted('  xplane-graphics=LOWEST  ') == 'true')
        check('wanted: the last line wins (off)', wanted('xplane-graphics = lowest\nxplane-graphics =\n') == 'false')
        check('wanted: the last line wins (on)', wanted('xplane-graphics = \nxplane-graphics = lowest') == 'true')
        check('wanted: a comment is no line', wanted('# xplane-graphics = lowest\n') == 'false')
        check('wanted: another value is no', wanted('xplane-graphics = low\n') == 'false')
        check('wanted: another key is no', wanted('env.xplane-graphics = lowest\nxplane-graphics-x = lowest\n') == 'false')
        check('wanted: nothing is no', wanted('') == 'false')

        # prepare
        game = t / 'X-Plane 12'
        res = game / 'Resources'
        res.mkdir(parents=True)
        settings = res / 'settings.txt'
        original = res / 'settings.txt.bcd-original'
        settings.write_bytes(SYNTHETIC.encode('utf-8'))
        line = run('prepare', game, 1)
        check('prepare: lowest lowers the file', settings.read_bytes() == EXPECTED.encode('utf-8'), line)
        check('prepare: X-Plane\'s own file kept beside it', original.read_bytes() == SYNTHETIC.encode('utf-8'))
        check('prepare: the log line counts the lowered defaults', line.startswith('[xplane-graphics] lowest: 18 defaults lowered'), line)
        line = run('prepare', game, 1)
        check('prepare: a second start changes nothing',
              settings.read_bytes() == EXPECTED.encode('utf-8') and original.read_bytes() == SYNTHETIC.encode('utf-8')
              and 'already at their lowest' in line, line)
        line = run('prepare', game, 0)
        check('prepare: the line taken out puts X-Plane\'s own file back',
              settings.read_bytes() == SYNTHETIC.encode('utf-8') and not original.exists() and 'is back' in line, line)
        line = run('prepare', game, 0)
        check('prepare: not wanted and nothing done before: nothing', line == '<nil>' and settings.read_bytes() == SYNTHETIC.encode('utf-8'), line)

        update = SYNTHETIC.replace('SETTING_DEFAULT all 3\nSETTING_DEFAULT vkmtl_async 4\nSETTING_DEFAULT safe 1',
                                   'SETTING_DEFAULT all 2\nSETTING_DEFAULT vkmtl_async 4\nSETTING_DEFAULT safe 1', 1)
        run('prepare', game, 1)
        settings.write_bytes(update.encode('utf-8'))           # an X-Plane update replaced the lowered file
        run('prepare', game, 1)
        check('prepare: an update\'s new file is the new original, lowered again',
              original.read_bytes() == update.encode('utf-8') and settings.read_bytes() == lower(update).encode('utf-8'))
        settings.write_bytes(SYNTHETIC.encode('utf-8'))        # another update while the line is out
        line = run('prepare', game, 0)
        check('prepare: not wanted after an update: X-Plane\'s file left alone, the old copy removed',
              settings.read_bytes() == SYNTHETIC.encode('utf-8') and not original.exists() and 'an update replaced it' in line, line)

        other = t / 'Other Game'
        (other / 'Resources').mkdir(parents=True)
        (other / 'Resources/settings.txt').write_text('[Display]\nWidth=1920\n')
        check('prepare: another game\'s settings.txt is left alone',
              run('prepare', other, 1) == '<nil>' and (other / 'Resources/settings.txt').read_text() == '[Display]\nWidth=1920\n'
              and not (other / 'Resources/settings.txt.bcd-original').exists())
        check('prepare: a folder without the file: nothing', run('prepare', t / 'nowhere', 1) == '<nil>' and not (t / 'nowhere').exists())

        if real:
            data = Path(real).read_bytes()
            text = data.decode('utf-8')
            low = lower(text)
            a, b = text.split('\n'), low.split('\n')
            changed = [(x, y) for x, y in zip(a, b) if x != y]
            check(f'real file: lowered ({len(changed)} lines)', low != '<nil>' and len(a) == len(b) and changed)
            check('real file: only SETTING_DEFAULT lines of other configs than safe change',
                  all(x.startswith('SETTING_DEFAULT') and not x.split()[1] == 'safe' for x, _ in changed))
            check('real file: a lowered file lowered again is the same', lower(low) == low)

    # wiring
    cv = (app / 'ContentView.swift').read_text()
    rec = cv.find('if GameRecommendations.prepare(&entry, new: isNew) { library.save(entry) }')
    hook = cv.find('XPlaneGraphics.prepare(folder: root, wanted: wanted)')
    dock = cv.find('// A Steam game starts through Madeira Dock with its own launch profile', rec)
    check('wiring: the hook runs after the recommended settings and before the start', 0 <= rec < hook < dock, (rec, hook, dock))
    check('wiring: the game\'s own lines and its settings file are read',
          'let own = (entry.gameConfigText ?? "") + "\\n" + GameProfile(windowsPath: entry.windowsPath).text' in cv[rec:dock])
    pbx = (root / 'app/Madeira.xcodeproj/project.pbxproj').read_text()
    check('wiring: XPlaneGraphics.swift is in the app target\'s sources',
          pbx.count('A1F76004 /* XPlaneGraphics.swift in Sources */') == 2 and pbx.count('path = XPlaneGraphics.swift;') == 1
          and pbx.count('A2F76004 /* XPlaneGraphics.swift */') == 3)
    cat = (app / 'ConfigCatalog.generated.swift').read_text()
    check('wiring: the settings catalog lists xplane-graphics as a Graphics choice',
          re.search(r'key: "xplane-graphics", title: "[^"]+", kind: \.choice, defaultValue: "", category: "Graphics".*'
                    r'choices: \[\("", "X-Plane\'s own"\), \("lowest", "Lowest"\)\]', cat) is not None)
    gen = (root / 'build/tools/gen-config-catalog.py').read_text()
    check('wiring: the catalog generator holds the entry', '"xplane-graphics": {"category": "Graphics"' in gen)
    rules = json.loads((root / 'recommended/recommendations.json').read_text())['rules']
    xp = next((r for r in rules if r['id'] == 'x-plane-12'), None)
    check('wiring: X-Plane 12\'s list sets it from build 701',
          xp is not None and {'line': 'xplane-graphics = lowest', 'minBuild': 701} in xp['config'] and xp['version'] >= 7)
    others = [r['id'] for r in rules if r['id'] != 'x-plane-12'
              and any('xplane-graphics' in (c if isinstance(c, str) else c['line']) for c in r['config'])]
    check('wiring: no other game\'s list sets it', not others, others)
    ci = (root / '.github/workflows/build-ipa.yml').read_text()
    check('wiring: CI runs this check', 'run: python3 tests/host/check-xplane-graphics.py' in ci)


main()
print('\n' + ('all passed' if not failed else f'{len(failed)} failed: ' + ', '.join(failed)))
sys.exit(1 if failed else 0)
