#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""RG32Uint UAV textures get Metal's ShaderAtomic usage only on GPUs with 64-bit texture atomics (build 652; madeira-d3d12; night round 2026-11-11, G8 S2).

REANIMAL Demo (Send Log 3ETFH4, build 634, iPhone14,3 = A15 = Apple8), Oar'some Adventures Demo (0KN81P) and Red Dead Redemption 2 (2PGRBB), both build
626 on an M1 iPad (Apple7): Unreal's Nanite visibility buffer is an RG32Uint UAV texture (64-bit InterlockedMax), mad_texinfo_from_desc gave every
RG32Uint UAV texture WMTTextureUsageShaderAtomic, and Metal's descriptor validation ends the process on any GPU below Apple9:
    -[MTLTextureDescriptorInternal validateWithDevice:]:1421: failed assertion `Texture Descriptor Validation
    This device does not support ulong texture atomics, so MTLPixelFormatRG32Uint cannot be used with texture atomics'
Now RG32Uint gets the usage only when the GPU is Apple9 or later (the conversion target's family: mad_family_atomics64, the same test
OPTIONS9's answer uses under MADEIRA_D3D12_CAPS_BY_FAMILY; a Mac counts as above Apple9). R32Uint and R32Sint have 32-bit texture atomics on every
supported GPU and keep the usage everywhere. Apple9 and later GPUs get exactly the usage they got before.

Checks:
  1. the source: mad_texinfo_from_desc takes the device first; its three callers (ResolveSubresource's stand-in, GetResourceAllocationInfo, resource
     creation) pass theirs; the format test is R32Uint || R32Sint || (RG32Uint && mad_family_atomics64(d)); the family helper is unchanged;
  2. the real mad_texinfo_from_desc is cut out of madeira_d3d12.c and compiled on the host with fakes (format mapping, sample-count clamp, the
     family helper, Metal's enums): for every format of a table (R32Uint, R32Sint, RG32Uint, RG32Float, R32Float, RGBA8) with and without the UAV flag
     on an Apple8-class and an Apple9-class device, the usage is what the old code gave except RG32Uint on Apple8, which loses only ShaderAtomic;
     the rest of the description (sizes, mips, types, storage) is the same on both devices;
  3. the auto-tune rule (the line CAPS_BY_FAMILY, no fixedIn) and its fixture are there; CI runs this check.
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


SIG = ("static int mad_texinfo_from_desc(struct mad_device *d, const D3D12_RESOURCE_DESC *desc, struct WMTTextureInfo *ti, enum WMTPixelFormat *pf, "
       "int *is_depth) {")
fn = cut(src, SIG + "\n    UINT layers", "\n}\n") if SIG in src else ""

# ---- 1. the source
check("the definition and both declarations take the device first", fn != "" and src.count(SIG[:-2] + ";") == 2)
check("ResolveSubresource's stand-in texture, GetResourceAllocationInfo and resource creation pass their device",
      "mad_texinfo_from_desc(e->q->device, &td, &ti, &pf, &is_depth)" in src
      and "mad_texinfo_from_desc(md, d, &ti, &pf, &isd) && ti.width && ti.height" in src
      and "mad_texinfo_from_desc(d, desc, &ti, &pf, &is_depth)" in src
      and src.count("mad_texinfo_from_desc(") == 6)   # the definition, two declarations, three callers
check("the usage test: R32Uint, R32Sint, and RG32Uint only where mad_family_atomics64(d) says the GPU has 64-bit texture atomics",
      "if (ti->pixel_format == WMTPixelFormatR32Uint || ti->pixel_format == WMTPixelFormatR32Sint ||\n"
      "            (ti->pixel_format == WMTPixelFormatRG32Uint && mad_family_atomics64(d)))\n"
      "            ti->usage = (enum WMTTextureUsage)(ti->usage | WMTTextureUsageShaderAtomic);" in fn)
check("the family helper is what OPTIONS9 uses (Apple9 and later, a Mac above them)",
      "static int mad_family_atomics64(struct mad_device *d) {\n    mad_resolve_target(d);\n    return g_target.family >= MAD_IR_FAMILY_APPLE9;\n}" in src
      and "if (mad_caps_by_family() && !mad_family_atomics64((struct mad_device *)This))" in src)
check("the atomic usage is still inside the UAV branch (a texture without the UAV flag never gets it)",
      fn != "" and fn.index("D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS") < fn.index("WMTTextureUsageShaderAtomic") < fn.index("WMTTextureUsagePixelFormatView"))

# ---- 2. the real function on the host
harness = r'''
#include <stdio.h>
#include <string.h>
#include <stdint.h>
typedef unsigned UINT; typedef uint64_t UINT64; typedef uint16_t UINT16;
enum WMTPixelFormat { WMTPixelFormatRGBA8Unorm = 70, WMTPixelFormatR32Uint = 53, WMTPixelFormatR32Sint = 54, WMTPixelFormatR32Float = 55,
                      WMTPixelFormatRG32Uint = 103, WMTPixelFormatRG32Sint = 104, WMTPixelFormatRG32Float = 105 };
enum WMTTextureType { WMTTextureType2DArray = 3, WMTTextureType2DMultisampleArray = 8, WMTTextureType3D = 7 };
enum WMTTextureUsage { WMTTextureUsageShaderRead = 1, WMTTextureUsageShaderWrite = 2, WMTTextureUsageRenderTarget = 4, WMTTextureUsagePixelFormatView = 16,
                       WMTTextureUsageShaderAtomic = 32 };
enum { WMTResourceStorageModePrivate = 2 };
struct WMTTextureInfo { enum WMTPixelFormat pixel_format; uint32_t width, height, depth, array_length, mipmap_level_count, sample_count; enum WMTTextureType type;
                        enum WMTTextureUsage usage; uint32_t options; uint64_t gpu_resource_id; };
typedef enum { D3D12_RESOURCE_DIMENSION_UNKNOWN, D3D12_RESOURCE_DIMENSION_BUFFER, D3D12_RESOURCE_DIMENSION_TEXTURE1D, D3D12_RESOURCE_DIMENSION_TEXTURE2D,
               D3D12_RESOURCE_DIMENSION_TEXTURE3D } D3D12_RESOURCE_DIMENSION;
#define D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET 0x1u
#define D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL 0x2u
#define D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS 0x4u
typedef unsigned D3D12_RESOURCE_FLAGS; typedef unsigned DXGI_FORMAT;
typedef struct { D3D12_RESOURCE_DIMENSION Dimension; UINT64 Alignment; UINT64 Width; UINT Height; UINT16 DepthOrArraySize; UINT16 MipLevels; DXGI_FORMAT Format;
                 struct { UINT Count, Quality; } SampleDesc; unsigned Layout; D3D12_RESOURCE_FLAGS Flags; } D3D12_RESOURCE_DESC;
struct mad_device { int apple9; };
static int mad_family_atomics64(struct mad_device *d) { return d->apple9; }
static UINT mad_clamp_sample_count(UINT n) { return n; }
/* the format mapping, as far as the table below needs it (DXGI value = the Metal format's number here) */
static int mad_map_texture_format(DXGI_FORMAT f, D3D12_RESOURCE_FLAGS flags, enum WMTPixelFormat *out, int *is_depth)
{ (void)flags; *is_depth = 0; *out = (enum WMTPixelFormat)f; return 1; }
''' + fn + r'''
int main(void)
{
    static const enum WMTPixelFormat formats[] = { WMTPixelFormatR32Uint, WMTPixelFormatR32Sint, WMTPixelFormatRG32Uint, WMTPixelFormatRG32Float,
                                                   WMTPixelFormatR32Float, WMTPixelFormatRGBA8Unorm, WMTPixelFormatRG32Sint };
    static const char *names[] = { "R32Uint", "R32Sint", "RG32Uint", "RG32Float", "R32Float", "RGBA8", "RG32Sint" };
    struct mad_device a8 = { 0 }, a9 = { 1 };
    unsigned f, uav, dev;
    for (f = 0; f < sizeof formats / sizeof formats[0]; f++)
        for (uav = 0; uav < 2; uav++) {
            unsigned usage[2] = { 0, 0 }; int same = 1;
            struct WMTTextureInfo first;
            for (dev = 0; dev < 2; dev++) {
                D3D12_RESOURCE_DESC d; struct WMTTextureInfo ti; enum WMTPixelFormat pf; int dep;
                memset(&d, 0, sizeof d);
                d.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D; d.Width = 64; d.Height = 32; d.DepthOrArraySize = 3; d.MipLevels = 5; d.Format = (DXGI_FORMAT)formats[f];
                d.SampleDesc.Count = 1; d.Flags = uav ? D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS : 0;
                if (!mad_texinfo_from_desc(dev ? &a9 : &a8, &d, &ti, &pf, &dep)) { printf("%s: refused\n", names[f]); return 1; }
                usage[dev] = (unsigned)ti.usage;
                if (dev == 0) first = ti;
                else same = first.width == ti.width && first.height == ti.height && first.array_length == ti.array_length && first.mipmap_level_count == ti.mipmap_level_count
                            && first.type == ti.type && first.options == ti.options && first.pixel_format == ti.pixel_format && first.sample_count == ti.sample_count;
            }
            printf("%s uav=%u apple8=%u apple9=%u same-rest=%d\n", names[f], uav, usage[0], usage[1], same);
        }
    return 0;
}
'''
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the texture-info harness")
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "texinfo.c"
        c.write_text(harness)
        exe = Path(tmp) / "texinfo"
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the real mad_texinfo_from_desc compiles against fakes (-Wall)", fn != "" and b.returncode == 0 and "warning" not in b.stderr, b.stderr[-2500:])
        if fn and b.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60).stdout
            rows = {}
            for ln in out.splitlines():
                p = ln.split()
                if len(p) == 5:
                    rows[(p[0], int(p[1].split("=")[1]))] = (int(p[2].split("=")[1]), int(p[3].split("=")[1]), p[4])
            READ, WRITE, VIEW, ATOMIC = 1, 2, 16, 32
            base = READ | VIEW
            want = {}
            for name in ("R32Uint", "R32Sint", "RG32Uint", "RG32Float", "R32Float", "RGBA8", "RG32Sint"):
                want[(name, 0)] = (base, base)
                atomic32 = name in ("R32Uint", "R32Sint")
                a8 = base | WRITE | (ATOMIC if atomic32 else 0)
                a9 = base | WRITE | (ATOMIC if (atomic32 or name == "RG32Uint") else 0)
                want[(name, 1)] = (a8, a9)
            check("every format, with and without UAV, on an Apple8-class and an Apple9-class device: the usage is the old one, minus ShaderAtomic on RG32Uint below Apple9",
                  all(rows.get(k, (None, None, ""))[:2] == v for k, v in want.items()), "\n".join(f"{k}: got {rows.get(k)} want {v}" for k, v in want.items() if rows.get(k, (0, 0))[:2] != v))
            check("RG32Uint UAV: Apple8 has no ShaderAtomic (the Metal abort), Apple9 has it (unchanged)",
                  rows[("RG32Uint", 1)][0] & ATOMIC == 0 and rows[("RG32Uint", 1)][1] & ATOMIC == ATOMIC and rows[("RG32Uint", 1)][0] & WRITE == WRITE)
            check("R32Uint and R32Sint UAVs keep ShaderAtomic on both", all(rows[(n, 1)][0] & ATOMIC and rows[(n, 1)][1] & ATOMIC for n in ("R32Uint", "R32Sint")))
            check("nothing else of the description depends on the device", all(v[2] == "same-rest=1" for v in rows.values()), out[:600])

# ---- 3. rule, fixture, CI
rules = (root / "build/tools/autotune_rules.py").read_text()
r0 = rules.find('"id": "d3d12-ulong-texture-atomics"')
rb = rules[r0:rules.index("\n    },\n", r0)] if r0 >= 0 else ""
check("the auto-tune rule adds the CAPS_BY_FAMILY line (no fixedIn: the line is still the remedy for what comes after) and its fixture is there",
      '"lines": ["env.MADEIRA_D3D12_CAPS_BY_FAMILY = 1"]' in rb and '"fixedIn"' not in rb and '"noFix"' not in rb
      and (root / "tests/fixtures/autotune/reanimal-ulong-texture-atomics-2026-11-09.expect").is_file())
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-texinfo-atomics.py" in ci)

print("PASS: d3d12 texture-info atomics" if ok else "FAILED: d3d12 texture-info atomics")
sys.exit(0 if ok else 1)
