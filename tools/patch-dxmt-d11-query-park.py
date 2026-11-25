#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
# Portions copyright 2026 Dragooon: code taken from https://github.com/willfaust/dxmt (pull request #58, commit 3dacbe5b: parking a burst of
# GetData polls on an event query) under GPL-3.0-or-later with the Madeira Converter Exception and modified for BCD in November 2026.
# BCD's terms above do not apply to those lines. See THIRD-PARTY-NOTICES.md.
"""A game that spins on ID3D11DeviceContext::GetData(event) parks on the queue's event instead (opt-in: env.MADEIRA_D3D11_QUERY_PARK = 1).

Games build a GPU fence out of `while (GetData(event) == S_FALSE) {}`. BCD's pulled logs show it in 42 logs of 21 titles at 100,000 to
15,000,000 polls a second ([gpu-perf] "not-ready polls/frame: event"; Schedule I, NSUNS1, Shroom and Gloom, Lonely Mountains, GalGun 2,
Palworld, Rocket League, ...). Under emulation each poll is an x64 -> ARM64EC round trip plus a Metal call, and the polling thread keeps a
performance core busy for nothing.

With MADEIRA_D3D11_QUERY_PARK=1 (exactly "1"; unset or anything else: this file changes nothing a game can see), once the polls of one
EVENT, TIMESTAMP or TIMESTAMP_DISJOINT query come in a burst (a third pending poll less than 250 us after the one before), GetData parks
the caller on the command queue's shared event until the query's value is signalled, at most 1 ms (Metal's wait is in milliseconds), and
then answers. A one-off poll never waits. It parks only once the chunk that holds the signal has been committed (before that no wait could
see it complete), and it does the flush GetData would do anyway first (no DONOTFLUSH). The [gpu-perf] line gains the parked polls and the
time parked per frame, only when there were some. "[query-park] madeira-bcd: ..." is said once, when the switch is first read.

The d11-query-stuck probe (tools/patch-dxmt-d11-query-stuck.py: [query-stuck] after 1.5 s, the timestamp watchdog after 2 s) counts polls and
reads the clock once in 65,536 of them; a parked poll that stayed pending can take 1 ms, which would put those two 44 times later. A poll that
parked and is still pending now reads the clock itself (a flag the park leaves, taken by the probe), so both keep their times.

Reach: PE side. The 32-bit d3d11.dll (the i386 farm builds it from these sources) and, for a 64-bit game, d3d11-src.dll
(env.MADEIRA_D3D11_SRC = 1, or MADEIRA_DXMT_FRESH = 1; tools/build-d3d11-dll.sh's BCD_PATCHES). The default 64-bit d3d11.dll is a prebuilt
binary and does not have it.

Applied after tools/patch-dxmt-d11-query-stuck.py (it reuses that script's ShouldBeSignaledAt(), the event id the query waits for, and its
probe). Idempotent (marker "madeira-bcd d11-query-park-v1"); exits 1 naming the anchor when one moved, the files unchanged.
Run from the repository root.
"""
import pathlib
import sys

MARKER = "madeira-bcd d11-query-park-v1"

HPP = pathlib.Path("dxmt/src/d3d11/d3d11_query.hpp")
CPP = pathlib.Path("dxmt/src/d3d11/d3d11_context_imm.cpp")
QHPP = pathlib.Path("dxmt/src/dxmt/dxmt_command_queue.hpp")
QCPP = pathlib.Path("dxmt/src/dxmt/dxmt_command_queue.cpp")

# ---------------------------------------------------------------- d3d11_query.hpp
H_CONST_OLD = """constexpr size_t kEventStallThreshold = 64;

struct MTLD3D11EventQuery : public ID3D11Query {
"""
H_CONST_NEW = """constexpr size_t kEventStallThreshold = 64;

/* """ + MARKER + """ (tools/patch-dxmt-d11-query-park.py): burst polling of an event query. Two pending polls less than this apart
 * continue a burst; after kEventPollsBeforePark of them the immediate context parks the caller on the queue's shared event (GetData). */
constexpr uint64_t kEventPollBurstGapNs = 250 * 1000;
constexpr uint32_t kEventPollsBeforePark = 2;

struct MTLD3D11EventQuery : public ID3D11Query {
"""
H_IFACE_OLD = """  virtual uint64_t ShouldBeSignaledAt() const { return 0; }
};
"""
H_IFACE_NEW = """  virtual uint64_t ShouldBeSignaledAt() const { return 0; }
  /* """ + MARKER + """: the command chunk that was being recorded when the query was issued (its signal reaches the GPU only once that
   * chunk is committed), and the burst counter of its pending polls. Defaults: no parking. */
  virtual void SetIssueChunk(uint64_t chunk_seq_id) { (void)chunk_seq_id; }
  virtual uint64_t IssueChunk() const { return 0; }
  /* Records a pending poll at now_ns; true when it continues a burst long enough to park on. */
  virtual bool NotePendingPoll(uint64_t now_ns) { (void)now_ns; return false; }
};
"""
H_ISSUE_OLD = """    should_be_signaled_at = current_seq_id;
    stall_counter = 0;
  }
"""
H_ISSUE_NEW = """    should_be_signaled_at = current_seq_id;
    stall_counter = 0;
    issue_chunk = 0;   /* """ + MARKER + """ */
    poll_burst = 0;
    last_pending_poll_ns = 0;
  }
"""
H_IMPL_OLD = """  uint64_t ShouldBeSignaledAt() const override { return should_be_signaled_at; }   /* madeira-bcd d11-query-stuck-v1 */

private:
  QueryState state = QueryState::Undefined;
  uint32_t stall_counter = 0;
  uint64_t should_be_signaled_at = 0;
};
"""
H_IMPL_NEW = """  uint64_t ShouldBeSignaledAt() const override { return should_be_signaled_at; }   /* madeira-bcd d11-query-stuck-v1 */

  /* """ + MARKER + """ */
  void SetIssueChunk(uint64_t chunk_seq_id) override { issue_chunk = chunk_seq_id; }
  uint64_t IssueChunk() const override { return issue_chunk; }
  bool NotePendingPoll(uint64_t now_ns) override {
    if (last_pending_poll_ns && now_ns - last_pending_poll_ns <= kEventPollBurstGapNs)
      poll_burst = poll_burst < kEventPollsBeforePark + 1 ? poll_burst + 1 : kEventPollsBeforePark + 1;
    else
      poll_burst = 1;
    last_pending_poll_ns = now_ns;
    return poll_burst > kEventPollsBeforePark;
  }

private:
  QueryState state = QueryState::Undefined;
  uint32_t stall_counter = 0;
  uint64_t should_be_signaled_at = 0;
  uint64_t issue_chunk = 0;   /* """ + MARKER + """ */
  uint32_t poll_burst = 0;
  uint64_t last_pending_poll_ns = 0;
};
"""

# ---------------------------------------------------------------- d3d11_context_imm.cpp
C_END_OLD = """        static_cast<MTLD3D11EventQuery *>(pAsync)->Issue(cmd_queue.GetCurrentEventSeqId());
      }
      break;
"""
C_END_NEW = """        static_cast<MTLD3D11EventQuery *>(pAsync)->Issue(cmd_queue.GetCurrentEventSeqId());
      }
      /* """ + MARKER + """: the signal (this one, or the earlier one it reuses) may still be in the chunk being recorded;
       * GetData parks only once that chunk is out. */
      static_cast<MTLD3D11EventQuery *>(pAsync)->SetIssueChunk(cmd_queue.CurrentSeqId());
      break;
"""
C_FN_OLD = """  HRESULT
  STDMETHODCALLTYPE
  GetData(ID3D11Asynchronous *pAsync, void *pData, UINT DataSize, UINT GetDataFlags) override {
"""
C_FN_NEW = """  /* """ + MARKER + """ (tools/patch-dxmt-d11-query-park.py, willfaust/dxmt pull request #58 by Dragooon; see that script).
   * Games build a GPU fence out of `while (GetData(event) == S_FALSE) {}`; once the polls of one query come in a burst this parks the
   * caller on the queue's shared event until the query's value is signalled, at most 1 ms, and then answers. A one-off poll never
   * waits. The park happens only once the chunk that holds the signal has been committed, after the flush GetData would do anyway.
   * Opt-in: env.MADEIRA_D3D11_QUERY_PARK = 1 (exactly "1"). */
  static bool
  madeiraQueryParkEnabled() {
    static const bool enabled = [] {
      const char *value = std::getenv("MADEIRA_D3D11_QUERY_PARK");
      bool on = value && !std::strcmp(value, "1");
      if (on)
        WARN("[query-park] madeira-bcd: D3D11 event-query parking on (env.MADEIRA_D3D11_QUERY_PARK = 1)");
      return on;
    }();
    return enabled;
  }

  /* Left by a park whose poll stayed pending, taken by madeiraQueryProbe: a parked poll takes up to 1 ms, so the probe's clock read
   * (once in 65,536 polls) would put [query-stuck] and the timestamp watchdog 44 times later. */
  static bool &
  madeiraParkedFlag() {
    static thread_local bool parked = false;
    return parked;
  }

  static bool
  madeiraParkedPollTaken() {
    bool &flag = madeiraParkedFlag();
    const bool taken = flag;
    flag = false;
    return taken;
  }

  HRESULT
  madeiraParkOnEventQuery(MTLD3D11EventQuery *query, UINT &GetDataFlags) {
    if ((GetDataFlags & D3D11_ASYNC_GETDATA_DONOTFLUSH) == 0) {
      cmd_queue.CurrentFrameStatistics().event_stall++;
      Flush();
      GetDataFlags |= D3D11_ASYNC_GETDATA_DONOTFLUSH; /* done: the tail of GetData must not flush again */
    }
    auto now = std::chrono::steady_clock::now();
    uint64_t now_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(now.time_since_epoch()).count();
    if (!query->NotePendingPoll(now_ns) || cmd_queue.CurrentSeqId() <= query->IssueChunk())
      return S_FALSE;
    cmd_queue.event.waitUntilSignaledValue(query->ShouldBeSignaledAt(), 1);
    g_perf.polls_parked.fetch_add(1, std::memory_order_relaxed);
    g_perf.park_ns.fetch_add(
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - now).count(),
        std::memory_order_relaxed);
    if (query->CheckEventState(cmd_queue.SignaledEventSeqId()) == EventState::Signaled)
      return S_OK;
    madeiraParkedFlag() = true;
    return S_FALSE;
  }

  HRESULT
  STDMETHODCALLTYPE
  GetData(ID3D11Asynchronous *pAsync, void *pData, UINT DataSize, UINT GetDataFlags) override {
"""
C_CALL_OLD = """      case EventState::Invalid:
        return DXGI_ERROR_INVALID_CALL;
      }
      break;
    }
    default:
      break;
    }
    /* madeira-bcd d11-query-stuck-v1: the probe of an event-class query (madeiraQueryProbe above) */
"""
C_CALL_NEW = """      case EventState::Invalid:
        return DXGI_ERROR_INVALID_CALL;
      }
      if (hr == S_FALSE && madeiraQueryParkEnabled())   /* """ + MARKER + """ */
        hr = madeiraParkOnEventQuery(static_cast<MTLD3D11EventQuery *>(pAsync), GetDataFlags);
      break;
    }
    default:
      break;
    }
    /* madeira-bcd d11-query-stuck-v1: the probe of an event-class query (madeiraQueryProbe above) */
"""
C_PROBE_OLD = """    if (++probe.polls % 65536)
      return;
"""
C_PROBE_NEW = """    const bool parked = madeiraParkedPollTaken();   /* """ + MARKER + """: a poll that parked and stayed pending reads the clock */
    if (++probe.polls % 65536 && !parked)
      return;
"""

# ---------------------------------------------------------------- dxmt_command_queue.hpp / .cpp
Q_HPP_OLD = """  std::atomic<uint64_t> polls_event{0}, polls_other{0};
"""
Q_HPP_NEW = """  std::atomic<uint64_t> polls_event{0}, polls_other{0};
  std::atomic<uint64_t> polls_parked{0}, park_ns{0};   /* """ + MARKER + """: D3D11 event-query parking */
"""
Q_TAKE_OLD = """  const double pe = take(g_perf.polls_event), po = take(g_perf.polls_other);
"""
Q_TAKE_NEW = """  const double pe = take(g_perf.polls_event), po = take(g_perf.polls_other);
  const double parked = take(g_perf.polls_parked), park_ms = take(g_perf.park_ns) / 1e6;   /* """ + MARKER + """ */
"""
Q_BUF_OLD = """  char line[800];
"""
Q_BUF_NEW = """  char line[900];
"""
Q_ERR_OLD = """  ERR(line);
  perf_.window_frame = frame;
"""
Q_ERR_NEW = """  if (parked > 0) {   /* """ + MARKER + """: the line is as before unless MADEIRA_D3D11_QUERY_PARK parked a poll */
    const size_t used = __builtin_strlen(line);
    snprintf(line + used, sizeof(line) - used, " | parked event polls/frame %.1f, %.2f ms/frame", parked / frames, park_ms / frames);
  }
  ERR(line);
  perf_.window_frame = frame;
"""

EDITS = {
    HPP: [(H_CONST_OLD, H_CONST_NEW, "d3d11_query.hpp: kEventStallThreshold before MTLD3D11EventQuery"),
          (H_IFACE_OLD, H_IFACE_NEW, "d3d11_query.hpp: MTLD3D11EventQuery's ShouldBeSignaledAt (patch-dxmt-d11-query-stuck.py runs first)"),
          (H_ISSUE_OLD, H_ISSUE_NEW, "d3d11_query.hpp: MTLD3D11EventQueryImpl::Issue"),
          (H_IMPL_OLD, H_IMPL_NEW, "d3d11_query.hpp: MTLD3D11EventQueryImpl's private state (patch-dxmt-d11-query-stuck.py runs first)")],
    CPP: [(C_END_OLD, C_END_NEW, "d3d11_context_imm.cpp: End()'s event query issue"),
          (C_FN_OLD, C_FN_NEW, "d3d11_context_imm.cpp: GetData's head"),
          (C_CALL_OLD, C_CALL_NEW, "d3d11_context_imm.cpp: GetData's event state switch (patch-dxmt-d11-query-stuck.py runs first)"),
          (C_PROBE_OLD, C_PROBE_NEW, "d3d11_context_imm.cpp: madeiraQueryProbe's poll counter (patch-dxmt-d11-query-stuck.py runs first)")],
    QHPP: [(Q_HPP_OLD, Q_HPP_NEW, "dxmt_command_queue.hpp: PerfCounters::polls_event")],
    QCPP: [(Q_TAKE_OLD, Q_TAKE_NEW, "dxmt_command_queue.cpp: AccountGpuTime's poll counters"),
           (Q_BUF_OLD, Q_BUF_NEW, "dxmt_command_queue.cpp: the [gpu-perf] line buffer"),
           (Q_ERR_OLD, Q_ERR_NEW, "dxmt_command_queue.cpp: AccountGpuTime's ERR(line)")],
}


def main():
    texts = {}
    for path in EDITS:
        if not path.is_file():
            print(f"::error::patch-dxmt-d11-query-park: {path} not found -- run from the repository root, dxmt moved")
            return 1
        texts[path] = path.read_text()
    marked = [MARKER in t for t in texts.values()]
    if all(marked):
        print("d3d11 query parking: already patched")
        return 0
    if any(marked):
        print("::error::patch-dxmt-d11-query-park: only some files carry the marker -- dxmt moved, review this patch")
        return 1
    if "madeira-bcd d11-query-stuck-v1" not in texts[HPP] or "madeira-bcd d11-query-stuck-v1" not in texts[CPP]:
        print("::error::patch-dxmt-d11-query-park: tools/patch-dxmt-d11-query-stuck.py has not been applied -- it must run first")
        return 1
    for path, edits in EDITS.items():
        for old, _new, name in edits:
            n = texts[path].count(old)
            if n != 1:
                print(f"::error::patch-dxmt-d11-query-park: {name}: anchor found {n} times (want 1) -- dxmt moved, review this patch")
                return 1
    for path, edits in EDITS.items():
        out = texts[path]
        for old, new, _name in edits:
            out = out.replace(old, new, 1)
        path.write_text(out)
    print("d3d11 query parking patched (env.MADEIRA_D3D11_QUERY_PARK = 1; off by default)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
