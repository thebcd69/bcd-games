#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: an indexed draw refreshes only the index bytes it reads (round 624; on by default, env DXMT_D9_IB_RANGE=0 turns it off).

Call of Duty: World at War (K8K2D3) locks its ~1 MB dynamic index buffer ~920 times a frame and the whole-mirror refresh copied the
whole buffer for every draw after a lock (~0.9 GB a frame through a translated rep movs). tools/patch-dxmt-d9-ib-range.py stores
only the bytes a draw reads, renaming first when an earlier draw of the generation read any of them (d3d9_ib_range.hpp).

This check:
  1. applies the patch to a copy of the pinned dxmt/src/d3d9 (or $DXMT_SRC_DIR) twice (the second run says "already patched"), and a
     moved anchor fails by name with no file changed;
  2. reads the result: the switch (on unless env DXMT_D9_IB_RANGE == "0", read once; the owner's decision of 2026-10-30), the dispatch in BuildDrawCapture (key off: the old
     refresh for every draw), the ordinary DrawIndexedPrimitive range, the seven draws that do not read the bound index buffer, the
     one UP draw that keeps the whole refresh, the rename fallbacks, the census line;
  3. compiles d3d9_ib_range.hpp on the host and runs it: the interval set, then random lock / write / draw sequences against a model
     of the GPU generations (every draw's bytes, checked after everything ran, must be what the mirror held when it was recorded:
     no store overwrites bytes a draw of its generation reads, no skipped store leaves stale bytes), then the copy volume of a ring
     (Call of Duty) and of a static buffer drawn in many pieces (one whole copy, then nothing);
  4. the wiring: the CI patch step and this check, the settings catalog, the list policy.
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


script = R / 'tools/patch-dxmt-d9-ib-range.py'
check('the patch script exists', script.exists())
wf = (R / '.github/workflows/build-ipa.yml').read_text()
check('CI applies it', 'python3 tools/patch-dxmt-d9-ib-range.py' in wf)
check('CI runs this check', 'python3 tests/host/check-dxmt-d9-ib-range.py' in wf)
if 'python3 tools/patch-dxmt-d9-ib-range.py' in wf:
    at = wf.index('python3 tools/patch-dxmt-d9-ib-range.py')
    check('the patch step runs before the i386 farm and the 64-bit Direct3D 9 build (both compile the patched tree)',
          at < wf.index('id: i386rev') and at < wf.index('bash tools/build-d3d9-x64-dll.sh'))

FILES = ['d3d9_buffer.hpp', 'd3d9_buffer.cpp', 'd3d9_device.hpp', 'd3d9_device.cpp', 'd3d9_census.hpp', 'd3d9_census.cpp']
src = pathlib.Path(os.environ.get('DXMT_SRC_DIR', R / 'dxmt')) / 'src' / 'd3d9'
cxx = shutil.which('c++') or shutil.which('clang++')
if not all((src / f).exists() for f in FILES):
    print('skip the patch run: no dxmt sources (set DXMT_SRC_DIR)')
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        d = t / 'dxmt/src/d3d9'
        d.mkdir(parents=True)
        for f in FILES:
            shutil.copy(src / f, d / f)
        if (src / 'd3d9_ib_range.hpp').exists():
            shutil.copy(src / 'd3d9_ib_range.hpp', d / 'd3d9_ib_range.hpp')
        r1 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, str(script)], cwd=t, capture_output=True, text=True)
        # In CI the workflow's patch step has already run on dxmt/src, so the first run may say "already patched" too.
        check('it applies to the pinned files (or finds them patched) and a second run says "already patched"',
              r1.returncode == 0 and r2.returncode == 0 and 'already patched' in r2.stdout and ('patched:' in r1.stdout or 'already patched' in r1.stdout),
              r1.stdout + r1.stderr + r2.stdout + r2.stderr)
        T = {f: (d / f).read_text() for f in FILES}
        H = (d / 'd3d9_ib_range.hpp').read_text() if (d / 'd3d9_ib_range.hpp').exists() else ''
        check('every file carries the marker and the header exists', all('madeira-bcd: DXMT_D9_IB_RANGE' in T[f] for f in FILES) and H)

        b = T['d3d9_buffer.cpp']
        sw = b[b.index('d9ib::rangeRefreshEnabled() {'):] if 'd9ib::rangeRefreshEnabled() {' in b else ''
        sw = sw[:sw.index('\n}\n')] if sw else ''
        check('the switch: on unless env DXMT_D9_IB_RANGE is "0" (the default since 2026-10-30), read once (a static), said once',
              'static const bool on' in sw and 'env::getEnvVar("DXMT_D9_IB_RANGE") != "0"' in sw and '[ib-range] r624 on:' in sw
              and '[ib-range] r624 off (DXMT_D9_IB_RANGE=0)' in sw, sw)
        rr = b[b.index('MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {'):] if 'MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {' in b else ''
        rr = rr[:rr.index('\n}\n')] if rr else ''
        check('refreshRange acts on the state machine: Skip stores nothing, Whole takes the whole-mirror refresh, RenameStore renames '
              'with the whole path\'s fallbacks, the store is the range only, the state learns it, the buffer stays dirty',
              'm_range.onRangeDraw(lo, hi, m_writeLocked)' in rr and 'refreshWholeMirror();' in rr and 'drainForDynamicRename()' in rr
              and 'noteRenameInPlace()' in rr and 'updateContents(\n      lo, static_cast<const char *>(m_hostPtr) + lo, hi - lo,' in rr
              and 'm_range.onRangeStored(lo, hi, renamed, m_writeLocked);' in rr and rr.rstrip().endswith(
                  'census::bufferRefresh(renamed ? census::kRefreshIbRangeRenamed : census::kRefreshIbRange, hi - lo);'), rr)
        whole = b[b.index('MTLD3D9IndexBuffer::refreshWholeMirror() {'):]
        whole = whole[:whole.index('\n}\n')]
        check('the whole-mirror refresh is as before, plus the census count and, with the key, the state of a whole generation',
              whole.count('m_dynamic->immediateName()->updateContents(0, m_hostPtr, m_size, m_dynamic->immediateSuballocation());') == 1
              and 'm_dirty = m_writeLocked;\n  // madeira-bcd: DXMT_D9_IB_RANGE. The fresh generation holds the whole mirror.\n'
                  '  if (d9ib::rangeRefreshEnabled())\n    m_range.onWholeStored(m_writeLocked, m_size);' in whole, whole[-600:])
        lock = b[b.index('MTLD3D9IndexBuffer::Lock('):b.index('MTLD3D9IndexBuffer::Unlock()')]
        check('a write lock of an index buffer forgets which stored bytes were current', 'm_range.onWriteLock();' in lock)

        dv = T['d3d9_device.cpp']
        cap = dv[dv.index('MTLD3D9Device::BuildDrawCapture(uint32_t ib_lo, uint32_t ib_hi) {'):]
        cap = cap[:cap.index('// POD state read by Resolve')]
        check('BuildDrawCapture: key off, or no range, the whole refresh as before (and with the key the whole buffer counts as read); a '
              'range goes to refreshRange; a draw that does not read the bound index buffer leaves it alone',
              'if (!d9ib::rangeRefreshEnabled() || ib_lo == d9ib::kWhole) {\n      m_indexBuffer->refreshWholeMirror();\n'
              '      if (d9ib::rangeRefreshEnabled())\n        m_indexBuffer->noteWholeRead();\n    } else if (ib_lo != d9ib::kNone) {\n'
              '      m_indexBuffer->refreshRange(ib_lo, ib_hi);' in cap, cap)
        dip = dv[dv.index('MTLD3D9Device::DrawIndexedPrimitive('):]
        dip = dip[:dip.index('\n}\n')]
        check('DrawIndexedPrimitive\'s ordinary draw passes the bytes it reads: StartIndex * index size to (StartIndex + count) * index size, '
              'clamped to the buffer', 'static_cast<uint64_t>(StartIndex) * isz, (static_cast<uint64_t>(StartIndex) + index_count) * isz,' in dip
              and 'draw.cap = BuildDrawCapture(ib_lo, ib_hi);' in dip and 'const uint64_t isz = m_indexBuffer->indexFormat() == D3DFMT_INDEX32 ? 4u : 2u;' in dip)
        check('seven draws that do not read the bound index buffer on the GPU skip its refresh (DrawPrimitive x2, the fan and sentinel '
              'draws of DrawIndexedPrimitive, DrawPrimitiveUP x2, the fan of DrawIndexedPrimitiveUP)',
              dv.count('BuildDrawCapture(d9ib::kNone, d9ib::kNone)') == 7)
        dipup = dv[dv.index('MTLD3D9Device::DrawIndexedPrimitiveUP('):]
        dipup = dipup[:dipup.index('\n}\n')]
        check('DrawIndexedPrimitiveUP\'s plain draw keeps the whole refresh (its own index handle is not checked before the draw)',
              dipup.count('BuildDrawCapture(d9ib::kNone, d9ib::kNone)') == 1 and '  draw.cap = BuildDrawCapture();\n' in dipup)
        check('every replaced draw is non-indexed or binds its own index buffer, which the fan / sentinel / UP-fan paths check for 0 first',
              all(dv[m.end():m.end() + 900].count('override_ib_buffer') or 'kNonIndexed' in dv[m.end():m.end() + 200]
                  for m in re.finditer(r'BuildDrawCapture\(d9ib::kNone, d9ib::kNone\);', dv)))
        cen = T['d3d9_census.cpp']
        check('the census summary prints a "buffer refresh per frame" line before the query line, counted only with the census on',
              'reportRefresh(frames);\n  reportQueries(frames);' in cen and 'buffer refresh per frame: vb whole' in cen
              and 'void bufferRefresh(unsigned kind, uint64_t bytes) {\n  if (!g_on || kind >= kRefreshKinds)\n    return;' in cen)
        check('the vertex buffers keep the whole refresh (one census count added, nothing else)',
              b.count('census::bufferRefresh(census::kRefreshVbWhole, m_size);') == 1 and 'MTLD3D9VertexBuffer::refreshRange' not in b)

        # A moved anchor fails by name and changes nothing (only on an unpatched tree: a tree the workflow already patched has no
        # old anchor left to move).
        pre_patched = 'madeira-bcd: DXMT_D9_IB_RANGE' in (src / 'd3d9_device.cpp').read_text()
        if pre_patched:
            print('note the dxmt tree is already patched: the moved-anchor test needs the pinned files')
        bad = t / 'bad'
        (bad / 'dxmt/src/d3d9').mkdir(parents=True)
        for f in FILES:
            shutil.copy(src / f, bad / 'dxmt/src/d3d9' / f)
        p = bad / 'dxmt/src/d3d9/d3d9_device.cpp'
        p.write_text(p.read_text().replace('MTLD3D9Device::BuildDrawCapture() {', 'MTLD3D9Device::BuildDrawCaptureX() {'))
        before = {f: (bad / 'dxmt/src/d3d9' / f).read_text() for f in FILES}
        r3 = subprocess.run([sys.executable, str(script)], cwd=bad, capture_output=True, text=True)
        check('a moved anchor fails by name and no file changes', pre_patched or
              r3.returncode != 0 and 'BuildDrawCapture definition: anchor not found once' in (r3.stdout + r3.stderr)
              and all((bad / 'dxmt/src/d3d9' / f).read_text() == before[f] for f in FILES)
              and not (bad / 'dxmt/src/d3d9/d3d9_ib_range.hpp').exists(), r3.stdout + r3.stderr)

        # ---------------------------------------------------------------- 3. the state machine on the host
        if not cxx:
            print('skip the host compile: no C++ compiler')
        else:
            harness = r'''
#include "d3d9_ib_range.hpp"
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
using namespace dxmt::d9ib;

static int fails;
#define EXPECT(c, ...) do { if (!(c)) { printf("FAIL "); printf(__VA_ARGS__); printf("\n"); fails++; } } while (0)

// The buffer and the GPU generations, acting on RangeState as MTLD3D9IndexBuffer does (d3d9_buffer.cpp).
struct Sim {
  uint32_t size;
  std::vector<uint8_t> mirror;
  std::vector<std::vector<uint8_t>> gens;
  uint32_t cur = 0;
  bool dirty = true, writeLocked = false;
  int lockCount = 0;
  RangeState st;
  struct Read { uint32_t gen, lo, hi; std::vector<uint8_t> expect; };
  std::vector<Read> reads;
  uint64_t bytes = 0, renames = 0, wholes = 0, skips = 0, stores = 0;
  explicit Sim(uint32_t s) : size(s), mirror(s, 0) { gens.push_back(std::vector<uint8_t>(s, 0xEE)); }
  void rename() { gens.push_back(std::vector<uint8_t>(size, 0xEE)); cur = (uint32_t)gens.size() - 1; renames++; }
  void wholeRefresh() {                  // refreshWholeMirror with the key on
    if (!dirty) return;
    rename();
    gens[cur] = mirror; bytes += size; wholes++;
    dirty = writeLocked;
    st.onWholeStored(writeLocked, size);
  }
  void drawWhole() {                     // BuildDrawCapture(kWhole) with the key on
    wholeRefresh(); st.onWholeRead(size);
    reads.push_back({cur, 0, size, mirror});
  }
  void drawRange(uint32_t lo, uint32_t hi) {  // refreshRange
    Action a = st.onRangeDraw(lo, hi, writeLocked);
    if (a == Action::Skip) skips++;
    else if (a == Action::Whole) { dirty = true; wholeRefresh(); }
    else {
      bool renamed = a == Action::RenameStore;
      if (renamed) rename();
      memcpy(gens[cur].data() + lo, mirror.data() + lo, hi - lo); bytes += hi - lo; stores++;
      st.onRangeStored(lo, hi, renamed, writeLocked);
      dirty = true;
    }
    reads.push_back({cur, lo, hi, std::vector<uint8_t>(mirror.begin() + lo, mirror.begin() + hi)});
  }
  void lock(bool write) { if (write) { dirty = true; writeLocked = true; st.onWriteLock(); } lockCount++; }
  void unlock() { if (lockCount > 0 && --lockCount == 0) writeLocked = false; }
  void write(std::mt19937 &rng, uint32_t lo, uint32_t hi) { for (uint32_t i = lo; i < hi; i++) mirror[i] = (uint8_t)rng(); }
  // The GPU runs every recorded draw after the CPU is done: the worst case for an overwrite.
  bool verify(const char *what) {
    for (size_t i = 0; i < reads.size(); i++) {
      const Read &r = reads[i];
      if (memcmp(gens[r.gen].data() + r.lo, r.expect.data(), r.hi - r.lo)) {
        printf("FAIL %s: draw %zu of [%u, %u) in generation %u reads bytes it was not recorded with\n", what, i, r.lo, r.hi, r.gen);
        fails++;
        return false;
      }
    }
    return true;
  }
};

static void intervals() {
  IntervalSet s;
  EXPECT(s.add(10, 20) && s.add(30, 40) && s.n == 2, "two disjoint");
  EXPECT(s.add(20, 30) && s.n == 1 && s.lo[0] == 10 && s.hi[0] == 40, "touching on both sides merge into one");
  EXPECT(s.overlaps(39, 50) && !s.overlaps(40, 50) && !s.overlaps(0, 10), "overlap is strict at the ends");
  EXPECT(s.covers(10, 40) && s.covers(15, 25) && !s.covers(5, 15) && !s.covers(35, 45), "covers needs one interval around");
  s.clear();
  for (uint32_t i = 0; i < IntervalSet::kCap; i++) EXPECT(s.add(i * 100, i * 100 + 10), "fill %u", i);
  EXPECT(!s.add(5000, 5010) && s.n == IntervalSet::kCap, "full: a new interval is refused and nothing changes");
  EXPECT(!s.wouldFit(5000, 5010) && s.wouldFit(105, 200) && s.wouldFit(110, 120), "wouldFit: a merge fits a full set, a new one does not");
  EXPECT(s.add(5, 105) && s.n == IntervalSet::kCap - 1 && s.lo[0] == 0 && s.hi[0] == 110, "a merge across two frees a slot");
  s.addOrHull(9000, 9001); s.addOrHull(9100, 9101);
  EXPECT(s.n == 1 && s.lo[0] == 0 && s.hi[0] == 9101, "addOrHull on a full set: one interval over everything");
  uint32_t lo = 7, hi = 7;
  EXPECT(!clampRange(100, 100, 50, lo, hi) && lo == 7, "empty or outside: refused, outputs untouched");
  EXPECT(clampRange(40, 80, 50, lo, hi) && lo == 40 && hi == 50, "clamped to the end");
  EXPECT(!clampRange(50, 60, 50, lo, hi), "starting at the end: refused");
  EXPECT(!clampRange(0xffffffff0ull, 0xffffffff8ull, 0xffffffffu, lo, hi), "a 64-bit range past 4 GB: refused");
}

static void randomRuns() {
  std::mt19937 rng(624);
  for (int run = 0; run < 400; run++) {
    uint32_t size = 64 + rng() % 4096;
    Sim s(size);
    s.lock(true); s.write(rng, 0, size); s.unlock();
    for (int op = 0; op < 600; op++) {
      uint32_t k = rng() % 100;
      if (k < 12) { s.lock(rng() % 4 != 0); }
      else if (k < 24) { s.unlock(); }
      else if (k < 40) { if (s.writeLocked) { uint32_t a = rng() % size, b = a + 1 + rng() % (size - a); s.write(rng, a, b); } }
      else if (k < 46) { s.drawWhole(); }
      else {
        uint32_t a = rng() % size, len = 1 + rng() % (k < 80 ? 64 : size), b = a + len > size ? size : a + len;
        s.drawRange(a, b);
      }
    }
    if (!s.verify("random")) { printf("     run %d size %u\n", run, size); return; }
  }
  printf("random-ok\n");
}

static void ring() {
  // Call of Duty: World at War's shape: lock (no-overwrite), write a chunk, unlock, draw it; chunks with a gap; a discard at the end.
  std::mt19937 rng(1);
  const uint32_t size = 1u << 20, chunk = 1000, gap = 24;
  Sim s(size);
  uint32_t o = 0, draws = 0, wraps = 0;
  for (int frame = 0; frame < 4; frame++) {
    for (int i = 0; i < 920; i++) {
      if (o + chunk > size) { o = 0; wraps++; }
      s.lock(true); s.write(rng, o, o + chunk); s.unlock();
      s.drawRange(o, o + chunk); draws++;
      o += chunk + gap;
    }
  }
  s.verify("ring");
  printf("ring: %u draws, %u wraps: %llu bytes stored (the whole-mirror refresh: %llu), %llu renames, %llu whole\n", draws, wraps,
         (unsigned long long)s.bytes, (unsigned long long)draws * size, (unsigned long long)s.renames, (unsigned long long)s.wholes);
  EXPECT(s.bytes == (uint64_t)draws * chunk && s.wholes == 0, "ring: only the chunks are copied, never the whole buffer");
  EXPECT(s.renames == wraps, "ring: one rename per wrap (%llu renames, %u wraps)", (unsigned long long)s.renames, wraps);
}

static void staticPieces() {
  // A buffer filled once and drawn in 100 pieces with gaps, ten frames: one whole copy once the pieces cannot be tracked, then nothing.
  std::mt19937 rng(2);
  const uint32_t size = 200000;
  Sim s(size);
  s.lock(true); s.write(rng, 0, size); s.unlock();
  uint64_t after_first = 0;
  for (int frame = 0; frame < 10; frame++) {
    for (uint32_t i = 0; i < 100; i++) s.drawRange(i * 1900, i * 1900 + 1500);
    if (frame == 0) after_first = s.bytes;
  }
  s.verify("static");
  printf("static: %llu bytes stored in 10 frames (frame 1: %llu), %llu whole, %llu skipped\n", (unsigned long long)s.bytes,
         (unsigned long long)after_first, (unsigned long long)s.wholes, (unsigned long long)s.skips);
  EXPECT(s.wholes == 1 && s.bytes == after_first, "static: one whole copy in the first frame, nothing after it");
  EXPECT(s.bytes <= (uint64_t)size + 32u * 1500u, "static: at most the whole buffer plus the tracked pieces");
}

static void repeated() {
  // The same range rewritten and drawn again: the second draw renames, so the first keeps its bytes.
  std::mt19937 rng(3);
  Sim s(4096);
  s.lock(true); s.write(rng, 0, 4096); s.unlock();
  s.drawRange(100, 200);
  s.lock(true); s.write(rng, 100, 200); s.unlock();
  s.drawRange(100, 200);
  EXPECT(s.renames == 1, "a rewritten range drawn again renames (%llu)", (unsigned long long)s.renames);
  s.drawRange(100, 200);
  EXPECT(s.renames == 1 && s.skips == 1, "drawn a third time with no lock: current, nothing stored");
  s.lock(true); s.write(rng, 3000, 3100);
  s.drawRange(100, 200);
  EXPECT(s.renames == 2, "while the buffer is still mapped every draw stores its range (the app may write without telling)");
  s.unlock();
  s.verify("repeated");
}

int main() {
  intervals();
  randomRuns();
  ring();
  staticPieces();
  repeated();
  if (!fails) puts("ib-range-ok");
  return fails != 0;
}
'''
            (d / 'h.cpp').write_text(harness)
            bld = subprocess.run([cxx, '-std=c++17', '-O2', '-Wall', '-Werror', '-I', str(d), '-o', str(d / 'h'), str(d / 'h.cpp')],
                                 capture_output=True, text=True)
            check('d3d9_ib_range.hpp compiles on the host (C++17, -Wall -Werror)', bld.returncode == 0, bld.stderr)
            if bld.returncode == 0:
                run = subprocess.run([str(d / 'h')], capture_output=True, text=True, timeout=600)
                for l in run.stdout.splitlines():
                    if l.startswith(('ring:', 'static:')):
                        print('     ' + l)
                check('the interval set, 400 random runs against the generation model, the ring, the static pieces and a rewritten range',
                      run.returncode == 0 and 'ib-range-ok' in run.stdout and 'random-ok' in run.stdout, run.stdout + run.stderr)
                # The model has teeth: the same harness on a state machine that forgets the earlier draws' bytes (no rename before
                # an overlapping store), or that trusts stored bytes across a write lock, must fail.
                for what, a, b2 in (('no rename before an overlapping store', 'if (reads.overlaps(lo, hi))\n      return Action::RenameStore;\n', ''),
                                    ('stored bytes trusted across a write lock', 'onWriteLock() {\n    clean.clear();\n  }', 'onWriteLock() {\n  }')):
                    hm = H.replace(a, b2)
                    m = d / 'mut'
                    m.mkdir(exist_ok=True)
                    (m / 'd3d9_ib_range.hpp').write_text(hm)
                    (m / 'h.cpp').write_text(harness)   # a quoted include looks beside the source first
                    mb = subprocess.run([cxx, '-std=c++17', '-O2', '-I', str(m), '-o', str(m / 'h'), str(m / 'h.cpp')], capture_output=True, text=True)
                    mr = subprocess.run([str(m / 'h')], capture_output=True, text=True, timeout=600) if mb.returncode == 0 else None
                    check('a broken state machine (%s) fails the model' % what,
                          hm != H and mr is not None and mr.returncode != 0 and 'reads bytes it was not recorded with' in mr.stdout,
                          (mb.stderr if mb.returncode else mr.stdout[-600:]))

# ---------------------------------------------------------------- 4. wiring outside DXMT
cat = (R / 'build/tools/gen-config-catalog.py').read_text()
check('the settings catalog knows the key (bool, default 1: on)',
      re.search(r'"env\.DXMT_D9_IB_RANGE": \{[^}]*?"kind": "bool", "default": "1"', cat, re.S) is not None)
pol = (R / 'app/Madeira/GameRecommendationsList.swift').read_text()
allowed = pol.split('static let allowedEnvNames')[1].split(']')[0] if 'static let allowedEnvNames' in pol else ''
case_at = pol.find('case "DXMT_D9_IB_RANGE":')
case_body = pol[case_at:] if case_at >= 0 else ''
case_body = case_body[:case_body.find('\n            case ')]
check('a downloaded list may set it, 0 or 1 (RecommendationsPolicy)',
      '"DXMT_D9_IB_RANGE"' in allowed and case_at >= 0 and 'guard value == "0" || value == "1"' in case_body, case_body[:400])

print('PASS: DXMT D3D9 index-buffer range refresh' if ok else 'FAILED: DXMT D3D9 index-buffer range refresh')
sys.exit(0 if ok else 1)
