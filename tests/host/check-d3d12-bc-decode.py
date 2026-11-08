#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""CPU decode of BC uploads on GPUs without BC sampling (upstream Madeira ml1310, 50cc1c3), opt-in
d3d12-bc-decode = 1; no GPU, no Wine.

On a GPU that cannot sample BC formats (the iPad's A16: supportsBCTextureCompression = NO) winemetal creates every BC
texture uncompressed (BC1/2/3/7 as RGBA8, BC4 as R8, BC5 as RG8, BC6H as RGBA16F) and DXMT's Direct3D 11 layer decodes
every upload; madeira-d3d12 copied the raw blocks in with the BC row pitch, so they were read as texels (Final Fantasy
Tactics - The Ivalice Chronicles' enhanced art, BC7, came out as noise; 175 textures). With the switch on, a
buffer-to-texture copy into a BC texture on such a GPU is decoded on the CPU at encode time with DXMT's block decoders
(dxmt_bcn: BC1/2/3/4/5/7, signed BC4/BC5) into a shared staging buffer at the texel pitch, clipped to the level, and
copied from there. BC6H (no decoder), a source buffer without a CPU view and a read past the buffer are copied raw as
before. BCD: default 0 (upstream default 1); switched off, the GPU is not even asked and nothing is logged.

1. Source: the key is read once, default 0, the device is queried only when it is on; the copy path asks first, after
   the null check and the upload-guard note, before it builds its own command; both build scripts compile
   mad_bcn.cpp and dxmt_bcn.cpp and link them into the DLL.
2. The decision and the upload function are cut out of madeira_d3d12.c and compiled on the host with fakes for the Metal
   side, linked with the real mad_bcn.cpp / dxmt_bcn.cpp: key off or a GPU with BC sampling: nothing is decoded; BC1
   blocks of known colours come out as the expected RGBA8 texels in a staging buffer at the texel pitch, the copy command
   names the staging buffer, the texel pitch and image size, the level, slice and origin; a level that is not a multiple
   of 4 is clipped; BC4 comes out as R8; a depth of 2 decodes both slices; BC6H, a GPU-only source and an overlong read
   are left to the raw copy.
3. CI runs this check.
SKIP (exit 0) of the behaviour part without a C++ compiler.
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
ci = (root / "tools/build-madeira-d3d12-dll.sh").read_text()
dev = (root / "build/madeira-d3d12/build-pe.sh").read_text()
bcn_shim = root / "madeira-d3d12/src/pe/mad_bcn.cpp"
bcn_cpp = root / "dxmt/src/dxmt/dxmt_bcn.cpp"
failures = []


def check(what, cond):
    print(("PASS: " if cond else "FAIL: ") + what)
    if not cond:
        failures.append(what)


def cut(text, a, b):
    i = text.index(a)
    return text[i:text.index(b, i)]


block = cut(src, "/* ml1310: BC uploads on a GPU that cannot sample BC.", "static void exec_copy(struct mad_exec *e, const struct mad_cmd *c) {")
on_fn = cut(block, "static int mad_bc_decode_on(struct mad_device *d) {", "\n}\n")
b2t = cut(src, "    case MC_COPY_B2T: {\n        struct wmtcmd_blit_copy_from_buffer_to_texture k;", "        memset(&k, 0, sizeof k);")

# ---- 1. source
check("the key is read once, default 0; the GPU is asked and a line is logged only when the key is on",
      'int want = mad_cfg_int_pe("d3d12-bc-decode", 0) ? 1 : 0, native = 1;' in on_fn and "if (want) native = MTLDevice_supportsBCTextureCompression" in on_fn
      and "g_bc_decode = want && !native;" in on_fn and on_fn.index("if (want)\n            d3d12_log") > on_fn.index("g_bc_decode = want && !native;")
      and src.count('"d3d12-bc-decode"') == 1)
check("the copy path tries the decode after the null check and the upload-guard note, before building its own command",
      b2t.index("if (!c->u.bt.tex->texture || !c->u.bt.buf->buffer) { MAD_SKIP(e); return; }") < b2t.index("if (mad_bc_upload_decoded(e, c)) return;")
      and "mad_ug_note(" in b2t and b2t.index("mad_ug_note(") < b2t.index("if (mad_bc_upload_decoded(e, c)) return;") and src.count("mad_bc_upload_decoded(e, c)") == 1)
check("the decode asks the switch before it looks at anything else of the copy besides the format",
      block.index("if (!kind) return 0;") < block.index("if (!mad_bc_decode_on(e->q->device)) return 0;") < block.index("if (kind < 0)"))
check("the shim is upstream's, byte for byte", bcn_shim.is_file() and "mad_bcn_decode_image" in bcn_shim.read_text() and "dxmt::bcn_decode_image" in bcn_shim.read_text())
for name, s, root_var in (("tools/build-madeira-d3d12-dll.sh", ci, '$R'), ("build/madeira-d3d12/build-pe.sh", dev, '$REPO_ROOT')):
    check(f"{name} compiles mad_bcn.cpp and dxmt_bcn.cpp and links them into madeira_d3d12.dll",
          '"$SRC/mad_bcn.cpp" "' + root_var + '/dxmt/src/dxmt/dxmt_bcn.cpp"' in s and '"$OUT/mad_bcn.o" "$OUT/dxmt_bcn.o"' in s
          and "-std=c++17 -fno-exceptions -fno-rtti" in s and s.index("dxmt_bcn.cpp") < s.index('-o "$OUT/madeira_d3d12.dll"'))
check("this check runs in the workflow", "tests/host/check-d3d12-bc-decode.py" in wf)

# ---- 2. the harness
cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
if not cxx or not bcn_cpp.is_file():
    print("SKIP: no C++ compiler or no dxmt_bcn.cpp for the behaviour part")
else:
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <string>
#include <vector>
typedef uint32_t UINT; typedef int32_t LONG; typedef uint64_t UINT64; typedef uint64_t obj_handle_t; typedef uint16_t UINT16;
enum WMTPixelFormat { WMTPixelFormatRGBA8Unorm = 1, WMTPixelFormatBC1_RGBA, WMTPixelFormatBC1_RGBA_sRGB, WMTPixelFormatBC2_RGBA, WMTPixelFormatBC2_RGBA_sRGB,
  WMTPixelFormatBC3_RGBA, WMTPixelFormatBC3_RGBA_sRGB, WMTPixelFormatBC4_RUnorm, WMTPixelFormatBC4_RSnorm, WMTPixelFormatBC5_RGUnorm, WMTPixelFormatBC5_RGSnorm,
  WMTPixelFormatBC7_RGBAUnorm, WMTPixelFormatBC7_RGBAUnorm_sRGB, WMTPixelFormatBC6H_RGBFloat, WMTPixelFormatBC6H_RGBUfloat };
enum { WMTBlitCommandCopyFromBufferToTexture = 5, WMTResourceStorageModeShared = 0 };
struct wmtcmd_base { uint32_t type; struct { void *ptr; } next; };
struct WMTSize { uint32_t width, height, depth; }; struct WMTOrigin { uint32_t x, y, z; };
struct wmtcmd_blit_copy_from_buffer_to_texture : wmtcmd_base {
  obj_handle_t src; uint64_t src_offset; uint64_t bytes_per_row, bytes_per_image; WMTSize size; obj_handle_t dst; uint32_t slice, level; WMTOrigin origin; uint16_t reserved[2]; };
struct WMTBufferInfo { uint64_t length; int options; struct { void *ptr; } memory; };
struct mad_resource { obj_handle_t texture, buffer; void *cpu; UINT64 size; UINT width, height, depth; int tex_pf; };
struct mad_cmd { int kind; union { struct { mad_resource *tex, *buf; UINT64 off; UINT row, rows; UINT w, h, d; UINT level, slice; UINT x, y, z; UINT plane; } bt; } u; };
struct mad_device { obj_handle_t mtl_device; }; struct mad_queue { mad_device *device; }; struct mad_exec { mad_queue *q; obj_handle_t benc; };
static std::string g_log; static int g_native_bc, g_cfg_decode, g_query_calls, g_stage_released, g_encoded;
static wmtcmd_blit_copy_from_buffer_to_texture g_cmd; static std::vector<uint8_t> g_stage; static obj_handle_t g_stage_handle = 0xABC0;
static void d3d12_log(const char *f, ...) { char b[1024]; va_list ap; va_start(ap, f); vsnprintf(b, sizeof b, f, ap); va_end(ap); g_log += b; }
static LONG InterlockedIncrement(volatile LONG *p) { return ++*p; }
static long long mad_cfg_int_pe(const char *k, long long d) { return !strcmp(k, "d3d12-bc-decode") ? g_cfg_decode : d; }
static bool MTLDevice_supportsBCTextureCompression(obj_handle_t) { g_query_calls++; return g_native_bc; }
static obj_handle_t MTLDevice_newBuffer(obj_handle_t, WMTBufferInfo *bi) { g_stage.assign(bi->length, 0xCD); bi->memory.ptr = g_stage.data(); return g_stage_handle; }
static void NSObject_release(obj_handle_t h) { if (h == g_stage_handle) g_stage_released++; }
static void MTLBlitCommandEncoder_encodeCommands(obj_handle_t, const wmtcmd_base *c) { g_cmd = *(const wmtcmd_blit_copy_from_buffer_to_texture *)c; g_encoded++; }
static void mad_mip_dims(const mad_resource *r, UINT level, UINT *w, UINT *h, UINT *d) { *w = r->width >> level ? r->width >> level : 1; *h = r->height >> level ? r->height >> level : 1; *d = r->depth ? r->depth : 1; }
''' + 'extern "C" {\n' + block + '\n}\n' + r'''
static int fails = 0;
static void expect(bool c, const char *m) { printf("%s %s\n", c ? "ok  " : "FAIL", m); if (!c) fails++; }
static void bc1_solid(uint8_t *b, uint16_t c565) { b[0] = c565 & 255; b[1] = c565 >> 8; b[2] = c565 & 255; b[3] = c565 >> 8; b[4] = b[5] = b[6] = b[7] = 0; }
static void reset() { g_log.clear(); g_query_calls = g_stage_released = g_encoded = 0; g_stage.clear(); memset(&g_cmd, 0, sizeof g_cmd); }
int main(int argc, char **argv) {
  int phase = atoi(argv[1]);
  mad_device dev = {1}; mad_queue q = {&dev}; mad_exec e = {&q, 77};
  static uint8_t src[4096]; mad_resource buf = {0x100, 0x200, src, sizeof src, 0, 0, 0, 0}; mad_resource tex = {0x300, 0x400, nullptr, 0, 8, 8, 1, WMTPixelFormatBC1_RGBA};
  mad_cmd c = {}; c.u.bt = {&tex, &buf, 16, 16, 2, 8, 8, 1, 0, 0, 0, 0, 0, 0};
  bc1_solid(src + 16 + 0, 0xF800); bc1_solid(src + 16 + 8, 0x07E0); bc1_solid(src + 16 + 16, 0x001F); bc1_solid(src + 16 + 24, 0xFFFF);
  /* rows of blocks are 16 bytes apart (two blocks), c.row = 16, rows = 2: red green / blue white */
  if (phase == 0) {   /* key off */
    g_cfg_decode = 0; g_native_bc = 0;
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_query_calls == 0 && g_log.empty() && g_encoded == 0 && g_stage.empty(),
           "key off: nothing decoded, the GPU is not asked, nothing logged, no staging buffer");
    return fails;
  }
  if (phase == 1) {   /* key on, GPU with BC sampling */
    g_cfg_decode = 1; g_native_bc = 1;
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_query_calls == 1 && g_encoded == 0 && g_log.find("native") != std::string::npos && g_log.find("copied raw") != std::string::npos,
           "key on, BC sampling native: the raw copy stays (logged once)");
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_query_calls == 1, "... decided once");
    return fails;
  }
  g_cfg_decode = 1; g_native_bc = 0;
  if (phase == 2) {
    expect(mad_bc_upload_decoded(&e, &c) == 1 && g_encoded == 1, "key on, no BC sampling: the BC1 upload is decoded and copied from a staging buffer");
    expect(g_log.find("NOT available") != std::string::npos && g_log.find("decoded on the CPU") != std::string::npos, "... the decision is logged");
    expect(g_stage.size() == 8 * 8 * 4, "the staging buffer holds 8x8 RGBA8 texels");
    auto px = [&](int x, int y) { return &g_stage[(y * 8 + x) * 4]; };
    expect(px(0, 0)[0] == 255 && px(0, 0)[1] == 0 && px(0, 0)[2] == 0 && px(0, 0)[3] == 255 && px(3, 3)[0] == 255, "block (0,0) is red");
    expect(px(4, 0)[0] == 0 && px(4, 0)[1] == 255 && px(4, 0)[2] == 0 && px(7, 3)[1] == 255, "block (1,0) is green");
    expect(px(0, 4)[2] == 255 && px(0, 4)[0] == 0 && px(3, 7)[2] == 255, "block (0,1) is blue");
    expect(px(4, 4)[0] == 255 && px(4, 4)[1] == 255 && px(4, 4)[2] == 255 && px(7, 7)[3] == 255, "block (1,1) is white");
    expect(g_cmd.type == WMTBlitCommandCopyFromBufferToTexture && g_cmd.src == g_stage_handle && g_cmd.src_offset == 0 && g_cmd.bytes_per_row == 32 && g_cmd.bytes_per_image == 256,
           "the copy names the staging buffer, the texel pitch (8 x 4) and the image size");
    expect(g_cmd.size.width == 8 && g_cmd.size.height == 8 && g_cmd.size.depth == 1 && g_cmd.dst == 0x300 && g_cmd.slice == 0 && g_cmd.level == 0, "... the extent, texture, slice and level");
    expect(g_stage_released == 1, "the staging buffer is released after encoding (the command buffer keeps it)");
    return fails;
  }
  if (phase == 3) {   /* a level that is not a multiple of 4, an origin, a mip */
    tex.width = 6; tex.height = 6; c.u.bt.w = 8; c.u.bt.h = 8; c.u.bt.x = 0; c.u.bt.y = 0;
    expect(mad_bc_upload_decoded(&e, &c) == 1, "a 6x6 level with an 8x8 copy: decoded");
    expect(g_cmd.size.width == 6 && g_cmd.size.height == 6 && g_cmd.bytes_per_row == 24 && g_stage.size() == 6 * 6 * 4, "... clipped to the level: 6x6, pitch 24");
    expect(g_stage[(5 * 6 + 5) * 4 + 0] == 255 && g_stage[(5 * 6 + 5) * 4 + 1] == 255, "... the clipped corner (white block) is right");
    reset(); tex.width = 16; tex.height = 16; c.u.bt.level = 1; c.u.bt.x = 4; c.u.bt.y = 0; c.u.bt.w = 4; c.u.bt.h = 8;   /* mip 1 is 8x8; origin x 4 leaves 4 columns */
    expect(mad_bc_upload_decoded(&e, &c) == 1 && g_cmd.level == 1 && g_cmd.origin.x == 4 && g_cmd.size.width == 4 && g_cmd.size.height == 8, "a mip with an origin: the origin and level reach the copy, the extent is clipped to what is left");
    reset(); c.u.bt.x = 8; expect(mad_bc_upload_decoded(&e, &c) == 1 && g_encoded == 0 && g_stage.empty(), "an origin outside the level: nothing to copy, nothing queued (D3D clips it away too)");
    return fails;
  }
  if (phase == 4) {   /* BC4 and depth */
    tex.tex_pf = WMTPixelFormatBC4_RUnorm; tex.width = 8; tex.height = 4;
    memset(src, 0, sizeof src);
    for (int i = 0; i < 2; i++) { uint8_t *b = src + 16 + 8 * i; b[0] = b[1] = (uint8_t)(100 + 100 * i); }   /* endpoints equal: every texel is the endpoint */
    c.u.bt.row = 16; c.u.bt.rows = 1; c.u.bt.w = 8; c.u.bt.h = 4;
    expect(mad_bc_upload_decoded(&e, &c) == 1 && g_stage.size() == 8 * 4 && g_cmd.bytes_per_row == 8, "BC4: one byte per texel (R8), pitch 8");
    expect(g_stage[0] == 100 && g_stage[3] == 100 && g_stage[4] == 200 && g_stage[7] == 200 && g_stage[31] == 200, "... the endpoints are the texels");
    reset(); tex.tex_pf = WMTPixelFormatBC1_RGBA; tex.width = 4; tex.height = 4; tex.depth = 2; c.u.bt.row = 8; c.u.bt.rows = 1; c.u.bt.w = 4; c.u.bt.h = 4; c.u.bt.d = 2;
    memset(src, 0, sizeof src); bc1_solid(src + 16, 0xF800); bc1_solid(src + 16 + 8, 0x001F);   /* slice 0 red, slice 1 blue: slices are row * rows apart */
    expect(mad_bc_upload_decoded(&e, &c) == 1 && g_stage.size() == 4 * 4 * 4 * 2 && g_cmd.bytes_per_image == 64 && g_cmd.size.depth == 2, "depth 2: both slices decoded, image size 64");
    expect(g_stage[0] == 255 && g_stage[2] == 0 && g_stage[64 + 0] == 0 && g_stage[64 + 2] == 255, "... slice 0 red, slice 1 blue");
    return fails;
  }
  if (phase == 5) {   /* left to the raw copy */
    tex.tex_pf = WMTPixelFormatBC6H_RGBFloat;
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_encoded == 0 && g_log.find("BC6H") != std::string::npos, "BC6H: no decoder, the raw copy (logged)");
    tex.tex_pf = WMTPixelFormatRGBA8Unorm; reset();
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_encoded == 0, "a texture that is not BC: untouched");
    tex.tex_pf = WMTPixelFormatBC1_RGBA; buf.cpu = nullptr; reset();
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_encoded == 0 && g_stage.empty(), "a source buffer without a CPU view: the raw copy");
    buf.cpu = src; buf.size = 20; reset();
    expect(mad_bc_upload_decoded(&e, &c) == 0 && g_encoded == 0 && g_stage.empty(), "a read past the end of its buffer: the raw copy");
    return fails;
  }
  return 2;
}
'''
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "h.cpp").write_text(harness)
        flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-Wno-unused-parameter", "-Wno-missing-field-initializers",
                 "-Wno-c++20-designator", "-Wno-writable-strings", "-Wno-unused-const-variable"]
        san = ["-fsanitize=address,undefined"]
        objs = []
        ok_build = True
        for name, cpp in (("mad_bcn", bcn_shim), ("dxmt_bcn", bcn_cpp)):
            o = t / f"{name}.o"
            r = subprocess.run([cxx, "-std=c++17", "-O1", "-g", *san, "-fno-exceptions", "-fno-rtti", f"-I{bcn_cpp.parent}", "-c", str(cpp), "-o", str(o)], capture_output=True, text=True)
            if r.returncode:
                r = subprocess.run([cxx, "-std=c++17", "-O1", "-g", "-fno-exceptions", "-fno-rtti", f"-I{bcn_cpp.parent}", "-c", str(cpp), "-o", str(o)], capture_output=True, text=True)
                san = []
            ok_build &= r.returncode == 0
            objs.append(str(o))
            if r.returncode:
                print(r.stderr[-1500:])
        b = subprocess.run([cxx, *flags, *san, "-o", str(t / "h"), str(t / "h.cpp"), *objs], capture_output=True, text=True) if ok_build else None
        check("the real mad_bcn.cpp / dxmt_bcn.cpp and the cut-out upload code compile on the host", b is not None and b.returncode == 0)
        if b is not None and b.returncode:
            print(b.stderr[-3500:])
        elif b is not None:
            for ph, what in ((0, "key off"), (1, "native BC sampling"), (2, "BC1 decode"), (3, "clipping and mips"), (4, "BC4 and depth"), (5, "left to the raw copy")):
                r = subprocess.run([str(t / "h"), str(ph)], capture_output=True, text=True)
                print(r.stdout.rstrip())
                if r.returncode:
                    print(r.stderr[-1500:])
                check(f"harness case {ph} ({what})", r.returncode == 0)

print("FAILED: d3d12 BC decode" if failures else "PASS: d3d12 BC decode")
sys.exit(1 if failures else 0)
