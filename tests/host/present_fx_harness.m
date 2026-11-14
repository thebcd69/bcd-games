// SPDX-License-Identifier: GPL-3.0-only
// Copyright 2026 thebcd69 (BCD)
// BCD Material: additional terms under GPL-3.0 section 7 apply; see LICENSE-BCD.md.

// The behaviour half of tests/host/check-present-fx.py. That script puts the code tools/patch-winemetal-present-fx.py adds to
// winemetal_unix.c (its blocks and its hook lines, taken from the script itself) between the marked lines below and compiles the
// result on the host (Objective-C without ARC, as winemetal is). There is no GPU here: the layer, drawables, textures, command
// buffers and MetalFX's scaler are small stand-ins that record what is done to them, so the test reads the order of the
// operations: which command buffer was committed when, which drawable was taken when, what was encoded into what.
#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <Metal/Metal.h>
#import <MetalFX/MetalFX.h>
#include <objc/message.h>
#include <objc/runtime.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef uint64_t obj_handle_t;
typedef int NTSTATUS;
#define STATUS_SUCCESS 0
struct unixcall_generic_obj_noret { obj_handle_t handle; };
struct unixcall_generic_obj_obj_noret { obj_handle_t handle; obj_handle_t arg; };
struct unixcall_generic_obj_obj_ret { obj_handle_t handle; obj_handle_t ret; };
struct unixcall_generic_obj_obj_double_noret { obj_handle_t handle; obj_handle_t arg0; double arg1; };
struct madeira_ctl_args { uint32_t op; uint32_t ret; uint64_t ptr; uint64_t len; char name[160]; };

// ---- the record --------------------------------------------------------------------------------------------------------------
static NSMutableArray<NSString *> *g_ops;
static NSMutableString *g_log;
static void op(NSString *fmt, ...) NS_FORMAT_FUNCTION(1, 2);
static void op(NSString *fmt, ...) {
  va_list ap; va_start(ap, fmt);
  NSString *s = [[[NSString alloc] initWithFormat:fmt arguments:ap] autorelease];
  va_end(ap);
  @synchronized (g_ops) { [g_ops addObject:s]; }
}
static int fake_dprintf(int fd, const char *fmt, ...) {
  char b[1024]; va_list ap; (void)fd;
  va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
  @synchronized (g_log) { [g_log appendFormat:@"%s", b]; }
  return 0;
}
#define dprintf fake_dprintf

// ---- stand-ins ---------------------------------------------------------------------------------------------------------------
static int g_next_id;
@interface FakeDevice : NSObject
@end
@interface FakeTexture : NSObject
@property (nonatomic) NSUInteger width, height;
@property (nonatomic) MTLPixelFormat pixelFormat;
@property (nonatomic, assign) FakeDevice *device;
@property (nonatomic, copy) NSString *name;
@end
@implementation FakeTexture
- (void)dealloc { [_name release]; [super dealloc]; }
@end
static int g_textures_made, g_scalers_made, g_pipelines_made, g_fx_supported = 1, g_fx_refuse;
@implementation FakeDevice
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)d {
  FakeTexture *t = [[FakeTexture alloc] init];
  t.width = d.width; t.height = d.height; t.pixelFormat = d.pixelFormat; t.device = self;
  t.name = [NSString stringWithFormat:@"tex%d(%lux%lu)", ++g_next_id, (unsigned long)d.width, (unsigned long)d.height];
  g_textures_made++;
  return t;
}
- (id)newLibraryWithSource:(NSString *)src options:(id)o error:(NSError **)e { (void)src; (void)o; (void)e; return [[NSObject alloc] init]; }
- (id)newRenderPipelineStateWithDescriptor:(id)d error:(NSError **)e { (void)d; (void)e; g_pipelines_made++; return [[NSObject alloc] init]; }
@end
@interface NSObject (FakeLibrary)
- (id)newFunctionWithName:(NSString *)n;
@end
@implementation NSObject (FakeLibrary)
- (id)newFunctionWithName:(NSString *)n { (void)n; return [[NSObject alloc] init]; }
@end

@class FakeLayer;
@interface FakeDrawable : NSObject
@property (nonatomic, retain) FakeTexture *texture;
@property (nonatomic, assign) FakeLayer *layer;
@property (nonatomic) int number;
- (void)addPresentedHandler:(void (^)(id))h;
@end
@implementation FakeDrawable
- (void)addPresentedHandler:(void (^)(id))h { (void)h; }
- (void)dealloc { [_texture release]; [super dealloc]; }
@end
@interface FakeLayer : NSObject
@property CGSize drawableSize;
@property MTLPixelFormat pixelFormat;
@property (assign) FakeDevice *device;
@property BOOL framebufferOnly;
@property int refuse;           // nextDrawable answers nil this many times
@property int taken;
- (id)nextDrawable;
- (NSUInteger)maximumDrawableCount;
@end
@implementation FakeLayer
- (id)nextDrawable {
  if (self.refuse > 0) { self.refuse--; op(@"layer nextDrawable -> nil"); return nil; }
  FakeDrawable *d = [[[FakeDrawable alloc] init] autorelease];
  FakeTexture *t = [[[FakeTexture alloc] init] autorelease];
  CGSize s = self.drawableSize;
  t.width = (NSUInteger)s.width; t.height = (NSUInteger)s.height; t.pixelFormat = self.pixelFormat; t.device = self.device;
  d.number = ++_taken;
  t.name = [NSString stringWithFormat:@"drawable%d(%lux%lu)", d.number, (unsigned long)t.width, (unsigned long)t.height];
  d.texture = t; d.layer = self;
  op(@"layer nextDrawable -> %@", t.name);
  return d;
}
- (NSUInteger)maximumDrawableCount { return 3; }
@end

@interface FakeQueue : NSObject
- (id)commandBuffer;
@end
@interface FakeCommandBuffer : NSObject
@property (nonatomic, assign) FakeQueue *commandQueue;
@property (nonatomic, copy) NSString *name;
@property (nonatomic) int committed;
@end
@interface FakeEncoder : NSObject
@property (nonatomic, assign) FakeCommandBuffer *cb;
@end
static NSString *texname(id t) { return [t respondsToSelector:@selector(name)] ? [(FakeTexture *)t name] : @"?"; }
@implementation FakeEncoder
- (void)copyFromTexture:(id)a toTexture:(id)b { op(@"%@: copy %@ -> %@", self.cb.name, texname(a), texname(b)); }
- (void)setRenderPipelineState:(id)p { (void)p; }
- (void)setFragmentTexture:(id)t atIndex:(NSUInteger)i { (void)i; op(@"%@: bilinear from %@", self.cb.name, texname(t)); }
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)c { (void)t; (void)s; (void)c; }
- (void)endEncoding {}
@end
@implementation FakeCommandBuffer
- (id)enc { FakeEncoder *e = [[[FakeEncoder alloc] init] autorelease]; e.cb = self; return e; }
- (id)blitCommandEncoder { return [self enc]; }
- (id)renderCommandEncoderWithDescriptor:(id)d { (void)d; return [self enc]; }
- (void)presentDrawable:(id)d { op(@"%@: present %@", self.name, texname([d texture])); }
- (void)presentDrawable:(id)d atTime:(CFTimeInterval)t { (void)t; op(@"%@: present %@ at a later time", self.name, texname([d texture])); }
- (void)presentDrawable:(id)d afterMinimumDuration:(CFTimeInterval)t { op(@"%@: present %@ after %.3f", self.name, texname([d texture]), t); }
- (void)commit { if (self.committed++) op(@"%@: COMMITTED TWICE", self.name); else op(@"%@: commit", self.name); }
- (void)dealloc { [_name release]; [super dealloc]; }
@end
static int g_cb_number;
@implementation FakeQueue
- (id)commandBuffer {
  FakeCommandBuffer *cb = [[[FakeCommandBuffer alloc] init] autorelease];
  cb.commandQueue = self;
  cb.name = [NSString stringWithFormat:@"own%d", ++g_cb_number];
  return cb;
}
@end

@interface FakeScaler : NSObject
@property (nonatomic, assign) id colorTexture, outputTexture, fence;
@property (nonatomic) MTLTextureUsage outputTextureUsage;
@property (nonatomic) NSUInteger iw, ih, ow, oh;
@end
@implementation FakeScaler
- (void)encodeToCommandBuffer:(id)cb {
  op(@"%@: MetalFX %@ -> %@", [(FakeCommandBuffer *)cb name], texname(self.colorTexture), texname(self.outputTexture));
}
@end
@interface FakeFXDescriptor : NSObject
@property MTLPixelFormat colorTextureFormat, outputTextureFormat;
@property NSUInteger inputWidth, inputHeight, outputWidth, outputHeight;
@end
@implementation FakeFXDescriptor
+ (BOOL)supportsDevice:(id)d { (void)d; return g_fx_supported != 0; }
- (id)newSpatialScalerWithDevice:(id)d {
  (void)d;
  if (g_fx_refuse) return nil;
  FakeScaler *s = [[FakeScaler alloc] init];
  s.iw = self.inputWidth; s.ih = self.inputHeight; s.ow = self.outputWidth; s.oh = self.outputHeight;
  s.outputTextureUsage = MTLTextureUsageRenderTarget;
  g_scalers_made++;
  return s;
}
@end
#define MTLFXSpatialScalerDescriptor FakeFXDescriptor
// The render pass and pipeline descriptors of the bilinear pass: plain objects here (the real ones look at their textures).
@interface FakeAttachment : NSObject
@property (assign) id texture; @property NSUInteger loadAction, storeAction; @property MTLPixelFormat pixelFormat;
@end
@implementation FakeAttachment
@end
@interface FakeAttachments : NSObject { FakeAttachment *_a; }
- (FakeAttachment *)objectAtIndexedSubscript:(NSUInteger)i;
@end
@implementation FakeAttachments
- (FakeAttachment *)objectAtIndexedSubscript:(NSUInteger)i { (void)i; if (!_a) _a = [[FakeAttachment alloc] init]; return _a; }
@end
@interface FakePassDescriptor : NSObject
@property (retain) FakeAttachments *colorAttachments; @property (assign) id vertexFunction, fragmentFunction;
+ (instancetype)renderPassDescriptor;
@end
@implementation FakePassDescriptor
- (instancetype)init { self = [super init]; _colorAttachments = [[FakeAttachments alloc] init]; return self; }
+ (instancetype)renderPassDescriptor { return [[[self alloc] init] autorelease]; }
@end
#define MTLRenderPassDescriptor FakePassDescriptor
#define MTLRenderPipelineDescriptor FakePassDescriptor

// ---- what the code under test calls in winemetal_unix.c ----------------------------------------------------------------------------
static int g_mode_key, g_fg_on_sim, g_sim_generate, g_wmtr, g_hooks, g_tracked;
static unsigned long long g_wait_ns_calls;
static int madeira_fgl_mode(void) { return g_mode_key; }
static int wmtr_enabled(void) { return g_wmtr; }
static int madeira_fg_enabled(void) { return g_fg_on_sim; }
static void *g_fg_layer;
static void madeira_fgl_track(id d) { (void)d; g_tracked++; }
static int madeira_frame_hooks_on(void) { return g_hooks; }
static void ios_frame_drawable_wait(unsigned long long ns) { (void)ns; g_wait_ns_calls++; }
static void madeira_log_present_cadence(const char *what, double v) { (void)v; op(@"cadence %s", what); }
static NTSTATUS _MTLCommandBuffer_commit(void *obj);

/*@EARLY@*/
/*@BLOCK_E@*/
/*@BLOCK_L@*/

// The four thunks, cut down to the old behaviour plus the script's own hook lines.
static NTSTATUS _MTLCommandBuffer_commit(void *obj) {
  struct unixcall_generic_obj_noret *params = obj;
/*@COMMIT@*/
  return STATUS_SUCCESS;
}
static int g_legacy_presents;
static int harness_frame_generation(id<MTLCommandBuffer> cb, id drawable);
static NTSTATUS _MTLCommandBuffer_presentDrawable(void *obj) {
  struct unixcall_generic_obj_obj_noret *params = obj;
/*@PRESENT_TOP@*/
  g_legacy_presents++;
  if (g_sim_generate && harness_frame_generation((id<MTLCommandBuffer>)params->handle, (id)params->arg)) {
    madeira_log_present_cadence("presentFramegen", 0.0);
    return STATUS_SUCCESS;
  }
/*@PRESENT_FG@*/
  madeira_log_present_cadence("presentDrawable", 0.0);
  [(id<MTLCommandBuffer>)params->handle presentDrawable:(id<MTLDrawable>)params->arg];
  return STATUS_SUCCESS;
}
static NTSTATUS _MTLCommandBuffer_presentDrawableAfterMinimumDuration(void *obj) {
/*@PRESENT_AFTER@*/
  [(id<MTLCommandBuffer>)params->handle presentDrawable:(id<MTLDrawable>)params->arg0 afterMinimumDuration:params->arg1];
  return STATUS_SUCCESS;
}
static NTSTATUS _MetalLayer_nextDrawable(void *obj) {
  struct unixcall_generic_obj_obj_ret *params = obj;
/*@NEXT@*/
  return STATUS_SUCCESS;
}
static void layer_set_props(FakeLayer *fake, double w, double h) {
  CAMetalLayer *layer = (CAMetalLayer *)fake;
  struct { double drawable_width, drawable_height; } p = { w, h }, *props = &p;
/*@SETPROPS@*/
}
static void layer_get_props(FakeLayer *fake, double *w, double *h) {
  CAMetalLayer *layer = (CAMetalLayer *)fake;
  struct { double drawable_width, drawable_height; } p, *props = &p;
/*@GETPROPS@*/
  *w = p.drawable_width; *h = p.drawable_height;
}
static uint32_t ctl30(uint32_t out[5]) {
  struct madeira_ctl_args args, *a = &args;
  memset(&args, 0, sizeof args);
  a->op = 30; a->ptr = (uint64_t)(uintptr_t)out; a->len = 5 * sizeof(uint32_t);
  switch (a->op) {
/*@CTL@*/
    break;
  }
  return a->ret;
}
// What frame generation's present function does with a stand-in (the lines the script puts into madeira_fg_present_inner0 and its
// wrapper): the generated picture is enlarged into an extra drawable and presented on the buffer it was given, then the pair is finished.
static int harness_frame_generation(id<MTLCommandBuffer> cb, id drawable) {
  FakeDevice *dev = [(FakeTexture *)[drawable texture] device];
  MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:[(FakeTexture *)[drawable texture] pixelFormat]
                                                                               width:[(FakeTexture *)[drawable texture] width]
                                                                              height:[(FakeTexture *)[drawable texture] height] mipmapped:NO];
  FakeTexture *generated = [[dev newTextureWithDescriptor:d] autorelease];
  generated.name = @"generated";
  if (!madeira_pfx_proxy(drawable)) return 0;
  id extra = [(CAMetalLayer *)[drawable layer] nextDrawable];
  if (!extra) return 0;
  if (!madeira_pfx_emit(cb, (id<MTLTexture>)generated, [extra texture], ((MadeiraPFXDrawable *)drawable)->_entry)) return 0;
  madeira_fgl_track(extra);
  [cb presentDrawable:extra];
  g_pfx_tls.pair_half = 0.008;
  madeira_pfx_finish_pair(cb, (MadeiraPFXDrawable *)drawable);
  return 1;
}

// ---- the test ------------------------------------------------------------------------------------------------------------------
static int g_fails;
static void expect(int ok, const char *what) { printf("%s %s\n", ok ? "ok  " : "FAIL", what); if (!ok) g_fails++; }
static void drain_main(void) { [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.02]]; }
static NSString *ops_text(void) { return [g_ops componentsJoinedByString:@" | "]; }
static void reset_ops(void) { [g_ops removeAllObjects]; }
static int has(NSString *needle) { return [ops_text() rangeOfString:needle].location != NSNotFound; }
static int before(NSString *a, NSString *b) {
  NSString *t = ops_text();
  NSRange ra = [t rangeOfString:a], rb = [t rangeOfString:b];
  return ra.location != NSNotFound && rb.location != NSNotFound && ra.location < rb.location;
}
// One frame as the game's side makes it: next drawable, its texture, present on its own command buffer, commit.
static id g_last_handle;
static FakeCommandBuffer *game_frame(FakeLayer *layer, FakeQueue *q, int how, NSString **texture_name) {
  struct unixcall_generic_obj_obj_ret n = { (obj_handle_t)layer, 0 };
  FakeCommandBuffer *cb = [q commandBuffer];
  cb.name = @"game";
  _MetalLayer_nextDrawable(&n);
  g_last_handle = (id)n.ret;
  if (texture_name) *texture_name = n.ret ? [texname([(id)n.ret texture]) copy] : nil;   /* kept past the caller's pool; never released */
  if (!n.ret) return nil;
  if (how == 1) {
    struct unixcall_generic_obj_obj_double_noret p = { (obj_handle_t)cb, n.ret, 1.0 / 30.0 };
    _MTLCommandBuffer_presentDrawableAfterMinimumDuration(&p);
  } else if (how == 0) {
    struct unixcall_generic_obj_obj_noret p = { (obj_handle_t)cb, n.ret };
    _MTLCommandBuffer_presentDrawable(&p);
  }
  if (how != 3) {
    struct unixcall_generic_obj_noret c = { (obj_handle_t)cb };
    _MTLCommandBuffer_commit(&c);
  }
  return cb;
}
static _Atomic int g_holder_ready, g_holder_release;
/* holds the lock until told to let go */
static void *holder_wait(void *arg) {
  (void)arg;
  pthread_mutex_lock(&g_pfx_mu);
  atomic_store(&g_holder_ready, 1);
  while (!atomic_load(&g_holder_release)) usleep(1000);
  pthread_mutex_unlock(&g_pfx_mu);
  return NULL;
}
/* takes the lock and is gone: a thread killed with its process */
static void *holder_dead(void *arg) { (void)arg; pthread_mutex_lock(&g_pfx_mu); return NULL; }

int main(int argc, char **argv) {
  int phase = argc > 1 ? atoi(argv[1]) : 0;
  @autoreleasepool {
    FakeDevice *dev = [[FakeDevice alloc] init];
    FakeLayer *layer = [[FakeLayer alloc] init];
    FakeQueue *q = [[FakeQueue alloc] init];
    NSString *tn = nil;
    double w, h;
    uint32_t c[5];
    g_ops = [[NSMutableArray alloc] init];
    g_log = [[NSMutableString alloc] init];
    layer.device = dev; layer.pixelFormat = MTLPixelFormatBGRA8Unorm;

    if (phase == 0) {
      /* ---- the key is off: nothing of present-fx happens, whatever the app sets */
      g_mode_key = 0;
      madeira_pfx_set_screen(1206, 2622);
      madeira_pfx_set_upscale(1, 1500);
      layer_set_props(layer, 1408, 648);
      expect(layer.drawableSize.width == 1408 && layer.drawableSize.height == 648, "key off: the layer gets the size the game's side asked for");
      layer_get_props(layer, &w, &h);
      expect(w == 1408 && h == 648, "key off: the size read back is the layer's own");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn isEqualToString:@"drawable1(1408x648)"] && [ops_text() isEqualToString:
             @"layer nextDrawable -> drawable1(1408x648) | cadence presentDrawable | game: present drawable1(1408x648) | game: commit"],
             "key off: the layer's own drawable, presented on the game's buffer, one commit, nothing else");
      expect(!g_pfx_class && !atomic_load(&g_pfx_pending) && g_textures_made == 0 && g_scalers_made == 0 && g_cb_number == 1,
             "key off: no stand-in, no picture, no scaler, no command buffer of ours");
      expect(ctl30(c) == 1 && c[0] == 0 && c[1] == 0 && c[3] == 2622 && c[4] == 1206, "key off: MadeiraCtl 30 says the presenter does not enlarge, and gives the screen's size");
      g_wmtr = 1; g_mode_key = 1;
      expect(!madeira_pfx_on() && ctl30(c) == 0, "the remote Metal path: off too");
      return g_fails ? 1 : 0;
    }

    g_mode_key = 1;
    madeira_pfx_set_screen(2622, 1206);
    if (phase == 1) {
      /* ---- key on, nothing to do: the layer's own drawables */
      layer_set_props(layer, 1408, 648);
      expect(layer.drawableSize.width == 1408, "key on, no upscaling: the layer keeps the game's size");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn isEqualToString:@"drawable1(1408x648)"] && has(@"game: present drawable1(1408x648)") && g_textures_made == 0 && !g_pfx_class,
             "key on, no upscaling, no frame generation: the layer's own drawable as without present-fx");

      /* ---- a factor of 1.5, chosen while the game runs */
      madeira_pfx_set_upscale(1, 1500);
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn isEqualToString:@"tex1(1408x648)"], "1.5 chosen: the game's side now gets a picture of its own size in place of the drawable");
      expect(has(@"copy tex1(1408x648) -> drawable2(1408x648)") && has(@": present drawable2(1408x648)") && before(@"game: commit", @"layer nextDrawable"),
             "the frame in between (the layer is not resized yet): copied as it is, and its drawable is taken only after the game's buffer is committed");
      drain_main();
      expect(layer.drawableSize.width == 2112 && layer.drawableSize.height == 972, "the real layer follows on the main thread: 2112x972");
      layer_get_props(layer, &w, &h);
      expect(w == 1408 && h == 648, "the game's side still reads the size it asked for");
      reset_ops(); g_cb_number = 0;
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([ops_text() isEqualToString:
              @"game: commit | layer nextDrawable -> drawable3(2112x972) | own2: MetalFX tex1(1408x648) -> tex2(2112x972) | "
               "own2: copy tex2(2112x972) -> drawable3(2112x972) | cadence presentDrawable | own2: present drawable3(2112x972) | own2: commit"],
             "a frame at 1.5: game's buffer committed, then a drawable is taken, MetalFX enlarges the picture into it, present, commit, in that order");
      expect(g_scalers_made == 1 && g_legacy_presents == 3 && !atomic_load(&g_pfx_pending), "one scaler; the present call's own code ran once per frame; nothing pending");
      {
        int i, made = g_textures_made;
        for (i = 0; i < 50; i++) @autoreleasepool { game_frame(layer, q, 0, &tn); }
        expect(g_textures_made == made && g_scalers_made == 1 && [tn isEqualToString:@"tex1(1408x648)"], "50 more frames reuse the one picture and the one scaler");
      }
      {
        int mode, gw, gh, sw, sh, kind;
        mode = madeira_pfx_status(&gw, &gh, &sw, &sh, &kind);
        expect(mode == 1 && gw == 1408 && gh == 648 && sw == 2112 && sh == 972 && kind == 1, "the menu's status: mode, both sizes, MetalFX");
      }
      expect(ctl30(c) == 1 && c[0] == 1 && c[1] == 1 && c[2] == 1500, "MadeiraCtl 30: the presenter enlarges (the Direct3D 12 runtime makes no scaler)");

      /* ---- to the screen */
      madeira_pfx_set_upscale(2, 0);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      drain_main();
      expect(layer.drawableSize.width == 2620 && layer.drawableSize.height == 1206, "to the screen: 1408x648 on a 2622x1206 screen is 2620x1206 (even sizes)");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(has(@"MetalFX tex1(1408x648) -> ") && has(@"(2620x1206)") && g_scalers_made == 2, "to the screen: a scaler for the new pair of sizes");

      /* ---- presentDrawable:afterMinimumDuration: from the game's side */
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 1, &tn); }
      expect(before(@"game: commit", @"layer nextDrawable") && has(@"after 0.033") && has(@"cadence presentDrawableAfterMinDuration") && !has(@"COMMITTED TWICE"),
             "a present after a minimum duration is deferred the same way and keeps its duration");

      /* ---- the layer gives no drawable */
      layer.refuse = 1; reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(has(@"layer nextDrawable -> nil") && !has(@": present ") && has(@"game: commit") && [g_log rangeOfString:@"a frame was dropped: the layer gave no drawable"].location != NSNotFound,
             "no drawable: the frame is dropped, nothing is presented, a line says so");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(has(@": present drawable") && [tn isEqualToString:@"tex1(1408x648)"], "the next frame is shown again, with the same picture slot");

      /* ---- a buffer that is never committed: the stand-in frees its picture when it is released */
      @autoreleasepool { game_frame(layer, q, 3, &tn); }
      expect(!atomic_load(&g_pfx_pending), "a presented stand-in whose buffer was dropped is not counted as pending after its release");
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn isEqualToString:@"tex1(1408x648)"], "and its picture is free again");

      /* ---- three stand-ins out at once, a fourth has no picture: no drawable this time */
      @autoreleasepool {
        struct unixcall_generic_obj_obj_ret n[4];
        int i;
        for (i = 0; i < 4; i++) { n[i].handle = (obj_handle_t)layer; n[i].ret = 0; _MetalLayer_nextDrawable(&n[i]); }
        expect(n[0].ret && n[1].ret && n[2].ret && !n[3].ret && [(id)n[0].ret texture] != [(id)n[1].ret texture] && [(id)n[1].ret texture] != [(id)n[2].ret texture],
               "three stand-ins have three pictures; a fourth gets none while the layer is enlarged (the frame is skipped, not drawn into a corner)");
      }
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(tn != nil, "after they are released a frame gets a picture again");

      /* ---- MetalFX cannot do the pair of sizes: bilinear */
      g_fx_refuse = 1;
      madeira_pfx_set_upscale(1, 2000);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      drain_main(); reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(layer.drawableSize.width == 2816 && has(@"bilinear from tex1(1408x648)") && !has(@"MetalFX") && has(@": present drawable")
             && [g_log rangeOfString:@"no MetalFX scaler for 1408x648"].location != NSNotFound, "MetalFX refuses the pair of sizes: the plain bilinear pass, and a line says so once");
      g_fx_refuse = 0;

      /* ---- the game's side changes its own size */
      layer_set_props(layer, 1920, 1080);
      expect(layer.drawableSize.width == 3840 && layer.drawableSize.height == 2160, "the game resizes to 1920x1080 at 2x: the layer gets 3840x2160");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasSuffix:@"(1920x1080)"] && has(@"MetalFX") && has(@"(3840x2160)"), "new pictures of the new size, a new scaler");

      /* ---- off again, while the game runs */
      madeira_pfx_set_upscale(0, 0);
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasSuffix:@"(1920x1080)"] && has(@"bilinear from"), "off chosen: the frame in between still goes through a picture (the layer is still large)");
      drain_main();
      expect(layer.drawableSize.width == 1920, "the real layer is back at the game's size");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasPrefix:@"drawable"] && has(@"game: present drawable") && before(@"layer nextDrawable", @"game: commit"),
             "and from then on the game's side gets the layer's own drawable again, presented on its own buffer");
      expect(!has(@"COMMITTED TWICE") && [g_log rangeOfString:@"COMMITTED TWICE"].location == NSNotFound, "no buffer was ever committed twice");
      return g_fails ? 1 : 0;
    }
    if (phase == 2) {
      /* ---- frame generation with a stand-in: the generated frame first, from its own buffer, then the real one */
      layer_set_props(layer, 1408, 648);
      madeira_pfx_set_upscale(1, 1500);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      drain_main();
      g_fg_on_sim = 1; g_sim_generate = 1; g_fg_layer = (void *)layer; g_hooks = 1;
      reset_ops(); g_cb_number = 0; g_tracked = 0;
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      if (getenv("PFX_SHOW")) printf("%s\n", ops_text().UTF8String);
      expect([ops_text() isEqualToString:
              @"game: commit | layer nextDrawable -> drawable2(2112x972) | own2: MetalFX generated -> tex3(2112x972) | own2: copy tex3(2112x972) -> drawable2(2112x972) | "
               "own2: present drawable2(2112x972) | own2: commit | layer nextDrawable -> drawable3(2112x972) | own3: MetalFX tex1(1408x648) -> tex3(2112x972) | "
               "own3: copy tex3(2112x972) -> drawable3(2112x972) | own3: present drawable3(2112x972) at a later time | own3: commit | cadence presentFramegen"],
             "a generated pair: the game's buffer, then the generated frame (enlarged) in a buffer committed before the real frame's drawable is taken, then the real frame at a later time");
      expect(g_tracked == 2 && g_wait_ns_calls == 1 && g_scalers_made == 1, "both frames are counted on their way to the screen; the wait for the real drawable is reported; one scaler serves both");
      /* frame generation on without upscaling: stand-ins too, copies instead of the scaler */
      g_sim_generate = 0;
      madeira_pfx_set_upscale(0, 0);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      drain_main(); reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasPrefix:@"tex"] && has(@"copy tex1(1408x648) -> drawable") && !has(@"MetalFX") && !has(@"bilinear"),
             "frame generation on, no upscaling: still a picture of the game's size (so the real drawable is taken late), copied as it is");
      g_fg_on_sim = 0;
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasPrefix:@"drawable"], "frame generation off and no upscaling: the layer's own drawable again");
      return g_fails ? 1 : 0;
    }
    if (phase == 3) {
      /* ---- the lock is held for good by a thread that is gone: frames are dropped, then present-fx gives up and the layers go back */
      pthread_t t;
      int i;
      layer_set_props(layer, 1408, 648);
      madeira_pfx_set_upscale(1, 1500);
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      drain_main();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect(layer.drawableSize.width == 2112 && [tn hasPrefix:@"tex"], "set up at 1.5");
      pthread_create(&t, NULL, holder_wait, NULL);
      while (!atomic_load(&g_holder_ready)) usleep(1000);
      @autoreleasepool {
        struct unixcall_generic_obj_obj_ret n = { (obj_handle_t)layer, 0 };
        _MetalLayer_nextDrawable(&n);
        expect(!n.ret, "the lock is busy when a frame starts: no drawable for that frame (it is skipped), nothing waits");
      }
      atomic_store(&g_holder_release, 1);
      pthread_join(t, NULL);
      /* a stand-in that was already out when the lock was lost for good: its frames are dropped, 20 in a row turn present-fx off */
      {
        id held[3] = { nil, nil, nil };
        NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];
        struct unixcall_generic_obj_obj_ret n = { (obj_handle_t)layer, 0 };
        _MetalLayer_nextDrawable(&n);
        held[0] = (id)n.ret;
        pthread_create(&t, NULL, holder_dead, NULL); pthread_join(t, NULL);
        reset_ops();
        for (i = 0; i < 20 && held[0]; i++) {
          FakeCommandBuffer *cb = [q commandBuffer];
          struct unixcall_generic_obj_obj_noret p = { (obj_handle_t)cb, (obj_handle_t)held[0] };
          struct unixcall_generic_obj_noret c2 = { (obj_handle_t)cb };
          cb.name = @"game";
          _MTLCommandBuffer_presentDrawable(&p);
          _MTLCommandBuffer_commit(&c2);
        }
        expect(held[0] && !has(@": present ") && atomic_load(&g_pfx_broken) == 1
               && [g_log rangeOfString:@"present-fx is off for the rest of this session"].location != NSNotFound,
               "the lock never comes free: 20 frames are dropped (nothing half-made is shown), then present-fx turns itself off and says so");
        [pool release];
      }
      drain_main();
      expect(layer.drawableSize.width == 1408 && layer.drawableSize.height == 648 && !madeira_pfx_on(), "the layer is back at the game's own size");
      reset_ops();
      @autoreleasepool { game_frame(layer, q, 0, &tn); }
      expect([tn hasPrefix:@"drawable"] && has(@"game: present drawable"), "and frames present as without present-fx from then on");
      return g_fails ? 1 : 0;
    }
  }
  return 2;
}
