#!/usr/bin/env python3
"""God of War below-1080p darkening: a GPU trace of the frame and the values
in the game's small float targets, plus one render-pass experiment. All of it
is OFF unless its switch is set (game file: `env.NAME = value`).

MADEIRA_GPU_TRACE=T[,S[,N]]   T seconds after the first presented frame:
    * `[gpu-trace] F<n> ...` -- one whole frame, encoder by encoder: render
      passes (attachments: texture id, Metal pixel format, size, load/store
      action, draw count, the fragment functions used), compute dispatches
      (Metal function -- DXMT names it shader_<first 8 hex digits of the DXBC
      SHA-1>, the same name tools/patch-airconv-gow-experiments.py's
      MADEIRA_SHADER_DUMP files carry --, grid, threadgroup size, and the
      textures/buffers that dispatch made resident with read/write usage) and
      blits. The DXBC of every compute and pixel shader the frame uses is
      written to Documents/shader-dump/<sha1>.<type>.dxbc (airconv keeps a
      registry of live shaders while this switch is set). `HAZARD` lines flag what D3D11's runtime would have unbound
      and DXMT (our pin, before upstream 2b688f8/d079b56, 2026-04-07) does
      not: a render-pass attachment that is also bound as a shader resource,
      or one texture made resident read-only and writable for the same
      dispatch (D3D11 nulls the SRV, the shader reads 0; DXVK does the same).
      A render pass's `uav:` list names what its pixel shaders can write
      (UAVs): DXMT keeps pixel-shader UAVs bound across OMSetRenderTargets,
      D3D11 unbinds them (upstream fix 7d59ed1, 2026-04-08), so a UAV seen in
      an unrelated pass is a stale binding that D3D would drop writes to.
      Then the contents of every tracked float texture (all mip
      levels): count, NaN, Inf, zeros, exact 1.0 and 32.0, min/max/mean, the
      mean of the last row and column, the values of tiny levels, and where
      the values are: grids up to 4096 texels row by row, larger levels as a
      16x9 map of block means (a target only partly written, e.g. scaled for
      the 1080p start-up size, shows up there). N such frames (default 3),
      60 s apart.
    * `[gpu-val] ...` -- every S seconds (default 2) the same statistics for
      the small tracked textures only (<= 40000 texels: the 1/8 and 1/32
      luminance grids, the 128x64 / 64x32 pyramids, 1x1s, the 32x32 RGBA16F
      the game reads back), level 0 and the last level, for 15 minutes.
    Tracked: every 2D texture created with a 32-bit float format (R32F,
    RG32F, RGBA32F; up to 2048x2048) and small 16-bit float ones (<= 4096
    texels); at most 64 textures / 160 MB, retained for the session.
    `[rb-src]` names the source buffer and offset of each distinct small
    (<= 256 byte) GPU-to-CPU readback, so the exposure value's spikes can be
    told apart from a second readback of another buffer.
    Costs: a GPU wait of a few ms per value dump on the submit thread.

MADEIRA_BORDER=black   Experiment: samplers DXMT gave the opaque-white border
    (its stand-in for every D3D border color Metal lacks, e.g. God of War's
    (32,32,32,32) sampler, created right before the luminance targets) get a
    transparent black border. A/B for "out-of-range border samples matter":
    D3D returns 32 there, DXMT 1.0, this 0.0. `[border]` logs such samplers
    (first 8) whether or not the switch is set.

MADEIRA_RP_LOAD=1   Experiment: render-pass attachments never start from or
    end in DontCare (load -> Load, store -> Store; memoryless and resolve
    attachments untouched). Rules out stale tile memory on Apple's tile-based
    GPU (a region a pass did not draw but stored) -- the shape of "only below
    1080p, wherever the sky is". Always correct, only slower.

Needs tools/patch-winemetal-readback-census.py and -gpu-span.py first.
Native only; not in the i386 farm key. Idempotent; fails by name if an anchor
moves. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: gpu trace"
NAME = "patch-winemetal-gpu-trace"

HELPERS = r'''/* madeira-bcd: gpu trace (tools/patch-winemetal-gpu-trace.py) -- opt-in GPU
 * frame trace, float-target value dumps and the MADEIRA_RP_LOAD experiment. */
#include <math.h>
#include <pthread.h>
#include <stdarg.h>
#include <time.h>
static pthread_mutex_t madeira_gt_lock = PTHREAD_MUTEX_INITIALIZER;
static int madeira_gt_mode = -1;            /* -1 unread, 0 off, 1 on */
static double madeira_gt_start = 0, madeira_gt_every = 2.0;
static int madeira_gt_full_left = 3, madeira_gt_small_left = 450;
static double madeira_gt_t0, madeira_gt_next_full, madeira_gt_next_small;
static obj_handle_t madeira_gt_present_cb;
static volatile int madeira_gt_tracing;
static unsigned madeira_gt_frame, madeira_gt_lines;

static int madeira_gt_on(void) {
  if (madeira_gt_mode < 0) {
    const char *e = getenv("MADEIRA_GPU_TRACE");
    double t = 0, s = 2.0;
    int n = 3;
    if (e && *e) {
      t = atof(e);
      const char *c = strchr(e, ',');
      if (c) {
        s = atof(c + 1);
        c = strchr(c + 1, ',');
        if (c) n = atoi(c + 1);
      }
    }
    if (s < 0.25) s = 0.25;
    if (n < 0) n = 0;
    madeira_gt_start = t;
    madeira_gt_every = s;
    madeira_gt_full_left = n;
    madeira_gt_small_left = (int)(900.0 / s);
    madeira_gt_mode = t > 0 ? 1 : 0;
    if (madeira_gt_mode)
      fprintf(stderr, "[gpu-trace] madeira-bcd on: frame traces from %.0f s after the first frame (%d, 60 s apart), "
                      "value dumps every %.2f s (MADEIRA_GPU_TRACE)\n", t, n, s);
  }
  return madeira_gt_mode;
}

static int madeira_rp_load_on(void) {
  static int mode = -1;
  if (mode < 0) {
    const char *e = getenv("MADEIRA_RP_LOAD");
    mode = e && e[0] == '1';
    if (mode)
      fprintf(stderr, "[rp-load] madeira-bcd render passes: DontCare load/store -> Load/Store (MADEIRA_RP_LOAD=1)\n");
  }
  return mode;
}

/* MADEIRA_BORDER=black: samplers DXMT created with the white border (its
 * stand-in for D3D border colors Metal cannot express -- God of War's
 * (32,32,32,32) -- become white) get a transparent black border instead. An
 * A/B of out-of-range border sampling, 1.0 vs 0.0; the D3D value 32 needs the
 * PE d3d11.dll. */
static int madeira_border_black(void) {
  static int mode = -1;
  if (mode < 0) {
    const char *e = getenv("MADEIRA_BORDER");
    mode = e && !strcmp(e, "black");
    if (mode)
      fprintf(stderr, "[border] madeira-bcd white sampler borders -> transparent black (MADEIRA_BORDER=black)\n");
  }
  return mode;
}

static void madeira_note_border_sampler(const struct WMTSamplerInfo *info, MTLSamplerDescriptor *d) {
  static unsigned n;
  /* DXMT creates every sampler twice: the D3D one, then a cube variant with
   * the same filters and all-border (linear) or all-edge addressing. */
  static _Thread_local struct WMTSamplerInfo prev;
  static _Thread_local int have_prev;
  int all_border = info->s_address_mode == WMTSamplerAddressModeClampToBorderColor &&
                   info->t_address_mode == WMTSamplerAddressModeClampToBorderColor &&
                   info->r_address_mode == WMTSamplerAddressModeClampToBorderColor;
  int prev_all_border = prev.s_address_mode == WMTSamplerAddressModeClampToBorderColor &&
                        prev.t_address_mode == WMTSamplerAddressModeClampToBorderColor &&
                        prev.r_address_mode == WMTSamplerAddressModeClampToBorderColor;
  int cube_variant = have_prev && all_border && !prev_all_border && prev.min_filter == info->min_filter &&
                     prev.mag_filter == info->mag_filter && prev.mip_filter == info->mip_filter &&
                     prev.compare_function == info->compare_function;
  prev = *info;
  have_prev = 1;
  if (info->border_color != WMTSamplerBorderColorOpaqueWhite) return;
  if (info->s_address_mode != WMTSamplerAddressModeClampToBorderColor &&
      info->t_address_mode != WMTSamplerAddressModeClampToBorderColor &&
      info->r_address_mode != WMTSamplerAddressModeClampToBorderColor)
    return;
  if (madeira_border_black()) d.borderColor = MTLSamplerBorderColorTransparentBlack;
  if (cube_variant) return;
  if (++n <= 8 || (n & 255) == 0)
    fprintf(stderr, "[border] madeira-bcd white-border sampler #%u: address %u/%u/%u min/mag/mip %u/%u/%u compare %u%s\n",
            n, (unsigned)info->s_address_mode, (unsigned)info->t_address_mode, (unsigned)info->r_address_mode,
            (unsigned)info->min_filter, (unsigned)info->mag_filter, (unsigned)info->mip_filter,
            (unsigned)info->compare_function, madeira_border_black() ? " -> black" : "");
}

static double madeira_gt_now(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec + ts.tv_nsec / 1e9;
}

static unsigned long madeira_gt_id(id obj) {
  return ((unsigned long)(uintptr_t)obj >> 4) & 0xfffff;
}

/* Metal function name per pipeline state (compute: shader_<sha8>, render: the
 * fragment function, ps_<sha8>_<variant>). */
#define MADEIRA_GT_PSO_CAP 65536u
struct madeira_gt_pso { uint64_t pso; char name[24]; };
static struct madeira_gt_pso *madeira_gt_psos;

static void madeira_gt_note_pso(obj_handle_t pso, obj_handle_t fn) {
  if (!madeira_gt_on() || !pso || !fn) return;
  char n[24];
  @autoreleasepool {
    const char *s = [[(id<MTLFunction>)fn name] UTF8String];
    snprintf(n, sizeof n, "%s", s ? s : "?");
  }
  pthread_mutex_lock(&madeira_gt_lock);
  if (!madeira_gt_psos) madeira_gt_psos = calloc(MADEIRA_GT_PSO_CAP, sizeof *madeira_gt_psos);
  if (madeira_gt_psos) {
    uint64_t h = ((uint64_t)pso >> 4) * 2654435761u;
    for (unsigned i = 0; i < 64; i++) {
      struct madeira_gt_pso *e = &madeira_gt_psos[(h + i) % MADEIRA_GT_PSO_CAP];
      if (!e->pso || e->pso == (uint64_t)pso) {
        e->pso = (uint64_t)pso;
        snprintf(e->name, sizeof e->name, "%s", n);
        break;
      }
    }
  }
  pthread_mutex_unlock(&madeira_gt_lock);
}

static const char *madeira_gt_pso_name(obj_handle_t pso) {
  const char *r = "?";
  pthread_mutex_lock(&madeira_gt_lock);
  if (madeira_gt_psos && pso) {
    uint64_t h = ((uint64_t)pso >> 4) * 2654435761u;
    for (unsigned i = 0; i < 64; i++) {
      struct madeira_gt_pso *e = &madeira_gt_psos[(h + i) % MADEIRA_GT_PSO_CAP];
      if (!e->pso) break;
      if (e->pso == (uint64_t)pso) { r = e->name; break; }
    }
  }
  pthread_mutex_unlock(&madeira_gt_lock);
  return r;
}

/* Tracked float textures (retained for the session). */
#define MADEIRA_GT_TEX_CAP 64u
static id<MTLTexture> madeira_gt_tex[MADEIRA_GT_TEX_CAP];
static unsigned madeira_gt_ntex;
static unsigned long long madeira_gt_tex_bytes;

/* bytes per texel, channels, kind (0 f32, 1 f16, 2 u32); 0 = not tracked */
static unsigned madeira_gt_fmt(MTLPixelFormat pf, unsigned *nc, unsigned *kind) {
  switch (pf) {
  case MTLPixelFormatR32Float: *nc = 1; *kind = 0; return 4;
  case MTLPixelFormatRG32Float: *nc = 2; *kind = 0; return 8;
  case MTLPixelFormatRGBA32Float: *nc = 4; *kind = 0; return 16;
  case MTLPixelFormatR16Float: *nc = 1; *kind = 1; return 2;
  case MTLPixelFormatRG16Float: *nc = 2; *kind = 1; return 4;
  case MTLPixelFormatRGBA16Float: *nc = 4; *kind = 1; return 8;
  case MTLPixelFormatR32Uint: *nc = 1; *kind = 2; return 4;
  default: return 0;
  }
}

static void madeira_gt_note_texture(id<MTLTexture> t) {
  if (!madeira_gt_on() || !t) return;
  unsigned nc, kind, bpp = madeira_gt_fmt(t.pixelFormat, &nc, &kind);
  if (!bpp || t.textureType != MTLTextureType2D || t.storageMode == MTLStorageModeMemoryless) return;
  unsigned long long texels = (unsigned long long)t.width * t.height;
  if (texels > 2048ull * 2048ull) return;
  if (kind == 1 && texels > 4096) return;          /* only small 16-bit float targets */
  if (kind == 2 && texels > 65536) return;
  unsigned long long bytes = texels * bpp * (t.mipmapLevelCount > 1 ? 2 : 1);
  pthread_mutex_lock(&madeira_gt_lock);
  if (madeira_gt_ntex < MADEIRA_GT_TEX_CAP && madeira_gt_tex_bytes + bytes <= (160ull << 20)) {
    madeira_gt_tex[madeira_gt_ntex++] = [t retain];
    madeira_gt_tex_bytes += bytes;
    fprintf(stderr, "[gpu-trace] madeira-bcd tracking texture #%u t%05lx fmt %lu %lux%lu mips %lu usage 0x%lx\n",
            madeira_gt_ntex, madeira_gt_id(t), (unsigned long)t.pixelFormat, (unsigned long)t.width,
            (unsigned long)t.height, (unsigned long)t.mipmapLevelCount, (unsigned long)t.usage);
  }
  pthread_mutex_unlock(&madeira_gt_lock);
}

static float madeira_gt_half(uint16_t h) {
  uint32_t s = (uint32_t)(h & 0x8000u) << 16, e = (h >> 10) & 0x1fu, m = h & 0x3ffu, f;
  if (e == 0) {
    if (!m) f = s;
    else {
      e = 113;
      while (!(m & 0x400u)) { m <<= 1; e--; }
      f = s | (e << 23) | ((m & 0x3ffu) << 13);
    }
  } else if (e == 31) {
    f = s | 0x7f800000u | (m << 13);
  } else {
    f = s | ((e + 112) << 23) | (m << 13);
  }
  float r;
  memcpy(&r, &f, 4);
  return r;
}

static unsigned long madeira_gt_base(id<MTLTexture> t) {
  return t ? madeira_gt_id(t.parentTexture ? t.parentTexture : t) : 0;
}

static void madeira_gt_texdesc(char *out, size_t n, id<MTLTexture> t) {
  if (!t) { snprintf(out, n, "-"); return; }
  id<MTLTexture> base = t.parentTexture ? t.parentTexture : t;
  snprintf(out, n, "t%05lx%s/%lu %lux%lu", madeira_gt_id(base), t.parentTexture ? "v" : "",
           (unsigned long)t.pixelFormat, (unsigned long)t.width, (unsigned long)t.height);
}

static void madeira_gt_line(const char *fmt, ...) {
  if (madeira_gt_lines >= 5000) {
    if (madeira_gt_lines++ == 5000) fprintf(stderr, "[gpu-trace] F%u ... (trace capped at 5000 lines)\n", madeira_gt_frame);
    return;
  }
  madeira_gt_lines++;
  char buf[1024];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  fprintf(stderr, "[gpu-trace] F%u %s\n", madeira_gt_frame, buf);
}

static const char *madeira_gt_la(unsigned a) { return a == 0 ? "dontcare" : a == 1 ? "load" : a == 2 ? "clear" : "?"; }
static const char *madeira_gt_sa(unsigned a) {
  return a == 0 ? "dontcare" : a == 1 ? "store" : a == 2 ? "resolve" : a == 3 ? "store+resolve" : "?";
}

/* attachments of the traced render pass, for the SRV hazard check */
static unsigned long madeira_gt_ratt[9];
static unsigned madeira_gt_natt, madeira_gt_rhaz, madeira_gt_ruav;
static char madeira_gt_rhazd[160], madeira_gt_ruavd[300];

static void madeira_gt_render_pass(const struct WMTRenderPassInfo *info, obj_handle_t enc) {
  if (!madeira_gt_tracing || !info) return;
  madeira_gt_natt = 0;
  madeira_gt_rhaz = 0;
  madeira_gt_rhazd[0] = 0;
  madeira_gt_ruav = 0;
  madeira_gt_ruavd[0] = 0;
  for (unsigned i = 0; i < 8; i++)
    if (info->colors[i].texture) madeira_gt_ratt[madeira_gt_natt++] = madeira_gt_base((id<MTLTexture>)info->colors[i].texture);
  if (info->depth.texture) madeira_gt_ratt[madeira_gt_natt++] = madeira_gt_base((id<MTLTexture>)info->depth.texture);
  char b[1024], d[96];
  int o = snprintf(b, sizeof b, "RP e%05lx rt=%ux%u", madeira_gt_id((id)enc), info->render_target_width,
                   info->render_target_height);
  for (unsigned i = 0; i < 8 && o < (int)sizeof b - 128; i++) {
    if (!info->colors[i].texture) continue;
    madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)info->colors[i].texture);
    o += snprintf(b + o, sizeof b - o, " c%u=%s L%u %s/%s", i, d, (unsigned)info->colors[i].level,
                  madeira_gt_la(info->colors[i].load_action), madeira_gt_sa(info->colors[i].store_action));
  }
  if (info->depth.texture && o < (int)sizeof b - 128) {
    madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)info->depth.texture);
    o += snprintf(b + o, sizeof b - o, " depth=%s %s/%s", d, madeira_gt_la(info->depth.load_action),
                  madeira_gt_sa(info->depth.store_action));
  }
  if (info->stencil.texture && o < (int)sizeof b - 128) {
    snprintf(b + o, sizeof b - o, " stencil %s/%s", madeira_gt_la(info->stencil.load_action),
             madeira_gt_sa(info->stencil.store_action));
  }
  madeira_gt_line("%s", b);
}

/* The DXBC of every compute / pixel shader a traced frame uses is written to
 * Documents/shader-dump by airconv (tools/patch-airconv-gow-experiments.py
 * keeps a registry of live shaders while MADEIRA_GPU_TRACE is set). */
extern int madeira_airconv_dump_function(const char *fn);
static unsigned madeira_gt_dumped, madeira_gt_unknown;

static void madeira_gt_dump_fn(const char *name) {
  static char seen[4096][12];
  static unsigned nseen;
  if (!name || name[0] == '?') return;
  const char *u = strchr(name, '_');
  if (!u) return;
  char key[12];
  snprintf(key, sizeof key, "%.11s", u + 1);
  for (unsigned i = 0; i < nseen; i++)
    if (!strcmp(seen[i], key)) return;
  if (nseen < 4096) snprintf(seen[nseen++], sizeof seen[0], "%s", key);
  int r = madeira_airconv_dump_function(name);
  if (r > 0) madeira_gt_dumped++;
  else if (r < 0) madeira_gt_unknown++;
}

/* per render encoder: draws and fragment functions, printed at endEncoding */
static obj_handle_t madeira_gt_renc;
static unsigned madeira_gt_rdraws, madeira_gt_rnames;
static char madeira_gt_rname[6][24];
static double madeira_gt_rvp[4];

static void madeira_gt_walk(int kind, obj_handle_t enc, const struct wmtcmd_base *c) {
  if (!madeira_gt_tracing) return;
  static obj_handle_t cenc;
  static char cname[24] = "?";
  static uint64_t tg[3];
  static char res[640];
  static int reso;
  static unsigned long hb[16];
  static unsigned hu[16], nh;
  if (kind == 0 && enc != madeira_gt_renc) {
    madeira_gt_renc = enc;
    madeira_gt_rdraws = madeira_gt_rnames = 0;
    madeira_gt_rvp[0] = madeira_gt_rvp[1] = madeira_gt_rvp[2] = madeira_gt_rvp[3] = -1;
  }
  if (kind == 1 && enc != cenc) { cenc = enc; reso = 0; res[0] = 0; nh = 0; snprintf(cname, sizeof cname, "?"); }
  for (; c; c = c->next.ptr) {
    if (kind == 0) {
      switch ((enum WMTRenderCommandType)c->type) {
      case WMTRenderCommandSetPSO: {
        const char *n = madeira_gt_pso_name(((const struct wmtcmd_render_setpso *)c)->pso);
        madeira_gt_dump_fn(n);
        unsigned i = 0;
        for (; i < madeira_gt_rnames; i++) if (!strcmp(madeira_gt_rname[i], n)) break;
        if (i == madeira_gt_rnames && madeira_gt_rnames < 6) snprintf(madeira_gt_rname[madeira_gt_rnames++], 24, "%s", n);
        break;
      }
      case WMTRenderCommandUseResource: {
        const struct wmtcmd_render_useresource *u = (const struct wmtcmd_render_useresource *)c;
        id r = (id)u->resource;
        if (!r) break;
        if (u->usage & 2) {   /* writable in a render pass: a pixel-shader UAV */
          if (madeira_gt_ruav++ < 4) {
            char d[96];
            size_t l = strlen(madeira_gt_ruavd);
            if ([r conformsToProtocol:@protocol(MTLTexture)])
              madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)r);
            else
              snprintf(d, sizeof d, "b%05lx/%lu", madeira_gt_id(r), (unsigned long)[(id<MTLBuffer>)r length]);
            snprintf(madeira_gt_ruavd + l, sizeof madeira_gt_ruavd - l, " %s", d);
          }
        }
        if (![r conformsToProtocol:@protocol(MTLTexture)]) break;
        unsigned long b = madeira_gt_base((id<MTLTexture>)r);
        for (unsigned i = 0; i < madeira_gt_natt; i++)
          if (madeira_gt_ratt[i] == b) {
            if (!madeira_gt_rhaz++) {
              char d[96];
              madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)r);
              snprintf(madeira_gt_rhazd, sizeof madeira_gt_rhazd, "%s:%s%s", d, (u->usage & 1) ? "r" : "",
                       (u->usage & 2) ? "w" : "");
            }
            break;
          }
        break;
      }
      case WMTRenderCommandSetViewports: {
        const struct wmtcmd_render_setviewports *v = (const struct wmtcmd_render_setviewports *)c;
        const struct WMTViewport *vp = v->viewports.ptr;
        if (vp && v->viewport_count && madeira_gt_rvp[2] < 0) {
          madeira_gt_rvp[0] = vp->originX; madeira_gt_rvp[1] = vp->originY;
          madeira_gt_rvp[2] = vp->width; madeira_gt_rvp[3] = vp->height;
        }
        break;
      }
      case WMTRenderCommandDraw: case WMTRenderCommandDrawIndexed: case WMTRenderCommandDrawIndirect:
      case WMTRenderCommandDrawIndexedIndirect: case WMTRenderCommandDrawMeshThreadgroups:
      case WMTRenderCommandDrawMeshThreadgroupsIndirect: case WMTRenderCommandDXMTGeometryDraw:
      case WMTRenderCommandDXMTGeometryDrawIndexed: case WMTRenderCommandDXMTGeometryDrawIndirect:
      case WMTRenderCommandDXMTGeometryDrawIndexedIndirect: case WMTRenderCommandDXMTTessellationMeshDraw:
      case WMTRenderCommandDXMTTessellationMeshDrawIndexed: case WMTRenderCommandDXMTTessellationMeshDrawIndirect:
      case WMTRenderCommandDXMTTessellationMeshDrawIndexedIndirect:
        madeira_gt_rdraws++;
        break;
      default:
        break;
      }
    } else if (kind == 1) {
      switch ((enum WMTComputeCommandType)c->type) {
      case WMTComputeCommandSetPSO: {
        const struct wmtcmd_compute_setpso *p = (const struct wmtcmd_compute_setpso *)c;
        snprintf(cname, sizeof cname, "%s", madeira_gt_pso_name(p->pso));
        madeira_gt_dump_fn(cname);
        tg[0] = p->threadgroup_size.width; tg[1] = p->threadgroup_size.height; tg[2] = p->threadgroup_size.depth;
        break;
      }
      case WMTComputeCommandUseResource: {
        const struct wmtcmd_compute_useresource *u = (const struct wmtcmd_compute_useresource *)c;
        id r = (id)u->resource;
        char d[96];
        if (!r || reso > (int)sizeof res - 100) break;
        if ([r conformsToProtocol:@protocol(MTLTexture)]) {
          madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)r);
          if (nh < 16) { hb[nh] = madeira_gt_base((id<MTLTexture>)r); hu[nh] = (unsigned)u->usage; nh++; }
        } else
          snprintf(d, sizeof d, "b%05lx/%lu", madeira_gt_id(r), (unsigned long)[(id<MTLBuffer>)r length]);
        reso += snprintf(res + reso, sizeof res - reso, " %s:%s%s", d, (u->usage & 1) ? "r" : "",
                         (u->usage & 2) ? "w" : "");
        break;
      }
      case WMTComputeCommandDispatch: case WMTComputeCommandDispatchThreads: {
        const struct wmtcmd_compute_dispatch *p = (const struct wmtcmd_compute_dispatch *)c;
        madeira_gt_line("CS %s %s=%llux%llux%llu tg=%llux%llux%llu new:%s", cname,
                        c->type == WMTComputeCommandDispatch ? "groups" : "threads",
                        (unsigned long long)p->size.width, (unsigned long long)p->size.height,
                        (unsigned long long)p->size.depth, (unsigned long long)tg[0], (unsigned long long)tg[1],
                        (unsigned long long)tg[2], reso ? res : " -");
        for (unsigned a = 0; a < nh; a++)
          for (unsigned b2 = a + 1; b2 < nh; b2++)
            if (hb[a] == hb[b2] && ((hu[a] & 2) != (hu[b2] & 2)))
              madeira_gt_line("HAZARD CS %s: t%05lx bound both read-only and writable in one dispatch -- D3D11 "
                              "unbinds the SRV (reads 0), DXMT does not", cname, hb[a]);
        reso = 0; res[0] = 0; nh = 0;
        break;
      }
      case WMTComputeCommandDispatchIndirect:
        madeira_gt_line("CS %s indirect tg=%llux%llux%llu new:%s", cname, (unsigned long long)tg[0],
                        (unsigned long long)tg[1], (unsigned long long)tg[2], reso ? res : " -");
        reso = 0; res[0] = 0; nh = 0;
        break;
      default:
        break;
      }
    } else {
      char s[96], d[96];
      switch ((enum WMTBlitCommandType)c->type) {
      case WMTBlitCommandCopyFromTextureToTexture: {
        const struct wmtcmd_blit_copy_from_texture_to_texture *b = (const void *)c;
        madeira_gt_texdesc(s, sizeof s, (id<MTLTexture>)b->src);
        madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)b->dst);
        madeira_gt_line("BLIT tex %s L%u -> %s L%u size %llux%llu", s, b->src_level, d, b->dst_level,
                        (unsigned long long)b->src_size.width, (unsigned long long)b->src_size.height);
        break;
      }
      case WMTBlitCommandCopyFromTextureToBuffer: {
        const struct wmtcmd_blit_copy_from_texture_to_buffer *b = (const void *)c;
        madeira_gt_texdesc(s, sizeof s, (id<MTLTexture>)b->src);
        madeira_gt_line("BLIT readback %s L%u %llux%llu -> b%05lx+%llu", s, b->level,
                        (unsigned long long)b->size.width, (unsigned long long)b->size.height,
                        madeira_gt_id((id)b->dst), (unsigned long long)b->offset);
        break;
      }
      case WMTBlitCommandCopyFromBufferToBuffer: {
        const struct wmtcmd_blit_copy_from_buffer_to_buffer *b = (const void *)c;
        madeira_gt_line("BLIT buf b%05lx+%llu -> b%05lx+%llu %llu bytes", madeira_gt_id((id)b->src),
                        (unsigned long long)b->src_offset, madeira_gt_id((id)b->dst),
                        (unsigned long long)b->dst_offset, (unsigned long long)b->copy_length);
        break;
      }
      case WMTBlitCommandCopyFromBufferToTexture: {
        const struct wmtcmd_blit_copy_from_buffer_to_texture *b = (const void *)c;
        madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)b->dst);
        madeira_gt_line("BLIT upload -> %s", d);
        break;
      }
      case WMTBlitCommandGenerateMipmaps: {
        const struct wmtcmd_blit_generate_mipmaps *b = (const void *)c;
        madeira_gt_texdesc(d, sizeof d, (id<MTLTexture>)b->texture);
        madeira_gt_line("BLIT mipgen %s", d);
        break;
      }
      default:
        break;
      }
    }
  }
}

static void madeira_gt_end_encoder(obj_handle_t enc) {
  if (!madeira_gt_tracing || enc != madeira_gt_renc) return;
  char b[256];
  int o = 0;
  b[0] = 0;
  for (unsigned i = 0; i < madeira_gt_rnames && o < (int)sizeof b - 30; i++)
    o += snprintf(b + o, sizeof b - o, " %s", madeira_gt_rname[i]);
  madeira_gt_line("RP e%05lx end: %u draws, vp0 %.0f,%.0f %.0fx%.0f, fs:%s%s%s", madeira_gt_id((id)enc), madeira_gt_rdraws,
                  madeira_gt_rvp[0], madeira_gt_rvp[1], madeira_gt_rvp[2], madeira_gt_rvp[3], o ? b : " -",
                  madeira_gt_ruav ? " | uav:" : "", madeira_gt_ruav ? madeira_gt_ruavd : "");
  if (madeira_gt_rhaz)
    madeira_gt_line("HAZARD RP e%05lx: an attachment is also bound as a shader resource (%s; %u times) -- D3D11 "
                    "unbinds such an SRV (reads 0), DXMT does not", madeira_gt_id((id)enc), madeira_gt_rhazd,
                    madeira_gt_rhaz);
  madeira_gt_renc = 0;
}

/* Statistics of every tracked texture (full) or of the small ones (level 0
 * and the last level), copied after the frame's command buffer. */
struct madeira_gt_job { unsigned ti, level, w, h, bpp, nc, kind; size_t off, bpr; };

static double madeira_gt_val(const struct madeira_gt_job *j, const uint8_t *p, unsigned x, unsigned y, unsigned c) {
  const uint8_t *r = p + y * j->bpr;
  if (j->kind == 0) { float f; memcpy(&f, r + (x * j->nc + c) * 4, 4); return f; }
  if (j->kind == 1) { uint16_t hv; memcpy(&hv, r + (x * j->nc + c) * 2, 2); return madeira_gt_half(hv); }
  uint32_t u;
  memcpy(&u, r + x * 4, 4);
  return u;
}

static void madeira_gt_stats(const char *tag, double t, unsigned ti, id<MTLTexture> tex, const struct madeira_gt_job *j,
                             const uint8_t *p, int full) {
  unsigned long n = 0, nan = 0, inf = 0, zero = 0, one = 0, b32 = 0;
  double mn = 0, mx = 0, sum[4] = {0, 0, 0, 0}, row = 0, col = 0;
  unsigned long cnt[4] = {0, 0, 0, 0};
  int have = 0;
  for (unsigned y = 0; y < j->h; y++)
    for (unsigned x = 0; x < j->w; x++)
      for (unsigned c = 0; c < j->nc; c++) {
        double v = madeira_gt_val(j, p, x, y, c);
        n++;
        if (v != v) { nan++; continue; }
        if (isinf(v)) { inf++; continue; }
        if (v == 0) zero++;
        if (v == 1.0) one++;
        if (v == 32.0) b32++;
        if (!have) { mn = mx = v; have = 1; }
        if (v < mn) mn = v;
        if (v > mx) mx = v;
        sum[c] += v; cnt[c]++;
        if (c == 0 && y == j->h - 1) row += v;
        if (c == 0 && x == j->w - 1) col += v;
      }
  char vals[400];
  int o = 0;
  vals[0] = 0;
  if ((unsigned long)j->w * j->h <= 16) {
    for (unsigned y = 0; y < j->h; y++)
      for (unsigned x = 0; x < j->w && o < (int)sizeof vals - 40; x++)
        for (unsigned c = 0; c < j->nc && c < 4; c++)
          o += snprintf(vals + o, sizeof vals - o, "%s%g", (x || y || c) ? (c ? "," : " ") : " vals=",
                        madeira_gt_val(j, p, x, y, c));
  }
  fprintf(stderr,
          "[%s] t=%.1f #%u t%05lx fmt %lu %lux%lu L%u %ux%u: n=%lu nan=%lu inf=%lu zero=%lu one=%lu b32=%lu min=%g "
          "max=%g mean=%g/%g/%g/%g lastrow=%g lastcol=%g%s\n",
          tag, t, ti + 1, madeira_gt_id(tex), (unsigned long)tex.pixelFormat, (unsigned long)tex.width,
          (unsigned long)tex.height, j->level, j->w, j->h, n, nan, inf, zero, one, b32, mn, mx,
          cnt[0] ? sum[0] / cnt[0] : 0, cnt[1] ? sum[1] / cnt[1] : 0, cnt[2] ? sum[2] / cnt[2] : 0,
          cnt[3] ? sum[3] / cnt[3] : 0, j->w ? row / j->w : 0, j->h ? col / j->h : 0, vals);
  if (!full || (unsigned long)j->w * j->h <= 16) return;
  /* Where the values are: small grids row by row (channel 0), larger ones as
   * a 16x9 map of block means (finite values; '-' = no finite value). */
  char line[1400];
  if ((unsigned long)j->w * j->h <= 4096 && j->w <= 128) {
    for (unsigned y = 0; y < j->h; y++) {
      o = snprintf(line, sizeof line, "[%s]   t%05lx L%u row %2u:", tag, madeira_gt_id(tex), j->level, y);
      for (unsigned x = 0; x < j->w && o < (int)sizeof line - 16; x++)
        o += snprintf(line + o, sizeof line - o, " %.4g", madeira_gt_val(j, p, x, y, 0));
      fprintf(stderr, "%s\n", line);
    }
  } else if (j->w >= 16 && j->h >= 9) {
    for (unsigned by = 0; by < 9; by++) {
      o = snprintf(line, sizeof line, "[%s]   t%05lx L%u map %u/9:", tag, madeira_gt_id(tex), j->level, by + 1);
      for (unsigned bx = 0; bx < 16; bx++) {
        unsigned x0 = bx * j->w / 16, x1 = (bx + 1) * j->w / 16, y0 = by * j->h / 9, y1 = (by + 1) * j->h / 9;
        double acc = 0;
        unsigned long k = 0;
        for (unsigned y = y0; y < y1; y++)
          for (unsigned x = x0; x < x1; x++) {
            double v = madeira_gt_val(j, p, x, y, 0);
            if (v == v && !isinf(v)) { acc += v; k++; }
          }
        if (k) o += snprintf(line + o, sizeof line - o, " %.4g", acc / k);
        else o += snprintf(line + o, sizeof line - o, " -");
      }
      fprintf(stderr, "%s\n", line);
    }
  }
}

static void madeira_gt_dump(id<MTLCommandBuffer> after, int full) {
  @autoreleasepool {
    id<MTLTexture> list[MADEIRA_GT_TEX_CAP];
    unsigned n;
    pthread_mutex_lock(&madeira_gt_lock);
    n = madeira_gt_ntex;
    for (unsigned i = 0; i < n; i++) list[i] = madeira_gt_tex[i];
    pthread_mutex_unlock(&madeira_gt_lock);
    if (!n) return;
    static struct madeira_gt_job jobs[512];
    unsigned nj = 0;
    size_t total = 0;
    for (unsigned i = 0; i < n && nj < 512; i++) {
      unsigned nc, kind, bpp = madeira_gt_fmt(list[i].pixelFormat, &nc, &kind);
      unsigned W = (unsigned)list[i].width, H = (unsigned)list[i].height, levels = (unsigned)list[i].mipmapLevelCount;
      if (!bpp || (!full && (unsigned long)W * H > 40000)) continue;
      for (unsigned L = 0; L < levels && L < 12 && nj < 512; L++) {
        if (!full && L != 0 && L != levels - 1) continue;
        struct madeira_gt_job *j = &jobs[nj++];
        j->ti = i; j->level = L; j->bpp = bpp; j->nc = nc; j->kind = kind;
        j->w = W >> L ? W >> L : 1;
        j->h = H >> L ? H >> L : 1;
        j->bpr = (size_t)j->w * bpp;
        j->off = total;
        total += (j->bpr * j->h + 255) & ~(size_t)255;
      }
    }
    if (!nj || !total) return;
    id<MTLDevice> dev = after.device;
    id<MTLBuffer> buf = [dev newBufferWithLength:total options:MTLResourceStorageModeShared];
    if (!buf) return;
    id<MTLCommandBuffer> cb = [[after commandQueue] commandBuffer];
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    for (unsigned k = 0; k < nj; k++) {
      struct madeira_gt_job *j = &jobs[k];
      [blit copyFromTexture:list[j->ti]
                     sourceSlice:0
                     sourceLevel:j->level
                    sourceOrigin:MTLOriginMake(0, 0, 0)
                      sourceSize:MTLSizeMake(j->w, j->h, 1)
                        toBuffer:buf
               destinationOffset:j->off
          destinationBytesPerRow:j->bpr
        destinationBytesPerImage:j->bpr * j->h];
    }
    [blit endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    const uint8_t *base = (const uint8_t *)[buf contents];
    double t = madeira_gt_now() - madeira_gt_t0;
    if (base)
      for (unsigned k = 0; k < nj; k++)
        madeira_gt_stats(full ? "gpu-trace" : "gpu-val", t, jobs[k].ti, list[jobs[k].ti], &jobs[k], base + jobs[k].off,
                         full);
    [buf release];
  }
}

static void madeira_gt_note_present(obj_handle_t cb) {
  if (madeira_gt_on()) madeira_gt_present_cb = cb;
}

static void madeira_gt_after_commit(obj_handle_t cb) {
  if (!madeira_gt_on() || !cb || cb != madeira_gt_present_cb) return;
  madeira_gt_present_cb = 0;
  double now = madeira_gt_now();
  if (madeira_gt_t0 == 0) {
    madeira_gt_t0 = now;
    madeira_gt_next_full = now + madeira_gt_start;
    madeira_gt_next_small = now + madeira_gt_start;
  }
  madeira_gt_frame++;
  if (madeira_gt_tracing) {
    madeira_gt_tracing = 0;
    fprintf(stderr, "[gpu-trace] F%u frame end (%u lines; %u new shader DXBC files in Documents/shader-dump, "
                    "%u names without a live shader); float targets after it:\n", madeira_gt_frame - 1,
            madeira_gt_lines, madeira_gt_dumped, madeira_gt_unknown);
    madeira_gt_dump((id<MTLCommandBuffer>)cb, 1);
    return;
  }
  if (madeira_gt_full_left > 0 && now >= madeira_gt_next_full) {
    madeira_gt_full_left--;
    madeira_gt_next_full = now + 60.0;
    madeira_gt_lines = 0;
    fprintf(stderr, "[gpu-trace] F%u frame begin (t=%.1f s)\n", madeira_gt_frame, now - madeira_gt_t0);
    madeira_gt_tracing = 1;
    return;
  }
  if (madeira_gt_small_left > 0 && now >= madeira_gt_next_small) {
    madeira_gt_small_left--;
    madeira_gt_next_small = now + madeira_gt_every;
    madeira_gt_dump((id<MTLCommandBuffer>)cb, 0);
  }
}

/* [rb-src]: where each distinct small readback comes from. */
static void madeira_gt_note_bufcopy(obj_handle_t src, uint64_t src_off, obj_handle_t dst, uint64_t dst_off, uint64_t len) {
  static struct { obj_handle_t src; uint64_t off, len; } seen[32];
  static unsigned nseen;
  if (!madeira_gt_on() || len > 256 || !src || !dst) return;
  if (((id<MTLBuffer>)dst).storageMode != MTLStorageModeShared) return;
  pthread_mutex_lock(&madeira_gt_lock);
  unsigned i = 0;
  for (; i < nseen; i++) if (seen[i].src == src && seen[i].off == src_off && seen[i].len == len) break;
  if (i == nseen && nseen < 32) {
    seen[nseen].src = src; seen[nseen].off = src_off; seen[nseen].len = len; nseen++;
    fprintf(stderr, "[rb-src] madeira-bcd small readback #%u: %llu bytes from b%05lx+%llu (storage %lu, %lu bytes) "
                    "to shared b%05lx+%llu\n", nseen, (unsigned long long)len, madeira_gt_id((id)src),
            (unsigned long long)src_off, (unsigned long)((id<MTLBuffer>)src).storageMode,
            (unsigned long)((id<MTLBuffer>)src).length, madeira_gt_id((id)dst), (unsigned long long)dst_off);
  }
  pthread_mutex_unlock(&madeira_gt_lock);
}

static NTSTATUS
_MTLCommandBuffer_commit(void *obj) {
'''

PAIRS = [
    ("static NTSTATUS\n_MTLCommandBuffer_commit(void *obj) {\n", HELPERS, 1),
    # frame boundary: the present's command buffer, then its commit
    ("  [(id<MTLCommandBuffer>)params->handle commit];\n  return STATUS_SUCCESS;\n}\n",
     "  [(id<MTLCommandBuffer>)params->handle commit];\n"
     "  madeira_gt_after_commit(params->handle);   /* madeira-bcd: gpu trace */\n"
     "  return STATUS_SUCCESS;\n}\n", 1),
    ("  if (madeira_frame_hooks_on())\n    ios_frame_encode_present(0);\n",
     "  if (madeira_frame_hooks_on())\n    ios_frame_encode_present(0);\n"
     "  madeira_gt_note_present(params->handle);   /* madeira-bcd: gpu trace */\n", 1),
    ('  madeira_log_present_cadence("presentDrawableAfterMinDuration", params->arg1);\n',
     '  madeira_log_present_cadence("presentDrawableAfterMinDuration", params->arg1);\n'
     '  madeira_gt_note_present(params->handle);   /* madeira-bcd: gpu trace */\n', 1),
    # textures and pipeline names
    ("""                (unsigned long)desc.mipmapLevelCount, (unsigned long)desc.usage);
    }
  }

  [desc release];
  return STATUS_SUCCESS;
}
""",
     """                (unsigned long)desc.mipmapLevelCount, (unsigned long)desc.usage);
    }
  }
  madeira_gt_note_texture(ret);   /* madeira-bcd: gpu trace */

  [desc release];
  return STATUS_SUCCESS;
}
""", 1),
    # The compute site is anchored on what follows the creation (dxmt d8894f4, pso-archive, rewrote the creation call itself).
    ("""  params->ret_error = (obj_handle_t)err;
  if (!err && info->binary_archive_for_serialization) {
    [(id<MTLBinaryArchive>)info->binary_archive_for_serialization addComputePipelineFunctionsWithDescriptor:descriptor
""",
     """  params->ret_error = (obj_handle_t)err;
  madeira_gt_note_pso(params->ret_pso, info->compute_function);   /* madeira-bcd: gpu trace */
  if (!err && info->binary_archive_for_serialization) {
    [(id<MTLBinaryArchive>)info->binary_archive_for_serialization addComputePipelineFunctionsWithDescriptor:descriptor
""", 1),
    ("""  params->ret_error = (obj_handle_t)err;
  if (!err && info->binary_archive_for_serialization) {
    [(id<MTLBinaryArchive>)info->binary_archive_for_serialization addRenderPipelineFunctionsWithDescriptor:descriptor
""",
     """  params->ret_error = (obj_handle_t)err;
  madeira_gt_note_pso(params->ret_pso, info->fragment_function);   /* madeira-bcd: gpu trace */
  if (!err && info->binary_archive_for_serialization) {
    [(id<MTLBinaryArchive>)info->binary_archive_for_serialization addRenderPipelineFunctionsWithDescriptor:descriptor
""", 2),
    # render pass: experiment + trace
    ("  params->ret = (obj_handle_t)[(id<MTLCommandBuffer>)params->handle renderCommandEncoderWithDescriptor:descriptor];\n",
     """  if (madeira_rp_load_on()) {   /* madeira-bcd: gpu trace -- MADEIRA_RP_LOAD experiment */
    for (unsigned i = 0; i < 8; i++) {
      MTLRenderPassColorAttachmentDescriptor *a = descriptor.colorAttachments[i];
      if (!a.texture || a.texture.storageMode == MTLStorageModeMemoryless) continue;
      if (a.loadAction == MTLLoadActionDontCare) a.loadAction = MTLLoadActionLoad;
      if (a.storeAction == MTLStoreActionDontCare) a.storeAction = MTLStoreActionStore;
    }
    if (descriptor.depthAttachment.texture && descriptor.depthAttachment.texture.storageMode != MTLStorageModeMemoryless) {
      if (descriptor.depthAttachment.loadAction == MTLLoadActionDontCare) descriptor.depthAttachment.loadAction = MTLLoadActionLoad;
      if (descriptor.depthAttachment.storeAction == MTLStoreActionDontCare) descriptor.depthAttachment.storeAction = MTLStoreActionStore;
    }
    if (descriptor.stencilAttachment.texture && descriptor.stencilAttachment.texture.storageMode != MTLStorageModeMemoryless) {
      if (descriptor.stencilAttachment.loadAction == MTLLoadActionDontCare) descriptor.stencilAttachment.loadAction = MTLLoadActionLoad;
      if (descriptor.stencilAttachment.storeAction == MTLStoreActionDontCare) descriptor.stencilAttachment.storeAction = MTLStoreActionStore;
    }
  }
  params->ret = (obj_handle_t)[(id<MTLCommandBuffer>)params->handle renderCommandEncoderWithDescriptor:descriptor];
  madeira_gt_render_pass(info, params->ret);   /* madeira-bcd: gpu trace */
""", 1),
    ("  [(id<MTLCommandEncoder>)params->handle endEncoding];\n  return STATUS_SUCCESS;\n}\n",
     "  madeira_gt_end_encoder(params->handle);   /* madeira-bcd: gpu trace */\n"
     "  [(id<MTLCommandEncoder>)params->handle endEncoding];\n  return STATUS_SUCCESS;\n}\n", 1),
    # encoder command walks
    ("  wmt_census_batch(next, 2);\n  id<MTLBlitCommandEncoder> encoder = (id<MTLBlitCommandEncoder>)params->encoder;\n",
     "  wmt_census_batch(next, 2);\n  id<MTLBlitCommandEncoder> encoder = (id<MTLBlitCommandEncoder>)params->encoder;\n"
     "  if (madeira_gt_tracing) madeira_gt_walk(2, params->encoder, next);   /* madeira-bcd: gpu trace */\n", 1),
    ("  wmt_census_batch(next, 1);\n  id<MTLComputeCommandEncoder> encoder = (id<MTLComputeCommandEncoder>)params->encoder;\n",
     "  wmt_census_batch(next, 1);\n  id<MTLComputeCommandEncoder> encoder = (id<MTLComputeCommandEncoder>)params->encoder;\n"
     "  if (madeira_gt_tracing) madeira_gt_walk(1, params->encoder, next);   /* madeira-bcd: gpu trace */\n", 1),
    ("  wmt_census_batch(next, 0);\n  id<MTLRenderCommandEncoder> encoder = (id<MTLRenderCommandEncoder>)params->encoder;\n",
     "  wmt_census_batch(next, 0);\n  id<MTLRenderCommandEncoder> encoder = (id<MTLRenderCommandEncoder>)params->encoder;\n"
     "  if (madeira_gt_tracing) madeira_gt_walk(0, params->encoder, next);   /* madeira-bcd: gpu trace */\n", 1),
    # sampler border experiment / census
    ("  sampler_desc.borderColor = (MTLSamplerBorderColor)info->border_color;\n",
     "  sampler_desc.borderColor = (MTLSamplerBorderColor)info->border_color;\n"
     "  madeira_note_border_sampler(info, sampler_desc);   /* madeira-bcd: gpu trace -- MADEIRA_BORDER */\n", 1),
    # [rb-src]
    ("        madeira_rb_buf((id<MTLBuffer>)body->dst, body->dst_offset, body->copy_length);\n",
     "        madeira_rb_buf((id<MTLBuffer>)body->dst, body->dst_offset, body->copy_length);\n"
     "      madeira_gt_note_bufcopy(body->src, body->src_offset, body->dst, body->dst_offset, body->copy_length);   /* madeira-bcd: gpu trace */\n", 1),
]


def main():
    s = PATH.read_text()
    if MARKER in s:
        print("winemetal_unix.c: already patched")
        return 0
    for need in ("madeira-bcd: readback census", "madeira-bcd: [frame] GPU timeline"):
        if need not in s:
            sys.exit(f"{NAME}: '{need}' missing -- run the earlier winemetal patches first")
    for old, new, want in PAIRS:
        n = s.count(old)
        if n != want:
            sys.exit(f"{NAME}: anchor found {n} times (want {want}) in {PATH}:\n{old}")
        s = s.replace(old, new)
    PATH.write_text(s)
    print("winemetal_unix.c: patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
