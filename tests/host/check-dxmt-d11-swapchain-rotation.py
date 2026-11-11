#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT IDXGISwapChain1::SetRotation / GetRotation answer the identity rotation instead of aborting (night round 2026-11-11, G1 S13).

The Battle for Wesnoth (CF2N9T, build 634): SDL2's Direct3D 11 renderer calls SetRotation right after CreateSwapChain; DXMT's
`{ IMPLEMENT_ME; }` logged "src\\d3d11\\d3d11_swapchain.cpp:SetRotation is not implemented." and abort()ed (exit code 3).
tools/patch-dxmt-d11-swapchain-rotation.py answers S_OK for the identity ("unspecified" too) and DXGI_ERROR_INVALID_CALL for a real
rotation, GetRotation reports the identity (E_INVALIDARG for a null pointer). This check applies the patch to a copy of the pinned
dxmt/src (or $DXMT_SRC_DIR), reads the result, and compiles the two answers as plain C++ on the host against the DXGI enum values
(the DXMT sources themselves need the Windows toolchain). SKIP of the source part when no dxmt sources exist.
"""
import os, pathlib, re, shutil, subprocess, sys, tempfile


def pinned(p):
    """The file as the pinned DXMT commit has it. In a build the working copy already carries the workflow's DXMT patches (this one
    among them), so the patch's own tests (applies once, a moved anchor) are run on the pinned original (night round 2026-11-11)."""
    try:
        top = subprocess.run(['git', '-C', str(p.parent), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True).stdout.strip()
        rel = p.resolve().relative_to(pathlib.Path(top).resolve())
        text = subprocess.run(['git', '-C', top, 'show', 'HEAD:' + rel.as_posix()], capture_output=True, text=True, check=True).stdout
    except (OSError, ValueError, subprocess.CalledProcessError):
        return p
    q = pathlib.Path(tempfile.mkdtemp(prefix='pinned-')) / p.name
    q.write_text(text)
    return q

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        ok = False
        if detail:
            print('     ' + str(detail)[:1500])


script = R / 'tools/patch-dxmt-d11-swapchain-rotation.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it before the i386 farm key', 'python3 tools/patch-dxmt-d11-swapchain-rotation.py' in wf
      and wf.index('python3 tools/patch-dxmt-d11-swapchain-rotation.py') < wf.index('name: i386 farm revision'))
check('CI runs this check', 'python3 tests/host/check-dxmt-d11-swapchain-rotation.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if not (src / 'd3d11/d3d11_swapchain.cpp').exists():
    alt = pathlib.Path.home() / 'madeira-bcd/dxmt/src'
    src = alt if (alt / 'd3d11/d3d11_swapchain.cpp').exists() else src
target = pinned(src / 'd3d11/d3d11_swapchain.cpp')
if target.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d11').mkdir(parents=True)
        shutil.copy(target, t / 'dxmt/src/d3d11/d3d11_swapchain.cpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned source and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        s = (t / 'dxmt/src/d3d11/d3d11_swapchain.cpp').read_text()
        a = s.index('SetRotation(DXGI_MODE_ROTATION Rotation) final {')
        b = s.index('HRESULT STDMETHODCALLTYPE SetSourceSize(UINT width, UINT height)')
        body = s[a:b]
        check('neither method calls IMPLEMENT_ME any more; the other stubs are untouched',
              '{ IMPLEMENT_ME; }' not in body and 'IMPLEMENT_ME;' not in body
              and s.count('IMPLEMENT_ME;') == target.read_text().count('IMPLEMENT_ME;') - 2)
        check('the first four SetRotation calls are logged (the marker the 64-bit DLL is checked for)',
              '++madeira_rotation_calls <= 4' in body and '[swapchain-rotation] madeira-bcd: SetRotation(' in body)
        check('<atomic> is included for the counter', '#include <atomic>' in s)
        # compile the two answers on the host: copy the method bodies into a tiny class with the DXGI constants
        set_body = body[body.index('{', body.index('SetRotation(')): body.index('GetRotation(')]
        set_body = set_body[:set_body.rindex('};') + 2] if '};' in set_body else set_body
        get_body = body[body.index('{', body.index('GetRotation(')):]
        prog = r'''
#include <atomic>
#include <cstdio>
#include <sstream>
#include <string>
typedef long HRESULT; typedef unsigned UINT;
enum DXGI_MODE_ROTATION { DXGI_MODE_ROTATION_UNSPECIFIED = 0, DXGI_MODE_ROTATION_IDENTITY = 1, DXGI_MODE_ROTATION_ROTATE90 = 2,
                          DXGI_MODE_ROTATION_ROTATE180 = 3, DXGI_MODE_ROTATION_ROTATE270 = 4 };
const HRESULT S_OK = 0, E_INVALIDARG = (HRESULT)0x80070057L, DXGI_ERROR_INVALID_CALL = (HRESULT)0x887A0001L;
struct Logger { static void info(const std::string &m) { std::printf("log: %s\n", m.c_str()); } };
namespace str { template <typename... A> std::string format(const A &...a) { std::stringstream s; (s << ... << a); return s.str(); } }
struct SwapChain {
  HRESULT SetRotation(DXGI_MODE_ROTATION Rotation) ''' + set_body.rstrip().rstrip(';') + r'''
  HRESULT GetRotation(DXGI_MODE_ROTATION *pRotation) ''' + get_body.rstrip().rstrip(';') + r'''
};
int main() {
  SwapChain c; DXGI_MODE_ROTATION r = DXGI_MODE_ROTATION_ROTATE90;
  std::printf("set0=%ld set1=%ld set2=%ld set3=%ld set4=%ld\n", (long)c.SetRotation(DXGI_MODE_ROTATION_UNSPECIFIED), (long)c.SetRotation(DXGI_MODE_ROTATION_IDENTITY),
              (long)c.SetRotation(DXGI_MODE_ROTATION_ROTATE90) == (long)DXGI_ERROR_INVALID_CALL, (long)c.SetRotation(DXGI_MODE_ROTATION_ROTATE180) == (long)DXGI_ERROR_INVALID_CALL,
              (long)c.SetRotation(DXGI_MODE_ROTATION_ROTATE270) == (long)DXGI_ERROR_INVALID_CALL);
  std::printf("get=%ld rot=%d null=%d\n", (long)c.GetRotation(&r), (int)r, (long)c.GetRotation(nullptr) == (long)E_INVALIDARG);
  return 0;
}
'''
        cc = shutil.which('clang++') or shutil.which('c++')
        (t / 'rot.cpp').write_text(prog)
        b2 = subprocess.run([cc, '-std=c++20', '-o', str(t / 'rot'), str(t / 'rot.cpp')], capture_output=True, text=True) if cc else None
        if b2 is None or b2.returncode != 0:
            check('the two methods compile on the host', False, (b2.stderr if b2 else 'no C++ compiler'))
        else:
            run = subprocess.run([str(t / 'rot')], capture_output=True, text=True).stdout
            check('SetRotation: S_OK for unspecified and identity, DXGI_ERROR_INVALID_CALL for 90/180/270; the first four calls are logged',
                  'set0=0 set1=0 set2=1 set3=1 set4=1' in run and run.count('log: [swapchain-rotation] madeira-bcd: SetRotation(') == 4, run)
            check('GetRotation: the identity (1), S_OK; a null pointer is E_INVALIDARG', 'get=0 rot=1 null=1' in run, run)
        moved = t / 'moved'
        (moved / 'dxmt/src/d3d11').mkdir(parents=True)
        mf = moved / 'dxmt/src/d3d11/d3d11_swapchain.cpp'
        mf.write_text(target.read_text().replace('GetRotation(DXGI_MODE_ROTATION *pRotation) final { IMPLEMENT_ME; };',
                                                 'GetRotation(DXGI_MODE_ROTATION *pRotation) final { return S_OK; };'))
        r3 = subprocess.run([sys.executable, str(script)], cwd=moved, capture_output=True, text=True)
        check('a moved anchor refuses the patch and leaves the file alone',
              r3.returncode != 0 and 'not found once each' in r3.stderr and 'madeira-bcd: d11-swapchain-rotation' not in mf.read_text(), r3.stderr)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll is built with the patch and refused without it (a 64-bit game takes it with env.MADEIRA_D3D11_SRC = 1)',
      '"patch-dxmt-d11-swapchain-rotation.py" "[swapchain-rotation] madeira-bcd: SetRotation("' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries the patch',
      '"[swapchain-rotation] madeira-bcd: SetRotation("' in wf[wf.index('for m in "[copy-structure-count] skipped #"'):][:700])
rules = (R / 'build/tools/autotune_rules.py').read_text()
blk = rules[rules.index('"id": "dxmt-d3d11-swapchain-rotation-abort"'):] if '"id": "dxmt-d3d11-swapchain-rotation-abort"' in rules else ''
blk = blk[:blk.index('\n    },\n')] if blk else ''
check('auto-tune: the rule names the abort and the exit code, asks for d3d11-src.dll, and stops when it was already in use',
      '"lines": ["env.MADEIRA_D3D11_SRC = 1"]' in blk and 'SetRotation is not implemented' in blk
      and 'NtTerminateProcess(handle=0x0, exit_code=0x3)' in blk and 'd3d11.dll -> d3d11-src.dll' in blk[blk.index('"none": ['):][:200], blk[:300])
exp = R / 'tests/fixtures/autotune/wesnoth-d3d11-swapchain-rotation-2026-11-10.expect'
check('its fixture is there and proposes that line', exp.is_file() and 'dxmt-d3d11-swapchain-rotation-abort' in exp.read_text()
      and 'env.MADEIRA_D3D11_SRC = 1' in exp.read_text())
print('PASS: DXMT swap chain rotation' if ok else 'FAILED: DXMT swap chain rotation')
sys.exit(0 if ok else 1)
