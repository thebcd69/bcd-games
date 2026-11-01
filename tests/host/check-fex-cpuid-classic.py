#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX CPUID opt-in: leaf 1 reports DE and PSE (round 626, agent G1 S7; Burnout Paradise AQY7NM, 7MSNGW).

FEX's Function_01h reports CPUID.1:EDX bits 2 (DE) and 3 (PSE) as 0, which no real x86 processor does; Burnout Paradise tests
bit 2 and stops with its own "CPU Error" box. tools/patch-fex-ios-cpuid-classic.py reports both as 1 when the process has
MADEIRA_FEX_CPUID_CLASSIC=1 (exactly "1"), and changes nothing otherwise.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned
CPUID.cpp, a second run says so and changes nothing, only the helper is added and only the two EDX lines of Function_01h change;
the helper and the EDX expression, cut from the patched and the pinned file and compiled on the host, give the pinned value for the
key unset, "", "0", "yes", "11", "1 " and the pinned value | 0xC for "1" (read once, one log line); both FEX builds apply it
(non-fatally) and restore CPUID.cpp; the list policy, the catalog, Burnout Paradise's row, auto-tune's rule and its fixture; CI.
"""
from pathlib import Path
import difflib, json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


patch = root / 'tools/patch-fex-ios-cpuid-classic.py'
fex = Path(os.environ.get('FEX_SRC', Path.home() / 'madeira-bcd/FEX'))
cpuid = fex / 'FEXCore/Source/Interface/Core/CPUID.cpp'
HEAD = 'FEXCore::CPUID::FunctionResults CPUIDEmu::Function_01h(uint32_t Leaf) const {'


def edx_of(text):
    f = text[text.index(HEAD):]
    f = f[:f.index('\n}\n')]
    e = f[f.index('  Res.edx = '):]
    return e[:e.index(';') + 1]


cxx = shutil.which('c++') or shutil.which('clang++')
if cpuid.is_file():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp) / 'CPUID.cpp'
        shutil.copy(cpuid, t)
        before = t.read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        once = t.read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        check('it applies to the pinned CPUID.cpp; a second run says so and changes nothing',
              r1.returncode == 0 and r2.returncode == 0 and 'already in' in r2.stdout and t.read_text() == once, r1.stdout + r1.stderr + r2.stdout)
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != 'equal']
        changed = [(o[0], a[o[1]:o[2]], b[o[3]:o[4]]) for o in ops]
        check('only the helper is inserted (before Function_01h) and only the DE and PSE lines change',
              len(ops) == 2 and ops[0][0] == 'insert' and b[ops[0][4]] == HEAD and ops[1][0] == 'replace'
              and [l.strip() for l in changed[1][1]] == ['(0 << 2) |  // Debugging extensions', '(0 << 3) |  // Page size extension']
              and all(l.strip().startswith(('(MadeiraCpuidClassic() << 2) |', '(MadeiraCpuidClassic() << 3) |')) for l in changed[1][2]), changed)
        helper = once[once.index('static uint32_t MadeiraCpuidClassic() {'):]
        helper = helper[:helper.index('\n}\n') + 3]
        check('the helper reads the key once and accepts exactly "1"', 'static const uint32_t On = [] {' in helper
              and "(Env && Env[0] == '1' && Env[1] == '\\0') ? 1u : 0u" in helper and helper.count('getenv(') == 1)
        harness = r'''
#include <cstdint>
#include <cstdio>
#include <cstdlib>
static int g_logs;
namespace LogMan { namespace Msg { template <typename... A> void IFmt(const char*, A...) { g_logs++; } } }
''' + helper + r'''
struct R { uint32_t edx; };
static uint32_t old_edx() { R Res {}; ''' + edx_of(before) + r''' return Res.edx; }
static uint32_t new_edx() { R Res {}; ''' + edx_of(once) + r''' return Res.edx; }
int main() {
  uint32_t a = new_edx(), b = new_edx();
  printf("%08x %08x %08x %d\n", old_edx(), a, b, g_logs);
  return 0;
}
'''
        if cxx:
            src = Path(tmp) / 't.cpp'
            src.write_text(harness)
            exe = Path(tmp) / 't'
            r = subprocess.run([cxx, '-std=c++20', '-O1', '-Wall', '-Werror', '-o', str(exe), str(src)], capture_output=True, text=True)
            check('the helper and both EDX expressions compile on the host', r.returncode == 0, r.stderr[-2000:])
            if r.returncode == 0:
                env0 = {k: v for k, v in os.environ.items() if k != 'MADEIRA_FEX_CPUID_CLASSIC'}
                for value in (None, '', '0', 'yes', '11', '1 ', '1'):
                    env = dict(env0)
                    if value is not None:
                        env['MADEIRA_FEX_CPUID_CLASSIC'] = value
                    out = subprocess.run([str(exe)], capture_output=True, text=True, env=env).stdout.split()
                    old, n1, n2, logs = int(out[0], 16), int(out[1], 16), int(out[2], 16), int(out[3])
                    if value == '1':
                        check('key "1": EDX is the pinned value with DE and PSE set (0x%08x -> 0x%08x), one log line' % (old, n1),
                              n1 == n2 == old | 0xC and old & 0xC == 0 and logs == 1, out)
                    else:
                        check('key %s: EDX bit for bit the pinned value (0x%08x), no log line' % ('unset' if value is None else repr(value), old),
                              n1 == n2 == old and logs == 0, out)
        else:
            print('skip the host run: no c++ compiler')
else:
    print(f'SKIP the patch run: no FEX tree at {fex} (set FEX_SRC)')

wow = (root / 'tools/build-xtajit-wow64.sh').read_text()
ec = (root / 'tools/build-xtajit64.sh').read_text()
check('the 32-bit module applies it (a drifted anchor only skips it) and restores CPUID.cpp',
      'python3 "$R/tools/patch-fex-ios-cpuid-classic.py" "$R/FEX/$CPUIDF" || echo "::warning::' in wow
      and re.search(r'restore\(\) \{ git -C "\$R/FEX" checkout -- "\$CPUIDF"', wow) is not None
      and wow.index('patch-fex-ios-cpuid-classic.py') < wow.index('\ncmake -S'))
fi = ec.index('python3 "$R/tools/patch-fex-ios-mono-bp-unlocked-miss.py"')
line = 'python3 "$R/tools/patch-fex-ios-cpuid-classic.py" "$R/FEX/FEXCore/Source/Interface/Core/CPUID.cpp" || echo "::warning::'
restore = next((l for l in ec.splitlines() if l.startswith('git -C FEX checkout -- "$CPUF"')), '')
check('the ARM64EC module applies it to every pin, before the build, and restores CPUID.cpp',
      ec.count(line) == 1 and ec.index(line) > fi and '\nbuild\n' in ec[ec.index(line):]
      and 'FEXCore/Source/Interface/Core/CPUID.cpp' in restore and ec.index(restore) > ec.index(line))
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a list may set the key, 0 or 1', '"MADEIRA_FEX_CPUID_CLASSIC"' in pol and re.search(r'case [^\n]*"MADEIRA_FEX_CPUID_CLASSIC"', pol) is not None)
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents it, off by default',
      re.search(r'key: "env\.MADEIRA_FEX_CPUID_CLASSIC"[^\n]*kind: \.bool, defaultValue: "0"', cat) is not None)
recs = json.loads((root / 'recommended/recommendations.json').read_text())
bp = next((r for r in recs['rules'] if r['id'] == 'burnout-paradise'), None)
carriers = sorted(r['id'] for r in recs['rules'] if any('MADEIRA_FEX_CPUID_CLASSIC' in (c['line'] if isinstance(c, dict) else c) for c in r['config']))
check('only Burnout Paradise\'s row sets it, from build 626, for any copy of the game',
      carriers == ['burnout-paradise'] and bp is not None and {'line': 'env.MADEIRA_FEX_CPUID_CLASSIC = 1', 'minBuild': 626} in bp['config']
      and {'name': 'burnoutparadise.exe'} in bp['match']['programs'], carriers)
rules = (root / 'build/tools/autotune_rules.py').read_text()
r = rules[rules.index('"id": "fex-cpuid-classic-sse2-box"'):] if '"id": "fex-cpuid-classic-sse2-box"' in rules else ''
r = r[:r.index('\n    },')] if r else ''
check('auto-tune adds the line after the game\'s CPU Error box', '"lines": ["env.MADEIRA_FEX_CPUID_CLASSIC = 1"]' in r
      and 'This machine does not support the SSE2 Command Set' in r and '[fex-cpuid] MADEIRA_FEX_CPUID_CLASSIC=1' in r, r[:400])
exp = root / 'tests/fixtures/autotune/burnout-paradise-cpu-error-2026-10-30.expect'
check('the fixture is there and expects the line', exp.is_file() and json.loads(exp.read_text()).get('next') == ['env.MADEIRA_FEX_CPUID_CLASSIC = 1'])
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-fex-cpuid-classic.py' in wf)
print('PASS: FEX CPUID classic bits' if ok else 'FAILED: FEX CPUID classic bits')
sys.exit(0 if ok else 1)
