#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""Uncompressed <-> block-compressed texture copies (build 666; issue #35, skate.; madeira-d3d12/src/pe/madeira_d3d12.c).

skate. writes BC blocks from a compute shader into an R32G32B32A32_UINT texture and copies it into BC3/BC5/BC6H textures with
CopyTextureRegion; D3D12 allows it (one source texel the size of one destination block), Metal's texture-to-texture blit does not
reinterpret, and every such texture came out black. exec_copy_reinterpret copies through the staging buffer instead. Checks:
  - the copy step calls it only after the depth/stencil branch and only when the two Metal formats differ, and falls back to the
    plain blit whenever it answers 0;
  - the production function and mad_format_info, compiled with stubs: RGBA32_UINT 64x64 -> BC3 256x256 (texel to block), BC3 ->
    RGBA32_UINT (block to texel), R32G32_UINT -> BC1, BC4 -> R32G32_UINT, a destination region clipped to a small mip, an offset
    destination, an array slice and a 3D depth; NOT taken (0, the plain blit) for the same block shape, different sizes (R32_UINT
    with BC1), a depth or stencil plane, t2t-reinterp = 0, or a GPU without BC sampling; a destination origin outside the mip
    is skipped;
  - the catalog documents t2t-reinterp (default 1); CI runs this check.
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
            print("     " + str(detail)[:2500])


def function(start):
    a = src.index(start)
    b = src.index("{", a)
    depth, c = 1, b + 1
    while depth:
        depth += (src[c] == "{") - (src[c] == "}")
        c += 1
    return src[a:c]


case = src[src.index("    case MC_COPY_T2T: {\n        struct wmtcmd_blit_copy_from_texture_to_texture k;"):]
case = case[:case.index("\n    }\n")]
call = "if (c->u.tt.src->tex_pf != c->u.tt.dst->tex_pf && exec_copy_reinterpret(e, c)) return;"
check("the copy step: after the depth/stencil branch, only for two different Metal formats, the plain blit when it answers 0",
      case.count(call) == 1 and case.index("exec_copy_aspect(e, c);") < case.index(call) < case.index("k.type = WMTBlitCommandCopyFromTextureToTexture;"))
check("the catalog documents t2t-reinterp (default 1)",
      re.search(r'"t2t-reinterp": \{[^}]*"default": "1"', (root / "build/tools/gen-config-catalog.py").read_text()) is not None)
check("CI runs this check", "python3 tests/host/check-d3d12-t2t-reinterp.py" in (root / ".github/workflows/build-ipa.yml").read_text())

head = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef uint64_t UINT64; typedef unsigned UINT; typedef uint32_t UINT32; typedef long LONG; typedef void *obj_handle_t;
typedef struct { int unused; } CRITICAL_SECTION;
typedef enum DXGI_FORMAT {
    DXGI_FORMAT_UNKNOWN = 0, DXGI_FORMAT_R32G32B32A32_TYPELESS = 1, DXGI_FORMAT_R32G32B32A32_UINT = 3, DXGI_FORMAT_R32G32B32A32_SINT = 4,
    DXGI_FORMAT_R32G32B32_TYPELESS = 5, DXGI_FORMAT_R32G32B32_SINT = 8, DXGI_FORMAT_R16G16B16A16_TYPELESS = 9, DXGI_FORMAT_R32G32_UINT = 17,
    DXGI_FORMAT_R32G32_SINT = 18, DXGI_FORMAT_R32G8X24_TYPELESS = 19, DXGI_FORMAT_X32_TYPELESS_G8X24_UINT = 22,
    DXGI_FORMAT_R10G10B10A2_TYPELESS = 23, DXGI_FORMAT_R32_UINT = 42, DXGI_FORMAT_X24_TYPELESS_G8_UINT = 47, DXGI_FORMAT_R8G8_TYPELESS = 48,
    DXGI_FORMAT_R16_SINT = 59, DXGI_FORMAT_R8_TYPELESS = 60, DXGI_FORMAT_A8_UNORM = 65, DXGI_FORMAT_R9G9B9E5_SHAREDEXP = 67,
    DXGI_FORMAT_R8G8_B8G8_UNORM = 68, DXGI_FORMAT_G8R8_G8B8_UNORM = 69, DXGI_FORMAT_BC1_TYPELESS = 70, DXGI_FORMAT_BC1_UNORM = 71,
    DXGI_FORMAT_BC1_UNORM_SRGB = 72, DXGI_FORMAT_BC2_TYPELESS = 73, DXGI_FORMAT_BC3_UNORM = 77, DXGI_FORMAT_BC3_UNORM_SRGB = 78,
    DXGI_FORMAT_BC4_TYPELESS = 79, DXGI_FORMAT_BC4_UNORM = 80, DXGI_FORMAT_BC4_SNORM = 81, DXGI_FORMAT_BC5_TYPELESS = 82,
    DXGI_FORMAT_BC5_UNORM = 83, DXGI_FORMAT_BC5_SNORM = 84, DXGI_FORMAT_B5G6R5_UNORM = 85, DXGI_FORMAT_B5G5R5A1_UNORM = 86,
    DXGI_FORMAT_B8G8R8A8_UNORM = 87, DXGI_FORMAT_B8G8R8X8_UNORM_SRGB = 93, DXGI_FORMAT_BC6H_TYPELESS = 94, DXGI_FORMAT_BC6H_UF16 = 95,
    DXGI_FORMAT_BC7_UNORM = 98, DXGI_FORMAT_BC7_UNORM_SRGB = 99
} DXGI_FORMAT;
enum { WMTBlitCommandCopyFromTextureToBuffer = 11, WMTBlitCommandCopyFromBufferToTexture = 12, WMTResourceStorageModePrivate = 32 };
struct WMTOrigin { uint64_t x, y, z; };
struct WMTSize { uint64_t width, height, depth; };
struct WMTBufferInfo { uint64_t length; int options; struct { void *ptr; } memory; uint64_t gpu_address; };
struct wmtcmd_base { int type; };
struct wmtcmd_blit_copy_from_texture_to_buffer { int type; obj_handle_t src; uint32_t slice, level; struct WMTOrigin origin; struct WMTSize size;
    obj_handle_t dst; uint64_t offset; uint32_t bytes_per_row, bytes_per_image, options, reserved2; };
struct wmtcmd_blit_copy_from_buffer_to_texture { int type; uint16_t reserved[3]; obj_handle_t src; uint64_t src_offset; uint32_t bytes_per_row,
    bytes_per_image; struct WMTSize size; obj_handle_t dst; uint32_t slice, level; struct WMTOrigin origin; };
struct mad_device { CRITICAL_SECTION heap_lock; obj_handle_t ds_scratch; UINT64 ds_scratch_size; obj_handle_t mtl_device; };
struct mad_queue { struct mad_device *device; };
struct mad_exec { struct mad_queue *q; obj_handle_t benc; unsigned skipped; int ended, begun; };
struct mad_resource { struct { DXGI_FORMAT Format; } desc; obj_handle_t texture; unsigned serial; UINT width, height, depth3; };
struct mad_cmd { union { struct { struct mad_resource *src, *dst; UINT sx, sy, sz, w, h, d, dx, dy, dz, slevel, sslice, splane,
    dlevel, dslice, dplane; } tt; } u; };
static void EnterCriticalSection(CRITICAL_SECTION *c) { (void)c; }
static void LeaveCriticalSection(CRITICAL_SECTION *c) { (void)c; }
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static void d3d12_log(const char *fmt, ...) { (void)fmt; }
static long long cfg = 1; static int bc_native = 1, cfg_reads;
static long long mad_cfg_int_pe(const char *key, long long dflt) { (void)dflt; if (strcmp(key, "t2t-reinterp")) abort(); cfg_reads++; return cfg; }
static int MTLDevice_supportsBCTextureCompression(obj_handle_t d) { (void)d; return bc_native; }
static obj_handle_t MTLDevice_newBuffer(obj_handle_t d, struct WMTBufferInfo *bi) { (void)d; return bi->length ? (obj_handle_t)0x5c : 0; }
static void NSObject_release(obj_handle_t o) { (void)o; }
static struct wmtcmd_blit_copy_from_texture_to_buffer K1; static struct wmtcmd_blit_copy_from_buffer_to_texture K2; static int ncmd;
static void MTLBlitCommandEncoder_encodeCommands(obj_handle_t enc, const struct wmtcmd_base *k) {
    (void)enc;
    if (k->type == WMTBlitCommandCopyFromTextureToBuffer) K1 = *(const struct wmtcmd_blit_copy_from_texture_to_buffer *)k;
    else if (k->type == WMTBlitCommandCopyFromBufferToTexture) K2 = *(const struct wmtcmd_blit_copy_from_buffer_to_texture *)k;
    else abort();
    ncmd++;
}
static void exec_end(struct mad_exec *e) { e->ended++; }
static int exec_begin_blit(struct mad_exec *e) { e->begun++; return 1; }
#define MAD_SKIP(e_) ((e_)->skipped++)
static void mad_mip_dims(const struct mad_resource *r, UINT level, UINT *w, UINT *h, UINT *d) {
    *w = r->width >> level ? r->width >> level : 1; *h = r->height >> level ? r->height >> level : 1; *d = r->depth3 ? r->depth3 : 1;
}
"""
info = function("static void mad_format_info(DXGI_FORMAT f, UINT *bytes, UINT *block) {")
fn = src[src.index("static int g_t2t_reinterp = -1;"):]
fn = fn[:fn.index("\n}\n") + 3]
tests = r"""
static int fails;
#define T(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
static struct mad_device dev; static struct mad_queue q = { &dev }; static struct mad_exec ex = { &q };
static struct mad_resource S, D; static struct mad_cmd C;
static int run(DXGI_FORMAT sf, UINT swid, UINT shei, DXGI_FORMAT df, UINT dwid, UINT dhei) {
    memset(&K1, 0, sizeof K1); memset(&K2, 0, sizeof K2); ncmd = 0; ex.skipped = ex.ended = ex.begun = 0;
    S.desc.Format = sf; S.width = swid; S.height = shei; S.texture = (obj_handle_t)0x51; S.serial = 1;
    D.desc.Format = df; D.width = dwid; D.height = dhei; D.texture = (obj_handle_t)0xd1; D.serial = 2;
    C.u.tt.src = &S; C.u.tt.dst = &D;
    return exec_copy_reinterpret(&ex, &C);
}
static void region(UINT w, UINT h, UINT d) { memset(&C, 0, sizeof C); C.u.tt.w = w; C.u.tt.h = h; C.u.tt.d = d; }
int main(void) {
    /* skate.: RGBA32_UINT 64x64 into BC3 256x256 (one texel, one block) */
    region(64, 64, 1);
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC3_UNORM, 256, 256) == 1 && ncmd == 2 && ex.ended == 1 && ex.begun == 1 && !ex.skipped);
    T(K1.size.width == 64 && K1.size.height == 64 && K1.size.depth == 1 && K1.bytes_per_row == 1024 && K1.bytes_per_image == 65536 && K1.src == (obj_handle_t)0x51 && K1.dst == (obj_handle_t)0x5c && K1.options == 0);
    T(K2.size.width == 256 && K2.size.height == 256 && K2.bytes_per_row == 1024 && K2.bytes_per_image == 65536 && K2.dst == (obj_handle_t)0xd1 && K2.src == (obj_handle_t)0x5c);
    T(dev.ds_scratch_size >= 65536);
    /* BC5 and BC6H the same way; BC7 too */
    region(64, 64, 1); T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC5_UNORM, 256, 256) == 1 && K2.size.width == 256);
    region(64, 64, 1); T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC6H_UF16, 256, 256) == 1 && K2.size.width == 256);
    /* the other way: BC3 256x256 into RGBA32_UINT 64x64 */
    region(256, 256, 1);
    T(run(DXGI_FORMAT_BC3_UNORM, 256, 256, DXGI_FORMAT_R32G32B32A32_UINT, 64, 64) == 1 && K1.size.width == 256 && K1.size.height == 256
      && K1.bytes_per_row == 1024 && K1.bytes_per_image == 65536 && K2.size.width == 64 && K2.size.height == 64 && K2.bytes_per_row == 1024);
    /* 8-byte pairs: R32G32_UINT <-> BC1 / BC4 */
    region(16, 8, 1); T(run(DXGI_FORMAT_R32G32_UINT, 16, 8, DXGI_FORMAT_BC1_UNORM, 64, 32) == 1 && K1.bytes_per_row == 128 && K2.size.width == 64 && K2.size.height == 32);
    region(64, 32, 1); T(run(DXGI_FORMAT_BC4_UNORM, 64, 32, DXGI_FORMAT_R32G32_UINT, 16, 8) == 1 && K1.bytes_per_row == 128 && K2.size.width == 16 && K2.size.height == 8);
    /* a small mip: the destination region is clipped to it (mip 7 of 256 = 2x2; one block) */
    region(1, 1, 1); C.u.tt.dlevel = 7;
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 1, 1, DXGI_FORMAT_BC3_UNORM, 256, 256) == 1 && K2.size.width == 2 && K2.size.height == 2 && K2.level == 7 && K2.bytes_per_row == 16);
    /* a destination offset and a partial block row at the edge of an odd source (BC 6x6 -> 2x2 texels) */
    region(6, 6, 1); T(run(DXGI_FORMAT_BC3_UNORM, 6, 6, DXGI_FORMAT_R32G32B32A32_UINT, 2, 2) == 1 && K1.bytes_per_row == 32 && K2.size.width == 2 && K2.size.height == 2);
    region(32, 32, 1); C.u.tt.dx = 128; C.u.tt.dy = 192;
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC3_UNORM, 256, 256) == 1 && K2.origin.x == 128 && K2.origin.y == 192 && K2.size.width == 128 && K2.size.height == 64);
    /* slices and 3D depth go through */
    region(16, 16, 4); C.u.tt.dslice = 3; C.u.tt.sslice = 2;
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 16, 16, DXGI_FORMAT_BC7_UNORM, 64, 64) == 1 && K1.slice == 2 && K2.slice == 3 && K1.size.depth == 4 && K2.size.depth == 4);
    /* an origin outside the destination mip: skipped, no command */
    region(16, 16, 1); C.u.tt.dx = 64;
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 16, 16, DXGI_FORMAT_BC3_UNORM, 64, 64) == 1 && ncmd == 0 && ex.skipped == 1);
    /* NOT this case: the plain blit (0) */
    region(64, 64, 1); T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_R32G32B32A32_UINT, 64, 64) == 0 && ncmd == 0);
    region(64, 64, 1); T(run(DXGI_FORMAT_BC1_UNORM, 64, 64, DXGI_FORMAT_BC3_UNORM, 64, 64) == 0 && ncmd == 0);
    region(64, 64, 1); T(run(DXGI_FORMAT_R32_UINT, 64, 64, DXGI_FORMAT_BC1_UNORM, 256, 256) == 0 && ncmd == 0);
    region(64, 64, 1); T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC1_UNORM, 256, 256) == 0 && ncmd == 0);
    region(64, 64, 1); C.u.tt.splane = 1; T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC3_UNORM, 256, 256) == 0 && ncmd == 0);
    /* the switch and the GPU are read once: off, or a GPU without BC sampling, is the plain blit */
    T(cfg_reads == 1);
    g_t2t_reinterp = -1; cfg = 0; region(64, 64, 1);
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC3_UNORM, 256, 256) == 0 && ncmd == 0);
    g_t2t_reinterp = -1; cfg = 1; bc_native = 0; region(64, 64, 1);
    T(run(DXGI_FORMAT_R32G32B32A32_UINT, 64, 64, DXGI_FORMAT_BC3_UNORM, 256, 256) == 0 && ncmd == 0);
    printf(fails ? "HARNESS FAILED\n" : "harness ok\n");
    return fails != 0;
}
"""
cc = shutil.which("cc") or shutil.which("clang")
with tempfile.TemporaryDirectory(prefix="bcd-t2t-") as tmp:
    c = Path(tmp) / "t.c"
    exe = Path(tmp) / "t"
    c.write_text(head + info + "\n" + fn + tests)
    b = subprocess.run([cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Werror", "-Wno-unused-function", "-fsanitize=address,undefined",
                        "-fno-omit-frame-pointer", str(c), "-o", str(exe)], capture_output=True, text=True)
    check("the production copy and mad_format_info compile on the host", b.returncode == 0, b.stderr)
    if b.returncode == 0:
        r = subprocess.run([str(exe)], capture_output=True, text=True, env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"))
        check("texel to block and back, 8- and 16-byte pairs, clipping, offsets, slices; the plain blit for every other case",
              r.returncode == 0 and r.stdout.strip().endswith("harness ok"), r.stdout + r.stderr)
print("PASS: d3d12 t2t-reinterp" if ok else "FAILED: d3d12 t2t-reinterp")
sys.exit(0 if ok else 1)
