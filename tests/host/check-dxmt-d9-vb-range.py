#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: a non-indexed draw refreshes only the vertex bytes it reads (keyed: on only with env DXMT_D9_VB_RANGE=1).

Resident Evil 4 (2005, B1TDSR, build 686) ran at 4 FPS: it locks one dynamic ~3.8 MB vertex buffer 210 times a frame (1 to 256 bytes
each, a DrawPrimitive after each) and the whole-mirror refresh copied the whole buffer for every draw, 788 MB a frame. tools/patch-dxmt-
d9-vb-range.py is the vertex-buffer counterpart of the index-buffer range refresh (tools/patch-dxmt-d9-ib-range.py) and reuses its state
machine (d3d9_ib_range.hpp); d3d9_vb_range.hpp holds what is specific to vertex buffers.

This check:
  1. the wiring in CI: the patch step directly after the index-buffer one, this check before it, both before the i386 farm key and the
     64-bit Direct3D 9 build (both compile the patched dxmt/src/d3d9);
  2. applies the patch to a copy of the pinned dxmt/src/d3d9 (or $DXMT_SRC_DIR; the index-buffer patch first, which it extends) twice
     (the second run says "already patched"); a moved anchor, and a tree without the index-buffer patch, fail by name with no file changed;
  3. reads the result: the switch (off unless env DXMT_D9_VB_RANGE == "1", read once, said once), the dispatch in BuildDrawCapture (key
     off: the loop as it was), what the vertex buffer does with a range (the rename fallbacks, the direct-map guard, the census), which
     draws pass a range (only DrawPrimitive's two) and which keep the whole refresh, the census line;
  4. compiles d3d9_vb_range.hpp on the host with -fsanitize=address,undefined and runs it against a model of the GPU generations:
     the Resident Evil 4 pattern (small writes at rising offsets, a draw after each: only those bytes are stored, no rename, no whole
     copy), a rewrite of bytes an earlier draw read (rename; the earlier draw keeps its bytes), a second draw over stored bytes (nothing
     stored), DISCARD and any other write lock, instancing / indexed / user-pointer / stride 0 / a declaration past the stride / no
     declaration / one buffer on two streams / an unknown primitive type / a range outside the buffer (all whole, unchanged), the interval
     set full (one whole copy), the key off (byte for byte the old refresh), and random lock / write / draw sequences over several
     streams. Deliberately broken bookkeeping must fail the model. The device's side is run too: MTLD3D9Device::RefreshVertexBuffersForDraw is
     cut out of the patched d3d9_device.cpp and compiled against the real Direct3D headers (when the dxmt tree has include/native, as in
     CI) with fakes of the buffer, the declaration and the stream arrays: declaration extents, strides, offsets and frequencies decide
     which stream gets a range (broken glue must fail too);
  5. the wiring outside DXMT: the settings catalog and the list policy.
"""
import os, pathlib, re, shutil, subprocess, sys, tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        ok = False
        if detail:
            print('     ' + str(detail)[:2000])


IB = 'tools/patch-dxmt-d9-ib-range.py'
VB = 'tools/patch-dxmt-d9-vb-range.py'
script = R / VB
ib_script = R / IB
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
scripts = re.findall(r'python3 (tools/patch-(?:dxmt|airconv|winemetal)-[\w.-]+\.py)', wf)
check('CI applies it directly after the index-buffer range patch (it extends that patch: header, census line, BuildDrawCapture)',
      VB in scripts and IB in scripts and scripts.index(VB) == scripts.index(IB) + 1)
check('CI runs this check, after the index-buffer patch step and before this patch step',
      'python3 tests/host/check-dxmt-d9-vb-range.py' in wf
      and wf.index('python3 ' + IB) < wf.index('python3 tests/host/check-dxmt-d9-vb-range.py') < wf.index('python3 ' + VB))
if 'python3 ' + VB in wf:
    at = wf.index('python3 ' + VB)
    check('the patch step runs before the i386 farm and the 64-bit Direct3D 9 build (both compile the patched tree)',
          at < wf.index('id: i386rev') and at < wf.index('bash tools/build-d3d9-x64-dll.sh'))
    check('the i386 farm key hashes every tools/patch-dxmt-*.py, so the new patch changes it',
          "'tools/patch-dxmt-*.py'" in wf[wf.index('id: i386rev'):][:900])

# The device's side as a program: the harness around the cut-out RefreshVertexBuffersForDraw (FUNCTION_SLOT).
GLUE_HARNESS = r"""
#include "d3d9.h"
#include "d3d9_process_vertices.hpp"
#include "d3d9_vb_range.hpp"
#include <cstdint>
#include <cstdio>
#include <vector>
namespace dxmt {
inline constexpr unsigned D3D9_MAX_VERTEX_STREAMS = 16;
struct Call { int id; int kind; uint32_t lo, hi; };   // kind 0: range, 1: whole refresh, 2: noteWholeRead
static std::vector<Call> g_calls;
struct MTLD3D9VertexBuffer {
  int id; UINT sz;
  UINT size() const { return sz; }
  void refreshRange(uint32_t lo, uint32_t hi) { g_calls.push_back({id, 0, lo, hi}); }
  void refreshWholeMirror() { g_calls.push_back({id, 1, 0, 0}); }
  void noteWholeRead() { g_calls.push_back({id, 2, 0, 0}); }
};
struct MTLD3D9VertexDeclaration {
  std::vector<D3DVERTEXELEMENT9> e;
  const D3DVERTEXELEMENT9 *elements() const { return e.data(); }
  UINT elementCount() const { return (UINT)e.size(); }
};
template <class T> struct Slot { T *p = nullptr; T *ptr() const { return p; } T *operator->() const { return p; } };   // Com<T, false>
struct MTLD3D9Device {
  Slot<MTLD3D9VertexBuffer> m_vertexBuffers[D3D9_MAX_VERTEX_STREAMS];
  UINT m_streamOffsets[D3D9_MAX_VERTEX_STREAMS] = {}, m_streamStrides[D3D9_MAX_VERTEX_STREAMS] = {}, m_streamFreq[D3D9_MAX_VERTEX_STREAMS];
  Slot<MTLD3D9VertexDeclaration> m_vertexDeclaration;
  MTLD3D9Device() { for (UINT &f : m_streamFreq) f = 1; }
  void RefreshVertexBuffersForDraw(uint32_t vb_first, uint64_t vb_count);
};
FUNCTION_SLOT
}
using namespace dxmt;
static int fails;
#define EXPECT(c, ...) do { if (!(c)) { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)
static D3DVERTEXELEMENT9 el(WORD stream, WORD offset, BYTE type) { D3DVERTEXELEMENT9 e = {stream, offset, type, 0, 0, 0}; return e; }
static D3DVERTEXELEMENT9 endEl() { D3DVERTEXELEMENT9 e = D3DDECL_END(); return e; }
static bool is(const std::vector<Call> &c, std::initializer_list<Call> want) {
  if (c.size() != want.size()) return false;
  size_t i = 0;
  for (const Call &w : want) { const Call &g = c[i++]; if (g.id != w.id || g.kind != w.kind || g.lo != w.lo || g.hi != w.hi) return false; }
  return true;
}
static void dump(const char *what) { printf("  %s:", what); for (const Call &c : g_calls) printf(" {%d,%d,%u,%u}", c.id, c.kind, c.lo, c.hi); printf("\n"); }

int main() {
  MTLD3D9VertexBuffer a{0, 4096}, b{1, 2048};
  MTLD3D9VertexDeclaration fvf;   // position, normal, texcoord: 32 bytes of vertex
  fvf.e = {el(0, 0, D3DDECLTYPE_FLOAT3), el(0, 12, D3DDECLTYPE_FLOAT3), el(0, 24, D3DDECLTYPE_FLOAT2), endEl()};
  auto fresh = [&](MTLD3D9Device &d) { g_calls.clear(); d.m_vertexDeclaration.p = &fvf; d.m_vertexBuffers[0].p = &a; d.m_streamOffsets[0] = 16; d.m_streamStrides[0] = 32; };

  { MTLD3D9Device d; fresh(d); d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 0, 16 + 320, 16 + 512}}), "one stream, extent 32 = stride: its range [offset + 10 * 32, + 6 * 32)"); if (fails) dump("got"); }
  { MTLD3D9Device d; fresh(d); d.m_streamStrides[0] = 28; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "a declaration that reaches past the stride (32 > 28): the whole refresh, the whole buffer read"); }
  { MTLD3D9Device d; fresh(d); d.m_streamStrides[0] = 0; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "stride 0: whole"); }
  { MTLD3D9Device d; fresh(d); d.m_streamFreq[3] = 0x80000001u; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "instance data on a stream: whole on every stream"); }
  { MTLD3D9Device d; fresh(d); d.m_streamFreq[0] = 0x40000003u; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "indexed data (instancing) on stream 0: whole"); }
  { MTLD3D9Device d; fresh(d); d.m_vertexDeclaration.p = nullptr; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "no vertex declaration: whole"); }
  { MTLD3D9Device d; fresh(d); d.RefreshVertexBuffersForDraw(d9vb::kWhole, 0);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "no range given (indexed, user pointer): whole"); }
  { MTLD3D9Device d; fresh(d); d.RefreshVertexBuffersForDraw(1000, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "a range outside the buffer: whole"); }
  { MTLD3D9Device d; fresh(d); d.RefreshVertexBuffersForDraw(10, 0);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "no vertices: whole"); }
  { MTLD3D9Device d; fresh(d); d.m_vertexBuffers[2].p = &a; d.m_streamStrides[2] = 32; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}, {0, 1, 0, 0}, {0, 2, 0, 0}}), "one buffer on two streams: whole on both"); }
  { // two buffers, a stream-1 declaration, the end marker and an element past the stream cap are ignored by the scan
    MTLD3D9VertexDeclaration two;
    two.e = {el(0, 0, D3DDECLTYPE_FLOAT3), el(0, 12, D3DDECLTYPE_FLOAT3), el(0, 24, D3DDECLTYPE_FLOAT2), el(1, 0, D3DDECLTYPE_FLOAT2),
             el(1, 8, D3DDECLTYPE_FLOAT2), el(20, 0, D3DDECLTYPE_FLOAT4), endEl()};
    MTLD3D9Device d; fresh(d); d.m_vertexDeclaration.p = &two; d.m_streamStrides[0] = 32; d.m_streamOffsets[0] = 0;
    d.m_vertexBuffers[1].p = &b; d.m_streamStrides[1] = 16; d.m_streamOffsets[1] = 8;
    d.RefreshVertexBuffersForDraw(5, 6);
    EXPECT(is(g_calls, {{0, 0, 160, 352}, {1, 0, 88, 184}}), "two streams: each its own range, in stream order; the end marker and stream 20 change nothing");
    if (fails) dump("got"); }
  { MTLD3D9Device d; fresh(d); d.m_vertexBuffers[1].p = nullptr; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 0, 336, 528}}), "an unbound stream: nothing"); }
  { MTLD3D9VertexDeclaration none; none.e = {endEl()};
    MTLD3D9Device d; fresh(d); d.m_vertexDeclaration.p = &none; d.RefreshVertexBuffersForDraw(10, 6);
    EXPECT(is(g_calls, {{0, 0, 336, 528}}), "a declaration with no element: nothing reaches past the stride, the range stands"); }
  { // the largest 32-bit numbers: no wrap-around into a small range
    MTLD3D9Device d; fresh(d); d.m_streamOffsets[0] = 0xffffffffu; d.m_streamStrides[0] = 0xffffffffu; d.RefreshVertexBuffersForDraw(0xfffffff0u, 0xffffffffffull);
    EXPECT(is(g_calls, {{0, 1, 0, 0}, {0, 2, 0, 0}}), "the largest offset, stride and start: whole, no overflow"); }
  if (!fails) puts("glue-ok");
  return fails != 0;
}
"""

FILES = ['d3d9_buffer.hpp', 'd3d9_buffer.cpp', 'd3d9_device.hpp', 'd3d9_device.cpp', 'd3d9_census.hpp', 'd3d9_census.cpp']
HEADERS = ['d3d9_ib_range.hpp', 'd3d9_vb_range.hpp']
src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src' / 'd3d9'
cxx = shutil.which('c++') or shutil.which('clang++')


def run(cmd, cwd):
    return subprocess.run([sys.executable, str(cmd)], cwd=cwd, capture_output=True, text=True)


def stage(root):
    """A copy of the source files (and the range headers that exist) at root/dxmt/src/d3d9."""
    d = root / 'dxmt/src/d3d9'
    d.mkdir(parents=True)
    for f in FILES + [h for h in HEADERS if (src / h).exists()]:
        shutil.copy(src / f, d / f)
    return d


if not all((src / f).exists() for f in FILES):
    print('skip the patch run: no dxmt sources (set DXMT_SRC_DIR)')
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        d = stage(t)
        # The index-buffer patch first (in CI its step has already run on the tree: "already patched").
        r0 = run(ib_script, t)
        check('the index-buffer range patch is in place under it (applied, or found applied)',
              r0.returncode == 0 and ('patched:' in r0.stdout or 'already patched' in r0.stdout), r0.stdout + r0.stderr)
        before_vb = {f: (d / f).read_text() for f in FILES}
        had_vb = (d / 'd3d9_vb_range.hpp').exists()
        r1 = run(script, t)
        r2 = run(script, t)
        check('it applies (or finds the tree patched) and a second run says "already patched"',
              r1.returncode == 0 and r2.returncode == 0 and 'already patched' in r2.stdout
              and ('patched:' in r1.stdout or 'already patched' in r1.stdout), r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        T = {f: (d / f).read_text() for f in FILES}
        H = (d / 'd3d9_vb_range.hpp').read_text() if (d / 'd3d9_vb_range.hpp').exists() else ''
        H_IB = (d / 'd3d9_ib_range.hpp').read_text() if (d / 'd3d9_ib_range.hpp').exists() else ''
        check('every file carries the marker and the header exists', all('madeira-bcd: DXMT_D9_VB_RANGE' in T[f] for f in FILES) and H)
        check('the index-buffer range files are still there (nothing of that patch was undone)',
              all('madeira-bcd: DXMT_D9_IB_RANGE' in T[f] for f in FILES) and H_IB)

        b = T['d3d9_buffer.cpp']
        sw = b[b.index('d9vb::rangeRefreshEnabled() {'):] if 'd9vb::rangeRefreshEnabled() {' in b else ''
        sw = sw[:sw.index('\n}\n')] if sw else ''
        check('the switch: OFF unless env DXMT_D9_VB_RANGE is "1", read once (a static), said once, on and off',
              'static const bool on' in sw and 'env::getEnvVar("DXMT_D9_VB_RANGE") == "1"' in sw
              and '[vb-range] madeira-bcd on: a non-indexed draw stores only the vertex bytes it reads (DXMT_D9_VB_RANGE=1)' in sw
              and '[vb-range] madeira-bcd off (DXMT_D9_VB_RANGE=1 turns it on)' in sw, sw)
        rr = b[b.index('MTLD3D9VertexBuffer::refreshRange(uint32_t lo, uint32_t hi) {'):] if 'MTLD3D9VertexBuffer::refreshRange(uint32_t lo, uint32_t hi) {' in b else ''
        rr = rr[:rr.index('\n}\n')] if rr else ''
        direct = 'bool m_direct = false;' in T['d3d9_buffer.hpp']   # dxmt 880241a's direct map; an older base tree (the patch-chain checks) has none
        check('refreshRange: a direct-mapped buffer returns first (no mirror; only where the tree has the direct map), then the state machine '
              'decides: Skip stores nothing, Whole takes the whole-mirror refresh, RenameStore renames with the whole path\'s fallbacks, the '
              'store is the range only, the state learns it, the buffer stays dirty, the census counts it',
              (rr.index('if (m_direct)') < rr.index('d9vb::onRangeDraw(') if direct else 'm_direct' not in rr)
              and rr.index('d9vb::onRangeDraw(m_range, m_dirty, m_writeLocked, lo, hi)') < rr.index('m_hostPtr')
              and 'census::bufferRefresh(census::kRefreshVbRangeSkip, 0);' in rr
              and 'm_dirty = true;\n    refreshWholeMirror();\n    return;' in rr and rr.count('drainForDynamicRename()') == 1
              and 'noteRenameInPlace()' in rr and 'updateContents(\n      lo, static_cast<const char *>(m_hostPtr) + lo, hi - lo,' in rr
              and 'm_range.onRangeStored(lo, hi, renamed, m_writeLocked);' in rr and rr.rstrip().endswith(
                  'census::bufferRefresh(renamed ? census::kRefreshVbRangeRenamed : census::kRefreshVbRange, hi - lo);'), rr)
        whole = b[b.index('MTLD3D9VertexBuffer::refreshWholeMirror() {'):]
        whole = whole[:whole.index('\n}\n')]
        check('the vertex buffer\'s whole-mirror refresh is as before (one whole store, the census count), plus, with the key, the state of a '
              'whole generation',
              whole.count('m_dynamic->immediateName()->updateContents(0, m_hostPtr, m_size, m_dynamic->immediateSuballocation());') == 1
              and 'census::bufferRefresh(census::kRefreshVbWhole, m_size);' in whole
              and 'm_dirty = m_writeLocked;\n  // madeira-bcd: DXMT_D9_VB_RANGE. The fresh generation holds the whole mirror.\n'
                  '  if (d9vb::rangeRefreshEnabled())\n    m_range.onWholeStored(m_writeLocked, m_size);' in whole, whole[-600:])
        lock = b[b.index('MTLD3D9VertexBuffer::Lock('):b.index('MTLD3D9VertexBuffer::Unlock()')]
        check('any write lock of a vertex buffer, whatever its flags or declared range, forgets which stored bytes were current',
              lock.count('m_range.onWriteLock();') == 1 and lock.index('m_writeLocked = true;') < lock.index('m_range.onWriteLock();')
              and lock.index('m_range.onWriteLock();') < lock.rindex('m_lockCount.increment();'), lock)
        check('the index buffer keeps its own state and calls (the vertex patch touched none of them)',
              b.count('m_range.onWriteLock();') == 2 and b.count('MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {') == 1
              and b.count('census::bufferRefresh(census::kRefreshVbWhole, m_size);') == 1)
        bh = T['d3d9_buffer.hpp']
        check('MTLD3D9VertexBuffer has refreshRange, noteWholeRead and the state; the header is included',
              '#include "d3d9_vb_range.hpp"' in bh and bh.count('d9ib::RangeState m_range;') == 2
              and bh.count('void refreshRange(uint32_t lo, uint32_t hi);') == 2 and bh.count('m_range.onWholeRead(m_size);') == 2)

        dv = T['d3d9_device.cpp']
        cap = dv[dv.index('MTLD3D9Device::BuildDrawCapture(uint32_t ib_lo, uint32_t ib_hi, uint32_t vb_first, uint64_t vb_count) {'):]
        cap = cap[:cap.index('// POD state read by Resolve')]
        check('BuildDrawCapture: key off, the vertex-buffer loop exactly as it was; key on, the range refresh; the index buffer part untouched',
              'if (d9vb::rangeRefreshEnabled()) {\n    RefreshVertexBuffersForDraw(vb_first, vb_count);\n  } else {\n'
              '    for (uint32_t s = 0; s < D3D9_MAX_VERTEX_STREAMS; ++s) {\n      if (auto *vb = m_vertexBuffers[s].ptr())\n'
              '        vb->refreshWholeMirror();\n    }\n  }\n' in cap
              and 'if (!d9ib::rangeRefreshEnabled() || ib_lo == d9ib::kWhole) {\n      m_indexBuffer->refreshWholeMirror();' in cap, cap)
        rf = dv[dv.index('MTLD3D9Device::RefreshVertexBuffersForDraw(uint32_t vb_first, uint64_t vb_count) {'):]
        rf = rf[:rf.index('\n}\n')]
        check('RefreshVertexBuffersForDraw: the plan comes from d9vb::planDraw over what the draw finds (declaration extents per stream, '
              'strides, offsets, frequencies); a range stream gets refreshRange, every other the whole refresh and the whole buffer read',
              'd9vb::planDraw(vb_first, vb_count, declared, m_streamFreq, in, plan);' in rf
              and 'in[s] = {vb, m_streamOffsets[s], m_streamStrides[s], 0, vb ? vb->size() : 0};' in rf
              and 'm_vertexDeclaration->elements()' in rf and 'decl_element_byte_size(e[i].Type)' in rf
              and 'if (e[i].Stream >= D3D9_MAX_VERTEX_STREAMS)' in rf
              and 'if (plan[s].range) {\n      vb->refreshRange(plan[s].lo, plan[s].hi);\n    } else {\n      vb->refreshWholeMirror();\n'
                  '      vb->noteWholeRead();\n    }' in rf
              and 'static_assert(D3D9_MAX_VERTEX_STREAMS == d9vb::kMaxStreams' in rf and 'd9vb::kTriangleFan == D3DPT_TRIANGLEFAN' in rf, rf)
        dp = dv[dv.index('MTLD3D9Device::DrawPrimitive(D3DPRIMITIVETYPE PrimitiveType, UINT StartVertex, UINT PrimitiveCount) {'):]
        dp = dp[:dp.index('\n}\n')]
        check('DrawPrimitive passes the vertices it reads: the fan [StartVertex, StartVertex + PrimitiveCount + 2) (wider than 32 bits), the '
              'plain draw d9vb::vertexCount(type, count) from StartVertex',
              'BuildDrawCapture(d9ib::kNone, d9ib::kNone, StartVertex, static_cast<uint64_t>(PrimitiveCount) + 2u);' in dp
              and 'BuildDrawCapture(d9ib::kNone, d9ib::kNone, StartVertex, d9vb::vertexCount(PrimitiveType, PrimitiveCount));' in dp
              and 'BuildDrawCapture(d9ib::kNone, d9ib::kNone);' not in dp, dp)
        check('no other draw passes a vertex range: indexed, the fan / sentinel draws of DrawIndexedPrimitive and the user-pointer draws keep '
              'the whole refresh (five calls with only the index arguments, the user-pointer index draw\'s plain call untouched)',
              dv.count('BuildDrawCapture(d9ib::kNone, d9ib::kNone);') == 5 and dv.count('BuildDrawCapture(ib_lo, ib_hi);') == 1
              and dv.count('BuildDrawCapture();') == 1 and dv.count('BuildDrawCapture(d9ib::kNone, d9ib::kNone, StartVertex') == 2)
        dh = T['d3d9_device.hpp']
        check('BuildDrawCapture\'s new arguments default to "no range", so a call that does not name them is the old call',
              'uint32_t ib_lo = d9ib::kWhole, uint32_t ib_hi = d9ib::kWhole, uint32_t vb_first = d9vb::kWhole, uint64_t vb_count = 0' in dh
              and 'void RefreshVertexBuffersForDraw(uint32_t vb_first, uint64_t vb_count);' in dh and '#include "d3d9_vb_range.hpp"' in dh)
        ch, cc = T['d3d9_census.hpp'], T['d3d9_census.cpp']
        check('the census summary\'s "buffer refresh per frame" line keeps its fields and ends with the vertex range fields (stores, renames, MB, '
              'already stored); three new counters',
              'kRefreshVbRange = 5,' in ch and 'kRefreshVbRangeRenamed = 6,' in ch and 'kRefreshVbRangeSkip = 7,' in ch and 'kRefreshKinds = 8' in ch
              and 'buffer refresh per frame: vb whole %.1f (%.2f MB) | ib whole %.1f (%.2f MB) | ib range %.1f + %.1f renamed (%.3f MB), %.1f '
                  '"\n       "already stored | vb range %.1f + %.1f renamed (%.3f MB), %.1f already stored"' in cc
              and 'reportRefresh(frames);\n  reportQueries(frames);' in cc
              and '(b[kRefreshVbRange] + b[kRefreshVbRangeRenamed]) / f / 1048576.0' in cc)

        # A moved anchor fails by name and changes nothing; so does a tree without the index-buffer patch. Both need the files before this
        # patch, which a tree that already has it cannot give.
        if had_vb:
            print('note the dxmt tree already has this patch: the moved-anchor and no-index-patch tests need the files before it')
        else:
            bad = t / 'bad'
            bd = stage(bad)
            run(ib_script, bad)
            p = bd / 'd3d9_device.cpp'
            p.write_text(p.read_text().replace('MTLD3D9Device::BuildDrawCapture(uint32_t ib_lo, uint32_t ib_hi) {',
                                                'MTLD3D9Device::BuildDrawCaptureX(uint32_t ib_lo, uint32_t ib_hi) {'))
            was = {f: (bd / f).read_text() for f in FILES}
            r3 = run(script, bad)
            check('a moved anchor fails by name and no file changes (no header made)',
                  r3.returncode != 0 and 'BuildDrawCapture definition: anchor not found once' in (r3.stdout + r3.stderr)
                  and all((bd / f).read_text() == was[f] for f in FILES) and not (bd / 'd3d9_vb_range.hpp').exists(), r3.stdout + r3.stderr)
            bare = t / 'bare'
            bad2 = stage(bare)
            if 'madeira-bcd: DXMT_D9_IB_RANGE' not in (bad2 / 'd3d9_device.cpp').read_text():
                was2 = {f: (bad2 / f).read_text() for f in FILES}
                r4 = run(script, bare)
                check('without the index-buffer range patch it fails by name and changes nothing',
                      r4.returncode != 0 and 'tools/patch-dxmt-d9-ib-range.py) has not been applied' in (r4.stdout + r4.stderr)
                      and all((bad2 / f).read_text() == was2[f] for f in FILES) and not (bad2 / 'd3d9_vb_range.hpp').exists(),
                      r4.stdout + r4.stderr)
            else:
                print('note the dxmt tree already has the index-buffer patch: the no-index-patch test needs the files before it')

        # ---------------------------------------------------------------- the bookkeeping on the host
        if not cxx:
            print('skip the host compile: no C++ compiler')
        else:
            harness = r'''
#include "d3d9_vb_range.hpp"
#include <cstdio>
#include <cstring>
#include <memory>
#include <random>
#include <vector>
using namespace dxmt;
using namespace dxmt::d9vb;
using d9ib::Action;
using d9ib::RangeState;

static int fails;
#define EXPECT(c, ...) do { if (!(c)) { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)
typedef unsigned long long ull;

// The number of vertices of a primitive, written out here once more and independently of d9vb::vertexCount (the model's truth).
static uint64_t trueVertices(uint32_t prim, uint32_t n) {
  switch (prim) {
  case 1: return n;
  case 2: return 2ull * n;
  case 3: return n + 1ull;
  case 4: return 3ull * n;
  case 5: case 6: return n + 2ull;
  }
  return 0;
}

// One vertex buffer as MTLD3D9VertexBuffer acts on its state (d3d9_buffer.cpp), against GPU generations that are kept for ever.
struct Vb {
  uint32_t size;
  bool key, dirty, writeLocked = false;
  int lockCount = 0;
  std::vector<uint8_t> mirror;
  std::vector<std::vector<uint8_t>> gens;
  uint32_t cur = 0;
  RangeState st;
  ull bytes = 0, renames = 0, wholes = 0, skips = 0, stores = 0, storesRenamed = 0;
  // initially_dirty: a pool other than DEFAULT (the first refresh stores the mirror into a generation of unknown contents, 0xEE here);
  // otherwise a DEFAULT buffer nobody has written: generation and mirror are both zero.
  Vb(uint32_t s, bool k, bool initially_dirty) : size(s), key(k), dirty(initially_dirty), mirror(s, 0) {
    gens.push_back(std::vector<uint8_t>(s, initially_dirty ? 0xEE : 0));
  }
  void rename() { gens.push_back(std::vector<uint8_t>(size, 0xEE)); cur = (uint32_t)gens.size() - 1; renames++; }
  void refreshWholeMirror() {
    if (!dirty) return;
    rename();
    gens[cur] = mirror; bytes += size; wholes++;
    dirty = writeLocked;
    if (key) st.onWholeStored(writeLocked, size);
  }
  void noteWholeRead() { st.onWholeRead(size); }
  void refreshRange(uint32_t lo, uint32_t hi) {
    Action a = onRangeDraw(st, dirty, writeLocked, lo, hi);
    if (a == Action::Skip) { skips++; return; }
    if (a == Action::Whole) { dirty = true; refreshWholeMirror(); return; }
    bool renamed = a == Action::RenameStore;
    if (renamed) rename();
    memcpy(gens[cur].data() + lo, mirror.data() + lo, hi - lo); bytes += hi - lo;
    (renamed ? storesRenamed : stores)++;
    st.onRangeStored(lo, hi, renamed, writeLocked);
    dirty = true;
  }
  void lock(bool write) { if (write) { dirty = true; writeLocked = true; st.onWriteLock(); } lockCount++; }
  void unlock() { if (lockCount > 0) lockCount--; if (lockCount == 0) writeLocked = false; }
  void write(std::mt19937 &rng, uint32_t lo, uint32_t hi) { for (uint32_t i = lo; i < hi; i++) mirror[i] = (uint8_t)(rng() | 1); }
  ull rangeStores() const { return stores + storesRenamed; }
};

// The vertex buffer without the patch: the whole-mirror refresh and nothing else (the key-off reference).
struct OldVb {
  uint32_t size;
  bool dirty = false, writeLocked = false;
  int lockCount = 0;
  std::vector<uint8_t> mirror;
  std::vector<std::vector<uint8_t>> gens;
  uint32_t cur = 0;
  ull bytes = 0, renames = 0;
  explicit OldVb(uint32_t s) : size(s), mirror(s, 0) { gens.push_back(std::vector<uint8_t>(s, 0)); }
  void refreshWholeMirror() {
    if (!dirty) return;
    gens.push_back(std::vector<uint8_t>(size, 0xEE)); cur = (uint32_t)gens.size() - 1; renames++;
    gens[cur] = mirror; bytes += size;
    dirty = writeLocked;
  }
  void lock(bool write) { if (write) { dirty = true; writeLocked = true; } lockCount++; }
  void unlock() { if (lockCount > 0) lockCount--; if (lockCount == 0) writeLocked = false; }
};

// What the GPU will read from a generation, and what it must find there.
struct Rec { Vb *vb; uint32_t gen, lo, hi; std::vector<uint8_t> expect; };

// The device's side (MTLD3D9Device::BuildDrawCapture + RefreshVertexBuffersForDraw) over up to 16 streams.
struct Dev {
  bool key, declared = true;
  Vb *vb[kMaxStreams] = {};
  uint32_t offset[kMaxStreams] = {}, stride[kMaxStreams] = {}, extent[kMaxStreams] = {}, freq[kMaxStreams];
  std::vector<Rec> recs;
  explicit Dev(bool k) : key(k) { for (uint32_t &f : freq) f = 1; }
  void bind(uint32_t s, Vb *b, uint32_t off, uint32_t str, uint32_t ext) { vb[s] = b; offset[s] = off; stride[s] = str; extent[s] = ext; }

  void refresh(uint32_t first, uint64_t count) {
    if (!key) {                       // the loop as it was
      for (uint32_t s = 0; s < kMaxStreams; s++) if (vb[s]) vb[s]->refreshWholeMirror();
      return;
    }
    StreamIn in[kMaxStreams];
    for (uint32_t s = 0; s < kMaxStreams; s++) in[s] = {vb[s], offset[s], stride[s], extent[s], vb[s] ? vb[s]->size : 0};
    StreamPlan plan[kMaxStreams];
    planDraw(first, count, declared, freq, in, plan);
    for (uint32_t s = 0; s < kMaxStreams; s++) {
      if (!vb[s]) continue;
      if (plan[s].range) vb[s]->refreshRange(plan[s].lo, plan[s].hi);
      else { vb[s]->refreshWholeMirror(); vb[s]->noteWholeRead(); }
    }
  }
  // Whether the model expects a stream of this draw to hold only its range (everything the range rests on is true), written independently.
  bool expectRange(uint32_t s, uint32_t first, uint64_t count) const {
    if (!key || first == kWhole || count == 0 || !declared) return false;
    for (uint32_t t = 0; t < kMaxStreams; t++) if (freq[t] != 1) return false;
    if (stride[s] == 0 || extent[s] > stride[s]) return false;
    for (uint32_t t = 0; t < kMaxStreams; t++) if (t != s && vb[t] == vb[s]) return false;
    return offset[s] + (ull)first * stride[s] < vb[s]->size;
  }
  // `passed`: the count the device hands BuildDrawCapture; `real`: the vertices the draw really reads. first == kWhole: no range given.
  void draw(uint32_t first, uint64_t passed, uint64_t real) {
    refresh(first, passed);
    for (uint32_t s = 0; s < kMaxStreams; s++) {
      if (!vb[s]) continue;
      Vb &v = *vb[s];
      uint32_t lo = 0, hi = v.size;
      if (expectRange(s, first, real)) {
        const ull a = offset[s] + (ull)first * stride[s];
        ull e = offset[s] + (ull)(first + real - 1) * stride[s] + extent[s];   // the last vertex's last declared byte
        if (e > v.size) e = v.size;
        if (extent[s] == 0 || e <= a) continue;
        lo = (uint32_t)a; hi = (uint32_t)e;
      }
      recs.push_back({&v, v.cur, lo, hi, std::vector<uint8_t>(v.mirror.begin() + lo, v.mirror.begin() + hi)});
    }
  }
  void wholeDraw() { draw(kWhole, 0, 0); }
  // DrawPrimitive: the device passes vertexCount(), the draw reads trueVertices().
  void dp(uint32_t prim, uint32_t n, uint32_t first) { draw(first, vertexCount(prim, n), trueVertices(prim, n)); }
  // The GPU runs every recorded draw after the CPU is done: the worst case for an overwrite.
  bool verify(const char *what) {
    for (size_t i = 0; i < recs.size(); i++) {
      const Rec &r = recs[i];
      if (memcmp(r.vb->gens[r.gen].data() + r.lo, r.expect.data(), r.hi - r.lo)) {
        printf("FAIL %s: draw %zu of [%u, %u) in generation %u reads bytes it was not recorded with\n", what, i, r.lo, r.hi, r.gen);
        fails++;
        return false;
      }
    }
    return true;
  }
};

static void fill(Vb &v, std::mt19937 &rng) { v.lock(true); v.write(rng, 0, v.size); v.unlock(); }

static void units() {
  EXPECT(vertexCount(1, 5) == 5 && vertexCount(2, 5) == 10 && vertexCount(3, 5) == 6 && vertexCount(4, 5) == 15 &&
         vertexCount(5, 5) == 7 && vertexCount(6, 5) == 7, "vertex counts: points N, lines 2N, line strip N+1, triangles 3N, strip and fan N+2");
  EXPECT(vertexCount(0, 5) == 0 && vertexCount(7, 5) == 0 && vertexCount(99, 1) == 0, "vertex count of an unknown type is 0");
  EXPECT(vertexCount(4, 0xffffffffu) == 3ull * 0xffffffffull && vertexCount(2, 0xffffffffu) == 2ull * 0xffffffffull, "vertex count does not wrap at 32 bits");
  for (uint32_t p = 1; p <= 6; p++) for (uint32_t n = 1; n < 40; n++) EXPECT(vertexCount(p, n) == trueVertices(p, n), "vertex count %u %u", p, n);
  uint32_t lo = 7, hi = 7;
  EXPECT(streamRange(2, 3, 100, 32, 4096, lo, hi) && lo == 164 && hi == 260, "range: offset + first*stride to offset + (first+count)*stride");
  lo = hi = 7;
  EXPECT(!streamRange(0, 3, 0, 0, 4096, lo, hi) && lo == 7 && hi == 7, "stride 0: no range, outputs untouched");
  EXPECT(!streamRange(0, 0, 0, 32, 4096, lo, hi), "no vertices: no range");
  EXPECT(!streamRange(1000, 3, 0, 32, 4096, lo, hi) && !streamRange(0, 3, 4096, 32, 4096, lo, hi), "starting at or past the end: no range");
  EXPECT(streamRange(120, 100, 0, 32, 4096, lo, hi) && lo == 3840 && hi == 4096, "clipped to the buffer");
  EXPECT(streamRange(0, 1ull << 40, 0, 32, 1000, lo, hi) && lo == 0 && hi == 1000, "a huge count is cut to the buffer without overflow");
  EXPECT(!streamRange(0xfffffff0u, 0xffffffffffull, 0xffffffffu, 0xffffffffu, 0xffffffffu, lo, hi), "the largest 32-bit factors: no range, no overflow");
  EXPECT(streamRange(0, 0xffffffffffull, 0, 1, 0xffffffffu, lo, hi) && lo == 0 && hi == 0xffffffffu, "the largest buffer");
  uint32_t one[kMaxStreams], two[kMaxStreams];
  for (uint32_t s = 0; s < kMaxStreams; s++) { one[s] = 1; two[s] = 1; }
  EXPECT(defaultFrequencies(one, kMaxStreams), "frequencies 1: default");
  two[9] = 0x40000001u;
  EXPECT(!defaultFrequencies(two, kMaxStreams), "an indexed-data frequency on any stream is not the default");
  two[9] = 0x80000001u;
  EXPECT(!defaultFrequencies(two, kMaxStreams), "an instance-data frequency on any stream is not the default");
  two[9] = 2;
  EXPECT(!defaultFrequencies(two, kMaxStreams), "a plain divider is not the default");
  two[9] = 0;
  EXPECT(!defaultFrequencies(two, kMaxStreams), "a zero frequency is not the default");

  // planDraw, case by case.
  int a, b;
  StreamIn in[kMaxStreams];
  StreamPlan plan[kMaxStreams];
  auto fresh = [&] { for (StreamIn &i : in) i = {nullptr, 0, 0, 0, 0}; in[0] = {&a, 16, 32, 28, 4096}; in[1] = {&b, 0, 16, 16, 2048}; };
  fresh();
  planDraw(10, 6, true, one, in, plan);
  EXPECT(plan[0].range && plan[0].lo == 16 + 320 && plan[0].hi == 16 + 512 && plan[1].range && plan[1].lo == 160 && plan[1].hi == 256 && !plan[2].range,
         "plan: every bound stream gets its own range, an unbound one nothing");
  planDraw(kWhole, 0, true, one, in, plan);
  EXPECT(!plan[0].range && !plan[1].range, "plan: no range given (indexed, user pointer): whole");
  planDraw(10, 0, true, one, in, plan);
  EXPECT(!plan[0].range, "plan: no vertices: whole");
  planDraw(10, 6, false, one, in, plan);
  EXPECT(!plan[0].range && !plan[1].range, "plan: no vertex declaration: whole");
  planDraw(10, 6, true, two, in, plan);
  EXPECT(!plan[0].range && !plan[1].range, "plan: a frequency other than 1 on any stream: whole on all");
  fresh(); in[0].stride = 0;
  planDraw(10, 6, true, one, in, plan);
  EXPECT(!plan[0].range && plan[1].range, "plan: a stride of 0 makes that stream whole, the other keeps its range");
  fresh(); in[0].extent = 33;
  planDraw(10, 6, true, one, in, plan);
  EXPECT(!plan[0].range && plan[1].range, "plan: an element reaching past the stride: that stream whole");
  fresh(); in[0].extent = 32;
  planDraw(10, 6, true, one, in, plan);
  EXPECT(plan[0].range, "plan: an element ending exactly at the stride is fine");
  fresh(); in[2] = {&a, 0, 32, 32, 4096};
  planDraw(10, 6, true, one, in, plan);
  EXPECT(!plan[0].range && !plan[2].range && plan[1].range, "plan: one buffer on two streams: both whole, the others keep their ranges");
  fresh();
  planDraw(0xffffffffu, 6, true, one, in, plan);
  EXPECT(!plan[0].range, "plan: StartVertex 0xffffffff is the no-range value: whole");
  planDraw(5000, 6, true, one, in, plan);
  EXPECT(!plan[0].range && !plan[1].range, "plan: a range outside the buffer: whole");

  // onRangeDraw: a buffer that is not dirty stores nothing and records the read.
  RangeState rs;
  EXPECT(onRangeDraw(rs, false, false, 100, 200) == Action::Skip && rs.reads.n == 1 && rs.reads.lo[0] == 100 && rs.reads.hi[0] == 200,
         "a current buffer: skip, the read recorded");
  EXPECT(onRangeDraw(rs, true, false, 100, 150) == Action::RenameStore, "a dirty buffer: the state machine (bytes read before: rename)");
  EXPECT(onRangeDraw(rs, true, false, 300, 350) == Action::Store, "a dirty buffer: the state machine (fresh bytes: store)");
}

// Resident Evil 4: one dynamic vertex buffer, 210 locks a frame of 1 to 256 bytes, a DrawPrimitive after each, the offset rising.
static void re4(uint32_t size, int frames, const char *name, bool print) {
  std::mt19937 rng(686);
  Vb v(size, true, false);                           // DEFAULT + DYNAMIC: dirty from the first lock on
  Dev d(true);
  d.bind(0, &v, 0, 32, 32);
  const uint32_t prims[] = {1, 2, 3, 4, 5, 6};
  const uint32_t maxn[] = {8, 4, 5, 2, 6, 6};
  uint32_t off = 0, draws = 0, wraps = 0;
  ull drawn = 0;
  for (int f = 0; f < frames; f++) {
    for (int i = 0; i < 210; i++) {
      uint32_t k = rng() % 6, n = 1 + rng() % maxn[k];
      uint64_t nv = trueVertices(prims[k], n);
      uint32_t bytes = (uint32_t)(nv * 32);
      if (off + bytes > size) { off = 0; wraps++; }
      v.lock(true);                                  // NOOVERWRITE (DISCARD at the wrap): any write lock is the same
      v.write(rng, off, off + bytes);
      v.unlock();
      d.dp(prims[k], n, off / 32);
      drawn += bytes; draws++;
      off += bytes;
    }
  }
  d.verify(name);
  if (print)
    printf("%s: %u draws, %u wraps: %llu bytes stored (the whole-mirror refresh: %llu), %llu renames, %llu whole copies, %llu already stored\n",
           name, draws, wraps, v.bytes, (ull)draws * size, v.renames, v.wholes, v.skips);
  EXPECT(v.wholes == 0, "%s: no whole copy", name);
  EXPECT(v.bytes == drawn, "%s: only the vertices the draws read are stored (%llu of %llu)", name, v.bytes, drawn);
  EXPECT(v.rangeStores() == draws && v.skips == 0, "%s: one range store per draw", name);
  EXPECT(v.renames == wraps, "%s: a rename only where a rewrite meets bytes a draw read, the wrap (%llu renames, %u wraps)", name, v.renames, wraps);
}

static void re4Off() {
  // The same pattern with the key off: the old refresh, a whole copy and a rename per draw after a lock.
  std::mt19937 rng(686);
  const uint32_t size = 3932160;
  Vb v(size, false, false);
  Dev d(false);
  d.bind(0, &v, 0, 32, 32);
  for (int i = 0; i < 12; i++) { v.lock(true); v.write(rng, i * 96, i * 96 + 96); v.unlock(); d.dp(4, 1, i * 3); }
  EXPECT(v.wholes == 12 && v.bytes == 12ull * size && v.rangeStores() == 0 && v.skips == 0, "key off: a whole copy per draw (%llu)", v.bytes);
  EXPECT(v.st.reads.n == 0 && v.st.clean.n == 0, "key off: the range state is never touched");
}

static void rewrite() {
  std::mt19937 rng(3);
  Vb v(4096, true, false);
  Dev d(true);
  d.bind(0, &v, 0, 32, 32);
  fill(v, rng);
  d.dp(4, 3, 0);                                     // A reads [0, 288)
  EXPECT(v.stores == 1 && v.bytes == 288 && v.renames == 0 && v.wholes == 0, "A stores its 288 bytes, nothing else");
  v.lock(true); v.write(rng, 0, 288); v.unlock();    // the application rewrites what A read
  d.dp(4, 3, 0);                                     // B reads the same bytes
  EXPECT(v.renames == 1 && v.storesRenamed == 1 && v.bytes == 576 && v.wholes == 0,
         "a rewrite of bytes an earlier draw read renames, and the fresh generation takes the range only (renames %llu, bytes %llu)", v.renames, v.bytes);
  EXPECT(v.cur == 1 && memcmp(v.gens[0].data(), v.gens[1].data(), 288) != 0, "the earlier draw keeps the old bytes in its generation");
  v.lock(true); v.write(rng, 1000, 1096); v.unlock();
  d.dp(4, 1, 1000 / 32 + 1);                         // elsewhere in the fresh generation: no rename
  EXPECT(v.renames == 1, "bytes nobody read are stored in place");
  d.verify("rewrite");
}

static void secondDraw() {
  std::mt19937 rng(4);
  Vb v(4096, true, false);
  Dev d(true);
  d.bind(0, &v, 0, 32, 32);
  fill(v, rng);
  d.dp(4, 3, 0);                                     // [0, 288)
  d.dp(4, 3, 0);                                     // the same again: nothing to store
  d.dp(1, 4, 2);                                     // inside it: nothing to store
  EXPECT(v.stores == 1 && v.skips == 2 && v.bytes == 288, "a second draw over stored bytes stores nothing (stores %llu skips %llu)", v.stores, v.skips);
  d.dp(4, 3, 9);                                     // [288, 576), next to it
  d.dp(1, 18, 0);                                    // [0, 576): the two merged
  EXPECT(v.stores == 2 && v.skips == 3 && v.bytes == 576 && v.renames == 0, "an adjacent range stores only itself; the union is covered");
  d.verify("second draw");
}

static void discardAndLocks() {
  std::mt19937 rng(5);
  Vb v(8192, true, false);
  Dev d(true);
  d.bind(0, &v, 0, 32, 32);
  v.lock(true); v.write(rng, 0, 1024); v.unlock();   // NOOVERWRITE
  d.dp(1, 32, 0);                                    // [0, 1024)
  d.dp(1, 32, 0);                                    // covered
  EXPECT(v.skips == 1 && v.renames == 0, "before the lock: covered");
  v.lock(true); v.write(rng, 0, 512); v.unlock();    // DISCARD: the application starts over (the flags change nothing here)
  d.dp(1, 16, 0);                                    // [0, 512): an earlier draw read these bytes
  EXPECT(v.renames == 1 && v.skips == 1, "after a DISCARD lock the bytes an earlier draw read are not stored in place");
  d.dp(1, 16, 16);                                   // [512, 1024): written by nobody since, but the lock means any stored byte may change
  EXPECT(v.skips == 1 && v.stores + v.storesRenamed == 3, "after a write lock stored bytes are stored again (a draw is not covered by what was stored before the lock)");
  v.lock(false);                                     // a read-only lock changes nothing
  v.unlock();
  d.dp(1, 16, 16);
  EXPECT(v.skips == 2, "a read-only lock does not forget the stored bytes");
  // a lock held across the draws: every draw stores its range, none is skipped
  v.lock(true); v.write(rng, 2048, 2048 + 128);
  d.dp(1, 4, 64); d.dp(1, 4, 64);
  EXPECT(v.skips == 2, "while the buffer is still locked a draw is never skipped");
  v.write(rng, 2048, 2048 + 128);
  d.dp(1, 4, 64);
  v.unlock();
  d.verify("discard");
}

static void whole() {
  struct Case { const char *what; void (*setup)(Dev &, Vb &, Vb &); };
  static const Case cases[] = {
    {"instancing (instance data on stream 1)", [](Dev &d, Vb &, Vb &) { d.freq[1] = 0x80000001u; }},
    {"instancing (indexed data on stream 0)", [](Dev &d, Vb &, Vb &) { d.freq[0] = 0x40000004u; }},
    {"a plain frequency divider", [](Dev &d, Vb &, Vb &) { d.freq[3] = 2; }},
    {"stride 0", [](Dev &d, Vb &a, Vb &) { d.bind(0, &a, 0, 0, 32); }},
    {"a declaration element past the stride", [](Dev &d, Vb &a, Vb &) { d.bind(0, &a, 0, 32, 36); }},
    {"no vertex declaration", [](Dev &d, Vb &, Vb &) { d.declared = false; }},
    {"one buffer on two streams", [](Dev &d, Vb &a, Vb &) { d.bind(1, &a, 16, 32, 32); }},
    {"a range outside the buffer", [](Dev &d, Vb &a, Vb &) { d.bind(0, &a, 100000, 32, 32); }},
  };
  for (const Case &c : cases) {
    std::mt19937 rng(7);
    Vb a(4096, true, false), b(2048, true, false);
    Dev d(true);
    d.bind(0, &a, 0, 32, 32);
    fill(a, rng);
    c.setup(d, a, b);
    d.dp(4, 3, 0);
    EXPECT(a.wholes == 1 && a.bytes == 4096 && a.rangeStores() == 0 && a.skips == 0, "%s: the whole mirror, once (wholes %llu, range stores %llu)", c.what, a.wholes, a.rangeStores());
    d.dp(4, 3, 3);
    EXPECT(a.wholes == 1 && a.bytes == 4096, "%s: nothing more to copy until the next lock", c.what);
    d.verify(c.what);
  }
  {
    // draws that give no range: indexed, user pointer (and ProcessVertices' draws)
    std::mt19937 rng(8);
    Vb a(4096, true, false);
    Dev d(true);
    d.bind(0, &a, 0, 32, 32);
    fill(a, rng);
    d.wholeDraw();
    EXPECT(a.wholes == 1 && a.rangeStores() == 0, "no range given (indexed, user pointer): the whole mirror");
    EXPECT(a.st.reads.n == 1 && a.st.reads.lo[0] == 0 && a.st.reads.hi[0] == 4096, "...and the whole buffer counts as read");
    a.lock(true); a.write(rng, 0, 64); a.unlock();
    d.dp(1, 2, 0);                                   // a range draw after it: the bytes were read, so it renames
    EXPECT(a.storesRenamed == 1 && a.renames == 2 && a.bytes == 4096 + 64, "a range draw after a whole draw of the generation renames and stores its range");
    d.verify("whole draw");
  }
  {
    // an unknown primitive type, and StartVertex 0xffffffff
    std::mt19937 rng(9);
    Vb a(4096, true, false);
    Dev d(true);
    d.bind(0, &a, 0, 32, 32);
    fill(a, rng);
    d.draw(0, vertexCount(99, 4), 0);
    EXPECT(a.wholes == 1 && a.rangeStores() == 0, "an unknown primitive type: the whole mirror");
    Vb b(4096, true, false);
    Dev e(true);
    e.bind(0, &b, 0, 32, 32);
    fill(b, rng);
    e.draw(0xffffffffu, 6, 6);
    EXPECT(b.wholes == 1 && b.rangeStores() == 0, "StartVertex 0xffffffff: the whole mirror");
    d.verify("unknown type"); e.verify("start vertex");
  }
  {
    // the key off: draws with every kind of arguments take the old refresh
    std::mt19937 rng(10);
    Vb a(4096, false, false);
    Dev d(false);
    d.bind(0, &a, 0, 32, 32);
    fill(a, rng);
    d.dp(4, 3, 0);
    EXPECT(a.wholes == 1 && a.rangeStores() == 0 && a.st.reads.n == 0 && a.st.clean.n == 0, "key off: the whole mirror, the state untouched");
    d.verify("key off");
  }
}

static void sharedBuffer() {
  // One buffer on two streams with different, overlapping ranges: stored as one whole refresh, not as two ranges (the second range's
  // rename would drop the first).
  std::mt19937 rng(11);
  Vb a(4096, true, false);
  Dev d(true);
  d.bind(0, &a, 0, 32, 32);
  d.bind(1, &a, 16, 32, 32);
  fill(a, rng);
  d.dp(4, 3, 0);
  EXPECT(a.wholes >= 1 && a.rangeStores() == 0, "a buffer on two streams takes the whole refresh (wholes %llu, range stores %llu)", a.wholes, a.rangeStores());
  d.verify("shared");
}

static void streams() {
  // two buffers, two strides: each gets its own range
  std::mt19937 rng(12);
  Vb a(4096, true, false), b(2048, true, false);
  Dev d(true);
  d.bind(0, &a, 0, 32, 28);
  d.bind(1, &b, 8, 16, 12);
  fill(a, rng); fill(b, rng);
  d.dp(4, 2, 5);                                     // 6 vertices from 5
  EXPECT(a.stores == 1 && a.bytes == 6 * 32 && b.stores == 1 && b.bytes == 6 * 16 && a.wholes == 0 && b.wholes == 0,
         "two streams: each stores its own range (%llu, %llu)", a.bytes, b.bytes);
  d.wholeDraw();
  EXPECT(a.wholes == 1 && b.wholes == 1, "a draw without a range refreshes both whole");
  d.verify("streams");
}

static void fullSet() {
  // 40 draws over gaps, no lock between: the interval set (32) fills, one whole copy takes over, the rest are covered.
  std::mt19937 rng(13);
  const uint32_t size = 65536;
  Vb v(size, true, false);
  Dev d(true);
  d.bind(0, &v, 0, 32, 32);
  fill(v, rng);
  for (uint32_t i = 0; i < 40; i++) d.dp(1, 1, i * 2);
  EXPECT(v.wholes == 1 && v.stores == 32 && v.skips == 7 && v.bytes == 32 * 32 + size,
         "interval set full: 32 ranges, then one whole copy, then covered (wholes %llu, stores %llu, skips %llu)", v.wholes, v.stores, v.skips);
  d.verify("full set");
}

static void defaultPool() {
  // A DEFAULT buffer nobody has written: a draw of it stores nothing (as the whole refresh does) but its read is recorded, so the first
  // write of those bytes after it renames.
  std::mt19937 rng(14);
  Vb v(4096, true, false);
  Dev d(true);
  d.bind(0, &v, 0, 32, 32);
  d.dp(1, 2, 0);
  EXPECT(v.bytes == 0 && v.skips == 1 && v.renames == 0, "a buffer not dirty: nothing stored");
  v.lock(true); v.write(rng, 0, 64); v.unlock();
  d.dp(1, 2, 0);
  EXPECT(v.renames == 1 && v.storesRenamed == 1, "the first draw's read was recorded: the rewrite renames (renames %llu)", v.renames);
  d.verify("default pool");
  // a pool other than DEFAULT is dirty from the start: the first draw stores its range
  Vb m(4096, true, true);
  Dev e(true);
  e.bind(0, &m, 0, 32, 32);
  e.dp(1, 2, 3);
  EXPECT(m.stores == 1 && m.bytes == 64 && m.wholes == 0, "a managed buffer: the first draw stores its range");
  e.verify("managed");
}

static void keyOff() {
  // Random lock / write / draw on one buffer with the key off against OldVb, the vertex buffer as it is without the patch: the same
  // generations, bytes and renames.
  for (int run = 0; run < 200; run++) {
    std::mt19937 rng(1000 + run);
    const uint32_t size = 64 + (uint32_t)(rng() % 4096);
    Vb v(size, false, false);
    OldVb o(size);
    Dev d(false);
    d.bind(0, &v, 0, 32, 32);
    for (int op = 0; op < 300; op++) {
      uint32_t k = rng() % 100;
      if (k < 20) { bool w = rng() % 4 != 0; v.lock(w); o.lock(w); }
      else if (k < 35) { v.unlock(); o.unlock(); }
      else if (k < 55) {
        if (v.writeLocked) {
          uint32_t a = rng() % size, b2 = a + 1 + rng() % (size - a);
          v.write(rng, a, b2);
          for (uint32_t i = a; i < b2; i++) o.mirror[i] = v.mirror[i];
        }
      } else {
        uint32_t first = rng() % 100;
        if (rng() % 3 == 0) d.wholeDraw(); else d.dp(4, 1 + rng() % 5, first);
        o.refreshWholeMirror();
      }
    }
    bool same = v.gens.size() == o.gens.size() && v.bytes == o.bytes && v.renames == o.renames && v.cur == o.cur && v.dirty == o.dirty;
    for (size_t g = 0; same && g < v.gens.size(); g++) same = v.gens[g] == o.gens[g];
    EXPECT(same && v.st.reads.n == 0 && v.st.clean.n == 0 && v.rangeStores() == 0, "key off: run %d is the old refresh byte for byte", run);
    if (!same) return;
  }
  printf("keyoff-ok\n");
}

static void randomRuns() {
  std::mt19937 rng(686);
  ull draws = 0, ranges = 0, wholes = 0, renames = 0;
  for (int run = 0; run < 400; run++) {
    const uint32_t nvb = 1 + rng() % 3;
    std::vector<std::unique_ptr<Vb>> own;
    for (uint32_t i = 0; i < nvb; i++) own.emplace_back(new Vb(1024 + rng() % 8192, true, rng() % 2));
    Dev d(true);
    const uint32_t ns = 1 + rng() % 3;
    for (uint32_t s = 0; s < ns; s++) {
      uint32_t stride = rng() % 12 == 0 ? 0 : 8 + (rng() % 8) * 8;
      uint32_t extent = rng() % 10 == 0 ? stride + 4 : (stride ? 1 + rng() % stride : 0);
      d.bind(s * 3, own[rng() % nvb].get(), rng() % 4 == 0 ? rng() % 64 : 0, stride, extent);
    }
    d.declared = rng() % 20 != 0;
    for (int op = 0; op < 400; op++) {
      Vb &v = *own[rng() % nvb];
      uint32_t k = rng() % 100;
      if (k < 10) v.lock(rng() % 4 != 0);
      else if (k < 20) v.unlock();
      else if (k < 40) { if (v.writeLocked) { uint32_t a = rng() % v.size, b = a + 1 + rng() % (v.size - a); v.write(rng, a, b); } }
      else if (k < 46) {                              // instancing now and then, and back to the default
        if (rng() % 3) { for (uint32_t &f : d.freq) f = 1; }
        else d.freq[rng() % kMaxStreams] = rng() % 2 ? 0x80000001u : 0x40000002u;
      }
      else if (k < 53) { d.wholeDraw(); draws++; }
      else {
        const uint32_t prim = 1 + rng() % 6, n = 1 + rng() % 12;
        d.dp(prim, n, rng() % 8 == 0 ? rng() % 200 : rng() % 24);
        draws++;
      }
    }
    for (auto &p : own) { ranges += p->rangeStores(); wholes += p->wholes; renames += p->renames; }
    if (!d.verify("random")) { printf("     run %d\n", run); return; }
  }
  printf("random-ok %llu draws, %llu range stores, %llu whole copies, %llu renames\n", draws, ranges, wholes, renames);
}

int main() {
  units();
  re4(3932160, 6, "re4", true);
  re4(65536, 12, "re4-wrap", true);
  re4Off();
  rewrite();
  secondDraw();
  discardAndLocks();
  whole();
  sharedBuffer();
  streams();
  fullSet();
  defaultPool();
  keyOff();
  randomRuns();
  if (!fails) puts("vb-range-ok");
  return fails != 0;
}
'''
            (d / 'h.cpp').write_text(harness)
            san = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-omit-frame-pointer']
            bld = subprocess.run([cxx, '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-missing-field-initializers'] + san +
                                 ['-I', str(d), '-o', str(d / 'h'), str(d / 'h.cpp')], capture_output=True, text=True)
            check('d3d9_vb_range.hpp compiles on the host (C++17, -Wall -Wextra -Werror, ASan + UBSan)', bld.returncode == 0, bld.stderr)
            if bld.returncode == 0:
                env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
                res = subprocess.run([str(d / 'h')], capture_output=True, text=True, timeout=900, env=env)
                for l in res.stdout.splitlines():
                    if l.startswith(('re4', 'random-ok')):
                        print('     ' + l)
                check('the model: bookkeeping units, the Resident Evil 4 pattern (no rename, no whole copy, only the vertices read), a rewrite, a '
                      'second draw, DISCARD and other locks, every whole-refresh case, the interval set full, the key off byte for byte, 400 '
                      'random runs over up to three streams',
                      res.returncode == 0 and 'vb-range-ok' in res.stdout and 'keyoff-ok' in res.stdout and 'random-ok' in res.stdout,
                      res.stdout[-1500:] + res.stderr[-1500:])
                # The model has teeth: the same harness on bookkeeping that is broken one way at a time must fail.
                muts = [
                    ('a strip or fan of N + 1 vertices', H, 'case kTriangleStrip:\n  case kTriangleFan:\n    return n + 2;',
                     'case kTriangleStrip:\n  case kTriangleFan:\n    return n + 1;'),
                    ('the range one vertex short', H, 'return d9ib::clampRange(first_byte, first_byte + n * stride, size, lo, hi);',
                     'return d9ib::clampRange(first_byte, first_byte + (n > 1 ? n - 1 : n) * stride, size, lo, hi);'),
                    ('instancing trusted', H, ' && defaultFrequencies(stream_freq, kMaxStreams);', ';'),
                    ('one buffer on two streams trusted', H, 'if (shared)\n      continue;', 'if (false)\n      continue;'),
                    ('an element past the stride trusted', H, ' || in[s].extent > in[s].stride)', ')'),
                    ('the read of a current buffer not recorded', H, '    state.reads.addOrHull(lo, hi);\n    return d9ib::Action::Skip;',
                     '    return d9ib::Action::Skip;'),
                    ('no rename before an overlapping store', H_IB, 'if (reads.overlaps(lo, hi))\n      return Action::RenameStore;\n', ''),
                    ('stored bytes trusted across a write lock', H_IB, 'onWriteLock() {\n    clean.clear();\n  }', 'onWriteLock() {\n  }'),
                ]
                for what, text, a, b2 in muts:
                    hm = text.replace(a, b2)
                    m = d / 'mut'
                    shutil.rmtree(m, ignore_errors=True)
                    m.mkdir()
                    for hn in ('d3d9_vb_range.hpp', 'd3d9_ib_range.hpp'):
                        (m / hn).write_text((H if hn == 'd3d9_vb_range.hpp' else H_IB))
                    (m / ('d3d9_vb_range.hpp' if text is H else 'd3d9_ib_range.hpp')).write_text(hm)
                    (m / 'h.cpp').write_text(harness)   # a quoted include looks beside the source first
                    mb = subprocess.run([cxx, '-std=c++17', '-O1', '-Wno-everything'] + san + ['-I', str(m), '-o', str(m / 'h'), str(m / 'h.cpp')],
                                        capture_output=True, text=True)
                    mr = subprocess.run([str(m / 'h')], capture_output=True, text=True, timeout=900, env=env) if mb.returncode == 0 else None
                    check('broken bookkeeping (%s) fails the model' % what,
                          hm != text and mr is not None and mr.returncode != 0 and 'FAIL' in mr.stdout,
                          (mb.stderr if mb.returncode else mr.stdout[-600:]))

        # ---------------------------------------------------------------- the device's side, run: RefreshVertexBuffersForDraw is cut out of the patched
        # d3d9_device.cpp and compiled against fakes of the vertex buffer, the declaration and the device's stream arrays (the real Direct3D headers
        # and the real decl_element_byte_size: needs the dxmt tree's include/native, as tests/host/check-dxmt-d9-direct-map.py does)
        if cxx:
            dxroot = src.parents[1]
            inc_dx, inc_win = dxroot / 'include/native/directx', dxroot / 'include/native/windows'
            pv, mx = src / 'd3d9_process_vertices.hpp', src / 'd3d9_matrix.hpp'
            start = dv.find('MTLD3D9Device::RefreshVertexBuffersForDraw(uint32_t vb_first, uint64_t vb_count) {')
            if not ((inc_dx / 'd3d9.h').is_file() and pv.is_file() and mx.is_file() and start >= 0):
                print('skip the device glue run: no Direct3D headers in the dxmt tree (include/native/directx)')
            else:
                start = dv.rfind('void\n', 0, start)
                fn = dv[start:dv.index('\n}\n', start) + 3]
                glue_harness = GLUE_HARNESS
                san2 = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined']
                flags = ['-std=c++20', '-O1', '-g', '-Wall', '-Wno-unused-function', '-Wno-unused-variable', '-Wno-unknown-pragmas', '-Wno-extern-c-compat',
                         '-Wno-microsoft-exception-spec', '-Wno-ignored-attributes', '-Wno-unused-parameter']
                inc = ['-I', str(d), '-I', str(inc_dx), '-I', str(inc_win), '-I', str(dxroot / 'include')]

                def build_glue(function_text, name):
                    g = d / name
                    g.mkdir(exist_ok=True)
                    for hn in ('d3d9_vb_range.hpp', 'd3d9_ib_range.hpp'):
                        shutil.copy(d / hn, g / hn)
                    shutil.copy(pv, g / pv.name)
                    shutil.copy(mx, g / mx.name)
                    (g / 'g.cpp').write_text(glue_harness.replace('FUNCTION_SLOT', function_text))
                    bb = subprocess.run([cxx] + flags + san2 + ['-I', str(g)] + inc[2:] + ['-o', str(g / 'g'), str(g / 'g.cpp')], capture_output=True, text=True)
                    return g, bb

                g, gb = build_glue(fn, 'glue')
                check('RefreshVertexBuffersForDraw, cut out of the patched d3d9_device.cpp, compiles on the host against the real Direct3D headers (ASan + UBSan)',
                      gb.returncode == 0, gb.stderr[-2500:])
                if gb.returncode == 0:
                    gr = subprocess.run([str(g / 'g')], capture_output=True, text=True, timeout=300, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
                    check('the device side behaves: ranges per stream from the declaration extents, strides and offsets; whole for a declaration past the stride, '
                          'a stride of 0, instancing, no declaration, no range, a range outside the buffer, one buffer on two streams',
                          gr.returncode == 0 and 'glue-ok' in gr.stdout, gr.stdout[-1500:] + gr.stderr[-1500:])
                    for what, a, b2 in (('the declaration extents ignored', 'if (end > in[e[i].Stream].extent)\n        in[e[i].Stream].extent = end;', ''),
                                        ('the whole buffer not marked read', 'vb->noteWholeRead();', ''),
                                        ('the stream frequencies not passed', 'd9vb::planDraw(vb_first, vb_count, declared, m_streamFreq, in, plan);',
                                         'uint32_t ones[D3D9_MAX_VERTEX_STREAMS]; for (uint32_t &o : ones) o = 1;\n  d9vb::planDraw(vb_first, vb_count, declared, ones, in, plan);')):
                        fm = fn.replace(a, b2)
                        gm, gmb = build_glue(fm, 'glue-mut')
                        mres = subprocess.run([str(gm / 'g')], capture_output=True, text=True, timeout=300, env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0')) if gmb.returncode == 0 else None
                        check('a broken device side (%s) fails the glue run' % what,
                              fm != fn and mres is not None and mres.returncode != 0 and 'FAIL' in mres.stdout, gmb.stderr[-800:] if gmb.returncode else (mres.stdout[-600:] if mres else ''))


# ---------------------------------------------------------------- 5. wiring outside DXMT
cat = (R / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the key (bool, default 0: off), with the file that reads it',
      re.search(r'"env\.DXMT_D9_VB_RANGE": \{[^}]*?"kind": "bool", "default": "0", "sources": \["tools/patch-dxmt-d9-vb-range\.py"\]', cat, re.S) is not None)
pol = (R / 'app/Madeira/GameRecommendationsList.swift').read_text()
allowed = pol.split('static let allowedEnvNames')[1].split(']')[0] if 'static let allowedEnvNames' in pol else ''
case_at = pol.find('case "DXMT_D9_VB_RANGE":')
case_body = pol[case_at:] if case_at >= 0 else ''
case_body = case_body[:case_body.find('\n            case ')]
check('a downloaded list may set it, 0 or 1 (RecommendationsPolicy)',
      '"DXMT_D9_VB_RANGE"' in allowed and case_at >= 0 and 'guard value == "0" || value == "1"' in case_body, case_body[:400])

print('PASS: DXMT D3D9 vertex-buffer range refresh' if ok else 'FAILED: DXMT D3D9 vertex-buffer range refresh')
sys.exit(0 if ok else 1)
