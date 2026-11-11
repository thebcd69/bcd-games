#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT CreateTexture1D/2D/3D refuse a size Direct3D 11 refuses, and winemetal never hands Metal an impossible descriptor
(night round 2026-11-11, G10 S9).

The Witness (E9TQZT, J5NRJG), Rise/Shadow of the Tomb Raider, Transformers and Danganronpa ended with Metal's abort
"MTLTextureDescriptor has height (3221225536) greater than the maximum allowed size of 32768" (a SIGABRT: Wine's exception
0x80000101, not an Objective-C exception, so no @try/@catch can stop it). Two patches:
  tools/patch-dxmt-texture-limit.py        the three public creation methods return E_INVALIDARG above Direct3D 11's limits
                                           (1D/2D 16384, 3D 2048, 2048 slices) and log "[dxmt-tex-limit] ... prev=WxH,WxH,WxH";
  tools/patch-winemetal-mtl-tex-fail.py    _MTLDevice_newTexture returns no texture above 32768 / 2048 (native, every API).
This check applies both to copies of the pinned sources (or $DXMT_SRC_DIR), reads the result, and runs the guard blocks and the
native condition as plain C++/Objective-C on the host with a fake descriptor: the Witness's 3221225536, an in-limit request, the
boundaries, the prev= ring. SKIP of the source part when no dxmt sources exist.
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


p1 = R / 'tools/patch-dxmt-texture-limit.py'
p2 = R / 'tools/patch-winemetal-mtl-tex-fail.py'
check('both patch scripts exist', p1.exists() and p2.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
fk = wf.index('name: i386 farm revision')
check('CI applies the d3d11 guard before the i386 farm key (it is a tools/patch-dxmt-*.py: the farm key hashes it)',
      'python3 tools/patch-dxmt-texture-limit.py' in wf and wf.index('python3 tools/patch-dxmt-texture-limit.py') < fk)
check('CI applies the native one with the other winemetal patches (not a tools/patch-dxmt-*.py: the farm key is not touched)',
      'python3 tools/patch-winemetal-mtl-tex-fail.py' in wf
      and wf.index('python3 tools/patch-winemetal-null-fragment.py') < wf.index('python3 tools/patch-winemetal-mtl-tex-fail.py'))
check('CI runs this check', 'python3 tests/host/check-dxmt-texture-limit.py' in wf)

src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src'
if not (src / 'd3d11/d3d11_device.cpp').exists():
    alt = pathlib.Path.home() / 'madeira-bcd/dxmt/src'
    src = alt if (alt / 'd3d11/d3d11_device.cpp').exists() else src
dev = pinned(src / 'd3d11/d3d11_device.cpp')
wm = pinned(src / 'winemetal/unix/winemetal_unix.c')
if dev.exists() and wm.exists():
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / 'dxmt/src/d3d11').mkdir(parents=True)
        (t / 'dxmt/src/winemetal/unix').mkdir(parents=True)
        shutil.copy(dev, t / 'dxmt/src/d3d11/d3d11_device.cpp')
        shutil.copy(wm, t / 'dxmt/src/winemetal/unix/winemetal_unix.c')
        runs = [subprocess.run([sys.executable, str(p)], cwd=t, capture_output=True, text=True) for p in (p1, p1, p2, p2)]
        check('both apply to the pinned sources; a second run of each says "already patched"',
              all(r.returncode == 0 for r in runs) and 'patched' in runs[0].stdout and 'already patched' in runs[1].stdout
              and 'refuses' in runs[2].stdout and 'already patched' in runs[3].stdout, [r.stdout + r.stderr for r in runs])
        d = (t / 'dxmt/src/d3d11/d3d11_device.cpp').read_text()
        # --- the Direct3D 11 guard blocks
        f1 = d[d.index('CreateTexture1D(const D3D11_TEXTURE1D_DESC *pDesc'):d.index('CreateTexture2D(const D3D11_TEXTURE2D_DESC *pDesc')]
        f2 = d[d.index('CreateTexture2D1(const D3D11_TEXTURE2D_DESC1 *pDesc'):d.index('CreateTexture3D1(const D3D11_TEXTURE3D_DESC1 *pDesc')]
        f3 = d[d.index('CreateTexture3D1(const D3D11_TEXTURE3D_DESC1 *pDesc'):][:2500]
        check('each guard comes after the pDesc and tiled tests and before anything is created',
              f1.index('if (pDesc->Width > 16384u || pDesc->ArraySize > 2048u)') < f1.index('CreateDeviceTexture1D(')
              and f1.index('D3D11_RESOURCE_MISC_TILED') < f1.index('[dxmt-tex-limit]')
              and f2.index('pDesc->Width > 16384u || pDesc->Height > 16384u || pDesc->ArraySize > 2048u') < f2.index('BCCensusRecord(')
              < f2.index('CreateDeviceTexture2D(')
              and f3.index('pDesc->Width > 2048u || pDesc->Height > 2048u || pDesc->Depth > 2048u') < f3.index('CreateDeviceTexture3D('))
        check('every refusal returns E_INVALIDARG and is logged capped (8, then powers of two)',
              all(f.count('return E_INVALIDARG;') >= 2 and '(n <= 8 || !(n & (n - 1)))' in f and '[dxmt-tex-limit]' in f for f in (f1, f2, f3)))
        check('the marker the 64-bit DLL is checked for is in the 2D message', '[dxmt-tex-limit] CreateTexture2D refused ' in d)
        check('<atomic> is included', '#include <atomic>' in d)
        # --- run the 2D block (with the ring) and the 1D / 3D blocks on the host
        def block(f, start):
            a = f.index(start)
            return f[a:f.index('BCCensusRecord(') if 'BCCensusRecord(' in f else f.index('try {', a)]
        b2 = block(f2, '/* madeira-bcd: texture limit')
        b1 = block(f1, '/* madeira-bcd: texture limit')
        b3 = block(f3, '/* madeira-bcd: texture limit')
        prog = r'''
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <iomanip>
#include <sstream>
#include <string>
typedef long HRESULT; typedef unsigned UINT;
const HRESULT E_INVALIDARG = (HRESULT)0x80070057L, S_OK = 0;
namespace str { template <typename... A> std::string format(const A &...a) { std::stringstream s; (s << ... << a); return s.str(); } }
#define ERR(...) std::printf("ERR %s\n", str::format(__VA_ARGS__).c_str())
struct D1 { UINT Width, ArraySize, MipLevels; int Format; UINT BindFlags, MiscFlags; };
struct D2 { UINT Width, Height, ArraySize, MipLevels; int Format; UINT BindFlags, MiscFlags; };
struct D3 { UINT Width, Height, Depth, MipLevels; int Format; UINT BindFlags, MiscFlags; };
HRESULT c1(const D1 *pDesc) {
''' + b1 + r'''
  return S_OK; }
HRESULT c2(const D2 *pDesc) {
''' + b2 + r'''
  return S_OK; }
HRESULT c3(const D3 *pDesc) {
''' + b3 + r'''
  return S_OK; }
int main() {
  D2 ok1{1280, 720, 1, 1, 28, 0x28, 0}, ok2{1920, 1080, 1, 1, 28, 0x28, 0}, ok3{16384, 16384, 1, 1, 28, 0x8, 0};
  std::printf("in-limit: %ld %ld %ld\n", (long)c2(&ok1), (long)c2(&ok2), (long)c2(&ok3));
  D2 witness{1192, 3221225536u, 1, 1, 45, 0x40, 0};
  std::printf("witness: %d\n", (int)(c2(&witness) == E_INVALIDARG));
  D2 wide{16385, 16, 1, 1, 28, 0x8, 0}, arr{16, 16, 2049, 1, 28, 0x8, 0}, edge{16384, 16384, 2048, 1, 28, 0x8, 0}, trw{0x80000300u, 4, 1, 1, 28, 0x8, 0};
  std::printf("edges: %d %d %ld %d\n", (int)(c2(&wide) == E_INVALIDARG), (int)(c2(&arr) == E_INVALIDARG), (long)c2(&edge), (int)(c2(&trw) == E_INVALIDARG));
  D1 t1ok{16384, 1, 1, 28, 0, 0}, t1big{16385, 1, 1, 28, 0, 0}, t1arr{16, 2049, 1, 28, 0, 0};
  D3 v3ok{2048, 2048, 2048, 1, 28, 0x8, 0}, v3big{2049, 4, 4, 1, 28, 0x8, 0}, v3d{4, 4, 0x80000000u, 1, 28, 0x8, 0};
  std::printf("1d/3d: %ld %d %d %ld %d %d\n", (long)c1(&t1ok), (int)(c1(&t1big) == E_INVALIDARG), (int)(c1(&t1arr) == E_INVALIDARG),
              (long)c3(&v3ok), (int)(c3(&v3big) == E_INVALIDARG), (int)(c3(&v3d) == E_INVALIDARG));
  return 0;
}
'''
        cc = shutil.which('clang++') or shutil.which('c++')
        (t / 'tex.cpp').write_text(prog)
        b = subprocess.run([cc, '-std=c++20', '-w', '-o', str(t / 'tex'), str(t / 'tex.cpp')], capture_output=True, text=True) if cc else None
        if b is None or b.returncode != 0:
            check('the guard blocks compile on the host', False, (b.stderr if b else 'no C++ compiler'))
        else:
            out = subprocess.run([str(t / 'tex')], capture_output=True, text=True).stdout
            check('in-limit 2D requests (1280x720, 1920x1080, 16384x16384) pass untouched', 'in-limit: 0 0 0' in out, out)
            check('the Witness\'s height 3221225536 is E_INVALIDARG and the log line carries prev= the three requests before it, oldest first',
                  'witness: 1' in out and 'ERR [dxmt-tex-limit] CreateTexture2D refused 1192x3221225536 array=1 mips=1 format=45 bind=0x40 misc=0x0 '
                  'prev=1280x720,1920x1080,16384x16384' in out, out)
            check('2D: 16385 wide, 2049 slices and Tomb Raider\'s 0x80000300 are refused; 16384x16384 with 2048 slices passes',
                  'edges: 1 1 0 1' in out, out)
            check('1D and 3D: the limits are 16384 / 2048 slices and 2048 per dimension; one over is refused', '1d/3d: 0 1 1 0 1 1' in out, out)
        # --- the native condition on the host (Objective-C, a real MTLTextureDescriptor; no device is needed for the properties)
        w = (t / 'dxmt/src/winemetal/unix/winemetal_unix.c').read_text()
        fn = w[w.index('_MTLDevice_newTexture(void *obj) {'):]
        fn = fn[:fn.index('\n}\n')]
        a = fn.index('  /* madeira-bcd: mtl-tex-fail')
        cond = fn[fn.index('if (desc.textureType', a):fn.index(') {', a) + 1]
        check('the native guard comes after the descriptor is filled and before Metal is called; it frees the descriptor and returns no texture',
              fn.index('fill_texture_descriptor(desc, info);') < a < fn.index('[device newTextureWithDescriptor:desc]')
              and 'params->ret = 0;' in fn[a:] and '[desc release];\n    return STATUS_SUCCESS;' in fn[a:fn.index('[device newTextureWithDescriptor:desc]')]
              and '[mtl-tex-fail] madeira-bcd #' in fn and '(n <= 8 || (n & (n - 1)) == 0)' in fn)
        check('the native guard skips buffer textures and only looks at sizes above every Apple GPU limit',
              'MTLTextureTypeTextureBuffer' in cond and '32768' in cond and '2048' in cond and 'desc.depth > 2048' in cond, cond)
        objc = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static int refused(MTLTextureDescriptor *desc) { return ''' + cond[len('if '):] + r'''; }
int main(void) { @autoreleasepool {
  MTLTextureDescriptor *d = [MTLTextureDescriptor new];
  d.textureType = MTLTextureType2D; d.pixelFormat = MTLPixelFormatBGRA8Unorm;
  d.width = 4096; d.height = 4096; int a = refused(d);
  d.width = 32768; d.height = 32768; int b = refused(d);
  d.height = 3221225536u; int c = refused(d);
  d.width = 1192; d.height = 32769; int e = refused(d);
  d.height = 768; d.textureType = MTLTextureType3D; d.depth = 2048; int f = refused(d);
  d.depth = 2049; int g = refused(d);
  d.textureType = MTLTextureType2DArray; d.depth = 1; d.arrayLength = 2049; int h = refused(d);
  d.textureType = MTLTextureTypeTextureBuffer; d.width = 100000000; d.height = 1; d.arrayLength = 1; int i = refused(d);
  printf("native: %d %d %d %d %d %d %d %d\n", a, b, c, e, f, g, h, i);
} return 0; }
'''
        (t / 'n.m').write_text(objc)
        bn = subprocess.run(['clang', '-x', 'objective-c', '-fblocks', '-w', '-framework', 'Foundation', '-framework', 'Metal', '-o', str(t / 'n'), str(t / 'n.m')],
                            capture_output=True, text=True)
        if bn.returncode != 0:
            print('skip: the native condition did not build on this host: ' + bn.stderr.strip()[-200:])
        else:
            out = subprocess.run([str(t / 'n')], capture_output=True, text=True).stdout
            check('native: 4096 and 32768 square pass; height 3221225536 and 32769, 3D depth 2049 and 2049 slices are refused; a 100M-texel buffer texture passes',
                  'native: 0 0 1 1 0 1 1 0' in out, out)
        moved = t / 'moved'
        (moved / 'dxmt/src/d3d11').mkdir(parents=True)
        mf = moved / 'dxmt/src/d3d11/d3d11_device.cpp'
        mf.write_text(dev.read_text().replace('return CreateDeviceTexture3D(this, pDesc, pInitialData, ppTexture3D);', 'return CreateDeviceTexture3DX(this, pDesc, pInitialData, ppTexture3D);'))
        r3 = subprocess.run([sys.executable, str(p1)], cwd=moved, capture_output=True, text=True)
        check('a moved anchor refuses the d3d11 patch and leaves the file alone',
              r3.returncode != 0 and 'not found exactly once' in r3.stderr and 'madeira-bcd: texture limit' not in mf.read_text(), r3.stderr)
        movedw = t / 'movedw'
        (movedw / 'dxmt/src/winemetal/unix').mkdir(parents=True)
        mw = movedw / 'dxmt/src/winemetal/unix/winemetal_unix.c'
        mw.write_text(wm.read_text().replace('  id<MTLTexture> ret = [device newTextureWithDescriptor:desc];\n  params->ret = (obj_handle_t)ret;\n',
                                             '  id<MTLTexture> ret = [device newTextureWithDescriptor:desc];\n  params->ret = (obj_handle_t)(ret);\n'))
        r4 = subprocess.run([sys.executable, str(p2)], cwd=movedw, capture_output=True, text=True)
        check('a moved anchor refuses the native patch and leaves the file alone',
              r4.returncode != 0 and 'not found exactly once' in r4.stderr and 'madeira-bcd: mtl-tex-fail' not in mw.read_text(), r4.stderr)
else:
    print(f'SKIP the patch run: no dxmt sources at {src} (check out the submodule or set DXMT_SRC_DIR)')

sh = (R / 'tools/build-d3d11-dll.sh').read_text()
check('d3d11-src.dll is built with the guard and refused without it (a 64-bit game takes it with env.MADEIRA_D3D11_SRC = 1)',
      '"patch-dxmt-texture-limit.py" "[dxmt-tex-limit] CreateTexture2D refused "' in sh)
check('the IPA is refused when no 64-bit d3d11.dll carries the guard',
      '"[dxmt-tex-limit] CreateTexture2D refused "' in wf[wf.index('for m in "[copy-structure-count] skipped #"'):][:800])
rules = (R / 'build/tools/autotune_rules.py').read_text()
blk = rules[rules.index('"id": "metal-texture-descriptor-too-large"'):] if '"id": "metal-texture-descriptor-too-large"' in rules else ''
blk = blk[:blk.index('\n    },\n')] if blk else ''
check('auto-tune: the rule is fixed in 652 and names Metal\'s assertion; its fixture is there',
      '"fixedIn": 652' in blk and 'MTLTextureDescriptor has' in blk and 'greater than the maximum allowed size' in blk
      and (R / 'tests/fixtures/autotune/the-witness-metal-texture-descriptor-2026-11-08.expect').is_file(), blk[:300])
print('PASS: DXMT texture size limits' if ok else 'FAILED: DXMT texture size limits')
sys.exit(0 if ok else 1)
