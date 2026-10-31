#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Direct3D 9 query that never completes: name it, and an opt-in way out (round 624, agent D2 S18; Counter-Strike: Source W69RYK).

Counter-Strike: Source (64-bit, DXMT's Direct3D 9 through MADEIRA_D3D9_X64 = dxmt) played its menu at 60 FPS for 139 s, then its
render thread polled MTLD3D9Query::GetData 13.8 million times a second for good and no frame came again. DXMT's park on the GPU
fence was not in play, so the GPU was not what the query waited for. The likely case is an occlusion query whose end fell into a
command buffer without visibility samples and was dropped (the 32-bit Direct3D 9 answers it with DXMT_QUERY_SETTLE_EMPTY, on by
default there and off in d3d9-dxmt.dll), but no line said which query it was or in which state.

1. [d3d9-query-stuck] (always on; one compare a pending poll): a query still pending after 1,048,576 polls (a healthy one completes
   within 3 to 17) says once what it waits for, 16 lines a process at most:

     [d3d9-query-stuck] madeira-bcd type=9 began=1 ended=1 event_seq=7021 current_seq=7024 coherent_seq=7023 vis=1 vis_begin=7021
       vis_end=7021 vis_issued=7020 polls=1048576 ms_since_issue=76 (lost: the work it was issued behind has retired, the result never came)

   the other verdicts: "building: Issue(END) never came", "the GPU is behind: coherent_seq < event_seq".
2. DXMT_D9_QUERY_LOST_MS = <ms> (100-60000; 0 or unset: off): an OCCLUSION query still pending that long after Issue(END) while the
   work it was issued behind has retired (CoherentSeqId >= its seq: no GPU wait can explain it) is answered as visible (0x7fffffff
   samples), so a culling loop draws instead of waiting for ever, until the next Issue(END); [d3d9-query-lost] says so (16 lines).
   Only a state that today never ends changes, and only in a game whose file sets the key.

PE side: the i386 d3d9.dll (the i386 farm key hashes tools/patch-dxmt-*.py) and the arm64ec d3d9-dxmt.dll built from this tree in
CI. Applied after tools/patch-dxmt-d9-texfmt-log.py. Idempotent (marker "d9-query-stuck-v1"); exits 1 naming the anchor when one
moved, the files unchanged. Run from the repository root.
"""
import pathlib
import sys

D = pathlib.Path("dxmt/src")
MARKER = "d9-query-stuck-v1"

OCC = D / "dxmt/dxmt_occlusion_query.hpp"
OCC_OLD = """  bool
  getValue(uint64_t *value) {
    if (seq_id_end <= seq_id_issued) {"""
OCC_NEW = """  /* madeira-bcd d9-query-stuck-v1 (round 624; tools/patch-dxmt-d9-query-stuck.py): the state [d3d9-query-stuck] prints. Reads only. */
  void
  debugState(uint64_t *begin, uint64_t *end, uint64_t *issued) const {
    *begin = seq_id_begin;
    *end = seq_id_end;
    *issued = seq_id_issued;
  }

  bool
  getValue(uint64_t *value) {
    if (seq_id_end <= seq_id_issued) {"""

HPP = D / "d3d9/d3d9_query.hpp"
HPP_OLD = """  uint64_t m_issue_ns = 0;
"""
HPP_NEW = """  uint64_t m_issue_ns = 0;
  // madeira-bcd d9-query-stuck-v1 (round 624): DXMT_D9_QUERY_LOST_MS answered this OCCLUSION query as visible; until the next
  // Issue(END) every poll gets the same answer.
  bool m_lost_answered = false;
"""

CPP = D / "d3d9/d3d9_query.cpp"
CPP_FN_OLD = """HRESULT STDMETHODCALLTYPE
MTLD3D9Query::GetData(void *pData, DWORD dwSize, DWORD dwGetDataFlags) {
"""
CPP_FN_NEW = """/* madeira-bcd d9-query-stuck-v1 (round 624, Counter-Strike: Source W69RYK; tools/patch-dxmt-d9-query-stuck.py): the watchdog's
 * decision, pure (tests/host/check-d9-query-stuck.py compiles it): a query that has ended, whose work has retired (the coherent seq
 * is at or past its own) and that is still pending limit_ms after Issue(END). limit_ms 0: never. */
/* d9-query-lost-test:begin */
static bool
madeira_d9_query_lost(bool ended, uint64_t event_seq, uint64_t coherent_seq, uint64_t issue_ns, uint64_t now_ns, uint32_t limit_ms) {
  return limit_ms && ended && event_seq && issue_ns && coherent_seq >= event_seq && now_ns >= issue_ns &&
         now_ns - issue_ns >= (uint64_t)limit_ms * 1000000ull;
}
/* d9-query-lost-test:end */

/* DXMT_D9_QUERY_LOST_MS, read once: 100-60000 ms, anything else (unset, 0, a word) off. */
static uint32_t
madeira_d9_query_lost_ms() {
  static const uint32_t ms = [] {
    const char *v = std::getenv("DXMT_D9_QUERY_LOST_MS");
    long n = v ? std::strtol(v, nullptr, 10) : 0;
    uint32_t r = (n >= 100 && n <= 60000) ? (uint32_t)n : 0;
    if (r)
      Logger::warn(str::format("[d3d9-query-lost] madeira-bcd watchdog on: an occlusion query whose work retired ", r,
                               " ms before without its result is answered as visible (DXMT_D9_QUERY_LOST_MS)"));
    return r;
  }();
  return ms;
}

HRESULT STDMETHODCALLTYPE
MTLD3D9Query::GetData(void *pData, DWORD dwSize, DWORD dwGetDataFlags) {
"""

CPP_POLL_OLD = """    m_polls_since_issue++;
    // ml1150: the old lifetime count penalized an asynchronous culling loop
"""
CPP_POLL_NEW = """    /* madeira-bcd d9-query-stuck-v1: the opt-in watchdog (madeira_d9_query_lost above); off unless the game's file sets the key */
    if (m_type == D3DQUERYTYPE_OCCLUSION && madeira_d9_query_lost_ms()) {
      bool lost = m_lost_answered && m_ended;
      if (!lost) {
        uint64_t now = (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(
                           std::chrono::steady_clock::now().time_since_epoch())
                           .count();
        if (madeira_d9_query_lost(m_ended, m_event_seq, m_device->dxmtQueue().CoherentSeqId(), m_issue_ns, now,
                                  madeira_d9_query_lost_ms())) {
          static std::atomic<uint32_t> lost_said{0};
          if (lost_said.fetch_add(1, std::memory_order_relaxed) < 16)
            Logger::warn(str::format("[d3d9-query-lost] madeira-bcd an occlusion query pending ", (now - m_issue_ns) / 1000000,
                                     " ms after Issue(END), its work retired (event_seq=", m_event_seq, ", ", m_polls_since_issue,
                                     " polls), is answered as visible"));
          m_lost_answered = true;
          noteCompletion();
          lost = true;
        }
      }
      if (lost) {
        if (pData && dwSize) {
          uint64_t visible = 0x7fffffffull;
          std::memcpy(pData, &visible, std::min<DWORD>(dwSize, sizeof(visible)));
        }
        census::queryPoll(true, false);
        return D3D_OK;
      }
    }
    m_polls_since_issue++;
    /* madeira-bcd d9-query-stuck-v1: a query still pending after 1,048,576 polls (a healthy one completes within 3-17) says once what it
     * waits for; one compare a pending poll, 16 lines a process at most. */
    if (m_polls_since_issue == 1048576u) {
      static std::atomic<uint32_t> stuck_said{0};
      if (stuck_said.fetch_add(1, std::memory_order_relaxed) < 16) {
        auto &q = m_device->dxmtQueue();
        uint64_t vb = 0, ve = 0, vi = 0, now = (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(
                                                    std::chrono::steady_clock::now().time_since_epoch())
                                                    .count();
        uint64_t current = q.CurrentSeqId(), coherent = q.CoherentSeqId();
        if (m_visibility_query)
          m_visibility_query->debugState(&vb, &ve, &vi);
        const char *why = m_began && !m_ended          ? "building: Issue(END) never came"
                          : coherent < m_event_seq      ? "the GPU is behind: coherent_seq < event_seq"
                          : m_type == D3DQUERYTYPE_OCCLUSION ? "lost: the work it was issued behind has retired, the result never came"
                                                              : "pending with its work retired";
        Logger::warn(str::format("[d3d9-query-stuck] madeira-bcd type=", (unsigned)m_type, " began=", (int)m_began, " ended=",
                                 (int)m_ended, " event_seq=", m_event_seq, " current_seq=", current, " coherent_seq=", coherent,
                                 " vis=", m_visibility_query ? 1 : 0, " vis_begin=", vb, " vis_end=", ve, " vis_issued=", vi,
                                 " polls=", m_polls_since_issue, " ms_since_issue=",
                                 m_issue_ns && now >= m_issue_ns ? (now - m_issue_ns) / 1000000 : 0, " (", why, ")"));
      }
    }
    // ml1150: the old lifetime count penalized an asynchronous culling loop
"""

CPP_ISSUE_OLD = """    m_polls_since_issue = 0;
    m_last_pending_poll_ns = 0;
    m_poll_burst = 0;
"""
CPP_ISSUE_NEW = """    m_polls_since_issue = 0;
    m_last_pending_poll_ns = 0;
    m_poll_burst = 0;
    m_lost_answered = false;   /* madeira-bcd d9-query-stuck-v1: a new result window */
"""


def main():
    texts = {p: p.read_text() for p in (OCC, HPP, CPP)}
    marked = [MARKER in t for t in texts.values()]
    if all(marked):
        print("d3d9 stuck queries: already patched")
        return 0
    if any(marked):
        print("::error::patch-dxmt-d9-query-stuck: only some files carry the marker -- dxmt moved, review this patch")
        return 1
    checks = ((OCC, OCC_OLD, "dxmt_occlusion_query.hpp: VisibilityResultQuery::getValue"),
              (HPP, HPP_OLD, "d3d9_query.hpp: m_issue_ns"),
              (CPP, CPP_FN_OLD, "d3d9_query.cpp: MTLD3D9Query::GetData"),
              (CPP, CPP_POLL_OLD, "d3d9_query.cpp: GetData's pending-poll count"),
              (CPP, CPP_ISSUE_OLD, "d3d9_query.cpp: Issue(END)'s poll reset"))
    for path, old, name in checks:
        n = texts[path].count(old)
        if n != 1:
            print(f"::error::patch-dxmt-d9-query-stuck: {name}: anchor found {n} times (want 1) -- dxmt moved, review this patch")
            return 1
    OCC.write_text(texts[OCC].replace(OCC_OLD, OCC_NEW, 1))
    HPP.write_text(texts[HPP].replace(HPP_OLD, HPP_NEW, 1))
    CPP.write_text(texts[CPP].replace(CPP_FN_OLD, CPP_FN_NEW, 1).replace(CPP_POLL_OLD, CPP_POLL_NEW, 1).replace(CPP_ISSUE_OLD, CPP_ISSUE_NEW, 1))
    print("d3d9 stuck queries patched ([d3d9-query-stuck]; DXMT_D9_QUERY_LOST_MS, off by default)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
