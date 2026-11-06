#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Frame generation follows one layer and never waits on its lock (dxmt 1f617cd = willfaust/dxmt#54);
no Metal, no Wine, no device.

Round 624 / 626 logs: 15 sessions with MADEIRA_FRAMEGEN on (GTA V Legacy / Enhanced, Red Dead Redemption 2,
builds 572 to 626) show the interpolator rebuilt 32 to 862 times with two sizes alternating (the game's
window and the Rockstar launcher's), then "Assertion failed: (MAX(srcSize->width >> *srcMip" and
"Command encoder released without endEncoding". Every Wine process presents through the one winemetal call,
so the launcher's frames and the game's shared the single interpolator state from several threads.

The change (winemetal_unix.c): frame generation binds to the largest layer presenting; any other layer
presents as usual; a layer silent for 2 s gives the binding up; the state is behind a mutex taken with
trylock (a busy or dead holder only costs that frame its generated twin);
MADEIRA_FRAMEGEN_ONE_LAYER=0 follows every layer again. Only sessions with MADEIRA_FRAMEGEN=1 reach it.

1. Source checks on the dxmt tree: the caller is untouched (mode != 2 && madeira_fg_enabled() && present),
   no interpolator state is touched before the lock is held, every exit after the lock unlocks, the
   interpolator itself still runs inside the lock only.
2. The function and its helper are cut out of the source and compiled on the host (C++, small fakes for
   Metal / mach time) with a clock the harness drives; scenarios: one layer (every frame reaches the
   interpolator), a small helper layer beside a big game layer (the helper never does, the game always
   does, the interpolator is never rebuilt by the helper), a bigger layer taking over (history reset
   once), a silent bound layer giving the binding up after 2 s (not before), the lock held by another
   thread (the frame returns 0 at once, nothing runs, the busy line comes every 600th), a lock held for
   good by a killed thread, ONE_LAYER=0 (every layer reaches the interpolator).
3. The workflow keeps running the patch chain check.
SKIP (exit 0) without the dxmt submodule.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
wm_path = root / "dxmt/src/winemetal/unix/winemetal_unix.c"
if not wm_path.is_file():
    print(f"SKIP: no dxmt sources at {wm_path} (check out the submodule)")
    sys.exit(0)
src = wm_path.read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


def cut(text, start, end_marker):
    a = text.index(start)
    b = text.index(end_marker, a)
    return text[a:b]


if "madeira_fg_other_layer" not in src:
    check("winemetal_unix.c has the one-layer frame generation (dxmt 1f617cd): pin the dxmt fork at the commit that carries it", False)
    print("FAILED: frame generation one layer")
    sys.exit(1)

# ---- 1. source checks
fn = cut(src, "static int madeira_fg_present(id<MTLCommandBuffer> cb", "\n}\n") + "\n}\n"
helper = cut(src, "static int madeira_fg_other_layer(", "\nstatic int madeira_fg_present(")
trylock = fn.index("pthread_mutex_trylock(&g_fg_mu)")
inner = fn.index("madeira_fg_present_inner(cb, drawable)")
check("no state of the interpolator (g_fg.) is touched before the lock is held except after trylock succeeded",
      "g_fg." not in fn[:trylock] and fn.index("g_fg.have_prev = 0") > trylock)
check("the interpolator runs after the lock is taken and before it is released",
      trylock < inner < fn.index("pthread_mutex_unlock(&g_fg_mu);\n  return r;"))
check("every early return after the lock is held unlocks first",
      fn.count("pthread_mutex_unlock(&g_fg_mu)") == 2 and "pthread_mutex_lock(" not in fn)
check("the lock is taken only with trylock (never blocks)", "pthread_mutex_lock(&g_fg_mu)" not in src)
check("MADEIRA_FRAMEGEN_ONE_LAYER=0 follows every layer again (read once, default on)",
      'getenv("MADEIRA_FRAMEGEN_ONE_LAYER")' in fn and "g_fg_one_layer = !(e && *e == '0');" in fn
      and src.count('"MADEIRA_FRAMEGEN_ONE_LAYER"') == 1)
check("the layer pointer is only compared, never dereferenced",
      "layer->" not in fn and "[layer " not in fn and "(void *)drawable.layer" in fn)
check("the other-layer decision lives in one small function with the 2 s silence limit",
      "2000000000ull" in helper and helper.count("return") == 3)
hook = cut(src, "_MTLCommandBuffer_presentDrawable(void *obj) {", "\n}\n")
check("the caller is as before: presents both frames itself only when the function returned 1",
      "mode != 2 && madeira_fg_enabled() &&" in hook
      and "madeira_fg_present((id<MTLCommandBuffer>)params->handle, (id<CAMetalDrawable>)params->arg)) {\n    madeira_log_present_cadence(\"presentFramegen\", 0.0);\n    return STATUS_SUCCESS;" in hook)

# ---- 2. the host harness
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP: no C++ compiler for the behaviour part")
else:
    start = src.index("/* One layer at a time: the largest one presenting.")
    end_fn = src.index("\n}\n", src.index("static int madeira_fg_present(id<MTLCommandBuffer> cb")) + 3
    code = src[start:end_fn]
    code = code.replace("@autoreleasepool { r = madeira_fg_present_inner(cb, drawable); }",
                        "{ r = madeira_fg_present_inner(cb, drawable); }")
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <stdarg.h>
#include <string>
#include <vector>
typedef struct { uint32_t numer, denom; } mach_timebase_info_data_t;
static uint64_t g_clock_ns = 1000000000ull;
static uint64_t mach_absolute_time(void) { return g_clock_ns; }
static int mach_timebase_info(mach_timebase_info_data_t *t) { t->numer = 1; t->denom = 1; return 0; }
struct FakeTex { uint64_t width, height; bool ok; operator bool() const { return ok; } };
struct FakeDrawable { void *layer; FakeTex texture; };
static std::string g_log;
static int fake_dprintf(int fd, const char *fmt, ...) {
  char b[512]; va_list ap; va_start(ap, fmt); int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
  g_log += b; return n;
}
#define dprintf fake_dprintf
static struct { int have_prev; } g_fg;
struct FakeCb { int unused; };
static int g_inner_calls, g_inner_ret = 1, g_inner_in_lock_violations;
static void *g_inner_layers[100000]; static int g_inner_n;
static int madeira_fg_present_inner(FakeCb *cb, FakeDrawable &drawable) {
  (void)cb;
  g_inner_calls++;
  if (g_inner_n < 100000) g_inner_layers[g_inner_n++] = drawable.layer;
  /* the interpolator must only ever run with the mutex held (trylock on it must fail here) */
  extern pthread_mutex_t g_fg_mu;
  if (pthread_mutex_trylock(&g_fg_mu) == 0) { g_inner_in_lock_violations++; pthread_mutex_unlock(&g_fg_mu); }
  return g_inner_ret;
}
'''
    # the extracted code uses ObjC spellings; map them to the fakes
    code = code.replace("id<MTLCommandBuffer> cb, id<CAMetalDrawable> drawable", "FakeCb *cb, FakeDrawable &drawable")
    code = code.replace("id<MTLTexture> t = drawable.texture;", "FakeTex t = drawable.texture;")
    # static -> extern so the harness can reach the mutex declared above its use
    code = code.replace("static pthread_mutex_t g_fg_mu = PTHREAD_MUTEX_INITIALIZER;", "pthread_mutex_t g_fg_mu = PTHREAD_MUTEX_INITIALIZER;")
    main = r'''
static void expect(bool c, const char *msg) { printf("%s %s\n", c ? "ok  " : "FAIL", msg); if (!c) fails++; }
int main(int argc, char **argv) {
  int phase = argc > 1 ? atoi(argv[1]) : 0;
  if (phase == 0) {
    for (int i = 0; i < 10; i++) { g_clock_ns += 16000000; if (present(&cbx, game) != 1) fails++; }
    expect(g_inner_calls == 10, "one layer: all 10 frames reach the interpolator");
    expect(g_inner_in_lock_violations == 0, "the interpolator ran only with the lock held");
    expect(g_log.find("follows layer") == std::string::npos, "no layer change logged for a single layer");
    g_inner_ret = 0;
    expect(present(&cbx, game) == 0 && g_inner_calls == 11, "an interpolator that declines (0) is passed through");
    g_inner_ret = 1;

    reset();
    int helper_hits = 0;
    for (int i = 0; i < 100; i++) {
      g_clock_ns += 8000000;
      if (present(&cbx, game) != 1) fails++;
      g_clock_ns += 100000;
      helper_hits += present(&cbx, helper);
    }
    int game_calls = 0, helper_calls = 0;
    for (int i = 0; i < g_inner_n; i++) (g_inner_layers[i] == game.layer ? game_calls : helper_calls)++;
    expect(game_calls == 100 && helper_calls == 0 && helper_hits == 0,
           "a small helper layer beside the game: the game reaches the interpolator 100 of 100, the helper 0");
    expect(g_log.find("follows layer") == std::string::npos, "the helper does not move the binding (no log line)");

    reset();
    g_clock_ns += 5000000;
    int r = present(&cbx, big_helper);
    expect(r == 1 && g_inner_calls == 1 && g_inner_layers[0] == big_helper.layer
           && g_log.find("follows layer 0x3000") != std::string::npos && g_fg.have_prev == 0,
           "a bigger layer takes over once: logged, history reset");
    reset(); g_fg.have_prev = 1;
    g_clock_ns += 8000000; present(&cbx, big_helper);
    expect(g_fg.have_prev == 1 && g_log.empty(), "the bound layer keeps its history while it keeps presenting");
    g_clock_ns += 8000000;
    expect(present(&cbx, game) == 0, "the game (smaller now) no longer reaches the interpolator while the bigger layer presents");

    /* silence: the bound layer stops; 1.9 s later the game is still held off, 2.1 s later it takes over */
    reset();
    g_clock_ns += 1900000000ull;
    expect(present(&cbx, game) == 0 && g_inner_calls == 0, "1.9 s after the bound layer's last frame: the binding holds");
    g_clock_ns += 200000000ull;
    expect(present(&cbx, game) == 1 && g_inner_calls == 1 && g_log.find("follows layer 0x1000") != std::string::npos,
           "2.1 s after: the binding is given up and the game takes it");
    expect(g_inner_in_lock_violations == 0, "never ran the interpolator without the lock");

    /* busy lock: another thread holds it */
    reset();
    pthread_mutex_lock(&g_fg_mu);
    int n = 0;
    for (int i = 0; i < 1201; i++) { g_clock_ns += 1000000; n += present(&cbx, game); }
    pthread_mutex_unlock(&g_fg_mu);
    size_t lines = 0, pos = 0;
    while ((pos = g_log.find("busy on another thread", pos)) != std::string::npos) { lines++; pos++; }
    expect(n == 0 && g_inner_calls == 0, "the lock held by another thread: every frame returns 0 at once, nothing runs");
    expect(lines == 3, "the busy line comes every 600th frame (1201 frames: 3 lines)");
    g_clock_ns += 1000000;
    expect(present(&cbx, game) == 1, "the lock released: the next frame reaches the interpolator again");
    return fails ? 1 : 0;
  }
  if (phase == 1) {
    /* a thread killed inside the interpolator leaves the lock held for good: frames still return, never hang */
    pthread_t t; pthread_create(&t, NULL, killed_holder, NULL); pthread_join(t, NULL);
    int n = 0;
    for (int i = 0; i < 50; i++) { g_clock_ns += 1000000; n += present(&cbx, game); }
    expect(n == 0 && g_inner_calls == 0, "a lock left held by a dead thread: 50 frames present as usual, no hang");
    return fails ? 1 : 0;
  }
  if (phase == 2) {
    /* MADEIRA_FRAMEGEN_ONE_LAYER=0: every layer follows (the lock stays) */
    int hits = 0;
    for (int i = 0; i < 20; i++) {
      g_clock_ns += 8000000;
      hits += present(&cbx, game);
      hits += present(&cbx, helper);
      hits += present(&cbx, big_helper);
    }
    expect(hits == 60 && g_inner_calls == 60, "ONE_LAYER=0: all three layers reach the interpolator on every frame");
    expect(g_log.find("follows layer") == std::string::npos, "ONE_LAYER=0: no binding, no layer lines");
    return fails ? 1 : 0;
  }
  return 2;
}
'''
    prelude = r'''
static FakeDrawable game = {(void *)0x1000, {2560, 1200, true}};
static FakeDrawable helper = {(void *)0x2000, {640, 480, true}};
static FakeDrawable big_helper = {(void *)0x3000, {3840, 2160, true}};
static FakeCb cbx;
static int fails = 0;
static void *killed_holder(void *);
static void reset(void) { g_inner_calls = 0; g_inner_n = 0; g_log.clear(); }
static int present(FakeCb *cb, FakeDrawable &d) { return madeira_fg_present(cb, d); }
'''
    full = harness + code + prelude + "static void *killed_holder(void *) { pthread_mutex_lock(&g_fg_mu); return NULL; }\n" + main
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(full)
        b = subprocess.run([cxx, "-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-pthread",
                            "-fsanitize=address,undefined", "-o", str(t / "h"), str(t / "h.cpp")],
                           capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cxx, "-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-pthread",
                                "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        check("the extracted present function compiles on the host", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            for phase, env_extra, what in ((0, {}, "scenarios"), (1, {}, "dead lock holder"),
                                           (2, {"MADEIRA_FRAMEGEN_ONE_LAYER": "0"}, "ONE_LAYER=0")):
                env = dict(os.environ)
                env.pop("MADEIRA_FRAMEGEN_ONE_LAYER", None)
                env.update(env_extra)
                r = subprocess.run([str(t / "h"), str(phase)], capture_output=True, text=True, env=env, timeout=120)
                print(r.stdout.rstrip())
                if r.returncode:
                    print(r.stderr[-1500:])
                check(f"harness phase {phase} ({what})", r.returncode == 0)

# ---- 3. workflow
wf = (root / ".github/workflows/build-ipa.yml").read_text()
check("the patch chain check still runs before the dxmt patches", "run: python3 tests/host/check-dxmt-patch-chain.py" in wf)
check("this check runs in the workflow", "tests/host/check-dxmt-framegen-one-layer.py" in wf)

print("FAILED: frame generation one layer" if failures else "PASS: frame generation one layer")
sys.exit(1 if failures else 0)
