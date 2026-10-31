#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_ZERO_MITIGATION_POLICY: Wine's GetProcessMitigationPolicy zeroes the caller's buffer; opt-in; no Wine runs.

Glyphica: Typing Survival (Steam 2400160, neosphere.exe: the Sphere engine neoSphere with ChakraCore.dll; 777YHV and JAHWGD, one start, build
592, iPad16,3) ends 0.8 s after Wine starts with exit c0000602, STATUS_FAIL_FAST_EXCEPTION, before the game's own code runs. The unwinder's
frames are ChakraCore.dll's static constructor GlobalSecurityPolicy (lib/Common/Core/GlobalSecurityPolicy.cpp): it loads
api-ms-win-core-memory-l1-1-3.dll, calls GetProcessMitigationPolicy( ProcessControlFlowGuardPolicy = 7, &local, 4 ) and returns the bit
`EnableControlFlowGuard` of that local, which it never cleared; when the bit is set it looks up SetProcessValidCallTargets (the Wine fork's
kernelbase.spec has it only as a commented stub) and calls RaiseFailFastException( NULL, NULL, FAIL_FAST_GENERATE_EXCEPTION_ADDRESS ). The
log shows the call ("fixme:process:GetProcessMitigationPolicy (FFFFFFFFFFFFFFFF, 7, 000000702580F2F8, 4): stub") and, on the next line, the
unhandled exception. Wine's stub answers TRUE and writes nothing: the answer is whatever the stack held.

What this checks:
  1. The Wine fork (dlls/kernelbase/process.c, where one is at hand: $BCD_WINE_SRC, wine/ of this checkout, ~/madeira-bcd/wine; skipped when the
     tree is not the commit this checkout pins, failed when it is that commit and lacks the change): the two functions are extracted from the
     source and compiled on the host with stubs for the Windows API, then run over the cases. Off (unset, 0, empty, 11, 2, yes, true): the buffer is
     not touched and the stub still answers TRUE. Exactly 1: a buffer of 1 to 16 bytes is zeroed and nothing after it; a NULL buffer, a length of
     0 or of 17 is left alone; the last error is the one the caller had.
  2. The list: only glyphica-typing-survival carries the line, from build 600 (rule and line), and it matches the game by App ID, folder and
     the program beside ChakraCore.dll; the policy takes it (0 or 1) and the standard list does not have it.
  3. Auto-tune: the rule chakracore-cfg-policy-failfast adds the line after that crash only (stage 2 at most, the exit c0000602 and ChakraCore's
     frames), once (noRetry), never when the line is already in force; its fixture proposes the line, no other fixture matches it.
  4. Nothing else sets it: no app code, no launcher entry, no other list; the settings catalog knows the key; the CI runs this check.
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
NAME = 'MADEIRA_ZERO_MITIGATION_POLICY'
RULE = 'chakracore-cfg-policy-failfast'
LINE = 'env.%s = 1' % NAME


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def sh(*args, cwd=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


# ---------------------------------------------------------------- 1. the Wine fork
pinned = ''
m = re.match(r'160000 commit ([0-9a-f]{40})\twine$', sh('git', 'ls-tree', 'HEAD', 'wine', cwd=root))
if m:
    pinned = m.group(1)
candidates = []
if os.environ.get('BCD_WINE_SRC'):
    candidates.append(Path(os.environ['BCD_WINE_SRC']).expanduser())
candidates += [root / 'wine', Path('~/madeira-bcd/wine').expanduser()]
wine = None
for c in candidates:
    if (c / 'dlls/kernelbase/process.c').is_file():
        wine = c
        break


def extract(src, name):
    """The text of a top-level function (and the comment block right above it) from its first line to the closing brace at column 0."""
    m = re.search(r'^(?:static )?(?:BOOL|DWORD)\s+(?:WINAPI\s+)?(?:/\*[^*]*\*/\s+)?%s\s*\(.*?^}\n' % re.escape(name), src, re.S | re.M)
    return m.group(0) if m else ''


HARNESS = r'''
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <wchar.h>
typedef int BOOL;
typedef unsigned int DWORD;
typedef long LONG;
typedef unsigned short WCHAR;      /* the harness compiles with -fshort-wchar: L"..." is 16 bit, as in the PE */
typedef void *HANDLE;
typedef unsigned long SIZE_T;
typedef int PROCESS_MITIGATION_POLICY;
#define WINAPI
#define TRUE 1
#define FALSE 0
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define FIXME(...) do { } while (0)

static DWORD last_error = 5;
static const char *env_value;       /* what MADEIRA_ZERO_MITIGATION_POLICY is in this run; NULL: unset */
static DWORD GetLastError(void) { return last_error; }
static void SetLastError(DWORD e) { last_error = e; }
static DWORD GetEnvironmentVariableW(const WCHAR *name, WCHAR *buf, DWORD size)
{
    static const WCHAR want[] = L"MADEIRA_ZERO_MITIGATION_POLICY";
    size_t i, n;
    for (i = 0; want[i]; i++) if (name[i] != want[i]) { last_error = 203; return 0; }
    if (name[i]) { last_error = 203; return 0; }
    if (!env_value) { last_error = 203; return 0; }          /* ERROR_ENVVAR_NOT_FOUND, as the real call sets it */
    n = strlen(env_value);
    if (n + 1 > size) return (DWORD)(n + 1);
    for (i = 0; i < n; i++) buf[i] = (unsigned char)env_value[i];
    buf[n] = 0;
    return (DWORD)n;
}

//SOURCE//

int main(int argc, char **argv)
{
    unsigned char buf[40];
    size_t length = (size_t)atoi(argv[2]);
    int null_buffer = argc > 3 && !strcmp(argv[3], "null");
    int r, i, touched = 0, zeroed_to = 0;

    env_value = strcmp(argv[1], "UNSET") ? (!strcmp(argv[1], "EMPTY") ? "" : argv[1]) : NULL;
    memset(buf, 0xA5, sizeof(buf));
    last_error = 5;
    r = GetProcessMitigationPolicy((HANDLE)-1, 7, null_buffer ? NULL : buf, length);
    for (i = 0; i < (int)sizeof(buf); i++) { if (buf[i] != 0xA5) touched++; if (buf[i] == 0 && zeroed_to == i) zeroed_to = i + 1; }
    printf("ret=%d touched=%d zeroed_prefix=%d last_error=%u\n", r, touched, zeroed_to, last_error);
    return 0;
}
'''

if wine is None:
    print('skip the Wine source is not at hand (set BCD_WINE_SRC to a checkout of the Wine fork)')
else:
    head = sh('git', 'rev-parse', 'HEAD', cwd=wine)
    src = (wine / 'dlls/kernelbase/process.c').read_text()
    stub = extract(src, 'GetProcessMitigationPolicy')
    if pinned and head != pinned:
        print('skip the Wine tree %s is at %s, this checkout pins %s' % (wine, head[:12] or '?', pinned[:12]))
        check('Wine (older tree): the stub answers TRUE and writes nothing (the premise)',
              'GetProcessMitigationPolicy' in src and 'return TRUE;' in stub and 'memset' not in stub)
    else:
        helper = extract(src, 'zero_mitigation_policy_enabled')
        check('Wine: GetProcessMitigationPolicy and its helper are there', bool(stub) and bool(helper))
        check('Wine: the helper reads exactly the one variable, and the value 1 only',
              src.count('MADEIRA_ZERO_MITIGATION_POLICY') >= 2 and 'L"MADEIRA_ZERO_MITIGATION_POLICY"' in helper
              and "len == 1 && value[0] == '1'" in helper)
        check('Wine: the stub still logs its FIXME and answers TRUE', 'FIXME(' in stub.replace(' ', '') or 'FIXME (' in stub)
        check('Wine: the zero-fill is behind the helper and a 1..16 byte buffer',
              'buffer && length && length <= 16 && zero_mitigation_policy_enabled()' in stub and 'memset( buffer, 0, length )' in stub)
        check('Wine: nothing else in kernelbase names the variable', sum(
            p.read_text(errors='ignore').count('MADEIRA_ZERO_MITIGATION_POLICY')
            for p in (wine / 'dlls/kernelbase').glob('*.c') if p.name != 'process.c') == 0)
        cc = shutil.which('cc') or shutil.which('clang') or shutil.which('gcc')
        if not cc or not helper or not stub:
            check('a C compiler is at hand for the harness', bool(cc))
        else:
            with tempfile.TemporaryDirectory() as d:
                c = Path(d) / 'h.c'
                exe = Path(d) / 'h'
                text = HARNESS.replace('//SOURCE//', helper + '\n' + stub)
                c.write_text(text)
                r = subprocess.run([cc, '-O1', '-fshort-wchar', '-Wno-unused-function', '-Wno-unused-parameter', '-o', str(exe), str(c)],
                                   capture_output=True, text=True)
                check('the extracted functions compile on the host', r.returncode == 0, r.stderr[-1500:])
                if r.returncode == 0:
                    def run(value, length, *extra):
                        # a fresh process per case: the helper caches the first answer
                        out = subprocess.run([str(exe), value, str(length)] + list(extra), capture_output=True, text=True).stdout.strip()
                        return dict(kv.split('=') for kv in out.split()) if out else {}
                    for value in ('UNSET', '0', 'EMPTY', '11', '2', 'yes', 'true', '01', ' 1', '1 '):
                        res = run(value, 4)
                        check('off (%s): the buffer is not touched and the answer is TRUE' % value,
                              res.get('ret') == '1' and res.get('touched') == '0', str(res))
                    for length in (1, 4, 8, 16):
                        res = run('1', length)
                        check('on (1): a %d-byte buffer is zeroed, nothing after it, the answer is TRUE' % length,
                              res.get('ret') == '1' and res.get('touched') == str(length) and res.get('zeroed_prefix') == str(length), str(res))
                    for length in (0, 17, 40):
                        res = run('1', length)
                        check('on (1): a length of %d leaves the buffer alone' % length, res.get('ret') == '1' and res.get('touched') == '0', str(res))
                    res = run('1', 4, 'null')
                    check('on (1): a NULL buffer is not written (and does not crash)', res.get('ret') == '1', str(res))
                    res = run('1', 4)
                    check('on (1): the last error is the caller\'s own (5), the variable lookup does not leave ERROR_ENVVAR_NOT_FOUND',
                          res.get('last_error') == '5', str(res))
                    res = run('UNSET', 4)
                    check('off (unset): the last error is the caller\'s own (5)', res.get('last_error') == '5', str(res))
        print('  (Wine tree %s at %s)' % (wine, head[:12]))

# ---------------------------------------------------------------- 2. the list
recs = json.loads((root / 'recommended/recommendations.json').read_text())
carrying = [r['id'] for r in recs['rules'] if NAME in json.dumps(r.get('config', []))]
check('only glyphica-typing-survival carries the line', carrying == ['glyphica-typing-survival'], str(carrying))
rule = next((r for r in recs['rules'] if r['id'] == 'glyphica-typing-survival'), None)
# Round 624: the public build 600 does not know the key and refused the whole online list because of the line (checked with its own code,
# ~/madeira-local/check-list-old-builds.py), so the line is from build 601 on (version 2); the rule itself stays new in build 600.
# Round 624 (agent E2 S3): version 3 adds the fork's kernelbase (kernelbase-optin.dll, build 624), the only kernelbase that reads the key.
check('the rule is new in build 600, its line carries minBuild 601, the fork\'s kernelbase minBuild 624 (version 3)',
      rule is not None and rule.get('minBuild') == 600 and {'line': LINE, 'minBuild': 601} in rule['config']
      and {'line': 'env.MADEIRA_OPTIN_DLLS = kernelbase', 'minBuild': 624} in rule['config'] and rule['version'] == 3,
      str(rule and rule['config']))
check('it matches the game by App ID, folder and the program beside ChakraCore.dll (any install source)',
      rule is not None and rule['match'] == {'steamAppIDs': [2400160], 'steamFolders': ['glyphica'],
                                             'programs': [{'name': 'neosphere.exe', 'beside': 'ChakraCore.dll'}]}, str(rule and rule['match']))
check('its other lines are the standard comments and the standard EC-hook line',
      rule is not None and rule['config'][:3] == ["# BCD's recommended settings for Glyphica: Typing Survival.",
                                                  '# Reset to Recommended, at the bottom of the game\'s settings, brings them back.',
                                                  'env.MADEIRA_EC_HOOK_TRACE = 0'] and len(rule['config']) == 5)
standard = next(r for r in recs['rules'] if r['id'] == 'standard')
check('the standard list does not have it', NAME not in json.dumps(standard))
# Round 620: thirteen lists of that round's logs follow Elliot.
# Round 602 (ajan41): seven lists (Direct3D 8 and 64-bit Direct3D 9 games) were added between this rule and "standard".
# The Adventures of Elliot (upstream port, build 602) stands last before "standard".
_r620 = ['forza-horizon-4', 'tiny-tinas-wonderlands', 'far-cry-3', 'barotrauma', 'slay-the-spire-2', 'resident-evil-3', 'marvel-cosmic-invasion',
         'haste', 'big-walk', 'ball-x-pit', 'just-volleyball-demo', 'farming-simulator-22-epic', 'max-payne-3',
         'f1-2020', 'need-for-speed-the-run',   # round 620's new lists (the last two after the Discord reports)
         'bloons-td-6']                         # round 623 (Bloons TD 6, ajan58)
_r624 = ['control', 'no-more-room-in-hell', 'red-faction', 'from-the-darkness', 'metal-gear-solid-4-master-collection', 'mycopunk', 'arctic-awakening', 'supermarket-together', 'robocop-rogue-city', 'blasphemous', 'nwjs-game-exe', 'kerbal-space-program', 'mafia-2', 'fallout-3', 'overlord-ii', 'rise-of-the-tomb-raider', 'lucieneslife', 'ubisoft-connect', 'unreal-gold', 'repo', 'osu', 'outlast', 'satisfactory', 'the-witcher-3', 'yakuza-6-the-song-of-life', 'euro-truck-simulator-2', 'gal-gun-2', 'hello-neighbor',
         'borderlands-goty-enhanced', 'dead-space-2023', 'schedule-i', 'dokimon-quest', 'sonic-adventure-dx', 'requiem-avenging-angel', 'friends-vs-friends', 'fallout-4-other', 'skyrim-special-edition-other', 'wrc-9', 'doki-doki-literature-club',
         'outlast-2', 'batman-arkham-knight', 'dead-rising', 'reanimal', 'pragmata', 'what-lies-in-the-multiverse', 'muv-luv-photonflowers']   # round 625 (owner's items 10, 12, 13, 14); round 624 (the ten analysis reports of tur624; Borderlands GOTY Enhanced, agent A S7; Dead Space (2023), Discord)
_ids = [r['id'] for r in recs['rules']]
check('the rule stands before "standard" (the last rule), after it only the round-602 lists, The Adventures of Elliot and the round-620/623 lists',
      _ids[_ids.index('glyphica-typing-survival'):] == ['glyphica-typing-survival', 'grand-theft-auto-iii', 'postal-2', 'hotline-miami', 'max-payne-2',
                                                      'pico-park', 'dont-starve-together', 'a-hat-in-time', 'the-adventures-of-elliot'] + _r620 + _r624 + ['standard'])
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the list policy knows the name', pol.count('"%s"' % NAME) == 2)
case = re.search(r'case "MADEIRA_WINTYPES"[^\n]*"%s":(.*?)guard value == "0" \|\| value == "1"' % NAME, pol, re.S)
check('the list policy takes 0 or 1 for it', case is not None)

# ---------------------------------------------------------------- 3. auto-tune
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rr = [x for x in mod.RULES if x['id'] == RULE]
check('auto-tune has the rule once', len(rr) == 1)
if rr:
    x = rr[0]
    check('it adds the line and the fork\'s kernelbase that reads it (round 624), once (noRetry), only up to stage 2, after a crash',
          x['lines'] == [LINE, 'env.MADEIRA_OPTIN_DLLS = kernelbase'] and x.get('noRetry') is True and x.get('maxStage') == 2
          and x.get('outcomes') == ['crashed'])
    sigs = json.dumps(x['all'])
    check('it needs the unhandled c0000602 and a ChakraCore frame of the unwinder', 'Unhandled exception code c0000602' in sigs
          and 'ChakraCore.dll+0x' in sigs and '[unwind] term' in sigs)
    check('it does not depend on the fixme line (a quiet log may drop it)', 'GetProcessMitigationPolicy' not in sigs)
    check('it stops matching once the fork\'s kernelbase is in force (the bridge\'s own line; round 624: the key alone never ran)',
          '[WineProc] MADEIRA_OPTIN_DLLS: kernelbase.dll -> kernelbase-optin.dll' in json.dumps(x['none']))
    check('its evidence names this check and the fixture', any('check-zero-mitigation-policy' in e for e in x['evidence'])
          and any('glyphica-chakracore-failfast' in e for e in x['evidence']))
others = [x['id'] for x in mod.RULES if NAME in json.dumps(x.get('lines', []))]
check('no other auto-tune rule adds the line', others == [RULE], str(others))
fixtures = root / 'tests/fixtures/autotune'
matching = [p.name for p in sorted(fixtures.glob('*.expect')) if RULE in json.loads(p.read_text()).get('matched', [])]
# round 624 (agent E2 S3): J9JDBG (build 623) failed the same way with the key set; the rule now also gives the fork's kernelbase
KB = 'env.MADEIRA_OPTIN_DLLS = kernelbase'
check('exactly the Glyphica fixtures match the rule (build 592, and build 623 with the key set)',
      matching == ['glyphica-chakracore-failfast-2026-10-24.expect', 'glyphica-chakracore-failfast-key-set-j9jdbg-2026-10-29.expect'], str(matching))
for _f in matching:
    exp = json.loads((fixtures / _f).read_text())
    check('%s is a crash at stage 2 that proposes the line and the fork\'s kernelbase' % _f,
          exp['outcome'] == 'crashed' and exp['stage'] == 2 and exp['next'] == [LINE, KB])

# ---------------------------------------------------------------- 4. nothing else sets it; the catalog and the CI know it
users = []
for sub in ('app', 'build', 'tools', 'madeira-d3d12', 'madeira-dock'):
    base = root / sub
    if not base.is_dir():
        continue
    for p in base.rglob('*'):
        if p.is_file() and p.suffix in ('.swift', '.m', '.mm', '.c', '.h', '.cpp', '.py', '.sh', '.json') and NAME in p.read_text(errors='ignore'):
            users.append(str(p.relative_to(root)))
allowed = {'app/Madeira/GameRecommendationsList.swift', 'app/Madeira/ConfigCatalog.generated.swift', 'app/Madeira/AutoTuneRules.generated.swift',
           'build/tools/autotune_rules.py', 'build/tools/gen-config-catalog.py',
           # round 624 (agent E2 S3): the build of kernelbase-optin.dll and its check name the switch they ship
           'tools/build-wine-extra-dlls.sh', 'tools/verify-kernelbase-optin.py'}
check('only the policy, the catalog and auto-tune name it in the app and build sources', set(users) <= allowed, str(sorted(set(users) - allowed)))
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the settings catalog knows the key, off by default', re.search(r'key: "env\.%s".*kind: \.bool, defaultValue: "0"' % NAME, cat) is not None)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('the CI runs this check', 'tests/host/check-zero-mitigation-policy.py' in wf)

print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
