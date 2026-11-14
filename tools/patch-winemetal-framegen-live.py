#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, patches 0135 to 0138)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""Frame generation (MetalFX, env.MADEIRA_FRAMEGEN): a live switch, a spare interpolator, a drawable gate, a GPU guard.

winemetal_unix.c (DXMT's native side, compiled into the app): the frame generation of willfaust/dxmt#15 presents a
MetalFX-interpolated frame and then the real one from the game's own present call. This script changes four things.

1. A live switch. madeira_fgl_set(1 / 0 / -1) from the app (the in-game menu) turns generation on or off while the
   game runs; -1 goes back to what the game started with (MADEIRA_FRAMEGEN). Switched off, the interpolator and its
   textures are given back at the next present. madeira_fgl_status() tells the menu what the path is doing.
   Nothing calls these unless the person uses the menu, and without a call the switch reads as before.

2. A spare interpolator (always; the old behaviour was the crash family of round 624/626). When the picture's size or
   format changes, the interpolator and textures of the size before are kept as a spare instead of being released, and
   taken back when that size returns: a game whose picture alternates between two sizes no longer builds an
   interpolator each time. The spare is released after 10 s without use. More than eight builds in 10 s pause
   generation for 10 s ("[framegen] the picture size keeps changing"); the frames present as usual meanwhile.

3. With MADEIRA_PRESENT_FX=1 (env.MADEIRA_PRESENT_FX = 1; off by default), or with their own keys:
   - MADEIRA_FRAMEGEN_GATE=1: the path never waits for its extra drawable. Frames presented and not yet on the screen
     are counted (addPresentedHandler); a frame whose extra drawable is not free goes out as it is, and so does a
     frame of a game the screen cannot show twice per frame (game frame shorter than 1.9 screen periods).
   - MADEIRA_FRAMEGEN_GPU_GUARD=<percent> (1 = 95): while the GPU is busy that share of the time for three seconds,
     generation pauses; it comes back when the GPU has room for it again (the share it used is remembered; after six
     hold times, a minute at least, a GPU that is merely not full is enough for one try), and a pause that follows a
     resume within 30 s lasts twice as long as the one before (10 s to 5 min).
   - MADEIRA_FRAMEGEN_STALL_MS=<ms> (160 with MADEIRA_PRESENT_FX, else 66 as before): a longer frame is a stall and
     drops the history. 66 ms shut games at 10 to 15 FPS out. With MADEIRA_PRESENT_FX the motion search also reaches
     10 blocks instead of 6 when the game frame is longer than 50 ms.
   - a line every two seconds: "[framegen] 2.0 s: game 41 fps, generated 40/s, skipped: ...".
   Without MADEIRA_PRESENT_FX and without those keys none of this runs: the decisions, the waits and the log are the
   old ones.

4. "Measure only" (madeira_fgl_set_measure(1), from the menu): the interpolator runs, nothing extra is shown, so the
   cost of the passes can be told from the cost of showing more frames.

Runs after tools/patch-winemetal-framegen-ios26.py. Idempotent; fails by name if an anchor moved. Run from the
repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: frame generation, live control"

# ---- the GPU meter: a lock-free copy of its busy time, and a second user ----
A_METER_DECL = "static _Atomic int g_madeira_gpu_meter;\n"
METER_DECL = ("static _Atomic uint64_t g_fgl_busy_ns;   /* " + MARKER + ": the meter's busy time, readable without its lock */\n")

A_METER_ENABLE = """void madeira_gpu_meter_enable(int on) {
  atomic_store_explicit(&g_madeira_gpu_meter, on ? 1 : 0, memory_order_relaxed);
}
"""
METER_ENABLE = """void madeira_gpu_meter_enable(int on) {
  /* """ + MARKER + """: bit 0 is the overlay's, bit 1 the frame generation guard's */
  if (on) atomic_fetch_or_explicit(&g_madeira_gpu_meter, 1, memory_order_relaxed);
  else atomic_fetch_and_explicit(&g_madeira_gpu_meter, ~1, memory_order_relaxed);
}
"""

A_METER_PUBLISH = """    if (end > g_gpu_meter_last_end)
      g_gpu_meter_last_end = end;
    os_unfair_lock_unlock(&g_gpu_meter_lock);
"""
METER_PUBLISH = """    if (end > g_gpu_meter_last_end)
      g_gpu_meter_last_end = end;
    atomic_store_explicit(&g_fgl_busy_ns, (uint64_t)(g_gpu_meter_busy_s * 1e9), memory_order_relaxed);   /* """ + MARKER + """ */
    os_unfair_lock_unlock(&g_gpu_meter_lock);
"""

# ---- block A: the switches, the guard, the gate's counters (before madeira_fg_enabled) ----
A_ENABLED = """static int g_fg_on = -1;
static int madeira_fg_enabled(void) {
  if (g_fg_on < 0) {
"""
BLOCK_A = r'''/* madeira-bcd: frame generation, live control (tools/patch-winemetal-framegen-live.py).
 *
 * The switch the game started with (MADEIRA_FRAMEGEN) stays what it was. Added:
 *  - madeira_fgl_set(): the app's in-game menu turns frame generation on and off while the game runs;
 *  - the interpolator and textures of the size before are kept as a spare (a picture that alternates between two
 *    sizes built an interpolator each time; rounds 624/626);
 *  - with MADEIRA_PRESENT_FX=1 (off by default) or their own keys: the path never waits for its extra drawable
 *    (MADEIRA_FRAMEGEN_GATE), pauses while the GPU is full (MADEIRA_FRAMEGEN_GPU_GUARD, a percentage) and takes only
 *    160 ms, not 66 ms, for a stall (MADEIRA_FRAMEGEN_STALL_MS).
 * The drawable gate, the screen-rate gate and the slow-game limits follow Porthole's patches 0135 to 0138
 * (https://github.com/tylerkaska112/Porthole, GPL-3.0-or-later, copyright 2026 The Porthole authors), modified for
 * BCD in November 2026. */
static int g_fg_on;                           /* defined below, as before: what MADEIRA_FRAMEGEN said when a present first asked */

static _Atomic int g_fgl_want = -1;           /* the app's switch: -1 = as the game started, 0 = off, 1 = on */
static _Atomic int g_fgl_measure;             /* 1 = run the interpolator, show nothing extra */
static _Atomic int g_fgl_guard_set = -1;      /* the app's GPU guard percentage; -1 = the key or the default */
static _Atomic int g_fgl_inflight;            /* frames presented by this path and not on the screen yet */
static _Atomic int g_fgl_track_gen;           /* raised when the count above is started again */
static _Atomic int g_fgl_ran;                 /* the path may hold an interpolator and textures */
static _Atomic int g_fgl_revive;              /* switched on from the menu: a path that gave up tries once more */

static int madeira_fgl_env_int(const char *name, int dflt) {
  const char *e = getenv(name);
  return (e && *e) ? atoi(e) : dflt;
}
/* MADEIRA_PRESENT_FX=1: the new present path and its defaults. Read when a session's presents first ask; madeira_fgl_session_reset
 * (below) clears the readings when the session ends, so a later game of the same app run reads its own. */
static int g_fgl_env_mode = -1, g_fgl_env_gate = -1, g_fgl_env_guard = -1;
static double g_fgl_env_stall = -1.0;
static int madeira_fgl_mode(void) {
  if (g_fgl_env_mode < 0) g_fgl_env_mode = madeira_fgl_env_int("MADEIRA_PRESENT_FX", 0) > 0 ? 1 : 0;
  return g_fgl_env_mode;
}
static int madeira_fgl_gate_on(void) {
  if (g_fgl_env_gate < 0) g_fgl_env_gate = madeira_fgl_env_int("MADEIRA_FRAMEGEN_GATE", madeira_fgl_mode()) > 0 ? 1 : 0;
  return g_fgl_env_gate;
}
/* fgl-pure-begin: plain C, no Metal (tests/host/check-framegen-live.py compiles these on the host) */
/* The GPU guard's limit from its key: 0 = off, 1 = the default (95), otherwise 50 to 100. */
static int madeira_fgl_guard_clamp(int v) {
  return v <= 0 ? 0 : v == 1 ? 95 : v < 50 ? 50 : v > 100 ? 100 : v;
}
/* What a frame longer than this is: a stall (the history is dropped). 34 to 500 ms. */
static double madeira_fgl_stall_clamp(int ms, int mode) {
  if (ms <= 0) return mode ? 0.16 : 1.0 / 15.0;
  if (ms < 34) ms = 34;
  if (ms > 500) ms = 500;
  return ms / 1000.0;
}
/* May this frame get a generated twin? inflight: frames presented and not on the screen yet; maxd: the layer's
 * drawables; need: how many the path must be able to hold besides those (3 = the one on the screen, the game's own
 * and the extra one; 2 when the game's own is taken later); dt: the game's frame time; hz: what the screen is held at
 * (0 = not known). 1 = yes, 0 = the screen cannot show two per game frame, -1 = no drawable free. */
static int madeira_fgl_gate_decide(int inflight, int maxd, int need, double dt, double hz) {
  if (hz > 0 && dt > 0 && dt < 1.9 / hz) return 0;
  if (maxd < 2) maxd = 2;
  if (inflight < 0) inflight = 0;
  if (inflight + need > maxd) return -1;
  return 1;
}
struct madeira_fgl_guard {
  uint64_t win_ns, win_busy_ns;        /* where the running one-second window began */
  unsigned long long win_gen;
  int started, paused, full_n, calm_n, first_paused_win;
  double busy, busy_at_pause, cost;    /* the last window's busy share; the share when it paused; what generation took */
  uint64_t paused_ns, resumed_ns, hold_ns;
  unsigned long long pauses;
};
/* One step of the GPU guard. pct: the limit (0 = off); now_ns: a monotonic clock; busy_ns: the GPU's busy time so
 * far; generated: frames generated so far. Returns 1 while generation is paused. Windows are one second long:
 * three in a row at or over the limit with generation running pause it; paused, the first window tells what the
 * generated frames took (at least 5 points), and it comes back after three windows with room for that share and ten
 * points more, but not before the hold time is over (10 s; doubled, up to 5 min, when the pause follows a resume
 * within 30 s). After six hold times (a minute at least) a GPU that is merely not full is enough: one try. */
static int madeira_fgl_guard_step(struct madeira_fgl_guard *g, int pct, uint64_t now_ns, uint64_t busy_ns,
                                  unsigned long long generated) {
  uint64_t el;
  double busy, limit;
  if (pct <= 0) { g->paused = 0; g->started = 0; g->full_n = g->calm_n = 0; return 0; }
  if (!g->started) { g->started = 1; g->win_ns = now_ns; g->win_busy_ns = busy_ns; g->win_gen = generated; return g->paused; }
  el = now_ns - g->win_ns;
  if (el < 1000000000ull) return g->paused;
  busy = busy_ns > g->win_busy_ns ? (double)(busy_ns - g->win_busy_ns) / (double)el : 0.0;
  if (busy > 1.0) busy = 1.0;
  limit = pct >= 100 ? 0.995 : pct / 100.0;
  if (!g->paused) {
    if (generated > g->win_gen && busy >= limit) g->full_n++; else g->full_n = 0;
    if (g->full_n >= 3) {
      g->hold_ns = (g->resumed_ns && now_ns - g->resumed_ns < 30000000000ull && g->hold_ns)
                   ? (g->hold_ns * 2 > 300000000000ull ? 300000000000ull : g->hold_ns * 2) : 10000000000ull;
      g->paused = 1; g->pauses++; g->paused_ns = now_ns; g->busy_at_pause = busy;
      g->cost = 0.05; g->first_paused_win = 1; g->full_n = g->calm_n = 0;
    }
  } else {
    if (g->first_paused_win) {
      g->first_paused_win = 0;
      if (g->busy_at_pause - busy > g->cost) g->cost = g->busy_at_pause - busy;
    }
    /* room for what generation took and ten points more; or, as a probe after six hold times (a minute at least), a
     * GPU that is simply not full: the share measured above also holds whatever else changed in that second */
    if (busy + g->cost + 0.10 <= limit ||
        (busy + 0.15 <= limit && now_ns - g->paused_ns >= (g->hold_ns * 6 > 60000000000ull ? g->hold_ns * 6 : 60000000000ull)))
      g->calm_n++;
    else
      g->calm_n = 0;
    if (g->calm_n >= 3 && now_ns - g->paused_ns >= g->hold_ns) {
      g->paused = 0; g->resumed_ns = now_ns; g->full_n = g->calm_n = 0;
    }
  }
  g->busy = busy; g->win_ns = now_ns; g->win_busy_ns = busy_ns; g->win_gen = generated;
  return g->paused;
}
/* Interpolator builds: 1 = too many (the ninth within 10 s of the first of the last eight): no build now, and none
 * for 10 s. ring: the times of the last eight builds. */
static int madeira_fgl_storm_step(uint64_t ring[8], unsigned *n, uint64_t *until_ns, uint64_t now_ns) {
  if (*until_ns && now_ns < *until_ns) return 1;
  if (*n >= 8 && now_ns - ring[*n % 8] < 10000000000ull) {
    *until_ns = now_ns + 10000000000ull;
    *n = 0;
    return 1;
  }
  ring[*n % 8] = now_ns;
  (*n)++;
  return 0;
}
/* fgl-pure-end */
static int madeira_fgl_guard_percent(void) {
  int live = atomic_load_explicit(&g_fgl_guard_set, memory_order_relaxed);
  if (live >= 0) return live;
  if (g_fgl_env_guard < 0) g_fgl_env_guard = madeira_fgl_guard_clamp(madeira_fgl_env_int("MADEIRA_FRAMEGEN_GPU_GUARD", madeira_fgl_mode() ? 95 : 0));
  return g_fgl_env_guard;
}
static double madeira_fgl_stall_s(void) {
  if (g_fgl_env_stall < 0) g_fgl_env_stall = madeira_fgl_stall_clamp(madeira_fgl_env_int("MADEIRA_FRAMEGEN_STALL_MS", 0), madeira_fgl_mode());
  return g_fgl_env_stall;
}
/* The app, when a session has ended (BCDFrameGenLive.sessionEnded; no present runs then): the keys' readings and the in-game menu's
 * switches go, so the next session of the same app run (MADEIRA_ONE_SESSION_PER_RUN = 0) reads its own game's keys, MADEIRA_FRAMEGEN
 * included, as WineProcessBridge.m's end of a session already unsets them. With one session per app run, the default, it changes
 * nothing: no later present reads them. */
void madeira_fgl_session_reset(void) {
  g_fgl_env_mode = -1; g_fgl_env_gate = -1; g_fgl_env_guard = -1; g_fgl_env_stall = -1.0;
  g_fg_on = -1;
  atomic_store_explicit(&g_fgl_want, -1, memory_order_relaxed);
  atomic_store_explicit(&g_fgl_guard_set, -1, memory_order_relaxed);
  atomic_store_explicit(&g_fgl_measure, 0, memory_order_relaxed);
}
static int madeira_fgl_measuring(void) { return atomic_load_explicit(&g_fgl_measure, memory_order_relaxed); }
/* The two-second line and the gates' own log lines: only where something of this patch is switched on. */
static int madeira_fgl_verbose(void) {
  return madeira_fgl_mode() || madeira_fgl_gate_on() || madeira_fgl_guard_percent() > 0 ||
         atomic_load_explicit(&g_fgl_want, memory_order_relaxed) >= 0;
}

static struct {
  unsigned long long skip_rate, skip_flight, skip_guard, measured, leak_resets;   /* since the last line */
  uint64_t stat_abs, refuse_abs;
  unsigned long long stat_frames, stat_gen0, stat_lines;
  struct madeira_fgl_guard guard;
  uint64_t build_ring[8], storm_until_ns;
  unsigned build_n;
  unsigned long long builds, swaps, storms;
  int reset_history, fbo_asked;
} g_fgl;
/* What the menu reads (madeira_fgl_status): plain values the present thread writes once a second. */
static struct {
  volatile int state, width, height;
  volatile double game_fps, generated_fps, gpu_busy;
} g_fgl_pub;

/* What is in force now (1 or 0). It never makes the present path's own first reading of MADEIRA_FRAMEGEN happen early (the app may ask
 * before a game's settings are exported): until a present has asked, the variable is read as it is now and not kept. */
int madeira_fgl_get(void) {
  int live = atomic_load_explicit(&g_fgl_want, memory_order_relaxed);
  const char *e;
  if (live >= 0) return live;
  if (g_fg_on >= 0) return g_fg_on;
  e = getenv("MADEIRA_FRAMEGEN");
  if (e && *e && *e != '0') {
    if (@available(iOS 26.0, macOS 26.0, *)) return 1;
  }
  return 0;
}
/* The app's switch. on: 1, 0, or -1 for "as the game started". Returns what is in force now (1 or 0). */
int madeira_fgl_set(int on) {
  int was = atomic_load_explicit(&g_fgl_want, memory_order_relaxed);
  if (on > 0) {
    on = 0;
    if (@available(iOS 26.0, macOS 26.0, *)) on = 1;
    if (!on) dprintf(STDERR_FILENO, "[framegen] live switch: MetalFX frame interpolation needs iOS 26 -- stays off\n");
  } else if (on < 0) {
    on = -1;
  }
  atomic_store_explicit(&g_fgl_want, on, memory_order_relaxed);
  if (on == 1) atomic_store_explicit(&g_fgl_revive, 1, memory_order_relaxed);
  if (on != was)
    dprintf(STDERR_FILENO, "[framegen] live switch: %s\n", on == 1 ? "ON" : on == 0 ? "OFF" : "as the game started");
  return madeira_fgl_get();
}
void madeira_fgl_set_measure(int on) {
  if (atomic_exchange_explicit(&g_fgl_measure, on ? 1 : 0, memory_order_relaxed) != (on ? 1 : 0))
    dprintf(STDERR_FILENO, "[framegen] measure only: %s\n", on ? "ON (the passes run, nothing extra is shown)" : "off");
}
/* percent: -1 = the key or the default, 0 = off, 1 = 95, otherwise 50 to 100. */
void madeira_fgl_set_guard(int percent) {
  int v = percent < 0 ? -1 : madeira_fgl_guard_clamp(percent);
  if (atomic_exchange_explicit(&g_fgl_guard_set, v, memory_order_relaxed) != v)
    dprintf(STDERR_FILENO, "[framegen] GPU guard: %d%s\n", v, v < 0 ? " (the key or the default)" : v ? "% busy pauses generation" : " (off)");
}
/* For the in-game menu: 0 off, 1 on and waiting (no generated frame in the last second), 2 generating, 3 paused
 * (the GPU is full), 4 cannot run on this device or picture, 5 measuring only. gpu_busy is -1 without the guard. */
int madeira_fgl_status(int *width, int *height, double *game_fps, double *generated_fps, double *gpu_busy) {
  if (width) *width = g_fgl_pub.width;
  if (height) *height = g_fgl_pub.height;
  if (game_fps) *game_fps = g_fgl_pub.game_fps;
  if (generated_fps) *generated_fps = g_fgl_pub.generated_fps;
  if (gpu_busy) *gpu_busy = g_fgl_pub.gpu_busy;
  if (!madeira_fgl_get()) return 0;
  return g_fgl_pub.state ? g_fgl_pub.state : 1;
}

/* A frame this path lets through is on its way to the screen; on the screen it no longer counts. */
static void madeira_fgl_track(id<MTLDrawable> d) {
  int gen;
  if (!d || !madeira_fgl_gate_on()) return;
  gen = atomic_load_explicit(&g_fgl_track_gen, memory_order_relaxed);
  atomic_fetch_add_explicit(&g_fgl_inflight, 1, memory_order_relaxed);
  [d addPresentedHandler:^(id<MTLDrawable> shown) {
    (void)shown;
    if (atomic_load_explicit(&g_fgl_track_gen, memory_order_relaxed) == gen)
      atomic_fetch_sub_explicit(&g_fgl_inflight, 1, memory_order_relaxed);
  }];
}

'''
ENABLED = BLOCK_A + """static int g_fg_on = -1;
static int madeira_fg_enabled(void) {
  {   /* """ + MARKER + """: the app's switch, when it was used */
    int live = atomic_load_explicit(&g_fgl_want, memory_order_relaxed);
    if (live >= 0) return live;
  }
  if (g_fg_on < 0) {
"""

# ---- block B: the spare set, the build limit, the gate (after g_fg and madeira_fg_release) ----
A_SETUP_HEAD = """/* Pipelines once, textures and the interpolator per drawable size/format. */
static int madeira_fg_setup(id<MTLDevice> dev, id<MTLTexture> src) {
"""
BLOCK_B = r'''/* madeira-bcd: frame generation, live control: the set of the size before, kept as a spare. */
static __typeof__(g_fg) g_fgl_spare;          /* only its textures, interpolator, w, h and fmt are used */
static uint64_t g_fgl_spare_abs;

static void madeira_fgl_spare_release(void) {
  int i;
  for (i = 0; i < 2; i++) {
    [g_fgl_spare.hist[i] release]; g_fgl_spare.hist[i] = nil;
    [g_fgl_spare.luma[i] release]; g_fgl_spare.luma[i] = nil;
  }
  [g_fgl_spare.flow release]; [g_fgl_spare.motion release]; [g_fgl_spare.depth release]; [g_fgl_spare.out release];
  g_fgl_spare.flow = g_fgl_spare.motion = g_fgl_spare.depth = g_fgl_spare.out = nil;
  [g_fgl_spare.interp release]; g_fgl_spare.interp = nil;
  g_fgl_spare.w = g_fgl_spare.h = 0;
}
#define MADEIRA_FGL_MOVE(dst, src, field) do { (dst).field = (src).field; (src).field = nil; } while (0)
/* The active set's objects go to `to` (which must be empty); the active set is empty afterwards. */
static void madeira_fgl_set_move(__typeof__(g_fg) *to, __typeof__(g_fg) *from) {
  int i;
  for (i = 0; i < 2; i++) { MADEIRA_FGL_MOVE(*to, *from, hist[i]); MADEIRA_FGL_MOVE(*to, *from, luma[i]); }
  MADEIRA_FGL_MOVE(*to, *from, flow); MADEIRA_FGL_MOVE(*to, *from, motion);
  MADEIRA_FGL_MOVE(*to, *from, depth); MADEIRA_FGL_MOVE(*to, *from, out);
  MADEIRA_FGL_MOVE(*to, *from, interp);
  to->w = from->w; to->h = from->h; to->fmt = from->fmt;
  from->w = from->h = 0;
}
/* The picture's size or format changed. 1 = the spare set is of the new size and is the active one now (the set of
 * the size before is the spare); 0 = the active set became the spare and the caller builds a new one. */
static int madeira_fgl_swap_spare(NSUInteger w, NSUInteger h, MTLPixelFormat fmt) {
  static __typeof__(g_fg) tmp;
  g_fg.have_prev = 0;
  g_fgl.reset_history = 1;
  g_fgl_spare_abs = mach_absolute_time();
  if (g_fgl_spare.interp && g_fgl_spare.w == w && g_fgl_spare.h == h && g_fgl_spare.fmt == fmt) {
    madeira_fgl_set_move(&tmp, &g_fgl_spare);
    if (g_fg.interp) madeira_fgl_set_move(&g_fgl_spare, &g_fg); else madeira_fg_release();
    madeira_fgl_set_move(&g_fg, &tmp);
    g_fgl.swaps++;
    if (g_fgl.swaps <= 4 || (g_fgl.swaps % 256) == 0)
      dprintf(STDERR_FILENO, "[framegen] picture %lux%lu again: the interpolator kept for it is used (%llu such changes)\n",
              (unsigned long)w, (unsigned long)h, g_fgl.swaps);
    return 1;
  }
  madeira_fgl_spare_release();
  if (g_fg.interp) madeira_fgl_set_move(&g_fgl_spare, &g_fg); else madeira_fg_release();
  return 0;
}
static uint64_t madeira_fgl_ns(uint64_t abs) {
  static mach_timebase_info_data_t tb;
  if (!tb.denom) mach_timebase_info(&tb);
  return abs * tb.numer / tb.denom;
}
/* 1 = interpolators are being built too often: none now. */
static int madeira_fgl_build_storm(void) {
  uint64_t now = madeira_fgl_ns(mach_absolute_time());
  int was = g_fgl.storm_until_ns && now < g_fgl.storm_until_ns;
  if (!madeira_fgl_storm_step(g_fgl.build_ring, &g_fgl.build_n, &g_fgl.storm_until_ns, now)) {
    g_fgl.builds++;
    return 0;
  }
  if (!was) {
    g_fgl.storms++;
    dprintf(STDERR_FILENO, "[framegen] the picture size keeps changing (more than eight interpolators in 10 s): no generated "
                           "frames for 10 s, the game's frames present as usual (%llu such pauses)\n", g_fgl.storms);
  }
  return 1;
}
/* A spare nobody came back to within 10 s is released. */
static void madeira_fgl_spare_expire(uint64_t now_abs) {
  if (g_fgl_spare.interp && madeira_fgl_ns(now_abs - g_fgl_spare_abs) > 10000000000ull) {
    dprintf(STDERR_FILENO, "[framegen] the interpolator kept for %lux%lu was not needed for 10 s: released\n",
            (unsigned long)g_fgl_spare.w, (unsigned long)g_fgl_spare.h);
    madeira_fgl_spare_release();
  }
}
/* May this frame get a generated twin (MADEIRA_FRAMEGEN_GATE)? `need` as in madeira_fgl_gate_decide. */
static int madeira_fgl_may_generate(CAMetalLayer *layer, int need, uint64_t now_abs) {
  double hz;
  int r, maxd;
  if (!madeira_fgl_gate_on()) return 1;
  /* what the screen is held at: the app's intent, or 60 where it holds nothing (iOS parks the panel there) */
  hz = g_madeira_intent_hz > 0 ? (double)g_madeira_intent_hz : g_madeira_panel_hz > 0 ? 60.0 : 0.0;
  maxd = layer ? (int)layer.maximumDrawableCount : 3;
  r = madeira_fgl_gate_decide(atomic_load_explicit(&g_fgl_inflight, memory_order_relaxed), maxd, need, g_fg.dt, hz);
  if (r == 1) { g_fgl.refuse_abs = 0; return 1; }
  if (r == 0) { g_fgl.skip_rate++; g_fgl.refuse_abs = 0; return 0; }
  g_fgl.skip_flight++;
  /* A count that never comes down (a frame whose presented handler did not run) would shut generation out for good:
   * after a second of nothing but refusals it starts again from zero. */
  if (!g_fgl.refuse_abs) g_fgl.refuse_abs = now_abs;
  else if (madeira_fgl_ns(now_abs - g_fgl.refuse_abs) > 1000000000ull) {
    atomic_fetch_add_explicit(&g_fgl_track_gen, 1, memory_order_relaxed);
    atomic_store_explicit(&g_fgl_inflight, 0, memory_order_relaxed);
    g_fgl.refuse_abs = 0;
    g_fgl.leak_resets++;
  }
  return 0;
}

'''

A_SETUP_RELEASE = """  if (g_fg.interp && g_fg.w == src.width && g_fg.h == src.height && g_fg.fmt == src.pixelFormat) return 1;

  madeira_fg_release();
  g_fg.w = src.width; g_fg.h = src.height; g_fg.fmt = src.pixelFormat;
"""
SETUP_RELEASE = """  if (g_fg.interp && g_fg.w == src.width && g_fg.h == src.height && g_fg.fmt == src.pixelFormat) return 1;

  /* """ + MARKER + """: the set of the size before is kept; too many builds pause */
  if (madeira_fgl_swap_spare(src.width, src.height, src.pixelFormat)) return 1;
  if (madeira_fgl_build_storm()) return 0;
  g_fg.w = src.width; g_fg.h = src.height; g_fg.fmt = src.pixelFormat;
"""

# ---- the present function itself becomes madeira_fg_present_inner0 ----
A_INNER = """static int madeira_fg_present_inner(id<MTLCommandBuffer> cb, id<CAMetalDrawable> drawable) {
  static mach_timebase_info_data_t tb;
  id<MTLTexture> src = drawable.texture;
"""
INNER = """static int madeira_fg_present_inner0(id<MTLCommandBuffer> cb, id<CAMetalDrawable> drawable) {   /* """ + MARKER + """: called by madeira_fg_present_inner below */
  static mach_timebase_info_data_t tb;
  id<MTLTexture> src = drawable.texture;
"""

A_STALL = "    if (d > 1.0 / 15.0) { d = 1.0 / 15.0; g_fg.have_prev = 0; }   /* a stall: no stale history */\n"
STALL = ("    { double stall = madeira_fgl_stall_s();   /* " + MARKER + ": 66 ms as before, or MADEIRA_FRAMEGEN_STALL_MS */\n"
         "      if (d > stall) { d = stall; g_fg.have_prev = 0; } }   /* a stall: no stale history */\n")

A_GATE = """  ni = g_fg.cur ^ 1;
  have_prev = g_fg.have_prev;
"""
GATE = """  ni = g_fg.cur ^ 1;
  have_prev = g_fg.have_prev;
  /* """ + MARKER + """: a frame whose extra drawable is not free, or that the screen cannot show twice, goes out as it is */
  int fgl_gen = !have_prev || madeira_fgl_may_generate((CAMetalLayer *)drawable.layer, 3, now);
"""

A_FLOW = """    if (have_prev) {
      int radius = (int)madeira_fg_envf("MADEIRA_FRAMEGEN_RADIUS", 6.0f);
"""
FLOW = """    if (have_prev && fgl_gen) {
      /* """ + MARKER + """: a slow game moves further between two frames (10 blocks under 20 FPS, with MADEIRA_PRESENT_FX) */
      int radius = (int)madeira_fg_envf("MADEIRA_FRAMEGEN_RADIUS", (madeira_fgl_mode() && g_fg.dt > 0.05) ? 10.0f : 6.0f);
"""

A_FIRST = """    g_fg.cur = ni;
    g_fg.have_prev = 1;
    return 0;
  }

  id<CAMetalDrawable> extra = nil;
"""
FIRST = """    g_fg.cur = ni;
    g_fg.have_prev = 1;
    return 0;
  }
  if (!fgl_gen) {   /* """ + MARKER + """: the history moves on, nothing is generated */
    g_fg.cur = ni;
    g_fg.frames++;
    return 0;
  }

  id<CAMetalDrawable> extra = nil;
"""

A_RESET = "    fi.shouldResetHistory = g_fg.generated == 0;\n"
RESET = ("    fi.shouldResetHistory = g_fg.generated == 0 || g_fgl.reset_history;   /* " + MARKER + ": after another size's set */\n"
         "    g_fgl.reset_history = 0;\n")

A_EXTRA = "    extra = [(CAMetalLayer *)drawable.layer nextDrawable];\n"
EXTRA = ("    extra = madeira_fgl_measuring() ? nil : [(CAMetalLayer *)drawable.layer nextDrawable];   /* " + MARKER + ": measure only */\n")

A_NODRAW = """    g_fg.no_drawable++;
    return 0;   /* the caller presents the real frame as usual */
"""
NODRAW = """    if (madeira_fgl_measuring()) g_fgl.measured++; else g_fg.no_drawable++;   /* """ + MARKER + """ */
    return 0;   /* the caller presents the real frame as usual */
"""

A_PAIR = """  [cb presentDrawable:extra];
  [cb presentDrawable:drawable atTime:CACurrentMediaTime() + g_fg.dt * 0.5];
"""
PAIR = """  madeira_fgl_track(extra); madeira_fgl_track(drawable);   /* """ + MARKER + """: both are on their way to the screen */
  [cb presentDrawable:extra];
  [cb presentDrawable:drawable atTime:CACurrentMediaTime() + g_fg.dt * 0.5];
"""

# ---- block C: the function the one-layer code calls (before "One layer at a time") ----
A_ONE_LAYER = "/* One layer at a time: the largest one presenting. Every Wine process presents\n"
BLOCK_C = r'''/* madeira-bcd: frame generation, live control: the GPU guard, the count of frames in flight and the report around
 * the present function above. Called with the frame generation lock held, like that function was. */
static void madeira_fgl_report(uint64_t now) {
  double secs;
  unsigned long long gen;
  int pct = madeira_fgl_guard_percent();
  g_fgl.stat_frames++;
  if (!g_fgl.stat_abs) { g_fgl.stat_abs = now; g_fgl.stat_gen0 = g_fg.generated; g_fgl.stat_frames = 0; return; }
  secs = (double)madeira_fgl_ns(now - g_fgl.stat_abs) / 1e9;
  if (secs < 1.0) return;
  gen = g_fg.generated - g_fgl.stat_gen0;
  g_fgl_pub.width = (int)g_fg.w; g_fgl_pub.height = (int)g_fg.h;
  g_fgl_pub.game_fps = g_fgl.stat_frames / secs;
  g_fgl_pub.generated_fps = gen / secs;
  g_fgl_pub.gpu_busy = pct > 0 ? g_fgl.guard.busy : -1.0;
  g_fgl_pub.state = g_fg.dead ? 4 : g_fgl.guard.paused ? 3 : madeira_fgl_measuring() ? 5 : gen ? 2 : 1;
  if (madeira_fgl_verbose() && (++g_fgl.stat_lines % 2) == 0) {
    dprintf(STDERR_FILENO, "[framegen] madeira-bcd %.1f s: game %.1f fps (frame %.1f ms), generated %.1f/s, skipped: screen too slow %llu, "
            "drawables busy %llu, GPU full %llu, measured only %llu | GPU busy %d%% (guard %d%%%s) | %lux%lu | in flight %d%s\n",
            secs, g_fgl.stat_frames / secs, g_fg.dt * 1000.0, gen / secs, g_fgl.skip_rate, g_fgl.skip_flight, g_fgl.skip_guard,
            g_fgl.measured, pct > 0 ? (int)(g_fgl.guard.busy * 100.0 + 0.5) : -1, pct, g_fgl.guard.paused ? ", PAUSED" : "",
            (unsigned long)g_fg.w, (unsigned long)g_fg.h, atomic_load_explicit(&g_fgl_inflight, memory_order_relaxed),
            g_fgl.leak_resets ? " (the count was started again)" : "");
    g_fgl.skip_rate = g_fgl.skip_flight = g_fgl.skip_guard = g_fgl.measured = g_fgl.leak_resets = 0;
  }
  g_fgl.stat_abs = now; g_fgl.stat_gen0 = g_fg.generated; g_fgl.stat_frames = 0;
}

static int madeira_fgl_guard_paused(uint64_t now) {
  int pct = madeira_fgl_guard_percent(), was = g_fgl.guard.paused, paused;
  if (pct > 0) atomic_fetch_or_explicit(&g_madeira_gpu_meter, 2, memory_order_relaxed);   /* the meter runs for the guard */
  else if (!was && !g_fgl.guard.started) return 0;
  else atomic_fetch_and_explicit(&g_madeira_gpu_meter, ~2, memory_order_relaxed);
  paused = madeira_fgl_guard_step(&g_fgl.guard, pct, madeira_fgl_ns(now),
                                  atomic_load_explicit(&g_fgl_busy_ns, memory_order_relaxed), g_fg.generated);
  if (paused != was) {
    if (paused)
      dprintf(STDERR_FILENO, "[framegen] madeira-bcd GPU guard: the GPU was busy %d%% of the time for 3 s (limit %d%%) -- generation "
                             "paused for at least %.0f s, the game's frames present as usual (pause %llu)\n",
              (int)(g_fgl.guard.busy_at_pause * 100.0 + 0.5), pct, g_fgl.guard.hold_ns / 1e9, g_fgl.guard.pauses);
    else
      dprintf(STDERR_FILENO, "[framegen] madeira-bcd GPU guard: the GPU has room again (busy %d%%, generation took about %d points) -- "
                             "generation resumes\n", (int)(g_fgl.guard.busy * 100.0 + 0.5), (int)(g_fgl.guard.cost * 100.0 + 0.5));
    g_fg.have_prev = 0;
  }
  return paused;
}

static int madeira_fg_present_inner(id<MTLCommandBuffer> cb, id<CAMetalDrawable> drawable) {
  uint64_t now = mach_absolute_time();
  int r = 0;
  atomic_store_explicit(&g_fgl_ran, 1, memory_order_relaxed);
  if (atomic_exchange_explicit(&g_fgl_revive, 0, memory_order_relaxed)) g_fg.dead = 0;
  madeira_fgl_spare_expire(now);
  if (madeira_fgl_guard_paused(now)) {
    g_fgl.skip_guard++;
  } else if (atomic_load_explicit(&g_fgl_want, memory_order_relaxed) == 1 && ((CAMetalLayer *)drawable.layer).framebufferOnly) {
    /* switched on from the menu on a layer whose drawables cannot be read: ask for readable ones (on the main
     * thread, where layer properties are set) and let this frame through */
    if (!(g_fgl.fbo_asked++ % 120)) {
      CAMetalLayer *layer = (CAMetalLayer *)drawable.layer;
      dispatch_async(dispatch_get_main_queue(), ^{ layer.framebufferOnly = NO; });
    }
  } else {
    r = madeira_fg_present_inner0(cb, drawable);
  }
  if (!r) madeira_fgl_track((id<MTLDrawable>)drawable);
  madeira_fgl_report(now);
  return r;
}

'''

# ---- block D: frame generation switched off gives its objects back (before the present thunk) ----
A_THUNK = """static NTSTATUS
_MTLCommandBuffer_presentDrawable(void *obj) {
"""
BLOCK_D = r'''/* madeira-bcd: frame generation, live control: switched off while the game runs, the interpolator, the history
 * and the spare are given back once, by a presenting thread, without waiting for the lock. */
static void madeira_fgl_idle(void) {
  if (!atomic_load_explicit(&g_fgl_ran, memory_order_relaxed) || madeira_fg_enabled()) return;
  if (pthread_mutex_trylock(&g_fg_mu)) return;
  if (atomic_exchange_explicit(&g_fgl_ran, 0, memory_order_relaxed)) {
    madeira_fg_release();
    madeira_fgl_spare_release();
    g_fg.last_abs = 0; g_fg.dt = 0;
    memset(&g_fgl.guard, 0, sizeof g_fgl.guard);
    g_fgl.stat_abs = 0;
    g_fgl_pub.state = 0; g_fgl_pub.generated_fps = 0;
    atomic_fetch_and_explicit(&g_madeira_gpu_meter, ~2, memory_order_relaxed);
    atomic_fetch_add_explicit(&g_fgl_track_gen, 1, memory_order_relaxed);
    atomic_store_explicit(&g_fgl_inflight, 0, memory_order_relaxed);
    dprintf(STDERR_FILENO, "[framegen] off: the interpolator and its pictures are released\n");
  }
  pthread_mutex_unlock(&g_fg_mu);
}

'''

A_HOOK = """  /* Frame generation presents both frames itself (madeira_fg_present). */
  if (mode != 2 && madeira_fg_enabled() &&
"""
HOOK = """  madeira_fgl_idle();   /* """ + MARKER + """: switched off from the menu, its pictures are given back */
  /* Frame generation presents both frames itself (madeira_fg_present). */
  if (mode != 2 && madeira_fg_enabled() &&
"""

PAIRS = (
    ("GPU meter declaration", A_METER_DECL, METER_DECL + A_METER_DECL),
    ("GPU meter switch", A_METER_ENABLE, METER_ENABLE),
    ("GPU meter completion handler", A_METER_PUBLISH, METER_PUBLISH),
    ("madeira_fg_enabled", A_ENABLED, ENABLED),
    ("madeira_fg_setup head", A_SETUP_HEAD, BLOCK_B + A_SETUP_HEAD),
    ("madeira_fg_setup release", A_SETUP_RELEASE, SETUP_RELEASE),
    ("madeira_fg_present_inner head", A_INNER, INNER),
    ("stall limit", A_STALL, STALL),
    ("history index", A_GATE, GATE),
    ("flow search", A_FLOW, FLOW),
    ("first frame", A_FIRST, FIRST),
    ("history reset", A_RESET, RESET),
    ("extra drawable", A_EXTRA, EXTRA),
    ("no drawable", A_NODRAW, NODRAW),
    ("the two presents", A_PAIR, PAIR),
    ("one-layer block", A_ONE_LAYER, BLOCK_C + A_ONE_LAYER),
    ("present thunk", A_THUNK, BLOCK_D + A_THUNK),
    ("present hook", A_HOOK, HOOK),
)


def main():
    if not PATH.exists():
        print(f"::error::{PATH} not found (run from the repository root)")
        return 1
    s = PATH.read_text()
    if MARKER in s:
        print(f"{PATH}: frame generation live control already patched")
        return 0
    if "madeira_fg_other_layer" not in s:
        # An older dxmt checkout: the frame generation tools/patch-dxmt-framegen.py writes, before the fork's one-layer change
        # (dxmt 1f617cd), which everything here builds on. Such a tree is left as it is. A build cannot go out without the
        # live control by mistake: the app calls madeira_fgl_set and does not link against a winemetal without it, and
        # tests/host/check-framegen-live.py fails on the pin when the script does not apply.
        print(f"{PATH}: the frame generation in this tree is from before the one-layer change (dxmt 1f617cd): live control needs it and is left out")
        return 0
    for name, anchor, _ in PAIRS:
        if s.count(anchor) != 1:
            print(f"::error::{PATH}: {name} anchor found {s.count(anchor)} times, not once -- dxmt moved, review tools/patch-winemetal-framegen-live.py")
            return 1
    for _, anchor, new in PAIRS:
        s = s.replace(anchor, new, 1)
    PATH.write_text(s)
    print(f"{PATH}: frame generation live control patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
