#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""wer-optin.dll (env.MADEIRA_WER = 1): Wine's wer.dll for the one game that imports it; no Wine runs.

Hello Neighbor's Unreal Engine 4 program imports wer.dll, which BCD does not ship, so the game
ended at once with 0xc0000135 (a tester's Send Log, build 573). Shipping Wine's wer.dll for every
game was taken back (b960fe4a): it has 68 unimplemented exports, and a program that loads wer.dll
optionally today could be aborted by one of them. So it is opt-in, per game:

  - tools/build-wine-extra-dlls.sh builds Wine's wer and ships it as wer-optin.dll, never as
    wer.dll, and not at all where a wer.dll already ships;
  - app/Madeira/WineProcessBridge.m links it as wer.dll for a session whose game file has
    env.MADEIRA_WER = 1, removes a link of ours that an earlier session left when the line is
    gone (wer.dll is no part of the bundle, so the per-session farms never touch it), and the
    switch ends with the session;
  - the settings catalog documents the switch, and auto-tune's rule "wer-dll-optin" adds it after
    a "Library wer.dll ... not found" failure (tests/fixtures/autotune/hello-neighbor-*.log).

Checks: the build, the bridge, the catalog, the rule, CI.
"""
from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


# ---- the build step ---------------------------------------------------------
build = (root / 'tools/build-wine-extra-dlls.sh').read_text()
want = build[build.index('WANT="'):]
want = want[:want.index('"\n', 6)]
check('wer is not in the list of DLLs shipped under their own name', not re.search(r'\bwer\b', want))
wer = build[build.index('WER=""'):]
wer = wer[:wer.index('\nfi\n') + 4]
check('it is built only where no wer.dll ships, and MADEIRA_WER_BUILD=0 skips it',
      '"${MADEIRA_WER_BUILD:-1}" != 0' in wer and '! shipped wer' in wer and
      'targets="$targets dlls/wer/arm64ec-windows/wer.dll"; WER=wer' in wer)
guard = re.search(r'\[ -n "\$todo((?:\$[A-Z]+)+)" \] \|\| \{ echo "nothing to build"; exit 0; \}', build)   # the variables in any order (round 624 added ZMP and DCO)
check('the guard "nothing to build" counts it', bool(guard) and '$WER' in re.findall(r'\$[A-Z]+', guard.group(1)))
ship = build[build.index('if [ -n "$WER" ]; then'):]
ship = ship[:ship.index('\nfi\n') + 4]
check('it ships as wer-optin.dll, stripped and padded like the others, and never as wer.dll',
      'cp "$f" "$SHIP/wer-optin.dll.tmp"' in ship and 'llvm-strip" "$SHIP/wer-optin.dll.tmp"' in ship and
      '+ 0x10000' in ship and 'mv "$SHIP/wer-optin.dll.tmp" "$SHIP/wer-optin.dll"' in ship and
      '$SHIP/wer.dll' not in build)
check('the plain wer source is not patched (it is Wine\'s own)',
      'patch-wine-wer' not in build)

# ---- the app ----------------------------------------------------------------
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *werOptIn = getenv("MADEIRA_WER");'):]
blk = blk[:blk.index('\n            }\n')]
check('WineProcessBridge links it for MADEIRA_WER=1: sysx64 in any session, system32 too in an ARM64EC one',
      "if (werOptIn && werOptIn[0] == '1')" in blk and 'use_arm64ec && werOptIn' not in blk and
      'stringByAppendingPathComponent:@"wer-optin.dll"' in blk and
      '[prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"]];' in blk and
      'if (use_arm64ec) [werDirs addObject:sys32Dir];' in blk and
      'stringByAppendingPathComponent:@"wer.dll"' in blk and
      'createSymbolicLinkAtPath:dst withDestinationPath:werDll' in blk)
check('and says so either way',
      'MADEIRA_WER=1: wer.dll -> wer-optin.dll' in blk and 'MADEIRA_WER=1 but this build has no wer-optin.dll' in blk)
check('without the line it removes only a link of ours (to wer-optin.dll) from sysx64 and system32',
      'destinationOfSymbolicLinkAtPath:dst' in blk and '[to.lastPathComponent isEqualToString:@"wer-optin.dll"]' in blk and
      '@[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]' in blk and
      blk.count('removeItemAtPath:dst') == 2)
check('the switch ends with the session', 'unsetenv("MADEIRA_WER");' in bridge)

# ---- catalog, rule, fixture, CI ---------------------------------------------
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
swift = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents env.MADEIRA_WER (off by default) and is generated with it',
      '"env.MADEIRA_WER": {' in gen and 'key: "env.MADEIRA_WER"' in swift and
      re.search(r'key: "env\.MADEIRA_WER"[^\n]*kind: \.bool, defaultValue: "0"', swift))
rules = (root / 'build/tools/autotune_rules.py').read_text()
rule = rules[rules.index('"id": "wer-dll-optin"'):]
rule = rule[:rule.index('\n    },\n')]
check('auto-tune adds the line after a "Library wer.dll ... not found" failure and not again once it ran',
      '"lines": ["env.MADEIRA_WER = 1"]' in rule and 'err:module:import_dll Library wer.dll' in rule and
      '"none": [s("MADEIRA_WER=1: wer.dll -> wer-optin.dll")]' in rule)
# (round 624 part 13 added two more Hello Neighbor fixtures, for the SHA extension: any of them may come first)
check('a fixture proves it', any('env.MADEIRA_WER = 1' in f.read_text() for f in root.glob('tests/fixtures/autotune/hello-neighbor-*.expect')))
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'run: python3 tests/host/check-wer-optin.py' in wf)
check('no game list sets it ahead of a failure',
      'MADEIRA_WER' not in (root / 'app/Madeira/GameRecommendations.swift').read_text()
      if (root / 'app/Madeira/GameRecommendations.swift').exists() else True)

print('PASS: wer opt-in' if ok else 'FAILED: wer opt-in')
sys.exit(0 if ok else 1)
