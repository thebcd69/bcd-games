#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""FEX ARM64EC: no fault on a torn-down context (round 626, agent C S4).

Builds 624-625 logged `[fex-fault] pc=libarm64ecfex.dll+0x1123c4 ... fault-addr=0x0` about 50 times while a process ended
(Clair Obscur: Expedition 33's crash reporter child in HWMBZ2 and MKX1Z2, DQQR62, CDPEFN, 1FJAWW): ResetToConsistentStateImpl read
the file-scope `CTX` after it was reset. tools/patch-fex-ios-ec-teardown-ctx.py returns false there ("not FEX's", Wine dispatches
the exception) when `CTX` is null; with a context nothing changes.

Checks (FEX tree: FEX_SRC, default ~/madeira-bcd/FEX; that part is skipped without it): the patch applies to a copy of the pinned
Module.cpp, a second run says so and changes nothing; it adds exactly the three guard lines, right before the IsJIT line, inside
ResetToConsistentStateImpl, and nothing else; the pin's other Module.cpp patches (launcher-smc, alias-capacity) still apply around
it in build order. tools/build-xtajit64.sh applies it to every pin before the build and restores Module.cpp after. auto-tune's
fex-runtime-fault leaves the 624-625 teardown fault to the session's own verdict; CI runs this check.
"""
from pathlib import Path
import difflib, os, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)[:1500]) if (detail and not cond) else ''))
    ok = ok and bool(cond)


patch = root / 'tools/patch-fex-ios-ec-teardown-ctx.py'
check('the patch script exists', patch.is_file())
fex = Path(os.environ.get('FEX_SRC', Path.home() / 'madeira-bcd/FEX'))
module = fex / 'Source/Windows/ARM64EC/Module.cpp'
ANCHOR = '  bool IsJIT = CTX->IsAddressInCodeBuffer(Thread, NativeContext->Pc);'
if module.is_file():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'Source/Windows/ARM64EC').mkdir(parents=True)
        shutil.copy(module, t / 'Source/Windows/ARM64EC/Module.cpp')
        alias = fex / 'Source/Windows/ARM64EC/IosJitAlias.cpp'
        if alias.is_file():
            shutil.copy(alias, t / 'Source/Windows/ARM64EC/IosJitAlias.cpp')
        before = (t / 'Source/Windows/ARM64EC/Module.cpp').read_text()
        r1 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        once = (t / 'Source/Windows/ARM64EC/Module.cpp').read_text()
        r2 = subprocess.run([sys.executable, str(patch), str(t)], capture_output=True, text=True)
        twice = (t / 'Source/Windows/ARM64EC/Module.cpp').read_text()
        check('it applies to the pinned Module.cpp; a second run says so and changes nothing',
              r1.returncode == 0 and r2.returncode == 0 and 'already in' in r2.stdout and once == twice, r1.stdout + r1.stderr + r2.stdout)
        a, b = before.splitlines(), once.splitlines()
        ops = [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != 'equal']
        check('exactly three lines are added (the guard), nothing else changes',
              len(ops) == 1 and ops[0][0] == 'insert' and ops[0][4] - ops[0][3] == 3, ops)
        i = b.index(ANCHOR) if ANCHOR in b else -1
        check('the guard sits right before the IsJIT line and returns false when the context is null',
              i >= 3 and b[i - 3].startswith('  if (!CTX) {   // ec-teardown-ctx-v1 madeira-bcd') and b[i - 2].strip().startswith('return false;')
              and b[i - 1] == '  }', b[max(0, i - 4):i + 1])
        fn = once[once.index('bool ResetToConsistentStateImpl('):]
        fn = fn[:fn.index('\n}\n')]
        check('...inside ResetToConsistentStateImpl', 'ec-teardown-ctx-v1 madeira-bcd' in fn and ANCHOR in fn)
        # The pin's other edits of the same file, in build order (tools/build-xtajit64.sh): launcher-smc before, alias-capacity after.
        fresh = Path(tmp) / 'seq'
        (fresh / 'Source/Windows/ARM64EC').mkdir(parents=True)
        shutil.copy(module, fresh / 'Source/Windows/ARM64EC/Module.cpp')
        if alias.is_file():
            shutil.copy(alias, fresh / 'Source/Windows/ARM64EC/IosJitAlias.cpp')
        steps = []
        if 'ec-ret-direct-v1 madeira-bcd' in before:
            steps.append([sys.executable, str(root / 'tools/patch-fex-ios-launcher-smc.py'), str(fresh / 'Source/Windows/ARM64EC/Module.cpp')])
        steps.append([sys.executable, str(patch), str(fresh)])
        if alias.is_file():
            steps.append([sys.executable, str(root / 'tools/patch-fex-ios-alias-capacity.py'), str(fresh)])
        res = [subprocess.run(s, capture_output=True, text=True) for s in steps]
        final = (fresh / 'Source/Windows/ARM64EC/Module.cpp').read_text()
        check('in build order with the pin\'s other Module.cpp patches, every one applies and the guard stays',
              all(x.returncode == 0 for x in res) and 'ec-teardown-ctx-v1 madeira-bcd' in final,
              ' | '.join((x.stdout + x.stderr).strip() for x in res))
else:
    print(f'SKIP the patch run: no FEX tree at {fex} (set FEX_SRC)')

sh = (root / 'tools/build-xtajit64.sh').read_text()
fi = sh.index('python3 "$R/tools/patch-fex-ios-mono-bp-unlocked-miss.py"')
line = 'python3 "$R/tools/patch-fex-ios-ec-teardown-ctx.py" "$R/FEX"'
restore = next((l for l in sh.splitlines() if l.startswith('git -C FEX checkout -- "$CPUF"')), '')
check('tools/build-xtajit64.sh applies it once to every pin (after the pin branches), before the build, and restores Module.cpp',
      sh.count(line) == 1 and sh.index(line) > fi and '\nbuild\n' in sh[sh.index(line):]
      and sh.index(restore) > sh.index(line) and 'Source/Windows/ARM64EC/Module.cpp' in restore)
rules = (root / 'build/tools/autotune_rules.py').read_text()
r = rules[rules.index('"id": "fex-runtime-fault"'):]
r = r[:r.index('\n    },\n')]
check('auto-tune\'s fex-runtime-fault leaves the 624-625 teardown fault to the session\'s own verdict',
      '"none": [s("[fex-fault] pc=libarm64ecfex.dll+0x1123c4")]' in r, r[:600])
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-fex-ec-teardown-ctx.py' in wf)
print('PASS: FEX torn-down context' if ok else 'FAILED: FEX torn-down context')
sys.exit(0 if ok else 1)
