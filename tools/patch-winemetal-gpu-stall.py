#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Just Cause 3's world stall, made readable (round 624, agent D2 S15; Send Logs ZGV6WF, EKWP12, Z0YFZ9).

In ZGV6WF the GPU took 340 ms to 8 s a frame from about 62 s after the first present (25 frames in 9.19 s, GPU busy 92 %, 24
command buffers in flight); the list's MADEIRA_GPU_TRACE = 95,30,2 starts its frame trace 95 s after the first present, and the
testers leave at 75-90 s, so no trace of the slow frames exists. Two switches, both off unless a game's file sets them:

MADEIRA_GPU_TRACE=slow=<ms>[,S[,N]]   The frame trace of tools/patch-winemetal-gpu-trace.py starts on a slow frame instead of a
    clock: after 3 presents in a row more than <ms> apart (one long gap, a loading hitch, is not enough), at most N traces (default
    3), 60 s apart; the value dumps (every S seconds) begin with the first such trace. `[gpu-trace] F<n> frame begin (slow: ...)`.
    The old form T[,S[,N]] is unchanged.

MADEIRA_GPU_STALL_MS=<ms>   (100-60000) A command buffer still unfinished <ms> after its commit is named once per stall:
    `[gpu-stall] madeira-bcd #k: command buffer <n> unfinished <age> ms after its commit (committed, not yet scheduled |
    scheduled: on the GPU or waiting for it); <in flight>, <scheduled>`; when it completes, how long it waited to be scheduled and
    how long the GPU ran it. That separates "the GPU executes it" from "it was never scheduled". A watch thread wakes every
    500 ms; each tracked buffer gets a scheduled and a completed handler (added before its commit, as Metal requires).

Applied after tools/patch-winemetal-gpu-trace.py (it changes that patch's trigger). Native only (the winemetal unix side). The
remote Metal path is untouched. Idempotent (marker "gpu-stall-v1"); exits 1 naming the anchor when one moved, the file unchanged.
Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "gpu-stall-v1"

STATICS_OLD = """static obj_handle_t madeira_gt_present_cb;
static volatile int madeira_gt_tracing;
static unsigned madeira_gt_frame, madeira_gt_lines;
"""
STATICS_NEW = """static obj_handle_t madeira_gt_present_cb;
static volatile int madeira_gt_tracing;
static unsigned madeira_gt_frame, madeira_gt_lines;
/* madeira-bcd gpu-stall-v1 (round 624, Just Cause 3; tools/patch-winemetal-gpu-stall.py): MADEIRA_GPU_TRACE=slow=<ms>,... starts the
 * frame trace after 3 presents in a row more than <ms> apart instead of at a time. */
static double madeira_gt_slow_ms, madeira_gt_last_present;
static int madeira_gt_slow_run;
"""

PARSE_OLD = """    if (e && *e) {
      t = atof(e);
      const char *c = strchr(e, ',');
"""
PARSE_NEW = """    if (e && *e) {
      if (!strncmp(e, "slow=", 5))   /* madeira-bcd gpu-stall-v1: a slow frame starts the trace */
        madeira_gt_slow_ms = atof(e + 5) > 0 ? atof(e + 5) : 0;
      else
        t = atof(e);
      const char *c = strchr(e, ',');
"""

MODE_OLD = """    madeira_gt_mode = t > 0 ? 1 : 0;
    if (madeira_gt_mode)
      fprintf(stderr, "[gpu-trace] madeira-bcd on: frame traces from %.0f s after the first frame (%d, 60 s apart), "
                      "value dumps every %.2f s (MADEIRA_GPU_TRACE)\\n", t, n, s);
"""
MODE_NEW = """    madeira_gt_mode = (t > 0 || madeira_gt_slow_ms > 0) ? 1 : 0;
    if (madeira_gt_mode && madeira_gt_slow_ms > 0)
      fprintf(stderr, "[gpu-trace] madeira-bcd on: frame traces after 3 presents in a row more than %.0f ms apart (%d, 60 s apart), "
                      "value dumps every %.2f s from the first such trace (MADEIRA_GPU_TRACE=slow=...)\\n", madeira_gt_slow_ms, n, s);
    else if (madeira_gt_mode)
      fprintf(stderr, "[gpu-trace] madeira-bcd on: frame traces from %.0f s after the first frame (%d, 60 s apart), "
                      "value dumps every %.2f s (MADEIRA_GPU_TRACE)\\n", t, n, s);
"""

T0_OLD = """  if (madeira_gt_t0 == 0) {
    madeira_gt_t0 = now;
    madeira_gt_next_full = now + madeira_gt_start;
    madeira_gt_next_small = now + madeira_gt_start;
  }
  madeira_gt_frame++;
"""
T0_NEW = """  if (madeira_gt_t0 == 0) {
    madeira_gt_t0 = now;
    madeira_gt_next_full = now + madeira_gt_start;
    /* madeira-bcd gpu-stall-v1: in slow mode the value dumps begin with the first slow trace */
    madeira_gt_next_small = madeira_gt_slow_ms > 0 ? HUGE_VAL : now + madeira_gt_start;
  }
  /* madeira-bcd gpu-stall-v1: the present-to-present gap, and how many such gaps in a row were slow */
  double madeira_gap_ms = madeira_gt_last_present > 0 ? (now - madeira_gt_last_present) * 1000.0 : 0;
  madeira_gt_last_present = now;
  if (madeira_gt_slow_ms > 0)
    madeira_gt_slow_run = madeira_gap_ms > madeira_gt_slow_ms ? madeira_gt_slow_run + 1 : 0;
  madeira_gt_frame++;
"""

FULL_OLD = """  if (madeira_gt_full_left > 0 && now >= madeira_gt_next_full) {
    madeira_gt_full_left--;
    madeira_gt_next_full = now + 60.0;
    madeira_gt_lines = 0;
    fprintf(stderr, "[gpu-trace] F%u frame begin (t=%.1f s)\\n", madeira_gt_frame, now - madeira_gt_t0);
    madeira_gt_tracing = 1;
    return;
  }
"""
FULL_NEW = """  if (madeira_gt_full_left > 0 && now >= madeira_gt_next_full && (madeira_gt_slow_ms <= 0 || madeira_gt_slow_run >= 3)) {
    madeira_gt_full_left--;
    madeira_gt_next_full = now + 60.0;
    madeira_gt_lines = 0;
    if (madeira_gt_slow_ms > 0) {   /* madeira-bcd gpu-stall-v1 */
      fprintf(stderr, "[gpu-trace] F%u frame begin (slow: %d presents in a row more than %.0f ms apart, the last %.0f ms; t=%.1f s)\\n",
              madeira_gt_frame, madeira_gt_slow_run, madeira_gt_slow_ms, madeira_gap_ms, now - madeira_gt_t0);
      if (madeira_gt_next_small == HUGE_VAL) madeira_gt_next_small = now + madeira_gt_every;
    } else
      fprintf(stderr, "[gpu-trace] F%u frame begin (t=%.1f s)\\n", madeira_gt_frame, now - madeira_gt_t0);
    madeira_gt_tracing = 1;
    return;
  }
"""

COMMIT_FN = "static NTSTATUS\n_MTLCommandBuffer_commit(void *obj) {\n"
HELPERS = """/* madeira-bcd gpu-stall-v1 (round 624, agent D2 S15; tools/patch-winemetal-gpu-stall.py): MADEIRA_GPU_STALL_MS=<ms> names a command
 * buffer still unfinished <ms> after its commit, once per stall, and on its completion how long it waited to be scheduled and how long
 * the GPU ran it. Off (nothing tracked, no thread) unless the switch holds a number from 100 to 60000. */
#define MADEIRA_GS_SLOTS 256
static struct { unsigned long long seq; double commit_t, sched_t; int state; } madeira_gs_slot[MADEIRA_GS_SLOTS];
static pthread_mutex_t madeira_gs_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_once_t madeira_gs_once = PTHREAD_ONCE_INIT;
static double madeira_gs_limit;                               /* seconds; 0 = off */
static unsigned long long madeira_gs_seq, madeira_gs_open;    /* open: the buffer named for the stall in progress */
static unsigned madeira_gs_stalls, madeira_gs_untracked;

static double madeira_gs_now(void) {
  return (double)clock_gettime_nsec_np(CLOCK_UPTIME_RAW) / 1e9;
}

/* One look at the tracked buffers: the oldest unfinished one, named once when it passes the limit. */
static void madeira_gs_scan(double now) {
  int oldest = -1, inflight = 0, scheduled = 0;
  pthread_mutex_lock(&madeira_gs_lock);
  for (int i = 0; i < MADEIRA_GS_SLOTS; i++) {
    if (!madeira_gs_slot[i].state) continue;
    inflight++;
    if (madeira_gs_slot[i].state == 2) scheduled++;
    if (oldest < 0 || madeira_gs_slot[i].commit_t < madeira_gs_slot[oldest].commit_t) oldest = i;
  }
  if (oldest >= 0 && !madeira_gs_open && now - madeira_gs_slot[oldest].commit_t > madeira_gs_limit) {
    madeira_gs_open = madeira_gs_slot[oldest].seq;
    madeira_gs_stalls++;
    if (madeira_gs_stalls <= 32 || !(madeira_gs_stalls % 100))
      fprintf(stderr, "[gpu-stall] madeira-bcd #%u: command buffer %llu unfinished %.0f ms after its commit (%s); %d in flight, "
                      "%d of them scheduled, %u not tracked\\n", madeira_gs_stalls, madeira_gs_slot[oldest].seq,
              (now - madeira_gs_slot[oldest].commit_t) * 1000.0,
              madeira_gs_slot[oldest].state == 2 ? "scheduled: on the GPU or waiting for it" : "committed, not yet scheduled",
              inflight, scheduled, madeira_gs_untracked);
  }
  pthread_mutex_unlock(&madeira_gs_lock);
}

static void *madeira_gs_watch(void *arg) {
  (void)arg;
  for (;;) {
    struct timespec half = { 0, 500 * 1000 * 1000 };
    nanosleep(&half, NULL);
    madeira_gs_scan(madeira_gs_now());
  }
  return NULL;
}

static void madeira_gs_init(void) {
  const char *e = getenv("MADEIRA_GPU_STALL_MS");
  double ms = e && *e ? atof(e) : 0;
  pthread_t thread;
  pthread_attr_t attr;
  if (ms < 100 || ms > 60000) return;
  madeira_gs_limit = ms / 1000.0;
  pthread_attr_init(&attr);
  pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
  if (pthread_create(&thread, &attr, madeira_gs_watch, NULL) != 0) madeira_gs_limit = 0;
  pthread_attr_destroy(&attr);
  if (madeira_gs_limit > 0)
    fprintf(stderr, "[gpu-stall] madeira-bcd on: a command buffer unfinished %.0f ms after its commit is named once per stall "
                    "(MADEIRA_GPU_STALL_MS)\\n", ms);
}

static void madeira_gs_track(id<MTLCommandBuffer> cb) {
  int slot = -1;
  unsigned long long seq;
  pthread_once(&madeira_gs_once, madeira_gs_init);
  if (madeira_gs_limit <= 0 || !cb) return;
  pthread_mutex_lock(&madeira_gs_lock);
  for (int i = 0; i < MADEIRA_GS_SLOTS; i++)
    if (!madeira_gs_slot[i].state) { slot = i; break; }
  seq = ++madeira_gs_seq;
  if (slot >= 0) {
    madeira_gs_slot[slot].seq = seq;
    madeira_gs_slot[slot].commit_t = madeira_gs_now();
    madeira_gs_slot[slot].sched_t = 0;
    madeira_gs_slot[slot].state = 1;
  } else
    madeira_gs_untracked++;
  pthread_mutex_unlock(&madeira_gs_lock);
  if (slot < 0) return;
  [cb addScheduledHandler:^(id<MTLCommandBuffer> buffer) {
    (void)buffer;
    pthread_mutex_lock(&madeira_gs_lock);
    if (madeira_gs_slot[slot].seq == seq && madeira_gs_slot[slot].state == 1) {
      madeira_gs_slot[slot].state = 2;
      madeira_gs_slot[slot].sched_t = madeira_gs_now();
    }
    pthread_mutex_unlock(&madeira_gs_lock);
  }];
  [cb addCompletedHandler:^(id<MTLCommandBuffer> buffer) {
    double now = madeira_gs_now(), commit_t = 0, sched_t = 0;
    int named = 0;
    pthread_mutex_lock(&madeira_gs_lock);
    if (madeira_gs_slot[slot].seq == seq && madeira_gs_slot[slot].state) {
      commit_t = madeira_gs_slot[slot].commit_t;
      sched_t = madeira_gs_slot[slot].sched_t;
      madeira_gs_slot[slot].state = 0;
      if (madeira_gs_open == seq) {
        madeira_gs_open = 0;
        named = 1;
      }
    }
    pthread_mutex_unlock(&madeira_gs_lock);
    if (named) {
      double gpu = buffer.GPUEndTime - buffer.GPUStartTime;
      char sched_txt[64], gpu_txt[64];
      if (sched_t > 0)
        snprintf(sched_txt, sizeof sched_txt, "%.0f ms until it was scheduled", (sched_t - commit_t) * 1000.0);
      else
        snprintf(sched_txt, sizeof sched_txt, "no scheduled time");
      if (buffer.GPUStartTime > 0 && gpu > 0)
        snprintf(gpu_txt, sizeof gpu_txt, "the GPU ran it %.0f ms", gpu * 1000.0);
      else
        snprintf(gpu_txt, sizeof gpu_txt, "no GPU time");
      fprintf(stderr, "[gpu-stall] madeira-bcd command buffer %llu done %.0f ms after its commit: %s, %s (status %ld%s)\\n", seq,
              (now - commit_t) * 1000.0, sched_txt, gpu_txt, (long)buffer.status, buffer.error ? ", an error" : "");
    }
  }];
}

"""

COMMIT_OLD = """  [(id<MTLCommandBuffer>)params->handle commit];
  madeira_gt_after_commit(params->handle);   /* madeira-bcd: gpu trace */
"""
COMMIT_NEW = """  madeira_gs_track((id<MTLCommandBuffer>)params->handle);   /* madeira-bcd: gpu stall watch (gpu-stall-v1) */
  [(id<MTLCommandBuffer>)params->handle commit];
  madeira_gt_after_commit(params->handle);   /* madeira-bcd: gpu trace */
"""

PAIRS = [
    ("the trace statics", STATICS_OLD, STATICS_NEW),
    ("the MADEIRA_GPU_TRACE parser", PARSE_OLD, PARSE_NEW),
    ("the trace's on/off line", MODE_OLD, MODE_NEW),
    ("the first present's timers", T0_OLD, T0_NEW),
    ("the full-trace trigger", FULL_OLD, FULL_NEW),
    ("the commit of a command buffer", COMMIT_OLD, COMMIT_NEW),
    ("the commit function", COMMIT_FN, HELPERS + COMMIT_FN),
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: gpu-stall already patched")
        return 0
    for name, old, _ in PAIRS:
        if s.count(old) != 1:
            print(f"::error::patch-winemetal-gpu-stall: {name}: anchor found {s.count(old)} times (want 1) in {PATH}"
                  " (run tools/patch-winemetal-gpu-trace.py first)")
            return 1
    for _, old, new in PAIRS:
        s = s.replace(old, new, 1)
    PATH.write_text(s)
    print("winemetal_unix.c: gpu-stall patched (MADEIRA_GPU_TRACE=slow=..., MADEIRA_GPU_STALL_MS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
