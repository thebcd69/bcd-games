#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_OPTIN_DLLS: more of Wine's DLLs, each for the games that list it; no Wine runs.

Fort Solis and DRAGON BALL Sparking! ZERO import dxva2.dll, Animal Well XPSPRINT.DLL, Spider-Man: Miles
Morales' crs-video.exe EVR.dll; the 64-bit set ships none, and the programs ended at once with c0000135
(testers' logs, builds 565-574). Shipping them for every game could change a program that loads one
optionally today (Chromium loads dxva2 for video), so each is opt-in, as wer-optin.dll:

  - tools/build-wine-extra-dlls.sh builds OPTIN_WANT and ships each as <name>-optin.dll, never as
    <name>.dll, and not at all where <name>.dll already ships;
  - app/Madeira/WineProcessBridge.m links each *-optin.dll the game lists as <name>.dll (sysx64, and
    system32 in an ARM64EC session), removes only a link of ours for every one it does not list, leaves
    wer and wintypes to their own switches, says what it did, and the switch ends with the session;
  - the catalog documents the switch (empty by default); auto-tune adds dxva2 / xpsprint after the
    import failure (fixtures fort-solis-*, animal-well-*).
"""
from pathlib import Path
import os
import re
import sys

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


build = (root / 'tools/build-wine-extra-dlls.sh').read_text()
want = build[build.index('WANT="'):]
want = want[:want.index('"\n', 6)]
optin = re.search(r'OPTIN_WANT="([^"]*)"', build)
names = optin.group(1).split() if optin else []
check('OPTIN_WANT lists dxva2, evr, xpsprint, sfc, sfc_os and tbs (Diablo II: Resurrected), twinapi.appcore (Hellblade II) (%s)' % names,
      {'dxva2', 'evr', 'xpsprint', 'sfc', 'sfc_os', 'tbs', 'twinapi.appcore'} <= set(names))
check('none of them is in the list shipped under its own name', not any(re.search(r'\b%s\b' % n, want) for n in names))
opt = build[build.index('OPT=""'):]
opt = opt[:opt.index('\nfi\n') + 4]
check('each is built only where no <name>.dll ships, and MADEIRA_OPTIN_BUILD=0 skips them',
      '"${MADEIRA_OPTIN_BUILD:-1}" != 0' in opt and 'shipped "$d" && continue' in opt and
      'targets="$targets dlls/$d/arm64ec-windows/$d.dll"; OPT="$OPT $d"' in opt)
check('the guard "nothing to build" counts them (and setupapi-optin.dll, SDL; dcomp-optin.dll, DCO, round 624)', '$WER$WTY$OPT$SDL$DCO" ]' in build)
ship = build[build.index('for d in $OPT; do'):]
ship = ship[:ship.index('\ndone\n') + 6]
check('each ships as <name>-optin.dll, stripped and padded, never as <name>.dll',
      'cp "$f" "$SHIP/$d-optin.dll.tmp"' in ship and 'llvm-strip" "$SHIP/$d-optin.dll.tmp"' in ship and
      '+ 0x10000' in ship and 'mv "$SHIP/$d-optin.dll.tmp" "$SHIP/$d-optin.dll"' in ship and '$SHIP/$d.dll' not in ship)

bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");'):]
blk = blk[:blk.index('\n            }\n')]
check('the list is split on commas, semicolons and spaces, lowercased, ".dll" optional',
      'characterSetWithCharactersInString:@",; "' in blk and 'lowercaseString' in blk and 'hasSuffix:@".dll"' in blk)
check('only shipped *-optin.dll files are considered; wer and wintypes keep their own switches',
      'contentsOfDirectoryAtPath:ecDir' in blk and 'hasSuffix:@"-optin.dll"' in blk and
      '[name isEqualToString:@"wer"] || [name isEqualToString:@"wintypes"]' in blk)
check('a listed one is linked as <name>.dll in sysx64, and system32 too in an ARM64EC session',
      'use_arm64ec\n                    ? @[ [prefix stringByAppendingPathComponent:@"drive_c/windows/sysx64"], sys32Dir ]' in blk
      and 'createSymbolicLinkAtPath:dst withDestinationPath:src' in blk)
check('one not listed loses only a link of ours', '[to.lastPathComponent isEqualToString:file]' in blk and
      blk.count('removeItemAtPath:dst') == 2)
check('it says what it linked, and what it could not', 'MADEIRA_OPTIN_DLLS: %s -> %s' in blk and
      'which this build does not ship as %s-optin.dll' in blk)
check('the switch ends with the session', 'unsetenv("MADEIRA_OPTIN_DLLS");' in bridge)

gen = (root / 'build/tools/gen-config-catalog.py').read_text()
swift = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents env.MADEIRA_OPTIN_DLLS (empty by default)',
      '"env.MADEIRA_OPTIN_DLLS": {' in gen and re.search(r'key: "env.MADEIRA_OPTIN_DLLS".*defaultValue: ""', swift))
rules = (root / 'build/tools/autotune_rules.py').read_text()
for name, log in (('dxva2', 'dxva2.dll'), ('xpsprint', 'XPSPRINT.DLL')):
    rule = rules[rules.index('"id": "%s-dll-optin"' % name):]
    rule = rule[:rule.index('\n    },')]
    check('auto-tune adds %s after "Library %s ... not found", not again once linked' % (name, log),
          '"lines": ["env.MADEIRA_OPTIN_DLLS = %s"]' % name in rule and 'Library %s' % log in rule and
          'MADEIRA_OPTIN_DLLS: %s.dll -> %s-optin.dll' % (name, name) in rule)
# Senua's Saga: Hellblade II (tester's log XJ7Z6Y, build 576): XCurl.dll and Microsoft.Xbox.Services.GDK.C.Thunks.dll import
# api-ms-win-core-psm-appnotify-l1-1-0.dll; Wine's apisetschema.spec maps it to twinapi.appcore.dll ("[dll-missing] #1
# twinapi.appcore.dll c0000135"), whose directory holds RegisterAppStateChangeNotification (a stub returning
# ERROR_CALL_NOT_IMPLEMENTED) and the Psm* stubs. A dotted name: the bridge keeps it whole (only ",; " split the list, and a
# trailing ".dll" is cut off), and the shipped file is twinapi.appcore-optin.dll.
rule = rules[rules.index('"id": "twinapi-appcore-dll-optin"'):]
rule = rule[:rule.index('\n    },')]
check('auto-tune adds twinapi.appcore after "Library api-ms-win-core-psm-appnotify-l1-1-0.dll ... not found", not again once linked',
      '"lines": ["env.MADEIRA_OPTIN_DLLS = twinapi.appcore"]' in rule and 'err:module:import_dll Library api-ms-win-core-psm-appnotify' in rule and
      'MADEIRA_OPTIN_DLLS: twinapi.appcore.dll -> twinapi.appcore-optin.dll' in rule and '"noRetry": True' in rule)
check('the bridge\'s link name for it is the one the rule\'s "none" signal reads: "<name>.dll -> <file>"',
      'dll.UTF8String, file.UTF8String' in blk and 'stringByAppendingString:@".dll"' in blk and 'substringToIndex:file.length - 10' in bridge)
fx = root / 'tests/fixtures/autotune'
check('fixtures prove the rules', any(fx.glob('fort-solis-*.expect')) and any(fx.glob('animal-well-*.expect')) and
      any(fx.glob('hellblade-2-*.expect')))
import json
recs = json.loads((root / 'recommended/recommendations.json').read_text())
# Senua's Saga: Hellblade II has no list: its only log (XJ7Z6Y) was a copy outside Steam (the lead, round 579); the
# twinapi.appcore DLL and the auto-tune rule are engine-level and stay. No list may name twinapi.appcore before build 579.
named = [r['id'] for r in recs['rules'] if 'twinapi.appcore' in json.dumps(r)]
# 2026-10-24: the recipe of thebcd69/bcd-games#16 by le0-VV (Minecraft Dungeons II) names it; no other list does.
check("only Minecraft Dungeons II's list names twinapi.appcore (Hellblade II's only log was a copy; the DLL and the rule stay engine-level)",
      named == ['minecraft-dungeons-2'])

# oledlg, fwpuclnt and sensapi (2026-10-23, testers' logs of build 580): Digimon Story Cyber Sleuth's exe imports oledlg.dll (PGET5A),
# FINAL FANTASY XV's ffxv_s.exe fwpuclnt.dll (DM9SAF, CDC7AK), Borderlands 3's vivoxsdk.dll SensApi.dll (VFM84T); each ended with
# c0000135. The 32-bit set has all three; the 64-bit one had none. Same mechanism as the DLLs above, one rule each.
NEW3 = {'oledlg': ('oledlg.dll', 'digimon-story-cyber-sleuth-2026-10-22'), 'fwpuclnt': ('fwpuclnt.dll', 'ffxv-demo-2026-10-22'),
        'sensapi': ('SensApi.dll', 'borderlands-3-2026-10-22'), 'glu32': ('GLU32.dll', 'amnesia-rebirth-2026-10-24'),
        'tdh': ('tdh.dll', 'aniimo-2026-10-25'),
        # gamingtcui (round 620): Forza Horizon 4's exe imports it under its API set name (YFK3A6, c0000135 at once).
        'gamingtcui': ('api-ms-win-gaming-tcui-l1-1-0.dll', 'forza-horizon-4-2026-10-27')}
check('OPTIN_WANT also lists oledlg, fwpuclnt, sensapi, glu32, tdh and gamingtcui', set(NEW3) <= set(names))
check('none of the three is in the list shipped under its own name', not any(re.search(r'\b%s\b' % n, want) for n in NEW3))
wine = Path(os.environ.get('WINE_SRC') or (root / 'wine'))
shipdir = root / 'app/Madeira/arm64ec-windows'
check('no oledlg.dll, fwpuclnt.dll or sensapi.dll ships in the 64-bit set (the script skips a DLL that ships)',
      not any((shipdir / (n + '.dll')).exists() or (shipdir / (n.upper() + '.dll')).exists() for n in NEW3))
if (wine / 'dlls/oledlg/Makefile.in').exists():
    for n in NEW3:
        mk = (wine / 'dlls' / n / 'Makefile.in').read_text()
        imports = re.search(r'^IMPORTS\s*=\s*(.*)$', mk, re.M)
        imports = imports.group(1).split() if imports else []
        check('Wine builds %s.dll from dlls/%s; its imports %s all ship in the 64-bit set (or are import-only libraries)' % (n, n, imports),
              re.search(r'^MODULE\s*=\s*%s\.dll$' % n, mk, re.M) and
              all((shipdir / (i + '.dll')).exists() or i in ('uuid', 'dxguid', 'strmiids') for i in imports))
else:
    print('note SKIPPED: no Wine sources here (set WINE_SRC); the three DLLs\' Makefile.in files are not read')
# msi, mspatcha, odbccp32, sxs and rstrtmgr (2026-10-23, the owner's EA app install on build 594, log 19:20:11): the 64-bit
# EABackgroundService.exe imports msi.dll and RstrtMgr.dll, msi.dll imports mspatcha, odbccp32 and sxs; none was in the 64-bit
# set, the service never started (1053) and the MSI rolled back (1627). Only the EA app's launcher entry names them.
EA5 = ['msi', 'mspatcha', 'odbccp32', 'sxs', 'rstrtmgr']
check('OPTIN_WANT also lists msi, mspatcha, odbccp32, sxs and rstrtmgr', set(EA5) <= set(names))
check('none of the five is in the list shipped under its own name', not any(re.search(r'\b%s\b' % n, want) for n in EA5))
check('no msi.dll, mspatcha.dll, odbccp32.dll, sxs.dll or rstrtmgr.dll ships in the 64-bit set',
      not any((shipdir / (n + '.dll')).exists() for n in EA5))
if (wine / 'dlls/msi/Makefile.in').exists():
    for n in EA5:
        mk = (wine / 'dlls' / n / 'Makefile.in').read_text()
        imports = re.search(r'^IMPORTS\s*=\s*(.*)$', mk, re.M)
        imports = imports.group(1).split() if imports else []
        check('Wine builds %s.dll; its imports %s ship in the 64-bit set or are among the five' % (n, imports),
              re.search(r'^MODULE\s*=\s*%s\.dll$' % n, mk, re.M) and
              all((shipdir / (i + '.dll')).exists() or re.search(r'\b%s\b' % i, want) or i in EA5 or i in ('uuid', 'dxguid', 'strmiids')
                  for i in imports))   # want: the DLLs this script builds and ships under their own name (cabinet)
launchers = (root / 'app/Madeira/BCDLaunchers.swift').read_text()
check('only the EA app\'s launcher entry names them',
      launchers.count('"env.MADEIRA_OPTIN_DLLS = msi,mspatcha,odbccp32,sxs,rstrtmgr,dxva2"') == 1 and
      not any('mspatcha' in json.dumps(r) for r in recs['rules']))
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('RecommendationsPolicy lets a downloaded list name oledlg, fwpuclnt and sensapi',
      '["oledlg", "fwpuclnt", "sensapi", "ddraw"].contains($0)' in policy and 'wmadmod, or oledlg, fwpuclnt, sensapi' in policy)
check('the catalog note names the three DLLs', 'oledlg, fwpuclnt and sensapi are three small system DLLs' in gen)
import importlib.util
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
atr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atr)
by_id = {r['id']: r for r in atr.RULES}
for n, (spelled, fixture) in NEW3.items():
    r = by_id.get('%s-dll-optin' % n)
    check('auto-tune has %s-dll-optin: one line, no retry of the unchanged start, not again once linked' % n,
          r and r['lines'] == ['env.MADEIRA_OPTIN_DLLS = %s' % n] and r.get('noRetry') is True and r['priority'] == 10 and
          r['none'] == [{'has': ['MADEIRA_OPTIN_DLLS: %s.dll -> %s-optin.dll' % (n, n)]}])
    sigs = r['any'] if r else []
    check('its signal matches the name as the tester\'s log spelled it (%s) and the usual upper-case spellings' % spelled,
          any(sg['has'][0] == 'err:module:import_dll Library ' + spelled for sg in sigs) and
          any(sg['has'][0] == 'err:module:import_dll Library %s.DLL' % n.upper() for sg in sigs))
    fx = (root / 'tests/fixtures/autotune' / (fixture + '.log')).read_text(errors='replace').split('\n')
    check('fixture %s has the import failure line the rule reads' % fixture,
          any('err:module:import_dll Library ' + spelled in l and 'not found' in l for l in fx))
    check('its fixture proves the rule', '"%s-dll-optin"' % n in (root / 'tests/fixtures/autotune' / (fixture + '.expect')).read_text())
# qwave (round 623): Bloons TD 6 (Epic copy, 4EJFKD, build 622) loads Plugins\x86_64\discord_partner_sdk.dll (the Discord Social SDK), which
# imports qwave.dll (Windows' quality-of-service API); the 64-bit set lacks it, and Unity IL2CPP ends the process with "Unable to load DLL
# 'discord_partner_sdk'". Mycopunk (Unity Mono; BP35FG, NEQQN0) has the same import failure but only logs the exception, so the DLL is
# opt-in and the auto-tune rule needs Unity's fatal text as well as the import line.
check('OPTIN_WANT also lists qwave', 'qwave' in names)
check('qwave is not in the list shipped under its own name and no qwave.dll ships in the 64-bit set',
      not re.search(r'\bqwave\b', want) and not (shipdir / 'qwave.dll').exists() and not (shipdir / 'QWAVE.dll').exists())
if (wine / 'dlls/qwave/Makefile.in').exists():
    mk = (wine / 'dlls/qwave/Makefile.in').read_text()
    check('Wine builds qwave.dll from dlls/qwave and it imports nothing beyond the kernel (no IMPORTS line)',
          re.search(r'^MODULE\s*=\s*qwave\.dll$', mk, re.M) and not re.search(r'^IMPORTS\s*=', mk, re.M))
rq = by_id.get('qwave-dll-optin')
check('auto-tune has qwave-dll-optin: one line, no retry of the unchanged start, not again once linked',
      rq and rq['lines'] == ['env.MADEIRA_OPTIN_DLLS = qwave'] and rq.get('noRetry') is True and rq['priority'] == 10 and
      rq['none'] == [{'has': ['MADEIRA_OPTIN_DLLS: qwave.dll -> qwave-optin.dll']}])
check('qwave-dll-optin needs Unity\'s fatal text and the import failure, as the tester\'s log spelled it (qwave.dll) and in capitals',
      rq and rq['all'] == [{'has': ['UnhandledExceptionHandler caught: Unable to load DLL']}] and
      any(sg['has'][0] == 'err:module:import_dll Library qwave.dll' for sg in rq['any']) and
      any(sg['has'][0] == 'err:module:import_dll Library QWAVE.DLL' for sg in rq['any']))
fxq = (root / 'tests/fixtures/autotune/bloons-td-6-qwave-2026-10-28.log').read_text(errors='replace').split('\n')
check('the Bloons TD 6 fixture has the import failure and Unity\'s fatal text',
      any('err:module:import_dll Library qwave.dll' in l and 'not found' in l for l in fxq) and
      any('UnhandledExceptionHandler caught: Unable to load DLL' in l for l in fxq))
check('its fixture proves the rule', '"qwave-dll-optin"' in (root / 'tests/fixtures/autotune/bloons-td-6-qwave-2026-10-28.expect').read_text())
check('RecommendationsPolicy lets a downloaded list name qwave', '"gamingtcui", "qwave"].contains($0)' in policy and 'tdh, gamingtcui, qwave' in policy)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-optin-dlls.py' in wf)
print('PASS: opt-in DLLs' if ok else 'FAILED: opt-in DLLs')
sys.exit(0 if ok else 1)
