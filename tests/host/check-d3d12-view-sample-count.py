#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A texture view whose sample count disagrees with its texture gets the texture's own type (build 652; madeira-d3d12; night round 2026-11-11, G1 S8).

Age of Wonders 4 (AOW4.exe, 64-bit Direct3D 12; Send Log DPJFWN, build 634, iPhone19,4) ended two seconds into its start in Metal's own validation:
    _mtlValidateArgumentsForTextureViewOnDevice:1877: failed assertion `Texture Creation
    source texture textureType (MTLTextureType2DArray) not compatible with texture view textureType (MTLTextureType2DMultisampleArray).'
A D3D12 engine fills every descriptor slot with a valid view of a small dummy texture, one per dimension, so a Texture2DMS / Texture2DMSArray SRV
can name a single-sample 2D dummy (and a Texture2D SRV a multisampled resource). Metal refuses a view of the other sample count by aborting the
process; the "texture view FAILED" fall-back of mad_texture_view_id never runs. The function now makes such a view a plain view of the texture's
own type (every level and slice), logs at most 8 of them, and leaves every pair of the same sample class as it was.

Checks:
  1. the source: the guard sits in mad_texture_view_id after the level clamps and before the "same type" shortcut and the view cache, names both
     multisample types on both sides, falls back to the texture's own type with explicit full ranges (not the ~0u placeholders);
  2. the real mad_texture_view_id is cut out of madeira_d3d12.c and compiled on the host with fakes for Metal and the view bookkeeping; the fake
     newTextureView records what Metal would be asked and flags what Metal's validation aborts on (a multisampled view of a single-sample texture or
     the reverse, a level or slice range outside the texture): a table of resources and requested views, among them the Age of Wonders 4 one, never
     reaches an aborting call, and every request of the same sample class reaches Metal with exactly the arguments it had before;
  3. the auto-tune rule is fixed in 652 and its fixture is there; CI runs this check.
Needs python3 and a C compiler (SKIP of part 2 without one).
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
src = (root / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()
ok = True


def check(what, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1800])


def cut(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a) + len(end)]


fn = cut(src, "static UINT64 mad_texture_view_id(struct mad_device *d, struct mad_resource *r, enum WMTTextureType want,\n"
              "                                  UINT lvl0, UINT nlvl, UINT sl0, UINT nsl, enum WMTPixelFormat pf, UINT swz) {", "\n}\n")
level_beyond = cut(src, "/* mip0-view-test:begin */", "/* mip0-view-test:end */")
swz_macro = [ln for ln in src.splitlines() if ln.startswith("#define MAD_SWZ_IDENTITY")][0]

# ---- 1. the source
g0 = fn.find("{   /* night round 2026-11-11 (G1 S8")
guard = fn[g0:fn.index("\n    }\n", g0) + 7] if g0 >= 0 else ""
check("the guard comes after the level and slice clamps and the mip0-view line, before the same-type shortcut and the view cache",
      g0 >= 0 and fn.index("if (nsl == 0 || nsl == ~0u || sl0 + nsl > r->tex_layers)") < fn.index("mad_view_level_beyond(lvl0, r->tex_mips)") < g0
      < fn.index("if (want == r->tex_type && lvl0 == 0 && nlvl == r->tex_mips") < fn.index("EnterCriticalSection(&d->view_lock)"))
check("it compares the sample class of the texture's type with the view's, both multisample types on both sides",
      "int src_ms  = r->tex_type == WMTTextureType2DMultisample || r->tex_type == WMTTextureType2DMultisampleArray;" in guard
      and "int want_ms = want == WMTTextureType2DMultisample || want == WMTTextureType2DMultisampleArray;" in guard
      and "if (src_ms != want_ms) {" in guard)
check("a mismatch takes the texture's own type with explicit full level and slice ranges (not the ~0u placeholders), logs at most 8 lines",
      "want = r->tex_type; lvl0 = 0; nlvl = r->tex_mips; sl0 = 0; nsl = r->tex_layers;" in guard and "~0u" not in guard
      and "if (InterlockedIncrement(&said_ms) <= 8)" in guard and "[madeira-d3d12] view-sample-count r#%u" in guard)

# ---- 2. the real function on the host
harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned UINT; typedef uint64_t UINT64; typedef uint32_t UINT32; typedef long LONG; typedef uint64_t obj_handle_t; typedef struct { int d; } CRITICAL_SECTION;
enum WMTTextureType { WMTTextureType2D = 2, WMTTextureType2DArray = 3, WMTTextureType2DMultisample = 4, WMTTextureTypeCube = 5, WMTTextureTypeCubeArray = 6,
                      WMTTextureType3D = 7, WMTTextureType2DMultisampleArray = 8 };
enum WMTPixelFormat { WMTPixelFormatRGBA8Unorm = 70, WMTPixelFormatBGRA8Unorm = 80, WMTPixelFormatX32_Stencil8 = 261 };
enum WMTTextureSwizzle { WMTTextureSwizzleZero = 0, WMTTextureSwizzleOne = 1 };
struct WMTTextureSwizzleChannels { enum WMTTextureSwizzle r, g, b, a; };
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
#define EnterCriticalSection(c) ((void)(c))
#define LeaveCriticalSection(c) ((void)(c))
''' + swz_macro + r'''
static int nlog, nlog_ms; static char lastlog[512], lastms[512];   /* nlog_ms: the view-sample-count lines only (the function also logs "texture view:" lines) */
static void d3d12_log(const char *fmt, ...)
{
    va_list ap; va_start(ap, fmt); vsnprintf(lastlog, sizeof lastlog, fmt, ap); va_end(ap); nlog++;
    if (strstr(lastlog, "view-sample-count")) { nlog_ms++; memcpy(lastms, lastlog, sizeof lastms); }
}
struct mad_device { CRITICAL_SECTION view_lock; };
struct mad_resource {
    obj_handle_t texture; int is_depth; enum WMTPixelFormat tex_pf; UINT tex_mips, tex_layers; enum WMTTextureType tex_type; UINT64 gpu_resource_id;
    unsigned nxview, xview_cap; struct mad_xview { UINT type, lvl0, nlvl, sl0, nsl, pf, swz; obj_handle_t tex; UINT64 id; } *xview;
    unsigned serial; const char *name; UINT width, height; struct { unsigned MipLevels; } desc;
};
static int ncalls, violations; static struct { UINT type, lvl0, nlvl, sl0, nsl, pf; } calls[64]; static UINT64 next_id = 1000;
static obj_handle_t MTLTexture_newTextureView(obj_handle_t src, enum WMTPixelFormat pf, enum WMTTextureType type, uint16_t l0, uint16_t nl, uint16_t s0, uint16_t ns,
                                              struct WMTTextureSwizzleChannels sw, uint64_t *id) {
    /* what Metal's own validation aborts on, and the call is recorded */
    struct mad_resource *r = (struct mad_resource *)(uintptr_t)src;
    int src_ms = r->tex_type == WMTTextureType2DMultisample || r->tex_type == WMTTextureType2DMultisampleArray;
    int want_ms = type == WMTTextureType2DMultisample || type == WMTTextureType2DMultisampleArray;
    (void)sw;
    if (src_ms != want_ms) violations++;                       /* source texture textureType ... not compatible with texture view textureType */
    if (l0 + nl > r->tex_mips) violations++;                   /* newLevelRange ... must be <= [texture mipmapLevelCount] */
    if (s0 + ns > r->tex_layers && r->tex_type != WMTTextureType3D) violations++;   /* newSliceRange */
    calls[ncalls].type = type; calls[ncalls].lvl0 = l0; calls[ncalls].nlvl = nl; calls[ncalls].sl0 = s0; calls[ncalls].nsl = ns; calls[ncalls].pf = pf; ncalls++;
    *id = next_id++;
    return (obj_handle_t)next_id;
}
static void NSObject_release(obj_handle_t h) { (void)h; }
static void mad_resident(struct mad_device *d, obj_handle_t h) { (void)d; (void)h; }
static void mad_vmap_put_locked(struct mad_device *d, UINT64 id, UINT32 a, UINT32 b) { (void)d; (void)id; (void)a; (void)b; }
static void mad_view_census(const char *kind, struct mad_resource *r, unsigned n) { (void)kind; (void)r; (void)n; }
static int mad_view_grow(struct mad_resource *r, void **arr, unsigned *cap, unsigned need, size_t elem) {
    (void)r; if (need <= *cap) return 1;
    *arr = realloc(*arr, (size_t)need * elem); if (!*arr) return 0; *cap = need; return 1;
}
static LONG g_xview_live;
''' + level_beyond + "\n" + fn + r'''
static struct mad_device dev;
static struct mad_resource *mk(enum WMTTextureType t, UINT mips, UINT layers, UINT w, UINT h)
{
    struct mad_resource *r = calloc(1, sizeof *r);
    static unsigned serial; static int slot;
    r->texture = (obj_handle_t)(uintptr_t)r; r->tex_pf = WMTPixelFormatRGBA8Unorm; r->tex_type = t; r->tex_mips = mips; r->tex_layers = layers; r->gpu_resource_id = 7000 + 10 * (UINT64)slot++;
    r->serial = ++serial; r->name = "Resource"; r->width = w; r->height = h; r->desc.MipLevels = mips;
    return r;
}
static void show(const char *label, struct mad_resource *r, UINT64 id, int from)
{
    int k;
    printf("%s: id=%s", label, id == r->gpu_resource_id ? "own" : "view");
    for (k = from; k < ncalls; k++)
        printf(" call[type=%u l=%u+%u s=%u+%u pf=%u]", calls[k].type, calls[k].lvl0, calls[k].nlvl, calls[k].sl0, calls[k].nsl, calls[k].pf);
    printf(" calls=%d ms_logs=%d violations=%d\n", ncalls - from, nlog_ms, violations);
}
int main(void)
{
    struct mad_resource *r; UINT64 id; int from; const UINT swz_rgba_swapped = 2u | 4u << 8 | 3u << 16 | 5u << 24;
    /* A: the Age of Wonders 4 case, a 4x1 single-sample dummy, a Texture2DMSArray SRV (type 8) of it, full ranges, identity swizzle */
    r = mk(WMTTextureType2DArray, 1, 1, 4, 1); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DMultisampleArray, 0, ~0u, 0, 1, 0, 0); show("A multisampled SRV of a single-sample dummy", r, id, from);
    printf("A-log: %s", lastms);
    /* B: the same with a swizzle: a view is made, of the texture's own type, full ranges */
    r = mk(WMTTextureType2DArray, 3, 2, 64, 64); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DMultisample, 1, 1, 1, 1, 0, swz_rgba_swapped); show("B multisampled view, a swizzle, sub-ranges asked", r, id, from);
    /* C: a multisampled resource and a plain 2D array view request (a Texture2D SRV of an MS resource) */
    r = mk(WMTTextureType2DMultisampleArray, 1, 2, 256, 256); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DArray, 0, ~0u, 0, 1, 0, swz_rgba_swapped); show("C plain view of a multisampled texture", r, id, from);
    /* D: the flat 2D view of a multisampled array (ml1308's helper): both multisampled, nothing changes */
    r = mk(WMTTextureType2DMultisampleArray, 1, 2, 256, 256); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DMultisample, 0, 1, 1, 1, 0, 0); show("D flat view of a multisampled array", r, id, from);
    /* E: single-sample sources and single-sample views: the sub-range and the type are what was asked */
    r = mk(WMTTextureType2DArray, 5, 4, 256, 256); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DArray, 1, 2, 1, 2, 0, 0); show("E sub-range view of a single-sample array", r, id, from);
    r = mk(WMTTextureType2DArray, 5, 6, 256, 256); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureTypeCube, 0, ~0u, 0, 6, 0, 0); show("F cube view of a single-sample array", r, id, from);
    r = mk(WMTTextureType2DArray, 1, 1, 4, 4); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DArray, 0, ~0u, 0, 1, 0, 0); show("G the texture's own view", r, id, from);
    /* H: a 3D texture asked for as multisampled: its own type, every level */
    r = mk(WMTTextureType3D, 4, 1, 64, 64); from = ncalls;
    id = mad_texture_view_id(&dev, r, WMTTextureType2DMultisampleArray, 0, ~0u, 0, 1, 0, swz_rgba_swapped); show("H multisampled view of a 3D texture", r, id, from);
    /* I: the log stops at eight lines */
    {
        int k, n0 = nlog_ms;
        for (k = 0; k < 20; k++) { r = mk(WMTTextureType2DArray, 1, 1, 4, 4); mad_texture_view_id(&dev, r, WMTTextureType2DMultisampleArray, 0, ~0u, 0, 1, 0, 0); }
        printf("I twenty more mismatches logged %d lines (ms_logs %d in all)\n", nlog_ms - n0, nlog_ms);
    }
    printf("violations total %d\n", violations);
    return 0;
}
'''
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the view harness")
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "view.c"
        c.write_text(harness)
        exe = Path(tmp) / "view"
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the real mad_texture_view_id compiles against fakes (-Wall)", b.returncode == 0 and "warning" not in b.stderr, b.stderr[-2500:])
        if b.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60).stdout
            L = {ln[0]: ln for ln in out.splitlines() if ln[:2] in {c + " " for c in "ABCDEFGH"}}
            check("A: the Age of Wonders 4 view (a multisampled SRV of a single-sample dummy, identity swizzle) is the texture's own id, no Metal call, one log line",
                  "A multisampled SRV of a single-sample dummy: id=own calls=0 ms_logs=1" in out
                  and "A-log: [madeira-d3d12] view-sample-count r#1 Resource 4x1: a multisampled view of a single-sample texture; using the texture's own type" in out, out[:600])
            check("B: with a swizzle the view is of the texture's own type (3) with every level (0+3) and slice (0+2), not the sub-range asked for",
                  "B multisampled view, a swizzle, sub-ranges asked: id=view call[type=3 l=0+3 s=0+2 pf=70] calls=1" in out, out[:900])
            check("C: a plain view request of a multisampled texture becomes the own multisampled type (8) with every slice",
                  "C plain view of a multisampled texture: id=view call[type=8 l=0+1 s=0+2 pf=70] calls=1" in out, out)
            check("D: the flat 2D view of a multisampled array (both multisampled) reaches Metal exactly as asked, and logs nothing new",
                  "D flat view of a multisampled array: id=view call[type=4 l=0+1 s=1+1 pf=70] calls=1 ms_logs=3" in out, out)
            check("E, F: single-sample sources with single-sample views reach Metal with the type and ranges asked, no log",
                  "E sub-range view of a single-sample array: id=view call[type=3 l=1+2 s=1+2 pf=70] calls=1 ms_logs=3" in out
                  and "F cube view of a single-sample array: id=view call[type=5 l=0+5 s=0+6 pf=70] calls=1 ms_logs=3" in out, out)
            check("G: the texture's own view is the texture's id, as before", "G the texture's own view: id=own calls=0 ms_logs=3" in out, out)
            check("H: a multisampled view of a 3D texture is a view of the 3D type (7), every level",
                  "H multisampled view of a 3D texture: id=view call[type=7 l=0+4 s=0+1 pf=70] calls=1 ms_logs=4" in out, out)
            check("I: only eight lines are logged in all", "I twenty more mismatches logged 4 lines (ms_logs 8 in all)" in out, out[-300:])
            check("no call that Metal's validation would abort on reaches the fake Metal (sample class, level range, slice range)",
                  "violations total 0" in out and "violations=0" in out and "violations=1" not in out, out)

# ---- 3. rule, fixture, CI
rules = (root / "build/tools/autotune_rules.py").read_text()
r0 = rules.find('"id": "d3d12-view-sample-count-mismatch"')
rb = rules[r0:rules.index("\n    },\n", r0)] if r0 >= 0 else ""
check("the auto-tune rule is fixed in 652 (no line, no noFix) and its fixture is there",
      '"fixedIn": 652' in rb and '"lines": []' in rb and '"noFix"' not in rb
      and (root / "tests/fixtures/autotune/age-of-wonders-4-view-sample-count-2026-11-09.expect").is_file())
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-view-sample-count.py" in ci)

print("PASS: d3d12 view sample count" if ok else "FAILED: d3d12 view sample count")
sys.exit(0 if ok else 1)
