#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX's alias table: a bigger one for the game that asks, the same 256 for every other (round 624, agent A S7; Borderlands V0FQAS).

Borderlands: Game of the Year Enhanced died 60 s in: a thread called the freshly loaded cryptnet.dll through its JIT-pool copy, and
FEX's alias table (Source/Windows/ARM64EC/IosJitAlias.cpp) had no entry for it ("[iOS-xquery] MISS", "NoExec instruction in entry
block"). The game's emulator received 120 aliases at registration and 184 image copies after; the table holds 256 and refused the
rest silently. tools/patch-fex-ios-alias-capacity.py gives the array 1024 entries but keeps the limit in force at 256 unless
env.MADEIRA_FEX_ALIAS_MAX names more (ProcessInit), and puts the table's fill and refusals into the miss line.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX): the patch applies to a copy and is idempotent; the patched IosJitAlias.cpp,
compiled on the host with stub headers, refuses the 257th distinct image by default (counted, untranslatable) and takes it with the
limit at 1024, clamps the limit to 256..1024, and translates exactly as before below the limit; the ProcessInit and miss-line edits;
tools/build-xtajit64.sh applies it to every pin and restores both files; the list policy, catalog, list row and auto-tune rule.
"""
from pathlib import Path
import json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


fex = Path(os.environ.get('FEX_SRC', Path.home() / 'madeira-bcd/FEX'))
alias_src = fex / 'Source/Windows/ARM64EC/IosJitAlias.cpp'
module_src = fex / 'Source/Windows/ARM64EC/Module.cpp'
patch = root / 'tools/patch-fex-ios-alias-capacity.py'

DRIVER = r'''
#include <cstdio>
#include <cstdint>
extern "C" {
void BTCpu64IosAddAliasMapping(uint64_t PeBase, uint64_t JitBase, uint64_t Size);
uint64_t IosJitTranslate(uint64_t Addr);
uint64_t IosJitReverseTranslate(uint64_t Addr);
void IosAliasSetCapacity(int Cap);
void IosAliasStats(int* Count, int* Cap, uint64_t* Refused);
}
static const uint64_t PE = 0x7100000000ull, JIT = 0x140000000ull, SZ = 0x10000ull;
static void push(int i) { BTCpu64IosAddAliasMapping(PE + i * 0x100000ull, JIT + i * 0x100000ull, SZ); }
static void stats(const char* tag) {
  int c = 0, cap = 0; uint64_t r = 0;
  IosAliasStats(&c, &cap, &r);
  printf("%s %d %d %llu\n", tag, c, cap, (unsigned long long)r);
}
static int rev_ok(int i) { uint64_t a = JIT + i * 0x100000ull + 0x1234; return IosJitReverseTranslate(a) == PE + i * 0x100000ull + 0x1234; }
int main() {
  stats("start");
  for (int i = 0; i < 300; i++) push(i);
  stats("default300");
  printf("rev %d %d %d\n", rev_ok(0), rev_ok(255), rev_ok(256));
  printf("fwd %d\n", IosJitTranslate(PE + 10 * 0x100000ull + 8) == JIT + 10 * 0x100000ull + 8);
  IosAliasSetCapacity(5);
  stats("clamp-low");
  IosAliasSetCapacity(100000);
  stats("clamp-high");
  IosAliasSetCapacity(1024);
  for (int i = 300; i < 344; i++) push(i);
  stats("cap1024");
  printf("rev2 %d %d %d\n", rev_ok(300), rev_ok(343), rev_ok(256));
  for (int i = 0; i < 256; i++) push(i);   /* identical re-registration of copies the table holds: nothing changes */
  stats("again");
  for (int i = 256; i < 300; i++) push(i);   /* the 44 refused before are taken now */
  stats("refused-now-taken");
  printf("rev3 %d\n", rev_ok(256));
  return 0;
}
'''

STUBS = {
    'windows.h': '#pragma once\n#include <cstdint>\n',
    'winternl.h': '#pragma once\nstruct BcdStubTeb { void* ProcessEnvironmentBlock; };\n'
                  'inline BcdStubTeb* NtCurrentTeb() { static BcdStubTeb t{(void*)0x10000}; return &t; }\n',
    'FEXCore/Utils/LogManager.h': '#pragma once\nnamespace LogMan { namespace Msg { template <typename... A> void EFmt(A&&...) {} '
                                  'template <typename... A> void IFmt(A&&...) {} } }\n',
}
LINKSTUBS = r'''
#include <cstdint>
extern "C" {   /* IosJitAlias.cpp declares them inside its extern "C" block */
uint64_t ios_fex_rip_from_hostpc(uint64_t, uint64_t) { return 0; }
void ios_fex_mono_bridge_publish(void*) {}
void ios_fex_mono_report_armed(uint64_t, uint64_t) {}
}
'''

if alias_src.exists() and module_src.exists() and shutil.which('c++'):
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        dst = t / 'FEX/Source/Windows/ARM64EC'
        dst.mkdir(parents=True)
        shutil.copy(alias_src, dst / 'IosJitAlias.cpp')
        shutil.copy(module_src, dst / 'Module.cpp')
        for extra in ('IosMonoBridge.h',):
            if (alias_src.parent / extra).exists():
                shutil.copy(alias_src.parent / extra, dst / extra)
        r1 = subprocess.run([sys.executable, str(patch), str(t / 'FEX')], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(patch), str(t / 'FEX')], capture_output=True, text=True)
        check('the patch applies to the pinned FEX and a second run changes nothing', r1.returncode == 0 and 'patched' in r1.stdout
              and r2.returncode == 0 and 'already patched' in r2.stdout, (r1.stdout + r1.stderr + r2.stdout + r2.stderr)[-600:])
        a = (dst / 'IosJitAlias.cpp').read_text()
        m = (dst / 'Module.cpp').read_text()
        check('the array has 1024 places, the limit in force is 256, refusals are counted where the stats read them',
              'constexpr int kMaxEntries = 1024;' in a and 'static int g_AliasCap = 256;' in a and 'if (count >= g_AliasCap) {' in a
              and '__atomic_fetch_add(&g_AliasRefused, 1, __ATOMIC_RELAXED);' in a and 'static uint64_t refused = 0;' not in a
              and a.count('LogMan::') == alias_src.read_text().count('LogMan::'))
        pi = m[m.index('NTSTATUS ProcessInit() {'):]
        check('ProcessInit reads env.MADEIRA_FEX_ALIAS_MAX after logging is up and says the limit only when the key is set',
              'const char* CapOpt = getenv("MADEIRA_FEX_ALIAS_MAX");' in pi and pi.index('FEX::Windows::Logging::Init();') < pi.index('MADEIRA_FEX_ALIAS_MAX')
              and 'if (Cap > 0) {' in pi and '"[iOS-alias] madeira-bcd alias table limit {} (MADEIRA_FEX_ALIAS_MAX={}) exe={}"' in pi)
        check('the miss line says how full the table is and how many copies it refused',
              '"[iOS-xquery] MISS tracker={} addr={:#x} rev={:#x} alias-table={}/{} refused={}"' in m)
        stub = t / 'stub'
        for name, text in STUBS.items():
            (stub / name).parent.mkdir(parents=True, exist_ok=True)
            (stub / name).write_text(text)
        (t / 'driver.cpp').write_text(DRIVER)
        (t / 'linkstubs.cpp').write_text(LINKSTUBS)
        b = subprocess.run(['c++', '-std=c++20', '-DFEX_IOS_HOST=1', '-w', '-I', str(stub), '-I', str(dst), '-o', str(t / 'alias'),
                            str(dst / 'IosJitAlias.cpp'), str(t / 'driver.cpp'), str(t / 'linkstubs.cpp')], capture_output=True, text=True)
        check('the patched IosJitAlias.cpp compiles on the host (stub Windows and LogMan headers)', b.returncode == 0, b.stderr[-1500:])
        if b.returncode == 0:
            out = dict((l.split(' ', 1) + [''])[:2] for l in subprocess.run([str(t / 'alias')], capture_output=True, text=True).stdout.splitlines())
            check('by default the table takes 256 copies and refuses (and counts) the 44 others', out.get('default300') == '256 256 44', out)
            check('...the 256th translates, the 257th does not (the V0FQAS miss), and translation below the limit is unchanged',
                  out.get('rev') == '1 1 0' and out.get('fwd') == '1', out)
            check('the limit is clamped to 256..1024', out.get('clamp-low') == '256 256 44' and out.get('clamp-high') == '256 1024 44', out)
            check('with the limit at 1024 new copies are taken and translate', out.get('cap1024') == '300 1024 44' and out.get('rev2') == '1 1 0', out)
            check('an identical re-registration of held copies changes nothing; copies refused before are taken once pushed again',
                  out.get('again') == '300 1024 44' and out.get('refused-now-taken') == '344 1024 44' and out.get('rev3') == '1', out)
else:
    print('skip the FEX part: no FEX tree (set FEX_SRC) or no c++')

sh = (root / 'tools/build-xtajit64.sh').read_text()
fi = sh.index('python3 "$R/tools/patch-fex-ios-mono-bp-unlocked-miss.py"')
check('tools/build-xtajit64.sh applies it to every pin (after the pin branches), before the build, and puts both files back',
      sh.count('python3 "$R/tools/patch-fex-ios-alias-capacity.py" "$R/FEX"') == 1 and sh.index('patch-fex-ios-alias-capacity.py" "$R/FEX"') > fi
      and sh[sh.index('patch-fex-ios-alias-capacity.py" "$R/FEX"'):].split('\n')[1] == 'build'
      and sh[sh.index('patch-fex-ios-alias-capacity.py" "$R/FEX"'):].split('\n')[2].startswith('git -C FEX checkout -- ')
      and 'Source/Windows/ARM64EC/Module.cpp' in sh[sh.index('patch-fex-ios-alias-capacity.py" "$R/FEX"'):].split('\n')[2]
      and 'Source/Windows/ARM64EC/IosJitAlias.cpp' in sh[sh.index('patch-fex-ios-alias-capacity.py" "$R/FEX"'):].split('\n')[2])
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a list may set the key, a whole number from 256 to 1024', '"MADEIRA_FEX_ALIAS_MAX",' in pol and 'case "MADEIRA_FEX_ALIAS_MAX":' in pol
      and 'guard let n = Int(value), (256...1024).contains(n), String(n) == value else {' in pol)
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents it (default 256)', re.search(r'key: "env\.MADEIRA_FEX_ALIAS_MAX"[^\n]*kind: \.int, defaultValue: "256"', cat) is not None)
recs = json.loads((root / 'recommended/recommendations.json').read_text())
bl = next((r for r in recs['rules'] if r['id'] == 'borderlands-goty-enhanced'), None)
check('Borderlands GOTY Enhanced\'s list: the bigger table from build 624, the pure-x64 images without a pool copy',
      bl is not None and {'line': 'env.MADEIRA_FEX_ALIAS_MAX = 1024', 'minBuild': 624} in bl['config']
      and {'line': 'env.MADEIRA_X64_IMAGE_NOCOPY = 1', 'minBuild': 576} in bl['config'] and bl['match']['steamAppIDs'] == [729040])
rules = (root / 'build/tools/autotune_rules.py').read_text()
r = rules[rules.index('"id": "pool-copy-rip-fex-table-miss"'):]
r = r[:r.index('\n    },\n')]
check('auto-tune gives the line after the miss and stops once the bigger table is in force',
      '"lines": ["env.MADEIRA_FEX_ALIAS_MAX = 1024"]' in r and 's("[iOS-alias] madeira-bcd alias table limit 1024")' in r and '"noFix"' not in r)
exp = json.loads((root / 'tests/fixtures/autotune/borderlands-goty-cryptnet-pool-rip-2026-10-29.expect').read_text())
check('V0FQAS\'s fixture: trying the line', exp['status'] == 'trying' and exp['next'] == ['env.MADEIRA_FEX_ALIAS_MAX = 1024'], exp)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-fex-alias-capacity.py' in wf)
print('PASS: FEX alias capacity' if ok else 'FAIL: FEX alias capacity')
raise SystemExit(0 if ok else 1)
