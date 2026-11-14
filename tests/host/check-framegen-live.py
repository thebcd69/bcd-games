#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Frame generation live control (tools/patch-winemetal-framegen-live.py); no Metal, no Wine, no device.

1. The script applies after the workflow's DXMT chain on a copy of dxmt/src, is idempotent, and changes
   winemetal_unix.c by exactly its own hunks (undoing each hunk gives the file before back, byte for byte). It is run
   on a copy, so the check passes on an unpatched submodule and on a tree a build already patched.
2. Off path: with no key and no call from the app every hunk reduces to the old code. Checked on the source (each
   decision sits behind madeira_fgl_gate_on / madeira_fgl_guard_percent / madeira_fgl_mode / the app's switch) and on
   the host, where the pure functions are compiled and run: the stall limit without keys is exactly 1.0 / 15.0, the
   gate without its key is never asked, the guard with 0 % never pauses.
3. Behaviour, on the host: the drawable and screen-rate gate, the GPU guard (pause after three full seconds, resume
   only with room for what generation took, the doubled hold after a quick relapse), the build limit, and the spare
   set (a picture alternating between two sizes builds two interpolators in all and releases none; a third size
   releases the oldest; a spare is released after 10 s; nothing leaks).
4. The patched file compiles for iOS (arm64, the flags of build/dxmt-ios/build.sh) when xcrun and the SDK are there.
5. The one-layer check's invariants still hold on the patched file (no blocking lock on g_fg_mu, the caller untouched).
6. The app side: the bridging header declares the functions, the in-game menu uses them, the workflow runs the script.
SKIP (exit 0) without the dxmt sources.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

R = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import winemetal_fx_kit as kit   # noqa: E402

if not kit.have_sources():
    print(f"SKIP: no dxmt sources at {kit.SRC} (check out the submodule or set DXMT_SRC_DIR)")
    sys.exit(0)
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)
    return cond


SCRIPT = "tools/patch-winemetal-framegen-live.py"
wf = (R / ".github/workflows/build-ipa.yml").read_text()
chain = kit.chain()
check("the workflow runs the script, after the iOS 26 frame generation patch",
      SCRIPT in chain and "tools/patch-winemetal-framegen-ios26.py" in chain
      and chain.index(SCRIPT) > chain.index("tools/patch-winemetal-framegen-ios26.py"))
check("the workflow runs this check", "tests/host/check-framegen-live.py" in wf)
if SCRIPT not in chain:
    print("FAILED: frame generation live control")
    sys.exit(1)

stage = kit.Stage(SCRIPT)
if True:
    t = stage.dir
    wm = stage.file
    body = kit.body
    for problem in stage.problems:
        print("  " + problem)
    check("the DXMT patch chain before the script applies on the copy (or the copy came patched and the later scripts' hunks undo cleanly)",
          not stage.problems)
    check("the script applies (or finds its own work in a tree a build already patched)",
          stage.first.returncode == 0 and stage.mod.MARKER in stage.after)
    check("a second run changes nothing", stage.idempotent and "already patched" in stage.second.stdout)
    if stage.first.returncode:
        print(stage.first.stdout + stage.first.stderr)
        print("FAILED: frame generation live control")
        sys.exit(1)
    if stage.before is not None:
        rc, said, untouched = kit.older_tree(SCRIPT, stage.before)
        check("an older dxmt checkout (frame generation from before the one-layer change) is left as it is, with a line that says so, and is no error",
              rc == 0 and untouched and "is left out" in said and "::error::" not in said)
    # ---- 1. exactly its own hunks
    check("the script changes the file by its own hunks and nothing else (undoing them gives the file before back, byte for byte)", stage.exact())
    src = stage.after

    # ---- 2. off path, on the source
    enabled = body(src, "static int madeira_fg_enabled(void) {")
    check("madeira_fg_enabled: only a switch the app set (0 or 1) answers before the old code; -1 falls through to it",
          "int live = atomic_load_explicit(&g_fgl_want, memory_order_relaxed);\n    if (live >= 0) return live;" in enabled
          and "static _Atomic int g_fgl_want = -1;" in src
          and enabled.index("if (live >= 0) return live;") < enabled.index('getenv("MADEIRA_FRAMEGEN")'))
    reset = body(src, "void madeira_fgl_session_reset(void) {")
    check("nothing in the patch stores the app's switch but madeira_fgl_set, and the end of a session, back to \"as the game started\"",
          src.count("atomic_store_explicit(&g_fgl_want") == 2 and "int madeira_fgl_set(int on) {" in src
          and "atomic_store_explicit(&g_fgl_want, -1, memory_order_relaxed);" in reset)
    # The present-fx keys per session (the port's known gap): their readings, MADEIRA_FRAMEGEN's and the menu's switches go when a session
    # ends (the app calls it from BCDFrameGenLive.sessionEnded; WineProcessBridge.m unsets the keys), so a later game of the same app run
    # (MADEIRA_ONE_SESSION_PER_RUN = 0) reads its own. With one session per app run, the default, nothing reads them afterwards.
    app_dir = R / "app/Madeira"
    check("a session's end clears the keys' readings, MADEIRA_FRAMEGEN's and the menu's switches; the app calls it and unsets the keys",
          "g_fgl_env_mode = -1; g_fgl_env_gate = -1; g_fgl_env_guard = -1; g_fgl_env_stall = -1.0;" in reset and "g_fg_on = -1;" in reset
          and "atomic_store_explicit(&g_fgl_guard_set, -1, memory_order_relaxed);" in reset
          and "atomic_store_explicit(&g_fgl_measure, 0, memory_order_relaxed);" in reset
          and "static int on = -1;" not in body(src, "static int madeira_fgl_mode(void) {")
          and "madeira_fgl_session_reset()" in (app_dir / "BCDFrameGenLive.swift").read_text()
          and "void madeira_fgl_session_reset(void);" in (app_dir / "Madeira-Bridging-Header.h").read_text()
          and all(f'unsetenv("{k}")' in (app_dir / "WineProcessBridge.m").read_text()
                  for k in ("MADEIRA_PRESENT_FX", "MADEIRA_FRAMEGEN_GATE", "MADEIRA_FRAMEGEN_GPU_GUARD", "MADEIRA_FRAMEGEN_STALL_MS")))
    check("MADEIRA_PRESENT_FX, the gate and the guard are read from their keys, off without them",
          'madeira_fgl_env_int("MADEIRA_PRESENT_FX", 0) > 0 ? 1 : 0' in src
          and 'madeira_fgl_env_int("MADEIRA_FRAMEGEN_GATE", madeira_fgl_mode()) > 0 ? 1 : 0' in src
          and 'madeira_fgl_env_int("MADEIRA_FRAMEGEN_GPU_GUARD", madeira_fgl_mode() ? 95 : 0)' in src
          and 'madeira_fgl_env_int("MADEIRA_FRAMEGEN_STALL_MS", 0), madeira_fgl_mode())' in src)
    gate = body(src, "static int madeira_fgl_may_generate(")
    check("the gate answers yes at once without its key",
          gate.index("if (!madeira_fgl_gate_on()) return 1;") < gate.index("madeira_fgl_gate_decide("))
    track = body(src, "static void madeira_fgl_track(")
    check("no presented handler is attached without the gate's key",
          track.index("if (!d || !madeira_fgl_gate_on()) return;") < track.index("addPresentedHandler"))
    guard = body(src, "static int madeira_fgl_guard_paused(")
    check("the guard touches the GPU meter and its state only with a limit above 0",
          "if (pct > 0) atomic_fetch_or_explicit(&g_madeira_gpu_meter, 2, memory_order_relaxed);" in guard
          and "else if (!was && !g_fgl.guard.started) return 0;" in guard)
    inner = body(src, "static int madeira_fg_present_inner(id<MTLCommandBuffer> cb")
    check("the wrapper calls the old function once unless the guard paused or the app's switch waits for a readable layer",
          inner.count("madeira_fg_present_inner0(cb, drawable)") == 1
          and "atomic_load_explicit(&g_fgl_want, memory_order_relaxed) == 1 && ((CAMetalLayer *)drawable.layer).framebufferOnly" in inner)
    report = body(src, "static void madeira_fgl_report(")
    check("the two-second line is written only where something of the patch is on",
          "if (madeira_fgl_verbose() && (++g_fgl.stat_lines % 2) == 0) {" in report and report.count("dprintf(") == 1)
    inner0 = body(src, "static int madeira_fg_present_inner0(")
    check("the old present function: stall limit, radius, measure and the pair of presents keep their old form without keys",
          "double stall = madeira_fgl_stall_s();" in inner0 and "if (d > stall) { d = stall; g_fg.have_prev = 0; }" in inner0
          and "(madeira_fgl_mode() && g_fg.dt > 0.05) ? 10.0f : 6.0f" in inner0
          and "extra = madeira_fgl_measuring() ? nil : [(CAMetalLayer *)drawable.layer nextDrawable];" in inner0
          and "[cb presentDrawable:extra];\n  [cb presentDrawable:drawable atTime:CACurrentMediaTime() + g_fg.dt * 0.5];" in inner0
          and "int fgl_gen = !have_prev || madeira_fgl_may_generate((CAMetalLayer *)drawable.layer, 3, now);" in inner0)
    idle = body(src, "static void madeira_fgl_idle(void) {")
    check("switched off: the release runs only after the path ran, only while off, and never waits for the lock",
          idle.index("if (!atomic_load_explicit(&g_fgl_ran, memory_order_relaxed) || madeira_fg_enabled()) return;")
          < idle.index("if (pthread_mutex_trylock(&g_fg_mu)) return;") < idle.index("madeira_fg_release();")
          and idle.count("pthread_mutex_unlock(&g_fg_mu);") == 1)
    hook = body(src, "_MTLCommandBuffer_presentDrawable(void *obj) {")
    check("the present call: one added line before the old frame generation test, which is as it was",
          hook.count("madeira_fgl") == 1 and "  madeira_fgl_idle();" in hook
          and "mode != 2 && madeira_fg_enabled() &&" in hook
          and "madeira_fg_present((id<MTLCommandBuffer>)params->handle, (id<CAMetalDrawable>)params->arg)) {\n"
              "    madeira_log_present_cadence(\"presentFramegen\", 0.0);\n    return STATUS_SUCCESS;" in hook)
    meter = body(src, "void madeira_gpu_meter_enable(int on) {")
    check("the overlay's GPU meter switch sets and clears its own bit only",
          "atomic_fetch_or_explicit(&g_madeira_gpu_meter, 1, memory_order_relaxed);" in meter
          and "atomic_fetch_and_explicit(&g_madeira_gpu_meter, ~1, memory_order_relaxed);" in meter)
    setup = body(src, "static int madeira_fg_setup(id<MTLDevice> dev, id<MTLTexture> src) {")
    check("a new size: the kept set first, then the build limit, then the build (no release of the set before)",
          "return 1;\n\n  /* madeira-bcd: frame generation, live control: the set of the size before is kept; too many builds pause */\n"
          "  if (madeira_fgl_swap_spare(src.width, src.height, src.pixelFormat)) return 1;\n"
          "  if (madeira_fgl_build_storm()) return 0;\n  g_fg.w = src.width;" in setup
          and "\n  madeira_fg_release();\n  g_fg.w = src.width;" not in setup)

    # ---- 5. the one-layer invariants
    outer = body(src, "static int madeira_fg_present(id<MTLCommandBuffer> cb")
    check("the lock is still only ever taken with trylock", "pthread_mutex_lock(&g_fg_mu)" not in src)
    check("the one-layer function is untouched (it calls madeira_fg_present_inner under its lock)",
          "madeira_fgl" not in outer and "@autoreleasepool { r = madeira_fg_present_inner(cb, drawable); }" in outer)

    # ---- 2 and 3. on the host
    cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    if not cc:
        print("SKIP: no C compiler for the behaviour part")
    else:
        pure = src[src.index("/* fgl-pure-begin"):src.index("/* fgl-pure-end */")]
        g_fg = src[src.index("static struct {\n  NSUInteger w, h;"):src.index("} g_fg;") + len("} g_fg;")]
        release = body(src, "static void madeira_fg_release(void) {")
        blockb = src[src.index("/* madeira-bcd: frame generation, live control: the set of the size before, kept as a spare. */"):
                     src.index("/* May this frame get a generated twin (MADEIRA_FRAMEGEN_GATE)?")]
        g_fgl = src[src.index("static struct {\n  unsigned long long skip_rate"):src.index("} g_fgl;") + len("} g_fgl;")]

        def fake_objc(code):
            return re.sub(r"\[([\w.]+(?:\[\w+\])?) release\]", r"fake_release(\1)", code)

        harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
typedef void *id;
#define nil ((void *)0)
#define id_tex id
typedef unsigned long NSUInteger;
typedef unsigned long MTLPixelFormat;
typedef struct { uint32_t numer, denom; } mach_timebase_info_data_t;
static uint64_t g_clock = 1000;
static uint64_t mach_absolute_time(void) { return g_clock; }
static int mach_timebase_info(mach_timebase_info_data_t *t) { t->numer = 1; t->denom = 1; return 0; }
#define STDERR_FILENO 2
static char g_log[65536];
static int fake_dprintf(int fd, const char *fmt, ...) {
  va_list ap; size_t n = strlen(g_log); (void)fd;
  va_start(ap, fmt); vsnprintf(g_log + n, sizeof g_log - n, fmt, ap); va_end(ap);
  return 0;
}
#define dprintf fake_dprintf
static int g_live, g_released;
static void fake_release(id o) { if (o) { g_live--; g_released++; } }
static id fake_new(void) { g_live++; return malloc(1); }
static int fails;
static void expect(int c, const char *msg) { printf("%s %s\n", c ? "ok  " : "FAIL", msg); if (!c) fails++; }
''' + pure + "\n" + re.sub(r"id<\w+>", "id", g_fg).replace("id interp;", "id interp;") + "\n" + g_fgl + "\n" \
            + fake_objc(re.sub(r"id<\w+>", "id", release)) + "\n" + fake_objc(blockb) + r'''
static unsigned long long g_built;
static int fake_setup(NSUInteger w, NSUInteger h, MTLPixelFormat fmt) {
  int i;
  if (g_fg.interp && g_fg.w == w && g_fg.h == h && g_fg.fmt == fmt) return 1;
  if (madeira_fgl_swap_spare(w, h, fmt)) return 1;
  if (madeira_fgl_build_storm()) return 0;
  g_fg.w = w; g_fg.h = h; g_fg.fmt = fmt;
  g_fg.interp = fake_new();
  for (i = 0; i < 2; i++) { g_fg.hist[i] = fake_new(); g_fg.luma[i] = fake_new(); }
  g_fg.flow = fake_new(); g_fg.motion = fake_new(); g_fg.depth = fake_new(); g_fg.out = fake_new();
  g_built++;
  return 1;
}
#define SEC 1000000000ull
int main(void) {
  /* ---- off path */
  expect(madeira_fgl_stall_clamp(0, 0) == 1.0 / 15.0, "the stall limit without keys is exactly 1.0 / 15.0 (the old constant)");
  expect(madeira_fgl_stall_clamp(0, 1) == 0.16 && madeira_fgl_stall_clamp(120, 0) == 0.12 && madeira_fgl_stall_clamp(5, 0) == 0.034
         && madeira_fgl_stall_clamp(9000, 1) == 0.5, "the stall key: 160 ms with MADEIRA_PRESENT_FX, a value as given, 34 to 500 ms");
  expect(madeira_fgl_guard_clamp(0) == 0 && madeira_fgl_guard_clamp(-3) == 0 && madeira_fgl_guard_clamp(1) == 95
         && madeira_fgl_guard_clamp(20) == 50 && madeira_fgl_guard_clamp(90) == 90 && madeira_fgl_guard_clamp(400) == 100,
         "the guard key: 0 off, 1 = 95, otherwise 50 to 100");
  {
    struct madeira_fgl_guard g; int i, paused = 0; uint64_t t = 5 * SEC, busy = 0;
    memset(&g, 0, sizeof g);
    for (i = 0; i < 600; i++) { t += SEC / 10; busy += SEC / 10; paused |= madeira_fgl_guard_step(&g, 0, t, busy, (unsigned long long)i); }
    expect(!paused && g.pauses == 0 && !g.started, "the guard with 0 % never pauses, even with the GPU always busy");
  }
  /* ---- the gate */
  expect(madeira_fgl_gate_decide(0, 3, 3, 0.025, 120.0) == 1, "gate: nothing in flight, 40 fps on a 120 Hz screen: generate");
  expect(madeira_fgl_gate_decide(1, 3, 3, 0.025, 120.0) == -1, "gate: one frame in flight, three drawables, three needed: no drawable free");
  expect(madeira_fgl_gate_decide(1, 3, 2, 0.025, 120.0) == 1 && madeira_fgl_gate_decide(2, 3, 2, 0.025, 120.0) == -1,
         "gate: with two needed one frame in flight still leaves room, two do not");
  expect(madeira_fgl_gate_decide(0, 3, 3, 0.0150, 120.0) == 0 && madeira_fgl_gate_decide(0, 3, 3, 0.0160, 120.0) == 1,
         "gate: a game faster than the screen's rate / 1.9 gets no extra frame (15.0 ms no, 16.0 ms yes at 120 Hz)");
  expect(madeira_fgl_gate_decide(0, 3, 3, 0.030, 60.0) == 0 && madeira_fgl_gate_decide(0, 3, 3, 0.034, 60.0) == 1,
         "gate: on a 60 Hz screen only games under about 31 fps");
  expect(madeira_fgl_gate_decide(0, 3, 3, 0.010, 0.0) == 1, "gate: screen rate not known: no rate test");
  expect(madeira_fgl_gate_decide(-2, 0, 2, 0.03, 120.0) == 1, "gate: a count below zero and a pool below two are taken as 0 and 2");
  /* ---- the GPU guard */
  {
    struct madeira_fgl_guard g; uint64_t t = 10 * SEC, busy = 0; unsigned long long gen = 0; int i, p = 0, resumed_at = -1;
    memset(&g, 0, sizeof g);
    madeira_fgl_guard_step(&g, 95, t, busy, gen);
    for (i = 0; i < 2; i++) { t += SEC; busy += SEC * 97 / 100; gen += 40; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(!p, "guard: two seconds at 97 % do not pause yet");
    t += SEC; busy += SEC * 97 / 100; gen += 40; p = madeira_fgl_guard_step(&g, 95, t, busy, gen);
    expect(p && g.pauses == 1 && g.hold_ns == 10 * SEC, "guard: the third second at 97 % pauses, for at least 10 s");
    /* paused: the GPU falls to 60 % (generation took 37 points); 60 + 37 + 10 > 95: it stays paused however long */
    for (i = 0; i < 40; i++) { t += SEC; busy += SEC * 60 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(p && g.cost > 0.36 && g.cost < 0.38, "guard: paused at 60 % it learns generation took 37 points and stays paused (no room)");
    /* the scene gets lighter: 40 %: 40 + 37 + 10 <= 95 -> after three such seconds it resumes */
    for (i = 0; i < 6 && resumed_at < 0; i++) { t += SEC; busy += SEC * 40 / 100; if (!madeira_fgl_guard_step(&g, 95, t, busy, gen)) resumed_at = i; }
    expect(resumed_at == 2, "guard: with room for that share and ten points more it resumes after three seconds");
    /* relapse within 30 s: the hold doubles */
    for (i = 0; i < 3; i++) { t += SEC; busy += SEC * 99 / 100; gen += 40; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(p && g.pauses == 2 && g.hold_ns == 20 * SEC, "guard: a pause within 30 s of a resume holds twice as long (20 s)");
    t += SEC; busy += SEC * 55 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen);   /* generation took 44 points */
    for (i = 0; i < 10; i++) { t += SEC; busy += SEC * 30 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(p && g.calm_n >= 3, "guard: calm seconds do not end the pause before its hold time");
    for (i = 0; i < 10; i++) { t += SEC; busy += SEC * 30 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(!p, "guard: after the hold time the calm GPU brings generation back");
    /* the probe: the scene changed in the very second generation paused (99 % -> 10 %), so the share measured is 89 points
     * and the strict rule can never pass; a GPU that is not full gets one try after six hold times, a minute at least */
    memset(&g, 0, sizeof g); t += 500 * SEC; madeira_fgl_guard_step(&g, 95, t, busy, gen);
    for (i = 0; i < 3; i++) { t += SEC; busy += SEC * 99 / 100; gen += 40; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    for (i = 0; i < 58; i++) { t += SEC; busy += SEC * 10 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(p && g.cost > 0.88 && g.hold_ns == 10 * SEC, "guard: a share measured too high keeps it paused for now (58 s)");
    for (i = 0, resumed_at = -1; i < 10 && resumed_at < 0; i++) { t += SEC; busy += SEC * 10 / 100; if (!madeira_fgl_guard_step(&g, 95, t, busy, gen)) resumed_at = i; }
    expect(resumed_at == 3, "guard: after a minute a GPU that is not full gets one try (62 s after the pause)");
    for (i = 0; i < 3; i++) { t += SEC; busy += SEC * 99 / 100; gen += 40; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    for (i = 0; i < 118; i++) { t += SEC; busy += SEC * 60 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(p && g.hold_ns == 20 * SEC, "guard: a failed try doubles the hold, and the next try waits six times that (still paused 118 s later)");
    for (i = 0; i < 5; i++) { t += SEC; busy += SEC * 60 / 100; p = madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(!p, "guard: the next try comes after 120 s");
    memset(&g, 0, sizeof g); t += 500 * SEC; madeira_fgl_guard_step(&g, 95, t, busy, gen);
    for (i = 0; i < 3; i++) { t += SEC; busy += SEC; gen += 40; madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    for (i = 0, p = 1; i < 2000; i++) { t += SEC; busy += SEC * 90 / 100; p &= madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(p, "guard: a GPU that stays 90 % busy without generation is never tried again");
    /* full GPU without any generated frame (gated game) never pauses */
    memset(&g, 0, sizeof g); t += 100 * SEC; madeira_fgl_guard_step(&g, 95, t, busy, gen);
    for (i = 0, p = 0; i < 10; i++) { t += SEC; busy += SEC; p |= madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(!p, "guard: a full GPU with no generated frame in the window is not generation's doing: no pause");
    /* the hold never exceeds five minutes */
    memset(&g, 0, sizeof g); g.hold_ns = 200 * SEC; g.resumed_ns = t; g.started = 1; g.win_ns = t; g.win_busy_ns = busy; g.win_gen = gen;
    for (i = 0; i < 3; i++) { t += SEC; busy += SEC; gen += 30; madeira_fgl_guard_step(&g, 95, t, busy, gen); }
    expect(g.paused && g.hold_ns == 300 * SEC, "guard: the hold is capped at five minutes");
    memset(&g, 0, sizeof g); madeira_fgl_guard_step(&g, 100, t, busy, gen);
    for (i = 0, p = 0; i < 5; i++) { t += SEC; busy += SEC * 99 / 100; gen += 30; p |= madeira_fgl_guard_step(&g, 100, t, busy, gen); }
    expect(!p, "guard: a limit of 100 means really full (99 % does not pause)");
  }
  /* ---- the build limit */
  {
    uint64_t ring[8] = {0}, until = 0, t = 50 * SEC; unsigned n = 0; int i, storm = 0;
    for (i = 0; i < 8; i++) { t += SEC / 2; storm |= madeira_fgl_storm_step(ring, &n, &until, t); }
    expect(!storm, "build limit: eight builds in four seconds pass");
    t += SEC / 2;
    expect(madeira_fgl_storm_step(ring, &n, &until, t) == 1 && until == t + 10 * SEC, "build limit: the ninth within 10 s is refused, for 10 s");
    expect(madeira_fgl_storm_step(ring, &n, &until, t + 9 * SEC) == 1, "build limit: still refused 9 s later");
    expect(madeira_fgl_storm_step(ring, &n, &until, t + 11 * SEC) == 0, "build limit: allowed again after 10 s");
    n = 0; until = 0;
    for (i = 0, storm = 0; i < 100; i++) { t += 2 * SEC; storm |= madeira_fgl_storm_step(ring, &n, &until, t); }
    expect(!storm, "build limit: a build every two seconds is never refused");
  }
  /* ---- the spare set */
  {
    int i, ok = 1;
    g_clock = 1000 * SEC;
    for (i = 0; i < 400; i++) {   /* GTA V's launcher and game sizes alternating, 20 times a second */
      g_clock += SEC / 20;
      ok &= fake_setup((i & 1) ? 1280 : 2622, (i & 1) ? 600 : 1206, 80);
      madeira_fgl_spare_expire(g_clock);
    }
    expect(ok && g_built == 2 && g_released == 0 && g_fgl.swaps == 398 && g_fgl.storms == 0,
           "spare: a picture alternating between two sizes 400 times builds two interpolators and releases nothing");
    expect(g_fg.w == 1280 && g_fgl_spare.w == 2622 && g_fg.interp && g_fgl_spare.interp && g_live == 18,
           "spare: both sets are alive, nine objects each");
    expect(g_fgl.reset_history == 1 && g_fg.have_prev == 0, "spare: a size change resets the history");
    fake_setup(1920, 1080, 80);
    expect(g_built == 3 && g_released == 9 && g_fgl_spare.w == 1280 && g_fg.w == 1920 && g_live == 18,
           "spare: a third size releases the older spare and keeps the size before");
    fake_setup(1920, 1080, 81);
    expect(g_built == 4 && g_fgl_spare.fmt == 80 && g_fg.fmt == 81, "spare: another pixel format is another set");
    g_clock += 9 * SEC; madeira_fgl_spare_expire(g_clock);
    expect(g_fgl_spare.interp != nil, "spare: kept for 9 s");
    g_clock += 2 * SEC; madeira_fgl_spare_expire(g_clock);
    expect(g_fgl_spare.interp == nil && g_live == 9, "spare: released after 10 s without use");
    madeira_fg_release();
    expect(g_live == 0, "spare: nothing leaks (every object made was released once)");
    /* a real storm: nine different sizes within a second */
    g_built = 0; ok = 1;
    for (i = 0; i < 9; i++) { g_clock += SEC / 10; ok &= fake_setup(100 + i, 100, 80); }
    expect(!ok && g_built == 8 && g_fgl.storms == 1 && strstr(g_log, "the picture size keeps changing") != NULL,
           "spare: the ninth different size within 10 s is not built; one log line says so");
    g_clock += SEC; i = fake_setup(300, 100, 80);
    expect(!i && g_fgl.storms == 1, "spare: during the pause nothing is built and the line is not repeated");
    g_clock += 10 * SEC;
    expect(fake_setup(300, 100, 80) == 1, "spare: 10 s later builds go on");
    madeira_fg_release(); madeira_fgl_spare_release();
    expect(g_live == 0, "spare: nothing leaks after the storm");
  }
  return fails ? 1 : 0;
}
'''
        (t / "h.c").write_text(harness)
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                            "-fsanitize=address,undefined", "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable",
                                "-o", str(t / "h"), str(t / "h.c")], capture_output=True, text=True)
        if check("the pure functions and the spare set compile on the host", b.returncode == 0):
            r = subprocess.run([str(t / "h")], capture_output=True, text=True, timeout=120)
            print(r.stdout.rstrip())
            if r.returncode:
                print(r.stderr[-2000:])
            check("host harness (off path, gate, guard, build limit, spare set)", r.returncode == 0)
        else:
            print(b.stderr[-3000:])

    # ---- 4. compiles for iOS
    ran, ok_c, err = stage.compile_ios()
    if not ran:
        print("SKIP: no iOS SDK (xcrun) or no dxmt/include for the compile part")
    else:
        if not check("the patched winemetal_unix.c compiles for iOS arm64", ok_c):
            print("\n".join(l for l in err.splitlines() if "error" in l)[:3000])
        check("no compiler warning names the new code", not any("fgl" in l for l in err.splitlines() if "warning" in l))
stage.close()

# ---- 6. the app side
hdr = (R / "app/Madeira/Madeira-Bridging-Header.h").read_text()
check("the bridging header declares the live functions",
      all(d in hdr for d in ("int madeira_fgl_set(int on);", "int madeira_fgl_get(void);", "void madeira_fgl_set_measure(int on);",
                             "void madeira_fgl_set_guard(int percent);",
                             "int madeira_fgl_status(int *width, int *height, double *game_fps, double *generated_fps, double *gpu_busy);")))

APP = R / "app/Madeira"
menu = (APP / "BCDMenuMore.swift").read_text()
live = (APP / "BCDFrameGenLive.swift").read_text()
check("the in-game menu: the live rows only in a session with env.MADEIRA_PRESENT_FX; otherwise the section is built as before",
      "if BCDFrameGenLive.live {" in menu and menu.index("if BCDFrameGenLive.live {   // madeira-bcd: a session with env.MADEIRA_PRESENT_FX")
      < menu.index('let wanted = GameProfile(windowsPath: entry.windowsPath).get("env.MADEIRA_FRAMEGEN") == "1"')
      and 'if BCDFrameGenLive.live { BCDFrameGenLive.switched(on: !value.isEmpty) }' in menu
      and 'else { LogStore.shared.log("[feel] frame generation \\(value.isEmpty ? "off" : "on") from the next start") }' in menu)
check("every engine call of live frame generation sits behind `live` (false without the key or below iOS 26)",
      all(f"    static func {fn}" in live and "guard live else { return }" in live.split(f"    static func {fn}")[1].split("\n    }")[0]
          for fn in ("switched(on: Bool)", "setGuard(_ on: Bool, profile: GameProfile)", "setMeasuring(_ on: Bool)"))
      and "nonisolated static var on: Bool { live && madeira_fgl_get() != 0 }" in live)
promo = (APP / "ProMotionIntent.swift").read_text()
check("the screen is held at its top rate only while live frame generation is on",
      "if BCDFrameGenLive.holdsPanel { return panelMaxFPS }" in promo and "nonisolated static var holdsPanel: Bool { on }" in live)
page = (APP / "BCDGamePageLive.swift").read_text()
check("a game's page and Settings write env.MADEIRA_PRESENT_FX; off is no line (or 0 where Settings has it on for every game)",
      'Self.toggle("presentfx", BCDFrameGenMenu.switchTitle' in page
      and 'setLine(game, "env.MADEIRA_PRESENT_FX", on ? "1" : (MadeiraConfig.get("env.MADEIRA_PRESENT_FX") == "1" ? "0" : ""))' in page
      and 'func setPresentFX(_ on: Bool) { write(BCDLiveKey.presentFX, on ? "1" : nil) }' in (APP / "BCDSettingsLive.swift").read_text()
      and 'BCDSettingsID.control(.graphics, "present-fx")' in (APP / "BCDSettingsSections.swift").read_text())
catalog = (APP / "ConfigCatalog.generated.swift").read_text()
check("the catalog has the four keys, each off by default",
      all(f'ConfigOption(key: "{k}"' in catalog and f'key: "{k}", title: ' in catalog for k in
          ("env.MADEIRA_PRESENT_FX", "env.MADEIRA_FRAMEGEN_GATE", "env.MADEIRA_FRAMEGEN_GPU_GUARD", "env.MADEIRA_FRAMEGEN_STALL_MS"))
      and all(re.search(r'key: "%s", title: "[^"]*", kind: \.\w+, defaultValue: "0"' % re.escape(k), catalog) for k in
              ("env.MADEIRA_PRESENT_FX", "env.MADEIRA_FRAMEGEN_GATE", "env.MADEIRA_FRAMEGEN_GPU_GUARD", "env.MADEIRA_FRAMEGEN_STALL_MS")))
pbx = (R / "app/Madeira.xcodeproj/project.pbxproj").read_text()
check("the two Swift files are in the Xcode project; the preview build leaves the engine-bound one out",
      all(pbx.count(f"/* {n} in Sources */") == 2 and pbx.count(f"/* {n} */") >= 2 for n in ("BCDFrameGen.swift", "BCDFrameGenLive.swift"))
      and '"BCDFrameGenLive.swift"' in (R / "tools/ui-preview.py").read_text())
if not shutil.which("xcrun"):
    check("xcrun swiftc is here for the Swift part", "--require-swift" not in sys.argv)
else:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "stubs.swift").write_text("""import Foundation
enum BCDSessionTone: Equatable { case normal, soft, good, warm, hot }
struct BCDChoice: Identifiable, Equatable { var id: String; var title: String }
struct BCDMenuRow: Identifiable, Equatable {
    enum Kind: Equatable { case toggle(Bool); case choice([BCDChoice], selected: String); case note(BCDSessionTone) }
    var id: String; var title: String; var detail: String? = nil; var symbol: String? = nil; var kind: Kind
}
struct BCDMenuSection: Identifiable, Equatable { var id: String; var title: String? = nil; var rows: [BCDMenuRow] }
""")
        shutil.copy(R / "tests/host/bcd_frame_gen_checks.swift", t / "main.swift")
        r = subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-o", str(t / "run"), str(t / "main.swift"), str(t / "stubs.swift"),
                            str(APP / "BCDFrameGen.swift")], capture_output=True, text=True)
        if check("BCDFrameGen.swift and its harness compile on the host", r.returncode == 0):
            r = subprocess.run([str(t / "run")], capture_output=True, text=True)
            for line in r.stdout.splitlines():
                if line.startswith("FAIL"):
                    print(line)
            check(f"the Swift harness: {sum(1 for l in r.stdout.splitlines() if l.startswith('PASS'))} checks, none failing",
                  r.returncode == 0 and "ALL PASS" in r.stdout)
        else:
            print((r.stdout + r.stderr)[-2500:])

print("FAILED: frame generation live control" if failures else "PASS: frame generation live control")
sys.exit(1 if failures else 0)
