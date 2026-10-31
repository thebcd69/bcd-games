#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Fallout 3 / New Vegas's older NVIDIA card (build 623): DXMT's d3d9.customDriver and BCD's GeForce 7900 GS card.

New Vegas picks its render path from the Direct3D 9 adapter's description AND its driver file; with BCD's Apple name and DXMT's
driver it takes the basic path and crashes loading a save at 00B57AA9 (BCD's fallout-new-vegas notes: four logs). Upstream's cure
(willfaust/dxmt f25e5568d, Madeira adec653): a GeForce 7900 GS (10de:0292) on nv4_disp.dll. This checks every piece:
  - tools/patch-dxmt-d3d9-custom-driver.py on a copy of the pinned d3d9_interface.cpp (dxmt/src or $DXMT_SRC_DIR; in CI the patch
    step may have run already): it applies, a second run says "already patched", the option defaults to empty and the Driver field
    is written only when it is set (nothing changes for any other game), after the description and before the log line, which marks
    the override like the other three;
  - CI patches dxmt with it before the i386 farm (Fallout is 32-bit) and runs this check;
  - the card: app table (0x0292, nv4_disp.dll, the only card with a driver), its DXMT options under DXMT's 259-character limit with
    the names, win32u's known identities, the settings catalog's choice;
  - the prefs sync (sD3DDevice in FalloutPrefs.ini) runs before the card is applied, by App ID or the program's name;
  - New Vegas's list line (minBuild 623) and the auto-tune rule after the crash, with its fixture.
The Swift behaviour (options, prefs text, lookup) is run by tests/host/check-gpu-cards.py. SKIP of the copy part without dxmt sources.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        ok = False
        if detail:
            print('     ' + str(detail)[:1500])


def read(rel):
    return (R / rel).read_text(encoding='utf-8')


# --- CI -------------------------------------------------------------------------------------------------------------------------
wf = read('.github/workflows/build-ipa.yml')
patch_at = wf.find('python3 tools/patch-dxmt-d3d9-custom-driver.py')
check('CI applies the patch and runs this check',
      patch_at >= 0 and 'python3 tests/host/check-dxmt-d3d9-custom-driver.py' in wf)
farm_steps = [m.start() for m in re.finditer(r'(?m)^\s*- name: .*i386', wf)]
check('the patch step comes before the i386 steps (New Vegas and Fallout 3 are 32-bit: their d3d9.dll is the farm\'s)',
      patch_at >= 0 and farm_steps and patch_at < farm_steps[0], (patch_at, farm_steps[:3]))

# --- the DXMT patch on a copy -----------------------------------------------------------------------------------------------------
script = R / 'tools/patch-dxmt-d3d9-custom-driver.py'
src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src' / 'd3d9' / 'd3d9_interface.cpp'
if not src.exists():
    print(f'SKIP: no {src}')
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d9').mkdir(parents=True)
        shutil.copy(src, t / 'dxmt/src/d3d9/d3d9_interface.cpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies (or finds the file patched) and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and r2.returncode == 0 and 'already patched' in r2.stdout,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        s = (t / 'dxmt/src/d3d9/d3d9_interface.cpp').read_text()
        a = s.index('struct AdapterIdOverride {')
        struct = s[a:s.index('};', a)]
        check('the override has a driver field', 'std::string driver;' in struct)
        check('d3d9.customDriver is read from DXMT_CONFIG with an empty default (off)',
              s.count('o.driver = cfg.getOption<std::string>("d3d9.customDriver", "");') == 1)
        desc_at = s.find('std::snprintf(pIdentifier->Description, MAX_DEVICE_IDENTIFIER_STRING, "%s", ov.desc.c_str());')
        guard_at = s.find('if (!ov.driver.empty())')
        drv_at = s.find('std::snprintf(pIdentifier->Driver, MAX_DEVICE_IDENTIFIER_STRING, "%s", ov.driver.c_str());')
        log_at = s.find('"GetAdapterIdentifier adapter=%u')
        default_at = s.find('std::snprintf(pIdentifier->Driver, MAX_DEVICE_IDENTIFIER_STRING, "%s", "DXMT (Metal)");')
        check('the Driver field is overwritten only when the option is set: after DXMT\'s own name and the description, before the log line',
              0 <= default_at < desc_at < guard_at < drv_at < log_at and s.count('pIdentifier->Driver, MAX_DEVICE_IDENTIFIER_STRING') == 2
              and s[guard_at:drv_at].count('\n') == 1, (default_at, desc_at, guard_at, drv_at, log_at))
        check('the adapter log line marks a driver override like the other three',
              '(ov.vendorId >= 0 || ov.deviceId >= 0 || !ov.desc.empty() || !ov.driver.empty()) ? "  [overridden by DXMT_CONFIG d3d9.custom*]"' in s)
        clang = shutil.which('clang++')
        if clang:
            # The struct and its reader compile as they are written (a syntax slip in the edit fails here, not in the farm).
            probe = t / 'probe.cpp'
            probe.write_text('#include <string>\n#include <cstdio>\n' + struct + '};\nint main(){AdapterIdOverride o{}; o.driver = "nv4_disp.dll";'
                             ' char d[512]; if (!o.driver.empty()) std::snprintf(d, 512, "%s", o.driver.c_str()); return d[0] == \'n\' ? 0 : 1;}\n')
            c = subprocess.run([clang, '-std=c++17', '-fsyntax-only', str(probe)], capture_output=True, text=True)
            check('the patched override struct compiles', c.returncode == 0, c.stderr[-1500:])

# --- the card ---------------------------------------------------------------------------------------------------------------------
cards = read('app/Madeira/BCDGPUCards.swift')
line = [l for l in cards.splitlines() if 'id: "nvidia-7900gs"' in l]
check('the app has the card: GeForce 7900 GS, NVIDIA 0x0292, nv4_disp.dll',
      len(line) == 1 and 'vendor: nvidia, device: 0x0292, name: "NVIDIA GeForce 7900 GS", driver: "nv4_disp.dll"' in line[0], line)
check('it is the only card with a driver file', cards.count('driver: "') == 1)
check('its DXMT options carry the driver (kept when the names must go)',
      'if let driver = card.driver { out.append("d3d9.customDriver=\\(driver)") }' in cards
      and cards.index('d3d9.customDriver=') < cards.index('if names {'))
opts = [f'{api}.custom{k}' for api in ('dxgi', 'd3d9') for k in ('VendorId=10de', 'DeviceId=0292')] + ['d3d9.customDriver=nv4_disp.dll'] \
    + [f'{api}.customDeviceDesc="NVIDIA GeForce 7900 GS"' for api in ('dxgi', 'd3d9')]
limit = int(re.search(r'static let dxmtConfigLimit = (\d+)', cards).group(1))
check(f'the card\'s options with their names fit DXMT\'s limit ({len(";".join(opts))} of {limit} characters; New Vegas\'s list has no DXMT lines)',
      len(';'.join(opts)) <= limit and limit == 259)
check('win32u knows the identity (a saved prefix may hold it)',
      '{ 0x10de, 0x0292 },' in read('build/win32u-unix/sysparams_ios.c'))
check('the settings catalog offers it (generator and generated file)',
      '("nvidia-7900gs", "NVIDIA GeForce 7900 GS (older Direct3D 9 games)")' in read('build/tools/gen-config-catalog.py')
      and 'nvidia-7900gs' in read('app/Madeira/ConfigCatalog.generated.swift'))

# --- the prefs sync ---------------------------------------------------------------------------------------------------------------
cv = read('app/Madeira/ContentView.swift')
sync = cv.find('BCDGPUCardsLive.syncGamePrefs(appID: profile?.steamAppID, program: profile?.relativePath)')
apply = cv.find('BCDGPUCardsLive.apply(to: &dxmtOptions)')
check('the launch syncs the game\'s prefs (by App ID or program) right before it applies the card', 0 <= sync < apply and cv.count('syncGamePrefs(') == 1)
live = read('app/Madeira/BCDGPUCardsLive.swift')
check('the sync looks the file up through BCDGPUCards.prefsFile and writes only a changed text',
      'BCDGPUCards.prefsFile(appID: appID, program: program)' in live and 'BCDGPUCards.prefsText(' in live
      and 'try out.write(to: file, options: .atomic)' in live)

# --- New Vegas's list and the auto-tune rule --------------------------------------------------------------------------------------
recs = json.loads(read('recommended/recommendations.json'))
fnv = [r for r in recs['rules'] if r['id'] == 'fallout-new-vegas']
check('New Vegas\'s list gives the card from build 623 (v3)',
      len(fnv) == 1 and {'line': 'gpu-card = nvidia-7900gs', 'minBuild': 623} in fnv[0]['config'] and fnv[0]['version'] == 3)
rules = read('build/tools/autotune_rules.py')
a = rules.find('"id": "fallout-nv-older-nvidia-card"')
rule = rules[a:rules.find('\n    },', a)]
check('auto-tune gives the same line after the crash at 00B57AA9 in New Vegas, not once the card is on',
      a >= 0 and '"lines": ["gpu-card = nvidia-7900gs"]' in rule
      and 's("Unhandled page fault on", "access to 00000000 at address 00B57AA9")' in rule   # read (613+, LDAPR label) or write
      and 's("FalloutNV")' in rule and '"none": [s("[gpu-card] nvidia-7900gs")]' in rule)
check('the app carries the rule (generated file)', '"fallout-nv-older-nvidia-card"' in read('app/Madeira/AutoTuneRules.generated.swift'))
fx = R / 'tests/fixtures/autotune/fallout-nv-b57aa9-2026-10-23.expect'
check('its fixture (6RMMDY, build 592) expects the line',
      fx.exists() and json.loads(fx.read_text()).get('next') == ['gpu-card = nvidia-7900gs']
      # round 624: the lowest-priority diagnostic rule x86-null-fault-diag may match the same 32-bit fault after it; the first match decides.
      and (json.loads(fx.read_text()).get('matched') or [None])[0] == 'fallout-nv-older-nvidia-card')

if not ok:
    print('FAILED: dxmt-d3d9-custom-driver')
    sys.exit(1)
print('PASS: Fallout\'s older NVIDIA card: d3d9.customDriver, the GeForce 7900 GS card, the prefs sync, the list line and the auto-tune rule')
