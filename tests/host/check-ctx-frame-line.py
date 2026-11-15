#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""env.MADEIRA_CTX_FRAME = 1 for the Unity stop that follows a garbage collection (build 598); no Wine runs.

The Wine server can report a thread parked in a system call by its saved call frame instead of the Mach-O registers it happens to be
executing (ml716, build/wineserver/mach_ios.c; off unless MADEIRA_CTX_FRAME is 1). Build 592's Unity logs (No Rest for the Wicked GGFKAY and
RKZSCQ, and 29 more of seven other titles) show NtGetContextThread answering a collector's stopped threads with the Mach-O state
("[ec-getctx] ml715 ... is_ec=0 ... native_pc=0x2501e8ae8") and the object-header stop ("[badrip]") a few dozen lines after the last burst.
Round 2026-10-24 offers the existing switch to that stop, opt in: the list line of No Rest for the Wicked (version 4, build 598) and the
auto-tune rule unity-gc-stopped-thread-context. This check holds the pieces together:

  - the server reads the switch once, by name, and only the value "1" turns it on; the substitution is gated by it and by the same test as
    Wine's own suspend path (the stack pointer lies between the thread's kernel stack and its syscall frame), and falls through otherwise;
  - the list policy knows the name and takes 0 or 1 only (the Swift harness of check-remote-recommendations.py runs the values);
  - only No Rest for the Wicked's list carries the line, from build 598, and its version is 4;
  - the auto-tune rule's line, signals and outcome, and a fixture whose log really has the burst and the stop.
Needs python3 only.
"""
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + detail) if (detail and not cond) else ''))
    ok = ok and bool(cond)


srv = (root / 'build/wineserver/mach_ios.c').read_text()
check('the switch is read once, by name', srv.count('getenv( "MADEIRA_CTX_FRAME" )') == 1)
check('only the value "1" turns it on', "ctx_frame_env = (e && e[0] == '1') ? 1 : 0;" in srv)
check('the substitution needs the switch and a thread with a TEB', 'if (ctx_frame_env && thread->teb)' in srv)
check("the substitution is Wine's own test: the stack pointer between the kernel stack and the syscall frame, frame and stack valid",
      'frame && kstack && !(frame & 7) && kstack < frame &&\n                sp >= kstack && sp <= frame' in srv)
sub = srv[srv.index('if (ctx_frame_env && thread->teb)'):srv.index('/* --- WoW64 (i386) process')]
# Round 660 (M001): the [ctx-frame] values are kept inside the substitution and the line is printed after the thread's momentary halt
# (a write to stderr while the target is suspended can wait on a lock it holds), so the print follows thread_resume().
after = srv[srv.index('if (ctx_frame_env && thread->teb)'):]
check('it only replaces the native context (pc, sp, x0-x30, pstate) and logs a capped [ctx-frame] ml716 line, after the halt',
      'native->ctl.arm64_regs.pc     = fpc;' in sub and 'n_sub++ < 48' in sub and 'n_frame_line = n_sub;' in sub
      and '[ctx-frame] ml716' not in sub and 'thread_resume( port );' in after
      and after.index('thread_resume( port );') < after.index('[ctx-frame] ml716'))
check('a failed validation changes nothing (no else branch that fills a context)', 'else' not in sub.replace('n_sub++ < 48', ''))

policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('the list policy knows the name', '"MADEIRA_CTX_FRAME"' in policy.split('static let allowedEnvNames')[1].split(']')[0])
case = policy[policy.index('case "MADEIRA_CTX_FRAME":'):]
case = case[:case.index('case "MADEIRA_USER_VA_GB":')]
check('...and takes 0 or 1 only', 'guard value == "0" || value == "1" else' in case)

recs = json.loads((root / 'recommended/recommendations.json').read_text())
carriers = [r['id'] for r in recs['rules'] if 'MADEIRA_CTX_FRAME' in json.dumps(r['config'])]
check('only No Rest for the Wicked carries the line', carriers == ['no-rest-for-the-wicked'], carriers)
nr = next(r for r in recs['rules'] if r['id'] == 'no-rest-for-the-wicked')
check('...from build 598, as the last line, in version 4', {'line': 'env.MADEIRA_CTX_FRAME = 1', 'minBuild': 598} == nr['config'][-1] and nr['version'] == 4)
notes = ' '.join(nr['notes'])
check('the list note quotes the lines it comes from', all(t in notes for t in ('GGFKAY L40884', 'RKZSCQ L62769', '[ec-getctx] ml715', '[srv-getctx] ml715', 'is_ec=0')))

rules = {}
src = (root / 'build/tools/autotune_rules.py').read_text()
ns = {}
exec(compile(src, 'autotune_rules.py', 'exec'), ns)
for r in ns['RULES']:
    rules[r['id']] = r
rule = rules.get('unity-gc-stopped-thread-context')
check('the auto-tune rule exists and adds exactly the one line', rule is not None and rule['lines'] == ['env.MADEIRA_CTX_FRAME = 1'])
if rule:
    check('...for a crash only, with the stop ([badrip]) and at least three parked threads asked for their context, and not again once the line ran',
          rule['outcomes'] == ['crashed']
          and {'has': ['[badrip] rev=ajan2-tani', 'is not a canonical user address']} in rule['all']
          and {'has': ['[ec-getctx] ml715', 'is_ec=0'], 'count': 3} in rule['all']
          and {'has': ['[madeira-env] game MADEIRA_CTX_FRAME=1']} in rule['none'])
fx = (root / 'tests/fixtures/autotune/no-rest-for-the-wicked-gc-badrip-2026-10-23.log').read_text().splitlines()
getctx = [i for i, l in enumerate(fx) if '[ec-getctx] ml715' in l and 'is_ec=0' in l]
bad = [i for i, l in enumerate(fx) if l.startswith('[badrip] rev=ajan2-tani') and 'is not a canonical user address' in l]
check('the fixture has the Mach-O answers and the stop after them', len(getctx) >= 3 and bad and getctx[-1] < bad[0], (len(getctx), bad))
expect = json.loads((root / 'tests/fixtures/autotune/no-rest-for-the-wicked-gc-badrip-2026-10-23.expect').read_text())
check('...and expects the line', expect['matched'] == ['unity-gc-stopped-thread-context'] and expect['next'] == ['env.MADEIRA_CTX_FRAME = 1'])
# no other rule adds the line, and none is for this game by name: the stop is the signal
check('no other auto-tune rule adds it', [i for i, r in rules.items() if any('MADEIRA_CTX_FRAME' in l for l in r['lines'])] == ['unity-gc-stopped-thread-context'])

print('PASS: ' if ok else 'FAIL: ', 'the stopped-thread context switch: server gate, policy, list line, auto-tune rule, fixture')
raise SystemExit(0 if ok else 1)
