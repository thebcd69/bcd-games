#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""An SRV's GetDesc reports the real mip count (madeira-bcd, round 624): Dark Souls III, opt-in.

Dark Souls III (Steam 374320; 10 logs of builds 565-622) wrote through NULL 26 s in: it made SRVs with MipLevels = -1 ("all
mips"), read the description back with GetDesc and sized an array with the count; DXMT returned 0xFFFFFFFF. With
env.MADEIRA_SRV_GETDESC_RESOLVE = 1 (or d3d11.srvGetDescResolve=1) tools/patch-dxmt-srv-getdesc-mips.py resolves MipLevels and
ArraySize = -1 in the description the view keeps; the Metal view is made from the translated copy as before. Default off.

This check applies the patch to a copy of the pinned file (dxmt/src or $DXMT_SRC_DIR), reads the result, compiles the resolver
on the host with stand-in types and runs it over every view dimension, and checks the wiring: the CI patch step, the 64-bit
d3d11-src.dll recipe, the catalog, the list policy, the auto-tune rule with its fixtures and the game's list row. The whole patch
chain is applied twice by check-dxmt-patch-chain.py; tools/build-d3d11-dll.sh compiles and links the patched file for arm64ec.
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


script = R / 'tools/patch-dxmt-srv-getdesc-mips.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it', 'python3 tools/patch-dxmt-srv-getdesc-mips.py' in wf)
check('CI runs this check', 'python3 tests/host/check-dxmt-srv-getdesc.py' in wf)
d11 = (R / 'tools/build-d3d11-dll.sh').read_text()
check('the 64-bit d3d11-src.dll is built with it (BCD_PATCHES, with the marker its code leaves in the DLL)',
      '"patch-dxmt-srv-getdesc-mips.py" "[srv-getdesc] madeira-bcd #"' in d11)
check('the IPA is refused when no 64-bit d3d11.dll carries it (round 624)',
      any('"[srv-getdesc] madeira-bcd #"' in line for line in wf.splitlines()
          if line.strip().startswith('for m in "[copy-structure-count] skipped #"')))

F = 'd3d11/d3d11_texture_device.cpp'
src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if (src / F).exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src' / F).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / F, t / 'dxmt/src' / F)
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        # In CI the workflow's patch step has already run on dxmt/src, so the first run may say "already patched" too.
        check('it applies to the pinned file (or finds it patched) and a second run says "already patched"',
              r1.returncode == 0 and ('patched:' in r1.stdout) and r2.returncode == 0 and 'already patched' in r2.stdout,
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        text = (t / 'dxmt/src' / F).read_text()
        check('the marker is there twice (the helpers and the call)', text.count('madeira-bcd: SRV GetDesc resolves') == 2)
        check('the helpers sit in an anonymous namespace before the DeviceTexture region',
              text.index('bool MadeiraSrvResolveOn() {') < text.index('#pragma region DeviceTexture')
              and text.index('namespace {') < text.index('bool MadeiraSrvResolveOn() {'))
        sw = text[text.index('bool MadeiraSrvResolveOn() {'):]
        sw = sw[:sw.index('\n}\n')]
        check('the switch: the environment first ("0" off, anything else on), then the DXMT option, off when neither is set',
              'env::getEnvVar("MADEIRA_SRV_GETDESC_RESOLVE")' in sw and 'return env_v != "0";' in sw
              and 'getOption<int>("d3d11.srvGetDescResolve", 0) > 0' in sw and 'static const bool on' in sw, sw)
        call = text[text.index('TextureViewKey key = underlying_texture_->createView(descriptor);\n    if (MadeiraSrvResolveOn())'):]
        call = call[:call.index('return S_OK;')]
        check('the SRV path resolves the LOGICAL description after the Metal view is made from the translated copy, only with the switch',
              'MadeiraResolveSrvCounts(finalDesc, this->desc.MipLevels, arraySize);' in call
              and call.index('MadeiraResolveSrvCounts') < call.index('new TextureSRV(key, &finalDesc'), call)
        check('nothing else of the file changes: one call site, the UAV and RTV paths untouched',
              text.count('MadeiraResolveSrvCounts(') == 2 and text.count('MadeiraSrvResolveOn()') == 2)

        # A mutated anchor fails by name, the file stays as it was. In CI (and a local build) the workflow's patch step has
        # already run on dxmt/src, and a patched file only says "already patched" (build 624 stopped here): the mutation
        # starts from the pinned file, the submodule's HEAD, whenever the work tree carries the marker.
        pinned = (src / F).read_text()
        if 'madeira-bcd: SRV GetDesc resolves' in pinned:
            g = subprocess.run(['git', '-C', str(src.parent), 'show', 'HEAD:src/' + F], capture_output=True, text=True)
            pinned = g.stdout if g.returncode == 0 else ''
        check('the pinned file is at hand unpatched (the submodule HEAD when the work tree is patched)',
              bool(pinned) and 'madeira-bcd: SRV GetDesc resolves' not in pinned and '#pragma region DeviceTexture' in pinned)
        bad = t / 'bad'
        (bad / 'dxmt/src' / F).parent.mkdir(parents=True, exist_ok=True)
        (bad / 'dxmt/src' / F).write_text(pinned.replace('#pragma region DeviceTexture', '#pragma region DeviceTextureX'))
        r3 = subprocess.run([sys.executable, str(script)], cwd=bad, capture_output=True, text=True)
        check('a moved anchor fails by name', r3.returncode != 0 and 'anchor not found (namespace dxmt / DeviceTexture)' in (r3.stdout + r3.stderr),
              r3.stdout + r3.stderr)

        # The resolver itself, compiled on the host with stand-in types (the names and members of d3d11.h's SRV description).
        helper = text[text.index('template <typename SrvDesc>'):]
        helper = helper[:helper.index('} // namespace')]
        harness = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
typedef unsigned int UINT;
enum { D3D11_SRV_DIMENSION_UNKNOWN, D3D11_SRV_DIMENSION_BUFFER, D3D11_SRV_DIMENSION_TEXTURE1D, D3D11_SRV_DIMENSION_TEXTURE1DARRAY,
       D3D11_SRV_DIMENSION_TEXTURE2D, D3D11_SRV_DIMENSION_TEXTURE2DARRAY, D3D11_SRV_DIMENSION_TEXTURE2DMS, D3D11_SRV_DIMENSION_TEXTURE2DMSARRAY,
       D3D11_SRV_DIMENSION_TEXTURE3D, D3D11_SRV_DIMENSION_TEXTURECUBE, D3D11_SRV_DIMENSION_TEXTURECUBEARRAY };
struct Desc {
  int ViewDimension;
  struct { UINT MostDetailedMip, MipLevels; } Texture1D, Texture2D, Texture3D, TextureCube;
  struct { UINT MostDetailedMip, MipLevels, FirstArraySlice, ArraySize; } Texture1DArray, Texture2DArray;
  struct { UINT FirstArraySlice, ArraySize; } Texture2DMSArray;
  struct { UINT MostDetailedMip, MipLevels, First2DArrayFace, NumCubes; } TextureCubeArray;
};
static int logged;
#define ERR(...) (logged++)
''' + helper + r'''
int main() {
  int fails = 0;
  Desc d = {}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D; d.Texture2D.MostDetailedMip = 2; d.Texture2D.MipLevels = ~0u;
  MadeiraResolveSrvCounts(d, 10, 1); if (d.Texture2D.MipLevels != 8) { puts("FAIL 2D: -1 from mip 2 of 10 is 8"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D; d.Texture2D.MipLevels = 5;
  MadeiraResolveSrvCounts(d, 10, 1); if (d.Texture2D.MipLevels != 5) { puts("FAIL 2D: an explicit count is kept"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D; d.Texture2D.MostDetailedMip = 12; d.Texture2D.MipLevels = ~0u;
  MadeiraResolveSrvCounts(d, 10, 1); if (d.Texture2D.MipLevels != 1) { puts("FAIL 2D: past the end gives 1, never 0 or a wrap"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2DARRAY; d.Texture2DArray.MipLevels = ~0u; d.Texture2DArray.FirstArraySlice = 1;
  d.Texture2DArray.ArraySize = ~0u;
  MadeiraResolveSrvCounts(d, 4, 6); if (d.Texture2DArray.MipLevels != 4 || d.Texture2DArray.ArraySize != 5) { puts("FAIL 2D array"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2DMSARRAY; d.Texture2DMSArray.ArraySize = ~0u;
  MadeiraResolveSrvCounts(d, 1, 3); if (d.Texture2DMSArray.ArraySize != 3) { puts("FAIL MS array"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURECUBEARRAY; d.TextureCubeArray.MipLevels = ~0u; d.TextureCubeArray.NumCubes = 2;
  MadeiraResolveSrvCounts(d, 7, 12); if (d.TextureCubeArray.MipLevels != 7 || d.TextureCubeArray.NumCubes != 2) { puts("FAIL cube array"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE3D; d.Texture3D.MipLevels = ~0u; d.Texture1D.MipLevels = ~0u;
  MadeiraResolveSrvCounts(d, 3, 1); if (d.Texture3D.MipLevels != 3) { puts("FAIL 3D"); fails++; }
  d = Desc{}; d.ViewDimension = D3D11_SRV_DIMENSION_BUFFER; d.Texture2D.MipLevels = ~0u;
  MadeiraResolveSrvCounts(d, 3, 1); if (d.Texture2D.MipLevels != ~0u) { puts("FAIL a buffer view is not touched"); fails++; }
  if (logged < 1) { puts("FAIL nothing was logged"); fails++; }
  if (!fails) puts("resolver-ok");
  return fails != 0;
}
'''
        cxx = shutil.which('c++') or shutil.which('clang++')
        if cxx:
            c = t / 'h.cpp'; c.write_text(harness)
            b = subprocess.run([cxx, '-std=c++17', '-Wall', '-Werror', '-o', str(t / 'h'), str(c)], capture_output=True, text=True)
            check('the resolver compiles on the host with stand-in types', b.returncode == 0, b.stderr)
            if b.returncode == 0:
                r = subprocess.run([str(t / 'h')], capture_output=True, text=True)
                check('it resolves -1 counts to what the resource has from the first mip / slice on, keeps explicit ones and never gives 0',
                      r.returncode == 0 and 'resolver-ok' in r.stdout, r.stdout + r.stderr)
        else:
            print('skip the host compile: no C++ compiler')
else:
    print('skip the patch run: no dxmt sources (set DXMT_SRC_DIR)')

# ---------------------------------------------------------------- wiring outside DXMT
cat = (R / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the key (bool, default 0)',
      re.search(r'"env\.MADEIRA_SRV_GETDESC_RESOLVE": \{[^}]*?"kind": "bool", "default": "0"', cat, re.S) is not None)
pol = (R / 'app/Madeira/GameRecommendationsList.swift').read_text()
case_at = pol.find('"MADEIRA_SRV_GETDESC_RESOLVE":')   # the case label (the allowed-names entry ends with a comma)
case_body = pol[case_at:] if case_at >= 0 else ''
case_body = case_body[:case_body.find('\n            case ')]
check('a downloaded list may set it, 0 or 1 (RecommendationsPolicy)',
      '"MADEIRA_SRV_GETDESC_RESOLVE"' in pol.split('static let allowedEnvNames')[1].split(']')[0]
      and case_at >= 0 and 'guard value == "0" || value == "1"' in case_body, case_body[:400])
spec = __import__('importlib.util').util.spec_from_file_location('autotune_rules', R / 'build/tools/autotune_rules.py')
mod = __import__('importlib.util').util.module_from_spec(spec)
spec.loader.exec_module(mod)
rule = next((r for r in mod.RULES if r['id'] == 'dxmt-srv-getdesc-mips'), None)
check('auto-tune gives both lines after the crash, not once the patch logged a resolution',
      rule is not None and rule['lines'] == ['env.MADEIRA_D3D11_SRC = 1', 'env.MADEIRA_SRV_GETDESC_RESOLVE = 1'] and rule.get('noRetry')
      and rule.get('outcomes') == ['crashed'] and '[srv-getdesc] madeira-bcd' in json.dumps(rule.get('none', []))
      and 'x86=48 89 04 0e 48 8d 76 08 3b dd 72 d9' in json.dumps(rule['all']))
for name in ('dark-souls-iii-srv-getdesc-2026-10-28', 'dark-souls-iii-srv-getdesc-sj0tz6-2026-10-28'):
    exp = R / 'tests/fixtures/autotune' / (name + '.expect')
    data = json.loads(exp.read_text()) if exp.is_file() else {}
    check('fixture %s proposes both lines' % name,
          data.get('matched', [None])[0] == 'dxmt-srv-getdesc-mips'
          and data.get('next') == ['env.MADEIRA_D3D11_SRC = 1', 'env.MADEIRA_SRV_GETDESC_RESOLVE = 1'], data)
recs = json.loads((R / 'recommended/recommendations.json').read_text())
ds3 = [r for r in recs['rules'] if r['id'] == 'dark-souls-iii']
lines = [c for c in (ds3[0]['config'] if ds3 else []) if isinstance(c, dict)]
check('the list row of Dark Souls III carries both lines from build 624',
      {'line': 'env.MADEIRA_D3D11_SRC = 1', 'minBuild': 624} in lines and {'line': 'env.MADEIRA_SRV_GETDESC_RESOLVE = 1', 'minBuild': 624} in lines,
      lines)
setters = sorted(r['id'] for r in recs['rules'] if 'MADEIRA_SRV_GETDESC_RESOLVE' in json.dumps(r.get('config', [])))
check('no other list row sets it', setters == ['dark-souls-iii'], setters)

print('PASS: DXMT SRV GetDesc mip count' if ok else 'FAILED: DXMT SRV GetDesc mip count')
sys.exit(0 if ok else 1)
