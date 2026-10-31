#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Godot 4's composition swap chain, for the game that asks (round 624, agent E2 S2b; Slay the Spire 2 BMVD7C and 20 more logs).

Godot 4.5's Direct3D 12 driver creates its swap chain with IDXGIFactory2::CreateSwapChainForComposition and then gives it to a
DirectComposition target of its window. DXMT's factory answered "Not implemented" and Wine's dcomp.dll is a stub, so the game stopped
at "swap_chain_resize ... ERR_CANT_CREATE". tools/patch-dxgi-composition.py serves the swap chain on the game's window in dxgi-src.dll
under env.MADEIRA_DXGI_COMPOSITION = 1 (unset: today's answer, word for word), and tools/patch-wine-dcomp-null.py makes dcomp-optin.dll
a device that accepts the composition calls and draws nothing (env.MADEIRA_OPTIN_DLLS = dcomp).

Checks: the DXGI patch on a copy of DXMT's factory after the other overlays (idempotent; the off path unchanged); its window choice,
compiled on the host with fake Win32 calls (the thread's largest visible window; the foreground window only when this process owns it;
nothing otherwise) and the exact-1 switch; the dcomp patch on a copy of Wine's device.c (the file's header kept word for word,
idempotent, a device.c it does not know refused) and, when Wine's tools and llvm-mingw are present, compiled with Wine's own arm64ec
flags; the verifier on the shipped dcomp.dll; the build scripts, the policy, the catalog, the list and the auto-tune rule.
"""
from pathlib import Path
import importlib.util, json, os, re, shutil, subprocess, sys, tempfile

root = Path(__file__).resolve().parents[2]
ok = True


def check(what, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + what + (('\n     ' + str(detail)) if (detail and not cond) else ''))
    ok = ok and bool(cond)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dx = load('dxgi_comp', root / 'tools/patch-dxgi-composition.py')
dc = load('dcomp_null', root / 'tools/patch-wine-dcomp-null.py')
dxmt = Path(os.environ.get('DXMT_SRC', Path.home() / 'madeira-bcd/dxmt'))
wine = Path(os.environ.get('WINE_SRC', Path.home() / 'madeira-bcd/wine'))

# ---- DXGI: the patch on DXMT's factory --------------------------------------------------------------------------------
fac = dxmt / 'src/dxgi/dxgi_factory.cpp'
if fac.exists():
    with tempfile.TemporaryDirectory() as t:
        f = Path(t) / 'dxgi_factory.cpp'
        shutil.copy(fac, f)
        for pre in ('patch-dxgi-factory7.py', 'patch-dxgi-x64-entry.py'):
            subprocess.run([sys.executable, str(root / 'tools' / pre), str(f)], capture_output=True, text=True)
        r1 = subprocess.run([sys.executable, str(root / 'tools/patch-dxgi-composition.py'), str(f)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(root / 'tools/patch-dxgi-composition.py'), str(f)], capture_output=True, text=True)
        out = f.read_text()
        check('the DXGI patch applies after the other overlays and a second run changes nothing',
              'patched' in r1.stdout and 'already patched' in r2.stdout, (r1.stdout + r1.stderr + r2.stdout)[-400:])
        meth = out[out.index('HRESULT STDMETHODCALLTYPE CreateSwapChainForComposition('):]
        meth = meth[:meth.index('\n  }\n') + 5]
        check('without the switch the method answers what it did ("Not implemented", E_NOTIMPL), before anything else runs',
              'if (!madeira_composition_wanted()) {\n      ERR("Not implemented");\n      return E_NOTIMPL;\n    }' in meth
              and meth.index('madeira_composition_wanted()') < meth.index('madeira_composition_window()'))
        check('with it: the window, alpha ignored, stretch scaling, CreateSwapChainForHwnd, one line in the log',
              'madeira_desc.AlphaMode = DXGI_ALPHA_MODE_IGNORE;' in meth and 'madeira_desc.Scaling = DXGI_SCALING_STRETCH;' in meth
              and 'CreateSwapChainForHwnd(pDevice, madeira_window, &madeira_desc, nullptr, pRestrictToOutput, ppSwapChain)' in meth
              and '"[dxgi-composition] madeira-bcd: composition swap chain "' in meth
              and out.index('static bool madeira_composition_wanted()') < out.index('class MTLDXGIFactory'))
else:
    print('skip the DXGI patch run: no DXMT tree (set DXMT_SRC)')

HOST = r'''
#include <cstdio>
#include <cstring>
#include <cstdint>
typedef void *HWND; typedef int BOOL; typedef long LONG; typedef long long LONGLONG; typedef intptr_t LPARAM; typedef unsigned long DWORD;
#define TRUE 1
#define FALSE 0
#define CALLBACK
struct RECT { LONG left, top, right, bottom; };
typedef BOOL (CALLBACK *WNDENUMPROC)(HWND, LPARAM);
struct FakeWin { HWND h; int visible; LONG w, h2; };
static FakeWin g_wins[8]; static int g_n; static HWND g_fg; static DWORD g_fg_pid; static const char *g_env;
static BOOL IsWindowVisible(HWND h) { for (int i = 0; i < g_n; i++) if (g_wins[i].h == h) return g_wins[i].visible; return FALSE; }
static BOOL GetClientRect(HWND h, RECT *r) { for (int i = 0; i < g_n; i++) if (g_wins[i].h == h) { r->left = 0; r->top = 0; r->right = g_wins[i].w; r->bottom = g_wins[i].h2; return TRUE; } return FALSE; }
static BOOL EnumThreadWindows(DWORD, WNDENUMPROC f, LPARAM p) { for (int i = 0; i < g_n; i++) if (!f(g_wins[i].h, p)) break; return TRUE; }
static DWORD GetCurrentThreadId() { return 7; }
static HWND GetForegroundWindow() { return g_fg; }
static DWORD GetWindowThreadProcessId(HWND, DWORD *pid) { *pid = g_fg_pid; return 1; }
static DWORD GetCurrentProcessId() { return 42; }
static DWORD GetEnvironmentVariableA(const char *, char *v, DWORD n) { if (!g_env) return 0; strncpy(v, g_env, n); return (DWORD)strlen(g_env); }
@HELPERS@
static void reset() { g_n = 0; g_fg = nullptr; g_fg_pid = 0; }
int main(int argc, char **argv) {
  if (argc > 1) { g_env = argv[1][0] ? argv[1] : nullptr; printf("wanted %d\n", madeira_composition_wanted() ? 1 : 0); return 0; }
  reset(); g_wins[g_n++] = {(HWND)0x10, 1, 200, 100}; g_wins[g_n++] = {(HWND)0x20, 1, 1916, 1050}; g_wins[g_n++] = {(HWND)0x30, 0, 4000, 4000};
  printf("largest %p\n", madeira_composition_window());
  reset(); g_fg = (HWND)0x99; g_fg_pid = 42; printf("foreground-own %p\n", madeira_composition_window());
  reset(); g_fg = (HWND)0x99; g_fg_pid = 5; printf("foreground-other %p\n", madeira_composition_window());
  reset(); g_wins[g_n++] = {(HWND)0x40, 0, 800, 600}; printf("hidden-only %p\n", madeira_composition_window());
  return 0;
}
'''
cxx = shutil.which('c++')
if cxx:
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / 'h.cpp').write_text(HOST.replace('@HELPERS@', dx.HELPERS))
        b = subprocess.run([cxx, '-std=c++17', '-w', '-o', str(t / 'h'), str(t / 'h.cpp')], capture_output=True, text=True)
        check('the window choice and the switch compile on the host (fake Win32 calls)', b.returncode == 0, b.stderr[-1200:])
        if b.returncode == 0:
            res = dict(l.split(' ', 1) for l in subprocess.run([str(t / 'h')], capture_output=True, text=True).stdout.splitlines())
            check('the thread\'s visible window with the largest client area is chosen (a bigger hidden one is not)',
                  res.get('largest') in ('0x20',), res)
            check('...else the foreground window when this process owns it; not another process\'s; a hidden window alone is nothing',
                  res.get('foreground-own') == '0x99' and res.get('foreground-other') in ('0x0', '(nil)') and res.get('hidden-only') in ('0x0', '(nil)'), res)
            vals = {v: subprocess.run([str(t / 'h'), v], capture_output=True, text=True).stdout.strip() for v in ('1', '0', '', '11', 'yes')}
            check('only the exact value 1 turns it on', vals == {'1': 'wanted 1', '0': 'wanted 0', '': 'wanted 0', '11': 'wanted 0', 'yes': 'wanted 0'}, vals)

# ---- dcomp: the null compositor ---------------------------------------------------------------------------------------
dev = wine / 'dlls/dcomp/device.c'
if dev.exists():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / 'dlls/dcomp').mkdir(parents=True)
        shutil.copy(dev, t / 'dlls/dcomp/device.c')
        orig = dev.read_text()
        r1 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-dcomp-null.py'), str(t)], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-dcomp-null.py'), str(t)], capture_output=True, text=True)
        new = (t / 'dlls/dcomp/device.c').read_text()
        check('the dcomp patch applies to Wine\'s stub, keeps the file\'s header (copyright, licence) word for word, and runs once',
              'patched' in r1.stdout and 'already patched' in r2.stdout and new.startswith(orig[:orig.index('#include <stdarg.h>')]))
        (t / 'odd').mkdir()
        (t / 'odd/dlls/dcomp').mkdir(parents=True)
        (t / 'odd/dlls/dcomp/device.c').write_text(orig.replace('return E_NOTIMPL;', 'return S_OK;', 1))
        r3 = subprocess.run([sys.executable, str(root / 'tools/patch-wine-dcomp-null.py'), str(t / 'odd')], capture_output=True, text=True)
        check('...and refuses a device.c it does not know', r3.returncode == 1 and 'is not the stub' in r3.stdout, r3.stdout[-300:])
        body = new[new.index('HRESULT WINAPI DCompositionCreateDevice(IDXGIDevice *dxgi_device'):]
        check('DCompositionCreateDevice gives an IDCompositionDevice (or IUnknown) and nothing else; Device2/3 stay Wine\'s stubs',
              'if (!IsEqualGUID(iid, &null_IID_IDCompositionDevice) && !IsEqualGUID(iid, &null_IID_IUnknown))' in body
              and new.count('return E_NOTIMPL;') >= 3 and 'DCompositionCreateDevice2(IUnknown *rendering_device' in new)
        check('the calls Godot makes succeed (CreateTargetForHwnd, CreateVisual, SetContent, SetRoot, Commit); the other factories say E_NOTIMPL',
              'null_device_CreateTargetForHwnd' in new and 'null_device_CreateVisual' in new and 'null_visual_SetContent' in new
              and 'null_target_SetRoot' in new and 'null_device_Commit' in new and 'NULL_DEVICE_NOTIMPL(CreateSurface,' in new)
        mingw = Path(os.environ.get('MINGW', root / 'toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'))
        if not mingw.exists():
            mingw = Path.home() / 'madeira-bcd/toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin'
        widl = wine / 'build-native/tools/widl/widl'
        bdir = wine / 'build-arm64ec'
        if (mingw / 'arm64ec-w64-mingw32-clang').exists() and widl.exists() and (bdir / 'include/dxgi.h').exists():
            inc = t / 'inc'
            inc.mkdir()
            for h in ('dcomp', 'dcompanimation', 'dcomptypes'):
                subprocess.run([str(widl), '-o', str(inc / (h + '.h')), '-m64', '--nostdinc', '-Ldlls/*', '-Iinclude', '-I' + str(wine / 'include'),
                                '-D__WINESRC__', str(wine / 'include' / (h + '.idl'))], cwd=bdir, capture_output=True, text=True)
            flags = ['-D__STDC__', '-c', '-o', str(t / 'device.o'), str(t / 'dlls/dcomp/device.c'), '-I' + str(inc), '-Iinclude',
                     '-I' + str(wine / 'include'), '-I' + str(wine / 'include/msvcrt'), '-D_UCRT', '-D__WINESRC__', '-D__WINE_PE_BUILD', '-Wall',
                     '-target', 'arm64ec-windows', '--no-default-config', '-fno-strict-aliasing', '-Wdeclaration-after-statement',
                     '-Wempty-body', '-Wignored-qualifiers', '-Winit-self', '-Wno-microsoft-enum-forward-reference', '-Wstrict-prototypes',
                     '-Wtype-limits', '-Wunused-but-set-parameter', '-Wvla', '-Wwrite-strings', '-Wpointer-arith', '-Wabsolute-value',
                     '-ffunction-sections', '-O2']
            c = subprocess.run([str(mingw / 'arm64ec-w64-mingw32-clang')] + flags, cwd=bdir, capture_output=True, text=True)
            check('the null compositor compiles with Wine\'s own arm64ec flags without a warning (every method matches its vtable slot)',
                  c.returncode == 0 and 'warning' not in c.stderr, c.stderr[-1500:])
        else:
            print('skip the arm64ec compile: no llvm-mingw, widl or configured wine/build-arm64ec')
else:
    print('skip the dcomp patch run: no Wine tree (set WINE_SRC)')

shipped = root / 'app/Madeira/arm64ec-windows/dcomp.dll'
if shipped.exists():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        shutil.copy(shipped, t / 'plain.dll')
        (t / 'marked.dll').write_bytes(shipped.read_bytes() + dc.SAY.encode() + b'\0')
        rp = subprocess.run([sys.executable, str(root / 'tools/patch-wine-dcomp-null.py'), '--verify', str(t / 'plain.dll'), str(shipped)], capture_output=True, text=True)
        rm = subprocess.run([sys.executable, str(root / 'tools/patch-wine-dcomp-null.py'), '--verify', str(t / 'marked.dll'), str(shipped)], capture_output=True, text=True)
        check('the verifier refuses Wine\'s stub and takes the same exports with the null compositor\'s marker',
              rp.returncode != 0 and 'no ' in rp.stderr and rm.returncode == 0, (rp.stderr + rm.stdout + rm.stderr)[-500:])

# ---- the build, the policy, the catalog, the list, the rule ----------------------------------------------------------
sh = (root / 'tools/build-wine-extra-dlls.sh').read_text()
blk = sh[sh.index('# madeira-bcd: dcomp-optin.dll (see DCO above)'):]
blk = blk[:blk.index('\nfi\n') + 4]
check('the extra-DLL build: its switch, the headers made first, then the DLL; stripped, padded, verified, shipped next to dcomp.dll; restored',
      'DCO=dcomp' in sh and '$SDL$DCO' in sh and 'make -C "$B" include/dcomp.h include/dcompanimation.h include/dcomptypes.h' in blk
      and blk.index('include/dcomp.h') < blk.index('dlls/dcomp/arm64ec-windows/dcomp.dll >> "$B.dcomp.log"')
      and 'python3 "$R/tools/patch-wine-dcomp-null.py" --verify "$dc_tmp" "$SHIP/dcomp.dll"' in blk
      and 'mv "$dc_tmp" "$SHIP/dcomp-optin.dll"' in blk and 'git -C "$R/wine" checkout -- dlls/dcomp/device.c' in blk)
bd = (root / 'tools/build-dxgi-dll.sh').read_text()
check('dxgi-src.dll\'s build applies it after the other factory overlays, tolerates a missed anchor, and checks the result',
      bd.index('patch-dxgi-x64-entry.py') < bd.index('patch-dxgi-composition.py') and 'COMPOSITION=""' in bd
      and 'LC_ALL=C grep -aq "MADEIRA_DXGI_COMPOSITION" "$OUT/dxgi.dll"' in bd)
pol = (root / 'app/Madeira/GameRecommendationsList.swift').read_text()
check('a list may set the switch (0 or 1) and name dcomp', '"MADEIRA_DXGI_COMPOSITION",' in pol and 'case "MADEIRA_DXGI_COMPOSITION":' in pol
      and '|| $0 == "kernelbase" || $0 == "dcomp" }) else {' in pol)
cat = (root / 'app/Madeira/ConfigCatalog.generated.swift').read_text()
check('the catalog documents the switch (off by default)', re.search(r'key: "env\.MADEIRA_DXGI_COMPOSITION"[^\n]*kind: \.bool, defaultValue: "0"', cat) is not None)
recs = json.loads((root / 'recommended/recommendations.json').read_text())
sts = next(r for r in recs['rules'] if r['id'] == 'slay-the-spire-2')
want = [{'line': 'env.MADEIRA_DXGI_SRC = 1', 'minBuild': 624}, {'line': 'env.MADEIRA_DXGI_COMPOSITION = 1', 'minBuild': 624},
        {'line': 'env.MADEIRA_OPTIN_DLLS = dcomp', 'minBuild': 624}]
check('Slay the Spire 2\'s list (v3, v4 since round 625) gives the three from build 624', sts['version'] == 4 and all(w in sts['config'] for w in want))
rules = (root / 'build/tools/autotune_rules.py').read_text()
r = rules[rules.index('"id": "godot-d3d12-composition-swapchain"'):]
r = r[:r.index('\n    },\n')]
check('auto-tune gives the three after the refused composition swap chain and stops once one was served',
      '"lines": ["env.MADEIRA_DXGI_SRC = 1", "env.MADEIRA_DXGI_COMPOSITION = 1", "env.MADEIRA_OPTIN_DLLS = dcomp"]' in r
      and 's("at: swap_chain_resize (drivers/d3d12/rendering_device_driver_d3d12.cpp:")' in r
      and 's("[dxgi-composition] madeira-bcd: composition swap chain")' in r)
exp = json.loads((root / 'tests/fixtures/autotune/slay-the-spire-2-composition-swapchain-2026-10-29.expect').read_text())
check('BMVD7C\'s fixture: trying the three', exp['status'] == 'trying' and exp['matched'] == ['godot-d3d12-composition-swapchain']
      and exp['next'] == [w['line'] for w in want], exp)
wf = (root / '.github/workflows/build-ipa.yml').read_text()
check('CI runs this check', 'python3 tests/host/check-godot-composition.py' in wf)
print('PASS: Godot composition' if ok else 'FAIL: Godot composition')
raise SystemExit(0 if ok else 1)
