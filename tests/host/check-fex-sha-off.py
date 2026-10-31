#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_FEX_SHA = 0: one game's guest is told the processor has no SHA extension; no Wine or FEX runs.

Gal*Gun 2 (Steam 851890; CWFP3N, B2B7BR, build 622, iPad16,5) and Hello Neighbor (10 logs of build 574, iPhone14,5) stopped with a jump
to <heap pointer low half>0000003c (or to 0x3c itself) right after the program loaded NETAPI32.DLL; the UE4 window "The UE4-<project> Game
has crashed and will close" followed. The registers at the jump are those of OpenSSL 1.0.2's SHA-1 routine for the SHA extension (R8, R9
and R10 still hold FEX's CPUID words, among them leaf 7 EBX with bit 29 = SHA), RSP is the SHA_CTX's data block + 64 and the popped
qword is the context's `num` (60). The routine's Win64 epilogue restores RSP from a register its loop reuses. OpenSSL takes that routine
only when CPUID says the processor has the SHA extension, which BCD reports on every device (FEX's iOS host features, SupportsSHA). With
the key the app overwrites the SHA entry of FEX's host probe with 0; FEX then reports no SHA (CPUID leaf 7 EBX bit 29 clear) and emulates
any SHA instruction without the host's, and OpenSSL takes its other SHA-1 code.

What this checks:
  1. app/Madeira/WineProcessBridge.m, madeira_publish_host_probe: the probe table has the one key "SHA" and no key before it ends in
     "SHA" (the rewrite finds the first "SHA="); the rewrite reads MADEIRA_FEX_SHA once, after the probe loop and before the probe is
     exported, acts on the exact value 0 only and logs it. The block is compiled on the host and run: unset, 1, empty, 00, " 0" and
     "0 " change nothing; 0 turns SHA=1 and SHA=? into SHA=0 and leaves every other entry as it was; FEX's own parser (the Absent lambda,
     ported here) then reads SHA as absent and nothing else.
  2. The game's env. lines are exported (setenv, "[madeira-env] game") before the probe is published, in the same thread function.
  3. FEX (where its source is at hand: $BCD_FEX_SRC, FEX/ of this checkout, ~/madeira-bcd/FEX): CPUFeatures.cpp turns SupportsSHA off
     for an explicit SHA=0 only, at the start of an entry; CPUID.cpp reports leaf 7 EBX bit 29 from it; the SHA instructions have a
     fallback when it is off; ImageTracker.cpp prints the "Load module" line the auto-tune rule reads.
  4. Opt-in: default on (the catalog says 1); no app code sets the key (the wild-jump dump of signal_arm64_ios.c only names it, in a
     line for the case RAX == RSP-8); the list policy accepts 0 or 1; the list sets 0 for Gal*Gun 2 and
     Hello Neighbor only, from build 624; auto-tune's rule fex-sha-off-openssl-shaext proposes it after this crash only, and its four
     fixtures (two of each game, one with the jump to 0x3c itself) propose it.
  5. The settings catalog (generator) knows the key and the CI runs this check.
Needs python3 and a C compiler.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


# ---------------------------------------------------------------- 1. the probe and the rewrite
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
fn = re.search(r'static void madeira_publish_host_probe\(void\)\n\{.*?\n\}\n', bridge, re.S)
check('madeira_publish_host_probe exists', fn is not None)
body = fn.group(0) if fn else ''
keys = re.findall(r'\{ "([A-Z0-9]+)",\s+"hw\.optional\.[^"]+" \}', body)
check('the probe table has the key SHA once, from FEAT_SHA256', keys.count('SHA') == 1
      and '{ "SHA",     "hw.optional.arm.FEAT_SHA256" }' in body, keys)
before = keys[:keys.index('SHA')] if 'SHA' in keys else keys
check('no key before SHA ends in "SHA" (the rewrite takes the first "SHA=" of the string)',
      not any(k.endswith('SHA') for k in before), before)
loop_end = body.find('if (len >= sizeof(buf)) return;')
block_at = body.find('const char *shaEnv = getenv("MADEIRA_FEX_SHA");')
export_at = body.find('setenv("FEX_MADEIRA_HOSTPROBE", buf, 1);')
check('the rewrite comes after the probe loop and before the probe is exported',
      0 <= loop_end < block_at < export_at, (loop_end, block_at, export_at))
check('the bridge reads the key in this one place', bridge.count('getenv("MADEIRA_FEX_SHA")') == 1)
m = re.search(r'\n    \{\n        const char \*shaEnv = getenv\("MADEIRA_FEX_SHA"\);\n.*?\n    \}\n', body, re.S)
check('the rewrite is one block that acts on the exact value 0 and logs it',
      m is not None and "shaEnv && shaEnv[0] == '0' && shaEnv[1] == '\\0'" in m.group(0)
      and 'MADEIRA_FEX_SHA=0: the guest is told the processor has no SHA extension' in m.group(0), m.group(0) if m else body[-900:])
cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
if m and cc:
    harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void rewrite(char *buf)
{
''' + m.group(0) + r'''
}

/* FEX Source/Windows/Common/CPUFeatures.cpp, the iOS branch's Absent lambda */
static int absent(const char *Probe, const char *Key)
{
    size_t Len = strlen(Key);
    for (const char *p = Probe; (p = strstr(p, Key)) != NULL; p += Len) {
        int AtStart = p == Probe || p[-1] == ',';
        if (AtStart && p[Len] == '=') return p[Len + 1] == '0';
    }
    return 0;
}

static const char *keys[] = { "AFP", "FLAGM", "FLAGM2", "FCMA", "RCPC", "AES", "PMULL", "SHA", "CRC", "ATOMICS" };

int main(void)
{
    int fails = 0;
    const char *probes[] = {
        "AFP=1,FLAGM=1,FLAGM2=1,FCMA=1,RCPC=1,LRCPC2=1,AES=1,PMULL=1,SHA=1,CRC=1,ATOMICS=1,LSE2=1,LRCPC3=0,FRINTTS=1,RPRES=0,CSSC=0,MOPS=0,ECV=1,WFXT=0",
        "AFP=0,FLAGM=0,FLAGM2=0,FCMA=1,RCPC=1,LRCPC2=1,AES=1,PMULL=1,SHA=?,CRC=1,ATOMICS=1,LSE2=1,LRCPC3=?,FRINTTS=1,RPRES=?,CSSC=?,MOPS=?,ECV=?,WFXT=?",
        "AFP=?,FLAGM=1,FLAGM2=0,FCMA=1,RCPC=1,LRCPC2=1,AES=1,PMULL=1,SHA=0,CRC=1,ATOMICS=1,LSE2=1,LRCPC3=0,FRINTTS=1,RPRES=0,CSSC=0,MOPS=0,ECV=0,WFXT=0",
    };
    const char *off[] = { NULL, "1", "", "00", " 0", "0 ", "01", "no" };
    for (size_t i = 0; i < sizeof(probes) / sizeof(probes[0]); i++) {
        for (size_t j = 0; j < sizeof(off) / sizeof(off[0]); j++) {
            char buf[256];
            snprintf(buf, sizeof(buf), "%s", probes[i]);
            if (off[j]) setenv("MADEIRA_FEX_SHA", off[j], 1); else unsetenv("MADEIRA_FEX_SHA");
            rewrite(buf);
            if (strcmp(buf, probes[i])) { printf("FAIL value '%s' changed probe %zu: %s\n", off[j] ? off[j] : "(unset)", i, buf); fails++; }
        }
        char buf[256];
        snprintf(buf, sizeof(buf), "%s", probes[i]);
        setenv("MADEIRA_FEX_SHA", "0", 1);
        rewrite(buf);
        if (strlen(buf) != strlen(probes[i])) { printf("FAIL the length changed: %s\n", buf); fails++; }
        size_t diff = 0;
        for (size_t k = 0; probes[i][k]; k++) diff += buf[k] != probes[i][k];
        if (diff != (strstr(probes[i], "SHA=0") ? 0u : 1u)) { printf("FAIL %zu characters changed in probe %zu: %s\n", diff, i, buf); fails++; }
        if (!strstr(buf, ",SHA=0,")) { printf("FAIL no SHA=0 in probe %zu: %s\n", i, buf); fails++; }
        for (size_t k = 0; k < sizeof(keys) / sizeof(keys[0]); k++) {
            int want = strcmp(keys[k], "SHA") ? absent(probes[i], keys[k]) : 1;
            if (absent(buf, keys[k]) != want) { printf("FAIL FEX reads %s as %s in probe %zu\n", keys[k], want ? "present" : "absent", i); fails++; }
        }
    }
    if (!fails) puts("rewrite-ok");
    return fails != 0;
}
'''
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        (t / 'h.c').write_text(harness)
        b = subprocess.run([cc, '-std=c11', '-Wall', '-Werror', '-o', str(t / 'h'), str(t / 'h.c')], capture_output=True, text=True)
        check('the rewrite compiles on the host', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            r = subprocess.run([str(t / 'h')], capture_output=True, text=True)
            check('only the exact value 0 acts; it turns SHA=1 and SHA=? into SHA=0 in place, and FEX\'s parser then reads SHA absent and '
                  'every other feature as before', r.returncode == 0 and 'rewrite-ok' in r.stdout, r.stdout + r.stderr)
elif not cc:
    print('skip the host compile: no C compiler')

# ---------------------------------------------------------------- 2. the game's env lines come first
thread = re.search(r'static void \*wine_process_thread\(void \*arg\) \{.*', bridge, re.S)
ttext = thread.group(0) if thread else ''
env_at = ttext.find('fprintf(stderr, "[madeira-env] game %s=%s\\n", k.UTF8String, v.UTF8String);')
set_at = ttext.rfind('setenv(k.UTF8String, v.UTF8String, 1);', 0, env_at if env_at >= 0 else 0)
call_at = ttext.find('madeira_publish_host_probe();')
main_at = ttext.find('__wine_main(argc, argv);')
check('wine_process_thread exports the game\'s env. lines, then publishes the probe, then starts Wine',
      0 <= set_at < env_at < call_at < main_at, (set_at, env_at, call_at, main_at))

# ---------------------------------------------------------------- 3. FEX
def sh(*args, cwd=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


pinned = ''
mm = re.match(r'160000 commit ([0-9a-f]{40})\tFEX$', sh('git', 'ls-tree', 'HEAD', 'FEX', cwd=root))
if mm:
    pinned = mm.group(1)
candidates = ([Path(os.environ['BCD_FEX_SRC']).expanduser()] if os.environ.get('BCD_FEX_SRC') else []) \
    + [root / 'FEX', Path('~/madeira-bcd/FEX').expanduser()]
fex = next((c for c in candidates if (c / 'Source/Windows/Common/CPUFeatures.cpp').is_file()), None)
if fex is None:
    print('skip FEX\'s source is not at hand (set BCD_FEX_SRC to a checkout of the FEX fork)')
else:
    head = sh('git', 'rev-parse', 'HEAD', cwd=fex)
    if pinned and head and head != pinned:
        print('note the FEX tree %s is at %s, this checkout pins %s: checked anyway' % (fex, head[:12], pinned[:12]))
    feat = (fex / 'Source/Windows/Common/CPUFeatures.cpp').read_text()
    check('FEX: the iOS host features assume SHA and the probe turns it off for SHA=0',
          'HostFeatures.SupportsSHA = true;' in feat and 'if (Absent("SHA")) HostFeatures.SupportsSHA = false;' in feat)
    check('FEX: the probe parser takes a key only at the start of an entry and only an explicit 0 as absent',
          "const bool AtStart = p == Probe || p[-1] == ',';" in feat and "if (AtStart && p[Len] == '=') {" in feat
          and "return p[Len + 1] == '0';" in feat and 'getenv("FEX_MADEIRA_HOSTPROBE")' in feat)
    cpuid = (fex / 'FEXCore/Source/Interface/Core/CPUID.cpp').read_text()
    check('FEX: CPUID leaf 7 EBX bit 29 is SupportsSHA', 'Features.SHA = CTX->HostFeatures.SupportsSHA;' in cpuid
          and re.search(r'\(Features\.SHA << 29\)', cpuid) is not None)
    crypto = fex / 'FEXCore/Source/Interface/Core/OpcodeDispatcher/Crypto.cpp'
    ctext = crypto.read_text() if crypto.is_file() else ''
    check('FEX: the SHA instructions have a path without the host\'s SHA (SupportsSHA tested in the dispatcher)',
          ctext.count('if (!CTX->HostFeatures.SupportsSHA) {') >= 4, ctext.count('if (!CTX->HostFeatures.SupportsSHA) {'))
    tracker = fex / 'Source/Windows/Common/ImageTracker.cpp'
    check('FEX: ImageTracker prints the "Load module" line the auto-tune rule reads',
          tracker.is_file() and 'LogMan::Msg::DFmt("Load module {} ({}): {:X}", ModuleName, ID, Address);' in tracker.read_text())
    print('  (FEX tree %s at %s)' % (fex, head[:12] or '?'))

# ---------------------------------------------------------------- 4. opt-in, the list, auto-tune
readers = []
for path in list((root / 'app/Madeira').rglob('*.swift')) + list((root / 'app/Madeira').rglob('*.m')) \
        + list((root / 'app/Madeira').rglob('*.c')) + list((root / 'build').rglob('*.c')) + list((root / 'build').rglob('*.h')):
    if path.name in ('ConfigCatalog.generated.swift', 'AutoTuneRules.generated.swift') or not path.is_file():
        continue
    try:
        text = path.read_text(errors='replace')
    except OSError:
        continue
    if '"MADEIRA_FEX_SHA"' in text or re.search(r'(?:get|set|unset)env\(\s*"MADEIRA_FEX_SHA', text):
        readers.append(str(path.relative_to(root)))
check('only the bridge and the list policy use the key in the app and the C sources (nothing sets it by default)',
      sorted(readers) == ['app/Madeira/GameRecommendationsList.swift', 'app/Madeira/WineProcessBridge.m'], sorted(readers))
sig = (root / 'build/ntdll-unix/signal_arm64_ios.c').read_text()
check('the wild-jump dump names the RAX == RSP-8 case (text only: one dprintf behind the comparison)',
      re.search(r'if \(rsp >= 8 && rax == rsp - 8\)\n\s+dprintf\( STDERR_FILENO, "\[badrip\]   RAX == RSP-8: ', sig) is not None)
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
allowed = pol.split('static let allowedEnvNames')[1].split(']')[0] if 'static let allowedEnvNames' in pol else ''
case_at = pol.find('case "MADEIRA_FEX_SHA":')
case_body = pol[case_at:] if case_at >= 0 else ''
case_body = case_body[:case_body.find('\n            case ')]
check('a downloaded list may set it, 0 or 1 (RecommendationsPolicy)',
      '"MADEIRA_FEX_SHA"' in allowed and case_at >= 0 and 'guard value == "0" || value == "1"' in case_body, case_body[:400])
recs = json.loads((root / 'recommended/recommendations.json').read_text())
setters = {}
for r in recs['rules']:
    for key in ('config', 'smallRAMConfig'):
        for c in r.get(key, []):
            line = c['line'] if isinstance(c, dict) else c
            if 'MADEIRA_FEX_SHA' in line:
                setters.setdefault(r['id'], []).append(c)
check('the list sets it for Gal*Gun 2 and Hello Neighbor only, to 0, from build 624',
      setters == {'gal-gun-2': [{'line': 'env.MADEIRA_FEX_SHA = 0', 'minBuild': 624}],
                  'hello-neighbor': [{'line': 'env.MADEIRA_FEX_SHA = 0', 'minBuild': 624}]}, setters)
byid = {r['id']: r for r in recs['rules']}
progs = {rid: sorted(p['name'] for p in byid[rid]['match'].get('programs', [])) for rid in ('gal-gun-2', 'hello-neighbor') if rid in byid}
check('the two rows match the programs of the logs (the launcher and the UE4 shipping exe)',
      progs == {'gal-gun-2': ['galgun2-win64-shipping.exe', 'galgun2.exe'],
                'hello-neighbor': ['helloneighbor-win64-shipping.exe', 'helloneighbor.exe']}, progs)
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r['id'] == 'fex-sha-off-openssl-shaext'), None)
check('auto-tune has the rule with exactly the one line, no retry first, after a crash or a hang',
      rule is not None and rule['lines'] == ['env.MADEIRA_FEX_SHA = 0'] and rule.get('noRetry') is True
      and rule.get('outcomes') == ['crashed', 'hung'])
if rule:
    flat = json.dumps(rule['all']) + json.dumps(rule.get('any', []))
    check('the rule needs the NETAPI32.DLL load and the jump to <pointer>0000003c or 0x3c, and is off once the line is in force',
          'Load module NETAPI32.DLL' in json.dumps(rule['all']) and '0000003c pc=0x' in flat and 'rip=0x3c pc=0x' in flat
          and 'UNMAPPED AT ENTRY (wild pointer)' in flat and 'MADEIRA_FEX_SHA=0' in json.dumps(rule.get('none', [])))
    others = [r['id'] for r in mod.RULES if r['id'] != rule['id'] and 'MADEIRA_FEX_SHA' in json.dumps(r.get('lines', []))]
    check('no other auto-tune rule adds the key', others == [], others)
for name in ('gal-gun-2-sha-extension-2026-10-29', 'gal-gun-2-sha-extension-b2b7br-2026-10-29',
             'hello-neighbor-sha-extension-gr23pc-2026-10-20', 'hello-neighbor-sha-extension-taw359-2026-10-20'):
    exp = root / 'tests/fixtures/autotune' / (name + '.expect')
    data = json.loads(exp.read_text()) if exp.is_file() else {}
    check('fixture %s proposes the line' % name,
          data.get('next') == ['env.MADEIRA_FEX_SHA = 0'] and (data.get('matched') or [None])[0] == 'fex-sha-off-openssl-shaext', data)

# ---------------------------------------------------------------- 5. catalog and CI
cat = (root / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the key (bool, default 1)',
      re.search(r'"env\.MADEIRA_FEX_SHA": \{[^}]*?"kind": "bool", "default": "1"', cat, re.S) is not None)
ci = (root / '.github/workflows/build-ipa.yml').read_text()
check('the CI runs this check', 'python3 tests/host/check-fex-sha-off.py' in ci)

print('PASS: SHA extension hidden per game' if ok else 'FAIL: SHA extension hidden per game')
sys.exit(0 if ok else 1)
