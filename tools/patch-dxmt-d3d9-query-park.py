#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""How much of a Direct3D 9 frame is the query wait (round 624, agent D2 S16; Far Cry 2, Send Log Z7CADQ).

Far Cry 2 draws 42 frames a second in the game and 60 in its menus; it polls GPU queries 89.9 times a frame, 86 % of the polls
park on the GPU fence, and a query's issue-to-complete is 16.4 ms in the menus and 24.4 ms overall, the length of a frame
([d3d9-query] lines). Whether that wait is what holds the game at 42 is the open question: the line has the share of parked polls
and the average latency, not the time spent parked nor the spread. With MADEIRA_FRAME_STATS on (the [frame] instrument's switch,
read once), MTLD3D9Query::GetData times its bounded park on the fence and the completions fill a 0.5 ms histogram of
issue-to-complete; reportQueries prints a third line a window:

  [d3d9-query] parked 9.80 ms/f in the fence wait (49012 ms this window) | issue_to_complete p50 <=16.5ms p90 <=24.0ms p99 <=31.5ms

Without the switch: no clock read, no histogram, the two lines as before. Applied after tools/patch-dxmt-d9-ib-range.py (which
edits the same census file elsewhere). PE side: i386 d3d9.dll (the i386 farm key hashes tools/patch-dxmt-*.py) and every d3d9
built from this source. Idempotent (marker "d3d9-query-park-v1"); exits 1 naming the anchor when one moved, the files unchanged.
Run from the repository root.
"""
import pathlib
import sys

D = pathlib.Path("dxmt/src/d3d9")
MARKER = "d3d9-query-park-v1"

HPP = D / "d3d9_census.hpp"
HPP_OLD = """void queryPoll(bool complete, bool parked);
void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls);
"""
HPP_NEW = """void queryPoll(bool complete, bool parked);
void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls);
/* madeira-bcd d3d9-query-park-v1 (round 624, Far Cry 2; tools/patch-dxmt-d3d9-query-park.py): with MADEIRA_FRAME_STATS on, the
 * time GetData spent parked on the GPU fence and the spread of issue-to-complete, on a third [d3d9-query] line. queryParkClock()
 * says whether to time the park (read once); queryParkedNs adds one park's duration. */
bool queryParkClock();
void queryParkedNs(uint64_t ns);
"""

CPP = D / "d3d9_census.cpp"
CPP_VARS_OLD = """std::atomic<uint32_t> g_q_latency_max_us;
"""
CPP_VARS_NEW = """std::atomic<uint32_t> g_q_latency_max_us;
/* madeira-bcd d3d9-query-park-v1: parked time and a 0.5 ms histogram of issue-to-complete (the last bucket: 64 ms and more). */
constexpr unsigned kQueryLatBuckets = 129;
std::atomic<uint64_t> g_q_park_ns;
std::atomic<uint32_t> g_q_lat_hist[kQueryLatBuckets];
std::atomic<int> g_q_park_clock{-1};   /* -1 unread, 0 off, 1 on */
uint64_t g_prev_q_park_ns;
uint32_t g_prev_q_lat_hist[kQueryLatBuckets];
"""

CPP_REPORT_OLD = """  if (!w_issued && !w_polls) {
    qline("no query traffic this window");
  } else {
    qline("issued=%u (%.1f/f) flush_submits=%u polls=%u (%.1f/f) hit=%u parked=%u (%.1f%% of polls)", w_issued,
          (double)w_issued / (double)frames, w_flushed, w_polls, (double)w_polls / (double)frames, w_hits, w_parked,
          w_polls ? 100.0 * (double)w_parked / (double)w_polls : 0.0);
    qline("completions=%u polls_per_completion=%.1f issue_to_complete_avg=%.1fus worst_ever=%uus", w_done,
          w_done ? (double)w_polls / (double)w_done : 0.0, w_done ? (double)w_latency / (double)w_done / 1000.0 : 0.0,
          worst);
  }
"""
CPP_REPORT_NEW = """  /* madeira-bcd d3d9-query-park-v1: this window's parked time and latency histogram (MADEIRA_FRAME_STATS only) */
  const bool w_park_on = queryParkClock();
  uint64_t w_park_ns = 0;
  uint32_t w_hist[kQueryLatBuckets] = {}, w_hist_total = 0;
  if (w_park_on) {
    uint64_t park = g_q_park_ns.load(std::memory_order_relaxed);
    w_park_ns = park - g_prev_q_park_ns;
    g_prev_q_park_ns = park;
    for (unsigned k = 0; k < kQueryLatBuckets; k++) {
      uint32_t c = g_q_lat_hist[k].load(std::memory_order_relaxed);
      w_hist[k] = c - g_prev_q_lat_hist[k];
      g_prev_q_lat_hist[k] = c;
      w_hist_total += w_hist[k];
    }
  }
  /* the upper edge (ms) of the bucket that holds percentile p of this window's completions */
  auto w_pct = [&](uint32_t p) -> double {
    uint32_t want = (uint32_t)(((uint64_t)w_hist_total * p + 99) / 100), seen = 0;
    if (!want)
      want = 1;
    for (unsigned k = 0; k < kQueryLatBuckets; k++) {
      seen += w_hist[k];
      if (seen >= want)
        return (double)(k + 1) * 0.5;
    }
    return (double)kQueryLatBuckets * 0.5;
  };

  if (!w_issued && !w_polls) {
    qline("no query traffic this window");
  } else {
    qline("issued=%u (%.1f/f) flush_submits=%u polls=%u (%.1f/f) hit=%u parked=%u (%.1f%% of polls)", w_issued,
          (double)w_issued / (double)frames, w_flushed, w_polls, (double)w_polls / (double)frames, w_hits, w_parked,
          w_polls ? 100.0 * (double)w_parked / (double)w_polls : 0.0);
    qline("completions=%u polls_per_completion=%.1f issue_to_complete_avg=%.1fus worst_ever=%uus", w_done,
          w_done ? (double)w_polls / (double)w_done : 0.0, w_done ? (double)w_latency / (double)w_done / 1000.0 : 0.0,
          worst);
    if (w_park_on)
      qline("parked %.2f ms/f in the fence wait (%.0f ms this window) | issue_to_complete p50 <=%.1fms p90 <=%.1fms p99 <=%.1fms%s",
            frames ? (double)w_park_ns / 1e6 / (double)frames : 0.0, (double)w_park_ns / 1e6,
            w_hist_total ? w_pct(50) : 0.0, w_hist_total ? w_pct(90) : 0.0, w_hist_total ? w_pct(99) : 0.0,
            w_hist[kQueryLatBuckets - 1] ? " (some 64 ms or more)" : "");
  }
"""

CPP_FN_OLD = """void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls) {
  (void)polls;
  g_q_completions.fetch_add(1, std::memory_order_relaxed);
  g_q_latency_ns.fetch_add(issue_to_complete_ns, std::memory_order_relaxed);
  uint32_t us = (uint32_t)(issue_to_complete_ns / 1000u);
"""
CPP_FN_NEW = """/* madeira-bcd d3d9-query-park-v1: MADEIRA_FRAME_STATS as the [frame] instrument reads it (set and not "0": on), once. */
bool queryParkClock() {
  int on = g_q_park_clock.load(std::memory_order_relaxed);
  if (on < 0) {
    std::string v = env::getEnvVar("MADEIRA_FRAME_STATS");
    on = (!v.empty() && v != "0") ? 1 : 0;
    g_q_park_clock.store(on, std::memory_order_relaxed);
  }
  return on == 1;
}

void queryParkedNs(uint64_t ns) {
  g_q_park_ns.fetch_add(ns, std::memory_order_relaxed);
}

void queryCompleted(uint64_t issue_to_complete_ns, uint32_t polls) {
  (void)polls;
  g_q_completions.fetch_add(1, std::memory_order_relaxed);
  g_q_latency_ns.fetch_add(issue_to_complete_ns, std::memory_order_relaxed);
  uint32_t us = (uint32_t)(issue_to_complete_ns / 1000u);
  if (queryParkClock()) {   /* madeira-bcd d3d9-query-park-v1 */
    unsigned b = us / 500u;
    g_q_lat_hist[b < kQueryLatBuckets ? b : kQueryLatBuckets - 1].fetch_add(1, std::memory_order_relaxed);
  }
"""

QRY = D / "d3d9_query.cpp"
QRY_OLD = """  bool ready = m_device->dxmtQueue().WaitCPUFenceBounded(park_seq, kPollParkNanos);
  if (!ready) {
"""
QRY_NEW = """  /* madeira-bcd d3d9-query-park-v1: with MADEIRA_FRAME_STATS the park is timed for the [d3d9-query] line */
  const uint64_t park_t0 = census::queryParkClock()
      ? (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()
      : 0;
  bool ready = m_device->dxmtQueue().WaitCPUFenceBounded(park_seq, kPollParkNanos);
  if (park_t0)
    census::queryParkedNs((uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count() - park_t0);
  if (!ready) {
"""

EDITS = [
    (HPP, [("d3d9_census.hpp: the query hooks", HPP_OLD, HPP_NEW)]),
    (CPP, [("d3d9_census.cpp: the query counters", CPP_VARS_OLD, CPP_VARS_NEW),
           ("d3d9_census.cpp: reportQueries' lines", CPP_REPORT_OLD, CPP_REPORT_NEW),
           ("d3d9_census.cpp: queryCompleted", CPP_FN_OLD, CPP_FN_NEW)]),
    (QRY, [("d3d9_query.cpp: GetData's park", QRY_OLD, QRY_NEW)]),
]


def main():
    texts = {p: p.read_text() for p, _ in EDITS}
    if any(MARKER in t for t in texts.values()):
        if all(MARKER in t for t in texts.values()):
            print("d3d9 query park: already patched")
            return 0
        print("::error::patch-dxmt-d3d9-query-park: only some files carry the marker -- dxmt moved, review this patch")
        return 1
    for path, pairs in EDITS:
        for name, old, _ in pairs:
            if texts[path].count(old) != 1:
                print(f"::error::patch-dxmt-d3d9-query-park: {name}: anchor found {texts[path].count(old)} times (want 1)")
                return 1
    for path, pairs in EDITS:
        for _, old, new in pairs:
            texts[path] = texts[path].replace(old, new, 1)
    for path, _ in EDITS:
        path.write_text(texts[path])
    print("d3d9 query park patched (MADEIRA_FRAME_STATS: parked time and issue-to-complete percentiles on [d3d9-query])")
    return 0


if __name__ == "__main__":
    sys.exit(main())
