#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A Direct3D 11 event-class query that never completes: name what it waits for, and an opt-in way out for the two types DXMT invents
anyway (round 624, agent D2 S11; WRC 9, Send Logs JDJ8KE = WV1P7T).

WRC 9 drew 32 frames, then its main thread called ID3D11DeviceContext::GetData 12.5 million times a second for two minutes on a
TIMESTAMP_DISJOINT query that stayed pending, while a second thread hashed at 100 %. The log cannot say whether the query's event was
emitted but its chunk never committed, a committed command buffer is stuck on the GPU, or its event id is never signalled at all.

1. [query-stuck] (always on): an EVENT, TIMESTAMP or TIMESTAMP_DISJOINT query that has answered S_FALSE for 1.5 s says what it waits
   for, then every 10 s, 6 lines a process:

     [query-stuck] madeira-bcd query=0x... type=4 polls=1179648 for 1502 ms | want=8812 signaled=8811 cur_event=8812 cur_seq=7024
       coherent=7023 | dirty=0 promote=0

   want: the event id the query waits for; signaled: the shared event's value; cur_event: the last id handed out; cur_seq/coherent: the
   command queue; dirty/promote: whether the context holds work that a Flush would submit. The clock is read once in 65,536 polls of
   one query (a thread's probe), so the cost on a healthy run is a counter.
2. DXMT_QUERY_TS_WATCHDOG = 1 (off unless a game's file sets it): a TIMESTAMP or TIMESTAMP_DISJOINT query still pending after 2 s of
   such polling is answered as complete until it is issued again. GetData already returns invented data for these two types (0, and
   1 GHz not disjoint) whatever the GPU did, so a late completion changes no number; [query-watchdog] says so (6 lines).

PE side: the i386 d3d11.dll and d3d11-src.dll (a 64-bit game gets them with env.MADEIRA_D3D11_SRC = 1; the default 64-bit d3d11.dll is a
prebuilt file). Applied after tools/patch-dxmt-d9-query-stuck.py. Idempotent (marker "d11-query-stuck-v1"); exits 1 naming the
anchor when one moved, the files unchanged. Run from the repository root.
"""
import pathlib
import sys

D = pathlib.Path("dxmt/src/d3d11")
MARKER = "d11-query-stuck-v1"

HPP = D / "d3d11_query.hpp"
HPP_IFACE_OLD = """  virtual EventState CheckEventState(uint64_t coherent_seq_id) = 0;
};
"""
HPP_IFACE_NEW = """  virtual EventState CheckEventState(uint64_t coherent_seq_id) = 0;
  /* madeira-bcd d11-query-stuck-v1 (round 624; tools/patch-dxmt-d11-query-stuck.py): the event id the query waits for, for
   * [query-stuck]. Reads only. */
  virtual uint64_t ShouldBeSignaledAt() const { return 0; }
};
"""
HPP_IMPL_OLD = """    return EventState::Stall;
  };

private:
  QueryState state = QueryState::Undefined;
"""
HPP_IMPL_NEW = """    return EventState::Stall;
  };

  uint64_t ShouldBeSignaledAt() const override { return should_be_signaled_at; }   /* madeira-bcd d11-query-stuck-v1 */

private:
  QueryState state = QueryState::Undefined;
"""

CPP = D / "d3d11_context_imm.cpp"
CPP_FN_OLD = """  HRESULT
  STDMETHODCALLTYPE
  GetData(ID3D11Asynchronous *pAsync, void *pData, UINT DataSize, UINT GetDataFlags) override {
"""
CPP_FN_NEW = """  /* madeira-bcd d11-query-stuck-v1 (round 624, agent D2 S11; WRC 9 JDJ8KE/WV1P7T; tools/patch-dxmt-d11-query-stuck.py): see that script.
   * A thread's probe of the event-class query it polls: [query-stuck] after 1.5 s of S_FALSE (then every 10 s, 6 lines a process), and
   * with DXMT_QUERY_TS_WATCHDOG=1 a TIMESTAMP or TIMESTAMP_DISJOINT query pending 2 s is answered as complete until it is issued again.
   * The clock is read once in 65536 polls of one query; hr is changed only by the watchdog. */
  void
  madeiraQueryProbe(ID3D11Asynchronous *query, D3D11_QUERY type, HRESULT &hr) {
    struct Probe {
      const void *query;
      uint64_t want, t0_ns, polls, next_ms;
      bool forced;
    };
    static thread_local Probe probe = {};
    static std::atomic<uint32_t> stuck_said{0}, forced_said{0};
    static const bool watchdog = [] {
      const char *v = std::getenv("DXMT_QUERY_TS_WATCHDOG");
      bool on = v && v[0] == '1' && !v[1];
      if (on)
        WARN("[query-watchdog] madeira-bcd on: a timestamp query pending 2 s is answered as complete (DXMT_QUERY_TS_WATCHDOG)");
      return on;
    }();
    if (hr == S_OK) {
      if (probe.query == query)
        probe.query = nullptr;
      return;
    }
    if (hr != S_FALSE)
      return;
    const uint64_t want = static_cast<MTLD3D11EventQuery *>(query)->ShouldBeSignaledAt();
    auto now_ns = [] {
      return (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch())
          .count();
    };
    if (probe.query != query || probe.want != want)
      probe = Probe{query, want, now_ns(), 0, 1500, false};
    if (probe.forced) {
      hr = S_OK;
      return;
    }
    if (++probe.polls % 65536)
      return;
    const uint64_t ms = (now_ns() - probe.t0_ns) / 1000000;
    if (ms >= probe.next_ms) {
      probe.next_ms = ms + 10000;
      if (stuck_said.fetch_add(1, std::memory_order_relaxed) < 6)
        WARN("[query-stuck] madeira-bcd query=", query, " type=", (unsigned)type, " polls=", probe.polls, " for ", ms,
             " ms | want=", want, " signaled=", cmd_queue.SignaledEventSeqId(), " cur_event=", cmd_queue.GetCurrentEventSeqId(),
             " cur_seq=", cmd_queue.CurrentSeqId(), " coherent=", cmd_queue.CoherentSeqId(),
             " | dirty=", (int)ctx_state.has_dirty_op_since_last_event, " promote=", (int)promote_flush);
    }
    if (watchdog && (type == D3D11_QUERY_TIMESTAMP || type == D3D11_QUERY_TIMESTAMP_DISJOINT) && ms >= 2000) {
      probe.forced = true;
      hr = S_OK;
      if (forced_said.fetch_add(1, std::memory_order_relaxed) < 6)
        WARN("[query-watchdog] madeira-bcd query=", query, " type=", (unsigned)type, " pending ", ms, " ms (want=", want,
             " signaled=", cmd_queue.SignaledEventSeqId(), ") is answered as complete; its data is DXMT's own (0, 1 GHz)");
    }
  }

  HRESULT
  STDMETHODCALLTYPE
  GetData(ID3D11Asynchronous *pAsync, void *pData, UINT DataSize, UINT GetDataFlags) override {
"""
CPP_CALL_OLD = """    default:
      break;
    }
    switch (desc.Query) {
    case D3D11_QUERY_EVENT: {
      if (pData)
"""
CPP_CALL_NEW = """    default:
      break;
    }
    /* madeira-bcd d11-query-stuck-v1: the probe of an event-class query (madeiraQueryProbe above) */
    if (desc.Query == D3D11_QUERY_EVENT || desc.Query == D3D11_QUERY_TIMESTAMP || desc.Query == D3D11_QUERY_TIMESTAMP_DISJOINT)
      madeiraQueryProbe(pAsync, desc.Query, hr);
    switch (desc.Query) {
    case D3D11_QUERY_EVENT: {
      if (pData)
"""


def main():
    texts = {p: p.read_text() for p in (HPP, CPP)}
    marked = [MARKER in t for t in texts.values()]
    if all(marked):
        print("d3d11 stuck queries: already patched")
        return 0
    if any(marked):
        print("::error::patch-dxmt-d11-query-stuck: only some files carry the marker -- dxmt moved, review this patch")
        return 1
    checks = ((HPP, HPP_IFACE_OLD, "d3d11_query.hpp: MTLD3D11EventQuery::CheckEventState"),
              (HPP, HPP_IMPL_OLD, "d3d11_query.hpp: MTLD3D11EventQueryImpl's private state"),
              (CPP, CPP_FN_OLD, "d3d11_context_imm.cpp: GetData"),
              (CPP, CPP_CALL_OLD, "d3d11_context_imm.cpp: GetData's event state switch"))
    for path, old, name in checks:
        n = texts[path].count(old)
        if n != 1:
            print(f"::error::patch-dxmt-d11-query-stuck: {name}: anchor found {n} times (want 1) -- dxmt moved, review this patch")
            return 1
    HPP.write_text(texts[HPP].replace(HPP_IFACE_OLD, HPP_IFACE_NEW, 1).replace(HPP_IMPL_OLD, HPP_IMPL_NEW, 1))
    CPP.write_text(texts[CPP].replace(CPP_FN_OLD, CPP_FN_NEW, 1).replace(CPP_CALL_OLD, CPP_CALL_NEW, 1))
    print("d3d11 stuck queries patched ([query-stuck]; DXMT_QUERY_TS_WATCHDOG, off by default)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
