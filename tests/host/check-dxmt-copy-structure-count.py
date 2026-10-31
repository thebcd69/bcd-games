#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
"""DXMT CopyStructureCount with an empty destination or counter is skipped (madeira-bcd, round 620).

F1 2020 (4W6WMV, G1WB9T, build 614) died on DXMT's encode thread at d3d11.dll+0x8af18, the CopyStructureCount encoder
lambda reading an empty captured buffer (`ldr x20,[x8,#8]`, x8 = 0). tools/patch-dxmt-copy-structure-count.py takes the
two buffers before the blit encoder is switched on and returns when either is empty, with a capped [copy-structure-count]
log line. This check applies the patch to a copy of the pinned dxmt/src (or $DXMT_SRC_DIR) and reads the result; the
whole patch chain is applied and re-applied by check-dxmt-patch-chain.py. SKIP of that part when no dxmt sources exist.
"""
import os, pathlib, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        ok = False
        if detail:
            print('     ' + str(detail)[:1500])


script = R / 'tools/patch-dxmt-copy-structure-count.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it', 'python3 tools/patch-dxmt-copy-structure-count.py' in wf)
check('CI runs this check', 'python3 tests/host/check-dxmt-copy-structure-count.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
target = src / 'd3d11/d3d11_context_impl.cpp'
if target.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d11').mkdir(parents=True)
        shutil.copy(target, t / 'dxmt/src/d3d11/d3d11_context_impl.cpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned source and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        s = (t / 'dxmt/src/d3d11/d3d11_context_impl.cpp').read_text()
        fn = s[s.index('CopyStructureCount(ID3D11Buffer *pDstBuffer'):]
        fn = fn[:fn.index('UpdateSubresource(')]
        guard = fn.index('if (!dst_rc || !counter_rc)')
        check('the empty-buffer test comes before the blit encoder is switched on and the command is emitted',
              guard < fn.index('SwitchToBlitEncoder(') < fn.index('EmitOP('), fn)
        check('the emitted command captures the same two buffers it tested', 'dst = std::move(dst_rc), counter = std::move(counter_rc)' in fn)
        check('the encoder body is unchanged (copy of 4 bytes from the counter to the destination)',
              'cmd.copy_length = 4;' in fn and 'cmd.src = counter_buffer->buffer();' in fn and 'cmd.dst = dst_buffer->buffer();' in fn)
        check('the log line is capped (8, then powers of two)', '(n <= 8 || !(n & (n - 1)))' in fn and '[copy-structure-count]' in fn)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

# The CI compiles only the 32-bit d3d11.dll; the 64-bit one is upstream's committed binary, and F1 2020 is 64-bit. The patch
# reaches it through d3d11-src.dll (tools/build-d3d11-dll.sh, on a copy), which env.MADEIRA_D3D11_SRC = 1 selects.
sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll is built with the patch and refused without it',
      '"patch-dxmt-copy-structure-count.py" "[copy-structure-count] skipped #"' in sh
      and '(cd "$OUT/patchroot" && python3 "$R/tools/${BCD_PATCHES[$i]}") || fail' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries the patch',
      any('"[copy-structure-count] skipped #"' in line for line in wf.splitlines() if line.strip().startswith('for m in "[copy-structure-count] skipped #"')) and "no 64-bit d3d11.dll carries BCD's Direct3D 11 patches" in wf)
rules = (R / 'build/tools/autotune_rules.py').read_text()
blk = rules[rules.index('"id": "dxmt-copy-structure-count-null"'):][:1800] if '"id": "dxmt-copy-structure-count-null"' in rules else ''
check('auto-tune answers the crash with d3d11-src.dll (no "fixed in" claim: the 64-bit default DLL is unchanged)',
      '"lines": ["env.MADEIRA_D3D11_SRC = 1"]' in blk and '"fixedIn"' not in blk.split('"evidence"')[0] and 'd3d11.dll+0x8af18' in blk, blk[:300])
print('PASS: DXMT CopyStructureCount' if ok else 'FAILED: DXMT CopyStructureCount')
sys.exit(0 if ok else 1)
