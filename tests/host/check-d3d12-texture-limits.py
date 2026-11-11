#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""A D3D12 texture beyond Metal's limits is refused (E_INVALIDARG), not handed to Metal (build 652; madeira-d3d12; night round 2026-11-11, G10 S9).

Shadow of the Tomb Raider (25PM6N and CEES6A, build 600, an Apple A19 GPU) asks, right after its swap chain and first descriptor heaps, for a texture whose
height is 0x80000300, and Metal's descriptor validation ends the whole process:
    -[MTLTextureDescriptorInternal validateWithDevice:]:1421: failed assertion `Texture Descriptor Validation
    MTLTextureDescriptor has height (2147484416) greater than the maximum allowed size of 32768.'
(exception 80000101). Direct3D 12 refuses such a description with E_INVALIDARG, as madeira-d3d12 already does for a zero extent (build 580). The limits are
Metal's: a 2D / 1D texture edge of 16384 (the A15 prints "maximum allowed size of 16384"; every Apple GPU BCD runs on but Apple10 is taken as that) or
32768 on an Apple10 GPU (A19, A19 Pro, M5: the A19 log prints 32768), a 3D texture 2048 on each edge, an array 2048 slices. Only a description Metal aborts on
reaches the new return; the GPU is asked for its limit only when a texture is past 16384, and the answer is remembered.

Checks:
  1. the source: the refusal in mad_create_resource_at follows the zero-extent refusal and precedes every Metal call of the texture branch (the placed heap
     size query, the heap texture, newTexture), frees the half-built resource, logs through mad_refuse_log (the sizes are in that line) and returns
     E_INVALIDARG; GetResourceAllocationInfo does not hand such a description to Metal's size query; the prototype comes before its first use;
  2. the real mad_metal_max_edge and mad_texinfo_over_limits are cut out of madeira_d3d12.c and compiled on the host with a fake device (Apple10 or not,
     counting the family queries): every size inside a GPU's limits (a grid of edges up to the limit, array lengths up to 2048, 3D edges up to 2048) is
     NOT refused on either GPU; 16385 and 32768 / 32769 edges, 0x80000300, 3D edges and array lengths of 2049 are; the family is asked only for a texture past
     16384 and only once;
  3. the auto-tune rule is fixed in 652 with its fixture; CI runs this check.
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


def ordered(text, *needles):
    at = [text.find(n) for n in needles]
    return all(a >= 0 for a in at) and at == sorted(at)


# ---- 1. the source
fn_start = src.find("static HRESULT mad_create_resource_at(struct mad_device *d, D3D12_HEAP_TYPE heap_type,\n"
                    "                                      const D3D12_RESOURCE_DESC *desc, REFIID riid, void **out,\n"
                    "                                      struct mad_memheap *ph, UINT64 poff) {")
fn = src[fn_start:src.index("\n}\n", fn_start)] if fn_start >= 0 else ""
tex = fn[fn.index("if (desc->Dimension != D3D12_RESOURCE_DIMENSION_BUFFER) {"):] if fn else ""
guard_at = tex.find("if (mad_texinfo_over_limits(d, &ti, &edge)) {")
guard = tex[guard_at:tex.index("\n            }\n", guard_at)] if guard_at >= 0 else ""
check("the refusal follows the zero-extent refusal and precedes every Metal call of the texture branch",
      ordered(tex, "if (!ti.width || !ti.height)", "if (mad_texinfo_over_limits(d, &ti, &edge)) {", "MTLDevice_heapTextureSizeAndAlign")
      and guard_at < tex.find("MTLDevice_newTexture") and guard_at < tex.find("MTLHeap_newTextureAtOffset"))
check("it frees the half-built resource, logs the sizes through mad_refuse_log and returns E_INVALIDARG",
      "free(r); mad_refuse_log(desc, heap_type, why); return E_INVALIDARG;" in guard and "texture beyond Metal's limits: %ux%ux%u, array %u, limit %u per edge" in guard)
ai = src[src.index("device_GetResourceAllocationInfo("):]
ai = ai[:ai.index("\n}\n")]
check("GetResourceAllocationInfo asks Metal's size query only for a texture inside the limits (and logs the other, four lines at most)",
      "if (!mad_texinfo_over_limits(md, &ti, &edge))" in ai and ai.index("mad_texinfo_over_limits(md, &ti, &edge)") < ai.index("MTLDevice_heapTextureSizeAndAlign")
      and "if (InterlockedIncrement(&said_limits) <= 4)" in ai)
check("the prototype of mad_texinfo_over_limits comes before its first use (GetResourceAllocationInfo) and its definition before the creation",
      src.index("static int mad_texinfo_over_limits(struct mad_device *d, const struct WMTTextureInfo *ti, UINT *edge);") < src.index("device_GetResourceAllocationInfo(")
      and src.index("static int mad_texinfo_over_limits(struct mad_device *d, const struct WMTTextureInfo *ti, UINT *edge) {") < fn_start)

# ---- 2. the real helpers on the host
helpers = (cut(src, "static UINT mad_metal_max_edge(struct mad_device *d) {", "\n}\n") + "\n"
           + cut(src, "static int mad_texinfo_over_limits(struct mad_device *d, const struct WMTTextureInfo *ti, UINT *edge) {", "\n}\n"))
fam = [ln for ln in src.splitlines() if ln.startswith("#define MAD_MTL_FAMILY_APPLE10_FV")][0]
harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned UINT; typedef long LONG; typedef uint64_t UINT64;
enum WMTTextureType { WMTTextureType2DArray = 3, WMTTextureType3D = 7, WMTTextureType2DMultisampleArray = 8 };
struct WMTTextureInfo { uint32_t width, height, depth, array_length; enum WMTTextureType type; };
struct mad_device { int apple10; };
static int queries;
''' + fam + r'''
static int MTLDevice_supportsFamily(struct mad_device *dev, int family) { queries++; return family == MAD_MTL_FAMILY_APPLE10_FV && dev->apple10; }
''' + "\n#define mtl_device_cast(d) (d)\n" + helpers.replace("d->mtl_device", "d") + r'''
static const char *kind(int t) { return t == WMTTextureType3D ? "3D" : "2D"; }
int main(int argc, char **argv)
{
    struct mad_device dev = { argc > 1 && !strcmp(argv[1], "apple10") };
    static const uint32_t edges[] = { 1, 2, 255, 256, 4096, 8192, 16383, 16384 };
    static const uint32_t layers[] = { 1, 6, 2047, 2048 };
    unsigned i, j, k, wrongly_refused = 0, tested = 0; UINT edge;
    for (i = 0; i < sizeof edges / sizeof edges[0]; i++)
        for (j = 0; j < sizeof edges / sizeof edges[0]; j++)
            for (k = 0; k < sizeof layers / sizeof layers[0]; k++) {
                struct WMTTextureInfo ti = { edges[i], edges[j], 1, layers[k], WMTTextureType2DArray };
                tested++; if (mad_texinfo_over_limits(&dev, &ti, &edge)) wrongly_refused++;
            }
    for (i = 0; i < 4; i++) for (j = 0; j < 4; j++) for (k = 0; k < 4; k++) {   /* 3D: every edge up to 2048 */
        static const uint32_t e3[] = { 1, 64, 1024, 2048 };
        struct WMTTextureInfo ti = { e3[i], e3[j], e3[k], 1, WMTTextureType3D };
        tested++; if (mad_texinfo_over_limits(&dev, &ti, &edge)) wrongly_refused++;
    }
    printf("inside the limits: %u textures, %u refused, %d family queries\n", tested, wrongly_refused, queries);
    {
        static const struct { const char *what; enum WMTTextureType t; uint32_t w, h, d, a; } t[] = {
            { "16385 wide 2D", WMTTextureType2DArray, 16385, 64, 1, 1 }, { "16385 high 2D", WMTTextureType2DArray, 64, 16385, 1, 1 },
            { "32768 square 2D", WMTTextureType2DArray, 32768, 32768, 1, 1 }, { "32769 wide 2D", WMTTextureType2DArray, 32769, 8, 1, 1 },
            { "Danganronpa 32767 wide", WMTTextureType2DArray, 32767, 32767, 1, 1 }, { "SOTTR height 0x80000300", WMTTextureType2DArray, 1280, 0x80000300u, 1, 1 },
            { "width 0x80000380", WMTTextureType2DArray, 0x80000380u, 720, 1, 1 }, { "array 2049", WMTTextureType2DArray, 64, 64, 1, 2049 },
            { "array 65535", WMTTextureType2DArray, 4, 4, 1, 65535 }, { "msaa array 4096", WMTTextureType2DMultisampleArray, 64, 64, 1, 4096 },
            { "3D 2049 wide", WMTTextureType3D, 2049, 16, 16, 1 }, { "3D 2049 deep", WMTTextureType3D, 16, 16, 2049, 1 },
            { "3D 4096 high", WMTTextureType3D, 16, 4096, 16, 1 }, { "3D 0x80000300 high", WMTTextureType3D, 16, 0x80000300u, 16, 1 },
        };
        for (i = 0; i < sizeof t / sizeof t[0]; i++) {
            struct WMTTextureInfo ti = { t[i].w, t[i].h, t[i].d, t[i].a, t[i].t };
            int r = mad_texinfo_over_limits(&dev, &ti, &edge);
            printf("%s: refused=%d edge=%u\n", t[i].what, r, edge);
        }
        {   /* the Apple10-only band: 16385 .. 32768 */
            struct WMTTextureInfo ti = { 20000, 20000, 1, 1, WMTTextureType2DArray };
            int r = mad_texinfo_over_limits(&dev, &ti, &edge);
            printf("20000 square 2D: refused=%d edge=%u\n", r, edge);
            ti.width = 32768; ti.height = 1; r = mad_texinfo_over_limits(&dev, &ti, &edge);
            printf("32768x1 2D: refused=%d edge=%u\n", r, edge);
        }
    }
    printf("family queries in all: %d\n", queries);
    return 0;
}
'''
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the texture-limit harness")
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "lim.c"
        c.write_text(harness)
        exe = Path(tmp) / "lim"
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the real mad_metal_max_edge and mad_texinfo_over_limits compile against a fake device (-Wall)", b.returncode == 0 and "warning" not in b.stderr, b.stderr[-2500:])
        if b.returncode == 0:
            res = {}
            for gpu in ("apple8", "apple10"):
                res[gpu] = subprocess.run([str(exe), gpu], capture_output=True, text=True, timeout=60).stdout
            for gpu, out in res.items():
                check(f"{gpu}: no texture inside the GPU's limits is refused, and none of them asks the device for its family",
                      "inside the limits: 320 textures, 0 refused, 0 family queries" in out, out[:400])
            for gpu, edge in (("apple8", 16384), ("apple10", 32768)):
                out = res[gpu]
                # past 16384 on the A15-class GPU: refused at 16384; on Apple10: refused only past 32768
                want = {
                    "16385 wide 2D": (edge < 16385, edge), "16385 high 2D": (edge < 16385, edge), "32768 square 2D": (edge < 32768, edge),
                    "32769 wide 2D": (1, edge), "Danganronpa 32767 wide": (edge < 32767, edge), "SOTTR height 0x80000300": (1, edge),
                    "width 0x80000380": (1, edge), "20000 square 2D": (edge < 20000, edge), "32768x1 2D": (edge < 32768, edge),
                }
                bad = [k for k, (r, e) in want.items() if f"{k}: refused={int(r)} edge={e}" not in out]
                check(f"{gpu}: the limit is {edge} per 2D edge (16385 refused only below Apple10, 32768 accepted only on Apple10, 32769 and the SOTTR 0x80000300 always)", not bad, f"{bad}\n{out}")
                bad = [k for k, r in (("array 2049", 1), ("array 65535", 1), ("msaa array 4096", 1), ("3D 2049 wide", 1), ("3D 2049 deep", 1), ("3D 4096 high", 1),
                                      ("3D 0x80000300 high", 1)) if f"{k}: refused={r} " not in out]
                check(f"{gpu}: an array past 2048 slices and a 3D texture past 2048 on any edge are refused", not bad, f"{bad}\n{out}")
                check(f"{gpu}: the family is asked only for a texture past 16384 and only once (at most 1 query in all)",
                      "family queries in all: 1" in out, out[-200:])

# ---- 3. rule, fixture, CI
rules = (root / "build/tools/autotune_rules.py").read_text()
r0 = rules.find('"id": "d3d12-texture-beyond-metal-limits"')
rb = rules[r0:rules.index("\n    },\n", r0)] if r0 >= 0 else ""
check("the auto-tune rule is fixed in 652 (no line, no noFix), Direct3D 12 only, and its fixture is there",
      '"fixedIn": 652' in rb and '"lines": []' in rb and '"noFix"' not in rb and '"api": "d3d12"' in rb
      and (root / "tests/fixtures/autotune/shadow-of-the-tomb-raider-texture-limit-2026-10-26.expect").is_file())
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-texture-limits.py" in ci)

print("PASS: d3d12 texture limits" if ok else "FAILED: d3d12 texture limits")
sys.exit(0 if ok else 1)
