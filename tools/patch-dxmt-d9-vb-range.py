#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""DXMT Direct3D 9: a non-indexed draw refreshes only the vertex bytes it reads (keyed: on only with env DXMT_D9_VB_RANGE=1).

Resident Evil 4 (2005, bio4.exe, 32-bit Direct3D 9; B1TDSR, build 686) runs at 4 FPS. The census says "buffer refresh per frame: vb whole
206.5 (787.72 MB)": the game locks one dynamic vertex buffer 210 times a frame (lock sizes 1 to 256 bytes, each lock followed by a
DrawPrimitive: 71.7 draws and 489 SetStreamSource calls a frame), and MTLD3D9VertexBuffer::refreshWholeMirror renames the buffer and
copies the WHOLE ~3.8 MB mirror into the fresh allocation for every draw after a lock. The hot thread sits in ucrtbase's _sse2_memmove.
Other games of the archive pay the same: Vampire Bloodlines 203 MB a frame, Garry's Mod 162, Alan Wake 115, NMRiH 88, Blur 80.

DXMT's frontend solved exactly this for index buffers in round 624 (tools/patch-dxmt-d9-ib-range.py, DXMT_D9_IB_RANGE: "an indexed draw
stores only the index bytes it reads"). This is its vertex-buffer counterpart and it reuses that patch's state machine (d3d9_ib_range.hpp:
RangeState, IntervalSet) unchanged; d3d9_vb_range.hpp holds what is specific to vertex buffers. A non-indexed draw (DrawPrimitive,
the triangle fan included, which draws through its own index buffer) reads exactly the vertices [StartVertex, StartVertex + count) of every
bound stream (points N, lines 2N, a line strip N + 1, triangles 3N, a strip or fan N + 2), that is the bytes
[streamOffset + StartVertex * stride, streamOffset + (StartVertex + count) * stride) of each bound vertex buffer, clipped to the buffer.
With the key on, those bytes are all that is stored, whatever the application wrote elsewhere (the reason the whole-mirror refresh exists:
36.6 % of NOOVERWRITE refreshes write outside the range the lock declared, so the lock's range is never used here, only the draw's):
  * the state machine of the buffer (d3d9_ib_range.hpp) stores the range into the current generation unless an earlier draw of that
    generation read any of those bytes (then it renames first: the earlier draws keep their generation, and the fresh one takes the range only),
    skips a range whose bytes were already stored since the last write lock of any kind (DISCARD and NOOVERWRITE included: any write lock
    means any stored byte may change now), and takes one whole refresh when the stored ranges can no longer be tracked (the interval set
    is full: one copy, after which every draw is current until the next lock). A buffer whose device copy is current (not dirty) stores
    nothing and records the read;
  * a stream keeps the whole-mirror refresh, marks the whole buffer read, and so behaves as before, when the draw cannot be trusted: no
    range given (every draw but DrawPrimitive: DrawIndexedPrimitive, whose MinVertexIndex / NumVertices are not trustworthy, the user-pointer
    draws, ProcessVertices), any stream stepped with a frequency other than 1 (instancing), no vertex declaration, a stride of 0, a
    declaration element that reaches past the stream's stride, the same vertex buffer bound to two streams, or nothing of the range inside
    the buffer.
Without the key nothing changes: BuildDrawCapture runs the vertex-buffer loop it had (one test of the key per draw is added) and the only
new work is a write lock's onWriteLock on state that nothing reads. The census summary's "buffer refresh per frame" line gets the vertex
range fields at its end (stores, renames, MB, draws already stored), so a log shows that the key acts. The key is read once and said once
("[vb-range] madeira-bcd on / off"). It is off by default, unlike the index-buffer range: nothing here has run on a device yet.
The state machine is pure and is compiled and run on the host by tests/host/check-dxmt-d9-vb-range.py (random lock / write / draw
sequences over several streams against a model of the GPU generations, the Resident Evil 4 pattern, instancing and the other whole-refresh
cases, the interval set full, the key off).

Applied right after tools/patch-dxmt-d9-ib-range.py (it reuses that patch's header and extends its census line and its BuildDrawCapture).
PE side: the i386 d3d9-emulated.dll and the 64-bit d3d9-dxmt.dll, both built from dxmt/src/d3d9. Idempotent; all files or none; fails by
name when an anchor moves. Run from the repository root.
"""
import pathlib
import sys

D = pathlib.Path("dxmt/src/d3d9")
MARKER = "madeira-bcd: DXMT_D9_VB_RANGE"
IB_MARKER = "madeira-bcd: DXMT_D9_IB_RANGE"

HEADER = r'''// SPDX-License-Identifier: Zlib
// madeira-bcd: DXMT_D9_VB_RANGE (Resident Evil 4, B1TDSR build 686). The vertex-buffer range refresh's bookkeeping, pure (no Metal, no COM):
// MTLD3D9VertexBuffer acts on what it decides, MTLD3D9Device::RefreshVertexBuffersForDraw feeds it, tests/host/check-dxmt-d9-vb-range.py
// runs it on the host. The per-buffer generation state machine is the index buffer's (d3d9_ib_range.hpp: RangeState, IntervalSet).
#pragma once

#include <cstdint>

#include "d3d9_ib_range.hpp"

namespace dxmt {
namespace d9vb {

// BuildDrawCapture's vertex range argument when the draw gives none (every draw but DrawPrimitive): every bound vertex buffer takes the
// whole-mirror refresh, as before.
constexpr uint32_t kWhole = 0xffffffffu;

// The switch, read once (d3d9_buffer.cpp): off unless env DXMT_D9_VB_RANGE=1.
bool rangeRefreshEnabled();

// D3D9_MAX_VERTEX_STREAMS and the D3DPRIMITIVETYPE values, repeated so that this header stays free of the Direct3D include surface;
// MTLD3D9Device::RefreshVertexBuffersForDraw static_asserts them.
constexpr uint32_t kMaxStreams = 16;
constexpr uint32_t kPointList = 1;
constexpr uint32_t kLineList = 2;
constexpr uint32_t kLineStrip = 3;
constexpr uint32_t kTriangleList = 4;
constexpr uint32_t kTriangleStrip = 5;
constexpr uint32_t kTriangleFan = 6;

// The vertices a non-indexed DrawPrimitive reads: points N, lines 2N, a line strip N + 1, triangles 3N, a strip or a fan N + 2. 0 for a
// type it does not know (the caller keeps the whole-mirror refresh).
inline uint64_t
vertexCount(uint32_t primitive_type, uint32_t primitive_count) {
  const uint64_t n = primitive_count;
  switch (primitive_type) {
  case kPointList:
    return n;
  case kLineList:
    return 2 * n;
  case kLineStrip:
    return n + 1;
  case kTriangleList:
    return 3 * n;
  case kTriangleStrip:
  case kTriangleFan:
    return n + 2;
  default:
    return 0;
  }
}

// Whether every stream steps per vertex at the default frequency (SetStreamSourceFreq 1). Any other setting is instancing or an
// instance divider, which changes which bytes a draw reads: such a draw keeps the whole-mirror refresh.
inline bool
defaultFrequencies(const uint32_t *freq, uint32_t n) {
  for (uint32_t i = 0; i < n; i++)
    if (freq[i] != 1)
      return false;
  return true;
}

// The bytes [lo, hi) that a non-indexed draw of `vertex_count` vertices from `first_vertex` reads through a stream bound at `offset` with
// `stride`: [offset + first * stride, offset + (first + count) * stride), clipped to a buffer of `size` bytes. False (lo / hi untouched)
// for a stride of 0, nothing to draw, or nothing of the range inside the buffer. No 64-bit overflow: the first byte is below 2^64 as the
// product of two 32-bit numbers plus a 32-bit one, and the count is cut to the vertices that start inside the buffer before it is
// multiplied.
inline bool
streamRange(
    uint32_t first_vertex, uint64_t vertex_count, uint32_t offset, uint32_t stride, uint32_t size, uint32_t &lo, uint32_t &hi
) {
  if (stride == 0 || vertex_count == 0)
    return false;
  const uint64_t first_byte = offset + static_cast<uint64_t>(first_vertex) * stride;
  if (first_byte >= size)
    return false;
  const uint64_t room = (size - first_byte + stride - 1) / stride;   // vertices that start inside the buffer
  const uint64_t n = vertex_count < room ? vertex_count : room;
  return d9ib::clampRange(first_byte, first_byte + n * stride, size, lo, hi);
}

// One stream as the draw finds it.
struct StreamIn {
  const void *buffer;   // the bound vertex buffer (its identity), null for an unbound stream
  uint32_t offset;      // SetStreamSource's OffsetInBytes
  uint32_t stride;      // SetStreamSource's Stride
  uint32_t extent;      // the byte, counted from the start of a vertex, that the highest-reaching declaration element of this stream ends at
  uint32_t size;        // the buffer's size in bytes
};

// What one stream's refresh is: the range [lo, hi) of its buffer, or (range false) the whole-mirror refresh.
struct StreamPlan {
  bool range;
  uint32_t lo;
  uint32_t hi;
};

// The refresh of every bound stream for a draw that reads `vertex_count` vertices from `first_vertex` (first_vertex == kWhole: the draw
// gave no range). A stream gets a range only when everything the range rests on holds: a vertex declaration, the default frequency on
// all streams, a stride that is known and covers every declared element, a buffer that no other stream uses (two ranges of one
// buffer would have to be stored together, one rename would drop the first), and something of the range inside the buffer.
inline void
planDraw(
    uint32_t first_vertex, uint64_t vertex_count, bool has_declaration, const uint32_t *stream_freq, const StreamIn *in,
    StreamPlan *out
) {
  const bool usable = first_vertex != kWhole && vertex_count != 0 && has_declaration && defaultFrequencies(stream_freq, kMaxStreams);
  for (uint32_t s = 0; s < kMaxStreams; s++) {
    out[s].range = false;
    out[s].lo = 0;
    out[s].hi = 0;
    if (!usable || !in[s].buffer || in[s].stride == 0 || in[s].extent > in[s].stride)
      continue;
    bool shared = false;
    for (uint32_t t = 0; t < kMaxStreams; t++)
      if (t != s && in[t].buffer == in[s].buffer)
        shared = true;
    if (shared)
      continue;
    out[s].range = streamRange(first_vertex, vertex_count, in[s].offset, in[s].stride, in[s].size, out[s].lo, out[s].hi);
  }
}

// MTLD3D9VertexBuffer::refreshRange's decision before any copy. A buffer whose device copy is current (not dirty) holds what the mirror
// holds as far as the whole-mirror refresh would have it (its first test is the same): nothing to store, only the read to record, so
// that a later write of those bytes renames. Otherwise the index buffer's state machine.
inline d9ib::Action
onRangeDraw(d9ib::RangeState &state, bool dirty, bool write_locked, uint32_t lo, uint32_t hi) {
  if (!dirty) {
    state.reads.addOrHull(lo, hi);
    return d9ib::Action::Skip;
  }
  return state.onRangeDraw(lo, hi, write_locked);
}

} // namespace d9vb
} // namespace dxmt
'''

# ---------------------------------------------------------------- d3d9_buffer.hpp
BUF_HPP = D / "d3d9_buffer.hpp"
BUF_HPP_INC_OLD = '#include "d3d9_ib_range.hpp"\n#include "dxmt_buffer.hpp"\n'
BUF_HPP_INC_NEW = '#include "d3d9_ib_range.hpp"\n#include "d3d9_vb_range.hpp"\n#include "dxmt_buffer.hpp"\n'
# (the vertex buffer's declaration: the index buffer's is followed by the DXMT_D9_IB_RANGE declarations, not by this comment)
BUF_HPP_DECL_OLD = '''  void refreshWholeMirror();
  // Raw access to the owning device: same rationale as'''
BUF_HPP_DECL_NEW = '''  void refreshWholeMirror();
  // madeira-bcd: DXMT_D9_VB_RANGE (d3d9_vb_range.hpp). refreshRange: a non-indexed draw that reads [lo, hi) of this buffer; noteWholeRead:
  // a draw that read it without saying where. Called only while DXMT_D9_VB_RANGE=1 (off by default).
  void refreshRange(uint32_t lo, uint32_t hi);
  void
  noteWholeRead() {
    m_range.onWholeRead(m_size);
  }
  // Raw access to the owning device: same rationale as'''
# (the anchor is the comment of m_lockCount, which only the vertex buffer has: dxmt 880241a adds m_direct before it, and the checks that
# apply the patch chain to an older base tree have no m_direct)
BUF_HPP_MEMBER_OLD = '''  // Nested Lock/Unlock depth; the upload fires on the outer Unlock only.
  D3D9BufferLockCount m_lockCount;
'''
BUF_HPP_MEMBER_NEW = '''  // madeira-bcd: DXMT_D9_VB_RANGE. Which bytes of the current generation draws read and which hold the mirror (d3d9_ib_range.hpp's state
  // machine); unused unless DXMT_D9_VB_RANGE=1.
  d9ib::RangeState m_range;
  // Nested Lock/Unlock depth; the upload fires on the outer Unlock only.
  D3D9BufferLockCount m_lockCount;
'''

# ---------------------------------------------------------------- d3d9_buffer.cpp
# A direct-mapped vertex buffer (env.MADEIRA_D3D9_DIRECT_MAP = 1, dxmt 880241a; off unless set) has no host mirror and no refresh: the draws
# read the allocation the application locked, so there is nothing for refreshRange to store (m_hostPtr is null). The guard is in only when
# the dxmt checkout has the direct map (m_direct), as in the index buffer's patch.
DIRECT_GUARD = '''  if (m_direct)   // madeira-bcd: a direct-mapped buffer (MADEIRA_D3D9_DIRECT_MAP=1) has no mirror to store from
    return;
'''
BUF_CPP = D / "d3d9_buffer.cpp"
BUF_CPP_KEY_OLD = '''    return v;
  }();
  return on;
}

MTLD3D9VertexBuffer::MTLD3D9VertexBuffer('''
BUF_CPP_KEY_NEW = '''    return v;
  }();
  return on;
}

// madeira-bcd: DXMT_D9_VB_RANGE (Resident Evil 4, B1TDSR). Off unless the environment says 1; read once, said once.
bool
d9vb::rangeRefreshEnabled() {
  static const bool on = [] {
    const bool v = env::getEnvVar("DXMT_D9_VB_RANGE") == "1";
    Logger::info(v ? "[vb-range] madeira-bcd on: a non-indexed draw stores only the vertex bytes it reads (DXMT_D9_VB_RANGE=1)"
                   : "[vb-range] madeira-bcd off (DXMT_D9_VB_RANGE=1 turns it on): every draw refreshes the whole vertex buffer, as before");
    return v;
  }();
  return on;
}

MTLD3D9VertexBuffer::MTLD3D9VertexBuffer('''
BUF_CPP_WHOLE_OLD = '''  census::bufferRefresh(census::kRefreshVbWhole, m_size);
  // Still mapped means the application can write more before the next draw
  // without telling us, so the cache cannot be declared current yet.
  m_dirty = m_writeLocked;
}
'''
BUF_CPP_WHOLE_NEW = '''  census::bufferRefresh(census::kRefreshVbWhole, m_size);
  // Still mapped means the application can write more before the next draw
  // without telling us, so the cache cannot be declared current yet.
  m_dirty = m_writeLocked;
  // madeira-bcd: DXMT_D9_VB_RANGE. The fresh generation holds the whole mirror.
  if (d9vb::rangeRefreshEnabled())
    m_range.onWholeStored(m_writeLocked, m_size);
}

// madeira-bcd: DXMT_D9_VB_RANGE (Resident Evil 4, B1TDSR; tools/patch-dxmt-d9-vb-range.py), the vertex-buffer counterpart of
// MTLD3D9IndexBuffer::refreshRange. A non-indexed draw reads exactly [lo, hi) of this buffer (BuildDrawCapture's vertex range, checked by
// d9vb::planDraw), so only those bytes have to reach the generation it binds, whatever the application wrote elsewhere. Bytes an
// earlier draw of the current generation read are never overwritten: such a range renames first (the earlier draws keep their
// generation, as on the whole-mirror path) and the fresh generation takes the range only. The generation then holds what draws stored,
// not the whole mirror, so m_dirty stays set and a draw that reads the buffer without a range takes the whole-mirror refresh. Calling
// thread, device lock.
void
MTLD3D9VertexBuffer::refreshRange(uint32_t lo, uint32_t hi) {
@DIRECT_GUARD@  const d9ib::Action action = d9vb::onRangeDraw(m_range, m_dirty, m_writeLocked, lo, hi);
  if (action == d9ib::Action::Skip) {
    census::bufferRefresh(census::kRefreshVbRangeSkip, 0);
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
          Logger::warn("d3d9: out of video memory refreshing a dynamic vertex buffer range; the draw reads stale contents");
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
  census::bufferRefresh(renamed ? census::kRefreshVbRangeRenamed : census::kRefreshVbRange, hi - lo);
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
    // madeira-bcd: DXMT_D9_VB_RANGE. Any stored byte may change now, whatever the flags or the declared range (unused unless
    // DXMT_D9_VB_RANGE=1).
    m_range.onWriteLock();
  }
'''

# ---------------------------------------------------------------- d3d9_device.hpp / .cpp
DEV_HPP = D / "d3d9_device.hpp"
DEV_HPP_INC_OLD = '#include "d3d9_enc_state.hpp"\n#include "d3d9_ib_range.hpp"\n'
DEV_HPP_INC_NEW = '#include "d3d9_enc_state.hpp"\n#include "d3d9_ib_range.hpp"\n#include "d3d9_vb_range.hpp"\n'
DEV_HPP_DECL_OLD = '''  D3D9DrawCapture BuildDrawCapture(uint32_t ib_lo = d9ib::kWhole, uint32_t ib_hi = d9ib::kWhole);
'''
DEV_HPP_DECL_NEW = '''  // madeira-bcd: DXMT_D9_VB_RANGE. vb_first / vb_count: the draw reads the vertices [vb_first, vb_first + vb_count) of every bound stream
  // (DrawPrimitive); d9vb::kWhole when it says nothing about them. Ignored unless DXMT_D9_VB_RANGE=1 (off by default).
  D3D9DrawCapture BuildDrawCapture(
      uint32_t ib_lo = d9ib::kWhole, uint32_t ib_hi = d9ib::kWhole, uint32_t vb_first = d9vb::kWhole, uint64_t vb_count = 0
  );
  // madeira-bcd: DXMT_D9_VB_RANGE. BuildDrawCapture's vertex-buffer refresh while DXMT_D9_VB_RANGE=1.
  void RefreshVertexBuffersForDraw(uint32_t vb_first, uint64_t vb_count);
'''
DEV_CPP = D / "d3d9_device.cpp"
DEV_CPP_DEF_OLD = '''MTLD3D9Device::D3D9DrawCapture
MTLD3D9Device::BuildDrawCapture(uint32_t ib_lo, uint32_t ib_hi) {
'''
DEV_CPP_DEF_NEW = '''MTLD3D9Device::D3D9DrawCapture
MTLD3D9Device::BuildDrawCapture(uint32_t ib_lo, uint32_t ib_hi, uint32_t vb_first, uint64_t vb_count) {
'''
DEV_CPP_LOOP_OLD = '''  for (uint32_t s = 0; s < D3D9_MAX_VERTEX_STREAMS; ++s) {
    if (auto *vb = m_vertexBuffers[s].ptr())
      vb->refreshWholeMirror();
  }
'''
DEV_CPP_LOOP_NEW = '''  // madeira-bcd: DXMT_D9_VB_RANGE (d3d9_vb_range.hpp). Off by default: the loop below, as it was. With DXMT_D9_VB_RANGE=1 a non-indexed
  // draw refreshes only the vertex bytes it reads, and every other draw keeps the whole-mirror refresh.
  if (d9vb::rangeRefreshEnabled()) {
    RefreshVertexBuffersForDraw(vb_first, vb_count);
  } else {
    for (uint32_t s = 0; s < D3D9_MAX_VERTEX_STREAMS; ++s) {
      if (auto *vb = m_vertexBuffers[s].ptr())
        vb->refreshWholeMirror();
    }
  }
'''
DEV_CPP_FN_OLD = '''  return cap;
}

void
MTLD3D9Device::QueueBatchedDraw(BatchedDraw &&draw) {
'''
DEV_CPP_FN_NEW = '''  return cap;
}

// madeira-bcd: DXMT_D9_VB_RANGE (Resident Evil 4, B1TDSR; d3d9_vb_range.hpp, tools/patch-dxmt-d9-vb-range.py). BuildDrawCapture's
// vertex-buffer refresh while DXMT_D9_VB_RANGE=1. A non-indexed draw (vb_first != d9vb::kWhole) reads the vertices
// [vb_first, vb_first + vb_count) of every bound stream, so only those bytes have to reach the generation it binds: d9vb::planDraw says
// for which streams that holds (a declaration whose elements fit the stride, the default frequency everywhere, a known stride, a
// buffer of its own). Every other stream, and every draw that gave no range (indexed, user-pointer, ProcessVertices), takes the
// whole-mirror refresh and marks the whole buffer read. Refreshing every stream before any is frozen keeps each rename ahead of the
// freeze that names it, as in the loop this replaces. Calling thread, device lock, between draws.
void
MTLD3D9Device::RefreshVertexBuffersForDraw(uint32_t vb_first, uint64_t vb_count) {
  static_assert(sizeof(UINT) == sizeof(uint32_t), "m_streamFreq is read as uint32_t");
  static_assert(D3D9_MAX_VERTEX_STREAMS == d9vb::kMaxStreams, "d3d9_vb_range.hpp mirrors the stream count");
  static_assert(
      d9vb::kPointList == D3DPT_POINTLIST && d9vb::kLineList == D3DPT_LINELIST && d9vb::kLineStrip == D3DPT_LINESTRIP &&
          d9vb::kTriangleList == D3DPT_TRIANGLELIST && d9vb::kTriangleStrip == D3DPT_TRIANGLESTRIP &&
          d9vb::kTriangleFan == D3DPT_TRIANGLEFAN,
      "d3d9_vb_range.hpp mirrors the primitive types"
  );
  d9vb::StreamIn in[D3D9_MAX_VERTEX_STREAMS];
  for (uint32_t s = 0; s < D3D9_MAX_VERTEX_STREAMS; ++s) {
    auto *vb = m_vertexBuffers[s].ptr();
    in[s] = {vb, m_streamOffsets[s], m_streamStrides[s], 0, vb ? vb->size() : 0};
  }
  const bool declared = m_vertexDeclaration.ptr() != nullptr;
  if (declared && vb_first != d9vb::kWhole) {
    // How far into one vertex the declaration reaches on each stream. An element that reaches past the stream's stride would read
    // bytes of the next vertex (or past the last one) that the range of the draw does not cover: that stream keeps the whole refresh.
    const D3DVERTEXELEMENT9 *e = m_vertexDeclaration->elements();
    for (UINT i = 0, n = m_vertexDeclaration->elementCount(); i < n; ++i) {
      if (e[i].Stream >= D3D9_MAX_VERTEX_STREAMS)   // D3DDECL_END, and the streams the draw drops
        continue;
      const uint32_t end = e[i].Offset + decl_element_byte_size(e[i].Type);
      if (end > in[e[i].Stream].extent)
        in[e[i].Stream].extent = end;
    }
  }
  d9vb::StreamPlan plan[D3D9_MAX_VERTEX_STREAMS];
  d9vb::planDraw(vb_first, vb_count, declared, m_streamFreq, in, plan);
  for (uint32_t s = 0; s < D3D9_MAX_VERTEX_STREAMS; ++s) {
    auto *vb = m_vertexBuffers[s].ptr();
    if (!vb)
      continue;
    if (plan[s].range) {
      vb->refreshRange(plan[s].lo, plan[s].hi);
    } else {
      vb->refreshWholeMirror();
      vb->noteWholeRead();
    }
  }
}

void
MTLD3D9Device::QueueBatchedDraw(BatchedDraw &&draw) {
'''
DP_SIG = "MTLD3D9Device::DrawPrimitive(D3DPRIMITIVETYPE PrimitiveType, UINT StartVertex, UINT PrimitiveCount) {"
DP_FAN_OLD = '''    draw.cap = BuildDrawCapture(d9ib::kNone, d9ib::kNone);  // madeira-bcd: DXMT_D9_IB_RANGE, not the bound index buffer
    draw.type = BatchedDraw::kIndexed;
    draw.primitive_type = D3DPT_TRIANGLELIST;
    draw.vertex_or_index_count = PrimitiveCount * 3;
'''
DP_FAN_NEW = '''    // madeira-bcd: DXMT_D9_VB_RANGE. The fan's own index buffer reads the vertices [StartVertex, StartVertex + PrimitiveCount + 2) of every stream.
    draw.cap = BuildDrawCapture(d9ib::kNone, d9ib::kNone, StartVertex, static_cast<uint64_t>(PrimitiveCount) + 2u);
    draw.type = BatchedDraw::kIndexed;
    draw.primitive_type = D3DPT_TRIANGLELIST;
    draw.vertex_or_index_count = PrimitiveCount * 3;
'''
DP_PLAIN_OLD = '''  draw.cap = BuildDrawCapture(d9ib::kNone, d9ib::kNone);  // madeira-bcd: DXMT_D9_IB_RANGE, not the bound index buffer
  draw.type = BatchedDraw::kNonIndexed;
'''
DP_PLAIN_NEW = '''  // madeira-bcd: DXMT_D9_VB_RANGE. The draw reads the vertices [StartVertex, StartVertex + count) of every stream (a primitive type it does not
  // know gives 0: no range).
  draw.cap = BuildDrawCapture(d9ib::kNone, d9ib::kNone, StartVertex, d9vb::vertexCount(PrimitiveType, PrimitiveCount));
  draw.type = BatchedDraw::kNonIndexed;
'''

# ---------------------------------------------------------------- d3d9_census.hpp / .cpp
CEN_HPP = D / "d3d9_census.hpp"
CEN_HPP_OLD = '''  kRefreshIbRangeSkip = 4,
  kRefreshKinds = 5
};
'''
CEN_HPP_NEW = '''  kRefreshIbRangeSkip = 4,
  /* madeira-bcd: DXMT_D9_VB_RANGE (Resident Evil 4, B1TDSR): a vertex buffer's range (only with DXMT_D9_VB_RANGE=1) into the current
   * generation or after a rename, and a range draw whose bytes were already stored. */
  kRefreshVbRange = 5,
  kRefreshVbRangeRenamed = 6,
  kRefreshVbRangeSkip = 7,
  kRefreshKinds = 8
};
'''
CEN_CPP = D / "d3d9_census.cpp"
CEN_CPP_LINE_OLD = '''  line("buffer refresh per frame: vb whole %.1f (%.2f MB) | ib whole %.1f (%.2f MB) | ib range %.1f + %.1f renamed (%.3f MB), %.1f "
       "already stored",
       n[kRefreshVbWhole] / f, b[kRefreshVbWhole] / f / 1048576.0, n[kRefreshIbWhole] / f, b[kRefreshIbWhole] / f / 1048576.0,
       n[kRefreshIbRange] / f, n[kRefreshIbRangeRenamed] / f,
       (b[kRefreshIbRange] + b[kRefreshIbRangeRenamed]) / f / 1048576.0, n[kRefreshIbRangeSkip] / f);
'''
CEN_CPP_LINE_NEW = '''  /* madeira-bcd: DXMT_D9_VB_RANGE: the vertex buffer's range fields (stores, renames, MB, draws whose bytes were already stored) end the line. */
  line("buffer refresh per frame: vb whole %.1f (%.2f MB) | ib whole %.1f (%.2f MB) | ib range %.1f + %.1f renamed (%.3f MB), %.1f "
       "already stored | vb range %.1f + %.1f renamed (%.3f MB), %.1f already stored",
       n[kRefreshVbWhole] / f, b[kRefreshVbWhole] / f / 1048576.0, n[kRefreshIbWhole] / f, b[kRefreshIbWhole] / f / 1048576.0,
       n[kRefreshIbRange] / f, n[kRefreshIbRangeRenamed] / f,
       (b[kRefreshIbRange] + b[kRefreshIbRangeRenamed]) / f / 1048576.0, n[kRefreshIbRangeSkip] / f, n[kRefreshVbRange] / f,
       n[kRefreshVbRangeRenamed] / f, (b[kRefreshVbRange] + b[kRefreshVbRangeRenamed]) / f / 1048576.0,
       n[kRefreshVbRangeSkip] / f);
'''


def fail(msg):
    print(f"::error::patch-dxmt-d9-vb-range: {msg} -- dxmt moved, review this patch")
    return 1


def once(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f"{what}: anchor not found once ({text.count(old)} matches)")
    return text.replace(old, new)


def in_function(text, signature, old, new, what):
    """once() inside one function body, from its signature to the first closing brace at column 0."""
    at = text.find(signature)
    if at < 0 or text.find(signature, at + 1) >= 0:
        raise ValueError(f"{what}: function {signature!r} not found once")
    end = text.index("\n}\n", at)
    body = once(text[at:end], old, new, what)
    return text[:at] + body + text[end:]


def main():
    files = [BUF_HPP, BUF_CPP, DEV_HPP, DEV_CPP, CEN_HPP, CEN_CPP]
    for f in files:
        if not f.exists():
            return fail(f"{f} missing")
    texts = {f: f.read_text() for f in files}
    hdr = D / "d3d9_vb_range.hpp"
    done = [f for f in files if MARKER in texts[f]]
    if done and len(done) == len(files) and hdr.exists():
        print("d3d9: vertex-buffer range refresh (DXMT_D9_VB_RANGE) already patched")
        return 0
    if done:
        return fail("only some files carry the marker: " + ", ".join(str(f) for f in done))
    ib_hdr = D / "d3d9_ib_range.hpp"
    if not ib_hdr.exists() or any(IB_MARKER not in texts[f] for f in files):
        return fail("the index-buffer range patch (tools/patch-dxmt-d9-ib-range.py) has not been applied: this one extends it and runs right after it")
    try:
        t = dict(texts)
        t[BUF_HPP] = once(once(once(t[BUF_HPP], BUF_HPP_INC_OLD, BUF_HPP_INC_NEW, "d3d9_buffer.hpp includes"),
                               BUF_HPP_DECL_OLD, BUF_HPP_DECL_NEW, "d3d9_buffer.hpp MTLD3D9VertexBuffer::refreshWholeMirror"),
                          BUF_HPP_MEMBER_OLD, BUF_HPP_MEMBER_NEW, "d3d9_buffer.hpp MTLD3D9VertexBuffer members")
        b = once(t[BUF_CPP], BUF_CPP_KEY_OLD, BUF_CPP_KEY_NEW, "d3d9_buffer.cpp d9ib::rangeRefreshEnabled")
        guard = DIRECT_GUARD if "bool m_direct = false;" in t[BUF_HPP] else ""
        b = once(b, BUF_CPP_WHOLE_OLD, BUF_CPP_WHOLE_NEW.replace("@DIRECT_GUARD@", guard), "d3d9_buffer.cpp MTLD3D9VertexBuffer::refreshWholeMirror store")
        vb_lock = b.index("MTLD3D9VertexBuffer::Lock(UINT OffsetToLock, UINT SizeToLock, void **ppbData, DWORD Flags) {")
        vb_unlock = b.index("MTLD3D9VertexBuffer::Unlock() {")
        region = b[vb_lock:vb_unlock]
        b = b[:vb_lock] + once(region, BUF_CPP_LOCK_OLD, BUF_CPP_LOCK_NEW, "d3d9_buffer.cpp MTLD3D9VertexBuffer::Lock") + b[vb_unlock:]
        t[BUF_CPP] = b
        t[DEV_HPP] = once(once(t[DEV_HPP], DEV_HPP_INC_OLD, DEV_HPP_INC_NEW, "d3d9_device.hpp includes"),
                          DEV_HPP_DECL_OLD, DEV_HPP_DECL_NEW, "d3d9_device.hpp BuildDrawCapture")
        d = once(t[DEV_CPP], DEV_CPP_DEF_OLD, DEV_CPP_DEF_NEW, "d3d9_device.cpp BuildDrawCapture definition")
        d = once(d, DEV_CPP_LOOP_OLD, DEV_CPP_LOOP_NEW, "d3d9_device.cpp BuildDrawCapture vertex buffer refresh")
        d = once(d, DEV_CPP_FN_OLD, DEV_CPP_FN_NEW, "d3d9_device.cpp end of BuildDrawCapture")
        d = in_function(d, DP_SIG, DP_FAN_OLD, DP_FAN_NEW, "d3d9_device.cpp DrawPrimitive fan draw")
        d = in_function(d, DP_SIG, DP_PLAIN_OLD, DP_PLAIN_NEW, "d3d9_device.cpp DrawPrimitive non-indexed draw")
        t[DEV_CPP] = d
        t[CEN_HPP] = once(t[CEN_HPP], CEN_HPP_OLD, CEN_HPP_NEW, "d3d9_census.hpp refresh kinds")
        t[CEN_CPP] = once(t[CEN_CPP], CEN_CPP_LINE_OLD, CEN_CPP_LINE_NEW, "d3d9_census.cpp buffer refresh line")
    except ValueError as e:
        return fail(str(e))
    for f in files:
        if MARKER not in t[f]:
            return fail(f"{f}: the marker did not land")
    hdr.write_text(HEADER)
    for f in files:
        f.write_text(t[f])
    print("patched: d3d9 vertex-buffer range refresh (off unless DXMT_D9_VB_RANGE=1) + the vertex range fields of the census buffer refresh line")
    return 0


if __name__ == "__main__":
    sys.exit(main())
