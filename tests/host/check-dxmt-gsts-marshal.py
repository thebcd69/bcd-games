#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT's GS/TS draw-argument marshal loops advance; a zero-size draw dispatches nothing (madeira-bcd, build 623).

tools/patch-dxmt-gsts-marshal.py backports willfaust/dxmt d811847f2 (3Shain/dxmt aadf0fb + the zero-count guard): both loops in
src/dxmt/dxmt_command.metal never did index++, so a batch of two or more geometry/tessellation draws spun on tasks[0] until the
GPU timed out. This applies the patch to a copy of the pinned file (dxmt/src or $DXMT_SRC_DIR; in CI the patch step may have run
already), checks both functions (the guard; index++ after the end_of_command break, so a one-draw batch is unchanged) and, when
a Metal compiler is present, that the patched file compiles as build/dxmt-ios/build.sh compiles it. SKIP without dxmt sources.
"""
import os
import pathlib
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


script = R / 'tools/patch-dxmt-gsts-marshal.py'
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies the patch and runs this check',
      'python3 tools/patch-dxmt-gsts-marshal.py' in wf and 'python3 tests/host/check-dxmt-gsts-marshal.py' in wf)
src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src' / 'dxmt' / 'dxmt_command.metal'
if not src.exists():
    print(f'SKIP: no {src}')
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/dxmt').mkdir(parents=True)
        shutil.copy(src, t / 'dxmt/src/dxmt/dxmt_command.metal')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        # On a pin that carries willfaust/dxmt d811847f itself (since the 2026-11-14 upstream merge) both runs say so and change
        # nothing; the checks of both functions below then read the fork's own lines.
        upstream = 'is upstream' in r1.stdout
        check('it applies (or finds the file patched) and a second run says "already patched"; or the fix is upstream, twice',
              r1.returncode == 0 and r2.returncode == 0
              and (('is upstream' in r2.stdout and 'nothing to do' in r1.stdout) if upstream
                   else ('patched' in r1.stdout and 'already patched' in r2.stdout)),
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        check('a pin with the upstream fix is left byte for byte as it was',
              not upstream or (t / 'dxmt/src/dxmt/dxmt_command.metal').read_bytes() == src.read_bytes())
        s = (t / 'dxmt/src/dxmt/dxmt_command.metal').read_text()
        for fn in ('ts_draw_arguments_marshal', 'gs_draw_arguments_marshal'):
            a = s.index('[[vertex]] void ' + fn + '(')
            body = s[a:s.index('\n}\n', a)]
            check(f'{fn}: a zero-size draw dispatches nothing',
                  'if ((task.draw_arguments.x * task.draw_arguments.y) == 0 ||' in body
                  and 'x * task.draw_arguments.y > task.max_object_threadgroups) {' in body)
            check(f'{fn}: index++ after the end_of_command break (a one-draw batch is as before)',
                  body.count('index++;') == 1
                  and body.index('if (task.end_of_command)\n      break;') < body.index('index++;')
                  and 'constant ' in body[body.index('for(;;) {'):body.index('task = tasks[index];') + 20])
        metal = shutil.which('xcrun')
        if metal and subprocess.run(['xcrun', '-sdk', 'macosx', 'metal', '--version'], capture_output=True).returncode == 0:
            c = subprocess.run(['xcrun', '-sdk', 'macosx', 'metal', '-std=metal3.1', '--target=air64-apple-macos14.0',
                                '-c', str(t / 'dxmt/src/dxmt/dxmt_command.metal'), '-o', str(t / 'cmd.air')],
                               capture_output=True, text=True)
            check('the patched file compiles (metal3.1, air64-apple-macos14.0, as build/dxmt-ios/build.sh)', c.returncode == 0,
                  c.stderr[-1500:])
        else:
            print('note: no Metal compiler here; the compile check runs in CI')
if not ok:
    print('FAILED: dxmt-gsts-marshal')
    sys.exit(1)
print('PASS: DXMT GS/TS marshal loops advance; zero-size draws dispatch nothing')
