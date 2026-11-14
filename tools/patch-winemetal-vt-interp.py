#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.
r"""An experiment: Apple's VideoToolbox low-latency frame interpolation as a frame generator (iOS 26; opt-in).

VTLowLatencyFrameInterpolation makes pictures between two frames from the two pictures alone: no depth, no motion vectors, no
help from the game, and nothing but the system's own model. What nobody outside Apple has published is which sizes it takes on an
iPhone, which pixel formats, and how long one pair takes; this script exists to find that out on a device, in two ways:

  madeira_vti_probe()   No game needed (Settings > Graphics > Experimental has the button). For a ladder of sizes and for one
                        frame, three frames and "2x larger plus one frame" it makes the configuration, starts a session, runs
                        ten pairs of a test picture (a bar that moves) and writes down: taken or refused, the pixel formats,
                        how long the model took to load, the time per pair, whether the bar is where it should be in the
                        result, and whether the processor can also be put into a Metal command buffer. The lines go to the
                        caller's buffer (the app writes them to the log as `[vt-interp] probe:` and shows them).

  in a game             MADEIRA_VT_INTERP = 1 or 3 (the app exports it for a game whose own line `vt-interpolation` asks),
                        with MADEIRA_PRESENT_FX = 1: present-fx already gives winemetal the game's finished picture at the
                        game's size and presents it itself after the game's command buffer is committed. With the key, a
                        frame of the layer that is followed is handed to a worker thread instead: on the game's thread the
                        picture is copied into a pixel buffer (BGRA, or two planes of 8-bit 4:2:0, whichever the processor
                        takes) on a command buffer that is committed right behind the game's; the worker waits for that copy,
                        lets VideoToolbox make one or three pictures between the frame before and this one, and presents
                        them and then the game's own frame, each from a command buffer of its own at its own time. The game's
                        thread never waits for the model or for a drawable.

  Rules of the in-game path: one layer is followed (the first with a picture of 640x360 or more). Pictures are made only when
  the screen can show them (game frame time x the screen's top rate: 2 for one, 4 for three) and only while the worker has no
  other frame waiting, so a model that is too slow costs the extra pictures, not the game's frames. A frame-rate limit holds the
  game's own frames (the limiter on the game's thread); the pictures come on top. A session is made on a background queue (the
  model takes longer than a frame to load) and frames present as without the key until it is there. Three errors in a row, a
  picture format that is not one of the four 8-bit ones, a size the processor refuses: one line in the log says why and the
  game goes on without extra pictures. madeira_vti_set() is the in-game menu's switch, madeira_vti_status() its status.

Nothing here runs without MADEIRA_VT_INTERP: the two hooks in present-fx's code start with madeira_vti_want(), which is 0, and
present-fx itself runs only with MADEIRA_PRESENT_FX=1. The probe runs only when the app calls it.

Presenting every made picture from a command buffer of its own, committed before the next drawable is taken, is the arrangement
of Porthole's patch 0189, as in tools/patch-winemetal-present-fx.py (knowledge only: no code of Porthole is in this script).

Runs after tools/patch-winemetal-lsfg-pacing.py. Idempotent; fails by name if an anchor moved. Run from the repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: vt-interp"
NEEDS = "madeira-bcd: lsfg pacing"
PRESENT_FX = "madeira-bcd: present-fx"

# ---- declarations for present-fx's code (before its layers block) ----
A_DECL = "/* madeira-bcd: present-fx: the layers, their pictures, the scaler and the presents. */\n"
DECL = ("/* " + MARKER + " (tools/patch-winemetal-vt-interp.py): what present-fx's code below asks the VideoToolbox experiment */\n"
        "struct madeira_pfx_layer;\n"
        "static int madeira_vti_want(void);\n"
        "static int madeira_vti_poll(struct madeira_pfx_layer *e);\n"
        "static int madeira_vti_take(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px);\n"
        + A_DECL)

# ---- madeira_pfx_next: a stand-in while pictures are wanted; no real drawable while the worker still presents ----
A_POLL = "  madeira_pfx_up_init();\n  madeira_pfx_want_size(e, &ow, &oh);\n  if (ow != atomic_load_explicit(&e->rw, memory_order_relaxed) || oh != atomic_load_explicit(&e->rh, memory_order_relaxed)) {\n"
POLL = ("  int vti_busy = madeira_vti_want() ? madeira_vti_poll(e) : 0;   /* " + MARKER + ": 1 = its worker still presents frames of this session */\n"
        + A_POLL)

A_NOLOCK = "  if (pthread_mutex_trylock(&g_pfx_mu)) return enlarged ? -1 : 0;\n"
NOLOCK = ("  /* " + MARKER + ": the worker may hold the lock for a moment (it enlarges its frames too): wait for it, and never take a real\n"
          "   * drawable beside the worker's */\n"
          "  if (pthread_mutex_trylock(&g_pfx_mu) && !(vti_busy && madeira_pfx_lock())) return (enlarged || vti_busy) ? -1 : 0;\n")

A_NOTEX = "  if (!tex) return enlarged ? -1 : 0;\n"
NOTEX = "  if (!tex) return (enlarged || vti_busy) ? -1 : 0;   /* " + MARKER + " */\n"

# ---- madeira_pfx_after_commit: the frame goes to the worker ----
A_TAKE = """  @autoreleasepool {
    id<MTLCommandBuffer> out = [cb.commandQueue commandBuffer];
    if (out && px->_mode == 1) {
"""
TAKE = ("  if (madeira_vti_want() && madeira_vti_take(cb, px)) {   /* " + MARKER + ": its worker presents this frame and gives the picture back */\n"
        "    [px release];\n"
        "    return;\n"
        "  }\n"
        + A_TAKE)

# ---- the block (after the lsfg pacing block) ----
A_BLOCK = "/* lsfgp-pure-end */\n\n"
BLOCK = A_BLOCK + r'''/* madeira-bcd: vt-interp (tools/patch-winemetal-vt-interp.py). An experiment, inert unless MADEIRA_VT_INTERP is 1 or 3 (and
 * MADEIRA_PRESENT_FX=1): Apple's VideoToolbox low-latency frame interpolation (iOS 26) makes one or three pictures between every
 * two frames of the game, on a worker thread, from the picture present-fx holds. See the script for the whole arrangement. */
#import <VideoToolbox/VideoToolbox.h>
#import <CoreVideo/CoreVideo.h>
#import <CoreMedia/CoreMedia.h>
#include <TargetConditionals.h>
#ifndef MADEIRA_VTI_AVAILABLE   /* tests/host/check-vt-interp.py defines it (stand-ins have no system version) */
#define MADEIRA_VTI_AVAILABLE @available(iOS 26.0, macOS 26.0, *)
#endif
#if TARGET_OS_SIMULATOR
#define MADEIRA_VTI_BUILT 0     /* VideoToolbox's frame processors are not in the simulator's SDK */
#else
#define MADEIRA_VTI_BUILT 1
#endif

/* vti-pure-begin: plain C (tests/host/check-vt-interp.py compiles these on the host) */
/* MADEIRA_VT_INTERP: how many pictures between two frames. 1 or 3; anything else is off. */
static int madeira_vti_parse(const char *text) {
  int v = text ? atoi(text) : 0;
  return v == 1 ? 1 : v == 3 ? 3 : 0;
}
/* How many pictures this game frame gets. want: 1 or 3 (what the session was made for). dt: the game's frame time in
 * seconds. hz: the screen's top rate (0 = not known: 60). backlog: frames waiting behind this one on the worker. The
 * screen has to be able to show them all (a little slack for a game just above half or a quarter of the rate), and a
 * worker that is behind makes none, so it catches up. */
static int madeira_vti_count(int want, double dt, int hz, int backlog) {
  double shown;
  if (want < 1 || backlog > 0 || !(dt > 0.0)) return 0;
  if (hz <= 0) hz = 60;
  shown = dt * (double)hz * 1.05;   /* how many screen refreshes one game frame lasts */
  if (want >= 3 && shown >= 4.0) return 3;
  return shown >= 2.0 ? 1 : 0;
}
/* When picture i of n (i = 1..n) and the game's own frame (i = n + 1) go to the screen, in seconds after the first. */
static double madeira_vti_offset(int i, int n, double dt) {
  if (n < 1 || i <= 1) return 0.0;
  if (i > n + 1) i = n + 1;
  return dt * (double)(i - 1) / (double)(n + 1);
}
#define MADEIRA_VTI_BGRA 0x42475241u   /* 'BGRA' */
#define MADEIRA_VTI_420F 0x34323066u   /* '420f': 8-bit 4:2:0, two planes, full range */
#define MADEIRA_VTI_420V 0x34323076u   /* '420v': the same, video range */
/* The pixel format used, of those the processor takes: BGRA first, then 4:2:0 full range, then video range. 0 = none. */
static unsigned madeira_vti_pick(const unsigned *fourcc, int count) {
  static const unsigned order[3] = { MADEIRA_VTI_BGRA, MADEIRA_VTI_420F, MADEIRA_VTI_420V };
  int o, i;
  for (o = 0; o < 3; o++)
    for (i = 0; i < count; i++)
      if (fourcc[i] == order[o]) return order[o];
  return 0;
}
/* The layer formats handled (Metal's numbers: BGRA8Unorm 80, BGRA8Unorm_sRGB 81, RGBA8Unorm 70, RGBA8Unorm_sRGB 71).
 * *linear: reading such a texture gives linear values (the picture is stored sRGB-encoded). 0 = not handled. */
static int madeira_vti_layer_format(unsigned long fmt, int *linear) {
  *linear = fmt == 81 || fmt == 71;
  return fmt == 80 || fmt == 81 || fmt == 70 || fmt == 71;
}
/* A pixel format's four letters for the log. */
static const char *madeira_vti_fourcc(unsigned v, char out[8]) {
  int i;
  for (i = 0; i < 4; i++) {
    unsigned c = (v >> (24 - 8 * i)) & 0xff;
    out[i] = (c >= 32 && c < 127) ? (char)c : '?';
  }
  out[4] = 0;
  return out;
}
/* Where the test picture's bar starts in frame k (the probe): it moves `step` pixels a frame and wraps before the edge. */
static unsigned madeira_vti_bar_x(unsigned k, unsigned w, unsigned bar, unsigned step) {
  unsigned span = w > bar + 2 * step ? w - bar - 2 * step : 1;
  return step + (k * step) % span;
}
/* vti-pure-end */

static const char *g_vti_msl =
  "#include <metal_stdlib>\n"
  "using namespace metal;\n"
  "struct VtiOut { float4 pos [[position]]; float2 uv; };\n"
  "struct VtiArgs { float lin; float video; };\n"
  "vertex VtiOut vti_vs(uint vid [[vertex_id]]) {\n"
  "  VtiOut o; float2 p = float2((vid << 1) & 2, vid & 2);\n"
  "  o.pos = float4(p * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0); o.uv = p; return o;\n"
  "}\n"
  "constexpr sampler vti_s(coord::normalized, filter::linear, address::clamp_to_edge);\n"
  "static float3 vti_encode(float3 c) { c = saturate(c); return mix(c * 12.92, 1.055 * pow(c, float3(1.0 / 2.4)) - 0.055, step(0.0031308, c)); }\n"
  "static float3 vti_decode(float3 c) { c = saturate(c); return mix(c / 12.92, pow((c + 0.055) / 1.055, float3(2.4)), step(0.04045, c)); }\n"
  "static float3 vti_stored(texture2d<float> t, float2 uv, float lin) {\n"   /* the picture as it is stored: sRGB-encoded */
  "  float3 c = t.sample(vti_s, uv).rgb; return lin > 0.5 ? vti_encode(c) : saturate(c);\n"
  "}\n"
  "fragment float4 vti_copy(VtiOut in [[stage_in]], texture2d<float> t [[texture(0)]]) {\n"
  "  return float4(t.sample(vti_s, in.uv).rgb, 1.0);\n"
  "}\n"
  "fragment float4 vti_y(VtiOut in [[stage_in]], texture2d<float> t [[texture(0)]], constant VtiArgs &a [[buffer(0)]]) {\n"
  "  float y = dot(vti_stored(t, in.uv, a.lin), float3(0.2126, 0.7152, 0.0722));\n"
  "  if (a.video > 0.5) y = (16.0 + 219.0 * y) / 255.0;\n"
  "  return float4(y, 0.0, 0.0, 1.0);\n"
  "}\n"
  "fragment float4 vti_c(VtiOut in [[stage_in]], texture2d<float> t [[texture(0)]], constant VtiArgs &a [[buffer(0)]]) {\n"
  "  float3 c = vti_stored(t, in.uv, a.lin);\n"
  "  float y = dot(c, float3(0.2126, 0.7152, 0.0722));\n"
  "  float2 cc = float2((c.b - y) / 1.8556, (c.r - y) / 1.5748);\n"
  "  cc = (128.0 + (a.video > 0.5 ? 224.0 : 255.0) * cc) / 255.0;\n"
  "  return float4(cc, 0.0, 1.0);\n"
  "}\n"
  "fragment float4 vti_rgb(VtiOut in [[stage_in]], texture2d<float> ty [[texture(0)]], texture2d<float> tc [[texture(1)]],\n"
  "                        constant VtiArgs &a [[buffer(0)]]) {\n"
  "  float y = ty.sample(vti_s, in.uv).r;\n"
  "  float2 cc = tc.sample(vti_s, in.uv).rg * 255.0 - 128.0;\n"
  "  if (a.video > 0.5) { y = (y * 255.0 - 16.0) / 219.0; cc = cc / 224.0; } else { cc = cc / 255.0; }\n"
  "  float3 c = float3(y + 1.5748 * cc.y, y - 0.1873 * cc.x - 0.4681 * cc.y, y + 1.8556 * cc.x);\n"
  "  return float4(a.lin > 0.5 ? vti_decode(c) : saturate(c), 1.0);\n"
  "}\n";

#define MADEIRA_VTI_RING 3
/* One session: a processor made for one size, its pixel buffers and their Metal textures. Everything up to `refs` is
 * set once by madeira_vti_open and only read afterwards. */
struct madeira_vti_session {
  unsigned w, h, fourcc;
  unsigned long layer_fmt;
  int linear, video, planar, frames;         /* frames: 1 or 3, what the configuration was made for */
  int blit_in, blit_out;                     /* the pixel buffer's texture has the layer's own format: plain copies */
  id proc, config;                           /* VTFrameProcessor, VTLowLatencyFrameInterpolationConfiguration */
  CVPixelBufferRef src[MADEIRA_VTI_RING], dst[3];
  id<MTLTexture> src_t[MADEIRA_VTI_RING][2], dst_t[3][2];
  id<MTLTexture> out[3];                     /* the pictures made, in the layer's format and the game's size */
  id<MTLRenderPipelineState> p_in, p_y, p_c, p_out;
  _Atomic int refs;
  _Atomic int reset;                         /* the worker: a copy failed, the next frame has no frame before it */
  /* under g_vti_mu, the game's thread: which buffer the last frame went into */
  int g_last, g_valid;
  long long g_us;
  /* the worker */
  int errors;
};
static struct {
  _Atomic int want;                 /* -1 until MADEIRA_VT_INTERP was read; then 0, 1 or 3 */
  _Atomic int live;                 /* the menu's switch: -1 = as the game started (on), 0 off, 1 on */
  _Atomic int state;                /* 0 nothing yet, 1 the model is loading, 2 ready, -1 not possible (why) */
  _Atomic int outstanding;          /* frames handed to the worker and not presented yet */
  _Atomic int attempts;
  _Atomic(void *) layer;            /* the layer followed: compared, never dereferenced */
  _Atomic unsigned long long layer_abs;
  _Atomic unsigned w, h;            /* what the session is, or is being, made for */
  _Atomic unsigned long fmt;
  struct madeira_vti_session *s;    /* under g_vti_mu */
  uint64_t last_abs;                /* the followed layer's present thread */
  double dt;
  double next_free;                 /* the worker: when the last frame it presented is due */
  char why[220];
  _Atomic unsigned long long frames, generated, skip_rate, skip_busy, errors, pairs, vt_us, wait_us;
  /* the worker's one-second report, for the menu */
  uint64_t stat_abs;
  unsigned long long stat_frames, stat_generated;
  volatile double game_fps, generated_fps, pair_ms;
} g_vti = { .want = -1, .live = -1 };
static pthread_mutex_t g_vti_mu = PTHREAD_MUTEX_INITIALIZER;

static int madeira_vti_want(void) {
  int w = atomic_load_explicit(&g_vti.want, memory_order_relaxed);
  if (w >= 0) return w;
  w = madeira_vti_parse(getenv("MADEIRA_VT_INTERP"));
  if (w) {
    int ok = 0;
    if (MADEIRA_VTI_AVAILABLE) ok = MADEIRA_VTI_BUILT;
    if (!ok) {
      dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd MADEIRA_VT_INTERP=%d: VideoToolbox frame interpolation needs iOS 26 on a device -- off\n", w);
      w = 0;
    } else if (!madeira_fgl_mode()) {
      dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd MADEIRA_VT_INTERP=%d needs env.MADEIRA_PRESENT_FX = 1 (it works on the picture that path holds) -- off\n", w);
      w = 0;
    } else {
      dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd on: up to %d picture(s) between two frames of the game, made by VideoToolbox on a worker thread\n", w);
    }
  }
  atomic_store_explicit(&g_vti.want, w, memory_order_relaxed);
  return w;
}
static int madeira_vti_on(void) {
  return madeira_vti_want() && atomic_load_explicit(&g_vti.live, memory_order_relaxed) != 0 &&
         atomic_load_explicit(&g_lsfg_shown, memory_order_relaxed) <= 1 && g_madeira_vsync_mode != 2;
}
/* A short, bounded wait for the lock (a thread killed with its process can leave it held). */
static int madeira_vti_lock(void) {
  int i;
  for (i = 0; i < 20; i++) {
    if (!pthread_mutex_trylock(&g_vti_mu)) return 1;
    usleep(250);
  }
  return 0;
}
static dispatch_queue_t madeira_vti_queue(void) {
  static dispatch_queue_t q;
  static dispatch_once_t once;
  dispatch_once(&once, ^{
    q = dispatch_queue_create("madeira.vt-interp", dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_USER_INTERACTIVE, 0));
  });
  return q;
}
static uint64_t madeira_vti_abs_ns(uint64_t abs) {
  static mach_timebase_info_data_t tb;
  if (!tb.denom) mach_timebase_info(&tb);
  return abs * tb.numer / tb.denom;
}

static void madeira_vti_close(struct madeira_vti_session *s) {
  int i, p;
  if (!s) return;
  @autoreleasepool {
#if MADEIRA_VTI_BUILT
    if (MADEIRA_VTI_AVAILABLE) [(VTFrameProcessor *)s->proc endSession];
#endif
    [s->proc release]; [s->config release];
    for (i = 0; i < MADEIRA_VTI_RING; i++) {
      for (p = 0; p < 2; p++) [s->src_t[i][p] release];
      if (s->src[i]) CVPixelBufferRelease(s->src[i]);
    }
    for (i = 0; i < 3; i++) {
      for (p = 0; p < 2; p++) [s->dst_t[i][p] release];
      if (s->dst[i]) CVPixelBufferRelease(s->dst[i]);
      [s->out[i] release];
    }
    [s->p_in release]; [s->p_y release]; [s->p_c release]; [s->p_out release];
  }
  dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd session for %ux%u closed\n", s->w, s->h);
  free(s);
}
static void madeira_vti_release(struct madeira_vti_session *s) {
  if (s && atomic_fetch_sub_explicit(&s->refs, 1, memory_order_acq_rel) == 1)
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ madeira_vti_close(s); });
}
/* The session in force is given up (the menu's switch, another size, three errors, the end of the game). Frames already
 * with the worker keep their own reference. */
static void madeira_vti_drop(int state) {
  struct madeira_vti_session *s = NULL;
  if (madeira_vti_lock()) {
    s = g_vti.s;
    g_vti.s = NULL;
    atomic_store_explicit(&g_vti.state, state, memory_order_relaxed);
    pthread_mutex_unlock(&g_vti_mu);
  }
  madeira_vti_release(s);
}

/* A pixel buffer the processor takes and Metal can draw into. */
static CVPixelBufferRef madeira_vti_buffer(NSDictionary *need, unsigned fourcc, unsigned w, unsigned h) {
  NSDictionary *mine = @{ (id)kCVPixelBufferPixelFormatTypeKey: @(fourcc), (id)kCVPixelBufferWidthKey: @(w), (id)kCVPixelBufferHeightKey: @(h),
                          (id)kCVPixelBufferIOSurfacePropertiesKey: @{}, (id)kCVPixelBufferMetalCompatibilityKey: @YES };
  CFDictionaryRef resolved = NULL;
  CVPixelBufferRef pb = NULL;
  if (need && CVPixelBufferCreateResolvedAttributesDictionary(kCFAllocatorDefault, (CFArrayRef)@[need, mine], &resolved) != kCVReturnSuccess) {
    if (resolved) CFRelease(resolved);
    resolved = NULL;
  }
  if (CVPixelBufferCreate(kCFAllocatorDefault, w, h, fourcc, resolved ? resolved : (CFDictionaryRef)mine, &pb) != kCVReturnSuccess) pb = NULL;
  if (resolved) CFRelease(resolved);
  if (pb) {   /* what the numbers in it mean: sRGB-encoded, BT.709 */
    CVBufferSetAttachment(pb, kCVImageBufferColorPrimariesKey, kCVImageBufferColorPrimaries_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
    CVBufferSetAttachment(pb, kCVImageBufferTransferFunctionKey, kCVImageBufferTransferFunction_sRGB, kCVAttachmentMode_ShouldPropagate);
    if (fourcc != MADEIRA_VTI_BGRA)
      CVBufferSetAttachment(pb, kCVImageBufferYCbCrMatrixKey, kCVImageBufferYCbCrMatrix_ITU_R_709_2, kCVAttachmentMode_ShouldPropagate);
  }
  return pb;
}
static id<MTLTexture> madeira_vti_plane(id<MTLDevice> dev, CVPixelBufferRef pb, int plane, MTLPixelFormat fmt) {
  IOSurfaceRef surf = CVPixelBufferGetIOSurface(pb);
  size_t w = CVPixelBufferIsPlanar(pb) ? CVPixelBufferGetWidthOfPlane(pb, plane) : CVPixelBufferGetWidth(pb);
  size_t h = CVPixelBufferIsPlanar(pb) ? CVPixelBufferGetHeightOfPlane(pb, plane) : CVPixelBufferGetHeight(pb);
  MTLTextureDescriptor *d;
  if (!surf || !w || !h) return nil;
  d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt width:w height:h mipmapped:NO];
  d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
  d.storageMode = MTLStorageModeShared;
  return [dev newTextureWithDescriptor:d iosurface:surf plane:(NSUInteger)plane];
}
/* The textures of a pixel buffer's planes: one (BGRA, in the layer's own encoding) or two (Y, CbCr). 1 = all there. */
static int madeira_vti_planes(id<MTLDevice> dev, CVPixelBufferRef pb, int planar, int linear, id<MTLTexture> t[2]) {
  if (!pb) return 0;
  if (!planar) {
    t[0] = madeira_vti_plane(dev, pb, 0, linear ? MTLPixelFormatBGRA8Unorm_sRGB : MTLPixelFormatBGRA8Unorm);
    return t[0] != nil;
  }
  t[0] = madeira_vti_plane(dev, pb, 0, MTLPixelFormatR8Unorm);
  t[1] = madeira_vti_plane(dev, pb, 1, MTLPixelFormatRG8Unorm);
  return t[0] && t[1];
}
static id<MTLRenderPipelineState> madeira_vti_pipeline(id<MTLDevice> dev, id<MTLLibrary> lib, NSString *fragment, MTLPixelFormat fmt) {
  MTLRenderPipelineDescriptor *d = [[MTLRenderPipelineDescriptor alloc] init];
  id<MTLFunction> vs = [lib newFunctionWithName:@"vti_vs"], fs = [lib newFunctionWithName:fragment];
  id<MTLRenderPipelineState> pso;
  d.vertexFunction = vs;
  d.fragmentFunction = fs;
  d.colorAttachments[0].pixelFormat = fmt;
  pso = (vs && fs) ? [dev newRenderPipelineStateWithDescriptor:d error:NULL] : nil;
  [d release]; [vs release]; [fs release];
  return pso;
}
/* One full-picture pass: t0 (and t1) through `pso` into dst. */
static void madeira_vti_pass(id<MTLCommandBuffer> cb, id<MTLRenderPipelineState> pso, id<MTLTexture> t0, id<MTLTexture> t1,
                             id<MTLTexture> dst, int linear, int video) {
  struct { float lin, video; } args = { linear ? 1.0f : 0.0f, video ? 1.0f : 0.0f };
  MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
  id<MTLRenderCommandEncoder> enc;
  rp.colorAttachments[0].texture = dst;
  rp.colorAttachments[0].loadAction = MTLLoadActionDontCare;
  rp.colorAttachments[0].storeAction = MTLStoreActionStore;
  enc = [cb renderCommandEncoderWithDescriptor:rp];
  [enc setRenderPipelineState:pso];
  [enc setFragmentTexture:t0 atIndex:0];
  if (t1) [enc setFragmentTexture:t1 atIndex:1];
  [enc setFragmentBytes:&args length:sizeof args atIndex:0];
  [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
  [enc endEncoding];
}
static void madeira_vti_copy(id<MTLCommandBuffer> cb, id<MTLTexture> src, id<MTLTexture> dst) {
  id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
  [blit copyFromTexture:src toTexture:dst];
  [blit endEncoding];
}
/* The game's picture into source buffer `i`. */
static void madeira_vti_encode_in(id<MTLCommandBuffer> cb, struct madeira_vti_session *s, id<MTLTexture> picture, int i) {
  if (s->planar) {
    madeira_vti_pass(cb, s->p_y, picture, nil, s->src_t[i][0], s->linear, s->video);
    madeira_vti_pass(cb, s->p_c, picture, nil, s->src_t[i][1], s->linear, s->video);
  } else if (s->blit_in) {
    madeira_vti_copy(cb, picture, s->src_t[i][0]);
  } else {
    madeira_vti_pass(cb, s->p_in, picture, nil, s->src_t[i][0], 0, 0);
  }
}
/* Picture `i` the processor made, into s->out[i] (the layer's format). */
static void madeira_vti_encode_out(id<MTLCommandBuffer> cb, struct madeira_vti_session *s, int i) {
  if (s->planar) madeira_vti_pass(cb, s->p_out, s->dst_t[i][0], s->dst_t[i][1], s->out[i], s->linear, s->video);
  else if (s->blit_out) madeira_vti_copy(cb, s->dst_t[i][0], s->out[i]);
  else madeira_vti_pass(cb, s->p_out, s->dst_t[i][0], nil, s->out[i], 0, 0);
}

/* A session for pictures of w x h in the layer's format, or NULL and why. Slow (the model loads): never on a game's thread. */
static struct madeira_vti_session *madeira_vti_open(id<MTLDevice> dev, unsigned w, unsigned h, unsigned long layer_fmt, int frames,
                                                    char *why, size_t why_n) {
  struct madeira_vti_session *s = NULL;
  int linear = 0, ok = 0, i;
  why[0] = 0;
  if (!madeira_vti_layer_format(layer_fmt, &linear)) {
    snprintf(why, why_n, "the game's picture has Metal format %lu; only 8-bit BGRA and RGBA pictures are handled", layer_fmt);
    return NULL;
  }
#if MADEIRA_VTI_BUILT
  if (MADEIRA_VTI_AVAILABLE) {
    @autoreleasepool {
      VTLowLatencyFrameInterpolationConfiguration *config = nil;
      VTFrameProcessor *proc = nil;
      NSError *err = nil;
      NSArray<NSNumber *> *formats;
      unsigned offered[16], fourcc;
      int count = 0;
      id<MTLLibrary> lib = nil;
      struct timespec t0, t1;
      char four[8];
      MTLPixelFormat layer_format = (MTLPixelFormat)layer_fmt;
      do {
        if (![VTLowLatencyFrameInterpolationConfiguration isSupported]) {
          snprintf(why, why_n, "VideoToolbox says this device does not support low-latency frame interpolation");
          break;
        }
        config = [[VTLowLatencyFrameInterpolationConfiguration alloc] initWithFrameWidth:w frameHeight:h numberOfInterpolatedFrames:frames >= 3 ? 2 : 1];
        if (!config) {
          snprintf(why, why_n, "VideoToolbox takes no pictures of %ux%u for %d frame(s) between two (the size is refused)", w, h, frames);
          break;
        }
        formats = config.frameSupportedPixelFormats;
        for (NSNumber *f in formats)
          if (count < 16) offered[count++] = f.unsignedIntValue;
        fourcc = madeira_vti_pick(offered, count);
        if (!fourcc) {
          char list[120] = "";
          for (i = 0; i < count; i++) snprintf(list + strlen(list), sizeof list - strlen(list), "%s'%s'", i ? ", " : "", madeira_vti_fourcc(offered[i], four));
          snprintf(why, why_n, "VideoToolbox takes only the pixel formats %s here; BGRA, 420f and 420v are handled", count ? list : "(none)");
          break;
        }
        proc = [[VTFrameProcessor alloc] init];
        clock_gettime(CLOCK_MONOTONIC, &t0);
        if (!proc || ![proc startSessionWithConfiguration:config error:&err]) {
          snprintf(why, why_n, "the VideoToolbox session for %ux%u did not start: %s", w, h, err ? err.localizedDescription.UTF8String : "no reason given");
          break;
        }
        clock_gettime(CLOCK_MONOTONIC, &t1);
        s = calloc(1, sizeof *s);
        if (!s) break;
        s->w = w; s->h = h; s->fourcc = fourcc; s->layer_fmt = layer_fmt; s->linear = linear; s->frames = frames >= 3 ? 3 : 1;
        s->planar = fourcc != MADEIRA_VTI_BGRA;
        s->video = fourcc == MADEIRA_VTI_420V;
        s->proc = [proc retain]; s->config = [config retain];
        s->g_last = -1;
        atomic_store_explicit(&s->refs, 1, memory_order_relaxed);
        ok = 1;
        for (i = 0; i < MADEIRA_VTI_RING && ok; i++) {
          s->src[i] = madeira_vti_buffer(config.sourcePixelBufferAttributes, fourcc, w, h);
          ok = madeira_vti_planes(dev, s->src[i], s->planar, linear, s->src_t[i]);
        }
        for (i = 0; i < s->frames && ok; i++) {
          s->dst[i] = madeira_vti_buffer(config.destinationPixelBufferAttributes, fourcc, w, h);
          ok = madeira_vti_planes(dev, s->dst[i], s->planar, linear, s->dst_t[i]);
          if (ok) {
            MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:layer_format width:w height:h mipmapped:NO];
            d.usage = MTLTextureUsageShaderRead | MTLTextureUsageRenderTarget;
            d.storageMode = MTLStorageModePrivate;
            s->out[i] = [dev newTextureWithDescriptor:d];
            ok = s->out[i] != nil;
          }
        }
        if (!ok) { snprintf(why, why_n, "no pixel buffers of %ux%u '%s' that Metal can draw into", w, h, madeira_vti_fourcc(fourcc, four)); break; }
        /* BGRA with a BGRA layer: the buffer's texture has the layer's own format and the picture is copied as it is */
        s->blit_in = s->blit_out = !s->planar && s->src_t[0][0].pixelFormat == layer_format;
        if (s->planar || !s->blit_in) {
          lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:g_vti_msl] options:nil error:&err];
          if (lib && s->planar) {
            s->p_y = madeira_vti_pipeline(dev, lib, @"vti_y", MTLPixelFormatR8Unorm);
            s->p_c = madeira_vti_pipeline(dev, lib, @"vti_c", MTLPixelFormatRG8Unorm);
            s->p_out = madeira_vti_pipeline(dev, lib, @"vti_rgb", layer_format);
            ok = s->p_y && s->p_c && s->p_out;
          } else if (lib) {
            s->p_in = madeira_vti_pipeline(dev, lib, @"vti_copy", s->src_t[0][0].pixelFormat);
            s->p_out = madeira_vti_pipeline(dev, lib, @"vti_copy", layer_format);
            ok = s->p_in && s->p_out;
          } else ok = 0;
          [lib release];
          if (!ok) { snprintf(why, why_n, "the conversion passes did not compile: %s", err ? err.localizedDescription.UTF8String : "no pipeline"); break; }
        }
        dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd session ready: %ux%u, up to %d picture(s) between two frames, pixel format '%s'%s, "
                               "the model loaded in %.0f ms\n", w, h, s->frames, madeira_vti_fourcc(fourcc, four),
                s->planar ? " (the picture is converted to it and back)" : s->blit_in ? "" : " (the picture is copied through a pass)",
                (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6);
      } while (0);
      [proc release]; [config release];
    }
  } else
#endif
  snprintf(why, why_n, "VideoToolbox frame interpolation needs iOS 26 on a device");
  (void)dev; (void)w; (void)h; (void)frames; (void)i;
  if (s && !ok) { madeira_vti_close(s); s = NULL; }
  if (!s && !why[0]) snprintf(why, why_n, "out of memory");
  return s;
}
/* The game's thread: a session for this size is wanted and none is there or being made. */
static void madeira_vti_kick(id<MTLDevice> dev, unsigned w, unsigned h, unsigned long fmt) {
  int expect = atomic_load_explicit(&g_vti.state, memory_order_relaxed), frames = madeira_vti_want();
  if (expect == 1) return;
  if (expect != 0 && atomic_load_explicit(&g_vti.w, memory_order_relaxed) == w && atomic_load_explicit(&g_vti.h, memory_order_relaxed) == h &&
      atomic_load_explicit(&g_vti.fmt, memory_order_relaxed) == fmt)
    return;   /* ready, or not possible, for this very size */
  if (atomic_load_explicit(&g_vti.attempts, memory_order_relaxed) >= 6) return;   /* a game that keeps changing its size */
  if (!atomic_compare_exchange_strong(&g_vti.state, &expect, 1)) return;
  atomic_fetch_add_explicit(&g_vti.attempts, 1, memory_order_relaxed);
  atomic_store_explicit(&g_vti.w, w, memory_order_relaxed);
  atomic_store_explicit(&g_vti.h, h, memory_order_relaxed);
  atomic_store_explicit(&g_vti.fmt, fmt, memory_order_relaxed);
  [dev retain];
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
    char why[220];
    struct madeira_vti_session *s = madeira_vti_open(dev, w, h, fmt, frames, why, sizeof why), *old = NULL;
    [dev release];
    if (!madeira_vti_lock()) { madeira_vti_release(s); atomic_store_explicit(&g_vti.state, -1, memory_order_relaxed); return; }
    if (s && atomic_load_explicit(&g_vti.live, memory_order_relaxed) == 0) {   /* switched off while the model loaded */
      atomic_store_explicit(&g_vti.state, 0, memory_order_relaxed);
      pthread_mutex_unlock(&g_vti_mu);
      madeira_vti_release(s);
      return;
    }
    old = g_vti.s;
    g_vti.s = s;
    if (!s) {
      snprintf(g_vti.why, sizeof g_vti.why, "%s", why);
      dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd no extra pictures: %s\n", why);
    }
    atomic_store_explicit(&g_vti.state, s ? 2 : -1, memory_order_relaxed);
    pthread_mutex_unlock(&g_vti_mu);
    madeira_vti_release(old);
  });
}

/* The worker: the pictures between frame `prev` and frame `src` of the ring, into s->out. Returns how many (0: none). */
static int madeira_vti_run(id<MTLCommandQueue> queue, struct madeira_vti_session *s, int prev, long long prev_us, int src, long long us, int n) {
  int made = 0;
#if MADEIRA_VTI_BUILT
  if (MADEIRA_VTI_AVAILABLE) {
    static const double phase1[1] = { 0.5 }, phase3[3] = { 0.25, 0.5, 0.75 };
    const double *phase = n >= 3 ? phase3 : phase1;
    NSMutableArray<NSNumber *> *phases = [NSMutableArray array];
    NSMutableArray<VTFrameProcessorFrame *> *dests = [NSMutableArray array];
    VTFrameProcessorFrame *sf, *pf;
    VTLowLatencyFrameInterpolationParameters *params = nil;
    NSError *err = nil;
    struct timespec t0, t1;
    int i, ok;
    n = n >= 3 ? 3 : 1;
    if (us <= prev_us + n) us = prev_us + n + 1;   /* times that go forwards, with room for the pictures between */
    sf = [[[VTFrameProcessorFrame alloc] initWithBuffer:s->src[src] presentationTimeStamp:CMTimeMake(us, 1000000)] autorelease];
    pf = [[[VTFrameProcessorFrame alloc] initWithBuffer:s->src[prev] presentationTimeStamp:CMTimeMake(prev_us, 1000000)] autorelease];
    for (i = 0; i < n; i++) {
      long long at = prev_us + (long long)((double)(us - prev_us) * phase[i]);
      VTFrameProcessorFrame *df = [[[VTFrameProcessorFrame alloc] initWithBuffer:s->dst[i] presentationTimeStamp:CMTimeMake(at, 1000000)] autorelease];
      if (df) { [dests addObject:df]; [phases addObject:@((float)phase[i])]; }
    }
    if (sf && pf && (int)dests.count == n)
      params = [[[VTLowLatencyFrameInterpolationParameters alloc] initWithSourceFrame:sf previousFrame:pf interpolationPhase:phases
                                                                   destinationFrames:dests] autorelease];
    clock_gettime(CLOCK_MONOTONIC, &t0);
    ok = params && [(VTFrameProcessor *)s->proc processWithParameters:params error:&err];
    clock_gettime(CLOCK_MONOTONIC, &t1);
    if (!ok) {
      unsigned long long e = atomic_fetch_add_explicit(&g_vti.errors, 1, memory_order_relaxed) + 1;
      s->errors++;
      if (e <= 3 || (e % 256) == 0)
        dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd VideoToolbox made no picture (%s; %llu so far)\n",
                err ? err.localizedDescription.UTF8String : params ? "no reason given" : "the frames were not accepted", e);
      return 0;
    }
    s->errors = 0;
    atomic_fetch_add_explicit(&g_vti.pairs, 1, memory_order_relaxed);
    atomic_fetch_add_explicit(&g_vti.vt_us, (unsigned long long)((t1.tv_sec - t0.tv_sec) * 1000000ll + (t1.tv_nsec - t0.tv_nsec) / 1000), memory_order_relaxed);
    made = n;
  }
#endif
  (void)queue; (void)prev; (void)prev_us; (void)src; (void)us;
  return made;
}
/* The worker, once a second: the numbers the menu shows, and a line in the log every ten seconds. */
static void madeira_vti_report(uint64_t now_abs, int generated) {
  double secs;
  g_vti.stat_frames++;
  g_vti.stat_generated += (unsigned long long)generated;
  if (!g_vti.stat_abs) { g_vti.stat_abs = now_abs; g_vti.stat_frames = 0; g_vti.stat_generated = 0; return; }
  secs = (double)madeira_vti_abs_ns(now_abs - g_vti.stat_abs) / 1e9;
  if (secs < 1.0) return;
  {
    static unsigned long long last_pairs, last_vt, last_wait;
    static unsigned lines;
    unsigned long long pairs = atomic_load_explicit(&g_vti.pairs, memory_order_relaxed), vt = atomic_load_explicit(&g_vti.vt_us, memory_order_relaxed),
                       wait = atomic_load_explicit(&g_vti.wait_us, memory_order_relaxed), dp = pairs - last_pairs;
    g_vti.game_fps = g_vti.stat_frames / secs;
    g_vti.generated_fps = g_vti.stat_generated / secs;
    g_vti.pair_ms = dp ? (double)(vt - last_vt) / 1000.0 / (double)dp : 0.0;
    if ((lines++ % 10) == 0)
      dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd %.1f s: game %.1f fps, pictures %.1f/s, VideoToolbox %.1f ms a pair, %.1f ms waiting for "
                             "the game's frame on the GPU | so far: %llu pictures, skipped: screen cannot show them %llu, worker busy %llu, errors %llu\n",
              secs, g_vti.game_fps, g_vti.generated_fps, g_vti.pair_ms, dp ? (double)(wait - last_wait) / 1000.0 / (double)dp : 0.0,
              atomic_load_explicit(&g_vti.generated, memory_order_relaxed), atomic_load_explicit(&g_vti.skip_rate, memory_order_relaxed),
              atomic_load_explicit(&g_vti.skip_busy, memory_order_relaxed), atomic_load_explicit(&g_vti.errors, memory_order_relaxed));
    last_pairs = pairs; last_vt = vt; last_wait = wait;
  }
  g_vti.stat_abs = now_abs; g_vti.stat_frames = 0; g_vti.stat_generated = 0;
}
/* The worker: one frame of the game. s (may be NULL), copy, prev..us: what the game's thread prepared for the pictures.
 * min_dur: the present call's minimum duration for a frame that goes out alone. */
static void madeira_vti_job(id<MTLCommandQueue> queue, MadeiraPFXDrawable *px, struct madeira_vti_session *s, id<MTLCommandBuffer> copy,
                            int has_prev, int prev, long long prev_us, int src, long long us, double dt, double min_dur) {
  @autoreleasepool {
    struct madeira_pfx_layer *e = px->_entry;
    id held = e ? objc_loadWeak(&e->weak) : nil;
    CAMetalLayer *layer = (held && held == (id)px->_layer) ? [held retain] : nil;
    int n = 0, made = 0, i;
    if (layer) {
      if (s && copy && has_prev) {
        int backlog = atomic_load_explicit(&g_vti.outstanding, memory_order_relaxed) - 1;
        n = madeira_vti_count(s->frames, dt, g_madeira_panel_hz, backlog);
        if (!n) atomic_fetch_add_explicit(backlog > 0 ? &g_vti.skip_busy : &g_vti.skip_rate, 1, memory_order_relaxed);
      }
      if (n) {
        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        [copy waitUntilCompleted];
        clock_gettime(CLOCK_MONOTONIC, &t1);
        atomic_fetch_add_explicit(&g_vti.wait_us, (unsigned long long)((t1.tv_sec - t0.tv_sec) * 1000000ll + (t1.tv_nsec - t0.tv_nsec) / 1000), memory_order_relaxed);
        if (copy.status == MTLCommandBufferStatusError) {
          atomic_store_explicit(&s->reset, 1, memory_order_relaxed);
        } else {
          made = madeira_vti_run(queue, s, prev, prev_us, src, us, n);
          if (!made && s->errors >= 3 && atomic_load_explicit(&g_vti.state, memory_order_relaxed) == 2) {
            if (madeira_vti_lock()) {
              snprintf(g_vti.why, sizeof g_vti.why, "VideoToolbox failed three times in a row");
              pthread_mutex_unlock(&g_vti_mu);
            }
            dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd three errors in a row: no more extra pictures in this session\n");
            atomic_store_explicit(&g_vti.attempts, 1000, memory_order_relaxed);
            madeira_vti_drop(-1);
          }
        }
      }
      {
        double now = CACurrentMediaTime(), base = now < g_vti.next_free ? g_vti.next_free : now;
        int shown = 0;
        for (i = 0; i < made; i++) {   /* each picture from a command buffer of its own, committed before the next drawable is taken */
          id<MTLCommandBuffer> g = [queue commandBuffer];
          id<CAMetalDrawable> d;
          if (!g) break;
          madeira_vti_encode_out(g, s, i);
          d = [layer nextDrawable];
          if (d && madeira_pfx_emit(g, s->out[i], d.texture, e)) {
            double at = base + madeira_vti_offset(i + 1, made, dt);
            if (at <= now) [g presentDrawable:d]; else [g presentDrawable:d atTime:at];
            shown++;
          }
          madeira_pfx_commit(g);
        }
        {
          id<MTLCommandBuffer> real = [queue commandBuffer];
          id<CAMetalDrawable> d = real ? madeira_pfx_resolve(real, px) : nil;
          if (d) {
            if (shown) [real presentDrawable:d atTime:base + madeira_vti_offset(made + 1, made, dt)];
            else if (px->_mode == 1) [real presentDrawable:d afterMinimumDuration:px->_after];
            else if (min_dur > 0.0) [real presentDrawable:d afterMinimumDuration:min_dur];
            else [real presentDrawable:d];
          }
          if (real) madeira_pfx_commit(real);
        }
        g_vti.next_free = shown ? base + madeira_vti_offset(made + 1, made, dt) : 0.0;
        atomic_fetch_add_explicit(&g_vti.frames, 1, memory_order_relaxed);
        atomic_fetch_add_explicit(&g_vti.generated, (unsigned long long)shown, memory_order_relaxed);
        madeira_vti_report(mach_absolute_time(), shown);
      }
      dispatch_async(dispatch_get_main_queue(), ^{ [layer release]; });   /* a layer's last release belongs to the main thread */
    }
    madeira_pfx_slot_free(px);
    [px release];
    [copy release];
    [queue release];
    madeira_vti_release(s);
    atomic_fetch_sub_explicit(&g_vti.outstanding, 1, memory_order_release);
  }
}

/* madeira_pfx_next, on the game's thread: stand-ins are needed while pictures are wanted or the worker still has frames,
 * and while it has, a new frame waits a moment for a picture of the pool to come back. 1 = the worker has frames. */
static int madeira_vti_poll(struct madeira_pfx_layer *e) {
  int busy = atomic_load_explicit(&g_vti.outstanding, memory_order_acquire) > 0, i, tries;
  atomic_store_explicit(&g_pfx_force, (busy || madeira_vti_on()) ? 1 : 0, memory_order_relaxed);
  if (!busy) return 0;
  for (tries = 0; tries < 400; tries++) {   /* at most 100 ms */
    for (i = 0; i < MADEIRA_PFX_PICTURES; i++)
      if (!atomic_load_explicit(&e->v_busy[i], memory_order_relaxed)) return 1;
    if (atomic_load_explicit(&g_vti.outstanding, memory_order_acquire) <= 0) break;
    usleep(250);
  }
  return 1;
}
/* madeira_pfx_after_commit, on the game's thread: 1 = this frame's present was handed to the worker (which then owns a
 * reference to the stand-in and gives its picture back). 0 = present it as without the key. */
static int madeira_vti_take(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px) {
  id<MTLCommandQueue> queue = cb.commandQueue;
  id<MTLTexture> picture = px->_texture;
  struct madeira_vti_session *s = NULL;
  id<MTLCommandBuffer> copy = nil;
  void *followed = atomic_load_explicit(&g_vti.layer, memory_order_relaxed);
  uint64_t now = mach_absolute_time();
  unsigned w, h;
  unsigned long fmt;
  int busy, on, vmode, capped, gen = 0, has_prev = 0, prev = -1, src = -1, stall = 0;
  long long prev_us = 0, us = (long long)(madeira_vti_abs_ns(now) / 1000);
  double period, min_dur = 0.0, dt;
  if (!queue || !picture) return 0;
  w = (unsigned)picture.width; h = (unsigned)picture.height; fmt = (unsigned long)picture.pixelFormat;
  busy = atomic_load_explicit(&g_vti.outstanding, memory_order_acquire) > 0;
  if (followed != (void *)px->_layer) {
    /* one layer is followed: the first with a picture of 640x360 or more, until it has not presented for two seconds */
    uint64_t seen = atomic_load_explicit(&g_vti.layer_abs, memory_order_relaxed);
    if (busy || w < 640 || h < 360 || (followed && madeira_vti_abs_ns(now - seen) < 2000000000ull)) return 0;
    atomic_store_explicit(&g_vti.layer, (void *)px->_layer, memory_order_relaxed);
    g_vti.last_abs = 0;
    dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd follows layer %p (%ux%u, Metal format %lu)\n", (void *)px->_layer, w, h, fmt);
  }
  atomic_store_explicit(&g_vti.layer_abs, now, memory_order_relaxed);
  on = madeira_vti_on() && px->_mode == 0;
  if (on) madeira_vti_kick(picture.device, w, h, fmt);
  gen = on && atomic_load_explicit(&g_vti.state, memory_order_relaxed) == 2;
  if (!busy && !gen) return 0;
  /* the game's frame time, as the frame generation path measures it */
  dt = g_vti.dt;
  if (g_vti.last_abs) {
    double d = (double)madeira_vti_abs_ns(now - g_vti.last_abs) / 1e9;
    if (d < 1.0 / 240.0) d = 1.0 / 240.0;
    if (d > 0.25) { d = 0.25; stall = 1; }   /* a stall: the frame before is no neighbour of this one */
    dt = dt > 0 ? dt * 0.8 + d * 0.2 : d;
  } else stall = 1;
  g_vti.last_abs = now;
  g_vti.dt = dt;
  if (gen && madeira_vti_lock()) {
    s = g_vti.s;
    /* a third frame while two are with the worker would be written over the buffer one of them still reads */
    if (s && s->w == w && s->h == h && s->layer_fmt == fmt && atomic_load_explicit(&g_vti.outstanding, memory_order_acquire) <= 1) {
      atomic_fetch_add_explicit(&s->refs, 1, memory_order_relaxed);
      src = (s->g_last + 1) % MADEIRA_VTI_RING;
      has_prev = s->g_valid && !stall && !atomic_exchange_explicit(&s->reset, 0, memory_order_relaxed);
      prev = s->g_last; prev_us = s->g_us;
      s->g_last = src; s->g_valid = 1; s->g_us = us;
    } else {
      if (s) s->g_valid = 0;
      s = NULL;
    }
    pthread_mutex_unlock(&g_vti_mu);
  }
  if (s) {   /* the picture into its buffer, right behind the game's own command buffer */
    copy = [queue commandBuffer];
    if (copy) {
      [copy retain];
      madeira_vti_encode_in(copy, s, picture, src);
      madeira_pfx_commit(copy);
    } else has_prev = 0;
  }
  /* the start of the present call, on the game's thread as always */
  madeira_app_wait_active("present");
  {
    extern unsigned long long ios_fps_cap_dxmt_pace(void);
    unsigned long long fps_cap_ns = ios_fps_cap_dxmt_pace();
    if (fps_cap_ns) ios_frame_limiter(fps_cap_ns);
  }
  vmode = g_madeira_vsync_mode;
  capped = vmode == 1 || vmode == 3 || vmode == 4;
  period = vmode == 3 ? 1.0 / 30.0 : vmode == 4 ? 1.0 / 40.0 : 1.0 / 60.0;
  if (capped && (s || madeira_present_limiter_enabled()))
    ios_frame_limiter(madeira_present_limit_to(period));   /* the limit holds the game's own frames; the pictures come on top */
  else if (capped)
    min_dur = period;
  if (madeira_frame_hooks_on()) ios_frame_encode_present(0);
  madeira_log_present_cadence(s ? "presentVTInterp" : "presentVTWorker", 0.0);
  atomic_fetch_add_explicit(&g_vti.outstanding, 1, memory_order_acq_rel);
  [px retain];
  [queue retain];
  dispatch_async(madeira_vti_queue(), ^{ madeira_vti_job(queue, px, s, copy, has_prev, prev, prev_us, src, us, dt, min_dur); });
  return 1;
}

/* The in-game menu's switch. on: 1, 0, or -1 for "as the game started". Returns what is in force (1 or 0). */
int madeira_vti_set(int on) {
  int v = on > 0 ? 1 : on < 0 ? -1 : 0, was = atomic_exchange_explicit(&g_vti.live, v, memory_order_relaxed);
  if (v != was && atomic_load_explicit(&g_vti.want, memory_order_relaxed) > 0) {
    dprintf(STDERR_FILENO, "[vt-interp] madeira-bcd live switch: %s\n", v ? "ON" : "OFF (the session is closed; switching on loads the model again)");
    if (!v) {
      atomic_store_explicit(&g_vti.attempts, 0, memory_order_relaxed);
      if (atomic_load_explicit(&g_vti.state, memory_order_relaxed) != 1) madeira_vti_drop(0);
    }
  }
  return atomic_load_explicit(&g_vti.want, memory_order_relaxed) > 0 && v != 0;
}
/* For the in-game menu: 0 = not asked for or switched off, 1 = the model is loading, 2 = ready and waiting (no picture in
 * the last second), 3 = making pictures, 4 = not possible (madeira_vti_why says why). pair_ms: VideoToolbox's time a pair. */
int madeira_vti_status(int *width, int *height, double *game_fps, double *generated_fps, double *pair_ms) {
  int state = atomic_load_explicit(&g_vti.state, memory_order_relaxed);
  if (width) *width = (int)atomic_load_explicit(&g_vti.w, memory_order_relaxed);
  if (height) *height = (int)atomic_load_explicit(&g_vti.h, memory_order_relaxed);
  if (game_fps) *game_fps = g_vti.game_fps;
  if (generated_fps) *generated_fps = g_vti.generated_fps;
  if (pair_ms) *pair_ms = g_vti.pair_ms;
  if (atomic_load_explicit(&g_vti.want, memory_order_relaxed) <= 0 || atomic_load_explicit(&g_vti.live, memory_order_relaxed) == 0) return 0;
  if (state < 0) return 4;
  if (state == 2) return g_vti.generated_fps > 0.5 ? 3 : 2;
  return 1;
}
/* Why no pictures can be made (status 4), as a sentence without a full stop. Returns its length. */
int madeira_vti_why(char *out, int cap) {
  if (!out || cap <= 0) return 0;
  out[0] = 0;
  if (madeira_vti_lock()) {
    snprintf(out, (size_t)cap, "%s", g_vti.why);
    pthread_mutex_unlock(&g_vti_mu);
  }
  return (int)strlen(out);
}
/* The game ended: the session and its model are given back. */
void madeira_vti_session_ended(void) {
  if (atomic_load_explicit(&g_vti.want, memory_order_relaxed) > 0) {
    madeira_vti_drop(0);
    atomic_store_explicit(&g_vti.attempts, 0, memory_order_relaxed);
    atomic_store_explicit(&g_vti.layer, NULL, memory_order_relaxed);
    atomic_store_explicit(&g_vti.live, -1, memory_order_relaxed);
  }
  atomic_store_explicit(&g_vti.want, -1, memory_order_relaxed);   /* the next session reads its own MADEIRA_VT_INTERP */
}

/* ---- the probe: what this device's VideoToolbox does, without a game ---- */
static void madeira_vti_say(char *out, int cap, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void madeira_vti_say(char *out, int cap, const char *fmt, ...) {
  char line[400];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(line, sizeof line, fmt, ap);
  va_end(ap);
  if (out && cap > 0) {   /* the caller logs them (the app's log takes stderr only while a game runs) */
    size_t used = strlen(out);
    if (used + 2 < (size_t)cap) snprintf(out + used, (size_t)cap - used, "%s\n", line);
  } else {
    dprintf(STDERR_FILENO, "[vt-interp] probe: %s\n", line);
  }
}
/* The test picture: a light bar of `bar` pixels at x on a dark ground (also for two planes of 4:2:0). */
static void madeira_vti_paint(CVPixelBufferRef pb, unsigned x, unsigned bar) {
  size_t w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb), row, col;
  int planar = CVPixelBufferIsPlanar(pb);
  if (CVPixelBufferLockBaseAddress(pb, 0) != kCVReturnSuccess) return;
  if (planar) {
    unsigned char *y = CVPixelBufferGetBaseAddressOfPlane(pb, 0), *c = CVPixelBufferGetBaseAddressOfPlane(pb, 1);
    size_t ys = CVPixelBufferGetBytesPerRowOfPlane(pb, 0), cs = CVPixelBufferGetBytesPerRowOfPlane(pb, 1), ch = CVPixelBufferGetHeightOfPlane(pb, 1);
    for (row = 0; y && row < h; row++) {
      memset(y + row * ys, 48, w);
      if (x < w) memset(y + row * ys + x, 220, x + bar <= w ? bar : w - x);
    }
    for (row = 0; c && row < ch; row++) memset(c + row * cs, 128, cs);
  } else {
    unsigned char *p = CVPixelBufferGetBaseAddress(pb);
    size_t stride = CVPixelBufferGetBytesPerRow(pb);
    for (row = 0; p && row < h; row++)
      for (col = 0; col < w; col++) {
        unsigned char v = (col >= x && col < x + bar) ? 220 : 48, *q = p + row * stride + col * 4;
        q[0] = v; q[1] = v; q[2] = v; q[3] = 255;
      }
  }
  CVPixelBufferUnlockBaseAddress(pb, 0);
}
/* Where the bar is in a picture: the middle of the light pixels of the middle row, or -1 when there are none. */
static double madeira_vti_find(CVPixelBufferRef pb) {
  size_t w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb), col, count = 0;
  int planar = CVPixelBufferIsPlanar(pb);
  double sum = 0.0;
  if (CVPixelBufferLockBaseAddress(pb, kCVPixelBufferLock_ReadOnly) != kCVReturnSuccess) return -1.0;
  {
    const unsigned char *base = planar ? CVPixelBufferGetBaseAddressOfPlane(pb, 0) : CVPixelBufferGetBaseAddress(pb);
    size_t stride = planar ? CVPixelBufferGetBytesPerRowOfPlane(pb, 0) : CVPixelBufferGetBytesPerRow(pb);
    const unsigned char *row = base ? base + (h / 2) * stride : NULL;
    for (col = 0; row && col < w; col++)
      if ((planar ? row[col] : row[col * 4 + 1]) > 134) { sum += (double)col + 0.5; count++; }
  }
  CVPixelBufferUnlockBaseAddress(pb, kCVPixelBufferLock_ReadOnly);
  return count ? sum / (double)count : -1.0;
}
static unsigned long long madeira_vti_footprint_mb(void) {
  task_vm_info_data_t info;
  mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
  if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count) != KERN_SUCCESS) return 0;
  return (unsigned long long)(info.phys_footprint >> 20);
}
static int madeira_vti_cmp_double(const void *a, const void *b) {
  double x = *(const double *)a, y = *(const double *)b;
  return x < y ? -1 : x > y;
}
#if MADEIRA_VTI_BUILT
/* One line of the ladder. kind 0: one picture between two; 1: three; 2: twice as large plus one picture; 3: one picture,
 * put into a Metal command buffer. Returns 1 when the session started. */
static int madeira_vti_probe_one(char *out, int cap, unsigned w, unsigned h, int kind) API_AVAILABLE(ios(26.0), macos(26.0)) {
  static const char *names[4] = { "1 picture", "3 pictures", "2x larger + 1 picture", "1 picture, in a Metal command buffer" };
  int started = 0;
  @autoreleasepool {
    VTLowLatencyFrameInterpolationConfiguration *config;
    VTFrameProcessor *proc = nil;
    NSError *err = nil;
    CVPixelBufferRef src[MADEIRA_VTI_RING] = { NULL, NULL, NULL }, dst[3] = { NULL, NULL, NULL };
    unsigned offered[16], fourcc, scale = kind == 2 ? 2 : 1, bar = w / 16, step = w / 32, k;
    int count = 0, n = kind == 1 ? 3 : 1, ndst = kind == 2 ? 2 : n, i, good = 0, bad = 0, failed = 0, runs = 0;
    double ms[16], load_ms, worst = 0.0;
    unsigned long long before = madeira_vti_footprint_mb(), after;
    struct timespec t0, t1;
    char four[8], list[160] = "", note[160] = "";
    id<MTLDevice> dev = nil;
    id<MTLCommandQueue> queue = nil;
    if (kind == 2)
      config = [[[VTLowLatencyFrameInterpolationConfiguration alloc] initWithFrameWidth:w frameHeight:h spatialScaleFactor:2] autorelease];
    else
      config = [[[VTLowLatencyFrameInterpolationConfiguration alloc] initWithFrameWidth:w frameHeight:h numberOfInterpolatedFrames:n == 3 ? 2 : 1] autorelease];
    if (!config) { madeira_vti_say(out, cap, "%ux%u, %s: refused (no configuration for this size)", w, h, names[kind]); return 0; }
    for (NSNumber *f in config.frameSupportedPixelFormats) {
      if (count < 16) offered[count++] = f.unsignedIntValue;
      snprintf(list + strlen(list), sizeof list - strlen(list), "%s'%s'", list[0] ? " " : "", madeira_vti_fourcc(f.unsignedIntValue, four));
    }
    fourcc = madeira_vti_pick(offered, count);
    if (!fourcc) { madeira_vti_say(out, cap, "%ux%u, %s: pixel formats %s -- none of BGRA, 420f, 420v", w, h, names[kind], list[0] ? list : "(none)"); return 0; }
    proc = [[[VTFrameProcessor alloc] init] autorelease];
    clock_gettime(CLOCK_MONOTONIC, &t0);
    if (!proc || ![proc startSessionWithConfiguration:config error:&err]) {
      madeira_vti_say(out, cap, "%ux%u, %s: the session did not start (%s)", w, h, names[kind], err ? err.localizedDescription.UTF8String : "no reason given");
      return 0;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    started = 1;
    load_ms = (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6;
    for (i = 0; i < MADEIRA_VTI_RING; i++) src[i] = madeira_vti_buffer(config.sourcePixelBufferAttributes, fourcc, w, h);
    for (i = 0; i < ndst; i++) dst[i] = madeira_vti_buffer(config.destinationPixelBufferAttributes, fourcc, w * scale, h * scale);
    if (kind == 3) { dev = [MTLCreateSystemDefaultDevice() autorelease]; queue = [[dev newCommandQueue] autorelease]; }
    if (!src[0] || !src[1] || !src[2] || !dst[0] || (ndst > 1 && !dst[1]) || (ndst > 2 && !dst[2]) || (kind == 3 && !queue)) {
      madeira_vti_say(out, cap, "%ux%u, %s: no pixel buffers of format '%s'", w, h, names[kind], madeira_vti_fourcc(fourcc, four));
    } else {
      madeira_vti_paint(src[0], madeira_vti_bar_x(0, w, bar, step), bar);
      for (k = 1; k <= 10 && failed < 3; k++) {   /* ten pairs, with the buffers going round as they do in a game */
        static const double phase1[1] = { 0.5 }, phase3[3] = { 0.25, 0.5, 0.75 };
        const double *phase = n == 3 ? phase3 : phase1;
        int cur = (int)(k % MADEIRA_VTI_RING), prev = (int)((k - 1) % MADEIRA_VTI_RING), ok = 0;
        unsigned x0 = madeira_vti_bar_x(k - 1, w, bar, step), x1 = madeira_vti_bar_x(k, w, bar, step);
        NSMutableArray<NSNumber *> *phases = [NSMutableArray array];
        NSMutableArray<VTFrameProcessorFrame *> *dests = [NSMutableArray array];
        VTFrameProcessorFrame *sf, *pf;
        VTLowLatencyFrameInterpolationParameters *params;
        madeira_vti_paint(src[cur], x1, bar);
        sf = [[[VTFrameProcessorFrame alloc] initWithBuffer:src[cur] presentationTimeStamp:CMTimeMake((int64_t)k * 33333, 1000000)] autorelease];
        pf = [[[VTFrameProcessorFrame alloc] initWithBuffer:src[prev] presentationTimeStamp:CMTimeMake((int64_t)(k - 1) * 33333, 1000000)] autorelease];
        for (i = 0; i < n; i++) {
          VTFrameProcessorFrame *df = [[[VTFrameProcessorFrame alloc] initWithBuffer:dst[i]
                                          presentationTimeStamp:CMTimeMake((int64_t)(k - 1) * 33333 + (int64_t)(33333.0 * phase[i]), 1000000)] autorelease];
          if (df) { [dests addObject:df]; [phases addObject:@((float)phase[i])]; }
        }
        if (kind == 2) {   /* the enlarged copy of the frame itself is the last destination */
          VTFrameProcessorFrame *df = [[[VTFrameProcessorFrame alloc] initWithBuffer:dst[1] presentationTimeStamp:CMTimeMake((int64_t)k * 33333, 1000000)] autorelease];
          if (df) [dests addObject:df];
        }
        params = (sf && pf && (int)dests.count == ndst)
          ? [[[VTLowLatencyFrameInterpolationParameters alloc] initWithSourceFrame:sf previousFrame:pf interpolationPhase:phases destinationFrames:dests] autorelease] : nil;
        err = nil;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        if (params && kind == 3) {
          id<MTLCommandBuffer> cb = [queue commandBuffer];
          int waited = 0;
          @try {
            [proc processWithCommandBuffer:cb parameters:params];
            [cb commit];
            while (cb.status < MTLCommandBufferStatusCompleted && waited++ < 2000) usleep(1000);
            ok = cb.status == MTLCommandBufferStatusCompleted;
            if (!ok) snprintf(note, sizeof note, "; command buffer status %lu%s%s", (unsigned long)cb.status, cb.error ? ": " : "",
                              cb.error ? cb.error.localizedDescription.UTF8String : "");
          } @catch (NSException *ex) {
            snprintf(note, sizeof note, "; it raised %s", ex.name.UTF8String);
          }
        } else if (params) {
          ok = [proc processWithParameters:params error:&err];
          if (!ok) snprintf(note, sizeof note, "; %s", err ? err.localizedDescription.UTF8String : "no reason given");
        } else snprintf(note, sizeof note, "; the frames were not accepted");
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (!ok) { failed++; continue; }
        if (k > 2 && runs < 16) {   /* the first two pairs warm it up */
          ms[runs] = (t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6;
          if (ms[runs] > worst) worst = ms[runs];
          runs++;
        }
        if (x1 > x0)   /* not the pair where the bar wraps */
          for (i = 0; i < n; i++) {
            double want = ((double)x0 + ((double)x1 - (double)x0) * phase[i] + (double)bar / 2.0) * (double)scale, got = madeira_vti_find(dst[i]);
            if (got >= 0.0 && fabs(got - want) <= (double)(step * scale) / 4.0 + 2.0) good++; else bad++;
          }
      }
      [proc endSession];
      after = madeira_vti_footprint_mb();
      if (runs) {
        qsort(ms, (size_t)runs, sizeof ms[0], madeira_vti_cmp_double);
        madeira_vti_say(out, cap, "%ux%u, %s: works. Format '%s' (offered: %s), model loaded in %.0f ms, %.1f ms a pair (middle of %d; longest %.1f), "
                                  "bar in place in %d of %d pictures, memory +%lld MB%s",
                        w, h, names[kind], madeira_vti_fourcc(fourcc, four), list, load_ms, ms[runs / 2], runs, worst, good, good + bad,
                        (long long)after - (long long)before, failed ? note : "");
      } else {
        madeira_vti_say(out, cap, "%ux%u, %s: the session started (format '%s', %.0f ms) but no pair was processed%s", w, h, names[kind],
                        madeira_vti_fourcc(fourcc, four), load_ms, note);
      }
      proc = nil;
    }
    if (proc) [proc endSession];
    for (i = 0; i < MADEIRA_VTI_RING; i++) if (src[i]) CVPixelBufferRelease(src[i]);
    for (i = 0; i < 3; i++) if (dst[i]) CVPixelBufferRelease(dst[i]);
  }
  return started;
}
#endif
/* The whole ladder. Slow (tens of seconds: every line loads a model): call it from a background thread, not while a game
 * runs. out gets its lines, one a line (without a buffer they go to stderr). Returns how many lines of the ladder worked. */
int madeira_vti_probe(char *out, int cap) {
  int worked = 0;
  if (out && cap > 0) out[0] = 0;
#if MADEIRA_VTI_BUILT
  if (MADEIRA_VTI_AVAILABLE) {
    static const unsigned sizes[6][2] = { { 640, 360 }, { 960, 540 }, { 1280, 720 }, { 1600, 900 }, { 1920, 1080 }, { 2560, 1440 } };
    int kind, i;
    BOOL supported = [VTLowLatencyFrameInterpolationConfiguration isSupported];
    madeira_vti_say(out, cap, "VideoToolbox low-latency frame interpolation: %s on this device", supported ? "supported" : "NOT supported");
    if ([(id)[VTLowLatencyFrameInterpolationConfiguration class] respondsToSelector:@selector(maximumDimensions)]) {
      CMVideoDimensions most = [VTLowLatencyFrameInterpolationConfiguration maximumDimensions];
      madeira_vti_say(out, cap, "largest picture it says it takes: %dx%d", (int)most.width, (int)most.height);
    }
    if ([(id)[VTLowLatencyFrameInterpolationConfiguration class] respondsToSelector:@selector(minimumDimensions)]) {
      CMVideoDimensions least = [VTLowLatencyFrameInterpolationConfiguration minimumDimensions];
      madeira_vti_say(out, cap, "smallest picture it says it takes: %dx%d", (int)least.width, (int)least.height);
    }
    if (!supported) return 0;
    for (kind = 0; kind < 3; kind++) {
      int misses = 0;
      for (i = 0; i < 6 && misses < 2; i++) {   /* two sizes in a row that do not start: the larger ones are not tried */
        if (madeira_vti_probe_one(out, cap, sizes[i][0], sizes[i][1], kind)) { worked++; misses = 0; } else misses++;
      }
    }
    worked += madeira_vti_probe_one(out, cap, 1280, 720, 3);
    madeira_vti_say(out, cap, "done: %d line(s) worked", worked);
    return worked;
  }
#endif
  madeira_vti_say(out, cap, "VideoToolbox low-latency frame interpolation needs iOS 26 on a device");
  return 0;
}

'''

PAIRS = [
    ("present-fx layers block (for the declarations)", A_DECL, DECL),
    ("madeira_pfx_next (sizes)", A_POLL, POLL),
    ("madeira_pfx_next (lock)", A_NOLOCK, NOLOCK),
    ("madeira_pfx_next (no picture)", A_NOTEX, NOTEX),
    ("madeira_pfx_after_commit", A_TAKE, TAKE),
    ("lsfg pacing block's end", A_BLOCK, BLOCK),
]


def main():
    if not PATH.exists():
        print(f"::error::{PATH} not found (run from the repository root)")
        return 1
    s = PATH.read_text()
    if MARKER in s:
        print(f"{PATH}: vt-interp already patched")
        return 0
    if NEEDS not in s or PRESENT_FX not in s:
        if PRESENT_FX not in s and "madeira_fg_other_layer" not in s:
            # An older dxmt checkout, which the live-control and present-fx patches left as it is (see the first of them): so does this.
            print(f"{PATH}: no present-fx in this tree (an older dxmt checkout): the VideoToolbox experiment builds on it and is left out")
            return 0
        print(f"::error::{PATH}: tools/patch-winemetal-present-fx.py and tools/patch-winemetal-lsfg-pacing.py have to run first")
        return 1
    for name, anchor, _ in PAIRS:
        if s.count(anchor) != 1:
            print(f"::error::{PATH}: {name} anchor found {s.count(anchor)} times, not once -- dxmt moved, review tools/patch-winemetal-vt-interp.py")
            return 1
    for _, anchor, new in PAIRS:
        s = s.replace(anchor, new, 1)
    PATH.write_text(s)
    print(f"{PATH}: vt-interp patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
