#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright 2026 thebcd69 (BCD)
# Portions copyright 2026 The Porthole authors: code taken from https://github.com/tylerkaska112/Porthole (commit 99ec315, patches 0189 and 0196)
# under GPL-3.0-or-later and modified for BCD in November 2026. See THIRD-PARTY-NOTICES.md.
"""present-fx: winemetal itself enlarges the picture (MetalFX spatial, switchable while the game runs) and frame generation
works on the game's own resolution. Opt-in: env.MADEIRA_PRESENT_FX = 1; off by default.

Until now the picture is enlarged by the Windows side (DXMT's MetalFX swap chain for Direct3D 11, madeira_d3d12's
`metalfx-upscale` for Direct3D 12): fixed when the game starts, and frame generation, which works on the drawable in
winemetal's present call, then runs on the ENLARGED picture (an interpolator of up to 5244x2412 in BCD's logs).

With MADEIRA_PRESENT_FX=1 the app hands the upscaling to winemetal (MADEIRA_PFX_UPSCALE, madeira_pfx_set_upscale) and
winemetal_unix.c does this for every layer the game presents to (Direct3D 9, 10, 11 and 12 alike, with the DLLs as they are):

  - MetalLayer_setProps: the game's side asks for a drawable of the game's size; the real layer gets the enlarged size
    (a factor of 1.1 to 2, or "to the screen": the smaller of screen/picture in both directions, at most 2, none under
    1.1). MetalLayer_getProps answers the size that was asked for.
  - MetalLayer_nextDrawable: while there is something to do (a size to enlarge to, or frame generation on), the game's
    side gets a stand-in for the drawable whose texture is a picture of the game's size. Otherwise it gets the layer's
    own drawable and everything is as without this patch.
  - MTLCommandBuffer_presentDrawable with a stand-in only notes it on the command buffer. When that buffer is
    committed, winemetal presents: a command buffer of its own on the same queue takes a real drawable just then,
    enlarges the picture into it (MetalFX's spatial scaler into a texture, then a copy; a plain copy when the sizes
    match; a bilinear pass when MetalFX cannot do the pair of sizes) and presents it through the same code as before
    (the FPS limits, the frame hooks, the frame counter).
  - Frame generation gets the stand-in: it works on the game-size picture, each generated frame is enlarged like the
    game's own and goes out in a command buffer of its own that is committed before the real frame's drawable is
    asked for. So the game's side never holds a real drawable, and at most one more than is on the way to the screen
    is asked for at a time.
  - The setting can change at any time (the in-game menu): the next frame resizes the real layer on the main thread
    and enlarges into whatever size the drawable has meanwhile.

Nothing here runs without MADEIRA_PRESENT_FX=1: every hook starts with madeira_pfx_on() (the key, read once) or with a
test for a stand-in, which only this path creates. MadeiraCtl op 30 answers the Direct3D 12 runtime whether winemetal
enlarges (it then makes no scaler of its own) and how large the screen is (`metalfx-upscale = fit` without present-fx).

The "to the screen" rule is Porthole's patch 0196; presenting every generated frame from a command buffer of its own,
committed before the next drawable is taken, is the arrangement of Porthole's patch 0189.

Runs after tools/patch-winemetal-framegen-live.py. Idempotent; fails by name if an anchor moved. Run from the
repository root.
"""
import pathlib
import sys

PATH = pathlib.Path("dxmt/src/winemetal/unix/winemetal_unix.c")
MARKER = "madeira-bcd: present-fx"
NEEDS = "madeira-bcd: frame generation, live control"

# ---- early declarations (next to the GPU meter, before MTLCommandBuffer_commit) ----
A_EARLY = "static _Atomic int g_madeira_gpu_meter;\n"
EARLY = """/* """ + MARKER + """ (tools/patch-winemetal-present-fx.py): presents noted on a command buffer and not yet made */
static _Atomic int g_pfx_pending;
static void madeira_pfx_after_commit(id<MTLCommandBuffer> cb);
"""

A_COMMIT = "  [(id<MTLCommandBuffer>)params->handle commit];\n"
COMMIT = (A_COMMIT +
          "  if (atomic_load_explicit(&g_pfx_pending, memory_order_relaxed))   /* " + MARKER + ": a stand-in was presented on a buffer; was it this one? */\n"
          "    madeira_pfx_after_commit((id<MTLCommandBuffer>)params->handle);\n")

# ---- block E: the stand-in and what frame generation needs of it (before madeira_fg_setup's helpers) ----
A_BLOCK_E = "/* madeira-bcd: frame generation, live control: the set of the size before, kept as a spare. */\n"
BLOCK_E = r'''/* madeira-bcd: present-fx (tools/patch-winemetal-present-fx.py). With MADEIRA_PRESENT_FX=1 winemetal enlarges the
 * picture itself and frame generation works on the game's resolution; see the script for the whole arrangement.
 * The "to the screen" rule follows Porthole's patch 0196, a command buffer of its own for every generated frame
 * Porthole's patch 0189 (https://github.com/tylerkaska112/Porthole, GPL-3.0-or-later, copyright 2026 The Porthole
 * authors), modified for BCD in November 2026. */
struct madeira_pfx_layer;
/* What the game's side holds in place of a drawable: a picture of the game's size. It answers the two questions the
 * game's side asks a drawable (its texture; and, for frame generation, its layer). */
@interface MadeiraPFXDrawable : NSObject {
@public
  id<MTLTexture> _texture;
  CAMetalLayer *_layer;    /* not retained, like the layer handle the game's side presents with: a layer's last release belongs to the main thread */
  struct madeira_pfx_layer *_entry;
  int _slot, _token;       /* which picture of the layer's pool this is, and the mark it holds it with */
  int _pending;            /* counted in g_pfx_pending */
  int _mode;               /* how the game's side presented it: 0 = presentDrawable, 1 = presentDrawable:afterMinimumDuration: */
  double _after;
}
- (id<MTLTexture>)texture;
- (CAMetalLayer *)layer;
@end
static void madeira_pfx_slot_free(MadeiraPFXDrawable *px);
@implementation MadeiraPFXDrawable
- (id<MTLTexture>)texture { return _texture; }
- (CAMetalLayer *)layer { return _layer; }
- (void)dealloc {
  if (_pending) atomic_fetch_sub_explicit(&g_pfx_pending, 1, memory_order_relaxed);
  madeira_pfx_slot_free(self);
  [_texture release];
  [super dealloc];
}
@end
static Class g_pfx_class;   /* set when the first stand-in is made: before that nothing can be one */
static int madeira_pfx_proxy(id obj) {
  return obj && g_pfx_class && object_getClass(obj) == g_pfx_class;
}
/* deferred: this thread is presenting a stand-in now (the present call must not note it again); committed: the frame
 * generation path already committed the buffer it was given; pair_half: how long after now the real frame of a
 * generated pair is due. */
static __thread struct { int deferred, committed; double pair_half; } g_pfx_tls;
static int madeira_pfx_emit(id<MTLCommandBuffer> cb, id<MTLTexture> src, id<MTLTexture> dst, struct madeira_pfx_layer *e);
static void madeira_pfx_finish_pair(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px);

'''

# ---- frame generation's present function: the stand-in's cases ----
A_NEED = "  int fgl_gen = !have_prev || madeira_fgl_may_generate((CAMetalLayer *)drawable.layer, 3, now);\n"
NEED = ("  int fgl_gen = !have_prev || madeira_fgl_may_generate((CAMetalLayer *)drawable.layer,\n"
        "                                                      madeira_pfx_proxy((id)drawable) ? 2 : 3, now);   /* " + MARKER + ": the game's own drawable is taken later */\n")

A_FITS = "  if (!extra || extra.texture.width != g_fg.w || extra.texture.height != g_fg.h) {\n"
FITS = ("  if (!extra || (!madeira_pfx_proxy((id)drawable) &&   /* " + MARKER + ": a generated frame is enlarged into its drawable */\n"
        "                 (extra.texture.width != g_fg.w || extra.texture.height != g_fg.h))) {\n")

A_BLIT = """  {
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    [blit copyFromTexture:g_fg.out toTexture:extra.texture];
    [blit endEncoding];
  }
"""
BLIT = """  if (madeira_pfx_proxy((id)drawable)) {   /* """ + MARKER + """: enlarged like the game's own frames */
    if (!madeira_pfx_emit(cb, g_fg.out, extra.texture, ((MadeiraPFXDrawable *)drawable)->_entry)) {
      g_fg.no_drawable++;
      return 0;
    }
  } else {
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    [blit copyFromTexture:g_fg.out toTexture:extra.texture];
    [blit endEncoding];
  }
"""

A_PAIR = """  madeira_fgl_track(extra); madeira_fgl_track(drawable);   /* madeira-bcd: frame generation, live control: both are on their way to the screen */
  [cb presentDrawable:extra];
  [cb presentDrawable:drawable atTime:CACurrentMediaTime() + g_fg.dt * 0.5];
"""
PAIR = """  if (madeira_pfx_proxy((id)drawable)) {   /* """ + MARKER + """: the real frame follows from a buffer of its own (madeira_pfx_finish_pair) */
    madeira_fgl_track(extra);
    [cb presentDrawable:extra];
    g_pfx_tls.pair_half = g_fg.dt * 0.5;
  } else {
  madeira_fgl_track(extra); madeira_fgl_track(drawable);   /* madeira-bcd: frame generation, live control: both are on their way to the screen */
  [cb presentDrawable:extra];
  [cb presentDrawable:drawable atTime:CACurrentMediaTime() + g_fg.dt * 0.5];
  }
"""

A_WRAP_FBO = "  } else if (atomic_load_explicit(&g_fgl_want, memory_order_relaxed) == 1 && ((CAMetalLayer *)drawable.layer).framebufferOnly) {\n"
WRAP_FBO = ("  } else if (!madeira_pfx_proxy((id)drawable) &&   /* " + MARKER + ": a stand-in's picture can always be read */\n"
            "             atomic_load_explicit(&g_fgl_want, memory_order_relaxed) == 1 && ((CAMetalLayer *)drawable.layer).framebufferOnly) {\n")

A_WRAP_TRACK = "  if (!r) madeira_fgl_track((id<MTLDrawable>)drawable);\n"
WRAP_TRACK = """  if (madeira_pfx_proxy((id)drawable)) {   /* """ + MARKER + """: the real frame of a generated pair; a frame let through is counted when it gets its drawable */
    if (r) madeira_pfx_finish_pair(cb, (MadeiraPFXDrawable *)drawable);
  } else
  if (!r) madeira_fgl_track((id<MTLDrawable>)drawable);
"""

# ---- block L: the layers, the pictures, the scaler, the presents (before the present thunk) ----
A_THUNK = """static NTSTATUS
_MTLCommandBuffer_presentDrawable(void *obj) {
"""
BLOCK_L = r'''/* madeira-bcd: present-fx: the layers, their pictures, the scaler and the presents. */
/* pfx-pure-begin: plain C (tests/host/check-present-fx.py compiles these on the host) */
/* The size a picture of w x h is enlarged to. mode 1: by milli / 1000 (1.1 to 2; even sizes, as bcd.cfg's
 * metalfx-upscale makes them). mode 2, "to the screen": by the smaller of screen / picture in both directions, at
 * most 2, and not at all under 1.1 (a game that already draws about as large as the screen is left alone). The
 * screen's long side goes with the picture's long side. Nothing over 8192. Anything else: w x h itself. */
static void madeira_pfx_target(int mode, int milli, unsigned screen_long, unsigned screen_short, unsigned w, unsigned h,
                               unsigned *ow, unsigned *oh) {
  double f;
  unsigned uw, uh;
  *ow = w; *oh = h;
  if (!w || !h) return;
  if (mode == 1) {
    f = milli / 1000.0;
  } else if (mode == 2 && screen_long && screen_short) {
    double sw = w >= h ? screen_long : screen_short, sh = w >= h ? screen_short : screen_long;
    double fx = sw / (double)w, fy = sh / (double)h;
    f = fx < fy ? fx : fy;
  } else {
    return;
  }
  if (f > 2.0) f = 2.0;
  if (!(f >= 1.1)) return;
  uw = ((unsigned)(w * f + 0.5)) & ~1u;
  uh = ((unsigned)(h * f + 0.5)) & ~1u;
  if (uw > 8192 || uh > 8192 || uw <= w || uh <= h) return;
  *ow = uw; *oh = uh;
}
/* MADEIRA_PFX_UPSCALE: "" or "off" = none, "fit" = to the screen, a number from 1.1 to 2 = that factor.
 * Returns the mode (0, 1, 2); *milli is the factor times 1000 for mode 1. */
static int madeira_pfx_parse(const char *text, int *milli) {
  double f;
  *milli = 0;
  if (!text || !*text) return 0;
  if (!strcmp(text, "fit")) return 2;
  f = atof(text);
  if (!(f >= 1.1)) return 0;
  if (f > 2.0) f = 2.0;
  *milli = (int)(f * 1000.0 + 0.5);
  return 1;
}
/* pfx-pure-end */

static _Atomic int g_pfx_up_mode = -1;        /* 0 none, 1 a factor, 2 to the screen; -1 until MADEIRA_PFX_UPSCALE is read */
static _Atomic int g_pfx_up_milli, g_pfx_screen_long, g_pfx_screen_short;
static _Atomic int g_pfx_broken;              /* gave up for this session: every layer presents as without present-fx */
static _Atomic int g_pfx_force;               /* something else needs the game-size picture (another frame generator) */
static _Atomic int g_pfx_strikes, g_pfx_token_next;
static _Atomic unsigned long long g_pfx_frames, g_pfx_scaled, g_pfx_copied, g_pfx_bilinear, g_pfx_dropped;
static pthread_mutex_t g_pfx_mu = PTHREAD_MUTEX_INITIALIZER;   /* the pictures, scalers and pipelines below; never waited on for long */
static char g_pfx_key;

#define MADEIRA_PFX_LAYERS 8
#define MADEIRA_PFX_PICTURES 3
struct madeira_pfx_layer {
  _Atomic(void *) key;                        /* the CAMetalLayer; compared, never dereferenced */
  id weak;                                    /* the same layer, through objc_storeWeak */
  _Atomic unsigned vw, vh;                    /* the drawable size the game's side asked for */
  _Atomic unsigned rw, rh;                    /* the size the real layer was last given */
  _Atomic int kind;                           /* how the last frame got into its drawable: 0 copy, 1 MetalFX, 2 bilinear */
  _Atomic int v_busy[MADEIRA_PFX_PICTURES];   /* 0 = free, else the mark of the stand-in that holds it */
  /* under g_pfx_mu: */
  id<MTLTexture> v[MADEIRA_PFX_PICTURES];
  unsigned v_w, v_h;
  MTLPixelFormat v_fmt;
  id scaler;                                  /* id<MTLFXSpatialScaler> */
  id<MTLTexture> up;
  unsigned s_iw, s_ih, s_ow, s_oh, s_failed;
  MTLPixelFormat s_ifmt, s_ofmt;
};
static struct madeira_pfx_layer g_pfx_layers[MADEIRA_PFX_LAYERS];

static int madeira_pfx_on(void) {
  return madeira_fgl_mode() && !wmtr_enabled() && !atomic_load_explicit(&g_pfx_broken, memory_order_relaxed);
}
static void madeira_pfx_up_init(void) {
  int expect = -1, milli = 0, mode;
  if (atomic_load_explicit(&g_pfx_up_mode, memory_order_relaxed) >= 0) return;
  mode = madeira_pfx_parse(getenv("MADEIRA_PFX_UPSCALE"), &milli);
  if (mode == 1) atomic_store_explicit(&g_pfx_up_milli, milli, memory_order_relaxed);
  if (atomic_compare_exchange_strong(&g_pfx_up_mode, &expect, mode))   /* a choice the menu made first stays */
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd on (MADEIRA_PRESENT_FX): upscaling %s%s (MADEIRA_PFX_UPSCALE), screen %dx%d\n",
            mode == 2 ? "to the screen" : mode == 1 ? "by a factor of " : "off", mode == 1 ? getenv("MADEIRA_PFX_UPSCALE") : "",
            atomic_load_explicit(&g_pfx_screen_long, memory_order_relaxed), atomic_load_explicit(&g_pfx_screen_short, memory_order_relaxed));
}

/* The app, when a session has ended (BCDPresentFXLive.sessionEnded; no present runs then): the next session reads its own
 * MADEIRA_PFX_UPSCALE and tries present-fx again if this one gave up. One session per app run, the default: nothing reads them later. */
void madeira_pfx_session_reset(void) {
  atomic_store_explicit(&g_pfx_up_mode, -1, memory_order_relaxed);
  atomic_store_explicit(&g_pfx_broken, 0, memory_order_relaxed);
}

/* The app: the screen's size in pixels (either order). Stored only; read by the "to the screen" rule. */
void madeira_pfx_set_screen(int a, int b) {
  atomic_store_explicit(&g_pfx_screen_long, a > b ? a : b, memory_order_relaxed);
  atomic_store_explicit(&g_pfx_screen_short, a > b ? b : a, memory_order_relaxed);
}
/* The app's upscaling choice, at any time: mode 0 none, 1 a factor (milli = factor * 1000, 1100 to 2000), 2 to the
 * screen. The next frame of each layer follows it. Stored only (it does nothing in a session without present-fx). */
void madeira_pfx_set_upscale(int mode, int milli) {
  if (mode == 1) {
    if (milli < 1100) mode = 0;
    else atomic_store_explicit(&g_pfx_up_milli, milli > 2000 ? 2000 : milli, memory_order_relaxed);
  } else if (mode != 2) {
    mode = 0;
  }
  if (atomic_exchange_explicit(&g_pfx_up_mode, mode, memory_order_relaxed) != mode || mode == 1)
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd upscaling from the app: %s (factor %.3f)\n",
            mode == 2 ? "to the screen" : mode == 1 ? "a factor" : "off", mode == 1 ? atomic_load_explicit(&g_pfx_up_milli, memory_order_relaxed) / 1000.0 : 1.0);
}
/* For the menu: the largest layer's sizes (what the game draws, what is shown) and how the last frame got there
 * (0 a copy, 1 MetalFX, 2 bilinear). Returns the mode in force (0, 1, 2), or -1 before any layer was set up. */
int madeira_pfx_status(int *game_w, int *game_h, int *shown_w, int *shown_h, int *kind) {
  struct madeira_pfx_layer *best = NULL;
  int i, mode = atomic_load_explicit(&g_pfx_up_mode, memory_order_relaxed);
  for (i = 0; i < MADEIRA_PFX_LAYERS; i++) {
    struct madeira_pfx_layer *e = &g_pfx_layers[i];
    if (atomic_load_explicit(&e->key, memory_order_relaxed) &&
        (!best || (uint64_t)e->vw * e->vh > (uint64_t)best->vw * best->vh))
      best = e;
  }
  if (game_w) *game_w = best ? (int)best->vw : 0;
  if (game_h) *game_h = best ? (int)best->vh : 0;
  if (shown_w) *shown_w = best ? (int)best->rw : 0;
  if (shown_h) *shown_h = best ? (int)best->rh : 0;
  if (kind) *kind = best ? atomic_load_explicit(&best->kind, memory_order_relaxed) : 0;
  return best ? (mode < 0 ? 0 : mode) : -1;
}

static struct madeira_pfx_layer *madeira_pfx_entry(CAMetalLayer *layer, int create) {
  int i;
  for (i = 0; i < MADEIRA_PFX_LAYERS; i++)
    if (atomic_load_explicit(&g_pfx_layers[i].key, memory_order_acquire) == (void *)layer) return &g_pfx_layers[i];
  if (!create) return NULL;
  for (i = 0; i < MADEIRA_PFX_LAYERS; i++) {
    void *expect = NULL;
    if (atomic_compare_exchange_strong(&g_pfx_layers[i].key, &expect, (void *)layer) || expect == (void *)layer)
      return &g_pfx_layers[i];
  }
  return NULL;   /* more layers than slots: this one presents as without present-fx */
}
static void madeira_pfx_want_size(struct madeira_pfx_layer *e, unsigned *ow, unsigned *oh) {
  int mode = atomic_load_explicit(&g_pfx_up_mode, memory_order_relaxed);
  madeira_pfx_target(mode < 0 ? 0 : mode, atomic_load_explicit(&g_pfx_up_milli, memory_order_relaxed),
                     (unsigned)atomic_load_explicit(&g_pfx_screen_long, memory_order_relaxed),
                     (unsigned)atomic_load_explicit(&g_pfx_screen_short, memory_order_relaxed),
                     atomic_load_explicit(&e->vw, memory_order_relaxed), atomic_load_explicit(&e->vh, memory_order_relaxed), ow, oh);
}
/* MetalLayer_setProps, on the main thread: the size the real layer gets for the size the game's side asked for. */
static CGSize madeira_pfx_layer_size(CAMetalLayer *layer, double w, double h) {
  struct madeira_pfx_layer *e;
  unsigned ow, oh;
  if (!madeira_pfx_on() || !layer || !(w >= 1.0) || !(h >= 1.0) || w > 16384.0 || h > 16384.0) return CGSizeMake(w, h);
  e = madeira_pfx_entry(layer, 1);
  if (!e) return CGSizeMake(w, h);
  madeira_pfx_up_init();
  objc_storeWeak(&e->weak, layer);
  atomic_store_explicit(&e->vw, (unsigned)w, memory_order_relaxed);
  atomic_store_explicit(&e->vh, (unsigned)h, memory_order_relaxed);
  madeira_pfx_want_size(e, &ow, &oh);
  if (ow != atomic_load_explicit(&e->rw, memory_order_relaxed) || oh != atomic_load_explicit(&e->rh, memory_order_relaxed))
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd layer %p: the game draws %ux%u, the screen gets %ux%u\n", (void *)layer,
            (unsigned)w, (unsigned)h, ow, oh);
  atomic_store_explicit(&e->rw, ow, memory_order_relaxed);
  atomic_store_explicit(&e->rh, oh, memory_order_relaxed);
  return CGSizeMake(ow, oh);
}
/* MetalLayer_getProps: the game's side reads back the size it asked for, not the enlarged one. */
static void madeira_pfx_virtual_size(CAMetalLayer *layer, double *w, double *h) {
  struct madeira_pfx_layer *e;
  unsigned vw, vh;
  if (!madeira_pfx_on()) return;
  e = madeira_pfx_entry(layer, 0);
  if (!e) return;
  vw = atomic_load_explicit(&e->vw, memory_order_relaxed);
  vh = atomic_load_explicit(&e->vh, memory_order_relaxed);
  if (vw && vh) { *w = vw; *h = vh; }
}

static void madeira_pfx_slot_free(MadeiraPFXDrawable *px) {
  int token = px->_token;
  if (px->_entry && px->_slot >= 0 && token)
    atomic_compare_exchange_strong(&px->_entry->v_busy[px->_slot], &token, 0);
  px->_token = 0;
}
/* A short, bounded wait for the lock: a thread killed with its process can leave it held. */
static int madeira_pfx_lock(void) {
  int i;
  for (i = 0; i < 40; i++) {
    if (!pthread_mutex_trylock(&g_pfx_mu)) { atomic_store_explicit(&g_pfx_strikes, 0, memory_order_relaxed); return 1; }
    usleep(500);
  }
  if (atomic_fetch_add_explicit(&g_pfx_strikes, 1, memory_order_relaxed) + 1 >= 20 &&
      !atomic_exchange_explicit(&g_pfx_broken, 1, memory_order_relaxed)) {
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd its lock was not free 20 frames in a row: present-fx is off for the rest of this "
                           "session and every layer goes back to the size the game asked for\n");
    for (i = 0; i < MADEIRA_PFX_LAYERS; i++) {
      struct madeira_pfx_layer *e = &g_pfx_layers[i];
      unsigned vw = atomic_load_explicit(&e->vw, memory_order_relaxed), vh = atomic_load_explicit(&e->vh, memory_order_relaxed);
      @autoreleasepool {
        CAMetalLayer *layer = atomic_load_explicit(&e->key, memory_order_relaxed) ? objc_loadWeak(&e->weak) : nil;
        if (layer && vw && vh) dispatch_async(dispatch_get_main_queue(), ^{ layer.drawableSize = CGSizeMake(vw, vh); });
      }
    }
  }
  return 0;
}
static id<MTLTexture> madeira_pfx_texture(id<MTLDevice> dev, MTLPixelFormat fmt, NSUInteger w, NSUInteger h, MTLTextureUsage usage) {
  MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt width:w height:h mipmapped:NO];
  d.usage = usage;
  d.storageMode = MTLStorageModePrivate;
  return [dev newTextureWithDescriptor:d];
}

/* MetalLayer_nextDrawable. 1 = *out is a stand-in for the game's side; 0 = nothing to do for this layer now, take the
 * layer's own drawable as without present-fx; -1 = no picture could be had while the layer is enlarged: no drawable
 * this time (the game's side drops the frame, as it does in the uncapped mode's skips). */
static int madeira_pfx_next(CAMetalLayer *layer, id *out) {
  struct madeira_pfx_layer *e = madeira_pfx_entry(layer, 0);
  unsigned vw, vh, ow, oh;
  int slot = -1, token = 0, i, enlarged;
  id<MTLTexture> tex = nil;
  MTLPixelFormat fmt;
  MadeiraPFXDrawable *px;
  if (!e) return 0;
  vw = atomic_load_explicit(&e->vw, memory_order_relaxed);
  vh = atomic_load_explicit(&e->vh, memory_order_relaxed);
  if (!vw || !vh) return 0;
  madeira_pfx_up_init();
  madeira_pfx_want_size(e, &ow, &oh);
  if (ow != atomic_load_explicit(&e->rw, memory_order_relaxed) || oh != atomic_load_explicit(&e->rh, memory_order_relaxed)) {
    /* the choice changed while the game runs: the real layer follows, on the main thread */
    atomic_store_explicit(&e->rw, ow, memory_order_relaxed);
    atomic_store_explicit(&e->rh, oh, memory_order_relaxed);
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd layer %p: the game draws %ux%u, the screen now gets %ux%u\n", (void *)layer, vw, vh, ow, oh);
    dispatch_async(dispatch_get_main_queue(), ^{ layer.drawableSize = CGSizeMake(ow, oh); });
  }
  {
    CGSize now = layer.drawableSize;
    enlarged = (unsigned)now.width != vw || (unsigned)now.height != vh;   /* also for a frame or two after "off" */
  }
  if (!enlarged && ow == vw && oh == vh && !madeira_fg_enabled() && !atomic_load_explicit(&g_pfx_force, memory_order_relaxed))
    return 0;
  if (pthread_mutex_trylock(&g_pfx_mu)) return enlarged ? -1 : 0;
  fmt = layer.pixelFormat;
  if (e->v_w != vw || e->v_h != vh || e->v_fmt != fmt) {
    for (i = 0; i < MADEIRA_PFX_PICTURES; i++) { [e->v[i] release]; e->v[i] = nil; }   /* a stand-in still out keeps its own reference */
    e->v_w = vw; e->v_h = vh; e->v_fmt = fmt;
  }
  for (i = 0; i < MADEIRA_PFX_PICTURES && slot < 0; i++)
    if (!atomic_load_explicit(&e->v_busy[i], memory_order_relaxed)) slot = i;
  if (slot >= 0) {
    if (!e->v[slot] && layer.device)
      e->v[slot] = madeira_pfx_texture(layer.device, fmt, vw, vh, MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead);
    tex = [e->v[slot] retain];
    if (tex) {
      token = atomic_fetch_add_explicit(&g_pfx_token_next, 1, memory_order_relaxed) + 1;
      if (!token) token = atomic_fetch_add_explicit(&g_pfx_token_next, 1, memory_order_relaxed) + 1;
      atomic_store_explicit(&e->v_busy[slot], token, memory_order_relaxed);
    }
  }
  pthread_mutex_unlock(&g_pfx_mu);
  if (!tex) return enlarged ? -1 : 0;
  px = [[[MadeiraPFXDrawable alloc] init] autorelease];
  px->_texture = tex;
  px->_layer = layer;
  px->_entry = e;
  px->_slot = slot;
  px->_token = token;
  if (!g_pfx_class) g_pfx_class = object_getClass(px);
  *out = px;
  return 1;
}

static const char *g_pfx_msl =
  "#include <metal_stdlib>\n"
  "using namespace metal;\n"
  "struct PfxOut { float4 pos [[position]]; float2 uv; };\n"
  "vertex PfxOut pfx_vs(uint vid [[vertex_id]]) {\n"
  "  PfxOut o; float2 p = float2((vid << 1) & 2, vid & 2);\n"
  "  o.pos = float4(p * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0); o.uv = p; return o;\n"
  "}\n"
  "fragment float4 pfx_fs(PfxOut in [[stage_in]], texture2d<float> t [[texture(0)]]) {\n"
  "  constexpr sampler s(coord::normalized, filter::linear, address::clamp_to_edge);\n"
  "  return t.sample(s, in.uv);\n"
  "}\n";
static struct { id<MTLRenderPipelineState> pso[2]; MTLPixelFormat fmt[2]; id<MTLLibrary> lib; int dead, next; } g_pfx_bil;
/* The plain bilinear pass for a pair of sizes MetalFX cannot do (called with g_pfx_mu held). */
static id<MTLRenderPipelineState> madeira_pfx_bilinear(id<MTLDevice> dev, MTLPixelFormat fmt) {
  NSError *err = nil;
  MTLRenderPipelineDescriptor *d;
  id<MTLFunction> vs, fs;
  id<MTLRenderPipelineState> pso;
  int i;
  for (i = 0; i < 2; i++)
    if (g_pfx_bil.pso[i] && g_pfx_bil.fmt[i] == fmt) return g_pfx_bil.pso[i];
  if (g_pfx_bil.dead) return nil;
  if (!g_pfx_bil.lib) {
    g_pfx_bil.lib = [dev newLibraryWithSource:[NSString stringWithUTF8String:g_pfx_msl] options:nil error:&err];
    if (!g_pfx_bil.lib) {
      dprintf(STDERR_FILENO, "[present-fx] madeira-bcd the bilinear pass did not compile: %s\n", err ? err.description.UTF8String : "?");
      g_pfx_bil.dead = 1;
      return nil;
    }
  }
  vs = [g_pfx_bil.lib newFunctionWithName:@"pfx_vs"];
  fs = [g_pfx_bil.lib newFunctionWithName:@"pfx_fs"];
  d = [[MTLRenderPipelineDescriptor alloc] init];
  d.vertexFunction = vs;
  d.fragmentFunction = fs;
  d.colorAttachments[0].pixelFormat = fmt;
  pso = [dev newRenderPipelineStateWithDescriptor:d error:&err];
  [d release]; [vs release]; [fs release];
  if (!pso) {
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd no bilinear pipeline for format %lu: %s\n", (unsigned long)fmt,
            err ? err.description.UTF8String : "?");
    return nil;
  }
  i = g_pfx_bil.next++ & 1;
  [g_pfx_bil.pso[i] release];
  g_pfx_bil.pso[i] = pso;
  g_pfx_bil.fmt[i] = fmt;
  return pso;
}
/* MetalFX's spatial scaler for this layer's pair of sizes, or nil (called with g_pfx_mu held). */
static id madeira_pfx_scaler(struct madeira_pfx_layer *e, id<MTLDevice> dev, id<MTLTexture> src, id<MTLTexture> dst) {
  unsigned iw = (unsigned)src.width, ih = (unsigned)src.height, ow = (unsigned)dst.width, oh = (unsigned)dst.height;
  MTLFXSpatialScalerDescriptor *desc;
  id<MTLFXSpatialScaler> s;
  if (e->s_iw == iw && e->s_ih == ih && e->s_ow == ow && e->s_oh == oh && e->s_ifmt == src.pixelFormat && e->s_ofmt == dst.pixelFormat)
    return e->s_failed ? nil : e->scaler;
  [e->scaler release]; e->scaler = nil;
  [e->up release]; e->up = nil;
  e->s_iw = iw; e->s_ih = ih; e->s_ow = ow; e->s_oh = oh; e->s_ifmt = src.pixelFormat; e->s_ofmt = dst.pixelFormat;
  e->s_failed = 1;
  if (![MTLFXSpatialScalerDescriptor supportsDevice:dev]) {
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd this GPU has no MetalFX spatial scaler: %ux%u -> %ux%u with a bilinear pass\n", iw, ih, ow, oh);
    return nil;
  }
  desc = [[MTLFXSpatialScalerDescriptor alloc] init];
  desc.colorTextureFormat = src.pixelFormat;
  desc.outputTextureFormat = dst.pixelFormat;
  desc.inputWidth = iw; desc.inputHeight = ih;
  desc.outputWidth = ow; desc.outputHeight = oh;
  s = [desc newSpatialScalerWithDevice:dev];
  [desc release];
  if (s) e->up = madeira_pfx_texture(dev, dst.pixelFormat, ow, oh, s.outputTextureUsage | MTLTextureUsageShaderRead);
  if (!s || !e->up) {
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd no MetalFX scaler for %ux%u (format %lu) -> %ux%u (format %lu): a bilinear pass instead\n",
            iw, ih, (unsigned long)src.pixelFormat, ow, oh, (unsigned long)dst.pixelFormat);
    [s release];
    [e->up release]; e->up = nil;
    return nil;
  }
  e->scaler = s;
  e->s_failed = 0;
  dprintf(STDERR_FILENO, "[present-fx] madeira-bcd MetalFX spatial scaler ready: %ux%u -> %ux%u (format %lu)\n", iw, ih, ow, oh,
          (unsigned long)dst.pixelFormat);
  return s;
}
/* src (the game's picture, or a generated one of the same size and format) into a drawable's texture: a copy when the
 * sizes and formats match, MetalFX's spatial scaler when the drawable has the size the layer was asked to have and is
 * larger, a bilinear pass otherwise. 1 = encoded, 0 = not (nothing may be presented then). */
static int madeira_pfx_emit(id<MTLCommandBuffer> cb, id<MTLTexture> src, id<MTLTexture> dst, struct madeira_pfx_layer *e) {
  NSUInteger iw, ih, ow, oh;
  int kind = 0;
  if (!cb || !src || !dst) return 0;
  iw = src.width; ih = src.height; ow = dst.width; oh = dst.height;
  if (iw == ow && ih == oh && src.pixelFormat == dst.pixelFormat) {
    id<MTLBlitCommandEncoder> blit = [cb blitCommandEncoder];
    [blit copyFromTexture:src toTexture:dst];
    [blit endEncoding];
    atomic_fetch_add_explicit(&g_pfx_copied, 1, memory_order_relaxed);
    if (e) atomic_store_explicit(&e->kind, 0, memory_order_relaxed);
    return 1;
  }
  if (!madeira_pfx_lock()) return 0;
  {
    id<MTLDevice> dev = src.device;
    id<MTLFXSpatialScaler> scaler = nil;
    id<MTLRenderPipelineState> pso = nil;
    if (e && ow >= iw && oh >= ih && ow == atomic_load_explicit(&e->rw, memory_order_relaxed) &&
        oh == atomic_load_explicit(&e->rh, memory_order_relaxed))
      scaler = (id<MTLFXSpatialScaler>)madeira_pfx_scaler(e, dev, src, dst);
    if (scaler) {
      id<MTLBlitCommandEncoder> blit;
      scaler.colorTexture = src;
      scaler.outputTexture = e->up;
      scaler.fence = nil;
      [scaler encodeToCommandBuffer:cb];
      blit = [cb blitCommandEncoder];
      [blit copyFromTexture:e->up toTexture:dst];
      [blit endEncoding];
      kind = 1;
    } else if ((pso = madeira_pfx_bilinear(dev, dst.pixelFormat))) {
      MTLRenderPassDescriptor *rp = [MTLRenderPassDescriptor renderPassDescriptor];
      id<MTLRenderCommandEncoder> enc;
      rp.colorAttachments[0].texture = dst;
      rp.colorAttachments[0].loadAction = MTLLoadActionDontCare;
      rp.colorAttachments[0].storeAction = MTLStoreActionStore;
      enc = [cb renderCommandEncoderWithDescriptor:rp];
      [enc setRenderPipelineState:pso];
      [enc setFragmentTexture:src atIndex:0];
      [enc drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
      [enc endEncoding];
      kind = 2;
    }
  }
  pthread_mutex_unlock(&g_pfx_mu);
  if (!kind) return 0;
  atomic_fetch_add_explicit(kind == 1 ? &g_pfx_scaled : &g_pfx_bilinear, 1, memory_order_relaxed);
  if (e) atomic_store_explicit(&e->kind, kind, memory_order_relaxed);
  return 1;
}

static void madeira_pfx_commit(id<MTLCommandBuffer> cb) {
  struct unixcall_generic_obj_noret c;
  memset(&c, 0, sizeof c);
  c.handle = (obj_handle_t)cb;
  _MTLCommandBuffer_commit(&c);   /* the same hooks as every other command buffer */
}
/* A real drawable for the stand-in's picture, just now, with the picture encoded into it on cb. nil = none (the
 * frame is dropped). */
static id<CAMetalDrawable> madeira_pfx_resolve(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px) {
  struct timespec t0, t1;
  id<CAMetalDrawable> d;
  clock_gettime(CLOCK_MONOTONIC, &t0);
  d = [px->_layer nextDrawable];
  clock_gettime(CLOCK_MONOTONIC, &t1);
  if (madeira_frame_hooks_on())
    ios_frame_drawable_wait((unsigned long long)((t1.tv_sec - t0.tv_sec) * 1000000000ll + (t1.tv_nsec - t0.tv_nsec)));
  if (!d || !madeira_pfx_emit(cb, px->_texture, d.texture, px->_entry)) {
    unsigned long long n = atomic_fetch_add_explicit(&g_pfx_dropped, 1, memory_order_relaxed) + 1;
    if (n <= 4 || (n % 256) == 0)
      dprintf(STDERR_FILENO, "[present-fx] madeira-bcd a frame was dropped: %s (%llu so far)\n",
              d ? "its picture could not be put into the drawable" : "the layer gave no drawable", n);
    return nil;
  }
  /* frame generation counts the frames on their way to the screen of the layer it follows */
  if (madeira_fg_enabled() && (void *)px->_layer == __atomic_load_n(&g_fg_layer, __ATOMIC_ACQUIRE))
    madeira_fgl_track(d);
  return d;
}
/* Frame generation made and presented its frame on cb (see madeira_fg_present_inner0): that buffer goes first, then
 * the real frame from a buffer of its own, half a game frame later. */
static void madeira_pfx_finish_pair(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px) {
  id<MTLCommandBuffer> real;
  id<CAMetalDrawable> d;
  madeira_pfx_commit(cb);
  g_pfx_tls.committed = 1;
  real = [cb.commandQueue commandBuffer];
  if (!real) return;
  d = madeira_pfx_resolve(real, px);
  if (d) [real presentDrawable:d atTime:CACurrentMediaTime() + g_pfx_tls.pair_half];
  madeira_pfx_commit(real);
}
static NTSTATUS _MTLCommandBuffer_presentDrawable(void *obj);
/* MTLCommandBuffer_presentDrawable with a stand-in: noted on the buffer, presented when the buffer is committed. */
static void madeira_pfx_defer(id<MTLCommandBuffer> cb, MadeiraPFXDrawable *px, int mode, double after) {
  px->_mode = mode;
  px->_after = after;
  if (!px->_pending) {
    px->_pending = 1;
    atomic_fetch_add_explicit(&g_pfx_pending, 1, memory_order_relaxed);
  }
  objc_setAssociatedObject(cb, &g_pfx_key, px, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
}
/* MTLCommandBuffer_commit: the game's buffer is on its way; now its picture is presented from a buffer of ours on
 * the same queue, through the present call's own code (limits, hooks, frame generation). */
static void madeira_pfx_after_commit(id<MTLCommandBuffer> cb) {
  MadeiraPFXDrawable *px = objc_getAssociatedObject(cb, &g_pfx_key);
  unsigned long long n;
  if (!px) return;
  [px retain];
  objc_setAssociatedObject(cb, &g_pfx_key, nil, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
  if (px->_pending) {
    px->_pending = 0;
    atomic_fetch_sub_explicit(&g_pfx_pending, 1, memory_order_relaxed);
  }
  @autoreleasepool {
    id<MTLCommandBuffer> out = [cb.commandQueue commandBuffer];
    if (out && px->_mode == 1) {
      id<CAMetalDrawable> d = madeira_pfx_resolve(out, px);
      if (d) {
        madeira_log_present_cadence("presentDrawableAfterMinDuration", px->_after);
        [out presentDrawable:d afterMinimumDuration:px->_after];
      }
      madeira_pfx_commit(out);
    } else if (out) {
      struct unixcall_generic_obj_obj_noret p;
      memset(&p, 0, sizeof p);
      p.handle = (obj_handle_t)out;
      p.arg = (obj_handle_t)px;
      g_pfx_tls.deferred = 1;
      g_pfx_tls.committed = 0;
      _MTLCommandBuffer_presentDrawable(&p);
      g_pfx_tls.deferred = 0;
      if (!g_pfx_tls.committed) madeira_pfx_commit(out);
    }
  }
  madeira_pfx_slot_free(px);
  [px release];
  n = atomic_fetch_add_explicit(&g_pfx_frames, 1, memory_order_relaxed) + 1;
  if (n == 1 || (n % 1800) == 0)
    dprintf(STDERR_FILENO, "[present-fx] madeira-bcd %llu frames: %llu enlarged by MetalFX, %llu copied, %llu bilinear, %llu dropped "
                           "(generated frames included)\n", n, atomic_load_explicit(&g_pfx_scaled, memory_order_relaxed),
            atomic_load_explicit(&g_pfx_copied, memory_order_relaxed), atomic_load_explicit(&g_pfx_bilinear, memory_order_relaxed),
            atomic_load_explicit(&g_pfx_dropped, memory_order_relaxed));
}

'''

A_PRESENT_TOP = """    wmtr_call(RM_OP_PRESENT_DRAWABLE, &a, sizeof a, 0, 0, 0);
    return STATUS_SUCCESS;
  }
"""
PRESENT_TOP = A_PRESENT_TOP + """  if (madeira_pfx_proxy((id)params->arg) && !g_pfx_tls.deferred) {   /* """ + MARKER + """: a stand-in is presented when this buffer is committed */
    madeira_pfx_defer((id<MTLCommandBuffer>)params->handle, (MadeiraPFXDrawable *)params->arg, 0, 0.0);
    return STATUS_SUCCESS;
  }
"""

A_PRESENT_FG = """    madeira_log_present_cadence("presentFramegen", 0.0);
    return STATUS_SUCCESS;
  }
"""
PRESENT_FG = A_PRESENT_FG + """  if (madeira_pfx_proxy((id)params->arg)) {   /* """ + MARKER + """: the picture goes into a real drawable now, and that is presented below */
    params->arg = (obj_handle_t)madeira_pfx_resolve((id<MTLCommandBuffer>)params->handle, (MadeiraPFXDrawable *)params->arg);
    if (!params->arg) return STATUS_SUCCESS;
  }
"""

A_PRESENT_AFTER = """  struct unixcall_generic_obj_obj_double_noret *params = obj;
  madeira_log_present_cadence("presentDrawableAfterMinDuration", params->arg1);
"""
PRESENT_AFTER = """  struct unixcall_generic_obj_obj_double_noret *params = obj;
  if (madeira_pfx_proxy((id)params->arg0)) {   /* """ + MARKER + """: as in MTLCommandBuffer_presentDrawable */
    madeira_pfx_defer((id<MTLCommandBuffer>)params->handle, (MadeiraPFXDrawable *)params->arg0, 1, params->arg1);
    return STATUS_SUCCESS;
  }
  madeira_log_present_cadence("presentDrawableAfterMinDuration", params->arg1);
"""

A_NEXT = """  struct timespec t0, t1;
  clock_gettime(CLOCK_MONOTONIC, &t0);
  params->ret = (obj_handle_t)[(CAMetalLayer *)params->handle nextDrawable];
"""
NEXT = """  if (madeira_pfx_on()) {   /* """ + MARKER + """: a picture of the game's size stands in for the drawable until the frame is committed */
    id pfx_stand_in = nil;
    int pfx_how = madeira_pfx_next((CAMetalLayer *)params->handle, &pfx_stand_in);
    if (pfx_how) {
      params->ret = pfx_how > 0 ? (obj_handle_t)pfx_stand_in : 0;
      return STATUS_SUCCESS;
    }
  }
""" + A_NEXT

A_SETPROPS = "    layer.drawableSize = CGSizeMake(props->drawable_width, props->drawable_height);\n"
SETPROPS = ("    layer.drawableSize = madeira_pfx_layer_size(layer, props->drawable_width, props->drawable_height);   /* " + MARKER
            + ": the enlarged size where it upscales; else the size given, as before */\n")

A_GETPROPS = """  props->drawable_height = layer.drawableSize.height;
  props->drawable_width = layer.drawableSize.width;
"""
GETPROPS = A_GETPROPS + ("  madeira_pfx_virtual_size(layer, &props->drawable_width, &props->drawable_height);   /* " + MARKER
                         + ": the size the game's side asked for */\n")

A_CTL = "  case 6:     /* ml1136: requested fence-chain mode (0 = none) */\n"
CTL = """  case 30: {  /* """ + MARKER + """ (tools/patch-winemetal-present-fx.py): ptr = five uint32: winemetal enlarges the picture itself
               * (1/0), the upscaling mode (0 none, 1 a factor, 2 to the screen), the factor times 1000, the screen's long
               * and short side in pixels */
    uint32_t *o = (uint32_t *)(uintptr_t)a->ptr;
    int on;
    if (!o || a->len < 5 * sizeof(uint32_t) || wmtr_enabled()) break;
    on = madeira_pfx_on();
    if (on) madeira_pfx_up_init();
    o[0] = on ? 1u : 0u;
    o[1] = on && atomic_load_explicit(&g_pfx_up_mode, memory_order_relaxed) > 0 ? (uint32_t)atomic_load_explicit(&g_pfx_up_mode, memory_order_relaxed) : 0u;
    o[2] = (uint32_t)atomic_load_explicit(&g_pfx_up_milli, memory_order_relaxed);
    o[3] = (uint32_t)atomic_load_explicit(&g_pfx_screen_long, memory_order_relaxed);
    o[4] = (uint32_t)atomic_load_explicit(&g_pfx_screen_short, memory_order_relaxed);
    a->ret = 1;
    break;
  }
""" + A_CTL

PAIRS = (
    ("early declarations", A_EARLY, EARLY + A_EARLY),
    ("command buffer commit", A_COMMIT, COMMIT),
    ("stand-in block", A_BLOCK_E, BLOCK_E + A_BLOCK_E),
    ("drawables needed", A_NEED, NEED),
    ("extra drawable size", A_FITS, FITS),
    ("generated frame copy", A_BLIT, BLIT),
    ("the two presents", A_PAIR, PAIR),
    ("wrapper readable layer", A_WRAP_FBO, WRAP_FBO),
    ("wrapper track", A_WRAP_TRACK, WRAP_TRACK),
    ("present thunk", A_THUNK, BLOCK_L + A_THUNK),
    ("present: stand-in noted", A_PRESENT_TOP, PRESENT_TOP),
    ("present: stand-in resolved", A_PRESENT_FG, PRESENT_FG),
    ("present after minimum duration", A_PRESENT_AFTER, PRESENT_AFTER),
    ("next drawable", A_NEXT, NEXT),
    ("layer set props", A_SETPROPS, SETPROPS),
    ("layer get props", A_GETPROPS, GETPROPS),
    ("MadeiraCtl", A_CTL, CTL),
)


def main():
    if not PATH.exists():
        print(f"::error::{PATH} not found (run from the repository root)")
        return 1
    s = PATH.read_text()
    if MARKER in s:
        print(f"{PATH}: present-fx already patched")
        return 0
    if NEEDS not in s:
        if "madeira_fg_other_layer" not in s:
            # An older dxmt checkout, which tools/patch-winemetal-framegen-live.py left as it is (see there): so does this.
            print(f"{PATH}: no frame generation live control in this tree (an older dxmt checkout): present-fx builds on it and is left out")
            return 0
        print(f"::error::{PATH}: tools/patch-winemetal-framegen-live.py has to run first")
        return 1
    for name, anchor, _ in PAIRS:
        if s.count(anchor) != 1:
            print(f"::error::{PATH}: {name} anchor found {s.count(anchor)} times, not once -- dxmt moved, review tools/patch-winemetal-present-fx.py")
            return 1
    for _, anchor, new in PAIRS:
        s = s.replace(anchor, new, 1)
    PATH.write_text(s)
    print(f"{PATH}: present-fx patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
