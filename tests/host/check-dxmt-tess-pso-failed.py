#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A tessellation or geometry mesh pipeline that did not compile: its draws are dropped (madeira-bcd, round 626, agent T S2).

Batman: Arkham City (9AGR39 = 1WAGW3, AE9TXK, build 623, iPad17,2) played 65 s, then a tessellation mesh pipeline failed to compile
("Total mesh size (33792) exceeds the maximum mesh size allowed (32768)"); its encoder lambda bound nothing, the draws that followed
were encoded as mesh draws anyway and the driver faulted in drawMeshThreadgroups. tools/patch-dxmt-tess-pso-failed.py records "no
pipeline" per kind in ArgumentEncodingContext and returns from the four tessellation and the four geometry draw lambdas while it is
set. This check applies the patch to a copy of the pinned dxmt/src (or $DXMT_SRC_DIR) and reads the result; the whole chain is applied
and re-applied by check-dxmt-patch-chain.py. SKIP of that part when no dxmt sources exist.
"""
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


script = R / 'tools/patch-dxmt-tess-pso-failed.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it, and this check runs first', 'python3 tools/patch-dxmt-tess-pso-failed.py' in wf
      and 'python3 tests/host/check-dxmt-tess-pso-failed.py' in wf
      and wf.index('python3 tests/host/check-dxmt-tess-pso-failed.py') < wf.index('python3 tools/patch-dxmt-tess-pso-failed.py'))
check('it runs before the i386 farm key (which hashes tools/patch-dxmt-*.py)',
      wf.index('python3 tools/patch-dxmt-tess-pso-failed.py') < wf.index('key=i386-farm-') and "'tools/patch-dxmt-*.py'" in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
cpp_src, hpp_src = src / 'd3d11/d3d11_context_impl.cpp', src / 'dxmt/dxmt_context.hpp'
if cpp_src.exists() and hpp_src.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d11').mkdir(parents=True)
        (t / 'dxmt/src/dxmt').mkdir(parents=True)
        shutil.copy(cpp_src, t / 'dxmt/src/d3d11/d3d11_context_impl.cpp')
        shutil.copy(hpp_src, t / 'dxmt/src/dxmt/dxmt_context.hpp')
        before = (t / 'dxmt/src/d3d11/d3d11_context_impl.cpp').read_text()
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned source and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        s = (t / 'dxmt/src/d3d11/d3d11_context_impl.cpp').read_text()
        h = (t / 'dxmt/src/dxmt/dxmt_context.hpp').read_text()
        check('ArgumentEncodingContext gets the two flags, false by default',
              'bool tess_pso_failed = false;' in h and 'bool gs_pso_failed = false;' in h)

        def body(name, until):
            f = s[s.index('  ' + name + '(\n'):]
            return f[:f.index(until)]

        for name in ('TessellationDraw', 'TessellationDrawIndexed', 'TessellationDrawIndirect', 'TessellationDrawIndexedIndirect'):
            f = body(name, '\n  }\n')
            lam = f[f.index('(ArgumentEncodingContext &enc) {'):]
            first = lam.split('\n')[1].strip()
            check(f'{name}: the encoder lambda returns first thing when the tessellation pipeline is missing',
                  first.startswith('if (enc.tess_pso_failed) return;'), first)
        for name in ('GeometryDraw', 'GeometryDrawIndexed', 'GeometryDrawIndirect', 'GeometryDrawIndexedIndirect'):
            f = body(name, '\n  }\n')
            lam = f[f.index('(ArgumentEncodingContext &enc) {'):]
            first = lam.split('\n')[1].strip()
            check(f'{name}: the encoder lambda returns first thing when the geometry pipeline is missing',
                  first.startswith('if (enc.gs_pso_failed) return;'), first)
        ts = s[s.index('FinalizeTessellationRenderPipeline() {'):]
        ts = ts[:ts.index('cmdbuf_state = CommandBufferState::TessellationRenderPipelineReady;')]
        check('the tessellation pipeline lambda sets the flag from the pipeline state before it decides, and says so when missing',
              ts.index('enc.tess_pso_failed = !GraphicsPipeline.PipelineState;') < ts.index('if (!GraphicsPipeline.PipelineState) {')
              and 'madeira_mesh_pso_missing("tessellation");' in ts and 'cmd.pso = GraphicsPipeline.PipelineState;' in ts)
        gs = s[s.index('FinalizeGeometryRenderPipeline() {'):]
        gs = gs[:gs.index('cmdbuf_state = CommandBufferState::GeometryRenderPipelineReady;')]
        check('the geometry pipeline lambda does the same for its flag',
              gs.index('enc.gs_pso_failed = !GraphicsPipeline.PipelineState;') < gs.index('if (!GraphicsPipeline.PipelineState) {')
              and 'madeira_mesh_pso_missing("geometry");' in gs and 'cmd.pso = GraphicsPipeline.PipelineState;' in gs)
        helper = s[s.index('static inline void madeira_mesh_pso_missing(const char *kind)'):]
        helper = helper[:helper.index('\n}\n')]
        check('the [mesh-skip] ml870 line is capped (once, then every 1024th)', '[mesh-skip] ml870 ' in helper
              and '(n == 1 || (n & 0x3FF) == 0)' in helper)
        # Nothing else moved: apart from the inserted lines, the file is the pinned one.
        added = [l for l in s.splitlines() if l not in set(before.splitlines())]
        foreign = [l for l in added if 'tess-pso-failed-v1' not in l and 'madeira_mesh_pso_missing' not in l and l.strip() not in
                   ('', '}', 'return;', '{', 'static std::atomic<uint32_t> missing{0};',
                    'uint32_t n = missing.fetch_add(1, std::memory_order_relaxed) + 1;', 'if (n == 1 || (n & 0x3FF) == 0)',
                    'if (!GraphicsPipeline.PipelineState) {')
                   and not l.strip().startswith(('*', '/*', 'ERR("[mesh-skip] ml870 '))]
        check('the patch only adds lines (the draws whose pipeline exists are encoded as before)', not foreign, foreign[:8])
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll (64-bit games, env.MADEIRA_D3D11_SRC = 1) is built with it',
      '"patch-dxmt-tess-pso-failed.py" "[mesh-skip] ml870 "' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries it',
      any('"[mesh-skip] ml870 "' in line for line in wf.splitlines() if line.strip().startswith('for m in "[copy-structure-count] skipped #"')))
rules = (R / 'build/tools/autotune_rules.py').read_text()
rule = rules[rules.index('"id": "tessellation-mesh-pso-failed"'):] if '"id": "tessellation-mesh-pso-failed"' in rules else ''
rule = rule[:rule.index('\n    },')] if rule else ''
check('auto-tune knows the crash of the builds before it (fixedIn 626)', '"fixedIn": 626' in rule
      and 'exceeds the maximum mesh size allowed' in rule and '[mesh-skip] ml870' in rule, rule[:300])
check('the fixture proves the rule', (R / 'tests/fixtures/autotune/batman-arkham-city-tess-pso-2026-10-31.expect').exists())
print('PASS: DXMT mesh pipeline failures' if ok else 'FAILED: DXMT mesh pipeline failures')
sys.exit(0 if ok else 1)
