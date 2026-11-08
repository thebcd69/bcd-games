#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""pso-async: render pipelines built on two background threads (madeira-d3d12, opt-in, default off;
upstream Madeira #516); no GPU, no Wine.

A lazy pipeline Metal has not compiled yet takes 35-50 ms at its first draw, on the thread that submits
the frame (Spider-Man Remastered's new districts: ~450 new pipelines per area; the upstream log: 252 builds,
12.3 s of the submitting thread in two minutes). With `pso-async = 1` (bcd.cfg or the game's file; needs
pso-lazy, which is on) the first draw hands the pipeline to two worker threads and the draw is skipped
until the pipeline exists; compute pipelines, tessellation pipelines and a full queue (2048) still build
where they are used. Off (the default) nothing changes: the same calls in the same places.

1. Source: the key is read once, default 0, only with pso-lazy; the three places that build a pipeline
   (mad_pso_realize, mad_prebuild_lists, exec_draw) are unchanged when the key is off (the old line is
   the else branch / the code after a failed queue call); mad_pso_realize publishes rps after a memory
   barrier; the queue holds a reference that the worker drops after the build.
2. The queue (cut out of madeira_d3d12.c, compiled on the host with small fakes for the Win32 calls
   and a fake builder), one process per case: key off or pso-lazy off: off; key on: one pipeline is queued
   once, built once, its reference given back; twenty pipelines from four producer threads are each built
   exactly once; 2060 pipelines against a blocked builder: the queue refuses at its limit without leaking
   a reference or leaving the pipeline marked as queued; a failed build gives its reference back and the
   pipeline is not queued again; no thread available: the caller builds (queue returns 0).
3. The draw gate cut out of exec_draw behaves: key off builds inline and draws; key on skips the draw
   while the pipeline is queued and draws once it is built; a tessellation pipeline is built inline.
4. The workflow runs this check.
SKIP (exit 0) without a C++ compiler.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
wf = (root / ".github/workflows/build-ipa.yml").read_text()
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


def between(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


# ---- 1. source
realize = between(src, "static obj_handle_t mad_pso_realize(struct mad_pso *p) {", "\n}\n")
prebuild = between(src, "static void mad_prebuild_lists(UINT count", "\n}\n")
gate = between(src, "    if (e->pso && !e->pso->rps && e->pso->lazy) {\n        if (!e->pso->tess && mad_pso_async_on()", "    if (!e->pso || (!e->pso->rps && !e->pso->tess)) {")
check("the key is read once, default 0, and only with pso-lazy",
      'mad_pso_lazy_on() && mad_cfg_int_pe("pso-async", 0) > 0' in src and src.count('"pso-async"') == 1 and "static int g_pso_async = -1;" in src)
check("mad_pso_realize builds into a local and publishes rps after a memory barrier",
      "if (built) { MemoryBarrier(); p->rps = built; }" in realize and "p->rps = p->has_vd" not in realize)
check("mad_prebuild_lists hands only render pipelines to the queue, before they join the inline batch",
      "if (!p->is_compute && mad_pso_async_on() && mad_pso_async_queue(p)) continue;" in prebuild
      and prebuild.index("mad_pso_async_queue(p)") < prebuild.index("v[n++] = p"))
check("exec_draw: tessellation pipelines and a refused queue build inline as before (else branch), a queued one is skipped until built",
      "} else mad_pso_realize(e->pso);" in gate and "!e->pso->tess && mad_pso_async_on()" in gate
      and "if (!e->pso->rps) { InterlockedIncrement(&g_pa_skips); MAD_SKIP(e); return; }" in gate)
check("the queue holds a reference that the worker drops after the build",
      "InterlockedIncrement(&p->refs);   /* the worker releases it once the pipeline is built */" in src
      and "pso_Release((ID3D12PipelineState *)p);   /* the queue's reference */" in src)
check("the pipeline carries the queued flag", "volatile LONG async_q;" in src)
check("this check runs in the workflow", "tests/host/check-pso-async.py" in wf)

# ---- 2 + 3. the harness
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx:
    print("SKIP: no C++ compiler for the behaviour part")
else:
    queue = between(src, "#define MAD_PA_MAX 2048", "/* madeira-bcd: PARALLEL FIRST USE.")
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
typedef int32_t LONG; typedef uint32_t ULONG; typedef uint32_t DWORD; typedef uint64_t obj_handle_t;
#define WINAPI
#define STDMETHODCALLTYPE
#define INFINITE 0xFFFFFFFFu
struct ID3D12PipelineState;
struct mad_pso { obj_handle_t rps; int lazy; int tess; volatile LONG async_q; LONG refs; int builds; int fail; };
/* --- Win32 fakes --- */
typedef struct { pthread_mutex_t m; } SRWLOCK;
#define SRWLOCK_INIT { PTHREAD_MUTEX_INITIALIZER }
static void AcquireSRWLockExclusive(SRWLOCK *l) { pthread_mutex_lock(&l->m); }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { pthread_mutex_unlock(&l->m); }
struct Sem { pthread_mutex_t m; pthread_cond_t c; long n; };
typedef Sem *HANDLE;
static HANDLE CreateSemaphoreA(void *, long, long, void *) { Sem *s = new Sem; pthread_mutex_init(&s->m, 0); pthread_cond_init(&s->c, 0); s->n = 0; return s; }
static DWORD WaitForSingleObject(HANDLE h, DWORD) { pthread_mutex_lock(&h->m); while (h->n <= 0) pthread_cond_wait(&h->c, &h->m); h->n--; pthread_mutex_unlock(&h->m); return 0; }
static int ReleaseSemaphore(HANDLE h, long n, long *) { pthread_mutex_lock(&h->m); h->n += n; pthread_cond_broadcast(&h->c); pthread_mutex_unlock(&h->m); return 1; }
static int g_no_threads;
struct ThreadArg { DWORD (*fn)(void *); void *arg; };
static void *thread_tramp(void *a) { ThreadArg *t = (ThreadArg *)a; t->fn(t->arg); return 0; }
static HANDLE CreateThread(void *, size_t, DWORD (*fn)(void *), void *arg, DWORD, DWORD *) {
  if (g_no_threads) return 0;
  pthread_t th; ThreadArg *t = new ThreadArg{fn, arg};
  if (pthread_create(&th, 0, thread_tramp, t)) return 0;
  pthread_detach(th); return (HANDLE)1;
}
static int CloseHandle(HANDLE) { return 1; }
static void Sleep(DWORD ms) { usleep(ms * 1000); }
static LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedDecrement(volatile LONG *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
static LONG InterlockedExchange(volatile LONG *p, LONG v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
static LONG InterlockedCompareExchange(volatile LONG *p, LONG ex, LONG cmp) { __atomic_compare_exchange_n(p, &cmp, ex, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); return cmp; }
static obj_handle_t NSAutoreleasePool_alloc_init() { return 1; }
static void NSObject_release(obj_handle_t) {}
static std::string g_log; static pthread_mutex_t g_log_mu = PTHREAD_MUTEX_INITIALIZER;
static void d3d12_log(const char *fmt, ...) {
  char b[1024]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
  pthread_mutex_lock(&g_log_mu); g_log += b; pthread_mutex_unlock(&g_log_mu);
}
/* --- the runtime's own pieces the queue calls --- */
static int g_cfg_async, g_cfg_lazy = 1;
static int mad_cfg_int_pe(const char *k, int d) { return !strcmp(k, "pso-async") ? g_cfg_async : d; }
static int mad_pso_lazy_on(void) { return g_cfg_lazy; }
static volatile int g_gate_closed;          /* the fake builder waits while closed */
static volatile LONG g_realize_calls;
static obj_handle_t mad_pso_realize(struct mad_pso *p) {
  if (p->rps || !p->lazy) return p->rps;
  InterlockedIncrement(&g_realize_calls);
  while (g_gate_closed) usleep(1000);
  usleep(200);
  if (p->fail) { p->lazy = 0; return 0; }
  __atomic_add_fetch(&p->builds, 1, __ATOMIC_SEQ_CST);
  __sync_synchronize(); p->rps = 7;
  return p->rps;
}
static ULONG STDMETHODCALLTYPE pso_Release(ID3D12PipelineState *T);
'''
    tail = r'''
static ULONG STDMETHODCALLTYPE pso_Release(ID3D12PipelineState *T) { return (ULONG)InterlockedDecrement(&((struct mad_pso *)T)->refs); }
struct mad_exec { struct mad_pso *pso; int skipped; };
#define MAD_SKIP(e_) ((e_)->skipped++)
static int draw_gate(struct mad_exec *e) {   /* the text of exec_draw's gate; 1 = the draw goes on */
''' + gate.replace("return;", "return 0;") + r'''    return e->pso && (e->pso->rps || e->pso->tess);
}
static int fails = 0;
static void expect(bool c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
static bool wait_for(bool (*f)(void *), void *a, int ms = 5000) { for (int i = 0; i < ms; i++) { if (f(a)) return true; usleep(1000); } return false; }
static bool built(void *p) { return ((struct mad_pso *)p)->rps != 0; }
static bool idle(void *) { return g_pa_built + g_pa_failed == g_pa_queued; }
static struct mad_pso *mk() { struct mad_pso *p = new mad_pso(); p->lazy = 1; p->refs = 1; return p; }
static void *producer(void *a) { auto *v = (std::vector<struct mad_pso *> *)a; for (auto *p : *v) { int r = mad_pso_async_queue(p); if (r != 1) fails++; } return 0; }
int main(int argc, char **argv) {
  int ph = atoi(argv[1]);
  if (ph == 0) { g_cfg_async = 0; expect(mad_pso_async_on() == 0 && g_log.empty(), "key off (the default): off, no log line"); return fails; }
  if (ph == 1) { g_cfg_async = 1; g_cfg_lazy = 0; expect(mad_pso_async_on() == 0, "key on but pso-lazy off: off"); return fails; }
  g_cfg_async = 1;
  if (ph == 2) {
    expect(mad_pso_async_on() == 1 && g_log.find("pso-async = 1") != std::string::npos, "key on with pso-lazy: on, logged once");
    struct mad_pso *p = mk();
    expect(mad_pso_async_queue(p) == 1, "queue: accepted");
    expect(wait_for(built, p), "the pipeline is built in the background");
    expect(wait_for([](void *) { return g_pa_built == 1; }, 0) && p->builds == 1, "built exactly once");
    expect(wait_for([](void *a) { return ((struct mad_pso *)a)->refs == 1; }, p), "the queue's reference is given back (refs 1 again)");
    expect(mad_pso_async_queue(p) == 1 && g_pa_queued == 1, "a built pipeline is not queued again");
    struct mad_pso *q = mk(); q->async_q = 1; q->refs = 1;
    expect(mad_pso_async_queue(q) == 1 && g_pa_queued == 1 && q->refs == 1, "a pipeline already marked queued is not queued twice (no extra reference)");
    /* four producers, twenty pipelines, each built once */
    std::vector<struct mad_pso *> all[4]; std::vector<struct mad_pso *> flat;
    for (int i = 0; i < 20; i++) { auto *x = mk(); all[i % 4].push_back(x); flat.push_back(x); }
    pthread_t th[4];
    for (int i = 0; i < 4; i++) pthread_create(&th[i], 0, producer, &all[i]);
    for (int i = 0; i < 4; i++) pthread_join(th[i], 0);
    bool ok = true;
    for (auto *x : flat) ok &= wait_for(built, x);
    expect(ok, "twenty pipelines from four producer threads are all built");
    expect(wait_for(idle, 0), "the queue drains");
    bool once = true, refs = true;
    for (auto *x : flat) { once &= x->builds == 1; refs &= wait_for([](void *a) { return ((struct mad_pso *)a)->refs == 1; }, x); }
    expect(once, "each built exactly once");
    expect(refs, "every reference given back");
    /* the draw gate with the key on */
    struct mad_pso *g = mk(); struct mad_exec e = {g, 0};
    g_gate_closed = 1;
    int first = draw_gate(&e);
    expect(first == 0 && e.skipped == 1 && g_pa_skips == 1, "draw gate: the first draw of a new pipeline is skipped, not blocked");
    int again = draw_gate(&e);
    expect(again == 0 && e.skipped == 2, "draw gate: skipped again while it is being built, no second queue entry");
    g_gate_closed = 0;
    expect(wait_for(built, g), "the blocked builder finishes");
    expect(draw_gate(&e) == 1 && e.skipped == 2, "draw gate: drawn once built");
    struct mad_pso *t = mk(); t->tess = 1; struct mad_exec et = {t, 0};
    long before = g_realize_calls;
    expect(draw_gate(&et) == 1 && g_realize_calls == before + 1 && t->builds == 1 && et.skipped == 0, "draw gate: a tessellation pipeline is built inline");
    return fails;
  }
  if (ph == 3) {
    /* a blocked builder: the queue refuses at its limit and keeps no stray reference or mark */
    g_gate_closed = 1;
    std::vector<struct mad_pso *> ps; int ok = 0, refused = 0; bool clean = true;
    for (int i = 0; i < MAD_PA_MAX + 12; i++) {
      auto *p = mk(); ps.push_back(p);
      int r = mad_pso_async_queue(p);
      if (r == 1) { ok++; clean &= p->async_q == 1 && p->refs == 2; } else { refused++; clean &= p->async_q == 0 && p->refs == 1; }
    }
    expect(ok >= MAD_PA_MAX && ok <= MAD_PA_MAX + 2 && refused >= 10 && g_pa_inline == (LONG)refused, "the queue takes its 2048 (+ the two the builders hold) and refuses the rest, counted as built-inline");
    expect(clean, "a refused pipeline is unmarked with its reference count untouched; an accepted one is marked with one extra reference");
    g_gate_closed = 0;
    bool all = true;
    for (auto *p : ps) if (p->async_q == 1) all &= wait_for(built, p, 20000);
    expect(all, "after the gate opens every accepted pipeline is built");
    expect(wait_for(idle, 0, 20000), "the queue drains");
    bool refs = true, once = true;
    for (auto *p : ps) { refs &= wait_for([](void *a) { return ((struct mad_pso *)a)->refs == 1; }, p); once &= p->builds <= 1; }
    expect(refs && once, "every reference is back and nothing was built twice");
    /* a refused pipeline can still be queued later */
    struct mad_pso *r = 0; for (auto *p : ps) if (p->async_q == 0) { r = p; break; }
    expect(r && mad_pso_async_queue(r) == 1 && wait_for(built, r), "a pipeline refused earlier is accepted and built once there is room");
    return fails;
  }
  if (ph == 4) {
    struct mad_pso *p = mk(); p->fail = 1;
    expect(mad_pso_async_queue(p) == 1, "queue: accepted");
    expect(wait_for([](void *) { return g_pa_failed == 1; }, 0), "a failed build is counted");
    expect(wait_for([](void *a) { return ((struct mad_pso *)a)->refs == 1; }, p), "a failed build gives its reference back");
    expect(p->rps == 0 && p->lazy == 0, "the pipeline stays unbuilt and no longer lazy (its draws are skipped as before)");
    expect(mad_pso_async_queue(p) == 1 && g_pa_queued == 1, "it is not queued again");
    return fails;
  }
  if (ph == 5) {
    g_no_threads = 1;
    struct mad_pso *p = mk();
    expect(mad_pso_async_queue(p) == 0 && p->async_q == 0 && p->refs == 1, "no thread could be created: the caller builds it, nothing marked, no reference taken");
    expect(g_log.find("no background thread could be created") != std::string::npos, "and it says so");
    expect(mad_pso_async_queue(p) == 0, "the next call does not try again");
    struct mad_exec e = {p, 0};
    expect(draw_gate(&e) == 1 && p->builds == 1, "draw gate: the draw builds the pipeline inline and goes on");
    return fails;
  }
  if (ph == 6) {
    /* key off: the gate is the old line */
    g_cfg_async = 0;
    struct mad_pso *p = mk(); struct mad_exec e = {p, 0};
    expect(draw_gate(&e) == 1 && p->builds == 1 && e.skipped == 0 && g_pa_queued == 0, "key off: the first draw builds the pipeline inline and draws");
    return fails;
  }
  return 2;
}
'''
    full = harness + queue + tail
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(full)
        flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter", "-pthread"]
        b = subprocess.run([cxx, *flags, "-fsanitize=address,undefined", "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        if b.returncode:
            b = subprocess.run([cxx, *flags, "-o", str(t / "h"), str(t / "h.cpp")], capture_output=True, text=True)
        check("the queue and the draw gate compile on the host", b.returncode == 0)
        if b.returncode:
            print(b.stderr[-3000:])
        else:
            for ph, what in ((0, "key off"), (1, "pso-lazy off"), (2, "key on: queue, producers, draw gate"), (3, "queue full"),
                             (4, "failed build"), (5, "no thread"), (6, "key off: the draw gate")):
                r = subprocess.run([str(t / "h"), str(ph)], capture_output=True, text=True, timeout=180)
                print(r.stdout.rstrip())
                if r.returncode:
                    print(r.stderr[-1500:])
                check(f"harness case {ph} ({what})", r.returncode == 0)

print("FAILED: pso-async" if failures else "PASS: pso-async")
sys.exit(1 if failures else 0)
