#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""kernelbase-optin.dll: the Wine fork's own kernelbase for the game that lists it (round 624, agent E2 S3; Glyphica).

Glyphica: Typing Survival failed with ChakraCore's fail-fast with env.MADEIRA_ZERO_MITIGATION_POLICY = 1 set (7611AG, build 600;
J9JDBG, build 623): the key is read by dlls/kernelbase/process.c of the Wine fork, and the shipped arm64ec-windows/kernelbase.dll is
a prebuilt file CI never rebuilds, without a single "MADEIRA_" string (the 623 IPA's kernelbase.dll has none; its
kernelbase-fibertrace.dll, built from the tree, has the key). Build 624 builds the tree's kernelbase as kernelbase-optin.dll
(tools/build-wine-extra-dlls.sh, block ZMP) and the game's list gives it with env.MADEIRA_OPTIN_DLLS = kernelbase.

Checks: tools/verify-kernelbase-optin.py on the shipped kernelbase.dll (refused: no key; accepted with the key's UTF-16 name; refused
for another machine); the build block (its switch, only from a kernelbase the pin left as it is, verified before it ships, after the
fiber-trace block, objects removed); the bridge's opt-in loop links it for the session and skips no kernelbase; in CI the extra DLLs
are built after the i386-only kernelbase patches are put back; the fork's process.c has the switch inside GetProcessMitigationPolicy
(WINE_SRC); the list and the auto-tune rule give the DLL together with the key.
"""
from pathlib import Path
import json, os, shutil, struct, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


shipped = root / 'app/Madeira/arm64ec-windows/kernelbase.dll'
tool = root / 'tools/verify-kernelbase-optin.py'
MARK = 'MADEIRA_ZERO_MITIGATION_POLICY'
if shipped.exists():
    data = shipped.read_bytes()
    check('the shipped kernelbase.dll has no "MADEIRA_" string (the reason for the variant)',
          b'MADEIRA_' not in data and 'MADEIRA_'.encode('utf-16-le') not in data)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)

        def run(dll):
            return subprocess.run([sys.executable, str(tool), str(dll), str(shipped)], capture_output=True, text=True)

        plain = t / 'plain.dll'
        shutil.copyfile(shipped, plain)
        r = run(plain)
        check('the verifier refuses a kernelbase without the key (the shipped one itself)', r.returncode != 0 and 'no UTF-16' in r.stderr, r.stderr[-400:])
        marked = t / 'marked.dll'
        marked.write_bytes(data + MARK.encode('utf-16-le') + b'\0\0')
        r = run(marked)
        check('...accepts the same exports with the key\'s UTF-16 name', r.returncode == 0 and 'present' in r.stdout, (r.stdout + r.stderr)[-400:])
        other = bytearray(data + MARK.encode('utf-16-le'))
        pe = struct.unpack_from('<I', other, 0x3c)[0]
        struct.pack_into('<H', other, pe + 4, 0x8664 if struct.unpack_from('<H', other, pe + 4)[0] != 0x8664 else 0xaa64)
        odd = t / 'other.dll'
        odd.write_bytes(bytes(other))
        r = run(odd)
        check('...and refuses another machine', r.returncode != 0 and 'machine' in r.stderr, r.stderr[-400:])
else:
    print('skip the verifier on the shipped file: no app/Madeira/arm64ec-windows/kernelbase.dll')

sh = (root / 'tools/build-wine-extra-dlls.sh').read_text()
blk = sh[sh.index('# madeira-bcd: kernelbase-optin.dll (see ZMP above)'):]
blk = blk[:blk.index('\nfi\n') + 4]
check('the block: its own switch (MADEIRA_ZMP_BUILD=0 skips it), counted by the "nothing to build" guard',
      'if [ "${MADEIRA_ZMP_BUILD:-1}" != 0 ] && [ -f "$R/wine/dlls/kernelbase/process.c" ] && [ -f "$SHIP/kernelbase.dll" ]; then\n    ZMP=kernelbase' in sh
      and '$FBT$ZMP$WER' in sh)
check('...builds only from a kernelbase the pin left as it is, after the fiber-trace block put its files back',
      'if git -C "$R/wine" diff --quiet -- dlls/kernelbase; then' in blk
      and sh.index('# madeira-bcd: kernelbase-fibertrace.dll (see FBT above)') < sh.index('# madeira-bcd: kernelbase-optin.dll (see ZMP above)'))
check('...strips it like the fiber-trace variant, verifies it, ships it next to the shipped kernelbase.dll, and never replaces that one',
      '"$MINGW/arm64ec-w64-mingw32-strip" --strip-debug "$SHIP/kernelbase-optin.dll.tmp"' in blk
      and 'python3 "$R/tools/verify-kernelbase-optin.py" "$SHIP/kernelbase-optin.dll.tmp" "$SHIP/kernelbase.dll"; then' in blk
      and 'mv "$SHIP/kernelbase-optin.dll.tmp" "$SHIP/kernelbase-optin.dll"' in blk and '"$SHIP/kernelbase.dll"' in blk
      and 'cp "$kb" "$SHIP/kernelbase.dll' not in blk and blk.count('rm -f "$kb" "$B"/dlls/kernelbase/arm64ec-windows/*.o') == 2)

bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
loop = bridge[bridge.index('if (![file hasSuffix:@"-optin.dll"]) continue;'):]
loop = loop[:loop.index('which this build does not ship as %s-optin.dll')]
check('the bridge links any <name>-optin.dll the game lists as <name>.dll (sysx64, and system32 in an ARM64EC session); kernelbase is not skipped',
      'if ([name isEqualToString:@"wer"] || [name isEqualToString:@"wintypes"]) continue;' in loop and 'kernelbase' not in loop
      and '[fm createSymbolicLinkAtPath:dst withDestinationPath:src error:nil];' in loop)

wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('in CI the extra DLLs are built after the i386-only kernelbase patches are put back',
      wf.index('- name: Put back the files patched for the i386 farm only') < wf.index('- name: Build extra Wine DLLs for arm64ec')
      and 'dlls/kernelbase/process.c' in wf[wf.index('- name: Put back the files patched for the i386 farm only'):][:600])

wine = Path(os.environ.get('WINE_SRC', root / 'wine'))
pc = wine / 'dlls/kernelbase/process.c'
if pc.exists():
    src = pc.read_text(errors='replace')
    fn = src[src.index('BOOL WINAPI /* DECLSPEC_HOTPATCH */ GetProcessMitigationPolicy('):]
    fn = fn[:fn.index('\n}\n')]
    check('the fork\'s process.c reads the key inside GetProcessMitigationPolicy (the code kernelbase-optin.dll carries)',
          'GetEnvironmentVariableW( L"MADEIRA_ZERO_MITIGATION_POLICY"' in src and 'zero_mitigation_policy_enabled()' in fn)
else:
    print('skip the Wine source pin: no %s (set WINE_SRC)' % pc)

recs = json.loads((root / 'recommended/recommendations.json').read_text())
gly = next(r for r in recs['rules'] if r['id'] == 'glyphica-typing-survival')
check('Glyphica\'s list gives the DLL from build 624 with the key', {'line': 'env.MADEIRA_OPTIN_DLLS = kernelbase', 'minBuild': 624} in gly['config'])
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the download policy of build 624 lets a list name kernelbase (else a 624 device would refuse the whole online list); older builds '
      'never check the line (minBuild 624, checkPolicy checks only the lines a build gets)',
      '|| $0 == "kernelbase" || $0 == "dcomp" }) else {' in pol and 'or glu32, tdh, gamingtcui, qwave, or kernelbase, dcomp")' in pol
      and 'for (j, line) in lines.enumerated() where everyLine || line.applies(build: build) {' in pol)
rules = (root / 'build/tools/autotune_rules.py').read_text()
r = rules[rules.index('"id": "chakracore-cfg-policy-failfast"'):]
r = r[:r.index('\n    },\n')]
check('the auto-tune rule gives both lines and stops once the fork\'s kernelbase was linked',
      '"lines": ["env.MADEIRA_ZERO_MITIGATION_POLICY = 1", "env.MADEIRA_OPTIN_DLLS = kernelbase"]' in r
      and 's("[WineProc] MADEIRA_OPTIN_DLLS: kernelbase.dll -> kernelbase-optin.dll")' in r)
# tools/check-optin-dlls-shipped.py: run after the extra DLLs are built, it fails the build when a named opt-in DLL is missing
tool2 = root / 'tools/check-optin-dlls-shipped.py'
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    import importlib.util as _iu
    _s = _iu.spec_from_file_location('cods', tool2); _m = _iu.module_from_spec(_s); _s.loader.exec_module(_m)
    names = _m.wanted(root / 'recommended/recommendations.json', root / 'build/tools/autotune_rules.py')
    for n in names:
        (t / (n + '-optin.dll')).write_bytes(b'MZ')
    r_all = subprocess.run([sys.executable, str(tool2), str(t)], capture_output=True, text=True)
    (t / 'kernelbase-optin.dll').unlink()
    r_kb = subprocess.run([sys.executable, str(tool2), str(t)], capture_output=True, text=True)
check('the shipped-DLL check names kernelbase among what the lists and rules want, passes when every DLL is there, and fails naming '
      'kernelbase-optin.dll and its wanters when it is not', 'kernelbase' in names and r_all.returncode == 0
      and r_kb.returncode == 1 and 'kernelbase-optin.dll is not in' in r_kb.stdout and 'glyphica-typing-survival' in r_kb.stdout,
      (r_all.stdout + r_kb.stdout)[-600:])
check('CI runs it after the extra DLLs are built (not continue-on-error)',
      wf.index('- name: Build extra Wine DLLs for arm64ec') < wf.index('- name: Verify the opt-in DLLs the lists and rules name')
      and 'run: python3 tools/check-optin-dlls-shipped.py app/Madeira/arm64ec-windows' in wf
      and 'continue-on-error' not in wf[wf.index('- name: Verify the opt-in DLLs the lists and rules name'):][:200])
check('CI runs this check', 'python3 tests/host/check-kernelbase-optin.py' in wf)
print('PASS: kernelbase-optin' if ok else 'FAIL: kernelbase-optin')
raise SystemExit(0 if ok else 1)
