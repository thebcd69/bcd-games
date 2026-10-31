#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
"""Render target views on a buffer (madeira-bcd, round 620): every failed view is logged, and an opt-in empty view.

Need for Speed: The Run (Frostbite 2, 32-bit; FRGCNV and a Discord log of 2026-10-28, build 614) stops while loading with
"DirectX function g_dxRenderer->getDevice()->CreateRenderTargetView(...) failed with E_INVALIDARG": it asks for a render target
view on a buffer, which DXMT does not have. DXVK answered S_OK and no view for that until 2026-08 (Battlefield 3 and 4).
tools/patch-dxmt-buffer-rtv.py logs every failed CreateRenderTargetView ([rtv-fail], capped) and, with
env.MADEIRA_BUFFER_RTV_EMPTY = 1 (or d3d11.bufferRtvEmpty=1), returns S_OK and no view for a buffer; ClearRenderTargetView
ignores a null view and the D3D10 wrapper passes an empty view on instead of calling through it. Default off.

This check applies the patch to copies of the pinned files (dxmt/src or $DXMT_SRC_DIR), reads the result, and checks the
wiring: the CI patch step, the 64-bit d3d11-src.dll, the catalog, the list policy, the session-end cleanup, the auto-tune
rule and the game's list. The whole patch chain is applied twice by check-dxmt-patch-chain.py; tools/build-d3d11-dll.sh
compiles and links the patched files for arm64ec.
"""
import json, os, pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        ok = False
        if detail:
            print('     ' + str(detail)[:1500])


script = R / 'tools/patch-dxmt-buffer-rtv.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it', 'python3 tools/patch-dxmt-buffer-rtv.py' in wf)
check('CI runs this check', 'python3 tests/host/check-dxmt-buffer-rtv.py' in wf)

FILES = ['d3d11/d3d11_device.cpp', 'd3d11/d3d11_context_impl.cpp', 'd3d10/d3d10_device.cpp']
src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if all((src / f).exists() for f in FILES):
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        for f in FILES:
            (t / 'dxmt/src' / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src / f, t / 'dxmt/src' / f)
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        # In CI the workflow's patch step has already run on dxmt/src, so the first run may say "already patched" too.
        check('it applies to the three pinned files (or finds them patched) and a second run says "already patched" for each',
              r1.returncode == 0 and r1.stdout.count(': patched') + r1.stdout.count(': already patched') == 3 and r2.returncode == 0
              and r2.stdout.count('already patched') == 3, r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        dev = (t / 'dxmt/src/d3d11/d3d11_device.cpp').read_text()
        ctx = (t / 'dxmt/src/d3d11/d3d11_context_impl.cpp').read_text()
        d10 = (t / 'dxmt/src/d3d10/d3d10_device.cpp').read_text()

        # The switch: environment first ("0" off, anything else on), then the DXMT option; off when neither is set.
        sw = dev[dev.index('bool MadeiraBufferRtvEmpty() {'):]
        sw = sw[:sw.index('\n}\n')]
        check('the switch is off unless MADEIRA_BUFFER_RTV_EMPTY or d3d11.bufferRtvEmpty asks for it',
              'env::getEnvVar("MADEIRA_BUFFER_RTV_EMPTY")' in sw and 'return env_v != "0";' in sw
              and 'getOption<int>("d3d11.bufferRtvEmpty", 0) > 0' in sw and 'kMadeira32BitModule' not in sw
              and 'madeiraSwitch' not in sw, sw)

        fn = dev[dev.index('HRESULT STDMETHODCALLTYPE CreateRenderTargetView1('):]
        fn = fn[:fn.index('\n  }\n') + 4]
        check('a failed view is logged, and only a BUFFER with the switch on returns S_OK',
              'HRESULT hr = static_cast<D3D11ResourceCommon *>(pResource)->CreateRenderTargetView(pDesc, ppRTView);' in fn
              and 'if (FAILED(hr)) {' in fn
              and 'const bool empty = dim == D3D11_RESOURCE_DIMENSION_BUFFER && MadeiraBufferRtvEmpty();' in fn
              and 'if (empty)\n        return S_OK;' in fn and fn.rstrip().endswith('return hr;\n  }') , fn)
        check('the view pointer is still the null InitReturnPtr wrote (nothing writes it on the empty path)',
              fn.index('InitReturnPtr(ppRTView);') < fn.index('HRESULT hr =') and '*ppRTView =' not in fn)
        check('a null resource stays E_INVALIDARG (logged) and a null ppRTView stays S_FALSE',
              'MadeiraLogRtvFailure(nullptr, pDesc, E_INVALIDARG, false);' in fn and 'return E_INVALIDARG;' in fn
              and 'if (!ppRTView)\n      return S_FALSE;' in fn)
        lg = dev[dev.index('void MadeiraLogRtvFailure('):]
        lg = lg[:lg.index('\n}\n')]
        check('the log line is capped (8, then powers of two) and names the call, the code, the resource and the view',
              'if (n > 8 && (n & (n - 1)))\n    return;' in lg and 'ERR("[rtv-fail] #", n, " CreateRenderTargetView ", code' in lg
              and '"resource=buffer bytes=%u' in lg and 'dimension=buffer first=%u count=%u' in lg
              and 'snprintf(code, sizeof code, "E_INVALIDARG");' in lg, lg)
        check('the line says how the empty view was chosen (auto-tune reads it)',
              '" -> S_OK with no view (env.MADEIRA_BUFFER_RTV_EMPTY)"' in lg)
        check('the device file includes what it uses', '#include <atomic>' in dev and '#include <cstdio>' in dev
              and '#include "util_env.hpp"' in dev and dev.index('#include "util_env.hpp"') < dev.index('namespace dxmt {'))

        clr = ctx[ctx.index('ClearRenderTargetView(ID3D11RenderTargetView *pRenderTargetView, const FLOAT ColorRGBA[4]) override {'):]
        clr = clr[:clr.index('\n  }\n')]
        check('ClearRenderTargetView ignores a null view before it takes the lock or dereferences it',
              clr.index('if (!pRenderTargetView)\n      return;') < clr.index('std::lock_guard') < clr.index('static_cast<D3D11RenderTargetView *>'), clr)
        inner = ctx[ctx.index('  ClearRenderTargetView(D3D11RenderTargetView *pRenderTargetView, const FLOAT ColorRGBA[4]) {'):]
        inner = inner[:inner.index('\n  }\n')]
        check('the clear itself is unchanged', 'pRenderTargetView->description();' in inner and 'enc.clearColor(' in inner)

        d = d10[d10.index('MTLD3D10Device::CreateRenderTargetView('):]
        d = d[:d.index('\n}\n')]
        check('the D3D10 wrapper returns S_OK and no view for an empty D3D11 view instead of calling through it',
              d.index('if (ppRTView && !d3d11_view)\n    return S_OK;') < d.index('d3d11_view->QueryInterface('), d)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

# The 64-bit d3d11.dll is upstream's committed binary; the patch reaches a 64-bit game through d3d11-src.dll.
sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll is built with the patch and refused without it',
      '"patch-dxmt-buffer-rtv.py" "[rtv-fail] #"' in sh and 'is not in the result (no' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries it', any('"[rtv-fail] #"' in line for line in wf.splitlines() if line.strip().startswith('for m in "[copy-structure-count] skipped #"')))

bridge = (R / 'app/Madeira/WineProcessBridge.m').read_text()
end = bridge[bridge.index('static void wine_session_end(void)\n{'):]   # the definition, not the declaration near the top
end = end[:end.index('\n}\n')]
check('the switch and d3d11-src.dll end with the session', 'unsetenv("MADEIRA_BUFFER_RTV_EMPTY");' in end and 'unsetenv("MADEIRA_D3D11_SRC");' in end)

cat = (R / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog lists env.MADEIRA_BUFFER_RTV_EMPTY, off by default',
      re.search(r'key: "env\.MADEIRA_BUFFER_RTV_EMPTY".*kind: \.bool, defaultValue: "0"', cat) is not None)
pol = (R / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a downloaded list may set it (build 620)', '"MADEIRA_BUFFER_RTV_EMPTY",' in pol)

rules = (R / 'build/tools/autotune_rules.py').read_text()
i = rules.find('"id": "d3d11-buffer-render-target-view"')
blk = rules[i:i + 4000] if i >= 0 else ''
blk = blk[:blk.index('"evidence"')] if '"evidence"' in blk else blk
check('auto-tune answers the box with both lines, and not again once the empty view is in force',
      '"lines": ["env.MADEIRA_BUFFER_RTV_EMPTY = 1", "env.MADEIRA_D3D11_SRC = 1"]' in blk
      and 's("CreateRenderTargetView", "E_INVALIDARG")' in blk and 's("[rtv-fail]", "resource=buffer")' in blk
      and '"none": [s("S_OK with no view (env.MADEIRA_BUFFER_RTV_EMPTY)")]' in blk, blk[:400])
fx = R / 'tests/fixtures/autotune/need-for-speed-the-run-buffer-rtv-2026-10-28.expect'
exp = json.loads(fx.read_text()) if fx.exists() else {}
# round 624: the lowest-priority diagnostic rule x86-null-fault-diag may match the same 32-bit fault after it; the first match decides.
check('the Discord log (build 614) is a fixture that gets both lines',
      (exp.get('matched') or [None])[0] == 'd3d11-buffer-render-target-view'
      and exp.get('next') == ['env.MADEIRA_BUFFER_RTV_EMPTY = 1', 'env.MADEIRA_D3D11_SRC = 1'], exp)

lists = json.loads((R / 'recommended/recommendations.json').read_text())
nfs = next((r for r in lists['rules'] if r['id'] == 'need-for-speed-the-run'), None)
check('Need for Speed: The Run\'s list sets the switch from build 620',
      nfs is not None and {'line': 'env.MADEIRA_BUFFER_RTV_EMPTY = 1', 'minBuild': 620} in nfs['config'], nfs)
print('PASS: DXMT render target views on a buffer' if ok else 'FAILED: DXMT render target views on a buffer')
sys.exit(0 if ok else 1)
