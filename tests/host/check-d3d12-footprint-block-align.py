#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
"""GetCopyableFootprints answers block-compressed subresources in whole blocks, and the copy that takes such a footprint stays inside the mip (build 652;
madeira-d3d12; night round 2026-11-11, G8 S11 = G4 S5).

Satisfactory (Epic, Unreal 5; Send Logs DPE0R8 build 630, GEZ8HT 614, CVBHM0 630, CM0TY7 and T0WWAF 634) ended at its first texture upload:
    LogWindows: Error: appError called: Assertion failed: (Footprint.Width % Format.BlockSizeX) == 0 [File:...D3D12Texture.cpp] [Line: 1201]
    Width not aligned to block size for: 'T_Default_Material_Grid_N'
Direct3D 12 answers a block-compressed subresource with whole blocks (a 2x2 or 1x1 BC mip has a 4x4 footprint, a 100x50 level 100x52; Wine's vkd3d,
device.c d3d12_device_get_copyable_footprints: width = align(w, block_width)); madeira-d3d12 answered the unrounded size. The footprint's Width and Height are
now rounded up to the block size (block is 1 for every uncompressed format and for buffers: nothing else changes; rows, row bytes, pitch, offsets and the
total are what they were). A footprint (or a box made of one) can then run past a small mip, so
list_CopyTextureRegion clips a block-compressed upload's extent to the mip, from the destination origin (a Metal copy region belongs inside the mip level; what Metal
does with one that ends beyond it has not been seen on a device): the same blocks are copied (a mip w texels wide has ceil(w / 4) block columns whichever extent is
given); a copy that takes a footprint of GetCopyableFootprints sends the extent it sent before (the mip's own), as mad_bc_upload_decoded already clips.

Checks:
  1. the source: the two rounded assignments, nothing else in the layout line group changed; the clip is in the upload branch only, after the origin is
     stored, only for block > 1, only when the extent overruns, logs at most four lines; the readback branch is untouched;
  2. the real mad_align, mad_format_info, mad_full_mips / mad_desc_mips, device_GetCopyableFootprints, mad_mip_dims, mad_subresource_plane and
     list_CopyTextureRegion are cut out of madeira_d3d12.c and compiled on the host with fakes for the object plumbing:
       - differential: the new device_GetCopyableFootprints against the same function with the two rounded lines put back, over buffers, uncompressed
         textures (1D/2D/3D, arrays, 1x1 mips) and BC1..BC7 textures: every field but Footprint.Width and Height is identical; those two differ only for block
         formats, where they are the old value rounded up to a multiple of 4 (and equal it for uncompressed formats and buffers);
       - the spec cases: BC1 256x256 with 9 mips (subresources 7 and 8 have a 4x4 footprint, one row of 8 bytes, pitch 256), BC7 100x50 (100x52, 13 rows);
       - the copy: for every subresource of those textures the upload recorded from the real footprint has the mip's own extent, the footprint's pitch and
         offset and the rows the footprint's height gives (as before the change); a box inside the mip is untouched, a box that overruns is clipped from the
         destination origin; an uncompressed texture is never clipped; no clip line past the fourth;
  3. the auto-tune rule is fixed in 652 and the fixtures are there; CI runs this check.
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
            print("     " + str(detail)[:2000])


def cut(text, start, end):
    a = text.index(start)
    return text[a:text.index(end, a) + len(end)]


def ordered(text, *needles):
    """True when every needle is in the text and they come in this order"""
    at = [text.find(n) for n in needles]
    return all(a >= 0 for a in at) and at == sorted(at)


align = cut(src, "static UINT64 mad_align(UINT64 v, UINT64 a) {", "\n")
fmt = cut(src, "static void mad_format_info(DXGI_FORMAT f, UINT *bytes, UINT *block) {", "\n}\n")
mips = cut(src, "static int g_mip0_full = -1;", "    return tmp;\n}\n")
gcf = cut(src, "static void STDMETHODCALLTYPE device_GetCopyableFootprints(ID3D12Device *This,", "\n}\n")
fpa = cut(src, "static int g_footprint_block_align = -1;", "\n}\n")   # the lead: the handle bcd.cfg d3d12-footprint-block-align = 0 (default 1)
sub = cut(src, "static void mad_subresource_plane(const struct mad_resource *r, UINT sub, UINT *level, UINT *slice, UINT *plane) {", "\n}\n")
dims = cut(src, "static void mad_mip_dims(const struct mad_resource *r, UINT level, UINT *w, UINT *h, UINT *d) {", "\n}\n")
copy = cut(src, "static void STDMETHODCALLTYPE list_CopyTextureRegion(ID3D12GraphicsCommandList *This,", "\n}\n")

# ---- 1. the source
LINE_W = "layouts[i].Footprint.Width = (UINT)mad_align(w, mad_footprint_block_align() ? block : 1);"
LINE_H = "layouts[i].Footprint.Height = (UINT)mad_align(h, mad_footprint_block_align() ? block : 1);"
check("the footprint's Width and Height are rounded up to the block size; depth, pitch, offset, rows, row bytes and the total are as before",
      LINE_W in gcf and LINE_H in gcf and "layouts[i].Footprint.Depth = (UINT)d;" in gcf and "layouts[i].Footprint.RowPitch = (UINT)pitch;" in gcf
      and "layouts[i].Offset = offset;" in gcf and "if (row_count) row_count[i] = (UINT)rows;" in gcf and "if (row_size) row_size[i] = rowbytes;" in gcf
      and "rows = (h + block - 1) / block;" in gcf and "offset += pitch * rows * d;" in gcf)
up_s = copy.index("if (d->texture && !s->texture) {")
up_e = copy.index("if (s->texture && !d->texture) {")
upload = copy[up_s:up_e]
readback = copy[up_e:copy.index("if (s->texture && d->texture) {")]
check("the clip is in the upload branch, after the destination origin is stored, for block > 1 only, only when the extent overruns, four log lines at most",
      ordered(upload, "c->u.bt.x = x; c->u.bt.y = y; c->u.bt.z = z;", "if (block > 1) {", "d3d12_log(\"[madeira-d3d12] block-compressed copy of")
      and "UINT rw = x < w ? w - x : 0, rh = y < h ? h - y : 0;" in upload and "if ((rw && c->u.bt.w > rw) || (rh && c->u.bt.h > rh)) {" in upload
      and "if (rw && c->u.bt.w > rw) c->u.bt.w = rw;" in upload and "if (rh && c->u.bt.h > rh) c->u.bt.h = rh;" in upload
      and "if (InterlockedIncrement(&said_clip) <= 4)" in upload)
check("the footprint-derived extent, rows, row pitch and the box handling above the clip are as before",
      "c->u.bt.w = f->Footprint.Width ? f->Footprint.Width : w;" in upload and "c->u.bt.rows = (c->u.bt.h + block - 1) / block;" in upload
      and "c->u.bt.w = box->right - box->left; c->u.bt.h = box->bottom - box->top; c->u.bt.d = box->back - box->front;" in upload
      and "c->u.bt.row = f->Footprint.RowPitch ? f->Footprint.RowPitch" in upload)
check("the readback branch is untouched (it sizes the copy from the mip and only reads the footprint for pitches)",
      "c->u.bt.w = w; c->u.bt.h = h; c->u.bt.d = dd;" in readback and "clipped" not in readback and "block > 1" not in readback)

check("the alignment has a handle: bcd.cfg d3d12-footprint-block-align, default 1, 0 = the mip's own size (build 651)",
      'g_footprint_block_align = mad_cfg_int_pe("d3d12-footprint-block-align", 1) ? 1 : 0;' in fpa)

# ---- 2. the real code on the host
harness = r'''
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
typedef unsigned UINT; typedef uint64_t UINT64; typedef uint16_t UINT16; typedef long LONG; typedef unsigned DXGI_FORMAT; typedef long HRESULT;
#define STDMETHODCALLTYPE
#define InterlockedIncrement(p) (__sync_add_and_fetch((p), 1))
enum { DXGI_FORMAT_UNKNOWN = 0, DXGI_FORMAT_R32G32B32A32_TYPELESS = 1, DXGI_FORMAT_R32G32B32A32_SINT = 4, DXGI_FORMAT_R32G32B32_TYPELESS = 5, DXGI_FORMAT_R32G32B32_SINT = 8,
       DXGI_FORMAT_R16G16B16A16_TYPELESS = 9, DXGI_FORMAT_R32G32_SINT = 18, DXGI_FORMAT_R32G8X24_TYPELESS = 19, DXGI_FORMAT_X32_TYPELESS_G8X24_UINT = 22,
       DXGI_FORMAT_R10G10B10A2_TYPELESS = 23, DXGI_FORMAT_R8G8B8A8_UNORM = 28, DXGI_FORMAT_X24_TYPELESS_G8_UINT = 47, DXGI_FORMAT_R8G8_TYPELESS = 48,
       DXGI_FORMAT_R16_SINT = 59, DXGI_FORMAT_R8_TYPELESS = 60, DXGI_FORMAT_R8_UNORM = 61, DXGI_FORMAT_A8_UNORM = 65, DXGI_FORMAT_R9G9B9E5_SHAREDEXP = 67,
       DXGI_FORMAT_R8G8_B8G8_UNORM = 68, DXGI_FORMAT_G8R8_G8B8_UNORM = 69, DXGI_FORMAT_BC1_TYPELESS = 70, DXGI_FORMAT_BC1_UNORM_SRGB = 72,
       DXGI_FORMAT_BC2_TYPELESS = 73, DXGI_FORMAT_BC3_UNORM_SRGB = 78, DXGI_FORMAT_BC4_TYPELESS = 79, DXGI_FORMAT_BC4_SNORM = 81, DXGI_FORMAT_BC5_TYPELESS = 82,
       DXGI_FORMAT_BC5_SNORM = 84, DXGI_FORMAT_B5G6R5_UNORM = 85, DXGI_FORMAT_B5G5R5A1_UNORM = 86, DXGI_FORMAT_B8G8R8A8_UNORM = 87, DXGI_FORMAT_B8G8R8X8_UNORM_SRGB = 93,
       DXGI_FORMAT_BC6H_TYPELESS = 94, DXGI_FORMAT_BC7_TYPELESS = 97, DXGI_FORMAT_BC7_UNORM = 98, DXGI_FORMAT_BC7_UNORM_SRGB = 99,
       DXGI_FORMAT_BC1_UNORM = 71, DXGI_FORMAT_BC3_UNORM = 77, DXGI_FORMAT_R16G16B16A16_FLOAT = 10, DXGI_FORMAT_R32_FLOAT = 41 };
#define D3D12_TEXTURE_DATA_PITCH_ALIGNMENT 256
#define D3D12_TEXTURE_DATA_PLACEMENT_ALIGNMENT 512
typedef enum { D3D12_RESOURCE_DIMENSION_UNKNOWN, D3D12_RESOURCE_DIMENSION_BUFFER, D3D12_RESOURCE_DIMENSION_TEXTURE1D, D3D12_RESOURCE_DIMENSION_TEXTURE2D,
               D3D12_RESOURCE_DIMENSION_TEXTURE3D } D3D12_RESOURCE_DIMENSION;
typedef struct { D3D12_RESOURCE_DIMENSION Dimension; UINT64 Alignment; UINT64 Width; UINT Height; UINT16 DepthOrArraySize; UINT16 MipLevels; DXGI_FORMAT Format;
                 struct { UINT Count, Quality; } SampleDesc; unsigned Layout; unsigned Flags; } D3D12_RESOURCE_DESC;
typedef struct { DXGI_FORMAT Format; UINT Width, Height, Depth, RowPitch; } D3D12_SUBRESOURCE_FOOTPRINT;
typedef struct { UINT64 Offset; D3D12_SUBRESOURCE_FOOTPRINT Footprint; } D3D12_PLACED_SUBRESOURCE_FOOTPRINT;
typedef struct { UINT left, top, front, right, bottom, back; } D3D12_BOX;
typedef struct ID3D12Device { int d; } ID3D12Device; typedef struct ID3D12Resource { int d; } ID3D12Resource; typedef struct ID3D12GraphicsCommandList { int d; } ID3D12GraphicsCommandList;
typedef enum { D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX = 0, D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT = 1 } D3D12_TEXTURE_COPY_TYPE;
typedef struct { ID3D12Resource *pResource; D3D12_TEXTURE_COPY_TYPE Type; union { D3D12_PLACED_SUBRESOURCE_FOOTPRINT PlacedFootprint; UINT SubresourceIndex; }; } D3D12_TEXTURE_COPY_LOCATION;
static int nlog; static char lastlog[512];
static void d3d12_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(lastlog, sizeof lastlog, fmt, ap); va_end(ap); nlog++; }
static long long mad_cfg_int_pe(const char *k, long long d) { (void)k; return d; }
struct mad_resource { void *texture; const char *name; D3D12_RESOURCE_DESC desc; int is_depth, has_stencil; UINT64 size; };
enum mad_ck { MC_COPY_B2T, MC_COPY_T2B, MC_COPY_T2T };
struct mad_cmd { enum mad_ck kind; union {
    struct { struct mad_resource *tex, *buf; UINT64 off; UINT row, rows; UINT w, h, d; UINT level, slice; UINT x, y, z; UINT plane; } bt;
    struct { struct mad_resource *dst, *src; UINT dlevel, dslice, slevel, sslice; UINT w, h, d; UINT dx, dy, dz, sx, sy, sz; UINT dplane, splane; } tt; } u; };
struct mad_list { struct mad_cmd cmds[4]; unsigned n; };
static struct mad_cmd *mad_list_push(struct mad_list *l, enum mad_ck kind) { struct mad_cmd *c = &l->cmds[l->n++ % 4]; memset(c, 0, sizeof *c); c->kind = kind; return c; }
static void list_CopyBufferRegion(ID3D12GraphicsCommandList *This, ID3D12Resource *dst, UINT64 doff, ID3D12Resource *src, UINT64 soff, UINT64 n)
{ (void)This; (void)dst; (void)doff; (void)src; (void)soff; (void)n; printf("BUFFERCOPY\n"); }
''' + align + "\n" + fmt + "\n" + mips + "\n" + fpa + "\n" + gcf + "\n" + gcf.replace(LINE_W, "layouts[i].Footprint.Width = (UINT)w;").replace(LINE_H, "layouts[i].Footprint.Height = (UINT)h;").replace(
    "device_GetCopyableFootprints(", "device_GetCopyableFootprints_old(") + "\n" + sub + "\n" + dims + "\n" + copy + r'''
static const char *fname(DXGI_FORMAT f) { switch (f) { case DXGI_FORMAT_BC1_UNORM: return "BC1"; case DXGI_FORMAT_BC3_UNORM: return "BC3"; case DXGI_FORMAT_BC4_TYPELESS: return "BC4";
    case DXGI_FORMAT_BC5_SNORM: return "BC5"; case DXGI_FORMAT_BC6H_TYPELESS: return "BC6H"; case DXGI_FORMAT_BC7_UNORM: return "BC7"; case DXGI_FORMAT_R8G8B8A8_UNORM: return "RGBA8";
    case DXGI_FORMAT_R32_FLOAT: return "R32F"; case DXGI_FORMAT_R16G16B16A16_FLOAT: return "RGBA16F"; default: return "?"; } }
static unsigned al4(unsigned v) { return (v + 3) & ~3u; }

int main(void)
{
    static const DXGI_FORMAT fmts[] = { DXGI_FORMAT_BC1_UNORM, DXGI_FORMAT_BC3_UNORM, DXGI_FORMAT_BC4_TYPELESS, DXGI_FORMAT_BC5_SNORM, DXGI_FORMAT_BC6H_TYPELESS, DXGI_FORMAT_BC7_UNORM,
                                        DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_FORMAT_R32_FLOAT, DXGI_FORMAT_R16G16B16A16_FLOAT };
    static const struct { D3D12_RESOURCE_DIMENSION dim; UINT64 w; UINT h; UINT16 depth, mips; const char *tag; } shapes[] = {
        { D3D12_RESOURCE_DIMENSION_TEXTURE2D, 256, 256, 1, 9, "256x256 m9" }, { D3D12_RESOURCE_DIMENSION_TEXTURE2D, 100, 50, 1, 7, "100x50 m7" },
        { D3D12_RESOURCE_DIMENSION_TEXTURE2D, 64, 64, 3, 7, "64x64 a3 m7" }, { D3D12_RESOURCE_DIMENSION_TEXTURE2D, 3, 5, 1, 3, "3x5 m3" },
        { D3D12_RESOURCE_DIMENSION_TEXTURE3D, 32, 16, 8, 6, "3D 32x16x8 m6" }, { D3D12_RESOURCE_DIMENSION_TEXTURE2D, 1, 1, 1, 1, "1x1" },
        { D3D12_RESOURCE_DIMENSION_TEXTURE1D, 33, 1, 1, 6, "1D 33 m6" }, { D3D12_RESOURCE_DIMENSION_TEXTURE2D, 6, 6, 1, 3, "6x6 m3" },
    };
    unsigned f, sh, s, mismatches = 0, cases = 0, bad_dim = 0, copies = 0, bad_copy = 0;
    for (f = 0; f < sizeof fmts / sizeof fmts[0]; f++)
        for (sh = 0; sh < sizeof shapes / sizeof shapes[0]; sh++) {
            D3D12_RESOURCE_DESC d; unsigned subs; struct mad_resource tex, buf;
            memset(&d, 0, sizeof d);
            d.Dimension = shapes[sh].dim; d.Width = shapes[sh].w; d.Height = shapes[sh].h; d.DepthOrArraySize = shapes[sh].depth; d.MipLevels = shapes[sh].mips; d.Format = fmts[f];
            d.SampleDesc.Count = 1;
            subs = (unsigned)d.MipLevels * (d.Dimension == D3D12_RESOURCE_DIMENSION_TEXTURE3D ? 1u : d.DepthOrArraySize);
            {   /* the whole layout at once, as UpdateSubresources asks, new against old */
                D3D12_PLACED_SUBRESOURCE_FOOTPRINT ln[64], lo[64]; UINT rn[64], ro[64]; UINT64 sn[64], so[64], tn = 0, to = 0;
                device_GetCopyableFootprints(0, &d, 0, subs, 4096, ln, rn, sn, &tn);
                device_GetCopyableFootprints_old(0, &d, 0, subs, 4096, lo, ro, so, &to);
                cases++;
                for (s = 0; s < subs; s++) {
                    int block = fmts[f] >= DXGI_FORMAT_BC1_TYPELESS && fmts[f] <= DXGI_FORMAT_BC7_UNORM_SRGB;
                    int same_rest = ln[s].Offset == lo[s].Offset && ln[s].Footprint.Format == lo[s].Footprint.Format && ln[s].Footprint.Depth == lo[s].Footprint.Depth &&
                                    ln[s].Footprint.RowPitch == lo[s].Footprint.RowPitch && rn[s] == ro[s] && sn[s] == so[s];
                    int dim_ok = block ? (ln[s].Footprint.Width == al4(lo[s].Footprint.Width) && ln[s].Footprint.Height == al4(lo[s].Footprint.Height))
                                       : (ln[s].Footprint.Width == lo[s].Footprint.Width && ln[s].Footprint.Height == lo[s].Footprint.Height);
                    if (!same_rest) mismatches++;
                    if (!dim_ok) bad_dim++;
                }
                if (tn != to) mismatches++;
            }
            memset(&tex, 0, sizeof tex); memset(&buf, 0, sizeof buf);
            tex.texture = &tex; tex.name = "tex"; tex.desc = d; buf.size = 1 << 24; buf.name = "buf";
            for (s = 0; s < subs; s++) {   /* the copy a game makes of every subresource: the footprint of GetCopyableFootprints, no box */
                D3D12_PLACED_SUBRESOURCE_FOOTPRINT lay; UINT rows; UINT64 rsz, tot;
                D3D12_TEXTURE_COPY_LOCATION dst, srcl; struct mad_list l; struct mad_cmd *c; UINT lv, sl, pl, mw, mh, md; int block;
                memset(&l, 0, sizeof l);
                device_GetCopyableFootprints(0, &d, s, 1, 0, &lay, &rows, &rsz, &tot);
                memset(&dst, 0, sizeof dst); memset(&srcl, 0, sizeof srcl);
                dst.pResource = (ID3D12Resource *)&tex; dst.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX; dst.SubresourceIndex = s;
                srcl.pResource = (ID3D12Resource *)&buf; srcl.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; srcl.PlacedFootprint = lay;
                list_CopyTextureRegion((ID3D12GraphicsCommandList *)&l, &dst, 0, 0, 0, &srcl, 0);
                c = &l.cmds[0];
                mad_subresource_plane(&tex, s, &lv, &sl, &pl); mad_mip_dims(&tex, lv, &mw, &mh, &md);
                block = fmts[f] >= DXGI_FORMAT_BC1_TYPELESS && fmts[f] <= DXGI_FORMAT_BC7_UNORM_SRGB;
                /* the old recording: extent = the old footprint = the mip's own size, rows from it, pitch and offset from the footprint */
                copies++;
                if (c->u.bt.w != mw || c->u.bt.h != mh || c->u.bt.d != lay.Footprint.Depth || c->u.bt.row != lay.Footprint.RowPitch || c->u.bt.off != lay.Offset ||
                    c->u.bt.rows != (block ? (lay.Footprint.Height + 3) / 4 : lay.Footprint.Height) || c->u.bt.level != lv || c->u.bt.slice != sl) {
                    bad_copy++;
                    printf("BADCOPY %s %s sub %u: ext %ux%ux%u mip %ux%u row %u rows %u\n", fname(fmts[f]), shapes[sh].tag, s, c->u.bt.w, c->u.bt.h, c->u.bt.d, mw, mh, c->u.bt.row, c->u.bt.rows);
                }
            }
        }
    printf("layouts: %u cases, %u field mismatches (offset/format/depth/pitch/rows/rowsize/total), %u footprint size faults\n", cases, mismatches, bad_dim);
    printf("copies: %u recorded from real footprints, %u not the mip's own extent / old pitch, offset, rows\n", copies, bad_copy);
    {   /* the spec cases */
        D3D12_RESOURCE_DESC d; D3D12_PLACED_SUBRESOURCE_FOOTPRINT lay[9]; UINT rows[9]; UINT64 rsz[9], tot;
        memset(&d, 0, sizeof d); d.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D; d.Width = 256; d.Height = 256; d.DepthOrArraySize = 1; d.MipLevels = 9; d.Format = DXGI_FORMAT_BC1_UNORM; d.SampleDesc.Count = 1;
        device_GetCopyableFootprints(0, &d, 0, 9, 0, lay, rows, rsz, &tot);
        printf("BC1 256x256: s0 %ux%u rows %u; s6 %ux%u rows %u; s7 %ux%u rows %u rowbytes %llu pitch %u; s8 %ux%u rows %u\n", lay[0].Footprint.Width, lay[0].Footprint.Height, rows[0],
               lay[6].Footprint.Width, lay[6].Footprint.Height, rows[6], lay[7].Footprint.Width, lay[7].Footprint.Height, rows[7], (unsigned long long)rsz[7], lay[7].Footprint.RowPitch,
               lay[8].Footprint.Width, lay[8].Footprint.Height, rows[8]);
        d.Width = 100; d.Height = 50; d.MipLevels = 7; d.Format = DXGI_FORMAT_BC7_UNORM;
        device_GetCopyableFootprints(0, &d, 0, 3, 0, lay, rows, rsz, &tot);
        printf("BC7 100x50: s0 %ux%u rows %u rowbytes %llu; s1 %ux%u; s2 %ux%u\n", lay[0].Footprint.Width, lay[0].Footprint.Height, rows[0], (unsigned long long)rsz[0],
               lay[1].Footprint.Width, lay[1].Footprint.Height, lay[2].Footprint.Width, lay[2].Footprint.Height);
        d.Format = DXGI_FORMAT_R8G8B8A8_UNORM; d.Width = 2; d.Height = 2; d.MipLevels = 2;
        device_GetCopyableFootprints(0, &d, 0, 2, 0, lay, rows, rsz, &tot);
        printf("RGBA8 2x2: s0 %ux%u; s1 %ux%u rows %u\n", lay[0].Footprint.Width, lay[0].Footprint.Height, lay[1].Footprint.Width, lay[1].Footprint.Height, rows[1]);
    }
    {   /* boxes: BC1 24x24, 3 mips (24, 12, 6); the 6x6 level and the 2x2 / 1x1 tail of a 256x256 */
        struct { const char *what; DXGI_FORMAT fmt; UINT64 w; UINT h; UINT16 mips; UINT sub, x, y; D3D12_BOX box; } t[] = {
            { "box inside the mip", DXGI_FORMAT_BC1_UNORM, 24, 24, 3, 0, 0, 0, { 4, 4, 0, 16, 12, 1 } },
            { "block-aligned box over the 6x6 level at 0,0", DXGI_FORMAT_BC1_UNORM, 24, 24, 3, 2, 0, 0, { 0, 0, 0, 8, 8, 1 } },
            { "the same at DstX 4", DXGI_FORMAT_BC1_UNORM, 24, 24, 3, 2, 4, 4, { 0, 0, 0, 8, 8, 1 } },
            { "4x4 box over a 2x2 mip", DXGI_FORMAT_BC1_UNORM, 256, 256, 9, 7, 0, 0, { 0, 0, 0, 4, 4, 1 } },
            { "4x4 box over a 1x1 mip", DXGI_FORMAT_BC1_UNORM, 256, 256, 9, 8, 0, 0, { 0, 0, 0, 4, 4, 1 } },
            { "box over an RGBA8 mip (not clipped)", DXGI_FORMAT_R8G8B8A8_UNORM, 8, 8, 4, 3, 0, 0, { 0, 0, 0, 4, 4, 1 } },
            { "DstX beyond the mip (left as given)", DXGI_FORMAT_BC1_UNORM, 24, 24, 3, 2, 8, 0, { 0, 0, 0, 4, 4, 1 } },
        };
        unsigned k;
        for (k = 0; k < sizeof t / sizeof t[0]; k++) {
            D3D12_RESOURCE_DESC d; D3D12_PLACED_SUBRESOURCE_FOOTPRINT lay; UINT rows; UINT64 rsz, tot; struct mad_resource tex, buf; struct mad_list l;
            D3D12_TEXTURE_COPY_LOCATION dst, srcl;
            memset(&d, 0, sizeof d); d.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D; d.Width = t[k].w; d.Height = t[k].h; d.DepthOrArraySize = 1; d.MipLevels = t[k].mips; d.Format = t[k].fmt;
            d.SampleDesc.Count = 1;
            memset(&tex, 0, sizeof tex); memset(&buf, 0, sizeof buf); memset(&l, 0, sizeof l);
            tex.texture = &tex; tex.name = "tex"; tex.desc = d; buf.size = 1 << 24;
            device_GetCopyableFootprints(0, &d, t[k].sub, 1, 0, &lay, &rows, &rsz, &tot);
            memset(&dst, 0, sizeof dst); memset(&srcl, 0, sizeof srcl);
            dst.pResource = (ID3D12Resource *)&tex; dst.SubresourceIndex = t[k].sub; srcl.pResource = (ID3D12Resource *)&buf; srcl.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; srcl.PlacedFootprint = lay;
            list_CopyTextureRegion((ID3D12GraphicsCommandList *)&l, &dst, t[k].x, t[k].y, 0, &srcl, &t[k].box);
            printf("BOX %s: extent %ux%u at %u,%u row %u rows %u off %llu\n", t[k].what, l.cmds[0].u.bt.w, l.cmds[0].u.bt.h, l.cmds[0].u.bt.x, l.cmds[0].u.bt.y, l.cmds[0].u.bt.row,
                   l.cmds[0].u.bt.rows, (unsigned long long)l.cmds[0].u.bt.off);
        }
    }
    printf("clip log lines so far: %d\n", nlog);
    return 0;
}
'''
cc = os.environ.get("CC") or shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
if not cc:
    print("SKIP: no C compiler for the footprint harness")
else:
    with tempfile.TemporaryDirectory() as tmp:
        c = Path(tmp) / "fp.c"
        c.write_text(harness)
        exe = Path(tmp) / "fp"
        b = subprocess.run([cc, "-std=gnu11", "-Wall", "-Wno-unused-function", "-Wno-unused-variable", "-o", str(exe), str(c)], capture_output=True, text=True)
        check("the real footprint, mip, subresource and copy code compiles against fakes (-Wall)", b.returncode == 0 and "warning" not in b.stderr, b.stderr[-2500:])
        if b.returncode == 0:
            out = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60).stdout
            check("differential: every field of every layout but Footprint.Width / Height is what the old function gave; those are rounded up to a multiple of 4 for BC formats and equal for the rest",
                  "layouts: 72 cases, 0 field mismatches (offset/format/depth/pitch/rows/rowsize/total), 0 footprint size faults" in out, out[:700])
            check("copies: all recorded uploads have the mip's own extent and the old pitch, offset and rows",
                  "0 not the mip's own extent / old pitch, offset, rows" in out and "BADCOPY" not in out, out[:1200])
            check("BC1 256x256: 256x256 with 64 rows at 0, the 4x4 tail mip at 6, a 2x2 mip (7) and a 1x1 mip (8) both 4x4 with one 8-byte row and pitch 256",
                  "BC1 256x256: s0 256x256 rows 64; s6 4x4 rows 1; s7 4x4 rows 1 rowbytes 8 pitch 256; s8 4x4 rows 1" in out, out)
            check("BC7 100x50: 100x52 with 13 rows of 400 bytes; the next levels 52x28 and 28x12 (12 is already a multiple of 4)", "BC7 100x50: s0 100x52 rows 13 rowbytes 400; s1 52x28; s2 28x12" in out, out)
            check("an uncompressed 2x2 texture's 1x1 mip stays 1x1", "RGBA8 2x2: s0 2x2; s1 1x1 rows 1" in out, out)
            check("a box inside the mip is untouched (12x8; the source offset moved by the box's own top and left, rows from the footprint)",
                  "BOX box inside the mip: extent 12x8 at 0,0 row 256 rows 6 off 264" in out, out)
            check("a block-aligned box over the 6x6 level is clipped to 6x6 at 0,0 and to 2x2 at DstX 4; a 4x4 box over the 2x2 and 1x1 mips to 2x2 and 1x1",
                  "BOX block-aligned box over the 6x6 level at 0,0: extent 6x6" in out and "BOX the same at DstX 4: extent 2x2 at 4,4" in out
                  and "BOX 4x4 box over a 2x2 mip: extent 2x2" in out and "BOX 4x4 box over a 1x1 mip: extent 1x1" in out, out)
            check("an uncompressed texture's box is never clipped, and a destination origin beyond the mip is left as given",
                  "BOX box over an RGBA8 mip (not clipped): extent 4x4" in out and "BOX DstX beyond the mip (left as given): extent 4x4 at 8,0" in out, out)
            check("clip lines: at most four in all", "clip log lines so far: 4" in out, out[-200:])

# ---- 3. rule, fixtures, CI
rules = (root / "build/tools/autotune_rules.py").read_text()
r0 = rules.find('"id": "d3d12-footprint-block-align"')
rb = rules[r0:rules.index("\n    },\n", r0)] if r0 >= 0 else ""
check("the auto-tune rule is fixed in 652 (no line, no noFix) and its two fixtures (builds 630 and 634) are there",
      '"fixedIn": 652' in rb and '"lines": []' in rb and '"noFix"' not in rb
      and (root / "tests/fixtures/autotune/satisfactory-footprint-block-assert-2026-11-05.expect").is_file()
      and (root / "tests/fixtures/autotune/satisfactory-footprint-block-assert-2026-11-08.expect").is_file())
ci = (root / ".github/workflows/build-ipa.yml").read_text()
check("CI runs this check", "python3 tests/host/check-d3d12-footprint-block-align.py" in ci)

print("PASS: d3d12 footprint block align" if ok else "FAILED: d3d12 footprint block align")
sys.exit(0 if ok else 1)
