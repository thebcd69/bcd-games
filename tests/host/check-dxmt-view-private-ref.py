#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT views hold their resource with a PRIVATE reference when env.MADEIRA_VIEW_PRIVATE_REF = 1 (night round 2026-11-11, G5 S15).

Hatred (Unreal Engine 4.10, PKJ7ZW, build 630) stops itself with "D3D11Viewport.cpp:(198): BackBuffer->GetResource() has 3 refs, expected
1": DXMT's RTV and SRV keep a PUBLIC `Com<RESOURCE_IMPL> resource;`, which raises the count AddRef/Release report. Windows' views (and
DXVK's, Com<T, false>) do not count. tools/patch-dxmt-view-private-ref.py (opt-in, default off) turns the reference the view just took
into a private one in the constructor and back before the member lets go in the destructor. This check applies it to a copy of the pinned
d3d11_resource.hpp (or $DXMT_SRC_DIR), reads the result, and runs the constructor and destructor bodies it added as plain C++ on the host
against DXMT's real com_object.hpp / com_pointer.hpp: with the key on a view leaves the resource's public count where it was (the game's one
reference), the resource stays alive when the game lets go before the view does and dies with the last view, and the log line shows the
counts; with the key off (the default) the counts are what they are today. SKIP of the source part when no dxmt sources exist.
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


script = R / 'tools/patch-dxmt-view-private-ref.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it before the i386 farm key', 'python3 tools/patch-dxmt-view-private-ref.py' in wf
      and wf.index('python3 tools/patch-dxmt-view-private-ref.py') < wf.index('name: i386 farm revision'))
check('CI runs this check', 'python3 tests/host/check-dxmt-view-private-ref.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if not (src / 'd3d11/d3d11_resource.hpp').exists():
    alt = pathlib.Path.home() / 'madeira-bcd/dxmt/src'
    src = alt if (alt / 'd3d11/d3d11_resource.hpp').exists() else src
target = pinned(src / 'd3d11/d3d11_resource.hpp')
if target.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d11').mkdir(parents=True)
        shutil.copy(target, t / 'dxmt/src/d3d11/d3d11_resource.hpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        check('it applies to the pinned header and a second run says "already patched"',
              r1.returncode == 0 and 'patched' in r1.stdout and 'already patched' in r2.stdout, r1.stdout + r1.stderr + r2.stdout)
        h = (t / 'dxmt/src/d3d11/d3d11_resource.hpp').read_text()
        cls = h[h.index('class TResourceViewBase'):h.index('#pragma region Resource Factory')]
        check('the key is read once, off unless set to something other than 0 (env.MADEIRA_VIEW_PRIVATE_REF)',
              'env::getEnvVar("MADEIRA_VIEW_PRIVATE_REF")' in h and '!env_v.empty() && env_v != "0"' in h and 'static const bool on = []' in h)
        check('the constructor does the dance only for a resource class that has the private calls, only with the key, after the description is kept',
              cls.index('desc = *pDesc;') < cls.index('MadeiraViewPrivateRefOn() && resource') and 'if constexpr (requires(typename tag::RESOURCE_IMPL *r)' in cls
              and cls.index('resource->AddRefPrivate();') < cls.index('resource->Release();') < cls.index('priv_ = true;'))
        check('the destructor gives the public reference back (AddRef, then ReleasePrivate) before the Com<> member releases it, only when it was taken',
              cls.index('if (priv_) {') < cls.index('resource->AddRef();') < cls.index('resource->ReleasePrivate();')
              and cls.index('Com<typename tag::RESOURCE_IMPL> resource;') < cls.index('bool priv_ = false;'))
        check('the first six views are logged ([view-ref], the marker the 64-bit DLL is checked for) with the public count before -> after and the private count',
              'MadeiraViewRefLogCount()' in cls and 'if (n <= 6)' in cls and '"[view-ref] madeira-bcd #"' in cls and 'public_after + 1, "->"' in cls)
        check('the resource member is still the public Com<> it was (nothing changes without the key)',
              'Com<typename tag::RESOURCE_IMPL> resource;' in cls and 'Com<typename tag::RESOURCE_IMPL, false>' not in cls)
        # --- the host model: the bodies the patch added, against the real Com / ComObject
        a = cls.index('/* madeira-bcd: views hold their resource privately: the public reference')
        ctor = cls[a:cls.index('~TResourceViewBase()')].rstrip()
        ctor = ctor[:ctor.rindex('}')]            # the constructor's own closing brace
        d0 = cls.index('~TResourceViewBase() {')
        dtor = cls[d0 + len('~TResourceViewBase() {'):cls.index('\n  }\n', d0)]
        prog = r'''
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <sstream>
#include <type_traits>
#include "com/com_object.hpp"
#include "com/com_pointer.hpp"
using namespace dxmt;
namespace env { std::string getEnvVar(const char *n) { const char *v = getenv(n); return v ? v : ""; } }
namespace str { template <typename... A> std::string format(const A &...a) { std::stringstream s; (s << ... << a); return s.str(); } }
#define ERR(...) std::printf("ERR %s\n", str::format(__VA_ARGS__).c_str())
struct ID3D11ShaderResourceView {}; struct ID3D11UnorderedAccessView {}; struct ID3D11RenderTargetView {}; struct ID3D11DepthStencilView {};
static bool g_alive = true;
struct Resource : ComObject<IUnknown> {
  HRESULT QueryInterface(REFIID, void **) override { return 0; }
  ULONG AddRef() override { return ComObject<IUnknown>::AddRef(); }
  ULONG Release() override { return ComObject<IUnknown>::Release(); }
  ~Resource() override { g_alive = false; }
  ULONG pub() { return m_refCount.load(); }
  ULONG priv() { return m_refPrivate.load(); }
};
struct Plain : IUnknown {   // a resource class without the private calls (a view on a plain ID3D11Resource): left alone
  std::atomic<unsigned> c{0};
  HRESULT QueryInterface(REFIID, void **) override { return 0; }
  ULONG AddRef() override { return ++c; }
  ULONG Release() override { return --c; }
};
inline bool MadeiraViewPrivateRefOn() {
  static const bool on = [] { const std::string env_v = env::getEnvVar("MADEIRA_VIEW_PRIVATE_REF"); return !env_v.empty() && env_v != "0"; }();
  return on;
}
inline unsigned MadeiraViewRefLogCount() { static std::atomic<unsigned> logged{0}; return ++logged; }
template <typename COMT, typename IMPL> struct tag { using COM = COMT; using RESOURCE_IMPL = IMPL; };
template <typename TAG> struct View {
  using tag = TAG;
  View(typename TAG::RESOURCE_IMPL *pResource) : resource(pResource) {
    ''' + ctor + r'''
  }
  ~View() {''' + dtor + r'''}
  Com<typename TAG::RESOURCE_IMPL> resource;
  bool priv_ = false;
};
int main() {
  Resource *res = new Resource;
  res->AddRef();                                               // the game's GetBuffer reference
  std::printf("start: public=%u private=%u\n", res->pub(), res->priv());
  {
    View<tag<ID3D11RenderTargetView, Resource>> rtv(res);
    View<tag<ID3D11ShaderResourceView, Resource>> srv(res);
    std::printf("two views: public=%u private=%u\n", res->pub(), res->priv());
    res->Release();                                            // the game lets go of its reference while the views live on
    std::printf("game released: public=%u private=%u alive=%d\n", res->pub(), res->priv(), (int)g_alive);
  }
  std::printf("views gone: alive=%d\n", (int)g_alive);
  Plain plain; plain.AddRef();
  { View<tag<ID3D11UnorderedAccessView, Plain>> uav(&plain); std::printf("plain: public=%u\n", plain.c.load()); }
  std::printf("plain after: public=%u\n", plain.c.load());
  return 0;
}
'''
        cc = shutil.which('clang++') or shutil.which('c++')
        (t / 'v.cpp').write_text(prog)
        inc = [f'-I{src / "util"}', f'-I{src / "util/com"}', f'-I{src.parent / "include/native/windows"}', f'-I{pathlib.Path.home() / "madeira-bcd/dxmt/include/native/windows"}']
        b = subprocess.run([cc, '-std=c++20', '-w', '-DNDEBUG'] + inc + ['-o', str(t / 'v'), str(t / 'v.cpp')], capture_output=True, text=True) if cc else None
        if b is None or b.returncode != 0:
            print('skip: the lifetime model did not build on this host: ' + ((b.stderr.strip().splitlines() or ['no C++ compiler'])[-1][:200] if b else 'no C++ compiler'))
        else:
            off = subprocess.run([str(t / 'v')], capture_output=True, text=True, env={**os.environ, 'MADEIRA_VIEW_PRIVATE_REF': '0'}).stdout
            on = subprocess.run([str(t / 'v')], capture_output=True, text=True, env={**os.environ, 'MADEIRA_VIEW_PRIVATE_REF': '1'}).stdout
            unset = subprocess.run([str(t / 'v')], capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != 'MADEIRA_VIEW_PRIVATE_REF'}).stdout
            check('key off (0 and unset): every view adds a public reference, as today, and nothing is logged',
                  'two views: public=3 private=1' in off and 'game released: public=2 private=1 alive=1' in off and 'views gone: alive=0' in off
                  and off == unset and 'ERR' not in off, off)
            check('key on: two views leave the public count at the game\'s one reference (Unreal\'s check reads 1), the private count is 3',
                  'start: public=1 private=1' in on and 'two views: public=1 private=3' in on, on)
            check('key on: the resource outlives the game\'s own Release while a view holds it, and dies with the last view',
                  'game released: public=0 private=2 alive=1' in on and 'views gone: alive=0' in on, on)
            check('key on: a view on a resource class without the private calls is left alone',
                  'plain: public=2' in on and 'plain after: public=1' in on, on)
            check('key on: the first views are logged with the public count before -> after and the private count',
                  'ERR [view-ref] madeira-bcd #1 kind=RTV resource=' in on and 'public=2->1 private=2' in on
                  and 'ERR [view-ref] madeira-bcd #2 kind=SRV resource=' in on and 'public=2->1 private=3' in on, on)
        moved = t / 'moved'
        (moved / 'dxmt/src/d3d11').mkdir(parents=True)
        mf = moved / 'dxmt/src/d3d11/d3d11_resource.hpp'
        mf.write_text(target.read_text().replace('  strong ref to resource\n', '  strong reference to the resource\n'))
        r3 = subprocess.run([sys.executable, str(script)], cwd=moved, capture_output=True, text=True)
        check('a moved anchor refuses the patch and leaves the file alone',
              r3.returncode != 0 and 'not found exactly once' in r3.stderr and 'madeira-bcd: views hold their resource privately' not in mf.read_text(), r3.stderr)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll is built with the patch and refused without it (a 64-bit game takes it with env.MADEIRA_D3D11_SRC = 1)',
      '"patch-dxmt-view-private-ref.py" "[view-ref] madeira-bcd #"' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries the patch',
      '"[view-ref] madeira-bcd #"' in wf[wf.index('for m in "[copy-structure-count] skipped #"'):][:900])
rules = (R / 'build/tools/autotune_rules.py').read_text()
blk = rules[rules.index('"id": "unreal-backbuffer-refcount-check"'):] if '"id": "unreal-backbuffer-refcount-check"' in rules else ''
blk = blk[:blk.index('\n    },\n')] if blk else ''
check('auto-tune: the rule asks for the source DLL and the key, names Unreal\'s check, and stops once the [view-ref] lines are in the log',
      '"lines": ["env.MADEIRA_D3D11_SRC = 1", "env.MADEIRA_VIEW_PRIVATE_REF = 1"]' in blk and 'BackBuffer->GetResource() has' in blk
      and '"none": [s("[view-ref] madeira-bcd")]' in blk and '"fixedIn"' not in blk.split('"evidence"')[0], blk[:300])
check('the key is in the settings catalog overlay (build/tools/gen-config-catalog.py)',
      '"env.MADEIRA_VIEW_PRIVATE_REF"' in (R / 'build/tools/gen-config-catalog.py').read_text())
exp = R / 'tests/fixtures/autotune/hatred-unreal-backbuffer-refcount-2026-11-04.expect'
check('its fixture is there and proposes both lines', exp.is_file() and 'unreal-backbuffer-refcount-check' in exp.read_text()
      and 'env.MADEIRA_VIEW_PRIVATE_REF = 1' in exp.read_text())
print('PASS: DXMT view private references' if ok else 'FAILED: DXMT view private references')
sys.exit(0 if ok else 1)
