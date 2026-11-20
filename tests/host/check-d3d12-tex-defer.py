#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A released D3D12 texture waits for the GPU (build 666; issue #36, skate.; madeira-d3d12/src/pe/madeira_d3d12.c).

skate. crashed in about 2 of 10 launches while it loaded: "x0 was FREED by our NSObject_release: AGXG19FamilyTexture", objc_retain
called from IOGPUMetalCommandBufferStorageReset on Metal's completion queue. res_Release released the resource's MTLTexture, its typed
and extra views and its resolve stand-in at once, and Metal's own command-buffer teardown still touched the texture a little later:
the race ml1148 closed for heaps. Checks:
  - res_Release hands the texture, every typed and extra view and the resolve stand-in to mad_release_after_gpu, and nothing else
    changes there (the buffer, the counters, the unresident calls stay where they were);
  - the production mad_release_after_gpu, mad_tex_defer_on and mad_mheap_reclaim, compiled with a stub device: an object waits until
    two more command buffers have completed; tex-defer = 0 releases at once; a list that cannot grow releases at once; the reclaim
    drains every ready entry (more than 64 in one call); a texture is released before a heap queued with it (the old order of a
    placed texture and its heap); "all" (device teardown) releases everything; the two older enqueue sites clear the new field;
  - CI runs this check.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:2000])


def function(start):
    a = src.index(start)
    b = src.index("{", a)
    depth, c = 1, b + 1
    while depth:
        depth += (src[c] == "{") - (src[c] == "}")
        c += 1
    return src[a:c]


rel = function("static ULONG STDMETHODCALLTYPE res_Release(ID3D12Resource *This) {")
check("res_Release: typed views, extra views, the texture and the resolve stand-in go to mad_release_after_gpu",
      "mad_release_after_gpu(r->owner, r->tview[k].tex); InterlockedDecrement(&g_tview_live);" in rel
      and "mad_unresident(r->owner, r->xview[k].tex); mad_release_after_gpu(r->owner, r->xview[k].tex); InterlockedDecrement(&g_xview_live);" in rel
      and "if (r->texture && !r->borrowed) mad_release_after_gpu(r->owner, r->texture);" in rel
      and "if (r->resolve_tmp) mad_release_after_gpu(r->owner, r->resolve_tmp);" in rel)
check("...the buffer is still released at once, and no texture is released at once any more",
      "if (r->buffer) NSObject_release(r->buffer);" in rel
      and "NSObject_release(r->texture)" not in rel and "NSObject_release(r->resolve_tmp)" not in rel
      and "NSObject_release(r->tview[k].tex)" not in rel and "NSObject_release(r->xview[k].tex)" not in rel)
check("the two older enqueue sites (buffer storage, heaps) clear the new field",
      "md->mhret[md->nmhret].obj = 0;" in src and "hd->mhret[hd->nmhret].obj = 0;" in src
      and "struct mad_mhret { obj_handle_t heap; UINT64 serial; void *mem; obj_handle_t obj; }" in src)
check("the catalog documents tex-defer (default 1)",
      re.search(r'"tex-defer": \{[^}]*"default": "1"', (root / "build/tools/gen-config-catalog.py").read_text()) is not None)
check("CI runs this check", "python3 tests/host/check-d3d12-tex-defer.py" in (root / ".github/workflows/build-ipa.yml").read_text())

harness_head = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef uint64_t UINT64; typedef long long LONG64; typedef void *obj_handle_t;
typedef struct { int unused; } CRITICAL_SECTION;
#define MEM_RELEASE 0x8000
static int locked;
static void EnterCriticalSection(CRITICAL_SECTION *c) { (void)c; if (locked++) abort(); }
static void LeaveCriticalSection(CRITICAL_SECTION *c) { (void)c; if (--locked) abort(); }
struct mad_device {
    volatile LONG64 gpu_serial;
    CRITICAL_SECTION heap_lock;
    struct mad_mhret { obj_handle_t heap; UINT64 serial; void *mem; obj_handle_t obj; } *mhret; unsigned nmhret, mhret_cap;
};
static UINT64 completed;
static UINT64 mad_gpu_completed(struct mad_device *d) { (void)d; return completed; }
static char events[8192]; static size_t nev;
static void note(char kind, void *p) { if (locked) abort(); nev += (size_t)snprintf(events + nev, sizeof events - nev, "%c%lu ", kind, (unsigned long)(uintptr_t)p); }
static void NSObject_release(obj_handle_t o) { note('R', o); }
static void mad_unresident(struct mad_device *d, obj_handle_t o) { (void)d; note('U', o); }
static int VirtualFree(void *p, size_t n, unsigned f) { (void)n; if (f != MEM_RELEASE) abort(); note('F', p); return 1; }
static long long cfg_value = 1;
static long long mad_cfg_int_pe(const char *key, long long dflt) { (void)dflt; if (strcmp(key, "tex-defer")) abort(); return cfg_value; }
static void d3d12_log(const char *fmt, ...) { (void)fmt; }
static int grow_fails;
"""
grow = function("static int mad_grow(void **arr, unsigned *cap, unsigned need, size_t elem) {")
grow = grow.replace("    if (need <= *cap) return 1;", "    if (need <= *cap) return 1;\n    if (grow_fails) return 0;", 1)
reclaim = function("static void mad_mheap_reclaim(struct mad_device *d, int all) {")
defer_on = src[src.index("static int g_tex_defer = -1;"):]
defer_on = defer_on[:defer_on.index("static void mad_release_after_gpu(")]
after = function("static void mad_release_after_gpu(struct mad_device *d, obj_handle_t obj) {")
tests = r"""
static int fails;
#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n  events: %s\n", __LINE__, #c, events); fails++; } } while (0)
static void reset_events(void) { nev = 0; events[0] = 0; }
static void push_heap(struct mad_device *d, uintptr_t heap, void *mem) {
    mad_grow((void **)&d->mhret, &d->mhret_cap, d->nmhret + 1, sizeof *d->mhret);
    d->mhret[d->nmhret].heap = (obj_handle_t)heap; d->mhret[d->nmhret].mem = mem; d->mhret[d->nmhret].obj = 0;
    d->mhret[d->nmhret].serial = (UINT64)d->gpu_serial; d->nmhret++;
}
int main(void) {
    struct mad_device d = {0};
    unsigned i;
    /* an object waits until two more command buffers have completed */
    d.gpu_serial = 10; completed = 9; reset_events();
    mad_release_after_gpu(&d, (obj_handle_t)101);
    T(d.nmhret == 1 && nev == 0);
    completed = 11; mad_mheap_reclaim(&d, 0); T(d.nmhret == 1 && nev == 0);
    completed = 12; mad_mheap_reclaim(&d, 0); T(d.nmhret == 0 && !strcmp(events, "R101 "));
    /* NULL is ignored */
    reset_events(); mad_release_after_gpu(&d, NULL); T(d.nmhret == 0 && nev == 0);
    /* more than 64 ready entries are released in one call */
    reset_events(); d.gpu_serial = 20;
    for (i = 0; i < 200; i++) mad_release_after_gpu(&d, (obj_handle_t)(uintptr_t)(1000 + i));
    T(d.nmhret == 200);
    completed = 22; mad_mheap_reclaim(&d, 0);
    T(d.nmhret == 0);
    { unsigned n = 0; for (const char *p = events; (p = strchr(p, 'R')); p++) n++; T(n == 200); }
    /* a texture before the heap queued with it, though the heap is on the list first */
    reset_events(); d.gpu_serial = 30;
    push_heap(&d, 7, NULL); mad_release_after_gpu(&d, (obj_handle_t)8); push_heap(&d, 0, (void *)9);
    completed = 32; mad_mheap_reclaim(&d, 0);
    T(d.nmhret == 0 && !strcmp(events, "R8 U7 R7 F9 "));
    /* teardown: everything, whatever the GPU did */
    reset_events(); d.gpu_serial = 40; completed = 0;
    mad_release_after_gpu(&d, (obj_handle_t)41); push_heap(&d, 42, NULL);
    mad_mheap_reclaim(&d, 1); T(d.nmhret == 0 && !strcmp(events, "R41 U42 R42 "));
    /* a list that cannot grow releases at once */
    reset_events(); d.mhret_cap = d.nmhret; grow_fails = 1;
    mad_release_after_gpu(&d, (obj_handle_t)51); T(d.nmhret == 0 && !strcmp(events, "R51 ")); grow_fails = 0;
    /* no device: at once */
    reset_events(); mad_release_after_gpu(NULL, (obj_handle_t)52); T(!strcmp(events, "R52 "));
    /* tex-defer = 0 (read once): at once, as before build 666 */
    g_tex_defer = -1; cfg_value = 0; reset_events();
    mad_release_after_gpu(&d, (obj_handle_t)61); T(d.nmhret == 0 && !strcmp(events, "R61 "));
    cfg_value = 1; mad_release_after_gpu(&d, (obj_handle_t)62); T(d.nmhret == 0 && !strcmp(events, "R61 R62 "));
    free(d.mhret);
    printf(fails ? "HARNESS FAILED\n" : "harness ok\n");
    return fails != 0;
}
"""
cc = shutil.which("cc") or shutil.which("clang")
with tempfile.TemporaryDirectory(prefix="bcd-tex-defer-") as tmp:
    c = Path(tmp) / "t.c"
    exe = Path(tmp) / "t"
    c.write_text(harness_head + grow + "\n" + reclaim + "\n" + defer_on + after + "\n" + tests)
    b = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Werror", "-Wno-unused-function", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer", str(c), "-o", str(exe)], capture_output=True, text=True)
    check("the production reclaim and deferral compile on the host", b.returncode == 0, b.stderr)
    if b.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"))
        check("deferral: two more command buffers, a full drain, textures before heaps, teardown, grow failure, tex-defer = 0",
              r.returncode == 0 and r.stdout.strip().endswith("harness ok"), r.stdout + r.stderr)
        # a mutant that releases at once must fail the harness
        bad = (harness_head + grow + "\n" + reclaim + "\n" + defer_on + after + "\n" + tests).replace(
            "if (d && mad_tex_defer_on()) {", "if (0 && d && mad_tex_defer_on()) {", 1)
        c.write_text(bad)
        b = subprocess.run([cc, "-std=gnu11", "-O1", "-Wall", "-Werror", "-Wno-unused-function", str(c), "-o", str(exe)],
                           capture_output=True, text=True)
        r = subprocess.run([str(exe)], capture_output=True, text=True) if b.returncode == 0 else None
        check("negative control: releasing at once is caught", r is not None and r.returncode != 0 and "HARNESS FAILED" in r.stdout)
print("PASS: d3d12 tex-defer" if ok else "FAILED: d3d12 tex-defer")
sys.exit(0 if ok else 1)
