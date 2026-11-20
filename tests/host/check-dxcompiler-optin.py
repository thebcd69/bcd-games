#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""dxcompiler-optin.dll (env.MADEIRA_OPTIN_DLLS = dxcompiler): the DirectX Shader Compiler for wgpu games (round 663, item 7c).

skate. 2 PC port (wgpu 30, Direct3D 12 back end; Send Log JQ0202, build 653) stopped at its first pipeline: wgpu found no
dxcompiler.dll, compiled with FXC (Wine's d3dcompiler_47.dll, vkd3d-shader), and naga declares a Shader Model 5.1 resource array in
every shader with a texture (`SamplerState nagaSamplerHeap[2048]`), which vkd3d-shader does not implement (E5017; upstream vkd3d
master of 2026-11-12 neither). madeira-d3d12's DXBC backend would also refuse such a range (madeira_ir_unix.mm: only singletons, only
space 0). With a dxcompiler.dll on the search path wgpu 30 (Dx12Compiler::Auto) compiles to DXIL, which madeira-d3d12 converts with
Apple's Metal Shader Converter. BCD builds DXC from source for x86-64 Windows; nothing of it changes a game that does not list it:

  - tools/build-dxcompiler.sh: DXC v1.9.2609 at its commit, the one patch, the host tablegen tools, the x86-64 DLL (no SPIR-V, no
    tests, no DXBC converter, the C++ runtime linked in), stripped, checked (tools/check-dxcompiler-shipped.py) and shipped as
    arm64ec-windows/dxcompiler-optin.dll; no llvm-mingw: nothing shipped, exit 0;
  - the patch: Raed Rizqie's MinGW-w64 port (DXC pull request #8265) without its StringRef.h hunk that removes the is_nothrow_constructible specialisations and without its
    MS_SUPPORT_VARIABLE_LANGOPTS define (with it the compiler read HLSL as C: "unknown type name 'float4'", found on the host);
  - the workflow restores / builds / saves build/dxc-win/out before "Verify the opt-in DLLs the lists and rules name";
  - RecommendationsPolicy lets a list name dxcompiler; the catalog note says what it is; auto-tune's wgpu-fxc-sm51-dxcompiler adds it
    after FXC's E5017 and comes before the noFix d3dcompile-sm51-resource-array; the skate. 2 row has it from build 663;
  - licences and notices: NCSA, kept as received; Microsoft's release binaries are not used.

With a host Wine and a built DLL at hand (BCD_HOST_WINE = a wine binary that runs x86-64 programs, plus build/dxc-win/out/dxcompiler.dll
or BCD_DXC_DLL), it also builds tests/host/dxc/dxc-probe.exe and fxc-probe.exe with llvm-mingw and runs them: FXC must stop with
E5017 on naga 30.0.1's HLSL (tests/host/dxc/naga-textured-wgpu.hlsl: naga-cli 30.0.1's output for textured.wgsl, its comparison heap
moved to register(s2048, space0) as wgpu-hal's dx12/device.rs binds it), and DXC must compile both of its stages to hashed DXIL; with
BCD_NATIVE_DXC (a dxc for the Mac built from the same tag) the DXIL must disassemble exactly as what the native compiler writes
(shader hash and ident string aside: BCD's tree carries the patch). No Wine runs otherwise.
"""
from pathlib import Path
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what)
    ok = ok and bool(cond)


# ---------------------------------------------------------------- the build script and the patch
build = (root / 'tools/build-dxcompiler.sh').read_text()
patch = (root / 'build/dxc-win/0001-mingw-w64-build.patch').read_text()
toolchain = (root / 'build/dxc-win/mingw-x86_64.cmake').read_text()
source = (root / 'build/dxc-win/BCD-SOURCE.txt').read_text()
check('the script pins DXC v1.9.2609 at commit 01b62ad4 and refuses any other commit',
      'DXC_TAG=v1.9.2609' in build and 'DXC_COMMIT=01b62ad47db7dee3dd33f90e7339cf9e860b3f93' in build
      and '[ "$(git -C "$SRC" rev-parse HEAD)" = "$DXC_COMMIT" ] || {' in build
      and 'DXC_URL=https://github.com/microsoft/DirectXShaderCompiler.git' in build)
check('it applies the one patch, and reuses a source tree only when it is that commit with that patch',
      'git -C "$SRC" apply "$PATCH"' in build and 'SRC_STAMP="$DXC_COMMIT $(shasum -a 256 "$PATCH" | cut -c1-64)"' in build)
check('the Windows build: llvm-mingw toolchain file, the host tablegen tools, SPIR-V, tests and the DXBC converter off, the runtime linked in',
      '-DCMAKE_TOOLCHAIN_FILE="$TOOLCHAIN"' in build and '-DLLVM_TABLEGEN="$WORK/host/bin/llvm-tblgen"' in build
      and '-DCLANG_TABLEGEN="$WORK/host/bin/clang-tblgen"' in build and '-DENABLE_SPIRV_CODEGEN=OFF' in build
      and '-DHLSL_INCLUDE_TESTS=OFF' in build and '-DHLSL_BUILD_DXILCONV=OFF' in build
      and '-DCMAKE_SHARED_LINKER_FLAGS="-static -Wl,--gc-sections"' in build and 'ninja -C "$WORK/x64" -j"$JOBS" dxcompiler' in build)
check('the toolchain file is x86_64-w64-mingw32 from MINGW_BIN',
      'set(CMAKE_SYSTEM_NAME Windows)' in toolchain and 'set(CMAKE_SYSTEM_PROCESSOR x86_64)' in toolchain
      and '"${MINGW_BIN}/x86_64-w64-mingw32-clang++"' in toolchain)
check('the compiler\'s __FILE__ paths are mapped away (-ffile-prefix-map for WORK and SRC), so the DLL names no build machine path',
      '-ffile-prefix-map=$WORK=build -ffile-prefix-map=$SRC=dxc' in build and '-DCMAKE_CXX_FLAGS="$MAPS"' in build
      and '-DCMAKE_C_FLAGS="$MAPS"' in build)
check('the result is stripped, checked, stamped, and shipped as dxcompiler-optin.dll (never as dxcompiler.dll)',
      '"$MINGW/x86_64-w64-mingw32-strip" -o "$OUT/dxcompiler.dll"' in build and 'python3 "$CHECK" "$OUT/dxcompiler.dll"' in build
      and 'echo "$STAMP" > "$OUT/stamp"' in build and '"$SHIP_DIR/dxcompiler-optin.dll"' in build
      and not re.search(r'SHIP_DIR/dxcompiler\.dll', build))
check('a current DLL in OUT is shipped without a build; no llvm-mingw ships nothing and exits 0',
      '[ "$(cat "$OUT/stamp" 2>/dev/null)" = "$STAMP" ]' in build and 'dxcompiler-optin.dll is not built"\n    exit 0' in build)
check("the patch credits Raed Rizqie's DXC pull request #8265 and says what BCD left out",
      'pull request #8265' in patch and 'Raed Rizqie' in patch and 'c1d55772d52b54a28c69f713964295d7a407da7f' in patch
      and 'StringRef.h' in patch.split('diff --git', 1)[0] and 'MS_SUPPORT_VARIABLE_LANGOPTS' in patch.split('diff --git', 1)[0])
check('the patch does not define MS_SUPPORT_VARIABLE_LANGOPTS (HLSL language options stay fixed) and gives MinGW the definitions',
      '+  add_definitions( -DMS_SUPPORT_VARIABLE_LANGOPTS )' not in patch
      and '+#if defined(LLVM_ON_UNIX) || defined(__MINGW32__)' in patch and 'tools/clang/lib/Basic/LangOptions.cpp' in patch)
check("the patch keeps MinGW-w64's own COM headers for the Windows build (the vtables a Windows program calls)",
      '+#include <unknwn.h>' in patch and '+#include <objidl.h>' in patch and '-#ifndef _WIN32\n+#ifndef _MSC_VER' in patch)
check("it does not touch StringRef.h's is_nothrow_constructible specialisations (v1.9.2609 skips them under libc++)",
      'is_nothrow_constructible' not in ''.join(l for l in patch.splitlines(True) if l.startswith(('+', '-'))))
dxc_src = os.environ.get('DXC_SRC')
if dxc_src and (Path(dxc_src) / 'include/dxc/dxcapi.h').exists():
    r = subprocess.run(['git', '-C', dxc_src, 'apply', '--check', str(root / 'build/dxc-win/0001-mingw-w64-build.patch')],
                       capture_output=True, text=True)
    check('the patch applies to the pristine tree in DXC_SRC', r.returncode == 0)
else:
    print('skip the patch-applies check (DXC_SRC names no pristine v1.9.2609 tree)')
check('BCD-SOURCE.txt names the version, the commit, the licence, the PR and that Microsoft\'s binaries are not used',
      'DXC 1.9.2609' in source and '01b62ad47db7dee3dd33f90e7339cf9e860b3f93' in source and 'NCSA' in source
      and 'pull request #8265 by Raed Rizqie' in source and 'NOT used: Microsoft\'s own release binaries' in source)

# ---------------------------------------------------------------- licences and notices
lic = root / 'LICENSES/DirectXShaderCompiler-LICENSE.txt'
tpn = root / 'LICENSES/DirectXShaderCompiler-ThirdPartyNotices.txt'
check("DXC's LICENSE.TXT and ThirdPartyNotices.txt are kept, and the app carries the same bytes",
      lic.exists() and tpn.exists() and b'University of Illinois/NCSA' in lic.read_bytes()
      and (root / 'app/Madeira/legal/LICENSES/DirectXShaderCompiler-LICENSE.txt').read_bytes() == lic.read_bytes()
      and (root / 'app/Madeira/legal/LICENSES/DirectXShaderCompiler-ThirdPartyNotices.txt').read_bytes() == tpn.read_bytes())
notices = (root / 'THIRD-PARTY-NOTICES.md').read_text()
short = (root / 'app/Madeira/licenses/THIRD-PARTY-NOTICES.txt').read_text()
check('THIRD-PARTY-NOTICES.md has DXC\'s row (licence, the patch and its author, the DLL, not Microsoft\'s binaries)',
      '| **DirectX Shader Compiler (DXC)** 1.9.2609' in notices and 'Raed Rizqie' in notices
      and '`arm64ec-windows/dxcompiler-optin.dll`' in notices and 'LICENSES/DirectXShaderCompiler-LICENSE.txt' in notices)
check('the shipped short notice names it', 'DirectX Shader Compiler 1.9.2609' in short and 'dxcompiler-optin.dll' in short)
gitignore = (root / '.gitignore').read_text()
check('the source, the build trees and the DLL are not committed',
      'build/dxc-win/work/\n' in gitignore and 'build/dxc-win/out/\n' in gitignore
      and 'app/Madeira/arm64ec-windows/dxcompiler-optin.dll\n' in gitignore)

# ---------------------------------------------------------------- the workflow
wf = (root / '.github/workflows/build-ipa.yml').read_text()
key = "dxc-win-x64-v1-${{ hashFiles('build/dxc-win/0001-mingw-w64-build.patch', 'build/dxc-win/mingw-x86_64.cmake', 'tools/build-dxcompiler.sh') }}"
i_restore = wf.find('- name: Restore dxcompiler.dll (DirectX Shader Compiler, x86-64)')
i_build = wf.find('- name: Build dxcompiler.dll and ship it as dxcompiler-optin.dll')
i_save = wf.find('- name: Save dxcompiler.dll')
i_verify = wf.find('- name: Verify the opt-in DLLs the lists and rules name')
i_mingw = wf.find('- name: Install ninja/meson and fetch llvm-mingw')
check('CI restores, builds (continue-on-error) and saves build/dxc-win/out after llvm-mingw and before the opt-in DLL check',
      -1 < i_mingw < i_restore < i_build < i_save < i_verify and wf.count(key) == 2
      and 'run: bash tools/build-dxcompiler.sh' in wf[i_build:i_save] and 'continue-on-error: true' in wf[i_build:i_save]
      and "if: env.MINGW_OK == '1'" in wf[i_build:i_save] and 'path: build/dxc-win/out' in wf[i_restore:i_build])
check('the cache key hashes the inputs only, never build/dxc-win/out or the work tree',
      "hashFiles('build/dxc-win/**" not in wf and 'build/dxc-win/work' not in wf)
check('CI runs this check', 'run: python3 tests/host/check-dxcompiler-optin.py' in wf)

# ---------------------------------------------------------------- the app: policy, bridge, catalog
policy = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
case = policy[policy.index('case "MADEIRA_OPTIN_DLLS":'):policy.index('case "MADEIRA_WG_VPROC":')]
check('a downloaded list may name dxcompiler', '|| $0 == "dxcompiler"' in case and 'coremessaging, combase, or dxcompiler, or mscms")' in case)
bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
blk = bridge[bridge.index('const char *optList = getenv("MADEIRA_OPTIN_DLLS");'):]
check('the bridge links any shipped <name>-optin.dll the game lists as <name>.dll in sysx64 (where an x64 program finds it) and '
      'removes only a link of ours otherwise (no code of its own for dxcompiler)',
      'hasSuffix:@"-optin.dll"' in blk and 'drive_c/windows/sysx64' in blk and '[to.lastPathComponent isEqualToString:file]' in blk
      and 'dxcompiler' not in bridge)
gen = (root / 'build/tools/gen-config-catalog.py').read_text()
opt = gen[gen.index('"env.MADEIRA_OPTIN_DLLS": {'):gen.index('"env.MADEIRA_WINTYPES": {')]
check('the catalog says what dxcompiler is and that auto-tune adds it', 'dxcompiler is not one of Wine\'s' in opt
      and '"tools/build-dxcompiler.sh"' in opt and 'combase, dxcompiler, mscms and (with' in opt)

# ---------------------------------------------------------------- auto-tune: the rule and its fixture
spec = importlib.util.spec_from_file_location('autotune_rules', root / 'build/tools/autotune_rules.py')
atr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(atr)
rules = {r['id']: r for r in atr.RULES}
new = rules.get('wgpu-fxc-sm51-dxcompiler', {})
old = rules.get('d3dcompile-sm51-resource-array', {})
check('wgpu-fxc-sm51-dxcompiler adds env.MADEIRA_OPTIN_DLLS = dxcompiler at once after FXC\'s E5017, and not again once linked',
      new.get('lines') == ['env.MADEIRA_OPTIN_DLLS = dxcompiler'] and new.get('noRetry') is True
      and new.get('all') == [{'has': ['FXC D3DCompile error', 'Shader model 5.1+ resource array']}]
      and new.get('none') == [{'has': ['MADEIRA_OPTIN_DLLS: dxcompiler.dll -> dxcompiler-optin.dll']}])
check('it comes before the noFix d3dcompile-sm51-resource-array, which stays for what is left',
      old.get('noFix') is True and old.get('lines') == [] and new.get('priority', 99) < old.get('priority', 0))
check('only this rule names dxcompiler', [r['id'] for r in atr.RULES if 'dxcompiler' in json.dumps(r.get('lines', []))]
      == ['wgpu-fxc-sm51-dxcompiler'])
generated = (root / 'app/Madeira/AutoTuneRules.generated.swift').read_text()
check('the generated rule table has it', '"wgpu-fxc-sm51-dxcompiler"' in generated and '"env.MADEIRA_OPTIN_DLLS = dxcompiler"' in generated)
fx = root / 'tests/fixtures/autotune'
exp = json.loads((fx / 'skate2-sm51-resource-array-2026-11-11.expect').read_text())
fxlog = (fx / 'skate2-sm51-resource-array-2026-11-11.log').read_text()
check('the fixture from Send Log JQ0202 gets the line first (wgpu-fxc-sm51-dxcompiler, then the noFix rule)',
      exp['next'] == ['env.MADEIRA_OPTIN_DLLS = dxcompiler'] and exp['matched'][:2] == ['wgpu-fxc-sm51-dxcompiler', 'd3dcompile-sm51-resource-array']
      and 'FXC D3DCompile error' in fxlog and 'E5017: Aborting due to not yet implemented feature: Shader model 5.1+ resource array.' in fxlog
      and '[build] v0.1.653 (653)' in fxlog)

# ---------------------------------------------------------------- the list
recs = json.loads((root / 'recommended/recommendations.json').read_text())
row = next((r for r in recs['rules'] if r['id'] == 'skate2-pc'), {})
lines = [c if isinstance(c, str) else c['line'] for c in row.get('config', [])]
check('skate. 2 PC port\'s row has the line from build 663 (the first build with the DLL)',
      row.get('minBuild') == 663 and 'env.MADEIRA_OPTIN_DLLS = dxcompiler' in lines and row.get('match') == {'programs': [{'name': 'skate2-pc.exe'}]})
named = [r['id'] for r in recs['rules'] if 'dxcompiler' in json.dumps(r.get('config', []) + r.get('smallRAMConfig', []))]
check('no other list names dxcompiler (every other game is as before)', named == ['skate2-pc'])

# ---------------------------------------------------------------- a built DLL, when there is one
dll = Path(os.environ.get('BCD_DXC_DLL') or (root / 'build/dxc-win/out/dxcompiler.dll'))
shipped = root / 'app/Madeira/arm64ec-windows/dxcompiler-optin.dll'
spec2 = importlib.util.spec_from_file_location('check_dxcompiler_shipped', root / 'tools/check-dxcompiler-shipped.py')
cds = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(cds)
for f in (dll, shipped):
    if f.exists():
        bad = cds.problems(f)
        check('%s: x86-64, DxcCreateInstance(2), only Wine\'s system DLLs and the UCRT imported, DXC 1.9 (%s)' % (f.name, bad or 'clean'), not bad)
    else:
        print('skip %s (not built here)' % f)
if dll.exists() and shipped.exists():
    check('the shipped dxcompiler-optin.dll is the built DLL', dll.read_bytes() == shipped.read_bytes())

# ---------------------------------------------------------------- the host run (optional)
wine = os.environ.get('BCD_HOST_WINE')
mingw = Path(os.environ.get('MINGW') or (root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'))
if wine and dll.exists() and (mingw / 'x86_64-w64-mingw32-clang').exists():
    probe = root / 'tests/host/dxc'
    work = Path(tempfile.mkdtemp(prefix='check-dxc-'))
    env = dict(os.environ, WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=')
    env.setdefault('WINEPREFIX', str(work / 'prefix'))
    for exe in ('dxc-probe', 'fxc-probe'):
        subprocess.run([str(mingw / 'x86_64-w64-mingw32-clang'), '-O1', '-o', str(work / (exe + '.exe')), str(probe / (exe + '.c'))], check=True)
    (work / 'dxcompiler.dll').write_bytes(dll.read_bytes())
    for name in ('naga-textured-wgpu.hlsl', 'sm51-array-test.hlsl'):
        (work / name).write_bytes((probe / name).read_bytes())

    def run(*args):
        r = subprocess.run([wine] + list(args), cwd=work, env=env, capture_output=True, text=True, errors='replace', timeout=600)
        return r.returncode, (r.stdout + r.stderr).replace('\r', '')

    rc, out = run('fxc-probe.exe', 'naga-textured-wgpu.hlsl', 'fs_main', 'ps_5_1')
    check("host Wine: FXC stops on naga's sampler heap exactly as in JQ0202 (12:14 E5017, 13:52 E5000/E5008)",
          rc != 0 and ':12:14: E5017: Aborting due to not yet implemented feature: Shader model 5.1+ resource array.' in out
          and ':13:52: E5008: Array size is not a positive integer constant.' in out)
    native = os.environ.get('BCD_NATIVE_DXC')
    for name, ep, target in (('naga-textured-wgpu.hlsl', 'fs_main', 'ps_6_6'), ('naga-textured-wgpu.hlsl', 'vs_main', 'vs_6_6'),
                             ('sm51-array-test.hlsl', 'main', 'ps_6_6')):
        outname = '%s-%s.dxil' % (name.split('.')[0], ep)
        rc, out = run('dxc-probe.exe', 'dxcompiler.dll', name, ep, target, outname)
        check('host Wine: DXC 1.9 (BCD\'s DLL, called as wgpu calls it) compiles %s %s %s to hashed DXIL' % (name, ep, target),
              rc == 0 and 'IDxcVersionInfo::GetVersion hr=0 -> 1.9' in out and 'status=0 ' in out and 'magic DXBC, digest set (hashed)' in out)
        if native and Path(native).exists():
            ref = work / ('ref-' + outname)
            subprocess.run([native, '-E', ep, '-T', target, '-HV', '2018', '-no-warnings', '-Ges', '-Fo', str(ref), name], cwd=work, check=True,
                           capture_output=True)

            def listing(obj):
                # the native compiler's disassembly without the two lines that differ by construction: the shader hash (it covers the
                # ident string) and the ident "dxc(private) 1.9.0.1 (01b62ad4)" (BCD's tree carries the patch: "(01b62ad4-dirty)")
                text = subprocess.run([native, '-dumpbin', str(obj)], cwd=work, capture_output=True, text=True).stdout
                return [l for l in text.splitlines() if not l.startswith('; shader hash:') and 'dxc(private)' not in l]
            a, b = listing(work / outname), listing(ref)
            check('  ... the same DXIL as DXC 1.9.2609 for the Mac writes (disassembly, shader hash and ident string aside)', a and a == b)
else:
    print('skip the host run (BCD_HOST_WINE names no wine, or no built DLL or llvm-mingw here)')

print('PASS: dxcompiler opt-in' if ok else 'FAILED: dxcompiler opt-in')
sys.exit(0 if ok else 1)
