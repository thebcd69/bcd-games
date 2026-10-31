#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: an indexed draw refreshes only the index bytes it reads (on by default since round 624; env DXMT_D9_IB_RANGE=0 turns it off).

Call of Duty: World at War (K8K2D3, build 623, iPad Pro M4, "heavy fps drop on some areas"): 32 fps in the heavy scenes with one
thread at 90-100 % of a core, and 89-92 % of its samples in one JIT block, FEX's translation of an x86 `rep movsd`, right after
winemetal's newBuffer / release stubs. The game locks its ~1 MB dynamic index buffer about 920 times a frame, each lock followed by
a draw; MTLD3D9IndexBuffer::refreshWholeMirror renames the buffer and copies the WHOLE mirror for every draw after a lock, ~0.9 GB a
frame. The same JIT block is in 12 more logs (GTA IV x5, Dead Space, LEGO Batman 2, Brutal Legend, Call of Duty 4, The Witcher,
Resident Evil 6, Command & Conquer 3).

An indexed draw reads exactly [StartIndex, StartIndex + count) indices of the bound index buffer (cmd.index_buffer_offset =
StartIndex * index size, index_count = count), so storing only those bytes is as correct for that draw as storing the whole mirror,
whatever the application wrote elsewhere (the reason the whole-mirror refresh exists: applications write outside the range they
lock, and that range is not used here). The owner made it the default for every Direct3D 9 game on 2026-10-30, before a device
test (the model test below is the evidence); env DXMT_D9_IB_RANGE=0 restores the old refresh for a game. With it on:
  * DrawIndexedPrimitive's ordinary path passes its byte range to BuildDrawCapture; MTLD3D9IndexBuffer::refreshRange stores the
    range into the current generation unless an earlier draw of that generation read any of those bytes (then it renames first:
    the earlier draws keep their generation) or the stored ranges cannot be tracked (then one whole refresh, after which every draw
    is current until the next lock: a static buffer drawn in many pieces costs one copy, as before, not one per draw);
  * draws that do not read the bound index buffer on the GPU (non-indexed, and the fan / sentinel / UP paths that draw from their
    own index data and return early when it is missing) skip its refresh;
  * every other draw keeps the whole-mirror refresh, and marks the whole buffer read.
The state machine is d3d9_ib_range.hpp (pure, compiled and run on the host by tests/host/check-dxmt-d9-ib-range.py over random
lock / write / draw sequences against a model of the GPU generations). With DXMT_D9_IB_RANGE=0 nothing changes but one relaxed
counter per refresh. Either way the census summary gets a "buffer refresh per frame" line (whole / range refreshes, MB copied,
renames), so a log shows the copy volume instead of it being inferred from a profile.

Idempotent; all files or none; fails by name when an anchor moves. Run from the repository root.
"""
import pathlib
import sys

D = pathlib.Path("dxmt/src/d3d9")
MARKER = "madeira-bcd: DXMT_D9_IB_RANGE"

HEADER = r'''// SPDX-License-Identifier: Zlib
// madeira-bcd: DXMT_D9_IB_RANGE (round 624, Call of Duty: World at War K8K2D3). The index-buffer range refresh's bookkeeping, pure
// (no Metal, no COM): MTLD3D9IndexBuffer acts on what it decides, tests/host/check-dxmt-d9-ib-range.py runs it on the host.
#pragma once

#include <cstdint>

namespace dxmt {
namespace d9ib {

// BuildDrawCapture's index-buffer arguments besides a byte range: the draw reads the whole bound index buffer or a range it does not
// know (the whole-mirror refresh), or it does not read the bound index buffer on the GPU at all.
constexpr uint32_t kWhole = 0xffffffffu;
constexpr uint32_t kNone = 0xfffffffeu;

// The switch, read once (d3d9_buffer.cpp): on unless env DXMT_D9_IB_RANGE=0 (the default since round 624, the owner's decision).
bool rangeRefreshEnabled();

// Up to kCap disjoint, sorted byte intervals [lo, hi); touching or overlapping intervals merge.
struct IntervalSet {
  static constexpr uint32_t kCap = 32;
  uint32_t lo[kCap];
  uint32_t hi[kCap];
  uint32_t n = 0;

  void
  clear() {
    n = 0;
  }
  bool
  overlaps(uint32_t a, uint32_t b) const {
    for (uint32_t i = 0; i < n; i++)
      if (a < hi[i] && lo[i] < b)
        return true;
    return false;
  }
  bool
  covers(uint32_t a, uint32_t b) const {
    for (uint32_t i = 0; i < n; i++)
      if (lo[i] <= a && b <= hi[i])
        return true;
    return false;
  }
  // The first interval that is not entirely before [a, b) (touching counts as not before), and how many touch or overlap it.
  void
  span(uint32_t a, uint32_t b, uint32_t &first, uint32_t &count) const {
    uint32_t i = 0;
    while (i < n && hi[i] < a)
      i++;
    uint32_t j = i;
    uint32_t nb = b;
    while (j < n && lo[j] <= nb) {
      if (hi[j] > nb)
        nb = hi[j];
      j++;
    }
    first = i;
    count = j - i;
  }
  bool
  wouldFit(uint32_t a, uint32_t b) const {
    if (a >= b)
      return true;
    uint32_t first, count;
    span(a, b, first, count);
    return count > 0 || n < kCap;
  }
  // Adds [a, b); false, and nothing changed, when it needs a new slot and none is free.
  bool
  add(uint32_t a, uint32_t b) {
    if (a >= b)
      return true;
    uint32_t first, count;
    span(a, b, first, count);
    if (count == 0) {
      if (n == kCap)
        return false;
      for (uint32_t k = n; k > first; k--) {
        lo[k] = lo[k - 1];
        hi[k] = hi[k - 1];
      }
      lo[first] = a;
      hi[first] = b;
      n++;
      return true;
    }
    uint32_t na = a < lo[first] ? a : lo[first];
    uint32_t nb = b > hi[first + count - 1] ? b : hi[first + count - 1];
    lo[first] = na;
    hi[first] = nb;
    uint32_t drop = count - 1;
    for (uint32_t k = first + 1; k + drop < n; k++) {
      lo[k] = lo[k + drop];
      hi[k] = hi[k + drop];
    }
    n -= drop;
    return true;
  }
  // add(), or, when the set is full, one interval over everything: a superset, which only a set that may claim more than it holds
  // (the bytes draws read) can take.
  void
  addOrHull(uint32_t a, uint32_t b) {
    if (add(a, b))
      return;
    uint32_t na = a < lo[0] ? a : lo[0];
    uint32_t nb = b > hi[n - 1] ? b : hi[n - 1];
    n = 1;
    lo[0] = na;
    hi[0] = nb;
  }
};

// A draw's index bytes [lo64, hi64) clamped to a buffer of `size` bytes; false (out_* untouched) when nothing of it is inside.
inline bool
clampRange(uint64_t lo64, uint64_t hi64, uint32_t size, uint32_t &out_lo, uint32_t &out_hi) {
  if (lo64 >= size || hi64 <= lo64)
    return false;
  out_lo = static_cast<uint32_t>(lo64);
  out_hi = hi64 > size ? size : static_cast<uint32_t>(hi64);
  return true;
}

enum class Action {
  Skip,         // the current generation already holds these bytes as the mirror has them: nothing to store
  Store,        // store the range into the current generation (no draw of it read these bytes)
  RenameStore,  // rename first (an earlier draw of this generation read some of these bytes), then store the range
  Whole,        // rename and store the whole mirror (the ranges stored could no longer be tracked)
};

// One index buffer's generation bookkeeping. `reads`: bytes that draws recorded against the current generation read (a superset is
// safe: it only renames more). `clean`: bytes stored into the current generation since the last write lock (exact: a byte in it
// holds what the mirror holds). A stored range always joins both, so clean is inside reads.
struct RangeState {
  IntervalSet reads;
  IntervalSet clean;

  void
  onWriteLock() {
    clean.clear();
  }
  // The whole mirror went into a fresh generation (any whole-mirror refresh, also on the key's Whole action).
  void
  onWholeStored(bool write_locked, uint32_t size) {
    reads.clear();
    clean.clear();
    reads.add(0, size);
    if (!write_locked)
      clean.add(0, size);
  }
  // A draw that reads the bound index buffer without saying where.
  void
  onWholeRead(uint32_t size) {
    reads.addOrHull(0, size);
  }
  Action
  onRangeDraw(uint32_t lo, uint32_t hi, bool write_locked) {
    if (!write_locked && clean.covers(lo, hi)) {
      reads.addOrHull(lo, hi);
      return Action::Skip;
    }
    if (!write_locked && !clean.wouldFit(lo, hi))
      return Action::Whole;
    if (reads.overlaps(lo, hi))
      return Action::RenameStore;
    return Action::Store;
  }
  // After Store / RenameStore put the range into the current generation (renamed: a new one, or the old one after a full drain).
  void
  onRangeStored(uint32_t lo, uint32_t hi, bool renamed, bool write_locked) {
    if (renamed) {
      reads.clear();
      clean.clear();
    }
    reads.addOrHull(lo, hi);
    if (!write_locked)
      clean.add(lo, hi);
  }
};

} // namespace d9ib
} // namespace dxmt
'''

# ---------------------------------------------------------------- d3d9_buffer.hpp
BUF_HPP = D / "d3d9_buffer.hpp"
BUF_HPP_INC_OLD = '#include "d3d9_buffer_map.hpp"\n'
BUF_HPP_INC_NEW = '#include "d3d9_buffer_map.hpp"\n#include "d3d9_ib_range.hpp"\n'
BUF_HPP_DECL_OLD = '''  bool
  isDirty() const {
    return m_dirty;
  }
  void refreshWholeMirror();
  // Host-mapped pointer to the current index data: the host mirror, which is'''
BUF_HPP_DECL_NEW = '''  bool
  isDirty() const {
    return m_dirty;
  }
  void refreshWholeMirror();
  // madeira-bcd: DXMT_D9_IB_RANGE (d3d9_ib_range.hpp). refreshRange: an indexed draw that reads [lo, hi) of this buffer; noteWholeRead:
  // a draw that read it without saying where. Called only while the range refresh is on (the default; DXMT_D9_IB_RANGE=0 is off).
  void refreshRange(uint32_t lo, uint32_t hi);
  void
  noteWholeRead() {
    m_range.onWholeRead(m_size);
  }
  // Host-mapped pointer to the current index data: the host mirror, which is'''
BUF_HPP_MEMBER_OLD = '''  bool m_writeLocked = false;
  D3D9BufferLockCount m_lockCount;
  UINT m_size;
  DWORD m_usage;
  D3DFORMAT m_format;'''
BUF_HPP_MEMBER_NEW = '''  bool m_writeLocked = false;
  // madeira-bcd: DXMT_D9_IB_RANGE. Which bytes of the current generation draws read and which hold the mirror (d3d9_ib_range.hpp);
  // unused with DXMT_D9_IB_RANGE=0.
  d9ib::RangeState m_range;
  D3D9BufferLockCount m_lockCount;
  UINT m_size;
  DWORD m_usage;
  D3DFORMAT m_format;'''

# ---------------------------------------------------------------- d3d9_buffer.cpp
BUF_CPP = D / "d3d9_buffer.cpp"
BUF_CPP_INC_OLD = '#include "log/log.hpp"\n#include "wsi_platform.hpp"\n'
BUF_CPP_INC_NEW = '#include "log/log.hpp"\n#include "util_env.hpp"\n#include "wsi_platform.hpp"\n'
BUF_CPP_KEY_OLD = '''  dynamic->updateImmediateName(current_seq, std::move(fresh), 0, false);
  return D3D_OK;
}

} // namespace
'''
BUF_CPP_KEY_NEW = '''  dynamic->updateImmediateName(current_seq, std::move(fresh), 0, false);
  return D3D_OK;
}

} // namespace

// madeira-bcd: DXMT_D9_IB_RANGE (round 624). On unless the environment says 0; read once, said once.
bool
d9ib::rangeRefreshEnabled() {
  static const bool on = [] {
    const bool v = env::getEnvVar("DXMT_D9_IB_RANGE") != "0";
    Logger::info(v ? "[ib-range] r624 on: an indexed draw stores only the index bytes it reads (DXMT_D9_IB_RANGE=0 turns it off)"
                   : "[ib-range] r624 off (DXMT_D9_IB_RANGE=0): every draw refreshes the whole index buffer, as before");
    return v;
  }();
  return on;
}
'''
BUF_CPP_VB_OLD = '''  // tracks refresh COUNT as much as bytes.
  {
    m_dynamic->immediateName()->updateContents(0, m_hostPtr, m_size, m_dynamic->immediateSuballocation());
  }
'''
BUF_CPP_VB_NEW = '''  // tracks refresh COUNT as much as bytes.
  {
    m_dynamic->immediateName()->updateContents(0, m_hostPtr, m_size, m_dynamic->immediateSuballocation());
  }
  // madeira-bcd: DXMT_D9_IB_RANGE's census line counts every refresh (text only).
  census::bufferRefresh(census::kRefreshVbWhole, m_size);
'''
BUF_CPP_IB_OLD = '''  // into the fresh name and why it is charged to the staging axis.
  {
    m_dynamic->immediateName()->updateContents(0, m_hostPtr, m_size, m_dynamic->immediateSuballocation());
  }
  // Still mapped means the application can write more before the next draw
  // without telling us, so the cache cannot be declared current yet.
  m_dirty = m_writeLocked;
}
'''
BUF_CPP_IB_NEW = '''  // into the fresh name and why it is charged to the staging axis.
  {
    m_dynamic->immediateName()->updateContents(0, m_hostPtr, m_size, m_dynamic->immediateSuballocation());
  }
  census::bufferRefresh(census::kRefreshIbWhole, m_size);
  // Still mapped means the application can write more before the next draw
  // without telling us, so the cache cannot be declared current yet.
  m_dirty = m_writeLocked;
  // madeira-bcd: DXMT_D9_IB_RANGE. The fresh generation holds the whole mirror.
  if (d9ib::rangeRefreshEnabled())
    m_range.onWholeStored(m_writeLocked, m_size);
}

// madeira-bcd: DXMT_D9_IB_RANGE (round 624, Call of Duty: World at War K8K2D3; tools/patch-dxmt-d9-ib-range.py). An indexed draw reads
// exactly [lo, hi) of this buffer, so only those bytes have to reach the generation it binds, whatever the application wrote
// elsewhere. Bytes an earlier draw of the current generation read are never overwritten: such a range renames first (the earlier
// draws keep their generation, as on the whole-mirror path). The generation then holds what draws stored, not the whole mirror, so
// m_dirty stays set and a draw that reads the buffer without a range takes the whole-mirror refresh. Calling thread, device lock.
void
MTLD3D9IndexBuffer::refreshRange(uint32_t lo, uint32_t hi) {
  const d9ib::Action action = m_range.onRangeDraw(lo, hi, m_writeLocked);
  if (action == d9ib::Action::Skip) {
    census::bufferRefresh(census::kRefreshIbRangeSkip, 0);
    return;
  }
  if (action == d9ib::Action::Whole) {
    m_dirty = true;
    refreshWholeMirror();
    return;
  }
  bool renamed = false;
  if (action == d9ib::Action::RenameStore) {
    // The whole-mirror path's rename, with its fallbacks (a full drain, then the current generation, which nothing unfinished reads
    // after the drain).
    if (FAILED(renameDynamicBuffer(
            m_device, m_dynamic.ptr(), m_device->m_currentCmdSeq,
            m_device->m_cachedSignaled.load(std::memory_order_acquire)
        ))) {
      if (m_device->drainForDynamicRename()) {
        if (FAILED(renameDynamicBuffer(
                m_device, m_dynamic.ptr(), m_device->m_currentCmdSeq,
                m_device->m_cachedSignaled.load(std::memory_order_acquire)
            )))
          m_device->noteRenameInPlace();
      } else {
        static bool warned = false;
        if (!warned) {
          warned = true;
          Logger::warn("d3d9: out of video memory refreshing a dynamic index buffer range; the draw reads stale contents");
        }
        return;
      }
    }
    renamed = true;
  }
  m_dynamic->immediateName()->updateContents(
      lo, static_cast<const char *>(m_hostPtr) + lo, hi - lo, m_dynamic->immediateSuballocation()
  );
  m_range.onRangeStored(lo, hi, renamed, m_writeLocked);
  m_dirty = true;
  census::bufferRefresh(renamed ? census::kRefreshIbRangeRenamed : census::kRefreshIbRange, hi - lo);
}
'''
BUF_CPP_LOCK_OLD = '''  if (buffer_lock_updates_dirty(Flags)) {
    m_dirty = true;
    m_writeLocked = true;
  }
'''
BUF_CPP_LOCK_NEW = '''  if (buffer_lock_updates_dirty(Flags)) {
    m_dirty = true;
    m_writeLocked = true;
    // madeira-bcd: DXMT_D9_IB_RANGE. Any stored byte may change now (unused with DXMT_D9_IB_RANGE=0).
    m_range.onWriteLock();
  }
'''

# ---------------------------------------------------------------- d3d9_device.hpp / .cpp
DEV_HPP = D / "d3d9_device.hpp"
DEV_HPP_INC_OLD = '#include "d3d9_enc_state.hpp"\n'
DEV_HPP_INC_NEW = '#include "d3d9_enc_state.hpp"\n#include "d3d9_ib_range.hpp"\n'
DEV_HPP_DECL_OLD = '''  // override_* args are per-call, not device state.
  D3D9DrawCapture BuildDrawCapture();
'''
DEV_HPP_DECL_NEW = '''  // override_* args are per-call, not device state.
  // madeira-bcd: DXMT_D9_IB_RANGE. ib_lo / ib_hi: the bytes of the bound index buffer the draw reads, d9ib::kWhole for the whole buffer
  // or an unknown range, d9ib::kNone when the draw does not read the bound index buffer on the GPU. Ignored with DXMT_D9_IB_RANGE=0.
  D3D9DrawCapture BuildDrawCapture(uint32_t ib_lo = d9ib::kWhole, uint32_t ib_hi = d9ib::kWhole);
'''
DEV_CPP = D / "d3d9_device.cpp"
DEV_CPP_DEF_OLD = '''MTLD3D9Device::D3D9DrawCapture
MTLD3D9Device::BuildDrawCapture() {
'''
DEV_CPP_DEF_NEW = '''MTLD3D9Device::D3D9DrawCapture
MTLD3D9Device::BuildDrawCapture(uint32_t ib_lo, uint32_t ib_hi) {
'''
DEV_CPP_REFRESH_OLD = '''  if (m_indexBuffer.ptr() != nullptr)
    m_indexBuffer->refreshWholeMirror();

'''
DEV_CPP_REFRESH_NEW = '''  if (m_indexBuffer.ptr() != nullptr) {
    // madeira-bcd: DXMT_D9_IB_RANGE (d3d9_ib_range.hpp). With DXMT_D9_IB_RANGE=0 every draw refreshes the whole mirror, as before.
    if (!d9ib::rangeRefreshEnabled() || ib_lo == d9ib::kWhole) {
      m_indexBuffer->refreshWholeMirror();
      if (d9ib::rangeRefreshEnabled())
        m_indexBuffer->noteWholeRead();
    } else if (ib_lo != d9ib::kNone) {
      m_indexBuffer->refreshRange(ib_lo, ib_hi);
    }
  }

'''
DIP_ORDINARY_OLD = '''  UINT index_count = prim_to_vertex_count(PrimitiveType, PrimitiveCount);
  BatchedDraw draw{};
  draw.cap = BuildDrawCapture();
  draw.type = BatchedDraw::kIndexed;
  draw.primitive_type = PrimitiveType;
  draw.vertex_or_index_count = index_count;
  draw.start_vertex_or_index = StartIndex;
'''
DIP_ORDINARY_NEW = '''  UINT index_count = prim_to_vertex_count(PrimitiveType, PrimitiveCount);
  BatchedDraw draw{};
  {
    // madeira-bcd: DXMT_D9_IB_RANGE. The bytes of the bound index buffer this draw reads (the encode reads index_count indices
    // from StartIndex * index size); a range outside the buffer keeps the whole-mirror refresh.
    const uint64_t isz = m_indexBuffer->indexFormat() == D3DFMT_INDEX32 ? 4u : 2u;
    uint32_t ib_lo = d9ib::kWhole, ib_hi = d9ib::kWhole;
    d9ib::clampRange(
        static_cast<uint64_t>(StartIndex) * isz, (static_cast<uint64_t>(StartIndex) + index_count) * isz,
        m_indexBuffer->size(), ib_lo, ib_hi
    );
    draw.cap = BuildDrawCapture(ib_lo, ib_hi);
  }
  draw.type = BatchedDraw::kIndexed;
  draw.primitive_type = PrimitiveType;
  draw.vertex_or_index_count = index_count;
  draw.start_vertex_or_index = StartIndex;
'''
PLAIN_CALL = "    draw.cap = BuildDrawCapture();\n"
PLAIN_CALL_TOP = "  draw.cap = BuildDrawCapture();\n"
NONE_CALL = "    draw.cap = BuildDrawCapture(d9ib::kNone, d9ib::kNone);  // madeira-bcd: DXMT_D9_IB_RANGE, not the bound index buffer\n"
NONE_CALL_TOP = "  draw.cap = BuildDrawCapture(d9ib::kNone, d9ib::kNone);  // madeira-bcd: DXMT_D9_IB_RANGE, not the bound index buffer\n"
# function signature -> the (indent, replace?) of each BuildDrawCapture() call in its body, in order. Every replaced call is a
# non-indexed draw or one with its own index buffer that returns early when that buffer could not be made.
SITES = [
    ("MTLD3D9Device::DrawPrimitive(D3DPRIMITIVETYPE PrimitiveType, UINT StartVertex, UINT PrimitiveCount) {",
     [("fan with its own index buffer", True), ("non-indexed", True)]),
    ("MTLD3D9Device::DrawIndexedPrimitive(",
     [("fan with its own index buffer", True), ("16-bit strip widened into its own index buffer", True)]),
    ("MTLD3D9Device::DrawPrimitiveUP(",
     [("fan with its own index buffer", True), ("non-indexed", True)]),
    ("MTLD3D9Device::DrawIndexedPrimitiveUP(",
     [("fan with its own index buffer", True), ("UP index data (kept: its handle is not checked before the draw)", False)]),
]

# ---------------------------------------------------------------- d3d9_census.hpp / .cpp
CEN_HPP = D / "d3d9_census.hpp"
CEN_HPP_OLD = '''void shaderConstF(unsigned vec4_count);
void lockBytes(unsigned size_bytes); /* 0 means "to the end of the buffer" */
'''
CEN_HPP_NEW = '''void shaderConstF(unsigned vec4_count);
void lockBytes(unsigned size_bytes); /* 0 means "to the end of the buffer" */

/* madeira-bcd: DXMT_D9_IB_RANGE (round 624). What the dynamic buffer refreshes cost, on a "buffer refresh per frame" line of the
 * summary: a vertex buffer's whole mirror, an index buffer's whole mirror, an index buffer's range (the default; not with =0) into the
 * current generation or after a rename, and a range draw whose bytes were already stored. Relaxed counters: a refresh is a copy,
 * one add beside it is not measurable. */
enum : unsigned {
  kRefreshVbWhole = 0,
  kRefreshIbWhole = 1,
  kRefreshIbRange = 2,
  kRefreshIbRangeRenamed = 3,
  kRefreshIbRangeSkip = 4,
  kRefreshKinds = 5
};
void bufferRefresh(unsigned kind, uint64_t bytes);
'''
CEN_CPP = D / "d3d9_census.cpp"
CEN_CPP_VARS_OLD = '''uint32_t g_prev_q_issued;
'''
CEN_CPP_VARS_NEW = '''/* madeira-bcd: DXMT_D9_IB_RANGE's buffer refresh counters (see d3d9_census.hpp). */
std::atomic<uint64_t> g_rf_count[kRefreshKinds];
std::atomic<uint64_t> g_rf_bytes[kRefreshKinds];
uint64_t g_prev_rf_count[kRefreshKinds];
uint64_t g_prev_rf_bytes[kRefreshKinds];

uint32_t g_prev_q_issued;
'''
CEN_CPP_REPORT_OLD = '''/* MADEIRA: one window's worth of the query-poll instrument. Same windowing
'''
CEN_CPP_REPORT_NEW = '''/* madeira-bcd: DXMT_D9_IB_RANGE. One window of the buffer refresh counters, per presented frame. */
void reportRefresh(uint32_t frames) {
  double f = frames ? (double)frames : 1.0;
  uint64_t n[kRefreshKinds], b[kRefreshKinds];
  for (unsigned k = 0; k < kRefreshKinds; k++) {
    uint64_t c = g_rf_count[k].load(std::memory_order_relaxed);
    uint64_t y = g_rf_bytes[k].load(std::memory_order_relaxed);
    n[k] = c - g_prev_rf_count[k];
    b[k] = y - g_prev_rf_bytes[k];
    g_prev_rf_count[k] = c;
    g_prev_rf_bytes[k] = y;
  }
  line("buffer refresh per frame: vb whole %.1f (%.2f MB) | ib whole %.1f (%.2f MB) | ib range %.1f + %.1f renamed (%.3f MB), %.1f "
       "already stored",
       n[kRefreshVbWhole] / f, b[kRefreshVbWhole] / f / 1048576.0, n[kRefreshIbWhole] / f, b[kRefreshIbWhole] / f / 1048576.0,
       n[kRefreshIbRange] / f, n[kRefreshIbRangeRenamed] / f,
       (b[kRefreshIbRange] + b[kRefreshIbRangeRenamed]) / f / 1048576.0, n[kRefreshIbRangeSkip] / f);
}

/* MADEIRA: one window's worth of the query-poll instrument. Same windowing
'''
CEN_CPP_CALL_OLD = '''  reportQueries(frames);
  reportBcDecode("census-summary");
'''
CEN_CPP_CALL_NEW = '''  reportRefresh(frames);
  reportQueries(frames);
  reportBcDecode("census-summary");
'''
CEN_CPP_FN_OLD = '''void lockBytes(unsigned n) {
  if (!g_on)
    return;
'''
CEN_CPP_FN_NEW = '''/* madeira-bcd: DXMT_D9_IB_RANGE. */
void bufferRefresh(unsigned kind, uint64_t bytes) {
  if (!g_on || kind >= kRefreshKinds)
    return;
  g_rf_count[kind].fetch_add(1, std::memory_order_relaxed);
  g_rf_bytes[kind].fetch_add(bytes, std::memory_order_relaxed);
}

void lockBytes(unsigned n) {
  if (!g_on)
    return;
'''


def fail(msg):
    print(f"::error::patch-dxmt-d9-ib-range: {msg} -- dxmt moved, review this patch")
    return 1


def once(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f"{what}: anchor not found once ({text.count(old)} matches)")
    return text.replace(old, new)


def patch_sites(dev):
    for sig, sites in SITES:
        at = dev.find(sig)
        if at < 0 or dev.find(sig, at + 1) >= 0:
            raise ValueError(f"d3d9_device.cpp {sig}: anchor not found once")
        end = dev.index("\n}\n", at)
        body = dev[at:end]
        calls = [(i, PLAIN_CALL) for i in _find_all(body, PLAIN_CALL)] + [(i, PLAIN_CALL_TOP) for i in _find_all(body, PLAIN_CALL_TOP)
                                                                          if not body[max(0, i - 2):i].endswith("  ")]
        calls.sort()
        if len(calls) != len(sites):
            raise ValueError(f"d3d9_device.cpp {sig}: {len(calls)} BuildDrawCapture() calls, expected {len(sites)}")
        for (pos, form), (what, replace) in reversed(list(zip(calls, sites))):
            if not replace:
                continue
            new = NONE_CALL if form == PLAIN_CALL else NONE_CALL_TOP
            body = body[:pos] + new + body[pos + len(form):]
        dev = dev[:at] + body + dev[end:]
    return dev


def _find_all(s, sub):
    i = s.find(sub)
    while i >= 0:
        yield i
        i = s.find(sub, i + 1)


def main():
    files = [BUF_HPP, BUF_CPP, DEV_HPP, DEV_CPP, CEN_HPP, CEN_CPP]
    for f in files:
        if not f.exists():
            return fail(f"{f} missing")
    texts = {f: f.read_text() for f in files}
    hdr = D / "d3d9_ib_range.hpp"
    done = [f for f in files if MARKER in texts[f]]
    if done and len(done) == len(files) and hdr.exists():
        print("d3d9: index-buffer range refresh (DXMT_D9_IB_RANGE) already patched")
        return 0
    if done:
        return fail("only some files carry the marker: " + ", ".join(str(f) for f in done))
    try:
        t = dict(texts)
        t[BUF_HPP] = once(once(once(t[BUF_HPP], BUF_HPP_INC_OLD, BUF_HPP_INC_NEW, "d3d9_buffer.hpp includes"),
                               BUF_HPP_DECL_OLD, BUF_HPP_DECL_NEW, "d3d9_buffer.hpp MTLD3D9IndexBuffer::refreshWholeMirror"),
                          BUF_HPP_MEMBER_OLD, BUF_HPP_MEMBER_NEW, "d3d9_buffer.hpp MTLD3D9IndexBuffer members")
        b = once(t[BUF_CPP], BUF_CPP_INC_OLD, BUF_CPP_INC_NEW, "d3d9_buffer.cpp includes")
        b = once(b, BUF_CPP_KEY_OLD, BUF_CPP_KEY_NEW, "d3d9_buffer.cpp renameDynamicBuffer")
        b = once(b, BUF_CPP_VB_OLD, BUF_CPP_VB_NEW, "d3d9_buffer.cpp MTLD3D9VertexBuffer::refreshWholeMirror store")
        b = once(b, BUF_CPP_IB_OLD, BUF_CPP_IB_NEW, "d3d9_buffer.cpp MTLD3D9IndexBuffer::refreshWholeMirror store")
        ib_lock = b.index("MTLD3D9IndexBuffer::Lock(UINT OffsetToLock, UINT SizeToLock, void **ppbData, DWORD Flags) {")
        ib_unlock = b.index("MTLD3D9IndexBuffer::Unlock() {")
        region = b[ib_lock:ib_unlock]
        b = b[:ib_lock] + once(region, BUF_CPP_LOCK_OLD, BUF_CPP_LOCK_NEW, "d3d9_buffer.cpp MTLD3D9IndexBuffer::Lock") + b[ib_unlock:]
        t[BUF_CPP] = b
        t[DEV_HPP] = once(once(t[DEV_HPP], DEV_HPP_INC_OLD, DEV_HPP_INC_NEW, "d3d9_device.hpp includes"),
                          DEV_HPP_DECL_OLD, DEV_HPP_DECL_NEW, "d3d9_device.hpp BuildDrawCapture")
        d = once(t[DEV_CPP], DEV_CPP_DEF_OLD, DEV_CPP_DEF_NEW, "d3d9_device.cpp BuildDrawCapture definition")
        d = once(d, DEV_CPP_REFRESH_OLD, DEV_CPP_REFRESH_NEW, "d3d9_device.cpp BuildDrawCapture index refresh")
        d = once(d, DIP_ORDINARY_OLD, DIP_ORDINARY_NEW, "d3d9_device.cpp DrawIndexedPrimitive ordinary path")
        t[DEV_CPP] = patch_sites(d)
        t[CEN_HPP] = once(t[CEN_HPP], CEN_HPP_OLD, CEN_HPP_NEW, "d3d9_census.hpp lockBytes")
        c = once(t[CEN_CPP], CEN_CPP_VARS_OLD, CEN_CPP_VARS_NEW, "d3d9_census.cpp query snapshots")
        c = once(c, CEN_CPP_REPORT_OLD, CEN_CPP_REPORT_NEW, "d3d9_census.cpp reportQueries")
        c = once(c, CEN_CPP_CALL_OLD, CEN_CPP_CALL_NEW, "d3d9_census.cpp summary tail")
        t[CEN_CPP] = once(c, CEN_CPP_FN_OLD, CEN_CPP_FN_NEW, "d3d9_census.cpp lockBytes")
    except ValueError as e:
        return fail(str(e))
    for f in files:
        if MARKER not in t[f]:
            return fail(f"{f}: the marker did not land")
    hdr.write_text(HEADER)
    for f in files:
        f.write_text(t[f])
    print("patched: d3d9 index-buffer range refresh (on by default; DXMT_D9_IB_RANGE=0 turns it off) + the census buffer refresh line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
