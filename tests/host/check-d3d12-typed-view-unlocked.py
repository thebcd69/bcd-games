#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""typed-view-unlocked = 1 (build 657, off unless set; madeira-d3d12/src/pe/madeira_d3d12.c, mad_typed_buffer_view).

A typed buffer view is a Metal texture buffer. BCD made it while it held the device's one view lock, so threads that create
views make them one at a time. The Last of Us Part I (the owner's log of 2026-11-12 19:48, build 656) re-creates its 6-20 MB
frame buffers every frame and makes 64 to over 1,000 views on each from six job threads while they record: 20,000 to 58,000
views a second in play; "[xp-api] CS contended 22-63k/s ... wait 0.9-1.3 s per second", and 93 % of the sampled contended
enters are on that lock ("[xp-api-cs]": device + 0x43a8, beside the device's live_lock at + 0x78 and fence_lock at
+ 0x44c8).
With the switch a plain view has its texture made with the lock released; the lock is taken again, the view is looked up
once more, and a view another thread made meanwhile wins.
Checks:
  - the source: the switch is off unless set; the lock is released only around the one Metal call and only for a plain view
    (no RGB32 copy, no aligned copy, the default 64-byte alignment, no ShaderAtomic usage); the view is looked up again
    under the lock; a texture made outside is either released or published by the function's own code; the code under
    the lock is the code it was;
  - on the host: the shape the unlocked path computes, compiled from the source, against the locked path's arithmetic
    written here, over 200,000 ranges; and a model of the two-step insert on eight threads (every key ends with exactly
    one texture, every other one made is released). The function itself runs on the host in check-skin-check.py, with the
    switch off and on: where the texture is made, another thread making the same view meanwhile, a refusal, a list that
    cannot grow, a cut view;
  - the whole runtime compiles (the full tree's toolchain); CI runs this check.
"""
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile

R = pathlib.Path(__file__).resolve().parents[2]
ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        ok = False
        if detail:
            print("     " + str(detail)[:1500])


SRC = (R / "madeira-d3d12/src/pe/madeira_d3d12.c").read_text()

# --- the source ---------------------------------------------------------------
fn = SRC[SRC.index("static int mad_typed_buffer_view(struct mad_device *d, struct mad_resource *r, DXGI_FORMAT fmt,"):]
fn = fn[:fn.index("\n}\n") + 3]
sw = SRC[SRC.index("static int mad_tv_unlocked(void) {"):SRC.index("/* tv-unlocked-test:begin */")]
shape = SRC[SRC.index("/* tv-unlocked-test:begin */"):SRC.index("/* tv-unlocked-test:end */")]

check("the switch is off unless set", 'mad_cfg_int_pe("typed-view-unlocked", 0) ? 1 : 0;' in sw)
check("...and says so once when it is on",
      sw.count("typed-view-unlocked = 1: typed buffer views are made outside the device's view lock") == 1)

head = fn[:fn.index("    again:\n")]
blk = head[head.index("/* madeira-bcd typed-view-unlocked: a plain view's texture is made with the lock released;"):]
check("the unlocked path is taken only for a plain view: no RGB32 copy, 64-byte alignment, the switch, no aligned copy, "
      "no ShaderAtomic usage",
      "if (!x3 && al == 64 && mad_tv_unlocked() && !mad_tvs_on() &&" in blk
      and "!(uav && (pf == WMTPixelFormatR32Uint || pf == WMTPixelFormatR32Sint) && mad_typed_uav_atomic())) {" in blk)
check("...and only after the view was looked up under the lock and not found",
      fn.index("EnterCriticalSection(&d->view_lock);   /* ml1049: creation races creation on another thread */")
      < fn.index("k = mad_tvh_find(r, (UINT)fmt, byte_off, num, (UINT)uav);   /* madeira-bcd tv-hash */")
      < fn.index("if (k == r->ntview) {") < fn.index("/* madeira-bcd typed-view-unlocked: a plain view's texture is made with the lock released;"))
i_leave = blk.index("LeaveCriticalSection(&d->view_lock);")
i_make = blk.index("pre = MTLBuffer_newTexture(r->buffer, &pti, pre_aligned, pre_bpr);")
i_enter = blk.index("EnterCriticalSection(&d->view_lock);")
i_find = blk.index("k = mad_tvh_find(r, (UINT)fmt, byte_off, num, (UINT)uav);")
check("the lock is released around the one Metal call, taken again, and the view is looked up once more",
      i_leave < i_make < i_enter < i_find and blk.count("LeaveCriticalSection") == 1 and blk.count("EnterCriticalSection") == 1
      and blk.count("MTLBuffer_newTexture") == 1)
check("nothing of the resource's view list is read while the lock is released",
      "tview" not in blk[i_leave:i_enter] and "tvh" not in blk[i_leave:i_enter])
check("a view another thread made meanwhile wins: ours is released and the function goes on with theirs",
      "if (k != r->ntview) {" in blk[i_find:] and "if (pre) { NSObject_release(pre); InterlockedIncrement(&g_tv_unl_lost); }" in blk[i_find:]
      and "goto have;" in blk[i_find:]
      and "\nhave:" in fn and fn.index("\nhave:") < fn.index("elem_off = r->tview[k].eoff;"))
check("a texture Metal refused outside the lock is dropped, and the code below tries and reports as before",
      "if (pre && !pti.gpu_resource_id) { NSObject_release(pre); pre = 0; }" in blk
      and blk.index("if (pre && !pti.gpu_resource_id)") < i_find and blk.rstrip().endswith("}"))
i_pub = blk.index("if (pre) {   /* ours to publish:")
pub = blk[i_pub:blk.index("goto made;")]
check("a texture made outside is published by the function's own code: its variables are set to what that code computes "
      "for these inputs, then it goes on at the point where the texture exists",
      i_find < i_pub
      and "aligned = pre_aligned; elem_off = (byte_off - aligned) / bytes; width = pti.width; bpr = pre_bpr;" in pub
      and "ti = pti; cut = pre_cut; sh_gpu = 0; sh = NULL; tex = pre;" in pub
      and fn.index("    again:\n") < fn.index("\n    made:") < fn.index("if (cut && why) *why = 9;")
      < fn.index("if (!mad_view_grow(r, (void **)&r->tview, &r->tview_cap, r->ntview + 1, sizeof *r->tview)) {"))
check("...so a list that cannot grow releases it, as it releases one made under the lock",
      "NSObject_release(tex); LeaveCriticalSection(&d->view_lock); if (why) *why = 8; return 0;" in fn[fn.index("\n    made:"):])
check("the texture has exactly three ways on after it is made: released (another thread's view, or refused), or published",
      blk.count("pre = MTLBuffer_newTexture(") == 1 and blk.count("NSObject_release(pre)") == 2
      and blk.count("goto have;") == 1 and blk.count("goto made;") == 1 and blk.count("goto ") == 2 and "tex = pre;" in pub)
BASE = """        if (tex) elem_off = 0;
        else tex = MTLBuffer_newTexture(r->buffer, &ti, aligned, bpr);
"""
check("the code under the lock is the code it was: the two no-width returns, the copy-or-make lines, the retries",
      BASE in fn
      and "if (aligned >= r->size) { LeaveCriticalSection(&d->view_lock); if (why) *why = 5; return 0; }   /* as before: no width */" in fn
      and "if (!width) { LeaveCriticalSection(&d->view_lock); if (why) *why = 5; return 0; }" in fn
      and fn.count("tex = MTLBuffer_newTexture(r->buffer, &ti, aligned, bpr);") == 2   # the plain call and the ShaderAtomic retry
      and "goto again;" in fn)
check("the view is published as before: grown, written, counted, indexed, mapped by id, all under the lock",
      fn.index("r->ntview = k + 1;   /* published last: a reader never sees a half-written entry */")
      < fn.index("mad_tvh_add(r, k);") < fn.index("mad_vmap_put_locked(d, ti.gpu_resource_id, (UINT32)num, (UINT32)elem_off);")
      < fn.index("\nhave:"))
callers = [ln for ln in SRC.splitlines() if "mad_typed_buffer_view(" in ln and "static int mad_typed_buffer_view" not in ln]
check("the function has its four callers (the view creators), none of them inside the view lock", len(callers) == 4, callers)
locked_regions = []
pos = 0
while True:
    a = SRC.find("EnterCriticalSection(&d->view_lock);", pos)
    if a < 0:
        break
    b = SRC.find("LeaveCriticalSection(&d->view_lock);", a)
    locked_regions.append(SRC[a:b])
    pos = a + 1
check("no code between an Enter and the next Leave of the view lock calls the view creator",
      all("mad_typed_buffer_view(" not in reg for reg in locked_regions), len(locked_regions))

# the locked path's arithmetic, as the test reads it from the source
check("the locked path's arithmetic is the one modelled below",
      all(t in fn for t in (
          "aligned = byte_off & ~(UINT64)(al - 1);",
          "elem_off = (byte_off - aligned) / bytes;",
          "width = num + elem_off;",
          "if (aligned + width * bytes > r->size) { width = (r->size - aligned) / bytes; cut = 1; }",
          "if (width > (1u << 28)) { width = (1u << 28); cut = 1; }",
          "bpr = width * bytes;",
          "ti.pixel_format = pf; ti.width = (uint32_t)width; ti.height = 1; ti.depth = 1; ti.array_length = 1;",
          "ti.type = WMTTextureTypeTextureBuffer; ti.mipmap_level_count = 1; ti.sample_count = 1;",
          "ti.usage = uav ? (WMTTextureUsageShaderRead | WMTTextureUsageShaderWrite) : WMTTextureUsageShaderRead;",
          "ti.options = (r->cpu || r->own_mem) ? WMTResourceStorageModeShared : WMTResourceStorageModePrivate;")))
check("the shape helper is called with the resource's size and storage, the view's format, range and access",
      "mad_tv_plain_shape(r->size, r->cpu || r->own_mem, (UINT)pf, bytes, byte_off, num, uav, al, &pti, &pre_aligned, &pre_bpr, &pre_cut)" in blk)


def locked_shape(size, shared, pf, bytes_, byte_off, num, uav, al=64):
    aligned = byte_off & ~(al - 1)
    elem_off = (byte_off - aligned) // bytes_
    width = num + elem_off
    cut = 0
    if aligned >= size:
        return None
    if aligned + width * bytes_ > size:
        width = (size - aligned) // bytes_
        cut = 1
    if not width:
        return None
    if width > (1 << 28):
        width = 1 << 28
        cut = 1
    return (aligned, width * bytes_, width, 3 if uav else 1, 1 if shared else 2, pf, cut)


# --- on the host ---------------------------------------------------------------
cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if cc:
    prog = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
typedef uint64_t UINT64; typedef unsigned int UINT;
enum WMTPixelFormat { WMTPixelFormatInvalid = 0 };
enum { WMTTextureTypeTextureBuffer = 9 };
enum { WMTTextureUsageShaderRead = 1, WMTTextureUsageShaderWrite = 2 };
enum { WMTResourceStorageModeShared = 1, WMTResourceStorageModePrivate = 2 };
struct WMTTextureInfo { int pixel_format; uint32_t width, height, depth, array_length; int type; uint32_t mipmap_level_count, sample_count;
                        int usage, options; uint64_t gpu_resource_id; };
''' + shape.replace("(enum WMTPixelFormat)pf", "(int)pf") + r'''
/* the two-step insert of mad_typed_buffer_view with the switch on, on a table of keys: look up under the lock; not there:
 * release the lock, make, take the lock, look up again; there now: release ours; else insert ours. */
#define KEYS 4096
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static uint64_t table[KEYS]; static long made, released, inserted, lost;
static uint64_t next_id = 1;
static void *worker(void *arg) {
    unsigned seed = (unsigned)(uintptr_t)arg; int n;
    for (n = 0; n < 200000; n++) {
        unsigned key = rand_r(&seed) % KEYS; uint64_t pre;
        pthread_mutex_lock(&lock);
        if (table[key]) { pthread_mutex_unlock(&lock); continue; }
        pthread_mutex_unlock(&lock);
        pre = __sync_fetch_and_add(&next_id, 1); __sync_fetch_and_add(&made, 1);
        pthread_mutex_lock(&lock);
        if (table[key]) { __sync_fetch_and_add(&released, 1); lost++; }
        else { table[key] = pre; inserted++; }
        pthread_mutex_unlock(&lock);
    }
    return NULL;
}
int main(int argc, char **argv) {
    char line[256];
    if (argc > 1 && !strcmp(argv[1], "threads")) {
        pthread_t t[8]; int i, live = 0;
        for (i = 0; i < 8; i++) pthread_create(&t[i], NULL, worker, (void *)(uintptr_t)(i + 1));
        for (i = 0; i < 8; i++) pthread_join(t[i], NULL);
        for (i = 0; i < KEYS; i++) live += table[i] != 0;
        printf("%d %ld %ld %ld %ld\n", live, made, released, inserted, lost);
        return 0;
    }
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long size, off, num; int shared, pf, bytes, uav, cut = 0; struct WMTTextureInfo ti; UINT64 aligned = 0, bpr = 0;
        if (sscanf(line, "%llu %d %d %d %llu %llu %d", &size, &shared, &pf, &bytes, &off, &num, &uav) != 7) continue;
        if (!mad_tv_plain_shape(size, shared, (UINT)pf, (UINT)bytes, off, num, uav, 64, &ti, &aligned, &bpr, &cut)) { printf("none\n"); continue; }
        printf("%llu %llu %u %d %d %d %d %u %u %u %d %u %u\n", (unsigned long long)aligned, (unsigned long long)bpr, ti.width, ti.usage,
               ti.options, ti.pixel_format, cut, ti.height, ti.depth, ti.array_length, ti.type, ti.mipmap_level_count, ti.sample_count);
    }
    return 0;
}
'''
    rnd = random.Random(657)
    cases = []
    sizes = [16, 64, 4096, 65536, 6291456, 13631488, 19922944, 67174400, 1040187392, (1 << 28) + 64, (1 << 32) + 4096]
    for _ in range(200000):
        size = rnd.choice(sizes) if rnd.random() < 0.8 else rnd.randrange(1, 1 << 33)
        bytes_ = rnd.choice([1, 2, 4, 8, 16])
        first = rnd.randrange(0, size // bytes_ + 8) if rnd.random() < 0.9 else rnd.randrange(0, 1 << 34)
        num = rnd.choice([0, 1, 3, 16, 255, 256, 1572864, 5597866, size // bytes_, 1 << 28, (1 << 28) + 1, rnd.randrange(0, 1 << 30)])
        cases.append((size, rnd.randrange(2), rnd.choice([53, 70, 73, 125]), bytes_, first * bytes_, num, rnd.randrange(2)))
    # the game's own views (the log's census): whole-buffer views of its frame buffers and of the 992 MB upload buffer
    cases += [(18874368, 1, 125, 16, 0, 1179648, 0), (1040187392, 1, 13, 1, 0, 1040187392, 0), (13631488, 0, 53, 4, 13631484, 1, 0),
              (64, 0, 53, 4, 64, 1, 0), (64, 0, 53, 4, 60, 4, 1), (4096, 1, 53, 4, 4092, 0, 0)]
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "t.c"
        src.write_text(prog)
        exe = pathlib.Path(td) / "t"
        r = subprocess.run([cc, "-O1", "-Wall", "-Wno-unused-function", "-pthread", "-o", str(exe), str(src)], capture_output=True, text=True)
        check("the shape helper compiles on the host from the source", r.returncode == 0, r.stderr[-1500:])
        if r.returncode == 0:
            inp = "".join("%d %d %d %d %d %d %d\n" % c for c in cases)
            out = subprocess.run([str(exe)], input=inp, capture_output=True, text=True).stdout.splitlines()
            bad = []
            none = 0
            for c, line in zip(cases, out):
                want = locked_shape(*c)
                if want is None:
                    none += 1
                    if line != "none":
                        bad.append((c, line, want))
                    continue
                g = line.split()
                if g == ["none"] or [int(x) for x in g[:7]] != list(want) or g[7:] != ["1", "1", "1", "9", "1", "1"]:
                    bad.append((c, line, want))
            check("the unlocked path's texture is the locked path's for %d ranges (%d of them have no view on either path)"
                  % (len(cases), none), len(out) == len(cases) and not bad and 0 < none < len(cases), bad[:3])
            cut = sum(1 for c in cases if locked_shape(*c) and locked_shape(*c)[2] == 1 << 28)
            ends = sum(1 for c in cases if locked_shape(*c) and locked_shape(*c)[6] and locked_shape(*c)[2] != 1 << 28)
            check("...among them views cut at Metal's 2^28-texel width and views cut at the end of their buffer (the cut flag is the same too)",
                  cut > 100 and ends > 100, (cut, ends))
            t = subprocess.run([str(exe), "threads"], capture_output=True, text=True).stdout.split()
            check("eight threads, 1.6 million lookups of 4,096 keys: every key has exactly one texture, every other one made was released",
                  len(t) == 5 and int(t[0]) == 4096 and int(t[3]) == 4096 and int(t[1]) - int(t[2]) == 4096 and int(t[2]) == int(t[4]), t)
else:
    print("note: no host C compiler; the shape and the insert model were not run")

# --- the whole runtime, for syntax, with the repo's toolchain -------------------------
mingw = R / "toolchains/llvm-mingw-20260421-ucrt-macos-universal/bin/arm64ec-w64-mingw32-clang"
winemetal = R / "dxmt/src/winemetal"
if mingw.exists() and winemetal.exists():
    r = subprocess.run([str(mingw), "-fsyntax-only", "-O2", "-Wall", '-DMAD_SC_CONVERTER_ID="0000000000000000"',
                        "-I" + str(R / "madeira-d3d12/src/pe"), "-I" + str(R / "madeira-d3d12/src"), "-I" + str(winemetal),
                        str(R / "madeira-d3d12/src/pe/madeira_d3d12.c")], capture_output=True, text=True)
    check("madeira_d3d12.c compiles (arm64ec, -fsyntax-only)", r.returncode == 0, r.stderr[-3000:])
else:
    print("note: llvm-mingw / dxmt not in this tree; the syntax compile runs in the full tree")

ci = (R / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-typed-view-unlocked.py" in ci)

print("PASS: d3d12-typed-view-unlocked" if ok else "FAILED: d3d12-typed-view-unlocked")
sys.exit(0 if ok else 1)
